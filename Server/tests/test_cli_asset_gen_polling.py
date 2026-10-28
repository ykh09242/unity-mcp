"""Generation polling follows the originating capability and exact local target."""

import json
import shlex

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture
def generation_wire(monkeypatch):
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append((request.url, json.loads(request.content)))
        return httpx.Response(200, json={"success": True, "data": {"job_id": "owned-job"}})

    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    return requests


@pytest.mark.parametrize("kind", ["image", "audio", "model"])
def test_generated_hint_dispatches_same_capability_and_explicit_target(
    generation_wire, kind, tmp_path
):
    root = tmp_path / "Owned Project"
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.0")
    arguments = [
        "--host",
        "127.0.0.1",
        "--port",
        "9081",
        "--timeout",
        "41",
        "--instance",
        "Same Project@aaaaaaaaaaaaaaaa",
        "--expect-project",
        str(root),
        "asset-gen",
        f"generate-{kind}",
        "--prompt",
        "fixture",
    ]
    started = CliRunner().invoke(cli, arguments)
    assert started.exit_code == 0, (started.output, started.exception)
    hint = started.output.split("Poll with: ", 1)[1].splitlines()[0]
    polled = CliRunner().invoke(cli, shlex.split(hint)[1:])
    assert polled.exit_code == 0, (polled.output, polled.exception)
    assert len(generation_wire) == 2
    url, payload = generation_wire[1]
    assert str(url) == "http://127.0.0.1:9081/api/context-command"
    assert payload == {
        "type": f"generate_{kind}",
        "params": {"action": "status", "jobId": "owned-job"},
        "unity_instance": "Same Project@aaaaaaaaaaaaaaaa",
        "expected_project": {"root": str(root.resolve())},
    }


@pytest.mark.parametrize("kind", ["image", "audio", "model"])
def test_explicit_poll_kind_dispatches_that_tool(generation_wire, kind):
    result = CliRunner().invoke(
        cli, ["asset-gen", "status", "--kind", kind, "--job-id", "owned-job"]
    )
    assert result.exit_code == 0, result.output
    assert generation_wire[0][1]["type"] == f"generate_{kind}"


def test_legacy_status_defaults_to_model_for_existing_commands(generation_wire):
    result = CliRunner().invoke(cli, ["asset-gen", "status", "--job-id", "owned-job"])
    assert result.exit_code == 0, result.output
    assert generation_wire[0][1]["type"] == "generate_model"
    assert "--kind image" in result.stderr and "--kind audio" in result.stderr


def test_invalid_kind_never_dispatches(generation_wire):
    result = CliRunner().invoke(
        cli, ["asset-gen", "status", "--kind", "unknown", "--job-id", "job"]
    )
    assert result.exit_code == 2
    assert generation_wire == []
