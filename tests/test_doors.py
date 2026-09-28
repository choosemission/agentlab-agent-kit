"""M0: the public door wants the gateway's key; the owner's door wants loopback."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest

from labagent.config import ConfigurationError, config_from_env
from labagent.core import envelope

from .conftest import ALICE_KEY


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
    def test_loopback_is_answered(self, make_lab) -> None:
        assert call(make_lab().alice.admin_app, "GET", "/health").json()["ok"] is True

    def test_anything_else_is_refused(self, make_lab) -> None:
        response = call(make_lab().alice.admin_app, "GET", "/health", client=("203.0.113.9", 1))
        assert response.status_code == 403

    def test_the_gateway_key_does_not_open_it(self, make_lab) -> None:
        response = call(
            make_lab().alice.admin_app, "GET", "/health", client=("203.0.113.9", 1), headers={"x-api-key": ALICE_KEY}
        )
        assert response.status_code == 403

    def test_over_a_real_socket_it_binds_loopback(self, make_lab) -> None:
        """The CLI's actual path: uvicorn on 127.0.0.1."""
        import uvicorn

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(make_lab().alice.admin_app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(50):
                if server.started:
                    break
                time.sleep(0.05)
            assert httpx.get(f"http://127.0.0.1:{port}/health").json()["ok"] is True
        finally:
            server.should_exit = True
            thread.join(5)


class TestConfig:
    def test_fails_closed_without_a_key(self) -> None:
        with pytest.raises(ConfigurationError, match="No inbound authentication"):
            config_from_env({})

    def test_short_keys_are_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="shorter than 32"):
            config_from_env({"LABAGENT_API_KEYS": "short"})
