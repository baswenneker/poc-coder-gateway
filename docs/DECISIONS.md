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
Bij een Proposal stuurt de Gateway de request niet upstream, maar antwoordt hij zelf met de
voorstel-tool-call. Het antwoord van de Developer gaat in de volgende request wel upstream, dus het
model ziet vraag en antwoord en gaat daarop verder. Dat is eenvoudiger dan een tool call aan een
lopende upstream-stream toevoegen, en het model blijft niet geblokkeerd: bij "nee" gaat het gewoon door.

## 5. Decision synchroon in het request-pad, fail-open
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
