# Knowledge Forge Public — feature availability

**Snapshot:** public `main` at
`490da9ab3a6002eef2309e6a403d3ed797ebc2ce`
(October 16, 2026). This document is a source-and-regression review,
**not** certification of an independently deployed agent integration.

| Capability | Availability | What the evidence supports |
| --- | --- | --- |
| Local checkout / ordinary Linux install | Implemented, tested | Installer checks, command shims, Linux CI |
| Native Windows ordinary install | Implemented, tested | Native `cmd.exe` Windows CI (Python 3.10/3.13); no operator installed |
| Source extraction and authoring | Implemented, agent-guided | Format-specific extractors, prepare/finalize and regression tests; external agent supplies structured draft and support plan |
| Package validation | Implemented, tested | `validate-package` and lifecycle tests; does not independently prove semantic truth |
| Personal Forge local library | Implemented, tested | `personal add/list/lookup/remove`, source bindings, tamper and no-match tests |
| One-sentence onboarding in any AI agent | Partial / not independently certified | Agent must read runbook and invoke commands; universal intent adapter is not installed |
| Auto lookup in fresh Claude/Codex conversations | Not included in generic installer | No native workflow/hook installed automatically |
| Governed registry / operator | Advanced, optional | Existing code/reference deployment; independent protections required for activation |
| OpenClaw / Hermes / generic native-host approval | Adapter contracts + synthetic tests | No proof of deployed host integration or protected principal |
| Codex native device verification bridge | Merged code + synthetic tests, not deployed | [PR #50](https://github.com/ToadAid/knowledge-forge-public/pull/50) merged the experimental `userVerification/verify` bridge with test-verifier coverage; it does **not** install a live Codex approval UI, enroll device credentials, or activate a protected backend |
| Claude native approval bridge | Not demonstrated | Do not infer from generic-host vocabulary or Codex bridge |
| Universal native `Approve once` button | Not deployed by generic installer | Requires host-specific real integration and enforcement boundary |

## Different approval meanings

**Personal acceptance:** user says yes; agent calls
`knowledge-forge personal add ... --accept`. The library explicitly records
`authenticated_human=false`. This is enough for local personal use and
**not** a `PROMOTED` lifecycle.

**Governed promotion:** `PROMOTED` requires an independent host to
authenticate a decision and protect its credential, signing key, registry and
ledger from agent permissions. Chat text, an agent-created PTY or a general
tool-command approval is not proof of this boundary.

For a first-run demonstration use [NEWCOMER_GUIDE.md](NEWCOMER_GUIDE.md).
For privileged operator deployment use the
[advanced host documentation](INLINE_APPROVAL.md).

## Known UX limits

- Newcomers currently depend on an agent following the structured-draft and
  support-plan runbook. That is more work than a universal "forge it" button.
- Personal lookup routes from indexed terms/aliases, not arbitrary semantic
  search; a miss may be due to vocabulary as well as absent coverage.
- Package files are not copied into the personal library; moving them
  invalidates references.
- PDF/OCR adapters have optional system dependencies.
- Source bindings survive in retrieval, but an agent must cite references
  actually returned rather than invent page numbers or URLs.

**Publication rule:** advance any `PARTIAL` or `NOT DEPLOYED` statement
only after code, adversarial tests and an actual host-run receipt support it.
