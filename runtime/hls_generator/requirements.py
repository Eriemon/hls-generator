"""HLS requirement confirmation and code-generation planning helpers."""

from __future__ import annotations

import copy
from typing import Any

from .patterns import pattern_definition, pattern_open_questions

STREAMABILITY_VALUES = ("streamable", "non_streamable", "unknown")
INTERFACE_FAMILIES = ("native", "axi_stream", "axi4", "custom")
AXI4_VARIANTS = ("axi4_full", "axi4_lite")
AXI4_ROLES = ("master", "slave")
AXI4_MODES = ("read", "write", "read_write")
AXI_STREAM_PROFILE_KEYS = ("keep_ready", "keep_last", "data_width")
AXI4_PROFILE_KEYS = (
    "axi4_variant",
    "role",
    "read_write_mode",
    "data_width",
    "addr_width",
    "id_width",
    "burst_support",
    "max_burst_len",
)
STREAM_KEYWORDS = (
    "stream",
    "packet",
    "frame",
    "sample",
    "line",
    "token",
    "vector",
    "sequence",
    "throughput",
    "ii",
    "pipeline",
    "valid",
    "ready",
    "last",
)


def detect_streamability(spec: dict[str, Any], evidence: dict[str, Any] | None = None) -> str:
    explicit = spec.get("streamability")
    if explicit in STREAMABILITY_VALUES:
        return str(explicit)
    requirements = spec.get("design_requirements")
    if isinstance(requirements, dict) and requirements.get("streamability") in STREAMABILITY_VALUES:
        return str(requirements["streamability"])

    fragments: list[str] = []
    for key in ("description",):
        value = spec.get(key)
        if isinstance(value, str):
            fragments.append(value)
    for key in ("behavior", "constraints", "notes"):
        for item in spec.get(key, []) or []:
            fragments.append(str(item.get("text") if isinstance(item, dict) else item))
    interfaces = spec.get("interfaces", {}) if isinstance(spec.get("interfaces"), dict) else {}
    for item in interfaces.get("arguments", []) or []:
        if isinstance(item, dict):
            fragments.extend(str(value) for value in item.values())
    if evidence:
        for item in evidence.get("items", [])[:12]:
            if isinstance(item, dict) and item.get("text"):
                fragments.append(str(item["text"]))

    blob = " ".join(fragment.lower() for fragment in fragments)
    if "m_axi" in blob or "axis" in blob or "axi-stream" in blob:
        return "streamable"
    if any(keyword in blob for keyword in STREAM_KEYWORDS):
        return "streamable"
    return "non_streamable"


def apply_requirement_defaults(
    raw_spec: dict[str, Any],
    *,
    design_requirements: dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: dict[str, Any] | None = None,
    confirmation_notes: str | None = None,
    confirmed_by_user: bool | None = None,
) -> dict[str, Any]:
    spec = copy.deepcopy(raw_spec)
    base = copy.deepcopy(spec.get("design_requirements", {})) if isinstance(spec.get("design_requirements"), dict) else {}
    if design_requirements:
        base.update(copy.deepcopy(design_requirements))

    resolved_streamability = streamability or base.get("streamability") or spec.get("streamability") or detect_streamability(spec)
    resolved_interface_family = interface_family or base.get("interface_family") or spec.get("interface_family")
    resolved_profile = copy.deepcopy(spec.get("interface_profile", {})) if isinstance(spec.get("interface_profile"), dict) else {}
    if isinstance(base.get("interface_profile"), dict):
        resolved_profile.update(copy.deepcopy(base["interface_profile"]))
    if interface_profile:
        resolved_profile.update(copy.deepcopy(interface_profile))
    resolved_pipeline = pipeline_required if pipeline_required is not None else base.get("pipeline_required", spec.get("pipeline_required", True))
    resolved_confirmed = bool(confirmed_by_user) if confirmed_by_user is not None else bool(base.get("confirmed_by_user", False))
    resolved_notes = confirmation_notes if confirmation_notes is not None else str(base.get("confirmation_notes", "") or "")

    spec["target"] = "hls"
    spec["pipeline_required"] = bool(resolved_pipeline)
    spec["streamability"] = str(resolved_streamability)
    spec["interface_family"] = resolved_interface_family
    spec["interface_profile"] = resolved_profile
    spec["codegen_plan_required"] = bool(spec.get("codegen_plan_required", True))
    spec["design_requirements"] = {
        "target": "hls",
        "pipeline_required": bool(resolved_pipeline),
        "streamability": str(resolved_streamability),
        "interface_family": resolved_interface_family,
        "interface_profile": resolved_profile,
        "confirmed_by_user": resolved_confirmed,
        "confirmation_notes": resolved_notes,
    }
    return spec


def validate_requirement_confirmation(spec: dict[str, Any]) -> None:
    issues = _requirement_confirmation_issues(spec, require_confirmed=True)
    if issues:
        raise ValueError(issues[0])


def validate_codegen_plan_payload(
    spec: dict[str, Any],
    payload: dict[str, Any],
    *,
    require_ready: bool,
) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Explicit codegen_plan_path must point to a JSON object.")
    if payload.get("version") != 1:
        raise ValueError("Explicit codegen plan must use version=1.")
    if payload.get("name") != spec.get("name"):
        raise ValueError("Explicit codegen plan name must match spec.name.")
    if payload.get("target") != "hls":
        raise ValueError("Explicit codegen plan target must be `hls`.")
    for field in ("interface_decision", "pipeline_strategy", "verification_strategy"):
        if not isinstance(payload.get(field), dict):
            raise ValueError(f"Explicit codegen plan must include object field `{field}`.")
    if not isinstance(payload.get("open_questions", []), list):
        raise ValueError("Explicit codegen plan open_questions must be a list.")
    if not isinstance(payload.get("ready_for_generation"), bool):
        raise ValueError("Explicit codegen plan ready_for_generation must be a boolean.")
    if require_ready and (payload.get("ready_for_generation") is not True or payload.get("open_questions")):
        blockers = payload.get("open_questions", []) or ["Confirm the remaining HLS design requirements."]
        raise ValueError("Explicit codegen plan is not ready for generation: " + "; ".join(str(item) for item in blockers))


def build_requirements_payload(spec: dict[str, Any]) -> dict[str, Any]:
    requirements = copy.deepcopy(spec.get("design_requirements", {})) if isinstance(spec.get("design_requirements"), dict) else {}
    return {
        "version": 1,
        "name": spec.get("name"),
        "target": "hls",
        "pipeline_required": bool(spec.get("pipeline_required", True)),
        "streamability": spec.get("streamability"),
        "interface_family": spec.get("interface_family"),
        "interface_profile": copy.deepcopy(spec.get("interface_profile", {})) if isinstance(spec.get("interface_profile"), dict) else {},
        "requirements_summary": _requirements_summary(spec),
        "design_requirements": requirements,
        "confirmed_by_user": requirements.get("confirmed_by_user") is True,
    }


def build_codegen_plan(spec: dict[str, Any]) -> dict[str, Any]:
    requirements = build_requirements_payload(spec)
    open_questions = _codegen_open_questions(spec)
    plan = {
        "version": 1,
        "name": spec.get("name"),
        "target": "hls",
        "requirements_summary": requirements["requirements_summary"],
        "interface_decision": {
            "family": spec.get("interface_family"),
            "profile": copy.deepcopy(spec.get("interface_profile", {})) if isinstance(spec.get("interface_profile"), dict) else {},
            "confirmed": bool((spec.get("design_requirements") or {}).get("confirmed_by_user")),
        },
        "pipeline_strategy": {
            "required": bool(spec.get("pipeline_required", True)),
            "strategy": "pipeline_required" if spec.get("pipeline_required", True) else "pipeline_optional",
            "notes": "Use HLS PIPELINE/DATAFLOW only where it matches dependencies and memory bandwidth.",
        },
        "module_partition": {
            "top": spec.get("interfaces", {}).get("top_function") or spec.get("name"),
            "subfunctions": [item.get("name") for item in spec.get("subfunctions", []) if isinstance(item, dict)] or [spec.get("name")],
            "decomposition_strategy": "Keep HLS helper functions explicit and synthesizable.",
        },
        "signal_width_strategy": {
            "policy": "Use ap_int/ap_uint/ap_fixed or scalar C++ types that preserve the required numeric range.",
        },
        "reset_clock_strategy": {
            "clock": copy.deepcopy(spec.get("clock", {})) if isinstance(spec.get("clock"), dict) else {},
            "reset": copy.deepcopy(spec.get("reset", {})) if isinstance(spec.get("reset"), dict) else {},
        },
        "verification_strategy": {
            "python_reference_required": True,
            "self_checking_hls_testbench_required": True,
            "vitis_readiness_required": True,
        },
        "syntax_risk_checks": _syntax_risk_checks(spec),
        "open_questions": open_questions,
        "ready_for_generation": not open_questions,
    }
    override = (spec.get("workflow") or {}).get("codegen_plan_override") if isinstance(spec.get("workflow"), dict) else None
    if isinstance(override, dict):
        plan.update(copy.deepcopy(override))
        plan.setdefault("open_questions", open_questions)
        plan.setdefault("ready_for_generation", not plan.get("open_questions"))
    return plan


def _requirements_summary(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": "hls",
        "pipeline_required": bool(spec.get("pipeline_required", True)),
        "streamability": spec.get("streamability"),
        "interface_family": spec.get("interface_family"),
        "top_function": spec.get("interfaces", {}).get("top_function"),
        "confirmation_notes": (spec.get("design_requirements") or {}).get("confirmation_notes", ""),
    }


def _requirement_confirmation_issues(spec: dict[str, Any], *, require_confirmed: bool) -> list[str]:
    requirements = spec.get("design_requirements")
    if not isinstance(requirements, dict):
        return ["Generation calls require a `design_requirements` object."] if require_confirmed else []
    issues: list[str] = []
    if requirements.get("target") != "hls" or spec.get("target") != "hls":
        issues.append("HLS generator accepts only target=`hls`.")
    if require_confirmed and requirements.get("confirmed_by_user") is not True:
        issues.append("Generation calls require design_requirements.confirmed_by_user=true.")
    if not isinstance(requirements.get("pipeline_required"), bool):
        issues.append("design_requirements.pipeline_required must be a boolean.")
    elif bool(requirements["pipeline_required"]) != bool(spec.get("pipeline_required", True)):
        issues.append("design_requirements.pipeline_required must match spec.pipeline_required.")
    streamability = requirements.get("streamability")
    if streamability not in STREAMABILITY_VALUES:
        issues.append(f"streamability must be one of {', '.join(STREAMABILITY_VALUES)}.")
    elif str(streamability) != str(spec.get("streamability")):
        issues.append("design_requirements.streamability must match spec.streamability.")
    interface_family = requirements.get("interface_family")
    if interface_family is not None and interface_family not in INTERFACE_FAMILIES:
        issues.append(f"interface_family must be one of {', '.join(INTERFACE_FAMILIES)}.")
    elif interface_family != spec.get("interface_family"):
        issues.append("design_requirements.interface_family must match spec.interface_family.")
    profile = requirements.get("interface_profile", {})
    if not isinstance(profile, dict):
        issues.append("design_requirements.interface_profile must be an object.")
        return issues
    if profile != spec.get("interface_profile", {}):
        issues.append("design_requirements.interface_profile must match spec.interface_profile.")
    if streamability == "streamable" and not interface_family:
        issues.append("Streamable tasks require an explicit interface_family confirmation before generation.")
    issues.extend(_interface_profile_issues(interface_family, profile, strict=require_confirmed))
    return issues


def _interface_profile_issues(interface_family: Any, profile: dict[str, Any], *, strict: bool) -> list[str]:
    issues: list[str] = []
    if interface_family == "custom" and not profile:
        issues.append("Custom HLS interfaces require a non-empty interface_profile.")
    if interface_family == "native":
        forbidden = sorted(key for key in profile if key in {*AXI_STREAM_PROFILE_KEYS, *AXI4_PROFILE_KEYS})
        if forbidden:
            issues.append("Native interfaces must not use AXI-specific keys: " + ", ".join(forbidden) + ".")
    if strict and interface_family == "axi_stream":
        for key in ("keep_ready", "keep_last"):
            if not isinstance(profile.get(key), bool):
                issues.append(f"AXI-Stream interface_profile requires boolean `{key}`.")
        if not isinstance(profile.get("data_width"), int) or int(profile["data_width"]) <= 0:
            issues.append("AXI-Stream interface_profile requires a positive integer `data_width`.")
    if strict and interface_family == "axi4":
        if profile.get("axi4_variant") not in AXI4_VARIANTS:
            issues.append(f"AXI4 interface_profile requires `axi4_variant` in {', '.join(AXI4_VARIANTS)}.")
        if profile.get("role") not in AXI4_ROLES:
            issues.append(f"AXI4 interface_profile requires `role` in {', '.join(AXI4_ROLES)}.")
        if profile.get("read_write_mode") not in AXI4_MODES:
            issues.append(f"AXI4 interface_profile requires `read_write_mode` in {', '.join(AXI4_MODES)}.")
        for key in ("data_width", "addr_width"):
            if not isinstance(profile.get(key), int) or int(profile[key]) <= 0:
                issues.append(f"AXI4 interface_profile requires a positive integer `{key}`.")
        if profile.get("axi4_variant") == "axi4_full" and (not isinstance(profile.get("id_width"), int) or int(profile["id_width"]) <= 0):
            issues.append("AXI4 full interface_profile requires a positive integer `id_width`.")
        if not isinstance(profile.get("burst_support"), bool):
            issues.append("AXI4 interface_profile requires boolean `burst_support`.")
        if profile.get("burst_support") and (not isinstance(profile.get("max_burst_len"), int) or int(profile["max_burst_len"]) <= 0):
            issues.append("AXI4 interface_profile requires positive integer `max_burst_len` when burst_support=true.")
    return issues


def _codegen_open_questions(spec: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    if not (spec.get("design_requirements") or {}).get("confirmed_by_user"):
        questions.append("Confirm the HLS target, pipeline requirement, and interface choice with the user.")
    if spec.get("streamability") == "streamable" and not spec.get("interface_family"):
        questions.append("Confirm whether the streamable HLS task should use AXI-Stream, AXI4, native, or custom interfaces.")
    profile = spec.get("interface_profile", {}) if isinstance(spec.get("interface_profile"), dict) else {}
    if spec.get("interface_family") == "axi_stream":
        for key in ("keep_ready", "keep_last", "data_width"):
            if key not in profile:
                questions.append(f"Confirm the AXI-Stream `{key}` field.")
    if spec.get("interface_family") == "axi4":
        for key in ("axi4_variant", "role", "read_write_mode", "data_width", "addr_width", "burst_support"):
            if key not in profile:
                questions.append(f"Confirm the AXI4 `{key}` field.")
        if profile.get("axi4_variant") == "axi4_full" and "id_width" not in profile:
            questions.append("Confirm the AXI4 full id width.")
        if profile.get("burst_support") is True and "max_burst_len" not in profile:
            questions.append("Confirm the AXI4 maximum burst length.")
    for question in pattern_open_questions(spec):
        if question not in questions:
            questions.append(question)
    for issue in _requirement_confirmation_issues(spec, require_confirmed=False):
        if issue not in questions:
            questions.append(issue)
    return questions


def _syntax_risk_checks(spec: dict[str, Any]) -> list[str]:
    checks = [
        "Reject placeholder text, undefined symbols, missing output artifacts, and non-HLS source extensions.",
        "Keep the HLS implementation aligned with the Python oracle and reference vectors.",
        "Keep hls_config.cfg syn.top and syn.file entries exact.",
    ]
    if spec.get("pipeline_required", True):
        checks.append("Require at least one justified #pragma HLS PIPELINE when pipeline_required=true.")
    if spec.get("interface_family") == "axi_stream":
        checks.append("Preserve confirmed AXI-Stream ready/last/data-width semantics.")
    if spec.get("interface_family") == "axi4":
        checks.append("Preserve confirmed AXI4 variant, role, widths, and burst policy.")
    definition = pattern_definition(spec)
    if definition.get("label"):
        checks.append(f"Preserve the confirmed {definition['label']} pattern metadata and keep comments aligned with it.")
    return checks
