# Linux protected promotion service

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
  Docker, reviewer socket, human credentials, or operator-state access;
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

1. `kf-agent`, `kf-operator`, and every human reviewer have distinct non-root
   UIDs; only humans belong to `kf-reviewers`.
2. `/opt/toadaid-kf-operator` is a pinned copy owned by root, mode 0755, with no
   writable or symlink ancestors. Run Python isolated from user site packages.
3. `/var/lib/toadaid-kf-operator` is owned by `kf-operator`, mode 0700, with a
   valid initially empty registry and initialized authorization ledger.
4. `/etc/toadaid-kf/operator.json` matches the reviewed example, is root-owned,
   and is not group/other writable.
5. The two supplied systemd units are root-owned, reviewed, daemon-reloaded and
   socket-activated only after adversarial UID/ACL tests.
6. The root-owned human client is available to reviewers but not installed in
   the ordinary agent command prefix.
7. `kf-agent` cannot connect to the socket, read operator state/code internals,
   access human TTYs or credentials, invoke sudo/polkit, or write a container
   daemon socket. Test these properties as the actual launched agent process.

The administrator must copy the reviewed tree and adapt the launchers so they
use an absolute trusted Python interpreter with isolated mode (`-I`) and import
only from `/opt/toadaid-kf-operator/src`. Do not execute the checkout launchers
as proof of deployment.

## What remains platform-specific

The reference is Linux/systemd only. macOS authorization services and native
Windows service/ACL/token implementations require separate threat models and
must not reuse Linux peer-credential claims. Passing unit tests does not prove
that a particular machine has been safely provisioned or activated.
