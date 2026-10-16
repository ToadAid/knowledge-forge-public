# ToadAid Knowledge Forge

Knowledge Forge is a local-first, provenance-first engine for turning source
material into inspectable knowledge skills that agents can discover and load
without confusing knowledge with execution authority.

It runs from a local checkout. No hosted service, VPS, account, remote approval
server, or production connection is required.

## Forge → Accept → Ask

**Turn your own document into source-grounded knowledge for your AI agent.**
Personal Forge needs no account, Docker, systemd service, separate operator,
or governed promotion.

```text
You:   Brother, turn my notes into a knowledge skill.
Agent: I created a VALIDATED package. I covered 12 of 13 sections;
       one was omitted (details available). Add it to your personal library?
You:   Yes, add it.
Agent: Added to your PERSONAL library (not promoted).
You:   What do my notes say about patience?
Agent: [Uses personal lookup and cites only returned source bindings.]
```

This is a **guided agent workflow**: a terminal-capable agent must follow the
instructions and run the existing commands. Installing the CLI **does not**
automatically register a Claude/Codex skill, intercept every question, or
display a native approval card. No claim of universal agent compatibility is
made until a real host test demonstrates it.

### Copy this into your terminal-capable AI agent

```text
Use https://github.com/ToadAid/knowledge-forge-public.
Follow docs/NEWCOMER_GUIDE.md and docs/FILE_TO_SKILL_AGENT_GUIDE.md.
Install the ordinary commands in my private workspace; don't change my
existing agent settings. Forge <path-to-my-document> into VALIDATED
source-grounded knowledge. Show the included and omitted content, source
references and validation findings. Ask me before adding the package to my
PERSONAL library. If I agree, use knowledge-forge personal add.
When I ask about it, use knowledge-forge personal lookup and cite only
the source bindings actually returned. Do not perform governed promotion.
```

[Start here: newcomer guide](docs/NEWCOMER_GUIDE.md) ·
[User manual](USER_MANUAL.md) ·
[Personal Forge reference](docs/PERSONAL_FORGE.md) ·
[What is implemented?](docs/FEATURE_STATUS.md)

### The commands underneath (for agents and developers)

```bash
knowledge-forge-authoring prepare --help
knowledge-forge-authoring finalize --help
knowledge-forge validate-package /path/to/output/package

# Only after explicit user acceptance of a VALIDATED package:
knowledge-forge personal add /path/to/output/package \
  --validated-lifecycle /path/to/output/lifecycle.validated.json --accept
knowledge-forge personal list
knowledge-forge personal lookup "What does my document say about patience?"
knowledge-forge personal remove <skill-id>
```

Authoring requires a source-bound structured draft and a model-judged support
plan; it is **not** a single automatic document-to-skill CLI command. Package
validation checks evidence and integrity rules, not the independent truth of
every model-written claim. The agent must report real coverage and omissions
and preserve source evidence. The package files must remain accessible for
revalidation. A no-match must be reported, not invented away.

**Want advanced, independently authenticated PROMOTED knowledge?** See
[optional governed promotion](#advanced-optional-human-review-and-governed-promotion).
The experimental [Codex native verification bridge](docs/CODEX_NATIVE_APPROVAL.md)
and [Codex MCP native-approval transport](docs/CODEX_MCP_HOST_INTEGRATION.md)
are included as host integration components. The protected deployment and
registration procedure is documented in
[Codex native MCP promotion deployment](docs/CODEX_NATIVE_MCP_DEPLOYMENT.md);
it is not silently installed or activated.
This advanced path is separate from the personal-library workflow.

## ToadAid community

ToadAid builds for the **Toadgang community, Tobyworld, builders, and the people**. Knowledge Forge is one part of that larger effort: useful tools, durable knowledge, and governed agents that can grow without confusing knowledge with authority.

If this project is useful to you, we would be grateful if you also took a little time to discover the community and world that inspired much of this work:

- 🐸 **Join the Toadgang community on Telegram:** [https://t.me/toadgang](https://t.me/toadgang)
- 📖 **Explore Tobyworld:** [https://tobyworld.app](https://tobyworld.app)
- 🌱 **Follow Toadgod and explore the lore on X:** [https://twitter.com/toadgod1017](https://twitter.com/toadgod1017)

There is no requirement to join any community in order to read, evaluate, or work with ToadAid projects under their published terms.

We believe tools are better when people, stories, builders, and communities grow around them.

**Built by ToadAid. Built for the Toadgang. Built for the people.**

## Architectural laws

```text
CANON != SKILL

KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY

VALIDATED != PROMOTED

REVIEWED != AUTHENTICATED != AUTHORIZED != EXECUTED
```

A source becomes a validated candidate only through explicit normalization,
compilation, provenance, faithfulness, and validation checks. A validated
package can be **personally accepted without promotion**, but is not current
*governed* registry truth until a separately authorized operator promotes it.
Retrieved knowledge remains untrusted data without tool, identity, memory,
or execution authority.

## What is included

- deterministic text, document-set, repository, PDF, DOCX, EPUB, technical,
  and explicit OCR ingestion boundaries;
- provider-neutral semantic compiler contracts;
- skill emission, validation, provenance, claim indexing, lifecycle, registry,
  conflict, incident, temporal, and consumption evidence;
- bounded discovery and current-skill loading for generic agent hosts;
- consumer and zero-promotion authoring CLIs;
- local Personal Forge add/list/lookup/remove of validated knowledge, without
  governed promotion or tool grants;
- an explicit checkout-local operator CLI that the generic installer never puts
  on an ordinary agent's `PATH`;
- the P2B0 provider-neutral approval request contract and review-only terminal
  interface;
- a command-free inline approval adapter for an independently trusted host;
- strict one-shot native-host profiles for OpenClaw, Hermes, Codex, and generic
  hosts, including a bounded Codex native user-verification bridge and MCP
  `openai/userVerification` transport adapter;
- deterministic schemas, synthetic fixtures, and the complete regression suite.

## Continuous integration

Public pull requests and merges are checked by a read-only GitHub Actions
Python 3.10–3.13 regression matrix with a stable aggregate status check.
See [the CI policy](docs/CI_POLICY.md) for the security boundary and the
separate human step to require the check in the `main` ruleset. A passing
build never authorizes promotion.

## Requirements

- Python 3.10 or newer
- Git
- a POSIX shell environment such as Linux, macOS, or WSL, **or** native
  Windows 10/11 with `cmd.exe` (ordinary consumer/authoring install only; see
  [INSTALL_AGENT.md](INSTALL_AGENT.md#native-windows-cmdexe))

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

On native Windows, from a normal `cmd.exe` (no WSL, Git Bash, PowerShell
scripts or administrator rights):

```bat
bin\knowledge-forge-install-agent.cmd --check-only
bin\knowledge-forge-install-agent.cmd
"%LOCALAPPDATA%\ToadAid\knowledge-forge\bin\knowledge-forge.exe" --help
```

The installer creates checkout-pointing shims and a host-owned integration
manifest. It installs only `knowledge-forge` and `knowledge-forge-authoring`.
It deliberately refuses to install `knowledge-forge-operator` into the same
ordinary agent prefix.

See [INSTALL.md](INSTALL.md) and [INSTALL_AGENT.md](INSTALL_AGENT.md) for an
isolated-prefix example and the complete installation boundary.

## Use with agents

Generic installation supplies ordinary authoring and consumer commands for
Linux and native Windows. With the
[file-to-skill runbook](docs/FILE_TO_SKILL_AGENT_GUIDE.md), a capable agent
can forge, validate, seek your acceptance, and query your local personal
library. Personal lookup returns bounded, revalidated `reasoning_context`
with real source bindings and `KNOWLEDGE_ONLY` trust stamps.

**Not automatic:** the installer does not register a Claude/Codex plugin,
activate a native approval screen, or hook new conversations to your library.
For a fresh conversation, tell the agent to run `personal lookup`.
Reading a raw source file or relying on general model memory is not a
Knowledge Forge lookup.

Advanced host-controlled, `PROMOTED` retrieval is a *different* path.
See [generic agent integration](docs/GENERIC_AGENT_INTEGRATION.md).

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

## Advanced: governed discovery and loading

Discovery reads validated registry truth and returns bounded identity metadata:

```bash
bin/knowledge-forge discover registry.json "question" --max-skills 1
```

Loading revalidates the exact current package, registry entry, lifecycle proof,
temporal applicability, and allowed tool intersection before returning bounded
reasoning context. See the CLI help and generic integration guide for the full
`load-current` arguments.

## Advanced: optional human review and governed promotion

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

### Command-free product integration

For an **advanced governed host**, the intended flow is conversational:
request promotion, review a native **Approve & Promote** card, and activate
it once. The user runs no command and handles no path, key, or digest. The
repository provides the bounded card and protected-completion adapter for a
product host to implement that flow; see
[docs/INLINE_APPROVAL.md](docs/INLINE_APPROVAL.md).

This is an integration contract, **not** a deployed approval service and
not a Personal Forge prerequisite. Chat text is
intent only. Until the surrounding product supplies an independently
authenticated native UI event and keeps its signing key and protected registry
outside agent permissions, the request remains pending and nothing is
promoted. There is no same-account TTY fallback.

### Linux protected promotion reference

The optional [generic daemon approval queue](docs/GENERIC_DAEMON_APPROVAL.md)
lets compatible agents submit validated bundles while a separate human desktop
broker displays approval popups. A dedicated operator UID owns snapshots,
registry and receipts. This requires explicit administrator deployment and an
allowed request transport; ordinary installation does not activate it.

The repository now includes a Linux/systemd reference for the intended simple
human experience:

```text
knowledge-forge-promote /path/to/validated-bundle
  -> type APPROVE <short challenge>
```

It authenticates through a systemd-owned Unix socket and kernel peer
credentials, keeps one-use challenge state under a separate `kf-operator` UID,
and independently revalidates the candidate and current registry. It is not
installed by the generic installer and is not active merely because the source
files exist. A separate administrator must first establish and adversarially
verify the `kf-agent` / human reviewer / `kf-operator` OS boundary. See
[docs/LINUX_PROTECTED_PROMOTION.md](docs/LINUX_PROTECTED_PROMOTION.md).

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

