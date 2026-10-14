"""Real-model probe with fabricated test dialogue, not participant data."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from travel_demo import prompts
from travel_demo.llm import MiniMax
from travel_demo.models import RecordDraft, Record, Settings, Variants
from travel_demo.node_format import parse_nodes


async def main():
    model=MiniMax()
    data=dict(old_state=dict(questions=[],focus='',boundaries=[],writing_brief=''),nodes_markdown='',
              existing_fragments=[],photos=[],new_turns=[dict(seq=2,role='user',type='conversation',
              text='午饭后，我和姐姐走到湖边，看到一张空椅子，就一起坐了五分钟。我们没有赶着去下一个地方，也没有拍照。我记得自己很放松，想留下的就是这五分钟。')])
    try:
        draft=await model.call(prompts.record_prompt(),data,RecordDraft,Settings(request_timeout=90,retries=1))
        record=Record(nodes=parse_nodes(draft.nodes_markdown),**draft.model_dump(exclude={'nodes_markdown'}))
        assert record.nodes and record.fragments and record.final_node_ids, 'Material selection was incomplete'
        assert all(n.source_seqs==[2] for n in record.nodes), 'Invalid provenance'
        print(json.dumps({'ok':True,'record':record.model_dump()},ensure_ascii=False),flush=True)
        variants=await model.call(prompts.variants_prompt(),dict(selected_nodes=[n.model_dump() for n in record.nodes],
                                 user_words=data['new_turns'],fragment_title=record.fragments[0].title),
                                 Variants,Settings(request_timeout=90,retries=1))
        assert len({v.style for v in variants.variants})==3
        assert all(all(word in v.text for word in ('姐姐','五分钟','放松')) for v in variants.variants), 'Test facts were omitted'
        print(json.dumps({'ok':True,'variants':variants.model_dump()},ensure_ascii=False),flush=True)
    finally:
        await model.close()


asyncio.run(main())
