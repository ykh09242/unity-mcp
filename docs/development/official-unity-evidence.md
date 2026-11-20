# Import evidence from official Unity CLI and Pipeline

Run the official `unity mcp` provider against the selected project. Have the
trusted host recorder retain its connected session, CLI and Pipeline versions,
loaded source identity, and the original Unity Test Framework NUnit XML.
For game state, retain a separate JSON capture written by a project-owned
observer in the launched standalone Player. Record the process ID from its
launcher, and inject the session, source and fresh execution IDs into the
observer at launch. Keep these exports outside actor control. The importer
reads existing artifacts offline and does not install a project probe.

```sh
unity-mcp-evidence --format json import-official session.json \
  --nunit results.xml --player native-player.json --output imported.json
unity-mcp --no-project-context --format json verification-evidence import-official session.json \
  --nunit results.xml --player native-player.json
```

The custodian produces a private UTF-8 JSON session record with
`schema: "official_unity_session@1"` and the following objects.

| Object | Fields |
| --- | --- |
| `producer` | `provider_command: "unity mcp"`, observed `unity_cli`, `pipeline_package`, identical `connected_pipeline`, `unity_version`, `session_id`, 32-hex Unity `project_guid`, full Git `source_revision`, and distinct pseudonymous `collector_id` and `operator_id`. |
| `nunit` | `execution_id`, `producer_id` and `sha256` of the exact XML bytes from that test job. Plain Unity NUnit cases retain their full parameterized names and reported states. |
| `player` | `execution_id`, `producer_id` of the independent game observer, integer `process_id`, `sha256` of the original JSON, `bindings` and `observations`. |

The importer accepts the project's original JSON field layout. In
`player.bindings`, map each required identity to its JSON Pointer in the
original capture. The field names are `session_id`, `source_revision`,
`project_guid`, `execution_id`, `producer_id`, `process_id` and
`environment`. The last must be the string `player`. The other fields must
match the recorder. For example, `"process_id": "/runtime/processId"`
selects the integer written by the launched process. In
`player.observations`, map each agreed game-state name to the location of
its independently observed value, such as
`{"door_open_after_interaction": "/frames/1/state/doorOpen"}`. Agree on
these fields before collection. Exclude expected values, actor conclusions
and command acknowledgments from the observation mapping. Select a state
field outside each identity pointer and its ancestors or descendants.
The reader preserves an explicitly observed empty inventory or null target.
A missing pointer leaves the observation unsupported. An unrelated
capture fails even when the copied report describes the same test.

The import preserves the producer and each available artifact digest. A
failed NUnit case or an observed Boolean `false` is retained as observed.
Exit 0 means both independent sources were imported, without grading the
game outcome. If the provider supplied only tests, omit `--player` and
the Player declaration. The import retains NUnit and marks game state
`unsupported`. Omit `--nunit` for a Player-only import. Missing coverage
and invalid bindings return exit 2. A digest establishes correspondence
with the trusted session record. Independent collection and access controls
establish who produced those bytes.
