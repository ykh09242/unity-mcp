"""Render a conservative NL/T job summary from JUnit, independently of the CI gate."""

import argparse
import html
from pathlib import Path
import re
import xml.etree.ElementTree as ET


REQUIRED_IDS = (
    *[f"NL-{n}" for n in range(5)],
    *[f"T-{letter}" for letter in "ABCDEFGHIJ"],
    *[f"GO-{n}" for n in range(11)],
)
TEST_ID = re.compile(r"\b(NL-\d+|T-[A-Z]|GO-\d+)\b")


def _compact(text: str) -> str:
    return html.escape(" ".join(text.split())[:400])


def summarize(path: Path) -> str:
    """Require every required ID and every testcase occurrence to pass before success."""
    lines = ["## Unity NL/T Editing Suite — Summary", ""]
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        return "\n".join([*lines, "JUnit evidence is missing; results are incomplete."])
    except (ET.ParseError, OSError) as error:
        return "\n".join(
            [*lines, f"JUnit evidence is corrupt or unreadable: {_compact(str(error))}"]
        )

    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    if root.tag not in {"testsuite", "testsuites"}:
        return "\n".join([*lines, "JUnit evidence is corrupt: expected testsuite or testsuites."])
    cases = list(root.iter("testcase"))
    seen: set[str] = set()
    problems: list[str] = []
    counts = {"passed": 0, "failure": 0, "error": 0, "skipped": 0}
    for case in cases:
        name = (case.get("name") or "(unnamed)").strip()
        match = TEST_ID.match(name) or TEST_ID.search(case.findtext("system-out") or "")
        if match:
            seen.add(match.group(1))
        bad = [node for node in case if node.tag in {"failure", "error", "skipped"}]
        if not bad:
            counts["passed"] += 1
        for node in bad:
            counts[node.tag] += 1
            detail = node.get("message") or node.text or "no detail"
            problems.append(f"- {_compact(name)}: {node.tag} — {_compact(detail)}")

    # Nonzero suite-level counts must also prevent success if testcase detail is absent.
    for suite in root.iter():
        if suite.tag not in {"testsuite", "testsuites"}:
            continue
        for attribute in ("failures", "errors", "skipped"):
            raw = suite.get(attribute, "0")
            if not raw.isdecimal() or int(raw) > 0:
                problems.append(f"- Suite reports {attribute}={_compact(raw)}.")
    missing = [tid for tid in REQUIRED_IDS if tid not in seen]
    lines += [
        f"Totals: {counts['passed']} passed, {counts['failure']} failed, "
        f"{counts['error']} errors, {counts['skipped']} skipped, {len(cases)} total",
        "",
    ]
    if not cases:
        problems.insert(0, "- JUnit evidence is empty: no testcases.")
    if missing:
        lines.append("Missing required IDs: " + ", ".join(missing))
    if problems:
        lines += ["", "### Tests not passed", *problems[:20]]
        if len(problems) > 20:
            lines.append(f"- … {len(problems) - 20} additional problems; see the JUnit artifact.")
    if not missing and not problems:
        lines.append("All tests passed. Full per-test details are in `reports/junit-nl-suite.md`.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    print(summarize(parser.parse_args().junit))


if __name__ == "__main__":
    main()
