"""P9B: CLI-level synthetic file-to-VALIDATED acceptance pilot.

Deliberately not an agent adapter, promotion, model, host hook, live Q/A,
network isolation attestation, or native Claude/Codex execution proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

from knowledge_forge.registry import create_skill_registry

STAMP = '2026-10-01T06:00:00Z'
CLAIM_KEY = 'pool.liquidity.active_in_range'
SOURCE = ('# Concentrated Liquidity\n'
          'Liquidity is active only while the current price is inside the position range.\n\n'
          '# Other\nUnrelated material.\n')
CLAIM: dict[str, Any] = {
    'claim_key': CLAIM_KEY,
    'subject': 'pool.liquidity',
    'predicate': 'active_in_range',
    'value': True,
    'qualifiers': {},
    'scope': {},
    'validity': {'as_of_source': 'test-source'},
    'executable': False,
}
MAX_CLI_OUTPUT = 128 * 1024


class AcceptancePilotError(ValueError):
    """Refusal of unsupported invocation or unexpected acceptance outcome."""


def _check_repo(root: Path) -> tuple[Path, Path]:
    """Known in-checkout CLI entrypoints only; never resolve user PATH commands."""
    if not isinstance(root, Path) or not root.is_absolute() or root.is_symlink():
        raise AcceptancePilotError('checkout root refused')
    if not (root / 'src/knowledge_forge/agent_authoring.py').is_file():
        raise AcceptancePilotError('checkout authoring core missing')
    cmds = (root / 'bin/knowledge-forge-authoring', root / 'bin/knowledge-forge')
    if any(p.is_symlink() or not p.is_file() or not os.access(p, os.X_OK) for p in cmds):
        raise AcceptancePilotError('ordinary CLI entrypoints missing or unsafe')
    return cmds


def _run(argv: Sequence[str], work: Path, *, expect: int,
         failure_output: str = 'stderr') -> dict[str, Any]:
    """Only expected local CLIs; stdout/stderr consumed solely as JSON."""
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(work / 'home'),
           'TMPDIR': str(work / 'tmp'), 'LANG': 'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE': '1'}
    try:
        proc = subprocess.run(list(argv), input=b'', stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=work, env=env,
                              check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcceptancePilotError('local CLI did not complete safely') from None
    if proc.returncode != expect or len(proc.stdout) > MAX_CLI_OUTPUT or len(proc.stderr) > MAX_CLI_OUTPUT:
        raise AcceptancePilotError('local CLI result or output ceiling mismatch')
    if failure_output not in {'stdout', 'stderr'}:
        raise AcceptancePilotError('invalid refused-command output route')
    payload = proc.stdout if expect == 0 or failure_output == 'stdout' else proc.stderr
    try:
        result = json.loads(payload.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AcceptancePilotError('local CLI did not emit expected JSON') from None
    if not isinstance(result, dict):
        raise AcceptancePilotError('local CLI JSON must be an object')
    if expect != 0 and failure_output == 'stderr' and proc.stdout:
        raise AcceptancePilotError('refused CLI unexpectedly emitted stdout')
    return result


# A governed discovery miss is an explicit CLI refusal, NOT a successful empty result.
NO_MATCH_DISCOVERY_ERROR = (
    'no governed knowledge skill matched knowledge-forge-cli discovery query'
)


def _require_discovery_refusal(consumer: Path, registry: Path,
                               question: str, work: Path) -> bool:
    """Require an exact fail-closed no-match response from the actual CLI."""
    refused = _run([str(consumer), 'discover', str(registry), question,
                    '--max-skills', '1'], work, expect=2)
    if (set(refused) != {'schema_version', 'ok', 'command', 'error'} or
            refused['schema_version'] != '0.1' or
            refused['ok'] is not False or
            refused['command'] != 'discover' or
            refused['error'] != NO_MATCH_DISCOVERY_ERROR):
        raise AcceptancePilotError('unexpected governed discovery refusal')
    return True


def _options() -> list[str]:
    return ['--knowledge-id', 'test:liquidity', '--skill-slug', 'liquidity',
            '--title', 'Liquidity', '--default-kind', 'DECISION_RULE']


def _support_plan(*, entailment: str = 'SUPPORTED') -> dict[str, Any]:
    return {'schema_version': '0.1',
            'judge_id': 'synthetic:test:assertion-only-not-independent-model',
            'units': [{'claim_key': CLAIM_KEY, 'section_ids': ['s001'],
                       'entailment': entailment, 'qualifiers_preserved': True}]}


def run_pilot(checkout: Path) -> dict[str, Any]:
    """Run one temporary offline synthetic CLI pilot. Never promote a package."""
    if sys.platform != 'linux':
        raise AcceptancePilotError('P9B Dell/Linux pilot only')
    author, consumer = _check_repo(checkout)
    with TemporaryDirectory(prefix='kf-p9b-synthetic-') as temp:
        work = Path(temp)
        (work / 'home').mkdir(mode=0o700)
        (work / 'tmp').mkdir(mode=0o700)
        source = work / 'source.md'
        draft = work / 'draft.md'
        plan = work / 'support-plan.json'
        output = work / 'validated-output'
        source.write_text(SOURCE, encoding='utf-8')
        original = source.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        draft.write_text(
            '# SKILL: synthetic\n# source_sha256: ' + digest + '\n\n'
            '# Liquidity is range bounded\n\n'
            'Liquidity becomes inactive outside the selected price range.\n\n'
            '## Structured claim\n\n```json\n'
            + json.dumps(CLAIM, indent=2, sort_keys=True) + '\n```\n',
            encoding='utf-8',
        )
        plan.write_text(json.dumps(_support_plan(), sort_keys=True) + '\n', encoding='utf-8')
        common = _options()
        prepared = _run([str(author), 'prepare', str(source), str(draft),
                         *common, '--occurred-at', STAMP], work, expect=0)
        sections = prepared['normalized_source']['sections']
        if len(sections) != 2 or sections[0]['section_id'] != 's001' or len(prepared['units']) != 1:
            raise AcceptancePilotError('source coverage or section binding mismatch')
        if prepared.get('promotion_authorized') is not False or prepared.get('execution_authority') != 'NONE':
            raise AcceptancePilotError('prepare authority boundary mismatch')
        result = _run([str(author), 'finalize', str(source), str(draft), *common,
                       '--plan', str(plan), '--output-root', str(output),
                       '--occurred-at', STAMP,
                       '--validator-id', 'synthetic:test-validator'], work, expect=0)
        if (result.get('lifecycle_state') != 'VALIDATED' or
                result.get('promotion_performed') is not False or
                result.get('human_authorization_consumed') is not False or
                result.get('execution_authority') != 'NONE' or
                result.get('package_valid') is not True or
                result.get('faithfulness_valid') is not True):
            raise AcceptancePilotError('finalize authority or package contract mismatch')
        package = Path(result['package_root'])
        if package != output / 'package' or not package.is_dir():
            raise AcceptancePilotError('materialized package location mismatch')
        emitted_manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
        emitted_units = emitted_manifest.get('units', [])
        if (emitted_manifest.get('authority') != 'KNOWLEDGE_ONLY' or
                emitted_manifest.get('source_ids') != [prepared['normalized_source']['source_id']] or
                len(emitted_units) != 1 or emitted_units[0].get('claim_key') != CLAIM_KEY or
                emitted_units[0].get('executable') is not False or
                len(emitted_units[0].get('support', [])) != 1 or
                emitted_units[0]['support'][0].get('section_id') != 's001' or
                emitted_units[0]['support'][0].get('source_id') != prepared['normalized_source']['source_id']):
            raise AcceptancePilotError('emitted package evidence and source binding drift')
        valid = _run([str(consumer), 'validate-package', str(package)], work, expect=0)
        if valid.get('valid') is not True or valid.get('artifact_count', 0) < 6:
            raise AcceptancePilotError('materialized package did not validate independently')
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise AcceptancePilotError('original source bytes changed')
        lifecycle = json.loads((output / 'lifecycle.validated.json').read_text(encoding='utf-8'))
        if lifecycle['state'] != 'VALIDATED':
            raise AcceptancePilotError('lifecycle advanced beyond validation')
        registry = work / 'empty-current-registry.json'
        registry.write_text(json.dumps(create_skill_registry(created_at=STAMP), sort_keys=True) + '\n',
                            encoding='utf-8')
        inspected = _run([str(consumer), 'registry-inspect', str(registry)], work, expect=0)
        if inspected.get('valid') is not True or inspected.get('skill_count') != 0:
            raise AcceptancePilotError('synthetic empty registry invalid')
        # The real discovery core deliberately refuses when there is no match:
        # an empty registry is NOT a successful result with match_count == 0.
        supported_question_discovery_refused = _require_discovery_refusal(
            consumer, registry, 'What is the liquidity range condition?', work)
        out_of_scope_question_discovery_refused = _require_discovery_refusal(
            consumer, registry, 'What does the unrelated section recommend?', work)
        refused = _run([str(consumer), 'load-current', str(package), str(registry),
                        result['canonical_skill_id'], 'What is the liquidity range condition?',
                        '--world-as-of', STAMP], work, expect=2)
        if refused.get('ok') is not False or refused.get('command') != 'load-current':
            raise AcceptancePilotError('unpromoted current load failed to refuse')
        # Negative control: SOURCE was changed after drafting; stale binding must refuse.
        changed_source = work / 'changed-source.md'
        changed_source.write_bytes(original + b'\nChanged after drafting.\n')
        unsupported_root = work / 'unsupported-result'
        bad_plan = work / 'unsupported-plan.json'
        bad_plan.write_text(json.dumps(_support_plan(entailment='UNSUPPORTED')) + '\n', encoding='utf-8')
        stale_root = work / 'stale-result'
        stale = _run([str(author), 'finalize', str(changed_source), str(draft),
                      *common, '--plan', str(plan), '--output-root', str(stale_root),
                      '--occurred-at', STAMP, '--validator-id', 'synthetic:test-validator'], work, expect=2)
        if stale.get('promotion_performed') is not False or stale_root.exists():
            raise AcceptancePilotError('changed source accepted')
        unsupported = _run([str(author), 'finalize', str(source), str(draft),
                            *common, '--plan', str(bad_plan), '--output-root', str(unsupported_root),
                            '--occurred-at', STAMP, '--validator-id', 'synthetic:test-validator'], work, expect=2)
        if unsupported.get('promotion_performed') is not False or unsupported_root.exists():
            raise AcceptancePilotError('unsupported entailment accepted')
        # Negative control: corrupt an emitted artifact; validator must refuse.
        skill_text = package / 'SKILL.md'
        if not skill_text.is_file() or skill_text.is_symlink():
            raise AcceptancePilotError('expected package artifact missing')
        skill_text.write_bytes(skill_text.read_bytes() + b'\nSynthetic tampering.\n')
        corrupt = _run([str(consumer), 'validate-package', str(package)], work,
                       expect=2, failure_output='stdout')
        if corrupt.get('valid') is not False:
            raise AcceptancePilotError('tampered package accepted')
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise AcceptancePilotError('source modified by negative controls')
        return {
            'schema_version': '0.1',
            'surface': 'P9B_SYNTHETIC_ORDINARY_CLI_ACCEPTANCE_PILOT_NOT_AGENT_INTEGRATION',
            'state': 'SYNTHETIC_CLI_ACCEPTANCE_PASS_NOT_GOVERNED_CONSUMPTION',
            'synthetic_source_sections': 2,
            'supported_unit_count': 1,
            'omitted_source_sections': 1,
            'source_bytes_unchanged': True,
            'prepare_cli_pass': True,
            'finalize_cli_validated_pass': True,
            'validator_cli_pass': True,
            'emitted_source_provenance_bound': True,
            'validated_lifecycle_not_promoted': True,
            'empty_registry_current_skills': 0,
            'promoted_discovery_no_match_refused': supported_question_discovery_refused,
            'out_of_scope_discovery_no_match_refused': out_of_scope_question_discovery_refused,
            'discovery_miss_surface': 'CLI_EXIT_2_STRUCTURED_STDERR_REFUSAL',
            'load_current_unpromoted_refused': True,
            'changed_source_binding_refused': True,
            'unsupported_entailment_refused': True,
            'package_tamper_refused': True,
            'separately_promoted_registry_constructed': False,
            'human_approval_authenticated': False,
            'native_agent_host_started': False,
            'model_question_answering_tested': False,
            'host_adapter_tested': False,
            'operator_invoked': False,
            'knowledge_promoted': False,
            'execution_authority': 'NONE',
            'tool_grants': [],
            'automatically_consumed_knowledge': False,
            'synthetic_fixture_cleanup_complete': True,
            'next_step': 'RELEASE_PILOT_HUMAN_USABILITY_NOT_CLAIMED',
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Synthetic ordinary CLI acceptance, no agent host or promotion')
    parser.add_argument('--mode', required=True)
    args = parser.parse_args(argv)
    if args.mode != 'SYNTHETIC_CLI_ONLY':
        print('Refused: no native, live agent, promotion or ON modes', file=sys.stderr)
        return 2
    try:
        root = Path(__file__).resolve().parents[2]
        report = run_pilot(root)
    except (AcceptancePilotError, OSError, ValueError, KeyError, TypeError):
        print('Refused: synthetic acceptance pilot did not pass', file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
