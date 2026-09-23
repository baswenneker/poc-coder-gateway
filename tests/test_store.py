import json
from pathlib import Path

from gateway_helpers import make_decision, make_workflow

from coder_gateway.domain import Message, ProposalSpec
from coder_gateway.store import (
    ConversationStore,
    ProposalMode,
    ProposalStatus,
    parse_text_answer,
    parse_tool_answer,
)

SPEC = ProposalSpec(question="q?", accept_label="Ja, maak een issue", decline_label="Nee, ga door")


def test_parse_tool_answer() -> None:
    assert parse_tool_answer('User answered: "q?"="Ja, maak een issue"', SPEC) is ProposalStatus.ACCEPTED
    assert parse_tool_answer('["nee, GA DOOR"]', SPEC) is ProposalStatus.DECLINED
    assert parse_tool_answer("The user dismissed this question", SPEC) is ProposalStatus.DECLINED
    assert parse_tool_answer('"q?"="eerst de spec"', SPEC) is ProposalStatus.ANSWERED


def test_parse_text_answer() -> None:
    for text in ("ja", "Yes please", "ok.", "y", "Ja, maak een issue"):
        assert parse_text_answer(text, SPEC) is ProposalStatus.ACCEPTED, text
    for text in ("nee", "No thanks", "n", "nee, ga door"):
        assert parse_text_answer(text, SPEC) is ProposalStatus.DECLINED, text
    for text in ("yesterday it broke", "niet nu, eerst lunch", ""):
        assert parse_text_answer(text, SPEC) is ProposalStatus.ANSWERED, text


def test_events_written_to_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "var" / "events.jsonl"
    store = ConversationStore(events_path=path)
    conv = store.get_or_create("vm", "sid:1")
    store.begin_request(conv, 1)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["type"] for e in lines] == ["conversation_started", "turn_started"]
    assert list(conv.events) == lines


def test_events_bounded() -> None:
    store = ConversationStore(max_events=5)
    conv = store.get_or_create("vm", "c")
    for _ in range(20):
        store.record_event(conv, "x")
    assert len(conv.events) == 5


def test_conversations_scoped_by_virtual_model() -> None:
    store = ConversationStore()
    a = store.get_or_create("a", "sid:1")
    b = store.get_or_create("b", "sid:1")
    assert a is not b
    assert store.list_conversations("a") == [a]
    assert store.get("b", "sid:1") is b


def test_flag_set_and_cleared() -> None:
    wf = make_workflow()
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    store.begin_request(conv, 1)
    store.update_flags(conv, make_decision("flag_no_spec"), wf)
    assert conv.flags["flag_no_spec"].since_turn == 1
    assert "propose_issue" not in conv.flags  # only Rules with intervention 'flag' become Flags
    store.begin_request(conv, 2)
    store.update_flags(conv, make_decision("flag_no_spec"), wf)
    assert conv.flags["flag_no_spec"].since_turn == 1
    store.update_flags(conv, make_decision(error="boom"), wf)  # failed Decision leaves Flags alone
    assert "flag_no_spec" in conv.flags
    store.update_flags(conv, make_decision(), wf)
    assert conv.flags == {}
    assert [e["type"] for e in conv.events].count("flag_cleared") == 1


def test_proposal_once_per_turn_and_not_while_open() -> None:
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    store.begin_request(conv, 1)
    assert store.can_propose(conv, "propose_issue")
    p = store.add_proposal(conv, "propose_issue", ProposalMode.TOOL, [])
    assert p.id == "gateway_proposal_1"
    assert not store.can_propose(conv, "propose_issue")
    assert not store.can_propose(conv, "other_rule")  # at most one Proposal per Turn


def test_tool_answer_declined_returns_next_turn() -> None:
    wf = make_workflow()
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    store.begin_request(conv, 1)
    p = store.add_proposal(conv, "propose_issue", ProposalMode.TOOL, [])
    msgs: list[Message] = [
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "tool_calls": [{"id": p.id, "type": "function"}]},
        {"role": "tool", "tool_call_id": p.id, "content": '"q"="Nee, ga door"'},
    ]
    store.apply_answers(conv, msgs, wf)
    assert p.status is ProposalStatus.DECLINED
    assert not store.can_propose(conv, "propose_issue")  # same Turn
    store.begin_request(conv, 2)
    assert store.can_propose(conv, "propose_issue")
    p2 = store.add_proposal(conv, "propose_issue", ProposalMode.TOOL, msgs)
    assert p2.id == "gateway_proposal_2"


def test_accepted_never_again() -> None:
    wf = make_workflow()
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    store.begin_request(conv, 1)
    p = store.add_proposal(conv, "propose_issue", ProposalMode.TOOL, [])
    store.apply_answers(conv, [{"role": "tool", "tool_call_id": p.id, "content": "Ja, maak een issue"}], wf)
    assert p.status is ProposalStatus.ACCEPTED
    store.begin_request(conv, 5)
    assert not store.can_propose(conv, "propose_issue")


def test_unanswered_tool_proposal_expires_as_declined() -> None:
    wf = make_workflow()
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    store.begin_request(conv, 1)
    p = store.add_proposal(conv, "propose_issue", ProposalMode.TOOL, [])
    store.begin_request(conv, 2)
    store.apply_answers(conv, [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}], wf)
    assert p.status is ProposalStatus.DECLINED and p.answer is None


def test_text_mode_answer_is_next_user_message() -> None:
    wf = make_workflow()
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    store.begin_request(conv, 1)
    p = store.add_proposal(conv, "propose_issue", ProposalMode.TEXT, [])
    store.apply_answers(conv, [{"role": "user", "content": "fix"}], wf)  # same Turn: still open
    assert p.status == ProposalStatus.OPEN
    msgs: list[Message] = [
        {"role": "user", "content": "fix"},
        {"role": "assistant", "content": "Zullen we..."},
        {"role": "user", "content": [{"type": "text", "text": "ja graag"}]},
    ]
    store.begin_request(conv, 2)
    store.apply_answers(conv, msgs, wf)
    assert str(p.status) == "accepted" and p.answer == "ja graag"


def test_proposal_number_survives_restart() -> None:
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    msgs = [{"role": "assistant", "tool_calls": [{"id": "gateway_proposal_4"}]}]
    assert store.add_proposal(conv, "propose_issue", ProposalMode.TOOL, msgs).id == "gateway_proposal_5"


def test_decider_state_and_phase() -> None:
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    store.begin_request(conv, 1)
    store.set_decision(conv, make_decision("flag_no_spec", phase="implement"))
    store.update_flags(conv, make_decision("flag_no_spec"), make_workflow())
    state = store.decider_state(conv)
    assert state == {"turn": 1, "phase": "implement", "active_flags": ["flag_no_spec"], "proposals": []}
    assert conv.last_decision is not None and conv.last_decision["broken"] == ["flag_no_spec"]
