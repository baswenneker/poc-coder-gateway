# Codex-review 1 (adversarial, alleen lezen) — diff 3f6de43..563f5f6

Uitgevoerd via de Codex-plugin (`codex:rescue`). Geen kritieke of hoge bevindingen; 7 middelzware.
Kolom "Opvolging" wordt bijgewerkt zodra een fix is gecommit.

| # | Bevinding | Plek | Opvolging |
|---|-----------|------|-----------|
| 1 | Tool-output wordt altijd op 2.000 tekens afgekapt, ook bij strategie `full`. Een testsamenvatting aan het eind van lange pytest-output verdwijnt, wat een onterechte Block kan geven. | transcript.py | gefixt: alleen afkappen boven 20.000 tekens, met begin én eind bewaard (DECISIONS #18) |
| 2 | Een oudere request kan na de `await` op de Decider de state van een nieuwere Turn overschrijven (flags, proposal). | app.py | gefixt: lock per Conversation rond Decision en state-update, doorsturen buiten de lock (DECISIONS #24) |
| 3 | Als de client de geschiedenis inkort (compaction), groeit de Turn-teller niet meer en blijven voorstellen onderdrukt. | store.py | gefixt: nieuwe Turn ook bij een ander laatste user-bericht; tekst-antwoord zonder index (DECISIONS #25) |
| 4 | Verbreekt de client de verbinding vóór de eerste chunk, dan blijft de upstream-stream open. | upstream.py | gefixt: response-klasse sluit de upstream-stream in `finally` over de hele levensduur |
| 5 | Een fout bij het schrijven van `var/events.jsonl` breekt de request af, ook na een fail-open Decision. | store.py / app.py | gefixt: `OSError` wordt één keer gelogd, request gaat door (DECISIONS #26) |
| 6 | Het dashboard `/gateway/` heeft geen authenticatie en toont alle Virtual Models. | app.py | gefixt: lock per Conversation rond Decision en state-update, doorsturen buiten de lock (DECISIONS #24) |
| 7 | Met `fallback: none` telt de benchmark een Decider-fout als correcte "niets aan de hand"-voorspelling. | benchmark.py / deciders | gefixt: `SoloDecider` zet `error` bij falen (DECISIONS #19) |
