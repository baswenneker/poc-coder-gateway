# Codex-review 3 (adversarial, alleen lezen) — diff 816e2fa..a2a7109

Onderwerp: Decision alleen aan het eind van een Turn en bij een trigger (`reply.py`, DECISIONS #28-#32).
Uitgevoerd met `codex exec --sandbox read-only`.

| # | Ernst | Bevinding | Plek | Opvolging |
|---|-------|-----------|------|-----------|
| 1 | P1 | Bij meerdere `choices` (`n > 1`) raakt de verzameling en de aanpassing van het antwoord door elkaar. | reply.py | in behandeling |
| 2 | P1 | De trigger zoekt in de JSON-tekst van de argumenten. `git push`, een tab of `git -C /pad push` ontsnapt aan de Block-check. | domain.py | in behandeling |
| 3 | P2 | Bevat de laatste chunk tekst én `finish_reason`, dan komt het voorstel vóór het laatste stuk tekst van het model. | reply.py | in behandeling |
| 4 | P2 | Breekt de verbinding met upstream halverwege af, dan gaan al ontvangen tool calls en de finish verloren. | reply.py | in behandeling |
| 5 | P2 | Een `error`-event van upstream na de finish voorkomt de Decision niet; het voorstel wordt dan opgeslagen maar nooit getoond. | reply.py | in behandeling |
| 6 | P2 | Een regex met veel backtracking in de workflow kan de event loop stilzetten. | domain.py | in behandeling |
| 7 | P3 | Een ongewijzigde stream is niet altijd byte-gelijk: een afsluitende newline verdwijnt. | reply.py | in behandeling |
