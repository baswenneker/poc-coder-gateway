from coder_gateway.fingerprint import content_text, conversation_id, store_key


def test_session_header_wins() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    assert conversation_id({"X-Session-Id": "ses_123"}, msgs) == "sid:ses_123"


def test_fingerprint_from_first_user_message() -> None:
    a = conversation_id({}, [{"role": "system", "content": "s"}, {"role": "user", "content": "  fix bug  "}])
    b = conversation_id({}, [{"role": "user", "content": "fix bug"}, {"role": "user", "content": "more"}])
    assert a == b
    assert a.startswith("fp:") and len(a) == 3 + 16


def test_fingerprint_list_content_equals_string_content() -> None:
    parts = [{"type": "text", "text": "fix bug"}]
    assert conversation_id({}, [{"role": "user", "content": parts}]) == conversation_id(
        {}, [{"role": "user", "content": "fix bug"}]
    )


def test_different_first_message_different_fingerprint() -> None:
    assert conversation_id({}, [{"role": "user", "content": "a"}]) != conversation_id(
        {}, [{"role": "user", "content": "b"}]
    )


def test_empty_header_falls_back_to_fingerprint() -> None:
    assert conversation_id({"x-session-id": " "}, [{"role": "user", "content": "a"}]).startswith("fp:")


def test_content_text_and_store_key() -> None:
    assert content_text(None) == ""
    assert content_text([{"type": "text", "text": "a"}, {"type": "image_url"}, "b"]) == "a\nb"
    assert store_key("fwd-coder", "sid:x") != store_key("other", "sid:x")
