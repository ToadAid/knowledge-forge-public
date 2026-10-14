# P3B-P2A — protected operator OS-layout preflight (NO authentication)

This is the **first bounded P3B-P2 cut**, not a working trusted promotion
bridge. P3B-P1 remains an untrusted agent handoff; the existing review TUI
records **non-authorizing** intent. The existing privileged operator CLI
implicitly trusts its caller, so calling it from the same UID as Claude is
unsafe. Nothing in P2A changes that fact.

## Required TNG architecture (not yet provisioned)

```text
Claude/GLM process: unprivileged `tommy`, no sudo capability to operator
  -> ordinary Forge author/validate and untrusted P3B-P1 handoff
  -> human selects candidate independently outside Claude control
  -> separately authenticated human session (not an agent-accessible TTY)
  -> protected operator process under dedicated non-root `kf-operator` UID
      root-owned, read-only code below /opt/<approved-root>
      operator-owned 0700 private state below /var/lib/<approved-root>
      authenticated, bounded, one-time decision ledger (P2B, not P2A)
      code-owned, independently revalidated candidate and registry snapshot
      -> only then privileged promotion and protected receipts (P3B-P3)
```

`src/knowledge_forge/protected_host_gate.py` is an **observational diagnostic**
for a prospective separate account. It checks the *actual* process EUID, the
resolved local account UIDs, and directory ownership/modes, rejecting agent-
owned or group/world-writable paths and symlink ancestors. Root must own the
operator code root; the operator must own private state (0700 or stricter).
It returns `OS_LAYOUT_PRECHECK_ONLY_NOT_AUTHENTICATION` and all authority flags
**false**, even when the diagnostics pass.

**Do not run a privileged promotion using its output.** Path inspection by
`lstat` is race-prone; it does not verify signed/pinned code, PAM identity,
process origin, imported modules, token protection, one-time use, authorization,
registry head, or a trusted candidate snapshot. Code executed from a checkout
writable by `tommy` is untrusted for privileged operations. `sudo`, `setpriv`,
`runuser`, and `bwrap` being installed do not themselves protect the operator.

## Next bounded cuts

1. **P3B-P2B:** Review TNG's real `sudoers`/PAM/UID/ACL paths and design a
   privileged, human-controlled, agent-inaccessible provisioning ceremony. A
   separate human must approve creation of the account, protected code/state,
   and launch mechanism. Do not run provisioning from Claude's shell session.
2. **P3B-P2C:** Protected human approval, exact independently owned candidate
   and registry bindings, timeout, random challenge, atomic consume-once state,
   cancellation, explicit refusal/audit and crash/retry reconciliation. Agent
   can submit a candidate but cannot invoke the operator host or cause approval.
3. **P3B-P3:** End-to-end protected promotion plus adversarial two-UID tests;
   only then a fresh generic-agent Claude/GLM newcomer test. Record INSTALL,
   AUTHOR, VALIDATE, PREVIEW, AUTHENTICATED REVIEW, PROMOTE, CONSUME separately.

### CI note

GitHub Actions passed for Python 3.10–3.13; the `main` ruleset still does **not**
require the successful check. An administrator must enforce the exact check
`Knowledge Forge CI / required` from GitHub Actions before the next merge.
