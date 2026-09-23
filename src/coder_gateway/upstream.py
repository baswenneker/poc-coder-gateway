"""Forward a chat-completions request to the upstream model (OpenAI) and pass the reply through."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse

from coder_gateway.config import UpstreamConfig
from coder_gateway.domain import VirtualModel

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
        self, body: dict[str, Any], vm: VirtualModel, *, inject_system_prompt: bool = True
    ) -> Response:
        payload = rewrite_request(body, vm, inject_system_prompt=inject_system_prompt)
        url = f"{self._config.base_url}/chat/completions"
        try:
            if payload.get("stream"):
                return await self._forward_stream(url, payload)
            resp = await self._client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            log.warning("upstream request failed: %s", type(exc).__name__)
            return _error_response(502, f"upstream request failed: {type(exc).__name__}")
        content = resp.content
        if resp.status_code == 200:
            content = _rename_model(content, vm.name)
        return Response(
            content=content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )

    async def _forward_stream(self, url: str, payload: dict[str, Any]) -> Response:
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

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()

        return StreamingResponse(
            relay(),
            status_code=200,
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )


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
