# P4-P1-P1-P3-P9B — Dell synthetic CLI acceptance pilot (premerge local stage)

**LOCAL STAGING ONLY. NOT A NATIVE AGENT TEST, NOT HUMAN APPROVAL, NOT GOVERNED KNOWLEDGE CONSUMPTION, NOT A PUBLIC RELEASE CERTIFICATION.**

## Why this cut exists

The intended public experience is source → authored draft → grounded, inspectable `VALIDATED` skill → separate authenticated promotion → governed current knowledge. P9B explicitly tests only the portions already available to an ordinary user, **without** inventing a privileged promotion, forging a current registry, or turning manual package inspection into native-agent knowledge integration.

P9A staged an offline reconciliation from exact main `405d1187c2f53fcfaccf2dcfdb632a4e41d56706`; it remains **local-only** at `6050e3cca3af755a4740111535d26e9e76cbcc86`. PR #18 (native Windows installer) and PR #36 (review-only native-test approval-request packet) are **unmerged** at the P9B starting point. P9B uses its **own separate Dell worktree and branch**, independently based on the same pinned protected `main`. It does not depend on P9A, import unpublished P9 content, advance Dell `main`, or publish a PR. Only after #18 then #36 merge, rebase/reconcile both P9A and P9B in clean new final-main worktrees, re-run the full CI matrix, and separately review publication.

## Real deterministic ordinary-CLI path

1. Create a temporary, **synthetic** Markdown source containing two normalized sections: one supported claim and one unrelated section. Record original source SHA-256; keep unchanged.
2. Create a source-hash-bound authored Markdown draft with one structured, `executable=false` claim. Its synthetic judge plan explicitly says `SUPPORTED` for normalized `s001`. This is a controlled fixture, **not independent model truth**.
3. Invoke the ordinary `bin/knowledge-forge-authoring prepare` from the pinned checkout. Verify two source sections, exactly one draft unit, and no authority.
4. Invoke ordinary `finalize`, producing an actual `VALIDATED` package and lifecycle. Verify `promotion_performed=false`, `human_authorization_consumed=false`, and no execution authority.
5. Invoke ordinary `bin/knowledge-forge validate-package` and verify the package independently.
6. Create an **empty** synthetic registry using the provider-neutral registry constructor. Invoke ordinary `registry-inspect`, `discover` on the supported topic, and `discover` on an out-of-scope topic. Both must **refuse** with exit code 2 and the exact structured `no governed knowledge skill matched` error on stderr; success with `match_count=0` is *not* the public consumer CLI contract. Then attempt `load-current` with the `VALIDATED` package and empty registry; it must **refuse** with status 2 and no success output.
7. Verify negative controls: a post-draft source mutation refuses finalization without output; an `UNSUPPORTED` support plan refuses finalization; a mutated `SKILL.md` refuses independent package validation.
8. The temporary fixture and all created package/state files disappear at test end. The public report carries only Boolean and count evidence, no original HOME, credentials, fixture paths, package bodies, user prompts, or secrets.

**P9B-R1 (2026-10-15):** The first Dell run failed at `discover` because the pilot incorrectly expected exit 0 with zero matches. The actual governed discovery code deliberately refuses an unmatched query with exit 2 and structured stderr. R1 updates only the pilot's expectation and receipt nomenclature, requires the exact no-match error, and adds negative controls that reject other errors or a misleading success response. The original product discovery implementation stays untouched. First-run focused integration and full-suite completion remain unproven until Dell reruns the guarded repair.

The test intentionally does **not** create a `PROMOTED` registry, invoke the privileged operator, enroll or run native Claude/Codex, install hooks, connect to models or issue approval. It never asserts real source-grounded question answering, host adapter integration, a validated human reviewer, OS network isolation, or general-user usability certification.

## Expected result taxonomy

| Dimension | Possible result |
| --- | --- |
| `INSTALL` | Only `--check-only` is run in stage script; **no installer activation** |
| `AUTHOR` | **PASS** if actual `prepare/finalize` emitted `VALIDATED` |
| `VALIDATE` | **PASS** if CLI validates and tampering is refused |
| `DISCOVER` | **EXIT 2 / NO GOVERNED MATCH**, not a successful empty match list |
| `CONSUME` | **REFUSED** as required for unpromoted knowledge |
| `HOST ADAPTER` | **NOT TESTED** |
| `MODEL ANSWER QUALITY` | **NOT TESTED** |
| `HUMAN PROMOTION` | **NOT ATTEMPTED** |
| `NATIVE CLAUDE/CODEX` | **NOT ATTEMPTED** |
| `PUBLIC USER USABILITY` | **NOT YET VALIDATED** |

## Post-publication R2 — native Windows CI test-boundary reconciliation

The draft PR's first Windows CI run (`38829873606`) failed on both Python 3.10
and Python 3.13 because `P9BRealOrdinaryCliTests.setUpClass` unconditionally
called `run_pilot()` on native Windows; the pilot intentionally raises
`AcceptancePilotError('P9B Dell/Linux pilot only')` **before** reading a checkout
or spawning any process. This was a **test selection error**, not evidence that
ordinary Windows consumer/authoring installation failed. Linux CI succeeded.

R2 limits the real E2E subprocess **test class** to Linux (not a blanket test
module skip), keeping all eight real checks active on Linux. Two new
cross-platform contract tests simulate `win32` and require that the callable
pilot refuses before checkout checks or subprocess execution, and that the
synthetic CLI mode refuses with **exit 2, empty stdout, exact stderr**. These
negative tests also run on native Windows CI. No production implementation,
installer, security gates, operator, or native host state is changed.

**Evidence limit:** a green Windows matrix following this repair will prove the
existing native Windows installation suites and the P9B fail-closed refusal,
**not** Windows-native end-to-end P9B authoring/finalization. A separate Windows
consumer+authoring E2E pilot using the installed `.exe` shims requires its own
scoped cut and proof, not a reinterpretation of these Linux results.

## Dell staging / reproduction

On exact Dell7920 protected-main P8 base, the self-contained P9B script uses an isolated worktree, exactly three SHA-256-sealed new source files, focused tests, complete `unittest discover -s tests`, read-only `bin/knowledge-forge-install-agent --check-only`, explicit synthetic pilot result and negative tests, then one **local-only** staging commit. No pushes, PRs, branch resets, protected-main writes, native hosts, operator commands, approvals or network requests from the pilot. The bootstrap shell performs bounded GitHub metadata queries and a single `git fetch` solely to obtain the pinned base.

Run the narrow test and pilot manually from the staged worktree:

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_portable_acceptance_pilot
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m knowledge_forge.portable_acceptance_pilot --mode SYNTHETIC_CLI_ONLY
```

Security laws: `VALIDATED != PROMOTED`, `REVIEWED != AUTHENTICATED`, `KNOWLEDGE != AUTHORITY`.
