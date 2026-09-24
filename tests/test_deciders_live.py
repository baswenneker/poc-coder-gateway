"""Live tests: call the real Jev (typesafe.ai) and the real OpenAI LLM fallback.

Deselected by default (`addopts = "-m 'not live'"` in pyproject.toml); run with:
    uv run pytest -m live -q

Needs TYPESAFE_API_KEY and OPENAI_API_KEY, loaded from .env.local (see PLAN.md / task brief).
Three hand-written transcripts with a known expected outcome per Rule, taken from the task brief:
(a) code change requested, no issue mentioned -> propose_issue & flag_no_spec broken
(b) issue referenced and SPEC.md read before implementing -> neither broken
(c) PR requested with no test run reported -> block_pr_without_tests broken
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from openai import AsyncOpenAI
from typesafe_sdk import AsyncTypeSafeClient

from coder_gateway.deciders.jev import JevDecider
from coder_gateway.deciders.llm import LLMDecider
from coder_gateway.domain import Decider, Decision, DecisionInput, Message
from coder_gateway.workflow import load_workflow

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env.local")

WORKFLOW = load_workflow(REPO_ROOT / "workflows" / "fwd-default.yaml")

# Transcripts end where the Gateway decides (DECISIONS.md #28): the final assistant message of a Turn,
# or an assistant message with a triggering tool call.
TRANSCRIPT_NO_ISSUE_NO_SPEC: list[Message] = [
    {"role": "user", "content": "please add a retry to src/net.py"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "edit",
                    "arguments": '{"filePath": "src/net.py", "oldString": "return get(url)", '
                    '"newString": "return retry(get, url, attempts=3)"}',
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "Edit applied successfully."},
    {"role": "assistant", "content": "Done: fetch now retries up to 3 times."},
]

TRANSCRIPT_WITH_ISSUE_AND_SPEC: list[Message] = [
    {"role": "user", "content": "implement #42 as described in SPEC.md"},
    {
        "role": "assistant",
        "content": "Let me read SPEC.md first.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read", "arguments": '{"filePath": "SPEC.md"}'},
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "# Spec for #42\nAdd a `--verbose` flag to the CLI that prints extra logging.",
    },
    {
        "role": "assistant",
        "content": "Got it, I'll implement the --verbose flag as described in issue #42 / SPEC.md.",
    },
]

TRANSCRIPT_PR_WITHOUT_TESTS: list[Message] = [
    {"role": "user", "content": "implement #7: add a --verbose flag"},
    {
        "role": "assistant",
        "content": "Done, I added the --verbose flag.",
    },
    {"role": "user", "content": "great, now create a PR"},
    {
        "role": "assistant",
        "content": "Creating the pull request.",
        "tool_calls": [
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command": "gh pr create --fill"}'},
            }
        ],
    },
]


def _jev_decider() -> JevDecider:
    client = AsyncTypeSafeClient(api_key=os.environ["TYPESAFE_API_KEY"], model="jev-latest")
    return JevDecider(client, model="jev-latest")


def _llm_decider() -> LLMDecider:
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return LLMDecider(client.chat.completions, model="gpt-5.4-mini")


async def _decide(decider: Decider, transcript: list[Message]) -> Decision:
    inp = DecisionInput(workflow=WORKFLOW, transcript=transcript, state={})
    decision = await decider.decide(inp)
    print(
        f"\n[{decider.name}] latency={decision.latency_ms:.0f}ms "
        f"tokens(in={decision.input_tokens},out={decision.output_tokens}) phase={decision.phase}"
    )
    for rule_id, verdict in decision.verdicts.items():
        print(f"    {rule_id}: p={verdict.probability:.2f} broken={verdict.broken}")
    return decision


@pytest.fixture(params=["jev", "llm"])
def live_decider(request: pytest.FixtureRequest) -> Decider:
    if request.param == "jev":
        return _jev_decider()
    return _llm_decider()


async def test_no_issue_no_spec_flags_both_rules(live_decider: Decider) -> None:
    decision = await _decide(live_decider, TRANSCRIPT_NO_ISSUE_NO_SPEC)
    assert decision.verdicts["propose_issue"].broken is True
    assert decision.verdicts["flag_no_spec"].broken is True
    assert decision.verdicts["block_pr_without_tests"].broken is False


async def test_issue_and_spec_referenced_flags_neither(live_decider: Decider) -> None:
    decision = await _decide(live_decider, TRANSCRIPT_WITH_ISSUE_AND_SPEC)
    assert decision.verdicts["propose_issue"].broken is False
    assert decision.verdicts["flag_no_spec"].broken is False


async def test_pr_without_tests_blocks(live_decider: Decider) -> None:
    decision = await _decide(live_decider, TRANSCRIPT_PR_WITHOUT_TESTS)
    assert decision.verdicts["block_pr_without_tests"].broken is True
