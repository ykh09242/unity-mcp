# Explicit local MCP targets

Local MCP servers preserve automatic selection by default. To require a deliberate
project choice before an operation can edit, run project code, or start tests, set
`UNITY_MCP_REQUIRE_EXPLICIT_TARGET=true` in the **server process environment** and
restart that server. `1` also enables the setting; `false` and `0` disable it.
Values are case-insensitive and may have surrounding whitespace. Any other value,
including an empty string, rejects configuration instead of silently disabling the
policy. This setting has no separate CLI flag.

For example, in PowerShell, set the variable before invoking your existing server
launch command:

```powershell
$env:UNITY_MCP_REQUIRE_EXPLICIT_TARGET = "true"
```

Read `mcpforunity://instances`, identify the intended project, and either:

- Pass `unity_instance` with the intended `Name@hash` on each tool call. This choice
  applies only to that call.
- On a stateful MCP session, call `set_active_instance` with that project, then use
  the same session for subsequent operations. Sessionless clients must pass
  `unity_instance` on every call that requires an explicit target.

Existing selector formats remain available, including unique hash prefixes and
local stdio port selectors. Prefer the complete `Name@hash` from discovery for a
durable project choice. A missing or disconnected explicit target is an error;
the server does not replace it with another project's sole running instance.
Select the intended project again after checking discovery when necessary.

The gate uses the server's operation permission classifier after public schema
defaults are applied. Any operation requiring `edit`, `run`, or `test` needs an
explicit target, including custom tools and batches containing such operations.
Tool annotations such as `readOnlyHint` do not lower this requirement. Unknown or
unsupported batch semantics fail closed. A batch containing only classified read
operations may still use automatic selection.

Built-in read classification and control exemptions require the actual trusted
built-in callback, including its server-owned registration wrappers. A custom
callback replacing a built-in name remains custom, even if it copies the original
callback's annotations or wrapper metadata. Registered custom extensions can run
after an explicit target is supplied.

Read-only discovery, listings, resources, and classified read tools can continue
to select the sole project automatically. That request-local selection does not
authorize a later mutation. Old automatically persisted selection state also does
not qualify as an explicit choice. Server/session controls and retained-result
receipt reads remain usable without a project selection.

Admission errors include stable codes:

- `explicit_unity_target_required`: choose a target for the operation.
- `explicit_unity_target_unavailable`: the chosen target could not be resolved.
- `explicit_unity_target_classification_failed`: the complete operation could not
  be classified; the handler was not entered.

The running server's `mcpforunity://doctor/config` report includes
`require_explicit_target`, its built-in default, and configuration origin. An
offline CLI configuration report describes the CLI process, not this server
policy. This option applies to local MCP tool admission. Remote-hosted MCP retains
its existing authenticated selection and permission behavior. Local REST/CLI
requests retain their existing target controls, including `--expect-project`;
this option does not extend that REST contract.

## Local REST and CLI composite compatibility

Local REST `/api/command` (including custom tool execution) and `/api/custom-tools`
require both parts of `Name@hash` to identify the same Editor, consistently with MCP.
The final `@` separates the name and hash, so `Game@2026@aaaa1111` selects the
project named `Game@2026` with hash `aaaa1111`. Both parts must be nonempty.
Conflicting names and hashes are refused before dispatch, with HTTP 409 when the
hash exists; an absent hash returns HTTP 404. Refusals report
`data.dispatch=not_dispatched` and the available composite selectors.

Previously, local REST gave the hash precedence even when its accompanying name
belonged to a different project. Clients relying on that behavior must copy a
current complete selector from discovery or send the bare exact hash. Bare hashes
still take precedence over colliding project names. Unique names, duplicate-name
refusal, and implicit selection of a sole Editor remain available. Existing
`--expect-project` checks continue to apply independently. The guarded
`/api/context-command` route also requires a composite's name to match its captured
Editor; it retains its structured `project_context_mismatch` refusal with HTTP 200
and no dispatch. CLI `status --ready` applies the same composite rule.
