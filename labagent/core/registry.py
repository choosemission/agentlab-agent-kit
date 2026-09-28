# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Which modules this agent runs, chosen by LABAGENT_MODULES.

Adding a capability is: write `labagent/modules/<name>/`, add it to `KNOWN`,
and list it in the environment. Nothing else in the core changes.
"""

from __future__ import annotations

from typing import Callable

from ..modules.feed.module import FeedModule
from ..modules.ping.module import PingModule
from .capability import Capability

KNOWN: dict[str, Callable[[], Capability]] = {
    "ping": PingModule,
    "feed": FeedModule,
}


class Registry:
    def __init__(self, names: tuple[str, ...] | list[str]) -> None:
        unknown = [n for n in names if n not in KNOWN]
        if unknown:
            raise ValueError(f"unknown module(s) {', '.join(unknown)}; known: {', '.join(KNOWN)}")
        self.modules: list[Capability] = [KNOWN[n]() for n in names]

    def route(self, skill: str) -> Capability | None:
        return next((m for m in self.modules if m.handles(skill)), None)

    def get(self, name: str) -> Capability | None:
        return next((m for m in self.modules if m.name == name), None)

    def skills(self):
        return [s for m in self.modules for s in m.skills()]

    def migrations(self) -> list[str]:
        return [sql for m in self.modules for sql in m.migrations()]
