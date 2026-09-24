"""Forward a chat-completions request to the upstream model (OpenAI) and pass the reply through."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from coder_gateway.config import UpstreamConfig
from coder_gateway.domain import VirtualModel
from coder_gateway.reply import OnFinish, amend_completion, inspect_stream, reply_from_completion

log = logging.getLogger("coder_gateway.upstream")


def rewrite_request(
    body: dict[str, Any], vm: VirtualModel, *, inject_system_prompt: bool = True
) -> dict[str, Any]:
    """The request as OpenAI should see it: the real model, the Virtual Model's system prompt first,
    and `max_completion_tokens` instead of `max_tokens` (gpt-5.x rejects `max_tokens`)."""
    out = dict(body)
    out["model"] = vm.upstream_model
    if "max_tokens" in out:
        max_tokens = out.pop("max_tokens")
        if max_tokens is not None:
            out.setdefault("max_completion_tokens", max_tokens)
    if inject_system_prompt and vm.system_prompt:
        out["messages"] = [{"role": "system", "content": vm.system_prompt}, *list(body.get("messages") or [])]
    return out


def _error_response(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"message": message, "type": "gateway_error", "code": None}}
    )


class Upstream:
    def __init__(self, config: UpstreamConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._config.api_key:
            headers["authorization"] = f"Bearer {self._config.api_key}"
        return headers

    async def forward(
        self,
        body: dict[str, Any],
        vm: VirtualModel,
        *,
        inject_system_prompt: bool = True,
        on_finish: OnFinish | None = None,
    ) -> Response:
        """Forward the request. With `on_finish`, a successful reply is inspected once complete and the
        Amendment the callback returns is applied before the client sees the end of the reply."""
        payload = rewrite_request(body, vm, inject_system_prompt=inject_system_prompt)
        url = f"{self._config.base_url}/chat/completions"
        try:
            if payload.get("stream"):
                return await self._forward_stream(url, payload, on_finish)
            resp = await self._client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            log.warning("upstream request failed: %s", type(exc).__name__)
            return _error_response(502, f"upstream request failed: {type(exc).__name__}")
        content = resp.content
        if resp.status_code == 200 and on_finish is not None:
            content = await _amend_json(content, on_finish)
        if resp.status_code == 200:
            content = _rename_model(content, vm.name)
        return Response(
            content=content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )

    async def _forward_stream(
        self, url: str, payload: dict[str, Any], on_finish: OnFinish | None
    ) -> Response:
        request = self._client.build_request("POST", url, json=payload, headers=self._headers())
        resp = await self._client.send(request, stream=True)
        if resp.status_code != 200:
            error_body = await resp.aread()
            await resp.aclose()
            return Response(
                content=error_body,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )

        return _UpstreamStream(resp, on_finish)


async def _amend_json(content: bytes, on_finish: OnFinish) -> bytes:
    """Non-streamed reply: ask for an Amendment and apply it. Fail-open on anything unexpected."""
    try:
        data = json.loads(content)
    except ValueError:
        return content
    reply = reply_from_completion(data)
    if reply is None or reply.finish_reason is None:
        return content
    try:
        amendment = await on_finish(reply)
    except Exception:  # noqa: BLE001 - fail-open: never break the model's reply
        log.exception("amending the upstream reply failed; passing it through unchanged")
        return content
    if amendment is None:
        return content
    return json.dumps(amend_completion(data, amendment), ensure_ascii=False).encode()


class _UpstreamStream(StreamingResponse):
    """Relays an upstream SSE stream and closes it over the whole response lifecycle: also when the
    client is gone before the first chunk (sending `http.response.start` fails, so the relay never
    starts) or the request is cancelled. `httpx.Response.aclose` is idempotent."""

    def __init__(self, upstream: httpx.Response, on_finish: OnFinish | None = None) -> None:
        self._upstream = upstream
        relay = self._relay() if on_finish is None else inspect_stream(self._relay(), on_finish)
        super().__init__(
            relay,
            status_code=200,
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    async def _relay(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._upstream.aiter_bytes():
                yield chunk
        finally:
            await self._upstream.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._upstream.aclose()


def _rename_model(content: bytes, name: str) -> bytes:
    """Show the Virtual Model name instead of the upstream model in a non-streamed reply (best effort)."""
    try:
        data = json.loads(content)
    except ValueError:
        return content
    if isinstance(data, dict) and "model" in data:
        data["model"] = name
        return json.dumps(data, ensure_ascii=False).encode()
    return content
