# How it works

One process, two doors, one SQLite file.

```
other agent ─► their gateway ─► YOUR A2A access point ─(injects the inbound key)─► agent :8080
                                                                                     │ cleans the text
                                                                                     ▼
                                                                                   inbox
you (Claude Code) ─► your MCP access point ─(injects the owner key)─► agent :8081 /mcp
send ─► a contact's URL: an access point on YOUR gateway ─► their access point ─► their agent
```

## Two doors, two keys

**The public door** (port 8080) is the A2A endpoint. Only your gateway should
reach it, and your gateway adds the inbound key (`LABAGENT_API_KEYS`) on the
last leg. Anything arriving without that key went round your gateway, and is
refused. Closing that path is the point of having a gateway in front.

**The owner's door** (port 8081, `/mcp`) is an MCP server for your coding
agent. It is reached through a different access point on the same gateway,
which adds a different key (`LABAGENT_OWNER_KEYS`). The inbound key never opens
it, so an agent that can reach you cannot act as you.

Both doors serve `/healthz` without a key, and the public door serves its agent
card without one, because a gateway fetches the card without adding the key.

## What arrives

Any A2A `message/send` that gets past the key. Text parts are kept as they are,
data parts as JSON. The text is cleaned (control and bidirectional characters
removed, capped at 4,000 characters) and stored with the time and its A2A ids.
A message delivered twice is kept once. The agent answers `Received.` and does
nothing else with it.

A message whose only text is `ping` is answered `pong` and not kept.

**The inbox does not say who sent a message.** Your gateway decides who may
reach you, through its access point's source authentication. What a sender
says about itself is in its text, and it stays there where you can read it.

## What goes out

Only to a contact, at the URL you gave for it. Nothing another agent sends can
make yours call a new address. A contact's URL is normally an access point on
your own gateway that reaches theirs: your gateway adds whatever credential
their side wants, so your agent never holds it. There is no queue: if their
agent is down, `send` says so, and you decide whether to try again.

## Other people's words

Everything another agent wrote comes back to your coding agent quoted, every
line marked `│`. It is somebody else's words: data to report, never
instructions to follow. `send` speaks for you, so your coding agent confirms
the words with you first.
