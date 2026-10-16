#!/usr/bin/env python3
"""Reviewed administrator entry point. No migration is run by importing this file."""
from pathlib import Path, PurePosixPath
import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import types


def snapshot(root, commit):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Exact reviewed 40-character Git commit required')
    root = root.resolve(strict=True)
    if '*' in str(root):
        raise ValueError('Wildcard checkout paths refused')
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1'}
    git = ['/usr/bin/git', '-c', 'safe.directory=' + str(root), '--no-replace-objects', '-C', str(root)]
    actual = subprocess.check_output(git + ['rev-parse', '--verify', commit + '^{commit}'], env=env).decode().strip()
    if actual != commit:
        raise ValueError('Reviewed commit mismatch')
    raw = subprocess.check_output(git + ['archive', '--format=tar', commit, '--',
        'src/knowledge_forge', 'deploy/linux-protected-operator', 'scripts/migrate_codex_native.py', 'install.sh'], env=env)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('Archive exceeds 32 MiB')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for item in archive.getmembers():
            normalized = item.name.rstrip('/') if item.isdir() else item.name
            path = PurePosixPath(normalized)
            if path.is_absolute() or '..' in path.parts or '\\' in item.name or str(path) != normalized:
                raise ValueError('Unsafe archive path')
            if item.isdir():
                continue
            if not item.isfile() or item.name in files:
                raise ValueError('Archive contains symlink, duplicate or non-regular artifact')
            files[item.name] = archive.extractfile(item).read()
    for name in ('scripts/migrate_codex_native.py', 'src/knowledge_forge/native_deployment.py', 'install.sh'):
        path = root / name
        if path.is_symlink() or files.get(name) != path.read_bytes():
            raise ValueError('Installer differs from reviewed commit: ' + name)
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--commit', required=True)
    parser.add_argument('--reviewer', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--plan', '--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--preflight', action='store_true')
    parser.add_argument('--enable-socket', action='store_true')
    parser.add_argument('--workspace-root', type=Path)
    parser.add_argument('--schema-dir', type=Path)
    parser.add_argument('--mcp-config-json', type=Path)
    parser.add_argument('--provider-status-json', type=Path)
    args = parser.parse_args(argv)
    try:
        if sys.platform != 'linux':
            raise ValueError('Linux/systemd required')
        if args.apply and (os.geteuid() != 0 or not sys.stdin.isatty() or not sys.stdout.isatty()):
            raise ValueError('Apply requires an independent interactive root administrator console')
        files = snapshot(args.source_root, args.commit)
        # Execute only the reviewed archive bytes, not a live mutable module.
        module = types.ModuleType('kf_reviewed_native_migration')
        sys.modules[module.__name__] = module
        exec(compile(files['src/knowledge_forge/native_deployment.py'], '<reviewed-native-migration>', 'exec'), module.__dict__)
        def evidence(path):
            if path is None:
                return None
            resolved = path.resolve(strict=True)
            if any(resolved == protected or protected in resolved.parents for protected in
                   (module.Layout().state, module.Layout().operator, module.Layout().native)):
                raise ValueError('Evidence must be explicit public export, never protected state/credential/receipt contents')
            return json.loads(module.read_public(path))
        if args.preflight:
            if args.workspace_root is None or not args.workspace_root.is_absolute():
                raise ValueError('Preflight requires explicit absolute --workspace-root')
            result = module.preflight(files, args.commit, args.reviewer, workspace=args.workspace_root,
                 schema_dir=args.schema_dir, mcp_config=evidence(args.mcp_config_json),
                 provider_status=evidence(args.provider_status_json))
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result['status'] == 'READY' else 2
        plan = module.migrate(files, args.commit, args.reviewer, enable=args.enable_socket)
        if not args.apply:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        print(json.dumps(plan, indent=2, sort_keys=True))
        if input('Type INSTALL DORMANT CODEX LANE to apply: ') != 'INSTALL DORMANT CODEX LANE':
            print('Cancelled; nothing changed.')
            return 1
        result = module.migrate(files, args.commit, args.reviewer, apply=True)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError, EOFError, KeyboardInterrupt) as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': str(exc), 'promotion_performed': False,
                          'personal_added': False, 'state_reinitialized': False,
                          'recovery': 'Preserve all existing state; inspect a partial native installation independently.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
