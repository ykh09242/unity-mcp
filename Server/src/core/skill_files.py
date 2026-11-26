"""File types supported by the packaged Unity MCP skill and its release tooling."""

import re


_MARKDOWN_PATH = re.compile(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.md\Z")
_PICKER_FILES = {"agents/openai.yaml": "application/yaml", "assets/unity-mcp.svg": "image/svg+xml"}


def skill_file_mime_type(path: str) -> str | None:
    if _MARKDOWN_PATH.fullmatch(path):
        return "text/markdown"
    return _PICKER_FILES.get(path)
