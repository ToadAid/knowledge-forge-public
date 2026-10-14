# P3B-P2C-P2 — independent human/host authentication boundary review

**STATUS: DOCUMENTED HOST REQUIREMENTS ONLY. NO AUTHENTICATION, NO OPERATOR SERVICE, NO PROVISIONING, NO TOKENS, NO PROMOTION.**

## Purpose and upstream truth

P3B-P2C-P1 is a syntactically checked **untrusted review plan**. It cannot attest candidate or registry truth, authenticate a reviewer or issue a decision. P2C-P2 adds a Linux/TNG **non-authorizing proof checklist** tied to the exact claimed P2C-P1 comparison values. It does not weaken or replace any approval or candidate lifecycle contract.

On TNG, the accounts `kf-agent` and `kf-operator` and protected paths are not yet provisioned. The existing `tommy` account has `sudo` and `docker` group membership and can write Docker's privileged socket. Running Claude/Codex/GLM or a nominally isolated process with access to `tommy`'s tools, sockets, SSH agent, environment or privileged terminal **cannot** establish the required separation. This is a real blocker, not a passing diagnostic.

## What the module does

`src/knowledge_forge/host_auth_boundary_plan.py` provides:

- `build_untrusted_host_auth_requirements(...)`: checks P2C-P1's internal plain-data contract and compares caller-supplied review times; copies exact request, candidate and claimed registry-head bindings; enumerates thirteen independent host proof questions. All flags are false. No operating-system attestation is performed.
- `validate_untrusted_host_auth_requirements(...)`: reprojects and checks exact fields against the **original untrusted** P2C-P1 plan. It refuses caller-supplied claims such as `human_authenticated=true`, `verified_by_host`, tool grants or a forged registry binding. Validation does **not** establish identity or an operator authorization.

The `review_plan_sha256` binds a deterministic canonical representation of agent-controlled bytes. It is not an authenticated signature, root-owned observation, replay fence or human approval. Caller-supplied `observed_at` is not a trusted clock. The output is only a design/review artifact; never interpret an untrusted `expected_registry_head_sha256` as the current registry head.

## Future separately implemented trusted boundary — acceptance requirements

1. **Different principals:** `kf-agent` and `kf-operator` are distinct non-root Linux UIDs, also distinct from human `tommy`. Agent cannot use sudo, Docker, polkit, helper services or a delegated tool to regain `tommy`/root/operator.
2. **Human-only login/control:** independently authenticated human session (physical secure console or independently protected TTY/process) is demonstrably unreachable by agent tools. SSH into the same agent-controlled TTY or having the agent call `sudo` is insufficient.
3. **Pinned trusted executable:** reviewed exact commit deployed under root-owned, non-agent-writable `/opt/toadaid-kf-operator`; no Python import, config, package cache or environment injection from writable agent paths.
4. **Private operator state:** `/var/lib/toadaid-kf-operator` owned by `kf-operator`, mode 0700, symlink-safe ancestry; validate real ACL and process EUID. State, receipts, keys, and any future IPC endpoint must not be agent-readable or agent-invocable.
5. **Fresh trusted re-read:** independently snapshot candidate bytes, validate lifecycle/evidence against approved SHA, re-read protected registry head, and compare to the human's independently displayed exact request; untrusted P2C-P1/P2C-P2 inputs never count as proof.
6. **Human confirmation:** human sees current candidate/source and registry binding, and approves explicitly. Untrusted review TUI strings, agent text, Telegram messages or a model-controlled desktop cannot stand in for trusted confirmation.
7. **Independent trusted time:** enforce <=15-minute request expiry against host time; no backwards clock acceptance or human/agent editable timestamp authority.
8. **Protected decision:** P2C-P3 must create unpredictable one-use intent, persist it with crash-safe atomic consumption, bind to principal + exact candidate and registry head + purpose + expiry, refuse duplicate/replay and ambiguous crash outcomes, and emit code-owned audit without leaking secrets.
9. **Operator invocation:** the existing checkout-local `operator_cli.py`/`OperatorAuthorization(verified_by_host=True)` presupposes authentication and **must not be offered to Claude/Codex/GLM or copied into their command prefix**. Until the protected host mediates each call, promotion remains OFF.
10. **Adversarial evidence:** independent human verification of real separate UIDs, sockets, inherited FDs, policy escalation, path/TOCTOU conditions, named services, protected launch, restart and failures is required before the newcomer pilot.

## Split stages

- **P2C-P2 (this cut):** no-authority host-auth requirements envelope and synthetic refusal tests only.
- **P2B2B-P2 (human administered):** independently approve and provision restricted Linux users and directories. This is not an agent cut and is not implicitly approved by passing these tests.
- **P2C-P3:** implement protected durable single-use authorization ledger in a trusted host, **after independent admin boundary is proved**. Design-only contracts may be built before then; no live production authority.
- **P3:** operator integration and real two-UID adversarial replay/race/crash tests, then first real Claude+GLM fresh-workspace pilot with no credentials or operator tools accessible to the model.

Native Windows operator isolation and macOS security are separate platform adapters, not covered by this Linux/TNG review cut.
