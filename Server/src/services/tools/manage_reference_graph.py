"""Automatic reference evidence with authenticated, instance-specific ownership."""

from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from services.credential_lease import CredentialLeaseError
from services.operation_permissions import OperationPermissionError
from services.reference_graph.store import RevisionConflict
from services.registry import mcp_for_unity_tool


@mcp_for_unity_tool(
    group="assets",
    description=(
        "Maintain an automatic asset/code/external-config reference graph for the selected Unity "
        "instance and authenticated user. No extra database installation. sync starts/resumes background "
        "work; poll status for progress. query uses node='asset:<guid>', direction='incoming' or "
        "'outgoing', limit and returned cursor. diagnostics uses node, offset, limit and revision. "
        "For a verified first page, query/diagnostics accept consistency='checked' to synchronize "
        "and rehash external inputs; ordinary cached pages expose provider verification timestamps. "
        "Code, Addressables and wrapper analysis is conservative: possible/unresolved evidence never "
        "proves an asset safe to delete. Sources require explicit read-only root and include patterns; "
        "remote paths belong to the server and require its operator's grant. Authored changes use id "
        "and expected_version from the corresponding list action. declare also needs owner and edge "
        "{target,kind}; set_source/set_rule/set_policy need config. watch needs enabled. "
        "Source config: {root,include,exclude?,mappings?}; mapping {field,scheme,prefix?,suffix?}. "
        "Rule config: {call,argument,scheme,mode?,prefix?,suffix?}; {enabled:false} disables a rule. "
        "Policy config: {exclude:[relative patterns]}; there are no application-specific exclusions. "
        "sync {force:true} rehashes external inputs. rebuild retains authored declarations and grants. "
        "Normal domain reload retains scope; a new Editor process has a separate graph."
    ),
    annotations=ToolAnnotations(title="Reference Graph", destructiveHint=True, openWorldHint=True),
)
async def manage_reference_graph(
    ctx: Context,
    action: Literal[
        "status",
        "sync",
        "query",
        "diagnostics",
        "declarations",
        "declare",
        "remove_declaration",
        "sources",
        "set_source",
        "rules",
        "set_rule",
        "policy",
        "set_policy",
        "rebuild",
        "watch",
    ],
    options: Annotated[
        dict[str, Any] | None,
        Field(
            description="Only the selected action's documented fields; identity and database paths cannot be supplied."
        ),
    ] = None,
) -> dict[str, Any]:
    service = getattr(ctx.fastmcp, "_reference_graph_service", None)
    if service is None:
        return {"success": False, "error": "reference_graph_unavailable"}
    try:
        return await service.execute(ctx, action, options if options is not None else {})
    except (CredentialLeaseError, OperationPermissionError):
        raise
    except RevisionConflict as error:
        return {"success": False, "error": "reference_graph_changed", "message": str(error)}
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        return {"success": False, "error": "reference_graph_unavailable", "message": str(error)}
