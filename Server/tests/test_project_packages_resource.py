"""Installed package observations use registered metadata and the selected Editor."""

import json
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP

from services.operation_permissions import (
    NO_GRANTS,
    OperationGrants,
    OperationPermissionError,
    Permission,
    authorize_operation,
    authorize_resource,
)
from services.resources import register_all_resources


INVENTORY = "mcpforunity://project/packages"
PACKAGE = "com.example.timeline"


@pytest.mark.asyncio
async def test_package_inventory_and_detail_are_advertised_and_read_from_selected_editor(
    monkeypatch,
):
    from services.resources import project_packages

    summary = {"name": PACKAGE, "version": "1.3.0"}
    detail = {
        **summary,
        "source": "Registry",
        "registry": {"name": "Production", "url": "https://packages.example.test/upm"},
        "description": "Timeline integration",
    }
    replies = [
        {"success": True, "data": {"count": 1, "packages": [summary]}},
        {"success": True, "data": detail},
        {"success": False, "error": "Resource 'get_project_packages' is disabled."},
    ]
    send = AsyncMock(side_effect=replies)
    monkeypatch.setattr(project_packages, "send_with_unity_instance", send)
    monkeypatch.setattr(
        project_packages, "get_unity_instance_from_context", AsyncMock(return_value="Project@hash")
    )
    server = FastMCP("installed-package-test")
    register_all_resources(server)
    async with Client(server, cache=False) as client:
        assert INVENTORY in {str(item.uri) for item in await client.list_resources()}
        assert INVENTORY + "/{package_name}" in {
            item.uri_template for item in await client.list_resource_templates()
        }
        for uri, reply in zip([INVENTORY, f"{INVENTORY}/{PACKAGE}", INVENTORY], replies):
            result = json.loads((await client.read_resource(uri))[0].text)
            assert all(result[key] == value for key, value in reply.items())
    assert [call.args[1:] for call in send.await_args_list] == [
        ("Project@hash", "get_project_packages", {}),
        ("Project@hash", "get_project_packages", {"package": PACKAGE}),
        ("Project@hash", "get_project_packages", {}),
    ]


def test_scoped_package_resources_and_native_owner_require_only_read():
    read = OperationGrants(frozenset({Permission.READ}), scoped=True)
    for uri in (INVENTORY, f"{INVENTORY}/{PACKAGE}"):
        authorize_resource(read, uri)
        with pytest.raises(OperationPermissionError):
            authorize_resource(NO_GRANTS, uri)
    authorize_operation(read, "get_project_packages", {}, native=True)
    with pytest.raises(OperationPermissionError):
        authorize_operation(NO_GRANTS, "get_project_packages", {}, native=True)
    for action in ("add_package", "add_registry", "resolve_packages"):
        with pytest.raises(OperationPermissionError):
            authorize_operation(read, "manage_packages", {"action": action}, native=True)
