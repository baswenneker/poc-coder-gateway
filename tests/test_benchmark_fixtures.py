"""Schema validation for benchmark/fixtures/*.json."""

from __future__ import annotations

from pathlib import Path

import pytest

from coder_gateway.benchmark import (
    DEFAULT_FIXTURES_GLOB,
    Fixture,
    load_fixture,
    load_fixtures,
    validate_fixture,
    validate_messages,
)
from coder_gateway.workflow import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = load_workflow(REPO_ROOT / "workflows" / "fwd-default.yaml")
RULE_IDS = [r.id for r in WORKFLOW.rules]


def _fixture_paths() -> list[Path]:
    return sorted((REPO_ROOT / "benchmark" / "fixtures").glob("*.json"))


def test_at_least_fifteen_fixtures_exist() -> None:
    assert len(_fixture_paths()) >= 15


@pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: p.stem)
def test_fixture_loads_and_is_valid(path: Path) -> None:
    fixture = load_fixture(path)
    problems = validate_fixture(fixture, RULE_IDS)
    assert problems == []


def test_load_fixtures_glob_matches_default_pattern() -> None:
    fixtures = load_fixtures(DEFAULT_FIXTURES_GLOB, rule_ids=RULE_IDS)
    assert len(fixtures) == len(_fixture_paths())
    assert all(isinstance(f, Fixture) for f in fixtures)


def test_load_fixtures_raises_on_empty_glob() -> None:
    with pytest.raises(ValueError, match="no fixtures matched"):
        load_fixtures(str(REPO_ROOT / "benchmark" / "fixtures" / "does-not-exist-*.json"))


# --- validate_fixture / validate_messages, unit-level ---------------------------------------


def _fx(expected: dict[str, bool], messages: list[dict[str, object]] | None = None) -> Fixture:
    return Fixture(name="x", description="d", messages=messages or [], expected=expected)


def test_validate_fixture_flags_missing_rule_id() -> None:
    problems = validate_fixture(_fx({"propose_issue": True}), RULE_IDS)
    assert any("missing rule id" in p for p in problems)


def test_validate_fixture_flags_unknown_rule_id() -> None:
    expected = {rid: False for rid in RULE_IDS} | {"bogus_rule": True}
    problems = validate_fixture(_fx(expected), RULE_IDS)
    assert any("unknown rule id" in p for p in problems)


def test_validate_fixture_flags_non_bool_expected_value() -> None:
    expected: dict[str, object] = {rid: False for rid in RULE_IDS}
    expected["propose_issue"] = "yes"
    problems = validate_fixture(_fx(expected), RULE_IDS)  # type: ignore[arg-type]
    assert any("not booleans" in p for p in problems)


def test_validate_messages_accepts_matching_tool_call_id() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]
    assert validate_messages(messages) == []


def test_validate_messages_flags_dangling_tool_call_id() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "call_missing", "content": "ok"},
    ]
    problems = validate_messages(messages)
    assert len(problems) == 1
    assert "call_missing" in problems[0]


def test_validate_messages_flags_missing_tool_call_id_field() -> None:
    messages = [{"role": "tool", "content": "ok"}]
    problems = validate_messages(messages)
    assert any("without tool_call_id" in p for p in problems)


def test_validate_messages_flags_duplicate_tool_call_ids() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "bash", "arguments": "{}"}}],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "bash", "arguments": "{}"}}],
        },
    ]
    problems = validate_messages(messages)
    assert any("duplicate tool_call id" in p for p in problems)
