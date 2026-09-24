"""JevDecider: one System One call (typesafe_sdk) per Decision, one Noul question per Rule.

See PLAN.md ("Beslissing ... door Jev") and docs/DECISIONS.md #6 (one yes/no question per Rule
instead of one enum, because multiple Rules can be broken at once).
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from typesafe_sdk import Choice, Noul

from coder_gateway.domain import Decision, DecisionInput, Rule, RuleVerdict, WorkflowDefinition
from coder_gateway.transcript import compact_transcript


class SystemOneClient(Protocol):
    """The slice of `typesafe_sdk.AsyncTypeSafeClient` JevDecider needs. Lets tests use a fake."""

    async def system_one(self, state: Any, questions: dict[str, Any], *, model: str | None = None) -> Any: ...


def _rule_question(rule: Rule) -> Noul:
    return Noul(
        instructions=rule.broken_when,
        criteria={"true": rule.broken_when, "false": rule.ok_when},
    )


def _phase_question(workflow: WorkflowDefinition) -> Choice | None:
    if not workflow.phases:
        return None
    return Choice(
        instructions="Which phase of the workflow is the conversation currently in?",
        criteria={phase.id: phase.description for phase in workflow.phases},
    )


def build_state(inp: DecisionInput) -> dict[str, Any]:
    """State handed to Jev: the compact Transcript, the Gateway's own conversation state, and a
    description of the Rules being judged (so Jev has the same context as the questions imply).
    """
    return {
        "transcript": compact_transcript(inp.transcript),
        "gateway_state": inp.state,
        "workflow": {
            rule.id: {"description": rule.description, "ok_when": rule.ok_when} for rule in inp.workflow.rules
        },
    }


class JevDecider:
    """Decider backed by typesafe.ai's Jev (System One model)."""

    def __init__(self, client: SystemOneClient, *, model: str = "jev-latest") -> None:
        self._client = client
        self._model = model
        self.name = "jev"

    async def decide(self, inp: DecisionInput) -> Decision:
        questions: dict[str, Any] = {rule.id: _rule_question(rule) for rule in inp.workflow.rules}
        phase_question = _phase_question(inp.workflow)
        if phase_question is not None:
            questions["phase"] = phase_question

        state = build_state(inp)

        start = time.perf_counter()
        resp = await self._client.system_one(state=state, questions=questions, model=self._model)
        latency_ms = (time.perf_counter() - start) * 1000

        verdicts: dict[str, RuleVerdict] = {}
        for rule in inp.workflow.rules:
            noul_answer = resp.nouls.get(rule.id)
            probability = float(noul_answer.noul) if noul_answer is not None else 0.0
            verdicts[rule.id] = RuleVerdict(
                rule_id=rule.id, probability=probability, broken=probability >= rule.threshold
            )

        phase: str | None = None
        if phase_question is not None:
            choice_answer = resp.choices.get("phase")
            if choice_answer is not None:
                phase = str(choice_answer.choice)

        usage = getattr(resp, "usage", None)
        return Decision(
            verdicts=verdicts,
            decider=f"{self.name}:{getattr(resp, 'model', self._model)}",
            latency_ms=latency_ms,
            phase=phase,
            input_tokens=getattr(usage, "input_tokens", None) if usage is not None else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage is not None else None,
        )
