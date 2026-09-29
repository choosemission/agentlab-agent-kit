# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""What a capability module is: the one interface the toolkit grows through.

A module is a small, self-contained thing an agent can do for its owner — keep
a feed, negotiate a meeting. It contributes four things:

- **skills** for the agent card, named `<module>.<action>`, which is also how an
  inbound message is routed to it;
- **tables**, prefixed with its name, in the shared SQLite store;
- **a handler** for inbound messages from peers, which returns a structured
  reply (an acknowledgement, never a commitment: see trust/provenance.py);
- **owner commands**, which the owner MCP server and the CLI expose to the person the
  agent represents, and an `on_decision` hook for items it queued for them.

The core does the rest: authentication, provenance, dedupe, audit, the approval
queue and the outbox. A module never sees a message whose shape it did not ask
for, and never gets an outbound address from anywhere but the owner's peer table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from a2a.types import AgentSkill

if TYPE_CHECKING:
    from ..config import Config
    from ..peers import Peers
    from ..trust.provenance import Provenance
    from .approvals import Approval, Approvals
    from .outbound import Outbound
    from .outbox import Outbox
    from .store import Store


@dataclass(frozen=True)
class Inbound:
    skill: str
    payload: dict[str, Any]
    provenance: "Provenance"
    message_id: str
    context_id: str | None = None
    task_id: str | None = None


@dataclass
class Context:
    config: "Config"
    store: "Store"
    peers: "Peers"
    approvals: "Approvals"
    outbound: "Outbound"
    outbox: "Outbox"


@dataclass(frozen=True)
class Arg:
    name: str
    help: str
    required: bool = True
    #: A `--name` option rather than a positional argument.
    option: bool = False
    #: `bool` for a switch, `int` for a number, `str` otherwise.
    kind: type = str
    default: Any = None


@dataclass(frozen=True)
class OwnerCommand:
    name: str
    help: str
    run: Callable[..., Awaitable[dict[str, Any]]]
    args: tuple[Arg, ...] = field(default_factory=tuple)


def refuse(error: str, **extra: Any) -> dict[str, Any]:
    """A structured no. The outbox treats it as an answer, not a failure to retry."""
    return {"ok": False, "error": error, **extra}


class Capability:
    name: str = ""
    version: str = "1"

    def skills(self) -> list[AgentSkill]:
        return []

    def migrations(self) -> list[str]:
        return []

    def handles(self, skill: str) -> bool:
        return skill == self.name or skill.startswith(self.name + ".")

    async def handle(self, req: Inbound, ctx: Context) -> dict[str, Any]:
        return refuse(f"{self.name} accepts no inbound messages")

    async def on_decision(self, approval: "Approval", ctx: Context) -> dict[str, Any]:
        return {"ok": True}

    def owner_commands(self) -> list[OwnerCommand]:
        return []
