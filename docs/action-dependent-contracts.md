# Action-dependent tool contracts

Three focused capabilities now share reviewed declarative action rules between invocation validation, SDK-advertised conditional input schemas and generated help:

- `asset_edit`: every action requires a path; create also requires `asset_type`, modify requires `properties`, and move/rename require `destination`. Duplicate retains native destination generation when destination is omitted.
- `prefab_contents`: modify/preview require `prefab_path`; applying a preview requires only its observed `preview_id`. Preview creation excludes edits outside the existing safe root-transform/state scope. Explicit nulls retain their old omitted-option meaning.
- `asset_bundles`: the two bundle-specific queries require `bundle_name`; listing bundles does not.

These are additional action rules, not a replacement for the underlying type, path, revision, permission or native target checks. Legacy combined tools remain available and keep their existing validation. Inputs that were valid for focused tools remain valid, including JSON-string properties and duplicate without a destination. Invalid combinations return `invalid_action_parameters` before instance discovery, preflight or native dispatch.

`unity-mcp tool describe asset_edit` reads the actual advertised description and action help. `unity-mcp tool schema asset_edit` exposes the complete schema receipt, including `allOf` conditionals and generated `examples`. The same applies to `prefab_contents` and `asset_bundles`. Example paths and bundle names are illustrative; apply-preview examples must be replaced with a token returned by the preview operation. Reading these descriptions does not execute the examples.

Bundle inspection now advertises success/failure variants discriminated by the existing boolean `success`. Successful pages describe the native entries, count, paging and assignment source. Failure envelopes retain machine codes, error text and additional details. Existing field names, nulls and extension fields are preserved; validation never adds a count ceiling or truncates a result. Other capabilities retain their current result schemas until their variants have been reviewed.

The schemas are produced from the same rules used by the adapter, but tests also check representative calls through real modern and legacy SDK clients, pre-dispatch rejection, legacy valid forms and native-shaped result examples. Native target/revision validation remains authoritative. This change does not bypass project consent or automatically replay a mutation.
