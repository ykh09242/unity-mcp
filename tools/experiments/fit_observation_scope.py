"""Fit an offline scope contrast from paired texts unrelated to the Unity catalog."""

import argparse
import gzip
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import struct
import sys

PAIRS = Path(__file__).with_name("observation_scope_pairs.json")
HEADER = b"UMRSCP01"


def capture(output):
    if not __package__:
        sys.path.insert(0, str(PAIRS.parents[2] / "Server/src"))
    from services.registry import semantic_metric

    pairs = json.loads(PAIRS.read_bytes())
    with gzip.open(output, "wt", encoding="utf-8") as stream:
        json.dump(
            [
                [
                    [
                        [semantic_metric._encode(text, index, document=True) for text in pair]
                        for pair in pairs[role]
                    ]
                    for role in ("requests", "declarations")
                ]
                for index in (0, 1)
            ],
            stream,
            separators=(",", ":"),
        )


def fit(source, output, *, check=False):
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    np = import_module("numpy")
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        encoders = json.load(stream)
    artifact = bytearray(HEADER)
    for roles in encoders:
        for pairs in roles:
            values = np.asarray(pairs, dtype="float64")
            if values.shape != (len(pairs), 2, 256) or not len(pairs):
                raise ValueError("Scope calibration requires nonempty pairs of embedded texts")
            features = values.reshape(-1, 256)
            mean = features.mean(axis=0)
            centered = features - mean
            labels = np.tile([1.0, -1.0], len(pairs))
            # Each contrast also teaches the change in extent while keeping the subject fixed.
            differences = values[:, 0] - values[:, 1]
            axis = np.linalg.solve(
                centered.T @ centered + differences.T @ differences + np.eye(256) * 0.4,
                centered.T @ labels + 2 * differences.sum(axis=0),
            )
            scale = float(np.abs(axis).max()) / 127 or 1.0
            artifact.extend(
                struct.pack(
                    "<f256bf", scale, *np.rint(axis / scale).astype(int), -float(mean @ axis)
                )
            )
    if check:
        if output.read_bytes() != artifact:
            raise SystemExit(f"Rebuilt scope weights differ from {output}")
    else:
        output.write_bytes(artifact)
    print(f"{len(artifact)} bytes, SHA-256 {hashlib.sha256(artifact).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("capture")
    snapshot.add_argument("snapshot", type=Path)
    build = commands.add_parser("fit")
    build.add_argument("snapshot", type=Path)
    build.add_argument("output", type=Path)
    build.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.snapshot)
    else:
        fit(args.snapshot, args.output, check=args.check)
