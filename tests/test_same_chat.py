import hashlib
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import loop


class ActiveSource:
    def __init__(self):
        self.turn = 'active'
        self.fingerprint = 'human-input'
    def latest(self): return {'turn':self.turn,'status':'inProgress'}
    def user_fingerprint(self): return self.fingerprint


class SameChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.checkpoint = Path(self.temp.name)/'checkpoint.txt'
        self.result = Path(self.temp.name)/'result.txt'
        self.checkpoint.write_text('The existing code has been inspected.')
        self.result.write_text('Fixed the reported defect and verified its behavior.')
        self.source = ActiveSource()
        self.state = {'mode':'same-chat','phase':'ready','deadline':time.time()+600,
            'maxIterations':2,'completedIterations':0,'history':[], 'cli':None,'scratch':None,
            'originTurnId':'active','userFingerprint':'human-input'}
        self.args = SimpleNamespace(checkpoint_file=str(self.checkpoint),result_file=str(self.result),
            response_id='sample',review='Within the requested fixture fix',reason='User stopped')
        self.suggestion = 'Fix the reported defect'
        self.samples = []

    def predict(self, source, **kwargs):
        checkpoint = kwargs['checkpoint']
        self.samples.append(checkpoint)
        return {'sourceTurnId':source.latest()['turn'],'responseId':'sample','suggestion':self.suggestion,
            'userFingerprint':source.user_fingerprint(),
            'checkpointDigest':hashlib.sha256(checkpoint.encode()).hexdigest()}

    def do(self, command):
        return loop.operate(self.state,command,self.args,source=self.source,predictor=self.predict)

    def test_two_actions_execute_within_one_active_turn(self):
        self.do('next');self.do('begin-step');self.do('finish-step')
        self.assertEqual(self.state['phase'],'ready')
        self.checkpoint.write_text('The defect is fixed and its behavior is verified.')
        self.suggestion = 'Update the fixture documentation'
        self.do('next');self.do('begin-step');self.do('finish-step')
        self.assertEqual(self.state['phase'],'complete')
        self.assertEqual(self.state['completedIterations'],2)
        self.assertEqual(len(set(self.samples)),2)
        self.assertEqual(self.source.latest()['turn'],'active')

    def test_never_needs_messaging_or_completed_target_turns(self):
        self.do('next');self.do('begin-step')
        with self.assertRaisesRegex(RuntimeError,'not a same-chat command'):self.do('sent')
        self.do('finish-step')
        self.assertEqual(self.source.latest()['status'],'inProgress')

    def test_does_not_repeat_action_after_interruption(self):
        self.do('next');self.do('begin-step')
        with self.assertRaisesRegex(RuntimeError,'reconcile'):self.do('next')
        with self.assertRaisesRegex(RuntimeError,'replay'):self.do('begin-step')
        self.assertEqual(len(self.samples),1)

    def test_changed_human_input_halts_before_action(self):
        self.do('next');self.source.fingerprint='new-human-input'
        self.do('begin-step')
        self.assertEqual(self.state['phase'],'stopped')
        self.assertEqual(self.state['completedIterations'],0)

    def test_new_turn_is_not_silently_adopted(self):
        self.source.turn='new-turn';self.do('next')
        self.assertEqual(self.state['phase'],'stopped')
        self.assertEqual(self.samples,[])

    def test_checkpoint_and_evidence_are_required(self):
        self.args.checkpoint_file=None
        with self.assertRaisesRegex(ValueError,'checkpoint-file'):self.do('next')
        self.args.checkpoint_file=str(self.checkpoint)
        self.do('next');self.do('begin-step');self.result.write_text('')
        with self.assertRaisesRegex(ValueError,'actual result'):self.do('finish-step')
        self.assertEqual(self.state['phase'],'executing')

    def test_unchanged_checkpoint_cannot_drive_next_iteration(self):
        self.do('next');self.do('begin-step');self.do('finish-step');self.do('next')
        self.assertEqual(self.state['phase'],'stopped')
        self.assertEqual(len(self.samples),1)

    def test_budget_blocks_beginning_an_action(self):
        self.do('next');self.state['deadline']=time.time()-1;self.do('begin-step')
        self.assertEqual(self.state['phase'],'stopped')
