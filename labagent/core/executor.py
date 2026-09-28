# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Every inbound message, in the same order: parse, label, dedupe, dispatch, audit.

The order is the design. A module is handed a payload whose shape was checked
and a provenance label computed before it runs, so no module can forget to ask
who is calling. Every message, including refusals, leaves an audit row.
"""

from __future__ import annotations

import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Part

from ..trust.identity import SELF_ASSERTED_EXTENSION
from ..trust.provenance import assess
from ..trust.verify import Verifier
from .capability import Context, Inbound, refuse
from .envelope import EnvelopeError, parse_parts
from .registry import Registry

log = logging.getLogger("labagent.executor")


class Executor(AgentExecutor):
    def __init__(self, ctx: Context, registry: Registry, verifier: Verifier | None, identity: dict[str, str]) -> None:
        self._ctx = ctx
        self._registry = registry
        self._verifier = verifier
        self._identity = identity

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        result, skill, provenance = await self._handle(message)

        reply = updater.new_agent_message(
            [Part(root=DataPart(data=result))],
            metadata={SELF_ASSERTED_EXTENSION: {"agentIdentity": dict(self._identity)}},
        )
        reply.extensions = [SELF_ASSERTED_EXTENSION]
        self._ctx.store.audit(
            skill=skill,
            provenance=provenance,
            outcome="ok" if result.get("ok", True) else str(result.get("error"))[:200],
        )
        await updater.complete(reply)

    async def _handle(self, message: Any) -> tuple[dict[str, Any], str | None, dict[str, Any]]:
        if message is None:
            return refuse("no message"), None, {}
        try:
            payload = parse_parts(message.parts)
        except EnvelopeError as error:
            return refuse(str(error)), None, {}

        skill = payload["skill"]
        provenance = assess(message.metadata, self._verifier, self._ctx.peers)
        seen = provenance.to_json()

        module = self._registry.route(skill)
        if module is None:
            return refuse(f"unknown skill {skill!r}"), skill, seen
        if not self._ctx.store.first_sight(message.message_id):
            return {"ok": True, "duplicate": True}, skill, seen

        inbound = Inbound(
            skill=skill,
            payload=payload,
            provenance=provenance,
            message_id=message.message_id,
            context_id=message.context_id,
            task_id=message.task_id,
        )
        try:
            result = await module.handle(inbound, self._ctx)
        except Exception:
            log.exception("module %s failed on %s", module.name, skill)
            result = refuse("the agent failed handling this; try again later", retry=True)
        return result, skill, seen

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Every exchange here completes in one turn; there is nothing to cancel.
        return None
