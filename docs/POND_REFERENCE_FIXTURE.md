# PondReference — Original Synthetic EVM Contract Fixture

This test-only fixture is an independently authored ToadAid teaching example.
It is **not** WETH9, an ERC-20 token, an onchain deployment, or an audited
production smart contract. No real blockchain or wallet connection is needed.

## Reproduction

Run `python3 scripts/generate_pond_reference_fixture.py` from the repository
root. The Python source is the complete deterministic EVM-runtime assembler and
fixture build recipe. It uses Python's standard library only and writes:

- `fixtures/contracts/pond-reference/abi.json`
- `fixtures/contracts/pond-reference/contract-pin.json`
- `fixtures/contracts/pond-reference/runtime-code.hex`

The EVM runtime implements deterministic synthetic view results for `name`,
`symbol`, `decimals`, `totalSupply`, `balanceOf`, and `allowance`. It also has
an explicitly non-view `transfer(address,uint256)` demonstration that **always
returns false**; it performs no storage writes. The runtime is intentionally
not a functional ERC-20 token. The bounded Knowledge Forge read-plan whitelist
must reject any attempt to invoke `transfer` as a view.

These read-plan tests use an in-memory fake RPC provider. They verify exact
chain identity, runtime byte length and SHA-256, function-call encoding,
read-only method allowlists, suspension on code mismatch, and refusal of
unknown or unsafe read plans. They do not demonstrate real Base WETH9
applicability, chain-state verification, contract deployment, or transactions.

The hand-assembled runtime is produced independently. Standard ABI selectors
come from common method signatures; no Dapphub source or WETH9 runtime
bytecode is included. The fixture's source and build materials are provided
so it can be regenerated and independently inspected.

SPDX-License-Identifier: Apache-2.0
