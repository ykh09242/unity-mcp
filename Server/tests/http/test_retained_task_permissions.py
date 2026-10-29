"""Retained Tasks use current grants at the real authenticated HTTP/SDK boundary."""

import time

import httpx
import pytest
from fastmcp import FastMCP

from core.config import config
from services.api_key_service import ApiKeyService, ValidationResult
from services.test_wait_journal import DeadlineCheckpoint, TaskNotFound, WaitOperation, WaitRecord
from transport import test_wait_binding as binding
from transport.remote_auth_middleware import RemoteControlAuthMiddleware
from transport.response_limit_middleware import ResponseLimitMiddleware
from transport.test_job_tasks import WaitTaskDeliveryMiddleware
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["working", "completed"])
async def test_retained_task_requires_current_read_grants(monkeypatch, tmp_path, status):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    grants = {"reader": ["read"], "empty": [], "downgraded": ["read"], "foreign": ["read"]}
    validator = ApiKeyService("https://fixture.invalid/validate", cache_ttl=300)

    async def validate(key):
        return ValidationResult(
            valid=True,
            user_id="bob" if key == "foreign" else "alice",
            metadata={"unity_mcp_permissions": grants[key]},
        )

    monkeypatch.setattr(validator, "_validate_external", validate)
    now = time.time()
    operation = WaitOperation(
        owner="hosted:v1:alice",
        user_id="alice",
        project="hash",
        instance="Fixture@hash",
        session_id="session",
        job_id="owned-job",
        wait=DeadlineCheckpoint.capture(1, now=now),
        outer=DeadlineCheckpoint.capture(2, now=now),
    )
    retained = {"content": [], "structuredContent": {"output": "synthetic retained output"}}
    record = WaitRecord(
        task_id="a" * 32,
        operation=operation,
        created_at=now,
        updated_at=now,
        expires_at=now + 3600,
        status=status,
        result=retained if status == "completed" else None,
    )
    reads = []

    class MemoryJournal:
        local_owner = "local:fixture"

        def get(self, task_id, principal):
            reads.append(principal)
            if task_id != record.task_id or principal != operation.owner:
                raise TaskNotFound()
            return record

        def close(self):
            return None

    monkeypatch.setattr(binding, "WaitJournal", lambda _root: MemoryJournal())
    server = FastMCP("retained-task-authority")
    server.add_middleware(UnityInstanceMiddleware())
    server.add_middleware(ResponseLimitMiddleware())

    @server.tool
    async def get_test_job(job_id: str) -> dict:
        pytest.fail("Permission refusal must precede the ordinary handler")

    runtime = binding.install_test_job_tasks(server, tmp_path, remote_hosted=True)
    assert runtime is not None
    sdk = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    app = RemoteControlAuthMiddleware(WaitTaskDeliveryMiddleware(sdk))

    async def exchange(client, key, *, ordinary=False):
        method = "tools/call" if ordinary else "tasks/get"
        params = (
            {"name": "get_test_job", "arguments": {"job_id": "owned-job"}}
            if ordinary
            else {"taskId": record.task_id}
        )
        response = await client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": method,
                "Mcp-Name": "get_test_job" if ordinary else record.task_id,
                "x-api-key": key,
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": "retained",
                "method": method,
                "params": {
                    **params,
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {"name": "fixture", "version": "1"},
                        "io.modelcontextprotocol/clientCapabilities": {
                            "extensions": {"io.modelcontextprotocol/tasks": {}}
                        },
                    },
                },
            },
        )
        assert response.status_code in {200, 400}
        return response.json()

    try:
        async with sdk.router.lifespan_context(sdk):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://fixture", trust_env=False
            ) as client:
                for key in ("reader", "downgraded"):
                    result = (await exchange(client, key))["result"]
                    assert result["status"] == status
                    assert result.get("result") == record.result
                assert reads == [operation.owner, operation.owner]
                ordinary = await exchange(client, "empty", ordinary=True)
                assert "error" in ordinary

                grants["downgraded"] = []
                await validator.invalidate_cache("downgraded")
                for key in ("empty", "downgraded"):
                    denied = await exchange(client, key)
                    assert "permission" in denied["error"]["message"].lower()
                    assert "synthetic retained output" not in str(denied)
                    assert "status" not in str(denied)
                assert reads == [operation.owner, operation.owner]
                foreign = await exchange(client, "foreign")
                assert foreign["error"]["message"] == "Task not found"
                assert reads[-1] == "hosted:v1:bob"
    finally:
        await runtime.close()
        await validator.aclose()
    assert not runtime.delivery_admission.active
    assert runtime._method_owners == runtime._io_owners == 0
    assert validator._lease_owners == 0
