"""Fit one shared operation metric using both catalog-wide and sibling negatives."""

import argparse
from dataclasses import replace
import gzip
from importlib import import_module
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.discovery_evaluation_fixture import FIXTURE, load_suite
from tools.experiments.fit_discovery_metric import TRAINING

SOURCES = {
    **TRAINING,
    "universal_discovery_read_scope.json": "e41a771653167454c49a1c4c03f85b149d6d7a2a3da0e2e21fb6b1104f1d0f75",
    "universal_discovery_edit_holdout.json": "386f463cf63b26cfecacec9bd05f3b0ae13f0e99f3a6ab1558c81c0138cea559",
    "universal_discovery_read_scope_confirmation.json": "db0940e98e3c88fe625f2d57fa59299bbfea205b638fb353f4038192363b70b7",
}
HELD_LANGUAGES = frozenset("da hu bg ms fa mr eu et".split())
HELD_FAMILIES = frozenset(("manage_addressables", "manage_localization", "manage_recording"))


def training_cards():
    import anyio
    from services.registry import task_search
    from tools.experiments import family_first_search as ranker
    from tools.experiments.discovery_family_split import binding_name, belongs_to
    from tools.experiments.universal_discovery_evaluation import evaluate_profile

    suites = [load_suite(FIXTURE.with_name(name)) for name in SOURCES]
    if [s.sha256 for s in suites] != list(SOURCES.values()):
        raise ValueError("Development corpus digest mismatch")
    requests = tuple(r for s in suites for r in s.requests)
    if any(
        r.language in HELD_LANGUAGES
        or any(
            belongs_to(k, family) for k in r.expected | r.wrong_effect for family in HELD_FAMILIES
        )
        for r in requests
    ):
        raise ValueError("Holdout present in development requests")
    cards = []

    def collect(query, capabilities, resources, unavailable, intent, surfaces):
        operations, effects = {}, {}
        catalog = task_search.semantic_documents(
            capabilities,
            resources,
            unavailable,
            intent,
            surfaces=surfaces,
            operations=operations,
            effects=effects,
        )
        held = {
            key for key in catalog if any(belongs_to(binding_name(key), f) for f in HELD_FAMILIES)
        }
        for group in task_search._EQUIVALENT_READS:
            if held.intersection(group.bindings()):
                held.update(group.bindings())
        for key in catalog:
            if key[0] != "tool" and any(
                f.removeprefix("manage_") in key[1].casefold() for f in HELD_FAMILIES
            ):
                held.add(key)
        visible = {k: v for k, v in catalog.items() if k not in held}
        cards.extend(ranker._cards(visible, operations, effects))
        if any(c.family in HELD_FAMILIES or held.intersection(c.bindings) for c in cards):
            raise ValueError("Held family in calibration catalog")

    with patch.object(task_search, "_semantic_catalog", collect):
        anyio.run(evaluate_profile, "core", replace(suites[0], requests=requests[:1]))
    return cards, requests


def capture(output):
    from tools.experiments import family_first_metric as metric
    from tools.experiments.discovery_family_split import binding_name

    cards, requests = training_cards()
    names = [{binding_name(key) for key in card.bindings} for card in cards]
    gold = [
        (
            r.query,
            [i for i, keys in enumerate(names) if keys & r.expected],
            [i for i, keys in enumerate(names) if keys & r.wrong_effect],
        )
        for r in requests
    ]
    examples = [(text, labels, opposites, 1.0) for text, labels, opposites in gold if labels]
    labelled_count = len(examples)
    # Catalog-authored examples spread the supervision across registered families.
    # Each operation supplies equal total mass even when it has many paraphrases.
    for index, card in enumerate(cards):
        samples = tuple(dict.fromkeys((card.purpose, *card.examples)))
        mass = labelled_count / (len(cards) * len(samples))
        examples.extend((text, [index], [], mass) for text in samples)
    families = list(dict.fromkeys(card.family for card in cards))
    encoders = []
    for i in (0, 1):
        queries = [
            metric.encode(text, i, document=False) or (0.0,) * metric.DIM for text, *_ in examples
        ]
        documents = [
            metric.contract_vector(card, metric.declaration(card, i)[0] or (0.0,) * metric.DIM)
            for card in cards
        ]
        encoders.append([queries, documents])
    with gzip.open(output, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "encoders": encoders,
                "families": [families.index(card.family) for card in cards],
                "labels": [labels for _, labels, _, _ in examples],
                "opposites": [opposites for _, _, opposites, _ in examples],
                "mass": [w for *_, w in examples],
            },
            stream,
            separators=(",", ":"),
        )


def fit(source, output):
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    from tools.experiments import family_first_metric as metric

    np = import_module("numpy")
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        data = json.load(stream)
    labels = data["labels"]
    families = np.asarray(data["families"])
    mass = np.asarray(data["mass"])
    mass /= mass.sum()
    target = np.zeros((len(labels), len(families)))
    for i, indices in enumerate(labels):
        target[i, indices] = 1 / len(indices)
    siblings = np.asarray([np.isin(families, families[indices]) for indices in labels])
    contrasts = siblings.copy()
    for i, opposites in enumerate(data["opposites"]):
        if opposites:
            contrasts[i] = False
            contrasts[i, labels[i] + opposites] = True
    result = bytearray(metric.HEADER)
    for queries, documents in data["encoders"]:
        q, d = np.asarray(queries), np.asarray(documents)
        initial = 12 * q @ d[:, : metric.DIM].T
        w = np.zeros((metric.DIM, d.shape[1]))
        for step in range(100):
            logits = initial + (q @ w) @ d.T
            global_logits = np.exp(logits - logits.max(axis=1, keepdims=True))
            joint = global_logits / global_logits.sum(axis=1, keepdims=True)
            conditional = np.where(siblings, global_logits, 0)
            conditional /= conditional.sum(axis=1, keepdims=True)
            paired = np.where(contrasts, global_logits, 0)
            paired /= paired.sum(axis=1, keepdims=True)
            error = (joint + conditional + paired) / 3 - target
            grad = q.T @ (error * mass[:, None]) @ d + 0.001 * w
            w -= 12 * grad / (1 + step / 32) ** 0.5
        u, values, vt = np.linalg.svd(w, full_matrices=False)
        v = vt[: metric.RANK]
        signs = np.sign(v[np.arange(len(v)), np.abs(v).argmax(axis=1)])[:, None]
        for bank, row_format in (
            ((u[:, : metric.RANK] * values[: metric.RANK]).T * signs, metric.QUERY_ROW),
            (v * signs, metric.DOC_ROW),
        ):
            for row in bank:
                scale = max(abs(row)) / 127 or 1.0
                result.extend(row_format.pack(scale, *(round(v / scale) for v in row)))
    output.write_bytes(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capture").add_argument("snapshot", type=Path)
    build = commands.add_parser("fit")
    build.add_argument("snapshot", type=Path)
    build.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.snapshot)
    else:
        fit(args.snapshot, args.output)
