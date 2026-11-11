# AssetGen actual domain reload CI

`AssetGen actual domain reload` (`assetgen-reload-tests.yml`) is a separate,
opted-in licensed workflow, also reused by `release-qualification.yml`. Dispatch it
on a reviewed repository ref and set `run_assetgen_reload` to `true`. The checkout
must match the dispatch event's exact SHA; a reusable caller must pass that same full
SHA as `ref`. The repository variable `UNITY_RUN_LICENSED_TESTS` must be `true`, with
the same Unity license credentials required by the ordinary licensed suite. An
opted-in run fails when those prerequisites are absent. The default input is `false`.

The job uses the validated `defaultVersion` from `tools/unity-versions.json`, its
pinned test Editor image, and `unity_ci_packages.py`'s version-specific package
preparation. It runs each probe in its own fresh prepared project and Editor
process, without restoring a Library cache or accepting a user project path:

| Probe | Explicit method | Required ownership variable |
| --- | --- | --- |
| Queued submission | `AssetGenQueuedActualReloadTests.ActualDomainReload_QueuedIntentSubmitsOnceWithSameId` | `UNITY_MCP_ASSETGEN_QUEUE_RELOAD_PROJECT` |
| Recorded task recovery | `AssetGenActualReloadTests.ActualDomainReload_ResumesRecordedTaskWithoutResubmit` | `UNITY_MCP_GEN06_RELOAD_PROJECT` |

Both fixtures are in `MCPForUnityTests.Editor.AssetGen`. They request real Unity
domain replacement while retaining fake provider/key boundaries; this workflow
does not require AssetGen provider credentials. The probes require an empty owned
generation history and preserve their existing session/nonce checks.

The pinned GameCI action does not expose arbitrary environment forwarding. The
workflow builds a local image from the already prepared Editor image with
`--network=none`, adding only the matching ownership environment variable. Its
value is exactly `/github/workspace/.unity-ci/assetgen-reload/<probe>/project`,
matching the pinned CLI's Linux workspace mount and prepared `projectPath`.
No image is published and no fixture ownership assertion is relaxed.

Each invocation selects its fully qualified method using `-testFilter` and
requires that same method through `check_unity_test_results.py --require-test`.
A missing, skipped, explicit-only or failing case cannot pass because an unrelated
test succeeded. The normal `domain_reload` category and mandatory skip policy are
unchanged; their results do not substitute for these two probes.

Artifacts retain the checked SHA, selected Editor/method, resolved package receipt,
ownership Dockerfile, and reserved native NUnit/log evidence for 30 days, including
failure paths. They exclude the disposable project's full Library, home directory
and license contents. A failed preparation may have only its earlier source receipt.

The implementation follows the exact pinned
[action wrapper](https://github.com/game-ci/unity-test-runner/blob/32e57712352b500e17974b245a6dce9e11a73213/src/index.ts),
[action inputs](https://github.com/game-ci/unity-test-runner/blob/32e57712352b500e17974b245a6dce9e11a73213/action.yml)
and CLI `v0.1.69`
[Docker mount implementation](https://github.com/game-ci/cli/blob/v0.1.69/src/model/docker.ts).
Keep the action and CLI pins explicit when changing these contracts.
