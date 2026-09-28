# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""The owner's queue: anything that would commit them waits here.

The agent represents a person, so the default for anything that binds them —
letting a new subscriber read their feed, accepting a meeting, publishing
something the agent drafted — is to ask. A module queues an item; the owner
decides through the CLI; the module's `on_decision` acts on it. Nothing a
counterparty sends can decide an approval.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .store import Store, dumps

PENDING, APPROVED, DENIED = "pending", "approved", "denied"


@dataclass(frozen=True)
class Approval:
    id: int
    module: str
    kind: str
    peer: str | None
    summary: str
    payload: dict[str, Any]
    status: str
    created_at: float
    decided_at: float | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Approval":
        return cls(**{**row, "payload": json.loads(row["payload"])})

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Approvals:
    def __init__(self, store: Store) -> None:
        self._store = store

    def request(self, *, module: str, kind: str, peer: str | None, summary: str, payload: dict[str, Any]) -> Approval:
        """Queue an item, or return the one already pending for the same thing."""
        existing = self._store.one(
            "SELECT * FROM approvals WHERE module=? AND kind=? AND peer IS ? AND payload=? AND status=?",
            (module, kind, peer, dumps(payload), PENDING),
        )
        if existing:
            return Approval.from_row(existing)
        new_id = self._store.execute(
            "INSERT INTO approvals (module, kind, peer, summary, payload, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (module, kind, peer, summary, dumps(payload), PENDING, time.time()),
        )
        return self.get(new_id)  # type: ignore[return-value]

    def get(self, approval_id: int) -> Approval | None:
        row = self._store.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        return Approval.from_row(row) if row else None

    def list(self, status: str | None = PENDING) -> list[Approval]:
        if status is None:
            rows = self._store.all("SELECT * FROM approvals ORDER BY id")
        else:
            rows = self._store.all("SELECT * FROM approvals WHERE status=? ORDER BY id", (status,))
        return [Approval.from_row(r) for r in rows]

    def decide(self, approval_id: int, decision: str) -> Approval:
        if decision not in (APPROVED, DENIED):
            raise ValueError("decision is approved or denied")
        changed = self._store.changes(
            "UPDATE approvals SET status=?, decided_at=? WHERE id=? AND status=?",
            (decision, time.time(), approval_id, PENDING),
        )
        if changed != 1:
            raise LookupError(f"no pending approval {approval_id}")
        return self.get(approval_id)  # type: ignore[return-value]
