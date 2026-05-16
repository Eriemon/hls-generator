#!/usr/bin/env python3
"""Remote SSH link and Vitis acceptance checks via erie-remote-ssh."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from integration.hls_adapter import run_hls_workflow  # noqa: E402
from runtime.hls_generator.config import remote_validation_config, skill_config_path, skill_dependencies_config, skill_root, vitis_tool_timeout  # noqa: E402
from runtime.hls_generator.skill_dependencies import SkillDependencyError, require_skill_dependencies  # noqa: E402
from runtime.hls_generator.user_config import get_vitis_selection, set_vitis_selection, user_config_path  # noqa: E402
from runtime.hls_generator.validation import READINESS_LEVELS  # noqa: E402

PASS_STATUS = "passed"
DRY_RUN_STATUS = "dry_run"
BLOCKED_VITIS_STATUS = "blocked_vitis_server"
BLOCKED_VERSION_STATUS = "blocked_remote_version_choice"
BLOCKED_PROFILE_STATUS = "blocked_remote_profile_config"
FAILED_STATUS = "failed"
UTF8_HINT = "Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 when calling erie-remote-ssh."


class RemoteAcceptanceError(RuntimeError):
    """Expected user-facing remote acceptance failure."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate HLS generator remote confidence through erie-remote-ssh.")
    parser.add_argument("--mode", required=True, choices=("link", "vitis"))
    parser.add_argument("--server", help="Single-server target id or name from erie-remote-ssh config.")
    parser.add_argument("--build-server", help="Build-server id or name for split build/validate topology.")
    parser.add_argument("--validate-server", help="Validation-server id or name for split build/validate topology.")
    parser.add_argument("--profile", help="Optional remote_validation.vitis_profiles key for Vitis mode.")
    parser.add_argument("--vitis-version", help="Explicit remote Vitis version to use and remember for this server.")
    parser.add_argument("--target-part", help="Optional explicit target part override for remote HLS synthesis.")
    parser.add_argument("--readiness", default="cosim", choices=READINESS_LEVELS)
    parser.add_argument("--example-spec", default="hls_vector_scale_mock_spec.json", help="Example spec from assets/examples used for Vitis acceptance artifacts.")
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
    return 1


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    require_skill_dependencies(skill_dependencies_config(), scopes={"core"})
    config = remote_validation_config()
    base_timeout = int(args.timeout or config["default_timeout_s"])
    if args.mode == "vitis":
        base_timeout = max(base_timeout, int(vitis_tool_timeout(args.readiness)) + 30)
    timeout = base_timeout
    helper = ErieHelper(config, timeout)
    topology = _resolve_topology(args)
    plan = _planned_steps(
        args.mode,
        topology["server"],
        args.profile,
        args.readiness,
        cleanup_remote=bool(getattr(args, "cleanup_remote", False)),
        example_spec=str(getattr(args, "example_spec", "")),
        validate_server=topology.get("validate_server"),
        topology=topology["topology"],
    )
    if args.dry_run:
        result = {
            "status": DRY_RUN_STATUS,
            "mode": args.mode,
            "server": topology["server"],
            "build_server": topology.get("build_server"),
            "validate_server": topology.get("validate_server"),
            "topology": topology["topology"],
            "steps": plan,
            "uses_erie_remote_ssh": True,
        }
        if args.mode == "vitis":
            result.update({"cleanup_performed": False, "remote_artifacts_retained": True})
        return result
    if args.mode == "link":
        return _run_link_mode(args, config, helper, plan, topology)
    if topology["topology"] == "split_build_validate":
        return _run_split_vitis_mode(args, config, helper, plan, topology)
    return _run_vitis_mode(args, config, helper, plan, topology)


def _run_link_mode(args: argparse.Namespace, config: dict[str, Any], helper: "ErieHelper", plan: list[str], topology: dict[str, Any]) -> dict[str, Any]:
    run_dir = _new_run_dir(config, "link")
    helper.preflight(topology["server"])
    output = helper.exec(topology["server"], list(config["link_probe_command"]))
    _reject_decode_noise(output)
    required = ("HLS_REMOTE_LINK_OK", "host=", "pwd=", "python=")
    missing = [item for item in required if item not in output]
    status = PASS_STATUS if not missing else FAILED_STATUS
    result = {
        "status": status,
        "mode": "link",
        "server": topology["server"],
        "topology": topology["topology"],
        "run_dir": str(run_dir),
        "steps": plan,
        "output": output,
        "missing_markers": missing,
        "uses_erie_remote_ssh": True,
    }
    _write_report(run_dir, result)
    return result


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
        required_fields=("settings_script", "expected_tool", "target_part"),
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

    artifact_dir = _generate_local_hls_artifacts(run_dir, comment_language=args.comment_language, example_spec=args.example_spec)
    package_path = _create_vitis_package(run_dir, artifact_dir)
    remote_dir = f"{config['remote_tmp_dir']}/{run_dir.name}"
    cleanup_performed = False

    request_paths: list[str] = []
    request_paths.append(helper.request_and_run(settings, server, "mkdir", [remote_dir], "prepare remote HLS validation directory"))
    request_paths.extend(_transfer_package_by_request_commands(helper, settings, server, remote_dir, package_path))
    command = _remote_vitis_command(remote_dir, selected_profile, args.readiness)
    request_paths.append(helper.request_and_run(settings, server, "command", command, "run Vitis HLS acceptance"))
    if args.cleanup_remote:
        request_paths.append(helper.request_and_run(settings, server, "delete", [remote_dir, "--recursive"], "cleanup remote HLS validation directory"))
        cleanup_performed = True

    result = {
        "status": PASS_STATUS,
        "mode": "vitis",
        "server": server,
        "topology": topology["topology"],
        "profile": args.profile,
        "vitis_version": selected_profile.get("version"),
        "readiness": args.readiness,
        "example_spec": args.example_spec,
        "run_dir": str(run_dir),
        "artifact_dir": str(artifact_dir),
        "remote_dir": remote_dir,
        "cleanup_performed": cleanup_performed,
        "remote_artifacts_retained": not cleanup_performed,
        "requests": request_paths,
        "uses_erie_remote_ssh": True,
    }
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


class ErieHelper:
    def __init__(self, config: dict[str, Any], timeout: int) -> None:
        self.config = config
        self.timeout = timeout
        self.erie_skill_dir = Path(config["erie_skill_dir"])
        self.settings = Path(config["erie_settings_path"])
        self.script = self.erie_skill_dir / "scripts" / "remote_ssh.py"
        if not self.script.exists():
            raise RemoteAcceptanceError(f"erie-remote-ssh helper was not found: {self.script}")
        if not self.settings.exists():
            raise RemoteAcceptanceError(f"erie-remote-ssh settings were not found: {self.settings}")

    def preflight(self, server: str, *, settings: Path | None = None) -> None:
        active_settings = settings or self.settings
        self._run(["discover", "--settings", str(active_settings), "--json"])
        self._run(["list", "--settings", str(active_settings)])
        self._run(["check", "--settings", str(active_settings), "--server", server])
        self._run(["workspace-check", "--settings", str(active_settings), "--server", server, "--timeout", str(self.timeout)])

    def exec(self, server: str, command: list[str], *, settings: Path | None = None) -> str:
        active_settings = settings or self.settings
        return self._run(["exec", "--settings", str(active_settings), "--server", server, "--timeout", str(self.timeout), "--", *command])

    def scan_software(self, server: str, *, settings: Path | None = None) -> str:
        active_settings = settings or self.settings
        return self._run(["scan-software", "--settings", str(active_settings), "--server", server, "--timeout", str(self.timeout)])

    def request_and_run(self, settings: Path, server: str, operation: str, payload: list[str] | str, reason: str) -> str:
        if operation == "mkdir":
            request_stdout = self._run(["request-mkdir", "--settings", str(settings), "--server", server, "--path", payload[0], "--reason", reason])
        elif operation == "delete":
            args = ["request-delete", "--settings", str(settings), "--server", server, "--path", payload[0], "--reason", reason]
            if "--recursive" in payload:
                args.insert(-2, "--recursive")
            request_stdout = self._run(args)
        elif operation == "command":
            command = payload if isinstance(payload, str) else " ".join(payload)
            request_stdout = self._run(["request-command", "--settings", str(settings), "--server", server, "--reason", reason, "--", command])
        else:
            raise RemoteAcceptanceError(f"Unsupported request operation: {operation}")
        request_path = _parse_request_path(request_stdout)
        self._run(["run-request", "--settings", str(settings), "--request", request_path, "--execute", "--timeout", str(self.timeout)])
        return request_path

    def exec_detached(self, server: str, reason: str, command: str, *, settings: Path | None = None) -> dict[str, Any]:
        active_settings = settings or self.settings
        output = self._run(["exec-detached", "--settings", str(active_settings), "--server", server, "--reason", reason, "--timeout", str(self.timeout), "--", "bash", "-lc", command])
        job_id = _field_from_output(output, "job_id")
        remote_job_dir = _field_from_output(output, "remote_job_dir")
        manifest = _field_from_output(output, "manifest")
        return {"job_id": job_id, "remote_job_dir": remote_job_dir, "manifest": manifest, "output": output}

    def wait_for_job(self, server: str, job_id: str, *, settings: Path | None = None, poll_s: int = 10, max_wait_s: int | None = None) -> dict[str, Any]:
        active_settings = settings or self.settings
        deadline = time.time() + float(max_wait_s or self.timeout)
        last_output = ""
        while time.time() < deadline:
            last_output, returncode = self._run_with_returncode(
                ["status", "--settings", str(active_settings), "--server", server, "--job", job_id, "--timeout", str(self.timeout)]
            )
            status = _field_from_output(last_output, "status")
            if status in {"succeeded", "failed", "not_found"}:
                return {"status": status, "output": last_output, "returncode": returncode}
            if returncode != 0:
                raise RemoteAcceptanceError(f"erie-remote-ssh status command failed: {last_output.strip()}")
            time.sleep(poll_s)
        tail = self.tail_log(server, job_id, settings=active_settings, lines=40)
        raise RemoteAcceptanceError(f"Detached remote job {job_id} did not finish within {max_wait_s or self.timeout}s.\n{tail}")

    def tail_log(self, server: str, job_id: str, *, settings: Path | None = None, lines: int = 40) -> str:
        active_settings = settings or self.settings
        return self._run(["tail-log", "--settings", str(active_settings), "--server", server, "--job", job_id, "--lines", str(lines), "--timeout", str(self.timeout)])

    def _run(self, args: list[str]) -> str:
        combined, returncode = self._run_with_returncode(args)
        if returncode != 0:
            raise RemoteAcceptanceError(f"erie-remote-ssh command failed ({args[0]}): {combined.strip()}")
        return combined

    def _run_with_returncode(self, args: list[str]) -> tuple[str, int]:
        env = os.environ.copy()
        env.update(self.config["python_env"])
        command = [sys.executable, str(self.script), *args]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=max(self.timeout + 10, 30), check=False)
        combined = (result.stdout or "") + (result.stderr or "")
        _reject_decode_noise(combined)
        return combined, result.returncode


def _resolve_topology(args: argparse.Namespace) -> dict[str, Any]:
    single = bool(getattr(args, "server", None))
    split_build = bool(getattr(args, "build_server", None))
    split_validate = bool(getattr(args, "validate_server", None))
    if single and (split_build or split_validate):
        raise ValueError("Use either --server or the pair --build-server/--validate-server, not both.")
    if split_build != split_validate:
        raise ValueError("Split topology requires both --build-server and --validate-server.")
    if split_build and split_validate:
        return {
            "topology": "split_build_validate",
            "server": str(args.build_server),
            "build_server": str(args.build_server),
            "validate_server": str(args.validate_server),
        }
    if single:
        return {"topology": "single_server", "server": str(args.server)}
    raise ValueError("Provide either --server or both --build-server and --validate-server.")


def _probe_vitis(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any]) -> dict[str, Any]:
    expected_tool = str(profile["expected_tool"])
    settings_script = str(profile["settings_script"])
    command = [
        "bash",
        "-lc",
        f"if [ -f {shlex.quote(settings_script)} ]; then source {shlex.quote(settings_script)} >/dev/null 2>&1; fi; printf 'expected_tool='; command -v {shlex.quote(expected_tool)} || true",
    ]
    output = helper.exec(server, command, settings=settings)
    _reject_decode_noise(output)
    tool_path = ""
    for line in output.splitlines():
        if line.startswith("expected_tool="):
            tool_path = line.split("=", 1)[1].strip()
            break
    return {"status": PASS_STATUS if tool_path else BLOCKED_VITIS_STATUS, "expected_tool": expected_tool, "tool_path": tool_path, "output": output}


def _probe_fpga_presence(server: str, settings: Path, helper: ErieHelper) -> dict[str, Any]:
    command = [
        "bash",
        "-lc",
        "if lspci | grep -iq 'xilinx'; then printf 'fpga_present=yes\\n'; lspci | grep -i 'xilinx' | head -n 12; else printf 'fpga_present=no\\n'; fi",
    ]
    output = helper.exec(server, command, settings=settings)
    _reject_decode_noise(output)
    return {"status": PASS_STATUS if "fpga_present=yes" in output else BLOCKED_VITIS_STATUS, "output": output}


def _probe_remote_workdir(server: str, settings: Path, helper: ErieHelper) -> str:
    output = helper.exec(server, ["bash", "-lc", "pwd"], settings=settings)
    _reject_decode_noise(output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise RemoteAcceptanceError(f"Could not determine remote workdir for server {server}.")
    return lines[-1]


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
        if not candidates or _find_candidate(candidates, str(saved.get("version") or "")):
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

    return _blocked_profile_config(args, run_dir, missing_fields=list(required_fields), configured_profiles=configured_profiles)


def _blocked_profile_config(
    args: argparse.Namespace,
    run_dir: Path,
    *,
    missing_fields: list[str],
    configured_profiles: dict[str, Any],
) -> dict[str, Any]:
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
        "recommended_commands": [
            f"python .\\scripts\\remote_vitis_acceptance.py --mode vitis --server {args.server} --profile <configured-profile> --readiness {args.readiness} --example-spec {args.example_spec} --json",
            f"python .\\scripts\\remote_vitis_acceptance.py --mode vitis --server {args.server} --vitis-version <version> --readiness {args.readiness} --example-spec {args.example_spec} --json",
        ],
    }
    request_path = run_dir / "remote_vitis_profile_request.json"
    _write_json(request_path, request)
    return {
        "status": BLOCKED_PROFILE_STATUS,
        "mode": "vitis",
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
        candidates.append(
            {
                "version": version,
                "settings_script": settings_script,
                "expected_tool": "vitis_hls",
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
    normalized = " ".join(models).lower()
    if "u55c" in normalized:
        return "xcu55c-fsvh2892-2L-e"
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


def _remote_vitis_version_request(args: argparse.Namespace, run_dir: Path, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    commands = [
        f"python .\\scripts\\remote_vitis_acceptance.py --mode vitis --server {args.server} --profile {args.profile} --vitis-version {item['version']} --readiness {args.readiness} --example-spec {args.example_spec} --json"
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
    result = run_hls_workflow(spec, out_dir=run_dir / "local-generation", provider_name="mock", readiness="static", run_external=False, comment_language=comment_language)
    if result["status"] != PASS_STATUS:
        raise RemoteAcceptanceError(f"Local artifact generation failed: {result['status']}")
    return Path(result["run_dir"]) / "attempt-001" / "hls" / "artifacts"


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
    expected_tool = shlex.quote(str(profile["expected_tool"]))
    target_part = shlex.quote(str(profile.get("target_part", "")))
    readiness_arg = shlex.quote(readiness)
    remote = shlex.quote(remote_dir)
    return (
        f"cd {remote} && base64 -d hls_artifacts.tar.gz.b64 > hls_artifacts.tar.gz && "
        "tar -xzf hls_artifacts.tar.gz && "
        f"HLS_SETTINGS_SCRIPT={settings_script} HLS_EXPECTED_TOOL={expected_tool} HLS_TARGET_PART={target_part} HLS_READINESS={readiness_arg} bash run_vitis.sh"
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
    remote_dir = f"{config['remote_tmp_dir']}/{run_dir.name}-{phase_label}"
    remote_exec_dir = str(PurePosixPath(remote_workdir) / PurePosixPath(remote_dir))
    request_paths: list[str] = []
    request_paths.append(helper.request_and_run(settings, server, "mkdir", [remote_dir], f"prepare remote HLS {phase_label} directory"))
    request_paths.extend(_transfer_package_by_request_commands(helper, settings, server, remote_dir, package_path))
    command = _remote_vitis_command(remote_exec_dir, profile, readiness)
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
    if cleanup_remote:
        request_paths.append(helper.request_and_run(settings, server, "delete", [remote_dir, "--recursive"], f"cleanup remote HLS {phase_label} directory"))
        cleanup_performed = True
    return {
        "status": PASS_STATUS,
        "server": server,
        "phase": phase_label,
        "vitis_version": profile.get("version"),
        "target_part": profile.get("target_part"),
        "remote_dir": remote_dir,
        "job_id": detached["job_id"],
        "requests": request_paths,
        "cleanup_performed": cleanup_performed,
        "remote_artifacts_retained": not cleanup_performed,
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
: "${HLS_EXPECTED_TOOL:?}"
: "${HLS_READINESS:?}"
HLS_TARGET_PART="${HLS_TARGET_PART:-}"
source "$HLS_SETTINGS_SCRIPT" >/dev/null 2>&1 || true
tool_path="$(command -v "$HLS_EXPECTED_TOOL" || true)"
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
if [ "$HLS_EXPECTED_TOOL" = "vitis-run" ]; then
  vitis-run --mode hls --tcl "$PWD/remote_vitis.tcl"
else
  "$HLS_EXPECTED_TOOL" -f "$PWD/remote_vitis.tcl"
fi
echo "HLS_REMOTE_STATUS passed"
"""


def _write_erie_settings_overlay(config: dict[str, Any], run_dir: Path) -> Path:
    base_settings_path = Path(config["erie_settings_path"])
    settings = json.loads(base_settings_path.read_text(encoding="utf-8"))
    settings.setdefault("paths", {})
    settings["paths"]["default_server_list"] = str(_resolve_erie_server_list(settings, base_settings_path, Path(config["erie_skill_dir"])))
    settings["paths"]["requests_dir"] = str(run_dir / "requests")
    settings["paths"]["downloads_dir"] = str(run_dir / "downloads")
    settings["paths"]["validation_tmp_dir"] = str(run_dir / "tmp")
    path = run_dir / "erie_settings.overlay.json"
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _resolve_erie_server_list(settings: dict[str, Any], settings_path: Path, erie_skill_dir: Path) -> Path:
    raw = str(settings.get("paths", {}).get("default_server_list") or "").strip()
    if not raw:
        raise RemoteAcceptanceError("erie-remote-ssh settings are missing paths.default_server_list. Ask the user to configure the remote server list before continuing.")
    replacements = {
        "skill_dir": str(erie_skill_dir),
        "settings_dir": str(settings_path.parent),
        "home": str(Path.home()),
    }
    for key, value in replacements.items():
        raw = raw.replace("${" + key + "}", value)
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def _new_run_dir(config: dict[str, Any], prefix: str) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = skill_root() / str(config["local_run_root"]) / f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_report(run_dir: Path, result: dict[str, Any]) -> None:
    _write_json(run_dir / "result.json", result)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _planned_steps(
    mode: str,
    server: str,
    profile: str,
    readiness: str,
    *,
    cleanup_remote: bool = False,
    example_spec: str = "",
    validate_server: str | None = None,
    topology: str = "single_server",
) -> list[str]:
    steps = ["erie discover", "erie list", f"erie check {server}", f"erie workspace-check {server}"]
    if topology == "split_build_validate" and validate_server:
        steps.extend([f"erie check {validate_server}", f"erie workspace-check {validate_server}"])
    if mode == "link":
        steps.append("erie exec read-only UTF-8 link probe")
    else:
        profile_label = profile or "<user-configured-profile>"
        steps.extend([f"erie exec Vitis profile probe {profile_label}", f"generate local HLS mock artifacts from {example_spec or 'default example'}", "erie request mkdir", "erie request command payload transfer", f"erie request command Vitis {readiness}"])
        if topology == "split_build_validate" and validate_server:
            steps.extend(["erie exec validation server device probe", "erie request mkdir validation", "erie request command payload transfer validation", f"erie request command validation Vitis {readiness}"])
        if cleanup_remote:
            steps.append("erie request delete cleanup")
        else:
            steps.append("retain remote validation directory")
    return steps


def _parse_request_path(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("request:"):
            return line.split(":", 1)[1].strip()
    raise RemoteAcceptanceError(f"Could not find request path in erie output: {stdout}")


def _field_from_output(output: str, key: str) -> str:
    prefix = f"{key}: "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.split(prefix, 1)[1].strip()
    return ""


def _reject_decode_noise(output: str) -> None:
    if "UnicodeDecodeError" in output or "_readerthread" in output:
        raise RemoteAcceptanceError(f"erie-remote-ssh output decoding failed. {UTF8_HINT}")


def _format_result(result: dict[str, Any]) -> str:
    lines = [f"status: {result.get('status')}"]
    for key in ("mode", "topology", "server", "build_server", "validate_server", "profile", "vitis_version", "readiness", "example_spec", "run_dir", "remote_dir", "remote_vitis_version_request", "remote_vitis_profile_request"):
        if result.get(key) is not None:
            lines.append(f"{key}: {result[key]}")
    if result.get("error"):
        lines.append(f"error: {result['error']}")
    if result.get("missing_fields"):
        lines.append("missing_fields: " + ", ".join(str(item) for item in result["missing_fields"]))
    if result.get("probe"):
        lines.append(f"probe: {result['probe'].get('status')}")
    if result.get("remote_artifacts_retained") is not None:
        lines.append(f"remote_artifacts_retained: {result['remote_artifacts_retained']}")
    if result.get("cleanup_performed") is not None:
        lines.append(f"cleanup_performed: {result['cleanup_performed']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
