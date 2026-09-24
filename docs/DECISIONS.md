# Decision log

Choices made during the autonomous build, with the reasoning. Newest at the bottom.
Terms follow `CONTEXT.md`.

## 1. Opencode does send a session id; the fingerprint stays as a fallback
The plan says opencode sends no session id. A captured request from opencode 1.18.32
contains the headers `x-session-id` and `x-session-affinity`. The Gateway uses `x-session-id` when
it is present. Otherwise it computes the Fingerprint from the first user messages. That way it also
works with clients that do not send the header.

## 2. Requests without tools get no Decision
Each turn, opencode also sends a separate request to generate a title (different system prompt,
no `tools`). It is not part of the Developer's work. The Gateway forwards requests without
`tools` unchanged (only the model is replaced) and makes no Decision.

## 3. A Proposal uses opencode's `question` tool, with tool call id `gateway_proposal_<n>`
The plan: the injection is a tool call with the fixed id `gateway_proposal`. A tool call only works
if the client knows the tool. In the TUI and server mode, opencode offers a `question` tool, which
shows the question to the Developer with choice buttons. The Gateway therefore names the tool call
`question` and gives it an id that starts with `gateway_proposal`. A sequence number makes the id
unique, because OpenAI may reject duplicate tool call ids within one conversation. The answer comes
back as a tool result with that id.

Fallback: if the client offers no `question` tool (for example `opencode run`, which disables that
tool), the Gateway sends the Proposal as plain assistant text. The next user message then counts as
the answer.

## 4. A Proposal replaces the upstream reply for that request
Superseded by #28.

On a Proposal, the Gateway does not send the request upstream but answers itself with the Proposal
tool call. The Developer's answer does go upstream in the next request, so the model sees question
and answer and carries on from there. This is simpler than adding a tool call to a running upstream
stream, and the model is not blocked: on "no" it simply continues.

## 5. Decision synchronous in the request path, fail-open
Superseded by #28.

Jev answers in 70-500 ms (measured: ~230 ms). The Gateway therefore waits for the Decision before
going upstream, with a timeout. If Jev fails or the timeout expires, it tries the LLM fallback. If
that fails too, the request proceeds without an Intervention (fail-open). A broken Decider must not
bring the Developer to a halt.

## 6. One yes/no question per Rule instead of one enum
The plan names a single enum `{none, propose_issue, flag_no_spec, block_pr}`. Several Rules can be
broken at the same time (for example no issue and no spec). An enum picks only one. The Decider
therefore asks Jev one `noul` question per Rule (yes/no with a probability) in a single call. Each
Rule has its own threshold.

## 7. A Block looks at the request, not at the model's reply
Superseded by #28.

The Decision is made before the request goes upstream. A Block therefore catches the Developer's
request ("create a PR") or an earlier `gh pr create` attempt in the Transcript. A `gh pr create`
that the model comes up with in its own reply is only seen by the Gateway on the next request. By
then the client has already run that tool call. A limitation of the POC.

## 8. The benchmark builds the Decider via `build_decider(fallback="none")`, not directly
`src/coder_gateway/benchmark.py` must measure Jev/LLM "bare", without a fallback that hides errors.
Instead of importing and constructing `JevDecider`/`LLMDecider` directly (with internal constructor
arguments that belong to the Deciders, not to the benchmark), the benchmark calls the existing
`build_decider(primary=..., fallback="none", ...)`. That is already the one central place where a
name is mapped to a Decider; `fallback="none"` gives the same "no fallback" guarantee as building
directly, without the benchmark having to know every Decider's constructor signature. New
strategies/Deciders (e.g. `summary_last_10`) can thus still be added in one place.

## 9. Ground truth in fixtures always covers the full conversation; each strategy only shrinks the
Transcript
`benchmark/fixtures/*.json` has one `expected` per fixture, not one per strategy. That is
deliberate: `expected` is the correct Decision as the Gateway would make it if it saw the whole
conversation. A strategy such as `last_10` may well score worse on it than `full` — that difference
in accuracy per strategy, not an adjusted ground truth, is what the table should show (see
`long_conversation_early_issue_lost` and `long_conversation_early_spec_lost`).

## 10. Errors of the Decider itself count as "error", separate from accuracy
If `decider.decide()` raises an exception or returns a `Decision` with `error` set, the benchmark
counts it under `errors`, separately from the accuracy counter (which only covers successful calls).
Otherwise a broken call would artificially lower accuracy and blur the difference between "the
Decider gave the wrong answer" and "the Decider gave no answer at all".

## 11. An unanswered Proposal counts as declined
If the Gateway shows a Proposal via the `question` tool and the Developer starts a new Turn without a
tool result for that question (for example after aborting), the Gateway sets the Proposal to
`declined` (event `proposal_expired`). Otherwise it would stay open forever and never come back.
A Proposal answered with anything other than yes/no (`answered`) also counts as declined: it may
come back as long as the Rule stays broken.

## 12. A declined Proposal only comes back in the Turn after the answer
In text mode the answer ("no") only arrives in the next Turn. Without this rule the Gateway would ask
again straight away in that same Turn. Hence: do not propose again in the Turn in which the Proposal
was answered. Also, at most one Proposal per Turn per Conversation, across all Rules.

## 13. Only Rules with intervention `flag` become a Flag
A broken `propose` or `block` Rule leads to a Proposal or Block, not to a Flag. The Decision itself
(all probabilities) is available in the read API (`last_decision`) and on the dashboard. A failed
Decision leaves existing Flags in place.

## 14. Title requests also get `max_completion_tokens`, but no system prompt
Requests without `tools` pass through unchanged (#2), except for the model and `max_tokens` →
`max_completion_tokens`: gpt-5.x rejects `max_tokens`, so without that conversion the title fails.
The Virtual Model's system prompt is only added to agent requests.

## 15. The timeout around the whole Decision is 2 × `timeout_s` + 0.5 s
`timeout_s` applies per Decider call (Jev, then the LLM fallback). The Gateway wraps an outer
timeout around them that leaves room for both calls. If it expires, the Gateway fails open (#5).

## 16. The Proposal number survives a restart
`gateway_proposal_<n>`: n is one higher than the highest number in the store or in the request's
messages. After a Gateway restart (the store is in-memory) this prevents duplicate tool call ids.
Tested live: OpenAI accepts an earlier `gateway_proposal_1` tool call with a tool result in the
history.

## 17. Default strategy `full`, timeout per Decider 3 s
The benchmark (18 test conversations, Jev) gave: full conversation 94% correct, last 10 messages
83%, last 10 with truncated tool output 89%. The errors in the shorter strategies are in long
conversations where the issue or the spec was mentioned early. With `full`, Jev used ~1,400 input
tokens on average, so the cost stays small. That is why `config/gateway.yaml` is set to `full`.
Average latency was ~0.9 s with a p95 of ~2.3 s; a timeout of 1.5 s would therefore often jump to
the fallback. The timeout per Decider call is therefore set to 3 s. Results: `var/benchmark/`.

## 18. `compact_transcript` now only truncates with a very generous head+tail cap, no longer at a
fixed 2000 characters
Every `role='tool'` message was always cut to 2000 characters, regardless of strategy. With a long
test log that has the summary at the end ("42 passed"), exactly that part disappeared, even under
the `full`/`last_10` strategies that meant to show the tool output in full — a false Block after
green tests. Truncating per strategy is already the job of `apply_strategy` (e.g.
`last_10_truncated` already sets older tool output to `<truncated>`); `compact_transcript` must not
do it again. Now only a generic safety valve applies against extremely large tool output
(> 20,000 characters): the first 1,500 + last 3,000 characters with a `...[N chars omitted]...`
marker in between, so a summary at the end always survives.

## 19. `build_decider(fallback="none")` returns a `SoloDecider`, not a `FallbackDecider` with
`NoneDecider` as fallback
As built, `fallback="none"` still wrapped the primary in `FallbackDecider(primary,
NoneDecider(), timeout_s)`. A primary exception then simply let the fallback (`NoneDecider`)
succeed: a Decision with all Rules not broken, `error=None` and `latency_ms=0.0` — indistinguishable
from a correct "nothing broken" answer. In the benchmark (which uses `fallback="none"` precisely to
measure the Decider bare, see #8), a broken call thus counted as a correct prediction instead of
under `errors` (#10).
New class `SoloDecider` (`deciders/fallback.py`): runs only the primary, with a timeout; on
exception/timeout it fails open (no broken Rules), with `error` set and the `latency_ms` of the
failed attempt. `build_decider` uses `SoloDecider` when `fallback="none"` and `primary !=
"none"`. The Gateway itself (`config/gateway.yaml`: `fallback: llm`) keeps running through
`FallbackDecider` unchanged, and so stays fail-open in the same way as before (#5).

## 20. Requests from an opencode subagent pass through without a Decision
Opencode's `task` tool starts a subagent (for example `explore`) with its own session. Those
requests carry the header `x-parent-session-id` (seen live in opencode 1.18.32). There the main
model talks to the subagent, not the Developer. A Proposal in text mode would then be answered by
the main model. `/gateway/status` also sometimes showed the subagent as the latest Conversation.
Hence: on `x-parent-session-id` the Gateway forwards the request, with the Virtual Model's system
prompt, but without a Decision and without a Conversation of its own. The subagent's result comes
back as a tool result in the Developer's Conversation; the Decision is made there. Limitation: the
Gateway does not see a `gh pr create` inside a subagent.

## 21. The Block Rule looks at the last message
Amended by #28: the Rule now looks at the tool call in the last assistant message.

Seen live: after a Block the Developer asked "Run the tests first". Jev still saw the earlier PR
request in the conversation and blocked again (in the benchmark the old text gave 0.65, live it was
above the 0.7 threshold). The Developer could therefore never get the tests to run. The
`broken_when` of `block_pr_without_tests` in `workflows/fwd-default.yaml` now explicitly names the
Developer's last message (or the model's last `gh pr create`). `ok_when` says that an earlier PR
request does not count if the last message asks for something else. New fixture:
`benchmark/fixtures/after_block_developer_asks_for_tests.json` (now 0.03). The benchmark on `full`
stayed at 100% for this Rule. The choice lives in the Rule text, not in the code: a team may want it
differently per Rule.

## 22. Answer to a Proposal: the actual opencode format
The tool result of opencode's `question` tool is (captured live):
`User has answered your questions: "<question>"="<answer>". You can now continue with the user's
answers in mind.` Dismissing it gives `The user dismissed this question`. Several selected labels
share one value, separated by ", ". For this format, `parse_tool_answer` reads only the value after
`"<question>"=`. That way a label inside the question text cannot affect the outcome. Exactly the
accept label is `accepted`, exactly the decline label is `declined`, everything else is `answered`
(counts as declined, #11). Other formats use the old, looser rule.
In text mode, `opencode run` wraps a message containing spaces in quotes (`"no, go ahead"`).
`parse_text_answer` therefore ignores leading punctuation.

## 23. The `gateway-status` skill lives in `skills/` and is copied per project to `.opencode/skills/`
Opencode finds skills in the project's `.opencode/skills/<name>/SKILL.md` (and globally in, among
others, `~/.claude/skills` and `~/.agents/skills`). The skill belongs to the Gateway, not to one
project. That is why the original lives in `skills/gateway-status/` of this repo, and
`scripts/setup-demo.sh` copies it to `.opencode/skills/` of the demo project. The skill calls a
small script (`curl` plus `python3`), configured with `CODER_GATEWAY_URL` and `CODER_GATEWAY_KEY`.
The script shows the API key's most recently active Conversation via `/gateway/status`. The skill
does not know its own session id, so there is no per-session filter.

## 24. One lock per Conversation around Decision and state update
Found in code review. The handler read the Conversation, waited for the Decider and then wrote
the result into whatever the state was at that moment. A slow request from Turn 1 could thus, after
a fast request from Turn 2, restore a Flag and book its Proposal on Turn 2. Now `app.py` holds an
`asyncio.Lock` per Conversation from `begin_request` up to and including the chosen Intervention.
Forwarding to upstream (and streaming) happens outside the lock. Why a lock and not "discard stale
results": in a single process it is the simplest watertight solution, and the order of requests
stays the order of processing. Cost: a second request from the same Conversation waits for at most
one Decision (bounded by `decision_timeout_s`). Opencode sends one request per session at a time
anyway.

## 25. A new Turn is recognised by the last Developer message, not just by the count
Found in code review. `conv.turn` was the highest number of user messages ever seen. If the
client shortened the history (compaction), the Turn stopped increasing: declined Proposals stayed
suppressed and the text-mode answer pointed to an index in the old history. Now a new Turn starts
when the number of user messages is greater than on the previous request, or when the last user
message has different text (hash). The Turn number is `max(turn + 1, count)`: normally equal to the
number of user messages, after compaction simply one higher. The text-mode answer to a Proposal is
the last user message of the first request in a later Turn; no index any more.
Limitation: if after compaction the client sends a different last user message without the
Developer saying anything (e.g. a synthetic "continue"), that counts as a new Turn.

## 26. The event log is best effort
Found in code review. An error while writing to `var/events.jsonl` (disk full, no permission)
aborted the request, even after a fail-open Decision. The file is an aid for looking back, not part
of the Decision. Now `ConversationStore` catches an `OSError`, logs one warning (and only again
after a write has succeeded in between) and carries on. The events stay in memory, so the read API
and the dashboard keep working.
The same applies to creating the directory of `var/events.jsonl` at
startup. If `mkdir` fails (no permission), `ConversationStore.__init__` logs one warning and the
Gateway starts anyway, without an events file.

## 27. Dashboard: open on localhost, optionally a token
Found in code review. `/gateway/` needs no API key and shows all Virtual Models. For manual
testing that is convenient (browser, no header), and the Gateway binds to `127.0.0.1` by default.
New: an optional `dashboard_token` in `config/gateway.yaml`. If it is set, the dashboard requires
`?token=<value>` (otherwise 401). `CODER_GATEWAY_HOST` selects a different address; if that is not a
loopback address and no token is set, the Gateway logs a warning at startup. The read API
(`/gateway/status` etc.) stays protected per Virtual Model by the API key.

## 28. Decision only at the end of a Turn and on a trigger
At first the Gateway called Jev before every request. One Turn has 5 to 20 requests, one per
tool call round. That is a lot of calls for little new information. Now every request goes upstream
without a Decision. The Gateway looks at the model's reply.

- **End of the Turn.** The reply has `finish_reason` `stop` and no tool calls. The model is then
  handing control back to the Developer. The Gateway makes exactly one Decision here, on the
  Transcript plus the last assistant message. It then updates the Flags.
- **Proposal afterwards.** If a `propose` Rule is broken, the Gateway attaches the Proposal to this
  last reply. With the `question` tool: an extra tool call `gateway_proposal_<n>` and finish
  `tool_calls`. Without it: the question as extra text with "(answer yes or no)". So the question
  comes after the work: "No issue has been mentioned for this work. Shall we create one?"
- **Block on the reply side.** A Block Rule has a `trigger`: a regex on the arguments of a tool
  call, optionally limited to tool names. For `block_pr_without_tests`:
  `gh pr create|glab mr create|git push`, without tool names, so that it works with any client. If a
  tool call in the reply matches, the Gateway makes an extra Decision. If the Rule is broken, it
  drops the tool call and ends the reply with the explanation (finish `stop`). The client therefore
  never runs the tool call. That removes the limitation of #7.
- **Streaming.** Text streams through live. The Gateway holds back tool call deltas until the reply
  is complete; the client only acts on them after the finish anyway. The finish chunk, the usage
  chunk and `[DONE]` also wait for the Decision. Without an Intervention they pass through byte for
  byte.
- **Fail-open stays.** If the Decision fails or the timeout expires, the reply passes through
  unchanged. Other finish reasons (`length`, `content_filter`) and upstream errors get no Decision.
- **The lock (#24) stays.** The per-Conversation lock wraps the Decision and the state update, not
  the streaming of the text.

Result: normally one Jev call per Turn, plus one per triggering tool call. The dashboard and
`/gateway/status` show `requests` and `decisions` per Conversation. The log writes one line per
Decision with the reason: `end_of_turn` or `trigger:<rule_id>`.

## 29. After a Proposal in the same Turn, no second Decision at the end
With the `question` tool, the Developer answers the Proposal within the same Turn. The model then
continues and ends with `stop` again. A second Decision could not propose anything at that point:
only one Proposal is allowed per Turn (#12). The Gateway therefore skips that Decision. That keeps it
at one Jev call per Turn. Cost: the Flags only catch up at the next Decision. A trigger Decision
(Block) always happens.

## 30. Triggers only on Block Rules; a Block drops all tool calls of that reply
A `trigger` on a `flag` or `propose` Rule is a load-time error. Such a Rule only acts at the end of
the Turn, so a trigger means nothing there. A Block Rule without a trigger is also an error: it
would never be evaluated.
If the model requests several tool calls in one reply and one of them matches the trigger, a Block
drops them all. A half-executed reply is harder to follow than one clear stop.
The trigger is deliberately cheap and coarse. Live we also saw it fire on a `todowrite` and on a
`question` from the model containing the text "gh pr create". Jev then judged "not broken", because
no PR was being created. That costs one extra call, not a wrongful Block.

## 31. A reply from an older Turn gets no Decision
If the reply to a request arrives after the Developer has already started a new Turn, the Gateway
makes no Decision on that reply. The state by then belongs to the new Turn. Opencode sends one
request per session at a time, so this only happens after an abort.

## 32. Rule texts and benchmark adapted to deciding afterwards
The benchmark fixtures now end where the Gateway decides: the last assistant message of a Turn, or
an assistant message with a triggering tool call. Two new fixtures:
`git_push_without_tests` and `pr_question_answered_in_words`. Rule texts in
`workflows/fwd-default.yaml`:

- `propose_issue`: broken if code was changed without an issue. A declined question about an issue
  does not count as a reference. Without that sentence `declined_proposal_still_open` dropped to
  0.61.
- `block_pr_without_tests`: broken if the last assistant message makes a tool call that creates a
  PR or pushes. A command that is only explained in text does not count. Without that sentence
  `pr_question_answered_in_words` gave 0.74.

`long_conversation_early_issue_lost` had `flag_no_spec: false`, but no spec appears in it.
Every Jev run gave 0.95 or higher. The ground truth is now `true`.
Result on `full` (21 fixtures, Jev, two runs): 100% correct, ~0.5 s on average, p95 ~1 s.

## 33. More than one choice (`n > 1`): no Decision
If a request asks for more than one reply (`n > 1`), or the reply contains a choice with an index
other than 0, the reply passes through unchanged, without a Decision. The Gateway only reads and
modifies choice 0. With multiple choices, collecting and amending the reply got mixed up
(found in code review). Opencode never sends `n > 1`, so in practice this costs nothing.

## 34. The trigger looks at the decoded arguments
The trigger searched the JSON text of the arguments. `{"command":"git push"}`, a tab or
`git -C /path push` therefore escaped the Block check. Now the Gateway decodes the JSON and joins
all string values together; if it is not JSON, the raw text counts. Every run of whitespace becomes
a single space. The default trigger in `workflows/fwd-default.yaml` also catches global git options:
`\bgit\b(\s+-\S+(\s+[^\s-]\S*)?)*\s+push\b`, plus `gh pr create` and `glab mr create` with
arbitrary whitespace.

## 35. Trigger regexes stay simple; the Gateway looks at the first 8,000 characters
A regex with heavy backtracking can stall the event loop. The workflow is the team's own config, and
therefore trusted: the Gateway sticks with Python `re` and uses no separate regex engine. However, a
trigger looks at no more than the first 8,000 characters of the decoded arguments. Write triggers as
simple patterns without nested repetition that can match the same text. That is why in the default
trigger an option value is never something that starts with `-`; then there is only one way to
match.
