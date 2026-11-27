"""Commission the optional project-scoped filesystem for a sealed live edit trial."""

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
from subprocess import run as run_sandbox_probe
import sys


def _owned_project(project):
    from tools.experiments.live_editor_fixture import lane

    project = lane.real_child(Path(project).absolute())
    lane.lane_marker(project.parent)
    if project.name != "project" or not project.is_dir():
        raise ValueError("The workspace must be the disposable Editor project")
    return project


def _sandbox_options():
    settings = [
        'sandbox_mode="workspace-write"',
        "sandbox_workspace_write.writable_roots=[]",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "sandbox_workspace_write.exclude_slash_tmp=true",
        "sandbox_workspace_write.network_access=false",
    ]
    if os.name == "nt":
        # exec ignores user config. Select the installed restricted Windows backend for this run.
        settings.append('windows.sandbox="elevated"')
    return [token for setting in settings for token in ("-c", setting)]


def project_scope_command(cmd, *, project):
    """Keep the sealed host invocation and make the disposable project its only workspace."""
    project = _owned_project(project)
    if (
        cmd.count("-C") != 1
        or cmd[-1] != "-"
        or "--approve-for-me" not in cmd
        or "--ignore-user-config" not in cmd
        or any(arg == "--add-dir" or arg.startswith("--add-dir=") for arg in cmd)
    ):
        raise ValueError("Expected the isolated stdin-only actor command")
    scoped = list(cmd)
    scoped[cmd.index("-C") + 1] = str(project)
    return [*scoped[:-1], *_sandbox_options(), "-"]


_PROBE = """
import json
from pathlib import Path
import sys

inside, outside, nonce = sys.argv[1:]
Path(inside).write_text(nonce, encoding="ascii")
try:
    with Path(outside).open("r+b"):
        denied = False
except PermissionError:
    denied = True
print(json.dumps({"outside_write_denied": denied}))
"""


def verify_project_scope(codex, project):
    """Exercise the installed OS sandbox without an agent turn or a Unity asset write."""
    project = _owned_project(project)
    executable = shutil.which(codex)
    if executable is None:
        raise RuntimeError("The installed actor executable is missing")
    with Path(executable).open("rb") as stream:
        executable_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    nonce = secrets.token_hex(16)
    inside = project / (".mcp-workspace-" + nonce)
    outside = project.parent / (".mcp-boundary-" + nonce)
    with ExitStack() as markers:
        # Ordinary inherited filesystem permissions, as on authored Unity files.
        for path, content in ((inside, b"before"), (outside, b"protected")):
            with path.open("xb") as stream:
                markers.callback(path.unlink)
                stream.write(content)
        result = run_sandbox_probe(
            [
                executable,
                "sandbox",
                *_sandbox_options(),
                "--",
                sys.executable,
                "-I",
                "-B",
                "-c",
                _PROBE,
                str(inside),
                str(outside),
                nonce,
            ],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=75,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"Project sandbox probe failed ({result.returncode}): {result.stderr[-600:]}"
            )
        if inside.read_bytes() != nonce.encode("ascii"):
            raise RuntimeError("Sandbox did not write the marker inside the disposable project")
        if (
            json.loads(result.stdout).get("outside_write_denied") is not True
            or outside.read_bytes() != b"protected"
        ):
            raise RuntimeError("Sandbox did not deny write access outside the disposable project")
    return {
        "workspace": "owned_editor_project",
        "sandbox_mode": "workspace-write",
        "backend": "elevated" if os.name == "nt" else sys.platform,
        "inside_write_verified": True,
        "outside_write_denied": True,
        "project": str(project),
        "actor_executable_sha256": executable_digest,
        "inside_marker_sha256": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
    }
