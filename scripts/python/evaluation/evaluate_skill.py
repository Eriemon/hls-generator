#!/usr/bin/env python3
"""Evaluate the Erie HLS skill corpus and expected pass-rate delta."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = SKILL_ROOT.parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the Erie HLS skill corpus and expected with-skill delta.")
    parser.add_argument("--evals", default=str(SKILL_ROOT / "evals" / "evals.json"), help="Path to evals.json.")
    parser.add_argument("--mode", choices=("with-skill", "without-skill", "both"), default="both")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a compact text summary.")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.evals).read_text(encoding="utf-8"))
    report = evaluate_payload(payload)
    if args.mode == "with-skill":
        output = {"mode": "with-skill", **report["with_skill"]}
    elif args.mode == "without-skill":
        output = {"mode": "without-skill", **report["without_skill"]}
    else:
        output = report
    if args.json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(_format_report(output))
    return 0 if report["with_skill"]["failed"] == 0 else 1


def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cases = payload.get("cases", [])
    with_results: list[dict[str, Any]] = []
    without_results: list[dict[str, Any]] = []
    for case in cases:
        with_pass, evidence = _evaluate_case(case)
        without_pass = bool(case.get("without_skill_expected_pass", False))
        with_results.append(
            {
                "id": case["id"],
                "title": case["title"],
                "passed": with_pass,
                "expected_pass": bool(case.get("with_skill_expected_pass", True)),
                "evidence": evidence,
            }
        )
        without_results.append(
            {
                "id": case["id"],
                "title": case["title"],
                "passed": without_pass,
                "expected_pass": without_pass,
                "baseline_reason": case.get("without_skill_reason", ""),
            }
        )
    with_summary = _summarize_results(with_results)
    without_summary = _summarize_results(without_results)
    pass_rate_delta = with_summary["pass_rate"] - without_summary["pass_rate"]
    return {
        "version": payload.get("version", 1),
        "title": payload.get("title", "Erie HLS Skill Evaluation"),
        "design_patterns": payload.get("design_patterns", []),
        "with_skill": with_summary,
        "without_skill": without_summary,
        "pass_rate_delta": pass_rate_delta,
        "cases": {
            "with_skill": with_results,
            "without_skill": without_results,
        },
    }


def _summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["passed"])
    failed = total - passed
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / total) if total else 0.0,
        "results": results,
    }


def _evaluate_case(case: dict[str, Any]) -> tuple[bool, list[str]]:
    evidence: list[str] = []
    passed = True
    for path_text in case.get("required_files", []):
        path = _resolve_case_path(path_text)
        ok = path.exists()
        evidence.append(f"file:{path_text}:{'ok' if ok else 'missing'}")
        passed = passed and ok
    for entry in case.get("required_terms", []):
        file_path = _resolve_case_path(entry["file"])
        text = file_path.read_text(encoding="utf-8")
        for term in entry.get("terms", []):
            ok = term in text
            evidence.append(f"term:{entry['file']}:{term}:{'ok' if ok else 'missing'}")
            passed = passed and ok
    expected = bool(case.get("with_skill_expected_pass", True))
    return passed == expected, evidence


def _resolve_case_path(path_text: str) -> Path:
    relative = Path(path_text)
    for base in (SKILL_ROOT, REPO_ROOT):
        candidate = (base / relative).resolve()
        if candidate.exists():
            return candidate
    return (SKILL_ROOT / relative).resolve()


def _format_report(report: dict[str, Any]) -> str:
    if report.get("mode") == "with-skill":
        return (
            f"mode=with-skill total={report['total']} passed={report['passed']} "
            f"failed={report['failed']} pass_rate={report['pass_rate']:.3f}"
        )
    if report.get("mode") == "without-skill":
        return (
            f"mode=without-skill total={report['total']} passed={report['passed']} "
            f"failed={report['failed']} pass_rate={report['pass_rate']:.3f}"
        )
    return (
        f"title={report['title']}\n"
        f"with-skill: total={report['with_skill']['total']} passed={report['with_skill']['passed']} "
        f"failed={report['with_skill']['failed']} pass_rate={report['with_skill']['pass_rate']:.3f}\n"
        f"without-skill: total={report['without_skill']['total']} passed={report['without_skill']['passed']} "
        f"failed={report['without_skill']['failed']} pass_rate={report['without_skill']['pass_rate']:.3f}\n"
        f"pass-rate-delta={report['pass_rate_delta']:.3f}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
