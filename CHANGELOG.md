# Changelog

## Unreleased — initial open-source source snapshot

- Make the agent ask before anything lands after validation (docs only, no
  runtime change): after showing VALIDATED results the agent asks
  **library, governed promotion, or both** — a chat yes to the library
  runs `personal add --accept`, a chat yes to promotion is only the
  *request* that stages the approval card (the two card clicks stay the
  actual signature), and silence adds nothing. Stated in
  `docs/FILE_TO_SKILL_AGENT_GUIDE.md` (new "The ask" section, routing
  reply template).

- Added a Codex stdio MCP promotion transport using native
  `openai/userVerification`, with P-256 proof verification and completion kept
  inside the Linux protected operator boundary. A separate proof-only systemd
  socket admits only the configured agent UID; the existing human TTY socket
  remains agent-inaccessible. Added one-time Codex reviewer credential
  enrollment with strict create-new 0600 registry handling.

- Make existing Personal Forge the default newcomer workflow:
  Forge → Accept → Ask. Align user manual, install guides, agent runbook,
  and advanced-doc entry points without changing any runtime policy.
- Add `docs/FEATURE_STATUS.md` distinguishing implemented/tested
  capabilities from agent-specific integration and deployment gaps,
  including the merged PR #50 Codex native bridge (code present; live
  native UI and protected deployment not established).
- Added the Claude-side on-screen approval card (`bin/approve-card`,
  `python3 -m knowledge_forge.approve_card`; docs/APPROVE_CARD.md): the
  ordinary user clicks **Approve & Promote** or **Reject** in a real desktop
  window — no commands, no paths, no typing — and, on Approve, one further
  confirm click executes the promotion (cancel or close refuses
  fail-closed). The card is the approval host: it prepares and
  completes the decision through the generic native-host adapter contract
  (`adapter_id="claude"`), signing with a key the agent channel cannot use,
  driving the unchanged engine to a real PROMOTED transition through the
  single-use ledger with immutable per-request receipt/lifecycle outputs.
  The agent stages and may pop the card; chat text never authorizes. The
  same-account procedural-independence caveat is printed on every card.

- Added the command-free inline approval adapter for trusted hosts. Agents can
  prepare a bounded review card, but ordinary chat and agent-created TTYs
  cannot authorize or promote. A native host Approve event produces the
  signed decision consumed by the existing single-use approval host. Removed
  the duplicate same-UID `bin/approve` ceremony, moved decisions out of
  agent-staged directories, added bounded no-follow reads, serialized host
  promotions, and made lifecycle/receipt output paths immutable per request.

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
  than simulated. Fixed the module entrypoint so
  `python3 -m knowledge_forge.approval_host_cli` (what
  `bin/knowledge-forge-approve` executes) actually runs instead of importing
  silently and exiting 0 without doing anything; regression test invokes the
  module as a script.
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

