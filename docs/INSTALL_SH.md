# Optional install.sh entry point

This wrapper keeps ordinary installation separate from Linux administrator
account preparation. It is not a complete protected-deployment installer.

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

The wrapper never enables services, registers MCP, changes agent settings,
adds a reviewer, migrates this session, or promotes a package. Ordinary mode
refuses root; account mutation mode requires Linux, root and interactive
confirmation. Do not run account mutation through an agent tool.
