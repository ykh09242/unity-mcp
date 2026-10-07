# Script formatting

These rules persist across sessions. Use them for touched and new authored source.
Format the whole repository only when explicitly requested, and keep a broad formatting
change separate from functional changes so both remain reviewable. Preserve unrelated
working-tree changes. Formatting does not replace correctness lint, tests or compilation.

## Canonical settings

| Language | Formatter / settings | Convention |
| --- | --- | --- |
| Python | Ruff **0.16.10**, root `ruff.toml` | Python 3.11 syntax, 100 columns, 4 spaces, double quotes, LF |
| C# | Local CSharpier **1.3.0**, `.config/dotnet-tools.json`, root `.editorconfig` | 160 columns, 4 spaces, Allman braces, LF; preserve compact comment-only catches |
| JS / TS / JSX / CSS / HTML / JSON / YAML | Root `.editorconfig`, existing file conventions | 2 spaces, LF; JS uses single quotes and semicolons; JSON uses double quotes |
| Shell / PowerShell | Root `.editorconfig`, existing file conventions | Shell 2 spaces; PowerShell 4 spaces; LF |
| Windows batch / command scripts | Root `.editorconfig` | 2 spaces, CRLF |

The configuration files above own machine settings. This document owns setup, scope and
workflow. CSharpier controls its supported syntax layout; EditorConfig's Roslyn newline
settings also guide manual C# edits. The runner applies the checked normalization described
below. The wider C# limit keeps simple declarations readable
on one line. Preserve short catches such as `catch { /* User assembly not always needed */ }`;
multi-statement blocks remain expanded. Line width is a formatter target: do not split literal
strings or other content merely to enforce it. Keep literal payloads, fixtures, templates,
shell heredocs and PowerShell here-strings intact. Never run a generic whitespace rewrite
over source files. Markdown hard-break spaces are preserved by EditorConfig.

There is no repository-pinned Prettier, shell formatter or PowerShell formatter. The runner
reports those files as **EditorConfig/manual-only** and does not rewrite or certify their
syntax formatting. Do not install or add website dependencies just to format a change.
If a formatter is introduced later, pin it and update this document and the runner together.

`.gitattributes` sets text line endings for script/config extensions (LF, except Windows
batch files). It does not format source. Do not renormalize unrelated files as part of an
ordinary task. Generated files and third-party files keep their own source of truth.

### Compact C# catches

Use the runner for C# checks and writes, rather than a direct `csharpier check`: plain
CSharpier expands comment-only catches. The runner formats temporary copies first,
then proposes a single-line catch only when the block contains exactly one single-line
`/* comment */` and whitespace and the resulting line fits 160 columns. Plain catches
and simple single-line typed headers are eligible; filters, complex headers, multiple
comments and statements remain expanded.

Every proposal is formatted again by the same CSharpier version and settings. It is
accepted only when that second result exactly equals the first formatted output, byte
for byte. This rejects lookalike text inside strings or raw strings; a rejected proposal
keeps the expanded formatter output and reports the fallback. Check mode compares the
canonical result with the original without writing. Write mode verifies that all sources
still match their initial snapshots before writing changed results. No product-source
ignore comments are introduced.

For an already-reviewed formatter-only baseline mirror, Python callers may import
`tools/format_scripts.py` and call `format_csharp_files(repository_root, explicit_paths,
write=True)`. The repository root supplies the configuration and local tool manifest;
the mirror does not need its own Git metadata. The caller owns the explicit file list.

## One-time setup

Run from the repository root with Python 3.11 or newer, Git, uv and a .NET SDK compatible
with the pinned CSharpier tool available:

```sh
uv tool install ruff==0.16.10
dotnet tool restore
ruff --version
dotnet tool run csharpier -- --version
```

An existing project Python environment may instead contain exactly `ruff==0.16.10`.
The runner tries that environment, then `ruff` on PATH, then uv's cached pinned tool via
`uv tool run --offline --from ruff==0.16.10 ruff`. It verifies the version before running.
CSharpier is resolved through the repository's local dotnet tool manifest, with its version
verified too. Checks and writes never fetch packages or restore the dotnet tool; uv may
prepare its tool environment from the local cache. A missing tool produces a setup error
before any selected file is formatted.

See the primary [Ruff formatter documentation](https://docs.astral.sh/ruff/formatter/),
[uv tool guide](https://docs.astral.sh/uv/guides/tools/), and CSharpier
[installation](https://csharpier.com/docs/Installation),
[configuration](https://csharpier.com/docs/Configuration) and
[CLI](https://csharpier.com/docs/CLI) documentation for tool details.

## Commands and scope

```sh
# Default: check changed tracked files (staged and unstaged) plus nonignored new files.
python tools/format_scripts.py
python tools/format_scripts.py --check

# Inspect the exact selection without invoking any formatter.
python tools/format_scripts.py --list
python tools/format_scripts.py --all --list

# Limit an ordinary task to explicit individual files; paths with spaces need quotes.
python tools/format_scripts.py --check Server/src/main.py tools/format_scripts.py
python tools/format_scripts.py --write Server/src/main.py tools/format_scripts.py

# Only when repository-wide formatting was requested:
python tools/format_scripts.py --all --check
python tools/format_scripts.py --all --write
```

Use the project's Python executable if `python` selects a different environment. The
runner works from any directory; explicit relative file paths are relative to the repository
root. It accepts files, not directory globs, and cannot combine explicit paths with `--all`.
Exit code 0 means the invoked formatters passed; 1 (or a formatter's positive exit code)
means a check/process failed; 2 means selection/setup failed. Manual-only files are reported
separately and are not covered by a passing formatter result.

Selection comes from Git metadata, without recursive filesystem scanning. Eligible files
must be tracked or nonignored new regular files inside the repository; symlinks and linked
ancestors are skipped. The authored scope is:

- Root scripts/config and shallow `Server/`, `website/`, `.config/` config files.
- `.github/`, `CustomTools/`, `MCPForUnity/Editor/`, `MCPForUnity/Runtime/`.
- `Server/src/`, all `Server/tests/` including e2e tests.
- `TestProjects/UnityMCPTests/Assets/Tests/` and `Assets/Scripts/`.
- `tools/` including experiments, tests and authored fixtures; `scripts/`.
- `website/src/` and `website/scripts/`.

Excluded paths include `.tmp/`, virtual environments, caches, Unity `Library/`, `Temp/`
and `Packages/`, vendor/external/third-party code, generated files, build output,
`.env*`, and every `Assets/Resources/GameData/` subtree. The authored
`MCPForUnity/Editor/Tools/Build/` directory is source, not build output. Unity assets and
metadata, lock files, generated tool-reference pages and other test projects are outside
the formatting scope. Exclusion checks run before file contents are read.

Regenerate tool references using `tools/generate_docs_reference.py`; do not hand-format
`website/docs/reference/`. Review formatter diffs for literal-content changes. Run relevant
tests after source changes and `tools/lint_python.py` when correctness lint is needed.
Do not launch Unity or install optional Unity packages just to validate formatting.
