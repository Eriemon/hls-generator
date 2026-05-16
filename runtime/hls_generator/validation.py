"""Built-in and AMD-Xilinx Vitis validation for generated HLS artifacts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import missing_vitis_tool_id, vitis_command, vitis_tcl_config, vitis_tool_names, vitis_tool_timeout, vitis_tools
from .hls_cfg import cfg_relative_path_issue, clock_period_ns, parse_hls_cfg_entries
from .hls_profile import validate_hls_profile
from .hls_reports import collect_hls_report_metrics
from .hls_tcl import render_vitis_hls_tcl
from .interface_contract import audit_interface
from .patterns import ADVANCED_LIBRARY_HEADERS, canonical_pattern_name, required_pattern_headers
from .prompt import require_comment_language
from .reference_contract import compare_reference_to_transcript, parse_semantic_transcript
from .spec import normalize_spec
from .verifier import plan_contract_interface_issues
from .vitis_rules import scan_vitis_rule_violations
from .vectors import VECTOR_HASH_TAG, extract_vector_hashes, find_vector_contracts

READINESS_LEVELS = ("static", "compile", "execute", "implement", "cosim")
_READINESS_ORDER = {name: index for index, name in enumerate(READINESS_LEVELS)}
_REPORT_STAGES = READINESS_LEVELS


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str
    path: str | None = None
    stage: str = "static"
    source: str = "current_module_issue"
    case_id: str | None = None
    tool: str | None = None
    detail: str | None = None

    def format(self) -> str:
        location = f" [{self.path}]" if self.path else ""
        case = f" case={self.case_id}" if self.case_id else ""
        tool = f" tool={self.tool}" if self.tool else ""
        return f"{self.severity.upper()}[{self.source}]{tool}{case}: {self.message}{location}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "stage": self.stage,
            "source": self.source,
            "case_id": self.case_id,
            "tool": self.tool,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ValidationReport:
    target: str
    root: Path
    issues: tuple[ValidationIssue, ...]
    metrics: dict[str, Any] | None = None

    @property
    def errors(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")

    @property
    def skips(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "skip")

    def ok(self) -> bool:
        return self.errors == 0

    def format(self) -> str:
        lines = [f"Validation report for {self.target} at {self.root}"]
        for stage in _REPORT_STAGES:
            stage_issues = [issue for issue in self.issues if issue.stage == stage]
            if stage_issues:
                lines.append(f"[{stage}]")
                lines.extend(issue.format() for issue in stage_issues)
            elif stage == "static":
                lines.append("[static]")
                lines.append("INFO: Static checks passed.")
        lines.append(f"Summary: {self.errors} error(s), {self.warnings} warning(s), {self.skips} skip(s)")
        if self.metrics:
            lines.append(f"Metrics: {self.metrics}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "root": str(self.root),
            "ok": self.ok(),
            "errors": self.errors,
            "warnings": self.warnings,
            "skips": self.skips,
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": self.metrics or {},
        }


def require_readiness(readiness: str) -> str:
    normalized = readiness.lower()
    if normalized not in _READINESS_ORDER:
        raise ValueError(f"Readiness must be one of {', '.join(READINESS_LEVELS)}.")
    return normalized


def readiness_at_least(readiness: str, stage: str) -> bool:
    return _READINESS_ORDER[readiness] >= _READINESS_ORDER[stage]


def validate_generated(
    spec: dict[str, Any],
    path: Path,
    target: str | None = None,
    *,
    run_external: bool = True,
    readiness: str = "static",
    comment_language: str = "zh",
    hls_profile: dict[str, Any] | None = None,
    reference_contract: dict[str, Any] | None = None,
) -> ValidationReport:
    normalized = normalize_spec(spec, target=target)
    readiness = require_readiness(readiness)
    comment_language = require_comment_language(comment_language)
    root = path.resolve()
    issues: list[ValidationIssue] = []
    metrics: dict[str, Any] = {}
    if not root.exists():
        issues.append(ValidationIssue("error", "Generated path does not exist.", str(root), source="spec_issue"))
        return ValidationReport("hls", root, tuple(issues), metrics)

    reference_cases = _reference_case_ids(reference_contract) or _collect_reference_cases(root)
    issues.extend(_validate_expected_outputs(normalized, root))
    issues.extend(_validate_hls_only_tree(root))
    issues.extend(_validate_vector_contracts(normalized, root))
    issues.extend(_validate_reference_models(root, readiness, reference_cases))
    issues.extend(_validate_hls(normalized, root))
    issues.extend(_validate_advanced_library_alignment(normalized, root))
    issues.extend(_contract_gate_issues(plan_contract_interface_issues(normalized, audit_interface("hls", root))))
    profile = hls_profile or normalized.get("hls_profile") or {}
    issues.extend(_profile_issues(validate_hls_profile(profile, root, normalized)))
    issues.extend(_validate_hls_reviewability(normalized, root, comment_language))
    issues.extend(_validate_hls_testbench(normalized, root, reference_cases))
    issues.extend(_validate_placeholders(root, _hls_files(root)))
    issues.extend(_validate_vitis_rules(root))
    tool_issues, tool_metrics = _run_hls_readiness(normalized, root, readiness, run_external)
    issues.extend(tool_issues)
    metrics.update(tool_metrics)
    semantic_execution = _semantic_execution_from_issues(issues)
    if semantic_execution:
        metrics["semantic_execution"] = semantic_execution
    metrics.update(collect_hls_report_metrics(root))
    issues.extend(_validate_hls_clock_goal(normalized, metrics, readiness))
    issues.extend(_validate_semantic_execution(reference_contract, metrics, readiness))
    issues.extend(_validate_performance(normalized, metrics, readiness))
    return ValidationReport("hls", root, tuple(issues), metrics)


def _profile_issues(raw_issues: list[dict[str, Any]]) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            str(item.get("severity", "warning")),
            str(item.get("message", "HLS profile issue.")),
            item.get("path"),
            str(item.get("stage", "static")),
            str(item.get("source", "current_module_issue")),
            item.get("case_id"),
            item.get("tool"),
            item.get("detail"),
        )
        for item in raw_issues
    ]


def _contract_gate_issues(raw_issues: list[dict[str, Any]]) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            str(item.get("severity", "error")),
            str(item.get("message", "Interface contract issue.")),
            item.get("path"),
            "static",
            str(item.get("source", "current_module_issue")),
            item.get("case_id"),
        )
        for item in raw_issues
    ]


def _validate_expected_outputs(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for output in spec["outputs"]:
        if not (root / output["path"]).exists():
            issues.append(ValidationIssue("error", f"Expected output file is missing: {output['path']}", output["path"], source="spec_issue"))
    return issues


def _validate_hls_only_tree(root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in sorted([*root.glob("**/*.v"), *root.glob("**/*.sv")]):
        issues.append(ValidationIssue("error", "Generated Verilog/SystemVerilog files are not allowed in this HLS-only skill.", path.relative_to(root).as_posix(), "static", "spec_issue"))
    return issues


def _hls_files(root: Path) -> list[Path]:
    patterns = ("**/*.cpp", "**/*.cc", "**/*.cxx", "**/*.h", "**/*.hpp", "**/*.cfg")
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(root.glob(pattern)))
    return files


def _validate_placeholders(root: Path, files: list[Path]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    banned_patterns = {
        r"\bTODO\b": "Placeholder TODO remains in generated code.",
        r"\bFIXME\b": "Placeholder FIXME remains in generated code.",
        r"your code here": "Placeholder text remains in generated code.",
        r"\.\.\.": "Ellipsis placeholder remains in generated code.",
    }
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel_path = path.relative_to(root).as_posix()
        for pattern, message in banned_patterns.items():
            if re.search(pattern, text, flags=re.IGNORECASE):
                issues.append(ValidationIssue("error", message, rel_path, "static"))
    return issues


def _validate_reference_models(root: Path, readiness: str, reference_cases: list[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in sorted({*root.glob("**/*_model.py"), *root.glob("**/model.py")}):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel_path = path.relative_to(root).as_posix()
        if not re.search(r"\bdef\s+run_tests\s*\(", text):
            issues.append(ValidationIssue("error", "Python reference model must expose run_tests().", rel_path, "static"))
        if not re.search(r"\bdef\s+run_case\s*\(", text):
            issues.append(ValidationIssue("error", "Python reference model must expose run_case(case).", rel_path, "static"))
        if "__name__" not in text or "__main__" not in text:
            issues.append(ValidationIssue("error", "Python reference model must provide a __main__ CLI entry.", rel_path, "static"))
        if "REFERENCE_VECTORS" not in text and not reference_cases:
            issues.append(ValidationIssue("error", "Python reference model must provide REFERENCE_VECTORS or a vectors.json file.", rel_path, "static", "spec_issue"))
        if readiness_at_least(readiness, "execute") and re.search(r"\bdef\s+run_tests\s*\(", text):
            issues.extend(_run_reference_model(path, root))
    return issues


def _run_reference_model(path: Path, root: Path) -> list[ValidationIssue]:
    rel_path = path.relative_to(root).as_posix()
    try:
        result = subprocess.run([sys.executable, str(path)], cwd=root, capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return [ValidationIssue("error", "Python reference model run_tests() timed out.", rel_path, "execute", "toolchain_issue", tool="python")]
    except OSError as exc:
        return [ValidationIssue("error", f"Python reference model failed to start: {exc}", rel_path, "execute", "toolchain_issue", tool="python")]
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        return [ValidationIssue("error", "Python reference model run_tests() failed.", rel_path, "execute", "current_module_issue", tool="python", detail=_short_output(output))]
    if "PASS" not in output.upper():
        return [ValidationIssue("warning", "Python reference model run_tests() completed without explicit PASS output.", rel_path, "execute", "testbench_issue", tool="python", detail=_short_output(output))]
    return [ValidationIssue("info", "Python reference model run_tests() completed successfully.", rel_path, "execute", "toolchain_issue", tool="python", detail=_short_output(output))]


def _validate_hls_testbench(spec: dict[str, Any], root: Path, reference_cases: list[str]) -> list[ValidationIssue]:
    requested_tb = [output["path"] for output in spec["outputs"] if output.get("kind") == "testbench" or "_tb." in output["path"].lower()]
    issues: list[ValidationIssue] = []
    top = str(spec.get("interfaces", {}).get("top_function") or spec["name"])
    for rel_path in requested_tb:
        path = root / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"\bint\s+main\s*\(", text):
            issues.append(ValidationIssue("error", "HLS testbench main() entry point was not found.", rel_path, "static", "testbench_issue"))
        if not re.search(rf"\b{re.escape(top)}\s*\(", text):
            issues.append(ValidationIssue("error", f"HLS testbench must call top function {top!r}.", rel_path, "static", "testbench_issue"))
        issues.extend(_validate_pass_fail_text(text, rel_path))
        issues.extend(_validate_case_mentions(spec, text, rel_path, reference_cases))
    return issues


def _validate_pass_fail_text(text: str, rel_path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not re.search(r"\bPASS\b", text, flags=re.IGNORECASE):
        issues.append(ValidationIssue("error", "HLS testbench does not contain explicit PASS behavior.", rel_path, "static", "testbench_issue"))
    if not re.search(r"\bFAIL\b", text, flags=re.IGNORECASE):
        issues.append(ValidationIssue("error", "HLS testbench does not contain explicit FAIL behavior.", rel_path, "static", "testbench_issue"))
    return issues


def _validate_case_mentions(spec: dict[str, Any], text: str, rel_path: str, reference_cases: list[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    lowered = text.lower()
    for case in [*_verification_cases(spec), *reference_cases]:
        if case.lower() not in lowered:
            issues.append(ValidationIssue("error" if case in reference_cases else "warning", f"Verification case {case!r} is not mentioned in the HLS testbench.", rel_path, "static", "testbench_issue" if case in reference_cases else "spec_issue", case_id=case))
    return issues


def _verification_cases(spec: dict[str, Any]) -> list[str]:
    cases: list[str] = []
    for subfunction in spec.get("subfunctions", []):
        if not isinstance(subfunction, dict):
            continue
        for field in ("behavior", "constraints", "test_intent"):
            for item in subfunction.get(field, []):
                if isinstance(item, dict):
                    for case in item.get("verification_cases", []):
                        value = str(case.get("id") or case.get("name") or case.get("text")) if isinstance(case, dict) else str(case)
                        if value and value not in cases:
                            cases.append(value)
    return cases


def _collect_reference_cases(root: Path) -> list[str]:
    cases: list[str] = []
    for vectors_path in sorted(root.glob("**/*vectors.json")):
        try:
            payload = json.loads(vectors_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for case_id in _case_ids_from_payload(payload):
            if case_id not in cases:
                cases.append(case_id)
    for model_path in sorted({*root.glob("**/*_model.py"), *root.glob("**/model.py")}):
        text = model_path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"['\"]id['\"]\s*:\s*['\"]([^'\"]+)['\"]", text):
            if match.group(1) not in cases:
                cases.append(match.group(1))
    return cases


def _reference_case_ids(reference_contract: dict[str, Any] | None) -> list[str]:
    if not reference_contract:
        return []
    return [str(case_id) for case_id in reference_contract.get("case_ids", []) or []]


def _case_ids_from_payload(payload: Any) -> list[str]:
    raw = payload.get("cases", payload.get("vectors", [])) if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for index, case in enumerate(raw, start=1):
        value = str(case.get("id") or case.get("name") or f"case_{index}") if isinstance(case, dict) else f"case_{index}"
        if value not in ids:
            ids.append(value)
    return ids


def _validate_vector_contracts(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    contracts = find_vector_contracts(root)
    if not contracts:
        return []
    testbench_paths = _testbench_files_for(spec, root)
    if not testbench_paths:
        return [ValidationIssue("error", "Reference vectors exist but no HLS testbench file was found for vector hash validation.", stage="static", source="testbench_issue")]
    hashes: list[str] = []
    for path in testbench_paths:
        for value in extract_vector_hashes(path.read_text(encoding="utf-8", errors="ignore")):
            if value not in hashes:
                hashes.append(value)
    issues: list[ValidationIssue] = []
    for contract in contracts:
        expected_hash = str(contract.get("sha256"))
        if expected_hash not in hashes:
            issues.append(ValidationIssue("error", f"Reference vector contract hash is missing from HLS testbench; expected `{VECTOR_HASH_TAG} {expected_hash}`.", contract.get("path"), "static", "testbench_issue"))
    return issues


def _testbench_files_for(spec: dict[str, Any], root: Path) -> list[Path]:
    requested: list[Path] = []
    for output in spec.get("outputs", []):
        if not isinstance(output, dict) or not output.get("path"):
            continue
        path = root / str(output["path"])
        if output.get("kind") == "testbench" or "_tb." in path.name.lower():
            requested.append(path)
    return [path for path in requested if path.exists()] or [path for path in _hls_files(root) if "_tb." in path.name.lower()]


def _validate_hls(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    cpp_files = [path for path in _hls_files(root) if path.suffix.lower() in {".cpp", ".cc", ".cxx", ".h", ".hpp"}]
    source_files = [path for path in cpp_files if "_tb" not in path.stem.lower()]
    source_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in source_files)
    top = str(spec.get("interfaces", {}).get("top_function") or spec["name"])
    if not re.search(rf"\b{re.escape(top)}\s*\(", source_text):
        issues.append(ValidationIssue("error", f"Top HLS function {top!r} was not found."))
    if "#pragma HLS" not in source_text:
        issues.append(ValidationIssue("warning", "No Vitis HLS pragmas were found."))
    interface_pragmas = _parse_hls_interface_pragmas(source_text)
    pragmas_by_port: dict[str, list[dict[str, str]]] = {}
    for pragma in interface_pragmas:
        if pragma.get("port"):
            pragmas_by_port.setdefault(str(pragma["port"]), []).append(pragma)
    for argument in spec.get("interfaces", {}).get("arguments", []):
        if not isinstance(argument, dict) or not argument.get("name"):
            continue
        issues.extend(_argument_pragma_issues(argument, pragmas_by_port, spec))
    control_interface = spec.get("interfaces", {}).get("control")
    if control_interface:
        return_pragmas = pragmas_by_port.get("return", [])
        if not any(_canonical_hls_mode(item.get("mode")) == _canonical_hls_mode(control_interface) for item in return_pragmas):
            issues.append(ValidationIssue("error", f"HLS control interface must include `{control_interface}` on `port=return`."))
    if spec.get("pipeline_required", True) and not re.search(r"#pragma\s+HLS\s+PIPELINE\b", source_text):
        issues.append(ValidationIssue("error", "Pipeline-required HLS kernels must include at least one `#pragma HLS PIPELINE`."))
    issues.extend(_forbidden_cpp_issues(root, source_text))
    issues.extend(_cfg_issues(spec, root))
    return issues


def _argument_pragma_issues(argument: dict[str, Any], pragmas_by_port: dict[str, list[dict[str, str]]], spec: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    argument_name = str(argument["name"])
    explicit_interface = argument.get("interface")
    bundle = argument.get("bundle")
    matching_pragmas = pragmas_by_port.get(argument_name, [])
    if explicit_interface:
        if not matching_pragmas:
            issues.append(ValidationIssue("error", f"HLS argument {argument_name!r} is missing the required {explicit_interface!r} interface pragma."))
        elif not any(_canonical_hls_mode(item.get("mode")) == _canonical_hls_mode(explicit_interface) for item in matching_pragmas):
            found_modes = ", ".join(sorted({_canonical_hls_mode(item.get("mode")) for item in matching_pragmas if item.get("mode")})) or "none"
            issues.append(ValidationIssue("error", f"HLS argument {argument_name!r} must use interface mode {explicit_interface!r}, found {found_modes}."))
        if bundle and not any(str(item.get("bundle") or "") == str(bundle) for item in matching_pragmas):
            issues.append(ValidationIssue("error", f"HLS argument {argument_name!r} must use bundle {bundle!r}."))
    elif not matching_pragmas:
        issues.append(ValidationIssue("warning", f"No HLS interface pragma was found for argument {argument_name!r}."))
    observed_modes = {_canonical_hls_mode(item.get("mode")) for item in matching_pragmas if item.get("mode")}
    if spec.get("interface_family") == "axi4" and "axis" in observed_modes:
        issues.append(ValidationIssue("error", f"HLS argument {argument_name!r} must not use AXI-Stream pragmas when spec.interface_family is axi4."))
    if spec.get("interface_family") == "axi_stream" and "m_axi" in observed_modes:
        issues.append(ValidationIssue("error", f"HLS argument {argument_name!r} must not use m_axi pragmas when spec.interface_family is axi_stream."))
    return issues


def _forbidden_cpp_issues(root: Path, source_text: str) -> list[ValidationIssue]:
    del root
    banned = {
        r"\bstd::vector\b": "std::vector is usually not synthesizable in Vitis HLS kernels.",
        r"\bstd::(?:map|unordered_map|list|deque|string|function)\b": "Unsupported standard library container or dynamic type used in HLS kernel.",
        r"\b(?:malloc|free)\s*\(": "Dynamic memory allocation is not suitable for this Vitis HLS flow.",
        r"\b(?:new|delete)\b": "C++ dynamic allocation is not suitable for this Vitis HLS flow.",
        r"\bthrow\b|\bcatch\s*\(": "Exceptions are not suitable for this Vitis HLS flow.",
        r"\b[A-Za-z_][A-Za-z0-9_:<>]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\[[A-Za-z_][A-Za-z0-9_]*\]\s*;": "Variable-length stack arrays are not suitable for this Vitis HLS flow.",
    }
    return [ValidationIssue("error", message) for pattern, message in banned.items() if re.search(pattern, source_text)]


def _cfg_issues(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    cfg_files = sorted(root.glob("**/*.cfg"))
    if not cfg_files:
        return [ValidationIssue("error", "No Vitis HLS .cfg file found.")]
    cfg_text = cfg_files[0].read_text(encoding="utf-8", errors="ignore")
    cfg_entries = parse_hls_cfg_entries(cfg_text)
    issues: list[ValidationIssue] = []
    for message in cfg_entries.get("parse_errors", []):
        issues.append(ValidationIssue("error", str(message), cfg_files[0].relative_to(root).as_posix()))
    top = str(spec.get("interfaces", {}).get("top_function") or spec["name"])
    if cfg_entries.get("syn.top") != top:
        issues.append(ValidationIssue("error", f"HLS cfg syn.top must be {top!r}.", cfg_files[0].relative_to(root).as_posix()))
    source_outputs = [output["path"] for output in spec["outputs"] if Path(str(output["path"])).suffix.lower() in {".cpp", ".cc", ".cxx", ".h", ".hpp"} and output.get("kind") != "testbench" and "_tb." not in str(output["path"]).lower()]
    testbench_outputs = [output["path"] for output in spec["outputs"] if Path(str(output["path"])).suffix.lower() in {".cpp", ".cc", ".cxx"} and (output.get("kind") == "testbench" or "_tb." in str(output["path"]).lower())]
    syn_files = [str(item) for item in cfg_entries.get("syn.files", [])]
    tb_files = [str(item) for item in cfg_entries.get("tb.files", [])]
    for source_path in syn_files:
        issue = cfg_relative_path_issue(source_path)
        if issue:
            issues.append(ValidationIssue("error", issue, cfg_files[0].relative_to(root).as_posix()))
    for tb_path in tb_files:
        issue = cfg_relative_path_issue(tb_path)
        if issue:
            issues.append(ValidationIssue("error", issue, cfg_files[0].relative_to(root).as_posix()))
    for source_path in source_outputs:
        if source_path not in syn_files:
            issues.append(ValidationIssue("error", f"HLS cfg must include syn.file for declared source output {source_path!r}.", cfg_files[0].relative_to(root).as_posix()))
    for tb_path in testbench_outputs:
        if tb_path not in tb_files:
            issues.append(ValidationIssue("error", f"HLS cfg must include tb.file for declared testbench output {tb_path!r}.", cfg_files[0].relative_to(root).as_posix()))
    target_clock = _spec_hls_clock_period(spec)
    if target_clock is not None:
        cfg_clock = _safe_float(cfg_entries.get("clock"))
        if cfg_clock is None:
            issues.append(ValidationIssue("error", "HLS cfg must include `clock=` when spec.clock.period_ns is declared."))
        elif abs(cfg_clock - target_clock) > 1e-6:
            issues.append(ValidationIssue("error", f"HLS cfg clock={cfg_clock} does not match spec.clock.period_ns={target_clock}."))
    expected_flow = str((spec.get("workflow") or {}).get("flow_target") or "").strip().lower()
    observed_flow = str(cfg_entries.get("flow_target") or "").strip().lower()
    if observed_flow and observed_flow not in {"vivado", "vitis"}:
        issues.append(ValidationIssue("error", f"HLS cfg flow_target must be `vivado` or `vitis`, found {observed_flow!r}.", cfg_files[0].relative_to(root).as_posix()))
    if expected_flow and observed_flow and observed_flow != expected_flow:
        issues.append(ValidationIssue("error", f"HLS cfg flow_target={observed_flow!r} does not match spec.workflow.flow_target={expected_flow!r}.", cfg_files[0].relative_to(root).as_posix()))
    expected_part = str((spec.get("workflow") or {}).get("part") or spec.get("part") or "").strip()
    observed_part = str(cfg_entries.get("part") or "").strip()
    if expected_part and observed_part and observed_part != expected_part:
        issues.append(ValidationIssue("error", f"HLS cfg part={observed_part!r} does not match spec workflow part={expected_part!r}.", cfg_files[0].relative_to(root).as_posix()))
    interface_profile = spec.get("interface_profile") if isinstance(spec.get("interface_profile"), dict) else {}
    if interface_profile.get("burst_support") is True:
        expected_burst = interface_profile.get("max_burst_len")
        observed_burst = cfg_entries.get("interface", {}).get("m_axi_max_read_burst_length") if isinstance(cfg_entries.get("interface"), dict) else cfg_entries.get("m_axi_max_read_burst_length")
        if expected_burst in (None, ""):
            issues.append(ValidationIssue("error", "HLS spec enables burst_support but omits interface_profile.max_burst_len.", cfg_files[0].relative_to(root).as_posix()))
        elif str(observed_burst or "") != str(expected_burst):
            issues.append(ValidationIssue("error", f"HLS cfg burst length {observed_burst!r} does not match spec.interface_profile.max_burst_len={expected_burst!r}.", cfg_files[0].relative_to(root).as_posix()))
    return issues


def _validate_advanced_library_alignment(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    required = set(required_pattern_headers(spec))
    pattern = canonical_pattern_name(spec)
    issues: list[ValidationIssue] = []
    for path in _hls_files(root):
        if path.suffix.lower() not in {".cpp", ".cc", ".cxx", ".h", ".hpp"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel_path = path.relative_to(root).as_posix()
        for header in ADVANCED_LIBRARY_HEADERS:
            if f"#include <{header}>" not in text and f'#include "{header}"' not in text:
                continue
            if header not in required:
                issues.append(ValidationIssue("error", f"Advanced HLS header {header!r} is not justified by the selected pattern {pattern or 'none'!r}.", rel_path, "static", "spec_issue"))
    return issues


def _parse_hls_interface_pragmas(source_text: str) -> list[dict[str, str]]:
    pragmas: list[dict[str, str]] = []
    for line in source_text.splitlines():
        if "#pragma HLS INTERFACE" in line:
            pragmas.append({"line": line.strip(), "mode": _pragma_value(line, "mode") or _pragma_interface_mode(line), "port": _pragma_value(line, "port"), "bundle": _pragma_value(line, "bundle")})
    return pragmas


def _pragma_value(line: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}\s*=\s*([A-Za-z0-9_]+)", line)
    return match.group(1) if match else ""


def _pragma_interface_mode(line: str) -> str:
    match = re.search(r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z0-9_]+)", line)
    return match.group(1) if match else ""


def _canonical_hls_mode(mode: Any) -> str:
    return str(mode or "").strip().lower().replace("-", "_")


def _parse_hls_cfg_entries(cfg_text: str) -> dict[str, Any]:
    return parse_hls_cfg_entries(cfg_text)


def _spec_hls_clock_period(spec: dict[str, Any]) -> float | None:
    clock = spec.get("clock")
    if not isinstance(clock, dict) or clock.get("period_ns") in (None, ""):
        return None
    return _safe_float(clock.get("period_ns"))


def _safe_float(value: Any) -> float | None:
    return clock_period_ns(value)


def _validate_vitis_rules(root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in _hls_files(root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel_path = path.relative_to(root).as_posix()
        language = "testbench" if "_tb" in path.stem.lower() else path.suffix.lower().lstrip(".")
        for item in scan_vitis_rule_violations(text, path=rel_path, language=language):
            issues.append(
                ValidationIssue(
                    str(item.get("severity", "warning")),
                    str(item.get("message", "Vitis HLS rule violation.")),
                    item.get("path"),
                    str(item.get("stage", "static")),
                    str(item.get("source", "current_module_issue")),
                )
            )
    return issues


def _validate_hls_clock_goal(spec: dict[str, Any], metrics: dict[str, Any], readiness: str) -> list[ValidationIssue]:
    target_clock = _spec_hls_clock_period(spec)
    if target_clock is None or not readiness_at_least(readiness, "implement"):
        return []
    estimated = (metrics.get("csynth", {}).get("timing", {}) if isinstance(metrics.get("csynth"), dict) else {}).get("estimated_clock_period_ns")
    if estimated is not None and float(estimated) > target_clock:
        return [ValidationIssue("error", f"HLS estimated clock period {estimated}ns exceeds target {target_clock}ns.", stage="implement", source="toolchain_issue")]
    return []


def _validate_performance(spec: dict[str, Any], metrics: dict[str, Any], readiness: str) -> list[ValidationIssue]:
    performance = spec.get("performance") if isinstance(spec.get("performance"), dict) else {}
    if not performance or not readiness_at_least(readiness, "implement"):
        return []
    if not isinstance(metrics.get("csynth"), dict):
        return [ValidationIssue("warning", "Performance constraints are present but no Vitis HLS synthesis metrics were found.", stage="implement", source="toolchain_issue")]
    return [ValidationIssue("info", "Performance constraints have collected HLS metrics for review.", stage="implement", source="toolchain_issue")]


def _validate_hls_reviewability(spec: dict[str, Any], root: Path, comment_language: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    hls_files = [path for path in _hls_files(root) if path.suffix.lower() in {".cpp", ".cc", ".cxx", ".h", ".hpp"}]
    all_comments = [comment for path in hls_files for comment in _all_comment_texts(path.read_text(encoding="utf-8", errors="ignore").splitlines())]
    if comment_language == "zh" and all_comments and not any(_contains_cjk(comment) for comment in all_comments):
        issues.append(ValidationIssue("warning", "Reviewability warning: expected Chinese comments, but no Chinese comment text was found.", stage="static"))
    if comment_language == "en" and any(_contains_cjk(comment) for comment in all_comments):
        issues.append(ValidationIssue("warning", "Reviewability warning: expected English comments, but Chinese comment text was found.", stage="static"))
    top = str(spec.get("interfaces", {}).get("top_function") or spec["name"])
    for path in hls_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel_path = path.relative_to(root).as_posix()
        if top in text and not all_comments:
            issues.append(ValidationIssue("warning", "Reviewability warning: HLS files should contain explanatory comments.", rel_path))
            break
    return issues


def _all_comment_texts(lines: list[str]) -> list[str]:
    comments: list[str] = []
    for line in lines:
        if "//" in line:
            comments.append(line.split("//", 1)[1].strip())
        comments.extend(match.group(1).strip() for match in re.finditer(r"/\*(.*?)\*/", line))
    return [comment for comment in comments if comment]


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _run_hls_readiness(spec: dict[str, Any], root: Path, readiness: str, run_external: bool) -> tuple[list[ValidationIssue], dict[str, Any]]:
    if readiness == "static":
        return ([], {})
    if not run_external:
        return ([ValidationIssue("error", f"External Vitis execution is disabled but {readiness!r} readiness was requested.", stage=readiness, source="toolchain_issue")], {})
    tool = _select_vitis_tool()
    if tool is None:
        names = " or ".join(f"`{name}`" for name in vitis_tool_names())
        return ([ValidationIssue("error", f"Required AMD-Xilinx HLS tool not found on PATH. Install/source Vitis so {names} is available.", stage="compile", source="toolchain_issue", tool=missing_vitis_tool_id())], {})
    return _run_vitis_tool(tool, spec, root, readiness)


def _select_vitis_tool() -> dict[str, Any] | None:
    for tool in vitis_tools():
        executable = str(tool.get("which") or tool.get("name") or "")
        if executable and shutil.which(executable):
            return tool
    return None


def _run_vitis_tool(tool: dict[str, Any], spec: dict[str, Any], root: Path, readiness: str) -> tuple[list[ValidationIssue], dict[str, Any]]:
    name = str(tool["name"])
    cfg = _first_cfg(root)
    if cfg is None:
        return ([ValidationIssue("error", "No Vitis HLS .cfg file found for required readiness.", stage="compile", source="toolchain_issue", tool=name)], {})
    metrics: dict[str, Any] = {}
    try:
        tcl, project_dir = _write_vitis_hls_tcl(spec, root, cfg, readiness)
    except ValueError as exc:
        return ([ValidationIssue("error", str(exc), stage="compile", source="spec_issue", tool=name)], {})
    try:
        stage = _tool_stage_for(readiness)
        issues, output = _run_tool(vitis_command(tool, tcl=tcl), root, str(tool.get("label") or f"{name} Tcl flow"), stage, timeout=_tool_timeout_for(readiness))
        _merge_semantic_metrics(metrics, output)
        return issues, metrics
    finally:
        _cleanup_vitis_temp(tcl, project_dir)


def _write_vitis_hls_tcl(spec: dict[str, Any], root: Path, cfg: Path, readiness: str) -> tuple[Path, Path]:
    entries = parse_hls_cfg_entries(cfg.read_text(encoding="utf-8", errors="ignore"))
    tcl_config = vitis_tcl_config()
    lines, project = render_vitis_hls_tcl(spec, root, entries, readiness, tcl_config)
    handle = tempfile.NamedTemporaryFile("w", suffix=".tcl", prefix=tcl_config["temp_tcl_prefix"], dir=root, delete=False, encoding="utf-8")
    with handle:
        handle.write(lines)
    return Path(handle.name), project


def _cleanup_vitis_temp(tcl: Path, project_dir: Path) -> None:
    tcl.unlink(missing_ok=True)
    shutil.rmtree(project_dir, ignore_errors=True)


def _tool_stage_for(readiness: str) -> str:
    if readiness_at_least(readiness, "cosim"):
        return "cosim"
    if readiness_at_least(readiness, "implement"):
        return "implement"
    if readiness_at_least(readiness, "execute"):
        return "execute"
    return "compile"


def _tool_timeout_for(readiness: str) -> int:
    if readiness_at_least(readiness, "cosim"):
        return vitis_tool_timeout("cosim")
    if readiness_at_least(readiness, "implement"):
        return vitis_tool_timeout("implement")
    if readiness_at_least(readiness, "execute"):
        return vitis_tool_timeout("execute")
    return vitis_tool_timeout("compile")


def _tcl_quote(value: str) -> str:
    return "{" + value.replace("}", "\\}") + "}"


def _first_cfg(root: Path) -> Path | None:
    return next(iter(sorted(root.glob("**/*.cfg"))), None)


def _run_tool(command: list[str], cwd: Path, label: str, stage: str, *, timeout: int) -> tuple[list[ValidationIssue], str]:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return [ValidationIssue("error", f"{label} timed out after {timeout}s.", stage=stage, source="toolchain_issue", tool=command[0])], ""
    except OSError as exc:
        return [ValidationIssue("error", f"{label} failed to start: {exc}", stage=stage, source="toolchain_issue", tool=command[0])], ""
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        detail = output.splitlines()[0] if output else f"exit code {result.returncode}"
        return [ValidationIssue("error", f"{label} failed: {detail}", stage=stage, source="current_module_issue", tool=command[0], detail=_short_output(output))], output
    return [ValidationIssue("info", f"{label} completed successfully.", stage=stage, source="toolchain_issue", tool=command[0], detail=_short_output(output))], output


def _merge_semantic_metrics(metrics: dict[str, Any], output: str) -> None:
    if not output:
        return
    try:
        transcript = parse_semantic_transcript(output)
    except Exception:
        return
    if transcript.get("case_count"):
        metrics["semantic_transcript"] = transcript


def _semantic_execution_from_issues(issues: list[ValidationIssue]) -> dict[str, Any] | None:
    del issues
    return None


def _validate_semantic_execution(reference_contract: dict[str, Any] | None, metrics: dict[str, Any], readiness: str) -> list[ValidationIssue]:
    if not reference_contract or not readiness_at_least(readiness, "execute"):
        return []
    transcript = metrics.get("semantic_transcript")
    if not transcript:
        return [ValidationIssue("warning", "No HLS semantic transcript markers were observed in Vitis output.", stage="execute", source="testbench_issue")]
    comparison = compare_reference_to_transcript(reference_contract, transcript)
    metrics["semantic_execution"] = comparison
    issues: list[ValidationIssue] = []
    for case_id in comparison.get("missing_cases", []):
        issues.append(ValidationIssue("error", "HLS semantic transcript is missing a reference case.", stage="execute", source="testbench_issue", case_id=case_id))
    for case_id in comparison.get("failed_cases", []):
        issues.append(ValidationIssue("error", "HLS semantic transcript reported FAIL.", stage="execute", source="current_module_issue", case_id=case_id))
    for item in comparison.get("mismatched_cases", []):
        issues.append(ValidationIssue("error", "HLS semantic output drifted from the Python oracle.", stage="execute", source="current_module_issue", case_id=item.get("case_id") if isinstance(item, dict) else None, detail=json.dumps(item, ensure_ascii=False)))
    return issues


def _short_output(text: str, *, limit: int = 20000) -> str:
    return text.strip().replace("\r", "")[:limit]
