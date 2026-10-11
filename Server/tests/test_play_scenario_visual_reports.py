"""Offline analysis preserves failures and rejects ambiguous or unsafe visual evidence."""

import json
from hashlib import sha256
from pathlib import Path
import socket
import subprocess

import click
from click.testing import CliRunner
from PIL import Image
import pytest

from cli.commands.play_scenario_analysis import register_analysis_commands


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline analysis attempted network or subprocess access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(
        "cli.commands.play_scenario_analysis.get_config",
        lambda: type("Config", (), {"format": "json"})(),
    )


@pytest.fixture
def command_group():
    group = click.Group()
    register_analysis_commands(group)
    return group


def write_json(path, content):
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def report(job_id="run-a", states=("passed",), **changes):
    value = {
        "job_id": job_id,
        "status": "succeeded" if all(state == "passed" for state in states) else "failed",
        "repeat_count": len(states),
        "execution_environment": "editor",
        "reproduction": {
            "definition_hash": "a" * 64,
            "unity_version": "6000.0",
            "package_version": "1.0",
            "environment": {
                "schema_version": 1,
                "operating_system": "Windows 11",
                "platform": "WindowsEditor",
                "architecture": "x64",
                "scripting_backend": "mono",
                "build_mode": "editor",
                "runtime_version": "6.0",
            },
        },
        "iteration_results_version": 1,
        "iteration_results": [
            {
                "iteration": index,
                "status": state,
                "started_unix_ms": None if state == "skipped" else index * 100,
                "finished_unix_ms": index * 100 + 20,
            }
            for index, state in enumerate(states, 1)
        ],
        "steps": [],
    }
    value.update(changes)
    return value


def policy(**changes):
    value = {
        "schema_version": 1,
        "width": 2,
        "height": 2,
        "channel_threshold": {"red": 0, "green": 0, "blue": 0, "alpha": 0},
        "max_changed_pixel_ratio": 0.0,
        "ignored_rectangles": [],
    }
    value.update(changes)
    return value


def png(path, color=(0, 0, 0, 255), size=(2, 2)):
    Image.new("RGBA", size, color).save(path)
    return path


def checkpoint_report(image):
    value = report()
    value["steps"] = [
        {
            "stage": "main",
            "step_index": 0,
            "iteration": 1,
            "name": "Menu",
            "action": "wait_object",
            "screenshot": {
                "name": "menu",
                "status": "captured",
                "path": image.name,
                "sha256": sha256(image.read_bytes()).hexdigest(),
                "requested_width": 2,
                "requested_height": 2,
                "width": 2,
                "height": 2,
            },
        }
    ]
    return value


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "hash",
        "traversal",
        "unavailable",
        "duplicate",
        "identity",
        "environment",
        "unknown_environment",
        "missing_environment",
    ],
)
def test_report_pairing_checks_identity_hash_and_containment(command_group, tmp_path, mutation):
    image = png(tmp_path / "capture.png")
    left = write_json(tmp_path / "left.json", checkpoint_report(image))
    candidate = checkpoint_report(image)
    capture = candidate["steps"][0]["screenshot"]
    if mutation == "hash":
        capture["sha256"] = "f" * 64
    elif mutation == "traversal":
        capture["path"] = "../capture.png"
    elif mutation == "unavailable":
        capture["status"] = "unavailable"
    elif mutation == "duplicate":
        candidate["steps"].append(candidate["steps"][0])
    elif mutation == "identity":
        candidate["steps"][0]["name"] = "Different"
    elif mutation == "environment":
        candidate["reproduction"]["environment"].update(
            platform="LinuxEditor", operating_system="Linux"
        )
    elif mutation == "unknown_environment":
        candidate["reproduction"]["environment"]["operating_system"] = None
    elif mutation == "missing_environment":
        candidate["reproduction"].pop("environment")
    right = write_json(tmp_path / "right.json", candidate)
    policy_file = write_json(tmp_path / "policy.json", policy())
    result = CliRunner().invoke(
        command_group,
        [
            "compare-visual-reports",
            str(left),
            str(right),
            "--policy",
            str(policy_file),
            "--checkpoint",
            "menu",
            "--stage",
            "main",
            "--step-index",
            "0",
            "--iteration",
            "1",
        ],
    )
    assert result.exit_code == (0 if mutation is None else 2), result.output
