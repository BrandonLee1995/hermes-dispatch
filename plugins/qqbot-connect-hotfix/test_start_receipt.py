"""Gateway lifecycle -> real background route -> external QQ send boundary."""
import asyncio
from gateway.config import PlatformConfig
from gateway.hooks import HookRegistry
from gateway.platforms.base import MessageEvent, MessageType, SessionSource, build_session_key
from test_streaming import BasePlatformAdapter, GatewayDummyAdapter, Platform, mod


async def case(*, failure=False, chat_type='dm', event_type='agent:start'):
    adapter = GatewayDummyAdapter()
    BasePlatformAdapter.__init__(adapter, PlatformConfig(typing_indicator=False), Platform.QQBOT)
    chat = 'group:test' if chat_type == 'group' else 'receipt-user'
    source = SessionSource(platform=Platform.QQBOT, chat_id=chat, chat_type=chat_type, user_id='receipt-user')
    event = MessageEvent(text='daily task', message_type=MessageType.TEXT, source=source, message_id='receipt-request')
    hooks = HookRegistry()
    entered, finish = asyncio.Event(), asyncio.Event()
    adapter.fail_normal_attempts = int(failure)

    async def backend(event):
        context = {'platform': 'qqbot', 'chat_id': chat, 'chat_type': chat_type}
        await hooks.emit(event_type, context)
        await hooks.emit(event_type, context)
        entered.set()
        await finish.wait()  # Model has not produced any text.
        return None

    adapter.set_message_handler(backend)
    task = asyncio.create_task(adapter._process_message_background(event, build_session_key(source)))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        wanted = chat_type == 'dm' and event_type == 'agent:start'
        assert len(adapter.normal_send_attempts) == int(wanted), adapter.normal_send_attempts
        if wanted and not failure:
            sent = adapter.normal_sends[0]
            assert sent[:3] == (chat, '已收到，正在处理。', 'receipt-request'), sent
            assert sent[3]['_interim_send'] is True and not sent[3].get('notify'), sent
        if failure:
            assert not adapter.normal_sends
        assert not task.done(), 'Receipt must not finish or replace the model task'
    finally:
        finish.set()
        await task


async def main():
    mod._patch_qq_c2c_streaming(GatewayDummyAdapter)
    await case()
    await case(failure=True)
    await case(chat_type='group')
    await case(event_type='agent:end')
    print('Initial C2C receipt precedes model output, occurs once, and preserves task/failure isolation: PASS')


if __name__ == '__main__':
    asyncio.run(main())
