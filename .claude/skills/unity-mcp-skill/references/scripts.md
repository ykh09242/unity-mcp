# Script Edits And Native File Handoff

Use this reference for C# source changes in the selected Unity project. The explicit `manage_script` consent applies to the wrappers, including reads/previews routed to that handler. Discover the exposed tools and `manage_script_capabilities` when supported before relying on edit operations.

## Choose An Edit Route

- For a new file, `create_script` takes a complete `Assets/.../*.cs` path and contents.
- For an existing file, `script_apply_edits` takes a script `name` without `.cs`, a containing `path`, and structured/text edits. Do not pass its locator as `uri`.
- For exact text spans, `apply_text_edits` takes a `uri`, edits and optional `precondition_sha256`. Explicit line/column coordinates are 1-based Unicode codepoints, with exclusive end positions; LSP ranges are 0-based UTF-16. Do not mix the conventions.
- `manage_script` is a compatibility router exposed for create/read/delete, not an `update` shortcut.
- Repository-only file edits do not require MCP; use the user's requested editor/file workflow without opening or launching Unity merely to satisfy this skill.

A structured proposal for an existing class/method:

```json
{"tool":"script_apply_edits","params":{"name":"PlayerController","path":"Assets/Scripts","edits":[{"op":"replace_method","className":"PlayerController","methodName":"HasTarget","replacement":"public bool HasTarget() { return currentTarget != null; }"}],"options":{"preview":true,"validate":"standard"}}}
```

Names, members and replacements are task-specific examples, not a request to add this behavior. Text/range/regex payloads are literal: supply all intended whitespace and line endings. Structured method/class edits can adapt formatting. Mixed structured/text preview is unsupported; split the proposal or use the direct route after assessing partial application risk.

## Validate A Proposal

`options.preview=true` prepares content only. It does not write files, apply edits or schedule refresh. Require a successful, complete, nontruncated proposal with both original/candidate contents, target identity and hashes. A no-op needs no application.

The preparation includes `project_root`, `absolute_path`, Assets-relative `path`, `original_sha256`, `candidate_sha256`, `candidate_bytes_sha256`, `encoding` and `bom`. Complete original plus candidate UTF-8 content is bounded to 1 MiB; a bounded/truncated diff is only a display aid, not the replacement source.

## Native Application Is Independently Checked

Preparation is not automatic permission or proof of local-host provenance. Use an available native file tool only when all of these are established:

1. The selected Unity Editor and agent share the same verified local project, and the exact target is inside a permitted workspace/`Assets` root without links or traversal. A returned remote absolute path does not establish this.
2. The original logical SHA still matches immediately before writing. On mismatch, reread and prepare again; do not overwrite intervening edits.
3. The native tool can preserve the candidate's exact UTF-8 bytes, BOM and newline/final-newline policy. Do not normalize raw string contents or fabricate file-edit events.
4. After writing, compare the raw file-byte SHA against `candidate_bytes_sha256` before Unity validation/refresh. If mismatched, stop, inspect the difference and correct only your own authorized edit; do not continue claiming the proposal was applied.

Hosted mode reports native application unavailable. If provenance, write permission or exact-byte preservation cannot be established, use the original direct Unity edit request without `options.preview`, within the same authorization. Never silently fall back to writing a similarly named local file. Older packages that reject preview need an upgrade or a supported direct edit, not a batch workaround.

## Direct Text Edits And Concurrency

Get a fresh SHA for the intended script:

```json
{"tool":"get_sha","params":{"uri":"Assets/Scripts/PlayerController.cs"}}
```

Supply the returned `data.sha256` as `precondition_sha256` for span edits. Recompute ranges from that exact content, reject overlaps, and preserve codepoint boundaries. A stale-file response requires a new read/proposal; repeatedly sending the same mutation cannot resolve it.

## Import, Compile And Verify

Successful direct script writes request Unity import/compilation. Wait for `data.compilation.is_compiling` and pending reload to clear, with readiness advice and a bounded timeout. No immediate extra force-refresh is needed just because a write succeeded.

Native file changes require import/refresh through the supported workflow if Unity has not already observed them:

```json
{"tool":"refresh_unity","params":{"mode":"if_dirty","scope":"scripts","compile":"request","wait_for_ready":true}}
```

Then validate and inspect the actual compiler diagnostics:

```json
{"tool":"validate_script","params":{"uri":"Assets/Scripts/PlayerController.cs","level":"standard","include_diagnostics":true}}
```

```json
{"tool":"read_console","params":{"action":"get","types":["error"],"count":10,"include_stacktrace":true}}
```

Validation is not a substitute for compiling the project with its real assemblies. Attach a new component only after successful compilation; a write acknowledgement alone does not prove the class is usable. After a disconnect, inspect the script/hash before repeating create/edit/delete.
