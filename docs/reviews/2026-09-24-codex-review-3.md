# Codex review 3 (adversarial, read-only) — diff 816e2fa..a2a7109

Subject: Decision only at the end of a Turn and on a trigger (`reply.py`, DECISIONS #28-#32).
Run with `codex exec --sandbox read-only`.

| # | Severity | Finding | Location | Follow-up |
|---|----------|---------|----------|-----------|
| 1 | P1 | With multiple `choices` (`n > 1`), collecting and amending the reply get mixed up. | reply.py | Resolved: with `n > 1` or a choice with index ≠ 0, the reply passes through unchanged, without a Decision (DECISIONS #33). |
| 2 | P1 | The trigger searches the JSON text of the arguments. `git push`, a tab or `git -C /path push` escapes the Block check. | domain.py | Resolved: the trigger matches on the decoded string values, with whitespace normalised; the default trigger catches `git -C /path push` (DECISIONS #34). |
| 3 | P2 | If the last chunk contains both text and `finish_reason`, the Proposal appears before the model's last piece of text. | reply.py | Resolved: the finish chunk's text goes first, then the Proposal, then the finish. |
| 4 | P2 | If the upstream connection breaks halfway, tool calls and the finish already received are lost. | reply.py | Resolved: on an upstream error, the held-back bytes pass through unchanged, without a Decision; then the error. |
| 5 | P2 | An upstream `error` event after the finish does not prevent the Decision; the Proposal is then stored but never shown. | reply.py | Resolved: no Decision after an `error` event; the stream passes through unchanged. |
| 6 | P2 | A regex with heavy backtracking in the workflow can stall the event loop. | domain.py | Resolved: the trigger looks at no more than 8,000 characters; triggers must be simple patterns (DECISIONS #35). |
| 7 | P3 | An unchanged stream is not always byte-identical: a trailing newline disappears. | reply.py | Resolved: any remaining buffer is passed through, even if it is only whitespace. |
