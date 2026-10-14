import asyncio
import copy
import hashlib
import json
import time
import uuid
from pathlib import Path

from . import prompts
from .models import Article, Cue, Emotion, Record, RecordDraft, Reply, Settings, Variants
from .node_format import parse_nodes, render_nodes


def stamp():
    return time.time()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Service:
    def __init__(self, store, model):
        self.store, self.model = store, model
        self.states, self.jobs = {}, {}
        self.closed = False

    def state(self, sid):
        if sid not in self.states:
            s = self.store.load(sid)
            # In-flight tasks cannot survive a process restart; preserve raw inputs.
            if s['status'] in ('preparing', 'responding', 'closing', 'generating'):
                previous_status = s['status']
                s['last_operation'] = {'preparing':'start', 'responding':'reply',
                                       'closing':'finish', 'generating':'generate'}[previous_status]
                s['status'] = 'paused'
                s['error'] = '上次处理被中断，材料已保存。点击恢复继续。'
            if s.get('recording') or s.get('writing'):
                s['recording'] = s['writing'] = False
                s['record_error'] = '后台处理被中断，可恢复。'
            self.states[sid] = s
            for call in s.get('model_calls', []):
                if call['status'] == 'running':
                    call['status'] = 'interrupted'
        return self.states[sid]

    def save(self, s):
        s['updated_at'] = stamp()
        self.store.save(s)

    def create(self, title, settings):
        if settings.photo_hard_minutes < settings.photo_soft_minutes:
            raise ValueError('照片收束上限不能小于软检查时间。')
        s = dict(id=str(uuid.uuid4()), title=title.strip()[:80] or '一次旅行的片刻',
                 created_at=stamp(), updated_at=stamp(), status='new', error=None,
                 settings=settings.model_dump(), photos=[], messages=[], nodes=[], questions=[],
                 fragments=[], boundaries=[], focus='', writing_brief='', processed_until=0,
                 seq=0, current_photo=None, photo_started_at=None, current_emotion=None,
                 recording=False, writing=False, record_error=None, write_error=None,
                 pending_reply_seq=None, seen_requests=[], metrics=[], article=None,
                 article_stale=False, prompt_version=prompts.version(), epoch=0,
                 recovery_count=0, last_operation=None, final_node_ids=[], user_edits={}, model_calls=[])
        self.states[s['id']] = s
        self.save(s)
        return s

    def settings(self, s):
        return Settings.model_validate(s['settings'])

    async def invoke(self, s, prompt, data, schema, settings, image=None):
        epoch = s['epoch']
        call = dict(role=schema.__name__, prompt_version=prompts.version(), at=stamp(),
                    input_seq=s['seq'], model=getattr(self.model, 'vision_model' if image else 'text_model', 'test'),
                    image_supplied=bool(image), settings=settings.model_dump(), status='running')
        s.setdefault('model_calls', []).append(call)
        self.save(s)
        start = time.perf_counter()
        try:
            result = await self.model.call(prompt, data, schema, settings, image)
            call['status'] = 'completed' if epoch == s['epoch'] else 'discarded'
            return result
        except asyncio.CancelledError:
            call['status'] = 'cancelled'
            raise
        except Exception:
            call['status'] = 'failed'
            raise
        finally:
            call['duration_ms'] = round((time.perf_counter()-start)*1000, 1)
            self.save(s)

    def emit(self, s, role, text, kind='conversation', **extra):
        s['seq'] += 1
        m = dict(seq=s['seq'], role=role, text=text, type=kind, at=stamp(), **extra)
        s['messages'].append(m)
        return m

    def spawn(self, sid, kind, awaitable):
        old = self.jobs.get((sid, kind))
        if old and not old.done() and not old.cancelling():
            awaitable.close()
            return old
        task = asyncio.create_task(awaitable)
        self.jobs[(sid, kind)] = task
        if kind == 'writer':
            task.add_done_callback(lambda done: self.kick_writer(sid))
        return task

    def kick_writer(self, sid):
        if self.closed:
            return
        s = self.state(sid)
        if (s['status'] in ('active', 'review') and not s['recording'] and
                not s.get('write_error') and any(f['status'] == 'queued' for f in s['fragments'])):
            self.spawn(sid, 'writer', self.write_fragments(sid))

    def has_job(self, sid, kind):
        task = self.jobs.get((sid, kind))
        return task and not task.done()

    def mark_request(self, s, rid):
        if rid in s['seen_requests']:
            return False
        s['seen_requests'].append(rid)
        return True

    def public(self, s):
        p = copy.deepcopy(s)
        # Retain processed data for local QA/export, but never expose paths or secrets.
        for photo in p['photos']:
            photo.pop('path', None)
            photo['url'] = f"/api/sessions/{s['id']}/photos/{photo['id']}"
        p.pop('seen_requests', None)
        p['waiting_fragments'] = [f['id'] for f in p['fragments']
                                  if f.get('status') == 'ready' and not f.get('selected_style')]
        return p

    async def prepare(self, sid):
        s = self.state(sid)
        epoch = s['epoch']
        try:
            for photo in s['photos']:
                if epoch != s['epoch'] or s['status'] == 'stopped':
                    return
                if photo.get('cue'):
                    continue
                cue = await self.invoke(s, prompts.cue_prompt(), {'photo_id': photo['id']},
                                            Cue, self.settings(s), Path(photo['path']))
                if epoch != s['epoch']:
                    return
                photo['cue'] = cue.model_dump()
                self.save(s)
            s['current_photo'] = s['photos'][0]['id']
            s['photo_started_at'] = stamp()
            await self.respond(sid, None, epoch)
        except Exception as e:
            if epoch == s['epoch']:
                self.fail(s, e, 'start')

    def fail(self, s, e, operation):
        from .llm import ModelError
        s['error'] = str(e) if isinstance(e, ModelError) else '本次处理未完成，已有材料已保存，请重试。'
        s['status'] = 'paused'
        s['last_operation'] = operation
        self.save(s)

    def context(self, s):
        turns = s['messages'][-self.settings(s).history_turns * 2:]
        photo = next((p for p in s['photos'] if p['id'] == s['current_photo']), None)
        elapsed = max(0, stamp() - (s.get('photo_started_at') or stamp())) / 60
        return dict(history=[{k: m.get(k) for k in ('seq', 'role', 'text', 'type')} for m in turns],
                    current_photo=photo['id'] if photo else None,
                    photos=[dict(id=p['id'], cue=p.get('cue'), visited=p.get('visited', False),
                                 skipped=p.get('skipped', False)) for p in s['photos']],
                    known_nodes=s['nodes'], questions=s['questions'], boundaries=s['boundaries'],
                    focus=s['focus'], emotion=s['current_emotion'],
                    elapsed_minutes=round(elapsed, 2),
                    soft_check=elapsed >= self.settings(s).photo_soft_minutes,
                    no_new_questions=elapsed >= self.settings(s).photo_hard_minutes)

    async def respond(self, sid, seq, epoch=None):
        s = self.state(sid)
        epoch = s['epoch'] if epoch is None else epoch
        try:
            if seq:
                m = next(m for m in s['messages'] if m['seq'] == seq)
                emotion = await self.invoke(s, prompts.emotion_prompt(),
                                                {'current_text': m['text'],
                                                 'context': self.context(s)['history'][-4:]},
                                                Emotion, self.settings(s))
                if epoch != s['epoch']:
                    return
                s['current_emotion'] = emotion.model_dump()
            r = await self.invoke(s, prompts.interview_prompt(), self.context(s), Reply, self.settings(s))
            if epoch != s['epoch'] or s['status'] == 'stopped':
                return
            if r.photo_id and r.photo_id not in {p['id'] for p in s['photos']}:
                raise ValueError('Unknown photo')
            if r.photo_id and r.photo_id != s['current_photo']:
                for p in s['photos']:
                    if p['id'] == s['current_photo']:
                        p['visited'] = True
                s['current_photo'] = r.photo_id
                s['photo_started_at'] = stamp()
            s['focus'] = r.focus or s['focus']
            if r.boundary and r.boundary not in s['boundaries']:
                s['boundaries'].append(r.boundary)
            self.emit(s, 'assistant', r.reply, action=r.action, reply_for=seq,
                      photo_id=s['current_photo'], question=r.question)
            if r.question:
                qid = 'asked-' + str(s['seq'])
                s['questions'].append(dict(id=qid, text=r.question, status='asked', basis_seqs=[seq] if seq else []))
            s['pending_reply_seq'] = None
            s['error'] = None
            s['last_operation'] = None
            s['status'] = 'active'
            self.save(s)
            self.spawn(sid, 'writer', self.write_fragments(sid))
            if r.action == 'close':
                s['status'] = 'closing'
                self.save(s)
                self.spawn(sid, 'finish', self.finish_review(sid))
        except Exception as e:
            if epoch == s['epoch']:
                for metric in s['metrics']:
                    if metric['seq'] == seq and metric.get('completed_at') is None:
                        metric['status'] = 'reply_failed'
                self.fail(s, e, 'reply' if seq else 'start')

    def submit(self, sid, text, rid, photo_id=None, client_submitted_at=None):
        s = self.state(sid)
        if rid in s['seen_requests']:
            return s
        if s['status'] not in ('active', 'review'):
            raise ValueError('请等待当前回应完成，或先恢复会话。')
        if photo_id and photo_id not in {p['id'] for p in s['photos']}:
            raise ValueError('照片不存在。')
        self.mark_request(s, rid)
        if photo_id and photo_id != s['current_photo']:
            s['current_photo'] = photo_id
            s['photo_started_at'] = stamp()
        m = self.emit(s, 'user', text.strip(), photo_id=s['current_photo'])
        if client_submitted_at is not None:
            s['metrics'].append(dict(seq=m['seq'],submitted_at=client_submitted_at,
                                     first_response_at=None,completed_at=None,
                                     first_response_ms=None,complete_response_ms=None,status='pending'))
        s['pending_reply_seq'] = m['seq']
        s['article_stale'] = bool(s['article'])
        # Immediately hide potentially outdated drafts; worker will revalidate each.
        for f in s['fragments']:
            if f.get('status') == 'ready':
                f['status'] = 'pending_check'
        s['status'] = 'responding'
        s['error'] = None
        self.save(s)
        self.spawn(sid, 'record', self.record_loop(sid))
        self.spawn(sid, 'interview', self.respond(sid, m['seq']))
        return s

    def validate_record(self, s, record):
        users = {m['seq'] for m in s['messages'] if m['role'] == 'user'}
        photos = {p['id'] for p in s['photos']}
        ids = [n.id for n in record.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate nodes')
        for node in record.nodes:
            if not set(node.source_seqs).issubset(users) or not set(node.photo_ids).issubset(photos):
                raise ValueError('Node without user provenance')
        if len({f.id for f in record.fragments}) != len(record.fragments):
            raise ValueError('Duplicate fragments')
        for frag in record.fragments:
            if not set(frag.node_ids).issubset(ids):
                raise ValueError('Unknown selected node')
        if not set(record.final_node_ids).issubset(ids):
            raise ValueError('Unknown final node')

    async def record_loop(self, sid):
        s = self.state(sid)
        epoch = s['epoch']
        s['recording'] = True
        s['record_error'] = None
        self.save(s)
        try:
            while epoch == s['epoch']:
                latest = max((m['seq'] for m in s['messages'] if m['role'] == 'user'), default=0)
                if latest <= s['processed_until']:
                    break
                until = s['seq']
                data = dict(old_state={k: copy.deepcopy(s[k]) for k in
                                      ('questions', 'focus', 'boundaries', 'writing_brief')},
                            nodes_markdown=render_nodes(s['nodes']),
                            existing_fragments=[{k: f.get(k) for k in ('id', 'title', 'node_ids')} for f in s['fragments']],
                            new_turns=copy.deepcopy([m for m in s['messages'] if s['processed_until'] < m['seq'] <= until]),
                            photos=[dict(id=p['id'], cue=p.get('cue')) for p in s['photos']])
                draft = await self.invoke(s, prompts.record_prompt(), data, RecordDraft, self.settings(s))
                record = Record(nodes=parse_nodes(draft.nodes_markdown), **draft.model_dump(exclude={'nodes_markdown'}))
                if epoch != s['epoch']:
                    return
                self.validate_record(s, record)
                s['nodes'] = [n.model_dump() for n in record.nodes]
                old_questions = {q['id']: q for q in s['questions']}
                new_questions = {q.id: q.model_dump() for q in record.questions}
                # Preserve authoritative recent asks and terminal question states.
                for qid, q in old_questions.items():
                    if q['status'] in ('answered', 'declined'):
                        new_questions[qid] = q
                    elif qid not in new_questions:
                        new_questions[qid] = q
                s['questions'] = list(new_questions.values())
                s['boundaries'] = list(dict.fromkeys(s['boundaries'] + record.boundaries))
                s['focus'] = record.focus or s['focus']
                s['writing_brief'] = record.writing_brief
                s['final_node_ids'] = record.final_node_ids
                old = {f['id']: f for f in s['fragments']}
                updated = []
                for plan in record.fragments:
                    if not plan.sufficient:
                        continue
                    nodes = [n for n in s['nodes'] if n['id'] in plan.node_ids]
                    signature = fingerprint(nodes)
                    prior = old.get(plan.id)
                    if prior and prior.get('signature') == signature:
                        f = prior
                        if f['status'] == 'pending_check':
                            f['status'] = 'ready'
                    else:
                        f = dict(id=plan.id, title=plan.title, node_ids=plan.node_ids,
                                 signature=signature, revision=(prior['revision'] + 1) if prior else 1,
                                 status='queued', variants=[], selected_style=None,
                                 selected_text=None, edited=False, source_until=until)
                        edit = s.get('user_edits', {}).get(plan.id)
                        latest_source = max((i for n in nodes for i in n['source_seqs']), default=0)
                        if edit and latest_source <= edit['seq']:
                            f.update(selected_style=edit['style'], selected_text=edit['text'], edited=True)
                    # Remove terminally discarded drafts, never show an old signature.
                    updated.append(f)
                s['fragments'] = updated
                s['processed_until'] = until
                self.save(s)
                # Loop coalesces every not-yet-started input into one newest task.
            if epoch == s['epoch']:
                s['recording'] = False
                self.save(s)
                self.spawn(sid, 'writer', self.write_fragments(sid))
        except Exception as e:
            if epoch == s['epoch']:
                s['record_error'] = '材料整理未完成，请点击恢复；原话已保存。'
                s['recording'] = False
                self.save(s)
        finally:
            if epoch == s['epoch']:
                s['recording'] = False

    async def write_fragments(self, sid):
        s = self.state(sid)
        epoch = s['epoch']
        if s['status'] in ('preparing', 'responding', 'stopped') or s['recording']:
            return
        s['writing'] = True
        s['write_error'] = None
        self.save(s)
        try:
            while epoch == s['epoch']:
                frag = next((f for f in s['fragments'] if f['status'] == 'queued'), None)
                if not frag:
                    break
                fid, signature = frag['id'], frag['signature']
                nodes = copy.deepcopy([n for n in s['nodes'] if n['id'] in frag['node_ids']])
                seqs = {i for n in nodes for i in n['source_seqs']}
                data = dict(selected_nodes=nodes,
                            user_words=copy.deepcopy([m for m in s['messages'] if m['seq'] in seqs]),
                            fragment_title=frag['title'])
                result = await self.invoke(s, prompts.variants_prompt(), data, Variants, self.settings(s))
                if len({v.style for v in result.variants}) != 3:
                    raise ValueError('Three distinct styles required')
                current = next((f for f in s['fragments'] if f['id'] == fid), None)
                # New input or a correction invalidates in-flight drafts.
                newest = max((m['seq'] for m in s['messages'] if m['role'] == 'user'), default=0)
                if epoch != s['epoch']:
                    return
                if current and current['signature'] == signature and newest <= s['processed_until']:
                    current['variants'] = [v.model_dump() for v in result.variants]
                    current['status'] = 'ready'
                    self.save(s)
                else:
                    break
        except Exception as e:
            if epoch == s['epoch']:
                from .llm import ModelError
                s['write_error'] = '片段写作未完成：' + str(e) if isinstance(e, ModelError) else '片段写作未完成，已有材料已保存，可以重试。'
        finally:
            if epoch == s['epoch']:
                s['writing'] = False
                self.save(s)

    def choose(self, sid, fid, req):
        s = self.state(sid)
        if req.request_id in s['seen_requests']:
            return s
        if s['status'] in ('preparing', 'responding', 'closing', 'generating', 'stopped') or s['recording']:
            raise ValueError('请等待材料处理完成后保存片段。')
        f = next((f for f in s['fragments'] if f['id'] == fid), None)
        if not f or f['revision'] != req.revision or f['status'] != 'ready':
            raise ValueError('该片段已更新，请刷新后修改最新版本。')
        original = next(v['text'] for v in f['variants'] if v['style'] == req.style)
        self.mark_request(s, req.request_id)
        f['selected_style'], f['selected_text'] = req.style, req.text.strip()
        f['edited'] = req.text.strip() != original.strip()
        s['article_stale'] = bool(s['article'])
        if f['edited']:
            # Save every exact edit as authoritative user input, never silently paraphrase it.
            m = self.emit(s, 'user', req.text.strip(), kind='edit', fragment_id=fid,
                          previous_text=original, style=req.style)
            s.setdefault('user_edits', {})[fid] = dict(text=req.text.strip(), style=req.style, seq=m['seq'])
            f['status'] = 'pending_check'
            self.save(s)
            self.spawn(sid, 'record', self.record_loop(sid))
        else:
            self.save(s)
        return s

    async def finish_review(self, sid):
        s = self.state(sid)
        epoch = s['epoch']
        try:
            task = self.spawn(sid, 'record', self.record_loop(sid))
            await task
            if epoch != s['epoch']:
                return
            if s['record_error']:
                raise ValueError('Record unfinished')
            # A running writer may refer to an earlier snapshot; wait, then drain new work.
            task = self.jobs.get((sid, 'writer'))
            if task and not task.done():
                await task
            await self.write_fragments(sid)
            if epoch != s['epoch']:
                return
            s['status'] = 'review'
            s['error'] = None
            self.save(s)
        except Exception as e:
            if epoch == s['epoch']:
                self.fail(s, e, 'finish')

    async def generate(self, sid):
        s = self.state(sid)
        epoch = s['epoch']
        try:
            await self.spawn(sid, 'record', self.record_loop(sid))
            if epoch != s['epoch']:
                return
            if s['record_error']:
                raise ValueError('Record unfinished')
            selected = [f for f in s['fragments'] if f.get('selected_text')]
            node_ids = set(s.get('final_node_ids', [])) | {i for f in selected for i in f['node_ids']}
            nodes = copy.deepcopy([n for n in s['nodes'] if n['id'] in node_ids])
            source_seqs = {i for n in nodes for i in n['source_seqs']}
            s['generation_snapshot'] = dict(at=stamp(), processed_until=s['processed_until'],
                                            selected_nodes=nodes,
                                            selected_fragments=copy.deepcopy(selected),
                                            writing_brief=s['writing_brief'],
                                            user_words=copy.deepcopy([m for m in s['messages'] if m['seq'] in source_seqs]))
            self.save(s)
            if not nodes:
                s['status'] = 'review'
                s['error'] = '目前没有足够的个人讲述可写成游记，可以继续聊聊或保留这次记录。'
                self.save(s)
                return
            result = await self.invoke(s, prompts.final_prompt(), s['generation_snapshot'], Article, self.settings(s))
            if epoch != s['epoch']:
                return
            s['article'] = result.model_dump()
            s['article_stale'] = False
            s['status'] = 'completed'
            s['error'] = None
            self.save(s)
        except Exception as e:
            if epoch == s['epoch']:
                self.fail(s, e, 'generate')

    def stop(self, sid):
        s = self.state(sid)
        s['epoch'] += 1
        for (owner, kind), task in self.jobs.items():
            if owner == sid and not task.done():
                task.cancel()
        s['status'] = 'stopped'
        for metric in s['metrics']:
            if metric.get('completed_at') is None:
                metric['status'] = 'stopped'
        s['recording'] = s['writing'] = False
        s['error'] = None
        self.save(s)
        return s

    def resume(self, sid):
        s = self.state(sid)
        if s['status'] in ('preparing', 'responding', 'closing', 'generating'):
            return s
        if s['recovery_count'] >= self.settings(s).recovery_attempts and s['status'] == 'paused':
            raise ValueError('本次自动恢复次数已用完。可在参数设置中调整后重试。')
        s['recovery_count'] += 1
        s['error'] = None
        op = s.get('last_operation')
        if not all(p.get('cue') for p in s['photos']) or op == 'start':
            s['status'] = 'preparing'
            self.spawn(sid, 'interview', self.prepare(sid))
        elif s['pending_reply_seq']:
            s['status'] = 'responding'
            self.spawn(sid, 'record', self.record_loop(sid))
            self.spawn(sid, 'interview', self.respond(sid, s['pending_reply_seq']))
        elif op == 'generate':
            s['status'] = 'generating'
            self.spawn(sid, 'finish', self.generate(sid))
        elif op == 'finish':
            s['status'] = 'closing'
            self.spawn(sid, 'finish', self.finish_review(sid))
        else:
            s['status'] = s['status'] if s['status'] in ('review', 'completed') else 'active'
            self.spawn(sid, 'record', self.record_loop(sid))
            self.spawn(sid, 'writer', self.write_fragments(sid))
        self.save(s)
        return s

    async def shutdown(self):
        self.closed = True
        for task in self.jobs.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        await self.model.close()
