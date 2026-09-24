# Benchmark fixtures

Fixtures for `uv run benchmark` (see `src/coder_gateway/benchmark.py`). The benchmark measures
how well the Decider judges each Rule when it sees a reduced Transcript. Strategies:

1. `full`: the whole conversation
2. `last_10`: only the last 10 messages
3. `last_10_truncated`: the last 10 messages in full, tool results before that replaced by `<truncated>`
4. `summary_last_10` (not implemented yet): a summary plus the last 10 messages

Output per strategy: accuracy per Rule at the configured threshold, average latency and tokens per
call. The script picks nothing; it reports.

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
what a correct Decision would be at the point where the conversation ends,
looking at the full `messages` list.

Each fixture ends where the Gateway now decides (`docs/DECISIONS.md` #28):

- **End of a Turn**: the last message is the assistant's final reply (text, no
  tool calls). `propose_issue` is judged after the fact: was code changed
  without an issue?
- **Trigger point**: the last message is an assistant message with a tool call
  that matches the trigger of `block_pr_without_tests`
  (`gh pr create|glab mr create|git push`), which has not run yet. The Rule is
  judged on that tool call.

A fixture never ends with a user message or a tool result: the Gateway does not
decide there. The benchmark script applies each transcript
strategy (`full`, `last_10`, `last_10_truncated`, ...) to that same list and
compares the Decider's verdict on the reduced transcript against this ground
truth. A strategy is allowed to score worse than `full` — that's exactly what
the benchmark measures.

## Ground truth reasoning per fixture

Trigger point (ends with a `gh pr create` / `git push` tool call):

- `clean_all_good` — issue referenced up front, SPEC.md read via the `read` tool, tests run and green right after the last edit, then a PR is requested and the assistant runs `gh pr create`: nothing should fire.
- `pr_without_tests` — issue and spec both fine, but the assistant runs `gh pr create` with zero test runs anywhere: `block_pr_without_tests` fires.
- `pr_after_failed_tests` — tests were run but FAILED, PR attempted anyway without a fix-and-rerun: `block_pr_without_tests` fires.
- `tests_passed_before_last_edit` — tests passed, then one more edit was made afterwards without re-running, before the PR attempt: the green run is stale, `block_pr_without_tests` fires.
- `tests_passed_after_last_edit_pr` — tests pass right after the last edit, then the PR attempt: correct order, nothing fires.
- `gh_pr_create_in_bash` — `gh pr create` inside a `bash` tool call after "ship it" (not a literal PR request), no tests shown: `block_pr_without_tests` fires.
- `git_push_without_tests` — the assistant runs `git push` after an edit, no tests shown: `block_pr_without_tests` fires (the Rule covers pushes too).
- `pr_request_in_words_dutch` — Dutch developer message asking in plain words for a pull request, then `git push && gh pr create`, no tests shown: `block_pr_without_tests` fires; rules must work in Dutch too.
- `combination_no_issue_no_spec_pr_no_tests` — worst case: no issue, no spec, PR attempted, no tests: all three rules fire.
- `lint_passed_not_tests` — only the linter was run (and passed), the actual test suite never ran, then the PR attempt: `block_pr_without_tests` fires — lint is not tests.

End of a Turn (ends with the assistant's final text reply):

- `no_issue_coding_starts` — code is fixed with no issue and no spec ever mentioned: both `propose_issue` and `flag_no_spec` fire; no PR attempted.
- `issue_only_flag_no_spec` — issue id `#482` referenced, but no spec file ever discussed: only `flag_no_spec` fires.
- `spec_read_no_issue` — spec doc read via the `read` tool, code written, but no issue ever referenced: only `propose_issue` fires.
- `pure_exploration_questions` — only reads/greps and questions, no code change, no PR: nothing fires.
- `pr_question_answered_in_words` — the developer asks how to open a PR, the assistant explains it in text only: nothing fires (no PR attempt, no code change).
- `dutch_developer_no_issue` — Dutch conversation, bug fix made with no issue and no spec ever mentioned: both `propose_issue` and `flag_no_spec` fire.
- `issue_url_github` — issue referenced as a full GitHub issue URL rather than a short id: `propose_issue` stays quiet (URL counts as a reference); no spec discussed, so `flag_no_spec` fires.
- `long_conversation_early_issue_lost` — 27+ messages; the issue (JIRA-410) is mentioned only in the very first message, followed by many rounds of unrelated exploration and then more edits. Ground truth over the *full* transcript is `propose_issue=false`; no spec is ever discussed, so `flag_no_spec=true` (corrected on 2026-09-24: it said false, against the Rule). This is where `last_10`-style strategies are expected to disagree with the `full` strategy, since they no longer see the first message.
- `long_conversation_early_spec_lost` — 27+ messages; `docs/caching-spec.md` is read once near the start, then many exploration turns and further edits happen much later. Ground truth is `flag_no_spec=false`; a `last_10` strategy would lose that early read and likely misjudge it.
- `declined_proposal_still_open` — at the end of Turn 1 the developer declined the `propose_issue` Proposal (opencode's `question` answer format), then asked for another change in Turn 2; no issue and no spec: both rules still fire — a declined Proposal doesn't clear the underlying Rule (see CONTEXT.md, "Proposal").
- `after_block_developer_asks_for_tests` — seen live: a PR attempt was blocked, the developer asked to run the tests, the model ran `pytest` (2 passed) and ended the Turn. The latest assistant message does not try to create a PR: nothing fires.

## Adding a fixture

Keep it hand-written and realistic (or generate it with a throwaway script and
commit the resulting JSON, as was done here). Every `tool` message's
`tool_call_id` must match a preceding assistant `tool_calls[].id`. Add the new
rule ids to `expected` if the workflow definition grows new rules.
