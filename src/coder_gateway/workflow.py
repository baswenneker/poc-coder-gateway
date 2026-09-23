"""Load a Workflow Definition from YAML. Format: see workflows/fwd-default.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from coder_gateway.domain import Intervention, Phase, ProposalSpec, Rule, WorkflowDefinition


def parse_workflow(data: dict[str, Any]) -> WorkflowDefinition:
    rules: list[Rule] = []
    for raw in data.get("rules", []):
        intervention = Intervention(raw["intervention"])
        proposal = ProposalSpec(**raw["proposal"]) if raw.get("proposal") else None
        if intervention is Intervention.PROPOSE and proposal is None:
            raise ValueError(f"rule {raw['id']!r}: intervention 'propose' needs a 'proposal' block")
        if intervention is Intervention.BLOCK and not raw.get("block_message"):
            raise ValueError(f"rule {raw['id']!r}: intervention 'block' needs a 'block_message'")
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
