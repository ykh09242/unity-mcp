# Hosted change review evidence

The **Change review evidence** workflow runs the focused offline controls on relevant
`beta` pushes and on demand. Dispatch it against the revision you want to qualify.
The checkout must equal the workflow event SHA. The run retains
`change-review-evidence-<sha>-<attempt>` for 30 days, with the checked revision,
execution and replay reports, and the workflow run and attempt IDs in `observations.json`.

| Observation | Comparison in the artifact |
| --- | --- |
| Resumable dependency inventory | Both scans read the same generated graph pages and committed fixture bytes. The test resumes after 19 pages and compares every inventory byte, plus the two SHA-256 digests. The JUnit properties record 1,000 assets, 5,000 edges, page and inventory sizes, and wall time for the uninterrupted call and the two resume calls. Rejection controls cover a partial diff, changed revision, stale pages, duplicate edges and a page gap. |
| Dependency diff | The CLI compares before and after graph inventories for a generated scene and records the changed relations, the explicit material retarget, the affected consumers, the output digest, and elapsed time. A policy-filtered control checks that hidden targets remain unknown. |
| Anticipated Git merge | Two real Git fixtures compare the base and both parent snapshots with the merged Git tree. Each merged result introduces a Resources finding while all three controls have zero failures. The receipt records exact fixture commits and trees, resolved-reference and failure data, and CLI elapsed time. Separate cases check Git conflicts and unavailable project scope. |
| Reviewed checkout divergence | Three generated Git checkout variants compare expected, committed and checked-out SHA-256 digests for the same inputs. Each case records the diagnostic classification, elapsed time, and actual admitted Player launch count of zero. |
| Paired test-budget replay | A generated NUnit fixture replays automatic and manual rankings with the same budget and independent full-run truth. Its recorded durations come from fixture captures. The separate CLI wall time measures hosted replay processing. The fixture provides expected misses, overrun counts and saved-time values for each plan. |
| Historical test-budget replay | `historical-replay.json` preserves the installed CLI result for the three real repository PRs in `historical-inventory.json`. Each archive row is missing full-run controls. The expected status is unavailable, with exit 2, zero paired PRs and no measured budget savings. |

`pytest-junit.xml` retains case durations, skipped or failed case reasons, and the
individual fixture receipts. `historical-stdout.txt` and `historical-stderr.txt` retain
the installed CLI streams. Dependency and merge timings measure the generated
corpora above. Native Editor and Player evidence comes from the corresponding
[native fixture jobs](native-case-coverage.md).
