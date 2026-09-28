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

Module commands are built from each module's `owner_commands()`, so a new
module's commands appear here without editing this file. Everything but `serve`
talks to the owner API on loopback (LABAGENT_ADMIN_URL, default
http://127.0.0.1:8081).
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
from ..core.untrusted import quoted


def _admin_url() -> str:
    return os.environ.get("LABAGENT_ADMIN_URL") or f"http://127.0.0.1:{os.environ.get('LABAGENT_ADMIN_PORT', '8081')}"


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = httpx.request(method, _admin_url() + path, json=body, timeout=30)
    except httpx.HTTPError as error:
        sys.exit(f"cannot reach the owner API at {_admin_url()}: {error}. Is the agent running?")
    try:
        return response.json()
    except ValueError:
        sys.exit(f"owner API answered HTTP {response.status_code}: {response.text[:200]}")


def _render_items(items: list[dict[str, Any]]) -> str:
    """Feed items for a person: the label first, the words quoted as somebody else's."""
    if not items:
        return "(nothing yet)"
    blocks = []
    for item in items:
        mark = "✔ gateway-verified" if item.get("label") == "gateway-verified" else "⚠ self-asserted"
        head = f"{mark} · from {item.get('peer')} · {item.get('created_at') or ''}"
        if item.get("topic"):
            head += f" · #{item['topic']}"
        lines = [head, quoted(item.get("body") or "")]
        if item.get("label") != "gateway-verified":
            lines.append(f"  why: {item.get('reason')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _print(result: dict[str, Any]) -> None:
    if isinstance(result.get("items"), list):
        print(_render_items(result["items"]))
    else:
        print(json.dumps(result, indent=2, default=str))
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

    for module_name, factory in KNOWN.items():
        commands = factory().owner_commands()
        if not commands:
            continue
        # A module with a single command whose name is the module (ping) is
        # promoted to the top level.
        if len(commands) == 1 and commands[0].name == module_name:
            targets = [(sub, commands[0], f"{module_name}:{commands[0].name}")]
        else:
            group = sub.add_parser(module_name, help=f"{module_name} commands").add_subparsers(
                dest="module_command", required=True
            )
            targets = [(group, c, f"{module_name}:{c.name}") for c in commands]
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

    if args.command == "health":
        return _print(_call("GET", "/health"))
    if args.command == "peers":
        return _print(_call("GET", "/peers"))
    if args.command == "approvals":
        return _print(_call("GET", "/approvals" + ("?status=all" if args.all else "")))
    if args.command in ("approve", "deny"):
        return _print(_call("POST", f"/approvals/{args.id}/{args.command}"))
    if args.command == "outbox":
        return _print(_call("POST", "/outbox/run") if args.flush else _call("GET", "/outbox"))
    if args.command == "audit":
        return _print(_call("GET", "/audit"))

    module, command = args.dispatch.split(":")
    skip = {"command", "module_command", "dispatch"}
    body = {k: v for k, v in vars(args).items() if k not in skip and v is not None}
    _print(_call("POST", f"/cmd/{module}/{command}", body))
