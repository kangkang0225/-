"""Integration checks using the downloaded CPU model, without API requests."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from content.memory_bank.memory_bank_vector_db import VectorMemoryBank
from content.question_bank.question_bank_vector_db import QuestionBankVectorDB
from utils.llm.embeddings import get_embedding_encoder, validate_saved_embeddings
import utils.llm.embeddings as embedding_module


@pytest.fixture(autouse=True)
def offline_local_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    # Verify that no OpenAI embedding client is even created.
    def forbidden_client(*args, **kwargs):
        raise AssertionError("Local embeddings must not create an API client")
    monkeypatch.setattr(embedding_module, "OpenAI", forbidden_client)


def test_shared_cpu_encoder_and_valid_vectors():
    memory_bank, question_bank = VectorMemoryBank(), QuestionBankVectorDB()
    assert memory_bank.encoder is question_bank.encoder
    assert memory_bank.embedding_dimension == question_bank.embedding_dimension == 384
    for text in ("我和姐姐在沙漠看了一场演出。", "A family trip to the desert.", "回忆" * 1000):
        vector = memory_bank._get_embedding(text)
        assert vector.shape == (384,)
        assert np.isfinite(vector).all()
        assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)


def test_memory_search_survives_save_and_reload():
    bank = VectorMemoryBank()
    travel = bank.add_memory("沙漠旅行", "我和姐姐到沙漠看演出，大家挥动手环，屏幕亮起光点。", 7, "test fixture")
    bank.add_memory("日常工作", "我在公司办公室整理财务表格。", 3, "test fixture")
    bank.save_to_file("test_user")
    restored = VectorMemoryBank.load_from_file("test_user")
    assert restored.index.ntotal == 2
    assert restored.search_memories("和家人一起在沙漠看表演", k=1)[0].id == travel.id
    assert restored.search_memories("Watching a desert performance with my sister", k=1)[0].id == travel.id


def test_question_search_survives_save_and_reload():
    bank = QuestionBankVectorDB()
    travel = bank.add_question("和你一起去沙漠旅行的人是谁？")
    bank.add_question("你平时在公司负责什么工作？")
    bank.save_to_file("test_user")
    restored = QuestionBankVectorDB.load_from_file("test_user")
    assert restored.index.ntotal == 2
    assert restored.search_questions("这次沙漠游览你和谁同行？", k=1)[0].id == travel.id


def test_incompatible_saved_vectors_are_rejected():
    encoder = get_embedding_encoder()
    with pytest.raises(ValueError, match="different model"):
        validate_saved_embeddings({"embeddings": [{"embedding": [0] * 1536}]}, encoder)
    with pytest.raises(ValueError, match="dimension"):
        validate_saved_embeddings({
            "embedding_model": encoder.fingerprint,
            "embeddings": [{"embedding": [0] * 100}],
        }, encoder)
