# Deploying your agent

> [!WARNING]
> Experimental. Gateway dashboard labels move between versions. Where a step
> names one, it is the name observed on the date in [`CLAIMS.md`](../CLAIMS.md).
> If yours differs, the [`affinidi-agent-surfaces`](https://github.com/choosemission/agent-gateway-skills)
> skill describes the alternatives.
>
> The public door (§3) was run end to end on 29 Sep 2026 against one
> participant gateway (M3; "G1" in CLAIMS.md), behind a quick tunnel. The Fly
> path (§2) and the owner's door (§4) have been run locally, not yet behind a
> live gateway.

Your agent has two doors, and your gateway stands in front of both:

```
other agents ─► your A2A access point ─► [signs the caller, injects the inbound key] ─► agent :8080
you (Claude Code) ─► your MCP access point ─► [checks your key, injects the owner key] ─► agent :8081 /mcp
```

Each door opens only to its own key, and neither key opens the other door. The
agent's own addresses are public, and that is fine: without the key your
gateway injects, they answer nothing but the agent card and a health check.

## 1. What any host needs

The agent is one Docker image (`deploy/Dockerfile`). Anything that can run it
this way will do:

- **Always on, one instance.** Messages from other agents arrive unannounced,
  and the outbox retries in the background. Its memory is one SQLite file, so
  never run two copies of it.
- **A disk that survives restarts**, mounted at `/app/data`.
- **Two HTTPS addresses**, one for port 8080 (the public door) and one for port
  8081 (the owner's door). Two ports on one hostname, or two hostnames.
- **Environment variables** for its settings, kept secret where the host allows.

Generate its settings once, on your own machine, into `.env` (never committed):

```bash
cp .env.example .env
# In .env:
#   LABAGENT_NAME=…           what your agent calls itself
#   LABAGENT_API_KEYS=…       openssl rand -hex 32   (the inbound key)
#   LABAGENT_OWNER_KEYS=…     openssl rand -hex 32   (the owner key: a different one)
```

The agent refuses to start without both keys, or if they share a key. Keep
`.env`: `check.sh` reads the keys from it, and your gateway needs them.

## 2. Host it

### The simple option: Fly.io

One small machine, always on, with a volume. Expect a few US dollars a month.
You need a Fly account and `flyctl`, logged in (`fly auth login`).

```bash
fly launch --copy-config --no-deploy          # keeps fly.toml, names your app
fly volumes create labagent_data --size 1     # in the region fly.toml names
grep -E '^LABAGENT_(NAME|API_KEYS|OWNER_KEYS)=' .env | fly secrets import
fly deploy
./deploy/check.sh https://<app>.fly.dev https://<app>.fly.dev:8443   # every line PASS
```

- **Public door:** `https://<app>.fly.dev`.
- **Owner's door:** `https://<app>.fly.dev:8443/mcp`.

`fly.toml` holds nothing secret. It uses the image's `fly` target, which hands
Fly's volume (owned by root) to the agent's own user before starting it.

### Other ways

Any host that meets §1 works. These have not been written up step by step:

- **A server you run** (a small VPS, a machine at home), using
  `deploy/docker-compose.yml`. Replace the quick tunnel with a **named**
  Cloudflare tunnel (`tunnel run --token $CLOUDFLARE_TUNNEL_TOKEN`), or with a
  reverse proxy that terminates TLS. Route two hostnames, to `http://agent:8080`
  and `http://agent:8081`. The volume `agent-data` holds its memory.
- **Another container platform** (Railway, Render and similar), if it gives you
  a persistent volume, keeps one instance running, and exposes two ports or two
  services. Platforms that scale to zero, or have no persistent disk, do not
  fit.

### Just trying it: a quick tunnel

`docker compose -f deploy/docker-compose.yml up --build -d` runs the agent
behind a Cloudflare **quick** tunnel. There is no account, and you get a new
`*.trycloudflare.com` address every time it starts. It carries only the public
door. It is good for a first look, and wrong for anything you leave running.
`./deploy/check.sh` with no arguments finds the tunnel's address in its logs.

Below, **AGENT** is your public door's address and **OWNER** your owner's
door's.

## 3. The public door: an A2A surface on your gateway

On your gateway's dashboard. Gateway-level objects come first, because a
surface can only reference what already exists.

1. **Management → Secrets**: a new secret holding the value of
   `LABAGENT_API_KEYS`.
2. **A new A2A surface.**
   - **External target**: `AGENT/`, with the trailing slash. The agent answers
     JSON-RPC at its root.
   - **Target credential**: the secret from step 1, on header **`x-api-key`**.
     It has appeared both as *Target Authentication* on the Managed Agent node
     and as an API key credential on the Managed Agent → External leg.
   - **Access point**: a route of your choice. For the Agent Lab, require an
     Agent Lab sign-in (JWT) at the access point.
3. **An Identity element on the inbound leg** (Access Point → Managed Agent):
   - extraction **Payload**;
   - meta field **`agentIdentity`**;
   - this schema:

   ```json
   {"type": "object",
    "properties": {"name": {"type": "string", "x-identity": true}},
    "required": ["name"]}
   ```

   Mark `name` only. A marked field that changes moves your caller DID.
4. **Identity Binding VP** on the Managed Agent node: on. The element resolves
   the caller; this switch stamps the result into the request your agent
   receives. Without it the agent sees nothing signed.

Then tell the agent its public address, the access point's URL. It goes into
the agent card:

```bash
fly secrets set LABAGENT_PUBLIC_URL=https://<your-access-point>   # Fly; elsewhere, set it and restart
./deploy/check.sh AGENT OWNER https://<your-access-point>          # adds: the card, through your gateway
```

## 4. The owner's door: an MCP surface on your gateway

This is how you, through your coding agent, reach your agent. It is the same
pattern as §3 with a different key.

1. **Management → Secrets**: a new secret holding the value of
   `LABAGENT_OWNER_KEYS`.
2. **A new MCP surface.**
   - **External target**: `OWNER/mcp`, for example
     `https://<app>.fly.dev:8443/mcp`.
   - **Target credential**: the secret from step 1, on header **`x-api-key`**.
   - **Access point**: a route of your choice, with source authentication that
     only you hold (an API key of your own, not either of the agent's keys).
3. Connect your coding agent to the access point, with your own credential:

   ```bash
   claude mcp add --transport http my-agent https://<your-mcp-access-point> \
     --header "x-api-key: <your access point key>"
   ```

   Ask it "what tools does my agent have?". It should list `health`, `peers`,
   `approvals`, `ping`, `feed_read` and the rest.

> [!NOTE]
> Not yet run behind a live gateway. Two things to watch for, and to record in
> CLAIMS.md: whether the gateway accepts an external target on a port other
> than 443, and whether its MCP surface passes the agent's plain JSON responses
> through unchanged. If the port is refused, tell us: serving the owner's door
> at `/mcp` on the public port is a small change.

Before the gateway is set up, or when it is down, you can still reach the
owner's door from your own machine with the CLI. It reads the owner key from
`.env`:

```bash
LABAGENT_OWNER_URL=https://<app>.fly.dev:8443/mcp ./run.sh health
```

## 5. Call yourself through your gateway

The quickest proof the route signs: your agent calls its own access point.
Ask your coding agent, or use the CLI (`lab` below). Add yourself as a peer,
**without** a `gateway_did` for now. No restart is needed: peers are your
agent's own data, changed through your owner tools.

```bash
lab() { LABAGENT_OWNER_URL=OWNER/mcp ./run.sh "$@"; }
lab peers add me https://<your-access-point>   # --api-key-env VAR if it wants a key
lab ping me
```

Expect `self-asserted` with the reason *"verified, but gateway did:webvh:… is
not pinned for any peer"*. That means both proofs verified and your gateway's
DID is the one named. Pin it, exactly as the reason printed it, and ping again:

```bash
lab peers pin me 'did:webvh:…'
lab ping me
```

Expect `gateway-verified`.

Calling the access point by hand instead? A `422` with
`identity_validation_failed` means the message did not carry the
`…/agent-identity/v1` self-description the Identity element reads. The gateway
refuses it before your agent sees it (C13). `labagent` always sends it.

Pinning your own gateway is for this check only. For real peers, the DID comes
from the peer, out of band, not from their first message.

## 6. Measure (for CLAIMS.md)

With `LABAGENT_CAPTURE_DIR` set, every request that passed the key gate, and
every card fetch, is written as one file each. With Docker Compose, set
`LABAGENT_CAPTURE_DIR=/app/captures`; the files appear in `captures/`. Check
one:

```bash
./run.sh verify captures/<file>.json
```

That runs on your machine and resolves the gateway's DID over the network. It
reports:
- where the presentation arrived, and whether it came as a JSON string (C1);
- whether both proofs verify (C7), and again under the strict proof purpose (C6);
- which credential headers reached the agent (C10);
- any fabric hop stamp (C3).

A capture of the card fetch shows whether the gateway injected the key there
(C9).

Captures hold real DIDs and hostnames. `captures/` is git-ignored: keep it
that way, and unset `LABAGENT_CAPTURE_DIR` when you are done.

## Keeping it up

- Rerun `check.sh` after every deploy, restart or key rotation.
- **To rotate a key:** add the new one alongside the old
  (`LABAGENT_API_KEYS=new,old`), move the gateway's secret to the new one, then
  drop the old.
- **Your agent's memory** is the volume. On Fly, `fly volumes snapshots list`
  shows the daily snapshots.
