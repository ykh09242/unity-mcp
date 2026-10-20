"""Serve the canonical packaged skill through the final read-only Skills extension."""

import base64
import copy
import hashlib
from importlib import metadata, resources
from inspect import signature
import json
import logging
import re
from typing import TYPE_CHECKING

from pydantic import JsonValue

from models.response_limits import MAX_RESPONSE_BYTES, response_size

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.server.extensions import ServerExtension

logger = logging.getLogger(__name__)
IDENTIFIER = "io.modelcontextprotocol/skills"
NAME = "unity-mcp-orchestrator"
PREFIX = "skill://" + NAME + "/"
SKILL_URI = PREFIX + "SKILL.md"
REPOSITORY = "https://github.com/ykh09242/unity-mcp"
MAX_BUNDLE_BYTES = 24 * 1024 * 1024
MAX_SKILL_BYTES = 16 * 1024 * 1024
PATH = re.compile(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.md\Z")


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate bundle key")
        result[key] = value
    return result


def _snapshot(raw: bytes) -> tuple[dict[str, str], dict[str, JsonValue], dict[str, JsonValue]]:
    """Validate accidental corruption and metadata before any SDK publication.

    The checksum is not a signature against an actor able to rewrite the package.
    YAML/source consistency is established by the verified build-time generator.
    """
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("Excessive skill bundle")
    record = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(record, dict) or set(record) != {"payload", "sha256"}:
        raise ValueError("Invalid bundle envelope")
    payload = record["payload"]
    if response_size(payload, max_bytes=MAX_BUNDLE_BYTES) is None:
        raise ValueError("Excessive skill metadata")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != record["sha256"]:
        raise ValueError("Skill bundle checksum mismatch")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"format", "repository", "revision", "frontmatter", "files"}
        or payload["format"] != 1
        or payload["repository"] != REPOSITORY
        or not isinstance(payload["revision"], str)
        or re.fullmatch(r"[0-9a-f]{40}", payload["revision"]) is None
        or int(payload["revision"], 16) == 0
    ):
        raise ValueError("Invalid bundle provenance/format")
    frontmatter = payload["frontmatter"]
    if (
        not isinstance(frontmatter, dict)
        or frontmatter.get("name") != NAME
        or not isinstance(frontmatter.get("description"), str)
        or not 1 <= len(frontmatter["description"]) <= 1024
    ):
        raise ValueError("Invalid canonical skill frontmatter")
    files = payload["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 512:
        raise ValueError("Invalid skill file count")
    texts: dict[str, str] = {}
    manifest = []
    paths: set[str] = set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "data", "digest", "size"}:
            raise ValueError("Invalid skill file record")
        path = item["path"]
        if not isinstance(path, str) or PATH.fullmatch(path) is None or path.casefold() in paths:
            raise ValueError("Unsafe or duplicate skill path")
        paths.add(path.casefold())
        if not isinstance(item["data"], str) or type(item["size"]) is not int:
            raise ValueError("Invalid skill bytes/size")
        data = base64.b64decode(item["data"], validate=True)
        total += len(data)
        if total > MAX_SKILL_BYTES or item["size"] != len(data):
            raise ValueError("Excessive or mismatched skill size")
        if item["digest"] != "sha256:" + hashlib.sha256(data).hexdigest():
            raise ValueError("Skill digest mismatch")
        uri = PREFIX + path
        texts[uri] = data.decode("utf-8", errors="strict")
        manifest.append({"uri": uri, "digest": item["digest"], "size": len(data)})
    if SKILL_URI not in texts:
        raise ValueError("Missing skill entry point")
    entry = {
        "uri": SKILL_URI,
        "frontmatter": frontmatter,
        "resources": sorted(manifest, key=lambda item: item["uri"]),
        "_meta": {
            "io.modelcontextprotocol.skills/sourceRepository": REPOSITORY,
            "io.modelcontextprotocol.skills/sourceRevision": payload["revision"],
        },
    }
    hints = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
    listed = {**hints, "skills": [entry]}
    fetched = {**hints, "skill": entry}
    # Generic extension methods do not traverse the resource response-size hook.
    # Admit each full immutable projection, leaving the ordinary envelope reserve.
    if any(
        response_size(value, max_bytes=MAX_RESPONSE_BYTES - 32_768) is None
        for value in (listed, fetched)
    ):
        raise ValueError("Skill result exceeds shared response limits")
    return texts, listed, fetched


def install_skills(
    server: "FastMCP", *, bundle_bytes: bytes | None = None
) -> "ServerExtension | None":
    """Install only the reviewed public SDK shape and a fully admitted snapshot.

    Invalid/missing optional support leaves ordinary MCP and file installation usable.
    No request-driven paths, package downloads or live guidance sources are consulted.
    """
    try:
        if metadata.version("mcp") != "2.3.0" or metadata.version("fastmcp") != "4.1.1":
            return None
        # Optional API imports stay inside the refusal boundary.
        from fastmcp.resources import Resource
        from fastmcp.server.extensions import MethodBinding, ServerExtension
        from mcp.shared.exceptions import MCPError
        from mcp_types import CacheableResult, INVALID_PARAMS, RequestParams
        from pydantic import Field

        if not all(
            callable(value)
            for value in (
                MethodBinding,
                ServerExtension,
                Resource,
                server.add_extension,
                server.add_resource,
            )
        ):
            return None
        if (
            not {"method", "params_type", "handler", "protocol_versions"}
            <= signature(MethodBinding).parameters.keys()
            or "extension" not in signature(server.add_extension).parameters
            or "resource" not in signature(server.add_resource).parameters
        ):
            return None
        if bundle_bytes is None:
            with resources.files("services").joinpath("skills_bundle.json").open("rb") as stream:
                bundle_bytes = stream.read(MAX_BUNDLE_BYTES + 1)
        texts, listed, fetched = _snapshot(bundle_bytes)

        class ListParams(RequestParams):
            cursor: str | None = None

        class GetParams(RequestParams):
            uri: str

        class ListResult(CacheableResult):
            skills: list[dict[str, JsonValue]]

        class GetResult(CacheableResult):
            skill: dict[str, JsonValue]

        class SkillResource(Resource):
            text: str = Field(exclude=True, repr=False)

            async def read(self) -> str:
                return self.text

        class SkillsExtension(ServerExtension):
            identifier = IDENTIFIER

            async def list_skills(self, _context, params):
                if params.cursor is not None:
                    raise MCPError(INVALID_PARAMS, "Unknown skill cursor")
                return ListResult.model_validate(copy.deepcopy(listed))

            async def get_skill(self, _context, params):
                if params.uri != SKILL_URI:
                    raise MCPError(INVALID_PARAMS, "Unknown skill URI")
                return GetResult.model_validate(copy.deepcopy(fetched))

            def methods(self):
                versions = frozenset({"2026-07-28"})
                return (
                    MethodBinding("skills/list", ListParams, self.list_skills, versions),
                    MethodBinding("skills/get", GetParams, self.get_skill, versions),
                )

        extension = SkillsExtension()
        files = [
            SkillResource(
                uri=uri, name=uri.removeprefix(PREFIX), mime_type="text/markdown", text=text
            )
            for uri, text in sorted(texts.items())
        ]
        # Construct/validate all public bindings and resources before publication.
        extension.methods()
    except (
        ImportError,
        AttributeError,
        metadata.PackageNotFoundError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
    ):
        logger.warning(
            "Packaged Skills support unavailable; existing file-install fallback remains usable"
        )
        return None
    server.add_extension(extension)
    for resource in files:
        server.add_resource(resource)
    return extension
