# P3D-P1-P0 — Claude/Codex portable governed-retrieval preflight

**STATUS: READ-ONLY, NON-AUTHORIZING, OFF/DRY_RUN ONLY. No `ON`, no hooks installed, no automatic retrieval, no human authentication, and no skill promotion.**

## Purpose

Knowledge Forge already provides provider-neutral `ConsumerBinding` discovery and the current-skill temporal trust wall. This first host-adapter cut creates a *common diagnostic entry point* for two unmodified CLI agent hosts: `claude-code` and `codex-cli`. It does **not** connect them to knowledge automatically; that requires separately reviewed, host-controlled lifecycle hooks and trusted `PROMOTED` registry/package mapping.

The ordinary user keeps one account, but a terminal process under that same account can impersonate keystrokes, change user-owned files, or supply a fake registry. This diagnostic does not authenticate human input or treat the local mapping as protected truth. User-friendly `APPROVE` from PR #16 is **review-only**; it does not authorize the operator.

## Modes and behavior

- **OFF**: no callbacks, registry reads, package resolution, discovery, load, injection or host changes. Tests force callbacks to throw if touched. OFF returns a bounded zero-authority status.
- **DRY_RUN**: require the caller's explicit registry and packages-map, validate current `PROMOTED` registry schema with the existing consumer core, route up to three skill identities (default one), and check that each caller-provided package path is a real directory without existing symlink components. This is **metadata only**, not package inspection or current-skill validation. It calls no `load-current`, reads no knowledge bodies, performs no writes, and places nothing into model context. Missing registry, stopped/no-match knowledge, unsafe path or configuration fails closed.
- **ON**: refused, even if requested on CLI. This cut cannot be used to activate retrieval.

All results have `reasoning_context=[]`, `model_context_injected=false`, `automatic_consumption_active=false`, `host_authentication_verified=false`, `operator_invoked=false`, `promotion_authorized=false`, `registry_mutated=false`, `execution_authority=NONE`, `instruction_authority=NONE`, `authority=KNOWLEDGE_ONLY`, `tool_grants=[]`. DRY_RUN emits only skill identities, versions, directory-existence diagnostics and explicit `trusted_registry_verified=false` / `promoted_package_revalidated=false`. Path and time are caller-observed, **not** host-attested. The diagnostic `lstat` path walk is not race-safe and is not an authorization primitive. The in-process `probe` API accepts callbacks supplied by its caller; callers must audit those callbacks separately, because this module cannot guarantee arbitrary callback code is free of side effects. The included CLI callbacks are read-only file loading and directory observation.

### CLI use

From the repository checkout (no installer mutation):

```bash
PYTHONPATH=src python3 -m knowledge_forge.portable_host_preflight --mode OFF --host claude-code
PYTHONPATH=src python3 -m knowledge_forge.portable_host_preflight --mode OFF --host codex-cli
```

DRY_RUN requires a **real** current registry (not a forged fixture) for ordinary use:

```bash
PYTHONPATH=src python3 -m knowledge_forge.portable_host_preflight \
  --mode DRY_RUN --host claude-code --registry /path/to/registry.json \
  --packages-map /path/to/packages-map.json --question 'my question'
```

The diagnostic caller-provided map schema is:

```json
{
  "schema_version": "0.1",
  "packages": {
    "<exact-current-skill-id>": {
      "version": 1,
      "path": "/absolute/path/to/package-directory"
    }
  }
}
```

This mapping is untrusted input; it does not confer `PROMOTED` truth. Do not generate or invent a production registry to make a demo pass. Synthetic registries are for tests only.

## Next cuts after P3D-P1-P0

1. Separate, genuinely human-authenticated one-account confirmation backed by OS-controlled identity/reauthentication that model-accessible shells cannot spoof. This may require a privilege-separated helper invisible to agents under the familiar user account. Do not run legacy operator CLI with model-controlled `verified_by_host=True`.
2. Protected one-use promotion with fresh source/registry re-read, protected code and ledger, two-actor adversarial tests, crash/restart reconciliation and audited receipts. Remain OFF until verified.
3. For Claude/Codex, implement *host-owned* hooks, `DRY_RUN` real-configuration checks, fail-closed package `load-current` and carefully scoped `ON` activation **after** authentic `PROMOTED` truth exists. Test OFF and DRY_RUN first, then no-trust/no-source/stale/incident/tool-widening refusals.

Windows is a separate implementation delegated to Numair. This cut has no hooks, OS accounts, daemon, credentials, operator, promotion, protected writes, PR, or merge. TNG tests and guarded publication remain required.
