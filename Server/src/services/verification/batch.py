"""Coalesce confirmed native source writes after a batch has returned."""

from services.verification.edits import complete_edit

_SOURCE_WRITES = {"create", "update", "edit", "apply_text_edits", "delete"}


async def complete_batch(ctx, instance, response, commands):
    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return response
    data = response["data"]
    if data.get("executionStatus") not in {"completed", "partial"}:
        return response
    results, outcomes = data.get("results"), data.get("outcomes")
    if not isinstance(results, list) or not isinstance(outcomes, list):
        return response
    expected = {}
    selected_indices = []
    excluded = 0
    for index, command in enumerate(commands):
        params = command.get("params", {})
        if command.get("tool") != "manage_script" or params.get("action") not in _SOURCE_WRITES:
            excluded += 1
            continue
        if index >= len(results) or index >= len(outcomes):
            excluded += 1
            continue
        item, outcome = results[index], outcomes[index]
        if (
            not isinstance(item, dict)
            or not isinstance(outcome, dict)
            or outcome.get("index") != index
            or item.get("tool") != command["tool"]
            or item.get("notExecuted") is True
        ):
            excluded += 1
            continue
        result = item.get("result")
        saved = result.get("data") if isinstance(result, dict) else None
        if (
            not isinstance(saved, dict)
            or saved.get("no_op") is True
            or saved.get("source_saved") is False
            or saved.get("applied") is False
        ):
            excluded += 1
            continue
        confirmed = saved.get("source_saved") is True or saved.get("applied") is True
        if not confirmed and (
            result.get("success") is not True or outcome.get("state") != "succeeded"
        ):
            excluded += 1
            continue
        path = saved.get("path") or saved.get("uri")
        if not isinstance(path, str) or not path.startswith("Assets/") or not path.endswith(".cs"):
            excluded += 1
            continue
        identity = {
            key: saved[key] for key in ("bytes_sha256", "sha256") if isinstance(saved.get(key), str)
        }
        if params["action"] == "delete":
            identity = {"absent": True}
        if not identity:
            excluded += 1
            continue
        expected[path] = identity
        selected_indices.append(index)
    data["batch_verification_selection"] = {
        "scope": "confirmed_script_writes",
        "selected_commands": len(selected_indices),
        "selected_command_indices": selected_indices[:64],
        "indices_truncated": len(selected_indices) > 64,
        "excluded_commands": excluded,
    }
    if not expected:
        data["verification"] = {
            "status": "incomplete",
            "scope": "confirmed_script_writes",
            "complete": False,
            "observed_values": [],
            "findings": [],
            "further_fixes_required": None,
            "reason": "no_confirmed_supported_source_writes",
        }
        return response
    paths = [path for path, identity in expected.items() if not identity.get("absent")]
    return await complete_edit(ctx, instance, response, paths, expected_sources=expected)
