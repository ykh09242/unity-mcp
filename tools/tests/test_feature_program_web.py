"""Isolated HTTP/process checks for the local read-only dashboard."""

from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
from hashlib import sha256
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import feature_program_web as web  # noqa: E402
from tools.tests import test_feature_program as program_helpers  # noqa: E402

fp = web.feature_program


@contextmanager
def running(server):
    """Own a short-lived real HTTP service, including deterministic shutdown."""
    with server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()


def request(server, path="/api/snapshot", method="GET", headers=None):
    """Perform an actual loopback HTTP request and return its observable response."""
    with closing(HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)) as connection:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()


@pytest.fixture
def program(tmp_path, monkeypatch):
    """Create a small synthetic program without Git or private repository state."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "source.md").write_text(
        "# Synthetic work\n\n### A1. Import parser\nAcceptance: preserve wrapped prose.\n"
        "\n### B2. Review flow\nAcceptance: inspect evidence.\n",
        encoding="utf-8",
    )
    plan = {
        "schema_version": 1,
        "program_id": "synthetic-atlas",
        "baseline_head": "a" * 40,
        "workstreams": [
            {"id": "platform", "name": "Platform", "default_agent": "worker", "focus": "Tests"}
        ],
        "tasks": [
            {
                "id": task_id,
                "workstream": "platform",
                "phase": 1,
                "depends_on": [],
                "related_to": [],
                "conflict_groups": [],
                "candidate_paths": [f"src/{task_id}.py"],
                "required_checks": ["acceptance", "scoped_tests", "format_lint"],
                "next_action": "Inspect synthetic source",
                "planning_note": "Synthetic only",
            }
            for task_id in ["A1", "B2"]
        ],
    }
    (repo / "plan.json").write_bytes(fp.canonical(plan))
    monkeypatch.setattr(fp, "git", lambda *_args: "a" * 40)
    assert (
        fp.main(
            [
                "--repo",
                str(repo),
                "--program-dir",
                "program",
                "init",
                "--report",
                "source.md",
                "--planning",
                "plan.json",
                "--review-baseline",
                "b" * 40,
            ]
        )
        == 0
    )
    return repo


def fingerprints(repo):
    return {
        path.relative_to(repo).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in repo.rglob("*")
        if path.is_file()
    }


def delivery_fixture(repo, monkeypatch):
    """Record real synthetic task completion and two distinct delivery observations."""
    monkeypatch.setattr(program_helpers.fp, "git", lambda *_args: program_helpers.HEAD)
    fixture = program_helpers.Fixture(
        fp.Program(repo, repo / "program"), (repo / "source.md").read_bytes()
    )
    assert fixture.claim("A1") == 0
    fixture.submit("A1")
    assert fixture.run("complete", "A1", "--review", fixture.review("A1")) == 0
    task = fixture.program.task(fixture.program.load(), "A1")
    source_digest = task["attempts"][-1]["submission"]["source_digest"]
    for label, conclusion in (("pending", "pending"), ("cleaned", "success")):
        metadata = {
            "task_id": "A1",
            "attempt": 1,
            "branch": "synthetic-branch",
            "base": "beta",
            "commits": [program_helpers.HEAD],
            "pr_url": "https://github.com/example/fixture/pull/1",
            "tested_commit": program_helpers.HEAD,
            "reviewed_source_digest": source_digest,
            "required_ci": ["unit"],
            "ci": [
                {
                    "name": "unit",
                    "url": "https://github.com/example/fixture/actions/runs/1",
                    "commit": program_helpers.HEAD,
                    "conclusion": conclusion,
                }
            ],
            "merge_status": "open" if label == "pending" else "merged",
            "evidence": ["evidence/A1/attempt-1/inspection.txt"],
            "extension": {"authored": ["preserve", "한국어"]},
        }
        if label == "cleaned":
            metadata["merge_commit"] = "b" * 40
            metadata["cleanup"] = {
                "status": "cleaned",
                "reason": "Synthetic recorded cleanup",
                "merge_identity": "b" * 40,
                "actor_id": "leader",
                "recorded_at": "2026-10-13T09:00:00+00:00",
                "evidence": metadata["evidence"],
            }
        receipt = fixture.write(f"evidence/A1/attempt-1/delivery-{label}.json", metadata)
        assert fixture.run("delivery", "A1", "--receipt", receipt) == 0
    return fixture


def test_delivery_history_preserves_all_records_and_current_selection(program, monkeypatch):
    # Given two actual recorded observations, including a later cleanup fact.
    fixture = delivery_fixture(program, monkeypatch)
    state = fixture.program.load()
    expected = state["tasks"][0]["deliveries"]
    before = fingerprints(program)
    monkeypatch.setattr(fp, "git", lambda *_args: pytest.fail("Snapshot must not execute Git"))
    monkeypatch.setattr(fp.Program, "lock", lambda *_args: pytest.fail("No snapshot lock"))
    monkeypatch.setattr(fp, "atomic_write", lambda *_args: pytest.fail("No snapshot write"))
    # When the real HTTP adapter projects the program.
    with running(web.create_server(program, Path("program"), port=0)) as server:
        status, _headers, body = request(server)
    # Then every canonical observation and exact locator remains available.
    snapshot = json.loads(body)
    task = snapshot["tasks"][0]
    history = task["delivery_history"]
    assert status == 200 and history["status"] == "recorded"
    assert [item["record"] for item in history["items"]] == expected
    assert task["delivery"] == expected[-1]
    for index, item in enumerate(history["items"]):
        event_index, event = next(
            (i, event)
            for i, event in enumerate(state["events"])
            if event.get("action") == "delivery"
            and event.get("receipt") == expected[index]["receipt"]
        )
        assert item["reference"] == {
            "path": "backlog.json",
            "pointer": f"/tasks/0/deliveries/{index}",
            "revision": snapshot["revision"],
        }
        assert item["recorded_at"] == event["timestamp"]
        assert item["time_status"] == "recorded"
        assert item["event_reference"] == {
            "path": "backlog.json",
            "pointer": f"/events/{event_index}",
            "revision": snapshot["revision"],
        }
        assert item["record"]["remote_verified_by_tool"] is False
    assert snapshot["tasks"][1]["delivery_history"] == {"status": "unrecorded", "items": []}
    assert fingerprints(program) == before


@pytest.mark.parametrize(
    "fault",
    ["none", "missing", "wrong_task", "wrong_hash", "extra_ref", "duplicate", "bad_time", "naive"],
)
def test_delivery_history_time_requires_one_exact_valid_event(fault):
    # Given one frozen reference and a potentially ambiguous or unrelated event.
    receipt = {"path": "evidence/A1/attempt-1/delivery.json", "sha256": "a" * 64}
    event = {
        "action": "delivery",
        "id": "A1",
        "receipt": deepcopy(receipt),
        "timestamp": "2026-10-13T10:00:00Z",
    }
    events = [event]
    match fault:
        case "missing":
            events = []
        case "wrong_task":
            event["id"] = "B2"
        case "wrong_hash":
            event["receipt"]["sha256"] = "b" * 64
        case "extra_ref":
            event["receipt"]["extra"] = "different full reference"
        case "duplicate":
            events.append(deepcopy(event))
        case "bad_time":
            event["timestamp"] = "yesterday"
        case "naive":
            event["timestamp"] = "2026-10-13T10:00:00"
        case "none":
            pass
    delivery = {"receipt": receipt, "metadata": {"attempt": 1}, "recorded_at": "authored"}
    state = {"events": events, "tasks": [{"id": "A1", "deliveries": [delivery, delivery]}]}
    before = fp.canonical(state)
    # When detached history is projected, repeated records are not deduplicated.
    items = fp._delivery_history_projection(state, "c" * 64)[0]["items"]
    # Then only an exact unique event yields time, and originals remain detached.
    assert len(items) == 2
    assert [item["record"] for item in items] == [delivery, delivery]
    for item in items:
        assert item["time_status"] == ("recorded" if fault == "none" else "unknown")
        assert item["recorded_at"] == (event["timestamp"] if fault == "none" else None)
        assert (item["event_reference"] is not None) == (fault == "none")
    items[0]["record"]["metadata"]["attempt"] = 99
    assert items[1]["record"]["metadata"]["attempt"] == 1
    assert fp.canonical(state) == before


@pytest.mark.parametrize("value", [None, {}, "", "not-a-date", 17])
def test_delivery_history_tolerates_malformed_optional_time(value):
    # Given a valid identity with legacy timestamp metadata of an unsupported shape.
    receipt = {"path": "evidence/A1/attempt-1/delivery.json", "sha256": "a" * 64}
    state = {
        "tasks": [{"id": "A1", "deliveries": [{"receipt": receipt, "metadata": {}}]}],
        "events": [{"action": "delivery", "id": "A1", "receipt": receipt, "timestamp": value}],
    }
    # When the recorded history is observed.
    item = fp._delivery_history_projection(state, "b" * 64)[0]["items"][0]
    # Then timestamp absence does not become a current time or an event claim.
    assert item["time_status"] == "unknown"
    assert item["recorded_at"] is None and item["event_reference"] is None


def test_delivery_history_retains_prior_attempt_when_current_is_active(program, monkeypatch):
    # Given a completed attempt with deliveries followed by a fresh active claim.
    fixture = delivery_fixture(program, monkeypatch)
    deliveries = deepcopy(fixture.program.load()["tasks"][0]["deliveries"])
    assert (
        fixture.run(
            "reopen", "A1", "--owner", "leader", "--reason", "Follow-up", "--next-action", "Inspect"
        )
        == 0
    )
    assert fixture.claim("A1", number=2) == 0
    # When the current snapshot is read.
    task = fp.read_snapshot(program, Path("program"))["tasks"][0]
    # Then old delivery observations remain history, not the new attempt's delivery.
    assert task["status"] == "active" and len(task["attempts"]) == 2
    assert task["delivery"] == {"status": "unrecorded", "remote_verified_by_tool": False}
    assert [item["record"] for item in task["delivery_history"]["items"]] == deliveries


@pytest.mark.parametrize("cleanup", [None, "unknown", "deferred"])
@pytest.mark.parametrize("conclusion", ["failure", "pending", "skipped", "neutral", None])
def test_delivery_history_preserves_legacy_optional_facts(program, cleanup, conclusion):
    # Given tolerated legacy metadata, without inventing a valid modern delivery receipt.
    path = program / "program/backlog.json"
    state = fp.read_json(path)
    metadata = {"ci": [{"conclusion": conclusion}], "cleanup": None}
    if cleanup is not None:
        # Cleanup validation is authoritative; exercise these complete forms in pure projection.
        metadata["cleanup"] = {"status": cleanup}
    delivery = {"metadata": metadata, "status": "unknown", "remote_verified_by_tool": False}
    state["tasks"][0]["deliveries"] = [delivery]
    # When the pure projection retains supplied optional facts.
    item = fp._delivery_history_projection(state, "c" * 64)[0]["items"][0]
    # Then missing/neutral/skipped/failed facts and explicit cleanup remain unmodified.
    assert item["record"] == delivery
    assert item["recorded_at"] is None and item["time_status"] == "unknown"


@pytest.mark.parametrize("deliveries", [None, {}, [None], [{"metadata": None}]])
def test_delivery_history_keeps_existing_structural_rejection(program, deliveries):
    # Given a registry shape already rejected by the authoritative validator.
    path = program / "program/backlog.json"
    state = fp.read_json(path)
    state["tasks"][0]["deliveries"] = deliveries
    path.write_bytes(fp.canonical(state))
    # When snapshot validation runs, no partial successful history is returned.
    with pytest.raises(fp.ProgramError):
        fp.read_snapshot(program, Path("program"))


@pytest.mark.parametrize("drift", ["changed", "missing"])
def test_delivery_history_preserves_context_with_frozen_evidence_drift(program, monkeypatch, drift):
    # Given real delivery records whose earlier frozen receipt has drifted.
    fixture = delivery_fixture(program, monkeypatch)
    state = fixture.program.load()
    expected = state["tasks"][0]["deliveries"]
    path = fixture.program.path(expected[0]["receipt"]["path"])
    if drift == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed frozen evidence")
    before = fingerprints(program)
    # When the snapshot observes integrity without hydrating another receipt.
    snapshot = fp.read_snapshot(program, Path("program"))
    # Then history remains supplied context and integrity remains visibly stale.
    assert snapshot["integrity"]["status"] == "stale"
    assert [x["record"] for x in snapshot["tasks"][0]["delivery_history"]["items"]] == expected
    assert any(expected[0]["receipt"]["path"] in w for w in snapshot["integrity"]["warnings"])
    assert fingerprints(program) == before


def test_delivery_history_rejects_registry_change_during_projection(program, monkeypatch):
    # Given an actual snapshot whose captured registry changes during history projection.
    project = fp._delivery_history_projection
    path = program / "program/backlog.json"

    def changed(state, revision):
        result = project(state, revision)
        updated = fp.read_json(path)
        updated["tasks"][0]["next_action"] = "Changed concurrently"
        path.write_bytes(fp.canonical(updated))
        return result

    monkeypatch.setattr(fp, "_delivery_history_projection", changed)
    # When the final existing registry guards run, the read rejects rather than mixing revisions.
    with pytest.raises(fp.ProgramError, match="Registry changed"):
        fp.read_snapshot(program, Path("program"))


def test_delivery_history_rejects_complete_output_over_existing_bound(program, monkeypatch):
    # Given a registry below its bound whose current/history duplication exceeds output budget.
    fixture = delivery_fixture(program, monkeypatch)
    path = fixture.program.path("backlog.json")
    state = fp.read_json(path)
    state["tasks"][0]["deliveries"][-1]["metadata"]["large_extension"] = "x" * (4 * 1024 * 1024)
    path.write_bytes(fp.canonical(state))
    assert path.stat().st_size < 8 * 1024 * 1024
    # When the full snapshot is built, history is not silently dropped or truncated.
    with pytest.raises(fp.ProgramError, match="Snapshot output exceeds 8 MiB"):
        fp.read_snapshot(program, Path("program"))


def test_delivery_history_http_refresh_retains_old_facts_and_changes_validator(
    program, monkeypatch
):
    # Given a current snapshot and its unchanged validator.
    fixture = delivery_fixture(program, monkeypatch)
    path = fixture.program.path("backlog.json")
    with running(web.create_server(program, Path("program"), port=0)) as server:
        first = request(server)
        original = json.loads(first[2])["tasks"][0]["delivery_history"]["items"]
        assert request(server, headers={"If-None-Match": first[1]["ETag"]})[0] == 304
        state = fp.read_json(path)
        state["tasks"][0]["deliveries"][0]["metadata"]["historical_note"] = "Recorded detail"
        path.write_bytes(fp.canonical(state))
        # When only an older recorded observation changes, refresh returns the new full history.
        status, headers, body = request(server, headers={"If-None-Match": first[1]["ETag"]})
    # Then current delivery remains unchanged while historical data changes the response identity.
    changed = json.loads(body)
    assert status == 200 and headers["ETag"] != first[1]["ETag"]
    assert changed["tasks"][0]["delivery"] == json.loads(first[2])["tasks"][0]["delivery"]
    assert (
        changed["tasks"][0]["delivery_history"]["items"][0]["record"]["metadata"]["historical_note"]
        == "Recorded detail"
    )
    assert original[0]["record"]["metadata"].get("historical_note") is None


def test_snapshot_reads_real_program_without_any_mutation(program, monkeypatch):
    before = fingerprints(program)
    monkeypatch.setattr(fp, "git", lambda *_args: pytest.fail("Snapshot must not execute Git"))
    monkeypatch.setattr(
        fp.Program, "lock", lambda *_args: pytest.fail("Snapshot must not acquire locks")
    )
    with running(web.create_server(program, Path("program"), port=0)) as server:
        status, headers, body = request(server)
    data = json.loads(body)
    assert status == 200
    assert data["snapshot_version"] == 1
    assert data["program_id"] == "synthetic-atlas"
    assert len(data["tasks"]) == 2
    assert "preserve wrapped prose" in data["tasks"][0]["source"]["text"]
    assert headers["Cache-Control"] == "no-store"
    assert fingerprints(program) == before


@pytest.mark.parametrize(
    "path",
    [
        "/../backlog.json",
        "/%2e%2e/backlog.json",
        "/api/snapshot?path=source.md",
        "/evidence/A1/file.txt",
        "/DESIGN.md",
        "/favicon.ico",
        "//api/snapshot",
        "/app.js/extra",
        "/style.css?x=1",
        "/.env",
        "/%61pi/snapshot",
    ],
)
def test_unlisted_routes_cannot_read_files(path):
    with running(
        web.DashboardServer("127.0.0.1", 0, lambda: pytest.fail("Must not read snapshot"))
    ) as server:
        status, _headers, _body = request(server, path)
    assert status == 404


@pytest.mark.parametrize(
    "method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"]
)
def test_methods_cannot_write_or_read_snapshot(method):
    with running(
        web.DashboardServer("127.0.0.1", 0, lambda: pytest.fail("Must not read snapshot"))
    ) as server:
        status, _headers, body = request(server, method=method)
    assert status == 405
    assert b"Read-only" in body


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example:8765"},
        {"Origin": "https://attacker.example"},
        {"Origin": "null"},
        {"Referer": "http://localhost:1/"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_cross_origin_and_dns_rebinding_requests_are_denied(headers):
    with running(
        web.DashboardServer("127.0.0.1", 0, lambda: pytest.fail("Must not read snapshot"))
    ) as server:
        status, response_headers, _body = request(server, headers=headers)
    assert status == 403
    assert "Access-Control-Allow-Origin" not in response_headers


def test_same_origin_requests_are_allowed_and_hardened():
    with running(web.DashboardServer("127.0.0.1", 0, lambda: {"tasks": []})) as server:
        status, headers, body = request(
            server, headers={"Origin": server.url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
        )
    assert status == 200
    assert json.loads(body) == {"tasks": []}
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_duplicate_host_header_is_denied():
    with running(
        web.DashboardServer("127.0.0.1", 0, lambda: pytest.fail("Must not read snapshot"))
    ) as server:
        with closing(
            HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        ) as connection:
            connection.putrequest("GET", "/api/snapshot")
            connection.putheader("Host", "attacker.example")
            connection.endheaders()
            response = connection.getresponse()
            status = response.status
            response.read()
    assert status == 403


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.0.1", "example.org", "127.0.0.2"])
def test_remote_binding_is_rejected(host):
    with pytest.raises(ValueError, match="loopback"):
        web.DashboardServer(host, 0, lambda: {})


def test_snapshot_errors_do_not_disclose_private_paths():
    def unavailable():
        raise OSError("private/path/credentials.txt")

    with running(web.DashboardServer("127.0.0.1", 0, unavailable)) as server:
        status, _headers, body = request(server)
    assert status == 503
    assert b"private" not in body


@pytest.mark.parametrize("route", ["/", "/app.js", "/style.css"])
def test_packaged_assets_and_head_are_available(route):
    with running(
        web.DashboardServer("127.0.0.1", 0, lambda: pytest.fail("Must not read snapshot"))
    ) as server:
        status, headers, body = request(server, route)
        head_status, head_headers, head_body = request(server, route, method="HEAD")
    assert status == head_status == 200
    assert body
    assert not head_body
    assert headers["Content-Length"] == head_headers["Content-Length"]


def test_http_refresh_observes_latest_files_and_recovers_from_errors(tmp_path):
    path = tmp_path / "record.json"
    path.write_text('{"revision":"one"}', encoding="utf-8")
    with running(
        web.DashboardServer("127.0.0.1", 0, lambda: json.loads(path.read_text()))
    ) as server:
        first = json.loads(request(server)[2])
        path.write_text("invalid", encoding="utf-8")
        failed = request(server)[0]
        path.write_text('{"revision":"two"}', encoding="utf-8")
        second = json.loads(request(server)[2])
    assert first["revision"] == "one"
    assert failed == 503
    assert second["revision"] == "two"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_conditional_snapshot_is_bodyless_and_revalidates_every_request(method):
    calls = []

    def snapshot():
        calls.append(len(calls))
        return {"revision": "same", "generated_at": str(len(calls)), "tasks": []}

    with running(web.DashboardServer("127.0.0.1", 0, snapshot)) as server:
        first, first_headers, first_body = request(server)
        status, headers, body = request(
            server, method=method, headers={"If-None-Match": first_headers["ETag"]}
        )
    assert first == 200 and first_body
    assert status == 304 and body == b""
    assert len(calls) == 2
    assert headers["ETag"] == first_headers["ETag"]
    assert headers["Cache-Control"] == "no-store"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "Content-Length" not in headers and "Content-Type" not in headers


def test_semantic_validator_covers_future_fields_and_nested_generation_times():
    first = {"generated_at": "one", "future": {"generated_at": "semantic", "value": 1}}
    same = {"future": {"value": 1, "generated_at": "semantic"}, "generated_at": "two"}
    assert web.snapshot_etag(first) == web.snapshot_etag(same)
    assert first["generated_at"] == "one"
    for changed in [
        {"future": {"generated_at": "changed", "value": 1}},
        {"future": {"generated_at": "semantic", "value": 2}},
        {**first, "new_field": []},
        {**first, "integrity": {"status": "stale", "warnings": ["Source drift"]}},
    ]:
        assert web.snapshot_etag(first) != web.snapshot_etag(changed)
    assert web.snapshot_etag({"items": [1, 2]}) != web.snapshot_etag({"items": [2, 1]})


@pytest.mark.parametrize(
    "validator,expected",
    [
        (lambda tag: tag, 304),
        (lambda tag: tag.removeprefix("W/"), 304),
        (lambda tag: f'"other,tag", {tag}', 304),
        (lambda tag: f" , {tag}, , ", 304),
        (lambda _tag: "*", 304),
        (lambda _tag: 'W/"different"', 200),
        (lambda _tag: "unquoted", 400),
        (lambda _tag: "", 200),
        (lambda _tag: " \t ", 200),
        (lambda _tag: ", ,", 200),
        (lambda tag: f"{tag}, invalid", 400),
        (lambda tag: f"{tag} {tag}", 400),
        (lambda tag: f"*, {tag}", 400),
        (lambda tag: tag.replace("W/", "w/"), 400),
        (lambda _tag: '"unterminated', 400),
        (lambda _tag: '"contains space"', 400),
        (lambda tag: f'{tag}, "' + "x" * web.MAX_VALIDATOR_BYTES + '"', 431),
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_conditional_validator_inputs_have_predictable_responses(validator, expected, method):
    calls = []

    def snapshot():
        calls.append(True)
        return {"tasks": []}

    with running(web.DashboardServer("127.0.0.1", 0, snapshot)) as server:
        tag = request(server)[1]["ETag"]
        status, headers, body = request(
            server, method=method, headers={"If-None-Match": validator(tag)}
        )
    assert status == expected
    assert len(calls) == 2
    if expected >= 400:
        assert "ETag" not in headers
    if method == "HEAD" or expected == 304:
        assert body == b""


def test_repeated_validator_headers_combine_as_one_list():
    with running(web.DashboardServer("127.0.0.1", 0, lambda: {"tasks": []})) as server:
        tag = request(server)[1]["ETag"]
        with closing(HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)) as conn:
            conn.putrequest("GET", "/api/snapshot")
            conn.putheader("If-None-Match", '"different"')
            conn.putheader("If-None-Match", tag)
            conn.endheaders()
            response = conn.getresponse()
            assert response.status == 304 and response.read() == b""


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("validator", ["*", "malformed", "x" * 9000])
def test_snapshot_failure_takes_priority_and_never_reuses_success(method, validator):
    current = {"tasks": []}

    def snapshot():
        if current is None:
            raise fp.ProgramError("private/path/failed.json")
        return current

    with running(web.DashboardServer("127.0.0.1", 0, snapshot)) as server:
        first_tag = request(server)[1]["ETag"]
        current = None
        for value in [first_tag, validator]:
            status, headers, body = request(server, method=method, headers={"If-None-Match": value})
            assert status == 503 and "ETag" not in headers
            assert b"private" not in body
        current = {"tasks": []}
        assert request(server, headers={"If-None-Match": first_tag})[0] == 304


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_conditional_snapshot_enforces_payload_bound_even_for_excluded_time(monkeypatch, method):
    monkeypatch.setattr(web, "MAX_RESPONSE_BYTES", 100)
    current = {"tasks": []}
    with running(web.DashboardServer("127.0.0.1", 0, lambda: current)) as server:
        tag = request(server)[1]["ETag"]
        current["generated_at"] = "x" * 101
        status, headers, body = request(server, method=method, headers={"If-None-Match": tag})
    assert status == 503 and "ETag" not in headers
    if method == "HEAD":
        assert body == b""


def test_conditional_real_snapshot_observes_source_projection_and_registry_drift(program):
    before = fingerprints(program)
    with running(web.create_server(program, Path("program"), port=0)) as server:
        initial = request(server)
        tag = initial[1]["ETag"]
        revision = json.loads(initial[2])["revision"]
        for path in [program / "source.md", program / "program" / "CHECKPOINT.md"]:
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"\nSynthetic independent drift\n")
                status, headers, body = request(server, headers={"If-None-Match": tag})
                data = json.loads(body)
                assert status == 200 and headers["ETag"] != tag
                assert data["revision"] == revision
                assert data["integrity"]["status"] == "stale"
            finally:
                path.write_bytes(original)
            assert request(server, headers={"If-None-Match": tag})[0] == 304
        backlog = program / "program" / "backlog.json"
        original = backlog.read_bytes()
        try:
            state = json.loads(original)
            state["tasks"][0]["next_action"] = "Synthetic changed registry"
            backlog.write_bytes(fp.canonical(state))
            status, headers, body = request(server, headers={"If-None-Match": tag})
            assert status == 200 and headers["ETag"] != tag
            assert json.loads(body)["revision"] != revision
        finally:
            backlog.write_bytes(original)
    assert fingerprints(program) == before


def test_snapshot_head_preserves_current_representation_length():
    with running(web.DashboardServer("127.0.0.1", 0, lambda: {"tasks": [], "value": 1})) as server:
        get_status, get_headers, get_body = request(server)
        status, headers, body = request(server, method="HEAD")
    assert status == get_status == 200 and body == b""
    assert headers["ETag"] == get_headers["ETag"]
    assert int(headers["Content-Length"]) == len(get_body)


def test_conditional_real_snapshot_rehashes_evidence_and_restores_validator(program, monkeypatch):
    monkeypatch.setattr(program_helpers.fp, "git", lambda *_args: program_helpers.HEAD)
    fixture = program_helpers.Fixture(
        fp.Program(program, program / "program"), (program / "source.md").read_bytes()
    )
    assert fixture.claim("A1") == 0
    path = fixture.program.path("evidence/A1/attempt-1/inspection.txt")
    original = path.read_bytes()
    with running(web.create_server(program, Path("program"), port=0)) as server:
        initial = request(server)
        tag = initial[1]["ETag"]
        revision = json.loads(initial[2])["revision"]
        try:
            path.write_bytes(original + b"Synthetic changed evidence\n")
            status, headers, body = request(server, headers={"If-None-Match": tag})
            data = json.loads(body)
            assert status == 200 and headers["ETag"] != tag
            assert data["revision"] == revision
            assert data["integrity"]["status"] == "stale"
            assert any("Frozen evidence changed" in text for text in data["integrity"]["warnings"])
        finally:
            path.write_bytes(original)
        assert request(server, headers={"If-None-Match": tag})[0] == 304


def test_real_process_launch_is_loopback_read_only(program):
    before = fingerprints(program)
    command = [
        sys.executable,
        str(TOOLS / "feature_program_web.py"),
        "--repo",
        str(program),
        "--program-dir",
        "program",
        "--port",
        "0",
    ]
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        try:
            assert process.stdout is not None
            line = process.stdout.readline().strip()
            url = line.removeprefix("Local work-program dashboard: ")
            assert url.startswith("http://127.0.0.1:")
            port = int(url.split(":")[2].rstrip("/"))
            with closing(HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
                connection.request("GET", "/api/snapshot")
                response = connection.getresponse()
                assert response.status == 200
                assert json.loads(response.read())["program_id"] == "synthetic-atlas"
        finally:
            process.terminate()
            process.wait(timeout=5)
    assert fingerprints(program) == before


def post_answer(
    server, payload=None, extra_headers=None, raw_body=None, route="/api/questions/answer"
):
    """Send a real same-origin response with the scoped session token."""
    body = (
        raw_body
        if raw_body is not None
        else json.dumps(
            payload
            or {
                "question_id": "Q1",
                "version": 1,
                "answer": "Use scoped verification",
                "submission_id": "synthetic-one",
            }
        ).encode()
    )
    headers = {
        "Origin": server.url.rstrip("/"),
        "Content-Type": "application/json",
        "X-Work-Program-Nonce": server.nonce,
    }
    headers.update(extra_headers or {})
    with closing(HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)) as connection:
        connection.request("POST", route, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()


def test_same_origin_nonce_session_exposes_only_configured_response_capability():
    with running(web.DashboardServer("127.0.0.1", 0, lambda: {})) as server:
        disabled = json.loads(request(server, "/api/session")[2])
    calls = []
    with running(
        web.DashboardServer(
            "127.0.0.1", 0, lambda: {}, lambda *args: calls.append(args) or {"status": "answered"}
        )
    ) as server:
        enabled = json.loads(request(server, "/api/session")[2])
        code, headers, body = post_answer(server)
    assert disabled == {"nonce": None, "responses_enabled": False}
    assert enabled["responses_enabled"] is True
    assert len(enabled["nonce"]) >= 32
    assert code == 200
    assert json.loads(body)["status"] == "answered"
    assert calls == [("Q1", 1, "Use scoped verification", "synthetic-one")]
    assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "null"},
        {"Origin": "http://attacker.example"},
        {"Origin": ""},
        {"X-Work-Program-Nonce": ""},
        {"X-Work-Program-Nonce": "wrong"},
        {"X-Work-Program-Nonce": "\u00e9"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_answer_requires_exact_origin_and_nonce_before_provider_call(headers):
    with running(
        web.DashboardServer(
            "127.0.0.1", 0, lambda: {}, lambda *_args: pytest.fail("Must not save response")
        )
    ) as server:
        code, _headers, _body = post_answer(server, extra_headers=headers)
    assert code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question_id": "Q1", "version": True, "answer": "Yes", "submission_id": "one"},
        {"question_id": "Q1", "version": 0, "answer": "Yes", "submission_id": "one"},
        {"question_id": "Q1", "version": 1, "answer": " ", "submission_id": "one"},
        {"question_id": "Q1", "version": 1, "answer": "a" * 4001, "submission_id": "one"},
        {
            "question_id": "Q1",
            "version": 1,
            "answer": "Yes",
            "submission_id": "one",
            "status": "done",
        },
    ],
)
def test_answer_rejects_invalid_or_extra_fields(payload):
    with running(
        web.DashboardServer(
            "127.0.0.1", 0, lambda: {}, lambda *_args: pytest.fail("Must not save response")
        )
    ) as server:
        code, _headers, _body = post_answer(server, raw_body=json.dumps(payload).encode())
    assert code == 400


@pytest.mark.parametrize(
    "headers,body,expected",
    [
        ({"Content-Type": "text/plain"}, b"{}", 415),
        ({"Content-Length": "40000"}, b"{}", 413),
        ({"Transfer-Encoding": "chunked"}, b"{}", 411),
        ({}, b'{"version":1,"version":2}', 400),
    ],
)
def test_answer_bounds_transport_and_duplicate_keys(headers, body, expected):
    with running(
        web.DashboardServer(
            "127.0.0.1", 0, lambda: {}, lambda *_args: pytest.fail("Must not save response")
        )
    ) as server:
        code, _headers, _body = post_answer(server, extra_headers=headers, raw_body=body)
    assert code == expected


@pytest.mark.parametrize(
    "route",
    [
        "/api/questions/answer?extra=1",
        "//api/questions/answer",
        "/api/status",
        "/api/questions/new",
    ],
)
def test_enabled_responses_do_not_authorize_other_mutation_routes(route):
    with running(
        web.DashboardServer(
            "127.0.0.1", 0, lambda: {}, lambda *_args: pytest.fail("Must not save response")
        )
    ) as server:
        code, _headers, _body = post_answer(server, route=route)
    assert code == 405


def test_answer_conflicts_return_409_without_private_error_disclosure():
    def conflict(*_args):
        error = fp.ProgramError("private/file/question.json")
        error.code = "conflict"
        raise error

    with running(web.DashboardServer("127.0.0.1", 0, lambda: {}, conflict)) as server:
        code, _headers, body = post_answer(server)
    assert code == 409
    assert b"private" not in body


def test_answer_adapter_is_bound_to_one_configured_program(program, monkeypatch):
    calls = []
    monkeypatch.setattr(
        web.feature_program_questions,
        "record_answer",
        lambda *args: calls.append(args) or {"status": "answered"},
    )
    before = fingerprints(program)
    with running(web.create_server(program, Path("program"), port=0)) as server:
        code, _headers, _body = post_answer(server)
    assert code == 200
    assert calls == [
        (
            program.resolve(),
            (program / "program").resolve(),
            "Q1",
            1,
            "Use scoped verification",
            "synthetic-one",
        )
    ]
    assert fingerprints(program) == before


def test_valid_long_question_ids_reach_the_scoped_provider():
    calls = []
    payload = {
        "question_id": "Q" * 128,
        "version": 1,
        "answer": "Keep scope bounded",
        "submission_id": "long-question-id",
    }
    with running(
        web.DashboardServer(
            "127.0.0.1", 0, lambda: {}, lambda *args: calls.append(args) or {"status": "answered"}
        )
    ) as server:
        code, _headers, _body = post_answer(server, payload=payload)
    assert code == 200
    assert calls[0][0] == "Q" * 128
