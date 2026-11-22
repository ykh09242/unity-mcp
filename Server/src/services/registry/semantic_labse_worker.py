"""Isolated local CPU worker. The optional profile owns the numerical dependencies."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import struct
import sys


DIMENSIONS = 768
MAX_DOCS = 2048
MAX_CHARS = 1024
MAX_LINE = MAX_DOCS * MAX_CHARS * 6 + 65536
_INDEX = Path(__file__).with_name("model") / "labse-catalog.f16"
_INDEX_SHA256 = "b748b06db76ae5e2c69e7b8d0a467718f5e3669efc30d4cefea0849815dca155"
_ROW_BYTES = 32 + 2 * DIMENSIONS


class Labse:
    def __init__(self, profile: Path):
        # The runtime is launched with -I. Keep its CPU workers bounded on large hosts.
        os.environ["RAYON_NUM_THREADS"] = "1"
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
        self.np = importlib.import_module("numpy")
        ort = importlib.import_module("onnxruntime")
        tokenizers = importlib.import_module("tokenizers")
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(profile / "labse.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = tokenizers.Tokenizer.from_file(str(profile / "labse-tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=96)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        with (profile / "labse-dense.safetensors").open("rb") as source:
            header_size = struct.unpack("<Q", source.read(8))[0]
            header = json.loads(source.read(header_size))
            weights = source.read()
        self.weight = self._tensor(weights, header["linear.weight"], (DIMENSIONS, DIMENSIONS))
        self.bias = self._tensor(weights, header["linear.bias"], (DIMENSIONS,))

    def _tensor(self, data: bytes, header: dict, shape: tuple):
        start, end = header["data_offsets"]
        if header["dtype"] != "F32" or tuple(header["shape"]) != shape:
            raise ValueError("Unexpected dense head tensor")
        return self.np.frombuffer(data[start:end], dtype="<f4").reshape(shape)

    def encode(self, texts: list[str]):
        np = self.np
        output = np.empty((len(texts), DIMENSIONS), dtype=np.float32)
        for start in range(0, len(texts), 12):
            # JSON can contain escaped lone surrogates; Rust tokenizers accept valid UTF-8 only.
            batch = [text.encode("utf-8", "replace").decode() for text in texts[start : start + 12]]
            tokens = self.tokenizer.encode_batch(batch)
            inputs = {
                name: np.asarray([getattr(row, field) for row in tokens], dtype=np.int64)
                for name, field in (
                    ("input_ids", "ids"),
                    ("attention_mask", "attention_mask"),
                    ("token_type_ids", "type_ids"),
                )
            }
            hidden = self.session.run(["last_hidden_state"], inputs)[0]
            vectors = np.tanh(hidden[:, 0, :] @ self.weight.T + self.bias)
            output[start : start + len(tokens)] = vectors / np.linalg.norm(
                vectors, axis=1, keepdims=True
            )
        return output

    def build_index(self, documents: list[str], output: Path) -> None:
        docs = sorted(set(text[:MAX_CHARS] for text in documents))
        vectors = self.encode(docs)
        with output.open("wb") as target:
            for text, vector in zip(docs, vectors):
                target.write(self.key(text))
                target.write(vector.astype("<f2").tobytes())

    @staticmethod
    def key(text: str) -> bytes:
        return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).digest()

    def public_vectors(self) -> dict:
        if not _INDEX.is_file():
            return {}
        with _INDEX.open("rb") as file:
            data = file.read(_ROW_BYTES * MAX_DOCS + 1)
        if len(data) % _ROW_BYTES or len(data) > _ROW_BYTES * MAX_DOCS:
            return {}
        if hashlib.sha256(data).hexdigest() != _INDEX_SHA256:
            return {}
        return {
            data[pos : pos + 32]: self.np.frombuffer(data, "<f2", DIMENSIONS, pos + 32)
            for pos in range(0, len(data), _ROW_BYTES)
        }

    def catalog(self, documents: list[str], public_vectors: dict):
        if len(documents) > MAX_DOCS:
            raise ValueError("Catalog exceeds local worker budget")
        docs = [text[:MAX_CHARS] for text in documents]
        rows = self.np.empty((len(docs), DIMENSIONS), dtype=self.np.float32)
        missing = []
        for index, text in enumerate(docs):
            vector = public_vectors.get(self.key(text))
            if vector is None:
                missing.append(index)
            else:
                rows[index] = vector
        if missing:
            rows[missing] = self.encode([docs[i] for i in missing])
        return rows

    def serve(self) -> None:
        public_vectors = self.public_vectors()
        vectors = None
        while line := sys.stdin.buffer.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE:
                raise ValueError("Oversized local request")
            request = json.loads(line)
            if "documents" in request:
                vectors = self.catalog(request["documents"], public_vectors)
            if vectors is None:
                raise ValueError("Catalog is not initialized")
            query = self.encode([request["query"][:MAX_CHARS]])[0]
            scores = (vectors @ query).astype("<f4", copy=False)
            sys.stdout.buffer.write(struct.pack("<I", len(scores)) + scores.tobytes())
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--index", nargs=2, metavar=("DOCUMENTS", "OUTPUT"), type=Path)
    args = parser.parse_args()
    model = Labse(args.profile)
    if args.check:
        check = model.encode(["Read the local catalog."])
        if check.shape != (1, DIMENSIONS) or not model.np.isfinite(check).all():
            raise ValueError("Invalid inference result")
    elif args.index:
        source_file, destination = args.index
        model.build_index(json.loads(source_file.read_bytes()), destination)
    else:
        model.serve()
