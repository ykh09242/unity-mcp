"""Packaging commands retain the registry-verified immutable top-level inputs."""

from pathlib import Path
import subprocess

from tools import generate_mcpb as bundle

ROOT = Path(__file__).resolve().parents[2]


def test_container_keeps_verified_python_index_digest_and_exact_uv_release():
    lines = (ROOT / "Server/Dockerfile").read_text().splitlines()
    assert (
        lines[0]
        == "FROM python:3.14-slim@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966"
    )
    assert "RUN pip install uv==0.13.7" in lines


def test_generator_invokes_reviewed_mcpb_release(tmp_path, monkeypatch):
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"owned icon")
    output = tmp_path / "bundle.mcpb"
    template = tmp_path / "manifest.json"
    template.write_text('{"name":"owned-fixture"}')
    monkeypatch.setattr(bundle, "MANIFEST_TEMPLATE", template)
    calls = []

    def pack(arguments, cwd):
        calls.append(arguments)
        Path(arguments[-1]).write_bytes(b"owned bundle")
        return subprocess.CompletedProcess(arguments, 0, "packed", "")

    monkeypatch.setattr(bundle, "run_npx", pack)
    bundle.generate_mcpb("1.2.3", output, icon)
    assert calls[0][:4] == ["--yes", "@anthropic-ai/mcpb@2.1.2", "pack", "."]
    assert output.read_bytes() == b"owned bundle"
