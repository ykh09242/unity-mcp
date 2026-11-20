"""Compare clean installed evidence commands and record first-read costs."""

import argparse
import json
import os
import runpy
import subprocess
import sys
import time
from hashlib import sha256
from importlib.metadata import distributions
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = "unity_mcp_evidence"


def _write_capture(path: Path, execution: int) -> Path:
    root = ElementTree.Element("test-run", total="1", result="Passed", duration="0.3")
    case = ElementTree.SubElement(root, "test-case", fullname="Doors.Opens", result="Passed")
    ElementTree.SubElement(case, "output").text = (
        f"MCP_VERIFICATION_EXECUTION {UUID(int=execution, version=4).hex}\n"
        "MCP_VERIFICATION_CONTEXT Editor|6000.0.69f1|Linux\n"
        "MCP_VERIFICATION_EXPECTED_CASE Doors.Opens\n"
        f"Assets/Doors.asset {'1' * 32} {'b' * 64}"
    )
    ElementTree.ElementTree(root).write(path, encoding="utf-8")
    return path


def _examples(folder: Path):
    fixtures = runpy.run_path(str(_ROOT / "Server/tests/verification_artifact_fixtures.py"))
    make_capture = fixtures["make_capture"]
    baseline = make_capture(folder, 1)
    candidate = make_capture(
        folder,
        2,
        "failed",
        base_manifest=f"{401:064x}",
        base_source=f"{1:064x}",
    )
    history = {
        "schema": "verification_build_history@1",
        "builds": [
            {
                "build_id": f"build-{index}",
                "source_revision": f"{index:064x}",
                "runs": [{"verification": pair[0].name, "native": pair[1].name}],
            }
            for index, pair in enumerate((baseline, candidate), 1)
        ],
    }
    manifest = folder / "history.json"
    manifest.write_text(json.dumps(history), encoding="utf-8")
    selected = _write_capture(folder / "selected.xml", 1)
    full = _write_capture(folder / "full.xml", 2)
    invalid = folder / "truncated.xml"
    invalid.write_bytes(full.read_bytes()[:-2])
    invalid_json = folder / "invalid.json"
    invalid_json.write_text('{"duplicate":true,"duplicate":false}', encoding="utf-8")
    first_case, second_case = 'Doors.Opens("red")', 'Doors.Opens("blue")'
    first = fixtures["write_nunit"](folder / "shard-first.xml", {first_case: "Passed"})
    second = fixtures["write_nunit"](folder / "shard-second.xml", {second_case: "Failed"})
    first_submission = {
        "run_id": "run-shards",
        "split_id": "first",
        "attempt": 1,
        "execution_id": "exec-first",
        "xml": first.name,
    }
    shard_plan = {
        "schema": "verification_nunit_shards@1",
        "run_id": "run-shards",
        "splits": [
            {"split_id": "first", "cases": [first_case]},
            {"split_id": "second", "cases": [second_case]},
        ],
        "submissions": [first_submission, first_submission],
    }
    partial = folder / "partial-shards.json"
    partial.write_text(json.dumps(shard_plan), encoding="utf-8")
    shard_plan["submissions"].append(
        {
            "run_id": "run-shards",
            "split_id": "second",
            "attempt": 1,
            "execution_id": "exec-second",
            "xml": second.name,
        }
    )
    complete = folder / "complete-shards.json"
    complete.write_text(json.dumps(shard_plan), encoding="utf-8")
    retry = fixtures["write_nunit"](folder / "shard-retry.xml", {second_case: "Passed"})
    shard_plan["submissions"].append(
        {
            **shard_plan["submissions"][-1],
            "attempt": 2,
            "execution_id": "exec-second-retry",
            "xml": retry.name,
        }
    )
    recovered = folder / "recovered-shards.json"
    recovered.write_text(json.dumps(shard_plan), encoding="utf-8")
    return [
        ("selection", 0, "measured", ["selection", "--selected", selected, "--full", full]),
        (
            "compare",
            1,
            "regressed",
            ["compare", "--baseline", *baseline, "--candidate", *candidate],
        ),
        (
            "history",
            1,
            "first_failure",
            ["first-failure", manifest, "--kind", "scenario", "--id", "doors"],
        ),
        ("truncated_xml", 2, "incomplete", ["selection", "--selected", invalid, "--full", full]),
        (
            "duplicate_json",
            2,
            "incomplete",
            ["compare", "--baseline", *baseline, "--candidate", invalid_json, candidate[1]],
        ),
        ("partial_shards", 2, "incomplete", ["shards", partial]),
        ("failed_shards", 1, "failed", ["shards", complete]),
        ("recovered_shards", 0, "passed", ["shards", recovered]),
    ]


def _installation(executable: Path) -> dict:
    code = "import sysconfig; print(sysconfig.get_path('purelib'))"
    package_root = Path(
        subprocess.check_output(
            [executable.parent / Path(sys.executable).name, "-I", "-c", code],
            text=True,
        ).strip()
    )
    package_files = sorted((package_root / _PACKAGE).rglob("*.py"))
    packages = tuple(distributions(path=[str(package_root)]))
    evidence_wheel = next(
        (row for row in packages if row.metadata["Name"] == "ykh09242-unity-mcp-evidence"), None
    )
    if evidence_wheel is not None:
        escaped_sources = [
            str(file)
            for file in evidence_wheel.files or ()
            if file.suffix == ".py" and file.parts[0] != _PACKAGE
        ]
        if escaped_sources:
            raise RuntimeError(
                f"Offline wheel included Python outside {_PACKAGE}: {escaped_sources}"
            )
    return {
        "site_packages_bytes": sum(
            path.stat().st_size for path in package_root.rglob("*") if path.is_file()
        ),
        "distributions": sorted(f"{row.metadata['Name']}=={row.version}" for row in packages),
        "reader_sources": {
            path.relative_to(package_root).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in package_files
        },
    }


def compare_installations(full: Path, lean: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    folder = output / "fixtures"
    folder.mkdir()
    installs = {name: _installation(path) for name, path in (("server", full), ("offline", lean))}
    offline_packages = {
        item.split("==", 1)[0].lower() for item in installs["offline"]["distributions"]
    }
    if offline_packages != {"click", "ykh09242-unity-mcp-evidence"}:
        raise RuntimeError(f"Unexpected clean offline install contents: {offline_packages}")
    sources = installs["offline"]["reader_sources"]
    if not sources or sources != installs["server"]["reader_sources"]:
        raise RuntimeError("The installed distributions do not contain the same shared sources")
    if installs["offline"]["site_packages_bytes"] >= installs["server"]["site_packages_bytes"]:
        raise RuntimeError("The offline environment did not reduce the installed package bytes")

    def commands(output_format: str) -> dict:
        return {
            "server": [
                full,
                "--no-project-context",
                "--format",
                output_format,
                "verification-evidence",
            ],
            "offline": [lean, "--format", output_format],
        }

    env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
        and not name.startswith("UNITY_MCP_")
    }
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "1"
    evidence = []
    for scenario, want_code, want_state, args in _examples(folder):
        verdicts = {}
        timings = {}
        for name, command in commands("json").items():
            start = time.perf_counter()
            response = subprocess.run(
                command + args,
                cwd=folder,
                env=env,
                capture_output=True,
                check=False,
                encoding="utf-8",
            )
            timings[name] = time.perf_counter() - start
            if response.returncode != want_code:
                raise RuntimeError(
                    f"{name}/{scenario} returned {response.returncode}, expected {want_code}: "
                    f"{response.stdout}\n{response.stderr}"
                )
            verdicts[name] = json.loads(response.stdout)
        if verdicts["offline"] != verdicts["server"] or verdicts["server"]["state"] != want_state:
            raise RuntimeError(f"{scenario} classification or JSON differed: {verdicts}")
        evidence.append(
            {"name": scenario, "exit": want_code, "state": want_state, "read_seconds": timings}
        )
    for command in commands("json").values():
        for action in (["team", "replay"], ["shards"]):
            subprocess.run(
                command + action + ["--help"],
                cwd=folder,
                env=env,
                capture_output=True,
                check=True,
            )
    for output_format in ("text", "table"):
        observed = [
            subprocess.run(
                command + ["shards", folder / "complete-shards.json"],
                cwd=folder,
                env=env,
                capture_output=True,
                check=False,
                encoding="utf-8",
            )
            for command in commands(output_format).values()
        ]
        if (
            any(row.returncode != 1 or not row.stdout or row.stderr for row in observed)
            or observed[0].stdout != observed[1].stdout
        ):
            raise RuntimeError(f"Installed {output_format} output differs for failed NUnit shards")
    return {"installations": installs, "first_read": evidence[0], "parity_examples": evidence}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--lean", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    evidence = compare_installations(args.full.resolve(), args.lean.resolve(), output)
    (output / "offline-install.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
