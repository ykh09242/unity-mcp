"""Export the four native trace rows from a passing mixed UI NUnit fixture."""

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

_TEST = (
    "MCPForUnityTests.EditMode.Tools.Input.MixedUiPointerRouteTests."
    "SharedPointerSwitchesFromToolkitToUguiAfterPickingRepairAtBothScales"
)
_RESULT_LIMIT = 16 * 1024 * 1024
_TRACE_NAMES = {
    (1, False): "1x-before.json",
    (1, True): "1x-after.json",
    (1.5, False): "1.5x-before.json",
    (1.5, True): "1.5x-after.json",
}


def export_traces(results: Path, output: Path) -> None:
    """Preserve the native rows as-is in a fresh directory, grouped by UI scale."""
    with results.open("rb") as stream:
        content = stream.read(_RESULT_LIMIT + 1)
    if len(content) > _RESULT_LIMIT:
        raise ValueError("Unity results exceed the 16 MiB limit")
    root = ET.fromstring(content)
    cases = [case for case in root.iter("test-case") if case.get("fullname") == _TEST]
    if root.tag != "test-run" or len(cases) != 1 or cases[0].get("result") != "Passed":
        raise ValueError("The exact mixed UI test must be recorded once and pass")

    traces: dict[str, str] = {}
    for line in (cases[0].findtext("output") or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        row = json.loads(line)
        if row.get("fixture") != "mixed-ui-pointer":
            continue
        scale, repaired = row.get("scale"), row.get("repaired")
        if type(scale) not in (int, float) or type(repaired) is not bool:
            raise ValueError("A native mixed UI row has invalid scale or repair metadata")
        name = _TRACE_NAMES.get((scale, repaired))
        if name is None or name in traces:
            raise ValueError("Mixed UI results contain an unexpected or duplicate trace row")
        traces[name] = line
    if traces.keys() != set(_TRACE_NAMES.values()):
        raise ValueError("Expected native before and after traces for both UI scales")

    output.mkdir(parents=True)
    for name, line in traces.items():
        (output / name).write_text(line + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("output", type=Path, help="New directory for the four trace files.")
    args = parser.parse_args()
    try:
        export_traces(args.results, args.output)
    except (OSError, ValueError, ET.ParseError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
