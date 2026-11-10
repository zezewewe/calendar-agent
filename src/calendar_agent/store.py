"""Pending proposals survive restarts (SQLite). Applied changes are journaled so they can be undone."""
from __future__ import annotations

import json
import sqlite3

from .models import Proposal


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, user_id TEXT, body TEXT, created REAL DEFAULT (strftime('%s','now')));
            CREATE TABLE IF NOT EXISTS messages (chat_id TEXT, message_id TEXT, proposal_id TEXT, PRIMARY KEY (chat_id, message_id));
            CREATE TABLE IF NOT EXISTS journal (proposal_id TEXT, user_id TEXT, kind TEXT, event_id TEXT, snapshot TEXT);
            """
        )
        try:  # migrate the old add-only `created` table into the journal
            self._db.execute("ALTER TABLE created RENAME TO journal_old")
            self._db.execute("INSERT INTO journal SELECT proposal_id, user_id, 'add', event_id, NULL FROM journal_old")
            self._db.execute("DROP TABLE journal_old")
        except sqlite3.OperationalError:
            pass

    def save(self, p: Proposal) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO proposals (id, user_id, body) VALUES (?,?,?)", (p.id, p.user_id, p.model_dump_json()))

    def get(self, proposal_id: str) -> Proposal | None:
        row = self._db.execute("SELECT body FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        return Proposal.model_validate(json.loads(row[0])) if row else None

    def take(self, proposal_id: str) -> Proposal | None:
        """Fetch and remove atomically, so a double-tapped Confirm can't create events twice."""
        with self._db:
            row = self._db.execute("SELECT body FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                return None
            self._db.execute("DELETE FROM proposals WHERE id=?", (proposal_id,))
        return Proposal.model_validate(json.loads(row[0]))

    def journal_add(self, proposal_id: str, user_id: str, event_id: str) -> None:
        with self._db:
            self._db.execute("INSERT INTO journal VALUES (?,?,?,?,?)", (proposal_id, user_id, "add", event_id, None))

    def journal_remove(self, proposal_id: str, user_id: str, event_id: str, snapshot: str) -> None:
        """Record a removed event plus the JSON needed to recreate it on undo."""
        with self._db:
            self._db.execute("INSERT INTO journal VALUES (?,?,?,?,?)", (proposal_id, user_id, "remove", event_id, snapshot))

    def take_journal(self, proposal_id: str, user_id: str | None = None) -> list[tuple[str, str, str | None]]:
        """Remove and return the (kind, event_id, snapshot) rows for a proposal."""
        with self._db:
            if user_id is None:
                where, args = "proposal_id=?", (proposal_id,)
            else:
                where, args = "proposal_id=? AND user_id=?", (proposal_id, user_id)
            rows = [(r[0], r[1], r[2]) for r in self._db.execute(f"SELECT kind, event_id, snapshot FROM journal WHERE {where}", args)]
            self._db.execute(f"DELETE FROM journal WHERE {where}", args)
        return rows

    def link_message(self, chat_id: str, message_id: str, proposal_id: str) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?)", (chat_id, message_id, proposal_id))

    def proposal_for_message(self, chat_id: str, message_id: str) -> str | None:
        row = self._db.execute("SELECT proposal_id FROM messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)).fetchone()
        return row[0] if row else None
