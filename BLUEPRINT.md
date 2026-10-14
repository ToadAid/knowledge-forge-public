# Knowledge Forge Blueprint

**Stage:** 0  
**Goal:** Build a source-to-skill compiler for ToadAid agents with provenance, versioning, governed promotion, and selective runtime loading.

## 1. System shape

```text
                         SOURCE MATERIAL
                              |
       -------------------------------------------------
       |            |           |          |           |
      PDF          DOCX        EPUB       MD/TXT      DOC SET
       |            |           |          |           |
       -------------------- INGEST ----------------------
                              |
                              v
                    SOURCE IDENTITY LAYER
                 hash / type / name / timestamps
                              |
                              v
                   DETERMINISTIC EXTRACTION
                 text / structure / section anchors
                              |
                              v
                    NORMALIZED SOURCE PACKET
                              |
                              v
                      SEMANTIC COMPILER
        concepts / frameworks / procedures / rules / conflicts
                              |
                              v
                    SKILL CANDIDATE PACKAGE
            SKILL.md + units + manifest + provenance
                              |
                              v
                        VALIDATION LAYER
              schema / security / anchors / budgets
                              |
                              v
                     GOVERNED PROMOTION
                              |
                              v
                       SKILL REGISTRY
                              |
                              v
                     RUNTIME DISCOVERY
                              |
                              v
                  SELECTIVE KNOWLEDGE LOAD
                              |
                              v
                         AGENT REASONING
```

## 2. Boundary with agent cognition

```text
                         AGENT
                           |
          -------------------------------------
          |                 |                 |
        MEMORY             TIME             SKILLS
     lived history     temporal truth    learned knowledge
          |                 |                 |
          -------------------------------------
                           |
                    reasoning assembly
```

Rules:

- Knowledge Forge writes skills, not autobiographical memory.
- Temporal metadata travels with source or skill truth where relevant.
- Runtime reasoning may combine all three.
- No one subsystem gains mutation authority over another by implication.

## 3. Proposed repository layout

```text
knowledge-forge/
├── README.md
├── DOCTRINE.md
├── BLUEPRINT.md
├── BUILD_LIST.md
├── src/
│   ├── ingest/
│   ├── extract/
│   ├── normalize/
│   ├── compile/
│   ├── validate/
│   ├── promote/
│   ├── registry/
│   └── runtime/
├── schemas/
│   ├── source-manifest.schema.json
│   ├── normalized-source.schema.json
│   ├── skill-manifest.schema.json
│   └── provenance-receipt.schema.json
├── tests/
│   ├── fixtures/
│   └── contracts/
├── examples/
└── docs/
```

The folders are architectural targets; Stage 0 need not create empty directories solely for appearance.

## 4. Core contracts

### 4.1 SourceManifest

Purpose: stable identity of one input source.

Candidate fields:

```json
{
  "schema_version": "0.1",
  "source_id": "sha256:<hex>",
  "name": "example.pdf",
  "media_type": "application/pdf",
  "byte_sha256": "<hex>",
  "ingested_at": "RFC3339",
  "published_at": null,
  "license_hint": null,
  "origin": {
    "kind": "local_file",
    "locator": "redacted-or-relative"
  }
}
```

### 4.2 NormalizedSource

Purpose: deterministic output of extraction.

Candidate fields:

```json
{
  "source_id": "sha256:<hex>",
  "extractor": {
    "name": "knowledge-forge",
    "version": "0.1.0"
  },
  "sections": [
    {
      "section_id": "s001",
      "heading": "Introduction",
      "ordinal": 1,
      "text": "...",
      "anchor": {
        "kind": "page-or-structural-anchor",
        "value": "..."
      }
    }
  ]
}
```

### 4.3 SkillManifest

Purpose: identity and lifecycle of a compiled skill.

Candidate fields:

```json
{
  "schema_version": "0.1",
  "skill_id": "knowledge-forge:<slug>",
  "skill_version": 1,
  "state": "COMPILED_CANDIDATE",
  "title": "Example Skill",
  "source_ids": ["sha256:<hex>"],
  "compiler": {
    "name": "knowledge-forge",
    "version": "0.1.0",
    "model_adapter": "provider-neutral-id"
  },
  "compiled_at": "RFC3339",
  "supersedes": null
}
```

### 4.4 ProvenanceReceipt

Purpose: prove what produced the artifact.

Candidate fields:

```json
{
  "skill_id": "knowledge-forge:<slug>",
  "skill_version": 1,
  "artifact_hashes": {
    "SKILL.md": "sha256:<hex>"
  },
  "source_bindings": [
    {
      "source_id": "sha256:<hex>",
      "anchors_used": ["s001", "s004"]
    }
  ],
  "created_at": "RFC3339"
}
```

## 5. Skill package

Initial target:

```text
skills/<slug>/
├── SKILL.md
├── manifest.json
├── SOURCE_RECEIPT.json
├── chapters/
│   └── *.md
├── glossary.md
├── patterns.md
└── cheatsheet.md
```

`SKILL.md` should contain:

- purpose
- when to use the skill
- core mental models
- retrieval/index map
- high-value decision rules
- references to detailed units
- explicit non-authority statement

It should not attempt to contain the whole source.

## 6. Compilation outputs

The compiler should be able to emit typed knowledge units such as:

```text
CONCEPT
FRAMEWORK
PROCEDURE
DECISION_RULE
TRADEOFF
ANTI_PATTERN
DEFINITION
EXAMPLE
CONFLICT
TEMPORAL_CLAIM
```

Each unit should support source anchors.

Later versions may assign confidence or validation state, but Stage 1 should avoid pseudo-precision.

## 7. Security boundary

Threat model includes:

- prompt injection embedded in source text
- invisible Unicode
- malicious filenames or paths
- parser exploits
- malformed documents
- poisoned metadata
- generated skill instructions that attempt authority escalation
- accidental secret inclusion
- untrusted remote documents
- provenance loss during fold-in or update

Minimum controls:

- normalize and sanitize extracted text
- never execute source-provided instructions
- safe parser configuration
- absolute and validated subprocess paths
- no shell construction from source names
- generated artifact scanner
- schema validation
- artifact and source hashes
- secret-pattern scan before promotion

## 8. Update and fold-in model

```text
skill v1
   |
new source or changed source
   |
   v
compile candidate v2
   |
   +---- diff knowledge units
   +---- record added/changed/removed source anchors
   +---- surface conflicts
   |
validation
   |
promotion
   |
skill v2 ----supersedes----> v1
```

No in-place invisible mutation.

## 9. Runtime retrieval

Proposed sequence:

```text
question
  |
skill discovery
  |
load SKILL.md/index
  |
select relevant units
  |
load referenced detail files
  |
optionally verify source anchors
  |
assemble bounded reasoning context
```

The runtime should record enough diagnostics to measure whether selective loading is actually saving context.

## 10. Integration targets

Long-term targets:

- Agent0 / trading-desk agent
- Agent1 successor agent
- frog-to-toad-agent public runtime
- ToadAid desktop
- provider-neutral specialist agents
- cross-agent `SKILL.md` compatibility where useful

Integration must happen through explicit adapters, not hidden coupling.

## 11. First vertical slice

The first executable build should remain intentionally narrow:

```text
INPUT:
  one .md or .txt source

DO:
  hash
  -> ingest manifest
  -> deterministic section extraction
  -> compile typed knowledge candidate
  -> emit SKILL.md
  -> emit manifest.json
  -> emit SOURCE_RECEIPT.json
  -> validate all artifacts

OUTPUT:
  one local skill candidate directory
```

Do not start with PDF/OCR, UI, vector databases, or multi-provider orchestration.

Prove the lifecycle first.

## 12. Definition of Stage 1 success

Stage 1 is real when a test source can be compiled and a separate consumer can answer a targeted question by loading the generated skill rather than the entire source, while preserving exact source identity and without writing into agent memory.

That is the first useful cut.
