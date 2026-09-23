"""Gateway-made replies must parse with the official OpenAI SDK, streamed and non-streamed."""

import json

import httpx2
import openai
from gateway_helpers import make_workflow

from coder_gateway.domain import ProposalSpec
from coder_gateway.interventions import (
    TEXT_MODE_SUFFIX,
    block_reply,
    completion_json,
    completion_sse,
    proposal_text_reply,
    proposal_tool_reply,
)

SPEC = ProposalSpec(question="Eerst een issue?", header="Issue", accept_label="Ja", decline_label="Nee")


def _client(body: bytes, content_type: str) -> openai.OpenAI:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": content_type})

    return openai.OpenAI(
        api_key="x",
        base_url="http://gw/v1",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_tool_proposal_non_stream_parses() -> None:
    body = json.dumps(completion_json(proposal_tool_reply(SPEC, "gateway_proposal_1"), "fwd-coder")).encode()
    resp = _client(body, "application/json").chat.completions.create(model="fwd-coder", messages=[])
    choice = resp.choices[0]
    assert choice.finish_reason == "tool_calls"
    tc = choice.message.tool_calls[0]  # type: ignore[index]
    assert tc.id == "gateway_proposal_1"
    assert tc.function.name == "question"  # type: ignore[union-attr]
    args = json.loads(tc.function.arguments)  # type: ignore[union-attr]
    q = args["questions"][0]
    assert q["question"] == "Eerst een issue?" and q["header"] == "Issue"
    assert [o["label"] for o in q["options"]] == ["Ja", "Nee"]


def test_tool_proposal_stream_parses_with_usage() -> None:
    events = completion_sse(proposal_tool_reply(SPEC, "gateway_proposal_2"), "fwd-coder", include_usage=True)
    assert all(e.startswith(b"data: ") and e.endswith(b"\n\n") for e in events)
    assert events[-1] == b"data: [DONE]\n\n"
    stream = _client(b"".join(events), "text/event-stream").chat.completions.create(
        model="fwd-coder", messages=[], stream=True
    )
    chunks = list(stream)
    content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    tool_deltas = [tc for c in chunks if c.choices for tc in (c.choices[0].delta.tool_calls or [])]
    finish = [c.choices[0].finish_reason for c in chunks if c.choices and c.choices[0].finish_reason]
    assert content
    assert len(tool_deltas) == 1 and tool_deltas[0].index == 0 and tool_deltas[0].id == "gateway_proposal_2"
    assert json.loads(tool_deltas[0].function.arguments)["questions"]  # type: ignore[union-attr,arg-type]
    assert finish == ["tool_calls"]
    assert chunks[-1].usage is not None and chunks[-1].usage.total_tokens == 0


def test_text_proposal_and_block_stream() -> None:
    reply = proposal_text_reply(SPEC)
    assert reply.content.endswith(TEXT_MODE_SUFFIX) and reply.finish_reason == "stop"
    rule = make_workflow().rule("block_pr_without_tests")
    events = completion_sse(block_reply(rule), "fwd-coder")
    chunks = list(
        _client(b"".join(events), "text/event-stream").chat.completions.create(
            model="m", messages=[], stream=True
        )
    )
    assert "".join(c.choices[0].delta.content or "" for c in chunks) == "Geblokkeerd: eerst tests groen."
    assert chunks[-1].choices[0].finish_reason == "stop"
    assert all(c.usage is None for c in chunks)  # no usage chunk unless asked
