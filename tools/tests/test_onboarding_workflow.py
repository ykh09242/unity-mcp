"""Docs-only edits must select semantic onboarding checks, including failure controls."""

from fnmatch import fnmatchcase
from pathlib import Path
import shlex
from urllib.parse import unquote, urlsplit

import pytest
import yaml

from tools.tests import test_fork_documentation as contracts


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/onboarding-contracts.yml").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("event", ["pull_request", "push"])
def test_all_authored_contract_inputs_select_the_focused_gate(workflow, event):
    trigger = workflow.get("on", workflow.get(True))[event]
    assert trigger["branches"] == (["main", "beta"] if event == "pull_request" else ["beta"])
    inputs = [
        *contracts.PAGES,
        "website/docs/another-route.md",
        "MCPForUnity/package.json",
        "Server/src/cli/main.py",
        "Server/src/services/resources/project_info.py",
        "Server/pyproject.toml",
        "Server/uv.lock",
        "tools/cli_reference.py",
        "tools/cli_example_validation.py",
        "tools/tests/test_fork_documentation.py",
    ]
    for path in inputs:
        assert any(fnmatchcase(path, pattern) for pattern in trigger["paths"]), path
    assert not any(fnmatchcase("MCPForUnity/Editor/Tool.cs", p) for p in trigger["paths"])


def test_focused_gate_executes_existing_contracts_without_site_or_unity_setup(workflow):
    job = workflow["jobs"]["contracts"]
    assert "needs" not in job and "if" not in job and not job.get("continue-on-error")
    commands = [shlex.split(step["run"]) for step in job["steps"] if "run" in step]
    assert commands[0] == ["uv", "sync", "--directory", "Server", "--locked", "--extra", "dev"]
    check = commands[1]
    assert check[:8] == ["uv", "run", "--project", "Server", "--locked", "--extra", "dev", "python"]
    assert check[8:11] == ["-m", "pytest", "tools/tests/test_fork_documentation.py"]
    assert "-W" in check and check[check.index("-W") + 1] == "error"
    assert len(commands) == 2


@pytest.mark.parametrize("event", ["pull_request", "push"])
def test_local_link_target_changes_select_the_contract_even_without_page_changes(workflow, event):
    """Derive the same local target closure as the maintained-page link assertions."""
    patterns = workflow.get("on", workflow.get(True))[event]["paths"]
    targets = set()
    for relative in contracts.PAGES:
        page = ROOT / relative
        text = contracts.FENCES.sub("", contracts._read(page))
        for raw in contracts.LINKS.findall(text):
            address = urlsplit(raw)
            if address.scheme or not address.path or address.path.startswith("/"):
                continue
            target = (page.parent / unquote(address.path)).resolve()
            if not target.suffix and not target.exists():
                target = target.with_suffix(".md")
            # Git reports directory removal through its deleted child paths.
            if target.is_dir():
                targets.update(path for path in target.iterdir() if path.is_file())
            else:
                targets.add(target)
    assert targets
    for target in targets:
        relative = target.relative_to(ROOT).as_posix()
        assert any(fnmatchcase(relative, pattern) for pattern in patterns), relative


@pytest.mark.parametrize("event", ["pull_request", "push"])
@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_removed_linked_target_selects_and_fails_existing_link_contract(
    workflow, tmp_path, monkeypatch, event, operation
):
    """Keep the referring page unchanged while breaking only its local target."""
    page = tmp_path / "SECURITY.md"
    page.write_bytes(b"[Resource limits](docs/development/resource-limits.md)\n")
    target = tmp_path / "docs/development/resource-limits.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"# Resource limits\n")
    monkeypatch.setattr(contracts, "ROOT", tmp_path)
    contracts.test_onboarding_links_have_local_targets("SECURITY.md", frozenset())
    if operation == "rename":
        target.rename(target.with_name("renamed-limits.md"))
    else:
        target.unlink()
    patterns = workflow.get("on", workflow.get(True))[event]["paths"]
    assert any(fnmatchcase(target.relative_to(tmp_path).as_posix(), p) for p in patterns)
    with pytest.raises(AssertionError, match="resource-limits"):
        contracts.test_onboarding_links_have_local_targets("SECURITY.md", frozenset())


@pytest.mark.parametrize("fault", ["source", "header", "upm"])
def test_actual_onboarding_contracts_reject_malformed_documentation(monkeypatch, fault):
    """Exercise the existing assertions against altered authored input, without writing docs."""
    read = contracts._read
    clients = contracts.SITE / "getting-started/clients.md"
    install = contracts.SITE / "getting-started/install.md"
    package = contracts.json.loads(read(ROOT / "MCPForUnity/package.json"))

    def malformed(path):
        text = read(path)
        if path == clients:
            if fault == "source":
                assert package["mcpServerSource"] in text
                return text.replace(package["mcpServerSource"], "git+https://example.invalid/wrong")
            if fault == "header":
                assert "X-Unity-MCP-Token" in text
                return text.replace("X-Unity-MCP-Token", "Wrong-Authentication-Header")
        if path == install and fault == "upm":
            assert "path=/MCPForUnity" in text
            return text.replace("path=/MCPForUnity", "path=/WrongPackage")
        return text

    monkeypatch.setattr(contracts, "_read", malformed)
    with pytest.raises(AssertionError):
        if fault == "upm":
            contracts.test_upm_install_examples_select_the_fork_subdirectory_and_release()
        else:
            contracts.test_pinned_client_and_launch_examples_match_package_source()
