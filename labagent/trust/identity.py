# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
#
# Vendored from affinidi-lab `servers/lab-coordinator/coordinator/identity.py`
# (28 Sep 2026). Changes here are marked "labagent:". A fix to the verification
# arithmetic belongs upstream as well — the two must not drift apart silently.
"""Reading what a gateway signed on the way in.

**Started as a copy of `servers/hello-a2a/hello_a2a/identity.py`, and has since
diverged.** This one keeps the parsed presentation (`Presentation.document`)
because `verify.py` checks its proofs, which `hello-a2a` does not. It is
duplicated rather than imported because the two run as separate containers. A
change measured against a live gateway belongs in both; neither is a mirror of
the other any longer.

Three descriptions of the caller can arrive in one message:

1. **Self-asserted** — the client's own descriptor under
   `…/extensions/agent-identity/v1`. Unsigned; it is what an ungoverned exchange
   looks like.
2. **Gateway-signed** — a Verifiable Presentation the sending gateway attached
   once an Identity element was configured on the leg. It arrives
   **serialised**: `verifiablePresentation` holds a JSON string, not a nested
   object. See `_as_object`.
3. Nothing at all, when the caller reached us some other way.

**What (2) is worth.** The presentation is signed by the *sending* gateway over
fields that gateway's own caller supplied. It is evidence that identity was
configured and that a particular gateway vouched for the call. Across a boundary
it is worth more than it is within one — the signer is somebody else's gateway.

**This module only reads.** It finds a presentation carrying a proof and says
what it claims; it does not check the signature or resolve the issuer DID.
`verify.py` does both, and `provenance.py` labels a message gateway-verified
only when both proofs verify and the issuer is the DID pinned for a peer.
Nothing read here should be trusted until then.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

#: The caller's unsigned self-description. Sent by the client, not the gateway.
SELF_ASSERTED_EXTENSION = "https://fabric.affinidi.io/extensions/agent-identity/v1"

#: Where a gateway-signed presentation has been observed. Both are checked
#: because the two legs use different URIs and the version suffix has moved
#: before: `agent-identity-binding/v1` on the inbound leg (measured 28 August
#: 2026), `agent-identity-credential/v1` on the response leg. A key not on this
#: list is still found — `_walk` looks for the shape, not the name — and the
#: name it arrived under is reported back, so a rename shows up as data rather
#: than as a broken recipe.
GATEWAY_EXTENSIONS = (
    "https://fabric.affinidi.io/extensions/agent-identity-binding/v1",
    "https://fabric.affinidi.io/extensions/agent-identity-credential/v1",
)

#: How deep to look for a presentation inside one metadata entry. The observed
#: shape needs 1; the allowance is for a wrapper appearing above it.
_MAX_DEPTH = 4


@dataclass(frozen=True)
class Presentation:
    """A gateway-signed presentation, as far as this server reads it."""

    #: The caller DID the gateway minted. On the inbound leg this is the DID of
    #: the *surface*, which is what makes the response leg's workload binding
    #: joinable to it.
    did: str | None
    #: The fields the gateway hashed to derive that DID — the ones the
    #: participant marked `"x-identity": true`.
    identity_fields: dict[str, Any] = field(default_factory=dict)
    #: The gateway's own DID. Their gateway, so this identifies the signer, not
    #: the participant.
    issuer: str | None = None
    #: Present iff the presentation carries a proof. Not checked here —
    #: `verify.py` checks it, against `document`.
    proof_value: str | None = None
    #: The presentation itself, parsed. Kept because `verify.py` has to
    #: canonicalize the document that was actually signed — a reading of it is
    #: not something a signature can be checked against.
    document: dict[str, Any] | None = None
    #: The metadata key it arrived under, reported so a URI change is visible.
    extension: str | None = None
    #: Where inside that entry it was found, e.g. `verifiablePresentation`.
    path: str | None = None


@dataclass(frozen=True)
class CallerIdentity:
    """Everything this server can say about who called it."""

    presentation: Presentation | None = None
    #: The caller's unsigned descriptor, if it sent one.
    self_asserted: dict[str, Any] | None = None
    #: Set when something presentation-shaped was found but could not be read.
    #: Reported rather than swallowed: a half-configured Identity element is a
    #: likelier explanation than a hostile caller, and the participant can only
    #: act on what they are told.
    malformed: str | None = None

    @property
    def signed(self) -> bool:
        """Whether a gateway vouched for this call. The gate on a code."""
        return self.presentation is not None


def _as_object(value: Any) -> dict[str, Any] | None:
    """The value as a JSON object, whether it arrived as one or as a string.

    The gateway sends `verifiablePresentation` **serialised** — a JSON string,
    not a nested object (measured 28 August 2026, both legs). Reading only the
    nested form told a participant with a correctly configured Identity element
    that their gateway had signed nothing, and sent them back to re-configure
    something that was working. Both are accepted because the wire is the
    authority and it has moved before.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _walk(value: Any, depth: int = 0) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield `(path, candidate)` for every presentation-shaped object below.

    Shape rather than key name. The gateway's envelope has moved once already,
    and a server that recognised only one spelling would report "no identity
    presented" for a call that presented one — sending the participant back to
    re-configure something that is working.
    """
    if depth > _MAX_DEPTH or not isinstance(value, dict):
        return
    for key, child in value.items():
        candidate = _as_object(child)
        if key == "verifiablePresentation" and candidate is not None:
            yield key, candidate
        elif _is_presentation(candidate):
            yield key, candidate
        elif isinstance(child, dict):
            for path, found in _walk(child, depth + 1):
                yield f"{key}.{path}", found


def _is_presentation(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    types = value.get("type")
    if isinstance(types, str):
        types = [types]
    return isinstance(types, list) and "VerifiablePresentation" in types


def _first_credential(presentation: dict[str, Any]) -> dict[str, Any] | None:
    """`verifiableCredential` is a single object in the shape the gateway sends
    and a list in the W3C data model. Accept both, serialised or not; read the
    first."""
    credential = presentation.get("verifiableCredential")
    if isinstance(credential, list):
        credential = next((c for c in (_as_object(item) for item in credential) if c is not None), None)
    return _as_object(credential)


def _proof_value(container: dict[str, Any]) -> str | None:
    proof = container.get("proof")
    if isinstance(proof, list):
        proof = next((p for p in proof if isinstance(p, dict)), None)
    if not isinstance(proof, dict):
        return None
    value = proof.get("proofValue")
    return value if isinstance(value, str) and value else None


def _read_presentation(extension: str, path: str, candidate: dict[str, Any], sibling_did: Any) -> Presentation | None:
    credential = _first_credential(candidate)
    subject = credential.get("credentialSubject") if credential else None
    subject = subject if isinstance(subject, dict) else {}

    did = None
    for value in (sibling_did, candidate.get("holder"), subject.get("id")):
        if isinstance(value, str) and value:
            did = value
            break

    proof_value = _proof_value(candidate) or (_proof_value(credential) if credential else None)
    # A presentation with neither a proof nor a credential is not one. Refusing
    # it here is what stops a caller pasting `{"type": ["VerifiablePresentation"]}`
    # into its own metadata and being handed a code.
    if credential is None or proof_value is None:
        return None

    identity_fields = subject.get("identityFields")
    issuer = credential.get("issuer")
    if isinstance(issuer, dict):
        issuer = issuer.get("id")

    return Presentation(
        did=did,
        identity_fields=dict(identity_fields) if isinstance(identity_fields, dict) else {},
        issuer=issuer if isinstance(issuer, str) else None,
        proof_value=proof_value,
        document=candidate,
        extension=extension,
        path=path,
    )


def read_caller_identity(metadata: Any) -> CallerIdentity:
    """Read a message's metadata. Never raises: a malformed envelope is a
    finding to report to the participant, not a 500."""
    if not isinstance(metadata, dict):
        return CallerIdentity()

    self_asserted = None
    descriptor = metadata.get(SELF_ASSERTED_EXTENSION)
    if isinstance(descriptor, dict):
        # The client may send it flat or under `agentIdentity` — the meta field
        # the participant configures on the Identity element decides which, and
        # both are worth showing back to them.
        inner = descriptor.get("agentIdentity")
        self_asserted = dict(inner) if isinstance(inner, dict) else dict(descriptor)

    malformed: str | None = None
    for extension, entry in metadata.items():
        if extension == SELF_ASSERTED_EXTENSION or not isinstance(entry, dict):
            continue
        for path, candidate in _walk(entry):
            presentation = _read_presentation(extension, path, candidate, entry.get("did"))
            if presentation is not None:
                return CallerIdentity(presentation=presentation, self_asserted=self_asserted)
            malformed = (
                f"{extension} carried something shaped like a presentation at '{path}', "
                "but it had no verifiable credential or no proof."
            )

    return CallerIdentity(self_asserted=self_asserted, malformed=malformed)
