#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

PASS_STATUS = "passed"

def _confidence_outcome(
    gates: dict[str, dict[str, Any]],
    *,
    remote_requested: bool,
    remote_skipped: bool,
) -> tuple[str, str, list[str], int]:
    local_gate_names = [name for name in gates if name not in {"remote_vitis_acceptance", "remote_board_acceptance", "remote_family_coverage"}]
    local_passed = all(gates[name]["status"] == "passed" for name in local_gate_names)
    remote_gate = gates.get("remote_vitis_acceptance")
    board_gate = gates.get("remote_board_acceptance")
    family_gate = gates.get("remote_family_coverage")
    route_gate = gates.get("route_contract")
    if remote_requested:
        family_gate_ok = family_gate is None or family_gate.get("status") in {PASS_STATUS, "skipped"}
        if route_gate and route_gate.get("status") != "passed":
            risks = _residual_risks("blocked_remote_validation", remote_requested=True, remote_skipped=False, gates=gates)
            return "blocked_remote_validation", "final", risks, 1
        if local_passed and remote_gate and remote_gate.get("status") == PASS_STATUS and board_gate and board_gate.get("status") == PASS_STATUS and family_gate_ok:
            return "factual_high_confidence", "final", [], 0
        if board_gate and board_gate.get("status") == "blocked":
            risks = _residual_risks("blocked_remote_validation", remote_requested=True, remote_skipped=False, gates=gates)
            return "blocked_remote_validation", "final", risks, 1
        if family_gate and family_gate.get("status") == "failed":
            risks = _residual_risks("blocked_remote_validation", remote_requested=True, remote_skipped=False, gates=gates)
            return "blocked_remote_validation", "final", risks, 1
        risks = _residual_risks("needs_attention", remote_requested=True, remote_skipped=False, gates=gates)
        return "needs_attention", "final", risks, 1
    if remote_skipped:
        risks = _residual_risks("local_high_confidence" if local_passed else "needs_attention", remote_requested=False, remote_skipped=True, gates=gates)
        return ("local_high_confidence", "local", risks, 0) if local_passed else ("needs_attention", "local", risks, 1)
    risks = _residual_risks("blocked_remote_validation", remote_requested=False, remote_skipped=False, gates=gates)
    return ("blocked_remote_validation", "final", risks, 1) if local_passed else ("needs_attention", "final", risks, 1)
def _residual_risks(confidence_status: str, *, remote_requested: bool, remote_skipped: bool, gates: dict[str, dict[str, Any]]) -> list[str]:
    risks: list[str] = []
    if confidence_status == "needs_attention":
        risks.append("At least one confidence gate failed; inspect gates for details.")
    if confidence_status == "blocked_remote_validation":
        route_gate = gates.get("route_contract")
        remote_gate = gates.get("remote_vitis_acceptance")
        board_gate = gates.get("remote_board_acceptance")
        if route_gate and route_gate.get("status") == "failed":
            risks.append("Remote route target does not match the AGENTS contract primary server.")
        if remote_gate and remote_gate.get("status") not in {None, "passed"}:
            risks.append("Remote Vitis acceptance did not pass on the routed server.")
        if board_gate and board_gate.get("status") == "blocked":
            board_results = board_gate.get("results", [])
            platform_blocked = False
            suggested_platform = ""
            for item in board_results:
                if str(item.get("status")) != "blocked_board_validation":
                    continue
                if "platform_probe" in set(str(reason) for reason in item.get("blocking_reasons", [])):
                    platform_blocked = True
                    probe = item.get("platform_probe", {}) if isinstance(item.get("platform_probe"), dict) else {}
                    suggested_platform = str(probe.get("suggested_platform_name") or "")
                    break
            if platform_blocked:
                if suggested_platform:
                    risks.append(f"Board acceptance is blocked; the routed host shows an active U55C shell but no matching installed platform/xpfm was found. Suggested platform package: {suggested_platform}.")
                else:
                    risks.append("Board acceptance is blocked; the routed host shows board-level evidence but no matching installed platform/xpfm was found.")
            else:
                risks.append("Board acceptance is blocked; hardware fingerprint or board profile evidence is incomplete.")
        family_gate = gates.get("remote_family_coverage")
        if family_gate and family_gate.get("status") == "failed":
            missing = ", ".join(str(item) for item in family_gate.get("missing_specs", []))
            risks.append(f"Remote factual coverage is incomplete for Tier 1 board targets: {missing}.")
    if remote_requested:
        return risks
    if remote_skipped:
        risks.append("Final confidence requires remote Vitis acceptance.")
    else:
        risks.append("Remote Vitis acceptance was not executed.")
    return risks
