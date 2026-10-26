"""Package source input rejection occurs before any transport dispatch."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.tools.manage_packages import manage_packages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "https://token@host/repo.git",
        "https://user:secret@host/repo.git",
        "ssh://user:secret@host/repo.git",
        "https://host/repo.git?access_token=secret",
        "https://host/repo.git?signature=secret",
        "git+https://token@host/repo.git",
        "user:secret@host:repo.git",
    ],
)
async def test_credential_source_is_rejected_before_dispatch(source):
    # Given credentials at the public source boundary.
    with patch(
        "services.tools.manage_packages._send_packages_command", new_callable=AsyncMock
    ) as send:
        # When the package tool receives an installation request.
        result = await manage_packages(MagicMock(), action="add_package", package=source)
    # Then no transport call or credential-bearing diagnostic is produced.
    assert result["success"] is False
    assert "secret" not in result["message"] and source not in result["message"]
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "git@host:repo.git#main",
        "ssh://git@host/repo.git",
        "https://host/repo.git?path=/Packages/tool#main",
        "file:../Fixture",
        "com.fixture.tool@1.2.3",
    ],
)
async def test_credential_free_git_and_registry_sources_are_preserved(source):
    # Given ordinary Git, subdirectory, local and registry identifiers.
    with patch(
        "services.tools.manage_packages._send_packages_command", new_callable=AsyncMock
    ) as send:
        send.return_value = {"success": True}
        # When accepted by the source boundary.
        result = await manage_packages(MagicMock(), action="add_package", package=source)
    # Then the exact source reaches native package validation.
    assert result["success"] is True
    assert send.await_args.args[1]["package"] == source
