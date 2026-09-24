"""Shared test helpers: a small Workflow Definition and Decision builders."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import openai

from coder_gateway.domain import Decision, RuleVerdict, WorkflowDefinition
from coder_gateway.workflow import parse_workflow

WORKFLOW_DATA = {
    "name": "test",
    "rules": [
        {
            "id": "propose_issue",
            "description": "Work starts from an issue.",
            "broken_when": "no issue",
            "intervention": "propose",
            "proposal": {
                "question": "Shall we create an issue first?",
                "header": "Issue first?",
                "accept_label": "Yes, create an issue",
                "decline_label": "No, continue",
            },
        },
        {
            "id": "flag_no_spec",
            "description": "Spec first.",
            "broken_when": "no spec",
            "intervention": "flag",
        },
        {
            "id": "block_pr_without_tests",
            "description": "Green tests before PR.",
            "broken_when": "pr without tests",
            "intervention": "block",
            "block_message": "Blocked: tests must pass first.",
            "trigger": {"pattern": "gh pr create|git push"},
        },
    ],
}


def make_workflow() -> WorkflowDefinition:
    return parse_workflow(WORKFLOW_DATA)


def make_decision(*broken: str, phase: str | None = "implement", error: str | None = None) -> Decision:
    rule_ids = ("propose_issue", "flag_no_spec", "block_pr_without_tests")
    verdicts = {rid: RuleVerdict(rid, 0.9 if rid in broken else 0.1, rid in broken) for rid in rule_ids}
    return Decision(verdicts=verdicts, decider="fake", latency_ms=1.0, phase=phase, error=error)


# --- upstream replies, as OpenAI would send them ---------------------------------------------------------

ToolCallSpec = tuple[str, str, str]  # (id, name, arguments JSON string)


def _default_finish(tool_calls: list[ToolCallSpec] | None) -> str:
    return "tool_calls" if tool_calls else "stop"


def upstream_json(
    content: str = "upstream", tool_calls: list[ToolCallSpec] | None = None, finish: str | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        message["tool_calls"] = [
            {"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in tool_calls
        ]
    return {
        "id": "chatcmpl-up",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-5.4",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish or ("tool_calls" if tool_calls else "stop"),
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def upstream_sse(
    content: str = "upstream",
    tool_calls: list[ToolCallSpec] | None = None,
    finish: str | None = None,
    include_usage: bool = True,
) -> bytes:
    """An OpenAI-shaped chat.completion.chunk stream: text in two deltas, each tool call as a header
    delta plus two argument deltas, a finish chunk, an optional usage chunk and [DONE]."""

    def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
        return {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-5.4",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    chunks = [chunk({"role": "assistant", "content": ""})]
    half = len(content) // 2
    for part in (content[:half], content[half:]):
        if part:
            chunks.append(chunk({"content": part}))
    for index, (call_id, name, args) in enumerate(tool_calls or []):
        head = {
            "index": index,
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": ""},
        }
        chunks.append(chunk({"tool_calls": [head]}))
        cut = len(args) // 2
        for part in (args[:cut], args[cut:]):
            chunks.append(chunk({"tool_calls": [{"index": index, "function": {"arguments": part}}]}))
    chunks.append(chunk({}, finish or _default_finish(tool_calls)))
    if include_usage:
        chunks.append(
            {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "gpt-5.4",
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
    events = [f"data: {json.dumps(c)}\n\n".encode() for c in chunks]
    return b"".join(events) + b"data: [DONE]\n\n"


def parse_with_openai_sdk(body: bytes, stream: bool) -> dict[str, Any]:
    """Parse a Gateway reply with the official OpenAI SDK; return content, tool calls and finish."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        ctype = "text/event-stream" if stream else "application/json"
        return httpx2.Response(200, content=body, headers={"content-type": ctype})

    client = openai.OpenAI(
        api_key="x",
        base_url="http://gw/v1",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    if not stream:
        resp = client.chat.completions.create(model="m", messages=[])
        choice = resp.choices[0]
        return {
            "content": choice.message.content or "",
            "tool_calls": [
                (tc.id, tc.function.name, tc.function.arguments)  # type: ignore[union-attr]
                for tc in choice.message.tool_calls or []
            ],
            "finish": [choice.finish_reason],
        }
    chunks = list(client.chat.completions.create(model="m", messages=[], stream=True))
    content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    calls: dict[int, list[str]] = {}
    for c in chunks:
        for tc in (c.choices[0].delta.tool_calls or []) if c.choices else []:
            entry = calls.setdefault(tc.index, ["", "", ""])
            entry[0] = tc.id or entry[0]
            if tc.function is not None:
                entry[1] += tc.function.name or ""
                entry[2] += tc.function.arguments or ""
    finish = [c.choices[0].finish_reason for c in chunks if c.choices and c.choices[0].finish_reason]
    return {
        "content": content,
        "tool_calls": [tuple(calls[i]) for i in sorted(calls)],
        "finish": finish,
        "usage": chunks[-1].usage.total_tokens if chunks and chunks[-1].usage else None,
    }
