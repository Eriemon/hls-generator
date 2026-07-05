"""定义 HLS 可读性门禁的稳定报告对象和序列化格式。"""

# 启用延迟注解，避免运行期解析泛型类型。
from __future__ import annotations

# 导入 JSON、数据类、路径和通用载荷类型。
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# HLS 规则内部 severity 映射到通用质量门级别。
SEVERITY_TO_LEVEL = {  # severity 到展示级别映射
    "error": "BLOCKER",  # error 级别在外层统一映射为阻断
    "warning": "WARNING",  # warning 级别在外层统一映射为警告
    "note": "NOTE",  # note 级别在外层统一映射为提示
    "info": "NOTE",  # info 在外层报告里并入提示级别
}

# 反向映射用于兼容需要从 level 恢复 severity 的调用方。
LEVEL_TO_SEVERITY = {  # 展示级别到 severity 映射
    str_level: str_severity  # 把展示级别还原成内部 severity
    for str_severity, str_level in SEVERITY_TO_LEVEL.items()  # 遍历原始映射生成反向索引
}

# make_issue 只接受这些附加字段，避免拼写错误静默进入报告。
ISSUE_OPTIONAL_FIELDS = (  # 问题附加字段白名单
    "detail",  # 规则上下文的附加说明
    "node_kind",  # 命中的源码节点类别
    "code_excerpt",  # 用于报告展示的短源码摘录
)

# HlsGateIssue 是单条 HLS 可读性诊断的不可变载体。
@dataclass(frozen=True)
class HlsGateIssue:
    """保存一条确定性的 HLS 可读性诊断。

    Args:
        rule: HLS 可读性规则编号，例如 HG008。
        severity: 规则内部严重级别，取 error、warning、note 或 info。
        path: 问题所在文件的报告相对路径。
        line: 问题所在的一基源码行号。
        message: 面向用户的诊断说明。
        detail: 可选的上下文细节文本。
        node_kind: 可选的源码节点或检查类型。
        code_excerpt: 可选的源码摘录。

    Returns:
        数据类实例本身不返回业务值。
    """

    # rule 保留 HLS 可读性规则编号。
    rule: str  # 规则编号

    # severity 保留规则内部严重级别。
    severity: str  # 内部严重级别

    # path 用于报告中定位源文件。
    path: str  # 问题文件路径

    # line 使用一基行号，便于终端和编辑器定位。
    line: int  # 问题行号

    # message 是报告中最主要的人类可读诊断。
    message: str  # 诊断消息

    # detail 承载可选的规则上下文。
    detail: str | None = None  # 诊断细节

    # node_kind 标记命中的源码节点类别。
    node_kind: str | None = None  # 节点类别

    # code_excerpt 保留短源码摘录，避免用户必须立即打开文件。
    code_excerpt: str | None = None  # 源码摘录

    # level 属性背后使用私有 helper 统一映射 severity。
    def _get_level(self) -> str:
        """
        把内部 severity 转换为通用质量门级别。

        参数:
            无额外业务参数；当前结果仅依赖 self.severity。
        返回:
            BLOCKER、WARNING 或 NOTE；未知 severity 会转为大写展示。
        """

        # 统一 severity 大小写，兼容调用方传入的大写或混合写法。
        str_severity = self.severity.lower()  # 规范化严重级别

        # 未知级别直接大写返回，保留原始诊断可见性。
        return SEVERITY_TO_LEVEL.get(str_severity, self.severity.upper())

    # 对外暴露只读 level 属性，供 JSON 报告与终端摘要统一取值。
    level = property(_get_level)  # 对外展示级别属性

    # 字典化结果用于 JSON 报告和测试断言。
    def to_dict(self) -> dict[str, Any]:
        """
        转换为稳定的 JSON 字段字典。

        参数:
            无额外业务参数；当前结果仅依赖实例中的诊断字段。
        返回:
            包含规则编号、级别、位置和上下文的报告字典。
        """

        # 返回字段顺序保持稳定，便于报告 diff 和测试断言。
        return {
            "rule": self.rule,
            "code": self.rule,
            "severity": self.severity,
            "level": self.level,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "detail": self.detail,
            "node_kind": self.node_kind,
            "code_excerpt": self.code_excerpt,
        }

    # 格式化结果用于人类可读终端报告。
    def format(self) -> str:
        """生成单条诊断的紧凑文本表示。

        参数:
            无额外业务参数；当前结果仅依赖实例中的诊断字段。
        Returns:
            形如 `[LEVEL] path:line RULE message` 的单行文本。
        """

        # 有文件路径时使用 path:line，否则退化为 line N。
        str_location = (
            f"{self.path}:{self.line}"  # 有路径时输出 path:line 形式
            if self.path  # 优先使用文件路径和行号组合定位
            else f"line {self.line}"  # 缺少路径时退化为单独行号定位
        )  # 文本定位片段

        # 单行格式保持旧输出兼容。
        return f"[{self.level}] {str_location} {self.rule} {self.message}"

# HlsGateReport 汇总一次 HLS 可读性门禁运行结果。
@dataclass(frozen=True)
class HlsGateReport:
    """保存 HLS 可读性门禁的聚合输出。

    Args:
        target: 用户传入或解析后的检查目标。
        root: 门禁扫描根目录。
        profile: HLS 可读性 profile 名称。
        style: 注释和结构风格名称。
        issues: 已发现的 HLS 可读性问题元组。
        metrics: 解析器、AST 或比较流程产生的度量信息。
        fail_on_warning: 是否把 warning 视为失败。

    Returns:
        数据类实例本身不返回业务值。
    """

    # target 记录用户请求的原始检查目标。
    target: str  # 检查目标

    # root 记录实际参与扫描的根目录。
    root: str  # 扫描根目录

    # profile 记录 HLS 可读性检查配置来源。
    profile: str  # HLS 可读性配置档位名称

    # style 记录注释和结构风格配置。
    style: str  # 检查风格

    # issues 保留所有规则诊断，默认无问题。
    issues: tuple[HlsGateIssue, ...] = ()  # 诊断问题集合

    # metrics 保存 AST provider、parse failure 等辅助指标。
    metrics: dict[str, Any] = field(default_factory=dict)  # 门禁度量信息

    # fail_on_warning 控制 warning 是否影响 ok()。
    fail_on_warning: bool = False  # warning 失败开关

    # errors 属性背后使用私有 helper 统计阻断级问题数量。
    def _get_errors(self) -> int:
        """
        统计 error severity 的问题数量。

        参数:
            无额外业务参数；当前结果仅依赖 self.issues。
        返回:
            当前报告中的 error 计数。
        """

        # error 直接对应 HLS 门禁阻断问题。
        return sum(1 for issue in self.issues if issue.severity == "error")

    # 对外暴露只读 errors 属性，供调用方直接读取阻断数量。
    errors = property(_get_errors)  # 阻断级问题数量属性

    # warnings 属性背后使用私有 helper 统计警告数量。
    def _get_warnings(self) -> int:
        """
        统计 warning severity 的问题数量。

        参数:
            无额外业务参数；当前结果仅依赖 self.issues。
        返回:
            当前报告中的 warning 计数。
        """

        # warning 表示可运行但需要治理的可读性风险。
        return sum(1 for issue in self.issues if issue.severity == "warning")

    # 对外绑定 warnings 只读属性，便于摘要逻辑直接读取警告计数。
    warnings = property(_get_warnings)  # 警告级问题数量属性

    # notes 属性背后使用私有 helper 统计提示数量。
    def _get_notes(self) -> int:
        """
        统计 note 和 info severity 的提示数量。

        参数:
            无额外业务参数；当前结果仅依赖 self.issues。
        返回:
            当前报告中的 note/info 计数。
        """

        # info 在外层报告中归并为 note，避免多一类展示级别。
        return sum(1 for issue in self.issues if issue.severity in {"note", "info"})

    # 对外绑定 notes 只读属性，便于 JSON 摘要统一读取提示计数。
    notes = property(_get_notes)  # 提示级问题数量属性

    # ok 表达当前报告是否允许继续交付。
    def ok(self) -> bool:
        """
        判断本次 HLS 可读性门禁是否通过。

        参数:
            无额外业务参数；当前结果依赖 errors、warnings 和 fail_on_warning。
        返回:
            没有 error，且在 fail_on_warning 模式下没有 warning 时为 True。
        """

        # error 始终阻断 HLS 可读性门禁。
        if self.errors > 0:

            # 存在阻断问题时报告不可通过。
            return False

        # 严格模式下 warning 也会阻断交付。
        if self.fail_on_warning and self.warnings > 0:

            # fail_on_warning 用于发布前严格验收。
            return False

        # 没有触发阻断条件时视为通过。
        return True

    # count_by_rule 生成规则维度的问题汇总。
    def count_by_rule(self) -> dict[str, int]:
        """
        按规则编号统计问题数量。

        参数:
            无额外业务参数；当前结果仅依赖 self.issues。
        返回:
            以规则编号为键、命中次数为值的有序字典。
        """

        # dict_counts 按规则编号累计，最后再排序输出。
        dict_counts: dict[str, int] = {}  # 规则命中计数

        # 遍历所有诊断并累计对应规则编号。
        for issue in self.issues:

            # get 默认 0，保证首次出现的规则能直接累加。
            dict_counts[issue.rule] = dict_counts.get(issue.rule, 0) + 1  # 单规则累计值

        # 排序后的字典让 JSON 报告在不同平台上稳定。
        return dict(sorted(dict_counts.items()))

    # to_dict 输出完整机器可读报告。
    def to_dict(self) -> dict[str, Any]:
        """
        转换为稳定的机器可读报告字典。

        参数:
            无额外业务参数；当前结果依赖报告实例中的汇总字段。
        返回:
            包含摘要、问题列表和度量信息的 JSON 兼容字典。
        """

        # 规则统计会在 summary 和顶层各出现一次，保持旧报告结构。
        dict_issues_by_rule = self.count_by_rule()  # 规则问题汇总

        # 诊断问题逐条转成普通字典，供 JSON 序列化。
        list_issue_payloads = [
            issue.to_dict()  # 单条诊断对应的普通字典载荷
            for issue in self.issues  # 按报告中的原始顺序遍历全部诊断对象
        ]  # 问题载荷列表

        # 返回字段顺序保持历史兼容。
        return {
            "version": 1,
            "target": self.target,
            "root": self.root,
            "profile": self.profile,
            "style": self.style,
            "ok": self.ok(),
            "fail_on_warning": self.fail_on_warning,
            "errors": self.errors,
            "warnings": self.warnings,
            "notes": self.notes,
            "summary": {
                "errors": self.errors,
                "warnings": self.warnings,
                "notes": self.notes,
                "issues_by_rule": dict_issues_by_rule,
            },
            "issues_by_rule": dict_issues_by_rule,
            "issues": list_issue_payloads,
            "metrics": self.metrics,
        }

    # to_json 输出带结尾换行的格式化 JSON。
    def to_json(self) -> str:
        """
        序列化为缩进 JSON 文本。

        参数:
            无额外业务参数；当前结果依赖 to_dict 生成的机器可读载荷。
        返回:
            UTF-8 友好的 JSON 字符串，末尾带一个换行。
        """

        # ensure_ascii=False 保留中文诊断文本的可读性。
        return json.dumps(
            self.to_dict(),
            indent=2,
            ensure_ascii=False,
        ) + "\n"

    # write_json 将报告写入指定路径。
    def write_json(self, path: str | Path) -> None:
        """把报告 JSON 写入文件。

        Args:
            path: 目标 JSON 文件路径，父目录会自动创建。

        Returns:
            该方法只产生文件写入副作用，不返回业务值。
        """

        # 将字符串路径统一转换为 Path，便于创建父目录。
        path_output = Path(path)  # JSON 输出路径

        # 确保报告目录存在，避免调用方提前创建目录。
        path_output.parent.mkdir(parents=True, exist_ok=True)

        # 写入完整 JSON 报告，保留中文诊断文本。
        path_output.write_text(self.to_json(), encoding="utf-8")

    # format 输出人类可读的多行报告。
    def format(self) -> str:
        """生成面向终端阅读的 HLS 门禁报告文本。

        参数:
            无额外业务参数；当前结果依赖 root、profile、style 与 issues。
        Returns:
            包含标题、问题列表和摘要的多行字符串。
        """

        # 报告头记录根目录、profile 和 style。
        list_lines = [
            f"HLS readability gate for {self.root}",  # 人类可读标题行
            f"Profile: {self.profile}; style: {self.style}",  # profile 与 style 摘要行
        ]  # 人类可读报告行

        # 无问题时保留历史通过提示。
        if not self.issues:

            # 通过提示只写一行，便于终端扫描。
            list_lines.append("INFO: HLS readability checks passed.")

        # 有问题时逐条追加格式化诊断。
        else:

            # 每条 issue 自己负责单行格式。
            list_lines.extend(issue.format() for issue in self.issues)

        # 摘要行始终位于报告末尾。
        list_lines.append(
            f"Summary: {self.errors} error(s), "
            f"{self.warnings} warning(s), {self.notes} note(s)",
        )

        # 返回换行拼接后的报告文本。
        return "\n".join(list_lines)

# make_issue 为各条 HLS 规则提供短构造入口。
def make_issue(
    rule: str,
    severity: str,
    path: str,
    line: int,
    message: str,
    **dict_optional_fields: str | None,
) -> HlsGateIssue:
    """构造带行号保护的 HLS 可读性问题。

    Args:
        rule: HLS 可读性规则编号。
        severity: 规则内部严重级别。
        path: 问题所在文件路径。
        line: 一基行号；空值或非正值会修正为 1。
        message: 面向用户的诊断说明。
        **dict_optional_fields: detail、node_kind、code_excerpt 三个可选字段。

    Returns:
        HlsGateIssue 诊断对象。

    Raises:
        TypeError: 传入未知可选字段时抛出，防止报告字段拼写错误。
    """

    # 检查调用方是否传入了未知报告字段。
    set_unknown_fields = set(dict_optional_fields) - set(ISSUE_OPTIONAL_FIELDS)  # 未知字段集合

    # 未知字段意味着规则调用处存在拼写或接口误用。
    if set_unknown_fields:

        # 排序后输出，保证错误消息稳定。
        str_unknown_fields = ", ".join(sorted(set_unknown_fields))  # 未知字段文本

        # 使用 current-project 错误前缀暴露调用方问题。
        raise TypeError(f"> ERR: [Python] unknown HLS issue field(s): {str_unknown_fields}")

    # 行号统一为正整数，避免报告出现 0 或空行号。
    int_line = max(1, int(line or 1))  # 规范化一基行号

    # 可选字段按旧 dataclass 构造顺序取出。
    str_detail = dict_optional_fields.get("detail")  # 补充描述当前命中规则的上下文细节

    # node_kind 描述命中的规则节点类型。
    str_node_kind = dict_optional_fields.get("node_kind")  # 标记当前问题对应的源码节点类别

    # code_excerpt 保留短源码上下文。
    str_code_excerpt = dict_optional_fields.get("code_excerpt")  # 保留便于终端阅读的短源码摘录

    # 返回旧调用方期望的 HlsGateIssue 实例。
    return HlsGateIssue(
        rule,
        severity,
        path,
        int_line,
        message,
        # 可选上下文按 dataclass 构造顺序传入。
        str_detail,
        str_node_kind,
        str_code_excerpt,
    )
