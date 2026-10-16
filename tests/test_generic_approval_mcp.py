from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _platform_support import skip_linux_account_module_on_windows
skip_linux_account_module_on_windows('generic_approval_mcp')

from knowledge_forge import generic_approval_mcp as mcp


class GenericApprovalMcpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.output = StringIO()
        self.server = mcp.GenericApprovalMcpServer(workspace_root=self.workspace,
            input_stream=StringIO(), output_stream=self.output)

    def test_only_submit_status_tools_and_no_native_elicitation(self):
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'},
                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        self.server.input = StringIO(''.join(json.dumps(m)+'\n' for m in messages))
        self.server.serve()
        output = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual(len(output), 2)
        self.assertEqual({tool['name'] for tool in output[1]['result']['tools']},
                         {'submit_knowledge_promotion', 'knowledge_promotion_status'})
        self.assertNotIn('elicitation', self.output.getvalue())

    def test_submit_only_snapshots_beneath_selected_workspace(self):
        bundle = self.workspace / 'bundle'
        bundle.mkdir()
        with patch.object(mcp, 'snapshot_bundle', return_value={'synthetic': True}) as snapshot, \
             patch.object(mcp, 'exchange', return_value={'status': 'PENDING', 'promotion_performed': False}) as exchange:
            result = self.server.call({'name': 'submit_knowledge_promotion', 'arguments': {'bundle_dir': 'bundle'}})
            snapshot.assert_called_once_with(str(bundle))
            self.assertEqual(exchange.call_args.args[0], {'operation': 'SUBMIT_GENERIC', 'bundle': {'synthetic': True}})
            self.assertFalse(result['structuredContent']['promotion_performed'])

    def test_paths_cannot_escape_by_absolute_parent_or_symlink(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.workspace / 'escape').symlink_to(outside, target_is_directory=True)
        for value in [str(outside), '../outside', 'escape']:
            with self.assertRaisesRegex(ValueError, 'outside configured workspace'), patch.object(mcp, 'exchange') as exchange:
                self.server.call({'name': 'submit_knowledge_promotion', 'arguments': {'bundle_dir': value}})
                exchange.assert_not_called()

    def test_status_is_exact_id_and_has_no_approval_fields(self):
        with patch.object(mcp, 'exchange', return_value={'status': 'PENDING'}) as exchange:
            self.server.call({'name': 'knowledge_promotion_status', 'arguments': {'request_id': 'a'*64}})
            self.assertEqual(exchange.call_args.args[0], {'operation': 'STATUS_GENERIC', 'request_id': 'a'*64})
        for args in [{'request_id': '../key'}, {'request_id': 'a'*64, 'approve': True}, {'request_id': None}]:
            with self.assertRaises(ValueError):
                self.server.call({'name': 'knowledge_promotion_status', 'arguments': args})

    def test_unknown_tools_extra_fields_and_non_mapping_arguments_refuse(self):
        for params in [None, {'name': 'approve', 'arguments': {}},
                       {'name': 'knowledge_promotion_status', 'arguments': []},
                       {'name': 'knowledge_promotion_status', 'arguments': {'request_id': 'a'*64}, 'approved': True}]:
            with self.assertRaises(ValueError):
                self.server.call(params)

    def test_failed_delivery_never_reports_known_false_promotion(self):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'knowledge_promotion_status', 'arguments': {'request_id': 'a'*64}}}
        self.server.input = StringIO(json.dumps(request)+'\n')
        with patch.object(mcp, 'exchange', side_effect=OSError('socket unavailable')):
            self.server.serve()
        result = json.loads(self.output.getvalue())['result']
        self.assertTrue(result['isError'])
        self.assertIsNone(result['structuredContent']['promotion_performed'])

    def test_message_size_is_bounded_before_parsing(self):
        self.server.input = StringIO('x'*(mcp.MAX_LINE+1))
        with self.assertRaisesRegex(ValueError, 'message limit'):
            self.server.serve()

    def test_invalid_json_and_unknown_methods_are_protocol_errors(self):
        self.server.input = StringIO('invalid\n[]\n'+json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'approve'})+'\n')
        self.server.serve()
        output = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual([r['error']['code'] for r in output], [-32700, -32600, -32601])


if __name__ == '__main__':
    unittest.main()
