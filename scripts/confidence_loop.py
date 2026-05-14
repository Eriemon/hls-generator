#!/usr/bin/env python3
"""Run repeatable Erie HLS Generator confidence gates."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from integration.hls_adapter import run_hls_workflow, validate_hls_artifacts  # noqa: E402
from runtime.hls_generator.config import skill_config_path, skill_dependencies_config  # noqa: E402
from runtime.hls_generator.skill_dependencies import check_skill_dependencies  # noqa: E402

SOURCE_NOTE_PATHS = {
    "references/vitis-hls-2024-2-script-guide.md",
    "references/vitis-hls-official-patterns.md",
    "references/hls-modeling-strategy.md",
    "references/hls-task-parallel-strategy.md",
}
REF_DEPENDENCY_PATTERN = "ref[/\\\\]|" + "Vitis-" + "Tutorials|" + "Vitis-HLS" + r"\.md|" + "UG" + "1399"
FORBIDDEN_REFERENCE_TERMS = ("vitis-hls-introductory-examples",)
SCAN_EXCLUDE_GLOBS = (
    "!ref/**",
    "!.git/**",
    "!reports/**",
    "!tests/**",
    "!smoke/**",
    "!scripts/confidence_loop.py",
)


def repo_root() -> Path:
    return SKILL_ROOT.parents[1]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run Erie HLS Generator local and optional remote confidence gates.")
    parser.add_argument("--server", help="Optional erie-remote-ssh server for real remote Vitis validation.")
    parser.add_argument("--readiness", default="cosim", choices=("static", "compile", "execute", "implement", "cosim"))
    parser.add_argument("--example-spec", action="append", help="Example spec to use for optional remote validation. Can be repeated.")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--skip-compileall", action="store_true")
    parser.add_argument("--skip-quick-validate", action="store_true")
    parser.add_argument("--skip-remote", action="store_true")
    parser.add_argument("--json-out", help="Write JSON summary to this path.")
    args = parser.parse_args(argv)

    run_root = SKILL_ROOT / "reports" / "confidence-loop" / f"{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S%fZ')}-pid{os.getpid()}"
    run_root.mkdir(parents=True, exist_ok=True)

    gates: dict[str, dict[str, Any]] = {}
    if not args.skip_smoke:
        gates["smoke"] = _run_command([sys.executable, "smoke/run_smoke.py"], cwd=SKILL_ROOT)
    if not args.skip_compileall:
        gates["compileall"] = _run_command([sys.executable, "-m", "compileall", "runtime/hls_generator"], cwd=SKILL_ROOT)
    if not args.skip_quick_validate:
        gates["quick_validate"] = _run_command([sys.executable, str(_quick_validate_path()), str(SKILL_ROOT)], cwd=SKILL_ROOT)
    gates["skill_dependencies"] = _skill_dependency_gate()
    gates["ref_dependency_scan"] = _ref_dependency_scan()
    gates["forbidden_reference_names"] = _forbidden_reference_name_scan()
    if gates["skill_dependencies"]["status"] == "passed":
        examples_gate, example_specs = _validate_examples(run_root)
    else:
        example_specs = _example_spec_names()
        examples_gate = {"status": "skipped", "reason": "blocked_dependency", "results": []}
    gates["example_mock_validation"] = examples_gate
    remote_results: list[dict[str, Any]] = []
    if args.server and not args.skip_remote:
        for spec_name in args.example_spec or ["hls_partition_vector_scale_spec.json"]:
            remote_results.append(_run_remote(args.server, args.readiness, spec_name))
        gates["remote_vitis"] = _summarize_remote(remote_results)

    confidence_status = "factual_high_confidence" if all(item["status"] == "passed" for item in gates.values()) else "needs_attention"
    payload = {
        "version": 1,
        "confidence_status": confidence_status,
        "run_root": str(run_root),
        "gates": gates,
        "example_specs": example_specs,
        "remote_results": remote_results,
        "residual_risks": _residual_risks(confidence_status, bool(args.server and not args.skip_remote)),
    }
    if args.json_out:
        output_path = _resolve_json_output(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if confidence_status == "factual_high_confidence" else 1


def _run_command(command: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": _tail(result.stdout),
        "stderr_tail": _tail(result.stderr),
    }


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


def _ref_dependency_scan() -> dict[str, Any]:
    result = subprocess.run(
        ["rg", *sum((["--glob", item] for item in SCAN_EXCLUDE_GLOBS), []), REF_DEPENDENCY_PATTERN, "."],
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    unexpected = [line for line in lines if _relative_match_path(line) not in SOURCE_NOTE_PATHS]
    return {
        "status": "passed" if result.returncode in {0, 1} and not unexpected else "failed",
        "command": ["rg", *sum((["--glob", item] for item in SCAN_EXCLUDE_GLOBS), []), REF_DEPENDENCY_PATTERN, "."],
        "matches": lines,
        "unexpected_matches": unexpected,
    }


def _forbidden_reference_name_scan() -> dict[str, Any]:
    result = subprocess.run(
        ["rg", "-n", *sum((["--glob", item] for item in SCAN_EXCLUDE_GLOBS), []), "|".join(FORBIDDEN_REFERENCE_TERMS), "."],
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
        "command": ["rg", "-n", *sum((["--glob", item] for item in SCAN_EXCLUDE_GLOBS), []), "|".join(FORBIDDEN_REFERENCE_TERMS), "."],
        "matches": lines,
        "unexpected_matches": unexpected,
    }


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
    spec_paths = sorted(examples_dir.glob("*.json"))
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
    return [path.name for path in sorted(examples_dir.glob("*.json"))]


def _run_remote(server: str, readiness: str, spec_name: str) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/remote_vitis_acceptance.py",
        "--mode",
        "vitis",
        "--server",
        server,
        "--profile",
        "vitis_2022",
        "--readiness",
        readiness,
        "--example-spec",
        spec_name,
        "--comment-language",
        "zh",
        "--json",
    ]
    result = subprocess.run(command, cwd=SKILL_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"status": "failed", "stdout_tail": _tail(result.stdout), "stderr_tail": _tail(result.stderr)}
    payload["returncode"] = result.returncode
    payload["example_spec"] = spec_name
    return payload


def _summarize_remote(remote_results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = bool(remote_results) and all(item.get("status") == "passed" and item.get("remote_artifacts_retained") is True for item in remote_results)
    return {"status": "passed" if passed else "failed", "results": remote_results}


def _residual_risks(confidence_status: str, remote_requested: bool) -> list[str]:
    risks: list[str] = []
    if confidence_status != "factual_high_confidence":
        risks.append("At least one confidence gate failed; inspect gates for details.")
    if not remote_requested:
        risks.append("Remote Vitis validation was skipped for this confidence-loop invocation.")
    risks.append("Unified HLS open_component and direct v++ flows remain documented extension points, not active execution paths.")
    return risks


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
    return (SKILL_ROOT / output_path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
