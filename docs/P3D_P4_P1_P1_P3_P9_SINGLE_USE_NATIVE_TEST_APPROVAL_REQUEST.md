# P4-P1-P1-P3-P9 — Single-use native-test **approval request** template

**REVIEW ONLY. NOT AN APPROVAL. NOT A LAUNCH TICKET. NOT NATIVE-TEST READINESS.**

P9 creates a deterministic, offline proposal for a future narrowly authorized Claude Code **or** Codex CLI test. It **does not** issue or consume a nonce or approval, authenticate a human, start a native agent, register hooks, inspect real user/managed/project/plugin configuration, send network traffic, access credentials, call models, or promote knowledge. Its fingerprint is a reproducible label **not** an authenticated authorization. All nine P2 proof gates remain `UNVERIFIED_NATIVE_EVIDENCE_REQUIRED`.

## Input evidence versus missing proof

- **P6:** TNG passively hashed Claude Code version-path `2.1.296` (`257068216` bytes, SHA-256 `24972e3bc859fab2b46ed4c1e51f7d6130f06d3bd550811a114640de3370d0de`) and Codex CLI `0.161.0` (`292309256` bytes, SHA-256 `9a820c17865fa825d04db416818679a9d63bd72e50835c396f496e5684626c9c`). These are **historical executable snapshots** only. Fresh absolute-path/inode/symlink authenticity and loader/dependency closure are unverified. Claude ELF dynamic strings were refused as `DYNSTR_BOUND_OR_ADDRESS_REFUSED`; Codex `NO_DT_NEEDED_OBSERVED` does **not** prove fully static linking.
- **P7/R4:** The TNG Python-only synthetic Bubblewrap fixture proved net/mount/PID namespace separation, loopback-only interface listing, a read-only `/usr`, private synthetic project/config/tmp and a strictly four-key Python re-exec environment. That is **not** sandbox attestation for either actual agent process. The broad read-only `/usr` runtime mount is not a vetted minimum for native hosts.
- **P8:** Candidate managed, user, project, local, plugin, session and built-in hook-source classes were mapped, but **no effective native hook loading** was observed. Neither Codex `hooks.json` nor the `UserPromptSubmit` event is accepted as supported by the exact installed Codex executable without version-specific evidence. Unknown or extra hook sources cause a test refusal.

## Proposed one-shot test envelope (not enforced by P9)

A human host may, **only after independently closing prerequisites**, separately consider an authorization valid for **one** exact native-host launch, **one** synthetic hook event, **20 seconds** maximum runtime and **10 minutes** maximum approval validity; zero model calls, zero tool calls, zero emitted hook stdout/stderr, zero external network, no credentials, no original user HOME/project, no promotion or hook activation outside the isolated synthetic temporary test. Use a fresh review of exact executable and dependency tree, pinned mount plan, effective hook trust, no-model/no-tools feasibility, and cleanup receipts. Any inability to prove or enforce these constraints **blocks P10**.

The required future one-time credential must be minted by a **separate authenticated human-controlled host** and include exact host, executable digest/path pin, scope, expiry, high-entropy unique nonce and **atomic use-once journal**. No such host, signature, nonce, journal, or approval is implemented here. PR merge, packet digest, review TUI receipt and synthetic evidence **never** supply consent. No native test can be run through P9's CLI; `REQUEST_REVIEW_ONLY` is its sole accepted mode.

## Blockers carried forward without dilution

P9 returns eight prerequisite families, all `BLOCKING_UNVERIFIED`: fresh executable/path provenance, ELF dependency closure, actual minimum native read-only mounts, effective managed/user/project/plugin hook-source trust, version-specific host hook support, no model/tool/network/secret activity, independent authenticated one-time approval, and bounded lifecycle/receipts. The nine canonical native proof gates remain explicitly unverified. Completion of a proposal must never be mistaken for their closure.

## Run (inert, no agent process)

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_portable_native_approval_request
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m knowledge_forge.portable_native_approval_request --host claude-code --mode REQUEST_REVIEW_ONLY
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m knowledge_forge.portable_native_approval_request --host codex-cli --mode REQUEST_REVIEW_ONLY
```

Before any prospective P10 work, separately obtain explicit human authorization **for a precise executable, host and test scope**, and review whether all independent technical gates can in fact be met. If a prerequisite cannot be proven, remain **BLOCKED**. P11 release work and P12 community announcement must not imply native proof until it exists. A bounded public preview of existing non-native features may be considered separately with honest labeling.

**Laws:** `VALIDATED != PROMOTED`; `REVIEWED != AUTHENTICATED != AUTHORIZED != EXECUTED`; `KNOWLEDGE != AUTHORITY`.
