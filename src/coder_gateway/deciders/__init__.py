"""Deciders: turn a Transcript into a Decision. (Stub: owned by deciders agent.)"""

from __future__ import annotations

from coder_gateway.domain import Decider


def build_decider(
    *,
    primary: str,
    fallback: str,
    timeout_s: float,
    jev_model: str,
    llm_model: str,
    openai_api_key: str | None,
    openai_base_url: str,
    typesafe_api_key: str | None,
) -> Decider:
    """Build the configured Decider. primary: 'jev' | 'llm' | 'none'; fallback: 'llm' | 'none'."""
    raise NotImplementedError
