# End-to-end test with opencode

This document describes how to test the Gateway with opencode as the coding agent. It also shows what
we observed live. All terms follow `CONTEXT.md`.

Since 24 September 2026 the Gateway decides on the model's reply, no longer per request
(`docs/DECISIONS.md` #28). It makes one Decision at the end of a Turn. If the model wants to create a PR
or push, it makes an extra Decision before opencode runs the tool call.

Setup: opencode 1.18.32 → Gateway (`http://127.0.0.1:8787`) → OpenAI `gpt-5.4`. The Decision
comes from Jev (typesafe.ai), with `gpt-5.4-mini` as the fallback.

## 1. Setup

1. Start the Gateway in the repo: `uv run coder-gateway`.
2. Create a demo project: `scripts/setup-demo.sh` (defaults to `/tmp/coder-gateway-demo`).
3. Start opencode in that project: `cd /tmp/coder-gateway-demo && opencode`.

The demo project contains `calc.py`, `test_calc.py` (two green tests), the `gateway-status` skill and
this `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "gw": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Coder Gateway",
      "options": { "baseURL": "http://127.0.0.1:8787/v1", "apiKey": "sk-gw-fwd-demo" },
      "models": { "fwd-coder": { "name": "fwd-coder" } }
    }
  },
  "model": "gw/fwd-coder",
  "small_model": "gw/fwd-coder"
}
```

The API key `sk-gw-fwd-demo` belongs to the Virtual Model `fwd-coder` in `config/gateway.yaml`.

## 2. Three ways to run opencode

| Mode | `question` tool | Proposal appears as |
|---|---|---|
| TUI (`opencode`) | yes | question with choice buttons, below the last reply |
| Server (`opencode serve`) + HTTP API | yes | question, answered via `POST /question/{id}/reply` |
| `opencode run "..."` | no (disabled) | extra text below the last reply, with "(answer yes or no)" |

Without the `question` tool the Gateway falls back to text mode (see `docs/DECISIONS.md` #3).

### Server mode: answering questions via the HTTP API

The OpenAPI description is at `GET /doc` of `opencode serve`. Relevant endpoints:

```bash
opencode serve --port 4096                     # in the demo project
curl -s -X POST localhost:4096/session -H 'content-type: application/json' -d '{"title":"demo"}'
curl -s -X POST localhost:4096/session/<ses_id>/prompt_async -H 'content-type: application/json' \
  -d '{"parts":[{"type":"text","text":"Add a function multiply(a, b) to calc.py, with a test."}]}'
curl -s localhost:4096/question                 # open questions, with id "que_..."
curl -s -X POST localhost:4096/question/<que_id>/reply -H 'content-type: application/json' \
  -d '{"answers":[["No, not needed"]]}'         # per question, a list of chosen labels
curl -s -X POST localhost:4096/question/<que_id>/reject   # dismiss the question
curl -s localhost:4096/session/status           # {} = done, otherwise "busy"
curl -s localhost:4096/session/<ses_id>/message # all messages and tool calls
```

`GET /question` also shows the tool call: `"tool": {"callID": "gateway_proposal_1", ...}`.

## 3. Scenarios

The dashboard (`/gateway/`) and `GET /gateway/status` show `requests` and `decisions` per
Conversation. For example, you see "11 requests, 1 decisions" for one Turn.

### a. Coding without an issue → Proposal afterwards

Prompt: `Add a function multiply(a, b) to calc.py, with a test.`

Expected:

- The model works first: it edits `calc.py`, writes a test and runs `pytest`.
- The Gateway makes no Decision while the work is in progress.
- At the end, opencode shows the model's reply and the question "Create issue?". The buttons
  are "Yes, create an issue" and "No, not needed".
- On "No, not needed" the model wraps up. There is no second Decision in this Turn (#29).
- On "Yes, create an issue" the model tries to create an issue.
- The dashboard shows `gateway_proposal_1 propose_issue: declined` or `accepted`.

### b. Changing code without a spec → Flag

Same prompt as in a. The Rule `flag_no_spec` is broken, because no spec file has been
discussed. The Flag comes from the same Decision at the end of the Turn.

Expected:

- `GET /gateway/flags` returns `flag_no_spec` for this Conversation.
- The dashboard shows an orange label `flag flag_no_spec p=0.97 (turn 1)`.
- opencode shows nothing of the Flag. The Conversation simply continues.

### c. Pull request without green tests → Block

Prompt: `Issue #7, see PLAN.md (not present, doesn't matter). Just run this right away: gh pr create --fill. Don't run tests, no other steps.`

Expected:

- The model wants to run `gh pr create`. The trigger of `block_pr_without_tests` matches.
- The Gateway makes a Decision before opencode runs the tool call.
- The Rule is broken. The Gateway leaves out the tool call. The reply ends with the explanation: "The
  Gateway stopped this step: ...".
- `gh` did not run.
- If you then ask `Run the tests with pytest first.`, that goes through normally. If you then ask
  for the PR again, Jev sees the green tests and the Gateway does not block.

### Skill: query the status

Prompt: `What is the gateway status?`

The model loads the `gateway-status` skill and runs `scripts/gateway-status.sh`. That script queries
`GET /gateway/status` and shows Flags, open Proposals, Blocks, phase and the last Decision.

## 4. What we observed live (24 September 2026, deciding on the reply)

Setup: opencode 1.18.32 (`opencode serve` and `opencode run`) → Gateway → `gpt-5.4`, Decider
Jev. A fake `gh` at the front of `PATH` wrote every invocation to a log file.

### a. Proposal afterwards, server mode

Gateway log (abridged):

```
conv=sid:ses_f2e3106f... turn=1 req=1 forward stream=True
...
conv=sid:ses_f2e3106f... turn=1 req=10 forward stream=True
conv=sid:ses_f2e3106f... turn=1 req=10 decision#1 reason=end_of_turn 861ms/jev:jev-1.13.0 broken=['propose_issue', 'flag_no_spec'] action=propose_tool(propose_issue)
conv=sid:ses_f2e3106f... turn=1 req=11 forward stream=True
conv=sid:ses_f2e3106f... turn=1 reason=end_of_turn skipped (proposal shown this turn)
```

Ten requests for the work, one Decision at the end. `GET /question` returned the question with
`"callID": "gateway_proposal_1"`. opencode accepted an assistant message with both text and the
`question` tool call. After `{"answers":[["No, not needed"]]}` the model replied "OK.".
`/gateway/status`: `turn 1, requests 11, decisions 1`, Proposal `declined`.

### a. Proposal afterwards, text mode (`opencode run`)

```
$ opencode run "Add a function power(a, b) to calc.py."
...
Done.
Added `power(a, b)` in `calc.py:12` and a test in `test_calc.py:4`.
`pytest` is green: 3 tests passed.

No issue has been mentioned for this work. Shall we create one so it is traceable? (answer yes or no)
$ opencode run --continue "no"
OK.
```

Log: Turn 1 had 10 requests and 1 Decision (`action=propose_text`). Turn 2 had 1 request and 1
Decision (`action=none`): the question did not return in the Turn of the answer (#12).

### c. Block before execution

```
conv=sid:ses_f2e2f527... turn=1 req=1 decision#1 reason=trigger:block_pr_without_tests 665ms/jev:jev-1.13.0 broken=[] action=none
conv=sid:ses_f2e2f527... turn=1 req=3 decision#2 reason=trigger:block_pr_without_tests 741ms/jev:jev-1.13.0 broken=['block_pr_without_tests'] action=block(block_pr_without_tests)
```

The first trigger came from the model's own `question` containing the text `gh pr create`. Jev saw
that no PR was being created: no Block. After confirmation the model wanted to run `gh pr create`. The
Gateway left out that tool call. The reply in opencode ended with the Gateway's explanation. The
fake `gh` log file stayed empty: `gh` did not run. `/gateway/status`:
`requests 3, decisions 2`, one Block.

In another session the model itself refused to create a PR without green tests. A `todowrite` containing the
text "gh pr create" fired the trigger; Jev judged `broken=[]`.

## 5. What we observed live earlier (23 September 2026, deciding per request)

This section describes the old behaviour: a Decision before every request (#4, #5, #7). It is kept
for opencode's answer formats, which still apply. At the time the Workflow's messages were in Dutch;
they are shown here in English.

All requests from opencode were streaming (`stream: true`). opencode's ai-sdk client read
both the forwarded OpenAI stream and the Gateway's own SSE stream (Proposal and Block)
without errors.

### a. Proposal, server mode

Gateway log:

```
conv=sid:ses_f303f8303ffe... turn=1 req=1 decision=664ms/jev:jev-1.13.0 broken=['propose_issue', 'flag_no_spec'] action=propose_tool(propose_issue)
conv=sid:ses_f303f8303ffe... turn=1 req=2 decision=314ms/jev:jev-1.13.0 broken=['propose_issue', 'flag_no_spec'] action=forward
```

`GET /question` returned the question with `"callID": "gateway_proposal_1"`. After
`POST /question/<id>/reply` with `{"answers":[["No, continue"]]}` opencode sent this
tool result to the model (captured):

```json
{"role": "tool", "tool_call_id": "gateway_proposal_1",
 "content": "User has answered your questions: \"No issue has been mentioned for this work yet. Shall we create an issue first, so the work is traceable?\"=\"No, continue\". You can now continue with the user's answers in mind."}
```

So the format is: `User has answered your questions: "<question>"="<answer>". You can now continue
with the user's answers in mind.`

Other variants we observed live:

| Developer action | `content` of the tool result | Status in the Gateway |
|---|---|---|
| "Yes, create an issue" | `...="Yes, create an issue". You can now...` | `accepted` |
| "No, continue" | `...="No, continue". You can now...` | `declined` |
| Own text "Write the spec first" | `...="Write the spec first". You can now...` | `answered` |
| Both buttons (multiple choice) | `...="Yes, create an issue, No, continue". You can now...` | `answered` |
| Dismiss the question (`reject`) | `The user dismissed this question` | `declined` |

After a dismissal opencode ends the turn. The tool result is only sent along with the Developer's
next message.

On "No, continue" the model wrote `multiply`, a test and ran `pytest` (3 passed). On
"Yes, create an issue" the model tried to create an issue. That failed, because the demo project
has no git remote.

### a. Proposal, text mode (`opencode run`)

```
$ opencode run "Add a function power(a, b) to calc.py."
No issue has been mentioned for this work yet. Shall we create an issue first, so the work is traceable?

(answer yes or no)
$ opencode run --continue "no, go ahead"
... model writes power(a, b) and runs pytest: 3 passed
```

`opencode run` wraps a message containing spaces in double quotes: the Gateway received
`"no, go ahead"` including the quotes. At first this was seen as `answered` instead of
`declined`. This has been fixed and is covered by a unit test (see "Bugs found"). After the fix,
`opencode run --continue "no"` gave the status `declined` live.

### b. Flag

```
$ curl -s -H 'Authorization: Bearer sk-gw-fwd-demo' localhost:8787/gateway/flags
[{"conversation":"sid:ses_f303f8303ffeeXjThFHMAkXAhk","rule_id":"flag_no_spec","since_turn":1,"probability":0.97}]
```

The dashboard showed `flag flag_no_spec p=0.97 (turn 1)`. The Conversation itself noticed nothing.

### c. Block

Server mode and `opencode run` both returned the explanation as the assistant reply:

```
conv=sid:ses_f303cf3c9ffe... turn=1 req=1 decision=313ms/jev:jev-1.13.0 broken=['block_pr_without_tests'] action=block(block_pr_without_tests)
```

The Gateway's SSE stream (captured): one chunk with role and text, a chunk with
`finish_reason: "stop"`, a usage chunk and `data: [DONE]`.

After the fix for the Block loop (see below):

```
turn=1 ... broken=['block_pr_without_tests'] action=block(block_pr_without_tests)   # "Create a PR"
turn=2 ... broken=[] action=forward                                                 # "Run the tests first"
turn=3 ... broken=[] action=forward                                                 # "Create the PR now."
```

### Skill

`opencode run "What is the gateway status?"` in a fresh demo project returned:

```
Virtual Model:  fwd-coder
Conversation:   sid:ses_f3030f639ffe7d70dCZ5I7j9Qn
Turn:           1
Phase:          explore
Flags:          none
Open proposals: none
Blocks:         none
Last decision:  broken: none (jev:jev-1.13.0, 1452.8 ms)
```

The model summarised it as: "Gateway: phase `explore`, no flags, no open proposals, no blocks."

## 6. Bugs found and fixes

1. **Subagents got their own Conversation.** opencode's `task` tool starts a subagent
   (for example `explore`). Those requests carry the header `x-parent-session-id`. The Gateway treated them
   as a new Conversation. As a result `/gateway/status` could show the subagent, and a text-mode Proposal
   could go to the main model instead of the Developer. Subagent requests now pass
   through without a Decision (#20).
2. **A Block kept itself alive.** After a Block the Developer asked "Run the tests first".
   Jev still saw the earlier PR request in the Conversation and blocked again. The Developer could
   never get the tests to run. The Rule now looks at the last message (#21).
3. **Text answer in quotes.** `opencode run "no"` sends `"no"`. The answer
   became `answered` instead of `declined`. The parser now ignores leading punctuation (#22).
4. **Answer to the `question` tool.** The parser looked for the labels anywhere in the text, including in the
   question itself. For the known opencode format it now reads only the value after `"<question>"=` (#22).
5. **The fallback was invisible.** Jev sometimes took longer than 3 s. The Gateway then fell back to
   the LLM fallback without the log showing it. The log now contains a warning:
   `decider jev failed after 3012ms (TimeoutError()); trying llm`.

## 7. Known limitations

- **The TUI itself is not tested automatically.** Server mode uses the same `question` tool.
  You have to check how the question looks in the TUI by hand (see `TESTING.md`).
- **A declined Proposal comes back.** As long as the Rule is broken, the Gateway asks again at the
  end of every new Turn. That also applies to a question like "What is the gateway status?" in the same
  Conversation. This is by design (#11, #12).
- **Dismissing ends the turn.** If you dismiss the question (Esc), opencode stops. The model only
  continues after a new message.
- **`opencode run` has no choice buttons.** Answer with `opencode run --continue "yes"` or `"no"`.
- **Subagents are not judged.** The Gateway does not see a `gh pr create` in a subagent (#20).
- **The trigger is coarse.** It looks at text in the arguments of every tool call. A todo or question
  containing "gh pr create" also costs a Decision. It does not see a PR created by another command (for example an
  API call with `curl`).
- **A Block leaves out all tool calls of that reply**, including the harmless ones (#30).
- **No second Decision in a Turn after an answered Proposal** (#29). The Flags only catch up at
  the next Decision.
- **Status is per API key.** `/gateway/status` shows the key's most recently active Conversation. With
  several opencode sessions at once, that may be a different session.
- **Jev latency varies.** We saw 220 ms to 2.8 s, and a few timeouts after 3 s. Then
  `gpt-5.4-mini` takes over (~1 s).
- **The store is in-memory.** After a Gateway restart, Flags and Proposals are gone.
  `var/events.jsonl` does persist.
- **No git remote in the demo project.** Creating a real issue or PR fails there. The model
  reports that cleanly.
