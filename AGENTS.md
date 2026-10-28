# Unity MCP repository guidance

Read the ancestor workspace guidance and any guide on the target file's ancestor path.

## Language

- Write repository documentation and skills in English; keep literal Hangul out of documentation.
- Scripts, tests and UI strings may contain literal Korean, including translations and Unicode regression fixtures. Unicode escapes are optional; preserve the intended text and test coverage.

## Formatting

- [docs/FORMATTING.md](docs/FORMATTING.md) owns formatter versions, source scope, setup and commands. Root `.editorconfig`, `ruff.toml` and `.config/dotnet-tools.json` are the canonical machine settings.
- Keep touched/new authored scripts consistent with those settings. Check the changed scope with `python tools/format_scripts.py --check`; use explicit file arguments for a narrower task.
- Repository-wide formatting requires an explicit request. Keep broad formatting separate from functional changes and preserve other contributors' edits. Do not commit, reset or discard work without authorization; staging already-authorized work does not require a separate approval.
- Do not format generated references, Unity metadata/assets, third-party code, caches, or `.env*`. Regenerate owned references with their generator.
- Formatting is separate from correctness checks. Preserve literal payloads, templates, fixtures, shell heredocs and PowerShell here-strings; do not apply blind whitespace replacement to source.

## Verification

Use the existing Python environment and relevant checks. `tools/lint_python.py` runs correctness lint; formatting does not replace it. Compilation is distinct from native Unity execution. Do not launch Unity, install optional packages, or mutate sibling projects merely to verify formatting or documentation.

## Tool guidance

- Put reusable decision rules in shared server instructions: scope, ownership/lifetime, state preservation, bounded observation and recovery after uncertain results. Keep tool descriptions focused on their capabilities and actual contracts.
- Generalize lessons from individual failures when the same principle applies elsewhere. Keep case-specific recipes and longer examples in the relevant skill reference, loaded when needed. Do not turn one example into an unconditional create/reset/cleanup sequence.
- Preserve concrete parameter names, limits, prerequisites and failure semantics needed for correct calls. Avoid unsupported performance claims and repeated common guidance that inflates every tool description.

## Task coordination

Use the applicable installed Superpowers skills for general workflow: `superpowers:writing-plans`, `superpowers:executing-plans`, `superpowers:requesting-code-review`, `superpowers:verification-before-completion` and `superpowers:finishing-a-development-branch`. Preserve existing user authorization and task scope; do not add redundant approval or tracking steps. Keep checks proportionate and reuse evidence whose inputs are unchanged.

- For authorized squash delivery, inspect review status and applicable CI for the exact PR head and base immediately before merging. Then inspect applicable beta CI for the actual receiving commit.
- Delete completed branches and worktrees when the user requests it, preserving work the user wants to retain.
- For compaction or transfer, update one short ignored handoff under `.work/handoffs/` with the actual repository/worktree, branch and commit, changed paths, checks and results, unresolved work, and next action. Resume without repeating completed work; keep temporary status out of this guide and never force-add private notes.
