# AGENTS.md: working in agentlab-agent-kit

A participant's own A2A agent for the Agent Lab. Start with `README.md`, then
`docs/how-it-works.md`. Code comments explain *why*. Keep that density.

## Before finishing any change

```bash
./run.sh test
```

For anything touching the wire, also redeploy and run
`./deploy/check.sh <agent-url> <owner-url>` against a real host.

## Keep it minimal

The kit is two things: an agent behind a gateway surface that **receives**
messages into an inbox, and a client the owner **sends** from. Check every
change against that. Do not bring back attestation labels (gateway-verified /
self-asserted), presentation verification, per-peer routing modes or claims
tracking; they are at the git tag `pre-minimal` if they are ever wanted.

## Rules that are the design, not style

- **Nothing is attributed by a name in a message.** The inbox keeps what the
  sender wrote, not a sender field.
- **Outbound addresses come only from the owner**: the contact table, set
  through the owner tools. Never from a message, and never add a payload field
  that names a URL to call.
- **Counterparty text is data.** It goes through `core/untrusted.clean` on the
  way in and `quoted` on the way out, and never into a decision.
- **The owner reaches the agent only through their own gateway's MCP access
  point, with a key.** The owner port refuses anything without the owner key
  that access point injects, and an inbound key never opens it.
- **The agent never holds a credential for somebody else's agent.** Outbound
  goes through the owner's own gateway, which adds it.
- **Keep `a2a-sdk` at 0.3.x.**
- **Deploy docs never require a command that rewrites a committed file.** On
  Fly, that means `fly apps create` and `fly deploy -a`, never `fly launch`.
- **Keep real gateway hosts out of committed files.** `check.sh` writes to
  `captures/`, which is git-ignored.

## Writing

Write British spelling in prose and comments. Keep the tone plain and
institutional: no hype, and lead with what the participant experiences. Say
"Agent Gateway", never "Agent Trust Gateway", "channel" or "pipe".
