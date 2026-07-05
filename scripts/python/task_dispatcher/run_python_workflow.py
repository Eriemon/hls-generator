"""执行 readable-python-generator 的任务分类和质量门工作流。

机器可读 stdout 协议: json。
"""

# 兼容包内导入和脚本直接运行两种入口
from __future__ import annotations

# 兼容包内导入和脚本直接运行两种入口
import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 为脚本直接运行补齐 skill 根目录导入路径
def configure_import_path() -> None:
    """把 skill 根目录加入导入路径，兼容直接运行脚本。

    参数:
        无外部业务参数。

    返回:
        无业务返回值；函数只在脚本直跑时修正导入搜索路径。
    """

    # 根据当前脚本位置定位 skill 根目录
    path_skill_root = Path(__file__).resolve().parents[3]  # skill 包根目录

    # sys.path 使用字符串路径进行成员比较
    str_skill_root_text = str(path_skill_root)  # skill 根目录文本路径

    # 只有缺失时才插入，避免重复污染模块搜索路径
    if str_skill_root_text not in sys.path:

        # 把 skill 根目录加入模块搜索路径
        sys.path.insert(0, str_skill_root_text)

# 在包内导入前完成路径修正
configure_import_path()

# 兼容包内导入和脚本直接运行两种入口
from scripts.python.quality_gate.profiles import ProfileConfig, get_profile_config, with_current_project_style
from scripts.python.quality_gate.report import GateReport
from scripts.python.quality_gate.rule_runner import check_target
from scripts.python.quality_gate.variable_naming_rules import apply_safe_renames
from scripts.python.task_dispatcher.classify_python_task import classify_request

# 列出允许进入自动注释重写计划的规则编号。
COMMENT_REWRITE_RULES = {"PG025", "PG030", "PG031", "PG033", "PG035", "PG036", "PG037", "PG043", "PG044"}  # 需要 agent 语义重写的规则编号

# 整理注释位置和文本，供当前规则检查注释质量。
SPECIAL_COMMENT_MARKERS = (  # 必须在注释重写阶段原样保留的工具标记集合
    "coding:",  # 编码声明前缀
    "coding=",  # 编码声明等号写法
    "copyright",  # 版权声明
    "license",  # 许可证声明
    "noqa",  # lint 豁免标记
    "pragma:",  # pragma 控制标记
    "type: ignore",  # 类型检查豁免标记
)  # 需要原样保留的特殊注释标记

# `build_parser` 构建参数解析器。
def build_parser() -> argparse.ArgumentParser:
    """构造执行器命令行参数。

    返回:
        返回构造完成的对象。
    """

    # 构造 CLI 参数解析器，集中声明入口支持的参数。
    parser = argparse.ArgumentParser(description="Run Python workflow dispatcher for readable-python-generator.")  # 参数解析器

    # 声明自由文本参数，兼容不带选项的任务描述。
    parser.add_argument("text", nargs="*", help="Request text to dispatch")

    # 声明请求文本选项，允许直接传入任务描述。
    parser.add_argument("--prompt", default="", help="Request text to dispatch")

    # 声明请求文件选项，允许从 UTF-8 文本读取任务描述。
    parser.add_argument("--prompt-file", default="", help="UTF-8 file containing request text")

    # 声明质量门 profile 选项，限制规则组合的选择范围。
    parser.add_argument("--profile", default="", help="Override recommended quality gate profile")

    # 声明风格叠加选项，允许 CLI 启用 current-project 规则。
    parser.add_argument("--style", default="current-project", choices=("default", "current-project"))

    # 这个开关允许调用方显式启用安全子集变量重命名落盘。
    parser.add_argument("--write-renames", action="store_true", help="Apply conservative token-level variable renames")

    # 这个开关用于保留只读分析流程，避免 modify 默认行为写回文件。
    parser.add_argument("--no-write-renames", action="store_true", help="Disable default safe-subset variable renames")

    # 返回已完成参数声明的 CLI 解析器。
    return parser

# 从 CLI 参数、文件或 stdin 读取待调度请求
def read_prompt(args: argparse.Namespace) -> str:
    """从 CLI 参数或 stdin 读取请求文本。

    参数:
        args: argparse 解析后的命令行参数对象。

    返回:
        合并后的请求文本。
    """

    # 显式 --prompt 优先级最高，便于脚本调用传入完整请求
    if args.prompt:

        # 返回命令行直接提供的请求文本
        return args.prompt

    # --prompt-file 允许调用方把长请求放入 UTF-8 文本文件
    if args.prompt_file:

        # 返回文件中保存的请求文本
        return Path(args.prompt_file).read_text(encoding="utf-8")

    # 位置参数兼容不带选项的短请求
    if args.text:

        # 将多个位置参数还原为一段请求文本
        return " ".join(args.text)

    # 没有显式参数时从管道读取请求
    return sys.stdin.read()

# 保留请求中确实存在的 Python 文件目标
def existing_targets(target_paths: list[str]) -> list[Path]:
    """保留请求中真实存在的 .py 目标。

    参数:
        target_paths: 目标路径列表

    返回:
        可直接交给质量门检查的 Path 列表。
    """

    # 保持请求中的路径顺序，后续报告按相同顺序输出
    list_targets: list[Path] = []  # 已存在的 Python 目标

    # 逐个解析请求中提取出的路径文本
    for str_raw_path in target_paths:

        # 将文本路径转为 Path，便于检查存在性和后缀
        path_path = Path(str_raw_path)  # 待检查目标路径

        # 只保留已经存在的 Python 文件，避免质量门读取不存在路径
        if path_path.exists() and path_path.suffix.casefold() == ".py":

            # 记录可运行质量门的目标文件
            list_targets.append(path_path)

    # 返回过滤后的目标列表
    return list_targets

# 提取工作流 JSON 需要展示的质量门摘要
def report_summary(report: GateReport) -> dict[str, Any]:
    """提取执行器 JSON 中需要的质量门摘要。

    参数:
        report: 质量门报告

    返回:
        包含目标、profile、style、summary 和按规则计数的字典。
    """

    # 直接返回前先压缩成稳定摘要，避免 workflow JSON 混入整份质量门明细。
    return {
        "target": report.target,
        "profile": report.profile,
        "style": report.style,
        "summary": report.to_dict()["summary"],
        "issues_by_rule": report.count_by_rule(),
    }

# `is_special_comment` 判断special注释。
def is_special_comment(text: str) -> bool:
    """判断注释是否属于必须保留的工具或法律声明。

    参数:
        text: 当前规则正在分析的文本内容。

    返回:
        布尔值，表示判断special注释是否成立。
    """

    # 去除strippedtext两端空白，避免格式差异影响规则判断。
    str_stripped_text = text.strip()  # 去掉边界空白，避免格式差异影响判断

    # 生成loweredtext的小写副本，确保关键词匹配不受大小写影响。
    str_lowered_text = str_stripped_text.casefold()  # 生成大小写无关文本，保证关键词匹配稳定

    # shebang 必须保留，否则脚本入口可能失效。
    if str_stripped_text.startswith("#!"):

        # 文件首行解释器声明属于特殊注释。
        return True

    # 返回任一关键词命中后的布尔判断结果
    return any(marker in str_lowered_text for marker in SPECIAL_COMMENT_MARKERS)

# `collect_preserved_comments` 收集preserved注释集合。
def collect_preserved_comments(filepath: str | Path) -> list[dict[str, Any]]:
    """收集 shebang、编码、noqa 等特殊注释保留项。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。

    返回:
        返回收集到的候选项和上下文信息。
    """

    # 解析pathpath所在位置，后续文件检查使用同一绝对路径。
    path_path = Path(filepath)  # 路径路径

    # 文件不存在时没有可保留的特殊注释。
    if not path_path.exists():

        # 调用方会继续处理其他质量门报告。
        return []

    # 保留项只记录 shebang、编码声明、lint 豁免等特殊注释。
    list_preserved_comments: list[dict[str, Any]] = []  # 特殊注释保留项

    # 按行扫描源码，定位空行、注释和语句边界。
    for int_line_number, str_line_text in enumerate(path_path.read_text(encoding="utf-8").splitlines(), start=1):

        # 统一提取当前源码行的去空白版本，供注释类型判断复用。
        str_stripped_text = str_line_text.strip()  # 当前源码行的规整化文本

        # 普通代码行不会进入保留注释清单。
        if not str_stripped_text.startswith("#"):

            # 非注释源码行不需要进入 preserve 清单。
            continue

        # 特殊注释需要在后续重写计划中明确保护。
        if is_special_comment(str_stripped_text):

            # 记录文件、行号和原文，避免 agent 重写时误删。
            list_preserved_comments.append({  # 特殊注释的文件位置与原文快照
                "filepath": str(path_path),
                "line": int_line_number,
                "text": str_stripped_text,
            })

    # 返回本轮扫描收集到的候选列表。
    return list_preserved_comments

# 根据质量门发现项生成注释重写计划
def comment_rewrite_plan(gate_reports: list[GateReport]) -> dict[str, Any]:
    """根据质量门问题生成 agent 语义重写注释时使用的修复清单。

    参数:
        gate_reports: 质量门报告列表

    返回:
        包含保留注释、可删除范围和重写目标的计划字典。
    """

    # 保留项用于保护 shebang、编码声明和 lint 豁免注释。
    list_preserve_comments: list[dict[str, Any]] = []  # 特殊注释保留清单

    # 删除范围只定位问题注释行，不生成替换文本。
    list_remove_comment_ranges: list[dict[str, Any]] = []  # 列表remove注释ranges

    # 重写目标保存规则发现项，交给 agent 阅读上下文后处理。
    list_rewrite_targets: list[dict[str, Any]] = []  # 保存用户请求中的目标文件，决定质量门检查范围

    # 同一目标可能产生多份报告，特殊注释只扫描一次。
    set_seen_preserve_paths: set[str] = set()  # 解析文件系统位置，保证后续读写使用一致路径

    # 逐份质量门报告汇总注释治理计划。
    for report in gate_reports:

        # 未扫描过的目标先补充特殊注释保留项。
        if report.target not in set_seen_preserve_paths:

            # 合并子扫描产物，保持最终报告覆盖所有候选。
            list_preserve_comments.extend(collect_preserved_comments(report.target))

            # 目标路径去重后可避免重复保留项。
            set_seen_preserve_paths.add(report.target)

        # 汇总规则发现项，生成报告统计。
        for issue in report.issues:

            # 只有注释质量相关规则进入重写计划。
            if issue.code not in COMMENT_REWRITE_RULES:

                # 非注释类规则不参与 comment rewrite 计划。
                continue

            # 保留原始发现项，报告中不提供模板替换文本。
            list_rewrite_targets.append(issue.to_dict())

            # 这些规则对应的注释行可以作为删除或重写位置。
            if issue.code in {"PG030", "PG031", "PG033", "PG035", "PG036", "PG037"}:

                # 行范围只做定位，具体改写必须由 agent 判断语义。
                list_remove_comment_ranges.append({  # 需要删除或重写的单行注释定位
                    "filepath": issue.filepath,
                    "start_line": issue.line,
                    "end_line": issue.line,
                    "reason": issue.code,
                })

    # 计划不包含 suggested_comment、replacement_text 等自动生成字段。
    return {
        "preserve_comments": list_preserve_comments,
        "remove_comment_ranges": list_remove_comment_ranges,
        "rewrite_targets": list_rewrite_targets,
    }

# `run_quality_gate` 运行质量门质量门。
def run_quality_gate(target: Path, profile: str, style: str) -> GateReport:
    """对单个目标运行现有质量门和变量命名门。

    参数:
        target: 需要运行质量门或外部工具的目标路径。
        profile: 用户选择或调度器推荐的语义 profile 名称。
        style: 风格名称

    返回:
        返回当前执行流程产生的退出状态或报告结果。
    """

    # 加载质量门 profile，决定本次检查启用的规则组合。
    profile_config_base_config: ProfileConfig = get_profile_config(profile)  # 规则配置

    # 默认先沿用基础 profile，只有 current-project 请求才覆盖它。
    profile_config_config: ProfileConfig = profile_config_base_config  # 默认执行使用的规则配置

    # current-project 风格会在基础 profile 上叠加本仓库的局部治理规则。
    if style == "current-project":

        # 用仓库局部治理规则覆盖默认 profile 配置。
        profile_config_config = with_current_project_style(profile_config_base_config)  # 叠加 current-project 风格规则后的配置

    # 收集规则发现项，生成最终报告。
    list_issues = check_target(target, profile_config_config)  # 质量门发现项集合

    # 这里返回轻量报告对象，供 workflow 汇总阶段统一拼装 JSON。
    return GateReport(
        target=str(target),
        profile=profile,
        style=style,
        issues=list_issues,
        tool_results=[],
    )

# 根据分类结果生成 agent 可执行的工作流步骤
def workflow_steps(classification: dict[str, Any], has_existing_targets: bool) -> list[str]:
    """返回人类和 agent 都可复用的工作流步骤。

    参数:
        classification: 请求分类结果
        has_existing_targets: 目标路径是否已存在

    返回:
        按执行顺序排列的工作流步骤名称。
    """

    # 先把分类结果标准化为字符串，后续分支统一按同一字段判断。
    str_task_type = str(classification["task_type"])  # 当前请求的任务类型

    # generate 路径必须先写合同、再生成代码、最后进入生成后闭环门禁。
    if str_task_type == "generate":

        # 返回新代码任务的固定执行顺序。
        return [
            "intent_contract",
            "write_intent_contract",
            "generate_python_code",
            "run_comment_quality_gate",
            "run_typed_variable_naming",
            "run_syntax_check",
        ]

    # modify 路径强调已有代码质量门、语义审查、修复和复检闭环。
    if str_task_type == "modify":

        # 返回已有代码治理流程，保持先检查后修复再复检的顺序。
        return [
            "run_existing_quality_gate",
            "inspect_real_code_semantics",
            "repair_comments_by_agent_when_needed",
            "run_typed_variable_naming",
            "rerun_quality_gate",
        ]

    # explain 请求默认只分类，不主动修改代码。
    list_steps = ["classify_only"]  # 列表步骤集合

    # 存在真实目标时可追加只读质量摘要步骤。
    if has_existing_targets:

        # explain 流程只报告问题，不写入文件。
        list_steps.append("read_only_quality_summary")

    # 解释类请求只返回只读步骤矩阵，不触发任何写回动作。
    return list_steps

# `build_workflow_result` 构建工作流结果。
def build_workflow_result(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """执行分类、可选重命名和质量门检查。

    参数:
        args: argparse 解析后的命令行参数对象。

    返回:
        返回构造完成的对象。
    """

    # 请求文本可能来自参数或标准输入，统一在这里读取。
    str_prompt = read_prompt(args)  # 文本请求文本

    # 分类结果决定 generate、modify、explain 的执行矩阵。
    dict_classification = classify_request(str_prompt)  # Python 请求分类结果

    # profile 覆盖遵循 CLI 优先、分类器推荐兜底的规则。
    str_profile = args.profile or str(dict_classification["recommended_profile"])  # 读取规则配置，决定本次检查启用哪些约束

    # 只保留真实存在的 .py 目标，避免 workflow 假装已经对虚构路径执行检查。
    list_targets = existing_targets(list(dict_classification["target_paths"]))  # 列表目标集合

    # 安全重命名结果按目标文件分组，便于用户审阅。
    dict_rename_results: dict[str, dict[str, str]] = {}  # 映射重命名结果集合

    # modify/explain 目标的质量门报告会汇总到最终 JSON。
    list_gate_reports: list[GateReport] = []  # 质量门报告缓存

    # task_type 会直接决定是否允许自动重命名以及后续是否跑已有代码质量门。
    str_task_type = str(dict_classification["task_type"])  # 当前 workflow 的任务类型

    # 先计算 modify 流程下默认是否允许安全重命名。
    bool_default_safe_renames = (not args.no_write_renames and str_task_type == "modify")  # 默认安全重命名开关

    # 用户显式开启时优先放行，否则沿用 modify 流程的默认策略。
    bool_should_apply_safe_renames = args.write_renames or bool_default_safe_renames  # 当前 workflow 是否执行安全子集变量重命名

    # modify 流程默认执行安全子集重命名，用户可通过参数关闭。
    if bool_should_apply_safe_renames:

        # 每个真实目标独立执行安全重命名。
        for target in list_targets:

            # 重命名工具只写入它能静态证明安全的标识符。
            dict_rename_results[str(target)] = apply_safe_renames(target)  # 每个目标的安全重命名结果

    # 只有已有代码目标才运行质量门。
    if str_task_type in {"modify", "explain"}:

        # 每个目标独立生成报告，避免多文件问题混在一起。
        for target in list_targets:

            # 报告对象保留发现项、profile、style 和工具结果。
            list_gate_reports.append(run_quality_gate(target, str_profile, args.style))

    # 注释治理计划只给出定位信息，不自动生成替换注释。
    dict_rewrite_plan = comment_rewrite_plan(list_gate_reports)  # 注释治理定位计划

    # generate 流程才需要声明生成后必须补跑的三项检查。
    list_post_generation_checks: list[str] = []  # generate 路径的后置检查

    # 只有生成新代码时才补充生成后检查矩阵。
    if dict_classification["task_type"] == "generate":

        # 固定返回生成后必须依次执行的检查名称。
        list_post_generation_checks = ["post_generation_quality_gate", "typed_variable_naming", "syntax_check"]  # generate 路径固定后置检查序列

    # CLI 输出汇总分类、执行步骤、重命名结果和质量门报告。
    dict_result = {  # workflow 对外暴露的结构化执行结果
        "classification": dict_classification,  # 原始分类结果
        "required_checks": dict_classification["required_checks"],  # 分类器要求执行的检查集合
        "check_applicability": dict_classification["check_applicability"],  # 各检查项是否适用于当前请求
        "needs_target_or_code": dict_classification["needs_target_or_code"],  # 当前请求是否必须补充目标或代码
        "workflow_steps": workflow_steps(dict_classification, bool(list_targets)),  # 建议执行步骤
        "post_generation_checks": list_post_generation_checks,  # 新代码生成完成后必须补跑的检查
        "rename_results": dict_rename_results,  # 各目标文件的安全重命名结果
        "gate_reports": [report_summary(report) for report in list_gate_reports],  # 各目标的质量门摘要
        "gate_summary": list_gate_reports[0].to_dict()["summary"] if list_gate_reports else {},  # 首个目标的汇总摘要
        "has_blockers": any(report.has_blocker() for report in list_gate_reports),  # 是否仍存在 blocker
        "comment_rewrite_required": bool(dict_rewrite_plan["rewrite_targets"]),  # 是否需要 agent 介入重写注释
        "comment_rewrite_plan": dict_rewrite_plan,  # 供后续 agent 精准定位需重写的注释
    }  # 工作流命令输出的结构化载荷

    # 返回成功退出码和完整 workflow 结果，供 CLI 入口统一输出。
    return 0, dict_result

# 工作流调度器命令行入口
def main(argv: list[str] | None = None) -> int:
    """执行工作流 CLI 并输出稳定 JSON。

    参数:
        argv: 命令行入口接收的参数序列；为 None 时使用进程参数。

    返回:
        返回命令行入口使用的进程退出码。
    """

    # 先构造唯一的 CLI 参数契约，保证脚本直跑与测试入口行为一致。
    parser = build_parser()  # 当前命令入口使用的参数解析器

    # 解析命令行输入，决定本次运行使用的目标和开关。
    args = parser.parse_args(argv)  # 命令行参数

    # 主流程同时需要退出码和 JSON 可序列化结果。
    tuple_exit_code, tuple_result = build_workflow_result(args)  # CLI 退出码和工作流结果

    # 当前 CLI 明确声明机器可读 stdout 协议，因此直接写出单个 JSON 结果。
    sys.stdout.write(json.dumps(tuple_result, ensure_ascii=False, indent=2) + "\n")

    # 把 build_workflow_result 计算出的退出码交还给 shell。
    return tuple_exit_code

# 脚本直接执行时启动 CLI 入口。
if __name__ == "__main__":

    # 直接透传 main 的退出码，便于上层脚本据此判断 workflow 是否成功。
    raise SystemExit(main())
