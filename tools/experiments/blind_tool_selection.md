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
window contents. For `graph-open` the saved file can establish only the
observation limit. The report keeps the first suitable window read empty.
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
