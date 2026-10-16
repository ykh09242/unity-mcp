"""Agent-facing prose must address resources by URI, never by bare name.

A resource's name and its URI are deliberately different (`editor_state` vs
`mcpforunity://editor/state`), and the URI scheme is not derivable from the
name. Any instruction, description or tool-result hint that mentions a resource
by name alone sends the reader to build `mcpforunity://<name>`, which 404s.
"""

import os
import re
import typing
from pathlib import Path

import pytest

import services.resources as resources_pkg
import services.tools as tools_pkg
from services.registry import get_registered_resources, get_registered_tools
from services.registry import TOOL_GROUPS
from utils.module_discovery import discover_modules

REPO_ROOT = Path(__file__).resolve().parents[2]
UNITY_EDITOR_DIR = REPO_ROOT / "MCPForUnity" / "Editor"

# C# string literal, honouring backslash escapes.
CSHARP_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


@pytest.fixture(scope="module")
def build_instructions():
    """Import `main` without leaking the env vars it sets at import time."""
    before = dict(os.environ)
    from main import _build_instructions

    os.environ.clear()
    os.environ.update(before)
    return _build_instructions


@pytest.fixture(scope="module")
def resource_uris_by_name() -> dict[str, str]:
    """Registered resources whose name is an unambiguous snake_case identifier.

    Single-word names (`tests`, `cameras`, `volumes`) are ordinary English and
    would match prose that is not referring to the resource at all.
    """
    list(discover_modules(Path(resources_pkg.__file__).parent, resources_pkg.__package__))
    list(discover_modules(Path(tools_pkg.__file__).parent, tools_pkg.__package__))
    registered = get_registered_resources()
    assert registered, "no resources registered — discovery failed"
    return {r["name"]: r["uri"] for r in registered if "_" in r["name"]}


def _offenders(
    text: str | None,
    where: str,
    uris_by_name: dict[str, str],
    *,
    tool_names: set[str] | None = None,
    tool_document: str | None = None,
) -> list[str]:
    """Require resource URIs while retaining explicit references to same-name tools."""
    if not text:
        return []
    if tool_names is None:
        tool_names = {tool["name"] for tool in get_registered_tools()}
    groups = "|".join(re.escape(group) for group in TOOL_GROUPS)
    found = []
    for line in str(text).splitlines():
        for name, uri in uris_by_name.items():
            if uri in line:
                continue
            tool_spans = []
            if name in tool_names:
                # Tool catalogs link the actual registered tool page; the rest of
                # the line is still checked for an ambiguous resource instruction.
                tool_spans = [
                    match.span()
                    for match in re.finditer(
                        rf"\[`?{re.escape(name)}`?\]\(\./(?:(?:{groups})/)?{re.escape(name)}\.md\)",
                        line,
                    )
                ]
                if tool_document == name:
                    if line.strip() in {f"title: {name}", f"sidebar_label: {name}", f"# `{name}`"}:
                        continue
                    tool_spans += [
                        match.span()
                        for match in re.finditer(rf"`services\.tools\.{re.escape(name)}`", line)
                    ]
            for match in re.finditer(rf"\b{re.escape(name)}\b", line):
                if any(start <= match.start() and match.end() <= end for start, end in tool_spans):
                    continue
                if name in tool_names and (
                    re.search(r"\btool\s+`?$", line[: match.start()])
                    or re.match(r"`?\s+tool\b", line[match.end() :])
                ):
                    continue
                found.append(f"{where}: '{name}' without '{uri}' in: {line.strip()}")
                break
    return found


@pytest.mark.parametrize("project_scoped_tools", [True, False])
def test_server_instructions_reference_resources_by_uri(
    project_scoped_tools: bool, build_instructions, resource_uris_by_name: dict[str, str]
):
    offenders = _offenders(
        build_instructions(project_scoped_tools),
        f"instructions(project_scoped_tools={project_scoped_tools})",
        resource_uris_by_name,
    )
    assert not offenders, "\n".join(offenders)


def test_resource_descriptions_reference_resources_by_uri(resource_uris_by_name: dict[str, str]):
    offenders = []
    for resource in get_registered_resources():
        offenders += _offenders(
            resource.get("description"),
            f"resource '{resource['name']}' description",
            resource_uris_by_name,
        )
    assert not offenders, "\n".join(offenders)


def test_tool_descriptions_reference_resources_by_uri(resource_uris_by_name: dict[str, str]):
    list(discover_modules(Path(tools_pkg.__file__).parent, tools_pkg.__package__))
    registered = get_registered_tools()
    assert registered, "no tools registered — discovery failed"

    offenders = []
    for tool in registered:
        name = tool["name"]
        func = tool["func"]
        offenders += _offenders(
            tool.get("kwargs", {}).get("description"),
            f"tool '{name}' description",
            resource_uris_by_name,
        )
        offenders += _offenders(func.__doc__, f"tool '{name}' docstring", resource_uris_by_name)

        hints = typing.get_type_hints(func, include_extras=True)
        for param, hint in hints.items():
            for meta in getattr(hint, "__metadata__", ()):
                if isinstance(meta, str):
                    offenders += _offenders(
                        meta, f"tool '{name}' parameter '{param}'", resource_uris_by_name
                    )

    assert not offenders, "\n".join(offenders)


def test_unity_tool_result_strings_reference_resources_by_uri(
    resource_uris_by_name: dict[str, str],
):
    """Hints Unity returns in tool payloads are read by the same agents.

    Only multi-word literals are checked: a bare token such as "get_tests" is a
    command name, not prose telling the reader to go read a resource.
    """
    assert UNITY_EDITOR_DIR.is_dir(), f"{UNITY_EDITOR_DIR} not found"

    offenders = []
    for source in sorted(UNITY_EDITOR_DIR.rglob("*.cs")):
        text = source.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            for match in CSHARP_STRING.finditer(line):
                literal = match.group(1)
                if " " not in literal.strip():
                    continue
                offenders += _offenders(
                    literal,
                    f"{source.relative_to(REPO_ROOT).as_posix()}:{line_no}",
                    resource_uris_by_name,
                )
    assert not offenders, "\n".join(offenders)


def test_agent_facing_markdown_references_resources_by_uri(resource_uris_by_name: dict[str, str]):
    """Docs that tell a reader to go read a resource must give its URI.

    Scoped to the surfaces that issue that instruction: the skill agents load,
    and the per-tool reference pages. Excluded deliberately —
    `website/docs/reference/resources/` is a generated catalog that puts each
    name in a heading and its URI on the following line, and the guides and
    getting-started pages name resources as subjects of a sentence rather than
    telling anyone to construct a URI.
    """
    targets = [REPO_ROOT / "unity-mcp-skill" / "SKILL.md"]
    targets += sorted((REPO_ROOT / "website" / "docs" / "reference" / "tools").rglob("*.md"))
    assert len(targets) > 1, "expected the skill and the tool reference pages"

    offenders = []
    for doc in targets:
        assert doc.is_file(), f"{doc} not found"
        text = doc.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            offenders += _offenders(
                line,
                f"{doc.relative_to(REPO_ROOT).as_posix()}:{line_no}",
                resource_uris_by_name,
                tool_document=doc.stem,
            )
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "text", ["Use the get_tests tool for further pages.", "Call tool `get_tests`."]
)
def test_same_name_registered_tool_mentions_do_not_require_resource_uri(text):
    assert not _offenders(
        text, "test", {"get_tests": "mcpforunity://tests"}, tool_names={"get_tests"}
    )


@pytest.mark.parametrize(
    "text",
    [
        "Read the get_tests resource.",
        "Read the [get_tests](./resources/get_tests.md) resource.",
        "Use the get_tests tool, then read the get_tests resource.",
        "- **[`get_tests`](./testing/get_tests.md)** — Read the get_tests resource.",
    ],
)
def test_same_name_tool_does_not_hide_bad_resource_references(text):
    assert _offenders(text, "test", {"get_tests": "mcpforunity://tests"}, tool_names={"get_tests"})


def test_only_registered_tool_page_metadata_and_links_are_exempt():
    mapping = {"get_tests": "mcpforunity://tests"}
    for text in (
        "title: get_tests",
        "sidebar_label: get_tests",
        "# `get_tests`",
        "**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.get_tests`",
        "- **[`get_tests`](./testing/get_tests.md)** — Lists Unity tests.",
    ):
        assert not _offenders(
            text, "tool doc", mapping, tool_names={"get_tests"}, tool_document="get_tests"
        )
        assert _offenders(
            text, "unregistered doc", mapping, tool_names=set(), tool_document="get_tests"
        )
    assert _offenders("# `get_tests`", "resource prose", mapping, tool_names={"get_tests"})
