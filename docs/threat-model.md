# Threat model

This agent speaks for a person, to other people's agents. What it defends
against, and what it does not.

## Defended

| Threat | Defence |
|---|---|
| Somebody calls the agent round the gateway | The public door wants the key only the owner's gateway injects. Without it the answer is 401, with a wrong key 403 (`gate.py`). |
| Somebody reaches the owner API | It is a separate port, bound to 127.0.0.1, and it refuses any client that is not on loopback, whatever the bind address. The gateway key does not open it. |
| A peer claims to be another peer | Attribution uses the issuer of a verified presentation, compared with the DID you pinned. A name in the payload is not enough. |
| A forged or edited presentation | Both proofs are verified. Any edit to a signed field fails. |
| A peer makes the agent call somewhere new (SSRF, relaying) | Outbound addresses come only from `peers.toml`. No payload carries a callback. |
| Prompt injection in counterparty text | Text is length-capped and stripped of control and bidi characters. It is shown quoted and is never an input to a decision. Modules decide on validated fields. |
| A peer floods the feed | Deliveries are accepted only from peers you subscribed to, at most 30 a minute per peer. |
| Duplicate or replayed deliveries | The A2A message id is deduplicated, and so are module ids (`item_id`). |
| The agent commits its owner without asking | Anything binding goes to the approval queue by default. |

## Not defended, and said out loud

- **Replay of a presentation.** Nothing binds a presentation to its request
  (CLAIMS C8). A captured presentation verifies anywhere for up to a year. The
  gateway key on the door is what makes this tolerable. It does not remove it.
- **Pinning is trust on first use.** You exchange gateway DIDs out of band.
  Nothing revokes a pin until the Lab runs a trust registry.
- **The DID log's history is not checked.** `did:webvh` is resolved as
  `did:web` (see `trust/resolve.py`). Whoever controls the gateway's host and
  TLS controls its key.
- **Proof purpose is not enforced** by default (CLAIMS C6).
- **The participant-to-participant hop is unmeasured** (CLAIMS C12). If the
  receiving gateway re-signed inbound traffic, the issuer the agent sees would be
  its own gateway, and "gateway-verified" would say nothing about the sender. M4
  settles this.
- **Your own machine.** The SQLite file holds everything the agent received, in
  plain text.
