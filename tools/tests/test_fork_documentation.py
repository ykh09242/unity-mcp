"""Validate maintained onboarding examples against local metadata and schemas."""

import ast
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shlex
import sys
import unicodedata
from urllib.parse import unquote, urlsplit

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from cli_reference import prepare_cli_reference


ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "website/docs"
PAGES = (
    "README.md",
    "Server/README.md",
    "MCPForUnity/README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "website/README.md",
    "website/docs/getting-started/install.md",
    "website/docs/getting-started/clients.md",
    "website/docs/getting-started/migrate.md",
    "website/docs/getting-started/first-prompt.md",
    "website/docs/getting-started/index.md",
    "website/docs/guides/security.md",
    "website/docs/guides/multi-instance.md",
    "website/docs/guides/tool-groups.md",
    "website/docs/guides/troubleshooting.md",
    "website/docs/guides/cli.md",
    "website/docs/guides/cli-examples.md",
    "website/docs/reference/cli.md",
    "website/docs/architecture/transports.md",
    "website/docs/architecture/instance-routing.md",
    "website/docs/contributing/dev-setup.md",
    "website/docs/contributing/testing.md",
)
FENCES = re.compile(r"^```(\w+)\s*\n(.*?)^```", re.MULTILINE | re.DOTALL)
LINKS = re.compile(r"\[[^\]\n]+\]\(([^\s)]+)\)")
MARKDOWN = MarkdownIt("commonmark")
EXPLICIT_HEADING_ID = re.compile(
    r"\s*(?:\{#([^\s}]+)\}|<!--\s*#([^\s]+)\s*-->|\{/\*\s*#([^\s]+)\s*\*/\})\s*$"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def site_routes() -> dict[str, Path]:
    """Read the unchanged documentation route inventory once for all maintained pages."""
    routes: dict[str, Path] = {}
    for target in SITE.rglob("*.md"):
        explicit = re.search(r"^slug:\s*(\S+)", _read(target), re.MULTILINE)
        default = "/" + target.relative_to(SITE).with_suffix("").as_posix()
        routes[explicit.group(1).rstrip("/") if explicit else default] = target
    return routes


def _markdown_tokens(text: str) -> list[Token]:
    """Exclude YAML front matter; CommonMark handles fences, code and inline syntax."""
    return MARKDOWN.parse(
        re.sub(r"\A---\r?\n.*?\r?\n---(?:\r?\n|$)", "", text, count=1, flags=re.DOTALL)
    )


def _inline_text(tokens: list[Token]) -> str:
    return "".join(
        _inline_text(token.children) if token.children else token.content
        for token in tokens
        if token.type in {"text", "code_inline", "image"}
    )


class _HtmlIds(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.ids.update(
            value
            for key, value in attrs
            if value is not None and (key == "id" or (tag == "a" and key == "name"))
        )


def _markdown_anchors(text: str) -> set[str]:
    """Recognize CommonMark ATX/setext headings and HTML IDs, plus Docusaurus IDs.

    Heading text uses parsed inline labels/code and Unicode letters/marks/numbers;
    punctuation/symbols are stripped except hyphens/underscores, spaces become hyphens.
    Repeated generated IDs receive GitHub-style collision suffixes. This focused
    contract covers maintained Markdown, not JSX-rendered or dynamically created IDs.
    """
    anchors: set[str] = set()
    generated: set[str] = set()
    html_ids = _HtmlIds()
    tokens = _markdown_tokens(text)
    for index, token in enumerate(tokens):
        if token.type == "html_block":
            html_ids.feed(token.content)
        for child in token.children or []:
            if child.type == "html_inline":
                html_ids.feed(child.content)
        if token.type != "inline" or index == 0 or tokens[index - 1].type != "heading_open":
            continue
        explicit = EXPLICIT_HEADING_ID.search(token.content)
        if explicit:
            anchors.add(next(value for value in explicit.groups() if value is not None))
            continue
        title = _inline_text(token.children or []).lower()
        base = "".join(
            char for char in title if char in " _-" or unicodedata.category(char)[0] in "LMN"
        ).replace(" ", "-")
        slug = base
        suffix = 0
        while slug in generated:
            suffix += 1
            slug = f"{base}-{suffix}"
        generated.add(slug)
        anchors.add(slug)
    return anchors | html_ids.ids


@pytest.mark.parametrize("relative", PAGES)
def test_onboarding_links_have_local_targets(relative: str, site_routes: dict[str, Path]) -> None:
    # Given a maintained page and the site's declared routes.
    page = ROOT / relative
    tokens = _markdown_tokens(_read(page))
    links = [
        str(child.attrGet("href"))
        for token in tokens
        for child in token.children or []
        if child.type == "link_open"
    ]
    anchors: dict[Path, set[str]] = {}

    # When resolving authored Markdown links (not fenced examples or remote sites).
    for raw in links:
        address = urlsplit(raw)
        if address.netloc and address.netloc != "ykh09242.github.io":
            continue
        target = None
        if address.netloc == "ykh09242.github.io":
            assert address.path.startswith("/unity-mcp/"), raw
            route = address.path.removeprefix("/unity-mcp").rstrip("/")
            assert not route or route in site_routes, (relative, raw)
            target = site_routes.get(route)
        elif not address.scheme and address.path.startswith("/"):
            assert address.path.rstrip("/") in site_routes, (relative, raw)
            target = site_routes[address.path.rstrip("/")]
        elif not address.scheme and address.path:
            target = (page.parent / unquote(address.path)).resolve()
            if not target.suffix and not target.exists():
                target = target.with_suffix(".md")
            # Then each relative link points to an existing file/directory.
            assert target.exists(), (relative, raw)
        elif not address.scheme and not address.netloc:
            target = page
        if address.fragment and (
            target is not None or not address.scheme or address.netloc == "ykh09242.github.io"
        ):
            assert target is not None and target.is_file(), (relative, raw, "no anchor target")
            if target not in anchors:
                anchors[target] = _markdown_anchors(_read(target))
            assert unquote(address.fragment) in anchors[target], (relative, raw, "missing anchor")


def test_pinned_client_and_launch_examples_match_package_source() -> None:
    # Given the installed-package metadata and authored configuration examples.
    package = json.loads(_read(ROOT / "MCPForUnity/package.json"))
    expected = package["mcpServerSource"]
    clients = _read(SITE / "getting-started/clients.md")
    blocks = [json.loads(body) for language, body in FENCES.findall(clients) if language == "json"]

    # When interpreting both HTTP config formats and the stdio launch config.
    assert len(blocks) == 3
    for block in blocks[:2]:
        entries = block.get("mcpServers", block.get("servers"))
        connection = entries["unityMCP"]
        # Then local HTTP includes the mandatory header and actual MCP endpoint.
        assert connection["url"] == "http://localhost:8080/mcp"
        assert set(connection["headers"]) == {"X-Unity-MCP-Token"}
    launch = blocks[2]["mcpServers"]["unityMCP"]
    assert launch["args"] == [
        "--python",
        ">=3.11",
        "--from",
        expected,
        "mcp-for-unity",
        "--transport",
        "stdio",
    ]
    for relative in ("Server/README.md", "website/docs/guides/cli.md"):
        sources = []
        for language, body in FENCES.findall(_read(ROOT / relative)):
            if language != "bash":
                continue
            for line in body.splitlines():
                words = shlex.split(line)
                if words and words[0] == "uvx" and "--from" in words:
                    assert words[words.index("--python") + 1] == ">=3.11", (relative, line)
                    sources.append(words[words.index("--from") + 1])
        assert sources, relative
        assert all(source == expected for source in sources), (relative, sources)


def test_upm_install_examples_select_the_fork_subdirectory_and_release() -> None:
    package = json.loads(_read(ROOT / "MCPForUnity/package.json"))
    stable = f"ykh09242-v{package['version']}"
    for page in (
        SITE / "getting-started/install.md",
        ROOT / "README.md",
        ROOT / "docs/i18n/README-zh.md",
    ):
        urls = [
            urlsplit(raw)
            for raw in re.findall(
                r"https://github\.com/ykh09242/unity-mcp\.git[^\s`]+", _read(page)
            )
        ]
        assert any(url.fragment == stable for url in urls), (page, stable)
        assert all(
            url.query == "path=/MCPForUnity"
            and (url.fragment == stable or re.fullmatch(r"[0-9a-f]{40}", url.fragment))
            for url in urls
        ), page


def test_server_readme_uses_reviewed_fastmcp_and_mcp_versions() -> None:
    packages = json.loads(_read(ROOT / "Server/src/core/sdk_compatibility.json"))["packages"]
    readme = _read(ROOT / "Server/README.md").replace("**", "")
    for name, label in (("fastmcp", "FastMCP"), ("mcp", "MCP SDK and types")):
        versions = re.findall(rf"\b{label} (\d+\.\d+\.\d+)\b", readme)
        assert set(versions) == {packages[name]}


def test_cli_examples_name_registered_commands_and_options() -> None:
    # Actual subordinate Click options, required arguments and scalar types are
    # parsed in an isolated child. Root/group/leaf/parameter callbacks never run.
    reference = prepare_cli_reference(ROOT)
    assert reference.example_count > 0


def test_authored_cli_inventory_matches_registered_groups() -> None:
    # Given the actual structured group-registration list.
    source = ast.parse(_read(ROOT / "Server/src/cli/main.py"))
    registration = next(
        node.value
        for node in ast.walk(source)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "optional_commands"
            for target in node.targets
        )
    )
    registered = {command for _, command in ast.literal_eval(registration)}

    # When reading only the authored inventory, not flags or example payloads.
    reference = _read(SITE / "reference/cli.md")
    inventory = reference.split("## Command groups\n", 1)[1].split("CLI verbs/flags", 1)[0]

    # Then documented groups are complete and do not advertise nonexistent names.
    assert set(re.findall(r"`([a-z_]+)`", inventory)) == registered


def test_resource_target_example_uses_registered_uri_and_metadata_shape() -> None:
    # Given the project resource's current decorator and the routing guide.
    resource = ast.parse(_read(ROOT / "Server/src/services/resources/project_info.py"))
    uris = {
        keyword.value.value
        for node in ast.walk(resource)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "uri" and isinstance(keyword.value, ast.Constant)
    }
    blocks = [
        json.loads(body)
        for language, body in FENCES.findall(_read(SITE / "guides/multi-instance.md"))
        if language == "json"
    ]

    # When decoding the protocol request example, not executing a resource read.
    request = next(block for block in blocks if "uri" in block)

    # Then it names a registered resource and puts targeting in reserved metadata.
    assert request["uri"] in uris
    assert set(request) == {"uri", "_meta"}
    assert set(request["_meta"]) == {"unity_instance"}
    assert request["_meta"]["unity_instance"]


@pytest.mark.parametrize(
    "link",
    [
        "#removed",
        "target.md#removed",
        "/declared-route#removed",
        "https://ykh09242.github.io/unity-mcp/declared-route#removed",
        "#example-only",
        "#duplicate-2",
    ],
)
def test_link_contract_rejects_missing_or_deleted_heading(tmp_path, monkeypatch, link):
    page = tmp_path / "README.md"
    target = tmp_path / "target.md"
    headings = "# Present\n## Duplicate\n## Duplicate\n```md\n# Example only\n```\n"
    page.write_text(f"[Link]({link})\n\n{headings}", encoding="utf-8")
    target.write_text(headings, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="removed|example-only|duplicate-2"):
        test_onboarding_links_have_local_targets("README.md", {"/declared-route": target})


@pytest.mark.parametrize(
    "fragment",
    [
        "present",
        "duplicate",
        "duplicate-1",
        "duplicate-1-1",
        "explicit-ID",
        "comment-id",
        "mdx-id",
        "use-code-and-label--unicode-한글-café",
        "setext-heading",
        "html-anchor",
    ],
)
@pytest.mark.parametrize(
    "prefix",
    ["", "target.md", "/declared-route", "https://ykh09242.github.io/unity-mcp/declared-route"],
)
def test_link_contract_accepts_heading_and_explicit_ids(tmp_path, monkeypatch, fragment, prefix):
    headings = (
        "# Present\n## Duplicate\n## Duplicate\n## Duplicate-1\n"
        "## Custom {#explicit-ID}\n## Comment <!-- #comment-id -->\n"
        "## MDX {/* #mdx-id */}\n"
        "## Use `code` and [label](https://example.com) & Unicode 한글 café!\n"
        'Setext heading\n---\n<a id="html-anchor"></a>\n'
    )
    page = tmp_path / "README.md"
    target = tmp_path / "target.md"
    page.write_text(f"[Link]({prefix}#{fragment})\n\n{headings}", encoding="utf-8")
    target.write_text(headings, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    test_onboarding_links_have_local_targets("README.md", {"/declared-route": target})


def test_link_contract_ignores_fenced_inline_code_and_external_examples(tmp_path, monkeypatch):
    page = tmp_path / "README.md"
    page.write_text(
        "```md\n[Example](absent.md#missing)\n```\n"
        "~~~\n[Example](absent.md#missing)\n~~~\n"
        "`[Code](absent.md#missing)`\n[Remote](https://example.com/#missing)\n"
        "[Remote](//example.com/#missing)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    test_onboarding_links_have_local_targets("README.md", {})


def test_deleting_target_heading_breaks_unchanged_relative_link(tmp_path, monkeypatch):
    page = tmp_path / "README.md"
    target = tmp_path / "target page.md"
    page.write_text("[Stable link](target%20page.md#retained)\n", encoding="utf-8")
    target.write_text("# Retained\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    test_onboarding_links_have_local_targets("README.md", {})
    target.write_text("# Renamed\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="retained"):
        test_onboarding_links_have_local_targets("README.md", {})
