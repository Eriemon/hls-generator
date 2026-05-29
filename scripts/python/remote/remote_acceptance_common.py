#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

MODULE_DIR = Path(__file__).resolve().parent
SKILL_ROOT = Path(__file__).resolve().parents[3]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from integration.hls_adapter import run_hls_workflow  # noqa: E402
from runtime.hls_generator.board_acceptance import BOARD_RUNNABLE_PROFILE, board_acceptance_config, resolve_host_template_path  # noqa: E402
from runtime.hls_generator.board_platform_payload import U55C_PLATFORM_NAME, default_local_u55c_payload_root, prepare_local_u55c_platform_archive, validate_local_board_platform_payload  # noqa: E402
from runtime.hls_generator.config import remote_validation_config, repo_root, skill_config_path, skill_dependencies_config, skill_root, vitis_tool_timeout  # noqa: E402
from runtime.hls_generator.remote_directory_contract import remote_directory_layout_for_workdir  # noqa: E402
from runtime.hls_generator.remote_recovery import field_from_equals_output, infer_target_part_from_platform_selection, recover_example_spec, recover_local_run_dir, resolve_recovery_target  # noqa: E402
from runtime.hls_generator.skill_dependencies import SkillDependencyError, require_skill_dependencies  # noqa: E402
from runtime.hls_generator.user_config import get_board_platform_selection, get_vitis_selection, set_board_platform_selection, set_vitis_selection, user_config_path  # noqa: E402
from runtime.hls_generator.validation import READINESS_LEVELS  # noqa: E402

PASS_STATUS = "passed"
DRY_RUN_STATUS = "dry_run"
BLOCKED_VITIS_STATUS = "blocked_vitis_server"
BLOCKED_VERSION_STATUS = "blocked_remote_version_choice"
BLOCKED_PROFILE_STATUS = "blocked_remote_profile_config"
BLOCKED_BOARD_STATUS = "blocked_board_validation"
FAILED_STATUS = "failed"
UTF8_HINT = "Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 when calling erie-remote-ssh."
BOARD_STATUS_MARKER = "HLS_BOARD_STATUS"

class RemoteAcceptanceError(RuntimeError):
    """Expected user-facing remote acceptance failure."""
class ErieHelper:
    def __init__(self, config: dict[str, Any], timeout: int) -> None:
        self.config = config
        self.timeout = timeout
        self.erie_skill_dir = Path(config["erie_skill_dir"])
        self.settings = Path(config["erie_settings_path"])
        self.server_list_config = Path(config["erie_server_list_config"]).resolve() if config.get("erie_server_list_config") else None
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
        self._run_request_execute(
            settings,
            request_path,
            retries=1 if self._is_idempotent_request(operation, reason) else 0,
        )
        return request_path

    def request_upload_and_run(self, settings: Path, server: str, local_path: Path, remote_path: str, reason: str) -> str:
        request_stdout = self._run(
            [
                "request-upload",
                "--settings",
                str(settings),
                "--server",
                server,
                "--local",
                str(local_path),
                "--remote",
                remote_path,
                "--reason",
                reason,
            ]
        )
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
        status_timeout = self._status_timeout()
        while time.time() < deadline:
            last_output, returncode = self._run_with_returncode(
                ["status", "--settings", str(active_settings), "--server", server, "--job", job_id, "--timeout", str(status_timeout)]
            )
            status = _field_from_output(last_output, "status")
            if status in {"succeeded", "failed", "not_found"}:
                return {"status": status, "output": last_output, "returncode": returncode}
            if returncode != 0:
                if _is_transient_status_failure(last_output):
                    time.sleep(poll_s)
                    continue
                raise RemoteAcceptanceError(f"erie-remote-ssh status command failed: {last_output.strip()}")
            time.sleep(poll_s)
        tail = self.tail_log(server, job_id, settings=active_settings, lines=40)
        raise RemoteAcceptanceError(f"Detached remote job {job_id} did not finish within {max_wait_s or self.timeout}s.\n{tail}")

    def tail_log(self, server: str, job_id: str, *, settings: Path | None = None, lines: int = 40) -> str:
        active_settings = settings or self.settings
        return self._run(["tail-log", "--settings", str(active_settings), "--server", server, "--job", job_id, "--lines", str(lines), "--timeout", str(self._status_timeout())])

    def _status_timeout(self) -> int:
        return min(max(int(self.timeout), 30), 180)

    def _request_timeout(self) -> int:
        return min(max(int(self.timeout), 30), 180)

    @staticmethod
    def _is_idempotent_request(operation: str, reason: str) -> bool:
        normalized_reason = reason.lower()
        if operation in {"mkdir", "delete"}:
            return True
        return operation == "command" and any(
            marker in normalized_reason
            for marker in (
                "initialize remote package payload",
                "prepare remote",
            )
        )

    def _run_request_execute(self, settings: Path, request_path: str, *, retries: int = 0) -> str:
        timeout_s = self._request_timeout()
        args = ["run-request", "--settings", str(settings), "--request", request_path, "--execute", "--timeout", str(timeout_s)]
        attempts = max(retries, 0) + 1
        last_output = ""
        for attempt in range(attempts):
            combined, returncode = self._run_with_returncode(args, timeout_s=timeout_s)
            if returncode == 0:
                return combined
            last_output = combined
            if "timed out" in combined.lower() and attempt + 1 < attempts:
                continue
            break
        raise RemoteAcceptanceError(f"erie-remote-ssh command failed (run-request): {last_output.strip()}")

    def _run(self, args: list[str]) -> str:
        combined, returncode = self._run_with_returncode(args)
        if returncode != 0:
            raise RemoteAcceptanceError(f"erie-remote-ssh command failed ({args[0]}): {combined.strip()}")
        return combined

    def _run_with_returncode(self, args: list[str], *, timeout_s: int | None = None) -> tuple[str, int]:
        env = os.environ.copy()
        env.update(self.config["python_env"])
        command = [sys.executable, str(self.script), *self._with_config(args)]
        process_timeout = max(int(timeout_s if timeout_s is not None else self.timeout) + 10, 30)
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=process_timeout, check=False)
        combined = (result.stdout or "") + (result.stderr or "")
        _reject_decode_noise(combined)
        return combined, result.returncode

    def _with_config(self, args: list[str]) -> list[str]:
        if not self.server_list_config or not args:
            return list(args)
        return [args[0], "--config", str(self.server_list_config), *args[1:]]
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
def _expand_settings_path(value: str, *, skill_dir: Path, settings_dir: Path) -> Path:
    expanded = (
        str(value)
        .replace("${skill_dir}", str(skill_dir))
        .replace("${settings_dir}", str(settings_dir))
        .replace("${home}", str(Path.home()))
        .replace("${cwd}", str(Path.cwd()))
        .replace("${project_root}", str(SKILL_ROOT.parents[1]))
    )
    path = Path(os.path.expandvars(os.path.expanduser(expanded)))
    if not path.is_absolute():
        path = settings_dir / path
    return path.resolve()
def _prepare_erie_server_list_copy(config: dict[str, Any]) -> Path:
    settings_path = Path(config["erie_settings_path"]).resolve()
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    configured = str(((settings.get("paths") or {}).get("default_server_list") or "")).strip()
    if not configured:
        raise RemoteAcceptanceError("erie-remote-ssh settings do not define paths.default_server_list.")
    source_path = _expand_settings_path(configured, skill_dir=Path(config["erie_skill_dir"]).resolve(), settings_dir=settings_path.parent)
    if not source_path.exists():
        raise RemoteAcceptanceError(f"erie-remote-ssh server list does not exist: {source_path}")
    copies_dir = repo_root() / "reports" / "remote-validation" / "_config_copies"
    copies_dir.mkdir(parents=True, exist_ok=True)
    target_path = copies_dir / f"{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}-server_list.local.json"
    shutil.copy2(source_path, target_path)
    return target_path
def _probe_vitis(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any]) -> dict[str, Any]:
    expected_tool = str(profile["expected_tool"])
    expected_tool_path = str(profile.get("expected_tool_path") or "").strip()
    settings_script = str(profile["settings_script"])
    env_setup_script = str(profile.get("env_setup_script") or "").strip()
    command_text = (
        f"if [ -f {shlex.quote(settings_script)} ]; then source {shlex.quote(settings_script)} >/dev/null 2>&1; fi; "
        f"printf 'expected_tool='; command -v {shlex.quote(expected_tool)} || true; "
    )
    if env_setup_script:
        command_text = (
            f"if [ -f {shlex.quote(settings_script)} ]; then source {shlex.quote(settings_script)} >/dev/null 2>&1; fi; "
            f"if [ -f {shlex.quote(env_setup_script)} ]; then source {shlex.quote(env_setup_script)} >/dev/null 2>&1; fi; "
            f"printf 'expected_tool='; command -v {shlex.quote(expected_tool)} || true; "
        )
    if expected_tool_path:
        command_text += (
            f"printf '\\nexpected_tool_path='; if [ -x {shlex.quote(expected_tool_path)} ]; then printf %s {shlex.quote(expected_tool_path)}; fi; "
        )
    command_text += "printf '\\nfallback_vitis_run='; command -v vitis-run || true; "
    command_text += "printf '\\nfallback_vitis_hls='; command -v vitis_hls || true"
    command = [
        "bash",
        "-lc",
        command_text,
    ]
    output = helper.exec(server, command, settings=settings)
    _reject_decode_noise(output)
    tool_path = ""
    direct_tool_path = ""
    fallback_vitis_run = ""
    fallback_vitis_hls = ""
    for line in output.splitlines():
        if line.startswith("expected_tool="):
            tool_path = line.split("=", 1)[1].strip()
        elif line.startswith("expected_tool_path="):
            direct_tool_path = line.split("=", 1)[1].strip()
        elif line.startswith("fallback_vitis_run="):
            fallback_vitis_run = line.split("=", 1)[1].strip()
        elif line.startswith("fallback_vitis_hls="):
            fallback_vitis_hls = line.split("=", 1)[1].strip()
    resolved_tool = expected_tool
    if not tool_path and direct_tool_path:
        tool_path = direct_tool_path
    if not tool_path and fallback_vitis_run:
        tool_path = fallback_vitis_run
        resolved_tool = "vitis-run"
    elif not tool_path and fallback_vitis_hls:
        tool_path = fallback_vitis_hls
        resolved_tool = "vitis_hls"
    return {
        "status": PASS_STATUS if tool_path else BLOCKED_VITIS_STATUS,
        "expected_tool": expected_tool,
        "resolved_tool": resolved_tool,
        "tool_path": tool_path,
        "output": output,
    }
def _probe_fpga_presence(server: str, settings: Path, helper: ErieHelper) -> dict[str, Any]:
    command = [
        "bash",
        "-lc",
        "if lspci | grep -iq 'xilinx'; then printf 'fpga_present=yes\\n'; lspci | grep -i 'xilinx' | head -n 12; else printf 'fpga_present=no\\n'; fi",
    ]
    output = helper.exec(server, command, settings=settings)
    _reject_decode_noise(output)
    return {"status": PASS_STATUS if "fpga_present=yes" in output else BLOCKED_VITIS_STATUS, "output": output}
def _probe_target_part_hint(server: str, settings: Path, helper: ErieHelper) -> str:
    command = [
        "bash",
        "-lc",
        "if [ -d /opt/xilinx/firmware/u55c ] || [ -d /tools/Xilinx/firmware/u55c ]; then printf 'target_part=xcu55c-fsvh2892-2L-e'; "
        "elif [ -d /opt/xilinx/firmware/u50 ] || [ -d /tools/Xilinx/firmware/u50 ]; then printf 'target_part=xcu50-fsvh2104-2-e'; "
        "fi",
    ]
    output = helper.exec(server, command, settings=settings)
    _reject_decode_noise(output)
    for line in output.splitlines():
        if line.startswith("target_part="):
            return line.split("=", 1)[1].strip()
    return ""
def _probe_hardware_fingerprint(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    source_settings = ""
    xbmgmt_tool_path = ""
    if profile:
        settings_script = str(profile.get("settings_script") or "").strip()
        xrt_setup_script = str(profile.get("xrt_setup_script") or "").strip()
        xbmgmt_tool_path = str(profile.get("xbmgmt_tool_path") or "").strip()
        if settings_script:
            source_settings += f"source {shlex.quote(settings_script)} >/dev/null 2>&1 || true; "
        if xrt_setup_script:
            source_settings += f"source {shlex.quote(xrt_setup_script)} >/dev/null 2>&1 || true; "
    xbmgmt_probe = (
        f"if [ -x {shlex.quote(xbmgmt_tool_path)} ]; then {shlex.quote(xbmgmt_tool_path)} examine 2>/dev/null; "
        "else xbmgmt examine 2>/dev/null; fi"
        if xbmgmt_tool_path
        else "xbmgmt examine 2>/dev/null || true"
    )
    command = [
        "bash",
        "-lc",
        f"{source_settings}"
        "printf 'cpu_model='; (lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -n 1); "
        "printf '\\nlspci='; (lspci | grep -Ei 'xilinx|alveo' | head -n 20 || true); "
        "printf '\\nfirmware_scan='; (find /opt/xilinx/firmware -maxdepth 2 -type d 2>/dev/null | head -n 40 || true); "
        "printf '\\nboard_scan='; ((xrt-smi examine 2>/dev/null || xbutil examine 2>/dev/null || true) | head -n 120); "
        f"printf '\\nmgmt_scan='; (({xbmgmt_probe}) | head -n 120)",
    ]
    output = helper.exec(server, command, settings=settings)
    _reject_decode_noise(output)
    lspci_text = _section_value(output, "lspci")
    firmware_text = _section_value(output, "firmware_scan")
    board_text = _section_value(output, "board_scan")
    mgmt_text = _section_value(output, "mgmt_scan")
    normalized = " ".join((lspci_text, board_text, mgmt_text)).lower()
    firmware_hint = any(token in firmware_text.lower() for token in ("u55c", "xcu55c", "xilinx_u55c"))
    status = PASS_STATUS if any(token in normalized for token in ("u55c", "xcu55c", "xilinx_u55c")) else BLOCKED_BOARD_STATUS
    evidence_path = ""
    if status != PASS_STATUS:
        evidence_path = "hardware fingerprint does not yet prove an active U55C device"
    return {"status": status, "output": output, "evidence": evidence_path, "firmware_hint": firmware_hint}
def _probe_board_toolchain(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any]) -> dict[str, Any]:
    settings_script = str(profile["settings_script"])
    xrt_setup_script = str(profile.get("xrt_setup_script") or "").strip()
    vpp_path = str(profile.get("vpp_path") or "").strip()
    xrt_tool_path = str(profile.get("xrt_tool_path") or "").strip()
    source_xrt = f"source {shlex.quote(xrt_setup_script)} >/dev/null 2>&1 || true; " if xrt_setup_script else ""
    command = [
        "bash",
        "-lc",
        f"source {shlex.quote(settings_script)} >/dev/null 2>&1 || true; "
        f"{source_xrt}"
        "printf 'vpp='; command -v v++ || true; "
        f"printf '\\nvpp_path='; if [ -x {shlex.quote(vpp_path)} ]; then printf %s {shlex.quote(vpp_path)}; fi; "
        "printf '\\ngpp='; command -v g++ || true; "
        "printf '\\nxrt='; command -v xrt-smi || command -v xbutil || true; "
        f"printf '\\nxrt_path='; if [ -x {shlex.quote(xrt_tool_path)} ]; then printf %s {shlex.quote(xrt_tool_path)}; fi",
    ]
    output = helper.exec(server, command, settings=settings)
    _reject_decode_noise(output)
    has_vpp = "vpp=/" in output or "vpp_path=/" in output
    has_xrt = "xrt=/" in output or "xrt_path=/" in output
    status = PASS_STATUS if has_vpp and "gpp=/" in output and has_xrt else BLOCKED_BOARD_STATUS
    return {"status": status, "output": output}
def _probe_platform_name(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if profile and str(profile.get("platform_name") or "").strip():
        platform_name = str(profile["platform_name"]).strip()
        upload_probe = _probe_uploaded_platform(server, settings, helper, profile)
        if upload_probe.get("status") == PASS_STATUS:
            return {
                "status": PASS_STATUS,
                "selected_platform": platform_name,
                "selected_xpfm": str(upload_probe.get("selected_xpfm") or ""),
                "candidates": [platform_name],
                "all_platforms": [platform_name],
                "output": str(upload_probe.get("output") or "platform_name=provided"),
            }
        if str(profile.get("remote_xpfm") or "").strip() or str(profile.get("remote_platform_root") or "").strip():
            shell_probe = _probe_shell_name(server, settings, helper, profile)
            return {
                "status": BLOCKED_BOARD_STATUS,
                "selected_platform": "",
                "selected_xpfm": "",
                "candidates": [],
                "all_platforms": [],
                "reason": str(upload_probe.get("reason") or "missing_uploaded_platform_payload"),
                "shell_name": str(shell_probe.get("shell_name") or ""),
                "suggested_platform_name": str(shell_probe.get("suggested_platform_name") or ""),
                "output": str(upload_probe.get("output") or "platform_name=provided"),
            }
    target_part = str(profile.get("target_part") or "").strip().lower() if profile else ""
    expected_family = "u55c" if "u55c" in target_part else "u50" if "u50" in target_part else ""
    command = [
        "bash",
        "-lc",
        "find /tools/Xilinx/Vitis /opt/xilinx -type f -name '*.xpfm' 2>/dev/null | head -n 200",
    ]
    output = helper.exec(server, command, settings=settings)
    _reject_decode_noise(output)
    paths = [line.strip() for line in output.splitlines() if line.strip()]
    platform_names = sorted({PurePosixPath(path).stem for path in paths})
    if expected_family:
        matched = [name for name in platform_names if expected_family in name.lower()]
    else:
        matched = [name for name in platform_names if any(token in name.lower() for token in ("u55c", "u50"))]
    if len(matched) == 1:
        return {
            "status": PASS_STATUS,
            "selected_platform": matched[0],
            "selected_xpfm": "",
            "candidates": matched,
            "all_platforms": platform_names,
            "output": output,
        }
    shell_probe = _probe_shell_name(server, settings, helper, profile)
    reason = "no_matching_platform" if not matched else "multiple_matching_platforms"
    if shell_probe.get("shell_name"):
        reason = f"{reason}_shell_detected"
    return {
        "status": BLOCKED_BOARD_STATUS,
        "selected_platform": "",
        "selected_xpfm": "",
        "candidates": matched,
        "all_platforms": platform_names,
        "reason": reason,
        "shell_name": str(shell_probe.get("shell_name") or ""),
        "suggested_platform_name": str(shell_probe.get("suggested_platform_name") or ""),
        "output": output,
    }
def _probe_uploaded_platform(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if not profile:
        return {"status": BLOCKED_BOARD_STATUS, "reason": "missing_profile"}
    remote_xpfm = str(profile.get("remote_xpfm") or "").strip()
    remote_platform_root = str(profile.get("remote_platform_root") or "").strip()
    platform_name = str(profile.get("platform_name") or "").strip()
    if remote_xpfm:
        command = ["bash", "-lc", f"if [ -f {shlex.quote(remote_xpfm)} ]; then printf 'selected_xpfm=%s' {shlex.quote(remote_xpfm)}; fi"]
        output = helper.exec(server, command, settings=settings)
        _reject_decode_noise(output)
        selected_xpfm = _section_value(output, "selected_xpfm")
        if selected_xpfm:
            return {"status": PASS_STATUS, "selected_xpfm": selected_xpfm, "output": output}
        return {"status": BLOCKED_BOARD_STATUS, "reason": "missing_uploaded_xpfm", "output": output}
    if remote_platform_root:
        command = [
            "bash",
            "-lc",
            f"find {shlex.quote(remote_platform_root)} -maxdepth 3 -type f -name '*.xpfm' 2>/dev/null | sed -n '1,40p'",
        ]
        output = helper.exec(server, command, settings=settings)
        _reject_decode_noise(output)
        paths = [line.strip() for line in output.splitlines() if line.strip()]
        if not paths:
            return {"status": BLOCKED_BOARD_STATUS, "reason": "missing_uploaded_platform_payload", "output": output}
        if platform_name:
            matched = [path for path in paths if PurePosixPath(path).stem == platform_name]
            if len(matched) == 1:
                return {"status": PASS_STATUS, "selected_xpfm": matched[0], "output": output}
        if len(paths) == 1:
            return {"status": PASS_STATUS, "selected_xpfm": paths[0], "output": output}
        return {"status": BLOCKED_BOARD_STATUS, "reason": "multiple_uploaded_xpfm_candidates", "output": output}
    return {"status": BLOCKED_BOARD_STATUS, "reason": "missing_uploaded_platform_payload"}
def _probe_shell_name(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    xbmgmt_tool_path = str(profile.get("xbmgmt_tool_path") or "").strip() if profile else ""
    command_text = (
        f"if [ -x {shlex.quote(xbmgmt_tool_path)} ]; then {shlex.quote(xbmgmt_tool_path)} examine 2>/dev/null; "
        "else xbmgmt examine 2>/dev/null; fi"
        if xbmgmt_tool_path
        else "xbmgmt examine 2>/dev/null || true"
    )
    output = helper.exec(server, ["bash", "-lc", command_text], settings=settings)
    _reject_decode_noise(output)
    shell_name = ""
    for line in output.splitlines():
        match = re.search(r"\|\[[^\]]+\]\s+\|\s*([A-Za-z0-9_]+)\s+\|", line)
        if match:
            shell_name = match.group(1).strip()
            break
    return {
        "shell_name": shell_name,
        "suggested_platform_name": _suggest_platform_name_from_shell(shell_name),
        "output": output,
    }
def _suggest_platform_name_from_shell(shell_name: str) -> str:
    normalized = str(shell_name or "").strip().lower()
    if normalized == "xilinx_u55c_gen3x16_xdma_base_3":
        return "xilinx_u55c_gen3x16_xdma_3_202210_1"
    return ""
def _merge_profile_fields(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, str):
            if value.strip():
                merged[key] = value
            continue
        if value is not None:
            merged[key] = value
    return merged
def _section_value(output: str, key: str) -> str:
    prefix = f"{key}="
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        parts = [line.split("=", 1)[1].strip()]
        for extra in lines[index + 1 :]:
            if re.match(r"^[A-Za-z0-9_]+=", extra):
                break
            parts.append(extra.strip())
        return "\n".join(item for item in parts if item)
    return ""
def _probe_remote_workdir(server: str, settings: Path, helper: ErieHelper) -> str:
    output = helper.exec(server, ["bash", "-lc", "pwd"], settings=settings)
    _reject_decode_noise(output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise RemoteAcceptanceError(f"Could not determine remote workdir for server {server}.")
    return lines[-1]
def _ensure_remote_project_layout(helper: ErieHelper, settings: Path, server: str, layout: dict[str, str]) -> list[str]:
    request_paths: list[str] = []
    project_root = shlex.quote(layout["project_root_relative"])
    conda_prefix = shlex.quote(layout["conda_prefix_relative"])
    runs_parent = shlex.quote(str(PurePosixPath(layout["active_run_relative"]).parent))
    backups_parent = shlex.quote(str(PurePosixPath(layout["backup_run_relative"]).parent))
    active_run = shlex.quote(layout["active_run_relative"])
    command = f"mkdir -p {project_root} {conda_prefix} {runs_parent} {backups_parent} {active_run}"
    request_paths.append(helper.request_and_run(settings, server, "command", command, "prepare governed remote project root, conda prefix path, and active run directory"))
    return request_paths
def _archive_remote_run(helper: ErieHelper, settings: Path, server: str, layout: dict[str, str]) -> str:
    active_run = shlex.quote(layout["active_run_relative"])
    backup_run = shlex.quote(layout["backup_run_relative"])
    backup_parent = shlex.quote(str(PurePosixPath(layout["backup_run_relative"]).parent))
    command = f"mkdir -p {backup_parent} && rm -rf {backup_run} && mv {active_run} {backup_run}"
    return helper.request_and_run(settings, server, "command", command, "archive verified remote run into governed backups directory")
def _write_erie_settings_overlay(config: dict[str, Any], run_dir: Path) -> Path:
    base_settings_path = Path(config["erie_settings_path"])
    settings = json.loads(base_settings_path.read_text(encoding="utf-8"))
    settings.setdefault("paths", {})
    settings["paths"]["default_server_list"] = str(_resolve_erie_server_list(settings, base_settings_path, Path(config["erie_skill_dir"])))
    settings["paths"]["requests_dir"] = str(run_dir / "requests")
    settings["paths"]["downloads_dir"] = str(run_dir / "downloads")
    settings["paths"]["validation_tmp_dir"] = str(run_dir / "tmp")
    upload_roots = [str(skill_root().parents[1])]
    for item in settings["paths"].get("upload_roots", []):
        if isinstance(item, str) and item not in upload_roots:
            upload_roots.append(item)
    settings["paths"]["upload_roots"] = upload_roots
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
    run_dir = repo_root() / str(config["local_run_root"]) / f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"
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
    elif mode == "board":
        profile_label = profile or "<user-configured-profile>"
        steps.extend(
            [
                f"erie exec board profile probe {profile_label}",
                "erie exec hardware fingerprint probe for 9950X/U55C evidence",
                f"generate local HLS mock artifacts from {example_spec or 'default example'}",
                "render validation-only board host scaffold",
                "ensure governed remote project root and project-local conda prefix",
                "prepare governed remote run directory under runs/<run-id>",
                "erie request command payload transfer",
                "erie exec detached board compile/link/host-run sequence",
                "archive verified remote run into backups/<run-id>",
            ]
        )
    else:
        profile_label = profile or "<user-configured-profile>"
        steps.extend(
            [
                f"erie exec Vitis profile probe {profile_label}",
                f"generate local HLS mock artifacts from {example_spec or 'default example'}",
                "ensure governed remote project root and project-local conda prefix",
                "prepare governed remote run directory under runs/<run-id>",
                "erie request command payload transfer",
                f"erie request command Vitis {readiness}",
                "archive verified remote run into backups/<run-id>",
            ]
        )
        if topology == "split_build_validate" and validate_server:
            steps.extend(["erie exec validation server device probe", "prepare governed validation run directory", "erie request command payload transfer validation", f"erie request command validation Vitis {readiness}"])
        if cleanup_remote:
            steps.append("keep archived backup and skip active-directory deletion because archive is mandatory")
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
def _is_transient_status_failure(output: str) -> bool:
    lowered = output.lower()
    transient_markers = (
        "timed out",
        "banner exchange",
        "connection aborted",
        "connection reset",
        "connection closed",
        "kex_exchange_identification",
    )
    return any(marker in lowered for marker in transient_markers)
def _format_result(result: dict[str, Any]) -> str:
    lines = [f"status: {result.get('status')}"]
    for key in (
        "mode",
        "topology",
        "server",
        "build_server",
        "validate_server",
        "profile",
        "vitis_version",
        "readiness",
        "example_spec",
        "run_dir",
        "run_id",
        "remote_project_root",
        "remote_conda_prefix",
        "remote_run_dir",
        "remote_backup_dir",
        "remote_dir",
        "remote_vitis_version_request",
        "remote_vitis_profile_request",
    ):
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
    if result.get("archived_after_verification") is not None:
        lines.append(f"archived_after_verification: {result['archived_after_verification']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
