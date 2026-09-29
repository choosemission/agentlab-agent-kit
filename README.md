# agentlab-agent-kit

> [!WARNING]
> **Experimental.** This is early, under-tested code for the Agent Lab. The
> gateway behaviour it relies on is listed, dated and sourced in
> [`CLAIMS.md`](./CLAIMS.md). Hosting behind one real gateway was re-measured
> with this toolkit on 29 Sep 2026 (M3); the rest was measured by the Lab.
> Verify against your own gateway, and open an issue when something is wrong.
>
> Not affiliated with, endorsed by, or supported by Affinidi.

**Your own agent in the Agent Lab.** It is a small, long-running A2A agent that
you deploy behind your own Agent Gateway. It represents you to other
participants' agents: it keeps a feed with the people you choose, and later it
will negotiate meetings for you. It asks you before it commits you to anything.
It tells you, for every message it receives, whether the sender's gateway
vouched for it or the sender only said so.

The toolkit grows by **modules**. Today there are two:

| Module | What it does |
|---|---|
| `ping` | Ask a peer how it labelled your call. The first thing to run on a new route. |
| `feed` | A shared intel feed. You publish to subscribers you approved, and you receive from peers you subscribed to. Every item carries a label. |

Next is `scheduling`: two organisations find a meeting slot, and only free time
crosses the boundary. See the roadmap below.

## What "gateway-verified" means

Every inbound message gets one of two labels before any module sees it.

- **✔ gateway-verified**:
  - the message carried a presentation from the sender's gateway;
  - both of its proofs verified;
  - the gateway is the one whose DID you pinned for that peer.
- **⚠ self-asserted**: anything else. The reason is shown next to the item.

Gateway-verified means *a caller whose gateway vouched for this agent identity
delivered this message*. It does **not** mean the content is signed, and it
cannot show the message is fresh. The gateway signs who is calling, not what
they say, and nothing binds that signature to one request. See
[`docs/threat-model.md`](./docs/threat-model.md).

## Try it on your laptop

Two agents, Alice and Bob, each behind a stand-in gateway that signs
presentations the way a real gateway does:

```bash
docker compose -f harness/docker-compose.yml up --build -d
./harness/demo.sh
```

Bob subscribes. Alice's agent asks her, she approves, she posts, and Bob reads:

```
✔ gateway-verified · from alice · 2026-09-28T16:12:26+00:00 · #demo
│ Harness check: the feed works end to end.
```

Speak to either agent as its owner:

```bash
docker compose -f harness/docker-compose.yml exec bob python -m labagent feed read
docker compose -f harness/docker-compose.yml exec alice python -m labagent approvals
```

Without Docker, run the suite, which drives the same two-agent topology in-process:

```bash
./run.sh test
```

## Run your own

> **Proven behind one live Agent Gateway (M3, 29 Sep 2026)**: a caller through
> that gateway was labelled gateway-verified. A participant-to-participant hop
> (M6) is not yet measured, so a label on a message from somebody else's gateway
> rests on the Lab's measurements, not this toolkit's.

[`deploy/DEPLOY.md`](./deploy/DEPLOY.md) is the runbook: the agent and a
Cloudflare tunnel in Docker, the surface to build on your gateway, a call to
yourself through it, and how to capture and check what the gateway delivered.
`./deploy/check.sh` tests the doors from the internet side.

The owner speaks to the agent through its **owner MCP server** (`/mcp` on
`LABAGENT_OWNER_PORT`, default 8081). It opens only to the owner key
(`LABAGENT_OWNER_KEYS`), which is never the same as an inbound key: your
coding agent reaches it through a second access point on your own gateway,
which injects that key. The CLI below calls the same tools.

## Owner commands

The same tools your coding agent sees over MCP (`ping`, `feed_post` and so on):

```
labagent health | peers | audit
labagent peers add <name> <url> [--mode gateway|direct] [--gateway-did d] [--api-key-env VAR]
labagent peers update <name> [--url u] [--mode m] [--gateway-did d]   labagent peers pin <name> <did>
labagent peers accept <name> <action> ask|auto|deny                   labagent peers remove <name>
labagent approvals [--all]        labagent approve <id> | deny <id>
labagent outbox [--flush]
labagent ping <peer>
labagent feed subscribe <peer> | unsubscribe <peer> | subscriptions | subscribers
labagent feed post "<text>" [--topic t]
labagent feed read [--verified-only] [--peer p] [--limit n]
```

## Adding a module

A module is one directory under `labagent/modules/`. It provides:
- skills for the agent card (`<module>.<action>`);
- its own tables;
- an inbound handler that returns an acknowledgement;
- owner commands;
- an `on_decision` hook for anything it queued for approval.

The core does the rest: authentication, labelling, deduplication, audit, the
approval queue and the outbox. See `labagent/core/capability.py`, and
`modules/ping` for the smallest example. Register it in `core/registry.py`.
Its commands then appear as owner MCP tools and in the CLI.

## Roadmap

| | |
|---|---|
| M0–M2 ✅ | Skeleton, core, `ping`, `feed`, the harness |
| M3 ✅ | Hosting behind one real gateway: a live presentation verifies, and the tunnel and doors behave |
| M4 ✅ | The owner MCP server: your coding agent is the owner's interface, through your own gateway |
| M5 | Peers as runtime data, managed through the owner MCP; outbound through your gateway or direct, per peer |
| M6 | Hosting that stays up, and measuring a participant-to-participant hop to confirm or revise the label rule |
| M7 | A welcome from the Lab: register, get called back, earn a completion code |
| M8 | `scheduling` with a fake or ICS calendar. Free intervals only cross the boundary; deterministic, never LLM-decided |
| Later | Google Calendar; discovery through the Lab directory; a personal digest over the feed |

## Licence

Apache 2.0. See [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE). Affinidi,
Affinidi Trust Fabric and Agent Gateway are trademarks of their owner. The
licence grants no trademark rights.
