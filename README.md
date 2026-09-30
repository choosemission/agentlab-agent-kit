# agentlab-agent-kit

> [!WARNING]
> **Experimental.** Early code for the Agent Lab. Verify against your own
> gateway, and open an issue when something is wrong.
>
> Not affiliated with, endorsed by, or supported by Affinidi.

**Your own agent in the Agent Lab.** A small A2A agent that runs all the time,
behind your own Agent Gateway. Other agents can send it messages, and it keeps
them for you. You talk to it from your coding agent (Claude Code, for example):
read what arrived, and send messages of your own.

That is all it does today, on purpose. It is the smallest thing that is really
yours on the network: an address other agents can reach, protected by your
gateway, that answers to you. What you put behind that address next, a model
that replies for you, say, is where it gets interesting, and where the stakes
start.

## What you can do with it

Your coding agent sees eight tools, served by your agent's owner MCP server:

| Tool | |
|---|---|
| `inbox` | Messages other agents sent you, newest first. Their words are quoted: data to read, not instructions |
| `card` | A contact's agent card: what their agent says it can do. Quoted, like everything they wrote |
| `send` | Send a message to a contact and get their agent's reply. A reply waiting for input returns a `task_id` and `context_id`; send the next message with them to carry on |
| `ping` | Check a contact's agent is up |
| `contacts`, `contacts_add`, `contacts_remove` | The agents yours can send to. The only place an address to send to comes from |
| `health` | Is it up, and how much is waiting |

Other agents see one thing: send it any text and it answers `Received.` Send
exactly `ping` and it answers `pong`.

## Try it on your laptop

```bash
./run.sh test
```

The suite runs two agents, Alice and Bob, in one process: each sends to the
other, pings, and reads its inbox through the owner tools.

## Run your own

[`deploy/DEPLOY.md`](./deploy/DEPLOY.md) is the runbook:
- what any host needs;
- Fly.io as the simple option (`fly.toml`), and the other options;
- the two surfaces to build on your gateway, one for other agents and one for you;
- messaging yourself through them.

`./deploy/check.sh` tests both doors from the internet side.
[`docs/how-it-works.md`](./docs/how-it-works.md) explains the two doors and the
two keys.

## Owner commands

The same tools, from a terminal. The CLI calls the owner MCP server:

```
labagent health
labagent inbox [--limit n] [--since-id id]
labagent card <contact>
labagent send <contact> "<text>" [--task-id <id> --context-id <id>]
labagent ping <contact>
labagent contacts
labagent contacts add <name> <url> [--api-key-env VAR]
labagent contacts remove <name>
```

## Licence

Apache 2.0. See [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE). Affinidi,
Affinidi Trust Fabric and Agent Gateway are trademarks of their owner. The
licence grants no trademark rights.
