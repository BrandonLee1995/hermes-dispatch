"""Alternate cached Agents over one thread using real upstream session cleanup."""
import os
import gc
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from test_hotfix import mod
from agent.transports.codex_app_server_session import CodexAppServerSession


def main():
    writers = {}
    clients = []

    class Client:
        def __init__(self, **kwargs):
            clients.append(self)
        def initialize(self, **kwargs):
            pass
        def request(self, method, params, **kwargs):
            if method in ('thread/start', 'thread/resume'):
                thread_id = params.get('threadId', 'test-writer-thread')
                assert thread_id not in writers, 'thread already has an active writer'
                writers[thread_id] = self
                return {'thread': {'id': thread_id}}
            return {}
        def close(self):
            for tid, owner in list(writers.items()):
                if owner is self:
                    del writers[tid]

    class Session(CodexAppServerSession):
        __init__ = mod.session_project.wrap_session_init(CodexAppServerSession.__init__)
        ensure_started = mod.session_project.wrap_ensure_started(CodexAppServerSession.ensure_started)

    def turn(agent):
        if agent._codex_session is None:
            agent._codex_session = Session(client_factory=Client)
        return {'codex_thread_id': agent._codex_session.ensure_started()}

    run = mod.session_project.wrap_codex_runtime_turn(turn)
    agents = [SimpleNamespace(_gateway_session_key='agent:main:a2a:dm:writer-test',
                              session_id='writer-test', _codex_session=None) for _ in range(2)]
    with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {
        'HERMES_HOME': home, 'HERMES_CODEX_APP_REGISTER_PROJECTS': 'false',
        'HERMES_CODEX_SESSION_PROJECTS_ROOT': ''}):
        try:
            for i in range(6):
                result = run(agents[i % 2])
                assert result['codex_thread_id'] == 'test-writer-thread'
                assert len(writers) == 1
            agents[1]._codex_session.close()
            assert not writers, 'final close leaked a writer'
            # A changed cache signature can replace an Agent without soft cleanup.
            # Model the callback cycle and collection seen before live turn five.
            run(agents[0])
            old = agents[0]._codex_session
            old._cycle = old
            agents[0]._codex_session = None
            del old
            gc.collect()
            run(agents[0])
            assert len(writers) == 1
            agents[0]._codex_session.close()
            assert not writers
        finally:
            for client in clients:
                client.close()
    print('Alternating cached Agents: six turns, one writer, final cleanup PASS')

if __name__ == '__main__':
    main()
