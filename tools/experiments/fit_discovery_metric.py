"""Rebuild the bundled operation metric using the local catalog and published training queries."""

import argparse
from dataclasses import replace
import gzip
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
import struct
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
TRAINING = {
    "universal_discovery_queries.json": "645d3fcd1924820fd62fa9320bfde872c8a6af10793f974b4cbd70f93fa8d841",
    "universal_discovery_queries_v2.json": "8c96401f5fb62cf3a385ec0e0b1683a53dc29187958e67f857fe308e49c9d797",
    "universal_discovery_queries_v3.json": "e78c2897240a0b33735471c33760f58528ca4d0b9fcf903f4d01d5cd492934ff",
    "universal_discovery_effect_pairs.json": "85cad694d2991c66d9112be9091af247643b16ac8e9509b9963c40ff13d1854c",
}


def capture(output):
    """Run this stage in the server environment. All inputs come from the checkout."""
    sys.path[:0] = [str(ROOT / "Server/src"), str(ROOT)]
    import anyio

    from services.registry import semantic_coverage, semantic_encoder, task_search
    from services.registry.semantic_catalog import semantic_scores
    from services.registry.semantic_metric import document_fields, query_views
    from tools.experiments.discovery_evaluation_fixture import FIXTURE, load_suite
    from tools.experiments.universal_discovery_evaluation import evaluate_profile

    suites = [load_suite(FIXTURE.with_name(name)) for name in TRAINING]
    if [suite.sha256 for suite in suites] != list(TRAINING.values()):
        raise ValueError("training corpus digest mismatch")

    # Capture the same scoped declarations the public search tool receives from FastMCP.
    # Intercept before ranking so the new metric cannot teach itself from its own results.
    documents, operations = {}, {}

    def snapshot(_query, capabilities, resources, unavailable_parents, intent, surfaces):
        documents.update(
            task_search.semantic_documents(
                capabilities,
                resources,
                unavailable_parents,
                intent,
                surfaces=surfaces,
                operations=operations,
            )
        )

    with patch.object(task_search, "_semantic_catalog", snapshot):
        anyio.run(evaluate_profile, "core", replace(suites[0], requests=suites[0].requests[:1]))

    indices = {}

    def index(text):
        return indices.setdefault(text, len(indices))

    fields, bindings = [], []
    for key, descriptions in documents.items():
        label, purpose, examples, subject = document_fields(key, descriptions, operations.get(key))
        fields.append(
            [
                [index(text) for text in group]
                for group in ((label,), (purpose,), examples, (subject,))
            ]
        )
        bindings.append(".".join(value for value in key[1:] if value))

    queries, targets, baseline = [], [], []
    for suite in suites:
        for request in suite.requests:
            matches = [i for i, name in enumerate(bindings) if name in request.expected]
            if not matches:
                continue
            queries.append([index(text) for text in query_views(request.query)])
            targets.append(matches)
            _, scores = semantic_scores(request.query, documents)
            baseline.append([scores.get(key, 0.0) for key in documents])

    encoders = (
        lambda text: semantic_encoder._encode(text, semantic_encoder._model()),
        lambda text: semantic_coverage._encode(semantic_coverage._load(), text),
    )
    vectors = [[encode(text) or [0.0] * 256 for text in indices] for encode in encoders]
    with gzip.open(output, "wt", encoding="utf-8") as file:
        json.dump([fields, queries, targets, baseline, vectors], file, separators=(",", ":"))
    print(f"Captured {len(documents)} declarations and {len(queries)} eligible training requests")


def projections(np, query, document, target, baseline):
    # Optimize retrieval cross-entropy in the spans of the observed queries and declarations.
    # All declarations compete for each query, including close matches in the existing index.
    u, singular, vt = np.linalg.svd(query, full_matrices=False)
    v, values, wt = np.linalg.svd(document, full_matrices=False)
    x, y = u * singular, v * values
    weights = np.zeros((x.shape[1], y.shape[1]))
    start = 12 * query @ document.T + 5 * baseline
    for step in range(90):
        logits = start + x @ weights @ y.T
        prob = np.exp(logits - logits.max(axis=1, keepdims=True))
        prob /= prob.sum(axis=1, keepdims=True)
        gradient = x.T @ (prob - target) @ y / len(query) + 0.001 * weights
        weights -= 8 * gradient / (1 + step / 32) ** 0.5
    left, strength, right = np.linalg.svd(vt.T @ weights @ wt, full_matrices=False)
    # Each singular-vector pair has two equivalent signs. Pick one before quantization.
    right = right[:12]
    signs = np.sign(right[np.arange(len(right)), np.abs(right).argmax(axis=1)])[:, None]
    return (left[:, :12] * strength[:12]).T * signs, right * signs


def fit(source, output, *, check=False):
    """Only this offline maintainer stage needs NumPy for matrix factorization."""
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    np = import_module("numpy")
    with gzip.open(source, "rt", encoding="utf-8") as file:
        fields, queries, targets, baseline, encoders = json.load(file)
    baseline = np.asarray(baseline, dtype="float32")
    target = np.zeros((len(targets), len(fields)))
    for row, matches in enumerate(targets):
        target[row, matches] = 1 / len(matches)

    def unit(vector):
        return vector / max(np.linalg.norm(vector), 0.00001)

    artifact = bytearray(b"UMOPMET1")
    for vectors in encoders:
        vectors = np.asarray(vectors, dtype="float32")
        query = np.array([unit(vectors[raw] + vectors[masked]) for raw, masked in queries])
        document = np.array(
            [
                unit(
                    np.array([0.20, 0.30, 0.35, 0.15])
                    @ [unit(vectors[field].mean(0)) for field in groups]
                )
                for groups in fields
            ]
        )
        for matrix in projections(np, query, document, target, baseline):
            for row in matrix.tolist():
                scale = max(map(abs, row)) / 127 or 1.0
                artifact.extend(
                    struct.pack("<f256b", scale, *(round(value / scale) for value in row))
                )
    if check:
        if output.read_bytes() != artifact:
            raise SystemExit(f"Rebuilt metric differs from {output}")
    else:
        output.write_bytes(artifact)
    print(f"{len(artifact)} bytes, SHA-256 {sha256(artifact).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capture").add_argument("snapshot", type=Path)
    build = commands.add_parser("fit")
    build.add_argument("snapshot", type=Path)
    build.add_argument("output", type=Path)
    build.add_argument("--check", action="store_true", help="Compare without replacing the file.")
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.snapshot)
    else:
        fit(args.snapshot, args.output, check=args.check)
