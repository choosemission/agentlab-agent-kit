# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""`labagent verify <file>`: what one captured request establishes.

Reads a file, never a terminal paste. It accepts:
- a capture written by `labagent/capture.py`;
- a bare JSON-RPC request;
- a bare A2A message.

It reports the facts CLAIMS.md is built from, so re-measuring a claim is reading
this output rather than re-deriving it:

- **C1**: which extension the presentation arrived under, and whether it was a
  JSON string or an object;
- **C7**: whether both proofs verify;
- **C6**: whether they still verify when the signing key must be listed under
  the proof's purpose;
- **C10**: which credential headers reached the agent;
- **C3**: whether a fabric hop was stamped.

It runs in the owner's process, not the agent's, and resolves DIDs over the
network, as the agent does.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .identity import read_caller_identity
from .provenance import HOP_KEY
from .resolve import Resolver
from .verify import VerificationError, Verifier


def _as_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def unwrap(document: Any) -> tuple[str, dict[str, Any], Any]:
    """`(kind, capture headers, message metadata)` for any accepted shape."""
    headers: dict[str, Any] = {}
    kind = "message"
    if isinstance(document, dict) and "captured_at" in document and "body" in document:
        headers = document.get("headers") or {}
        kind = f"capture of {document.get('method')} {document.get('path')}"
        document = _as_json(document.get("body")) if document.get("body") else None
    if isinstance(document, dict) and "jsonrpc" in document:
        if kind == "message":
            kind = "JSON-RPC request"
        document = (document.get("params") or {}).get("message")
    metadata = document.get("metadata") if isinstance(document, dict) else None
    return kind, headers, metadata


def _raw_at(metadata: dict[str, Any], extension: str, path: str) -> Any:
    value: Any = metadata.get(extension)
    for key in path.split("."):
        value = _as_json(value)
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _attempt(verifier: Verifier, presentation: dict[str, Any]) -> dict[str, Any]:
    try:
        checked = verifier.verify(presentation)
    except VerificationError as error:
        return {"ok": False, "error": str(error)}
    return {"ok": True, "issuer": checked.issuer, "holder": checked.holder}


def examine(
    document: Any,
    *,
    resolver: Callable[[bool], Resolver] | None = None,
    host_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The report. `resolver(strict)` builds a resolver; tests pass an offline one."""
    make = resolver or (lambda strict: Resolver(host_map=host_map, strict_purpose=strict))
    kind, headers, metadata = unwrap(document)

    report: dict[str, Any] = {
        "source": kind,
        "credential_headers": {
            name: name in headers for name in ("x-api-key", "authorization")
        }
        if headers
        else None,
        "hop": metadata.get(HOP_KEY) if isinstance(metadata, dict) else None,
    }
    identity = read_caller_identity(metadata)
    report["self_asserted"] = identity.self_asserted
    presentation = identity.presentation
    if presentation is None or presentation.document is None:
        report["presentation"] = None
        report["why"] = identity.malformed or "no gateway presentation in this message"
        report["verified"] = False
        return report

    raw = _raw_at(metadata, presentation.extension or "", presentation.path or "")
    report["presentation"] = {
        "extension": presentation.extension,
        "path": presentation.path,
        "serialised_as": "JSON string" if isinstance(raw, str) else type(raw).__name__,
        "issuer": presentation.issuer,
        "holder": presentation.did,
        "identity_fields": presentation.identity_fields,
    }
    lenient = _attempt(Verifier(make(False)), presentation.document)
    strict = _attempt(Verifier(make(True)), presentation.document)
    report["proofs"] = lenient
    report["strict_proof_purpose"] = strict
    report["verified"] = lenient["ok"]
    return report
