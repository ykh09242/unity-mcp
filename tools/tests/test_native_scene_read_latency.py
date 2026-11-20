"""Check that the observation clock ends at the expected loaded scene."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import sys

import anyio
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.experiments import native_scene_read_latency as probe


@pytest.mark.parametrize(
    ("scene", "loaded", "valid"),
    [
        ("Assets/Expected.unity", True, True),
        ("Assets/Other.unity", True, False),
        ("Assets/Expected.unity", False, False),
    ],
)
def test_scene_identity_controls_verified_receipt(monkeypatch, scene, loaded, valid):
    calls = []

    async def call_tool(name, params):
        calls.append((name, params))
        return SimpleNamespace(
            is_error=False,
            structured_content={"success": True, "data": {"path": scene, "isLoaded": loaded}},
        )

    @asynccontextmanager
    async def connect(_endpoint, _mode):
        yield SimpleNamespace(protocol_version="fixture", call_tool=call_tool)

    monkeypatch.setattr(probe, "official_http_client", connect)
    args = SimpleNamespace(
        endpoint="http://localhost", scene="Assets/Expected.unity", instance="Owned@hash", samples=2
    )
    if valid:
        receipt = anyio.run(probe.measure, args)
        assert receipt["public_scene_calls"] == 3
        assert len(receipt["warm"]) == 2
        assert receipt["warm_p95_ms"] is None
        assert receipt["connect_to_first_verified_ms"] >= receipt["first"]["ms"]
        assert receipt["initialize_rpc_calls"] is None
        assert {row["sha256"] for row in receipt["warm"]} == {receipt["first"]["sha256"]}
    else:
        with pytest.raises(ValueError, match="expected loaded scene"):
            anyio.run(probe.measure, args)
        assert len(calls) == 1
    assert all(
        call == ("scene_inspect", {"action": "get_active", "unity_instance": "Owned@hash"})
        for call in calls
    )
