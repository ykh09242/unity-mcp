"""Exercise classified checkout divergence at the pair admission boundary."""

from hashlib import sha256
import json
from time import perf_counter

import pytest

from services.verification import reproduce
from .test_play_scenario_player import bundle  # noqa: F401 - imported fixture
from .test_play_scenario_player_assurance import verified_bundle  # noqa: F401 - imported fixture
from .test_verification_git_draft import git
from .test_verification_player_candidates import registered  # noqa: F401 - imported fixture
from .test_verification_reproduce import review  # noqa: F401 - imported fixture
from .test_verification_reproduce import run, write


@pytest.mark.parametrize(
    ("pointer_case", "curve_kind"),
    [
        ("reviewed_object", "unhydrated_lfs_pointer"),
        ("wrong_oid", "committed_mismatch"),
        ("substituted_pointer", "content_mismatch"),
    ],
)
def test_clean_checkout_reports_each_divergent_input_without_admitting_a_player(
    request, tmp_path, monkeypatch, pointer_case, curve_kind
):
    project, verifier, artifacts, pair = request.getfixturevalue("review")
    curve = "Assets/WindCurve.asset"
    script = "Assets/Wind.cs"
    metadata = curve + ".meta"
    original = {path: (project / path).read_bytes() for path in (curve, metadata, script)}
    oid = "1" * 64 if pointer_case == "wrong_oid" else sha256(original[curve]).hexdigest()
    pointer = (
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(original[curve])}\n"
    ).encode("ascii")
    committed_curve = original[curve] if pointer_case == "substituted_pointer" else pointer
    (project / curve).write_bytes(committed_curve)
    (project / ".gitattributes").write_bytes(b"Assets/WindCurve.asset -text\n")
    git(project, "add", curve, ".gitattributes")
    git(project, "commit", "-m", "record reviewed content behind an LFS pointer")
    commit = git(project, "rev-parse", "HEAD").decode().strip()
    closure = artifacts / "candidate/closure.json"
    document = json.loads(closure.read_bytes())
    document["candidate_commit"] = commit
    pair["candidate"].update(project_commit=commit, closure_sha256=write(closure, document))

    clone = tmp_path / "divergent-checkout"
    git(tmp_path, "clone", "--config", "core.autocrlf=true", "--local", str(project), str(clone))
    # An index flag hides this genuine source edit from Git status.
    git(clone, "update-index", "--assume-unchanged", script)
    if pointer_case == "substituted_pointer":
        git(clone, "update-index", "--assume-unchanged", curve)
        (clone / curve).write_bytes(pointer)
    actual_change = b"class Wind { int speed = 999; }"
    (clone / script).write_bytes(actual_change)
    assert git(clone, "status", "--porcelain") == b""
    monkeypatch.setattr(reproduce, "run_player", lambda options: pytest.fail("unadmitted launch"))

    started = perf_counter()
    code, report = run((clone, verifier, artifacts, pair), tmp_path)
    seconds = perf_counter() - started

    assert code == 2 and report["status"] == "unavailable" and not report["runs"]
    findings = {row["path"]: row for row in report["checkout_diagnostics"]}
    actual = {
        script: actual_change,
        curve: pointer,
        metadata: original[metadata].replace(b"\n", b"\r\n"),
    }
    assert {path: row["kind"] for path, row in findings.items()} == {
        script: "content_mismatch",
        curve: curve_kind,
        metadata: "line_endings",
    }
    for path, row in findings.items():
        assert row["expected_sha256"] == sha256(original[path]).hexdigest()
        assert row["checkout_sha256"] == sha256(actual[path]).hexdigest()
        assert (
            row["committed_sha256"]
            == sha256(committed_curve if path == curve else original[path]).hexdigest()
        )
        assert (clone / path).read_bytes() == actual[path]
    request.node.user_properties.append(
        (
            "checkout_observation",
            json.dumps(
                {
                    "exit_code": code,
                    "status": report["status"],
                    "reason": report.get("reason"),
                    "seconds": round(seconds, 6),
                    "reviewed_commit": commit,
                    "pointer_case": pointer_case,
                    "launches": len(report["runs"]),
                    "input_diagnostics": report["checkout_diagnostics"],
                },
                sort_keys=True,
            ),
        )
    )
