# Copyright 2026 Choose Mission Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Text from somebody else's agent is data, never instructions.

Every string a counterparty sends passes through `clean` before it is stored,
and through `quoted` before it is shown. Nothing in this toolkit makes a
decision on counterparty text: modules decide on structured fields, and text is
carried for a person to read. When an LLM arrives (a digest over the feed), it
gets this text quoted, and no tools.
"""

from __future__ import annotations

import unicodedata

#: Bidirectional overrides and isolates: they make text display in an order
#: other than the one it is stored in, which is how a quoted line impersonates
#: the frame around it.
_BIDI = {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))}


def clean(value: object, limit: int) -> str:
    """Printable text, capped. Newlines and tabs survive; other controls do not."""
    text = value if isinstance(value, str) else ""
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
        elif ch in _BIDI or unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co"):
            continue
        else:
            out.append(ch)
    cleaned = "".join(out).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def quoted(text: str) -> str:
    """For display: every line marked as somebody else's words."""
    return "\n".join("│ " + line for line in text.splitlines() or [""])
