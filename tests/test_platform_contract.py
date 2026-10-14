"""P3B-CP0: portable support labels can never become execution authority."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest.mock import patch

from knowledge_forge.platform_contract import (
    CONTRACT_ID, HOST_FAMILIES, PORTABLE_ARTIFACTS,
    PlatformContractError, classify_host, observed_host_family, plan_for_host,
)


class PlatformContractTests(unittest.TestCase):
    def test_linux_native(self):
        self.assertEqual(classify_host("Linux", "6.9.0-generic"), "linux")

    def test_wsl_linux(self):
        self.assertEqual(classify_host("Linux", "5.15.167.4-microsoft-standard-WSL2"), "wsl")

    def test_wsl_only_from_linux(self):
        self.assertEqual(classify_host("Windows", "WSL2"), "windows")

    def test_windows_native(self):
        self.assertEqual(classify_host("Windows", "10"), "windows")

    def test_macos(self):
        self.assertEqual(classify_host("Darwin", "24.2.0"), "macos")

    def test_unknown_refusal_family(self):
        self.assertEqual(classify_host("FreeBSD", "14"), "unknown")

    def test_case_normalization(self):
        self.assertEqual(classify_host("wInDoWs", ""), "windows")

    def test_non_text_input_refused(self):
        for pair in ((None, ""), ("Linux", None), ([], ""), ("Linux", {})):
            with self.assertRaises(PlatformContractError):
                classify_host(*pair)

    def test_embedded_newline_refused(self):
        with self.assertRaises(PlatformContractError):
            classify_host("Windows\ntrusted", "")

    def test_oversized_release_refused(self):
        with self.assertRaises(PlatformContractError):
            classify_host("Linux", "a" * 513)

    def test_observation_is_for_support_only(self):
        with patch("knowledge_forge.platform_contract.platform.system", return_value="Windows"), \
             patch("knowledge_forge.platform_contract.platform.release", return_value="11"):
            result = plan_for_host(observed_host_family())
        self.assertEqual(result["host_family"], "windows")
        self.assertFalse(result["human_authentication_verified"])

    def test_exact_families_only(self):
        for candidate in ("WINDOWS", "win32", "linux/../", "", {"host_family": "windows"}, None):
            with self.assertRaises(PlatformContractError):
                plan_for_host(candidate)

    def test_all_family_plans_zero_authority(self):
        for family in HOST_FAMILIES:
            with self.subTest(family=family):
                result = plan_for_host(family)
                self.assertEqual(result["status"], "CROSS_PLATFORM_DESIGN_REVIEW_ONLY")
                self.assertEqual(result["execution_authority"], "NONE")
                self.assertEqual(result["tool_grants"], [])
                for field in (
                    "native_windows_install_verified", "native_windows_operator_verified",
                    "native_macos_operator_verified", "linux_operator_authentication_verified",
                    "wsl_is_windows_security_boundary", "installer_executes", "agent_integrated",
                    "independent_host_attestation_verified", "human_authentication_verified",
                    "operator_authorized", "promotion_authorized", "registry_mutated",
                ):
                    self.assertIs(result[field], False)

    def test_windows_installer_not_claimed(self):
        result = plan_for_host("windows")
        self.assertEqual(result["installer_support"], "NATIVE_WINDOWS_INSTALLER_ABSENT")
        self.assertEqual(result["verification_status"], "WINDOWS_HOST_PROOF_MISSING")

    def test_linux_installer_only_not_operator(self):
        result = plan_for_host("linux")
        self.assertEqual(result["installer_support"], "POSIX_INSTALLER_PRESENT")
        self.assertFalse(result["linux_operator_authentication_verified"])

    def test_macos_installer_unverified(self):
        result = plan_for_host("macos")
        self.assertEqual(result["verification_status"], "MACOS_HOST_PROOF_MISSING")

    def test_wsl_not_security_boundary(self):
        result = plan_for_host("wsl")
        self.assertFalse(result["wsl_is_windows_security_boundary"])

    def test_unknown_not_supported(self):
        self.assertEqual(plan_for_host("unknown")["installer_support"], "UNSUPPORTED_UNKNOWN_HOST")

    def test_contract_id_stable(self):
        self.assertEqual(CONTRACT_ID, "toadaid-knowledge-forge-platform-support-v1")

    def test_plain_json_artifact_labels(self):
        self.assertEqual(PORTABLE_ARTIFACTS,
                         ("candidate", "validation_evidence", "review_handoff", "registry_record"))

    def test_no_os_specific_imports_in_review_contract(self):
        source = Path(__file__).resolve().parents[1] / "src" / "knowledge_forge" / "platform_contract.py"
        names = []
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
        self.assertFalse(set(names) & {"pwd", "grp", "fcntl", "termios", "winreg", "ctypes"})


if __name__ == "__main__":
    unittest.main()
