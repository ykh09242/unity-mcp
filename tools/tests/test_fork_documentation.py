"""Validate maintained onboarding examples against local metadata and schemas."""

import ast
import json
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import unquote, urlsplit

import pytest

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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", PAGES)
def test_onboarding_links_have_local_targets(relative: str) -> None:
    # Given a maintained page and the site's declared routes.
    page = ROOT / relative
    routes: set[str] = set()
    for target in SITE.rglob("*.md"):
        explicit = re.search(r"^slug:\s*(\S+)", _read(target), re.MULTILINE)
        default = "/" + target.relative_to(SITE).with_suffix("").as_posix()
        routes.add(explicit.group(1).rstrip("/") if explicit else default)
    text = FENCES.sub("", _read(page))

    # When resolving authored Markdown links (not fenced examples or remote sites).
    for raw in LINKS.findall(text):
        address = urlsplit(raw)
        if address.netloc == "ykh09242.github.io":
            assert address.path.startswith("/unity-mcp/"), raw
            route = address.path.removeprefix("/unity-mcp").rstrip("/")
            assert not route or route in routes, (relative, raw)
        elif not address.scheme and address.path.startswith("/"):
            assert address.path.rstrip("/") in routes, (relative, raw)
        elif not address.scheme and address.path:
            target = (page.parent / unquote(address.path)).resolve()
            if not target.suffix and not target.exists():
                target = target.with_suffix(".md")
            # Then each relative link points to an existing file/directory.
            assert target.exists(), (relative, raw)


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
    # Given current package identity/version and the canonical install page.
    package = json.loads(_read(ROOT / "MCPForUnity/package.json"))
    urls = re.findall(
        r"https://github\.com/ykh09242/unity-mcp\.git[^\s`]+",
        _read(SITE / "getting-started/install.md"),
    )

    # When parsing tag and commit-addressed install URLs.
    assert len(urls) == 2
    for raw in urls:
        parsed = urlsplit(raw)
        # Then both select the package subdirectory, with stable tag or full SHA.
        assert parsed.query == "path=/MCPForUnity"
        assert parsed.fragment == f"ykh09242-v{package['version']}" or re.fullmatch(
            r"[0-9a-f]{40}", parsed.fragment
        )


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
