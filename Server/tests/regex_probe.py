"""Isolated real-regex probes with a separate parent-owned runaway guard."""

import json
import re
import subprocess
import sys
import time

from services.tools import bounded_regex
from services.tools.script_apply_edits import _find_best_anchor_match

OUTER_GUARD_SECONDS = 30


def run_probe(operation):
    """Reap the child on success, failure or guard expiry; never leave a regex worker behind."""
    result = subprocess.run(
        [sys.executable, "-m", "tests.regex_probe", operation],
        check=True,
        capture_output=True,
        text=True,
        timeout=OUTER_GUARD_SECONDS,
    )
    return json.loads(result.stdout)


def measure(operation):
    requested = []
    original = bounded_regex._compile

    class ObservedPattern:
        def __init__(self, compiled):
            self.compiled = compiled

        def finditer(self, text, *, timeout):
            requested.append(timeout)
            return self.compiled.finditer(text, timeout=timeout)

        def search(self, text, *, timeout):
            requested.append(timeout)
            return self.compiled.search(text, timeout=timeout)

    bounded_regex._compile = lambda *args: ObservedPattern(original(*args))
    started = time.monotonic()
    outcome = "completed"
    try:
        if operation == "braces":
            text = "class C {\n" + (" " * 193 + "}\n") * 10_000
            match = _find_best_anchor_match(r"^ *}$", text, re.MULTILINE)
            assert match.start() == text.rfind("\n", 0, len(text) - 2) + 1
        else:
            text = "a" * 30_000 + "!"
            if operation == "find":
                bounded_regex.find_matches(r"(a+)+$", text)
            elif operation == "anchor":
                _find_best_anchor_match(r"(a+)+$", text, 0)
            elif operation == "replace":
                bounded_regex.substitute(r"(a+)+$", "x", text, 0)
            else:
                raise ValueError(operation)
    except TimeoutError:
        outcome = "timed_out"
    finally:
        bounded_regex._compile = original
    return {
        "outcome": outcome,
        "requested_timeouts": requested,
        "elapsed_ms": (time.monotonic() - started) * 1000,
    }


if __name__ == "__main__":
    print(json.dumps(measure(sys.argv[1])))
