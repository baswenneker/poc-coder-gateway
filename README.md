# Coder Gateway (prototype)

Een OpenAI-compatible gateway tussen een coding agent (opencode) en OpenAI. De Gateway toetst het
gesprek aan de werkafspraken van het team en grijpt zo nodig in. Een Flag markeert een gebroken
afspraak zonder het gesprek te raken. Een Proposal stelt de Developer een vraag onder het laatste
antwoord. Een Block houdt een tool call tegen, zoals `gh pr create`, en legt uit waarom.

De Gateway beslist aan het eind van elke Turn, en extra als het model een PR wil maken of wil pushen.
Zo is het meestal één beslissing per Turn in plaats van één per request (`docs/DECISIONS.md` #28). De
beslissing komt van Jev (typesafe.ai), met een klein OpenAI-model als terugval. De
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
- [docs/reviews/](docs/reviews/): twee Codex-reviews met per bevinding de opvolging.
- [benchmark/README.md](benchmark/README.md): de benchmark van de Decider (`uv run benchmark`).
- [skills/gateway-status/SKILL.md](skills/gateway-status/SKILL.md): opencode-skill die de status
  toont via de read-API.

## Ontwikkelen

```bash
uv run pytest -q
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
```
