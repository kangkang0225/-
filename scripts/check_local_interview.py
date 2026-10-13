"""A live startup/recording smoke check with synthetic input, isolated from the demo user."""
import asyncio
from datetime import datetime
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TIKTOKEN_CACHE_DIR"] = str(ROOT / ".setup-cache" / "tiktoken")
from interview_session.interview_session import InterviewSession
from interview_session.session_models import Message, MessageType


async def main():
    captured = asyncio.Event()

    class Capture:
        async def on_message(self, message):
            captured.set()

    session = InterviewSession(
        interaction_mode="terminal",
        user_config={"user_id": "_setup_check_" + datetime.now().strftime("%Y%m%d%H%M%S")},
    )
    # Suppress interactive stdin only in this check; run the actual interviewer.
    session._subscriptions["Interviewer"] = [Capture()]
    await asyncio.wait_for(session._interviewer.on_message(None), timeout=90)
    await asyncio.wait_for(captured.wait(), timeout=5)
    print("CHECK: Original interviewer produced its first response.", flush=True)
    await session.session_scribe.on_message(session.chat_history[-1])
    await session.session_scribe.on_message(Message(
        id="synthetic-setup-answer", type=MessageType.CONVERSATION, role="User",
        content="I grew up in Chengdu. On weekends my sister and I visited our grandparents and helped them cook dinner. These family evenings are a warm childhood memory.",
        timestamp=datetime.now(),
    ))
    await asyncio.sleep(0.1)
    deadline = time.monotonic() + 150
    while session.session_scribe.processing_in_progress:
        if time.monotonic() > deadline:
            raise TimeoutError("Session scribe did not finish the smoke check")
        await asyncio.sleep(0.25)
    assert session.memory_bank.memories, "Session scribe did not store the synthetic memory"
    results = session.memory_bank.search_memories("Cooking with family in Chengdu")
    assert results, "Saved memory could not be retrieved"
    session.memory_bank.save_to_file(session.user_id)
    session.historical_question_bank.save_to_file(session.user_id)
    session.end_session()
    print("CHECK: Original scribe stored a memory and local FAISS retrieval succeeded.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
