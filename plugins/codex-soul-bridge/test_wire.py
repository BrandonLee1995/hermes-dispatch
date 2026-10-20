"""Real Codex binaries + local Responses fixture; no online model or credentials."""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util,json,os,shutil,subprocess,sys,tempfile,threading,time,unittest

SOURCE=Path(os.environ['HERMES_SOURCE_DIR']);sys.path.insert(0,str(SOURCE))
from agent.transports.codex_app_server import CodexAppServerClient
spec=importlib.util.spec_from_file_location('soul_bridge_wire',Path(__file__).with_name('__init__.py'));bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
SENTINEL='SOUL_GATEWAY_ONLY_47C92'
class Responses(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        item={'type':'message','id':'msg_fixture','role':'assistant','status':'completed','phase':'final_answer','content':[{'type':'output_text','text':'READY','annotations':[]}]}
        response={'id':'resp_fixture','object':'response','status':'completed','output':[item],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
        events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}}, {'type':'response.output_item.added','output_index':0,'item':item},{'type':'response.output_item.done','output_index':0,'item':item},{'type':'response.completed','response':response}]
        body=''.join('data: '+json.dumps(x)+'\n\n' for x in events).encode();self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

class WireTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='soul-wire-');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.home=self.root/'hermes';self.home.mkdir();self.cwd=self.home/'codex-projects/test';self.cwd.mkdir(parents=True)
        (self.home/'SOUL.md').write_text(SENTINEL)
        plugin=self.home/'plugins/codex-soul-bridge';shutil.copytree(Path(__file__).parent,plugin,ignore=shutil.ignore_patterns('__pycache__'))
        (self.home/'config.yaml').write_text('plugins:\n  enabled: [codex-soul-bridge]\n')
        self.codex=self.root/'codex';self.codex.mkdir();self.server=ThreadingHTTPServer(('127.0.0.1',0),Responses);self.server.requests=[]
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start();self.addCleanup(self.stop)
        wrapper=Path(__file__).resolve().parents[2]/'mcp/hermes-tools-a2a/hermes_tools_a2a.py'
        text='model_provider="fixture"\nmodel="gpt-5.6-luna"\ndeveloper_instructions="KEEP_EXISTING_RULE"\n[model_providers.fixture]\nname="fixture"\nbase_url="http://127.0.0.1:'+str(self.server.server_port)+'"\nwire_api="responses"\nrequires_openai_auth=false\n[mcp_servers.hermes-tools]\ncommand='+json.dumps(sys.executable)+'\nargs=['+json.dumps(str(wrapper))+']\nrequired=true\n[mcp_servers.hermes-tools.env]\nHERMES_HOME='+json.dumps(str(self.home))+'\nPYTHONPATH='+json.dumps(str(SOURCE))+'\nHERMES_QUIET="1"\n'
        (self.codex/'config.toml').write_text(text);self.config_before=text
        self.env={'HOME':str(self.root),'USERPROFILE':str(self.root),'CODEX_HOME':str(self.codex),'HERMES_HOME':str(self.home),'PYTHONDONTWRITEBYTECODE':'1'}
        self.agent=SimpleNamespace(_gateway_session_key='agent:main:a2a:dm:fixture',session_id='fixture',_session_db=SimpleNamespace(db_path=str(self.home/'state.db')))
    def stop(self):self.server.shutdown();self.server.server_close();self.worker.join(timeout=5)
    def client(self,binary=None):
        c=CodexAppServerClient(codex_bin=binary or os.environ['CODEX_BINARY'],codex_home=str(self.codex),env=self.env)
        self.addCleanup(c._proc.stdout.close);self.addCleanup(c._proc.stderr.close);self.addCleanup(c.close)
        c.initialize(client_name='soul_regression',client_version='1');return c
    def turn(self,c,tid,prompt='Return READY only.'):
        c.request('turn/start',{'threadId':tid,'input':[{'type':'text','text':prompt}],'approvalPolicy':'never'},timeout=20)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            e=c.take_notification(timeout=.1)
            if e and e.get('method')=='turn/completed':
                self.assertIsNone(e.get('params',{}).get('turn',{}).get('error'));return
        self.fail('fixture turn timed out')
    def body(self):return json.dumps(self.server.requests[-1],ensure_ascii=False)
    def check_existing_thread_injection(self,binary):
        c=self.client(binary);tid=c.request('thread/start',{'cwd':str(self.cwd)},timeout=20)['thread']['id']
        self.turn(c,tid,'Remember OLD_CONTEXT_9D13 and return READY.')
        self.assertNotIn(SENTINEL,self.body())
        untouched=c.request('thread/start',{'cwd':str(self.cwd)},timeout=20)['thread']['id']
        self.turn(c,untouched,'Remember ORDINARY_CONTEXT_28BA and return READY.')
        count=len(self.server.requests);pid=c._proc.pid
        c.request('thread/inject_items',{'threadId':tid,'items':[{
            'type':'message','role':'developer','content':[{'type':'input_text','text':SENTINEL}],
        }]},timeout=20)
        self.assertEqual(len(self.server.requests),count,'Injection unexpectedly ran a model turn')
        self.assertEqual(c._proc.pid,pid)
        self.turn(c,tid)
        self.assertIn(SENTINEL,self.body());self.assertIn('OLD_CONTEXT_9D13',self.body());self.assertIn('KEEP_EXISTING_RULE',self.body())
        self.turn(c,untouched)
        self.assertNotIn(SENTINEL,self.body());self.assertNotIn('OLD_CONTEXT_9D13',self.body());self.assertIn('ORDINARY_CONTEXT_28BA',self.body())
        c.close();resumed=self.client(binary)
        same=resumed.request('thread/resume',{'threadId':tid,'cwd':str(self.cwd)},timeout=20)['thread']['id']
        self.assertEqual(same,tid);self.turn(resumed,tid)
        self.assertIn(SENTINEL,self.body());self.assertIn('OLD_CONTEXT_9D13',self.body())
        fresh=resumed.request('thread/start',{'cwd':str(self.cwd)},timeout=20)['thread']['id'];self.turn(resumed,fresh)
        self.assertNotIn(SENTINEL,self.body());self.assertNotIn('OLD_CONTEXT_9D13',self.body())
        self.assertEqual((self.codex/'config.toml').read_text(),self.config_before)
    def test_existing_loaded_thread_injection_preserves_history_and_scope(self):
        self.check_existing_thread_injection(os.environ['CODEX_BINARY'])
    @unittest.skipUnless(os.environ.get('CODEX_APP_BINARY'),'Set CODEX_APP_BINARY')
    def test_app_backend_existing_thread_injection(self):
        self.check_existing_thread_injection(os.environ['CODEX_APP_BINARY'])
    def test_scoped_start_resume_then_unscoped_same_process(self):
        with patch.dict(os.environ,self.env),patch.object(CodexAppServerClient,'request',bridge.wrap_request(CodexAppServerClient.request)):
            c=self.client()
            def start(a):return c.request('thread/start',{'cwd':str(self.cwd)},timeout=20)['thread']['id']
            tid=bridge.wrap_runtime_turn(start)(self.agent);self.turn(c,tid)
            self.assertIn(SENTINEL,self.body());self.assertIn('KEEP_EXISTING_RULE',self.body())
            c.close();c2=self.client()
            bridge.wrap_runtime_turn(lambda a:c2.request('thread/resume',{'threadId':tid,'cwd':str(self.cwd)},timeout=20))(self.agent)
            self.turn(c2,tid);self.assertIn(SENTINEL,self.body());self.assertIn('KEEP_EXISTING_RULE',self.body())
            ordinary=c2.request('thread/start',{'cwd':str(self.cwd)},timeout=20)['thread']['id'];self.turn(c2,ordinary)
            self.assertNotIn(SENTINEL,self.body());self.assertIn('KEEP_EXISTING_RULE',self.body())
        self.assertEqual((self.codex/'config.toml').read_text(),self.config_before)
    def test_resume_legacy_thread_without_soul_gets_identity_before_next_turn(self):
        with patch.dict(os.environ,self.env):
            legacy=self.client();tid=legacy.request('thread/start',{'cwd':str(self.cwd)},timeout=20)['thread']['id']
            self.turn(legacy,tid,'Remember LEGACY_CONTEXT_35BC and return READY.')
            self.assertNotIn(SENTINEL,self.body());legacy.close()
            with patch.object(CodexAppServerClient,'request',bridge.wrap_request(CodexAppServerClient.request)):
                current=self.client()
                def resume_and_turn(a):
                    resumed=current.request('thread/resume',{'threadId':tid,'cwd':str(self.cwd)},timeout=20)
                    self.assertEqual(resumed['thread']['id'],tid)
                    self.turn(current,tid)
                bridge.wrap_runtime_turn(resume_and_turn)(self.agent)
                self.assertIn(SENTINEL,self.body());self.assertIn('LEGACY_CONTEXT_35BC',self.body())
    def test_plain_cli_with_shared_home_and_mcp_plugin_does_not_inject(self):
        env=os.environ.copy();env.update(self.env)
        p=subprocess.run([os.environ['CODEX_BINARY'],'exec','--skip-git-repo-check','--json','-C',str(self.cwd),'-s','read-only','Return READY only.'],env=env,capture_output=True,text=True,timeout=45)
        self.assertEqual(p.returncode,0,p.stderr[-1000:]);self.assertTrue(self.server.requests)
        self.assertNotIn(SENTINEL,self.body());self.assertIn('KEEP_EXISTING_RULE',self.body())
        self.assertEqual((self.codex/'config.toml').read_text(),self.config_before)
    def test_actual_hermes_runtime_registration_is_scoped_and_idempotent(self):
        from agent import codex_runtime
        agent=SimpleNamespace(**vars(self.agent), session_cwd=str(self.cwd), _codex_session=None,
            _skill_nudge_interval=0, session_api_calls=0, context_compressor=None,
            _flush_messages_to_session_db=lambda messages: True, model='gpt-5.6-luna',
            provider='fixture', base_url='http://127.0.0.1', _session_db_created=True,
            session_estimated_cost_usd=0)
        for counter in ('prompt','completion','total','input','output','cache_read','cache_write','reasoning'):
            setattr(agent,'session_'+counter+'_tokens',0)
        agent._session_db.queue_token_counts=lambda *args,**kwargs: None
        with patch.dict(os.environ,{**self.env,'PATH':str(Path(os.environ['CODEX_BINARY']).parent)+os.pathsep+os.environ['PATH']}), \
             patch.object(codex_runtime,'run_codex_app_server_turn',codex_runtime.run_codex_app_server_turn), \
             patch.object(CodexAppServerClient,'request',CodexAppServerClient.request):
            try:
                before=codex_runtime.run_codex_app_server_turn(agent,user_message='Remember PRE_BRIDGE_CONTEXT_71F4; return READY.',
                    original_user_message='fixture',messages=[],effective_task_id='fixture')
                self.assertTrue(before['completed'],before);self.assertNotIn(SENTINEL,self.body())
                thread_id=before['codex_thread_id'];pid=agent._codex_session._client._proc.pid
                bridge.register(None)
                installed=(codex_runtime.run_codex_app_server_turn,CodexAppServerClient.request)
                bridge.register(None)
                self.assertEqual(installed,(codex_runtime.run_codex_app_server_turn,CodexAppServerClient.request))
                result=codex_runtime.run_codex_app_server_turn(agent,user_message='Return READY only.',
                    original_user_message='Return READY only.',messages=[],effective_task_id='fixture')
                self.assertTrue(result['completed'],result)
                self.assertIn(SENTINEL,self.body());self.assertIn('KEEP_EXISTING_RULE',self.body())
                self.assertIn('PRE_BRIDGE_CONTEXT_71F4',self.body())
                self.assertEqual(result['codex_thread_id'],thread_id);self.assertEqual(agent._codex_session._client._proc.pid,pid)
                for _ in range(2):
                    result=codex_runtime.run_codex_app_server_turn(agent,user_message='Return READY only.',
                        original_user_message='fixture',messages=[],effective_task_id='fixture')
                    self.assertTrue(result['completed'],result);self.assertEqual(self.body().count(SENTINEL),1)
                (self.home/'SOUL.md').write_text('SOUL_CHANGED_95EA')
                for _ in range(2):
                    result=codex_runtime.run_codex_app_server_turn(agent,user_message='Return READY only.',
                        original_user_message='fixture',messages=[],effective_task_id='fixture')
                    self.assertTrue(result['completed'],result);self.assertEqual(result['codex_thread_id'],thread_id)
                    self.assertEqual(self.body().count('SOUL_CHANGED_95EA'),1)
                ordinary=self.client();tid=ordinary.request('thread/start',{'cwd':str(self.cwd)},timeout=20)['thread']['id']
                self.turn(ordinary,tid);self.assertNotIn(SENTINEL,self.body());self.assertNotIn('SOUL_CHANGED_95EA',self.body())
            finally:
                if agent._codex_session is not None:
                    client=agent._codex_session._client;agent._codex_session.close()
                    client._proc.stdout.close();client._proc.stderr.close()
        self.assertEqual((self.codex/'config.toml').read_text(),self.config_before)
    @unittest.skipUnless(os.environ.get('CODEX_APP_BINARY'),'Set CODEX_APP_BINARY')
    def test_app_bundled_server_new_thread_has_no_identity(self):
        c=self.client(os.environ['CODEX_APP_BINARY']);tid=c.request('thread/start',{'cwd':str(self.cwd)},timeout=20)['thread']['id'];self.turn(c,tid)
        self.assertNotIn(SENTINEL,self.body());self.assertIn('KEEP_EXISTING_RULE',self.body())
        self.assertEqual((self.codex/'config.toml').read_text(),self.config_before)

if __name__=='__main__':unittest.main()
