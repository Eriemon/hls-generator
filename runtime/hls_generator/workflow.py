"""End-to-end HLS workflow runner."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .config import resolve_vitis_skill_preference, vitis_blocking_tool_ids
from .extractor import ExtractionError, extract_response
from .interface_contract import audit_interface
from .model_provider import GenerationContext, ManualResponseRequired, ModelProviderError, build_model_provider
from .planning import decompose_spec
from .prompt import _manifest_for, _stage_manifest_for, render_prompt
from .reference_contract import audit_reference
from .requirements import build_codegen_plan, build_requirements_payload, validate_codegen_plan_payload, validate_requirement_confirmation
from .spec import SpecError, read_spec, write_spec
from .trace import append_trace_event, read_trace, safe_path, spec_summary
from .user_config import comment_language_request, resolve_comment_language
from .validation import validate_generated
from .vectors import audit_vectors
from .verifier import verify_stage
from .workspace import require_configured_output_path, require_workspace_path, require_workspace_path_from, require_write_path, update_workflow_state, use_workspace_root, write_json, write_text

WORKFLOW_STATUSES = ("passed", "failed", "blocked_human", "blocked_toolchain", "max_attempts", "invalid_response")
DEFAULT_STAGES = ["requirements", "codegen_plan", "tests", "python", "hls"]
FINAL_STAGE = "hls"


class WorkflowError(ValueError):
    """Raised when workflow configuration or resume state is invalid."""


def run_workflow(
    *,
    spec_path: Path | None = None,
    target: str | None = None,
    out_dir: Path | None = None,
    resume_dir: Path | None = None,
    decision_path: Path | None = None,
    evidence_path: Path | None = None,
    provider_name: str = "manual",
    provider_command: str | None = None,
    readiness: str = "execute",
    max_attempts: int = 3,
    stop_on_human: bool = True,
    run_external: bool = True,
    comment_language: str = "auto",
    hls_profile: dict[str, Any] | None = None,
    model_timeout_s: int = 120,
    state_updates: bool = True,
) -> dict[str, Any]:
    if target not in (None, "hls"):
        raise WorkflowError("This skill is HLS-only; workflow target must be `hls`.")
    if resume_dir is not None:
        return _resume_workflow(
            resume_dir=resume_dir,
            decision_path=decision_path,
            stop_on_human=stop_on_human,
            run_external=run_external,
            comment_language=comment_language,
            model_timeout_s=model_timeout_s,
            state_updates=state_updates,
        )
    if spec_path is None or out_dir is None:
        raise WorkflowError("New workflow runs require both spec_path and out_dir.")

    spec_file = require_workspace_path(spec_path, purpose="spec path", must_exist=True)
    run_dir = require_configured_output_path(out_dir, purpose="workflow output directory")
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace.jsonl"
    state_path = run_dir / "workflow-state.json"
    result_path = run_dir / "workflow_result.json"
    config_path = run_dir / "workflow_config.json"
    plan_path = run_dir / "plan.json"

    raw_spec = read_spec(spec_file, target="hls")
    validate_requirement_confirmation(raw_spec)
    external_codegen_plan = _resolve_external_codegen_plan(raw_spec, spec_file)
    evidence = _read_json(evidence_path) if evidence_path else None
    plan = decompose_spec(raw_spec, target="hls", evidence=evidence)
    write_spec(plan_path, plan)

    config = _workflow_config(
        plan,
        provider_name=provider_name,
        provider_command=provider_command,
        readiness=readiness,
        max_attempts=max_attempts,
        stop_on_human=stop_on_human,
        run_external=run_external,
        comment_language=comment_language,
        hls_profile=hls_profile or plan.get("hls_profile") or {},
        external_codegen_plan=external_codegen_plan,
        model_timeout_s=model_timeout_s,
    )
    resolved_comment_language = resolve_comment_language(str(config.get("comment_language", "auto")))
    if resolved_comment_language is None:
        request_path = run_dir / "comment_language_request.json"
        write_json(request_path, comment_language_request())
        result = {
            "version": 1,
            "name": plan["name"],
            "target": "hls",
            "status": "blocked_human",
            "plan_path": "plan.json",
            "workflow_config": "workflow_config.json",
            "trace_path": "trace.jsonl",
            "attempts": [],
            "comment_language_request": safe_path(request_path, run_dir),
        }
        write_json(config_path, config)
        _write_result(result_path, result)
        append_trace_event(trace_path, {"event": "comment_language_request", "output": request_path, "preferred_values": ["en", "zh"]})
        _record_state(state_path, "comment_language_request", {"output": request_path}, enabled=state_updates)
        return result
    config["comment_language"] = resolved_comment_language
    write_json(config_path, config)
    result = {
        "version": 1,
        "name": plan["name"],
        "target": "hls",
        "status": "failed",
        "plan_path": "plan.json",
        "workflow_config": "workflow_config.json",
        "trace_path": "trace.jsonl",
        "attempts": [],
    }
    _write_result(result_path, result)
    _record_state(state_path, "run_workflow", {"out_dir": run_dir, "target": "hls", "name": plan["name"]}, enabled=state_updates)
    return _execute_workflow(run_dir, plan, config, result, result_path, trace_path, state_path, _read_json(decision_path) if decision_path else None, state_updates)


def _resume_workflow(
    *,
    resume_dir: Path,
    decision_path: Path | None,
    stop_on_human: bool,
    run_external: bool,
    comment_language: str,
    model_timeout_s: int,
    state_updates: bool,
) -> dict[str, Any]:
    run_dir = require_workspace_path(resume_dir, purpose="workflow resume directory", must_exist=True)
    config_path = require_workspace_path(run_dir / "workflow_config.json", purpose="workflow config", must_exist=True)
    result_path = require_workspace_path(run_dir / "workflow_result.json", purpose="workflow result", must_exist=True)
    plan_path = require_workspace_path(run_dir / "plan.json", purpose="workflow plan", must_exist=True)
    trace_path = require_write_path(run_dir / "trace.jsonl", purpose="workflow trace")
    state_path = require_write_path(run_dir / "workflow-state.json", purpose="workflow state")
    config = _read_json(config_path)
    result = _read_json(result_path)
    plan = read_spec(plan_path, target="hls")
    decision = _read_json(decision_path) if decision_path else None
    if result.get("status") == "blocked_human" and decision is None:
        raise WorkflowError("Resuming a blocked_human workflow requires a decision JSON file.")
    if result.get("status") == "blocked_human" and decision is not None:
        plan = _apply_human_decision_to_plan(plan, decision)
        if isinstance(config.get("external_codegen_plan"), dict):
            config["external_codegen_plan"] = _apply_human_decision_to_codegen_plan(config["external_codegen_plan"], decision)
        write_spec(plan_path, plan)
    config["stop_on_human"] = stop_on_human
    config["run_external"] = run_external
    requested_comment_language = comment_language or config.get("comment_language", "auto")
    resolved_comment_language = resolve_comment_language(str(requested_comment_language))
    if resolved_comment_language is None:
        request_path = run_dir / "comment_language_request.json"
        write_json(request_path, comment_language_request())
        result["status"] = "blocked_human"
        result["comment_language_request"] = safe_path(request_path, run_dir)
        write_json(config_path, config)
        _write_result(result_path, result)
        append_trace_event(trace_path, {"event": "comment_language_request", "output": request_path, "preferred_values": ["en", "zh"]})
        _record_state(state_path, "comment_language_request", {"output": request_path}, enabled=state_updates)
        return result
    config["comment_language"] = resolved_comment_language
    config["model_timeout_s"] = model_timeout_s or int(config.get("model_timeout_s", 120))
    write_json(config_path, config)
    _record_state(state_path, "resume_workflow", {"resume_dir": run_dir, "decision": decision_path}, enabled=state_updates)
    return _execute_workflow(run_dir, plan, config, result, result_path, trace_path, state_path, decision, state_updates)


def _execute_workflow(
    run_dir: Path,
    plan: dict[str, Any],
    config: dict[str, Any],
    result: dict[str, Any],
    result_path: Path,
    trace_path: Path,
    state_path: Path,
    decision: dict[str, Any] | None,
    state_updates: bool,
) -> dict[str, Any]:
    provider = build_model_provider(
        str(config["provider"]["name"]),
        command=config["provider"].get("command"),
        timeout_s=int(config.get("model_timeout_s", 120)),
        config=config,
    )
    stages = [str(item) for item in config.get("stages", []) or DEFAULT_STAGES]
    max_attempts = int(config.get("max_attempts", 3))

    while len(result.get("attempts", [])) < max_attempts:
        attempt_number = len(result.get("attempts", [])) + 1
        attempt_id = f"attempt-{attempt_number:03d}"
        attempt_dir = require_write_path(run_dir / attempt_id, purpose="attempt directory")
        attempt_dir.mkdir(parents=True, exist_ok=True)
        attempt_record = _new_attempt_record(attempt_id, provider.name)
        result.setdefault("attempts", []).append(attempt_record)
        _write_result(result_path, result)

        stage_outputs: dict[str, dict[str, Any]] = {}
        active_codegen_plan = config.get("external_codegen_plan") if isinstance(config.get("external_codegen_plan"), dict) else None
        try:
            for stage in stages:
                stage_output = _run_generation_stage(
                    run_dir=run_dir,
                    attempt_dir=attempt_dir,
                    attempt_id=attempt_id,
                    plan=plan,
                    stage=stage,
                    provider=provider,
                    config=config,
                    decision=decision,
                    previous_stage=stage_outputs.get(_previous_stage(stage, stages)),
                    active_codegen_plan=active_codegen_plan,
                    trace_path=trace_path,
                    state_path=state_path,
                    state_updates=state_updates,
                )
                stage_outputs[stage] = stage_output
                attempt_record.setdefault("stage_outputs", {})[stage] = stage_output["summary"]
                if stage == "codegen_plan":
                    active_codegen_plan = stage_output.get("codegen_plan")
                    if active_codegen_plan and (not active_codegen_plan.get("ready_for_generation", False) or active_codegen_plan.get("open_questions")):
                        return _block_for_human(attempt_record, result, result_path, attempt_dir, state_path, trace_path, active_codegen_plan, provider.name, state_updates)
                if stage == FINAL_STAGE:
                    attempt_record["prompt_path"] = stage_output["summary"]["prompt_path"]
                    attempt_record["response_path"] = stage_output["summary"]["response_path"]
                    attempt_record["artifact_dir"] = stage_output["summary"]["artifact_dir"]
                    attempt_record["stage"] = stage
                    result["last_attempt_id"] = attempt_id
                    _write_result(result_path, result)
        except ManualResponseRequired as exc:
            attempt_record["status"] = "invalid_response"
            attempt_record["error"] = str(exc)
            result["status"] = "invalid_response"
            _write_result(result_path, result)
            return result
        except (ExtractionError, ModelProviderError, SpecError, ValueError) as exc:
            attempt_record["status"] = "invalid_response" if isinstance(exc, ExtractionError) else "failed"
            attempt_record["error"] = str(exc)
            result["status"] = attempt_record["status"]
            _write_result(result_path, result)
            return result

        final_output = stage_outputs[FINAL_STAGE]
        validation_report = validate_generated(
            plan,
            final_output["artifact_dir"],
            target="hls",
            run_external=bool(config.get("run_external", True)),
            readiness=str(config.get("readiness", "execute")),
            comment_language=str(config.get("comment_language", "zh")),
            hls_profile=config.get("hls_profile") or {},
            reference_contract=stage_outputs.get("python", {}).get("reference_contract"),
        )
        validation_json_path = attempt_dir / "validation.json"
        write_json(validation_json_path, validation_report.to_dict())
        attempt_record["validation_json"] = safe_path(validation_json_path)
        _record_state(state_path, "validate", {"path": final_output["artifact_dir"], "output": validation_json_path, "readiness": config.get("readiness"), "ok": validation_report.ok()}, enabled=state_updates)
        append_trace_event(trace_path, {"event": "validate", "attempt_id": attempt_id, "target": "hls", "readiness": config.get("readiness"), "path": final_output["artifact_dir"], "ok": validation_report.ok(), "errors": validation_report.errors, "warnings": validation_report.warnings, "skips": validation_report.skips, "issues": [issue.to_dict() for issue in validation_report.issues], "metrics": validation_report.metrics or {}, "provider": provider.name})
        if _blocked_toolchain(validation_report):
            remote_request_path = _write_remote_toolchain_request(attempt_dir, attempt_id, config, validation_report)
            attempt_record["status"] = "blocked_toolchain"
            attempt_record["validation_summary"] = validation_report.format()
            attempt_record["remote_toolchain_request"] = safe_path(remote_request_path, run_dir)
            result["status"] = "blocked_toolchain"
            result["last_attempt_id"] = attempt_id
            result["remote_toolchain_request"] = safe_path(remote_request_path, run_dir)
            _write_result(result_path, result)
            _record_state(state_path, "workflow_attempt", {"attempt_id": attempt_id, "status": "blocked_toolchain", "validation_json": validation_json_path, "remote_toolchain_request": remote_request_path}, enabled=state_updates)
            append_trace_event(trace_path, {"event": "remote_toolchain_request", "attempt_id": attempt_id, "output": remote_request_path, "preferred_skill": "erie-remote-ssh"})
            return result

        interface_gate = _interface_gate(plan, stage_outputs, final_output, attempt_dir, trace_path)
        semantic_gate = _semantic_gate(plan, validation_report, stage_outputs, attempt_dir, trace_path)
        combined_gate = _combine_gate_results(interface_gate["result"] if interface_gate else None, semantic_gate["result"] if semantic_gate else None)
        attempt_record["contract_paths"] = dict(final_output["contract_paths"])
        if interface_gate:
            attempt_record["contract_paths"]["interface_gate"] = safe_path(interface_gate["path"])
        if semantic_gate:
            attempt_record["contract_paths"]["semantic_gate"] = safe_path(semantic_gate["path"])

        if validation_report.ok() and (combined_gate is None or combined_gate.get("ready", True)):
            attempt_record["status"] = "passed"
            result["status"] = "passed"
            result["last_attempt_id"] = attempt_id
            _write_result(result_path, result)
            _record_state(state_path, "workflow_attempt", {"attempt_id": attempt_id, "status": "passed", "validation_json": validation_json_path}, enabled=state_updates)
            return result

        attempt_record["status"] = "failed"
        attempt_record["validation_summary"] = validation_report.format()
        _write_result(result_path, result)

    result["status"] = "max_attempts"
    _write_result(result_path, result)
    return result


def _run_generation_stage(
    *,
    run_dir: Path,
    attempt_dir: Path,
    attempt_id: str,
    plan: dict[str, Any],
    stage: str,
    provider: Any,
    config: dict[str, Any],
    decision: dict[str, Any] | None,
    previous_stage: dict[str, Any] | None,
    active_codegen_plan: dict[str, Any] | None,
    trace_path: Path,
    state_path: Path,
    state_updates: bool,
) -> dict[str, Any]:
    stage_dir = attempt_dir / stage
    artifact_dir = stage_dir / "artifacts"
    stage_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest = _stage_manifest(plan, stage)
    if stage == "requirements":
        return _run_internal_json_stage(attempt_id, plan, stage, manifest, stage_dir, artifact_dir, trace_path, state_path, state_updates, build_requirements_payload(plan), "requirements")
    if stage == "codegen_plan" and active_codegen_plan is None:
        return _run_internal_json_stage(attempt_id, plan, stage, manifest, stage_dir, artifact_dir, trace_path, state_path, state_updates, build_codegen_plan(plan), "codegen_plan")
    if stage == "codegen_plan" and active_codegen_plan is not None:
        validate_codegen_plan_payload(plan, active_codegen_plan, require_ready=False)
        return _run_internal_json_stage(attempt_id, plan, stage, manifest, stage_dir, artifact_dir, trace_path, state_path, state_updates, active_codegen_plan, "codegen_plan")

    prompt_path = stage_dir / f"{stage}_prompt.md"
    response_path = stage_dir / f"{stage}_response.md"
    vector_contract = previous_stage.get("vector_contract") if previous_stage else None
    prompt_text = render_prompt(
        plan,
        target="hls",
        stage=stage,
        context_manifest=previous_stage.get("manifest") if previous_stage else None,
        context_dir=previous_stage.get("artifact_dir") if previous_stage else None,
        evidence=None,
        memory=None,
        comment_language=str(config.get("comment_language", "zh")),
        vector_contract=vector_contract,
        codegen_plan=active_codegen_plan,
        budget="normal",
        hls_profile=config.get("hls_profile") or {},
        decision=decision,
    )
    write_text(prompt_path, prompt_text)
    append_trace_event(trace_path, {"event": "prompt", "attempt_id": attempt_id, "target": "hls", "stage": stage, "spec": spec_summary(plan), "output": prompt_path, "provider": provider.name})
    response_text = provider.generate(
        prompt_text,
        GenerationContext(attempt_id, stage, prompt_path, response_path, run_dir, attempt_dir, plan, manifest, config, vector_contract=vector_contract, comment_language=str(config.get("comment_language", "zh"))),
    )
    write_text(response_path, response_text)
    written = extract_response(response_text, artifact_dir)
    append_trace_event(trace_path, {"event": "extract", "attempt_id": attempt_id, "response": response_path, "out_dir": artifact_dir, "written_files": [safe_path(path) for path in written]})
    output: dict[str, Any] = {"stage": stage, "prompt_path": prompt_path, "response_path": response_path, "artifact_dir": artifact_dir, "manifest": manifest, "contract_paths": {}, "summary": {"prompt_path": safe_path(prompt_path), "response_path": safe_path(response_path), "artifact_dir": safe_path(artifact_dir)}}
    if stage == "python":
        reference_contract = audit_reference(artifact_dir)
        reference_contract_path = stage_dir / "reference_contract.json"
        write_json(reference_contract_path, reference_contract)
        python_contract = audit_interface("python", artifact_dir)
        python_contract_path = stage_dir / "python_interface.json"
        write_json(python_contract_path, python_contract)
        vector_path = next((path for path in written if path.name.endswith("_vectors.json")), None)
        vector_contract_payload = audit_vectors(vector_path) if vector_path is not None else None
        if vector_contract_payload is not None:
            vector_contract_path = stage_dir / "vector_contract.json"
            write_json(vector_contract_path, vector_contract_payload)
            output["vector_contract"] = vector_contract_payload
            output["contract_paths"]["vector_contract"] = safe_path(vector_contract_path)
        output.update({"reference_contract": reference_contract, "python_contract": python_contract})
        output["contract_paths"].update({"reference_contract": safe_path(reference_contract_path), "python_interface": safe_path(python_contract_path)})
    elif stage == "hls":
        interface_contract = audit_interface("hls", artifact_dir)
        interface_contract_path = stage_dir / "hls_interface.json"
        write_json(interface_contract_path, interface_contract)
        output["interface_contract"] = interface_contract
        output["contract_paths"]["hls_interface"] = safe_path(interface_contract_path)
    return output


def _run_internal_json_stage(
    attempt_id: str,
    plan: dict[str, Any],
    stage: str,
    manifest: dict[str, Any],
    stage_dir: Path,
    artifact_dir: Path,
    trace_path: Path,
    state_path: Path,
    state_updates: bool,
    payload: dict[str, Any],
    payload_key: str,
) -> dict[str, Any]:
    prompt_path = stage_dir / f"{stage}_prompt.md"
    response_path = stage_dir / f"{stage}_response.md"
    write_text(prompt_path, f"# Internal {stage} stage\n\nThis stage is synthesized from confirmed HLS inputs.\n")
    file_entry = manifest["files"][0]
    artifact_path = artifact_dir / Path(*Path(str(file_entry["path"])).parts)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(artifact_path, payload)
    response_text = "```json\n" + json.dumps(manifest, indent=2, ensure_ascii=False) + "\n```\n" + f"```json path={file_entry['path']}\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n```\n"
    write_text(response_path, response_text)
    append_trace_event(trace_path, {"event": "prompt", "attempt_id": attempt_id, "target": "hls", "stage": stage, "spec": spec_summary(plan), "output": prompt_path, "provider": "internal"})
    _record_state(state_path, "extract", {"response": response_path, "out_dir": artifact_dir, "written_files": [artifact_path]}, enabled=state_updates)
    output = {"stage": stage, "prompt_path": prompt_path, "response_path": response_path, "artifact_dir": artifact_dir, "manifest": manifest, "contract_paths": {}, "summary": {"prompt_path": safe_path(prompt_path), "response_path": safe_path(response_path), "artifact_dir": safe_path(artifact_dir), "artifact_path": safe_path(artifact_path)}, payload_key: payload}
    return output


def _block_for_human(attempt_record: dict[str, Any], result: dict[str, Any], result_path: Path, attempt_dir: Path, state_path: Path, trace_path: Path, codegen_plan: dict[str, Any], provider_name: str, state_updates: bool) -> dict[str, Any]:
    intervention_path = attempt_dir / "intervention.json"
    intervention = {"version": 1, "action": "ask_human", "primary_source": "needs_human_intervention", "question": str((codegen_plan.get("open_questions") or ["Confirm the remaining HLS requirements."])[0]), "observations": codegen_plan.get("open_questions", []), "expected_answer_format": {"decision": "one concise HLS design decision", "constraints": "interface or pipeline constraints to preserve"}}
    write_json(intervention_path, intervention)
    attempt_record["intervention_path"] = safe_path(intervention_path)
    attempt_record["status"] = "blocked_human"
    result["status"] = "blocked_human"
    _write_result(result_path, result)
    _record_state(state_path, "human_intervention", {"output": intervention_path, "attempt_id": attempt_record["attempt_id"], "primary_source": "needs_human_intervention"}, enabled=state_updates)
    append_trace_event(trace_path, {"event": "human_intervention", "attempt_id": attempt_record["attempt_id"], "output": intervention_path, "primary_source": "needs_human_intervention", "provider": provider_name})
    return result


def _apply_human_decision_to_plan(plan: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    resolved = copy.deepcopy(plan)
    workflow = copy.deepcopy(resolved.get("workflow", {})) if isinstance(resolved.get("workflow"), dict) else {}
    override = copy.deepcopy(workflow.get("codegen_plan_override", {})) if isinstance(workflow.get("codegen_plan_override"), dict) else {}
    if override.get("open_questions") or override.get("ready_for_generation") is False:
        override["open_questions"] = []
        override["ready_for_generation"] = True
        override["human_resolution"] = {
            "decision": decision.get("decision"),
            "constraints": decision.get("constraints", []),
            "evidence": decision.get("evidence", []),
        }
        workflow["codegen_plan_override"] = override
        resolved["workflow"] = workflow
    return resolved


def _apply_human_decision_to_codegen_plan(codegen_plan: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    resolved = copy.deepcopy(codegen_plan)
    if resolved.get("open_questions") or resolved.get("ready_for_generation") is False:
        resolved["open_questions"] = []
        resolved["ready_for_generation"] = True
        resolved["human_resolution"] = {
            "decision": decision.get("decision"),
            "constraints": decision.get("constraints", []),
            "evidence": decision.get("evidence", []),
        }
    return resolved


def _blocked_toolchain(validation_report: Any) -> bool:
    for issue in getattr(validation_report, "issues", []) or []:
        if getattr(issue, "severity", None) != "error" or getattr(issue, "source", None) != "toolchain_issue":
            continue
        if getattr(issue, "tool", None) in vitis_blocking_tool_ids():
            return True
    return False


def _write_remote_toolchain_request(attempt_dir: Path, attempt_id: str, config: dict[str, Any], validation_report: Any) -> Path:
    path = attempt_dir / "remote_toolchain_request.json"
    readiness = str(config.get("readiness", "execute"))
    vitis_skill = resolve_vitis_skill_preference()
    request = {
        "version": 1,
        "action": "ask_remote_server",
        "primary_source": "local_vitis_missing",
        "preferred_skill": "erie-remote-ssh",
        "vitis_skill_routing": vitis_skill,
        "question": "Local Vitis HLS tools were not found. Ask the user to choose a configured erie-remote-ssh server with Vitis/Vivado available, then run remote HLS validation there.",
        "attempt_id": attempt_id,
        "readiness": readiness,
        "local_toolchain_errors": [issue.to_dict() for issue in getattr(validation_report, "issues", []) if getattr(issue, "source", None) == "toolchain_issue"],
        "erie_remote_ssh": {
            "selection_commands": [
                "python <erie-skill-dir>\\scripts\\remote_ssh.py discover --settings <erie-settings.json>",
                "python <erie-skill-dir>\\scripts\\remote_ssh.py choices --settings <erie-settings.json>",
                "python <erie-skill-dir>\\scripts\\remote_ssh.py check --settings <erie-settings.json> --server <erie-server>",
                "python <erie-skill-dir>\\scripts\\remote_ssh.py workspace-check --settings <erie-settings.json> --server <erie-server>",
                "python <erie-skill-dir>\\scripts\\remote_ssh.py scan-software --settings <erie-settings.json> --server <erie-server>",
                "python <erie-skill-dir>\\scripts\\remote_ssh.py software --settings <erie-settings.json> --server <erie-server> --name vitis",
            ],
            "user_decision_required": "Select one enabled erie server id or name before any SSH execution.",
        },
        "hls_generator_remote_commands": [
            "python .\\scripts\\python\\remote\\remote_vitis_acceptance.py --mode link --server <erie-server> --json",
            f"python .\\scripts\\python\\remote\\remote_vitis_acceptance.py --mode vitis --server <erie-server> --readiness {readiness} --json",
        ],
        "remote_artifact_policy": {
            "default": "retain",
            "location": "The helper reports `remote_dir`, relative to the selected erie server workdir.",
            "cleanup_override": "Pass --cleanup-remote only when the user explicitly wants the remote validation directory deleted after success.",
        },
        "remote_vitis_version_policy": {
            "default": "scan_and_require_choice_when_multiple",
            "user_config_path": "~/.hls-generator/config.json",
            "selection_override": "Pass --vitis-version <version> to save and use a specific remote Vitis version for the selected server.",
        },
        "expected_next_step": f"Use erie-remote-ssh discovery/choices first; use {vitis_skill['selected_skill']} for Vitis flow guidance when available; after the user selects a server, run scan-software and the HLS remote acceptance helper. If multiple Vitis versions are detected, ask the user to choose one before continuing. By default, inspect the retained remote_dir under the selected server workdir after Vitis validation.",
    }
    write_json(path, request)
    return path


def _interface_gate(plan: dict[str, Any], stage_outputs: dict[str, dict[str, Any]], final_output: dict[str, Any], attempt_dir: Path, trace_path: Path) -> dict[str, Any] | None:
    python_contract = stage_outputs.get("python", {}).get("python_contract")
    interface_contract = final_output.get("interface_contract")
    if not python_contract or not interface_contract:
        return None
    result = verify_stage(plan, python_contract, interface_contract)
    path = attempt_dir / "interface_gate.json"
    write_json(path, result)
    append_trace_event(trace_path, {"event": "verify_stage", "attempt_id": attempt_dir.name, "output": path, "ready": result.get("ready"), "issues": result.get("issues", [])})
    return {"path": path, "result": result}


def _semantic_gate(plan: dict[str, Any], validation_report: Any, stage_outputs: dict[str, dict[str, Any]], attempt_dir: Path, trace_path: Path) -> dict[str, Any] | None:
    reference_contract = stage_outputs.get("python", {}).get("reference_contract")
    if not reference_contract or not validation_report.metrics:
        return None
    result = verify_stage(plan, reference_contract, {"metrics": validation_report.metrics, "case_ids": reference_contract.get("case_ids", [])})
    path = attempt_dir / "semantic_gate.json"
    write_json(path, result)
    append_trace_event(trace_path, {"event": "verify_stage", "attempt_id": attempt_dir.name, "output": path, "ready": result.get("ready"), "issues": result.get("issues", [])})
    return {"path": path, "result": result}


def _combine_gate_results(interface_gate: dict[str, Any] | None, semantic_gate: dict[str, Any] | None) -> dict[str, Any] | None:
    if interface_gate is None and semantic_gate is None:
        return None
    issues: list[dict[str, Any]] = []
    error_sources: list[str] = []
    ready = True
    for gate in [interface_gate, semantic_gate]:
        if not gate:
            continue
        issues.extend(issue for issue in gate.get("issues", []) or [] if issue not in issues)
        for source in gate.get("error_sources", []) or []:
            if source not in error_sources:
                error_sources.append(source)
        if gate.get("ready") is False:
            ready = False
    return {"version": 1, "ready": ready, "issues": issues, "error_sources": error_sources, "recommended_action": "regenerate_current"}


def _workflow_config(plan: dict[str, Any], *, provider_name: str, provider_command: str | None, readiness: str, max_attempts: int, stop_on_human: bool, run_external: bool, comment_language: str, hls_profile: dict[str, Any], external_codegen_plan: dict[str, Any] | None, model_timeout_s: int) -> dict[str, Any]:
    return {"version": 1, "name": plan["name"], "target": "hls", "design_requirements": copy.deepcopy(plan.get("design_requirements", {})), "streamability": plan.get("streamability"), "interface_family": plan.get("interface_family"), "interface_profile": copy.deepcopy(plan.get("interface_profile", {})), "pipeline_required": bool(plan.get("pipeline_required", True)), "codegen_plan_required": bool(plan.get("codegen_plan_required", True)), "codegen_plan_path": plan.get("codegen_plan_path"), "stages": list(DEFAULT_STAGES), "readiness": readiness, "max_attempts": max_attempts, "stop_on_human": stop_on_human, "run_external": run_external, "comment_language": comment_language, "hls_profile": hls_profile, "external_codegen_plan": copy.deepcopy(external_codegen_plan) if isinstance(external_codegen_plan, dict) else None, "model_timeout_s": model_timeout_s, "provider": {"name": provider_name, "command": provider_command}, "budgets": {stage: "normal" for stage in DEFAULT_STAGES}, "mock_behavior": (plan.get("workflow") or {}).get("mock_behavior")}


def _stage_manifest(plan: dict[str, Any], stage: str) -> dict[str, Any]:
    return _stage_manifest_for(plan, stage) if stage else _manifest_for(plan)


def _new_attempt_record(attempt_id: str, provider: str) -> dict[str, Any]:
    return {"attempt_id": attempt_id, "stage": FINAL_STAGE, "prompt_path": None, "response_path": None, "artifact_dir": None, "validation_json": None, "contract_paths": {}, "status": "failed", "provider": provider}


def _write_result(path: Path, result: dict[str, Any]) -> None:
    if result.get("status") not in WORKFLOW_STATUSES and result.get("attempts"):
        raise WorkflowError(f"Workflow status must be one of {', '.join(WORKFLOW_STATUSES)}.")
    write_json(path, result)


def _previous_stage(stage: str, stages: list[str]) -> str | None:
    try:
        index = stages.index(stage)
    except ValueError:
        return None
    return stages[index - 1] if index > 0 else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    json_path = require_workspace_path(path, purpose="JSON path", must_exist=True)
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Invalid JSON in {json_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError(f"Expected JSON object in {json_path}.")
    return data


def _record_state(state_path: Path, event: str, payload: dict[str, Any], *, enabled: bool) -> None:
    update_workflow_state(state_path, event, payload, enabled=enabled)


def _resolve_external_codegen_plan(spec: dict[str, Any], spec_file: Path) -> dict[str, Any] | None:
    raw_path = spec.get("codegen_plan_path")
    if not raw_path:
        return None
    plan_path = require_workspace_path_from(spec_file, Path(str(raw_path)), purpose="codegen plan path", must_exist=True)
    payload = _read_json(plan_path)
    validate_codegen_plan_payload(spec, payload, require_ready=False)
    return payload
