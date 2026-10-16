# P3B UX review ergonomics P1

## Status

REVIEW-ONLY UX POLISH — NOT AUTHENTICATED, NOT AUTHORIZED, NOT PROMOTED,
NO OPERATOR INVOCATION, NO REGISTRY MUTATION, NO SCHEMA CHANGE, NO SURFACE
CHANGE, NO NETWORK, NO AGENT-RELAYED APPROVAL.

This cut changes presentation and ergonomics of the local terminal review
surface only. `src/knowledge_forge/approval.py` receives zero diff; the
permanent fail-closed seam `issue_operator_authorization_from_review`
(`src/knowledge_forge/approval.py:624-629`) is untouched. Receipts remain
review evidence, never authorization (`docs/GENERIC_APPROVAL_TUI.md`), and
`VALIDATED != PROMOTED` and
`REVIEWED != AUTHENTICATED != AUTHORIZED != EXECUTED` hold unchanged.

## Motivation

The first end-to-end human review through the P2B0 TUI (outside pilot tests) hit
four usability failures, all observable in a real session:

1. The confirmation token was derived from the request ID's last 8 alphanumeric
   characters (`_suffix`). For a request named `p10c-2024a-promotion-request`
   this rendered as `APPROVE nrequest` — the operator typed bare `APPROVE`,
   twice, and received a terse `approval confirmation mismatch` with no
   recovery information.
2. The `review` subcommands required three full input paths plus a receipt
   path; a newcomer must copy all four out of an agent's chat message.
3. The evidence view listed opaque labels and hashes with no instruction on
   what the operator should verify against their own workspace.
4. EOF/Ctrl-C during the confirmation prompt escaped as a raw traceback because
   the confirmation `read_line` sat outside the `try/except EOFError` guarding
   the menu loop.

## Changes (all in `src/knowledge_forge/approval_tui.py`)

1. **Confirmation phrase from the request SHA-256.** `_confirmation_token`
   derives the token from the first 8 hex characters of the canonical
   `request_sha256` (already computed by `approval_request_sha256` and shown in
   the evidence view). Neutral, stable, independent of agent-chosen request
   names. The phrase is bound to no state: receipts and the session ledger
   never store it, so the receipt contract is untouched.
2. **Bounded mismatch recovery.** A wrong confirmation is not recorded and
   shows the exact expected phrase; the operator returns to the menu and may
   retry up to 3 attempts per session, after which the same fail-closed
   `ApprovalContractError`/stderr-JSON/exit-2 path runs as before. The retry
   loop re-runs the full select-`A` ceremony, re-requires typed attention, and
   expiry is re-checked before any decision is recorded exactly as before.
   EOF/Ctrl-C during confirmation raises `terminal review cancelled`
   (regression fixed).
3. **Evidence-view guidance.** Fixed plain-language lines explain that labels
   are opaque by design, that the view contains hashes only, and that the
   operator should independently recompute each sha256 against the agent's
   artifacts in their own workspace before approving. Presentation text only.
4. **`--from-dir <directory>`.** Loads the three canonical files
   (`approval-request.json`, `approval-evidence.json`, `current-candidate.json`)
   from one directory through the existing bounded `_load_json` (symlink
   refusal, size cap, UTF-8 object checks). Mutually exclusive with the three
   explicit flags; all three are required when it is absent; validation fails
   closed before any file read. Filenames are fixed, so the operator never
   passes agent-chosen arbitrary paths beyond one directory.

## Trust analysis of the bounded retry

The TTY gate is an ergonomics gate, not an authenticated boundary
(`docs/GENERIC_APPROVAL_TUI.md`): an agent can simulate a TTY, and a recorded
review grants no authority. Retries therefore change the trust model in neither
direction: a scripted agent grinds prompts exactly as easily as before, and a
human gets the exact expected phrase instead of a dead end. Bounded 3 keeps the
unchanged failure terminal state (same exception class, same stderr JSON,
same exit code 2); unlimited retry would have made scripted grinding invisible.
A single-attempt variant was considered and rejected as strictly worse UX with
no security benefit.

## Guardrails kept

- `src/knowledge_forge/approval.py` unchanged (zero diff) — receipt contract,
  session ledger, projections, and the operator-authorization seam untouched.
- No token or phrase stored in any receipt or ledger.
- No new decision types, presentation surfaces, or schema versions.
- TTY gating on stdin and stdout unchanged; all fail-closed paths preserved.
- No `--approve`, batch, environment-based, or caller-clock approval surface.
- No imports of operator or promotion code, no subprocesses, no network.
- `typed_review.py` and its separate grammar are untouched.

## Follow-ups that remain future cuts

Authenticated operator approval (enrollment, challenges, protected one-time
decision storage, replay control, and the adapter to `OperatorAuthorization`)
per `docs/GENERIC_APPROVAL_TUI.md` "Approval versus execution" and
"Future hosted-service integration".