"""Proxy configuration contracts; checked-in Caddy assertions do not execute Caddy."""

from ipaddress import ip_address, ip_network
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from core.config import config, parse_trusted_proxy_cidrs


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "0.0.0.0/0",
        "::/0",
        "0.0.0.0/1,128.0.0.0/1",
        "::/1,8000::/1",
        "not-an-ip",
        "fe80::1%eth0/128",
        "127.0.0.1/24",
        "127.0.0.1,",
        ",",
    ],
)
def test_proxy_trust_rejects_invalid_and_catch_all_networks(value):
    with pytest.raises(ValueError, match="UNITY_MCP_AUTH_TRUSTED_PROXY_CIDRS"):
        parse_trusted_proxy_cidrs(value)


def test_proxy_trust_defaults_to_no_peers_and_supports_explicit_ipv4_ipv6():
    assert parse_trusted_proxy_cidrs("") == ()
    assert parse_trusted_proxy_cidrs("  ") == ()
    assert parse_trusted_proxy_cidrs("127.0.0.1, 2001:db8:1::/64") == (
        ip_network("127.0.0.1/32"),
        ip_network("2001:db8:1::/64"),
    )


@pytest.mark.parametrize("remote", [True, False])
def test_sync_runner_protects_remote_socket_peer_without_changing_local_options(
    monkeypatch, remote
):
    from fastmcp import FastMCP
    from main import UnityMCP

    monkeypatch.setattr(config, "http_remote_hosted", remote)
    runner = AsyncMock()
    monkeypatch.setattr(FastMCP, "run_http_async", runner)
    settings = {"proxy_headers": True, "timeout_keep_alive": 3}
    UnityMCP("runner-proxy-contract").run(
        transport="http", show_banner=False, uvicorn_config=settings
    )
    passed_settings = runner.call_args.args[6]
    assert passed_settings == {"proxy_headers": not remote, "timeout_keep_alive": 3}
    assert settings == {"proxy_headers": True, "timeout_keep_alive": 3}


def test_shipped_topology_trusts_only_the_fixed_proxy_and_overwrites_client_metadata():
    root = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load((root / "docker-compose.remote.yml").read_text(encoding="utf-8"))
    backend = compose["services"]["unity-mcp-server"]
    proxy = compose["services"]["proxy"]
    trusted = parse_trusted_proxy_cidrs(
        backend["environment"]["UNITY_MCP_AUTH_TRUSTED_PROXY_CIDRS"]
    )
    proxy_ip = ip_address(proxy["networks"]["remote_backend"]["ipv4_address"])
    assert trusted == (ip_network(str(proxy_ip) + "/32"),)
    assert proxy_ip in ip_network(
        compose["networks"]["remote_backend"]["ipam"]["config"][0]["subnet"]
    )
    assert backend["networks"] == ["remote_backend"]
    assert "ports" not in backend
    caddy = (root / "Server/deploy/Caddyfile.remote").read_text(encoding="utf-8")
    directives = {line.strip() for line in caddy.splitlines() if not line.lstrip().startswith("#")}
    assert "header_up X-Unity-MCP-Client-IP {remote_host}" in directives
    assert "header_up X-Forwarded-For {remote_host}" in directives
    assert "header_up -Forwarded" in directives
    assert "header_up -X-Real-IP" in directives
