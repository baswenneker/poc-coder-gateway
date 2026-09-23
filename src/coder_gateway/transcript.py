"""Transcript strategies: which part of the Conversation a Decider sees.

Vocabulary follows CONTEXT.md: the Transcript is the list of messages of a Conversation as
handed to the Decision. Messages are OpenAI chat-completions messages (see domain.Message):
`content` may be a plain string or a list of parts (e.g. `{"type": "text", "text": ...}`),
assistant messages may carry `tool_calls`, and tool-result messages have `role == "tool"`.
"""

from __future__ import annotations

from typing import Any

from coder_gateway.domain import Message

# 'summary_last_10' is reserved for later (PLAN.md: "later" strategy, LLM-generated summary of
# everything before the last 10 messages). Not implemented yet: apply_strategy raises for it.
STRATEGIES: tuple[str, ...] = ("full", "last_10", "last_10_truncated", "summary_last_10")

_LAST_N = 10
_TOOL_TEXT_LIMIT = 2000


def _is_non_system(message: Message) -> bool:
    return message.get("role") != "system"


def _truncate_tool_message(message: Message) -> Message:
    """Return a copy of a role='tool' message with its content replaced by a marker."""
    truncated = dict(message)
    truncated["content"] = "<truncated>"
    return truncated


def _strip_tool_result_content(message: Message) -> Message:
    """For a non-tool message, keep text and tool_calls (name + arguments); drop nothing else
    relevant. Non-tool messages already carry only text/tool_calls, so they pass through as-is.
    """
    return message


def apply_strategy(messages: list[Message], strategy: str) -> list[Message]:
    """Reduce the request messages to the Transcript for the Decider. Always drops role='system'.

    Strategies:
    - 'full': every non-system message, unchanged.
    - 'last_10': the last 10 non-system messages, unchanged.
    - 'last_10_truncated': the last 10 non-system messages in full; every non-system message
      before that window keeps its user/assistant text and tool-call names+arguments, but has
      role='tool' content replaced with '<truncated>'.

    Note on slicing: cutting the window at "last 10 messages" can separate an assistant message
    that made tool_calls from the role='tool' results that answer them (the results may fall
    inside the window while the call falls outside it, or vice versa). For the Decider this is
    harmless: messages are handed over as text for a probability judgement, not replayed as a
    real conversation, so a dangling tool_call or an orphaned tool result is just slightly less
    context, never a structural error.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown transcript strategy: {strategy!r}")
    if strategy == "summary_last_10":
        raise NotImplementedError("'summary_last_10' is a placeholder for later (see PLAN.md)")

    non_system = [m for m in messages if _is_non_system(m)]

    if strategy == "full":
        return non_system

    if strategy == "last_10":
        return non_system[-_LAST_N:]

    # last_10_truncated
    window = non_system[-_LAST_N:]
    before = non_system[: len(non_system) - len(window)]
    reduced_before = [
        _truncate_tool_message(m) if m.get("role") == "tool" else _strip_tool_result_content(m)
        for m in before
    ]
    return reduced_before + window


def _content_to_text(content: Any) -> str:
    """Render a message's `content` (string or list of parts) as plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(content)


def _tool_calls_view(message: Message) -> list[dict[str, Any]] | None:
    tool_calls = message.get("tool_calls")
    if not tool_calls:
        return None
    view: list[dict[str, Any]] = []
    for call in tool_calls:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        view.append({"name": function.get("name"), "args": function.get("arguments")})
    return view


def compact_transcript(messages: list[Message]) -> list[dict[str, Any]]:
    """A compact, token-efficient JSON view of a Transcript for the Decider.

    [{"role": "user", "text": ...},
     {"role": "assistant", "text": ..., "tool_calls": [{"name": "bash", "args": {...}}]},
     {"role": "tool", "text": <tool output, truncated to _TOOL_TEXT_LIMIT chars>}]

    Assumes system messages were already dropped by `apply_strategy`; any that remain are
    dropped here too, so a Decider never sees the (long, irrelevant) system prompt.
    """
    compact: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        text = _content_to_text(message.get("content"))
        if role == "tool":
            entry: dict[str, Any] = {"role": "tool", "text": text[:_TOOL_TEXT_LIMIT]}
            compact.append(entry)
            continue
        entry = {"role": role, "text": text}
        tool_calls = _tool_calls_view(message)
        if tool_calls:
            entry["tool_calls"] = tool_calls
        compact.append(entry)
    return compact
