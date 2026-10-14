"""P3B-CP0: platform-support review contract; zero installation or authority.

This plain-data module is deliberately importable without POSIX-only modules.
A platform label is not host attestation, an installer, or human authentication.
"""
from __future__ import annotations

import platform
import re
from typing import Any


CONTRACT_ID = "toadaid-knowledge-forge-platform-support-v1"
HOST_FAMILIES = frozenset({"linux", "macos", "wsl", "windows", "unknown"})
PORTABLE_ARTIFACTS = (
    "candidate", "validation_evidence", "review_handoff", "registry_record"
)


class PlatformContractError(ValueError):
    """Invalid platform review input; never a runtime authorization denial."""


def classify_host(system: str, kernel_release: str = "") -> str:
    """Classify supplied OS data for SUPPORT REVIEW, not trusted host identity.

    Windows builds may call this with platform.system()='Windows'. Linux in
    WSL is distinguished only for support documentation and does not prove a
    Windows boundary. Both arguments may be forged by an ordinary agent.
    """
    if not isinstance(system, str) or not isinstance(kernel_release, str):
        raise PlatformContractError("platform inputs must be text")
    if len(system) > 64 or len(kernel_release) > 512 or any(
        ord(c) < 32 for c in system + kernel_release
    ):
        raise PlatformContractError("invalid or unbounded platform input")
    key = system.casefold()
    if key == "windows":
        return "windows"
    if key == "darwin":
        return "macos"
    if key == "linux":
        return "wsl" if re.search(r"microsoft|wsl", kernel_release, re.I) else "linux"
    return "unknown"


def observed_host_family() -> str:
    """Self-observed OS name; mutable by the agent, never security proof."""
    return classify_host(platform.system(), platform.release())


def plan_for_host(family: str) -> dict[str, Any]:
    """Describe known support truth without installing or authorizing anything.

    An agent can request an arbitrary family. All results have zero authority.
    """
    if not isinstance(family, str) or family not in HOST_FAMILIES:
        raise PlatformContractError("host family must be a known exact label")
    status = {
        "linux": ("POSIX_INSTALLER_PRESENT", "TNG_LINUX_TESTED"),
        "macos": ("POSIX_INSTALLER_DOCUMENTED", "MACOS_HOST_PROOF_MISSING"),
        "wsl": ("POSIX_INSTALLER_DOCUMENTED", "WSL_HOST_PROOF_MISSING"),
        "windows": ("NATIVE_WINDOWS_INSTALLER_ABSENT", "WINDOWS_HOST_PROOF_MISSING"),
        "unknown": ("UNSUPPORTED_UNKNOWN_HOST", "HOST_NOT_SUPPORTED"),
    }
    installer, verification = status[family]
    return {
        "schema_version": "0.1",
        "contract_id": CONTRACT_ID,
        "status": "CROSS_PLATFORM_DESIGN_REVIEW_ONLY",
        "host_family": family,
        "installer_support": installer,
        "verification_status": verification,
        "portable_artifact_families": list(PORTABLE_ARTIFACTS),
        "native_windows_install_verified": False,
        "native_windows_operator_verified": False,
        "native_macos_operator_verified": False,
        "linux_operator_authentication_verified": False,
        "wsl_is_windows_security_boundary": False,
        "installer_executes": False,
        "agent_integrated": False,
        "independent_host_attestation_verified": False,
        "human_authentication_verified": False,
        "operator_authorized": False,
        "promotion_authorized": False,
        "registry_mutated": False,
        "tool_grants": [],
        "execution_authority": "NONE",
        "warning": (
            "Support labels are untrusted plain data, not OS proof. "
            "Native Windows install, operator isolation, agent integration and "
            "human authorization require independent implementation and host tests."
        ),
    }
