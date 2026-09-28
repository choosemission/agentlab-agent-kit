# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
#
# Adapted from affinidi-lab `servers/lab-coordinator/coordinator/{auth,app}.py`.
"""Two doors, and neither opens the other.

**The public door** is the A2A endpoint. Your gateway is the only thing that
should reach it, and it injects a key on the last leg; anything arriving without
that key came round the gateway, and is refused. Closing that ungoverned path is
the point of having a gateway at all.

**The owner's door** is the admin API, and it opens only to loopback. It is on a
different port, bound to 127.0.0.1, and refuses any connection that is not from
loopback even if somebody binds it wider. Nothing about it is routed through the
gateway: the gateway is for other people's agents, and this is you.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
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


def accepts(config: Config, credential: str | None) -> bool:
    if config.allow_anonymous and not config.api_keys:
        return True
    if credential is None:
        return False
    actual = _digest(credential)
    return any(hmac.compare_digest(_digest(key), actual) for key in config.api_keys)


def is_exempt(path: str) -> bool:
    return path in HEALTH_PATHS or path in CARD_PATHS


class ApiKeyGate:
    """Pure ASGI, not `BaseHTTPMiddleware`: the A2A app streams SSE, and a
    middleware that buffered responses would hold those open."""

    def __init__(self, app: Any, config: Config) -> None:
        self._app = app
        self._config = config

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or is_exempt(scope.get("path", "")):
            await self._app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        credential = presented_credential(headers)
        if accepts(self._config, credential):
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


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Starlette's TestClient reports "testclient"; nothing real does.
        return False


class LoopbackOnly:
    """The owner's door. Refuses any peer that is not loopback, whatever the
    bind address says — so a `0.0.0.0` typo is a refusal, not an exposure."""

    def __init__(self, app: Any, *, trust: tuple[str, ...] = ()) -> None:
        self._app = app
        self._trust = trust  # tests only: pseudo-hosts to treat as loopback

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            client = scope.get("client")
            host = client[0] if client else None
            if not (is_loopback(host) or host in self._trust):
                response = JSONResponse(
                    {"error": "forbidden", "message": "The owner API answers loopback only."}, status_code=403
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)
