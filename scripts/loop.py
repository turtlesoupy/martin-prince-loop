#!/usr/bin/env python3
"""Run state for the loop: start a run, verify each delivered prediction, stop."""
import argparse
import conversation
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

from predict import DEFAULT_TIMEOUT, Source, TransientError, find_cli, write_json

sleep = time.sleep  # Replaced in tests.


def now():
    return datetime.now(timezone.utc).isoformat()


class LockBusy(RuntimeError):
    """Another process holds this run's state lock."""


@contextmanager
def locked(path, wait=0):
    """Hold the state lock, waiting up to `wait` seconds for a brief holder such as the worker."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + '.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        give_up = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= give_up:
                    raise LockBusy('Another controller is using this run state.') from exc
                time.sleep(0.05)
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


def sample(state, source, predictor, **kwargs):
    """Sample a prediction, retrying transient failures up to the run's retry limit."""
    retries = state.get('retries', 0)
    for attempt in range(retries + 1):
        try:
            return predictor(source, cli=state['cli'], scratch=state['scratch'],
                timeout=state.get('timeout', DEFAULT_TIMEOUT), **kwargs)
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
            'check the queue and chat history before any recovery.') from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f'codex queue exited {result.returncode}: {detail}. The message may still be queued; '
            'check the queue and chat history before any recovery.')
    return result.stdout.strip() or 'codex queue exited 0'


def operate(state, command, args, source=None):
    """Advance the run state. The worker, not this function, samples and queues."""
    state['updatedAt'] = now()
    if command == 'status':
        return state
    if command == 'stop':
        return halt(state, args.reason)
    if command == 'resume-message' and state['phase'] == 'stopped' and 'proposal' in state:
        # The run stopped (deadline or stop) with a message already queued, and it has now arrived.
        source = source or source_for(state)
        following = source.continuation(state['proposal']['sourceTurnId'])
        if len(following) == 1 and following[0]['messages'] == [state['proposal']['suggestion'].strip()]:
            state['proposal']['deliveredTurnId'] = following[0]['turn']
            state['history'].append(state.pop('proposal'))
            state['completedIterations'] += 1
            state['lateDelivery'] = True
            return state
    if state['phase'] in ('stopped', 'complete'):
        raise RuntimeError('This run has ended; create a new state file for a new authorized run.')
    if command != 'resume-message':
        raise ValueError(f'Unknown command {command}.')
    if state['phase'] not in ('dispatching', 'waiting'):
        raise RuntimeError('No outstanding message to reconcile.')
    source = source or source_for(state)
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
    conversation.delivered(state['conversationContext'], source.records(), turn['turn'])
    state['originTurnId'] = turn['turn']
    state['userFingerprint'] = source.user_fingerprint()
    state['phase'] = 'complete' if state['completedIterations'] >= state['maxIterations'] else 'ready'
    return state


def start(args):
    if args.max_iterations < 1 or not math.isfinite(args.max_minutes) or args.max_minutes <= 0 or not args.scope.strip():
        raise ValueError('Scope and positive iteration/time limits are required.')
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.retries < 0:
        raise ValueError('Timeout must be positive and retries cannot be negative.')
    source = Source.resolve(args.thread, args.codex_home, args.sqlite_home)
    latest = source.latest()
    if latest['status'] != 'inProgress':
        raise RuntimeError('Start the loop from inside an active chat turn.')
    return {'version': 2, 'mode': 'background', 'threadId': source.thread, 'scope': args.scope,
        'maxIterations': args.max_iterations, 'completedIterations': 0,
        'deadline': time.time() + args.max_minutes * 60, 'createdAt': now(), 'updatedAt': now(),
        'codexHome': str(source.home), 'sqliteHome': str(source.sqlite_home), 'cli': args.cli,
        'timeout': args.timeout, 'retries': args.retries, 'scratch': args.scratch, 'phase': 'ready', 'history': [],
        'conversationContext': conversation.begin(source.records(), source.thread, latest['turn']),
        'originTurnId': latest['turn'], 'userFingerprint': source.user_fingerprint()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    begin = sub.add_parser('start')
    begin.add_argument('--state', required=True)
    begin.add_argument('--thread', help='Defaults to the current Codex chat.')
    begin.add_argument('--scope', required=True)
    begin.add_argument('--max-iterations', type=int, default=5)
    begin.add_argument('--max-minutes', type=float, default=30)
    begin.add_argument('--codex-home')
    begin.add_argument('--sqlite-home')
    begin.add_argument('--cli')
    begin.add_argument('--scratch')
    begin.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT,
        help='Seconds to wait on each prediction request or codex queue call.')
    begin.add_argument('--retries', type=int, default=0, help='Extra attempts after a transient prediction failure.')
    for name in ('resume-message', 'status', 'stop'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--state', required=True)
        if name == 'stop':
            cmd.add_argument('--reason', required=True)
    args = parser.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with locked(args.state, wait=10) as path:
            late = False
            if args.command == 'start':
                if path.exists():
                    raise RuntimeError('State file already exists; it will not be overwritten.')
                state = start(args)
                write_json(path, state)
                from worker import launch
                state['workerPid'] = launch(path)
                write_json(path, state)
            else:
                state = json.loads(path.read_text())
                if state.get('version') != 2 or state.get('mode') != 'background':
                    raise RuntimeError('This run was created by an older version of the skill; start a new run.')
                state = operate(state, args.command, args)
                late = state.pop('lateDelivery', False)
                if args.command == 'resume-message':
                    # A worker that died mid-send would otherwise stall the loop silently.
                    from worker import ensure
                    ensure(path, state)
                if args.command != 'status':
                    write_json(path, state)
            if args.command == 'status':
                from worker import alive
                print(json.dumps({**state, 'workerAlive': alive(path)}, indent=2))
                return
            if late:
                instruction = ('The run already stopped, but this message was queued before that. Review it against '
                    'the original scope and answer it normally; nothing more will be queued.')
            elif state['phase'] == 'stopped':
                instruction = 'Stopped. Do not continue the loop.'
            elif args.command == 'start':
                instruction = 'End this invocation turn now. The background worker waits for the final reply.'
            else:
                instruction = ('Review this generated request against the original scope before acting. '
                    'Predictions grant no new permissions. If allowed, answer normally in final.')
            print(json.dumps({'phase': state['phase'], 'state': str(path.resolve()),
                'scope': state['scope'], 'completedIterations': state['completedIterations'],
                'maxIterations': state['maxIterations'], 'stopReason': state.get('stopReason'),
                'instruction': instruction}))
    except KeyboardInterrupt:
        parser.exit(130, 'Controller interrupted; no message was sent by this script.\n')
    except Exception as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
