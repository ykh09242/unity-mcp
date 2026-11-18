# Blind Unity tool selection

This benchmark asks a model to perform an observation without naming the tool,
action, resource URI, or expected result. Each task is available in English,
Korean, Japanese, Spanish, and Arabic. The matrix runs each selected task and
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

```sh
python tools/experiments/blind_tool_selection.py run \
  --project /work/Game --endpoint http://127.0.0.1:8080/mcp \
  --instance Game@0123456789abcdef --graph-asset Assets/Example.shadergraph \
  --output .work/blind-trial-01 --model gpt-6.2-sol --effort medium \
  --task game-view --task graph --task graph-saved \
  --language en --language ko --role main --role subagent
```

Repeat `--task`, `--language` or `--role` to select the matrix axes. The other task
IDs are `game-view`, `editor` and `context`. The other locales are `ja`, `es` and
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

## Score the traces

```sh
python tools/experiments/blind_tool_selection.py score .work/blind-trial-01
python -m pytest tools/tests/test_blind_tool_selection.py -q
```

The report records the first Unity business attempt and the first suitable
attempt in request order. Skill reads, resource listings and catalog operations
are recorded as discovery. Approval blocks can occur before the wire recorder
sees a request. The host events retain those attempts separately from server
errors. The repeat count groups equivalent tool and resource operations and
keeps Shader Graph pages distinct. It includes intentional re-observation. The
retry count reports the subset that follows a failure or an approval block on
the same operation and page.

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
report flags other possible saved-file observations for manual review. Review the raw
observation and answer to check the actual reported values. The score covers
trace evidence. Review natural-language answers separately.

Confirmed subagent attribution requires a host-observed native spawn with a
receiver ID and no competing parent business calls. Some CLI versions omit
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
