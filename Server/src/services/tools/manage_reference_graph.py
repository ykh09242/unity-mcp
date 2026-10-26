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
        "explain {id} returns evidence for an edge. impact {node,direction?,limit?,cursor?,max_depth?} "
        "traverses reachable evidence with disk-backed cycle detection. path {node,target,direction?,limit?,cursor?} "
        "finds a shortest evidence path, returning its edges in target-to-start order; "
        "path_found=null means searching, false means unreachable. snapshot {name?,limit?,cursor?} "
        "captures a graph revision; continue until its state is ready. diff {before,after,limit?,cursor?} "
        "compares snapshot IDs, with historical evidence reauthorized on each page. Empty pages can "
        "have a next_cursor: always continue until null. release_query {id} releases a query_id; "
        "delete_snapshot {id} removes only a captured snapshot. Keep first-page options on continuation. "
        "For a verified first page, query/diagnostics/explain/impact/path/snapshot accept consistency='checked' to synchronize "
        "and rehash external inputs; ordinary cached pages expose provider verification timestamps. "
        "Code, Addressables and wrapper analysis is conservative: possible/unresolved evidence never "
        "proves an asset safe to delete. Sources require explicit read-only root and include patterns; "
        "remote paths belong to the server and require its operator's grant. Authored changes use id "
        "and expected_version from the corresponding list action. declare also needs owner and edge "
        "{target,kind}; set_source/set_rule/set_policy need config. watch needs enabled. "
        "Source config: {root,include,exclude?,mappings?,git_revision?,adapter?}; "
        "adapter defaults to {name:'json-csv',version:1}; mapping {field,scheme,prefix?,suffix?,values?}. "
        "Rule config: {call,argument,scheme,mode?,prefix?,suffix?}; {enabled:false} disables a rule. "
        "Policy config: {exclude:[relative patterns],respect_gitignore?:true}; root and nested .gitignore rules apply by default, independently of Git tracked state. "
        "sync {force:true} rehashes external inputs. rebuild retains authored declarations and grants. "
        "Normal domain reload retains scope; a new Editor process has a separate graph."
        " Mutation batches: begin_mutation {paths} captures incident evidence before writes; "
        "add_mutation_targets {id,paths} appends target pages before dispatch. These actions also "
        "accept guids instead of paths for assets in the synchronized inventory. finish_mutation "
        "{id,outcome:'accepted'|'failed'|'unknown'} seals caller-reported outcomes. "
        "await_mutation {id,wait_seconds?} waits for imported evidence without a total job deadline; "
        "mutation_diff {id,limit?,cursor?} pages direct incident changes after synchronization. "
        "release_mutation {id} releases private receipt state. A synchronized receipt is not proof "
        "that an uncertain mutation succeeded; concurrent changes can contribute to its delta."
        " Runtime evidence is opt-in: runtime_start {} (or {id} to recover a start) returns a capture ID; "
        "runtime_poll {id,limit?} ingests one native page, runtime_stop {id} ends observation, "
        "runtime_events {id,after?,limit?} and runtime_handles {id,after?,limit?} page historical "
        "loads and acquired/released tokens. runtime_status {id} is cached; runtime_sessions "
        "{after?,limit?} lists this scope's captures. runtime_release {id} stops and removes only "
        "that capture. Wrappers must explicitly call the bundled ReferenceGraphRuntime hooks. "
        "Runtime events do not prove a resource unused or a retained handle leaked."
        " overview reports scoped storage, cache presence, jobs and retention. retry resumes failed collection. "
        "export_config {kind:'rule'|'source'|'policy',after?,limit?} returns portable authored items in pages; "
        "pages are not an atomic snapshot. import_config {kind,id?,config,expected_version,enabled?,reauthorize?} "
        "imports one item with destination CAS; sources require reauthorize=true and current operator access. "
        "cleanup {before:<Unix timestamp>,limit?} removes completed query scratch older than before and "
        "abandoned staging in batches; repeat while more. It preserves active jobs, snapshots, captures, "
        "receipts and authored state, and clears memory caches only when idle. SQLite pages remain reusable."
    ),
    annotations=ToolAnnotations(title="Reference Graph", destructiveHint=True, openWorldHint=True),
)
async def manage_reference_graph(
    ctx: Context,
    action: Literal[
        "overview",
        "retry",
        "export_config",
        "import_config",
        "cleanup",
        "status",
        "sync",
        "query",
        "diagnostics",
        "explain",
        "impact",
        "path",
        "snapshot",
        "diff",
        "release_query",
        "delete_snapshot",
        "begin_mutation",
        "add_mutation_targets",
        "finish_mutation",
        "await_mutation",
        "mutation_diff",
        "release_mutation",
        "runtime_sessions",
        "runtime_start",
        "runtime_poll",
        "runtime_status",
        "runtime_stop",
        "runtime_events",
        "runtime_handles",
        "runtime_release",
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
