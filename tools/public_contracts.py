"""Observe real tools/list contracts for the reference generator in an isolated process."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Final, Literal, assert_never

from pydantic import JsonValue

ROOT: Final = Path(__file__).resolve().parents[1]
CONTRACT_OUT: Final = ROOT / "Server/tests/fixtures/public_mcp_contracts.json"
_ANNOTATIONS: Final = {"description", "title", "examples", "$comment"}
_SCHEMA_MAPS: Final = {
    "properties",
    "patternProperties",
    "$defs",
    "definitions",
    "dependentSchemas",
}
_LITERALS: Final = {"default", "const", "enum"}


def _project(value: JsonValue, *, preserve_keys: bool = False) -> JsonValue:
    match value:
        case dict():
            return {
                key: child
                if not preserve_keys and key in _LITERALS
                else _project(child, preserve_keys=not preserve_keys and key in _SCHEMA_MAPS)
                for key, child in value.items()
                if preserve_keys or key not in _ANNOTATIONS
            }
        case list():
            return [_project(child) for child in value]
        case str() | int() | float() | bool() | None:
            return value
        case unreachable:
            assert_never(unreachable)


def schema_contract(schema: dict[str, JsonValue]) -> JsonValue:
    """Exclude prose annotations while preserving machine names and literal payloads."""
    return _project(schema)


@dataclass(frozen=True, slots=True)
class ContractChange:
    kind: Literal["added", "potentially_breaking"]
    path: str


def contract_changes(before: JsonValue, after: JsonValue, path: str = "") -> list[ContractChange]:
    """Report conservative breaking candidates and additive fields at exact JSON paths."""
    match before, after:
        case dict(), dict():
            changes = []
            for key in sorted(before.keys() | after.keys()):
                child_path = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in after:
                    changes.append(ContractChange("potentially_breaking", child_path))
                elif key not in before:
                    # New tools and optional properties are additions. A newly
                    # introduced schema constraint (especially required/enum/
                    # additionalProperties) may reject previously valid calls.
                    addition = path.rpartition("/")[2] in {
                        "tools",
                        "properties",
                        "$defs",
                        "definitions",
                    }
                    changes.append(
                        ContractChange("added" if addition else "potentially_breaking", child_path)
                    )
                else:
                    changes.extend(contract_changes(before[key], after[key], child_path))
            return changes
        case list(), list():
            if len(before) == len(after) and all(
                not contract_changes(old, new) for old, new in zip(before, after)
            ):
                return []
        case _:
            # JSON numbers compare by value, but booleans are a distinct JSON type.
            if before == after and isinstance(before, bool) == isinstance(after, bool):
                return []
    return [ContractChange("potentially_breaking", path or "/")]


async def _observe() -> dict[str, JsonValue]:
    """Use the SDK transport and dispatcher, never a synthetic reflection schema."""
    sys.path.insert(0, str(ROOT / "Server/src"))
    from fastmcp import Client, FastMCP
    from core.config import config
    from services.tools import register_all_tools

    config.transport_mode = "stdio"
    config.http_remote_hosted = False
    server = FastMCP("public-contract-snapshot")
    register_all_tools(server)
    protocols = {}
    for label, mode in (("modern", "2026-07-28"), ("legacy", "legacy")):
        async with Client(server, mode=mode, cache=False) as client:
            tools = {}
            for tool in await client.list_tools():
                raw = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                tools[tool.name] = {
                    key: schema_contract(raw[key])
                    for key in ("inputSchema", "outputSchema", "annotations")
                    if key in raw
                }
            protocols[label] = {"tools": dict(sorted(tools.items()))}
    return {
        "schema_version": "unity-mcp/public-contracts@1",
        "scope": {"profile": "full", "transport": "in_process", "project": "none", "hosted": False},
        "protocols": protocols,
    }


def capture_public_contracts() -> dict[str, JsonValue]:
    """Isolate imports, registration and configuration from documentation/unit tests."""
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--capture"],
        cwd=ROOT,
        env={**os.environ, "UNITY_MCP_TOOL_PROFILE": "full", "UNITY_MCP_DISABLE_TELEMETRY": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=True,
    )
    return json.loads(result.stdout)


def render_public_contracts() -> str:
    return (
        json.dumps(capture_public_contracts(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def public_contract_drift(expected: str, path: Path = CONTRACT_OUT) -> list[str]:
    """Keep CI failure messages bounded and directly actionable."""
    if not path.is_file():
        return [f"missing public MCP contract snapshot: {path}"]
    changes = contract_changes(json.loads(path.read_text(encoding="utf-8")), json.loads(expected))
    messages = [f"public MCP {item.kind}: {item.path}" for item in changes[:40]]
    if len(changes) > 40:
        messages.append(f"public MCP: {len(changes) - 40} further changed paths")
    return messages


if __name__ == "__main__":
    import anyio

    if sys.argv[1:] != ["--capture"]:
        raise SystemExit(
            "Use tools/generate_docs_reference.py to generate or check public contracts."
        )
    print(json.dumps(anyio.run(_observe), ensure_ascii=False, sort_keys=True))
