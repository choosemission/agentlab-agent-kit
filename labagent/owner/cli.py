# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""`python -m labagent …` — run the agent, or speak to it as its owner.

    python -m labagent serve
    python -m labagent peers
    python -m labagent approvals [--all]
    python -m labagent approve 3        python -m labagent deny 3
    python -m labagent outbox [--flush]
    python -m labagent audit
    python -m labagent ping bob
    python -m labagent feed subscribe alice
    python -m labagent feed post "text" [--topic t]
    python -m labagent feed read [--verified-only]
    python -m labagent verify captures/….json

Module commands are built from each module's `owner_commands()`, so a new
module's commands appear here without editing this file. Everything but `serve`
and `verify` is a call to the agent's owner MCP server, the same tools your
coding agent sees. It reads LABAGENT_OWNER_URL (default
http://127.0.0.1:8081/mcp) and LABAGENT_OWNER_KEY (default: the first of
LABAGENT_OWNER_KEYS, so `docker compose exec` works inside the container).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx

from ..core.registry import KNOWN
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
    sub.add_parser("health", help="is the agent up, and what is waiting")
    sub.add_parser("peers", help="list configured peers")
    approvals = sub.add_parser("approvals", help="what is waiting for your decision")
    approvals.add_argument("--all", action="store_true")
    for name in ("approve", "deny"):
        sub.add_parser(name, help=f"{name} a queued item").add_argument("id", type=int)
    outbox = sub.add_parser("outbox", help="messages waiting to be delivered")
    outbox.add_argument("--flush", action="store_true", help="attempt delivery now")
    sub.add_parser("audit", help="recent inbound messages and how they were labelled")
    verify = sub.add_parser("verify", help="check a captured request's gateway presentation (reads a file)")
    verify.add_argument("file", help="a capture from LABAGENT_CAPTURE_DIR, a JSON-RPC request or a message")

    for module_name, factory in KNOWN.items():
        commands = factory().owner_commands()
        if not commands:
            continue
        # A module with a single command whose name is the module (ping) is
        # promoted to the top level.
        if len(commands) == 1 and commands[0].name == module_name:
            targets = [(sub, commands[0], module_name)]
        else:
            group = sub.add_parser(module_name, help=f"{module_name} commands").add_subparsers(
                dest="module_command", required=True
            )
            targets = [(group, c, f"{module_name}_{c.name}") for c in commands]
        for container, cmd, dispatch in targets:
            p = container.add_parser(cmd.name, help=cmd.help)
            p.set_defaults(dispatch=dispatch)
            for arg in cmd.args:
                flag = "--" + arg.name.replace("_", "-")
                if arg.kind is bool:
                    p.add_argument(flag, dest=arg.name, action="store_true", help=arg.help)
                elif arg.option or not arg.required:
                    p.add_argument(flag, dest=arg.name, type=arg.kind, default=arg.default, help=arg.help)
                else:
                    p.add_argument(arg.name, type=arg.kind, help=arg.help)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)

    if args.command == "serve":
        from ..app import serve
        from ..config import config_from_env

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        asyncio.run(serve(config_from_env()))
        return

    if args.command == "verify":
        from ..config import _host_map
        from ..trust.evidence import examine

        with open(args.file, encoding="utf-8") as handle:
            document = json.load(handle)
        report = examine(document, host_map=_host_map(os.environ.get("LABAGENT_RESOLVE_HOSTS", "")))
        print(json.dumps(report, indent=2, default=str))
        if not report["verified"]:
            sys.exit(1)
        return

    if args.command in ("health", "peers", "audit"):
        return _print(_call(args.command))
    if args.command == "approvals":
        return _print(_call("approvals", {"status": "all"} if args.all else {}))
    if args.command in ("approve", "deny"):
        return _print(_call(args.command, {"id": args.id}))
    if args.command == "outbox":
        return _print(_call("outbox_flush" if args.flush else "outbox"))

    tool = args.dispatch
    skip = {"command", "module_command", "dispatch"}
    _print(_call(tool, {k: v for k, v in vars(args).items() if k not in skip and v is not None}))
