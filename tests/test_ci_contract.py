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

    def test_pdf_extractor_is_present_for_full_regression(self):
        self.assertIn('sudo apt-get install -y --no-install-recommends poppler-utils', self.source)
        self.assertIn('command -v pdftotext', self.source)

    def test_capsule_tests_are_explicitly_abi_gated(self):
        capsule = (REPO / 'tests' / 'test_consumer_capsule.py').read_text(encoding='utf-8')
        self.assertIn('sys.version_info[:2] == (3, 12)', capsule)
        self.assertIn('"consumer capsule builder is supported only on CPython 3.12"', capsule)
        self.assertIn('@unittest.skipUnless(', capsule)

    def test_stable_fail_closed_aggregate_gate(self):
        # P3B-CP1: the unchanged required check now also requires native Windows.
        source = self.source
        self.assertIn('name: Knowledge Forge CI / required', source)
        self.assertIn('needs: [regression, windows]', source)
        self.assertIn('if: always()', source)
        self.assertIn('REGRESSION_RESULT: ${{ needs.regression.result }}', source)
        self.assertIn('WINDOWS_RESULT: ${{ needs.windows.result }}', source)
        self.assertIn("test \"$REGRESSION_RESULT\" = 'success'", source)
        self.assertIn("test \"$WINDOWS_RESULT\" = 'success'", source)

    def test_native_windows_job_is_real_blocking_and_unprivileged(self):
        source = self.source
        self.assertIn('  windows:\n', source)
        windows = source.split('  windows:\n', 1)[1].split('\n  required:\n', 1)[0]
        self.assertIn('runs-on: windows-latest', windows)
        self.assertIn('shell: cmd', windows)
        self.assertIn("KF_REQUIRE_WINDOWS_E2E: '1'", windows)
        self.assertIn('bin\\knowledge-forge-install-agent.cmd --check-only', windows)
        self.assertIn('knowledge-forge.exe" --help', windows)
        self.assertIn('knowledge-forge-authoring.exe" --help', windows)
        self.assertIn('python -m unittest discover -s tests', windows)
        self.assertIn('actions/checkout@11d5960a326750d5838078e36cf38b85af677262', windows)
        self.assertIn('actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065', windows)
        self.assertIn('persist-credentials: false', windows)
        for forbidden in ('continue-on-error', 'wsl', 'bash', 'runas', 'Start-Process',
                          'operator_cli', 'knowledge-forge-operator', 'secrets.'):
            self.assertNotIn(forbidden, windows)
        self.assertNotIn('continue-on-error', source)

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
