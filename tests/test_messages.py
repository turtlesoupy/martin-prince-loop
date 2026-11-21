import unittest
import loop
import test_same_chat


class MessageTests(unittest.TestCase):
    predict = test_same_chat.SameChatTests.predict
    do = test_same_chat.SameChatTests.do
    def setUp(self):
        test_same_chat.SameChatTests.setUp(self)
        self.state['mode'] = 'chat-messages'
        self.args.receipt = 'accepted'
        self.following = []
        self.source.continuation = lambda _: self.following

    def test_real_message_delivery_and_repeat_reach_limit(self):
        for i in range(2):
            self.checkpoint.write_text(f'Answered {i} delivered messages.')
            self.do('next'); self.do('prepare-send'); self.do('sent')
            self.assertEqual(self.state['phase'], 'waiting')
            self.source.turn = f'delivered-{i}'
            self.source.fingerprint = f'input-{i}'
            self.following = [{'turn': self.source.turn, 'status': 'inProgress', 'messages': [self.suggestion]}]
            self.do('resume-message')
        self.assertEqual(self.state['phase'], 'complete')
        self.assertEqual(self.state['completedIterations'], 2)

    def test_wrong_message_halts(self):
        self.do('next'); self.do('prepare-send'); self.do('sent')
        self.following = [{'turn':'human', 'status':'inProgress', 'messages':['Stop']}]
        self.do('resume-message')
        self.assertEqual(self.state['phase'], 'stopped')

    def send(self, sender, retry=False):
        self.args.retry = retry
        return loop.operate(self.state, 'send', self.args, source=self.source, predictor=self.predict,
            sender=sender, persist=self.saved.append)

    def test_send_queues_exact_text_once_then_waits(self):
        self.saved, sent = [], []
        def sender(state, text, timeout):
            sent.append((text, timeout)); return 'queued item-1'
        self.args.timeout = 12
        self.do('next'); self.do('prepare-send'); self.send(sender)
        self.assertEqual(sent, [(self.suggestion, 12)])
        self.assertEqual(self.state['proposal']['receipt'], 'queued item-1')
        self.assertEqual(len(self.saved), 1)  # Attempt recorded before sending.
        with self.assertRaisesRegex(RuntimeError, 'No prepared'): self.send(sender)
        self.assertEqual(len(sent), 1)

    def test_failed_send_needs_manual_retry(self):
        self.saved, calls = [], []
        def sender(state, text, timeout):
            calls.append(text)
            if len(calls) == 1: raise RuntimeError('codex queue timed out')
            return 'queued item-2'
        self.do('next'); self.do('prepare-send')
        with self.assertRaisesRegex(RuntimeError, 'timed out'): self.send(sender)
        self.assertEqual(self.state['phase'], 'dispatching')
        with self.assertRaisesRegex(RuntimeError, 'send --retry'): self.send(sender)
        self.assertEqual(len(calls), 1)
        self.send(sender, retry=True)
        self.assertEqual(self.state['phase'], 'waiting')
        self.assertEqual(self.state['proposal']['sendAttempts'], 2)

    def test_retry_refuses_when_message_already_arrived(self):
        self.saved = []
        def sender(state, text, timeout): raise RuntimeError('codex queue timed out')
        self.do('next'); self.do('prepare-send')
        with self.assertRaises(RuntimeError): self.send(sender)
        self.following = [{'turn': 'delivered', 'status': 'inProgress', 'messages': [self.suggestion]}]
        with self.assertRaisesRegex(RuntimeError, 'resume-message'): self.send(sender, retry=True)
        self.assertEqual(self.state['proposal']['sendAttempts'], 1)


class PredictionRetryTests(unittest.TestCase):
    predict = test_same_chat.SameChatTests.predict
    def setUp(self):
        test_same_chat.SameChatTests.setUp(self)
        self.delays = []
        original = loop.sleep
        loop.sleep = self.delays.append
        self.addCleanup(setattr, loop, 'sleep', original)

    def flaky(self, failures):
        def predictor(source, **kwargs):
            self.attempts = getattr(self, 'attempts', 0) + 1
            self.assertEqual(kwargs['timeout'], 5)
            if self.attempts <= failures: raise loop.TransientError('thread/sample timed out')
            return self.predict(source, **kwargs)
        return predictor

    def run_next(self, predictor):
        return loop.operate(self.state, 'next', self.args, source=self.source, predictor=predictor)

    def test_transient_failures_retry_with_backoff(self):
        self.state.update(retries=2, timeout=5)
        self.run_next(self.flaky(2))
        self.assertEqual(self.state['phase'], 'proposed')
        self.assertEqual(self.delays, [1, 2])
        self.assertEqual(len(self.state['retryLog']), 2)

    def test_retries_default_to_zero(self):
        self.state['timeout'] = 5
        with self.assertRaisesRegex(RuntimeError, 'after 1 attempt'): self.run_next(self.flaky(1))
        self.assertEqual(self.delays, [])

    def test_command_flags_override_run_settings(self):
        self.state.update(retries=0, timeout=60)
        self.args.retries, self.args.timeout = 1, 5
        self.run_next(self.flaky(1))
        self.assertEqual(self.state['phase'], 'proposed')

    def test_non_transient_errors_are_not_retried(self):
        self.state.update(retries=3, timeout=5)
        def broken(source, **kwargs): raise RuntimeError('Live prediction prompt is disabled')
        with self.assertRaisesRegex(RuntimeError, 'disabled'): self.run_next(broken)
        self.assertEqual(self.delays, [])
