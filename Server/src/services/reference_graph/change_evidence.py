"""Explain a candidate asset using explicitly selected source and catalog snapshots."""

import json

from services.reference_graph.analysis import (
    AnalysisIndex,
    Catalog,
    SerializedScanError,
    iter_serialized_references,
    parse_csharp,
)


def explain_asset_references(
    *, asset_guid, assets, scripts, serialized, previous_keys=(), contexts=None
):
    catalog = Catalog(assets)
    target = "asset:" + asset_guid.lower()
    facts = [
        parse_csharp(path, path, source, context=(contexts or {}).get(path))
        for path, source in sorted(scripts.items())
    ]
    index = AnalysisIndex(facts, catalog)
    findings = []
    coverage = {"selected_sources_only", "partial_static"}
    remaining_bytes = 32 * 1024

    def retain(finding):
        nonlocal remaining_bytes
        size = len(json.dumps(finding).encode()) + 1
        if len(findings) >= 128 or size > remaining_bytes:
            coverage.add("finding_limit")
            return
        remaining_bytes -= size
        findings.append(finding)

    targets, _ = catalog.resolve("guid", asset_guid)
    for path, text in sorted(serialized.items()):
        try:
            for ref in iter_serialized_references(text):
                if ref.guid == asset_guid.lower():
                    retain(
                        {
                            "kind": "serialized_guid",
                            "state": "target_guid_retained"
                            if target in targets
                            else "missing_from_catalog",
                            "path": path,
                            "line": ref.line,
                            "guid": ref.guid,
                            "file_id": ref.file_id,
                            "confidence": "possible",
                        }
                    )
        except SerializedScanError:
            coverage.add("serialized_scan_incomplete")
    keys = set(previous_keys)
    for path in sorted(scripts):
        result = index.analyze(path)
        for edge in result.edges:
            if edge.target == target:
                retain(
                    {
                        "kind": "loader_key",
                        "state": "catalog_match",
                        "path": path,
                        "line": edge.line,
                        "key": edge.key,
                        "scheme": edge.kind.removesuffix("_load"),
                        "confidence": edge.confidence,
                        "guid": asset_guid.lower(),
                    }
                )
        for issue in result.unresolved:
            if issue["reason"] == "dynamic_key":
                retain({"kind": "dynamic_key", "state": "unresolved", "path": path, **issue})
            elif issue.get("key") in keys:
                retain({"kind": "loader_key", "state": issue["reason"], "path": path, **issue})
            else:
                coverage.add(issue["reason"])
    return {"findings": findings, "coverage": sorted(coverage)}
