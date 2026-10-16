# Generic agents: protected queue and desktop approval

This Linux reference adds a common promotion request path for terminal agents.
It reuses the protected operator UID, package validation, five-minute human
challenge, consume-once authorization ledger and governed promotion engine.
It does not require an agent-specific native verification API.

It is implemented code and synthetic regression coverage, **not a provisioned
or verified deployment on your computer**. Merging this change does not start
a service, modify agent settings, register an MCP, or promote existing packages.

## Separation of responsibilities

| Process | OS identity | Capabilities |
| --- | --- | --- |
| Any compatible agent/request transport | `kf-agent` | Submit an immutable VALIDATED bundle; read its own request status |
| Desktop reviewer broker | distinct human UID, member of `kf-reviewers` | List pending requests; display evidence; approve, reject or cancel |
| Protected service | `kf-operator` | Validate and snapshot requests; own queue/ledger/registry/receipts; perform exactly approved transitions |

The agent has no display access, reviewer socket, signing key, decision API,
or write access to protected state. The broker needs the human's graphical
session but holds no signing key or registry. Kernel `SO_PEERCRED` identifies
the submitter/reviewer; labels supplied by an agent are not authentication.

The desktop click is trustworthy only if model execution **cannot run as the
human UID**, control that desktop, invoke the reviewer broker as the human,
or replace its root-installed code. A daemon UID alone is insufficient. An
agent running as the same human account behind a permissive sandbox does not
establish this boundary. Do not carry over the same-account Claude card's
procedural independence claim as OS isolation.

## Agent path

After ordinary authoring and `validate-package`, a bundle contains `package/`
and `lifecycle.validated.json`. From the configured agent account:

```bash
bin/knowledge-forge-request submit /path/to/validated-bundle
bin/knowledge-forge-request status <returned-request-id>
```

The first call returns `PENDING`, an opaque request ID, exact bundle/package
digests, counts and expiry. It **does not open a window or promote**. The
already-running desktop reviewer polls for requests and presents them. Agent
status returns no approval token, reviewer challenge, raw source or operator
filesystem paths. Read status after the human decides; report success only
for `status=PROMOTED`, `promotion_performed=true` and a returned receipt digest.

The CLI works with agents able to invoke it from the configured agent UID.
For sandboxes that deny Unix socket access, the host must explicitly expose
only this submit/status operation through its own bounded transport. The
included `bin/knowledge-forge-approval-mcp --workspace-root /absolute/workspace`
is a stdio MCP wrapper for that role, running as `kf-agent`. It exposes only
`submit_knowledge_promotion` (bundle path restricted to that workspace) and
`knowledge_promotion_status` (opaque request ID). Its host-selected
`--operator-socket` defaults to the agent request socket. Register it explicitly
in a compatible MCP host after deployment and restart/reload that host; no
client configuration is changed here. Do not launch it as the human reviewer
UID or give it the reviewer's socket/desktop. Neither the CLI nor
a different daemon UID bypasses sandbox restrictions. No universal native
agent integration is installed or claimed by this PR.

Only the configured `agent_user` UID may submit. Multiple agent products may
share that dedicated UID; they share its queue visibility and are not isolated
from one another. Multiple mutually isolated agent UIDs require a separate
policy/instance design; arbitrary local UIDs are refused.

## Human desktop path

The root-installed reviewer launcher is:

```bash
/opt/toadaid-kf-operator/bin/knowledge-forge-review-daemon
# Or inspect at most one pending request:
/opt/toadaid-kf-operator/bin/knowledge-forge-review-daemon --once
```

The broker polls the human-only `operator.sock`, opens a plain evidence view,
then **Approve & Promote / Reject** and one additional confirmation. Closing
or cancelling the confirmation cannot promote. It sends a decision only over
the same authenticated reviewer connection which received the exact challenge.
Agents cannot retrieve or use that connection or challenge. There is no
`--approve` flag, chat approval, PTY simulation, or executable source content.

The operator revalidates the snapshot when review begins, binds the challenge
to the reviewer UID, bundle and current registry digest, and rechecks registry
truth and challenge expiry before consuming approval. The existing engine
writes the PROMOTED lifecycle and receipt. The broker does not call an
in-process completion API or operate the old same-account signing-key card.

Validation/claim counts do not prove independent semantic truth or whole-corpus
coverage. The popup preserves the engine's source-coverage warning. Review the
author's coverage/omission ledger and model-judgment disclosures separately;
this transport does not manufacture those facts.

## Administrator deployment after review and merge

First establish and adversarially test every OS boundary in
[LINUX_PROTECTED_PROMOTION.md](LINUX_PROTECTED_PROMOTION.md). Keep the pinned
operator **and reviewer code** root-owned and non-writable to both agents and
reviewers, with the service's `/opt/toadaid-kf-operator` isolated Python launcher.
Do not run the operator from a model-writable checkout. Confirm the actual
model-execution UID is `kf-agent`, the reviewer UID is different, and neither
agents nor the operator belong to `kf-reviewers`.

An administrator then reviews and installs these additional reference files:

- `knowledge-forge-agent-requests.socket` into the system unit directory; it
  activates the existing `knowledge-forge-operator@.service` as `kf-operator`.
  The new socket is `kf-operator:kf-agent` mode `0660` and exposes submit/status
  only to the configured agent UID. Calling a human operation as that UID is
  independently refused even if the agent can reach another socket.
- `knowledge-forge-runtime.conf` into the system tmpfiles directory. Ensure
  `/run/toadaid-knowledge-forge` is root-owned `0755` so both role groups can
  traverse it. Socket modes remain `0660`; do not make sockets world-accessible.
  Existing deployments with a `0750` parent need deliberate administrator
  adjustment. Review all three socket units before activating them.
- `knowledge-forge-reviewer.service` into the **human reviewer's user manager**
  only. Use that human's existing graphical session environment (`DISPLAY` or
  `WAYLAND_DISPLAY`, Xauthority and desktop bus). Zenity must be installed.
  Do not give the operator daemon a display or expose desktop credentials to
  `kf-agent`. Start/enable this user service only as an explicit human action.

The ordinary installer is unchanged and still installs only the consumer and
authoring commands. No automatic account creation, sudo invocation, service
enablement, migration of a personal registry, or trust-anchor replacement is
performed. The old same-account approval-host registry is not the protected
daemon registry; existing entries require a separately reviewed migration.

## Persistence, bounds and failures

The operator creates `approval-queue.sqlite3` under its `0700` state root with
mode `0600`. Requests are copied into the database; moving or modifying the
agent's original bundle after submission cannot change the reviewed snapshot.
The snapshot digest is checked again at review. Conditional SQLite transitions
allow only one reviewer to claim/decide a request.

- Queue expiry: one hour. Human challenge expiry: five minutes.
- Maximum serialized bundle: 8 MiB; pending/in-flight requests: 16; retained
  rows: 128; total stored bundle bytes: 128 MiB. The existing package, lifecycle
  and protocol limits also apply. Capacity exhaustion refuses new submissions.
- Exact duplicate active/promoted/uncertain bundles are refused. Rejected or
  cancelled requests may be resubmitted as new requests; their prior decisions
  and receipts are never overwritten.
- Reject/cancel/disconnect before approval changes no registry. Missing display
  or broker leaves requests pending until expiry. Expiry and status survive
  service restarts. A broker crash during evidence display leaves REVIEWING
  until expiry; another broker cannot silently take it over.
- Ambiguity after approval starts becomes `RECONCILIATION_REQUIRED` (or a
  surviving `PROMOTING` row with `reconciliation_required=true`). Never retry
  that request automatically: inspect protected registry and authorization
  receipts first. A registry write may have succeeded even if queue completion
  or delivery failed. A successfully persisted queue result remains queryable
  if the connection drops while its response is sent.
- No automatic deletion/compaction is included. At capacity, an administrator
  must review/archive terminal audit records while the socket services are
  stopped and preserve reconciliation evidence. Do not delete active requests
  or authorization state to force a retry.

## Verification and acceptance before real promotion

Run the complete regression suite and the synthetic tests in
`tests/test_generic_approval_queue.py`, `tests/test_generic_approval_reviewer.py`
and `tests/test_generic_approval_mcp.py`.
They cover queue persistence, snapshot binding, UID dispatch, duplicate guards,
rejection/cancellation, expiry, replay, storage caps, uncertain persistence,
and the unchanged promotion engine. Scripted UI tests prove code behavior,
not a real human or a correctly deployed machine.

After merge/sync and independent administrator deployment, test with synthetic
data under the **actual three UIDs**: successful popup/confirmation, rejection,
cancel, no display, wrong UID/socket, agent attempts to decide, changed registry,
expired/replayed requests, service restart and interrupted persistence. Confirm
the actual sandbox can reach only its designated request transport. Only then
resubmit the unchanged real VALIDATED packages and ask for human approval.

This path never adds to PERSONAL. Personal acceptance remains a separate,
explicit `knowledge-forge personal add --accept` operation.
