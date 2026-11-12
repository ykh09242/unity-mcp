# Required Windows hermetic regressions

The `windows_hermetic` job in `python-tests.yml` runs
`tools/check_windows_regressions.py` independently of Unity licenses. Its explicit
inventory requires **70 executed tests**, with no skipped, failed or errored cases.
The JUnit gate also rejects missing, extra or duplicate case counts.

The release evidence export boundary selects
`test_linked_destination_parent_is_rejected[junction]` explicitly. This creates an
actual Windows directory junction, requires the exporter to reject a destination
below it, and verifies that the outside target remains empty. Linux skips cannot
satisfy this required Windows coverage. The symlink variant remains a separate
portable test because symlink creation can depend on host privileges.

Reproduce with the existing development environment from the repository root:

```sh
python tools/check_windows_regressions.py --output .tmp/windows-hermetic
```

The command creates and checks owned temporary fixtures only. It starts neither
Unity nor a live Player. Update the explicit inventory and its regression tests
together whenever a required case or parameter count changes.
