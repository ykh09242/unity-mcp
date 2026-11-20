"""Run a managed CLI contender, waiting immediately before its real socket acquisition."""

import json
import os
import sys
import traceback
from pathlib import Path


def run(ready_file: Path) -> None:
    import main as server
    from core.local_auth import local_http_sockets

    def gated_sockets(host, port):
        ready_file.write_text(str(os.getpid()), encoding="ascii")
        if sys.stdin.buffer.read(1) != b"1":
            raise RuntimeError("The parent closed the launch gate")
        return local_http_sockets(host, port)

    server.local_http_sockets = gated_sockets
    server.main()


if __name__ == "__main__":
    ready = Path(os.environ["LAUNCH_READY_FILE"])
    try:
        run(ready)
    except BaseException as error:
        # A child can exit before the parent observes discovery. Preserve its
        # exception site and OS error without emitting messages or credentials.
        frame = traceback.extract_tb(error.__traceback__)[-1]
        ready.with_name("failure.json").write_text(
            json.dumps(
                {
                    "exception": type(error).__name__,
                    "file": Path(frame.filename).name,
                    "function": frame.name,
                    "line": frame.lineno,
                    "errno": getattr(error, "errno", None),
                    "winerror": getattr(error, "winerror", None),
                }
            ),
            encoding="ascii",
        )
        raise
