"""LLMDecider: OpenAI chat completions with structured output, as the fallback for JevDecider.

Same semantics as JevDecider (a probability 0..1 per Rule, plus an optional phase) so the two are
interchangeable behind the `Decider` protocol. Default model is `gpt-5.4-mini`; gpt-5.x models take
`max_completion_tokens` instead of `max_tokens`, so this module always uses the former.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from coder_gateway.domain import Decision, DecisionInput, RuleVerdict, WorkflowDefinition
from coder_gateway.transcript import compact_transcript

_SYSTEM_PROMPT = (
    "You are the Decider of a coding-agent Gateway. You judge, for the given Transcript and "
    "state, whether each listed Rule is currently broken. For every rule, answer with the "
    "probability (0..1) that it is broken right now, using its `broken_when` statement as the "
    "TRUE condition and its `ok_when` statement (if given) as the FALSE condition. If the "
    "workflow defines phases, also classify the current phase."
)


class ChatCompletionsClient(Protocol):
    """The slice of `openai.AsyncOpenAI` LLMDecider needs (structurally: `client.chat.completions`).
    Lets tests use a fake. `openai`'s real `create()` has a much stricter (overloaded, keyword-only)
    signature; this Protocol is intentionally loose so both the real client and fakes satisfy it.
    """

    def create(self, *args: Any, **kwargs: Any) -> Any: ...


def _response_schema(workflow: WorkflowDefinition) -> dict[str, Any]:
    rule_properties = {
        rule.id: {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": f"Probability (0..1) that rule {rule.id!r} is broken: {rule.broken_when}",
        }
        for rule in workflow.rules
    }
    properties: dict[str, Any] = {
        "rules": {
            "type": "object",
            "properties": rule_properties,
            "required": list(rule_properties),
            "additionalProperties": False,
        }
    }
    required = ["rules"]
    if workflow.phases:
        properties["phase"] = {"type": "string", "enum": [phase.id for phase in workflow.phases]}
        required.append("phase")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _build_user_content(inp: DecisionInput) -> str:
    payload = {
        "transcript": compact_transcript(inp.transcript),
        "gateway_state": inp.state,
        "rules": [
            {
                "id": rule.id,
                "description": rule.description,
                "broken_when": rule.broken_when,
                "ok_when": rule.ok_when,
            }
            for rule in inp.workflow.rules
        ],
        "phases": [{"id": phase.id, "description": phase.description} for phase in inp.workflow.phases],
    }
    return json.dumps(payload)


class LLMDecider:
    """Decider backed by an OpenAI chat-completions model with strict JSON-schema output."""

    def __init__(self, client: ChatCompletionsClient, *, model: str = "gpt-5.4-mini") -> None:
        self._client = client
        self._model = model
        self.name = "llm"

    async def decide(self, inp: DecisionInput) -> Decision:
        schema = _response_schema(inp.workflow)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_content(inp)},
        ]

        start = time.perf_counter()
        resp = await self._client.create(
            model=self._model,
            messages=messages,
            max_completion_tokens=1024,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "gateway_decision", "schema": schema, "strict": True},
            },
        )
        latency_ms = (time.perf_counter() - start) * 1000

        content = resp.choices[0].message.content
        parsed = json.loads(content)
        rule_probs: dict[str, float] = parsed.get("rules", {})

        verdicts: dict[str, RuleVerdict] = {}
        for rule in inp.workflow.rules:
            probability = float(rule_probs.get(rule.id, 0.0))
            verdicts[rule.id] = RuleVerdict(
                rule_id=rule.id, probability=probability, broken=probability >= rule.threshold
            )

        usage = getattr(resp, "usage", None)
        return Decision(
            verdicts=verdicts,
            decider=f"{self.name}:{self._model}",
            latency_ms=latency_ms,
            phase=parsed.get("phase"),
            input_tokens=getattr(usage, "prompt_tokens", None) if usage is not None else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage is not None else None,
        )
