"""Check supported authored files in an explicit CI base/head range, without writing."""

import argparse
from pathlib import Path
import re
import subprocess
import sys
from collections.abc import Sequence

if __package__:
    from tools import format_scripts
else:
    import format_scripts


ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-fA-F]{40}")
PYTHON_CONFIG = frozenset({"ruff.toml"})
CSHARP_CONFIG = frozenset({".editorconfig", ".config/dotnet-tools.json"})
RUNNER_INPUTS = frozenset({"tools/format_scripts.py", "tools/check_changed_formatting.py"})


def select_files(root: Path, base: str, head: str) -> tuple[Path, ...]:
    """Require exact commits and expand changed formatter settings to their language scope."""
    if any(not SHA.fullmatch(ref) or ref == "0" * 40 for ref in (base, head)):
        raise ValueError("Formatting requires explicit nonzero base and head commit SHAs.")
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    if actual.casefold() != head.casefold():
        raise ValueError("Formatting checkout does not match the requested head commit.")
    changed = format_scripts.git_paths(
        root, ["diff", "--name-only", "-z", "--no-renames", base, head, "--"]
    )
    # Use the runner's authored scope and link/generated/vendor exclusions unchanged.
    candidates = format_scripts.discover_files(root, True, ())
    python_scope = bool(changed & (PYTHON_CONFIG | RUNNER_INPUTS))
    csharp_scope = bool(changed & (CSHARP_CONFIG | RUNNER_INPUTS))
    return tuple(
        path
        for path in candidates
        if path.suffix.casefold() in format_scripts.PYTHON_SUFFIXES | format_scripts.CSHARP_SUFFIXES
        and (
            path.relative_to(root).as_posix() in changed
            or (python_scope and path.suffix.casefold() in format_scripts.PYTHON_SUFFIXES)
            or (csharp_scope and path.suffix.casefold() in format_scripts.CSHARP_SUFFIXES)
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--list", action="store_true", help="Print the selection without checking.")
    args = parser.parse_args(argv)
    try:
        files = select_files(ROOT, args.base, args.head)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Formatting selection failed: {error}", file=sys.stderr)
        return 2
    paths = [path.relative_to(ROOT).as_posix() for path in files]
    if args.list:
        print("\n".join(paths))
        return 0
    if not paths:
        # Never pass an empty explicit selection to the runner's local-change default.
        print("No supported authored formatting inputs changed; manual-only files remain manual.")
        return 0
    return format_scripts.main(["--check", *paths])


if __name__ == "__main__":
    raise SystemExit(main())
