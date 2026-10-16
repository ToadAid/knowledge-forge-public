# Native host approval adapters

## What this adds

`knowledge_forge.generic_host_adapter` is the provider-neutral completion
boundary for an agent host that already has an authenticated, native human
approval surface. It lets the ordinary user stay in the host UI:

```text
User: Promote it.
Host: [View details] [Approve once & Promote] [Reject]
User: selects Approve once & Promote.
Host: Promoted, with a receipt.
```

The user does not run a Knowledge Forge command, copy a digest, manage a key,
or become a Knowledge Forge operator. The agent prepares the candidate and the
non-authorizing card. Trusted host code displays the card, captures one native
decision, and completes the promotion outside the agent's permissions.

This module is a host integration seam, not an automatically installed plugin.
Installing Knowledge Forge does not edit OpenClaw, Hermes, or another host's
configuration. A host maintainer still has to wire the seam and establish the
protected deployment boundary below.

## Supported profiles

| Adapter ID | Native approval input | Accepted approval | Rejected inputs |
|---|---|---|---|
| `generic-native-host` | host-owned approval UI | `approve-once` | reusable approval, bypass, chat text |
| `openclaw` | OpenClaw plugin approval | `allow-once` | `allow-always`, bypass, chat text |
| `hermes` | Hermes approval transport | `once` | `session`, `always`, YOLO/bypass, chat text |
| `codex` | Codex native user verification | `approve-once` | MCP confirmation, chat text, unverifiable device proof |
| `claude` | Claude desktop approval card (`bin/approve-card`) | `approve-once` | reusable approval, bypass, chat text |

The OpenClaw mapping was checked against upstream commit
`4881c9bbda01cb300680a09847e0895a0bcf492c`. The Hermes mapping was checked
against upstream commit `e5eb6b36fe78eb012cefd1f5f436cd458411b8b3`.
Those pins document the reviewed APIs; they are not a claim that future host
versions are compatible. Re-review the host on upgrade.

The Codex profile has a dedicated bridge for the experimental app-server
`userVerification/verify` protocol. See
[Codex native approval](CODEX_NATIVE_APPROVAL.md) for its exact readiness and
deployment boundary, and
[Codex MCP native-approval integration](CODEX_MCP_HOST_INTEGRATION.md) for the
`openai/userVerification` elicitation transport. The stdio server and protected
proof-only operator socket are covered by
[Codex native MCP promotion deployment](CODEX_NATIVE_MCP_DEPLOYMENT.md). Their
presence in source is not an assertion that the current client has installed,
enrolled, or enabled them.

## Integration sequence

1. Agent-accessible code forges, validates, stages, and requests promotion.
2. Host-only code calls `prepare_generic_host_approval(...)` and renders the
   returned card. The card is explicitly non-authorizing.
3. The host authenticates the reviewer through its native operator surface.
4. The host emits the exact event contract with the displayed
   `request_sha256`, a host-generated unique approval ID, its authenticated
   principal, and a one-shot decision.
5. Host-only code calls `complete_generic_host_approval(...)`.
6. The adapter revalidates the candidate, request and event; rechecks the
   request digest immediately before signing; signs with the protected host
   key; and enters the existing consume-once promotion path.
7. Only `promoted: true` is success. Denial returns `promoted: false`; malformed,
   stale, changed, replayed, persistent, or bypassed approvals raise and leave
   the candidate unpromoted.

The event mapping is evidence from trusted host code, not a credential. Giving
the model access to the completion call or signing key destroys this boundary.

## Required deployment boundary

Do not enable an adapter until all of these are true:

- the approval surface authenticates a human independently of model output;
- the model cannot resolve its own approval or synthesize the host callback;
- the completion function is not registered as a model-callable tool;
- the agent account cannot read the host key;
- the agent account cannot write the registry, decision ledger, lifecycle, or
  receipt directories;
- only the one-shot decision is offered for Knowledge Forge promotion;
- persistent/session/always approval and YOLO/bypass modes are rejected;
- the host generates unique approval IDs and supplies a trusted clock;
- request expiry, denial, timeout, cancellation, replay, host restart, and
  package/request mutation have all been tested fail-closed; and
- host upgrades are pinned and reviewed before compatibility is asserted.

An in-process plugin is not automatically isolated merely because it uses a
native approval widget. For example, an agent with write access to the host's
plugin/config directory, unrestricted host execution, or access to the signing
key can impersonate the trusted side. Use OS ownership and service boundaries,
not event fields, as the security control.

## OpenClaw wiring

Use OpenClaw's durable plugin approval flow and operator approval scope. The
displayed request must carry the exact Knowledge Forge card and allow only
`allow-once` or `deny`. After OpenClaw returns its approval ID and decision,
host-owned plugin/backend code constructs the event and invokes completion.

Do not expose completion as an ordinary OpenClaw tool. Do not map
`allow-always` to approval. The gateway/plugin directory, completion backend,
host key, and registry must be outside the agent's write and execution reach.

## Hermes wiring

Use a host-owned Hermes approval transport and accept only its `once` decision
or denial. `session` and `always` are not valid Knowledge Forge promotion
decisions. If YOLO or any approval bypass is active, completion must not be
called and the adapter independently refuses an event marked as bypassed.

Do not expose completion as a Hermes model tool or let a model-controlled
plugin claim that bypass is off. The host integration must obtain that fact
from trusted runtime state. If the Hermes version cannot provide a
non-bypassable authenticated callback outside model control, the secure result
is **unsupported and unpromoted**.

## Generic hosts

Another host can use `generic-native-host` when it can provide the same native,
one-shot, independently authenticated decision and protected backend. Rename
or translate its UI labels in host code, but do not broaden the accepted event
contract. A terminal line or chat message saying `APPROVE` is not a native host
event.

## Codex wiring

Use `prepare_codex_native_approval(...)` to create the bounded challenge and
native verification request. The host may present the approval control beside
the conversation. A human can click it, or focus it and press Enter. Keyboard
activation is valid only because it activates the trusted native control; text
entered into the conversation is still ordinary model input.

The host backend must verify the returned device signature against an enrolled
credential and authenticated Codex account, then map that account to a bounded
reviewer principal before calling `complete_codex_native_approval(...)`.
Cancellation or failed verification produces no proof and no promotion. Keep
the proof verifier, credential registry, host key, decision ledger, registry,
and completion call outside model permissions.

## Claude wiring

The Claude side uses the same generic adapter contract with a real desktop
window instead of a hypothetical host app. The agent may stage the request
(bounded artifacts plus the validated bundle) and may invoke
`bin/approve-card <request-dir>`; the card window it opens is the approval
host. The user clicks **[Approve & Promote]** or **[Reject]** — click-only, no
typing; Approve needs one further confirm click (fail-closed on cancel or
close). The card process then builds the one-shot host event and
calls `complete_generic_host_approval(adapter_id="claude", ...)`, which
signs with the host key a36d8dd-style outside any agent channel and drives
the unchanged engine to the real PROMOTED transition.

One-time provisioning is `bin/approve-card --setup` (0700 state root,
generated 0600 host key, initialized registry; refuses overwrite). On this
pilot's personal machine the setup and the card share the user's account
with the agent; independence is procedural — a real window the agent does
not click — not OS-level, and the card states exactly that on every show.
Chat text such as `APPROVE` in the conversation remains intent only and
can never authorize.

Manual smoke on a real display verifies the promoted receipts; CI drives
the same core with a scripted window. See `docs/APPROVE_CARD.md`.

## Verification included here

The regression suite covers successful OpenClaw/Hermes/Codex one-shot promotion,
denial without mutation, persistent/session/always refusal, YOLO/bypass
refusal, request-digest mismatch, adapter substitution, request swapping after
review, extra-field refusal, chat-text refusal, changed Codex challenge/display,
failed device-proof verification, expiry, and the non-authorizing card.

These tests validate the Knowledge Forge seam with synthetic host events and a
stubbed Codex proof verifier. They do not certify a deployed OpenClaw, Hermes,
or Codex instance, its human identity, its filesystem isolation, its credential
backend, or its current host implementation.
