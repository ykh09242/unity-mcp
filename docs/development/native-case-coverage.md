# Native fixture coverage on pull requests

Open **Native case coverage** in the PR checks list. Its final job name gives the
current native count out of three, the state of each fixture, and a separate
successful workflow run count. The run summary links to the producing job and
immutable Actions artifact for each admitted observation. Download
`native-case-coverage-<run-id>-<attempt>` for the machine-readable snapshot.

| Fixture | Evidence needed on the observed revision |
| --- | --- |
| Prefab | Two independent Windows replays agree on the broken and repaired Player observations. The maintained prefab oracle confirms serialized identity, the broken load and the repaired state. The verifier and candidate package match the observed repository revision. The independently pinned game project checkout matches the candidate build's fixture commit. |
| Pointer | One licensed mixed UI matrix job runs the fixture and publishes the checked revision and both scale comparisons. Both show the first recipient changing from UI Toolkit to uGUI and retain the terminal handler and gameplay observations. |
| Recovery | The licensed Windows package job retains three forced process deaths and the fresh Editor observations of the GUID, entry count and intervening user address. |

**Current** requires a successful job from a reviewed same-repository beta push
or dispatch, the expected top-level workflow, the correct producing attempt,
and an unexpired source-bound artifact whose downloaded bytes match the Actions
SHA-256 digest. Actions must record artifact creation during the named successful
upload step of that job. A successful workflow, package preparation, compilation or a
synthetic regression alone supplies no current observation. A retained job
from a partial rerun links to the original attempt. A changed runner timeline
requires a new receipt. Each producer workflow's most recent run on the source
revision is considered.

**Archived** means a maintainer explicitly selected a retained artifact from a
proven ancestor commit and the same admission rules succeeded. Set
`NATIVE_CASE_ARCHIVE_IDS` to a comma-separated list of up to 12 numeric Actions
artifact IDs, preferred first. A manual dispatch can override that selection
through `archive_artifact_ids`. Earlier observations stay in the prior column,
even if the current case also has evidence. **Unavailable** includes the
observed reason and its source. An expired artifact or an API read failure
cannot supply a positive result.

## What the CI count covers

For a PR the observed revision is the contributor's head SHA. The collector
runs from reviewed beta. On a beta push it observes the receiving commit. On
a beta manual dispatch it accepts an optional full `observed_sha`. The run
summary identifies the observed, collector and event revisions. PR workflows
associated with a head SHA may actually have checked out GitHub's synthetic
merge commit. Their conclusions appear only in the CI count.

The denominator counts distinct run IDs returned by Actions for this SHA and
repository from push, dispatch and pull request events, excluding this coverage
workflow itself. PR records must match the head repository. A partial rerun is
one run with the most recent attempt's conclusion. Queued, running, skipped,
cancelled and failed runs contribute to the denominator. The numerator counts
`success`. An incomplete inventory is marked unknown. Each run links to its
reported attempt.

The check takes a snapshot without waiting for parallel workflows. Rerun all
jobs in this coverage workflow from the original PR check to refresh its
visible title after producers finish. For a receiving commit, dispatch **Native
case coverage** on beta with the full observed SHA. The dispatch requires no
Unity license, credentials for a native build, or reviewed Player pair.

## Recorded baseline

At `7367cafd70e7c54bc9bb808d5af9958363c64310`, all **10/10** Actions runs
succeeded and **0/3** maintained native fixtures had a current observation.
The Player admission reported missing reviewed build pins. The mixed UI job
and the Windows package job were skipped.
[Player admission artifact](https://github.com/ykh09242/unity-mcp/actions/runs/44754434481/artifacts/14636238014)
· [optional native job](https://github.com/ykh09242/unity-mcp/actions/runs/44754434762/job/136350890612)
· [Windows package job](https://github.com/ykh09242/unity-mcp/actions/runs/44754434582/job/136350466177).

| Successful workflow | Successful workflow |
| --- | --- |
| [Compile check](https://github.com/ykh09242/unity-mcp/actions/runs/44755454857) | [Offline verification](https://github.com/ykh09242/unity-mcp/actions/runs/44754514750) |
| [Transport wire](https://github.com/ykh09242/unity-mcp/actions/runs/44754509689) | [Onboarding](https://github.com/ykh09242/unity-mcp/actions/runs/44754434579) |
| [Formatting](https://github.com/ykh09242/unity-mcp/actions/runs/44754434511) | [Release qualification](https://github.com/ykh09242/unity-mcp/actions/runs/44754434582) |
| [Documentation](https://github.com/ykh09242/unity-mcp/actions/runs/44754434518) | [Prebuilt Player pair](https://github.com/ykh09242/unity-mcp/actions/runs/44754434481) |
| [Distribution canary, attempt 2](https://github.com/ykh09242/unity-mcp/actions/runs/44754434490/attempts/2) | [Fork beta tools](https://github.com/ykh09242/unity-mcp/actions/runs/44754434762) |
