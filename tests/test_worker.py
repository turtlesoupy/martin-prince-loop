import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import conversation
import loop
import worker
import test_prediction
from test_prediction import predict


def message(role, text, phase=None):
    p = {'type':'message','role':role,'content':[{'type':'output_text' if role=='assistant' else 'input_text','text':text}]}
    if phase: p['phase'] = phase
    return {'type':'response_item','payload':p}


def event(kind, turn):
    return {'type':'event_msg','payload':{'type':kind,'turn_id':turn}}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        test_prediction.HistoryFixture.setUp(self)
        self.records = [{'type':'session_meta','payload':{'id':'target'}}, event('task_started','done'),
            message('user','draw an ASCII cat'), message('assistant','/\\_/\\ (=^.^=)', 'final_answer'),
            event('task_complete','done'), event('task_started','busy'),
            message('user','$martin-prince-loop'), message('assistant','Starting loop','commentary')]
        self.save_records('inProgress')
        self.state = {'version':2,'mode':'background','threadId':'target','scope':'Draw ASCII cats',
            'phase':'ready','deadline':time.time()+600,'maxIterations':2,'completedIterations':0,'history':[],
            'cli':None,'scratch':str(self.scratch),'timeout':5,'retries':0,
            'codexHome':str(self.home),'sqliteHome':str(self.home),
            'originTurnId':'busy','userFingerprint':self.source.user_fingerprint(),
            'conversationContext':conversation.begin(self.records,'target','busy')}
        self.path = self.home/'run.json'
        predict.write_json(self.path,self.state)
        self.sampled, self.sent = [], []

    def save_records(self, status):
        self.rollout.write_text(''.join(json.dumps(r)+'\n' for r in self.records))
        con = sqlite3.connect(self.history)
        con.execute('UPDATE thread_turns SET status=?,rollout_end_byte_offset=? WHERE thread_id=? AND turn_id=?',
            (status, self.rollout.stat().st_size if status=='completed' else None, 'target','busy'))
        con.commit(); con.close()

    def finish(self):
        self.records += [message('assistant','', 'final_answer'), event('task_complete','busy')]
        self.save_records('completed')

    def predictor(self, source, **kwargs):
        self.assertNotIn('checkpoint',kwargs)
        self.assertTrue(kwargs['idle'])
        self.assertTrue(kwargs['completed_context'])
        with predict.snapshot(source, scratch=self.scratch, idle=True,
                loop_context=kwargs['loop_context'], completed_context=True) as (_, copied, turn, _):
            snapshot = copied.read_text()
            self.sampled.append(snapshot)
            self.assertNotIn('$martin-prince-loop',snapshot)
            self.assertNotIn('Starting loop',snapshot)
            replies = [json.loads(line)['payload'] for line in snapshot.splitlines()
                if json.loads(line).get('type')=='response_item' and json.loads(line)['payload'].get('role')=='assistant']
            self.assertEqual(replies[-1]['content'][0]['text'], '/\\_/\\ (=^.^=)')
            return {'sourceTurnId':turn['turn'],'responseId':'sample','suggestion':'make it sleepy',
                'userFingerprint':turn['userFingerprint'],'conversationDigest':turn['conversationDigest']}

    def sender(self, state, text, timeout):
        on_disk = json.loads(self.path.read_text())
        self.assertEqual(on_disk['phase'],'dispatching')
        self.assertEqual(on_disk['proposal']['sendAttempts'],1)
        self.sent.append(text)
        return 'queued-1'

    def tick(self, predictor=None, sender=None):
        return worker.tick(self.path, predictor or self.predictor, sender or self.sender, self.source)

    def test_waits_for_final_reply_then_samples_and_queues_once(self):
        self.assertTrue(self.tick())
        self.assertEqual(self.sampled,[])
        self.finish()
        self.assertTrue(self.tick())
        self.assertEqual(len(self.sampled),1)
        self.assertEqual(self.sent,['make it sleepy'])
        self.assertTrue(self.tick())
        self.assertEqual(len(self.sampled),1)

    def test_supplied_prompt_runs_before_first_prediction_with_actual_answer(self):
        prompt = 'draw a puppy from this image'
        image = {'type':'input_image','image_url':'data:image/png;base64,original-image'}
        request = message('user', '$martin-prince-loop turns=1 timeout=10m ' + prompt)
        request['payload']['content'].append(image)
        self.records[7:] = [request, message('user', '<skill>loop setup instructions</skill>'),
            {'type':'response_item','payload':{'type':'function_call','call_id':'start','arguments':'loop.py start'}}]
        self.save_records('inProgress')
        _, args = loop.parse_args(['start','--state',str(self.path),'--turns','1','--run-timeout','10m',
            '--prompt',prompt,'--scratch',str(self.scratch)])
        with patch.object(loop.Source, 'resolve', return_value=self.source):
            state = loop.start(args)
        predict.write_json(self.path, state)
        def unexpected(*args, **kwargs):
            self.fail('No prediction before the supplied task is answered')
        self.assertTrue(self.tick(predictor=unexpected))
        answer = ' / \\__\n(    @\\___\n /         O'
        self.records += [
            {'type':'response_item','payload':{'type':'function_call_output','call_id':'start','output':'controller instruction'}},
            {'type':'response_item','payload':{'type':'function_call','call_id':'draw','arguments':'draw puppy'}},
            {'type':'response_item','payload':{'type':'function_call_output','call_id':'draw','output':'puppy drawing'}},
            message('assistant',answer,'final_answer'),event('task_complete','busy')]
        self.save_records('completed')
        def next_prediction(source, **kwargs):
            with predict.snapshot(source, scratch=self.scratch, idle=True,
                    loop_context=kwargs['loop_context'], completed_context=True) as (_, copied, turn, _):
                rows = [json.loads(line) for line in copied.read_text().splitlines()]
                inputs = [r['payload'] for r in rows if r.get('type') == 'response_item' and r['payload'].get('role') == 'user']
                self.assertEqual(inputs[-1]['content'], [{'type':'input_text','text':prompt}, image])
                responses = [r['payload'] for r in rows if r.get('type') == 'response_item' and r['payload'].get('role') == 'assistant']
                self.assertEqual(responses[-1]['content'][0]['text'], answer)
                calls = [r['payload'].get('call_id') for r in rows if r.get('type') == 'response_item' and 'call_id' in r['payload']]
                self.assertEqual(calls, ['draw', 'draw'])
                text = copied.read_text()
                for excluded in ('$martin-prince-loop', 'loop setup instructions', 'controller instruction', 'timeout=10m'):
                    self.assertNotIn(excluded, text)
                self.assertEqual(rows[-1]['payload']['last_agent_message'], answer)
                self.sampled.append(text)
                return {'sourceTurnId':turn['turn'],'responseId':'first-prediction','suggestion':'give the puppy a friend',
                    'userFingerprint':turn['userFingerprint'],'conversationDigest':turn['conversationDigest']}
        self.assertTrue(self.tick(predictor=next_prediction))
        self.assertEqual(len(self.sampled), 1)
        self.assertEqual(self.sent, ['give the puppy a friend'])
        self.assertEqual(json.loads(self.path.read_text())['completedIterations'], 0)
        self.records += [event('task_started','delivery'),message('user','give the puppy a friend')]
        self.rollout.write_text(''.join(json.dumps(r)+'\n' for r in self.records))
        con = sqlite3.connect(self.history)
        con.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?)',('target','delivery','inProgress',3,None))
        con.execute('INSERT INTO thread_items VALUES (?,?,?,?,?)',('target','delivery','userMessage',
            json.dumps({'content':[{'type':'text','text':'give the puppy a friend'}]}),3))
        con.commit(); con.close()
        state = loop.operate(json.loads(self.path.read_text()), 'resume-message', SimpleNamespace(), source=self.source)
        self.assertEqual(state['phase'], 'complete')
        self.assertEqual(state['completedIterations'], 1)
        predict.write_json(self.path, state)
        self.assertFalse(self.tick(predictor=unexpected))

    def test_human_input_before_sampling_stops_without_network_call(self):
        self.records.append(message('user','stop'))
        self.save_records('inProgress')
        self.assertFalse(self.tick())
        self.assertEqual(self.sampled,[])
        self.assertEqual(self.sent,[])

    def test_delivery_turn_can_precede_its_indexed_message(self):
        self.finish(); self.tick()
        con = sqlite3.connect(self.history)
        con.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?)', ('target','delivery','inProgress',3,None))
        con.commit(); con.close()
        self.assertTrue(self.tick())
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'waiting')
        con = sqlite3.connect(self.history)
        con.execute('INSERT INTO thread_items VALUES (?,?,?,?,?)',
            ('target','delivery','userMessage',json.dumps({'content':[{'type':'text','text':'make it sleepy'}]}),3))
        con.commit(); con.close()
        self.assertTrue(self.tick())
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'waiting')
        self.assertEqual(len(self.sampled),1)
        self.assertEqual(self.sent,['make it sleepy'])

    def test_rollout_can_precede_delivery_history_without_false_human_stop(self):
        self.finish(); self.tick()
        self.records += [event('task_started','delivery'), message('user','make it sleepy')]
        self.rollout.write_text(''.join(json.dumps(r)+'\n' for r in self.records))
        self.assertTrue(self.tick())
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'waiting')
        self.assertEqual(len(self.sampled),1)
        self.assertEqual(self.sent,['make it sleepy'])
        state=json.loads(self.path.read_text());state['deadline']=0;predict.write_json(self.path,state)
        self.assertFalse(self.tick())
        self.assertEqual(json.loads(self.path.read_text())['stopReason'], 'Time budget reached.')

    def test_waiting_still_stops_for_indexed_human_input(self):
        self.finish(); self.tick()
        con=sqlite3.connect(self.history)
        con.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?)', ('target','human','inProgress',3,None))
        con.execute('INSERT INTO thread_items VALUES (?,?,?,?,?)',
            ('target','human','userMessage',json.dumps({'content':[{'type':'text','text':'stop'}]}),3))
        con.commit();con.close()
        self.assertFalse(self.tick())
        self.assertIn('Unexpected human input',json.loads(self.path.read_text())['stopReason'])
        self.assertEqual(len(self.sampled),1)
        self.assertEqual(self.sent,['make it sleepy'])

    def test_human_input_during_sampling_prevents_queue(self):
        self.finish()
        def racing(source,**kwargs):
            proposal = self.predictor(source,**kwargs)
            self.records.append(message('user','stop'))
            self.save_records('completed')
            return proposal
        self.assertFalse(self.tick(predictor=racing))
        self.assertEqual(self.sent,[])

    def test_explicit_stop_can_take_lock_while_sampling(self):
        self.finish()
        def stopped(source,**kwargs):
            proposal = self.predictor(source,**kwargs)
            with loop.locked(self.path):
                state=json.loads(self.path.read_text())
                loop.halt(state,'User stopped')
                predict.write_json(self.path,state)
            return proposal
        self.assertFalse(self.tick(predictor=stopped))
        self.assertEqual(self.sent,[])

    def test_ambiguous_send_is_durable_and_never_replayed(self):
        self.finish()
        def ambiguous(state,text,timeout):
            self.sent.append(text)
            raise RuntimeError('queue timed out after acceptance')
        self.assertFalse(self.tick(sender=ambiguous))
        saved=json.loads(self.path.read_text())
        self.assertEqual(saved['phase'],'dispatching')
        with self.assertRaisesRegex(RuntimeError,'no automatic replay'): self.tick()
        self.assertEqual(self.sent,['make it sleepy'])

    def test_null_stops_without_queueing(self):
        self.finish()
        def empty(source,**kwargs):
            p=self.predictor(source,**kwargs); p['suggestion']=None; return p
        self.assertFalse(self.tick(predictor=empty))
        self.assertEqual(self.sent,[])

    def test_no_dispatch_if_deadline_expires_during_sampling(self):
        self.finish()
        def expired(source,**kwargs):
            proposal=self.predictor(source,**kwargs)
            s=json.loads(self.path.read_text());s['deadline']=0;predict.write_json(self.path,s)
            return proposal
        self.assertFalse(self.tick(predictor=expired))
        self.assertEqual(self.sent,[])

    def test_next_turn_preserves_exact_response_and_ordinary_tools(self):
        self.finish();self.tick()
        original_end=len(self.records)
        self.records += [event('task_started','delivery'),message('user','make it sleepy'),
            {'type':'response_item','payload':{'type':'function_call','call_id':'resume','arguments':'resume-message'}}]
        self.rollout.write_text(''.join(json.dumps(r)+'\n' for r in self.records))
        con=sqlite3.connect(self.history)
        con.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?)',('target','delivery','inProgress',3,None))
        con.execute('INSERT INTO thread_items VALUES (?,?,?,?,?)',('target','delivery','userMessage',json.dumps({'content':[{'type':'text','text':'make it sleepy'}]}),3))
        con.commit();con.close()
        state=json.loads(self.path.read_text())
        loop.operate(state,'resume-message',SimpleNamespace(),source=self.source)
        predict.write_json(self.path,state)
        self.assertTrue(self.tick())
        self.assertEqual(len(self.sampled),1)  # No sampling until the answer is final.
        exact='`/\\_/\\ (=-.-=) zzz`'
        self.records += [
            {'type':'response_item','payload':{'type':'function_call_output','call_id':'resume','output':'controller metadata'}},
            {'type':'response_item','payload':{'type':'function_call','call_id':'draw','arguments':'ordinary drawing tool'}},
            {'type':'response_item','payload':{'type':'function_call_output','call_id':'draw','output':'drawing result'}},
            message('assistant',exact,'final_answer'),event('task_complete','delivery')]
        self.rollout.write_text(''.join(json.dumps(r)+'\n' for r in self.records))
        con=sqlite3.connect(self.history)
        con.execute('UPDATE thread_turns SET status=?,rollout_end_byte_offset=? WHERE turn_id=?',('completed',self.rollout.stat().st_size,'delivery'))
        con.commit();con.close()
        def next_prediction(source,**kwargs):
            with predict.snapshot(source, scratch=self.scratch, idle=True,loop_context=kwargs['loop_context'],completed_context=True) as (_,copied,turn,_):
                text=copied.read_text(); rows=[json.loads(line) for line in text.splitlines()]
                self.assertNotIn('controller metadata',text)
                self.assertIn('ordinary drawing tool',text)
                self.assertIn('drawing result',text)
                responses=[r['payload'] for r in rows if r.get('type')=='response_item' and r['payload'].get('role')=='assistant']
                self.assertEqual(responses[-1]['content'][0]['text'],exact)
                self.assertEqual(sum(p['content'][0]['text']==exact for p in responses),1)
                return {'sourceTurnId':'delivery','responseId':'sample2','suggestion':'give it a friend',
                    'userFingerprint':turn['userFingerprint'],'conversationDigest':turn['conversationDigest']}
        self.assertTrue(self.tick(predictor=next_prediction))
        self.assertEqual(self.sent,['make it sleepy','give it a friend'])

    def test_limit_exits_worker_without_another_prediction(self):
        s=json.loads(self.path.read_text());s['completedIterations']=2;predict.write_json(self.path,s)
        self.assertFalse(self.tick())
        self.assertEqual(self.sampled,[])

    def test_launch_detaches_stdio_and_uses_private_log(self):
        with patch.object(worker.subprocess,'Popen') as popen:
            popen.return_value.pid=123
            self.assertEqual(worker.launch(self.path),123)
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            self.assertTrue(popen.call_args.kwargs['close_fds'])
            self.assertEqual(popen.call_args.kwargs['stdin'],worker.subprocess.DEVNULL)
        self.assertEqual(self.path.with_suffix('.worker.log').stat().st_mode & 0o777,0o600)

    def test_start_returns_while_reply_active_and_worker_observes_stop(self):
        path=self.home/'detached.json'
        script=Path(loop.__file__).resolve()
        before=time.monotonic()
        result=subprocess.run([sys.executable,str(script),'start','--state',str(path),
            '--thread','target','--codex-home',str(self.home),
            '--sqlite-home',str(self.home),'--max-minutes','1'],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertLess(time.monotonic()-before,5)
        state=json.loads(path.read_text())
        self.assertNotIn('scope',state)
        pid=state['workerPid']
        try:
            os.kill(pid,0)
            time.sleep(0.7)
            self.assertEqual(json.loads(path.read_text())['phase'],'ready')
            stopped=subprocess.run([sys.executable,str(script),'stop','--state',str(path),'--reason','test complete'],
                capture_output=True,text=True,timeout=5)
            self.assertEqual(stopped.returncode,0,stopped.stderr)
            # Acquire the singleton worker lock to verify that it exited.
            deadline=time.monotonic()+4
            while True:
                try:
                    with loop.locked(str(path)+'.worker'): break
                except RuntimeError:
                    if time.monotonic()>deadline: self.fail('Worker did not exit after stop')
                    time.sleep(0.1)
        finally:
            state=json.loads(path.read_text());loop.halt(state,'test cleanup');predict.write_json(path,state)

    def test_dead_worker_is_restarted_once_work_remains(self):
        self.state['workerPid'] = 111
        self.state['workerError'] = 'codex queue timed out'
        launched = []
        def launcher(path):
            launched.append(path); return 222
        self.assertFalse(worker.alive(self.path))
        self.assertTrue(worker.ensure(self.path, self.state, launcher))
        self.assertEqual(self.state['workerPid'], 222)
        self.assertNotIn('workerError', self.state)
        self.assertEqual(self.state['workerRestarts'][0]['previousError'], 'codex queue timed out')
        self.assertEqual(self.state['workerRestarts'][0]['previousPid'], 111)
        self.assertEqual(len(launched), 1)

    def test_live_or_finished_runs_are_not_restarted(self):
        def launcher(path): self.fail('Must not launch a second worker')
        with loop.locked(str(self.path) + '.worker'):
            self.assertTrue(worker.alive(self.path))
            self.assertFalse(worker.ensure(self.path, self.state, launcher))
        for phase in ('complete', 'stopped', 'waiting', 'dispatching'):
            self.state['phase'] = phase
            self.assertFalse(worker.ensure(self.path, self.state, launcher))
    def test_worker_shares_configuration_cache_between_predictions(self):
        caches = []
        def fake_tick(path, predictor):
            predictor(self.source, idle=True)
            return len(caches) < 2
        def fake_predict(source, **kwargs):
            caches.append(kwargs['config_cache'])
        with patch.object(worker, 'tick', fake_tick), patch.object(worker, 'predict', fake_predict), \
             patch.object(worker.time, 'sleep'):
            worker.run(self.path)
        self.assertEqual(len(caches), 2)
        self.assertIs(caches[0], caches[1])
