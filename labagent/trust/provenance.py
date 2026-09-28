# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Who sent this, and how sure can we be: one label per inbound message.

**Two labels, and the rule between them is strict.**

- `gateway-verified`: a gateway presentation arrived on the request leg, BOTH
  proofs verified, and the credential's issuer is exactly the gateway DID you
  pinned for a peer in `peers.toml`.
- `self-asserted`: anything else. The reason is always recorded, because "why
  isn't this verified?" is the first question an owner will ask.

**What gateway-verified means, and what it does not.** It means *a caller whose
gateway vouched for this agent identity delivered this message*. The gateway
signs the caller's identity, not the message body, and nothing in the envelope
binds the presentation to this request — no nonce, no challenge — so a captured
presentation replays cleanly. It is not "the content is signed", and nothing in
this toolkit may present it as such.

**The request leg only.** Response-leg signing across a gateway-to-gateway hop
is unproven (CLAIMS.md), so no reply is ever labelled. Anything that commits
somebody travels as a new request to the party that relies on it.

**Keyed on the issuer, not the caller.** The caller DID is a hash of fields the
participant writes themselves; the issuer is their gateway, and they cannot mint
a second one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from ..peers import Peer, Peers
from .identity import read_caller_identity
from .verify import VerificationError, Verifier

VERIFIED = "gateway-verified"
SELF_ASSERTED = "self-asserted"

#: Where the route of a fabric hop is stamped (measured 16 Sep 2026). Recorded,
#: never relied on: it is not on every message and its order is unverified.
HOP_KEY = "x-affinidi-fabric-gateway-did"


@dataclass(frozen=True)
class Provenance:
    label: Literal["gateway-verified", "self-asserted"]
    reason: str
    #: The peer this message is attributed to by a verified issuer, if any.
    peer: str | None = None
    #: The peer named by the caller's unsigned self-description, if it matches
    #: one. Never enough on its own; see `Peer.accept_self_asserted`.
    claimed_peer: str | None = None
    issuer: str | None = None
    holder: str | None = None
    hop: Any = None

    @property
    def verified(self) -> bool:
        return self.label == VERIFIED

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def attributable(provenance: Provenance, peers: Peers) -> Peer | None:
    """The peer a module may act on behalf of, or None.

    A verified peer always; a claimed one only if you said, for that peer, that
    self-assertion is enough.
    """
    if provenance.verified and provenance.peer:
        return peers.get(provenance.peer)
    if provenance.claimed_peer:
        peer = peers.get(provenance.claimed_peer)
        if peer is not None and peer.accept_self_asserted:
            return peer
    return None


def assess(metadata: Any, verifier: Verifier | None, peers: Peers) -> Provenance:
    identity = read_caller_identity(metadata)
    hop = metadata.get(HOP_KEY) if isinstance(metadata, dict) else None

    name = (identity.self_asserted or {}).get("name")
    claimed = peers.by_claimed_name(name if isinstance(name, str) else None)
    claimed_name = claimed.name if claimed else None

    def unverified(reason: str, issuer: str | None = None, holder: str | None = None) -> Provenance:
        return Provenance(SELF_ASSERTED, reason, None, claimed_name, issuer, holder, hop)

    presentation = identity.presentation
    if presentation is None or presentation.document is None:
        return unverified(identity.malformed or "no gateway presentation on the request")
    if verifier is None:
        return unverified("verification is switched off (LABAGENT_VERIFY=false)", presentation.issuer)

    try:
        checked = verifier.verify(presentation.document)
    except VerificationError as error:
        return unverified(f"the presentation did not verify: {error}", presentation.issuer)

    peer = peers.by_gateway_did(checked.issuer)
    if peer is None:
        if claimed is not None:
            return unverified(
                f"claims to be {claimed.name}, but its gateway {checked.issuer} is not the DID pinned for "
                f"{claimed.name} ({claimed.gateway_did or 'none pinned'})",
                checked.issuer,
                checked.holder,
            )
        return unverified(f"verified, but gateway {checked.issuer} is not pinned for any peer", checked.issuer, checked.holder)

    return Provenance(
        VERIFIED,
        f"both proofs verified; issuer is the gateway pinned for {peer.name}",
        peer.name,
        claimed_name,
        checked.issuer,
        checked.holder,
        hop,
    )
