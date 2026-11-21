#!/usr/bin/env python3
"""Durable bounded controller state. Dispatch is performed by the Codex skill."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
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


def operate(state, command, args, source=None, predictor=predict):
    """Mutate state; no connector calls or shell execution of generated text."""
    state['updatedAt'] = now()
    if command == 'status':
        return state
    if command == 'stop':
        return halt(state, args.reason)
    if state['phase'] in ('stopped', 'complete'):
        raise RuntimeError('This run has ended; create a new state file for a new authorized run.')
    if command in ('next', 'prepare-send') and time.time() >= state['deadline']:
        return halt(state, 'Time budget reached.')
    source = source or source_for(state)
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
    init.add_argument('--thread', required=True)
    init.add_argument('--scope', required=True)
    init.add_argument('--max-iterations', type=int, default=5)
    init.add_argument('--max-minutes', type=float, default=30)
    init.add_argument('--codex-home')
    init.add_argument('--sqlite-home')
    init.add_argument('--cli')
    init.add_argument('--scratch')
    for name in ('next', 'prepare-send', 'sent', 'complete-step', 'status', 'stop'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--state', required=True)
        if name == 'prepare-send':
            cmd.add_argument('--response-id', required=True)
            cmd.add_argument('--review', required=True)
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
                source.latest()  # Confirm the target is readable before initializing.
                state = {'version': 1, 'threadId': args.thread, 'scope': args.scope,
                    'maxIterations': args.max_iterations, 'completedIterations': 0,
                    'deadline': time.time() + args.max_minutes * 60, 'createdAt': now(), 'updatedAt': now(),
                    'codexHome': str(source.home), 'sqliteHome': str(source.sqlite_home), 'cli': args.cli,
                    'scratch': args.scratch, 'phase': 'ready', 'history': []}
            else:
                state = json.loads(path.read_text())
                if state.get('version') != 1:
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
