# P3B-P2B1 — ordinary-agent privilege exclusion and protected-host provisioning plan

Status: **diagnostic code and refusal tests only.** This cut creates no Linux
accounts, installs no operator, authenticates no human, issues no one-time
decision, performs no registry promotion, and does not enable a host adapter.

## TNG facts and implications

P2A's read-only TNG report observed ordinary user `tommy` in the `sudo` and
`docker` groups, plus additional administrative groups. The `kf-operator`
account, protected code directory and protected state directory were absent.
An agent running under this account is **not** independent from a privileged
operator. Docker access is commonly root-equivalent. `bwrap`, `setpriv`, and
`runuser` being installed does not remove this danger from a process with a
privileged host UID. An agent-accessible terminal, review TUI or sudo prompt
is not independent human authentication.

## Minimum protected runtime design (requires separately authorized provisioning)

- Run Claude/GLM as a **new, non-root `kf-agent` OS user**, not under the
  existing privileged `tommy` user. Explicitly forbid `sudo`, `wheel`, `docker`,
  `podman`, `lxd`, `libvirt`, `disk`, `adm`, `shadow`, `systemd-journal`,
  effective Linux capabilities, inherited privileged environment credentials,
  and access to Docker/Podman control sockets or the operator state tree.
- Keep the human's existing `tommy` session outside the agent's accessible
  filesystem, TTY, tools, input automation, processes, containers, and sudo
  delegation. Human authentication must be independently controlled, not a
  string passed into a model tool or a model-readable challenge.
- Run a distinct `kf-operator` non-root service under **root-owned reviewed
  code** (e.g. `/opt/toadaid-kf-operator`) and operator-owned mode `0700`
  private state (e.g. `/var/lib/toadaid-kf-operator`). No agent access to
  either execution controls or secrets; no agent-callable privileged socket.
- Use explicit, separately approved, fixed human-run provisioning from a
  trusted physical/host admin session. Do not perform root account creation,
  `sudoers` edits, systemd service activation, ACL/ownership changes, or token
  writes from inside Claude's ordinary agent runtime or this cut.
- Before considering operator activation, independently test *real* UID,
  process groups, Linux capabilities, effective sudo/polkit rules, Docker
  daemon and socket ACLs, user namespaces/mounts, host file visibility, PAM
  session control, exact code integrity, path-race defenses and state secrecy.
  Self-inspection of agent-modifiable Python is **not attestation**.

## What P3B-P2B1 implements

`agent_runtime_gate.inspect_ordinary_agent_runtime` is a read-only **self-
observation** requiring both users to exist under different non-root UIDs; the
currently executing process to be the designated agent; no privileged groups,
nonzero `CapEff`, selected host-capable environment variables, Docker socket
write access, or agent access to the separately owned private operator state.
It returns explicit zero-authority flags even when all checks pass. The agent
can tamper with its own code or report, and these observations cannot authorize
human promotion. The host must independently establish the runtime boundary.

This module provides a conservative minimum baseline, NOT an exhaustive list
of all ways a local process can acquire privilege. For example, supplementary
group checks and empty capability sets do not independently establish the
absence of privileged `sudoers` policies, permitted binaries, polkit actions,
exposed host sockets, Docker-over-TCP, or host injection via agent tools.

## Next bounded cuts

**P2B2:** Human-controlled account/layout provisioning with exact, reviewed
commands, isolated code and state, fail-closed no-model-sudo delegation, and
external proof from both Linux UIDs. Provisioning requires a separate explicit
human approval. No credentials or sudo tokens in a Claude-accessible file.

**P2C:** Independent human authentication, exact trusted snapshot/registry head
binding, timeout, random challenge and protected atomic consume-once decision
ledger, cancellation, refusal receipts and crash/retry reconciliation.

**P3:** Privileged promotion + two-UID adversarial verification, and only then
an empty-directory, new-generic-user Claude+GLM newcomer test.

**Repository governance:** GitHub `main` currently has non-fast-forward and
branch deletion protection, but does not yet require the green aggregate check
`Knowledge Forge CI / required`. Enforce it separately via authenticated
repository administration before the next security-sensitive merge.
