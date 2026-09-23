# Handmatig testen

Deze stappen laten de Gateway werken met opencode. Je ziet de drie ingrepen: Proposal, Flag en
Block. Reken op ongeveer 20 minuten. Achtergrond en live-resultaten: `docs/e2e-opencode.md`.

## 1. Voorbereiding

Nodig:

- `uv` (Python-pakketbeheer) en Python 3.12 of nieuwer.
- opencode 1.18 of nieuwer (`opencode --version`).
- `curl` en `python3` (voor de skill).
- Een OpenAI API key en een typesafe.ai API key (voor Jev).

Stappen:

1. Ga naar de repo: `cd poc-coder-gateway`.
2. Installeer de pakketten: `uv sync`.
3. Zet de keys in `.env.local` (dit bestand staat niet in git):

   ```
   OPENAI_API_KEY=sk-...
   TYPESAFE_API_KEY=...
   ```

`.env.example` toont welke variabelen er zijn.

## 2. Gateway starten

```bash
uv run coder-gateway
```

Je ziet één regel zoals:

```
Coder Gateway on http://127.0.0.1:8787 (decider jev, fallback llm, strategy full); dashboard /gateway/
```

Een andere poort kies je met `CODER_GATEWAY_PORT=8790 uv run coder-gateway`. Laat dit venster open.
De Gateway schrijft per request één regel in de log.

## 3. Dashboard openen

Open http://127.0.0.1:8787/gateway/ in de browser. De pagina ververst elke 3 seconden. Nu staat er
"No Conversations yet."

## 4. Demo-project maken

In een tweede terminal, in de repo:

```bash
scripts/setup-demo.sh                  # maakt /tmp/coder-gateway-demo
# of: scripts/setup-demo.sh ~/tmp/mijn-demo
```

Het script maakt een git repo met `calc.py`, `test_calc.py`, een `opencode.json` die naar de
Gateway wijst en de skill `gateway-status` in `.opencode/skills/`. Bestaat de map al, dan stopt het
script. Verwijder de map dan eerst: `rm -rf /tmp/coder-gateway-demo`.

Een andere Gateway-URL of key geef je mee met `CODER_GATEWAY_URL` en `CODER_GATEWAY_KEY`.

Start opencode:

```bash
cd /tmp/coder-gateway-demo
opencode
```

De TUI moet `fwd-coder` als model tonen. Vraagt opencode om toestemming voor een commando of
bestandswijziging, sta het dan toe.

## 5. Scenario a: coderen zonder issue → Proposal

Typ:

```
Voeg een functie multiply(a, b) toe aan calc.py, met een test.
```

Verwacht:

1. opencode toont de vraag "Eerst een issue?" met twee keuzes: "Ja, maak een issue" en
   "Nee, ga door".
2. In de Gateway-log staat `action=propose_tool(propose_issue)`.
3. Kies "Nee, ga door". Het model schrijft `multiply`, een test en draait `pytest`.
4. Op het dashboard staat `gateway_proposal_1 propose_issue: declined (tool, turn 1)`.

Probeer ook eens "Ja, maak een issue" in een nieuwe sessie (`/new`). Het model probeert dan eerst
een issue aan te maken. In het demo-project lukt dat niet, want er is geen git remote. Dat is
verwacht.

Let op: zolang er geen issue genoemd is, komt de vraag in elke nieuwe Turn terug. Zo is het
ontworpen (`docs/DECISIONS.md` #11 en #12). Noem een issue ("Dit is issue #12") om hem weg te
krijgen.

## 6. Scenario b: code wijzigen zonder spec → Flag

Dit gebeurt in dezelfde sessie als scenario a. Er is geen spec-bestand besproken.

Verwacht:

1. In opencode zie je niets van de Flag.
2. Op het dashboard staat een oranje label `flag flag_no_spec p=0.9x (turn 1)`.
3. De read-API geeft de Flag:

   ```bash
   curl -s -H 'Authorization: Bearer sk-gw-fwd-demo' http://127.0.0.1:8787/gateway/flags
   ```

   Uitkomst, ongeveer:

   ```json
   [{"conversation":"sid:ses_...","rule_id":"flag_no_spec","since_turn":1,"probability":0.97}]
   ```

## 7. Skill: status opvragen

Typ in opencode:

```
Wat is de gateway status?
```

Verwacht: het model laadt de skill `gateway-status`, draait `scripts/gateway-status.sh` en toont
de fase, Flags, open Proposals, Blocks en de laatste Decision.

Doe je dit in de sessie van scenario a, dan komt eerst de issue-vraag weer (zie de opmerking bij
scenario a). Kies "Nee, ga door"; daarna volgt de status. In een nieuwe sessie (`/new`) komt de
vraag niet.

## 8. Scenario c: pull request zonder groene tests → Block

Begin een nieuwe sessie met `/new`. Typ:

```
Issue #7, zie SPEC.md. Maak nu een pull request aan met gh pr create voor mijn wijzigingen.
```

Verwacht:

1. Het antwoord in opencode is: "De Gateway blokkeert dit verzoek: er wordt een pull request
   aangemaakt, maar in dit gesprek is niet gemeld dat de tests groen zijn ...".
2. In de Gateway-log staat `action=block(block_pr_without_tests)`.
3. Op het dashboard staat een rood label `block block_pr_without_tests (turn 1)`.

Typ daarna:

```
Draai eerst de tests met pytest.
```

Verwacht: dit gaat door (`action=forward`). Het model draait `pytest` en meldt 2 passed.

Typ daarna `Maak nu de PR.` Verwacht: geen Block meer. Het model meldt dat er geen git remote is.

## 9. Read-API met curl

Alle endpoints vragen de header `Authorization: Bearer sk-gw-fwd-demo`.

```bash
KEY='Authorization: Bearer sk-gw-fwd-demo'
curl -s -H "$KEY" http://127.0.0.1:8787/gateway/status | python3 -m json.tool
curl -s -H "$KEY" http://127.0.0.1:8787/gateway/flags
curl -s -H "$KEY" "http://127.0.0.1:8787/gateway/proposals?status=open"
curl -s -H "$KEY" http://127.0.0.1:8787/gateway/conversations
curl -s -H "$KEY" http://127.0.0.1:8787/gateway/conversations/sid:ses_...   # met alle events
```

De skill-uitvoer krijg je ook zonder opencode: `bash skills/gateway-status/scripts/gateway-status.sh`
(voeg `--json` toe voor de ruwe JSON).

## 10. Tekst-modus (optioneel)

`opencode run` biedt geen `question`-tool aan. De Gateway stelt de vraag dan als gewone tekst:

```bash
cd /tmp/coder-gateway-demo
opencode run "Voeg een functie power(a, b) toe aan calc.py."
# → "... Zullen we eerst een issue aanmaken ...? (antwoord ja of nee)"
opencode run --continue "nee"
```

## 11. Automatische tests en benchmark

In de repo:

```bash
uv run pytest -q              # unit- en app-tests, zonder externe API's
uv run pytest -q -m live      # tests die echt Jev/OpenAI aanroepen (kost geld)
uv run mypy
uv run ruff check src tests
uv run benchmark              # meet Jev per strategie op benchmark/fixtures/
uv run benchmark --strategies full --repeats 1   # sneller
```

De benchmark schrijft de resultaten naar `var/benchmark/`. Uitleg staat in `benchmark/README.md`.

## 12. Problemen oplossen

- **opencode geeft "Cannot connect to API".** Draait de Gateway? Klopt de poort in
  `opencode.json` van het demo-project?
- **401 van de Gateway.** De key in `opencode.json` of in je curl-commando klopt niet met
  `config/gateway.yaml`.
- **Waar zie ik wat er gebeurde?**
  - De Gateway-log: één regel per request met `decision=...`, `broken=[...]` en `action=...`.
  - `var/events.jsonl`: elk event als één JSON-regel, ook na een herstart.
  - Het dashboard: de laatste 12 events per Conversation.
- **In de log staat `decider jev failed after 3000ms (TimeoutError()); trying llm`.** Jev was te
  traag. De LLM-terugval (`gpt-5.4-mini`) nam het over. Dat is geen fout.
- **Er gebeurt nooit iets.** Staat er `broken=error(...)` in de log? Dan faalden beide
  Deciders en liet de Gateway alles door (fail-open). Controleer de keys in `.env.local`.
- **Een andere Decider proberen.** Pas `decider` aan in `config/gateway.yaml` en herstart de
  Gateway:

  ```yaml
  decider:
    primary: llm      # jev | llm | none
    fallback: none    # llm | none
  ```

  Met `primary: none` grijpt de Gateway nooit in. Hij stuurt dan alles door naar OpenAI.
- **Flags en Proposals zijn weg.** De Gateway houdt ze alleen in het geheugen. Na een herstart
  begint alles opnieuw.
- **Subagent-requests.** In de log staat soms `passthrough (subagent of ses_...)`. Dat zijn
  requests van een opencode-subagent. Die krijgen geen Decision (`docs/DECISIONS.md` #20).
