"""Actual offline named baseline CLI, immutable evidence, and writer admission."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

from click.testing import CliRunner
import pytest

from cli.main import cli
from cli.utils import play_scenario_soak_baselines as baselines
from .play_scenario_soak_budget_fixtures import _known_evidence, _policy, ENVIRONMENT
from .play_scenario_soak_compare_fixtures import _change_reproduction, _evidence


@pytest.fixture
def command():
    return cli


def _invoke(command, registry, *arguments):
    return CliRunner().invoke(
        command,
        [
            "--format",
            "json",
            "play-scenario",
            "baseline",
            "--registry",
            str(registry),
            *map(str, arguments),
        ],
    )


def test_real_cli_register_pin_list_compare_check_and_audited_history(
    tmp_path, command, monkeypatch
):
    left = _known_evidence(tmp_path / "left", [0, 100, 110, 120])
    right = _known_evidence(tmp_path / "right", [0, 200, 220, 240])
    policy = _policy(tmp_path / "policy.json")
    registry = tmp_path / "baselines.json"
    originals = {
        str(path): path.read_bytes() for directory in (left, right) for path in directory.iterdir()
    }

    def forbidden(*_arguments, **_keywords):
        raise AssertionError("Baseline commands must never launch a process")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    for arguments in (
        ("register", "stable", left),
        ("pin", "stable", "--reason", "Reviewed successful soak"),
        ("list",),
        ("compare", right, "--name", "stable"),
        ("check", right, "--name", "stable", "--policy", policy),
        ("unpin", "stable", "--reason", "Retired after review"),
        ("history",),
    ):
        result = _invoke(command, registry, *arguments)
        assert result.exit_code == 0, result.output
    stored = baselines.read_registry(registry)
    assert stored.revision == 3 and stored.pins == []
    assert stored.history[1].pins == ["stable"]
    assert stored.history[2].previous_pins == ["stable"]
    assert stored.history[2].reason == "Retired after review"
    assert originals == {
        str(path): path.read_bytes() for directory in (left, right) for path in directory.iterdir()
    }


def test_registered_name_never_overwrites_original_reference(tmp_path):
    registry = tmp_path / "registry.json"
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    right = _known_evidence(tmp_path / "right", [0, 2, 3])
    baselines.register_baseline(registry, "stable", left)
    before = registry.read_bytes()
    with pytest.raises(ValueError):
        baselines.register_baseline(registry, "stable", right)
    assert registry.read_bytes() == before


@pytest.mark.parametrize("damage", ["unknown", "mismatch", "legacy", "unsupported"])
def test_named_selection_refuses_unknown_or_incompatible_environment(tmp_path, damage):
    registry = tmp_path / "registry.json"
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    right = _known_evidence(tmp_path / "right", [0, 2, 3])
    baselines.register_baseline(registry, "stable", left)
    baselines.set_baseline_pin(registry, "stable", (True, "Reviewed"))
    if damage == "legacy":
        right = _evidence(tmp_path / "legacy", [0, 2, 3])
    else:
        change = {
            "unknown": {"runtime_version": None},
            "mismatch": {"runtime_version": "different"},
            "unsupported": {"platform": "Android"},
        }[damage]
        _change_reproduction(right, environment={**ENVIRONMENT, **change})
    with pytest.raises(ValueError):
        baselines.select_baseline(registry, right, "stable")


def test_mutated_evidence_is_refused_even_when_still_valid(tmp_path, command):
    registry = tmp_path / "registry.json"
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    right = _known_evidence(tmp_path / "right", [0, 2, 3])
    baselines.register_baseline(registry, "stable", left)
    baselines.set_baseline_pin(registry, "stable", (True, "Reviewed"))
    before = registry.read_bytes()
    # Whitespace preserves valid JSON and native admission, but changes the frozen evidence bytes.
    with (left / "process.json").open("a") as stream:
        stream.write("\n")
    result = _invoke(command, registry, "compare", right, "--name", "stable")
    assert result.exit_code == 2 and "evidence changed" in result.output
    with pytest.raises(ValueError, match="evidence changed"):
        baselines.set_baseline_pin(registry, "stable", (False, "Retire"))
    assert registry.read_bytes() == before


def test_ambiguous_compatible_pins_need_explicit_name(tmp_path):
    registry = tmp_path / "registry.json"
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    right = _known_evidence(tmp_path / "right", [0, 2, 3])
    for name in ("first", "second"):
        baselines.register_baseline(registry, name, left)
        baselines.set_baseline_pin(registry, name, (True, "Reviewed"))
    with pytest.raises(ValueError, match="exactly one"):
        baselines.select_baseline(registry, right)
    assert baselines.select_baseline(registry, right, "second").name == "second"


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version":1,"schema_version":1}',
        '{"schema_version":true,"revision":0,"entries":[],"pins":[],"history":[]}',
        '{"schema_version":1.0,"revision":0,"entries":[],"pins":[],"history":[]}',
        '{"schema_version":1}',
        '{"schema_version":1,"revision":0,"entries":[],"pins":[],"history":[],"unexpected":1}',
        '{"schema_version":1,"revision":true,"entries":[],"pins":[],"history":[]}',
        '{"schema_version":1,"revision":1,"entries":[],"pins":[],"history":[]}',
        '{"schema_version":1,"revision":0,"entries":[],"pins":[{"name":"missing"}],"history":[]}',
    ],
)
def test_malformed_registry_never_changes_on_failed_operation(tmp_path, raw):
    registry = tmp_path / "registry.json"
    registry.write_text(raw)
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    with pytest.raises(ValueError):
        baselines.register_baseline(registry, "stable", left)
    assert registry.read_text() == raw


def test_lock_contention_is_fail_closed_and_preserves_another_lock(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    lock = tmp_path / "registry.json.lock"
    lock.write_text("another writer")
    ticks = iter((0, 6))
    monkeypatch.setattr(baselines.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="locked"):
        baselines.register_baseline(registry, "stable", left)
    assert lock.read_text() == "another writer" and not registry.exists()


def test_concurrent_writers_never_lose_an_accepted_update(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    rendezvous = Barrier(2)
    original = baselines.update_registry

    def synchronized(selected, event, entry=None):
        rendezvous.wait(timeout=10)
        return original(selected, event, entry)

    monkeypatch.setattr(baselines, "update_registry", synchronized)

    def attempt(name):
        try:
            return baselines.register_baseline(registry, name, left).entries[0].name
        except baselines.BaselineError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("first", "second")))
    stored = baselines.read_registry(registry)
    accepted = [name for name in results if name is not None]
    assert len(accepted) == 1
    assert [entry.name for entry in stored.entries] == accepted
    assert stored.revision == 1 and not registry.with_name(registry.name + ".lock").exists()


def test_uncooperative_writer_is_detected_before_atomic_replace(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    baselines.register_baseline(registry, "stable", left)
    original = baselines._persist
    changed = registry.read_bytes() + b"\n"

    def interrupted(path, stored, before):
        path.write_bytes(changed)
        return original(path, stored, before)

    monkeypatch.setattr(baselines, "_persist", interrupted)
    with pytest.raises(ValueError, match="lost update"):
        baselines.set_baseline_pin(registry, "stable", (True, "Reviewed"))
    assert registry.read_bytes() == changed


def test_broken_pin_audit_and_oversized_registry_are_rejected(tmp_path):
    registry = tmp_path / "registry.json"
    left = _known_evidence(tmp_path / "left", [0, 1, 2])
    baselines.register_baseline(registry, "stable", left)
    baselines.set_baseline_pin(registry, "stable", (True, "Reviewed"))
    raw = json.loads(registry.read_text())
    raw["history"][1]["previous_pins"] = ["stable"]
    registry.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="chain"):
        baselines.read_registry(registry)
    registry.write_bytes(b" " * (baselines.REGISTRY_LIMIT + 1))
    with pytest.raises(ValueError, match="bound"):
        baselines.read_registry(registry)
