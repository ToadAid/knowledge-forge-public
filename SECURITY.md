# Security policy

Knowledge Forge treats knowledge as untrusted data, not authority. A passing
validation, a review receipt, or a promoted skill cannot grant tools, authenticate
a human, change host policy, or authorize execution.

## Reporting a vulnerability

Do not post active exploits, credentials, private source material, customer data,
or sensitive logs in a public issue. Contact ToadAid through an established
private maintainer channel and include the affected version/commit, reproduction
conditions, impact, and a minimally sensitive proof.

Do not include real secrets. If a credential may have been exposed, revoke or
rotate it through the owning provider rather than relying on deletion from Git.

## Supported surface

Security fixes are evaluated against the current `main` branch until versioned
release support is announced. This initial local-first distribution provides no
hosted service, account system, remote approval channel, or production endpoint.

## Important boundaries

- The generic installer never installs the operator CLI into an ordinary agent
  prefix.
- The P2B0 TUI is review-only and does not authenticate its keyboard input.
- Promotion is a distinct local operator action and is never automatic.
- Registry, lifecycle, package, receipt, evidence, temporal, and tool-ceiling
  checks fail closed.
- Optional RPC and source retrieval must remain read-only and independently
  bounded by the caller.

