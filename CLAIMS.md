# Claims about the Agent Gateway

Every behaviour of the Affinidi Agent Gateway that this toolkit's code depends
on is listed here, with where it came from and when it was last checked. The
code cites these by number. If the gateway contradicts one, open an issue with
what you observed and when. Do not quietly change the code.

**Sources.**
- **O-Lab**: observed by the Agent Lab team against a live gateway, recorded in
  the Lab's own repository. **Not yet re-measured with this toolkit.** Milestones
  M3 and M4 are where that happens.
- **O**: observed with this toolkit (dated, with the gateway it was seen on).
- **V**: vendor guidance (dated).

**Last full review:** none yet. Everything below is inherited from the Lab.

| # | Claim | Source | Checked | Where it matters |
|---|---|---|---|---|
| C1 | On the request leg, the sending gateway attaches a Verifiable Presentation under `https://fabric.affinidi.io/extensions/agent-identity-binding/v1`, **serialised as a JSON string** in `verifiablePresentation`. | O-Lab | 28 Aug 2026 | `trust/identity.py` |
| C2 | Across a `fabric://` gateway-to-gateway hop, the **originating** gateway's presentation arrives intact. The relay does not re-sign. | O-Lab | 16 Sep 2026 | the whole label rule, `trust/provenance.py` |
| C3 | The hop's route is stamped at `params.message.metadata["x-affinidi-fabric-gateway-did"]`. It is not on every message, and its order is unverified. | O-Lab | 16 Sep 2026 | recorded as `hop`, never relied on |
| C4 | Response-leg signing across a hop is **unproven**: no response-leg credential reached the client in the one measurement. | O-Lab | 16 Sep 2026 | why nothing is labelled on a reply, and why the feed pushes |
| C5 | Response-leg signing is A2A v0.3-shaped (`result.status.message.metadata`). A v1.0 reply is forwarded unsigned, without an error. | O-Lab | 8 Sep 2026 | the `a2a-sdk==0.3.25` pin |
| C6 | Gateway credentials are signed by `#key-2` with `proofPurpose: assertionMethod`, but the gateway's DID document lists only `#key-1` under `assertionMethod`. A strict verifier rejects every one. Surface documents do list `#key-2`. | O-Lab | 16 Sep 2026 | `LABAGENT_STRICT_PROOF_PURPOSE` is off by default |
| C7 | Both proofs, the credential's and the presentation's, verify with `eddsa-rdfc-2022` (proof-options hash, then document hash). Two independent implementations agree. | O-Lab | 16 Sep 2026 | `trust/verify.py` |
| C8 | Nothing binds a presentation to its request: there is no nonce, challenge or `domain`. The credential lives for a year. | O-Lab | 16 Sep 2026 | label wording; `docs/threat-model.md` |
| C9 | An A2A surface fetches the target's agent card **without** injecting the target credential. It injects only on the JSON-RPC path. | O-Lab | 31 Aug 2026 | the card is served without the key (`gate.py`) |
| C10 | The gateway forwards the caller's own `Authorization` header and injects its credential alongside it, so `x-api-key` must be read first. | O-Lab (code, not a dated measurement) | — | `gate.presented_credential` |
| C11 | The gateway carries A2A and MCP only: no HTTP or LLM targets, and "pipes" no longer exist. | V (Reference Guide v0.3.10) | Aug 2026 | an LLM-using module holds its own key |
| C12 | A **participant-to-participant** hop (one participant's transit point to another's access point, or a fabric hop between two participants' gateways) has **not been measured**. | — | — | M4. Until then, "gateway-verified" is proven only against the stand-in gateway |

## Contradictions

None recorded yet.
