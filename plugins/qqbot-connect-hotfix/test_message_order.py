"""Client-visible C2C bubble chronology through the real Hermes consumer."""
import anyio
from test_streaming import GatewayDummyAdapter, GatewayStreamConsumer, StreamConsumerConfig, mod, streaming_mod


class TimelineAdapter(GatewayDummyAdapter):
    def __init__(self):
        super().__init__(); self.timeline=[]

    async def _api_request(self, method, path, body):
        result=await super()._api_request(method,path,body)
        self.timeline.append(('stream',result['id'],body['content_raw'],body['input_state']))
        return result

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        result=await super().send(chat_id,content,reply_to,metadata)
        if result.success:self.timeline.append(('message',result.message_id,content,None))
        return result


async def main():
    mod._patch_qq_c2c_streaming(TimelineAdapter)
    adapter=TimelineAdapter();chat='user-order';anchor='inbound-order'
    streaming_mod._mark_native_lane(adapter,chat)
    consumer=GatewayStreamConsumer(adapter,chat,StreamConsumerConfig(transport='auto',chat_type='dm',edit_interval=.01,buffer_threshold=1,cursor=''),initial_reply_to_id=anchor)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consumer.run)
        consumer.on_delta('FIRST');await anyio.sleep(.06)
        consumer.on_commentary('FIRST');await anyio.sleep(.06)
        consumer.on_delta('MIDDLE');await anyio.sleep(.06)
        consumer.on_segment_break();await anyio.sleep(.06)
        assert len({x[1] for x in adapter.timeline if x[0]=='stream'}) == 1, adapter.timeline
        await adapter.send(chat,'⏳ Working — 3 min',metadata={'_interim_send':True,'_qq_working_boundary':True,'reply_to_message_id':anchor})
        boundary=len(adapter.timeline)
        consumer.on_delta('AFTER');await anyio.sleep(.06)
        consumer.on_commentary('AFTER');await anyio.sleep(.06)
        consumer.on_delta('FINAL');await anyio.sleep(.06)
        consumer.finish('FINAL');await anyio.sleep(.06)
    first_id=next(x[1] for x in adapter.timeline if x[0]=='stream')
    assert all(x[1]!=first_id for x in adapter.timeline[boundary:]),adapter.timeline
    sealed=[(x[1],x[2]) for x in adapter.timeline if x[0]=='stream' and x[3]==10]
    assert [x[1] for x in sealed]==['FIRSTMIDDLE','AFTERFINAL'],adapter.timeline
    assert len({x[0] for x in sealed})==2,adapter.timeline
    assert consumer.final_response_sent
    print('Only Working separates bubbles; commentary and final reuse the current bubble: PASS')

    # QQ's network send is the boundary: a draft must not overtake Working.
    a=TimelineAdapter();chat='user-slow-status';anchor='slow-status'
    await a.send_draft(chat,1,'BEFORE',{'reply_to_message_id':anchor})
    a.normal_send_entered=anyio.Event();a.normal_send_release=anyio.Event()
    async def status():
        await a.send(chat,'⏳ Working',metadata={'_interim_send':True,'_qq_working_boundary':True,'reply_to_message_id':anchor})
    async def delta():await a.send_draft(chat,1,'BEFOREAFTER',{'reply_to_message_id':anchor})
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(status);await a.normal_send_entered.wait()
        tasks.start_soon(delta);await anyio.sleep(.03)
        assert not any(x[2]=='AFTER' for x in a.timeline)
        a.normal_send_release.set()
    assert [x[2] for x in a.timeline][-2:]==['⏳ Working','AFTER'],a.timeline
    await a.send(chat,'FINAL',metadata={'notify':True,'reply_to_message_id':anchor})
    print('Slow Working delivery cannot be overtaken by later tokens: PASS')

    # A failed close must never make later output mutate the older bubble.
    a=TimelineAdapter();chat='user-close-failure';anchor='close-failure'
    await a.send_draft(chat,2,'OLD',{'reply_to_message_id':anchor})
    old_id=a.timeline[-1][1];a.fail_seal_attempts=3
    await a.send(chat,'Working',metadata={'_interim_send':True,'_qq_working_boundary':True,'reply_to_message_id':anchor})
    boundary=len(a.timeline)
    await a.send_draft(chat,2,'OLDNEW',{'reply_to_message_id':anchor})
    await a.send(chat,'NEW',metadata={'notify':True,'reply_to_message_id':anchor})
    assert all(x[1]!=old_id for x in a.timeline[boundary:]),a.timeline
    visible={x[1]:x[2] for x in a.timeline if x[0]=='stream'}
    assert list(visible.values())==['OLD','NEW'],a.timeline
    print('Seal failure preserves text and never reopens an older bubble: PASS')

    # Hermes reports already-streamed assistant items via on_segment_break.
    a=TimelineAdapter();chat='user-streamed-boundary';anchor='streamed-boundary'
    streaming_mod._mark_native_lane(a,chat)
    c=GatewayStreamConsumer(a,chat,StreamConsumerConfig(transport='auto',chat_type='dm',edit_interval=.01,buffer_threshold=1,cursor=''),initial_reply_to_id=anchor)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(c.run);c.on_delta('FIRST');await anyio.sleep(.06)
        c.on_segment_break();await anyio.sleep(.06)
        c.on_delta('SECOND');await anyio.sleep(.06)
        c.finish('SECOND');await anyio.sleep(.06)
    assert [x[2] for x in a.timeline if x[0]=='stream' and x[3]==10]==['FIRSTSECOND'],a.timeline
    print('Already-streamed assistant boundary keeps the same bubble: PASS')

    # Real attachment replies can split a MEDIA directive across token frames.
    a=TimelineAdapter();chat='user-media';anchor='media'
    streaming_mod._mark_native_lane(a,chat)
    c=GatewayStreamConsumer(a,chat,StreamConsumerConfig(transport='auto',chat_type='dm',edit_interval=.01,buffer_threshold=1,cursor=''),initial_reply_to_id=anchor)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(c.run);c.on_delta('BEFORE');await anyio.sleep(.06)
        c.on_commentary('BEFORE');await anyio.sleep(.06)
        await a.send(chat,'Working',metadata={'_interim_send':True,'_qq_working_boundary':True,'reply_to_message_id':anchor})
        for chunk in ['FINAL\nM','EDIA:"/tmp/report']:
            c.on_delta(chunk);await anyio.sleep(.06)
        c.on_delta('.zip"\nfooter')
        c.finish('FINAL\n\nfooter');await anyio.sleep(.06)
    assert [x[2] for x in a.timeline if x[0]=='stream' and x[3]==10]==['BEFORE','FINAL\n\nfooter'],a.timeline
    assert all('MEDIA:' not in x[2] and '/tmp/report' not in x[2] for x in a.timeline),a.timeline
    print('Partial attachment directives never leak or duplicate the final answer: PASS')

    # Buffering a possible directive must preserve real prose and code examples.
    for name, chunks, final in [
        ('letter', ['Memo ends with ', 'M'], 'Memo ends with M'),
        ('code', ['Example:\n```text\nMEDIA:', '/tmp/sample.zip\n```'], 'Example:\n```text\nMEDIA:/tmp/sample.zip\n```'),
    ]:
        a=TimelineAdapter();chat='user-literal-'+name;anchor='literal-'+name
        streaming_mod._mark_native_lane(a,chat)
        c=GatewayStreamConsumer(a,chat,StreamConsumerConfig(transport='auto',chat_type='dm',edit_interval=.01,buffer_threshold=1,cursor=''),initial_reply_to_id=anchor)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(c.run)
            for chunk in chunks:
                c.on_delta(chunk);await anyio.sleep(.06)
            c.finish(final);await anyio.sleep(.06)
        assert [x[2] for x in a.timeline if x[0]=='stream' and x[3]==10]==[final],a.timeline
    print('Literal trailing letters and fenced MEDIA examples remain visible: PASS')


if __name__=='__main__':anyio.run(main)
