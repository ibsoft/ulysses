from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Message:
    id: int
    session_id: str
    role: str
    content: str
    created_at: str
    metadata: dict[str, Any]


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists sessions (
                    id text primary key,
                    title text not null,
                    created_at text not null,
                    updated_at text not null,
                    metadata text not null default '{}'
                );
                create table if not exists messages (
                    id integer primary key autoincrement,
                    session_id text not null references sessions(id) on delete cascade,
                    role text not null,
                    content text not null,
                    created_at text not null,
                    metadata text not null default '{}'
                );
                create index if not exists idx_messages_session_created on messages(session_id, created_at);
                """
            )

    def create_session(self, title: str = "New session", metadata: dict[str, Any] | None = None) -> str:
        session_id = f"sess_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                "insert into sessions(id, title, created_at, updated_at, metadata) values (?, ?, ?, ?, ?)",
                (session_id, title, now, now, json.dumps(metadata or {})),
            )
        return session_id

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from sessions order by updated_at desc").fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def session_metadata(self, session_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("select metadata from sessions where id = ?", (session_id,)).fetchone()
        if not row:
            return {}
        return json.loads(row["metadata"])

    def update_session_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "update sessions set metadata = ?, updated_at = ? where id = ?",
                (json.dumps(metadata), utcnow(), session_id),
            )

    def add_message(self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> int:
        now = utcnow()
        with self.connect() as conn:
            cursor = conn.execute(
                "insert into messages(session_id, role, content, created_at, metadata) values (?, ?, ?, ?, ?)",
                (session_id, role, content, now, json.dumps(metadata or {})),
            )
            conn.execute("update sessions set updated_at = ? where id = ?", (now, session_id))
            return int(cursor.lastrowid)

    def messages(self, session_id: str, limit: int = 50) -> list[Message]:
        with self.connect() as conn:
            rows = conn.execute(
                "select * from messages where session_id = ? order by id desc limit ?", (session_id, limit)
            ).fetchall()
        return [
            Message(row["id"], row["session_id"], row["role"], row["content"], row["created_at"], json.loads(row["metadata"]))
            for row in reversed(rows)
        ]

    def message_count(self, session_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute("select count(*) as count from messages where session_id = ?", (session_id,)).fetchone()
        return int(row["count"])

    def total_message_chars(self, session_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "select coalesce(sum(length(content)), 0) as chars from messages where session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["chars"])

    def prune_messages_keep_last(self, session_id: str, keep_last: int) -> int:
        with self.connect() as conn:
            rows = conn.execute(
                "select id from messages where session_id = ? order by id desc limit ?",
                (session_id, keep_last),
            ).fetchall()
            keep_ids = [int(row["id"]) for row in rows]
            if keep_ids:
                placeholders = ",".join("?" for _ in keep_ids)
                cursor = conn.execute(
                    f"delete from messages where session_id = ? and id not in ({placeholders})",
                    (session_id, *keep_ids),
                )
            else:
                cursor = conn.execute("delete from messages where session_id = ?", (session_id,))
            conn.execute("update sessions set updated_at = ? where id = ?", (utcnow(), session_id))
            return int(cursor.rowcount)

    def erase_all(self) -> None:
        with self.connect() as conn:
            conn.execute("delete from messages")
            conn.execute("delete from sessions")
