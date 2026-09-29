# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Calling a peer, at the address you configured for them and nothing else.

The URL is the peer's `url`, which only the owner sets (`peers.py`). The message
carries only your agent's unsigned self-description. In mode `gateway` the URL
is your gateway's transit point for the peer, and your gateway's Identity
element turns that into a signed presentation on the way out. In mode `direct`
it is the peer's own access point, and nothing signs it: they see you as
self-asserted. Either way this process never holds a gateway key.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..peers import Peer
from . import envelope

TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class OutboundError(RuntimeError):
    pass


class Outbound:
    def __init__(self, identity: dict[str, str], client: httpx.AsyncClient | None = None) -> None:
        self._identity = dict(identity)
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def send(self, peer: Peer, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one payload; return the peer's reply payload. Raises OutboundError."""
        headers = {"content-type": "application/json"}
        key = peer.api_key()
        if key:
            headers["x-api-key"] = key
        try:
            response = await self._client.post(
                peer.url, json=envelope.request(payload, self._identity), headers=headers
            )
        except httpx.HTTPError as error:
            raise OutboundError(f"{peer.name}: {type(error).__name__}: {error}") from error
        if response.status_code != 200:
            raise OutboundError(f"{peer.name}: HTTP {response.status_code} {response.text[:200]}")
        try:
            return envelope.reply_payload(response.json())
        except (ValueError, envelope.EnvelopeError) as error:
            raise OutboundError(f"{peer.name}: {error}") from error

    async def aclose(self) -> None:
        await self._client.aclose()
