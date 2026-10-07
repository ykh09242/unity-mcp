"""Check changed authored scripts; writing and repository-wide scope are explicit."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
RUFF_VERSION: Final = "0.16.10"
CSHARPIER_VERSION: Final = "1.3.0"
PYTHON_SUFFIXES: Final = frozenset({".py", ".pyi"})
CSHARP_SUFFIXES: Final = frozenset({".cs", ".csx"})
EDITOR_SUFFIXES: Final = frozenset(
    {
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".json",
        ".css",
        ".scss",
        ".html",
        ".yml",
        ".yaml",
        ".toml",
        ".sh",
        ".bash",
        ".ps1",
        ".psm1",
        ".psd1",
        ".bat",
        ".cmd",
    }
)
SOURCE_ROOTS: Final = (
    ".github/",
    "CustomTools/",
    "MCPForUnity/Editor/",
    "MCPForUnity/Runtime/",
    "Server/src/",
    "Server/tests/",
    "TestProjects/UnityMCPTests/Assets/Tests/",
    "TestProjects/UnityMCPTests/Assets/Scripts/",
    "tools/",
    "scripts/",
    "website/src/",
    "website/scripts/",
)
EXCLUDED_PARTS: Final = frozenset(
    {
        ".git",
        ".tmp",
        ".omo",
        ".codex",
        ".codegraph",
        ".venv",
        "venv",
        ".unity-ci",
        ".unity-ci-sdk",
        ".compile-refs",
        ".unity-check-logs",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "library",
        "temp",
        "logs",
        "obj",
        "bin",
        "build",
        "builds",
        "dist",
        "vendor",
        "external",
        "thirdparty",
        "third-party",
        "generated",
        "packages",
    }
)
GENERATED_NAMES: Final = frozenset({"package-lock.json", "packages-lock.json"})
COMPACT_CATCH: Final = re.compile(
    r"^(?P<indent>[ \t]*)(?P<header>catch(?:[ \t]*\([^()\r\n]*\))?)[ \t]*\r?\n"
    r"[ \t]*\{\s*(?P<comment>/\*(?:(?!/\*|\*/)[^\r\n])*\*/)\s*\}[ \t]*(?=\r?$)",
    re.MULTILINE,
)


def is_authored_path(relative: str) -> bool:
    """Reject excluded paths before reading their contents or following links."""
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    parts = tuple(part.casefold() for part in path.parts)
    if any(part.startswith(".env") for part in parts):
        return False
    if "/assets/resources/gamedata/" in "/" + relative.casefold() + "/":
        return False
    for part in parts:
        # The package contains an authored Editor/Tools/Build source directory.
        if part in EXCLUDED_PARTS and not (part == "build" and parts[0] == "mcpforunity"):
            return False
    name = path.name.casefold()
    if name in GENERATED_NAMES or name.endswith(
        (
            ".lock.json",
            ".g.cs",
            ".generated.cs",
            ".designer.cs",
            "_pb2.py",
            "_pb2_grpc.py",
            ".min.js",
            ".min.css",
        )
    ):
        return False
    if path.suffix.casefold() not in PYTHON_SUFFIXES | CSHARP_SUFFIXES | EDITOR_SUFFIXES:
        return False
    return (
        len(path.parts) == 1
        or relative.startswith(SOURCE_ROOTS)
        or (len(path.parts) == 2 and path.parts[0] in {"Server", "website", ".config"})
    )


def git_paths(root: Path, arguments: Sequence[str]) -> set[str]:
    """Use NUL-separated Git metadata so spaces and renamed paths are preserved."""
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in result.stdout.split(b"\0")
        if value
    }


def discover_files(root: Path, all_files: bool, requested: Sequence[str]) -> tuple[Path, ...]:
    """Select existing tracked and nonignored new files, without traversing the tree."""
    exclusions = (
        ":(glob,exclude)**/.env*",
        ":(exclude).env*",
        ":(glob,exclude)**/Assets/Resources/GameData/**",
    )
    candidates = git_paths(
        root,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".", *exclusions],
    )
    if requested:
        selected = set()
        for argument in requested:
            path = Path(argument)
            absolute = path if path.is_absolute() else root / path
            try:
                relative = absolute.absolute().relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("Explicit formatter paths must be inside the repository.") from exc
            if relative not in candidates or not is_authored_path(relative):
                raise ValueError(f"Not an eligible tracked/new authored script: {relative}")
            selected.add(relative)
        candidates &= selected
    elif not all_files:
        changed = git_paths(
            root,
            ["diff", "--name-only", "-z", "--diff-filter=ACMRT", "HEAD", "--", ".", *exclusions],
        )
        changed |= git_paths(
            root, ["ls-files", "-z", "--others", "--exclude-standard", "--", ".", *exclusions]
        )
        candidates &= changed
    files = []
    for relative in sorted(candidates):
        if not is_authored_path(relative):
            continue
        path = root / relative
        # resolve equality also excludes symlink/junction ancestors pointing elsewhere.
        if path.is_file() and not path.is_symlink() and path.resolve() == path.absolute():
            files.append(path)
        elif requested:
            raise ValueError(
                f"Formatter paths must be existing regular files without links: {relative}"
            )
    return tuple(files)


def verified_command(root: Path, command: Sequence[str], version: str) -> tuple[str, ...] | None:
    try:
        result = subprocess.run(
            [*command, "--version"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    pattern = rf"(?m)^(?:ruff\s+)?{re.escape(version)}(?:\+\S+)?\s*$"
    if result.returncode == 0 and re.search(pattern, result.stdout):
        return tuple(command)
    return None


def ruff_command(root: Path) -> tuple[str, ...]:
    candidates = [(sys.executable, "-m", "ruff")]
    if executable := shutil.which("ruff"):
        candidates.append((executable,))
    if executable := shutil.which("uv"):
        candidates.append(
            (executable, "tool", "run", "--offline", "--from", f"ruff=={RUFF_VERSION}", "ruff")
        )
    for command in candidates:
        if verified := verified_command(root, command, RUFF_VERSION):
            return verified
    raise RuntimeError(
        "Ruff 0.16.10 is unavailable. Run: uv tool install ruff==0.16.10 (see docs/FORMATTING.md)."
    )


def csharpier_command(root: Path) -> tuple[str, ...]:
    executable = shutil.which("dotnet")
    command = (executable, "tool", "run", "csharpier", "--") if executable else ()
    if command and (verified := verified_command(root, command, CSHARPIER_VERSION)):
        return verified
    raise RuntimeError(
        "Local CSharpier 1.3.0 is unavailable. Install the compatible .NET SDK, then run dotnet tool restore from the repository root."
    )


def run_batches(root: Path, command: Sequence[str], files: Sequence[Path]) -> int:
    """Keep explicit file arguments below Windows' command-line length limit."""
    status = 0
    batch: list[str] = []
    size = sum(len(argument) + 3 for argument in command)
    for path in files:
        argument = str(path)
        if batch and size + len(argument) + 3 > 20_000:
            result = subprocess.run([*command, *batch], cwd=root, check=False)
            status = max(status, result.returncode if result.returncode >= 0 else 1)
            batch = []
            size = sum(len(value) + 3 for value in command)
        batch.append(argument)
        size += len(argument) + 3
    if batch:
        result = subprocess.run([*command, *batch], cwd=root, check=False)
        status = max(status, result.returncode if result.returncode >= 0 else 1)
    return status


def compact_catch_candidates(formatted: bytes) -> bytes:
    """Propose narrow whitespace edits; only a formatter roundtrip can accept them."""
    text = formatted.decode("utf-8")

    def compact(match: re.Match[str]) -> str:
        line = f"{match['indent']}{match['header']} {{ {match['comment']} }}"
        return line if len(line) <= 160 else match[0]

    return COMPACT_CATCH.sub(compact, text).encode("utf-8")


def format_csharp_files(
    root: Path,
    files: Sequence[Path],
    write: bool = False,
    command: Sequence[str] | None = None,
) -> int:
    """Canonicalize explicit C# files, including formatter-only baseline mirrors.

    Public CLI selection happens in discover_files. Programmatic callers must supply
    their own reviewed file list. The root supplies settings and the local tool manifest.
    """
    if not files:
        return 0
    executable = tuple(command) if command is not None else csharpier_command(root)
    originals = {path: path.read_bytes() for path in files}
    with tempfile.TemporaryDirectory(prefix="unity-mcp-csharp-format-") as temporary:
        directory = Path(temporary)
        ignore = directory / ".csharpierignore"
        ignore.write_bytes(b"")
        mirrors = {}
        for index, path in enumerate(files):
            mirror = directory / f"source-{index}{path.suffix}"
            mirror.write_bytes(originals[path])
            mirrors[path] = mirror
        formatter = (
            *executable,
            "format",
            "--include-generated",
            "--no-cache",
            "--config-path",
            str(root / ".editorconfig"),
            "--ignore-path",
            str(ignore),
        )
        status = run_batches(root, formatter, tuple(mirrors.values()))
        if status:
            return status
        formatted = {path: mirror.read_bytes() for path, mirror in mirrors.items()}
        candidates = {path: compact_catch_candidates(value) for path, value in formatted.items()}
        changed = tuple(path for path in files if candidates[path] != formatted[path])
        for path in changed:
            mirrors[path].write_bytes(candidates[path])
        if changed:
            status = run_batches(root, formatter, tuple(mirrors[path] for path in changed))
            if status:
                return status
            for path in changed:
                if mirrors[path].read_bytes() != formatted[path]:
                    print(
                        f"Keeping expanded C# formatting: unsafe catch candidate in {path}",
                        flush=True,
                    )
                    candidates[path] = formatted[path]
        different = tuple(path for path in files if candidates[path] != originals[path])
        # Check every source before the first write, including files whose output is equal.
        if any(path.read_bytes() != originals[path] for path in files):
            raise RuntimeError("C# source changed while formatting; no C# output was written.")
        for path in different:
            if write:
                path.write_bytes(candidates[path])
            else:
                print(f"Would reformat: {path}")
        return 0 if write or not different else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check only (the default).")
    mode.add_argument(
        "--write", action="store_true", help="Format selected Python/C# files in place."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Select all eligible authored scripts; use only when requested.",
    )
    parser.add_argument(
        "--list", action="store_true", help="List selected paths without invoking formatters."
    )
    parser.add_argument(
        "paths", nargs="*", help="Explicit repository-relative file paths instead of changed scope."
    )
    args = parser.parse_args(argv)
    if args.all and args.paths:
        parser.error("Use either --all or explicit paths.")
    try:
        files = discover_files(ROOT, args.all, args.paths)
        if args.list:
            for path in files:
                print(path.relative_to(ROOT).as_posix())
            return 0
        python_files = tuple(path for path in files if path.suffix.casefold() in PYTHON_SUFFIXES)
        csharp_files = tuple(path for path in files if path.suffix.casefold() in CSHARP_SUFFIXES)
        editor_files = len(files) - len(python_files) - len(csharp_files)
        if editor_files:
            print(
                f"EditorConfig/manual-only: {editor_files} web/config/shell/PowerShell files; no syntax formatter or whitespace rewriting applied.",
                flush=True,
            )
        commands = []
        if python_files:
            command = (
                *ruff_command(ROOT),
                "format",
                "--config",
                str(ROOT / "ruff.toml"),
                "--no-cache",
            )
            commands.append((command if args.write else (*command, "--check"), python_files))
        csharp_command = csharpier_command(ROOT) if csharp_files else None
        status = 0
        for command, targets in commands:
            print(
                f"{'Formatting' if args.write else 'Checking'} {len(targets)} {targets[0].suffix} files",
                flush=True,
            )
            status = max(status, run_batches(ROOT, command, targets))
        if csharp_files:
            print(
                f"{'Formatting' if args.write else 'Checking'} {len(csharp_files)} C# files",
                flush=True,
            )
            status = max(
                status, format_csharp_files(ROOT, csharp_files, args.write, csharp_command)
            )
        if not files:
            print("No eligible changed authored scripts.")
        return status
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Formatting failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
