# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Sending a message to a contact, at the address you gave for them and nowhere else.

One A2A `message/send` with one text part, posted to the contact's URL. The
reply's text comes back to you. There is no queue and no retry: if the other
agent is down, you are told so now, and you decide whether to send again.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from .contacts import Contact
from .inbox import PING, PONG, text_of

TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class SendError(RuntimeError):
    pass


def request(text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


def reply_text(response: dict[str, Any]) -> str:
    """The words of a `message/send` answer: a Task (the reply is its status
    message) or a bare Message."""
    if "error" in response:
        error = response["error"] or {}
        raise SendError(f"JSON-RPC error {error.get('code')}: {error.get('message')}")
    result = response.get("result") or {}
    message = (result.get("status") or {}).get("message") if result.get("kind") == "task" else result
    return text_of((message or {}).get("parts"))


class Sender:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def send(self, contact: Contact, text: str) -> str:
        """Send one message; return the reply's text. Raises SendError."""
        headers = {"content-type": "application/json"}
        key = contact.api_key()
        if key:
            headers["x-api-key"] = key
        try:
            response = await self._client.post(contact.url, json=request(text), headers=headers)
        except httpx.HTTPError as error:
            raise SendError(f"{contact.name}: {type(error).__name__}: {error}") from error
        if response.status_code != 200:
            raise SendError(f"{contact.name}: HTTP {response.status_code} {response.text[:200]}")
        try:
            return reply_text(response.json())
        except ValueError as error:
            raise SendError(f"{contact.name}: the reply was not JSON") from error

    async def ping(self, contact: Contact) -> dict[str, Any]:
        started = time.monotonic()
        answer = await self.send(contact, PING)
        return {"pong": answer.strip().lower() == PONG, "ms": round((time.monotonic() - started) * 1000)}

    async def aclose(self) -> None:
        await self._client.aclose()
