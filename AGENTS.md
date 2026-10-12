# Unity MCP repository guidance

Read the ancestor workspace guidance and any guide on the target file's ancestor path.

## Language

- Write repository source, comments, documentation, UI text, and skills in English.
- Keep literal Hangul out of authored text. Preserve Unicode regression test values with Unicode escape sequences instead of removing their coverage.

## Formatting

- [docs/FORMATTING.md](docs/FORMATTING.md) owns formatter versions, source scope, setup and commands. Root `.editorconfig`, `ruff.toml` and `.config/dotnet-tools.json` are the canonical machine settings.
- Keep touched/new authored scripts consistent with those settings. Check the changed scope with `python tools/format_scripts.py --check`; use explicit file arguments for a narrower task.
- Repository-wide formatting requires an explicit request. Keep broad formatting separate from functional changes and preserve other contributors' edits. Do not commit, reset or discard work without authorization; staging already-authorized work does not require a separate approval.
- Do not format generated references, Unity metadata/assets, third-party code, caches, `.env*`, or `Assets/Resources/GameData/**`. Regenerate owned references with their generator.
- Formatting is separate from correctness checks. Preserve literal payloads, templates, fixtures, shell heredocs and PowerShell here-strings; do not apply blind whitespace replacement to source.

## Verification

Use the existing Python environment and relevant checks. `tools/lint_python.py` runs correctness lint; formatting does not replace it. Compilation is distinct from native Unity execution. Do not launch Unity, install optional packages, or mutate sibling projects merely to verify formatting or documentation.

## Feature program coordination

Use [the feature program tool guide](docs/development/feature-program-tool.md) for durable task coordination. When a local `.omo/feature-program/README.md` exists, follow its recovery protocol before resuming that program. Keep personal plans, source reports, agent/session information and execution evidence in the ignored local program directory; never force-add them. Use the program tool for state changes, and keep transient status out of this guide.
