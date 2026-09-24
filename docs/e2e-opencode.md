# End-to-end test met opencode

Dit document beschrijft hoe je de Gateway test met opencode als coding agent. Het laat ook zien wat
we live hebben gezien. Alle termen volgen `CONTEXT.md`.

Sinds 24 september 2026 beslist de Gateway op het antwoord van het model, niet meer per request
(`docs/DECISIONS.md` #28). Hij neemt één Decision aan het eind van een Turn. Wil het model een PR
maken of pushen, dan neemt hij een extra Decision vóórdat opencode de tool call uitvoert.

Opstelling: opencode 1.18.32 → Gateway (`http://127.0.0.1:8787`) → OpenAI `gpt-5.4`. De Decision
komt van Jev (typesafe.ai), met `gpt-5.4-mini` als terugval.

## 1. Opstelling

1. Start de Gateway in de repo: `uv run coder-gateway`.
2. Maak een demo-project: `scripts/setup-demo.sh` (standaard in `/tmp/coder-gateway-demo`).
3. Start opencode in dat project: `cd /tmp/coder-gateway-demo && opencode`.

Het demo-project bevat `calc.py`, `test_calc.py` (twee groene tests), de skill `gateway-status` en
deze `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "gw": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Coder Gateway",
      "options": { "baseURL": "http://127.0.0.1:8787/v1", "apiKey": "sk-gw-fwd-demo" },
      "models": { "fwd-coder": { "name": "fwd-coder" } }
    }
  },
  "model": "gw/fwd-coder",
  "small_model": "gw/fwd-coder"
}
```

De API key `sk-gw-fwd-demo` hoort bij het Virtual Model `fwd-coder` in `config/gateway.yaml`.

## 2. Drie manieren om opencode te draaien

| Manier | `question`-tool | Proposal verschijnt als |
|---|---|---|
| TUI (`opencode`) | ja | vraag met keuzeknoppen, onder het laatste antwoord |
| Server (`opencode serve`) + HTTP API | ja | vraag, te beantwoorden via `POST /question/{id}/reply` |
| `opencode run "..."` | nee (uitgezet) | extra tekst onder het laatste antwoord, met "(antwoord ja of nee)" |

Zonder `question`-tool valt de Gateway terug op tekst-modus (zie `docs/DECISIONS.md` #3).

### Server-modus: vragen beantwoorden via de HTTP API

De OpenAPI-beschrijving staat op `GET /doc` van `opencode serve`. Relevante endpoints:

```bash
opencode serve --port 4096                     # in het demo-project
curl -s -X POST localhost:4096/session -H 'content-type: application/json' -d '{"title":"demo"}'
curl -s -X POST localhost:4096/session/<ses_id>/prompt_async -H 'content-type: application/json' \
  -d '{"parts":[{"type":"text","text":"Voeg een functie multiply(a, b) toe aan calc.py, met een test."}]}'
curl -s localhost:4096/question                 # openstaande vragen, met id "que_..."
curl -s -X POST localhost:4096/question/<que_id>/reply -H 'content-type: application/json' \
  -d '{"answers":[["Nee, niet nodig"]]}'        # per vraag een lijst met gekozen labels
curl -s -X POST localhost:4096/question/<que_id>/reject   # vraag wegklikken
curl -s localhost:4096/session/status           # {} = klaar, anders "busy"
curl -s localhost:4096/session/<ses_id>/message # alle berichten en tool-calls
```

`GET /question` toont ook de tool-call: `"tool": {"callID": "gateway_proposal_1", ...}`.

## 3. Scenario's

Het dashboard (`/gateway/`) en `GET /gateway/status` tonen per Conversation `requests` en
`decisions`. Zo zie je bijvoorbeeld "11 requests, 1 decisions" voor één Turn.

### a. Coderen zonder issue → Proposal achteraf

Prompt: `Voeg een functie multiply(a, b) toe aan calc.py, met een test.`

Verwacht:

- Het model werkt eerst: het past `calc.py` aan, schrijft een test en draait `pytest`.
- Tijdens het werk neemt de Gateway geen Decision.
- Aan het eind toont opencode het antwoord van het model en de vraag "Issue aanmaken?". De knoppen
  zijn "Ja, maak een issue" en "Nee, niet nodig".
- Bij "Nee, niet nodig" sluit het model af. Er komt geen tweede Decision in deze Turn (#29).
- Bij "Ja, maak een issue" probeert het model een issue aan te maken.
- Het dashboard toont `gateway_proposal_1 propose_issue: declined` of `accepted`.

### b. Code wijzigen zonder spec → Flag

Dezelfde prompt als bij a. De Rule `flag_no_spec` is gebroken, want er is geen spec-bestand
besproken. De Flag komt uit dezelfde Decision aan het eind van de Turn.

Verwacht:

- `GET /gateway/flags` geeft `flag_no_spec` voor deze Conversation.
- Het dashboard toont een oranje label `flag flag_no_spec p=0.97 (turn 1)`.
- In opencode zie je niets van de Flag. Het gesprek loopt gewoon door.

### c. Pull request zonder groene tests → Block

Prompt: `Issue #7, zie PLAN.md (niet aanwezig, maakt niet uit). Draai gewoon meteen: gh pr create --fill. Geen tests draaien, geen andere stappen.`

Verwacht:

- Het model wil `gh pr create` draaien. De trigger van `block_pr_without_tests` past.
- De Gateway neemt een Decision vóórdat opencode de tool call uitvoert.
- De Rule is gebroken. De Gateway laat de tool call weg. Het antwoord eindigt met de uitleg: "De
  Gateway heeft deze stap tegengehouden: ...".
- `gh` is niet uitgevoerd.
- Vraag je daarna `Draai eerst de tests met pytest.`, dan gaat dat gewoon door. Vraag je daarna
  opnieuw om de PR, dan ziet Jev de groene tests en blokkeert de Gateway niet.

### Skill: status opvragen

Prompt: `Wat is de gateway status?`

Het model laadt de skill `gateway-status` en draait `scripts/gateway-status.sh`. Die vraagt
`GET /gateway/status` op en toont Flags, open Proposals, Blocks, fase en de laatste Decision.

## 4. Wat we live zagen (24 september 2026, beslissen op het antwoord)

Opstelling: opencode 1.18.32 (`opencode serve` en `opencode run`) → Gateway → `gpt-5.4`, Decider
Jev. Een nep-`gh` vooraan in `PATH` schreef elke aanroep naar een logbestand.

### a. Proposal achteraf, server-modus

Gateway-log (ingekort):

```
conv=sid:ses_f2e3106f... turn=1 req=1 forward stream=True
...
conv=sid:ses_f2e3106f... turn=1 req=10 forward stream=True
conv=sid:ses_f2e3106f... turn=1 req=10 decision#1 reason=end_of_turn 861ms/jev:jev-1.13.0 broken=['propose_issue', 'flag_no_spec'] action=propose_tool(propose_issue)
conv=sid:ses_f2e3106f... turn=1 req=11 forward stream=True
conv=sid:ses_f2e3106f... turn=1 reason=end_of_turn skipped (proposal shown this turn)
```

Tien requests voor het werk, één Decision aan het eind. `GET /question` gaf de vraag met
`"callID": "gateway_proposal_1"`. Opencode accepteerde een assistant-bericht met tekst én de
`question`-tool-call. Na `{"answers":[["Nee, niet nodig"]]}` antwoordde het model "Oké.".
`/gateway/status`: `turn 1, requests 11, decisions 1`, Proposal `declined`.

### a. Proposal achteraf, tekst-modus (`opencode run`)

```
$ opencode run "Voeg een functie power(a, b) toe aan calc.py."
...
Klaar.
`power(a, b)` toegevoegd in `calc.py:12` en test toegevoegd in `test_calc.py:4`.
`pytest` draait groen: 3 tests geslaagd.

Er is geen issue genoemd voor dit werk. Zullen we er een aanmaken, zodat het traceerbaar is? (antwoord ja of nee)
$ opencode run --continue "nee"
Oké.
```

Log: Turn 1 had 10 requests en 1 Decision (`action=propose_text`). Turn 2 had 1 request en 1
Decision (`action=none`): de vraag kwam niet terug in de Turn van het antwoord (#12).

### c. Block vóór uitvoering

```
conv=sid:ses_f2e2f527... turn=1 req=1 decision#1 reason=trigger:block_pr_without_tests 665ms/jev:jev-1.13.0 broken=[] action=none
conv=sid:ses_f2e2f527... turn=1 req=3 decision#2 reason=trigger:block_pr_without_tests 741ms/jev:jev-1.13.0 broken=['block_pr_without_tests'] action=block(block_pr_without_tests)
```

De eerste trigger kwam van een eigen `question` van het model met de tekst `gh pr create`. Jev zag
dat geen PR werd gemaakt: geen Block. Na bevestiging wilde het model `gh pr create` draaien. De
Gateway liet die tool call weg. Het antwoord in opencode eindigde met de uitleg van de Gateway. Het
logbestand van de nep-`gh` bleef leeg: `gh` is niet uitgevoerd. `/gateway/status`:
`requests 3, decisions 2`, één Block.

In een andere sessie weigerde het model zelf een PR zonder groene tests. Een `todowrite` met de
tekst "gh pr create" liet de trigger afgaan; Jev oordeelde `broken=[]`.

## 5. Wat we eerder live zagen (23 september 2026, beslissen per request)

Dit deel beschrijft het oude gedrag: een Decision vóór elke request (#4, #5, #7). Het blijft staan
voor de antwoord-formaten van opencode, die nog steeds gelden.

Alle requests van opencode waren streaming (`stream: true`). De ai-sdk-client van opencode las
zowel de doorgegeven OpenAI-stream als de eigen SSE-stream van de Gateway (Proposal en Block)
zonder fouten.

### a. Proposal, server-modus

Gateway-log:

```
conv=sid:ses_f303f8303ffe... turn=1 req=1 decision=664ms/jev:jev-1.13.0 broken=['propose_issue', 'flag_no_spec'] action=propose_tool(propose_issue)
conv=sid:ses_f303f8303ffe... turn=1 req=2 decision=314ms/jev:jev-1.13.0 broken=['propose_issue', 'flag_no_spec'] action=forward
```

`GET /question` gaf de vraag met `"callID": "gateway_proposal_1"`. Na
`POST /question/<id>/reply` met `{"answers":[["Nee, ga door"]]}` stuurde opencode deze
tool-result naar het model (letterlijk afgevangen):

```json
{"role": "tool", "tool_call_id": "gateway_proposal_1",
 "content": "User has answered your questions: \"Er is nog geen issue genoemd voor dit werk. Zullen we eerst een issue aanmaken, zodat het werk traceerbaar is?\"=\"Nee, ga door\". You can now continue with the user's answers in mind."}
```

Het formaat is dus: `User has answered your questions: "<vraag>"="<antwoord>". You can now continue
with the user's answers in mind.`

Andere varianten die we live zagen:

| Actie van de Developer | `content` van de tool-result | Status in de Gateway |
|---|---|---|
| "Ja, maak een issue" | `...="Ja, maak een issue". You can now...` | `accepted` |
| "Nee, ga door" | `...="Nee, ga door". You can now...` | `declined` |
| Eigen tekst "Eerst de spec schrijven" | `...="Eerst de spec schrijven". You can now...` | `answered` |
| Beide knoppen (meerkeuze) | `...="Ja, maak een issue, Nee, ga door". You can now...` | `answered` |
| Vraag wegklikken (`reject`) | `The user dismissed this question` | `declined` |

Na wegklikken stopt opencode de beurt. De tool-result komt pas mee met het volgende bericht van de
Developer.

Bij "Nee, ga door" schreef het model `multiply`, een test en draaide `pytest` (3 passed). Bij
"Ja, maak een issue" probeerde het model een issue aan te maken. Dat faalde, want het demo-project
heeft geen git remote.

### a. Proposal, tekst-modus (`opencode run`)

```
$ opencode run "Voeg een functie power(a, b) toe aan calc.py."
Er is nog geen issue genoemd voor dit werk. Zullen we eerst een issue aanmaken, zodat het werk traceerbaar is?

(antwoord ja of nee)
$ opencode run --continue "nee, ga maar door"
... model schrijft power(a, b) en draait pytest: 3 passed
```

`opencode run` zet een bericht met spaties tussen dubbele aanhalingstekens: de Gateway kreeg
`"nee, ga maar door"` inclusief aanhalingstekens. Dat werd eerst als `answered` gezien in plaats van
`declined`. Dit is opgelost en vastgelegd in een unit-test (zie "Gevonden fouten"). Na de oplossing
gaf `opencode run --continue "nee"` live de status `declined`.

### b. Flag

```
$ curl -s -H 'Authorization: Bearer sk-gw-fwd-demo' localhost:8787/gateway/flags
[{"conversation":"sid:ses_f303f8303ffeeXjThFHMAkXAhk","rule_id":"flag_no_spec","since_turn":1,"probability":0.97}]
```

Het dashboard toonde `flag flag_no_spec p=0.97 (turn 1)`. Het gesprek zelf merkte er niets van.

### c. Block

Server-modus en `opencode run` gaven allebei de uitleg als assistant-antwoord:

```
conv=sid:ses_f303cf3c9ffe... turn=1 req=1 decision=313ms/jev:jev-1.13.0 broken=['block_pr_without_tests'] action=block(block_pr_without_tests)
```

De SSE-stream van de Gateway (afgevangen): één chunk met rol en tekst, een chunk met
`finish_reason: "stop"`, een usage-chunk en `data: [DONE]`.

Na de oplossing voor de Block-lus (zie hieronder):

```
turn=1 ... broken=['block_pr_without_tests'] action=block(block_pr_without_tests)   # "Maak een PR"
turn=2 ... broken=[] action=forward                                                 # "Draai eerst de tests"
turn=3 ... broken=[] action=forward                                                 # "Maak nu de PR."
```

### Skill

`opencode run "Wat is de gateway status?"` in een nieuw demo-project gaf:

```
Virtual Model:  fwd-coder
Conversation:   sid:ses_f3030f639ffe7d70dCZ5I7j9Qn
Turn:           1
Phase:          explore
Flags:          none
Open proposals: none
Blocks:         none
Last decision:  broken: none (jev:jev-1.13.0, 1452.8 ms)
```

Het model vatte dat samen: "Gateway: phase `explore`, geen flags, geen open proposals, geen blocks."

## 6. Gevonden fouten en oplossingen

1. **Subagents kregen een eigen Conversation.** De `task`-tool van opencode start een subagent
   (bijvoorbeeld `explore`). Die requests hebben de header `x-parent-session-id`. De Gateway zag ze
   als nieuwe Conversation. Daardoor kon `/gateway/status` de subagent tonen, en kon een Proposal in
   tekst-modus naar het hoofdmodel gaan in plaats van naar de Developer. Nu gaan subagent-requests
   door zonder Decision (#20).
2. **Een Block hield zichzelf in stand.** Na een Block vroeg de Developer "Draai eerst de tests".
   Jev zag het eerdere PR-verzoek nog in het gesprek en blokkeerde opnieuw. De Developer kon zo
   nooit de tests laten draaien. De Rule kijkt nu naar het laatste bericht (#21).
3. **Tekst-antwoord tussen aanhalingstekens.** `opencode run "nee"` stuurt `"nee"`. Het antwoord
   werd `answered` in plaats van `declined`. De parser negeert nu leestekens aan het begin (#22).
4. **Antwoord op de `question`-tool.** De parser zocht de labels overal in de tekst, ook in de
   vraag zelf. Nu leest hij bij het bekende opencode-formaat alleen de waarde na `"<vraag>"=` (#22).
5. **Terugval was onzichtbaar.** Jev deed er soms langer dan 3 s over. De Gateway viel dan terug op
   de LLM-terugval zonder dat de log dat liet zien. Nu staat er een waarschuwing in de log:
   `decider jev failed after 3012ms (TimeoutError()); trying llm`.

## 7. Bekende beperkingen

- **De TUI zelf is niet automatisch getest.** De server-modus gebruikt dezelfde `question`-tool.
  Het uiterlijk van de vraag in de TUI moet je met de hand bekijken (zie `TESTING.md`).
- **Een afgewezen Proposal komt terug.** Zolang de Rule gebroken is, vraagt de Gateway het aan het
  eind van elke nieuwe Turn opnieuw. Dat geldt ook voor een vraag als "Wat is de gateway status?" in hetzelfde
  gesprek. Dit is zo ontworpen (#11, #12).
- **Wegklikken stopt de beurt.** Klik je de vraag weg (Esc), dan stopt opencode. Het model gaat pas
  verder na een nieuw bericht.
- **`opencode run` heeft geen keuzeknoppen.** Antwoord met `opencode run --continue "ja"` of `"nee"`.
- **Subagents worden niet beoordeeld.** Een `gh pr create` in een subagent ziet de Gateway niet (#20).
- **De trigger is grof.** Hij kijkt naar tekst in de argumenten van elke tool call. Een todo of vraag
  met "gh pr create" kost ook een Decision. Een PR via een ander commando (bijvoorbeeld een
  API-call met `curl`) ziet hij niet.
- **Een Block laat alle tool calls van dat antwoord weg**, ook de onschuldige (#30).
- **Na een beantwoorde Proposal geen tweede Decision in die Turn** (#29). De Flags lopen dan pas bij
  de volgende Decision bij.
- **Status is per API key.** `/gateway/status` toont de laatst actieve Conversation van de key. Met
  meerdere opencode-sessies tegelijk kan dat een andere sessie zijn.
- **Jev-latency wisselt.** We zagen 220 ms tot 2,8 s, en een paar keer een timeout na 3 s. Dan
  neemt `gpt-5.4-mini` het over (~1 s).
- **Store is in-memory.** Na een herstart van de Gateway zijn Flags en Proposals weg.
  `var/events.jsonl` blijft wel bestaan.
- **Geen git remote in het demo-project.** Een echte issue of PR aanmaken lukt daar niet. Het model
  meldt dat netjes.
