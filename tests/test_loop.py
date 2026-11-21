from pathlib import Path
from types import SimpleNamespace
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import loop


class FakeSource:
    def __init__(self):
        self.current = {'turn':'original','status':'completed'}
        self.following = []
    def latest(self): return self.current
    def continuation(self, _): return self.following


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.state = {'mode':'supervise','phase':'ready','deadline':time.time()+600,'maxIterations':2,
            'completedIterations':0,'history':[],'cli':None,'scratch':None}
        self.source = FakeSource()
        self.args = SimpleNamespace(response_id='response', review='Within the authorized test task', receipt='accepted', reason='User stopped')
        self.suggestion = 'Run the checks'
        self.samples = 0

    def predict(self, source, **_):
        self.samples += 1
        return {'sourceTurnId':source.latest()['turn'],'responseId':'response','suggestion':self.suggestion}

    def do(self, command):
        return loop.operate(self.state, command, self.args, source=self.source, predictor=self.predict)

    def dispatch(self):
        self.do('next'); self.do('prepare-send'); self.do('sent')

    def finish(self, turn):
        self.source.current = {'turn':turn,'status':'completed'}
        self.source.following = [{'turn':turn,'status':'completed','messages':[self.suggestion]}]
        self.do('complete-step')

    def test_two_complete_iterations_enforce_budget(self):
        self.dispatch(); self.finish('step-one')
        self.assertEqual(self.state['phase'], 'ready')
        self.suggestion = 'Summarize the verified result'
        self.dispatch(); self.finish('step-two')
        self.assertEqual(self.state['completedIterations'], 2)
        self.assertEqual(self.state['phase'], 'complete')
        with self.assertRaisesRegex(RuntimeError, 'ended'): self.do('next')
        self.assertEqual(self.samples, 2)

    def test_ambiguous_send_is_never_replayed_and_can_be_reconciled(self):
        self.do('next'); self.do('prepare-send')
        # Simulated crash after connector acceptance but before recording sent.
        with self.assertRaisesRegex(RuntimeError, 'resolve'): self.do('next')
        with self.assertRaisesRegex(RuntimeError, 'Only a proposed'): self.do('prepare-send')
        self.assertEqual(self.samples, 1)
        self.finish('delivered-once')
        self.assertEqual(self.state['completedIterations'], 1)

    def test_stale_prediction_cannot_be_prepared(self):
        self.do('next')
        self.source.current = {'turn':'human-intervened','status':'completed'}
        self.do('prepare-send')
        self.assertEqual(self.state['phase'], 'stopped')
        self.assertIn('changed', self.state['stopReason'])

    def test_target_change_during_generation_stops(self):
        def racing_predict(source, **kwargs):
            proposal = self.predict(source, **kwargs)
            source.current = {'turn':'new-input','status':'inProgress'}
            return proposal
        loop.operate(self.state, 'next', self.args, source=self.source, predictor=racing_predict)
        self.assertEqual(self.state['phase'], 'stopped')

    def test_unrelated_or_additional_user_messages_stop(self):
        self.dispatch()
        self.source.current = {'turn':'different','status':'completed'}
        self.source.following = [{'turn':'different','status':'completed','messages':['Do unrelated work']}]
        self.do('complete-step')
        self.assertEqual(self.state['phase'], 'stopped')
        self.assertEqual(self.state['completedIterations'], 0)

    def test_duplicate_proposal_does_not_generate_twice(self):
        self.do('next'); self.do('next')
        self.assertEqual(self.samples, 1)

    def test_repeated_suggestion_is_allowed(self):
        self.dispatch(); self.finish('first')
        self.do('next')
        self.assertEqual(self.state['phase'], 'proposed')
        self.assertEqual(self.state['proposal']['suggestion'], self.state['history'][0]['suggestion'])

    def test_expired_budget_cannot_dispatch_existing_proposal(self):
        self.do('next'); self.state['deadline'] = time.time()-1
        self.do('prepare-send')
        self.assertEqual(self.state['phase'], 'stopped')

    def test_null_prediction_stops(self):
        self.suggestion = None
        self.do('next')
        self.assertEqual(self.state['phase'], 'stopped')

    def test_failure_is_not_counted_as_completion(self):
        self.dispatch()
        self.source.following = [{'turn':'failed','status':'failed','messages':[self.suggestion]}]
        self.do('complete-step')
        self.assertEqual(self.state['phase'], 'stopped')
        self.assertEqual(self.state['completedIterations'], 0)

    def test_wrong_reviewed_response_id_is_rejected(self):
        self.do('next'); self.args.response_id = 'wrong'
        with self.assertRaisesRegex(RuntimeError, 'does not match'): self.do('prepare-send')
        self.assertEqual(self.state['phase'], 'proposed')
