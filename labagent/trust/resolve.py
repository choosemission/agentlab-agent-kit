# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
#
# Vendored from affinidi-lab `servers/lab-coordinator/coordinator/resolve.py`
# (28 Sep 2026). Changes here are marked "labagent:". A fix to the verification
# arithmetic belongs upstream as well — the two must not drift apart silently.
"""Resolving `did:webvh`, far enough to get a verification key.

A gateway's DID and a surface's DID are both `did:webvh`, and both publish a
**DID log** — a JSON Lines file, one entry per version, reachable over HTTPS at
a path the DID itself dictates:

    did:webvh:<scid>:<host>                       → https://<host>/.well-known/did.jsonl
    did:webvh:<scid>:<host>:surface:<id>          → https://<host>/surface/<id>/did.jsonl

The last line's `state` is the current DID document. Measured against a live
gateway on 16 September 2026; both shapes above were fetched and both carried a
`Multikey` verification method at `#key-2`, which is what the gateway's proofs
name.

**What this does not do, and it matters.** `did:webvh`'s whole argument is that
the SCID in the DID commits to the log's history, so a host that rewrites its
own log can be caught. This resolver does not verify the SCID, the entry hashes
or the witness proofs — it reads the latest entry and trusts TLS and the host.

The practical consequence: **this reduces to `did:web`.** A gateway operator can
serve whatever key they like for their own DID, which is fine, because a gateway
signing for itself is exactly what we are checking. What it does not protect
against is somebody who can take over the host or the TLS certificate. Verifying
the log chain is the upgrade, and it belongs here rather than anywhere else.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping

#: The DID log is small; a response far above this is not one.
MAX_LOG_BYTES = 512 * 1024

#: How long a resolved key is trusted before being fetched again. Keys rotate
#: rarely and an interview is a handful of messages, so this is about not
#: fetching the same log four times in one conversation.
CACHE_TTL_SECONDS = 15 * 60

#: Ed25519 in multicodec, which is what a `z6Mk…` Multikey decodes to.
ED25519_MULTICODEC = b"\xed\x01"

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ResolutionError(RuntimeError):
    """The DID could not be resolved into a key. Never a 500: the caller turns
    this into a refusal the participant can act on."""


def b58decode(value: str) -> bytes:
    number = 0
    for character in value:
        index = _B58.find(character)
        if index < 0:
            raise ResolutionError(f"not base58: {character!r}")
        number = number * 58 + index
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(value) - len(value.lstrip("1"))) + decoded


def multikey_to_public_bytes(multibase: str) -> bytes:
    """`z6Mk…` → the 32 raw Ed25519 bytes."""
    if not multibase.startswith("z"):
        raise ResolutionError("verification key is not multibase base58btc")
    raw = b58decode(multibase[1:])
    if raw[:2] != ED25519_MULTICODEC:
        raise ResolutionError(f"verification key is not Ed25519 (multicodec {raw[:2].hex()})")
    if len(raw) != 34:
        raise ResolutionError(f"Ed25519 key is {len(raw) - 2} bytes, expected 32")
    return raw[2:]


def log_url(did: str, host_map: Mapping[str, str] | None = None) -> str:
    """Where the DID log for this DID lives.

    labagent: `host_map` rewrites a DID's host to a base URL, so the two-agent
    harness can serve DID logs over plain HTTP from `harness/pretend_gateway.py`.
    It is set from LABAGENT_RESOLVE_HOSTS, which a deployment never sets.
    """
    parts = did.split(":")
    if len(parts) < 4 or parts[0] != "did" or parts[1] != "webvh":
        raise ResolutionError(f"not a did:webvh: {did}")

    host = parts[3].replace("%3A", ":")
    if not host or "/" in host:
        raise ResolutionError(f"DID names no usable host: {did}")

    base = (host_map or {}).get(host, f"https://{host}").rstrip("/")
    path = [segment for segment in parts[4:] if segment]
    if path:
        return f"{base}/{'/'.join(path)}/did.jsonl"
    return f"{base}/.well-known/did.jsonl"


def fetch(url: str, timeout: float = 10.0) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read(MAX_LOG_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise ResolutionError(f"{url} returned HTTP {error.code}") from error
    except Exception as error:  # timeouts, DNS, TLS
        raise ResolutionError(f"{url} could not be fetched: {error}") from error


def document_from_log(body: bytes) -> dict:
    """The current DID document: the `state` of the last entry in the log."""
    if len(body) > MAX_LOG_BYTES:
        raise ResolutionError("DID log is larger than this resolver will read")
    lines = [line for line in body.decode("utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        raise ResolutionError("DID log is empty")
    try:
        entry = json.loads(lines[-1])
    except ValueError as error:
        raise ResolutionError("the last DID log entry is not JSON") from error
    state = entry.get("state")
    if not isinstance(state, dict):
        raise ResolutionError("the last DID log entry carries no DID document")
    return state


def check_purpose(document: dict, verification_method: str, purpose: str) -> None:
    """labagent: whether the DID document authorises this key for this purpose.

    Off by default (`LABAGENT_STRICT_PROOF_PURPOSE`). Measured 16 Sep 2026: every
    gateway-issued credential is signed by `#key-2` with `assertionMethod`, and
    the gateway's DID document lists only `#key-1` there. A conformant verifier
    rejects all of them on authorisation, not cryptography. See CLAIMS.md.
    """
    for entry in document.get(purpose) or []:
        ident = entry.get("id") if isinstance(entry, dict) else entry
        if isinstance(ident, str) and (ident == verification_method or document.get("id", "") + ident == verification_method):
            return
    raise ResolutionError(f"{verification_method} is not listed under {purpose} in its DID document")


def key_from_document(document: dict, verification_method: str) -> bytes:
    """The public key this proof names, from the DID document that should hold it."""
    methods = document.get("verificationMethod")
    if not isinstance(methods, list):
        raise ResolutionError("DID document lists no verification methods")

    for method in methods:
        if not isinstance(method, dict) or method.get("id") != verification_method:
            continue
        multibase = method.get("publicKeyMultibase")
        if not isinstance(multibase, str):
            # `#key-1` on these gateways is a JsonWebKey2020 and carries no
            # multibase. Not an error in itself — the proof names which one it
            # used, and that one has to be usable.
            raise ResolutionError(f"{verification_method} publishes no publicKeyMultibase")
        return multikey_to_public_bytes(multibase)

    raise ResolutionError(f"{verification_method} is not in the DID document")


@dataclass
class _Cached:
    key: bytes
    at: float


class Resolver:
    """Verification methods, resolved and briefly remembered.

    `fetcher` is injected so tests run offline against a real DID log captured
    from a live gateway, rather than against a mock of what one might look like.
    """

    def __init__(
        self,
        fetcher: Callable[[str], bytes] = fetch,
        now: Callable[[], float] = time.time,
        ttl: float = CACHE_TTL_SECONDS,
        host_map: Mapping[str, str] | None = None,
        strict_purpose: bool = False,
    ) -> None:
        self._host_map = dict(host_map or {})
        self._strict_purpose = strict_purpose
        self._fetch = fetcher
        self._now = now
        self._ttl = ttl
        self._cache: dict[str, _Cached] = {}

    def public_key(self, verification_method: str, purpose: str = "assertionMethod") -> bytes:
        """The key bytes for a `did:webvh:…#key-n`, fetching the log if needed."""
        if "#" not in verification_method:
            raise ResolutionError(f"verification method names no key: {verification_method}")

        cached = self._cache.get(verification_method)
        if cached is not None and self._now() - cached.at < self._ttl:
            return cached.key

        did = verification_method.split("#", 1)[0]
        url = log_url(did, self._host_map)
        document = document_from_log(self._fetch(url))

        # The document must be the one the DID asked for. A log served at the
        # right URL but describing another DID is the shape a redirect or a
        # misconfigured proxy takes, and it must not silently supply a key.
        if document.get("id") != did:
            raise ResolutionError(f"DID log at {url} describes {document.get('id')!r}, not {did!r}")

        key = key_from_document(document, verification_method)
        if self._strict_purpose:
            check_purpose(document, verification_method, purpose)
        self._cache[verification_method] = _Cached(key=key, at=self._now())
        return key
