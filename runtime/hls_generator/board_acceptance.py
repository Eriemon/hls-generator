"""Board-acceptance metadata helpers for shipped HLS examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


BOARD_RUNNABLE_PROFILE = "u55c_m_axi_host"
NON_BOARD_RUNNABLE_PROFILE = "not_board_runnable"
HOST_TEMPLATE_FILENAMES = {
    "vector_scale_host": "vector_scale_host.cpp.tpl",
    "vector_increment_host": "vector_increment_host.cpp.tpl",
    "binary_add_host": "binary_add_host.cpp.tpl",
    "matrix_unary_host": "matrix_unary_host.cpp.tpl",
    "unary_memory_host": "unary_memory_host.cpp.tpl",
    "binary_memory_host": "binary_memory_host.cpp.tpl",
    "matrix_memory_host": "matrix_memory_host.cpp.tpl",
    "wrapper_unary_memory_host": "wrapper_unary_memory_host.cpp.tpl",
}


def board_acceptance_config(spec: dict[str, Any]) -> dict[str, Any]:
    workflow = spec.get("workflow") if isinstance(spec.get("workflow"), dict) else {}
    config = workflow.get("board_acceptance") if isinstance(workflow.get("board_acceptance"), dict) else {}
    return dict(config)


def board_acceptance_profile(spec: dict[str, Any]) -> str:
    return str(board_acceptance_config(spec).get("profile") or "").strip()


def board_acceptance_target_family(spec: dict[str, Any]) -> str:
    config = board_acceptance_config(spec)
    return str(config.get("target_family") or "").strip()


def is_board_runnable(spec: dict[str, Any]) -> bool:
    return board_acceptance_profile(spec) == BOARD_RUNNABLE_PROFILE


def validate_board_acceptance_config(spec: dict[str, Any]) -> list[str]:
    config = board_acceptance_config(spec)
    profile = str(config.get("profile") or "").strip()
    reason = str(config.get("reason") or "").strip()
    errors: list[str] = []
    if not profile:
        errors.append("workflow.board_acceptance.profile is required")
        return errors
    if profile not in {BOARD_RUNNABLE_PROFILE, NON_BOARD_RUNNABLE_PROFILE}:
        errors.append(f"workflow.board_acceptance.profile must be {BOARD_RUNNABLE_PROFILE!r} or {NON_BOARD_RUNNABLE_PROFILE!r}")
    if profile == NON_BOARD_RUNNABLE_PROFILE and not reason:
        errors.append("workflow.board_acceptance.reason is required when profile is not_board_runnable")
    if profile == BOARD_RUNNABLE_PROFILE and not str(config.get("host_template") or "").strip():
        errors.append("workflow.board_acceptance.host_template is required for board-runnable examples")
    return errors


def partition_example_specs_by_board_acceptance(examples_dir: Path) -> dict[str, Any]:
    board_specs: list[dict[str, Any]] = []
    exempt_specs: list[dict[str, Any]] = []
    invalid_specs: list[dict[str, Any]] = []
    for path in sorted(examples_dir.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if str(spec.get("target") or "").strip().lower() != "hls":
            continue
        config = board_acceptance_config(spec)
        profile = board_acceptance_profile(spec)
        errors = validate_board_acceptance_config(spec)
        entry = {
            "spec": path.name,
            "profile": profile,
            "reason": str(config.get("reason") or "").strip(),
            "host_template": str(config.get("host_template") or "").strip(),
        }
        if errors:
            invalid_specs.append({**entry, "issues": errors})
            continue
        if profile == BOARD_RUNNABLE_PROFILE:
            board_specs.append(entry)
        else:
            exempt_specs.append(entry)
    return {
        "board_specs": board_specs,
        "exempt_specs": exempt_specs,
        "invalid_specs": invalid_specs,
    }


def resolve_host_template_path(skill_root: Path, template_name: str) -> Path:
    filename = HOST_TEMPLATE_FILENAMES.get(template_name)
    if not filename:
        raise ValueError(f"Unsupported board host template {template_name!r}.")
    path = skill_root / "assets" / "validation-board" / "hosts" / filename
    if not path.exists():
        raise ValueError(f"Board host template does not exist: {path}")
    return path
