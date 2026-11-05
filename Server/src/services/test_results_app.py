"""Static, offline MCP Apps resource and request-local capability projection."""

from importlib.resources import files
from typing import Final, TypedDict

from fastmcp import Context, FastMCP
from fastmcp.apps import (
    UI_EXTENSION_ID,
    UI_MIME_TYPE,
    AppConfig,
    ResourceCSP,
    ResourcePermissions,
    app_config_to_meta_dict,
)
from fastmcp.resources import TextResource
from fastmcp.server.extensions import ServerExtension
from fastmcp.tools.base import Tool
from mcp.server.apps import client_supports_apps

TEST_RESULTS_URI: Final = "ui://unity-mcp/test-results-v1"


class AppsSettings(TypedDict):
    mimeTypes: list[str]


class TestResultsExtension(ServerExtension):
    """Advertise only the static HTML MIME type; register no callable app tools."""

    identifier = UI_EXTENSION_ID

    def settings(self) -> AppsSettings:
        return {"mimeTypes": [UI_MIME_TYPE]}


def supports_test_results_app(ctx: Context | None) -> bool:
    """Check the host's accepted HTML MIME type across both protocol generations."""
    if getattr(ctx, "request_context", None) is None:
        return False
    return client_supports_apps(ctx)


def project_test_results_tools(ctx: Context | None, tools: list[Tool]) -> list[Tool]:
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
            mime_type=UI_MIME_TYPE,
            text=html,
            meta={
                "ui": app_config_to_meta_dict(
                    AppConfig(
                        csp=ResourceCSP(
                            connect_domains=[],
                            resource_domains=[],
                            frame_domains=[],
                            base_uri_domains=[],
                        ),
                        permissions=ResourcePermissions(),
                        prefers_border=True,
                    )
                )
            },
        )
    )
