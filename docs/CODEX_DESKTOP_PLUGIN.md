# Codex Desktop — Knowledge Forge local plugin pilot (P0)

**Status:** repository plugin package and synthetic tests only. This is NOT a
claim that it has been installed, loaded, or tested in a live Codex Desktop
session. No screenshot, native popup, userVerification enrolment, or protected
daemon is required for this personal-library pilot.

## The visible product

In the Codex Desktop Plugins directory, the local marketplace offers
**ToadAid Knowledge Forge**. The plugin bundles a guided Forge → Accept → Ask
skill and two bounded read-only tools: `library_list` and
`knowledge_lookup`. The skill uses existing installed authoring/validation
CLI commands, asks for explicit personal acceptance, and then answers using
the library lookup result and source bindings.

The plugin is **not a custom embedded Codex panel**. UI components returned
from MCP apps are documented for ChatGPT; Codex Desktop visual rendering must
be separately proven rather than presumed.

## Developer pilot — deliberate human installation

1. Review and clone this repository into a persistent local directory.
2. Verify Python 3.10+ and install the ordinary Knowledge Forge CLI using
   [INSTALL_AGENT.md](../INSTALL_AGENT.md). Confirm `knowledge-forge --help`
   and `knowledge-forge-authoring --help`. The plugin's read-only MCP
   invokes the `knowledge-forge` executable from PATH. It does not install
   privileged tools, modify Codex config, or package a second copy of the
   core engine.
3. In the actual local Codex environment, explicitly add the checkout's local
   plugin marketplace (Codex CLI supports
   `codex plugin marketplace add /absolute/path/to/knowledge-forge-public`).
   Alternatively open this trusted checkout in Codex Desktop; its
   `.agents/plugins/marketplace.json` is repo-scoped.
4. Restart the desktop client. In the Plugins directory, choose the
   **ToadAid Local Plugins** marketplace and install **ToadAid Knowledge
   Forge**. Start a new conversation. Run the plugin's setup skill.
5. Check that the installed plugin exposes `library_list` and
   `knowledge_lookup`. If they do not appear, inspect the local plugin
   logs/config; do not claim the integration works.
6. Test with an authorized synthetic document and an independent personal
   library. Forge the document, inspect coverage and omissions, explicitly
   accept the VALIDATED package, then run lookup with source citations.
   Verify a no-match, malformed request, and a fresh-chat lookup. Test cancel
   of personal acceptance and verify nothing was added.

## Security

This P0 plugin **never** exposes governed promotion, host keys, approval
completion, daemon sockets or registry writes. Its MCP tools are read-only
and invoke only `personal list` and `personal lookup`. They return
untrusted source data and grant no tools. The separate protected UID daemon
remains an optional advanced deployment for PROMOTED knowledge.

Ordinary Personal Forge acceptance is conversational; it is not independent
human authentication. The model must not infer that an MCP prompt, a chat
confirmation, or a local plugin installation authorizes PROMOTED registration.

## Limits / release truth

- Requires Codex Desktop support for installing a local plugin marketplace,
  and a **local** terminal-capable Codex environment with ordinary CLI on PATH.
- Python executable in bundled `mcp.json` is `python3`; platform-specific
  startup must be verified on native Windows before claiming Windows support
  for this plugin.
- Does not automatically parse every format or attach every future chat.
- No protected service or native approval UI is activated.
- Local plugin installation is separate from public directory publishing.
  OpenAI documentation limits public submission of local-MCP desktop plugins
  to approved partners.
- Do not switch to a hosted remote API or upload user documents to make this
  prototype appear successful. Local data remains local except for whatever
  processing the user's selected model/provider performs.

See [the ordinary newcomer guide](NEWCOMER_GUIDE.md) and
[the personal library contract](PERSONAL_FORGE.md).
