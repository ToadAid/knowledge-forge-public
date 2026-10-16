# P3B-CP2 — Windows protected operator: design note only

**Status: DESIGN ONLY. NOT IMPLEMENTED, NOT INSTALLED, NOT ACTIVATED, NOT
AUTHORIZED.** Nothing in P3B-CP1 provides a Windows operator, a trusted human
approval channel, or promotion authority. A normal Windows login, an ordinary
installation, a green test run, a detected platform label or a connected agent
host never satisfies the trusted-human-approval requirement.

```text
INSTALLED != AUTHORIZED          PLATFORM DETECTED != TRUSTED HOST
AGENT REQUEST != HUMAN AUTHORIZATION          VALIDATED != PROMOTED
```

`knowledge-forge-public` owns the portable knowledge and promotion contracts.
`toadaid-agent-capabilities` may later supply governed host *transports*, never
ambient promotion authority. The repositories stay modular.

## Threats a Windows operator must defeat

| Area | Requirement for any future design |
| --- | --- |
| Service identity / token | Operator runs as a dedicated, non-interactive service identity (virtual service account or gMSA-style), distinct from every agent and every human login. Its token must not be obtainable by the agent's account (no `SeImpersonatePrivilege`/`SeDebugPrivilege` paths, no shared credentials, no auto-logon). |
| Human approval UI | Approval happens on a surface the agent's account cannot drive: a separate secure desktop or a separate authenticated device, never a console, TTY, window or clipboard shared with a model process. Typing into the agent's terminal is not approval. |
| Administrator / UAC | UAC is not a security boundary between processes of the same user. A filtered admin token, an elevated agent or an auto-approved consent prompt must never count as a second identity. Approval must not depend on the user being an administrator. |
| NTFS DACL inheritance | Protected state, binaries and the approval endpoint live under a root whose DACL is set explicitly with inheritance broken; writers limited to the service identity; verified at every start, not only at install. CP1 already shows real machines grant extra writers (Authenticated Users on drive roots; sandbox groups on `%TEMP%`). |
| Junctions / reparse points | Every path component is opened with reparse handling disabled and re-verified by handle (`GetFinalPathNameByHandleW`, file IDs), defeating junction swaps and TOCTOU. No string-only checks. |
| Named-pipe endpoints | Any IPC endpoint is created with an explicit security descriptor (no default DACL), `FILE_FLAG_FIRST_PIPE_INSTANCE` against squatting, remote clients rejected, and the client identity verified per request (`GetNamedPipeClientProcessId` plus token checks). |
| Handle inheritance / impersonation | Children are spawned with an explicit handle list (`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`), no inheritable privileged handles, and clients connect at `SecurityIdentification` at most, so the operator is never impersonated *by* a client and never impersonates one. |
| Protected state | Approval records, one-time nonces and registry heads are writable only by the service identity; readers get read-only handles; integrity checked on load. Agent-reachable copies are evidence, never authority. |
| Code integrity / launch path | The operator binary and interpreter are pinned (hash or signature) on an admin-owned, non-writable launch path; no `PATH`/`PYTHONPATH`/DLL search-order influence; optional WDAC policy. CP1's per-user `.exe` launchers are explicitly **not** acceptable for the operator. |
| Replay / cancellation | Each approval binds candidate digest, registry head, requester and an expiry; it is consumed once, atomically; cancellation and crash/retry are explicit states (mirroring the Linux SQLite consume-once pilots). |
| Two-identity verification | Proofs must use **real separate Windows accounts** (agent account vs. approver vs. service), demonstrated on a real host. Synthetic platform classification or a single-account test proves nothing. |

## Lessons from CP1 on a real desktop (2026-10-14)

- Packaged (MSIX) desktop apps can virtualize `%LOCALAPPDATA%` writes for every
  process they start; a path the operator "sees" may not be what other
  processes see. Resolve and verify real paths by handle.
- Sandboxed agents may run as **separate local accounts** (for example
  `CodexSandboxOffline`/`CodexSandboxOnline` via the `CodexSandboxUsers` group)
  that still have write access to parts of the user profile.
- AppContainer package SIDs appear in profile DACLs. They are same-user lowbox
  identities, but an operator must not treat any SID present in a DACL as an
  approver.

## Explicitly out of scope until separately approved

Implementing, installing or activating any Windows operator; creating Windows
users, services or ACL changes; issuing approval tokens; adding
`verified_by_host` shortcuts; changing `operator_cli.py`; or wiring promotion
into any agent. A CP2 implementation needs its own reviewed task, real
multi-identity host proofs, and human sign-off.
