"""提供质量门命令行参数解析和报告输出流程。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入 CLI 入口所需的参数解析器。
import argparse

# 导入质量门主流程依赖的规则运行、配置和报告能力。
from .rule_runner import check_target
from .profiles import ProfileConfig, get_profile_config, list_profiles, with_current_project_style
from .report import GateReport
from .tool_runner import EXTERNAL_TOOL_COMMANDS, run_external_tools

# `build_parser` 构建参数解析器。
def build_parser() -> argparse.ArgumentParser:
    """构建当前 CLI 的参数解析器。

    返回:
        返回构造完成的对象。
    """

    # 构造 CLI 参数解析器，集中声明入口支持的参数。
    parser = argparse.ArgumentParser(description="Readable Python quality gate")  # 参数解析器

    # 声明待检查文件或目录的位置参数。
    parser.add_argument("target", help="Python file or directory to inspect")

    # 声明质量门 profile 选项，限制规则组合的选择范围。
    parser.add_argument("--profile", default="scientific", choices=list_profiles())

    # 允许调用方选择是否附带运行外部工具检查。
    parser.add_argument("--run-tools", default="none", choices=sorted(EXTERNAL_TOOL_COMMANDS))

    # 风格覆盖层控制是否叠加 current-project 约束。
    parser.add_argument(
        "--style",
        default="default",
        choices=("default", "current-project"),
        help="Optional readability style overlay",
    )

    # project-root 只传给外部工具，避免其在错误目录下解析配置。
    parser.add_argument("--project-root", default=None, help="Working directory for external tools")

    # 声明 Markdown 报告输出路径，便于人工审阅质量门结果。
    parser.add_argument("--markdown", default=None, help="Markdown report output path")

    # 声明 JSON 报告输出路径，便于自动化流程读取结果。
    parser.add_argument("--json", default=None, help="JSON report output path")

    # warn-only 适合探索性扫描，保持成功退出码方便批量收集报告。
    parser.add_argument("--warn-only", action="store_true", help="Always return exit code 0")

    # strict-tools 打开后，缺失外部工具也会让质量门失败。
    parser.add_argument("--strict-tools", action="store_true", help="Missing external tools fail the gate")

    # 声明 warning 失败策略，供 CI 使用更严格的退出码。
    parser.add_argument("--fail-on-warning", action="store_true", help="Warnings fail the gate in final strict mode")

    # 返回已完成参数声明的 CLI 解析器。
    return parser

# CLI 主流程负责解析参数、运行规则、输出报告并返回退出码。
def main(argv: list[str] | None = None) -> int:
    """组织命令行入口的参数解析、执行和退出码。

    参数:
        argv: 命令行入口接收的参数序列；为 None 时使用进程参数。

    返回:
        返回命令行入口使用的进程退出码。
    """

    # 主流程先拿到统一解析器，后续目标路径、风格开关和报告选项都从这一个入口读取。
    parser = build_parser()  # 复用 build_parser 中集中声明的 CLI 参数契约

    # 解析命令行输入，决定本次运行使用的目标和开关。
    args = parser.parse_args(argv)  # 命令行参数

    # 加载质量门 profile，决定本次检查启用的规则组合。
    profile_config_profile_config: ProfileConfig = get_profile_config(args.profile)  # 基础质量门配置对象

    # current-project 覆盖层会额外启用中文注释、空行和命名规则。
    if args.style == "current-project":

        # 叠加 current-project 覆盖层，让脚本遵守当前仓库中文风格约束。
        profile_config_profile_config = with_current_project_style(profile_config_profile_config)  # 当前项目规则配置对象

    # 收集规则发现项，生成最终报告。
    list_issues = check_target(args.target, profile_config_profile_config)  # 质量门发现项集合

    # 外部工具结果与内置规则结果合并展示，但不改变规则发现项本身。
    list_tool_results = run_external_tools(args.run_tools, args.target, cwd=args.project_root)  # 外部工具运行结果

    # 报告对象统一负责 Markdown/JSON 输出和失败判定。
    gate_report_gate_report: GateReport = GateReport(  # 汇总本轮扫描结果与外部工具执行状态
        target=args.target,  # 本轮检查目标
        profile=args.profile,  # 当前启用的基础 profile
        style=args.style,  # 当前叠加的风格覆盖层
        issues=list_issues,  # 规则发现项明细
        tool_results=list_tool_results,  # 外部工具结果明细
    )

    # 先读取 blocker 数量，终端摘要要优先告诉调用方是否仍有阻断项。
    int_blocker_count = gate_report_gate_report.count_level("BLOCKER")  # 当前检查范围里的 blocker 总数

    # warning 数量用于提示当前结果是否还停留在“可继续收敛”的阶段。
    int_warning_count = gate_report_gate_report.count_level("WARNING")  # 判断是否仍有未清理的中等级别问题

    # note 数量单独保留，便于区分“已经干净”还是“只剩收尾型记录”。
    int_note_count = gate_report_gate_report.count_level("NOTE")  # 说明是否只剩低优先级整理提示

    # 终端只输出带前缀的短摘要，详细结构化结果统一写入文件。
    print(
        "> INFO: [Python] Quality gate summary: "
        f"BLOCKER {int_blocker_count}, WARNING {int_warning_count}, "
        f"NOTE {int_note_count}"
    )

    # 用户指定 Markdown 路径时保存人工审阅版报告。
    if args.markdown:

        # 报告层负责创建父目录并写入 UTF-8 文本。
        gate_report_gate_report.write_markdown(args.markdown)

    # 用户指定 JSON 路径时保存自动化可读取的报告。
    if args.json:

        # JSON 报告保留规则编号、严重级别和工具结果。
        gate_report_gate_report.write_json(args.json)

    # warn-only 用于探索性扫描，终端仍展示完整发现项。
    if args.warn_only:

        # 返回 CLI 约定的进程退出码。
        return 0

    # 将失败判断拆出，避免退出码表达式过长影响阅读。
    bool_has_blocker = gate_report_gate_report.has_blocker(  # 当前运行是否需要失败退出
        strict_tools=args.strict_tools,  # 缺失工具是否按失败处理
        fail_on_warning=args.fail_on_warning,  # warning 是否升级为失败
    )

    # 把失败判定映射成 shell 与 CI 都能直接识别的退出码。
    return 1 if bool_has_blocker else 0

# 脚本直接执行时启动 CLI 入口。
if __name__ == "__main__":

    # 把 main 返回码交给调用进程，便于 shell 和 CI 判断成功失败。
    raise SystemExit(main())
