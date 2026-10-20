"""Preserve runtime failure across Gateway text delivery and native A2A tools."""
from contextvars import ContextVar
from functools import wraps
import inspect

_TURN = ContextVar('a2a_runtime_result', default=None)


def wrap_run_agent(original):
    signature = inspect.signature(original)

    @wraps(original)
    async def run(*args, **kwargs):
        result = await original(*args, **kwargs)
        turn = _TURN.get()
        if turn is not None and isinstance(result, dict):
            source = signature.bind(*args, **kwargs).arguments.get('source')
            if (getattr(getattr(source, 'platform', None), 'value', None) == 'a2a'
                    and source.chat_id == turn['context']):
                if (result.get('failed') or result.get('error') or result.get('partial')
                        or result.get('interrupted') or result.get('completed') is False):
                    turn['failed'] = True
                    turn['text'] = str(result.get('final_response') or result.get('error')
                                       or '[agent processing failed]')
        return result
    run._a2a_result_status = True
    return run


def install(adapter, tools):
    cls = adapter.A2AAdapter
    if getattr(cls, '_a2a_result_status', False):
        return
    original_connect = cls.connect
    original_process = cls._process_message_background
    original_send = cls.send
    original_complete = cls.on_processing_complete
    original_send_task = tools._send_task

    @wraps(original_connect)
    async def connect(self, *args, **kwargs):
        # Gateway classes are fully initialized here, outside plugin discovery.
        from gateway.run import GatewayRunner
        if not getattr(GatewayRunner._run_agent, '_a2a_result_status', False):
            GatewayRunner._run_agent = wrap_run_agent(GatewayRunner._run_agent)
        return await original_connect(self, *args, **kwargs)

    @wraps(original_process)
    async def process(self, event, session_key):
        token = _TURN.set({'context': event.source.chat_id, 'task': event.message_id,
                           'failed': False, 'text': ''})
        try:
            return await original_process(self, event, session_key)
        finally:
            _TURN.reset(token)

    @wraps(original_send)
    async def send(self, chat_id, content, reply_to=None, metadata=None):
        turn = _TURN.get()
        if (turn and turn['failed'] and turn['context'] == chat_id
                and (metadata or {}).get('notify')):
            self._resolve_task(turn['task'], adapter.protocol.STATE_FAILED, turn['text'])
            return adapter.SendResult(success=True)
        return await original_send(self, chat_id, content, reply_to, metadata)

    @wraps(original_complete)
    async def complete(self, event, outcome):
        turn = _TURN.get()
        if turn and turn['failed'] and turn['task'] == event.message_id:
            self._resolve_task(turn['task'], adapter.protocol.STATE_FAILED, turn['text'])
            return
        return await original_complete(self, event, outcome)

    @wraps(original_send_task)
    def send_task(*args, **kwargs):
        try:
            reply, context, state = original_send_task(*args, **kwargs)
        except ValueError as exc:
            # Native a2a_call returns ValueErrors verbatim, unlike HTTP errors.
            raise ValueError('Error: ' + str(exc).removeprefix('Error: ')) from exc
        if tools._short_state(state) in {'failed', 'rejected', 'canceled', 'cancelled'}:
            raise ValueError(f'Error: A2A task {tools._short_state(state)} '
                             f'(context {context}):\n{reply}')
        return reply, context, state

    cls.connect = connect
    cls._process_message_background = process
    cls.send = send
    cls.on_processing_complete = complete
    cls._a2a_result_status = True
    tools._send_task = send_task
