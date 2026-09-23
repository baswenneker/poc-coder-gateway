from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from coder_gateway.deciders import build_decider
from coder_gateway.deciders.fallback import FallbackDecider, NoneDecider, SoloDecider
from coder_gateway.deciders.jev import JevDecider
from coder_gateway.deciders.llm import LLMDecider
from coder_gateway.domain import (
    Decision,
    DecisionInput,
    Intervention,
    Phase,
    ProposalSpec,
    Rule,
    RuleVerdict,
    WorkflowDefinition,
)

RULE_A = Rule(
    id="propose_issue",
    description="Work starts from an issue.",
    broken_when="No issue is referenced and code changes are requested.",
    ok_when="An issue is referenced, or no code changes are requested.",
    intervention=Intervention.PROPOSE,
    threshold=0.7,
    proposal=ProposalSpec(question="Shall we create an issue first?"),
)
RULE_B = Rule(
    id="flag_no_spec",
    description="Code changes are based on a discussed spec file.",
    broken_when="Code is changed without a spec file discussed.",
    ok_when="A spec file was discussed.",
    intervention=Intervention.FLAG,
    threshold=0.7,
)
WORKFLOW = WorkflowDefinition(name="test", rules=(RULE_A, RULE_B))
WORKFLOW_WITH_PHASES = WorkflowDefinition(
    name="test-phases",
    rules=(RULE_A,),
    phases=(
        Phase(id="explore", description="reading code"),
        Phase(id="implement", description="writing code"),
    ),
)


def _input(workflow: WorkflowDefinition = WORKFLOW) -> DecisionInput:
    return DecisionInput(workflow=workflow, transcript=[{"role": "user", "content": "hi"}], state={"turn": 1})


# --- NoneDecider ---------------------------------------------------------------------------


async def test_none_decider_reports_nothing_broken() -> None:
    decider = NoneDecider()
    decision = await decider.decide(_input())
    assert decision.broken() == []
    assert decision.decider == "none"
    assert decision.error is None


# --- FallbackDecider -------------------------------------------------------------------------


class _StubDecider:
    def __init__(self, name: str, *, decision: Decision | None = None, error: Exception | None = None,
                 delay: float = 0.0) -> None:
        self.name = name
        self._decision = decision
        self._error = error
        self._delay = delay
        self.calls = 0

    async def decide(self, inp: DecisionInput) -> Decision:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        assert self._decision is not None
        return self._decision


def _decision(name: str) -> Decision:
    return Decision(
        verdicts={"propose_issue": RuleVerdict(rule_id="propose_issue", probability=0.1, broken=False)},
        decider=name,
        latency_ms=1.0,
    )


async def test_fallback_returns_primary_result_when_it_succeeds() -> None:
    primary = _StubDecider("primary", decision=_decision("primary"))
    fallback = _StubDecider("fallback", decision=_decision("fallback"))
    decider = FallbackDecider(primary, fallback, timeout_s=1.0)
    decision = await decider.decide(_input())
    assert decision.decider == "primary"
    assert fallback.calls == 0


async def test_fallback_runs_fallback_when_primary_raises() -> None:
    primary = _StubDecider("primary", error=RuntimeError("boom"))
    fallback = _StubDecider("fallback", decision=_decision("fallback"))
    decider = FallbackDecider(primary, fallback, timeout_s=1.0)
    decision = await decider.decide(_input())
    assert decision.decider == "fallback"


async def test_fallback_runs_fallback_when_primary_times_out(caplog: pytest.LogCaptureFixture) -> None:
    primary = _StubDecider("primary", decision=_decision("primary"), delay=0.5)
    fallback = _StubDecider("fallback", decision=_decision("fallback"))
    decider = FallbackDecider(primary, fallback, timeout_s=0.01)
    with caplog.at_level("WARNING", logger="coder_gateway.deciders"):
        decision = await decider.decide(_input())
    assert decision.decider == "fallback"
    # Seen live: Jev once took > 3 s; without this warning the switch to the fallback was invisible.
    assert "decider primary failed" in caplog.text and "trying fallback" in caplog.text


async def test_fallback_fails_open_when_both_fail() -> None:
    primary = _StubDecider("primary", error=RuntimeError("primary broke"))
    fallback = _StubDecider("fallback", error=RuntimeError("fallback broke"))
    decider = FallbackDecider(primary, fallback, timeout_s=1.0)
    decision = await decider.decide(_input())
    assert decision.broken() == []
    assert decision.error is not None
    assert "primary broke" in decision.error
    assert "fallback broke" in decision.error


# --- SoloDecider (fallback='none': no fallback, but still fails open) ------------------------


async def test_solo_decider_returns_primary_result_when_it_succeeds() -> None:
    primary = _StubDecider("primary", decision=_decision("primary"))
    decider = SoloDecider(primary, timeout_s=1.0)
    decision = await decider.decide(_input())
    assert decision.decider == "primary"
    assert decision.error is None


async def test_solo_decider_fails_open_with_error_when_primary_raises() -> None:
    # Regression for DECISIONS.md #10 / benchmark finding: fallback='none' must not silently
    # turn a primary exception into an all-clear ("nothing broken", error=None, latency 0). It
    # must fail open (no broken Rules) but *with* `error` set and a real latency, so callers
    # (the benchmark, in particular) can tell "no answer" apart from "correct answer".
    primary = _StubDecider("primary", error=RuntimeError("primary broke"))
    decider = SoloDecider(primary, timeout_s=1.0)
    decision = await decider.decide(_input())
    assert decision.broken() == []
    assert decision.error is not None
    assert "primary broke" in decision.error
    assert decision.latency_ms > 0.0


async def test_solo_decider_fails_open_with_error_when_primary_times_out() -> None:
    primary = _StubDecider("primary", decision=_decision("primary"), delay=0.5)
    decider = SoloDecider(primary, timeout_s=0.01)
    decision = await decider.decide(_input())
    assert decision.broken() == []
    assert decision.error is not None
    assert decision.latency_ms > 0.0


# --- JevDecider ------------------------------------------------------------------------------


class _FakeNoulAnswer:
    def __init__(self, noul: float) -> None:
        self.noul = noul


class _FakeChoiceAnswer:
    def __init__(self, choice: str) -> None:
        self.choice = choice


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeSystemOneResponse:
    def __init__(self, nouls: dict[str, float], choices: dict[str, str] | None = None,
                 model: str = "jev-latest") -> None:
        self.nouls = {k: _FakeNoulAnswer(v) for k, v in nouls.items()}
        self.choices = {k: _FakeChoiceAnswer(v) for k, v in (choices or {}).items()}
        self.usage = _FakeUsage(120, 12)
        self.model = model


class _FakeSystemOneClient:
    def __init__(self, response: _FakeSystemOneResponse) -> None:
        self._response = response
        self.last_state: Any = None
        self.last_questions: Any = None

    async def system_one(self, state: Any, questions: Any, *, model: str | None = None) -> Any:
        self.last_state = state
        self.last_questions = questions
        return self._response


async def test_jev_decider_maps_noul_probabilities_to_verdicts() -> None:
    response = _FakeSystemOneResponse(nouls={"propose_issue": 0.9, "flag_no_spec": 0.2})
    client = _FakeSystemOneClient(response)
    decider = JevDecider(client)
    decision = await decider.decide(_input())
    assert decision.verdicts["propose_issue"].broken is True
    assert decision.verdicts["propose_issue"].probability == 0.9
    assert decision.verdicts["flag_no_spec"].broken is False
    assert decision.decider == "jev:jev-latest"
    assert decision.input_tokens == 120
    assert decision.output_tokens == 12


async def test_jev_decider_asks_one_question_per_rule() -> None:
    response = _FakeSystemOneResponse(nouls={"propose_issue": 0.1, "flag_no_spec": 0.1})
    client = _FakeSystemOneClient(response)
    decider = JevDecider(client)
    await decider.decide(_input())
    assert set(client.last_questions.keys()) == {"propose_issue", "flag_no_spec"}


async def test_jev_decider_adds_phase_choice_question_when_workflow_has_phases() -> None:
    response = _FakeSystemOneResponse(nouls={"propose_issue": 0.1}, choices={"phase": "implement"})
    client = _FakeSystemOneClient(response)
    decider = JevDecider(client)
    decision = await decider.decide(_input(WORKFLOW_WITH_PHASES))
    assert "phase" in client.last_questions
    assert decision.phase == "implement"


async def test_jev_decider_omits_phase_question_without_phases() -> None:
    response = _FakeSystemOneResponse(nouls={"propose_issue": 0.1, "flag_no_spec": 0.1})
    client = _FakeSystemOneClient(response)
    decider = JevDecider(client)
    decision = await decider.decide(_input())
    assert "phase" not in client.last_questions
    assert decision.phase is None


async def test_jev_decider_state_includes_compact_transcript_and_gateway_state() -> None:
    response = _FakeSystemOneResponse(nouls={"propose_issue": 0.1, "flag_no_spec": 0.1})
    client = _FakeSystemOneClient(response)
    decider = JevDecider(client)
    await decider.decide(_input())
    assert client.last_state["gateway_state"] == {"turn": 1}
    assert client.last_state["transcript"] == [{"role": "user", "text": "hi"}]
    assert "propose_issue" in client.last_state["workflow"]


# --- LLMDecider ------------------------------------------------------------------------------


class _FakeChatMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChatChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeChatMessage(content)


class _FakeChatUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeChatCompletion:
    def __init__(self, parsed: dict[str, Any]) -> None:
        self.choices = [_FakeChatChoice(json.dumps(parsed))]
        self.usage = _FakeChatUsage(200, 20)


class _FakeChatCompletionsClient:
    def __init__(self, parsed: dict[str, Any]) -> None:
        self._parsed = parsed
        self.last_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return _FakeChatCompletion(self._parsed)


async def test_llm_decider_maps_structured_output_to_verdicts() -> None:
    client = _FakeChatCompletionsClient({"rules": {"propose_issue": 0.85, "flag_no_spec": 0.1}})
    decider = LLMDecider(client, model="gpt-5.4-mini")
    decision = await decider.decide(_input())
    assert decision.verdicts["propose_issue"].broken is True
    assert decision.verdicts["flag_no_spec"].broken is False
    assert decision.decider == "llm:gpt-5.4-mini"
    assert decision.input_tokens == 200
    assert decision.output_tokens == 20


async def test_llm_decider_uses_max_completion_tokens_not_max_tokens() -> None:
    client = _FakeChatCompletionsClient({"rules": {"propose_issue": 0.1, "flag_no_spec": 0.1}})
    decider = LLMDecider(client)
    await decider.decide(_input())
    assert "max_completion_tokens" in client.last_kwargs
    assert "max_tokens" not in client.last_kwargs


async def test_llm_decider_requests_strict_json_schema_response_format() -> None:
    client = _FakeChatCompletionsClient({"rules": {"propose_issue": 0.1, "flag_no_spec": 0.1}})
    decider = LLMDecider(client)
    await decider.decide(_input())
    response_format = client.last_kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


async def test_llm_decider_includes_phase_in_schema_and_output_when_present() -> None:
    client = _FakeChatCompletionsClient({"rules": {"propose_issue": 0.1}, "phase": "implement"})
    decider = LLMDecider(client)
    decision = await decider.decide(_input(WORKFLOW_WITH_PHASES))
    schema = client.last_kwargs["response_format"]["json_schema"]["schema"]
    assert "phase" in schema["properties"]
    assert decision.phase == "implement"


# --- build_decider -----------------------------------------------------------------------------


def test_build_decider_none_primary_returns_none_decider() -> None:
    decider = build_decider(
        primary="none",
        fallback="none",
        timeout_s=1.0,
        jev_model="jev-latest",
        llm_model="gpt-5.4-mini",
        openai_api_key=None,
        openai_base_url="https://api.openai.com/v1",
        typesafe_api_key=None,
    )
    assert isinstance(decider, NoneDecider)


def test_build_decider_fallback_none_wraps_primary_in_solo_decider_not_fallback_decider() -> None:
    # Regression: fallback='none' must mean "no fallback", not "fall back to NoneDecider". A
    # FallbackDecider whose fallback is NoneDecider turns a primary exception into a *successful*
    # all-clear Decision (error=None, latency 0ms) — exactly the false "all clear" this fixes.
    decider = build_decider(
        primary="llm",
        fallback="none",
        timeout_s=1.0,
        jev_model="jev-latest",
        llm_model="gpt-5.4-mini",
        openai_api_key="sk-test",
        openai_base_url="https://api.openai.com/v1",
        typesafe_api_key=None,
    )
    assert isinstance(decider, SoloDecider)
    assert not isinstance(decider, FallbackDecider)


def test_build_decider_wraps_non_none_primary_in_fallback_decider_when_fallback_is_llm() -> None:
    decider = build_decider(
        primary="jev",
        fallback="llm",
        timeout_s=1.0,
        jev_model="jev-latest",
        llm_model="gpt-5.4-mini",
        openai_api_key="sk-test",
        openai_base_url="https://api.openai.com/v1",
        typesafe_api_key="ts-test",
    )
    assert isinstance(decider, FallbackDecider)


def test_build_decider_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        build_decider(
            primary="bogus",
            fallback="none",
            timeout_s=1.0,
            jev_model="jev-latest",
            llm_model="gpt-5.4-mini",
            openai_api_key=None,
            openai_base_url="https://api.openai.com/v1",
            typesafe_api_key=None,
        )
