# poc-coder-gateway — werkplan

Prototype. Ruw mag, moeilijk instellen mag.

## Wat het is
OpenAI-compatible gateway. Per API key een virtual model (echt model + system prompt +
workflow-definitie). Een async observer leest het transcript en toetst het tegen de
workflow. Drie ingrepen: flag, injectie, blokkade.

## Besluiten
- Injectie = een tool call met vast id (`gateway_proposal`) in het assistant-antwoord.
  De developer antwoordt ja/nee/anders. Niet blokkerend aan modelzijde.
- Blokkeren kan, maar alleen als de workflow-definitie dat per regel zegt.
- Gateway heeft een read-API (status, flags, openstaande voorstellen) zodat een skill
  in de coding agent dit kan tonen.
- Conversatie-identificatie: fingerprint op de eerste user-berichten (opencode stuurt geen session-id).
- Upstream: OpenAI direct (OPENAI_API_KEY in .env). Verzoek is al OpenAI-formaat, dus doorgeven zonder vertaling.
- Streaming zit in de POC. Bij block en propose produceert de gateway zelf een SSE-stream.
- Beslissing "ingrijpen of niet" per beurt door Jev (typesafe.ai, System One model):
  input = transcript + gestructureerde state, output = vooraf gedefinieerde enum + kans.
  70-500ms, dus kan synchroon in het request-pad. Early access, hosted.
  https://typesafe.ai/blog/introducing-system-one-models-and-jev

## Vereenvoudigde case (eerste bouwdoel)
1. Injectie: geen issue genoemd in de conversatie en developer begint te coderen
   → voorstel "zullen we eerst een issue aanmaken?" via tool call.
2. Flag: developer past code aan zonder dat er een spec-bestand is besproken
   → flag `no_spec` in dashboard/API, geen effect op gesprek.
3. Blokkade: request bevat `gh pr create` (of gelijkwaardig) terwijl tests niet
   groen zijn gemeld → gateway weigert, stuurt uitleg terug als assistant-antwoord.

## Todo
- [ ] Benchmarkscript: strategieën voor Jev-state vergelijken op fixtures met ground truth
- [ ] End-to-end test met opencode als client (provider-config, scenario voor propose/flag/block)
- [ ] Configuratieformaat van workflow-definitie uitwerken (fases, regels, ingreep per regel)
- [x] Techstack: Python + uv, FastAPI, streaming (SSE). Upstream OpenAI. Jev via typesafe-sdk.
- [ ] Jev toetsen (key is er): classificeert het transcript betrouwbaar naar {none, propose_issue, flag_no_spec, block_pr}?
- [ ] Fallback als Jev niet beschikbaar is: klein LLM met structured output (zelfde enum)
- [ ] Fingerprint-functie voor conversatie-identificatie
- [ ] Skill voor coding agent die de read-API bevraagt

## Benchmark: wat krijgt Jev als state?
Repeatable script (`uv run benchmark`) dat per strategie de Jev-kansen meet op een vaste set
testtranscripten met bekende verwachte uitkomst (ground truth per regel).

Strategieën:
1. hele conversatie
2. alleen laatste 10 berichten
3. laatste 10 berichten volledig, alles daarvoor met tool results vervangen door `<truncated>`
4. (later) samenvatting + laatste 10

Output per strategie: accuracy per regel bij de ingestelde drempel, gemiddelde latency,
tokens per call. Script kiest niets, het rapporteert. Wij kiezen op basis van de tabel.

Testtranscripten: handmatig geschreven fixtures in `benchmark/fixtures/*.json`, elk met
`expected: {propose_issue: true/false, flag_no_spec: ..., block_pr_without_tests: ...}`.

## End-to-end test met opencode
opencode als coding agent, custom provider die naar onze gateway wijst (OpenAI-compatible).
Scenario: developer begint te coderen zonder issue → propose zichtbaar als tool call;
developer maakt PR zonder groene tests → block zichtbaar als antwoord; flag zichtbaar via
read-API. Vastleggen in `docs/e2e-opencode.md` hoe je dit draait.
