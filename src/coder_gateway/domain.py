"""Shared domain types. Vocabulary follows CONTEXT.md (Virtual Model, Rule, Decision, ...).

This module is the contract between the gateway core, the Deciders and the benchmark.
Keep it free of I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

# An OpenAI chat-completions message, passed through as-is.
Message = dict[str, Any]

# Tool-call ids the Gateway uses for Proposals all start with this prefix.
PROPOSAL_TOOL_CALL_PREFIX = "gateway_proposal"


class Intervention(StrEnum):
    FLAG = "flag"
    PROPOSE = "propose"
    BLOCK = "block"


@dataclass(frozen=True)
class ProposalSpec:
    """What the Developer sees when a Rule with intervention `propose` is broken."""

    question: str
    header: str = "Gateway"
    accept_label: str = "Ja"
    accept_description: str = "Doe dit eerst"
    decline_label: str = "Nee"
    decline_description: str = "Ga door zonder"


@dataclass(frozen=True)
class Rule:
    """One expectation in a Workflow Definition plus the Intervention it triggers when broken."""

    id: str
    description: str
    # Instructions for the Decider: a yes/no statement that is TRUE when the Rule is broken.
    broken_when: str
    # Optional extra guidance for the Decider on the "not broken" side.
    ok_when: str | None
    intervention: Intervention
    threshold: float = 0.7
    proposal: ProposalSpec | None = None
    # Explanation returned as the assistant reply when the Rule blocks a request.
    block_message: str | None = None


@dataclass(frozen=True)
class Phase:
    id: str
    description: str


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    rules: tuple[Rule, ...]
    phases: tuple[Phase, ...] = ()

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)


@dataclass(frozen=True)
class VirtualModel:
    """What the coding agent sees as 'the model' behind one API key."""

    name: str
    api_key: str
    upstream_model: str
    system_prompt: str
    workflow: WorkflowDefinition


@dataclass(frozen=True)
class RuleVerdict:
    rule_id: str
    # Probability (0..1) that the Rule is broken.
    probability: float
    # probability >= rule.threshold
    broken: bool


@dataclass(frozen=True)
class Decision:
    """The verdict for one request: which Rules are broken."""

    verdicts: dict[str, RuleVerdict]
    decider: str
    latency_ms: float
    phase: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Set when the Decider failed; the Gateway then fails open (no Intervention).
    error: str | None = None

    def broken(self) -> list[str]:
        return [rid for rid, v in self.verdicts.items() if v.broken]


@dataclass(frozen=True)
class DecisionInput:
    """Everything a Decider gets: the (already strategy-reduced) Transcript plus structured state."""

    workflow: WorkflowDefinition
    transcript: list[Message]
    # Structured state the Gateway knows about the Conversation, e.g.
    # {"turn": 3, "active_flags": ["no_spec"], "proposals": [{"rule_id": ..., "status": "declined", "turn": 2}]}
    state: dict[str, Any] = field(default_factory=dict)


class Decider(Protocol):
    name: str

    async def decide(self, inp: DecisionInput) -> Decision: ...
