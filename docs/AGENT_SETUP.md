# Agent setup: ordinary tools and optional protected approval

This is part of onboarding. Explain the protected option before a newcomer
expects an approval popup from the ordinary installer. The agent handles the
checks and preparation; account/service provisioning stays with an independent
human administrator.

## 1. Finish the ordinary install

Follow INSTALL.md and INSTALL_AGENT.md. Install the consumer and authoring
commands, check their help, and show paths. Keep existing agent settings and
services unchanged. Personal Forge can author, validate, accept and retrieve
without any daemon or extra accounts.

The installer output links here. `READY` means the ordinary commands are ready;
it does not mean protected approval is deployed.

## 2. Run the setup helper

From the checkout, the agent runs:

```bash
bin/knowledge-forge-setup --check-only
```

Or, on any platform with Python, including native Windows:

```text
python -B src/knowledge_forge/agent_setup.py --check-only
```

The helper reads account/group and filesystem metadata only. It creates no
accounts, runs no shell commands, reads no keys, enables no services and changes
no agent settings. Missing prerequisites are a successful diagnostic report,
not a verified deployment. It deliberately never reports protected approval
ready: service activity, code ownership/ancestry, complete privilege exclusion,
the actual transport UID, sandbox access and a real human UI must be verified
independently. Known privileged groups are a partial check, not proof that no
other privilege path exists.

The daemon reference requires Linux/systemd. Native Windows and macOS retain
ordinary Personal Forge support; the helper reports protected setup unsupported
there. On WSL, independently verify systemd and the human graphical session;
Linux detection alone does not establish either.

## 3. Ask once, respecting the user's existing choice

If no preference has already been supplied, explain that Personal Forge needs
no service, then ask:

> Would you like protected promotion with approval popups? It requires you or
> your administrator to create separate `kf-agent` and `kf-operator` accounts,
> keeping your human reviewer account separate.

This is the **agent's conversational onboarding question**, not an interactive
installer prompt or a per-package approval. The helper returns it as data for
the agent to present. It never treats input text as consent or authorization.

"Do not perform governed promotion" forbids promoting packages; by itself it
does not decline an explicitly requested setup explanation or onboarding
question. Respect an explicit Personal Forge-only or no-service choice as below.

- **Yes / protected promotion already requested:** prepare the administrator
  handoff below. Do not ask the same choice again. A setup yes authorizes
  preparation, not promotion or an agent impersonating the administrator.
- **No / Personal Forge only / services forbidden:** continue ordinary use.
  Do not create accounts, stage a privileged deployment or keep asking.
- **No answer:** leave protected setup pending; continue independently
  authorized personal authoring, validation and retrieval work.
- **Unsupported platform:** explain the exact platform limit. Do not ask the
  user to create Linux service accounts on a platform this reference cannot run.

Do not collect sudo passwords or other administrator credentials in chat.

## 4. Prepare the concrete administrator handoff

After a protected-setup choice, show the exact reviewed checkout commit,
read-only findings, and deployment plan from
[GENERIC_DAEMON_APPROVAL.md](GENERIC_DAEMON_APPROVAL.md) and
[LINUX_PROTECTED_PROMOTION.md](LINUX_PROTECTED_PROMOTION.md). Identify:

1. `kf-agent`: the actual model execution/request transport account, non-root,
   without sudo, Docker, reviewer membership, desktop access or operator state.
2. `kf-operator`: a different non-root service UID owning protected state.
3. The existing human reviewer UID, distinct from both, with human-only
   `kf-reviewers` membership. Do not repurpose the human account as the agent.
4. Root-owned pinned code/configuration, operator-owned state, and the separate
   agent-request and human-review sockets with their role ACLs.
5. The reviewer broker in the human desktop session and the allowed bounded
   CLI/MCP request transport under `kf-agent`. Agent settings/MCP registration
   require the user's explicit authorization; do not modify them silently.

The administrator reviews and provisions from a **separate agent-inaccessible
console**. The agent may prepare the handoff but must not execute a root
provisioning script, create the accounts itself, launch a reviewer as the human,
or run a fake approval. There is no combined privileged installer in this flow.

The administrator may review the account-only convenience script described in
[INSTALL_SH.md](INSTALL_SH.md). Its `--create-protected-accounts --plan` mode is
read-only; its explicit privileged mode is for the administrator's independent
console only. It creates locked non-login accounts and an empty reviewer group,
not services or a verified protected deployment. Ordinary installation remains
unprivileged and Personal Forge needs no account provisioning.

## 5. Verify, then request the real package approval

Re-run the helper after provisioning and show what changed. Existing account
names, socket files or green software tests are not deployment certification.
Independently test the actual three UIDs and transport using synthetic packages:
approval, reject/cancel, expiry/replay, unavailable display, agent attempts to
decide, changed registry and interrupted persistence.

Only after that deployment is verified may the agent submit real VALIDATED
packages and poll protected status. The human approves each exact package in
the separate popup. Report promotion only from the real protected result and
receipt. Setup consent does not approve a package. Personal library acceptance
remains a separate explicit step.
