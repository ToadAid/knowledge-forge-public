# Public Knowledge Forge CI policy (P3A-CI)

This public repository uses a **read-only GitHub Actions CI workflow** at
`.github/workflows/verify.yml`. It runs on pull requests targeting `main` and on
pushes to `main` with no production secrets, no write permissions, no privileged
operator invocation, and no remote deployment or promotion.

The `regression` matrix tests the advertised Python 3.10+ standard-library
baseline on Python 3.10, 3.11, 3.12, and 3.13. Each entry runs the installer
`--check-only` preflight, read-only command-surface smoke tests, and the full
`python -m unittest discover -s tests` suite. Optional PDF/OCR executables are
not installed; their explicit boundaries are tested with synthetic fixtures.

The aggregate job **`Knowledge Forge CI / required`** only succeeds if every
matrix entry succeeds. It has a stable name so a human administrator can later
select it as a required status check in the `main` repository ruleset.

## Deployment and authority boundaries

- `VALIDATED != PROMOTED`; a green CI run is **not** a human review, promotion,
  source-truth guarantee, independent operator authentication, or authorization.
- The workflow uses `pull_request`, **never** `pull_request_target`, and grants
  `contents: read` only. It does not consume runtime secrets.
- `actions/checkout` and `actions/setup-python` use reviewed immutable commit
  SHAs, and checkout disables persisted GitHub credentials.
- Tests do not create a live registry, issue promotion receipts, install the
  operator into an agent's PATH, or activate any production host.
- CI is not a substitute for source review and a separately protected operator.

## Human-owned repository protection (separate cut)

A workflow file alone does **not** enforce a required check. After a PR run
appears and all four matrix jobs succeed, a repository administrator should
verify the actual aggregate check name in GitHub, then update the `main` ruleset
to require **`Knowledge Forge CI / required`**. If the GitHub UI reports a
slightly different check name, verify the emitted status and use the exact
name shown rather than guessing.

Do not claim required CI is enabled until the active ruleset and a blocked
failing-PR case are verified. Keep manual human review for privileged and
security-sensitive changes, especially the future P3B operator boundary.
