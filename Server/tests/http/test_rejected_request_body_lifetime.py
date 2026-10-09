"""Rejected HTTP input must not live through slow error delivery or traceback handling."""

import asyncio
import gc
import weakref

import pytest
from starlette.exceptions import HTTPException

from transport.request_body_limit_middleware import RequestBodyLimitMiddleware


class TrackedMessage(dict):
    """Track the ASGI message that owns an ordinary immutable bytes body."""


def input_receiver(references):
    async def receive():
        message = TrackedMessage(
            type="http.request", body=b"x" * (2 * 1024 * 1024), more_body=False
        )
        references.append(weakref.ref(message))
        return message

    return receive


def http_scope():
    return {"type": "http", "headers": [], "state": {}, "method": "POST", "path": "/"}


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["resume", "cancel"])
async def test_rejected_chunk_is_released_while_413_send_is_blocked(completion):
    references = []
    blocked, resume = asyncio.Event(), asyncio.Event()
    sent = []

    async def send(message):
        sent.append((message["type"], message.get("status")))
        if message["type"] == "http.response.start":
            blocked.set()
            await resume.wait()

    async def parser(scope, receive, send):
        try:
            await receive()
        except HTTPException:
            # Existing handlers may catch ingress errors and try to overwrite them.
            await send({"type": "http.response.start", "status": 500})

    app = RequestBodyLimitMiddleware(parser, max_body_size=1024 * 1024)
    caller = asyncio.create_task(app(http_scope(), input_receiver(references), send))
    try:
        await asyncio.wait_for(blocked.wait(), 2)
        gc.collect()
        assert sent == [("http.response.start", 413)]
        assert len(references) == 1
        assert references[0]() is None, "Slow 413 delivery retains the rejected raw chunk"
        if completion == "cancel":
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert sent == [("http.response.start", 413)]
        else:
            resume.set()
            await caller
            assert sent == [("http.response.start", 413), ("http.response.body", None)]
        gc.collect()
        assert references[0]() is None
    finally:
        resume.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)


@pytest.mark.asyncio
async def test_started_stream_rejection_traceback_does_not_own_raw_chunk():
    references, errors, sent = [], [], []
    caught, finish = asyncio.Event(), asyncio.Event()

    async def send(message):
        sent.append((message["type"], message.get("status")))

    async def parser(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        try:
            await receive()
        except HTTPException as error:
            # Deliberately retain the real traceback; observers do not store the input.
            errors.append(error)
        caught.set()
        await finish.wait()

    app = RequestBodyLimitMiddleware(parser, max_body_size=1024 * 1024)
    caller = asyncio.create_task(app(http_scope(), input_receiver(references), send))
    try:
        await asyncio.wait_for(caught.wait(), 2)
        gc.collect()
        assert errors[0].status_code == 413
        assert errors[0].__traceback__ is not None
        assert sent == [("http.response.start", 200)]
        assert references[0]() is None, "The streaming rejection traceback owns the raw chunk"
        finish.set()
        with pytest.raises(HTTPException) as final:
            await caller
        assert final.value.status_code == 413
        assert sent == [("http.response.start", 200)]
    finally:
        finish.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        errors.clear()


@pytest.mark.asyncio
async def test_accepted_message_is_forwarded_without_middleware_retention():
    references = []
    parsed, finish = asyncio.Event(), asyncio.Event()

    async def send(message):
        pass

    async def parser(scope, receive, send):
        message = await receive()
        assert message is references[0]()
        assert len(message["body"]) == 2 * 1024 * 1024
        del message
        parsed.set()
        await finish.wait()

    app = RequestBodyLimitMiddleware(parser, max_body_size=2 * 1024 * 1024)
    caller = asyncio.create_task(app(http_scope(), input_receiver(references), send))
    try:
        await asyncio.wait_for(parsed.wait(), 2)
        gc.collect()
        assert references[0]() is None
        finish.set()
        await caller
    finally:
        finish.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
