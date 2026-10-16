# Codex native approval: administrator deployment contract

**The native lane is blocked, not production-ready.** Repository support can
install a dormant, pinned native lane beside an existing protected deployment.
It cannot safely launch a Codex Desktop stdio MCP as `kf-agent` from a human-UID
session. No supported cross-UID Desktop mechanism is supplied here. A reported
`providerUnavailable` is also a blocking failure, even when protocol negotiation
works. Successful human-terminal, generic-popup or Telegram approval proves
nothing about Codex native verification.

Personal Forge continues to work without accounts or services. Native approval
updates the governed registry only. It never calls `knowledge-forge personal
add`, grants PERSONAL acceptance, or treats setup consent as package approval.

## Authorities and installed artifacts

| Lane | Socket ACL | Authority / service |
| --- | --- | --- |
| Existing human reviewer | `operator.sock`, `kf-operator:kf-reviewers`, 0660 | Existing authenticated human ceremony and unchanged operator service |
| New native lane | `codex-operator.sock`, `kf-operator:kf-agent`, 0660 | Exact kernel agent UID, signed native proof, separate `knowledge-forge-codex-operator@.service` |

The native service checks its exact local socket name and `SO_PEERCRED` before
accepting `PREPARE_CODEX`. Typed/TUI, generic queue and fallback operations are
refused. A human native-protocol request to the existing reviewer service fails
the exact agent-UID check; the reviewer socket ACL excludes the agent group.
The existing multiprotocol implementation dispatches by operation: its socket
ACL is essential and must not be widened or bypassed by passing descriptors.

`Accept=yes` uses a **matching service template**, without a `Service=` override.
The new matching template is distinct from the human operator template. The
migration installs isolated `/usr/bin/python3 -I -B` launchers at:

- `/opt/toadaid-kf-agent/bin/knowledge-forge-codex-mcp` (agent transport);
- `/opt/toadaid-kf-agent/bin/knowledge-forge-codex-operator` (operator service).

Both load the reviewed snapshot under `/opt/toadaid-kf-agent/src`. The MCP
requires real and effective UID `kf-agent`; the native service requires
`kf-operator`. The operator reads the unchanged fixed configuration and state
root but explicitly validates this separate native code root. Shared installed
Python modules must match the reviewed snapshot byte for byte, except the two
client-only enrollment/MCP entry points. An incompatible governed engine needs
a separate reviewed upgrade, never silent substitution.

## 1. Establish the existing protected baseline

For a new host, independently review the initial deployment described in
[INSTALL_SH.md](INSTALL_SH.md) and [LINUX_PROTECTED_PROMOTION.md](LINUX_PROTECTED_PROMOTION.md).
The initial `--deploy-protected` command initializes state **only for a new
installation** and refuses existing targets. It neither installs this native
lane nor upgrades existing state. Never rerun it to repair an initialized host.

Legacy generic deployment in this revision still includes an invalid
`Accept=yes`/template `Service=` combination in the agent-request socket; the
initial installer also verifies a bare service template. These are separate
baseline defects, not repaired by adding the native lane. Independently repair
and review a functioning human baseline first. This migration neither copies
those defective units nor rewrites installed human units. A missing/unknown
baseline fails closed. Do not use native installation to recover a partial
initial deployment.

## 2. Review and plan the additive migration

An independent administrator must review a committed **full 40-character SHA**,
including `install.sh`, `scripts/migrate_codex_native.py`, the migration module,
native handler, transport, dependency snapshot and both native units. An
uncommitted patch is not an installable reviewed pin. No agent should execute
privileged deployment on the user's behalf.

From the reviewed checkout, the read-only interface is:

```bash
./install.sh --migrate-codex-native --reviewer HUMAN_LOGIN \
  --commit FULL_REVIEWED_40_CHARACTER_SHA --plan
# --check is an alias for --plan
```

The bootstrap compares itself, `install.sh` and the migration module with the
pin, consumes `git archive` bytes, and executes only the archived migration
module. An exact per-command Git safe-directory exception avoids modifying
Git settings. No live checkout Python is imported as administrator code.

The plan checks Linux/systemd, distinct non-root identities and groups, non-login service shells/homes, safe
root-owned ancestry, the installed public code manifest and hashes, fixed
configuration, existing human unit hashes, and state **metadata only**. Registry,
ledger, credential and promotion receipt contents are never read or hashed.
The state directory is operator-owned 0700; registry/ledger are 0600, and
receipt/lifecycle directories are 0700. Existing runtime ancestry must already
be root-owned 0755 for both socket groups to traverse; migration never widens it.
Inaccessible evidence refuses the plan; an administrator can run it from their
independent root console for metadata access. Plan success certifies no state
semantics, transport, provider or deployment readiness.

## 3. Administrator installs dormant artifacts

Only after independent review, the root administrator uses the **same pin**:

```text
./install.sh --migrate-codex-native --reviewer HUMAN_LOGIN \
  --commit FULL_REVIEWED_40_CHARACTER_SHA --apply
```

This requires Linux, root, an interactive independent console and the exact
confirmation `INSTALL DORMANT CODEX LANE`. It creates only the previously absent
native snapshot and its two system unit files, verifies the new units and
reloads systemd. It does not create accounts, start/enable/restart services,
change configuration or user settings, initialize/migrate state, enroll, submit,
approve or promote anything.

The bounded public receipt is
`/opt/toadaid-kf-agent/NATIVE_MIGRATION.json` (root-owned 0644, at most 64 KiB).
It records the pin, public artifact hashes, role UIDs/GIDs, actions and explicit
no-state/no-promotion flags. It contains no credential, protected-state hash,
secret or promotion receipt. Exact completed, still-dormant repeats perform inspection only; unexpected
activation refuses.
Changed pins/hashes, unknown native files/units, or a partial installation refuse.
A partial failure leaves a marker and preserves all prior state; there is no
automatic rollback, overwrite, reinitialization or blind resume. Independent
recovery is required before any further migration.

`--enable-socket` is deliberately **refused**, before writes. This release
cannot establish the transport/provider prerequisites, so has no activation
path. Do not bypass this by manually enabling the socket. A future separately
reviewed integration must establish those prerequisites and provide explicit
activation; dormant installation alone is not native approval deployment.

## 4. Native credential enrollment is a separate human ceremony

Do not enroll while the provider is unavailable. In an authenticated human
Codex session, the enrollment helper opts into the experimental app-server API
with `capabilities.experimentalApi=true`. Local protocol contracts come from:

```bash
codex app-server generate-json-schema --experimental --out /PUBLIC/SCHEMA_DIRECTORY
```

Schema support does not prove provider, account or device availability. The
optional `bin/knowledge-forge-codex-enroll --status-only` calls only status and
returns nonzero for `providerUnavailable`, missing credential or unavailable
biometrics. It never creates an enrollment or displays an approval control.
Enrollment itself is an explicit independent human action:

```text
bin/knowledge-forge-codex-enroll --credential-registry /PRIVATE/NEW/public-metadata.json \
  --principal codex:user:HUMAN_LOGIN
```

The output is public credential ID, ES256 public key, principal and timestamp,
create-new mode 0600. Nullable/incomplete metadata is refused. Symlink paths,
existing outputs and protected destinations are refused before contacting the
provider. The unprivileged helper never installs or overwrites the protected
registry. The independent administrator reviews public metadata and separately
installs it **create-new**, operator-owned 0600, at
`/var/lib/toadaid-kf-operator/codex-credentials.json`. Existing credentials require
separate review/rotation; do not overwrite them. Do not copy private keys or
read existing protected credentials into an agent session.

## 5. Verified transport, registration and a new session

The process that connects to the native socket must actually have kernel peer
UID `kf-agent`. A configured command path, matching account name, stdio proxy
or a human Codex process does not establish that identity. This repository has
no supported safe Desktop launch mechanism for crossing this UID boundary.
Do not add the human to `kf-agent`, grant passwordless sudo, add setuid helpers,
transfer reviewer descriptors, relax ACLs or weaken the peer-UID check.

Only after a separately reviewed supported mechanism exists should the human
explicitly register the exact MCP command and arguments in the client that
will launch it under the verified agent identity:

```text
codex mcp add knowledge_forge_approval -- \
  /opt/toadaid-kf-agent/bin/knowledge-forge-codex-mcp \
  --workspace-root /srv/toadaid-kf-workspace \
  --operator-socket /run/toadaid-knowledge-forge/codex-operator.sock
```

This is a future integration contract, **not a working human-UID Desktop
shortcut**. Registration changes Codex configuration and requires explicit
human action, followed by a **new Codex session**. Never register a checkout
launcher or another socket as fallback.

## 6. Read-only preflight and verification matrix

```text
./install.sh --migrate-codex-native --reviewer HUMAN_LOGIN \
  --commit FULL_REVIEWED_40_CHARACTER_SHA --preflight \
  --workspace-root /srv/toadaid-kf-workspace \
  --schema-dir /PUBLIC/SCHEMA_DIRECTORY \
  --mcp-config-json /PUBLIC/SANITIZED_MCP_EXPORT.json \
  --provider-status-json /PUBLIC/STATUS_RESULT.json
```

JSON reports PASS/FAIL/UNKNOWN per gate and exits 2 unless READY. It verifies
pinned launcher/unit hashes, operator service identity, expected UIDs, exact
socket ACL, current preflight process UID (never inferred as the live transport
UID), completed migration identity,
credential presence/owner/mode **without contents**, schema support, and
proof-only/no-fallback artifacts. Expected MCP command/arguments are emitted;
`SANITIZED_MCP_EXPORT.json` has `mcp_servers.knowledge_forge_approval` containing
exactly `command` and `args`. `STATUS_RESULT.json` is the public raw result of
`userVerification/status`, never a credential registry or proof. Exports are
supplied deliberately; preflight does not read Codex user configuration or
contact an app-server, socket or native control. `providerUnavailable` remains
an explicit failing gate. Missing or inaccessible evidence stays blocking.

An exact export match is evidence of argument shape only. Fresh live MCP
registration, device verification, supported cross-UID launch and live
expiry/replay rejection remain UNKNOWN. Thus this release **never reports
READY**, even if all offline evidence matches. The actual MCP peer UID and running operator UID remain UNKNOWN until independently
observed; the current preflight process UID is reported separately.
Administrator and actual transport observations are separately required.

After separate review, deployment, safe transport and provider integration,
verify with synthetic packages before any real approval:

| Case | Required result |
| --- | --- |
| Native accept with valid enrolled proof | Exactly one governed transition and receipt |
| Decline / cancel / unavailable provider / missing credential | No transition; no fallback |
| Expired approval / repeat proof / restart after consumption | Refuse; never reuse authority |
| Wrong peer UID / wrong socket / TUI text on native socket | Refuse before authority |
| Wrong credential / malformed proof / invalid signature | Refuse |
| Changed package / registry / request binding | Refuse |
| Interrupted migration / changed pin or artifact | Refuse; preserve existing state |

Offline tests use only temporary synthetic state. They prove neither real
Desktop availability nor host isolation. Only after real accept/reject/replay
and failure testing may an independently requested real VALIDATED package
enter native approval. PERSONAL acceptance still requires its own explicit
consent and `knowledge-forge personal add`.
