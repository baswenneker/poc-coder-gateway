"""Benchmark: compare transcript strategies against fixed fixtures with known ground truth.

See PLAN.md, section "Benchmark: wat krijgt Jev als state?". The script chooses nothing,
it only reports: per strategy, how well the configured Decider recovers the ground-truth
verdict for each Rule when it only sees the transcript reduced by that strategy.

Usage: `uv run benchmark [--strategies full last_10 ...] [--decider jev|llm] [--repeats N]
[--fixtures GLOB] [--workflow PATH] [--out PATH]`.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import glob
import json
import math
import os
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from coder_gateway.deciders import build_decider
from coder_gateway.domain import Decider, DecisionInput, Message, WorkflowDefinition
from coder_gateway.transcript import STRATEGIES, apply_strategy
from coder_gateway.workflow import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES_GLOB = str(REPO_ROOT / "benchmark" / "fixtures" / "*.json")
DEFAULT_WORKFLOW = REPO_ROOT / "workflows" / "fwd-default.yaml"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_LLM_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_CONCURRENCY = 4


# --------------------------------------------------------------------------- fixtures


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    messages: list[Message]
    expected: dict[str, bool]
    path: str = ""


def _fixture_from_dict(data: dict[str, Any], path: str) -> Fixture:
    for key in ("name", "description", "messages", "expected"):
        if key not in data:
            raise ValueError(f"{path}: fixture is missing required key {key!r}")
    return Fixture(
        name=data["name"],
        description=data["description"],
        messages=list(data["messages"]),
        expected=dict(data["expected"]),
        path=path,
    )


def load_fixture(path: str | Path) -> Fixture:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return _fixture_from_dict(data, str(path))


def validate_fixture(fixture: Fixture, rule_ids: Sequence[str]) -> list[str]:
    """Return a list of human-readable problems with `fixture` (empty means valid)."""
    problems: list[str] = []
    missing = [rid for rid in rule_ids if rid not in fixture.expected]
    if missing:
        problems.append(f"{fixture.name}: expected is missing rule id(s) {missing}")
    unknown = [rid for rid in fixture.expected if rid not in rule_ids]
    if unknown:
        problems.append(f"{fixture.name}: expected has unknown rule id(s) {unknown}")
    non_bool = [rid for rid, v in fixture.expected.items() if not isinstance(v, bool)]
    if non_bool:
        problems.append(f"{fixture.name}: expected value(s) for {non_bool} are not booleans")
    problems.extend(f"{fixture.name}: {msg}" for msg in validate_messages(fixture.messages))
    return problems


def validate_messages(messages: Sequence[Message]) -> list[str]:
    """Check that every tool-result message's tool_call_id matches a preceding assistant tool call."""
    problems: list[str] = []
    seen_ids: set[str] = set()
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                call_id = call.get("id")
                if not call_id:
                    problems.append(f"message {i}: assistant tool_call without an id")
                    continue
                if call_id in seen_ids:
                    problems.append(f"message {i}: duplicate tool_call id {call_id!r}")
                seen_ids.add(call_id)
        elif role == "tool":
            call_id = msg.get("tool_call_id")
            if not call_id:
                problems.append(f"message {i}: tool message without tool_call_id")
            elif call_id not in seen_ids:
                problems.append(
                    f"message {i}: tool_call_id {call_id!r} does not match any preceding assistant tool_call"
                )
    return problems


def load_fixtures(pattern: str, rule_ids: Sequence[str] | None = None) -> list[Fixture]:
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise ValueError(f"no fixtures matched {pattern!r}")
    fixtures = [load_fixture(p) for p in paths]
    if rule_ids is not None:
        problems: list[str] = []
        for fx in fixtures:
            problems.extend(validate_fixture(fx, rule_ids))
        if problems:
            raise ValueError("invalid fixture(s):\n" + "\n".join(problems))
    return fixtures


# --------------------------------------------------------------------------- running


@dataclass(frozen=True)
class RunRecord:
    fixture: str
    strategy: str
    repeat: int
    rule_probabilities: dict[str, float] = field(default_factory=dict)
    rule_correct: dict[str, bool] = field(default_factory=dict)
    overall_correct: bool = False
    latency_ms: float | None = None
    input_tokens: int | None = None
    error: str | None = None


async def _run_one(
    decider: Decider,
    workflow: WorkflowDefinition,
    fixture: Fixture,
    strategy: str,
    repeat: int,
    semaphore: asyncio.Semaphore,
) -> RunRecord:
    async with semaphore:
        turn = sum(1 for m in fixture.messages if m.get("role") == "user")
        try:
            transcript = apply_strategy(fixture.messages, strategy)
            inp = DecisionInput(workflow=workflow, transcript=transcript, state={"turn": turn})
            decision = await decider.decide(inp)
        except Exception as exc:  # noqa: BLE001 - a broken Decider is a benchmark result, not a crash
            return RunRecord(fixture=fixture.name, strategy=strategy, repeat=repeat, error=repr(exc))

        if decision.error:
            return RunRecord(fixture=fixture.name, strategy=strategy, repeat=repeat, error=decision.error)

        rule_probabilities = {rid: v.probability for rid, v in decision.verdicts.items()}
        rule_correct = {
            rid: v.broken == fixture.expected[rid]
            for rid, v in decision.verdicts.items()
            if rid in fixture.expected
        }
        overall_correct = bool(rule_correct) and all(rule_correct.values())
        return RunRecord(
            fixture=fixture.name,
            strategy=strategy,
            repeat=repeat,
            rule_probabilities=rule_probabilities,
            rule_correct=rule_correct,
            overall_correct=overall_correct,
            latency_ms=decision.latency_ms,
            input_tokens=decision.input_tokens,
        )


async def evaluate(
    decider: Decider,
    workflow: WorkflowDefinition,
    fixtures: Sequence[Fixture],
    strategies: Sequence[str],
    repeats: int,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[RunRecord]:
    """Run decider.decide() for every (strategy, fixture, repeat) combination, bounded concurrency."""
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        _run_one(decider, workflow, fixture, strategy, repeat, semaphore)
        for strategy in strategies
        for fixture in fixtures
        for repeat in range(repeats)
    ]
    return list(await asyncio.gather(*tasks))


# --------------------------------------------------------------------------- reporting


@dataclass(frozen=True)
class StrategySummary:
    strategy: str
    rule_accuracy: dict[str, float]
    overall_accuracy: float
    mean_latency_ms: float
    p95_latency_ms: float
    mean_input_tokens: float
    error_count: int
    n: int


def _percentile(sorted_values: Sequence[float], p: float) -> float:
    if not sorted_values:
        return float("nan")
    idx = max(0, math.ceil(p * len(sorted_values)) - 1)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else float("nan")


def summarize(records: Sequence[RunRecord], rule_ids: Sequence[str]) -> dict[str, StrategySummary]:
    by_strategy: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        by_strategy[r.strategy].append(r)

    summaries: dict[str, StrategySummary] = {}
    for strategy, recs in by_strategy.items():
        ok = [r for r in recs if r.error is None]
        rule_accuracy = {
            rid: _mean([1.0 if r.rule_correct[rid] else 0.0 for r in ok if rid in r.rule_correct])
            for rid in rule_ids
        }
        overall_accuracy = _mean([1.0 if r.overall_correct else 0.0 for r in ok])
        latencies = sorted(r.latency_ms for r in ok if r.latency_ms is not None)
        tokens = [float(r.input_tokens) for r in ok if r.input_tokens is not None]
        summaries[strategy] = StrategySummary(
            strategy=strategy,
            rule_accuracy=rule_accuracy,
            overall_accuracy=overall_accuracy,
            mean_latency_ms=_mean(latencies),
            p95_latency_ms=_percentile(latencies, 0.95),
            mean_input_tokens=_mean(tokens),
            error_count=sum(1 for r in recs if r.error is not None),
            n=len(recs),
        )
    return summaries


def _format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("-+-".join("-" * w for w in widths))
    for row in rows:
        lines.append(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def _fmt(value: float, spec: str = ".2f") -> str:
    return "nan" if math.isnan(value) else format(value, spec)


def render_summary_table(
    summaries: dict[str, StrategySummary], rule_ids: Sequence[str], strategy_order: Sequence[str]
) -> str:
    headers = [
        "strategy",
        *[f"acc[{rid}]" for rid in rule_ids],
        "overall_acc",
        "mean_ms",
        "p95_ms",
        "mean_in_tok",
        "errors",
        "n",
    ]
    rows = []
    for strategy in strategy_order:
        summ = summaries.get(strategy)
        if summ is None:
            continue
        rows.append(
            [
                strategy,
                *[_fmt(summ.rule_accuracy.get(rid, float("nan"))) for rid in rule_ids],
                _fmt(summ.overall_accuracy),
                _fmt(summ.mean_latency_ms, ".0f"),
                _fmt(summ.p95_latency_ms, ".0f"),
                _fmt(summ.mean_input_tokens, ".0f"),
                str(summ.error_count),
                str(summ.n),
            ]
        )
    return _format_table(headers, rows)


def render_detail_table(
    records: Sequence[RunRecord],
    rule_ids: Sequence[str],
    fixture_order: Sequence[str],
    strategy_order: Sequence[str],
) -> str:
    grouped: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for r in records:
        grouped[(r.fixture, r.strategy)].append(r)

    headers = ["fixture", "strategy", *rule_ids]
    rows = []
    for fixture_name in fixture_order:
        for strategy in strategy_order:
            recs = grouped.get((fixture_name, strategy), [])
            if not recs:
                continue
            cells = []
            if all(r.error is not None for r in recs):
                cells = ["ERR"] * len(rule_ids)
            else:
                ok_recs = [r for r in recs if r.error is None]
                for rid in rule_ids:
                    probs = [r.rule_probabilities[rid] for r in ok_recs if rid in r.rule_probabilities]
                    corrects = [r.rule_correct[rid] for r in ok_recs if rid in r.rule_correct]
                    if not probs:
                        cells.append("ERR")
                        continue
                    mark = "✓" if all(corrects) else ("✗" if not any(corrects) else "~")
                    cells.append(f"{_mean(probs):.2f}{mark}")
            rows.append([fixture_name, strategy, *cells])
    return _format_table(headers, rows)


# --------------------------------------------------------------------------- CLI


@dataclass
class BenchmarkArgs:
    strategies: list[str]
    decider: str
    repeats: int
    fixtures: str
    workflow: str
    out: str
    concurrency: int


def parse_args(argv: Sequence[str] | None = None) -> BenchmarkArgs:
    parser = argparse.ArgumentParser(description="Compare Decider transcript strategies on fixtures.")
    parser.add_argument("--strategies", nargs="+", default=list(STRATEGIES), choices=list(STRATEGIES))
    parser.add_argument("--decider", choices=["jev", "llm"], default="jev")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--fixtures", default=DEFAULT_FIXTURES_GLOB)
    parser.add_argument("--workflow", default=str(DEFAULT_WORKFLOW))
    parser.add_argument("--out", default=None)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ns = parser.parse_args(argv)
    out = ns.out
    if out is None:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = str(REPO_ROOT / "var" / "benchmark" / f"{ts}.json")
    return BenchmarkArgs(
        strategies=ns.strategies,
        decider=ns.decider,
        repeats=ns.repeats,
        fixtures=ns.fixtures,
        workflow=ns.workflow,
        out=out,
        concurrency=ns.concurrency,
    )


def _load_env() -> None:
    load_dotenv(REPO_ROOT / ".env.local")
    load_dotenv(REPO_ROOT / ".env")


def _build_decider(name: str) -> Decider:
    # Single place to look up a Decider by name; add 'summary_last_10' etc. here later.
    return build_decider(
        primary=name,
        fallback="none",  # we measure the Decider itself, no fallback masking failures.
        timeout_s=DEFAULT_TIMEOUT_S,
        jev_model=DEFAULT_JEV_MODEL,
        llm_model=DEFAULT_LLM_MODEL,
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        openai_base_url=DEFAULT_OPENAI_BASE_URL,
        typesafe_api_key=os.environ.get("TYPESAFE_API_KEY"),
    )


RunOutcome = tuple[list[RunRecord], dict[str, StrategySummary], list[str], list[str]]


async def run(args: BenchmarkArgs) -> RunOutcome:
    _load_env()
    workflow = load_workflow(args.workflow)
    rule_ids = [r.id for r in workflow.rules]
    fixtures = load_fixtures(args.fixtures, rule_ids=rule_ids)
    decider = _build_decider(args.decider)
    records = await evaluate(decider, workflow, fixtures, args.strategies, args.repeats, args.concurrency)
    summaries = summarize(records, rule_ids)
    fixture_order = [fx.name for fx in fixtures]
    return records, summaries, rule_ids, fixture_order


def write_results(
    out_path: str,
    args: BenchmarkArgs,
    records: Sequence[RunRecord],
    summaries: dict[str, StrategySummary],
) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "args": dataclasses.asdict(args),
        "summaries": {k: dataclasses.asdict(v) for k, v in summaries.items()},
        "records": [dataclasses.asdict(r) for r in records],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


async def _amain(args: BenchmarkArgs) -> None:
    records, summaries, rule_ids, fixture_order = await run(args)
    print(f"# Coder Gateway benchmark — decider={args.decider} repeats={args.repeats}\n")
    print("## Per strategy\n")
    print(render_summary_table(summaries, rule_ids, args.strategies))
    print("\n## Per fixture\n")
    print(render_detail_table(records, rule_ids, fixture_order, args.strategies))
    write_results(args.out, args, records, summaries)
    print(f"\nWrote results to {args.out}")


def main() -> None:
    args = parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
