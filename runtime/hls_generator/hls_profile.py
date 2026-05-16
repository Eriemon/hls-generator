"""Vitis HLS profile checks and repair-prompt generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .patterns import ADVANCED_LIBRARY_HEADERS, canonical_pattern_name, required_pattern_headers

DEFAULT_FORBIDDEN_FEATURES = (
    "std::vector",
    "new",
    "malloc",
    "free",
    "throw",
    "catch",
    "std::map",
    "std::unordered_map",
    "std::list",
    "std::deque",
    "std::string",
)


def validate_hls_profile(profile: dict[str, Any], root: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    if not profile:
        return []
    source_text = _source_text(root)
    cfg_text = _cfg_text(root)
    issues: list[dict[str, Any]] = []
    issues.extend(_check_required_metadata(profile))
    issues.extend(_check_headers(profile, source_text))
    issues.extend(_check_allowed_libraries(profile, source_text))
    issues.extend(_check_forbidden_features(profile, source_text))
    issues.extend(_check_interface_modes(profile, source_text))
    issues.extend(_check_required_pragmas(profile, source_text, spec))
    issues.extend(_check_pattern_semantics(profile, source_text))
    issues.extend(_check_static_arrays(profile, source_text))
    issues.extend(_check_forbidden_combinations(profile, source_text))
    issues.extend(_check_cfg(profile, cfg_text))
    return issues


def build_hls_optimizer_prompt(validation_json: dict[str, Any], profile: dict[str, Any]) -> str:
    profile_json = json.dumps(profile, indent=2, ensure_ascii=False, sort_keys=True)
    issues = _profile_related_issues(validation_json)
    issues_json = json.dumps(issues, indent=2, ensure_ascii=False)
    return f"""# HLS profile repair prompt

You are repairing Vitis HLS C++ artifacts to satisfy the project HLS profile. Do not change the algorithm unless an issue explicitly requires it.

## HLS profile

```json
{profile_json}
```

## Profile-related validation issues

```json
{issues_json}
```

## Repair constraints

- Align `hls_config.cfg` with `syn.top` and every required `syn.file`.
- Emit `#pragma HLS INTERFACE` pragmas for all external arguments using only allowed interface modes.
- Remove forbidden C++ features from kernel code: dynamic memory, exceptions, recursion, and unsupported STL containers.
- Replace dynamic arrays with static bounded arrays or stream/buffer structures that Vitis HLS can synthesize.
- Preserve the manifest/code-fence output contract and regenerate only the affected HLS files.
"""


def _check_forbidden_features(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    features = profile.get("forbidden_features") or DEFAULT_FORBIDDEN_FEATURES
    issues: list[dict[str, Any]] = []
    patterns = {
        "std::vector": r"\bstd::vector\b",
        "new": r"\bnew\s+[A-Za-z_]",
        "malloc": r"\bmalloc\s*\(",
        "free": r"\bfree\s*\(",
        "throw": r"\bthrow\b",
        "catch": r"\bcatch\s*\(",
        "std::map": r"\bstd::map\b",
        "std::unordered_map": r"\bstd::unordered_map\b",
        "std::list": r"\bstd::list\b",
        "std::deque": r"\bstd::deque\b",
        "std::string": r"\bstd::string\b",
    }
    for feature in features:
        pattern = patterns.get(str(feature), re.escape(str(feature)))
        if re.search(pattern, source_text):
            issues.append(_issue("error", f"HLS profile violation: forbidden feature {feature!r} was found."))
    return issues


def _check_interface_modes(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    allowed = profile.get("allowed_interface_modes") or profile.get("interface_modes") or []
    if not allowed:
        return []
    allowed_set = {str(item) for item in allowed}
    issues: list[dict[str, Any]] = []
    for line in source_text.splitlines():
        if "#pragma HLS INTERFACE" not in line:
            continue
        mode = _pragma_mode(line)
        if mode and mode not in allowed_set:
            issues.append(_issue("error", f"HLS profile violation: interface mode {mode!r} is not allowed."))
    return issues


def _check_required_pragmas(profile: dict[str, Any], source_text: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for pragma in profile.get("required_pragmas", []) or []:
        token = str(pragma)
        if token and token not in source_text:
            issues.append(_issue("error", f"HLS profile violation: required pragma {token!r} was not found."))
    if profile.get("require_interface_pragmas", True) is False:
        return issues
    for argument in spec.get("interfaces", {}).get("arguments", []) or []:
        if not isinstance(argument, dict) or not argument.get("name"):
            continue
        name = str(argument["name"])
        if not re.search(rf"#pragma\s+HLS\s+INTERFACE[^\n]*\bport\s*=\s*{re.escape(name)}\b", source_text):
            issues.append(_issue("error", f"HLS profile violation: missing interface pragma for argument {name!r}."))
    return issues


def _check_required_metadata(profile: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    issues: list[dict[str, Any]] = []
    for field in profile.get("required_metadata_fields", []) or []:
        key = str(field)
        if metadata.get(key) in (None, "", [], {}):
            issues.append(_issue("error", f"HLS profile violation: missing metadata field {key!r}."))
    return issues


def _check_pattern_semantics(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    pattern = canonical_pattern_name(profile)
    if pattern == "task_graph":
        issues: list[dict[str, Any]] = []
        if "hls::task" not in source_text:
            issues.append(_issue("error", "HLS profile violation: task_graph pattern must instantiate hls::task explicitly."))
        if "style=flp" not in source_text and "style=frp" not in source_text:
            issues.append(_issue("error", "HLS profile violation: task_graph task actors must use a flushing or free-running pipeline style."))
        return issues
    return []


def _check_headers(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    required_headers = required_pattern_headers(profile)
    issues: list[dict[str, Any]] = []
    for header in required_headers:
        if f"#include <{header}>" not in source_text and f'#include "{header}"' not in source_text:
            issues.append(_issue("error", f"HLS profile violation: required header {header!r} was not found."))
    return issues


def _check_allowed_libraries(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    allowed = {str(item) for item in profile.get("allowed_libraries", []) or []}
    required = set(required_pattern_headers(profile))
    allowed.update(required)
    if not allowed:
        return []
    issues: list[dict[str, Any]] = []
    for header in ADVANCED_LIBRARY_HEADERS:
        if f"#include <{header}>" not in source_text and f'#include "{header}"' not in source_text:
            continue
        if header not in allowed:
            issues.append(_issue("error", f"HLS profile violation: advanced header {header!r} is not allowed for this pattern."))
    return issues


def _check_static_arrays(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    if profile.get("require_static_arrays", True) is False and profile.get("static_memory_rule") != "static_bound":
        return []
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_:<>]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\[[A-Za-z_][A-Za-z0-9_]*\]\s*;", source_text):
        return [_issue("error", "HLS profile violation: dynamic stack array was found; use static bounds.")]
    return []


def _check_forbidden_combinations(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in profile.get("forbidden_combinations", []) or []:
        if not isinstance(item, dict):
            continue
        markers = [str(marker) for marker in item.get("all_of", []) or [] if str(marker)]
        if markers and all(marker in source_text for marker in markers):
            issues.append(_issue("error", str(item.get("message") or "HLS profile violation: forbidden combination was found.")))
    return issues


def _check_cfg(profile: dict[str, Any], cfg_text: str) -> list[dict[str, Any]]:
    if profile.get("require_syn_file", True) is False:
        issues: list[dict[str, Any]] = []
    else:
        issues = []
        if not re.search(r"(?m)^\s*syn\.file\s*=", cfg_text):
            issues.append(_issue("error", "HLS profile violation: cfg is missing syn.file."))
    for entry in profile.get("required_cfg_entries", []) or []:
        token = str(entry)
        if token and token not in cfg_text:
            issues.append(_issue("error", f"HLS profile violation: cfg is missing required entry {token!r}."))
    return issues


def _pragma_mode(line: str) -> str:
    mode = _pragma_value(line, "mode")
    if mode:
        return mode
    match = re.search(r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z0-9_]+)", line)
    return match.group(1) if match else ""


def _pragma_value(line: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}\s*=\s*([A-Za-z0-9_]+)", line)
    return match.group(1) if match else ""


def _profile_related_issues(validation_json: dict[str, Any]) -> list[dict[str, Any]]:
    issues = validation_json.get("issues", []) if isinstance(validation_json, dict) else []
    selected: list[dict[str, Any]] = []
    for issue in issues or []:
        text = json.dumps(issue, ensure_ascii=False).lower() if isinstance(issue, dict) else str(issue).lower()
        if "hls profile" in text or "pragma" in text or "syn.file" in text or "std::vector" in text or "dynamic" in text:
            selected.append(issue if isinstance(issue, dict) else {"message": str(issue)})
    return selected


def _source_text(root: Path) -> str:
    texts: list[str] = []
    for pattern in ("**/*.cpp", "**/*.cc", "**/*.cxx", "**/*.h", "**/*.hpp"):
        for path in sorted(root.glob(pattern)):
            texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(texts)


def _cfg_text(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in sorted(root.glob("**/*.cfg")))


def _issue(severity: str, message: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "message": message,
        "stage": "static",
        "source": "current_module_issue",
    }

