# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""The smallest capability, and the first thing to run between two agents.

`ping` answers with how the caller was labelled on arrival — gateway-verified or
self-asserted, and why. It commits nobody to anything, so it needs no approval,
and it is the quickest way to check a peer's route and your pinned DID before
anything that matters depends on them.
"""

from __future__ import annotations

from typing import Any

from a2a.types import AgentSkill

from ...core import envelope
from ...core.capability import Arg, Capability, Context, Inbound, OwnerCommand, refuse
from ...core.outbound import OutboundError


class PingModule(Capability):
    name = "ping"

    def skills(self) -> list[AgentSkill]:
        return [
            AgentSkill(
                id="ping",
                name="Ping",
                description=(
                    "Replies with how your call was labelled on arrival: gateway-verified (your gateway's "
                    "presentation verified and matches the DID pinned for you) or self-asserted, and why."
                ),
                tags=["diagnostics", "identity"],
            )
        ]

    async def handle(self, req: Inbound, ctx: Context) -> dict[str, Any]:
        p = req.provenance
        return {
            "ok": True,
            "skill": "ping.pong",
            "seen_as": p.label,
            "reason": p.reason,
            "peer": p.peer or p.claimed_peer,
            "issuer": p.issuer,
        }

    def owner_commands(self) -> list[OwnerCommand]:
        async def ping(ctx: Context, peer: str) -> dict[str, Any]:
            target = ctx.peers.get(peer)
            if target is None:
                return refuse(f"no peer called {peer!r}")
            try:
                reply = await ctx.outbound.send(target, envelope.body("ping"))
            except OutboundError as error:
                return refuse(str(error))
            return {"ok": True, "peer": peer, "mode": target.mode, "reply": reply}

        return [OwnerCommand("ping", "Ping a peer and see how it labelled you.", ping, (Arg("peer", "peer name"),))]
