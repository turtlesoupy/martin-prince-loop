from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import loop


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
