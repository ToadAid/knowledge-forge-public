# Local installation

Knowledge Forge is distributed as a persistent source checkout. It has no
required third-party Python package and does not modify the global Python
installation.

## Prerequisites

- Python 3.10+
- Git
- POSIX shell (Linux, macOS, or WSL)

Optional adapters:

- Poppler for PDF text extraction
- Poppler plus Tesseract for explicitly requested OCR

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

## Ordinary-agent boundary

The installer checks that the checkout-local operator CLI exists and starts, but
does not install it into the ordinary agent prefix. It also does not install the
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

