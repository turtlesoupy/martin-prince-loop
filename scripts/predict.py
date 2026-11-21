#!/usr/bin/env python3
"""Sample Codex's live composer-prediction prompt on an isolated chat snapshot."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

CONFIG_URL = 'https://chatgpt.com/backend-api/wham/predictions/config'


def write_json(path, value):
    """Atomically replace a private state file, including on an existing path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def database(directory, stem):
    candidates = []
    for path in Path(directory).glob(stem + '_*.sqlite'):
        match = re.fullmatch(re.escape(stem) + r'_(\d+)\.sqlite', path.name)
        if match:
            candidates.append((int(match[1]), path))
    if not candidates:
        raise RuntimeError(f'No {stem} database found in {directory}. Local paginated history is required.')
    return max(candidates)[1]


def query(path, sql, params):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return connection.execute(sql, params).fetchall()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f'Unsupported or unavailable Codex history schema: {exc}') from exc
    finally:
        connection.close()


@dataclass(frozen=True)
class Source:
    home: Path
    sqlite_home: Path
    thread: str

    @classmethod
    def resolve(cls, thread=None, home=None, sqlite_home=None):
        thread = thread or os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID')
        if not thread:
            raise RuntimeError('Current chat ID is unavailable. Supply --thread with a verified ID.')
        home = Path(home or os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve()
        sqlite_home = Path(sqlite_home or os.environ.get('CODEX_SQLITE_HOME', home)).expanduser().resolve()
        return cls(home, sqlite_home, thread)

    def rollout(self):
        rows = query(database(self.sqlite_home, 'state'),
            'SELECT rollout_path FROM threads WHERE id=?', (self.thread,))
        if not rows:
            raise RuntimeError('Thread metadata was not found.')
        return Path(rows[0][0])

    def records(self):
        data = self.rollout().read_bytes()
        # Ignore a partial trailing record written concurrently by the app.
        newline = data.rfind(b'\n')
        if newline < 0:
            raise RuntimeError('No complete saved rollout records are available.')
        return [json.loads(line) for line in data[:newline + 1].splitlines()]

    def user_fingerprint(self):
        return user_fingerprint(self.records())

    def latest(self):
        rows = query(database(self.sqlite_home, 'thread_history'),
            'SELECT turn_id,status,rollout_end_byte_offset FROM thread_turns '
            'WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1', (self.thread,))
        if not rows:
            raise RuntimeError('No saved turns found for this local chat.')
        return dict(zip(('turn', 'status', 'end'), rows[0]))

    def boundary(self, idle=False):
        if idle:
            turn = self.latest()
            if turn['status'] != 'completed' or turn['end'] is None:
                raise RuntimeError('Target is not at a completed, saved turn. Wait or resolve its failure first.')
        else:
            rows = query(database(self.sqlite_home, 'thread_history'),
                "SELECT turn_id,status,rollout_end_byte_offset FROM thread_turns WHERE thread_id=? "
                "AND status='completed' AND rollout_end_byte_offset IS NOT NULL ORDER BY rollout_ordinal DESC LIMIT 1",
                (self.thread,))
            if not rows:
                raise RuntimeError('No completed turn with a saved rollout boundary was found.')
            turn = dict(zip(('turn', 'status', 'end'), rows[0]))
        return turn, self.rollout()

    def continuation(self, source_turn):
        """Return turns after the prediction source and their user messages."""
        db = database(self.sqlite_home, 'thread_history')
        turns = query(db,
            'SELECT turn_id,status FROM thread_turns WHERE thread_id=? AND rollout_ordinal > '
            '(SELECT rollout_ordinal FROM thread_turns WHERE thread_id=? AND turn_id=?) ORDER BY rollout_ordinal',
            (self.thread, self.thread, source_turn))
        result = []
        for turn, status in turns:
            messages = query(db, "SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? "
                "AND item_type='userMessage' ORDER BY rollout_ordinal", (self.thread, turn))
            text = []
            for (raw,) in messages:
                item = json.loads(raw)
                parts = item.get('content', [])
                if any(p.get('type') != 'text' for p in parts):
                    text.append(None)
                else:
                    text.append(''.join(p.get('text', '') for p in parts).strip())
            result.append({'turn': turn, 'status': status, 'messages': text})
        return result


def find_cli(explicit=None):
    if explicit:
        found = shutil.which(explicit)
        if found:
            return found
        raise RuntimeError('The specified Codex CLI executable was not found.')
    for path in (
        '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
        '/Applications/Codex.app/Contents/Resources/codex',
    ):
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    found = shutil.which('codex')
    if found:
        return found
    raise RuntimeError('Codex CLI not found. Supply --cli with the bundled executable.')


def configuration(home):
    try:
        tokens = json.loads((home / 'auth.json').read_text()).get('tokens', {})
        token = tokens['access_token']
    except (OSError, KeyError, ValueError) as exc:
        raise RuntimeError('A ChatGPT login in CODEX_HOME/auth.json is required; keychain-only and API-key-only logins are not supported.') from exc
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    if tokens.get('account_id'):
        headers['ChatGPT-Account-ID'] = tokens['account_id']
    request = urllib.request.Request(CONFIG_URL, headers=headers)
    # Do not forward authentication through redirects to another service.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            config = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Prediction configuration request returned HTTP {exc.code}; no fallback prompt was used.') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Could not reach the prediction configuration endpoint: {exc.reason}') from exc
    if not config.get('is_enabled') or not isinstance(config.get('prompt'), str) or not config['prompt']:
        raise RuntimeError('The live prediction prompt is disabled or unavailable; no fallback prompt was used.')
    return config


def wrapped_prompt(config):
    schema = {'type': 'object', 'properties': {'suggestion': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}},
        'required': ['suggestion'], 'additionalProperties': False}
    return config['prompt'] + '\n\nReturn only JSON matching this schema: ' + json.dumps(schema, separators=(',', ':')) + '.'


def user_fingerprint(records):
    # Identify human steering without treating additional assistant/tool work as a new user.
    messages = [r['payload'] for r in records if r.get('type') == 'response_item'
        and r.get('payload', {}).get('role') == 'user']
    return hashlib.sha256(json.dumps(messages, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def checkpoint_records(records, turn, checkpoint):
    """Close only the copied current turn; never write a synthetic event to the live chat."""
    starts = [r['payload'] for r in records if r.get('type') == 'event_msg'
        and r.get('payload', {}).get('type') == 'task_started']
    if not starts or starts[-1].get('turn_id') != turn['turn']:
        raise RuntimeError('Saved rollout and current turn disagree; retry at a stable checkpoint.')
    if turn['status'] != 'inProgress':
        raise RuntimeError('Same-chat checkpoint mode requires the current active turn.')
    if any(r.get('type') == 'event_msg' and r.get('payload', {}).get('type') == 'task_complete'
           and r['payload'].get('turn_id') == turn['turn'] for r in records):
        raise RuntimeError('The source turn completed during capture; do not apply the active checkpoint.')
    output_ids = {r['payload'].get('call_id') for r in records if r.get('type') == 'response_item'
        and r.get('payload', {}).get('type', '').endswith('_call_output')}
    kept = []
    removed = 0
    for record in records:
        item = record.get('payload', {})
        if (record.get('type') == 'response_item' and item.get('call_id')
                and item.get('type', '').endswith('_call') and item['call_id'] not in output_ids):
            removed += 1
            continue
        kept.append(record)
    stamp = datetime.now(timezone.utc).isoformat()
    ordinal = max(r.get('ordinal', 0) for r in records) + 1
    kept.append({'timestamp': stamp, 'ordinal': ordinal, 'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'status': 'completed',
        'content': [{'type': 'output_text', 'text': checkpoint}]}})
    kept.append({'timestamp': stamp, 'ordinal': ordinal + 1, 'type': 'event_msg', 'payload': {
        'type': 'task_complete', 'root_turn_id': starts[-1].get('root_turn_id', turn['turn']),
        'turn_id': turn['turn'], 'last_agent_message': checkpoint, 'completed_at': int(time.time())}})
    return kept, removed


@contextmanager
def snapshot(source, scratch=None, idle=False, checkpoint=None):
    if checkpoint is not None:
        if idle or not isinstance(checkpoint, str) or not checkpoint.strip():
            raise ValueError('Provide a nonempty checkpoint for same-chat mode; do not combine it with require-idle.')
        turn, rollout = source.latest(), source.rollout()
        original = source.records()
        records, removed = checkpoint_records(original, turn, checkpoint)
        turn = {**turn, 'status': 'completed', 'snapshotMode': 'active-checkpoint',
            'userFingerprint': user_fingerprint(original), 'removedPendingCalls': removed,
            'checkpointDigest': hashlib.sha256(checkpoint.encode()).hexdigest()}
        data = ''.join(json.dumps(r) + '\n' for r in records).encode()
    else:
        turn, rollout = source.boundary(idle=idle)
        with rollout.open('rb') as stream:
            data = stream.read(turn['end'])
        if len(data) != turn['end'] or not data.endswith(b'\n'):
            raise RuntimeError('Snapshot boundary is not a complete saved record.')
    if scratch:
        Path(scratch).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='martin-prince-', dir=scratch) as directory:
        home = Path(directory)
        os.chmod(home, 0o700)
        copied = home / 'sessions' / rollout.name
        copied.parent.mkdir()
        copied.write_bytes(data)
        for name in ('auth.json', 'models_cache.json'):
            if (source.home / name).exists():
                shutil.copyfile(source.home / name, home / name)
                os.chmod(home / name, 0o600)
        (home / 'config.toml').write_text('approval_policy = "never"\nsandbox_mode = "read-only"\n')
        env = dict(os.environ, CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home))
        yield home, copied, turn, env


class RPC:
    def __init__(self, executable, env):
        self.process = subprocess.Popen([executable, 'app-server', '--stdio'], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, start_new_session=True)
        self.messages = queue.Queue()
        self.request_id = 0
        def read():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            except Exception as exc:
                self.messages.put(exc)
            finally:
                self.messages.put(EOFError('App server exited.'))
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def send(self, data):
        self.process.stdin.write(json.dumps(data) + '\n')
        self.process.stdin.flush()

    def call(self, method, params, timeout=90):
        self.request_id += 1
        request_id = self.request_id
        self.send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise TimeoutError(f'{method} timed out.') from exc
            if isinstance(message, Exception):
                raise message
            if 'method' in message and 'id' in message:
                self.send({'id': message['id'], 'error': {'code': -32601, 'message': 'Prediction reader does not execute tools or approvals.'}})
                continue
            if message.get('id') != request_id:
                continue
            if 'error' in message:
                raise RuntimeError(f"{method}: {message['error'].get('message', 'request failed')}")
            return message['result']

    def close(self):
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def predict(source, cli=None, scratch=None, idle=False, checkpoint=None):
    config = configuration(source.home)
    executable = find_cli(cli)
    with snapshot(source, scratch=scratch, idle=idle, checkpoint=checkpoint) as (_, copied, turn, env):
        rpc = RPC(executable, env)
        try:
            rpc.call('initialize', {'clientInfo': {'name': 'martin_prince_loop', 'version': '0.2.0'},
                'capabilities': {'experimentalApi': True}})
            rpc.send({'method': 'initialized'})
            loaded = rpc.call('thread/resume', {'threadId': source.thread, 'path': str(copied)})['thread']
            turns = loaded.get('turns', [])
            if not turns or turns[-1]['id'] != turn['turn'] or turns[-1]['status'] != 'completed':
                raise RuntimeError('Snapshot did not end at the expected completed turn.')
            result = rpc.call('thread/sample', {'threadId': source.thread, 'expectedTurnId': turn['turn'],
                'prompt': wrapped_prompt(config), 'effort': config.get('prediction_reasoning_effort'),
                'threadSource': 'composer_predictions', 'turnTrigger': 'composer_predictions',
                'responsesapiClientMetadata': {'prediction_source_thread_id': source.thread,
                    'prediction_source_turn_id': turn['turn'], 'prediction_trigger': 'catch_up'}, 'disableMcp': True})
        finally:
            rpc.close()
    if result.get('sourceTurnId') != turn['turn']:
        raise RuntimeError('Prediction returned a different source turn.')
    if result.get('text') is None:
        raise RuntimeError('Sampler returned no text.')
    decoded = json.loads(result['text'])
    if not isinstance(decoded, dict) or 'suggestion' not in decoded:
        raise RuntimeError('Sampler did not return a suggestion field.')
    suggestion = decoded['suggestion']
    if suggestion is not None and (not isinstance(suggestion, str) or not suggestion.strip()):
        raise RuntimeError('Invalid suggestion format.')
    return {'threadId': source.thread, 'sourceTurnId': turn['turn'], 'promptVersion': config.get('prompt_version'),
        'method': 'thread/sample', 'context': 'isolated active-turn checkpoint' if checkpoint is not None else 'isolated completed-turn snapshot',
        **{key: turn[key] for key in ('userFingerprint', 'checkpointDigest', 'removedPendingCalls') if key in turn},
        'suggestion': suggestion,
        'responseId': result.get('responseId'),
        'usage': {key: result.get(key) for key in ('inputTokens', 'cachedInputTokens', 'outputTokens')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thread', help='Defaults to the current Codex chat environment.')
    parser.add_argument('--checkpoint-file', help='Factual assistant checkpoint for the current active turn.')
    parser.add_argument('--codex-home')
    parser.add_argument('--sqlite-home')
    parser.add_argument('--cli')
    parser.add_argument('--scratch')
    parser.add_argument('--require-idle', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        result = predict(Source.resolve(args.thread, args.codex_home, args.sqlite_home),
            cli=args.cli, scratch=args.scratch, idle=args.require_idle,
            checkpoint=Path(args.checkpoint_file).read_text() if args.checkpoint_file else None)
        if args.output:
            write_json(args.output, result)
        print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        parser.exit(130, 'Prediction interrupted.\n')
    except Exception as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
