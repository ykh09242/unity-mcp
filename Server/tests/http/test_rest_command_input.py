"""Exercise JSON admission on both public REST routes with inert registered peers."""

import asyncio
import json
from threading import Event, get_ident

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from tests.http.test_local_project_context import environment, hub_environment, payload  # noqa: F401
from tests.http.test_request_body_limit import invoke, scope_for
from tests.test_execution_receipt_integration import options
from tests.timing import await_signal, watchdog_timeout
from transport import rest_command_input as inputs
from transport.execution_receipts import ExecutionReceipts
from transport.request_body_limit_middleware import RequestBodyLimitMiddleware

JSON_HEADERS = {"content-type": "application/json"}


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix,tail", [(rb"\\upload", "\\upload"), (rb"\u0061", "a")])
async def test_default_rest_budget_preserves_large_ascii_escape(environment, suffix, tail):
    _, _, a, b, client = environment
    prefix = b'{"type":"ping","unity_instance":"' + a.project_hash.encode() + b'","params":{"v":"'
    size = 29 * 1024**2
    raw = prefix + b"a" * size + suffix + b'"}}'
    response = await client.post("/api/command", content=raw, headers=JSON_HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert len(a.executes) == 1 and b.executes == []
    value = a.executes[0]["params"]["v"]
    assert len(value) == size + len(tail) and value.endswith(tail)


@pytest.mark.parametrize(
    "suffix,wide",
    [
        (rb"\\upload", False),
        (rb"\u0061", False),
        (rb"\u00Ff", False),
        (rb"\\uFFff", False),
        (rb"\\\u0100", True),
        (rb"\\\\u0100", False),
        (rb"\u0100", True),
        (rb"\ud83d\ude00", True),
        ("漢".encode() + rb"\n", True),
    ],
)
def test_string_widening_profile_uses_token_contents(suffix, wide):
    # A wide literal in a different field must not widen this field's escape.
    token = b"ascii-prefix" + suffix
    raw = b'{"tag":"' + "漢".encode() + b'","v":"' + token + b'"}'
    profile = inputs.bounded_json_profile(
        raw, max_bytes=1024, max_depth=8, max_nodes=16, profile_string_widening=True
    )
    assert profile.complete_strings
    assert bool(profile.max_widening_string) is wide


@pytest.mark.asyncio
async def test_rest_admits_decoder_widening_workspace_before_loads(monkeypatch):
    parser = inputs.RestCommandInput()
    calls = 0
    loads = inputs.loads

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return loads(*args, **kwargs)

    monkeypatch.setattr(inputs, "loads", counted)

    async def route(scope, receive, send):
        async with parser.parse(Request(scope, receive), guarded=False) as body:
            if isinstance(body, inputs.InputFailure):
                response = body.response()
            else:
                response = JSONResponse({"last": ord(body["params"]["v"][-1])})
            await response(scope, receive, send)

    ingress = RequestBodyLimitMiddleware(route)
    prefix = b'{"type":"x","params":{"v":"' + b"a" * 4_000_000
    widening = rb"\uffff\ud83d\ude00"
    for cap, suffix, status, last in (
        (32, widening, 413, None),
        (32, b"", 200, ord("a")),
        (32, b'","other":"' + widening, 200, ord("a")),
        (128, widening, 200, 0x1F600),
    ):
        before = calls
        parser.max_request = cap * 1024**2
        sent, _ = await invoke(
            ingress, scope_for(), [{"type": "http.request", "body": prefix + suffix + b'"}}'}]
        )
        assert sent[0]["status"] == status
        assert calls == before + int(status == 200)
        if status == 200:
            assert json.loads(sent[1]["body"])["last"] == last
        assert parser.retained_bytes == ingress.retained_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/command", "/api/context-command"])
async def test_malformed_rest_envelopes_never_dispatch(environment, path):
    hub, _, a, b, client = environment
    for raw in (
        b"[]",
        b"null",
        b"{",
        b"\xff",
        b'{"type":false}',
        b'{"type":1}',
        b'{"type":""}',
        b'{"type":"x","params":null}',
        b'{"type":"x","params":[]}',
        b'{"type":"x","unity_instance":{}}',
        b'{"type":"x","params":{"v":NaN}}',
        b'{"type":"x","execution":{}}',
        b'{"type":"x","execution":null}',
    ):
        response = await client.post(path, content=raw, headers=JSON_HEADERS)
        assert response.status_code == 400, (raw, response.text)
        assert response.json()["data"]["dispatch"] == "not_dispatched"
    assert a.executes == b.executes == [] and hub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/command", "/api/context-command"])
async def test_rest_prescans_wide_and_deep_json(environment, monkeypatch, path):
    hub, _, a, b, client = environment
    monkeypatch.setattr(hub, "MAX_COMMAND_PAYLOAD_NODES", 32)
    monkeypatch.setattr(
        inputs, "loads", lambda _: pytest.fail("over-limit input reached graph decoder")
    )
    for param in (b"[" + b"1," * 16000 + b"0]", b"[" * 80 + b"0" + b"]" * 80):
        raw = (
            json.dumps(payload(a.root))
            .encode()
            .replace(b'"params": {}', b'"params": {"v":' + param + b"}")
        )
        response = await client.post(path, content=raw, headers=JSON_HEADERS)
        assert response.status_code == 413, response.text
        assert response.json()["data"]["dispatch"] == "not_dispatched"
    assert a.executes == b.executes == [] and hub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/command", "/api/context-command"])
async def test_rest_admits_large_valid_strings(environment, path):
    _, _, a, b, client = environment
    value = 'text"\\한😀' * 10000
    body = payload(a.root, params={"value": value})
    if path == "/api/command":
        body.pop("expected_project")
    for ascii_only in (True, False):
        response = await client.post(
            path, content=json.dumps(body, ensure_ascii=ascii_only).encode(), headers=JSON_HEADERS
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True
        assert a.executes[-1]["params"]["value"] == value
    assert len(a.executes) == 2 and b.executes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-32"])
async def test_unicode_input_chunks_preserve_split_codepoints(environment, encoding):
    _, _, a, b, client = environment
    # Offset the first non-BMP character across a UTF-8 or UTF-16 decoder boundary.
    prefix = '{"type":"ping","unity_instance":"' + a.project_hash + '","params":{"v":"'
    unit = {"utf-16": 2, "utf-32": 4}.get(encoding, 1)
    size = (65536 - len(prefix.encode(encoding)) - 2) // unit
    value = "x" * size + "😀" + "한" * 65536
    body = (prefix + value + '"}}').encode(encoding)
    response = await client.post("/api/command", content=body, headers=JSON_HEADERS)
    assert response.status_code == 200 and response.json()["success"] is True
    assert a.executes[-1]["params"]["v"] == value and not b.executes


@pytest.mark.asyncio
async def test_cancellation_owns_running_parser_and_drops_queued_parser(monkeypatch):
    parser = inputs.RestCommandInput()
    loop = asyncio.get_running_loop()
    loop_thread = get_ident()
    blocked, queued = [asyncio.Event(), asyncio.Event()], asyncio.Event()
    release = Event()
    decode = inputs._decode_envelope
    parsed = []

    def held(text, guarded):
        index = int(text[6])
        parsed.append(index)
        assert get_ident() != loop_thread and index in (0, 1)
        loop.call_soon_threadsafe(blocked[index].set)
        assert release.wait(watchdog_timeout())
        return decode(text, guarded)

    monkeypatch.setattr(inputs, "_decode_envelope", held)

    async def route(scope, receive, send):
        request = Request(scope, receive)
        if scope["path"] == "/2":
            queued.set()
        async with parser.parse(request, guarded=False) as body:
            await JSONResponse({"ok": body["type"]})(scope, receive, send)

    ingress = RequestBodyLimitMiddleware(route)
    raw_size = 0

    def post(index):
        nonlocal raw_size
        raw = json.dumps({"i": index, "type": "x", "params": {"text": "x" * 70000}}).encode()
        raw_size = len(raw)
        return asyncio.create_task(
            invoke(ingress, scope_for(path=f"/{index}"), [{"type": "http.request", "body": raw}])
        )

    tasks = []
    try:
        for index in (0, 1):
            tasks.append(post(index))
            await await_signal(
                blocked[index], "started REST graph decoder", producers=(tasks[index],)
            )
        initial = parser.retained_bytes
        tasks.append(post(2))
        await await_signal(queued, "queued REST parser", producers=(tasks[2],))
        assert ingress.retained_bytes == 3 * raw_size and parser.retained_bytes > initial
        tasks[2].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[2]
        tasks[0].cancel()
        await asyncio.sleep(0)
        tasks[0].cancel()
        await asyncio.sleep(0)
        assert not tasks[0].done()
        assert ingress.retained_bytes == 2 * raw_size and parser.retained_bytes == initial
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert (await tasks[1])[0][0]["status"] == 200
        assert sorted(parsed) == [0, 1]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert ingress.retained_bytes == parser.retained_bytes == 0


@pytest.mark.asyncio
async def test_graph_capacity_lives_through_dispatch_and_refuses_before_decode(monkeypatch):
    parser = inputs.RestCommandInput()
    entered, release = asyncio.Event(), asyncio.Event()

    async def route(scope, receive, send):
        async with parser.parse(Request(scope, receive), guarded=False) as body:
            if isinstance(body, inputs.InputFailure):
                response = body.response()
            else:
                entered.set()
                await release.wait()
                response = JSONResponse({"ok": True})
            await response(scope, receive, send)

    ingress = RequestBodyLimitMiddleware(route)
    messages = [{"type": "http.request", "body": b'{"type":"x","params":{"v":[0,1,2,3]}}'}]
    task = asyncio.create_task(invoke(ingress, scope_for(), messages))
    try:
        await await_signal(entered, "REST command dispatch", producers=(task,))
        assert parser.retained_bytes > ingress.retained_bytes > 0
        parser.max_total = parser.retained_bytes + 1024
        with monkeypatch.context() as patch:
            patch.setattr(inputs, "loads", lambda _: pytest.fail("graph decoded without capacity"))
            sent, _ = await invoke(ingress, scope_for(), messages)
            assert sent[0]["status"] == 503
            parser.max_request = 1024
            sent, _ = await invoke(ingress, scope_for(), messages)
            assert sent[0]["status"] == 413
    finally:
        release.set()
        await task
    assert parser.retained_bytes == ingress.retained_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/command", "/api/context-command"])
async def test_receipt_forwarding_owns_a_fresh_envelope(environment, tmp_path, monkeypatch, path):
    import main

    _, _, a, b, client = environment
    runtime = ExecutionReceipts(tmp_path / "rest-ledger", private_root=True)
    await runtime.start()
    forward = main.execute_receipted_rest
    import transport.execution_receipts as receipt_module

    capture = receipt_module.capture_identity
    owner_thread = get_ident()

    def capture_large(*args, **kwargs):
        assert get_ident() != owner_thread, "large receipt input validation occupied the owner loop"
        return capture(*args, **kwargs)

    monkeypatch.setattr(receipt_module, "capture_identity", capture_large)

    async def owned_runtime(_, *args, **kwargs):
        return await forward(runtime, *args, **kwargs)

    monkeypatch.setattr(main, "execute_receipted_rest", owned_runtime)
    command = payload(a.root, command="ping", params={"text": "a" * 70000})
    if path == "/api/command":
        command.pop("expected_project")
    command["execution"] = options(runtime, project=command["unity_instance"])
    try:
        for replayed in (False, True):
            response = await client.post(path, json=command)
            assert response.status_code == 200, response.text
            assert response.json()["success"] is True
            assert response.json()["execution_receipt"]["replayed"] is replayed
        assert len(a.executes) == 1 and b.executes == []
        assert a.executes[0]["params"] == command["params"]
        if path == "/api/context-command":
            # An idempotency conflict still uses the guarded route's refusal contract.
            command["params"]["changed"] = True
            refused = await client.post(path, json=command)
            assert (
                refused.status_code == 200
                and refused.json()["data"]["dispatch"] == "not_dispatched"
            )
            assert len(a.executes) == 1
    finally:
        runtime.close()
