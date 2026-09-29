# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""A shared intel feed between participants' agents, with provenance on every item.

**Push, not pull.** The publisher's agent delivers each post to each subscriber
as a new request. That puts the publisher's gateway presentation on the request
leg, which is the leg whose signature is known to survive a gateway-to-gateway
hop; a pulled reply would rest on response-leg signing, which is unproven there.

**The flow.**

1. Bob's owner: `feed subscribe alice`. Bob's agent records the request and
   sends `feed.subscribe` to Alice's.
2. Alice's agent applies her policy for Bob (`accept."feed.subscribe"` on
   the peer): `ask` (default) queues it for Alice to approve, `auto` accepts,
   `deny` refuses. On approval it sends `feed.subscribed` back.
3. Alice's owner: `feed post "…"`. The outbox delivers `feed.deliver` to every
   active subscriber, retrying until each acknowledges.
4. Bob's agent accepts a delivery only from a peer he subscribed to, only as
   that peer's own post (no relaying in v1), and stores it with its label.

**What the label on an item means.** See trust/provenance.py: gateway-verified
is *a caller whose gateway vouched for this agent identity delivered this*, not
"the content is signed".
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from a2a.types import AgentSkill

from ...core import envelope
from ...core.approvals import APPROVED
from ...core.capability import Arg, Capability, Context, Inbound, OwnerCommand, refuse
from ...core.untrusted import clean
from ...trust.provenance import attributable

MAX_BODY = 2000
MAX_TOPIC = 40
MAX_ID = 64
#: Items accepted per peer per minute. A misbehaving peer fills its own quota,
#: not your disk.
RATE_PER_MINUTE = 30


class FeedModule(Capability):
    name = "feed"

    def migrations(self) -> list[str]:
        return [
            """CREATE TABLE IF NOT EXISTS feed_subscribers (
                peer TEXT PRIMARY KEY, status TEXT NOT NULL, created_at REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS feed_subscriptions (
                peer TEXT PRIMARY KEY, status TEXT NOT NULL, created_at REAL NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS feed_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, direction TEXT NOT NULL,
                peer TEXT, topic TEXT, body TEXT NOT NULL, created_at TEXT, received_at REAL NOT NULL,
                label TEXT, reason TEXT, issuer TEXT, holder TEXT, hop TEXT,
                UNIQUE (direction, peer, item_id))""",
        ]

    def skills(self) -> list[AgentSkill]:
        return [
            AgentSkill(
                id="feed.subscribe",
                name="Subscribe to this feed",
                description="Ask to receive this participant's posts. Their owner decides; you are told with feed.subscribed.",
                tags=["feed", "intel"],
            ),
            AgentSkill(
                id="feed.deliver",
                name="Deliver a post",
                description=(
                    "Deliver one of your posts to a subscriber. Accepted only from peers this agent subscribed "
                    "to, and stored with a provenance label."
                ),
                tags=["feed", "intel"],
            ),
            AgentSkill(id="feed.subscribed", name="Subscription accepted", description="Your subscription was accepted.", tags=["feed"]),
            AgentSkill(id="feed.declined", name="Subscription declined", description="Your subscription was declined.", tags=["feed"]),
            AgentSkill(id="feed.unsubscribe", name="Unsubscribe", description="Stop receiving this feed.", tags=["feed"]),
        ]

    # --- inbound ------------------------------------------------------------

    async def handle(self, req: Inbound, ctx: Context) -> dict[str, Any]:
        peer = attributable(req.provenance, ctx.peers)
        if peer is None:
            return refuse(f"this agent cannot attribute your message to a peer it knows: {req.provenance.reason}")

        if req.skill == "feed.subscribe":
            return self._subscribe(peer.name, peer.policy("feed.subscribe"), req, ctx)
        if req.skill == "feed.subscribed":
            return self._set_subscription(ctx, peer.name, "active")
        if req.skill == "feed.declined":
            return self._set_subscription(ctx, peer.name, "declined")
        if req.skill == "feed.unsubscribe":
            ctx.store.execute("DELETE FROM feed_subscribers WHERE peer=?", (peer.name,))
            return {"ok": True, "status": "unsubscribed"}
        if req.skill == "feed.deliver":
            return self._receive(peer.name, req, ctx)
        return refuse(f"unknown skill {req.skill!r}")

    def _subscribe(self, peer: str, policy: str, req: Inbound, ctx: Context) -> dict[str, Any]:
        if policy == "deny":
            return refuse("this participant does not accept subscriptions from you")
        row = ctx.store.one("SELECT status FROM feed_subscribers WHERE peer=?", (peer,))
        if row and row["status"] == "active":
            ctx.outbox.enqueue(peer, envelope.body("feed.subscribed"))
            return {"ok": True, "status": "active"}
        if policy == "auto":
            self._activate(ctx, peer)
            return {"ok": True, "status": "active"}

        ctx.store.execute(
            "INSERT OR IGNORE INTO feed_subscribers (peer, status, created_at) VALUES (?, 'pending', ?)",
            (peer, time.time()),
        )
        approval = ctx.approvals.request(
            module=self.name,
            kind="subscribe",
            peer=peer,
            summary=f"{peer} asks to subscribe to your feed ({req.provenance.label})",
            payload={"peer": peer, "label": req.provenance.label, "reason": req.provenance.reason},
        )
        return {"ok": True, "status": "pending-approval", "approval": approval.id}

    def _activate(self, ctx: Context, peer: str) -> None:
        ctx.store.execute(
            "INSERT INTO feed_subscribers (peer, status, created_at) VALUES (?, 'active', ?) "
            "ON CONFLICT(peer) DO UPDATE SET status='active'",
            (peer, time.time()),
        )
        ctx.outbox.enqueue(peer, envelope.body("feed.subscribed"))

    def _set_subscription(self, ctx: Context, peer: str, status: str) -> dict[str, Any]:
        changed = ctx.store.changes("UPDATE feed_subscriptions SET status=? WHERE peer=?", (status, peer))
        if not changed:
            return refuse("this agent has not asked to subscribe to you")
        return {"ok": True, "status": status}

    def _receive(self, peer: str, req: Inbound, ctx: Context) -> dict[str, Any]:
        sub = ctx.store.one("SELECT status FROM feed_subscriptions WHERE peer=?", (peer,))
        if sub is None or sub["status"] not in ("requested", "active"):
            return refuse("this agent is not subscribed to you")

        item_id = req.payload.get("item_id")
        if not isinstance(item_id, str) or not item_id or len(item_id) > MAX_ID:
            return refuse("item_id is required")
        body = clean(req.payload.get("body"), MAX_BODY)
        if not body:
            return refuse("body is required")

        recent = ctx.store.one(
            "SELECT COUNT(*) AS n FROM feed_items WHERE direction='in' AND peer=? AND received_at>?",
            (peer, time.time() - 60),
        )
        if recent and recent["n"] >= RATE_PER_MINUTE:
            return refuse("rate limited; try again shortly", retry=True)

        p = req.provenance
        ctx.store.execute(
            "INSERT OR IGNORE INTO feed_items (item_id, direction, peer, topic, body, created_at, received_at, "
            "label, reason, issuer, holder, hop) VALUES (?, 'in', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                peer,
                clean(req.payload.get("topic"), MAX_TOPIC) or None,
                body,
                clean(req.payload.get("created_at"), 40) or None,
                time.time(),
                p.label,
                p.reason,
                p.issuer,
                p.holder,
                None if p.hop is None else str(p.hop),
            ),
        )
        return {"ok": True, "stored_as": p.label}

    # --- approvals ----------------------------------------------------------

    async def on_decision(self, approval, ctx: Context) -> dict[str, Any]:
        if approval.kind != "subscribe":
            return {"ok": True}
        peer = approval.payload.get("peer")
        if approval.status == APPROVED:
            self._activate(ctx, peer)
            return {"ok": True, "subscriber": peer, "status": "active"}
        ctx.store.execute("DELETE FROM feed_subscribers WHERE peer=?", (peer,))
        ctx.outbox.enqueue(peer, envelope.body("feed.declined"))
        return {"ok": True, "subscriber": peer, "status": "declined"}

    # --- owner --------------------------------------------------------------

    def owner_commands(self) -> list[OwnerCommand]:
        async def subscribe(ctx: Context, peer: str) -> dict[str, Any]:
            if ctx.peers.get(peer) is None:
                return refuse(f"no peer called {peer!r}")
            ctx.store.execute(
                "INSERT INTO feed_subscriptions (peer, status, created_at) VALUES (?, 'requested', ?) "
                "ON CONFLICT(peer) DO UPDATE SET status=CASE WHEN status='active' THEN 'active' ELSE 'requested' END",
                (peer, time.time()),
            )
            ctx.outbox.enqueue(peer, envelope.body("feed.subscribe"))
            return {"ok": True, "peer": peer, "status": "requested"}

        async def unsubscribe(ctx: Context, peer: str) -> dict[str, Any]:
            ctx.store.execute("DELETE FROM feed_subscriptions WHERE peer=?", (peer,))
            if ctx.peers.get(peer) is not None:
                ctx.outbox.enqueue(peer, envelope.body("feed.unsubscribe"))
            return {"ok": True, "peer": peer, "status": "unsubscribed"}

        async def post(ctx: Context, text: str, topic: str | None = None) -> dict[str, Any]:
            body = clean(text, MAX_BODY)
            if not body:
                return refuse("nothing to post")
            item_id = uuid.uuid4().hex
            created = datetime.now(timezone.utc).isoformat(timespec="seconds")
            topic = clean(topic, MAX_TOPIC) or None
            ctx.store.execute(
                "INSERT INTO feed_items (item_id, direction, peer, topic, body, created_at, received_at) "
                "VALUES (?, 'out', NULL, ?, ?, ?, ?)",
                (item_id, topic, body, created, time.time()),
            )
            subscribers = [r["peer"] for r in ctx.store.all("SELECT peer FROM feed_subscribers WHERE status='active'")]
            payload = envelope.body("feed.deliver", item_id=item_id, topic=topic, body=body, created_at=created)
            for peer in subscribers:
                ctx.outbox.enqueue(peer, payload)
            return {"ok": True, "item_id": item_id, "queued_for": subscribers}

        async def read(ctx: Context, verified_only: bool = False, limit: int = 20, peer: str | None = None) -> dict[str, Any]:
            sql = "SELECT * FROM feed_items WHERE direction='in'"
            params: list[Any] = []
            if verified_only:
                sql += " AND label='gateway-verified'"
            if peer:
                sql += " AND peer=?"
                params.append(peer)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(int(limit))
            return {"ok": True, "items": ctx.store.all(sql, params)}

        async def subscribers(ctx: Context) -> dict[str, Any]:
            return {"ok": True, "subscribers": ctx.store.all("SELECT * FROM feed_subscribers ORDER BY peer")}

        async def subscriptions(ctx: Context) -> dict[str, Any]:
            return {"ok": True, "subscriptions": ctx.store.all("SELECT * FROM feed_subscriptions ORDER BY peer")}

        return [
            OwnerCommand("subscribe", "Ask a peer to send you their posts.", subscribe, (Arg("peer", "peer name"),)),
            OwnerCommand("unsubscribe", "Stop receiving a peer's posts.", unsubscribe, (Arg("peer", "peer name"),)),
            OwnerCommand(
                "post",
                "Publish a post to every active subscriber.",
                post,
                (Arg("text", "what to post"), Arg("topic", "optional topic label", required=False, option=True)),
            ),
            OwnerCommand(
                "read",
                "Read posts delivered to you, newest first, with their labels.",
                read,
                (
                    Arg("verified_only", "only gateway-verified items", required=False, option=True, kind=bool, default=False),
                    Arg("limit", "how many", required=False, option=True, kind=int, default=20),
                    Arg("peer", "only from this peer", required=False, option=True),
                ),
            ),
            OwnerCommand("subscribers", "Who receives your posts.", subscribers),
            OwnerCommand("subscriptions", "Whose posts you receive.", subscriptions),
        ]
