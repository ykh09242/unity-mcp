# Blind Unity tool selection

This benchmark asks a model to perform an observation without naming the tool,
action, resource URI, or expected result. Each task is available in English,
Korean, Japanese, Spanish, Arabic, and Turkish. The matrix runs each selected task and
language in either the main role or a fresh native subagent role. Both roles
receive identical observation requests. The parent delivers the request and
collects the result for subagent cells.

## Run against an open Editor

Use the checkout's locked Server Python environment and an installed, already
authenticated Codex CLI. Select an already-connected Unity MCP HTTP loopback
server, an exact `Name@hash` Editor instance, and the matching project directory.
For either graph task, supply an existing project-relative `.shadergraph` path.
The benchmark uses the local Unity MCP authentication path and never writes the
credential to its outputs. It checks the selected Editor's reported project root
before forwarding an observation. It leaves the Editor's scenes, playback,
settings and files untouched. Place trial outputs in a private ignored directory
outside the project.

Use `--host-mode configured --installed --installed-endpoint http://127.0.0.1:8080/mcp
--instance Name@hash` for the installed Unity service. The runner probes the
ordinary user configuration in a model-free thread. A second ephemeral thread
exposes the same verified installation through the target-pinned observation
proxy. Before its model turn both receipts must agree on the installation,
project and advertised Unity catalog. The report records any difference between
the two threads' competing capabilities. The proxy enforces
the Unity read boundary. Competing MCP services retain their configured grants;
use competing services trusted for observation in the game project.
For configured baseline/candidate comparisons, provide trusted read-only
relays. The runner disables only the installed Unity entry for the process and
exposes the selected relay under the same trial name on both arms. The default
`isolated` mode remains available for controlled comparisons.

Before the model acts, the configured runner records the same thread's paged
advertised inventory in private `host.json`. An installed doctor receipt must
identify the requested Editor. A trusted archive relay can serve
`mcpforunity://trial/source` with `kind: archived_server`, a verified tar commit
in `source_commit`, and its `archive_sha256`. Configured pairs require observed
server commits and matching competitor catalogs. The scored output distinguishes
MCP wire responses, installed host responses, and exact saved-file reads.

```sh
python tools/experiments/blind_tool_selection.py run \
  --project /work/Game --endpoint http://127.0.0.1:8080/mcp \
  --instance Game@0123456789abcdef --graph-asset Assets/Example.shadergraph \
  --output .work/blind-trial-01 --model gpt-6.2-sol --effort medium \
  --task game-view --task graph --task graph-saved \
  --language en --language ko --role main --role subagent
```

Repeat `--task`, `--language` or `--role` to select the matrix axes. The other task
IDs include `graph-open`, `editor` and `context`. The other locales are `ja`, `es` and
`ar`. `raycast` requires a trusted relay that guarantees read-only raycasts.
The generic endpoint adapter admits fixed observations and disables refresh for
its GameObject lookup. Use `--repeat` for repeated samples. Run the same matrix against baseline
and candidate servers with the same model, reasoning effort, permission policy,
project, and Editor starting state. Each cell uses a separate ephemeral CLI
invocation and the runner records its model, effort and approval policy. An
operator-provisioned variant can use `--relay /path/to/read_only_relay.py` in place
of `--endpoint` and `--instance`. That Python stdio relay must already enforce
the specified project, target its active Editor, proxy its authentic capability
catalog and safely perform only allowed observations. The wire recorder still
applies its own route guard with either backend.

The default policy is `never` for approvals and `read-only` for the Codex
sandbox. Use `--approval auto-review` only if that approval policy is in scope
for the evaluation. Record permission blocks with the trial. Keep the policy the
same when comparing model outcomes. Neither live models nor connected Editors
are used by the CI test.

## Compare two conditions

Two additional tasks exercise action choice on a fixed scene and a saved interface.
`camera-depth` asks for the Unity camera with the greatest depth and its numeric value.
Its trusted relay can admit the scene camera inventory resource and the exact
`list_cameras` operation. Use a fixture with a unique maximum.
`saved-ui-controls` requires `--ui-asset Assets/Example/Menu.uxml`. It counts authored
Unity UI Toolkit Button and TextField elements in the named saved document. A verified
literal read of the complete file and a successful exact-path UI source read can both
establish the counts. The trusted relay must bind the project and reject other paths,
arguments, operations, imports and edits. Probe the observation routes in each arm
before delivery. Include unavailable advertised routes and unattributed answers in the
report. Direct-file and editor-channel claims are distinguishable in V3.
Current wire records include a monotonic observation timestamp (`observed_ns`) for
the request and reply. Prepared pairs record `prompt_ns` on the same machine. Older
records can establish call order even where wall-time-to-evidence is unavailable.

Pin each relay to its respective server revision and use the same Editor and
policy for both arms. A trusted read-only Python state probe receives the
absolute project directory as its only argument. Before each case it should
verify the project and print one JSON object containing the stable facts
relevant to the selected tasks. For example, record the instance, scene, play
and compile state, rendered dimensions and preset, and the saved asset digest
when testing a graph. Leave volatile counters and timestamps out of this object.
If the probe cannot verify the state, it should exit nonzero. A missing or failed
probe preserves both observations and excludes the pair from controlled totals.

```sh
python tools/experiments/blind_tool_selection.py matched \
  --project /work/Game --instance Game@0123456789abcdef \
  --baseline-relay /work/bench/baseline.py --candidate-relay /work/bench/candidate.py \
  --state-probe /work/bench/observe_state.py --graph-asset Assets/Example.shadergraph \
  --output .work/matched-01 --model gpt-6.2-sol --effort medium --approval never \
  --task game-view --task graph --task graph-open --language ko --language ja \
  --role main --role subagent --delegation legacy --repeat 2
python tools/experiments/blind_tool_selection.py compare .work/matched-01/baseline .work/matched-01/candidate
```

Each adjacent pair gets the same prompt, model request, effort request, project,
policy and delegation mode. AB/BA order alternates across repetitions within
each task, language and role. Every case records its prompt digest, measured
starting state, relay script digest or endpoint address, and position in the
schedule. Record the actual upstream revisions alongside private runs when a
relay imports external code or two endpoints proxy different revisions. Identical
backend identifiers cannot establish two treatments. The state probe also works
with `run`. Use `--baseline-endpoint` and `--candidate-endpoint` for HTTP arms
under the same instance and endpoint restrictions as `run`.

`compare` joins the union of planned and recorded cells by task, language,
role and repetition. It preserves both arm results and the reasons for excluding
a pair when the conditions, completion, role or execution order cannot be
verified or differ. Its aggregate counts share one eligible-pair denominator.
Older independent matrices can therefore be rescored without promoting them to
counterbalanced evidence. It also reports descriptive counts for every observed
arm by requested role, including the native actor attribution breakdown. The
report includes counts in each observed order.
An incomplete comparison exits nonzero after printing the report.

## Score the traces

```sh
python tools/experiments/blind_tool_selection.py score .work/blind-trial-01
python -m pytest tools/tests/test_blind_tool_selection.py tools/tests/test_blind_tool_comparison.py -q
```

The report records the first Unity business attempt and the first suitable
attempt in request order. Skill reads, resource listings and catalog operations
are recorded as discovery. Approval blocks can occur before the wire recorder
sees a request. The host events retain those attempts separately from server
errors. The repeat count groups equivalent tool and resource operations and
keeps Shader Graph pages distinct. It includes intentional re-observation. The
retry count reports the subset that follows a failure or an approval block on
the same operation and page.

`repeats_after_complete_evidence` counts repeated operation/page attempts after
a complete result was already available. A trace cannot establish the reason
for repeating an observation. `raycast_before_size` counts pointer queries
issued before a successful rendered-size response. Both timing metrics compare
the requesting and responding events on the same wire connection. Overlapping
requests cannot use a later response. A missing common timeline yields null,
and the summary includes the number of measurable cases. Host-visible shell reads count in
business-call order. Child approval counts cover the events visible to the parent.

The MCP recorder validates and summarizes task-specific domain data. An RPC
response, a tool error, or discovery success alone earns no evidence. The
Game View task requires positive rendered dimensions. The raycast task requires
those dimensions and a safe, ordered native observation at their center for a
complete result. A diagnostic such as `missing_event_system` can provide partial
evidence. The graph task requires graph totals and two distinct observed node
types. The Editor task requires fresh play and compile state and an observed
active scene. The context task requires the project root, Unity version and
render pipeline from the Editor. The saved graph task uses a separate signal for
raw serialized graph output from a direct shell read of the exact path. The
report flags other possible saved-file observations for manual review. A graph
summary returned through the Editor also reads serialized file contents. The
`graph` task requests the Editor's response. Neither route observes unsaved
window contents. For `graph-open`, a successful `inspect_open_shader_graph`
or `manage_shader.inspect_open_graph` read must return the requested path,
`status="open_model"`, nonnegative counts and the window's dirty flag.
The counts describe the open model even when the flag is false. Node types
and saved-file references are scored separately. To include an open-graph
pair in positive-availability rates, the independent state probe must record
`observation_fixtures["graph-open"]` with the exact path in `arguments.path`
and the corresponding successful native response in `reply` before both turns.
`wire_evidence` and the historical `live_evidence` field report transport
and role-attributed evidence. The graph data state identifies the content source.
`task_evidence` also credits a confirmed saved-file read for the saved graph task.

Matched runs request a final JSON object after the localized user task. Single
runs default to prose. Use `--answer-format json|prose` to select either format.
The current `--answer-protocol v2` identifies the response channel with
`channel` (`editor` or `file`) and the content origin with `source`
(`editor_live` or `asset_file`). A serialized graph returned by the Editor
has channel `editor` and source `asset_file`. Direct disk reads use the same
source with channel `file`. Use `--answer-protocol v1` to reproduce the older
single-source-field prompt verbatim. Cases without protocol metadata retain v1.
Successful-looking legacy graph values require review when that field leaves
their provenance ambiguous. A checked review remains bound to the recorded
protocol, answer and evidence.
For a child trial the delivered answer is the parent's final relay. Native actor
attribution is independently measured from host events and wire receipts.
`final_answer` scores the delivered answer independently from wire evidence. It
checks named dimensions, graph totals, two distinct types, graph provenance, the
ordered pointer result and exact center, and the requested scene or project
settings. A new size receipt replaces the prior dimensions and preset. A later
unknown result at the center supersedes an older observed hit. Reporting unknowns can receive `limited` when the evidence establishes
a limit. Legacy records can recover omitted facts from a uniquely matching host
MCP result. Unverified values or free prose in any language remain
`needs_review` until adjudicated. A missing answer remains `missing`.

To adjudicate a private case, read its answer and the relevant raw receipts.
Store `answer-review.json` in its case directory with `review_digest` from that
case's score, a `status` of `correct`, `incorrect`, `limited` or `needs_review`,
and a specific evidence-based `reason`. Check, for example, that the answer
assigns each total to the right quantity and distinguishes Editor transport from
serialized bytes. The digest binds the verdict to the task, answer and retained
observations. Re-scoring ignores a stale review. Keep adjudication reasons, wire
summaries and comparison reports private because they can contain project data.

Main trials request direct execution. A completed native agent wait in a main
trial makes its role mixed, including when the CLI omits the spawn receipt and
only a separate MCP connection recorded observations.

Confirmed subagent attribution requires a host-observed native spawn with a
receiver ID, business requests linked to that same child, and no competing
parent business calls. A spawn and unbound requests support inference.
Conflicting request actor metadata makes the role mixed. Some CLI versions omit
nested shell activity or v2 spawn notifications from JSONL. Try `--delegation
legacy` with the installed CLI if v2 omits them. If the host reports a native
wait and a separate MCP process recorded the only business reads, the report
labels that connection-based attribution as inferred. When Codex includes
host-owned thread metadata, the recorder stores a hash of the thread UUID to
correlate actors. `wire_evidence` reports what Unity returned in either case.
Only confirmed cases contribute to role-attributed success. A
case that ran without finding evidence remains a measured case. The report
identifies process failures and missing cells, and either yields a nonzero exit
status. Avoid publishing `events.jsonl`, `stderr.txt`, and `answer.txt` because
they can contain project data. The JSON score contains summarized observations.

Configured trials can also establish a direct child from a parent-linked host
notification and its completed tool items. The scorer rejects unrelated child
items and unmatched wire observations. The first search receipt keeps up to three
ranked results or groups and the declared distinct-group count. An unavailable
rank, approval decision, native result or requested actor is reported separately.
Only a complete inventory for the relevant actor establishes absence.
The legacy `discovery_calls` and `discovery_successes` counters cover the
whole trial. `selection_diagnosis.actor_discovery` counts the selected
actor's receipts and remains null when its capture is unknown.

## Per-claim answers

Use `--answer-format json --answer-protocol v3` to score the value, data origin
and delivery channel of each claim. V1 and V2 retain their original prompts
and scoring. V3 reports valid JSON shape, measured fact accuracy and measured
provenance accuracy independently. Unknown, unverified and malformed claims
stay visible outside their respective accuracy denominators. A saved number
reported in an open-window answer belongs in the optional `saved_reference`
object. A limited receipt can support an honest unknown about the open window.
A number without an actor-owned receipt receives no accuracy credit.

## Balanced consumer study

Use `matched --study consumer-discovery` with a project, exact instance,
graph asset, model, effort, trusted baseline and candidate relays, state probe
and a new private output directory. It defaults to a configured host and V3.
Each repetition schedules 36 pairs. Game View, the saved graph and the open
graph window are requested in English, Korean, Japanese, Spanish, Arabic and
held-out Turkish, in both roles. Keep prompts and model settings identical
between arms. The order alternates within each role across locales.

The runner writes the manifest, both matrices and the complete schedule first.
Separate game-view pilots establish business-call ownership without grading
which tool the actor selected. Each arm needs its own actor census, permissions,
namespace, binary identity, product receipt and Editor state. A scoped host
lookup requires schema support for a direct parent filter and validates each
returned parent link. If a role lacks evidence, its planned cells remain in
the comparison with the pilot reasons. A transient control difference permits
one pilot retry. The model does not see the pilot or a suggested route.

The coverage report retains planned, observed and eligible denominators by
task, language and role. The open-window task evaluates the answer's limits.
Wire and first-business success use the eligible observable tasks. The direct
score requires confirmed absence of a catalog search. Top-three retrieval
uses only captured natural searches. Zero searches yields a denominator of
zero. Scripted discovery probes are reported separately.

## Contrast observation with action

Run the disconnected fixture to compare observing compilation with starting
it, reading a saved graph with observing unsaved window contents, and inspecting
a uGUI hit with dispatching a click. Each request has its own disposable
directory and SQLite journal. The fixture advertises the repository's tool
schemas and intercepts all Editor calls. Its simulated work never reaches Unity.

```sh
python tools/experiments/blind_tool_contrasts.py run --output .work/contrasts-01 \
  --model gpt-6.2-sol --effort medium --language en
```

The report compares the answer against observed results and the journal's exact
effect sequence. A duplicate click fails the dispatch case. A saved graph count
cannot establish the contents of the unsaved window. The game-project benchmark
above supplies separate live observations.

## Preparing a controlled pair

Use `matched --prepare-pairs --state-probe <script>` with two separately identified backends. Both configured, read-only ephemeral hosts open the same game project, report their inventories, source commits, model, effort, permissions and Editor state, and wait before either receives the prompt. The runner delivers the two prompts in the scheduled AB or BA order and samples the Editor again before delivery and after each turn. A failed preflight keeps its reason and starts no model turn. One retry is available for an observed transient mismatch before either prompt.

For a fixed competitor cohort, pass `--competitor NAME` for every required competing MCP namespace. Use `--disable-competitor NAME` to disable a specific competing namespace in the trial processes. The observed remaining set must match the declaration in both arms. Omitting these options uses the configured competitors and records that cohort separately. Each case retains its shared readiness digest, both readiness timestamps and its own prompt dispatch timestamp. Private `preparation/<cell>/pair.json` records all attempts, including withheld prompts.

`matched --study observation-choices` schedules current compilation, active scene, an existing compilation result and a pointer ordering task in six languages and both roles. The pilot uses the same prepared-host protocol. In delegated cells both parent hosts are prepared before the task. The host captures each newly spawned child's own controls during the turn. A paired child result requires matching child grants and catalogs for competitors, an observable business call and that child's completed answer. The host records connected and lazily advertised MCP namespaces separately. Use `--disable-plugins` with a declared roster to control plugin-provided competitors for that cohort.

Supply `--existing-compile-id` only for a known earlier request. For a pointer trial supply a previously identified `--pointer-target` and `--pointer-position X Y`. Without the corresponding fixture the runner keeps the planned cell and withholds the prompt. For these two conditional tasks the state probe can return `observation_fixtures` keyed by task, each entry containing the scoped read’s `arguments` and decoded `reply`. Both pre-prompt receipts must establish the result to enter the positive selection denominator. Other cases retain their availability and evidence grades. Only searches that returned a captured ranking enter the top-three denominator.

Report the actor's first business call and its first verified observation separately. For example, a shell command can precede the MCP call that supplies the answer.

### Read-only action selection in a game project

The November 2026 comparison used one running game project and separate read-only
hosts for each revision. The prompts named neither tools nor resources. Main and
direct child actors used the same model and reasoning setting in both arms.

| Comparison | Eligible pairs | Current-state or saved-source answer supported by a captured observation |
| --- | --- | --- |
| Initial action descriptions, `41b81950a` to `386329d5e`, Korean/English/Japanese, camera and saved UI | 8 of 12 | Baseline 1/8, candidate 0/8. All 16 eligible answers matched the independent values. |
| Read-only camera resource guidance, `6ef373ec1` to `1748931bb`, Korean/Spanish, camera only | 4 of 4 | Baseline 3/4, candidate 4/4. All eight answers matched the independent values. |

Four pairs in the initial comparison lacked unambiguous attribution. Re-scoring
the frozen receipts preserved those exclusions. In the follow-up each baseline
actor first tried the mixed camera tool for the task, and each candidate actor
first used the read-only camera resource. In the three pairs where both arms
obtained the live observation, the candidate used two, six and six fewer
preceding calls. All eight actors used the shell first when setup calls are
included. No candidate actor used ranked search in this follow-up, so these
sessions cannot isolate the effect of the ranking change. Raw game evidence
remains local.

### Isolating discovery surfaces

Use the prepared-pair procedure above for each factor. For the description and
order controls, choose two source revisions with identical trees for the served
package. Pin each archive and verify both source receipts. In one read-only
relay's FastMCP server factory, call
`install_factor(server, "camera-description")` or
`install_factor(server, "route-order")` from `blind_tool_factors` immediately
before returning the server. Keep the other relay's responses as shipped.

- The description condition removes one resource-reference sentence from the
  two advertised camera tool descriptions. Action help, resources and search
  results keep their original text.
- The order condition reverses the returned `routes` and `route_groups` on each
  search page. It preserves group membership, members' descriptions, legacy
  tool-level `results` and all totals. A single row in each array has no order
  contrast. Before task delivery compare the tool schemas and a multi-result
  query's text and structured content in both arms.
- For protocol links, use the actual before/after server revisions with the same
  relay and native game. Independently verify equality of the original text,
  structured results and order. Inspect the additional `resource_link` blocks,
  including the absence of a link for an unbound resource template.

Ask the natural camera question without naming an API. Pair a saved graph
question with a current-window question for the same asset. Check the saved
graph's exact bytes and the Editor's actual window inventory separately. Record
a missing window as a live limitation and a verified saved read as saved evidence.
`calls_before_window_absence` counts earlier business calls when the actor
obtained a complete window list with no Shader Graph window.
`calls_before_declared_graph_limit` counts earlier host business calls when the
search reports that current graph contents cannot be observed. Report search use
over eligible actors, top-three placement over actors with a returned ranking,
and protocol links over searches with observable content blocks. Keep each
factor's results and the pre-treatment pilot separate.

Four prepared camera pairs exercised the two counterfactuals in one running game.
The ordinary relay served `826e55bb7`. Each experimental relay changed one response
surface of the byte-identical package tree from `63d84de05`.
Each factor included one main actor and one direct child, with one
English and one Korean request. All four pairs passed the role, host, permission,
competitor and stable-state checks. All eight actors opened with the shell and
eventually obtained the correct answer from the live camera inventory.

| Factor | Business calls before a verified read, main / child | Actors using ranked search, main and child combined |
| --- | --- | --- |
| Shipped descriptions / camera sentence omitted | 2 / 4 with the sentence, 3 / 8 without it | 1/2 with the sentence, 2/2 without it |
| Shipped order / reversed order within the returned page | 4 / 6 in each condition | 0/2 shipped, 1/2 reversed |

Every naturally returned first ranking contained a suitable group in its top
three. The sole actor who searched in the order comparison got one group
containing three equivalent bindings. Its group position was fixed. The probe
also showed that a typed search returning one group can reorder the separate
flat list. The actor receipts captured group ranks. New receipts include
`flat_top3` for the flat list as well. These samples cannot estimate the effect
of ranking on natural adoption.

### Resource links in actual model sessions

Six additional pairs compared `826e55bb7` with `0d2d51ce9`. One separate
Korean main-actor baseline turn finished before the new links shipped.
The pairs kept the same running Editor, saved asset bytes, model
(`gpt-6.2-sol`, medium), read-only sandbox, never approvals, shell and Context7.
All six pairs passed the controls. Languages below identify prompt locales.

| Request and actor | Baseline / link candidate outcome |
| --- | --- |
| Live camera, Korean main | Both first inspected Editor state, then attempted the mixed camera tool. A successful camera resource read followed 6 / 8 prior business calls. Both answers were grounded. |
| Live camera, English direct child | The first Unity business operation was the camera resource / mixed camera tool. Each reached the camera resource after 2 / 8 prior business calls, without search. Both answers were grounded. |
| Current graph, English main | Both received an explicit current-buffer limitation after 4 / 3 prior business calls and withheld current values. Only the candidate's optional saved reference had a verified file receipt. |
| Current graph, Korean direct child | Both received the same limitation after 4 / 5 prior business calls. The baseline submitted saved values in the current fields. The candidate withheld them and supplied an unverified saved reference. |
| Saved graph, Korean main and English direct child | All four used the shell only for business observations and returned the correct saved counts and two node types. Their path-mentioning commands lacked an attributable literal read with returned graph facts. |

All 12 actors began with a shell command when setup is included. The two
sources had 5/6 and 6/6 answers agreeing with the independent oracle,
respectively, and 2/6 and 3/6 fully grounded finals, including any optional
claims. For the saved task, the sanitized shell records preserve no evidence
to resolve the unverified claims further. A separate probe verified the
installed saved-graph tool. The graph resource template was unavailable on the
installed connection. The evaluator's live window inventory contained no
Shader Graph window. None of the four current-graph actors obtained that live
window receipt. Their search established a limitation of the available API.

Three of six actors in each arm searched. Two of those three received an
empty result explaining the current-buffer limitation. The remaining actor
received a suitable camera group in the top three in both arms (1/1 each).
First search responses carried a protocol link in 0/3 baseline and 1/3 candidate
turns. The recipient subsequently read the camera resource. The receipt does
not distinguish clicking the link from choosing a URI also present in the text.
Independent camera, active-scene, saved-graph and current-graph probes preserved
identical text bytes and structured JSON. Only the camera and active-scene
queries gained a concrete protocol link. This sample showed no reduction in
calls to the first verified camera read.

**Final integration check.** With locked FastMCP 4.2.2, `e86a39862` returned
a duplicate stdio response-identity error from both search tools. The corrected
server at `28b6676f6` passed all four selector probes over raw stdio. The
game relay also reproduced all four original link-only responses, including
the exact text bytes, structured JSON and expected links. The relay served
`28b6676f6` to the actors. The existing Unity 6000.0.69f1 Editor supplied the
observations through its installed server and native package at `8fdc71f69`.

Two fresh actors on the corrected server produced answers grounded in the same
running Editor. Both used `gpt-6.2-sol` at medium effort and started in the
shell. The Korean main actor first read project information and verified the
complete camera list and count on business call seven. The English direct child
first tried the mixed scene tool, encountered an approval block, and verified
the current scene name and path from Editor state on business call five.
The independent before/after observations matched in both turns. Neither actor
searched (0/2), leaving zero eligible search responses for conditional ranking
or link exposure. These two turns stay outside the ten matched pairs.

### Source-matched classification and camera trials

The follow-up used an isolated Unity 6000.0.69f1 Editor with the unchanged scene
and GUID metadata from the running game's active scene. The fixture contained
one allowlisted game scene and its recorded Editor version. Its native camera
inventory agreed with the running game. Missing game dependencies and the
batch-mode window environment limit conclusions about the full game or its
interactive desktop.
The original Editor's package and server both reported `8fdc71f69`. Its
independent camera observation is a source and scene check outside the trial
denominators.

The classification comparison used `11ee734d70ba0eaba563b0b95a0f44f4034ea481`
and `90c3eca13718476b86456b72bac4e6c9fa932109`. The camera comparison used
that classification revision and `c445b98189c2971c788de07e122f9c86a78e338d`.
Each server ran from a separate Git archive. Unity reported the loaded package
path for the command registry assembly. All 1,554 archived native package
files matched that installed package in every condition. The same running
Editor handled the narrowly admitted RPCs through its command registry.
The fixture supplied a positive declaration-list read for the Frame Debugger
and a unique maximum camera depth. Its native permission setting denied
the separate, exact-path declaration inspection. The read-only relay preserved
that denial and ruled out execution and menu validation callbacks.

The `menu-declaration` and `camera-depth` entries in the
[prompt manifest](blind_tool_tasks.json) contain the six translated questions.
Camera actors also received the corresponding manifest `limits` paragraph.
Both tasks ended with this instruction, with the field list selected by task.

> Use only this project and its current editor. Preserve files and editor state.
> Do not inspect packages, implementation source, configuration outside the
> project, or evaluation materials. Return a JSON object with {fields} and a
> brief evidence explanation. Use null if you cannot verify a value.

The field lists were `menu_path` and `camera, depth`. Each main performed its
own request. Each delegating parent passed the request verbatim to one fresh
direct child with the same workspace, connection, model and effort. Operator
probes, revision names and expected answers stayed outside the prompts.
The runner launched individual configured sessions in alternating arm order
by locale and role. It capped concurrent sessions at three. Each arm used
Codex CLI 0.171.0, `gpt-6.2-sol`, medium reasoning, the read-only sandbox,
never approvals and shell. The six competing namespaces were `codegraph`,
`codex-security`, `codex_apps`, `context7`, `cua_repl` and `node_repl`.
It captured the parent's catalog before delivery and the child's own catalog
and permissions in delegated cases. Read-only preflight warmed the connection and withheld
the task if the Unity descriptors or served revision could not be verified.

For a repeat on another eligible scene, supply a strictly read-only relay
and native state probe to the public runner. The recorded sessions selected
the question, footer and JSON fields above before delivering the prompt.
The public runner can issue the same observation tasks with its standard
answer and evidence instructions.

```sh
python tools/experiments/blind_tool_selection.py run --host-mode configured \
  --project /local/game-fixture --instance Game@hash --relay /local/arm.py \
  --state-probe /local/state.py --output /private/unique-cell \
  --task menu-declaration --language en --role main --model gpt-6.2-sol \
  --effort medium --approval never --delegation v2 --live-child --timeout 360
```

Repeat for `camera-depth`, the six manifest locales and both roles. Compare
matched actors only after verifying their revisions, independent before/after
state, actual model/effort, competitor catalogs, grants, completed answers and
role attribution. The first host call includes setup and discovery. The first
business attempt excludes instruction reads, working-directory checks and
catalog discovery. Shell calls whose purpose was unknown count as business
attempts. An appropriate attempt selects a task-specific observation
route, including a declaration or inventory lookup that the host then blocks.
A verified read requires returned task facts tied to the actor. An oracle match
without an attributable read counts only as a correct answer. Raw game data,
paths and actor transcripts stay local.

**Primary results.** All 48 planned actors completed. The role and host controls admitted 23 of 24 pairs. An optional window read in the Turkish menu main baseline produced different wire and host statuses, which prevented unambiguous role attribution. Its complete pair is excluded throughout the primary results. All 46 actors in the admitted pairs first used the shell when setup was included.

Each arrow compares counts over eligible actors in the earlier and later arm of that task.

| Task | Role | First business attempt appropriate | First business attempt verified | Answer matched oracle | Answer grounded | Answer withheld |
| --- | --- | --- | --- | --- | --- | --- |
| menu | main | 2/5 → 5/5 | 0/5 → 0/5 | 1/5 → 0/5 | 0/5 → 0/5 | 4/5 → 5/5 |
| menu | direct child | 4/6 → 1/6 | 0/6 → 0/6 | 0/6 → 1/6 | 0/6 → 1/6 | 6/6 → 5/6 |
| camera | main | 3/6 → 4/6 | 1/6 → 3/6 | 5/6 → 6/6 | 4/6 → 6/6 | 1/6 → 0/6 |
| camera | direct child | 1/6 → 0/6 | 0/6 → 0/6 | 5/6 → 6/6 | 5/6 → 6/6 | 1/6 → 0/6 |

For each locale and role, `yes` means the first business attempt selected a task-appropriate read. The number gives the business-call position of the first verified observation. A dash means the actor did not obtain an attributable observation of the requested facts.

| Locale | Role | Menu baseline | Menu classified | Camera classified | Camera fixed reader |
| --- | --- | --- | --- | --- | --- |
| en | main | yes / — | yes / — | yes / — | yes / 1 |
| en | direct child | yes / — | no / — | no / 5 | no / 2 |
| ko | main | no / — | yes / — | yes / 2 | yes / 4 |
| ko | direct child | no / — | no / 5 | no / 3 | no / 5 |
| ja | main | yes / — | yes / — | no / — | no / 6 |
| ja | direct child | no / — | no / — | no / 3 | no / 2 |
| es | main | no / — | yes / — | yes / 1 | yes / 1 |
| es | direct child | yes / — | no / — | yes / 3 | no / 2 |
| ar | main | no / — | yes / — | no / 3 | no / 2 |
| ar | direct child | yes / — | yes / — | no / — | no / 3 |
| tr | main | excluded | excluded | no / 4 | yes / 1 |
| tr | direct child | yes / — | no / — | no / 2 | no / 3 |

The menu actors first tried the mixed tool's declaration inspection when they reached a task-specific route (11/11 per arm). All 22 faced the same native setting. Each arm withheld ten answers. Only one classified actor produced a grounded answer through the declaration list. A baseline actor gave the correct path without an attributable read.

In the camera trials, the earlier arm first selected the inventory resource in five cases and a mixed camera tool in seven. All 12 later actors selected the fixed camera reader as their first task-specific route. Counting the preceding business attempts left first-appropriate rates at 4/12 in each arm. Verified first attempts increased from 1/12 to 3/12 and grounded camera answers from 9/12 to 12/12. Each locale and role contributed at most one pair per factor.

Menu actors used search in 2/11 baseline and 4/11 classified turns. A suitable route appeared in the first three ranked groups for 0/2 and 1/4 of those returned rankings. The corresponding camera counts were 2/12 and 1/12 searches, with a suitable top-three group in 2/2 and 1/1 returned rankings.

For the menu, every eligible main (5/5 per arm) and child (6/6 per arm) first
tried a declaration lookup as their first task-specific route. That attempt
failed in every case. Every menu actor encountered at least one host approval
block. Five classified children began business work in the shell, while all
five classified mains began business work with the declaration lookup.

Among the six camera mains in the earlier arm, four first tried a mixed camera
tool for the task and two first used the resource. Three began business work by
inspecting the active scene or Editor state. Among the six earlier children,
three first tried a mixed camera tool for the task and three used the resource.
Four began business work in the shell. In the fixed-reader arm all six mains
and six children used the fixed reader as their first task-specific route.
Four mains started business work with it. The other two mains and four children
started business work in the shell. The two remaining children inspected
Editor or scene state first.

A separately scheduled Turkish menu main pair passed the same controls. Both
actors began in the shell. The baseline reached the blocked declaration lookup
on business call six. The classified main attempted it on business call one.
Both withheld the path. This supplementary pair stays outside the primary
counts above.

**Integrated-source qualification.** All five sessions, including the repeat, loaded both the
UPM package and the server from `7b9140dd72c9b21dc50ea71b060ac0d9d3de5749`.
The Editor identified the package location at runtime and all 1,556 package
files matched the archived revision. The release metadata commit
`6dd01d2fec00066ce983793e30440f7c1e4e560e` changes only the immutable server
URL in the package manifest and points it at the tested source. Its runtime
server source, dependency lock and project metadata are byte-identical.

This snapshot also includes the later discovery wording for bound reads and
unknown menu paths. It stays outside both one-factor comparisons. An
intermediate build had returned a duplicate stdio response-identity error for
the camera tool while its resource read succeeded on the same connection.
With the target bound to the SDK request, the integrated tool returned the
complete camera resource payload on the actual game scene. The static menu
declaration list remained readable. The fixture's native exact-path declaration
inspection remained disabled.

| Exact-source actor | First business attempt | First verified observation | Answer |
| --- | --- | --- | --- |
| Camera, Korean main | Fixed camera reader | Fixed reader on call 1 | Matched the oracle |
| Camera, English direct child | Shell call with unclassified purpose | Fixed reader on call 4 | Matched the oracle |
| Menu, English main | Exact declaration inspection, blocked by host approval | Menu declarations on call 6 | Matched the oracle |
| Menu, Korean direct child, repeat | Exact declaration inspection, blocked by host approval | Menu declarations on call 4 | Matched the oracle |

All four attributable actors started with an instruction read in the shell.
The original Korean menu child returned the correct path in a linked final
answer item. Its completed-turn notification was missing. The separately
recorded repeat waited for the child's own completion receipt and qualified.
The resulting five sessions had identical before/after scene, play, menu and
camera observations. Postflight also verified the original running game's
connection generation, active scene, play state, camera inventory and saved
scene digest against the initial measurements.

**Launch-pinned package retest.** Two fresh camera actors used UPM
`2d909c0e0ebb84585d6969533c05c46adbec5a44` and its declared server
`7b9140dd72c9b21dc50ea71b060ac0d9d3de5749`. The Editor resolved the native
assembly to that package. All 1,556 package files and 449 server source,
project metadata and lock files matched their respective archives. The
compiled assembly contained the corrected camera description. The fixed
tool and resource returned equal live camera inventories. Both actors had
the same prompts, permissions, competing services and host binary as their
corresponding integrated-source actors above.

| Camera actor | First host call | First business attempt | First verified observation | Answer |
| --- | --- | --- | --- | --- |
| Korean main | Shell instruction read | Fixed camera reader | Fixed reader on call 1 | Matched the oracle |
| English direct child | Shell instruction read | Editor state | Fixed reader on call 2 | Matched the oracle |
