# Optional install.sh entry point

This wrapper keeps ordinary installation separate from Linux administrator
account preparation and explicit protected service deployment. Deployment
installs the Linux reference; it does not certify isolation or host integration.

From the checkout, as your normal user:

```bash
./install.sh --check-only
./install.sh
```

The default delegates to bin/knowledge-forge-install-agent and then runs the
read-only setup helper. Existing different shims/manifests are preserved by the
original installer. For an isolated installation, choose private persistent paths:

```bash
./install.sh --prefix "$HOME/forge-tools" \
  --state-root "$HOME/forge-state"
```

Personal Forge needs no extra accounts, services or promotion. Author and
validate first, obtain explicit package acceptance, then use personal add and
personal lookup. Installation does not automatically add any knowledge.

For optional protected account preparation, preview without making changes:

```bash
./install.sh --create-protected-accounts --plan
```

An independent administrator reviews this script and the pinned source, then
runs the following from a separate, agent-inaccessible console:

```bash
sudo ./install.sh --create-protected-accounts
```

Any sudo authentication belongs in that console. The script collects no
passwords and creates locked non-login accounts. It creates three system groups
and two distinct system users; kf-reviewers starts empty. Existing conflicting
names cause refusal, including after partial failure. An administrator must
inspect partial state manually before retrying. No automatic rollback occurs.

Both accounts have a locked password (`!`), a `nologin` shell, no newly created
home directory, and an explicitly empty supplementary-group list. Normal
password login and interactive shell login are disabled. This does not prevent
root or a service manager from running processes as those UIDs; that is how
the protected service and agent transport can use them. Do not add SSH keys or
enable interactive authentication for these service roles. Account UIDs/GIDs
are assigned by the local system and are not fixed values.

If the script reports `Accounts created` followed by `PROTECTED SETUP
INCOMPLETE`, account preparation succeeded. Re-run
`bin/knowledge-forge-setup --check-only`: account blockers should be gone, while
missing protected code/state/configuration/sockets may remain. Do not rerun
account creation to fix service blockers. The helper is diagnostic and never
certifies a protected deployment.

Account creation alone does not deliver protected approval: independently
install pinned root-owned code, configure operator-owned state and socket ACLs,
select a distinct human reviewer, arrange actual agent execution as kf-agent,
and verify the desktop broker and sandbox transport. Follow AGENT_SETUP.md,
GENERIC_DAEMON_APPROVAL.md and LINUX_PROTECTED_PROMOTION.md. Review distribution
defaults for new accounts and verify all privileges under the actual UIDs.

The ordinary and account-only modes never enable services, register MCP, change
agent settings, add a reviewer, migrate this session, or promote a package. Ordinary mode
refuses root; account mutation mode requires Linux, root and interactive
confirmation. Do not run account mutation through an agent tool.

The separate administrator deployment mode below can add the selected reviewer
and enable the two system sockets when explicitly requested.

## Deploy accounts and the protected service together (Linux/systemd)

Use a separate agent-inaccessible administrator console. Review the exact
checkout commit, including `install.sh` and `scripts/install_protected.py`.
The system `/usr/bin/python3` must be 3.10+, with Git, systemd (including
`systemd-analyze`), account-management utilities and Zenity already installed.
There are no dependency downloads. WSL needs working systemd and a separately
verified human graphical session; Linux detection is insufficient.

Replace `REVIEWER` with your existing human login and `REVIEWED_COMMIT_SHA`
with the full 40-character commit you reviewed. These are placeholders.

```bash
# Read-only preview; may run without sudo. Password locks are rechecked as root.
./install.sh --deploy-protected --reviewer REVIEWER \
  --commit REVIEWED_COMMIT_SHA --plan --enable-services

# Explicit administrator operation: accounts + service installation + sockets.
sudo ./install.sh --deploy-protected --reviewer REVIEWER \
  --commit REVIEWED_COMMIT_SHA --enable-services
```

Type `DEPLOY PROTECTED FORGE` only after reviewing the displayed plan. Any sudo
password prompt stays in that independent console. Do not give passwords to
the agent. Omitting `--enable-services` installs dormant units without enabling
or starting the system sockets. Ordinary `./install.sh` remains unprivileged.

The administrator normally reads a checkout owned by the human. The installer
passes `safe.directory=<that exact resolved checkout>` only to its read-only
Git commands, so sudo does not require changing root's global Git settings.
It never runs `git config --global` or trusts `safe.directory=*`. Exact commit
and installer-byte verification still apply. If an older installer reports
`dubious ownership` before making changes, update to a reviewed fixed commit
and use that commit's full SHA; do not disable the ownership check globally.

The deployment creates missing role accounts or accepts the existing accounts
from account-only mode after checking distinct non-root UIDs, locked passwords,
`nologin`, dedicated primary groups, no supplementary groups, and `/nonexistent`
homes. It adds only the explicitly selected human to `kf-reviewers`. Unexpected
existing members or shared role groups cause refusal. These checks do not prove
absence of every sudo/polkit/credential/desktop privilege path.

It reads a pinned Git archive, not live working-tree Python files, and refuses
if either installer file differs from the chosen commit. Installed code is
root-owned under `/opt/toadaid-kf-operator`, with isolated `/usr/bin/python3 -I
-B` launchers and a `DEPLOYMENT.json` recording commit and installed hashes.
The fixed configuration is `/etc/toadaid-kf/operator.json`; mode-0700 state is
owned by `kf-operator` under `/var/lib/toadaid-kf-operator`. State initialization
runs as that non-root account using the installed code.

The system receives `knowledge-forge-operator@.service`, the human-reviewer and
agent-request socket units, and the runtime tmpfiles rule. The optional Codex
native-proof socket is not installed or enabled. The desktop broker unit is
installed at `/etc/systemd/user/knowledge-forge-reviewer.service`, available but
disabled for users until the selected human explicitly enables it.

Existing protected code/state/configuration/unit targets are refused without
overwrite. This is an initial deployment command, not an upgrade/reset command.
If interrupted, partial accounts/files/units may remain; inspect independently
and preserve any registry/authorization state. Do not delete it or repeatedly
rerun the installer to force success. There is no automatic rollback.

## Finish the human session and agent integration

After successful deployment, the human reviewer logs out/in so the new group
membership takes effect, then runs these commands in their graphical session:

```bash
id                         # Confirm kf-reviewers membership.
systemctl --user daemon-reload
systemctl --user enable --now knowledge-forge-reviewer.service
systemctl --user status knowledge-forge-reviewer.service
```

The installer never starts a broker as the human, captures desktop credentials,
or changes Codex/Claude/MCP settings. If the user manager lacks the graphical
display environment, fix it within the human session; do not expose it to the
agent or operator. A missing display must refuse approval, never auto-approve.

**An existing agent running as the human remains outside the protected design.**
An administrator must arrange actual agent execution/request transport under
`kf-agent`, exclude human/privileged access and verify workspace/socket access.
The installed request CLI and bounded MCP launcher are under
`/opt/toadaid-kf-operator/bin/`; neither is automatically registered. Request
explicit user authorization for any host configuration changes. Starting a
different-UID daemon by itself does not isolate a human-UID model process.

Re-run `bin/knowledge-forge-setup --check-only`, then perform synthetic accept,
reject/cancel, expiry/replay, wrong-UID/socket, absent-display, changed-registry,
restart and interrupted-persistence tests under the actual three identities.
Check ancestry/ownership/ACLs and actual sandbox reachability independently.
`INSTALLED, NOT CERTIFIED` and active sockets do not prove those boundaries.
Only after verification should a real VALIDATED package be submitted for its
own human decision. Setup consent approves no knowledge package.
