"""Build or verify the packaged LaBSE vectors for the public English catalog."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "Server/src/services/registry"
INDEX = REGISTRY / "model/labse-catalog.f16"


async def collect_documents() -> list[str]:
    # This authoring command runs in the server's normal, locked Python environment.
    # Register built-in declarations only. No Unity instance or custom project is involved.
    sys.path.insert(0, str(ROOT / "Server/src"))
    from fastmcp import Client, FastMCP
    from services.registry import get_registered_resources, get_registered_tools
    from services.registry.action_catalog import (
        concrete_resources,
        resource_templates,
        tool_capability,
    )
    from services.registry.task_search import semantic_documents
    from services.resources import register_all_resources
    from services.tools import register_all_tools

    texts = set()
    os.environ.pop("UNITY_MCP_TOOL_GROUPS", None)
    for profile in ("core", "full"):
        os.environ["UNITY_MCP_TOOL_PROFILE"] = profile
        server = FastMCP("public-search-index")
        register_all_tools(server)
        register_all_resources(server)
        async with Client(server, mode="legacy", cache=False) as client:
            visible = {tool.name for tool in await client.list_tools()}
        entries = get_registered_tools()
        capabilities = [(entry, tool_capability(entry, visible_names=visible)) for entry in entries]
        resources = get_registered_resources()
        catalog = semantic_documents(
            capabilities,
            [*concrete_resources(resources), *resource_templates(resources)],
            {entry["name"] for entry in entries} - visible,
        )
        texts.update(text for descriptions in catalog.values() for text in descriptions)
    return sorted(texts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=INDEX)
    parser.add_argument("--texts-only", type=Path, metavar="JSON")
    parser.add_argument(
        "--check", action="store_true", help="Verify the current index byte for byte"
    )
    args = parser.parse_args()
    docs = asyncio.run(collect_documents())
    encoded = json.dumps(docs, ensure_ascii=False, indent=2).encode("utf-8")
    if args.texts_only:
        args.texts_only.write_bytes(encoded)
        print(f"Exported {len(docs)} public descriptions to {args.texts_only}")
        return 0
    from services.registry import semantic_profile

    profile = semantic_profile.profile_dir()
    if not semantic_profile.ready_profile(profile):
        parser.error("Provision the local model first with unity-mcp search-model install.")
    semantic_profile.verify_weights(profile)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as directory:
        scratch = Path(directory)
        source = scratch / "documents.json"
        source.write_bytes(encoded)
        built = scratch / "catalog.f16"
        subprocess.run(
            [
                str(semantic_profile.runtime_python(profile)),
                "-I",
                str(REGISTRY / "semantic_labse_worker.py"),
                str(profile),
                "--index",
                str(source),
                str(built),
            ],
            check=True,
        )
        digest = hashlib.sha256(built.read_bytes()).hexdigest()
        if args.check:
            if not args.output.exists() or built.read_bytes() != args.output.read_bytes():
                print(f"Public vector index differs, new SHA-256 {digest}", file=sys.stderr)
                return 1
        else:
            built.replace(args.output)
    print(f"{len(docs)} public descriptions, SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
