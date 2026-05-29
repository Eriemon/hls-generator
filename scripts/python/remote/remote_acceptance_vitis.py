#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
import tarfile
from pathlib import Path
from typing import Any

from runtime.hls_generator.workspace import use_workspace_root

from remote_acceptance_common import (
    BLOCKED_PROFILE_STATUS,
    BLOCKED_VERSION_STATUS,
    BLOCKED_VITIS_STATUS,
    PASS_STATUS,
    RemoteAcceptanceError,
    get_vitis_selection,
    repo_root,
    remote_validation_config,
    set_vitis_selection,
    skill_root,
    skill_config_path,
    user_config_path,
    remote_directory_layout_for_workdir,
    _archive_remote_run,
    _ensure_remote_project_layout,
    _field_from_output,
    _new_run_dir,
    _probe_remote_workdir,
    _probe_target_part_hint,
    _probe_vitis,
    _resolve_erie_server_list,
    run_hls_workflow,
    _write_json,
    _write_erie_settings_overlay,
    _write_report,
)

def _run_vitis_mode(args: argparse.Namespace, config: dict[str, Any], helper: "ErieHelper", plan: list[str], topology: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("vitis_profiles", {})
    run_dir = _new_run_dir(config, "vitis")
    settings = _write_erie_settings_overlay(config, run_dir)
    server = topology["server"]
    helper.preflight(server, settings=settings)
    helper.scan_software(server, settings=settings)
    candidates = _vitis_version_candidates(config, settings, server)
    profile = _resolve_profile_config(
        args,
        run_dir,
        candidates=candidates,
        configured_profiles=profiles,
        required_fields=("settings_script", "expected_tool"),
    )
    if profile.get("status") == BLOCKED_PROFILE_STATUS:
        _write_report(run_dir, profile)
        return profile
    selected_profile = _select_vitis_profile(args, run_dir, candidates, profile)
    if selected_profile.get("status") == BLOCKED_VERSION_STATUS:
        _write_report(run_dir, selected_profile)
        return selected_profile

    if args.target_part and not str(selected_profile.get("target_part") or "").strip():
        selected_profile = {**selected_profile, "target_part": str(args.target_part)}
    profile_probe = _probe_vitis(server, settings, helper, selected_profile)
    if profile_probe["status"] != PASS_STATUS:
        result = {
            "status": BLOCKED_VITIS_STATUS,
            "mode": "vitis",
            "server": server,
            "profile": args.profile,
            "vitis_version": selected_profile.get("version"),
            "readiness": args.readiness,
            "run_dir": str(run_dir),
            "topology": topology["topology"],
            "steps": plan,
            "probe": profile_probe,
            "uses_erie_remote_ssh": True,
        }
        _write_report(run_dir, result)
        return result
    selected_profile = {
        **selected_profile,
        "expected_tool": str(profile_probe.get("resolved_tool") or selected_profile.get("expected_tool")),
        "tool_path": str(profile_probe.get("tool_path") or ""),
    }
    if not str(selected_profile.get("target_part") or "").strip():
        inferred_target_part = _probe_target_part_hint(server, settings, helper)
        if inferred_target_part:
            selected_profile["target_part"] = inferred_target_part

    artifact_dir = _generate_local_hls_artifacts(run_dir, comment_language=args.comment_language, example_spec=args.example_spec)
    package_path = _create_vitis_package(run_dir, artifact_dir)
    remote_workdir = _probe_remote_workdir(server, settings, helper)
    result = _run_server_vitis_phase(
        helper,
        settings,
        server,
        selected_profile,
        args.readiness,
        package_path,
        config,
        run_dir,
        phase_label="single",
        cleanup_remote=args.cleanup_remote,
        remote_workdir=remote_workdir,
    )
    result.update(
        {
            "mode": "vitis",
            "topology": topology["topology"],
            "profile": args.profile,
            "example_spec": args.example_spec,
            "run_dir": str(run_dir),
            "artifact_dir": str(artifact_dir),
            "uses_erie_remote_ssh": True,
        }
    )
    _write_report(run_dir, result)
    return result
def _run_split_vitis_mode(args: argparse.Namespace, config: dict[str, Any], helper: "ErieHelper", plan: list[str], topology: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("vitis_profiles", {})
    run_dir = _new_run_dir(config, "vitis-split")
    settings = _write_erie_settings_overlay(config, run_dir)
    build_server = topology["build_server"]
    validate_server = topology["validate_server"]

    helper.preflight(build_server, settings=settings)
    helper.preflight(validate_server, settings=settings)
    helper.scan_software(build_server, settings=settings)
    helper.scan_software(validate_server, settings=settings)

    build_candidates = _vitis_version_candidates(config, settings, build_server)
    validate_candidates = _vitis_version_candidates(config, settings, validate_server)
    shared_version = _select_shared_vitis_version(args, build_candidates, validate_candidates)
    build_profile = _resolve_profile_for_version(build_server, build_candidates, profiles, shared_version)
    validate_profile = _resolve_profile_for_version(validate_server, validate_candidates, profiles, shared_version)
    target_part = _resolve_target_part(args, settings, validate_server, validate_profile, build_profile)
    if not target_part:
        blocked = _blocked_profile_config(
            argparse.Namespace(
                server=build_server,
                profile=args.profile,
                readiness=args.readiness,
                example_spec=args.example_spec,
            ),
            run_dir,
            missing_fields=["target_part"],
            configured_profiles=profiles,
        )
        blocked["topology"] = topology["topology"]
        blocked["build_server"] = build_server
        blocked["validate_server"] = validate_server
        blocked["vitis_version"] = shared_version
        _write_report(run_dir, blocked)
        return blocked

    build_profile = {**build_profile, "target_part": target_part}
    validate_profile = {**validate_profile, "target_part": target_part}
    build_workdir = _probe_remote_workdir(build_server, settings, helper)
    validate_workdir = _probe_remote_workdir(validate_server, settings, helper)

    build_probe = _probe_vitis(build_server, settings, helper, build_profile)
    validate_probe = _probe_vitis(validate_server, settings, helper, validate_profile)
    device_probe = _probe_fpga_presence(validate_server, settings, helper)
    if build_probe["status"] != PASS_STATUS or validate_probe["status"] != PASS_STATUS or device_probe["status"] != PASS_STATUS:
        result = {
            "status": BLOCKED_VITIS_STATUS,
            "mode": "vitis",
            "topology": topology["topology"],
            "build_server": build_server,
            "validate_server": validate_server,
            "vitis_version": shared_version,
            "target_part": target_part,
            "run_dir": str(run_dir),
            "steps": plan,
            "build_probe": build_probe,
            "validate_probe": validate_probe,
            "device_probe": device_probe,
            "uses_erie_remote_ssh": True,
        }
        _write_report(run_dir, result)
        return result
    build_profile = {
        **build_profile,
        "expected_tool": str(build_probe.get("resolved_tool") or build_profile.get("expected_tool")),
        "tool_path": str(build_probe.get("tool_path") or ""),
    }
    validate_profile = {
        **validate_profile,
        "expected_tool": str(validate_probe.get("resolved_tool") or validate_profile.get("expected_tool")),
        "tool_path": str(validate_probe.get("tool_path") or ""),
    }

    artifact_dir = _generate_local_hls_artifacts(run_dir, comment_language=args.comment_language, example_spec=args.example_spec)
    package_path = _create_vitis_package(run_dir, artifact_dir)

    build_result = _run_server_vitis_phase(
        helper,
        settings,
        build_server,
        build_profile,
        args.readiness,
        package_path,
        config,
        run_dir,
        phase_label="build",
        cleanup_remote=args.cleanup_remote,
        remote_workdir=build_workdir,
    )
    validate_result = _run_server_vitis_phase(
        helper,
        settings,
        validate_server,
        validate_profile,
        args.readiness,
        package_path,
        config,
        run_dir,
        phase_label="validation",
        cleanup_remote=args.cleanup_remote,
        remote_workdir=validate_workdir,
    )

    passed = build_result["status"] == PASS_STATUS and validate_result["status"] == PASS_STATUS
    result = {
        "status": PASS_STATUS if passed else FAILED_STATUS,
        "mode": "vitis",
        "topology": topology["topology"],
        "build_server": build_server,
        "validate_server": validate_server,
        "vitis_version": shared_version,
        "target_part": target_part,
        "readiness": args.readiness,
        "example_spec": args.example_spec,
        "run_dir": str(run_dir),
        "steps": plan,
        "build_result": build_result,
        "validation_result": validate_result,
        "uses_erie_remote_ssh": True,
        "remote_artifacts_retained": (build_result.get("remote_artifacts_retained") is True and validate_result.get("remote_artifacts_retained") is True),
    }
    _write_report(run_dir, result)
    return result
def _select_vitis_profile(args: argparse.Namespace, run_dir: Path, candidates: list[dict[str, Any]], fallback_profile: dict[str, Any]) -> dict[str, Any]:
    explicit_version = str(args.vitis_version or "").strip()
    if explicit_version:
        selected = _find_candidate(candidates, explicit_version)
        if not selected:
            raise RemoteAcceptanceError(f"Requested Vitis version {explicit_version!r} was not found on {args.server}.")
        set_vitis_selection(args.server, selected)
        return selected

    saved = get_vitis_selection(args.server)
    if saved:
        candidate = _find_candidate(candidates, str(saved.get("version") or "")) if candidates else None
        if candidate:
            merged = {**candidate, **saved}
            set_vitis_selection(args.server, merged)
            return merged
        if not candidates:
            return saved

    if len(candidates) > 1:
        request = _remote_vitis_version_request(args, run_dir, candidates)
        request_path = run_dir / "remote_vitis_version_request.json"
        _write_json(request_path, request)
        return {
            "status": BLOCKED_VERSION_STATUS,
            "mode": "vitis",
            "server": args.server,
            "profile": args.profile,
            "readiness": args.readiness,
            "example_spec": args.example_spec,
            "run_dir": str(run_dir),
            "remote_vitis_version_request": str(request_path),
            "candidate_versions": candidates,
            "user_config_path": str(user_config_path()),
            "uses_erie_remote_ssh": True,
        }
    if len(candidates) == 1:
        return candidates[0]
    return {
        "version": str(fallback_profile.get("version") or args.profile),
        "settings_script": str(fallback_profile["settings_script"]),
        "expected_tool": str(fallback_profile["expected_tool"]),
        "target_part": str(fallback_profile.get("target_part", "")),
    }
def _select_shared_vitis_version(args: argparse.Namespace, build_candidates: list[dict[str, Any]], validate_candidates: list[dict[str, Any]]) -> str:
    if args.vitis_version:
        return str(args.vitis_version)
    shared = sorted({str(item.get("version")) for item in build_candidates} & {str(item.get("version")) for item in validate_candidates}, key=_version_sort_key)
    if not shared:
        raise RemoteAcceptanceError("No shared Vitis version is available across the selected build and validation servers.")
    return shared[0]
def _version_sort_key(value: str) -> tuple[int, ...]:
    match = re.findall(r"\d+", str(value))
    return tuple(int(item) for item in match) if match else (9999,)
def _resolve_profile_for_version(server: str, candidates: list[dict[str, Any]], configured_profiles: dict[str, Any], version: str) -> dict[str, Any]:
    saved = get_vitis_selection(server)
    if saved and str(saved.get("version") or "") == version and str(saved.get("settings_script") or "").strip() and str(saved.get("expected_tool") or "").strip():
        return saved
    candidate = _find_candidate(candidates, version)
    if candidate:
        set_vitis_selection(server, candidate)
        return candidate
    for _, profile in configured_profiles.items():
        if not isinstance(profile, dict):
            continue
        if str(profile.get("version") or "") == version and str(profile.get("settings_script") or "").strip() and str(profile.get("expected_tool") or "").strip():
            return profile
    raise RemoteAcceptanceError(f"Could not resolve Vitis profile for server {server!r} and version {version!r}.")
def _resolve_target_part(args: argparse.Namespace, settings: Path, validate_server: str, validate_profile: dict[str, Any], build_profile: dict[str, Any]) -> str:
    if str(getattr(args, "target_part", "") or "").strip():
        return str(args.target_part).strip()
    for profile in (validate_profile, build_profile, get_vitis_selection(validate_server) or {}):
        target_part = str(profile.get("target_part") or "").strip() if isinstance(profile, dict) else ""
        if target_part:
            return target_part
    inferred = _infer_target_part_from_server(settings, validate_server)
    return inferred
def _resolve_profile_config(
    args: argparse.Namespace,
    run_dir: Path,
    *,
    candidates: list[dict[str, Any]],
    configured_profiles: dict[str, Any],
    required_fields: tuple[str, ...],
) -> dict[str, Any]:
    explicit_profile = str(args.profile or "").strip()
    if explicit_profile:
        profile = configured_profiles.get(explicit_profile)
        if not isinstance(profile, dict):
            return _blocked_profile_config(args, run_dir, missing_fields=list(required_fields), configured_profiles=configured_profiles)
        resolved = {**profile, "version": str(profile.get("version") or explicit_profile)}
        missing = [field for field in required_fields if not str(resolved.get(field) or "").strip()]
        if missing:
            return _blocked_profile_config(args, run_dir, missing_fields=missing, configured_profiles=configured_profiles)
        return resolved

    saved = get_vitis_selection(args.server)
    if saved:
        missing = [field for field in required_fields if not str(saved.get(field) or "").strip()]
        if not missing:
            return saved

    complete_profiles: list[tuple[str, dict[str, Any]]] = []
    for name, profile in configured_profiles.items():
        if not isinstance(profile, dict):
            continue
        resolved = {**profile, "version": str(profile.get("version") or name)}
        missing = [field for field in required_fields if not str(resolved.get(field) or "").strip()]
        if not missing:
            complete_profiles.append((name, resolved))
    if len(complete_profiles) == 1:
        return complete_profiles[0][1]

    candidate_profiles = [
        item
        for item in candidates
        if all(str(item.get(field) or "").strip() for field in required_fields)
    ]
    if candidate_profiles:
        return dict(candidate_profiles[0])

    return _blocked_profile_config(args, run_dir, missing_fields=list(required_fields), configured_profiles=configured_profiles)
def _blocked_profile_config(
    args: argparse.Namespace,
    run_dir: Path,
    *,
    missing_fields: list[str],
    configured_profiles: dict[str, Any],
) -> dict[str, Any]:
    mode = str(getattr(args, "mode", "vitis") or "vitis")
    recommended_commands = [
        f"python .\\scripts\\python\\remote\\remote_vitis_acceptance.py --mode {mode} --server {args.server} --profile <configured-profile> --readiness {args.readiness} --example-spec {args.example_spec} --json",
        f"python .\\scripts\\python\\remote\\remote_vitis_acceptance.py --mode {mode} --server {args.server} --vitis-version <version> --readiness {args.readiness} --example-spec {args.example_spec} --json",
    ]
    request = {
        "version": 1,
        "action": "ask_remote_vitis_profile_config",
        "question": "Remote Vitis validation requires an explicit configured profile or a previously saved remote selection. Configure the missing values before retrying.",
        "server": args.server,
        "profile": args.profile,
        "readiness": args.readiness,
        "example_spec": args.example_spec,
        "missing_fields": missing_fields,
        "configured_profiles": sorted(str(name) for name in configured_profiles),
        "user_config_path": str(user_config_path()),
        "recommended_commands": recommended_commands,
    }
    request_path = run_dir / "remote_vitis_profile_request.json"
    _write_json(request_path, request)
    return {
        "status": BLOCKED_PROFILE_STATUS,
        "mode": mode,
        "server": args.server,
        "profile": args.profile,
        "readiness": args.readiness,
        "example_spec": args.example_spec,
        "run_dir": str(run_dir),
        "missing_fields": missing_fields,
        "configured_profiles": sorted(str(name) for name in configured_profiles),
        "remote_vitis_profile_request": str(request_path),
        "user_config_path": str(user_config_path()),
        "uses_erie_remote_ssh": True,
    }
def _vitis_version_candidates(config: dict[str, Any], settings_path: Path, server: str) -> list[dict[str, Any]]:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    server_list_path = _resolve_erie_server_list(settings, settings_path, Path(config["erie_skill_dir"]))
    try:
        server_list = json.loads(server_list_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    raw_server = _find_server_record(server_list, server)
    if not raw_server:
        return []
    inferred_target_part = _infer_target_part_from_server_record(raw_server)
    scan = raw_server.get("software_scan", {})
    tools = scan.get("tools", {}) if isinstance(scan, dict) else {}
    vitis = tools.get("vitis", {}) if isinstance(tools, dict) else {}
    versions = vitis.get("versions") if isinstance(vitis, dict) else None
    raw_versions = versions if isinstance(versions, list) else ([vitis] if vitis.get("status") == "installed" else [])
    candidates: list[dict[str, Any]] = []
    for item in raw_versions:
        if not isinstance(item, dict) or item.get("status") != "installed":
            continue
        install_path = str(item.get("install_path") or "").strip()
        executable_path = str(item.get("path") or "").strip()
        version = _version_label(item)
        settings_script = (install_path.rstrip("/") + "/settings64.sh") if install_path else ""
        expected_tool_path = _infer_vitis_hls_executable(install_path, version)
        env_setup_script = _infer_vitis_hls_env_setup(install_path, version)
        candidates.append(
            {
                "version": version,
                "settings_script": settings_script,
                "expected_tool": "vitis_hls",
                "expected_tool_path": expected_tool_path,
                "env_setup_script": env_setup_script,
                "vpp_path": install_path.rstrip("/") + "/bin/v++" if install_path else "",
                "xrt_tool_path": "/opt/xilinx/xrt/bin/xrt-smi",
                "xrt_setup_script": "/opt/xilinx/xrt/setup.sh",
                "xbmgmt_tool_path": "/opt/xilinx/xrt/bin/xbmgmt",
                "target_part": str(item.get("target_part") or inferred_target_part or ""),
                "install_path": install_path,
                "executable_path": executable_path,
            }
        )
    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        unique.setdefault(str(candidate["version"]), candidate)
    return list(unique.values())
def _find_server_record(server_list: dict[str, Any], server: str) -> dict[str, Any] | None:
    for item in server_list.get("servers", []):
        if not isinstance(item, dict):
            continue
        selectors = {str(item.get("id") or ""), str(item.get("name") or ""), str(item.get("legacy_id") or "")}
        if server in selectors:
            return item
    return None
def _infer_target_part_from_server(settings_path: Path, server: str) -> str:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    server_list_path = _resolve_erie_server_list(settings, settings_path, Path(remote_validation_config()["erie_skill_dir"]))
    try:
        server_list = json.loads(server_list_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    record = _find_server_record(server_list, server)
    if not record:
        return ""
    return _infer_target_part_from_server_record(record)
def _infer_target_part_from_server_record(record: dict[str, Any]) -> str:
    models: list[str] = []
    for source_key in ("inventory_snapshot", "software_scan"):
        source = record.get(source_key)
        if not isinstance(source, dict):
            continue
        for item in source.get("fpga_devices", []) or []:
            if isinstance(item, dict) and item.get("model"):
                models.append(str(item["model"]))
    normalized = (" ".join(models) + " " + str(record.get("name") or "")).lower()
    if "u55c" in normalized:
        return "xcu55c-fsvh2892-2L-e"
    if "u50" in normalized:
        return "".join(("xcu", "50", "-fsvh2104-2-e"))
    return ""
def _version_label(item: dict[str, Any]) -> str:
    for value in (item.get("install_path"), item.get("version"), item.get("path")):
        text = str(value or "")
        match = re.search(r"(20\d{2}\.\d+)", text)
        if match:
            return match.group(1)
    return str(item.get("version") or item.get("install_path") or item.get("path") or "unknown")
def _find_candidate(candidates: list[dict[str, Any]], version: str) -> dict[str, Any] | None:
    return next((item for item in candidates if str(item.get("version")) == version), None)
def _infer_vitis_hls_executable(install_path: str, version: str) -> str:
    path_text = str(install_path or "").strip()
    if "/Vitis/" in path_text:
        return path_text.replace("/Vitis/", "/Vitis_HLS/").rstrip("/") + "/bin/vitis_hls"
    version_text = str(version or "").strip()
    if version_text:
        return f"/tools/Xilinx/Vitis_HLS/{version_text}/bin/vitis_hls"
    return ""
def _infer_vitis_hls_env_setup(install_path: str, version: str) -> str:
    path_text = str(install_path or "").strip()
    if "/Vitis/" in path_text:
        return path_text.replace("/Vitis/", "/Vitis_HLS/").rstrip("/") + "/bin/setupEnv.sh"
    version_text = str(version or "").strip()
    if version_text:
        return f"/tools/Xilinx/Vitis_HLS/{version_text}/bin/setupEnv.sh"
    return ""
def _remote_vitis_version_request(args: argparse.Namespace, run_dir: Path, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    commands = [
        f"python .\\scripts\\python\\remote\\remote_vitis_acceptance.py --mode vitis --server {args.server} --profile {args.profile} --vitis-version {item['version']} --readiness {args.readiness} --example-spec {args.example_spec} --json"
        for item in candidates
    ]
    return {
        "version": 1,
        "action": "ask_remote_vitis_version",
        "primary_source": "multiple_remote_vitis_versions",
        "question": "Multiple Vitis versions were detected on the selected remote server. Choose one before HLS validation or development continues.",
        "server": args.server,
        "profile": args.profile,
        "readiness": args.readiness,
        "example_spec": args.example_spec,
        "candidate_versions": candidates,
        "user_config_path": str(user_config_path()),
        "recommended_commands": commands,
        "output": str(run_dir / "remote_vitis_version_request.json"),
    }
def _generate_local_hls_artifacts(run_dir: Path, *, comment_language: str, example_spec: str = "hls_vector_scale_mock_spec.json") -> Path:
    spec_path = skill_config_path("examples_dir") / example_spec
    if not spec_path.exists() or spec_path.name != example_spec:
        raise RemoteAcceptanceError(f"Unknown HLS acceptance example spec: {example_spec}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    with use_workspace_root(repo_root()):
        result = run_hls_workflow(spec, out_dir=run_dir / "local-generation", provider_name="mock", readiness="static", run_external=False, comment_language=comment_language)
    if result["status"] != PASS_STATUS:
        raise RemoteAcceptanceError(f"Local artifact generation failed: {result['status']}")
    return Path(result["run_dir"]) / "attempt-001" / "hls" / "artifacts"
def _load_example_spec(example_spec: str) -> dict[str, Any]:
    spec_path = skill_config_path("examples_dir") / example_spec
    if not spec_path.exists() or spec_path.name != example_spec:
        raise RemoteAcceptanceError(f"Unknown HLS acceptance example spec: {example_spec}")
    return json.loads(spec_path.read_text(encoding="utf-8"))
def _create_vitis_package(run_dir: Path, artifact_dir: Path) -> Path:
    runner = run_dir / "run_vitis.sh"
    runner.write_text(_remote_runner_script(), encoding="utf-8", newline="\n")
    package_path = run_dir / "hls_artifacts.tar.gz"
    with tarfile.open(package_path, "w:gz") as tar:
        for path in sorted(artifact_dir.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=Path("artifacts") / path.relative_to(artifact_dir))
        tar.add(runner, arcname="run_vitis.sh")
    return package_path
def _transfer_package_by_request_commands(helper: ErieHelper, settings: Path, server: str, remote_dir: str, package_path: Path) -> list[str]:
    encoded = base64.b64encode(package_path.read_bytes()).decode("ascii")
    requests: list[str] = []
    remote_b64 = f"{remote_dir}/hls_artifacts.tar.gz.b64"
    requests.append(helper.request_and_run(settings, server, "command", f": > {shlex.quote(remote_b64)}", "initialize remote package payload"))
    for index in range(0, len(encoded), 7000):
        chunk = encoded[index : index + 7000]
        requests.append(helper.request_and_run(settings, server, "command", f"printf %s {shlex.quote(chunk)} >> {shlex.quote(remote_b64)}", "append remote package payload chunk"))
    return requests
def _remote_vitis_command(remote_dir: str, profile: dict[str, Any], readiness: str) -> str:
    settings_script = shlex.quote(str(profile["settings_script"]))
    env_setup_script = shlex.quote(str(profile.get("env_setup_script") or ""))
    expected_tool = shlex.quote(str(profile.get("tool_path") or profile["expected_tool"]))
    target_part = shlex.quote(str(profile.get("target_part", "")))
    readiness_arg = shlex.quote(readiness)
    remote = shlex.quote(remote_dir)
    return (
        f"cd {remote} && base64 -d hls_artifacts.tar.gz.b64 > hls_artifacts.tar.gz && "
        "tar -xzf hls_artifacts.tar.gz && "
        f"HLS_SETTINGS_SCRIPT={settings_script} HLS_ENV_SETUP_SCRIPT={env_setup_script} "
        f"HLS_EXPECTED_TOOL={expected_tool} HLS_TARGET_PART={target_part} HLS_READINESS={readiness_arg} bash run_vitis.sh"
    )
def _run_server_vitis_phase(
    helper: ErieHelper,
    settings: Path,
    server: str,
    profile: dict[str, Any],
    readiness: str,
    package_path: Path,
    config: dict[str, Any],
    run_dir: Path,
    *,
    phase_label: str,
    cleanup_remote: bool,
    remote_workdir: str,
) -> dict[str, Any]:
    layout = remote_directory_layout_for_workdir(remote_workdir, f"{run_dir.name}-{phase_label}")
    request_paths: list[str] = []
    request_paths.extend(_ensure_remote_project_layout(helper, settings, server, layout))
    request_paths.extend(_transfer_package_by_request_commands(helper, settings, server, layout["active_run_relative"], package_path))
    command = _remote_vitis_command(layout["active_run_dir"], profile, readiness)
    detached = helper.exec_detached(server, f"run Vitis HLS {phase_label}", command, settings=settings)
    job_result = helper.wait_for_job(server, detached["job_id"], settings=settings, max_wait_s=max(helper.timeout, 1800))
    request_paths.append(detached["manifest"])
    if job_result["status"] != "succeeded":
        tail = _safe_tail_log(helper, server, detached["job_id"], settings)
        details = job_result["output"].strip()
        raise RemoteAcceptanceError(
            f"Detached Vitis HLS {phase_label} job failed for server {server}.\n{details}\n{tail}"
        )
    cleanup_performed = False
    archived_after_verification = False
    if config["directory_contract"]["archive_after_verification"]:
        request_paths.append(_archive_remote_run(helper, settings, server, layout))
        archived_after_verification = True
    return {
        "status": PASS_STATUS,
        "server": server,
        "phase": phase_label,
        "vitis_version": profile.get("version"),
        "target_part": profile.get("target_part"),
        "run_id": layout["run_id"],
        "remote_project_root": layout["project_root_relative"],
        "remote_project_root_abs": layout["project_root"],
        "remote_conda_prefix": layout["conda_prefix_relative"],
        "remote_conda_prefix_abs": layout["conda_prefix"],
        "remote_run_dir": layout["active_run_relative"],
        "remote_run_dir_abs": layout["active_run_dir"],
        "remote_backup_dir": layout["backup_run_relative"],
        "remote_backup_dir_abs": layout["backup_run_dir"],
        "remote_dir": layout["backup_run_relative"] if archived_after_verification else layout["active_run_relative"],
        "job_id": detached["job_id"],
        "requests": request_paths,
        "cleanup_performed": cleanup_performed,
        "remote_artifacts_retained": True,
        "archived_after_verification": archived_after_verification,
        "archive_trigger": config["directory_contract"]["archive_trigger"],
        "job_status": job_result["status"],
    }
def _safe_tail_log(helper: ErieHelper, server: str, job_id: str, settings: Path) -> str:
    try:
        return helper.tail_log(server, job_id, settings=settings, lines=80)
    except RemoteAcceptanceError as exc:
        return f"tail_log_unavailable: {exc}"
def _remote_runner_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
: "${HLS_SETTINGS_SCRIPT:?}"
: "${HLS_ENV_SETUP_SCRIPT:=}"
: "${HLS_EXPECTED_TOOL:?}"
: "${HLS_READINESS:?}"
HLS_TARGET_PART="${HLS_TARGET_PART:-}"
source "$HLS_SETTINGS_SCRIPT" >/dev/null 2>&1 || true
if [ -n "$HLS_ENV_SETUP_SCRIPT" ] && [ -f "$HLS_ENV_SETUP_SCRIPT" ]; then
  set +u
  source "$HLS_ENV_SETUP_SCRIPT" >/dev/null 2>&1 || true
  set -u
fi
if [[ "$HLS_EXPECTED_TOOL" == */* ]] && [ -x "$HLS_EXPECTED_TOOL" ]; then
  tool_path="$HLS_EXPECTED_TOOL"
else
  tool_path="$(command -v "$HLS_EXPECTED_TOOL" || true)"
fi
if [ -z "$tool_path" ]; then
  echo "HLS_REMOTE_STATUS blocked_vitis_server"
  exit 44
fi
cd artifacts
python3 - "$PWD/hls_config.cfg" "$PWD/remote_vitis.tcl" "remote_vitis_project" "$HLS_READINESS" "$HLS_TARGET_PART" <<'PY'
from pathlib import Path
import sys

cfg_path = Path(sys.argv[1])
tcl_path = Path(sys.argv[2])
project = Path(sys.argv[3])
readiness = sys.argv[4]
target_part = sys.argv[5]
entries = {"syn.file": [], "tb.file": []}
for raw in cfg_path.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = raw.strip()
    if not line or line.startswith("[") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if key in {"syn.file", "tb.file"}:
        entries.setdefault(key, []).append(value)
    else:
        entries[key] = value
def q(value):
    return "{" + str(value).replace("}", "\\\\}") + "}"

lines = [
    f"open_project -reset {q(project)}",
    f"set_top {q(entries.get('syn.top', 'kernel'))}",
]
for item in entries.get("syn.file", []):
    lines.append(f"add_files {q(Path.cwd() / item)}")
for item in entries.get("tb.file", []):
    lines.append(f"add_files -tb {q(Path.cwd() / item)}")
lines.append("open_solution -reset {solution1}")
if entries.get("part"):
    lines.append(f"set_part {q(entries['part'])}")
elif target_part:
    lines.append(f"set_part {q(target_part)}")
if entries.get("clock"):
    lines.append(f"create_clock -period {entries['clock']}")
order = {"static": 0, "compile": 1, "execute": 2, "implement": 3, "cosim": 4}
level = order.get(readiness, 4)
if level >= 1:
    lines.append("csim_design")
if level >= 3:
    lines.append("csynth_design")
if level >= 4:
    lines.append("cosim_design")
lines.append("exit")
tcl_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
PY
if [ "${tool_path##*/}" = "vitis-run" ]; then
  vitis-run --mode hls --tcl "$PWD/remote_vitis.tcl"
else
  "$tool_path" -f "$PWD/remote_vitis.tcl"
fi
echo "HLS_REMOTE_STATUS passed"
"""
