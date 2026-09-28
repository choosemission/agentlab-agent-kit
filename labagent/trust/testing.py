# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""A stand-in gateway that signs the way a real one does, for tests and the harness.

**Why real proofs, not placeholders.** The Lab's coordinator tests run with
verification switched off and prove the verifier separately against a captured
envelope. That leaves the path in between (read the metadata, verify, compare
with a pinned DID, label) tested only with the verifier absent. Here the
stand-in issues genuine `eddsa-rdfc-2022` proofs, so the whole path runs with
verification **on**, and a test that strips or swaps a proof sees the verifier
itself refuse it.

It mirrors the shape of a real gateway, measured on 16 September 2026:

- a gateway DID, `did:webvh:<scid>:<host>`, that issues the credential;
- a surface DID, `…:<host>:surface:<id>`, that holds it and signs the
  presentation;
- both keys at `#key-2`, as Multikey, which is what the gateway's proofs name;
- the gateway's DID document lists only `#key-1` under `assertionMethod`, the gap
  recorded in CLAIMS.md. `authorise_key2=True` closes it, which is what a strict
  verifier needs.

**What it is not.** It proves the verifier and the labelling. It proves nothing
about what an Affinidi gateway does; that evidence is captured from live
gateways and recorded, dated, in CLAIMS.md.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .identity import GATEWAY_EXTENSIONS
from .resolve import ED25519_MULTICODEC, _B58, log_url
from .verify import CRYPTOSUITE, _document_loader, _hash_data

CREDENTIAL_CONTEXTS = [
    "https://www.w3.org/ns/credentials/v2",
    "https://d2oeuqaac90cm.cloudfront.net/TAgentIdentityCredentialV1R0.jsonld",
]


def b58encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, rem = divmod(number, 58)
        out = _B58[rem] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


def _key(seed: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed.encode()).digest())


def _multikey(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return "z" + b58encode(ED25519_MULTICODEC + raw)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class TestGateway:
    """Deterministic from `seed`, so a DID pinned in a peers file survives a restart."""

    __test__ = False  # not a pytest class

    seed: str
    host: str
    surface: str = "out"
    authorise_key2: bool = False

    def __post_init__(self) -> None:
        scid = "QmTest" + b58encode(hashlib.sha256(f"gw:{self.seed}".encode()).digest())[:32]
        sscid = "QmTest" + b58encode(hashlib.sha256(f"sf:{self.seed}".encode()).digest())[:32]
        self.gateway_did = f"did:webvh:{scid}:{self.host}"
        self.surface_did = f"did:webvh:{sscid}:{self.host}:surface:{self.surface}"
        self._gateway_key = _key(f"gw-key:{self.seed}")
        self._surface_key = _key(f"sf-key:{self.seed}")
        self._loader = _document_loader()

    # --- DID logs -----------------------------------------------------------

    def _document(self, did: str, key: Ed25519PrivateKey, *, gateway: bool) -> dict[str, Any]:
        key2 = f"{did}#key-2"
        # The gateway's real document authorises #key-1 only; the surface's
        # authorises #key-2. Reproduced, not tidied.
        assertion = [f"{did}#key-1"] if gateway and not self.authorise_key2 else [key2]
        return {
            "@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/multikey/v1"],
            "id": did,
            "verificationMethod": [
                {
                    "id": f"{did}#key-1",
                    "type": "JsonWebKey2020",
                    "controller": did,
                    "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519", "x": "placeholder"},
                },
                {"id": key2, "type": "Multikey", "controller": did, "publicKeyMultibase": _multikey(key)},
            ],
            "assertionMethod": assertion,
        }

    def did_logs(self) -> dict[str, bytes]:
        """Path → DID log body, for serving over HTTP or from a fetcher."""
        out = {}
        for did, key, gateway in (
            (self.gateway_did, self._gateway_key, True),
            (self.surface_did, self._surface_key, False),
        ):
            entry = {"versionId": "1-test", "state": self._document(did, key, gateway=gateway)}
            path = "/" + log_url(did).split("/", 3)[3]
            out[path] = (json.dumps(entry) + "\n").encode()
        return out

    # --- signing ------------------------------------------------------------

    def _sign(self, document: dict[str, Any], did: str, key: Ed25519PrivateKey) -> dict[str, Any]:
        proof = {
            "type": "DataIntegrityProof",
            "cryptosuite": CRYPTOSUITE,
            "created": _now(),
            "verificationMethod": f"{did}#key-2",
            "proofPurpose": "assertionMethod",
        }
        signature = key.sign(_hash_data(document, proof, self._loader))
        return {**document, "proof": {**proof, "proofValue": "z" + b58encode(signature)}}

    def presentation(self, identity_fields: dict[str, Any]) -> dict[str, Any]:
        """A presentation of the shape the gateway attaches on the request leg."""
        issued = datetime.now(timezone.utc)
        credential = self._sign(
            {
                "@context": CREDENTIAL_CONTEXTS,
                "id": f"urn:uuid:{uuid.uuid4()}",
                "type": ["VerifiableCredential", "AgentIdentityCredential"],
                "credentialSubject": {"id": self.surface_did, "identityFields": identity_fields},
                "issuer": self.gateway_did,
                "validFrom": issued.isoformat(),
                "validUntil": (issued + timedelta(days=365)).isoformat(),
            },
            self.gateway_did,
            self._gateway_key,
        )
        return self._sign(
            {
                "@context": ["https://www.w3.org/ns/credentials/v2"],
                "id": f"urn:uuid:{uuid.uuid4()}",
                "type": ["VerifiablePresentation"],
                "holder": self.surface_did,
                "verifiableCredential": credential,
            },
            self.surface_did,
            self._surface_key,
        )

    def attach(self, message: dict[str, Any]) -> dict[str, Any]:
        """Do to an outbound A2A message what a gateway's Identity element does:
        sign the caller's self-described name and attach it, serialised, under
        the request-leg extension URI."""
        from .identity import SELF_ASSERTED_EXTENSION

        metadata = dict(message.get("metadata") or {})
        descriptor = metadata.get(SELF_ASSERTED_EXTENSION) or {}
        inner = descriptor.get("agentIdentity", descriptor) if isinstance(descriptor, dict) else {}
        name = inner.get("name") if isinstance(inner, dict) else None
        vp = self.presentation({"agentIdentity.name": name or "unnamed"})
        metadata[GATEWAY_EXTENSIONS[0]] = {"verifiablePresentation": json.dumps(vp), "did": self.surface_did}
        return {**message, "metadata": metadata}


def fetcher_for(*gateways: TestGateway):
    """A resolver fetcher that serves these gateways' DID logs and nothing else."""
    from .resolve import ResolutionError

    table: dict[str, bytes] = {}
    for gateway in gateways:
        for path, body in gateway.did_logs().items():
            table[f"https://{gateway.host}{path}"] = body

    def fetch(url: str) -> bytes:
        if url not in table:
            raise ResolutionError(f"no test DID log for {url}")
        return table[url]

    return fetch
