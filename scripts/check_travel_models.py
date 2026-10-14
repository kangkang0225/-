"""Probe configured text and vision models using a synthetic image, never print credentials."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from travel_demo.llm import MiniMax
from travel_demo.models import Cue, Emotion, Settings
from travel_demo import prompts


async def main():
    model = MiniMax()
    path = Path(__file__).resolve().parents[1] / 'data/travel_demo/model-check.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new('RGB', (320, 240), '#eee9dd')
    ImageDraw.Draw(im).rectangle((100, 70, 220, 170), fill='#b84434')
    im.save(path)
    settings = Settings(request_timeout=90, retries=0)
    try:
        for label, schema, prompt, data, photo in (
            ('text', Emotion, prompts.emotion_prompt(), {'current_text': '这次旅行很平静。'}, None),
            ('vision', Cue, prompts.cue_prompt(), {'photo_id': 'synthetic-test'}, path),
        ):
            try:
                result = await model.call(prompt, data, schema, settings, photo)
                print(json.dumps({'probe': label, 'ok': True, 'result': result.model_dump()}, ensure_ascii=False), flush=True)
            except Exception as e:
                print(json.dumps({'probe': label, 'ok': False, 'error': str(e)}, ensure_ascii=False), flush=True)
    finally:
        await model.close()


asyncio.run(main())
