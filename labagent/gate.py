# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
#
# Adapted from affinidi-lab `servers/lab-coordinator/coordinator/{auth,app}.py`.
"""Two doors, and neither opens the other.

**The public door** is the A2A endpoint. Your gateway is the only thing that
should reach it, and it injects a key on the last leg; anything arriving without
that key came round the gateway, and is refused. Closing that ungoverned path is
the point of having a gateway at all.

**The owner's door** is the owner MCP server (`owner/mcp.py`), on its own port.
It is reached through a *different* access point on the same gateway, which
injects a *different* key: `LABAGENT_OWNER_KEYS`. The inbound key never opens
it, so an agent that can reach you cannot act as you.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping

from starlette.responses import JSONResponse

from .config import Config

#: Served without the key. A surface fetches the target's card without
#: injecting the credential (measured by the Lab 31 Aug 2026), so a card behind
#: the key breaks discovery rather than protecting anything.
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
HEALTH_PATHS = ("/healthz",)

GUIDANCE = (
    "This agent is reached through its owner's gateway, which adds the credential for the last leg. "
    "Call the gateway's access point, not this address."
)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def presented_credential(headers: Mapping[str, str]) -> str | None:
    """`x-api-key` is read BEFORE `Authorization`, and the order is load-bearing:
    the gateway forwards the caller's own `Authorization` header and injects its
    credential alongside it."""
    api_key = headers.get("x-api-key")
    if api_key and api_key.strip():
        return api_key.strip()
    header = headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def accepts(config: Config, credential: str | None, keys: tuple[str, ...] | None = None) -> bool:
    # `keys` names the key set, so one gate serves both doors.
    keys = config.api_keys if keys is None else keys
    if config.allow_anonymous and not keys:
        return True
    if credential is None:
        return False
    actual = _digest(credential)
    return any(hmac.compare_digest(_digest(key), actual) for key in keys)


def is_exempt(path: str) -> bool:
    return path in HEALTH_PATHS or path in CARD_PATHS


class ApiKeyGate:
    """Pure ASGI, not `BaseHTTPMiddleware`: the A2A app streams SSE, and a
    middleware that buffered responses would hold those open."""

    def __init__(self, app: Any, config: Config, *, keys: tuple[str, ...] | None = None) -> None:
        self._app = app
        self._config = config
        self._keys = keys

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or is_exempt(scope.get("path", "")):
            await self._app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        credential = presented_credential(headers)
        if accepts(self._config, credential, self._keys):
            await self._app(scope, receive, send)
            return
        if credential is None:
            response = JSONResponse(
                {"error": "unauthorized", "message": "Missing credential.", "guidance": GUIDANCE},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="labagent"'},
            )
        else:
            response = JSONResponse(
                {"error": "forbidden", "message": "Credential not recognised.", "guidance": GUIDANCE},
                status_code=403,
            )
        await response(scope, receive, send)

