"""Transcript strategies: which part of the Conversation a Decider sees. (Stub: owned by deciders agent.)"""

from __future__ import annotations

from coder_gateway.domain import Message

STRATEGIES: tuple[str, ...] = ("full", "last_10", "last_10_truncated")


def apply_strategy(messages: list[Message], strategy: str) -> list[Message]:
    """Reduce the request messages to the Transcript for the Decider. Drops system messages."""
    raise NotImplementedError
