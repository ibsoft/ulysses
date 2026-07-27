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
