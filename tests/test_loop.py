from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import loop


class StartTests(unittest.TestCase):
    def start(self, *options):
        _, args = loop.parse_args(['start', '--state', '/unused/state.json', *options])
        source = SimpleNamespace(thread='thread', home=Path('/source'), sqlite_home=Path('/source'),
            latest=lambda: {'turn': 'invoke', 'status': 'inProgress'}, user_fingerprint=lambda: 'fingerprint',
            records=lambda: [{'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'invoke'}}])
        with patch.object(loop.Source, 'resolve', return_value=source), patch.object(loop.time, 'time', return_value=1000):
            return loop.start(args)

    def test_default_and_custom_budgets(self):
        defaults = self.start()
        self.assertEqual(defaults['maxIterations'], 5)
        self.assertEqual(defaults['deadline'], 2800)
        for duration, seconds in [('90', 90), ('90s', 90), ('2.5m', 150), ('1h', 3600)]:
            with self.subTest(duration=duration):
                state = self.start('--turns', '3', '--run-timeout', duration, '--request-timeout', '7')
                self.assertEqual(state['maxIterations'], 3)
                self.assertEqual(state['deadline'], 1000 + seconds)
                self.assertEqual(state['timeout'], 7)
        legacy = self.start('--max-iterations', '2', '--max-minutes', '4', '--timeout', '8')
        self.assertEqual((legacy['maxIterations'], legacy['deadline'], legacy['timeout']), (2, 1240, 8))

    def test_invalid_limits_and_empty_prompt_fail_before_reading_source(self):
        for options in [('--turns', '0'), ('--max-minutes', 'nan'), ('--max-minutes', '-1'),
                ('--request-timeout', '0'), ('--prompt', '  ')]:
            with self.subTest(options=options):
                _, args = loop.parse_args(['start', '--state', '/unused/state.json', *options])
                with patch.object(loop.Source, 'resolve') as resolve, self.assertRaises(ValueError):
                    loop.start(args)
                resolve.assert_not_called()
        for value in ('0', '-1s', 'nan', 'inf', 'forever', '1e309s'):
            with self.subTest(duration=value), self.assertRaises(loop.argparse.ArgumentTypeError):
                loop.duration(value)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.state = {'cli': None, 'scratch': None, 'timeout': 5, 'deadline': time.time() + 600}
        self.delays, self.attempts = [], 0
        original = loop.sleep
        loop.sleep = self.delays.append
        self.addCleanup(setattr, loop, 'sleep', original)

    def flaky(self, failures):
        def predictor(source, **kwargs):
            self.attempts += 1
            self.assertEqual(kwargs['timeout'], 5)
            if self.attempts <= failures: raise loop.TransientError('thread/sample timed out')
            return {'suggestion': 'Run the checks'}
        return predictor

    def test_transient_failures_retry_with_backoff(self):
        self.state['retries'] = 2
        self.assertEqual(loop.sample(self.state, None, self.flaky(2))['suggestion'], 'Run the checks')
        self.assertEqual(self.delays, [1, 2])
        self.assertEqual(len(self.state['retryLog']), 2)

    def test_retries_default_to_zero(self):
        with self.assertRaisesRegex(RuntimeError, 'after 1 attempt'): loop.sample(self.state, None, self.flaky(1))
        self.assertEqual(self.delays, [])

    def test_retries_stop_at_the_deadline(self):
        self.state.update(retries=5, deadline=time.time() + 0.5)
        with self.assertRaisesRegex(RuntimeError, 'after 1 attempt'): loop.sample(self.state, None, self.flaky(5))

    def test_non_transient_errors_are_not_retried(self):
        self.state['retries'] = 3
        def broken(source, **kwargs): raise RuntimeError('Live prediction prompt is disabled')
        with self.assertRaisesRegex(RuntimeError, 'disabled'): loop.sample(self.state, None, broken)
        self.assertEqual(self.delays, [])


class LockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'

    def hold(self, seconds):
        held = threading.Event()
        def holder():
            with loop.locked(self.path):
                held.set(); time.sleep(seconds)
        thread = threading.Thread(target=holder); thread.start()
        self.addCleanup(thread.join)
        held.wait()

    def test_busy_lock_fails_immediately_by_default(self):
        self.hold(0.3)
        with self.assertRaises(loop.LockBusy):
            with loop.locked(self.path): pass

    def test_controller_waits_out_a_brief_holder(self):
        self.hold(0.3)
        with loop.locked(self.path, wait=5): pass


class LateDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.state = {'phase': 'stopped', 'stopReason': 'Time budget reached.', 'completedIterations': 1,
            'maxIterations': 5, 'history': [], 'proposal': {'sourceTurnId': 'source', 'suggestion': 'Run the checks'}}

    def resume(self, messages):
        source = SimpleNamespace(continuation=lambda turn: [{'turn': 'late', 'status': 'inProgress', 'messages': messages}])
        return loop.operate(self.state, 'resume-message', SimpleNamespace(), source=source)

    def test_message_queued_before_the_stop_is_accepted_without_restarting(self):
        self.resume(['Run the checks'])
        self.assertEqual(self.state['phase'], 'stopped')
        self.assertEqual(self.state['completedIterations'], 2)
        self.assertEqual(self.state['history'][0]['deliveredTurnId'], 'late')
        self.assertTrue(self.state['lateDelivery'])
        self.assertNotIn('proposal', self.state)

    def test_other_input_after_the_stop_is_still_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'ended'): self.resume(['Something the user typed'])
        self.assertEqual(self.state['completedIterations'], 1)
