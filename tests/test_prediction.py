import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import predict


class HistoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'source'
        self.home.mkdir()
        self.scratch = Path(self.temp.name) / 'scratch'
        self.scratch.mkdir()
        self.rollout = self.home / 'rollout.jsonl'
        self.prefix = b'{"type":"session_meta"}\n{"type":"completed"}\n'
        self.rollout.write_bytes(self.prefix + b'{"type":"in_progress"}\n')
        (self.home / 'auth.json').write_text('{"tokens":{"access_token":"synthetic-test-token"}}')
        con = sqlite3.connect(self.home / 'state_5.sqlite')
        con.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT)')
        con.execute('INSERT INTO threads VALUES (?,?)', ('target', str(self.rollout)))
        con.commit(); con.close()
        self.history = self.home / 'thread_history_1.sqlite'
        con = sqlite3.connect(self.history)
        con.execute('CREATE TABLE thread_turns(thread_id TEXT, turn_id TEXT, status TEXT, rollout_ordinal INT, rollout_end_byte_offset INT)')
        con.execute('CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,item_type TEXT,item_json TEXT,rollout_ordinal INT)')
        con.executemany('INSERT INTO thread_turns VALUES (?,?,?,?,?)', [
            ('target', 'done', 'completed', 1, len(self.prefix)),
            ('target', 'busy', 'inProgress', 2, None),
            ('other', 'unrelated', 'completed', 90, 999)])
        con.commit(); con.close()
        self.source = predict.Source(self.home, self.home, 'target')

    def test_snapshot_excludes_active_turn_and_does_not_change_source(self):
        before = {p.name: p.read_bytes() for p in self.home.iterdir()}
        with predict.snapshot(self.source, self.scratch) as (home, copied, turn, env):
            self.assertEqual(copied.read_bytes(), self.prefix)
            self.assertEqual(turn['turn'], 'done')
            self.assertEqual(env['CODEX_HOME'], str(home))
            self.assertEqual(env['CODEX_SQLITE_HOME'], str(home))
            self.assertEqual((home / 'auth.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual({p.name for p in home.iterdir()}, {'sessions','auth.json','config.toml'})
        self.assertFalse(home.exists())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.home.iterdir()})

    def test_credentials_cleaned_after_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            with predict.snapshot(self.source, self.scratch) as (home, _, _, _):
                raise RuntimeError('injected')
        self.assertFalse(home.exists())

    def test_busy_target_rejected_in_controller_mode(self):
        with self.assertRaisesRegex(RuntimeError, 'completed, saved'):
            with predict.snapshot(self.source, self.scratch, idle=True):
                self.fail('Busy target must not be sampled by the controller')

    def test_truncated_rollout_is_rejected(self):
        self.rollout.write_bytes(b'partial')
        with self.assertRaisesRegex(RuntimeError, 'boundary'):
            with predict.snapshot(self.source, self.scratch):
                pass
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_predict_uses_live_prompt_and_cleans_up(self):
        calls = []
        class FakeRPC:
            def __init__(self, executable, env):
                self.home = Path(env['CODEX_HOME'])
            def send(self, data): pass
            def close(self): calls.append(('closed', self.home))
            def call(self, method, params):
                calls.append((method, params))
                if method == 'thread/resume':
                    return {'thread': {'turns': [{'id':'done','status':'completed'}]}}
                if method == 'thread/sample':
                    self_outer.assertIn('live-test-prompt', params['prompt'])
                    self_outer.assertTrue(params['disableMcp'])
                    return {'sourceTurnId':'done','responseId':'synthetic-response','text':'{"suggestion":"Run the checks"}'}
                return {}
        self_outer = self
        with patch.object(predict, 'configuration', return_value={'prompt':'live-test-prompt','prompt_version':'test'}), \
             patch.object(predict, 'find_cli', return_value='fake'), patch.object(predict, 'RPC', FakeRPC):
            result = predict.predict(self.source, scratch=self.scratch)
        self.assertEqual(result['suggestion'], 'Run the checks')
        self.assertEqual(result['promptVersion'], 'test')
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(calls[-1][0], 'closed')

    def test_private_output_replaces_an_existing_file(self):
        path = Path(self.temp.name) / 'state.json'
        path.write_text('{}'); os.chmod(path, 0o644)
        predict.write_json(path, {'phase':'ready'})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()), {'phase':'ready'})
