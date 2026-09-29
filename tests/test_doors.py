"""The public door wants the inbound key; the owner's door wants the owner key; neither opens the other."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest

from labagent.config import ConfigurationError, config_from_env
from labagent.core import envelope

from .conftest import ALICE_KEY, ALICE_OWNER_KEY


def call(app, method: str, path: str, *, client=("127.0.0.1", 1), **kw) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as c:
            return await c.request(method, path, **kw)

    return asyncio.run(go())


SEND = envelope.request(envelope.body("ping"), {"name": "anyone"})


class TestPublicDoor:
    def test_message_send_without_the_key_is_401(self, make_lab) -> None:
        response = call(make_lab().alice.public_app, "POST", "/", json=SEND)
        assert response.status_code == 401
        assert "gateway" in response.json()["guidance"]

    def test_a_wrong_key_is_403(self, make_lab) -> None:
        response = call(make_lab().alice.public_app, "POST", "/", json=SEND, headers={"x-api-key": "z" * 64})
        assert response.status_code == 403

    def test_the_right_key_is_answered(self, make_lab) -> None:
        response = call(make_lab().alice.public_app, "POST", "/", json=SEND, headers={"x-api-key": ALICE_KEY})
        assert response.status_code == 200
        reply = envelope.reply_payload(response.json())
        # Reached directly, not through a gateway: answered, and labelled so.
        assert reply["seen_as"] == "self-asserted"

    def test_the_card_is_served_without_the_key(self, make_lab) -> None:
        card = call(make_lab().alice.public_app, "GET", "/.well-known/agent-card.json").json()
        assert card["name"] == "Alice's agent"
        assert {s["id"] for s in card["skills"]} >= {"ping", "feed.subscribe", "feed.deliver"}
        assert card["url"] == "https://alice-gw.test/a2a/"

    def test_health_needs_no_key(self, make_lab) -> None:
        assert call(make_lab().alice.public_app, "GET", "/healthz").status_code == 200


class TestOwnerDoor:
    """The owner MCP server opens to the owner key and nothing else, wherever
    the call comes from: it is reached through a gateway, not by being local."""

    def rpc(self, lab, key):
        return asyncio.run(lab.rpc(lab.alice, "tools/list", key=key))

    def test_the_owner_key_is_answered(self, make_lab) -> None:
        response = self.rpc(make_lab(), "owner")
        assert response.status_code == 200
        assert "approve" in {t["name"] for t in response.json()["result"]["tools"]}

    def test_no_key_is_401(self, make_lab) -> None:
        assert self.rpc(make_lab(), None).status_code == 401

    def test_a_wrong_key_is_403(self, make_lab) -> None:
        assert self.rpc(make_lab(), "z" * 64).status_code == 403

    def test_the_inbound_key_does_not_open_it(self, make_lab) -> None:
        """An agent that can reach you cannot act as you."""
        assert self.rpc(make_lab(), ALICE_KEY).status_code == 403

    def test_the_owner_key_does_not_open_the_public_door(self, make_lab) -> None:
        lab = make_lab()
        response = call(lab.alice.public_app, "POST", "/", json=SEND, headers={"x-api-key": ALICE_OWNER_KEY})
        assert response.status_code == 403

    def test_over_a_real_socket(self, make_lab) -> None:
        """The CLI's actual path: uvicorn, a real port, the key in a header."""
        import uvicorn

        lab = make_lab()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(lab.alice.owner_app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(50):
                if server.started:
                    break
                time.sleep(0.05)
            message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "health"}}
            url = f"http://127.0.0.1:{port}/mcp"
            assert httpx.post(url, json=message).status_code == 401
            answer = httpx.post(url, json=message, headers={"x-api-key": ALICE_OWNER_KEY}).json()
            assert answer["result"]["structuredContent"]["ok"] is True
        finally:
            server.should_exit = True
            thread.join(5)


class TestConfig:
    def test_fails_closed_without_a_key(self) -> None:
        with pytest.raises(ConfigurationError, match="No inbound authentication"):
            config_from_env({})

    def test_short_keys_are_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="shorter than 32"):
            config_from_env({"LABAGENT_API_KEYS": "short", "LABAGENT_OWNER_KEYS": "o" * 64})

    def test_fails_closed_without_an_owner_key(self) -> None:
        with pytest.raises(ConfigurationError, match="No owner authentication"):
            config_from_env({"LABAGENT_API_KEYS": "a" * 64})

    def test_an_owner_key_may_not_be_an_inbound_key(self) -> None:
        with pytest.raises(ConfigurationError, match="also an inbound key"):
            config_from_env({"LABAGENT_API_KEYS": "a" * 64, "LABAGENT_OWNER_KEYS": "a" * 64})
