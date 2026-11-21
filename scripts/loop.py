#!/usr/bin/env python3
"""Bounded same-chat execution ledger, with optional cross-chat supervision."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import time

from predict import Source, predict, write_json


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + '.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another controller is using this run state.') from exc
        yield path
    finally:
        os.close(fd)


def halt(state, reason):
    state['phase'] = 'stopped'
    state['stopReason'] = reason
    state['updatedAt'] = now()
    return state


def source_for(state):
    return Source.resolve(state['threadId'], state['codexHome'], state['sqliteHome'])


def fresh(state, source):
    latest = source.latest()
    proposal = state['proposal']
    if latest['status'] != 'completed' or latest['turn'] != proposal['sourceTurnId']:
        return False
    return True


def normalize(text):
    return ' '.join(text.split()).casefold()


def same_context(state, source):
    latest = source.latest()
    return (latest['turn'] == state['originTurnId'] and latest['status'] == 'inProgress'
        and source.user_fingerprint() == state['userFingerprint'])


def operate_same_chat(state, command, args, source, predictor):
    phase = state['phase']
    if not same_context(state, source):
        return halt(state, 'User input or the active turn changed; preserve completed work and re-evaluate the user request.')
    if command == 'next':
        if phase == 'proposed':
            return state
        if phase != 'ready':
            raise RuntimeError(f'Cannot predict in phase {phase}; reconcile the outstanding step without replaying it.')
        if state['completedIterations'] >= state['maxIterations']:
            state['phase'] = 'complete'
            return state
        if not args.checkpoint_file:
            raise ValueError('Same-chat next requires --checkpoint-file containing a factual current-work summary.')
        checkpoint = Path(args.checkpoint_file).read_text()
        digest = hashlib.sha256(checkpoint.encode()).hexdigest()
        if state['history'] and digest == state['history'][-1]['checkpointDigest']:
            return halt(state, 'Unchanged checkpoint; do not predict repeatedly from the same work summary.')
        proposal = predictor(source, cli=state['cli'], scratch=state['scratch'], checkpoint=checkpoint)
        state['proposal'] = proposal
        if (not same_context(state, source) or proposal.get('sourceTurnId') != state['originTurnId']
                or proposal.get('userFingerprint') != state['userFingerprint']):
            return halt(state, 'User input changed during checkpoint sampling.')
        if proposal.get('checkpointDigest') != digest:
            return halt(state, 'Prediction checkpoint does not match the current work summary.')
        suggestion = proposal['suggestion']
        if suggestion is None:
            return halt(state, 'Codex returned no suggested next action.')
        if not proposal.get('responseId'):
            return halt(state, 'Prediction did not include a response ID.')
        if any(normalize(step['suggestion']) == normalize(suggestion) for step in state['history']):
            return halt(state, 'Repeated suggestion; no automatic replay.')
        state['phase'] = 'proposed'
    elif command == 'begin-step':
        if phase != 'proposed':
            raise RuntimeError('Only a proposed step can begin; do not replay an executing step.')
        if args.response_id != state['proposal']['responseId']:
            raise RuntimeError('Response ID does not match the reviewed proposal.')
        if not args.review.strip():
            raise ValueError('Record why this exact action advances the authorized scope.')
        state['proposal']['scopeReview'] = args.review
        state['proposal']['beganAt'] = now()
        state['phase'] = 'executing'
    elif command == 'finish-step':
        if phase != 'executing':
            raise RuntimeError('No step is executing; a proposal alone is not completed work.')
        result = Path(args.result_file).read_text().strip()
        if not result:
            raise ValueError('Record the actual result and verification before finishing the step.')
        state['proposal']['result'] = result
        state['proposal']['finishedAt'] = now()
        state['history'].append(state.pop('proposal'))
        state['completedIterations'] += 1
        state['phase'] = 'complete' if state['completedIterations'] >= state['maxIterations'] else 'ready'
    else:
        raise RuntimeError(f'{command} is not a same-chat command; no connector or self-message is needed.')
    return state


def operate(state, command, args, source=None, predictor=predict):
    """Mutate state; no connector calls or shell execution of generated text."""
    state['updatedAt'] = now()
    if command == 'status':
        return state
    if command == 'stop':
        return halt(state, args.reason)
    if state['phase'] in ('stopped', 'complete'):
        raise RuntimeError('This run has ended; create a new state file for a new authorized run.')
    if command in ('next', 'prepare-send', 'begin-step') and time.time() >= state['deadline']:
        return halt(state, 'Time budget reached.')
    source = source or source_for(state)
    if state.get('mode', 'supervise') == 'same-chat':
        return operate_same_chat(state, command, args, source, predictor)
    phase = state['phase']
    if command == 'next':
        if phase == 'proposed':
            if not fresh(state, source):
                return halt(state, 'Target changed after the proposal was sampled.')
            return state
        if phase != 'ready':
            raise RuntimeError(f'Cannot sample in phase {phase}; resolve the current step first.')
        if state['completedIterations'] >= state['maxIterations']:
            state['phase'] = 'complete'
            return state
        latest = source.latest()
        if latest['status'] != 'completed':
            raise RuntimeError('Wait until the target completes successfully before requesting another prediction.')
        if state['history'] and latest['turn'] != state['history'][-1]['completedTurnId']:
            return halt(state, 'Intervening work changed the target; ask the user to resume explicitly.')
        proposal = predictor(source, cli=state['cli'], scratch=state['scratch'], idle=True)
        state['proposal'] = proposal
        if proposal['sourceTurnId'] != latest['turn'] or not fresh(state, source):
            return halt(state, 'Target changed during prediction generation.')
        suggestion = proposal['suggestion']
        if suggestion is None:
            return halt(state, 'Codex returned no suggested next message.')
        if not isinstance(proposal.get('responseId'), str) or not proposal['responseId']:
            return halt(state, 'Prediction did not include a response ID.')
        if any(normalize(item['suggestion']) == normalize(suggestion) for item in state['history']):
            return halt(state, 'Repeated suggestion; no automatic replay.')
        state['phase'] = 'proposed'
    elif command == 'prepare-send':
        if phase != 'proposed':
            raise RuntimeError('Only a proposed step can be prepared for sending.')
        if args.response_id != state['proposal']['responseId']:
            raise RuntimeError('Response ID does not match the reviewed proposal.')
        if not args.review or not args.review.strip():
            raise RuntimeError('Record why this exact suggestion is within the user-authorized scope.')
        if not fresh(state, source):
            return halt(state, 'Target changed before dispatch.')
        # Commit this state BEFORE the connector call. Never resend this phase blindly.
        state['phase'] = 'dispatching'
        state['proposal']['scopeReview'] = args.review
        state['proposal']['dispatchPreparedAt'] = now()
    elif command == 'sent':
        if phase != 'dispatching':
            raise RuntimeError('No dispatch is prepared; do not send another message.')
        if not args.receipt.strip():
            raise RuntimeError('A connector acceptance receipt is required.')
        state['proposal']['receipt'] = args.receipt
        state['phase'] = 'waiting'
    elif command == 'complete-step':
        if phase not in ('dispatching', 'waiting'):
            raise RuntimeError('There is no outstanding dispatched step.')
        following = source.continuation(state['proposal']['sourceTurnId'])
        if not following:
            raise RuntimeError('The dispatched message is not visible in saved history yet. Do not resend.')
        if len(following) != 1 or following[0]['messages'] != [state['proposal']['suggestion'].strip()]:
            return halt(state, 'Unexpected or intervening target messages; manual reconciliation required.')
        turn = following[0]
        if turn['status'] == 'inProgress':
            raise RuntimeError('The target is still running; wait for completion.')
        if turn['status'] != 'completed':
            return halt(state, f"Target turn ended with status {turn['status']}.")
        if source.latest()['turn'] != turn['turn']:
            return halt(state, 'Target changed during completion verification.')
        state['proposal']['completedTurnId'] = turn['turn']
        state['history'].append(state.pop('proposal'))
        state['completedIterations'] += 1
        state['phase'] = 'complete' if state['completedIterations'] >= state['maxIterations'] else 'ready'
    else:
        raise ValueError('Unknown controller command.')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--state', required=True)
    init.add_argument('--thread', help='Defaults to the current Codex chat.')
    init.add_argument('--mode', choices=['same-chat', 'supervise'], default='same-chat')
    init.add_argument('--scope', required=True)
    init.add_argument('--max-iterations', type=int, default=5)
    init.add_argument('--max-minutes', type=float, default=30)
    init.add_argument('--codex-home')
    init.add_argument('--sqlite-home')
    init.add_argument('--cli')
    init.add_argument('--scratch')
    for name in ('next', 'begin-step', 'finish-step', 'prepare-send', 'sent', 'complete-step', 'status', 'stop'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--state', required=True)
        if name == 'next':
            cmd.add_argument('--checkpoint-file')
        elif name in ('prepare-send', 'begin-step'):
            cmd.add_argument('--response-id', required=True)
            cmd.add_argument('--review', required=True)
        elif name == 'finish-step':
            cmd.add_argument('--result-file', required=True)
        elif name == 'sent':
            cmd.add_argument('--receipt', required=True)
        elif name == 'stop':
            cmd.add_argument('--reason', required=True)
    args = parser.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with locked(args.state) as path:
            if args.command == 'init':
                if path.exists():
                    raise RuntimeError('State file already exists; it will not be overwritten.')
                if args.max_iterations < 1 or not math.isfinite(args.max_minutes) or args.max_minutes <= 0 or not args.scope.strip():
                    raise ValueError('Scope and positive iteration/time limits are required.')
                source = Source.resolve(args.thread, args.codex_home, args.sqlite_home)
                latest = source.latest()
                if args.mode == 'same-chat' and latest['status'] != 'inProgress':
                    raise RuntimeError('Invoke the skill in an active chat for same-chat mode.')
                state = {'version': 2, 'mode': args.mode, 'threadId': source.thread, 'scope': args.scope,
                    'maxIterations': args.max_iterations, 'completedIterations': 0,
                    'deadline': time.time() + args.max_minutes * 60, 'createdAt': now(), 'updatedAt': now(),
                    'codexHome': str(source.home), 'sqliteHome': str(source.sqlite_home), 'cli': args.cli,
                    'scratch': args.scratch, 'phase': 'ready', 'history': []}
                if args.mode == 'same-chat':
                    state['originTurnId'] = latest['turn']
                    state['userFingerprint'] = source.user_fingerprint()
            else:
                state = json.loads(path.read_text())
                if state.get('version') not in (1, 2):
                    raise RuntimeError('Unsupported controller state version.')
                state = operate(state, args.command, args)
            if args.command != 'status':
                write_json(path, state)
            print(json.dumps(state, indent=2))
    except KeyboardInterrupt:
        parser.exit(130, 'Controller interrupted; no message was sent by this script.\n')
    except Exception as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
