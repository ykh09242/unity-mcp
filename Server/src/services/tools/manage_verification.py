"""Prepare and run bounded source-linked verification profiles."""

from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from models.verification import VerificationAction
from services.registry import mcp_for_unity_tool
from services.verification.service import VerificationService


@mcp_for_unity_tool(
    group="testing",
    description=(
        "Run project verification with source-linked stage reports. profiles reads defaults and explicit project overrides without writing. "
        "set_profile requires profile_id, profile and expected_hash from profiles (missing for a new file). "
        "prepare requires paths XOR change_set_id and accepts profile_id (script-check default), capture_baseline or baseline_id. "
        "It freezes selection and profile; poll status with preparation_id until ready before editing after a requested baseline. "
        "run takes preparation_id and returns one run_id; repeating it observes the same run. status accepts run_id XOR preparation_id. "
        "cancel and report take run_id. Cancellation intent does not prove child termination: inspect native_settled and retained child IDs. "
        "Defaults are script-check, editmode-check and playmode-check. Profiles allow refresh_policy, analysis_required, diagnostic_scope, "
        "fail_on severities, impact, test_mode and explicit test/assembly/category selectors plus bounded stage deadlines. "
        "No formatter, arbitrary command, scene switching, console clearing, focus change or replay. Unknown console loss prevents a clean pass. "
        "Reports last 30 minutes after settlement, bounded to 16 per owner/project and 64 shared; expired or restarted handles never rerun work."
    ),
    annotations=ToolAnnotations(title="Manage Verification", destructiveHint=False),
)
async def manage_verification(
    ctx: Context,
    action: VerificationAction,
    options: Annotated[
        dict[str, Any] | None, "Action-specific profile, selection or owned handle parameters."
    ] = None,
) -> dict[str, Any]:
    service = getattr(ctx.fastmcp, "_verification_service", None)
    if service is None:
        service = VerificationService()
        ctx.fastmcp._verification_service = service
    return await service.execute(ctx, action, options)
