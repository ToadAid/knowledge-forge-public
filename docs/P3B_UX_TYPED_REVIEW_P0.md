# P3B-UX-TYPED-REVIEW-P0 — One-word review, zero promotion authority

**STATUS: REVIEW-ONLY USER EXPERIENCE; NO HUMAN AUTHENTICATION, PROTECTED HOST, OPERATOR INVOCATION, OR REGISTRY MUTATION.**

Ordinary users run Claude Code or Codex under their existing account. The goal
is a small, comprehensible typed-word experience. No separate account or GUI
is needed to **record a review opinion**. This does not solve authenticated
human approval. An agent sharing the same account can simulate terminal input.

The checkout-local, *not installed into the agent's command prefix*, command:

```bash
PYTHONPATH=src python3 -m knowledge_forge.typed_review \
  --request /existing/approval-request.json \
  --evidence /existing/approval-evidence.json \
  --current-binding /existing/candidate-binding.json \
  --receipt-out /existing/directory/new-review-receipt.json
```

requires stdin **and** stdout to be interactive terminals. The user types
exact uppercase `APPROVE`, `REJECT`, `DETAILS`, or `CANCEL`. `REJECT` asks
for a reason. `DETAILS` shows all bound review digests and evidence items.
`APPROVE` produces `REVIEWED_APPROVED`, *never* `PROMOTED`. Fuzzy expressions
like `go ahead` are deliberately not accepted. No username, title, clock, input
or claimed request field grants authority.

This module reuses `approval.py` contracts and `approval_tui.py` bounded JSON
file guards. It verifies canonical request/evidence/caller-observed candidate
bindings and expiry before presenting, then rechecks claimed candidate and time
at decision. The result is a regular untrusted P2B0 review receipt with all
authentication/authorization/promotion/operator flags `false`. Its session
ledger is an in-memory duplicate guard, not a cross-session durable protector.
The `receipt-out` path is create-new-only; receipt save errors are fail-closed.

A model may invoke this preview/review command, but **must not claim** that
an apparent `APPROVE` came from an independently authenticated human. The
actual protected operator must not be exposed to same-account Claude/Codex.

**Safety gates:** three files only; zero changes to `operator_cli.py`,
`operator_promotion.py`, existing installer, auth permissions, accounts or
registry. Tests include TTY enforcement, exact typed grammar, replay in one
session, refusal for expired/tampered review, interruption, nonmutation and
no authority. Every full regression and existing installer check must pass
on TNG before a PR; no commit, push, merge or protected activation in this P0.

**Later independent implementation:** the same one-account UX can invoke a
separately protected OS-controlled human verification flow; only such a verified
host may construct operator authorization after fresh real candidate+registry
read, durable atomic consume-once, and independent approval. Until that
boundary is proven, `VALIDATED != PROMOTED` and `REVIEWED != AUTHENTICATED`.
