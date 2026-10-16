# Unity MCP repository guidance

Read the ancestor workspace guidance and any guide on the target file's ancestor path.

## Language

- Write repository documentation and skills in English; keep literal Hangul out of documentation.
- Scripts, tests and UI strings may contain literal Korean, including translations and Unicode regression fixtures. Unicode escapes are optional; preserve the intended text and test coverage.

## Formatting

- [docs/FORMATTING.md](docs/FORMATTING.md) owns formatter versions, source scope, setup and commands. Root `.editorconfig`, `ruff.toml` and `.config/dotnet-tools.json` are the canonical machine settings.
- Keep touched/new authored scripts consistent with those settings. Check the changed scope with `python tools/format_scripts.py --check`; use explicit file arguments for a narrower task.
- Repository-wide formatting requires an explicit request. Keep broad formatting separate from functional changes and preserve other contributors' edits. Do not commit, reset or discard work without authorization; staging already-authorized work does not require a separate approval.
- Do not format generated references, Unity metadata/assets, third-party code, caches, `.env*`, or `Assets/Resources/GameData/**`. Regenerate owned references with their generator.
- Formatting is separate from correctness checks. Preserve literal payloads, templates, fixtures, shell heredocs and PowerShell here-strings; do not apply blind whitespace replacement to source.

## Verification

Use the existing Python environment and relevant checks. `tools/lint_python.py` runs correctness lint; formatting does not replace it. Compilation is distinct from native Unity execution. Do not launch Unity, install optional packages, or mutate sibling projects merely to verify formatting or documentation.

## Task coordination

Use the task conversation, scoped commits/PRs, relevant tests and CI for ordinary development. Keep one short handoff under ignored `.work/handoffs/` when continuity is needed. Reuse completed checks when their inputs have not changed; do not require registry updates, evidence packets, dashboard availability or management-tool repairs before implementing, reviewing or merging core changes.

The [feature program tool](docs/development/feature-program-tool.md) and [directing workflow](docs/development/work-program-workflow.md) are optional. Use them only when the user explicitly selects that workflow for the current task; existing local programs do not activate it. When selected, use its commands to change program state and follow relocation markers. Preserve private records in ignored `.work/programs/`; never force-add them. Keep transient status out of this guide.
