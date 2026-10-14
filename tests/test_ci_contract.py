"""P3A-CI: maintain the public, unprivileged workflow's security invariants."""

from __future__ import annotations

from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / '.github' / 'workflows' / 'verify.yml'


class CiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding='utf-8')

    def test_scope_and_read_only_permissions(self):
        source = self.source
        self.assertIn('  pull_request:\n    branches: [main]', source)
        self.assertIn('  push:\n    branches: [main]', source)
        self.assertIn('permissions:\n  contents: read', source)
        self.assertNotIn('pull_request_target:', source)
        self.assertNotIn('workflow_run:', source)
        self.assertNotIn('write-all', source)
        self.assertNotIn('permissions: write', source)

    def test_official_actions_are_immutable_sha_pinned(self):
        self.assertIn('actions/checkout@11d5960a326750d5838078e36cf38b85af677262', self.source)
        self.assertIn('actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065', self.source)
        self.assertIn('persist-credentials: false', self.source)
        self.assertNotIn('uses: actions/checkout@v', self.source)
        self.assertNotIn('uses: actions/setup-python@v', self.source)

    def test_full_stdlib_suite_on_supported_versions(self):
        source = self.source
        self.assertIn("python: ['3.10', '3.11', '3.12', '3.13']", source)
        self.assertIn('PYTHONPATH: src', source)
        self.assertIn('python -m unittest discover -s tests', source)
        self.assertIn('bin/knowledge-forge-install-agent --check-only', source)
        self.assertIn('bin/knowledge-forge-review synthetic-projections', source)

    def test_stable_fail_closed_aggregate_gate(self):
        source = self.source
        self.assertIn('name: Knowledge Forge CI / required', source)
        self.assertIn('needs: [regression]', source)
        self.assertIn('if: always()', source)
        self.assertIn("run: test \"$REGRESSION_RESULT\" = 'success'", source)

    def test_no_operator_or_release_activation_step(self):
        # The runner must never install the privileged CLI or touch live state.
        source = self.source
        self.assertNotIn('bin/knowledge-forge-operator', source)
        self.assertNotIn('promote --', source)
        self.assertNotIn('gh pr merge', source)
        self.assertNotIn('curl ', source)
        self.assertNotIn('secrets.', source)


if __name__ == '__main__':
    unittest.main()
