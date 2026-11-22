"""Build the offline Unigram coverage encoder from the pinned MinishLab weights."""

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import struct
import tempfile

from build_catalog_encoder import verify_file


REVISION = "6fea87d68483744bdb34f8ddab7f5c31a94dc0b8"
WEIGHTS_SHA256 = "14b5eb39cb4ce5666da8ad1f3dc6be4346e9b2d601c073302fa0a31bf7943397"
TOKENIZER_SHA256 = "19f1909063da3cfe3bd83a782381f040dccea475f4816de11116444a73e1b6a1"
OUTPUT = Path(__file__).resolve().parents[1] / "Server/src/services/registry/model/potion-256.q2"
FLOAT_ROW = struct.Struct("<256f")


def convert(weights: Path, tokenizer: Path, output: Path) -> None:
    verify_file(weights, WEIGHTS_SHA256)
    verify_file(tokenizer, TOKENIZER_SHA256)
    config = json.loads(tokenizer.read_bytes())
    vocab = config["model"]["vocab"]
    tokens = "\n".join(token for token, _ in vocab).encode("utf-8")
    charsmap = base64.b64decode(
        config["normalizer"]["normalizers"][0]["normalizers"][0]["precompiled_charsmap"]
    )
    # Store the vocabulary and scores uncompressed. Package formats already compress them.
    with weights.open("rb") as source, output.open("wb") as target:
        header_bytes = struct.unpack("<Q", source.read(8))[0]
        tensor = json.loads(source.read(header_bytes))["embeddings"]
        if (
            tensor["shape"] != [len(vocab), 256]
            or tensor["data_offsets"][0]
            or tensor["dtype"] != "F32"
        ):
            raise ValueError("Unexpected embedding tensor layout")
        target.write(struct.pack("<4sIII", b"PCQ2", len(vocab), len(tokens), len(charsmap)))
        target.write(tokens)
        target.write(charsmap)
        target.write(struct.pack(f"<{len(vocab)}f", *(score for _, score in vocab)))
        for _ in vocab:
            values = FLOAT_ROW.unpack(source.read(FLOAT_ROW.size))
            scale = math.sqrt(math.fsum(value * value for value in values) / 256)
            threshold = 0.982 * scale
            codes = [(2 if value >= 0 else 0) + (abs(value) > threshold) for value in values]
            target.write(struct.pack("<e", scale))
            target.write(
                bytes(a | b << 2 | c << 4 | d << 6 for a, b, c, d in zip(*[iter(codes)] * 4))
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="Compare against the existing artifact"
    )
    args = parser.parse_args()
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as temp:
        built = Path(temp.name)
    try:
        convert(args.weights, args.tokenizer, built)
        with built.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if args.check:
            verify_file(args.output, digest)
        else:
            built.replace(args.output)
        print(f"{args.output.stat().st_size} bytes, SHA-256 {digest}")
    finally:
        built.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
