"""Exercise the archived workspace proof and independent launch receipt replay."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import pytest

from tools.experiments import live_agent_trial
from tools.experiments.verify_actor_workspace import verify_actor_workspace


def write_record(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ArchivedLaunch:
    work: Path
    proof: dict
    metadata: dict
    actor: dict
    row: dict
    slot: dict

    @property
    def private_slot(self):
        return self.work / "receipts-ko" / self.row["id"] / "attempt-1" / "actor-workspace.json"

    @property
    def launch(self):
        return self.work / "launches" / self.row["id"]

    def bind_slot(self, *, rehash_argv=True):
        if rehash_argv:
            raw_argv = json.dumps(self.slot["argv"], ensure_ascii=False).encode()
            self.slot["argv_sha256"] = hashlib.sha256(raw_argv).hexdigest()
        self.row["actor_workspace_sha256"] = write_record(self.private_slot, self.slot)
        write_record(
            self.launch / "workspace-binding.json",
            {
                "actor_workspace_sha256": self.row["actor_workspace_sha256"],
                "workspace_probe_sha256": self.metadata["proof_sha256"],
            },
        )

    def bind_proof(self):
        self.metadata["proof_sha256"] = write_record(self.work / "actor-workspace.json", self.proof)
        self.slot["probe_sha256"] = self.metadata["proof_sha256"]
        self.bind_slot()

    def verify(self, *, private=True):
        verify_actor_workspace(
            self.metadata, {"ko": [self.row]}, actor=self.actor, work=self.work if private else None
        )


@pytest.fixture
def archived_launch(tmp_path):
    # Replays on a different path and even on a different OS from the recorded command.
    work = tmp_path / "relocated-evidence"
    recorded = PureWindowsPath("D:/study/연구/.work/korean-study/followup")
    project = recorded / "lane-0/project"
    instance = "project@01a01a01"
    actor = {
        "sha256": "1" * 64,
        "model": live_agent_trial.MODEL,
        "reasoning_effort": live_agent_trial.EFFORT,
    }
    metadata = {
        "workspace": "owned_editor_project",
        "sandbox_mode": "workspace-write",
        "backend": "elevated",
        "inside_write_verified": True,
        "outside_write_denied": True,
    }
    proof = {
        **metadata,
        "project": str(project),
        "actor_executable_sha256": actor["sha256"],
        "inside_marker_sha256": "1234567890abcdef" * 4,
    }
    row = {
        "id": "2" * 16,
        "profile": "core",
        "original_sha256": "3" * 64,
        "verdict": "completed",
        "host_item_types": {"agent_message": 1, "mcp_tool_call": 1},
        "host_routes": ["unityMCP/ui_source"],
        "other_integrations": [],
        "host_file_changes": [],
        "calls": [{"method": "tools/call", "binding": "ui_source", "reply": "success"}],
        "review": {"evidence": [{"index": 0}]},
    }
    journal = recorded / "receipts-ko" / row["id"] / "attempt-1"
    argv = live_agent_trial.actor_command(
        r"C:\Codex\codex.EXE",
        project,
        journal,
        recorded / "product/server.cmd",
        instance,
        row,
        status_dir=recorded / "lane-0/status",
    )
    # The measured controller used its audited proxy and a native Windows interpreter.
    for i, arg in enumerate(argv):
        if arg.startswith("mcp_servers.unityMCP.command="):
            argv[i] = 'mcp_servers.unityMCP.command="D:/study/python.exe"'
        if arg.startswith("mcp_servers.unityMCP.args="):
            prefix, _, args = arg.partition("=")
            proxy = json.loads(args)
            proxy[3] = (
                recorded.parents[1] / "archived-controller/heldout/heldout_wire.py"
            ).as_posix()
            argv[i] = prefix + "=" + json.dumps(proxy, ensure_ascii=False)
    argv.pop()
    for setting in (
        'sandbox_mode="workspace-write"',
        "sandbox_workspace_write.writable_roots=[]",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "sandbox_workspace_write.exclude_slash_tmp=true",
        "sandbox_workspace_write.network_access=false",
        'windows.sandbox="elevated"',
    ):
        argv.extend(["-c", setting])
    argv.append("-")
    slot = {
        "workspace": "owned_editor_project",
        "project_relative_to_lane": "project",
        "argv": argv,
        "editor_instance": instance,
        "profile": "core",
    }
    launch = ArchivedLaunch(work, proof, metadata, actor, row, slot)
    write_record(work / "lane-0/owned-lane.json", {"lane": 0, "project": str(project)})
    write_record(work / "lane-0/launch.json", {"pid": 7007, "project": str(project)})
    write_record(
        work / "lane-0/control/response.json",
        {"ok": True, "pid": 7007, "project_root": str(project), "instance": instance},
    )
    write_record(
        launch.launch / "submission.json",
        {"profile": row["profile"], "original_sha256": row["original_sha256"]},
    )
    launch.bind_proof()
    return launch


def test_replays_relocated_workspace_after_editor_shutdown(archived_launch):
    archived_launch.verify()


def test_accepts_pre_scope_rows_only_without_scope_hash(archived_launch):
    row = archived_launch.row.copy()
    row.pop("actor_workspace_sha256")
    verify_actor_workspace(None, {"ko": [row]}, actor=archived_launch.actor)
    with pytest.raises(ValueError, match="workspace"):
        verify_actor_workspace(None, {"ko": [archived_launch.row]}, actor=archived_launch.actor)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project", r"D:\disposable\lane-0\project"),
        ("backend", "unelevated"),
        ("inside_write_verified", 1),
    ],
)
def test_public_claim_has_only_supported_scope_and_safe_fields(archived_launch, field, value):
    archived_launch.metadata[field] = value
    with pytest.raises(ValueError, match="workspace"):
        archived_launch.verify(private=False)


def test_rejects_reused_slot_digest(archived_launch):
    row = archived_launch.row | {"id": "4" * 16}
    with pytest.raises(ValueError, match="workspace"):
        verify_actor_workspace(
            archived_launch.metadata,
            {"ko": [archived_launch.row, row]},
            actor=archived_launch.actor,
        )


@pytest.mark.parametrize(
    "host_activity",
    [
        {"host_file_changes": [{"status": "completed", "paths": 1, "all_paths_owned": True}]},
        {"host_routes": ["unityMCP/ui_source", "codex/command_execution"]},
        {"host_item_types": {"mcp_tool_call": 1, "command_execution": 1}},
        {"other_integrations": ["nativeEditor"]},
        {
            "calls": [
                {"method": "resources/read", "binding": "skill://unity/help", "reply": "success"}
            ]
        },
    ],
)
def test_completed_native_edit_requires_allowed_mcp_evidence(archived_launch, host_activity):
    archived_launch.row.update(host_activity)
    with pytest.raises(ValueError, match="Unity MCP"):
        archived_launch.verify(private=False)


def test_keeps_incomplete_attempt_with_host_file_activity(archived_launch):
    archived_launch.row.update(
        verdict="incomplete_native_target", host_file_changes=[{"status": "completed"}], calls=[]
    )
    archived_launch.verify()
    assert archived_launch.row["verdict"] == "incomplete_native_target"


@pytest.mark.parametrize(("lane_wide", "diagnostic"), [(True, "proof"), (False, "binding")])
def test_requires_exact_receipt_bytes_after_publication(archived_launch, lane_wide, diagnostic):
    receipt = (
        archived_launch.work / "actor-workspace.json" if lane_wide else archived_launch.private_slot
    )
    receipt.write_bytes(receipt.read_bytes() + b"\n")
    with pytest.raises(ValueError, match=diagnostic):
        archived_launch.verify()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project", r"D:\elsewhere\lane-0\project"),
        ("actor_executable_sha256", "4" * 64),
        ("inside_marker_sha256", ""),
    ],
)
def test_rejects_borrowed_or_invalid_private_proof(archived_launch, field, value):
    archived_launch.proof[field] = value
    archived_launch.bind_proof()
    with pytest.raises(ValueError, match="proof"):
        archived_launch.verify()


def test_rejects_launch_binding_to_another_probe(archived_launch):
    write_record(
        archived_launch.launch / "workspace-binding.json",
        {
            "actor_workspace_sha256": archived_launch.row["actor_workspace_sha256"],
            "workspace_probe_sha256": "5" * 64,
        },
    )
    with pytest.raises(ValueError, match="binding"):
        archived_launch.verify()


def test_rejects_argv_changed_without_internal_hash(archived_launch):
    archived_launch.slot["argv"][-1] = "extra instructions"
    archived_launch.bind_slot(rehash_argv=False)
    with pytest.raises(ValueError, match="argv"):
        archived_launch.verify()


@pytest.mark.parametrize(
    ("token", "replacement"),
    [
        ("--approve-for-me", "--dangerously-bypass-approvals-and-sandbox"),
        ("--ignore-user-config", "--add-dir=extra"),
        ("-", "alternate prompt"),
        ('sandbox_mode="workspace-write"', 'sandbox_mode="danger-full-access"'),
        ('windows.sandbox="elevated"', 'windows.sandbox="unelevated"'),
        (
            "sandbox_workspace_write.writable_roots=[]",
            'sandbox_workspace_write.writable_roots=["D:/"]',
        ),
        ("shell_tool", "local_shell"),
    ],
)
def test_rejects_widened_actor_policy_even_when_rehashed(archived_launch, token, replacement):
    argv = archived_launch.slot["argv"]
    argv[argv.index(token)] = replacement
    archived_launch.bind_slot()
    with pytest.raises(ValueError, match="argv"):
        archived_launch.verify()


@pytest.mark.parametrize(
    ("option", "replacement"),
    [
        ("--server", "D:/elsewhere/server.cmd"),
        ("--instance", "project@01a01a02"),
        ("--profile", "full"),
        ("--status-dir", "D:/elsewhere/status"),
        ("--journal-dir", "D:/elsewhere/attempt-1"),
    ],
)
def test_rejects_proxy_target_from_another_lane(archived_launch, option, replacement):
    prefix = "mcp_servers.unityMCP.args="
    argv = archived_launch.slot["argv"]
    index = next(i for i, arg in enumerate(argv) if arg.startswith(prefix))
    proxy = json.loads(argv[index][len(prefix) :])
    proxy[proxy.index(option) + 1] = replacement
    argv[index] = prefix + json.dumps(proxy, ensure_ascii=False)
    archived_launch.bind_slot()
    with pytest.raises(ValueError, match="proxy"):
        archived_launch.verify()
