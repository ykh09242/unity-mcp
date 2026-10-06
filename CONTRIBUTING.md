# Contributing to Unity MCP (ykh09242)

This Git-only fork is maintained by ykh09242. Contributions are welcome: bug fixes, tools, docs improvements, and tests. Original upstream authorship and MIT copyright are retained.

## Quick Start

1. **Fork** this repo and **clone** your fork.
2. Branch off `beta` (not `main`):
   ```bash
   git switch -c feat/your-idea origin/beta
   ```
3. Use the remote that actually points to the fork's `beta` branch (the example assumes `origin`). Prepare only the environment needed for your change: [Dev Setup](website/docs/contributing/dev-setup.md).
4. Make your change with tests.
5. Open a PR against `beta`. PRs against `main` will be redirected.

## What We Look For

- **Tests for new behavior.** Python tests live in `Server/tests/`; Unity EditMode tests live in `TestProjects/UnityMCPTests/Assets/Tests/`.
- **Domain symmetry.** New tools live in *both* `Server/src/services/tools/manage_<domain>.py` (Python MCP tool) and `MCPForUnity/Editor/Tools/Manage<Domain>.cs` (C# implementation). See [Adding a New Tool](https://github.com/ykh09242/unity-mcp/blob/beta/website/docs/contributing/dev-setup.md).
- **Minimal abstraction.** Three similar lines of code is better than a helper that's only used once.
- **Documentation as code.** Tool reference pages under `website/docs/reference/` are auto-generated — never hand-edit them outside the `<!-- examples:start --><!-- examples:end -->` blocks.

## Before You Push

Use checks proportional to the change. For Python behavior, run focused tests from `Server/` with warnings as errors; for tooling, run the relevant `tools/tests` modules from the repository root. For Unity API/shim changes, use the affected version compile checks and state separately whether licensed EditMode/PlayMode execution ran. See [Testing](website/docs/contributing/testing.md).

For Python changes, also run the repository-wide correctness check from the repository root:

```bash
uv run --directory Server --locked --extra dev python ../tools/lint_python.py
```

Markdown-only changes need link, example/schema and manifest-pin checks, not a Unity/server launch. Site layout changes also need rendered desktop/mobile verification. Generated references must be refreshed through `tools/generate_docs_reference.py`, not hand-edited; optional hooks live in `tools/install-hooks.sh`. Return to the repository root before running root-level tools.

## Pull Request Checklist

- [ ] Branched off `beta`
- [ ] New or updated tests
- [ ] Docs updated (the auto-gen handles the tool reference; narrative docs under `website/docs/` are hand-written)
- [ ] No commented-out code, no `// removed for X` markers, no `_unused` renames
- [ ] PR description explains the **why**, exact checks, and any unverified runtime/platform claims

## Code Style

- **Python:** type hints required; follow the patterns in existing `manage_*.py` files.
- **C#:** match existing namespace conventions under `MCPForUnity.Editor.*`; route Unity API differences through `MCPForUnity/Runtime/Helpers/Unity*Compat.cs` shims rather than `#if UNITY_*_OR_NEWER` blocks.
- **Markdown:** wrap at sensible widths; use sentence case in headings.

## Areas That Need Help

- Examples in tool reference pages (`website/docs/reference/tools/**/*.md` — add inside the `<!-- examples:start --><!-- examples:end -->` blocks).
- Net-new guide content (multi-instance routing, tool groups, transport modes).
- Translations beyond Chinese.
- Cross-platform shell testing for the CLI.

## Reporting Bugs / Requesting Features

Use the issue templates under [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/). For security concerns, see [SECURITY.md](SECURITY.md) — do **not** open a public issue.

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Be excellent to each other.

## Questions?

- [GitHub Issues](https://github.com/ykh09242/unity-mcp/issues) — bugs, features
- Use fork issues for questions and design ideas; no fork Discord or Discussions service is advertised.
