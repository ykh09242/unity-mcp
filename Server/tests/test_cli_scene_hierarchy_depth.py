"""Scene hierarchy CLI traversal through the paged, single-level HTTP contract."""

import json

import httpx
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


def test_scene_hierarchy_depth_and_paging(monkeypatch):
    calls = []
    children = {None: [(10, "Root")], "10": [(20, "Child"), (21, "Sibling")], "20": [(30, "Leaf")]}

    def respond(request):
        assert request.url.path == "/api/command"
        payload = json.loads(request.content)
        params = payload["params"]
        assert payload["type"] == "manage_scene" and params["action"] == "get_hierarchy"
        calls.append(params)
        siblings = children[params.get("parent")]
        start = params["cursor"]
        end = start + params["pageSize"]
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [{"instanceID": i, "name": name} for i, name in siblings[start:end]],
                    "next_cursor": str(end) if end < len(siblings) else None,
                },
            },
        )

    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    client = httpx.AsyncClient
    monkeypatch.setattr(
        connection, "_http_client", lambda config: client(transport=httpx.MockTransport(respond))
    )
    runner = CliRunner()

    def invoke(*args):
        return runner.invoke(cli, ["--format", "json", "scene", "hierarchy", *args])

    for depth in ("0", "3", "-1"):
        result = invoke("--max-depth", depth)
        assert result.exit_code != 0 and not calls, result.output
        assert "--parent" in result.output and "next_cursor" in result.output

    def page(*args):
        result = invoke(*args)
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)["data"]

    root = page("--max-depth", "1")["items"][0]
    first = page("--parent", str(root["instanceID"]), "--limit", "1")
    second = page(
        "--parent", str(root["instanceID"]), "--limit", "1", "--cursor", first["next_cursor"]
    )
    assert second["items"][0]["name"] == "Sibling"
    assert page("--parent", str(first["items"][0]["instanceID"]))["items"][0]["name"] == "Leaf"
    help_text = invoke("--help").output
    assert "--max-depth 3" not in help_text and "next_cursor" in help_text
