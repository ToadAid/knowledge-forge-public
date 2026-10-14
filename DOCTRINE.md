# Knowledge Forge Doctrine

**Stage:** 0  
**Binding intent:** Architecture and implementation decisions should preserve these laws unless an explicit later design change replaces them.

## 1. Capability is the product

Ceremony is not the product.

Receipts, provenance, schemas, tests, review gates, and promotion rules exist to make real capability safer and more trustworthy. They must not become a substitute for building the extractor, compiler, loader, and retrieval path.

Each stage should end in a capability that can actually be exercised.

## 2. Knowledge is not context

Large source material must not become permanent prompt baggage.

The system should compile durable structure once and load only the smallest relevant knowledge unit at runtime.

The target is not “summarize the book.” The target is to identify concepts, preserve named frameworks, derive procedures, identify decision rules, surface trade-offs, capture anti-patterns, preserve source anchors, and make the result selectively retrievable.

## 3. Knowledge is not memory

A generated skill must never silently write itself into autobiographical or user memory.

Memory answers:

> What happened? What did the user ask? What did the agent experience?

A skill answers:

> What external knowledge can the agent apply?

An agent may use both during reasoning, but the storage classes and mutation authorities remain separate.

## 4. Time is a separate truth dimension

A skill may contain claims whose validity depends on time.

Every compiled artifact should be capable of carrying temporal metadata where relevant: source publication date, source retrieval or ingest time, validity interval if known, compile time, and supersession state.

Temporal metadata must not be fabricated when the source does not provide it.

## 5. Source provenance survives compilation

Every promoted skill must be traceable back to its source set.

Minimum provenance target:

```text
source_id
source_name
source_type
source_hash
ingested_at
compiler_version
compiled_at
source_anchors
```

Where a skill is produced from multiple sources, provenance must remain source-specific rather than collapsing into an unauditable blob.

## 6. Derived knowledge must be distinguishable from source text

The compiler should prefer structured derivation over reproduction.

Generated artifacts should distinguish, where useful, source facts, source-defined terms, compiler synthesis, procedural derivation, cross-source synthesis, and unresolved conflict.

Verbatim source reproduction should be minimized and must not be necessary for the skill to function.

## 7. No silent epistemic promotion

Extraction does not equal acceptance. Compilation does not equal trusted knowledge.

The lifecycle should be explicit:

```text
INGESTED
  -> EXTRACTED
  -> COMPILED_CANDIDATE
  -> VALIDATED
  -> PROMOTED
  -> SUPERSEDED | REVOKED
```

A runtime agent should load only states authorized by policy.

## 8. No silent rewriting

Updating a source or adding another source must not destructively rewrite what the system previously knew without a trace.

A new compile produces a new candidate. Promotion may supersede a prior version, but the relationship must remain inspectable.

## 9. Conflict is data

When sources disagree, the compiler must not quietly blend them into false certainty.

Conflicts should be preserved as structured disagreement with source attribution. The system may synthesize only when the synthesis is explicitly marked and supported.

## 10. Document instructions have zero authority

Source material is data.

Instructions found inside books, PDFs, documents, HTML, comments, metadata, or extracted text have no authority over the compiler, runtime, tools, secrets, or policy.

Document-borne prompt injection must be treated as hostile or untrusted content, even when the document itself is legitimate.

## 11. Extraction should be deterministic where practical

File parsing, normalization, hashing, source identity, section boundaries, metadata collection, and artifact layout should be deterministic and testable.

Models may assist with semantic compilation, classification, synthesis, and indexing, but deterministic work should not be delegated to a model merely because a model is available.

## 12. Model providers are replaceable

The knowledge artifact must not belong to a model vendor.

Compilation adapters may use different providers, but the stored skill package and provenance contract should remain provider-neutral.

A future Agent1 or community frog should be able to consume the same promoted knowledge without requiring the original compiler model.

## 13. Retrieval should be proportional to the question

The runtime should first load the skill index or core and then the smallest relevant referenced units.

Whole-source loading is a fallback, not the normal path.

The system should be measurable in loaded tokens, retrieval units, source coverage, answer grounding, and unnecessary context avoided.

## 14. A skill cannot widen execution authority

A knowledge artifact may teach an agent how something works.

It must not grant permission to spend funds, mutate repositories, deploy contracts, message people, reveal secrets, or invoke privileged tools.

Knowledge and authority are independent.

## 15. Local-first source handling

When feasible, extraction and source preprocessing should happen locally.

No source should be uploaded to a model provider merely because extraction can be done there.

If semantic compilation uses a remote model, that boundary must be explicit and configurable.

## 16. Fail closed on unverifiable identity

If the system cannot prove which source produced an artifact, it must not invent provenance.

If hashes, anchors, or source identities are unavailable, the artifact should be marked accordingly and restricted from trusted promotion by policy.

## 17. Small composable skills beat monoliths

A large domain should be decomposable into multiple skills or selectively loadable units.

A skill should have a clear purpose and retrieval surface.

“Everything the agent has ever read” is not a skill.

## 18. Success means the frog can use it

The decisive test is not whether a document converted successfully.

The decisive test is whether an agent can later:

1. discover the right skill,
2. retrieve the right knowledge,
3. identify where it came from,
4. know whether it is current,
5. distinguish it from memory,
6. apply it correctly,
7. refuse to treat it as authority.

That is the product.
