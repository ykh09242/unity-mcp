# Fork compatibility review

The [inventory](../../tools/fork-compatibility.json) maps focused tool catalogs, project consent, immutable distribution identity, opt-in defaults, lifecycle ownership and protocol adapters to their owning paths and existing checks. It records the files that define supported versions rather than duplicating their version numbers. Those policies are not evidence of a new native run.

Select an explicit, already available upstream commit range. The base must be an ancestor of the head. The tool never fetches, checks out, merges or executes upstream code:

```sh
python tools/fork_compatibility.py --base <reviewed-start> --head <selected-end> --output .work/upstream-review/impact.json
```

The report records resolved commit identities, every commit in the range, touched owner paths (including renames and later reverted edits), unmapped paths, inventory/version-policy hashes and the current fork checkout. An impact report is not a compatibility verdict. Unmapped paths require manual review, including new features and native surfaces for which Python contracts alone are insufficient.

After adapting changes, use the same range on a clean committed fork checkout:

```sh
python tools/fork_compatibility.py --base <reviewed-start> --head <selected-end> --run-checks --output .work/upstream-review/validation.json
```

Add `--all-checks` to select the entire inventory explicitly; the report still preserves the actual path impacts and identifies the wider validation scope. Checks execute in separate processes so the existing HTTP and regular pytest fixture configurations remain isolated.

Mapped checks use the current Python environment, existing SDK/ownership tests, check-only catalog/SDK generators, and the release-evidence component identity validator. No dependency installation, native Editor launch, package publication or workload-limit change occurs. Each selected check retains its command, exit code and hashed log in a unique report directory; failures remain failures. The checkout must remain unchanged through execution. Use an ignored output directory and do not edit concurrently.

Passing mapped checks establishes only the recorded scope. Add native compile/Editor/Player evidence for changed native behavior using the existing isolated project and caches, preserving real test inventories and input hashes. The [release evidence guide](../../website/docs/contributing/releases.md) explains how actual results differ from skipped, historical and unavailable evidence. Keep the reviewed upstream range, adaptation commits and final validation report with the PR, without adding issue-closing references. Inventory additions belong with changes to a fork contract; unmatched changes must not silently inherit a passing result.
