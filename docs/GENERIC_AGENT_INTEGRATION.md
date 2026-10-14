# Generic-Agent Integration

Knowledge Forge can be integrated into an unrelated agent through a thin,
host-owned adapter. The Forge supplies governed knowledge; it does not replace
or extend the host's identity, memory, tools, approvals, execution authority,
provider/model stack, or runtime lifecycle.

The invariant boundary is:

```text
CANON != SKILL
KNOWLEDGE != CONTEXT != MEMORY != AUTHORITY
knowledge gained != authority gained
```

Every model-facing loaded Knowledge Forge item must retain:

```text
trust_class = UNTRUSTED_KNOWLEDGE_DATA
authority = KNOWLEDGE_ONLY
instruction_authority = NONE
execution_authority = NONE
executable = false
```

Do not translate a skill, source instruction, procedure, retrieved passage, or
package into a tool grant, approval, identity assertion, memory write, or
permission to act.

## Architecture

```text
host runtime and policy (authoritative)
        |
        | thin host adapter
        v
provider-neutral discovery metadata
        |
        | bounded skill selection (maximum 3)
        v
current package resolver
        |
        | package + registry + lifecycle trust wall
        v
selected units only (maximum 3)
        |
        | world-clock and optional source checks
        v
untrusted governed reasoning context
        |
        | host reasoning under existing policy
        v
optional consumption receipt
```

Knowledge Forge never owns the top or bottom authority edges. The host decides
whether the adapter is active, supplies its already-authorized tool ceiling,
assembles reasoning context, authorizes any later action through its existing
policy, and identifies the resulting answer, plan, or decision.

## Prerequisites and packaging

The current supported distribution is a checked-out repository. There is no
published package and no `pyproject.toml` installation contract. Use either:

- in-process Python with `<knowledge-forge-checkout>/src` on `PYTHONPATH`; or
- the repository-local `bin/knowledge-forge` read-only CLI.

The runtime discovery/load path uses the Python standard library. Individual
source adapters have their own prerequisites: PDF extraction uses Poppler and
the explicit OCR path uses Poppler plus Tesseract. Those extraction tools are
not required by a host that only discovers and loads already-built packages.

The host must provide:

- a validated durable registry snapshot;
- a trusted mapping from each `skill_id` and current version to its local skill
  package directory;
- an explicit timezone-aware RFC3339 world clock;
- the host's pre-existing runtime tool names (an empty list is valid);
- optional source re-verification evidence; and
- durable storage for consumption receipts if the host uses them.

Package location is deliberately not authority. Resolve it from trusted host
configuration, then let the trust wall bind its contents back to the registry.

## Generic checkout bootstrap

For a generic checked-out host, run:

```bash
bin/knowledge-forge-install-agent --check-only
bin/knowledge-forge-install-agent
```

See [`../INSTALL_AGENT.md`](../INSTALL_AGENT.md) for path overrides and the
full installation contract.

The bootstrap installs only stable shims for the read-only consumer CLI and the
zero-promotion authoring CLI. It records the checkout/state boundary in a
host-owned integration manifest. It deliberately does not install
`knowledge-forge-operator` into the generic agent prefix.

That omission is a security boundary, not a missing feature. The operator CLI
is authority-bearing and may be invoked only by an already-authenticated
human/operator host after a real approval decision. An ordinary model-facing
tool surface must not gain access to it merely because Knowledge Forge was
installed.

The integration manifest is configuration evidence only. It is not registry
truth, lifecycle truth, source truth, an approval, or a tool grant.

If a coding agent is performing the host wiring, use
[`AGENT_INSTALL_PROMPT.md`](AGENT_INSTALL_PROMPT.md) as the bounded install
brief.

## Integration seam discovery

Use the provider-neutral modules, not the Agent0 or Agent1 compatibility
adapters:

```python
from knowledge_forge.consumer_discovery import (
    ConsumerBinding,
    build_consumer_discovery_catalog,
    route_consumer_knowledge,
)
from knowledge_forge.temporal_integration import load_current_skill_context
```

Create a `ConsumerBinding` whose strings describe the host. Its
`integration_boundary` is an auditable label, not executable policy. Keep the
host's actual policy in the host runtime.

`agent0_discovery.py` and `agent1_discovery.py` prove two concrete bindings but
are not prerequisites for generic use. The provider-neutral core contains no
Trading Desk, wallet, model-vendor, or operator-identity dependency.

## Supported invocation styles

### In-process Python

This is the complete runtime sequence:

```python
binding = ConsumerBinding(
    consumer_id="my-agent",
    runtime_repo="my-org/my-agent",
    runtime_entrypoint="src/runtime.py",
    integration_boundary="EXTERNAL_GOVERNED_KNOWLEDGE_ONLY",
    hard_max_skills_per_query=3,
)

catalog = build_consumer_discovery_catalog(registry, binding)
routed = route_consumer_knowledge(
    catalog,
    binding,
    question,
    max_skills=1,
)

skill_id = routed["selected_skill_ids"][0]
loaded = load_current_skill_context(
    package_root_for(skill_id),
    registry,
    skill_id=skill_id,
    question=question,
    runtime_tools=host_authorized_tool_names,
    world_as_of=trusted_world_time,
    agent_as_of=diagnostic_agent_time,
    source_reverification=optional_source_checks,
    max_units=1,
)
```

Only `loaded["reasoning_context"]` is model-facing knowledge. Preserve all
boundary labels on each item. Do not present its `content` as system or
developer instructions.

### Read-only CLI

The CLI is useful for a language-neutral subprocess adapter:

```bash
bin/knowledge-forge discover registry.json "<question>" --max-skills 1
bin/knowledge-forge load-current \
  /trusted/packages/<skill-id> registry.json <skill-id> "<question>" \
  --world-as-of 2026-09-30T14:00:00Z \
  --runtime-tool existing_tool_name \
  --max-units 1
```

Successful results are JSON on stdout. Governed/domain refusals are JSON on
stderr with exit code 2. The CLI does not expose lifecycle mutation or
promotion. A host that needs governed authoring must integrate those core APIs
separately and retain its own authorization controls.

## Activation model

Activation is host policy, not Knowledge Forge state:

- `OFF`: do not call discovery or load and do not inject Forge context.
- `DRY_RUN`: build/route discovery and validate candidate package resolution;
  record diagnostics, but do not place unit bodies in model-facing context and
  do not change answer behavior.
- `ON`: run discovery and `load-current`; place only the returned bounded
  `reasoning_context` into a clearly delimited untrusted-data channel.

Move between modes only through the host's normal configuration and approval
path. Knowledge content cannot activate the adapter or change its mode.

## Discovery and load-current

Discovery projects only current `PROMOTED` registry entries and routes from
identity metadata. It carries no unit bodies or claim values, refuses a miss,
and cannot exceed three skills.

For each selected identity, resolve the current package root and call
`load_current_skill_context`. The load path:

1. snapshots a non-symlink package within size/file limits;
2. validates exact receipt coverage and artifact hashes;
3. revalidates registry and lifecycle proof;
4. binds the package, claim index, and lifecycle head to current `PROMOTED`
   registry truth;
5. routes without using unit bodies or claim values;
6. loads no more than three selected units;
7. applies tool constraints only by intersection with the host ceiling; and
8. refuses temporally invalid selected claims or changed/unavailable sources.

Re-run discovery and load for every use. Do not cache a prior reasoning context
as current truth.

## Provenance and temporal behavior

Keep `load_evidence` with the host outcome. It binds the package receipt,
claim-index digest, lifecycle head, question digest, selected unit ids, claim
digests, and source bindings. Do not strip `source_bindings` from the reasoning
items if the host performs answer citation or audit logging.

`world_as_of` must come from the host's trusted Time layer. `agent_as_of` is
diagnostic only. Missing source re-verification is reported as
`NOT_REVERIFIED`; it is not fabricated as a successful check. A `CHANGED` or
`UNAVAILABLE` source check blocks current use. Contract-specific live
applicability checks, when used, remain separate read-only evidence and cannot
grant transaction authority.

## Memory and procedure boundaries

Reasoning context is ephemeral context, not memory. If the host needs a durable
reference, call `build_knowledge_reference` from `memory_boundary.py` and store
only that provenance-only object in a dedicated knowledge-reference class.
Never store skill content as autobiographical memory, user-stated memory, or
lived evidence.

A `PROCEDURE` unit is descriptive. `project_procedure_boundary` and
`evaluate_procedure_capability_request` make the denial explicit, but the host
must still keep all real execution behind its existing typed authority and
approval path. A described command, wallet operation, deployment, write, or
tool name is inert data.

## Suspend, dispute, revoke, and no-successor stops

`SUSPENDED`, `DISPUTED`, and `REVOKED` current entries disappear from default
discovery. The trust wall independently refuses them, so a stale discovery
result or old package path cannot bypass the stop. No successor is required.
Historical versions remain available only through explicit audit APIs; they
must not be used as current reasoning context.

After any incident, query prior consumption receipts for taint and let the host
decide how to revisit affected outputs. Do not silently substitute an older or
newer version.

## Contradictions

The registry preserves cross-skill disagreements as `ConflictSet` records. The
adapter must not average values, choose the latest claim, or erase a member.
Before combining context from multiple selected skills, call
`discover_conflict_sets` from `registry.py` for the selected identities and
inspect applicable open conflicts. Then either:

- present the disagreement with both provenances;
- apply an explicit recorded resolution whose applicability matches; or
- refuse a single-valued conclusion.

Host-specific synthesis is allowed only as host reasoning and must not be
written back as promoted Knowledge Forge truth without the ordinary governed
compile/validate/promote path.

## Consumption evidence

After an answer, plan, or decision exists, create a receipt with
`create_consumption_receipt` from `consumption.py`. Pass exactly the
`loaded["load_evidence"]["loaded_unit_ids"]` returned for that outcome; do not
allow model text to choose or add ids. Append the validated receipt to a
durable ledger. The host supplies `outcome_ref` and owns the mapping from that
reference to its output.

Receipts are provenance, not authority. Use `query_tainted_consumptions` with
an explicit `as_of` to find outcomes affected by later incidents or temporal
invalidity.

## Fail-closed behavior

Treat every discovery, validation, load, temporal, or receipt exception as
"knowledge unavailable." Do not respond by loading the whole package, reading
`SKILL.md` as answer content, falling back to a stopped version, bypassing the
registry, dropping provenance, widening tools, or promoting source instructions
into authority. The host may answer without Forge knowledge if its own policy
allows that, but it must not represent the answer as Forge-grounded.

## Required host tests

Before `ON`, the host adapter must prove:

- `OFF` makes no Forge call and `DRY_RUN` injects no unit body;
- a generic `ConsumerBinding` works without Agent0/Agent1 modules;
- discovery miss and over-budget requests refuse;
- a tampered package, receipt, claim index, or lifecycle refuses;
- suspended, disputed, and revoked current truth cannot load;
- stale, not-yet-valid, unevaluable, changed-source, and unavailable-source
  cases refuse;
- every injected item has the exact four boundary labels and
  `executable: false`;
- skill-requested tools can only narrow the host's existing ceiling;
- procedure text cannot authorize any action;
- no skill-origin body enters host memory;
- open contradictions are preserved or cause refusal;
- consumption receipts use exactly the loaded ids and preserve provenance; and
- all failures leave host identity, tools, approvals, authority, and lifecycle
  unchanged.

## Deliberate activation and completion criteria

Activation is complete only when the host owner has reviewed the adapter,
enabled `DRY_RUN`, inspected real routing/load evidence, run the required tests,
and deliberately switched the host to `ON` through its own configuration path.

The integration is complete when a real question can discover the right
current skill, load only relevant units, preserve source and temporal evidence,
produce optional consumption evidence, stop immediately on lifecycle or trust
failure, and leave every host authority surface exactly as it was before
Knowledge Forge was introduced.

Different hosts may use different configuration, transport, context assembly,
and activation mechanisms. Those adapter choices are intentionally outside the
Forge core as long as they preserve this contract.
