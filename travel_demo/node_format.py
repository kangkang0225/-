"""Transient model-facing Markdown; JSON remains the only persistent node state."""
import re
from .models import Node


def render_nodes(nodes):
    result = []
    for value in nodes:
        n = Node.model_validate(value)
        result.append(f'### 记忆节点 {n.id}\n时间：{n.date or "未知"}\n主题：{n.topic}\n'
                      f'人物：{"；".join(n.people) or "未知"}\n详细描述：{n.description}\n'
                      f'原话序号：{",".join(map(str,n.source_seqs))}\n照片标识：{",".join(n.photo_ids) or "未知"}')
    return '\n\n'.join(result)


def parse_nodes(text):
    if not text.strip() or text.strip() in ('无', '暂无', '无节点'):
        return []
    # Only documented field labels are parsed; no arbitrary YAML, code, or eval.
    headers = list(re.finditer(r'^###\s+记忆节点\s+([A-Za-z0-9_-]+)\s*$', text, re.M))
    if not headers or text[:headers[0].start()].strip():
        raise ValueError('记忆节点格式不完整。')
    nodes = []
    fields = ('时间', '主题', '人物', '详细描述', '原话序号', '照片标识')
    for i,h in enumerate(headers):
        body=text[h.end():headers[i+1].start() if i+1<len(headers) else len(text)]
        matches=list(re.finditer(r'^(时间|主题|人物|详细描述|原话序号|照片标识)[：:]\s*(.*)$',body,re.M))
        if tuple(m.group(1) for m in matches)!=fields:
            raise ValueError('记忆节点字段缺失或顺序错误。')
        values={m.group(1):body[m.start(2):matches[j+1].start() if j+1<len(matches) else len(body)].strip()
                for j,m in enumerate(matches)}
        seqs=[v.strip() for v in re.split(r'[,，;；\s]+',values['原话序号']) if v.strip()]
        if not seqs or any(not s.isdecimal() for s in seqs):
            raise ValueError('节点必须引用有效用户话语序号。')
        unknown=('', '未知', '待确认', '待问', '无', 'null', '[]')
        people=[] if values['人物'] in unknown else re.split(r'[;；]',values['人物'])
        photos=[] if values['照片标识'] in unknown else re.split(r'[,，;；\s]+',values['照片标识'])
        nodes.append(Node(id=h.group(1),date=None if values['时间'] in unknown else values['时间'],
                          topic=values['主题'],people=[p.strip() for p in people if p.strip()],
                          description=values['详细描述'],source_seqs=[int(s) for s in seqs],photo_ids=photos))
    return nodes
