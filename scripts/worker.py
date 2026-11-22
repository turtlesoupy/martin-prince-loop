#!/usr/bin/env python3
"""Bounded background dispatcher: sample only after a real final reply is saved."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import conversation
from loop import LockBusy, halt, locked, now, queue_message, sample, source_for
from predict import predict, write_json


def launch(state_path):
    """Detach with closed stdio so replying never waits for prediction work."""
    state_path = Path(state_path).resolve()
    log = state_path.with_suffix('.worker.log')
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--state', str(state_path)],
            stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, start_new_session=True, close_fds=True)
    finally:
        os.close(fd)
    return process.pid


def current(state, source):
    latest = source.latest()
    return (latest['turn'] == state['originTurnId'] and latest['status'] == 'completed'
        and latest.get('end') is not None and source.user_fingerprint() == state['userFingerprint'])


def prepare(state, source):
    """Return an immutable sampling job, or None while waiting/after stopping."""
    if state['phase'] in ('complete', 'stopped'):
        return None
    if time.time() >= state['deadline']:
        halt(state, 'Time budget reached.')
        return None
    if state['phase'] == 'waiting':
        following = source.continuation(state['proposal']['sourceTurnId'])
        if following:
            expected = state['proposal']['suggestion'].strip()
            if len(following) != 1 or following[0]['messages'] != [expected]:
                halt(state, 'Unexpected human input; the background loop stopped.')
            elif following[0]['status'] != 'inProgress':
                halt(state, 'Delivered turn ended without resume-message verification.')
        elif source.user_fingerprint() != state['userFingerprint']:
            halt(state, 'Human input changed; the background loop stopped.')
        return None
    if state['phase'] != 'ready':
        # A crashed sampling/dispatch step is never replayed automatically.
        raise RuntimeError('Outstanding sampling or dispatch requires reconciliation; no automatic replay.')
    if state['completedIterations'] >= state['maxIterations']:
        state['phase'] = 'complete'
        return None
    latest = source.latest()
    if latest['turn'] != state['originTurnId'] or source.user_fingerprint() != state['userFingerprint']:
        halt(state, 'Human input or the target turn changed; the background loop stopped.')
        return None
    if latest['status'] == 'inProgress':
        return None
    if latest['status'] != 'completed':
        halt(state, 'Target turn did not complete successfully.')
        return None
    if latest.get('end') is None:
        return None  # Wait for the saved final response, not only a UI completion event.
    context = copy.deepcopy(state['conversationContext'])
    # Use the completed saved boundary, excluding any concurrently arriving turn.
    with source.rollout().open('rb') as stream:
        data = stream.read(latest['end'])
    if len(data) != latest['end'] or not data.endswith(b'\n'):
        return None
    records = [json.loads(line) for line in data.splitlines()]
    conversation.capture(context, records)
    if not current(state, source):
        halt(state, 'Human input changed while capturing the reply.')
        return None
    state['conversationContext'] = context
    state['phase'] = 'sampling'
    state['samplingStartedAt'] = now()
    state['samplingKey'] = conversation.digest([state['originTurnId'], context])
    return copy.deepcopy(state)


def commit(state, job, proposal, source, sender, persist):
    """Validate again, durably record one send attempt, then queue exact text."""
    if state['phase'] != 'sampling' or state.get('samplingKey') != job['samplingKey']:
        return state  # A stop while the network request ran wins.
    if time.time() >= state['deadline']:
        return halt(state, 'Time budget reached during sampling.')
    if not current(state, source):
        return halt(state, 'Human input changed during prediction; nothing was queued.')
    if (proposal.get('sourceTurnId') != state['originTurnId']
            or proposal.get('userFingerprint') != state['userFingerprint']
            or proposal.get('conversationDigest') != conversation.digest(job['conversationContext'])):
        return halt(state, 'Prediction did not match the completed conversation.')
    suggestion = proposal.get('suggestion')
    if suggestion is None:
        return halt(state, 'Codex returned no suggested next message.')
    if not isinstance(suggestion, str) or not suggestion.strip() or not proposal.get('responseId'):
        return halt(state, 'Invalid prediction response; nothing was queued.')
    state['proposal'] = {**proposal, 'sendAttempts': 1,
        'authorizationReview': 'Required on delivery before task actions; original scope remains authoritative.'}
    state['phase'] = 'dispatching'
    persist(state)  # Persist before side effects; ambiguous errors cannot double-send.
    if not current(state, source) or time.time() >= state['deadline']:
        return halt(state, 'Context or time budget changed before queueing.')
    receipt = sender(state, suggestion, state['timeout'])
    if not isinstance(receipt, str) or not receipt.strip():
        raise RuntimeError('Queue returned no receipt; reconcile before retrying.')
    state['proposal']['receipt'] = receipt
    state['phase'] = 'waiting'
    state['updatedAt'] = now()
    return state


def tick(path, predictor=predict, sender=queue_message, source=None):
    with locked(path):
        state = json.loads(Path(path).read_text())
        if state['mode'] != 'background':
            raise ValueError('Worker requires a background-mode run.')
        source = source or source_for(state)
        previous = copy.deepcopy(state)
        job = prepare(state, source)
        if state != previous:
            write_json(path, state)
        if job is None:
            return state['phase'] not in ('complete', 'stopped')
    # Do not hold the state lock during the network request: stop stays responsive.
    started = time.monotonic()
    try:
        proposal = sample(job, source, predictor, idle=True,
            loop_context=job['conversationContext'], completed_context=True)
    except Exception as exc:
        with locked(path):
            state = json.loads(Path(path).read_text())
            if state['phase'] == 'sampling' and state.get('samplingKey') == job['samplingKey']:
                halt(state, f'Prediction failed: {exc}')
                write_json(path, state)
        return False
    proposal['samplingSeconds'] = round(time.monotonic() - started, 3)
    with locked(path):
        state = json.loads(Path(path).read_text())
        if job.get('retryLog'):
            state['retryLog'] = job['retryLog']
        try:
            commit(state, job, proposal, source, sender, lambda s: write_json(path, s))
        except Exception as exc:
            # Leave dispatching intact: the message may already be in the queue.
            state['workerError'] = str(exc)
            write_json(path, state)
            return False
        write_json(path, state)
        return state['phase'] not in ('complete', 'stopped')


def run(path):
    # A second launched process cannot double-sample or double-dispatch.
    with locked(str(path) + '.worker'):
        while True:
            try:
                if not tick(path):
                    return
            except LockBusy:
                pass  # A controller call holds the state briefly; try again.
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    args = parser.parse_args()
    path = Path(args.state).resolve()
    try:
        run(path)
    except LockBusy as exc:
        # A second worker's lock failure is not a failure of the running worker.
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        with locked(path, wait=10):
            state = json.loads(path.read_text())
            if state['phase'] not in ('complete', 'stopped'):
                state['workerError'] = str(exc)
                if state['phase'] not in ('dispatching', 'waiting'):
                    halt(state, f'Worker stopped: {exc}')
                write_json(path, state)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
