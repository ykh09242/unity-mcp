import pytest


@pytest.mark.asyncio
async def test_debug_request_context_includes_server_diagnostics(monkeypatch):
    # Import inside test so stubs in conftest are applied.
    import services.tools.debug_request_context as mod

    class DummyCtx:
        # minimal surface for debug_request_context
        request_context = None
        session_id = None
        client_id = None

        async def get_state(self, _k):
            return None

    from core.server_build import ServerBuild

    monkeypatch.setattr(mod, "RUNNING_SERVER", ServerBuild("9.9.9-test", "a" * 40, "b" * 32))

    res = await mod.debug_request_context(DummyCtx())
    assert res.get("success") is True
    data = res.get("data") or {}
    server = data.get("server") or {}
    assert server.get("version") == "9.9.9-test"
    assert server.get("source_commit") == "a" * 40
    assert server.get("server_id") == "b" * 32
    assert "restart" in server.get("version_guidance", "").lower()
    assert "cwd" not in server
    assert "argv" not in server
    detailed = (await mod.debug_request_context(DummyCtx(), detail="server"))["data"]
    assert "server process" in detailed["server"]["cwd_scope"]
    assert "argv" in detailed["server"]
    assert "request_context" not in detailed


@pytest.mark.asyncio
async def test_diagnostics_report_request_route_separately_from_client_and_session():
    from types import SimpleNamespace
    from services.tools.debug_request_context import debug_request_context

    class RoutedContext:
        request_context = SimpleNamespace(
            meta={
                "local_filesystem": "read-only",
                "nested": [{"Authorization": {"unexpected": "PRIVATE"}, "api-key": "PRIVATE"}],
                "cookie": ["PRIVATE"],
                "url": "https://example.invalid/?data=PRIVATE",
            }
        )
        session_id = None
        client_id = None

        async def get_state(self, key):
            return "Isolated@1234" if key == "unity_instance" else None

    data = (await debug_request_context(RoutedContext()))["data"]
    assert "request_context" not in data and "read-only" not in str(data)
    assert "available_attributes" not in data and "direct_properties" not in data
    assert data["session_state"]["active_instance"] is None
    assert data["editor_route"]["unity_instance"] == "Isolated@1234"
    assert data["editor_route"]["edit_authorization"] == "not_tested"
    detailed = (await debug_request_context(RoutedContext(), detail="client"))["data"]
    assert detailed["request_context"]["meta"]["local_filesystem"] == "read-only"
    assert "PRIVATE" not in str(detailed)
    assert "cwd" not in detailed["server"]


@pytest.mark.asyncio
async def test_debug_request_context_redacts_secret_argv(monkeypatch):
    """Any tenant can call this tool on a remote-hosted server, so the service token that
    the server was started with must not come back in the diagnostics."""
    import json
    import services.tools.debug_request_context as mod

    class DummyCtx:
        request_context = None
        session_id = None
        client_id = None

        async def get_state(self, _k):
            return None

    monkeypatch.setattr(
        mod.sys,
        "argv",
        [
            "mcp-for-unity",
            "--http-remote-hosted",
            "--api-key-service-token",
            "s3cret-service-token",
            "--api-key-validation-url=https://auth.example.com/validate?token=url-embedded",
            "--api-key-service-token-header",
            "X-Service-Token",
            "--http-port",
            "8080",
        ],
    )

    res = await mod.debug_request_context(DummyCtx(), detail="server")
    argv = res["data"]["server"]["argv"]
    dumped = json.dumps(argv)

    assert "s3cret-service-token" not in dumped
    assert "url-embedded" not in dumped
    # Flag names and non-secret values survive so the deployment shape stays debuggable.
    assert "--api-key-service-token" in argv
    assert "--http-remote-hosted" in argv
    assert argv[-2:] == ["--http-port", "8080"]

    monkeypatch.setattr(mod.config, "http_remote_hosted", True)
    remote = (await mod.debug_request_context(DummyCtx(), detail="server"))["data"]
    assert remote["server"]["process_details"] == "local_only"
    assert "cwd" not in remote["server"] and "argv" not in remote["server"]


def test_redact_argv_hides_the_value_even_when_it_starts_with_a_dash():
    import services.tools.debug_request_context as mod

    # Secret flags always take a value; a value that begins with "-" is still the secret.
    assert mod._redact_argv(["--token", "-abc123", "--verbose"]) == ["--token", "***", "--verbose"]
    assert mod._redact_argv(["--token", "abc", "--verbose"]) == ["--token", "***", "--verbose"]
    assert mod._redact_argv(["--password=hunter2"]) == ["--password=***"]
    assert mod._redact_argv(["--unity-i", "abc"]) == ["--unity-i", "***"]
    assert mod._redact_argv(["positional", "--port", "1"]) == ["positional", "--port", "1"]


@pytest.mark.parametrize("option", ["--http-url", "--http-u"])
@pytest.mark.parametrize("joined", [True, False])
def test_debug_argv_omits_sensitive_url_parts(option, joined):
    from services.tools.debug_request_context import _redact_argv

    url = "http://user:SYNTHETIC_PASSWORD@example.invalid:8080/private?signature=SYNTHETIC#fragment"
    args = [f"{option}={url}"] if joined else [option, url]
    expected = (
        [f"{option}=http://example.invalid:8080/"]
        if joined
        else [option, "http://example.invalid:8080/"]
    )
    assert _redact_argv(args) == expected
