"""Skill dependency discovery, blocking requests, and installation helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


DEPENDENCY_OK = "ok"
DEPENDENCY_MISSING = "missing"
DEPENDENCY_INVALID = "invalid"
BLOCKED_DEPENDENCY = "blocked_dependency"


class SkillDependencyError(RuntimeError):
    """Raised when blocking skill dependencies are missing or invalid."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(_format_blocked_error(report))


def check_skill_dependencies(
    dependencies: list[dict[str, Any]] | None,
    *,
    skill_dirs: list[Path] | None = None,
    plugin_cache_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Return a machine-readable dependency status report."""

    entries = [_normalize_dependency(item) for item in dependencies or []]
    index = _discover_installed_skills(skill_dirs=skill_dirs, plugin_cache_dirs=plugin_cache_dirs)
    reports = [_check_one(entry, index) for entry in entries]
    blocked = [item for item in reports if item["blocking"] and item["status"] != DEPENDENCY_OK]
    return {
        "version": 1,
        "status": BLOCKED_DEPENDENCY if blocked else DEPENDENCY_OK,
        "skills_dirs": [str(path) for path in _default_skill_dirs() if skill_dirs is None] if skill_dirs is None else [str(path) for path in skill_dirs],
        "plugin_cache_dirs": [str(path) for path in _default_plugin_cache_dirs() if plugin_cache_dirs is None] if plugin_cache_dirs is None else [str(path) for path in plugin_cache_dirs],
        "dependencies": reports,
    }


def find_installed_skill(
    name: str,
    *,
    aliases: list[str] | tuple[str, ...] | set[str] | None = None,
    skill_dirs: list[Path] | None = None,
    plugin_cache_dirs: list[Path] | None = None,
) -> dict[str, Any] | None:
    index = _discover_installed_skills(skill_dirs=skill_dirs, plugin_cache_dirs=plugin_cache_dirs)
    return _find_skill(name, set(aliases or []), index)


def require_skill_dependencies(dependencies: list[dict[str, Any]] | None) -> dict[str, Any]:
    report = check_skill_dependencies(dependencies)
    if report["status"] == BLOCKED_DEPENDENCY:
        raise SkillDependencyError(report)
    return report


def build_dependency_request(report: dict[str, Any]) -> dict[str, Any]:
    blocked = [item for item in report.get("dependencies", []) if item.get("blocking") and item.get("status") != DEPENDENCY_OK]
    return {
        "version": 1,
        "action": "ask_install_skill_dependencies",
        "status": BLOCKED_DEPENDENCY if blocked else DEPENDENCY_OK,
        "question": "Required HLSGenerator skill dependencies are missing or invalid. Ask the user whether to install or repair them before continuing.",
        "missing_or_invalid": blocked,
        "recommended_commands": [
            "python -m runtime.hls_generator deps check --json",
            "python -m runtime.hls_generator deps request --out reports/skill_dependency_request.json",
            "python -m runtime.hls_generator deps install --all",
        ],
        "restart_required": "Restart Codex after installing new skills so trigger metadata is reloaded.",
        "policy": "All configured required and recommended dependencies are blocking for this skill.",
    }


def install_skill_dependencies(
    dependencies: list[dict[str, Any]],
    *,
    ids: list[str] | None = None,
    install_all: bool = False,
    dest_root: Path | None = None,
    skill_dirs: list[Path] | None = None,
    plugin_cache_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    if not install_all and not ids:
        raise ValueError("Dependency installation requires --all or at least one --ids value.")
    requested = set(ids or [])
    normalized = [_normalize_dependency(item) for item in dependencies]
    selected = [item for item in normalized if install_all or item["id"] in requested]
    if not selected:
        raise ValueError("No matching skill dependencies were selected for installation.")
    destination = (dest_root or _default_install_root()).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report = check_skill_dependencies(normalized, skill_dirs=skill_dirs, plugin_cache_dirs=plugin_cache_dirs)
    report_by_id = {item["id"]: item for item in report["dependencies"]}
    skipped: list[dict[str, Any]] = []
    repair_required: list[dict[str, Any]] = []
    to_install: list[tuple[dict[str, Any], list[str]]] = []
    for entry in selected:
        item = report_by_id.get(entry["id"])
        if item and item["status"] == DEPENDENCY_OK:
            skipped.append({"id": entry["id"], "reason": "already_installed"})
        elif item and item["status"] == DEPENDENCY_INVALID:
            repair_required.append({"id": entry["id"], "reason": "installed_dependency_invalid", "invalid": item.get("invalid", []), "installed": item.get("installed", [])})
        else:
            missing = item.get("missing", entry["expected_skill_names"]) if item else entry["expected_skill_names"]
            to_install.append((entry, list(missing)))
    results = [_install_one(entry, destination, missing_names) for entry, missing_names in to_install]
    install_skipped = [
        {"dependency_id": item["id"], **satisfied}
        for item in report["dependencies"]
        if item["id"] in {entry["id"] for entry in selected}
        for satisfied in item.get("satisfied_by", [])
    ]
    if repair_required:
        status = "repair_required"
    else:
        status = DEPENDENCY_OK if all(item["status"] == DEPENDENCY_OK for item in results) else "failed"
    return {
        "version": 1,
        "status": status,
        "destination": str(destination),
        "installed": results,
        "skipped": skipped,
        "install_skipped": install_skipped,
        "repair_required": repair_required,
        "restart_required": True,
        "next_step": "Restart Codex to pick up newly installed skills.",
    }


def validate_skill_dependency_config(dependencies: Any) -> list[dict[str, Any]]:
    if dependencies is None:
        return []
    if not isinstance(dependencies, list):
        raise ValueError("Runtime config skill_dependencies must be a list.")
    return [_normalize_dependency(item) for item in dependencies]


def format_dependency_report(report: dict[str, Any]) -> str:
    lines = [f"Skill dependency status: {report.get('status')}"]
    for item in report.get("dependencies", []):
        detail = f"- {item['id']}: {item['status']} ({item['level']})"
        if item.get("missing"):
            detail += f"; missing={', '.join(item['missing'])}"
        if item.get("satisfied_by"):
            detail += "; satisfied_by=" + ", ".join(f"{entry['name']}->{entry['provider']['frontmatter_name']}" for entry in item["satisfied_by"])
        if item.get("invalid"):
            detail += f"; invalid={', '.join(item['invalid'])}"
        lines.append(detail)
    return "\n".join(lines)


def _check_one(entry: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    expected = list(entry["expected_skill_names"])
    aliases = set(entry.get("aliases", []))
    allowed_names = set(expected) | aliases
    installed: list[dict[str, Any]] = []
    satisfied_by: list[dict[str, Any]] = []
    missing: list[str] = []
    invalid: list[str] = []
    for name in expected:
        match = _find_skill(name, aliases, index)
        if not match:
            alternative = _find_alternative_provider(name, entry.get("alternative_providers", []), index)
            if alternative:
                satisfied_by.append(alternative)
                continue
        if not match:
            missing.append(name)
            continue
        installed.append(match)
        frontmatter_name = str(match.get("frontmatter_name") or "")
        if frontmatter_name not in allowed_names:
            invalid.append(f"{name}:frontmatter_name={frontmatter_name}")
        missing_files = _missing_required_files(match["path"], entry.get("required_files", []))
        invalid.extend(f"{name}:{item}" for item in missing_files)
    status = DEPENDENCY_OK
    if missing:
        status = DEPENDENCY_MISSING
    if invalid:
        status = DEPENDENCY_INVALID
    return {
        "id": entry["id"],
        "level": entry["level"],
        "purpose": entry["purpose"],
        "repo_url": entry["repo_url"],
        "ref": entry["ref"],
        "paths": entry["paths"],
        "destination_names": entry["destination_names"],
        "expected_skill_names": expected,
        "aliases": sorted(aliases),
        "adapter": entry["adapter"],
        "blocking": entry["blocking"],
        "status": status,
        "installed": installed,
        "satisfied_by": satisfied_by,
        "missing": missing,
        "invalid": invalid,
    }


def _find_alternative_provider(name: str, providers: list[dict[str, Any]], index: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    for provider in providers:
        if provider["for"] != name:
            continue
        allowed_names = set(provider["skill_names"]) | set(provider["aliases"])
        for skill_name in provider["skill_names"]:
            match = _find_skill(skill_name, set(provider["aliases"]), index)
            if not match:
                continue
            if str(match.get("frontmatter_name") or "") not in allowed_names:
                continue
            return {
                "name": name,
                "provider": match,
                "install_policy": provider["install_policy"],
                "purpose": provider["purpose"],
            }
    return None


def _find_skill(name: str, aliases: set[str], index: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    candidates = index.get(name, [])
    if not candidates:
        for alias in aliases:
            candidates.extend(index.get(alias, []))
    if not candidates:
        return None
    return deepcopy(candidates[0])


def _missing_required_files(skill_path: str, required_files: list[str]) -> list[str]:
    root = Path(skill_path)
    missing: list[str] = []
    for rel in required_files:
        if not (root / rel).exists():
            missing.append(rel)
    return missing


def _discover_installed_skills(*, skill_dirs: list[Path] | None, plugin_cache_dirs: list[Path] | None) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for root in skill_dirs if skill_dirs is not None else _default_skill_dirs():
        _index_skill_root(root, index, recursive=False, source="skills")
    for root in plugin_cache_dirs if plugin_cache_dirs is not None else _default_plugin_cache_dirs():
        _index_skill_root(root, index, recursive=True, source="plugin-cache")
    return index


def _index_skill_root(root: Path, index: dict[str, list[dict[str, Any]]], *, recursive: bool, source: str) -> None:
    if not root.exists():
        return
    skill_files = list(root.rglob("SKILL.md")) if recursive else _direct_skill_files(root)
    for skill_file in skill_files:
        frontmatter = _read_skill_frontmatter(skill_file)
        name = str(frontmatter.get("name") or skill_file.parent.name).strip()
        record = {
            "name": skill_file.parent.name,
            "frontmatter_name": name,
            "path": str(skill_file.parent.resolve()),
            "skill_file": str(skill_file.resolve()),
            "source": source,
        }
        for key in {skill_file.parent.name, name}:
            index.setdefault(key, []).append(record)


def _direct_skill_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if (root / "SKILL.md").exists():
        files.append(root / "SKILL.md")
    for child in root.iterdir():
        if child.is_dir() and (child / "SKILL.md").exists():
            files.append(child / "SKILL.md")
    return files


def _read_skill_frontmatter(skill_file: Path) -> dict[str, str]:
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip("'\"")
    return data


def _install_one(entry: dict[str, Any], destination: Path, missing_names: list[str]) -> dict[str, Any]:
    installed_paths: list[str] = []
    selected = _install_selections(entry, missing_names)
    for _, dest_name in selected:
        target = destination / dest_name
        if target.exists():
            raise ValueError(f"Skill destination already exists and will not be overwritten: {target}")
    with tempfile.TemporaryDirectory(prefix="hls-skill-deps-") as tmp:
        checkout = Path(tmp) / "repo"
        _run(["git", "clone", "--depth", "1", "--branch", entry["ref"], entry["repo_url"], str(checkout)])
        sources: list[tuple[Path, str]] = []
        for repo_path, dest_name in selected:
            source = checkout if repo_path == "." else checkout / repo_path
            if not (source / "SKILL.md").exists():
                raise ValueError(f"Dependency {entry['id']} path {repo_path!r} does not contain SKILL.md.")
            sources.append((source, dest_name))
        for source, dest_name in sources:
            target = destination / dest_name
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            installed_paths.append(str(target))
    return {"id": entry["id"], "status": DEPENDENCY_OK, "installed_paths": installed_paths}


def _install_selections(entry: dict[str, Any], missing_names: list[str]) -> list[tuple[str, str]]:
    missing = set(missing_names)
    selections: list[tuple[str, str]] = []
    for expected, repo_path, dest_name in zip(entry["expected_skill_names"], entry["paths"], entry["destination_names"], strict=True):
        if expected in missing:
            selections.append((repo_path, dest_name))
    if not selections and missing:
        raise ValueError(f"Dependency {entry['id']} has missing skills not mapped to install paths: {', '.join(sorted(missing))}")
    return selections


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise ValueError(f"Command failed: {' '.join(command)}\n{result.stdout}{result.stderr}")


def _normalize_dependency(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Each skill dependency must be a JSON object.")
    required = ("id", "level", "purpose", "repo_url", "ref", "paths", "expected_skill_names", "destination_names", "aliases", "adapter", "blocking")
    missing = [key for key in required if key not in item]
    if missing:
        raise ValueError(f"Skill dependency is missing required fields: {', '.join(missing)}")
    paths = _string_list(item["paths"], "paths")
    expected = _string_list(item["expected_skill_names"], "expected_skill_names")
    destinations = _string_list(item["destination_names"], "destination_names")
    if len(paths) != len(destinations):
        raise ValueError(f"Skill dependency {item.get('id')!r} paths and destination_names must have the same length.")
    if len(expected) != len(paths):
        raise ValueError(f"Skill dependency {item.get('id')!r} expected_skill_names and paths must have the same length.")
    if not expected:
        raise ValueError(f"Skill dependency {item.get('id')!r} must list expected_skill_names.")
    normalized = {
        "id": _non_empty_string(item["id"], "id"),
        "level": _non_empty_string(item["level"], "level"),
        "purpose": _non_empty_string(item["purpose"], "purpose"),
        "repo_url": _non_empty_string(item["repo_url"], "repo_url"),
        "ref": _non_empty_string(item["ref"], "ref"),
        "paths": paths,
        "expected_skill_names": expected,
        "destination_names": destinations,
        "aliases": _string_list(item["aliases"], "aliases", allow_empty=True),
        "adapter": _non_empty_string(item["adapter"], "adapter"),
        "blocking": bool(item["blocking"]),
        "required_files": _string_list(item.get("required_files", []), "required_files", allow_empty=True),
        "alternative_providers": _normalize_alternative_providers(item.get("alternative_providers", []), expected),
    }
    if normalized["level"] not in {"required", "recommended"}:
        raise ValueError(f"Skill dependency {normalized['id']!r} level must be required or recommended.")
    return normalized


def _normalize_alternative_providers(value: Any, expected: list[str]) -> list[dict[str, Any]]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise ValueError("Skill dependency alternative_providers must be a list.")
    result: list[dict[str, Any]] = []
    expected_set = set(expected)
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each skill dependency alternative provider must be a JSON object.")
        target = _non_empty_string(item.get("for"), "alternative_providers.for")
        if target not in expected_set:
            raise ValueError(f"Alternative provider target {target!r} is not listed in expected_skill_names.")
        result.append(
            {
                "for": target,
                "skill_names": _string_list(item.get("skill_names"), "alternative_providers.skill_names"),
                "aliases": _string_list(item.get("aliases", []), "alternative_providers.aliases", allow_empty=True),
                "install_policy": str(item.get("install_policy") or "skip_if_present").strip() or "skip_if_present",
                "purpose": str(item.get("purpose") or "").strip(),
            }
        )
    return result


def _string_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"Skill dependency {name} must be a list.")
    result = [str(item).strip() for item in value]
    if any(not item for item in result):
        raise ValueError(f"Skill dependency {name} must contain only non-empty strings.")
    return result


def _non_empty_string(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Skill dependency {name} must be set.")
    return text


def _default_install_root() -> Path:
    override = _path_list_env("HLS_GENERATOR_SKILLS_DIRS")
    if override:
        return override[0]
    codex_home = os.environ.get("CODEX_HOME")
    return (Path(codex_home) if codex_home else Path.home() / ".codex") / "skills"


def _default_skill_dirs() -> list[Path]:
    override = _path_list_env("HLS_GENERATOR_SKILLS_DIRS")
    if override is not None:
        return override
    roots: list[Path] = []
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        roots.append(Path(codex_home) / "skills")
    roots.append(Path.home() / ".codex" / "skills")
    return _dedupe_paths(roots)


def _default_plugin_cache_dirs() -> list[Path]:
    override = _path_list_env("HLS_GENERATOR_PLUGIN_CACHE_DIRS")
    if override is not None:
        return override
    roots: list[Path] = []
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        roots.append(Path(codex_home) / "plugins" / "cache")
    roots.append(Path.home() / ".codex" / "plugins" / "cache")
    return _dedupe_paths(roots)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        resolved = str(path.expanduser().resolve())
        if resolved not in seen:
            seen.add(resolved)
            result.append(Path(resolved))
    return result


def _path_list_env(name: str) -> list[Path] | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    if not raw.strip():
        return []
    return _dedupe_paths([Path(item) for item in raw.split(os.pathsep) if item.strip()])


def _format_blocked_error(report: dict[str, Any]) -> str:
    request = build_dependency_request(report)
    return json.dumps(request, indent=2, ensure_ascii=False)
