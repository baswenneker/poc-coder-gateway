# Codex-review 1 (adversarial, alleen lezen) — diff 3f6de43..563f5f6

Uitgevoerd via de Codex-plugin (`codex:rescue`). Geen kritieke of hoge bevindingen; 7 middelzware.
Kolom "Opvolging" wordt bijgewerkt zodra een fix is gecommit.

| # | Bevinding | Plek | Opvolging |
|---|-----------|------|-----------|
| 1 | Tool-output wordt altijd op 2.000 tekens afgekapt, ook bij strategie `full`. Een testsamenvatting aan het eind van lange pytest-output verdwijnt, wat een onterechte Block kan geven. | transcript.py | in behandeling |
| 2 | Een oudere request kan na de `await` op de Decider de state van een nieuwere Turn overschrijven (flags, proposal). | app.py | in behandeling |
| 3 | Als de client de geschiedenis inkort (compaction), groeit de Turn-teller niet meer en blijven voorstellen onderdrukt. | store.py | in behandeling |
| 4 | Verbreekt de client de verbinding vóór de eerste chunk, dan blijft de upstream-stream open. | upstream.py | in behandeling |
| 5 | Een fout bij het schrijven van `var/events.jsonl` breekt de request af, ook na een fail-open Decision. | store.py / app.py | in behandeling |
| 6 | Het dashboard `/gateway/` heeft geen authenticatie en toont alle Virtual Models. | app.py | in behandeling |
| 7 | Met `fallback: none` telt de benchmark een Decider-fout als correcte "niets aan de hand"-voorspelling. | benchmark.py / deciders | in behandeling |
