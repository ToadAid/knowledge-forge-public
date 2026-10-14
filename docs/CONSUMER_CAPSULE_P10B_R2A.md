# P10B-R2A deterministic consumer capsule

This packaging boundary emits a deterministic Python 3.12 ZIP application for
one operation: `index-build` over the future registry snapshot at descriptor
path `/proc/self/fd/4`.

The recipe is outside `src/knowledge_forge`; Stage 8 sealed implementation
files are read as build inputs and are never modified. The builder begins at
`knowledge_forge.skill_index.build_skill_index`, computes its exact Python
symbol dependency closure, and emits only selected definitions. It refuses
operator, authoring, repository, promotion, durable registry write, and
lifecycle-transition symbols.

## Deterministic format

- Format: `PYTHON_ZIPAPP_STORED_V1`.
- Entry point: `__main__.py` from
  `packaging/consumer_capsule/entrypoint.py`.
- Python: CPython 3.12, ABI tag `cp312`.
- Entries: lexical order, ZIP `STORED`, mode `0644`.
- Timestamp: `1980-01-01T00:00:00Z` for every entry.
- Comment, extra build paths, cached bytecode, and external dependencies: none.
- Source revision: explicit 40-character Git commit supplied to the builder.
- Manifest: ordered per-file SHA-256, capsule SHA-256, file-manifest SHA-256,
  entry-point SHA-256, source revision, ABI, format, builder-source SHA-256,
  exact Python version/cache tag/executable SHA-256, and Linux/x86_64 build
  identity.

Example build (output should be outside the checkout):

```text
python3 scripts/build_consumer_capsule.py \
  --source-root /exact/clean/source \
  --source-revision <40-hex-revision> \
  --output-dir /isolated/output
```

The generated capsule and sidecar manifest are build evidence, not source
artifacts and are not committed.

## Future invocation contract

The capsule is designed for this future fixed argument sequence only:

```text
<pinned CPython ELF> -I -S -B /proc/self/fd/3 \
  index-build /proc/self/fd/4
```

R2A does not execute that command. Archive import behavior from a sealed memfd,
the Python ELF descriptor launch, dynamic loader/shared library/stdlib/native
extension binding, descriptor seals, cleanup, and output bounding remain
unproven until a separately authorized R2B.

The capsule contains pure Python plus standard-library imports. It includes no
native extension. CPython itself still depends on its ELF interpreter, dynamic
loader, shared libraries, standard library, and any native extension imported
by the standard library; those identities belong to Bridge configuration v2
and future runtime verification.

The capsule is knowledge-only data and grants no instruction, execution, tool,
operator, promotion, mutation, registration, or activation authority.
