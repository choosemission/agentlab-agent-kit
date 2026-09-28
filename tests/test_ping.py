"""M1: two agents, through two stand-in gateways, labelling each other."""

from __future__ import annotations

import asyncio

from labagent.trust.testing import TestGateway

from .conftest import peer


def ping(lab, frm: str, to: str) -> dict:
    agent = getattr(lab, frm)
    return asyncio.run(lab.owner(agent, "POST", "/cmd/ping/ping", {"peer": to}))


def test_alice_and_bob_see_each_other_as_gateway_verified(make_lab) -> None:
    lab = make_lab()
    there = ping(lab, "alice", "bob")["reply"]
    back = ping(lab, "bob", "alice")["reply"]
    assert there["seen_as"] == "gateway-verified" and there["peer"] == "alice"
    assert back["seen_as"] == "gateway-verified" and back["peer"] == "bob"
    assert there["issuer"] == lab.alice_gw.gateway_did


def test_a_stripped_proof_is_self_asserted(make_lab) -> None:
    """Alice's gateway attaches nothing: her agent's name arrives, unsigned."""
    reply = ping(make_lab(alice_signs=False), "alice", "bob")["reply"]
    assert reply["seen_as"] == "self-asserted"
    assert "no gateway presentation" in reply["reason"]
    assert reply["peer"] == "alice"  # claimed, by name — and labelled as a claim


def test_a_wrong_pinned_did_is_self_asserted(make_lab) -> None:
    """Bob pinned some other gateway for Alice. Her proofs verify, and it still
    is not her as far as Bob is concerned."""
    wrong = TestGateway("not-alice", "elsewhere.test")
    reply = ping(make_lab(bob_peers=[peer("alice", wrong)]), "alice", "bob")["reply"]
    assert reply["seen_as"] == "self-asserted"
    assert "not the DID pinned for alice" in reply["reason"]


def test_every_inbound_message_is_audited(make_lab) -> None:
    lab = make_lab()
    ping(lab, "alice", "bob")
    rows = asyncio.run(lab.owner(lab.bob, "GET", "/audit"))["audit"]
    assert rows[0]["skill"] == "ping" and rows[0]["label"] == "gateway-verified" and rows[0]["peer"] == "alice"


def test_an_unknown_peer_is_refused_by_the_owner_command(make_lab) -> None:
    lab = make_lab()
    assert ping(lab, "alice", "carol")["ok"] is False
