---
title: manage_reference_graph
sidebar_label: manage_reference_graph
description: "Maintain an automatic asset/code/external-config reference graph for the selected Unity instance and authenticated user."
---

# `manage_reference_graph`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `assets` &nbsp;·&nbsp; **Module:** `services.tools.manage_reference_graph`

## Description

Maintain an automatic asset/code/external-config reference graph for the selected Unity instance and authenticated user. No extra database installation. sync starts/resumes background work; poll status for progress. query uses node='asset:<guid>', direction='incoming' or 'outgoing', limit and returned cursor. diagnostics uses node, offset, limit and revision. explain {id} returns evidence for an edge. impact {node,direction?,limit?,cursor?,max_depth?} traverses reachable evidence with disk-backed cycle detection. path {node,target,direction?,limit?,cursor?} finds a shortest evidence path, returning its edges in target-to-start order; path_found=null means searching, false means unreachable. snapshot {name?,limit?,cursor?} captures a graph revision; continue until its state is ready. diff {before,after,limit?,cursor?} compares snapshot IDs, with historical evidence reauthorized on each page. Empty pages can have a next_cursor: always continue until null. release_query {id} releases a query_id; delete_snapshot {id} removes only a captured snapshot. Keep first-page options on continuation. For a verified first page, query/diagnostics/explain/impact/path/snapshot accept consistency='checked' to synchronize and rehash external inputs; ordinary cached pages expose provider verification timestamps. Code, Addressables and wrapper analysis is conservative: possible/unresolved evidence never proves an asset safe to delete. Sources require explicit read-only root and include patterns; remote paths belong to the server and require its operator's grant. Authored changes use id and expected_version from the corresponding list action. declare also needs owner and edge {target,kind}; set_source/set_rule/set_policy need config. watch needs enabled. Source config: {root,include,exclude?,mappings?,git_revision?,adapter?}; adapter defaults to {name:'json-csv',version:1}; mapping {field,scheme,prefix?,suffix?,values?}. Rule config: {call,argument,scheme,mode?,prefix?,suffix?}; {enabled:false} disables a rule. Policy config: {exclude:[relative patterns]}; there are no application-specific exclusions. sync {force:true} rehashes external inputs. rebuild retains authored declarations and grants. Normal domain reload retains scope; a new Editor process has a separate graph. Mutation batches: begin_mutation {paths} captures incident evidence before writes; add_mutation_targets {id,paths} appends target pages before dispatch. These actions also accept guids instead of paths for assets in the synchronized inventory. finish_mutation {id,outcome:'accepted'|'failed'|'unknown'} seals caller-reported outcomes. await_mutation {id,wait_seconds?} waits for imported evidence without a total job deadline; mutation_diff {id,limit?,cursor?} pages direct incident changes after synchronization. release_mutation {id} releases private receipt state. A synchronized receipt is not proof that an uncertain mutation succeeded; concurrent changes can contribute to its delta.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['status', 'sync', 'query', 'diagnostics', 'explain', 'impact', 'path', 'snapshot', 'diff', 'release_query', 'delete_snapshot', 'begin_mutation', 'add_mutation_targets', 'finish_mutation', 'await_mutation', 'mutation_diff', 'release_mutation', 'declarations', 'declare', 'remove_declaration', 'sources', 'set_source', 'rules', 'set_rule', 'policy', 'set_policy', 'rebuild', 'watch']` | yes |  |
| `options` | `dict[str, Any] \| None` | — | Only the selected action's documented fields; identity and database paths cannot be supplied. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

