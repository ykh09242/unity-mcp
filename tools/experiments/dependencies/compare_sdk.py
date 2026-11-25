"""Compare catalog notifications, result hydration and adapter admission on two SDKs."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SDK_PAIR = ("fastmcp", "fastmcp-slim")
ARMS = {"previous": "4.2.3", "reviewed": "4.2.6"}


def run(*command, **kwargs):
    return subprocess.run(command, cwd=REPO, check=True, timeout=150, **kwargs)


def validate(previous, reviewed):
    """Guard the environment identity, adapter decisions and only expected wire delta."""
    assert previous["python"] == reviewed["python"]
    manifest = {"fastmcp": ARMS["reviewed"], "mcp": "2.3.0", "mcp-types": "2.3.0"}
    for name, result in (("previous", previous), ("reviewed", reviewed)):
        active = name == "reviewed"
        assert all(result["packages"].get(pkg) == ARMS[name] for pkg in SDK_PAIR)
        adapters = result["adapters"]
        runtime = adapters["runtime"]
        assert runtime["sdk"]["reviewed"] == manifest
        assert runtime["sdk"]["resolved"] == {**manifest, "fastmcp": ARMS[name]}
        assert runtime["sdk"]["supported"] is active
        assert adapters["tasks_installed"] is active
        assert adapters["store_paths_present"] == [active, active]
        expected_receipt = "opened" if active else "execution_receipt_sdk_unsupported"
        assert adapters["receipt_admission"] == expected_receipt
        expected_state = "active" if active else "sdk_unsupported"
        for feature in ("execution_receipts", "wait_tasks"):
            assert runtime["features"][feature] == {"configured": True, "state": expected_state}
    assert previous["packages"]["authlib"] == "1.9.10"
    assert "authlib" not in reviewed["packages"]
    assert previous["modern_tools_changed"] is False
    assert reviewed["modern_tools_changed"] is True
    assert {
        pkg: version
        for pkg, version in previous["packages"].items()
        if pkg not in (*SDK_PAIR, "authlib")
    } == {pkg: version for pkg, version in reviewed["packages"].items() if pkg not in SDK_PAIR}

    for mode in ("legacy", "2026-07-28"):
        old = previous["results"][mode]
        new = reviewed["results"][mode]
        assert old == new
        for case in ("complete", "short"):
            assert new[case]["hydrated"] == new[case]["wire"]


def compare(scratch: Path, output: Path):
    uv_version = run("uv", "--version", capture_output=True, text=True).stdout.strip()
    project = REPO / "Server"
    run(
        "uv",
        "build",
        "--wheel",
        "--project",
        project,
        "--out-dir",
        scratch,
        "--python",
        sys.executable,
    )
    (wheel,) = scratch.glob("*.whl")
    wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    results = {}
    for arm in ARMS:
        environment = scratch / arm
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        requirements = scratch / f"{arm}.txt"
        omit = []
        inputs = [requirements]
        if arm == "previous":
            omit = [part for pkg in SDK_PAIR for part in ("--no-emit-package", pkg)]
            inputs.append(HERE / "fastmcp-4.2.3.txt")
        run(
            "uv",
            "export",
            "--project",
            project,
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--output-file",
            requirements,
            *omit,
        )
        run("uv", "venv", "--no-config", "--python", sys.executable, environment)
        run("uv", "pip", "sync", "--no-config", "--require-hashes", "--python", python, *inputs)
        # Retain the synced graph. The previous arm deliberately overrides one wheel requirement.
        run("uv", "pip", "install", "--no-config", "--no-deps", "--python", python, wheel)
        with (output / f"{arm}.json").open("w", encoding="utf-8") as stream:
            subprocess.run(
                [python, "-I", HERE / "sdk_probe.py"],
                cwd=scratch,
                check=True,
                stdout=stream,
                timeout=60,
            )
        results[arm] = json.loads((output / f"{arm}.json").read_text(encoding="utf-8"))
    validate(results["previous"], results["reviewed"])
    summary = {
        "application_commit": run(
            "git", "rev-parse", "HEAD", capture_output=True, text=True
        ).stdout.strip(),
        "same_server_wheel_sha256": wheel_hash,
        "uv": uv_version,
        "results": "Both SDKs hydrated both result shapes. Only the reviewed SDK delivered a native modern catalog notification and activated the optional adapters.",
    }
    (output / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory(prefix="unity-mcp-sdk-comparison-") as directory:
        compare(Path(directory).resolve(), output)


if __name__ == "__main__":
    main()
