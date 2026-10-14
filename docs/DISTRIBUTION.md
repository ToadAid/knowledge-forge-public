# Distribution status — Knowledge Forge local-first source

This repository now contains the Apache-2.0-licensed, local-first Knowledge
Forge source checkout, subject to the terms in [LICENSE](../LICENSE) and
any applicable notices in [NOTICE](../NOTICE). This is a source-checkout
distribution, not a Python wheel, container, hosted API, or cloud approval
service. No VPS, service account, or remote approval server is required.

## Install from this checkout

See [INSTALL.md](../INSTALL.md) and
[INSTALL_AGENT.md](../INSTALL_AGENT.md). The checkout installer places only
consumer and zero-promotion authoring commands in an ordinary agent prefix.
The privileged operator command remains outside that prefix. Local terminal
review produces non-authorizing evidence; it does not authenticate anyone or
authorize promotion.

## Independent interoperability testing

The Codex and Claude native-host pilots are still separate, planned
verification. The local synthetic clean-room tests do not certify either
native host integration. See [INDEPENDENT_PILOT.md](INDEPENDENT_PILOT.md).

Release tags, binary packages, production host adapters, and live blockchain
integration are not claimed by this source publication.
