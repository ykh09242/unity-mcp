"""Score a bounded shortlist with a local BGE cross-encoder in its isolated runtime."""

import argparse
import importlib
import json
import os
from pathlib import Path
import struct
import sys


MAX_PASSAGES = 48
MAX_CHARS = 1024
MAX_LINE = (MAX_PASSAGES + 1) * MAX_CHARS * 6 + 4096


class Reranker:
    def __init__(self, profile: Path):
        os.environ["RAYON_NUM_THREADS"] = "1"
        self.np = importlib.import_module("numpy")
        ort = importlib.import_module("onnxruntime")
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(profile / "reranker.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = importlib.import_module("tokenizers").Tokenizer.from_file(
            str(profile / "reranker-tokenizer.json")
        )
        self.tokenizer.enable_truncation(max_length=192)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")

    def score(self, query: str, documents: list[str]):
        if not 0 < len(documents) <= MAX_PASSAGES:
            raise ValueError("Invalid local reranker shortlist size")
        query = query[:MAX_CHARS].encode("utf-8", "replace").decode()
        scores = self.np.empty(len(documents), dtype="<f4")
        for start in range(0, len(documents), 16):
            pairs = [
                (query, text[:MAX_CHARS].encode("utf-8", "replace").decode())
                for text in documents[start : start + 16]
            ]
            tokens = self.tokenizer.encode_batch(pairs)
            inputs = {
                "input_ids": self.np.asarray([t.ids for t in tokens], dtype=self.np.int64),
                "attention_mask": self.np.asarray(
                    [t.attention_mask for t in tokens], dtype=self.np.int64
                ),
            }
            logits = self.session.run(["logits"], inputs)[0]
            if logits.shape != (len(pairs), 1) or not self.np.isfinite(logits).all():
                raise ValueError("Invalid local reranker output")
            scores[start : start + len(pairs)] = logits[:, 0]
        return scores

    def serve(self) -> None:
        documents = []
        while line := sys.stdin.buffer.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE:
                raise ValueError("Oversized local reranker request")
            request = json.loads(line)
            documents = request.get("documents", documents)
            scores = self.score(request["query"], documents)
            sys.stdout.buffer.write(struct.pack("<I", len(scores)) + scores.tobytes())
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    model = Reranker(args.profile)
    if args.check:
        model.score("Read the console.", ["Report messages from the editor console."])
    else:
        model.serve()
