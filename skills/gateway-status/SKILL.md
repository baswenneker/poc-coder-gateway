---
name: gateway-status
description: Shows what the Coder Gateway currently thinks of this conversation - active flags, open proposals, blocks, workflow phase and the last decision. Use when the developer asks for the gateway status, "gateway status", "wat ziet de gateway", flags, open proposals/voorstellen, blocks, or why the gateway asked a question or blocked a request.
---

# Gateway status

The Coder Gateway sits between this coding agent and the model. It checks every request against
the team workflow and can:

- set a **flag**: a rule is broken; no effect on the conversation;
- make a **proposal**: a question to the developer (e.g. "create an issue first?");
- **block** a request: refuse it and explain why (e.g. a pull request without green tests).

The Gateway has a read API. This skill queries it and shows the result to the developer.

## Steps

1. Run the helper script (path relative to this skill's base directory):

   ```bash
   bash scripts/gateway-status.sh
   ```

   It prints a plain-text summary. Add `--json` for the raw JSON of `GET /gateway/status`.
   The script reads `CODER_GATEWAY_URL` (default `http://127.0.0.1:8787`) and
   `CODER_GATEWAY_KEY` (default `sk-gw-fwd-demo`, the demo Virtual Model key).

2. Show the summary to the developer. Keep it short, in the developer's language:
   - active flags (rule id, since which turn, probability);
   - open proposals (rule id, question mode);
   - blocks in this conversation;
   - the phase and the last decision (which rules are broken, decider, latency).

3. If a flag or an open proposal is shown, say in one sentence what the developer can do about it
   (for `flag_no_spec`: mention or write a spec/plan file; for `propose_issue`: reference or create an
   issue; for `block_pr_without_tests`: run the tests and show they pass before asking for the PR).

## Rules

- Only report what the read API returns. Do not guess the status.
- Do not change code or files as part of this skill.
- If the script fails (Gateway not running, wrong key), show the error and stop.
- The status is for the most recently active conversation of this API key. With several opencode
  sessions open at once it can show another session; say so if the conversation id looks unfamiliar.
