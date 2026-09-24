"""Read the upstream model's reply and change it before the client sees it (DECISIONS.md #28).

The Gateway decides on the response side: at the end of a Turn (finish 'stop', no tool calls) and when
a tool call matches a trigger. Both need the whole reply. This module turns an upstream reply, a
chat.completion object or a chat.completion.chunk SSE stream, into an `AssistantReply`, asks a callback
for an `Amendment`, and applies it.

Streaming: text deltas go to the client live. Tool-call deltas are held back until the reply is
complete (the client only acts on them after the finish anyway), and so are the finish chunk and
everything after it (usage chunk, `[DONE]`). Without an Amendment the held events go out byte for byte.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from coder_gateway.domain import Message

log = logging.getLogger("coder_gateway.reply")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string, as in the OpenAI format

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class AssistantReply:
    """What the upstream model answered (choice 0)."""

    content: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str | None

    def as_message(self) -> Message:
        """The reply as an assistant message, to append to the Transcript."""
        message: Message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [tc.to_json() for tc in self.tool_calls]
        return message


@dataclass(frozen=True)
class Amendment:
    """How the Gateway changes the upstream reply."""

    finish_reason: str
    # Text added after the model's own text.
    append_text: str = ""
    # A tool call added to the reply (a Proposal via the client's `question` tool).
    tool_call: ToolCall | None = None
    # Leave out the model's tool calls (a Block: the client never gets to run them).
    drop_tool_calls: bool = False


OnFinish = Callable[[AssistantReply], Awaitable[Amendment | None]]


# --- non-streaming ------------------------------------------------------------------------------------


def reply_from_completion(data: Any) -> AssistantReply | None:
    """The reply in a chat.completion object, or None when it has no usable choice 0."""
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    choice = choices[0]
    message = _as_dict(choice.get("message"))
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") if isinstance(raw, dict) else None
        if isinstance(fn, dict):
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    arguments=str(fn.get("arguments") or ""),
                )
            )
    content = message.get("content")
    return AssistantReply(
        content=content if isinstance(content, str) else "",
        tool_calls=tuple(calls),
        finish_reason=choice.get("finish_reason"),
    )


def amend_completion(data: dict[str, Any], amendment: Amendment) -> dict[str, Any]:
    """Apply an Amendment to a chat.completion object (in place; returned for convenience)."""
    choice = data["choices"][0]
    message = choice.setdefault("message", {"role": "assistant"})
    if amendment.append_text:
        message["content"] = (message.get("content") or "") + amendment.append_text
    if amendment.drop_tool_calls:
        message.pop("tool_calls", None)
    if amendment.tool_call is not None:
        message["tool_calls"] = [*(message.get("tool_calls") or []), amendment.tool_call.to_json()]
    choice["finish_reason"] = amendment.finish_reason
    return data


# --- streaming ------------------------------------------------------------------------------------------

_EVENT_END = re.compile(rb"\r?\n\r?\n")


@dataclass
class _Event:
    raw: bytes  # the event as received, including its blank-line terminator
    chunk: dict[str, Any] | None = None  # parsed `data:` JSON, if any
    done: bool = False  # `data: [DONE]`


def _parse_event(raw: bytes) -> _Event:
    lines = [line for line in raw.decode("utf-8", errors="replace").splitlines() if line.startswith("data:")]
    if not lines:
        return _Event(raw)
    data = "\n".join(line[5:].lstrip() for line in lines)
    if data.strip() == "[DONE]":
        return _Event(raw, done=True)
    try:
        parsed = json.loads(data)
    except ValueError:
        return _Event(raw)
    return _Event(raw, chunk=parsed if isinstance(parsed, dict) else None)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _choice0(chunk: dict[str, Any] | None) -> dict[str, Any] | None:
    if chunk is None:
        return None
    choices = chunk.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return None


def _sse(chunk: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()


@dataclass
class _Collector:
    """Accumulates the reply from the stream's deltas."""

    content: list[str] = field(default_factory=list)
    calls: dict[int, dict[str, str]] = field(default_factory=dict)
    finish_reason: str | None = None

    def add(self, choice: dict[str, Any]) -> None:
        delta = _as_dict(choice.get("delta"))
        if isinstance(delta.get("content"), str):
            self.content.append(delta["content"])
        for raw in delta.get("tool_calls") or []:
            if not isinstance(raw, dict):
                continue
            call = self.calls.setdefault(int(raw.get("index") or 0), {"id": "", "name": "", "arguments": ""})
            if raw.get("id"):
                call["id"] = str(raw["id"])
            fn = _as_dict(raw.get("function"))
            if fn.get("name"):
                call["name"] += str(fn["name"])
            if fn.get("arguments"):
                call["arguments"] += str(fn["arguments"])
        if choice.get("finish_reason"):
            self.finish_reason = str(choice["finish_reason"])

    def reply(self) -> AssistantReply:
        calls = tuple(ToolCall(**self.calls[i]) for i in sorted(self.calls))
        return AssistantReply(
            content="".join(self.content), tool_calls=calls, finish_reason=self.finish_reason
        )


def _amended_events(held: list[_Event], amendment: Amendment, next_index: int) -> list[bytes]:
    """The held events with the Amendment applied: new deltas go right before the finish chunk."""
    out: list[bytes] = []
    finished = False
    for ev in held:
        choice = _choice0(ev.chunk)
        if ev.chunk is None or choice is None:
            out.append(ev.raw)
            continue
        is_finish = bool(choice.get("finish_reason")) and not finished
        delta = _as_dict(choice.get("delta"))
        changed = False
        if amendment.drop_tool_calls and "tool_calls" in delta:
            delta = {k: v for k, v in delta.items() if k != "tool_calls"}
            changed = True
        if is_finish:
            finished = True
            base = {k: v for k, v in ev.chunk.items() if k not in ("choices", "usage")}

            def extra(d: dict[str, Any], base: dict[str, Any] = base) -> bytes:
                return _sse(
                    {**base, "choices": [{"index": 0, "delta": d, "logprobs": None, "finish_reason": None}]}
                )

            if amendment.append_text:
                out.append(extra({"content": amendment.append_text}))
            if amendment.tool_call is not None:
                out.append(extra({"tool_calls": [{"index": next_index, **amendment.tool_call.to_json()}]}))
            new_choice = {**choice, "delta": delta, "finish_reason": amendment.finish_reason}
            out.append(_sse({**ev.chunk, "choices": [new_choice, *ev.chunk["choices"][1:]]}))
            continue
        if changed:
            if not delta and not choice.get("finish_reason") and not ev.chunk.get("usage"):
                continue  # only tool-call deltas: drop the whole event
            out.append(_sse({**ev.chunk, "choices": [{**choice, "delta": delta}, *ev.chunk["choices"][1:]]}))
            continue
        out.append(ev.raw)
    return out


async def inspect_stream(source: AsyncIterator[bytes], on_finish: OnFinish) -> AsyncIterator[bytes]:
    """Relay an upstream SSE stream, holding back tool-call deltas and the finish, and apply the
    Amendment `on_finish` returns once the upstream stream is complete. Fail-open: an exception in
    `on_finish` (or a stream without a finish) leaves the held events unchanged."""
    buffer = b""
    held: list[_Event] = []
    holding = False
    collector = _Collector()

    def handle(ev: _Event) -> bytes | None:
        nonlocal holding
        choice = _choice0(ev.chunk)
        if choice is not None:
            collector.add(choice)
            delta = _as_dict(choice.get("delta"))
            if delta.get("tool_calls") or choice.get("finish_reason"):
                holding = True
        if holding:
            held.append(ev)
            return None
        return ev.raw

    async for data in source:
        buffer += data
        while True:
            match = _EVENT_END.search(buffer)
            if match is None:
                break
            raw, buffer = buffer[: match.end()], buffer[match.end() :]
            out = handle(_parse_event(raw))
            if out is not None:
                yield out
    if buffer.strip():
        out = handle(_parse_event(buffer))
        if out is not None:
            yield out

    amendment: Amendment | None = None
    reply = collector.reply()
    if reply.finish_reason is not None:
        try:
            amendment = await on_finish(reply)
        except Exception:  # noqa: BLE001 - fail-open: never break the model's reply
            log.exception("amending the upstream stream failed; passing it through unchanged")
            amendment = None
    if amendment is None:
        for ev in held:
            yield ev.raw
        return
    next_index = 0 if amendment.drop_tool_calls else len(reply.tool_calls)
    for raw in _amended_events(held, amendment, next_index):
        yield raw
