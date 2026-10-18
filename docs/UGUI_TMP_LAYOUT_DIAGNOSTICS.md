# Current TMP layout diagnostics

`manage_ugui(action="diagnose_text", target="Canvas/Label")` observes an existing generated TextMeshProUGUI layout. It does not calculate preferred sizes, rebuild text, load fonts, populate atlases, resize the Game View or render an image. The existing `diagnose` action keeps its conservative TMP estimate and preview behavior.

This action reports the native cached overflow flag/index, truncation, character/line counts, effective base font size and autosize setting, observed point-size range, local visible-character quad bounds, local rectangle/margins, and aggregate fallback/font counts. It collects no text, glyph values, links, font resource names or resource paths. Ordinary label metrics remain available; recognized InputField/TMP_InputField contents and owned renderers are redacted before cache reads, including inactive inputs and directly referenced external renderers. Incomplete privacy inspection refuses the request.

## Interpret the evidence

`source` is expressed by `evidence.observation="existing_generated_layout"`; `freshness="cached_not_marked_dirty"` records the limited readiness test. `status="observed"` means compatible existing data was read without marked dirty/in-progress state. It does not prove that a script never cleared a dirty flag, that this layout was generated in this frame, or that pixels are visible. Dirty, uninitialized, incompatible, malformed or over-budget caches are `unknown`, with null metrics and a fixed reason. Input-owned nodes are `redacted`, also with null metrics.

Native overflow is influenced by TMP's overflow mode. A false flag or a small ink rectangle does not prove that all source content fit: ellipsis and masking need their own interpretation; paged/linked modes or an attached linked text component return unknown. Bounds are local generated character-quad envelopes, including font padding; they are not exact colored-pixel bounds. Clipping, alpha, shader, camera, occlusion and interaction are not evaluated. Rich text, CJK, fallback and autosize reflect what TMP already generated; no replacement shaping or character-count heuristic is applied.

The action deliberately uses current generated data rather than a supposedly isolated clone. TMP preferred-size/rebuild APIs can change receiver buffers, populate dynamic fallback fonts or invoke shared callbacks; shallow font clones can retain original atlas/material references. This action does not advertise safe isolated recomputation or hypothetical resolution results. Update/render the UI through your ordinary application flow, then inspect it again when it is no longer marked dirty.

## Request and bounds

Accepted action-specific parameters are `target`, `include_inactive` and the following budgets. Other mutation/projection/resolution options are refused before lookup. Target uses the existing strict name/path/instance-ID resolution and ambiguity refusal. IDs and positional diagnostic paths describe current objects, not persistent handles.

| Budget | Default | Range |
| --- | --- | --- |
| `max_nodes` | 200 | 1..1000 inspected Transform entries |
| `max_depth` | 10 | 0..24 physical Transform depth |
| `max_evaluations` | 20000 | 1..200000 precharged traversal/cache observation units |
| `max_characters` | 65536 | 1..262144 retained metadata UTF-16 units |

Cache work is reserved before scanning: 14 units per character sample (a conservative bound for the allowed fields/corners), two per line sample (array element and line-height field), plus fixed readiness/metadata observations. Failed admission publishes unknown metrics and scans no cache entries; this counter does not include native inventory allocation or fixed privacy preflight work.

Native requests also accept the existing camel aliases for these budget names, with duplicate snake/camel settings refused. Native supplied null budgets are invalid; Python's omitted/new optional budgets use their defaults. The existing public `max_nodes` non-null integer schema is preserved. No budget accepts booleans or numeric strings.

A single cache is limited to4096 characters,512 lines and8 distinct font references; shared inspected cache limits are16384 characters and2048 lines. Counts and array lengths are checked before scanning; an incomplete aggregate is unknown. Work is reserved before scanning. Incremental traversal has a bounded stack and does not precollect the tree. Positional paths are emitted atomically; metadata names are bounded and Unicode-safe. Traversal reports visited/emitted nodes, work, retained metadata characters, scanned cache entries, truncation and fixed reasons. A partial response can contain useful observed/redacted nodes and unknown nodes; it never labels a truncated measurement complete.

Privacy preflight separately uses two fixed include-inactive native InputField inventories, a combined4096-entry processing cap,8192 owned-renderer IDs and128 ancestor steps. Native inventory allocation, target/type lookup and native execution time are outside the managed traversal observation budget; these limits are not a hard wall-clock or all-allocation bound. No user callback or input getter is used to establish ownership.

## Compatibility and refusal

TMP support is optional. The implementation recognizes exact TMP/uGUI assembly types and declared field shapes and reads only those allowlisted fields. It does not call `text`, `textInfo`, preferred-size, font lookup or rebuild getters/methods. Missing/incompatible types return `tmp_unavailable` or `unsupported_tmp_version`; project subclasses/lookalikes are not treated as exact engine layouts. In-progress registry state returns `layout_in_progress` without initializing its singleton or registering a callback.

The Python boundary validates the closed schema1 result, budget echoes and actual response limits before publication. Unrecognized/malformed peer data yields `unsupported_text_response`; native failures are reduced to fixed allowed codes or `text_unavailable`. Error text never echoes private native payloads. Tool/group enablement and the parent tool's mixed mutation annotations remain unchanged. SDK tests use real installed modern/legacy clients under fake dispatch; normalized SDK result artifacts are not raw transport captures.

Native execution and supported-version results are recorded in the task evidence report. Compile/API recognition is distinct from actual runtime, rendered appearance and original atlas/dirty/callback invariance. Do not infer unexecuted engine-version support from a matching API name.

## Fixture font input

The native QA fixture constructs its own text layouts; this setup dependency does not change `diagnose_text`, which only reads an existing generated layout. Supply a compatible TrueType font at face index 0 as a local file named `font-input/UI08.ttf` in the parent directory of the Unity test project. The fixture resolves this as `Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "font-input", "UI08.ttf"))`. For the repository test project, that location is `TestProjects/font-input/UI08.ttf`.

The supplied font must have proportional Latin metrics and every character in the fixture's Latin seed and Korean/Han seed (U+D55C, U+AD6D, U+C5B4, U+6F22 and U+5B57, plus space U+0020). The fixture loads the explicit file through TMP's file-path factory, seeds both owned atlases with the existing strict `TryAddCharacters` checks, and then makes them static. Missing files, an unloadable font face or missing glyphs fail setup; the fixture does not skip, choose an installed OS font or substitute Latin-only coverage. Keep the file unchanged and readable until fixture teardown and the final input audit complete.

The test profile must also provide the compatible `TextMeshPro/Mobile/Distance Field` shader and its includes. The fixture does not import TMP resources or install fonts. The task-local QA profile supplies pinned shader sources and a separately supplied font file; no font binary is included in this repository. A file-based input contract does not establish font availability, glyph coverage or supported-version behavior on another host.

The fixture is marked `Explicit` and is excluded from an ordinary unfiltered EditMode run. Select `MCPForUnityTests.Editor.Tools.UguiTmpLayoutDiagnosticsTests` by its exact name only in a fresh isolated test project after supplying the font and shader inputs described here and ensuring the required TMP static state is empty. Explicit selection does not prepare those inputs, bypass setup assertions or turn missing prerequisites into a skip. The reviewed task-local native export derivatives retain their separate finite selectors and input contracts.

The fixture assigns owned, non-null TextAssets with empty leading/following forbidden-character sets to its owned TMP settings. Its CJK cases exercise letters and fallback glyphs, not a locale-specific punctuation line-breaking policy. Cleanup detaches an owned camera from an owned render texture before destroying that texture, including when rendering fails before the pixel-read block.
