"""Exercise complete API-key cache entry points with real HTTPX in isolated children."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "race_valid_retained",
        "race_negative_retained",
        "refresh_negative_retained",
        "distinct_negative",
        "negative_priority",
        "expired_cleanup",
        "transient_retry",
    ],
)
def test_api_key_cache_contract(case, tmp_path):
    child_root = tmp_path / case
    child_root.mkdir()
    env = os.environ.copy()
    for key in (
        "APPDATA",
        "XDG_DATA_HOME",
        "UNITY_MCP_LOG_DIR",
        "HOME",
        "USERPROFILE",
        "TEMP",
        "TMP",
    ):
        env[key] = str(child_root)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    env["UNITY_MCP_TRANSPORT"] = "stdio"
    result = subprocess.run(
        [sys.executable, "-B", __file__, case, str(child_root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def _scenario(case):
    import asyncio
    import socket
    from unittest.mock import patch

    import httpx
    from services.api_key_service import ApiKeyService

    service = ApiKeyService("https://fixture.invalid/validate", cache_ttl=10)
    service.MAX_CACHE_ENTRIES = 2
    requests = []
    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    race_index = 0
    now = [0.0]
    transient_index = 0

    async def handler(request):
        nonlocal race_index, transient_index
        key = json.loads(request.content)["api_key"]
        requests.append(key)
        if key == "fixture-concurrent":
            index = race_index
            race_index += 1
            started[index].set()
            await release[index].wait()
            if case == "race_second_negative" and index == 1:
                return httpx.Response(401)
        if key == "fixture-negative" or (
            case == "race_negative_retained" and key == "fixture-retained"
        ):
            return httpx.Response(401)
        if case == "transient_retry" and key == "fixture-transient":
            transient_index += 1
            if transient_index == 1:
                return httpx.Response(503)
        return httpx.Response(200, json={"valid": True, "user_id": "fixture-user"})

    actual_client = httpx.AsyncClient

    def client(**kwargs):
        return actual_client(transport=httpx.MockTransport(handler), **kwargs)

    def denied(*args, **kwargs):
        raise AssertionError("Application network prohibited")

    # Windows asyncio creates its internal socketpair before application guards are installed.
    with (
        patch.object(socket.socket, "connect", denied),
        patch.object(socket.socket, "connect_ex", denied),
        patch("socket.create_connection", denied),
        patch("httpx.AsyncClient", client),
        patch("services.api_key_service.time.time", lambda: now[0]),
    ):
        if case.startswith("race_"):
            retained = await service.validate("fixture-retained")
            first = asyncio.create_task(service.validate("fixture-concurrent"))
            await started[0].wait()
            second = asyncio.create_task(service.validate("fixture-concurrent"))
            # Identical misses now share one validation; no second outbound request.
            for _ in range(10):
                await asyncio.sleep(0)
            assert race_index == 1
            release[0].set()
            assert (await first).valid
            second_result = await second
            assert second_result.valid
            before = len(requests)
            assert (await service.validate("fixture-retained")).valid is retained.valid
            assert len(requests) == before, (
                "Replacing the same cached key must not evict another entry"
            )
            assert (await service.validate("fixture-concurrent")).valid
            assert len(requests) == before, "The shared definitive verdict must remain cached"
            assert len(service._cache) == 2
        elif case == "refresh_negative_retained":
            await service.validate("fixture-retained")
            release[0].set()
            await service.validate("fixture-concurrent")
            await service.invalidate_cache("fixture-concurrent")
            # A new definitive negative refresh at capacity retains unrelated keys.
            case = "race_second_negative"
            release[1].set()
            assert not (await service.validate("fixture-concurrent")).valid
            before = len(requests)
            assert (await service.validate("fixture-retained")).valid
            assert not (await service.validate("fixture-concurrent")).valid
            assert len(requests) == before and len(service._cache) == 2
        elif case == "distinct_negative":
            await service.validate("fixture-one")
            await service.validate("fixture-two")
            assert not (await service.validate("fixture-negative")).valid
            before = len(requests)
            assert (await service.validate("fixture-one")).valid
            assert (await service.validate("fixture-two")).valid
            assert len(requests) == before
            assert "fixture-negative" not in service._cache
        elif case == "negative_priority":
            await service.validate("fixture-retained")
            await service.validate("fixture-negative")
            await service.validate("fixture-new")
            assert "fixture-negative" not in service._cache
            before = len(requests)
            assert (await service.validate("fixture-retained")).valid
            assert (await service.validate("fixture-new")).valid
            assert len(requests) == before
        elif case == "expired_cleanup":
            await service.validate("fixture-expired")
            now[0] = 5
            await service.validate("fixture-retained")
            now[0] = 11
            await service.validate("fixture-new")
            assert "fixture-expired" not in service._cache
            before = len(requests)
            assert (await service.validate("fixture-retained")).valid
            assert (await service.validate("fixture-new")).valid
            assert len(requests) == before
        elif case == "transient_retry":
            initial = await service.validate("fixture-transient")
            assert not initial.valid and not initial.cacheable
            assert (await service.validate("fixture-transient")).valid
            assert (await service.validate("fixture-transient")).valid
            assert len(requests) == 2
        else:
            raise AssertionError("Unknown fixture scenario")
        await service.aclose()
    print(json.dumps({"case": case, "requests": requests, "cacheKeys": sorted(service._cache)}))


if __name__ == "__main__":
    import asyncio

    owned_home = Path(sys.argv[2]).resolve()
    Path.home = classmethod(lambda cls: owned_home)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    asyncio.run(_scenario(sys.argv[1]))
