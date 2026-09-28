# Copyright 2026 Choose Mission Ltd
# Portions copyright Affinidi Pte. Ltd., from `affinidi-labs-tgw-get-started`
# at commit 64babfc3ef27a3b1fb73ec9c25246b032b5708c4 (`a2a/a2a_server.py`), by way
# of affinidi-lab `servers/lab-coordinator`: the agent card's shape and the
# identity extension URI in `create_agent_card`.
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Assembly: one process, two listeners, one worker.

| Listener | Who reaches it | What opens it |
| --- | --- | --- |
| public A2A (`LABAGENT_PORT`) | other agents, through YOUR gateway | the key your gateway injects |
| owner API (`LABAGENT_ADMIN_PORT`) | you, via `labagent` CLI | being on loopback |

The outbox worker delivers queued messages to peers in the background.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    APIKeySecurityScheme,
    In,
    SecurityScheme,
)
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__
from .admin.api import create_admin_app
from .config import Config
from .core.approvals import Approvals
from .core.capability import Context
from .core.executor import Executor
from .core.outbound import TIMEOUT, Outbound
from .core.outbox import Outbox
from .core.registry import Registry
from .core.store import Store
from .gate import ApiKeyGate
from .peers import Peers
from .trust.identity import SELF_ASSERTED_EXTENSION
from .trust.resolve import Resolver
from .trust.verify import Verifier

log = logging.getLogger("labagent")


def identity_of(config: Config) -> dict[str, str]:
    """The self-description sent with every message. Mark `name` in your
    gateway's Identity element; do not mark `version`, or your agent's DID moves
    on every upgrade."""
    return {"name": config.name, "role": config.role, "model": "none", "version": __version__}


def create_agent_card(config: Config, registry: Registry) -> AgentCard:
    return AgentCard(
        name=config.name,
        description=(
            f"{config.name}: a participant's own agent in the Agent Lab, built with agentlab-agent-kit. "
            "It speaks for one person, asks them before committing them to anything, and labels every "
            "message it receives as gateway-verified or self-asserted."
        ),
        url=config.public_url.rstrip("/") + "/",
        version=__version__,
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(
            streaming=False,
            extensions=[
                AgentExtension(
                    uri=SELF_ASSERTED_EXTENSION,
                    description="Supports agent identity exchange",
                    required=False,
                    params=identity_of(config),
                )
            ],
        ),
        security_schemes={
            "gatewayApiKey": SecurityScheme(
                root=APIKeySecurityScheme(
                    name="x-api-key",
                    in_=In.header,
                    description="Injected by the owner's gateway on the last leg. Callers do not hold it.",
                )
            )
        },
        security=[{"gatewayApiKey": []}],
        skills=registry.skills(),
    )


@dataclass
class Agent:
    ctx: Context
    registry: Registry
    public_app: Any
    admin_app: Any


def build(
    config: Config,
    *,
    peers: Peers | None = None,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver | None = None,
    admin_trust: tuple[str, ...] = (),
) -> Agent:
    """Everything but the listeners. Tests call this and drive both apps in-process."""
    store = Store(config.db_path)
    registry = Registry(config.modules)
    store.migrate(registry.migrations())
    peers = peers if peers is not None else Peers.load(config.peers_path)

    verifier = None
    if config.verify:
        verifier = Verifier(
            resolver
            or Resolver(host_map=config.resolve_hosts, strict_purpose=config.strict_proof_purpose)
        )

    outbound = Outbound(identity_of(config), client or httpx.AsyncClient(timeout=TIMEOUT))
    ctx = Context(
        config=config,
        store=store,
        peers=peers,
        approvals=Approvals(store),
        outbound=outbound,
        outbox=Outbox(store, peers, outbound),
    )

    handler = DefaultRequestHandler(
        agent_executor=Executor(ctx, registry, verifier, identity_of(config)),
        task_store=InMemoryTaskStore(),
    )
    public = A2AStarletteApplication(agent_card=create_agent_card(config, registry), http_handler=handler).build()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    public.routes.append(Route("/healthz", health, methods=["GET"]))
    return Agent(ctx, registry, ApiKeyGate(public, config), create_admin_app(ctx, registry, trust=admin_trust))


async def serve(config: Config) -> None:
    import uvicorn

    agent = build(config)
    log.info(
        "listening: public A2A on 0.0.0.0:%d, owner API on %s:%d, modules %s, signatures %s",
        config.port,
        config.admin_host,
        config.admin_port,
        ",".join(config.modules),
        "verified" if config.verify else "NOT VERIFIED — nothing will be labelled gateway-verified",
    )
    if not config.api_keys:
        log.warning("serving without inbound authentication (LABAGENT_ALLOW_ANONYMOUS). Never behind a real gateway.")
    if config.resolve_hosts:
        log.warning("DID resolution is redirected for %s — harness only", ", ".join(config.resolve_hosts))

    public = uvicorn.Server(uvicorn.Config(agent.public_app, host="0.0.0.0", port=config.port, log_level="info"))
    admin = uvicorn.Server(
        uvicorn.Config(agent.admin_app, host=config.admin_host, port=config.admin_port, log_level="warning")
    )
    worker = asyncio.create_task(agent.ctx.outbox.run_forever(config.outbox_interval))
    try:
        await asyncio.gather(public.serve(), admin.serve())
    finally:
        worker.cancel()
