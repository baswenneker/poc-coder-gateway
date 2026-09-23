"""Conversation identity: session header when the client sends one, else a Fingerprint."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from coder_gateway.domain import Message

SESSION_HEADER = "x-session-id"


def content_text(content: Any) -> str:
    """Plain text of a message `content`: a string, or a list of parts ({type: text, text: ...})."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return str(content)


def user_messages(messages: list[Message]) -> list[Message]:
    return [m for m in messages if m.get("role") == "user"]


def conversation_id(headers: Mapping[str, str], messages: list[Message]) -> str:
    """'sid:<x-session-id>' if the header is present, else 'fp:' + 16 hex chars of sha256(first user text)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    sid = (lowered.get(SESSION_HEADER) or "").strip()
    if sid:
        return f"sid:{sid}"
    users = user_messages(messages)
    first = content_text(users[0].get("content")).strip() if users else ""
    return "fp:" + hashlib.sha256(first.encode("utf-8")).hexdigest()[:16]


def store_key(virtual_model: str, conv_id: str) -> str:
    """Key in the ConversationStore: the same Conversation id under another Virtual Model is separate."""
    return f"{virtual_model}/{conv_id}"
