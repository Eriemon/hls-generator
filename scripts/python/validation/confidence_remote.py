#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.hls_generator.board_acceptance import partition_example_specs_by_board_acceptance  # noqa: E402
from runtime.hls_generator.config import skill_config_path  # noqa: E402
from runtime.hls_generator.remote_directory_contract import validate_remote_result_contract  # noqa: E402
from runtime.hls_generator.route_contract import load_remote_route_contract, validate_remote_route_target  # noqa: E402
from confidence_local import _load_tier1_board_matrix, _run_process, _tail  # noqa: E402

PASS_STATUS = "passed"
MAX_REMOTE_PARALLELISM = 3
TRANSIENT_REMOTE_FAILURE_MARKERS = (
    "ssh: connect to host",
    "unknown error",
    "connection reset by peer",
    "connection closed by remote host",
    "broken pipe",
)

def _route_contract_gate(
    server: str | None,
    build_server: str | None,
    validate_server: str | None,
    *,
    remote_requested: bool,
) -> dict[str, Any]:
    contract = load_remote_route_contract(SKILL_ROOT)
    if not remote_requested:
        return {"status": "passed", "mode": "not_requested", "contract": contract}
    issues = validate_remote_route_target(
        contract,
        server=server,
        build_server=build_server,
        validate_server=validate_server,
    )
    return {
        "status": "passed" if not issues else "failed",
        "mode": "remote_requested",
        "contract": contract,
        "issues": issues,
    }
def _board_acceptance_partition_gate() -> dict[str, Any]:
    partition = partition_example_specs_by_board_acceptance(skill_config_path("examples_dir"))
    invalid_specs = partition["invalid_specs"]
    return {
        "status": "passed" if not invalid_specs else "failed",
        **partition,
    }
def _remote_directory_contract_gate(remote_results: list[dict[str, Any]], *, remote_requested: bool) -> dict[str, Any]:
    if not remote_requested:
        return {"status": "passed", "mode": "static_contract_only", "results": []}
    if not remote_results:
        return {"status": "failed", "mode": "remote_required", "results": [], "issues": ["remote results missing"]}
    results: list[dict[str, Any]] = []
    for item in remote_results:
        errors = validate_remote_result_contract(item)
        results.append(
            {
                "example_spec": str(item.get("example_spec") or item.get("phase") or ""),
                "run_id": item.get("run_id"),
                "status": "passed" if not errors else "failed",
                "issues": errors,
            }
        )
    passed = all(entry["status"] == "passed" for entry in results)
    return {"status": "passed" if passed else "failed", "mode": "remote_result_validation", "results": results}
def _run_remote_command(command: list[str], *, timeout_s: int = 900) -> dict[str, Any]:
    result = _run_process(command, cwd=SKILL_ROOT, timeout_s=timeout_s)
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
def _parallelism_limit(parallelism: int, item_count: int) -> int:
    return max(1, min(max(1, int(parallelism)), max(1, item_count), MAX_REMOTE_PARALLELISM))
def _run_parallel_specs(spec_names: list[str], worker, *, parallelism: int) -> list[dict[str, Any]]:
    if len(spec_names) <= 1 or parallelism <= 1:
        return [worker(spec_name) for spec_name in spec_names]
    max_workers = _parallelism_limit(parallelism, len(spec_names))
    ordered_results: list[dict[str, Any] | None] = [None] * len(spec_names)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hls-remote-review") as executor:
        future_map = {
            executor.submit(worker, spec_name): index
            for index, spec_name in enumerate(spec_names)
        }
        for future in as_completed(future_map):
            index = future_map[future]
            ordered_results[index] = future.result()
    return [item for item in ordered_results if isinstance(item, dict)]
def _run_remote_acceptance(
    server: str,
    readiness: str,
    example_specs: list[str],
    *,
    vitis_version: str | None = None,
    parallelism: int = 1,
) -> dict[str, Any]:
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
        return {
            "status": "failed",
            "server": server,
            "vitis_version": vitis_version,
            "link": link_payload,
            "results": [],
        }
    vitis_results = _run_parallel_specs(
        example_specs,
        lambda spec_name: _run_remote(server, readiness, spec_name, vitis_version=vitis_version),
        parallelism=parallelism,
    )
    passed = link_payload.get("status") == "passed" and all(item.get("status") == "passed" and item.get("remote_artifacts_retained") is True for item in vitis_results)
    return {
        "status": "passed" if passed else "failed",
        "server": server,
        "vitis_version": vitis_version,
        "link": link_payload,
        "results": vitis_results,
    }
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
def _run_split_remote_acceptance(
    build_server: str,
    validate_server: str,
    readiness: str,
    example_specs: list[str],
    *,
    vitis_version: str | None = None,
    parallelism: int = 1,
) -> dict[str, Any]:
    if not example_specs:
        return {
            "status": "failed",
            "topology": "split_build_validate",
            "build_server": build_server,
            "validate_server": validate_server,
            "vitis_version": vitis_version,
            "results": [],
        }
    first_result = _run_split_remote(build_server, validate_server, readiness, example_specs[0], vitis_version=vitis_version)
    if first_result.get("status") != "passed":
        return {
            "status": "failed",
            "topology": "split_build_validate",
            "build_server": build_server,
            "validate_server": validate_server,
            "vitis_version": vitis_version,
            "results": [],
            "first_result": first_result,
        }
    remaining = _run_parallel_specs(
        example_specs[1:],
        lambda spec_name: _run_split_remote(build_server, validate_server, readiness, spec_name, vitis_version=vitis_version),
        parallelism=parallelism,
    )
    results = [first_result, *remaining]
    passed = all(item.get("status") == "passed" and item.get("remote_artifacts_retained") is True for item in results)
    return {
        "status": "passed" if passed else "failed",
        "topology": "split_build_validate",
        "build_server": build_server,
        "validate_server": validate_server,
        "vitis_version": vitis_version,
        "results": results,
    }
def _remote_board_acceptance_gate(
    server: str | None,
    readiness: str,
    *,
    vitis_version: str | None,
    remote_requested: bool,
    remote_vitis_gate: dict[str, Any] | None,
    board_partition: dict[str, Any],
    selected_specs: list[str],
    parallelism: int,
) -> dict[str, Any]:
    invalid_specs = board_partition.get("invalid_specs", [])
    if invalid_specs:
        return {"status": "failed", "reason": "invalid_board_acceptance_metadata", "invalid_specs": invalid_specs}
    board_specs = [entry for entry in board_partition.get("board_specs", []) if entry["spec"] in set(selected_specs)]
    exempt_specs = [entry for entry in board_partition.get("exempt_specs", []) if entry["spec"] in set(selected_specs)]
    if not remote_requested:
        return {
            "status": "passed",
            "mode": "declarations_only",
            "board_specs": board_specs,
            "exempt_specs": exempt_specs,
            "results": [],
        }
    if not server:
        return {"status": "failed", "reason": "board acceptance requires a single remote server", "results": []}
    if not remote_vitis_gate or remote_vitis_gate.get("status") != "passed":
        return {"status": "blocked", "reason": "board acceptance requires successful remote vitis acceptance first", "results": []}
    if not board_specs:
        return {"status": "passed", "mode": "no_board_specs_selected", "board_specs": [], "exempt_specs": exempt_specs, "results": []}
    results = _run_parallel_specs(
        [entry["spec"] for entry in board_specs],
        lambda spec_name: _run_remote_board(server, readiness, spec_name, vitis_version=vitis_version),
        parallelism=parallelism,
    )
    statuses = {str(item.get("status")) for item in results}
    if statuses == {PASS_STATUS}:
        status = "passed"
    elif any(item in statuses for item in {"blocked_board_validation", "blocked_remote_profile_config", "blocked_remote_version_choice"}):
        status = "blocked"
    else:
        status = "failed"
    return {
        "status": status,
        "mode": "remote_board_validation",
        "board_specs": board_specs,
        "exempt_specs": exempt_specs,
        "results": results,
    }
def _remote_family_coverage_gate(results: list[dict[str, Any]], *, coverage_mode: str) -> dict[str, Any]:
    if coverage_mode != "tier1":
        return {
            "status": "skipped",
            "mode": coverage_mode,
            "required_specs": [],
            "vitis_passed_specs": [],
            "board_passed_specs": [],
            "missing_specs": [],
        }
    matrix = _load_tier1_board_matrix()
    families = matrix.get("families", {}) if isinstance(matrix, dict) else {}
    required_specs: list[str] = []
    for config in families.values():
        if not isinstance(config, dict):
            continue
        for key in ("representative", "high_risk"):
            value = str(config.get(key) or "").strip()
            if value and value not in required_specs:
                required_specs.append(value)
    vitis_passed = {
        str(item.get("example_spec") or "")
        for item in results
        if str(item.get("phase") or "") == "vitis" and str(item.get("status") or "") == PASS_STATUS
    }
    board_passed = {
        str(item.get("example_spec") or "")
        for item in results
        if str(item.get("phase") or "") == "board" and str(item.get("status") or "") == PASS_STATUS
    }
    missing_specs = [
        spec_name
        for spec_name in required_specs
        if spec_name not in vitis_passed or spec_name not in board_passed
    ]
    return {
        "status": "passed" if not missing_specs else "failed",
        "mode": str(matrix.get("mode") or coverage_mode),
        "required_specs": required_specs,
        "vitis_passed_specs": sorted(vitis_passed),
        "board_passed_specs": sorted(board_passed),
        "missing_specs": missing_specs,
    }
