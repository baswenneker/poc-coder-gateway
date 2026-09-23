"""FastAPI app: the OpenAI-compatible endpoint with Decision + Interventions, and a read API."""

from __future__ import annotations

import asyncio
import html
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from coder_gateway import transcript
from coder_gateway.config import GatewayConfig
from coder_gateway.domain import Decider, Decision, DecisionInput, Intervention, Message, Rule, VirtualModel
from coder_gateway.fingerprint import conversation_id, user_messages
from coder_gateway.interventions import (
    QUESTION_TOOL,
    GatewayReply,
    block_reply,
    completion_json,
    completion_sse,
    proposal_text_reply,
    proposal_tool_reply,
)
from coder_gateway.store import Conversation, ConversationStore, ProposalMode, ProposalStatus
from coder_gateway.upstream import Upstream

log = logging.getLogger("coder_gateway")

StrategyFn = Callable[[list[Message], str], list[Message]]


def _openai_error(status: int, message: str, type_: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"message": message, "type": type_, "code": None}}
    )


def _tool_names(body: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for tool in body.get("tools") or []:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            names.add(fn["name"])
    return names


def create_app(
    config: GatewayConfig,
    decider: Decider | None = None,
    strategy_fn: StrategyFn | None = None,
    http_client: httpx.AsyncClient | None = None,
    store: ConversationStore | None = None,
) -> FastAPI:
    """Build the Gateway. Without a Decider every request is forwarded without Decision."""
    store = store if store is not None else ConversationStore(config.events_path)
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    upstream = Upstream(config.upstream, client)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_client:
            await client.aclose()

    app = FastAPI(title="Coder Gateway", lifespan=lifespan)
    app.state.store = store
    app.state.config = config

    def auth(request: Request) -> VirtualModel | None:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return config.virtual_model_for_key(token.strip())

    def require_vm(request: Request) -> VirtualModel:
        vm = auth(request)
        if vm is None:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        return vm

    async def decide(vm: VirtualModel, conv: Conversation, messages: list[Message]) -> Decision | None:
        """Run the Decider with a timeout. Fail-open: any failure yields a Decision with `error` set."""
        if decider is None:
            return None
        started = time.perf_counter()
        name = getattr(decider, "name", type(decider).__name__)
        try:
            reduce = strategy_fn or transcript.apply_strategy
            inp = DecisionInput(
                workflow=vm.workflow,
                transcript=reduce(messages, config.decider.strategy),
                state=store.decider_state(conv),
            )
            return await asyncio.wait_for(decider.decide(inp), timeout=config.decider.decision_timeout_s)
        except TimeoutError:
            error = f"timeout after {config.decider.decision_timeout_s:.1f}s"
        except Exception as exc:  # noqa: BLE001 - a broken Decider must never stop the Developer
            error = f"{type(exc).__name__}: {exc}"
        return Decision(
            verdicts={}, decider=name, latency_ms=(time.perf_counter() - started) * 1000, error=error
        )

    def choose_intervention(
        vm: VirtualModel,
        conv: Conversation,
        decision: Decision | None,
        body: dict[str, Any],
        messages: list[Message],
    ) -> tuple[str, GatewayReply | None, Rule | None]:
        if decision is None or decision.error is not None:
            return "forward", None, None
        broken = [
            r for r in vm.workflow.rules if r.id in decision.verdicts and decision.verdicts[r.id].broken
        ]
        for rule in broken:
            if rule.intervention is Intervention.BLOCK:
                store.add_block(conv, rule.id)
                return "block", block_reply(rule), rule
        for rule in broken:
            if (
                rule.intervention is Intervention.PROPOSE
                and rule.proposal
                and store.can_propose(conv, rule.id)
            ):
                if QUESTION_TOOL in _tool_names(body):
                    p = store.add_proposal(conv, rule.id, ProposalMode.TOOL, messages)
                    return "propose_tool", proposal_tool_reply(rule.proposal, p.id), rule
                store.add_proposal(conv, rule.id, ProposalMode.TEXT, messages)
                return "propose_text", proposal_text_reply(rule.proposal), rule
        return "forward", None, None

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def list_models(request: Request) -> dict[str, Any]:
        vm = auth(request)
        vms = [vm] if vm else list(config.virtual_models)
        return {
            "object": "list",
            "data": [
                {"id": v.name, "object": "model", "created": 0, "owned_by": "coder-gateway"} for v in vms
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        vm = auth(request)
        if vm is None:
            return _openai_error(401, "invalid or missing API key", "authentication_error")
        try:
            body = await request.json()
        except ValueError:
            return _openai_error(400, "request body is not valid JSON")
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            return _openai_error(400, "request needs a 'messages' list")
        messages: list[Message] = body["messages"]

        if not body.get("tools"):
            # Side requests such as opencode's title generation: no Decision (DECISIONS.md #2).
            log.info("vm=%s passthrough (no tools) stream=%s", vm.name, bool(body.get("stream")))
            return await upstream.forward(body, vm, inject_system_prompt=False)

        conv = store.get_or_create(vm.name, conversation_id(request.headers, messages))
        store.begin_request(conv, len(user_messages(messages)))
        store.apply_answers(conv, messages, vm.workflow)

        decision = await decide(vm, conv, messages)
        if decision is not None:
            summary = store.set_decision(conv, decision)
            store.record_event(conv, "decision_error" if decision.error else "decision", decision=summary)
            store.update_flags(conv, decision, vm.workflow)

        action, reply, rule = choose_intervention(vm, conv, decision, body, messages)
        stream = bool(body.get("stream"))
        log.info(
            "conv=%s turn=%d req=%d decision=%s broken=%s action=%s%s",
            conv.id,
            conv.turn,
            conv.requests,
            f"{decision.latency_ms:.0f}ms/{decision.decider}" if decision else "-",
            (f"error({decision.error})" if decision.error else decision.broken()) if decision else "-",
            action,
            f"({rule.id})" if rule else "",
        )
        if reply is None:
            store.record_event(conv, "forwarded", stream=stream)
            return await upstream.forward(body, vm)
        if stream:
            include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
            return StreamingResponse(
                iter(completion_sse(reply, vm.name, include_usage)),
                media_type="text/event-stream",
                headers={"cache-control": "no-cache"},
            )
        return JSONResponse(completion_json(reply, vm.name))

    # --- Read API (scoped to the caller's Virtual Model) -------------------------------------------

    @app.get("/gateway/conversations")
    async def conversations(request: Request) -> list[dict[str, Any]]:
        vm = require_vm(request)
        return [c.to_dict(include_events=False) for c in store.list_conversations(vm.name)]

    @app.get("/gateway/conversations/{conv_id:path}")
    async def conversation(conv_id: str, request: Request) -> dict[str, Any]:
        vm = require_vm(request)
        conv = store.get(vm.name, conv_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conv.to_dict()

    @app.get("/gateway/flags")
    async def flags(request: Request) -> list[dict[str, Any]]:
        vm = require_vm(request)
        return [
            {
                "conversation": c.id,
                "rule_id": f.rule_id,
                "since_turn": f.since_turn,
                "probability": f.probability,
            }
            for c in store.list_conversations(vm.name)
            for f in c.flags.values()
        ]

    @app.get("/gateway/proposals")
    async def proposals(request: Request, status: str | None = None) -> list[dict[str, Any]]:
        vm = require_vm(request)
        if status is not None and status not in {s.value for s in ProposalStatus}:
            raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
        return [
            {"conversation": c.id, **p_dict}
            for c in store.list_conversations(vm.name)
            for p_dict in c.to_dict(include_events=False)["proposals"]
            if status is None or p_dict["status"] == status
        ]

    @app.get("/gateway/status")
    async def status(request: Request) -> dict[str, Any]:
        """Summary of the caller's most recently active Conversation (for a skill in the coding agent)."""
        vm = require_vm(request)
        conv = store.latest(vm.name)
        if conv is None:
            return {"virtual_model": vm.name, "conversation": None}
        data = conv.to_dict(include_events=False)
        return {
            "virtual_model": vm.name,
            "conversation": conv.id,
            "turn": conv.turn,
            "phase": conv.phase,
            "flags": data["flags"],
            "open_proposals": [p for p in data["proposals"] if p["status"] == ProposalStatus.OPEN],
            "blocks": data["blocks"],
            "last_decision": conv.last_decision,
            "updated_at": conv.updated_at,
        }

    @app.get("/gateway/", response_class=HTMLResponse)
    async def dashboard() -> str:
        """Server-rendered overview of all Conversations. No auth: prototype, localhost only."""
        return render_dashboard(store.list_conversations())

    return app


# --- Dashboard ---------------------------------------------------------------------------------------

_CSS = """
:root { --bg:#fafafa; --fg:#1d1d1f; --muted:#6b6b70; --card:#fff; --line:#e3e3e8;
  --flag:#b45309; --block:#b91c1c; --ok:#15803d; --prop:#1d4ed8; }
@media (prefers-color-scheme: dark) { :root { --bg:#141416; --fg:#ececf0; --muted:#9a9aa3; --card:#1d1d21;
  --line:#2e2e34; --flag:#f59e0b; --block:#f87171; --ok:#4ade80; --prop:#93c5fd; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 -apple-system,system-ui,sans-serif;
  margin:0; padding:16px; }
h1 { font-size:18px; margin:0 0 4px; } .muted { color:var(--muted); }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 14px;
  margin:12px 0; max-width:1100px; }
.card h2 { font-size:15px; margin:0 0 6px; word-break:break-all; }
.tag { display:inline-block; border-radius:4px; padding:1px 6px; margin:2px 4px 2px 0; font-size:12px;
  border:1px solid currentColor; }
.flag { color:var(--flag); } .block { color:var(--block); }
.prop { color:var(--prop); } .ok { color:var(--ok); }
table { border-collapse:collapse; width:100%; font-size:12px; } td { padding:2px 6px 2px 0;
  border-top:1px solid var(--line); vertical-align:top; } code { font-size:12px; }
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def render_dashboard(conversations: list[Conversation]) -> str:
    cards: list[str] = []
    for c in conversations:
        flags = (
            "".join(
                f'<span class="tag flag">flag {_e(f.rule_id)} p={f.probability:.2f} '
                f"(turn {f.since_turn})</span>"
                for f in c.flags.values()
            )
            or '<span class="muted">no flags</span>'
        )
        props = "".join(
            f'<span class="tag prop">{_e(p.id)} {_e(p.rule_id)}: {_e(p.status)} ({_e(p.mode)}, turn {p.turn})'
            f"{' - ' + _e(p.answer[:60]) if p.answer else ''}</span>"
            for p in c.proposals
        )
        blocks = "".join(
            f'<span class="tag block">block {_e(b.rule_id)} (turn {b.turn})</span>' for b in c.blocks
        )
        d = c.last_decision
        decision = (
            f"{_e(d['decider'])} {d['latency_ms']} ms, broken: {_e(', '.join(d['broken']) or 'none')}"
            + (f' <span class="block">error: {_e(d["error"])}</span>' if d.get("error") else "")
            if d
            else '<span class="muted">no decision yet</span>'
        )
        events = "".join(
            f"<tr><td>{_e(ev['ts'][11:23])}</td><td>turn {_e(ev['turn'])}</td><td>{_e(ev['type'])}</td>"
            f"<td><code>{_e(_event_details(ev))}</code></td></tr>"
            for ev in reversed(list(c.events)[-12:])
        )
        cards.append(
            f'<div class="card"><h2>{_e(c.id)}</h2>'
            f'<div class="muted">{_e(c.virtual_model)} &middot; turn {c.turn} &middot; {c.requests} requests '
            f"&middot; phase {_e(c.phase or '-')} &middot; updated {_e(c.updated_at[11:19])} UTC</div>"
            f"<p>{flags}{props}{blocks}</p><p>Last Decision: {decision}</p>"
            f"<table>{events}</table></div>"
        )
    body = "".join(cards) or '<p class="muted">No Conversations yet.</p>'
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="refresh" content="3"><title>Coder Gateway</title>'
        f"<style>{_CSS}</style></head><body><h1>Coder Gateway</h1>"
        f'<div class="muted">{len(conversations)} Conversations &middot; refreshes every 3 s</div>'
        f"{body}</body></html>"
    )


_EVENT_BASE_KEYS = {"ts", "conversation", "virtual_model", "turn", "type"}


def _event_details(event: dict[str, Any]) -> str:
    return str({k: v for k, v in event.items() if k not in _EVENT_BASE_KEYS})[:200]
