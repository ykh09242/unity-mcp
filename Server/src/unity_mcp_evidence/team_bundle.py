"""Pack and replay relocatable verification inputs without sharing workstation data."""

import hmac
from pathlib import Path

from .play_scenario_analysis_io import AnalysisError, read_bytes, write_new
from .play_scenario_report_json import REPORT_LIMIT, parse_report
from .team_impact import (
    MAPPING_CHUNK_BYTES,
    MAX_MAPPING_CHUNKS,
    assess_impact,
    guid,
    limit_impact,
    project_inventory,
    project_mappings,
    project_selected,
    restore_impact,
)
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
from .team_recovery import assess_recovery, project_recovery, restore_recovery
from .verification_change import captured_run, compare_runs
from .verification_selection import read_cohort, selection_quality

_SCHEMA = "verification_team_bundle@1"
_INPUTS = {
    "change": dict.fromkeys(("baseline", "candidate"), ("verification", "native")),
    "selection": dict.fromkeys(("selected", "full", "fallback"), ("nunit",)),
    "impact": {
        "baseline": ("inventory",),
        "candidate": ("inventory",),
        "mappings": ("manifest",),
        "selected": ("nunit",),
    },
    "recovery": dict.fromkeys(("reconcile", "replan", "resumed"), ("receipt",)),
}


def _slot(paths, withheld: bool, aliases: Aliases, analysis: str, role: str, subject):
    if not paths:
        if withheld:
            raise ValueError("A withheld role needs a source to fingerprint")
        return {"availability": "absent", "original_sha256": {}}
    names = _INPUTS[analysis][role]
    if analysis == "impact" and role == "mappings":
        if len(paths) > MAX_MAPPING_CHUNKS + 1:
            raise ValueError("At most eight candidate manifest input chunks may be supplied")
        names = (*names, *(f"chunk_{index}" for index in range(1, len(paths))))
    if names == ("nunit",) and not withheld:
        observed = read_cohort(paths[0])
        originals = {"nunit": observed.digest}
        project = project_cohort if analysis == "selection" else project_selected
        projection = project(observed, aliases)
    else:
        raw = [
            read_bytes(path, MAPPING_CHUNK_BYTES if name.startswith("chunk_") else REPORT_LIMIT)
            for name, path in zip(names, paths, strict=True)
        ]
        originals = {name: digest(data) for name, data in zip(names, raw, strict=True)}
        if withheld:
            return {"availability": "withheld", "original_sha256": originals}
        if analysis == "change":
            observed = captured_run(*(parse_report(data) for data in raw))
            projection = project_run(observed, aliases)
        elif analysis == "recovery":
            projection = project_recovery(raw[0], role, aliases)
        elif role == "mappings":
            projection = project_mappings(raw, aliases)
        else:
            projection = project_inventory(
                raw[0], subject, aliases, include_route=role == "candidate"
            )
    return {
        "availability": "included",
        "original_sha256": originals,
        "projection_sha256": digest(encoded(projection)),
        "projection": projection,
    }


def pack(analysis: str, inputs: dict, withheld, affected, destination: Path, *, subject=None):
    roles = _INPUTS[analysis]
    if set(inputs) != set(roles) or not set(withheld) <= set(roles) or not any(inputs.values()):
        raise ValueError("Supply at least one source and select only declared roles")
    aliases = Aliases()
    bundle = {
        "schema": _SCHEMA,
        "analysis": analysis,
        "roles": {
            role: _slot(inputs[role], role in withheld, aliases, analysis, role, subject)
            for role in roles
        },
        "affected": sorted({aliases.token("test", name) for name in affected}),
    }
    if analysis == "impact":
        bundle["subject"] = aliases.token("guid", guid(subject))
        projections = {
            role: slot["projection"]
            for role, slot in bundle["roles"].items()
            if "projection" in slot
        }
        limit_impact(projections, bundle["subject"])
        for role, projection in projections.items():
            bundle["roles"][role]["projection_sha256"] = digest(encoded(projection))
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
        raise AnalysisError("Invalid shared evidence role")
    available = choice(slot.get("availability"), {"included", "withheld", "absent"})
    fields(
        slot,
        {"availability", "original_sha256"}
        | ({"projection", "projection_sha256"} if available == "included" else set()),
    )
    required = _INPUTS[analysis][role]
    optional = ()
    if analysis == "impact" and role == "mappings" and available != "absent":
        optional = tuple(f"chunk_{index}" for index in range(1, MAX_MAPPING_CHUNKS + 1))
    original = fields(slot["original_sha256"], required if available != "absent" else (), optional)
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
    if analysis == "change":
        restored = restore_run(projection)
    elif analysis == "selection":
        restored = restore_cohort(projection, original["nunit"])
    elif analysis == "impact":
        restored = restore_impact(role, projection)
    else:
        restored = restore_recovery(role, projection)
        if restored is None:
            provenance["observation"] = projection["evidence"]
    return provenance, restored


def replay_document(bundle: dict) -> dict:
    base = ("schema", "analysis", "roles", "affected")
    fields(bundle, base, ("subject",))
    if bundle["schema"] != _SCHEMA:
        raise ValueError("Unsupported team evidence schema")
    analysis = choice(bundle["analysis"], _INPUTS)
    fields(bundle, (*base, "subject") if analysis == "impact" else base)
    roles = fields(bundle["roles"], _INPUTS[analysis])
    affected = [opaque(item) for item in items(bundle["affected"])]
    if (analysis != "selection" and affected) or len(affected) != len(set(affected)):
        raise ValueError("Invalid affected-test selection")
    inputs, sources = {}, {}
    for role in _INPUTS[analysis]:
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
    elif analysis == "impact":
        verdict = assess_impact(inputs, opaque(bundle["subject"]))
    elif analysis == "recovery":
        verdict = assess_recovery(inputs)
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
    return result, {"measured": 0, "no_new_failures": 0, "regressed": 1, "reconstructed": 0}.get(
        result["state"], 2
    )
