# Reviewed SDK runtime

Immutable server releases install the exact FastMCP, MCP and MCP types versions
reviewed by the optional execution-receipt and Tasks adapters. Application source
pins remain immutable. `uvx --from` resolves a tool environment independently of
the checkout's lock, so these three requirements are exact in the published
package metadata as well as `Server/uv.lock`.

`Server/src/core/sdk_compatibility.json` is generated from that reviewed lock by
`python tools/generate_sdk_compatibility.py --write`. Check mode is the default;
`--check` fails if a range, locked version or generated manifest disagrees. All optional
adapter gates read this packaged manifest. Updating the generated file alone is
not evidence that a new SDK has been reviewed. Other transitive dependencies keep
their declared constraints; this is a reproducible adapter SDK set, not a claim
that every unlocked transitive dependency is frozen.

## Diagnostics

Read `mcpforunity://doctor/runtime` for the resolved and reviewed SDK versions and
optional feature state. It works without selecting or contacting Unity, retains
ordinary resource authorization, and does not activate features or open stores.
The original `mcpforunity://doctor` connection receipt and CLI shape stay unchanged.
Startup logs include the same bounded dependency and configuration information.

`disabled` means not configured; `sdk_unsupported` means an override does not match
the reviewed set; `unavailable` means a requested adapter could not be installed
or its local receipt store cannot accept operations;
`inactive` means installed/configured but outside its active store/lifespan;
`active` means that local runtime is open. None of these states proves authority,
Unity readiness, job existence or result availability for a particular operation.
Version matches do not replace the Tasks adapter's private-hook shape checks.
No diagnostic contains an installation path, credential, request or user payload.

## Deliberate SDK upgrades

In a development checkout, change the exact requirements, run `uv lock --project
Server`, review the resolved set, regenerate the compatibility manifest, and run
the real modern/legacy protocol, receipt/replay, cancellation and Tasks contracts.
Only then advance immutable launch pins through the ordinary review/CI process.
There is no automatic dependency update or compatibility-gate bypass at startup.

For an explicit compatibility experiment, uv's override requirements can replace
exact dependency requirements in a separate tool environment. Keep the original
manifest: unsupported overrides remain visible and optional adapter gates refuse
them. Never copy experiment metadata into the reviewed manifest merely to enable
an adapter. See uv's [tool environments](https://docs.astral.sh/uv/concepts/tools/)
and [dependency overrides](https://docs.astral.sh/uv/concepts/resolution/#dependency-overrides).

Cold and offline bootstrap CI verifies installed registration, the resolved SDK
set, and both optional adapters with private temporary stores, without opening a
network listener or starting Unity. Candidate checkout installs also run that
probe before the locked test suite, covering the published dependency metadata.

## Tool registration on FastMCP 4.2.3

The supported SDK floor and current SDK are the same exact package set:
FastMCP 4.2.3, MCP 2.3.0 and MCP Types 2.3.0. The Python Tests server matrix
installs the lock on Python 3.11 through 3.15. Its registration regression uses
both `legacy` and `2026-07-28` clients on each interpreter. It registers the
original handlers on one server first, then discovers and calls the derived
tools on a second server. The request must supply the required public arguments
and the SDK must inject the context for the second server. The fixture checks
the GameObject command and the Game View, uGUI and Shader Graph reads at the
Unity transport boundary. [Registration regression](../Server/tests/test_functional_tool_registration.py) · [CI matrix](../.github/workflows/python-tests.yml)

FastMCP adds a dependency default to the handler's injected context and can
move it behind required positional-or-keyword arguments. Focused tools that
call an admitted parent pass `ctx` by name so the parent's captured signature
can bind the action and context independently. [FastMCP context injection](https://github.com/PrefectHQ/fastmcp/blob/v4.2.3/fastmcp_slim/fastmcp/server/dependencies.py)

FastMCP 4.2.3 corrects schema conversion for non-object defaults and positional
arrays. Its OpenAPI route validation and opt-in background span parenting apply
to the OpenAPI and background worker integrations. This server registers Python
handlers and runs its own Tasks adapter. The separate platformdirs 4.14.8 lock
update strengthens runtime-directory lookup and creation on Unix and macOS and
validates Windows UNC overrides. [FastMCP 4.2.3 release](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.2.3) · [platformdirs 4.14.8 release](https://github.com/tox-dev/platformdirs/releases/tag/4.14.8)


## Skills and scene-input installation

The runtime doctor also reports `skills` and `scene_load_inputs`. These entries
include `configured`, `installed`, `state`, a bounded `reason` code and
`next_action`. Configuration is captured when the server is created; changing an
environment variable later does not imply that middleware was installed.

Skills are active only when the immutable packaged extension is the extension
actually registered on this server. A missing bundle/extension reports
`repair_packaged_skill_bundle`; reinstall the reviewed server package and restart.
Do not download or execute a replacement skill in response to diagnostic output.

Scene inputs require HTTP and `UNITY_MCP_SCENE_LOAD_INPUTS=1` at startup. A stdio
configuration reports `use_http_transport`; a missing adapter/middleware reports
`check_scene_input_installation`. Check startup warnings and the reviewed SDK,
then restart. A closed input ledger reports `restart_server`. Disabled scene
inputs leave ordinary explicit scene arguments available. No limits or operation
deadlines are reduced by these diagnostics.

## Skills on FastMCP 4.2.2

FastMCP now discovers skills through tool-only server transforms. The packaged
adapter resolves each concrete manifest URI under the caller's current transforms
and uses the reviewed SDK's Skills read policy before returning the manifest.
A hidden, replaced or unauthorized reference omits the entry from `skills/list`
and makes `skills/get` return the same error as an unknown URI. These checks avoid
reading file bodies. Hosted discovery applies the same URI permission as a direct
resource read. An explicitly scoped `read` credential can read the runtime doctor,
while the packaged `skill://` URI is outside its permitted routes. An unavailable
SDK read-policy hook disables the optional adapter.

The packaged adapter validates the sealed metadata before registration and
serves the recorded source revision, original UTF-8 file bytes and private zero-TTL
results. The native provider's `SkillManifest.to_mcp_skill` constructs its entry
from frontmatter and file descriptors, omitting the package's source metadata.
[FastMCP 4.2.2 change](https://github.com/PrefectHQ/fastmcp/pull/6548) · [Native manifest projection](https://github.com/PrefectHQ/fastmcp/blob/v4.2.2/fastmcp_slim/fastmcp/skills/skill.py)

## Skills on FastMCP 4.2.1

The packaged adapter uses FastMCP's `ListSkillsParams`, `GetSkillParams`,
`ListSkillsResult` and `GetSkillResult`. It validates the bundled manifest
before registering resources and gives each request a detached result. The
server's tool visibility transforms prevented the 4.2.1 upstream `SkillsExtension`
from discovering skill sources. The packaged adapter preserves the approved
revision, file digests and private cache hints with those transforms enabled.
[FastMCP 4.2 Skills release](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.2.0) · [Upstream extension implementation](https://github.com/PrefectHQ/fastmcp/blob/v4.2.1/fastmcp_slim/fastmcp/skills/extension.py)

## Earlier experimental Skills provider evaluation

The FastMCP 4.1.3 provider was compared with the packaged adapter using an owned
copy of the canonical skill bundle and real modern HTTP list/get/read requests.
The fixture was changed after discovery to verify snapshot behavior. No user
skill directories, credentials or Unity projects were read.

| Contract | Packaged adapter | Experimental directory provider |
| --- | --- | --- |
| List/get with the server's tool visibility transforms | Succeeds | Both return an internal error |
| List/get without transforms | Succeeds | Succeeds |
| Private cache, zero TTL | Preserved | Preserved without transforms |
| Repository and immutable revision metadata | Preserved | Absent by default |
| Read remains equal after the fixture file changes | Yes | No |

The 4.1.3 evaluation retained the packaged adapter because of live-directory reads
and the experimental extension's handling of transforms. Authenticated HTTP, scoped-read,
legacy file-install fallback, manifest digest and byte-limit tests remain the
acceptance gate for any future migration. The comparison does not establish a
performance advantage or justify bypassing authorization.

This matches the documented [experimental provider limitations](https://gofastmcp.com/servers/providers/experimental-skills).
Revisit only when transforms, immutable provenance and the existing authorization
and fallback contracts can all be preserved.


## Build and dependency acquisition

The server uses Starlette/MCP routes and has no FastAPI import or plugin contract.
FastAPI is no longer a direct runtime dependency. External extensions that import
FastAPI must declare their own dependency instead of relying on the server to
install an unrelated framework.

`Server/pyproject.toml` pins the isolated build backend and wheel's packaging
dependency exactly. Unlike a uv-only workspace constraint, `build-system.requires`
also accompanies immutable archive installs and pip builds. Runtime requirements
and the reviewed SDK lock remain separate. Update these three build pins as one
reviewed unit, then exercise an isolated build and cached/offline registration.

Python CI keeps the existing uv caches and includes both the project metadata and
lock in cache keys. uv's native HTTP retry count is five; dependency connection
and read allowances are 30 and 60 seconds. These apply only to uv acquisition,
not Unity commands, MCP requests, builds or assertions. Successful cached requests
incur no delay. Persistent download errors, resolver/build failures and test
failures still fail their step; no workflow or test-suite retry loop is added.
Cold bootstrap intentionally uses its own empty cache before its offline pass.

See uv's [HTTP settings](https://docs.astral.sh/uv/reference/environment/#uv_http_retries)
and [build dependency behavior](https://docs.astral.sh/uv/concepts/projects/dependencies/#build-dependencies).
