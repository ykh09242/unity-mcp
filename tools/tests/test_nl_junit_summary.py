"""The job summary must reflect structured suite evidence, including incomplete runs."""

import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    *[f"NL-{n}" for n in range(5)],
    *[f"T-{c}" for c in "ABCDEFGHIJ"],
    *[f"GO-{n}" for n in range(11)],
]


def _summary(tmp_path: Path, xml: str | None) -> str:
    reports = tmp_path / "reports"
    reports.mkdir()
    if xml is not None:
        (reports / "junit-nl-suite.xml").write_text(xml, encoding="utf-8")
    # Reproduce the old false-success trigger: NOT PASSED was never matched as FAIL.
    (reports / "junit-nl-suite.md").write_text(
        "# Results\n\n## Test Details\n### NL-0 — NOT PASSED\nBroken\n", encoding="utf-8"
    )
    workflow = yaml.safe_load((ROOT / ".github/workflows/claude-nl-suite.yml").read_text())
    step = next(
        s
        for s in workflow["jobs"]["nl-suite"]["steps"]
        if s.get("name") == "NL/T details -> Job Summary"
    )
    script = step["run"]
    if "<<'PY'" in script:
        command = [sys.executable, "-c", script.split("\n", 1)[1].rsplit("\nPY", 1)[0]]
    else:
        command = shlex.split(script.split(">>", 1)[0])
        command[0] = sys.executable
        command[1] = str(ROOT / command[1])
        command[2] = str(reports / "junit-nl-suite.xml")
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env={**os.environ, "JUNIT_OUT": str(reports / "junit-nl-suite.xml")},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _xml(*extra: str, omit: str = "") -> str:
    return (
        "<testsuites><testsuite>"
        + "".join(f'<testcase name="{tid}"/>' for tid in REQUIRED if tid != omit)
        + "".join(extra)
        + "</testsuite></testsuites>"
    )


def test_complete_passing_evidence_reports_success(tmp_path):
    assert "All tests passed" in _summary(tmp_path, _xml())


@pytest.mark.parametrize(
    "xml, detail",
    [
        (_xml('<testcase name="NL-0"><failure message="broken"/></testcase>'), "broken"),
        (_xml('<testcase name="T-A"><error>crashed</error></testcase>'), "crashed"),
        (_xml('<testcase name="GO-0"><skipped message="unavailable"/></testcase>'), "skipped"),
        (_xml(omit="NL-1"), "NL-1"),
        (None, "missing"),
        ("<testsuites/>", "empty"),
        ("<testsuite>", "corrupt"),
        (
            _xml('<testcase name="unrelated"><failure message="unexpected"/></testcase>'),
            "unexpected",
        ),
    ],
)
def test_incomplete_or_bad_evidence_never_claims_success(tmp_path, xml, detail):
    summary = _summary(tmp_path, xml)
    assert "All tests passed" not in summary
    assert detail.lower() in summary.lower()


def test_duplicate_passes_and_system_out_ids_are_accepted(tmp_path):
    xml = _xml('<testcase name="additional"><system-out>NL-0</system-out></testcase>')
    assert "All tests passed" in _summary(tmp_path, xml)
