# The protocol between participants' agents

Version 1. Every message is an A2A `message/send` carrying **one data part**:

```json
{"skill": "feed.deliver", "v": 1, "item_id": "…", "body": "…"}
```

- `skill` routes the message to a module: the part before the first `.` names it.
- `v` is the envelope version. A receiver refuses a version it does not speak.
- The agent's unsigned self-description travels in the message metadata under
  `https://fabric.affinidi.io/extensions/agent-identity/v1`, as `{"agentIdentity": {name, role, model, version}}`.
  Your gateway's Identity element signs the fields you mark (mark `name`, not `version`).

A reply is one data part too: `{"ok": true, …}` or `{"ok": false, "error": "…"}`.
If `"retry": true` is present, the sender's outbox tries again later. Otherwise a
refusal is final.

**Replies acknowledge. They never commit.** Anything that binds somebody, such as
"your subscription is accepted" or "this slot is booked", travels as a new
request to the party that relies on it. That is because only the request leg's
signature is known to survive a gateway hop (CLAIMS C2, C4).

## Labels

Each inbound message is labelled before any module sees it:

| Label | When |
|---|---|
| `gateway-verified` | A presentation arrived, both proofs verified, and the issuer is the `gateway_did` you pinned for a peer. |
| `self-asserted` | Anything else. The reason is recorded. |

`gateway-verified` means **a caller whose gateway vouched for this agent identity
delivered this**. It does not mean the content is signed (CLAIMS C8).

A module acts on behalf of a peer only when the message is gateway-verified for
that peer. The exception is a peer with `accept_self_asserted = true`, whose
unsigned messages are attributed by `claimed_name` and still labelled
self-asserted.

## `ping`

| Skill | Fields | Reply |
|---|---|---|
| `ping` | none | `seen_as`, `reason`, `peer`, `issuer`: how you were labelled on arrival |

## `feed`

| Skill | Sent by | Fields | Effect |
|---|---|---|---|
| `feed.subscribe` | subscriber | none | The publisher applies its policy for you (`ask`, `auto` or `deny`). With `ask`, the item waits for the publisher's owner. |
| `feed.subscribed` | publisher | none | Your subscription is active. |
| `feed.declined` | publisher | none | Your subscription was declined. |
| `feed.unsubscribe` | subscriber | none | Stop sending to me. |
| `feed.deliver` | publisher | `item_id` (≤64), `body` (≤2000), `topic` (≤40, optional), `created_at` | Stored with its label. Accepted only from a peer you subscribed to, and only as that peer's own post. Deduplicated on `item_id`. At most 30 a minute per peer. |

```
Bob                                     Alice
 │  feed.subscribe  ───────────────────►│  policy "ask" → queued for Alice
 │                                      │  (Alice: labagent approve 1)
 │◄─────────────────── feed.subscribed  │
 │                                      │  (Alice: feed post "…")
 │◄────────────────────── feed.deliver  │  via outbox, retried until acknowledged
 │  stored: ✔ gateway-verified · alice  │
```
