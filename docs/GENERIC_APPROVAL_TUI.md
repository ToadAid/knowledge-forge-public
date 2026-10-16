# Generic approval contract and local review TUI

## Status and boundary

P2B0 adds a provider-neutral approval-request contract and a local terminal
review surface. This cut is deliberately **review-only** because Knowledge
Forge does not yet have an independently authenticated, agent-inaccessible
host/operator state store for generic terminals.

An `APPROVE` review result means only that the local review ceremony recorded
that choice. Every P2B0 receipt says:

```text
host_authentication_verified = false
authorization_granted = false
promotion_authorized = false
execution_authority = NONE
tool_grants = []
operator_cli_invoked = false
production_registry_mutated = false
```

The receipt is not `OperatorAuthorization`, is rejected by the conversion seam,
and is not consumed by `operator_promotion.py`. The privileged
`knowledge-forge-operator` remains outside the ordinary generic-agent PATH.

The standing laws remain:

```text
CANON != SKILL
KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY
validated != promoted
reviewed != authenticated != authorized != executed
```

## Implementation

- `src/knowledge_forge/approval.py` owns strict contracts, canonical hashes,
  evidence/current-candidate binding, non-authorizing review receipts,
  duplicate guards, reconciliation classification, and presentation
  projections.
- `src/knowledge_forge/approval_tui.py` owns TTY interaction and bounded JSON
  file handling. It imports no promotion implementation.
- `bin/knowledge-forge-review` is a checkout-local launcher. The generic-agent
  installer does not install it or the operator CLI into the agent prefix.
- `tests/test_approval.py` and `tests/test_approval_tui.py` use synthetic data
  only.

No Mirror Desktop Bridge code is copied and Knowledge Forge has no Bridge
dependency. The design was informed by Bridge PRs 227, 231, and 250: one
canonical contract below multiple surfaces; exact evidence binding; closed
schemas; re-read before a decision; explicit typed confirmation; bounded
reasons; truthful queue/receipt state; and reconciliation instead of claiming a
safe rollback when state is uncertain.

## Approval request schema

Schema version `0.1` contains exactly:

```text
schema_version
request_id
request_type = SKILL_PROMOTION_REVIEW
requesting_agent
requesting_principal
target_skill_id
target_skill_version
package_receipt_sha256
validated_lifecycle_head_sha256
requested_at
expires_at
evidence_digest
requested_action = PROMOTE_VALIDATED_SKILL
authority_boundary = EXTERNAL_AUTHENTICATED_HUMAN_HOST_ONLY
```

Unknown fields, unsupported versions/types/actions, unsafe identifiers,
non-SHA-256 bindings, non-timezone timestamps, and nonpositive versions fail
closed. `expires_at` must be later than `requested_at`.

`requesting_agent` and `requesting_principal` are bound labels, not proof of
identity. Altering either changes the canonical request SHA-256 and invalidates
an existing receipt. A model-provided principal string never authenticates a
human.

## Exact evidence and current-candidate binding

The evidence contract carries the request/skill/version/package/lifecycle
identity, exact `VALIDATED` state, and one to sixteen sorted unique evidence
items. Each item is only an opaque safe label and a SHA-256 reference. Raw
source bodies, local paths, secrets, validation output, and private engine code
are not presentation data.

`evidence_digest` is the canonical SHA-256 of the evidence body and is copied
into the request. Review requires equality across:

- request ID;
- skill ID and version;
- package receipt SHA-256;
- validated lifecycle head SHA-256; and
- evidence digest.

A separate current-candidate observation repeats those bindings and requires
`lifecycle_state=VALIDATED`. Any mismatch is a stale-candidate refusal before a
decision is recorded. This observation is evidence only in P2B0; it is not
protected host truth and therefore cannot authorize promotion.

## Decisions and review states

The contract supports these decisions and deterministic resulting states:

| Decision | Review state |
| --- | --- |
| `APPROVE` | `REVIEWED_APPROVED` |
| `REJECT` | `REVIEWED_REJECTED` |
| `REQUEST_CHANGES` | `CHANGES_REQUESTED` |
| `EXPIRE` | `EXPIRED` |
| `CANCEL` | `CANCELLED` |

`PENDING` is the initial unexpired state. `RECONCILIATION_REQUIRED` is the
fail-closed classification for multiple, malformed, or request-mismatched
receipts. It is never converted into approval.

Rejection and change requests require bounded nonempty reasons. Expired
requests cannot be approved, rejected, changed, or cancelled; only an `EXPIRE`
review record may be produced at or after expiry. A session ledger accepts one
decision per exact request SHA-256 and rejects duplicate/replayed recording.

The session ledger and create-new receipt file are not protected durable
operator state. Cross-process replay-resistant authorization remains blocked
until a separately reviewed host can keep one-time decision state inaccessible
to the requesting agent. This limitation is safe because P2B0 receipts grant no
authority at all.

## Receipt integrity

A receipt binds the request SHA-256, evidence digest, decision, result state,
review time, review surface, reviewer display identity, and optional reason.
Its `receipt_id` is the canonical SHA-256 of all other receipt fields. Receipt
validation uses an exact field set and recomputes that digest.

The only P2B0 reviewer identity kind is
`UNAUTHENTICATED_LOCAL_REVIEWER` from `LOCAL_TTY_REVIEW_ONLY`. Attempts to set
host authentication true, claim a different identity kind/source, or use the
requesting agent/principal as the reviewer fail closed. These checks prevent a
model from relabeling review output as authenticated, but they do not claim to
identify the person at the keyboard.

## P3A: read-only preview of a real VALIDATED package

The preview below validates an exact package snapshot and VALIDATED lifecycle,
checks their bindings, and shows bounded identity, claim coverage, and hashed
evidence before review. It neither creates an approval request nor records a
decision. The review contract projection in JSON output is an in-memory demo,
**not** current authenticated host truth or a live approval request.

```bash
bin/knowledge-forge-review preview-validated --package-root /path/to/packages/package --validated-lifecycle /path/to/packages/lifecycle.validated.json
```

Add `--format json` to inspect the bounded review-contract projection.
No operator, registry, approval receipt, or host activation is touched.
The preview reports only the units and claims included in this package. It
cannot know whether those are every entry in the original source corpus.
It must not describe a partial source selection as a fully forged archive.
`VALIDATED != PROMOTED`, and previewing a package does not establish semantic
truth, independent authentication, or entitlement to promote.

## Terminal interaction

Run the synthetic review demonstration from a local or SSH terminal:

```bash
bin/knowledge-forge-review synthetic \
  --receipt-out /an/existing/real/directory/review-receipt.json
```

Review supplied bounded JSON, either with three explicit input paths:

```bash
bin/knowledge-forge-review review \
  --request approval-request.json \
  --evidence approval-evidence.json \
  --current-binding current-candidate.json \
  --receipt-out review-receipt.json
```

or by pointing `--from-dir` at a bundle directory holding the three canonical
filenames (`approval-request.json`, `approval-evidence.json`,
`current-candidate.json`):

```bash
bin/knowledge-forge-review review \
  --from-dir /path/to/approval-bundle \
  --receipt-out review-receipt.json
```

The three canonical filenames are fixed; `--from-dir` cannot be combined with
the three explicit flags, and every input still passes the same bounded-JSON
checks (real non-symlink files, size cap, UTF-8 JSON objects).

The command requires both stdin and stdout to be TTYs. Piped input, redirected
output, EOF, Ctrl-C, wrong confirmation, stale evidence, expiry, malformed JSON,
oversized files, symlinks, unknown fields, and an existing receipt target fail
closed. There is no `--approve`, environment approval, or batch-decision flag.

The interface supports:

- `V`: display exact bounded digest evidence;
- `A`: type `APPROVE <first 8 hex characters of the request SHA-256>` to record
  approval review. The confirmation phrase is an attention aid, not a secret
  and not proof of authenticity. A mismatch is not recorded and shows the exact
  expected phrase; the operator can retry (`A`) or exit (`Q`) up to three
  attempts, after which the review fails closed with exit code 2. EOF or Ctrl-C
  during the confirmation prompt cancels the review without recording it;
- `R`: record rejection with a required reason;
- `C`: request changes with a required reason; and
- `Q`: exit with the request still pending.

The screen always displays the skill identity/version, package digest, state,
expiry, evidence availability, and the review-only warning. Successful output
explicitly repeats that host authentication, authorization, promotion, and
operator invocation are false.

An interactive TTY is an ergonomics gate only. An agent can simulate a TTY or
keyboard input. Consequently, a scripted `APPROVE` can produce only a
non-authorizing review receipt and can never produce operator authorization or
invoke promotion. The review surface remains unauthenticated, and receipts
grant no authority.

## Multi-surface compatibility

`project_approval_request` deterministically projects the same canonical
request/evidence into:

- `TERMINAL_TUI`;
- `TELEGRAM_CARD`;
- `DESKTOP_CARD`; and
- `WEB_TEMPLE`.

Generate all four synthetic projections without opening the TUI:

```bash
bin/knowledge-forge-review synthetic-projections
```

Every projection carries the same request hash and evidence bindings and says
`transport_authority=NONE`, `authorization_granted=false`, and
`promotion_authorized=false`. The surface name is the only presentation
identity difference. Telegram networking, callbacks, polling, webhooks,
desktop UI, and web endpoints are not implemented.

A notification, displayed card, callback value, or terminal keypress is not
authorization. Future remote approval requires separately reviewed operator
enrollment, authenticated inbound transport, exact user/channel binding,
short-lived challenges, expiry, one-time use, anti-replay, rate limiting,
revocation, protected audit state, and local emergency disable. High-risk
actions may additionally require a protected local confirmation.

## Approval versus execution

P2B0 stops before both authorization and execution:

```text
agent prepares VALIDATED candidate
  -> agent requests review
  -> TUI records non-authorizing review intent
  -> STOP
```

There is no edge from `approval.py` or `approval_tui.py` to
`operator_cli.py`, `promote_validated_skill`, registry persistence, subprocess,
shell execution, network access, or production state. A future authenticated
host integration must independently revalidate the exact package, lifecycle,
evidence, decision one-time state, authenticated operator, expiry, and current
policy before it may construct `OperatorAuthorization`. That future integration
requires its own threat review and tests.

## Failure, cancellation, expiry, and reconciliation

- Validation failures make the request unavailable for review.
- Changed package/lifecycle/evidence bindings produce stale-candidate refusal.
- Expired requests display a warning and cannot enter the approval prompt.
- `CANCEL` and `EXPIRE` are contract decisions; the local human-review menu
  intentionally exposes only approve/reject/request-changes/exit.
- Duplicate recording in one review ledger is refused.
- Multiple or invalid receipts classify as `RECONCILIATION_REQUIRED`; callers
  must not choose a winner or treat either as authorization.
- Receipt output uses create-new mode `0600`, refuses symlinks/existing files,
  and fsyncs the file. It remains operator-visible review evidence, not a
  protected authorization store.

## Future hosted-service integration

A hosted service may reuse the canonical request/evidence/projection shapes,
but transport must not determine authority. Before hosted authorization is
possible, a separate cut must add authenticated tenant/operator identity,
protected one-time decision storage, challenge and replay controls, retention
and deletion policy, audit/reconciliation behavior, rate limits, and an
explicit adapter from authenticated decisions to the existing operator
boundary. No hosted service, endpoint, token, or production activation is part
of P2B0.
