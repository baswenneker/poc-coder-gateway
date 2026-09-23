"""Upstream forwarding: the upstream stream is closed whatever happens downstream."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from gateway_helpers import make_workflow

from coder_gateway.config import UpstreamConfig
from coder_gateway.domain import VirtualModel
from coder_gateway.upstream import Upstream

VM = VirtualModel("fwd-coder", "sk-gw-test", "gpt-5.4", "", make_workflow())


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        self.closed += 1


async def test_stream_closed_when_client_disconnects_before_first_chunk() -> None:
    # Codex review 1, finding 4: sending `http.response.start` fails (client already gone), so the
    # relay generator never starts. The upstream response must be closed anyway.
    stream = TrackedStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})

    upstream = Upstream(
        UpstreamConfig(base_url="https://up.test/v1"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    response = await upstream.forward({"messages": [], "stream": True}, VM)

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        raise OSError("client disconnected")

    scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
    with pytest.raises(Exception):  # noqa: B017 - Starlette turns the OSError into ClientDisconnect
        await response(scope, receive, send)
    assert stream.closed == 1


async def test_stream_closed_once_after_normal_relay() -> None:
    stream = TrackedStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})

    upstream = Upstream(
        UpstreamConfig(base_url="https://up.test/v1"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    response = await upstream.forward({"messages": [], "stream": True}, VM)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert b"".join(m.get("body", b"") for m in sent) == b"data: [DONE]\n\n"
    assert stream.closed == 1
