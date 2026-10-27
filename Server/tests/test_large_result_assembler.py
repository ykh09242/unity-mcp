"""Cross-language result framing and bounded transfer ownership regressions."""

import base64
import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import pytest

from transport.large_result_assembler import (
    CHUNK_PAYLOAD_BYTES,
    MAGIC,
    MAX_FRAME_BYTES,
    MAX_RESULT_BYTES,
    THRESHOLD_BYTES,
    LargeResultAssembler,
    LargeResultProtocolError,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from owned_process import drain_owned_process

COMMAND_ID = "01234567-89ab-cdef-0123-456789abcdef"
OWNER = "socket-generation-a"


class Budget:
    """Mutable owned accounting fixture; no external sessions are accessed."""

    def __init__(self, maximum=MAX_RESULT_BYTES):
        self.maximum = maximum
        self.pending = {(OWNER, COMMAND_ID)}
        self.reservations = {}
        self.now = 10.0

    def reserve(self, owner, command_id, size):
        if sum(self.reservations.values()) + size > self.maximum:
            return False
        self.reservations[owner, command_id] = size
        return True

    def release(self, owner, command_id):
        self.reservations.pop((owner, command_id))

    def assembler(self):
        return LargeResultAssembler(
            lambda owner, cid: (owner, cid) in self.pending,
            self.reserve,
            self.release,
            clock=lambda: self.now,
        )


def frames(payload, command_id=COMMAND_ID):
    return [
        MAGIC
        + command_id.encode("ascii")
        + offset.to_bytes(4, "big")
        + payload[offset : offset + CHUNK_PAYLOAD_BYTES]
        for offset in range(0, len(payload), CHUNK_PAYLOAD_BYTES)
    ]


def started(size=THRESHOLD_BYTES):
    budget = Budget()
    assembler = budget.assembler()
    assembler.begin(
        OWNER, COMMAND_ID, size, (size + CHUNK_PAYLOAD_BYTES - 1) // CHUNK_PAYLOAD_BYTES
    )
    return budget, assembler


@pytest.fixture(scope="module")
def csharp_setup_properties() -> list[tuple[str, str]]:
    """Hold module preparation measurements until its first consuming test reports."""
    return []


@contextmanager
def csharp_phase(properties: list[tuple[str, str]], name: str) -> Iterator[None]:
    """Preserve phase durations in JUnit even when a child fails or times out."""
    began = perf_counter()
    try:
        yield
    finally:
        properties.append((name, str(perf_counter() - began)))


@pytest.fixture(scope="module")
def compiled_csharp_harness(tmp_path_factory, csharp_setup_properties):
    root = Path(__file__).resolve().parents[2]
    # Cold compiler startup competes with xdist workers on constrained runners.
    # Setup is not a request-latency assertion and returns as soon as the child exits.
    budget_name = "UNITY_MCP_TEST_CSHARP_SETUP_TIMEOUT_SECONDS"
    try:
        setup_timeout = float(os.environ.get(budget_name, "300"))
    except ValueError:
        pytest.fail(f"{budget_name} must be a positive finite number", pytrace=False)
    if not math.isfinite(setup_timeout) or setup_timeout <= 0:
        pytest.fail(f"{budget_name} must be a positive finite number", pytrace=False)
    csharp_setup_properties.append(("csharp_setup_timeout_seconds", str(setup_timeout)))
    with csharp_phase(csharp_setup_properties, "csharp_sdk_discovery_seconds"):
        dotnet = shutil.which("dotnet")
        if dotnet is None:
            pytest.skip("dotnet required for actual C# writer framing roundtrip")
        sdk_listing = subprocess.run(
            [dotnet, "--list-sdks"],
            check=True,
            capture_output=True,
            text=True,
            timeout=setup_timeout,
        ).stdout.splitlines()
        if not sdk_listing:
            pytest.skip("dotnet SDK required to compile actual C# writer")
        sdk_version, sdk_parent = sdk_listing[-1].split(" [", 1)
        compiler = Path(sdk_parent.rstrip("]")) / sdk_version / "Roslyn/bincore/csc.dll"
        framework_root = Path(dotnet).resolve().parent
        ref_root = framework_root / "packs/Microsoft.NETCore.App.Ref"
        ref_version = sorted(
            ref_root.iterdir(), key=lambda item: tuple(map(int, item.name.split(".")))
        )[-1]
        target_framework = next((ref_version / "ref").iterdir())
        runtime_version = ref_version.name
    csharp_setup_properties.extend(
        [("csharp_sdk_version", sdk_version), ("csharp_target_framework", target_framework.name)]
    )
    work = tmp_path_factory.mktemp("large-result-csharp")
    dll = work / "LargeResultHarness.dll"
    arguments = [
        dotnet,
        str(compiler),
        "/nologo",
        "/noconfig",
        "/nostdlib+",
        "/target:exe",
        "/langversion:latest",
        f"/out:{dll}",
    ]
    arguments.extend(f"/reference:{reference}" for reference in target_framework.glob("*.dll"))
    arguments.extend(
        [
            str(root / "MCPForUnity/Editor/Services/Transport/LargeResultWriter.cs"),
            str(root / "tools/tests/fixtures/large_result/LargeResultHarness.cs"),
        ]
    )
    with csharp_phase(csharp_setup_properties, "csharp_compile_seconds"):
        built = subprocess.run(arguments, capture_output=True, text=True, timeout=setup_timeout)
        assert built.returncode == 0, built.stdout + built.stderr
    dll.with_suffix(".runtimeconfig.json").write_text(
        json.dumps(
            {
                "runtimeOptions": {
                    "tfm": target_framework.name,
                    "framework": {"name": "Microsoft.NETCore.App", "version": runtime_version},
                }
            }
        ),
        encoding="utf-8",
    )
    return dotnet, dll


@pytest.fixture
def csharp_harness(request, csharp_setup_properties):
    # Direct item properties survive xdist and avoid record_property's xunit2 warning.
    try:
        dotnet, dll = request.getfixturevalue("compiled_csharp_harness")
    finally:
        request.node.user_properties.extend(csharp_setup_properties)
        csharp_setup_properties.clear()
    return dotnet, dll, request.node.user_properties


def run_writer(harness, tmp_path, payload, mode):
    path = tmp_path / "synthetic-result.bin"
    path.write_bytes(payload)
    with csharp_phase(harness[2], "csharp_execution_seconds"):
        execution = subprocess.run(
            [harness[0], str(harness[1]), str(path), mode, COMMAND_ID],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    return [
        (kind, base64.b64decode(encoded))
        for line in execution.stdout.splitlines()
        if "|" in line
        for kind, encoded in [line.split("|", 1)]
    ]


@pytest.mark.parametrize("mode", ["interleave", "interleave-held-caller"])
def test_csharp_large_result_reconstructs_identical_json_with_control_interleaving(
    csharp_harness, tmp_path, mode
):
    # Given: synthetic UTF-8 JSON produced by the same C# writer used in transport.
    payload = json.dumps(
        {
            "type": "command_result",
            "id": COMMAND_ID,
            "result": {"status": "success", "text": "\uc131\uacf5" * 100000},
        },
        ensure_ascii=False,
    ).encode()
    budget = Budget()
    assembler = budget.assembler()
    # When: complete individually locked messages include a queued control send.
    records = run_writer(csharp_harness, tmp_path, payload, mode)
    metadata = json.loads(records[0][1])
    assembler.begin(OWNER, metadata["id"], metadata["total_bytes"], metadata["chunk_count"])
    completed = None
    for kind, frame in records[1:]:
        if kind == "B":
            assert len(frame) <= MAX_FRAME_BYTES
            completed = assembler.feed(OWNER, frame)
    # Then: control message is between chunks and raw/normal JSON results are unchanged.
    assert [kind for kind, _ in records[:4]] == ["T", "B", "T", "B"]
    assert json.loads(records[2][1]) == {"type": "pong"}
    assert completed.payload == payload
    assert json.loads(completed.payload) == json.loads(payload)
    assert sum(budget.reservations.values()) == len(payload)
    assembler.discard(OWNER, COMMAND_ID)
    assert budget.reservations == {}


@pytest.mark.parametrize(
    "failure",
    [subprocess.TimeoutExpired("owned-child", 30), subprocess.CalledProcessError(7, "owned-child")],
)
def test_csharp_phase_reports_elapsed_when_child_fails(monkeypatch, failure):
    readings = iter([10.0, 10.25])
    monkeypatch.setattr(sys.modules[__name__], "perf_counter", lambda: next(readings))
    properties = []
    with pytest.raises(type(failure)) as raised:
        with csharp_phase(properties, "csharp_execution_seconds"):
            raise failure
    assert raised.value is failure
    assert properties == [("csharp_execution_seconds", "0.25")]


def test_csharp_phase_preserves_prior_properties_after_success(monkeypatch):
    readings = iter([10.0, 10.5])
    monkeypatch.setattr(sys.modules[__name__], "perf_counter", lambda: next(readings))
    properties = [("csharp_sdk_version", "owned-sdk")]
    with csharp_phase(properties, "csharp_compile_seconds"):
        pass
    assert properties == [("csharp_sdk_version", "owned-sdk"), ("csharp_compile_seconds", "0.5")]


def test_csharp_sdk_discovery_timeout_is_bounded_and_reported(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("UNITY_MCP_TEST_CSHARP_SETUP_TIMEOUT_SECONDS", "61")
    monkeypatch.setattr(shutil, "which", lambda _name: "owned-dotnet")

    def stalled_listing(command, *, timeout, **_options):
        assert command == ["owned-dotnet", "--list-sdks"]
        assert timeout == 61
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(subprocess, "run", stalled_listing)
    properties = []
    with pytest.raises(subprocess.TimeoutExpired):
        compiled_csharp_harness.__wrapped__(tmp_path_factory, properties)
    assert len(properties) == 2
    assert properties[0] == ("csharp_setup_timeout_seconds", "61.0")
    assert properties[1][0] == "csharp_sdk_discovery_seconds"
    assert float(properties[1][1]) >= 0


def test_csharp_failed_preparation_attaches_timings_only_to_first_consumer():
    properties = [("csharp_sdk_discovery_seconds", "0.1")]
    item_properties = []
    failure = subprocess.TimeoutExpired("owned-discovery", 30)

    def unavailable_fixture(_name):
        raise failure

    request = SimpleNamespace(
        node=SimpleNamespace(user_properties=item_properties), getfixturevalue=unavailable_fixture
    )
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        csharp_harness.__wrapped__(request, properties)
    assert raised.value is failure
    assert item_properties == [("csharp_sdk_discovery_seconds", "0.1")]
    assert properties == []
    with pytest.raises(subprocess.TimeoutExpired):
        csharp_harness.__wrapped__(request, properties)
    assert item_properties == [("csharp_sdk_discovery_seconds", "0.1")]


@pytest.mark.parametrize(
    "mode,size", [("legacy", THRESHOLD_BYTES + 1), ("normal", THRESHOLD_BYTES - 1)]
)
def test_csharp_old_peer_or_small_result_preserves_single_text_envelope(
    csharp_harness, tmp_path, mode, size
):
    # Given / When: negotiation absent or payload small.
    payload = b"x" * size
    records = run_writer(csharp_harness, tmp_path, payload, mode)
    # Then: original bytes arrive as one text message.
    assert records == [("T", payload)]


def test_csharp_cancellation_stops_after_first_chunk(csharp_harness, tmp_path):
    # Given / When: cancellation occurs during a large transfer.
    records = run_writer(csharp_harness, tmp_path, b"x" * THRESHOLD_BYTES, "cancel")
    # Then: no remaining chunks are replayed or sent.
    assert [kind for kind, _ in records] == ["T", "B"]


@pytest.mark.parametrize(
    "size,count",
    [(True, 1), (THRESHOLD_BYTES - 1, 4), (MAX_RESULT_BYTES + 1, 513), (THRESHOLD_BYTES, 99)],
)
def test_malformed_metadata_does_not_reserve_bytes(size, count):
    # Given / When: invalid advertised allocation/count crosses the boundary.
    budget = Budget()
    assembler = budget.assembler()
    with pytest.raises(LargeResultProtocolError):
        assembler.begin(OWNER, COMMAND_ID, size, count)
    # Then: no allocation is charged.
    assert budget.reservations == {}


@pytest.mark.parametrize("wrong_owner", ["wrong-socket", OWNER])
def test_unsolicited_result_is_ignored_without_reservation(wrong_owner):
    budget = Budget()
    if wrong_owner == OWNER:
        budget.pending.clear()
    assembler = budget.assembler()
    assert not assembler.begin(wrong_owner, COMMAND_ID, THRESHOLD_BYTES, 5)
    assert budget.reservations == {}


def test_aggregate_capacity_prevents_second_allocation():
    budget, assembler = started()
    second = str(uuid4())
    budget.pending.add((OWNER, second))
    budget.maximum = THRESHOLD_BYTES
    with pytest.raises(LargeResultProtocolError, match="capacity"):
        assembler.begin(OWNER, second, THRESHOLD_BYTES, 5)
    assert sum(budget.reservations.values()) == THRESHOLD_BYTES


@pytest.mark.parametrize("mutation", ["offset", "short", "oversized", "magic"])
def test_malformed_frame_is_rejected(mutation):
    budget, assembler = started()
    frame = frames(b"x" * THRESHOLD_BYTES)[0]
    if mutation == "offset":
        frame = frame[:40] + (1).to_bytes(4, "big") + frame[44:]
    elif mutation == "short":
        frame = frame[:-1]
    elif mutation == "oversized":
        frame += b"x"
    else:
        frame = b"BAD!" + frame[4:]
    with pytest.raises(LargeResultProtocolError):
        assembler.feed(OWNER, frame)
    assembler.discard_owner(OWNER)
    assert budget.reservations == {}


def test_partial_transfer_timeout_releases_reserved_bytes():
    budget, assembler = started()
    assembler.feed(OWNER, frames(b"x" * THRESHOLD_BYTES)[0])
    budget.now += 300
    assert assembler.expire() == ((OWNER, COMMAND_ID),)
    assert budget.reservations == {}
    assert assembler.retained_bytes == 0


def test_owner_invalidation_releases_partial_transfer():
    budget, assembler = started()
    budget.pending.clear()
    assert assembler.feed(OWNER, frames(b"x" * THRESHOLD_BYTES)[0]) is None
    assert budget.reservations == {}


def test_wrong_owner_chunk_does_not_destroy_legitimate_transfer():
    budget, assembler = started()
    assert assembler.feed("replaced-socket", frames(b"x" * THRESHOLD_BYTES)[0]) is None
    assert assembler.retained_bytes == THRESHOLD_BYTES
    assert sum(budget.reservations.values()) == THRESHOLD_BYTES


def test_replay_is_rejected_and_releases_completed_reservation():
    budget, assembler = started()
    wire = frames(b"x" * THRESHOLD_BYTES)
    for frame in wire:
        assembler.feed(OWNER, frame)
    with pytest.raises(LargeResultProtocolError, match="replay"):
        assembler.feed(OWNER, wire[-1])
    assert budget.reservations == {}


@pytest.mark.parametrize("tail", [0, 1, CHUNK_PAYLOAD_BYTES - 1])
def test_exact_chunk_boundary_and_short_final_chunk_reconstruct_payload(tail):
    # Given: lengths just around a complete-frame boundary.
    payload = bytes(range(256)) * (CHUNK_PAYLOAD_BYTES * 5 // 256 + 1)
    payload = (payload * 2)[: CHUNK_PAYLOAD_BYTES * 5 + tail]
    _, assembler = started(len(payload))
    # When: all full/final chunks arrive in order.
    completed = None
    for frame in frames(payload):
        completed = assembler.feed(OWNER, frame)
    # Then: no byte is omitted or duplicated at the boundary.
    assert completed.payload == payload


def test_duplicate_start_drops_only_affected_transfer():
    budget, assembler = started()
    other = str(uuid4())
    budget.pending.add((OWNER, other))
    assembler.begin(OWNER, other, THRESHOLD_BYTES, 5)
    with pytest.raises(LargeResultProtocolError, match="duplicate"):
        assembler.begin(OWNER, COMMAND_ID, THRESHOLD_BYTES, 5)
    assert budget.reservations == {(OWNER, other): THRESHOLD_BYTES}


def test_late_chunks_after_cancellation_are_ignored_without_reallocation():
    budget, assembler = started()
    wire = frames(b"x" * THRESHOLD_BYTES)
    assembler.feed(OWNER, wire[0])
    budget.pending.clear()
    # When: remaining in-flight frames arrive after the pending command is gone.
    for frame in wire[1:]:
        assert assembler.feed(OWNER, frame) is None
    assert not assembler.begin(OWNER, COMMAND_ID, THRESHOLD_BYTES, 5)
    # Then: this connection can still serve other commands and retains no bytes.
    assert budget.reservations == {}
    assert assembler.retained_bytes == 0


def test_two_results_can_interleave_without_payload_mixing():
    budget, assembler = started()
    other = str(uuid4())
    budget.pending.add((OWNER, other))
    assembler.begin(OWNER, other, THRESHOLD_BYTES, 5)
    first_payload = b"a" * THRESHOLD_BYTES
    other_payload = b"b" * THRESHOLD_BYTES
    first_completed = other_completed = None
    for first_frame, other_frame in zip(frames(first_payload), frames(other_payload, other)):
        first_completed = assembler.feed(OWNER, first_frame)
        other_completed = assembler.feed(OWNER, other_frame)
    assert first_completed.payload == first_payload
    assert other_completed.payload == other_payload
    assembler.discard_owner(OWNER)
    assert budget.reservations == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["interleave", "interleave-held-caller"])
async def test_real_csharp_websocket_messages_reconstruct_with_control_between_chunks(
    csharp_harness, tmp_path, mode
):
    """Only an ephemeral loopback socket carries owned synthetic bytes."""
    server_api = pytest.importorskip("websockets.asyncio.server")
    payload = json.dumps(
        {
            "type": "command_result",
            "id": COMMAND_ID,
            "result": {"status": "success", "text": "\uac00" * 100000},
        },
        ensure_ascii=False,
    ).encode()
    path = tmp_path / "synthetic-websocket-result.bin"
    path.write_bytes(payload)
    budget = Budget()
    assembler = budget.assembler()
    received = asyncio.get_running_loop().create_future()
    message_kinds = []

    async def handler(socket):
        async for message in socket:
            if isinstance(message, bytes):
                message_kinds.append("B")
                complete = assembler.feed(OWNER, message)
                if complete is not None:
                    received.set_result(complete)
                    return
            else:
                data = json.loads(message)
                message_kinds.append(data["type"])
                if data["type"] == "result_start":
                    assembler.begin(OWNER, data["id"], data["total_bytes"], data["chunk_count"])

    async with server_api.serve(
        handler, "127.0.0.1", 0, compression=None, max_size=MAX_FRAME_BYTES, close_timeout=2
    ) as server:
        port = server.sockets[0].getsockname()[1]
        with csharp_phase(csharp_harness[2], "csharp_execution_seconds"):
            process = await asyncio.create_subprocess_exec(
                csharp_harness[0],
                str(csharp_harness[1]),
                str(path),
                mode,
                COMMAND_ID,
                f"ws://127.0.0.1:{port}",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                async with drain_owned_process(process) as output:
                    complete = await asyncio.wait_for(received, 15)
                    _, stderr = await asyncio.wait_for(asyncio.shield(output), 15)
                    assert process.returncode == 0, stderr.decode()
            finally:
                assembler.discard_owner(OWNER)
    assert complete.payload == payload
    assert message_kinds[:4] == ["result_start", "B", "pong", "B"]
    assert budget.reservations == {}
