"""Command line entrypoint for the HLS generator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import config_path, runtime_config, skill_dependencies_config, validate_runtime_config
from .hls_profile import build_hls_optimizer_prompt
from .prompt import COMMENT_LANGUAGE_CHOICES, PROMPT_BUDGETS, PROMPT_STAGES, render_prompt
from .requirements import apply_requirement_defaults, build_codegen_plan, build_requirements_payload, validate_requirement_confirmation
from .skill_dependencies import (
    BLOCKED_DEPENDENCY,
    SkillDependencyError,
    build_dependency_request,
    check_skill_dependencies,
    format_dependency_report,
    install_skill_dependencies,
    require_skill_dependencies,
)
from .spec import SpecError, read_spec, scaffold_spec, write_spec
from .user_config import load_user_config, resolve_comment_language, set_comment_language, user_config_path
from .validation import READINESS_LEVELS, validate_generated
from .workflow import run_workflow
from .workspace import require_configured_output_path, require_workspace_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hls-gen", description="AMD-Xilinx/Vitis HLS-only generator CLI.")
    parser.add_argument("--version", action="version", version="hls-gen 0.1.9")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold = subparsers.add_parser("scaffold", help="Create a starter HLS spec.")
    scaffold.add_argument("--target", default="hls", choices=("hls",))
    scaffold.add_argument("--name", default="hls_kernel")
    scaffold.add_argument("--out", required=True, type=Path)
    scaffold.set_defaults(func=_cmd_scaffold)

    prompt = subparsers.add_parser("prompt", help="Render an HLS prompt from a spec.")
    prompt.add_argument("--target", default="hls", choices=("hls",))
    prompt.add_argument("--spec", required=True, type=Path)
    prompt.add_argument("--out", required=True, type=Path)
    prompt.add_argument("--stage", choices=PROMPT_STAGES)
    prompt.add_argument("--comment-language", default="auto", choices=COMMENT_LANGUAGE_CHOICES)
    prompt.add_argument("--budget", default="normal", choices=PROMPT_BUDGETS)
    prompt.add_argument("--hls-profile", type=Path)
    prompt.set_defaults(func=_cmd_prompt)

    validate = subparsers.add_parser("validate", help="Validate generated HLS artifacts.")
    validate.add_argument("--target", default="hls", choices=("hls",))
    validate.add_argument("--spec", required=True, type=Path)
    validate.add_argument("--path", required=True, type=Path)
    validate.add_argument("--readiness", default="static", choices=READINESS_LEVELS)
    validate.add_argument("--comment-language", default="auto", choices=COMMENT_LANGUAGE_CHOICES)
    validate.add_argument("--hls-profile", type=Path)
    validate.add_argument("--reference-contract", type=Path)
    validate.add_argument("--report-json", type=Path)
    validate.add_argument("--no-external", action="store_true")
    validate.set_defaults(func=_cmd_validate)

    run = subparsers.add_parser("run-workflow", help="Run a staged HLS generation workflow.")
    run.add_argument("--target", default="hls", choices=("hls",))
    run.add_argument("--spec", type=Path)
    run.add_argument("--out-dir", type=Path)
    run.add_argument("--resume-dir", type=Path)
    run.add_argument("--decision", type=Path)
    run.add_argument("--provider", default="manual", choices=("manual", "mock", "command"))
    run.add_argument("--provider-command")
    run.add_argument("--readiness", default="execute", choices=READINESS_LEVELS)
    run.add_argument("--max-attempts", default=3, type=int)
    run.add_argument("--comment-language", default="auto", choices=COMMENT_LANGUAGE_CHOICES)
    run.add_argument("--hls-profile", type=Path)
    run.add_argument("--no-external", action="store_true")
    run.set_defaults(func=_cmd_run_workflow)

    opt = subparsers.add_parser("optimize-hls-prompt", help="Generate a focused HLS profile repair prompt.")
    opt.add_argument("--report-json", required=True, type=Path)
    opt.add_argument("--profile", required=True, type=Path)
    opt.add_argument("--out", required=True, type=Path)
    opt.set_defaults(func=_cmd_optimize_hls_prompt)

    config = subparsers.add_parser("config", help="Print the active runtime configuration.")
    config.add_argument("--path", action="store_true", help="Print only the active config file path.")
    config.set_defaults(func=_cmd_config)

    user_config = subparsers.add_parser("user-config", help="Print or update the user-level HLS generator config.")
    user_config.add_argument("--path", action="store_true", help="Print only the user config path.")
    user_config.add_argument("--set-comment-language", choices=("en", "zh"), help="Persist the generated C/HLS comment language preference.")
    user_config.set_defaults(func=_cmd_user_config)

    deps = subparsers.add_parser("deps", help="Check or install HLSGenerator skill dependencies.")
    deps_subparsers = deps.add_subparsers(dest="deps_command", required=True)

    deps_check = deps_subparsers.add_parser("check", help="Check configured skill dependencies.")
    deps_check.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    deps_check.add_argument("--human", action="store_true", help="Print human-readable status.")
    deps_check.set_defaults(func=_cmd_deps_check)

    deps_request = deps_subparsers.add_parser("request", help="Write an install request for missing dependencies.")
    deps_request.add_argument("--out", required=True, type=Path)
    deps_request.set_defaults(func=_cmd_deps_request)

    deps_install = deps_subparsers.add_parser("install", help="Install selected skill dependencies after user confirmation.")
    deps_install.add_argument("--ids", nargs="+", help="Dependency ids to install.")
    deps_install.add_argument("--all", action="store_true", help="Install all configured dependencies.")
    deps_install.add_argument("--dest", type=Path, help="Override destination skills root.")
    deps_install.set_defaults(func=_cmd_deps_install)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (SpecError, ValueError, SkillDependencyError) as exc:
        parser.exit(2, f"error: {exc}\n")


def _cmd_scaffold(args: argparse.Namespace) -> int:
    spec = scaffold_spec("hls", name=args.name)
    output = require_configured_output_path(args.out, purpose="spec output path")
    write_spec(output, spec)
    print(output)
    return 0


def _cmd_prompt(args: argparse.Namespace) -> int:
    _require_dependencies_for_use(args.out.parent)
    spec = _confirmed_spec(read_spec(require_workspace_path(args.spec, purpose="spec path", must_exist=True), target="hls"))
    prompt_text = render_prompt(
        spec,
        target="hls",
        stage=args.stage,
        comment_language=_require_resolved_comment_language(args.comment_language),
        budget=args.budget,
        hls_profile=_read_json(args.hls_profile) if args.hls_profile else None,
        codegen_plan=build_codegen_plan(spec),
    )
    output = require_configured_output_path(args.out, purpose="prompt output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(prompt_text, encoding="utf-8")
    print(output)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    _require_dependencies_for_use(args.report_json.parent if args.report_json else None)
    spec = _confirmed_spec(read_spec(require_workspace_path(args.spec, purpose="spec path", must_exist=True), target="hls"))
    report = validate_generated(
        spec,
        require_workspace_path(args.path, purpose="artifacts path", must_exist=True),
        target="hls",
        run_external=not args.no_external,
        readiness=args.readiness,
        comment_language=_require_resolved_comment_language(args.comment_language),
        hls_profile=_read_json(args.hls_profile) if args.hls_profile else None,
        reference_contract=_read_json(args.reference_contract) if args.reference_contract else None,
    )
    print(report.format())
    if args.report_json:
        report_path = require_configured_output_path(args.report_json, purpose="validation report path")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if report.ok() else 1


def _cmd_run_workflow(args: argparse.Namespace) -> int:
    if args.resume_dir is None and (args.spec is None or args.out_dir is None):
        raise ValueError("run-workflow requires --spec and --out-dir for new runs, or --resume-dir for resume.")
    _require_dependencies_for_use(args.resume_dir or args.out_dir)
    result = run_workflow(
        spec_path=args.spec,
        target="hls",
        out_dir=args.out_dir,
        resume_dir=args.resume_dir,
        decision_path=args.decision,
        provider_name=args.provider,
        provider_command=args.provider_command,
        readiness=args.readiness,
        max_attempts=args.max_attempts,
        run_external=not args.no_external,
        comment_language=args.comment_language,
        hls_profile=_read_json(args.hls_profile) if args.hls_profile else None,
    )
    print(json.dumps({"status": result["status"], "run_dir": str(args.resume_dir or args.out_dir)}, indent=2))
    return 0 if result["status"] == "passed" else 1


def _cmd_optimize_hls_prompt(args: argparse.Namespace) -> int:
    prompt = build_hls_optimizer_prompt(_read_json(args.report_json), _read_json(args.profile))
    output = require_configured_output_path(args.out, purpose="optimizer prompt output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(prompt, encoding="utf-8")
    print(output)
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    validate_runtime_config()
    if args.path:
        print(config_path())
        return 0
    print(json.dumps(runtime_config(), indent=2, ensure_ascii=False))
    return 0


def _cmd_user_config(args: argparse.Namespace) -> int:
    if args.set_comment_language:
        path = set_comment_language(args.set_comment_language)
        print(path)
        return 0
    if args.path:
        print(user_config_path())
        return 0
    print(json.dumps(load_user_config(), indent=2, ensure_ascii=False))
    return 0


def _cmd_deps_check(args: argparse.Namespace) -> int:
    report = check_skill_dependencies(skill_dependencies_config(), scopes={"core"})
    if args.json or not args.human:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_dependency_report(report))
    return 0 if report["status"] != BLOCKED_DEPENDENCY else 1


def _cmd_deps_request(args: argparse.Namespace) -> int:
    report = check_skill_dependencies(skill_dependencies_config(), scopes={"core"})
    request = build_dependency_request(report)
    output = require_configured_output_path(args.out, purpose="dependency request output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(request, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 0


def _cmd_deps_install(args: argparse.Namespace) -> int:
    result = install_skill_dependencies(skill_dependencies_config(), ids=args.ids, install_all=bool(args.all), dest_root=args.dest)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


def _confirmed_spec(spec: dict) -> dict:
    if not spec.get("design_requirements"):
        spec = apply_requirement_defaults(spec, confirmed_by_user=True, confirmation_notes="Confirmed by local CLI caller.")
    validate_requirement_confirmation(spec)
    return spec


def _read_json(path: Path) -> dict:
    return json.loads(require_workspace_path(path, purpose="JSON path", must_exist=True).read_text(encoding="utf-8"))


def _require_resolved_comment_language(comment_language: str) -> str:
    resolved = resolve_comment_language(comment_language)
    if resolved is None:
        raise ValueError("Comment language is not configured. Choose `en` or `zh` with `python -m runtime.hls_generator user-config --set-comment-language <en|zh>`, or pass --comment-language en|zh.")
    return resolved


def _require_dependencies_for_use(request_dir: Path | None) -> None:
    report = check_skill_dependencies(skill_dependencies_config(), scopes={"core"})
    if report["status"] != BLOCKED_DEPENDENCY:
        return
    if request_dir is not None:
        try:
            request_path = require_configured_output_path(request_dir / "skill_dependency_request.json", purpose="dependency request output path")
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(json.dumps(build_dependency_request(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except (OSError, ValueError):
            pass
    raise SkillDependencyError(report)


if __name__ == "__main__":
    raise SystemExit(main())
