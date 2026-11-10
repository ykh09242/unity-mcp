# Local command input errors

The following command input failures exit with code 1 before dispatching a request:

| Command input | Stable JSON `code` |
| --- | --- |
| `raw TOOL PARAMS` with malformed inline JSON, nonfinite numbers or a non-object value | `invalid_json_params` |
| `code execute` without source code or `--file` | `missing_code` |
| `scene load VALUE --by-index` when `VALUE` cannot be parsed as an integer | `invalid_scene_index` |

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
Click option/argument parsing errors and other commands' input diagnostics retain
their existing contracts. Raw `@file` and stdin JSON source parser failures also
retain their existing behavior.

CLI callbacks raise `CLIInputError`; the shared `handle_unity_errors` boundary owns
format selection and exit behavior. New command input cases should use that boundary
with an explicitly documented code rather than constructing JSON in each callback.
