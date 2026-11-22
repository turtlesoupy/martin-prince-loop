import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import benchmark
import conversation


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'rollout.jsonl'
        self.output = Path(self.temp.name) / 'report.json'
        records = [{'type':'session_meta','payload':{'id':'chat'}}]
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in records))
        self.turn = {'turn':'finished','status':'completed','end':self.path.stat().st_size}
        self.state = {'mode':'background','phase':'complete','threadId':'chat','originTurnId':'finished',
            'codexHome':self.temp.name,'sqliteHome':self.temp.name,
            'conversationContext':{'version':1,'threadId':'chat',
                'segments':[conversation.segment(records,0,len(records))]}}
        owner = self
        class Source:
            thread = 'chat'
            def boundary(self, idle=False): return copy.deepcopy(owner.turn), owner.path
            def latest(self): return copy.deepcopy(owner.turn)
            def user_fingerprint(self): return 'original'
        self.source = Source()
        self.calls = []

    def predictor(self, source, **kwargs):
        self.calls.append(kwargs)
        return {'sourceTurnId':'finished','userFingerprint':'original',
            'conversationDigest':conversation.digest(kwargs['loop_context']),
            'configurationCached':len(self.calls)>1, 'suggestion':'make it sleepy',
            'timingsSeconds':{'configuration':.2,'sample':5,'total':5.2},
            'usage':{'outputTokens':20}}

    def run_benchmark(self, predictor=None):
        with patch.object(benchmark.Source,'resolve',return_value=self.source), contextlib.redirect_stdout(io.StringIO()):
            return benchmark.benchmark(self.state, 2, self.output, predictor or self.predictor)

    def test_report_reuses_cache_and_preserves_finished_run(self):
        before_state, before_rollout = copy.deepcopy(self.state), self.path.read_bytes()
        report = self.run_benchmark()
        self.assertEqual(len(report['samples']),2)
        self.assertIs(self.calls[0]['config_cache'], self.calls[1]['config_cache'])
        self.assertTrue(all(call['idle'] and call['completed_context'] for call in self.calls))
        self.assertEqual(self.state,before_state)
        self.assertEqual(self.path.read_bytes(),before_rollout)
        self.assertEqual(json.loads(self.output.read_text()),report)
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)

    def test_active_loop_is_rejected_before_sampling(self):
        self.state['phase']='waiting'
        with self.assertRaisesRegex(ValueError,'never joins an active'):
            self.run_benchmark()
        self.assertEqual(self.calls,[])

    def test_human_input_during_prediction_stops_remaining_trials(self):
        def changing(source, **kwargs):
            result=self.predictor(source,**kwargs)
            self.turn['turn']='new-human-input'
            return result
        with self.assertRaisesRegex(RuntimeError,'Chat changed'):
            self.run_benchmark(changing)
        self.assertEqual(len(self.calls),1)
        self.assertFalse(self.output.exists())

    def test_mismatched_snapshot_is_rejected(self):
        def wrong_context(source, **kwargs):
            result=self.predictor(source,**kwargs)
            result['conversationDigest']='wrong'
            return result
        with self.assertRaisesRegex(RuntimeError,'frozen benchmark'):
            self.run_benchmark(wrong_context)
        self.assertFalse(self.output.exists())
