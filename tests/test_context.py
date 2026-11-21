import copy
import hashlib
import json
import unittest
from test_prediction import predict


def message(role, text):
    return {'type':'response_item','payload':{'type':'message','role':role,'content':[{'type':'input_text' if role=='user' else 'output_text','text':text}]}}


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {'type':'session_meta','payload':{'id':'target'}},
            message('developer','Preserve runtime constraints'),
            {'type':'event_msg','payload':{'type':'task_started','turn_id':'active'}},
            message('user','Fix the README overflow'),
            message('assistant','The artwork now scales.'),
            message('user','<skill>Loop instructions</skill>'),
            message('assistant','3/5 delivered; queued next'),
            {'type':'compacted','payload':{'message':'Loop bookkeeping summary'}},
            {'type':'event_msg','payload':{'type':'agent_message','message':'3/5 delivered'}},
            {'type':'response_item','payload':{'type':'function_call','call_id':'call','arguments':'loop.py next'}},
            {'type':'response_item','payload':{'type':'function_call_output','call_id':'call','output':'queue receipt'}},
            message('user','Keep running the loop'),
        ]
        self.context={'version':1,'threadId':'target','messages':[
            {'index':i,'sha256':hashlib.sha256(json.dumps(self.records[i],sort_keys=True).encode()).hexdigest()} for i in (3,4)]}

    def test_only_exact_task_messages_and_runtime_constraints_survive(self):
        before=copy.deepcopy(self.records)
        projected=predict.task_projection(self.records,self.context,'target')
        self.assertEqual(projected,self.records[:5])
        closed,_=predict.checkpoint_records(projected,{'turn':'active','status':'inProgress'},'The SVG scales.')
        text=json.dumps(closed)
        for contaminant in ('3/5','bookkeeping','queue receipt','loop.py','Keep running','<skill>'):
            self.assertNotIn(contaminant,text)
        self.assertEqual(self.records,before)
        self.assertIn('Preserve runtime constraints',text)

    def test_mutated_source_fails_closed(self):
        self.records[3]=message('user','Different request')
        with self.assertRaisesRegex(ValueError,'source changed'):
            predict.task_projection(self.records,self.context,'target')

    def test_thread_mismatch_and_reordered_messages_rejected(self):
        with self.assertRaises(ValueError):predict.task_projection(self.records,self.context,'other')
        self.context['messages'].reverse()
        with self.assertRaises(ValueError):predict.task_projection(self.records,self.context,'target')

    def test_real_user_meta_request_is_not_keyword_filtered(self):
        i=11
        self.context['messages'].append({'index':i,'sha256':hashlib.sha256(json.dumps(self.records[i],sort_keys=True).encode()).hexdigest()})
        self.assertIn('Keep running the loop',json.dumps(predict.task_projection(self.records,self.context,'target')))
