import os
import sys
import types
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[2]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
SERVER_SRC = SERVER_ROOT / "src"
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

# Ensure telemetry is disabled during test collection and execution to avoid
# any background network or thread startup that could slow or block pytest.
os.environ.setdefault("DISABLE_TELEMETRY", "true")
os.environ.setdefault("UNITY_MCP_DISABLE_TELEMETRY", "true")
os.environ.setdefault("MCP_DISABLE_TELEMETRY", "true")

# NOTE: These tests are integration tests for the MCP server Python code.
# They test tools, resources, and utilities without requiring Unity to be running.
# Tests can now import directly from the parent package since they're inside src/
# To run: cd Server && uv run pytest tests/integration/ -v

# Stub telemetry modules to avoid file I/O during import of tools package
telemetry = types.ModuleType("telemetry")


def _noop(*args, **kwargs):
    pass


class MilestoneType:
    pass


telemetry.record_resource_usage = _noop
telemetry.record_tool_usage = _noop
telemetry.record_milestone = _noop
telemetry.MilestoneType = MilestoneType
telemetry.get_package_version = lambda: "0.0.0"
sys.modules.setdefault("telemetry", telemetry)

telemetry_decorator = types.ModuleType("telemetry_decorator")


def _noop_decorator(*_dargs, **_dkwargs):
    def _wrap(fn):
        return fn

    return _wrap


telemetry_decorator.telemetry_tool = _noop_decorator
telemetry_decorator.telemetry_resource = _noop_decorator
sys.modules.setdefault("telemetry_decorator", telemetry_decorator)

# FastMCP and MCP are installed project dependencies. Global SDK stubs here
# leak during collection and prevent neighboring tests from using the real Client.
