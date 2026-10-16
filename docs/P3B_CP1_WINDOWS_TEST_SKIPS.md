# P3B-CP1 R2 — native Windows test skips (by identity)

Upstream `main` (`405d118`) added Linux-native portable modules (P4/P6/P7 host inventory,
ELF/dependency evidence, provenance hints, bubblewrap namespace preflights, hook fixture,
native version probe). They inspect POSIX executables and Linux namespaces. On native
Windows they never resolve an executable and never reach a namespace probe: they fail
closed. The skips below name the exact Linux primitive each test needs. **None of them
skips on Linux** (Linux CI runs every one; per-identity Linux status is unchanged on
Python 3.10–3.13). A skip is never counted as a pass.

Replacement evidence on Windows: `tests/test_portable_native_windows_fail_closed.py` asserts
that, on native Windows, each module refuses or stays unresolved with every authority
flag false, no hash, no path disclosure and no subprocess; its env-allowlist test runs on
every OS. Helpers live in `tests/_platform_support.py`.

| # | Module | Test | Skip helper | Linux primitive required |
|---|---|---|---|---|
| 1 | `test_portable_host_preflight` | `test_symlink_ancestor_refuses` | `skip_unless_symlinks` (real probe) | symlink privilege (runs on CI; skips only on hosts without it) |
| 2 | `test_portable_host_preflight` | `test_symlink_package_refuses` | `skip_unless_symlinks` (real probe) | symlink privilege (runs on CI; skips only on hosts without it) |
| 3 | `test_portable_isolation_preflight` | `test_fake_shared_network_rejected` | `skip_linux_proc_namespaces_on_windows` | Linux `/proc/self/ns/*` + `/tmp` canary |
| 4 | `test_portable_isolation_preflight` | `test_positive_synthetic_result_cannot_attest_host` | `skip_linux_proc_namespaces_on_windows` | Linux `/proc/self/ns/*` + `/tmp` canary |
| 5 | `test_portable_native_binary_dossier` | `test_both_hosts_digest_small_fixture_same_descriptor` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 6 | `test_portable_native_binary_dossier` | `test_direct_symlink_and_parent_group_mode_hints` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 7 | `test_portable_native_binary_dossier` | `test_file_swap_during_hash_refused` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 8 | `test_portable_native_binary_dossier` | `test_no_subprocess_or_credentials_when_dossier_called` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 9 | `test_portable_native_binary_dossier` | `test_os_read_is_bounded_by_file_size_and_chunk` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 10 | `test_portable_native_binary_dossier` | `test_oversize_refuses_at_configured_bound_without_digest` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 11 | `test_portable_native_binary_dossier` | `test_refuses_world_writable_candidate` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 12 | `test_portable_native_binary_dossier` | `test_search_path_outside_home_refused_no_leak` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 13 | `test_portable_native_binary_dossier` | `test_symlink_swap_during_hash_is_refused` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 14 | `test_portable_native_dependency_evidence` | `test_bounded_small_script_header_only` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 15 | `test_portable_native_dependency_evidence` | `test_candidate_header_read_is_bounded_for_oversized_file` | `skip_without_os_pread` | `os.pread` absent on Windows |
| 16 | `test_portable_native_dependency_evidence` | `test_inferred_loader_hint_is_not_dependency_attestation` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 17 | `test_portable_native_dependency_evidence` | `test_missing_or_moving_candidate_fail_closed` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 18 | `test_portable_native_dependency_evidence` | `test_oversized_sparse_binary_avoids_full_digest` | `skip_without_os_pread` | `os.pread` absent on Windows |
| 19 | `test_portable_native_dependency_evidence` | `test_symlink_path_redaction` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 20 | `test_portable_native_dependency_evidence` | `test_world_writable_candidate_refused` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 21 | `test_portable_native_executable_inventory` | `test_both_hosts_passive_identity_and_hash` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 22 | `test_portable_native_executable_inventory` | `test_changed_during_digest_distinct_and_not_success` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 23 | `test_portable_native_executable_inventory` | `test_empty_candidate_classified_no_native_execution` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 24 | `test_portable_native_executable_inventory` | `test_oversized_candidate_classified_without_hash_or_path` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 25 | `test_portable_native_executable_inventory` | `test_path_redaction_is_boundary_aware` | `skip_posix_path_labels_on_windows` | POSIX `/` path labels |
| 26 | `test_portable_native_executable_inventory` | `test_size_cap_and_non_executable_file_refuse` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 27 | `test_portable_native_executable_inventory` | `test_symlink_chain_is_bounded_and_read_only` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 28 | `test_portable_native_executable_inventory` | `test_symlink_cycle_category_without_path_disclosure` | `skip_unless_symlinks` (real probe) | symlink privilege (runs on CI; skips only on hosts without it) |
| 29 | `test_portable_native_executable_inventory` | `test_symlink_cycle_refused` | `skip_unless_symlinks` (real probe) | symlink privilege (runs on CI; skips only on hosts without it) |
| 30 | `test_portable_native_executable_inventory` | `test_symlink_depth_category` | `skip_unless_symlinks` (real probe) | symlink privilege (runs on CI; skips only on hosts without it) |
| 31 | `test_portable_native_executable_inventory` | `test_unavailable_file_and_link_are_distinct_safe_categories` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 32 | `test_portable_native_hermetic_preflight` | `test_synthetic_runner_scope_fixed_and_sanitized` | `skip_linux_proc_namespaces_on_windows` | Linux `/proc/self/ns/*` + `/tmp` canary |
| 33 | `test_portable_native_hook_fixture` | `test_both_host_config_shapes_and_narrow_paths` | `skip_posix_owner_fixture_on_windows` | POSIX owner id (`os.getuid`) |
| 34 | `test_portable_native_hook_fixture` | `test_cli_offline_smoke_and_on_refusal` | `skip_posix_owner_fixture_on_windows` | POSIX owner id (`os.getuid`) |
| 35 | `test_portable_native_hook_fixture` | `test_duplicate_synthetic_replay_is_metadata_only` | `skip_posix_owner_fixture_on_windows` | POSIX owner id (`os.getuid`) |
| 36 | `test_portable_native_hook_fixture` | `test_handler_discards_bad_event_and_no_stdout` | `skip_posix_owner_fixture_on_windows` | POSIX owner id (`os.getuid`) |
| 37 | `test_portable_native_hook_fixture` | `test_offline_subprocess_has_no_credentials_or_python_loader` | `skip_posix_owner_fixture_on_windows` | POSIX owner id (`os.getuid`) |
| 38 | `test_portable_native_hook_fixture` | `test_symlink_root_refused_without_touching_target` | `skip_unless_symlinks` (real probe) | symlink privilege (runs on CI; skips only on hosts without it) |
| 39 | `test_portable_native_host_probe` | `test_isolated_version_env_both_hosts` | `skip_posix_shebang_host_on_windows` | `#!/usr/bin/env python3` host script |
| 40 | `test_portable_native_host_probe` | `test_real_shell_profile_and_secrets_not_needed` | `skip_posix_shebang_host_on_windows` | `#!/usr/bin/env python3` host script |
| 41 | `test_portable_native_host_probe` | `test_unrelated_secrets_and_loader_options_never_inherited` | `skip_posix_shebang_host_on_windows` | `#!/usr/bin/env python3` host script |
| 42 | `test_portable_native_provenance_hints` | `test_file_change_fail_closed_no_paths` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 43 | `test_portable_native_provenance_hints` | `test_group_writable_directory_bits_are_a_hint_not_access_proof` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 44 | `test_portable_native_provenance_hints` | `test_installation_layout_and_parent_metadata_only` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 45 | `test_portable_native_provenance_hints` | `test_intermediate_symlink_0777_not_reported_as_writable_directory` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 46 | `test_portable_native_provenance_hints` | `test_malformed_elf_bounds_refuse_without_execution` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 47 | `test_portable_native_provenance_hints` | `test_missing_and_unsafe_candidate_are_nonexecution` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 48 | `test_portable_native_provenance_hints` | `test_no_dynamic_segment_never_implies_static_binary` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 49 | `test_portable_native_provenance_hints` | `test_non_symlink_install_path_still_not_attested` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |
| 50 | `test_portable_native_provenance_hints` | `test_oversized_file_is_never_fully_read` | `skip_without_os_pread` | `os.pread` absent on Windows |
| 51 | `test_portable_native_provenance_hints` | `test_synthetic_elf_needed_names_not_resolution` | `skip_posix_executable_fixture_on_windows` | POSIX executable fixture: `shutil.which()` + mode bits |

Plus one sub-test skip: `test_portable_native_provenance_hints.ProvenanceHintTests.test_invalid_arguments_and_redaction`
sub-test *POSIX $HOME path labels* (reason `POSIX_PATH_LABEL_SKIP_REASON`); its invalid-argument
assertions still run on Windows.

Source change (one, fail-closed): `portable_native_hook_fixture._root_check` now refuses with a typed
`NativeHookFixtureError` when `os.getuid` does not exist, instead of raising an unhandled
`AttributeError` (which made the CLI crash with a traceback rather than refuse with exit 2).

Pre-existing CP1 Windows skips (unchanged): Bash launchers under `bin/`, Linux-only `pwd`/`grp`
review modules, POSIX-shebang fake OCR tools, Poppler `pdftotext` absence, symlink privilege.
