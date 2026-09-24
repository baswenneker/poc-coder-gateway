"""End-to-end tests of the Gateway app with a fake Decider and a mocked upstream.

The Gateway decides on the reply (DECISIONS.md #28): once at the end of a Turn, and when a tool call
matches a Block Rule's trigger. Most tests run streamed and non-streamed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from gateway_helpers import (
    ToolCallSpec,
    make_decision,
    make_workflow,
    parse_with_openai_sdk,
    upstream_json,
    upstream_sse,
)

from coder_gateway.app import create_app
from coder_gateway.config import DeciderConfig, GatewayConfig, UpstreamConfig
from coder_gateway.domain import Decider, Decision, DecisionInput, Message, VirtualModel
from coder_gateway.store import ConversationStore

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "sk-gw-test"
OTHER_KEY = "sk-gw-other"
AUTH = {"authorization": f"Bearer {KEY}"}
PR_CALL: ToolCallSpec = ("call_pr", "bash", json.dumps({"command": "gh pr create --fill"}))
LS_CALL: ToolCallSpec = ("call_ls", "bash", json.dumps({"command": "ls"}))
BLOCK_TEXT = "Blocked: tests must pass first."
STREAMS = pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])


class FakeDecider:
    """Returns scripted Decisions in order (the last one repeats); records its inputs."""

    name = "fake"

    def __init__(self, *decisions: Decision, delay: float = 0.0, exc: Exception | None = None) -> None:
        self.decisions = list(decisions) or [make_decision()]
        self.delay = delay
        self.exc = exc
        self.inputs: list[DecisionInput] = []

    async def decide(self, inp: DecisionInput) -> Decision:
        self.inputs.append(inp)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]


def identity_strategy(messages: list[Message], strategy: str) -> list[Message]:
    return [m for m in messages if m.get("role") != "system"]


class Reply:
    def __init__(
        self,
        content: str = "upstream",
        tool_calls: list[ToolCallSpec] | None = None,
        finish: str | None = None,
    ) -> None:
        self.content, self.tool_calls, self.finish = content, tool_calls, finish


class Upstream:
    """Mocked OpenAI. Answers with the queued Replies in order, then with plain text 'upstream'."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.status = 200
        self.replies: list[Reply] = []
        self.sent: list[bytes] = []  # the raw bodies upstream sent back

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "bad"}})
        reply = self.replies.pop(0) if self.replies else Reply()
        if body.get("stream"):
            include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
            data = upstream_sse(reply.content, reply.tool_calls, reply.finish, include_usage)
            self.sent.append(data)
            return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})
        data = json.dumps(upstream_json(reply.content, reply.tool_calls, reply.finish)).encode()
        self.sent.append(data)
        return httpx.Response(200, content=data, headers={"content-type": "application/json"})


def make_config(timeout_s: float = 1.0, dashboard_token: str | None = None) -> GatewayConfig:
    wf = make_workflow()
    return GatewayConfig(
        upstream=UpstreamConfig(base_url="https://up.test/v1", api_key="sk-upstream"),
        decider=DeciderConfig(timeout_s=timeout_s),
        virtual_models=(
            VirtualModel("fwd-coder", KEY, "gpt-5.4", "Follow the team workflow.", wf),
            VirtualModel("other", OTHER_KEY, "gpt-5.4-mini", "", wf),
        ),
        dashboard_token=dashboard_token,
    )


class Harness:
    def __init__(
        self,
        decider: Decider | None,
        timeout_s: float = 1.0,
        store: ConversationStore | None = None,
        dashboard_token: str | None = None,
    ) -> None:
        self.upstream = Upstream()
        self.store = store or ConversationStore()
        self.decider = decider
        app = create_app(
            make_config(timeout_s, dashboard_token),
            decider=decider,
            strategy_fn=identity_strategy,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.upstream.handler)),
            store=self.store,
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")

    async def chat(self, body: dict[str, Any], key: str = KEY, sid: str | None = "ses_1") -> httpx.Response:
        headers = {"authorization": f"Bearer {key}"}
        if sid:
            headers["x-session-id"] = sid
        return await self.client.post("/v1/chat/completions", json=body, headers=headers)

    async def reply(self, body: dict[str, Any], sid: str | None = "ses_1") -> dict[str, Any]:
        """Send a request and parse the Gateway's reply with the OpenAI SDK."""
        r = await self.chat(body, sid=sid)
        assert r.status_code == 200, r.text
        return parse_with_openai_sdk(r.content, stream=bool(body.get("stream")))

    def conv(self, sid: str = "ses_1") -> Any:
        conv = self.store.get("fwd-coder", f"sid:{sid}")
        assert conv is not None
        return conv


def agent_body(*messages: Message, stream: bool = False, question_tool: bool = True) -> dict[str, Any]:
    tools = [{"type": "function", "function": {"name": "bash", "parameters": {}}}]
    if question_tool:
        tools.append({"type": "function", "function": {"name": "question", "parameters": {}}})
    body: dict[str, Any] = {
        "model": "fwd-coder",
        "max_tokens": 1000,
        "messages": [{"role": "system", "content": "agent"}, *messages],
        "tools": tools,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def user(text: str) -> Message:
    return {"role": "user", "content": text}


def assistant(content: str = "", *calls: ToolCallSpec) -> Message:
    msg: Message = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [
            {"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in calls
        ]
    return msg


def tool_result(call_id: str, content: str) -> Message:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def answer(question: str, value: str) -> str:
    return f'User has answered your questions: "{question}"="{value}". You can now continue.'


# --- auth and passthrough ----------------------------------------------------------------------------


async def test_auth_required() -> None:
    h = Harness(FakeDecider())
    r = await h.client.post("/v1/chat/completions", json=agent_body(user("hi")))
    assert r.status_code == 401
    r = await h.chat(agent_body(user("hi")), key="sk-wrong")
    assert r.status_code == 401 and r.json()["error"]["type"] == "authentication_error"
    assert h.upstream.requests == []


async def test_models_listing() -> None:
    h = Harness(None)
    assert [m["id"] for m in (await h.client.get("/v1/models", headers=AUTH)).json()["data"]] == ["fwd-coder"]
    assert len((await h.client.get("/v1/models")).json()["data"]) == 2


async def test_title_request_passthrough_without_decision() -> None:
    decider = FakeDecider(make_decision("propose_issue"))
    h = Harness(decider)
    body = json.loads((FIXTURES / "opencode_title_request.json").read_text())
    r = await h.chat(body)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.content == h.upstream.sent[0]
    assert decider.inputs == []
    sent = h.upstream.requests[0]
    assert sent["model"] == "gpt-5.4"
    assert sent["messages"] == body["messages"]  # no system prompt injected
    assert "max_tokens" not in sent and sent["max_completion_tokens"] == 32000
    assert h.store.list_conversations() == []


async def test_subagent_request_passthrough_without_decision() -> None:
    # opencode's `task` tool starts a subagent session; its requests carry x-parent-session-id
    # (captured live with opencode 1.18.32). The main agent talks there, not the Developer.
    decider = FakeDecider(make_decision("propose_issue", "block_pr_without_tests"))
    h = Harness(decider)
    h.upstream.replies = [Reply("I'll open a PR.", [PR_CALL])]
    headers = {**AUTH, "x-session-id": "ses_child", "x-parent-session-id": "ses_parent"}
    r = await h.client.post(
        "/v1/chat/completions", json=agent_body(user("Zoek calc.py"), question_tool=False), headers=headers
    )
    assert r.status_code == 200 and r.content == h.upstream.sent[0].replace(b"gpt-5.4", b"fwd-coder")
    assert decider.inputs == []
    assert h.store.list_conversations() == []
    assert h.upstream.requests[0]["messages"][0] == {"role": "system", "content": "Follow the team workflow."}


async def test_forward_rewrites_request_and_uses_upstream_key() -> None:
    decider = FakeDecider()
    h = Harness(decider)
    body = json.loads((FIXTURES / "opencode_agent_request.json").read_text())
    r = await h.chat(body, sid="ses_fixture")
    assert r.status_code == 200 and r.content == h.upstream.sent[0]  # clean Decision: stream unchanged
    sent = h.upstream.requests[0]
    assert sent["model"] == "gpt-5.4"
    assert sent["messages"][0] == {"role": "system", "content": "Follow the team workflow."}
    assert sent["messages"][1:] == body["messages"]
    assert sent["max_completion_tokens"] == 32000 and "max_tokens" not in sent
    assert sent["tools"] == body["tools"] and sent["stream_options"] == {"include_usage": True}
    assert h.upstream.headers[0]["authorization"] == "Bearer sk-upstream"
    # One Decision at the end of the Turn: the Transcript (through the strategy) plus the final reply.
    [inp] = decider.inputs
    assert all(m["role"] != "system" for m in inp.transcript)
    assert inp.transcript[:-1] == body["messages"][1:]
    assert inp.transcript[-1] == {"role": "assistant", "content": "upstream"}
    assert inp.state["turn"] == 1


async def test_non_stream_forward_renames_model_and_passes_errors() -> None:
    decider = FakeDecider(make_decision("propose_issue"))
    h = Harness(decider)
    h.upstream.status = 400
    r = await h.chat(agent_body(user("hi")))
    assert r.status_code == 400 and r.json()["error"]["message"] == "bad"
    r = await h.chat(agent_body(user("hi"), stream=True))
    assert r.status_code == 400
    assert decider.inputs == []  # upstream error: no Decision
    h.upstream.status = 200
    r = await h.chat(agent_body(user("hi"), question_tool=False))
    assert r.json()["model"] == "fwd-coder"


async def test_fingerprint_when_no_session_header() -> None:
    h = Harness(FakeDecider())
    await h.chat(agent_body(user("build it")), sid=None)
    await h.chat(agent_body(user("build it"), assistant("ok"), user("more")), sid=None)
    convs = h.store.list_conversations()
    assert len(convs) == 1 and convs[0].id.startswith("fp:") and convs[0].turn == 2


# --- when the Decider is called ----------------------------------------------------------------------


@STREAMS
async def test_one_decision_per_turn_none_on_intermediate_requests(stream: bool) -> None:
    decider = FakeDecider()
    h = Harness(decider)
    test_call: ToolCallSpec = ("call_2", "bash", '{"command":"pytest"}')
    h.upstream.replies = [Reply("Looking first.", [LS_CALL]), Reply("", [test_call])]
    history: list[Message] = [user("voeg multiply toe")]
    first = await h.reply(agent_body(*history, stream=stream))
    assert first["tool_calls"] == [LS_CALL] and first["finish"] == ["tool_calls"]
    assert decider.inputs == []
    history += [assistant("Looking first.", LS_CALL), tool_result("call_ls", "calc.py")]
    await h.reply(agent_body(*history, stream=stream))
    assert decider.inputs == []
    history += [assistant("", test_call), tool_result("call_2", "2 passed")]
    last = await h.reply(agent_body(*history, stream=stream))
    assert last["content"] == "upstream" and last["finish"] == ["stop"]
    assert len(decider.inputs) == 1
    assert decider.inputs[0].transcript[-1] == {"role": "assistant", "content": "upstream"}
    conv = h.conv()
    assert (conv.requests, conv.decisions) == (3, 1)
    assert conv.last_decision["reason"] == "end_of_turn"
    status = (await h.client.get("/gateway/status", headers=AUTH)).json()
    assert (status["requests"], status["decisions"]) == (3, 1)


@STREAMS
async def test_other_finish_reasons_get_no_decision(stream: bool) -> None:
    decider = FakeDecider(make_decision("propose_issue"))
    h = Harness(decider)
    h.upstream.replies = [Reply("half a rep", finish="length")]
    r = await h.chat(agent_body(user("x"), stream=stream))
    assert decider.inputs == []
    reply = parse_with_openai_sdk(r.content, stream=stream)
    assert reply["content"] == "half a rep" and reply["finish"] == ["length"]


@STREAMS
async def test_tool_call_without_trigger_is_unchanged_without_decision(stream: bool) -> None:
    decider = FakeDecider(make_decision("block_pr_without_tests"))
    h = Harness(decider)
    h.upstream.replies = [Reply("Kijken.", [LS_CALL])]
    r = await h.chat(agent_body(user("x"), stream=stream))
    assert decider.inputs == []
    if stream:
        assert r.content == h.upstream.sent[0]
    else:
        assert r.json()["choices"][0]["message"]["tool_calls"][0]["id"] == "call_ls"


@STREAMS
async def test_request_with_more_than_one_choice_gets_no_decision(stream: bool) -> None:
    decider = FakeDecider(make_decision("propose_issue"))
    h = Harness(decider)
    r = await h.chat({**agent_body(user("x"), stream=stream), "n": 2})
    assert r.status_code == 200 and decider.inputs == []
    reply = parse_with_openai_sdk(r.content, stream=stream)
    assert reply["content"] == "upstream" and reply["tool_calls"] == []


# --- Flags ---------------------------------------------------------------------------------------------


async def test_flag_set_and_cleared_at_end_of_turn_without_effect_on_reply() -> None:
    h = Harness(FakeDecider(make_decision("flag_no_spec"), make_decision()))
    reply = await h.reply(agent_body(user("edit code")))
    assert reply["content"] == "upstream" and reply["finish"] == ["stop"]
    conv = h.conv()
    assert "flag_no_spec" in conv.flags
    flags = (await h.client.get("/gateway/flags", headers=AUTH)).json()
    assert flags[0]["rule_id"] == "flag_no_spec"
    await h.chat(agent_body(user("edit code"), assistant("upstream"), user("spec is SPEC.md")))
    assert conv.flags == {}


# --- Proposals -----------------------------------------------------------------------------------------


@STREAMS
async def test_proposal_tool_mode_appended_to_final_reply(stream: bool) -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))
    h.upstream.replies = [Reply("multiply is in there.")]
    reply = await h.reply(agent_body(user("add a feature"), stream=stream))
    assert reply["content"] == "multiply is in there."  # the model's own text is kept
    assert reply["finish"] == ["tool_calls"]
    [(call_id, name, args)] = reply["tool_calls"]
    assert call_id == "gateway_proposal_1" and name == "question"
    q = json.loads(args)["questions"][0]
    assert [o["label"] for o in q["options"]] == ["Yes, create an issue", "No, continue"]
    assert len(h.upstream.requests) == 1  # the model did answer
    status = (await h.client.get("/gateway/status", headers=AUTH)).json()
    assert status["open_proposals"][0]["id"] == "gateway_proposal_1"


@STREAMS
async def test_proposal_text_mode_appended_to_final_reply(stream: bool) -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))
    h.upstream.replies = [Reply("multiply is in there.")]
    reply = await h.reply(agent_body(user("add a feature"), stream=stream, question_tool=False))
    assert reply["content"] == "multiply is in there.\n\nShall we create an issue first? (answer yes or no)"
    assert reply["finish"] == ["stop"] and reply["tool_calls"] == []


async def test_proposal_tool_mode_once_per_turn_then_accepted() -> None:
    decider = FakeDecider(make_decision("propose_issue"))
    h = Harness(decider)
    first = await h.reply(agent_body(user("add a feature")))
    [(call_id, _, args)] = first["tool_calls"]
    question = json.loads(args)["questions"][0]["question"]

    # The client answers the question tool in the same Turn; the model continues and finishes again.
    # No second Decision this Turn: the Proposal of this Turn has been shown (DECISIONS.md #29).
    history: list[Message] = [
        user("add a feature"),
        assistant("upstream", (call_id, "question", args)),
        tool_result(call_id, answer(question, "Yes, create an issue")),
    ]
    second = await h.reply(agent_body(*history))
    assert second["content"] == "upstream" and second["tool_calls"] == []
    assert h.upstream.requests[1]["messages"][-1]["tool_call_id"] == "gateway_proposal_1"
    assert len(decider.inputs) == 1
    accepted = (await h.client.get("/gateway/proposals?status=accepted", headers=AUTH)).json()
    assert accepted[0]["id"] == "gateway_proposal_1"

    # Accepted: never again, even in a later Turn with the Rule still broken.
    third = await h.reply(agent_body(*history, assistant("upstream"), user("next")))
    assert third["tool_calls"] == [] and len(decider.inputs) == 2
    assert h.conv().decisions == 2


async def test_declined_proposal_returns_next_turn() -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))
    first = await h.reply(agent_body(user("add a feature")))
    [(call_id, _, args)] = first["tool_calls"]
    history: list[Message] = [
        user("add a feature"),
        assistant("upstream", (call_id, "question", args)),
        tool_result(call_id, answer("q", "No, continue")),
    ]
    again = await h.reply(agent_body(*history))
    assert again["tool_calls"] == []
    history += [assistant("upstream"), user("go on")]
    nxt = await h.reply(agent_body(*history))
    assert nxt["tool_calls"][0][0] == "gateway_proposal_2"
    props = (await h.client.get("/gateway/proposals", headers=AUTH)).json()
    assert [p["status"] for p in props] == ["declined", "open"]


async def test_proposal_text_mode_answer_in_next_turn() -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))
    first = await h.reply(agent_body(user("add a feature"), question_tool=False, stream=True))
    text = first["content"]
    assert text.endswith("(answer yes or no)")
    history = [user("add a feature"), assistant(text), user("nee")]
    reply = await h.reply(agent_body(*history, question_tool=False))
    assert reply["content"] == "upstream"  # declined in Turn 2: not asked again in Turn 2
    conv = h.conv()
    assert conv.proposals[0].status == "declined" and conv.proposals[0].answer == "nee"
    history += [assistant("upstream"), user("go on")]
    reply = await h.reply(agent_body(*history, question_tool=False))
    assert reply["content"].endswith("(answer yes or no)")


async def test_text_proposal_answered_after_history_compaction() -> None:
    # DECISIONS.md #25: the client compacts the history, so it carries fewer user messages
    # than before. A new Developer message must still start a new Turn and settle the Proposal.
    h = Harness(FakeDecider(make_decision("propose_issue")))
    history: list[Message] = [user("a"), assistant("x"), user("b"), assistant("y"), user("c")]
    text = (await h.reply(agent_body(*history, question_tool=False)))["content"]
    assert text.endswith("(answer yes or no)")
    compacted = [user("summary of a, b and c"), assistant(text), user("nee")]
    assert (await h.reply(agent_body(*compacted, question_tool=False)))["content"] == "upstream"
    conv = h.conv()
    assert conv.turn == 4
    assert conv.proposals[0].status == "declined" and conv.proposals[0].answer == "nee"
    compacted += [assistant("upstream"), user("go on")]
    reply = await h.reply(agent_body(*compacted, question_tool=False))
    assert reply["content"].endswith("(answer yes or no)")
    assert conv.turn == 5


# --- Block ---------------------------------------------------------------------------------------------


@STREAMS
async def test_trigger_match_and_broken_blocks_the_tool_call(stream: bool) -> None:
    # A broken propose Rule in the same Decision does not add a Proposal: that is for the end of a Turn.
    decider = FakeDecider(make_decision("block_pr_without_tests", "propose_issue"))
    h = Harness(decider)
    h.upstream.replies = [Reply("Ik maak de PR.", [PR_CALL])]
    r = await h.chat(agent_body(user("open a PR"), stream=stream))
    reply = parse_with_openai_sdk(r.content, stream=stream)
    assert reply == {
        **reply,
        "content": f"Ik maak de PR.\n\n{BLOCK_TEXT}",
        "tool_calls": [],
        "finish": ["stop"],
    }
    assert b"gh pr create" not in r.content
    [inp] = decider.inputs
    assert inp.transcript[-1]["tool_calls"][0]["function"]["arguments"] == PR_CALL[2]
    status = (await h.client.get("/gateway/status", headers=AUTH)).json()
    assert status["blocks"][0]["rule_id"] == "block_pr_without_tests"
    assert status["open_proposals"] == []
    assert status["last_decision"]["reason"] == "trigger:block_pr_without_tests"


@STREAMS
async def test_trigger_match_not_broken_passes_tool_call_unchanged(stream: bool) -> None:
    decider = FakeDecider(make_decision("propose_issue"))  # block Rule not broken
    h = Harness(decider)
    h.upstream.replies = [Reply("Ik maak de PR.", [PR_CALL])]
    r = await h.chat(agent_body(user("open a PR"), stream=stream))
    assert len(decider.inputs) == 1
    if stream:
        assert r.content == h.upstream.sent[0]  # every delta byte for byte
    else:
        assert r.json()["choices"][0] == json.loads(h.upstream.sent[0])["choices"][0]
    assert h.conv().blocks == [] and h.conv().proposals == []


@STREAMS
async def test_trigger_decider_timeout_fails_open(stream: bool) -> None:
    h = Harness(FakeDecider(make_decision("block_pr_without_tests"), delay=1.0), timeout_s=0.01)
    h.upstream.replies = [Reply("Ik maak de PR.", [PR_CALL])]
    r = await h.chat(agent_body(user("open a PR"), stream=stream))
    reply = parse_with_openai_sdk(r.content, stream=stream)
    assert reply["tool_calls"] == [PR_CALL] and reply["finish"] == ["tool_calls"]
    conv = h.conv()
    assert conv.last_decision["error"].startswith("timeout") and conv.blocks == []


async def test_after_block_next_turn_runs_normally() -> None:
    h = Harness(FakeDecider(make_decision("block_pr_without_tests"), make_decision()))
    h.upstream.replies = [Reply("", [PR_CALL])]
    blocked = await h.reply(agent_body(user("open a PR")))
    assert blocked["content"] == BLOCK_TEXT
    reply = await h.reply(agent_body(user("open a PR"), assistant(BLOCK_TEXT), user("run the tests")))
    assert reply["content"] == "upstream" and reply["finish"] == ["stop"]


# --- fail-open -----------------------------------------------------------------------------------------


@STREAMS
async def test_end_of_turn_decider_timeout_fails_open(stream: bool) -> None:
    h = Harness(FakeDecider(make_decision("propose_issue"), delay=1.0), timeout_s=0.01)
    r = await h.chat(agent_body(user("add a feature"), stream=stream))
    reply = parse_with_openai_sdk(r.content, stream=stream)
    assert reply["content"] == "upstream" and reply["tool_calls"] == [] and reply["finish"] == ["stop"]
    if stream:
        assert r.content == h.upstream.sent[0]
    conv = h.conv()
    assert conv.last_decision["error"].startswith("timeout")
    assert any(e["type"] == "decision_error" for e in conv.events)


async def test_decider_exception_fails_open() -> None:
    h = Harness(FakeDecider(exc=RuntimeError("jev down")))
    r = await h.chat(agent_body(user("open a PR"), stream=True))
    assert r.content == h.upstream.sent[0]
    assert "jev down" in h.conv().last_decision["error"]


async def test_strategy_failure_fails_open() -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))

    def broken_strategy(messages: list[Message], strategy: str) -> list[Message]:
        raise NotImplementedError

    app = create_app(
        make_config(),
        decider=h.decider,
        strategy_fn=broken_strategy,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(h.upstream.handler)),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")
    r = await client.post("/v1/chat/completions", json=agent_body(user("x")), headers=AUTH)
    assert r.json()["choices"][0]["message"]["content"] == "upstream"


async def test_no_decider_forwards() -> None:
    h = Harness(None)
    r = await h.chat(agent_body(user("x")))
    assert r.json()["choices"][0]["message"]["content"] == "upstream"
    conv = h.conv()
    assert conv.last_decision is None and conv.decisions == 0


# --- concurrency and robustness ---------------------------------------------------------------------------


async def test_stale_request_does_not_overwrite_newer_turn() -> None:
    # A Turn 1 reply that is still in the Decider must not restore a Flag or
    # record its Proposal against Turn 2. The per-Conversation lock serialises them (DECISIONS.md #24).
    class GatedDecider:
        name = "gated"

        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def decide(self, inp: DecisionInput) -> Decision:
            if inp.transcript[-2]["content"] == "turn one":
                self.entered.set()
                await self.release.wait()
                return make_decision("flag_no_spec", "propose_issue")
            return make_decision()

    decider = GatedDecider()
    h = Harness(decider, timeout_s=5.0)
    first = asyncio.create_task(h.chat(agent_body(user("turn one"))))
    await decider.entered.wait()
    second = asyncio.create_task(h.chat(agent_body(user("turn one"), assistant("ok"), user("turn two"))))
    await asyncio.sleep(0.05)  # without serialisation Turn 2 completes here
    assert not second.done()
    decider.release.set()
    _, r2 = await asyncio.gather(first, second)
    assert r2.json()["choices"][0]["message"]["content"] == "upstream"
    conv = h.conv()
    assert conv.turn == 2
    assert conv.flags == {}
    assert conv.last_decision is not None and conv.last_decision["broken"] == []
    assert all(p.turn == 1 for p in conv.proposals)


async def test_reply_of_an_older_turn_gets_no_decision() -> None:
    # A reply that finishes after the Developer already started a new Turn is not judged.
    decider = FakeDecider(make_decision("propose_issue"))
    h = Harness(decider)
    gate = asyncio.Event()
    handler = h.upstream.handler

    async def slow_first(request: httpx.Request) -> httpx.Response:
        if len(h.upstream.requests) == 0:
            response = handler(request)
            await gate.wait()
            return response
        return handler(request)

    app = create_app(
        make_config(),
        decider=decider,
        strategy_fn=identity_strategy,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(slow_first)),
        store=h.store,
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")
    headers = {**AUTH, "x-session-id": "ses_1"}
    first = asyncio.create_task(
        client.post("/v1/chat/completions", json=agent_body(user("one")), headers=headers)
    )
    await asyncio.sleep(0.05)
    await client.post(
        "/v1/chat/completions", json=agent_body(user("one"), assistant("x"), user("two")), headers=headers
    )
    gate.set()
    r1 = await first
    assert r1.json()["choices"][0]["finish_reason"] == "stop"  # no Proposal on the stale reply
    assert len(decider.inputs) == 1 and h.conv().proposals[0].turn == 2


async def test_event_log_write_failure_does_not_abort_request(tmp_path: Path) -> None:
    # DECISIONS.md #26.
    path = tmp_path / "events.jsonl"
    store = ConversationStore(events_path=path)
    path.mkdir()  # appending to it now fails with an OSError
    h = Harness(FakeDecider(make_decision("flag_no_spec")), store=store)
    r = await h.chat(agent_body(user("x")))
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "upstream"
    assert "flag_no_spec" in h.conv().flags


# --- read API ------------------------------------------------------------------------------------------


async def test_read_api_scoped_to_virtual_model() -> None:
    h = Harness(FakeDecider(make_decision("flag_no_spec")))
    await h.chat(agent_body(user("x")), sid="mine")
    await h.chat(agent_body(user("y")), key=OTHER_KEY, sid="theirs")
    other = {"authorization": f"Bearer {OTHER_KEY}"}
    assert (await h.client.get("/gateway/conversations")).status_code == 401
    mine = (await h.client.get("/gateway/conversations", headers=AUTH)).json()
    assert [c["id"] for c in mine] == ["sid:mine"] and mine[0]["decisions"] == 1
    assert (await h.client.get("/gateway/conversations/sid:theirs", headers=AUTH)).status_code == 404
    detail = (await h.client.get("/gateway/conversations/sid:theirs", headers=other)).json()
    assert detail["virtual_model"] == "other" and detail["events"]
    assert [f["conversation"] for f in (await h.client.get("/gateway/flags", headers=other)).json()] == [
        "sid:theirs"
    ]
    status = (await h.client.get("/gateway/status", headers=AUTH)).json()
    assert status["conversation"] == "sid:mine" and status["last_decision"]["broken"] == ["flag_no_spec"]
    assert (await h.client.get("/gateway/proposals?status=bogus", headers=AUTH)).status_code == 400


async def test_dashboard_and_health() -> None:
    h = Harness(FakeDecider(make_decision("flag_no_spec")))
    await h.chat(agent_body(user("<script>x</script>")), sid="<b>x</b>")
    r = await h.client.get("/gateway/")
    assert r.status_code == 200 and "flag_no_spec" in r.text
    assert "1 requests, 1 decisions" in r.text and "end_of_turn" in r.text
    assert "<b>x</b>" not in r.text and "&lt;b&gt;x&lt;/b&gt;" in r.text
    assert (await h.client.get("/healthz")).json() == {"status": "ok"}


async def test_dashboard_token_when_configured() -> None:
    # DECISIONS.md #27: with `dashboard_token` set, /gateway/ needs ?token=.
    h = Harness(FakeDecider(), dashboard_token="s3cret")
    assert (await h.client.get("/gateway/")).status_code == 401
    assert (await h.client.get("/gateway/?token=wrong")).status_code == 401
    assert (await h.client.get("/gateway/", headers=AUTH)).status_code == 401
    r = await h.client.get("/gateway/?token=s3cret")
    assert r.status_code == 200 and "Coder Gateway" in r.text
