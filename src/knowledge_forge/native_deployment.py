"""Additive, administrator-reviewed Codex lane installation and read-only gates.

No identity delegation is supplied. A human-UID Codex process cannot use the
launcher as kf-agent; deployment is dormant until a separately reviewed host
transport exists. Never read credential, registry, ledger or receipt contents.
"""
from __future__ import annotations

from dataclasses import dataclass
import configparser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

SOCKET_NAME = 'knowledge-forge-codex-operator.socket'
SERVICE_NAME = 'knowledge-forge-codex-operator@.service'
MAX_BYTES = 32 * 1024 * 1024
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
# These state/proof implementations must be unchanged across the additive lane.
COMPATIBILITY = ('registry.py', 'lifecycle.py', 'operator_promotion.py',
                 'protected_operator_service.py', 'codex_credential_registry.py', 'codex_p256.py')


class NativeDeploymentError(ValueError):
    pass


@dataclass(frozen=True)
class Layout:
    operator: Path = Path('/opt/toadaid-kf-operator')
    native: Path = Path('/opt/toadaid-kf-agent')
    config: Path = Path('/etc/toadaid-kf/operator.json')
    state: Path = Path('/var/lib/toadaid-kf-operator')
    units: Path = Path('/etc/systemd/system')
    runtime: Path = Path('/run/toadaid-knowledge-forge')

    @property
    def socket(self):
        return self.runtime / 'codex-operator.sock'

    @property
    def receipt(self):
        return self.native / 'NATIVE_MIGRATION.json'


class Host:
    def metadata(self, path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        return {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
                'kind': 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode)
                else 'socket' if stat.S_ISSOCK(info.st_mode) else 'unsafe', 'size': info.st_size}

    def identities(self, reviewer):
        import grp
        import pwd
        result = {}
        for name in ('kf-agent', 'kf-operator', reviewer):
            entry = pwd.getpwnam(name)
            result[name] = {'uid': entry.pw_uid, 'gid': entry.pw_gid, 'shell': entry.pw_shell, 'home': entry.pw_dir,
                            'groups': {grp.getgrgid(gid).gr_name for gid in os.getgrouplist(name, entry.pw_gid)}}
        result['reviewer_gid'] = grp.getgrnam('kf-reviewers').gr_gid
        for name in ('kf-agent', 'kf-operator'):
            if grp.getgrnam(name).gr_gid != result[name]['gid']:
                raise NativeDeploymentError('Service roles require dedicated primary groups')
        return result

    def systemd(self):
        return sys.platform == 'linux' and Path('/run/systemd/system').is_dir()

    def observer_uid(self):
        return os.geteuid()

    def run(self, args):
        subprocess.run(args, env=ENV, check=True)

    def unit_absent(self, name):
        query = name.replace('@.', '@native-install-check.')
        result = subprocess.run(['/usr/bin/systemctl', 'show', '--property=LoadState', '--value', query],
                                capture_output=True, text=True, env=ENV)
        if result.returncode not in (0, 4) or result.stdout.strip() != 'not-found':
            return False
        enabled = subprocess.run(['/usr/bin/systemctl', 'is-enabled', name], capture_output=True, text=True, env=ENV)
        return enabled.returncode in (1, 4) and enabled.stdout.strip() in ('', 'not-found', 'disabled')


    def unit_dormant(self, name):
        enabled = subprocess.run(['/usr/bin/systemctl', 'is-enabled', name], capture_output=True, text=True, env=ENV)
        allowed = {'disabled'} if name.endswith('.socket') else {'static', 'disabled'}
        if enabled.returncode not in (0, 1) or enabled.stdout.strip() not in allowed:
            return False
        query = name.replace('@.', '@native-install-check.')
        active = subprocess.run(['/usr/bin/systemctl', 'is-active', query], capture_output=True, text=True, env=ENV)
        return active.returncode == 3 and active.stdout.strip() == 'inactive'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_public(path, limit=256 * 1024):
    # Never call this on protected state. All callers below use public code,
    # non-secret configuration, schema or explicit sanitized evidence only.
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise NativeDeploymentError('Public artifact must be a bounded regular file: ' + str(path))
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise NativeDeploymentError('Artifact changed while opening')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            value = stream.read(limit + 1)
        if len(value) > limit or os.fstat(fd).st_mtime_ns != current.st_mtime_ns:
            raise NativeDeploymentError('Artifact changed or exceeded limit')
        return value
    finally:
        os.close(fd)


def check(host, path, *, kind, uid, mode, gid=None):
    value = host.metadata(path)
    if value is None or value['kind'] != kind or value['uid'] != uid or value['mode'] != mode:
        raise NativeDeploymentError('Owner/mode/type mismatch: ' + str(path))
    if gid is not None and value['gid'] != gid:
        raise NativeDeploymentError('Group mismatch: ' + str(path))
    return value


def ancestry(host, path):
    if not path.is_absolute() or '..' in path.parts:
        raise NativeDeploymentError('Unsafe absolute deployment path')
    for parent in reversed((path, *path.parents)):
        value = host.metadata(parent)
        if value is not None and (value['kind'] != 'directory' or value['uid'] != 0 or value['mode'] & 0o022):
            raise NativeDeploymentError('Symlink, non-root or writable ancestry: ' + str(parent))


def relative(value):
    if not isinstance(value, str):
        raise NativeDeploymentError('Artifact path must be text')
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value or str(path) != value:
        raise NativeDeploymentError('Unsafe artifact path')
    return path


def baseline(host, layout, reviewer, archive):
    if not host.systemd():
        raise NativeDeploymentError('Linux with a running systemd manager is required')
    ids = host.identities(reviewer)
    agent, operator, human = (ids[name] for name in ('kf-agent', 'kf-operator', reviewer))
    if reviewer in ('kf-agent', 'kf-operator') or len({0, agent['uid'], operator['uid'], human['uid']}) != 4:
        raise NativeDeploymentError('Agent/operator/reviewer must have distinct non-root UIDs')
    for account in (agent, operator):
        if account.get('shell') not in ('/usr/sbin/nologin', '/sbin/nologin') or account.get('home') != '/nonexistent':
            raise NativeDeploymentError('Existing service roles must remain non-login with /nonexistent homes')
    if agent['groups'] != {'kf-agent'} or operator['groups'] != {'kf-operator'} or 'kf-agent' in human['groups']:
        raise NativeDeploymentError('Unexpected service privileges or human agent-group membership')
    if len({agent['gid'], operator['gid'], ids['reviewer_gid']}) != 3:
        raise NativeDeploymentError('Service and reviewer socket groups must be distinct')
    if 'kf-reviewers' not in human['groups']:
        raise NativeDeploymentError('Selected human is not a reviewer')
    for path in (layout.operator, layout.config.parent, layout.state.parent, layout.units, layout.native.parent, layout.runtime):
        ancestry(host, path)
    check(host, layout.runtime, kind='directory', uid=0, mode=0o755)
    check(host, layout.operator, kind='directory', uid=0, mode=0o755)
    check(host, layout.config, kind='file', uid=0, mode=0o644)
    expected_config = {'schema_version': '0.1', 'agent_user': 'kf-agent', 'operator_user': 'kf-operator',
                       'code_root': str(layout.operator), 'state_root': str(layout.state)}
    if json.loads(read_public(layout.config)) != expected_config:
        raise NativeDeploymentError('Unknown protected configuration; no automatic migration')
    # State is inspected ONLY via metadata. No state file is opened or hashed.
    check(host, layout.state, kind='directory', uid=operator['uid'], gid=operator['gid'], mode=0o700)
    for name in ('registry.json', 'authorizations.sqlite3'):
        check(host, layout.state / name, kind='file', uid=operator['uid'], gid=operator['gid'], mode=0o600)
    for name in ('receipts', 'lifecycles'):
        check(host, layout.state / name, kind='directory', uid=operator['uid'], gid=operator['gid'], mode=0o700)
    for index, path in enumerate(layout.state.rglob('*')):
        if index >= 100000:
            raise NativeDeploymentError('State metadata inventory exceeds bounded inspection')
        meta = host.metadata(path)
        kind = meta['kind'] if meta else None
        if kind not in ('file', 'directory'):
            raise NativeDeploymentError('Unsafe state metadata; no contents inspected')
        check(host, path, kind=kind, uid=operator['uid'], gid=operator['gid'],
              mode=0o700 if kind == 'directory' else 0o600)
    human_socket = layout.runtime / 'operator.sock'
    if host.metadata(human_socket) is not None:
        check(host, human_socket, kind='socket', uid=operator['uid'], gid=ids['reviewer_gid'], mode=0o660)
    manifest_path = layout.operator / 'DEPLOYMENT.json'
    check(host, manifest_path, kind='file', uid=0, mode=0o644)
    manifest = json.loads(read_public(manifest_path))
    if set(manifest) != {'reviewed_commit', 'installed_sha256'} or not re.fullmatch('[0-9a-f]{40}', manifest['reviewed_commit']):
        raise NativeDeploymentError('Unknown operator installation manifest')
    hashes = manifest['installed_sha256']
    if not isinstance(hashes, dict) or not 1 <= len(hashes) <= 512:
        raise NativeDeploymentError('Invalid installed public artifact inventory')
    for name, expected in hashes.items():
        relative(name)
        path = layout.operator / name
        ancestry(host, path.parent)
        check(host, path, kind='file', uid=0, mode=0o755 if name.startswith('bin/') else 0o644)
        if digest(read_public(path, MAX_BYTES)) != expected:
            raise NativeDeploymentError('Installed public code hash mismatch: ' + name)
    actual = set()
    for path in layout.operator.rglob('*'):
        meta = host.metadata(path)
        if meta['kind'] not in ('file', 'directory'):
            raise NativeDeploymentError('Symlink or non-regular artifact in installed public code')
        if meta['kind'] == 'directory':
            ancestry(host, path)
            check(host, path, kind='directory', uid=0, mode=0o755)
        elif meta['kind'] == 'file':
            actual.add(path.relative_to(layout.operator).as_posix())
    if actual != set(hashes) | {'DEPLOYMENT.json'}:
        raise NativeDeploymentError('Unknown or partial public operator installation')
    # The initial installer copies the complete module tree and four launchers.
    # A self-consistent but truncated manifest is still a partial installation.
    added = {'src/knowledge_forge/codex_native_operator.py', 'src/knowledge_forge/native_deployment.py'}
    expected_modules = {name for name in archive if name.startswith('src/knowledge_forge/') and name.endswith('.py')} - added
    expected_launchers = {'bin/knowledge-forge-' + name for name in
                          ('protected-operator', 'review-daemon', 'request', 'approval-mcp')}
    if not (expected_modules | expected_launchers) <= set(hashes):
        raise NativeDeploymentError('Incomplete existing protected module/launcher inventory')
    required = {'src/knowledge_forge/' + name for name in COMPATIBILITY}
    shared = {name for name in hashes if name.startswith('src/knowledge_forge/') and name.endswith('.py')}
    # These client-only entry points do not participate in governed state writes.
    shared -= {'src/knowledge_forge/codex_enrollment.py', 'src/knowledge_forge/codex_mcp_server.py'}
    for key in required | shared:
        if key not in archive or hashes.get(key) != digest(archive[key]):
            raise NativeDeploymentError('Incompatible state/proof engine; separate code review required: ' + key)
    for name in ('knowledge-forge-operator@.service', 'knowledge-forge-operator.socket'):
        path = layout.units / name
        check(host, path, kind='file', uid=0, mode=0o644)
        if read_public(path) != archive['deploy/linux-protected-operator/' + name]:
            raise NativeDeploymentError('Unknown existing human service/socket identity')
    return ids


def launch(module, *, transport=False):
    guard = ('import os,pwd; u=pwd.getpwnam("kf-agent").pw_uid; '
             'sys.exit("transport must run as non-root kf-agent") if u==0 or os.getuid()!=u or os.geteuid()!=u else None; ') if transport else ''
    return ('#!/bin/sh\nexport PATH=/usr/bin:/bin\nexec /usr/bin/python3 -I -B -c '
            "'import sys; " + guard + 'sys.path.insert(0,"/opt/toadaid-kf-agent/src"); '
            'from knowledge_forge.' + module + ' import main; raise SystemExit(main())\' "$@"\n').encode()


def artifacts(archive):
    for name in archive:
        relative(name)
    files = {'native/' + name: data for name, data in archive.items() if name.startswith('src/knowledge_forge/') and name.endswith('.py')}
    for required in ('codex_native_operator.py', 'codex_mcp_server.py', *COMPATIBILITY):
        if 'src/knowledge_forge/' + required not in archive:
            raise NativeDeploymentError('Incomplete reviewed native snapshot: ' + required)
    files['native/bin/knowledge-forge-codex-mcp'] = launch('codex_mcp_server', transport=True)
    files['native/bin/knowledge-forge-codex-operator'] = launch('codex_native_operator')
    for name in (SOCKET_NAME, SERVICE_NAME):
        files['unit/' + name] = archive['deploy/linux-protected-operator/' + name]
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(files['unit/' + SOCKET_NAME].decode('utf-8'))
        expected_socket = {'ListenStream': '/run/toadaid-knowledge-forge/codex-operator.sock',
                           'Accept': 'yes', 'SocketUser': 'kf-operator', 'SocketGroup': 'kf-agent',
                           'SocketMode': '0660', 'DirectoryMode': '0750', 'RemoveOnStop': 'yes'}
        if dict(parser['Socket']) != expected_socket:
            raise NativeDeploymentError('Native socket separation/ACL/template contract changed')
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read_string(files['unit/' + SERVICE_NAME].decode('utf-8'))
        expected_service = {'User': 'kf-operator', 'Group': 'kf-operator', 'UMask': '0077',
            'StandardInput': 'socket', 'StandardOutput': 'socket',
            'ExecStart': '/opt/toadaid-kf-agent/bin/knowledge-forge-codex-operator',
            'NoNewPrivileges': 'yes', 'ProtectSystem': 'strict', 'ProtectHome': 'yes',
            'ReadWritePaths': '/var/lib/toadaid-kf-operator', 'RestrictAddressFamilies': 'AF_UNIX',
            'CapabilityBoundingSet': '', 'AmbientCapabilities': ''}
        if any(parser['Service'].get(key) != value for key, value in expected_service.items()):
            raise NativeDeploymentError('Native operator service identity/hardening changed')
    except (configparser.Error, UnicodeError, KeyError) as exc:
        raise NativeDeploymentError('Invalid native unit contract') from exc
    return files


def target(layout, key):
    if key.startswith('native/'):
        return layout.native / key.removeprefix('native/')
    return layout.units / key.removeprefix('unit/')


def migration_receipt(commit, files, ids, reviewer):
    value = {'kind': 'KF_NATIVE_LANE_MIGRATION', 'version': 1, 'reviewed_commit': commit,
             'agent_uid': ids['kf-agent']['uid'], 'operator_uid': ids['kf-operator']['uid'],
             'reviewer_uid': ids[reviewer]['uid'], 'agent_gid': ids['kf-agent']['gid'],
             'operator_gid': ids['kf-operator']['gid'], 'reviewer_gid': ids['reviewer_gid'], 'artifacts': {name: digest(data) for name, data in sorted(files.items())},
             'actions': ['install_missing_native_snapshot', 'install_dormant_native_socket_and_template'],
             'state_contents_read': False, 'state_reinitialized': False, 'socket_enabled': False,
             'promotion_performed': False, 'personal_added': False}
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    if len(raw) > 65536:
        raise NativeDeploymentError('Migration receipt exceeds 64 KiB')
    return value


def inspect_migration(host, layout, expected, files):
    present = host.metadata(layout.native)
    unit_present = any(host.metadata(layout.units / name) is not None for name in (SOCKET_NAME, SERVICE_NAME))
    if present is None:
        if unit_present:
            raise NativeDeploymentError('Partial or unrelated native units already exist')
        if host.metadata(layout.socket) is not None:
            raise NativeDeploymentError('Unexpected existing native socket')
        return 'ABSENT'
    check(host, layout.native, kind='directory', uid=0, mode=0o755)
    if host.metadata(layout.native / '.migration-in-progress') is not None:
        raise NativeDeploymentError('Partial migration marker; independent recovery required')
    check(host, layout.receipt, kind='file', uid=0, mode=0o644)
    if json.loads(read_public(layout.receipt, 65536)) != expected:
        raise NativeDeploymentError('Changed commit, inventory or identity; refusing repeat migration')
    actual = set()
    for path in layout.native.rglob('*'):
        meta = host.metadata(path)
        if meta['kind'] == 'directory':
            ancestry(host, path)
            check(host, path, kind='directory', uid=0, mode=0o755)
        elif meta['kind'] == 'file':
            actual.add(path.relative_to(layout.native).as_posix())
        else:
            raise NativeDeploymentError('Unsafe native installation artifact')
    wanted = {key.removeprefix('native/') for key in files if key.startswith('native/')} | {'NATIVE_MIGRATION.json'}
    if actual != wanted:
        raise NativeDeploymentError('Unknown or partial native file inventory')
    for key, data in files.items():
        path = target(layout, key)
        ancestry(host, path.parent)
        check(host, path, kind='file', uid=0, mode=0o755 if key.startswith('native/bin/') else 0o644)
        if read_public(path, MAX_BYTES) != data:
            raise NativeDeploymentError('Native artifact hash mismatch: ' + key)
    return 'COMPLETED_SAME_MIGRATION'


def put_new(path, data, mode=0o644):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb', closefd=False) as output:
            output.write(data)
            output.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def migrate(archive, commit, reviewer, *, apply=False, enable=False, host=None, layout=None):
    host, layout = host or Host(), layout or Layout()
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise NativeDeploymentError('Exact reviewed 40-character commit required')
    # This release has no supported Desktop cross-UID stdio launcher. No user
    # boolean, JSON attestation or synthetic test can manufacture that authority.
    if enable:
        raise NativeDeploymentError('Native enablement blocked: verified kf-agent Desktop transport/provider unavailable; no unsafe delegation supplied')
    if apply and (sys.platform != 'linux' or os.geteuid() != 0):
        raise NativeDeploymentError('Apply requires independent Linux root administrator')
    ids = baseline(host, layout, reviewer, archive)
    files = artifacts(archive)
    receipt = migration_receipt(commit, files, ids, reviewer)
    status = inspect_migration(host, layout, receipt, files)
    result = {'status': status, 'plan_only': not apply, 'receipt': receipt,
              'native_ready': False, 'blockers': ['VERIFIED_KF_AGENT_DESKTOP_TRANSPORT', 'NATIVE_PROVIDER', 'LIVE_UID_ACL_EXPIRY_REPLAY_TESTS']}
    if status == 'ABSENT':
        for unit in (SOCKET_NAME, SERVICE_NAME):
            if not host.unit_absent(unit):
                raise NativeDeploymentError('Existing/unknown loaded or enabled native systemd unit; no writes performed')
    if status == 'COMPLETED_SAME_MIGRATION':
        if host.metadata(layout.socket) is not None or not all(host.unit_dormant(unit) for unit in (SOCKET_NAME, SERVICE_NAME)):
            raise NativeDeploymentError('Completed migration no longer dormant; independent review required')
    if not apply or status == 'COMPLETED_SAME_MIGRATION':
        return result
    layout.native.mkdir(mode=0o700)
    put_new(layout.native / '.migration-in-progress', b'Incomplete migration: preserve protected state; inspect independently.\n')
    # Every write target is either the NEW native root or one of two absent
    # system unit files. State/config/human service files are never write targets.
    for key, data in sorted(files.items()):
        put_new(target(layout, key), data, 0o755 if key.startswith('native/bin/') else 0o644)
    for directory, _, _ in os.walk(layout.native):
        Path(directory).chmod(0o755)
    host.run(['/usr/bin/systemd-analyze', 'verify', '--man=no', str(layout.units / SERVICE_NAME), str(layout.units / SOCKET_NAME)])
    host.run(['/usr/bin/systemctl', 'daemon-reload'])
    put_new(layout.receipt, (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode())
    (layout.native / '.migration-in-progress').unlink()
    directory = os.open(layout.native, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {**result, 'status': 'INSTALLED_DORMANT_NOT_READY'}


def schema_support(directory):
    root = Path(directory)
    initialize = json.loads(read_public(root / 'v1/InitializeParams.json'))
    enroll = json.loads(read_public(root / 'v2/UserVerificationEnrollResponse.json'))
    status = json.loads(read_public(root / 'v2/UserVerificationStatusResponse.json'))
    params = json.loads(read_public(root / 'v2/UserVerificationEnrollParams.json'))
    verify_params = json.loads(read_public(root / 'v2/UserVerificationVerifyParams.json'))
    verify_response = json.loads(read_public(root / 'v2/UserVerificationVerifyResponse.json'))
    request = json.loads(read_public(root / 'ClientRequest.json', 8 * 1024 * 1024))
    if initialize['definitions']['InitializeCapabilities']['properties']['experimentalApi']['type'] != 'boolean':
        return False
    properties = enroll.get('properties', {})
    proof = verify_response['definitions']['UserVerificationProof']
    return (set(verify_params.get('required', [])) == {'challenge', 'description', 'title'} and
            verify_params.get('additionalProperties') is False and
            set(proof.get('required', [])) == {'credentialId', 'signature'} and
            proof.get('additionalProperties') is False and
            set(properties) >= {'credentialId', 'publicKey', 'algorithm'} and
            'credentialId' in enroll.get('required', []) and params.get('additionalProperties') is False and
            set(status.get('properties', {})) >= {'credentialId', 'unavailableReason'} and
            all(method in json.dumps(request) for method in ('userVerification/enroll', 'userVerification/status', 'userVerification/verify')))


def preflight(archive, commit, reviewer, *, workspace, schema_dir=None, mcp_config=None,
              provider_status=None, host=None, layout=None):
    host, layout = host or Host(), layout or Layout()
    gates = {}
    def gate(name, action):
        try:
            value = action()
            gates[name] = {'status': 'PASS' if value else 'FAIL'}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            gates[name] = {'status': 'UNKNOWN', 'reason': type(exc).__name__}
    gate('existing_protected_installation', lambda: bool(baseline(host, layout, reviewer, archive)))
    ids = None
    try:
        ids = host.identities(reviewer)
    except (OSError, KeyError, ValueError):
        pass
    files = artifacts(archive)
    for key in ('native/bin/knowledge-forge-codex-mcp', 'unit/' + SOCKET_NAME, 'unit/' + SERVICE_NAME):
        path = target(layout, key)
        def identity(path=path, key=key):
            ancestry(host, path.parent)
            check(host, path, kind='file', uid=0, mode=0o755 if key.startswith('native/bin/') else 0o644)
            return read_public(path) == files[key]
        gate(key, identity)
        gates[key]['expected_sha256'] = digest(files[key])
    if ids:
        agent, operator = ids['kf-agent'], ids['kf-operator']
        gate('native_socket_exact_acl', lambda: bool(check(host, layout.socket, kind='socket', uid=operator['uid'], gid=agent['gid'], mode=0o660)))
        gate('credential_metadata_only', lambda: bool(check(host, layout.state / 'codex-credentials.json', kind='file', uid=operator['uid'], gid=operator['gid'], mode=0o600)))
        gate('preflight_process_uid', lambda: host.observer_uid() == agent['uid'] and agent['uid'] != 0)
        gate('same_completed_migration', lambda: inspect_migration(host, layout, migration_receipt(commit, files, ids, reviewer), files) == 'COMPLETED_SAME_MIGRATION')
    else:
        for name in ('native_socket_exact_acl', 'credential_metadata_only', 'preflight_process_uid', 'same_completed_migration'):
            gates[name] = {'status': 'UNKNOWN'}
    gates['current_transport_uid'] = {'status': 'UNKNOWN', 'reason': 'Observer UID is not evidence of the actual MCP kernel peer UID'}
    gates['live_operator_service_uid'] = {'status': 'UNKNOWN', 'reason': 'Pinned unit identity is not a running-process UID observation'}
    expected = {'command': '/opt/toadaid-kf-agent/bin/knowledge-forge-codex-mcp',
                'args': ['--workspace-root', str(workspace), '--operator-socket', '/run/toadaid-knowledge-forge/codex-operator.sock']}
    gate('exact_mcp_configuration', lambda: mcp_config['mcp_servers']['knowledge_forge_approval'] == expected)
    gates['live_mcp_configuration_presence'] = {'status': 'UNKNOWN', 'reason': 'Sanitized JSON export checks arguments only; live client/session registration is not observed'}
    gate('experimental_protocol_schema', lambda: schema_support(schema_dir))
    if provider_status is None:
        gates['native_provider'] = {'status': 'UNKNOWN'}
    else:
        gate('native_provider', lambda: isinstance(provider_status, dict) and
             set(provider_status) <= {'credentialId', 'unavailableReason', 'unavailableMessage'} and
             provider_status.get('unavailableReason') is None and
             isinstance(provider_status.get('credentialId'), str) and bool(provider_status['credentialId']))
    if isinstance(provider_status, dict) and provider_status.get('unavailableReason') in ('providerUnavailable', 'credentialMissing', 'biometricsUnavailable'):
        gates['native_provider']['reason'] = provider_status['unavailableReason']
    gate('proof_only_endpoint_no_fallback', lambda: b'Service=' not in files['unit/' + SOCKET_NAME] and
         b'codex_native_operator' in files['native/bin/knowledge-forge-codex-operator'])
    gates['live_native_provider_verification'] = {'status': 'UNKNOWN', 'reason': 'Public status export is evidence only, not fresh authenticated device verification'}
    # Evidence from config/schema presence is not proof of these runtime gates.
    gates['safe_desktop_uid_launch_mechanism'] = {'status': 'UNKNOWN', 'reason': 'No supported cross-UID Desktop launch mechanism in this repository'}
    gates['live_expiry_and_replay_rejection'] = {'status': 'UNKNOWN', 'reason': 'Requires separate real transport tests; never inferred from metadata'}
    return {'status': 'READY' if all(v['status'] == 'PASS' for v in gates.values()) else 'BLOCKED',
            'gates': gates, 'expected_agent_uid': ids['kf-agent']['uid'] if ids else None,
            'expected_operator_uid': ids['kf-operator']['uid'] if ids else None,
            'observed_transport_uid': None, 'current_preflight_uid': host.observer_uid(), 'expected_mcp': expected,
            'expected_socket_acl': {'path': str(layout.socket), 'owner_uid': ids['kf-operator']['uid'] if ids else None,
                                    'group_gid': ids['kf-agent']['gid'] if ids else None, 'mode': '0660'},
            'reviewed_commit': commit, 'credential_contents_read': False,
            'promotion_performed': False, 'personal_added': False}
