# First-attempt edit failures and their regression checks

The preceding matched run finished 23 of 24 Unity edits. Five tool calls and two
resource reads failed. Five edits recovered during the same turn. The one
unfinished edit received successful replies throughout. The published ledger
was checked again against the original requests, host events and saved assets.
[Preceding study](compact_edit_confirmation.md) · [Preceding case ledger](compact_edit_confirmation_results.json) · [Follow-up on the same originals](heartbeat_edit_confirmation.md)

| Prior hosted measurement | Default (core) | Full |
| --- | ---: | ---: |
| Edits completed | 12/12 | 11/12 |
| Rejected tool calls | 3/78 | 2/85 |
| Failed resource reads | 2/44 | 0/39 |

## Causes and corrections

- The full catalog saved an extra period in a tooltip. The agent selected the
  sentence-ending period as part of the value, then confirmed its own edit on
  readback. The server's initialize instructions and both skill copies now
  direct clients to compare the preview and saved field with the intended span
  in the original request. An agent can use delimiters, neighboring text and
  sentence structure or ask about a genuinely ambiguous value. The server
  accepts intentional punctuation and does not infer the intended substring.
- In one core rename, three consecutive preparations failed schema validation.
  The first call supplied a raw string for `exact_text`. The next call used the
  wrong item properties and the third supplied a scalar path. The first tool
  list and the selected operation now share an example pairing the original
  query with an array of assertions. The selected tool's draft stays independently
  runnable. Both SDK protocol modes exercised a top-level field and a nested
  field.
- Two full-catalog calls lost their explicitly selected Editor before an edit
  was dispatched. Both target discovery and the complete inventory could return
  empty in the same observation window. The contemporaneous records identify
  an empty inventory but do not establish why the Editor did not respond.
  After a previously observed v2 Editor vanishes from both lookups, stdio waits
  up to one 50 ms polling interval within the request deadline and probes that
  exact target once. The returning listener must agree with its status file.
  Loopback TCP tests cover recovery, a permanently missing target, a conflicting
  listener, cancellation and expiry. The extra probe sends no edit command.
- Two core reads used a host-local file path and an unregistered skill URI.
  The catalog now lists skill files actually advertised on that MCP connection
  and supplies an exact read call. A selected Editor is preserved in the read
  metadata. Tests follow the returned call through the SDK and an authenticated
  HTTP server, check its bytes and exercise an inventory with hidden and
  read-restricted files.

## Isolated Unity readback after the fix

An SDK client drove Unity 6000.0.69f1 using package `05f77b719`, server
`077edd3e7` and bundled skill `5875fd66f`. It opened the advertised
`ui_source` operation from each live catalog and supplied the intended source
span. One control used the original tooltip request and expected
`Opens the handover log`. The other explicitly requested `Ready.`. Each began
with a fresh fixture. For each value, the SDK tried a mismatching draft,
prepared a preview, saved with the preview's source hash and read the file again.

| SDK control on a real Editor | Default (core) | Full |
| --- | ---: | ---: |
| Correct saved edits, checked in Unity and on disk | 2/2 | 2/2 |
| Unexpected tool rejections | 0/18 calls | 0/18 calls |
| Deliberate mismatch checks refused during preparation | 2 | 2 |
| Published skill reads matching the package bytes | 1/1 | 1/1 |

Preview and rejected preparations preserved the original file and its metadata.
Each successful write changed only the intended tooltip. The independent Unity
observation, disk bytes and public readback agreed on all four values. The owned
Editor exited and all 130 shared tool preferences matched their initial hashes.
The local SDK receipt SHA-256 is
`faa81d3d6488937c4ef688d502eb9ee5c490647c7d7d1a5d8cb130cc57bb137b`.

These controls used a caller-supplied interpretation. The table at the top of
this report describes the preceding hosted cohort. The follow-up used fresh
original-only turns on the published heartbeat changes.

## Follow-up on the two lost Editor selections

The archived prefab and scene turns both lost their explicit Editor **before
dispatch**. Each selected the same Editor successfully later in its turn. The
recorded responses and Unity logs establish the empty inventories but do not
identify the failing filesystem or socket operation. The study fixture replaced
its heartbeat file with `File.Replace`. The ordinary Editor instead rewrote its
published JSON in place every half second.

Two controlled regressions reproduced these failure paths independently. In
Unity 6000.0.69f1, an open reader of the old publisher saw the next heartbeat
overwrite its snapshot. On Windows, the old server reader could also report
`Available: none` while a matching v2 listener remained reachable and its
complete status file was held for replacement. A single incomplete inventory
could then outlive the original contention in the server cache.

The publisher now closes a complete sibling snapshot before installing it.
The server permits file replacement while reading and obtains each file's
timestamp and contents from the same handle. Discovery rechecks a shrinking
inventory before treating the remaining Editor as the only choice. A previously
observed explicit target must match its identity and live listener on a fresh
observation. A conflicting identity cannot reuse a previous cache entry.
These checks take place before sending an Editor command.

When a named local Editor remains unavailable, both supported MCP protocol
modes return a target-unavailable error pointing to `mcpforunity://instances`.
The response omits private identifiers. When an argument is invalid, an
ordinary admitted tool can return a bounded example from its published schema.
Inputs that require further authority checks retain their original validation
error until admission. A rejected call still counts as a failure in the ledger.

The full-catalog tooltip failure had a different cause. Both catalogs took the
direct read, preview, write and reread route. The full preview already contained
the extra period. Those calls supplied the proposed value but no independently
selected span of the original request. Both candidate values occur verbatim in
the original. A caller can compare the preview with its original or supply the
intended literal to preparation. The server preserves the exact text it receives.

## Filesystem and authentication boundaries

The status directory belongs to the local Editor user. The status document
associates a project path and ID with a listener generation. A matching v2
greeting supplies a candidate for discovery. Before sending a command, the
connection authenticates that generation using the OS-protected credential.
The authenticated transcript includes the generation, challenge, nonce and
session. Keeping the registry directory under the user's control protects the
project-to-generation association. This also applies to a custom
`UNITY_MCP_STATUS_DIR`. [Authentication implementation](../../MCPForUnity/Editor/Services/Transport/Transports/StdioBridgeAuthentication.cs)

On Windows a reader can request read access and share the file for replacement.
The stage needs its intended discretionary access policy at creation, including
while it is empty. The existing public name and backup can have different
outcomes on a failed replacement. On Linux an open reader retains its inode
across a successful same-filesystem rename, and default ACLs participate in
file creation. On macOS the mode bits and the extended ACL are independent
parts of the file's access policy. These distinctions inform the native
filesystem checks. [Windows sharing and creation](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) · [Windows replacement outcomes](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew#return-value) · [Linux rename](https://man7.org/linux/man-pages/man2/rename.2.html) · [Linux ACL inheritance](https://manpages.ubuntu.com/manpages/noble/man5/acl.5.html#object_creation_and_default_acls) · [Apple file security metadata](https://github.com/apple-oss-distributions/copyfile/blob/9f91eb6ced021952278816cdc76ad68da8631ccb/copyfile.3#L75-L105)

The published `64f66bbe7` package passed the filesystem checks on Windows
Server 2025 x64, Ubuntu 22.04 x64 and macOS 26.7.2 arm64. Each runner compiled
the same production source and checked the old reader, new contents, access
rules and recovery. On Windows the developer-only fixture also exercised a
managed failure with a diagnostic and a normal failing exit code. A separate
licensed Windows Unity 6000.0.69f1 run passed 28 lifecycle tests and read the
running Editor through the public MCP resource and an authenticated ping.
All 475 MCP Editor preference values matched after shutdown.
[Three-platform run](https://github.com/ykh09242/unity-mcp/actions/runs/45920784246)

The same product source also passed the three-platform check after the Linux
workflow moved to Ubuntu 26.04. Mono and the ACL utilities were installed on
the disposable Linux CI runner. [Updated runner matrix](https://github.com/ykh09242/unity-mcp/actions/runs/45922125245)

The Unity 7 alpha compile check exposed Windows dependencies on managed ACL
APIs outside its .NET Standard 2.1 surface. The publisher now creates the stage
with the source DACL through Win32 and checks its owner and group before
writing. The licensed Windows Editor suite passed all 28 lifecycle cases.
It checked the ordered allow and deny entries and protection flags across
stage creation, publication and backup recovery. [Windows file access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights)

The Windows Application log traced the reported `Publisher.exe` dialog to
an unhandled exception in the developer fixture. The harness now sets the
child's noninteractive error mode and the executable returns diagnostics on
failure. The local .NET Framework and .NET 10 runs each returned 1 on the
deliberate refusal and 0 on success. [Windows error modes](https://learn.microsoft.com/en-us/windows/win32/debug/error-mode)

Unity's CoreCLR transition guide also withdraws support for direct calls to
Mono native libraries. The Unix CoreCLR publisher uses the runtime's native
file API to create the stage. It reads permissions from the same open file
descriptor that established write access. The Mono branch retains its own
runtime ABI. [Unity migration guide](https://discussions.unity.com/t/path-to-coreclr-2026-upgrade-guide/1714279) · [.NET 10 native file ABI](https://github.com/dotnet/runtime/blob/v10.0.0/src/native/libs/System.Native/pal_io.h)

If a custom macOS directory supplies inherited rules that differ from the
old file's ACL, publication stops before writing data and retains the previous
snapshot. The native checks covered explicit and inherited ACLs separately.
Windows retains an unresolved predecessor after an interrupted replacement
when it cannot safely restore the original name. Its ordinary cleanup retry
handles a transient backup lock during the same Editor domain. These checks
covered local filesystems and the access policies above.

## Matched Windows SDK controls

A replacement-access handle remained open on the complete status file while
a client disabled a prefab child or prepared a scene save. On `05f77b719`
all four calls (two per catalog) lost the selected Editor before dispatch.
On `64f66bbe7` all four succeeded. The prefab child ended inactive, the scene
saved the requested sorting order of `-12`, and the protected neighbors stayed
unchanged. Both catalogs also saved the original tooltip and the explicitly
punctuated `Ready.` control exactly. The independent observer and saved-file
checks agreed, and all 130 shared tool preferences matched after shutdown.

Each catalog made 25 SDK tool calls, with two deliberate mismatch rejections
and zero unexpected failures, and read one advertised skill successfully.
The SDK controls had supplied target values. The separately measured hosted
agents received only the original user requests.
The before and after SDK receipt SHA-256 values are
`f79d307c84a65f36b5a7fd804e94be3fd17cbe9e26e184884f4c09ef42caa141` and
`561d73f0867b87520dbe89c69239ebfe96aefdff1f54c28139840b85342f093d`.

## First catalog on an Editor that appears after initialization

The first hosted core client observed an inventory of 90 while its commissioned
core catalog contained 61 tools. An independent control started a stdio server
process and an MCP SDK client, waited for initialization, then published an
authenticated loopback test peer with the recorded Editor policy. Its first
public request listed tools. The previous server returned the same 90 names
as the affected actor and sent zero Editor commands. The revised server read
`get_tool_states` once and returned the exact 61 commissioned names. Both
versions returned a complete page. A second list reproduced the first with
zero additional Editor commands. This control exercised real stdio and TCP
with a test peer. The matched hosted run used a Unity Editor.

## Verification of the shipping package

The isolated Windows Editor ran package `f9e2ddc4b`, server `bf92d33d3` and
skill `dad211b92`. The SDK's first tool list contained 61 core or 91 full
tools. In each profile, the SDK received the revised startup instructions and
bundled skill, resolved the Editor's project root through the public resource
and verified the saved asset at its project-relative path. The client's
separate working directory contained no such asset. The two text edits,
prefab edit, saved scene value, audio import setting and active scene lookup
all produced their expected observations.

Each profile made 33 tool calls. Two deliberate literal mismatches and one
invented operation were rejected. Following the returned operation-list call
led to a published action and a verified audio edit. No unexpected tool
rejections occurred. The final Editor shutdown preserved all 130 shared tool
preferences. The SDK receipt SHA-256 is
`a914bf4051e13da444365c2255be850a3b1dbf7c81729a9df4b62881afdbd195`.
The 24 hosted turns measured the earlier `64f66bbe7` package. These shipping
checks used SDK-supplied arguments.
