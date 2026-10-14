# P3B-P2C-P3-P1 — SQLite transaction pilot (NOT a protected authorization ledger)

**Status: UNTRUSTED TRANSACTION REHEARSAL ONLY. No authentication, approvals, privileged operator, protected data, credentials, installed services or promotion.**

## Baseline

P2C-P3-P0 merged in public PR #13, commit `32a7e749a0a5af119b6795d860737aebd685dbbd`. Its pure-data snapshots intentionally cannot prevent two independent processes from consuming the same old snapshot. This P1 cut uses the Python standard-library `sqlite3` database, local file transactions with `BEGIN IMMEDIATE`, expected revisions and an event-chain check to rehearse a single winner across **separate processes accessing one disposable local database file**.

## What this pilot actually provides

- `SQLiteLedgerPilot` has no CLI and is not exported from the ordinary installer. It only accepts an explicit absolute `.sqlite3` path with an existing parent; symlink ancestors and certain protected/system paths are rejected as an additional safety fence, not an OS security proof.
- `prepare(...)` creates one immutable simulation ID bound to a caller-supplied digest and a caller-supplied request interval of at most 15 minutes; it refuses duplicate IDs.
- `transition(...)` uses one `BEGIN IMMEDIATE` transaction to compare ID, binding, revision, prepared state and supplied timestamp before committing `CONSUME_SIMULATION`, `CANCEL_SIMULATION`, or `RECOVER_UNCERTAIN`. Later attempts with the same ID/revision fail.
- Separate-process contention produces exactly one simulated consume and one refusal in tests. A subprocess that exits **after a committed consume but before reporting success** cannot consume again after reopen; its outcome must be reconciled externally.
- SQLite's rollback journal and `synchronous=FULL` are used for committed test-file operations. Local commit/reopen and process crash *simulation* are tested. **Physical power-loss durability, malicious host rollback, network filesystem consistency and storage hardware semantics are not proven.**
- An event checksum chain covers the initial caller-supplied digest and expiry, then subsequent transitions. This can catch accidental edits but **cannot authenticate** a database controlled by an agent that can rewrite both state and checksums.
- All return values retain `execution_authority=NONE`, false authentication/promotion flags and empty tool grants. There is **no mechanism that can create `OperatorAuthorization`, invoke `operator_cli`, write a real registry, or decide human intent**.

## Deliberate refusal / nonclaims

A named SQLite file in the `tommy` session is mutable and rollbackable by `tommy` or an agent operating as that user. Host timestamps, digest, ID and SQLite transactions are **not** human authentication, independent candidate/registry evidence or a protected one-time authorization. `RECOVER_UNCERTAIN` is terminal and never resumes or promotes. Expired or cancelled attempts refuse; no synthetic state grants an approval.

No privileged account is provisioned by this cut. TNG still requires independent human-administrator review and creation of separate `kf-agent` and `kf-operator` identities, root-owned pinned operator code, protected operator ledger/state and safe service/TTY isolation. The agent-visible `tommy` account currently has sudo and Docker/socket access, so do **not** activate promotion from this session.

## Test gates

Focused tests cover duplicate ID, stale revision, tamper refusal, incorrect digest, expiry, cancellation, recovery, restart persistence, malicious value injection, a real two-process race, and a subprocess crashing after commit. The complete public regression plus `bin/knowledge-forge-install-agent --check-only` must also pass on TNG. Existing GitHub required CI checks continue to gate any eventual PR. No auto-commit, push, merge or deployment in the P1-P0 test cut.

## Follow-on cuts (not part of this one)

1. **P2C-P3-P2**: Independently protected state/actor ownership, trusted time, pinned code, host-supplied unpredictable one-use challenge and authenticated human review. Harden durable audit, process death, power-loss simulation, schema migration, filesystem and SQLite threat models.
2. **P2B2B-P2 human-only ceremony**: Separately approved provisioning, effective sudo/polkit/container/service/access tests under real distinct Linux UIDs. Without this, no protected host exists.
3. **P3**: Revalidate exact current candidate and registry from protected paths, bind and consume a real authenticated decision once, then perform adversarial two-UID tests before any fresh Claude/GLM agent test involving promotion.

Cross-platform contract remains unchanged: `VALIDATED != PROMOTED`; `KNOWLEDGE != AUTHORITY`. Native Windows installer work by Numair/Claude remains separate.
