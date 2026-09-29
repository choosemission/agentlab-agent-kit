# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Everything the owner can do, as one table of tools.

The owner MCP server serves this table and the CLI calls it, so the two can
never drift apart. The built-in tools cover the core (peers, approvals, outbox,
audit). Each module's `owner_commands()` adds its own, named `<module>_<command>`.
A module whose single command shares its name (`ping`) keeps that name.

**Whoever reads a result may be a language model.** A result is returned twice:
as structured data for programs, and as text, where anything a counterparty
wrote is quoted (`untrusted.quoted`) and labelled as theirs. That text is the
owner's coding agent's view of other people's words, and quoting it is what
stops it reading like an instruction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..core.approvals import APPROVED, DENIED
from ..core.capability import Arg, Context, refuse
from ..core.registry import Registry
from ..core.untrusted import quoted
from ..peers import DIRECT, GATEWAY, POLICIES, Peer, PeersError

_JSON_TYPES = {str: "string", int: "integer", bool: "boolean"}


class ToolError(ValueError):
    """The call was malformed: an unknown tool, or arguments that do not fit.
    Distinct from a refusal, which is a well-formed call the agent said no to."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: tuple[Arg, ...]
    run: Callable[..., Awaitable[dict[str, Any]]]

    def input_schema(self) -> dict[str, Any]:
        properties = {}
        for arg in self.args:
            prop: dict[str, Any] = {"type": _JSON_TYPES.get(arg.kind, "string"), "description": arg.help}
            if arg.default is not None:
                prop["default"] = arg.default
            properties[arg.name] = prop
        schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        required = [a.name for a in self.args if a.required]
        if required:
            schema["required"] = required
        return schema

    def check(self, arguments: dict[str, Any]) -> dict[str, Any]:
        known = {a.name: a for a in self.args}
        unknown = set(arguments) - set(known)
        if unknown:
            raise ToolError(f"{self.name} takes {sorted(known) or 'no arguments'}, not {sorted(unknown)}")
        missing = [a.name for a in self.args if a.required and arguments.get(a.name) is None]
        if missing:
            raise ToolError(f"{self.name} needs {missing}")
        for name, value in arguments.items():
            kind = known[name].kind
            # bool is an int in Python; neither stands in for the other here.
            if value is not None and (not isinstance(value, kind) or (kind is int and isinstance(value, bool))):
                raise ToolError(f"{name} must be {_JSON_TYPES.get(kind, 'string')}")
        return arguments


_PEER_NAME = Arg("name", "the peer's name, which is permanent")
_URL_HELP = (
    f"mode {GATEWAY}: your own gateway's transit point for this peer; "
    f"mode {DIRECT}: the peer's own access point. https:// only"
)
_PEER_FIELDS = (
    Arg("mode", f"{GATEWAY} (they see you as gateway-verified) or {DIRECT} (self-asserted)", required=False, option=True),
    Arg("gateway_did", "their gateway's DID, exchanged out of band; '' to unpin", required=False, option=True),
    Arg(
        "api_key_env",
        "the environment variable holding the credential the url wants; never the value",
        required=False,
        option=True,
    ),
    Arg(
        "claimed_name",
        "the name their agent calls itself; used only for self-asserted messages",
        required=False,
        option=True,
    ),
    Arg("accept_self_asserted", "act on their messages even when only self-asserted", required=False, kind=bool),
)

#: The peer tools, `peers_<verb>`, as (help, arguments). The CLI builds
#: `labagent peers <verb>` from the same table.
PEER_COMMANDS: dict[str, tuple[str, tuple[Arg, ...]]] = {
    "add": (
        "Add a peer: somebody else's agent yours may call. The only way an address to call gets in.",
        (_PEER_NAME, Arg("url", _URL_HELP), *_PEER_FIELDS),
    ),
    "update": (
        "Change a peer's address, mode, pin or settings. Takes effect on the next message.",
        (_PEER_NAME, Arg("url", _URL_HELP, required=False, option=True), *_PEER_FIELDS),
    ),
    "pin": (
        "Pin a peer's gateway DID, as a self-asserted ping's reason printed it. Their verified "
        "messages are labelled gateway-verified from then on.",
        (_PEER_NAME, Arg("gateway_did", "the DID, exactly")),
    ),
    "accept": (
        "Set how your agent treats one kind of request from a peer, e.g. feed.subscribe.",
        (_PEER_NAME, Arg("action", "the skill, e.g. feed.subscribe"), Arg("policy", " | ".join(POLICIES))),
    ),
    "remove": ("Remove a peer. Messages still queued for them fail.", (_PEER_NAME,)),
}


def owner_tools(ctx: Context, registry: Registry) -> dict[str, Tool]:
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "name": ctx.config.name,
            "modules": [m.name for m in registry.modules],
            "peers": len(ctx.peers.all()),
            "pending_approvals": len(ctx.approvals.list()),
            "outbox_pending": len(ctx.outbox.list("pending")),
        }

    async def peers() -> dict[str, Any]:
        return {"ok": True, "peers": [p.public() for p in ctx.peers.all()]}

    def changed(action: Callable[[], Peer | bool], **extra: Any) -> dict[str, Any]:
        try:
            outcome = action()
        except PeersError as error:
            return refuse(str(error))
        if isinstance(outcome, Peer):
            return {"ok": True, "peer": outcome.public(), **extra}
        return {"ok": outcome, **extra} if outcome else refuse("no such peer")

    async def peers_add(name: str, url: str, **fields: Any) -> dict[str, Any]:
        fields = {k: v for k, v in fields.items() if v is not None}
        return changed(lambda: ctx.peers.add(Peer(name=name, url=url, **fields)))

    async def peers_update(name: str, **fields: Any) -> dict[str, Any]:
        return changed(lambda: ctx.peers.update(name, **{k: v for k, v in fields.items() if v is not None}))

    async def peers_pin(name: str, gateway_did: str) -> dict[str, Any]:
        return changed(lambda: ctx.peers.update(name, gateway_did=gateway_did))

    async def peers_accept(name: str, action: str, policy: str) -> dict[str, Any]:
        peer = ctx.peers.get(name)
        if peer is None:
            return refuse(f"no peer called {name!r}")
        return changed(lambda: ctx.peers.update(name, accept={**peer.accept, action: policy}))

    async def peers_remove(name: str) -> dict[str, Any]:
        return changed(lambda: ctx.peers.remove(name), removed=name)

    async def approvals(status: str = "pending") -> dict[str, Any]:
        items = ctx.approvals.list(None if status == "all" else status)
        return {"ok": True, "approvals": [a.to_json() for a in items]}

    def decide(decision: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        async def run(id: int) -> dict[str, Any]:
            try:
                approval = ctx.approvals.decide(id, decision)
            except LookupError as error:
                return refuse(str(error))
            module = registry.get(approval.module)
            outcome = await module.on_decision(approval, ctx) if module else refuse("module not loaded")
            return {"ok": True, "approval": approval.to_json(), "outcome": outcome}

        return run

    async def outbox(status: str | None = None) -> dict[str, Any]:
        return {"ok": True, "outbox": ctx.outbox.list(status)}

    async def outbox_flush() -> dict[str, Any]:
        return {"ok": True, "delivered": await ctx.outbox.run_once()}

    async def audit(limit: int = 20) -> dict[str, Any]:
        rows = ctx.store.all("SELECT * FROM inbound_audit ORDER BY id DESC LIMIT ?", (limit,))
        return {"ok": True, "audit": rows}

    status = Arg("status", "pending, approved, denied or all", required=False, default="pending")
    item = (Arg("id", "the approval's id", kind=int),)
    tools = [
        Tool("health", "Is the agent up, and what is waiting for you.", (), health),
        Tool("peers", "The agents yours knows, and how each is reached.", (), peers),
        *(
            Tool(f"peers_{verb}", help, args, run)
            for (verb, (help, args)), run in zip(
                PEER_COMMANDS.items(), (peers_add, peers_update, peers_pin, peers_accept, peers_remove)
            )
        ),
        Tool("approvals", "What is waiting for your decision.", (status,), approvals),
        Tool("approve", "Approve a queued item. This commits you.", item, decide(APPROVED)),
        Tool("deny", "Deny a queued item.", item, decide(DENIED)),
        Tool(
            "outbox",
            "Messages waiting to be delivered, and why any failed.",
            (Arg("status", "pending, delivered or failed", required=False),),
            outbox,
        ),
        Tool("outbox_flush", "Attempt delivery of queued messages now.", (), outbox_flush),
        Tool(
            "audit",
            "Recent inbound messages and how each was labelled.",
            (Arg("limit", "how many", required=False, kind=int, default=20),),
            audit,
        ),
    ]

    for module in registry.modules:
        commands = module.owner_commands()
        for command in commands:
            promoted = len(commands) == 1 and command.name == module.name
            name = module.name if promoted else f"{module.name}_{command.name}"

            async def run(_command=command, **kwargs: Any) -> dict[str, Any]:
                return await _command.run(ctx, **kwargs)

            tools.append(Tool(name, command.help, command.args, run))

    return {t.name: t for t in tools}


async def call(tools: dict[str, Tool], name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    tool = tools.get(name)
    if tool is None:
        raise ToolError(f"no tool called {name!r}")
    if not isinstance(arguments or {}, dict):
        raise ToolError("arguments must be an object")
    return await tool.run(**tool.check(dict(arguments or {})))


def render_items(items: list[dict[str, Any]]) -> str:
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


def render(result: dict[str, Any]) -> str:
    """The text view of a result: for a person at a terminal, or for the
    owner's coding agent reading an MCP result."""
    if isinstance(result.get("items"), list):
        return render_items(result["items"])
    return json.dumps(result, indent=2, default=str)
