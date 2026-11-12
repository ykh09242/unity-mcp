"""Exercise optional execution receipts through the actual CLI and HTTP helper."""

import json
from dataclasses import dataclass, field

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


STORE = "a" * 32
PROJECT = "b" * 16
RECEIPT = "c" * 32
KEY = "owned-command-0001"


@dataclass
class ReceiptPeer:
    """Mutable controlled peer used to observe dispatch without a live server."""

    requests: list = field(default_factory=list)
    available: bool = True
    timeout: bool = False
    missing_receipt: bool = False
    wrapped: bool = False
    failed: bool = False
    lookup_error: bool = False
    invalid_control: bool = False

    def respond(self, request):
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        if body["type"] == "get_execution_receipt":
            if self.invalid_control:
                return httpx.Response(200, json={"success": True, "data": "unrecognized command"})
            data = (
                {"version": 1, "available": self.available, "store_id": STORE}
                if body["params"]["action"] == "describe"
                else {"receipt_id": RECEIPT, "status": "unknown", "result_available": False}
            )
            return httpx.Response(
                200,
                json={"content": [], "structuredContent": data, "isError": self.lookup_error},
            )
        if self.timeout:
            raise httpx.ReadTimeout("untrusted transport diagnostic", request=request)
        result = {"success": not self.failed, "data": {"changed": False}}
        if self.failed:
            result["error"] = "Controlled operation failure"
        if self.wrapped:
            result = {"status": "success", "result": result}
        if not self.missing_receipt:
            result["execution_receipt"] = {
                "version": 1,
                "receipt_id": RECEIPT,
                "store_id": STORE,
                "project_hash": PROJECT,
                "status": "completed",
            }
        return httpx.Response(200, json=result)


@pytest.fixture
def receipt_peer(monkeypatch):
    peer = ReceiptPeer()
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(peer.respond)),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda _config: {})
    return peer


def execute_args(*, target=PROJECT, store=STORE, key=KEY, command="manage_scene", params="{}"):
    return [
        "--no-project-context",
        "--format",
        "json",
        "--instance",
        target,
        "receipt",
        "execute",
        "--store-id",
        store,
        "--idempotency-key",
        key,
        command,
        params,
    ]


@pytest.mark.parametrize("wrapped", [False, True])
def test_execute_keeps_receipt_outside_native_params_and_preserves_metadata(receipt_peer, wrapped):
    receipt_peer.wrapped = wrapped
    args = execute_args(target=f"Fixture@{PROJECT}", params='{"action":"get_active","flag":false}')
    args.insert(args.index("manage_scene"), "--retain-result")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["execution_receipt"]["receipt_id"] == RECEIPT
    assert document["data"] == {"changed": False}
    assert len(receipt_peer.requests) == 2
    preflight, dispatch = [body for _, body in receipt_peer.requests]
    assert preflight["type"] == "get_execution_receipt" and "execution" not in preflight
    assert dispatch["params"] == {"action": "get_active", "flag": False}
    assert dispatch["unity_instance"] == f"Fixture@{PROJECT}"
    assert dispatch["execution"] == {
        "version": 1,
        "store_id": STORE,
        "project_hash": PROJECT,
        "idempotency_key": KEY,
        "retain_result": True,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"target": "short"},
        {"target": ""},
        {"store": "bad"},
        {"key": "short"},
        {"key": "private value with spaces"},
        {"command": "get_execution_receipt"},
        {"command": "run_tests"},
        {"params": "[]"},
        {"params": '{"bad":NaN}'},
    ],
)
def test_invalid_protected_request_has_no_http_effect(receipt_peer, overrides):
    result = CliRunner().invoke(cli, execute_args(**overrides))
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["success"] is False
    assert receipt_peer.requests == []


def test_unavailable_store_refuses_before_mutation(receipt_peer):
    receipt_peer.available = False
    result = CliRunner().invoke(cli, execute_args())
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["code"] == "execution_receipts_unavailable"
    assert len(receipt_peer.requests) == 1
    assert receipt_peer.requests[0][1]["type"] == "get_execution_receipt"


@pytest.mark.parametrize("failure", ["timeout", "missing_receipt"])
def test_lost_or_unreceipted_response_never_replays_and_retains_lookup(receipt_peer, failure):
    setattr(receipt_peer, failure, True)
    result = CliRunner().invoke(cli, execute_args())
    assert result.exit_code == 1, result.output
    document = json.loads(result.stdout)
    assert document["code"] == "execution_receipt_outcome_unknown"
    assert document["data"]["lookup"] == {
        "store_id": STORE,
        "project_hash": PROJECT,
        "idempotency_key": KEY,
    }
    assert "untrusted transport diagnostic" not in result.output
    assert len(receipt_peer.requests) == 2


def test_failed_operation_preserves_receipt_and_nonzero_exit(receipt_peer):
    receipt_peer.failed = True
    result = CliRunner().invoke(cli, execute_args())
    assert result.exit_code == 1, result.output
    document = json.loads(result.stdout)
    assert document["error"] == "Controlled operation failure"
    assert document["execution_receipt"]["receipt_id"] == RECEIPT
    assert len(receipt_peer.requests) == 2


def test_describe_needs_no_editor_and_preserves_mcp_envelope(receipt_peer):
    result = CliRunner().invoke(
        cli, ["--no-project-context", "--format", "json", "receipt", "describe"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["structuredContent"]["store_id"] == STORE
    assert receipt_peer.requests == [
        ("/api/command", {"type": "get_execution_receipt", "params": {"action": "describe"}})
    ]


@pytest.mark.parametrize("selector,value", [("--receipt-id", RECEIPT), ("--idempotency-key", KEY)])
def test_lookup_without_editor_preserves_unknown_status(receipt_peer, selector, value):
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "receipt",
            "get",
            "--store-id",
            STORE,
            "--project-hash",
            PROJECT,
            selector,
            value,
            "--include-result",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["structuredContent"]["status"] == "unknown"
    assert len(receipt_peer.requests) == 1
    params = receipt_peer.requests[0][1]["params"]
    assert params["action"] == "get" and params["include_result"] is True
    assert params[selector[2:].replace("-", "_")] == value
    assert "unity_instance" not in receipt_peer.requests[0][1]


def test_lookup_error_is_not_success(receipt_peer):
    receipt_peer.lookup_error = True
    result = CliRunner().invoke(
        cli, ["--no-project-context", "--format", "json", "receipt", "describe"]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["isError"] is True


def test_invalid_control_response_is_not_reported_as_success(receipt_peer):
    receipt_peer.invalid_control = True
    result = CliRunner().invoke(
        cli, ["--no-project-context", "--format", "json", "receipt", "describe"]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["code"] == "invalid_receipt_response"


def test_default_text_keeps_receipt_id_visible(receipt_peer):
    args = execute_args()
    args[args.index("json")] = "text"
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert RECEIPT in result.stdout and "completed" in result.stdout


@pytest.mark.parametrize("selectors", [[], ["--receipt-id", RECEIPT, "--idempotency-key", KEY]])
def test_lookup_requires_exactly_one_identifier_before_network(receipt_peer, selectors):
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "receipt",
            "get",
            "--store-id",
            STORE,
            "--project-hash",
            PROJECT,
            *selectors,
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "invalid_receipt_lookup"
    assert receipt_peer.requests == []


def test_execute_accepts_owned_json_file_without_nested_source_expansion(receipt_peer, tmp_path):
    source = tmp_path / "params.json"
    source.write_text('{"action":"create","name":"@literal"}', encoding="utf-8")
    result = CliRunner().invoke(cli, execute_args(params=f"@{source}"))
    assert result.exit_code == 0, result.output
    assert receipt_peer.requests[-1][1]["params"]["name"] == "@literal"


def test_protected_dispatch_and_lookup_preserve_project_guard(receipt_peer, tmp_path):
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.0.69f1\n"
    )
    args = execute_args()
    args[:1] = ["--expect-project", str(tmp_path)]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert all(path == "/api/context-command" for path, _ in receipt_peer.requests)
    assert all(body["expected_project"]["root"] for _, body in receipt_peer.requests)
    receipt_peer.requests.clear()
    result = CliRunner().invoke(
        cli, ["--expect-project", str(tmp_path), "--format", "json", "receipt", "describe"]
    )
    assert result.exit_code == 0, result.output
    assert receipt_peer.requests[0][0] == "/api/context-command"


def test_ordinary_command_never_enables_receipts(receipt_peer):
    result = CliRunner().invoke(
        cli, ["--no-project-context", "--format", "json", "raw", "manage_scene"]
    )
    assert result.exit_code == 0, result.output
    assert len(receipt_peer.requests) == 1
    assert "execution" not in receipt_peer.requests[0][1]
