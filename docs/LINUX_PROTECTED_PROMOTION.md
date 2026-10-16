# Linux protected promotion service

For agent-neutral submission and a popup broker running under a separate human
UID, see [Generic daemon approval](GENERIC_DAEMON_APPROVAL.md). Its optional
queue/socket and reviewer service reuse this protected operator boundary.

## Status

This directory contains a Linux-first reference implementation for the simple
human ceremony:

```text
knowledge-forge-promote /path/to/validated-bundle
  -> inspect exact skill/package/registry identity
  -> type APPROVE <12-hex challenge>
  -> PROMOTED or a fail-closed refusal
```

It is deliberately **not installed or activated by the ordinary agent
installer**. Source code in a model-writable checkout is not a trusted operator.
No live promotion is safe until an administrator independently establishes the
OS boundary below and verifies it with the actual agent and human accounts.

## Trust boundary

The reference deployment uses:

- `kf-agent`: the only account allowed to run model/agent processes; no sudo,
  Docker, human-reviewer socket, human credentials, or operator-state access;
- `kf-operator`: non-login service account owning mode-0700 state;
- `kf-reviewers`: human-only group allowed to connect to the systemd-owned Unix
  socket; `kf-agent` and `kf-operator` must not be members;
- root-owned, non-writable code at `/opt/toadaid-kf-operator` and root-owned
  configuration at `/etc/toadaid-kf/operator.json`;
- a systemd socket whose kernel `SO_PEERCRED` identity is bound to every
  challenge and promotion receipt.

The protected service refuses root, `kf-agent`, and `kf-operator` as review
peers. The socket ACL and peer credentials are the authentication boundary;
the typed phrase is an attention/intent ceremony, not a password. This is safe
only if the model really runs as `kf-agent` and cannot invoke commands as a
human reviewer, use sudo/polkit/container sockets, inherit reviewer file
descriptors, or modify the installed client/service.

An optional second socket supports the Codex native-proof flow documented in
[Codex native MCP promotion deployment](CODEX_NATIVE_MCP_DEPLOYMENT.md). It is
accessible to `kf-agent`, but its protocol accepts only a signed native
user-verification proof; it cannot run the typed human ceremony. The protected
native service checks its exact socket and peer UID before accepting proof operations; the existing human socket ACL remains unchanged.

## Candidate bundle

The one positional bundle directory contains:

```text
bundle/
  package/
    SKILL.md
    manifest.json
    SOURCE_RECEIPT.json
    claim-index.json
    retrieval-index.json
    units/*.md
  lifecycle.validated.json
```

The client snapshots regular non-symlink files into a bounded request. The
operator independently validates package hashes, lifecycle state and bindings,
then binds a random five-minute challenge to the kernel peer UID, exact bundle
digest, and current protected registry digest. The registry is re-read before
the one-use challenge is consumed. A changed registry, changed candidate,
expired challenge, wrong peer, replay, symlink, malformed package, or invalid
confirmation refuses.

An operator-owned file lock serializes registry verification, challenge
consumption, and persistence across socket-activated service processes. The
protected SQLite ledger transitions `ISSUED -> CONSUMING -> COMPLETE`.
Three incorrect confirmations become `DENIED`. Any failure after consume is
`RECONCILIATION_REQUIRED`; it is never blindly retried. Registry truth remains
authoritative if a crash occurs after its atomic replacement but before ledger
completion.

## Independent administrator installation

There is intentionally no agent-runnable installer. An administrator working
from a separate, agent-inaccessible console must review the exact commit and
the existing machine state before creating accounts, groups, directories,
socket policy, or services. At minimum, independently verify:

For account preparation only, the administrator may review
[`install.sh --create-protected-accounts`](INSTALL_SH.md). It creates locked
non-login users and an empty reviewer group in a separate explicit mode.
Agents must not execute it. It does not perform the remaining deployment steps
or establish any of the guarantees below.

For a full **administrator-run initial deployment**, see
[`install.sh --deploy-protected`](INSTALL_SH.md#deploy-accounts-and-the-protected-service-together-linuxsystemd).
It creates or verifies the accounts, installs pinned root-owned code and units,
initializes operator-owned state, adds the explicitly selected human reviewer,
and optionally enables the two generic system sockets. Agents must not execute
it. The human session, actual agent isolation and adversarial verification
below remain required; this command is not an upgrade or certification tool.

1. `kf-agent`, `kf-operator`, and every human reviewer have distinct non-root
   UIDs; only humans belong to `kf-reviewers`.
2. `/opt/toadaid-kf-operator` is a pinned copy owned by root, mode 0755, with no
   writable or symlink ancestors. Run Python isolated from user site packages.
3. `/var/lib/toadaid-kf-operator` is owned by `kf-operator`, mode 0700, with a
   valid initially empty registry and initialized authorization ledger.
4. `/etc/toadaid-kf/operator.json` matches the reviewed example, is root-owned,
   and is not group/other writable.
5. The supplied service and socket units are root-owned, reviewed,
   daemon-reloaded and socket-activated only after adversarial UID/ACL tests.
   The Codex migration installs dormant units only; native enablement remains
   blocked pending a separately reviewed supported UID transport and provider.
6. The root-owned human client is available to reviewers but not installed in
   the ordinary agent command prefix.
7. `kf-agent` cannot connect to the human-reviewer socket, read operator
   state/code internals, access human TTYs or credentials, invoke sudo/polkit,
   or write a container daemon socket. If the Codex socket is enabled, verify
   that it accepts only the native-proof protocol from the exact agent UID.
   Test these properties as the actual launched agent process.

The administrator must copy the reviewed tree and adapt the launchers so they
use an absolute trusted Python interpreter with isolated mode (`-I`) and import
only from `/opt/toadaid-kf-operator/src`. Do not execute the checkout launchers
as proof of deployment.

## What remains platform-specific

The reference is Linux/systemd only. macOS authorization services and native
Windows service/ACL/token implementations require separate threat models and
must not reuse Linux peer-credential claims. Passing unit tests does not prove
that a particular machine has been safely provisioned or activated.


## Adding the trusted Codex-native lane to an existing deployment

Follow the single [Codex native administrator workflow](CODEX_NATIVE_MCP_DEPLOYMENT.md).
`install.sh --migrate-codex-native --reviewer HUMAN --commit FULL_SHA --plan`
(or `--check`) inspects an existing protected baseline without changing it.
The separate independent-root `--apply` installs only absent pinned native
artifacts and leaves them dormant. It never resets state, updates existing
human units, changes agent settings, enrolls, approves or promotes a package.
Partial/unknown installations and incompatible engines refuse; preserve them
for independent review. Do not rerun the initial installer on valuable state.

The native socket uses its own matching `knowledge-forge-codex-operator@.service`,
not the human service template. `/opt/toadaid-kf-agent` is a new pinned snapshot;
ordinary installation does not create it. The read-only `--preflight` reports
blocking/unknown evidence and never certifies a human-UID Desktop session.
There is no safe supported Desktop cross-UID launcher in this repository;
`--enable-socket` refuses. Credential enrollment, verified actual `kf-agent`
transport, native provider availability, explicit future activation, MCP
registration and a new session all require separate human/admin steps. TUI or
generic popup success is not native verification. Governed promotion and
explicit PERSONAL acceptance remain separate.
