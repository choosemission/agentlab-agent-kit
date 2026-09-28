"""Verification, against a presentation a real gateway really signed.

**Vendored from affinidi-lab `servers/lab-coordinator/tests/test_verify.py`,
unchanged but for imports and where the fixtures are read from.** The captured
envelope is not copied into this public repository: it names real Lab gateway
hosts, and its signatures cover them, so it cannot be scrubbed without breaking
it. Point LABAGENT_CAPTURED_FIXTURES at a directory holding the capture (by
default the sibling affinidi-lab checkout); without one these tests skip, and
`test_trust.py` still exercises the same verifier against the stand-in gateway.

`tests/fixtures/presentation.json` is an envelope that arrived across the
gateway-to-gateway hop on 16 September 2026, unedited — real DIDs, real
signatures, both proofs good. The two DID logs beside it were fetched from those
gateways the same day. It is the same sample
`docs/evidence/gateway-presentation-proof/` checks with a second implementation.

That is the point of the fixtures: nothing here is a mock of what a proof might
look like. If Affinidi changes how the gateway signs, these tests fail, which is
the only way this server finds out before a participant does.

Offline: the resolver's fetcher is replaced with one that reads the captured
logs. What is exercised is the canonicalization, the hashing, the signature and
the key lookup — everything but the HTTPS request.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from labagent.trust.resolve import ResolutionError, Resolver, log_url, multikey_to_public_bytes
from labagent.trust.verify import VerificationError, Verifier

FIXTURES = Path(
    os.environ.get("LABAGENT_CAPTURED_FIXTURES")
    or Path(__file__).resolve().parents[2] / "affinidi-lab/servers/lab-coordinator/tests/fixtures"
)

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "presentation.json").exists(),
    reason="captured gateway fixtures not available; set LABAGENT_CAPTURED_FIXTURES",
)
def _captured() -> tuple[str, str, dict[str, str]]:
    """The DIDs and log URLs, read from the capture rather than written here, so
    no real gateway host is committed to this repository."""
    if not (FIXTURES / "presentation.json").exists():
        return "", "", {}
    vp = json.loads((FIXTURES / "presentation.json").read_text())
    logs = json.loads((FIXTURES / "did-logs.json").read_text())
    by_file = {name: url for url, name in logs.items()}
    return vp["verifiableCredential"]["issuer"], vp["holder"], by_file


GATEWAY_DID, SURFACE_DID, LOG_URLS = _captured()

#: While the captured credential is inside its validity window. It runs to
#: September 2027, so this only matters when somebody runs the suite later.
WHEN = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)


def presentation() -> dict:
    return json.loads((FIXTURES / "presentation.json").read_text())


def offline_fetcher(swap: dict[str, str] | None = None):
    """Serves the captured DID logs and nothing else — a test that reached the
    network would be testing somebody else's uptime."""
    index = json.loads((FIXTURES / "did-logs.json").read_text())

    def fetch(url: str) -> bytes:
        name = (swap or {}).get(url) or index.get(url)
        if name is None:
            raise ResolutionError(f"no captured DID log for {url}")
        return (FIXTURES / name).read_bytes()

    return fetch


@pytest.fixture
def verifier() -> Verifier:
    return Verifier(Resolver(fetcher=offline_fetcher()))


class TestARealPresentation:
    def test_both_proofs_verify(self, verifier: Verifier) -> None:
        verified = verifier.verify(presentation(), now=WHEN)
        assert verified.issuer == GATEWAY_DID
        assert verified.holder == SURFACE_DID
        assert verified.identity_fields == {"agentIdentity.name": "A2A Test Client"}
        assert verified.presentation_proof is True

    def test_a_tampered_presentation_proof_is_refused(self, verifier: Verifier) -> None:
        """Wrapping somebody else's credential in an envelope of your own."""
        tampered = presentation()
        tampered["id"] = "urn:uuid:00000000-0000-4000-8000-000000000000"
        with pytest.raises(VerificationError, match="does not verify"):
            verifier.verify(tampered, now=WHEN)

    def test_a_changed_identity_field_fails(self, verifier: Verifier) -> None:
        """The whole purpose. Editing what the gateway signed must not survive."""
        tampered = presentation()
        tampered["verifiableCredential"]["credentialSubject"]["identityFields"] = {
            "agentIdentity.name": "Somebody Else"
        }
        with pytest.raises(VerificationError, match="does not verify"):
            verifier.verify(tampered, now=WHEN)

    def test_a_swapped_issuer_fails(self, verifier: Verifier) -> None:
        """Claiming another organisation's gateway: the directory key itself."""
        tampered = presentation()
        tampered["verifiableCredential"]["issuer"] = "did:webvh:QmSomebodyElse:example.test"
        with pytest.raises(VerificationError, match="not"):
            verifier.verify(tampered, now=WHEN)

    def test_a_credential_presented_by_a_different_holder_fails(self, verifier: Verifier) -> None:
        tampered = presentation()
        tampered["holder"] = "did:webvh:QmSomeoneElse:example.test:surface:1234"
        with pytest.raises(VerificationError, match="issued to"):
            verifier.verify(tampered, now=WHEN)

    def test_an_expired_credential_fails(self, verifier: Verifier) -> None:
        with pytest.raises(VerificationError, match="expired"):
            verifier.verify(presentation(), now=datetime(2028, 1, 1, tzinfo=timezone.utc))

    def test_an_unimplemented_cryptosuite_is_refused_not_attempted(self, verifier: Verifier) -> None:
        tampered = presentation()
        tampered["verifiableCredential"]["proof"]["cryptosuite"] = "bbs-2023"
        with pytest.raises(VerificationError, match="verifies eddsa-rdfc-2022 only"):
            verifier.verify(tampered, now=WHEN)

    def test_an_unbundled_context_is_refused(self, verifier: Verifier) -> None:
        """A document naming a context we do not carry cannot be canonicalized
        honestly, so it is refused rather than fetched at request time."""
        tampered = presentation()
        tampered["verifiableCredential"]["@context"] = [
            "https://www.w3.org/ns/credentials/v2",
            "https://example.test/whatever.jsonld",
        ]
        with pytest.raises(VerificationError, match="does not carry"):
            verifier.verify(tampered, now=WHEN)

    def test_a_log_describing_another_did_supplies_no_key(self) -> None:
        """The shape a redirect or a misconfigured proxy takes."""
        swapped = Verifier(
            Resolver(
                fetcher=offline_fetcher(
                    {LOG_URLS["gateway.jsonl"]: "surface.jsonl"}
                )
            )
        )
        with pytest.raises(VerificationError, match="describes"):
            swapped.verify(presentation(), now=WHEN)


class TestResolution:
    def test_the_log_url_follows_from_the_did(self) -> None:
        assert log_url(GATEWAY_DID) == LOG_URLS["gateway.jsonl"]
        assert log_url(SURFACE_DID) == LOG_URLS["surface.jsonl"]

    def test_a_non_ed25519_key_is_refused(self) -> None:
        with pytest.raises(ResolutionError, match="not Ed25519"):
            # z6LS… is X25519: a key agreement key, not a signing one.
            multikey_to_public_bytes("z6LSbysY2xFMRpGMhb7tFTLMpeuPRaqaWM1yECx2AtzE3KCc")

    def test_the_log_is_fetched_once_per_key(self) -> None:
        calls: list[str] = []
        fetcher = offline_fetcher()

        def counting(url: str) -> bytes:
            calls.append(url)
            return fetcher(url)

        verifier = Verifier(Resolver(fetcher=counting))
        verifier.verify(presentation(), now=WHEN)
        verifier.verify(presentation(), now=WHEN)
        # Two DIDs, two logs, and the second verification resolves neither again.
        assert len(calls) == 2
