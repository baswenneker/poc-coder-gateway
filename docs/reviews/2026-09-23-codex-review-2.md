# Codex review 2 (adversarial, read-only) — diff 563f5f6..c10d29d

Run with `codex exec --sandbox read-only` (the first attempt via the plugin timed out).
Codex found no deadlock, no lock held during streaming and no double close.
Repeated requests within one Turn keep the same Turn. 3 medium issues, 1 low.

| # | Finding | Location | Follow-up |
|---|---------|----------|-----------|
| 1 | A free-form answer that contains the accept label ("Not Yes, create an issue; spec first") counts as `accepted`. After that the Proposal never comes back. | store.py | resolved: for the known opencode format, `parse_tool_answer` now requires an exact match with the accept or decline label; anything else is `answered` |
| 2 | `setup-demo.sh` does write a different URL or key to `opencode.json`, but the skill script still uses the defaults. | scripts/setup-demo.sh | resolved: without env vars, `gateway-status.sh` reads the `gw` provider from the nearest `opencode.json` |
| 3 | If the directory of `var/events.jsonl` cannot be created, the Gateway does not start. | store.py | resolved: `ConversationStore.__init__` catches the `OSError` (same policy as #26), logs and keeps running without an events file |
| 4 | A lock per Conversation stays in memory, never cleaned up. | app.py | accepted for the POC: the store itself does not clean anything up either; a demo has dozens of Conversations |
