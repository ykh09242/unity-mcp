"""Whole-operation accounting preserves the supported 1000-command boundary."""

import pytest

from services.registry import operation_invocation as invocation
from services.registry.operation_context import scheduler
from services.registry.operation_scheduler import OperationScope
from services.registry.operation_target import OperationTarget
from tests.test_batch_execute_limits import Context, batch_module, install_editor  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1000, 1001])
async def test_whole_tool_admission_preserves_large_batch_limit(batch_module, monkeypatch, count):
    sends, _ = install_editor(monkeypatch, batch_module, {"A@hash-a": 1000})

    async def target(*args, **kwargs):
        return OperationTarget(OperationScope("local", "hash-a", "g1"), lambda: True, True)

    async def instance(ctx):
        return ctx.instance

    monkeypatch.setattr(invocation, "capture_target", target)
    monkeypatch.setattr("services.tools.get_unity_instance_from_context", instance)
    call = invocation.wrap_tool(batch_module.batch_execute, "batch_execute", "batch_execute")
    commands = [{"tool": "read_console", "params": {"count": 1}} for _ in range(count)]
    if count == 1001:
        with pytest.raises(ValueError, match="1000"):
            await call(Context("A@hash-a"), commands)
        assert not any(command == "batch_execute" for _, command, _ in sends)
    else:
        assert (await call(Context("A@hash-a"), commands))["data"]["executed"] == count
        assert sends[-1][2]["commands"] == commands
    assert scheduler().snapshot() == {
        "instances": 0,
        "active": 0,
        "waiting": 0,
        "retained_bytes": 0,
    }
