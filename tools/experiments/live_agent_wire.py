"""Forward the production stdio stream verbatim and journal actor-owned MCP observations."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


def unpack(reply):
    if "error" in reply:
        return False, reply["error"]
    result = reply.get("result", {})
    data = result.get("structuredContent")
    if data is None:
        parts = []
        for content in result.get("contents", result.get("content", [])):
            if isinstance(content.get("text"), str):
                try:
                    value = json.loads(content["text"])
                except ValueError:
                    value = content["text"]
                parts.append(value)
        data = parts[0] if len(parts) == 1 else parts
    ok = not result.get("isError") and not (isinstance(data, dict) and data.get("success") is False)
    return ok, data


def without_secrets(value):
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    if isinstance(value, dict):
        return {
            key: without_secrets(item)
            for key, item in value.items()
            if key.lower().replace("-", "_")
            not in {
                "authorization",
                "x_unity_mcp_token",
                "access_token",
                "refresh_token",
                "api_key",
            }
        }
    return value


class Journal:
    def __init__(self, stream):
        self.stream = stream
        self.pending = {}
        self.lock = threading.Lock()

    def write(self, **event):
        self.stream.write(
            json.dumps({"time_ns": time.time_ns(), **event}, ensure_ascii=True) + "\n"
        )
        self.stream.flush()

    def observe(self, raw, *, from_client):
        try:
            message = json.loads(raw)
        except (UnicodeError, ValueError):
            return
        if not isinstance(message, dict) or "id" not in message:
            return
        request_id = message["id"]
        with self.lock:
            if from_client and "method" in message:
                method = message["method"]
                self.pending[request_id] = method
                params = message.get("params") or {}
                if method in ("tools/call", "resources/read"):
                    self.write(
                        event="request",
                        id=request_id,
                        name=params.get("name") if method == "tools/call" else params.get("uri"),
                        arguments=without_secrets(params.get("arguments") or {}),
                    )
                return
            if from_client or "method" in message:
                return
            method = self.pending.pop(request_id, None)
            if method in ("tools/call", "resources/read"):
                ok, data = unpack(message)
                self.write(event="reply", id=request_id, success=ok, result=without_secrets(data))
            elif method in ("tools/list", "resources/list", "resources/templates/list"):
                result = message.get("result") or {}
                key, field = {
                    "tools/list": ("tools", "name"),
                    "resources/list": ("resources", "uri"),
                    "resources/templates/list": ("resourceTemplates", "uriTemplate"),
                }[method]
                self.write(
                    event="inventory",
                    surface=method,
                    names=[item[field] for item in result.get(key, [])],
                    more=bool(result.get("nextCursor")),
                )
            elif method == "initialize":
                result = message.get("result") or {}
                self.write(
                    event="initialize",
                    protocol=result.get("protocolVersion"),
                    server=result.get("serverInfo"),
                    instructions_sha256=hashlib.sha256(
                        result.get("instructions", "").encode("utf-8")
                    ).hexdigest(),
                )


def server_arguments(profile, instance):
    return ["--transport", "stdio", "--tool-profile", profile, "--default-instance", instance]


def server_environment(status_dir):
    return {"UNITY_MCP_STATUS_DIR": str(status_dir), "UNITY_MCP_DISABLE_TELEMETRY": "1"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--profile", choices=("core", "full"), required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--journal-dir", type=Path, required=True)
    args = parser.parse_args()
    env = os.environ | server_environment(args.status_dir)
    env.pop("UNITY_MCP_TOOL_GROUPS", None)
    path = args.journal_dir / f"wire-{uuid.uuid4().hex}.jsonl"
    with (
        path.open("x", encoding="utf-8") as events,
        subprocess.Popen(
            [args.server, *server_arguments(args.profile, args.instance)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        ) as server,
    ):
        journal = Journal(events)

        def to_server():
            try:
                for line in sys.stdin.buffer:
                    journal.observe(line, from_client=True)
                    server.stdin.write(line)
                    server.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                server.stdin.close()

        inbound = threading.Thread(target=to_server, daemon=True)
        inbound.start()
        try:
            for line in server.stdout:
                journal.observe(line, from_client=False)
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
        except BrokenPipeError:
            server.terminate()


if __name__ == "__main__":
    main()
