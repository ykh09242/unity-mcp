"""
Privacy-focused, anonymous telemetry system for MCP for Unity
Inspired by Onyx's telemetry implementation with Unity-specific adaptations

Fire-and-forget telemetry sender with a single background worker.
- No context/thread-local propagation to avoid re-entrancy into tool resolution.
- Small network timeouts to prevent stalls.
"""

import contextlib
from dataclasses import dataclass
from enum import Enum
from importlib import import_module, metadata
import json
import inspect
import logging
import os
from pathlib import Path
import platform
import queue
import sys
import threading
import time
from typing import Any, Callable, Literal, get_args, get_origin, get_type_hints
from urllib.parse import urlparse
import uuid

import tomli

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore
    HAS_HTTPX = False

logger = logging.getLogger("unity-mcp-telemetry")
PACKAGE_NAME = "ykh09242-unity-mcp-server"


def _version_from_local_pyproject() -> str:
    """Locate the nearest pyproject.toml that matches our package name."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "pyproject.toml"
        if not candidate.exists():
            continue
        try:
            with candidate.open("rb") as f:
                data = tomli.load(f)
        except (OSError, tomli.TOMLDecodeError):
            continue

        project_table = data.get("project") or {}
        poetry_table = data.get("tool", {}).get("poetry", {})

        project_name = project_table.get("name") or poetry_table.get("name")
        if project_name and project_name.lower() != PACKAGE_NAME.lower():
            continue

        version = project_table.get("version") or poetry_table.get("version")
        if version:
            return version
    raise FileNotFoundError(f"pyproject.toml not found for {PACKAGE_NAME}")


def get_package_version() -> str:
    """
    Get package version in different ways:
    1. First we try the installed metadata - this is because uvx is used on the asset store
    2. If that fails, we try to read from pyproject.toml - this is available for users who download via Git
    Default is "unknown", but that should never happen
    """
    try:
        return metadata.version(PACKAGE_NAME)
    except Exception:
        # Fallback for development: read from pyproject.toml
        try:
            return _version_from_local_pyproject()
        except Exception:
            return "unknown"


MCP_VERSION = get_package_version()


class RecordType(str, Enum):
    """Types of telemetry records we collect"""
    VERSION = "version"
    STARTUP = "startup"
    USAGE = "usage"
    LATENCY = "latency"
    FAILURE = "failure"
    RESOURCE_RETRIEVAL = "resource_retrieval"
    TOOL_EXECUTION = "tool_execution"
    UNITY_CONNECTION = "unity_connection"
    CLIENT_CONNECTION = "client_connection"


class MilestoneType(str, Enum):
    """Major user journey milestones"""
    FIRST_STARTUP = "first_startup"
    FIRST_TOOL_USAGE = "first_tool_usage"
    FIRST_SCRIPT_CREATION = "first_script_creation"
    FIRST_SCENE_MODIFICATION = "first_scene_modification"
    MULTIPLE_SESSIONS = "multiple_sessions"
    DAILY_ACTIVE_USER = "daily_active_user"
    WEEKLY_ACTIVE_USER = "weekly_active_user"


def _error_class(error: object) -> str:
    """Return fixed diagnostic labels without formatting caller-controlled errors."""
    classes = (
        ("FileNotFoundError", FileNotFoundError),
        ("PermissionError", PermissionError),
        ("ConnectionError", ConnectionError),
        ("TimeoutError", TimeoutError),
        ("ValueError", ValueError),
        ("TypeError", TypeError),
        ("OSError", OSError),
        ("RuntimeError", RuntimeError),
        ("Exception", Exception),
    )
    if isinstance(error, Exception):
        for label, exception_type in classes:
            if isinstance(error, exception_type):
                return label
    elif isinstance(error, str):
        for label, _ in classes:
            if error == label:
                return label
    return "Exception"


_tool_actions: dict[str, tuple[frozenset[str], bool]] = {}


def register_tool_actions(tool_name: str, func: Callable) -> None:
    """Snapshot trusted built-in action contracts without importing tool modules."""
    original = inspect.unwrap(func)
    if type(tool_name) is not str or original.__module__ != f"services.tools.{tool_name}" or original.__name__ != tool_name:
        return  # Dynamic plugin schemas cannot add telemetry dimensions.
    labels: frozenset[str] = frozenset()
    normalize_case = False
    try:
        action_type = get_type_hints(original).get("action")
        if get_origin(action_type) is Literal:
            labels = frozenset(value for value in get_args(action_type) if type(value) is str)
        elif action_type is str:
            # Unrestricted tools already validate against this server-owned constant.
            actions = original.__globals__.get("ALL_ACTIONS", ())
            if type(actions) in (list, tuple, set, frozenset):
                labels = frozenset(value for value in actions if type(value) is str)
                normalize_case = True
    except Exception:
        pass  # Missing/unresolvable schemas suppress the dimension.
    _tool_actions[tool_name] = (labels, normalize_case)


def tool_action_label(tool_name: object, action: object) -> str | None:
    """Return only registered action labels; never format arbitrary caller values."""
    if type(tool_name) is not str or type(action) is not str:
        return None
    labels, normalize_case = _tool_actions.get(tool_name, (frozenset(), False))
    if not labels or len(action) > max(map(len, labels)):
        return None
    value = action.lower() if normalize_case else action
    return value if value in labels else None


def _metadata_only_error(data: dict[str, Any]) -> dict[str, Any]:
    """Detach metadata and constrain error/action dimensions at every boundary."""
    sanitized = dict(data)
    if "error" in sanitized and sanitized["error"] is not None:
        sanitized["error"] = _error_class(sanitized["error"])
    if "sub_action" in sanitized:
        label = tool_action_label(sanitized.get("tool_name"), sanitized["sub_action"])
        if label is None:
            sanitized.pop("sub_action")
        else:
            sanitized["sub_action"] = label
    return sanitized


@dataclass
class TelemetryRecord:
    """Structure for telemetry data"""
    record_type: RecordType
    timestamp: float
    customer_uuid: str
    session_id: str
    data: dict[str, Any]
    milestone: MilestoneType | None = None


class TelemetryConfig:
    """Telemetry configuration"""

    def __init__(self):
        """
        Prefer config file, then allow env overrides
        """
        server_config = None
        for modname in (
            # Use the same config module as the server entry point.
            "core.config",
            "src.core.config",
            "config",
            "src.config",
            "Server.config",
        ):
            try:
                mod = import_module(modname)
                server_config = getattr(mod, "config", None)
                if server_config is not None:
                    break
            except Exception:
                continue

        # Determine enabled flag: config -> env DISABLE_* opt-out
        cfg_enabled = True if server_config is None else bool(
            getattr(server_config, "telemetry_enabled", True))
        # No default destination: only explicit config or environment endpoints opt in.
        cfg_default = None if server_config is None else getattr(
            server_config, "telemetry_endpoint", None)
        default_ep = self._validated_endpoint(cfg_default, "") if cfg_default else ""
        self.default_endpoint = default_ep
        # Prefer config default; allow explicit env override only when set
        env_ep = os.environ.get("UNITY_MCP_TELEMETRY_ENDPOINT")
        if env_ep is not None and env_ep != "":
            self.endpoint = self._validated_endpoint(env_ep, default_ep)
        else:
            self.endpoint = default_ep
        self.enabled = cfg_enabled and bool(self.endpoint) and not self._is_disabled()
        try:
            logger.info(
                f"Telemetry configured: endpoint={self.endpoint} (default={default_ep}), timeout_env={os.environ.get('UNITY_MCP_TELEMETRY_TIMEOUT') or '<unset>'}")
        except Exception:
            pass

        # Local storage for UUID and milestones
        self.data_dir = self._get_data_directory()
        self.uuid_file = self.data_dir / "customer_uuid.txt"
        self.milestones_file = self.data_dir / "milestones.json"

        # Request timeout (small, fail fast). Override with UNITY_MCP_TELEMETRY_TIMEOUT
        try:
            self.timeout = float(os.environ.get(
                "UNITY_MCP_TELEMETRY_TIMEOUT", "1.5"))
        except Exception:
            self.timeout = 1.5
        try:
            logger.info(f"Telemetry timeout={self.timeout:.2f}s")
        except Exception:
            pass

        # Session tracking
        self.session_id = str(uuid.uuid4())

    def _is_disabled(self) -> bool:
        """Check if telemetry is disabled via environment variables"""
        disable_vars = [
            "DISABLE_TELEMETRY",
            "UNITY_MCP_DISABLE_TELEMETRY",
            "MCP_DISABLE_TELEMETRY"
        ]

        for var in disable_vars:
            if os.environ.get(var, "").lower() in ("true", "1", "yes", "on"):
                return True
        return False

    def _get_data_directory(self) -> Path:
        """Get directory for storing telemetry data"""
        if os.name == 'nt':  # Windows
            base_dir = Path(os.environ.get(
                'APPDATA', Path.home() / 'AppData' / 'Roaming'))
        elif os.name == 'posix':  # macOS/Linux
            if 'darwin' in os.uname().sysname.lower():  # macOS
                base_dir = Path.home() / 'Library' / 'Application Support'
            else:  # Linux
                base_dir = Path(os.environ.get('XDG_DATA_HOME',
                                Path.home() / '.local' / 'share'))
        else:
            base_dir = Path.home() / '.unity-mcp'

        data_dir = base_dir / 'UnityMCP'
        if self.enabled:
            data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    def _validated_endpoint(self, candidate: str, fallback: str) -> str:
        """Validate telemetry endpoint URL scheme; allow only http/https.
        Falls back to the provided default on error.
        """
        try:
            parsed = urlparse(candidate)
            if parsed.scheme not in ("https", "http"):
                raise ValueError(f"Unsupported scheme: {parsed.scheme}")
            # Basic sanity: require network location and path
            if not parsed.netloc:
                raise ValueError("Missing netloc in endpoint")
            # Reject localhost/loopback endpoints in production to avoid accidental local overrides
            host = parsed.hostname or ""
            if host in ("localhost", "127.0.0.1", "::1"):
                raise ValueError(
                    "Localhost endpoints are not allowed for telemetry")
            return candidate
        except Exception as e:
            logger.debug(
                f"Invalid telemetry endpoint '{candidate}', using default. Error: {e}",
                exc_info=True,
            )
            return fallback


class TelemetryCollector:
    """Main telemetry collection class"""

    def __init__(self):
        self.config = TelemetryConfig()
        self._customer_uuid: str | None = None
        self._milestones: dict[str, dict[str, Any]] = {}
        self._lock: threading.Lock = threading.Lock()
        # Bounded queue with single background worker (records only; no context propagation)
        self._queue: "queue.Queue[TelemetryRecord]" = queue.Queue(maxsize=1000)
        self._shutdown: bool = False
        self._worker: threading.Thread | None = None
        if not self.config.enabled:
            return
        # Load persistent data before starting worker so first events have UUID
        self._load_persistent_data()
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True)
        self._worker.start()

    def _load_persistent_data(self):
        """Load UUID and milestones from disk"""
        # Load customer UUID
        try:
            if self.config.uuid_file.exists():
                self._customer_uuid = self.config.uuid_file.read_text(
                    encoding="utf-8").strip() or str(uuid.uuid4())
            else:
                self._customer_uuid = str(uuid.uuid4())
                try:
                    self.config.uuid_file.write_text(
                        self._customer_uuid, encoding="utf-8")
                    if os.name == "posix":
                        os.chmod(self.config.uuid_file, 0o600)
                except OSError as e:
                    logger.debug(
                        f"Failed to persist customer UUID: {e}", exc_info=True)
        except (OSError, UnicodeDecodeError) as e:
            logger.debug(f"Failed to load customer UUID: {e}", exc_info=True)
            self._customer_uuid = str(uuid.uuid4())

        # Load milestones (failure here must not affect UUID)
        try:
            if self.config.milestones_file.exists():
                content = self.config.milestones_file.read_text(
                    encoding="utf-8")
                self._milestones = json.loads(content) or {}
                if not isinstance(self._milestones, dict):
                    self._milestones = {}
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug(f"Failed to load milestones: {e}", exc_info=True)
            self._milestones = {}

    def _save_milestones(self):
        """Save milestones to disk. Caller must hold self._lock."""
        try:
            self.config.milestones_file.write_text(
                json.dumps(self._milestones, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"Failed to save milestones: {e}", exc_info=True)

    def record_milestone(self, milestone: MilestoneType, data: dict[str, Any] | None = None) -> bool:
        """Record a milestone event, returns True if this is the first occurrence"""
        if not self.config.enabled:
            return False
        milestone_key = milestone.value
        with self._lock:
            if milestone_key in self._milestones:
                return False  # Already recorded
            milestone_data = {
                "timestamp": time.time(),
                "data": data or {},
            }
            self._milestones[milestone_key] = milestone_data
            self._save_milestones()

        # Also send as telemetry record
        self.record(
            record_type=RecordType.USAGE,
            data={"milestone": milestone_key, **(data or {})},
            milestone=milestone
        )

        return True

    def record(self,
               record_type: RecordType,
               data: dict[str, Any],
               milestone: MilestoneType | None = None):
        """Record a telemetry event (async, non-blocking)"""
        if not self.config.enabled:
            return

        # Allow fallback sender when httpx is unavailable (no early return)

        record = TelemetryRecord(
            record_type=record_type,
            timestamp=time.time(),
            customer_uuid=self._customer_uuid or "unknown",
            session_id=self.config.session_id,
            data=_metadata_only_error(data),
            milestone=milestone
        )
        # Enqueue for background worker (non-blocking). Drop on backpressure.
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            logger.debug(
                f"Telemetry queue full; dropping {record.record_type}")

    def _worker_loop(self):
        """Background worker that serializes telemetry sends."""
        while not self._shutdown:
            try:
                rec = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                # Run sender directly; do not reuse caller context/thread-locals
                self._send_telemetry(rec)
            except Exception:
                logger.debug("Telemetry worker send failed", exc_info=True)
            finally:
                with contextlib.suppress(Exception):
                    self._queue.task_done()

    def shutdown(self):
        """Shutdown the telemetry collector and worker thread."""
        self._shutdown = True
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def _send_telemetry(self, record: TelemetryRecord):
        """Send telemetry data to endpoint"""
        try:
            # System fingerprint (top-level remains concise; details stored in data JSON)
            _platform = platform.system()          # 'Darwin' | 'Linux' | 'Windows'
            _source = sys.platform                 # 'darwin' | 'linux' | 'win32'
            _platform_detail = f"{_platform} {platform.release()} ({platform.machine()})"
            _python_version = platform.python_version()

            # Enrich data JSON so BigQuery stores detailed fields without schema change
            enriched_data = _metadata_only_error(record.data or {})
            enriched_data.setdefault("platform_detail", _platform_detail)
            enriched_data.setdefault("python_version", _python_version)

            payload = {
                "record": record.record_type.value,
                "timestamp": record.timestamp,
                "customer_uuid": record.customer_uuid,
                "session_id": record.session_id,
                "data": enriched_data,
                "version": MCP_VERSION,
                "platform": _platform,
                "source": _source,
            }

            if record.milestone:
                payload["milestone"] = record.milestone.value

            # Prefer httpx when available; otherwise fall back to urllib
            if httpx:
                with httpx.Client(timeout=self.config.timeout) as client:
                    # Re-validate endpoint at send time to handle dynamic changes
                    endpoint = self.config._validated_endpoint(
                        self.config.endpoint, self.config.default_endpoint)
                    response = client.post(endpoint, json=payload)
                    if 200 <= response.status_code < 300:
                        logger.debug(f"Telemetry sent: {record.record_type}")
                    else:
                        logger.warning(
                            f"Telemetry failed: HTTP {response.status_code}")
            else:
                import urllib.request
                import urllib.error
                data_bytes = json.dumps(payload).encode("utf-8")
                endpoint = self.config._validated_endpoint(
                    self.config.endpoint, self.config.default_endpoint)
                req = urllib.request.Request(
                    endpoint,
                    data=data_bytes,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                        if 200 <= resp.getcode() < 300:
                            logger.debug(
                                f"Telemetry sent (urllib): {record.record_type}")
                        else:
                            logger.warning(
                                f"Telemetry failed (urllib): HTTP {resp.getcode()}")
                except urllib.error.URLError as ue:
                    logger.warning(f"Telemetry send failed (urllib): {ue}")

        except Exception as e:
            # Never let telemetry errors interfere with app functionality
            logger.debug(f"Telemetry send failed: {e}")


# Global telemetry instance
_telemetry_collector: TelemetryCollector | None = None


def get_telemetry() -> TelemetryCollector:
    """Get the global telemetry collector instance"""
    global _telemetry_collector
    if _telemetry_collector is None:
        _telemetry_collector = TelemetryCollector()
    return _telemetry_collector


def reset_telemetry():
    """Reset the global telemetry collector. For testing only."""
    global _telemetry_collector
    if _telemetry_collector is not None:
        _telemetry_collector.shutdown()
        _telemetry_collector = None


def record_telemetry(record_type: RecordType,
                     data: dict[str, Any],
                     milestone: MilestoneType | None = None):
    """Convenience function to record telemetry"""
    get_telemetry().record(record_type, data, milestone)


def record_milestone(milestone: MilestoneType, data: dict[str, Any] | None = None) -> bool:
    """Convenience function to record a milestone"""
    return get_telemetry().record_milestone(milestone, data)


def record_tool_usage(tool_name: str, success: bool, duration_ms: float, error: Exception | str | None = None, sub_action: str | None = None):
    """Record tool usage telemetry

    Args:
        tool_name: Name of the tool invoked (e.g., 'manage_scene').
        success: Whether the tool completed successfully.
        duration_ms: Execution duration in milliseconds.
        error: Optional exception or stable class label; messages are never sent.
        sub_action: Only labels from the registered built-in action contract are emitted.
    """
    data = {
        "tool_name": tool_name,
        "success": success,
        "duration_ms": round(duration_ms, 2)
    }

    label = tool_action_label(tool_name, sub_action)
    if label is not None:
        data["sub_action"] = label

    if error:
        data["error"] = _error_class(error)

    record_telemetry(RecordType.TOOL_EXECUTION, data)


def record_resource_usage(resource_name: str, success: bool, duration_ms: float, error: Exception | str | None = None):
    """Record resource usage telemetry

    Args:
        resource_name: Name of the resource invoked (e.g., 'get_tests').
        success: Whether the resource completed successfully.
        duration_ms: Execution duration in milliseconds.
        error: Optional exception or stable class label; messages are never sent.
    """
    data = {
        "resource_name": resource_name,
        "success": success,
        "duration_ms": round(duration_ms, 2)
    }

    if error:
        data["error"] = _error_class(error)

    record_telemetry(RecordType.RESOURCE_RETRIEVAL, data)


def record_latency(operation: str, duration_ms: float, metadata: dict[str, Any] | None = None):
    """Record latency telemetry"""
    data = {
        "operation": operation,
        "duration_ms": round(duration_ms, 2)
    }

    if metadata:
        data.update(metadata)

    record_telemetry(RecordType.LATENCY, data)


def record_failure(component: str, error: Exception | str, metadata: dict[str, Any] | None = None):
    """Record failure telemetry"""
    data = {
        "component": component,
        "error": _error_class(error)
    }

    if metadata:
        data.update(metadata)

    record_telemetry(RecordType.FAILURE, data)


def is_telemetry_enabled() -> bool:
    """Check if telemetry is enabled"""
    return get_telemetry().config.enabled
