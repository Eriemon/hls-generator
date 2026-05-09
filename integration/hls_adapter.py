"""Stable local facade for HLS-only generation workflows."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from runtime.hls_generator.config import generated_roots, protected_files, protected_roots, skill_config_path, skill_dependencies_config, skill_root
from runtime.hls_generator.prompt import render_prompt
from runtime.hls_generator.requirements import apply_requirement_defaults, build_codegen_plan, build_requirements_payload, validate_requirement_confirmation
from runtime.hls_generator.skill_dependencies import require_skill_dependencies
from runtime.hls_generator.spec import normalize_spec, read_spec, write_spec
from runtime.hls_generator.user_config import resolve_comment_language
from runtime.hls_generator.validation import validate_generated
from runtime.hls_generator.workflow import run_workflow
from runtime.hls_generator.workspace import use_workspace_root

DEFAULT_CONFIG_PATH = skill_config_path("default_workflow_config")
SKILL_ROOT = skill_root()

__all__ = [
    "run_hls_workflow",
    "render_hls_prompt",
    "validate_hls_artifacts",
    "load_default_workflow_config",
    "load_workflow_result",
]


def load_default_workflow_config() -> dict[str, Any]:
    return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def load_workflow_result(run_dir: str | Path) -> dict[str, Any]:
    run_path = _resolve_skill_input_path(run_dir, purpose="workflow result directory", must_exist=True)
    return json.loads((run_path / "workflow_result.json").read_text(encoding="utf-8"))


def run_hls_workflow(
    spec: str | Path | dict[str, Any] | None = None,
    *,
    out_dir: str | Path | None = None,
    resume_dir: str | Path | None = None,
    workflow_config: str | Path | dict[str, Any] | None = None,
    evidence: str | Path | dict[str, Any] | None = None,
    decision: str | Path | dict[str, Any] | None = None,
    provider_name: str | None = None,
    provider_command: str | None = None,
    target: str | None = None,
    design_requirements: str | Path | dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: str | Path | dict[str, Any] | None = None,
    readiness: str | None = None,
    max_attempts: int | None = None,
    stop_on_human: bool | None = None,
    run_external: bool | None = None,
    comment_language: str | None = None,
    hls_profile: str | Path | dict[str, Any] | None = None,
    model_timeout_s: int | None = None,
) -> dict[str, Any]:
    require_skill_dependencies(skill_dependencies_config())
    _reject_non_hls_target(target)
    defaults = load_default_workflow_config()
    overrides = _load_optional_json(workflow_config) or {}
    merged = {**defaults, **overrides}
    resolved_readiness = readiness or merged.get("readiness", "execute")
    resolved_attempts = int(max_attempts or merged.get("max_attempts", 3))
    resolved_stop_on_human = bool(stop_on_human) if stop_on_human is not None else bool(merged.get("stop_on_human", True))
    resolved_run_external = bool(run_external) if run_external is not None else bool(merged.get("run_external", True))
    resolved_comment_language = comment_language or str(merged.get("comment_language", "auto"))
    resolved_provider_name = provider_name or str(merged.get("model_provider", "command"))
    resolved_timeout = int(model_timeout_s or merged.get("model_timeout_s", 120))

    if resume_dir is not None:
        run_dir = _resolve_generated_path(resume_dir, purpose="workflow resume directory", must_exist=True)
        decision_path = _materialize_optional_json(decision, run_dir / "_adapter_inputs" / "decision.json")
        with use_workspace_root(run_dir):
            result = run_workflow(
                resume_dir=run_dir,
                decision_path=decision_path,
                stop_on_human=resolved_stop_on_human,
                run_external=resolved_run_external,
                comment_language=resolved_comment_language,
                model_timeout_s=resolved_timeout,
            )
        return {"status": result["status"], "run_dir": str(run_dir), "result_path": str(run_dir / "workflow_result.json"), "workflow_result": result}

    if spec is None or out_dir is None:
        raise ValueError("New HLS workflow runs require both `spec` and `out_dir`.")
    run_dir = _resolve_generated_path(out_dir, purpose="workflow output directory")
    inputs_dir = run_dir / "_adapter_inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    prepared_spec = _prepare_facade_spec(
        spec,
        design_requirements=_load_optional_json(design_requirements),
        pipeline_required=pipeline_required,
        streamability=streamability,
        interface_family=interface_family,
        interface_profile=_load_optional_json(interface_profile),
    )
    requirements_path = _write_json_object(inputs_dir / "requirements.json", build_requirements_payload(prepared_spec))
    codegen_plan_path = _write_json_object(inputs_dir / "codegen_plan.json", build_codegen_plan(prepared_spec))
    prepared_spec["codegen_plan_path"] = codegen_plan_path.relative_to(run_dir).as_posix()
    spec_path = _materialize_spec(prepared_spec, inputs_dir / "spec.json")
    evidence_path = _materialize_optional_json(evidence, inputs_dir / "evidence.json")
    decision_path = _materialize_optional_json(decision, inputs_dir / "decision.json")
    hls_profile_obj = _load_optional_json(hls_profile)

    with use_workspace_root(run_dir):
        result = run_workflow(
            spec_path=spec_path,
            target="hls",
            out_dir=run_dir,
            decision_path=decision_path,
            evidence_path=evidence_path,
            provider_name=resolved_provider_name,
            provider_command=provider_command,
            readiness=resolved_readiness,
            max_attempts=resolved_attempts,
            stop_on_human=resolved_stop_on_human,
            run_external=resolved_run_external,
            comment_language=resolved_comment_language,
            hls_profile=hls_profile_obj if isinstance(hls_profile_obj, dict) else None,
            model_timeout_s=resolved_timeout,
        )
    return {"status": result["status"], "run_dir": str(run_dir), "result_path": str(run_dir / "workflow_result.json"), "requirements_path": str(requirements_path), "codegen_plan_path": str(codegen_plan_path), "workflow_result": result}


def render_hls_prompt(
    spec: str | Path | dict[str, Any],
    out_path: str | Path,
    *,
    target: str | None = None,
    design_requirements: str | Path | dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: str | Path | dict[str, Any] | None = None,
    stage: str | None = None,
    context_manifest: str | Path | dict[str, Any] | None = None,
    context_dir: str | Path | None = None,
    evidence: str | Path | dict[str, Any] | None = None,
    memory: str | Path | dict[str, Any] | None = None,
    comment_language: str = "auto",
    vector_contract: str | Path | dict[str, Any] | None = None,
    budget: str = "normal",
    hls_profile: str | Path | dict[str, Any] | None = None,
    decision: str | Path | dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_skill_dependencies(skill_dependencies_config())
    _reject_non_hls_target(target)
    resolved_spec = _prepare_facade_spec(
        spec,
        design_requirements=_load_optional_json(design_requirements),
        pipeline_required=pipeline_required,
        streamability=streamability,
        interface_family=interface_family,
        interface_profile=_load_optional_json(interface_profile),
    )
    prompt_text = render_prompt(
        resolved_spec,
        target="hls",
        stage=stage or "hls",
        context_manifest=_load_optional_json(context_manifest),
        context_dir=Path(context_dir) if context_dir is not None else None,
        evidence=_load_optional_json(evidence),
        memory=_load_optional_json(memory),
        comment_language=_require_resolved_comment_language(comment_language),
        vector_contract=_load_optional_json(vector_contract),
        codegen_plan=build_codegen_plan(resolved_spec),
        budget=budget,
        hls_profile=_load_optional_json(hls_profile),
        decision=_load_optional_json(decision),
    )
    output_path = _resolve_generated_path(out_path, purpose="prompt output path")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt_text, encoding="utf-8")
    return {"path": str(output_path), "prompt": prompt_text}


def validate_hls_artifacts(
    spec: str | Path | dict[str, Any],
    artifacts_path: str | Path,
    *,
    target: str | None = None,
    design_requirements: str | Path | dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: str | Path | dict[str, Any] | None = None,
    run_external: bool = True,
    readiness: str = "static",
    comment_language: str = "auto",
    hls_profile: str | Path | dict[str, Any] | None = None,
    reference_contract: str | Path | dict[str, Any] | None = None,
    report_json: str | Path | None = None,
) -> dict[str, Any]:
    require_skill_dependencies(skill_dependencies_config())
    _reject_non_hls_target(target)
    resolved_spec = _prepare_facade_spec(
        spec,
        design_requirements=_load_optional_json(design_requirements),
        pipeline_required=pipeline_required,
        streamability=streamability,
        interface_family=interface_family,
        interface_profile=_load_optional_json(interface_profile),
    )
    report = validate_generated(
        resolved_spec,
        _resolve_skill_input_path(artifacts_path, purpose="artifacts path", must_exist=True),
        target="hls",
        run_external=run_external,
        readiness=readiness,
        comment_language=_require_resolved_comment_language(comment_language),
        hls_profile=_load_optional_json(hls_profile),
        reference_contract=_load_optional_json(reference_contract),
    )
    payload = report.to_dict()
    if report_json is not None:
        out_path = _resolve_generated_path(report_json, purpose="validation report path")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _prepare_facade_spec(
    spec: str | Path | dict[str, Any],
    *,
    design_requirements: dict[str, Any] | None,
    pipeline_required: bool | None,
    streamability: str | None,
    interface_family: str | None,
    interface_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = _load_raw_spec(spec)
    enriched = apply_requirement_defaults(
        raw,
        design_requirements=design_requirements,
        pipeline_required=pipeline_required,
        streamability=streamability,
        interface_family=interface_family,
        interface_profile=interface_profile,
        confirmed_by_user=True if any(value is not None for value in (design_requirements, pipeline_required, streamability, interface_family, interface_profile)) else None,
    )
    normalized = normalize_spec(enriched, target="hls")
    validate_requirement_confirmation(normalized)
    return normalized


def _require_resolved_comment_language(comment_language: str | None) -> str:
    resolved = resolve_comment_language(comment_language or "auto")
    if resolved is None:
        raise ValueError("Comment language is not configured. Choose `en` or `zh` and save it with `python -m runtime.hls_generator user-config --set-comment-language <en|zh>`, or pass comment_language explicitly.")
    return resolved


def _reject_non_hls_target(target: str | None) -> None:
    if target not in (None, "hls"):
        raise ValueError("This skill is HLS-only; target must be `hls`.")


def _materialize_spec(spec: str | Path | dict[str, Any], out_path: Path) -> Path:
    normalized = read_spec(_resolve_skill_input_path(spec, purpose="spec path", must_exist=True), target="hls") if isinstance(spec, (str, Path)) else normalize_spec(spec, target="hls")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_spec(out_path, normalized)
    return out_path


def _write_json_object(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _materialize_optional_json(value: str | Path | dict[str, Any] | None, out_path: Path) -> Path | None:
    if value is None:
        return None
    if isinstance(value, (str, Path)):
        return _resolve_skill_input_path(value, purpose="JSON input path", must_exist=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def _load_optional_json(value: str | Path | dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return json.loads(_resolve_skill_input_path(value, purpose="JSON input path", must_exist=True).read_text(encoding="utf-8"))


def _load_raw_spec(spec: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(spec, dict):
        return deepcopy(spec)
    return json.loads(_resolve_skill_input_path(spec, purpose="spec path", must_exist=True).read_text(encoding="utf-8"))


def _resolve_skill_input_path(path: str | Path, *, purpose: str, must_exist: bool) -> Path:
    raw = Path(path)
    candidate = raw if raw.is_absolute() else SKILL_ROOT / raw
    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        raise ValueError(f"{purpose} does not exist inside this skill: {path}") from None
    except OSError as exc:
        raise ValueError(f"Could not resolve {purpose}: {path}: {exc}") from exc
    try:
        resolved.relative_to(SKILL_ROOT)
    except ValueError as exc:
        raise ValueError(f"{purpose} must stay inside {SKILL_ROOT}: {path}") from exc
    return resolved


def _resolve_generated_path(path: str | Path, *, purpose: str, must_exist: bool = False) -> Path:
    resolved = _resolve_skill_input_path(path, purpose=purpose, must_exist=must_exist)
    rel_parts = resolved.relative_to(SKILL_ROOT).parts
    if not rel_parts:
        raise ValueError(f"{purpose} must not be the skill root.")
    first = rel_parts[0]
    configured_protected = protected_roots() | protected_files()
    configured_generated = generated_roots()
    if first in configured_protected:
        raise ValueError(f"{purpose} must not write into protected skill source path {first!r}.")
    if first not in configured_generated:
        raise ValueError(f"{purpose} must be under one of: {', '.join(sorted(configured_generated))}.")
    return resolved
