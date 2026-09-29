"""Two agents, two stand-in gateways, one in-process network.

    alice ──► alice-gw.test/to/bob ──(signs as alice's gateway, injects bob's key)──► bob.test
    bob   ──► bob-gw.test/to/alice ──(signs as bob's gateway, injects alice's key)──► alice.test

Nothing is mocked between them: real A2A apps, real JSON-RPC, real proofs,
verification on. The network is an httpx transport that routes by host to ASGI
apps, and can take a host "down" to exercise the outbox.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from harness.pretend_gateway import create_gateway_app
from labagent.app import Agent, build
from labagent.config import Config
from labagent.peers import Peer, Peers
from labagent.trust.resolve import Resolver
from labagent.trust.testing import TestGateway, fetcher_for

ALICE_KEY = "a" * 64
BOB_KEY = "b" * 64
ALICE_OWNER_KEY = "o" * 32 + "a" * 32
BOB_OWNER_KEY = "o" * 32 + "b" * 32
OWNER_KEYS = {"alice": ALICE_OWNER_KEY, "bob": BOB_OWNER_KEY}


class Network(httpx.AsyncBaseTransport):
    """Routes by host to ASGI apps. `down` holds hosts that refuse connections."""

    def __init__(self) -> None:
        self.apps: dict[str, Any] = {}
        self.down: set[str] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.down or host not in self.apps:
            raise httpx.ConnectError(f"{host} is unreachable", request=request)
        transport = httpx.ASGITransport(app=self.apps[host], client=("127.0.0.1", 40000))
        return await transport.handle_async_request(request)


@dataclass
class Lab:
    network: Network
    alice: Agent
    bob: Agent
    alice_gw: TestGateway
    bob_gw: TestGateway
    tmp: Path

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.network, timeout=10)

    async def flush(self, *agents: Agent) -> int:
        """One outbox pass on each agent, ignoring backoff."""
        total = 0
        for agent in agents or (self.alice, self.bob):
            agent.ctx.store.execute("UPDATE outbox SET next_at=0 WHERE status='pending'")
            total += await agent.ctx.outbox.run_once()
        return total

    async def rpc(self, agent: Agent, method: str, params: dict | None = None, *, key: str | None = "owner") -> httpx.Response:
        """One JSON-RPC message to an agent's owner MCP server, with its owner key by default."""
        if key == "owner":
            key = agent.ctx.config.owner_keys[0]
        transport = httpx.ASGITransport(app=agent.owner_app, client=("203.0.113.9", 50000))
        headers = {"x-api-key": key} if key else {}
        message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        async with httpx.AsyncClient(transport=transport, base_url="http://owner") as c:
            return await c.post("/mcp", json=message, headers=headers)

    async def owner(self, agent: Agent, tool: str, arguments: dict | None = None) -> dict:
        """Call an owner tool as the owner's coding agent would, and return its result."""
        answer = (await self.rpc(agent, "tools/call", {"name": tool, "arguments": arguments or {}})).json()
        assert "error" not in answer, answer
        return answer["result"]["structuredContent"]

    def restart(self, name: str, **peer_overrides: Any) -> Agent:
        """Rebuild an agent from the same database file, as a process restart would."""
        old: Agent = getattr(self, name)
        agent = make_agent(old.ctx.config, old.ctx.peers, self)
        setattr(self, name, agent)
        self.network.apps[f"{name}.test"] = agent.public_app
        return agent


def config(name: str, key: str, tmp: Path) -> Config:
    return Config(
        name=f"{name.title()}'s agent",
        role="participant representative",
        public_url=f"https://{name}-gw.test/a2a/",
        api_keys=(key,),
        owner_keys=(OWNER_KEYS[name],),
        allow_anonymous=False,
        db_path=str(tmp / f"{name}.sqlite3"),
        peers_path=str(tmp / "unused.toml"),
    )


def make_agent(cfg: Config, peers: Peers, lab_or_net: Any) -> Agent:
    if isinstance(lab_or_net, Lab):
        network, gateways = lab_or_net.network, (lab_or_net.alice_gw, lab_or_net.bob_gw)
    else:
        network, gateways = lab_or_net["network"], lab_or_net["gateways"]
    return build(
        cfg,
        peers=peers,
        client=httpx.AsyncClient(transport=network, timeout=10),
        resolver=Resolver(fetcher=fetcher_for(*gateways)),
    )


def peer(name: str, gateway: TestGateway, **extra: Any) -> Peer:
    via = "alice-gw.test" if name == "bob" else "bob-gw.test"
    return Peer(
        name=name,
        url=f"http://{via}/to/{name}",
        gateway_did=gateway.gateway_did,
        claimed_name=f"{name.title()}'s agent",
        **extra,
    )


@pytest.fixture
def make_lab(tmp_path: Path):
    """Build the two-agent lab. Keyword arguments adjust the topology:

    - `alice_peers` / `bob_peers`: override each side's view of the other;
    - `alice_signs` / `bob_signs`: whether each stand-in gateway attaches a presentation.
    """

    def _make(
        *,
        alice_peers: list[Peer] | None = None,
        bob_peers: list[Peer] | None = None,
        alice_signs: bool = True,
        bob_signs: bool = True,
    ) -> Lab:
        network = Network()
        alice_gw, bob_gw = TestGateway("alice", "alice-gw.test"), TestGateway("bob", "bob-gw.test")
        ctx = {"network": network, "gateways": (alice_gw, bob_gw)}

        alice = make_agent(
            config("alice", ALICE_KEY, tmp_path),
            Peers(alice_peers if alice_peers is not None else [peer("bob", bob_gw)]),
            ctx,
        )
        bob = make_agent(
            config("bob", BOB_KEY, tmp_path),
            Peers(bob_peers if bob_peers is not None else [peer("alice", alice_gw)]),
            ctx,
        )
        gw_client = httpx.AsyncClient(transport=network, timeout=10)
        network.apps.update(
            {
                "alice.test": alice.public_app,
                "bob.test": bob.public_app,
                "alice-gw.test": create_gateway_app(
                    alice_gw, {"bob": {"url": "http://bob.test/", "key": BOB_KEY}}, client=gw_client, sign=alice_signs
                ),
                "bob-gw.test": create_gateway_app(
                    bob_gw, {"alice": {"url": "http://alice.test/", "key": ALICE_KEY}}, client=gw_client, sign=bob_signs
                ),
            }
        )
        return Lab(network, alice, bob, alice_gw, bob_gw, tmp_path)

    return _make


__all__ = ["Lab", "peer", "config", "replace", "ALICE_KEY", "BOB_KEY", "ALICE_OWNER_KEY", "BOB_OWNER_KEY"]
