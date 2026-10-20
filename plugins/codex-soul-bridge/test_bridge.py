"""Hermes runtime scoping, instruction composition and concurrent profile isolation."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util
import os
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location('soul_bridge', Path(__file__).with_name('__init__.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name);(self.home/'SOUL.md').write_text('ROLE_ALPHA')
        self.agent = SimpleNamespace(_gateway_session_key='agent:main:a2a:dm:fixture', session_id='fixture', _session_db=SimpleNamespace(db_path=str(self.home/'state.db')))
        self.calls=[]
        self.client=SimpleNamespace()
        def request(client,method,params=None,**kwargs):
            self.calls.append((method,params))
            return {'config':{'developer_instructions':'KEEP_GLOBAL'}} if method=='config/read' else params
        self.request=bridge.wrap_request(request)
        self.scope=patch.object(bridge,'agent_home',lambda a: Path(a._session_db.db_path).parent if getattr(a,'_session_db',None) else None);self.scope.start();self.addCleanup(self.scope.stop)
        self.loader=patch.object(bridge,'load_soul',lambda home:(home/'SOUL.md').read_text() if (home/'SOUL.md').exists() else None);self.loader.start();self.addCleanup(self.loader.stop)

    def test_only_gateway_runtime_adds_soul_and_preserves_instruction_layers(self):
        params={'cwd':str(self.home),'baseInstructions':'KEEP_BASE'}
        run=bridge.wrap_runtime_turn(lambda agent:self.request(self.client,'thread/start',params))
        result=run(self.agent)
        self.assertEqual(result['baseInstructions'],'KEEP_BASE')
        self.assertIn('KEEP_GLOBAL',result['developerInstructions']);self.assertIn('ROLE_ALPHA',result['developerInstructions'])
        self.assertNotIn('developerInstructions',params)
        self.assertEqual(self.request(self.client,'thread/start',params),params)
        for method in ['thread/start']:
            explicit={'developerInstructions':'KEEP_EXPLICIT','config':{'developer_instructions':'OVERRIDE'}}
            result=bridge.wrap_runtime_turn(lambda a:self.request(self.client,method,explicit))(self.agent)
            self.assertIn('KEEP_EXPLICIT',result['developerInstructions']);self.assertNotIn('OVERRIDE',result['developerInstructions'])
            override={'config':{'developer_instructions':'KEEP_OVERRIDE'}}
            result=bridge.wrap_runtime_turn(lambda a:self.request(self.client,method,override))(self.agent)
            self.assertIn('KEEP_OVERRIDE',result['developerInstructions'])

    def test_env_cwd_mcp_and_non_gateway_do_not_activate(self):
        params={'cwd':str(self.home)}
        with patch.dict(os.environ,{'HERMES_HOME':str(self.home),'CODEX_HOME':str(self.home)}):
            self.assertEqual(self.request(self.client,'thread/start',params),params)
            result=bridge.wrap_runtime_turn(lambda a:self.request(self.client,'thread/start',params))(SimpleNamespace())
            self.assertEqual(result,params)
        self.assertFalse(any(m=='config/read' for m,p in self.calls))

    def test_missing_profile_or_soul_does_not_use_ambient_default(self):
        self.agent._session_db=None
        self.assertNotIn('developerInstructions',bridge.wrap_runtime_turn(lambda a:self.request(self.client,'thread/start',{}))(self.agent))
        self.agent._session_db=SimpleNamespace(db_path=str(self.home/'state.db'));(self.home/'SOUL.md').unlink()
        self.assertNotIn('developerInstructions',bridge.wrap_runtime_turn(lambda a:self.request(self.client,'thread/start',{}))(self.agent))

    def test_no_turn_payload_changes_and_no_scope_leak_after_exception(self):
        params={'threadId':'fixture','input':[{'type':'text','text':'hello'}]}
        self.assertEqual(bridge.wrap_runtime_turn(lambda a:self.request(self.client,'turn/start',params))(self.agent),params)
        def fail(a):raise RuntimeError('fixture')
        with self.assertRaises(RuntimeError):bridge.wrap_runtime_turn(fail)(self.agent)
        self.assertEqual(self.request(self.client,'thread/start',{}),{})

    def test_nested_unscoped_call_and_concurrent_profiles(self):
        inner=bridge.wrap_runtime_turn(lambda a:self.request(self.client,'thread/start',{}))
        outer=bridge.wrap_runtime_turn(lambda a:inner(SimpleNamespace()))
        self.assertEqual(outer(self.agent),{})
        other=self.home/'other';other.mkdir();(other/'SOUL.md').write_text('ROLE_BETA')
        beta=SimpleNamespace(_gateway_session_key='agent:main:qqbot:dm:fixture',session_id='other',_session_db=SimpleNamespace(db_path=str(other/'state.db')))
        barrier=threading.Barrier(2)
        def body(a):barrier.wait();return self.request(self.client,'thread/start',{})
        run=bridge.wrap_runtime_turn(body)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,[self.agent,beta]))
        self.assertIn('ROLE_ALPHA',results[0]['developerInstructions']);self.assertNotIn('ROLE_BETA',results[0]['developerInstructions'])
        self.assertIn('ROLE_BETA',results[1]['developerInstructions']);self.assertNotIn('ROLE_ALPHA',results[1]['developerInstructions'])

    def test_existing_thread_injects_once_and_refreshes_changed_soul(self):
        params={'threadId':'existing','input':[{'type':'text','text':'retain history'}]}
        run=bridge.wrap_runtime_turn(lambda a:self.request(self.client,'turn/start',params))
        self.assertEqual(run(self.agent),params);run(self.agent)
        injected=[p for m,p in self.calls if m=='thread/inject_items']
        self.assertEqual(len(injected),1)
        self.assertEqual(injected[0]['threadId'],'existing')
        self.assertEqual(injected[0]['items'][0]['role'],'developer')
        self.assertEqual(injected[0]['items'][0]['content'][0]['text'],'ROLE_ALPHA')
        (self.home/'SOUL.md').write_text('ROLE_UPDATED')
        run(self.agent);run(self.agent)
        injected=[p for m,p in self.calls if m=='thread/inject_items']
        self.assertEqual(len(injected),2)
        self.assertEqual(injected[-1]['items'][0]['content'][0]['text'],'ROLE_UPDATED')
        self.request(self.client,'turn/start',{'threadId':'ordinary','input':[]})
        self.assertEqual(sum(m=='thread/inject_items' for m,p in self.calls),2)

    def test_start_and_resume_do_not_duplicate_identity_on_next_turn(self):
        calls=[];client=SimpleNamespace()
        def original(c,method,params=None,**kwargs):
            calls.append((method,params))
            if method=='config/read':return {'config':{}}
            if method in ['thread/start','thread/resume']:return {'thread':{'id':'owned'}}
            return {}
        request=bridge.wrap_request(original)
        def flow(a):
            request(client,'thread/start',{})
            request(client,'turn/start',{'threadId':'owned'})
            request(client,'thread/resume',{'threadId':'owned'})
            request(client,'turn/start',{'threadId':'owned'})
        bridge.wrap_runtime_turn(flow)(self.agent)
        self.assertFalse(any(m=='thread/inject_items' for m,p in calls))

    def test_injection_failure_blocks_turn_and_does_not_mark_it_loaded(self):
        calls=[];client=SimpleNamespace();fail=True
        def original(c,method,params=None,**kwargs):
            calls.append(method)
            if method=='thread/inject_items' and fail:raise RuntimeError('injection failed')
            return {}
        request=bridge.wrap_request(original)
        run=bridge.wrap_runtime_turn(lambda a:request(client,'turn/start',{'threadId':'owned'}))
        with self.assertRaises(RuntimeError):run(self.agent)
        self.assertNotIn('turn/start',calls)
        fail=False;run(self.agent)
        self.assertEqual(calls,['thread/inject_items','thread/inject_items','turn/start'])

    def test_legacy_resume_does_not_falsely_mark_identity_as_loaded(self):
        calls=[];client=SimpleNamespace()
        def original(c,method,params=None,**kwargs):
            calls.append((method,params))
            return {'thread':{'id':'legacy'}} if method=='thread/resume' else {}
        request=bridge.wrap_request(original)
        def flow(a):
            request(client,'thread/resume',{'threadId':'legacy'})
            request(client,'turn/start',{'threadId':'legacy'})
            request(client,'turn/start',{'threadId':'legacy'})
        bridge.wrap_runtime_turn(flow)(self.agent)
        self.assertEqual([m for m,p in calls],['thread/resume','thread/inject_items','turn/start','turn/start'])
        self.assertEqual(calls[0][1],{'threadId':'legacy'})

if __name__=='__main__':unittest.main()
