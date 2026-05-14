"""Prompt rendering for AMD-Xilinx/Vitis HLS generation."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .config import resolve_vitis_skill_preference
from .spec import normalize_spec
from .user_config import COMMENT_LANGUAGES, require_comment_language
from .vectors import VECTOR_HASH_TAG

PROMPT_STAGES = ("requirements", "codegen_plan", "tests", "python", "hls")
COMMENT_LANGUAGE_CHOICES = ("auto", *COMMENT_LANGUAGES)
PROMPT_BUDGETS = ("normal", "compact", "repair")


def render_prompt(
    spec: dict[str, Any],
    target: str | None = None,
    stage: str | None = None,
    *,
    context_manifest: dict[str, Any] | None = None,
    context_dir: Path | None = None,
    evidence: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    comment_language: str = "zh",
    vector_contract: dict[str, Any] | None = None,
    codegen_plan: dict[str, Any] | None = None,
    subfunction: str | None = None,
    budget: str = "normal",
    hls_profile: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
) -> str:
    del subfunction
    normalized = normalize_spec(spec, target=target)
    comment_language = require_comment_language(comment_language)
    budget = require_prompt_budget(budget)
    if stage:
        return _render_staged_prompt(
            normalized,
            _require_stage(stage),
            context_manifest=context_manifest,
            context_dir=context_dir,
            evidence=evidence,
            memory=memory,
            comment_language=comment_language,
            vector_contract=vector_contract,
            codegen_plan=codegen_plan,
            budget=budget,
            hls_profile=hls_profile,
            decision=decision,
        )
    return _render_hls_prompt(normalized, comment_language, hls_profile=hls_profile, decision=decision)


def require_prompt_budget(budget: str) -> str:
    normalized = budget.lower()
    if normalized not in PROMPT_BUDGETS:
        raise ValueError(f"Prompt budget must be one of {', '.join(PROMPT_BUDGETS)}.")
    return normalized


def _require_stage(stage: str) -> str:
    normalized = stage.lower()
    if normalized not in PROMPT_STAGES:
        raise ValueError("This skill is HLS-only; stage must be one of " + ", ".join(PROMPT_STAGES) + ".")
    return normalized


def _render_hls_prompt(
    spec: dict[str, Any],
    comment_language: str,
    *,
    hls_profile: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
) -> str:
    return _append_optional_sections(
        _base_prompt(
            spec=spec,
            title="Vitis HLS generation task",
            target_line="Generate AMD-Xilinx Vitis HLS compatible C/C++ artifacts only.",
            rules=_hls_rules(spec, comment_language, hls_profile or spec.get("hls_profile") or {}),
            manifest=_manifest_for(spec),
        ),
        hls_profile=hls_profile or spec.get("hls_profile") or {},
        decision=decision,
    )


def _render_staged_prompt(
    spec: dict[str, Any],
    stage: str,
    *,
    context_manifest: dict[str, Any] | None,
    context_dir: Path | None,
    evidence: dict[str, Any] | None,
    memory: dict[str, Any] | None,
    comment_language: str,
    vector_contract: dict[str, Any] | None,
    codegen_plan: dict[str, Any] | None,
    budget: str,
    hls_profile: dict[str, Any] | None,
    decision: dict[str, Any] | None,
) -> str:
    manifest = _stage_manifest_for(spec, stage)
    stage_title, stage_goal, stage_rules = _stage_guidance(spec, stage, comment_language, vector_contract, hls_profile or {})
    prompt = f"""# {stage_title}

You are executing an HLS-only staged generator. Stage goal: {stage_goal}
Think internally, then return only the requested fenced blocks.
Prompt budget: {budget}.

## HLS spec

```json
{json.dumps(spec, indent=2, ensure_ascii=False)}
```

## Stage rules

{chr(10).join(f"- {rule}" for rule in stage_rules)}

## Prior artifact context

```json
{json.dumps(_artifact_context(context_manifest, context_dir, budget=budget), indent=2, ensure_ascii=False)}
```

## Evidence context

```json
{json.dumps(evidence or {}, indent=2, ensure_ascii=False)}
```

## Prompt memory constraints

```json
{json.dumps(_memory_constraints(memory, stage, budget=budget), indent=2, ensure_ascii=False)}
```

## Code generation plan

```json
{json.dumps(codegen_plan or {}, indent=2, ensure_ascii=False)}
```

## Reference vector contract

```json
{json.dumps(vector_contract or {}, indent=2, ensure_ascii=False)}
```

## HLS profile constraints

```json
{json.dumps(hls_profile or {}, indent=2, ensure_ascii=False)}
```

## Human decision constraints

```json
{json.dumps(decision or {}, indent=2, ensure_ascii=False)}
```

## Output contract

Return only fenced code blocks: first the manifest JSON, then one file block per manifest file.
Every file block must use `path=<relative/path>`, and every path must match the manifest exactly.

```json
{json.dumps(manifest, indent=2, ensure_ascii=False)}
```
"""
    return prompt


def _base_prompt(*, spec: dict[str, Any], title: str, target_line: str, rules: list[str], manifest: dict[str, Any]) -> str:
    return f"""# {title}

You are an expert AMD-Xilinx HLS design generator. {target_line}
Do not generate Verilog or SystemVerilog. Do not output analysis.

## Generation spec

```json
{json.dumps(spec, indent=2, ensure_ascii=False)}
```

## Design rules

{chr(10).join(f"- {rule}" for rule in rules)}

## Output contract

Return only fenced code blocks: first the manifest JSON, then one file block per manifest file.
The manifest must preserve the `files` array exactly and may fill the `checks` arrays with concise strings.

```json
{json.dumps(manifest, indent=2, ensure_ascii=False)}
```

Then return one fenced code block for every manifest file, and no extra file blocks. Put the exact relative file path in the fence info as `path=<relative/path>`.

Path rules:

- Every manifest path must have exactly one matching code fence.
- Every code fence path must appear in the manifest.
- Paths must be relative, unique, case-exact, slash-exact, and must not contain `..`.

Example fence header:

```cpp path=src/example_kernel.cpp
```
"""


def _hls_rules(spec: dict[str, Any], comment_language: str, hls_profile: dict[str, Any]) -> list[str]:
    rules = [
        "Target Vitis HLS 2024.2 compatible C/C++ and script/config artifacts.",
        "Use the stable Tcl/.cfg execution flow; do not generate Unified HLS open_component or direct v++ flow artifacts unless explicitly requested by the spec.",
        "Implement the top function named exactly as interfaces.top_function when present; otherwise use spec.name.",
        "Use fixed-width ap_int/ap_uint/ap_fixed types where they improve hardware intent.",
        "Use HLS libraries deliberately: default to ap_int.h, ap_fixed.h, hls_stream.h, and hls_math.h; use advanced libraries such as hls_task.h, hls_vector.h, or hls_streamofblocks.h only for explicit requirements.",
        "Add #pragma HLS INTERFACE pragmas for all external arguments and the return control interface.",
        "For AXI4 memory ports use m_axi with explicit bundles and concrete depth values for C/RTL co-simulation; for AXI4-Stream ports use hls::stream with axis interfaces; for native scalar controls use s_axilite or the requested native control mode.",
        "Identify the intended HLS pattern before choosing pragmas: scalar pipeline, local-buffer partition/reshape, read-compute-write dataflow, multi-m_axi bandwidth, or fixed/float numeric strategy.",
        "Add PIPELINE, DATAFLOW, ARRAY_PARTITION, ARRAY_RESHAPE, UNROLL, or STREAM pragmas only when justified by loop structure, memory access pattern, or explicit performance evidence.",
        "Use report-driven reasoning: target II, achieved II, loop interval, load/store bottlenecks, timing slack, interface bandwidth, and resource growth should explain each optimization choice.",
        "When pipelining an outer loop, account for implied inner-loop concurrency; if the bottleneck is parallel memory access, choose partition, reshape, or banking based on the accessed dimension.",
        "For variable-bound loops, keep the control structure honest: require a justified maximum bound before aggressive unroll or complete banking, and use tripcount guidance only as reporting support.",
        "Treat pointer aliasing, template expansion, and vector-style packed operations as modeling choices that must preserve explicit interface intent and testability.",
        "Place #pragma HLS directives at the function or loop scope they control, keep dataflow regions free of global-state coupling and recursion, and do not combine array_partition and array_reshape on the same variable.",
        "For DATAFLOW designs, split read/compute/write stages with clear hls::stream FIFO boundaries and explicit stream depth when producer and consumer rates can differ.",
        "Distinguish control-driven orchestration from data-driven task graphs; only introduce task-level parallel structure when restart behavior, channel ownership, and stage boundaries are explicit.",
        "For fixed-point or floating-point designs, document the range/precision tradeoff and explicitly decide whether unsafe_math_optimizations is allowed.",
        "Treat DSP-oriented transforms and filters as explicit requirements; do not inject FFT, FIR, or intrinsic-heavy structures unless the spec calls for them.",
        "Do not use deprecated Vivado/Vitis HLS commands or pragmas: config_sdx, set_directive_data_pack, set_directive_resource, DATA_PACK, or hls_linear_algebra.h.",
        "Migration-oriented Tcl, Python, and unified CLI flows are reference surfaces only; keep generated execution on the stable Tcl plus .cfg path unless the runtime explicitly adds a new mode.",
        "Ensure hls_config.cfg includes exact syn.top and syn.file entries when a cfg file is requested.",
        "Avoid dynamic allocation, recursion, exceptions, RTTI, std::vector, and unsupported standard library features.",
        "Include a self-checking C++ testbench and hls_config.cfg when requested by outputs.",
        "Make generated HLS suitable for Vitis C simulation, synthesis, and co-simulation.",
        *_vitis_skill_rules(),
        *_performance_rules_for(spec),
        *_hls_profile_rules(hls_profile),
        *_comment_rules_for(comment_language),
    ]
    return rules


def _vitis_skill_rules() -> list[str]:
    preference = resolve_vitis_skill_preference()
    fallbacks = ", ".join(preference["fallback_skills"])
    return [
        f"For Vitis development, simulation, co-simulation, and HLS debug guidance, prefer the `{preference['selected_skill']}` Codex skill when available.",
        f"If `{preference['preferred_skill']}` is not installed, fall back to: {fallbacks}.",
    ]


def _stage_guidance(
    spec: dict[str, Any],
    stage: str,
    comment_language: str,
    vector_contract: dict[str, Any] | None,
    hls_profile: dict[str, Any],
) -> tuple[str, str, list[str]]:
    common = [
        "Do not use TODO, FIXME, ellipses, placeholder text, or unsupported HLS features.",
        "Preserve interfaces, case ids, and file paths exactly.",
    ]
    if stage == "requirements":
        return (
            "Confirmed HLS requirement normalization",
            "Normalize user-confirmed HLS requirements into a stable pre-generation contract.",
            ["Do not invent missing confirmation data; record unresolved items as open questions.", *common],
        )
    if stage == "codegen_plan":
        return (
            "HLS pre-generation code plan",
            "Produce a structured implementation plan before HLS code is generated.",
            [
                "Create requirements_summary, interface_decision, pipeline_strategy, module_partition, width strategy, verification_strategy, syntax_risk_checks, open_questions, and ready_for_generation.",
                "Keep ready_for_generation false when any interface or pipeline decision is unresolved.",
                *common,
            ],
        )
    if stage == "tests":
        return (
            "Semantic HLS test oracle generation",
            "Create deterministic reference vectors shared by Python and the HLS testbench.",
            [
                "Generate stable case ids, nominal cases, boundary cases, and invalid-input cases when relevant.",
                "Define expected outputs and checkpoints for each case.",
                *common,
            ],
        )
    if stage == "python":
        return (
            "Python oracle generation",
            "Create an executable Python reference model and vectors for semantic checking.",
            [
                "Expose run_case(case), collect_checkpoints(case), run_tests(), and REFERENCE_VECTORS.",
                "Use deterministic pure Python; do not require external packages.",
                "Mirror all vector case ids exactly.",
                *common,
            ],
        )
    return (
        "Vitis HLS implementation generation",
        "Create HLS C/C++ source, header, self-checking testbench, and cfg artifacts.",
        [
            *_hls_rules(spec, comment_language, hls_profile),
            *_vector_contract_rules(vector_contract),
            *common,
        ],
    )


def _stage_manifest_for(spec: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage == "requirements":
        files = [{"path": f"plan/{spec['name']}_requirements.json", "kind": "requirements", "language": "json"}]
    elif stage == "codegen_plan":
        files = [{"path": f"plan/{spec['name']}_codegen_plan.json", "kind": "codegen_plan", "language": "json"}]
    elif stage == "tests":
        files = [{"path": f"plan/{spec['name']}_test_vectors.json", "kind": "test_vectors", "language": "json"}]
    elif stage == "python":
        files = [
            {"path": f"model/{spec['name']}_model.py", "kind": "reference_model", "language": "python"},
            {"path": f"model/{spec['name']}_vectors.json", "kind": "reference_vectors", "language": "json"},
        ]
    elif stage == "hls":
        files = [
            {
                "path": output["path"],
                "kind": output.get("kind", "source"),
                "language": output.get("language", _language_from_path(output["path"])),
            }
            for output in spec["outputs"]
        ]
    else:
        raise ValueError("This skill is HLS-only; unknown stage " + repr(stage) + ".")
    return {"target": "hls", "name": spec["name"], "stage": stage, "top": spec["interfaces"].get("top_function", spec["name"]), "files": files, "checks": _checks_template()}


def _manifest_for(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": "hls",
        "name": spec["name"],
        "top": spec["interfaces"].get("top_function", spec["name"]),
        "files": [
            {
                "path": output["path"],
                "kind": output.get("kind", "source"),
                "language": output.get("language", _language_from_path(output["path"])),
            }
            for output in spec["outputs"]
        ],
        "checks": _checks_template(),
    }


def _checks_template() -> dict[str, list[str]]:
    return {
        "spec_coverage": [],
        "verification_plan": [],
        "execution_plan": [],
        "implementation_assessment": [],
        "reviewability_assessment": [],
        "assumptions": [],
        "known_limitations": [],
    }


def _comment_rules_for(comment_language: str) -> list[str]:
    if comment_language == "zh":
        language_rule = "Use Chinese comments by default; identifiers, protocol names, and tool names may remain in English."
    else:
        language_rule = "Use English comments only."
    return [
        language_rule,
        "Comment hardware intent rather than restating syntax: explain the top function role, every external argument protocol, each HLS pragma, key loops, boundary conditions, local buffers, and testbench case checks.",
        "Use short // comments for C/C++ by default; use /* ... */ only for file headers or multi-line hardware constraints.",
        "Do not add line-by-line noise comments, contradictory comments, commented-out old code, or forced translations of tool/protocol names.",
        "Use the manifest checks.reviewability_assessment field to summarize comment coverage and limitations.",
    ]


def _performance_rules_for(spec: dict[str, Any]) -> list[str]:
    performance = spec.get("performance") or {}
    if not performance:
        return []
    return [
        "Honor explicit performance constraints in spec.performance and summarize latency, II, resource, and timing handling in the manifest.",
        f"Performance constraints: {json.dumps(performance, ensure_ascii=False, sort_keys=True)}",
    ]


def _hls_profile_rules(profile: dict[str, Any]) -> list[str]:
    if not profile:
        return []
    return [
        "Honor the explicit hls_profile compatibility rules for interfaces, pragma policy, memory policy, and forbidden C++ features.",
        f"HLS profile: {json.dumps(profile, ensure_ascii=False, sort_keys=True)}",
    ]


def _vector_contract_rules(vector_contract: dict[str, Any] | None) -> list[str]:
    if not vector_contract:
        return []
    return [
        f"Mirror the reference vector contract exactly: case_count={vector_contract.get('case_count')}, case_ids={vector_contract.get('case_ids')}.",
        f"Every generated HLS testbench must include an adjacent comment `{VECTOR_HASH_TAG} {vector_contract.get('sha256')}` and use the same case ids.",
    ]


def _append_optional_sections(prompt: str, *, hls_profile: dict[str, Any] | None, decision: dict[str, Any] | None) -> str:
    sections: list[str] = []
    if hls_profile:
        sections.append("## HLS profile constraints\n\n```json\n" + json.dumps(hls_profile, indent=2, ensure_ascii=False) + "\n```")
    if decision:
        sections.append("## Human decision constraints\n\n```json\n" + json.dumps(decision, indent=2, ensure_ascii=False) + "\n```")
    return prompt if not sections else prompt.rstrip() + "\n\n" + "\n\n".join(sections) + "\n"


def _language_from_path(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {"cpp": "cpp", "cc": "cpp", "cxx": "cpp", "h": "cpp", "hpp": "cpp", "cfg": "ini", "json": "json", "py": "python"}.get(suffix, "text")


def _artifact_context(manifest: dict[str, Any] | None, context_dir: Path | None, *, budget: str) -> dict[str, Any]:
    del budget
    if not manifest:
        return {}
    context: dict[str, Any] = {
        "stage": manifest.get("stage"),
        "target": manifest.get("target"),
        "top": manifest.get("top"),
        "files": [
            {"path": item.get("path"), "kind": item.get("kind"), "language": item.get("language")}
            for item in manifest.get("files", [])
            if isinstance(item, dict)
        ],
        "checks": manifest.get("checks", {}),
    }
    if context_dir:
        context["artifacts"] = _artifact_summaries(manifest, context_dir)
    return context


def _artifact_summaries(manifest: dict[str, Any], context_dir: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    root = context_dir.resolve()
    for file_entry in manifest.get("files", []):
        if not isinstance(file_entry, dict) or not file_entry.get("path"):
            continue
        rel_path = str(file_entry["path"])
        artifact_path = _safe_context_path(root, rel_path)
        summary: dict[str, Any] = {"path": rel_path, "exists": artifact_path.exists()}
        if artifact_path.exists() and artifact_path.is_file():
            text = artifact_path.read_text(encoding="utf-8", errors="ignore")
            summary["preview"] = text[:800]
        summaries.append(summary)
    return summaries


def _memory_constraints(memory: dict[str, Any] | None, stage: str, *, budget: str) -> list[dict[str, Any]]:
    del budget
    if not memory:
        return []
    entries: list[dict[str, Any]] = []
    for entry in memory.get("entries", []):
        if not isinstance(entry, dict):
            continue
        entry_stage = str(entry.get("stage", "")).lower()
        if entry_stage and entry_stage not in {stage, "*", "unknown", "validate", "execute", "implement", "cosim"}:
            continue
        entries.append(
            {
                "stage": entry.get("stage"),
                "error_signature": entry.get("error_signature"),
                "constraint": entry.get("constraint"),
            }
        )
    return entries[:20]


def _safe_context_path(root: Path, relative_path: str) -> Path:
    if "\\" in relative_path:
        return root / "__invalid_backslash_path__"
    posix = PurePosixPath(relative_path)
    windows = PureWindowsPath(relative_path)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or any(part in ("", ".", "..") for part in posix.parts):
        return root / "__invalid_unsafe_path__"
    candidate = (root / Path(*posix.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return root / "__invalid_outside_path__"
    return candidate
