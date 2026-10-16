# P4-P1-P1-P3-P9A — Dell offline readiness crosswalk (pre-merge staging)

**STAGED ON DELL; NOT RELEASED, NOT A HOST PROOF, NOT AN APPROVAL, NOT NATIVE TEST AUTHORITY.**

This bounded preparation cut is intentionally built against protected `main` commit `405d1187c2f53fcfaccf2dcfdb632a4e41d56706`, while Numair's Windows PR #18 and the P9 review-only PR #36 are still unmerged. The current Dell checkout may lag; the worktree script fetches that exact historical branch tip **into a separate local worktree without fast-forwarding Dell `main` or pushing a branch/PR**. When PR #18 passes native Windows CI and is merged, then PR #36 is reconciled and merged, this staging commit must be re-reviewed and deliberately incorporated into a new clean worktree based on the exact combined `main`. The current staging commit is **not** eligible for promotion or native execution.

## Evidence boundaries

- **P6:** full-file snapshots are historical bytes and static ELF hints, not installation provenance or complete loader closure.
- **P7/R4:** the Bubblewrap isolation receipt applies only to the **synthetic Python fixture**, not Claude Code or Codex CLI native processes.
- **P8:** managed, user, project, plugin, and other possible hook sources were classified, not read or independently attested.
- **P9 PR #36:** human native-test approval **request**, not issued authority; its implementation is **not imported** from an unmerged PR. This pre-merge crosswalk can only use the eight named P9 prerequisite families as descriptive review vocabulary.

## Eight unresolved prerequisite families

The `portable_native_readiness_reconciliation` module maps each blocker to one or more of the existing nine native proof gates. Every family remains `BLOCKING_UNVERIFIED` and every native gate remains `UNVERIFIED_NATIVE_EVIDENCE_REQUIRED`.

| Family | What can be reviewed without native launch | What remains unknown |
| --- | --- | --- |
| Fresh executable integrity | Bounded hash/path refresh preparation | Provenance, path stability and exact executable provenance |
| Dependency closure | Static bounded loader/dependency design | Complete runtime closure and trust |
| Native host mounts | Synthetic minimum mount plan and canaries | Actual process enforcement with vetted minimal mounts |
| Effective hook sources | Candidate layer taxonomy | Managed/user/project/plugin effective load and precedence |
| Hook support at exact version | Documentation/version comparison plan | Actual event schema and native dispatch |
| No model/tools | Synthetic negative controls | Actual host nonexecution and zero network/credential access |
| Authenticated one-time authority | Human-controlled nonce and journal specification | Authenticated issued credential and atomic use-once enforcement |
| Bounded lifecycle and receipts | Timer, kill, teardown, receipt design | Actual crash-safe native test lifecycle |

`reconcile(host, unverified_claims=...)` accepts only `NOT_OBSERVED` and `CLAIMED_UNVERIFIED` for the exact eight named families. It refuses any apparent `PASS`, `TRUSTED`, `SIGNED`, `APPROVED`, extra family or modified canonical gate. Review fingerprints are deterministic strings, **not signatures or approvals**.

The only CLI mode is `REVIEW_ONLY`. It reads no host files, initializes no network requests, launches no processes, issues no authorization, installs no hooks, and never promotes Knowledge Forge knowledge. The script's regression suite uses synthetic inputs only. **No P10 work is authorized by compiling, testing, committing, or later merging this plan.**

## Dell local staging procedure

Use the sealed Dell script. It guards exact `main`, original PR statuses, origin, branch, worktree cleanliness, payload checksums and test scope. It may perform a read-only remote fetch and create a single **local-only** isolated development worktree/commit; it does not push or open a PR. Full current Linux regression and `--check-only` must pass before local staging. Script output names the exact worktree and report.

When ordered merges complete, run the separately guarded Dell sync process; then make a **new** post-merge P9A review cut on the merged baseline. Do not reset, force push, blind cherry-pick, or mistake the pre-merge staging commit for release proof.

**Laws:** `VALIDATED != PROMOTED`; `REVIEWED != AUTHENTICATED != AUTHORIZED != EXECUTED`; `KNOWLEDGE != AUTHORITY`.
