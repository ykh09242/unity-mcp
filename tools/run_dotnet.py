"""Run the repository's one-shot .NET SDK builds with checked temporary storage."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import os
from pathlib import Path
import subprocess
import sys
import tempfile


COMMANDS = ("build", "msbuild", "publish", "restore", "run", "test")


def temp_parent() -> Path:
    names = ("TMP", "TEMP") if os.name == "nt" else ("TMPDIR",)
    for name in names:
        if value := os.environ.get(name):
            return Path(value).absolute()
    return Path(tempfile.gettempdir())


def check_artifacts(command: str, arguments: Sequence[str]) -> None:
    # Everything after -- belongs to the fixture or test adapter.
    sdk_arguments = list(arguments)
    if "--" in sdk_arguments:
        sdk_arguments = sdk_arguments[: sdk_arguments.index("--")]
    options = {"--artifacts-path"}
    if command in {"build", "publish", "test"}:
        options.update(("-o", "--output"))
    if command == "test":
        options.add("--results-directory")
    for index, argument in enumerate(sdk_arguments):
        option, equal, destination = argument.partition("=")
        if option not in options:
            continue
        if not equal:
            if index + 1 == len(sdk_arguments) or sdk_arguments[index + 1].startswith("-"):
                raise ValueError(f"{option} requires a directory")
            destination = sdk_arguments[index + 1]
        if not destination:
            raise ValueError(f"{option} requires a directory")
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path) as probe:
            probe.write(b"1")
            probe.flush()


def run(command: str, arguments: Sequence[str], parent: Path) -> int:
    # Creating and writing here before starting dotnet also tests the actual ACL.
    # Do not let tempfile silently fall back from a broken inherited TMP/TEMP.
    with tempfile.TemporaryDirectory(prefix="unity-mcp-build-", dir=parent) as stage:
        with tempfile.TemporaryFile(dir=stage) as probe:
            probe.write(b"1")
            probe.flush()
    check_artifacts(command, arguments)
    # NuGet uses the temporary root for cross-process locks on its shared package cache.
    environment = {
        **os.environ,
        "TMP": str(parent),
        "TEMP": str(parent),
        "TMPDIR": str(parent),
        "MSBUILDDISABLENODEREUSE": "1",
        "MSBUILDUSESERVER": "0",
        "UseSharedCompilation": "false",
    }
    options = [] if command == "msbuild" else ["--disable-build-servers"]
    # dotnet run forwards unknown MSBuild switches to the application.
    if command != "run":
        options += ["-m:1", "-nr:false", "-p:UseSharedCompilation=false"]
    return subprocess.run(
        ["dotnet", command, *options, *arguments], env=environment, check=False
    ).returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent", type=Path, help="Existing writable temporary directory")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        parent = args.temp_parent or temp_parent()
        return run(args.command, args.arguments, parent.absolute())
    except OSError as error:
        detail = f"{error.filename}: {error.strerror}" if error.filename else str(error)
        print(f"Cannot run dotnet {args.command}: {detail}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"Cannot run dotnet {args.command}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
