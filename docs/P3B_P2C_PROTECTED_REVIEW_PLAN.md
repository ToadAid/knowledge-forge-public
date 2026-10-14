# P3B-P2C-P1 — Protected-host review plan inputs (NO authorization)

**Status: UNTRUSTED DATA CONTRACT ONLY. No host attestation, tokens, root operations, approvals, ledger, or promotion.**

## Why this cut exists

P2B2B-P0 inventory found no `kf-agent`/`kf-operator` OS identities on TNG.
The existing `tommy` identity has sudo and Docker, including writable Docker
sockets. P2B2B-P1 reconciled local public `main` at CP0 and ran 547 tests.
Provisioning still requires an *independent human-only* administrative session.

The existing `approval.py` review receipt is an **unauthenticated intent**. The
existing privileged `operator_cli.py` constructs `OperatorAuthorization` with
`verified_by_host=True` based on caller assumption; it **MUST NOT** be launched
by Claude, Codex, GLM, or by any account accessible to them. P2C-P1 does not
call or import the privileged operator and cannot grant permissions.

## P2C-P1 product

`protected_review_plan.py` accepts an existing canonical request, evidence,
caller-supplied candidate binding, a *claimed* registry-head SHA and canonical
UTC observation time. It verifies internal consistency, 15-minute maximum
request lifetime and syntax, then emits a **plain, deterministic, zero-authority
comparison plan** for a future independent host. No plan property claims that
an input was read from protected storage; even a perfect plan can be forged.

Revalidation of this plan checks format and forbids extra fields/claims such as
`verified_by_host`, `operator_invoked`, `promotion_authorized`, nonempty tool
grants, or `human_authentication_verified=true`. It does not provide an
attestation, authenticator, password, challenge, bearer token or cryptographic
integrity against an attacker controlling the agent workspace.

## What must happen next (not provided by this module)

- **P2B2B-P2 (human only):** Independently approve and provision the two Linux
  accounts and protected directories; verify sudoers/polkit/socket/TTY policy
  under real separate UIDs. Fail closed on unexpected existing state.
- **P2C-P2:** Root-owned, reviewed operator code loaded from a pinned immutable
  source; independently authenticated human ceremony inaccessible to the agent.
- **P2C-P3:** Protected durable single-use decision ledger: unpredictable
  host-created challenge, expiry, registry-head/validated candidate binding,
  race-safe consume-once, cancellation, restart/replay refusal and audit.
- **P3:** Restricted process integration, independently fresh candidate +
  registry re-read, protected promotion receipts and two-UID adversarial tests.
- **Pilot:** Fresh Claude + GLM in empty workspace with no operator keys/tools.

If the human-only administrator boundary cannot be demonstrated, stop before
provisioning or installing promotion code, even if all synthetic tests pass.

The module is portable Python stdlib and must not import `pwd`, `grp`,
`operator_cli`, `operator_promotion`, or any OS-specific privileged host API.
The native Windows CP1 installer is a separate, non-promoting development track.
