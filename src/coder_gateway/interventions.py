"""Replies the Gateway produces itself (Proposal, Block), as chat.completion JSON or as an SSE stream.

The stream mimics OpenAI's chat.completion.chunk format so the OpenAI SDK and the Vercel ai-sdk
openai-compatible provider (used by opencode) parse it like a normal model reply.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from coder_gateway.domain import ProposalSpec, Rule

QUESTION_TOOL = "question"
TOOL_MODE_INTRO = "Voordat ik verder ga heeft de Gateway een vraag."
TEXT_MODE_SUFFIX = "(antwoord ja of nee)"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string, as in the OpenAI format


@dataclass(frozen=True)
class GatewayReply:
    content: str
    finish_reason: str  # 'stop' | 'tool_calls'
    tool_call: ToolCall | None = None


def proposal_tool_reply(spec: ProposalSpec, proposal_id: str) -> GatewayReply:
    """Proposal as a call to the client's `question` tool (opencode shows it with choice buttons)."""
    args = {
        "questions": [
            {
                "question": spec.question,
                "header": spec.header,
                "options": [
                    {"label": spec.accept_label, "description": spec.accept_description},
                    {"label": spec.decline_label, "description": spec.decline_description},
                ],
            }
        ]
    }
    return GatewayReply(
        content=TOOL_MODE_INTRO,
        finish_reason="tool_calls",
        tool_call=ToolCall(
            id=proposal_id, name=QUESTION_TOOL, arguments=json.dumps(args, ensure_ascii=False)
        ),
    )


def proposal_text_reply(spec: ProposalSpec) -> GatewayReply:
    """Proposal as plain assistant text, for clients without a `question` tool."""
    return GatewayReply(content=f"{spec.question.strip()}\n\n{TEXT_MODE_SUFFIX}", finish_reason="stop")


def block_reply(rule: Rule) -> GatewayReply:
    return GatewayReply(
        content=(rule.block_message or f"Blocked by rule {rule.id}.").strip(), finish_reason="stop"
    )


def _completion_id() -> str:
    return f"chatcmpl-gw-{uuid.uuid4().hex[:24]}"


def _zero_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _tool_call_json(tc: ToolCall) -> dict[str, Any]:
    return {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}


def completion_json(reply: GatewayReply, model: str) -> dict[str, Any]:
    """Non-streaming chat.completion object."""
    message: dict[str, Any] = {"role": "assistant", "content": reply.content, "refusal": None}
    if reply.tool_call is not None:
        message["tool_calls"] = [_tool_call_json(reply.tool_call)]
    return {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "logprobs": None, "finish_reason": reply.finish_reason}],
        "usage": _zero_usage(),
    }


def completion_sse(reply: GatewayReply, model: str, include_usage: bool = False) -> list[bytes]:
    """Streaming reply as SSE events: role+content, tool call (index 0), finish, [usage], [DONE]."""
    cid = _completion_id()
    created = int(time.time())

    def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
        return {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish_reason}],
        }

    chunks: list[dict[str, Any]] = [chunk({"role": "assistant", "content": reply.content})]
    if reply.tool_call is not None:
        chunks.append(chunk({"tool_calls": [{"index": 0, **_tool_call_json(reply.tool_call)}]}))
    chunks.append(chunk({}, reply.finish_reason))
    if include_usage:
        chunks.append(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": _zero_usage(),
            }
        )
    events = [f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode() for c in chunks]
    events.append(b"data: [DONE]\n\n")
    return events
