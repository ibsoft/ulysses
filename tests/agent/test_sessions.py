from sirina_agent.sessions.store import SessionStore


def test_session_persistence(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    sid = store.create_session("test")
    msg_id = store.add_message(sid, "user", "hello")
    messages = store.messages(sid)
    assert msg_id > 0
    assert messages[0].content == "hello"
    assert store.list_sessions()[0]["id"] == sid


def test_session_metadata_and_prune(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    sid = store.create_session("test")
    for idx in range(5):
        store.add_message(sid, "user", f"message {idx}")
    store.update_session_metadata(sid, {"summary": "old context"})
    assert store.session_metadata(sid)["summary"] == "old context"
    deleted = store.prune_messages_keep_last(sid, 2)
    assert deleted == 3
    assert [msg.content for msg in store.messages(sid, limit=10)] == ["message 3", "message 4"]


def test_session_store_serializes_bytes_metadata(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    sid = store.create_session("test", {"raw": b"session bytes"})

    store.add_message(sid, "tool", b"tool bytes", {"data": {"stdout": b"byte output"}})

    assert store.session_metadata(sid)["raw"] == "session bytes"
    message = store.messages(sid)[0]
    assert message.content == "tool bytes"
    assert message.metadata["data"]["stdout"] == "byte output"


def test_session_store_redacts_nested_credentials(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    sid = store.create_session("test", {"oauth_token": "session-secret", "safe": "value"})

    store.add_message(
        sid,
        "tool",
        "completed",
        {"arguments": {"sudo_password": "chat-secret", "command": "sudo id"}},
    )

    assert store.session_metadata(sid)["oauth_token"] == "<redacted>"
    message = store.messages(sid)[0]
    assert message.metadata["arguments"]["sudo_password"] == "<redacted>"
    assert "chat-secret" not in str(message.metadata)
