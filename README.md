# Coder Gateway (prototype)

Een OpenAI-compatible gateway tussen een coding agent (opencode) en OpenAI. De Gateway toetst elk
request aan de werkafspraken van het team en grijpt zo nodig in. Een Flag markeert een gebroken
afspraak zonder het gesprek te raken. Een Proposal stelt de Developer een vraag. Een Block weigert
een request en legt uit waarom.

De beslissing komt per request van Jev (typesafe.ai), met een klein OpenAI-model als terugval. De
werkafspraken staan in `workflows/fwd-default.yaml`. Begrippen staan in `CONTEXT.md`.

## Quickstart

```bash
uv sync
cp .env.example .env.local        # vul OPENAI_API_KEY en TYPESAFE_API_KEY in
uv run coder-gateway              # http://127.0.0.1:8787, dashboard op /gateway/
scripts/setup-demo.sh             # demo-project in /tmp/coder-gateway-demo
cd /tmp/coder-gateway-demo && opencode
```

## Meer lezen

- [TESTING.md](TESTING.md): stap voor stap handmatig testen, met de drie scenario's.
- [docs/e2e-opencode.md](docs/e2e-opencode.md): end-to-end test met opencode en wat we live zagen.
- [docs/DECISIONS.md](docs/DECISIONS.md): gemaakte keuzes en waarom.
- [benchmark/README.md](benchmark/README.md): de benchmark van de Decider (`uv run benchmark`).
- [skills/gateway-status/SKILL.md](skills/gateway-status/SKILL.md): opencode-skill die de status
  toont via de read-API.

## Ontwikkelen

```bash
uv run pytest -q
uv run mypy
uv run ruff check src tests
```
