"""Owned stdio wire contracts; native/SDK end-to-end acceptance is separate.

TASK3_API_PREREQUISITE failures are interface readiness, never behavioral RED.
The routing case deliberately uses only Task1 APIs and is the causal baseline.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import inspect
import json
import secrets
import select
import socket
import struct
import threading
import time
from types import SimpleNamespace
from uuid import UUID

import pytest

from core.config import config
from core.server_build import RUNNING_SERVER
from models.models import UnityInstanceInfo
from services import command_progress as progress
from transport.legacy import stdio_auth as auth
from transport.legacy import unity_connection as uc
from transport.unity_transport import send_with_unity_instance


CAPABILITY = "command_progress_v1"
TOKEN = "owned-core06-stdio-" + "k" * 32
IDENTITY = "Owned@d0c0f006"
PARAMS = {"commands": [{"tool": "fixture_first", "params": {"text": "literal"}}] * 2}
RESULT = b'{"status":"success","result":{"settled":2,"text":"2026-10-19T01:02:03Z"}}'
_ABSENT = object()


def _exact(peer, count):
    data = bytearray()
    while len(data) < count:
        chunk = peer.recv(count - len(data))
        if not chunk:
            raise EOFError("Owned wire peer closed inside a frame")
        data.extend(chunk)
    return bytes(data)


def _frame(peer):
    length = struct.unpack(">Q", _exact(peer, 8))[0]
    assert 0 < length <= 65_536, "Unexpected client fixture frame size"
    return _exact(peer, length)


def _write(peer, payload):
    peer.sendall(struct.pack(">Q", len(payload)) + payload)


def _control(command_id, **changes):
    value = {
        "type": "command_progress",
        "id": command_id,
        "completed": 1,
        "total": 2,
        "stage": "batch_items",
    }
    value.update(changes)
    return b"MCPG" + json.dumps(value, separators=(",", ":")).encode("utf-8")


@contextmanager
def wire_peer(respond=None, *, capabilities=_ABSENT, legacy=False, forged=False, commands=1):
    """Real TCP/HMAC/framing, one owned listener and one bounded joined thread."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    observed = SimpleNamespace(
        port=listener.getsockname()[1],
        generation=secrets.token_hex(16),
        session=secrets.token_hex(16),
        auth=[],
        business=[],
        preludes=[],
        failures=[],
        peers=[],
        listener=listener,
        closing=threading.Event(),
    )

    def serve():
        try:
            with listener.accept()[0] as peer:
                observed.peers.append(peer)
                peer.settimeout(2)
                if legacy:
                    peer.sendall(b"WELCOME UNITY-MCP 1 FRAMING=1\n")
                else:
                    challenge = secrets.token_hex(32)
                    greeting = (
                        "WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 "
                        f"SERVER={observed.generation} CHALLENGE={challenge}\n"
                    )
                    peer.sendall(greeting.encode("ascii"))
                    request = json.loads(_frame(peer))
                    observed.auth.append(request)
                    nonce = request["client_nonce"]
                    assert request["proof"] == auth.authentication_proof(
                        TOKEN, ["client", observed.generation, challenge, nonce]
                    )
                    reply = {
                        "type": "authenticated",
                        "version": 2,
                        "session_id": observed.session,
                        "proof": auth.authentication_proof(
                            TOKEN,
                            ["server", observed.generation, challenge, nonce, observed.session],
                        ),
                    }
                    if capabilities is not _ABSENT:
                        reply["capabilities"] = capabilities
                    if forged:
                        reply["proof"] = "0" * 64
                    _write(peer, json.dumps(reply, separators=(",", ":")).encode("ascii"))
                if commands == 0:
                    return
                for index in range(commands):
                    body = _frame(peer)
                    decoded = json.loads(body)
                    prelude = None
                    if decoded.get("type") == "command_progress_begin":
                        prelude = decoded
                        assert set(prelude) == {"type", "version", "id"}
                        assert type(prelude["version"]) is int and prelude["version"] == 1
                        assert str(UUID(prelude["id"])) == prelude["id"]
                        observed.preludes.append(prelude)
                        body = _frame(peer)
                    observed.business.append(body)
                    if respond is None:
                        _write(peer, RESULT)
                    else:
                        respond(peer, prelude, observed, index)
        except (OSError, EOFError) as error:
            if not observed.closing.is_set():
                observed.failures.append(error)
        except Exception as error:
            # Thread failures must reach the owning test instead of becoming warnings.
            observed.failures.append(error)

    thread = threading.Thread(target=serve, name="owned-core06-stdio-peer")
    thread.start()
    try:
        yield observed
    finally:
        observed.closing.set()
        for peer in observed.peers:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            peer.close()
        listener.close()
        thread.join(3)
        assert not thread.is_alive(), "Owned peer did not close/join"
        assert not observed.failures, observed.failures


class RecordingContext:
    """Only the public SDK notification boundary is substituted, not the transport."""

    def __init__(self, token="owned-mcp-token"):
        self.request_context = SimpleNamespace(meta={"progressToken": token})
        self.calls = []
        self.received = asyncio.Queue()
        self.acknowledged = threading.Event()
        self.command = None

    async def report_progress(self, completed, total=None, message=None):
        self.calls.append(
            (completed, total, message, threading.get_ident(), not self.command.done())
        )
        self.received.put_nowait(completed)
        self.acknowledged.set()


async def _wait_owned_event(event, timeout, *, task=None):
    async with asyncio.timeout(timeout):
        while not event.is_set():
            if task is not None and task.done():
                task.result()
                pytest.fail("Command returned before its owned peer checkpoint")
            await asyncio.sleep(0.001)


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    # No user status/discovery read is needed for a wholly owned explicit endpoint.
    monkeypatch.setattr(uc, "read_status_file", lambda *_: None)
    pool = uc.UnityConnectionPool()
    pool._default_instance_id = IDENTITY
    pool._last_full_scan = time.time()
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    yield pool
    for connection in pool._connections.values():
        connection.disconnect()


def _connection(environment, peer, *, legacy=False):
    connection = uc.UnityConnection(
        host="127.0.0.1",
        port=peer.port,
        instance_id=IDENTITY,
        auth_token_provider=lambda _: TOKEN,
        allow_legacy_auth=legacy,
    )
    environment._known_instances[IDENTITY] = UnityInstanceInfo(
        id=IDENTITY,
        name="Owned",
        hash="d0c0f006",
        path="/Owned/Assets",
        port=peer.port,
        status="running",
    )
    environment._connections[IDENTITY] = connection
    return connection


def _expected_business():
    return json.dumps(
        {
            "type": "batch_execute",
            "params": PARAMS,
            "server_info": RUNNING_SERVER.command_metadata(),
        }
    ).encode("utf-8")


async def _prepared_connection(environment, peer, *, legacy=False):
    connection = _connection(environment, peer, legacy=legacy)
    assert await asyncio.wait_for(asyncio.to_thread(connection.connect), 3)
    # This fixture owns bootstrap metadata. Consume its real reconnect claim before
    # the command, so unrelated background tool resync cannot create extra commands.
    assert connection.claim_tool_resync()
    return connection


async def _route(environment, peer, ctx, *, deadline=None, require_api=True, connection=None):
    """Use actual Task1 routing, pool/admission, worker, authentication and framed I/O."""
    if require_api:
        assert "_progress" in inspect.signature(uc.async_send_command_with_retry).parameters, (
            "TASK3_API_PREREQUISITE: explicit stdio sink forwarding is not implemented"
        )
    if connection is None:
        await _prepared_connection(environment, peer)
    async with progress.foreground_progress(ctx):
        sink = progress.claim_command_progress(total=2)
        ctx.command = asyncio.create_task(
            send_with_unity_instance(
                uc.async_send_command_with_retry,
                IDENTITY,
                "batch_execute",
                PARAMS,
                _progress=sink,
                _deadline=deadline,
                retry_on_reload=False,
            )
        )
        try:
            return await asyncio.wait_for(asyncio.shield(ctx.command), 3)
        finally:
            if not ctx.command.done():
                ctx.command.cancel()
            await asyncio.gather(ctx.command, return_exceptions=True)


def test_old_auth_wrapper_preserves_generation_and_does_not_offer_capabilities():
    # Given: a new peer offers an optional capability to the existing wrapper.
    with wire_peer(capabilities=[CAPABILITY], commands=0) as peer:
        with socket.create_connection(("127.0.0.1", peer.port), timeout=2) as sock:
            banner = bytearray()
            while not banner.endswith(b"\n"):
                banner.extend(_exact(sock, 1))
            # When: the legacy public wrapper authenticates using its unchanged signature.
            generation = auth.authenticate_stdio(
                sock,
                banner.decode("ascii").rstrip("\n"),
                auth.StdioAuthentication(lambda _: TOKEN, time.monotonic() + 2),
            )
        # Then: its exact combined identity/proof transcript and old offer remain compatible.
        assert generation == peer.generation + ":" + peer.session
        assert set(peer.auth[0]) == {"type", "version", "client_nonce", "proof"}


@pytest.mark.parametrize(
    "reply,expected",
    [
        ([CAPABILITY], frozenset({CAPABILITY})),
        (_ABSENT, frozenset()),
        ([], frozenset()),
        (["unknown_capability"], frozenset()),
        (CAPABILITY, frozenset()),
        (True, frozenset()),
        ([CAPABILITY] * 17, frozenset()),
        (["x" * 65, CAPABILITY], frozenset()),
    ],
    ids=["accepted", "old-peer", "empty", "unknown", "scalar", "bool", "too-many", "too-long"],
)
def test_auth_session_accepts_only_bounded_optional_intersection(reply, expected):
    # Given: an actual valid HMAC peer with a controlled optional acknowledgement.
    method = getattr(auth, "authenticate_stdio_session", None)
    assert method is not None, "TASK3_API_PREREQUISITE: authenticated session API is absent"
    with wire_peer(capabilities=reply, commands=0) as peer:
        with socket.create_connection(("127.0.0.1", peer.port), timeout=2) as sock:
            banner = bytearray()
            while not banner.endswith(b"\n"):
                banner.extend(_exact(sock, 1))
            # When: the new caller offers exactly one optional capability.
            session = method(
                sock,
                banner.decode("ascii").rstrip("\n"),
                auth.StdioAuthentication(lambda _: TOKEN, time.monotonic() + 2),
                capabilities=(CAPABILITY,),
            )
        # Then: optional junk cannot create capability or change verified connection identity.
        assert session.server_generation == peer.generation
        assert session.session_id == peer.session
        assert session.session_generation == peer.generation + ":" + peer.session
        assert session.capabilities == expected
        assert isinstance(session.capabilities, frozenset)
        with pytest.raises(AttributeError):
            session.session_id = "changed"
        assert peer.auth[0]["capabilities"] == [CAPABILITY]
        assert TOKEN not in json.dumps(peer.auth)


def test_optional_capability_does_not_bypass_required_server_proof():
    # Given: a capability-bearing acknowledgement with a forged required proof.
    method = getattr(auth, "authenticate_stdio_session", None)
    assert method is not None, "TASK3_API_PREREQUISITE: authenticated session API is absent"
    with wire_peer(capabilities=[CAPABILITY], forged=True, commands=0) as peer:
        with socket.create_connection(("127.0.0.1", peer.port), timeout=2) as sock:
            banner = bytearray()
            while not banner.endswith(b"\n"):
                banner.extend(_exact(sock, 1))
            # When: optional capability negotiation shares the original mutual authentication.
            with pytest.raises(auth.StdioAuthenticationError):
                method(
                    sock,
                    banner.decode("ascii").rstrip("\n"),
                    auth.StdioAuthentication(lambda _: TOKEN, time.monotonic() + 2),
                    capabilities=(CAPABILITY,),
                )
        # Then: no command admission follows the rejected reciprocal proof.
        assert peer.business == []


@pytest.mark.asyncio
async def test_disconnect_and_old_peer_reconnect_clear_accepted_capabilities(environment):
    # Given: a connection has one verified capable session, not process-global capability state.
    with wire_peer(capabilities=[CAPABILITY], commands=0) as first:
        connection = _connection(environment, first)
        assert hasattr(connection, "authenticated_session"), (
            "TASK3_API_PREREQUISITE: connection session ownership is absent"
        )
        assert await asyncio.wait_for(asyncio.to_thread(connection.connect), 3)
        session = connection.authenticated_session
        assert session.capabilities == frozenset({CAPABILITY})
        assert connection.session_generation == session.session_generation
    # When: the peer's joined real FIN reaches the actual stale-socket peek path.
    async with asyncio.timeout(1):
        while not select.select([connection.sock], [], [], 0)[0]:
            await asyncio.sleep(0.001)
    connection._ensure_live_connection()
    assert connection.sock is None
    assert connection.authenticated_session is None
    assert connection.session_generation is None
    with wire_peer(commands=0) as second:
        connection.port = second.port
        assert await asyncio.wait_for(asyncio.to_thread(connection.connect), 3)
        # Then: the new verified identity has no inherited optional authority.
        assert connection.authenticated_session.capabilities == frozenset()
        assert connection.authenticated_session.session_id == second.session
        assert connection.authenticated_session.server_generation == second.generation
        assert connection.session_generation != session.session_generation
        connection.disconnect()
        assert connection.sock is None
        assert connection.session_generation is None
        assert connection.authenticated_session is None


@pytest.mark.parametrize("mode", ["old-peer", "no-token", "explicit-legacy"])
@pytest.mark.asyncio
async def test_absent_optin_keeps_one_original_business_frame(environment, mode):
    # Given: an old peer, no MCP token, or deliberate unauthenticated legacy operation.
    legacy = mode == "explicit-legacy"
    capabilities = _ABSENT if mode == "old-peer" else [CAPABILITY]
    ctx = RecordingContext(None if mode == "no-token" else "owned-mcp-token")
    with wire_peer(capabilities=capabilities, legacy=legacy) as peer:
        connection = await _prepared_connection(environment, peer, legacy=legacy)
        # When: the existing routing sends an ordinary single batch.
        async with progress.foreground_progress(ctx):
            sink = progress.claim_command_progress(total=2)
            ctx.command = asyncio.create_task(
                send_with_unity_instance(
                    uc.async_send_command_with_retry,
                    IDENTITY,
                    "batch_execute",
                    PARAMS,
                    _progress=sink,
                    retry_on_reload=False,
                )
            )
            result = await asyncio.wait_for(ctx.command, 3)
        connection.disconnect()
        # Then: no prelude/control/report changes old wire bytes or terminal result.
        assert peer.business == [_expected_business()]
        assert peer.preludes == []
        assert ctx.calls == []
        assert result == {"settled": 2, "text": "2026-10-19T01:02:03Z"}


@pytest.mark.asyncio
async def test_routed_optin_reports_before_real_terminal_result(environment):
    # Given: a capable real peer gates its result on the original owner's notification.
    ctx = RecordingContext()
    owner_thread = threading.get_ident()

    def respond(sock, prelude, _peer, _index):
        if prelude is not None:
            assert set(prelude) == {"type", "version", "id"}
            assert prelude["version"] == 1
            _write(sock, _control(prelude["id"]))
            assert ctx.acknowledged.wait(1), "Progress was not observed while command is pending"
        _write(sock, RESULT)

    with wire_peer(respond, capabilities=[CAPABILITY]) as peer:
        # When: current public Task1 routing executes the one admitted native command.
        result = await _route(environment, peer, ctx, require_api=False)
        # Then: missing stdio forwarding is causal RED, not a missing future API.
        assert [(x[0], x[1]) for x in ctx.calls] == [(1, 2)]
        assert ctx.calls[0][3:] == (owner_thread, True)
        assert peer.business == [_expected_business()]
        assert len(peer.preludes) == 1
        assert "owned-mcp-token" not in json.dumps(peer.preludes + peer.auth)
        assert result == {"settled": 2, "text": "2026-10-19T01:02:03Z"}


@pytest.mark.parametrize(
    "bad",
    [
        "matched-positive",
        "wrong-id",
        "malformed",
        "oversized",
        "unknown-stage",
        "bool",
        "extra",
        "flood",
    ],
)
@pytest.mark.asyncio
async def test_optional_controls_cannot_replace_final_response(environment, bad):
    # Given: a negotiated peer sends invalid bounded controls before unchanged result bytes.
    ctx = RecordingContext()
    frames_written = threading.Event()
    allow_terminal = threading.Event()

    def respond(sock, prelude, _peer, _index):
        assert prelude is not None
        command_id = prelude["id"]
        if bad == "matched-positive":
            frames = [_control(command_id)]
        elif bad == "wrong-id":
            frames = [_control("different-command")]
        elif bad == "malformed":
            frames = [b"MCPG{"]
        elif bad == "oversized":
            frames = [b"MCPG" + b"x" * 4097]
        elif bad == "unknown-stage":
            frames = [_control(command_id, stage="not_batch_items")]
        elif bad == "bool":
            frames = [_control(command_id, completed=True)]
        elif bad == "extra":
            frames = [_control(command_id, secret="not-forwardable")]
        else:
            frames = [_control("wrong-command") for _ in range(256)]
            frames.append(_control(command_id))
        for frame in frames:
            _write(sock, frame)
        frames_written.set()
        assert allow_terminal.wait(2), "Owner did not release its held terminal response"
        _write(sock, RESULT)

    with wire_peer(respond, capabilities=[CAPABILITY]) as peer:
        route = asyncio.create_task(_route(environment, peer, ctx))
        try:
            # When: the real command stays pending while the foreground has time to deliver.
            await _wait_owned_event(frames_written, 1, task=route)
            if route.done():
                route.result()
            assert not route.done(), "Command completed before its terminal response existed"
            assert not ctx.command.done()
            if bad == "matched-positive":
                assert await asyncio.wait_for(ctx.received.get(), 1) == 1
                assert [(call[0], call[1]) for call in ctx.calls] == [(1, 2)]
                assert ctx.calls[0][4] is True
            else:
                # Longer than the reporter's 100ms interval and immediate recording callback.
                # The valid 257th flood frame is also forbidden before terminal closure.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(ctx.received.get(), 0.35)
                assert ctx.calls == []
            assert not route.done()
            assert not ctx.command.done(), "Foreground command ended inside the observation window"
            # Then: only after the pre-terminal oracle may the original result finish the call.
            allow_terminal.set()
            result = await asyncio.wait_for(asyncio.shield(route), 3)
            assert result == {"settled": 2, "text": "2026-10-19T01:02:03Z"}
            assert len(ctx.calls) == (1 if bad == "matched-positive" else 0)
            assert peer.business == [_expected_business()]
        finally:
            allow_terminal.set()
            if not route.done():
                route.cancel()
            await asyncio.gather(route, return_exceptions=True)


@pytest.mark.parametrize("part", ["header", "payload"])
@pytest.mark.asyncio
async def test_partial_optional_frame_is_unknown_and_never_resends(environment, part):
    # Given: business execution is admitted before a deliberately corrupt partial frame.
    ctx = RecordingContext()

    def respond(sock, prelude, _peer, _index):
        assert prelude is not None
        body = _control(prelude["id"])
        wire = struct.pack(">Q", len(body)) + body
        sock.sendall(wire[:4] if part == "header" else wire[:-3])
        sock.shutdown(socket.SHUT_WR)

    with wire_peer(respond, capabilities=[CAPABILITY]) as peer:
        # When: the actual worker reaches EOF after the single business payload was written.
        result = await _route(environment, peer, ctx)
        # Then: uncertainty survives and the same operation is never automatically sent again.
        view = result.model_dump() if hasattr(result, "model_dump") else result
        assert view["success"] is False
        assert view["data"]["reason"] == "outcome_unknown"
        assert view["hint"] == "inspect_state_before_retry"
        assert peer.business == [_expected_business()]
        assert ctx.calls == []
        peer.listener.setblocking(False)
        try:
            unexpected, _ = peer.listener.accept()
        except BlockingIOError:
            pass
        else:
            with unexpected:
                pytest.fail("A partial optional frame caused an unsafe reconnect attempt")


@pytest.mark.asyncio
async def test_controls_do_not_renew_original_command_deadline(environment):
    # Given: paced traffic would continue well beyond the original half-second ceiling.
    ctx = RecordingContext()
    owner_closed = threading.Event()
    observed = {}

    def respond(sock, prelude, _peer, _index):
        assert prelude is not None
        sock.settimeout(0.1)
        for index in range(20):
            try:
                _write(sock, _control("unowned-id"))
                unexpected = sock.recv(1)
            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError):
                unexpected = b""
            assert unexpected == b"", "Unexpected command bytes while waiting for owned close"
            observed["unsent_frames"] = 19 - index
            observed["closed_at"] = time.monotonic()
            owner_closed.set()
            return
        pytest.fail("Owner accepted paced optional traffic beyond its original deadline")

    with wire_peer(respond, capabilities=[CAPABILITY]) as peer:
        # Complete real authentication before measuring the command's ceiling.
        connection = await _prepared_connection(environment, peer)
        started = asyncio.get_running_loop().time()
        # When: one explicit monotonic ceiling covers admission, every control and final wait.
        result = await _route(environment, peer, ctx, deadline=started + 0.5, connection=connection)
        # Then: optional traffic never becomes a fresh duration or work retry.
        ended = asyncio.get_running_loop().time()
        await _wait_owned_event(owner_closed, 1)
        view = result.model_dump() if hasattr(result, "model_dump") else result
        assert view["success"] is False
        assert view["data"]["reason"] == "outcome_unknown"
        assert 0.35 <= ended - started < 1.25
        assert observed["closed_at"] - started < 1.25
        assert observed["unsent_frames"] >= 5, "Traffic ended before the deadline oracle"
        assert peer.business == [_expected_business()]
        assert ctx.calls == []


@pytest.mark.asyncio
async def test_identical_next_command_cannot_inherit_previous_progress_owner(environment):
    # Given: the same authenticated socket processes two byte-identical commands in order.
    contexts = [RecordingContext("first-owner"), RecordingContext("second-owner")]
    previous_id = []

    def respond(sock, prelude, _peer, index):
        assert prelude is not None
        if index:
            assert prelude["id"] != previous_id[0]
            _write(sock, _control(previous_id[0], completed=2))
        else:
            previous_id.append(prelude["id"])
        _write(sock, _control(prelude["id"]))
        assert contexts[index].acknowledged.wait(1)
        _write(sock, RESULT)

    with wire_peer(respond, capabilities=[CAPABILITY], commands=2) as peer:
        connection = await _prepared_connection(environment, peer)
        # When: the first foreground finishes before the next identical native operation.
        first = await _route(environment, peer, contexts[0], connection=connection)
        second = await _route(environment, peer, contexts[1], connection=connection)
        # Then: late prior-owner progress is dropped, without changing either operation/result.
        assert first == second == {"settled": 2, "text": "2026-10-19T01:02:03Z"}
        assert peer.business == [_expected_business(), _expected_business()]
        assert [len(context.calls) for context in contexts] == [1, 1]
        assert [[call[0] for call in context.calls] for context in contexts] == [[1], [1]]


@pytest.mark.asyncio
async def test_closed_foreground_cannot_deliver_a_later_wire_update(environment):
    # Given: the native command remains pending after its first actual optional update.
    assert "_progress" in inspect.signature(uc.async_send_command_with_retry).parameters, (
        "TASK3_API_PREREQUISITE: explicit stdio sink forwarding is not implemented"
    )
    ctx = RecordingContext()
    resume = threading.Event()

    def respond(sock, prelude, _peer, _index):
        assert prelude is not None
        _write(sock, _control(prelude["id"]))
        assert ctx.acknowledged.wait(1)
        assert resume.wait(2), "Owner did not release its owned peer"
        _write(sock, _control(prelude["id"], completed=2))
        _write(sock, RESULT)

    with wire_peer(respond, capabilities=[CAPABILITY]) as peer:
        await _prepared_connection(environment, peer)
        try:
            async with progress.foreground_progress(ctx):
                sink = progress.claim_command_progress(total=2)
                ctx.command = asyncio.create_task(
                    send_with_unity_instance(
                        uc.async_send_command_with_retry,
                        IDENTITY,
                        "batch_execute",
                        PARAMS,
                        _progress=sink,
                        retry_on_reload=False,
                    )
                )
                assert await asyncio.wait_for(ctx.received.get(), 1) == 1
                assert not ctx.command.done()
            # When: the foreground closes while its independently owned I/O later finishes.
            resume.set()
            result = await asyncio.wait_for(asyncio.shield(ctx.command), 2)
            # Then: the second frame does not resurrect the public callback or replay work.
            assert [call[0] for call in ctx.calls] == [1]
            assert result == {"settled": 2, "text": "2026-10-19T01:02:03Z"}
            assert peer.business == [_expected_business()]
        finally:
            resume.set()
            if ctx.command is not None:
                if not ctx.command.done():
                    ctx.command.cancel()
                await asyncio.gather(ctx.command, return_exceptions=True)
