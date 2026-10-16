# Inline document-to-skill approval

## Ordinary-user experience

The product experience is intentionally short:

```text
User: Make this document into knowledge skills.
Agent: Forged and validated. Coverage and omissions are shown.
User: Promote it.
Host: [View details] [Approve & Promote] [Reject]
User: selects Approve & Promote.
Agent: Promoted. Receipt and registry identity are shown.
```

The user runs no command, copies no path, manages no key and types no digest.
All authoring, validation, staging, revalidation and receipt handling are
implementation work performed by the agent and its surrounding host.

## Security boundary

The agent may call `prepare_inline_approval`. Its result always says
`AWAITING_NATIVE_HOST_APPROVAL`, grants no authority and performs no promotion.
The surrounding trusted host renders the card and captures the human action
through its own UI channel. That host signs the exact request and calls
`complete_inline_promotion` outside agent tool permissions.

Ordinary chat text—including `approve`, `APPROVE`, or a copied token—is not an
authorization input. An agent-created pseudo-terminal is not human identity.
If the host key, native approval event or protected registry is unavailable,
the flow remains pending and fails closed.

## Host integration contract

1. The agent forges, validates and stages the exact request, evidence,
   candidate binding and promotion bundle.
2. The host calls `prepare_inline_approval(...)` and renders the returned
   bounded card without giving the model an approval control.
3. On a native human action, the host authenticates its user and creates a
   signed decision with `approval_host.build_host_decision` using a key the
   agent cannot access.
4. The host calls `complete_inline_promotion(...)` with that decision.
5. The adapter re-reads and validates every binding, consumes the signed
   decision once, serializes registry mutation, and writes unique immutable
   lifecycle and receipt artifacts under protected state.
6. Only a returned `promoted: true` receipt is reported as success.

The legacy review-only terminal remains non-authorizing for diagnostics. The
Linux protected operator remains available as a hardened backend. Neither is
an additional ordinary-user ceremony.
