"""Cache retention must preserve recoverable generations and fail closed."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest


TOOLS = Path(__file__).resolve().parents[1]
for name in (
    "unity_ci",
    "unity_compile_cache",
    "unity_ci_packages",
    "unity_ci_package_cache",
    "ci_cache_retention",
):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
retention = sys.modules["ci_cache_retention"]
NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
VERSION = "6000.0.84f1"
REF = "refs/heads/beta"


def key(generation, family="sdk", schema=1, version=VERSION):
    return f"unity-ci-{family}-v{schema}-linux-amd64-{version}-" + generation * 64


def entry(identifier, generation, days, **changes):
    record = {
        "id": identifier,
        "key": key(generation),
        "ref": REF,
        "version": f"archive-{identifier}",
        "size_in_bytes": identifier * 100,
        "created_at": (NOW - timedelta(days=days)).isoformat(),
        "last_accessed_at": (NOW - timedelta(days=days)).isoformat(),
    }
    record.update(changes)
    return retention.parse_entry(record)


def test_current_and_immediately_previous_distinct_generation_are_preserved():
    entries = (entry(1, "a", 6), entry(2, "b", 5), entry(3, "b", 4), entry(4, "c", 3))
    report = retention.build_plan(entries, {("sdk", VERSION): key("c")}, "owner/repo", REF, NOW, 0)
    assert [row["id"] for row in report["candidates"]] == [1]
    assert report["candidate_bytes"] == 100


def plan(entries, keys=None, active=0):
    return retention.build_plan(
        tuple(entries),
        keys if keys is not None else {("sdk", VERSION): key("c")},
        "owner/repo",
        REF,
        NOW,
        active,
    )


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"ref": "refs/heads/main"}, "other-ref"),
        ({"ref": "refs/pull/12/merge"}, "other-ref"),
        ({"key": "uv-cache-example"}, "unknown-namespace"),
        ({"key": key("a", schema=99)}, "unknown-namespace"),
        ({"key": key("a").replace("linux-amd64", "linux-arm64")}, "unknown-namespace"),
        ({"last_accessed_at": NOW.isoformat()}, "recently-created-or-accessed"),
        (
            {"created_at": NOW.isoformat(), "last_accessed_at": NOW.isoformat()},
            "recently-created-or-accessed",
        ),
        (
            {
                "created_at": (NOW + timedelta(hours=1)).isoformat(),
                "last_accessed_at": (NOW + timedelta(hours=1)).isoformat(),
            },
            "recently-created-or-accessed",
        ),
    ],
)
def test_unknown_refs_namespaces_and_recent_or_future_data_are_preserved(changes, expected):
    report = plan([entry(1, "a", 5, **changes)], {})
    assert not report["candidates"]
    assert report["preserved"][0]["reason"] == expected


def test_missing_current_key_preserves_every_active_generation():
    report = plan([entry(1, "a", 6), entry(2, "b", 5)])
    assert not report["candidates"]
    assert {row["reason"] for row in report["preserved"]} == {"current-key-not-published"}


def test_newer_generation_and_tied_predecessors_are_preserved():
    report = plan(
        [
            entry(1, "a", 8),
            entry(2, "b", 6),
            entry(3, "d", 6),
            entry(4, "c", 4),
            entry(5, "e", 3),
        ]
    )
    assert [row["id"] for row in report["candidates"]] == [1]
    assert {row["reason"] for row in report["preserved"]} == {
        "current-generation",
        "previous-generation",
        "unconfirmed-newer-generation",
    }


def test_old_removed_versions_and_obsolete_schema_can_be_candidates():
    entries = [
        entry(1, "a", 4, key=key("a", version="6000.0.1f1")),
        entry(2, "a", 4, key=key("a", family="optional", schema=1)),
        entry(3, "b", 3, key=key("b", family="optional", schema=2)),
        entry(4, "c", 2, key=key("c", family="optional", schema=2)),
    ]
    report = plan(entries, {("optional", VERSION): key("c", family="optional", schema=2)})
    assert {row["id"] for row in report["candidates"]} == {1, 2}


def test_any_active_workflow_blocks_candidates_for_all_refs():
    report = plan([entry(1, "a", 5)], {}, active=1)
    assert not report["candidates"]
    assert report["preserved"][0]["reason"] == "active-workflow-runs"


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", True),
        ("id", 0),
        ("size_in_bytes", -1),
        ("size_in_bytes", "100"),
        ("version", None),
        ("key", "bad\nkey"),
        ("ref", ""),
        ("created_at", None),
        ("created_at", "2026-10-01"),
        ("last_accessed_at", "2020-01-01T00:00:00Z"),
    ],
)
def test_malformed_entry_types_fail_closed(field, value):
    with pytest.raises(retention.RetentionError):
        entry(1, "a", 4, **{field: value})


class FakeApi:
    def __init__(self, entries=(), runs=(), responses=()):
        self.entries = [row.record() for row in entries]
        self.runs = list(runs)
        self.responses = list(responses)
        self.requests = []
        self.deleted = []

    def request(self, endpoint, method="GET"):
        self.requests.append((method, endpoint))
        if method == "DELETE":
            identifier = int(endpoint.rsplit("/", 1)[1])
            self.deleted.append(identifier)
            self.entries = [row for row in self.entries if row["id"] != identifier]
            return None
        if self.responses:
            return self.responses.pop(0)
        parsed = urlsplit(endpoint)
        parameters = parse_qs(parsed.query)
        page = int(parameters["page"][0])
        size = int(parameters["per_page"][0])
        if parsed.path.endswith("/runs"):
            status = parameters["status"][0]
            records = [row for row in self.runs if row["status"] == status]
            field = "workflow_runs"
        else:
            records, field = self.entries, "actions_caches"
        return {"total_count": len(records), field: records[(page - 1) * size : page * size]}


def test_empty_inventory_is_complete_but_missing_rows_fail_closed():
    api = FakeApi()
    assert retention.inventory(api, "owner/repo", "caches", "actions_caches", {}) == []
    assert len(api.requests) == 1
    with pytest.raises(retention.RetentionError, match="Incomplete"):
        retention.inventory(
            FakeApi(responses=[{"total_count": 1, "actions_caches": []}]),
            "owner/repo",
            "caches",
            "actions_caches",
            {},
        )


def test_cache_and_active_run_inventories_paginate_completely(monkeypatch):
    monkeypatch.setattr(retention, "PAGE_SIZE", 2)
    api = FakeApi(
        [entry(index, "a", 5) for index in range(1, 6)],
        [{"id": index, "status": "in_progress"} for index in range(1, 6)],
    )
    rows = retention.inventory(api, "owner/repo", "caches", "actions_caches", {})
    assert len(rows) == 5
    assert retention.active_runs(api, "owner/repo") == 5
    assert any("page=3" in endpoint and "/runs?" in endpoint for _, endpoint in api.requests)


@pytest.mark.parametrize(
    "responses",
    [
        [{"total_count": 0, "actions_caches": [{"id": 1}]}],
        [{"total_count": True, "actions_caches": []}],
        [{"total_count": 2001, "actions_caches": []}],
        [{"total_count": 1, "actions_caches": None}],
        [{"total_count": 2, "actions_caches": [{"id": 1}, {"id": 1}]}],
        [{"total_count": 2, "actions_caches": [{"id": True}, {"id": 2}]}],
        [
            {"total_count": 3, "actions_caches": [{"id": 1}, {"id": 2}]},
            {"total_count": 4, "actions_caches": [{"id": 3}, {"id": 4}]},
        ],
        [
            {"total_count": 3, "actions_caches": [{"id": 1}, {"id": 2}]},
            {"total_count": 3, "actions_caches": [{"id": 1}]},
        ],
    ],
)
def test_incomplete_or_changing_pagination_fails_closed(monkeypatch, responses):
    monkeypatch.setattr(retention, "PAGE_SIZE", 2)
    with pytest.raises(retention.RetentionError):
        retention.inventory(
            FakeApi(responses=responses), "owner/repo", "caches", "actions_caches", {}
        )


def test_wrong_active_status_fails_closed():
    api = FakeApi(
        responses=[{"total_count": 1, "workflow_runs": [{"id": 1, "status": "completed"}]}]
    )
    with pytest.raises(retention.RetentionError, match="status"):
        retention.active_runs(api, "owner/repo")


@pytest.fixture
def application(monkeypatch):
    keys = {("sdk", VERSION): key("c")}
    monkeypatch.setattr(retention, "current_keys", lambda: keys)
    monkeypatch.setattr(retention, "checkout_binding", lambda *_args: None)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(retention, "datetime", Clock)
    rows = [entry(1, "a", 6), entry(2, "b", 5), entry(3, "c", 3)]
    api = FakeApi(rows)
    return api, retention.fresh_plan(api, "owner/repo", REF, NOW)


def test_reviewed_application_deletes_only_exact_eligible_ids(application):
    api, reviewed = application
    assert retention.apply_plan(api, reviewed, "owner/repo", REF, NOW) == [1]
    assert api.deleted == [1]
    assert [row["id"] for row in api.entries] == [2, 3]
    deletes = [endpoint for method, endpoint in api.requests if method == "DELETE"]
    assert deletes == ["repos/owner/repo/actions/caches/1"]


@pytest.mark.parametrize(
    "change", ["access", "activity", "new-current", "current-absent", "key", "size", "archive"]
)
def test_application_revalidates_metadata_and_protection_before_any_delete(application, change):
    api, reviewed = application
    if change == "access":
        api.entries[0]["last_accessed_at"] = NOW.isoformat()
    elif change == "activity":
        api.runs.append({"id": 99, "status": "queued"})
    elif change == "new-current":
        api.entries.insert(0, entry(4, "c", 8).record())
    elif change == "current-absent":
        api.entries.pop()
    elif change == "key":
        api.entries[0]["key"] = "unknown-cache"
    elif change == "size":
        api.entries[0]["size_in_bytes"] += 1
    else:
        api.entries[0]["version"] = "replacement-archive"
    with pytest.raises(retention.RetentionError):
        retention.apply_plan(api, reviewed, "owner/repo", REF, NOW)
    assert not api.deleted


@pytest.mark.parametrize(
    "field,value",
    [
        ("generated_at", (NOW - timedelta(hours=2)).isoformat()),
        ("generated_at", (NOW + timedelta(seconds=1)).isoformat()),
        ("repository", "wrong/repo"),
        ("ref", "refs/heads/main"),
        ("minimum_age_hours", 0),
        ("last_access_grace_hours", 0),
        ("current_keys", []),
        ("candidates", None),
    ],
)
def test_stale_or_changed_plan_is_rejected(application, field, value):
    api, reviewed = application
    reviewed[field] = value
    with pytest.raises(retention.RetentionError):
        retention.apply_plan(api, reviewed, "owner/repo", REF, NOW)
    assert not api.deleted


def test_duplicate_or_protected_candidate_injection_is_rejected(application):
    api, reviewed = application
    reviewed["candidates"].append(reviewed["candidates"][0])
    with pytest.raises(retention.RetentionError, match="Duplicate"):
        retention.apply_plan(api, reviewed, "owner/repo", REF, NOW)
    reviewed["candidates"] = [api.entries[2]]
    with pytest.raises(retention.RetentionError, match="eligibility"):
        retention.apply_plan(api, reviewed, "owner/repo", REF, NOW)
    assert not api.deleted


def test_run_start_between_review_check_and_delete_blocks_mutation(application, monkeypatch):
    api, reviewed = application
    original = retention.fresh_plan
    calls = 0

    def race(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            api.runs.append({"id": 99, "status": "in_progress"})
        return original(*args)

    monkeypatch.setattr(retention, "fresh_plan", race)
    with pytest.raises(retention.RetentionError, match="protected"):
        retention.apply_plan(api, reviewed, "owner/repo", REF, NOW)
    assert not api.deleted


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("gh", 30), OSError("missing gh")])
def test_transport_timeout_and_unavailable_commands_fail_closed(monkeypatch, failure):
    def run(*_args, **kwargs):
        assert kwargs["timeout"] == 30
        raise failure

    monkeypatch.setattr(retention.subprocess, "run", run)
    with pytest.raises(retention.RetentionError, match="unavailable or timed out"):
        retention.command(["gh", "api", "example"])


def test_transport_error_never_emits_stderr_credentials(monkeypatch):
    monkeypatch.setattr(
        retention.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "fake-secret-value"),
    )
    with pytest.raises(retention.RetentionError) as error:
        retention.command(["gh", "api", "example"])
    assert "fake-secret-value" not in str(error.value)


def test_actual_manifest_keys_use_ci_helpers_without_provider_operations():
    keys = retention.current_keys()
    manifest = sys.modules["unity_ci"].load_manifest(TOOLS / "unity-versions.json")
    assert {version for family, version in keys if family == "sdk"} == {
        row.id for row in manifest.versions
    }
    assert (
        keys[("sdk", VERSION)]
        == sys.modules["unity_compile_cache"].identity(manifest, VERSION)["cache_key"]
    )
    request = sys.modules["unity_ci_package_cache"].CacheRequest(
        VERSION,
        TOOLS.parent,
        TOOLS / "unity-versions.json",
        TOOLS / "unity-ci-packages.json",
    )
    assert (
        keys[("optional", VERSION)]
        == sys.modules["unity_ci_package_cache"].identity(request).cache_key
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/ykh09242/unity-mcp.git",
        "https://github.com/ykh09242/unity-mcp",
        "git@github.com:ykh09242/unity-mcp.git",
    ],
)
def test_origin_parsing_recognizes_authorized_fork(url):
    assert retention.origin_repository(url) == "ykh09242/unity-mcp"


@pytest.mark.parametrize(
    "url",
    [
        "https://fake-token@github.com/owner/repo.git",
        "https://github.com.evil/owner/repo",
        "https://github.com/owner/repo?token=fake-token",
        "file:///local/repo",
    ],
)
def test_bad_or_credential_bearing_origin_is_rejected_without_url_disclosure(url):
    with pytest.raises(retention.RetentionError) as error:
        retention.origin_repository(url)
    assert url not in str(error.value)
    assert "fake-token" not in str(error.value)


@pytest.fixture
def bound_checkout(monkeypatch):
    calls = []
    responses = {
        "remote": "https://github.com/ykh09242/unity-mcp.git\n",
        "symbolic-ref": REF + "\n",
        "ls-files": "tracked\n",
        "diff": "",
        "rev-parse": "a" * 40 + "\n",
        "ls-remote": "a" * 40 + "\t" + REF + "\n",
    }

    def command(arguments):
        calls.append(arguments)
        assert arguments[0] == "git"  # Never use gh's upstream default repository.
        return responses[arguments[1]]

    monkeypatch.setattr(retention, "command", command)
    return responses, calls


def test_fork_binding_uses_origin_even_when_gh_would_default_to_upstream(bound_checkout):
    _, calls = bound_checkout
    retention.checkout_binding("ykh09242/unity-mcp", REF)
    assert len(calls) == 6
    with pytest.raises(retention.RetentionError, match="origin"):
        retention.checkout_binding("CoplayDev/unity-mcp", REF)


@pytest.mark.parametrize(
    "field,value",
    [
        ("diff", "tools/unity-versions.json\n"),
        ("symbolic-ref", "refs/heads/main\n"),
        ("ls-remote", "b" * 40 + "\t" + REF + "\n"),
        ("ls-remote", ""),
        ("rev-parse", "invalid\n"),
    ],
)
def test_dirty_inputs_wrong_branch_or_stale_remote_head_fail_closed(bound_checkout, field, value):
    responses, _ = bound_checkout
    responses[field] = value
    with pytest.raises(retention.RetentionError):
        retention.checkout_binding("ykh09242/unity-mcp", REF)


def test_default_cli_writes_reviewable_report_without_deletion(
    application, monkeypatch, tmp_path, capsys
):
    api, _ = application
    monkeypatch.setattr(retention, "GitHubApi", lambda: api)
    output = tmp_path / "plan.json"
    assert retention.main(["--repository", "owner/repo", "--output", str(output)]) == 0
    assert output.is_file()
    assert '"candidate_count": 1' in output.read_text(encoding="utf-8")
    assert '"deleted_ids"' not in capsys.readouterr().out
    assert not api.deleted


def test_pagination_limit_cannot_return_a_partial_inventory(monkeypatch):
    monkeypatch.setattr(retention, "PAGE_SIZE", 1)
    monkeypatch.setattr(retention, "MAX_PAGES", 1)
    with pytest.raises(retention.RetentionError, match="count"):
        retention.inventory(
            FakeApi([entry(1, "a", 4), entry(2, "b", 4)]),
            "owner/repo",
            "caches",
            "actions_caches",
            {},
        )


def test_delete_error_stops_before_subsequent_ids(application):
    api, reviewed = application

    class ErrorApi:
        def request(self, endpoint, method="GET"):
            if method == "DELETE":
                raise retention.RetentionError("GitHub deletion failed")
            return api.request(endpoint, method)

    with pytest.raises(retention.RetentionError, match="deletion failed"):
        retention.apply_plan(ErrorApi(), reviewed, "owner/repo", REF, NOW)
    assert not api.deleted


def test_untracked_identity_inputs_fail_closed(bound_checkout, monkeypatch):
    original = retention.command

    def command(arguments):
        if arguments[1] == "ls-files":
            raise retention.RetentionError("git command failed")
        return original(arguments)

    monkeypatch.setattr(retention, "command", command)
    with pytest.raises(retention.RetentionError):
        retention.checkout_binding("ykh09242/unity-mcp", REF)


def test_workflow_search_result_cap_cannot_hide_active_runs():
    api = FakeApi(responses=[{"total_count": 1001, "workflow_runs": []}])
    with pytest.raises(retention.RetentionError, match="count"):
        retention.active_runs(api, "owner/repo")


def test_boolean_plan_schema_is_not_an_integer(application):
    api, reviewed = application
    reviewed["schema"] = True
    with pytest.raises(retention.RetentionError, match="Unsupported"):
        retention.apply_plan(api, reviewed, "owner/repo", REF, NOW)
    assert not api.deleted
