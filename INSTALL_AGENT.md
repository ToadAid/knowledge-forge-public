# Install Knowledge Forge for a Generic Agent

Knowledge Forge is distributed as a checked-out repository. It is not currently
published as a Python package. The generic-agent installer therefore bootstraps
a stable host-facing command surface around an exact checkout instead of
copying Forge code into another agent.

The installation law is:

```text
checkout stays canonical
        ↓
read-only consumer CLI + zero-promotion authoring CLI
        ↓
host-owned adapter / state
        ↓
governed knowledge context

privileged operator promotion
        ≠
ordinary agent PATH
```

## Requirements

- POSIX shell environment (Linux, macOS, or WSL)
- Git
- Python 3.10 or newer
- a persistent Knowledge Forge checkout

Optional source adapters have separate prerequisites. Text-bearing PDF
extraction uses Poppler. Explicit OCR uses Poppler plus Tesseract. An agent that
only discovers and loads already-built packages does not need those tools.

## Quick install

From the repository root:

```bash
bin/knowledge-forge-install-agent
```

The default installation writes:

```text
~/.local/bin/knowledge-forge
~/.local/bin/knowledge-forge-authoring
~/.local/state/knowledge-forge/agent-integration.json
```

If `XDG_STATE_HOME` is set, the state manifest lives under
`$XDG_STATE_HOME/knowledge-forge/`.

Add the command prefix to the host PATH if it is not already present:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Then verify:

```bash
knowledge-forge --help
knowledge-forge-authoring --help
```

The installer is create-once/idempotent for exact generated files. It refuses
to overwrite different existing shims or manifests and refuses a prefix that
already exposes `knowledge-forge-operator`.

## Custom host paths

```bash
bin/knowledge-forge-install-agent \
  --prefix /opt/my-agent/knowledge \
  --state-root /var/lib/my-agent/knowledge-forge
```

Use `--manifest PATH` to choose a different integration-manifest location.

For a no-write prerequisite/checkout check:

```bash
bin/knowledge-forge-install-agent --check-only
```

`--check-only` does not create the prefix, state root, shims, or manifest.

## What the installer deliberately does not install

The generic installer does **not** install `knowledge-forge-operator` into the
agent command prefix.

That executable is authority-bearing. It may only be invoked by a host after
external human authentication and an explicit approval decision. A model may
research, author, prepare, validate, and request promotion; it may not convert
its own request, prior chat intent, journal text, or successful validation into
promotion authority.

The installer also does not:

- create or promote a skill;
- create an operator approval;
- add tools or execution authority to the agent;
- modify the host's identity, memory, provider/model stack, or policy;
- install Poppler or Tesseract;
- create a registry or pretend an empty registry is current truth; or
- switch a host integration from `OFF`/`DRY_RUN` to `ON`.

## Integration manifest

The installer emits a small host-owned JSON manifest that records the checkout,
command surfaces, and the non-authority boundary:

```text
trust_class           = UNTRUSTED_KNOWLEDGE_DATA
authority             = KNOWLEDGE_ONLY
instruction_authority = NONE
execution_authority   = NONE
executable             = false
operator_cli_installed = false
promotion_authority   = EXTERNAL_AUTHENTICATED_HUMAN_HOST_ONLY
```

The manifest is configuration evidence, not registry truth and not an approval.

## Wire the host adapter

After installation, follow
[`docs/GENERIC_AGENT_INTEGRATION.md`](docs/GENERIC_AGENT_INTEGRATION.md).

The minimum consumer path is:

```text
host question
  → discover current PROMOTED identity
  → resolve trusted package path
  → load-current through the trust wall
  → inject only bounded reasoning_context as untrusted data
  → host reasons under its existing policy
```

A model-assisted authoring host may separately use
`knowledge-forge-authoring prepare` and `finalize`. `finalize` can reach
`VALIDATED`; it cannot promote.

Promotion is a distinct host ceremony:

```text
VALIDATED candidate
  → model/agent requests promotion
  → authenticated human approves through host policy
  → host invokes checkout-local knowledge-forge-operator
  → PROMOTED registry truth
  → consumer index/catalog refresh
```

Do not expose the operator executable as an ordinary model tool.

## Activation

Keep activation host-owned:

1. `OFF` — no Forge discovery/load.
2. `DRY_RUN` — route and validate without injecting unit bodies.
3. `ON` — inject only returned bounded governed reasoning context.

Before `ON`, run the required host tests in
[`docs/GENERIC_AGENT_INTEGRATION.md`](docs/GENERIC_AGENT_INTEGRATION.md).

## Agent-assisted installation

If a coding agent will perform the host wiring, give it
[`docs/AGENT_INSTALL_PROMPT.md`](docs/AGENT_INSTALL_PROMPT.md). The prompt keeps
the agent inside the same authority boundary and requires it to stop before any
promotion.

## Moving or replacing the checkout

Installed command shims intentionally point to the exact checkout used during
installation. If that checkout moves, install from the new checkout into a new
prefix/state location or remove the old exact generated files and reinstall
deliberately. Do not silently retarget an existing shim.

The checkout remains the software distribution and should stay version
controlled, inspectable, and updateable through the operator's normal Git
workflow.
