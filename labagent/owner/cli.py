# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""`python -m labagent …` — run the agent, or speak to it as its owner.

    python -m labagent serve
    python -m labagent health
    python -m labagent inbox [--limit 20] [--since-id 7]
    python -m labagent send coordinator "hello"
    python -m labagent ping coordinator
    python -m labagent contacts
    python -m labagent contacts add coordinator https://your-gateway.example/to-lab [--api-key-env VAR]
    python -m labagent contacts remove coordinator

Everything but `serve` is a call to the agent's owner MCP server, the same
tools your coding agent sees. It reads LABAGENT_OWNER_URL (default
http://127.0.0.1:8081/mcp) and LABAGENT_OWNER_KEY (default: the first of
LABAGENT_OWNER_KEYS, so `docker compose exec` works inside the container).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

import httpx

from .tools import render


def _owner_url() -> str:
    return os.environ.get("LABAGENT_OWNER_URL") or f"http://127.0.0.1:{os.environ.get('LABAGENT_OWNER_PORT', '8081')}/mcp"


def _owner_key() -> str | None:
    key = os.environ.get("LABAGENT_OWNER_KEY") or os.environ.get("LABAGENT_OWNER_KEYS", "").split(",")[0]
    return key.strip() or None


def _call(tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """One `tools/call` to the owner MCP server. Returns the tool's result."""
    key = _owner_key()
    headers = {"x-api-key": key} if key else {}
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments or {}}}
    try:
        response = httpx.post(_owner_url(), json=message, headers=headers, timeout=30)
    except httpx.HTTPError as error:
        sys.exit(f"cannot reach the owner MCP server at {_owner_url()}: {error}. Is the agent running?")
    try:
        answer = response.json()
    except ValueError:
        sys.exit(f"owner MCP server answered HTTP {response.status_code}: {response.text[:200]}")
    if response.status_code in (401, 403):
        # The key gate, not MCP: its answer is plain JSON.
        sys.exit(f"the owner MCP server refused the key ({response.status_code}). Set LABAGENT_OWNER_KEY.")
    if "error" in answer:
        sys.exit(answer["error"].get("message", "the owner MCP server returned an error"))
    return answer["result"]["structuredContent"]


def _print(result: dict[str, Any]) -> None:
    print(render(result))
    if result.get("ok") is False:
        sys.exit(1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="labagent", description="A participant's own Agent Lab agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the agent (reads LABAGENT_* from the environment)")
    sub.add_parser("health", help="is the agent up, and how much is in its inbox")
    inbox = sub.add_parser("inbox", help="messages other agents sent you, newest first")
    inbox.add_argument("--limit", type=int)
    inbox.add_argument("--since-id", dest="since_id", type=int)
    send = sub.add_parser("send", help="send a message to a contact")
    send.add_argument("to")
    send.add_argument("text")
    sub.add_parser("ping", help="check a contact's agent is up").add_argument("to")

    contacts = sub.add_parser("contacts", help="list your contacts, or change them")
    verbs = contacts.add_subparsers(dest="verb")
    add = verbs.add_parser("add", help="add a contact")
    add.add_argument("name")
    add.add_argument("url")
    add.add_argument("--api-key-env", dest="api_key_env")
    verbs.add_parser("remove", help="remove a contact").add_argument("name")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)

    if args.command == "serve":
        from ..app import serve
        from ..config import config_from_env

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        asyncio.run(serve(config_from_env()))
        return

    fields = {k: v for k, v in vars(args).items() if k not in ("command", "verb") and v is not None}
    tool = f"contacts_{args.verb}" if args.command == "contacts" and args.verb else args.command
    _print(_call(tool, fields))
