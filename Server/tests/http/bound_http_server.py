"""Launch a wire-test CLI with an OS-assigned listener owned by this child."""

from contextlib import nullcontext
from functools import partialmethod
import json
import os
from pathlib import Path
import runpy
import sys
from unittest.mock import patch


ENDPOINT_ENV = "UNITY_MCP_TEST_BOUND_HTTP_ENDPOINT"


def reported_port(path: Path, pid: int) -> int | None:
    try:
        endpoint = json.loads(path.read_text(encoding="ascii"))
    except FileNotFoundError:
        return None
    port = endpoint["port"]
    if endpoint["pid"] != pid or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid endpoint reported by the HTTP fixture child")
    return port


def main() -> None:
    import main as entry
    from core.local_auth import local_http_sockets

    module, host, *options = sys.argv[1:]
    report = Path(os.environ.pop(ENDPOINT_ENV))
    with local_http_sockets(host, 0) as listeners:
        port = listeners[0].getsockname()[1]
        sys.argv = [
            module,
            "--transport",
            "http",
            "--http-host",
            host,
            "--http-port",
            str(port),
            *options,
        ]

        def borrow(requested_host, requested_port):
            if (requested_host, requested_port) != (host, port):
                raise ValueError("CLI requested a different listener from the HTTP fixture")
            return nullcontext(listeners)

        # Local startup obtains its sockets before publishing the launch credential.
        # Hosted startup normally leaves binding to the runner, which also accepts sockets.
        if "--http-remote-hosted" in options:
            handoff = patch.object(
                entry.UnityMCP, "run", partialmethod(entry.UnityMCP.run, sockets=listeners)
            )
        else:
            handoff = patch.object(entry, "local_http_sockets", borrow)
        with handoff:
            staging = report.with_suffix(".tmp")
            staging.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="ascii")
            staging.replace(report)
            if module == "main":
                entry.main()
            else:
                runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
