# AGENTS.md: working in agentlab-agent-kit

A participant's own A2A agent for the Agent Lab. Start with `README.md`, then
`docs/protocol.md` and `CLAIMS.md`. Code comments explain *why*. Keep that
density.

## Before finishing any change

```bash
./run.sh test
```

For anything touching the wire, also run the harness:
`docker compose -f harness/docker-compose.yml up --build -d && ./harness/demo.sh`,
then `down -v`.

## Rules that are the design, not style

- **Trust the request leg only.** Nothing is ever labelled from a reply.
  Anything that commits somebody is a new request to the party that relies on it
  (CLAIMS C2, C4).
- **Key on the issuer, pinned per peer.** Never attribute a message by a name in
  its payload, except through a peer's explicit `accept_self_asserted`, and even
  then it stays labelled self-asserted.
- **Outbound addresses come only from `peers.toml`.** Never add a payload field
  that names a URL to call.
- **Counterparty text is data.** It goes through `core/untrusted.clean` on the
  way in and `quoted` on the way out, and never into a decision. Negotiation
  logic is deterministic code, never an LLM.
- **Ask the owner** before anything that commits them. Use the approval queue.
- **Keep the owner API loopback-only**, and never route it through a gateway.
- **Keep `a2a-sdk` at 0.3.x** (CLAIMS C5).
- **Vendored trust code** (`labagent/trust/{resolve,verify,identity}.py`,
  `gate.py`) comes from affinidi-lab `servers/lab-coordinator`. Mark local
  changes with `labagent:`. A fix to the verification arithmetic belongs in both
  places.
- **Every claim about gateway behaviour** goes in `CLAIMS.md` with its source
  and date. Capture evidence to files with a script. Never paste it from a
  terminal.
- **Keep real gateway hosts out of committed files.** `tests/test_verify.py`
  reads the captured fixtures from `LABAGENT_CAPTURED_FIXTURES`.

## Writing

Write British spelling in prose and comments. Keep the tone plain and
institutional: no hype, and lead with what the participant experiences. Say
"Agent Gateway", never "Agent Trust Gateway", "channel" or "pipe".
