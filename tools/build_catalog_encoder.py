"""Rebuild the bundled CPU search encoder from the pinned upstream files."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile


MODEL = "sentence-transformers/static-similarity-mrl-multilingual-v1"
REVISION = "b68f4122911bcffcd6e1f695f2d99cd6788972d8"
WEIGHTS_SHA256 = "8245ab78ee71dded845a82d2270fcb9e785b29dad0e1619f69d5390c47d9ba00"
TOKENIZER_SHA256 = "11aaf894a4ccf3d95e8830e27c0f8152791fbbff2b988e29a265580b86edd216"
DIMENSIONS = 256
OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "Server/src/services/registry/model/static-multilingual-v1.q8"
)


def verify_file(path: Path, digest: str) -> None:
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != digest:
            raise ValueError(f"SHA-256 mismatch for {path}")


def convert(source: Path, output: Path) -> None:
    weights = source / "model.safetensors"
    tokenizer = source / "tokenizer.json"
    verify_file(weights, WEIGHTS_SHA256)
    verify_file(tokenizer, TOKENIZER_SHA256)
    vocab = json.loads(tokenizer.read_bytes())["model"]["vocab"]
    words = sorted(vocab, key=vocab.__getitem__)
    encoded_words = "\n".join(words).encode("utf-8")

    output.parent.mkdir(exist_ok=True, parents=True)
    with weights.open("rb") as table:
        header_size = struct.unpack("<Q", table.read(8))[0]
        tensor = json.loads(table.read(header_size))["embedding.weight"]
        rows, columns = tensor["shape"]
        assert rows == len(words) and tensor["data_offsets"][0] == 0 and tensor["dtype"] == "F32"
        row = struct.Struct(f"<{DIMENSIONS}f")
        packed = struct.Struct(f"<f{DIMENSIONS}b")
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            with temp_path.open("wb") as target:
                target.write(struct.pack("<I", len(encoded_words)))
                target.write(encoded_words)
                for _ in range(rows):
                    values = row.unpack_from(table.read(columns * 4))
                    scale = max(abs(value) for value in values) / 127
                    codes = (
                        [round(value / scale) for value in values] if scale else [0] * DIMENSIONS
                    )
                    target.write(packed.pack(scale, *codes))
            temp_path.replace(output)
        finally:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", type=Path, help="Directory containing original model and tokenizer"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    convert(args.source, args.output)
    with args.output.open("rb") as artifact:
        print(hashlib.file_digest(artifact, "sha256").hexdigest(), args.output)
