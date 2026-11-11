"""Emit bounded release evidence from a command or an explicitly observed native action.

Run: python tools/release_execution.py --help
Records associate saved outputs; they do not authenticate an untrusted workflow author.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


PREFIX = "UNITY_MCP_EXECUTION "
Json = str | int | bool | None | list["Json"] | dict[str, "Json"]
JsonMap = dict[str, Json]


def ordinary(path: Path) -> Path:
    """Keep producer reads/writes inside ordinary owned paths, including on Windows."""
    if ".." in path.parts:
        raise ValueError("Parent traversal in execution output paths is unsupported")
    absolute = path.absolute()
    for parent in (absolute, *absolute.parents):
        if parent.exists() or parent.is_symlink():
            if parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked execution output paths are unsupported")
    return absolute


def identity() -> JsonMap:
    root = Path(os.environ["GITHUB_WORKSPACE"])
    revision = subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=30
    ).strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Execution requires an actual full checkout revision")
    run_id, attempt = int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])
    if run_id < 1 or attempt < 1:
        raise ValueError("Run identity and attempt must be positive")
    return {
        "schema_version": 1,
        "repository": os.environ["GITHUB_REPOSITORY"],
        "run_id": run_id,
        "run_attempt": attempt,
        "job_key": os.environ["GITHUB_JOB"],
        "checkout_sha": revision,
    }


def emit(record: JsonMap) -> None:
    print(PREFIX + json.dumps(record, sort_keys=True, separators=(",", ":")), flush=True)


def output_paths(values: list[str]) -> dict[str, str]:
    paths: dict[str, str] = {}
    for value in values:
        role, separator, raw = value.partition("=")
        if not separator or role not in {"results", "preparation"} or role in paths:
            raise ValueError("Expected unique results=PATH or preparation=PATH output")
        path = ordinary(Path(raw))
        if path.exists() or not path.is_relative_to(Path.cwd()):
            raise ValueError("Execution output must be fresh and inside the working directory")
        paths[role] = path.relative_to(Path.cwd()).as_posix()
    return paths


def pointers(paths: dict[str, str]) -> JsonMap:
    outputs: JsonMap = {}
    for role, raw in paths.items():
        path = ordinary(Path(raw))
        if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Missing or oversized execution output: " + role)
        outputs[role] = {"path": raw, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return outputs


def step_output(values: dict[str, str]) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(key + "=" + value + "\n")


def fresh_record(args: argparse.Namespace) -> JsonMap:
    record = identity()
    execution_id = uuid.uuid4().hex
    output_root = f".release-evidence/{execution_id}"
    ordinary(Path(output_root)).mkdir(parents=True, exist_ok=False)
    if f"[{record['job_key']}:" not in args.step_name:
        raise ValueError("Execution step name must include its workflow job key and row identity")
    record.update(
        execution_id=execution_id, kind=args.kind, step_name=args.step_name, output_root=output_root
    )
    return record


def run_command(args: argparse.Namespace) -> int:
    paths = output_paths(args.output)
    required = {"python": {"results"}, "package-preparation": {"preparation"}, "compile": set()}
    if set(paths) != required[args.kind]:
        raise ValueError("Output roles differ from execution kind")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ValueError("A wrapped command is required")
    record = fresh_record(args)
    paths["execution-log"] = str(record["output_root"]) + "/execution.log"
    record.update(record_type="subprocess-completion", emitter_step_name=args.step_name)
    emit({**record, "event": "started", "expected_outputs": paths})
    with Path(paths["execution-log"]).open("xb") as log:
        header = (
            f"Python {sys.version.split()[0]}; wrapped command: {json.dumps(command)}\n".encode()
        )
        log.write(header)
        sys.stdout.buffer.write(header)
        sys.stdout.buffer.flush()
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as child:
            if child.stdout is None:
                raise RuntimeError("Wrapped command has no output pipe")
            for chunk in iter(lambda: child.stdout.read1(65536), b""):
                log.write(chunk)
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            exit_code = child.wait()
    current = identity()
    if any(record.get(key) != value for key, value in current.items()):
        raise ValueError("Checkout or execution identity changed during the command")
    # Start a separate line even if the child did not terminate its final line.
    print(flush=True)
    emit({**record, "event": "completed", "exit_code": exit_code, "outputs": pointers(paths)})
    step_output(
        {"execution_log": paths["execution-log"], "execution_id": str(record["execution_id"])}
    )
    return exit_code


def reserve_action(args: argparse.Namespace) -> int:
    record = fresh_record(args)
    mode = args.kind.removeprefix("native-")
    root = str(record["output_root"])
    paths = {"results": f"{root}/{mode}-results.xml", "execution-log": f"{root}/{mode}.log"}
    record.update(
        record_type="action-observation",
        event="reserved",
        emitter_step_name=args.emitter_step_name,
        observer_step_name=args.observer_step_name,
        expected_outputs=paths,
    )
    reservation = Path(root) / "reservation.json"
    with reservation.open("x", encoding="utf-8") as stream:
        json.dump(record, stream)
    emit(record)
    step_output(
        {
            "root": root,
            "reservation": reservation.as_posix(),
            "results": paths["results"],
            "log": paths["execution-log"],
        }
    )
    return 0


def observe_action(args: argparse.Namespace) -> int:
    reservation = ordinary(args.reservation)
    if reservation.stat().st_size > 32768:
        raise ValueError("Action reservation exceeds 32 KiB")
    record = json.loads(reservation.read_bytes())
    current = identity()
    if any(record.get(key) != value for key, value in current.items()):
        raise ValueError("Action reservation belongs to another run, attempt, job or checkout")
    if args.outcome not in {"success", "failure"}:
        raise ValueError("Skipped or unavailable actions are not executed evidence")
    if (
        record.get("record_type") != "action-observation"
        or record.get("event") != "reserved"
        or re.fullmatch(r"[0-9a-f]{32}", str(record.get("execution_id"))) is None
        or record.get("output_root") != ".release-evidence/" + str(record.get("execution_id"))
        or reservation != ordinary(Path(str(record["output_root"])) / "reservation.json")
    ):
        raise ValueError("Invalid owned action reservation")
    record.update(
        event="observed",
        emitter_step_name=record.pop("observer_step_name"),
        runner_outcome=args.outcome,
        outputs=pointers(record.pop("expected_outputs")),
    )
    emit(record)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run")
    run.add_argument("--kind", choices=("python", "package-preparation", "compile"), required=True)
    run.add_argument("--step-name", required=True)
    run.add_argument("--output", action="append", default=[])
    run.add_argument("command", nargs=argparse.REMAINDER)
    reserve = commands.add_parser("reserve")
    reserve.add_argument("--kind", choices=("native-editmode", "native-playmode"), required=True)
    reserve.add_argument("--step-name", required=True)
    reserve.add_argument("--emitter-step-name", required=True)
    reserve.add_argument("--observer-step-name", required=True)
    observe = commands.add_parser("observe")
    observe.add_argument("--reservation", type=Path, required=True)
    observe.add_argument("--outcome", required=True)
    args = parser.parse_args()
    try:
        match args.action:
            case "run":
                return run_command(args)
            case "reserve":
                return reserve_action(args)
            case "observe":
                return observe_action(args)
            case _:
                raise ValueError("Unsupported evidence operation")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
