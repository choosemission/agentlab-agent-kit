# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Messages that must arrive, even if the peer is down when they are sent.

Participants' agents run on laptops and small VMs; somebody's will be off. A
feed post or a meeting confirmation goes into the outbox, in the same SQLite
file as everything else, and a worker retries it with backoff until the peer
acknowledges it or it runs out of attempts. It survives a restart of either side.

Delivery is at-least-once. Receivers dedupe: on the A2A message id, and on the
module's own ids (a feed item id, a meeting id) where a retry mints a new message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from ..peers import Peers
from .outbound import Outbound, OutboundError
from .store import Store, dumps

log = logging.getLogger("labagent.outbox")

PENDING, DELIVERED, FAILED = "pending", "delivered", "failed"
MAX_ATTEMPTS = 30
MAX_BACKOFF = 600.0


def backoff(attempts: int) -> float:
    return min(MAX_BACKOFF, 2.0 ** min(attempts, 10))


class Outbox:
    def __init__(
        self,
        store: Store,
        peers: Peers,
        outbound: Outbound,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._peers = peers
        self._outbound = outbound
        self._now = now
        #: Called with (peer name, sent payload, reply payload) on delivery.
        self.on_delivered: list[Callable[[str, dict, dict], None]] = []

    def enqueue(self, peer: str, payload: dict[str, Any]) -> int:
        now = self._now()
        return self._store.execute(
            "INSERT INTO outbox (peer, body, status, attempts, next_at, created_at) VALUES (?, ?, ?, 0, ?, ?)",
            (peer, dumps(payload), PENDING, now, now),
        )

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self._store.all("SELECT * FROM outbox WHERE status=? ORDER BY id", (status,))
        return self._store.all("SELECT * FROM outbox ORDER BY id")

    async def run_once(self) -> int:
        """Attempt every due message once. Returns how many were delivered."""
        due = self._store.all(
            "SELECT * FROM outbox WHERE status=? AND next_at<=? ORDER BY id", (PENDING, self._now())
        )
        delivered = 0
        for row in due:
            payload = json.loads(row["body"])
            peer = self._peers.get(row["peer"])
            attempts = row["attempts"] + 1
            if peer is None:
                self._fail(row["id"], attempts, f"{row['peer']} is no longer in peers.toml", final=True)
                continue
            try:
                reply = await self._outbound.send(peer, payload)
            except OutboundError as error:
                self._fail(row["id"], attempts, str(error), final=attempts >= MAX_ATTEMPTS)
                continue
            if reply.get("ok") is False and not reply.get("retry"):
                # A refusal is an answer. Retrying it would be pestering.
                self._fail(row["id"], attempts, f"refused: {reply.get('error')}", final=True)
                continue
            self._store.execute(
                "UPDATE outbox SET status=?, attempts=?, delivered_at=?, last_error=NULL WHERE id=?",
                (DELIVERED, attempts, self._now(), row["id"]),
            )
            delivered += 1
            for callback in self.on_delivered:
                callback(peer.name, payload, reply)
        return delivered

    def _fail(self, row_id: int, attempts: int, error: str, *, final: bool) -> None:
        log.info("outbox delivery failed: %s (attempt %d%s)", error, attempts, ", giving up" if final else "")
        self._store.execute(
            "UPDATE outbox SET status=?, attempts=?, next_at=?, last_error=? WHERE id=?",
            (FAILED if final else PENDING, attempts, self._now() + backoff(attempts), error, row_id),
        )

    async def run_forever(self, interval: float) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:  # never let one bad row stop the worker
                log.exception("outbox pass failed")
            await asyncio.sleep(interval)
