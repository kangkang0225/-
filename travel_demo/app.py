import io
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .llm import MiniMax
from .models import ChoiceRequest, MessageRequest, MetricRequest, Settings
from .service import Service
from .storage import Store

ROOT = Path(__file__).resolve().parents[1]


def create_app(store=None, model=None):
    svc = Service(store or Store(ROOT / 'data/travel_demo'), model or MiniMax())

    @asynccontextmanager
    async def lifespan(app):
        yield
        await svc.shutdown()

    app = FastAPI(title='旅行片刻', lifespan=lifespan)
    app.state.service = svc
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def same_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD') and origin:
            from urllib.parse import urlparse
            if urlparse(origin).netloc != request.headers.get('host'):
                return Response('不允许跨站请求', status_code=403)
        result = await call_next(request)
        result.headers['Cache-Control'] = 'no-store'
        result.headers['X-Content-Type-Options'] = 'nosniff'
        result.headers['Referrer-Policy'] = 'same-origin'
        return result

    def state(sid):
        try:
            return svc.state(sid)
        except (KeyError, ValueError):
            raise HTTPException(404, '会话不存在。')

    def busy(s):
        return s['status'] in ('preparing', 'responding', 'closing', 'generating')

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.get('/api/config')
    async def config():
        return dict(settings=Settings().model_dump(), key_ready=bool(getattr(svc.model, 'key', False)),
                    text_model=getattr(svc.model, 'text_model', 'test'),
                    vision_model=getattr(svc.model, 'vision_model', 'test'),
                    prompt_version=__import__('travel_demo.prompts', fromlist=['version']).version())

    @app.get('/api/sessions')
    async def sessions():
        return svc.store.list()

    @app.post('/api/sessions')
    async def create(request: Request):
        body = await request.json()
        s = svc.create(str(body.get('title', '')), Settings.model_validate(body.get('settings', {})))
        return svc.public(s)

    @app.get('/api/sessions/{sid}')
    async def read(sid: str):
        return svc.public(state(sid))

    @app.put('/api/sessions/{sid}/settings')
    async def settings(sid: str, settings: Settings):
        s = state(sid)
        if busy(s):
            raise HTTPException(409, '请等当前处理完成后修改参数。')
        if settings.photo_hard_minutes < settings.photo_soft_minutes:
            raise HTTPException(422, '收束上限不能小于软检查时间。')
        s['settings'] = settings.model_dump()
        s['recovery_count'] = 0
        svc.save(s)
        return svc.public(s)

    @app.post('/api/sessions/{sid}/photos')
    async def upload(sid: str, files: list[UploadFile] = File(...)):
        s = state(sid)
        if s['status'] != 'new':
            raise HTTPException(409, '开始访谈后不能再替换初始照片，请新建旅行。')
        if len(s['photos']) + len(files) > 20:
            raise HTTPException(422, '一次最多上传 20 张照片。')
        validated = []
        for file in files:
            data = await file.read(10 * 1024 * 1024 + 1)
            if len(data) > 10 * 1024 * 1024:
                raise HTTPException(422, '单张照片不能超过 10 MB。')
            try:
                with Image.open(io.BytesIO(data)) as im:
                    fmt = im.format
                    if fmt not in ('JPEG', 'PNG', 'WEBP') or im.width * im.height > 40_000_000:
                        raise ValueError()
                    im.verify()
            except Exception:
                raise HTTPException(422, '请使用有效的 JPG、PNG 或 WEBP 照片（不超过 4000 万像素）。')
            validated.append((file.filename or '照片', fmt, data))
        # Validate all uploads before making persistent mutations.
        for name, fmt, data in validated:
            pid = str(uuid.uuid4())
            suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[fmt]
            path = svc.store.folder(sid) / (pid + suffix)
            path.write_bytes(data)
            s['photos'].append(dict(id=pid, name=Path(name).name[:120], path=str(path),
                                    cue=None, visited=False, skipped=False, captured_at=None))
        svc.save(s)
        return svc.public(s)

    @app.get('/api/sessions/{sid}/photos/{pid}')
    async def photo(sid: str, pid: str):
        s = state(sid)
        p = next((p for p in s['photos'] if p['id'] == pid), None)
        if not p:
            raise HTTPException(404, '照片不存在。')
        return FileResponse(p['path'])

    @app.post('/api/sessions/{sid}/start')
    async def start(sid: str):
        s = state(sid)
        if s['status'] != 'new' or not s['photos']:
            raise HTTPException(409, '请先上传照片；已经开始的会话可使用恢复。')
        s['status'] = 'preparing'
        svc.save(s)
        svc.spawn(sid, 'interview', svc.prepare(sid))
        return svc.public(s)

    @app.post('/api/sessions/{sid}/messages')
    async def message(sid: str, req: MessageRequest):
        state(sid)
        if not req.text.strip():
            raise HTTPException(422, '请输入想说的话。')
        return svc.public(svc.submit(sid, req.text, req.request_id, req.photo_id, req.client_submitted_at))

    @app.post('/api/sessions/{sid}/fragments/{fid}/choice')
    async def choice(sid: str, fid: str, req: ChoiceRequest):
        state(sid)
        if not req.text.strip():
            raise HTTPException(422, '片段不能是空白。')
        return svc.public(svc.choose(sid, fid, req))

    @app.post('/api/sessions/{sid}/finish')
    async def finish(sid: str):
        s = state(sid)
        if s['status'] not in ('active', 'review'):
            raise HTTPException(409, '请等待当前回应完成，或先恢复会话。')
        s['status'] = 'closing'
        svc.save(s)
        svc.spawn(sid, 'finish', svc.finish_review(sid))
        return svc.public(s)

    @app.post('/api/sessions/{sid}/generate')
    async def generate(sid: str):
        s = state(sid)
        if s['status'] not in ('review', 'completed', 'stopped'):
            raise HTTPException(409, '请先结束访谈并查看片段。')
        s['status'] = 'generating'
        svc.save(s)
        svc.spawn(sid, 'finish', svc.generate(sid))
        return svc.public(s)

    @app.post('/api/sessions/{sid}/stop')
    async def stop(sid: str):
        state(sid)
        return svc.public(svc.stop(sid))

    @app.post('/api/sessions/{sid}/resume')
    async def resume(sid: str):
        state(sid)
        return svc.public(svc.resume(sid))

    @app.post('/api/sessions/{sid}/metrics')
    async def metric(sid: str, req: MetricRequest):
        s = state(sid)
        if not req.submitted_at <= req.first_response_at <= req.completed_at:
            raise HTTPException(422, '时间顺序无效。')
        if not any(m.get('reply_for') == req.seq for m in s['messages']):
            raise HTTPException(422, '没有对应的实质回应。')
        existing = next((m for m in s['metrics'] if m['seq'] == req.seq), None)
        if not existing or existing.get('completed_at') is None:
            if existing:
                s['metrics'].remove(existing)
            s['metrics'].append(dict(**req.model_dump(), status='completed',
                                     first_response_ms=req.first_response_at - req.submitted_at,
                                     complete_response_ms=req.completed_at - req.submitted_at))
            svc.save(s)
        return {'ok': True}

    @app.get('/api/sessions/{sid}/export')
    async def export(sid: str):
        s = state(sid)
        return Response(json.dumps(svc.public(s), ensure_ascii=False, indent=2),
                        media_type='application/json',
                        headers={'Content-Disposition': 'attachment; filename="travel-record.json"'})

    app.mount('/assets', StaticFiles(directory=Path(__file__).parent / 'static'), name='assets')

    @app.get('/')
    async def index():
        return FileResponse(Path(__file__).parent / 'static/index.html')

    return app


app = create_app()
