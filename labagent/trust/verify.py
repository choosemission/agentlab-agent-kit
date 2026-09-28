# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
#
# Vendored from affinidi-lab `servers/lab-coordinator/coordinator/verify.py`
# (28 Sep 2026). Changes here are marked "labagent:". A fix to the verification
# arithmetic belongs upstream as well — the two must not drift apart silently.
"""Verifying the proofs, rather than noting that they are there.

`hello-a2a` checks that a presentation carries a proof and says plainly that it
checks nothing more. That is honest for a recipe whose lesson is "your own
gateway signed this, so it proves you configured identity". It is not enough
here: the directory is keyed on the issuer DID, so an unverified proof means
anything that reaches this server can claim an organisation under any gateway's
name.

So this module does the arithmetic. Two proofs arrive, and **both must verify**:

1. **The credential**, signed by the gateway (`issuer`). It says the caller has
   these identity fields, and its issuer is what the directory is keyed on.
2. **The presentation**, signed by the surface (`holder`), wrapping that
   credential for this exchange. Requiring it is what stops somebody who has
   obtained a credential — they travel in every message — from wrapping it in an
   envelope of their own.

It was briefly believed that request-leg presentation proofs did not verify.
They do: `docs/evidence/gateway-presentation-proof/` holds samples checked by two
independent implementations, and the one that failed had been transcribed by
hand. That is why the proof is required here rather than merely noted.

**Cryptosuite: `eddsa-rdfc-2022`.** Canonicalize the proof options and the
document separately with RDFC-1.0, SHA-256 each, concatenate *proof hash then
document hash*, and verify an Ed25519 signature over the result. Confirmed
against a real credential from a live gateway on 16 September 2026: that order
verifies and the other does not.

**Contexts are bundled, not fetched.** Canonicalization needs the JSON-LD
contexts, and fetching them per request would put w3.org and a CloudFront
distribution on the path of every claim — and would let whoever serves them
change what a document means. `contexts/` holds them with their hashes; a
document naming a context that is not there cannot be verified, and is refused
rather than waved through.

**What is still not checked, and should be said out loud.**

- **Replay.** Nothing in the envelope is bound to this request: no nonce, no
  challenge, no `domain`. A presentation captured from one exchange verifies
  perfectly in another. The credential lives a year and the presentation proof
  nine months. So a verified proof says "this gateway signed this, at some
  point", not "this gateway is speaking to me now". The door — an API key held
  only by the Lab's own gateway — is what makes that gap tolerable.
- **The DID log's history.** See `resolve.py`: this trusts TLS and the host,
  which reduces `did:webvh` to `did:web`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .resolve import Resolver, ResolutionError, b58decode

CONTEXTS = Path(__file__).parent / "contexts"

#: The cryptosuite this module implements. A proof naming anything else is
#: refused rather than attempted: a suite we do not implement is exactly the
#: case where guessing is worst.
CRYPTOSUITE = "eddsa-rdfc-2022"


class VerificationError(RuntimeError):
    """The proof did not check out, or could not be checked. The message is
    shown to the participant, so it says which."""


@dataclass(frozen=True)
class Verified:
    """What a checked presentation establishes."""

    #: The gateway that issued the credential. What the directory is keyed on,
    #: and now something proved rather than read.
    issuer: str
    #: The surface DID the credential was issued to, and that signed the
    #: presentation.
    holder: str
    identity_fields: dict[str, Any]
    #: Whether the presentation's own proof verified. Always True on a returned
    #: `Verified` — a failure raises — so it records that both proofs were
    #: checked rather than leaving a reader to infer it. Nothing reads it today.
    presentation_proof: bool = True


def _document_loader() -> Any:
    """A loader that serves the bundled contexts and refuses everything else."""
    index = json.loads((CONTEXTS / "index.json").read_text())
    documents = {
        url: json.loads((CONTEXTS / entry["file"]).read_text()) for url, entry in index.items()
    }

    def load(url: str, options: dict | None = None) -> dict:
        document = documents.get(url)
        if document is None:
            raise VerificationError(
                f"the proof needs the JSON-LD context {url}, which this server does not carry. "
                "Adding it is a deliberate act — see labagent/trust/contexts."
            )
        return {"contextUrl": None, "documentUrl": url, "document": document}

    return load


def _canonical(document: dict, loader: Any) -> str:
    from pyld import jsonld

    try:
        return jsonld.normalize(
            document,
            {"algorithm": "URDNA2015", "format": "application/n-quads", "documentLoader": loader},
        )
    except jsonld.JsonLdError as error:
        # pyld wraps whatever the document loader raised, several layers deep.
        # The loader's own message is the useful one — it names the context that
        # is missing — so dig it out rather than reporting "could not convert
        # input to RDF", which tells a participant nothing.
        cause: BaseException | None = error
        while cause is not None:
            if isinstance(cause, VerificationError):
                raise cause
            cause = cause.__cause__
        raise VerificationError(f"the document could not be canonicalized: {error}") from error


def _hash_data(secured: dict, proof: dict, loader: Any) -> bytes:
    """The bytes the signature is over: SHA-256 of the proof options, then
    SHA-256 of the document, concatenated in that order."""
    document = {key: value for key, value in secured.items() if key != "proof"}
    options = {key: value for key, value in proof.items() if key != "proofValue"}
    options["@context"] = secured["@context"]

    proof_hash = hashlib.sha256(_canonical(options, loader).encode()).digest()
    document_hash = hashlib.sha256(_canonical(document, loader).encode()).digest()
    return proof_hash + document_hash


def _proof_of(document: dict) -> dict:
    proof = document.get("proof")
    if isinstance(proof, list):
        proof = next((item for item in proof if isinstance(item, dict)), None)
    if not isinstance(proof, dict):
        raise VerificationError("no proof on the document")
    if proof.get("cryptosuite") != CRYPTOSUITE:
        raise VerificationError(
            f"proof uses {proof.get('cryptosuite')!r}; this server verifies {CRYPTOSUITE} only"
        )
    if not isinstance(proof.get("proofValue"), str):
        raise VerificationError("proof carries no proofValue")
    return proof


def _check_signature(secured: dict, resolver: Resolver, loader: Any, *, expected_controller: str) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    proof = _proof_of(secured)
    method = proof.get("verificationMethod")
    if not isinstance(method, str):
        raise VerificationError("proof names no verification method")

    # The key has to belong to the party the document says signed it. Without
    # this a credential could name one issuer and be signed by any DID at all.
    if method.split("#", 1)[0] != expected_controller:
        raise VerificationError(
            f"signed by {method.split('#', 1)[0]}, which is not {expected_controller}"
        )

    value = proof["proofValue"]
    if not value.startswith("z"):
        raise VerificationError("proofValue is not multibase base58btc")
    signature = b58decode(value[1:])

    try:
        # labagent: the purpose is checked only when the resolver is strict.
        key = resolver.public_key(method, str(proof.get("proofPurpose") or "assertionMethod"))
    except ResolutionError as error:
        raise VerificationError(f"could not resolve {method}: {error}") from error

    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, _hash_data(secured, proof, loader))
    except InvalidSignature as error:
        raise VerificationError(
            f"the signature does not verify against the key published at {method}"
        ) from error


def _within_validity(credential: dict, now: datetime) -> None:
    for field, compare in (("validFrom", "before"), ("validUntil", "after")):
        raw = credential.get(field)
        if not isinstance(raw, str):
            continue
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise VerificationError(f"{field} is not a timestamp: {raw!r}") from None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if compare == "before" and now < moment:
            raise VerificationError(f"the credential is not valid until {raw}")
        if compare == "after" and now > moment:
            raise VerificationError(f"the credential expired at {raw}")


class Verifier:
    """Checks a gateway-signed presentation, or says why it could not."""

    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolver = resolver if resolver is not None else Resolver()
        self._loader = _document_loader()

    def verify(self, presentation: dict, *, now: datetime | None = None) -> Verified:
        now = now or datetime.now(timezone.utc)

        credential = presentation.get("verifiableCredential")
        if isinstance(credential, list):
            credential = next((item for item in credential if isinstance(item, dict)), None)
        if not isinstance(credential, dict):
            raise VerificationError("the presentation carries no verifiable credential")

        issuer = credential.get("issuer")
        if isinstance(issuer, dict):
            issuer = issuer.get("id")
        if not isinstance(issuer, str) or not issuer:
            raise VerificationError("the credential names no issuer")

        subject = credential.get("credentialSubject")
        subject = subject if isinstance(subject, dict) else {}
        holder = presentation.get("holder") or subject.get("id")
        if not isinstance(holder, str) or not holder:
            raise VerificationError("the presentation names no holder")

        # The gateway issues the credential to the surface that then presents
        # it. A presentation holding somebody else's credential is the one
        # forgery this catches that a signature check alone would not.
        if isinstance(subject.get("id"), str) and subject["id"] != holder:
            raise VerificationError(
                f"the credential was issued to {subject['id']}, but presented by {holder}"
            )

        _within_validity(credential, now)

        # Both, and either failing refuses the claim.
        _check_signature(credential, self._resolver, self._loader, expected_controller=issuer)
        _check_signature(presentation, self._resolver, self._loader, expected_controller=holder)

        fields = subject.get("identityFields")
        return Verified(
            issuer=issuer,
            holder=holder,
            identity_fields=dict(fields) if isinstance(fields, dict) else {},
        )
