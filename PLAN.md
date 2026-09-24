# poc-coder-gateway — work plan

Prototype. Rough is fine, hard to configure is fine.

## What it is
OpenAI-compatible gateway. One Virtual Model per API key (real model + system prompt +
Workflow Definition). An async observer reads the transcript and checks it against the
workflow. Three interventions: flag, injection, block.

## Decisions
- Injection = a tool call with a fixed id (`gateway_proposal`) in the assistant reply.
  The Developer answers yes/no/something else. Non-blocking on the model side.
- Blocking is possible, but only if the Workflow Definition says so per Rule.
- The Gateway has a read API (status, flags, open proposals) so a skill
  in the coding agent can display it.
- Conversation identification: fingerprint on the first user messages (opencode sends no session id).
- Upstream: OpenAI directly (OPENAI_API_KEY in .env). The request is already in OpenAI format, so pass it through without translation.
- Streaming is in the POC. For block and propose, the Gateway produces its own SSE stream.
- The "intervene or not" decision per turn is made by Jev (typesafe.ai, System One model):
  input = transcript + structured state, output = predefined enum + probability.
  70-500ms, so it can run synchronously in the request path. Early access, hosted.
  https://typesafe.ai/blog/introducing-system-one-models-and-jev

## Simplified case (first build target)
1. Injection: no issue mentioned in the Conversation and the Developer starts coding
   → proposal "shall we create an issue first?" via a tool call.
2. Flag: the Developer changes code without any spec file having been discussed
   → flag `no_spec` in the dashboard/API, no effect on the Conversation.
3. Block: the request contains `gh pr create` (or equivalent) while tests have not been
   reported green → the Gateway refuses and sends back an explanation as the assistant reply.

## Todo
- [x] Benchmark script: compare strategies for Jev state on fixtures with ground truth — `uv run benchmark`, see `benchmark/README.md`
- [x] End-to-end test with opencode as the client (provider config, scenario for propose/flag/block) — see `docs/e2e-opencode.md`
- [x] Work out the configuration format of the Workflow Definition (phases, rules, intervention per Rule) — `workflows/fwd-default.yaml`
- [x] Tech stack: Python + uv, FastAPI, streaming (SSE). Upstream OpenAI. Jev via typesafe-sdk.
- [x] Test Jev (key is available): does it reliably classify the transcript into {none, propose_issue, flag_no_spec, block_pr}? — yes/no question per Rule (DECISIONS #6); benchmark ~94% with strategy `full`
- [x] Fallback when Jev is unavailable: small LLM with structured output (same enum) — `deciders/llm.py`, gpt-5.4-mini
- [x] Fingerprint function for conversation identification — `fingerprint.py`; opencode does send `x-session-id` (DECISIONS #1)
- [x] Skill for the coding agent that queries the read API — `skills/gateway-status/`

## Benchmark: what state does Jev get?
Repeatable script (`uv run benchmark`) that measures the Jev probabilities per strategy on a fixed set
of test transcripts with a known expected outcome (ground truth per Rule).

Strategies:
1. whole conversation
2. only the last 10 messages
3. last 10 messages in full, everything before that with tool results replaced by `<truncated>`
4. (later) summary + last 10

Output per strategy: accuracy per Rule at the configured threshold, average latency,
tokens per call. The script picks nothing, it reports. We choose based on the table.

Test transcripts: hand-written fixtures in `benchmark/fixtures/*.json`, each with
`expected: {propose_issue: true/false, flag_no_spec: ..., block_pr_without_tests: ...}`.

## End-to-end test with opencode
opencode as the coding agent, with a custom provider that points to our Gateway (OpenAI-compatible).
Scenario: the Developer starts coding without an issue → propose visible as a tool call;
the Developer creates a PR without green tests → block visible as a reply; flag visible via the
read API. Document in `docs/e2e-opencode.md` how to run this.
