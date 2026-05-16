"""Vitis HLS 2022.2+ command and source compatibility rules."""

from __future__ import annotations

import re
from typing import Any

ALLOWED_INTERFACE_MODES = frozenset({"ap_ctrl_none", "ap_ctrl_hs", "ap_fifo", "ap_memory", "ap_none", "axis", "m_axi", "s_axilite"})
ALLOWED_CONFIG_COMMANDS = frozenset({"config_compile", "config_interface", "config_rtl", "config_dataflow", "config_csim", "config_cosim", "config_schedule", "config_export"})
ALLOWED_CONFIG_OPTIONS = {
    "compile": frozenset({"pipeline_loops", "enable_auto_rewind", "pipeline_style", "unsafe_math_optimizations"}),
    "interface": frozenset({"m_axi_addr64", "m_axi_max_read_burst_length", "default_slave_interface"}),
    "rtl": frozenset({"reset", "register_all_io", "module_prefix", "reset_level"}),
    "dataflow": frozenset({"fifo_depth", "strict_mode", "start_fifo_depth"}),
    "schedule": frozenset({"enable_dsp_full_reg"}),
    "csim": frozenset({"clean", "argv", "compile_only", "o", "ldflags"}),
    "cosim": frozenset({"rtl", "tool", "trace_level", "wave_debug", "random_stall", "enable_tasks_with_m_axi"}),
    "export": frozenset({"format", "rtl", "vendor", "library", "version", "display_name", "vivado_synth_strategy", "ip_xdc_file"}),
}
ALLOWED_DIRECTIVES = frozenset(
    {
        "aggregate",
        "array_partition",
        "array_reshape",
        "bind_op",
        "bind_storage",
        "dataflow",
        "dependence",
        "inline",
        "interface",
        "loop_flatten",
        "loop_merge",
        "loop_tripcount",
        "pipeline",
        "stream",
        "unroll",
    }
)
ALLOWED_REPORT_COMMANDS = frozenset({"report_utilization", "report_timing", "report_directive", "report_dataflow", "report_interface", "report_top"})

DEPRECATED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bconfig_sdx\b", "Deprecated Vitis HLS command `config_sdx` is not allowed in new scripts."),
    (r"\bset_directive_data_pack\b", "Deprecated Vitis HLS command `set_directive_data_pack` is not allowed; use aggregate."),
    (r"\bset_directive_resource\b", "Deprecated Vitis HLS command `set_directive_resource` is not allowed; use bind_op or bind_storage."),
    (r"#pragma\s+HLS\s+DATA_PACK\b", "Deprecated Vitis HLS pragma `DATA_PACK` is not allowed; use AGGREGATE."),
    (r"[\"<]hls_linear_algebra\.h[\">]", "Deprecated Vitis HLS header `hls_linear_algebra.h` is not allowed."),
    (r"\b-std=c\+\+0x\b", "Obsolete C++ flag `-std=c++0x` is not suitable for modern Vitis HLS 2022.2+ Clang-based flows."),
)

VARIABLE_LENGTH_ARRAY_PATTERN = r"\b[A-Za-z_][A-Za-z0-9_:<>]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\[[A-Za-z_][A-Za-z0-9_]*\]\s*;"


def scan_vitis_rule_violations(text: str, *, path: str | None = None, language: str = "text") -> list[dict[str, Any]]:
    """Return deterministic Vitis HLS compatibility issues for source/config text."""
    issues: list[dict[str, Any]] = []
    for pattern, message in DEPRECATED_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            issues.append(_issue("error", message, path))
    if language.lower() in {"c", "cpp", "c++", "cc", "cxx", "h", "hpp", "text"} and re.search(VARIABLE_LENGTH_ARRAY_PATTERN, text):
        issues.append(_issue("error", "Variable-length stack arrays are not suitable for this Vitis HLS flow; use static bounds.", path))
    issues.extend(_interface_mode_issues(text, path))
    issues.extend(_array_partition_reshape_issues(text, path))
    if language.lower() not in {"testbench", "tb"} and re.search(r"\bfloat\b|\bdouble\b", text) and not re.search(r"\bunsafe_math_optimizations\b", text):
        issues.append(_issue("warning", "Floating-point HLS code should explicitly decide whether `config_compile -unsafe_math_optimizations` is allowed.", path))
    return issues


def require_allowed_directive(name: str) -> str:
    normalized = name.strip().lower()
    if normalized not in ALLOWED_DIRECTIVES:
        raise ValueError(f"Unsupported Vitis HLS directive {name!r}.")
    return normalized


def require_allowed_config_section(section: str) -> str:
    normalized = section.strip().lower()
    command = f"config_{normalized}"
    if command not in ALLOWED_CONFIG_COMMANDS:
        raise ValueError(f"Unsupported Vitis HLS config section {section!r}.")
    return normalized


def require_allowed_config_option(section: str, key: str) -> str:
    normalized_section = section.strip().lower()
    normalized_key = key.strip().lower()
    allowed = ALLOWED_CONFIG_OPTIONS.get(normalized_section)
    if allowed is None or normalized_key not in allowed:
        raise ValueError(f"Unsupported Vitis HLS cfg option [{section}].{key}.")
    return normalized_key


def _interface_mode_issues(text: str, path: str | None) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for line in text.splitlines():
        if "#pragma HLS INTERFACE" not in line:
            continue
        mode = _pragma_value(line, "mode") or _pragma_interface_mode(line)
        if mode and mode not in ALLOWED_INTERFACE_MODES:
            issues.append(_issue("error", f"Unsupported Vitis HLS interface mode {mode!r}.", path))
    return issues


def _array_partition_reshape_issues(text: str, path: str | None) -> list[dict[str, Any]]:
    partitioned = _pragma_array_targets(text, "ARRAY_PARTITION") | _directive_array_targets(text, "array_partition")
    reshaped = _pragma_array_targets(text, "ARRAY_RESHAPE") | _directive_array_targets(text, "array_reshape")
    conflicts = sorted(partitioned & reshaped)
    return [
        _issue("error", f"Do not apply both ARRAY_PARTITION and ARRAY_RESHAPE to variable {name!r} in the same solution.", path)
        for name in conflicts
    ]


def _pragma_array_targets(text: str, pragma: str) -> set[str]:
    pattern = rf"#pragma\s+HLS\s+{re.escape(pragma)}\b[^\n]*\bvariable\s*=\s*([A-Za-z_][A-Za-z0-9_]*)"
    return {match.group(1) for match in re.finditer(pattern, text, flags=re.IGNORECASE)}


def _directive_array_targets(text: str, directive: str) -> set[str]:
    targets: set[str] = set()
    pattern = rf"\b(?:syn\.directive\.)?{re.escape(directive)}\s*=\s*([A-Za-z_][A-Za-z0-9_:]*)\s+([A-Za-z_][A-Za-z0-9_]*)"
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        targets.add(match.group(2))
    return targets


def _pragma_value(line: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}\s*=\s*([A-Za-z0-9_]+)", line)
    return match.group(1) if match else ""


def _pragma_interface_mode(line: str) -> str:
    match = re.search(r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z0-9_]+)", line)
    return match.group(1) if match else ""


def _issue(severity: str, message: str, path: str | None) -> dict[str, Any]:
    return {
        "severity": severity,
        "message": message,
        "path": path,
        "stage": "static",
        "source": "current_module_issue",
    }
