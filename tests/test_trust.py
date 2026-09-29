"""The verifier against the stand-in gateway, and the label rule on its own."""

from __future__ import annotations

import json

import pytest

from labagent.peers import Peer, Peers
from labagent.trust.identity import GATEWAY_EXTENSIONS, SELF_ASSERTED_EXTENSION
from labagent.trust.provenance import SELF_ASSERTED, VERIFIED, assess, attributable
from labagent.trust.resolve import Resolver
from labagent.trust.testing import TestGateway, fetcher_for
from labagent.trust.verify import VerificationError, Verifier

GW = TestGateway("alice", "alice-gw.test")
OTHER = TestGateway("mallory", "mallory-gw.test")


def verifier(*, strict: bool = False, gateways=(GW, OTHER)) -> Verifier:
    return Verifier(Resolver(fetcher=fetcher_for(*gateways), strict_purpose=strict))


def metadata(gateway: TestGateway | None = GW, name: str = "Alice's agent") -> dict:
    message = {"metadata": {SELF_ASSERTED_EXTENSION: {"agentIdentity": {"name": name}}}}
    return (gateway.attach(message) if gateway else message)["metadata"]


PEERS = Peers.of([Peer(name="alice", url="http://x/", gateway_did=GW.gateway_did, claimed_name="Alice's agent")])


class TestStandInGateway:
    def test_its_presentations_verify(self) -> None:
        checked = verifier().verify(GW.presentation({"agentIdentity.name": "Alice's agent"}))
        assert checked.issuer == GW.gateway_did and checked.holder == GW.surface_did

    def test_it_reproduces_the_key2_gap_that_strict_purpose_rejects(self) -> None:
        """No Affinidi gateway credential passes a strict proof-purpose check
        (CLAIMS.md). The stand-in reproduces that, so the flag is exercised."""
        with pytest.raises(VerificationError, match="not listed under assertionMethod"):
            verifier(strict=True).verify(GW.presentation({"x": 1}))
        fixed = TestGateway("alice", "alice-gw.test", authorise_key2=True)
        verifier(strict=True, gateways=(fixed,)).verify(fixed.presentation({"x": 1}))

    def test_an_edited_identity_field_fails(self) -> None:
        vp = GW.presentation({"agentIdentity.name": "Alice's agent"})
        vp["verifiableCredential"]["credentialSubject"]["identityFields"] = {"agentIdentity.name": "Bob"}
        with pytest.raises(VerificationError, match="does not verify"):
            verifier().verify(vp)


class TestLabel:
    def test_verified_and_pinned(self) -> None:
        p = assess(metadata(), verifier(), PEERS)
        assert p.label == VERIFIED and p.peer == "alice" and p.issuer == GW.gateway_did
        assert attributable(p, PEERS).name == "alice"

    def test_no_presentation_is_self_asserted_even_with_the_right_name(self) -> None:
        p = assess(metadata(None), verifier(), PEERS)
        assert p.label == SELF_ASSERTED and p.claimed_peer == "alice"
        assert "no gateway presentation" in p.reason
        assert attributable(p, PEERS) is None  # not without the peer's opt-in

    def test_a_verified_but_unpinned_gateway_is_self_asserted(self) -> None:
        """Mallory's gateway signs genuinely, and claims to be Alice."""
        p = assess(metadata(OTHER, name="Alice's agent"), verifier(), PEERS)
        assert p.label == SELF_ASSERTED
        assert "not the DID pinned for alice" in p.reason
        assert p.issuer == OTHER.gateway_did

    def test_a_forged_presentation_is_self_asserted(self) -> None:
        meta = metadata()
        vp = json.loads(meta[GATEWAY_EXTENSIONS[0]]["verifiablePresentation"])
        vp["verifiableCredential"]["issuer"] = OTHER.gateway_did
        meta[GATEWAY_EXTENSIONS[0]]["verifiablePresentation"] = json.dumps(vp)
        p = assess(meta, verifier(), PEERS)
        assert p.label == SELF_ASSERTED and "did not verify" in p.reason

    def test_verification_off_never_verifies(self) -> None:
        p = assess(metadata(), None, PEERS)
        assert p.label == SELF_ASSERTED and "switched off" in p.reason

    def test_self_assertion_is_attributable_only_with_the_peers_opt_in(self) -> None:
        lenient = Peers.of([Peer(name="alice", url="http://x/", claimed_name="Alice's agent", accept_self_asserted=True)])
        p = assess(metadata(None), verifier(), lenient)
        assert p.label == SELF_ASSERTED
        assert attributable(p, lenient).name == "alice"
