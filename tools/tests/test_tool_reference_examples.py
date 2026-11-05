"""High-impact authored examples follow the advertised MCP inputs and survive generation."""

import json
from pathlib import Path
import re
import sys

import pytest
from jsonschema import validate

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import generate_docs_reference as docs  # noqa: E402
from public_contracts import capture_public_contracts  # noqa: E402

EXAMPLE_PAGES = (
    ("core", "manage_scene_edits", 4),
    ("prefabs", "prefab_create", 1),
    ("prefabs", "prefab_contents", 2),
    ("legacy", "manage_scene", 7),
)


@pytest.fixture(scope="module")
def public_contracts():
    return capture_public_contracts()


@pytest.mark.parametrize("group,name,count", EXAMPLE_PAGES)
def test_editing_examples_validate_against_observed_sdk_schema(
    public_contracts, group, name, count
):
    path = docs.TOOLS_OUT / group / f"{name}.md"
    examples = docs._read_existing_examples(path)
    assert "../../../guides/scene-and-prefab-edits.md" in examples
    assert (path.parent / "../../../guides/scene-and-prefab-edits.md").resolve().is_file()
    blocks = re.findall(r"^```json\n(.*?)^```", examples, re.MULTILINE | re.DOTALL)
    assert len(blocks) == count
    for protocol in public_contracts["protocols"].values():
        schema = protocol["tools"][name]["inputSchema"]
        for block in blocks:
            validate(instance=json.loads(block), schema=schema)


def test_editing_examples_and_guide_links_survive_regeneration(tmp_path):
    tools_root = tmp_path / "tools"
    docs.generate(tools_root, tmp_path / "resources", examples_source=docs.TOOLS_OUT)
    for group, name, _count in EXAMPLE_PAGES:
        relative = Path(group) / f"{name}.md"
        original = docs._read_existing_examples(docs.TOOLS_OUT / relative)
        generated = docs._read_existing_examples(tools_root / relative)
        assert "No examples yet" not in original
        assert generated == original
