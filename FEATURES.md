# Knowledge Forge Features

This file is a capability truth surface: what exists now, what is being hardened next, and which boundaries are product invariants rather than optional polish.

## Implemented now

### Deterministic source identity

- SHA-256 identity for `.md` / `.txt` source bytes
- deterministic normalization into stable source sections
- source anchors survive into compiled knowledge bindings
- source drift is detected before normalization proceeds

### Provider-neutral compilation boundary

- semantic compiler adapter is provider-neutral
- compiler output crosses a typed knowledge boundary before it can become a skill package
- generated knowledge remains `KNOWLEDGE_ONLY`

### Skill package emission

Current package shape:

```text
skills/<slug>/
├── SKILL.md
├── manifest.json
├── SOURCE_RECEIPT.json
└── units/
    └── <unit-id>.md
```

- deterministic package bytes for the same candidate + compile time
- artifact hashes recorded in the source receipt
- no silent overwrite of an existing output directory

### Package/security validation

- exact receipt coverage
- artifact hash verification
- manifest/receipt identity checks
- unsafe path refusal
- invisible-Unicode detection
- instruction-override hygiene scan
- secret-shaped-content detection

These checks prove package integrity and hygiene. They do **not** yet prove semantic faithfulness of every compiled claim.

### Selective consumer proof

- targeted question selects a bounded knowledge unit instead of loading the full source
- proof survives deletion of the original source before the question is asked
- no-match questions refuse rather than greedily widening context
- selected-unit tampering refuses

### Governed lifecycle

Current proven path:

```text
COMPILED_CANDIDATE
        ↓
     VALIDATED
        ↓
      PROMOTED
        ↓
    SUPERSEDED
```

- state skipping is refused
- validation binds the exact package receipt
- promotion requires a decision reference
- lifecycle events form an append-only integrity hash chain
- actor labels are provenance only, never inferred authorization

The hash chain is an internal integrity check, not external tamper proof until anchored or signed outside the lifecycle record.

### Durable registry + version fold-in

- only promoted knowledge becomes current registry truth
- registry re-validates embedded lifecycle proof
- discovery resolves by skill identity/version instead of requiring a filesystem path
- a replacement version must be independently promoted
- the registered current lifecycle must be extended exactly to `SUPERSEDED`
- historical versions remain inspectable
- only one version may be current

## Next semantic-trust feature tranche

### Knowledge identity + compiler profile

Implemented in P2C.1:

```text
source_id            = exact source bytes
knowledge_id         = stable subject/topic/protocol/contract identity
compiler_profile_id  = semantic compilation lineage
skill_id             = governed skill identity
skill_version        = monotonic package version

compiler_digest
compiler_version
model_identity
prompt_template_hash
parameters
```

A compiler change must be visible provenance, not indistinguishable from a source change.

`compiler_profile_id` is derived deterministically from compiler digest/version, model identity, prompt-template hash, and canonical parameters. The governed `skill_id` binds `knowledge_id` to that compiler profile, so a compiler-profile change creates a new skill lineage rather than a quiet version bump.

### Stable claim model

Implemented in P2C.2:

```text
claim_key
subject
predicate
value
qualifiers
scope
valid_from / valid_until / as_of_source
bindings[]
executable: false
```

Stable identity must derive from what the claim is about, not from its prose wording.

`unit_id` is derived from `knowledge_id + claim_key`; title/body/value changes do not silently create a new identity. Every claim carries explicit qualifiers, scope, validity metadata, and `executable: false`. `TEMPORAL_CLAIM` is no longer a special unit kind because validity belongs to every claim; conflict remains structural for P2D.

P2C.2 remains section-bound: `source_bindings` identify supporting source sections. Exact byte/span hashes, qualifier-loss checks, and entailment/faithfulness verdicts are deliberately deferred to P2C.4 and are not claimed here.

### Exact source support + faithfulness

P2C.4A implements exact normalized-source span evidence plus a provider-neutral semantic judgment boundary:

```text
source_id
section_id or stable section hash
start_offset
end_offset
span_hash
```

The forge deterministically verifies exact UTF-8 byte spans plus section/span hashes. A separate `FaithfulnessJudge` evaluates semantic entailment and qualifier preservation; hashes never count as semantic proof. Missing/tampered support, unsupported or uncertain entailment, and qualifier loss produce `FAILED_VALIDATION`.

P2C.4B binds the canonical candidate hash into `manifest.json` and `SOURCE_RECEIPT.json`, requires hash-bound package-validation and faithfulness reports before lifecycle `VALIDATED`, and preserves exact support metadata at the typed candidate boundary. A deterministic recompile planner carries units forward only when every bound section matches uniquely and the compiler profile is unchanged.

### Incremental recompilation

- deterministic section hashes identify which normalized source regions actually changed
- semantic unit payloads bound only to uniquely unchanged sections carry forward byte-identical; provenance bindings are deterministically re-bound when source/section identity moves
- changed or ambiguously matched sections are recompiled; a compiler-profile change also forces recompilation
- diff cause is explicit: source change, compiler change, new source, or scope change

Goal: recompiling unchanged source should not manufacture fake knowledge change.

### Deterministic retrieval + eval

Implemented in P2C.3. Each skill can be evaluated with fixture questions using explicit routing expectations:

```text
question
allowed_unit_ids
forbidden_unit_ids
max_units
must_refuse
```

Retrieval must have:

- deterministic claim index / aliases
- a hard maximum on loaded units
- refusal on miss instead of widening
- answer receipts citing the units and source bindings actually loaded
- `SKILL.md` as routing/index metadata only, not an answer source

P2C.3 emits a hash-bound `retrieval-index.json` from claim metadata only. Unit body/value text is excluded from routing. Retrieval is deterministic, capped at three units per turn, and refuses on a miss rather than broadening context. Consumer receipts record the selected `unit_id`, `claim_key`, and source bindings actually loaded.

### Diff and conflict preservation

Version diff and live disagreement are separate concepts.

P2D-A implements deterministic diffing on stable `claim_key` identity. Semantic comparison uses structured claim content plus unit kind/conflict structure; title/body wording and provenance coordinates do not create fake knowledge changes. `SPLIT` and `MERGED` are accepted only through explicit lineage declarations that bind removed identities to added identities. The forge does not infer split/merge from fuzzy text similarity.

Implemented diff kinds:

```text
ADDED
REMOVED
CHANGED
UNCHANGED
SPLIT
MERGED
```

P2D-B1 implements a provider-neutral `ConflictSet` core over structured claims. Claims with the same subject + predicate and different values are classified as `CONTRADICTION` only when scope, qualifiers, and validity are identical. Differences in those applicability fields are preserved as `VARIANCE` dimensions rather than guessed away. Conflict sets contain immutable claim references and provenance; resolution records applicability without deleting any member or averaging values.

P2D-B2A emits a receipt-bound `claim-index.json` containing structured non-executable claims and source bindings. The manifest binds its schema, digest, and exact claim count; package validation recomputes the artifact digest and verifies exact unit coverage against the manifest.

P2D-B2B stores each version's verified structured claim index in durable registry truth. Only current promoted versions participate in the derived cross-skill scan; historical superseded indexes remain preserved for audit. Registration and version handoff recompute deterministic `ConflictSet` truth without blocking promotion or choosing a winner. Registry validation independently recomputes the open conflict structure from current claims, while explicit resolutions may persist only when the immutable conflict identity is unchanged.

Conflict behavior:

- conflicting claims are never averaged
- scope/qualifier/time variance is distinguished from exact-applicability contradiction
- resolution records why one or more claims apply without deleting the other claims
- cross-skill contradictions are surfaced durably at registry/promotion handoff without blocking promotion

### Incident lifecycle + consumption receipts

P2E-A adds incident lifecycle states after promotion:

```text
PROMOTED
  ├── SUSPENDED
  ├── DISPUTED
  ├── REVOKED
  └── SUPERSEDED

SUSPENDED / DISPUTED
  ├── PROMOTED      # explicit incident resolution
  ├── each other    # escalation / hold
  ├── REVOKED
  └── SUPERSEDED

SUPERSEDED
  └── REVOKED       # historical taint may still be discovered

REVOKED             # terminal
```

`SUSPENDED` and `DISPUTED` require an incident reference plus reason; `REVOKED`
also requires an explicit decision reference. Once an incident is active,
suspend/dispute escalation and revocation preserve that exact `incident_ref`.
Restoration from an incident state must reference the active incident plus a
resolution reference. Durable lifecycle validation replays transition evidence
rules in addition to checking the append-only hash chain, so a rehashed
malformed incident event is refused.

P2E-B carries those incident states into durable registry truth. The registry's
`current_version` remains the latest registered version even when that version
is stopped, but only a `PROMOTED` current entry is usable through default
discovery or participates in active cross-skill conflict truth. `SUSPENDED`,
`DISPUTED`, and `REVOKED` therefore stop current knowledge immediately without
requiring a successor. Explicit-version discovery remains available for audit.

Incident fold-in requires one exact lifecycle event extending the lifecycle
already stored for that current version. Restoration reactivates knowledge only
after lifecycle proof returns it to `PROMOTED`. A terminally revoked current
version may later receive an independently promoted newer version without
rewriting the revoked lifecycle into `SUPERSEDED`; the revoked version remains
durable historical evidence.

P2E-C computes temporal condition from the exact structured claims already
bound into `claim-index.json`. Knowledge Forge does not call the wall clock:
the caller / Time layer supplies an explicit timezone-aware RFC3339 `as_of`.

Claim conditions are:

```text
CURRENT
NOT_YET_VALID   # as_of < valid_from
STALE           # as_of > valid_until
UNEVALUABLE     # malformed or internally reversed temporal bounds
```

`valid_from` and `valid_until` are inclusive boundaries, so a claim is still
current at the exact boundary. Claims without temporal bounds remain current.
`as_of_source` is preserved as provenance but is not guessed into a timestamp.

The package report binds the exact `claim-index.json` SHA and conservatively
reports `STALE` when any unit is stale; otherwise `UNEVALUABLE` wins over
`NOT_YET_VALID`. Only a fully current package is
`temporally_applicable: true`.

Temporal applicability is deliberately not generic usability. Registry and
lifecycle state remain authoritative for consumption: a `SUSPENDED`,
`DISPUTED`, or `REVOKED` package does not become usable merely because its
claims are temporally current. P2E-C computes time truth only; lifecycle history
remains byte-for-byte unchanged and a promoted package does not acquire a
stored `STALE` lifecycle state.

P2E-D adds governed consumption receipts plus a durable consumption ledger.
Each receipt binds an externally identified `ANSWER`, `PLAN`, or `DECISION` to
the exact registered package receipt, claim-index SHA, PROMOTED lifecycle head,
loaded unit ids, structured claim hashes, and source bindings that were
actually consumed.

The recorded lifecycle head is historical evidence: later incident events may
extend the lifecycle, but the receipt remains valid only if its recorded head
is still present in that lifecycle history and that event reached `PROMOTED`.
This allows later `SUSPENDED`, `DISPUTED`, or `REVOKED` state to taint outputs
that previously consumed the package without rewriting either the lifecycle or
the historical consumption receipt.

Explicit Time-layer `as_of` input is combined with P2E-C temporal truth. Unit
and package taint queries distinguish lifecycle incident taint from
`TEMPORAL_STALE`, `TEMPORAL_NOT_YET_VALID`, and `TEMPORAL_UNEVALUABLE`.
A historical output is temporally tainted only by units it actually loaded;
an unrelated stale unit in the same package does not by itself rewrite that
dependency record.

The ledger can therefore answer directly:

> Which answers, plans, or decisions loaded this unit/package and are now
> tainted by suspended, disputed, revoked, or temporally invalid knowledge?

Consumption evidence remains `KNOWLEDGE_ONLY`; it records dependency and never
grants execution authority.

### PDF text extraction

P3A expands the source boundary to text-bearing PDF files while preserving the
same identity law as Markdown and plain text: `source_id` is still derived only
from the exact original PDF bytes. Extraction does not participate in source
identity.

PDF normalization uses the explicit system Poppler `pdftotext` binary in UTF-8
mode. The normalized artifact records the exact extractor name and version so
extractor drift remains visible rather than silently canonical. Each extracted
page becomes one normalized section with a `pdf_page` anchor and the usual
section hash / byte-length evidence.

P3A is deliberately text-only. A PDF with no extractable text refuses instead
of becoming an empty knowledge source. OCR is not silently substituted and
remains the separate optional P3G boundary. PDF extraction does not alter
`KNOWLEDGE_ONLY`, claim identity, lifecycle, registry, memory, or authority.

### DOCX text extraction

P3B adds deterministic text extraction for `.docx` while preserving the same
source identity law: the exact original DOCX bytes define `source_id`; ZIP/XML
extraction does not. The forge reads only `word/document.xml` directly from the
archive and never unpacks the container to disk.

Extraction uses the Python standard library, records a stable extractor name
and version, and preserves visible paragraph order (including table-cell
paragraphs). Word paragraph style ids are preserved only as descriptive
`source_style_id` metadata inside the `docx_paragraph` anchor; they are never
promoted into the canonical semantic `heading` field. Normalized sections stay
tied to original paragraph ordinal. Missing, malformed, oversized, or text-empty
main document parts refuse instead of producing guessed knowledge.

DOCX content remains untrusted `KNOWLEDGE_ONLY` input. Macros, relationships,
embedded objects, and document instructions do not widen authority or activate
tools.

### EPUB spine extraction

P3C adds deterministic `.epub` extraction while preserving byte-owned source
identity. The exact original EPUB bytes define `source_id`; container/package
parsing does not. The forge never unpacks the archive to disk and reads only
local package members declared through `META-INF/container.xml`, the OPF
manifest, and the OPF spine.

Reading order comes only from the package spine. Navigation documents, cover
files, ZIP entry order, and manifest order cannot silently replace spine order.
P3C accepts local `application/xhtml+xml` spine items, requires the exact EPUB
`mimetype` bytes and an OPF package rootfile media type, rejects remote URLs and
parent-directory traversal, bounds archive/member sizes, rejects duplicate ZIP
member names and DTD/entity declarations, and ignores script/style/SVG content.

Normalized EPUB sections use `epub_spine_block` anchors carrying spine index,
manifest item id, document path, linear flag, block index, and source tag.
Markup tags such as `h1`/`h2` remain descriptive `source_tag` metadata and are
never promoted into the canonical semantic `heading` field. EPUB content
remains untrusted `KNOWLEDGE_ONLY` data and cannot widen authority.

### Folder / document-set ingest

P3D adds deterministic recursive folder ingest without collapsing a collection
into one synthetic source. Every supported member keeps its ordinary byte-owned
`source_id`, SourceManifest, media type, and normalization path. A separate
`document_set_id` binds the sorted `(relative_path, source_id)` membership for
collection provenance while excluding ingestion timestamps.

Folder traversal is explicit and fail-closed: members are sorted by POSIX
relative path; symlinks, unsupported regular files, empty sets, excessive
member counts, and excessive aggregate bytes refuse rather than being silently
ignored. Each member manifest is independently checked for exact schema shape,
basename, suffix-derived media type, source-id/digest consistency, byte-length
type, and document-set origin before parsing. Normalization then rechecks exact
membership and each member's source bytes, so added/removed/changed files or
parser-selection metadata tamper cannot slip through an old set manifest.

The compiler boundary now supports multiple normalized sources. Semantic compile
requests are intentionally path-insensitive and bind only immutable source ids
plus normalized sections. Exact duplicate byte-identical sources may exist as
separate collection members but compile once when their normalized sections
agree; the same source id with conflicting normalized content refuses. Folder
paths and `document_set_id` never become semantic truth or execution authority.


### Repository / technical-document parser

P3E reads immutable local-Git commit objects rather than the mutable working
tree. Dirty edits and untracked files cannot leak into a repository snapshot.
Each supported tracked blob keeps byte-owned `source_id`; the repository
manifest separately binds commit id, Git blob id, file mode, relative path,
explicit exclusions, and deterministic `repository_snapshot_id`.

Supported tracked Markdown/TXT/PDF/DOCX/EPUB and technical-text files are
included. Tracked symlinks, gitlinks/non-blobs, and unsupported file types are
recorded as explicit exclusions. Historical pinned snapshots can still normalize
after `HEAD` moves when their Git objects remain available.

Technical text covers common code/config suffixes, requires UTF-8, is size
bounded, and normalizes into deterministic `technical_line_range` blocks with
descriptive `source_language`. Code, comments, configuration, filenames, Git
metadata, executable file modes, and repository instructions remain untrusted
knowledge data and never become execution authority.

Semantic compile requests remain path/commit-insensitive: they receive immutable
source ids plus normalized sections only. Repository paths, commit/blob ids,
snapshot ids, source instructions, and executable bits cannot widen tools,
writes, signing, deployment, spend, or other authority.


### Synthetic PondReference contract-shaped pilot

P3F adds one bounded contract-knowledge pilot using the original synthetic
PondReference runtime on a non-production chain identity. The pin binds its
local deterministic build recipe, non-proxy contract identity, and exact ABI
and runtime commitments. The fixture is not deployed and is not a production
token.

The ABI may describe state-changing functions because it is contract evidence,
but executable read plans are a separate allowlist containing only view getters.
`HttpReadonlyEvmProvider` exposes only `eth_chainId`, `eth_getCode`, and
`eth_call`; it has no signing, transaction, deployment, or write surface.

Live applicability checks chain id, code presence, exact runtime bytecode, and
the identity tuple `Pond Reference` / `POND` / `18`. Contract knowledge stays
`KNOWLEDGE_ONLY` and never widens authority.

### Explicit optional OCR boundary

P3G adds OCR as a separate opt-in normalization path rather than a fallback in
ordinary PDF ingestion. `normalize_source()` remains text-only for PDFs and still
refuses image-only/scanned input. A caller must deliberately select
`normalize_pdf_with_ocr(...)` to cross the OCR boundary.

The OCR path uses external Poppler `pdftoppm` rendering plus Tesseract. Exact
tool versions, render DPI, OCR language, page segmentation mode, and the original
byte-owned PDF `source_id` are preserved. OCR text is marked
`OCR_DERIVED_UNVERIFIED`, and every section uses a `pdf_ocr_page` anchor so OCR
output cannot masquerade as embedded PDF text.

The optional path fails closed when binaries are absent, source bytes drift,
media type is not PDF, render pages are discontinuous, tool output is invalid
UTF-8, or OCR returns no nonblank text. No shell command construction is used,
and the OCR boundary grants no execution/tool authority.

### Agent0 governed-knowledge discovery adapter

P4A binds the first runtime consumer to the real Agent0 in `ToadAid/trading-desk`. Agent0 already has an operator-authored procedural `skill` playbook tool; Knowledge Forge does **not** reuse that door. Procedural playbooks and governed knowledge remain separate runtime surfaces.

`build_agent0_discovery_catalog(...)` projects only current `PROMOTED` registry truth into a compact discovery catalog. `SUSPENDED`, `DISPUTED`, and `REVOKED` current skills remain auditable in registry history but are absent from Agent0 default discovery. The catalog carries skill/package/lifecycle/source identity and claim routing metadata, but no claim values, unit bodies, source receipts, or lifecycle bodies.

`route_agent0_knowledge(...)` routes from identity metadata only, refuses on a miss, and hard-caps discovery at three skill identities per query. P4A does not load knowledge-unit content; P4C owns the load-time trust wall. Every Agent0 discovery projection is `KNOWLEDGE_ONLY`, declares execution authority `NONE`, and returns an empty tool-grant set.

### General consumer discovery core + Agent1 adapter

P4B extracts P4A's proven discovery mechanics into a provider-neutral `consumer_discovery` core so Knowledge Forge is not coupled to Agent0 or Agent1. A consumer binding names its runtime identity and integration boundary, while the core keeps the invariant contract fixed: current `PROMOTED` knowledge only, bounded metadata-only routing, `KNOWLEDGE_ONLY`, execution authority `NONE`, and an empty tool-grant set. The global routing cap remains three skill identities per query and cannot be widened by a consumer adapter.

The Agent0 public adapter remains backward-compatible but now delegates its registry projection and routing mechanics to the shared core. Agent1 is the second real consumer proof, bound to `ToadAid/frog-to-toad` at `agent1/runtime.mjs`. Its discovery surface is external to the current Stage-0 tool roster and does not materialize knowledge into `agent1/skills`, write Agent1 journal/memory, or load unit bodies. P4C remains responsible for load-time package/lifecycle verification before knowledge content enters reasoning.

### General load-time trust wall

P4C joins exact package validation, current registry/lifecycle truth, and selective retrieval at the runtime boundary. A consumer may discover a skill identity through P4A/P4B, but unit bodies do not enter reasoning until the trust wall has snapshotted the package, verified all receipted artifact hashes and package schemas, revalidated the governed registry, matched the exact source receipt and claim index to the current registry entry, and revalidated the current `PROMOTED` lifecycle head.

The wall verifies the whole package but places only routed unit bodies into the model-facing `reasoning_context`. Every loaded unit is explicitly stamped `UNTRUSTED_KNOWLEDGE_DATA`, `instruction_authority: NONE`, `authority: KNOWLEDGE_ONLY`, and `executable: false`. Load evidence binds the exact package receipt, claim-index digest, lifecycle head, question digest, selected unit ids, claim digests, and source bindings for later consumption receipts.

Tool authority is monotonic. Runtime tools exist before skill loading. A skill-origin tool constraint may leave that ceiling unchanged or narrow it by set intersection; requested tools outside the pre-existing runtime set are denied and recorded. Unit prose is never parsed as authority, so tool names appearing in knowledge content cannot activate capabilities.

### Memory boundary proof

P4D makes the doctrine `KNOWLEDGE != MEMORY` executable at runtime. A verified P4C load remains `GOVERNED_SKILL_KNOWLEDGE`; it may cross out of the reasoning boundary only as a provenance-only `KNOWLEDGE_REFERENCE`. That reference carries package receipt, claim-index, lifecycle-head, question, unit, claim-digest, and source-binding evidence but deliberately contains no skill body or reasoning-context content and has `memory_mutation_authority: NONE`.

Skill-origin knowledge is fail-closed from `AUTOBIOGRAPHICAL_MEMORY`, `USER_STATED_MEMORY`, and `LIVED_EVIDENCE`. The boundary does not provide a positive memory-write authority for any other origin; actual memory systems keep their own evidence and mutation policy. A forged trust class, executable unit, or reference that claims autobiographical/user/lived evidence is invalid.

### Temporal runtime integration

P4E composes the load-time trust wall with the existing deterministic Time layer. Runtime callers supply an explicit timezone-aware `world_as_of`; selected claim validity is evaluated against that trusted world clock rather than the agent's local clock. Optional `agent_as_of` is diagnostic only: drift is recorded as `MATCH`, `AGENT_BEHIND`, or `AGENT_AHEAD`, but it never changes claim applicability.

Only units actually selected by P4C are evaluated for current use. `STALE`, `NOT_YET_VALID`, and `UNEVALUABLE` selected claims fail closed before a current-use reasoning context is returned. Exact validity boundaries preserve the P2E-C rule that `valid_until` is still current at the exact timestamp and becomes stale only after it.

P4E also accepts optional source re-verification evidence bound by `source_id`. `UNCHANGED` evidence is preserved with its check time; `CHANGED` or `UNAVAILABLE` blocks current use. Missing source re-verification remains explicitly `NOT_REVERIFIED` and is never misrepresented as verification. A source check from after the trusted world clock is refused.

### Procedure boundary proof

P4F makes `PROCEDURE` explicitly descriptive at runtime. A loaded procedure remains `UNTRUSTED_KNOWLEDGE_DATA`, `KNOWLEDGE_ONLY`, and `executable: false`; its `execution_authority` is always `NONE`. Procedure projection emits empty `tool_grants` and empty `capability_grants` even when the procedure body names commands, tools, wallet actions, deploy steps, or repository writes.

The runtime's pre-existing effective tool ceiling is reported separately and is never reclassified as a grant from knowledge. Requests to treat a procedure as authority for `SPEND`, `SIGN`, `DEPLOY`, `PUSH`, or `WRITE` are deterministically denied with `PROCEDURE_KNOWLEDGE_HAS_ZERO_EXECUTION_AUTHORITY`. Any real action must come from a separate external typed runtime authority path.

### Contract runtime applicability / code-hash proof

P4G binds implementation-specific knowledge to the exact 255-byte synthetic
PondReference runtime generated by
`scripts/generate_pond_reference_fixture.py`. The checked-in ABI, pin, and
runtime regenerate byte for byte from that standard-library-only recipe.

Live applicability now checks chain, code presence, and exact runtime code commitment before identity reads. A mismatch yields `CODE_HASH_MISMATCH`, marks implementation-bound knowledge `SUSPENDED` for that runtime observation, and performs no identity reads. This suspension is computed applicability state and does not mutate the governed lifecycle automatically.

`execute_read_plan` independently enforces the same code commitment before any `eth_call`, so callers cannot bypass applicability by skipping the preflight helper. The surface remains read-only and `KNOWLEDGE_ONLY`; no sign/send/deploy/write authority is introduced.

### Repository-local operator CLI

P5A exposes a read-only operator surface over the governed core. `bin/knowledge-forge` launches `python -m knowledge_forge` against the repository `src/` tree and emits deterministic JSON for successful commands; fail-closed domain errors are machine-readable JSON on stderr with exit code 2.

The first commands are `validate-package`, `registry-inspect`, `discover`, and `load-current`. They reuse package validation, durable registry validation, provider-neutral discovery, the P4C trust wall, and P4E temporal truth rather than reimplementing those rules in the CLI. `discover` remains identity/routing metadata only. `load-current` requires an explicit trusted `--world-as-of` and preserves `KNOWLEDGE_ONLY` / zero instruction authority.

P5A deliberately exposes no compile promotion, incident mutation, signing, spending, deployment, repository push, contract write, or other authority-bearing operation. Those remain outside this read-only CLI until later governed workflow productization.

### ToadAid desktop projection

P5B establishes the first Knowledge Forge surface inside `ToadAid/toadaid-app`.
The Pond view is a fixture-bound projection tied to exact Knowledge Forge P5A
commit `41a15164f091f63e9a4d3b06c5ac4e43c9d4755b` / tree `53bc0ef4a92dc65fe83cd26c28df0ee4a0152bb1` and canonical architecture ref
`bc7a971dfb243f0aa4417da6cef85cc56204f783`. It exposes the P5A operator command shape and the runtime trust
laws without invoking the Forge runtime.

The surface is explicitly `KNOWLEDGE_ONLY`, `instruction_authority: NONE`,
`execution_authority: NONE`, `runtimeConnection: NOT_INCLUDED`,
`hostIntegration: NOT_INCLUDED`, `mutation: NONE`, and
`activation: NOT_INCLUDED`. It introduces no Tauri command, shell execution,
Bridge/MCP connection, network request, registry mutation, or authority-bearing
action. P5C may build the skill library browser on this bounded presentation
seam.

### Skill library browser

P5C adds a metadata-only Skill Library browser to the Knowledge Forge desktop
surface in `ToadAid/toadaid-app`. The browser fixture is bound to canonical
Knowledge Forge P5B commit `caa4bdf3b4fbd95a9d8f0a2686798cc9e1a6f0ad` / tree `1ef8fe116e65bd0ac4eb832451d195831c2c396e` and Pond P5B
commit `d0c10d0a22054837fa583381132c60be0cef7f15` / tree `ac808f62f0272654741cb60fd10f9b933e8057c1`.

The browser supports deterministic client-side search plus lifecycle-state and
unit-kind filters over representative registry-shaped skill metadata. It shows
skill identity, title, current version/state, knowledge identity, authority,
unit kinds, unit/source/conflict counts, and temporal condition. It deliberately
contains no claim bodies and no source bodies; deeper source/version/provenance/
faithfulness inspection remains P5D.

Stopped-state knowledge may remain visible for operator inspection, but library
visibility is not agent discovery, load eligibility, execution authority, or
activation. P5C remains fixture-bound with `KNOWLEDGE_ONLY`, execution authority
`NONE`, runtime connection `NOT_INCLUDED`, mutation `NONE`, and activation
`NOT_INCLUDED`.

### Evidence inspector

P5D adds a source/version/provenance/faithfulness inspector to the existing
Knowledge Forge Skill Library surface in `ToadAid/toadaid-app`. The inspector is
fixture-bound to canonical Knowledge Forge P5C commit `d4cf038eb188975e9ac4a5d875c15998e366a8ee` / tree
`60e7b36b61c4ac9617c2ef1df0a787b56e7eef0f` and Pond P5C commit `3117bb66c7ab88f6e1abcac6e0101ff07fe68506` / tree `12903cb30a0184c3e253752fb945ef88b8b155c5`.

The inspector follows the skill selected by P5C and presents four bounded
evidence classes: exact source identities and byte commitments, version lineage,
compiler/package provenance commitments, and faithfulness validation verdicts.
Faithfulness keeps exact-span verification distinct from semantic entailment and
binds the displayed validation to candidate and unit/claim digests.

P5D deliberately does not expose raw source bodies, claim bodies, or exact
support text. It remains a fixture projection rather than live registry truth,
with `KNOWLEDGE_ONLY`, execution authority `NONE`, runtime connection
`NOT_INCLUDED`, mutation `NONE`, and activation `NOT_INCLUDED`. Selection
coordination between P5C and P5D is presentation-only and grants no authority.

### Governed workflow console

P5E closes Stage 5 with an interactive workflow console on the Knowledge Forge
desktop surface in `ToadAid/toadaid-app`. The console is fixture-bound to
canonical Knowledge Forge P5D commit `368077ee22b692f6879659d47b276f5edda5c113` / tree `d346931179979e34fe9a37b2d85aa0276ca04c2c` and
Pond P5D commit `8de15239a150f5433d3b8c41f4a4c0dcfa66d8c4` / tree `f513da15a0c9f56d47a3137acd3ccc841f8da8b3`.

The console models the existing Forge lifecycle contract for compile, validate,
promote, dispute, and revoke. It exposes allowed source states, target state,
required evidence, selected skill identity, missing-evidence diagnostics, and a
deterministic local workflow-preview packet suitable for external handoff.

P5E does not execute compilation, validation, promotion, dispute, or revocation.
Knowledge Forge lifecycle actor identifiers and decision references remain
provenance labels; authorization is external. The UI therefore carries
`KNOWLEDGE_ONLY`, instruction and execution authority `NONE`, lifecycle
authority `EXTERNAL_REQUIRED_NOT_ESTABLISHED`, runtime/host integration
`NOT_INCLUDED`, mutation and persistence `NONE`, and activation `NOT_INCLUDED`.
No registry write, lifecycle transition, Tauri/Bridge/MCP call, network request,
signing, spend, deployment, push, or other authority-bearing action is exposed.

## Product invariants

### Knowledge is not authority

```text
KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY
```

A skill may describe what an operator typically does. It never grants permission to spend, sign, deploy, push, merge, message, mutate secrets, or widen tools.

### Canon is not a skill

```text
CANON != SKILL
```

Constitution, doctrine, witness law, and authority policy must not enter ordinary Knowledge Forge compilation/versioning where a later source could supersede them.

### Procedures are descriptive

Procedure knowledge remains:

```text
executable: false
```

and should be rendered descriptively rather than as privileged imperative instructions.

### Origin survives reasoning

A model must be able to distinguish:

```text
I observed X     # evidence / memory
I was taught X   # skill knowledge
```

Skill-origin statements must never become autobiographical memory or manufactured evidence.

### Time owns “now”

Skills carry validity metadata and read specifications. The Time/live-evidence layer decides whether a claim applies now.

### Mutable contract facts stay live

A contract skill may know:

- function selectors and arguments
- roles and authorization model
- upgrade structure
- how to read a fee/owner/role/paused state

It must not promote mutable live values as timeless truth. Mutable facts become read plans, and runtime/implementation code-hash applicability must be checked before use.

## Source families

```text
BOOK / DOCUMENT
REPOSITORY / TECHNICAL DOCS
API
CONTRACT / PROTOCOL
RESEARCH PACK
```

The contract lane is implemented as one bounded pilot after the generic
faithfulness/retrieval gates. API and research-pack adapters remain prospective,
not implied by the completed roadmap.

## Deliberately deferred

- vector database as mandatory infrastructure
- mandatory embeddings
- graph database
- autonomous crawling
- broad OCR stack
- marketplace / skill NFTs / token economics
- auto-promotion
- agent-authored promoted skills from memory
- cross-skill synthesis into new canonical knowledge
- universal protocol ontology
- multi-model consensus compilation
- live-chain writes from skill consumers
- on-chain skill identity before off-chain load-time verification is proven
