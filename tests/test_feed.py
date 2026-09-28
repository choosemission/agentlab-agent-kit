"""M2: the intel feed, end to end, through both stand-in gateways."""

from __future__ import annotations

import asyncio

from labagent.core import envelope

from .conftest import peer


async def subscribed(lab) -> None:
    """Bob subscribes to Alice; Alice approves; the confirmation reaches Bob."""
    await lab.owner(lab.bob, "POST", "/cmd/feed/subscribe", {"peer": "alice"})
    await lab.flush(lab.bob)
    pending = (await lab.owner(lab.alice, "GET", "/approvals"))["approvals"]
    assert [(a["kind"], a["peer"]) for a in pending] == [("subscribe", "bob")]
    assert "gateway-verified" in pending[0]["summary"]
    decided = await lab.owner(lab.alice, "POST", f"/approvals/{pending[0]['id']}/approve")
    assert decided["outcome"]["status"] == "active"
    await lab.flush(lab.alice)


def test_subscribe_post_read_with_labels(make_lab) -> None:
    lab = make_lab()

    async def go():
        await subscribed(lab)
        subs = (await lab.owner(lab.bob, "POST", "/cmd/feed/subscriptions"))["subscriptions"]
        assert [(s["peer"], s["status"]) for s in subs] == [("alice", "active")]

        posted = await lab.owner(lab.alice, "POST", "/cmd/feed/post", {"text": "Rate limits moved to 50/s", "topic": "ops"})
        assert posted["queued_for"] == ["bob"]
        assert await lab.flush(lab.alice) == 1

        items = (await lab.owner(lab.bob, "POST", "/cmd/feed/read", {}))["items"]
        assert len(items) == 1
        item = items[0]
        assert (item["peer"], item["body"], item["topic"]) == ("alice", "Rate limits moved to 50/s", "ops")
        assert item["label"] == "gateway-verified"
        assert item["issuer"] == lab.alice_gw.gateway_did
        assert item["item_id"] == posted["item_id"]

    asyncio.run(go())


def test_an_unsubscribed_sender_is_refused(make_lab) -> None:
    """Alice pushes to Bob, who never asked for her feed."""
    lab = make_lab()

    async def go():
        lab.alice.ctx.outbox.enqueue("bob", envelope.body("feed.deliver", item_id="x1", body="unsolicited"))
        assert await lab.flush(lab.alice) == 0
        row = lab.alice.ctx.outbox.list()[0]
        assert row["status"] == "failed" and "not subscribed" in row["last_error"]
        assert (await lab.owner(lab.bob, "POST", "/cmd/feed/read", {}))["items"] == []

    asyncio.run(go())


def test_an_unverified_subscriber_is_refused_by_default(make_lab) -> None:
    lab = make_lab(bob_signs=False)

    async def go():
        await lab.owner(lab.bob, "POST", "/cmd/feed/subscribe", {"peer": "alice"})
        await lab.flush(lab.bob)
        assert (await lab.owner(lab.alice, "GET", "/approvals"))["approvals"] == []
        assert "cannot attribute" in lab.bob.ctx.outbox.list()[0]["last_error"]

    asyncio.run(go())


def test_self_asserted_items_are_stored_and_labelled_when_the_peer_allows_it(make_lab) -> None:
    """Bob opted in to Alice's unsigned messages; her gateway attaches nothing.
    Delivery works — and every item says so."""
    lab = make_lab(alice_signs=False, bob_peers=None)
    lab.bob.ctx.peers._by_name["alice"] = peer("alice", lab.alice_gw, accept_self_asserted=True)

    async def go():
        await subscribed(lab)
        await lab.owner(lab.alice, "POST", "/cmd/feed/post", {"text": "unsigned but welcome"})
        await lab.flush(lab.alice)
        items = (await lab.owner(lab.bob, "POST", "/cmd/feed/read", {}))["items"]
        assert items[0]["label"] == "self-asserted"
        assert "no gateway presentation" in items[0]["reason"]
        verified = (await lab.owner(lab.bob, "POST", "/cmd/feed/read", {"verified_only": True}))["items"]
        assert verified == []

    asyncio.run(go())


def test_the_outbox_retries_across_a_restart(make_lab) -> None:
    """Bob is down when Alice posts. Alice's agent restarts. Bob comes back.
    The post arrives once."""
    lab = make_lab()

    async def go():
        await subscribed(lab)
        lab.network.down.add("bob.test")
        await lab.owner(lab.alice, "POST", "/cmd/feed/post", {"text": "while you were out"})
        assert await lab.flush(lab.alice) == 0
        row = lab.alice.ctx.outbox.list("pending")[0]
        assert row["attempts"] == 1 and "unreachable" in row["last_error"]

        lab.restart("alice")
        lab.restart("bob")
        lab.network.down.clear()
        assert await lab.flush(lab.alice) == 1
        assert await lab.flush(lab.alice) == 0  # nothing left to send

        items = (await lab.owner(lab.bob, "POST", "/cmd/feed/read", {}))["items"]
        assert [i["body"] for i in items] == ["while you were out"]

    asyncio.run(go())


def test_a_redelivered_item_is_stored_once(make_lab) -> None:
    lab = make_lab()

    async def go():
        await subscribed(lab)
        payload = envelope.body("feed.deliver", item_id="same", body="once")
        for _ in range(2):
            lab.alice.ctx.outbox.enqueue("bob", payload)
        assert await lab.flush(lab.alice) == 2
        items = (await lab.owner(lab.bob, "POST", "/cmd/feed/read", {}))["items"]
        assert len(items) == 1

    asyncio.run(go())


def test_counterparty_text_is_cleaned(make_lab) -> None:
    lab = make_lab()

    async def go():
        await subscribed(lab)
        sneaky = "safe‮ txet desrever\x07 " + "x" * 5000
        lab.alice.ctx.outbox.enqueue("bob", envelope.body("feed.deliver", item_id="s", body=sneaky))
        await lab.flush(lab.alice)
        body = (await lab.owner(lab.bob, "POST", "/cmd/feed/read", {}))["items"][0]["body"]
        assert "‮" not in body and "\x07" not in body and len(body) <= 2000

    asyncio.run(go())


def test_a_denied_subscriber_is_told(make_lab) -> None:
    lab = make_lab()

    async def go():
        await lab.owner(lab.bob, "POST", "/cmd/feed/subscribe", {"peer": "alice"})
        await lab.flush(lab.bob)
        approval = (await lab.owner(lab.alice, "GET", "/approvals"))["approvals"][0]
        await lab.owner(lab.alice, "POST", f"/approvals/{approval['id']}/deny")
        await lab.flush(lab.alice)
        subs = (await lab.owner(lab.bob, "POST", "/cmd/feed/subscriptions"))["subscriptions"]
        assert subs[0]["status"] == "declined"
        # And Alice's posts do not go to him.
        posted = await lab.owner(lab.alice, "POST", "/cmd/feed/post", {"text": "hello"})
        assert posted["queued_for"] == []

    asyncio.run(go())
