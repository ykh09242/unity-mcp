"""Real SDK execution, external-state isolation and actual interpreter ownership."""

import json
from importlib.metadata import version
import subprocess
import sys

import pytest

from tests.sdk_scenarios import run_sdk_scenario, sdk_scenario_host  # noqa: F401


def test_real_sdk_state_and_callable_graphs_do_not_survive_a_scenario():
    first = run_sdk_scenario("""
        import builtins, hmac, json, logging, os, random, secrets, socket, sys, time, warnings, weakref
        from importlib.metadata import version
        from pathlib import Path
        import anyio
        from fastmcp import Client, FastMCP
        import fastmcp
        import fastmcp.server.middleware.dereference as dereference
        from services.custom_tool_service import CustomToolService
        from transport.plugin_hub import PluginHub
        from core.config import config
        app = FastMCP("real-isolation-control")
        service = CustomToolService(app)
        @app.tool
        async def echo(value: int) -> int:
            return value
        async def scenario():
            async with Client(app, mode="legacy") as client:
                assert (await client.call_tool("echo", {"value": 7})).data == 7
        anyio.run(scenario)
        print(json.dumps({"prefix":sys.prefix, "python":sys.version, "sdk":version("fastmcp")}))
        builtins._sdk_case_app_ref = weakref.ref(app)
        service._project_tools["poison"] = {}
        PluginHub._pending["poison"] = {}
        config.transport_mode = "poison"
        socket.socket.connect = lambda *args: None
        socket.socket.connect_ex = lambda *args: 17
        socket.create_connection = lambda *args: None
        Path.home = classmethod(lambda cls: Path("poison"))
        time.monotonic = lambda: -1
        time.sleep = lambda seconds: None
        random.uniform = lambda *args: -1
        secrets.token_hex = lambda count: "poison"
        hmac.compare_digest = lambda *args: False
        dereference.dereference_refs = lambda value: "poison"
        fastmcp.settings.log_level = "CRITICAL"
        os.environ["SDK_SCENARIO_POISON"] = "present"
        warnings.filterwarnings("ignore", message="sdk-scenario-poison")
        logger = logging.getLogger("sdk-isolation-control")
        logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.CRITICAL)
        leaked_namespace_value = "poison"
    """)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout) == {
        "prefix": sys.prefix,
        "python": sys.version,
        "sdk": version("fastmcp"),
    }
    second = run_sdk_scenario("""
        import builtins, hmac, logging, os, random, secrets, socket, time, warnings
        from pathlib import Path
        import fastmcp
        import fastmcp.server.middleware.dereference as dereference
        from services.custom_tool_service import CustomToolService
        from transport.plugin_hub import PluginHub
        from core.config import config
        assert CustomToolService._instance is None
        assert PluginHub._pending == {}
        assert config.transport_mode != "poison"
        assert Path.home() != Path("poison")
        assert socket.socket.connect.__name__ == "connect"
        assert socket.socket.connect_ex.__name__ == "connect_ex"
        assert socket.create_connection.__name__ == "create_connection"
        assert time.monotonic() > 0 and time.sleep.__name__ == "sleep"
        assert random.uniform(0, 1) >= 0
        assert secrets.token_hex(4) != "poison", "secrets.token_hex was retained"
        assert hmac.compare_digest(b"same", b"same") is True, "hmac.compare_digest was retained"
        assert dereference.dereference_refs.__name__ == "dereference_refs"
        assert fastmcp.settings.log_level != "CRITICAL"
        assert "SDK_SCENARIO_POISON" not in os.environ
        assert "leaked_namespace_value" not in globals()
        assert not any(getattr(row[1], "pattern", "") == "sdk-scenario-poison" for row in warnings.filters)
        logger = logging.getLogger("sdk-isolation-control")
        assert not logger.handlers and logger.level == logging.NOTSET
        assert builtins._sdk_case_app_ref() is None, "prior SDK callable retained its application"
        del builtins._sdk_case_app_ref
    """)
    assert second.returncode == 0, second.stderr


def test_path_home_restoration_preserves_descriptor_inheritance_and_ownership():
    result = run_sdk_scenario("""
        from inspect import getattr_static
        from pathlib import Path
        from tests import sdk_scenario_worker as worker
        class InheritedPath(type(Path())):
            pass
        class OwnedPath(type(Path())):
            @classmethod
            def home(cls):
                return cls("original")
        for path_type in (Path, InheritedPath, OwnedPath):
            descriptor = getattr_static(path_type, "home")
            owner = next(base for base in path_type.__mro__ if "home" in vars(base))
            owns = "home" in vars(path_type)
            expected = path_type.home()
            worker._GLOBALS = [(path_type, "home", vars(path_type).get("home", worker._ABSENT))]
            path_type.home = classmethod(lambda cls: cls("poison"))
            assert path_type.home() == path_type("poison")
            worker._restore_globals()
            assert ("home" in vars(path_type)) is owns
            assert getattr_static(path_type, "home") is descriptor
            assert next(base for base in path_type.__mro__ if "home" in vars(base)) is owner
            assert path_type.home() == expected
    """)
    assert result.returncode == 0, result.stderr


def test_failed_scenario_returns_its_traceback_and_discards_the_process(sdk_scenario_host):
    failed = run_sdk_scenario("""
        import os
        from fastmcp import FastMCP
        app = FastMCP("intentional-failure")
        print(os.getpid())
        raise AssertionError("intentional SDK scenario failure")
    """)
    assert failed.returncode != 0 and "intentional SDK scenario failure" in failed.stderr
    prior_pid = int(failed.stdout.strip())
    fresh = run_sdk_scenario("import os; from fastmcp import FastMCP; print(os.getpid())")
    assert fresh.returncode == 0, fresh.stderr
    assert int(fresh.stdout.strip()) != prior_pid
    assert sdk_scenario_host.process.pid == int(fresh.stdout.strip())


def test_timeout_reaps_the_actual_interpreter_and_next_case_is_fresh(sdk_scenario_host):
    warm = run_sdk_scenario("import os; from fastmcp import FastMCP; print(os.getpid())")
    assert warm.returncode == 0, warm.stderr
    process = sdk_scenario_host.process
    assert process.pid == int(warm.stdout.strip())
    readers = sdk_scenario_host.readers[:]
    with pytest.raises(subprocess.TimeoutExpired):
        run_sdk_scenario(
            "from fastmcp import FastMCP; import time; app=FastMCP('held'); time.sleep(60)",
            timeout=0.1,
        )
    assert process.poll() is not None
    assert not any(reader.is_alive() for reader in readers)
    fresh = run_sdk_scenario("import os; from fastmcp import FastMCP; print(os.getpid())")
    assert fresh.returncode == 0, fresh.stderr
    assert int(fresh.stdout.strip()) != process.pid


def test_scenario_output_limit_is_visible_and_does_not_corrupt_next_receipt():
    refused = run_sdk_scenario("print('x' * (1024 * 1024 + 1))")
    assert refused.returncode != 0 and "output limit" in refused.stderr
    fresh = run_sdk_scenario(
        "import json; from fastmcp import FastMCP; print(json.dumps({'fresh':True}))"
    )
    assert fresh.returncode == 0, fresh.stderr
    assert json.loads(fresh.stdout) == {"fresh": True}


def test_escaped_thread_refuses_reuse_and_discards_the_host():
    refused = run_sdk_scenario("""
        from fastmcp import FastMCP
        import threading, time
        app = FastMCP("escaped-thread-control")
        threading.Thread(target=time.sleep, args=(60,), daemon=True, name="escaped-control").start()
    """)
    assert refused.returncode != 0 and "retained a live thread: escaped-control" in refused.stderr
    fresh = run_sdk_scenario("from fastmcp import FastMCP; assert FastMCP('fresh').name == 'fresh'")
    assert fresh.returncode == 0, fresh.stderr
