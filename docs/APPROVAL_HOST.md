# Approval host — the usable last mile of promotion (APPROVAL UX P1)

**STATUS: WORKING AND TESTED IN-PROCESS. NOT YET CONNECTED TO A DEPLOYED
TRUSTED HOST. Until that host exists, every path reports the exact missing
dependency instead of approving anything.**

**Ordinary-user integration:** `inline_approval.prepare_inline_approval`
returns one native-host card; the host renders Approve/Reject in the existing
conversation and calls `complete_inline_promotion` only with its own signed
decision. There is deliberately no same-UID `approve` command or TTY fallback.
See `docs/INLINE_APPROVAL.md`.

## What this solves

Users could forge and validate a skill, but completing promotion required a
full protected-operator deployment (dedicated accounts + systemd + socket
install). The middle — one human saying *approving this, for real* from a
simple interface — had no contract a trusted host could implement.

The contract below is that middle. It reuses the existing promotion engine
unchanged (`operator_promotion.promote_validated_skill` +
`persist_operator_promotion`; same `VALIDATED -> PROMOTED` transition, same
registry, same receipts) and adds the smallest trusted-host seam:

- chat or an agent can **request** promotion (`approval.build_approval_request`
  — expires, digest-bound; chat alone never authenticates);
- a **trusted host** (its own process, its own account, its own secret key)
  authenticates the human in whatever UI it has — one Approve/Reject action —
  and signs the decision with its own HMAC key;
- the host calls one protected completion operation with that decision;
- the engine verifies everything and, only if all checks pass, performs the
  real registry transition and writes real receipts.

## The flow in user terms

```text
user:   Forge this book into a skill.
agent:  Skill created and validated.                     (existing pipeline)
user:   Promote it.
agent:  Promotion request ready. Please review and approve.
        (stages approval-request.json + approval-evidence.json +
         current-candidate.json; chat CANNOT approve)
trusted host:  shows the inline card; the human clicks Approve; the host
        independently authenticates the human, signs the decision and calls
        the protected completion API outside agent tool permissions
agent:  Skill successfully promoted.                     (real PROMOTED state)
```

## What the host supplies and gets back

Inputs on disk (the first three are staged by the requesting agent; the host
verifies them before displaying anything):

| File | Meaning |
| --- | --- |
| `approval-request.json` | the promotion request (identity, receipts, expiry) |
| `approval-evidence.json` | digest-bound evidence items for the exact package |
| `current-candidate.json` | staleness binding: "this is the exact candidate now" |

The signed decision is host-owned state or an in-memory host value. It must
never be written into the agent-staged request directory.

A host backend builds the signed decision in one call:

```python
from knowledge_forge.approval_host import build_host_decision
decision = build_host_decision(
    request,
    decision="APPROVED",
    decided_by=f"toadaid-app:user:{user_id}",   # as the HOST verified it
    host_id="toadaid-app",
    decision_nonce=secrets.token_hex(16),
    decided_at=now,
    host_key=<host secret bytes>,
)
```

The host then calls the command-free adapter:

```python
from knowledge_forge.inline_approval import complete_inline_promotion
result = complete_inline_promotion(
    request_dir,
    bundle_dir,
    signed_host_decision=decision,
    registry_path=protected_registry,
    host_key_path=protected_host_key,
    now=now,
)
```

Success output is a real promotion receipt
(`promoted: true`, `model_authorized: false`, registry digest, file paths).
The adapter raises a typed refusal on failure; the diagnostic host CLI exits 2
with an `ok: false` JSON naming that refusal.

## What is verified before anything is promoted

| Requirement | Check |
| --- | --- |
| Altered decision | full-record HMAC-SHA256 signature over canonical bytes |
| Altered / mismatched request | decision binds `request_sha256`; request ↔ evidence ↔ candidate binding re-verified; manifest/receipt/lifecycle digests compared against the delivered bundle |
| Expired | request `expires_at` enforced at authorize time; a decision created after expiry cannot exist |
| Repeated (replay) | durable single-use SQLite ledger (`approval-host-decisions.sqlite3`, `0600`) next to the registry; a consumed decision refuses |
| Not approved | non-`APPROVED` decisions authorize nothing |
| No trusted host | typed refusal: *"no authenticated approval host is available…"* — never a fallback |
| Engine invariants | unchanged: `VALIDATED` state only, registry validation, anti-stale write order, receipt-last persistence (`operator_promotion`) |
| Agent impersonation | the decision signature requires a host key that never passes through the agent process; the key file must be `0600`, not group/other readable, and owned by the process using it — a distinct host account in a real deployment |

The review TUI and its receipts remain exactly as before
(`REVIEWED != AUTHENTICATED`): a review receipt still cannot authorize
anything (`issue_operator_authorization_from_review` still always raises).

## How to connect ToadAid-app (or any independently trusted host)

1. Provision (administrator step, outside everyday user flows): a host
   account for the approval service, a directory it owns (`0700`) holding the
   host key file (`0600`) and the governed registry state.
2. In the host backend: after the human clicks Approve **with the host's own
   authentication already established**, call `build_host_decision` with the
   host key and pass the in-memory decision to `complete_inline_promotion`.
   Do not expose either operation as an agent tool.
3. Show the returned receipt to the user. Do not paraphrase a failure as
   success: `promoted: true` is the only success signal.

## What is NOT yet live (exact missing dependencies)

- **A deployed trusted host.** This repo provides the adapter contract, not a
  ToadAid-app/Codex native confirmation implementation. Until a host connects
  its protected UI event, "approve it" remains intent awaiting host approval,
  not a promotion.
- **The registry itself** must be initialized by an administrator
  (`--registry` must already exist; the approve flow refuses to create it).
- For hardened multi-host deployments the existing
  `docs/LINUX_PROTECTED_PROMOTION.md` socket operator remains the stronger
  path; the approval-host contract drives the same engine with the same
  authorization type, verified by signature + ledger instead of socket peer
  identity. The two paths do not conflict.

## Honest limitations

- The host key is the whole trust anchor. If it leaks, approvals can be
  forged *and replay protection is the only remaining brake* (single use, so
  each leak is worth exactly one already-consumed decision).
- The key-file ownership check is process-local (`euid`); when the host runs
  under the same account as the agent, this contract is *not* independent of
  the agent process and must not be claimed as such.
- Ledger and key are host-local files; a host that wants multi-user
  reconciliation must mirror the consumed decisions into its own audit store.

**Laws (unchanged):** `VALIDATED != PROMOTED`, `REVIEWED != AUTHENTICATED !=
AUTHORIZED != EXECUTED`, `KNOWLEDGE != AUTHORITY`, `ACCEPTED !=
AUTHENTICATED`. Chat can request; only a signed, single-use decision from a
trusted host completes a real promotion.
