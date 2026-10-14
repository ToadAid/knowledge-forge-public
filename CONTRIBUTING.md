# Contributing to Knowledge Forge

Thank you for helping improve Knowledge Forge.

## Development setup

Use Python 3.10 or newer from a local checkout. The core has no required
third-party Python dependency.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Keep changes deterministic, provider-neutral, local-first, and fail-closed.
Tests must use synthetic material and must not depend on private registries,
credentials, user data, hosted services, or production infrastructure.

## Security boundaries

Contributions must preserve:

```text
CANON != SKILL
KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY
VALIDATED != PROMOTED
REVIEWED != AUTHENTICATED != AUTHORIZED != EXECUTED
```

Do not expose `knowledge-forge-operator` through the generic installer, convert
review receipts into authorization, add hidden network activation, or weaken
package/registry/lifecycle validation.

## Licensing

Unless explicitly stated otherwise, a contribution intentionally submitted for
inclusion is provided under Apache License 2.0, consistent with Section 5 of the
license. Submit only work you have the right to contribute. Identify copied,
adapted, generated, or third-party material and its exact license in the pull
request; do not silently introduce incompatible code or data.

## Pull requests

- Keep changes focused and explain their security effect.
- Add deterministic tests for changed behavior.
- Run the complete suite and `git diff --check`.
- Do not include secrets, private data, generated runtime state, or production
  registry contents.
- Update documentation when behavior or boundaries change.

Report suspected vulnerabilities privately according to [SECURITY.md](SECURITY.md).

