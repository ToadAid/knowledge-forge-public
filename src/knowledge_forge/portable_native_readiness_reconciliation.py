"""P4-P1-P1-P3-P9A: no-execution native readiness blocker crosswalk.

This is a deterministic static *reconciliation plan*, not observed native host
isolation, full dependency inventory, authenticated consent or launch authority.
Never opens host files, starts processes, reads configurations or issues tokens.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any, Mapping, Sequence

from knowledge_forge.portable_native_sandbox_contract import POLICY, PROOF_GATES, contract
from knowledge_forge.portable_native_hook_trust_review import review
from knowledge_forge.portable_native_hermetic_preflight import manifest

HOSTS = ('claude-code', 'codex-cli')
# P9's eight prerequisite families are copied as review vocabulary, not imported
# from unmerged PR #36 or promoted to evidence by a matching string.
FAMILIES = (
    ('fresh_executable_integrity',
     ('actual_host_executable_identity_and_dependency_tree',),
     'PASSIVE_REFRESH_POSSIBLE_NOT_PROVENANCE',
     'Fresh absolute path/inode/symlink/digest observation; independently establish authenticity and refuse races.'),
    ('dependency_closure',
     ('actual_host_executable_identity_and_dependency_tree',),
     'BOUNDED_STATIC_REVIEW_POSSIBLE_NOT_CLOSURE',
     'Resolve loader, transitive dependencies and runtime trust; do not execute ldd or the native host.'),
    ('native_host_mount_plan',
     ('actual_host_process_namespace_and_network_attestation',
      'actual_host_process_unmounted_private_files_and_secrets'),
     'SYNTHETIC_PREPARATION_ONLY_ACTUAL_NATIVE_UNVERIFIED',
     'Prepare minimum mounts and negative canaries; actual host-process mount/network evidence requires separately authorized test.'),
    ('effective_hook_source_inventory',
     ('host_hook_configuration_discovery_and_trust_review',
      'absence_of_extra_user_project_plugin_managed_hooks'),
     'STATIC_SOURCE_TAXONOMY_ONLY_EFFECTIVE_SOURCES_UNKNOWN',
     'Reconcile managed/user/project/plugin/other sources; unknown or uninspectable sources block native tests.'),
    ('version_specific_hook_support',
     ('hook_exit_status_stdout_stderr_and_duplicate_behavior',),
     'OFFLINE_DOCUMENTATION_REVIEW_ONLY_RUNTIME_UNKNOWN',
     'Version-scoped official hook schema review; do not infer actual dispatch from synthetic hooks.json.'),
    ('no_model_or_tools',
     ('no_model_or_tool_execution_during_native_test',),
     'NEGATIVE_CONTROL_DESIGN_ONLY',
     'Propose no-model/no-tool/no-network controls; cannot attest actual host behavior without approved native test.'),
    ('authenticated_single_use_authority',
     ('separate_explicit_authorized_test_scope',),
     'EXTERNAL_AUTHENTICATED_HOST_NOT_IMPLEMENTED',
     'Separate human-host issuance, exact digest/scope/expiry, nonce and atomic use-once journal required.'),
    ('bounded_lifecycle_and_receipts',
     ('bounded_test_runtime_cleanup_and_auditable_receipt',),
     'REVIEW_ONLY_LIFECYCLE_PLAN_NOT_NATIVE_RECEIPT',
     'Design timeout/kill/teardown receipts; verify crash cleanup only during separately approved native test.'),
)
ALLOWED_UNVERIFIED = frozenset({'NOT_OBSERVED', 'CLAIMED_UNVERIFIED'})


class ReadinessReconciliationError(ValueError):
    """A caller tried to supply false evidence, change a contract, or execute."""


def _claims(values: Mapping[str, str] | None) -> dict[str, str]:
    names = tuple(x[0] for x in FAMILIES)
    if values is None:
        return {n: 'NOT_OBSERVED' for n in names}
    if type(values) is not dict or set(values) != set(names):
        raise ReadinessReconciliationError('exact eight blocker families required')
    if any(type(values[n]) is not str or values[n] not in ALLOWED_UNVERIFIED for n in names):
        raise ReadinessReconciliationError('verified or authorized claim refused')
    return {n: values[n] for n in names}


def reconcile(host: str, *, unverified_claims: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read only deterministic policies. Never touch host environment or launch."""
    if type(host) is not str or host not in HOSTS:
        raise ReadinessReconciliationError('host label refused')
    parent = contract(host)
    hook = review(host)
    synthetic = manifest(host)
    if (len(PROOF_GATES) != 9 or len(FAMILIES) != 8
            or tuple(parent['required_future_evidence']) != tuple(PROOF_GATES)
            or parent.get('native_launch_authorized') is not False
            or hook.get('unverified_proof_gate_count') != 9
            or hook.get('actual_effective_hook_sources_identified') is not False
            or hook.get('extra_managed_user_project_plugin_hooks_excluded') is not False
            or hook.get('parent_contract_sha256') != parent['contract_sha256']
            or synthetic.get('native_runtime_gates_proven') != 0
            or synthetic.get('native_launch_authorized') is not False
            or tuple(synthetic.get('proof_gates_unverified', [])) != tuple(PROOF_GATES)
            or POLICY.get('approval') != 'SEPARATE_EXPLICIT_HUMAN_AUTHORIZATION'
            or POLICY.get('network') != 'UNSHARED_NO_EXTERNAL_ROUTE_OR_PACKET'
            or POLICY.get('extra_hook_sources') != 'ABSENT_OR_TEST_REFUSED'):
        raise ReadinessReconciliationError('canonical security contract drift')
    covered = [gate for _, gates, _, _ in FAMILIES for gate in gates]
    if len({name for name, *_ in FAMILIES}) != 8 or set(covered) != set(PROOF_GATES):
        raise ReadinessReconciliationError('incomplete blocker or proof-gate coverage')
    claims = _claims(unverified_claims)
    blockers = [
        {'family': key, 'mapped_native_gates': list(gates),
         'evidence_route_not_attestation': route,
         'required_future_evidence': guidance,
         'user_claim_unverified_only': claims[key],
         'status': 'BLOCKING_UNVERIFIED'}
        for key, gates, route, guidance in FAMILIES
    ]
    fingerprint_src = {'host': host, 'parent_contract': parent['contract_sha256'],
                       'hook_review': hook['source_state_fingerprint_not_host_attestation'],
                       'blockers': blockers, 'gates': list(PROOF_GATES)}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_src, sort_keys=True,
                        separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
    return {
        'schema_version': '0.1',
        'surface': 'P4_P1_P1_P3_P9A_PREMERGE_OFFLINE_READINESS_RECONCILIATION',
        'host_label_only': host,
        'state': 'REVIEW_ONLY_PREMERGE_BLOCKED_NO_NATIVE_EVIDENCE',
        'p9_dependency_at_authoring_base': 'PR36_NOT_IMPORTED_REQUIRES_POSTMERGE_RECONCILIATION',
        'parent_contract_sha256': parent['contract_sha256'],
        'hook_taxonomy_fingerprint_not_attestation': hook['source_state_fingerprint_not_host_attestation'],
        'p7_evidence_scope': 'PYTHON_SYNTHETIC_ONLY_NOT_ACTUAL_HOST',
        'blocker_families': blockers,
        'blocking_unverified_count': len(blockers),
        'native_proof_gates': [{'gate': gate, 'status': 'UNVERIFIED_NATIVE_EVIDENCE_REQUIRED'}
                               for gate in PROOF_GATES],
        'native_proof_gates_unverified': 9,
        'review_fingerprint_not_signature_or_permission': fingerprint,
        'native_host_started': False, 'hook_dispatched': False,
        'host_configurations_read': False, 'user_secrets_read': False,
        'model_called': False, 'tools_executed': False,
        'network_requested': False, 'native_launch_authorized': False,
        'human_authorization_received': False, 'authorization_token_issued': False,
        'approval_journal_implemented': False, 'promotion_authorized': False,
        'safe_to_activate_hooks': False, 'tool_grants': [],
        'next_action': 'RECONCILE_ON_EXACT_ORDERED_18_THEN_36_MAIN_WITH_FRESH_CI_BEFORE_REVIEW_PR',
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Inert Dell P9A premerge blocker review')
    parser.add_argument('--host', required=True)
    parser.add_argument('--mode', required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode != 'REVIEW_ONLY':
            raise ReadinessReconciliationError('native modes forbidden')
        result = reconcile(args.host)
    except ReadinessReconciliationError:
        print('Readiness reconciliation refused; no native host or approval', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
