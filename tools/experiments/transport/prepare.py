#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic==2.13.5"]
# ///
# How to run: Server/.venv/Scripts/python.exe tools/experiments/transport/prepare.py
"""Generate the measurement team's common owned workload fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.tests.fixtures.transport_bench.workload import Workload, make_result  # noqa: E402


def main() -> None:
    fixtures = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / ".artifacts/fixtures"
    )
    fixtures.mkdir(parents=True, exist_ok=True)
    for workload in Workload:
        value = make_result(workload, 4 * 1024 * 1024)
        _ = (fixtures / f"{workload.value}.json").write_text(
            json.dumps(value, separators=(",", ":")), encoding="utf-8"
        )
    print(fixtures.resolve())


if __name__ == "__main__":
    main()
