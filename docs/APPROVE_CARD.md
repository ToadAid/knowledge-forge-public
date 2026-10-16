# Claude approval card (on-screen Approve / Reject)

The ordinary-user surface for Claude sessions: **no commands to type, no
paths to manage**. After the agent stages a promotion request, a real
desktop window opens on the user's screen with the card and two buttons.

```text
Agent:  Forged and validated. Coverage and omissions shown. Promotion needs your approval.
Card:   [desktop window opens: skill, source, validation, digests, expiry]
User:   clicks [Approve & Promote]  →  types  APPROVE <8-hex token>  →  [OK]
Card:   PROMOTED. Receipt and registry identity shown.
```

`Reject` records a signed no-op — nothing is promoted. Closing the window
without choosing, or mistyping the phrase three times, refuses fail-closed.

## The one command (agent side)

```bash
bin/approve-card <request-dir>
```

Everything else is buttons in the window. One-time administrative
provisioning:

```bash
bin/approve-card --setup
```

creates the state root (`~/.local/state/knowledge-forge/approval-host/`),
a generated 0600 host key and an initialized governed registry; it refuses
to overwrite an existing trust anchor.

## Boundary

- The card is the **approval host**; it signs the decision with the key it
  holds and completes the promotion through the generic native-host adapter
  (`adapter_id="claude"`), reusing the unchanged engine, single-use ledger
  and immutable output paths.
- The agent stages and may pop the card, but the agent process never signs
  and chat text (`APPROVE` typed in the conversation) never authorizes.
- **Honest same-account caveat:** on a personal machine the card shares the
  user's account with the agent. Its independence is procedural — a real
  window the agent does not operate plus a typed phrase — not OS-level.
  The card states this on every show.
- `VALIDATED != PROMOTED`; `ACCEPTED != AUTHENTICATED`; no host key, no
  promotion; missing dependencies are named exactly, never simulated.

Related: `docs/INLINE_APPROVAL.md` (host contract),
`docs/NATIVE_HOST_APPROVAL_ADAPTERS.md` (Claude wiring section),
`docs/APPROVE_TUI.md`-era review TUI remains the review-only diagnostic.