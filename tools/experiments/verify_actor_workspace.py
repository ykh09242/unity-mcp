"""Replay the scoped actor launch evidence without starting an Editor or agent."""

import json
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Final

from tools.experiments.verify_agent_outcome_holdout import digest, is_hash, require

WORKSPACE: Final = "owned_editor_project"
BACKENDS: Final = frozenset({"elevated", "darwin", "linux"})
PRIVATE_PROOF_FIELDS: Final = (
    "workspace",
    "sandbox_mode",
    "backend",
    "inside_write_verified",
    "outside_write_denied",
)
HOST_ITEMS: Final = frozenset({"agent_message", "mcp_tool_call", "reasoning", "todo_list"})
RESOURCE_ROUTES: Final = frozenset(
    {"codex/list_mcp_resources", "codex/list_mcp_resource_templates", "codex/read_mcp_resource"}
)
DISABLED_FEATURES: Final = frozenset(
    {
        "apps",
        "plugins",
        "shell_tool",
        "unified_exec",
        "computer_use",
        "multi_agent",
        "multi_agent_v2",
        "shell_snapshot",
    }
)


def _verify_completed_route(row: dict) -> None:
    routes = set(row["host_routes"])
    calls = row["calls"]
    reviewed = (
        calls[index]
        for item in row["review"]["evidence"]
        if type(index := item["index"]) is int and 0 <= index < len(calls)
    )
    require(
        not row["host_file_changes"]
        and not row["other_integrations"]
        and set(row["host_item_types"]) <= HOST_ITEMS
        and all(route.startswith("unityMCP/") or route in RESOURCE_ROUTES for route in routes)
        and any(
            call["method"] == "tools/call"
            and call["reply"] == "success"
            and f"unityMCP/{call['binding']}" in routes
            for call in reviewed
        ),
        f"Completed edit used an unverified Unity MCP route for {row['id']}",
    )


@dataclass(frozen=True, slots=True)
class ScopedLaunch:
    project: PurePath
    instance: str
    actor: dict
    proof_sha256: str

    def verify_argv(self, argv: list[str], journal: PurePath, profile: str) -> None:
        """Check the recorded exec shape and reject unrecognized host or scope options."""
        require(
            isinstance(argv, list)
            and len(argv) >= 14
            and all(isinstance(arg, str) for arg in argv),
            "Missing scoped actor argv",
        )
        path_type = type(self.project)
        windows = isinstance(self.project, PureWindowsPath)
        executable = path_type(argv[0])
        require(
            executable.is_absolute()
            and ".." not in executable.parts
            and path_type(executable.name) == path_type("codex.exe" if windows else "codex")
            and argv[1:7]
            == [
                "exec",
                "--approve-for-me",
                "--ignore-user-config",
                "--ephemeral",
                "--skip-git-repo-check",
                "-C",
            ]
            and argv[7] == str(self.project)
            and argv[8:13]
            == [
                "--model",
                self.actor["model"],
                "--json",
                "--output-last-message",
                str(journal / "answer.txt"),
            ]
            and argv[-1] == "-",
            "Scoped actor argv differs from the restricted stdin launch",
        )
        options = argv[13:-1]
        require(len(options) % 2 == 0, "Unpaired scoped actor argv option")
        disabled = set()
        settings = {}
        for flag, argument in zip(options[::2], options[1::2], strict=True):
            if flag == "--disable":
                require(argument not in disabled, "Repeated scoped actor argv feature")
                disabled.add(argument)
                continue
            key, separator, value = argument.partition("=")
            require(
                flag == "-c" and separator and key not in settings,
                "Unexpected or repeated scoped actor argv option",
            )
            settings[key] = value
        interpreter = json.loads(settings.pop("mcp_servers.unityMCP.command", "null"))
        proxy = json.loads(settings.pop("mcp_servers.unityMCP.args", "null"))
        require(
            disabled == DISABLED_FEATURES
            and settings
            == {
                "project_doc_max_bytes": "0",
                "web_search": '"disabled"',
                "model_reasoning_effort": json.dumps(self.actor["reasoning_effort"]),
                "mcp_servers.unityMCP.startup_timeout_sec": "90",
                "mcp_servers.unityMCP.tool_timeout_sec": "75",
                "sandbox_mode": '"workspace-write"',
                "sandbox_workspace_write.writable_roots": "[]",
                "sandbox_workspace_write.exclude_tmpdir_env_var": "true",
                "sandbox_workspace_write.exclude_slash_tmp": "true",
                "sandbox_workspace_write.network_access": "false",
                **({"windows.sandbox": '"elevated"'} if windows else {}),
            },
            "Scoped actor argv differs from the restricted workspace or feature settings",
        )
        require(
            isinstance(interpreter, str)
            and path_type(interpreter).is_absolute()
            and ".." not in path_type(interpreter).parts
            and path_type(interpreter).stem.startswith("python"),
            "Scoped actor argv must use an absolute Python interpreter",
        )
        require(
            isinstance(proxy, list)
            and len(proxy) == 14
            and all(isinstance(arg, str) for arg in proxy)
            and proxy[:3] == ["-B", "-X", "utf8"],
            "Invalid Unity MCP proxy registration",
        )
        work = self.project.parent.parent
        script = path_type(proxy[3])
        require(
            script.parts[-2:] == ("heldout", "heldout_wire.py")
            and script.parent.parent.parent == work.parent.parent
            and ".." not in script.parts
            and proxy[4:]
            == [
                "--server",
                (work / "product/server.cmd").as_posix(),
                "--instance",
                self.instance,
                "--status-dir",
                (self.project.parent / "status").as_posix(),
                "--journal-dir",
                journal.as_posix(),
                "--profile",
                profile,
            ],
            "Unity MCP proxy differs from the owned editor and attempt",
        )

    def verify_slot(self, work: Path, panel: str, row: dict) -> None:
        """Bind the public row, original submission, raw receipt and lane probe."""
        slot_id = row["id"]
        launch = work / "launches" / slot_id
        submission = json.loads((launch / "submission.json").read_bytes())
        require(
            submission == {"profile": row["profile"], "original_sha256": row["original_sha256"]},
            f"Scoped submission differs for {slot_id}",
        )
        relative = Path(f"receipts-{panel}") / slot_id / "attempt-1"
        raw = (work / relative / "actor-workspace.json").read_bytes()
        receipt_sha256 = digest(raw)
        binding = json.loads((launch / "workspace-binding.json").read_bytes())
        require(
            receipt_sha256 == row["actor_workspace_sha256"]
            and binding
            == {
                "actor_workspace_sha256": receipt_sha256,
                "workspace_probe_sha256": self.proof_sha256,
            },
            f"Actor workspace binding differs for {slot_id}",
        )
        attempt = json.loads(raw)
        require(
            attempt.get("workspace") == WORKSPACE
            and attempt.get("project_relative_to_lane") == "project"
            and attempt.get("probe_sha256") == self.proof_sha256
            and attempt.get("profile") == row["profile"]
            and attempt.get("editor_instance") == self.instance,
            f"Actor workspace receipt is bound to a different lane or profile for {slot_id}",
        )
        argv = attempt.get("argv")
        require(
            digest(json.dumps(argv, ensure_ascii=False).encode()) == attempt.get("argv_sha256"),
            f"Scoped actor argv hash differs for {slot_id}",
        )
        self.verify_argv(argv, self.project.parent.parent.joinpath(*relative.parts), row["profile"])


def _archived_launch(work: Path, metadata: dict, actor: dict) -> ScopedLaunch:
    raw = (work / "actor-workspace.json").read_bytes()
    proof_sha256 = metadata["proof_sha256"]
    require(digest(raw) == proof_sha256, "Raw actor workspace proof hash differs")
    proof = json.loads(raw)
    require(
        all(
            type(proof.get(key)) is type(metadata[key]) and proof[key] == metadata[key]
            for key in PRIVATE_PROOF_FIELDS
        )
        and is_hash(actor["sha256"])
        and proof.get("actor_executable_sha256") == actor["sha256"]
        and is_hash(proof.get("inside_marker_sha256"))
        and proof["inside_marker_sha256"] != "0" * 64,
        "Actor workspace proof differs from its public claim or pinned executable",
    )
    recorded = proof.get("project")
    require(isinstance(recorded, str), "Actor workspace proof has no owned project path")
    path_type = PureWindowsPath if metadata["backend"] == "elevated" else PurePosixPath
    project = path_type(recorded)
    owned = work / "lane-0"
    marker = json.loads((owned / "owned-lane.json").read_bytes())
    require(
        project.is_absolute()
        and project.parts[-2:] == ("lane-0", "project")
        and len(project.parents) >= 4
        and ".." not in project.parts
        and type(marker.get("lane")) is int
        and marker["lane"] == 0
        and marker.get("project") == recorded,
        "Actor workspace proof refers to a different owned project",
    )
    # Unity removes its live status file on shutdown. The final native response
    # retains the instance, owned project and PID of the Editor that was stopped.
    editor = json.loads((owned / "launch.json").read_bytes())
    response = json.loads((owned / "control/response.json").read_bytes())
    instance = response.get("instance")
    require(
        type(editor.get("pid")) is int
        and editor["pid"] > 0
        and response.get("pid") == editor["pid"]
        and response.get("ok") is True
        and editor.get("project") == response.get("project_root") == recorded
        and isinstance(instance, str)
        and instance.startswith("project@")
        and (is_hash(instance[8:], 8) or is_hash(instance[8:], 16)),
        "Native Editor response does not identify the owned lane",
    )
    return ScopedLaunch(project, instance, actor, proof_sha256)


def verify_actor_workspace(
    metadata: dict | None,
    panels: dict[str, list[dict]],
    *,
    actor: dict,
    work: Path | None = None,
) -> None:
    """Verify the sanitized workspace claims and, when available, their private receipts."""
    rows = [row for panel in panels.values() for row in panel]
    if metadata is None:
        require(
            all("actor_workspace_sha256" not in row for row in rows),
            "Actor workspace digests require a workspace proof",
        )
        return

    require(
        isinstance(metadata, dict)
        and set(metadata) == {*PRIVATE_PROOF_FIELDS, "proof_sha256"}
        and metadata["workspace"] == WORKSPACE
        and metadata["sandbox_mode"] == "workspace-write"
        and isinstance(metadata["backend"], str)
        and metadata["backend"] in BACKENDS
        and metadata["inside_write_verified"] is True
        and metadata["outside_write_denied"] is True
        and is_hash(metadata["proof_sha256"]),
        "Invalid public actor workspace proof",
    )
    hashes = [row.get("actor_workspace_sha256") for row in rows]
    require(
        all(is_hash(value) for value in hashes) and len(hashes) == len(set(hashes)),
        "Actor workspace receipt SHA-256 digests are missing or reused",
    )
    for row in rows:
        if row["verdict"] == "completed":
            _verify_completed_route(row)

    if work is not None:
        launch = _archived_launch(work, metadata, actor)
        for panel, cases in panels.items():
            require(panel in {"old", "new", "ko"}, "Unknown actor workspace panel")
            for row in cases:
                require(is_hash(row["id"], 16), "Invalid actor workspace slot ID")
                launch.verify_slot(work, panel, row)
