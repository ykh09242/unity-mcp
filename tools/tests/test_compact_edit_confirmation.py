import base64
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.experiments.verify_agent_outcome_holdout import summary
from tools.experiments.verify_agent_outcome_holdout_confirmation import STRICT_ZERO
from tools.experiments.verify_compact_edit import (
    SKILL_ROOT,
    digest,
    host_error_records,
    read,
    skill_read_uri,
    verified_skill_read,
    verify_host_receipts,
)
from tools.experiments.verify_compact_edit_confirmation import host_tool_calls, verified_archives
from tools.experiments.verify_compact_edit_followup import call_outcomes, perfect

RESOURCE_TOOL_BINDINGS = [
    ("manage_tools", {"action": "read_resource"}),
    ("read_unity_resource", {}),
]


def test_extracted_product_must_match_frozen_archive(tmp_path):
    pins = {"package": "a" * 40, "runtime": "b" * 40}
    for key, dirname, filename in (
        ("package", "dependencies", "mcp.zip"),
        ("runtime", "product", "source.zip"),
    ):
        directory = tmp_path / dirname
        directory.mkdir()
        (directory / "fixture").write_bytes(b"frozen")
        with ZipFile(directory / filename, "w") as archive:
            archive.comment = pins[key].encode()
            archive.writestr("fixture", b"frozen")
    assert verified_archives(tmp_path, pins)["package"]["files"] == 1
    (tmp_path / "dependencies" / "fixture").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Extracted product differs"):
        verified_archives(tmp_path, pins)


def test_host_tool_denominator_counts_recovered_failures():
    raw = {"host_calls": [{"status": "failed"}, {"status": "completed"}]}
    assert host_tool_calls(raw) == {"failed": 1, "completed": 1}


@pytest.mark.parametrize("form", ["text", "blob"])
@pytest.mark.parametrize(("tool", "binding"), RESOURCE_TOOL_BINDINGS)
def test_generic_skill_tool_verifies_published_uri_and_exact_bytes(form, tool, binding):
    uri = SKILL_ROOT + "references/scene.md"
    body = "장면\r\n".encode()
    content = body.decode() if form == "text" else base64.b64encode(body).decode()
    call = {
        "name": tool,
        "arguments": {**binding, "uri": uri},
        "success": True,
        "result": {"contents": [{"uri": uri, form: content}]},
    }

    assert skill_read_uri(call) == uri
    assert verified_skill_read(call, {"references/scene.md": "sha256:" + digest(body)})
    with pytest.raises(ValueError, match="differs from the pinned packaged skill"):
        verified_skill_read(call, {"references/scene.md": "sha256:" + digest(b"other")})
    call["result"]["contents"][0]["uri"] = SKILL_ROOT + "other.md"
    with pytest.raises(ValueError, match="another file"):
        verified_skill_read(call, {"references/scene.md": "sha256:" + digest(body)})


@pytest.mark.parametrize("uri", [SKILL_ROOT + "unknown.md", "file:///unlisted/SKILL.md"])
@pytest.mark.parametrize(("tool", "binding"), RESOURCE_TOOL_BINDINGS)
def test_rejected_generic_skill_read_is_a_skill_attempt_and_a_failed_tool(uri, tool, binding):
    call = {
        "name": tool,
        "arguments": {**binding, "uri": uri},
        "success": False,
    }
    cases = [
        {
            "profile": "core",
            "verdict": "completed",
            "calls": [
                {"method": "tools/call", "reply": "failure", "rejection_reason": "unavailable"}
            ],
        }
    ]

    assert skill_read_uri(call) == uri
    assert not verified_skill_read(call, {})
    outcomes = call_outcomes(cases)["core"]
    assert outcomes["tools/call"]["failed"] == 1
    assert outcomes["resources/read"]["failed"] == 0


@pytest.mark.parametrize(
    "uri", ["mcpforunity://scene/gameobject/42/component/Canvas", "file:///notes.md", None, 42]
)
def test_generic_tool_read_of_a_non_skill_uri_is_not_a_skill_read(uri):
    call = {
        "name": "manage_tools",
        "arguments": {"action": "read_resource", "uri": uri},
        "success": True,
    }

    assert skill_read_uri(call) is None
    assert not verified_skill_read(call, {})


def test_connection_reader_without_a_selector_verifies_the_packaged_guide():
    uri = SKILL_ROOT + "SKILL.md"
    call = {
        "name": "read_unity_resource",
        "arguments": {},
        "success": True,
        "result": {"contents": [{"uri": uri, "text": "guide"}]},
    }
    assert skill_read_uri(call) == uri
    assert verified_skill_read(call, {"SKILL.md": "sha256:" + digest(b"guide")})
    call["success"] = False
    assert skill_read_uri(call) == uri
    assert not verified_skill_read(call, {})


def test_recovered_rejection_fails_perfection_gate():
    scores = dict.fromkeys(STRICT_ZERO, 0) | {"completed": 6, "scheduled": 6}
    totals = {
        panel: {profile: scores.copy() for profile in ("core", "full")} for panel in ("old", "new")
    }
    assert perfect(totals)
    totals["new"]["core"]["unsuccessful_public_replies"] = 1
    assert not perfect(totals)


@pytest.mark.parametrize(
    ("study", "include_primary", "failures"),
    [
        ("project_scope", False, {}),
        (
            "project_scope",
            True,
            {
                "full": {
                    "host_errors": 1,
                    "host_stderr_error_records": 1,
                    "host_router_errors_without_public_request": 1,
                },
            },
        ),
        (
            "initial_discovery",
            True,
            {
                "core": {
                    "unsuccessful_public_replies": 1,
                    "unsuccessful_resource_replies": 1,
                    "failed_skill_document_reads": 1,
                    "host_errors": 2,
                    "host_stderr_error_records": 1,
                },
                "full": {
                    "unsuccessful_public_replies": 2,
                    "unsuccessful_tool_replies": 2,
                    "host_errors": 2,
                },
            },
        ),
        ("preflight_policy", True, {}),
    ],
    ids=["supplement", "combined", "recovered-public-reads", "clean-combined-cohort"],
)
def test_strict_replay_distinguishes_host_and_public_failures(study, include_primary, failures):
    experiments = Path(__file__).resolve().parents[1] / "experiments"
    per_catalog = 13 if include_primary else 1
    replay = subprocess.run(
        [
            sys.executable,
            str(experiments / "verify_edit_language_supplement.py"),
            str(experiments / f"korean_{study}_confirmation_results.json"),
            "--require-perfect",
            *(["--include-primary"] if include_primary else []),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert replay.returncode == int(bool(failures)), replay.stderr or replay.stdout
    totals = json.loads(replay.stdout)
    assert set(totals) == {"core", "full"}
    for profile, counts in totals.items():
        assert counts["scheduled"] == counts["completed"] == per_catalog
        assert counts["native_edit_targets_reached"] == per_catalog
        assert counts["skill_document_reads"] > 0
        for counter in (
            "unsuccessful_public_replies",
            "missing_public_replies",
            "unsuccessful_resource_replies",
            "unsuccessful_tool_replies",
            "failed_skill_document_reads",
            "host_errors",
            "host_stderr_error_records",
            "host_router_errors_without_public_request",
        ):
            assert counts[counter] == failures.get(profile, {}).get(counter, 0)


@pytest.mark.parametrize("omitted_stream", ["events", "stderr"])
def test_host_error_verifier_rejects_omissions_even_when_error_totals_agree(
    tmp_path, omitted_stream
):
    attempt = tmp_path / "attempt-1"
    attempt.mkdir()
    (attempt / "host.events.jsonl").write_bytes(
        b'{"type":"item.completed","item":{"type":"mcp_tool_call","status":"failed"}}\n'
    )
    (attempt / "host.stderr").write_bytes(
        b"INFO starting\nERROR failed to parse function arguments\n"
    )
    evidence = host_error_records(attempt)
    assert [(item["stream"], item["class"], item["index"]) for item in evidence] == [
        ("events", "item.completed", 0),
        ("stderr", "host_router_without_public_request", 1),
    ]
    report = (
        Path(__file__).resolve().parents[1] / "experiments/compact_edit_confirmation_results.json"
    )
    panel = read(report)["runs"]["followup"]["panels"]["old"]
    row = next(
        item for item in panel["cases"] if item["task"] == "r03" and item["profile"] == "full"
    )
    row["host_errors"] = [item for item in evidence if item["stream"] != omitted_stream]
    panel["summary"] = summary(panel["cases"])
    with pytest.raises(ValueError, match="Host errors differ"):
        verify_host_receipts(row, tmp_path, tmp_path)


@pytest.mark.parametrize("present", ["host.events.jsonl", "host.stderr"])
def test_delivered_actor_requires_both_host_streams_even_without_errors(tmp_path, present):
    attempt = tmp_path / "attempt-1"
    attempt.mkdir()
    (attempt / present).write_bytes(b"")
    row = {"prompt_reached_model": True, "model_turns_started": 1, "host_errors": []}
    with pytest.raises(ValueError, match="missing its host event or stderr stream"):
        verify_host_receipts(row, tmp_path, tmp_path)
