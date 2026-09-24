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


@pytest.mark.parametrize(
    "arguments",
    [
        '{"command":"git push"}',
        '{"command":"git\\u0020push"}',
        '{"command": "git\\tpush origin main"}',
        json.dumps({"command": "git  push"}),
        json.dumps({"args": ["bash", "-c", "git push"]}),
        "git push",  # not JSON: matched as it is
    ],
)
def test_trigger_matches_decoded_arguments(arguments: str) -> None:
    assert Trigger(pattern="git push").matches("bash", arguments)


def test_trigger_only_looks_at_the_start_of_long_arguments() -> None:
    padding = "x" * 10_000
    assert not Trigger(pattern="git push").matches("bash", json.dumps({"command": padding + " git push"}))


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git -C /x push",
        "git -c a=b push origin main",
        "git --no-pager push",
        "cd repo && git\tpush -u origin feat",
        "gh pr create --fill",
        "gh  pr\tcreate",
        "glab mr create",
    ],
)
def test_default_trigger_matches(command: str) -> None:
    rule = load_workflow(REPO_ROOT / "workflows" / "fwd-default.yaml").rule("block_pr_without_tests")
    assert rule.trigger is not None and rule.trigger.matches("bash", json.dumps({"command": command}))


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "pushd /tmp",
        "git -C /x status",
        "git pushx",
        "legit push",
        " ".join(["git"] + ["-a"] * 40 + ["status"]),
    ],
)
def test_default_trigger_does_not_match(command: str) -> None:
    rule = load_workflow(REPO_ROOT / "workflows" / "fwd-default.yaml").rule("block_pr_without_tests")
    assert rule.trigger is not None and not rule.trigger.matches("bash", json.dumps({"command": command}))
