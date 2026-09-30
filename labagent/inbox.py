# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""What arrives through your access point, kept for you to read.

Every A2A message that gets past the key gate lands here, cleaned, with the time
it arrived and its A2A ids. The agent answers `Received.` and does nothing else
with it: no rule acts on another agent's words. You read the inbox through your
owner tools.

Nothing here says who sent a message. The gateway vouches for the call reaching
you, not for what the caller wrote, so a name inside the message is only what
the sender says about itself, and it stays in the text where you can see it.

One exception: a message whose only text is `ping` is answered `pong` and not
stored. It is a liveness check, for you and for anyone checking your agent is up.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TextPart

from .core.store import Store
from .core.untrusted import clean

log = logging.getLogger("labagent.inbox")

#: Characters kept of one message. Longer ones are cut, and say so.
LIMIT = 4000
PING, PONG, RECEIVED = "ping", "pong", "Received."

TABLE = """CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT NOT NULL,
    message_id TEXT UNIQUE, context_id TEXT, text TEXT NOT NULL)"""


def text_of(parts: Any) -> str:
    """The words of a message: text parts as they are, data parts as JSON, and a
    note for anything else. Works on the SDK's models and on plain JSON."""
    out = []
    for part in parts or []:
        root = getattr(part, "root", part)
        get = root.get if isinstance(root, dict) else lambda k, r=root: getattr(r, k, None)
        kind = get("kind")
        if kind == "text":
            out.append(get("text") or "")
        elif kind == "data":
            out.append(json.dumps(get("data"), ensure_ascii=False, sort_keys=True))
        else:
            out.append(f"[a {kind or 'unknown'} part, not kept]")
    return "\n".join(out)


class Inbox:
    def __init__(self, store: Store) -> None:
        self._store = store
        store.migrate([TABLE])

    def add(self, text: str, *, message_id: str | None, context_id: str | None) -> bool:
        """Store one message. False if this message id was stored before: a
        redelivery is answered again but kept once."""
        return self._store.changes(
            "INSERT OR IGNORE INTO inbox (received_at, message_id, context_id, text) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), message_id, context_id, text),
        ) == 1

    def list(self, limit: int = 20, since_id: int | None = None) -> list[dict[str, Any]]:
        return self._store.all(
            "SELECT * FROM inbox WHERE id > ? ORDER BY id DESC LIMIT ?", (since_id or 0, limit)
        )

    def count(self) -> int:
        return self._store.one("SELECT count(*) AS n FROM inbox")["n"]


class Executor(AgentExecutor):
    def __init__(self, inbox: Inbox) -> None:
        self._inbox = inbox

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        message = context.message
        text = clean(text_of(message.parts if message else None), LIMIT)
        if text.lower() == PING:
            answer = PONG
        elif not text:
            answer = "Nothing to read: send a text part."
        else:
            if self._inbox.add(text, message_id=message.message_id, context_id=message.context_id):
                log.info("inbox: a message of %d characters", len(text))
            answer = RECEIVED
        await updater.complete(updater.new_agent_message([Part(root=TextPart(text=answer))]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Every exchange here completes in one turn; there is nothing to cancel.
        return None
