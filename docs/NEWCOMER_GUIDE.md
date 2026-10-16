# Start here: forge your first personal knowledge skill

**You do not need to deploy a protected operator to use your own validated
knowledge.** A capable terminal AI agent can run the existing Knowledge Forge
commands to forge, validate, accept and retrieve from a local personal library.

> **Important:** This is a guided workflow, not a built-in Claude/Codex chat
> hook or a universal automatic plugin. The agent must follow this guide,
> and you must explicitly accept packages before adding them.

## 1. Install the ordinary tools once

Prerequisites: Python 3.10+, Git and Linux/macOS/WSL shell or native Windows
10/11 Command Prompt. Optional text-PDF extraction may require Poppler.
Explicit OCR may require Poppler plus Tesseract.

Or tell your agent:

```text
Install https://github.com/ToadAid/knowledge-forge-public in my private
persistent workspace, following INSTALL.md and INSTALL_AGENT.md.
Install only the ordinary consumer and authoring commands; check --help.
Read docs/AGENT_SETUP.md and run its read-only setup helper. Explain optional
protected approval and ask whether I want separate kf-agent and kf-operator
accounts, unless I have already chosen Personal Forge only.
Do not overwrite my agent configuration, promote knowledge, or publish my
source. Ordinary installation must not create accounts, enable services or
install an operator. If I choose protected setup, prepare the separate
administrator handoff first. Show installed paths.
```

**Linux / macOS / WSL:**

```bash
git clone https://github.com/ToadAid/knowledge-forge-public.git
cd knowledge-forge-public
bin/knowledge-forge-install-agent --check-only
bin/knowledge-forge-install-agent
export PATH="$HOME/.local/bin:$PATH"
knowledge-forge --help
knowledge-forge-authoring --help
```

**Native Windows (non-administrator `cmd.exe`):**

```bat
git clone https://github.com/ToadAid/knowledge-forge-public.git
cd knowledge-forge-public
bin\knowledge-forge-install-agent.cmd --check-only
bin\knowledge-forge-install-agent.cmd
"%LOCALAPPDATA%\ToadAid\knowledge-forge\bin\knowledge-forge.exe" --help
"%LOCALAPPDATA%\ToadAid\knowledge-forge\bin\knowledge-forge-authoring.exe" --help
```

The installed shims point at the original checkout; keep it in place.
For custom/private paths and exact Windows requirements, see
[INSTALL_AGENT.md](../INSTALL_AGENT.md).

### Optional protected approval setup

As part of onboarding, the agent follows [AGENT_SETUP.md](AGENT_SETUP.md) and
runs `bin/knowledge-forge-setup --check-only`. Unless you have already chosen,
it asks whether you want protected promotion with approval popups, explaining
that you or an independent administrator must create separate `kf-agent` and
`kf-operator` accounts and keep your human reviewer account distinct.

Personal Forge works without that setup. Choosing protected setup prepares an
administrator handoff; it does not create accounts, enable services, change
agent settings or approve packages. The protected daemon reference is currently
Linux/systemd only; ordinary Windows/macOS installation remains available.

For a shell convenience entry point, see [install.sh instructions](INSTALL_SH.md).
The default runs ordinary installation plus the diagnostic helper. Its separate
Linux administrator mode creates locked non-login accounts only, with no
services enabled. A successful account-creation message does not mean approval
popups are ready; follow the independent deployment and verification steps.

## 2. Forge and validate a document

Choose a file you are authorized to process. Give your agent its **real**
local path and this instruction:

```text
Read docs/FILE_TO_SKILL_AGENT_GUIDE.md in this Knowledge Forge checkout.
Forge <absolute-path-to-my-document> into VALIDATED knowledge package(s).
Before drafting, tell me the source type, hash, record/section count,
intended coverage and omissions. Don't change the original source.
Preserve quotes, dates, authors, qualifiers and available source references.
Use authoring prepare -> source-support judgment -> finalize, then
validate-package. Show real package paths, evidence, failed checks and
which support judgments you made as a model. Do NOT add or promote yet.
```

The current CLI requires a **structured draft plus support plan** supplied
by the agent. It does not itself automatically understand every PDF, book or
nested JSON record. Complex source formats may need an auditable intermediate
render. Never claim full coverage of an unprocessed collection. One draft can
contain up to 64 knowledge units; each unit can have at most three support
sections.

## 3. Accept into your personal library

Review validation findings, included/omitted items and source samples. Then:

```text
Yes, add the VALIDATED package(s) to my PERSONAL library.
Do not perform governed promotion.
```

After that explicit acceptance, the agent runs this once per package:

```bash
knowledge-forge personal add <package-root> \
  --validated-lifecycle <lifecycle.validated.json> --accept
knowledge-forge personal list
```

`--accept` records **conversational local acceptance, not authenticated
human authorization**. The package stays `VALIDATED`; no operator or
governed PROMOTED registry is needed.

## 4. Ask a sourced question

```text
What does my book say about patience? Use knowledge-forge personal lookup.
Answer using only relevant returned reasoning_context, show unit ID and
any actual source IDs/URLs. Don't invent references. If no match, say so.
```

Underneath, the agent runs:

```bash
knowledge-forge personal lookup "What does my book say about patience?"
```

The result contains bounded revalidated units with source bindings and
`UNTRUSTED_KNOWLEDGE_DATA`, `KNOWLEDGE_ONLY`, `instruction_authority=NONE`,
`execution_authority=NONE`, and no tool grants. It is knowledge, not commands.

**A fresh chat session does not automatically look in this library.** Until a
host integration is separately tested and enabled, tell your agent to run
`personal lookup`. Raw-file reading or model memory is not a Forge result.

Try a question not covered by your package. An honest no-match is a
correct outcome. Avoid assuming the agent knows everything in a book.

## 5. Remove a library entry if you wish

```bash
knowledge-forge personal list
knowledge-forge personal remove <skill-id>
```

Only the library reference is removed. Original source and package files
remain unchanged.

## Advanced (optional): governed promotion

Protected promotion and governed `discover`/`load-current` are separate
from Personal Forge. They require independent authenticated authorization,
a protected host and an explicit host integration. No ordinary installation
activates a native Codex/Claude/OpenClaw/Hermes approval UI.

[File-to-skill runbook](FILE_TO_SKILL_AGENT_GUIDE.md) ·
[Personal Forge internals](PERSONAL_FORGE.md) ·
[Advanced host approval](INLINE_APPROVAL.md) ·
[Capability/status table](FEATURE_STATUS.md)
