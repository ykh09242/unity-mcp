"""Private, bounded request/response pipes to the optional local search workers."""

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


_WORKER_PATH = Path(__file__).with_name("semantic_labse_worker.py")
_RERANKER_PATH = Path(__file__).with_name("semantic_reranker_worker.py")
_MAX_DOCS = 2048
_MAX_CHARS = 1024


class LocalWorker:
    def __init__(self, profile: Path, *, reranker: bool = False):
        if reranker:
            semantic_profile.verify_reranker_weights(profile)
        else:
            semantic_profile.verify_weights(profile)
        script = _RERANKER_PATH if reranker else _WORKER_PATH
        self.process = subprocess.Popen(
            [str(semantic_profile.runtime_python(profile)), "-I", str(script), str(profile)],
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


class WorkerSlot:
    def __init__(self, *, reranker: bool = False):
        self.reranker = reranker
        self.guard = Lock()
        self.worker: LocalWorker | None = None
        self.failed_profile: Path | None = None
        self.retry_after = 0.0
        atexit.register(self.stop)

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

    def score(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...] | None:
        if not documents:
            return None
        try:
            profile = semantic_profile.profile_dir()
        except (OSError, RuntimeError):
            # Unix service accounts may have neither a home variable nor a passwd entry.
            return None
        if not self.guard.acquire(timeout=10):
            return None
        try:
            ready = (
                semantic_profile.reranker_ready if self.reranker else semantic_profile.ready_profile
            )
            if not ready(profile):
                self.stop()
                return None
            if self.worker is not None and self.worker.profile != profile:
                self.stop()
            if self.failed_profile == profile and monotonic() < self.retry_after:
                return None
            if self.worker is None:
                self.worker = LocalWorker(profile, reranker=self.reranker)
            scores = []
            for start in range(0, len(documents), _MAX_DOCS):
                batch = tuple(text[:_MAX_CHARS] for text in documents[start : start + _MAX_DOCS])
                scores.extend(self.worker.query(query, batch))
            return tuple(scores)
        except (OSError, ValueError, subprocess.SubprocessError):
            self.stop()
            self.failed_profile = profile
            self.retry_after = monotonic() + 60
            return None
        finally:
            self.guard.release()


quality_scores = WorkerSlot().score
rerank_scores = WorkerSlot(reranker=True).score
