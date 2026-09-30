# Copyright 2026 Choose Mission Ltd
# Portions copyright Affinidi Pte. Ltd., from `affinidi-labs-tgw-get-started`
# at commit 64babfc3ef27a3b1fb73ec9c25246b032b5708c4 (`a2a/a2a_server.py`), by way
# of affinidi-lab `servers/lab-coordinator`: the agent card's shape in
# `create_agent_card`.
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Assembly: one process, two listeners.

| Listener | Who reaches it | What opens it |
| --- | --- | --- |
| public A2A (`LABAGENT_PORT`) | other agents, through YOUR gateway | the key your gateway injects there |
| owner MCP (`LABAGENT_OWNER_PORT`) | you: your coding agent, or the `labagent` CLI | the owner key, injected by a separate access point |
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
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, APIKeySecurityScheme, In, SecurityScheme
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__
from .config import Config
from .contacts import Contacts
from .core.store import Store
from .gate import ApiKeyGate
from .inbox import Executor, Inbox
from .owner.mcp import create_owner_app
from .send import TIMEOUT, Sender

log = logging.getLogger("labagent")


def create_agent_card(config: Config) -> AgentCard:
    return AgentCard(
        name=config.name,
        description=(
            f"{config.name}: a participant's own agent in the Agent Lab, built with agentlab-agent-kit. "
            "Send it a message and its owner will read it."
        ),
        url=config.public_url.rstrip("/") + "/",
        version=__version__,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
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
        skills=[
            AgentSkill(
                id="message",
                name="Leave a message",
                description="Any text. It is kept for the owner to read, and answered 'Received.'",
                tags=["inbox"],
            ),
            AgentSkill(
                id="ping",
                name="Ping",
                description="Send exactly 'ping' to check the agent is up. Answered 'pong'; nothing is kept.",
                tags=["liveness"],
            ),
        ],
    )


@dataclass
class Agent:
    config: Config
    store: Store
    inbox: Inbox
    contacts: Contacts
    sender: Sender
    public_app: Any = None
    owner_app: Any = None


def build(config: Config, *, client: httpx.AsyncClient | None = None) -> Agent:
    """Everything but the listeners. Tests call this and drive both apps in-process."""
    store = Store(config.db_path)
    agent = Agent(
        config=config,
        store=store,
        inbox=Inbox(store),
        contacts=Contacts(store, allow_http=config.allow_http_contacts),
        sender=Sender(client or httpx.AsyncClient(timeout=TIMEOUT)),
    )
    handler = DefaultRequestHandler(agent_executor=Executor(agent.inbox), task_store=InMemoryTaskStore())
    public = A2AStarletteApplication(agent_card=create_agent_card(config), http_handler=handler).build()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    public.routes.append(Route("/healthz", health, methods=["GET"]))
    agent.public_app = ApiKeyGate(public, config)
    agent.owner_app = create_owner_app(agent)
    return agent


async def serve(config: Config) -> None:
    import uvicorn

    agent = build(config)
    log.info("listening: public A2A on 0.0.0.0:%d, owner MCP on 0.0.0.0:%d/mcp", config.port, config.owner_port)
    if not config.api_keys:
        log.warning("serving without inbound authentication (LABAGENT_ALLOW_ANONYMOUS). Never behind a real gateway.")
    if not config.owner_keys:
        log.warning("serving the owner MCP without a key (LABAGENT_ALLOW_ANONYMOUS). Anyone who reaches it is you.")

    public = uvicorn.Server(uvicorn.Config(agent.public_app, host="0.0.0.0", port=config.port, log_level="info"))
    owner = uvicorn.Server(uvicorn.Config(agent.owner_app, host="0.0.0.0", port=config.owner_port, log_level="warning"))
    await asyncio.gather(public.serve(), owner.serve())
