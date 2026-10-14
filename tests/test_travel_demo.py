import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from travel_demo import prompts
from travel_demo.app import create_app
from travel_demo.llm import MiniMax, ModelError, parse_json
from travel_demo.models import Article, ChoiceRequest, Cue, Emotion, Record, RecordDraft, Reply, Settings, Variants
from travel_demo.node_format import parse_nodes, render_nodes
from travel_demo.service import Service
from travel_demo.storage import Store


class FakeModel:
    key = 'test-only'
    text_model = vision_model = 'test'

    def __init__(self):
        self.calls = []
        self.record_gate = None
        self.variant_gate = None
        self.fail_reply = False
        self.fail_variants = False

    async def close(self):
        pass

    async def call(self, prompt, data, schema, settings, image=None):
        self.calls.append((schema, data, image))
        if schema is Cue:
            return Cue(description='湖边的椅子', visible_details=['椅子'])
        if schema is Emotion:
            return Emotion(emotions=['neutral'])
        if schema is Reply:
            if self.fail_reply:
                raise ModelError('测试超时，材料已保存。')
            return Reply(reply='你在这里做了什么？', action='ask', question='你在这里做了什么？', photo_id=data['current_photo'])
        if schema is RecordDraft:
            if self.record_gate:
                await self.record_gate.wait()
            users = [m for m in data['new_turns'] if m['role'] == 'user']
            if not users:
                return RecordDraft(nodes_markdown=data['nodes_markdown'])
            m = users[-1]
            return RecordDraft(nodes_markdown=render_nodes([dict(id='m1', topic='湖边', description=m['text'], source_seqs=[m['seq']], photo_ids=[])]),
                          fragments=[dict(id='f1',title='湖边的一刻',node_ids=['m1'],sufficient=True,reason='用户讲述')],final_node_ids=['m1'])
        if schema is Variants:
            if self.fail_variants:
                raise ModelError('测试写作超时。')
            if self.variant_gate:
                await self.variant_gate.wait()
            text = data['selected_nodes'][0]['description']
            return Variants(variants=[dict(style=style,text=text) for style in ('original','natural','literary')])
        if schema is Article:
            return Article(title='湖边',body=data['selected_fragments'][0]['selected_text'] if data['selected_fragments'] else data['selected_nodes'][0]['description'])
        raise AssertionError(schema)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.model = FakeModel()
        self.svc = Service(Store(Path(self.tmp.name)),self.model)
        self.s = self.svc.create('test',Settings())
        self.s['photos']=[dict(id='p1',path=str(Path(self.tmp.name)/'original.png'),name='test',cue=None)]
        self.s['status']='preparing'
        await self.svc.prepare(self.s['id'])
        await self.drain()

    async def asyncTearDown(self):
        await self.svc.shutdown()
        self.tmp.cleanup()

    async def drain(self):
        for _ in range(30):
            await asyncio.sleep(0)
            running=[t for t in self.svc.jobs.values() if not t.done()]
            if not running:
                # Completion callbacks may schedule the replacement draft worker.
                await asyncio.sleep(.005)
                if not any(not t.done() for t in self.svc.jobs.values()):
                    return
            await asyncio.gather(*running)
        self.fail('Work failed to drain')

    def submit(self,text,rid='request-001'):
        return self.svc.submit(self.s['id'],text,rid)

    async def test_full_flow_exact_user_edit_and_single_initial_image(self):
        self.submit('我和朋友在湖边坐了一会儿，没有拍照。')
        await self.drain()
        f=self.s['fragments'][0]
        edited='我和姐姐坐在湖边，只坐了五分钟。'
        self.svc.choose(self.s['id'],'f1',ChoiceRequest(style='natural',text=edited,revision=f['revision'],request_id='edit-001'))
        await self.drain()
        f=self.s['fragments'][0]
        self.assertEqual(f['selected_text'],edited)
        self.assertTrue(f['edited'])
        self.assertEqual(self.s['nodes'][0]['description'],edited)
        self.s['status']='closing'
        await self.svc.finish_review(self.s['id'])
        self.assertEqual(self.s['status'],'review')
        self.s['status']='generating'
        await self.svc.generate(self.s['id'])
        self.assertEqual(self.s['article']['body'],edited)
        self.assertEqual(sum(c[2] is not None for c in self.model.calls),1)
        self.assertEqual(self.s['generation_snapshot']['selected_nodes'][0]['description'],edited)

    async def test_record_coalesces_pending_inputs_without_losing_raw_words(self):
        gate=asyncio.Event(); self.model.record_gate=gate
        self.submit('第一段')
        await asyncio.sleep(.01)
        self.assertEqual(self.s['status'],'active')
        self.submit('第二段','request-002'); await asyncio.sleep(.01)
        self.submit('第三段','request-003'); await asyncio.sleep(.01)
        gate.set(); await self.drain()
        calls=[c for c in self.model.calls if c[0] is RecordDraft]
        self.assertEqual(len(calls),2)
        self.assertEqual([m['text'] for m in calls[1][1]['new_turns'] if m['role']=='user'],['第二段','第三段'])
        self.assertEqual(len([m for m in self.s['messages'] if m['role']=='user']),3)

    async def test_new_correction_invalidates_running_draft(self):
        gate=asyncio.Event(); self.model.variant_gate=gate
        self.submit('我和朋友坐在湖边。'); await asyncio.sleep(.01)
        self.submit('不是朋友，是姐姐。','request-002'); await asyncio.sleep(.01)
        gate.set(); await self.drain()
        self.assertEqual(self.s['fragments'][0]['status'],'ready')
        self.assertTrue(all('姐姐' in v['text'] for v in self.s['fragments'][0]['variants']))

    async def test_stop_cancels_work_and_resume_deduplicates_input(self):
        gate=asyncio.Event(); self.model.record_gate=gate
        self.submit('坐在湖边。'); await asyncio.sleep(.01)
        self.svc.stop(self.s['id']); before=len(self.s['messages'])
        gate.set(); await asyncio.sleep(.01)
        self.assertEqual(self.s['status'],'stopped')
        self.assertEqual(len(self.s['messages']),before)
        self.svc.resume(self.s['id']); await self.drain()
        self.submit('坐在湖边。')
        self.assertEqual(len([m for m in self.s['messages'] if m['role']=='user']),1)

    async def test_recovery_preserves_pending_reply_and_raw_input(self):
        self.model.fail_reply=True
        self.submit('我很喜欢这一刻。'); await self.drain()
        self.assertEqual(self.s['status'],'paused')
        self.model.fail_reply=False
        self.svc.resume(self.s['id']); await self.drain()
        self.assertEqual(self.s['status'],'active')
        self.assertEqual(len([m for m in self.s['messages'] if m['role']=='user']),1)

    async def test_missing_client_response_time_remains_missing_on_failure(self):
        self.model.fail_reply=True
        self.svc.submit(self.s['id'],'坐在湖边。','timing-test',client_submitted_at=123456)
        await self.drain()
        self.assertEqual(self.s['metrics'][0]['submitted_at'],123456)
        self.assertIsNone(self.s['metrics'][0]['first_response_ms'])
        self.assertTrue(any(c['role']=='Reply' and c['status']=='failed' for c in self.s['model_calls']))

    async def test_retry_background_writer_preserves_completed_session(self):
        self.submit('在湖边坐了五分钟。'); await self.drain()
        self.s['status']='completed'
        self.s['fragments'][0]['status']='queued'
        self.model.fail_variants=True
        await self.svc.write_fragments(self.s['id'])
        self.assertIn('超时',self.s['write_error'])
        self.model.fail_variants=False
        self.svc.resume(self.s['id']); await self.drain()
        self.assertEqual(self.s['status'],'completed')
        self.assertEqual(self.s['fragments'][0]['status'],'ready')

    async def test_rejects_assistant_as_personal_fact_and_invalid_photo(self):
        with self.assertRaises(ValueError):
            self.svc.validate_record(self.s,Record(nodes=[dict(id='m',topic='x',description='x',source_seqs=[1])]))
        with self.assertRaises(ValueError):
            self.svc.submit(self.s['id'],'hello','bad-photo','unknown')

    async def test_model_semaphore_queue_has_total_deadline(self):
        model=MiniMax(); model.key='fake'; await model.slots.acquire(); await model.slots.acquire(); await model.slots.acquire()
        settings=Settings.model_construct(request_timeout=.03,retries=0)
        try:
            with self.assertRaises(ModelError):
                await asyncio.wait_for(model.call('test',{},Emotion,settings),timeout=.3)
        finally:
            await model.close()


class ApiTests(unittest.TestCase):
    def test_upload_validation_storage_export_and_origin(self):
        with tempfile.TemporaryDirectory() as root:
            with TestClient(create_app(Store(Path(root)),FakeModel())) as client:
                sid=client.post('/api/sessions',json={'title':'test'}).json()['id']
                im=Image.new('RGB',(20,20),'red'); buf=io.BytesIO(); im.save(buf,format='PNG'); data=buf.getvalue()
                r=client.post(f'/api/sessions/{sid}/photos',files=[('files',('../../escape.png',data,'image/png')),('files',('bad.jpg',b'invalid','image/jpeg'))])
                self.assertEqual(r.status_code,422)
                self.assertEqual(client.get(f'/api/sessions/{sid}').json()['photos'],[])
                r=client.post(f'/api/sessions/{sid}/photos',files={'files':('../../escape.png',data,'image/png')})
                photo=r.json()['photos'][0]
                self.assertNotIn('path',photo)
                self.assertEqual(client.get(photo['url']).content,data)
                self.assertEqual(client.post(f'/api/sessions/{sid}/stop',headers={'Origin':'https://example.com'}).status_code,403)
                exported=client.get(f'/api/sessions/{sid}/export').text
                self.assertNotIn('test-only',exported)
                self.assertFalse((Path(root)/'escape.png').exists())

    def test_all_source_prompts_build_and_safe_json_parser(self):
        for fn in (prompts.cue_prompt,prompts.emotion_prompt,prompts.interview_prompt,prompts.record_prompt,prompts.variants_prompt,prompts.final_prompt):
            self.assertTrue(fn())
        self.assertEqual(parse_json('<think>hidden</think>```json\n{"ok":true}\n```'),{'ok':True})
        with self.assertRaises(ModelError):
            parse_json('__import__("os").system("danger")')

    def test_markdown_nodes_roundtrip_and_invalid_provenance(self):
        original=dict(id='m1',date=None,topic='湖边',people=['姐姐','我'],description='坐下。\n只坐了五分钟。',source_seqs=[2,4],photo_ids=['p1'])
        self.assertEqual(parse_nodes(render_nodes([original]))[0].model_dump(),original)
        with self.assertRaises(ValueError):
            parse_nodes('### 记忆节点 m1\n主题：一段无来源的故事')


if __name__=='__main__':
    unittest.main()
