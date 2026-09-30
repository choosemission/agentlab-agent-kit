"""Two agents on one in-process network.

    alice ──(contact "bob": http://bob.test/, key from BOB_KEY_FOR_ALICE)──► bob
    bob   ──(contact "alice": http://alice.test/, key from ALICE_KEY_FOR_BOB)──► alice

In a deployment each contact URL is an access point on the sender's own
gateway; here the key a gateway would add is held in an environment variable
the contact names. Nothing is mocked between them: real A2A apps, real
JSON-RPC. The network is an httpx transport that routes by host to ASGI apps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from labagent.app import Agent, build
from labagent.config import Config
from labagent.contacts import Contact

ALICE_KEY = "a" * 64
BOB_KEY = "b" * 64
ALICE_OWNER_KEY = "o" * 32 + "a" * 32
BOB_OWNER_KEY = "o" * 32 + "b" * 32


class Network(httpx.AsyncBaseTransport):
    """Routes by host to ASGI apps. `down` holds hosts that refuse connections."""

    def __init__(self) -> None:
        self.apps: dict[str, Any] = {}
        self.down: set[str] = set()
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
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

    async def rpc(self, agent: Agent, method: str, params: dict | None = None, *, key: str | None = "owner") -> httpx.Response:
        """One JSON-RPC message to an agent's owner MCP server, with its owner key by default."""
        if key == "owner":
            key = agent.config.owner_keys[0]
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

    def restart(self, name: str) -> Agent:
        """Rebuild an agent from the same database file, as a process restart would."""
        old: Agent = getattr(self, name)
        agent = build(old.config, client=httpx.AsyncClient(transport=self.network, timeout=10))
        setattr(self, name, agent)
        self.network.apps[f"{name}.test"] = agent.public_app
        return agent


def config(name: str, key: str, owner_key: str, tmp: Path) -> Config:
    return Config(
        name=f"{name.title()}'s agent",
        public_url=f"https://{name}-gw.test/a2a/",
        api_keys=(key,),
        owner_keys=(owner_key,),
        allow_anonymous=False,
        db_path=str(tmp / f"{name}.sqlite3"),
        allow_http_contacts=True,
    )


@pytest.fixture
def lab(tmp_path: Path, monkeypatch) -> Lab:
    """Alice and Bob, each with the other as a contact."""
    monkeypatch.setenv("BOB_KEY_FOR_ALICE", BOB_KEY)
    monkeypatch.setenv("ALICE_KEY_FOR_BOB", ALICE_KEY)
    network = Network()
    client = httpx.AsyncClient(transport=network, timeout=10)
    alice = build(config("alice", ALICE_KEY, ALICE_OWNER_KEY, tmp_path), client=client)
    bob = build(config("bob", BOB_KEY, BOB_OWNER_KEY, tmp_path), client=client)
    alice.contacts.add(Contact("bob", "http://bob.test/", "BOB_KEY_FOR_ALICE"))
    bob.contacts.add(Contact("alice", "http://alice.test/", "ALICE_KEY_FOR_BOB"))
    network.apps.update({"alice.test": alice.public_app, "bob.test": bob.public_app})
    return Lab(network, alice, bob)
