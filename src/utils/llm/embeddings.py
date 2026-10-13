"""Independent embedding configuration, shared by both existing FAISS banks."""
from functools import lru_cache
import json
import os
from pathlib import Path

import numpy as np
from openai import OpenAI


class OpenAIEmbeddings:
    def __init__(self, dimension=1536):
        self.client = OpenAI()
        self.dimension = dimension
        self.fingerprint = "openai:text-embedding-3-small"

    def encode(self, text):
        response = self.client.embeddings.create(input=text, model="text-embedding-3-small")
        return np.asarray(response.data[0].embedding, dtype=np.float32)


class LocalEmbeddings:
    def __init__(self, model_dir):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_dir = Path(model_dir)
        manifest = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
        self.dimension = 384
        self.fingerprint = (
            f"local:{manifest['model_id']}:{manifest['revision']}:qint8:query:mean:l2"
        )
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        self.session = ort.InferenceSession(
            str(model_dir / "model_qint8_avx512_vnni.onnx"),
            sess_options=options, providers=["CPUExecutionProvider"],
        )

    def encode(self, text):
        # E5 recommends query prefixes on both sides for symmetric similarity.
        tokens = self.tokenizer.encode("query: " + text)
        inputs = {
            "input_ids": np.asarray([tokens.ids], dtype=np.int64),
            "attention_mask": np.asarray([tokens.attention_mask], dtype=np.int64),
            "token_type_ids": np.asarray([tokens.type_ids], dtype=np.int64),
        }
        hidden = self.session.run(None, {
            arg.name: inputs[arg.name] for arg in self.session.get_inputs()
        })[0]
        mask = inputs["attention_mask"][..., None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1)
        vector = pooled[0].astype(np.float32)
        vector /= max(float(np.linalg.norm(vector)), 1e-12)
        if vector.shape != (self.dimension,) or not np.isfinite(vector).all():
            raise ValueError("Invalid local embedding output")
        return vector


@lru_cache(maxsize=2)
def _local_encoder(model_dir):
    return LocalEmbeddings(model_dir)


def get_embedding_encoder(openai_dimension=1536):
    provider = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
    if provider == "openai":
        return OpenAIEmbeddings(openai_dimension)
    if provider == "local":
        root = Path(__file__).resolve().parents[3]
        path = Path(os.getenv("LOCAL_EMBEDDING_DIR", "models/multilingual-e5-small"))
        if not path.is_absolute():
            path = root / path
        return _local_encoder(str(path.resolve()))
    raise ValueError(f"Unknown embedding provider: {provider}")


def validate_saved_embeddings(data, encoder):
    """Never mix vector spaces when restoring an existing user's FAISS index."""
    saved = data.get("embedding_model", "openai:text-embedding-3-small")
    if data.get("embeddings") and saved != encoder.fingerprint:
        raise ValueError("Saved embeddings use a different model. Start with a new user_id.")
    if any(len(item["embedding"]) != encoder.dimension for item in data["embeddings"]):
        raise ValueError("Saved embedding dimension does not match the configured model.")
