# Codex-review 2 (adversarial, alleen lezen) — diff 563f5f6..c10d29d

Uitgevoerd met `codex exec --sandbox read-only` (de eerste poging via de plugin liep in een timeout).
Codex vond geen deadlock, geen lock die tijdens het streamen vastgehouden wordt en geen dubbele close.
Herhaalde requests binnen één Turn houden dezelfde Turn. 3 middelzware punten, 1 laag.

| # | Bevinding | Plek | Opvolging |
|---|-----------|------|-----------|
| 1 | Een vrij antwoord dat het accept-label bevat ("Niet Ja, maak een issue; eerst de spec") telt als `accepted`. Daarna komt het voorstel nooit meer terug. | store.py | in behandeling |
| 2 | `setup-demo.sh` zet een andere URL of key wel in `opencode.json`, maar het skill-script gebruikt nog de standaardwaarden. | scripts/setup-demo.sh | in behandeling |
| 3 | Kan de map van `var/events.jsonl` niet worden aangemaakt, dan start de Gateway niet. | store.py | in behandeling |
| 4 | Per Conversation blijft een lock in het geheugen staan, zonder opruimen. | app.py | geaccepteerd voor de POC: de store zelf ruimt ook niets op; een demo heeft tientallen Conversations |
