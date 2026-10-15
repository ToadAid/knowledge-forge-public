# P3D-P2-P0 — Portable CLI `VERIFY_ONLY` current-knowledge verification

**STATUS: LOCAL READ-ONLY PROTOTYPE; NOT AN AUTHENTICATED HOST, LIVE AGENT HOOK, OR ENABLED RETRIEVAL.**

## One-account user experience

Claude Code or Codex CLI may run under the user's familiar login. P3D-P1-P0 already provides `OFF` (no callbacks) and `DRY_RUN` (metadata-only route/path check). This **additive** second stage adds **`VERIFY_ONLY`**, deliberately different from `DRY_RUN`, to exercise the *existing* Knowledge Forge `load_current_skill_context` trust + temporal wall on a caller-supplied promoted registry/package. Neither `APPROVE` nor `VERIFY_ONLY` can promote knowledge. User-typed `APPROVE` remains review acknowledgment only.

`VERIFY_ONLY` loads a bounded selected knowledge unit **internally into the verifier process**, checks package/registry/lifecycle/claim and temporal trust walls, then discards content. The response is a hardcoded allowlist of identity/count/booleans, with `reasoning_context=[]`; neither unit content nor source text is passed into a model, CLI response, agent hook, log, prompt, receipt, or memory. `OFF` makes zero external supplier calls. `ON` is permanently refused in this cut.

## Examples (checkout-local, nothing installed or activated)

```bash
PYTHONPATH=src python3 -m knowledge_forge.portable_verify_only --host claude-code --mode OFF
PYTHONPATH=src python3 -m knowledge_forge.portable_verify_only --host codex-cli --mode OFF

PYTHONPATH=src python3 -m knowledge_forge.portable_verify_only \
  --host claude-code --mode VERIFY_ONLY --question 'my question' \
  --registry /path/to/current-registry.json \
  --packages-map /path/to/package-map.json \
  --world-as-of 2026-10-15T12:00:00Z
```

Package mapping uses the P3D-P1-P0 `schema_version=0.1` + `packages` map. No registry/package may be fabricated for production demonstration. Tests use explicitly synthetic promoted fixtures only; these fixtures grant no real promotion.

## Security and honest limitations

- `verification_scope=CALLER_SUPPLIED_INPUTS_NOT_HOST_ATTESTED`. A same-account CLI agent can alter paths, registry, apparent world time, and supplier callback implementations; consistency with supplied values is **not** independent authenticated registry truth.
- The bounded verifier uses the already implemented `load_current_skill_context` with a fixed `max_units` <= 3 and <= 3 skills. It refuses invalid/stopped/no-match/tampered packages and temporal source-invalidity through existing trust wall. Its output does not contain even the selected unit IDs or source references. `model_context_injected=false` always.
- `VERIFY_ONLY` necessarily reads knowledge into process memory; do not claim `knowledge_bodies_loaded=false`. The guarantee is **no knowledge body in returned result**. Same-account code can introspect local process memory. This is not a privilege/security boundary.
- Untrusted or externally supplied callbacks may have effects; this module does not attest to purity of the calling host. The CLI's own callbacks read caller files. Paths are only diagnostic-checked by the existing P3D-P1 helper, with known TOCTOU limitations.
- Errors are redacted on CLI; no exception/source text is included in output. A failure cannot fall back to direct source reading, ignore tamper, activate `ON`, invoke `operator_cli`, or create a PROMOTED registry.
- No hooks, agent config, service, global install, account, ACL, operator, protected ledger, human reauthentication, PR or merge are part of this P0.

## Gates before real automatic consumption

1. A trusted read-only host adapter must obtain current PROMOTED registry from a host-controlled source and trusted package root mapping. Validate `OFF`, `DRY_RUN` and `VERIFY_ONLY` before enabling `ON`.
2. An independently protected promotion path requires genuine human-only authentication, fresh exact candidate/registry review, one-time replay-safe ledger, and operator code/state inaccessible to Claude/Codex. A typed terminal word alone never meets this gate.
3. A native Claude/Codex host hook must be developed and security-tested separately; production `ON` injection can carry **only** bounded `reasoning_context` marked `UNTRUSTED_KNOWLEDGE_DATA`, no instructions, tools, memory or privileges. Windows CP1 is a parallel unrelated installer PR from Numair, not a prerequisite for these Linux diagnostics.

**Laws:** `VALIDATED != PROMOTED`, `REVIEWED != AUTHENTICATED`, `KNOWLEDGE != AUTHORITY`.
