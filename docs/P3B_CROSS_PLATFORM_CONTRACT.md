# P3B-CP0 — Cross-platform Forge contract and support truth

**Status: ADDITIVE REVIEW CONTRACT ONLY. NO NATIVE WINDOWS INSTALLER OR TRUSTED OPERATOR.**

## Non-negotiable invariant

The Forge's *knowledge* contracts must remain identical across host OSes and
across Claude Code, Codex, Agent0, GLM, and future agents. A platform adapter
may change how code runs, installs or protects files, **never the meaning of
VALIDATED, PROMOTED, evidence, candidate IDs or authorization**.

`CANON != SKILL`; `KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY`;
`VALIDATED != PROMOTED`; a model may request human review but **never promote**.

## Grounded support matrix (2026-10-14)

| Host | Current ordinary install | Operator / promotion status |
| --- | --- | --- |
| Linux | POSIX Bash installer; TNG regression proof | P3B Linux *diagnostics* only; protected host not provisioned |
| macOS | POSIX documented; no macOS runner proof | Not implemented / not tested |
| Windows WSL | POSIX documented; no WSL runner proof | WSL does not prove independent Windows host security |
| Native Windows | **No Forge `.cmd`/PowerShell installer yet** | Not implemented / not tested |

The new `platform_contract.py` is self-observed **review-only data**. Even if
an agent forges a platform label, it cannot obtain any authority via this API.
It cannot attest to identity, attest to sandboxing, install, grant tools, authenticate,
mutate a registry, or authorize promotion. POSIX-specific P3B security modules
stay Linux-specific; importing their `pwd`/`grp` dependencies in a native
Windows entrypoint must be avoided by future adapter design.

## Platform-independent evidence (future interoperability proof)

Portable evidence envelopes and semantic JSON schemas must be byte-stable and
path-neutral. Canonical digest inputs must not depend on path separators,
Windows drive letters, shell quoting or local timezone. Preserve integrity
checks, candidate lifecycle and registry-head/stale-state semantics. Do not
pretend the four artifact-family names in CP0 are a new serialization standard;
reuse the currently published Forge schemas and add explicit cross-platform
fixtures before claiming interoperability. Never add `verified_by_host` as a
caller-controlled shortcut to the privileged operator CLI.

## Tracks and acceptance

**CP1 — Windows native consumer/authoring installer**, in
`ToadAid/knowledge-forge-public`:

1. Implement a **Python stdlib installer core** and tiny `.cmd` wrapper for
   native Windows. Follow the existing native Windows installer pattern in
   `ToadAid/toadaid-agent-capabilities` (a `.cmd` transport wrapper around a
   shared cross-platform implementation), without importing its authority.
2. Use `sys.executable`/`py -3` or a deliberately selected interpreter; no
   implicit PowerShell policy, Git Bash, WSL, Windows administrator rights,
   `cmd /c` on untrusted input, or unsafe shell-concatenation.
3. Default Windows state under `%LOCALAPPDATA%` only when verified; explicit
   bounded paths win; fail closed for missing, UNC/device paths, symlink/
   junction/reparse escape, stale collisions, untrusted mutable targets and
   inconsistent manifests. Use Windows-aware file/ACL inspection where needed.
4. Install only the **ordinary** `knowledge-forge` and
   `knowledge-forge-authoring` command shims. Never install/expose
   `knowledge-forge-operator`. Installation is not agent integration.
5. `--check-only` does no writes. No account creation, registry writes,
   elevation, secrets, network services or promotion. Native Windows tests
   prove fresh-prefix install, repeat install, tampering/collision refusal,
   dry run, paths with spaces, cleanup refusal and no operator shim.
6. Run an actual GitHub `windows-latest` job and an independent Windows
   desktop proof with native `cmd.exe` and both Claude Code and Codex.

**CP2 — Protected host adapters, all OSes:** Linux distinct UIDs/ownership,
Windows distinct service account/token/NTFS ACLs and independently controlled
human-approval UI, macOS separate privileged architecture later. No OS adapter
may infer authority from installed shims, review text, platform detection,
connected MCP host, human TTY shared with a model, or simple filesystem tests.
The Windows threat model must include Administrator/UAC, service token identity,
reparse points, inheritable handles, impersonation, named-pipe DACLs, and
Code Integrity/pinned launch path. Tests must use *real separate identities*.

**CP3 — Shared fixture compatibility:** same reviewed candidate and evidence,
identical canonical digest, equivalent registry read; independent human
approval required on each host. Exercise replay, stale candidate, registry
head race, cancellation and crash/retry under both Windows and Linux.

## Coordination

Knowledge Forge remains source of truth for knowledge/promotion protocols.
Numair's Windows capabilities work may provide **governed transport/host
integration** but must never become a promotion authority by being installed.
Keep the repos modular. Open PRs to their respective owners; no automatic
merges, policy-grant widening or system provisioning from the agent.

## Deployment and honest labeling

CP0 does **not** claim native Windows compatibility, macOS parity, a security
boundary, operator provisioning, authenticated approval or a usable Windows
installer. Update public compatibility claims only after real target OS proofs.
