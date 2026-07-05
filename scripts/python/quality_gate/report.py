"""负责把质量门问题整理为 Markdown 和 JSON 报告。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 单条发现项同时服务 Markdown 行和 JSON 结构。
@dataclass(frozen=True)
class Issue:
    """保存质量问题发现项字段。"""

    # PG 编号用于统计规则命中次数和定位具体规则。
    code: str  # 质量门规则编号

    # BLOCKER/WARNING/NOTE 决定质量门退出状态和报告分组。
    level: str  # 发现项严重级别

    # 文件路径保持字符串，方便 JSON 序列化和 Markdown 展示。
    filepath: str  # 发现项所在文件

    # 行号用于编辑器和终端报告定位源码位置。
    line: int  # 发现项源码行号

    # 消息必须给出触发原因或可操作修复方向。
    message: str  # 面向用户的诊断消息

    # `to_dict` 处理映射。
    def to_dict(self) -> dict[str, object]:
        """转换为报告 JSON 使用的字典结构。

        参数:
            无显式参数，直接读取当前发现项字段。

        返回:
            可写入 JSON 报告的字段映射。
        """

        # JSON 输出字段保持扁平，便于测试和外部脚本读取。
        return {
            "code": self.code,
            "level": self.level,
            "filepath": self.filepath,
            "line": self.line,
            "message": self.message,
        }

    # Markdown 报告逐条展示发现项，保留文件和行号。
    def to_markdown_line(self) -> str:
        """把发现项格式化成 Markdown 列表行。

        参数:
            无显式参数，直接读取当前发现项字段。

        返回:
            包含级别、文件、行号、规则编号和消息的 Markdown 文本行。
        """

        # 保留单行列表格式，方便在 Markdown 明细区直接拼接。
        return f"- [{self.level}] {self.filepath}:{self.line} {self.code} {self.message}"

# 外部工具结果用于补充质量门自身规则之外的验证信息。
@dataclass(frozen=True)
class ToolResult:
    """保存tool结果报告字段。"""

    # 工具名称用于报告分节标题和失败摘要。
    name: str  # 外部工具名称

    # 原始命令帮助调用方复现工具输出。
    command: str  # 外部工具执行命令

    # 非零退出码会在严格模式下影响质量门结果。
    exit_code: int  # 外部工具退出码

    # stdout/stderr 合并文本写入报告，便于离线诊断。
    output: str  # 外部工具输出文本

    # 缺失工具单独计数，调用方可选择是否按失败处理。
    missing: bool = False  # 工具是否缺失

    # `to_dict` 会把工具执行结果转成稳定的机器可读结构。
    def to_dict(self) -> dict[str, object]:
        """转换为报告 JSON 使用的字典结构。

        参数:
            无显式参数，直接读取当前工具结果字段。

        返回:
            可写入 JSON 报告的字段映射。
        """

        # 工具结果字段与 Markdown 报告使用同一份数据源。
        return {
            "name": self.name,
            "command": self.command,
            "exit_code": self.exit_code,
            "output": self.output,
            "missing": self.missing,
        }

# 完整报告对象封装质量门输入、发现项和外部工具结果。
@dataclass(frozen=True)
class GateReport:
    """保存一次质量门运行的报告数据。"""

    # target 保留用户传入的检查范围，用于报告标题。
    target: str  # 质量门检查目标

    # profile 决定启用的规则集合和严重级别。
    profile: str  # 质量门配置档案

    # style 标识当前项目风格约束来源。
    style: str  # 代码风格配置名称

    # issues 是报告的主体，同时用于计算退出状态。
    issues: list[Issue]  # 质量门发现项列表

    # 外部工具结果补充编译、lint 或格式检查状态。
    tool_results: list[ToolResult]  # 外部工具执行结果集合

    # 按严重级别统计发现项，用于摘要和退出判断。
    def count_level(self, level: str) -> int:
        """统计指定严重级别的发现项数量。

        参数:
            level: 验证发现项的严重级别。

        返回:
            与指定级别完全匹配的发现项数量。
        """

        # 级别字符串由规则生成端控制，这里只做精确匹配。
        return sum(1 for issue in self.issues if issue.level == level)

    # 工具失败计数不包含缺失工具，缺失项由单独计数控制。
    def failed_tool_count(self) -> int:
        """统计已运行但退出码非零的外部工具数量。

        参数:
            无显式参数，直接遍历当前工具结果列表。

        返回:
            非缺失且退出码不为 0 的工具结果数量。
        """

        # 缺失工具可能是环境问题，是否失败交给 strict_tools。
        return sum(
            1
            for result in self.tool_results
            if result.exit_code != 0 and not result.missing
        )

    # 缺失工具计数用于 strict_tools 模式下提升失败。
    def missing_tool_count(self) -> int:
        """统计当前环境缺失的外部工具数量。

        参数:
            无显式参数，直接遍历当前工具结果列表。

        返回:
            标记为 missing 的工具结果数量。
        """

        # missing 标记由工具执行层设置。
        return sum(1 for result in self.tool_results if result.missing)

    # 规则编号聚合用于报告中快速定位高频问题。
    def count_by_rule(self) -> dict[str, int]:
        """按 PG 规则编号统计发现项数量。

        参数:
            无显式参数，直接遍历当前发现项列表。

        返回:
            按规则编号排序后的发现项数量映射。
        """

        # 初始化counts收集容器，汇总本轮扫描发现。
        dict_counts: dict[str, int] = {}  # 统计当前候选规模，判断是否超过治理阈值

        # 汇总规则发现项，生成报告统计。
        for issue in self.issues:

            # 同一规则可能在多个文件多次命中，摘要按编号累加。
            dict_counts[issue.code] = dict_counts.get(issue.code, 0) + 1  # 当前规则判定所需状态

        # 排序后输出，保证报告在不同 Python 运行中保持稳定。
        return dict(sorted(dict_counts.items()))

    # 退出判断同时考虑 blocker、可选 warning 和工具状态。
    def has_blocker(self, strict_tools: bool = False, fail_on_warning: bool = False) -> bool:
        """判断当前对象是否具备 blocker。

        参数:
            strict_tools: 外部工具缺失是否按失败处理
            fail_on_warning: WARNING 是否提升为失败退出码

        返回:
            返回当前对象是否具备目标特征。
        """

        # 内置规则发现 BLOCKER 时质量门必须失败。
        if any(issue.level == "BLOCKER" for issue in self.issues):

            # 失败原因已经保存在 issues 明细中。
            return True

        # 严格模式下 WARNING 也用于阻断最终交付。
        if fail_on_warning and any(issue.level == "WARNING" for issue in self.issues):

            # 调用者通过 fail-on-warning 选择这一路径。
            return True

        # 已安装工具返回非零退出码时视为外部门禁失败。
        if self.failed_tool_count() > 0:

            # 工具输出会出现在报告的外部工具分节中。
            return True

        # strict-tools 要求环境完整，缺失工具也阻断质量门。
        if strict_tools and self.missing_tool_count() > 0:

            # 缺失工具名称会保存在 ToolResult 明细中。
            return True

        # 没有任何阻断条件时允许 CLI 返回成功。
        return False

    # `to_dict` 汇总摘要、发现项与工具结果，供 JSON 持久化复用。
    def to_dict(self) -> dict[str, object]:
        """转换为报告 JSON 使用的字典结构。

        参数:
            无显式参数，直接读取当前报告对象状态。

        返回:
            可写入 JSON 报告的字段映射。
        """

        # 机器可读报告保留摘要、明细和外部工具三类数据。
        return {
            "target": self.target,
            "profile": self.profile,
            "style": self.style,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": {
                "blocker": self.count_level("BLOCKER"),
                "warning": self.count_level("WARNING"),
                "note": self.count_level("NOTE"),
                "tool_failures": self.failed_tool_count(),
                "missing_tools": self.missing_tool_count(),
            },
            "issues_by_rule": self.count_by_rule(),
            "issues": [issue.to_dict() for issue in self.issues],
            "tools": [result.to_dict() for result in self.tool_results],
        }

    # Markdown 输出面向终端和人工审阅，保留摘要和详细列表。
    def to_markdown(self) -> str:
        """生成完整的 Markdown 质量门报告。

        参数:
            无显式参数，直接读取当前报告对象状态。

        返回:
            以换行结尾的 Markdown 报告文本。
        """

        # 报告头部固定展示运行配置和摘要计数。
        list_lines: list[str] = []  # 按输出顺序缓存整份 Markdown 报告的每一行

        # 先写入头部与摘要基础结构，后续再按条件追加分节内容。
        list_lines.extend([
            "# Readable Python Quality Gate Report",
            "",
            f"- target: `{self.target}`",
            f"- profile: `{self.profile}`",
            f"- style: `{self.style}`",
            f"- generated_at: `{datetime.now().isoformat(timespec='seconds')}`",
            "",
            "## Summary",
            "",
            f"- BLOCKER: {self.count_level('BLOCKER')}",
            f"- WARNING: {self.count_level('WARNING')}",
            f"- NOTE: {self.count_level('NOTE')}",
            f"- tool_failures: {self.failed_tool_count()}",
            f"- missing_tools: {self.missing_tool_count()}",
            "",
        ])

        # 有规则发现时先展示编号统计，再列出逐条明细。
        if self.issues:

            # 先给出按规则编号聚合的摘要，方便用户决定治理优先级。
            list_lines.extend(["## Issue counts by rule", ""])

            # 规则编号统计帮助用户优先处理高频问题。
            for str_rule_code, int_issue_count in self.count_by_rule().items():

                # 每个编号展示一次，数量来自 count_by_rule。
                list_lines.append(f"- {str_rule_code}: {int_issue_count}")

            # 空行分隔统计区和明细区，提升终端可读性。
            list_lines.append("")

            # 明细区保留文件、行号、级别和诊断消息。
            list_lines.extend(["## AST / readability issues", ""])

            # 逐条展开规则发现，保留 issue 原始顺序便于回到源码定位。
            list_lines.extend(issue.to_markdown_line() for issue in self.issues)

            # 空行分隔问题明细和后续工具结果。
            list_lines.append("")

        # 没有规则发现时明确写出清零结论，减少人工判读成本。
        else:

            # 没有规则发现时仍写出明确结论，方便日志检索。
            list_lines.extend(["AST / readability gate found no issues.", ""])

        # 启用外部工具时追加命令、退出码和输出摘要。
        if self.tool_results:

            # 外部工具结果单独分节，避免与内置规则混在一起。
            list_lines.extend(["## External tool results", ""])

            # 外部工具逐项展示，便于定位具体失败命令。
            for result in self.tool_results:

                # 每个工具单独起节，命令和退出信息保持紧邻显示。
                list_lines.extend([
                    f"### {result.name}",
                    "",
                    f"- command: `{result.command}`",
                    f"- exit_code: `{result.exit_code}`",
                    f"- missing: `{result.missing}`",
                ])

                # 工具有输出时用代码块保留换行和缩进。
                if result.output.strip():

                    # 仅在有输出时附加代码块，避免生成空白围栏。
                    list_lines.extend(["", "```text", result.output.strip(), "```"])

                # 为下一个工具结果或文件尾保留可读分隔。
                list_lines.append("")

        # 报告末尾保留单个换行，便于命令行和 diff 阅读。
        return "\n".join(list_lines).rstrip() + "\n"

    # Markdown 写入会自动创建父目录，便于脚本指定新报告路径。
    def write_markdown(self, filepath: str | Path) -> None:
        """把当前报告写入 Markdown 文件。

        参数:
            filepath: Markdown 报告输出路径。

        返回:
            None。结果通过写入文件产生副作用。
        """

        # 统一输出路径对象，后续创建父目录并写入文本。
        path_output_path = Path(filepath)  # Markdown 报告输出路径

        # 先补齐父目录，避免首次输出到新目录时报错。
        path_output_path.parent.mkdir(parents=True, exist_ok=True)

        # 直接落盘当前 Markdown 文本，供人工审阅或归档复用。
        path_output_path.write_text(self.to_markdown(), encoding="utf-8")

    # JSON 写入用于 CI、测试和调用方读取机器可读结果。
    def write_json(self, filepath: str | Path) -> None:
        """把当前报告写入 JSON 文件。

        参数:
            filepath: JSON 报告输出路径。

        返回:
            None。结果通过写入文件产生副作用。
        """

        # 将 JSON 目标统一转成 Path，便于后续目录和写盘操作复用。
        path_output_path = Path(filepath)  # JSON 报告目标路径对象

        # JSON 报告常用于 CI 产物目录，先确保父目录链已经存在。
        path_output_path.parent.mkdir(parents=True, exist_ok=True)

        # JSON 使用缩进格式，方便 CI 产物和人工排查同时阅读。
        path_output_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
