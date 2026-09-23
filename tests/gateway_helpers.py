"""Shared test helpers: a small Workflow Definition and Decision builders."""

from __future__ import annotations

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
                "question": "Zullen we eerst een issue aanmaken?",
                "header": "Eerst een issue?",
                "accept_label": "Ja, maak een issue",
                "decline_label": "Nee, ga door",
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
            "block_message": "Geblokkeerd: eerst tests groen.",
        },
    ],
}


def make_workflow() -> WorkflowDefinition:
    return parse_workflow(WORKFLOW_DATA)


def make_decision(*broken: str, phase: str | None = "implement", error: str | None = None) -> Decision:
    rule_ids = ("propose_issue", "flag_no_spec", "block_pr_without_tests")
    verdicts = {rid: RuleVerdict(rid, 0.9 if rid in broken else 0.1, rid in broken) for rid in rule_ids}
    return Decision(verdicts=verdicts, decider="fake", latency_ms=1.0, phase=phase, error=error)
