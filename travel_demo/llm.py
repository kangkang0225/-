import asyncio
import base64
import io
import json
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image, ImageOps
from pydantic import ValidationError

from .models import Settings

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)


class ModelError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
    decoder = json.JSONDecoder()
    # Accept an object with an optional short prefix, not arbitrary eval or XML.
    for i, c in enumerate(text):
        if c == '{':
            try:
                value, _ = decoder.raw_decode(text[i:])
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
    raise ModelError('模型返回格式不完整，请重试；已有材料已保存。')


def image_data(path: Path) -> str:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert('RGB')
        im.thumbnail((1440, 1440))
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=86)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


class MiniMax:
    def __init__(self):
        self.key = os.getenv('MINIMAX_API_KEY', '')
        self.base = os.getenv('MINIMAX_BASE_URL', 'https://api.minimax.cn/v1').rstrip('/')
        self.text_model = os.getenv('MODEL_NAME', 'MiniMax-M2.7')
        self.vision_model = os.getenv('DEMO_VISION_MODEL', 'MiniMax-M3')
        self.slots = asyncio.Semaphore(max(1, int(os.getenv('DEMO_MODEL_CONCURRENCY', '3'))))
        self.client = httpx.AsyncClient()

    async def close(self):
        await self.client.aclose()

    async def call(self, system, data, schema, settings: Settings, image: Path | None = None):
        if not self.key:
            raise ModelError('请先在 StorySage 的 .env 中配置 MiniMax Key。')
        system += '\n输出 JSON 必须符合这个数据结构：\n' + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        content = json.dumps(data, ensure_ascii=False)
        if image:
            content = [{'type': 'text', 'text': content},
                       {'type': 'image_url', 'image_url': {'url': image_data(image)}}]
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]
        model = self.vision_model if image else self.text_model
        last = None
        started = time.perf_counter()
        for attempt in range(settings.retries + 1):
            try:
                remain = settings.request_timeout - (time.perf_counter() - started)
                if remain <= 0:
                    raise TimeoutError()
                async with asyncio.timeout(remain), self.slots:
                    body = {'model': model, 'messages': messages, 'temperature': 0.65,
                            'max_tokens': 8192, 'reasoning_split': True}
                    r = await self.client.post(self.base + '/chat/completions',
                                               headers={'Authorization': 'Bearer ' + self.key},
                                               json=body, timeout=remain)
                    if r.status_code >= 400:
                        # Do not echo vendor bodies (may contain request material/credentials).
                        if r.status_code in (401, 403):
                            raise ModelError(f'MiniMax 拒绝请求（{r.status_code}），请检查 Key 与模型权限。')
                        if r.status_code == 400:
                            raise ModelError(f'模型 {model} 不接受当前输入，请检查图片模型配置。')
                        r.raise_for_status()
                    j = r.json()
                    if j.get('base_resp', {}).get('status_code', 0):
                        raise ModelError('MiniMax 返回调用错误，请检查配额与模型权限。')
                    c = j['choices'][0]
                    try:
                        parsed = parse_json(c['message'].get('content') or '')
                    except ModelError as exc:
                        raise ValueError('Incomplete JSON') from exc
                    value = schema.model_validate(parsed)
                    return value
            except (httpx.TimeoutException, TimeoutError):
                last = ModelError('本次响应超时，已有材料已保存，可以重试。')
            except (ValueError, KeyError, ValidationError) as e:
                last = ModelError('模型返回格式未通过检查，已有材料已保存，可以重试。')
                if attempt < settings.retries:
                    messages.append({'role': 'user', 'content': '上一输出无法解析。只返回符合上述结构的完整 JSON。'})
            except httpx.HTTPStatusError as e:
                last = ModelError(f'MiniMax 暂时不可用（{e.response.status_code}），请稍后重试。')
            except httpx.RequestError:
                last = ModelError('无法连接 MiniMax，请检查网络；已有材料已保存。')
            if attempt < settings.retries:
                remaining=settings.request_timeout-(time.perf_counter()-started)
                if remaining <= 0:
                    break
                await asyncio.sleep(min(2 ** attempt, 2, remaining))
        raise last or ModelError('调用未完成，请重试。')
