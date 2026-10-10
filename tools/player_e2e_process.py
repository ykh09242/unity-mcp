"""Owned subprocess execution with bounded logs and fresh main-loop observations."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import signal
from threading import Thread
import time

from player_e2e_artifacts import read_json, write_json, unique_object

LOG_LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True)
class Invocation:
    """An owned command and its independent process lifetime policy."""

    command: list[str]
    directory: Path
    timeout: float = 75
    cancel_on_progress: bool = False
    deadline_after_progress: float | None = None
    environment: dict[str, str] | None = None


def stop_owned_tree(process: subprocess.Popen) -> None:
    """Bound termination to this still-owned process tree and require its parent exit."""
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
    process.wait(timeout=10)


def execute(invocation: Invocation) -> dict:
    """Drain even truncated output, kill only the owned child, and always reap it."""
    directory = invocation.directory
    directory.mkdir(parents=True, exist_ok=True)
    request = (
        read_json(directory / "request.json", 16384)
        if (invocation.cancel_on_progress or invocation.deadline_after_progress is not None)
        else None
    )
    started = int(time.time() * 1000)
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    environment = os.environ.copy()
    if invocation.environment:
        environment.update(invocation.environment)
    process = subprocess.Popen(
        invocation.command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
        startupinfo=startup,
        env=environment,
        start_new_session=os.name != "nt",
    )
    overflow = []
    errors = []

    def drain() -> None:
        retained = 0
        try:
            with (directory / "process.log").open("xb") as log:
                while chunk := process.stdout.read(8192):
                    log.write(chunk[: max(0, LOG_LIMIT - retained)])
                    retained += len(chunk)
                    if retained > LOG_LIMIT and not overflow:
                        overflow.append(True)
        except OSError as exc:
            errors.append(str(exc))

    thread = Thread(target=drain, daemon=True)
    thread.start()
    observations = []
    progress_errors = []
    cancelled = False
    forced = False
    live_deadline_armed = False
    deadline = time.monotonic() + invocation.timeout
    try:
        while process.poll() is None:
            progress_path = directory / "progress.json"
            if progress_path.exists():
                try:
                    from cli.utils.play_scenario_player_progress import read_progress_bytes

                    progress = json.loads(
                        read_progress_bytes(progress_path), object_pairs_hook=unique_object
                    )
                    if not isinstance(progress, dict):
                        raise ValueError("Progress snapshot must be an object")
                    if not observations or progress != observations[-1]:
                        if len(observations) >= 256:
                            raise ValueError("Progress snapshot count exceeds bound")
                        observations.append(progress)
                    matching_live = (
                        request is not None
                        and type(progress.get("main_loop_sequence")) is int
                        and progress["main_loop_sequence"] > 0
                        and type(progress.get("heartbeat_unix_ms")) is int
                        and progress["heartbeat_unix_ms"] > 0
                        and progress.get("process_id") == process.pid
                        and all(
                            progress.get(key) == request.get(key)
                            for key in (
                                "job_id",
                                "definition_hash",
                                "build_id",
                                "payload_hash",
                                "build_source_revision",
                            )
                        )
                    )
                    if (
                        matching_live
                        and invocation.deadline_after_progress is not None
                        and not live_deadline_armed
                    ):
                        deadline = min(
                            deadline, time.monotonic() + invocation.deadline_after_progress
                        )
                        live_deadline_armed = True
                    if matching_live and invocation.cancel_on_progress and not cancelled:
                        (directory / "cancel").write_text("cancel\n", encoding="utf-8")
                        cancelled = True
                        deadline = min(deadline, time.monotonic() + 20)
                except FileNotFoundError:
                    pass
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    if len(progress_errors) < 16:
                        progress_errors.append(str(exc))
            if time.monotonic() >= deadline:
                forced = True
                stop_owned_tree(process)
                break
            time.sleep(0.1)
        actual_exit = process.wait(timeout=10)
    finally:
        stop_owned_tree(process)
        thread.join(timeout=10)
    if thread.is_alive() or errors:
        raise RuntimeError("Owned process output capture failed")
    result = {
        "command": invocation.command,
        "process_id": process.pid,
        "started_unix_ms": started,
        "finished_unix_ms": int(time.time() * 1000),
        "actual_exit_code": actual_exit,
        "process_ended": True,
        "forced_termination": forced,
        "live_deadline_armed": live_deadline_armed,
        "cancel_requested": cancelled,
        "log_truncated": bool(overflow),
        "progress_observations": observations,
        "progress_read_errors": progress_errors,
    }
    write_json(directory / "launch.json", result)
    return result
