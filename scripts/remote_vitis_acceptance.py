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
import uuid
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from integration.hls_adapter import run_hls_workflow  # noqa: E402
from runtime.hls_generator.config import remote_validation_config, skill_config_path, skill_dependencies_config, skill_root  # noqa: E402
from runtime.hls_generator.skill_dependencies import SkillDependencyError, require_skill_dependencies  # noqa: E402
from runtime.hls_generator.user_config import get_vitis_selection, set_vitis_selection, user_config_path  # noqa: E402
from runtime.hls_generator.validation import READINESS_LEVELS  # noqa: E402

PASS_STATUS = "passed"
DRY_RUN_STATUS = "dry_run"
BLOCKED_VITIS_STATUS = "blocked_vitis_server"
BLOCKED_VERSION_STATUS = "blocked_remote_version_choice"
FAILED_STATUS = "failed"
UTF8_HINT = "Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 when calling erie-remote-ssh."


class RemoteAcceptanceError(RuntimeError):
    """Expected user-facing remote acceptance failure."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate HLS generator remote confidence through erie-remote-ssh.")
    parser.add_argument("--mode", required=True, choices=("link", "vitis"))
    parser.add_argument("--server", required=True, help="Server id or name from erie-remote-ssh config.")
    parser.add_argument("--profile", default="vitis_2022", help="remote_validation.vitis_profiles key for Vitis mode.")
    parser.add_argument("--vitis-version", help="Explicit remote Vitis version to use and remember for this server.")
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
    if result["status"] == BLOCKED_VERSION_STATUS:
        return 4
    if result["status"] == BLOCKED_VITIS_STATUS:
        return 3
    return 1


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    require_skill_dependencies(skill_dependencies_config())
    config = remote_validation_config()
    timeout = int(args.timeout or config["default_timeout_s"])
    helper = ErieHelper(config, timeout)
    plan = _planned_steps(args.mode, args.server, args.profile, args.readiness, cleanup_remote=bool(getattr(args, "cleanup_remote", False)), example_spec=str(getattr(args, "example_spec", "")))
    if args.dry_run:
        result = {"status": DRY_RUN_STATUS, "mode": args.mode, "server": args.server, "steps": plan, "uses_erie_remote_ssh": True}
        if args.mode == "vitis":
            result.update({"cleanup_performed": False, "remote_artifacts_retained": True})
        return result
    if args.mode == "link":
        return _run_link_mode(args, config, helper, plan)
    return _run_vitis_mode(args, config, helper, plan)


def _run_link_mode(args: argparse.Namespace, config: dict[str, Any], helper: "ErieHelper", plan: list[str]) -> dict[str, Any]:
    run_dir = _new_run_dir(config, "link")
    helper.preflight(args.server)
    output = helper.exec(args.server, list(config["link_probe_command"]))
    _reject_decode_noise(output)
    required = ("HLS_REMOTE_LINK_OK", "host=", "pwd=", "python=")
    missing = [item for item in required if item not in output]
    status = PASS_STATUS if not missing else FAILED_STATUS
    result = {
        "status": status,
        "mode": "link",
        "server": args.server,
        "run_dir": str(run_dir),
        "steps": plan,
        "output": output,
        "missing_markers": missing,
        "uses_erie_remote_ssh": True,
    }
    _write_report(run_dir, result)
    return result


def _run_vitis_mode(args: argparse.Namespace, config: dict[str, Any], helper: "ErieHelper", plan: list[str]) -> dict[str, Any]:
    profiles = config["vitis_profiles"]
    if args.profile not in profiles:
        raise RemoteAcceptanceError(f"Unknown remote Vitis profile {args.profile!r}.")
    profile = profiles[args.profile]
    run_dir = _new_run_dir(config, "vitis")
    settings = _write_erie_settings_overlay(config, run_dir)
    helper.preflight(args.server, settings=settings)
    helper.scan_software(args.server, settings=settings)
    candidates = _vitis_version_candidates(config, settings, args.server, profile)
    selected_profile = _select_vitis_profile(args, run_dir, candidates, profile)
    if selected_profile.get("status") == BLOCKED_VERSION_STATUS:
        _write_report(run_dir, selected_profile)
        return selected_profile

    profile_probe = _probe_vitis(args.server, settings, helper, selected_profile)
    if profile_probe["status"] != PASS_STATUS:
        result = {
            "status": BLOCKED_VITIS_STATUS,
            "mode": "vitis",
            "server": args.server,
            "profile": args.profile,
            "vitis_version": selected_profile.get("version"),
            "readiness": args.readiness,
            "run_dir": str(run_dir),
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
    request_paths.append(helper.request_and_run(settings, args.server, "mkdir", [remote_dir], "prepare remote HLS validation directory"))
    request_paths.extend(_transfer_package_by_request_commands(helper, settings, args.server, remote_dir, package_path))
    command = _remote_vitis_command(remote_dir, selected_profile, args.readiness)
    request_paths.append(helper.request_and_run(settings, args.server, "command", command, "run Vitis HLS acceptance"))
    if args.cleanup_remote:
        request_paths.append(helper.request_and_run(settings, args.server, "delete", [remote_dir, "--recursive"], "cleanup remote HLS validation directory"))
        cleanup_performed = True

    result = {
        "status": PASS_STATUS,
        "mode": "vitis",
        "server": args.server,
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

    def _run(self, args: list[str]) -> str:
        env = os.environ.copy()
        env.update(self.config["python_env"])
        command = [sys.executable, str(self.script), *args]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=max(self.timeout + 10, 30), check=False)
        combined = (result.stdout or "") + (result.stderr or "")
        _reject_decode_noise(combined)
        if result.returncode != 0:
            raise RemoteAcceptanceError(f"erie-remote-ssh command failed ({args[0]}): {combined.strip()}")
        return combined


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


def _vitis_version_candidates(config: dict[str, Any], settings_path: Path, server: str, fallback_profile: dict[str, Any]) -> list[dict[str, Any]]:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    server_list_path = _resolve_erie_server_list(settings, settings_path, Path(config["erie_skill_dir"]))
    try:
        server_list = json.loads(server_list_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    raw_server = _find_server_record(server_list, server)
    if not raw_server:
        return []
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
        settings_script = (install_path.rstrip("/") + "/settings64.sh") if install_path else str(fallback_profile.get("settings_script") or "")
        candidates.append(
            {
                "version": version,
                "settings_script": settings_script,
                "expected_tool": "vitis_hls",
                "target_part": str(fallback_profile.get("target_part", "")),
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
    raw = str(settings.get("paths", {}).get("default_server_list") or erie_skill_dir / "config" / "server_list.local.json")
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


def _planned_steps(mode: str, server: str, profile: str, readiness: str, *, cleanup_remote: bool = False, example_spec: str = "") -> list[str]:
    steps = ["erie discover", "erie list", f"erie check {server}", f"erie workspace-check {server}"]
    if mode == "link":
        steps.append("erie exec read-only UTF-8 link probe")
    else:
        steps.extend([f"erie exec Vitis profile probe {profile}", f"generate local HLS mock artifacts from {example_spec or 'default example'}", "erie request mkdir", "erie request command payload transfer", f"erie request command Vitis {readiness}"])
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


def _reject_decode_noise(output: str) -> None:
    if "UnicodeDecodeError" in output or "_readerthread" in output:
        raise RemoteAcceptanceError(f"erie-remote-ssh output decoding failed. {UTF8_HINT}")


def _format_result(result: dict[str, Any]) -> str:
    lines = [f"status: {result.get('status')}"]
    for key in ("mode", "server", "profile", "vitis_version", "readiness", "example_spec", "run_dir", "remote_dir", "remote_vitis_version_request"):
        if result.get(key) is not None:
            lines.append(f"{key}: {result[key]}")
    if result.get("error"):
        lines.append(f"error: {result['error']}")
    if result.get("probe"):
        lines.append(f"probe: {result['probe'].get('status')}")
    if result.get("remote_artifacts_retained") is not None:
        lines.append(f"remote_artifacts_retained: {result['remote_artifacts_retained']}")
    if result.get("cleanup_performed") is not None:
        lines.append(f"cleanup_performed: {result['cleanup_performed']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
