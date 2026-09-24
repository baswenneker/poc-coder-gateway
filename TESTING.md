# Manual testing

These steps get the Gateway running with opencode. You will see the three Interventions: Proposal, Flag and
Block. The Gateway decides at the end of a Turn, and when the model wants to create a PR or push. Allow about 20 minutes. Background and live results: `docs/e2e-opencode.md`.

## 1. Preparation

You need:

- `uv` (Python package manager) and Python 3.12 or newer.
- opencode 1.18 or newer (`opencode --version`).
- `curl` and `python3` (for the skill).
- An OpenAI API key and a typesafe.ai API key (for Jev).

Steps:

1. Go to the repo: `cd poc-coder-gateway`.
2. Install the packages: `uv sync`.
3. Put the keys in `.env.local` (this file is not in git):

   ```
   OPENAI_API_KEY=sk-...
   TYPESAFE_API_KEY=...
   ```

`.env.example` lists the available variables.

## 2. Start the Gateway

```bash
uv run coder-gateway
```

You see one line like:

```
Coder Gateway on http://127.0.0.1:8787 (decider jev, fallback llm, strategy full); dashboard /gateway/
```

To pick another port, use `CODER_GATEWAY_PORT=8790 uv run coder-gateway`. Keep this window open.
The Gateway logs one line per request (`forward`). For each Decision it logs one more
line with the reason: `reason=end_of_turn` or `reason=trigger:<rule_id>`.

## 3. Open the dashboard

Open http://127.0.0.1:8787/gateway/ in the browser. The page refreshes every 3 seconds. For now it shows
"No Conversations yet."

For each Conversation the dashboard shows how many requests and how many Decisions (Jev calls) there were,
for example `11 requests, 1 decisions`. The Gateway does not decide per request. It decides at the end
of a Turn, and additionally when the model wants to create a PR or push (`docs/DECISIONS.md` #28).

If `dashboard_token` is set in `config/gateway.yaml`, open
`http://127.0.0.1:8787/gateway/?token=<value>` instead. Without the token the page returns 401.

## 4. Create the demo project

In a second terminal, in the repo:

```bash
scripts/setup-demo.sh                  # creates /tmp/coder-gateway-demo
# or: scripts/setup-demo.sh ~/tmp/my-demo
```

The script creates a git repo with `calc.py`, `test_calc.py`, an `opencode.json` that points to the
Gateway, and the `gateway-status` skill in `.opencode/skills/`. If the directory already exists, the
script stops. Remove the directory first in that case: `rm -rf /tmp/coder-gateway-demo`.

To use a different Gateway URL or key, set `CODER_GATEWAY_URL` and `CODER_GATEWAY_KEY`. Without those
env vars, the skill's `gateway-status.sh` reads the `gw` provider from the nearest
`opencode.json`, so a non-default URL or key works too.

Start opencode:

```bash
cd /tmp/coder-gateway-demo
opencode
```

The TUI should show `fwd-coder` as the model. If opencode asks for permission to run a command or
change a file, allow it.

## 5. Scenario a: coding without an issue → Proposal afterwards

Type:

```
Add a function multiply(a, b) to calc.py, with a test.
```

Expected:

1. The model works first. It edits `calc.py`, writes a test and runs `pytest`. No question
   appears yet.
2. At the end, opencode shows the model's reply with the question "Create issue?" below it.
   The choices are "Yes, create an issue" and "No, not needed".
3. The Gateway log has a `forward` line per request. At the end there is one line with
   `decision#1 reason=end_of_turn ... action=propose_tool(propose_issue)`.
4. Choose "No, not needed". The model wraps up, for example with "OK.". There is no second Decision.
5. The dashboard shows `gateway_proposal_1 propose_issue: declined (tool, turn 1)` and,
   for example, `11 requests, 1 decisions`.

Also try "Yes, create an issue" in a new session (`/new`). The model then tries to create an
issue. In the demo project this fails because there is no git remote. That is expected.

Note: as long as no issue has been mentioned, the question returns at the end of every new Turn. This
is by design (`docs/DECISIONS.md` #11 and #12). Mention an issue ("This is issue #12") to make it
go away.

## 6. Scenario b: changing code without a spec → Flag

This happens in the same session as scenario a. No spec file has been discussed.

Expected:

1. opencode shows nothing of the Flag.
2. The dashboard shows an orange label `flag flag_no_spec p=0.9x (turn 1)`.
3. The read API returns the Flag:

   ```bash
   curl -s -H 'Authorization: Bearer sk-gw-fwd-demo' http://127.0.0.1:8787/gateway/flags
   ```

   Output, roughly:

   ```json
   [{"conversation":"sid:ses_...","rule_id":"flag_no_spec","since_turn":1,"probability":0.97}]
   ```

## 7. Skill: query the status

Type in opencode:

```
What is the gateway status?
```

Expected: the model loads the `gateway-status` skill, runs `scripts/gateway-status.sh` and shows
the phase, Flags, open Proposals, Blocks and the last Decision.

If you do this in the session from scenario a, the issue question returns after the status (see the
note under scenario a). Choose "No, not needed". In a new session (`/new`) the question does not appear,
because no code has been changed there.

## 8. Scenario c: pull request without green tests → Block

Start a new session with `/new`. Type:

```
Issue #7. Run gh pr create --fill right away. Don't run tests, no other steps.
```

The model follows the team Workflow from its system prompt, so it sometimes asks for
confirmation itself first. If so, choose the option to continue.

Expected:

1. The model wants to run `gh pr create`. The Gateway sees that tool call before opencode runs it.
2. The reply in opencode ends with: "The Gateway stopped this step: the model wanted to open a
   pull request or push code, but ...".
3. opencode shows no executed tool call with `gh pr create`. The command did not run.
4. The Gateway log shows `reason=trigger:block_pr_without_tests ... action=block(block_pr_without_tests)`.
5. The dashboard shows a red label `block block_pr_without_tests (turn 1)`.

The trigger is a simple pattern: `gh pr create`, `glab mr create` or `git push`. If that text appears in
another tool call, for example a todo list, the Gateway also makes a Decision. Jev then usually
judges "not broken" (`action=none`), because no PR is being created.

Then type:

```
Run the tests with pytest first.
```

Expected: the model runs `pytest` and reports 2 passed. At the end the log shows `reason=end_of_turn` with
`broken=[]`. No Block.

Then type `Create the PR now.` Expected: the trigger fires, but Jev sees green tests
(`broken=[]`). The model runs `gh pr create` and reports that there is no git remote.

## 9. Read API with curl

All endpoints require the header `Authorization: Bearer sk-gw-fwd-demo`.

```bash
KEY='Authorization: Bearer sk-gw-fwd-demo'
curl -s -H "$KEY" http://127.0.0.1:8787/gateway/status | python3 -m json.tool
curl -s -H "$KEY" http://127.0.0.1:8787/gateway/flags
curl -s -H "$KEY" "http://127.0.0.1:8787/gateway/proposals?status=open"
curl -s -H "$KEY" http://127.0.0.1:8787/gateway/conversations
curl -s -H "$KEY" http://127.0.0.1:8787/gateway/conversations/sid:ses_...   # with all events
```

You can also get the skill output without opencode: `bash skills/gateway-status/scripts/gateway-status.sh`
(add `--json` for the raw JSON).

## 10. Text mode (optional)

`opencode run` does not offer a `question` tool. The Gateway then asks the question as plain text:

```bash
cd /tmp/coder-gateway-demo
opencode run "Add a function power(a, b) to calc.py."
# → the model writes power(a, b), runs pytest and ends with:
#   "No issue has been mentioned for this work. Shall we create one so it is traceable?
#    (answer yes or no)"
opencode run --continue "no"
# → the model replies briefly ("OK."). The question does not return in this Turn.
```

## 11. Automated tests and benchmark

In the repo:

```bash
uv run pytest -q              # unit and app tests, no external APIs
uv run pytest -q -m live      # tests that actually call Jev/OpenAI (costs money)
uv run mypy
uv run ruff check src tests
uv run benchmark              # measures Jev per strategy on benchmark/fixtures/
uv run benchmark --strategies full --repeats 1   # faster
```

The benchmark writes its results to `var/benchmark/`. See `benchmark/README.md` for details.

## 12. Troubleshooting

- **opencode says "Cannot connect to API".** Is the Gateway running? Does the port in the demo
  project's `opencode.json` match?
- **401 from the Gateway.** The key in `opencode.json` or in your curl command does not match
  `config/gateway.yaml`.
- **Where can I see what happened?**
  - The Gateway log: one line per request (`forward`) and one line per Decision with `reason=...`,
    `broken=[...]` and `action=...`.
  - `var/events.jsonl`: every event as one JSON line, also after a restart.
  - The dashboard: the last 12 events per Conversation.
- **The log says `decider jev failed after 3000ms (TimeoutError()); trying llm`.** Jev was too
  slow. The LLM fallback (`gpt-5.4-mini`) took over. That is not an error.
- **Nothing ever happens.** Does the log show `broken=error(...)`? Then both
  Deciders failed and the Gateway passed the reply through unchanged (fail-open). Check the keys in
  `.env.local`.
- **No question during the work.** Correct. The Gateway only decides once the model has finished the
  Turn. The question then appears below the last reply.
- **Trying another Decider.** Change `decider` in `config/gateway.yaml` and restart the
  Gateway:

  ```yaml
  decider:
    primary: llm      # jev | llm | none
    fallback: none    # llm | none
  ```

  With `primary: none` the Gateway never intervenes. It then forwards everything to OpenAI.
- **Flags and Proposals are gone.** The Gateway keeps them in memory only. After a restart
  everything starts from scratch.
- **Subagent requests.** The log sometimes shows `passthrough (subagent of ses_...)`. Those are
  requests from an opencode subagent. They get no Decision (`docs/DECISIONS.md` #20).
