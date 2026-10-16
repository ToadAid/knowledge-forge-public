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

The OpenClaw mapping was checked against upstream commit
`4881c9bbda01cb300680a09847e0895a0bcf492c`. The Hermes mapping was checked
against upstream commit `e5eb6b36fe78eb012cefd1f5f436cd458411b8b3`.
Those pins document the reviewed APIs; they are not a claim that future host
versions are compatible. Re-review the host on upgrade.

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

## Verification included here

The regression suite covers successful OpenClaw/Hermes one-shot promotion,
denial without mutation, persistent/session/always refusal, YOLO/bypass
refusal, request-digest mismatch, adapter substitution, request swapping after
review, extra-field refusal, chat-text refusal, and the non-authorizing card.

These tests validate the Knowledge Forge seam with synthetic host events. They
do not certify a deployed OpenClaw or Hermes instance, its human identity, its
filesystem isolation, or its current plugin implementation.
