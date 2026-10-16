# Codex native MCP promotion deployment

This deployment gives Codex the ordinary-user flow:

```text
User: Promote Toadgod's lore.
Codex: [native Approve once & Promote / Reject]
User: clicks Approve once & Promote.
Codex: reports the protected promotion receipt.
```

The user runs no command during ordinary use. Chat text and ordinary MCP tool
confirmation remain non-authorizing.

## Security architecture

The Codex MCP process is an unprivileged transport. It can validate a staged
request, snapshot the candidate, and ask the protected operator to prepare a
challenge. It cannot read the credential registry, authorization ledger,
governed registry, receipts, or an operator key.

The protected operator independently revalidates the candidate and current
registry, creates the exact displayed challenge, and returns an
`elicitation/create` request with mode `openai/userVerification`. After the
human activates Codex's native control, only the proof is returned to the
operator. The operator verifies the P-256/SHA-256 signature against an enrolled
credential, consumes the request once, and performs the registry transition.

Two sockets preserve separate authorities:

- `operator.sock` is group `kf-reviewers` and supports the existing human TTY
  ceremony. The configured agent UID is rejected.
- `codex-operator.sock` is group `kf-agent` and supports only the native-proof
  protocol. It never accepts text confirmation.

## One-time administrator deployment

This is an administrator ceremony, not an agent or ordinary-user action.

1. Install the protected operator according to
   [Linux protected promotion](LINUX_PROTECTED_PROMOTION.md), including the
   distinct `kf-agent`, human reviewer, and `kf-operator` identities.
2. Install and enable both socket units:
   `knowledge-forge-operator.socket` and
   `knowledge-forge-codex-operator.socket`. Both activate the same hardened
   `knowledge-forge-operator@.service` implementation.
3. From the authenticated human Codex desktop session, run the enrollment
   helper once. It calls app-server `userVerification/enroll` and produces a
   create-new 0600 `codex-credentials.json`. Review its credential ID,
   ES256 public key, principal, and timestamp, then install it as
   `kf-operator:kf-operator` mode 0600 under the protected state root. The file
   contains public verification metadata, not a private key.
4. Register the stdio MCP transport with the local Codex client:

   ```text
   codex mcp add knowledge_forge_approval -- \
     /opt/toadaid-kf-agent/bin/knowledge-forge-codex-mcp \
     --workspace-root /srv/toadaid-kf-workspace \
     --operator-socket /run/toadaid-knowledge-forge/codex-operator.sock
   ```

   Use the actual protected installation and workspace paths selected by the
   administrator. Registration changes Codex configuration and must be
   explicit. A new Codex session is required before the tool appears.
5. Verify `codex mcp list`, then perform the synthetic accept, reject, expiry,
   mutation, wrong-credential, bad-signature, replay, restart, and unavailable
   provider tests before real promotion.

## Repository entry points

- `bin/knowledge-forge-codex-mcp`: stdio MCP transport.
- `bin/knowledge-forge-codex-enroll`: one-time native credential enrollment.
- `deploy/linux-protected-operator/knowledge-forge-codex-operator.socket`:
  agent-accessible, proof-only socket.
- `knowledge_forge.protected_operator_service`: proof verification and
  consume-once promotion inside the operator boundary.

## Fail-closed conditions

Missing enrollment, unavailable native verification, decline, cancel, expiry,
wrong response ID, changed candidate, changed registry, malformed proof,
unknown credential, invalid signature, replay, wrong peer UID, missing socket,
or service failure leaves the candidate unpromoted.

The stdio server is not a substitute for the protected service. Do not place
an agent-readable credential registry or governed registry in its command
line or environment.
