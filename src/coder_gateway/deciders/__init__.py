"""Deciders: turn a Transcript into a Decision."""

from __future__ import annotations

from coder_gateway.deciders.fallback import FallbackDecider, NoneDecider, SoloDecider
from coder_gateway.deciders.jev import JevDecider
from coder_gateway.deciders.llm import LLMDecider
from coder_gateway.domain import Decider

__all__ = [
    "FallbackDecider",
    "JevDecider",
    "LLMDecider",
    "NoneDecider",
    "SoloDecider",
    "build_decider",
]


def _build_named(
    name: str,
    *,
    jev_model: str,
    llm_model: str,
    openai_api_key: str | None,
    openai_base_url: str,
    typesafe_api_key: str | None,
) -> Decider:
    if name == "none":
        return NoneDecider()
    if name == "jev":
        from typesafe_sdk import AsyncTypeSafeClient

        client = AsyncTypeSafeClient(api_key=typesafe_api_key, model=jev_model)
        return JevDecider(client, model=jev_model)
    if name == "llm":
        from openai import AsyncOpenAI

        openai_client = AsyncOpenAI(api_key=openai_api_key, base_url=openai_base_url)
        return LLMDecider(openai_client.chat.completions, model=llm_model)
    raise ValueError(f"unknown decider: {name!r}")


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
    """Build the configured Decider. primary: 'jev' | 'llm' | 'none'; fallback: 'llm' | 'none'.

    fallback='none' means exactly that: no fallback Decider is tried. The primary still fails
    open on exception/timeout (see docs/DECISIONS.md #5), via SoloDecider -- not by wrapping the
    primary in a FallbackDecider with NoneDecider as its fallback, which would turn a primary
    failure into a Decision that looks like a correct, error-free "nothing broken" answer (see
    docs/DECISIONS.md #10 and the benchmark finding this fixes).
    """
    kwargs: dict[str, object] = {
        "jev_model": jev_model,
        "llm_model": llm_model,
        "openai_api_key": openai_api_key,
        "openai_base_url": openai_base_url,
        "typesafe_api_key": typesafe_api_key,
    }
    primary_decider = _build_named(primary, **kwargs)  # type: ignore[arg-type]
    if primary == "none":
        return primary_decider
    if fallback == "none":
        return SoloDecider(primary_decider, timeout_s)
    fallback_decider = _build_named(fallback, **kwargs)  # type: ignore[arg-type]
    return FallbackDecider(primary_decider, fallback_decider, timeout_s)
