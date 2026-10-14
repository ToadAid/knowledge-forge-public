# ToadAid Knowledge Forge

Knowledge Forge is a local-first, provenance-first engine for turning source
material into inspectable knowledge skills that agents can discover and load
without confusing knowledge with execution authority.

It runs from a local checkout. No hosted service, VPS, account, remote approval
server, or production connection is required.

## Architectural laws

```text
CANON != SKILL

KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY

VALIDATED != PROMOTED

REVIEWED != AUTHENTICATED != AUTHORIZED != EXECUTED
```

A source may become a validated candidate only through explicit normalization,
compilation, provenance, faithfulness, and validation checks. A validated
candidate is not current registry truth until a separate local operator action
promotes it. Loaded knowledge remains untrusted data with no tool, identity,
memory, or execution authority.

## What is included

- deterministic text, document-set, repository, PDF, DOCX, EPUB, technical,
  and explicit OCR ingestion boundaries;
- provider-neutral semantic compiler contracts;
- skill emission, validation, provenance, claim indexing, lifecycle, registry,
  conflict, incident, temporal, and consumption evidence;
- bounded discovery and current-skill loading for generic agent hosts;
- consumer and zero-promotion authoring CLIs;
- an explicit checkout-local operator CLI that the generic installer never puts
  on an ordinary agent's `PATH`;
- the P2B0 provider-neutral approval request contract and review-only terminal
  interface;
- deterministic schemas, synthetic fixtures, and the complete regression suite.

## Requirements

- Python 3.10 or newer
- Git
- a POSIX shell environment such as Linux, macOS, or WSL

The core and test suite use the Python standard library. Text-bearing PDF
extraction optionally calls Poppler tools. Explicit OCR optionally calls Poppler
and Tesseract. Knowledge Forge does not install those system tools.

## Install from a checkout

Clone the public repository, keep the checkout in a persistent location, and
run the prerequisite check:

```bash
git clone https://github.com/ToadAid/knowledge-forge-public.git
cd knowledge-forge-public
bin/knowledge-forge-install-agent --check-only
```

Install the ordinary agent-facing commands into a user-controlled prefix:

```bash
bin/knowledge-forge-install-agent
export PATH="$HOME/.local/bin:$PATH"
knowledge-forge --help
knowledge-forge-authoring --help
```

The installer creates checkout-pointing shims and a host-owned integration
manifest. It installs only `knowledge-forge` and `knowledge-forge-authoring`.
It deliberately refuses to install `knowledge-forge-operator` into the same
ordinary agent prefix.

See [INSTALL.md](INSTALL.md) and [INSTALL_AGENT.md](INSTALL_AGENT.md) for an
isolated-prefix example and the complete installation boundary.

## Use with agents

Knowledge Forge exposes provider-neutral discovery and loading contracts suitable
for Codex, Claude, Agent0, and other terminal agents. The generic integration
procedure is documented in
[docs/GENERIC_AGENT_INTEGRATION.md](docs/GENERIC_AGENT_INTEGRATION.md).

The source distribution and generic installer are locally tested. This initial
release does not claim independently certified end-to-end adapters for every
named agent host. A host must preserve Knowledge Forge output as untrusted
knowledge data and retain its own identity, memory, policy, tools, and authority.

## Create and validate skills

The authoring CLI exposes a bounded two-stage flow:

```bash
bin/knowledge-forge-authoring prepare --help
bin/knowledge-forge-authoring finalize --help
```

`prepare` creates deterministic source and compiler-request material. A semantic
compiler adapter may create a candidate against that request. `finalize` checks
the exact request/source/candidate binding and can produce a `VALIDATED` package.
It cannot promote that package.

Validate an emitted package independently:

```bash
bin/knowledge-forge validate-package /path/to/package
```

All examples and tests use synthetic or explicitly attributed fixtures. Do not
ingest material you lack rights to process or redistribute.

## Discover and load current knowledge

Discovery reads validated registry truth and returns bounded identity metadata:

```bash
bin/knowledge-forge discover registry.json "question" --max-skills 1
```

Loading revalidates the exact current package, registry entry, lifecycle proof,
temporal applicability, and allowed tool intersection before returning bounded
reasoning context. See the CLI help and generic integration guide for the full
`load-current` arguments.

## Human review and promotion

The P2B0 terminal interface records review decisions only:

```bash
bin/knowledge-forge-review synthetic --receipt-out /tmp/review-receipt.json
```

It requires an interactive TTY, displays exact bounded evidence, uses the current
UTC clock for real requests, rechecks expiry immediately before recording, and
emits a create-new non-authorizing receipt. A scripted terminal can simulate
keystrokes, so the TUI is not a human-authentication boundary.

A review receipt does **not** prove an authenticated operator, grant tools,
authorize promotion, invoke the operator CLI, or mutate a registry. Privileged
promotion remains a separate, explicit local operator operation outside the
ordinary agent `PATH`. Read
[docs/GENERIC_APPROVAL_TUI.md](docs/GENERIC_APPROVAL_TUI.md) before integrating
review output.

## Run tests

From the checkout root:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

No production registry or external service is required.

## What Knowledge Forge does not do

- It does not host an API or require a ToadAid account.
- It does not authenticate a human through terminal input alone.
- It does not let a model approve or promote its own output.
- It does not turn retrieved knowledge into memory, instructions, or permissions.
- It does not grant transaction, wallet, deployment, or other execution authority.
- It does not silently resolve conflicting claims or use stopped knowledge.
- It does not install optional system dependencies or run a background service.

## Security and licensing

Report vulnerabilities according to [SECURITY.md](SECURITY.md). Knowledge Forge
is prepared under the [Apache License 2.0](LICENSE).

The contract-pilot tests use the original, synthetic PondReference EVM fixture.
Its complete standard-library generator, ABI, pin, and runtime artifact are
included and reproducible byte for byte. PondReference is not deployed, is not a
production token, and grants no transaction authority. The former GPL-derived
WETH9 runtime fixture is not included in this source snapshot.

Contributions are welcome under the same Apache-2.0 terms; see
[CONTRIBUTING.md](CONTRIBUTING.md).

