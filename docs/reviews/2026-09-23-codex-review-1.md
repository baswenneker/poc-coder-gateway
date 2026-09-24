# Codex review 1 (adversarial, read-only) — diff 3f6de43..563f5f6

Run via the Codex plugin (`codex:rescue`). No critical or high findings; 7 medium.
The "Follow-up" column is updated once a fix has been committed.

| # | Finding | Location | Follow-up |
|---|---------|----------|-----------|
| 1 | Tool output is always cut at 2,000 characters, even with strategy `full`. A test summary at the end of long pytest output disappears, which can cause a wrongful Block. | transcript.py | fixed: only truncate above 20,000 characters, keeping both start and end (DECISIONS #18) |
| 2 | An older request can, after the `await` on the Decider, overwrite the state of a newer Turn (flags, proposal). | app.py | fixed: lock per Conversation around Decision and state update, forwarding outside the lock (DECISIONS #24) |
| 3 | If the client shortens the history (compaction), the Turn counter stops increasing and Proposals stay suppressed. | store.py | fixed: a new Turn also starts on a different last user message; text answer without an index (DECISIONS #25) |
| 4 | If the client drops the connection before the first chunk, the upstream stream stays open. | upstream.py | fixed: the response class closes the upstream stream in `finally` over its whole lifetime |
| 5 | An error while writing `var/events.jsonl` aborts the request, even after a fail-open Decision. | store.py / app.py | fixed: the `OSError` is logged once and the request continues (DECISIONS #26) |
| 6 | The `/gateway/` dashboard has no authentication and shows all Virtual Models. | app.py | fixed: optional `dashboard_token`; warning at startup when bound to a non-loopback address without a token (DECISIONS #27) |
| 7 | With `fallback: none`, the benchmark counts a Decider error as a correct "nothing wrong" prediction. | benchmark.py / deciders | fixed: `SoloDecider` sets `error` on failure (DECISIONS #19) |
