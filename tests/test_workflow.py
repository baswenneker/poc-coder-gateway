"""Workflow Definition parsing: triggers on Block Rules (DECISIONS.md #28)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from gateway_helpers import WORKFLOW_DATA

from coder_gateway.domain import Trigger
from coder_gateway.workflow import load_workflow, parse_workflow

REPO_ROOT = Path(__file__).resolve().parents[1]


def _data_with_block(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(WORKFLOW_DATA)
    block = data["rules"][2]
    for key, value in changes.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value
    return data


def test_block_rule_without_trigger_is_an_error() -> None:
    with pytest.raises(ValueError, match="'block' needs a 'trigger'"):
        parse_workflow(_data_with_block(trigger=None))


@pytest.mark.parametrize(
    "trigger", [{"pattern": ""}, {"tools": ["bash"]}, "gh pr create", {"pattern": "(unclosed"}]
)
def test_invalid_trigger_is_an_error(trigger: Any) -> None:
    with pytest.raises(ValueError, match="trigger"):
        parse_workflow(_data_with_block(trigger=trigger))


def test_trigger_only_on_block_rules() -> None:
    data = copy.deepcopy(WORKFLOW_DATA)
    data["rules"][1]["trigger"] = {"pattern": "x"}
    with pytest.raises(ValueError, match="only a rule with intervention 'block'"):
        parse_workflow(data)


def test_trigger_matching() -> None:
    trigger = Trigger(pattern="gh pr create|git push")
    assert trigger.matches("bash", json.dumps({"command": "git add . && GH PR CREATE --fill"}))
    assert trigger.matches("shell", '{"command": "git push origin main"}')
    assert not trigger.matches("bash", '{"command": "git status"}')
    only_bash = Trigger(pattern="git push", tools=("bash",))
    assert only_bash.matches("bash", '{"command": "git push"}')
    assert not only_bash.matches("write", '{"content": "run git push later"}')


def test_default_workflow_block_rule_has_client_agnostic_trigger() -> None:
    rule = load_workflow(REPO_ROOT / "workflows" / "fwd-default.yaml").rule("block_pr_without_tests")
    assert rule.trigger is not None and rule.trigger.tools == ()
    for command in ("gh pr create --fill", "glab mr create", "git push -u origin feat"):
        assert rule.trigger.matches("bash", json.dumps({"command": command}))
