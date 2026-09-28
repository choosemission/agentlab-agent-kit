# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""The owner's API: loopback only, and never behind the gateway.

The CLI is its only intended client. Every module's owner commands are mounted
at `POST /cmd/<module>/<command>`, with the command's arguments as a JSON body.
"""

from __future__ import annotations

from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..core.approvals import APPROVED, DENIED
from ..core.capability import Context
from ..core.registry import Registry
from ..gate import LoopbackOnly


def create_admin_app(ctx: Context, registry: Registry, *, trust: tuple[str, ...] = ()) -> Any:
    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "name": ctx.config.name,
                "modules": [m.name for m in registry.modules],
                "peers": len(ctx.peers.all()),
                "pending_approvals": len(ctx.approvals.list()),
                "outbox_pending": len(ctx.outbox.list("pending")),
            }
        )

    async def peers(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "peers": [p.public() for p in ctx.peers.all()]})

    async def approvals(request: Request) -> JSONResponse:
        status = request.query_params.get("status", "pending")
        items = ctx.approvals.list(None if status == "all" else status)
        return JSONResponse({"ok": True, "approvals": [a.to_json() for a in items]})

    async def decide(request: Request) -> JSONResponse:
        decision = {"approve": APPROVED, "deny": DENIED}.get(request.path_params["decision"])
        if decision is None:
            return JSONResponse({"ok": False, "error": "approve or deny"}, status_code=404)
        try:
            approval = ctx.approvals.decide(int(request.path_params["id"]), decision)
        except (LookupError, ValueError) as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=404)
        module = registry.get(approval.module)
        outcome = await module.on_decision(approval, ctx) if module else {"ok": False, "error": "module not loaded"}
        return JSONResponse({"ok": True, "approval": approval.to_json(), "outcome": outcome})

    async def outbox(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "outbox": ctx.outbox.list(request.query_params.get("status"))})

    async def flush(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "delivered": await ctx.outbox.run_once()})

    async def audit(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", "20"))
        rows = ctx.store.all("SELECT * FROM inbound_audit ORDER BY id DESC LIMIT ?", (limit,))
        return JSONResponse({"ok": True, "audit": rows})

    async def command(request: Request) -> JSONResponse:
        module = registry.get(request.path_params["module"])
        if module is None:
            return JSONResponse({"ok": False, "error": "module not loaded on this agent"}, status_code=404)
        found = next((c for c in module.owner_commands() if c.name == request.path_params["command"]), None)
        if found is None:
            return JSONResponse({"ok": False, "error": "no such command"}, status_code=404)
        try:
            kwargs = await request.json() if await request.body() else {}
        except ValueError:
            return JSONResponse({"ok": False, "error": "body must be JSON"}, status_code=400)
        allowed = {a.name for a in found.args}
        if not isinstance(kwargs, dict) or set(kwargs) - allowed:
            return JSONResponse({"ok": False, "error": f"arguments are {sorted(allowed)}"}, status_code=400)
        try:
            return JSONResponse(await found.run(ctx, **kwargs))
        except TypeError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/peers", peers),
            Route("/approvals", approvals),
            Route("/approvals/{id:int}/{decision}", decide, methods=["POST"]),
            Route("/outbox", outbox),
            Route("/outbox/run", flush, methods=["POST"]),
            Route("/audit", audit),
            Route("/cmd/{module}/{command}", command, methods=["POST"]),
        ]
    )
    return LoopbackOnly(app, trust=trust)
