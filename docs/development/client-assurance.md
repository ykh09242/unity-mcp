# MCP client assurance

The client assurance tests run the pinned official MCP Python client against the
production CLI, FastMCP registration, request middleware, authentication, and
transport lifecycles. `Server/tests/http/test_client_assurance_wire.py` keeps the
shared-server routing and retention scenarios together. The independent profile
matrix lives in `test_client_assurance_profiles.py`; authenticated HTTP and framed
stdio ownership scenarios live in `test_client_assurance_identity.py`. All three
belong to the existing isolated HTTP test group so legacy test-module stubs cannot
replace the real SDK.

The test-only CLI entry point adds 512 scalar tools through the production
registry and a resource exposing retained-state counters. It does not replace
middleware, routing, authorization, schema validation, or command dispatch.
Two controlled WebSocket peers emulate Unity responses; these tests do not start
Unity and do not establish native Editor or third-party host compatibility.

| Scenario | Actual boundary and assertions |
| --- | --- |
| Modern and explicit legacy HTTP | Official client to real loopback server, two independently identified plugin sockets, overlapping target calls, repeated client reconnects |
| Minimal, core, full profiles | Official client over modern HTTP, legacy HTTP, and legacy stdio; complete synthetic catalog visibility, description, validation, activation and reset where supported |
| Two principals | Production remote middleware and API-key service call a real HTTPS validation fixture; each requester sees its own plugin and cannot dispatch to the other principal's target |
| Cancellation | Cancel an official client's in-flight command, observe cancellation on the owning plugin socket, then reuse the same client/socket successfully |
| Stdio target selection | Official legacy MCP client to the production stdio CLI and two framed TCP peers; a per-call override preserves the pinned target, and both backend sockets close with the child |
| Retention and cleanup | No growth in serialized routing-state entries/bytes across repeated explicit calls; pending commands, retained/raw results and charged result bytes return to zero; disconnected plugin admissions and heartbeat tasks are released |
| Native probe | Standalone official client repeats read-only calls to two caller-supplied Editor targets and optionally verifies distinct scene identities |

The HTTPS validator generates an ephemeral self-signed certificate inside the
test's temporary directory, trusts it through the child process's `SSL_CERT_FILE`,
and removes the certificate and fixture key on shutdown. No user credentials,
certificate store, or real remote validator is needed. Tests use ephemeral ports,
per-test storage, owned reader threads and joined server processes.

The framed TCP fixtures use the supported, explicit
`UNITY_MCP_STDIO_ALLOW_LEGACY=1` opt-in inside their child process. They do not
write Windows Credential Manager or claim coverage of the default authenticated
Unity handshake. The default authentication tests and native execution evidence
remain separate from this routing/lifecycle check.

## Run the wire matrix

Use the existing Server environment, from `Server/`:

```powershell
$env:UNITY_MCP_RUN_HTTP_TESTS = '1'
$env:DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = 'src'
.venv/Scripts/python.exe -m pytest tests/http/test_client_assurance_wire.py `
  tests/http/test_client_assurance_profiles.py tests/http/test_client_assurance_identity.py -q -W error
```

The default routing run uses 64 iterations per target, with two targets and two
connections in each protocol era: 512 scene reads in total. Set
`UNITY_MCP_ASSURANCE_ROUNDS=512` for 4,096 scene reads, or a value up to 10,000
for a longer soak. This is a call-count soak, not proof of a particular number of
hours of idle operation. The retained counters measure owned serialized storage
and command reservations; they do not constitute an RSS or allocator benchmark.
The fixed catalog pressure is 512 additional tools in every profile/transport
case. Keep all nine profile/transport combinations when increasing routing load.

## Run against actual Editors

Start an authorized local HTTP server with `UNITY_MCP_TOOL_PROFILE=full`, then
connect two Editors. `manage_scene` is in the compatibility group, so the full
profile exposes the read-only `get_active` action in both protocol eras. Prepare
different active scene names in owned test projects for an independent routing
oracle. The probe never creates scenes or changes Editor state.

From the repository root, using the existing Server environment:

```powershell
$env:PYTHONPATH = 'Server/src'
Server/.venv/Scripts/python.exe tools/experiments/mcp_client_assurance.py `
  --endpoint http://127.0.0.1:8080/mcp `
  --instance 'First@first-hash' --instance 'Second@second-hash' `
  --expected-scene 'FirstScene' --expected-scene 'SecondScene' `
  --rounds 2 --output .work/client-assurance-native.json
```

The endpoint must be loopback HTTP without URL credentials, query or fragment.
The probe uses the existing local authentication reader and never includes its
credential in the receipt. It checks the actual negotiated protocol, fresh tool
catalog, both inventory IDs and the returned scene for each explicit target,
reconnecting between rounds. Omitting expected scenes reduces the evidence to
successful explicit calls and inventory membership; it cannot independently
distinguish otherwise identical targets.

A native receipt proves only the server/Editor versions and conditions actually
executed. Record their versions, source commit, platform, reload settings and
test project identities separately. Claude, Cursor, Codex host UI, macOS, Linux,
proxy deployments, reconnect after Editor crashes, and hours-long idle sessions
require their own execution evidence; this suite does not claim those outcomes.

## Actual Codex client regression

`tools/check_codex_interop.py` drives the installed **Codex CLI 0.166.0**
`app-server` through its experimental direct MCP RPCs. These are the actual Rust
`codex-rmcp-client` transport and decoder, not the Python client configured with a
Codex name. The fixture starts the production CLI, registry and all normal
middleware, adds the existing 512-tool catalog pressure, and registers small
test-only echo, error, slow and payload tools. `manage_tools.describe` and the
production retained-response reader also execute through that client.

```powershell
Server/.venv/Scripts/python.exe tools/check_codex_interop.py `
  --codex /path/to/codex --output .work/codex-interop
```

The machine-readable receipt records the exact binary version, legacy initialize
exchange, actual per-request protocol, catalog count, deadline error and verified
size cases. The Windows run on 2026-11-02 passed all four protocol/transport cases. The matrix uses both the legacy default and the installed experimental
`mcp_2026_07_28` feature over stdio and locally authenticated streamable HTTP.
Modern stdio also sets the supported `CODEX_MCP_PROTOCOL_VERSION=2026-07-28`
server opt-in. Every actual tool request must use the selected protocol:
**2025-06-18** for legacy and **2026-07-28** for modern. Silent fallback fails the
check. Modern request protocol evidence is recorded directly because that
lifecycle can use `server/discover` without an initialize exchange.

Each transport checks complete discovery, a production tool description, Unicode
echo, MCP `isError`, strict input rejection, resource reads, a configured tool
deadline followed by same-connection recovery, and a fresh ephemeral runtime after
configuration reload with another observed legacy handshake or modern catalog
request. Cancellation is
recorded only when the fixture catches and re-raises the cancellation exception,
and must be observed before the reload. Normal completion and teardown cannot
create a false cancellation pass. This is **deadline-triggered cancellation**; the pinned direct
MCP RPC API has no per-call cancel operation. Explicit caller cancellation remains
covered by the independent Python wire tests.

Size cases verify an inline payload within 512 bytes of the production result
budget, an 8 MiB ASCII payload and a 9 MiB UTF-8 Unicode payload. Oversized
responses must return a retained handle; the actual Codex client reads every page,
verifies the complete byte count and SHA256, reconstructs the original text, then
releases the handle. The check fails on truncation, a lost handle, a decoder error
or a failed connection after the deadline.

Every run allocates a fresh temporary `CODEX_HOME` and empty working directory,
uses file-only credential stores, strips inherited authentication variables,
disables plugins, analytics and feedback, and selects a loopback-only offline
provider. It never starts a model turn, loads user authentication/configuration,
or creates a sidebar chat. All test processes and temporary configuration belong
to the run. This proves the app-server MCP client surface, not Codex desktop UI
adoption or a native Unity Editor scenario.

The dedicated [Codex interoperability workflow](../../.github/workflows/codex-interop.yml)
uses the official release archive with its published SHA256 digest pinned in the
workflow. The binary version check is mandatory; upgrading the pin requires
rerunning all four protocol/transport cases and reviewing the observed protocol. Protocol
contracts come from the installed experimental schema and the
[official app-server documentation](https://learn.chatgpt.com/docs/app-server).
The client implementation is documented by the
[pinned Rust source](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/rmcp-client/src/rmcp_client.rs),
and the binary comes from the
[official 0.166.0 release](https://github.com/openai/codex/releases/tag/rust-v0.166.0).

The pinned client's [protocol selection policy](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/rmcp-client/src/protocol_mode.rs)
requires both the feature and server opt-in for modern stdio. `codex features
list`, run inside the isolated temporary home, independently exposes the feature
as under development in 0.166.0. This opt-in coverage does not imply that modern
MCP is enabled in a user's default Codex configuration.
