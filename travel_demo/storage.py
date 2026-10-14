import json
import os
import uuid
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def folder(self, sid: str) -> Path:
        # UUID validation also prevents path traversal on photo and export routes.
        return self.root / str(uuid.UUID(sid))

    def load(self, sid: str) -> dict:
        path = self.folder(sid) / 'session.json'
        if not path.exists():
            raise KeyError(sid)
        return json.loads(path.read_text(encoding='utf-8'))

    def save(self, state: dict):
        folder = self.folder(state['id'])
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / 'session.tmp'
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, folder / 'session.json')

    def list(self):
        result = []
        for path in self.root.glob('*/session.json'):
            try:
                s = json.loads(path.read_text(encoding='utf-8'))
                result.append({k: s.get(k) for k in ('id', 'title', 'created_at', 'updated_at', 'status')})
            except (ValueError, OSError):
                continue
        return sorted(result, key=lambda x: x['updated_at'] or 0, reverse=True)
