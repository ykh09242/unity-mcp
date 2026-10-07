"""Structural editor UI checks; not a substitute for Unity import/render tests."""

from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
WINDOWS = ROOT / "MCPForUnity/Editor/Windows"
UXML_FILES = sorted(WINDOWS.rglob("*.uxml"))
USS_FILES = sorted(WINDOWS.rglob("*.uss"))


@pytest.mark.parametrize("path", UXML_FILES, ids=lambda p: p.relative_to(WINDOWS).as_posix())
def test_editor_markup_has_complete_named_controls_and_external_styles(path: Path) -> None:
    root = ET.parse(path).getroot()
    elements = list(root.iter())
    names = [element.attrib["name"] for element in elements if "name" in element.attrib]
    assert len(names) == len(set(names)), path
    assert all("style" not in element.attrib for element in elements), path
    children = [element for element in root if element.tag.rsplit("}", 1)[-1] != "Style"]
    assert len(children) == 1, path
    for element in root:
        if element.tag.rsplit("}", 1)[-1] == "Style":
            stylesheet = (path.parent / element.attrib["src"]).resolve()
            assert stylesheet.is_relative_to(WINDOWS)
            assert stylesheet.is_file(), stylesheet


@pytest.mark.parametrize("path", USS_FILES, ids=lambda p: p.relative_to(WINDOWS).as_posix())
def test_styles_avoid_browser_only_layout_properties(path: Path) -> None:
    source = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    assert source.count("{") == source.count("}"), path
    properties = re.findall(r"(?:^|[;{])\s*([\w-]+)\s*:", source)
    assert not set(properties).intersection(
        {"gap", "z-index", "box-shadow", "filter", "border", "outline", "pointer-events"}
    )
    assert not re.search(r":(?:first-child|last-child|nth-child)\b", source)
    assert all(
        value.strip() == "0" for value in re.findall(r"letter-spacing\s*:\s*([^;]+)", source)
    )


def test_all_theme_tokens_resolve_in_both_editor_skins() -> None:
    common = (WINDOWS / "Components/Common.uss").read_text(encoding="utf-8")
    blocks = re.findall(r"([^{}]+)\{([^{}]*)\}", common)
    defaults = next(body for selector, body in blocks if selector.strip() == ".mcp-editor")
    light = next(body for selector, body in blocks if ".mcp-editor.unity-theme-light" in selector)
    defined = set(re.findall(r"(--mcp-[\w-]+)\s*:", defaults))
    assert defined == set(re.findall(r"(--mcp-[\w-]+)\s*:", light))
    for path in USS_FILES:
        referenced = set(re.findall(r"var\((--mcp-[\w-]+)\)", path.read_text(encoding="utf-8")))
        assert referenced <= defined, (path, referenced - defined)


def _common_style(selector: str) -> dict[str, str]:
    source = (WINDOWS / "Components/Common.uss").read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    properties = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
        if selector in {part.strip() for part in selectors.split(",")}:
            properties.update(
                (name.strip(), value.strip())
                for name, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", body)
            )
    return properties


def test_column_dropdown_does_not_use_horizontal_flex_basis_as_height() -> None:
    markup = ET.parse(WINDOWS / "Components/Validation/McpValidationSection.uxml")
    column = next(element for element in markup.iter() if element.get("class") == "setting-column")
    field = next(element for element in column if element.get("name") == "validation-level")
    assert field.get("class") == "setting-dropdown"
    style = _common_style(".setting-dropdown")
    assert style.get("flex-basis", "auto") == "auto"
    assert style.get("flex-grow", "0") == "0"
    assert style.get("height", "auto") == "auto"


def test_inline_dropdown_retains_horizontal_growth_and_wrapping() -> None:
    style = _common_style(".setting-dropdown-inline")
    assert style["flex-grow"] == "1"
    assert style["flex-shrink"] == "1"
    assert style["flex-basis"] == "200px"
    assert _common_style(".setting-row")["flex-wrap"] == "wrap"


@pytest.mark.parametrize("selector", [".mcp-editor", ".mcp-editor.unity-theme-light"])
@pytest.mark.parametrize(
    ("token", "native"),
    [
        ("--mcp-bg", "--unity-colors-window-background"),
        ("--mcp-surface", "--unity-colors-toolbar-background"),
        ("--mcp-field", "--unity-colors-input_field-background"),
        ("--mcp-text", "--unity-colors-default-text"),
    ],
)
def test_editor_surfaces_follow_native_unity_theme(selector: str, token: str, native: str) -> None:
    assert _common_style(selector)[token].startswith(f"var({native},")


@pytest.mark.parametrize(
    "relative",
    [
        "MCPForUnityEditorWindow",
        "MCPSetupWindow",
        "EditorPrefs/EditorPrefsWindow",
        "Components/Connection/McpConnectionSection",
        "Components/ClientConfig/McpClientConfigSection",
        "Components/Advanced/McpAdvancedSection",
        "Components/Tools/McpToolsSection",
        "Components/Resources/McpResourcesSection",
        "Components/Validation/McpValidationSection",
    ],
)
def test_static_controller_bindings_have_markup_targets(relative: str) -> None:
    source = (WINDOWS / f"{relative}.cs").read_text(encoding="utf-8")
    names = {
        element.attrib["name"]
        for element in ET.parse(WINDOWS / f"{relative}.uxml").iter()
        if "name" in element.attrib
    }
    if relative == "EditorPrefs/EditorPrefsWindow":
        names |= {
            element.attrib["name"]
            for element in ET.parse(WINDOWS / "EditorPrefs/EditorPrefItem.uxml").iter()
            if "name" in element.attrib
        }
    references = set(re.findall(r'\.Q<[^>]+>\("([^"\n]+)"\)', source))
    # Logos are inserted by CreateGUI, not authored in UXML.
    references -= {"header-logo", "setup-logo"}
    assert references <= names, (relative, references - names)


def test_setup_is_manual_and_documentation_preserves_windowless_operation() -> None:
    setup = (ROOT / "MCPForUnity/Editor/Setup/SetupWindowService.cs").read_text(encoding="utf-8")
    assert "InitializeOnLoad" not in setup
    assert "EditorApplication.delayCall" not in setup
    assert "public static void ShowSetupWindow" in setup
    docs = (ROOT / "website/docs/getting-started/install.md").read_text(encoding="utf-8")
    assert "Closing a window does not stop the" in docs
    assert "does not open it automatically" in docs
