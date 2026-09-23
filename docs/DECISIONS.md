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
