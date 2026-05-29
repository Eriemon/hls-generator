#!/usr/bin/env python3
"""Run repeatable Erie HLS Generator confidence gates."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

MODULE_DIR = Path(__file__).resolve().parent
SKILL_ROOT = Path(__file__).resolve().parents[3]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import confidence_local as _cl
import confidence_remote as _cr
import confidence_report as _cp
from runtime.hls_generator import __version__  # noqa: E402

PASS_STATUS = "passed"
TRANSIENT_REMOTE_FAILURE_MARKERS = (
    "ssh: connect to host",
    "unknown error",
    "connection reset by peer",
    "connection closed by remote host",
    "broken pipe",
)

_iter_scan_paths = _cl._iter_scan_paths
_tier1_board_matrix_path = _cl._tier1_board_matrix_path
_run_command = _cl._run_command
_quick_validate_path = _cl._quick_validate_path
_skill_dependency_gate = _cl._skill_dependency_gate
_forbidden_reference_name_scan = _cl._forbidden_reference_name_scan
_example_spec_names = _cl._example_spec_names
_validate_examples = _cl._validate_examples
_comment_policy_gate = _cl._comment_policy_gate
_forward_test_gate = _cl._forward_test_gate
_resolve_json_output = _cl._resolve_json_output
_tail = _cl._tail
_route_contract_gate = _cr._route_contract_gate
_board_acceptance_partition_gate = _cr._board_acceptance_partition_gate
_remote_directory_contract_gate = _cr._remote_directory_contract_gate
_residual_risks = _cp._residual_risks
_orig_iter_scan_paths = _cl._iter_scan_paths
_orig_run_remote = _cr._run_remote
_orig_run_remote_board = _cr._run_remote_board
_orig_run_split_remote = _cr._run_split_remote


def _copyright_term_scan(*, root: Path | None = None) -> dict[str, Any]:
    _cl._iter_scan_paths = _iter_scan_paths
    _cl.repo_root = repo_root
    return _cl._copyright_term_scan(root=root)


def _release_sensitivity_scan(*, root: Path | None = None) -> dict[str, Any]:
    if root is not None:
        def _scan_paths(scan_root: Path):
            yield scan_root
            for current_root, dirnames, filenames in os.walk(scan_root, topdown=True, onerror=lambda _exc: None):
                current_path = Path(current_root)
                dirnames[:] = [name for name in dirnames if name not in _cl.SKIP_SCAN_DIRS]
                for dirname in dirnames:
                    yield current_path / dirname
                for filename in filenames:
                    yield current_path / filename
        _cl._iter_scan_paths = _scan_paths
    else:
        _cl._iter_scan_paths = _orig_iter_scan_paths
    _cl.repo_root = repo_root
    return _cl._release_sensitivity_scan(root=root)


def _remote_default_example_specs(coverage_mode: str) -> list[str]:
    _cl._tier1_board_matrix_path = _tier1_board_matrix_path
    return _cl._remote_default_example_specs(coverage_mode)


def _resolve_remote_example_specs(explicit_specs: list[str] | None, coverage_mode: str) -> tuple[list[str], str]:
    _cl._tier1_board_matrix_path = _tier1_board_matrix_path
    return _cl._resolve_remote_example_specs(explicit_specs, coverage_mode)


def _confidence_outcome(gates: dict[str, dict[str, Any]], *, remote_requested: bool, remote_skipped: bool) -> tuple[str, str, list[str], int]:
    _cp.PASS_STATUS = PASS_STATUS
    _cp._residual_risks = _residual_risks
    return _cp._confidence_outcome(gates, remote_requested=remote_requested, remote_skipped=remote_skipped)


def _run_remote_command(command: list[str], *, timeout_s: int = 900) -> dict[str, Any]:
    result = _cl._run_process(command, cwd=SKILL_ROOT, timeout_s=timeout_s)
    if result["timed_out"]:
        return {
            "status": "timeout",
            "command": command,
            "returncode": None,
            "timeout_s": timeout_s,
            "stdout_tail": _tail(result["stdout"]),
            "stderr_tail": _tail(result["stderr"]),
        }
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError:
        payload = {"status": "failed", "stdout_tail": _tail(result["stdout"]), "stderr_tail": _tail(result["stderr"])}
    payload["returncode"] = result["returncode"]
    payload["timeout_s"] = timeout_s
    return payload


def _is_transient_remote_failure(payload: dict[str, Any]) -> bool:
    if str(payload.get("status") or "") == PASS_STATUS:
        return False
    text = "\n".join(
        str(payload.get(key) or "")
        for key in ("error", "message", "stdout_tail", "stderr_tail")
    ).lower()
    return any(marker in text for marker in TRANSIENT_REMOTE_FAILURE_MARKERS)


def _run_remote_command_with_retry(command: list[str], *, timeout_s: int = 900, retries: int = 1) -> dict[str, Any]:
    payload = _run_remote_command(command, timeout_s=timeout_s)
    attempt = 0
    while attempt < retries and _is_transient_remote_failure(payload):
        attempt += 1
        payload = _run_remote_command(command, timeout_s=timeout_s)
        payload["retry_count"] = attempt
    return payload


def _run_remote(server: str, readiness: str, spec_name: str, *, vitis_version: str | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/python/remote/remote_vitis_acceptance.py",
        "--mode",
        "vitis",
        "--server",
        server,
        "--readiness",
        readiness,
        "--example-spec",
        spec_name,
        "--comment-language",
        "zh",
        "--json",
    ]
    if vitis_version:
        command.extend(["--vitis-version", vitis_version])
    payload = _run_remote_command_with_retry(command, timeout_s=5400, retries=1)
    payload["example_spec"] = spec_name
    return payload


def _run_remote_board(server: str, readiness: str, spec_name: str, *, vitis_version: str | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/python/remote/remote_vitis_acceptance.py",
        "--mode",
        "board",
        "--server",
        server,
        "--readiness",
        readiness,
        "--example-spec",
        spec_name,
        "--comment-language",
        "zh",
        "--timeout",
        "5400",
        "--json",
    ]
    if vitis_version:
        command.extend(["--vitis-version", vitis_version])
    payload = _run_remote_command_with_retry(command, timeout_s=5400, retries=1)
    payload["example_spec"] = spec_name
    return payload


def _run_split_remote(build_server: str, validate_server: str, readiness: str, spec_name: str, *, vitis_version: str | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/python/remote/remote_vitis_acceptance.py",
        "--mode",
        "vitis",
        "--build-server",
        build_server,
        "--validate-server",
        validate_server,
        "--readiness",
        readiness,
        "--example-spec",
        spec_name,
        "--comment-language",
        "zh",
        "--json",
    ]
    if vitis_version:
        command.extend(["--vitis-version", vitis_version])
    payload = _run_remote_command_with_retry(command, retries=1)
    payload["example_spec"] = spec_name
    return payload


def _run_remote_acceptance(server: str, readiness: str, example_specs: list[str], *, vitis_version: str | None = None, parallelism: int = 1) -> dict[str, Any]:
    link_payload = _run_remote_command_with_retry(
        [
            sys.executable,
            "scripts/python/remote/remote_vitis_acceptance.py",
            "--mode",
            "link",
            "--server",
            server,
            "--timeout",
            "300",
            "--json",
        ],
        retries=1,
    )
    if link_payload.get("status") != "passed":
        return {"status": "failed", "server": server, "vitis_version": vitis_version, "link": link_payload, "results": []}
    vitis_results = _cr._run_parallel_specs(example_specs, lambda spec_name: _run_remote(server, readiness, spec_name, vitis_version=vitis_version), parallelism=parallelism)
    passed = link_payload.get("status") == "passed" and all(item.get("status") == "passed" and item.get("remote_artifacts_retained") is True for item in vitis_results)
    return {"status": "passed" if passed else "failed", "server": server, "vitis_version": vitis_version, "link": link_payload, "results": vitis_results}


def _run_split_remote_acceptance(build_server: str, validate_server: str, readiness: str, example_specs: list[str], *, vitis_version: str | None = None, parallelism: int = 1) -> dict[str, Any]:
    if not example_specs:
        return {"status": "failed", "topology": "split_build_validate", "build_server": build_server, "validate_server": validate_server, "vitis_version": vitis_version, "results": []}
    first_result = _run_split_remote(build_server, validate_server, readiness, example_specs[0], vitis_version=vitis_version)
    if first_result.get("status") != "passed":
        return {"status": "failed", "topology": "split_build_validate", "build_server": build_server, "validate_server": validate_server, "vitis_version": vitis_version, "results": [], "first_result": first_result}
    remaining = _cr._run_parallel_specs(example_specs[1:], lambda spec_name: _run_split_remote(build_server, validate_server, readiness, spec_name, vitis_version=vitis_version), parallelism=parallelism)
    results = [first_result, *remaining]
    passed = all(item.get("status") == "passed" and item.get("remote_artifacts_retained") is True for item in results)
    return {"status": "passed" if passed else "failed", "topology": "split_build_validate", "build_server": build_server, "validate_server": validate_server, "vitis_version": vitis_version, "results": results}


def _remote_board_acceptance_gate(server: str | None, readiness: str, *, vitis_version: str | None, remote_requested: bool, remote_vitis_gate: dict[str, Any] | None, board_partition: dict[str, Any], selected_specs: list[str], parallelism: int) -> dict[str, Any]:
    invalid_specs = board_partition.get("invalid_specs", [])
    if invalid_specs:
        return {"status": "failed", "reason": "invalid_board_acceptance_metadata", "invalid_specs": invalid_specs}
    board_specs = [entry for entry in board_partition.get("board_specs", []) if entry["spec"] in set(selected_specs)]
    exempt_specs = [entry for entry in board_partition.get("exempt_specs", []) if entry["spec"] in set(selected_specs)]
    if not remote_requested:
        return {"status": "passed", "mode": "declarations_only", "board_specs": board_specs, "exempt_specs": exempt_specs, "results": []}
    if not server:
        return {"status": "failed", "reason": "board acceptance requires a single remote server", "results": []}
    if not remote_vitis_gate or remote_vitis_gate.get("status") != "passed":
        return {"status": "blocked", "reason": "board acceptance requires successful remote vitis acceptance first", "results": []}
    if not board_specs:
        return {"status": "passed", "mode": "no_board_specs_selected", "board_specs": [], "exempt_specs": exempt_specs, "results": []}
    results = _cr._run_parallel_specs([entry["spec"] for entry in board_specs], lambda spec_name: _run_remote_board(server, readiness, spec_name, vitis_version=vitis_version), parallelism=parallelism)
    statuses = {str(item.get("status")) for item in results}
    if statuses == {PASS_STATUS}:
        status = "passed"
    elif any(item in statuses for item in {"blocked_board_validation", "blocked_remote_profile_config", "blocked_remote_version_choice"}):
        status = "blocked"
    else:
        status = "failed"
    return {"status": status, "mode": "remote_board_validation", "board_specs": board_specs, "exempt_specs": exempt_specs, "results": results}


def _remote_family_coverage_gate(results: list[dict[str, Any]], *, coverage_mode: str) -> dict[str, Any]:
    _cl._tier1_board_matrix_path = _tier1_board_matrix_path
    if coverage_mode != "tier1":
        return {"status": "skipped", "mode": coverage_mode, "required_specs": [], "vitis_passed_specs": [], "board_passed_specs": [], "missing_specs": []}
    matrix = _cl._load_tier1_board_matrix()
    families = matrix.get("families", {}) if isinstance(matrix, dict) else {}
    required_specs: list[str] = []
    for config in families.values():
        if not isinstance(config, dict):
            continue
        for key in ("representative", "high_risk"):
            value = str(config.get(key) or "").strip()
            if value and value not in required_specs:
                required_specs.append(value)
    vitis_passed = {str(item.get("example_spec") or "") for item in results if str(item.get("phase") or "") == "vitis" and str(item.get("status") or "") == PASS_STATUS}
    board_passed = {str(item.get("example_spec") or "") for item in results if str(item.get("phase") or "") == "board" and str(item.get("status") or "") == PASS_STATUS}
    missing_specs = [spec_name for spec_name in required_specs if spec_name not in vitis_passed or spec_name not in board_passed]
    return {"status": "passed" if not missing_specs else "failed", "mode": str(matrix.get("mode") or coverage_mode), "required_specs": required_specs, "vitis_passed_specs": sorted(vitis_passed), "board_passed_specs": sorted(board_passed), "missing_specs": missing_specs}


def repo_root() -> Path:
    return SKILL_ROOT.parents[1]


def _cleanup_ephemeral_validation_dirs() -> None:
    for base in (repo_root(), SKILL_ROOT):
        cache_dir = base / ".pytest_cache"
        if not cache_dir.exists():
            continue
        for path in sorted(cache_dir.rglob("*"), reverse=True):
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            except (FileNotFoundError, OSError):
                continue
        try:
            cache_dir.rmdir()
        except (FileNotFoundError, OSError):
            continue


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run Erie HLS Generator local and optional remote confidence gates.")
    parser.add_argument("--server", help="Optional erie-remote-ssh server for real remote Vitis validation.")
    parser.add_argument("--build-server", help="Optional split-topology build server for real remote Vitis validation.")
    parser.add_argument("--validate-server", help="Optional split-topology validation server for real remote Vitis validation.")
    parser.add_argument("--vitis-version", help="Explicit remote Vitis version to use for remote matrix validation.")
    parser.add_argument("--readiness", default="cosim", choices=("static", "compile", "execute", "implement", "cosim"))
    parser.add_argument("--example-spec", action="append", help="Example spec to use for optional remote validation. Can be repeated.")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--skip-compileall", action="store_true")
    parser.add_argument("--skip-quick-validate", action="store_true")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-remote", action="store_true")
    parser.add_argument("--remote-parallelism", type=int, default=3, help="Requested concurrent remote review jobs; the runtime hard-caps actual Vivado/Vitis fan-out at 3.")
    parser.add_argument("--remote-coverage", default="smoke", choices=("smoke", "tier1", "all_examples"), help="Default remote example set when --example-spec is not provided.")
    parser.add_argument("--gate-timeout-s", type=int, default=900, help="Timeout for each local confidence gate command.")
    parser.add_argument("--json-out", help="Write JSON summary to this path.")
    args = parser.parse_args(argv)

    run_root = repo_root() / "reports" / "confidence-loop" / f"{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S%fZ')}-pid{os.getpid()}"
    run_root.mkdir(parents=True, exist_ok=True)

    gates: dict[str, dict[str, Any]] = {}
    if not args.skip_smoke:
        gates["smoke"] = _run_command([sys.executable, "smoke/run_smoke.py"], cwd=repo_root(), timeout_s=args.gate_timeout_s)
    if not args.skip_compileall:
        gates["compileall"] = _run_command([sys.executable, "-m", "compileall", "runtime/hls_generator"], cwd=SKILL_ROOT, timeout_s=args.gate_timeout_s)
    if not args.skip_quick_validate:
        gates["quick_validate"] = _run_command([sys.executable, "scripts/python/validation/quick_validate.py", str(SKILL_ROOT)], cwd=SKILL_ROOT, timeout_s=args.gate_timeout_s)
    if not args.skip_pytest:
        gates["pytest"] = _run_command([sys.executable, "-m", "pytest", "-q", "tests"], cwd=repo_root(), timeout_s=args.gate_timeout_s)
    _cleanup_ephemeral_validation_dirs()
    gates["verify_agents"] = _run_command([sys.executable, "scripts/python/governance/verify_agents.py", str(repo_root())], cwd=SKILL_ROOT, timeout_s=args.gate_timeout_s)
    gates["manage_docs_verify"] = _run_command([sys.executable, "scripts/python/governance/manage_docs.py", "verify", str(repo_root())], cwd=SKILL_ROOT, timeout_s=args.gate_timeout_s)
    gates["manage_dirs_verify"] = _run_command([sys.executable, "scripts/python/governance/manage_dirs.py", "verify", str(repo_root())], cwd=SKILL_ROOT, timeout_s=args.gate_timeout_s)
    gates["skill_dependencies"] = _skill_dependency_gate()
    gates["copyright_term_scan"] = _copyright_term_scan()
    gates["release_sensitivity_scan"] = _release_sensitivity_scan()
    gates["forbidden_reference_names"] = _forbidden_reference_name_scan()
    example_specs = _example_spec_names()
    remote_example_specs, remote_coverage_mode = _resolve_remote_example_specs(args.example_spec, args.remote_coverage)
    if gates["skill_dependencies"]["status"] == "passed":
        examples_gate, example_specs = _validate_examples(run_root)
    else:
        examples_gate = {"status": "skipped", "reason": "blocked_dependency", "results": []}
    gates["example_mock_validation"] = examples_gate
    gates["comment_policy"] = _comment_policy_gate(run_root) if gates["skill_dependencies"]["status"] == "passed" else {"status": "skipped", "reason": "blocked_dependency"}
    gates["forward_test"] = _forward_test_gate(run_root) if gates["skill_dependencies"]["status"] == "passed" else {"status": "skipped", "reason": "blocked_dependency", "results": []}
    remote_results: list[dict[str, Any]] = []
    split_remote_requested = bool(args.build_server and args.validate_server and not args.skip_remote)
    remote_requested = bool((args.server or split_remote_requested) and not args.skip_remote)
    gates["route_contract"] = _route_contract_gate(args.server, args.build_server, args.validate_server, remote_requested=remote_requested)
    board_partition = _board_acceptance_partition_gate()
    gates["board_acceptance_declarations"] = board_partition
    if split_remote_requested:
        selected_remote_specs = remote_example_specs
        if gates["route_contract"]["status"] == "passed":
            gates["remote_vitis_acceptance"] = _run_split_remote_acceptance(args.build_server, args.validate_server, args.readiness, selected_remote_specs, vitis_version=args.vitis_version, parallelism=args.remote_parallelism)
            remote_results = gates["remote_vitis_acceptance"].get("results", [])
    elif remote_requested and gates["route_contract"]["status"] == "passed":
        selected_remote_specs = remote_example_specs
        gates["remote_vitis_acceptance"] = _run_remote_acceptance(args.server, args.readiness, selected_remote_specs, vitis_version=args.vitis_version, parallelism=args.remote_parallelism)
        remote_results = gates["remote_vitis_acceptance"]["results"]
    gates["remote_directory_contract"] = _remote_directory_contract_gate(remote_results, remote_requested=remote_requested)
    gates["remote_board_acceptance"] = _remote_board_acceptance_gate(args.server, args.readiness, vitis_version=args.vitis_version, remote_requested=remote_requested and not split_remote_requested, remote_vitis_gate=gates.get("remote_vitis_acceptance"), board_partition=board_partition, selected_specs=remote_example_specs, parallelism=args.remote_parallelism)
    coverage_results: list[dict[str, Any]] = []
    remote_vitis_gate = gates.get("remote_vitis_acceptance", {})
    if isinstance(remote_vitis_gate, dict):
        for item in remote_vitis_gate.get("results", []) or []:
            if isinstance(item, dict):
                coverage_results.append({"example_spec": item.get("example_spec"), "phase": "vitis", "status": item.get("status")})
    remote_board_gate = gates.get("remote_board_acceptance", {})
    if isinstance(remote_board_gate, dict):
        for item in remote_board_gate.get("results", []) or []:
            if isinstance(item, dict):
                coverage_results.append({"example_spec": item.get("example_spec"), "phase": "board", "status": item.get("status")})
    gates["remote_family_coverage"] = _remote_family_coverage_gate(coverage_results, coverage_mode=remote_coverage_mode)

    confidence_status, confidence_scope, residual_risks, returncode = _confidence_outcome(gates, remote_requested=remote_requested, remote_skipped=bool(args.skip_remote))
    payload = {"version": 1, "confidence_status": confidence_status, "confidence_scope": confidence_scope, "run_root": str(run_root), "gates": gates, "example_specs": example_specs, "remote_example_specs": remote_example_specs, "remote_coverage_mode": remote_coverage_mode, "remote_results": remote_results, "residual_risks": residual_risks}
    if args.json_out:
        output_path = _resolve_json_output(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
