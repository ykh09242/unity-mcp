import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools.experiments import native_console_state as probe


def page(cursor, rows, more=False):
    end = cursor + len(rows)
    return {
        "data": {
            "items": [{"type": severity, "message": text} for severity, text in rows],
            "cursor": cursor,
            "pageSize": 2,
            "truncated": more,
            "nextCursor": str(end) if more else None,
            "total": end + int(more),
        },
    }


def collect(pages):
    async def call(name, args):
        assert name == "read_console"
        assert args["types"] == ["error"]
        assert args["fields"] == ["type", "message"]
        assert args["include_mcp_logs"] is True
        return pages[args["cursor"]]

    return asyncio.run(probe.collect(call, "error"))


def test_paged_console_keeps_duplicate_messages_and_unity_error_types(monkeypatch):
    monkeypatch.setattr(probe, "PAGE_SIZE", 2)
    pages = {
        0: page(0, [("Error", "repeated"), ("Exception", "unique")], more=True),
        2: page(2, [("Assert", "repeated"), ("Error", "another")], more=True),
        4: page(4, [("Error", "last")]),
    }
    assert collect(pages) == ["another", "last", "repeated", "repeated", "unique"]


@pytest.mark.parametrize(
    "change",
    [
        {"cursor": 1},
        {"truncated": 1},
        {"truncated": True, "nextCursor": "0", "total": 3},
        {"total": 9},
        {"nextCursor": "1"},
        {"items": [{"type": "Warning", "message": "wrong severity"}]},
    ],
)
def test_partial_or_wrong_severity_page_cannot_be_ground_truth(monkeypatch, change):
    monkeypatch.setattr(probe, "PAGE_SIZE", 2)
    response = page(0, [("Error", "sample")])
    response["data"].update(change)
    with pytest.raises(ValueError):
        collect({0: response})


@pytest.mark.parametrize("project_hash", ["01ab23cd", "01AB23CD4567Ef89"])
def test_selection_requires_one_running_project_even_with_matching_display_names(
    tmp_path, project_hash
):
    name = "SharedName"
    project = tmp_path / "A" / name
    other = tmp_path / "B" / name
    target = {
        "path": str(project / "Assets"),
        "name": name,
        "id": name + "@" + project_hash,
        "status": "running",
    }
    neighbor = dict(target, path=str(other / "Assets"), id=name + "@bbbbbbbb")
    inventory = {"transport": "stdio", "instances": [neighbor, target]}
    assert probe.project_instance(inventory, project) == target["id"]
    alias = dict(inventory, instances=[dict(target, id="Other@" + project_hash)])
    with pytest.raises(ValueError, match="exact instance"):
        probe.project_instance(alias, project)
    conflict = dict(inventory, instances=[target, dict(target, id=name + "@aaaaaaaa")])
    with pytest.raises(ValueError, match="exactly one"):
        probe.project_instance(conflict, project)
    target["status"] = "reloading"
    with pytest.raises(ValueError, match="exactly one"):
        probe.project_instance(inventory, project)


def test_observer_rejects_a_different_native_project_or_changing_rows(tmp_path):
    project = tmp_path / "Fixture"
    instance = "Fixture@aaaa1111"

    def blocks(data):
        return [SimpleNamespace(text=json.dumps({"success": True, **data}))]

    def resource(data):
        return SimpleNamespace(contents=blocks(data))

    def tool(data):
        return SimpleNamespace(is_error=False, content=blocks(data))

    discovered = resource(
        {
            "transport": "stdio",
            "instances": [
                {
                    "status": "running",
                    "name": "Fixture",
                    "id": instance,
                    "path": str(project / "Assets"),
                }
            ],
        }
    )
    session = AsyncMock()
    session.read_resource.side_effect = [
        discovered,
        resource({"data": {"projectRoot": str(tmp_path), "assetsPath": str(tmp_path / "Assets")}}),
    ]
    with pytest.raises(ValueError, match="identity"):
        asyncio.run(probe.observe(session, project))
    session.call_tool.assert_not_awaited()
    session.read_resource.assert_awaited_with(
        "mcpforunity://project/info", meta={"unity_instance": instance}
    )

    session.read_resource.side_effect = [
        discovered,
        resource({"data": {"projectRoot": str(project), "assetsPath": str(project / "Assets")}}),
    ]

    def snapshot(text):
        result = page(0, [("Error", text)] if text else [])
        result["data"]["pageSize"] = probe.PAGE_SIZE
        return tool(result)

    session.call_tool.side_effect = [
        snapshot("first"),
        snapshot(""),
        snapshot("second"),
        snapshot(""),
    ]
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(probe.observe(session, project))


def test_http_instance_requires_one_exact_registered_id(tmp_path):
    instance = "Fixture@bbbbbbbb11111111"
    inventory = {"transport": "http", "instances": [{"id": "Other@aaaa1111"}, {"id": instance}]}
    assert probe.project_instance(inventory, tmp_path, instance) == instance
    for invalid in ("Fixture", "Fixture@bbbb", "Missing@aaaa1111"):
        with pytest.raises(ValueError):
            probe.project_instance(inventory, tmp_path, invalid)
    inventory["instances"].append({"id": instance})
    with pytest.raises(ValueError, match="exactly one"):
        probe.project_instance(inventory, tmp_path, instance)


def test_http_cli_passes_endpoint_and_instance_together(monkeypatch, tmp_path, capsys):
    (tmp_path / "Assets").mkdir()
    endpoint = "http://127.0.0.1:54321/mcp"
    instance = "Fixture@aaaa1111"
    run = AsyncMock(return_value={"observation_fixtures": {}})
    monkeypatch.setattr(probe, "run", run)
    argv = ["native_console_state.py", str(tmp_path), "--endpoint", endpoint]
    monkeypatch.setattr(probe.sys, "argv", argv)
    with pytest.raises(SystemExit):
        probe.main()
    run.assert_not_awaited()
    monkeypatch.setattr(probe.sys, "argv", [*argv, "--instance", instance])
    probe.main()
    run.assert_awaited_once_with(tmp_path.resolve(), endpoint, instance)
    assert json.loads(capsys.readouterr().out) == {"observation_fixtures": {}}


def test_http_rejects_remote_endpoint_before_credential_lookup(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(probe.SERVER.parent))
    from tools.experiments import mcp_client_assurance as assurance

    def unexpected_lookup(*args):
        pytest.fail("Unexpected credential lookup")

    monkeypatch.setattr(assurance, "read_local_auth_token", unexpected_lookup)
    with pytest.raises(ValueError, match="loopback"):
        asyncio.run(probe.run(tmp_path, "http://example.invalid/mcp", "Fixture@aaaa1111"))


def test_official_http_client_observes_only_exact_project(monkeypatch, tmp_path):
    import httpx2
    from fastmcp import Context, FastMCP

    monkeypatch.syspath_prepend(str(probe.SERVER.parent))
    from tools.experiments import mcp_client_assurance as assurance

    instance = "Fixture@01AB23cd4567Ef89"
    server = FastMCP("Local Console fixture")
    project = tmp_path / "Fixture"
    pinned = []

    @server.resource("mcpforunity://instances")
    def instances():
        return {"success": True, "transport": "http", "instances": [{"id": instance}]}

    @server.resource("mcpforunity://project/info")
    def project_info(ctx: Context):
        meta = ctx.request_context.meta or {}
        assert meta.get("unity_instance") == instance
        return {
            "success": True,
            "data": {"projectRoot": str(project), "assetsPath": str(project / "Assets")},
        }

    @server.tool()
    def read_console(
        action: str,
        types: list[str],
        format: str,
        fields: list[str],
        include_mcp_logs: bool,
        page_size: int,
        cursor: int,
        unity_instance: str,
    ):
        assert action == "get" and format == "json" and fields == ["type", "message"]
        assert include_mcp_logs and page_size == probe.PAGE_SIZE and cursor == 0
        assert unity_instance == instance
        pinned.append(types[0])
        return {
            "success": True,
            "data": {
                "cursor": 0,
                "pageSize": page_size,
                "total": 1,
                "nextCursor": None,
                "truncated": False,
                "items": [{"type": types[0].title(), "message": types[0]}],
            },
        }

    app = server.http_app(stateless_http=True, json_response=True)

    def in_process_client(**options):
        assert options["trust_env"] is False
        assert options["headers"][assurance.LOCAL_AUTH_HEADER] == "fixture-only"
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **options)

    monkeypatch.setattr(
        assurance, "httpx2", SimpleNamespace(AsyncClient=in_process_client, Timeout=httpx2.Timeout)
    )
    monkeypatch.setattr(assurance, "read_local_auth_token", lambda host, port: "fixture-only")

    async def scenario():
        async with app.router.lifespan_context(app):
            return await probe.run(project, "http://127.0.0.1:12345/mcp", instance)

    assert asyncio.run(scenario()) == {
        "observation_fixtures": {"console-errors": {"errors": ["error"], "warnings": ["warning"]}}
    }
    assert pinned == ["error", "warning"] * 2
