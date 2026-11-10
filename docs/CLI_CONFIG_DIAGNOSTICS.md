# Effective configuration diagnostics

`unity-mcp --format json doctor --config` reads the current CLI invocation's
resolved configuration offline. It does not require an instance, read a launch
token, contact Unity, or attest a running server. Its scope is
`local_cli_configuration`, with `running_server_config_observed: false`.

The separate MCP resource `mcpforunity://doctor/config` describes the process
serving that request, with scope `running_server_configuration`. Scoped remote
credentials need the `read` grant. The server returns its redacted settings even
when no Editor is connected. The existing connection doctor and its target/exit-code
contract remain unchanged. Neither view loads `.env` files or exports the environment.

Each known field includes `value`, `default`, `source`, `source_detail`,
`overrides` (source names only), and stable `diagnostics` codes. Provenance is
recorded at the existing resolver; an assignment outside those captured decisions
reports `runtime` with `origin_unavailable`. This avoids guessing an origin from
a later environment snapshot. Entries are bounded and secret values, token/header
fields, URL user information, paths, queries and fragments are redacted. An unset
secret remains `null`. Unknown environment variables are never included.

CLI precedence is Click command line, environment, default map, then built-in
default. Project context replaces only explicitly supplied fields that Click marked
as built-in defaults. Omitted context fields retain their canonical CLI defaults and
`default` provenance, including the 300-second timeout. Explicit flags
that equal a built-in value retain their actual source. Empty Click environment
values are ignored; invalid selected values still fail with exit code 2 and an
input-free error. An invalid value hidden by a higher-priority flag is not parsed.

Server precedence is field-specific and this diagnostic preserves it. For
example, nonempty `UNITY_MCP_API_KEY_CACHE_TTL` wins over its CLI option, while
`--http-port` wins over the environment and URL port. `UNITY_MCP_HTTP_URL` wins
over `--http-url`. Transport selection uses `--transport`, then
`UNITY_MCP_TRANSPORT`, then `stdio`. The selected value must be `stdio` or `http`;
an invalid environment value is ignored when an explicit valid flag wins. Timeout
factories ignore empty, invalid, nonpositive and nonfinite values; handshake
values above 300 seconds are clamped. Invalid server startup overrides that the
runtime ignores carry a diagnostic without reproducing the raw input.
