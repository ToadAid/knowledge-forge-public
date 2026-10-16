# One-word approval — the trusted host in your terminal (`approve`)

**STATUS: WORKING AND TESTED IN-PROCESS. The everyday user flow is one word:
`approve`. One `approve --setup` is the only command an administrator ever
runs manually.**

## The whole user experience

```text
user:   "Forge this doc into a skill."
agent:  Skill created and validated.               (existing pipeline)
user:   "Promote it."
agent:  Promotion request ready. Please review and approve.
        (agent stages the request artifacts + the validated bundle into
         <request-dir>; the agent CANNOT approve, and never runs approve)
user:   approve <request-dir>

        +-- KNOWLEDGE FORGE — PROMOTION APPROVAL ------------------+
        | Agent: ...        Action: Promote validated skill         |
        | Skill: ...        Version: ...                            |
        | Source validation: VALIDATED (package receipt-bound)      |
        | What will be promoted: registry VALIDATED->PROMOTED       |
        |   + receipts; no tools granted, model stays unauthorized  |
        | Evidence digests (recompute them yourself) ...            |
        | Expires / Host / request digest ...                       |
        +-----------------------------------------------------------+
        Approve this promotion? Type APPROVE f21604f9 (or REJECT ...) >

user:   APPROVE f21604f9            ← the one user-facing action
        PROMOTED. A real registry transition was executed and receipts
        written: registry, promoted lifecycle, receipt.
```

The typed phrase is `APPROVE ` + the first 8 hex characters of the canonical
request digest (the confirmation token shown on the prompt). It proves a
human actually read this card; it is not a secret. Mismatching three times
refuses; input ending (EOF) refuses; an expired request refuses before any
prompt. There is an agent-held or scripted way around none of these.

After the phrase, the process signs the host decision with its own key and
runs the unchanged, verified promotion pipeline (`approval_host_cli
.run_approval` — signature, expiry, request/candidate binding, single-use
ledger, unchanged engine). Any failure prints the exact missing dependency
and promotes nothing. `REJECT <token>` records a signed, durable rejection
and changes nothing in the registry.

## One-time setup (administrator; run humanly, once)

```bash
approve --setup
# state root (default ~/.local/state/knowledge-forge/approval-host):
#   host-key   0600, generated on the spot, never printed
#   registry.json  initialized governed registry (created only if absent)
```

Everyday use needs no setup: `approve <request-dir>` (flags exist for
non-default bundle / state root / key, and `<request-dir>` defaults to the
staging layout, with the bundle at `<request-dir>/promotion-bundle`).

## Where this can run

- Any real TTY the human operates: a plain terminal, tmux, or the built-in
  TUI of agent frameworks such as OpenClaw or Hermes — the command is an
  ordinary shell command; the TTY + typed-phrase gates are identical.
- The signed-decision contract below the TUI is the same one a future
  hosted approval host (ToadAid-app, a Telegram card backend) would drive:
  show the card, authenticate the human in the host's own way, sign, apply.
  The repo's `TELEGRAM_CARD`/`DESKTOP_CARD`/`WEB_TEMPLE` projections already
  render the same card but remain presentation-only
  (`transport_authority=NONE`) until a deployed host fills that role.

## Honest boundary

When the host process runs under the same Linux account as the agent, its
independence is procedural: a real TTY, a typed phrase derived from the
request digest, bounded retries, fail-closed EOF, and the agent-side rule
that the agent never runs `approve`. It is not OS-level isolation from a
malicious agent process — for that, run the host under a separate account
(the protected operator service path) or as part of an independently
deployed host. The laws are unchanged either way:
`VALIDATED != PROMOTED`, `REVIEWED != AUTHENTICATED != AUTHORIZED !=
EXECUTED`, `KNOWLEDGE != AUTHORITY`, `ACCEPTED != AUTHENTICATED`. Chat can
request; only the human, through a host decision this process signs, can
complete a real promotion.