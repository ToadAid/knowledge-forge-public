# Install Knowledge Forge for a Generic Agent

Knowledge Forge is distributed as a checked-out repository. It is not currently
published as a Python package. The generic-agent installer therefore bootstraps
a stable host-facing command surface around an exact checkout instead of
copying Forge code into another agent.

The installation law is:

```text
checkout stays canonical
        ↓
ordinary consumer CLI + zero-promotion authoring CLI
        ↓
PERSONAL: explicit user acceptance → personal library → lookup
or
ADVANCED: protected host → PROMOTED registry → governed context

privileged operator promotion
        ≠
ordinary agent PATH
```

## Requirements

- POSIX shell environment (Linux, macOS, or WSL), **or** native Windows 10/11
  with `cmd.exe` for the ordinary consumer/authoring install
  ([Native Windows](#native-windows-cmdexe))
- Git
- Python 3.10 or newer
- a persistent Knowledge Forge checkout

Optional source adapters have separate prerequisites. Text-bearing PDF
extraction uses Poppler. Explicit OCR uses Poppler plus Tesseract. An agent that
only discovers and loads already-built packages does not need those tools.

## What a newcomer should expect

**For ordinary users, start with
[NEWCOMER_GUIDE.md](docs/NEWCOMER_GUIDE.md).** The installer provides
`knowledge-forge` and `knowledge-forge-authoring`. A capable terminal agent
follows the [agent runbook](docs/FILE_TO_SKILL_AGENT_GUIDE.md) to forge and
validate, ask the user whether to accept the package, use
`personal add --accept`, and answer via `personal lookup`.

Installation does **not** automatically register a native Claude/Codex skill,
approval card, or future-chat retrieval hook. A personal library entry stays
`VALIDATED` and grants no tools. The explicit local acceptance flag is not
independently authenticated human authorization.

For separately governed `PROMOTED` registry consumption, a protected
operator and tested host adapter remain necessary. See
[GENERIC_AGENT_INTEGRATION.md](docs/GENERIC_AGENT_INTEGRATION.md).

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

## Native Windows (cmd.exe)

P3B-CP1 adds an **ordinary** native Windows installer: standard user, plain
`cmd.exe`, Python 3.10+. No WSL, Git Bash, Node, PowerShell scripts,
administrator rights, registry writes, services, downloads or PATH changes.
It installs the same two ordinary commands and **never** installs, exposes or
runs `knowledge-forge-operator`. Windows operator/promotion support is not
implemented (see [the CP2 design note](docs/P3B_CP2_WINDOWS_OPERATOR_DESIGN.md)).

Open a normal Command Prompt (not elevated) in the checkout:

```bat
bin\knowledge-forge-install-agent.cmd --check-only
bin\knowledge-forge-install-agent.cmd
```

Default locations:

```text
%LOCALAPPDATA%\ToadAid\knowledge-forge\bin\knowledge-forge.exe
%LOCALAPPDATA%\ToadAid\knowledge-forge\bin\knowledge-forge-authoring.exe
%LOCALAPPDATA%\ToadAid\knowledge-forge\state\agent-integration.json
```

`--prefix`, `--state-root` and `--manifest` take absolute paths and win over
the defaults (commands go in `<prefix>\bin`). If `%LOCALAPPDATA%` is missing or
fails the checks below and no explicit paths are given, the installer refuses;
it never guesses another location. PATH is not modified: call the commands by
full path, or add `<prefix>\bin` to your own user PATH yourself.

**Interpreter.** `KNOWLEDGE_FORGE_PYTHON` (absolute path to `python.exe`) if
set, otherwise the Python Launcher `py -3`, otherwise the first `python` on
PATH. Older than 3.10, or missing, exits 2 with nothing written. The absolute
interpreter path and version are recorded in the manifest.

**Why `.exe` commands.** The installed commands receive model-controlled
arguments. A `.cmd` wrapper has to re-expand `%*`, and `cmd.exe` would then
interpret `&`, `|`, `<`, `>` and `%` inside arguments (an argument `a&b` would
run `b`). Each command is therefore built from the console launcher stub that
ships with the selected interpreter's own `pip`
(`pip\_vendor\distlib\t64.exe`, the mechanism pip uses for every Windows
console script): its shebang pins the exact `python.exe -I`, and an embedded
`__main__.py` runs `knowledge_forge` or `knowledge_forge.authoring_cli` from
this exact checkout. Arguments go straight to Python; no shell parses them.
Nothing is downloaded. If that interpreter has no `pip`, the installer
refuses rather than falling back to a shell wrapper. Only the human-run
installer itself is a `.cmd`; quote any of its path arguments that contain
spaces or `& | ( )`.

**Destination policy (fail closed).**

- absolute local-drive paths only (`C:\...`); UNC/network, device or
  extended-length (`\\?\`, `\\.\`), relative, `..`, alternate data streams,
  reserved device names, trailing dots/spaces and paths over 200 characters
  are refused;
- symlinks, junctions and other reparse points anywhere in the path are
  refused, as is any path whose real OS path differs (8.3 short names, SUBST
  or mapped network drives, packaged-app file virtualization);
- every directory written into must be owned by you, and no account other
  than you, SYSTEM or Administrators may hold write access. A drive root such
  as `E:\` (which usually grants *Authenticated Users* modify),
  `C:\Users\Public`, other users' folders and `Program Files` are refused.
  AppContainer grants (sandboxed apps running as *your own* account) are
  reported, not refused, because they cannot exceed your rights;
- an existing `knowledge-forge-operator` (any extension) or a shadowing
  `knowledge-forge.cmd`/`.bat`/`.ps1`/... in `<prefix>\bin` is refused.

`--check-only` performs all of these checks plus a doctor run of the two
ordinary modules and writes nothing (no directories, files or bytecode
caches). Its JSON reports `operator_cli_installed: false`,
`promotion_authority: EXTERNAL_AUTHENTICATED_HUMAN_HOST_ONLY`,
`windows_support.operator_security_verified: false`, and whether the checkout
itself is writable by other accounts (`checkout_location_private`).

**Writes, retries and recovery.** Each file is written to a temporary name
and renamed into place without replacement. An existing file with identical
bytes counts as done; different bytes are refused and preserved. The manifest
is written last, only after both installed commands pass `--help`, so a failed
run never leaves a manifest. Re-running after an interruption completes the
missing files. If a file was changed (or `pip` was upgraded, which changes the
launcher bytes), install into a new prefix/state, or deliberately delete only
the exact files the installer named and run it again.

**Known environment notes.**

- Run the installer from a normal `cmd.exe`. Processes started by a packaged
  (MSIX) desktop app may have `%LOCALAPPDATA%` writes virtualized into that
  app's private storage; the installer detects this and refuses.
- Some Windows sandboxes (for example the Codex sandbox) grant their own
  accounts write access to `%LOCALAPPDATA%\Temp`; such a location is refused.

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

## After a skill reaches VALIDATED

**Personal Forge (default for ordinary users):** Show exact validation status,
source evidence, coverage and omissions. Wait for the user's explicit yes,
then call `knowledge-forge personal add <package> --validated-lifecycle
<lifecycle.validated.json> --accept`. On later questions, use
`knowledge-forge personal lookup "question"` and cite only returned source
bindings. No governed promotion, privileged operator or service is needed.

**Advanced governed host:** `VALIDATED != PROMOTED`. Governed
`discover`/`load-current` still needs separately authenticated promotion
and a tested host-owned adapter with OFF/DRY_RUN/ON controls. A personal
entry must never masquerade as current governed registry truth.

Neither path is automatically activated merely because the CLI shims were
installed. Reading the raw source is not a Forge lookup.

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
