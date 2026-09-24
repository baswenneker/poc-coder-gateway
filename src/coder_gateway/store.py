"""In-memory ConversationStore: per Conversation the Turn, Flags, Proposals, Blocks and events.

Single process, asyncio: every method is synchronous (no awaits), so each call is atomic with
respect to other requests. Every event is also appended to a JSON-lines file when configured.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from coder_gateway.domain import (
    PROPOSAL_TOOL_CALL_PREFIX,
    Decision,
    Intervention,
    Message,
    ProposalSpec,
    WorkflowDefinition,
)
from coder_gateway.fingerprint import content_text, store_key, user_messages

log = logging.getLogger("coder_gateway.store")

MAX_EVENTS = 200


class ProposalMode(StrEnum):
    TOOL = "tool"  # asked via the client's `question` tool; answer comes back as a tool result
    TEXT = "text"  # asked as plain assistant text; the next Developer message is the answer


class ProposalStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    ANSWERED = "answered"  # answered with something other than accept/decline


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


# --- Proposal answer parsing (kept small and separate so it is easy to adjust) -------------------

_DISMISSED = ("dismissed", "rejected", "cancelled", "canceled")
_TEXT_ACCEPT = {"ja", "yes", "ok", "okay", "y", "j"}
_TEXT_DECLINE = {"nee", "no", "n"}


# opencode 1.18's `question` tool result (captured live):
#   User has answered your questions: "<question>"="<label>". You can now continue with ...
# Several selected labels end up in one value, joined with ", ". A dismissed question gives
#   The user dismissed this question
_OPENCODE_ANSWERED = "user has answered your questions:"
_OPENCODE_ANSWER_VALUE = re.compile(r'"="(.*?)"(?=, "|\.\s|\.?$)', re.DOTALL)


def _classify_labels(text: str, spec: ProposalSpec) -> ProposalStatus | None:
    has_accept = spec.accept_label.lower() in text
    has_decline = spec.decline_label.lower() in text
    if has_accept and not has_decline:
        return ProposalStatus.ACCEPTED
    if has_decline and not has_accept:
        return ProposalStatus.DECLINED
    return None


def parse_tool_answer(content: str, spec: ProposalSpec) -> ProposalStatus:
    """Interpret opencode's `question` tool result. For opencode's known format only the answer
    value(s) after `"<question>"=` count, so words in the question text cannot tip the result, and
    only an exact (case-insensitive, trimmed) match with a label decides: a free-form value that
    merely contains the accept or decline label (e.g. "Not Yes, create an issue; spec first")
    stays `answered`, same as a value that selects both labels.
    Other formats: the accept/decline label anywhere in the text (case-insensitive) decides; a
    dismissed question counts as declined; anything else is a free-form answer."""
    text = content.lower()
    if text.lstrip().startswith(_OPENCODE_ANSWERED):
        values = _OPENCODE_ANSWER_VALUE.findall(content)
        if values:
            answer = " | ".join(v.strip().lower() for v in values)
            if answer == spec.accept_label.lower():
                return ProposalStatus.ACCEPTED
            if answer == spec.decline_label.lower():
                return ProposalStatus.DECLINED
            return ProposalStatus.ANSWERED
    status = _classify_labels(text, spec)
    if status is not None:
        return status
    if spec.accept_label.lower() not in text and any(word in text for word in _DISMISSED):
        return ProposalStatus.DECLINED
    return ProposalStatus.ANSWERED


def parse_text_answer(content: str, spec: ProposalSpec) -> ProposalStatus:
    """Interpret the Developer's next message after a text-mode Proposal (ja/yes/ok/y vs nee/no/n).
    Leading quotes and punctuation are ignored: `opencode run "nee"` sends the message as '"nee"'."""
    stripped = re.sub(r"^[\W_]+", "", content.strip().lower())
    if stripped.startswith(spec.accept_label.lower()):
        return ProposalStatus.ACCEPTED
    if stripped.startswith(spec.decline_label.lower()):
        return ProposalStatus.DECLINED
    match = re.match(r"[^\W\d_]+", stripped)
    first = match.group(0) if match else ""
    if first in _TEXT_ACCEPT:
        return ProposalStatus.ACCEPTED
    if first in _TEXT_DECLINE:
        return ProposalStatus.DECLINED
    return ProposalStatus.ANSWERED


# --- Records ------------------------------------------------------------------------------------


@dataclass
class ActiveFlag:
    rule_id: str
    since_turn: int
    probability: float


@dataclass
class ProposalRecord:
    id: str  # tool-call id, 'gateway_proposal_<n>'
    rule_id: str
    turn: int
    mode: ProposalMode
    status: ProposalStatus = ProposalStatus.OPEN
    answer: str | None = None
    created_at: str = field(default_factory=_now)
    answered_at: str | None = None
    answered_turn: int | None = None


@dataclass
class BlockRecord:
    rule_id: str
    turn: int
    at: str = field(default_factory=_now)


@dataclass
class Conversation:
    id: str
    virtual_model: str
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    turn: int = 0
    requests: int = 0
    # Decider calls (Jev). Normally one per Turn plus one per triggering tool call (DECISIONS.md #28).
    decisions: int = 0
    # The latest Developer message seen: user-message count and a hash of its text (Turn detection).
    user_count: int = 0
    last_user_hash: str = ""
    phase: str | None = None
    flags: dict[str, ActiveFlag] = field(default_factory=dict)
    proposals: list[ProposalRecord] = field(default_factory=list)
    blocks: list[BlockRecord] = field(default_factory=list)
    last_decision: dict[str, Any] | None = None
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))

    def open_proposals(self) -> list[ProposalRecord]:
        return [p for p in self.proposals if p.status is ProposalStatus.OPEN]

    def proposal_shown_this_turn(self) -> bool:
        return any(p.turn == self.turn for p in self.proposals)

    def to_dict(self, *, include_events: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "virtual_model": self.virtual_model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn": self.turn,
            "requests": self.requests,
            "decisions": self.decisions,
            "phase": self.phase,
            "flags": [asdict(f) for f in self.flags.values()],
            "proposals": [asdict(p) for p in self.proposals],
            "blocks": [asdict(b) for b in self.blocks],
            "last_decision": self.last_decision,
        }
        if include_events:
            data["events"] = list(self.events)
        return data


def decision_to_dict(decision: Decision) -> dict[str, Any]:
    return {
        "decider": decision.decider,
        "latency_ms": round(decision.latency_ms, 1),
        "phase": decision.phase,
        "broken": decision.broken(),
        "verdicts": {
            rid: {"probability": v.probability, "broken": v.broken} for rid, v in decision.verdicts.items()
        },
        "input_tokens": decision.input_tokens,
        "output_tokens": decision.output_tokens,
        "error": decision.error,
        "at": _now(),
    }


class ConversationStore:
    def __init__(self, events_path: Path | None = None, max_events: int = MAX_EVENTS) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._max_events = max_events
        self._write_failing = False
        if events_path is not None:
            try:
                events_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                # Same best-effort policy as _append (DECISIONS.md #26): warn once and keep running
                # with in-memory events only, instead of failing Gateway startup.
                log.warning("cannot create directory for events file %s: %s", events_path, exc)
                events_path = None
        self._events_path = events_path

    # --- lookup ---

    def get_or_create(self, virtual_model: str, conv_id: str) -> Conversation:
        key = store_key(virtual_model, conv_id)
        conv = self._conversations.get(key)
        if conv is None:
            conv = Conversation(id=conv_id, virtual_model=virtual_model)
            conv.events = deque(maxlen=self._max_events)
            self._conversations[key] = conv
            self.record_event(conv, "conversation_started")
        return conv

    def get(self, virtual_model: str, conv_id: str) -> Conversation | None:
        return self._conversations.get(store_key(virtual_model, conv_id))

    def list_conversations(self, virtual_model: str | None = None) -> list[Conversation]:
        convs = [
            c
            for c in self._conversations.values()
            if virtual_model is None or c.virtual_model == virtual_model
        ]
        return sorted(convs, key=lambda c: c.updated_at, reverse=True)

    def latest(self, virtual_model: str) -> Conversation | None:
        convs = self.list_conversations(virtual_model)
        return convs[0] if convs else None

    # --- events ---

    def record_event(self, conv: Conversation, type_: str, **data: Any) -> dict[str, Any]:
        event = {
            "ts": _now(),
            "conversation": conv.id,
            "virtual_model": conv.virtual_model,
            "turn": conv.turn,
            "type": type_,
            **data,
        }
        conv.events.append(event)
        conv.updated_at = event["ts"]
        if self._events_path is not None:
            self._append(self._events_path, event)
        return event

    def _append(self, path: Path, event: dict[str, Any]) -> None:
        """Best effort (DECISIONS.md #26): a failing events file is logged once until it works again,
        and never breaks the request."""
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            if not self._write_failing:
                log.warning("cannot write events to %s: %s", path, exc)
            self._write_failing = True
            return
        self._write_failing = False

    # --- per request ---

    def begin_request(self, conv: Conversation, user_count: int, last_user: str = "") -> None:
        """Register one request of the coding agent. A new Developer message starts a new Turn: more
        user messages than before, or another latest one (the client may have compacted the history,
        so the count alone can shrink). The Turn number follows the user-message count where it can
        and otherwise just goes up by one (DECISIONS.md #25)."""
        conv.requests += 1
        last_hash = hashlib.sha256(last_user.encode("utf-8")).hexdigest()[:16]
        if user_count > conv.user_count or last_hash != conv.last_user_hash:
            conv.turn = max(conv.turn + 1, user_count)
            self.record_event(conv, "turn_started")
        conv.user_count = user_count
        conv.last_user_hash = last_hash
        conv.updated_at = _now()

    def apply_answers(
        self, conv: Conversation, messages: list[Message], workflow: WorkflowDefinition
    ) -> list[ProposalRecord]:
        """Settle open Proposals from what the request carries. Returns the Proposals that changed."""
        changed: list[ProposalRecord] = []
        by_id = {p.id: p for p in conv.open_proposals()}
        # Tool mode: a tool result for our tool call.
        for m in messages:
            call_id = m.get("tool_call_id")
            if m.get("role") != "tool" or not isinstance(call_id, str):
                continue
            if not call_id.startswith(PROPOSAL_TOOL_CALL_PREFIX) or call_id not in by_id:
                continue
            p = by_id.pop(call_id)
            spec = _spec(workflow, p.rule_id)
            answer = content_text(m.get("content"))
            self._settle(conv, p, parse_tool_answer(answer, spec), answer)
            changed.append(p)
        users = user_messages(messages)
        for p in list(by_id.values()):
            if p.mode is ProposalMode.TEXT and conv.turn > p.turn and users:
                # Text mode: the Developer message that starts the next Turn is the answer. That is the
                # latest one of the first request in that Turn; no index into a history that may have
                # been compacted meanwhile (DECISIONS.md #25).
                answer = content_text(users[-1].get("content"))
                self._settle(conv, p, parse_text_answer(answer, _spec(workflow, p.rule_id)), answer)
                changed.append(p)
            elif p.mode is ProposalMode.TOOL and conv.turn > p.turn:
                # The Developer moved on without answering the question: treat it as declined.
                self._settle(conv, p, ProposalStatus.DECLINED, None, event="proposal_expired")
                changed.append(p)
        return changed

    def _settle(
        self,
        conv: Conversation,
        p: ProposalRecord,
        status: ProposalStatus,
        answer: str | None,
        event: str = "proposal_answered",
    ) -> None:
        p.status = status
        p.answer = answer
        p.answered_at = _now()
        p.answered_turn = conv.turn
        self.record_event(conv, event, proposal_id=p.id, rule_id=p.rule_id, status=str(status), answer=answer)

    def set_decision(
        self, conv: Conversation, decision: Decision, reason: str | None = None
    ) -> dict[str, Any]:
        """Record a Decision (one Decider call) and why it was made ('end_of_turn', 'trigger:<rule>')."""
        conv.decisions += 1
        conv.last_decision = {**decision_to_dict(decision), "reason": reason}
        if decision.phase and decision.error is None:
            conv.phase = decision.phase
        return conv.last_decision

    def update_flags(self, conv: Conversation, decision: Decision, workflow: WorkflowDefinition) -> None:
        """Set a Flag when its Rule is broken, clear it once the Rule is no longer broken."""
        if decision.error is not None:
            return
        for rule in workflow.rules:
            if rule.intervention is not Intervention.FLAG or rule.id not in decision.verdicts:
                continue
            verdict = decision.verdicts[rule.id]
            active = conv.flags.get(rule.id)
            if verdict.broken and active is None:
                conv.flags[rule.id] = ActiveFlag(rule.id, conv.turn, verdict.probability)
                self.record_event(conv, "flag_set", rule_id=rule.id, probability=verdict.probability)
            elif verdict.broken and active is not None:
                active.probability = verdict.probability
            elif not verdict.broken and active is not None:
                del conv.flags[rule.id]
                self.record_event(conv, "flag_cleared", rule_id=rule.id, probability=verdict.probability)

    # --- Proposals and Blocks ---

    def can_propose(self, conv: Conversation, rule_id: str) -> bool:
        """At most one Proposal per Turn; never while one for this Rule is open or after it was accepted.
        A declined (or otherwise answered) Proposal may return in a Turn after the one it was answered in."""
        for p in conv.proposals:
            if p.turn == conv.turn:
                return False
            if p.rule_id == rule_id and p.status in (ProposalStatus.OPEN, ProposalStatus.ACCEPTED):
                return False
            if p.rule_id == rule_id and p.answered_turn == conv.turn:
                return False
        return True

    def add_proposal(
        self, conv: Conversation, rule_id: str, mode: ProposalMode, messages: list[Message]
    ) -> ProposalRecord:
        n = max(len(conv.proposals), _max_proposal_number(messages)) + 1
        p = ProposalRecord(id=f"{PROPOSAL_TOOL_CALL_PREFIX}_{n}", rule_id=rule_id, turn=conv.turn, mode=mode)
        conv.proposals.append(p)
        self.record_event(conv, "proposal_shown", proposal_id=p.id, rule_id=rule_id, mode=str(mode))
        return p

    def add_block(self, conv: Conversation, rule_id: str) -> BlockRecord:
        b = BlockRecord(rule_id=rule_id, turn=conv.turn)
        conv.blocks.append(b)
        self.record_event(conv, "block", rule_id=rule_id)
        return b

    def decider_state(self, conv: Conversation) -> dict[str, Any]:
        """Structured state handed to the Decider next to the Transcript."""
        return {
            "turn": conv.turn,
            "phase": conv.phase,
            "active_flags": sorted(conv.flags),
            "proposals": [
                {"rule_id": p.rule_id, "status": str(p.status), "turn": p.turn} for p in conv.proposals
            ],
        }


def _spec(workflow: WorkflowDefinition, rule_id: str) -> ProposalSpec:
    try:
        spec = workflow.rule(rule_id).proposal
    except KeyError:
        spec = None
    return spec or ProposalSpec(question="")


def _max_proposal_number(messages: list[Message]) -> int:
    """Highest n among 'gateway_proposal_<n>' tool calls in the request (survives a Gateway restart)."""
    best = 0
    pattern = re.compile(rf"^{PROPOSAL_TOOL_CALL_PREFIX}_(\d+)$")
    for m in messages:
        ids = [tc.get("id") for tc in m.get("tool_calls") or [] if isinstance(tc, dict)]
        ids.append(m.get("tool_call_id"))
        for call_id in ids:
            match = pattern.match(call_id) if isinstance(call_id, str) else None
            if match:
                best = max(best, int(match.group(1)))
    return best
