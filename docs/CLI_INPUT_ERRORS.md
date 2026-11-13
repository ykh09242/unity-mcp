# Local command input errors

The following command input failures exit with code 1 before dispatching a request:

| Command input | Stable JSON `code` |
| --- | --- |
| `raw TOOL PARAMS` with malformed inline JSON, nonfinite numbers or a non-object value | `invalid_json_params` |
| Shared JSON parsing/admission failures, including malformed JSON, wrong containers, unreadable files, invalid UTF-8, excessive nesting, nonfinite numbers, oversized external inputs and conflicting stdin sources | `invalid_json_input` |
| `batch inline` or `batch run` exceeding the command-count limit | `invalid_batch_size` |
| `code execute --file` and `shader create/update --file` source read or decoding failures; shader stdin failures or missing update contents | `invalid_source_input` |
| `code execute` without source code or `--file` | `missing_code` |
| `scene load VALUE --by-index` when `VALUE` cannot be parsed as an integer | `invalid_scene_index` |
| `texture modify` without pixel modifications or import flags | `missing_texture_modifications` |
| `texture set-import-settings` without import flags | `missing_texture_import_settings` |
| `texture create` or `texture sprite` combining `--image-path` with color, pattern, or (for `create`) palette | `conflicting_texture_inputs` |
| `texture create` or `texture sprite` with nonpositive generated width or height | `invalid_texture_dimensions` |
| `texture modify` or `texture set-import-settings` combining `--as-sprite` with other import flags | `conflicting_texture_import_settings` |

With `--format json` or `UNITY_MCP_FORMAT=json`, these failures emit exactly one
JSON document on stdout, with `success: false`, `code` and `error`; stderr remains
empty. The `error` diagnostic is capped at 1000 characters. Consumers should branch
on `code`, because diagnostic wording is for people and may change.

```json
{
  "success": false,
  "code": "missing_code",
  "error": "Provide code as an argument or use --file."
}
```

Text and table modes retain their diagnostic on stderr and leave stdout empty.
Successful command responses and `--help` keep their existing output behavior.
Click option/argument parsing errors and command-specific `BadParameter` diagnostics
retain their existing contracts, including shader `--file` directory rejection
(exit 2 with an option diagnostic in every output format). Shared JSON source failures
use this structured contract even when the consuming callback has no error decorator.

JSON `@file` and stdin sources retain their 4 MiB encoded-input limit and accept
UTF-8 with one optional leading BOM. Code/shader source reads also accept an
optional UTF-8 BOM and retain their existing size behavior. Typed input diagnostics never
include external source contents, file paths or raw operating-system exceptions.
Inline JSON syntax diagnostics report line and column without quoting input.
Human syntax diagnostics retain the shell-quoting example and tip.

Remote-host security warnings are deferred until a connection is attempted and
appear once per invocation before remote traffic. Local input rejections and
offline operations therefore leave JSON stderr empty even with a remote host configured.

CLI callbacks raise `CLIInputError`; the shared `handle_unity_errors` boundary owns
format selection and exit behavior. New command input cases should use that boundary
with an explicitly documented code rather than constructing JSON in each callback.
