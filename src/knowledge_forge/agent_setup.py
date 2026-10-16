"""Read-only onboarding for optional protected promotion.

The agent may inspect prerequisites and explain the administrator handoff.
This helper never creates accounts, elevates privileges, changes settings,
starts services, reads keys, or authorizes knowledge promotion.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

SETUP_QUESTION = (
    'Would you like protected promotion with approval popups? '
    'It requires you or your administrator to create separate kf-agent and '
    'kf-operator accounts, keeping your human reviewer account separate.'
)
PROTECTED_PATHS = {
    'code_root': Path('/opt/toadaid-kf-operator'),
    'state_root': Path('/var/lib/toadaid-kf-operator'),
    'configuration': Path('/etc/toadaid-kf/operator.json'),
    'agent_request_socket': Path('/run/toadaid-knowledge-forge/agent-requests.sock'),
    'reviewer_socket': Path('/run/toadaid-knowledge-forge/operator.sock'),
}


def _account(name):
    # Imported only on Linux; native Windows ordinary setup stays supported.
    import grp
    import pwd
    try:
        account = pwd.getpwnam(name)
    except KeyError:
        return None
    groups = []
    for gid in os.getgrouplist(name, account.pw_gid):
        try:
            groups.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            groups.append(str(gid))
    return {'name': name, 'uid': account.pw_uid, 'groups': sorted(groups)}


def _path_metadata(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'path': str(path), 'exists': False}
    except OSError as exc:
        return {'path': str(path), 'exists': None, 'error': type(exc).__name__}
    kind = ('symlink' if stat.S_ISLNK(info.st_mode) else
            'directory' if stat.S_ISDIR(info.st_mode) else
            'socket' if stat.S_ISSOCK(info.st_mode) else
            'file' if stat.S_ISREG(info.st_mode) else 'other')
    return {'path': str(path), 'exists': True, 'kind': kind,
            'uid': info.st_uid, 'mode': oct(stat.S_IMODE(info.st_mode))}


def setup_report():
    report = {
        'schema_version': '0.1', 'operation': 'READ_ONLY_AGENT_SETUP',
        'platform': sys.platform,
        'guide': 'docs/AGENT_SETUP.md',
        'deployment_guide': 'docs/GENERIC_DAEMON_APPROVAL.md',
        'setup_question': SETUP_QUESTION,
        'personal_forge_requires_service': False,
        'protected_deployment_verified': False,
        'accounts_created': False, 'services_enabled': False,
        'agent_settings_changed': False, 'promotion_performed': False,
    }
    if sys.platform != 'linux':
        return {**report, 'status': 'PROTECTED_SETUP_UNSUPPORTED_ON_THIS_PLATFORM',
                'setup_question': None,
                'next_step': 'Continue ordinary Personal Forge. This protected daemon reference requires Linux/systemd; do not offer Linux account/service creation on this platform.'}
    agent, operator = _account('kf-agent'), _account('kf-operator')
    blockers = []
    for name, account in [('kf-agent', agent), ('kf-operator', operator)]:
        if account is None:
            blockers.append(f'Missing account: {name}')
        elif account['uid'] == 0:
            blockers.append(f'{name} must be non-root')
    if agent and operator and agent['uid'] == operator['uid']:
        blockers.append('kf-agent and kf-operator must have different UIDs')
    if agent:
        privileged = sorted(set(agent['groups']) & {'sudo', 'wheel', 'admin', 'docker', 'lxd', 'kf-reviewers'})
        if privileged:
            blockers.append('kf-agent has disallowed privileged/reviewer groups: ' + ', '.join(privileged))
    if operator and 'kf-reviewers' in operator['groups']:
        blockers.append('kf-operator must not belong to kf-reviewers')
    paths = {name: _path_metadata(path) for name, path in PROTECTED_PATHS.items()}
    expected_kinds = {'code_root': 'directory', 'state_root': 'directory',
                      'configuration': 'file', 'agent_request_socket': 'socket', 'reviewer_socket': 'socket'}
    for name, info in paths.items():
        if info['exists'] is not True or info.get('kind') != expected_kinds[name]:
            blockers.append('Missing or invalid protected prerequisite: ' + name)
    return {**report,
            'status': 'ADMIN_SETUP_REQUIRED' if blockers else 'INDEPENDENT_VERIFICATION_REQUIRED',
            'accounts': {'kf-agent': agent, 'kf-operator': operator},
            'current_process_uid': os.getuid(),
            'current_process_is_agent_uid': agent is not None and agent['uid'] == os.getuid(),
            'prerequisite_paths': paths, 'blockers': blockers,
            'next_step': 'Follow docs/AGENT_SETUP.md. Respect prior choices; ask once if no choice exists. If yes, prepare the pinned deployment handoff for an administrator in a separate agent-inaccessible console. Account existence alone does not verify deployment.'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='knowledge-forge-setup', description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='inspect prerequisites only (also the default; no installation mode exists)')
    parser.parse_args(argv)
    try:
        print(json.dumps(setup_report(), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'SETUP_CHECK_REFUSED', 'error': str(exc),
                          'protected_deployment_verified': False, 'promotion_performed': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
