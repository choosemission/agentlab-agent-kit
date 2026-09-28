# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""A stand-in for your gateway's outbound leg, so two agents can talk on a laptop.

It does the two things a real Agent Gateway does on the way out that matter to
this toolkit, and nothing else:

1. **Signs the caller's identity.** It reads the agent's self-description from
   the message metadata and attaches a presentation, signed with real
   `eddsa-rdfc-2022` proofs, under the request-leg extension URI (see
   labagent/trust/testing.py).
2. **Injects the target's credential.** The calling agent never holds the key
   for the agent it calls; the gateway does.

It also serves its own DID logs, so the receiving agent resolves and verifies
exactly as it would against a real gateway (over plain HTTP, via
LABAGENT_RESOLVE_HOSTS — harness only).

**It is not a model of Affinidi's gateway.** It has no policy, no surfaces, no
fabric hop. What the real one does is recorded, dated, in CLAIMS.md; M4 in
the plan is where the participant↔participant hop is measured.

    GW_SEED=alice GW_HOST=alice-gw.test PORT=8000 \\
    GW_ROUTES='{"bob": {"url": "http://bob:8080/", "key": "…"}}' \\
    python -m harness.pretend_gateway

    python -m harness.pretend_gateway did alice alice-gw.test   # print its DIDs
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from labagent.trust.testing import TestGateway


def create_gateway_app(
    gateway: TestGateway,
    routes: dict[str, dict[str, str]],
    *,
    client: httpx.AsyncClient | None = None,
    sign: bool = True,
) -> Starlette:
    client = client or httpx.AsyncClient(timeout=30)
    logs = gateway.did_logs()

    async def did_log(request: Request) -> Response:
        body = logs.get(request.url.path)
        if body is None:
            return Response(status_code=404)
        return Response(body, media_type="application/jsonl")

    async def forward(request: Request) -> Response:
        route = routes.get(request.path_params["peer"])
        if route is None:
            return JSONResponse({"error": "No route configured"}, status_code=404)
        try:
            payload: Any = await request.json()
        except ValueError:
            return JSONResponse({"error": "Invalid JSON-RPC request"}, status_code=400)
        if sign and isinstance(payload, dict) and payload.get("method") == "message/send":
            params = payload.get("params") or {}
            if isinstance(params.get("message"), dict):
                payload = {**payload, "params": {**params, "message": gateway.attach(params["message"])}}
        headers = {"content-type": "application/json"}
        if route.get("key"):
            headers["x-api-key"] = route["key"]
        try:
            upstream = await client.post(route["url"], json=payload, headers=headers)
        except httpx.HTTPError as error:
            return JSONResponse({"error": f"upstream unreachable: {error}"}, status_code=502)
        return Response(upstream.content, status_code=upstream.status_code, media_type="application/json")

    paths = list(logs)
    return Starlette(
        routes=[Route(p, did_log) for p in paths] + [Route("/to/{peer}", forward, methods=["POST"])]
    )


def main() -> None:
    if len(sys.argv) >= 4 and sys.argv[1] == "did":
        g = TestGateway(sys.argv[2], sys.argv[3])
        print(json.dumps({"gateway_did": g.gateway_did, "surface_did": g.surface_did}, indent=2))
        return

    import uvicorn

    gateway = TestGateway(os.environ["GW_SEED"], os.environ["GW_HOST"])
    routes = json.loads(os.environ.get("GW_ROUTES", "{}"))
    for route in routes.values():
        # Keys come from the environment by name, as in peers.toml.
        if route.get("key_env"):
            route["key"] = os.environ.get(route["key_env"], "")
    sign = os.environ.get("GW_SIGN", "true") != "false"
    print(f"pretend gateway {gateway.gateway_did} signing={sign} routes={sorted(routes)}", flush=True)
    uvicorn.run(create_gateway_app(gateway, routes, sign=sign), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
