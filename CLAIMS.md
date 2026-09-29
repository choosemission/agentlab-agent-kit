# Claims about the Agent Gateway

Every behaviour of the Affinidi Agent Gateway that this toolkit's code depends
on is listed here, with where it came from and when it was last checked. The
code cites these by number. If the gateway contradicts one, open an issue with
what you observed and when. Do not quietly change the code.

**Sources.**
- **O-Lab**: observed by the Agent Lab team against a live gateway, recorded in
  the Lab's own repository. **Not yet re-measured with this toolkit.** Milestones
  M3 and M4 are where that happens.
- **O**: observed with this toolkit (dated, with the gateway it was seen on),
  from a file written by `LABAGENT_CAPTURE_DIR` and read with `labagent verify`.
- **V**: vendor guidance (dated).

**Gateways observed on.** Named here rather than by hostname, which stays out of
this repository. The raw captures are held by whoever measured.
- **G1**: a participant gateway on `agentgateway.affinidi.io`. One A2A surface;
  access point with no source authentication; an Identity element on the inbound
  leg (Payload, meta field `agentIdentity`, `name` marked); Identity Binding VP
  on; the key injected as `x-api-key`; the agent behind a Cloudflare quick
  tunnel. Measured 29 Sep 2026 (M3), calling from the agent itself to its own
  access point.

**Last full review:** none yet. M3 (29 Sep 2026) re-measured C1, C6, C7, C9 and
C10 on G1; the rest is still inherited from the Lab.

| # | Claim | Source | Checked | Where it matters |
|---|---|---|---|---|
| C1 | On the request leg, the sending gateway attaches a Verifiable Presentation under `https://fabric.affinidi.io/extensions/agent-identity-binding/v1`, **serialised as a JSON string** in `verifiablePresentation`. | O (G1); O-Lab | 29 Sep 2026 | `trust/identity.py` |
| C2 | Across a `fabric://` gateway-to-gateway hop, the **originating** gateway's presentation arrives intact. The relay does not re-sign. | O-Lab | 16 Sep 2026 | the whole label rule, `trust/provenance.py` |
| C3 | The hop's route is stamped at `params.message.metadata["x-affinidi-fabric-gateway-did"]`. It is not on every message, and its order is unverified. | O-Lab | 16 Sep 2026 | recorded as `hop`, never relied on |
| C4 | Response-leg signing across a hop is **unproven**: no response-leg credential reached the client in the one measurement. | O-Lab | 16 Sep 2026 | why nothing is labelled on a reply, and why the feed pushes |
| C5 | Response-leg signing is A2A v0.3-shaped (`result.status.message.metadata`). A v1.0 reply is forwarded unsigned, without an error. | O-Lab | 8 Sep 2026 | the `a2a-sdk==0.3.25` pin |
| C6 | Gateway credentials are signed by `#key-2` with `proofPurpose: assertionMethod`, but the gateway's DID document lists only `#key-1` under `assertionMethod`. A strict verifier rejects every one. Surface documents do list `#key-2`. | O (G1: holds, a third gateway); O-Lab | 29 Sep 2026 | `LABAGENT_STRICT_PROOF_PURPOSE` is off by default |
| C7 | Both proofs, the credential's and the presentation's, verify with `eddsa-rdfc-2022` (proof-options hash, then document hash). | O (G1, this toolkit's verifier); O-Lab (two independent implementations agree) | 29 Sep 2026 | `trust/verify.py` |
| C8 | Nothing binds a presentation to its request: there is no nonce, challenge or `domain`. The credential lives for a year. | O-Lab | 16 Sep 2026 | label wording; `docs/threat-model.md` |
| C9 | An A2A surface fetches the target's agent card **without** injecting the target credential. It injects only on the JSON-RPC path. | O (G1); O-Lab | 29 Sep 2026 | the card is served without the key (`gate.py`) |
| C10 | ~~The gateway forwards the caller's own `Authorization` header and injects its credential alongside it, so `x-api-key` must be read first.~~ **Contradicted on G1**; see below. | O-Lab (code, not a dated measurement); O (G1: not forwarded) | 29 Sep 2026 | `gate.presented_credential`, which still reads `x-api-key` first: harmless either way |
| C11 | The gateway carries A2A and MCP only: no HTTP or LLM targets, and "pipes" no longer exist. | V (Reference Guide v0.3.10) | Aug 2026 | an LLM-using module holds its own key |
| C13 | With an Identity element on the inbound leg, a message **without** the `…/agent-identity/v1` extension is refused at the gateway: `422`, `code: identity_validation_failed`, `slot: inbound_identity`. It never reaches the agent. | O (G1) | 29 Sep 2026 | `core/envelope.py` always sends the extension |
| C14 | Fetched through the access point, the agent card's `url` is rewritten to the access point, whatever `LABAGENT_PUBLIC_URL` says. With no Identity element on the response leg, the card carries no gateway credential. | O (G1) | 29 Sep 2026 | `LABAGENT_PUBLIC_URL` is still set, for a card read directly |
| C12 | A **participant-to-participant** hop (one participant's transit point to another's access point, or a fabric hop between two participants' gateways) has **not been measured**. | — | — | M4. Until then, "gateway-verified" is proven only against the stand-in gateway |

## Contradictions

**C10 (29 Sep 2026, G1).** A caller sent `Authorization: Bearer …` to the access
point. The request the agent received carried the injected `x-api-key` and **no**
`Authorization` header. The gateway added `x-caller-did: anonymous` and
`x-caller-identity-source: self-presented` instead. The earlier claim came from
code, not a measurement. It may hold on an access point that does its own source
authentication, which G1 does not; that is unmeasured. Nothing in the toolkit
depends on the header being forwarded.
