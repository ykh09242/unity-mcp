"""Bounded multi-source preparation and recoverable native file application."""

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal, assert_never

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.manage_script import _validate_script_name
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class ScriptChangeFile(BaseModel):
    """One final UTF-8 source buffer guarded by the original raw-byte digest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    path: str = Field(min_length=1, max_length=512)
    expected_bytes_sha256: str
    contents: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Keep source identity explicit and reuse the existing per-file name contract."""
        normalized = value.replace("\\", "/")
        if (
            not normalized.startswith("Assets/")
            or not normalized.endswith(".cs")
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
            or ":" in normalized
            or _validate_script_name(PurePosixPath(normalized).stem)
        ):
            raise ValueError("path must identify an existing .cs source under Assets/.")
        return normalized

    @field_validator("expected_bytes_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        """Require an exact raw-file SHA256, including any UTF-8 BOM."""
        if re.fullmatch(r"[a-fA-F0-9]{64}", value) is None:
            raise ValueError("expected_bytes_sha256 must contain 64 hex characters.")
        return value.lower()

    @field_validator("contents")
    @classmethod
    def validate_contents(cls, value: str) -> str:
        """Bound actual encoded bytes rather than Unicode character count."""
        if "\0" in value or len(value.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("Each candidate must be NUL-free UTF-8 of at most 2 MiB.")
        return value


@mcp_for_unity_tool(
    unity_target="manage_script",
    description=(
        "Prepare, apply, inspect or recover 1–256 existing C# source replacements as one bounded "
        "change set. Prepare takes final contents plus each original raw-byte SHA256 (including BOM), "
        "validates each file and retains a local recovery manifest without source writes. Apply checks "
        "all original source/metadata preconditions before writes. Failures retain ordered confirmed, "
        "restored or uncertain outcomes; recovery never overwrites a changed external source. "
        "This is not a filesystem-wide transaction, semantic rename or callback rollback. "
        "UTF-8 BOM/newlines and metadata are preserved. This request does not schedule a refresh; "
        "Unity automatic/deferred source import may still compile changed scripts."
    ),
    annotations=ToolAnnotations(
        title="Script Change Set", destructiveHint=True, idempotentHint=False, openWorldHint=False
    ),
)
async def script_change_set(
    ctx: Context,
    action: Literal["prepare", "apply", "status", "recover"],
    files: Annotated[
        list[ScriptChangeFile] | None, "prepare only: ordered final source buffers."
    ] = None,
    change_set_id: Annotated[
        str | None, "apply/status/recover: identifier returned by prepare."
    ] = None,
    validation: Literal["basic", "standard", "comprehensive", "strict"] = "standard",
) -> dict[str, JsonValue]:
    """Admit bounded input before selection and preserve native recovery evidence unchanged."""
    params: dict[str, JsonValue]
    match action:
        case "prepare":
            if change_set_id is not None or files is None or not 1 <= len(files) <= 256:
                return {
                    "success": False,
                    "code": "invalid_change_set",
                    "message": "prepare requires 1–256 files and no change_set_id.",
                }
            paths = [file.path.casefold() for file in files]
            if len(set(paths)) != len(paths):
                return {
                    "success": False,
                    "code": "duplicate_path",
                    "message": "Each source path may occur only once.",
                }
            if sum(len(file.contents.encode("utf-8")) for file in files) > 16 * 1024 * 1024:
                return {
                    "success": False,
                    "code": "payload_too_large",
                    "message": "Combined candidates exceed 16 MiB.",
                }
            params = {
                "action": "prepare_change_set",
                "files": [file.model_dump() for file in files],
                "validation": validation,
            }
        case "apply" | "status" | "recover":
            if (
                files is not None
                or not isinstance(change_set_id, str)
                or re.fullmatch(r"[a-f0-9]{32}", change_set_id) is None
                or validation != "standard"
            ):
                return {
                    "success": False,
                    "code": "invalid_change_set",
                    "message": "apply/status/recover require only a valid change_set_id.",
                }
            native = {
                "apply": "apply_change_set",
                "status": "get_change_set",
                "recover": "recover_change_set",
            }[action]
            params = {"action": native, "change_set_id": change_set_id}
        case unreachable:
            assert_never(unreachable)
    instance = await get_unity_instance_from_context(ctx)
    from services.reference_graph.mutation_tracking import active_service, tracked_mutation
    from services.credential_lease import CredentialLeaseError

    paths = None
    preparation_error = None
    if action in {"apply", "recover"} and active_service(ctx, instance) is not None:
        preparation_error = "target_lookup_unavailable"
        try:
            observed = await send_with_unity_instance(
                async_send_command_with_retry,
                instance,
                "manage_script",
                {"action": "get_change_set", "change_set_id": change_set_id},
            )
        except CredentialLeaseError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError):
            observed = None
        if (
            isinstance(observed, dict)
            and observed.get("success")
            and isinstance(observed.get("data"), dict)
        ):
            rows = observed["data"].get("files")
            if (
                isinstance(rows, list)
                and rows
                and all(isinstance(row, dict) and isinstance(row.get("path"), str) for row in rows)
            ):
                paths = [row["path"] for row in rows]
                preparation_error = None
    response = await tracked_mutation(
        ctx,
        instance,
        "manage_script",
        params,
        lambda: send_with_unity_instance(
            async_send_command_with_retry,
            instance,
            "manage_script",
            params,
            retry_on_reload=action not in {"apply", "recover"},
        ),
        paths=paths,
        preparation_error=preparation_error,
    )
    return (
        response
        if isinstance(response, dict)
        else {"success": False, "code": "invalid_native_response", "message": str(response)}
    )
