"""Runtime configuration loader for the HLS generator skill."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from .skill_dependencies import check_skill_dependencies, find_installed_skill, validate_skill_dependency_config

CONFIG_ENV_VAR = "HLS_GENERATOR_RUNTIME_CONFIG"
_DEFAULT_CONFIG_NAME = "runtime_config.json"


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def repo_root() -> Path:
    return skill_root().parents[1]


def config_path() -> Path:
    raw_override = os.environ.get(CONFIG_ENV_VAR)
    if raw_override:
        candidate = Path(raw_override)
        if not candidate.is_absolute():
            candidate = repo_root() / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(repo_root())
        except ValueError as exc:
            raise ValueError(f"{CONFIG_ENV_VAR} must point inside this repository: {raw_override}") from exc
        return resolved
    return Path(__file__).with_name(_DEFAULT_CONFIG_NAME).resolve()


@lru_cache(maxsize=1)
def _cached_runtime_config() -> dict[str, Any]:
    path = config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"HLS generator runtime config was not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid HLS generator runtime config JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"HLS generator runtime config must be a JSON object: {path}")
    return payload


def runtime_config() -> dict[str, Any]:
    return deepcopy(_cached_runtime_config())


def validate_runtime_config() -> None:
    skill_config_path("default_workflow_config")
    skill_config_path("examples_dir")
    workflow_state_path()
    smoke_root_name()
    generated_roots()
    protected_roots()
    protected_files()
    missing_vitis_tool_id()
    vitis_tools()
    vitis_skill_routing()
    vitis_tcl_config()
    for stage in ("compile", "execute", "implement", "cosim"):
        vitis_tool_timeout(stage)
    remote_validation_config()
    skill_dependencies_config()


def skill_config_path(key: str) -> Path:
    value = _path_config_value(key)
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError(f"Configured path {key!r} must be relative to the skill root: {value}")
    resolved = (skill_root() / candidate).resolve()
    try:
        resolved.relative_to(skill_root())
    except ValueError as exc:
        raise ValueError(f"Configured path {key!r} must stay inside the skill root: {value}") from exc
    return resolved


def workflow_state_path() -> Path:
    value = Path(_path_config_value("workflow_state_file"))
    if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        raise ValueError("Runtime config paths.workflow_state_file must be relative to the workspace root.")
    return value


def smoke_root_name() -> str:
    value = _path_config_value("smoke_root").replace("\\", "/")
    path = Path(value)
    if path.is_absolute() or value.startswith("~/") or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Runtime config paths.smoke_root must be a relative workspace path without traversal.")
    return value


def generated_roots() -> set[str]:
    return _path_name_set("generated_roots")


def protected_roots() -> set[str]:
    return _path_name_set("protected_roots")


def protected_files() -> set[str]:
    return _path_name_set("protected_files")


def protected_write_targets() -> set[str]:
    return protected_roots() | protected_files()


def vitis_tools() -> list[dict[str, Any]]:
    raw_tools = _vitis_config().get("tools", [])
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ValueError("Runtime config vitis.tools must be a non-empty list.")
    tools: list[dict[str, Any]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            raise ValueError("Each Vitis tool config must be a JSON object.")
        name = str(item.get("name") or "").strip()
        command = item.get("command")
        if not name or not isinstance(command, list) or not command:
            raise ValueError("Each Vitis tool config requires name and command list.")
        tools.append(deepcopy(item))
    return tools


def vitis_tool_names() -> tuple[str, ...]:
    return tuple(str(tool["name"]) for tool in vitis_tools())


def vitis_skill_routing() -> dict[str, Any]:
    raw = _vitis_config().get("skill_routing", {})
    if not isinstance(raw, dict):
        raise ValueError("Runtime config vitis.skill_routing must be a JSON object.")
    preferred = str(raw.get("preferred_skill") or "").strip()
    fallbacks = raw.get("fallback_skills", [])
    if not preferred:
        raise ValueError("Runtime config vitis.skill_routing.preferred_skill must be set.")
    if not isinstance(fallbacks, list) or not fallbacks:
        raise ValueError("Runtime config vitis.skill_routing.fallback_skills must be a non-empty list.")
    resolved_fallbacks = [str(item).strip() for item in fallbacks]
    if any(not item for item in resolved_fallbacks):
        raise ValueError("Runtime config vitis.skill_routing.fallback_skills must contain only non-empty strings.")
    return {"preferred_skill": preferred, "fallback_skills": resolved_fallbacks}


def resolve_vitis_skill_preference(
    *,
    skill_dirs: list[Path] | None = None,
    plugin_cache_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    routing = vitis_skill_routing()
    candidates = [routing["preferred_skill"], *routing["fallback_skills"]]
    installed: list[dict[str, Any]] = []
    for name in candidates:
        match = find_installed_skill(name, skill_dirs=skill_dirs, plugin_cache_dirs=plugin_cache_dirs)
        if match and str(match.get("frontmatter_name") or "") == name:
            installed.append(match)
            return {
                "preferred_skill": routing["preferred_skill"],
                "fallback_skills": routing["fallback_skills"],
                "selected_skill": name,
                "status": "ok",
                "installed": installed,
            }
    return {
        "preferred_skill": routing["preferred_skill"],
        "fallback_skills": routing["fallback_skills"],
        "selected_skill": routing["preferred_skill"],
        "status": "missing",
        "installed": installed,
    }


def vitis_blocking_tool_ids() -> set[str]:
    return {missing_vitis_tool_id(), *vitis_tool_names()}


def missing_vitis_tool_id() -> str:
    value = str(_vitis_config().get("missing_tool_id") or "").strip()
    if not value:
        raise ValueError("Runtime config vitis.missing_tool_id must be set.")
    return value


def vitis_command(tool: dict[str, Any], *, tcl: Path) -> list[str]:
    command = tool.get("command")
    if not isinstance(command, list) or not command:
        raise ValueError(f"Vitis tool {tool.get('name')!r} has no command template.")
    replacements = {"tcl": str(tcl)}
    return [str(part).format(**replacements) for part in command]


def vitis_tool_timeout(stage: str) -> int:
    timeouts = _vitis_config().get("timeouts_s", {})
    if not isinstance(timeouts, dict):
        raise ValueError("Runtime config vitis.timeouts_s must be a JSON object.")
    if stage not in timeouts:
        raise ValueError(f"Runtime config vitis.timeouts_s.{stage} must be set.")
    return int(timeouts[stage])


def vitis_tcl_config() -> dict[str, str]:
    raw = _vitis_config().get("tcl", {})
    if not isinstance(raw, dict):
        raise ValueError("Runtime config vitis.tcl must be a JSON object.")
    required = ("temp_tcl_prefix", "project_dir_prefix", "solution_name")
    missing = [key for key in required if not str(raw.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Runtime config vitis.tcl is missing: {', '.join(missing)}")
    return {key: str(raw[key]) for key in required}


def remote_validation_config() -> dict[str, Any]:
    raw = runtime_config().get("remote_validation", {})
    if not isinstance(raw, dict):
        raise ValueError("Runtime config remote_validation must be a JSON object.")
    config = deepcopy(raw)
    config["erie_skill_dir"] = str(_resolve_erie_remote_skill_dir(config))
    config["erie_settings_path"] = str(_resolve_erie_settings_path(config))
    config["local_run_root"] = _remote_local_run_root(str(_remote_required(config, "local_run_root")))
    config["remote_tmp_dir"] = _remote_top_level_name(str(_remote_required(config, "remote_tmp_dir")), "remote_tmp_dir")
    config["default_timeout_s"] = _remote_positive_int(config.get("default_timeout_s"), "default_timeout_s")
    python_env = config.get("python_env", {})
    if not isinstance(python_env, dict) or not python_env:
        raise ValueError("Runtime config remote_validation.python_env must be a non-empty object.")
    config["python_env"] = {str(key): str(value) for key, value in python_env.items()}
    link_probe = config.get("link_probe_command", [])
    if not isinstance(link_probe, list) or not link_probe or not all(isinstance(item, str) and item for item in link_probe):
        raise ValueError("Runtime config remote_validation.link_probe_command must be a non-empty list of strings.")
    profiles = config.get("vitis_profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("Runtime config remote_validation.vitis_profiles must be a JSON object when set.")
    for name, profile in profiles.items():
        if not str(name).strip() or not isinstance(profile, dict):
            raise ValueError("Each remote Vitis profile must be a named JSON object.")
    config["directory_contract"] = _remote_directory_contract(config)
    return config


def skill_dependencies_config() -> list[dict[str, Any]]:
    return validate_skill_dependency_config(runtime_config().get("skill_dependencies", []))


def _resolve_erie_remote_skill_dir(config: dict[str, Any]) -> Path:
    configured = _expand_remote_value(_remote_required(config, "erie_skill_dir"))
    if os.environ.get("HLS_GENERATOR_SKILLS_DIRS") is not None or os.environ.get("CODEX_HOME"):
        discovered = _discover_erie_remote_skill_dir()
        if discovered is not None:
            return discovered
    configured_settings = _erie_settings_candidates(config, configured)
    if (configured / "scripts" / "remote_ssh.py").exists() and any(path.exists() for path in configured_settings):
        return configured
    discovered = _discover_erie_remote_skill_dir()
    if discovered is not None:
        return discovered
    return configured


def _discover_erie_remote_skill_dir() -> Path | None:
    for dependency in skill_dependencies_config():
        if dependency["id"] != "remote-ssh":
            continue
        report = check_skill_dependencies([dependency])
        item = report["dependencies"][0] if report["dependencies"] else {}
        if item.get("status") == "ok" and item.get("installed"):
            return Path(item["installed"][0]["path"]).resolve()
    return None


def _resolve_erie_settings_path(config: dict[str, Any]) -> Path:
    candidates = _erie_settings_candidates(config, Path(str(config["erie_skill_dir"])))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _erie_settings_candidates(config: dict[str, Any], erie_skill_dir: Path) -> list[Path]:
    configured = _expand_remote_value(_remote_required(config, "erie_settings_path"), {"erie_skill_dir": str(erie_skill_dir)})
    candidates = [
        configured,
        (erie_skill_dir / "assets" / "defaults.json").resolve(),
        (erie_skill_dir / "config" / "defaults.json").resolve(),
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _path_config_value(key: str) -> str:
    paths = runtime_config().get("paths", {})
    if not isinstance(paths, dict):
        raise ValueError("Runtime config paths must be a JSON object.")
    value = str(paths.get(key) or "").strip()
    if not value:
        raise ValueError(f"Runtime config paths.{key} must be set.")
    return value


def _path_name_set(key: str) -> set[str]:
    paths = runtime_config().get("paths", {})
    raw = paths.get(key) if isinstance(paths, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"Runtime config paths.{key} must be a non-empty list.")
    values: set[str] = set()
    for item in raw:
        value = str(item).strip().replace("\\", "/")
        if not value or "/" in value or value in {".", ".."}:
            raise ValueError(f"Runtime config paths.{key} entries must be top-level names: {item!r}")
        values.add(value)
    return values


def _vitis_config() -> dict[str, Any]:
    value = runtime_config().get("vitis", {})
    if not isinstance(value, dict):
        raise ValueError("Runtime config vitis must be a JSON object.")
    return value


def _remote_required(config: dict[str, Any], key: str) -> str:
    value = str(config.get(key) or "").strip()
    if not value:
        raise ValueError(f"Runtime config remote_validation.{key} must be set.")
    return value


def _expand_remote_value(value: str, extra: dict[str, str] | None = None) -> Path:
    replacements = {
        "home": str(Path.home()),
        "skill_root": str(skill_root()),
    }
    if extra:
        replacements.update(extra)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key.startswith("env:"):
            return os.environ.get(key[4:], "")
        return replacements.get(key, match.group(0))

    return Path(os.path.expandvars(os.path.expanduser(re.sub(r"\$\{([^}]+)\}", replace, value)))).resolve()


def _remote_local_run_root(value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Runtime config remote_validation.local_run_root must be a relative path inside a generated root.")
    first = candidate.parts[0] if candidate.parts else ""
    if first not in generated_roots():
        raise ValueError(f"Runtime config remote_validation.local_run_root must start with one of: {', '.join(sorted(generated_roots()))}.")
    return value.replace("\\", "/")


def _remote_top_level_name(value: str, key: str) -> str:
    normalized = value.replace("\\", "/")
    if "/" in normalized or normalized in {"", ".", ".."}:
        raise ValueError(f"Runtime config remote_validation.{key} must be a top-level relative directory name.")
    return normalized


def _remote_positive_int(value: Any, key: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Runtime config remote_validation.{key} must be a positive integer.") from exc
    if parsed < 1:
        raise ValueError(f"Runtime config remote_validation.{key} must be a positive integer.")
    return parsed


def _remote_directory_contract(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("directory_contract", {})
    if not isinstance(raw, dict):
        raise ValueError("Runtime config remote_validation.directory_contract must be a JSON object.")
    project_root_dirname = _remote_top_level_name(str(raw.get("project_root_dirname") or "").strip(), "directory_contract.project_root_dirname")
    conda_prefix_path = _remote_relative_path(str(raw.get("conda_prefix_path") or "").strip(), "directory_contract.conda_prefix_path")
    active_run_path_template = _remote_relative_path(
        str(raw.get("active_run_path_template") or "").strip(),
        "directory_contract.active_run_path_template",
        require_run_id=True,
    )
    backup_run_path_template = _remote_relative_path(
        str(raw.get("backup_run_path_template") or "").strip(),
        "directory_contract.backup_run_path_template",
        require_run_id=True,
    )
    platform_root_path_template = _remote_relative_path(
        str(raw.get("platform_root_path_template") or "").strip(),
        "directory_contract.platform_root_path_template",
        require_platform_name=True,
    )
    archive_after_verification = raw.get("archive_after_verification")
    if not isinstance(archive_after_verification, bool):
        raise ValueError("Runtime config remote_validation.directory_contract.archive_after_verification must be a boolean.")
    archive_trigger = str(raw.get("archive_trigger") or "").strip()
    if not archive_trigger:
        raise ValueError("Runtime config remote_validation.directory_contract.archive_trigger must be set.")
    return {
        "project_root_dirname": project_root_dirname,
        "conda_prefix_path": conda_prefix_path,
        "active_run_path_template": active_run_path_template,
        "backup_run_path_template": backup_run_path_template,
        "platform_root_path_template": platform_root_path_template,
        "archive_after_verification": archive_after_verification,
        "archive_trigger": archive_trigger,
    }


def _remote_relative_path(value: str, key: str, *, require_run_id: bool = False, require_platform_name: bool = False) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized:
        raise ValueError(f"Runtime config remote_validation.{key} must be set.")
    path = Path(normalized)
    if path.is_absolute() or normalized.startswith("~/") or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Runtime config remote_validation.{key} must be a relative POSIX path.")
    if require_run_id and "<run-id>" not in normalized:
        raise ValueError(f"Runtime config remote_validation.{key} must include <run-id>.")
    if require_platform_name and "<platform-name>" not in normalized:
        raise ValueError(f"Runtime config remote_validation.{key} must include <platform-name>.")
    return normalized
