#!/usr/bin/env python3
"""Administrator-only initial Linux deployment; never invoked by an agent.

No downloads, credential enrollment, model configuration, reviewer impersonation,
package submission or promotion. --plan performs reads only. Existing protected
installations are refused rather than upgraded or silently overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile


CODE = Path('/opt/toadaid-kf-operator')
STATE = Path('/var/lib/toadaid-kf-operator')
CONFIG = Path('/etc/toadaid-kf/operator.json')
SYSTEM = Path('/etc/systemd/system')
USER = Path('/etc/systemd/user')
TMPFILES = Path('/etc/tmpfiles.d/knowledge-forge-runtime.conf')
RUNTIME = Path('/run/toadaid-knowledge-forge')
UNITS = ('knowledge-forge-operator@.service', 'knowledge-forge-operator.socket',
         'knowledge-forge-agent-requests.socket')
REVIEWER_UNIT = 'knowledge-forge-reviewer.service'
SOCKETS = ('knowledge-forge-operator.socket', 'knowledge-forge-agent-requests.socket')
SYSTEM_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'
ENV = {'PATH': SYSTEM_PATH, 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_NO_REPLACE_OBJECTS': '1'}
MAX_ARCHIVE = 32 * 1024 * 1024


class DeploymentError(ValueError):
    pass


def pinned_files(root: Path, commit: str) -> dict[str, bytes]:
    """Read an exact Git object snapshot, never copy live working-tree code."""
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise DeploymentError('--commit must be the full reviewed 40-character Git commit')
    git = ['/usr/bin/git', '--no-replace-objects', '-C', str(root)]
    actual = subprocess.check_output(git + ['rev-parse', '--verify', commit + '^{commit}'], env=ENV).decode().strip()
    if actual != commit:
        raise DeploymentError('Reviewed commit does not resolve exactly')
    # These paths are fixed; Git never receives model-selected archive options.
    archive = subprocess.check_output(git + ['archive', '--format=tar', commit, '--',
                                            'src/knowledge_forge', 'deploy/linux-protected-operator',
                                            'scripts/install_protected.py', 'install.sh', 'LICENSE', 'NOTICE'], env=ENV)
    if len(archive) > MAX_ARCHIVE:
        raise DeploymentError('Pinned deployment snapshot exceeds 32 MiB')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name:
                raise DeploymentError('Unsafe archive path')
            if member.isdir():
                continue
            if not member.isfile() or member.size > MAX_ARCHIVE or member.name in files:
                raise DeploymentError('Only distinct regular snapshot files are allowed')
            files[member.name] = source.extractfile(member).read()
    required = {'src/knowledge_forge/__init__.py', 'src/knowledge_forge/protected_operator_cli.py',
                'src/knowledge_forge/generic_approval_reviewer.py',
                'src/knowledge_forge/generic_approval_client.py', 'src/knowledge_forge/generic_approval_mcp.py',
                'scripts/install_protected.py',
                'install.sh', 'LICENSE', 'NOTICE'}
    required |= {'deploy/linux-protected-operator/' + name for name in (*UNITS, REVIEWER_UNIT,
                  'knowledge-forge-runtime.conf', 'operator.json.example')}
    if not required.issubset(files):
        raise DeploymentError('Pinned snapshot is missing deployment components')
    # Refuse running an uncommitted helper or wrapper under a previously reviewed pin.
    for relative in ('scripts/install_protected.py', 'install.sh'):
        path = root / relative
        if path.is_symlink() or path.read_bytes() != files[relative]:
            raise DeploymentError(f'{relative} differs from the reviewed commit; use a reviewed clean checkout')
    return files


class LinuxHost:
    def account(self, name):
        import grp
        import pwd
        try:
            record = pwd.getpwnam(name)
        except KeyError:
            return None
        groups = [grp.getgrgid(gid).gr_name for gid in os.getgrouplist(name, record.pw_gid)]
        return {'name': name, 'uid': record.pw_uid, 'gid': record.pw_gid,
                'shell': record.pw_shell, 'home': record.pw_dir, 'groups': groups}

    def group(self, name):
        import grp
        try:
            group = grp.getgrnam(name)
        except KeyError:
            return None
        return {'gid': group.gr_gid, 'members': group.gr_mem}

    def password_locked(self, name):
        # Read only the lock prefix; never print, retain or return password hashes.
        value = subprocess.check_output(['/usr/bin/getent', 'shadow', name], env=ENV).decode().split(':')[1]
        return value.startswith(('!', '*'))

    def primary_users(self, gid):
        import pwd
        return {entry.pw_name for entry in pwd.getpwall() if entry.pw_gid == gid}

    def run(self, args):
        subprocess.run(args, env=ENV, check=True)

    def owner(self, path, uid, gid):
        os.chown(path, uid, gid, follow_symlinks=False)

    def unit_load_state(self, name):
        result = subprocess.run(['/usr/bin/systemctl', 'show', '--property=LoadState', '--value', name],
                                env=ENV, capture_output=True, text=True)
        value = result.stdout.strip()
        if value == 'not-found':
            return value
        result.check_returncode()
        return value


def safe_ancestors(path: Path):
    """Validate every existing ancestor without following directory symlinks."""
    if not path.is_absolute() or '..' in path.parts:
        raise DeploymentError('Deployment paths must be absolute and bounded')
    for node in reversed((path, *path.parents)):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise DeploymentError(f'Unsafe protected directory or ancestry: {node}')


def destinations():
    return [CODE, STATE, CONFIG, TMPFILES, USER / REVIEWER_UNIT,
            *(SYSTEM / name for name in UNITS)]


def preflight(host, reviewer, *, mutation):
    if sys.platform != 'linux':
        raise DeploymentError('Protected deployment requires Linux/systemd')
    if not Path('/run/systemd/system').is_dir():
        raise DeploymentError('A running systemd system manager is required (including on WSL)')
    for path in ('/usr/bin/python3', '/usr/bin/git', '/usr/bin/systemctl', '/usr/bin/zenity',
                 '/usr/bin/getent', '/usr/sbin/runuser', '/usr/sbin/groupadd',
                 '/usr/sbin/useradd', '/usr/sbin/usermod', '/usr/sbin/nologin',
                 '/usr/bin/systemd-analyze'):
        if not os.access(path, os.X_OK):
            raise DeploymentError(f'Missing system prerequisite: {path}; install it independently first')
    human = host.account(reviewer)
    if human is None or human['uid'] == 0 or reviewer in ('kf-agent', 'kf-operator'):
        raise DeploymentError('--reviewer must name an existing, distinct non-root human account')
    if human['shell'].endswith(('nologin', '/false')):
        raise DeploymentError('Reviewer must have an interactive human session')
    if host.account('kf-reviewers') is not None:
        raise DeploymentError('An account named kf-reviewers already exists; review independently')
    roles = [host.account(name) for name in ('kf-agent', 'kf-operator')]
    for name, account in zip(('kf-agent', 'kf-operator'), roles):
        group = host.group(name)
        if account is None:
            if group is not None:
                raise DeploymentError(f'Group {name} exists without its service account; review manually')
            continue
        if account['uid'] in (0, human['uid']):
            raise DeploymentError(f'{name} requires a distinct non-root UID')
        if group is None or account['gid'] == 0 or account['gid'] != group['gid']:
            raise DeploymentError(f'{name} must use its dedicated primary group')
        if host.primary_users(group['gid']) != {name}:
            raise DeploymentError(f'{name} primary group is shared with another account')
        if account['shell'] not in ('/usr/sbin/nologin', '/sbin/nologin'):
            raise DeploymentError(f'{name} must have a nologin shell')
        if account['home'] != '/nonexistent' or set(account['groups']) != {name}:
            raise DeploymentError(f'{name} must have /nonexistent home and no supplementary groups')
        if group['members'] and set(group['members']) - {name}:
            raise DeploymentError(f'Unexpected members of {name}; review manually')
        if mutation and not host.password_locked(name):
            raise DeploymentError(f'{name} password must be locked')
    if all(roles) and roles[0]['uid'] == roles[1]['uid']:
        raise DeploymentError('Agent and operator UIDs must differ')
    reviewers = host.group('kf-reviewers')
    if reviewers and set(reviewers['members']) - {reviewer}:
        raise DeploymentError('Existing reviewer group contains other members; review independently')
    if reviewers:
        if reviewers['gid'] == 0 or host.primary_users(reviewers['gid']):
            raise DeploymentError('Reviewer group must not be a primary group; review all identities')
    for path in destinations():
        safe_ancestors(path.parent)
        if path.exists() or path.is_symlink():
            raise DeploymentError(f'Existing deployment target; refusing overwrite: {path}')
    safe_ancestors(RUNTIME)
    if RUNTIME.exists():
        # A fresh deployment may reuse an empty root-owned traversal directory.
        if any(RUNTIME.iterdir()):
            raise DeploymentError('Runtime directory is not empty; review existing sockets first')
    return human


def launcher(module):
    return ("#!/bin/sh\nexport PATH=/usr/bin:/bin\n"
            "exec /usr/bin/python3 -I -B -c "
            "'import sys; sys.path.insert(0,\"/opt/toadaid-kf-operator/src\"); "
            f"from knowledge_forge.{module} import main; raise SystemExit(main())' \"$@\"\n").encode()


def write_new(path, data, mode=0o644):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for parent in reversed(missing):
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
    with path.open('xb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    path.chmod(mode)


def deployment_plan(commit, reviewer, enable):
    return {'operation': 'ADMIN_PROTECTED_DEPLOYMENT_PLAN', 'reviewed_commit': commit,
            'reviewer': reviewer, 'code_root': str(CODE), 'state_root': str(STATE),
            'configuration': str(CONFIG), 'system_units': list(UNITS),
            'reviewer_unit': str(USER / REVIEWER_UNIT),
            'system_sockets_enable_requested': enable,
            'accounts': 'Create missing roles; verify existing locked non-login roles',
            'reviewer_membership': 'Add only the explicitly selected human to kf-reviewers',
            'human_session': 'Fresh login; human explicitly starts/enables desktop broker',
            'protected_deployment_verified': False, 'promotion_performed': False,
            'agent_settings_changed': False, 'agent_migrated': False,
            'existing_deployment_policy': 'Refuse; preserve state and review upgrades/recovery manually'}


def deploy(host, files, commit, reviewer, *, enable=False):
    """Caller must perform root/TTY confirmation before entering mutation phase."""
    human = preflight(host, reviewer, mutation=True)
    # Check systemd availability before any account or filesystem changes.
    host.run(['/usr/bin/systemctl', 'show', '--property=Version', '--value'])
    for unit in UNITS:
        if host.unit_load_state(unit) != 'not-found':
            raise DeploymentError(f'Systemd already knows {unit}; inspect existing installation manually')
    for name in ('kf-agent', 'kf-operator'):
        if host.account(name) is None:
            host.run(['/usr/sbin/groupadd', '--system', name])
            host.run(['/usr/sbin/useradd', '--system', '--gid', name, '--groups', '',
                      '--no-create-home', '--home-dir', '/nonexistent', '--shell',
                      '/usr/sbin/nologin', '--password', '!', name])
    if host.group('kf-reviewers') is None:
        host.run(['/usr/sbin/groupadd', '--system', 'kf-reviewers'])
    # Recheck identities after NSS/account creation; never guess assigned IDs.
    agent, operator = host.account('kf-agent'), host.account('kf-operator')
    if not agent or not operator or len({agent['uid'], operator['uid'], human['uid'], 0}) != 4:
        raise DeploymentError('Created role UIDs are invalid; independent recovery required')
    for name, account in (('kf-agent', agent), ('kf-operator', operator)):
        if not host.password_locked(name) or account['shell'] not in ('/usr/sbin/nologin', '/sbin/nologin') or set(account['groups']) != {name}:
            raise DeploymentError(f'Created account {name} failed restriction checks')
    host.run(['/usr/sbin/usermod', '--append', '--groups', 'kf-reviewers', reviewer])

    CODE.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    stage = Path(tempfile.mkdtemp(prefix='.knowledge-forge-install-', dir=CODE.parent))
    try:
        deployed_hashes = {}
        for relative, payload in files.items():
            if relative.startswith('src/knowledge_forge/') or relative in ('LICENSE', 'NOTICE'):
                write_new(stage / relative, payload)
                deployed_hashes[relative] = hashlib.sha256(payload).hexdigest()
        modules = {'knowledge-forge-protected-operator': 'protected_operator_cli',
                   'knowledge-forge-review-daemon': 'generic_approval_reviewer',
                   'knowledge-forge-request': 'generic_approval_client',
                   'knowledge-forge-approval-mcp': 'generic_approval_mcp'}
        for name, module in modules.items():
            payload = launcher(module)
            write_new(stage / 'bin' / name, payload, 0o755)
            deployed_hashes['bin/' + name] = hashlib.sha256(payload).hexdigest()
        write_new(stage / 'DEPLOYMENT.json', (json.dumps({'reviewed_commit': commit,
                  'installed_sha256': deployed_hashes}, sort_keys=True, indent=2) + '\n').encode())
        for directory, _, _ in os.walk(stage):
            Path(directory).chmod(0o755)
        stage.rename(CODE)
    finally:
        if stage.exists():
            shutil.rmtree(stage)

    config = {'schema_version': '0.1', 'operator_user': 'kf-operator', 'agent_user': 'kf-agent',
              'state_root': str(STATE), 'code_root': str(CODE)}
    write_new(CONFIG, (json.dumps(config, sort_keys=True, indent=2) + '\n').encode())
    STATE.mkdir(mode=0o700)
    host.owner(STATE, operator['uid'], operator['gid'])
    # Only protected installed code is executed, as the non-root operator UID.
    host.run(['/usr/sbin/runuser', '--user', 'kf-operator', '--',
              str(CODE / 'bin/knowledge-forge-protected-operator'), 'initialize-state'])

    deploy_root = 'deploy/linux-protected-operator/'
    for name in UNITS:
        write_new(SYSTEM / name, files[deploy_root + name])
    write_new(USER / REVIEWER_UNIT, files[deploy_root + REVIEWER_UNIT])
    write_new(TMPFILES, files[deploy_root + 'knowledge-forge-runtime.conf'])
    RUNTIME.mkdir(mode=0o755, exist_ok=True)
    RUNTIME.chmod(0o755)
    host.run(['/usr/bin/systemd-analyze', 'verify', *(str(SYSTEM / name) for name in UNITS)])
    host.run(['/usr/bin/systemctl', 'daemon-reload'])
    # Installed units are dormant unless the administrator explicitly opts in.
    if enable:
        host.run(['/usr/bin/systemctl', 'enable', '--now', *SOCKETS])
        for unit in SOCKETS:
            host.run(['/usr/bin/systemctl', 'is-active', '--quiet', unit])
    return {**deployment_plan(commit, reviewer, enable),
            'operation': 'ADMIN_PROTECTED_DEPLOYMENT_INSTALLED',
            'system_sockets_active_checked': enable,
            'reviewer_broker_started': False,
            'next_steps': [
                'Human reviewer logs out/in to refresh kf-reviewers membership.',
                'In the human graphical session: systemctl --user daemon-reload',
                'Then: systemctl --user enable --now knowledge-forge-reviewer.service',
                'Independently isolate actual model execution/request transport as kf-agent; no configuration was changed.',
                'Verify actual UID/ACL/desktop/sandbox boundaries with synthetic packages before any real promotion.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--enable-services', action='store_true')
    args = parser.parse_args(argv)
    mutated = False
    try:
        if sys.platform != 'linux':
            raise DeploymentError('Protected deployment requires Linux/systemd')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*[$]?', args.reviewer):
            raise DeploymentError('Invalid reviewer account name')
        if not args.plan and (os.geteuid() != 0 or not sys.stdin.isatty() or not sys.stdout.isatty()):
            raise DeploymentError('Run as root from an interactive independent administrator console; --plan is read-only')
        if sys.version_info < (3, 10):
            raise DeploymentError('System /usr/bin/python3 must be Python 3.10 or newer')
        files = pinned_files(args.source_root, args.commit)
        host = LinuxHost()
        preflight(host, args.reviewer, mutation=not args.plan)
        print(json.dumps(deployment_plan(args.commit, args.reviewer, args.enable_services), indent=2))
        if args.plan:
            return 0
        print('Independent administrator: review the pinned source, UID isolation and deployment plan.')
        if input('Type DEPLOY PROTECTED FORGE to install: ') != 'DEPLOY PROTECTED FORGE':
            print('Cancelled; nothing changed.')
            return 1
        mutated = True
        print(json.dumps(deploy(host, files, args.commit, args.reviewer, enable=args.enable_services), indent=2))
        print('INSTALLED, NOT CERTIFIED. No package submitted, approved or promoted.')
        return 0
    except (ValueError, KeyError, IndexError, tarfile.TarError, OSError,
            subprocess.SubprocessError, EOFError, KeyboardInterrupt) as exc:
        print(json.dumps({'status': 'DEPLOYMENT_REFUSED_OR_INCOMPLETE', 'error': str(exc),
                          'partial_changes_possible': mutated, 'promotion_performed': False,
                          'protected_deployment_verified': False,
                          'recovery': 'Inspect partial accounts/files/units manually. Preserve protected state; do not blindly retry or delete it.'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
