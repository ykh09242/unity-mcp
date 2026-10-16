"""The final Skills extension serves the same pinned bytes over authenticated MCP."""

import base64
import hashlib
import copy
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from core.config import config
from core.local_auth import LOCAL_AUTH_HEADER
from models.response_limits import MAX_RESPONSE_BYTES
from mcp.shared.inbound import encode_header_value
from tests.http.prepared_tool_registration import prepared_tool_factory  # pylint: disable=unused-import
from tests.http.test_mcp_catalog_subscription_limits import hosted_catalog  # pylint: disable=unused-import
from tests.http.test_mcp_protocol_wire import LEGACY_VERSION, _body, _headers, _message

EXTENSION = "io.modelcontextprotocol/skills"
SKILL_URI = "skill://unity-mcp-orchestrator/SKILL.md"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def skills_server(monkeypatch, prepared_tool_factory):
    """Production assembly without a process per case or an Editor connection."""
    from fastmcp.tools.base import Tool
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "local_auth_token", secrets.token_urlsafe(32))
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    with monkeypatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        return create_mcp_server(False)


@asynccontextmanager
async def skills_http(server):
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
            headers={
                LOCAL_AUTH_HEADER: config.local_auth_token,
                "Accept": "application/json, text/event-stream",
            },
        ) as client:
            yield client


async def skill_request(client, method, params=None, *, declared=False, headers=None):
    body = _body(method, params)
    if declared:
        body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
            "extensions": {EXTENSION: {}}
        }
    name = (params or {}).get("uri") or (params or {}).get("name")
    response = await client.post(
        "/mcp",
        json=body,
        headers={
            **_headers(method, name=encode_header_value(name) if name else None),
            **(headers or {}),
        },
    )
    return response


def complete_result(response):
    assert response.status_code == 200, response.text
    message = _message(response)
    assert "error" not in message, message
    result = message["result"]
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"
    assert "io.modelcontextprotocol/serverInfo" in result["_meta"]
    assert len(response.content) <= MAX_RESPONSE_BYTES
    return result


def packaged_record():
    return json.loads((ROOT / "Server/src/services/skills_bundle.json").read_bytes())


def encode_record(record):
    """Create a sealed synthetic package record; this is not a signature/trust claim."""
    payload = json.dumps(
        record["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    record["sha256"] = hashlib.sha256(payload).hexdigest()
    return json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


@pytest.fixture
def bare_skills_server(monkeypatch):
    from main import UnityMCP
    from transport.response_limit_middleware import ResponseLimitMiddleware

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "local_auth_token", secrets.token_urlsafe(32))
    server = UnityMCP("skills-admission-fixture")
    server.add_middleware(ResponseLimitMiddleware())

    @server.tool
    def ordinary_alive() -> bool:
        return True

    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [False, True], ids=["published-shape", "declared-client"])
async def test_packaged_skill_is_discovered_then_read_on_demand(skills_server, declared):
    # Given: the production authenticated server, without a running Unity instance.
    reads = []

    async def observed(request):
        if request.headers.get("Mcp-Method") == "resources/read":
            reads.append(json.loads(request.content)["params"]["uri"])

    async with skills_http(skills_server) as client:
        client.event_hooks["request"].append(observed)
        # When: discovery, list and direct lookup precede individually requested files.
        discovery = complete_result(await skill_request(client, "server/discover"))
        assert EXTENSION in discovery["capabilities"]["extensions"]
        assert "resources" in discovery["capabilities"]
        assert not discovery["capabilities"]["extensions"][EXTENSION].get("directoryRead", False)
        listed = complete_result(await skill_request(client, "skills/list", declared=declared))
        entry = listed["skills"][0]
        fetched = complete_result(
            await skill_request(client, "skills/get", {"uri": SKILL_URI}, declared=declared)
        )
        assert fetched["skill"] == entry
        assert reads == []

        # Then: the complete manifest describes exactly the package-matched canonical bytes.
        assert entry["uri"] == SKILL_URI
        assert entry["frontmatter"]["name"] == "unity-mcp-orchestrator"
        assert (
            entry["_meta"]["io.modelcontextprotocol.skills/sourceRevision"]
            == json.loads((ROOT / "MCPForUnity/package.json").read_text(encoding="utf-8"))[
                "mcpSkillRevision"
            ]
        )
        resources = entry["resources"]
        assert len(resources) == 7
        assert [item["uri"] for item in resources] == sorted(item["uri"] for item in resources)
        assert SKILL_URI in {item["uri"] for item in resources}
        for item in resources:
            result = complete_result(
                await skill_request(client, "resources/read", {"uri": item["uri"]})
            )
            content = result["contents"][0]
            assert content["mimeType"] == "text/markdown"
            assert content["uri"] == item["uri"]
            raw = content["text"].encode("utf-8")
            relative = item["uri"].removeprefix("skill://unity-mcp-orchestrator/")
            canonical = (ROOT / "unity-mcp-skill" / relative).read_bytes().replace(b"\r\n", b"\n")
            assert raw == canonical
            assert len(raw) == item["size"]
            assert "sha256:" + hashlib.sha256(raw).hexdigest() == item["digest"]
        assert reads == [item["uri"] for item in resources]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["checksum", "file_digest", "file_size", "name", "missing_main", "case_collision"]
)
async def test_invalid_bundle_refuses_the_whole_optional_publication(bare_skills_server, kind):
    # Given: accidental corruption or an invalid metadata/file-set record.
    from transport.skills_extension import install_skills

    record = packaged_record()
    if kind == "checksum":
        record["sha256"] = "0" * 64
        encoded = json.dumps(record).encode()
    else:
        payload = record["payload"]
        if kind == "file_digest":
            payload["files"][0]["digest"] = "sha256:" + "0" * 64
        elif kind == "file_size":
            payload["files"][0]["size"] += 1
        elif kind == "name":
            payload["frontmatter"]["name"] = "unexpected-skill"
        elif kind == "missing_main":
            payload["files"] = [item for item in payload["files"] if item["path"] != "SKILL.md"]
        else:
            alias = copy.deepcopy(payload["files"][0])
            alias["path"] = alias["path"].swapcase()
            payload["files"].append(alias)
        encoded = encode_record(record)
    # When: the installer validates everything before registering methods/resources.
    assert install_skills(bare_skills_server, bundle_bytes=encoded) is None
    # Then: ordinary authenticated SDK behavior remains and no partial skill set is advertised.
    async with skills_http(bare_skills_server) as client:
        discovery = complete_result(await skill_request(client, "server/discover"))
        assert EXTENSION not in discovery["capabilities"].get("extensions", {})
        listed = complete_result(await skill_request(client, "resources/list"))
        assert not any(item["uri"].startswith("skill://") for item in listed["resources"])
        called = await skill_request(
            client, "tools/call", {"name": "ordinary_alive", "arguments": {}}
        )
        assert called.status_code == 200
        assert _message(called)["result"].get("isError", False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bytes", "depth", "nodes"])
async def test_result_metadata_is_admitted_before_optional_publication(
    bare_skills_server, monkeypatch, kind
):
    # Given: valid sealed records whose metadata exceeds one existing projection limit.
    import transport.skills_extension as module

    record = packaged_record()
    if kind == "bytes":
        monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 40_000)
        record["payload"]["frontmatter"]["extra"] = "x" * 10_000
    elif kind == "depth":
        value = "leaf"
        for _ in range(66):
            value = {"nested": value}
        record["payload"]["frontmatter"]["extra"] = value
    else:
        record["payload"]["frontmatter"]["extra"] = [None] * 100_001
    # When: both list/get projections are checked with the shared response-size helper.
    installed = module.install_skills(bare_skills_server, bundle_bytes=encode_record(record))
    # Then: failed admission publishes neither capability nor resources.
    assert installed is None
    async with skills_http(bare_skills_server) as client:
        discovery = complete_result(await skill_request(client, "server/discover"))
        assert EXTENSION not in discovery["capabilities"].get("extensions", {})
        listed = complete_result(await skill_request(client, "resources/list"))
        assert not any(item["uri"].startswith("skill://") for item in listed["resources"])


@pytest.mark.asyncio
async def test_admitted_snapshot_copies_are_isolated_and_actual_envelopes_fit(
    bare_skills_server, monkeypatch
):
    # Given: a smaller accepted byte ceiling and an immutable admitted record.
    import transport.skills_extension as module

    record = packaged_record()
    record["payload"]["frontmatter"]["extra"] = "x" * 4000
    original = copy.deepcopy(record)
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 40_000)
    assert module.install_skills(bare_skills_server, bundle_bytes=encode_record(record)) is not None
    async with skills_http(bare_skills_server) as client:
        # When: a caller mutates its returned metadata then asks again.
        first = complete_result(await skill_request(client, "skills/list"))
        first["skills"][0]["frontmatter"]["name"] = "caller-mutated"
        second_response = await skill_request(client, "skills/get", {"uri": SKILL_URI})
        second = complete_result(second_response)
    # Then: the retained snapshot did not mutate and real SDK overhead remains in reserve.
    assert second["skill"]["frontmatter"] == original["payload"]["frontmatter"]
    assert len(second_response.content) < 40_000


@pytest.mark.asyncio
@pytest.mark.parametrize("projection", ["skills", "skill"], ids=["list", "get"])
async def test_each_projection_honors_shared_retained_memory_refusal(
    bare_skills_server, monkeypatch, projection
):
    # Given: the real shared estimator, with a deliberately exhausted retained-memory boundary.
    import transport.skills_extension as module

    original = module.response_size
    seen = []

    def retained_refusal(value, **kwargs):
        if isinstance(value, dict) and projection in value:
            seen.append(kwargs)
            return original(value, max_retained=0, **kwargs)
        return original(value, **kwargs)

    monkeypatch.setattr(module, "response_size", retained_refusal)
    # When: only one of the two immutable method projections cannot be admitted.
    installed = module.install_skills(
        bare_skills_server, bundle_bytes=encode_record(packaged_record())
    )
    # Then: either refusal prevents the entire publication, using the existing envelope reserve.
    assert installed is None
    assert seen and all(item["max_bytes"] == module.MAX_RESPONSE_BYTES - 32_768 for item in seen)
    async with skills_http(bare_skills_server) as client:
        discovery = complete_result(await skill_request(client, "server/discover"))
        assert EXTENSION not in discovery["capabilities"].get("extensions", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["skills/get", "resources/read"])
@pytest.mark.parametrize(
    "suffix",
    [
        "missing.md",
        "../outside.md",
        "%2e%2e/outside.md",
        "%252e%252e/outside.md",
        "references\\guide.md",
        "/absolute.md",
        "C:/outside.md",
        "SKILL.md?query=1",
        "SKILL.md#fragment",
        "references/../SKILL.md",
        "references/%2e%2e/SKILL.md",
        "nul\x00.md",
    ],
)
async def test_unlisted_or_unsafe_uris_never_read_files(skills_server, method, suffix):
    # Given: only the seven immutable advertised resource URIs are served.
    uri = "skill://unity-mcp-orchestrator/" + suffix
    async with skills_http(skills_server) as client:
        # When: callers provide malformed, traversal, encoded or nonmember URIs.
        response = await skill_request(client, method, {"uri": uri})
    # Then: the real protocol boundary refuses; no fallback filesystem path is opened.
    message = _message(response)
    assert response.status_code == 400, message
    assert message["error"]["code"] == -32602, message


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "stale-launch-token"])
async def test_existing_local_authentication_precedes_skill_methods(skills_server, token):
    # Given: a valid installed extension but no current authenticated local control token.
    async with skills_http(skills_server) as client:
        # When: discovery metadata cannot substitute for HTTP authentication.
        response = await skill_request(
            client, "skills/get", {"uri": SKILL_URI}, headers={LOCAL_AUTH_HEADER: token}
        )
    # Then: ordinary auth refusal occurs before extension dispatch.
    assert response.status_code == 401
    assert "jsonrpc" not in response.json()


@pytest.mark.asyncio
async def test_hosted_guidance_is_static_across_credentials_without_an_editor(
    hosted_catalog, monkeypatch
):
    # Given: two authenticated hosted principals and no registered Unity session.
    from transport.plugin_hub import PluginHub

    app, _ = hosted_catalog
    commands = AsyncMock(side_effect=AssertionError("Guidance must not dispatch a Unity command"))
    monkeypatch.setattr(PluginHub, "send_command", commands)
    results = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
            headers={"Accept": "application/json, text/event-stream"},
        ) as client:
            # When: principals interleave direct lookup and ordinary file reads without a selector.
            for principal in ("alice-key", "bob-key", "alice-key"):
                headers = {"x-api-key": principal}
                entry = complete_result(
                    await skill_request(client, "skills/get", {"uri": SKILL_URI}, headers=headers)
                )["skill"]
                content = complete_result(
                    await skill_request(
                        client, "resources/read", {"uri": SKILL_URI}, headers=headers
                    )
                )["contents"]
                results.append((entry, content))
    # Then: the exact package-only guidance is independent of previous tenant/instance state.
    assert results[0] == results[1] == results[2]
    commands.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_legacy_keeps_resource_fallback_without_skills_methods(skills_server):
    # Given: an explicitly legacy initialized caller using existing authenticated HTTP.
    async with skills_http(skills_server) as client:
        initialized = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "wire-request",
                "method": "initialize",
                "params": {
                    "protocolVersion": LEGACY_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "skills-legacy", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200
        headers = {"MCP-Protocol-Version": LEGACY_VERSION}
        # When: a legacy client requests the modern extension or its ordinary resource fallback.
        results = []
        for method, params in (("skills/list", {}), ("skills/get", {"uri": SKILL_URI})):
            response = await client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": "wire-request", "method": method, "params": params},
            )
            results.append(_message(response))
        resource = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "wire-request",
                "method": "resources/read",
                "params": {"uri": SKILL_URI},
            },
        )
    # Then: method-version gating preserves old clients and the same plain resource bytes.
    assert all(result["error"]["code"] == -32601 for result in results)
    assert resource.status_code == 200
    assert _message(resource)["result"]["contents"][0]["uri"] == SKILL_URI


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["class", "version", "asset", "signature"])
async def test_optional_support_failure_preserves_authenticated_ordinary_calls(
    bare_skills_server, monkeypatch, tmp_path, unavailable
):
    # Given: an unavailable optional public hook/version/asset, without SDK mutation on disk.
    import fastmcp.server.extensions as sdk
    import transport.skills_extension as module

    if unavailable == "class":
        monkeypatch.delattr(sdk, "MethodBinding")
    elif unavailable == "version":
        monkeypatch.setattr(module.metadata, "version", lambda _name: "unreviewed")
    elif unavailable == "asset":
        monkeypatch.setattr(module.resources, "files", lambda _package: tmp_path)
    else:
        monkeypatch.setattr(
            module, "signature", lambda _callable: type("Shape", (), {"parameters": {}})()
        )
    # When: installation evaluates its public compatibility boundary.
    assert module.install_skills(bare_skills_server) is None
    # Then: no false capability and no default-server startup/call regression.
    async with skills_http(bare_skills_server) as client:
        discovery = complete_result(await skill_request(client, "server/discover"))
        assert EXTENSION not in discovery["capabilities"].get("extensions", {})
        result = _message(
            await skill_request(client, "tools/call", {"name": "ordinary_alive", "arguments": {}})
        )["result"]
        assert not result.get("isError", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [512, 513])
async def test_exact_manifest_count_boundary_is_atomic(bare_skills_server, count):
    # Given: valid sealed snapshots at and one file beyond the interoperability ceiling.
    from transport.skills_extension import install_skills

    record = packaged_record()
    files = record["payload"]["files"]
    for number in range(count - len(files)):
        item = copy.deepcopy(files[0])
        item["path"] = f"references/extra_{number}.md"
        files.append(item)
    # When: install the entire snapshot once.
    installed = install_skills(bare_skills_server, bundle_bytes=encode_record(record))
    # Then: exactly 512 is available; 513 publishes neither extension nor resources.
    assert (installed is not None) == (count == 512)
    async with skills_http(bare_skills_server) as client:
        discovery = complete_result(await skill_request(client, "server/discover"))
        assert (EXTENSION in discovery["capabilities"].get("extensions", {})) == (count == 512)
        if count == 512:
            result = complete_result(await skill_request(client, "skills/list"))
            assert len(result["skills"][0]["resources"]) == count
        else:
            result = complete_result(await skill_request(client, "resources/list"))
            assert result["resources"] == []


@pytest.mark.parametrize("limit", ["bundle", "bytes"])
def test_raw_bundle_and_decoded_file_limits_refuse_before_publication(
    bare_skills_server, monkeypatch, limit
):
    # Given: the same otherwise-valid artifact with one exhausted admission budget.
    import transport.skills_extension as module

    monkeypatch.setattr(module, "MAX_BUNDLE_BYTES" if limit == "bundle" else "MAX_SKILL_BYTES", 1)
    # When/Then: no method/resource publication can occur after this refusal.
    assert (
        module.install_skills(bare_skills_server, bundle_bytes=encode_record(packaged_record()))
        is None
    )


@pytest.mark.asyncio
async def test_cursor_and_directory_reads_are_not_implicitly_supported(skills_server):
    # Given: a single complete atomic entry, without directoryRead capability.
    async with skills_http(skills_server) as client:
        # When: callers request unsupported cursor/directory operations.
        cursor = _message(await skill_request(client, "skills/list", {"cursor": "next"}))
        directory = _message(
            await skill_request(
                client, "resources/directory/read", {"uri": "skill://unity-mcp-orchestrator/"}
            )
        )
    # Then: no partial or invented pagination/directory contract is returned.
    assert cursor["error"]["code"] == -32602
    assert directory["error"]["code"] == -32601


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, -1], ids=["exact-byte-budget", "one-byte-over"])
async def test_result_byte_boundary_uses_existing_envelope_reserve(
    skills_server, bare_skills_server, monkeypatch, offset
):
    # Given: independently measured real SDK projections from the production snapshot.
    import transport.skills_extension as module

    projections = []
    async with skills_http(skills_server) as client:
        for method, params in (("skills/list", None), ("skills/get", {"uri": SKILL_URI})):
            result = complete_result(await skill_request(client, method, params))
            result.pop("_meta")  # SDK-added serverInfo is covered by the envelope reserve.
            # The existing shared estimator uses JSONEncoder's default separators;
            # the compact SDK envelope is smaller. Preserve that ordinary policy.
            projections.append(len(json.dumps(result, ensure_ascii=False).encode("utf-8")))
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", max(projections) + 32_768 + offset)
    # When: admit exactly that handler projection budget or one byte less.
    installed = module.install_skills(bare_skills_server)
    # Then: byte refusal is atomic, while the exact admitted actual envelope fits.
    assert (installed is not None) == (offset == 0)
    if installed is not None:
        async with skills_http(bare_skills_server) as client:
            response = await skill_request(client, "skills/list")
            complete_result(response)
            assert len(response.content) < module.MAX_RESPONSE_BYTES


@pytest.mark.asyncio
async def test_resource_manifest_covers_exact_utf8_and_newline_bytes(bare_skills_server):
    # Given: a valid synthetic package blob with non-ASCII text and committed CRLF.
    from transport.skills_extension import install_skills

    record = packaged_record()
    item = next(file for file in record["payload"]["files"] if file["path"] != "SKILL.md")
    raw = "UTF-8: 한글 é\r\nsecond line\n".encode("utf-8")
    item.update(
        data=base64.b64encode(raw).decode("ascii"),
        size=len(raw),
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    assert install_skills(bare_skills_server, bundle_bytes=encode_record(record)) is not None
    # When: a real authenticated modern caller looks up and reads that advertised file.
    async with skills_http(bare_skills_server) as client:
        entry = complete_result(await skill_request(client, "skills/get", {"uri": SKILL_URI}))[
            "skill"
        ]
        descriptor = next(file for file in entry["resources"] if file["uri"].endswith(item["path"]))
        content = complete_result(
            await skill_request(client, "resources/read", {"uri": descriptor["uri"]})
        )["contents"][0]
    # Then: HTTP/SDK resource serialization has not normalized the manifest's bytes.
    assert content["text"].encode("utf-8") == raw
    assert descriptor["size"] == len(raw)
    assert descriptor["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
