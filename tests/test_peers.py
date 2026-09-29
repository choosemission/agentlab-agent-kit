"""M5: peers are the owner's runtime data, and each is reached through the gateway or directly."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from labagent.core.store import Store
from labagent.peers import Peer, Peers, PeersError

from .conftest import BOB_KEY, make_agent, peer
from .test_ping import ping


def owner(lab, agent, tool, arguments=None) -> dict:
    return asyncio.run(lab.owner(agent, tool, arguments))


class TestTable:
    def table(self, allow_http: bool = False) -> Peers:
        return Peers(Store(":memory:"), allow_http=allow_http)

    def test_only_https_unless_the_harness_says_otherwise(self) -> None:
        with pytest.raises(PeersError, match="https"):
            self.table().add(Peer("bob", "http://bob.example/"))
        self.table(allow_http=True).add(Peer("bob", "http://bob.example/"))

    @pytest.mark.parametrize(
        "bad, match",
        [
            (Peer("bob", "ftp://bob.example/"), "https"),
            (Peer("bob", "https:///no-host"), "https"),
            (Peer("bob", "https://user:pw@bob.example/"), "credentials"),
            (Peer("../bob", "https://bob.example/"), "peer name"),
            (Peer("bob", "https://bob.example/", mode="sideways"), "mode"),
            (Peer("bob", "https://bob.example/", api_key_env="$(rm -rf)"), "api_key_env"),
            (Peer("bob", "https://bob.example/", gateway_did="bob-gateway"), "DID"),
            (Peer("bob", "https://bob.example/", accept={"feed.subscribe": "yes"}), "policies"),
        ],
    )
    def test_fields_are_checked(self, bad, match) -> None:
        with pytest.raises(PeersError, match=match):
            self.table().add(bad)

    def test_one_gateway_one_peer(self) -> None:
        table = self.table()
        table.add(Peer("bob", "https://a.example/", gateway_did="did:web:bob"))
        with pytest.raises(PeersError, match="already pinned for bob"):
            table.add(Peer("robert", "https://b.example/", gateway_did="did:web:bob"))
        table.add(Peer("carol", "https://c.example/"))
        with pytest.raises(PeersError, match="already pinned for bob"):
            table.update("carol", gateway_did="did:web:bob")

    def test_names_are_permanent_and_unique(self) -> None:
        table = self.table()
        table.add(Peer("bob", "https://a.example/"))
        with pytest.raises(PeersError, match="already a peer"):
            table.add(Peer("bob", "https://b.example/"))
        with pytest.raises(PeersError, match="permanent"):
            table.update("bob", name="robert")

    def test_an_empty_string_unpins(self) -> None:
        table = self.table()
        table.add(Peer("bob", "https://a.example/", gateway_did="did:web:bob"))
        assert table.update("bob", gateway_did="").gateway_did is None
        assert table.by_gateway_did("did:web:bob") is None

    def test_the_seed_adds_and_never_overwrites(self) -> None:
        table = self.table()
        table.seed([Peer("bob", "https://seed.example/")])
        table.update("bob", url="https://changed.example/")
        assert table.seed([Peer("bob", "https://seed.example/"), Peer("carol", "https://c.example/")]) == ["carol"]
        assert table.get("bob").url == "https://changed.example/"


def test_the_owner_adds_a_peer_and_it_survives_a_restart(make_lab) -> None:
    lab = make_lab()
    added = owner(lab, lab.alice, "peers_add", {"name": "carol", "url": "https://carol-gw.example/a2a/", "mode": "direct"})
    assert added["ok"] and added["peer"]["mode"] == "direct" and added["peer"]["key_set"] is False
    lab.restart("alice")
    names = [p["name"] for p in owner(lab, lab.alice, "peers")["peers"]]
    assert names == ["bob", "carol"]


def test_a_bad_peer_is_refused_not_stored(make_lab) -> None:
    lab = make_lab()
    answer = owner(lab, lab.alice, "peers_add", {"name": "carol", "url": "https://x.example/", "mode": "sideways"})
    assert answer["ok"] is False and "mode" in answer["error"]
    assert lab.alice.ctx.peers.get("carol") is None


def test_the_owner_pins_a_peer_without_a_restart(make_lab) -> None:
    """Bob knows Alice only by name. The reason on her ping names her gateway;
    Bob pins it, and her next ping is gateway-verified."""
    lab = make_lab(bob_peers=[Peer(name="alice", url="http://bob-gw.test/to/alice", claimed_name="Alice's agent")])
    first = ping(lab, "alice", "bob")["reply"]
    assert first["seen_as"] == "self-asserted"
    assert "none pinned" in first["reason"]

    assert owner(lab, lab.bob, "peers_pin", {"name": "alice", "gateway_did": first["issuer"]})["ok"]
    second = ping(lab, "alice", "bob")["reply"]
    assert second["seen_as"] == "gateway-verified" and second["peer"] == "alice"


def test_removing_a_peer_fails_what_was_queued_for_them(make_lab) -> None:
    lab = make_lab()
    lab.alice.ctx.outbox.enqueue("bob", {"skill": "ping", "v": 1})
    assert owner(lab, lab.alice, "peers_remove", {"name": "bob"})["ok"]
    asyncio.run(lab.flush(lab.alice))
    row = lab.alice.ctx.outbox.list()[0]
    assert row["status"] == "failed" and row["last_error"] == "bob is no longer a peer"
    assert owner(lab, lab.alice, "peers_remove", {"name": "bob"})["ok"] is False


def test_a_policy_set_by_the_owner_applies_to_the_next_request(make_lab) -> None:
    lab = make_lab()
    assert owner(lab, lab.alice, "peers_accept", {"name": "bob", "action": "feed.subscribe", "policy": "auto"})["ok"]
    owner(lab, lab.bob, "feed_subscribe", {"peer": "alice"})
    asyncio.run(lab.flush(lab.bob))
    assert owner(lab, lab.alice, "approvals")["approvals"] == []
    assert owner(lab, lab.alice, "feed_subscribers")["subscribers"][0]["status"] == "active"


class TestModes:
    def test_through_the_gateway_bob_sees_alice_as_gateway_verified(self, make_lab) -> None:
        result = ping(make_lab(), "alice", "bob")
        assert result["mode"] == "gateway"
        assert result["reply"]["seen_as"] == "gateway-verified"

    def test_direct_bob_sees_alice_as_self_asserted(self, make_lab, monkeypatch) -> None:
        """Alice calls Bob's agent at its own address, with the key Bob's door
        wants. Nothing signs it on the way, and Bob says so."""
        monkeypatch.setenv("ALICE_TO_BOB_KEY", BOB_KEY)
        lab = make_lab()
        added = owner(
            lab,
            lab.alice,
            "peers_add",
            {"name": "bob-direct", "url": "http://bob.test/", "mode": "direct", "api_key_env": "ALICE_TO_BOB_KEY"},
        )
        assert added["peer"]["key_set"] is True
        result = ping(lab, "alice", "bob-direct")
        assert result["mode"] == "direct"
        assert result["reply"]["seen_as"] == "self-asserted"
        assert "no gateway presentation" in result["reply"]["reason"]

    def test_the_seed_file_sets_the_mode(self, make_lab, tmp_path) -> None:
        lab = make_lab()
        seed = tmp_path / "peers.toml"
        seed.write_text('[peers.dave]\nurl = "https://dave.example/"\nmode = "direct"\n')
        cfg = replace(lab.alice.ctx.config, db_path=str(tmp_path / "fresh.sqlite3"), peers_path=str(seed))
        agent = make_agent(cfg, None, lab)
        assert agent.ctx.peers.get("dave").mode == "direct"
