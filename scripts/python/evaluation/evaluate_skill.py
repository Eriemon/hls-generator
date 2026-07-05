#!/usr/bin/env python3
"""
评估 Erie HLS skill 语料并计算有无技能时的通过率差异。

命令行协议:
    默认 stdout 输出紧凑文本摘要；传入 --json 时 stdout 输出完整 JSON 报告。
"""

# 延迟解析注解，避免 CLI 在旧解释器环境中提前求值泛型。
from __future__ import annotations

# 标准库负责 CLI 参数解析、JSON 语料读取和路径定位。
import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

# skill 根目录用于解析 evals.json 中相对 skill 的资源路径。
SKILL_ROOT = Path(__file__).resolve().parents[3]  # erie-hls-generator 技能根目录。

# 仓库根目录用于兼容 evals.json 中相对工作区的资源路径。
REPO_ROOT = SKILL_ROOT.parents[1]  # HLSGenerator 工作区根目录。

# CLI 入口读取评估语料并输出指定模式的报告。
def main(argv: list[str] | None = None) -> int:
    """
    运行评估 CLI 并根据 with-skill 结果返回进程状态。

    参数:
        argv: 可选命令行参数；为 None 时使用当前进程参数。

    返回:
        返回 0 表示 with-skill 场景无失败用例，否则返回 1。
    """

    # argparse 负责保持历史 CLI 参数和帮助文本兼容。
    parser = argparse.ArgumentParser(  # 评估命令参数解析器。
        description="Evaluate the Erie HLS skill corpus and expected with-skill delta."  # CLI 帮助中的命令说明。
    )

    # evals 路径默认指向技能内置语料文件。
    parser.add_argument(
        "--evals",
        default=str(SKILL_ROOT / "evals" / "evals.json"),
        help="Path to evals.json.",
    )

    # mode 控制只输出单侧结果或完整对比报告。
    parser.add_argument("--mode", choices=("with-skill", "without-skill", "both"), default="both")

    # --json 保留机器可读报告输出协议。
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a compact text summary.")

    # 解析后的命名空间集中承载调用方选择的输入与输出模式。
    namespace_args: argparse.Namespace = parser.parse_args(argv)  # CLI 参数解析结果。

    # 将 evals 输入路径显式转成 Path，后续读取逻辑只接收路径对象。
    path_evals: Path = Path(namespace_args.evals)  # evals.json 实际输入路径。

    # 读取评估语料 JSON，保持原始字段结构交给 evaluate_payload 处理。
    dict_payload: dict[str, Any] = json.loads(path_evals.read_text(encoding="utf-8"))  # 评估用例语料载荷。

    # 执行纯数据评估，便于测试直接调用。
    dict_report: dict[str, Any] = evaluate_payload(dict_payload)  # 完整有技能/无技能对比报告。

    # 按调用方指定模式裁剪输出载荷。
    if namespace_args.mode == "with-skill":

        # 只输出启用技能后的汇总，保留历史 mode 字段。
        dict_output: dict[str, Any] = {"mode": "with-skill", **dict_report["with_skill"]}  # with-skill 模式报告。

    # 单独基线模式只保留无技能侧摘要，方便比对能力增益。
    elif namespace_args.mode == "without-skill":

        # 把人工基线统计裁成单侧视图，便于单独检查无技能时的表现。
        dict_output = {"mode": "without-skill", **dict_report["without_skill"]}  # 无技能单侧统计输出载荷。

    # 默认模式返回完整对比报告，供人工审阅两侧差异。
    else:

        # both 模式返回完整报告，包含 pass_rate_delta 和两侧用例明细。
        dict_output = dict_report  # both 模式下直接输出完整评估报告。

    # JSON 模式是显式声明的机器可读 stdout 协议。
    if namespace_args.json:

        # JSON 模式直接写出协议正文，保留完整结构供上游程序消费。
        sys.stdout.write(json.dumps(dict_output, indent=2, ensure_ascii=False) + "\n")

    # 文本模式只输出紧凑摘要，保持终端审阅时的信息密度。
    else:

        # 紧凑摘要沿用历史文本结构，避免干扰依赖该格式的人工流程。
        sys.stdout.write(_format_report(dict_output) + "\n")

    # 启用技能场景仍有失败用例时让 CI 以失败状态退出。
    return 0 if dict_report["with_skill"]["failed"] == 0 else 1

# 纯函数评估 evals.json 载荷并生成对比报告。
def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    评估所有用例并汇总 with-skill 与 without-skill 两组结果。

    参数:
        payload: evals.json 解析后的字典载荷。

    返回:
        返回包含版本、标题、设计模式、两侧汇总、通过率差值和用例明细的报告。
    """

    # cases 是 evals.json 的核心用例列表，缺失时按空语料处理。
    list_cases: list[dict[str, Any]] = cast(list[dict[str, Any]], payload.get("cases", []))  # 待评估用例集合。

    # 启用技能明细记录真实资源检查是否符合预期。
    list_with_results: list[dict[str, Any]] = []  # 启用技能时每个用例的评估记录。

    # without-skill 明细记录语料中声明的基线预期。
    list_without_results: list[dict[str, Any]] = []  # 禁用技能时每个用例的基线记录。

    # 逐个用例生成两侧结果，保证 pass-rate delta 来源透明。
    for dict_case in list_cases:

        # 单个用例检查返回通过状态和定位证据。
        tuple_case_result: tuple[bool, list[str]] = _evaluate_case(dict_case)  # 当前用例的检查结果与证据。

        # 取出启用技能后的真实通过状态。
        bool_with_pass: bool = tuple_case_result[0]  # 当前用例真实检查是否符合预期。

        # 取出文件和关键词检查证据，写入报告明细。
        list_evidence: list[str] = tuple_case_result[1]  # 当前用例的资源检查证据列表。

        # without-skill 结果由语料的人工基线字段直接给出。
        bool_without_pass: bool = bool(dict_case.get("without_skill_expected_pass", False))  # 无技能基线是否预期通过。

        # 记录启用技能后的实际检查结果和证据。
        list_with_results.append(
            {
                "id": dict_case["id"],
                "title": dict_case["title"],
                "passed": bool_with_pass,
                "expected_pass": bool(dict_case.get("with_skill_expected_pass", True)),
                "evidence": list_evidence,
            }
        )

        # 记录禁用技能时的预期基线，便于计算能力增益。
        list_without_results.append(
            {
                "id": dict_case["id"],
                "title": dict_case["title"],
                "passed": bool_without_pass,
                "expected_pass": bool_without_pass,
                "baseline_reason": dict_case.get("without_skill_reason", ""),
            }
        )

    # 把启用技能侧结果压缩成计数摘要，供最终报告直接复用。
    dict_with_summary: dict[str, Any] = _summarize_results(list_with_results)  # with-skill 汇总结果。

    # 把 eval 语料声明的人工基线整理成统计块，供后面计算收益差值。
    dict_without_summary: dict[str, Any] = _summarize_results(list_without_results)  # 人工基线统计摘要。

    # 通过率差值衡量技能相对无技能基线的收益。
    float_pass_rate_delta: float = dict_with_summary["pass_rate"] - dict_without_summary["pass_rate"]  # 两组通过率差值。

    # 返回完整评估报告，字段名保持历史兼容。
    return {
        "version": payload.get("version", 1),
        "title": payload.get("title", "Erie HLS Skill Evaluation"),
        "design_patterns": payload.get("design_patterns", []),
        "with_skill": dict_with_summary,
        "without_skill": dict_without_summary,
        "pass_rate_delta": float_pass_rate_delta,
        "cases": {
            "with_skill": list_with_results,
            "without_skill": list_without_results,
        },
    }

# 汇总单侧评估明细，生成通过率统计。
def _summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    将用例明细压缩成计数和通过率摘要。

    参数:
        results: 单侧评估结果列表。

    返回:
        返回 total、passed、failed、pass_rate 和原始 results 字段。
    """

    # total 是通过率分母，空列表时通过率固定为 0.0。
    int_total: int = len(results)  # 当前结果集合的用例总数。

    # passed 只统计显式 passed 为真值的用例。
    int_passed: int = sum(1 for dict_item in results if dict_item["passed"])  # 当前结果集合的通过用例数。

    # failed 由总数减通过数得到，避免重复遍历。
    int_failed: int = int_total - int_passed  # 当前结果集合的失败用例数。

    # 组装评估报告中的计数摘要和原始用例明细。
    return {
        "total": int_total,
        "passed": int_passed,
        "failed": int_failed,
        "pass_rate": (int_passed / int_total) if int_total else 0.0,
        "results": results,
    }

# 检查一个评估用例的文件与关键词证据。
def _evaluate_case(case: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    检查单个 with-skill 用例声明的文件和关键词证据。

    参数:
        case: evals.json 中的单个用例字典。

    返回:
        返回该用例是否符合预期，以及文件/关键词检查证据列表。
    """

    # evidence 记录每个必需文件和关键词的命中状态，便于失败定位。
    list_evidence: list[str] = []  # 当前用例的资源检查证据。

    # bool_passed 会随任一缺失文件或缺失关键词变为 False。
    bool_passed: bool = True  # 当前用例真实证据是否全部满足。

    # 逐项确认 required_files 中声明的资源是否存在。
    for str_path_text in case.get("required_files", []):

        # 将语料中的相对路径解析到技能根或仓库根。
        path_required_file: Path = _resolve_case_path(str_path_text)  # 必需资源的实际候选路径。

        # 文件存在性直接决定该项证据状态。
        bool_file_exists: bool = path_required_file.exists()  # 必需文件是否存在。

        # 写入文件证据，保持原有 file:<path>:ok/missing 格式。
        list_evidence.append(f"file:{str_path_text}:{'ok' if bool_file_exists else 'missing'}")

        # 任一必需文件缺失都会使真实检查不通过。
        bool_passed = bool_passed and bool_file_exists  # 文件检查后的累计通过状态。

    # 逐项检查 required_terms 中声明的文件关键词。
    for dict_entry in case.get("required_terms", []):

        # 每个关键词检查项先解析其目标文件路径。
        path_term_file: Path = _resolve_case_path(dict_entry["file"])  # 关键词检查目标文件路径。

        # 读取目标文件文本，关键词匹配保持简单包含语义。
        str_text: str = path_term_file.read_text(encoding="utf-8")  # 关键词检查目标文本。

        # 遍历当前文件必须包含的关键词集合。
        for str_term in dict_entry.get("terms", []):

            # 单个关键词的命中状态进入证据列表。
            bool_term_found: bool = str_term in str_text  # 当前关键词是否出现在目标文件中。

            # 记录关键词证据，格式保持 term:<file>:<term>:ok/missing。
            list_evidence.append(f"term:{dict_entry['file']}:{str_term}:{'ok' if bool_term_found else 'missing'}")

            # 任一关键词缺失都会使真实检查不通过。
            bool_passed = bool_passed and bool_term_found  # 关键词检查后的累计通过状态。

    # with_skill_expected_pass 表示这个用例在启用技能时应达到的结果。
    bool_expected: bool = bool(case.get("with_skill_expected_pass", True))  # 启用技能时的预期通过状态。

    # 返回真实检查是否符合预期，以及完整证据链。
    return bool_passed == bool_expected, list_evidence

# 解析 evals 语料中声明的资源路径。
def _resolve_case_path(path_text: str) -> Path:
    """
    将用例中的相对路径解析到技能根或仓库根。

    参数:
        path_text: evals.json 中声明的相对路径文本。

    返回:
        返回已存在的候选路径；若都不存在，则返回相对技能根的解析路径。
    """

    # evals.json 中的路径按相对路径处理，不在此处访问外部绝对输入。
    path_relative: Path = Path(path_text)  # 用例声明的相对资源路径。

    # 先查技能根，再查仓库根，兼容两种历史写法。
    for path_base in (SKILL_ROOT, REPO_ROOT):

        # resolve 统一路径格式，便于后续证据输出稳定。
        path_candidate: Path = (path_base / path_relative).resolve()  # 当前根目录下的候选路径。

        # 命中第一个实际存在的候选路径。
        if path_candidate.exists():

            # 返回存在路径，避免后续读取走 fallback。
            return path_candidate

    # 若资源不存在，返回技能根下的预期位置用于生成 missing 证据。
    return (SKILL_ROOT / path_relative).resolve()

# 格式化人工阅读用的紧凑评估摘要。
def _format_report(report: dict[str, Any]) -> str:
    """
    将评估报告转换成历史兼容的紧凑文本摘要。

    参数:
        report: evaluate_payload 或单侧模式裁剪后的报告字典。

    返回:
        返回一行或多行文本摘要。
    """

    # 单独 with-skill 模式只输出启用技能后的摘要行。
    if report.get("mode") == "with-skill":

        # 保持历史字段顺序，方便脚本做简单文本匹配。
        return (
            f"mode=with-skill total={report['total']} passed={report['passed']} "
            f"failed={report['failed']} pass_rate={report['pass_rate']:.3f}"
        )

    # 单独 without-skill 模式只输出无技能基线摘要行。
    if report.get("mode") == "without-skill":

        # 保持 without-skill 输出与 with-skill 输出结构对称。
        return (
            f"mode=without-skill total={report['total']} passed={report['passed']} "
            f"failed={report['failed']} pass_rate={report['pass_rate']:.3f}"
        )

    # 完整报告输出标题、两侧摘要和通过率差值。
    return (
        f"title={report['title']}\n"
        f"with-skill: total={report['with_skill']['total']} passed={report['with_skill']['passed']} "
        f"failed={report['with_skill']['failed']} pass_rate={report['with_skill']['pass_rate']:.3f}\n"
        f"without-skill: total={report['without_skill']['total']} passed={report['without_skill']['passed']} "
        f"failed={report['without_skill']['failed']} pass_rate={report['without_skill']['pass_rate']:.3f}\n"
        f"pass-rate-delta={report['pass_rate_delta']:.3f}"
    )

# 脚本执行时将 CLI 返回码交给 Python 解释器。
if __name__ == "__main__":

    # raise SystemExit 保持命令行退出码可被 CI 捕获。
    raise SystemExit(main())
