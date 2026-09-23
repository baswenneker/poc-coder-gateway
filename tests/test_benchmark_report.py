"""Report aggregation logic, exercised with a fake Decider (no network)."""

from __future__ import annotations

import math

from gateway_helpers import make_workflow

from coder_gateway.benchmark import (
    Fixture,
    RunRecord,
    evaluate,
    parse_args,
    render_detail_table,
    render_summary_table,
    summarize,
)
from coder_gateway.domain import Decision, DecisionInput, RuleVerdict

WORKFLOW = make_workflow()
RULE_IDS = [r.id for r in WORKFLOW.rules]


class FakeDecider:
    """A Decider whose verdicts depend only on the fixture's own expected outcome and strategy,
    so tests can assert precisely on strategy-dependent accuracy without any network calls.
    """

    name = "fake"

    def __init__(self, wrong_on_strategy: str | None = None, error_on_fixture: str | None = None) -> None:
        self.wrong_on_strategy = wrong_on_strategy
        self.error_on_fixture = error_on_fixture
        self.calls: list[DecisionInput] = []

    async def decide(self, inp: DecisionInput) -> Decision:
        self.calls.append(inp)
        # Recover which fixture/strategy this is via the transcript's first message text.
        first_text = inp.transcript[0]["content"] if inp.transcript else ""
        if self.error_on_fixture and self.error_on_fixture in str(first_text):
            return Decision(verdicts={}, decider=self.name, latency_ms=5.0, error="boom")
        wrong = self.wrong_on_strategy is not None and inp.state.get("strategy") == self.wrong_on_strategy
        verdicts = {}
        for rid in RULE_IDS:
            expected_broken = bool(inp.state["expected"][rid])
            broken = (not expected_broken) if wrong else expected_broken
            verdicts[rid] = RuleVerdict(rid, 0.9 if broken else 0.1, broken)
        return Decision(verdicts=verdicts, decider=self.name, latency_ms=10.0, input_tokens=123)


def _fixture(name: str, expected: dict[str, bool]) -> Fixture:
    return Fixture(
        name=name,
        description="d",
        messages=[{"role": "user", "content": name}],
        expected=expected,
    )


ALL_OK = {rid: False for rid in RULE_IDS}
ALL_BROKEN = {rid: True for rid in RULE_IDS}


# FakeDecider needs strategy/expected in state to behave deterministically; evaluate() only
# passes {"turn": ...}, so we drive it through a thin subclass for these report-focused tests.
class StrategyAwareFakeDecider(FakeDecider):
    def __init__(self, fixtures: dict[str, Fixture], wrong_on_strategy: str | None = None) -> None:
        super().__init__(wrong_on_strategy=wrong_on_strategy)
        self._fixtures = fixtures

    async def decide(self, inp: DecisionInput) -> Decision:
        first_text = str(inp.transcript[0]["content"]) if inp.transcript else ""
        fixture = self._fixtures[first_text]
        inp = DecisionInput(
            workflow=inp.workflow,
            transcript=inp.transcript,
            state={**inp.state, "expected": fixture.expected},
        )
        return await super().decide(inp)


async def _decide_records(strategies: list[str], repeats: int = 1) -> list[RunRecord]:
    fixtures = [_fixture("clean", ALL_OK), _fixture("dirty", ALL_BROKEN)]
    by_name = {f.name: f for f in fixtures}
    decider = StrategyAwareFakeDecider(by_name)
    return await evaluate(decider, WORKFLOW, fixtures, strategies, repeats)


async def test_evaluate_produces_one_record_per_strategy_fixture_repeat() -> None:
    records = await _decide_records(["full", "last_10"], repeats=2)
    assert len(records) == 2 * 2 * 2  # strategies * fixtures * repeats


async def test_evaluate_marks_correct_decision_as_correct() -> None:
    records = await _decide_records(["full"])
    by_fixture = {r.fixture: r for r in records}
    assert by_fixture["clean"].overall_correct is True
    assert by_fixture["dirty"].overall_correct is True
    assert all(by_fixture["clean"].rule_correct.values())


async def test_evaluate_captures_decider_error() -> None:
    fixtures = [_fixture("boom", ALL_OK)]
    decider = FakeDecider(error_on_fixture="boom")
    records = await evaluate(decider, WORKFLOW, fixtures, ["full"], 1)
    assert len(records) == 1
    assert records[0].error == "boom"
    assert records[0].overall_correct is False


async def test_evaluate_captures_decider_exception() -> None:
    class RaisingDecider:
        name = "raiser"

        async def decide(self, inp: DecisionInput) -> Decision:
            raise RuntimeError("kaboom")

    fixtures = [_fixture("x", ALL_OK)]
    records = await evaluate(RaisingDecider(), WORKFLOW, fixtures, ["full"], 1)
    assert records[0].error is not None
    assert "kaboom" in records[0].error


def test_summarize_computes_accuracy_and_latency() -> None:
    records = [
        RunRecord(
            fixture="f1",
            strategy="full",
            repeat=0,
            rule_probabilities={rid: 0.9 for rid in RULE_IDS},
            rule_correct=dict.fromkeys(RULE_IDS, True),
            overall_correct=True,
            latency_ms=100.0,
            input_tokens=200,
        ),
        RunRecord(
            fixture="f2",
            strategy="full",
            repeat=0,
            rule_probabilities=dict.fromkeys(RULE_IDS, 0.1),
            rule_correct={RULE_IDS[0]: False, **dict.fromkeys(RULE_IDS[1:], True)},
            overall_correct=False,
            latency_ms=200.0,
            input_tokens=400,
        ),
    ]
    summaries = summarize(records, RULE_IDS)
    summ = summaries["full"]
    assert summ.rule_accuracy[RULE_IDS[0]] == 0.5
    assert summ.overall_accuracy == 0.5
    assert summ.mean_latency_ms == 150.0
    assert summ.mean_input_tokens == 300.0
    assert summ.error_count == 0
    assert summ.n == 2


def test_summarize_excludes_errored_records_from_accuracy_but_counts_them() -> None:
    records = [
        RunRecord(fixture="f1", strategy="full", repeat=0, error="boom"),
        RunRecord(
            fixture="f2",
            strategy="full",
            repeat=0,
            rule_correct=dict.fromkeys(RULE_IDS, True),
            overall_correct=True,
        ),
    ]
    summ = summarize(records, RULE_IDS)["full"]
    assert summ.overall_accuracy == 1.0
    assert summ.error_count == 1
    assert summ.n == 2


def test_summarize_nan_when_no_successful_records() -> None:
    records = [RunRecord(fixture="f1", strategy="full", repeat=0, error="boom")]
    summ = summarize(records, RULE_IDS)["full"]
    assert math.isnan(summ.overall_accuracy)
    assert summ.error_count == 1


MARKER = "ISSUE-1 REFERENCED HERE"


class MarkerDecider:
    """A Decider that only 'sees' a Rule as satisfied if MARKER is present in its transcript.

    Used to show, with the real `apply_strategy`, that a strategy which drops early messages
    (like 'last_10') can genuinely disagree with the 'full' strategy on the same fixture.
    """

    name = "marker"

    async def decide(self, inp: DecisionInput) -> Decision:
        text_blob = " ".join(str(m.get("content", "")) for m in inp.transcript)
        broken = MARKER not in text_blob
        verdicts = {rid: RuleVerdict(rid, 0.9 if broken else 0.1, broken) for rid in RULE_IDS}
        return Decision(verdicts=verdicts, decider=self.name, latency_ms=1.0, input_tokens=1)


def _long_fixture_with_early_marker(n_filler: int = 14) -> Fixture:
    # MARKER only appears in the very first message; a 'last_10' strategy drops it once there
    # are enough messages, while 'full' keeps it. Ground truth (over the full conversation) is
    # "nothing broken", matching what MarkerDecider answers when it can still see MARKER.
    messages = [{"role": "user", "content": MARKER}]
    messages += [{"role": "user", "content": f"filler {i}"} for i in range(n_filler)]
    return Fixture(name="long", description="d", messages=messages, expected=ALL_OK)


async def test_last_10_strategy_can_score_worse_than_full() -> None:
    fixture = _long_fixture_with_early_marker()
    records = await evaluate(MarkerDecider(), WORKFLOW, [fixture], ["full", "last_10"], 1)
    summaries = summarize(records, RULE_IDS)
    assert summaries["full"].overall_accuracy == 1.0
    assert summaries["last_10"].overall_accuracy == 0.0


# --- rendering --------------------------------------------------------------------------------


def test_render_summary_table_contains_strategy_rows() -> None:
    records = [
        RunRecord(
            fixture="f1",
            strategy="full",
            repeat=0,
            rule_correct=dict.fromkeys(RULE_IDS, True),
            overall_correct=True,
            latency_ms=42.0,
            input_tokens=10,
        )
    ]
    summaries = summarize(records, RULE_IDS)
    table = render_summary_table(summaries, RULE_IDS, ["full"])
    assert "full" in table
    assert "overall_acc" in table
    assert "1.00" in table


def test_render_detail_table_marks_correct_and_incorrect() -> None:
    records = [
        RunRecord(
            fixture="f1",
            strategy="full",
            repeat=0,
            rule_probabilities=dict.fromkeys(RULE_IDS, 0.9),
            rule_correct=dict.fromkeys(RULE_IDS, True),
            overall_correct=True,
        ),
        RunRecord(
            fixture="f2",
            strategy="full",
            repeat=0,
            rule_probabilities=dict.fromkeys(RULE_IDS, 0.9),
            rule_correct=dict.fromkeys(RULE_IDS, False),
            overall_correct=False,
        ),
    ]
    table = render_detail_table(records, RULE_IDS, ["f1", "f2"], ["full"])
    assert "✓" in table
    assert "✗" in table


def test_render_detail_table_marks_errors() -> None:
    records = [RunRecord(fixture="f1", strategy="full", repeat=0, error="boom")]
    table = render_detail_table(records, RULE_IDS, ["f1"], ["full"])
    assert "ERR" in table


# --- CLI ----------------------------------------------------------------------------------


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.decider == "jev"
    assert args.repeats == 1
    assert "full" in args.strategies
    assert args.out.endswith(".json")


def test_parse_args_overrides() -> None:
    args = parse_args(["--decider", "llm", "--repeats", "3", "--strategies", "full", "last_10"])
    assert args.decider == "llm"
    assert args.repeats == 3
    assert args.strategies == ["full", "last_10"]
