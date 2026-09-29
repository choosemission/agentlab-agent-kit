# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Everything the agent reads from its environment, in one place.

Fails closed: an agent with no inbound key does not quietly become a public
endpoint. It speaks for a person, so an open door is somebody else speaking to
them unannounced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


class ConfigurationError(RuntimeError):
    """The deployment is wrong. Raised at start-up, never per request."""


@dataclass(frozen=True)
class Config:
    #: What this agent calls itself. Sent, unsigned, in every outbound message;
    #: your gateway's Identity element signs the fields you mark from it.
    name: str
    role: str
    #: The URL other agents reach you at: your gateway's access point, not this
    #: process. Written into the agent card.
    public_url: str
    #: Accepted inbound keys: the credential your gateway injects on the last leg.
    api_keys: tuple[str, ...]
    allow_anonymous: bool
    port: int = 8080
    admin_host: str = "127.0.0.1"
    admin_port: int = 8081
    db_path: str = "data/labagent.sqlite3"
    peers_path: str = "peers.toml"
    modules: tuple[str, ...] = ("ping", "feed")
    #: Verify gateway presentations. Without it nothing is ever labelled
    #: gateway-verified, which is the honest consequence rather than a mode.
    verify: bool = True
    #: Require the signing key to be listed under the proof's purpose in the DID
    #: document. Off because no Affinidi gateway credential passes it today.
    strict_proof_purpose: bool = False
    #: Harness only: DID host → base URL, to resolve DID logs over plain HTTP.
    resolve_hosts: Mapping[str, str] = field(default_factory=dict)
    #: Seconds between outbox passes.
    outbox_interval: float = 2.0
    #: Measurement only: write every request that gets past the key gate, as it
    #: arrived, to one file each in this directory. Off unless set. See
    #: `labagent/capture.py` and deploy/DEPLOY.md.
    capture_dir: str | None = None


def _host_map(raw: str) -> dict[str, str]:
    out = {}
    for item in raw.split(","):
        if "=" in item:
            host, base = item.split("=", 1)
            out[host.strip()] = base.strip()
    return out


def config_from_env(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env

    keys = tuple(k.strip() for k in env.get("LABAGENT_API_KEYS", "").split(",") if k.strip())
    allow_anonymous = env.get("LABAGENT_ALLOW_ANONYMOUS") == "true"
    if not keys and not allow_anonymous:
        raise ConfigurationError(
            "No inbound authentication configured. Set LABAGENT_API_KEYS (the key your gateway "
            "injects), or LABAGENT_ALLOW_ANONYMOUS=true for local development."
        )
    if any(len(k) < 32 for k in keys):
        raise ConfigurationError("An API key is shorter than 32 characters. Generate one with: openssl rand -hex 32")

    port = int(env.get("LABAGENT_PORT", "8080"))
    return Config(
        name=env.get("LABAGENT_NAME", "Lab agent"),
        role=env.get("LABAGENT_ROLE", "participant representative"),
        public_url=env.get("LABAGENT_PUBLIC_URL") or f"http://localhost:{port}",
        api_keys=keys,
        allow_anonymous=allow_anonymous,
        port=port,
        admin_host=env.get("LABAGENT_ADMIN_HOST", "127.0.0.1"),
        admin_port=int(env.get("LABAGENT_ADMIN_PORT", "8081")),
        db_path=env.get("LABAGENT_DB", "data/labagent.sqlite3"),
        peers_path=env.get("LABAGENT_PEERS", "peers.toml"),
        modules=tuple(m.strip() for m in env.get("LABAGENT_MODULES", "ping,feed").split(",") if m.strip()),
        verify=env.get("LABAGENT_VERIFY") != "false",
        strict_proof_purpose=env.get("LABAGENT_STRICT_PROOF_PURPOSE") == "true",
        resolve_hosts=_host_map(env.get("LABAGENT_RESOLVE_HOSTS", "")),
        outbox_interval=float(env.get("LABAGENT_OUTBOX_INTERVAL", "2")),
        capture_dir=env.get("LABAGENT_CAPTURE_DIR") or None,
    )
