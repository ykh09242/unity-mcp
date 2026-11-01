"""Private source-scenario worker; application state is fresh for every real SDK call."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import gc
import hmac
import io
import json
import logging
import os
from pathlib import Path
import random
import secrets
import socket
import sys
import threading
import time
import traceback
import warnings

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]
_CLOCK = time.perf_counter
_CPU = time.process_time
_STARTED, _CPU_STARTED = _CLOCK(), _CPU()

from fastmcp import Client, FastMCP  # noqa: E402,F401
import fastmcp  # noqa: E402
import fastmcp.server.middleware.dereference as dereference  # noqa: E402
from fastmcp.server.dependencies import without_injected_parameters  # noqa: E402
from fastmcp.server.sessions import session_id_parameter_names  # noqa: E402
from fastmcp.tools.function_tool import _wrap_body_errors  # noqa: E402
from fastmcp.utilities.types import get_cached_typeadapter  # noqa: E402
from uncalled_for.introspection import _parameter_cache, _signature_cache  # noqa: E402

_ABSENT = object()
_GLOBALS = [
    (socket.socket, "connect", socket.socket.connect),
    (socket.socket, "connect_ex", socket.socket.connect_ex),
    (socket, "create_connection", socket.create_connection),
    (Path, "home", vars(Path).get("home", _ABSENT)),
    (time, "monotonic", time.monotonic),
    (time, "sleep", time.sleep),
    (random, "uniform", random.uniform),
    (secrets, "token_hex", secrets.token_hex),
    (hmac, "compare_digest", hmac.compare_digest),
    (dereference, "dereference_refs", dereference.dereference_refs),
]
_SETTINGS_OBJECT = fastmcp.settings
_SETTINGS = deepcopy(vars(_SETTINGS_OBJECT))
_PATH = sys.path[:]
_WARNINGS = warnings.filters[:]
_THREADS = set(threading.enumerate())
_ENV = dict(os.environ)
_PROTOCOL = sys.stdout
_INPUT = sys.stdin
_CALLABLE_CACHES = (
    get_cached_typeadapter,
    without_injected_parameters,
    session_id_parameter_names,
    _wrap_body_errors,
)


class _Output(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 1024 * 1024:
            raise ValueError("SDK scenario exceeded its output limit")
        return super().write(value)


def _restore_globals():
    for target, name, value in _GLOBALS:
        if value is _ABSENT:
            if name in vars(target):
                delattr(target, name)
        else:
            setattr(target, name, value)


def _reset():
    _restore_globals()
    fastmcp.settings = _SETTINGS_OBJECT
    vars(_SETTINGS_OBJECT).clear()
    vars(_SETTINGS_OBJECT).update(deepcopy(_SETTINGS))
    warnings.filters[:] = _WARNINGS
    sys.path[:] = _PATH
    os.chdir(_ROOT)
    sys.argv[:] = ["-c"]
    sys.stdin = _INPUT
    for name in list(sys.modules):
        if name.split(".", 1)[0] in {
            "core",
            "models",
            "services",
            "transport",
            "utils",
            "cli",
            "tests",
            "main",
        }:
            del sys.modules[name]
    # These four SDK LRUs key on dynamic callables whose globals retain the old app.
    # Recompile/rewrap every case; fail visibly if the pinned SDK changes this interface.
    for cache in _CALLABLE_CACHES:
        cache.cache_clear()
    # FastMCP's DI signature keys also hold each scenario function and its globals.
    _parameter_cache.clear()
    _signature_cache.clear()
    gc.collect()


def _execute(request):
    started, cpu = _CLOCK(), _CPU()
    output, errors = _Output(), _Output()
    status = 0
    loggers = [
        logging.getLogger(),
        *[
            logger
            for logger in logging.root.manager.loggerDict.values()
            if isinstance(logger, logging.Logger)
        ],
    ]
    handlers = {logger: (logger.handlers[:], logger.level, logger.propagate) for logger in loggers}
    try:
        _reset()
        env = request["env"]
        startup_keys = {"PY_KEY_VALUE_DISABLE_BEARTYPE", "PYTHONWARNINGS", "PYTHONHASHSEED"}
        assert {
            key: value
            for key, value in env.items()
            if key.startswith("FASTMCP_") or key in startup_keys
        } == {
            key: value
            for key, value in _ENV.items()
            if key.startswith("FASTMCP_") or key in startup_keys
        }, "SDK startup-setting changes require a fresh process"
        os.environ.clear()
        os.environ.update(env)
        with warnings.catch_warnings(), redirect_stdout(output), redirect_stderr(errors):
            namespace = {"__name__": "__main__"}
            exec(compile(request["source"], "<sdk-scenario>", "exec"), namespace)
            del namespace
    except BaseException:
        status = 1
        errors.write(traceback.format_exc())
    finally:
        for logger in [
            logging.getLogger(),
            *[
                value
                for value in logging.root.manager.loggerDict.values()
                if isinstance(value, logging.Logger)
            ],
        ]:
            prior, level, propagate = handlers.get(logger, ([], logging.NOTSET, True))
            for handler in logger.handlers:
                if handler not in prior:
                    handler.close()
            logger.handlers[:] = prior
            logger.setLevel(level)
            logger.propagate = propagate
        _reset()
        os.environ.clear()
        os.environ.update(_ENV)
        settled_by = _CLOCK() + 0.5
        for thread in set(threading.enumerate()) - _THREADS:
            thread.join(timeout=max(0, settled_by - _CLOCK()))
            if thread.is_alive():
                status = 1
                errors.write("SDK scenario retained a live thread: " + thread.name)
    return {
        "id": request["id"],
        "returncode": status,
        "stdout": output.getvalue(),
        "stderr": errors.getvalue(),
        "timing": {"pid": os.getpid(), "wall": _CLOCK() - started, "cpu": _CPU() - cpu},
    }


def main():
    _PROTOCOL.write(
        json.dumps(
            {
                "ready": True,
                "pid": os.getpid(),
                "startup_wall": _CLOCK() - _STARTED,
                "startup_cpu": _CPU() - _CPU_STARTED,
            }
        )
        + "\n"
    )
    _PROTOCOL.flush()
    for line in _INPUT:
        if len(line) > 4 * 1024 * 1024:
            raise ValueError("SDK scenario exceeded its input limit")
        result = _execute(json.loads(line))
        _PROTOCOL.write(json.dumps(result) + "\n")
        _PROTOCOL.flush()
        if result["returncode"]:
            break


if __name__ == "__main__":
    main()
