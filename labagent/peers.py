# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""The people your agent knows, and how far it trusts each of them.

A peer is somebody else's agent. `peers.toml` is written by you, and it is the
only place an outbound address comes from: nothing a counterparty sends can make
this agent call somewhere new. That closes off using it as a relay, and it makes
every outbound call something you configured.

```toml
[peers.bob]
# Where to send messages for Bob: YOUR gateway's transit point for him, which
# carries the call to his gateway. Not Bob's agent directly.
url = "https://your-gateway.example/a2a/to-bob"
# Bob's gateway DID, exchanged with him out of band. A message is labelled
# gateway-verified only when its credential's issuer is exactly this.
gateway_did = "did:webvh:Qm…:bob-gateway.example"
# The environment variable holding the credential your transit point wants,
# if it wants one. Never the value itself.
api_key_env = "PEER_BOB_KEY"
# The name Bob's agent calls itself. Used only to attribute a message that
# arrives WITHOUT a verifiable presentation, and only if you allow it below.
claimed_name = "Bob's agent"
# Accept Bob's messages when they are only self-asserted. Off by default.
accept_self_asserted = false

[peers.bob.accept]
"feed.subscribe" = "ask"    # ask | auto | deny
```

**Pinning is trust on first use.** A gateway DID is exchanged out of band and
nothing revokes it until the Lab runs a trust registry. See docs/threat-model.md.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PeersError(RuntimeError):
    pass


@dataclass(frozen=True)
class Peer:
    name: str
    url: str
    gateway_did: str | None = None
    api_key_env: str | None = None
    claimed_name: str | None = None
    accept_self_asserted: bool = False
    accept: dict[str, str] = field(default_factory=dict)

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    def policy(self, action: str, default: str = "ask") -> str:
        value = self.accept.get(action, default)
        return value if value in ("ask", "auto", "deny") else "deny"

    def public(self) -> dict[str, Any]:
        """For the owner's CLI. Names the key variable, never the key."""
        return {
            "name": self.name,
            "url": self.url,
            "gateway_did": self.gateway_did,
            "api_key_env": self.api_key_env,
            "claimed_name": self.claimed_name,
            "accept_self_asserted": self.accept_self_asserted,
            "accept": dict(self.accept),
        }


class Peers:
    def __init__(self, peers: list[Peer]) -> None:
        self._by_name = {p.name: p for p in peers}
        dids = [p.gateway_did for p in peers if p.gateway_did]
        if len(dids) != len(set(dids)):
            # One gateway, one peer. Two names on one DID would make every
            # verified message ambiguous about who sent it.
            raise PeersError("two peers pin the same gateway_did")

    @classmethod
    def load(cls, path: str | Path) -> "Peers":
        path = Path(path)
        if not path.exists():
            return cls([])
        data = tomllib.loads(path.read_text())
        peers = []
        for name, raw in (data.get("peers") or {}).items():
            if not isinstance(raw, dict) or not isinstance(raw.get("url"), str):
                raise PeersError(f"peer {name!r} needs a url")
            peers.append(
                Peer(
                    name=name,
                    url=raw["url"],
                    gateway_did=raw.get("gateway_did"),
                    api_key_env=raw.get("api_key_env"),
                    claimed_name=raw.get("claimed_name"),
                    accept_self_asserted=bool(raw.get("accept_self_asserted", False)),
                    accept={str(k): str(v) for k, v in (raw.get("accept") or {}).items()},
                )
            )
        return cls(peers)

    def get(self, name: str) -> Peer | None:
        return self._by_name.get(name)

    def by_gateway_did(self, did: str | None) -> Peer | None:
        if not did:
            return None
        return next((p for p in self._by_name.values() if p.gateway_did == did), None)

    def by_claimed_name(self, claimed: str | None) -> Peer | None:
        if not claimed:
            return None
        return next((p for p in self._by_name.values() if p.claimed_name == claimed), None)

    def all(self) -> list[Peer]:
        return list(self._by_name.values())
