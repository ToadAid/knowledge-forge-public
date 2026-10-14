# P3B-P2B2A — TNG human-controlled operator provisioning review

**Status: HUMAN REVIEW PLAN ONLY — NOT PROVISIONED, AUTHENTICATED OR ACTIVATED.**

The merged P3B-P2B1 observed that the existing `tommy` account retains `sudo`
and Docker privileges. The agent must NOT run under that account when a real
human/operator boundary is introduced. P2B2A defines a fixed, inspectable TNG
account/layout plan and reports the real host's observed state. It does not
create accounts, change permissions, run privileged commands, install code,
launch an operator, create credentials, or promote knowledge.

## Fixed proposed identities and directories

| Identity / path | Owner | Mode / shell | Authority |
| --- | --- | --- | --- |
| `kf-agent` | separate non-root local user | `/usr/sbin/nologin`; no privileged groups | Ordinary Forge/Claude process only |
| `/var/lib/toadaid-kf-agent` | `kf-agent` | `0700` | Agent private home/workspace; no operator credentials |
| `kf-operator` | separate non-root local user | `/usr/sbin/nologin`; no privileged groups | Future human-controlled operator service ONLY |
| `/opt/toadaid-kf-operator` | `root` | `0755` | Reviewed, pinned code; NEVER deploy from agent-writable checkout |
| `/var/lib/toadaid-kf-operator` | `kf-operator` | `0700` | Operator private state; no agent access |

The operator UID must be distinct from `kf-agent` and `tommy`. Account creation
must not grant `sudo`, Docker, extra groups, login passwords, `sudoers` rules,
or model-accessible sockets. `kf-agent` must not inherit `tommy`'s SSH/GitHub,
cloud, Docker, or other privileged credentials. Linux UID separation alone
is not protection if the agent has a tool that runs commands as `tommy`, sudo,
Docker, or another privileged principal. Explicitly verify the *real launched*
process's UIDs, groups, capabilities, inherited file descriptors, socket
access, confinement, and callable tools.

## Independent administrator ceremony — NOT executed by this cut

The eventual provisioning work belongs in a **separately authenticated,
agent-inaccessible administrative TTY**. Do not simply prefix a model-created
shell script with `sudo` or execute it via Claude. Before issuing any root
command, a human must verify the machine, the actual host accounts, sudoers,
polkit and Docker ACLs, existing directories and their ancestors, and a pinned
trusted code revision. Unexpected or partial existing state requires manual
reconciliation; never delete, overwrite, chown recursively, or retry blindly.

**Proposed command sequence for independent review only** (NOT in the runnable
P2B2A script, NOT approved for execution yet):

```text
# After independent root/admin authentication and exact account-state review:
/usr/sbin/useradd --system --user-group --no-create-home --home-dir /var/lib/toadaid-kf-agent --shell /usr/sbin/nologin kf-agent
/usr/sbin/useradd --system --user-group --no-create-home --home-dir /var/lib/toadaid-kf-operator --shell /usr/sbin/nologin kf-operator
/usr/bin/install -d -o kf-agent -g kf-agent -m 0700 /var/lib/toadaid-kf-agent
/usr/bin/install -d -o root -g root -m 0755 /opt/toadaid-kf-operator
/usr/bin/install -d -o kf-operator -g kf-operator -m 0700 /var/lib/toadaid-kf-operator
```

These lines are **design examples, not verified TNG-safe instructions**. Account
creation is a durable privileged change requiring separate user approval.
Existing account names/paths, symlinks, compromised parent directories, or
unexpected sudoers/polkit policy can make this sequence unsafe. Validate the
local `useradd` and `install` paths and flags in the trusted root session.
Never let a generic agent run or approve these commands.

### Human-owned proof gates after independently approved provisioning

1. Independently verify `getent passwd`, `id`, real UID/GID and group memberships
   of BOTH new accounts. No `sudo`, Docker or other privileged group.
2. Verify exact ownership/modes and symlink-free ancestors. Operator code must
   come from a reviewed, exact commit and be installed by root into root-owned
   `/opt` — not imported from `/home/tommy` or an agent-writable tree.
3. Run controlled adversarial tests **as the actual `kf-agent` UID**: it must be
   unable to read, traverse, mutate, or invoke operator private state, code
   execution or credentials; inability to run `sudo` must be checked via
   effective policy, not just group membership.
4. Check whether the agent can reach privileged sockets, file descriptors,
   local services, polkit, container endpoints, `tommy`'s TTY/session or
   credential agents. Block the bypasses before running an LLM.
5. Separately verify the operator process EUID, trusted code/mount identity,
   protected state, auditable human authentication and fixed output paths.
6. Only later build P3B-P2C one-use, short-lived host-issued decisions and
   P3B-P3 protected promotion/registry reconciliation. No operator is active
   merely because the accounts exist.

## What the new code does (and does NOT do)

`src/knowledge_forge/provision_review.py` provides
`inspect_tng_provisioning_plan()`: a read-only TNG local-account and filesystem
observation tied to fixed names, expected homes, shells, groups, owner/mode,
and symlink-safe ancestry checks. Its digest is of a **public proposed policy**;
it is NOT a signature, root attestation, token, or approval. Every result says
`HUMAN_REVIEW_REQUIRED_NO_PROVISIONING_PERFORMED` and all authority flags are
false. The ordinary `tommy` session can modify this Python source or report;
therefore a real root-owned service must independently re-read host truth.

The active GitHub `main` ruleset also needs an administrator to require the
`Knowledge Forge CI / required` check from GitHub Actions. A green workflow
alone does not enforce that rule. P2B2A makes no GitHub ruleset changes.
