#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
from typing import Any

from remote_acceptance_common import (
    BOARD_STATUS_MARKER,
    BLOCKED_BOARD_STATUS,
    FAILED_STATUS,
    PASS_STATUS,
    field_from_equals_output,
    get_vitis_selection,
    infer_target_part_from_platform_selection,
    recover_example_spec,
    recover_local_run_dir,
    remote_validation_config,
    repo_root,
    resolve_recovery_target,
    skill_config_path,
    skill_root,
    remote_directory_layout_for_workdir,
    _archive_remote_run,
    _merge_profile_fields,
    _new_run_dir,
    _probe_board_toolchain,
    _probe_hardware_fingerprint,
    _probe_platform_name,
    _probe_remote_workdir,
    _write_erie_settings_overlay,
    _write_report,
)
from remote_acceptance_board import _board_metadata_for_spec, _resolve_board_platform_selection
from remote_acceptance_vitis import _find_candidate, _infer_target_part_from_server, _vitis_version_candidates

def _recover_board_mode(args: argparse.Namespace, config: dict[str, Any], helper: "ErieHelper", topology: dict[str, Any]) -> dict[str, Any]:
    run_id, remote_run_dir = _resolve_recovery_target(args)
    local_run_dir = _recover_local_run_dir(config, run_id)
    settings = _recover_settings_path(config, local_run_dir)
    server = topology["server"]
    helper.preflight(server, settings=settings)
    helper.scan_software(server, settings=settings)
    remote_workdir = _probe_remote_workdir(server, settings, helper)
    layout = remote_directory_layout_for_workdir(remote_workdir, run_id)
    example_spec = str(args.example_spec or "").strip()
    if not example_spec or example_spec == "hls_vector_scale_mock_spec.json":
        example_spec = _recover_example_spec(local_run_dir)
    board_metadata = _board_metadata_for_spec(example_spec)
    selected_profile = _recover_board_profile(args, server, remote_workdir, settings)
    platform_probe = _probe_platform_name(server, settings, helper, selected_profile)
    hardware_probe = _probe_hardware_fingerprint(server, settings, helper, selected_profile)
    toolchain_probe = _probe_board_toolchain(server, settings, helper, selected_profile)
    active_remote_dir = remote_run_dir or layout["active_run_dir"]
    backup_remote_dir = layout["backup_run_dir"]
    active_probe = _probe_recoverable_board_result(server, settings, helper, active_remote_dir)
    backup_probe = _probe_recoverable_board_result(server, settings, helper, backup_remote_dir)
    request_paths: list[str] = []
    recovered_remote_dir = backup_remote_dir if backup_probe.get("board_status") == PASS_STATUS else active_remote_dir
    recovered_remote_rel = layout["backup_run_relative"] if backup_probe.get("board_status") == PASS_STATUS else layout["active_run_relative"]
    archived_after_verification = backup_probe.get("board_status") == PASS_STATUS
    if active_probe.get("board_status") == PASS_STATUS and not archived_after_verification:
        request_paths.append(_archive_remote_run(helper, settings, server, layout))
        archived_after_verification = True
        recovered_remote_dir = backup_remote_dir
        recovered_remote_rel = layout["backup_run_relative"]
        backup_probe = _probe_recoverable_board_result(server, settings, helper, backup_remote_dir)
    recovered_probe = backup_probe if archived_after_verification else active_probe
    if recovered_probe.get("board_status") != PASS_STATUS:
        result = {
            "status": FAILED_STATUS,
            "mode": "board",
            "server": server,
            "topology": topology["topology"],
            "profile": args.profile,
            "vitis_version": str(selected_profile.get("version") or ""),
            "readiness": args.readiness,
            "example_spec": example_spec,
            "run_dir": str(local_run_dir),
            "run_id": run_id,
            "remote_run_dir": layout["active_run_relative"],
            "remote_backup_dir": layout["backup_run_relative"],
            "remote_dir": recovered_remote_rel,
            "platform_probe": platform_probe,
            "hardware_probe": hardware_probe,
            "toolchain_probe": toolchain_probe,
            "board_metadata": board_metadata,
            "board_probe": recovered_probe,
            "requests": request_paths,
            "recovered_from_run_id": run_id,
            "recovered_from_remote_logs": False,
            "evidence_sources": [
                f"remote:{layout['active_run_relative']}",
                f"remote:{layout['backup_run_relative']}",
            ],
            "uses_erie_remote_ssh": True,
        }
        _write_report(local_run_dir, result)
        return result
    result = {
        "status": PASS_STATUS,
        "mode": "board",
        "server": server,
        "topology": topology["topology"],
        "profile": args.profile,
        "vitis_version": str(selected_profile.get("version") or ""),
        "readiness": args.readiness,
        "example_spec": example_spec,
        "run_dir": str(local_run_dir),
        "artifact_dir": str(local_run_dir / "local-generation" / "attempt-001" / "hls" / "artifacts"),
        "run_id": run_id,
        "remote_project_root": layout["project_root_relative"],
        "remote_project_root_abs": layout["project_root"],
        "remote_conda_prefix": layout["conda_prefix_relative"],
        "remote_conda_prefix_abs": layout["conda_prefix"],
        "remote_run_dir": layout["active_run_relative"],
        "remote_run_dir_abs": layout["active_run_dir"],
        "remote_backup_dir": layout["backup_run_relative"],
        "remote_backup_dir_abs": layout["backup_run_dir"],
        "remote_dir": recovered_remote_rel,
        "cleanup_performed": False,
        "remote_artifacts_retained": True,
        "archived_after_verification": archived_after_verification,
        "archive_trigger": config["directory_contract"]["archive_trigger"],
        "requests": request_paths,
        "job_id": "recovered-from-existing-run",
        "job_status": "recovered",
        "platform_probe": platform_probe,
        "platform_upload": {},
        "hardware_probe": hardware_probe,
        "toolchain_probe": toolchain_probe,
        "board_profile": {
            "platform_name": str(selected_profile.get("platform_name") or ""),
            "remote_platform_root": str(selected_profile.get("remote_platform_root") or ""),
            "remote_xpfm": str(selected_profile.get("remote_xpfm") or ""),
            "target_part": str(selected_profile.get("target_part") or ""),
        },
        "board_metadata": board_metadata,
        "board_status_marker": BOARD_STATUS_MARKER,
        "recovered_from_run_id": run_id,
        "recovered_from_remote_logs": True,
        "evidence_sources": [
            f"remote:{recovered_remote_rel}/artifacts/board_run.log",
            f"remote:{recovered_remote_rel}/artifacts/v++_kernel.log",
        ],
        "uses_erie_remote_ssh": True,
    }
    _write_report(local_run_dir, result)
    return result
def _resolve_recovery_target(args: argparse.Namespace) -> tuple[str, str]:
    try:
        return resolve_recovery_target(
            str(getattr(args, "recover_run_id", "") or ""),
            str(getattr(args, "recover_remote_run_dir", "") or ""),
        )
    except ValueError as exc:
        raise RemoteAcceptanceError(str(exc)) from exc
def _recover_local_run_dir(config: dict[str, Any], run_id: str) -> Path:
    try:
        return recover_local_run_dir(repo_root() / str(config["local_run_root"]), run_id)
    except ValueError as exc:
        raise RemoteAcceptanceError(str(exc)) from exc
def _recover_settings_path(config: dict[str, Any], local_run_dir: Path) -> Path:
    overlay = local_run_dir / "erie_settings.overlay.json"
    if overlay.is_file():
        return overlay
    return _write_erie_settings_overlay(config, local_run_dir)
def _recover_example_spec(local_run_dir: Path) -> str:
    try:
        return recover_example_spec(skill_config_path("examples_dir"), local_run_dir)
    except ValueError as exc:
        raise RemoteAcceptanceError(str(exc)) from exc
def _recover_board_profile(args: argparse.Namespace, server: str, remote_workdir: str, settings: Path) -> dict[str, Any]:
    candidates = _vitis_version_candidates(remote_validation_config(), settings, server)
    explicit_version = str(getattr(args, "vitis_version", "") or "").strip()
    if explicit_version:
        candidate = _find_candidate(candidates, explicit_version)
        if candidate:
            selected = dict(candidate)
        else:
            selected = dict(get_vitis_selection(server) or {})
    else:
        selected = dict(get_vitis_selection(server) or {})
        if not selected and candidates:
            selected = dict(candidates[0])
    if not str(selected.get("target_part") or "").strip():
        inferred_target_part = _infer_target_part_from_server(settings, server)
        if inferred_target_part:
            selected["target_part"] = inferred_target_part
    selected = _merge_profile_fields(
        selected,
        _resolve_board_platform_selection(args, server, remote_workdir, selected, remote_validation_config()["directory_contract"]),
    )
    if not str(selected.get("target_part") or "").strip():
        inferred_target_part = infer_target_part_from_platform_selection(selected)
        if inferred_target_part:
            selected["target_part"] = inferred_target_part
    return selected
def _probe_recoverable_board_result(server: str, settings: Path, helper: ErieHelper, remote_run_dir: str) -> dict[str, Any]:
    remote = shlex.quote(remote_run_dir)
    script = f"""
if [ -f {remote}/artifacts/board_run.log ]; then
  if grep -q '{BOARD_STATUS_MARKER} passed' {remote}/artifacts/board_run.log; then
    echo board_status=passed
  elif grep -q '{BOARD_STATUS_MARKER} failed' {remote}/artifacts/board_run.log; then
    echo board_status=failed
  else
    echo board_status=unknown
  fi
  echo board_log=1
else
  echo board_status=missing
  echo board_log=0
fi
if [ -f {remote}/artifacts/kernel.xclbin ]; then echo xclbin=1; else echo xclbin=0; fi
if [ -f {remote}/artifacts/v++_kernel.log ]; then echo vpp_log=1; else echo vpp_log=0; fi
"""
    output = helper.exec(server, ["bash", "-lc", script], settings=settings)
    board_status = field_from_equals_output(output, "board_status")
    return {
        "board_status": board_status,
        "board_log": field_from_equals_output(output, "board_log"),
        "xclbin": field_from_equals_output(output, "xclbin"),
        "vpp_log": field_from_equals_output(output, "vpp_log"),
        "output": output,
    }
