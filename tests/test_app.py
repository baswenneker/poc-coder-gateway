"""End-to-end tests of the Gateway app with a fake Decider and a mocked upstream."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from gateway_helpers import make_decision, make_workflow

from coder_gateway.app import create_app
from coder_gateway.config import DeciderConfig, GatewayConfig, UpstreamConfig
from coder_gateway.domain import Decision, DecisionInput, Message, VirtualModel
from coder_gateway.store import ConversationStore

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "sk-gw-test"
OTHER_KEY = "sk-gw-other"
AUTH = {"authorization": f"Bearer {KEY}"}
UPSTREAM_JSON = {
    "id": "chatcmpl-up",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-5.4",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "upstream"}, "finish_reason": "stop"}
    ],
}
UPSTREAM_SSE = (
    b'data: {"id":"c","object":"chat.completion.chunk","created":1,"model":"gpt-5.4",'
    b'"choices":[{"index":0,"delta":{"role":"assistant","content":"up"},"finish_reason":null}]}\n\n'
    b"data: [DONE]\n\n"
)


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


class Upstream:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "bad"}})
        if body.get("stream"):
            return httpx.Response(200, content=UPSTREAM_SSE, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=UPSTREAM_JSON)


def make_config(timeout_s: float = 1.0) -> GatewayConfig:
    wf = make_workflow()
    return GatewayConfig(
        upstream=UpstreamConfig(base_url="https://up.test/v1", api_key="sk-upstream"),
        decider=DeciderConfig(timeout_s=timeout_s),
        virtual_models=(
            VirtualModel("fwd-coder", KEY, "gpt-5.4", "Follow the team workflow.", wf),
            VirtualModel("other", OTHER_KEY, "gpt-5.4-mini", "", wf),
        ),
    )


class Harness:
    def __init__(self, decider: FakeDecider | None, timeout_s: float = 1.0) -> None:
        self.upstream = Upstream()
        self.store = ConversationStore()
        self.decider = decider
        app = create_app(
            make_config(timeout_s),
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


def sse_chunks(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.split("\n\n"):
        if block.startswith("data: ") and block != "data: [DONE]":
            out.append(json.loads(block[len("data: ") :]))
    return out


def user(text: str) -> Message:
    return {"role": "user", "content": text}


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
    decider = FakeDecider(make_decision("block_pr_without_tests"))
    h = Harness(decider)
    body = json.loads((FIXTURES / "opencode_title_request.json").read_text())
    r = await h.chat(body)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.content == UPSTREAM_SSE
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
    headers = {**AUTH, "x-session-id": "ses_child", "x-parent-session-id": "ses_parent"}
    r = await h.client.post(
        "/v1/chat/completions", json=agent_body(user("Zoek calc.py"), question_tool=False), headers=headers
    )
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "upstream"
    assert decider.inputs == []
    assert h.store.list_conversations() == []
    assert h.upstream.requests[0]["messages"][0] == {"role": "system", "content": "Follow the team workflow."}


async def test_forward_rewrites_request_and_uses_upstream_key() -> None:
    decider = FakeDecider()
    h = Harness(decider)
    body = json.loads((FIXTURES / "opencode_agent_request.json").read_text())
    r = await h.chat(body, sid="ses_fixture")
    assert r.status_code == 200 and r.content == UPSTREAM_SSE
    sent = h.upstream.requests[0]
    assert sent["model"] == "gpt-5.4"
    assert sent["messages"][0] == {"role": "system", "content": "Follow the team workflow."}
    assert sent["messages"][1:] == body["messages"]
    assert sent["max_completion_tokens"] == 32000 and "max_tokens" not in sent
    assert sent["tools"] == body["tools"] and sent["stream_options"] == {"include_usage": True}
    assert h.upstream.headers[0]["authorization"] == "Bearer sk-upstream"
    # The Decider saw the Transcript (through the strategy) and the state.
    inp = decider.inputs[0]
    assert all(m["role"] != "system" for m in inp.transcript)
    assert inp.state["turn"] == 1
    assert h.store.get("fwd-coder", "sid:ses_fixture") is not None


async def test_non_stream_forward_renames_model_and_passes_errors() -> None:
    h = Harness(FakeDecider())
    r = await h.chat(agent_body(user("hi")))
    assert r.json()["model"] == "fwd-coder" and r.json()["choices"][0]["message"]["content"] == "upstream"
    h.upstream.status = 400
    r = await h.chat(agent_body(user("hi")))
    assert r.status_code == 400 and r.json()["error"]["message"] == "bad"
    r = await h.chat(agent_body(user("hi"), stream=True))
    assert r.status_code == 400


async def test_fingerprint_when_no_session_header() -> None:
    h = Harness(FakeDecider())
    await h.chat(agent_body(user("build it")), sid=None)
    await h.chat(agent_body(user("build it"), {"role": "assistant", "content": "ok"}, user("more")), sid=None)
    convs = h.store.list_conversations()
    assert len(convs) == 1 and convs[0].id.startswith("fp:") and convs[0].turn == 2


# --- Flags ---------------------------------------------------------------------------------------------


async def test_flag_set_and_cleared_without_effect_on_reply() -> None:
    h = Harness(FakeDecider(make_decision("flag_no_spec"), make_decision()))
    r = await h.chat(agent_body(user("edit code")))
    assert r.json()["choices"][0]["message"]["content"] == "upstream"
    conv = h.store.get("fwd-coder", "sid:ses_1")
    assert conv is not None and "flag_no_spec" in conv.flags
    flags = (await h.client.get("/gateway/flags", headers=AUTH)).json()
    assert flags[0]["rule_id"] == "flag_no_spec"
    await h.chat(agent_body(user("edit code"), user("spec is SPEC.md")))
    assert conv.flags == {}


# --- Proposals -----------------------------------------------------------------------------------------


async def test_proposal_tool_mode_once_per_turn_then_accepted() -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))
    first = agent_body(user("add a feature"))
    r = await h.chat(first)
    data = r.json()
    msg = data["choices"][0]["message"]
    assert data["choices"][0]["finish_reason"] == "tool_calls"
    tc = msg["tool_calls"][0]
    assert tc["id"] == "gateway_proposal_1" and tc["function"]["name"] == "question"
    assert (
        json.loads(tc["function"]["arguments"])["questions"][0]["options"][0]["label"] == "Ja, maak een issue"
    )
    assert h.upstream.requests == []  # the Proposal replaces the upstream reply

    # The client answers the question tool in the same Turn: goes upstream, no second Proposal.
    answered = agent_body(
        user("add a feature"),
        {"role": "assistant", "content": msg["content"], "tool_calls": [tc]},
        {"role": "tool", "tool_call_id": tc["id"], "content": 'User answered: "..."="Ja, maak een issue"'},
    )
    r = await h.chat(answered)
    assert r.json()["choices"][0]["message"]["content"] == "upstream"
    assert h.upstream.requests[0]["messages"][-1]["tool_call_id"] == "gateway_proposal_1"
    open_props = (await h.client.get("/gateway/proposals?status=open", headers=AUTH)).json()
    assert open_props == []
    accepted = (await h.client.get("/gateway/proposals?status=accepted", headers=AUTH)).json()
    assert accepted[0]["id"] == "gateway_proposal_1"

    # Accepted: never again, even in a later Turn with the Rule still broken.
    r = await h.chat(
        agent_body(*answered["messages"][1:], {"role": "assistant", "content": "done"}, user("next"))
    )
    assert r.json()["choices"][0]["message"]["content"] == "upstream"


async def test_declined_proposal_returns_next_turn() -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))
    r = await h.chat(agent_body(user("add a feature")))
    tc = r.json()["choices"][0]["message"]["tool_calls"][0]
    history: list[Message] = [
        user("add a feature"),
        {"role": "assistant", "content": "", "tool_calls": [tc]},
        {"role": "tool", "tool_call_id": tc["id"], "content": '"..."="Nee, ga door"'},
    ]
    r = await h.chat(agent_body(*history))
    assert r.json()["choices"][0]["message"]["content"] == "upstream"
    history += [{"role": "assistant", "content": "upstream"}, user("go on")]
    r = await h.chat(agent_body(*history))
    tc2 = r.json()["choices"][0]["message"]["tool_calls"][0]
    assert tc2["id"] == "gateway_proposal_2"
    props = (await h.client.get("/gateway/proposals", headers=AUTH)).json()
    assert [p["status"] for p in props] == ["declined", "open"]


async def test_proposal_text_mode_without_question_tool() -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))
    r = await h.chat(agent_body(user("add a feature"), question_tool=False, stream=True))
    chunks = sse_chunks(r.text)
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    assert "issue" in text and text.endswith("(antwoord ja of nee)")
    assert not any(c["choices"] and c["choices"][0]["delta"].get("tool_calls") for c in chunks)
    history = [user("add a feature"), {"role": "assistant", "content": text}, user("nee")]
    r = await h.chat(agent_body(*history, question_tool=False))
    assert r.json()["choices"][0]["message"]["content"] == "upstream"
    conv = h.store.get("fwd-coder", "sid:ses_1")
    assert conv is not None and conv.proposals[0].status == "declined" and conv.proposals[0].answer == "nee"
    # Declined in Turn 2 (the answer's Turn): may return from Turn 3 on.
    history += [{"role": "assistant", "content": "upstream"}, user("go on")]
    r = await h.chat(agent_body(*history, question_tool=False))
    assert r.json()["choices"][0]["message"]["content"].endswith("(antwoord ja of nee)")


async def test_proposal_stream_tool_mode() -> None:
    h = Harness(FakeDecider(make_decision("propose_issue")))
    r = await h.chat(agent_body(user("add a feature"), stream=True))
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.endswith("data: [DONE]\n\n")
    chunks = sse_chunks(r.text)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[1]["choices"][0]["delta"]["tool_calls"][0]["id"] == "gateway_proposal_1"
    assert chunks[2]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[3]["choices"] == [] and chunks[3]["usage"]["total_tokens"] == 0


# --- Block ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stream", [False, True])
async def test_block(stream: bool) -> None:
    # Block wins over a Proposal in the same Decision.
    h = Harness(FakeDecider(make_decision("block_pr_without_tests", "propose_issue")))
    r = await h.chat(agent_body(user("open a PR"), stream=stream))
    assert h.upstream.requests == []
    if stream:
        chunks = sse_chunks(r.text)
        text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
        finish = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"] and c["choices"][0]["finish_reason"]
        ]
    else:
        text = r.json()["choices"][0]["message"]["content"]
        finish = [r.json()["choices"][0]["finish_reason"]]
    assert text == "Geblokkeerd: eerst tests groen." and finish == ["stop"]
    status = (await h.client.get("/gateway/status", headers=AUTH)).json()
    assert status["blocks"][0]["rule_id"] == "block_pr_without_tests"
    assert status["open_proposals"] == []


# --- fail-open -----------------------------------------------------------------------------------------


async def test_decider_timeout_fails_open() -> None:
    h = Harness(FakeDecider(make_decision("block_pr_without_tests"), delay=1.0), timeout_s=0.01)
    r = await h.chat(agent_body(user("open a PR")))
    assert r.json()["choices"][0]["message"]["content"] == "upstream"
    conv = h.store.get("fwd-coder", "sid:ses_1")
    assert conv is not None and conv.last_decision is not None
    assert conv.last_decision["error"].startswith("timeout")
    assert any(e["type"] == "decision_error" for e in conv.events)


async def test_decider_exception_fails_open() -> None:
    h = Harness(FakeDecider(exc=RuntimeError("jev down")))
    r = await h.chat(agent_body(user("open a PR"), stream=True))
    assert r.content == UPSTREAM_SSE
    conv = h.store.get("fwd-coder", "sid:ses_1")
    assert conv is not None and conv.last_decision is not None
    assert "jev down" in conv.last_decision["error"]


async def test_strategy_failure_fails_open() -> None:
    h = Harness(FakeDecider(make_decision("block_pr_without_tests")))

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
    conv = h.store.get("fwd-coder", "sid:ses_1")
    assert conv is not None and conv.last_decision is None


# --- read API ------------------------------------------------------------------------------------------


async def test_read_api_scoped_to_virtual_model() -> None:
    h = Harness(FakeDecider(make_decision("flag_no_spec")))
    await h.chat(agent_body(user("x")), sid="mine")
    await h.chat(agent_body(user("y")), key=OTHER_KEY, sid="theirs")
    other = {"authorization": f"Bearer {OTHER_KEY}"}
    assert (await h.client.get("/gateway/conversations")).status_code == 401
    mine = (await h.client.get("/gateway/conversations", headers=AUTH)).json()
    assert [c["id"] for c in mine] == ["sid:mine"]
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
    assert "<b>x</b>" not in r.text and "&lt;b&gt;x&lt;/b&gt;" in r.text
    assert (await h.client.get("/healthz")).json() == {"status": "ok"}
