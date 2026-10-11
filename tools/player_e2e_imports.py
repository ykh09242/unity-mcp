"""Own an actual Editor importer alongside one actual CLI/Player soak."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event
import time
from uuid import uuid4

from player_e2e_artifacts import read_json, write_json
from player_e2e_process import Invocation, execute

CASES = {"soak-import-overlap", "soak-import-cancel"}
FIXTURE = (
    "MCPForUnityTests.Editor.Windows.PlayScenarioPlayerImportOverlapTests.ConcurrentPlayerImports"
)
ROOT = Path(__file__).resolve().parents[1]


class ImportE2EError(RuntimeError):
    """The owned concurrent reader could not establish its execution contract."""


@dataclass(frozen=True, slots=True)
class ImportEditor:
    """The installed Editor and explicitly isolated, prepared test project."""

    executable: Path
    project: Path

    def validate(self) -> None:
        """Reject another project's checkout before starting an Editor."""
        project = self.project.resolve(strict=True)
        if not any(project.is_relative_to(ROOT / name) for name in (".tmp", ".unity-ci")):
            raise ImportE2EError("Import E2E requires a project in owned .tmp or .unity-ci scratch")
        if not self.executable.is_file():
            raise ImportE2EError("Import E2E requires an installed Editor executable")


def execute_with_imports(invocation: Invocation, editor: ImportEditor) -> None:
    """Reap the owned reader on startup failure, CLI failure, interruption and success."""
    editor.validate()
    directory = invocation.directory / "editor-import"
    directory.mkdir(parents=True, exist_ok=False)
    nonce = uuid4().hex
    write_json(directory / "config.json", {"schema_version": 1, "nonce": nonce})
    stop = Event()
    command = [
        str(editor.executable.resolve(strict=True)),
        "-batchmode",
        "-projectPath",
        str(editor.project.resolve(strict=True)),
        "-runTests",
        "-testPlatform",
        "EditMode",
        "-testFilter",
        FIXTURE,
        "-testResults",
        str((directory / "results.xml").resolve()),
        "-logFile",
        "-",
    ]
    reader = Invocation(
        command,
        directory,
        timeout=invocation.timeout + 300,
        environment={"MCP_PLAYER_IMPORT_CASE_ROOT": str(invocation.directory.resolve())},
        stop_requested=stop,
    )
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="player-import-e2e") as pool:
        future = pool.submit(execute, reader)
        try:
            ready = directory / "ready.json"
            deadline = time.monotonic() + 240
            while not ready.exists():
                if future.done():
                    future.result()
                    raise ImportE2EError("Editor importer ended before becoming ready")
                if time.monotonic() >= deadline:
                    raise ImportE2EError("Editor importer did not become ready before its deadline")
                time.sleep(0.1)
            if read_json(ready, 16384).get("nonce") != nonce:
                raise ImportE2EError("Editor importer readiness belongs to another invocation")
            execute(invocation)
            # Existence is the Editor's completion signal; publish only the full marker.
            temporary = directory / "finished.json.tmp"
            write_json(temporary, {"nonce": nonce, "finished_unix_ms": int(time.time() * 1000)})
            temporary.rename(directory / "finished.json")
            result = future.result(timeout=45)
            if result["actual_exit_code"] or result["forced_termination"]:
                raise ImportE2EError(
                    "Concurrent Editor importer failed; inspect its NUnit evidence"
                )
        finally:
            stop.set()
