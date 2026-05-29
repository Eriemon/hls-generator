#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[3]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.hls_generator import __version__  # noqa: E402
from integration.hls_adapter import run_hls_workflow, validate_hls_artifacts  # noqa: E402
from runtime.hls_generator.board_acceptance import partition_example_specs_by_board_acceptance  # noqa: E402
from runtime.hls_generator.config import generated_roots, repo_root as configured_repo_root, skill_config_path, skill_dependencies_config  # noqa: E402
from runtime.hls_generator.skill_dependencies import check_skill_dependencies  # noqa: E402

FORBIDDEN_REFERENCE_TERMS = ("vitis-hls-introductory-examples",)
COPYRIGHT_TERM_PARTS = (("off", "icial"), ("tuto", "rials"), ("Vitis-", "Tuto", "rials"), ("UG", "1399"))
TEXT_SCAN_EXTENSIONS = {".md", ".py", ".json", ".yaml", ".yml", ".txt"}
SKIP_SCAN_DIRS = {".git", "__pycache__", ".pytest_cache", "reports", "tests", "smoke", *generated_roots()}
RELEASE_SENSITIVITY_PATTERNS = (
    re.compile(re.escape("/tools/Xilinx/"), re.IGNORECASE),
    re.compile(re.escape("".join(["C", ":", "\\", "Users", "\\"])), re.IGNORECASE),
    re.compile(re.escape("server_list.local.json"), re.IGNORECASE),
    re.compile(re.escape("xcu50-fsvh2104-2-e"), re.IGNORECASE),
)
RELEASE_SENSITIVITY_EXEMPT_REL_PATHS = {
    "scripts/python/remote/remote_acceptance_common.py",
    "scripts/python/remote/remote_acceptance_vitis.py",
    "scripts/python/remote/remote_vitis_acceptance.py",
    "scripts/python/validation/confidence_local.py",
    "smoke/run_smoke.py",
    "tests/test_remote_vitis_acceptance.py",
    "tests/test_user_config.py",
}
PASS_STATUS = "passed"
REMOTE_SMOKE_SPEC = "hls_host_kernel_split_spec.json"


def repo_root() -> Path:
    return configured_repo_root()

def _run_command(command: list[str], *, cwd: Path, timeout_s: int = 900) -> dict[str, Any]:
    result = _run_process(command, cwd=cwd, timeout_s=timeout_s)
    if result["timed_out"]:
        return {
            "status": "timeout",
            "command": command,
            "returncode": None,
            "timeout_s": timeout_s,
            "stdout_tail": _tail(result["stdout"]),
            "stderr_tail": _tail(result["stderr"]),
        }
    return {
        "status": "passed" if result["returncode"] == 0 else "failed",
        "command": command,
        "returncode": result["returncode"],
        "timeout_s": timeout_s,
        "stdout_tail": _tail(result["stdout"]),
        "stderr_tail": _tail(result["stderr"]),
    }
def _run_process(command: list[str], *, cwd: Path, timeout_s: int) -> dict[str, Any]:
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        return {"timed_out": False, "returncode": process.returncode, "stdout": stdout or "", "stderr": stderr or ""}
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return {"timed_out": True, "returncode": None, "stdout": stdout or "", "stderr": stderr or ""}
def _terminate_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, check=False)
        return
    try:
        os.killpg(pid, 9)
    except ProcessLookupError:
        return
def _quick_validate_path() -> Path:
    return Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"
def _skill_dependency_gate() -> dict[str, Any]:
    try:
        dependencies = skill_dependencies_config()
        report = check_skill_dependencies(dependencies, scopes={"core"})
    except ValueError as exc:
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "passed" if report["status"] == "ok" else "failed",
        "dependency_count": len(dependencies),
        "report": report,
    }
def _copyright_term_scan(*, root: Path | None = None) -> dict[str, Any]:
    scan_root = (root or SKILL_ROOT).resolve()
    matches: list[str] = []
    term_patterns = [(term, re.compile(re.escape(term), re.IGNORECASE)) for term in _copyright_terms()]
    for path in _iter_scan_paths(scan_root):
        if path != scan_root and any(part in SKIP_SCAN_DIRS for part in path.relative_to(scan_root).parts):
            continue
        if path != scan_root:
            for term, pattern in term_patterns:
                if pattern.search(path.relative_to(scan_root).as_posix()):
                    matches.append(f"path:{path.relative_to(scan_root).as_posix()}:{term}")
        try:
            is_file = path.is_file()
        except FileNotFoundError:
            continue
        if not is_file or path.suffix.lower() not in TEXT_SCAN_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        for term, pattern in term_patterns:
            if pattern.search(text):
                matches.append(f"content:{path.relative_to(scan_root).as_posix()}:{term}")
    return {
        "status": "passed" if not matches else "failed",
        "root": str(scan_root),
        "matches": matches,
    }
def _forbidden_reference_name_scan() -> dict[str, Any]:
    result = subprocess.run(
        ["rg", "-n", *sum((["--glob", item] for item in _scan_exclude_globs()), []), "|".join(FORBIDDEN_REFERENCE_TERMS), "."],
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    unexpected = [line for line in lines if not line.split(":", 1)[0].replace("\\", "/").startswith("ref/")]
    return {
        "status": "passed" if result.returncode in {0, 1} and not unexpected else "failed",
        "command": ["rg", "-n", *sum((["--glob", item] for item in _scan_exclude_globs()), []), "|".join(FORBIDDEN_REFERENCE_TERMS), "."],
        "matches": lines,
        "unexpected_matches": unexpected,
    }
def _release_sensitivity_scan(*, root: Path | None = None) -> dict[str, Any]:
    scan_root = (root or SKILL_ROOT).resolve()
    roots = [scan_root]
    if root is None:
        release_dir = repo_root() / "dist" / f"erie-hls-generator-v{__version__}"
        release_zip = repo_root() / "dist" / f"erie-hls-generator-v{__version__}.zip"
        if release_dir.exists():
            roots.append(release_dir)
        if release_zip.exists():
            roots.append(release_zip)
    matches: list[str] = []
    for active_root in roots:
        if active_root.is_file() and active_root.suffix.lower() == ".zip":
            matches.extend(_scan_release_zip(active_root))
            continue
        for path in _iter_scan_paths(active_root):
            if path != active_root and any(part in SKIP_SCAN_DIRS for part in path.relative_to(active_root).parts):
                continue
            rel_path = path.relative_to(active_root).as_posix() if path != active_root else "."
            for pattern in RELEASE_SENSITIVITY_PATTERNS:
                if pattern.search(rel_path):
                    matches.append(f"path:{active_root.name}:{rel_path}:{pattern.pattern}")
            try:
                is_file = path.is_file()
            except FileNotFoundError:
                continue
            if not is_file or path.suffix.lower() not in TEXT_SCAN_EXTENSIONS:
                continue
            if _release_sensitivity_is_exempt(rel_path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                continue
            for pattern in RELEASE_SENSITIVITY_PATTERNS:
                if pattern.search(text):
                    matches.append(f"content:{active_root.name}:{rel_path}:{pattern.pattern}")
    return {
        "status": "passed" if not matches else "failed",
        "roots": [str(item) for item in roots],
        "matches": matches,
    }
def _scan_release_zip(archive_path: Path) -> list[str]:
    matches: list[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            rel_name = name.rstrip("/")
            if not rel_name:
                continue
            rel_path = rel_name.replace("\\", "/")
            for pattern in RELEASE_SENSITIVITY_PATTERNS:
                if pattern.search(rel_path):
                    matches.append(f"path:{archive_path.name}:{rel_path}:{pattern.pattern}")
            if Path(rel_path).suffix.lower() not in TEXT_SCAN_EXTENSIONS or rel_name.endswith("/"):
                continue
            if _release_sensitivity_is_exempt(rel_path):
                continue
            text = archive.read(name).decode("utf-8", errors="replace")
            for pattern in RELEASE_SENSITIVITY_PATTERNS:
                if pattern.search(text):
                    matches.append(f"content:{archive_path.name}:{rel_path}:{pattern.pattern}")
    return matches
def _iter_scan_paths(root: Path):
    yield root
    for current_root, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda _exc: None):
        current_path = Path(current_root)
        dirnames[:] = [name for name in dirnames if name not in SKIP_SCAN_DIRS]
        for dirname in dirnames:
            yield current_path / dirname
        for filename in filenames:
            yield current_path / filename
def _release_sensitivity_is_exempt(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lstrip("./")
    skill_marker = f"/{SKILL_ROOT.relative_to(repo_root()).as_posix().rstrip('/')}/"
    if skill_marker in f"/{normalized}":
        normalized = f"/{normalized}".split(skill_marker, 1)[1]
    return normalized in RELEASE_SENSITIVITY_EXEMPT_REL_PATHS
def _relative_match_path(line: str) -> str:
    path_text = line.split(":", 1)[0].replace("\\", "/")
    if path_text.startswith("./"):
        path_text = path_text[2:]
    root = SKILL_ROOT.as_posix().rstrip("/") + "/"
    if path_text.startswith(root):
        return path_text[len(root) :]
    marker = SKILL_ROOT.relative_to(repo_root()).as_posix().rstrip("/") + "/"
    if marker in path_text:
        return path_text.split(marker, 1)[1]
    return path_text
def _validate_examples(run_root: Path) -> tuple[dict[str, Any], list[str]]:
    examples_dir = skill_config_path("examples_dir")
    spec_paths = [path for path in sorted(examples_dir.glob("*.json")) if _is_example_spec_path(path)]
    results: list[dict[str, Any]] = []
    for spec_path in spec_paths:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        out_dir = run_root / spec_path.stem
        result = run_hls_workflow(spec, out_dir=out_dir, provider_name="mock", readiness="static", run_external=False, comment_language="zh")
        artifact_dir = Path(result["run_dir"]) / "attempt-001" / "hls" / "artifacts"
        report = validate_hls_artifacts(spec, artifact_dir, readiness="static", run_external=False, comment_language="zh") if artifact_dir.exists() else {"ok": False, "errors": 1, "warnings": 0}
        results.append(
            {
                "spec": spec_path.name,
                "workflow_status": result.get("status"),
                "validation_ok": bool(report.get("ok")),
                "errors": report.get("errors"),
                "warnings": report.get("warnings"),
            }
        )
    passed = all(item["workflow_status"] == "passed" and item["validation_ok"] for item in results)
    return {"status": "passed" if passed else "failed", "results": results}, [path.name for path in spec_paths]
def _example_spec_names() -> list[str]:
    examples_dir = skill_config_path("examples_dir")
    return [path.name for path in sorted(examples_dir.glob("*.json")) if _is_example_spec_path(path)]
def _remote_default_example_specs(coverage_mode: str) -> list[str]:
    if coverage_mode == "smoke":
        return [REMOTE_SMOKE_SPEC]
    if coverage_mode == "tier1":
        matrix = _load_tier1_board_matrix()
        families = matrix.get("families", {}) if isinstance(matrix, dict) else {}
        spec_names: list[str] = []
        for config in families.values():
            if not isinstance(config, dict):
                continue
            for key in ("representative", "high_risk"):
                value = str(config.get(key) or "").strip()
                if value and value not in spec_names:
                    spec_names.append(value)
        return spec_names
    if coverage_mode == "all_examples":
        return _example_spec_names()
    raise ValueError(f"Unsupported remote coverage mode: {coverage_mode!r}")
def _resolve_remote_example_specs(explicit_specs: list[str] | None, coverage_mode: str) -> tuple[list[str], str]:
    cleaned_explicit_specs = [spec_name for spec_name in explicit_specs or [] if str(spec_name).strip()]
    if cleaned_explicit_specs:
        unique_specs: list[str] = []
        for spec_name in cleaned_explicit_specs:
            if spec_name not in unique_specs:
                unique_specs.append(spec_name)
        return unique_specs, "explicit_specs"
    return _remote_default_example_specs(coverage_mode), coverage_mode
def _tier1_board_matrix_path() -> Path:
    return skill_config_path("examples_dir") / "tier1_board_coverage_matrix.json"
def _load_tier1_board_matrix() -> dict[str, Any]:
    return json.loads(_tier1_board_matrix_path().read_text(encoding="utf-8"))
def _is_example_spec_path(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return str(payload.get("target") or "").strip().lower() == "hls"
def _forward_test_gate(run_root: Path) -> dict[str, Any]:
    spec_names = [
        "hls_2d_block_transform_spec.json",
        "hls_array_reshape_vector_scale_spec.json",
        "hls_axi4_burst_vector_scale_spec.json",
        "hls_dataflow_axis_spec.json",
        "hls_directio_freerun_axis_spec.json",
        "hls_fixed_point_scale_spec.json",
        "hls_host_kernel_split_spec.json",
        "hls_minimal_vitis_pipeline_spec.json",
        "hls_multi_m_axi_add_spec.json",
        "hls_partition_vector_scale_spec.json",
        "hls_streamofblocks_axis_spec.json",
        "hls_task_graph_axis_spec.json",
    ]
    results: list[dict[str, Any]] = []
    for spec_name in spec_names:
        spec_path = skill_config_path("examples_dir") / spec_name
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        out_dir = run_root / "forward-test" / spec_path.stem
        result = run_hls_workflow(spec, out_dir=out_dir, provider_name="mock", readiness="static", run_external=False, comment_language="zh")
        artifact_dir = Path(result["run_dir"]) / "attempt-001" / "hls" / "artifacts"
        report = validate_hls_artifacts(spec, artifact_dir, readiness="static", run_external=False, comment_language="zh") if artifact_dir.exists() else {"ok": False, "errors": 1, "warnings": 0}
        results.append(
            {
                "spec": spec_name,
                "workflow_status": result.get("status"),
                "validation_ok": bool(report.get("ok")),
                "errors": report.get("errors"),
                "warnings": report.get("warnings"),
                "mode": "near_real_spec_static",
            }
        )
    passed = all(item["workflow_status"] == "passed" and item["validation_ok"] for item in results)
    return {"status": "passed" if passed else "failed", "results": results}
def _comment_policy_gate(run_root: Path) -> dict[str, Any]:
    spec_path = skill_config_path("examples_dir") / "hls_vector_scale_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    out_dir = run_root / "comment-policy"
    result = run_hls_workflow(spec, out_dir=out_dir / "good", provider_name="mock", readiness="static", run_external=False, comment_language="en")
    artifact_dir = Path(result["run_dir"]) / "attempt-001" / "hls" / "artifacts"
    good_report = validate_hls_artifacts(spec, artifact_dir, readiness="static", run_external=False, comment_language="en") if artifact_dir.exists() else {"ok": False, "issues": [], "metrics": {}}

    bad_dir = out_dir / "bad"
    if artifact_dir.exists():
        shutil.copytree(artifact_dir, bad_dir)
        for path in sorted(bad_dir.glob("**/*")):
            if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".cc", ".cxx"}:
                continue
            text = re.sub(r"//.*$", "// generic generated line, not hardware intent", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
            path.write_text(text, encoding="utf-8")
    bad_report = validate_hls_artifacts(spec, bad_dir, readiness="static", run_external=False, comment_language="en") if bad_dir.exists() else {"ok": True, "issues": [], "metrics": {}}
    bad_messages = "\n".join(str(issue.get("message", "")) for issue in bad_report.get("issues", []))
    passed = (
        result.get("status") == "passed"
        and bool(good_report.get("ok"))
        and good_report.get("metrics", {}).get("comment_policy", {}).get("policy") == "typed_hls_comment_placement"
        and not bool(bad_report.get("ok"))
        and "comment policy" in bad_messages.lower()
        and "generic" in bad_messages.lower()
    )
    return {
        "status": "passed" if passed else "failed",
        "good_workflow_status": result.get("status"),
        "good_validation_ok": bool(good_report.get("ok")),
        "bad_validation_ok": bool(bad_report.get("ok")),
        "bad_issue_count": len(bad_report.get("issues", [])),
    }
def _copyright_terms() -> tuple[str, ...]:
    return tuple("".join(parts) for parts in COPYRIGHT_TERM_PARTS)
def _scan_exclude_globs() -> tuple[str, ...]:
    return (
        "!ref/**",
        "!.git/**",
        "!reports/**",
        "!tests/**",
        "!smoke/**",
        "!scripts/python/validation/confidence_local.py",
        "!scripts/python/validation/confidence_loop.py",
    )
def _tail(text: str, *, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text
def _resolve_json_output(path_text: str) -> Path:
    output_path = Path(path_text)
    if output_path.is_absolute():
        return output_path
    parts = output_path.parts
    skill_prefix = tuple(SKILL_ROOT.relative_to(repo_root()).parts)
    if len(parts) >= len(skill_prefix) and tuple(part.lower() for part in parts[: len(skill_prefix)]) == tuple(part.lower() for part in skill_prefix):
        output_path = Path(*parts[len(skill_prefix) :]) if len(parts) > len(skill_prefix) else Path()
    elif parts and parts[0].lower() == SKILL_ROOT.name.lower():
        output_path = Path(*parts[1:]) if len(parts) > 1 else Path()
    return (repo_root() / output_path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
