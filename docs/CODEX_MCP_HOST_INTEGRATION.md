# Codex MCP native-approval integration

## User flow

Once a protected host embeds this adapter, the ordinary interaction is:

```text
User: Promote it.
Agent: requests the exact validated candidate.
Codex: displays the native user-verification surface.
User: approves in that trusted surface.
Protected host: verifies the device proof and promotes exactly once.
Agent: reports the receipt.
```

The user does not run a command, copy a digest, or manage a key. Text typed in
chat remains non-authorizing. An ordinary MCP tool-confirmation dialog also
remains non-authorizing; this adapter uses the distinct
`openai/userVerification` elicitation mode.

## Protocol mapping

`prepare_codex_mcp_approval(...)` maps the exact challenge created by
`prepare_codex_native_approval(...)` to an MCP `elicitation/create` request:

```json
{
  "jsonrpc": "2.0",
  "id": "codex:<unique-nonce>",
  "method": "elicitation/create",
  "params": {
    "mode": "openai/userVerification",
    "challenge": "<base64url challenge>",
    "title": "Approve Knowledge Forge promotion",
    "description": "<exact bounded candidate details>"
  }
}
```

An accepted response must bind the same JSON-RPC ID and contain exactly the
credential ID and P-256 signature in its content. Decline, cancel, JSON-RPC
error, wrong ID, changed display, changed challenge, extra proof fields, chat
text, stale challenge, or failed proof verification cannot promote.

`run_codex_mcp_approval(...)` is the host integration seam. The host supplies
two capabilities that must never be model-callable:

- `send_elicitation(request)`, implemented by its MCP transport; and
- `verify_proof(challenge, credential_id, signature)`, implemented by its
  protected credential/account backend.

Only after both return successfully does the adapter enter the existing
consume-once promotion path.

## Current Codex contract evidence

The integration was checked against the experimental JSON Schema emitted by
Codex CLI `0.162.1` using:

```text
codex app-server generate-json-schema --experimental
```

That schema defines `elicitation/create` mode `openai/userVerification`, an
accepted proof in response content, a 1–4096-byte base64url challenge, bounded
title and description fields, and a base64url DER ECDSA P-256/SHA-256
signature. This is an experimental interface. A host must regenerate and
review the schema when its Codex version changes.

## Protected deployment boundary

The module is repository integration code, not an automatically activated MCP
server. A production host must still provide a process boundary where:

- the model can request a promotion but cannot call completion directly;
- the elicitation response and signature are not returned to the model;
- the credential registry binds the credential to an authenticated account;
- the proof verifier, host key, decision ledger, governed registry and receipts
  are inaccessible to the agent account;
- request expiry and single-use state survive host restart; and
- unavailable native verification, missing enrollment, cancellation and
  transport failure all remain unpromoted.

Do not place a host key or credential registry in an ordinary local MCP server
spawned under the same writable identity as an unrestricted coding agent. Use
the protected operator/service boundary. Installation and account enrollment
remain explicit administrator actions; this repository change performs
neither.

The bounded stdio transport and protected proof-only operator protocol are
described in
[Codex native MCP promotion deployment](CODEX_NATIVE_MCP_DEPLOYMENT.md). They
remain inactive until an administrator installs the protected service, enrolls
the reviewer credential, registers the MCP server, and starts a new Codex
session.

## Test status

The deterministic tests exercise the exact elicitation mapping, a successful
host-owned exchange, decline/cancel, chat refusal, request-ID substitution,
extra proof data, changed display, verifier failure and the existing replay
ledger. The proof verifier and Codex transport are stubbed; these tests do not
claim that a live Codex client displayed a prompt or that production device
enrollment is complete.
