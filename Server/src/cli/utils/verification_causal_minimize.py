"""Narrow verified repair groups using independent executions of explicitly composed inputs."""

import json
import os
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from cli.utils.verification_causal import TrialArchive, inventory, trial_manifest
from cli.utils.verification_change import same_json
from services.verification.input_chunks import CONFIG_PATHS, source_revision, validate_input_paths


def change_groups(broken, repaired, together):
    paths = set(broken) | set(repaired)
    validate_input_paths(paths)
    changed = sorted(p for p in paths if broken.get(p) != repaired.get(p))
    if not changed:
        raise ValueError("The endpoint inventories have no changed inputs")
    by_asset = {}
    for path in changed:
        asset = path.removesuffix(".meta")
        for endpoint in (broken, repaired):
            if asset not in CONFIG_PATHS and (asset in endpoint) != (asset + ".meta" in endpoint):
                raise ValueError(
                    f"Include both the changed asset and its .meta in each applicable endpoint: {asset}"
                )
        by_asset.setdefault(asset, []).append(path)
    if not isinstance(together, list) or len(together) > 128:
        raise ValueError("Expected a bounded list of atomic group merges")
    used = set()
    merged = {}
    for assets in together:
        if (
            not isinstance(assets, list)
            or len(assets) < 2
            or any(not isinstance(a, str) for a in assets)
        ):
            raise ValueError("Each atomic merge must name at least two changed assets")
        keys = set(assets)
        if len(keys) != len(assets) or not keys <= by_asset.keys() or keys & used:
            raise ValueError("Unknown or overlapping atomic groups")
        merged[min(keys)] = sorted(p for a in keys for p in by_asset[a])
        used.update(keys)
    groups = merged | {a: paths for a, paths in by_asset.items() if a not in used}
    if len(groups) > 128:
        raise ValueError("Causal minimization supports at most 128 atomic groups")
    return dict(sorted(groups.items()))


def mixed_inputs(broken, repaired, groups, selected):
    inputs = dict(broken)
    for group in selected:
        for path in groups[group]:
            if path in repaired:
                inputs[path] = repaired[path]
            else:
                del inputs[path]
    return inputs


def _setup(path):
    data, key = trial_manifest(path, "verification_change_trials@1", 512)
    broken, repaired = inventory(data.get("broken_inputs")), inventory(data.get("repaired_inputs"))
    groups = change_groups(broken, repaired, data.get("together", []))
    return data, key, broken, repaired, groups


def _selection(names, groups):
    if not isinstance(names, list) or any(not isinstance(g, str) or g not in groups for g in names):
        raise ValueError("Each trial must list exact group IDs from its input inventory")
    selected = frozenset(names)
    if len(selected) != len(names):
        raise ValueError("Duplicate group in trial")
    return selected


def _next_sets(best):
    # Probe partitions and complements, then each single removal.  A passing
    # subset starts another round, including when several changes must travel together.
    ordered = sorted(best)
    width = len(best) // 2
    while width > 1:
        for start in range(0, len(ordered), width):
            partition = frozenset(ordered[start : start + width])
            yield partition
            yield best - partition
        width //= 2
    for group in ordered:
        yield best - {group}
        yield frozenset([group])


def minimize_changes(path):
    data, key, broken, repaired, groups = _setup(path)
    archive = TrialArchive(path)
    observations = {}
    conditions = None
    receipts = []
    invalid = False
    for row in data["trials"]:
        selection = _selection(row.get("groups"), groups)
        inputs = mixed_inputs(broken, repaired, groups, selection)
        output = {"groups": sorted(selection), "source_revision": source_revision(inputs)}
        try:
            receipt, environment, oracle = archive.read(row, key, inputs)
            context = (environment, oracle)
            if conditions is not None and not same_json(conditions, context):
                raise ValueError("Execution conditions or oracle changed between subsets")
            conditions = context
            if receipt["state"] == "failed" and not receipt["signature"]:
                raise ValueError("This check lacks an observed failure signature")
            previous = observations.get(selection)
            observed = (receipt["state"], receipt["signature"])
            if previous is not None and not same_json(previous, observed):
                raise ValueError("Independent repeats of this subset disagree")
            observations[selection] = observed
            output.update(receipt)
        except (OSError, ValueError) as exc:
            invalid = True
            output.update(state="incomplete", reason=str(exc))
        receipts.append(output)
    result = {
        "schema": "verification_change_minimization@1",
        "check": data["check"],
        "groups": groups,
        "trials": receipts,
        "next_trials": [],
    }
    empty, full = frozenset(), frozenset(groups)

    def propose(selected):
        inputs = mixed_inputs(broken, repaired, groups, selected)
        result["next_trials"].append(
            {"groups": sorted(selected), "source_revision": source_revision(inputs)}
        )

    for endpoint in (empty, full):
        if endpoint not in observations:
            propose(endpoint)
    if invalid or result["next_trials"]:
        return {**result, "state": "incomplete"}, 2
    if observations[empty][0] != "failed" or observations[full][0] != "passed":
        return {**result, "state": "endpoints_not_reproduced"}, 2
    if any(
        state == "failed" and not same_json(signature, observations[empty][1])
        for state, signature in observations.values()
    ):
        return {**result, "state": "different_failure"}, 2
    passes = (s for s, (state, _) in observations.items() if state == "passed")
    best = min(passes, key=lambda s: (len(s), sorted(s)))
    result["smallest_observed_repair"] = sorted(best)
    if len(groups) <= 8:
        # Exhaust the smaller cardinalities, including subsets outside the current
        # best. This needs no monotonicity assumption when changes interact.
        for size in range(1, len(best)):
            for combo in combinations(groups, size):
                selection = frozenset(combo)
                if selection not in observations:
                    propose(selection)
                    if len(result["next_trials"]) == 16:
                        return {**result, "state": "needs_trials"}, 2
        if not result["next_trials"]:
            return {**result, "state": "minimum_cardinality"}, 0
    else:
        if all(best - {name} in observations for name in best):
            return {**result, "state": "one_minimal"}, 0
        seen = set(observations)
        for selection in _next_sets(best):
            if selection not in seen:
                propose(selection)
                seen.add(selection)
                if len(result["next_trials"]) == 16:
                    break
    return {**result, "state": "needs_trials"}, 2


def export_subset(path, selected, broken_root, repaired_root, output):
    _, _, broken, repaired, groups = _setup(path)
    selection = _selection(list(selected), groups)
    inputs = mixed_inputs(broken, repaired, groups, selection)
    repaired_paths = {p for group in selection for p in groups[group]}
    roots = [safe_local_path(root) for root in (broken_root, repaired_root)]
    if not all(root.is_dir() for root in roots):
        raise ValueError("Both source roots must be local snapshot directories")
    destination = safe_local_path(output)
    if any(destination.is_relative_to(root) for root in roots):
        raise ValueError("Write the source overlay outside both endpoint snapshots")
    total = 0
    metadata = {
        "schema": "verification_source_overlay@1",
        "groups": sorted(selection),
        "inputs": inputs,
        "source_revision": source_revision(inputs),
        "absent_paths": sorted((broken.keys() | repaired.keys()) - inputs.keys()),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=safe_local_path(destination.parent)) as stage:
        temporary = Path(stage) / "overlay.zip"
        with ZipFile(temporary, "x", compression=ZIP_DEFLATED) as bundle:
            for name, expected in sorted(inputs.items()):
                root = roots[1] if name in repaired_paths else roots[0]
                content = read_bytes(root / name, min(4 * 1024 * 1024, 32 * 1024 * 1024 - total))
                if sha256(content).hexdigest() != expected:
                    raise ValueError(f"Source snapshot differs from the declared inventory: {name}")
                total += len(content)
                bundle.writestr(name, content)
            bundle.writestr("overlay.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        os.link(safe_local_path(temporary), safe_local_path(destination))
    return {"state": "exported", "archive": str(destination), **metadata}, 0
