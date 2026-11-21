import unittest
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
