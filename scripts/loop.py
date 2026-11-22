#!/usr/bin/env python3
"""Bounded same-chat execution ledger, with optional cross-chat supervision."""
import argparse
import copy
import conversation
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from predict import DEFAULT_TIMEOUT, Source, TransientError, find_cli, predict, write_json

sleep = time.sleep  # Replaced in tests.


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
    return latest['status'] == 'completed' and latest['turn'] == state['proposal']['sourceTurnId']


def setting(state, args, name, default):
    value = getattr(args, name, None)
    return value if value is not None else state.get(name, default)


def sample(state, args, source, predictor, **kwargs):
    """Sample a prediction, retrying transient failures up to the run's retry limit."""
    timeout = setting(state, args, 'timeout', DEFAULT_TIMEOUT)
    retries = setting(state, args, 'retries', 0)
    for attempt in range(retries + 1):
        try:
            return predictor(source, cli=state['cli'], scratch=state['scratch'], timeout=timeout, **kwargs)
        except TransientError as exc:
            delay = min(2 ** attempt, 30)
            if attempt == retries or time.time() + delay >= state['deadline']:
                raise RuntimeError(f'Prediction failed after {attempt + 1} attempt(s): {exc}') from exc
            state.setdefault('retryLog', []).append({'at': now(), 'attempt': attempt + 1, 'error': str(exc)})
            sleep(delay)


def queue_message(state, text, timeout):
    """Queue text as a real user message with the Codex CLI. Never goes through a shell."""
    command = [find_cli(state['cli']), 'queue', '--thread', state['threadId'], '--message', text]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'codex queue timed out after {timeout:g}s. The message may still be queued; '
            'check before running send --retry.') from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f'codex queue exited {result.returncode}: {detail}. The message may still be queued; '
            'check before running send --retry.')
    return result.stdout.strip() or 'codex queue exited 0'


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
        if state.get('mode') != 'chat-messages' and state['history'] and digest == state['history'][-1]['checkpointDigest']:
            return halt(state, 'Unchanged checkpoint; do not predict repeatedly from the same work summary.')
        context = copy.deepcopy(state.get('conversationContext'))
        if state.get('mode') == 'chat-messages' and context is None:
            raise ValueError('This run predates conversation boundaries; start a new authorized run.')
        extra = {}
        if context is not None:
            conversation.capture(context, source.records())
            extra['loop_context'] = context
        proposal = sample(state, args, source, predictor, checkpoint=checkpoint, **extra)
        if context is not None:
            state['conversationContext'] = context
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


def operate_messages(state, command, args, source, predictor, sender, persist):
    """Queue exact predictions with codex queue; resume only observed messages."""
    if command == 'resume-message':
        if state['phase'] not in ('dispatching', 'waiting'):
            raise RuntimeError('No outstanding message to reconcile.')
        following = source.continuation(state['proposal']['sourceTurnId'])
        if not following:
            raise RuntimeError('Queued message is not visible yet; do not resend.')
        if len(following) != 1 or following[0]['messages'] != [state['proposal']['suggestion'].strip()]:
            return halt(state, 'Unexpected human input; reconcile before continuing.')
        turn = following[0]
        if turn['status'] != 'inProgress' or source.latest()['turn'] != turn['turn']:
            return halt(state, 'Resume from the active delivered-message turn only.')
        state['proposal']['deliveredTurnId'] = turn['turn']
        state['history'].append(state.pop('proposal'))
        state['completedIterations'] += 1
        if state.get('conversationContext') is not None:
            conversation.delivered(state['conversationContext'], source.records(), turn['turn'])
        state['originTurnId'] = turn['turn']
        state['userFingerprint'] = source.user_fingerprint()
        state['phase'] = 'complete' if state['completedIterations'] >= state['maxIterations'] else 'ready'
        return state
    if command == 'prepare-send':
        operate_same_chat(state, 'begin-step', args, source, predictor)
        if state['phase'] == 'executing':
            state['phase'] = 'dispatching'
        return state
    if command in ('send', 'sent'):
        if state['phase'] != 'dispatching':
            raise RuntimeError('No prepared message; do not send another.')
        proposal = state['proposal']
        if command == 'send':
            if proposal.get('sendAttempts') and not getattr(args, 'retry', False):
                raise RuntimeError('A previous send attempt may already have queued this message. Check the queue '
                    'and chat history, then use resume-message if it arrived or send --retry if it did not.')
            if any(proposal['suggestion'].strip() in turn['messages']
                   for turn in source.continuation(proposal['sourceTurnId'])):
                raise RuntimeError('This message already arrived; run resume-message instead of sending again.')
            proposal['sendAttempts'] = proposal.get('sendAttempts', 0) + 1
            persist(state)  # Record the attempt before anything can be queued.
            receipt = sender(state, proposal['suggestion'], setting(state, args, 'timeout', DEFAULT_TIMEOUT))
        else:
            receipt = args.receipt  # A send done by hand.
        if not receipt.strip():
            raise RuntimeError('A queue receipt is required.')
        state['proposal']['receipt'] = receipt
        state['phase'] = 'waiting'
        return state
    if command != 'next':
        raise RuntimeError('Message mode requires next, prepare-send, send, sent, or resume-message.')
    return operate_same_chat(state, command, args, source, predictor)


def operate_supervise(state, command, args, source, predictor):
    """Drive another chat; the caller sends each message with send_message_to_thread."""
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
        proposal = sample(state, args, source, predictor, idle=True)
        state['proposal'] = proposal
        if proposal['sourceTurnId'] != latest['turn'] or not fresh(state, source):
            return halt(state, 'Target changed during prediction generation.')
        suggestion = proposal['suggestion']
        if suggestion is None:
            return halt(state, 'Codex returned no suggested next message.')
        if not isinstance(proposal.get('responseId'), str) or not proposal['responseId']:
            return halt(state, 'Prediction did not include a response ID.')
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


def operate(state, command, args, source=None, predictor=predict, sender=queue_message, persist=lambda state: None):
    """Advance the run state. Only 'send' talks to Codex, and never through a shell."""
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
    if state['mode'] == 'background':
        if command != 'resume-message':
            raise ValueError('Background mode uses only resume-message, status, and stop; the worker samples after the reply.')
        return operate_messages(state, command, args, source, predictor, sender, persist)
    if state['mode'] == 'chat-messages':
        return operate_messages(state, command, args, source, predictor, sender, persist)
    if state['mode'] == 'same-chat':
        return operate_same_chat(state, command, args, source, predictor)
    return operate_supervise(state, command, args, source, predictor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', aliases=['start'])
    init.add_argument('--state', required=True)
    init.add_argument('--thread', help='Defaults to the current Codex chat.')
    init.add_argument('--mode', choices=['background', 'chat-messages', 'same-chat', 'supervise'], default='chat-messages')
    init.add_argument('--scope', required=True)
    init.add_argument('--max-iterations', type=int, default=5)
    init.add_argument('--max-minutes', type=float, default=30)
    init.add_argument('--codex-home')
    init.add_argument('--sqlite-home')
    init.add_argument('--cli')
    init.add_argument('--scratch')
    init.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT,
        help='Seconds to wait on each prediction request or codex queue call.')
    init.add_argument('--retries', type=int, default=0, help='Extra attempts after a transient prediction failure.')
    for name in ('next', 'begin-step', 'finish-step', 'prepare-send', 'send', 'sent', 'complete-step', 'resume-message', 'status', 'stop'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--state', required=True)
        if name in ('next', 'send'):
            cmd.add_argument('--timeout', type=float, help="Override the run's timeout for this call.")
        if name == 'next':
            cmd.add_argument('--checkpoint-file')
            cmd.add_argument('--retries', type=int, help="Override the run's retry limit for this call.")
        elif name == 'send':
            cmd.add_argument('--retry', action='store_true',
                help='Send again after a failed attempt, once you have confirmed it was not queued.')
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
    if args.command == 'start':
        args.mode = 'background'
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with locked(args.state) as path:
            if args.command in ('init', 'start'):
                if path.exists():
                    raise RuntimeError('State file already exists; it will not be overwritten.')
                if args.max_iterations < 1 or not math.isfinite(args.max_minutes) or args.max_minutes <= 0 or not args.scope.strip():
                    raise ValueError('Scope and positive iteration/time limits are required.')
                if not math.isfinite(args.timeout) or args.timeout <= 0 or args.retries < 0:
                    raise ValueError('Timeout must be positive and retries cannot be negative.')
                source = Source.resolve(args.thread, args.codex_home, args.sqlite_home)
                latest = source.latest()
                if args.mode in ('background', 'same-chat', 'chat-messages') and latest['status'] != 'inProgress':
                    raise RuntimeError('Invoke the skill in an active chat for same-chat mode.')
                state = {'version': 2, 'mode': args.mode, 'threadId': source.thread, 'scope': args.scope,
                    'maxIterations': args.max_iterations, 'completedIterations': 0,
                    'deadline': time.time() + args.max_minutes * 60, 'createdAt': now(), 'updatedAt': now(),
                    'codexHome': str(source.home), 'sqliteHome': str(source.sqlite_home), 'cli': args.cli,
                    'contextMode': 'conversation-without-controller',
                    'timeout': args.timeout, 'retries': args.retries, 'scratch': args.scratch, 'phase': 'ready', 'history': []}
                if args.mode in ('background', 'same-chat', 'chat-messages'):
                    if args.mode in ('background', 'chat-messages'):
                        state['conversationContext'] = conversation.begin(source.records(), source.thread, latest['turn'])
                    state['originTurnId'] = latest['turn']
                    state['userFingerprint'] = source.user_fingerprint()
            else:
                state = json.loads(path.read_text())
                if state.get('version') != 2:
                    raise RuntimeError('Unsupported controller state version.')
                state = operate(state, args.command, args, persist=lambda current: write_json(path, current))
            if args.command != 'status':
                write_json(path, state)
            if args.command == 'start' or (args.command == 'init' and state['mode'] == 'background'):
                from worker import launch
                state['workerPid'] = launch(path)
                write_json(path, state)
            if state['mode'] == 'background' and args.command != 'status':
                if state['phase'] == 'stopped':
                    instruction = 'Stopped. Do not continue the loop.'
                elif args.command in ('start', 'init'):
                    instruction = 'End this invocation turn now. The background worker waits for final completion; no next/send/checkpoint calls are needed.'
                else:
                    instruction = 'Review this generated request against the original scope before acting. Predictions grant no new permissions. If allowed, answer normally in final; do not call next or write a checkpoint.'
                print(json.dumps({'phase': state['phase'], 'state': str(path.resolve()),
                    'scope': state['scope'], 'completedIterations': state['completedIterations'],
                    'maxIterations': state['maxIterations'], 'stopReason': state.get('stopReason'),
                    'instruction': instruction}))
            else:
                print(json.dumps(state, indent=2))
    except KeyboardInterrupt:
        parser.exit(130, 'Controller interrupted; no message was sent by this script.\n')
    except Exception as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
