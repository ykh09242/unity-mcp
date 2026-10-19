---
title: manage_build
sidebar_label: manage_build
description: "Manage Unity player builds — trigger builds, switch platforms, configure settings, manage build scenes and profiles, run batch builds across platforms."
---

# `manage_build`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_build`

## Description

Manage Unity player builds — trigger builds, switch platforms, configure settings, manage build scenes and profiles, run batch builds across platforms. Actions: plan, build_from_plan, build, status, platform, settings, scenes, profiles, batch, cancel, clean_output, report_history, report, compare_reports.  plan observes one ordinary build without applying settings; build_from_plan requires its expected_plan_sha256 and rejects drift. These actions do not support build profiles or batch targets. clean_output previews an explicit Builds/ descendant by default; dry_run=false deletes it. Refuses links, active builds, project source paths, and scans over 5000 entries. Read-only report_history/report/compare_reports retain bounded completed build summaries, paged steps/packed_assets and semantic comparisons across domain reload. Active observations after reload are interrupted, not resumed. Absolute paths/messages are omitted. Reports expire after 30 days or bounded eviction; missing/corrupt/partial evidence is explicit.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `str` | yes | Action: plan, build_from_plan, build, status, platform, settings, scenes, profiles, batch, cancel, clean_output, report_history, report, compare_reports |
| `target` | `str \| None` | — | Build target: windows64, osx, linux64, android, ios, webgl, uwp, tvos, visionos |
| `output_path` | `str \| None` | — | Output path for the build |
| `scenes` | `str \| None` | — | JSON array of scene paths, or comma-separated paths |
| `development` | `str \| None` | — | Development build (true/false) |
| `options` | `str \| None` | — | JSON array of BuildOptions: clean_build, auto_run, deep_profiling, compress_lz4, strict_mode, detailed_report |
| `subtarget` | `str \| None` | — | Build subtarget: player or server |
| `scripting_backend` | `str \| None` | — | Scripting backend: mono or il2cpp (persistent change) |
| `profile` | `str \| None` | — | Build Profile asset path (Unity 6+ only) |
| `property` | `str \| None` | — | Settings property: product_name, company_name, version, bundle_id, scripting_backend, defines, architecture |
| `value` | `str \| None` | — | Value to set for the property (omit to read) |
| `activate` | `str \| None` | — | Activate a build profile (true/false) |
| `targets` | `str \| None` | — | JSON array of targets for batch build |
| `profiles` | `str \| None` | — | JSON array of profile paths for batch build (Unity 6+) |
| `output_dir` | `str \| None` | — | Base output directory for batch builds |
| `job_id` | `str \| None` | — | Job ID for status/cancel |
| `expected_plan_sha256` | `str \| None` | — | build_from_plan only: 64-hex SHA256 from a fresh ordinary build plan |
| `dry_run` | `bool \| str \| None` | — | clean_output only: preview by default; explicitly false deletes the selected Builds/ descendant. No links allowed. |
| `report_id` | `str \| None` | — | Opaque retained report ID from report_history/status. |
| `baseline_report_id` | `str \| None` | — | compare_reports baseline report identity. |
| `candidate_report_id` | `str \| None` | — | compare_reports candidate report identity. |
| `report_section` | `str \| None` | — | report/compare_reports: summary (default), steps or packed_assets. |
| `page_size` | `int \| str \| None` | — | Report reads/comparisons: 1-100 rows; default 50. |
| `page_number` | `int \| str \| None` | — | Report reads/comparisons: 1-based page. |
| `report_revision` | `str \| None` | — | report: exact 64-hex observation revision for stable repeated pages. |
| `baseline_revision` | `str \| None` | — | compare_reports: optional observed baseline revision. |
| `candidate_revision` | `str \| None` | — | compare_reports: optional observed candidate revision. |
| `history_revision` | `str \| None` | — | report_history: optional 64-hex inventory revision for repeated pages. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

