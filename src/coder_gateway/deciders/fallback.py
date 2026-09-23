"""Deciders that never leave the Gateway without a Decision: NoneDecider and FallbackDecider.

See docs/DECISIONS.md #5: the Decision sits synchronously in the request path with a timeout;
if the primary Decider fails or times out, a fallback Decider is tried; if that also fails, the
Gateway fails open (a Decision with no broken Rules and `error` set), so a broken Decider never
blocks the Developer.
"""

from __future__ import annotations

import asyncio
import logging
import time

from coder_gateway.domain import Decider, Decision, DecisionInput, RuleVerdict

log = logging.getLogger("coder_gateway.deciders")


class NoneDecider:
    """Instant Decider that reports every Rule as not broken. Used as `primary='none'`, as the
    terminal fallback, and in tests.
    """

    name = "none"

    async def decide(self, inp: DecisionInput) -> Decision:
        verdicts = {
            rule.id: RuleVerdict(rule_id=rule.id, probability=0.0, broken=False)
            for rule in inp.workflow.rules
        }
        return Decision(verdicts=verdicts, decider=self.name, latency_ms=0.0)


def _fail_open(inp: DecisionInput, decider_name: str, error: str, latency_ms: float = 0.0) -> Decision:
    verdicts = {
        rule.id: RuleVerdict(rule_id=rule.id, probability=0.0, broken=False) for rule in inp.workflow.rules
    }
    return Decision(verdicts=verdicts, decider=decider_name, latency_ms=latency_ms, error=error)


class FallbackDecider:
    """Runs `primary` with a timeout; on exception or timeout runs `fallback` (also timed out).
    If both fail, fails open: a Decision with no broken Rules and `error` set.
    """

    def __init__(self, primary: Decider, fallback: Decider, timeout_s: float) -> None:
        self._primary = primary
        self._fallback = fallback
        self._timeout_s = timeout_s
        self.name = f"fallback({primary.name}->{fallback.name})"

    async def decide(self, inp: DecisionInput) -> Decision:
        start = time.perf_counter()
        try:
            return await asyncio.wait_for(self._primary.decide(inp), timeout=self._timeout_s)
        except Exception as primary_error:  # noqa: BLE001 - any primary failure triggers fallback
            primary_elapsed_ms = (time.perf_counter() - start) * 1000
            log.warning(
                "decider %s failed after %.0fms (%r); trying %s",
                self._primary.name,
                primary_elapsed_ms,
                primary_error,
                self._fallback.name,
            )
            try:
                return await asyncio.wait_for(self._fallback.decide(inp), timeout=self._timeout_s)
            except Exception as fallback_error:  # noqa: BLE001 - both failed: fail open
                total_elapsed_ms = (time.perf_counter() - start) * 1000
                error = (
                    f"primary {self._primary.name!r} failed after {primary_elapsed_ms:.0f}ms: "
                    f"{primary_error!r}; fallback {self._fallback.name!r} failed: {fallback_error!r}"
                )
                return _fail_open(inp, self.name, error, latency_ms=total_elapsed_ms)


class SoloDecider:
    """Runs `primary` with a timeout and no fallback (`fallback='none'`, see
    docs/DECISIONS.md #10 and the benchmark finding it was written to fix). On exception or
    timeout it fails open like FallbackDecider (no broken Rules), but crucially with `error` set
    and `latency_ms` reflecting the failed attempt.

    This is deliberately NOT `FallbackDecider(primary, NoneDecider(), timeout_s)`: that would make
    the "fallback" (NoneDecider) *succeed* on a primary failure, producing a Decision with
    error=None and latency_ms=0.0 that looks like a correct "nothing broken" answer instead of a
    failed one -- e.g. in the benchmark, silently counted as a correct prediction.
    """

    def __init__(self, primary: Decider, timeout_s: float) -> None:
        self._primary = primary
        self._timeout_s = timeout_s
        self.name = f"solo({primary.name})"

    async def decide(self, inp: DecisionInput) -> Decision:
        start = time.perf_counter()
        try:
            return await asyncio.wait_for(self._primary.decide(inp), timeout=self._timeout_s)
        except Exception as primary_error:  # noqa: BLE001 - fail open, but report the failure
            elapsed_ms = (time.perf_counter() - start) * 1000
            error = f"primary {self._primary.name!r} failed after {elapsed_ms:.0f}ms: {primary_error!r}"
            return _fail_open(inp, self.name, error, latency_ms=elapsed_ms)
