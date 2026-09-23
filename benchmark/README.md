# Benchmark fixtures

Fixtures for `uv run benchmark` (see `src/coder_gateway/benchmark.py` and the
"Benchmark: wat krijgt Jev als state?" section of `PLAN.md`).

## Format

Each `benchmark/fixtures/*.json` file:

```json
{
  "name": "...",
  "description": "...",
  "messages": [ /* OpenAI chat-completions messages, opencode-shaped */ ],
  "expected": {
    "propose_issue": true|false,
    "flag_no_spec": true|false,
    "block_pr_without_tests": true|false
  }
}
```

`messages` follows the shape opencode actually sends (see
`tests/fixtures/opencode_agent_request.json`): plain `user`/`assistant` turns,
assistant `tool_calls` with JSON-string `arguments` (tool names `bash`, `edit`,
`read`, `write`, `glob`, `grep`, `question`), and `tool` role results carrying
the matching `tool_call_id`.

`expected` is the ground truth for the **whole conversation as given** — i.e.
what a correct Decision would be for the last (most recent) request, looking
at the full `messages` list. The benchmark script applies each transcript
strategy (`full`, `last_10`, `last_10_truncated`, ...) to that same list and
compares the Decider's verdict on the reduced transcript against this ground
truth. A strategy is allowed to score worse than `full` — that's exactly what
the benchmark measures.

## Ground truth reasoning per fixture

- `clean_all_good` — issue referenced up front, SPEC.md read via the `read` tool, tests run and green right after the last edit, then a PR is requested: nothing should fire.
- `no_issue_coding_starts` — code is fixed with no issue and no spec ever mentioned: both `propose_issue` and `flag_no_spec` fire; no PR requested yet.
- `issue_only_flag_no_spec` — issue id `#482` referenced, but no spec file ever discussed: only `flag_no_spec` fires.
- `spec_read_no_issue` — spec doc read via the `read` tool, but no issue ever referenced: only `propose_issue` fires.
- `pr_without_tests` — issue and spec both fine, but a PR is requested with zero test runs anywhere: `block_pr_without_tests` fires.
- `pr_after_failed_tests` — tests were run but FAILED, PR requested anyway without a fix-and-rerun: `block_pr_without_tests` fires.
- `tests_passed_before_last_edit` — tests passed, then one more edit was made afterwards without re-running, before the PR request: the green run is stale, `block_pr_without_tests` fires.
- `tests_passed_after_last_edit_pr` — tests pass right after the last edit, then PR requested: correct order, nothing fires.
- `pure_exploration_questions` — only reads/greps and questions, no code change requested, no PR: nothing fires.
- `gh_pr_create_in_bash` — `gh pr create` appears inside a `bash` tool call (not spoken by the developer), no tests shown: `block_pr_without_tests` fires regardless of how the PR request surfaced.
- `pr_request_in_words_dutch` — Dutch developer message asking in plain words for a pull request, no tests shown: `block_pr_without_tests` fires; rules must work in Dutch too.
- `dutch_developer_no_issue` — Dutch conversation, bug fix requested with no issue and no spec ever mentioned: both `propose_issue` and `flag_no_spec` fire.
- `issue_url_github` — issue referenced as a full GitHub issue URL rather than a short id: `propose_issue` stays quiet (URL counts as a reference); no spec discussed, so `flag_no_spec` fires.
- `long_conversation_early_issue_lost` — 27+ messages; the issue (JIRA-410) is mentioned only in the very first message, followed by many rounds of unrelated exploration and then more edits. Ground truth over the *full* transcript is `propose_issue=false`; this is where `last_10`-style strategies are expected to disagree with the `full` strategy, since they no longer see the first message.
- `long_conversation_early_spec_lost` — 27+ messages; `docs/caching-spec.md` is read once near the start, then many exploration turns and further edits happen much later. Ground truth is `flag_no_spec=false`; a `last_10` strategy would lose that early read and likely misjudge it.
- `combination_no_issue_no_spec_pr_no_tests` — worst case: no issue, no spec, PR requested, no tests: all three rules fire.
- `lint_passed_not_tests` — only the linter was run (and passed), the actual test suite never ran, then a PR is requested: `block_pr_without_tests` fires — lint is not tests.
- `declined_proposal_still_open` — the developer already declined the `propose_issue` Proposal earlier (via the `question` tool) and coding continued anyway with no issue and no spec: both rules still fire — a declined Proposal doesn't clear the underlying Rule (see CONTEXT.md, "Proposal").

## Adding a fixture

Keep it hand-written and realistic (or generate it with a throwaway script and
commit the resulting JSON, as was done here). Every `tool` message's
`tool_call_id` must match a preceding assistant `tool_calls[].id`. Add the new
rule ids to `expected` if the workflow definition grows new rules.
