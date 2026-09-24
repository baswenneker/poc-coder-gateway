"""Reading and amending the upstream reply (coder_gateway.reply) and the Intervention amendments.

Every amended reply must still parse with the official OpenAI SDK, streamed and non-streamed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from gateway_helpers import make_workflow, parse_with_openai_sdk, upstream_json, upstream_sse

from coder_gateway.domain import ProposalSpec
from coder_gateway.interventions import (
    TEXT_MODE_SUFFIX,
    block_amendment,
    proposal_text_amendment,
    proposal_tool_amendment,
)
from coder_gateway.reply import (
    Amendment,
    AssistantReply,
    amend_completion,
    inspect_stream,
    reply_from_completion,
)

SPEC = ProposalSpec(question="An issue?", header="Issue", accept_label="Yes", decline_label="No")
PR_CALL = ("call_1", "bash", json.dumps({"command": "gh pr create --fill"}))


async def _source(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    # Odd-sized pieces: events are split across network chunks.
    for i in range(0, len(data), size):
        yield data[i : i + size]


async def _run(data: bytes, amendment: Amendment | None) -> tuple[bytes, list[AssistantReply]]:
    seen: list[AssistantReply] = []

    async def on_finish(reply: AssistantReply) -> Amendment | None:
        seen.append(reply)
        return amendment

    out = b"".join([piece async for piece in inspect_stream(_source(data), on_finish)])
    return out, seen


async def test_stream_without_amendment_is_unchanged() -> None:
    for data in (upstream_sse("Done."), upstream_sse("I'll open a PR.", [PR_CALL])):
        out, seen = await _run(data, None)
        assert out == data
        assert len(seen) == 1


async def test_stream_collects_the_reply() -> None:
    calls = [PR_CALL, ("call_2", "read", '{"filePath": "a.py"}')]
    _, seen = await _run(upstream_sse("Reading first.", calls), None)
    reply = seen[0]
    assert reply.content == "Reading first." and reply.finish_reason == "tool_calls"
    assert [(tc.id, tc.name, tc.arguments) for tc in reply.tool_calls] == calls
    assert reply.as_message()["tool_calls"][0]["function"]["name"] == "bash"


async def test_text_streams_live_before_tool_calls_are_complete() -> None:
    data = upstream_sse("I'll open a PR.", [PR_CALL])
    events = [e + b"\n\n" for e in data.split(b"\n\n") if e]
    pulled: list[int] = []

    async def source() -> AsyncIterator[bytes]:
        for i, e in enumerate(events):
            pulled.append(i)
            yield e

    async def on_finish(reply: AssistantReply) -> Amendment | None:
        return None

    stream = inspect_stream(source(), on_finish)
    first = [await stream.__anext__() for _ in range(3)]  # role chunk + two text deltas
    assert b"".join(first) == b"".join(events[:3])
    assert max(pulled) == 2  # nothing further was read from upstream yet
    await stream.aclose()


async def test_stream_proposal_tool_call_appended() -> None:
    amendment = proposal_tool_amendment(SPEC, "gateway_proposal_1")
    out, _ = await _run(upstream_sse("Done: added multiply."), amendment)
    parsed = parse_with_openai_sdk(out, stream=True)
    assert parsed["content"] == "Done: added multiply."
    assert parsed["finish"] == ["tool_calls"]
    [(call_id, name, args)] = parsed["tool_calls"]
    assert call_id == "gateway_proposal_1" and name == "question"
    q = json.loads(args)["questions"][0]
    assert q["question"] == "An issue?" and [o["label"] for o in q["options"]] == ["Yes", "No"]
    assert parsed["usage"] == 15 and out.endswith(b"data: [DONE]\n\n")


async def test_stream_proposal_text_appended() -> None:
    out, _ = await _run(upstream_sse("Done."), proposal_text_amendment(SPEC, "Done."))
    parsed = parse_with_openai_sdk(out, stream=True)
    assert parsed["content"] == f"Done.\n\nAn issue? {TEXT_MODE_SUFFIX}"
    assert parsed["finish"] == ["stop"] and parsed["tool_calls"] == []


async def test_stream_block_replaces_tool_call() -> None:
    rule = make_workflow().rule("block_pr_without_tests")
    out, _ = await _run(upstream_sse("I'll open a PR.", [PR_CALL]), block_amendment(rule, "I'll open a PR."))
    parsed = parse_with_openai_sdk(out, stream=True)
    assert parsed["tool_calls"] == []
    assert parsed["content"] == "I'll open a PR.\n\nBlocked: tests must pass first."
    assert parsed["finish"] == ["stop"]
    assert b"gh pr create" not in out


async def test_stream_on_finish_error_fails_open() -> None:
    data = upstream_sse("I'll open a PR.", [PR_CALL])

    async def on_finish(reply: AssistantReply) -> Amendment | None:
        raise RuntimeError("boom")

    out = b"".join([p async for p in inspect_stream(_source(data), on_finish)])
    assert out == data


async def test_stream_without_finish_skips_on_finish() -> None:
    data = upstream_sse("half")
    cut = data.split(b'"finish_reason": "stop"')[0].rsplit(b"data: ", 1)[0]
    out, seen = await _run(cut, proposal_text_amendment(SPEC, ""))
    assert out == cut and seen == []


def test_json_reply_and_amendments() -> None:
    data = upstream_json("I'll open a PR.", [PR_CALL])
    reply = reply_from_completion(data)
    assert reply is not None and reply.finish_reason == "tool_calls" and reply.tool_calls[0].name == "bash"
    rule = make_workflow().rule("block_pr_without_tests")
    blocked = amend_completion(data, block_amendment(rule, reply.content))
    parsed = parse_with_openai_sdk(json.dumps(blocked).encode(), stream=False)
    assert parsed == {
        "content": "I'll open a PR.\n\nBlocked: tests must pass first.",
        "tool_calls": [],
        "finish": ["stop"],
    }

    proposed = amend_completion(upstream_json("Done."), proposal_tool_amendment(SPEC, "gateway_proposal_3"))
    parsed = parse_with_openai_sdk(json.dumps(proposed).encode(), stream=False)
    assert parsed["content"] == "Done." and parsed["finish"] == ["tool_calls"]
    assert parsed["tool_calls"][0][:2] == ("gateway_proposal_3", "question")


def test_text_amendment_without_model_text_has_no_leading_blank_line() -> None:
    assert proposal_text_amendment(SPEC, "").append_text == f"An issue? {TEXT_MODE_SUFFIX}"
    assert proposal_text_amendment(SPEC, "x").append_text.startswith("\n\n")


# --- Edge cases ---------------------------------------------------------------------------------------------


def _chunk(index: int, delta: dict[str, object], finish: str | None = None) -> bytes:
    choice = {"index": index, "delta": delta, "finish_reason": finish}
    chunk = {"id": "c", "object": "chat.completion.chunk", "choices": [choice]}
    return f"data: {json.dumps(chunk)}\n\n".encode()


async def test_stream_with_more_than_one_choice_gets_no_decision() -> None:
    data = b"".join(
        [
            _chunk(0, {"role": "assistant", "content": "A"}),
            _chunk(1, {"role": "assistant", "content": "B"}),
            _chunk(0, {}, "stop"),
            _chunk(1, {}, "stop"),
            b"data: [DONE]\n\n",
        ]
    )
    out, seen = await _run(data, proposal_text_amendment(SPEC, "A"))
    assert out == data and seen == []


def test_json_with_more_than_one_choice_has_no_reply() -> None:
    data = upstream_json("A")
    data["choices"].append({**data["choices"][0], "index": 1})
    assert reply_from_completion(data) is None


async def test_stream_text_in_finish_chunk_comes_before_the_amendment() -> None:
    data = upstream_sse("Done").replace(
        b'"delta": {}, "finish_reason": "stop"', b'"delta": {"content": "."}, "finish_reason": "stop"'
    )
    out, seen = await _run(data, proposal_text_amendment(SPEC, "Done."))
    assert seen[0].content == "Done."
    parsed = parse_with_openai_sdk(out, stream=True)
    assert parsed["content"] == f"Done.\n\nAn issue? {TEXT_MODE_SUFFIX}"
    assert parsed["finish"] == ["stop"] and parsed["usage"] == 15


async def test_stream_upstream_error_flushes_held_events_and_propagates() -> None:
    data = upstream_sse("I'll open a PR.", [PR_CALL])
    cut = data.split(b"data: [DONE]")[0] + b"data: [DO"
    seen: list[AssistantReply] = []

    async def source() -> AsyncIterator[bytes]:
        async for piece in _source(cut):
            yield piece
        raise ConnectionError("upstream gone")

    async def on_finish(reply: AssistantReply) -> Amendment | None:
        seen.append(reply)
        return None

    out: list[bytes] = []
    with pytest.raises(ConnectionError):
        async for piece in inspect_stream(source(), on_finish):
            out.append(piece)
    assert b"".join(out) == cut and seen == []


async def test_stream_error_event_after_finish_gets_no_decision() -> None:
    error = b'data: {"error": {"message": "overloaded"}}\n\n'
    data = upstream_sse("Done.").replace(b"data: [DONE]", error + b"data: [DONE]")
    out, seen = await _run(data, proposal_text_amendment(SPEC, "Done."))
    assert out == data and seen == []


async def test_stream_trailing_whitespace_is_kept() -> None:
    data = upstream_sse("Done.") + b"\n"
    out, _ = await _run(data, None)
    assert out == data
