"""Collect or execute the sealed pytest node IDs in one fresh hosted job."""

import argparse
import os
import sys
from pathlib import Path
from time import monotonic

import pytest
from prospective_pr import ROLES, digest, git, read, stamp, write


class Capture:
    def __init__(self, selected):
        self.selected = selected
        self.inventory = []
        self.cases = {}

    def pytest_collection_modifyitems(self, config, items):
        self.inventory = [item.nodeid for item in items]
        if self.selected is not None:
            wanted = set(self.selected)
            config.hook.pytest_deselected(
                items=[item for item in items if item.nodeid not in wanted]
            )
            items[:] = [item for item in items if item.nodeid in wanted]

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.cases[report.nodeid] = "failed"
        elif report.skipped:
            self.cases.setdefault(report.nodeid, "skipped")
        elif report.when == "call":
            self.cases.setdefault(report.nodeid, "passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("inventory", *ROLES))
    parser.add_argument("output", type=Path)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    # The trusted observer lives in a second checkout. Tests must import modules
    # from the candidate they are exercising, including this feature's own tests.
    sys.path[0] = str(Path.cwd() / "tools")
    sys.modules.pop("prospective_pr", None)
    options = [
        "--rootdir=.",
        "-o",
        "asyncio_default_fixture_loop_scope=function",
        "-q",
        "--tb=short",
        "tools/tests",
    ]
    if args.role == "inventory":
        capture = Capture(None)
        code = pytest.main(["--collect-only", *options], plugins=[capture])
        if code == 0:
            write(args.output, capture.inventory)
        return code
    plan = read(args.plan)
    head = git("rev-parse", "HEAD")
    if (head, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]) != (
        plan["head"],
        plan["run_id"],
        plan["attempt"],
    ):
        raise ValueError("Plan differs from the job's checked-out opening head or workflow attempt")
    names = plan["selection"][args.role]
    if not names:
        print(f"No {args.role} invocation: selection empty or unavailable.")
        return 0
    capture = Capture(None if args.role == "full" else names)
    started_at = stamp()
    start = monotonic()
    code = pytest.main(options, plugins=[capture])
    seconds = monotonic() - start
    write(
        args.output,
        {
            "role": args.role,
            "head": head,
            "plan_sha256": digest(plan),
            "execution_id": f"{plan['run_id']}/{plan['attempt']}/{args.role}",
            "started_at": started_at,
            "seconds": seconds,
            "exit": int(code),
            "cases": capture.cases,
        },
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
