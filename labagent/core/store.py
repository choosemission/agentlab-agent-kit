# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""One SQLite file for the agent's memory: core tables plus each module's own.

Modules prefix their tables with their name and supply their DDL through
`Capability.migrations()`. Every statement is `CREATE … IF NOT EXISTS`, which
is the whole migration story until a module needs to change a table.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

CORE = [
    """CREATE TABLE IF NOT EXISTS seen_messages (
        message_id TEXT PRIMARY KEY, at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS inbound_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, skill TEXT, label TEXT,
        reason TEXT, peer TEXT, issuer TEXT, holder TEXT, outcome TEXT)""",
    """CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT NOT NULL, kind TEXT NOT NULL,
        peer TEXT, summary TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, decided_at REAL)""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, peer TEXT NOT NULL, body TEXT NOT NULL,
        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL,
        last_error TEXT, created_at REAL NOT NULL, delivered_at REAL)""",
]


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.migrate(CORE)

    def migrate(self, statements: Iterable[str]) -> None:
        with self._lock:
            for statement in statements:
                self._db.execute(statement)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run a write; returns lastrowid."""
        with self._lock:
            return self._db.execute(sql, tuple(params)).lastrowid or 0

    def changes(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run a write; returns the number of rows it changed."""
        with self._lock:
            return self._db.execute(sql, tuple(params)).rowcount

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, tuple(params)).fetchall()]

    # --- core helpers -------------------------------------------------------

    def first_sight(self, message_id: str) -> bool:
        """True the first time a message id is seen. A redelivery is answered
        without being acted on twice."""
        return self.changes(
            "INSERT OR IGNORE INTO seen_messages (message_id, at) VALUES (?, ?)", (message_id, time.time())
        ) == 1

    def audit(self, *, skill: str | None, provenance: dict[str, Any], outcome: str) -> None:
        self.execute(
            "INSERT INTO inbound_audit (at, skill, label, reason, peer, issuer, holder, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                skill,
                provenance.get("label"),
                provenance.get("reason"),
                provenance.get("peer") or provenance.get("claimed_peer"),
                provenance.get("issuer"),
                provenance.get("holder"),
                outcome,
            ),
        )


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
