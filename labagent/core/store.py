# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""One SQLite file for the agent's memory: the inbox and the contacts.

Each owner of a table supplies its DDL through `migrate()`. Every statement is
`CREATE … IF NOT EXISTS`, which is the whole migration story until a table
needs to change. Tables an older version of the kit created are left alone.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()

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
