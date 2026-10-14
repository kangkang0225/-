"""Archive primary source material; never import the original LLM agents."""
import ast
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
OUT = ROOT / 'travel_demo' / 'sources'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sources = []

    def add(name, text, origin, locator, kind='author_prompt'):
        (OUT / name).write_text(text, encoding='utf-8')
        sources.append(dict(file=name, origin=origin, locator=locator, kind=kind,
                            sha256=hashlib.sha256(text.encode()).hexdigest()))

    for name, rel, constants in [
        ('storysage_interviewer.txt', 'src/agents/interviewer/prompts.py', ['CONTEXT', 'INSTRUCTIONS']),
        ('storysage_scribe.txt', 'src/agents/session_scribe/prompts.py',
         ['UPDATE_MEMORY_QUESTION_BANK_INSTRUCTIONS', 'CONSIDER_AND_PROPOSE_FOLLOWUPS_INSTRUCTIONS']),
        ('storysage_writer.txt', 'src/agents/biography_team/section_writer/prompts.py', ['INSTRUCTIONS']),
    ]:
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8-sig'))
        values = {}
        for n in tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        values[t.id] = n.value.value
        text = '\n\n'.join(f'[{c}]\n{values[c]}' for c in constants)
        add(name, text, 'https://github.com/ShayanTalaei/StorySage', rel + ': ' + ', '.join(constants))

    guide = (WORKSPACE / 'output/zotero_code_audit/SFIMPQYQ.txt').read_text(encoding='utf-8-sig')
    start = guide.index('B Memory Graph Extrapolation')
    end = guide.index('E Autobiography Generation', start)
    add('guidellm_B_D.txt', guide[start:end], 'https://aclanthology.org/2025.naacl-long.287.pdf',
        'Appendices B–D; local PDF text extraction')
    skill = Path('C:/Users/23345/SCU/研一下/rfid游记/backend/backend-rfid/src/main/resources/skills/travelog-writer/references/heartfelt-visit.md')
    add('project_heartfelt_visit.md', skill.read_text(encoding='utf-8-sig'), str(skill),
        'Existing project writing rules, not a paper prompt', 'project_rules')

    # Public primary supplement is fetched from the Europe PMC mirror.
    import requests
    sys.path.insert(0, str(WORKSPACE / 'tmp/paper-card-deps'))
    import pymupdf
    url = 'https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12265993/supplementaryFiles'
    r = requests.get(url, timeout=45)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        key = next(n for n in z.namelist() if 'Appendix_A' in n and n.endswith('.pdf'))
        blob = z.read(key)
        (OUT / 'micro_narratives_appendix_A.pdf').write_bytes(blob)
        with pymupdf.open(stream=blob, filetype='pdf') as doc:
            text = '\n\n'.join(p.get_text() for p in doc)
        add('micro_narratives_appendix_A.txt', text, url, 'Appendix A: exact extraction, creation and adaptation prompts')
        sources.append(dict(file='micro_narratives_appendix_A.pdf', origin=url, locator=key,
                            kind='primary_supplement', sha256=hashlib.sha256(blob).hexdigest()))
    (OUT / 'manifest.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Archived {len(sources)} sources. No API keys or model calls involved.')


if __name__ == '__main__':
    main()
