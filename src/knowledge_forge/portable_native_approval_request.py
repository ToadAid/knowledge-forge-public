"""P4-P1-P1-P3-P9: inert, deterministic *request* for a native-test review.

No approval credentials, nonce issuance, host configuration inspection,
subprocess, network, filesystem reads, hook install, or native host dispatch.
A merge, fingerprint, or packet is NEVER permission to launch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any, Mapping, Sequence

from knowledge_forge.portable_native_sandbox_contract import POLICY, PROOF_GATES, contract
from knowledge_forge.portable_native_hook_trust_review import review

HOSTS = ('claude-code', 'codex-cli')
# Historical P6 TNG observation, NOT a fresh file hash, provenance or stable pin.
SNAPSHOTS: dict[str, dict[str, Any]] = {
    'claude-code': {
        'version_reference_at_snapshot': '2.1.296',
        'file_size_bytes_at_snapshot': 257068216,
        'sha256_at_snapshot_only': '24972e3bc859fab2b46ed4c1e51f7d6130f06d3bd550811a114640de3370d0de',
        'elf_dependency_parser_status': 'DYNSTR_BOUND_OR_ADDRESS_REFUSED',
    },
    'codex-cli': {
        'version_reference_at_snapshot': '0.161.0',
        'file_size_bytes_at_snapshot': 292309256,
        'sha256_at_snapshot_only': '9a820c17865fa825d04db416818679a9d63bd72e50835c396f496e5684626c9c',
        'elf_dependency_parser_status': 'NO_DT_NEEDED_OBSERVED',
    },
}
# This is an upper-bound review proposal; not a runtime parameter injection.
LIMITS = {
    'max_native_host_launches_if_later_authorized': 1,
    'max_synthetic_hook_events_if_later_authorized': 1,
    'max_wall_clock_seconds_if_later_authorized': 20,
    'max_authorization_valid_seconds_if_later_issued': 600,
    'expected_hook_stdout_bytes': 0,
    'expected_hook_stderr_bytes': 0,
    'model_calls_allowed': 0,
    'tool_calls_allowed': 0,
    'outbound_network_allowed': False,
    'user_credentials_mounted_allowed': False,
    'user_home_or_project_mounted_allowed': False,
    'operator_promotion_allowed': False,
}
REQUIREMENTS = (
    ('fresh_executable_integrity', 'Re-observe exact absolute executable, symlink/parent chain, digest and provenance without silent race or substitution.'),
    ('dependency_closure', 'Account for real ELF loader and all dynamic/transitive dependencies; P6 name hints are insufficient.'),
    ('native_host_mount_plan', 'Independently review minimum read-only native runtime mounts and exclusion of home, credentials, configs and real projects.'),
    ('effective_hook_source_inventory', 'Review version-specific managed/user/project/plugin/session hook sources; unknown or extra sources refuse.'),
    ('version_specific_hook_support', 'Verify actual event name/schema and hook behavior on each pinned installed host version.'),
    ('no_model_or_tools', 'Evidence that a native test cannot invoke a model, tools, external network or credentials.'),
    ('authenticated_single_use_authority', 'Fresh human-host authentication, exact digest+host+scope+expiry-bound one-time nonce and atomic claim journal.'),
    ('bounded_lifecycle_and_receipts', 'Independently specified timeout, kill, cleanup, negative controls and audit receipt path.'),
)
ALLOWED_CLAIM_LABELS = frozenset({'NOT_OBSERVED', 'CLAIMED_UNVERIFIED'})


class NativeApprovalRequestError(ValueError):
    """Refused type, forged proof claim, altered original contract, or mode."""


def _claims(claims: Mapping[str, str] | None) -> dict[str, str]:
    if claims is None:
        return {g: 'NOT_OBSERVED' for g in PROOF_GATES}
    if type(claims) is not dict or set(claims) != set(PROOF_GATES):
        raise NativeApprovalRequestError('exact nine unverifiable gates required')
    if any(type(claims[g]) is not str or claims[g] not in ALLOWED_CLAIM_LABELS for g in PROOF_GATES):
        raise NativeApprovalRequestError('approval or verified evidence cannot originate in packet')
    return {g: claims[g] for g in PROOF_GATES}


def packet(host: str, *, unverified_claims: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Pure request template. No request issuance, key, account or file access."""
    if type(host) is not str or host not in HOSTS:
        raise NativeApprovalRequestError('unknown host')
    parent = contract(host)
    hooks = review(host)
    if (len(PROOF_GATES) != 9 or tuple(parent['required_future_evidence']) != tuple(PROOF_GATES)
            or parent['native_launch_authorized'] is not False
            or hooks['parent_contract_sha256'] != parent['contract_sha256']
            or hooks['unverified_proof_gate_count'] != 9
            or hooks['actual_effective_hook_sources_identified'] is not False
            or hooks['extra_managed_user_project_plugin_hooks_excluded'] is not False
            or POLICY.get('approval') != 'SEPARATE_EXPLICIT_HUMAN_AUTHORIZATION'
            or POLICY.get('host_settings') != 'NO_READ_OR_WRITE'
            or POLICY.get('model_and_tool_grants') != 'NONE'
            or POLICY.get('network') != 'UNSHARED_NO_EXTERNAL_ROUTE_OR_PACKET'
            or POLICY.get('extra_hook_sources') != 'ABSENT_OR_TEST_REFUSED'):
        raise NativeApprovalRequestError('canonical policy or P8 trust boundary drift')
    claims = _claims(unverified_claims)
    snapshot = dict(SNAPSHOTS[host])
    if (type(snapshot['file_size_bytes_at_snapshot']) is not int
            or snapshot['file_size_bytes_at_snapshot'] <= 0
            or len(snapshot['sha256_at_snapshot_only']) != 64
            or any(c not in '0123456789abcdef' for c in snapshot['sha256_at_snapshot_only'])):
        raise NativeApprovalRequestError('historical snapshot malformed')
    fingerprint_material = {
        'host': host,
        'snapshot': snapshot,
        'sandbox_contract_sha256': parent['contract_sha256'],
        'hook_source_review_fingerprint': hooks['source_state_fingerprint_not_host_attestation'],
        'unverified_claims': claims,
        'proposed_limits': LIMITS,
        'blocking_requirements': list(REQUIREMENTS),
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_material, sort_keys=True,
                          ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()
    return {
        'schema_version': '0.1',
        'surface': 'P4_P1_P1_P3_P9_NATIVE_TEST_REQUEST_TEMPLATE_NO_AUTHORIZATION',
        'state': 'UNISSUED_PROPOSAL_BLOCKED_PENDING_INDEPENDENT_EVIDENCE',
        'host_label_only': host,
        'parent_contract_sha256': parent['contract_sha256'],
        'p8_hook_taxonomy_fingerprint_not_runtime_trust': hooks['source_state_fingerprint_not_host_attestation'],
        'p6_historical_executable_snapshot_not_fresh_attestation': snapshot,
        'p7_evidence_scope': 'SYNTHETIC_PYTHON_ONLY_NOT_NATIVE_HOST',
        'review_fingerprint_not_signature_or_approval': fingerprint,
        'proposed_single_use_limits_not_enforced': dict(LIMITS),
        'blocking_preconditions_unverified': [
            {'requirement': key, 'required_independent_evidence': why, 'status': 'BLOCKING_UNVERIFIED'}
            for key, why in REQUIREMENTS
        ],
        'proof_gates': [
            {'gate': g, 'claim_unverified_only': claims[g], 'actual_native_status': 'UNVERIFIED_NATIVE_EVIDENCE_REQUIRED'}
            for g in PROOF_GATES
        ],
        'unverified_proof_gate_count': 9,
        'proposed_future_authority_owner': 'SEPARATE_AUTHENTICATED_HUMAN_HOST',
        'single_use_requires_future_host_owned_nonce_and_atomic_consumption': True,
        'request_token_issued': False,
        'approval_signature_issued': False,
        'single_use_mechanism_implemented_or_proven': False,
        'authorization_nonce': None,
        'authorization_expiry': None,
        'human_identity_authenticated': False,
        'human_authorization_received': False,
        'approved_host_test_scope': None,
        'native_launch_authorized': False,
        'native_host_started': False,
        'native_hook_dispatched': False,
        'actual_host_isolation_verified': False,
        'actual_hook_sources_verified': False,
        'dependency_closure_verified': False,
        'installer_provenance_verified': False,
        'model_called': False,
        'tools_executed': False,
        'network_traffic_sent': False,
        'credentials_accessed': False,
        'hooks_installed': False,
        'user_configuration_modified': False,
        'promotion_authorized': False,
        'tool_grants': [],
        'next_cut': 'P4-P1-P1-P3-P10_ONLY_IF_SEPARATELY_AUTHORIZED_AND_UNBLOCKED',
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='P9 inert native-test review packet, NOT authorization')
    parser.add_argument('--host', required=True)
    parser.add_argument('--mode', required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode != 'REQUEST_REVIEW_ONLY':
            raise NativeApprovalRequestError('native/approved modes forbidden')
        result = packet(args.host)
    except NativeApprovalRequestError:
        print('Native-test request refused; no authorization or host launch', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
