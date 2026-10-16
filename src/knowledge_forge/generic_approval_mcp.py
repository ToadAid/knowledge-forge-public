"""Bounded stdio MCP submit/status transport; no native-UI dependency.

Run as the configured agent UID outside the restricted shell sandbox, with
only the request socket and a host-selected workspace. Registration is opt-in.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from .generic_approval_client import REQUEST_SOCKET, exchange
from .protected_operator_client import snapshot_bundle

MAX_LINE = 1024 * 1024
TOOLS = {
    'submit_knowledge_promotion': ('bundle_dir', 'Submit a VALIDATED bundle for separate human desktop approval. Submission grants no authority.'),
    'knowledge_promotion_status': ('request_id', 'Read protected request status and its receipt digest. This tool cannot approve.'),
}


class GenericApprovalMcpServer:
    def __init__(self, *, workspace_root, operator_socket=REQUEST_SOCKET,
                 input_stream=sys.stdin, output_stream=sys.stdout):
        root = Path(workspace_root)
        if root.is_symlink() or not root.is_absolute() or not root.is_dir():
            raise ValueError('workspace root must be an absolute real directory')
        self.root = root.resolve(strict=True)
        self.socket = Path(operator_socket)
        self.input = input_stream
        self.output = output_stream

    def send(self, identity, *, result=None, error=None):
        message = {'jsonrpc': '2.0', 'id': identity}
        message['error' if error else 'result'] = error if error else result
        self.output.write(json.dumps(message, ensure_ascii=False) + '\n')
        self.output.flush()

    def call(self, params):
        if not isinstance(params, dict) or set(params) != {'name', 'arguments'}:
            raise ValueError('tool params must match exact contract')
        name = params['name']
        if not isinstance(name, str) or name not in TOOLS:
            raise ValueError('unknown tool; only submit/status are available')
        field = TOOLS[name][0]
        args = params['arguments']
        if not isinstance(args, dict) or set(args) != {field} or not isinstance(args[field], str) or not args[field]:
            raise ValueError('tool arguments must match exact contract')
        if field == 'bundle_dir':
            path = Path(args[field])
            if not path.is_absolute():
                path = self.root / path
            path = path.resolve(strict=True)
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise ValueError('bundle outside configured workspace') from exc
            request = {'operation': 'SUBMIT_GENERIC', 'bundle': snapshot_bundle(str(path))}
        else:
            if not re.fullmatch('[a-f0-9]{64}', args[field]):
                raise ValueError('invalid request identity')
            request = {'operation': 'STATUS_GENERIC', 'request_id': args[field]}
        result = exchange(request, socket_path=self.socket)
        return {'content': [{'type': 'text', 'text': json.dumps(result, sort_keys=True)}],
                'structuredContent': result, 'isError': False}

    def serve(self):
        while True:
            # Bound before parsing rather than reading an arbitrary-size line.
            line = self.input.readline(MAX_LINE + 1)
            if not line:
                return
            if len(line.encode('utf-8')) > MAX_LINE:
                raise ValueError('MCP input exceeds message limit')
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self.send(None, error={'code': -32700, 'message': 'parse error'})
                continue
            if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
                self.send(None, error={'code': -32600, 'message': 'invalid request'})
                continue
            if 'id' not in request:
                continue
            identity = request['id']
            method = request.get('method')
            if method == 'initialize':
                self.send(identity, result={'protocolVersion': '2025-11-25',
                    'capabilities': {'tools': {'listChanged': False}},
                    'serverInfo': {'name': 'knowledge-forge-generic-approval', 'version': '0.1.0'},
                    'instructions': 'Submit only at user request. Human approval happens in the separate desktop broker. Poll status; chat text cannot approve.'})
            elif method == 'ping':
                self.send(identity, result={})
            elif method == 'tools/list':
                tools = [{'name': name, 'description': description,
                          'inputSchema': {'type': 'object', 'additionalProperties': False,
                              'properties': {field: {'type': 'string'}}, 'required': [field]}}
                         for name, (field, description) in TOOLS.items()]
                self.send(identity, result={'tools': tools})
            elif method == 'tools/call':
                try:
                    result = self.call(request.get('params'))
                except (OSError, ValueError) as exc:
                    result = {'content': [{'type': 'text', 'text': str(exc)[:1000]}],
                              'structuredContent': {'promotion_performed': None}, 'isError': True}
                self.send(identity, result=result)
            else:
                self.send(identity, error={'code': -32601, 'message': 'method not found'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace-root', required=True)
    parser.add_argument('--operator-socket', type=Path, default=REQUEST_SOCKET)
    args = parser.parse_args(argv)
    try:
        GenericApprovalMcpServer(workspace_root=args.workspace_root, operator_socket=args.operator_socket).serve()
        return 0
    except (OSError, ValueError) as exc:
        print('Knowledge Forge request transport refused: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
