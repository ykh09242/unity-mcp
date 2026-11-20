"""Compare clean installed evidence commands and record first-read costs."""

import argparse
import json
import os
import runpy
import subprocess
import sys
import time
from importlib.metadata import distributions
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

_ROOT = Path(__file__).resolve().parents[1]


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
    make_capture = runpy.run_path(str(_ROOT / "Server/tests/verification_artifact_fixtures.py"))[
        "make_capture"
    ]
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
    ]


def _installation(executable: Path) -> dict:
    code = "import sysconfig; print(sysconfig.get_path('purelib'))"
    package_root = Path(
        subprocess.check_output(
            [executable.parent / Path(sys.executable).name, "-I", "-c", code],
            text=True,
        ).strip()
    )
    return {
        "site_packages_bytes": sum(
            path.stat().st_size for path in package_root.rglob("*") if path.is_file()
        ),
        "distributions": sorted(
            f"{row.metadata['Name']}=={row.version}"
            for row in distributions(path=[str(package_root)])
        ),
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
    if installs["offline"]["site_packages_bytes"] >= installs["server"]["site_packages_bytes"]:
        raise RuntimeError("The offline environment did not reduce the installed package bytes")
    commands = {
        "server": [full, "--no-project-context", "--format", "json", "verification-evidence"],
        "offline": [lean, "--format", "json"],
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
        for name, command in commands.items():
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
