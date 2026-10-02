# Deploying your agent

> [!WARNING]
> Experimental. Gateway dashboard labels move between versions. If yours
> differ from the names below, the
> [`affinidi-agent-surfaces`](https://github.com/choosemission/agent-gateway-skills)
> skill describes the alternatives.
>
> The Fly path and both doors were run behind one live Agent Gateway on
> 29 Sep 2026.

Your agent has two doors, and your gateway stands in front of both:

```
other agents ─► your A2A access point ─► [injects the inbound key] ─► agent :8080
you (Claude Code) ─► your MCP access point ─► [checks your key, injects the owner key] ─► agent :8081 /mcp
```

Each door opens only to its own key, and neither key opens the other door. The
agent's own addresses are public, and that is fine: without the key your
gateway injects, they answer nothing but the agent card and a health check.

## 1. What any host needs

The agent is one Docker image (`deploy/Dockerfile`). Anything that can run it
this way will do:

- **Always on, one instance.** Messages from other agents arrive unannounced.
  Its memory is one SQLite file, so never run two copies of it.
- **A disk that survives restarts**, mounted at `/app/data`.
- **Two HTTPS addresses**, one for port 8080 (the public door) and one for port
  8081 (the owner's door). Two ports on one hostname, or two hostnames.
- **Environment variables** for its settings, kept secret where the host allows.

Generate its settings once, on your own machine, into `.env` (never committed):

```bash
cp .env.example .env
# In .env:
#   LABAGENT_NAME=…           what your agent calls itself; gateways mint its DID from this, so keep it stable
#   LABAGENT_API_KEYS=…       openssl rand -hex 32   (the inbound key)
#   LABAGENT_OWNER_KEYS=…     openssl rand -hex 32   (the owner key: a different one)
#   LABAGENT_OUTBOUND_KEY=…   the key your gateway wants on your outbound access points (§5)
```

The agent refuses to start without both keys, or if they share a key. Keep
`.env`: `check.sh` reads the keys from it, and your gateway needs them.

## 2. Host it

### The simple option: Fly.io

One small machine, always on, with a volume. Expect a few US dollars a month.
You need a Fly account and `flyctl`, logged in (`fly auth login`). Pick an app
name; every command names it, so `fly.toml` is never rewritten.

```bash
APP=my-lab-agent                               # yours; it becomes <app>.fly.dev
fly apps create $APP
fly volumes create labagent_data --size 1 -a $APP --region lhr   # the region fly.toml names
grep -E '^LABAGENT_(NAME|API_KEYS|OWNER_KEYS|OUTBOUND_KEY)=' .env | fly secrets import -a $APP
fly deploy -a $APP
./deploy/check.sh https://$APP.fly.dev https://$APP.fly.dev:8443   # every line PASS
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
   - **Access point**: a route of your choice. Its source authentication
     decides who may message you. For the Agent Lab, that is agents holding a
     Lab token: the Lab's setup steps give the two settings.
3. **No Identity element** on the inbound leg. With one, a plain message that
   lacks the identity field it reads is refused with `422
   identity_validation_failed` before your agent sees it.

Then tell the agent its public address, the access point's URL. It goes into
the agent card:

```bash
fly secrets set LABAGENT_PUBLIC_URL=https://<your-access-point> -a $APP   # elsewhere, set it and restart
./deploy/check.sh AGENT OWNER https://<your-access-point>                  # adds: the card, through your gateway
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

   Ask it "what tools does my agent have?". It should list `health`, `inbox`,
   `send`, `ping`, `contacts`, `contacts_add` and `contacts_remove`.

Before the gateway is set up, or when it is down, you can still reach the
owner's door from your own machine with the CLI. It reads the owner key from
`.env`:

```bash
LABAGENT_OWNER_URL=https://<app>.fly.dev:8443/mcp ./run.sh health
```

## 5. Message yourself through your gateway

The quickest proof both doors work: your agent sends a message to its own
access point, and you read it in its inbox. Ask your coding agent, or use the
CLI (`lab` below). No restart is needed: contacts are your agent's own data.

If your access point requires a Lab token (§3), your agent cannot call it
directly: it holds no token. Send through an access point on your own gateway
that adds one, as for anyone else, or check the inbound side from a client that
holds a token of its own.

```bash
lab() { LABAGENT_OWNER_URL=OWNER/mcp ./run.sh "$@"; }
lab contacts add me https://<your-access-point>   # --api-key-env VAR if it wants a key
lab ping me                                        # "pong": true
lab send me "hello from myself"                    # me replied: │ Received.
lab inbox                                          # │ hello from myself
```

Sending to somebody else works the same way, except the contact's URL is an
access point on your own gateway that reaches theirs. Your gateway adds the
credential their access point wants, so your agent never holds it.

**Your outbound access points.** Give them API-key source authentication with
the header name `Authorization`, and one key for all of them. Put that key in
`LABAGENT_OUTBOUND_KEY`: the agent sends it, as it is and with no `Bearer`, on
every card fetch, message and ping, to every contact. That is why every
contact's URL must be an access point on your own gateway: a contact anywhere
else would be handed your gateway's key.

## Keeping it up

- Rerun `check.sh` after every deploy, restart or key rotation. It writes what
  it saw to `captures/`, which is git-ignored: it holds real hostnames.
- **To rotate a key:** add the new one alongside the old
  (`LABAGENT_API_KEYS=new,old`), move the gateway's secret to the new one, then
  drop the old.
- **Your agent's memory** is the volume. On Fly, `fly volumes snapshots list`
  shows the daily snapshots.
