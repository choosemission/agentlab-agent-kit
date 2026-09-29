# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Measurement: what your gateway actually delivered, written to a file.

Switched on by `LABAGENT_CAPTURE_DIR`, and meant to be switched off again once a
measurement is taken. Every claim in CLAIMS.md that this toolkit re-measures is
read from one of these files, never from a terminal: the Lab lost a day to a
presentation that was copied by hand and turned out to hold a typo.

It sits **inside** the key gate, so it records only requests the gateway let
through, plus the agent card, which is served without the key. Each request is
one JSON file:

- `body`: the request body exactly as it arrived, as text. Not re-serialised, so
  a presentation the gateway sent as a JSON string stays one.
- `headers`: every header, except that a credential's value is replaced by
  `"<present>"`. Which credentials arrived, and alongside which others, is the
  measurement (C9, C10). The values are secrets and are never written.

The health check is not recorded. It runs every few seconds and says nothing
about the gateway.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gate import HEALTH_PATHS

log = logging.getLogger("labagent.capture")

#: Headers whose values are credentials. Recorded as present, never as a value.
SECRET_HEADERS = frozenset({"x-api-key", "authorization", "cookie", "proxy-authorization"})
#: A body larger than this is recorded as truncated rather than in full.
MAX_BODY = 1_000_000
PRESENT = "<present>"


def redacted(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw_name, raw_value in headers:
        name = raw_name.decode("latin-1").lower()
        value = PRESENT if name in SECRET_HEADERS else raw_value.decode("latin-1")
        # A repeated header is kept as a list rather than overwritten.
        if name in out:
            prior = out[name]
            out[name] = [*prior, value] if isinstance(prior, list) else [prior, value]
        else:
            out[name] = value
    return out


class Capture:
    """Pure ASGI, like the gate. It reads the body once, writes it, then hands the
    same bytes to the app, so the app sees exactly what was captured."""

    def __init__(self, app: Any, directory: str | Path) -> None:
        self._app = app
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in HEALTH_PATHS:
            await self._app(scope, receive, send)
            return

        chunks: list[bytes] = []
        disconnect: dict[str, Any] | None = None
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                disconnect = message
                break
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        self._write(scope, body)

        replayed = False

        async def replay() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                if disconnect is not None:
                    return disconnect
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._app(scope, replay, send)

    def _write(self, scope: dict[str, Any], body: bytes) -> None:
        at = datetime.now(timezone.utc)
        record = {
            "captured_at": at.isoformat(),
            "method": scope.get("method"),
            "path": scope.get("path"),
            "query": scope.get("query_string", b"").decode("latin-1"),
            "headers": redacted(scope.get("headers", [])),
            "body": body[:MAX_BODY].decode("utf-8", errors="replace"),
            "body_truncated": len(body) > MAX_BODY,
        }
        name = f"{at.strftime('%Y%m%dT%H%M%S.%fZ')}-{(scope.get('method') or 'x').lower()}-{uuid.uuid4().hex[:8]}.json"
        try:
            (self._dir / name).write_text(json.dumps(record, indent=2))
        except OSError:
            # A measurement aid must never cost a participant a message.
            log.exception("could not write capture %s", name)
