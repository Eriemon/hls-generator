#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from remote_acceptance_common import (
    BOARD_STATUS_MARKER,
    BOARD_RUNNABLE_PROFILE,
    BLOCKED_BOARD_STATUS,
    BLOCKED_PROFILE_STATUS,
    BLOCKED_VERSION_STATUS,
    BLOCKED_VITIS_STATUS,
    FAILED_STATUS,
    PASS_STATUS,
    RemoteAcceptanceError,
    SKILL_ROOT,
    board_acceptance_config,
    U55C_PLATFORM_NAME,
    default_local_u55c_payload_root,
    get_board_platform_selection,
    infer_target_part_from_platform_selection,
    prepare_local_u55c_platform_archive,
    remote_directory_layout_for_workdir,
    resolve_host_template_path,
    set_board_platform_selection,
    validate_local_board_platform_payload,
    _archive_remote_run,
    _ensure_remote_project_layout,
    _merge_profile_fields,
    _new_run_dir,
    _parse_request_path,
    _probe_board_toolchain,
    _probe_hardware_fingerprint,
    _probe_platform_name,
    _probe_remote_workdir,
    _probe_target_part_hint,
    _probe_vitis,
    _write_json,
    _write_erie_settings_overlay,
    _write_report,
)
from remote_acceptance_vitis import _generate_local_hls_artifacts, _infer_target_part_from_server, _load_example_spec, _resolve_profile_config, _safe_tail_log, _select_vitis_profile, _transfer_package_by_request_commands, _vitis_version_candidates

def _run_board_mode(args: argparse.Namespace, config: dict[str, Any], helper: "ErieHelper", plan: list[str], topology: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("vitis_profiles", {})
    run_dir = _new_run_dir(config, "board")
    settings = _write_erie_settings_overlay(config, run_dir)
    server = topology["server"]
    helper.preflight(server, settings=settings)
    helper.scan_software(server, settings=settings)
    remote_workdir = _probe_remote_workdir(server, settings, helper)
    candidates = _vitis_version_candidates(config, settings, server)
    board_profile = _resolve_profile_config(
        args,
        run_dir,
        candidates=candidates,
        configured_profiles=profiles,
        required_fields=("settings_script", "expected_tool"),
    )
    if board_profile.get("status") == BLOCKED_PROFILE_STATUS:
        _write_report(run_dir, board_profile)
        return board_profile
    selected_profile = _select_vitis_profile(args, run_dir, candidates, board_profile)
    if selected_profile.get("status") == BLOCKED_VERSION_STATUS:
        _write_report(run_dir, selected_profile)
        return selected_profile
    selected_profile = _merge_profile_fields(selected_profile, board_profile)
    if args.target_part and not str(selected_profile.get("target_part") or "").strip():
        selected_profile["target_part"] = str(args.target_part)
    if not str(selected_profile.get("target_part") or "").strip():
        inferred_target_part = _probe_target_part_hint(server, settings, helper) or _infer_target_part_from_server(settings, server)
        if inferred_target_part: selected_profile["target_part"] = inferred_target_part
    selected_profile = _merge_profile_fields(
        selected_profile,
        _resolve_board_platform_selection(args, server, remote_workdir, selected_profile, config["directory_contract"]),
    )
    if not str(selected_profile.get("target_part") or "").strip():
        inferred_target_part = _infer_target_part_from_platform_selection(selected_profile)
        if inferred_target_part:
            selected_profile["target_part"] = inferred_target_part
    platform_probe = _probe_platform_name(server, settings, helper, selected_profile)
    platform_upload: dict[str, Any] = {}
    if platform_probe["status"] != PASS_STATUS:
        upload_selection = _local_board_platform_upload_selection(remote_workdir, selected_profile, platform_probe, config["directory_contract"])
        if upload_selection:
            try:
                platform_upload = _upload_local_board_platform_payload(helper, settings, server, run_dir, remote_workdir, upload_selection)
            except RemoteAcceptanceError as exc:
                platform_upload = {"status": FAILED_STATUS, "error": str(exc), "selection": upload_selection}
            if platform_upload.get("status") == PASS_STATUS:
                selected_profile = _merge_profile_fields(selected_profile, platform_upload["selection"])
                platform_probe = _probe_platform_name(server, settings, helper, selected_profile)
    if platform_probe.get("selected_platform") and not str(selected_profile.get("platform_name") or "").strip():
        selected_profile["platform_name"] = str(platform_probe["selected_platform"])
    if platform_probe.get("selected_xpfm") and not str(selected_profile.get("remote_xpfm") or "").strip():
        selected_profile["remote_xpfm"] = str(platform_probe["selected_xpfm"])
    hardware_probe = _probe_hardware_fingerprint(server, settings, helper, selected_profile)
    toolchain_probe = _probe_board_toolchain(server, settings, helper, selected_profile)
    blocking_reasons: list[str] = []
    if not str(selected_profile.get("platform_name") or "").strip():
        blocking_reasons.append("missing_platform_name")
    if not str(selected_profile.get("target_part") or "").strip():
        blocking_reasons.append("missing_target_part")
    if platform_probe["status"] != PASS_STATUS:
        blocking_reasons.append("platform_probe")
    if hardware_probe["status"] != PASS_STATUS:
        blocking_reasons.append("hardware_probe")
    if toolchain_probe["status"] != PASS_STATUS:
        blocking_reasons.append("toolchain_probe")
    if blocking_reasons:
        upload_plan = _board_platform_upload_plan(run_dir, server, remote_workdir, selected_profile, platform_probe, config["directory_contract"])
        result = {
            "status": BLOCKED_BOARD_STATUS,
            "mode": "board",
            "server": server,
            "profile": args.profile,
            "readiness": args.readiness,
            "example_spec": args.example_spec,
            "run_dir": str(run_dir),
            "topology": topology["topology"],
            "steps": plan,
            "blocking_reasons": blocking_reasons,
            "platform_probe": platform_probe,
            "platform_upload": platform_upload,
            "hardware_probe": hardware_probe,
            "toolchain_probe": toolchain_probe,
            "platform_upload_plan": upload_plan,
            "uses_erie_remote_ssh": True,
        }
        _write_report(run_dir, result)
        return result

    artifact_dir = _generate_local_hls_artifacts(run_dir, comment_language=args.comment_language, example_spec=args.example_spec)
    package_path, board_metadata = _create_board_package(run_dir, artifact_dir, example_spec=args.example_spec)
    layout = remote_directory_layout_for_workdir(remote_workdir, run_dir.name)
    request_paths: list[str] = []
    request_paths.extend(_ensure_remote_project_layout(helper, settings, server, layout))
    request_paths.extend(_transfer_package_by_request_commands(helper, settings, server, layout["active_run_relative"], package_path))
    command = _remote_board_command(layout["active_run_dir"], selected_profile, board_metadata)
    detached = helper.exec_detached(server, "run board-level HLS acceptance", command, settings=settings)
    job_result = helper.wait_for_job(server, detached["job_id"], settings=settings, max_wait_s=max(helper.timeout, 5400))
    request_paths.append(detached["manifest"])
    if job_result["status"] != "succeeded":
        tail = _safe_tail_log(helper, server, detached["job_id"], settings)
        result = {
            "status": FAILED_STATUS,
            "mode": "board",
            "server": server,
            "profile": args.profile,
            "readiness": args.readiness,
            "example_spec": args.example_spec,
            "run_dir": str(run_dir),
            "topology": topology["topology"],
            "steps": plan,
            "hardware_probe": hardware_probe,
            "toolchain_probe": toolchain_probe,
            "job_status": job_result["status"],
            "job_output": job_result["output"],
            "tail_log": tail,
            "uses_erie_remote_ssh": True,
        }
        _write_report(run_dir, result)
        return result
    request_paths.append(_archive_remote_run(helper, settings, server, layout))
    result = {
        "status": PASS_STATUS,
        "mode": "board",
        "server": server,
        "topology": topology["topology"],
        "profile": args.profile,
        "vitis_version": str(selected_profile.get("version") or ""),
        "readiness": args.readiness,
        "example_spec": args.example_spec,
        "run_dir": str(run_dir),
        "artifact_dir": str(artifact_dir),
        "run_id": layout["run_id"],
        "remote_project_root": layout["project_root_relative"],
        "remote_project_root_abs": layout["project_root"],
        "remote_conda_prefix": layout["conda_prefix_relative"],
        "remote_conda_prefix_abs": layout["conda_prefix"],
        "remote_run_dir": layout["active_run_relative"],
        "remote_run_dir_abs": layout["active_run_dir"],
        "remote_backup_dir": layout["backup_run_relative"],
        "remote_backup_dir_abs": layout["backup_run_dir"],
        "remote_dir": layout["backup_run_relative"],
        "cleanup_performed": False,
        "remote_artifacts_retained": True,
        "archived_after_verification": True,
        "archive_trigger": config["directory_contract"]["archive_trigger"],
        "requests": request_paths,
        "job_id": detached["job_id"],
        "job_status": job_result["status"],
        "platform_probe": platform_probe,
        "platform_upload": platform_upload,
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
        "uses_erie_remote_ssh": True,
    }
    _write_report(run_dir, result)
    return result
def _resolve_board_platform_selection(
    args: argparse.Namespace,
    server: str,
    remote_workdir: str,
    selected_profile: dict[str, Any],
    directory_contract: dict[str, Any],
) -> dict[str, Any]:
    explicit = _explicit_board_platform_selection(args, remote_workdir)
    if explicit:
        set_board_platform_selection(server, explicit)
        return explicit
    saved = get_board_platform_selection(server)
    if saved:
        normalized_saved = dict(saved)
        normalized_saved["remote_platform_root"] = _normalize_remote_platform_path(remote_workdir, str(saved.get("remote_platform_root") or ""))
        normalized_saved["remote_xpfm"] = _normalize_remote_platform_path(remote_workdir, str(saved.get("remote_xpfm") or ""))
        return normalized_saved
    platform_name = str(selected_profile.get("platform_name") or "").strip()
    if not platform_name:
        platform_name = _default_platform_name_for_part(str(selected_profile.get("target_part") or ""))
    if not platform_name:
        return {}
    return _governed_remote_platform_selection(remote_workdir, platform_name, directory_contract)
def _explicit_board_platform_selection(args: argparse.Namespace, remote_workdir: str) -> dict[str, Any]:
    if not any(str(getattr(args, field, "") or "").strip() for field in ("platform_name", "remote_platform_root", "remote_xpfm")):
        return {}
    return {
        "platform_name": str(getattr(args, "platform_name", "") or "").strip(),
        "remote_platform_root": _normalize_remote_platform_path(remote_workdir, str(getattr(args, "remote_platform_root", "") or "").strip()),
        "remote_xpfm": _normalize_remote_platform_path(remote_workdir, str(getattr(args, "remote_xpfm", "") or "").strip()),
        "source": "upload",
    }
def _normalize_remote_platform_path(remote_workdir: str, raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute():
        return path.as_posix()
    return (PurePosixPath(remote_workdir) / path).as_posix()
def _default_platform_name_for_part(target_part: str) -> str:
    normalized = str(target_part or "").strip().lower()
    if "u55c" in normalized:
        return "xilinx_u55c_gen3x16_xdma_3_202210_1"
    if "u50" in normalized:
        return "xilinx_u50_gen3x16_xdma_5_202210_1"
    return ""
def _governed_remote_platform_selection(remote_workdir: str, platform_name: str, directory_contract: dict[str, Any]) -> dict[str, Any]:
    project_root = PurePosixPath(remote_workdir) / str(directory_contract["project_root_dirname"])
    root_template = str(directory_contract["platform_root_path_template"]).replace("<platform-name>", platform_name)
    root = (project_root / PurePosixPath(root_template)).as_posix()
    return {
        "platform_name": platform_name,
        "remote_platform_root": root,
        "remote_xpfm": (PurePosixPath(root) / f"{platform_name}.xpfm").as_posix(),
        "source": "upload",
    }
def _local_board_platform_upload_selection(
    remote_workdir: str,
    selected_profile: dict[str, Any],
    platform_probe: dict[str, Any],
    directory_contract: dict[str, Any],
) -> dict[str, Any]:
    platform_name = str(selected_profile.get("platform_name") or platform_probe.get("suggested_platform_name") or _default_platform_name_for_part(str(selected_profile.get("target_part") or ""))).strip()
    if platform_name != U55C_PLATFORM_NAME:
        return {}
    selection = _governed_remote_platform_selection(remote_workdir, platform_name, directory_contract)
    if str(selected_profile.get("remote_platform_root") or "").strip():
        selection["remote_platform_root"] = str(selected_profile["remote_platform_root"]).strip()
    if str(selected_profile.get("remote_xpfm") or "").strip():
        selection["remote_xpfm"] = str(selected_profile["remote_xpfm"]).strip()
    return selection
def _upload_local_board_platform_payload(
    helper: ErieHelper,
    settings: Path,
    server: str,
    run_dir: Path,
    remote_workdir: str,
    selection: dict[str, Any],
    *,
    local_root: Path | None = None,
) -> dict[str, Any]:
    platform_name = str(selection.get("platform_name") or "").strip()
    if platform_name != U55C_PLATFORM_NAME:
        return {"status": "skipped", "reason": "only the governed U55C payload has a fixed local dependency source", "selection": selection}
    prepared = prepare_local_u55c_platform_archive(run_dir / "platform-upload", local_root=local_root)
    if prepared.get("status") != PASS_STATUS:
        return {
            "status": BLOCKED_BOARD_STATUS,
            "reason": "invalid_local_u55c_platform_payload",
            "local_payload": prepared,
            "selection": selection,
        }
    archive_path = Path(str(prepared["archive_path"]))
    remote_root = PurePosixPath(str(selection["remote_platform_root"]))
    remote_parent = remote_root.parent
    remote_archive_abs = remote_parent / archive_path.name
    remote_archive_rel = _remote_relative_to_workdir(remote_workdir, remote_archive_abs)
    remote_parent_rel = _remote_relative_to_workdir(remote_workdir, remote_parent)
    mkdir_request = helper.request_and_run(
        settings,
        server,
        "command",
        f"mkdir -p {shlex.quote(remote_parent_rel)}",
        "prepare governed remote board platform directory",
    )
    upload_request = helper.request_upload_and_run(settings, server, archive_path, remote_archive_rel, "upload U55C platform payload")
    remote_xpfm = str(selection["remote_xpfm"])
    command = (
        f"mkdir -p {shlex.quote(remote_parent.as_posix())} && "
        f"tar -xzf {shlex.quote(remote_archive_abs.as_posix())} -C {shlex.quote(remote_parent.as_posix())} && "
        f"test -f {shlex.quote(remote_xpfm)}"
    )
    extract_request = helper.request_and_run(settings, server, "command", command, "extract U55C platform payload")
    set_board_platform_selection(server, selection)
    return {
        "status": PASS_STATUS,
        "platform_name": platform_name,
        "archive_path": str(archive_path),
        "remote_archive": remote_archive_abs.as_posix(),
        "remote_archive_relative": remote_archive_rel,
        "remote_platform_root": str(selection["remote_platform_root"]),
        "remote_xpfm": remote_xpfm,
        "selection": selection,
        "local_payload": prepared,
        "requests": [mkdir_request, upload_request, extract_request],
    }
def _remote_relative_to_workdir(remote_workdir: str, remote_path: PurePosixPath) -> str:
    workdir = PurePosixPath(remote_workdir)
    try:
        return remote_path.relative_to(workdir).as_posix()
    except ValueError:
        return remote_path.as_posix().lstrip("/")
def _board_platform_upload_plan(
    run_dir: Path,
    server: str,
    remote_workdir: str,
    selected_profile: dict[str, Any],
    platform_probe: dict[str, Any],
    directory_contract: dict[str, Any],
) -> dict[str, Any]:
    platform_name = str(selected_profile.get("platform_name") or platform_probe.get("suggested_platform_name") or _default_platform_name_for_part(str(selected_profile.get("target_part") or ""))).strip()
    if not platform_name:
        return {}
    selection = _governed_remote_platform_selection(remote_workdir, platform_name, directory_contract)
    local_payload = (
        validate_local_board_platform_payload(default_local_u55c_payload_root(), expected_platform_name=U55C_PLATFORM_NAME)
        if platform_name == U55C_PLATFORM_NAME
        else {}
    )
    upload_plan = {
        "server": server,
        "platform_name": platform_name,
        "source": "upload",
        "expected_local_directory": platform_name,
        "local_payload": local_payload,
        "remote_platform_root": selection["remote_platform_root"],
        "remote_xpfm": selection["remote_xpfm"],
        "recommended_steps": [
            f"tar the local platform directory {platform_name}/ into a single archive",
            f"upload the archive to {server} under {selection['remote_platform_root']}",
            f"extract the archive so that {selection['remote_xpfm']} exists on the remote host",
            f"rerun scripts/python/remote/remote_vitis_acceptance.py --mode board --server {server} --platform-name {platform_name} --remote-platform-root {selection['remote_platform_root']} --remote-xpfm {selection['remote_xpfm']}",
        ],
        "recommended_commands": [
            f"python %CODEX_HOME%/skills/erie-remote-ssh/scripts/remote_ssh.py request-upload --settings <erie-settings.json> --server {server} --local <local-platform-archive> --remote erie-hls-generator/platforms/alveo/{platform_name}.tar.gz --reason \"upload U55C platform payload\"",
            f"python %CODEX_HOME%/skills/erie-remote-ssh/scripts/remote_ssh.py request-command --settings <erie-settings.json> --server {server} --reason \"extract U55C platform payload\" -- bash -lc \"mkdir -p {shlex.quote(selection['remote_platform_root'])} && tar -xzf erie-hls-generator/platforms/alveo/{platform_name}.tar.gz -C {shlex.quote(selection['remote_platform_root'])} --strip-components=1\"",
        ],
    }
    request_path = run_dir / "remote_board_platform_request.json"
    _write_json(request_path, upload_plan)
    upload_plan["request_path"] = str(request_path)
    return upload_plan
def _create_board_package(run_dir: Path, artifact_dir: Path, *, example_spec: str) -> tuple[Path, dict[str, Any]]:
    metadata = _board_metadata_for_spec(example_spec)
    top_function = str(metadata["top_function"])
    host_template = str(metadata["host_template"])
    host_source = _render_board_host(example_spec, top_function, host_template)
    board_dir = run_dir / "board"
    board_dir.mkdir(parents=True, exist_ok=True)
    host_path = board_dir / "host.cpp"
    host_path.write_text(host_source, encoding="utf-8", newline="\n")
    runner = run_dir / "run_board_validation.sh"
    runner.write_text(_board_runner_script(top_function), encoding="utf-8", newline="\n")
    package_path = run_dir / "board_artifacts.tar.gz"
    with tarfile.open(package_path, "w:gz") as tar:
        for path in sorted(artifact_dir.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=Path("artifacts") / path.relative_to(artifact_dir))
        tar.add(host_path, arcname="board/host.cpp")
        tar.add(runner, arcname="run_board_validation.sh")
    return package_path, metadata
def _board_metadata_for_spec(example_spec: str) -> dict[str, Any]:
    spec = _load_example_spec(example_spec)
    board_config = board_acceptance_config(spec)
    if str(board_config.get("profile") or "").strip() != BOARD_RUNNABLE_PROFILE:
        raise RemoteAcceptanceError(f"Example spec {example_spec} is not declared board-runnable.")
    return {
        "example_spec": example_spec,
        "top_function": str(spec.get("interfaces", {}).get("top_function") or spec.get("name") or "kernel"),
        "host_template": str(board_config.get("host_template") or "").strip(),
        "profile": str(board_config.get("profile") or ""),
    }
def _render_board_host(example_spec: str, top_function: str, template_name: str) -> str:
    spec = _load_example_spec(example_spec)
    if template_name in {"unary_memory_host", "binary_memory_host", "matrix_memory_host", "wrapper_unary_memory_host"}:
        return _render_vector_driven_board_host(spec, top_function, template_name)
    template_path = resolve_host_template_path(SKILL_ROOT, template_name)
    text = template_path.read_text(encoding="utf-8")
    rendered = text.replace("{{TOP_FUNCTION}}", top_function)
    if "{{TOP_FUNCTION}}" in rendered:
        raise RemoteAcceptanceError(f"Board host template {template_name!r} was not rendered completely for {example_spec}.")
    return rendered
def _render_vector_driven_board_host(spec: dict[str, Any], top_function: str, template_name: str) -> str:
    vectors = (spec.get("workflow") or {}).get("mock_vectors")
    if not isinstance(vectors, list) or not vectors:
        raise RemoteAcceptanceError(f"Board host template {template_name!r} requires workflow.mock_vectors in {spec.get('name')!r}.")
    case = vectors[0]
    inputs = case.get("inputs", {}) if isinstance(case, dict) else {}
    expected_outputs = case.get("expected_outputs", {}) if isinstance(case, dict) else {}
    arguments = {
        str(item.get("name")): item
        for item in spec.get("interfaces", {}).get("arguments", [])
        if isinstance(item, dict) and item.get("name")
    }
    if template_name in {"unary_memory_host", "wrapper_unary_memory_host"}:
        if not {"input", "output"}.issubset(arguments):
            raise RemoteAcceptanceError(f"Board host template {template_name!r} requires unary memory arguments in {spec.get('name')!r}.")
        values = [int(item) for item in inputs.get("input", [])]
        expected = [int(item) for item in expected_outputs.get("output", [])]
        length = int(inputs.get("length", len(values)))
        wrapper_comment = "// Wrapper-backed board validation keeps a memory-facing host while the kernel preserves internal stream staging.\n" if template_name == "wrapper_unary_memory_host" else ""
        return f'''#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

int main(int argc, char** argv) {{
  if (argc < 2) {{
    std::cerr << "usage: host <xclbin>\\n";
    return 2;
  }}
  const std::string xclbin_path = argv[1];
  const int length = {length};
{wrapper_comment}  std::vector<std::uint32_t> input = {{{_vector_literal(values)}}};
  std::vector<std::uint32_t> output(length, 0U);
  std::vector<std::uint32_t> expected = {{{_vector_literal(expected)}}};

  auto device = xrt::device(0);
  auto uuid = device.load_xclbin(xclbin_path);
  auto kernel = xrt::kernel(device, uuid, "{top_function}");
  auto in_bo = xrt::bo(device, sizeof(std::uint32_t) * input.size(), kernel.group_id(0));
  auto out_bo = xrt::bo(device, sizeof(std::uint32_t) * output.size(), kernel.group_id(1));
  auto in_map = in_bo.map<std::uint32_t*>();
  auto out_map = out_bo.map<std::uint32_t*>();
  std::copy(input.begin(), input.end(), in_map);
  std::fill(out_map, out_map + output.size(), 0U);
  in_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);
  out_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);

  auto run = kernel(in_bo, out_bo, length);
  run.wait();
  out_bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE);
  std::copy(out_map, out_map + output.size(), output.begin());

  bool pass = true;
  for (int i = 0; i < length; ++i) {{
    if (output[i] != expected[i]) {{
      pass = false;
      break;
    }}
  }}
  std::cout << "HLS_BOARD_STATUS " << (pass ? "passed" : "failed") << "\\n";
  return pass ? 0 : 1;
}}
'''
    if template_name == "binary_memory_host":
        if not {"input_a", "input_b", "output"}.issubset(arguments):
            raise RemoteAcceptanceError(f"Board host template {template_name!r} requires binary memory arguments in {spec.get('name')!r}.")
        a_values = [int(item) for item in inputs.get("input_a", [])]
        b_values = [int(item) for item in inputs.get("input_b", [])]
        expected = [int(item) for item in expected_outputs.get("output", [])]
        length = int(inputs.get("length", min(len(a_values), len(b_values), len(expected))))
        return f'''#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

int main(int argc, char** argv) {{
  if (argc < 2) {{
    std::cerr << "usage: host <xclbin>\\n";
    return 2;
  }}
  const std::string xclbin_path = argv[1];
  const int length = {length};
  std::vector<std::uint32_t> input_a = {{{_vector_literal(a_values)}}};
  std::vector<std::uint32_t> input_b = {{{_vector_literal(b_values)}}};
  std::vector<std::uint32_t> output(length, 0U);
  std::vector<std::uint32_t> expected = {{{_vector_literal(expected)}}};

  auto device = xrt::device(0);
  auto uuid = device.load_xclbin(xclbin_path);
  auto kernel = xrt::kernel(device, uuid, "{top_function}");
  auto in_a_bo = xrt::bo(device, sizeof(std::uint32_t) * input_a.size(), kernel.group_id(0));
  auto in_b_bo = xrt::bo(device, sizeof(std::uint32_t) * input_b.size(), kernel.group_id(1));
  auto out_bo = xrt::bo(device, sizeof(std::uint32_t) * output.size(), kernel.group_id(2));
  auto in_a_map = in_a_bo.map<std::uint32_t*>();
  auto in_b_map = in_b_bo.map<std::uint32_t*>();
  auto out_map = out_bo.map<std::uint32_t*>();
  std::copy(input_a.begin(), input_a.end(), in_a_map);
  std::copy(input_b.begin(), input_b.end(), in_b_map);
  std::fill(out_map, out_map + output.size(), 0U);
  in_a_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);
  in_b_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);
  out_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);

  auto run = kernel(in_a_bo, in_b_bo, out_bo, length);
  run.wait();
  out_bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE);
  std::copy(out_map, out_map + output.size(), output.begin());

  bool pass = true;
  for (int i = 0; i < length; ++i) {{
    if (output[i] != expected[i]) {{
      pass = false;
      break;
    }}
  }}
  std::cout << "HLS_BOARD_STATUS " << (pass ? "passed" : "failed") << "\\n";
  return pass ? 0 : 1;
}}
'''
    if template_name == "matrix_memory_host":
        if not {"input", "output"}.issubset(arguments):
            raise RemoteAcceptanceError(f"Board host template {template_name!r} requires matrix memory arguments in {spec.get('name')!r}.")
        values = [int(item) for item in inputs.get("input", [])]
        expected = [int(item) for item in expected_outputs.get("output", [])]
        rows = int(inputs.get("rows", 1))
        cols = int(inputs.get("cols", len(values)))
        length = rows * cols
        return f'''#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#include <xrt/xrt_bo.h>
#include <xrt/xrt_device.h>
#include <xrt/xrt_kernel.h>

int main(int argc, char** argv) {{
  if (argc < 2) {{
    std::cerr << "usage: host <xclbin>\\n";
    return 2;
  }}
  const std::string xclbin_path = argv[1];
  const int rows = {rows};
  const int cols = {cols};
  const int length = rows * cols;
  std::vector<std::uint32_t> input = {{{_vector_literal(values)}}};
  std::vector<std::uint32_t> output(length, 0U);
  std::vector<std::uint32_t> expected = {{{_vector_literal(expected)}}};

  auto device = xrt::device(0);
  auto uuid = device.load_xclbin(xclbin_path);
  auto kernel = xrt::kernel(device, uuid, "{top_function}");
  auto in_bo = xrt::bo(device, sizeof(std::uint32_t) * input.size(), kernel.group_id(0));
  auto out_bo = xrt::bo(device, sizeof(std::uint32_t) * output.size(), kernel.group_id(1));
  auto in_map = in_bo.map<std::uint32_t*>();
  auto out_map = out_bo.map<std::uint32_t*>();
  std::copy(input.begin(), input.end(), in_map);
  std::fill(out_map, out_map + output.size(), 0U);
  in_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);
  out_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);

  auto run = kernel(in_bo, out_bo, rows, cols);
  run.wait();
  out_bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE);
  std::copy(out_map, out_map + output.size(), output.begin());

  bool pass = true;
  for (int i = 0; i < length; ++i) {{
    if (output[i] != expected[i]) {{
      pass = false;
      break;
    }}
  }}
  std::cout << "HLS_BOARD_STATUS " << (pass ? "passed" : "failed") << "\\n";
  return pass ? 0 : 1;
}}
'''
    raise RemoteAcceptanceError(f"Unsupported dynamic board host template {template_name!r}.")
def _vector_literal(values: list[int]) -> str:
    return ", ".join(str(int(item)) for item in values) if values else "0"
def _remote_board_command(remote_dir: str, profile: dict[str, Any], metadata: dict[str, Any]) -> str:
    settings_script = shlex.quote(str(profile["settings_script"]))
    platform_name = shlex.quote(str(profile.get("platform_spec") or profile.get("remote_xpfm") or profile["platform_name"]))
    target_part = shlex.quote(str(profile.get("target_part", "")))
    top_function = shlex.quote(str(metadata["top_function"]))
    xrt_setup_script = str(profile.get("xrt_setup_script") or "").strip()
    xrt_setup_arg = shlex.quote(xrt_setup_script)
    vpp_tool = shlex.quote(str(profile.get("vpp_path") or "v++"))
    xrt_tool = shlex.quote(str(profile.get("xrt_tool_path") or ""))
    remote = shlex.quote(remote_dir)
    return (
        f"cd {remote} && base64 -d hls_artifacts.tar.gz.b64 > board_artifacts.tar.gz && "
        "tar -xzf board_artifacts.tar.gz && "
        f"HLS_SETTINGS_SCRIPT={settings_script} HLS_PLATFORM_NAME={platform_name} "
        f"HLS_TARGET_PART={target_part} HLS_TOP_FUNCTION={top_function} "
        f"HLS_XRT_SETUP_SCRIPT={xrt_setup_arg} HLS_VPP_TOOL={vpp_tool} HLS_XRT_TOOL={xrt_tool} bash run_board_validation.sh"
    )
def _board_runner_script(top_function: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
: "${{HLS_SETTINGS_SCRIPT:?}}"
: "${{HLS_PLATFORM_NAME:?}}"
: "${{HLS_TOP_FUNCTION:?}}"
HLS_TARGET_PART="${{HLS_TARGET_PART:-}}"
HLS_XRT_SETUP_SCRIPT="${{HLS_XRT_SETUP_SCRIPT:-}}"
HLS_VPP_TOOL="${{HLS_VPP_TOOL:-v++}}"
HLS_XRT_TOOL="${{HLS_XRT_TOOL:-}}"
source "$HLS_SETTINGS_SCRIPT" >/dev/null 2>&1 || true
if [ -n "$HLS_XRT_SETUP_SCRIPT" ] && [ -f "$HLS_XRT_SETUP_SCRIPT" ]; then
  source "$HLS_XRT_SETUP_SCRIPT" >/dev/null 2>&1 || true
fi
if ! command -v "$HLS_VPP_TOOL" >/dev/null 2>&1 && [ ! -x "$HLS_VPP_TOOL" ]; then
  echo "{BOARD_STATUS_MARKER} blocked_vpp"
  exit 45
fi
if ! command -v g++ >/dev/null 2>&1; then
  echo "{BOARD_STATUS_MARKER} blocked_gpp"
  exit 46
fi
if ! command -v xrt-smi >/dev/null 2>&1 && ! command -v xbutil >/dev/null 2>&1 && {{ [ -z "$HLS_XRT_TOOL" ] || [ ! -x "$HLS_XRT_TOOL" ]; }}; then
  echo "{BOARD_STATUS_MARKER} blocked_xrt"
  exit 47
fi
XRT_INCLUDE_DIR="${{XILINX_XRT:-/opt/xilinx/xrt}}/include"
XRT_LIB_DIR="${{XILINX_XRT:-/opt/xilinx/xrt}}/lib"
export LD_LIBRARY_PATH="$XRT_LIB_DIR${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
cd artifacts
SRC_FILE="$(find src -maxdepth 1 -type f \\( -name '*.cpp' -o -name '*.cc' -o -name '*.cxx' \\) | head -n 1)"
if [ -z "$SRC_FILE" ]; then
  echo "{BOARD_STATUS_MARKER} missing_kernel_source"
  exit 48
fi
"$HLS_VPP_TOOL" -c -t hw --platform "$HLS_PLATFORM_NAME" -k "$HLS_TOP_FUNCTION" "$SRC_FILE" -o kernel.xo
"$HLS_VPP_TOOL" -l -t hw --platform "$HLS_PLATFORM_NAME" kernel.xo -o kernel.xclbin
g++ -std=c++17 -O2 ../board/host.cpp -I"$XRT_INCLUDE_DIR" -L"$XRT_LIB_DIR" -Wl,-rpath,"$XRT_LIB_DIR" -lxrt_coreutil -pthread -o host.exe
set +e
./host.exe kernel.xclbin 2>&1 | tee board_run.log
host_rc=${{PIPESTATUS[0]}}
set -e
if [ "$host_rc" -ne 0 ] && grep -qi "Permission denied Device index 0" board_run.log && command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  set +e
  sudo -n env LD_LIBRARY_PATH="$LD_LIBRARY_PATH" XILINX_XRT="${{XILINX_XRT:-/opt/xilinx/xrt}}" ./host.exe kernel.xclbin 2>&1 | tee board_run.log
  host_rc=${{PIPESTATUS[0]}}
  set -e
fi
if [ "$host_rc" -ne 0 ]; then
  exit "$host_rc"
fi
grep -q "{BOARD_STATUS_MARKER} passed" board_run.log
echo "{BOARD_STATUS_MARKER} passed"
"""
