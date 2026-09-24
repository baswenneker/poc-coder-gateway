"""Load a Workflow Definition from YAML. Format: see workflows/fwd-default.yaml."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from coder_gateway.domain import Intervention, Phase, ProposalSpec, Rule, Trigger, WorkflowDefinition


def _parse_trigger(rule_id: str, raw: Any) -> Trigger | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("pattern"), str) or not raw["pattern"]:
        raise ValueError(f"rule {rule_id!r}: 'trigger' needs a non-empty 'pattern'")
    try:
        re.compile(raw["pattern"])
    except re.error as exc:
        raise ValueError(f"rule {rule_id!r}: trigger pattern is not a valid regex: {exc}") from exc
    tools = raw.get("tools") or []
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ValueError(f"rule {rule_id!r}: trigger 'tools' must be a list of tool names")
    return Trigger(pattern=raw["pattern"], tools=tuple(tools))


def parse_workflow(data: dict[str, Any]) -> WorkflowDefinition:
    rules: list[Rule] = []
    for raw in data.get("rules", []):
        intervention = Intervention(raw["intervention"])
        proposal = ProposalSpec(**raw["proposal"]) if raw.get("proposal") else None
        if intervention is Intervention.PROPOSE and proposal is None:
            raise ValueError(f"rule {raw['id']!r}: intervention 'propose' needs a 'proposal' block")
        if intervention is Intervention.BLOCK and not raw.get("block_message"):
            raise ValueError(f"rule {raw['id']!r}: intervention 'block' needs a 'block_message'")
        trigger = _parse_trigger(raw["id"], raw.get("trigger"))
        if intervention is Intervention.BLOCK and trigger is None:
            # A Block happens on the response side, before the client runs the tool call. Only a
            # trigger match makes the Gateway judge the Rule there (DECISIONS.md #28).
            raise ValueError(f"rule {raw['id']!r}: intervention 'block' needs a 'trigger'")
        if trigger is not None and intervention is not Intervention.BLOCK:
            raise ValueError(
                f"rule {raw['id']!r}: only a rule with intervention 'block' can have a 'trigger'"
            )
        rules.append(
            Rule(
                id=raw["id"],
                description=raw["description"],
                broken_when=raw["broken_when"],
                ok_when=raw.get("ok_when"),
                intervention=intervention,
                threshold=float(raw.get("threshold", 0.7)),
                proposal=proposal,
                block_message=raw.get("block_message"),
                trigger=trigger,
            )
        )
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate rule ids in workflow")
    phases = tuple(Phase(id=p["id"], description=p["description"]) for p in data.get("phases", []))
    return WorkflowDefinition(name=data["name"], rules=tuple(rules), phases=phases)


def load_workflow(path: str | Path) -> WorkflowDefinition:
    with open(path, encoding="utf-8") as f:
        return parse_workflow(yaml.safe_load(f))
