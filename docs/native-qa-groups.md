# Reusable native QA groups

For actual multi-Editor preference ownership, owned forced termination and gated
Unity 6.5/6.6 cases, see [native preference assurance](native-preference-assurance.md).

`tools/unity-native-groups.json` declares exact required test methods, disruptive
cohorts and explicit platform exclusions. The transport QA runner derives its
Unity filter from the complete selected inventory. It validates the NUnit result
with `check_unity_test_results.py`; partial matches, missing/renamed methods,
zero cases, skipped required cases and unsuccessful process exits cannot pass.
Parameterized methods require every recorded case to pass. The method filter is
not narrowed to a particular argument case.

## Prepare, launch and refresh

Use a task-owned directory beneath this checkout's `reports/`. Existing external
projects and running Editors are not adopted. Package preparation uses the
existing resolver and accepts its existing registry cache option.
The disposable project's `ProjectVersion.txt` records the selected Editor version;
the source test project is unchanged. This prevents an older source version marker
from triggering a project upgrade that rewrites the pinned package manifest.

```powershell
python tools/unity_editor_transport_qa.py prepare --output reports/native-qa --ref HEAD --version 6000.0.69f1 --editor 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe'
python tools/unity_editor_transport_qa.py launch --output reports/native-qa --group transport-readiness --group transport-lifetime --label transport-001 --isolation-verified
python tools/unity_editor_transport_qa.py refresh --output reports/native-qa --ref HEAD
python tools/unity_editor_transport_qa.py launch --output reports/native-qa --group transport-lifetime --label transport-002 --isolation-verified
```

Supply the same explicit Editor/version options when overriding the defaults.
`--working-tree` is an explicit alternative to a committed snapshot. The existing
`--timeout` is configurable for long native workloads; no shorter operation limit
is introduced. `--isolation-verified` still requires the caller to establish the
package lifecycle isolation used by that project. These commands do not enable
licensed Unity CI or grant access to another project.

Refresh verifies the previous source, prepared files, Editor and package inputs
before staging the next source snapshot. It preflights all ownership conflicts,
then replaces only changed managed files and removes only deleted managed source
files. Unchanged files keep their timestamps. Library, package caches and unowned
project files remain in place. Changes to the Editor, resolver, dependency set or
authored project settings require new preparation. This prevents treating an
incompatible cache as verified. No automatic cache deletion occurs.

An exclusive operation marker prevents this runner from refreshing during its own
launch; an existing Unity project lock also refuses launch/refresh. Interrupted
refresh marks the preparation unlaunchable. Preserve the evidence and prepare a
new owned output after such an interruption; do not erase a marker while its
owner is running.

## Evidence and boundaries

Launch records include the source receipt, input file hashes, exact Editor binary
hash/configured version, selection-manifest hash, selected methods, platform
exclusions and whether Library existed. Prepared local package inputs are hashed
with the other snapshot inputs. Both pre-launch and post-launch input checks must
succeed. Every label requires fresh log, XML and receipt paths.

Unity-generated ProjectSettings are recorded before and after execution separately
from the authored settings snapshot; import/version normalization does not look
like a source edit. Initially absent `.meta` and package lock files may be created
by Unity. Previously captured files remain guarded. Generated Editor UserSettings, caches and outputs
are excluded from source identity; new C# or package inputs are not.

Disruptive groups cannot be combined with ordinary groups. A platform exclusion
refuses that selection with its declared reason rather than reporting success.
The initial reload cohort is explicitly limited to the available Windows evidence.
A manifest entry alone does not claim native execution on other platforms.

The existing owned warm-project controller may also consume `load_selection` and
`check_results` to validate its exact method inventory, preserving its stronger
lifecycle controls and existing input receipts. It must retain those receipts and
record its overlays; it must not relabel an arbitrary old result as a new launch.

Groups with `requires_graphics: true` (including `validation-capabilities`) launch
with a graphics device: the runner omits `-nographics` for that entire selection.
Other groups retain the headless default. The selected requirement is recorded
in launch evidence. Run graphics groups on a host with a usable graphics device;
a skipped panel case still fails the required-method gate. External owned warm
controllers consuming `load_selection` must honor its `requiresGraphics` field.
