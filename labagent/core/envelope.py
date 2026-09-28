# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""The wire shape between two participants' agents.

One A2A message carries one `DataPart`:

    {"skill": "feed.deliver", "v": 1, ...fields}

`skill` routes it to a module; `v` lets a module change its fields without
guessing. Structured data rather than prose, deliberately: a module decides on
fields it validated, never on words another agent chose. See docs/protocol.md.

The caller's self-description rides in the metadata under the extension the
gateway's Identity element reads, so a gateway configured for the Lab's recipes
signs these messages without further setup.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..trust.identity import SELF_ASSERTED_EXTENSION

VERSION = 1
MAX_SKILL = 64


class EnvelopeError(ValueError):
    pass


def body(skill: str, **fields: Any) -> dict[str, Any]:
    return {"skill": skill, "v": VERSION, **fields}


def message(payload: dict[str, Any], identity: dict[str, str]) -> dict[str, Any]:
    return {
        "role": "user",
        "messageId": uuid.uuid4().hex,
        "parts": [{"kind": "data", "data": payload}],
        "extensions": [SELF_ASSERTED_EXTENSION],
        "metadata": {SELF_ASSERTED_EXTENSION: {"agentIdentity": dict(identity)}},
    }


def request(payload: dict[str, Any], identity: dict[str, str]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {"message": message(payload, identity)},
    }


def _data_of(part: Any) -> Any:
    """A part, as the SDK's model or as plain JSON."""
    root = getattr(part, "root", part)
    if isinstance(root, dict):
        return root.get("data") if root.get("kind") == "data" else None
    return getattr(root, "data", None) if getattr(root, "kind", None) == "data" else None


def _single_data(parts: Any) -> dict[str, Any]:
    found = [d for d in (_data_of(p) for p in parts or []) if isinstance(d, dict)]
    if len(found) != 1:
        raise EnvelopeError("send exactly one data part: {\"skill\": ..., \"v\": 1, ...}")
    return found[0]


def parse_parts(parts: Any) -> dict[str, Any]:
    """The single data payload of a request, validated for shape. Raises EnvelopeError."""
    payload = _single_data(parts)
    skill = payload.get("skill")
    if not isinstance(skill, str) or not skill or len(skill) > MAX_SKILL:
        raise EnvelopeError("the data part names no skill")
    if payload.get("v") != VERSION:
        raise EnvelopeError(f"unsupported envelope version {payload.get('v')!r}; this agent speaks v{VERSION}")
    return payload


def reply_payload(response: dict[str, Any]) -> dict[str, Any]:
    """The data payload out of a JSON-RPC `message/send` response: a Task (the
    reply is its status message) or a bare Message. A reply is an
    acknowledgement, so it is not held to the request's skill/v shape."""
    if "error" in response:
        error = response["error"] or {}
        raise EnvelopeError(f"JSON-RPC error {error.get('code')}: {error.get('message')}")
    result = response.get("result") or {}
    msg = (result.get("status") or {}).get("message") if result.get("kind") == "task" else result
    try:
        return _single_data((msg or {}).get("parts"))
    except EnvelopeError:
        raise EnvelopeError("the reply carried no data payload") from None
