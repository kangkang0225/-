"""Download only the pinned, quantized E5 model and its tokenizer."""
import hashlib
import json
from pathlib import Path

import requests

MODEL_ID = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
FILES = ("model_qint8_avx512_vnni.onnx", "tokenizer.json")
TARGET = Path(__file__).resolve().parents[1] / "models" / "multilingual-e5-small"


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    listing = requests.get(
        f"https://huggingface.co/api/models/{MODEL_ID}/tree/{REVISION}/onnx",
        timeout=30,
    )
    listing.raise_for_status()
    entries = {entry["path"].split("/")[-1]: entry for entry in listing.json()}
    hashes = {}
    for name in FILES:
        entry = entries[name]
        expected_hash = entry.get("lfs", {}).get("oid")
        target = TARGET / name
        if target.exists() and target.stat().st_size == entry["size"]:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if not expected_hash or digest == expected_hash:
                hashes[name] = digest
                print(f"Already downloaded: {name}", flush=True)
                continue
        print(f"Downloading {name} ({entry['size'] / 1e6:.1f} MB)", flush=True)
        with requests.get(
            f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/onnx/{name}",
            stream=True, timeout=(30, 60),
        ) as response:
            response.raise_for_status()
            partial = target.with_suffix(target.suffix + ".part")
            digest = hashlib.sha256()
            with partial.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
        if partial.stat().st_size != entry["size"]:
            raise ValueError(f"Incomplete download: {name}")
        if expected_hash and digest.hexdigest() != expected_hash:
            raise ValueError(f"Checksum mismatch: {name}")
        partial.replace(target)
        hashes[name] = digest.hexdigest()
    (TARGET / "manifest.json").write_text(json.dumps({
        "model_id": MODEL_ID, "revision": REVISION, "sha256": hashes,
    }, indent=2), encoding="utf-8")
    print(f"Local model ready: {sum((TARGET / f).stat().st_size for f in FILES) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
