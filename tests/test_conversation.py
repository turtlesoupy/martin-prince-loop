import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conversation


def msg(role, text):
    return {'type':'response_item','payload':{'type':'message','role':role,'content':[{'type':'input_text','text':text}]}}

def start(turn):
    return {'type':'event_msg','payload':{'type':'task_started','turn_id':turn}}

def call(id, output=False):
    return {'type':'response_item','payload':{'type':'function_call_output' if output else 'function_call','call_id':id,'output':'result' if output else None}}


class ConversationTests(unittest.TestCase):
    def test_prompt_and_attachments_survive_without_invocation_or_controller(self):
        prompt = 'Explain the loop shown in this image'
        request = msg('user', '$martin-prince-loop turns=3 timeout=10m ' + prompt)
        attachment = {'type': 'input_image', 'image_url': 'data:image/png;base64,unchanged', 'detail': 'original'}
        request['payload']['content'].append(attachment)
        records = [msg('user', 'prior context'), start('invoke'), msg('developer', 'runtime update'),
            request, msg('user', '<skill>controller instructions</skill>'), call('init')]
        context = conversation.begin(records, 'thread', 'invoke', prompt)
        records += [call('init', True), call('inspect-image'), call('inspect-image', True),
            msg('assistant', 'The image shows a loop.')]
        before = copy.deepcopy(records)
        conversation.capture(context, records)
        closed = conversation.project(records, context, 'thread')
        expected = msg('user', prompt)
        expected['payload']['content'].append(attachment)
        self.assertEqual(closed, [records[0], records[2], expected, *records[-3:]])
        self.assertEqual(records, before)
        records[3]['payload']['content'][0]['text'] = 'changed input'
        with self.assertRaisesRegex(ValueError, 'source changed'):
            conversation.project(records, context, 'thread')

    def test_prompt_must_come_from_current_invocation(self):
        records = [msg('user', 'draw a dog'), start('invoke'), msg('user', '$martin-prince-loop draw a cat'),
            msg('user', '<skill>Example: draw a kitten</skill>')]
        for prompt in ('draw a dog', 'invented task', 'draw a kitten'):
            with self.subTest(prompt=prompt), self.assertRaisesRegex(ValueError, 'verbatim'):
                conversation.begin(records, 'thread', 'invoke', prompt)

    def test_preserves_task_tools_and_messages_excludes_controller(self):
        records = [msg('developer','runtime'), start('task'), msg('user','draw a cat'),
            call('ordinary'), call('ordinary', True), msg('assistant','cat'), start('invoke'),
            msg('user','$martin-prince-loop'), msg('user','<skill>controller</skill>'), call('init')]
        context = conversation.begin(records, 'thread', 'invoke')
        records += [call('init', True), call('queue'), call('queue', True), start('delivered'),
            msg('user','make it sleepy'), msg('assistant','Verifying delivery'), call('resume')]
        conversation.delivered(context, records, 'delivered')
        records += [call('resume', True), call('drawing-tool'), call('drawing-tool', True),
            msg('assistant','sleepy cat')]
        before = copy.deepcopy(records)
        conversation.capture(context, records)
        closed = conversation.project(records, context, 'thread')
        self.assertEqual(records, before)
        for id in ('ordinary','drawing-tool'):
            self.assertEqual(sum(r.get('payload',{}).get('call_id') == id for r in closed), 2)
        for id in ('init','queue','resume'):
            self.assertFalse(any(r.get('payload',{}).get('call_id') == id for r in closed))
        for text in ('$martin-prince-loop','<skill>controller</skill>','Verifying delivery'):
            self.assertNotIn(msg('user',text), closed)
            self.assertNotIn(msg('assistant',text), closed)
        self.assertIn(msg('user','make it sleepy'), closed)
        self.assertIn(msg('assistant','sleepy cat'), closed)

    def test_real_meta_content_is_preserved_by_provenance(self):
        records = [msg('user','Explain a programming loop'), call('tool'),call('tool',True),start('invoke')]
        context = conversation.begin(records,'thread','invoke')
        self.assertEqual(conversation.project(records,context,'thread'),records[:3])

    def test_wrong_thread_or_mutated_history_fails_closed(self):
        records = [msg('user','cat'), start('invoke')]
        context = conversation.begin(records,'thread','invoke')
        with self.assertRaises(ValueError): conversation.project(records,context,'other')
        records[0] = msg('user','dog')
        with self.assertRaisesRegex(ValueError,'source changed'): conversation.project(records,context,'thread')
