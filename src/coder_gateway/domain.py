"""Shared domain types. Vocabulary follows CONTEXT.md (Virtual Model, Rule, Decision, ...).

This module is the contract between the gateway core, the Deciders and the benchmark.
Keep it free of I/O.
"""

from __future__ import annotations

import json
import re
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
    accept_label: str = "Yes"
    accept_description: str = "Do this first"
    decline_label: str = "No"
    decline_description: str = "Continue without"


# A trigger looks at no more than this many characters of a tool call's arguments (DECISIONS.md #35).
TRIGGER_MAX_CHARS = 8_000
_WHITESPACE = re.compile(r"\s+")


def _string_values(value: Any) -> list[str]:
    """All strings in a decoded JSON value, depth first (keys left out)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _string_values(v)]
    if isinstance(value, list):
        return [s for v in value for s in _string_values(v)]
    return []


def trigger_text(arguments: str) -> str:
    """What a trigger is matched against: the string values of the arguments JSON (the raw string if
    it is no JSON), joined, every run of whitespace made one space, cut to TRIGGER_MAX_CHARS
    (DECISIONS.md #34, #35)."""
    try:
        text = " ".join(_string_values(json.loads(arguments)))
    except ValueError:
        text = arguments
    return _WHITESPACE.sub(" ", text)[:TRIGGER_MAX_CHARS]


@dataclass(frozen=True)
class Trigger:
    """A cheap pattern on a tool call the model wants to make. Only a match makes the Gateway run a
    Decision for a Block Rule before the client executes the tool call (DECISIONS.md #28)."""

    # Regular expression, matched case-insensitively against `trigger_text(arguments)`. Keep it simple:
    # the workflow is trusted config, but the regex runs on the event loop (DECISIONS.md #35).
    pattern: str
    # Optional tool names; when given, the tool call's name must be one of them.
    tools: tuple[str, ...] = ()

    def matches(self, tool_name: str, arguments: str) -> bool:
        if self.tools and tool_name not in self.tools:
            return False
        return re.search(self.pattern, trigger_text(arguments), re.IGNORECASE) is not None


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
    # Explanation that replaces the blocked tool call in the assistant reply.
    block_message: str | None = None
    # Required for intervention `block`: which tool calls make the Gateway judge this Rule.
    trigger: Trigger | None = None


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
    """The verdict at one decision point (end of a Turn, or a triggering tool call): which Rules are
    broken."""

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
    # {"turn": 3, "active_flags": ["no_spec"],
    #  "proposals": [{"rule_id": ..., "status": "declined", "turn": 2}]}
    state: dict[str, Any] = field(default_factory=dict)


class Decider(Protocol):
    name: str

    async def decide(self, inp: DecisionInput) -> Decision: ...
