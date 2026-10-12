"""Loopback-only dashboard for one explicitly configured local work program.

Run from the repository root:
    python tools/feature_program_web.py --repo . --program-dir .work/programs/default
Task views are read-only. The sole write endpoint saves answers to existing director
questions with a same-origin session token. Use --read-only to disable responses.
No Git command, native-agent action, network lookup or browser launch occurs here.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import secrets
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

try:
    from tools import feature_program, feature_program_questions
except ModuleNotFoundError:
    import feature_program
    import feature_program_questions

if TYPE_CHECKING:
    from feature_program import Json

ASSETS = Path(__file__).with_suffix("") / "generated"
STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class DashboardServer(ThreadingHTTPServer):
    """Serve only packaged assets and the configured snapshot provider."""

    daemon_threads = True

    def __init__(
        self,
        host: str,
        port: int,
        snapshot: Callable[[], Json],
        answer: Callable[[str, int, str, str], Json] | None = None,
    ):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Dashboard binding must be loopback (127.0.0.1 or ::1)")
        if host == "localhost":
            host = "127.0.0.1"
        if host == "::1":
            self.address_family = socket.AF_INET6
        self.snapshot = snapshot
        self.answer = answer
        self.nonce = secrets.token_urlsafe(32)
        super().__init__((host, port), DashboardHandler)
        actual_port = self.server_address[1]
        authority = f"[{host}]:{actual_port}" if host == "::1" else f"{host}:{actual_port}"
        self.authorities = {authority, f"localhost:{actual_port}"}
        self.url = f"http://{authority}/"

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


class DashboardHandler(BaseHTTPRequestHandler):
    """Reject unrelated origins and file paths before reading any program data."""

    server: DashboardServer
    server_version = "LocalWorkProgram/1"
    sys_version = ""

    def log_message(self, _format: str, *_args) -> None:
        # Request paths and headers can contain private or attacker-controlled text.
        return

    def _response(self, code: int, body: bytes, content_type: str, head: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; font-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _error(self, code: int, message: str, head: bool = False) -> None:
        self._response(
            code, json.dumps({"error": message}).encode(), "application/json; charset=utf-8", head
        )

    def _allowed(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0] not in self.server.authorities:
            return False
        expected = f"http://{hosts[0]}"
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [expected]:
            return False
        referer = self.headers.get("Referer")
        if referer:
            try:
                parsed = urlsplit(referer)
            except ValueError:
                return False
            if f"{parsed.scheme}://{parsed.netloc}" != expected:
                return False
        return self.headers.get("Sec-Fetch-Site", "none") in {"none", "same-origin"}

    def _read(self, head: bool = False) -> None:
        if not self._allowed():
            self._error(403, "Local same-origin requests only", head)
            return
        if self.requestline.split()[1] != self.path:
            self._error(404, "Route not found", head)
            return
        if self.path == "/api/session":
            body = json.dumps(
                {
                    "responses_enabled": self.server.answer is not None,
                    "nonce": self.server.nonce if self.server.answer is not None else None,
                }
            ).encode()
            self._response(200, body, "application/json; charset=utf-8", head)
            return
        if self.path == "/api/snapshot":
            try:
                body = json.dumps(self.server.snapshot(), ensure_ascii=True).encode("utf-8")
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("Snapshot response exceeds limit")
            except (OSError, ValueError, UnicodeError, feature_program.ProgramError):
                self._error(503, "Program snapshot unavailable; check the configured program", head)
                return
            self._response(200, body, "application/json; charset=utf-8", head)
            return
        asset = STATIC_ROUTES.get(self.path)
        if asset is None:
            self._error(404, "Route not found", head)
            return
        filename, content_type = asset
        try:
            body = (ASSETS / filename).read_bytes()
        except OSError:
            self._error(503, "Dashboard asset unavailable", head)
            return
        self._response(200, body, content_type, head)

    def do_GET(self) -> None:
        """Read a permitted asset or snapshot."""
        self._read()

    def do_HEAD(self) -> None:
        """Read headers without a response body."""
        self._read(head=True)

    def _deny_write(self) -> None:
        self.close_connection = True
        self._error(405, "Read-only dashboard; only GET and HEAD are supported")

    def do_POST(self) -> None:
        """Accept only a bounded, same-origin answer to an existing director question."""
        self.close_connection = True
        if (
            self.path != "/api/questions/answer"
            or self.requestline.split()[1] != self.path
            or self.server.answer is None
        ):
            self._deny_write()
            return
        hosts = self.headers.get_all("Host", [])
        if (
            not self._allowed()
            or len(hosts) != 1
            or self.headers.get_all("Origin", []) != [f"http://{hosts[0]}"]
        ):
            self._error(403, "Same-origin response required")
            return
        nonces = self.headers.get_all("X-Work-Program-Nonce", [])
        if len(nonces) != 1 or not hmac.compare_digest(
            nonces[0].encode(), self.server.nonce.encode()
        ):
            self._error(403, "Response session token required")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get("Transfer-Encoding")
            or len(lengths) != 1
            or not lengths[0].isdigit()
            or not lengths[0].isascii()
            or len(lengths[0]) > 10
        ):
            self._error(411, "Explicit content length required")
            return
        size = int(lengths[0])
        if size < 1 or size > 32 * 1024:
            self._error(413, "Response body exceeds limit")
            return
        if self.headers.get_content_type() != "application/json":
            self._error(415, "JSON response required")
            return
        try:

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate answer field")
                    result[key] = value
                return result

            payload = json.loads(self.rfile.read(size), object_pairs_hook=unique)
            if not isinstance(payload, dict) or set(payload) != {
                "question_id",
                "version",
                "answer",
                "submission_id",
            }:
                raise ValueError("Exact response fields required")
            if any(
                not isinstance(payload[key], str) or not payload[key].strip()
                for key in ("question_id", "answer", "submission_id")
            ):
                raise ValueError("Nonblank response strings required")
            if type(payload["version"]) is not int or payload["version"] < 1:
                raise ValueError("Positive version required")
            if (
                len(payload["answer"]) > 4000
                or len(payload["question_id"]) > 128
                or len(payload["submission_id"]) > 128
            ):
                raise ValueError("Response field exceeds limit")
            result = self.server.answer(
                payload["question_id"],
                payload["version"],
                payload["answer"],
                payload["submission_id"],
            )
        except feature_program.ProgramError as error:
            code = getattr(error, "code", None)
            self._error(
                409
                if code == "conflict"
                else 404
                if code == "unknown"
                else 400
                if code == "invalid"
                else 503,
                "Response conflicts with current question; refresh before retrying"
                if code == "conflict"
                else "Response could not be saved",
            )
            return
        except (ValueError, UnicodeError):
            self._error(400, "Invalid response JSON or fields")
            return
        except OSError:
            self._error(503, "Response could not be saved; retry with the same submission")
            return
        self._response(
            200, json.dumps(result, ensure_ascii=True).encode(), "application/json; charset=utf-8"
        )

    do_PUT = _deny_write
    do_PATCH = _deny_write
    do_DELETE = _deny_write
    do_OPTIONS = _deny_write
    do_TRACE = _deny_write
    do_CONNECT = _deny_write


def create_server(
    repo: Path, program_dir: Path, host: str = "127.0.0.1", port: int = 8765, responses: bool = True
):
    """Create a server without reading or modifying the configured program."""
    repository = repo.resolve()
    directory = program_dir if program_dir.is_absolute() else repository / program_dir
    directory = directory.resolve()
    return DashboardServer(
        host,
        port,
        lambda: feature_program.read_snapshot(repository, directory),
        (
            lambda question_id, version, answer, submission_id: (
                feature_program_questions.record_answer(
                    repository, directory, question_id, version, answer, submission_id
                )
            )
        )
        if responses
        else None,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the local dashboard until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--program-dir", type=Path, default=Path(".work/programs/default"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--read-only", action="store_true", help="Disable responses to director questions"
    )
    args = parser.parse_args(argv)
    try:
        with create_server(
            args.repo, args.program_dir, args.host, args.port, not args.read_only
        ) as server:
            print(f"Local work-program dashboard: {server.url}", flush=True)
            print(
                "Task views are read-only; question responses "
                + ("disabled." if args.read_only else "enabled."),
                flush=True,
            )
            print("Press Ctrl+C to stop. Saved records do not prove agent liveness.", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as error:
        parser.exit(2, f"Dashboard could not start: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
