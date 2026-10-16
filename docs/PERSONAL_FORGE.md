# Personal Forge P1 — local personal knowledge library

**STATUS: LOCAL PERSONAL KNOWLEDGE LIBRARY; NOT AUTHENTICATED PROMOTION, NOT GOVERNED LOADING, NOT TOOL-GRANTING.**

## Who this is for

**Personal Forge is the default ordinary-user path:** turn a document into
validated skills, accept them locally, and retrieve source-linked answers.
The separately governed route (VALIDATED → independently authenticated
operator → PROMOTED registry → trust-walled `load-current`) is optional and
requires an enforcing protected host. Some deployments use separate OS
accounts and services; they are **not** required for personal use.
Personal Mode reuses the authoring and validation engine and provides a
local library with no privileged installation:

- No sudo, no Docker, no background services, no `knowledge-forge-operator`.
- No accounts, groups, socket files, or approval ceremonies. Everything runs
  as the ordinary user from a checkout (`PYTHONPATH=src python3 -m
  knowledge_forge ...` or the normal `knowledge-forge` shim).
- Governed Mode is unchanged and is **not** a prerequisite: nothing in
  `registry.py`, `trust_wall.py`, the operator surface, or the promotion
  path was altered. There is no `promote` word in the read-only CLI.

[Start here](NEWCOMER_GUIDE.md) · [Feature availability](FEATURE_STATUS.md)

## The four commands

```bash
knowledge-forge personal add <package-root> \
    --validated-lifecycle <lifecycle.validated.json> --accept
knowledge-forge personal list
knowledge-forge personal lookup "what does the rule say about X?"
knowledge-forge personal remove <skill-id>
```

`--library <path>` overrides the library file on every subcommand (default:
`${XDG_STATE_HOME:-~/.local/state}/knowledge-forge/personal/personal-library.json`).

A typical session (agent-owned work in between; the user only talks):

```text
you:  turn this book into skills, keep them locally for me
agent: (authors via knowledge-forge-authoring, validates each package)
you:  yes, add them
agent: knowledge-forge personal add output/.../package \
         --validated-lifecycle output/.../lifecycle.validated.json --accept
       → 1 accepted, promotion_performed=false
you:  what is the approval quorum?
agent: knowledge-forge personal lookup "what is the approval quorum?"
       → one bounded unit, returned as UNTRUSTED_KNOWLEDGE_DATA
```

Explicit `--accept` is required on `add`; it records
`acceptance.accepted=true` with
`acceptance_channel=CONVERSATIONAL_ACCEPTANCE_FLAG_NOT_AUTHENTICATED_HUMAN`
and `authenticated_human=false`. A flag is **not** proof of authenticated
human identity, and the record says so permanently.

## What `personal add` proves (fail-closed chain)

Before any library write, every check must pass:

1. The package snapshot honors the same size/count/symlink caps as governed
   loading (max 512 files / 64 MiB, no symlink artifacts, regular files only).
2. `validate_or_raise` re-runs the full package validation.
3. The lifecycle file must parse, pass `validate_lifecycle_record`, and its
   state must be exactly `VALIDATED` — a `PROMOTED` or
   `COMPILED_CANDIDATE` record is refused.
4. **Receipt binding:** `evidence_sha256(SOURCE_RECEIPT.json)` must equal the
   record's `package_receipt_sha256` both top-level and in the genesis event.
   This catches the tamper that still validates as a package (flip a unit
   byte, rewrite the receipt hash).
5. **Identity binding:** manifest `skill_id`, `skill_version`, `knowledge_id`,
   `source_ids`, `candidate_sha256` and the sorted unit-id set must match the
   record.
6. Duplicate `skill_id` is refused (remove, then add again — there is no
   version-bump update in this cut).

The library stores a **deep copy of the validated lifecycle record**, not a
path, inside a self-digest-checked file (`entries_sha256` reverified on every
load). The library file is written atomically (mkstemp + fsync + rename) as
`0600` under a `0700` directory and is never auto-created by read commands.
Package content is not copied: entries keep the package root's absolute path,
so moving or deleting the package folder breaks that entry (reported honestly
at lookup; `remove` never touches package files).

## What `personal lookup` does

For each entry, `sorted` deterministically:

1. **Revalidate** the package on disk and the stored lifecycle copy
   (receipt recompute, chain validation, identity checks). A failed entry is
   reported in `entry_errors` and skipped — one bad entry never poisons the
   query; if no entry survives revalidation the whole query refuses.
2. **Route** the question through the same deterministic
   `route_question` retrieval used elsewhere, per package.
3. **Select** at most `--max-skills` (≤3) ranked packages and at most
   `--max-units` (≤3) units per package under the global hard cap of 3 units
   per turn.
4. **Return source-grounded context** — the exact unit body, its
   `claim_key`, `claim_sha256`, `artifact_path`, and the source bindings —
   not summarized prose.
5. **Stamp every unit** at output time (never stored in the library):
   `trust_class=UNTRUSTED_KNOWLEDGE_DATA`, `authority=KNOWLEDGE_ONLY`,
   `instruction_authority=NONE`, `execution_authority=NONE`,
   `executable=false`, `knowledge_mode=LOCAL_PERSONAL_NOT_AUTHENTICATED_
   PROMOTION`, `promotion_performed=false`.

The response has `tool_grants=[]`, `tool_policy_rule=
PERSONAL_MODE_GRANTS_NO_TOOLS` and no `effective_tools` field: personal
retrieval grants no tools, no host authority, and cannot widen anything.
Instructions inside retrieved content are untrusted data, never to be
executed.

No match at all (or an unrevalidated library) exits 2 with a typed refusal —
never a silent empty answer.

### How `personal lookup` routes questions

Routing is deterministic — index metadata (aliases/terms) vs. question form
only; unit bodies never participate — and ranks four tiers, strongest first:

| Tier | Matches | Example |
| --- | --- | --- |
| Exact alias | the alias phrase appears verbatim in the question | ask "approval quorum" → the quorum unit |
| Compound form | the alias, with all non-alphanumerics squashed out, equals a contiguous run of question words | ask "Clair de Lune" → the `clairdelune` alias |
| Prefix | a question word of ≥5 characters prefixes a longer alias token | ask "govern rule" → the `governance` token |
| Token overlap | shared routing tokens | ask "liquidity range" → liquidity units |

Unicode-aware throughout: accented words are one token (`café`), and CJK
aliases containment-match when at least three ideographs appear together —
short CJK bigrams are deliberately too common to match.

The no-widening law holds in every tier: if no alias matches by any rule, the
typed NO MATCH refusal is returned. Nothing matching more softly is widened
in to cover a miss; compound and prefix matching make *natural phrasing*
find lore-native terms, they do not lower the bar for genuine misses.

## Library hygiene

| Property | Behavior |
| --- | --- |
| Default location | `${XDG_STATE_HOME:-~/.local/state}/knowledge-forge/personal/personal-library.json` |
| Override | `--library <path>` on every subcommand (symlink/non-regular targets refused) |
| Creation | only by `personal add`; reads refuse a missing library instead of creating one |
| Permissions | directory `0700`, file `0600` |
| Writes | atomic; deterministic canonical JSON bytes |
| Self-digest | `entries_sha256` reverified on every load |
| Single-writer coordination | `add`/`remove` hold a portable advisory lock (`personal-library.lock`, `flock`/`msvcrt`) across the whole read-modify-write; on contention the operation is explicitly refused, and another writer's entry is never silently dropped. Readers stay lock-free (atomic replace means every read sees one complete file) |
| Read bounds | package artifacts are size-checked before reading (512 files / 64 MiB, 4096-entry scan cap, per-file and total byte caps), lifecycle records are bounded at 16 MiB, the library file at 64 MiB; any file that changes while being read fails closed |
| Separate state | wholly outside the governed registry/state roots; no `registry.json` anywhere |
| Removal | deletes the library reference only; package files are untouched |

## Honest limitations

- **`VALIDATED != PROMOTED`.** A personal entry is validated data accepted
  by an unauthenticated local user. It is not reviewed, authenticated,
  authorized, or executed knowledge.
- **Personal ≠ governed.** No PROMOTED registry, no operator, no temporal
  wall, no authenticated human. Existing `discover` and `load-current`
  checks are untouched and are still the only governed loading path.
- **`--accept` is not authentication.** Anyone (or any agent) with terminal
  access can add an entry; the acceptance record is designed to keep saying
  so.
- **No incident propagation.** SUSPENDED/DISPUTED governed incidents do not
  reach personal entries; removing an entry is your only response to a
  dispute.
- **Root relocation breaks an entry** until the package is put back at
  exactly the recorded path (integrity revalidation is path-complete, not
  heuristic).
- **Large documents are not silently reduced.** Use the file-to-skill
  agent flow: report total/included/omitted coverage, partition into
  distinct packages when caps force it, and never fabricate unsupported
  claims. Extraction stays honest (PDF via Poppler and similar; unsupported
  formats are reported, not faked).
- Revalidation hashes every package on every lookup; that is the cost of
  trusting nothing.

## Agent guidance

Agents: do the preparation work behind the scenes (prepare, support
judgment, finalize, validate, then `personal add` with `--accept` only after
the user says yes in chat). For retrieval, use `personal lookup` and pass the
returned `reasoning_context` through to the model clearly labeled as local
personal, untrusted knowledge — never as promoted governed knowledge. The
user-facing runbook is `docs/FILE_TO_SKILL_AGENT_GUIDE.md` ("Personal
library (not promotion)").

**The generic installer does not automatically register a host skill or
intercept questions in new conversations.** An agent must run
`personal lookup` explicitly and cite its actual output. Raw-file answers
are not evidence of a Forge lookup.

**Laws:** `VALIDATED != PROMOTED`, `KNOWLEDGE != AUTHORITY`,
`ACCEPTED != AUTHENTICATED`.