"""Join bounded declaration deltas to the existing checked reference graph."""

import base64
import hashlib
import hmac
import json

from services.script_quality.pages import encoded_size


def declaration_changes(before, after):
    unavailable = {"complete": False, "changes": [], "reason": "change_comparison_unavailable"}
    if (
        not before
        or any(
            item.get("complete") is not True
            or item.get("stale") is not False
            or item.get("declarations_complete") is not True
            for item in (before, after)
        )
        or any(before.get(key) != after.get(key) for key in ("project_id", "environment_signature"))
    ):
        return unavailable

    def files(result):
        values = {row["path"]: row.get("guid") or row["path"] for row in result["selected_files"]}
        if len(values) != len(result["selected_files"]) or len(set(values.values())) != len(values):
            raise ValueError()
        return values

    try:
        old_files, new_files = files(before), files(after)
        if set(old_files.values()) != set(new_files.values()):
            return unavailable
        current = after["declarations"]
        changes = []
        for old in before["declarations"]:
            if old["kind"] == "field" and not old.get("serialized"):
                continue
            candidates = [
                row
                for row in current
                if row["kind"] == old["kind"]
                and row["container"] == old["container"]
                and new_files[row["path"]] == old_files[old["path"]]
            ]
            same = [row for row in candidates if row["name"] == old["name"]]
            reason = None
            if old["kind"] == "field":
                if same:
                    if len(same) != 1:
                        return unavailable
                    if not same[0].get("serialized"):
                        reason = "field_no_longer_serialized"
                    elif same[0]["type"] != old["type"]:
                        reason = "serialized_type_changed"
                else:
                    aliases = [
                        row
                        for row in candidates
                        if row.get("serialized") and old["name"] in row.get("former_names", [])
                    ]
                    reason = (
                        "field_renamed_with_alias"
                        if len(aliases) == 1 and aliases[0]["type"] == old["type"]
                        else "field_removed_or_renamed"
                    )
            elif not any(row["signature"] == old["signature"] for row in same):
                reason = "method_signature_changed" if same else "method_removed_or_renamed"
            if reason:
                path = next(
                    path
                    for path, identity in new_files.items()
                    if identity == old_files[old["path"]]
                )
                changes.append({**old, "path": path, "change": reason})
        return {"complete": True, "changes": changes, "reason": None}
    except (KeyError, TypeError, ValueError):
        return unavailable


def _token(value, secret):
    payload = (
        base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    return payload + "." + hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def _decode(value, secret):
    try:
        payload, signature = value.split(".")
        if not hmac.compare_digest(
            signature, hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        ):
            raise ValueError()
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, TypeError):
        raise ValueError("invalid_impact_cursor") from None


async def inspect_script_impact(ctx, current, baseline, *, options, owner, secret, graph, native):
    if graph is None:
        raise ValueError("reference_graph_unavailable")
    delta = declaration_changes(baseline, current)
    view = hashlib.sha256(
        json.dumps(
            [
                owner,
                options["analysis_id"],
                options.get("baseline_id"),
                current["source_token"],
                baseline,
                delta,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    state = {"view": view, "file": 0, "cursor": None, "revision": None, "scope": None}
    if options.get("cursor"):
        state = _decode(options["cursor"], secret)
        if state.get("view") != view:
            raise ValueError("invalid_impact_cursor")
    files = current["selected_files"]
    if type(state.get("file")) is not int or not 0 <= state["file"] < len(files):
        raise ValueError("invalid_impact_cursor")
    file = files[state["file"]]
    gaps = [] if delta["complete"] else ["change_comparison_unavailable"]
    changes = [row for row in delta["changes"] if row["path"] == file["path"]]
    if len(changes) > 128:
        changes = changes[:128]
        gaps.append("member_limit")
    findings, graph_status, revision = [], {}, state["revision"]
    continuation = None
    if not file.get("guid"):
        gaps.append("script_guid_unavailable")
    else:
        query = {
            "node": "asset:" + file["guid"],
            "direction": "incoming",
            "limit": min(options.get("page_size", 100), 10),
        }
        if state["cursor"]:
            query["cursor"] = state["cursor"]
        else:
            query["consistency"] = "checked"
        response = await graph.execute(ctx, "query", query)
        if response.get("success") is not True:
            raise ValueError("reference_graph_unavailable")
        page = response["data"]
        revision, graph_status = page["revision"], page["graph_status"]
        if state["revision"] is not None and (
            revision != state["revision"] or state["scope"] != page["scope"]
        ):
            raise ValueError("graph_revision_changed")
        state.update(revision=revision, scope=page["scope"])
        if (
            not graph_status.get("current")
            or graph_status.get("admission_pending")
            or graph_status.get("catalog_pending")
        ):
            gaps.append("graph_coverage_incomplete")
        edges = page["edges"]
        if len(edges) > 10:
            raise ValueError("impact_page_limit")
        assets = sorted(
            {
                edge["source_path"]
                for edge in edges
                if isinstance(edge.get("source_path"), str)
                and edge["source_path"].startswith(("Assets/", "Packages/"))
            }
        )
        observation = await native(
            {
                "action": "impact",
                "script_path": file["path"],
                "script_guid": file["guid"],
                "source_hash": file["bytes_sha256"],
                "assets": assets,
                "members": [
                    {"name": row["name"], "kind": row["kind"], "container": row["container"]}
                    for row in changes
                ],
            }
        )
        data = observation.get("data", {})
        if observation.get("success") is not True:
            gaps.append("serialized_observation_unavailable")
        gaps.extend(data.get("coverage_gaps", []))
        observed = {row["path"]: row for row in data.get("assets", [])}
        for edge in edges:
            evidence = observed.get(edge.get("source_path"), {})
            members = evidence.get("members", [])
            findings.append(
                {
                    "edge_id": edge.get("id"),
                    "script_path": file["path"],
                    "asset_path": edge.get("source_path"),
                    "provider": edge.get("provider"),
                    "certainty": "confirmed_reference"
                    if any(row.get("certainty") == "confirmed_reference" for row in members)
                    else "potential_reference",
                    "changed_members": changes,
                    "member_evidence": members,
                    "observation": {
                        key: value for key, value in evidence.items() if key != "members"
                    },
                }
            )
        # Checked delivery after disk observation catches graph/import/policy changes during the join.
        check = await graph.execute(
            ctx,
            "query",
            {"node": query["node"], "direction": "incoming", "limit": 1, "consistency": "checked"},
        )
        if (
            check.get("success") is not True
            or check["data"].get("revision") != revision
            or check["data"].get("scope") != state["scope"]
        ):
            raise ValueError("graph_revision_changed")
        if check["data"].get("graph_status", {}).get("current") is not True:
            gaps.append("graph_coverage_incomplete")
        continuation = page.get("next_cursor")
    state["cursor"] = continuation
    if continuation is None:
        state["file"] += 1
    next_cursor = _token(state, secret) if state["file"] < len(files) else None
    result = {
        "schema": "script_impact@1",
        "analysis_id": options["analysis_id"],
        "baseline_id": options.get("baseline_id"),
        "source_token": current["source_token"],
        "graph_revision": revision,
        "graph_status": graph_status,
        "findings": findings,
        "changes": changes,
        "comparison_complete": delta["complete"],
        "complete": not gaps and next_cursor is None,
        "coverage_gaps": sorted(set(gaps)),
        "next_cursor": next_cursor,
        "limitations": [
            "Static references cannot prove safe deletion or migration.",
            "Reflection, custom serialization, nested prefab overrides and dynamic runtime calls are not fully discoverable.",
        ],
    }
    if encoded_size(result) > 500 * 1024 or next_cursor and len(next_cursor) > 4096:
        raise ValueError("impact_page_limit")
    return result
