"""Static, offline MCP Apps resource and request-local capability projection."""

from importlib.resources import files
from typing import Final, TypedDict

from fastmcp import Context, FastMCP
from fastmcp.resources import TextResource
from fastmcp.server.extensions import ServerExtension
from fastmcp.tools.base import Tool
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID, client_supports_apps

TEST_RESULTS_URI: Final = "ui://unity-mcp/test-results-v1"


class AppsSettings(TypedDict):
    mimeTypes: list[str]


class TestResultsExtension(ServerExtension):
    """Advertise only the static HTML MIME type; register no callable app tools."""

    identifier = EXTENSION_ID

    def settings(self) -> AppsSettings:
        return {"mimeTypes": [APP_MIME_TYPE]}


def supports_test_results_app(ctx: Context) -> bool:
    """Use SDK unified session capabilities for both legacy and modern clients."""
    if ctx.request_context is None:
        return False
    return client_supports_apps(ctx)


def project_test_results_tools(ctx: Context, tools: list[Tool]) -> list[Tool]:
    """Copy only this tool's metadata; never mutate a registered component."""
    if supports_test_results_app(ctx):
        return tools
    projected = []
    for tool in tools:
        if tool.name == "view_test_results":
            metadata = dict(tool.meta or {})
            metadata.pop("ui", None)
            tool = tool.model_copy(update={"meta": metadata})
        projected.append(tool)
    return projected


def install_test_results_app(server: FastMCP) -> None:
    """Load package data once, with no request data or external dependencies."""
    html = files("services").joinpath("test_results_view.html").read_text(encoding="utf-8")
    server.add_extension(TestResultsExtension())
    server.add_resource(
        TextResource(
            uri=TEST_RESULTS_URI,
            name="test_results_view",
            description="Offline view of one completed test-result page.",
            mime_type=APP_MIME_TYPE,
            text=html,
            meta={
                "ui": {
                    "csp": {
                        "connectDomains": [],
                        "resourceDomains": [],
                        "frameDomains": [],
                        "baseUriDomains": [],
                    },
                    "permissions": {},
                    "prefersBorder": True,
                }
            },
        )
    )
