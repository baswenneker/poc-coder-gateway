import json
from pathlib import Path

import pytest
from gateway_helpers import make_decision, make_workflow

from coder_gateway.domain import Message, ProposalSpec
from coder_gateway.store import (
    ConversationStore,
    ProposalMode,
    ProposalStatus,
    parse_text_answer,
    parse_tool_answer,
)

SPEC = ProposalSpec(question="q?", accept_label="Yes, create an issue", decline_label="No, continue")


def test_parse_tool_answer() -> None:
    assert parse_tool_answer('User answered: "q?"="Yes, create an issue"', SPEC) is ProposalStatus.ACCEPTED
    assert parse_tool_answer('["no, CONTINUE"]', SPEC) is ProposalStatus.DECLINED
    assert parse_tool_answer("The user dismissed this question", SPEC) is ProposalStatus.DECLINED
    assert parse_tool_answer('"q?"="spec first"', SPEC) is ProposalStatus.ANSWERED


# Real tool results from opencode 1.18.32 (captured live via `opencode serve`).
_REAL_Q = (
    "No issue has been mentioned for this work yet. Shall we create an issue first, so the work is traceable?"
)


def _opencode_answer(value: str, question: str = _REAL_Q) -> str:
    return (
        f'User has answered your questions: "{question}"="{value}". '
        "You can now continue with the user's answers in mind."
    )


def test_parse_tool_answer_real_opencode_format() -> None:
    assert parse_tool_answer(_opencode_answer("Yes, create an issue"), SPEC) is ProposalStatus.ACCEPTED
    assert parse_tool_answer(_opencode_answer("No, continue"), SPEC) is ProposalStatus.DECLINED
    assert parse_tool_answer(_opencode_answer("Write the spec first"), SPEC) is ProposalStatus.ANSWERED
    # Multi-select: both labels in one value.
    both = _opencode_answer("Yes, create an issue, No, continue")
    assert parse_tool_answer(both, SPEC) is ProposalStatus.ANSWERED
    assert parse_tool_answer("The user dismissed this question", SPEC) is ProposalStatus.DECLINED


def test_parse_tool_answer_requires_exact_match_in_known_format() -> None:
    # A free-form value that merely contains the accept label must not count as accepted, or the
    # Proposal is suppressed for good (codex-review-2, finding 1).
    answer = _opencode_answer("Not Yes, create an issue; spec first")
    assert parse_tool_answer(answer, SPEC) is ProposalStatus.ANSWERED


def test_parse_tool_answer_ignores_labels_in_question_text() -> None:
    question = "No, continue is fine too. Shall we create an issue first?"
    answer = _opencode_answer("Yes, create an issue", question=question)
    assert parse_tool_answer(answer, SPEC) is ProposalStatus.ACCEPTED


def test_parse_text_answer() -> None:
    for text in ("ja", "Yes please", "ok.", "y", "Yes, create an issue"):
        assert parse_text_answer(text, SPEC) is ProposalStatus.ACCEPTED, text
    # `opencode run "no, just continue"` sends the message wrapped in double quotes.
    for text in ('"ja"', '"Yes, create an issue"'):
        assert parse_text_answer(text, SPEC) is ProposalStatus.ACCEPTED, text
    for text in ("nee", "No thanks", "n", "no, continue", '"no, just continue"'):
        assert parse_text_answer(text, SPEC) is ProposalStatus.DECLINED, text
    for text in ("yesterday it broke", "not now, lunch first", ""):
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
        {"role": "tool", "tool_call_id": p.id, "content": '"q"="No, continue"'},
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
    store.apply_answers(conv, [{"role": "tool", "tool_call_id": p.id, "content": "Yes, create an issue"}], wf)
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


def test_new_turn_when_history_shrinks() -> None:
    # Codex review 1, finding 3: after compaction the request carries fewer user messages. A changed
    # latest Developer message still starts a new Turn; the same one (more agent steps) does not.
    store = ConversationStore()
    conv = store.get_or_create("vm", "c")
    store.begin_request(conv, 3, "c")
    assert conv.turn == 3
    store.begin_request(conv, 1, "c")  # compacted mid-Turn: same latest message
    assert conv.turn == 3
    store.begin_request(conv, 2, "d")  # compacted history plus a new Developer message
    assert conv.turn == 4
    store.begin_request(conv, 2, "d")
    assert conv.turn == 4
    store.begin_request(conv, 3, "d")  # the same text sent again is a new message
    assert conv.turn == 5


def test_event_log_write_failure_is_logged_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # Codex review 1, finding 5: an unwritable events file must not break request handling.
    path = tmp_path / "events.jsonl"
    store = ConversationStore(events_path=path)
    path.mkdir()
    conv = store.get_or_create("vm", "c")
    store.record_event(conv, "x")
    assert [e["type"] for e in conv.events] == ["conversation_started", "x"]
    assert len([r for r in caplog.records if "events" in r.getMessage()]) == 1
    path.rmdir()
    store.record_event(conv, "y")  # writable again: the write goes through
    assert [json.loads(line)["type"] for line in path.read_text().splitlines()] == ["y"]


def test_event_log_dir_creation_failure_does_not_block_startup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Codex review 2, finding 3: a directory that cannot be created must not stop the Gateway from
    # starting up; it just falls back to in-memory events.
    blocker = tmp_path / "var"
    blocker.write_text("not a directory")
    path = blocker / "events.jsonl"
    store = ConversationStore(events_path=path)
    conv = store.get_or_create("vm", "c")
    store.record_event(conv, "x")
    assert [e["type"] for e in conv.events] == ["conversation_started", "x"]
    assert not path.exists()
    assert len([r for r in caplog.records if "events" in r.getMessage()]) == 1
