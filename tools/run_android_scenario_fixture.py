"""Run an Android scenario fixture on an explicitly selected, authorized ADB device."""

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from player_e2e_artifacts import contained, digest, read_json, unique_object, write_json

_RESULT = re.compile(r"I/Unity[ \t]*\([ \t]*(\d+)\): MCP_ANDROID_FIXTURE_RESULT (.{1,1024})")
_APP = r"[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)+"
_HEX = r"[0-9a-f]"
_SCHEMA = 1


class PilotError(RuntimeError):
    """The Android observation did not establish the required launch and state."""


@dataclass(frozen=True, slots=True)
class FixtureBuild:
    app_id: str
    scenario: str
    build_id: str
    source_revision: str
    unity_version: str
    abi: str
    apk_sha256: str
    apk: Path

    @classmethod
    def read(cls, receipt: Path) -> "FixtureBuild":
        """Read and bind the build receipt to its APK bytes and IL2CPP payload."""
        value = read_json(receipt, 4096)
        expected = {
            "app_id": _APP,
            "scenario": r"[a-z][a-z0-9-]{0,63}",
            "build_id": _HEX + "{32}",
            "source_revision": _HEX + "{64}",
            "unity_version": r"\d{4}\.\d+\.\d+[abfp]\d+",
            "abi": r"arm64-v8a|armeabi-v7a|x86_64",
            "apk_sha256": _HEX + "{64}",
            "apk_file": r"[a-zA-Z0-9_-]+\.apk",
        }
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != _SCHEMA
            or value.get("backend") != "IL2CPP"
            or any(
                not isinstance(value.get(field), str) or not re.fullmatch(pattern, value[field])
                for field, pattern in expected.items()
            )
        ):
            raise PilotError("Malformed Android IL2CPP fixture build receipt")
        apk = contained(receipt.parent, value["apk_file"])
        if digest(apk) != value["apk_sha256"]:
            raise PilotError("APK digest does not match this build receipt")
        with ZipFile(apk) as bundle:
            entries = bundle.namelist()
            if any(
                entries.count(name) != 1
                for name in ("AndroidManifest.xml", f"lib/{value['abi']}/libil2cpp.so")
            ):
                raise PilotError("The APK does not contain the declared Android IL2CPP payload")
        return cls(**{field: value[field] for field in expected if field != "apk_file"}, apk=apk)


@dataclass(frozen=True, slots=True)
class AdbTarget:
    serial: str
    transport_id: str


def select_device(devices: str, serial: str) -> AdbTarget:
    """Require one explicit and online device row with an ADB transport identity."""
    rows = [row.split() for row in devices.splitlines() if row.split()[:1] == [serial]]
    if len(rows) != 1 or rows[0][1:2] != ["device"]:
        raise PilotError("Selected ADB device is not uniquely present and online")
    ids = [
        part.removeprefix("transport_id:")
        for part in rows[0][2:]
        if part.startswith("transport_id:")
    ]
    if len(ids) != 1 or not re.fullmatch(r"[1-9][0-9]*", ids[0]):
        raise PilotError("Selected ADB device has no unique transport identity")
    return AdbTarget(serial, ids[0])


@dataclass(frozen=True, slots=True)
class ReadyReport:
    ready: bool
    raw_evidence: str


@dataclass(frozen=True, slots=True)
class LaunchObservation:
    activity: str
    launch_response: str
    pid: str
    observation: ReadyReport


def read_fresh_result(
    log: str, build: FixtureBuild, launch_id: str, pid: str
) -> ReadyReport | None:
    """Accept a single result from the current process and match the full launch/build binding."""
    matches = []
    for line in log.splitlines():
        match = _RESULT.fullmatch(line)
        if not match or match[1] != pid:
            continue
        data = json.loads(match[2], object_pairs_hook=unique_object)
        if not isinstance(data, dict):
            raise PilotError("Malformed result record from the launched fixture process")
        if data.get("launch_id") != launch_id:
            continue
        expected = {
            "app_id": build.app_id,
            "scenario": build.scenario,
            "build_id": build.build_id,
            "source_revision": build.source_revision,
            "unity_version": build.unity_version,
        }
        if (
            type(data.get("schema_version")) is not int
            or data["schema_version"] != _SCHEMA
            or any(data.get(key) != value for key, value in expected.items())
            or type(data.get("ready")) is not bool
        ):
            raise PilotError(
                "Result for the current launch has a different build or invalid app state"
            )
        matches.append(ReadyReport(data["ready"], line))
    if len(matches) > 1:
        raise PilotError("Ambiguous duplicate result for the same Android launch")
    return matches[0] if matches else None


@dataclass(frozen=True, slots=True)
class Adb:
    executable: str
    target: AdbTarget

    def call(self, *arguments: str, timeout: int = 20) -> str:
        return adb_output(self.executable, ("-t", self.target.transport_id, *arguments), timeout)

    def property(self, key: str) -> str:
        value = self.call("shell", "getprop", key).strip()
        if not value or "\n" in value or len(value) > 512:
            raise PilotError(f"Device returned an invalid identity property: {key}")
        return value

    def current_pid(self, app_id: str) -> str:
        try:
            return self.call("shell", "pidof", app_id).strip()
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 1 or exc.stderr:
                raise
            return ""

    def verify_installed(self, build: FixtureBuild) -> None:
        path = self.call("shell", "pm", "path", build.app_id).strip()
        if not re.fullmatch(r"package:/[a-zA-Z0-9._/=~+-]+\.apk", path):
            raise PilotError("Selected app has no single installed base APK")
        digest_text = self.call("shell", "sha256sum", path.removeprefix("package:")).split()
        if not digest_text or digest_text[0] != build.apk_sha256:
            raise PilotError("Installed APK does not match the verified build")


def adb_output(executable: str, arguments: tuple[str, ...], timeout: int = 20) -> str:
    return subprocess.run(
        [executable, *arguments],
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    ).stdout


def observe(adb: Adb, build: FixtureBuild, launch_id: str, timeout: int) -> LaunchObservation:
    """Launch the resolved activity and wait for the matched runtime output."""
    component = (
        adb.call(
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            build.app_id,
        )
        .strip()
        .splitlines()[-1:]
    )
    if not component or not re.fullmatch(
        re.escape(build.app_id) + r"/[.a-zA-Z0-9_]+", component[0]
    ):
        raise PilotError("Selected package has no verified launcher activity")
    launched = adb.call(
        "shell",
        "am",
        "start",
        "-S",
        "-W",
        "-n",
        component[0],
        "--es",
        "mcp_fixture_launch_id",
        launch_id,
    )
    if not re.search(r"^Status:\s+ok\s*$", launched, flags=re.MULTILINE):
        raise PilotError("ADB could not confirm the app launch")
    deadline = time.monotonic() + timeout
    observed_pid = ""
    while time.monotonic() < deadline:
        pid = adb.current_pid(build.app_id)
        if observed_pid and observed_pid != pid:
            raise PilotError("The launched app process ended or restarted without settling")
        if pid:
            if not re.fullmatch(r"[1-9][0-9]*", pid):
                raise PilotError("The selected app does not have one unambiguous process")
            observed_pid = pid
            log = adb.call(
                "logcat",
                "-d",
                "-t",
                "2000",
                "-b",
                "main",
                "-v",
                "brief",
                "--pid",
                pid,
                "Unity:I",
                "*:S",
            )
            if (result := read_fresh_result(log, build, launch_id, pid)) is not None:
                return LaunchObservation(component[0], launched, pid, result)
        time.sleep(0.5)
    raise PilotError("No current-launch result was observed before the deadline")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build", type=Path, required=True, help="Trusted local build.json receipt"
    )
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True, help="Serial of an owned or authorized device")
    parser.add_argument(
        "--install", action="store_true", help="Install/update the fixture APK on this device"
    )
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if not 1 <= options.timeout <= 180:
        parser.error("--timeout must be between 1 and 180 seconds")
    try:
        options.output.mkdir(parents=True)
    except OSError as error:
        parser.error(str(error))
    launch_id = uuid4().hex
    report = {"schema_version": _SCHEMA, "launch_id": launch_id, "requested_device": options.serial}
    try:
        build = FixtureBuild.read(options.build.absolute())
        report["build"] = {key: value for key, value in asdict(build).items() if key != "apk"}
        report["build"]["backend"] = "IL2CPP"
        target = select_device(adb_output(options.adb, ("devices", "-l")), options.serial)
        adb = Adb(options.adb, target)
        fingerprint = adb.property("ro.build.fingerprint")
        api = adb.property("ro.build.version.sdk")
        abis = adb.property("ro.product.cpu.abilist").split(",")
        report["device"] = {**asdict(target), "fingerprint": fingerprint, "api": api, "abis": abis}
        if build.abi not in abis:
            raise PilotError("Device ABI does not match the fixture")
        if options.install and adb.call(
            "install", "-r", str(build.apk), timeout=120
        ).strip().splitlines()[-1:] != ["Success"]:
            raise PilotError("Fixture installation did not succeed")
        adb.verify_installed(build)
        launched = observe(adb, build, launch_id, options.timeout)
        adb.verify_installed(build)
        if (
            adb.current_pid(build.app_id) != launched.pid
            or adb.property("ro.build.fingerprint") != fingerprint
        ):
            raise PilotError("Device or fixture process changed while reading its result")
        report.update(
            asdict(launched), verdict="passed" if launched.observation.ready else "failed"
        )
    except (OSError, ValueError, BadZipFile, subprocess.SubprocessError, PilotError) as error:
        report.update({"verdict": "unverified", "reason": str(error)})
    write_json(options.output / "result.json", report)
    print(options.output / "result.json")
    return 0 if report["verdict"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
