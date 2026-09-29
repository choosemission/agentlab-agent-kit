# Putting your agent behind your gateway

> [!WARNING]
> Experimental. Gateway dashboard labels move between versions. Where a step
> names one, it is the name observed on the date in [`CLAIMS.md`](../CLAIMS.md).
> If yours differs, the [`affinidi-agent-surfaces`](https://github.com/choosemission/agent-gateway-skills)
> skill describes the alternatives.

What you end up with:

```
another agent ─► your access point ─► [Identity element signs the caller]
                        │                   [your key injected]
                        ▼
                 cloudflared tunnel ─► agent :8080     owner API :8081 (container loopback)
```

The gateway is the only thing meant to reach the agent. The key it injects is
what makes that true: the tunnel URL is public, and without the key it refuses
everything but the agent card and the health check.

## 1. Run the agent and the tunnel

```bash
cp .env.example .env              # set LABAGENT_NAME; LABAGENT_API_KEYS=$(openssl rand -hex 32)
touch peers.toml                  # must exist, even empty
docker compose -f deploy/docker-compose.yml up --build -d
./deploy/check.sh                 # every line PASS
```

`check.sh` finds the tunnel's hostname in the logs and prints it. Call it
`TUNNEL` below. It changes whenever the tunnel container restarts.

## 2. Build the surface

On your gateway's dashboard. Gateway-level objects come first, because a
surface can only reference what already exists.

1. **Management → Secrets**: a new secret holding the value of
   `LABAGENT_API_KEYS` from your `.env`.
2. **A new A2A surface.**
   - **External target**: `TUNNEL/`, with the trailing slash. The agent answers
     JSON-RPC at its root.
   - **Target credential**: the secret from step 1, on header **`x-api-key`**.
     It has appeared both as *Target Authentication* on the Managed Agent node
     and as an API key credential on the Managed Agent → External leg.
   - **Access point**: a route of your choice. Source authentication is up to
     you. If you set any, give the credential to `peers.toml` in step 4
     (`api_key_env`).
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

Then set `LABAGENT_PUBLIC_URL` in `.env` to the access point's URL, and
`docker compose -f deploy/docker-compose.yml up -d agent` to pick it up.

```bash
./deploy/check.sh https://<your-access-point>   # adds: the card, through your gateway
```

## 3. Call yourself through your gateway

The quickest proof the route signs: your agent calls its own access point.
Add yourself to `peers.toml`, **without** a `gateway_did` for now:

```toml
[peers.me]
url = "https://<your-access-point>"
# api_key_env = "MY_ACCESS_POINT_KEY"   # only if the access point wants one
```

```bash
docker compose -f deploy/docker-compose.yml restart agent
docker compose -f deploy/docker-compose.yml exec agent python -m labagent ping me
```

Expect `self-asserted` with the reason *"verified, but gateway did:webvh:… is
not pinned for any peer"*. That means both proofs verified and your gateway's
DID is the one named. Pin it:

```toml
[peers.me]
url = "https://<your-access-point>"
gateway_did = "did:webvh:…"          # exactly as the reason printed it
```

Restart the agent and ping again. Expect `gateway-verified`.

Pinning your own gateway is for this check only. For real peers, the DID comes
from the peer, out of band, not from their first message.

## 4. Measure (for CLAIMS.md)

With `LABAGENT_CAPTURE_DIR=/app/captures` in `.env`, every request that passed
the key gate, and every card fetch, is in `captures/` as one file each. Check
one:

```bash
./run.sh verify captures/<file>.json
```

That runs on the host and resolves the gateway's DID over the network. It
reports:
- where the presentation arrived, and whether it came as a JSON string (C1);
- whether both proofs verify (C7), and again under the strict proof purpose (C6);
- which credential headers reached the agent (C10);
- any fabric hop stamp (C3).

A capture of the card fetch shows whether the gateway injected the key there
(C9).

Captures hold real DIDs and hostnames. `captures/` is git-ignored: keep it
that way, and take `LABAGENT_CAPTURE_DIR` out of `.env` when you are done.

## Keeping it up

A quick tunnel is for a measurement. For an agent other people depend on, use a
**named** Cloudflare tunnel on a hostname you own, so a restart does not change
the external target. Swap the `tunnel` service's command for
`tunnel run --token $CLOUDFLARE_TUNNEL_TOKEN`, and route the hostname to
`http://agent:8080` in Cloudflare. Rerun `check.sh` after every redeploy,
restart or key rotation.
