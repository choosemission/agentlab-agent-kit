# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Talking to a contact, at the address you gave for them and nowhere else.

The standard A2A client flow, in three calls, all to the contact's URL:

1. **Discover**: fetch their agent card, to read what their agent says it can do.
2. **Send**: one `message/send` with one text part. The reply comes back to you.
3. **Continue**: if the reply is a task waiting for input, send the next message
   with that task's id and context id, and their agent carries on the same task.

What to send, and whether to continue, is decided by you and your coding agent
from the card and the reply. Nothing here knows about any particular
counterparty. There is no queue and no retry: if the other agent is down, you
are told so now, and you decide whether to send again.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import httpx

from .contacts import Contact
from .core.untrusted import clean
from .inbox import PING, PONG, text_of

TIMEOUT = httpx.Timeout(20.0, connect=5.0)

#: Where a card lives, relative to the contact's URL: the A2A 0.3 name first,
#: then the older one, which some agents still serve only.
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
#: A card bigger than this is refused rather than read.
CARD_BYTES = 256 * 1024

#: The states an A2A 0.3 task can be in. Anything else is reported as `unknown`,
#: so a state is always one of these words and safe to show unquoted.
STATES = frozenset(
    {"submitted", "working", "input-required", "completed", "canceled", "failed", "rejected", "auth-required", "unknown"}
)
#: Task and context ids are chosen by the other agent. Only a plain token is
#: kept, so an id can be shown and sent back without quoting.
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class SendError(RuntimeError):
    pass


def request(text: str, *, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "user",
        "messageId": uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": text}],
    }
    # Carrying both ids is how A2A continues a task rather than starting another.
    if task_id:
        message["taskId"] = task_id
    if context_id:
        message["contextId"] = context_id
    return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send", "params": {"message": message}}


def _id(value: Any) -> str | None:
    return value if isinstance(value, str) and _ID.match(value) else None


def reply(response: dict[str, Any]) -> dict[str, Any]:
    """A `message/send` answer: its words, and, for a Task, its state and ids.
    A bare Message has no state; its context id is kept if it has one."""
    if "error" in response:
        error = response["error"] or {}
        raise SendError(f"JSON-RPC error {error.get('code')}: {error.get('message')}")
    result = response.get("result") or {}
    if result.get("kind") == "task":
        status = result.get("status") or {}
        state = status.get("state")
        answer = {
            "text": text_of((status.get("message") or {}).get("parts")),
            "state": state if state in STATES else "unknown",
            "task_id": _id(result.get("id")),
            "context_id": _id(result.get("contextId")),
        }
    else:
        answer = {"text": text_of(result.get("parts")), "context_id": _id(result.get("contextId"))}
    return {k: v for k, v in answer.items() if v is not None}


def reply_text(response: dict[str, Any]) -> str:
    return reply(response)["text"]


def _texts(value: Any, limit: int, count: int) -> list[str]:
    return [clean(v, limit) for v in value[:count] if isinstance(v, str)] if isinstance(value, list) else []


def card_summary(raw: Any) -> dict[str, Any]:
    """The parts of an agent card a coding agent needs to decide what to send.
    Every string is the other agent's words, cleaned and capped here and quoted
    when shown. The card's `url` is kept for information only: sending still
    goes to the contact's URL, never to an address a card names."""
    if not isinstance(raw, dict):
        raise SendError("the agent card was not a JSON object")
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    skills = []
    for skill in (raw.get("skills") if isinstance(raw.get("skills"), list) else [])[:32]:
        if not isinstance(skill, dict):
            continue
        skills.append(
            {
                "id": clean(skill.get("id"), 128),
                "name": clean(skill.get("name"), 200),
                "description": clean(skill.get("description"), 2000),
                "examples": _texts(skill.get("examples"), 500, 8),
                "input_modes": _texts(skill.get("inputModes"), 100, 8),
                "output_modes": _texts(skill.get("outputModes"), 100, 8),
            }
        )
    return {
        "name": clean(raw.get("name"), 200),
        "description": clean(raw.get("description"), 2000),
        "url": clean(raw.get("url"), 500),
        "version": clean(raw.get("version"), 64),
        # Only yes/no flags: anything richer here is more of their words.
        "capabilities": {clean(k, 64): v for k, v in capabilities.items() if isinstance(v, bool)},
        "input_modes": _texts(raw.get("defaultInputModes"), 100, 8),
        "output_modes": _texts(raw.get("defaultOutputModes"), 100, 8),
        "skills": skills,
    }


class Sender:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    @staticmethod
    def _headers(contact: Contact) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        key = contact.api_key()
        if key:
            headers["x-api-key"] = key
        return headers

    async def card(self, contact: Contact) -> dict[str, Any]:
        """Fetch the contact's agent card through the contact's URL. Raises SendError."""
        base = contact.url.rstrip("/")
        for path in CARD_PATHS:
            try:
                response = await self._client.get(base + path, headers=self._headers(contact))
            except httpx.HTTPError as error:
                raise SendError(f"{contact.name}: {type(error).__name__}: {error}") from error
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                raise SendError(f"{contact.name}: HTTP {response.status_code} {response.text[:200]}")
            if len(response.content) > CARD_BYTES:
                raise SendError(f"{contact.name}: the agent card is over {CARD_BYTES // 1024} KB")
            try:
                return card_summary(response.json())
            except ValueError as error:
                raise SendError(f"{contact.name}: the agent card was not JSON") from error
        raise SendError(f"{contact.name}: no agent card at {' or '.join(CARD_PATHS)}")

    async def send(
        self, contact: Contact, text: str, *, task_id: str | None = None, context_id: str | None = None
    ) -> dict[str, Any]:
        """Send one message; return the reply (see `reply`). Raises SendError."""
        body = request(text, task_id=task_id, context_id=context_id)
        try:
            response = await self._client.post(contact.url, json=body, headers=self._headers(contact))
        except httpx.HTTPError as error:
            raise SendError(f"{contact.name}: {type(error).__name__}: {error}") from error
        if response.status_code != 200:
            raise SendError(f"{contact.name}: HTTP {response.status_code} {response.text[:200]}")
        try:
            return reply(response.json())
        except ValueError as error:
            raise SendError(f"{contact.name}: the reply was not JSON") from error

    async def ping(self, contact: Contact) -> dict[str, Any]:
        started = time.monotonic()
        answer = (await self.send(contact, PING))["text"]
        return {"pong": answer.strip().lower() == PONG, "ms": round((time.monotonic() - started) * 1000)}

    async def aclose(self) -> None:
        await self._client.aclose()
