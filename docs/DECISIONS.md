# Keuzelogboek

Keuzes die tijdens de autonome bouw zijn gemaakt, met reden. Nieuwste onderaan.
Termen volgen `CONTEXT.md`.

## 1. Opencode stuurt wél een session-id; fingerprint blijft als terugval
Het plan zegt dat opencode geen session-id stuurt. Een afgevangen request van opencode 1.18.32
bevat de headers `x-session-id` en `x-session-affinity`. De Gateway gebruikt `x-session-id` als die
er is. Anders berekent hij de Fingerprint uit de eerste user-berichten. Zo werkt het ook met clients
zonder die header.

## 2. Requests zonder tools krijgen geen Decision
Opencode stuurt per beurt ook een aparte request om een titel te genereren (andere system prompt,
geen `tools`). Die hoort niet bij het werk van de Developer. De Gateway stuurt requests zonder
`tools` ongewijzigd door (alleen het model wordt vervangen) en neemt geen Decision.

## 3. Proposal gebruikt opencode's `question`-tool, met tool-call-id `gateway_proposal_<n>`
Het plan: injectie is een tool call met vast id `gateway_proposal`. Een tool call werkt alleen als de
client die tool kent. Opencode biedt in de TUI en server-modus een `question`-tool aan; die toont de
vraag met keuzeknoppen aan de Developer. De Gateway geeft de tool call daarom de naam `question` en
een id dat begint met `gateway_proposal`. Een volgnummer maakt het id uniek, omdat OpenAI dubbele
tool-call-ids in één conversatie kan weigeren. Het antwoord komt terug als tool-result met dat id.

Terugval: biedt de client geen `question`-tool aan (bijvoorbeeld `opencode run`, dat die tool uitzet),
dan stuurt de Gateway het voorstel als gewone assistant-tekst. Het volgende user-bericht geldt dan
als antwoord.

## 4. Proposal vervangt het upstream-antwoord van die request
Vervangen door #28.

Bij een Proposal stuurt de Gateway de request niet upstream, maar antwoordt hij zelf met de
voorstel-tool-call. Het antwoord van de Developer gaat in de volgende request wel upstream, dus het
model ziet vraag en antwoord en gaat daarop verder. Dat is eenvoudiger dan een tool call aan een
lopende upstream-stream toevoegen, en het model blijft niet geblokkeerd: bij "nee" gaat het gewoon door.

## 5. Decision synchroon in het request-pad, fail-open
Vervangen door #28.

Jev antwoordt in 70-500 ms (gemeten: ~230 ms). De Gateway wacht daarom op de Decision vóór hij
upstream gaat, met een timeout. Faalt Jev of loopt de timeout af, dan probeert hij de LLM-terugval.
Faalt die ook, dan gaat de request zonder Intervention door (fail-open). Een kapotte Decider mag de
Developer niet stilzetten.

## 6. Eén ja/nee-vraag per Rule in plaats van één enum
Het plan noemt één enum `{none, propose_issue, flag_no_spec, block_pr}`. Meerdere Rules kunnen
tegelijk gebroken zijn (bijvoorbeeld geen issue én geen spec). Een enum kiest er maar één. De Decider
stelt Jev daarom per Rule een `noul`-vraag (ja/nee met kans) in één call. Elke Rule heeft een eigen
drempel.

## 7. Block kijkt naar de request, niet naar het antwoord van het model
Vervangen door #28.

De Decision valt vóór de request upstream gaat. Een Block vangt dus het verzoek van de Developer
("maak een PR") of een eerdere `gh pr create`-poging in de Transcript. Een `gh pr create` die het
model zelf in zijn antwoord bedenkt, ziet de Gateway pas bij de volgende request. Het uitvoeren van
die tool call gebeurt dan al in de client. Beperking van de POC.

## 8. Benchmark bouwt de Decider via `build_decider(fallback="none")`, niet rechtstreeks
`src/coder_gateway/benchmark.py` moet Jev/LLM "kaal" meten, zonder terugval die fouten verbergt.
In plaats van `JevDecider`/`LLMDecider` rechtstreeks te importeren en te construeren (met interne
constructor-argumenten die bij de Deciders horen, niet bij de benchmark), roept de benchmark de
bestaande `build_decider(primary=..., fallback="none", ...)` aan. Dat is al de ene centrale plek
waar een naam naar een Decider wordt vertaald; `fallback="none"` levert dezelfde "geen terugval"-
garantie als rechtstreeks bouwen, zonder dat de benchmark de constructor-signatuur van elke Decider
hoeft te kennen. Nieuwe strategieën/Deciders (bijv. `summary_last_10`) blijven zo op één plek toevoegen.

## 9. Ground truth in fixtures is altijd over de volle conversatie, per strategie wordt alleen de
Transcript verkleind
`benchmark/fixtures/*.json` heeft één `expected` per fixture, niet één per strategie. Dat is
expres: `expected` is de juiste Decision zoals de Gateway die zou geven als hij het hele gesprek
zag. Een strategie als `last_10` mag daar juist slechter op scoren dan `full` — dat verschil in
accuracy per strategie, niet een aangepaste ground truth, is wat de tabel moet laten zien (zie
`long_conversation_early_issue_lost` en `long_conversation_early_spec_lost`).

## 10. Fouten van de Decider zelf tellen als "error", los van accuracy
Als `decider.decide()` een exception gooit of een `Decision` met `error` teruggeeft, telt de
benchmark dat als `errors`, apart van de accuracy-teller (die alleen over geslaagde calls gaat).
Een kapotte call zou anders de accuracy kunstmatig verlagen en het verschil tussen "Decider gaf het
verkeerde antwoord" en "Decider gaf helemaal geen antwoord" verdoezelen.

## 11. Een Proposal die niet beantwoord wordt, telt als afgewezen
Toont de Gateway een Proposal via de `question`-tool en begint de Developer een nieuwe Turn zonder
tool-result voor die vraag (bijvoorbeeld afgebroken), dan zet de Gateway de Proposal op `declined`
(event `proposal_expired`). Anders blijft hij eeuwig open en komt hij nooit meer terug.
Een Proposal die met iets anders dan ja/nee is beantwoord (`answered`) telt ook als afgewezen: hij
mag terugkomen zolang de Rule gebroken blijft.

## 12. Een afgewezen Proposal komt pas terug in de Turn ná het antwoord
In tekst-modus komt het antwoord ("nee") pas in de volgende Turn binnen. Zonder deze regel zou de
Gateway in diezelfde Turn meteen opnieuw vragen. Daarom: niet opnieuw voorstellen in de Turn waarin
de Proposal beantwoord is. Ook geldt maximaal één Proposal per Turn per Conversation, over alle
Rules heen.

## 13. Alleen Rules met ingreep `flag` worden een Flag
Een gebroken `propose`- of `block`-Rule leidt tot een Proposal of Block, niet tot een Flag. De
Decision zelf (alle kansen) staat wel in de read-API (`last_decision`) en op het dashboard. Een
mislukte Decision laat bestaande Flags staan.

## 14. Titel-requests krijgen ook `max_completion_tokens`, maar geen system prompt
Requests zonder `tools` gaan ongewijzigd door (#2), behalve het model en `max_tokens` →
`max_completion_tokens`: gpt-5.x weigert `max_tokens`, dus zonder die omzetting faalt de titel.
Het system prompt van het Virtual Model gaat alleen mee met agent-requests.

## 15. Timeout rond de hele Decision is 2 × `timeout_s` + 0,5 s
`timeout_s` geldt per Decider-call (Jev, daarna de LLM-terugval). De Gateway zet er zelf nog een
buitenste timeout omheen die ruimte laat voor beide calls. Loopt die af, dan fail-open (#5).

## 16. Proposal-nummer overleeft een herstart
`gateway_proposal_<n>`: n is één hoger dan het hoogste nummer in de store óf in de berichten van de
request. Na een herstart van de Gateway (store is in-memory) ontstaan zo geen dubbele tool-call-ids.
Live getest: OpenAI accepteert een eerdere `gateway_proposal_1`-tool-call met tool-result in de
geschiedenis.

## 17. Standaardstrategie `full`, timeout per Decider 3 s
De benchmark (18 testgesprekken, Jev) gaf: hele conversatie 94% goed, laatste 10 berichten 83%,
laatste 10 met ingekorte tool-output 89%. De fouten bij de kortere strategieën zitten in lange
gesprekken waar het issue of de spec vroeg genoemd is. Jev gebruikte bij `full` gemiddeld ~1.400
input-tokens, dus de kosten blijven klein. Daarom staat `config/gateway.yaml` op `full`. De
gemiddelde latency was ~0,9 s met een p95 van ~2,3 s; een timeout van 1,5 s zou dus vaak naar de
terugval springen. De timeout per Decider-call staat daarom op 3 s. Resultaten: `var/benchmark/`.

## 18. `compact_transcript` kort alleen nog met een zeer ruime head+tail-cap, niet meer vast op
2000 tekens
Elke `role='tool'`-boodschap werd altijd tot 2000 tekens afgekapt, ongeacht de strategie. Bij een
lang testlog met de samenvatting aan het eind ("42 passed") verdween precies dat stukje, ook bij
strategie `full`/`last_10` die de tool-output juist volledig wilden laten zien — een vals Block
na groene tests. Inkorten naar strategie is al het werk van `apply_strategy` (bijv.
`last_10_truncated` zet oudere tool-output al op `<truncated>`); `compact_transcript` mag dat niet
nog eens overdoen. Nu geldt alleen een generieke veiligheidsklep tegen extreem grote tool-output
(> 20.000 tekens): eerste 1.500 + laatste 3.000 tekens met een `...[N chars omitted]...`-merker
ertussen, zodat een samenvatting aan het eind altijd overleeft.

## 19. `build_decider(fallback="none")` levert een `SoloDecider`, geen `FallbackDecider` met
`NoneDecider` als terugval
Zoals gebouwd wrapte `fallback="none"` de primary alsnog in `FallbackDecider(primary,
NoneDecider(), timeout_s)`. Een primary-exception liet de terugval (`NoneDecider`) dan gewoon
slagen: een Decision met alle Rules niet-gebroken, `error=None` en `latency_ms=0.0` — niet te
onderscheiden van een correct "niets gebroken"-antwoord. In de benchmark (die juist met
`fallback="none"` de Decider kaal wil meten, zie #8) telde een kapotte call zo als een juiste
voorspelling in plaats van als `errors` (#10).
Nieuwe klasse `SoloDecider` (`deciders/fallback.py`): draait alleen de primary, met timeout; bij
exception/timeout faalt hij open (geen gebroken Rules), mét `error` gezet en `latency_ms` van de
mislukte poging. `build_decider` gebruikt `SoloDecider` wanneer `fallback="none"` en `primary !=
"none"`. De Gateway zelf (`config/gateway.yaml`: `fallback: llm`) blijft ongewijzigd via
`FallbackDecider` lopen, en blijft dus fail-open op dezelfde manier als voorheen (#5).

## 20. Requests van een opencode-subagent gaan door zonder Decision
De `task`-tool van opencode start een subagent (bijvoorbeeld `explore`) met een eigen sessie. Die
requests hebben de header `x-parent-session-id` (live gezien in opencode 1.18.32). Daar praat het
hoofdmodel met de subagent, niet de Developer. Een Proposal in tekst-modus zou dan door het
hoofdmodel beantwoord worden. Ook toonde `/gateway/status` soms de subagent als laatste
Conversation. Daarom: bij `x-parent-session-id` stuurt de Gateway de request door, met het system
prompt van het Virtual Model, maar zonder Decision en zonder eigen Conversation. Het resultaat van
de subagent komt als tool-result terug in de Conversation van de Developer; daar valt de Decision
wel. Beperking: een `gh pr create` in een subagent ziet de Gateway niet.

## 21. De Block-Rule kijkt naar het laatste bericht
Aangepast door #28: de Rule kijkt nu naar de tool call in het laatste assistant-bericht.

Live gezien: na een Block vroeg de Developer "Draai eerst de tests". Jev zag het eerdere PR-verzoek
nog in het gesprek en blokkeerde opnieuw (in de benchmark gaf de oude tekst 0,65, live boven de
drempel van 0,7). De Developer kon de tests zo nooit laten draaien. De `broken_when` van
`block_pr_without_tests` in `workflows/fwd-default.yaml` noemt nu expliciet het laatste bericht van
de Developer (of de laatste `gh pr create` van het model). `ok_when` zegt dat een eerder PR-verzoek
niet telt als het laatste bericht iets anders vraagt. Nieuwe fixture:
`benchmark/fixtures/after_block_developer_asks_for_tests.json` (nu 0,03). De benchmark op `full`
bleef voor deze Rule op 100%. De keuze ligt in de Rule-tekst, niet in de code: een team kan het
per Rule anders willen.

## 22. Antwoord op een Proposal: het echte opencode-formaat
De tool-result van opencode's `question`-tool is (live afgevangen):
`User has answered your questions: "<vraag>"="<antwoord>". You can now continue with the user's
answers in mind.` Wegklikken geeft `The user dismissed this question`. Meerdere gekozen labels
staan in één waarde, gescheiden door ", ". `parse_tool_answer` leest bij dit formaat alleen de
waarde na `"<vraag>"=`. Zo kan een label in de vraagtekst de uitkomst niet beïnvloeden. Exact het
accept-label is `accepted`, exact het decline-label is `declined`, al het andere is `answered`
(telt als afgewezen, #11). Andere formaten gebruiken de oude, ruime regel.
In tekst-modus zet `opencode run` een bericht met spaties tussen aanhalingstekens (`"nee, ga
door"`). `parse_text_answer` negeert daarom leestekens aan het begin.

## 23. Skill `gateway-status` staat in `skills/` en gaat per project naar `.opencode/skills/`
Opencode vindt skills in `.opencode/skills/<naam>/SKILL.md` van het project (en globaal in onder
meer `~/.claude/skills` en `~/.agents/skills`). De skill hoort bij de Gateway, niet bij één
project. Daarom staat het origineel in `skills/gateway-status/` van deze repo, en kopieert
`scripts/setup-demo.sh` hem naar `.opencode/skills/` van het demo-project. De skill roept een
klein script aan (`curl` plus `python3`), met `CODER_GATEWAY_URL` en `CODER_GATEWAY_KEY` als
instelling. Het script toont de laatst actieve Conversation van de API key via `/gateway/status`.
De skill weet zijn eigen sessie-id niet, dus een filter per sessie zit er niet in.

## 24. Eén lock per Conversation rond Decision en state-update
Codex-review 1, bevinding 2. De handler las de Conversation, wachtte op de Decider en schreef het
resultaat daarna in de dan geldende state. Een trage request uit Turn 1 kon zo na een snelle request
uit Turn 2 een Flag terugzetten en zijn Proposal op Turn 2 boeken. Nu houdt `app.py` per Conversation
een `asyncio.Lock` vast vanaf `begin_request` tot en met de gekozen Intervention. Het doorsturen naar
upstream (en het streamen) valt buiten de lock. Waarom een lock en niet "oude resultaten weggooien":
in één proces is het de eenvoudigste sluitende oplossing, en de volgorde van requests blijft de
volgorde van verwerken. Prijs: een tweede request van dezelfde Conversation wacht hooguit één
Decision (begrensd door `decision_timeout_s`). Opencode stuurt per sessie toch één request tegelijk.

## 25. Een nieuwe Turn herken je aan het laatste Developer-bericht, niet alleen aan het aantal
Codex-review 1, bevinding 3. `conv.turn` was het hoogste aantal user-berichten ooit gezien. Kort de
client de geschiedenis in (compaction), dan liep de Turn niet meer op: afgewezen Proposals bleven
onderdrukt en het antwoord in tekst-modus wees naar een index in de oude geschiedenis. Nu begint een
nieuwe Turn als het aantal user-berichten groter is dan bij de vorige request, óf als het laatste
user-bericht een andere tekst heeft (hash). Het Turn-nummer is `max(turn + 1, aantal)`: normaal
gelijk aan het aantal user-berichten, na compaction gewoon één hoger. Het antwoord op een Proposal in
tekst-modus is het laatste user-bericht van de eerste request in een latere Turn; geen index meer.
Beperking: stuurt de client na compaction een ander laatste user-bericht zonder dat de Developer iets
zei (bijv. een synthetische "ga door"), dan telt dat als nieuwe Turn.

## 26. Het event-log is best effort
Codex-review 1, bevinding 5. Een fout bij het schrijven naar `var/events.jsonl` (schijf vol, geen
rechten) brak de request af, ook na een fail-open Decision. Het bestand is een hulpmiddel om terug te
kijken, geen onderdeel van de Decision. Nu vangt `ConversationStore` een `OSError` af, logt één
waarschuwing (opnieuw pas nadat het schrijven weer eens gelukt is) en gaat door. De events blijven in
het geheugen, dus de read-API en het dashboard werken gewoon.
Codex-review 2, bevinding 3: hetzelfde geldt voor het aanmaken van de map van `var/events.jsonl` bij
het opstarten. Lukt `mkdir` niet (geen rechten), dan logt `ConversationStore.__init__` één
waarschuwing en start de Gateway gewoon door, zonder events-bestand.

## 27. Dashboard: open op localhost, optioneel een token
Codex-review 1, bevinding 6. `/gateway/` heeft geen API key nodig en toont alle Virtual Models. Voor
handmatig testen is dat handig (browser, geen header), en de Gateway bindt standaard op `127.0.0.1`.
Nieuw: optioneel `dashboard_token` in `config/gateway.yaml`. Staat die, dan vraagt het dashboard
`?token=<waarde>` (anders 401). `CODER_GATEWAY_HOST` kiest een ander adres; is dat geen loopback en
staat er geen token, dan logt de Gateway bij het starten een waarschuwing. De read-API
(`/gateway/status` enz.) blijft per Virtual Model afgeschermd met de API key.

## 28. Decision alleen aan het eind van een Turn en bij een trigger
Eerst riep de Gateway Jev aan vóór elke request. Eén Turn telt 5 tot 20 requests, één per
tool-call-ronde. Dat is veel calls voor weinig nieuws. Nu gaat elke request zonder Decision upstream.
De Gateway kijkt naar het antwoord van het model.

- **Eind van de Turn.** Het antwoord heeft `finish_reason` `stop` en geen tool calls. Dan geeft het
  model de beurt terug aan de Developer. De Gateway neemt precies hier één Decision, op de Transcript
  plus het laatste assistant-bericht. Daarna werkt hij de Flags bij.
- **Proposal achteraf.** Is een `propose`-Rule gebroken, dan hangt de Gateway de Proposal aan dit
  laatste antwoord. Met `question`-tool: een extra tool call `gateway_proposal_<n>` en finish
  `tool_calls`. Zonder: de vraag als extra tekst met "(antwoord ja of nee)". De vraag komt dus ná het
  werk: "Er is geen issue genoemd voor dit werk. Zullen we er een aanmaken?"
- **Block aan de antwoordkant.** Een Block-Rule heeft een `trigger`: een regex op de argumenten van
  een tool call, eventueel beperkt tot tool-namen. Voor `block_pr_without_tests`:
  `gh pr create|glab mr create|git push`, zonder tool-namen, zodat het bij elke client werkt. Past een
  tool call in het antwoord, dan neemt de Gateway een extra Decision. Is de Rule gebroken, dan laat hij
  de tool call weg en eindigt het antwoord met de uitleg (finish `stop`). De client voert de tool call
  dus nooit uit. Dat lost de beperking van #7 op.
- **Streaming.** Tekst gaat live door. Tool-call-deltas houdt de Gateway vast tot het antwoord klaar
  is; de client doet er pas iets mee na de finish. Ook de finish-chunk, de usage-chunk en `[DONE]`
  wachten op de Decision. Zonder ingreep gaan ze byte voor byte door.
- **Fail-open blijft.** Faalt de Decision of loopt de timeout af, dan gaat het antwoord ongewijzigd
  door. Andere finish-redenen (`length`, `content_filter`) en upstream-fouten krijgen geen Decision.
- **Lock (#24) blijft.** De lock per Conversation zit om Decision en state-update, niet om het
  streamen van de tekst.

Gevolg: normaal één Jev-call per Turn, plus één per triggerende tool call. Dashboard en
`/gateway/status` tonen per Conversation `requests` en `decisions`. De log schrijft één regel per
Decision met de reden: `end_of_turn` of `trigger:<rule_id>`.

## 29. Na een Proposal in dezelfde Turn geen tweede Decision aan het eind
Met de `question`-tool beantwoordt de Developer de Proposal binnen dezelfde Turn. Het model gaat
daarna verder en eindigt opnieuw met `stop`. Een tweede Decision kan dan niets meer voorstellen: er
mag maar één Proposal per Turn (#12). De Gateway slaat die Decision daarom over. Zo blijft het één
Jev-call per Turn. Prijs: de Flags lopen pas bij de volgende Decision bij. Een trigger-Decision (Block)
gebeurt wel altijd.

## 30. Trigger alleen op Block-Rules; een Block laat alle tool calls van dat antwoord weg
Een `trigger` op een `flag`- of `propose`-Rule is een fout bij het laden. Zo'n Rule doet pas iets aan
het eind van de Turn, dus een trigger heeft daar geen betekenis. Een Block-Rule zonder trigger is ook
een fout: hij zou nooit beoordeeld worden.
Vraagt het model in één antwoord meerdere tool calls en past er één in de trigger, dan laat een Block
ze allemaal weg. Een half uitgevoerd antwoord is lastiger te volgen dan één duidelijke stop.
De trigger is bewust goedkoop en grof. Live zagen we hem ook afgaan op een `todowrite` en een
`question` van het model met de tekst "gh pr create". Jev oordeelde dan "niet gebroken", want er
werd geen PR gemaakt. Dat kost één extra call, geen onterechte Block.

## 31. Een antwoord uit een oudere Turn krijgt geen Decision
Komt het antwoord van een request binnen terwijl de Developer al een nieuwe Turn begon, dan neemt de
Gateway geen Decision op dat antwoord. De state hoort dan al bij de nieuwe Turn. Opencode stuurt per
sessie één request tegelijk, dus dit gebeurt alleen na afbreken.

## 32. Rule-teksten en benchmark aangepast aan beslissen achteraf
De benchmark-fixtures eindigen nu waar de Gateway beslist: het laatste assistant-bericht van een
Turn, of een assistant-bericht met een triggerende tool call. Twee nieuwe fixtures:
`git_push_without_tests` en `pr_question_answered_in_words`. Rule-teksten in
`workflows/fwd-default.yaml`:

- `propose_issue`: gebroken als er code is gewijzigd zonder issue. Een afgewezen vraag om een issue
  telt niet als verwijzing. Zonder die zin zakte `declined_proposal_still_open` naar 0,61.
- `block_pr_without_tests`: gebroken als het laatste assistant-bericht een tool call doet die een PR
  maakt of pusht. Een commando dat alleen in tekst wordt uitgelegd telt niet. Zonder die zin gaf
  `pr_question_answered_in_words` 0,74.

`long_conversation_early_issue_lost` had `flag_no_spec: false`, maar er komt geen spec in voor.
Elke Jev-run gaf 0,95 of hoger. De ground truth is nu `true`.
Resultaat op `full` (21 fixtures, Jev, twee runs): 100% goed, gemiddeld ~0,5 s, p95 ~1 s.
