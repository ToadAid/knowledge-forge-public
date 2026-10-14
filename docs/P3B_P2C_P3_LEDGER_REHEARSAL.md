# P3B-P2C-P3-P0 — single-use ledger *rehearsal*, not a trusted ledger

**STATUS: UNTRUSTED PURE-DATA SIMULATION. NO DURABLE HOST LEDGER, AUTHENTICATION, AUTHORIZATION, OPERATOR, TOKEN, OR PROMOTION.**

## Goal

Prototype the fail-closed decision-state transition contract before building the protected host implementation. This is testable on TNG and in GitHub CI without privileged installation. It is not the final P2C-P3 protected ledger; this cut is P2C-P3-P0 only.

`ledger_simulation.py` consumes the existing **untrusted** P2C-P1 review plan and P2C-P2 host-auth requirements, compares their claimed request/candidate/registry bindings, and constructs a deterministic state with only untrusted SHA-256 checksums. Neither plan, checksum, OS label, caller clock, model output nor state transition is an authenticated fact.

## Synthetic state transitions

- `PENDING -> PREPARED_SIMULATION -> CONSUMED_SIMULATION`; an additional `CONSUME_SIMULATION` is rejected *against that same state snapshot*. These are labels for rehearsing logic; **no human decision or operator dispatch is performed**.
- `PENDING` or `PREPARED_SIMULATION` can be cancelled; cancellation is terminal.
- They can expire at or after the claimed request deadline; expiry is terminal. Ordinary transitions at/after expiry are refused.
- `RESTART_SIMULATION` after preparation or simulated consumption enters `RECONCILIATION_REQUIRED` and cannot consume again; it can be closed only as `CLOSED_NO_EFFECT` in the synthetic model before the deadline. The actual protected ledger must require independent evidence, and must never treat this caller-supplied synthetic closeout as a trusted reconciliation.
- Monotonic caller-supplied timestamps, event-parent checksum chaining, exact fields and bounded event count are checked by replay from the matching untrusted upstream plan. These are **integrity checks over caller-controlled bytes, NOT signatures**.

### Known non-claims and explicit limitations

- **No cross-process atomicity**: two copies of an old `PREPARED_SIMULATION` snapshot can both simulate consumption. The test suite deliberately demonstrates this limitation.
- **No durable storage or crash guarantees**: a temporary-file JSON roundtrip test is not fsync, atomic transaction, interprocess lock, replay protection, or evidence that a power loss preserves a spend.
- **No trusted clock, challenge, user authentication, identity attestation, or fresh independent candidate/registry read**. The agent can supply forged inputs and rewind any file it owns.
- **No invocation of `operator_cli.py`, `operator_promotion.py`, or `OperatorAuthorization(verified_by_host=True)`**. No new CLI/installer surface, scheduler, service, token, or production registry mutation.
- Every output has `execution_authority=NONE`, empty tool grants, and explicit false flags for authentication, authorization, durable storage, atomic consumption, replay protection, operator invocation, and promotion.

## Conditions for a separately approved protected host ledger

Before a production ledger or promotion path exists, an independent human-controlled administrator must provision distinct `kf-agent` / `kf-operator` Linux identities, root-owned pinned code, a protected state root, an agent-inaccessible authentication surface, and tested sudo/polkit/container/FD/socket boundaries. TNG currently lacks these accounts; ordinary `tommy` holds sudo and writable Docker socket access. Never run the host ceremony through Claude, Codex, GLM, a shared agent terminal or a model-accessible sudo session.

The later host-owned P2C-P3-P1 implementation must provide **unpredictable host-created one-use challenge**, authenticated human intent, exact current registry head + validated candidate evidence re-read under a trusted UID, code-owned durable audit, atomic consume-once with process lock and durable ordering, cancellation, expiry, replay/race/crash refusal, and explicit ambiguous-result reconciliation. Only afterward can P3 integrate protected promotion and real two-UID adversarial tests. Windows protected operator remains a separate architecture track.

Keep `CANON != SKILL`, `KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY`, `VALIDATED != PROMOTED`.
