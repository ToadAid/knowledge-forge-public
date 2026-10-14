# P3B: trusted human-promotion bridge — P1 contract boundary

## Current status: untrusted handoff only (NOT human authentication)

P3B-P1 defines the handoff between an ordinary agent and a **future, independently
protected operator host**. The agent may validate a package and ask for review
using a bounded request/evidence contract. Neither a handoff nor a local TUI
review receipt is an authenticated approval, one-time authorization, or registry
promotion. This cut does not invoke a privileged operator or write a registry.

```text
ordinary agent (untrusted; may author/forge candidate)
   -> VALIDATED package + signed-by-nobody evidence/request handoff
   -> STOP at host trust boundary
   -> [P3B-P2 future] separate protected host independently verifies bytes,
        authenticates actual human, enforces one-time decision and expiry
   -> [P3B-P3 future] controlled privileged operator execution + receipt,
        registry reconciliation, discover/load proof
```

The handoff's `requesting_principal` is a caller-supplied LABEL, not an
identity proof. Hashes are tamper-detection against an independently selected
snapshot; they are not signatures or authenticity proofs. A model may create,
modify, or replay the request and evidence. Validation of a handoff checks the
specified files supplied by the caller; it **does not** establish that those
files are host-protected, and cannot grant authorization.

`src/knowledge_forge/host_handoff.py` provides:

- `build_untrusted_review_handoff(files, lifecycle, ...)`: packages strictly
  bounded request/evidence for one exact `VALIDATED` candidate, with <=15 min
  requested lifetime and explicit zero authority flags.
- `validate_untrusted_review_handoff(value, files, lifecycle, observed_at=...)`:
  fail-closed revalidation of hashes, package and lifecycle, request bindings,
  expiry and unknown fields. Returns non-authorizing evidence **only**.
- No CLI, operator integration, human authentication, approval receipt, event
  listener, socket, network activity, subprocess or registry mutations.

## The existing operator seam must be protected, not relabeled

`operator_cli.py` constructs `OperatorAuthorization(verified_by_host=True)`
**on the assumption that invocation happened after a real external host/shell
authentication**. The string references are NOT authentication. If a generic
agent shares that UID, filesystem or executable access, simply telling it not
to invoke the operator is no security boundary. The local TUI can also be
automated; a recorded `APPROVE` does not grant privilege.

Before any live promotion bridge is enabled, P3B-P2 must establish and verify:

1. A separate OS account/process or equivalent real privilege separation;
   generic-agent process cannot use the operator's credentials/state/socket
   or write promoted registry files. Same-UID separation is insufficient.
2. Authenticated human ceremony at the protected host (not model-typed strings
   or a terminal accessible to the agent), explicit selected candidate review.
3. Trusted snapshot re-read + exact binding of package, lifecycle, registry
   head, review decision, operator principal, identity and policy.
4. Short-lived random challenges and protected atomic consume-once state;
   reject replay, stale registry, changed candidate, expiration and cancellation.
5. Atomic/durable writes, audit and crash/retry reconciliation; **never** infer
   success from an unknown execution outcome or a green test alone.
6. Independent refusal tests under actual agent and operator UIDs. Keep
   privileged `knowledge-forge-operator` absent from ordinary agent PATH and
   callable interfaces; no agent-controlled `sudo` or privileged host command.

The trusted host must also prevent an agent from choosing arbitrary privileged
output paths or authorizations. Promotion is human-initiated from an independently
reviewed fixed policy and exact host-controlled candidate snapshot.

## CI and newcomer pilot

CI runs on GitHub but is **not a required merge rule** until an administrator
changes the active `main` ruleset and verifies a blocked failing PR. P3B-P1 can
be developed as an uncommitted, non-authorizing cut meanwhile; no promotion may
be enabled on that basis.

After P3B-P2/P3 are independently verified, test Claude+GLM as a fresh generic
user from an empty directory with isolated config, credentials, prior chats and
private sources. Report INSTALL / AUTHOR / PREVIEW / REVIEW / PROMOTE / CONSUME
and SECURITY separately, with source-vs-package-vs-governed retrieval labels.
A `VALIDATED` package is not `PROMOTED`; a raw-source answer is not governed
`discover` + `load-current`. Do not install any privileged operator into the
agent-facing prefix.
