# Changelog

## Unreleased — initial open-source source snapshot

- Added a Linux/systemd protected-promotion reference: kernel Unix peer
  credentials, separate operator state, bounded candidate transport, exact
  package/registry binding, serialized registry mutation, five-minute one-use SQLite challenges, and a
  one-command human `promote` + typed `APPROVE` client. It remains absent from
  the ordinary agent installer and requires independent administrator
  provisioning before activation.
- Review TUI ergonomics P1: confirmation phrase derives from the request
  SHA-256 (with bounded mismatch recovery), `--from-dir` bundle loading,
  evidence-view operator guidance, and fail-closed EOF handling. Review-only;
  `approval.py` and the authority boundary are unchanged.
- Prepared the complete local-first Knowledge Forge engine for an Apache-2.0
  source distribution.
- Included generic consumer and authoring CLIs, checkout installer, schemas,
  deterministic tests, and safe fixtures.
- Included the P2B0 provider-neutral approval contract and review-only terminal
  interface with trusted-clock expiry checks.
- Preserved explicit local operator separation; no hosted service, account,
  remote approval, production activation, or release artifact is included.
- Replaced the former GPL-derived WETH9 test runtime with the independently
  authored, deterministic PondReference synthetic fixture and build recipe.

This entry describes a candidate snapshot. It is not a publication or release
announcement.

