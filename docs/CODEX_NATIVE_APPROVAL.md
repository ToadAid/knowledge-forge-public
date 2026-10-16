# Codex native approval

## Intended user experience

The ordinary-user flow can remain inside the Codex conversation surface:

```text
User: Promote it.
Codex: [Review exact skill] [Approve once & Promote] [Reject]
User: clicks Approve once & Promote, or focuses it and presses Enter.
Codex: Promoted. Receipt and registry identity are shown.
```

Click and Enter are equivalent activations of a trusted native UI control.
Typing `approve`, `APPROVE`, or `build it` into chat is model input and cannot
authorize promotion. An MCP tool confirmation is also not proof of the human
reviewer's identity.

## Repository support

`knowledge_forge.codex_approval_bridge` prepares a non-authorizing, short-lived
challenge for Codex app-server's experimental `userVerification/verify` method.
The challenge binds the exact request digest, skill identity and version,
approval ID, visible title and description, issue and expiry times, and a
host-generated random nonce. Its lifetime cannot exceed five minutes.

After native verification, the bridge accepts only the exact proof result,
asks a protected verifier to validate the credential signature and map it to
an authenticated principal, revalidates the package and request, and enters
the existing consume-once promotion path. The decision ledger also rejects a
reused decision nonce even when an attacker changes and re-signs its timestamp.

Preparation never authorizes or promotes. Cancellation, expiry, changed
display or request data, malformed proof, verification failure, replay, or an
empty principal fails closed.

## Codex protocol boundary

Current Codex app-server documentation describes `userVerification/verify` as
experimental. The request contains a base64url challenge plus a title and
description. A successful response contains a credential ID and a P-256 ECDSA
SHA-256 signature over the decoded challenge bytes. The local client may
collect that proof, but the authenticated service owns credential registration
and account binding; local UI success alone is not enrollment or authorization.

For an MCP host, Knowledge Forge maps this primitive through Codex's distinct
`openai/userVerification` elicitation mode. See
[Codex MCP native-approval integration](CODEX_MCP_HOST_INTEGRATION.md).

Knowledge Forge deliberately does not implement credential enrollment or a
standalone trust store inside agent-accessible code. The bridge receives an
injected proof verifier so a deployment can keep account binding and public-key
verification in its protected host backend.

## Deployment requirements

The bridge and MCP transport adapter are ready for protected host integration,
not automatically active after a normal Knowledge Forge install. A real
deployment still requires all of the following:

- a Codex client and app-server configuration that exposes native user
  verification on supported hardware;
- human credential enrollment and backend registration;
- a protected verifier that validates the P-256 signature and returns the
  authenticated reviewer principal;
- OS isolation that denies the agent access to the verifier, host key,
  credential registry, decision ledger, promotion registry and receipts;
- host wiring that displays the exact returned title and description and calls
  completion only after verified native activation; and
- end-to-end tests for click, keyboard Enter, cancellation, timeout, restart,
  mutation, replay, account mismatch and unavailable verification hardware.

Do not expose the proof verifier or completion function as a model-callable
tool. Do not treat Codex's experimental capability advertisement as proof that
the current client, account or machine is enrolled.

## What the repository tests prove

The tests exercise the binding, strict schemas, short expiry, proof-verifier
boundary, chat refusal, changed-data refusal, successful one-shot promotion,
and nonce replay protection with synthetic packages and a stubbed verifier.
They do not prove that a live Codex UI rendered the control, that a real device
signed the challenge, or that a production backend verified an enrolled user.
No service is installed, no host permissions are changed, and no package is
promoted by adding this bridge.
