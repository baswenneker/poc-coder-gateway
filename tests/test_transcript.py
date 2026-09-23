from __future__ import annotations

import pytest

from coder_gateway.transcript import STRATEGIES, apply_strategy, compact_transcript


def _msg(role: str, content: object, **extra: object) -> dict:
    return {"role": role, "content": content, **extra}


def test_strategies_tuple_contains_expected_names() -> None:
    assert "full" in STRATEGIES
    assert "last_10" in STRATEGIES
    assert "last_10_truncated" in STRATEGIES
    assert "summary_last_10" in STRATEGIES


def test_full_drops_system_messages() -> None:
    messages = [
        _msg("system", "sys prompt"),
        _msg("user", "hi"),
        _msg("assistant", "hello"),
    ]
    result = apply_strategy(messages, "full")
    assert [m["role"] for m in result] == ["user", "assistant"]


def test_full_keeps_everything_else_unchanged() -> None:
    messages = [_msg("system", "sys"), _msg("user", "a"), _msg("tool", "raw output")]
    result = apply_strategy(messages, "full")
    assert result == [_msg("user", "a"), _msg("tool", "raw output")]


def test_last_10_keeps_only_last_ten_non_system_messages() -> None:
    messages = [_msg("system", "sys")] + [_msg("user", f"m{i}") for i in range(15)]
    result = apply_strategy(messages, "last_10")
    assert len(result) == 10
    assert result[0]["content"] == "m5"
    assert result[-1]["content"] == "m14"


def test_last_10_truncated_keeps_window_full_and_truncates_tool_before_it() -> None:
    messages = [_msg("system", "sys")]
    for i in range(12):
        messages.append(_msg("tool", f"tool-output-{i}"))
    result = apply_strategy(messages, "last_10_truncated")
    assert len(result) == 12
    # First two (outside the last-10 window) are truncated.
    assert result[0]["content"] == "<truncated>"
    assert result[1]["content"] == "<truncated>"
    # The window itself (last 10) stays intact.
    for i, m in enumerate(result[2:]):
        assert m["content"] == f"tool-output-{i + 2}"


def test_last_10_truncated_keeps_user_assistant_text_before_window() -> None:
    messages = [_msg("user", "please read SPEC.md")]
    messages += [_msg("user", f"m{i}") for i in range(10)]
    result = apply_strategy(messages, "last_10_truncated")
    assert result[0]["content"] == "please read SPEC.md"


def test_last_10_truncated_keeps_tool_call_names_and_arguments_before_window() -> None:
    call_message = _msg(
        "assistant",
        None,
        tool_calls=[
            {"id": "1", "function": {"name": "bash", "arguments": '{"command": "ls"}'}},
        ],
    )
    messages = [call_message] + [_msg("user", f"m{i}") for i in range(10)]
    result = apply_strategy(messages, "last_10_truncated")
    assert result[0]["tool_calls"][0]["function"]["name"] == "bash"
    assert result[0]["tool_calls"][0]["function"]["arguments"] == '{"command": "ls"}'


def test_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError):
        apply_strategy([], "bogus")


def test_summary_last_10_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        apply_strategy([_msg("user", "hi")], "summary_last_10")


def test_compact_transcript_drops_system_and_renders_string_content() -> None:
    messages = [_msg("system", "sys"), _msg("user", "hello there")]
    result = compact_transcript(messages)
    assert result == [{"role": "user", "text": "hello there"}]


def test_compact_transcript_handles_list_content_parts() -> None:
    messages = [_msg("user", [{"type": "text", "text": "part one"}, {"type": "text", "text": "part two"}])]
    result = compact_transcript(messages)
    assert result == [{"role": "user", "text": "part one\npart two"}]


def test_compact_transcript_includes_tool_calls_on_assistant() -> None:
    messages = [
        _msg(
            "assistant",
            "I'll run a command",
            tool_calls=[{"id": "1", "function": {"name": "bash", "arguments": '{"command": "pytest"}'}}],
        )
    ]
    result = compact_transcript(messages)
    assert result == [
        {
            "role": "assistant",
            "text": "I'll run a command",
            "tool_calls": [{"name": "bash", "args": '{"command": "pytest"}'}],
        }
    ]


def test_compact_transcript_truncates_long_tool_output() -> None:
    long_output = "x" * 5000
    messages = [_msg("tool", long_output)]
    result = compact_transcript(messages)
    assert len(result[0]["text"]) == 2000


def test_compact_transcript_handles_none_content() -> None:
    tool_call = {"id": "1", "function": {"name": "bash", "arguments": "{}"}}
    messages = [_msg("assistant", None, tool_calls=[tool_call])]
    result = compact_transcript(messages)
    assert result[0]["text"] == ""
    assert result[0]["tool_calls"] == [{"name": "bash", "args": "{}"}]
