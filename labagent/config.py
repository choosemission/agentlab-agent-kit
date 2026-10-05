# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Everything the agent reads from its environment, in one place.

Fails closed: an agent with no inbound key does not quietly become a public
endpoint. It speaks for a person, so an open door is somebody else speaking to
them unannounced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


#: The Agent Lab's IdP. The register-your-agent recipe has every participant's
#: access point accept its tokens, so it is what a kit agent's card declares
#: unless LABAGENT_LAB_ISSUER says otherwise.
LAB_ISSUER = "https://idp.agentlab.choosemission.com/realms/mission-agent-lab"


class ConfigurationError(RuntimeError):
    """The deployment is wrong. Raised at start-up, never per request."""


@dataclass(frozen=True)
class Config:
    #: What this agent calls itself, on its agent card.
    name: str
    #: The URL other agents reach you at: your gateway's access point, not this
    #: process. Written into the agent card.
    public_url: str
    #: Accepted inbound keys: the credential your gateway injects on the last leg.
    api_keys: tuple[str, ...]
    allow_anonymous: bool
    #: Accepted owner keys: the credential your gateway injects on the owner
    #: MCP access point. Never the same as an inbound key.
    owner_keys: tuple[str, ...] = ()
    port: int = 8080
    owner_port: int = 8081
    db_path: str = "data/labagent.sqlite3"
    #: Local testing only: let a contact's url be plain http://.
    allow_http_contacts: bool = False
    #: The key your own gateway wants on the access points your contacts' URLs
    #: name, sent in `Authorization` on every outbound call. One key for all of
    #: them, because every contact is reached through your gateway first.
    outbound_key: str | None = None
    #: The Lab IdP that issues the token your access point requires: a Lab
    #: agent token, client credentials, audience `lab-agents`. Declared on the
    #: agent card so a caller knows what to present. None declares nothing.
    lab_issuer: str | None = LAB_ISSUER


def _keys(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(k.strip() for k in env.get(name, "").split(",") if k.strip())


def config_from_env(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env

    keys = _keys(env, "LABAGENT_API_KEYS")
    owner_keys = _keys(env, "LABAGENT_OWNER_KEYS")
    allow_anonymous = env.get("LABAGENT_ALLOW_ANONYMOUS") == "true"
    if not keys and not allow_anonymous:
        raise ConfigurationError(
            "No inbound authentication configured. Set LABAGENT_API_KEYS (the key your gateway "
            "injects), or LABAGENT_ALLOW_ANONYMOUS=true for local development."
        )
    if not owner_keys and not allow_anonymous:
        raise ConfigurationError(
            "No owner authentication configured. Set LABAGENT_OWNER_KEYS (the key your gateway "
            "injects on the owner MCP access point), or LABAGENT_ALLOW_ANONYMOUS=true for local development."
        )
    if any(len(k) < 32 for k in keys + owner_keys):
        raise ConfigurationError("An API key is shorter than 32 characters. Generate one with: openssl rand -hex 32")
    if set(keys) & set(owner_keys):
        raise ConfigurationError(
            "An owner key is also an inbound key, so anybody who can reach your agent could act as you. "
            "Generate a separate one with: openssl rand -hex 32"
        )

    outbound_key = env.get("LABAGENT_OUTBOUND_KEY", "").strip() or None
    if outbound_key and outbound_key in keys + owner_keys:
        raise ConfigurationError(
            "LABAGENT_OUTBOUND_KEY is also an inbound or owner key. It is sent on every outbound call, "
            "so it must not open either of your agent's doors. Use your gateway's own key for it."
        )

    port = int(env.get("LABAGENT_PORT", "8080"))
    return Config(
        name=env.get("LABAGENT_NAME", "Lab agent"),
        public_url=env.get("LABAGENT_PUBLIC_URL") or f"http://localhost:{port}",
        # Unset means the Lab's; set but empty means declare nothing.
        lab_issuer=(env.get("LABAGENT_LAB_ISSUER", LAB_ISSUER) or "").strip() or None,
        api_keys=keys,
        allow_anonymous=allow_anonymous,
        owner_keys=owner_keys,
        port=port,
        owner_port=int(env.get("LABAGENT_OWNER_PORT", "8081")),
        db_path=env.get("LABAGENT_DB", "data/labagent.sqlite3"),
        allow_http_contacts=env.get("LABAGENT_ALLOW_HTTP_CONTACTS") == "true",
        outbound_key=outbound_key,
    )
