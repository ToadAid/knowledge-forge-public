# Changelog

## Unreleased — initial open-source source snapshot

- Added the approval-host contract (APPROVAL UX P1): chat can request skill
  promotion (`approval.build_approval_request`, unchanged review-only
  receipts), while a trusted host process converts one human Approve/Reject
  into a real PROMOTED registry transition — `build_host_decision` signs an
  HMAC-bound decision against the exact request bytes,
  `authorize_from_host_decision` verifies signature, expiry, request/candidate
  binding and a durable single-use ledger before constructing the existing
  `OperatorAuthorization`, and `bin/knowledge-forge-approve` performs the
  promotion through the unchanged operator engine with fail-closed refusals.
  The full chain (request → review → authenticated approval → promotion →
  registry verification) is covered end-to-end in `tests/test_approval_host.py`.
  No host key, no promotion; the exact missing dependency is reported rather
  than simulated.
- Added Unicode-aware deterministic retrieval matching (Personal Forge P1.1):
  questions route to knowledge units through four ranked tiers — exact alias
  phrase, compound form (a naturally spaced phrase like "Clair de Lune" now
  reaches a lore-native single-token alias such as `clairdelune`), a
  ≥5-character word prefix onto a longer alias token, and shared tokens —
  with accented words kept whole and CJK aliases containment-matching from
  three or more ideographs. The no-widening law is unchanged: a genuine miss
  still returns the typed NO MATCH refusal, bodies never participate in
  routing, and governed and personal consumption surfaces share one ranking
  helper (`question_rank_scores`). Covered by retrieval and end-to-end
  personal-library tests including refusal classes.
- Added Personal Forge P1: an ordinary-user-local personal knowledge library
  (`knowledge-forge personal add/list/lookup/remove`) that accepts only fully
  VALIDATED packages with exact receipt/identity binding, revalidates every
  package and stored lifecycle copy on every lookup, returns bounded
  source-grounded units stamped as untrusted local personal knowledge, and
  grants no tools, promotion or operator surface. Writers serialize through a
  portable advisory lock covering the whole read-modify-write (concurrent
  writers refuse instead of silently losing updates), and all package /
  lifecycle / library reads are size-bounded before reading. Governed Mode is
  unchanged and remains the only path to PROMOTED.
- Added a Linux/systemd protected-promotion reference: kernel Unix peer
  credentials, separate operator state, bounded candidate transport, exact
  package/registry binding, serialized registry mutation, five-minute one-use SQLite challenges, and a
  one-command human `promote` + typed `APPROVE` client. It remains absent from
  the ordinary agent installer and requires independent administrator
  provisioning before activation.
- Review TUI ergonomics P1: confirmation phrase derives from the request
  SHA-256 (with bounded mismatch recovery), `--from-dir` bundle loading,
  evidence-view operator guidance, and fail-closed EOF handling. Review-only;
  `approval.py` and the authority boundary are unchanged.
- Prepared the complete local-first Knowledge Forge engine for an Apache-2.0
  source distribution.
- Included generic consumer and authoring CLIs, checkout installer, schemas,
  deterministic tests, and safe fixtures.
- Included the P2B0 provider-neutral approval contract and review-only terminal
  interface with trusted-clock expiry checks.
- Preserved explicit local operator separation; no hosted service, account,
  remote approval, production activation, or release artifact is included.
- Replaced the former GPL-derived WETH9 test runtime with the independently
  authored, deterministic PondReference synthetic fixture and build recipe.

This entry describes a candidate snapshot. It is not a publication or release
announcement.

