"""Real Gateway delivery/A2A futures, with deterministic runtime results."""
import asyncio
import tempfile
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from plugins.platforms.a2a import adapter, tools
from result_status import install, wrap_run_agent, _TURN


async def main():
    if "--upstream" not in sys.argv:
        install(adapter, tools)
    config = PlatformConfig(enabled=True, extra={})
    config.typing_indicator = False
    a = adapter.A2AAdapter(config)
    result = {}

    async def run(self, source):
        return result
    run = wrap_run_agent(run)

    async def handler(event):
        return (await run(None, source=event.source)).get('final_response')
    a._message_handler = handler

    for i, fields in enumerate(({'failed': True}, {'error': 'startup failed'},
                                {'completed': False}, {'partial': True}, {'interrupted': True},
                                {'completed': True})):
        result = {**fields, 'final_response': 'Error: quoted example, not a failure signal'}
        context = f'test-{i}'
        task = f'task-{i}'
        event = MessageEvent(text='test', message_type=MessageType.TEXT,
                             source=a.build_source(chat_id=context, chat_type='dm', user_id='fixture'),
                             message_id=task)
        future = a._add_pending(task, context)
        next_future = a._add_pending(task + '-next', context)
        await a._process_message_background(event, context)
        assert future.done(), fields
        state, text = future.result()
        expected = adapter.protocol.STATE_COMPLETED if fields == {'completed': True} else adapter.protocol.STATE_FAILED
        assert state == expected, (fields, state)
        assert text == result['final_response'], (fields, text)
        assert not next_future.done(), 'failure delivery resolved a different queued task'
        assert _TURN.get() is None, 'outcome leaked outside its processing task'
        a._pop_pending(task)
        a._pop_pending(task + '-next')
    print('Runtime failures -> failed; quoted error -> completed; queued task isolation PASS')

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {'HERMES_HOME': home}):
        asyncio.run(main())
