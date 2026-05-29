#!/usr/bin/env python3
"""Remote SSH link and Vitis acceptance checks via erie-remote-ssh."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MODULE_DIR = Path(__file__).resolve().parent
SKILL_ROOT = Path(__file__).resolve().parents[3]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import remote_acceptance_vitis as _rv
import remote_acceptance_board as _rb
import remote_acceptance_recovery as _rr

from remote_acceptance_common import BLOCKED_BOARD_STATUS, BLOCKED_PROFILE_STATUS, BLOCKED_VERSION_STATUS, BLOCKED_VITIS_STATUS, DRY_RUN_STATUS, FAILED_STATUS, PASS_STATUS, READINESS_LEVELS, RemoteAcceptanceError, SkillDependencyError, ErieHelper, get_board_platform_selection, get_vitis_selection, infer_target_part_from_platform_selection, remote_validation_config, require_skill_dependencies, set_board_platform_selection, set_vitis_selection, skill_dependencies_config, subprocess, time, vitis_tool_timeout, _expand_settings_path, _format_result, _is_transient_status_failure, _planned_steps, _prepare_erie_server_list_copy, _probe_board_toolchain, _probe_hardware_fingerprint, _probe_platform_name, _probe_remote_workdir, _probe_vitis, _reject_decode_noise, _resolve_topology
from remote_acceptance_link import _run_link_mode
from remote_acceptance_vitis import _blocked_profile_config, _create_vitis_package, _find_candidate, _find_server_record, _generate_local_hls_artifacts, _infer_target_part_from_server, _infer_target_part_from_server_record, _infer_vitis_hls_env_setup, _infer_vitis_hls_executable, _load_example_spec, _remote_runner_script, _remote_vitis_command, _remote_vitis_version_request, _resolve_profile_config, _resolve_profile_for_version, _resolve_target_part, _run_server_vitis_phase, _run_split_vitis_mode, _run_vitis_mode, _safe_tail_log, _select_shared_vitis_version, _select_vitis_profile, _version_label, _version_sort_key, _vitis_version_candidates, _transfer_package_by_request_commands
from remote_acceptance_board import _board_platform_upload_plan, _board_runner_script, _board_metadata_for_spec, _create_board_package, _default_platform_name_for_part, _explicit_board_platform_selection, _governed_remote_platform_selection, _local_board_platform_upload_selection, _normalize_remote_platform_path, _remote_board_command, _remote_relative_to_workdir, _render_board_host, _render_vector_driven_board_host, _resolve_board_platform_selection, _run_board_mode, _upload_local_board_platform_payload, _vector_literal
from remote_acceptance_recovery import _recover_board_mode, _recover_board_profile, _recover_example_spec, _recover_local_run_dir, _recover_settings_path, _resolve_recovery_target, _probe_recoverable_board_result
from remote_acceptance_common import _archive_remote_run, _ensure_remote_project_layout, _field_from_output, _merge_profile_fields, _new_run_dir, _parse_request_path, _probe_fpga_presence, _probe_shell_name, _probe_target_part_hint, _probe_uploaded_platform, _resolve_erie_server_list, _section_value, _suggest_platform_name_from_shell, _write_erie_settings_overlay, _write_json, _write_report

BOARD_STATUS_MARKER = "HLS_BOARD_STATUS"
UTF8_HINT = "Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 when calling erie-remote-ssh."

_orig_select_vitis_profile = _rv._select_vitis_profile
_orig_resolve_profile_config = _rv._resolve_profile_config
_orig_blocked_profile_config = _rv._blocked_profile_config
_orig_resolve_board_platform_selection = _rb._resolve_board_platform_selection
_orig_upload_local_board_platform_payload = _rb._upload_local_board_platform_payload
_orig_run_server_vitis_phase = _rv._run_server_vitis_phase


def _select_vitis_profile(args, run_dir, candidates, fallback_profile):
    _rv.get_vitis_selection = get_vitis_selection
    _rv.set_vitis_selection = set_vitis_selection
    return _orig_select_vitis_profile(args, run_dir, candidates, fallback_profile)


def _resolve_profile_config(args, run_dir, *, candidates, configured_profiles, required_fields):
    _rv.get_vitis_selection = get_vitis_selection
    return _orig_resolve_profile_config(args, run_dir, candidates=candidates, configured_profiles=configured_profiles, required_fields=required_fields)


def _blocked_profile_config(args, run_dir, *, missing_fields, configured_profiles):
    _rv.user_config_path = _rv.user_config_path
    return _orig_blocked_profile_config(args, run_dir, missing_fields=missing_fields, configured_profiles=configured_profiles)


def _resolve_board_platform_selection(args, server, remote_workdir, profile, directory_contract):
    _rb.get_board_platform_selection = get_board_platform_selection
    _rb.set_board_platform_selection = set_board_platform_selection
    return _orig_resolve_board_platform_selection(args, server, remote_workdir, profile, directory_contract)


def _upload_local_board_platform_payload(helper, settings, server, run_dir, remote_workdir, selection, *, local_root=None):
    _rb.set_board_platform_selection = set_board_platform_selection
    return _orig_upload_local_board_platform_payload(helper, settings, server, run_dir, remote_workdir, selection, local_root=local_root)


def _run_server_vitis_phase(helper, settings, server, profile, readiness, package_path, config, run_dir, *, phase_label, cleanup_remote, remote_workdir):
    _rv._transfer_package_by_request_commands = _transfer_package_by_request_commands
    _rv._remote_vitis_command = _remote_vitis_command
    return _orig_run_server_vitis_phase(helper, settings, server, profile, readiness, package_path, config, run_dir, phase_label=phase_label, cleanup_remote=cleanup_remote, remote_workdir=remote_workdir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate HLS generator remote confidence through erie-remote-ssh.")
    parser.add_argument("--mode", required=True, choices=("link", "vitis", "board"))
    parser.add_argument("--server", help="Single-server target id or name from erie-remote-ssh config.")
    parser.add_argument("--build-server", help="Build-server id or name for split build/validate topology.")
    parser.add_argument("--validate-server", help="Validation-server id or name for split build/validate topology.")
    parser.add_argument("--profile", help="Optional remote_validation.vitis_profiles key for Vitis mode.")
    parser.add_argument("--vitis-version", help="Explicit remote Vitis version to use and remember for this server.")
    parser.add_argument("--target-part", help="Optional explicit target part override for remote HLS synthesis.")
    parser.add_argument("--platform-name", help="Explicit board platform name or platform spec for board mode.")
    parser.add_argument("--remote-platform-root", help="Remote directory containing an uploaded board platform for board mode.")
    parser.add_argument("--remote-xpfm", help="Explicit remote XPFM path for board mode.")
    parser.add_argument("--readiness", default="cosim", choices=READINESS_LEVELS)
    parser.add_argument("--example-spec", default="hls_vector_scale_mock_spec.json", help="Example spec from assets/examples used for Vitis acceptance artifacts.")
    parser.add_argument("--recover-run-id", help="Recover a prior detached remote acceptance result by local/remote run id instead of launching a new run.")
    parser.add_argument("--recover-remote-run-dir", help="Recover a prior detached remote acceptance result from an explicit remote run directory.")
    parser.add_argument("--comment-language", default="auto", choices=("auto", "en", "zh"), help="Comment language for locally generated HLS acceptance artifacts.")
    parser.add_argument("--timeout", type=int, help="Override remote command timeout in seconds.")
    parser.add_argument("--cleanup-remote", action="store_true", help="Delete the remote validation directory after a successful Vitis run.")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned erie helper steps without connecting.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        result = run_acceptance(args)
    except SkillDependencyError as exc:
        result = exc.report
    except (OSError, RemoteAcceptanceError, ValueError) as exc:
        result = {"status": FAILED_STATUS, "error": str(exc)}

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_format_result(result))

    if result["status"] in {PASS_STATUS, DRY_RUN_STATUS}:
        return 0
    if result["status"] == BLOCKED_PROFILE_STATUS:
        return 5
    if result["status"] == BLOCKED_VERSION_STATUS:
        return 4
    if result["status"] == BLOCKED_VITIS_STATUS:
        return 3
    if result["status"] == BLOCKED_BOARD_STATUS:
        return 6
    return 1


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    require_skill_dependencies(skill_dependencies_config(), scopes={"core"})
    config = remote_validation_config()
    if not args.dry_run:
        config = {**config, "erie_server_list_config": str(_prepare_erie_server_list_copy(config))}
    base_timeout = int(args.timeout or config["default_timeout_s"])
    if args.mode in {"vitis", "board"}:
        base_timeout = max(base_timeout, int(vitis_tool_timeout(args.readiness)) + 30)
    helper = ErieHelper(config, base_timeout)
    topology = _resolve_topology(args)
    plan = _planned_steps(args.mode, topology["server"], args.profile, args.readiness, cleanup_remote=bool(getattr(args, "cleanup_remote", False)), example_spec=str(getattr(args, "example_spec", "")), validate_server=topology.get("validate_server"), topology=topology["topology"])
    if args.dry_run:
        result = {"status": DRY_RUN_STATUS, "mode": args.mode, "server": topology["server"], "build_server": topology.get("build_server"), "validate_server": topology.get("validate_server"), "topology": topology["topology"], "steps": plan, "uses_erie_remote_ssh": True}
        if args.mode in {"vitis", "board"}:
            result.update({"cleanup_performed": False, "remote_artifacts_retained": True})
        return result
    if args.mode == "link":
        return _run_link_mode(args, config, helper, plan, topology)
    if args.mode == "board":
        if topology["topology"] != "single_server":
            raise ValueError("Board acceptance currently requires --server and does not support split topology.")
        if str(getattr(args, "recover_run_id", "") or "").strip() or str(getattr(args, "recover_remote_run_dir", "") or "").strip():
            return _recover_board_mode(args, config, helper, topology)
        return _run_board_mode(args, config, helper, plan, topology)
    if topology["topology"] == "split_build_validate":
        return _run_split_vitis_mode(args, config, helper, plan, topology)
    return _run_vitis_mode(args, config, helper, plan, topology)

from remote_acceptance_common import *  # noqa: F401,F403,E402
from remote_acceptance_link import *  # noqa: F401,F403,E402
from remote_acceptance_vitis import *  # noqa: F401,F403,E402
from remote_acceptance_board import *  # noqa: F401,F403,E402
from remote_acceptance_recovery import *  # noqa: F401,F403,E402

if __name__ == "__main__":
    raise SystemExit(main())
