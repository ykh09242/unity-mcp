"""Private, bounded request/response pipe to one optional local encoder per server."""

import atexit
import json
import math
from pathlib import Path
from queue import Empty, Queue
import struct
import subprocess
from threading import Lock, Thread
from time import monotonic

from services.registry import semantic_profile


_GUARD = Lock()
_WORKER_PATH = Path(__file__).with_name("semantic_labse_worker.py")
_MAX_DOCS = 2048
_MAX_CHARS = 1024


class LocalWorker:
    def __init__(self, profile: Path):
        semantic_profile.verify_weights(profile)
        self.process = subprocess.Popen(
            [str(semantic_profile.runtime_python(profile)), "-I", str(_WORKER_PATH), str(profile)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.documents = None
        self.profile = profile

    def stop(self) -> None:
        try:
            self.process.kill()
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()

    def _read_exactly(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            part = self.process.stdout.read(size - len(data))
            if not part:
                raise OSError("Local encoder closed its pipe")
            data.extend(part)
        return bytes(data)

    def query(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        request = {"query": query[:_MAX_CHARS]}
        if self.documents != documents:
            request["documents"] = documents
        message = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        payload = memoryview(message.encode("utf-8", errors="backslashreplace"))
        completed = Queue(maxsize=1)

        def exchange():
            try:
                sent = 0
                while sent < len(payload):
                    written = self.process.stdin.write(payload[sent:])
                    if not written:
                        raise OSError("Local encoder closed its pipe")
                    sent += written
                if struct.unpack("<I", self._read_exactly(4))[0] != len(documents):
                    raise ValueError("Invalid local encoder result size")
                completed.put(self._read_exactly(4 * len(documents)))
            except (OSError, ValueError):
                completed.put(b"")

        # A deadline covers writes as well as reads, including startup and first inference.
        Thread(target=exchange, daemon=True).start()
        try:
            data = completed.get(timeout=45 if "documents" in request else 10)
        except Empty as exc:
            raise TimeoutError("Local encoder timed out") from exc
        if not data:
            raise ValueError("Invalid local encoder result")
        values = struct.unpack(f"<{len(documents)}f", data)
        if not all(map(math.isfinite, values)):
            raise ValueError("Invalid local encoder scores")
        self.documents = documents
        return values


_worker: LocalWorker | None = None
_failed_profile: Path | None = None
_retry_after = 0.0


def _stop() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


atexit.register(_stop)


def quality_scores(query: str, documents: tuple[str, ...]) -> tuple[float, ...] | None:
    global _worker, _failed_profile, _retry_after
    if not documents:
        return None
    profile = semantic_profile.profile_dir()
    if not _GUARD.acquire(timeout=10):
        return None
    try:
        if not semantic_profile.ready_profile(profile):
            _stop()
            return None
        if _worker is not None and _worker.profile != profile:
            _stop()
        if _failed_profile == profile and monotonic() < _retry_after:
            return None
        try:
            if _worker is None:
                _worker = LocalWorker(profile)
            scores = []
            for start in range(0, len(documents), _MAX_DOCS):
                batch = tuple(text[:_MAX_CHARS] for text in documents[start : start + _MAX_DOCS])
                scores.extend(_worker.query(query, batch))
            return tuple(scores)
        except (OSError, ValueError, subprocess.SubprocessError):
            _stop()
            _failed_profile = profile
            _retry_after = monotonic() + 60
            return None
    finally:
        _GUARD.release()
