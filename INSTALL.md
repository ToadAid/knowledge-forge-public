# Local installation

Knowledge Forge is distributed as a persistent source checkout. It has no
required third-party Python package and does not modify the global Python
installation.

## Prerequisites

- Python 3.10+
- Git
- POSIX shell (Linux, macOS, or WSL), or native Windows 10/11 `cmd.exe`
  for the ordinary consumer/authoring install (see
  [INSTALL_AGENT.md](INSTALL_AGENT.md#native-windows-cmdexe))

Optional adapters:

- Poppler for PDF text extraction
- Poppler plus Tesseract for explicitly requested OCR

## Installing for your AI agent?

Start with the [personal newcomer guide](docs/NEWCOMER_GUIDE.md):
**Forge → Accept → Ask.** This installs ordinary commands, not an automatic
Claude/Codex chat hook or an authenticated native approval card.

A capable terminal agent can follow the runbook to create and validate a
package, request your explicit local acceptance, use `personal add --accept`,
and query `personal lookup` for source-linked knowledge. This does **not**
require a PROMOTED registry or separate protected operator.

If you are implementing independently authenticated *governed* consumption,
see [advanced generic host integration](docs/GENERIC_AGENT_INTEGRATION.md).

## Verify the checkout

```bash
python3 --version
bin/knowledge-forge-install-agent --check-only
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Isolated installation example

This example keeps all generated state below a temporary or user-selected root:

```bash
INSTALL_ROOT="$(mktemp -d)"
bin/knowledge-forge-install-agent \
  --prefix "$INSTALL_ROOT/prefix" \
  --state-root "$INSTALL_ROOT/state"

"$INSTALL_ROOT/prefix/bin/knowledge-forge" --help
"$INSTALL_ROOT/prefix/bin/knowledge-forge-authoring" --help
test ! -e "$INSTALL_ROOT/prefix/bin/knowledge-forge-operator"
```

The generated shims point to the exact checkout. Keep that checkout in place, or
perform a deliberate new installation after moving it.

### Native Windows (cmd.exe)

```bat
py -3 --version
bin\knowledge-forge-install-agent.cmd --check-only
set PYTHONPATH=src
py -3 -m unittest discover -s tests

bin\knowledge-forge-install-agent.cmd --prefix "%USERPROFILE%\kf-try\prefix" --state-root "%USERPROFILE%\kf-try\state"
"%USERPROFILE%\kf-try\prefix\bin\knowledge-forge.exe" --help
"%USERPROFILE%\kf-try\prefix\bin\knowledge-forge-authoring.exe" --help
if exist "%USERPROFILE%\kf-try\prefix\bin\knowledge-forge-operator*" echo UNEXPECTED
```

Destinations must be private to your Windows account; shared locations such
as a drive root, `C:\Users\Public` or another account's folders are refused.
See [INSTALL_AGENT.md](INSTALL_AGENT.md#native-windows-cmdexe) for the full
native Windows policy.

## Ordinary-agent boundary

The POSIX installer checks that the checkout-local operator CLI exists and
starts, but does not install it into the ordinary agent prefix. The native
Windows installer never runs the operator at all; its doctor runs only the two
ordinary modules. It also does not install the
review TUI. This prevents ordinary installation from widening the model-facing
tool surface.

The checkout-local review TUI is review-only. The checkout-local operator CLI is
privileged. Neither should be exposed as an ordinary model tool.

For the full manifest, path, activation, and host integration contract, read
[INSTALL_AGENT.md](INSTALL_AGENT.md) and
[docs/GENERIC_AGENT_INTEGRATION.md](docs/GENERIC_AGENT_INTEGRATION.md).

## Uninstall

The installer intentionally performs no automatic uninstall. Review the exact
paths printed in the integration manifest, then remove only those generated
shims/state files through your normal local administration process. Do not delete
an entire prefix or state root unless you have independently confirmed ownership
of every file in it.

