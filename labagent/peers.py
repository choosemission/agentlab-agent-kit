# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""The people your agent knows, and how far it trusts each of them.

A peer is somebody else's agent. You add peers, through your owner tools
(`peers_add`, `peers_update`, `peers_pin`, `peers_remove`) or by seeding them
from `peers.toml`. The peer table is the only place an outbound address comes from:
nothing a counterparty sends can make this agent call somewhere new. That
closes off using it as a relay, and it makes every outbound call something you
configured.

Peers live in the agent's database, so a change takes effect at once and
survives a restart. `peers.toml` is a **seed**: at start-up each peer in it that
the database does not already have is added. It never overwrites or deletes, so
what you changed through your owner tools stays changed.

```toml
[peers.bob]
# Where to send messages for Bob. With mode = "gateway" (the default) this is
# YOUR gateway's transit point for him, which carries the call to his gateway
# and signs it on the way: Bob sees you as gateway-verified. With
# mode = "direct" it is Bob's own access point, and your message carries no
# presentation: Bob sees you as self-asserted.
url = "https://your-gateway.example/a2a/to-bob"
mode = "gateway"
# Bob's gateway DID, exchanged with him out of band. A message is labelled
# gateway-verified only when its credential's issuer is exactly this.
gateway_did = "did:webvh:Qm…:bob-gateway.example"
# The environment variable holding the credential the url wants, if it wants
# one. Never the value itself.
api_key_env = "PEER_BOB_KEY"
# The name Bob's agent calls itself. Used only to attribute a message that
# arrives WITHOUT a verifiable presentation, and only if you allow it below.
claimed_name = "Bob's agent"
# Accept Bob's messages when they are only self-asserted. Off by default.
accept_self_asserted = false

[peers.bob.accept]
"feed.subscribe" = "ask"    # ask | auto | deny
```

**Names are permanent.** Approvals, the outbox and every module's tables refer
to a peer by name, so renaming is removing and adding.

**Pinning is trust on first use.** A gateway DID is exchanged out of band and
nothing revokes it until the Lab runs a trust registry. See docs/threat-model.md.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .core.store import Store

GATEWAY, DIRECT = "gateway", "direct"
MODES = (GATEWAY, DIRECT)
POLICIES = ("ask", "auto", "deny")

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

TABLE = """CREATE TABLE IF NOT EXISTS peers (
    name TEXT PRIMARY KEY, url TEXT NOT NULL, mode TEXT NOT NULL, gateway_did TEXT UNIQUE,
    api_key_env TEXT, claimed_name TEXT, accept_self_asserted INTEGER NOT NULL,
    accept TEXT NOT NULL, source TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)"""


class PeersError(ValueError):
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
    mode: str = GATEWAY

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    def policy(self, action: str, default: str = "ask") -> str:
        value = self.accept.get(action, default)
        return value if value in POLICIES else "deny"

    def public(self) -> dict[str, Any]:
        """For the owner. Names the key variable, never the key."""
        return {**asdict(self), "accept": dict(self.accept), "key_set": self.api_key() is not None}

    def checked(self, *, allow_http: bool = False) -> "Peer":
        """This peer, if every field is one this agent will act on."""
        if not _NAME.match(self.name):
            raise PeersError(f"{self.name!r} is not a usable peer name: letters, digits, '.', '_' or '-'")
        parts = urlsplit(self.url)
        schemes = ("https", "http") if allow_http else ("https",)
        if parts.scheme not in schemes or not parts.hostname:
            raise PeersError(f"{self.name}: url must be an https:// address, not {self.url!r}")
        if parts.username or parts.password:
            raise PeersError(f"{self.name}: url must not carry credentials; name them with api_key_env")
        if self.mode not in MODES:
            raise PeersError(f"{self.name}: mode is {' or '.join(MODES)}, not {self.mode!r}")
        if self.api_key_env and not _ENV.match(self.api_key_env):
            raise PeersError(f"{self.name}: api_key_env names an environment variable, not {self.api_key_env!r}")
        if self.gateway_did and not self.gateway_did.startswith("did:"):
            raise PeersError(f"{self.name}: gateway_did must be a DID")
        bad = {k: v for k, v in self.accept.items() if v not in POLICIES}
        if bad:
            raise PeersError(f"{self.name}: accept policies are {', '.join(POLICIES)}, not {bad}")
        return self


def _from_row(row: dict[str, Any]) -> Peer:
    return Peer(
        name=row["name"],
        url=row["url"],
        mode=row["mode"],
        gateway_did=row["gateway_did"],
        api_key_env=row["api_key_env"],
        claimed_name=row["claimed_name"],
        accept_self_asserted=bool(row["accept_self_asserted"]),
        accept=json.loads(row["accept"]),
    )


def read_seed(path: str | Path) -> list[Peer]:
    """The peers in a `peers.toml`, or none if there is no file."""
    path = Path(path)
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text())
    peers = []
    for name, raw in (data.get("peers") or {}).items():
        if not isinstance(raw, dict) or not isinstance(raw.get("url"), str):
            raise PeersError(f"peer {name!r} needs a url")
        peers.append(
            Peer(
                name=name,
                url=raw["url"],
                mode=str(raw.get("mode", GATEWAY)),
                gateway_did=raw.get("gateway_did"),
                api_key_env=raw.get("api_key_env"),
                claimed_name=raw.get("claimed_name"),
                accept_self_asserted=bool(raw.get("accept_self_asserted", False)),
                accept={str(k): str(v) for k, v in (raw.get("accept") or {}).items()},
            )
        )
    return peers


class Peers:
    """The peer table. Every read goes to the database, so a change made through
    the owner tools is what the next message sees."""

    def __init__(self, store: Store, *, allow_http: bool = False) -> None:
        self._store = store
        self._allow_http = allow_http
        store.migrate([TABLE])

    @classmethod
    def of(cls, peers: list[Peer]) -> "Peers":
        """An in-memory peer table: for tests and offline tools."""
        table = cls(Store(":memory:"), allow_http=True)
        table.seed(peers)
        return table

    def seed(self, peers: list[Peer]) -> list[str]:
        """Add each peer the table does not have by name. Never overwrites.
        Returns the names added."""
        added = []
        for peer in peers:
            if self.get(peer.name) is None:
                self._insert(peer, source="seed")
                added.append(peer.name)
        return added

    def add(self, peer: Peer) -> Peer:
        if self.get(peer.name) is not None:
            raise PeersError(f"there is already a peer called {peer.name!r}; update it, or remove it first")
        return self._insert(peer, source="owner")

    def update(self, name: str, /, **changes: Any) -> Peer:
        current = self.get(name)
        if current is None:
            raise PeersError(f"no peer called {name!r}")
        if "name" in changes:
            raise PeersError("a peer's name is permanent; remove it and add it again")
        # An empty string clears an optional field: that is how a pin is dropped.
        optional = ("gateway_did", "api_key_env", "claimed_name")
        changes = {k: (None if k in optional and v == "" else v) for k, v in changes.items()}
        peer = replace(current, **changes).checked(allow_http=self._allow_http)
        row = self._row(peer)
        try:
            self._store.execute(
                "UPDATE peers SET url=?, mode=?, gateway_did=?, api_key_env=?, claimed_name=?, "
                "accept_self_asserted=?, accept=?, updated_at=? WHERE name=?",
                (row["url"], row["mode"], row["gateway_did"], row["api_key_env"], row["claimed_name"],
                 row["accept_self_asserted"], row["accept"], time.time(), name),
            )
        except sqlite3.IntegrityError as error:
            raise self._conflict(peer) from error
        return peer

    def remove(self, name: str) -> bool:
        return self._store.changes("DELETE FROM peers WHERE name=?", (name,)) == 1

    def get(self, name: str | None) -> Peer | None:
        if not name:
            return None
        row = self._store.one("SELECT * FROM peers WHERE name=?", (name,))
        return _from_row(row) if row else None

    def by_gateway_did(self, did: str | None) -> Peer | None:
        if not did:
            return None
        row = self._store.one("SELECT * FROM peers WHERE gateway_did=?", (did,))
        return _from_row(row) if row else None

    def by_claimed_name(self, claimed: str | None) -> Peer | None:
        if not claimed:
            return None
        row = self._store.one("SELECT * FROM peers WHERE claimed_name=? ORDER BY name LIMIT 1", (claimed,))
        return _from_row(row) if row else None

    def all(self) -> list[Peer]:
        return [_from_row(r) for r in self._store.all("SELECT * FROM peers ORDER BY name")]

    def _insert(self, peer: Peer, *, source: str) -> Peer:
        peer = peer.checked(allow_http=self._allow_http)
        row, now = self._row(peer), time.time()
        try:
            self._store.execute(
                "INSERT INTO peers (name, url, mode, gateway_did, api_key_env, claimed_name, "
                "accept_self_asserted, accept, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (peer.name, row["url"], row["mode"], row["gateway_did"], row["api_key_env"], row["claimed_name"],
                 row["accept_self_asserted"], row["accept"], source, now, now),
            )
        except sqlite3.IntegrityError as error:
            raise self._conflict(peer) from error
        return peer

    def _conflict(self, peer: Peer) -> PeersError:
        # One gateway, one peer. Two names on one DID would make every verified
        # message ambiguous about who sent it.
        holder = self.by_gateway_did(peer.gateway_did)
        return PeersError(f"{peer.gateway_did} is already pinned for {holder.name if holder else 'another peer'}")

    @staticmethod
    def _row(peer: Peer) -> dict[str, Any]:
        return {
            "url": peer.url,
            "mode": peer.mode,
            "gateway_did": peer.gateway_did or None,
            "api_key_env": peer.api_key_env or None,
            "claimed_name": peer.claimed_name or None,
            "accept_self_asserted": int(peer.accept_self_asserted),
            "accept": json.dumps(peer.accept, sort_keys=True),
        }
