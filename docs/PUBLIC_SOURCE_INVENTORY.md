# Public source inventory and audit summary

This candidate is a fresh source snapshot exported from private canonical commit
`1666de142e783c4a22142e2a8d146e5068b3c718`. Private Git objects, refs, tags,
remotes, configuration, and ancestry are not present.

The machine-readable `SOURCE_PROVENANCE.json` records every candidate path,
source/export SHA-256 values, inclusion classification, and license
classification.

## Included groups

- core Python engine under `src/knowledge_forge/`;
- all deterministic JSON schemas;
- consumer, authoring, operator, installer, and review checkout entry points;
- provider-neutral and compatibility integration documentation;
- consumer-capsule builder and entry point;
- all regression tests and synthetic fixtures, including the original
  PondReference EVM fixture and its deterministic generator;
- release-specific Apache-2.0, installation, contribution, security, changelog,
  notice, and local-first README material.

## Excluded private-source paths

- `BUILD_LIST.md` — internal roadmap and cross-project planning; not required at
  runtime.
- `SEALED_BASELINE.md` — private development-seal evidence; not required at
  runtime.
- `STAGE1_PROOF.md` — private historical proof record; not required at runtime.
- private `README.md` — replaced by the audited local-first public README.

## Always excluded

`.git/`, `.codegraph/`, environment files, credentials, signing material,
production registry/runtime state, user or customer data, private memory,
private catalogs, personal configuration, caches, compiled bytecode, and
unapproved generated artifacts.

No such path was tracked at the source commit. Historical blob scanning found no
real credential signature; manually reviewed hits were synthetic security-test
markers, an `example.test` identity, or architectural prose.

## Licensing

Knowledge Forge is prepared under Apache License 2.0. Tracked author identities
are ToadAid and the ToadAid-controlled Agent 0 automation identity. PondReference
is generated solely by the included hand-written Python assembler; generation
uses standard ABI signature selectors but no copied WETH9 source or bytecode.
Release approval must retain a human maintainer attestation that ToadAid authored
or otherwise controls sufficient rights to license the complete work.

This audit summary is preparation evidence, not publication approval.
