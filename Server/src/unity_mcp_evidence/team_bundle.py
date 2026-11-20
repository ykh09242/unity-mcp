"""Pack and replay relocatable verification inputs without sharing workstation data."""

import hmac
from pathlib import Path

from .play_scenario_analysis_io import read_bytes, write_new
from .play_scenario_report_json import REPORT_LIMIT, parse_report
from .team_projection import (
    Aliases,
    choice,
    digest,
    encoded,
    fields,
    items,
    opaque,
    project_cohort,
    project_run,
    restore_cohort,
    restore_run,
)
from .verification_change import captured_run, compare_runs
from .verification_selection import read_cohort, selection_quality

_SCHEMA = "verification_team_bundle@1"
_ROLES = {"change": ("baseline", "candidate"), "selection": ("selected", "full", "fallback")}


def _slot(paths, withheld: bool, aliases: Aliases, analysis: str):
    if not paths:
        if withheld:
            raise ValueError("A withheld role needs a source to fingerprint")
        return {"availability": "absent", "original_sha256": {}}
    if withheld:
        names = ("verification", "native") if analysis == "change" else ("nunit",)
        return {
            "availability": "withheld",
            "original_sha256": {
                name: digest(read_bytes(path, REPORT_LIMIT))
                for name, path in zip(names, paths, strict=True)
            },
        }
    if analysis == "change":
        verification, native = (read_bytes(path, REPORT_LIMIT) for path in paths)
        originals = {"verification": digest(verification), "native": digest(native)}
        observed = captured_run(parse_report(verification), parse_report(native))
        projection = project_run(observed, aliases)
    else:
        observed = read_cohort(paths[0])
        originals = {"nunit": observed.digest}
        projection = project_cohort(observed, aliases)
    return {
        "availability": "included",
        "original_sha256": originals,
        "projection_sha256": digest(encoded(projection)),
        "projection": projection,
    }


def pack(analysis: str, inputs: dict, withheld, affected, destination: Path):
    roles = _ROLES[analysis]
    if set(inputs) != set(roles) or not set(withheld) <= set(roles) or not any(inputs.values()):
        raise ValueError("Supply at least one source and select only declared roles")
    aliases = Aliases()
    bundle = {
        "schema": _SCHEMA,
        "analysis": analysis,
        "roles": {role: _slot(inputs[role], role in withheld, aliases, analysis) for role in roles},
        "affected": sorted({aliases.token("test", name) for name in affected}),
    }
    content = encoded(bundle)
    if len(content) > REPORT_LIMIT:
        raise ValueError("Shared evidence exceeds the reader's 16 MiB limit")
    assessment = replay_document(bundle)
    write_new(destination, content)
    return {
        "schema": "verification_team_export@1",
        "bundle_sha256": digest(content),
        "analysis": analysis,
        "state": assessment["state"],
        "sources": assessment["sources"],
    }


def _unpack(role, slot, analysis):
    if not isinstance(slot, dict):
        raise ValueError("Invalid shared evidence role")
    available = choice(slot.get("availability"), {"included", "withheld", "absent"})
    fields(
        slot,
        {"availability", "original_sha256"}
        | ({"projection", "projection_sha256"} if available == "included" else set()),
    )
    required = {"native", "verification"} if analysis == "change" else {"nunit"}
    original = fields(slot["original_sha256"], required if available != "absent" else set())
    for value in original.values():
        opaque(value)
    provenance = {"availability": available, "original_sha256": original}
    if available != "included":
        return provenance, None
    projection = slot["projection"]
    actual = digest(encoded(projection))
    if not hmac.compare_digest(opaque(slot["projection_sha256"]), actual):
        raise ValueError(f"Shared {role} projection differs from its recorded digest")
    provenance["projection_sha256"] = actual
    restored = (
        restore_run(projection)
        if analysis == "change"
        else restore_cohort(projection, original["nunit"])
    )
    return provenance, restored


def replay_document(bundle: dict) -> dict:
    fields(bundle, ("schema", "analysis", "roles", "affected"))
    if bundle["schema"] != _SCHEMA:
        raise ValueError("Unsupported team evidence schema")
    analysis = choice(bundle["analysis"], _ROLES)
    roles = fields(bundle["roles"], _ROLES[analysis])
    affected = [opaque(item) for item in items(bundle["affected"])]
    if (analysis == "change" and affected) or len(affected) != len(set(affected)):
        raise ValueError("Invalid affected-test selection")
    inputs, sources = {}, {}
    for role in _ROLES[analysis]:
        sources[role], inputs[role] = _unpack(role, roles[role], analysis)
    missing = [
        role
        for role, value in inputs.items()
        if value is None and (role != "fallback" or sources[role]["availability"] == "withheld")
    ]
    result = {
        "schema": "verification_team_replay@1",
        "analysis": analysis,
        "sources": sources,
        "unavailable_roles": missing,
    }
    if analysis == "change" and not missing:
        verdict = compare_runs(inputs["baseline"], inputs["candidate"])
    elif analysis == "selection" and inputs["selected"] is not None and inputs["full"] is not None:
        verdict = selection_quality(
            inputs["selected"], inputs["full"], affected, inputs["fallback"]
        )
    else:
        verdict = None
    result["state"] = "incomplete" if missing else verdict["state"]
    result["assessment"] = verdict
    return result


def replay(source: Path, expected_digest: str):
    content = read_bytes(source, REPORT_LIMIT)
    if not hmac.compare_digest(opaque(expected_digest), digest(content)):
        raise ValueError("Shared bundle differs from its independently supplied SHA-256")
    result = replay_document(parse_report(content))
    result["bundle_sha256"] = expected_digest
    return result, {"measured": 0, "no_new_failures": 0, "regressed": 1}.get(result["state"], 2)
