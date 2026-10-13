"""Confirm accepted QQ private tasks before waiting for model output."""
import asyncio
import functools
import logging

from .steering import _ROUTE

logger = logging.getLogger(__name__)
_PATCHED = '_qq_start_receipt_patched'


def patch_qq_start_receipt(QQAdapter):
    from gateway.hooks import HookRegistry
    original = HookRegistry.emit
    if getattr(original, _PATCHED, False):
        return 'QQ start receipt already patched'

    @functools.wraps(original)
    async def emit(self, event_type, context=None):
        route = _ROUTE.get()
        if (event_type == 'agent:start' and isinstance(context, dict)
                and context.get('platform') == 'qqbot'
                and context.get('chat_type') in {'dm', 'private', 'c2c'}
                and route is not None and isinstance(route.adapter, QQAdapter)
                and context.get('chat_id') == route.chat_id and route.anchor
                and not route.receipt_attempted):
            # This lifecycle event occurs after auth; claim before IO so an
            # ambiguous timeout cannot produce another receipt for this task.
            route.receipt_attempted = True
            try:
                result = await asyncio.wait_for(route.adapter.send(
                    route.chat_id, '已收到，正在处理。', reply_to=route.anchor,
                    metadata={'_interim_send': True, 'reply_to_message_id': route.anchor},
                ), timeout=5)
                logger.info('QQ C2C task receipt delivered=%s', bool(result.success))
            except Exception:
                logger.warning('QQ C2C task receipt failed; continuing the accepted task')
        return await original(self, event_type, context)

    setattr(emit, _PATCHED, True)
    HookRegistry.emit = emit
    return 'QQ C2C accepted-task receipt patched'
