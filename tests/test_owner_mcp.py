"""The owner's MCP server speaks the protocol, and the CLI drives the same tools."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from labagent.owner import cli
from labagent.owner.mcp import INSTRUCTIONS, PROTOCOL_VERSIONS


def rpc(lab, method, params=None, **kw) -> dict:
    return asyncio.run(lab.rpc(lab.alice, method, params, **kw)).json()


def raw(lab, method: str, **kw) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=lab.alice.owner_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://owner") as c:
            return await c.request(method, "/mcp", headers={"x-api-key": lab.alice.config.owner_keys[0]}, **kw)

    return asyncio.run(go())


class TestProtocol:
    def test_initialize_negotiates_a_version_and_gives_instructions(self, lab) -> None:
        known = rpc(lab, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        assert known["protocolVersion"] == "2025-06-18"
        assert known["capabilities"] == {"tools": {"listChanged": False}}
        assert known["instructions"] == INSTRUCTIONS
        unknown = rpc(lab, "initialize", {"protocolVersion": "1999-01-01"})["result"]
        assert unknown["protocolVersion"] == PROTOCOL_VERSIONS[-1]

    def test_a_notification_is_accepted_without_an_answer(self, lab) -> None:
        assert raw(lab, "POST", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202

    def test_there_is_no_stream_to_open(self, lab) -> None:
        assert raw(lab, "GET").status_code == 405

    def test_an_unknown_method_is_method_not_found(self, lab) -> None:
        assert rpc(lab, "resources/list")["error"]["code"] == -32601

    def test_schemas(self, lab) -> None:
        tools = {t["name"]: t for t in rpc(lab, "tools/list")["result"]["tools"]}
        assert tools["send"]["inputSchema"]["required"] == ["to", "text"]
        assert tools["inbox"]["inputSchema"]["properties"]["limit"]["type"] == "integer"

    def test_arguments_are_checked(self, lab) -> None:
        for arguments in ({}, {"to": 3}, {"to": "bob", "text": "x", "extra": 1}):
            answer = rpc(lab, "tools/call", {"name": "send", "arguments": arguments})
            assert answer["error"]["code"] == -32602, arguments
        assert rpc(lab, "tools/call", {"name": "nope"})["error"]["code"] == -32602

    def test_a_refusal_is_a_tool_error_not_a_protocol_error(self, lab) -> None:
        answer = rpc(lab, "tools/call", {"name": "ping", "arguments": {"to": "nobody"}})["result"]
        assert answer["isError"] is True
        assert answer["structuredContent"]["ok"] is False


class TestCli:
    @pytest.fixture
    def owner_cli(self, lab, monkeypatch):
        """Point the CLI at Alice's owner MCP server, in-process."""
        monkeypatch.setattr(cli, "_call", lambda tool, arguments=None: asyncio.run(lab.owner(lab.alice, tool, arguments)))
        return lab

    def test_send_and_read(self, owner_cli, capsys) -> None:
        lab = owner_cli
        cli.main(["send", "bob", "hello from the cli"])
        assert "│ Received." in capsys.readouterr().out
        asyncio.run(lab.owner(lab.bob, "send", {"to": "alice", "text": "hello back"}))
        cli.main(["inbox"])
        assert "│ hello back" in capsys.readouterr().out

    def test_contacts(self, owner_cli, capsys) -> None:
        lab = owner_cli
        cli.main(["contacts", "add", "carol", "https://carol.example/a2a/", "--api-key-env", "CAROL_KEY"])
        assert lab.alice.contacts.get("carol").api_key_env == "CAROL_KEY"
        cli.main(["contacts", "remove", "carol"])
        cli.main(["contacts"])
        assert "carol" not in capsys.readouterr().out.split('"contacts"')[-1]

    def test_ping(self, owner_cli, capsys) -> None:
        cli.main(["ping", "bob"])
        assert '"pong": true' in capsys.readouterr().out

    def test_a_refusal_exits_non_zero(self, owner_cli) -> None:
        with pytest.raises(SystemExit):
            cli.main(["ping", "nobody"])
