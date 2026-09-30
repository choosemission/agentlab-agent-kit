# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""The agents yours can send to, and where each is reached.

A contact is a name and a URL. You add contacts through your owner tools
(`contacts_add`, `contacts_remove`), and the contact table is the only place an
outbound address comes from: nothing another agent sends can make this agent
call somewhere new. That keeps it from being used as a relay, and makes every
outbound call one you configured.

The URL is normally an access point on **your own** gateway that carries the
call to the other agent's access point. Your gateway adds whatever credential
the other side wants, so this process never holds it. If your own access point
wants a key from the agent, name the environment variable that holds it in
`api_key_env`; the value never goes in the table.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from .core.store import Store

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

TABLE = """CREATE TABLE IF NOT EXISTS contacts (
    name TEXT PRIMARY KEY, url TEXT NOT NULL, api_key_env TEXT, created_at REAL NOT NULL)"""


class ContactsError(ValueError):
    pass


@dataclass(frozen=True)
class Contact:
    name: str
    url: str
    api_key_env: str | None = None

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    def public(self) -> dict[str, Any]:
        """For the owner. Names the key variable, never the key."""
        return {**asdict(self), "key_set": self.api_key() is not None}

    def checked(self, *, allow_http: bool = False) -> "Contact":
        if not _NAME.match(self.name):
            raise ContactsError(f"{self.name!r} is not a usable name: letters, digits, '.', '_' or '-'")
        parts = urlsplit(self.url)
        schemes = ("https", "http") if allow_http else ("https",)
        if parts.scheme not in schemes or not parts.hostname:
            raise ContactsError(f"{self.name}: url must be an https:// address, not {self.url!r}")
        if parts.username or parts.password:
            raise ContactsError(f"{self.name}: url must not carry credentials; name them with api_key_env")
        if self.api_key_env and not _ENV.match(self.api_key_env):
            raise ContactsError(f"{self.name}: api_key_env names an environment variable, not {self.api_key_env!r}")
        return self


class Contacts:
    def __init__(self, store: Store, *, allow_http: bool = False) -> None:
        self._store = store
        self._allow_http = allow_http
        store.migrate([TABLE])

    def add(self, contact: Contact) -> Contact:
        contact = contact.checked(allow_http=self._allow_http)
        if self.get(contact.name) is not None:
            raise ContactsError(f"there is already a contact called {contact.name!r}; remove it first")
        self._store.execute(
            "INSERT INTO contacts (name, url, api_key_env, created_at) VALUES (?, ?, ?, ?)",
            (contact.name, contact.url, contact.api_key_env or None, time.time()),
        )
        return contact

    def remove(self, name: str) -> bool:
        return self._store.changes("DELETE FROM contacts WHERE name=?", (name,)) == 1

    def get(self, name: str) -> Contact | None:
        row = self._store.one("SELECT name, url, api_key_env FROM contacts WHERE name=?", (name,))
        return Contact(**row) if row else None

    def all(self) -> list[Contact]:
        return [Contact(**r) for r in self._store.all("SELECT name, url, api_key_env FROM contacts ORDER BY name")]
