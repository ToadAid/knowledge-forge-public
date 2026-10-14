# P3B-P2C-P3-P2-P0 — host-challenge transaction pilot (NOT host authentication)

**STATUS: UNTRUSTED, TEST-ONLY SQLITE CHALLENGE SIMULATION. NO HUMAN AUTHENTICATION, HOST ATTESTATION, PROTECTED ACCOUNT, AUTHORIZATION, OPERATOR, SERVICE, CREDENTIAL, OR PROMOTION.**

## Where this belongs

This is the first bounded P2C-P3-P2 cut following P2C-P3-P1's SQLite transaction pilot, merged in public PR #14 at `f9f7d7731019caeb56fe2d2d006175f110a7f5d1`. P2C-P3-P1 demonstrated one winner among competing processes over one local SQLite DB file. P2C-P3-P2-P0 exercises independently sampled challenge identifiers, current review-binding comparison, cancellation, and expiry against the same test-only transactional model. **This does not instantiate a trusted host.**

## Prototype behavior

- `src/knowledge_forge/host_challenge_pilot.py` uses `secrets.token_hex(32)` to generate 256-bit random *simulation identifiers* **inside the caller's Python process**. A model or agent can call this code itself; generation alone does not establish host origin. Identifiers are *not bearer authorization tokens*.
- A challenge row binds the **caller-supplied** exact request digest, canonical target skill ID and version, package receipt digest, validated lifecycle head, expected registry-head digest, purpose, issue time, and expiry. No independent source re-read occurs.
- The effective expiry is at most 900 seconds after caller-process time, and cannot extend beyond the supplied review request expiry. The optional injected clock exists only for deterministic tests. Even the default Python wall clock is **not attested trusted time**.
- `BEGIN IMMEDIATE` protects each local SQLite state transition. Two subprocesses competing to simulate spending the same ID produce one success and one refusal. Replay, stale version, duplicate ID, incorrect binding, expiry, and cancellation refuse. Reopening after commit still refuses a second spend.
- Stored event checksums and initial binding/expiry checks detect selected accidental or unauthorized-in-place edits; an attacker controlling the database can rewrite the whole history. A SQLite commit/reopen and process-exit-after-commit test do not prove physical power loss, disk firmware, filesystem guarantees, trusted audit, or rollback resistance.
- API methods are deliberately `issue`, `inspect`, and `transition` **without CLI integration**. Accepted transition actions are only `SPEND_SIMULATION` and `CANCEL_SIMULATION`; no code calls `operator_cli`, `operator_promotion`, or `OperatorAuthorization`.
- Every result reports `execution_authority=NONE`, `tool_grants=[]`, explicit false human/host/clock/storage/authorization/operator/promotion fields, and untrusted source labels.

## Real protected host: additional acceptance gates NOT implemented here

1. **Separately operated identities:** Independently human-approved provisioning of `kf-agent` and `kf-operator`; verify their effective Linux UID, groups, sudoers, polkit, container engines, socket/FD inheritance, SSH agent, and TTY access. The existing `tommy` session has sudo and writable Docker access and is NOT agent-isolated.
2. **Trusted launch and input:** Root-owned pinned operator code and libraries under `/opt/toadaid-kf-operator`, secure ancestry and no agent-controlled import/config/code paths, with read-only approved binaries and environment.
3. **Protected database:** SQLite DB, journal and directory under protected operator ownership with verified ACL, mount, link, open flags, interprocess locking, durable ordering and fsync; real privileged UID test. Caller-selectable test DB paths are not sufficient.
4. **Secure human authentication:** Independently authenticated admin-only console/TTY inaccessible to Claude/Codex/GLM and delegated model tools. Host-generated challenge displayed with exact candidate, registry head, request, source/evidence and purpose; genuine human confirmation bound to the challenge. No signed-off approval is provided by this cut.
5. **Independent truth and time:** Operator must re-read validated candidate bytes, lifecycle/receipts and current registry head independently under trusted identity, enforce trusted clock and <=15-minute request/challenge validity, refuse clock rollback and stale registry/candidate.
6. **Protected one-shot audit:** Authenticate human decision independently, persist consume-once intent and outcome with code-owned append-only receipts, handle process death, uncertain external effects, replay, restarts, cancellations, hardware/power failure and reconciliation without blind retry.
7. **Adversarial promotion gate:** Real separate-UID tests prove agent cannot call the operator or modify its code/state, inherit credentials/FDs, or trick the human display. Production promotion remains OFF until this and independent operator integration are completed.

## Scope and testing

This cut adds only three files: `host_challenge_pilot.py`, its focused synthetic tests, and this design document. It neither edits the existing operator nor exports a new tool, daemon, installer, credential, ledger service or public promotion surface. Tests use disposable temp DBs and include actual two-process spend race, crash-after-commit without response, bad issuer clock, binding substitution, event/expiry tampering, path-symlink refusals and permanent zero authority. TNG focused + entire public regression + no-write installer check must pass before any PR. The guarded P0 script does **not** commit, push, create a PR, merge or provision accounts.

**Boundary:** `CANON != SKILL`; `KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY`; `VALIDATED != PROMOTED`. Native Windows protected operator track is separate.
