"""Offline, bounded custom-tool templates and no-replace source publication.

The lock coordinates scaffold writers only. Metadata absence is an observation;
Unity and other writers may create metadata independently of source publication.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
from string import Template
import unicodedata
from uuid import uuid4


_KEYWORDS = frozenset(
    "abstract as base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new null object operator out override params private protected public readonly ref return sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using virtual void volatile while".split()
)
_RESERVED_CLASSES = frozenset(
    {
        "Parameters",
        "Result",
        "ActionKind",
        "JobRecord",
        "JobState",
        "HandleCommand",
        "ExecuteWork",
        "currentJob",
        "MaxTextLength",
        "System",
        "Newtonsoft",
        "MCPForUnity",
    }
)
_DEVICE_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)
_DEPENDENCY_TYPES = frozenset(
    {
        "System.Char",
        "System.Threading.Tasks.Task",
        "System.Threading.CancellationToken",
        "System.Guid",
        "System.OperationCanceledException",
        "System.Math",
        "System.Globalization.NumberStyles",
        "System.Globalization.CultureInfo",
        "Newtonsoft.Json.Linq.JObject",
        "Newtonsoft.Json.Linq.JTokenType",
        "Newtonsoft.Json.JsonConverterAttribute",
        "Newtonsoft.Json.Formatting",
        "Newtonsoft.Json.Converters.StringEnumConverter",
        "MCPForUnity.Editor.Tools.McpForUnityToolAttribute",
        "MCPForUnity.Editor.Tools.ToolParameterAttribute",
        "MCPForUnity.Editor.Helpers.PropertyConversion",
        "MCPForUnity.Editor.Helpers.SuccessResponse",
        "MCPForUnity.Editor.Helpers.ErrorResponse",
        "MCPForUnity.Editor.Helpers.PendingResponse",
    }
)


@dataclass(frozen=True, slots=True)
class ScaffoldOptions:
    class_name: str
    namespace: str
    tool_name: str
    project_root: Path
    output: str | None = None
    template: str = "sync"
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class ScaffoldReceipt:
    template: str
    namespace: str
    class_name: str
    tool_name: str
    destination: str
    byte_count: int
    sha256: str
    created: bool
    cleanup_complete: bool
    reason: str
    exit_code: int
    residual_paths: tuple[str, ...] = ()
    metadata_provenance: str = "not_observed"
    compile_verified: bool = False
    discovery_verified: bool = False
    tool_name_uniqueness: str = "unknown"


class ScaffoldError(Exception):
    """Static, path-redacted refusal and any incomplete owned cleanup."""

    def __init__(self, reason: str, exit_code: int = 2, *, residual_paths: tuple[str, ...] = ()):
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code
        self.residual_paths = residual_paths


def _identifier(value: str) -> str:
    decoded = value[1:] if value.startswith("@") else value
    if (
        not decoded
        or len(value) > 64
        or unicodedata.normalize("NFC", value) != value
        or "__" in decoded
    ):
        raise ScaffoldError("invalid_identifier")
    if value == decoded and decoded in _KEYWORDS:
        raise ScaffoldError("keyword_identifier")
    for index, character in enumerate(decoded):
        allowed = {"Lu", "Ll", "Lt", "Lm", "Lo", "Nl"}
        if index:
            allowed |= {"Mn", "Mc", "Nd", "Pc"}
        if character != "_" and unicodedata.category(character) not in allowed:
            raise ScaffoldError("invalid_identifier")
    return decoded


def _names(options: ScaffoldOptions) -> None:
    canonical = _identifier(options.class_name)
    if canonical in _RESERVED_CLASSES:
        raise ScaffoldError("reserved_class")
    if len(options.class_name.encode("utf-8")) > 128 or len(options.namespace) > 512:
        raise ScaffoldError("identifier_limit")
    decoded = ""
    if options.namespace:
        parts = options.namespace.split(".")
        if len(parts) > 32:
            raise ScaffoldError("namespace_limit")
        decoded = ".".join(_identifier(part) for part in parts)
        if decoded.startswith("System"):
            raise ScaffoldError("unsupported_dto_namespace")
    full_class = decoded + "." + canonical if decoded else canonical
    if any(
        dependency == full_class or dependency.startswith(full_class + ".")
        for dependency in _DEPENDENCY_TYPES
    ):
        raise ScaffoldError("dependency_shadow")
    if decoded and any(
        decoded == dependency or decoded.startswith(dependency + ".")
        for dependency in _DEPENDENCY_TYPES
    ):
        raise ScaffoldError("dependency_shadow")
    if not 1 <= len(options.tool_name) <= 128 or not all(
        c.isalnum() or c in "_.-" for c in options.tool_name
    ):
        raise ScaffoldError("invalid_tool_name")


def _parts(options: ScaffoldOptions) -> tuple[str, ...]:
    value = (
        options.output
        if options.output is not None
        else f"Assets/Editor/CustomTools/{options.class_name}.cs"
    )
    value = value.replace("\\", "/")
    parts = tuple(value.split("/"))
    if PureWindowsPath(value).drive or value.startswith("/") or len(parts) > 16:
        raise ScaffoldError("invalid_output_path")
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} or c in '<>:"|?*' for c in part)
            or len(part.encode("utf-8")) > 255
        ):
            raise ScaffoldError("invalid_output_path")
        if (
            unicodedata.normalize("NFC", part) != part
            or part.split(".")[0].upper() in _DEVICE_NAMES
        ):
            raise ScaffoldError("invalid_output_path")
    if parts[0] != "Assets" or "Editor" not in parts[1:-1] or not parts[-1].endswith(".cs"):
        raise ScaffoldError("editor_output_required")
    return parts


def _identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ScaffoldError("unsafe_path")
    return info.st_dev, info.st_ino


def _directory(path: Path) -> None:
    _identity(path)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ScaffoldError("unsafe_path")


def _entries(path: Path) -> tuple[str, ...]:
    names: list[str] = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names) >= 1024:
                raise ScaffoldError("directory_limit")
            names.append(entry.name)
    return tuple(names)


def _fold(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _root(value: Path) -> Path:
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in str(value)):
        raise ScaffoldError("invalid_project_root")
    root = Path(os.path.abspath(value))
    if len(str(root)) > 4096 or len(root.parents) > 128:
        raise ScaffoldError("project_root_limit")
    for parent in (*reversed(root.parents), root):
        _directory(parent)
    for relative in (
        "Assets",
        "ProjectSettings",
        "Packages",
        "ProjectSettings/ProjectVersion.txt",
        "Packages/manifest.json",
    ):
        path = root / relative
        names = _entries(path.parent)
        if path.name not in names or any(
            _fold(n) == _fold(path.name) and n != path.name for n in names
        ):
            raise ScaffoldError("invalid_project_root")
        _identity(path)
        if relative in {"Assets", "ProjectSettings", "Packages"}:
            _directory(path)
        elif not stat.S_ISREG(path.lstat().st_mode):
            raise ScaffoldError("invalid_project_root")
    return root


def _check_destination(root: Path, parts: tuple[str, ...]) -> None:
    parent = root
    for index, part in enumerate(parts):
        names = _entries(parent)
        if index and any(name.lower().endswith((".asmdef", ".asmref")) for name in names):
            raise ScaffoldError("assembly_boundary")
        matches = [name for name in names if _fold(name) == _fold(part)]
        if any(name != part for name in matches):
            raise ScaffoldError("case_collision")
        if index == len(parts) - 1:
            if matches:
                raise ScaffoldError("destination_exists")
            if any(_fold(name) == _fold(part + ".meta") for name in names):
                raise ScaffoldError("metadata_exists")
            return
        if not matches:
            return
        parent /= part
        _directory(parent)


def _unlink_owned(path: Path, identity: tuple[int, int]) -> bool:
    try:
        if _identity(path) != identity:
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except (OSError, ScaffoldError):
        return False


def scaffold(options: ScaffoldOptions) -> ScaffoldReceipt:
    """Render fully, check bounded scope, and publish without replacing source."""
    _names(options)
    parts = _parts(options)
    payload = _render(options)
    try:
        root = _root(options.project_root)
        _check_destination(root, parts)
    except OSError as error:
        raise ScaffoldError("invalid_project_root") from error
    destination = root.joinpath(*parts)
    common = {
        "template": options.template,
        "namespace": options.namespace,
        "class_name": options.class_name,
        "tool_name": options.tool_name,
        "destination": "/".join(parts),
        "byte_count": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if options.dry_run:
        return ScaffoldReceipt(
            **common, created=False, cleanup_complete=True, reason="dry_run", exit_code=0
        )
    lock = root / ".unity-mcp-scaffold.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ScaffoldError("busy", 1) from error
    except OSError as error:
        raise ScaffoldError("publication_failed", 1) from error
    lock_identity = None
    staging = None
    staging_identity = None
    directories: list[tuple[Path, tuple[int, int]]] = []
    residual: list[str] = []
    published = False
    failure = None
    provenance = "not_observed"
    try:
        with os.fdopen(descriptor, "wb") as stream:
            info = os.fstat(stream.fileno())
            lock_identity = info.st_dev, info.st_ino
            stream.write(b"unity-mcp scaffold writer\n")
        _check_destination(root, parts)
        parent = root
        for part in parts[:-1]:
            parent /= part
            try:
                parent.mkdir()
                directories.append((parent, _identity(parent)))
            except FileExistsError:
                _directory(parent)
        _check_destination(root, parts)
        staging = destination.parent / (".unity-mcp-" + uuid4().hex + ".tmp")
        descriptor = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            info = os.fstat(stream.fileno())
            staging_identity = info.st_dev, info.st_ino
            if stream.write(payload) != len(payload):
                raise OSError("Incomplete scaffold write")
            stream.flush()
            os.fsync(stream.fileno())
        _check_destination(root, parts)
        if _identity(staging) != staging_identity:
            raise ScaffoldError("staging_ownership_changed", 1)
        os.link(staging, destination, follow_symlinks=False)
        published = True
        try:
            if any(
                _fold(name) == _fold(destination.name + ".meta")
                for name in _entries(destination.parent)
            ):
                provenance = "unknown"
        except (OSError, ScaffoldError):
            provenance = "unknown"
    except ScaffoldError as error:
        failure = error
    except OSError as error:
        failure = ScaffoldError("publication_failed", 1)
        failure.__cause__ = error
    finally:
        if (
            staging is not None
            and staging_identity is not None
            and not _unlink_owned(staging, staging_identity)
        ):
            residual.append(staging.relative_to(root).as_posix())
        if lock_identity is not None and not _unlink_owned(lock, lock_identity):
            residual.append(lock.relative_to(root).as_posix())
        if not published:
            for directory, identity in reversed(directories):
                try:
                    if _identity(directory) == identity:
                        directory.rmdir()
                except FileNotFoundError:
                    continue
                except (OSError, ScaffoldError):
                    # Preserve new content/replacements and disclose incomplete cleanup.
                    residual.append(directory.relative_to(root).as_posix())
    if published:
        return ScaffoldReceipt(
            **common,
            created=True,
            cleanup_complete=not residual,
            reason="cleanup_incomplete" if residual else "created",
            exit_code=1 if residual else 0,
            residual_paths=tuple(residual),
            metadata_provenance=provenance,
        )
    if failure is not None:
        raise ScaffoldError(
            failure.reason, failure.exit_code, residual_paths=tuple(residual)
        ) from failure
    raise ScaffoldError("publication_failed", 1, residual_paths=tuple(residual))


_PARAMETERS = """
    public class Parameters
    {
        [global::MCPForUnity.Editor.Tools.ToolParameterAttribute("Text to echo", MinLength = 1, MaxLength = 128)]
        public string text = "";

        [global::MCPForUnity.Editor.Tools.ToolParameterAttribute("Repeat count", Required = false, DefaultValue = "1", Minimum = 1, Maximum = 8)]
        public int count = 1;
$poll_parameters
    }

    public class Result
    {
        public string text = "";
        public int count = 1;
    }

    // Replace this bounded, pure demonstration with your project work.
    private static Result ExecuteWork(Parameters parameters)
    {
        return new Result { text = parameters.text, count = parameters.count };
    }
"""

_VALIDATION = """
        if (arguments == null || arguments["text"]?.Type != global::Newtonsoft.Json.Linq.JTokenType.String)
            return new global::MCPForUnity.Editor.Helpers.ErrorResponse("text_required");
        string text = (string)arguments["text"];
        int length = text.Length;
        for (int index = 0; index + 1 < text.Length; index++)
        {
            if (global::System.Char.IsHighSurrogate(text[index]) && global::System.Char.IsLowSurrogate(text[index + 1]))
            {
                length--;
                index++;
            }
        }
        if (length < 1 || length > MaxTextLength)
            return new global::MCPForUnity.Editor.Helpers.ErrorResponse("text_out_of_bounds");
        int normalizedCount = 1;
        if (arguments["count"] != null)
        {
            var token = arguments["count"];
            if ((token.Type != global::Newtonsoft.Json.Linq.JTokenType.Integer && token.Type != global::Newtonsoft.Json.Linq.JTokenType.Float)
                || !double.TryParse(token.ToString(global::Newtonsoft.Json.Formatting.None), global::System.Globalization.NumberStyles.Float,
                    global::System.Globalization.CultureInfo.InvariantCulture, out double count)
                || count < 1 || count > 8 || count != global::System.Math.Truncate(count))
                return new global::MCPForUnity.Editor.Helpers.ErrorResponse("count_out_of_bounds");
            normalizedCount = (int)count;
        }
"""

_SYNC = """
    public static object HandleCommand(global::Newtonsoft.Json.Linq.JObject arguments)
    {
$validation
        var normalized = new global::Newtonsoft.Json.Linq.JObject { ["text"] = text, ["count"] = normalizedCount };
        var parameters = global::MCPForUnity.Editor.Helpers.PropertyConversion.ConvertTo<Parameters>(normalized);
        return new global::MCPForUnity.Editor.Helpers.SuccessResponse("Scaffold completed.", ExecuteWork(parameters));
    }
"""

_POLL_PARAMETERS = """
        [global::MCPForUnity.Editor.Tools.ToolParameterAttribute("Start or inspect the owned job", Required = false, DefaultValue = "start")]
        public ActionKind action = ActionKind.start;

        [global::MCPForUnity.Editor.Tools.ToolParameterAttribute("ID returned by start", Required = false, AllowNull = true, MaxLength = 64)]
        public string job_id = null;
"""

_POLL = """
    [global::Newtonsoft.Json.JsonConverterAttribute(typeof(global::Newtonsoft.Json.Converters.StringEnumConverter))]
    public enum ActionKind { start, status }
    private enum JobState { pending, processing, done }
    private class JobRecord
    {
        public string id;
        public Parameters parameters;
        public JobState state;
        public Result result;
    }
    private static JobRecord currentJob;

    // Main-thread cooperative sample; no detached/background worker or durable state.
    public static async global::System.Threading.Tasks.Task<object> HandleCommand(
        global::Newtonsoft.Json.Linq.JObject arguments, global::System.Threading.CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
$validation
        if (arguments["action"] != null && (arguments["action"].Type != global::Newtonsoft.Json.Linq.JTokenType.String
            || ((string)arguments["action"] != "start" && (string)arguments["action"] != "status")))
            return new global::MCPForUnity.Editor.Helpers.ErrorResponse("invalid_action");
        if (arguments["job_id"] != null && arguments["job_id"].Type != global::Newtonsoft.Json.Linq.JTokenType.Null
            && (arguments["job_id"].Type != global::Newtonsoft.Json.Linq.JTokenType.String || ((string)arguments["job_id"]).Length > 64))
            return new global::MCPForUnity.Editor.Helpers.ErrorResponse("invalid_job_id");
        var normalized = new global::Newtonsoft.Json.Linq.JObject { ["text"] = text, ["count"] = normalizedCount };
        if (arguments["action"] != null)
            normalized["action"] = (string)arguments["action"];
        if (arguments["job_id"] != null)
            normalized["job_id"] = (string)arguments["job_id"];
        var parameters = global::MCPForUnity.Editor.Helpers.PropertyConversion.ConvertTo<Parameters>(normalized);
        if (parameters.action == ActionKind.start)
        {
            if (currentJob != null && currentJob.state != JobState.done)
                return new global::MCPForUnity.Editor.Helpers.ErrorResponse("job_busy");
            currentJob = new JobRecord { id = global::System.Guid.NewGuid().ToString("N"), parameters = parameters, state = JobState.pending };
            return new global::MCPForUnity.Editor.Helpers.PendingResponse("Job created.", 0.1, new { job_id = currentJob.id });
        }
        var job = currentJob;
        if (job == null || parameters.job_id != job.id)
            return new global::MCPForUnity.Editor.Helpers.ErrorResponse("job_not_found");
        if (job.state == JobState.done)
            return new global::MCPForUnity.Editor.Helpers.SuccessResponse("Scaffold completed.", job.result);
        if (job.state == JobState.processing)
            return new global::MCPForUnity.Editor.Helpers.PendingResponse("Job processing.", 0.1, new { job_id = job.id });
        job.state = JobState.processing;
        try
        {
            await global::System.Threading.Tasks.Task.Yield();
            cancellationToken.ThrowIfCancellationRequested();
            job.result = ExecuteWork(job.parameters);
            job.state = JobState.done;
            return new global::MCPForUnity.Editor.Helpers.SuccessResponse("Scaffold completed.", job.result);
        }
        catch (global::System.OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            job.state = JobState.pending;
            throw;
        }
    }
"""


def _render(options: ScaffoldOptions) -> bytes:
    match options.template:
        case "sync":
            polling, poll_parameters, handler = "", "", _SYNC
        case "async-polling":
            polling, poll_parameters, handler = (
                ', RequiresPolling = true, PollAction = "status", MaxPollSeconds = 30',
                _POLL_PARAMETERS,
                _POLL,
            )
        case _:
            raise ScaffoldError("invalid_template")
    parameters = Template(_PARAMETERS).substitute(poll_parameters=poll_parameters)
    handler = Template(handler).substitute(validation=_VALIDATION)
    body = (
        "// Generated offline. Requires schema-version-1 server support; compile and review before use.\n"
        "// Namespace does not make the wire tool name unique. No background scheduling is provided.\n"
        f"[global::MCPForUnity.Editor.Tools.McpForUnityToolAttribute({json.dumps(options.tool_name, ensure_ascii=True)},\n"
        f'    Description = "Owned custom tool scaffold", SchemaVersion = 1, StructuredOutput = true, OutputDataType = typeof({options.class_name}.Result){polling})]\n'
        f"public static class {options.class_name}\n{{\n    private const int MaxTextLength = 128;\n"
        + parameters
        + handler
        + "}\n"
    )
    if options.namespace:
        body = (
            "namespace "
            + options.namespace
            + "\n{\n"
            + "\n".join("    " + line if line else "" for line in body.splitlines())
            + "\n}\n"
        )
    payload = body.encode("utf-8")
    if len(payload) > 32768:
        raise ScaffoldError("source_limit")
    return payload
