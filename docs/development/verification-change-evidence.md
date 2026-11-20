# Compare a change using retained verification evidence

Save the `data` document returned by `manage_verification report`, or its
successful response envelope, on each side. Retain the corresponding Player
`run.json` from the reported child artifact directory. Prepare the candidate
with the baseline registration ID. The comparison checks its captured baseline
manifest hash and source token against the actual baseline report.

```sh
unity-mcp --no-project-context --format json verification-evidence compare \
  --baseline before/verification.json before/run.json \
  --candidate after/verification.json after/run.json --output comparison.json
```

Each native artifact must match the owned, settled child, scenario, source,
build ID, launcher-admitted payload and finalization evidence. Runtime platform,
backend, architecture, definition, repetitions, execution controls and recorded
project identity must agree. A deliberate source or payload change is expected
in a PR pair. The command reads only the paths passed by the caller. An earlier
successful run from another job cannot satisfy the candidate.

An Editor result needs a matching selected check, execution record and
acknowledged, settled Editor child. The records identify the check by kind,
name and definition hash. They also bind the result to the candidate source.
An individual NUnit result needs the exact returned case name and state from
the recorded test job. Older archives without these fields leave the selected
Editor check incomplete. A profile with no observed analysis or test job also
remains incomplete.

Each named result is classified as newly failed, still failing, changed failure,
repaired, still passing, outstanding or incomparable. An existing failure with
a different observed failure signature counts as changed. Exit 1 signals a new
or changed failure. Exit 2 indicates incomplete evidence or incompatible
conditions. Exit 0 reports no new failures in a complete comparison and can
include known pre-existing failures. An unresolved change in selected check
coverage remains visible in the JSON even when a new failure sets exit 1.
The comparison includes outstanding selection reasons such as an unmapped
changed path. Missing final source confirmation, incomplete input chunks and
Player reporting errors prevent the affected run from establishing a regression.
An incomplete Editor check or missing state marker leaves an independently
observed Player regression visible. The observation's expected values apply
to that observation when comparing reports or finding the first failing build.
Two failed results need comparable failure signatures
to distinguish a changed failure from an existing one.

## Find the first failing archived build

List the intended sequence oldest first in a `verification_build_history@1`
JSON document. Each `builds` entry has a distinct `build_id`, its exact
64-character `source_revision`, and `runs`. Each run specifies two relative
paths named `verification` and `native`, below the manifest directory. Use an
empty `runs` array for a known build whose artifacts were unavailable. Independent
repeats of a build go in the same row. The order comes from the supplied CI
inventory. The conclusion covers the builds in that inventory.

```sh
unity-mcp --format json verification-evidence first-failure history.json \
  --kind scenario --id doors --output first-failure.json
```

The command verifies the recorded build, source and unique jobs on every trial.
Repeated trials of one build must share its candidate manifest and payload
hash. A missing referenced file leaves a gap in that build's history. An
invalid or contradictory archive produces an error with exit 2.
A complete passing prefix followed by consistent failures identifies the first
failing build with exit 1. Consistent passes return 0. Missing artifacts,
contradictory repeats, an unmatched runtime or a later return to passing leave
the range open with exit 2. The result includes the ordered timeline, observed
passing/failing endpoints and unverified builds. For observed Editor checks,
each trial also identifies the Editor jobs in `check_job_ids`.

## Install the offline reader

Download the wheel from the `offline-verification-evidence` artifact on a successful
**Offline verification evidence** workflow run. To build it from a checkout, run
`python tools/build_offline_evidence.py --output .tmp/offline-wheel` with `uv` on
your path. Install the downloaded or locally built wheel in a Python 3.11 or newer
environment.

```sh
python -m pip install .tmp/offline-wheel/*.whl
unity-mcp-evidence --format json compare \
  --baseline before/verification.json before/run.json \
  --candidate after/verification.json after/run.json
```

The wheel needs Click and uses the same `unity_mcp_evidence` package bundled with
the full server. The installed commands `compare`, `first-failure`, `selection`,
and `shards` use the same arguments, output formats, and exit codes as the
corresponding `unity-mcp verification-evidence` commands.
NUnit selection checks the complete XML and computes SHA-256 from the original
bytes while streaming a regular local file up to 16 MiB. The reader rejects
linked paths, malformed or truncated XML, document types and entity declarations.

The workflow artifact also includes the two clean environments' installed package
bytes, shared Python source digests, and end-to-end first-read times in
`offline-install.json`. It compares complete JSON verdicts and exit codes for
synthetic saved JSON and NUnit captures and checks text and table output parity.
`nunit-reader.json` records verdict and digest parity, median read time, and
traced peak Python allocations against the previous reader on the same runner.

## Merge retained NUnit shards

Keep a manifest with the full case inventory assigned to each split and recorded
submission identities. Use exact NUnit `fullname` values, including arguments
for parameterized tests. The manifest can refer to ordinary NUnit XML without the
instrumentation required by `selection`.

```json
{
  "schema": "verification_nunit_shards@1",
  "run_id": "build-123/editmode",
  "splits": [
    {"split_id": "first", "cases": ["Doors.Opens(\"red\")"]},
    {"split_id": "second", "cases": ["Doors.Opens(\"blue\")"]}
  ],
  "submissions": [
    {
      "run_id": "build-123/editmode",
      "split_id": "first",
      "attempt": 1,
      "execution_id": "build-123/first/1",
      "xml": "artifacts/first.xml"
    }
  ]
}
```

```sh
unity-mcp-evidence --format json shards shards.json
unity-mcp --no-project-context --format json verification-evidence shards shards.json
```

The report has one outcome per planned case and a history of observed attempts.
Use ascending attempt numbers for retries within one split. A partial retry updates
the cases it reports. An identical submission of the same split attempt counts
once, including a byte-identical copy at a second path. Conflicting bytes for one
attempt, reused execution IDs or artifact digests across attempts, and cases outside
the assigned split are rejected. Submission IDs are declared by the archive manifest
and output digests identify the admitted XML bytes.

Coverage uses all planned splits and cases as denominators, including unsubmitted
splits. Only `Passed` and `Failed` cases count as executed. The report lists missing
and incomplete splits and retains any earlier results when a retry's artifact is
missing. Exit 0 means complete and passing, exit 1 means complete with failures,
and exit 2 means incomplete coverage or invalid evidence. Use `--output PATH`
to save a new JSON result for a readable manifest.

XML paths are relative to the manifest directory. The same local-file and streaming
limits apply to each shard. A merge accepts up to 128 splits, 256 submitted rows,
65,536 planned cases, and 128 MiB of shard XML in total.

To share change or selection evidence with a teammate, export a redacted
bundle and verify its digest on a separate machine. The
[team handoff guide](../verification-team-handoff.md) covers the supported
verdicts and how replay reports withheld inputs.
