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

## Experimental Skills provider evaluation

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

Retain the packaged adapter. The provider's live-directory behavior does not
preserve the shipped snapshot, and its experimental extension cannot currently
compose with this server's transforms. Existing authenticated HTTP, scoped-read,
legacy file-install fallback, manifest digest and byte-limit tests remain the
acceptance gate for any future migration. The comparison does not establish a
performance advantage or justify bypassing authorization.

This matches the documented [experimental provider limitations](https://gofastmcp.com/servers/providers/experimental-skills).
Revisit only when transforms, immutable provenance and the existing authorization
and fallback contracts can all be preserved.
