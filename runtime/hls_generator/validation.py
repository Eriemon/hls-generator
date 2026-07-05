"""负责生成 HLS 产物的静态、Vitis 和远端验收治理结果。"""

# 启用延迟求值注解，避免类型提示在模块导入阶段触发不必要的求值。
from __future__ import annotations

# 导入文本与结构化数据处理组件，供规则匹配和 JSON 报告复用。
import json
import re

# 导入进程与环境相关组件，供外部工具调用和临时脚本执行。
import shutil
import subprocess
import tempfile

# 导入数据结构与路径工具，供验证对象和文件树遍历复用。
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 导入 HLS 注释治理规则，用于校验生成代码的注释边界是否满足仓库契约。
from .comment_policy import validate_hls_comment_policy

# 导入 Vitis 工具链配置，供本模块拼装本地与远端验证命令。
from .config import (
    missing_vitis_tool_id,
    vitis_command,
    vitis_tcl_config,
    vitis_tool_names,
    vitis_tool_timeout,
    vitis_tools,
)

# 导入 HLS AST 注释护栏，避免生成代码绕过关键语义注释约束。
from .hls_ast_guard import validate_hls_ast_guard

# 导入 hls_config.cfg 解析工具，供静态验证阶段读取时钟和配置项。
from .hls_cfg import cfg_relative_path_issue, clock_period_ns, parse_hls_cfg_entries

# 导入 profile 校验器，确保生成 spec 与约定的 HLS profile 一致。
from .hls_profile import validate_hls_profile

# 导入报告指标采集器，把综合与实现结果收敛到统一 metrics 中。
from .hls_reports import collect_hls_report_metrics

# 导入 Vitis HLS Tcl 渲染器，供本地编译和 readiness 阶段生成执行脚本。
from .hls_tcl import render_vitis_hls_tcl

# 导入接口审计器，检查 top function 接口与契约是否匹配。
from .interface_contract import audit_interface

# 导入模式库匹配工具，用于校验高级库头文件和模式命名约束。
from .patterns import ADVANCED_LIBRARY_HEADERS, canonical_pattern_name, required_pattern_headers

# 导入注释语言约束解析器，统一处理 auto 与显式语言设置。
from .prompt import require_comment_language

# 导入 HLS 可读性 gate，复用仓库内 HLS 读写规范检查逻辑。
from .readability_gate.runner import run_hls_readability_gate

# 导入 spec 归一化器，把用户输入收敛成统一的 HLS 规范结构。
from .spec import normalize_spec

# 导入接口契约规划器，生成接口契约与 spec 不一致时的结构化问题列表。
from .verifier import plan_contract_interface_issues

# 导入 Vitis 规则扫描器，用于补充特定工具链规则违规诊断。
from .vitis_rules import scan_vitis_rule_violations

# 导入向量契约工具，校验测试向量 hash 与合同标签是否匹配。
from .vectors import VECTOR_HASH_TAG, extract_vector_hashes, find_vector_contracts

# 固定定义支持的 readiness 阶段顺序，供 CLI 与验证流程共享。
READINESS_LEVELS = ("static", "compile", "execute", "implement", "cosim")  # HLS 验证允许声明的 readiness 阶段序列

# 把 readiness 阶段映射到整数顺序，便于后续比较“至少执行到哪一步”。
dict_readiness_order = {name: index for index, name in enumerate(READINESS_LEVELS)}  # readiness 阶段到顺序编号的映射表

# 报告输出沿用 readiness 阶段顺序，保证静态、编译和执行结果稳定排序。
tuple_report_stages = READINESS_LEVELS  # 汇总报告时要按此顺序展开的阶段序列

# 汇总单条验证问题的基础字段，供报告、JSON 和 CLI 摘要统一复用。
@dataclass(frozen=True)
class ValidationIssue:
    """描述一条 HLS 验证问题及其来源上下文。

    参数:
        severity: 问题严重级别，如 error、warning 或 skip。
        message: 面向用户的主诊断信息。
        path: 问题关联的文件或目录路径；缺失时允许为 None。
        stage: 该问题归属的验证阶段名称。
        source: 产生该问题的规则或子系统来源标识。
        case_id: 可选的 reference case 标识。
        tool: 可选的工具链名称。
        detail: 可选的附加诊断正文。

    返回:
        无业务返回值；dataclass 仅负责承载结构化问题字段。
    """

    # 记录这条问题的严重级别，决定最终报告是否失败。
    severity: str  # 当前问题对最终验证结果的严重级别

    # 保存面向用户展示的主诊断文本。
    message: str  # 最终报告里展示的核心问题描述

    # 记录问题关联的源路径，缺失时保持 None。
    path: str | None = None  # 触发该问题的文件或目录路径

    # 标记问题属于哪个 readiness 阶段，方便分阶段汇总。
    stage: str = "static"  # 当前问题归属的验证阶段名称

    # 记录该问题来自哪个校验子模块或规则来源。
    source: str = "current_module_issue"  # 生成这条问题的规则来源标识

    # 记录 reference contract 中的具体 case 标识。
    case_id: str | None = None  # 命中的参考 case 标识

    # 记录产生该问题的具体工具名称。
    tool: str | None = None  # 问题关联的外部工具或内部子工具名称

    # 保存较长的附加诊断正文，供 JSON 报告进一步展开。
    detail: str | None = None  # 附加的长文本诊断信息

    # 把结构化问题格式化成单行文本，供 CLI 报告直接打印。
    def format(self) -> str:
        """把单条验证问题格式化成 CLI 摘要文本。

        参数:
            无额外业务参数。

        返回:
            str: 包含 source、tool、case 与 message 的单行摘要文本。
        """

        # 路径存在时，把它拼成带方括号的可读定位片段。
        str_location = f" [{self.path}]" if self.path else ""  # 当前问题对应的可选路径定位片段

        # case_id 存在时，把 reference case 标识拼进摘要文本。
        str_case = f" case={self.case_id}" if self.case_id else ""  # 当前问题对应的可选 case 标识片段

        # tool 存在时，把工具名拼进摘要文本，方便定位具体子工具。
        str_tool = f" tool={self.tool}" if self.tool else ""  # 终端摘要里用于标记触发该问题的具体工具

        # 把 severity、source、tool、case 和 message 收敛成单行摘要文本。
        return f"{self.severity.upper()}[{self.source}]{str_tool}{str_case}: {self.message}{str_location}"

    # 把 dataclass 转成 JSON 友好的字典结构，供报告文件直接写出。
    def to_dict(self) -> dict[str, Any]:
        """把验证问题对象转成可序列化字典。

        参数:
            无额外业务参数。

        返回:
            dict[str, Any]: 适合 JSON 序列化的验证问题字段字典。
        """

        # 返回完整问题字段字典，供 report JSON 原样写出。
        return {
            "severity": self.severity,  # 当前问题的严重级别
            "message": self.message,  # 当前问题的主诊断文本
            "path": self.path,  # 当前问题关联的路径
            "stage": self.stage,  # 当前问题归属的验证阶段
            "source": self.source,  # 当前问题的规则来源标识
            "case_id": self.case_id,  # 当前问题关联的参考 case 标识
            "tool": self.tool,  # 当前问题关联的工具名称
            "detail": self.detail,  # 当前问题附带的长文本诊断
        }

# 汇总整轮 HLS 验证结果，统一承载问题列表、阶段统计与附加 metrics。
@dataclass(frozen=True)
class ValidationReport:
    """描述一次完整 HLS 验证执行后的聚合结果。

    参数:
        target: 当前验证对象的逻辑目标名称。
        root: 当前验证树的根目录路径。
        issues: 本轮验证收集到的问题列表。
        metrics: 可选的附加结构化指标字典。

    返回:
        无业务返回值；dataclass 仅负责承载整轮验证结果。
    """

    # 保存当前验证对象的逻辑名称，供 CLI 摘要和 JSON 报告复用。
    target: str  # 这轮验证面向的目标名称

    # 保存当前验证树的根目录，供路径汇总与下游工具定位。
    root: Path  # 本轮验证对象对应的根目录

    # 保存本轮验证收集到的全部问题对象。
    issues: tuple[ValidationIssue, ...]  # 整轮验证收集到的结构化问题列表

    # 保存额外 metrics，供工具链执行细节和报告统计扩展。
    metrics: dict[str, Any] | None = None  # 可选的结构化附加指标字典

    # 暴露 error 数量属性，供上游摘要和最终通过判定直接读取。
    @property

    # 统计会阻断最终通过判定的 error 条目数量。
    def errors(self) -> int:
        """统计本轮验证中的 error 数量。

        参数:
            无额外业务参数。

        返回:
            int: 严重级别为 error 的问题总数。
        """

        # 只统计会阻断通过判定的 error 条目数量。
        return sum(1 for issue in self.issues if issue.severity == "error")

    # 暴露 warning 数量属性，供 CLI 摘要与 JSON 报告复用。
    @property

    # 统计需要人工关注的 warning 条目数量。
    def warnings(self) -> int:
        """统计本轮验证中的 warning 数量。

        参数:
            无额外业务参数。

        返回:
            int: 严重级别为 warning 的问题总数。
        """

        # 只统计需要人工关注但不阻断流程的 warning 条目数量。
        return sum(1 for issue in self.issues if issue.severity == "warning")

    # 暴露 skip 数量属性，供摘要区分主动跳过与真实异常。
    @property

    # 统计因条件不满足而被主动跳过的条目数量。
    def skips(self) -> int:
        """统计本轮验证中的 skip 数量。

        参数:
            无额外业务参数。

        返回:
            int: 严重级别为 skip 的问题总数。
        """

        # 只统计因前置条件不足而被跳过的 skip 条目数量。
        return sum(1 for issue in self.issues if issue.severity == "skip")

    # 只有 error 计数为零时，这轮验证结果才算通过。
    def ok(self) -> bool:
        """判断这轮验证是否没有 error 级问题。

        参数:
            无额外业务参数。

        返回:
            bool: 没有 error 问题时返回 True，否则返回 False。
        """

        # error 计数为零时，这轮验证才允许进入通过状态。
        return self.errors == 0

    # 生成面向 CLI 的阶段化文本摘要，方便终端直接浏览结果。
    def format(self) -> str:
        """把整轮验证结果格式化成多行文本摘要。

        参数:
            无额外业务参数。

        返回:
            str: 按阶段展开的问题摘要文本。
        """

        # 先写入总标题，标明目标名称与验证根目录。
        list_lines = [f"Validation report for {self.target} at {self.root}"]  # CLI 摘要的逐行文本列表

        # 按固定阶段顺序展开问题，保证 static/compile/execute 输出稳定。
        for stage in tuple_report_stages:

            # 只抽取当前阶段的问题，避免不同阶段混在一起。
            tuple_stage_issues = tuple(issue for issue in self.issues if issue.stage == stage)  # 当前阶段对应的问题子集

            # 当前阶段存在问题时，先打印阶段头，再追加每条问题摘要。
            if tuple_stage_issues:

                # 写入当前阶段的标题行，提示后续问题归属。
                list_lines.append(f"[{stage}]")

                # 逐条追加当前阶段问题的单行摘要。
                list_lines.extend(issue.format() for issue in tuple_stage_issues)

            # static 阶段完全通过时，也要显式写出通过提示。
            elif stage == "static":

                # 写入 static 阶段标题，避免摘要里看不出静态阶段已被检查。
                list_lines.append("[static]")

                # 明确说明静态检查通过，方便和“未执行”区分。
                list_lines.append("INFO: Static checks passed.")

        # 把 error、warning 与 skip 统计写到摘要尾部。
        list_lines.append(f"Summary: {self.errors} error(s), {self.warnings} warning(s), {self.skips} skip(s)")

        # metrics 存在时，把结构化指标的简写摘要附到最后。
        if self.metrics:

            # 直接附加 metrics 文本，方便 CLI 上快速查看额外指标。
            list_lines.append(f"Metrics: {self.metrics}")

        # 返回终端直接可打印的多行验证摘要文本。
        return "\n".join(list_lines)

    # 生成适合 JSON 落盘的完整验证结果字典。
    def to_dict(self) -> dict[str, Any]:
        """把整轮验证结果转成可序列化字典。

        参数:
            无额外业务参数。

        返回:
            dict[str, Any]: 适合写入 report JSON 的完整结果字典。
        """

        # 缺少 metrics 时回落到空字典，避免后续字段读取分支过多。
        dict_metrics = self.metrics or {}  # 当前报告最终要写出的 metrics 字典

        # 只有 toolchain 字段本身是字典时，才允许继续读取 executed 标志。
        dict_toolchain = dict_metrics.get("toolchain", {}) if isinstance(dict_metrics.get("toolchain"), dict) else {}  # 当前 metrics 中的 toolchain 子字典

        # 记录本轮是否真的执行过 Vitis 工具链阶段。
        bool_vitis_executed = bool(dict_toolchain.get("executed"))  # 这轮验证是否实际跑过 Vitis 工具链

        # 返回完整的结构化报告字典，供 JSON 报告和上游调用方直接复用。
        return {
            "target": self.target,  # 当前验证目标名称
            "root": str(self.root),  # 当前验证根目录文本
            "ok": self.ok(),  # 当前报告是否没有 error 级问题
            "errors": self.errors,  # 当前报告里的 error 数量
            "warnings": self.warnings,  # 需要人工关注但未阻断流程的 warning 计数
            "skips": self.skips,  # 因前置条件不足而跳过的检查计数
            "static_only": not bool_vitis_executed,  # 是否只执行了静态检查
            "vitis_executed": bool_vitis_executed,  # 是否真正执行过 Vitis 工具链阶段
            "dependency_warnings": dict_metrics.get("dependency_warnings", []),  # 依赖层面的补充 warning 列表
            "acceptance_required": not bool_vitis_executed,  # 没跑 Vitis 时是否仍需后续验收
            "issues": [issue.to_dict() for issue in self.issues],  # 当前报告的全部问题字典
            "metrics": dict_metrics,  # 当前报告附带的结构化指标
        }

# 收拢 validate_generated 的可选执行策略，避免入口函数持续增长散参。
@dataclass(frozen=True)
class ValidationRunOptions:
    """描述 validate_generated 的可选执行参数集合。"""

    # 是否允许运行外部工具链步骤。
    run_external: bool = True  # 当前验证是否允许执行外部工具链

    # 本轮允许推进到的 readiness 阶段。
    readiness: str = "static"  # 当前验证阶段上限

    # 注释语言配置，支持 auto 与显式语言值。
    comment_language: str = "zh"  # 当前注释语言策略

    # 可选的 HLS profile 覆盖字典。
    hls_profile: dict[str, Any] | None = None  # 当前验证显式注入的 HLS profile

    # 可选的基线目录，用于 AST 注释对比。
    baseline_path: Path | None = None  # 注释 AST 护栏使用的基线路径

# 旧式 validate_generated 关键字兼容层只允许这几个公开参数名。
LEGACY_VALIDATION_OPTION_NAMES = {  # 旧式兼容关键字白名单
    "run_external",  # 控制是否触发外部工具链阶段
    "readiness",  # 约束验证流程推进到哪个阶段
    "comment_language",  # 覆盖自动选择出的注释语言
    "hls_profile",  # 注入额外的 HLS profile 配置
    "baseline_path",  # 指向 AST 注释对比的基线目录
}

# 旧式关键字兼容层统一通过这个 helper 回读显式值或默认值。
def _compat_option_value(
    dict_compat_options: dict[str, Any],
    str_option_name: str,
    obj_default: Any,
) -> Any:
    """返回旧式兼容关键字中的显式值，缺失时回退到默认值。

    参数:
        dict_compat_options: 旧式 validate_generated 关键字字典。
        str_option_name: 当前要读取的兼容关键字名称。
        obj_default: 调用方未显式传入该关键字时的回退值。

    返回:
        Any: 显式兼容关键字值或回退默认值。
    """

    # 只有调用方真的显式传了这个旧式关键字，才允许覆写默认值。
    if str_option_name in dict_compat_options:

        # 返回调用方显式传入的旧式关键字值。
        return dict_compat_options[str_option_name]

    # 缺少显式旧式关键字时，继续沿用调用方已有默认值。
    return obj_default

# 旧式关键字要先合并到 ValidationRunOptions，避免主入口继续堆散参兼容分支。
def _resolve_validation_run_options(
    *,
    options: ValidationRunOptions | None,
    dict_compat_options: dict[str, Any],
) -> ValidationRunOptions:
    """把显式 options 与旧式关键字兼容层合并成统一的验证参数对象。

    参数:
        options: 调用方显式传入的 ValidationRunOptions；缺省时使用默认值。
        dict_compat_options: 旧式 validate_generated 关键字字典。

    返回:
        ValidationRunOptions: 合并兼容关键字后的统一验证参数对象。

    异常:
        TypeError: 旧式关键字里包含未声明名称时抛出。
    """

    # reference_contract 旧协议已经移除，命中时必须直接要求调用方从 fresh HLS-only run 重跑。
    if "reference_contract" in dict_compat_options:

        # 直接抛出显式重跑错误，避免回退到模糊的 unexpected keyword 报错。
        raise ValueError(
            "> ERR: [Python] reference_contract is no longer supported. "
            "This skill is HLS-only now; rerun from a fresh HLS-only workflow run."
        )

    # 先识别旧式兼容关键字里不被允许的名称，避免静默吞掉拼写错误。
    set_unknown_names = set(dict_compat_options) - LEGACY_VALIDATION_OPTION_NAMES  # 旧式兼容关键字里的未知名称集合

    # 只要出现未知旧式关键字，就立刻按 Python 关键字参数错误返回。
    if set_unknown_names:

        # 保持错误信息稳定，便于测试与调用方快速定位拼写错误。
        str_unknown_names = ", ".join(sorted(set_unknown_names))  # 未知旧式关键字的稳定展示文本

        # 当前入口不接受白名单以外的旧式关键字名称。
        raise TypeError(
            f"> ERR: [Python] validate_generated got unexpected keyword argument(s): {str_unknown_names}"
        )

    # 缺少显式 options 时，先从 dataclass 默认值开始叠加旧式关键字。
    validation_run_options_base: ValidationRunOptions = options or ValidationRunOptions()  # 合并旧式关键字前的基准验证参数对象

    # 返回统一参数对象，供 validate_generated 主流程后续各阶段复用。
    return ValidationRunOptions(
        run_external=_compat_option_value(
            dict_compat_options,
            "run_external",
            validation_run_options_base.run_external,
        ),
        readiness=_compat_option_value(
            dict_compat_options,
            "readiness",
            validation_run_options_base.readiness,
        ),
        comment_language=_compat_option_value(
            dict_compat_options,
            "comment_language",
            validation_run_options_base.comment_language,
        ),
        hls_profile=_compat_option_value(
            dict_compat_options,
            "hls_profile",
            validation_run_options_base.hls_profile,
        ),
        baseline_path=_compat_option_value(
            dict_compat_options,
            "baseline_path",
            validation_run_options_base.baseline_path,
        ),
    )

# 校验用户声明的 readiness 是否落在受支持阶段集合内。
def require_readiness(readiness: str) -> str:
    """规范化并校验 readiness 字符串。

    参数:
        readiness: 用户输入的 readiness 文本。

    返回:
        str: 归一化后且已通过校验的 readiness 阶段名称。

    异常:
        ValueError: readiness 不在受支持阶段集合内时抛出。
    """

    # 先把用户输入转成小写，避免大小写差异影响阶段匹配。
    str_normalized_readiness = readiness.lower()  # 归一化后的 readiness 阶段名称

    # readiness 不在允许集合中时，立刻返回显式错误。
    if str_normalized_readiness not in dict_readiness_order:

        # 直接抛出允许阶段列表，提示调用方修正输入。
        raise ValueError(f"> ERR: [Python] Readiness must be one of {', '.join(READINESS_LEVELS)}.")

    # 返回已经通过校验的 readiness 文本。
    return str_normalized_readiness

# 判断某个 readiness 是否已经覆盖到指定阶段，用于切分 compile/execute 等流程。
def readiness_at_least(readiness: str, stage: str) -> bool:
    """判断 readiness 是否至少覆盖到指定阶段。

    参数:
        readiness: 当前声明的 readiness 阶段名称。
        stage: 需要比较的目标阶段名称。

    返回:
        bool: readiness 顺序大于等于目标阶段时返回 True。
    """

    # 通过阶段顺序映射直接比较两个阶段的先后关系。
    return dict_readiness_order[readiness] >= dict_readiness_order[stage]

# 汇总 HLS 产物树的静态检查、注释治理、工具链执行与报告指标。
def validate_generated(
    spec: dict[str, Any],
    path: Path,
    target: str | None = None,
    *,
    options: ValidationRunOptions | None = None,
    **dict_compat_options: Any,
) -> ValidationReport:
    """对生成后的 HLS 产物树执行完整验证。

    参数:
        spec: 已经通过上游合同整理的 HLS 规范字典。
        path: 待验证产物树的根目录路径。
        target: 可选的逻辑目标名；缺省时由规范内部决定。
        options: 可选的验证执行参数对象；缺省时使用默认验证策略。
        dict_compat_options: 旧式兼容关键字；仅允许
            run_external、readiness、comment_language、hls_profile、
            baseline_path。

    返回:
        ValidationReport: 聚合后的整轮验证结果对象。
    """

    # 先把输入 spec 归一化，避免后续每个子校验器重复兼容原始结构。
    dict_normalized_spec = normalize_spec(spec, target=target)  # 归一化后的 HLS 规范字典

    # 把显式 options 与旧式关键字兼容层合并成统一的运行参数对象。
    validation_run_options_obj_options: ValidationRunOptions = (  # 当前验证生效的可选执行参数对象
        _resolve_validation_run_options(  # 把两路输入合并成统一的 ValidationRunOptions
            options=options,  # 调用方显式传入的 ValidationRunOptions
            dict_compat_options=dict_compat_options,  # 旧式 validate_generated 关键字映射
        )  # 兼容层 helper 返回的统一验证参数对象
    )

    # 把 options 里的 readiness 先收敛成统一阶段名，后续所有子校验器都复用这一份结果。
    str_readiness = require_readiness(validation_run_options_obj_options.readiness)  # 当前验证实际生效的 readiness 阶段上限

    # 把注释语言统一收敛成 reviewability 与 AST 护栏共享的显式值。
    str_comment_language = _resolve_validation_comment_language(  # 当前验证实际生效的注释语言
        validation_run_options_obj_options,  # 调用方传入的验证运行选项
    )

    # 解析待验证目录的绝对路径，保证所有相对路径报告口径一致。
    path_root = path.resolve()  # 当前验证产物树的绝对根目录

    # 收集整轮验证过程中发现的全部结构化问题。
    list_issues: list[ValidationIssue] = []  # 当前验证累积的问题列表

    # 汇总工具链、报告和注释治理附加指标。
    dict_metrics: dict[str, Any] = {}  # 当前报告附带的结构化指标容器

    # 根目录不存在时，没有必要继续进入后续规则链。
    if not path_root.exists():

        # 直接返回生成目录缺失的阻断报告，避免继续进入后续扫描链。
        return _missing_generated_path_report(path_root)

    # 当前验证只从 HLS 侧向量与 testbench 工件中提取 case 标识，不再接受跨语言 reference_contract。
    list_reference_cases = _collect_reference_cases(path_root)  # 本轮验证可用的 HLS case 标识

    # 组合本轮 profile 配置，优先尊重显式传入覆盖。
    dict_profile = validation_run_options_obj_options.hls_profile or dict_normalized_spec.get("hls_profile") or {}  # 当前验证生效的 HLS profile 配置

    # 先汇总静态树、reference 与 profile 校验问题。
    list_static_issues = _collect_static_validation_issues(  # 静态树与合同阶段返回的问题列表
        dict_normalized_spec,  # 规范化后的验证规格
        path_root,  # 当前待验证的工作根目录
        list_reference_cases,  # reference case 标识集合
        dict_profile,  # 当前生效的 HLS profile 配置
        str_readiness,  # 当前 readiness 阶段标签
    )

    # 再把静态树阶段问题并回总问题列表。
    list_issues.extend(list_static_issues)

    # 先取出 reviewability helper 需要复用的 AST 护栏基线路径。
    path_reviewability_baseline = validation_run_options_obj_options.baseline_path  # reviewability helper 使用的 AST 护栏基线路径

    # 再补齐注释、AST 与 readability gate 指标，供最终报告统一复用。
    tuple_reviewability_result = _collect_reviewability_metrics(  # reviewability helper 返回的问题与指标
        dict_normalized_spec,  # reviewability helper 复用的规范合同输入
        path_root,  # reviewability helper 读取 HLS 交付树时使用的绝对根目录
        dict_profile,  # reviewability helper 推导 readability 档位的 profile 配置
        str_comment_language,  # reviewability helper 执行注释语言校验时使用的显式值
        path_reviewability_baseline,  # reviewability helper 做 AST 增量比对时使用的可选基线
    )

    # 解包 reviewability 聚合结果，分别并回问题列表与 metrics。
    tuple_reviewability_issues, dict_metrics = tuple_reviewability_result  # 注释治理与 readability 聚合结果

    # reviewability 相关问题也要参与最终阻断判断。
    list_issues.extend(tuple_reviewability_issues)

    # runtime helper 会补跑 testbench、placeholder、Vitis 规则与 readiness 工具链阶段。
    tuple_runtime_result = _collect_runtime_validation_issues(  # runtime helper 的原始二元返回值
        dict_normalized_spec,  # 规范合同输入
        path_root,  # 交付树根目录
        list_reference_cases,  # 已解析的 reference case 列表
        validation_run_options_obj_options,  # readiness 与工具链阶段的选项集合
        str_readiness,  # compile 或 execute 的深度上限
    )

    # 这里单独拆出 readiness 指标，后面还要并回统一 metrics。
    tuple_runtime_issues, dict_metrics_readiness = tuple_runtime_result  # 分拆成问题列表与 readiness metrics

    # readiness、testbench 与 Vitis 规则问题也要进入最终报告。
    list_issues.extend(tuple_runtime_issues)

    # 合并工具链执行阶段 metrics，补全最终报告的执行上下文。
    dict_metrics.update(dict_metrics_readiness)

    # 从问题列表中恢复语义执行概况，补充到 metrics 便于上游聚合。
    dict_semantic_execution = _semantic_execution_from_issues(list_issues)  # 从问题列表反推的执行语义摘要

    # 仅在确实推导出执行语义时才写入该字段。
    if dict_semantic_execution:

        # 把语义执行摘要落到 metrics，供后续合同对照直接消费。
        dict_metrics["semantic_execution"] = dict_semantic_execution  # 回填从 issue 反推出的语义执行摘要

    # 汇入综合、实现和报告侧的附加指标。
    dict_metrics.update(collect_hls_report_metrics(path_root))

    # 把时钟目标与 readiness 上限的组合校验追加到问题列表。
    list_issues.extend(
        _validate_hls_clock_goal(
            dict_normalized_spec,
            dict_metrics,
            str_readiness,
        )
    )

    # 在已有 metrics 基础上补充性能目标与 readiness 的一致性检查。
    list_issues.extend(
        _validate_performance(
            dict_normalized_spec,
            dict_metrics,
            str_readiness,
        )
    )

    # 返回完整聚合报告，供 CLI、测试和后续治理流程统一消费。
    return ValidationReport("hls", path_root, tuple(list_issues), dict_metrics)

# 把 comment_language 的 auto/显式输入统一成 reviewability 共用的白名单值。
def _resolve_validation_comment_language(
    validation_run_options_obj_options: ValidationRunOptions,
) -> str:
    """解析 validate_generated 需要使用的显式注释语言。

    参数:
        validation_run_options_obj_options: 调用方传入的验证运行选项对象。

    返回:
        str: reviewability、AST 护栏与 readability gate 共享的显式注释语言。
    """

    # 先判断注释语言是否仍处于 auto 模式。
    bool_auto_comment_language = str(validation_run_options_obj_options.comment_language).strip().lower() == "auto"  # 当前注释语言是否仍为 auto

    # auto 模式统一回落到中文治理口径。
    if bool_auto_comment_language:

        # auto 模式默认落到中文注释治理。
        return "zh"

    # 非 auto 场景继续走白名单校验，避免无效值下沉到子检查器。
    return require_comment_language(validation_run_options_obj_options.comment_language)

# 根目录缺失时直接返回统一的阻断报告，避免主编排函数内联早退构造。
def _missing_generated_path_report(path_root: Path) -> ValidationReport:
    """返回生成目录不存在时的阻断验证报告。

    参数:
        path_root: 当前准备扫描的生成产物根目录。

    返回:
        ValidationReport: 仅包含路径缺失问题的阻断验证报告。
    """

    # 先构造生成目录缺失时的单条阻断问题对象。
    issue_missing_generated_path = ValidationIssue(  # 生成目录缺失时需要回传的阻断问题
        "error",  # 阻断级别
        "> ERR: [Python] Generated path does not exist.",  # 稳定错误消息
        str(path_root),  # 缺失的 generated 根目录
        source="spec_issue",  # 问题来源标签
    )

    # 直接返回仅包含根路径错误的验证报告。
    return ValidationReport("hls", path_root, (issue_missing_generated_path,), {})

# 汇总不依赖 AST/readability 或 readiness 执行结果的静态树验证问题。
def _collect_static_validation_issues(
    dict_normalized_spec: dict[str, Any],
    path_root: Path,
    list_reference_cases: list[str],
    dict_profile: Any,
    str_readiness: str,
) -> list[ValidationIssue]:
    """收集 validate_generated 的静态树与合同检查问题。

    参数:
        dict_normalized_spec: 经过 normalize_spec 归一化后的 HLS 规范字典。
        path_root: 当前验证产物树的绝对根目录。
        list_reference_cases: reference contract 与产物树联合推导出的 case 列表。
        dict_profile: 当前验证生效的 HLS profile 配置对象。
        str_readiness: 当前验证实际生效的 readiness 阶段上限。

    返回:
        list[ValidationIssue]: 不依赖 reviewability 与工具链执行的静态阶段问题列表。
    """

    # 准备承接静态树、reference 与 profile 校验结果的问题列表。
    list_issues: list[ValidationIssue] = []  # 静态树校验累积的问题列表

    # 校验规范声明的输出是否与产物树实际文件集合一致。
    list_issues.extend(_validate_expected_outputs(dict_normalized_spec, path_root))

    # 阻止最终 HLS 交付物混入非 HLS 类型文件。
    list_issues.extend(_validate_hls_only_tree(path_root))

    # 检查向量合同与 testbench 中的 hash 标记是否匹配。
    list_issues.extend(_validate_vector_contracts(dict_normalized_spec, path_root))

    # 检查核心 HLS 源码、pragma、cfg 与接口契约是否对齐。
    list_issues.extend(_validate_hls(dict_normalized_spec, path_root))

    # 审查高级 HLS 头文件是否确实被当前 pattern 合同允许。
    list_issues.extend(_validate_advanced_library_alignment(dict_normalized_spec, path_root))

    # 先获取接口审计差异，再转成统一的 ValidationIssue 结构。
    list_issues.extend(
        _contract_gate_issues(
            plan_contract_interface_issues(
                dict_normalized_spec,
                audit_interface("hls", path_root),
            )
        )
    )

    # 把 profile 校验器返回的结构化问题挂到总问题列表。
    list_issues.extend(_profile_issues(validate_hls_profile(dict_profile, path_root, dict_normalized_spec)))

    # 返回静态树阶段累计的问题列表。
    return list_issues

# 汇总注释治理、AST 护栏与 readability gate 的问题和指标。
def _collect_reviewability_metrics(
    dict_normalized_spec: dict[str, Any],
    path_root: Path,
    dict_profile: Any,
    str_comment_language: str,
    path_requested_baseline: Path | None,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """收集注释治理、AST 护栏与 readability gate 的问题和指标。

    参数:
        dict_normalized_spec: 经过 normalize_spec 归一化后的 HLS 规范字典。
        path_root: 当前验证产物树的绝对根目录。
        dict_profile: 当前验证生效的 HLS profile 配置对象。
        str_comment_language: 已解析出的显式注释语言。
        path_requested_baseline: 可选的注释 AST 护栏基线路径。

    返回:
        tuple[list[ValidationIssue], dict[str, Any]]: 注释治理阶段的问题列表与指标字典。
    """

    # reviewability 相关问题需要单独累积后再并回总报告。
    list_issues: list[ValidationIssue] = []  # 注释治理阶段的问题列表

    # reviewability 相关指标会合并到 validate_generated 的最终 metrics。
    dict_metrics: dict[str, Any] = {}  # 注释治理阶段的指标字典

    # 把用户传入的 baseline 值规范化成 AST 护栏可直接消费的目录对象。
    path_baseline_root = path_requested_baseline.resolve() if path_requested_baseline else None  # AST 护栏最终消费的已解析基线路径

    # 运行 HLS 注释与可审阅性检查，单独保留其指标快照。
    tuple_reviewability_issues, tuple_dict_metrics_comment_policy = _validate_hls_reviewability(  # HLS 注释可审阅性问题与指标
        dict_normalized_spec,  # 当前规范化规格
        path_root,  # HLS 源码扫描根目录
        str_comment_language,  # 期望的注释语言
    )

    # 执行 AST 注释护栏，防止注释被生成流程悄悄削弱。
    tuple_ast_issues, tuple_dict_metrics_ast_guard = _validate_hls_comment_ast_guard(  # AST 注释护栏问题与指标
        path_root,  # HLS 工程扫描根目录
        baseline_path=path_baseline_root,  # AST 守卫使用的基线目录
    )

    # 先确定 HLS readability gate 使用的 profile 名称。
    str_readability_profile = "kernel"  # 默认回退到 kernel profile

    # 只有 profile 字典存在时，才允许覆盖默认的 readability profile。
    if isinstance(dict_profile, dict):

        # 用 profile 配置里的 readability_profile 覆盖默认 kernel 取值。
        str_readability_profile = str(dict_profile.get("readability_profile") or "kernel")  # 优先读取 profile 配置里的 readability_profile

    # 先取出 readability gate 需要显式接收的 top function 名称。
    str_top_function_name = str(  # readability gate 使用的 top function 名称
        dict_normalized_spec.get("interfaces", {}).get("top_function")  # 优先取接口声明的 top function
        or dict_normalized_spec["name"]  # 缺省回落到规格名称
    )

    # 补跑仓库内 HLS 可读性门禁，补齐通用校验未覆盖的交付约束。
    report_hls_readability_gate = run_hls_readability_gate(  # HLS readability gate 的完整扫描结果
        path_root,  # 直接扫描当前 HLS 产物树根目录
        profile=str_readability_profile,  # 使用当前 profile 映射出的 readability 档位
        style="current-project",  # 与仓库中文注释契约保持一致
        baseline_root=path_baseline_root,  # 沿用当前 AST 护栏使用的可选基线路径
        top_function=str_top_function_name,  # 传入 readability gate 的 top function 名称
    )

    # readability gate 报告会被多个指标字段复用，先转成字典避免重复序列化。
    dict_readability_report = report_hls_readability_gate.to_dict()  # HLS readability gate 的完整报告字典

    # 先并回注释治理阶段直接发现的问题。
    list_issues.extend(tuple_reviewability_issues)

    # 再并回 AST 护栏发现的问题。
    list_issues.extend(tuple_ast_issues)

    # 最后并回 readability gate 翻译后的问题。
    list_issues.extend(_readability_gate_issues(dict_readability_report.get("issues", [])))

    # 一次性写回 reviewability、AST 护栏与 readability gate 指标，避免重复赋值片段。
    dict_metrics.update(
        {
            "comment_policy": tuple_dict_metrics_comment_policy,  # 注释治理指标快照
            "hls_ast_guard": tuple_dict_metrics_ast_guard,  # AST 注释护栏主指标
            "hls_ast_comment_guard": tuple_dict_metrics_ast_guard,  # AST 注释护栏兼容指标
            "readability_gate": dict_readability_report,  # HLS readability gate 的完整报告
            "comment_quality_gate": report_hls_readability_gate.metrics.get("comment_quality_gate", {}),  # readability gate 的注释质量子指标
            "hls_naming_gate": report_hls_readability_gate.metrics.get("hls_naming_gate", {}),  # readability gate 的命名质量子指标
        }
    )

    # 返回注释治理阶段累计的问题和指标。
    return list_issues, dict_metrics

# 汇总 testbench、placeholder、Vitis 规则与 readiness 工具链阶段的问题和指标。
def _collect_runtime_validation_issues(
    dict_normalized_spec: dict[str, Any],
    path_root: Path,
    list_reference_cases: list[str],
    validation_run_options_obj_options: ValidationRunOptions,
    str_readiness: str,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """收集 validate_generated 的运行期补充问题和工具链指标。

    参数:
        dict_normalized_spec: 经过 normalize_spec 归一化后的 HLS 规范字典。
        path_root: 当前验证产物树的绝对根目录。
        list_reference_cases: reference contract 与产物树联合推导出的 case 列表。
        validation_run_options_obj_options: 当前验证运行选项对象。
        str_readiness: 当前验证实际生效的 readiness 阶段上限。

    返回:
        tuple[list[ValidationIssue], dict[str, Any]]: 运行期补充问题列表与工具链指标字典。
    """

    # 准备承接 testbench、placeholder、Vitis 规则与 readiness 的问题列表。
    list_issues: list[ValidationIssue] = []  # readiness 阶段累积的问题列表

    # 对 testbench 入口、PASS/FAIL 和 reference case 提及进行补充检查。
    list_issues.extend(_validate_hls_testbench(dict_normalized_spec, path_root, list_reference_cases))

    # 阻止 TODO、FIXME 与省略号占位文本泄露到最终产物中。
    list_issues.extend(_validate_placeholders(path_root, _hls_files(path_root)))

    # 追加 Vitis 规则扫描器发现的专用违规项。
    list_issues.extend(_validate_vitis_rules(path_root))

    # 再按 readiness 上限和外部工具开关执行 compile、execute 等工具链阶段。
    tuple_tool_issues, tuple_dict_metrics_readiness = _run_hls_readiness(  # 工具链执行阶段的问题与指标
        dict_normalized_spec,  # 工具链阶段复用的规范合同输入
        path_root,  # 工具链阶段执行与产物落盘共同依赖的交付树根目录
        str_readiness,  # 工具链阶段判断 compile 或 execute 深度时使用的 readiness 上限
        validation_run_options_obj_options.run_external,  # 工具链阶段是否放行外部执行的显式开关
    )

    # 把工具链阶段问题并回同一份 runtime 问题列表。
    list_issues.extend(tuple_tool_issues)

    # 返回 runtime 补充问题与工具链指标。
    return list_issues, tuple_dict_metrics_readiness

# 把 readability gate 的 issue 字典转换成统一的 ValidationIssue 对象。
def _readability_gate_issues(raw_issues: list[dict[str, Any]]) -> list[ValidationIssue]:
    """转换 HLS readability gate 的原始问题字典。

    参数:
        raw_issues: readability gate 输出的 issue 字典列表。

    返回:
        list[ValidationIssue]: 转换后的统一问题对象列表。
    """

    # 准备承接转换结果的统一问题列表。
    list_converted_issues: list[ValidationIssue] = []  # readability gate 转换后的问题列表

    # 逐条吸收 readability gate 的 issue 字段。
    for dict_issue in raw_issues:

        # 读取原始 issue 的严重级别，缺省时按 warning 处理。
        str_severity = str(dict_issue.get("severity", "warning"))  # 原始 issue 对应的严重级别

        # 把单条 readability issue 转成统一问题对象并追加到结果列表。
        list_converted_issues.append(
            ValidationIssue(
                str_severity,
                (
                    f"{dict_issue.get('rule') or dict_issue.get('code')}: "
                    f"{dict_issue.get('message', 'HLS readability issue.')}"
                ),
                dict_issue.get("path"),
                "static",
                "readability_gate",
                detail=dict_issue.get("detail") or dict_issue.get("code_excerpt"),
            )
        )

    # 返回全部转换完成的问题对象。
    return list_converted_issues

# 把 profile 校验器的 issue 字典转成统一问题对象。
def _profile_issues(raw_issues: list[dict[str, Any]]) -> list[ValidationIssue]:
    """转换 HLS profile 校验器返回的问题字典。

    参数:
        raw_issues: profile 校验器返回的原始 issue 字典列表。

    返回:
        list[ValidationIssue]: 转换后的统一问题对象列表。
    """

    # 直接把 profile issue 字典投影成 ValidationIssue 列表。
    return [
        ValidationIssue(
            str(dict_issue.get("severity", "warning")),
            str(dict_issue.get("message", "HLS profile issue.")),
            dict_issue.get("path"),
            str(dict_issue.get("stage", "static")),
            str(dict_issue.get("source", "current_module_issue")),
            dict_issue.get("case_id"),
            dict_issue.get("tool"),
            dict_issue.get("detail"),
        )
        for dict_issue in raw_issues
    ]

# 把接口合同规划器的问题字典映射成统一问题对象。
def _contract_gate_issues(raw_issues: list[dict[str, Any]]) -> list[ValidationIssue]:
    """转换接口合同规划器返回的问题字典。

    参数:
        raw_issues: 接口合同规划器输出的原始 issue 字典列表。

    返回:
        list[ValidationIssue]: 转换后的统一问题对象列表。
    """

    # 直接把接口合同 issue 列表映射到统一问题对象。
    return [
        ValidationIssue(
            str(dict_issue.get("severity", "error")),
            str(dict_issue.get("message", "Interface contract issue.")),
            dict_issue.get("path"),
            "static",
            str(dict_issue.get("source", "current_module_issue")),
            dict_issue.get("case_id"),
        )
        for dict_issue in raw_issues
    ]

# 运行 HLS AST 注释护栏，并把结果转换成统一问题对象与指标字典。
def _validate_hls_comment_ast_guard(
    root: Path,
    *,
    baseline_path: Path | None,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """执行 HLS AST 注释护栏。

    参数:
        root: 待检查 HLS 产物树根目录。
        baseline_path: 可选的基线目录路径。

    返回:
        tuple[list[ValidationIssue], dict[str, Any]]: 统一问题列表与护栏指标字典。
    """

    # 运行 AST 注释护栏，并同时拿到原始 issue 与指标快照。
    list_raw_issues, dict_metrics = validate_hls_ast_guard(  # 汇总 AST 注释护栏原始结果
        root,  # 指向当前待比对的 HLS 产物树根目录
        _hls_files(root),  # 参与 AST 护栏检查的全部 HLS 文件
        baseline_root=baseline_path,  # 让护栏按既有注释 AST 做增量比对
    )

    # 把 AST 护栏问题对象逐条转成 ValidationIssue。
    list_issues: list[ValidationIssue] = []  # AST 护栏原始问题映射后的统一 ValidationIssue 列表

    # 逐条封装 AST 护栏 issue，保证上游消费统一的数据结构。
    for raw_issue in list_raw_issues:

        # 把单条 AST 护栏问题包装成 ValidationIssue 后加入结果列表。
        list_issues.append(
            ValidationIssue(
                raw_issue.severity,
                raw_issue.message,
                raw_issue.path,
                "static",
                "ast_guard",
                tool=raw_issue.tool,
                detail=raw_issue.detail,
            )
        )

    # 返回统一问题列表和护栏指标，供上游主流程继续聚合。
    return list_issues, dict_metrics

# 校验 spec 声明的 expected outputs 是否与真实 HLS 产物树一致。
def _validate_expected_outputs(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    """检查 expected outputs 声明与实际文件集合是否一致。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。

    返回:
        list[ValidationIssue]: 输出缺失或额外产物对应的问题列表。
    """

    # 汇总 expected outputs 检查生成的全部问题。
    list_issues: list[ValidationIssue] = []  # 输出声明一致性检查的问题列表

    # 先创建 expected outputs 的去重路径集合。
    set_expected_paths: set[str] = set()  # spec 中声明的输出路径集合

    # 逐条遍历 spec 声明的输出项，提取其中显式给出的路径。
    for dict_output in spec["outputs"]:

        # 只收集真正声明了 path 的输出项。
        if isinstance(dict_output, dict) and dict_output.get("path"):

            # 把输出项声明的相对路径加入去重集合。
            set_expected_paths.add(str(dict_output["path"]))

    # 逐条确认声明输出是否已经真实落盘。
    for dict_output in spec["outputs"]:

        # 文件缺失时，记录合同声明与产物树状态不一致。
        if not (root / dict_output["path"]).exists():

            # 把缺失 expected output 的问题登记到结果列表。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"Expected output file is missing: {dict_output['path']}",
                    dict_output["path"],
                    source="spec_issue",
                )
            )

    # 遍历真实 HLS 文件集合，查找合同未声明的额外交付物。
    for path_file in _hls_files(root):

        # 把文件路径转成统一的相对 POSIX 文本。
        str_rel_path = path_file.relative_to(root).as_posix()  # 当前 HLS 文件的相对路径文本

        # 文件没有出现在 expected outputs 中时，记录为越界产物。
        if str_rel_path not in set_expected_paths:

            # 把未声明的额外交付物登记成 spec 越界问题。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Unexpected HLS source/config artifact is not declared in "
                    f"expected outputs: {str_rel_path}",
                    str_rel_path,
                    source="spec_issue",
                )
            )

    # 返回输出声明一致性检查结果。
    return list_issues

# 确保最终 HLS 交付树不会混入受边界禁止的非 HLS 文件。
def _validate_hls_only_tree(root: Path) -> list[ValidationIssue]:
    """检查 HLS-only 交付边界。

    参数:
        root: 当前验证产物树根目录。

    返回:
        list[ValidationIssue]: 发现越界文件时生成的问题列表。
    """

    # 收集 HLS-only 边界检查产生的全部问题。
    list_issues: list[ValidationIssue] = []  # HLS-only 交付边界问题列表

    # 扫描 Verilog/SystemVerilog 文件，阻止手写 RTL 混入最终交付树。
    for path_file in sorted([*root.glob("**/*.v"), *root.glob("**/*.sv")]):

        # 发现 RTL 文件时，记录它违反 HLS-only 技能边界。
        list_issues.append(
            ValidationIssue(
                "error",
                "Generated Verilog/SystemVerilog files are not allowed in this "
                "HLS-only skill.",
                path_file.relative_to(root).as_posix(),
                "static",
                "spec_issue",
            )
        )

    # 扫描 Python、JSON 和 Markdown，阻止说明性文件混入最终产物。
    for path_file in sorted([*root.glob("**/*.py"), *root.glob("**/*.json"), *root.glob("**/*.md")]):

        # 发现非 HLS 交付物时，登记边界违规项。
        list_issues.append(
            ValidationIssue(
                "error",
                "Final HLS artifacts must not mix in Python scripts, JSON "
                "contracts, or Markdown notes.",
                path_file.relative_to(root).as_posix(),
                "static",
                "spec_issue",
            )
        )

    # 返回 HLS-only 边界检查结果。
    return list_issues

# 收集当前产物树中所有受 HLS 规则治理的源码与 cfg 文件。
def _hls_files(root: Path) -> list[Path]:
    """枚举 HLS 源码和配置文件。

    参数:
        root: 当前验证产物树根目录。

    返回:
        list[Path]: 当前产物树中匹配到的 HLS 文件路径列表。
    """

    # 固定声明需要纳入治理的 HLS 文件匹配模式。
    tuple_patterns = ("**/*.cpp", "**/*.cc", "**/*.cxx", "**/*.h", "**/*.hpp", "**/*.cfg")  # 当前模块认可的 HLS 文件 glob 模式集合

    # 准备累积所有命中的 HLS 文件路径。
    list_files: list[Path] = []  # 当前产物树收集到的 HLS 文件路径列表

    # 按模式逐类展开 glob，保持 cpp、header 和 cfg 全覆盖。
    for str_pattern in tuple_patterns:

        # 把当前模式命中的文件稳定追加到结果列表。
        list_files.extend(sorted(root.glob(str_pattern)))

    # 返回收集完成的 HLS 文件路径列表。
    return list_files

# 扫描最终 HLS 源码里是否残留 TODO、FIXME 或省略号占位文本。
def _validate_placeholders(root: Path, files: list[Path]) -> list[ValidationIssue]:
    """检查 HLS 产物中是否残留占位文本。

    参数:
        root: 当前验证产物树根目录。
        files: 需要扫描的 HLS 文件路径列表。

    返回:
        list[ValidationIssue]: 命中占位文本时生成的问题列表。
    """

    # 收集占位文本检查产生的全部问题。
    list_issues: list[ValidationIssue] = []  # 占位文本检查的问题列表

    # 明确列出最终 HLS 产物里禁止出现的占位文本模式。
    dict_banned_patterns = {
        r"\bTODO\b": "Placeholder TODO remains in generated code.",  # 拦截 TODO 标记
        r"\bFIXME\b": "Placeholder FIXME remains in generated code.",  # 拦截生成后待修的 FIXME 标记
        r"your code here": "Placeholder text remains in generated code.",  # 拦截模板脚手架占位文本
        r"\.\.\.": "Ellipsis placeholder remains in generated code.",  # 拦截省略号占位符
    }  # 禁止出现在最终产物中的占位文本模式表

    # 逐个文件读取文本，并匹配所有禁止模式。
    for path_file in files:

        # 读取当前 HLS 文件文本，为正则扫描提供输入。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 当前 HLS 文件的完整文本

        # 转成相对路径文本，便于统一写入报告。
        str_rel_path = path_file.relative_to(root).as_posix()  # 写入 ValidationIssue 时使用的产物树内相对路径

        # 针对每种占位文本模式逐条扫描。
        for str_pattern, str_message in dict_banned_patterns.items():

            # 命中占位文本时，记录对应静态错误。
            if re.search(str_pattern, str_text, flags=re.IGNORECASE):

                # 把命中的占位文本问题登记到最终静态检查结果。
                list_issues.append(
                    ValidationIssue(
                        "error",
                        str_message,
                        str_rel_path,
                        "static",
                    )
                )

    # 返回占位文本检查结果。
    return list_issues

# 校验 HLS testbench 的入口、PASS/FAIL 行为与 case 提及情况。
def _validate_hls_testbench(
    spec: dict[str, Any],
    root: Path,
    reference_cases: list[str],
) -> list[ValidationIssue]:
    """检查 HLS testbench 是否满足基础验证契约。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。
        reference_cases: 已知 reference case 标识列表。

    返回:
        list[ValidationIssue]: testbench 检查产生的问题列表。
    """

    # 先准备 spec 中声明或按命名识别出的 testbench 路径列表。
    list_requested_tb: list[str] = []  # spec 中声明或按命名识别出的 testbench 路径列表

    # 逐条检查输出项，提取 testbench 路径。
    for dict_output in spec["outputs"]:

        # 按文件名约定识别没有显式 kind 的 testbench。
        bool_is_named_testbench = "_tb." in dict_output["path"].lower()  # 当前输出项是否命中 testbench 命名约定

        # 只把显式或按命名识别出的 testbench 路径加入结果列表。
        if dict_output.get("kind") == "testbench" or bool_is_named_testbench:

            # 把当前输出项路径加入 testbench 巡检列表。
            list_requested_tb.append(dict_output["path"])

    # 为当前 testbench 巡检准备统一的问题收集列表。
    list_issues: list[ValidationIssue] = []  # main、top 调用与 case 提及检查累积的问题

    # 解析 top function 名称，后续要确认 testbench 是否真实调用它。
    str_top = str(spec.get("interfaces", {}).get("top_function") or spec["name"])  # 当前 HLS 规范对应的 top function 名称

    # 逐个 testbench 文件检查入口、top 调用和 case 提及。
    for str_rel_path in list_requested_tb:

        # 组合 testbench 的绝对路径，准备后续读取文本。
        path_testbench = root / str_rel_path  # 当前 testbench 文件的绝对路径

        # 声明过但尚未落盘的 testbench 由其它检查处理，这里直接跳过。
        if not path_testbench.exists():

            # 跳过缺失 testbench，避免重复登记相同缺失问题。
            continue

        # 读取 testbench 文本，供入口和 case 匹配复用。
        str_text = path_testbench.read_text(encoding="utf-8", errors="ignore")  # 当前 testbench 文件的全文文本

        # main 入口缺失时，testbench 无法作为独立验证入口运行。
        if not re.search(r"\bint\s+main\s*\(", str_text):

            # 把缺少 main 入口的问题登记到当前 testbench 的静态检查结果。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "HLS testbench main() entry point was not found.",
                    str_rel_path,
                    "static",
                    "testbench_issue",
                )
            )

        # top function 没被调用时，说明 testbench 与核心内核脱节。
        if not re.search(rf"\b{re.escape(str_top)}\s*\(", str_text):

            # 把缺少 top function 调用的问题登记到当前 testbench 的静态检查结果。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"HLS testbench must call top function {str_top!r}.",
                    str_rel_path,
                    "static",
                    "testbench_issue",
                )
            )

        # 把 PASS/FAIL 文本约束的检查结果并入总问题列表。
        list_issues.extend(_validate_pass_fail_text(str_text, str_rel_path))

        # 把 verification case 提及情况的检查结果并入总问题列表。
        list_issues.extend(
            _validate_case_mentions(
                spec,
                str_text,
                str_rel_path,
                reference_cases,
            )
        )

    # 返回全部 testbench 检查问题。
    return list_issues

# 确认 testbench 文本中同时存在 PASS 和 FAIL 行为标记。
def _validate_pass_fail_text(text: str, rel_path: str) -> list[ValidationIssue]:
    """检查 testbench 是否包含 PASS 和 FAIL 文本。

    参数:
        text: testbench 文件文本。
        rel_path: testbench 的相对路径文本。

    返回:
        list[ValidationIssue]: PASS/FAIL 行为缺失时的问题列表。
    """

    # 收集 PASS/FAIL 行为检查生成的问题。
    list_issues: list[ValidationIssue] = []  # PASS/FAIL 行为检查的问题列表

    # 缺少 PASS 标记时，说明通过行为不可见。
    if not re.search(r"\bPASS\b", text, flags=re.IGNORECASE):

        # 把缺少 PASS 行为的问题登记到当前 testbench 的静态检查结果。
        list_issues.append(
            ValidationIssue(
                "error",
                "HLS testbench does not contain explicit PASS behavior.",
                rel_path,
                "static",
                "testbench_issue",
            )
        )

        # 缺少 FAIL 标记时，说明失败行为不可见。
        if not re.search(r"\bFAIL\b", text, flags=re.IGNORECASE):

            # 把缺少显式 FAIL 行为的 testbench 记录成阻断问题。
            list_issues.append(
                ValidationIssue(
                    "error",
                "HLS testbench does not contain explicit FAIL behavior.",
                rel_path,
                "static",
                "testbench_issue",
            )
        )

    # 返回 PASS/FAIL 检查结果。
    return list_issues

# 检查 testbench 是否提到了 spec 和 reference contract 里的关键 case。
def _validate_case_mentions(
    spec: dict[str, Any],
    text: str,
    rel_path: str,
    reference_cases: list[str],
) -> list[ValidationIssue]:
    """检查 testbench 是否提到全部关键 case。

    参数:
        spec: 当前 HLS 规范字典。
        text: testbench 文件文本。
        rel_path: testbench 的相对路径文本。
        reference_cases: 已知 reference case 标识列表。

    返回:
        list[ValidationIssue]: case 提及缺失时的问题列表。
    """

    # 收集 case 提及检查产生的问题。
    list_issues: list[ValidationIssue] = []  # case 提及检查的问题列表

    # 先把 testbench 文本降为小写，避免大小写影响匹配。
    str_lowered_text = text.lower()  # 统一成小写后的 testbench 文本

    # 合并 spec 内部 case 与 reference contract case，一次性检查。
    for str_case_id in [*_verification_cases(spec), *reference_cases]:

        # 当前 case 未出现在 testbench 文本中时，记录缺失问题。
        if str_case_id.lower() not in str_lowered_text:

            # 把 testbench 未提及 verification case 的问题登记到结果列表。
            list_issues.append(
                ValidationIssue(
                    "error" if str_case_id in reference_cases else "warning",
                    f"Verification case {str_case_id!r} is not mentioned in the "
                    "HLS testbench.",
                    rel_path,
                    "static",
                    (
                        "testbench_issue"
                        if str_case_id in reference_cases
                        else "spec_issue"
                    ),
                    case_id=str_case_id,
                )
            )

    # 返回 case 提及检查结果。
    return list_issues

# 把 verification case 条目统一归一化成稳定标识，避免不同写法重复分支处理。
def _verification_case_id(raw_case: Any) -> str:
    """提取单个 verification case 条目的稳定标识。

    参数:
        raw_case: verification_cases 中的单个原始条目，可以是文本或字典。

    返回:
        str: 当前条目归一化后的稳定 case 标识文本。
    """

    # 先按普通文本值准备缺省标识，兼容最简单的字符串声明。
    str_case_id = str(raw_case)  # 当前 verification case 的缺省标识文本

    # 字典条目优先读取显式命名字段，保持 case 命名稳定。
    if isinstance(raw_case, dict):

        # 优先使用 id，其次回退到 name 或 text 字段。
        str_case_id = str(raw_case.get("id") or raw_case.get("name") or raw_case.get("text"))  # 当前 verification case 的归一化标识文本

    # 返回当前条目最终使用的 case 标识文本。
    return str_case_id

# 从单个 spec 字段条目列表中补充 verification case 标识。
def _extend_verification_cases(
    list_cases: list[str],
    obj_field_entries: Any,
) -> None:
    """把 behavior/constraints/test_intent 中声明的 case 追加到结果列表。

    参数:
        list_cases: 当前已经收集到的去重 case 标识列表。
        obj_field_entries: 当前字段原始值；只有列表形态才会继续解析。

    返回:
        None: 结果直接追加到传入的 list_cases 中。
    """

    # 非列表字段不具备稳定条目结构，这里直接跳过。
    if not isinstance(obj_field_entries, list):

            # 当前字段结构不可安全遍历，这里直接终止本次补充流程。
            return

    # 顺序遍历字段中的每个结构化条目。
    for item in obj_field_entries:

        # 只有字典条目才可能承载 verification_cases 字段。
        if not isinstance(item, dict):

            # 当前条目缺少结构化字段，不参与 verification case 提取。
            continue

        # 逐个提取当前条目声明的 verification case。
        for raw_case in item.get("verification_cases", []):

            # 把不同写法的 case 条目统一收敛成稳定标识。
            str_case_id = _verification_case_id(raw_case)  # 当前 verification case 的稳定标识文本

            # 非空且未出现过的 case 标识才追加到结果列表。
            if str_case_id and str_case_id not in list_cases:

                # 把当前 spec 中首次出现的 case 标识加入返回列表。
                list_cases.append(str_case_id)

# 从 spec.subfunctions 中提取所有用于验证追踪的 case 标识。
def _verification_cases(spec: dict[str, Any]) -> list[str]:
    """提取 spec 中声明的 verification case 标识。

    参数:
        spec: 当前 HLS 规范字典。

    返回:
        list[str]: 去重后的 verification case 标识列表。
    """

    # 准备去重收集 verification case 标识。
    list_cases: list[str] = []  # spec 中解析出的 verification case 标识列表

    # 遍历所有子功能，抽取行为、约束和测试意图里的 case。
    for dict_subfunction in spec.get("subfunctions", []):

        # 非字典子功能无法提供结构化 case 信息，这里直接跳过。
        if not isinstance(dict_subfunction, dict):

            # 跳过不合法的 subfunction 结构，避免影响其它条目解析。
            continue

        # 固定检查会包含 verification_cases 的三个字段。
        for str_field_name in ("behavior", "constraints", "test_intent"):

            # 把当前字段声明的 verification case 统一并入结果列表。
            _extend_verification_cases(list_cases, dict_subfunction.get(str_field_name, []))

    # 返回 spec 内部声明过的全部 case 标识。
    return list_cases

# 从 vectors.json 中补充搜集可用 case 标识。
def _collect_reference_cases(root: Path) -> list[str]:
    """扫描产物树中的向量 case 标识。

    参数:
        root: 当前验证产物树根目录。

    返回:
        list[str]: 去重后的向量 case 标识列表。
    """

    # 只收集向量工件中显式声明的 case 标识。
    list_cases: list[str] = []  # 产物树扫描得到的向量 case 标识列表

    # 先扫描 vectors.json，优先复用结构化 case 定义。
    for path_vectors in sorted(root.glob("**/*vectors.json")):

        # 尝试解析当前 vectors.json；格式损坏时跳过该文件。
        try:

            # 读取并解析当前 vectors.json，供 case 标识提取复用。
            payload = json.loads(path_vectors.read_text(encoding="utf-8"))  # 当前 vectors.json 解析出的负载对象

        # JSON 解析失败时，不让坏文件阻断其它候选 case 的搜集。
        except Exception:

            # 跳过当前损坏的 vectors.json，继续扫描其余文件。
            continue

        # 从当前 vectors 负载中提取全部 case 标识。
        for str_case_id in _case_ids_from_payload(payload):

            # 同一个 case 只保留第一次出现的记录。
            if str_case_id not in list_cases:

                # 把 vectors.json 中首次出现的 case 标识加入结果列表。
                list_cases.append(str_case_id)

    # 返回产物树内搜集到的全部向量 case 标识。
    return list_cases

# 从 vectors 负载对象中解析出可用于对照的 case 标识。
def _case_ids_from_payload(payload: Any) -> list[str]:
    """从 vectors 负载对象中提取 case 标识。

    参数:
        payload: vectors.json 解析后的负载对象。

    返回:
        list[str]: 从负载中提取并去重后的 case 标识列表。
    """

    # 兼容 cases、vectors 和直接列表三种输入结构。
    list_raw_cases = payload.get("cases", payload.get("vectors", [])) if isinstance(payload, dict) else payload  # 当前负载中待解析的原始 case 集合

    # 非列表结构无法逐项提取 case 标识，这里直接返回空。
    if not isinstance(list_raw_cases, list):

        # 返回空列表，表示该负载不提供可识别 case 集合。
        return []

    # 准备承接解析出的 case 标识，并做顺序去重。
    list_case_ids: list[str] = []  # 从 payload 解析出的 case 标识列表

    # 为每个 case 生成稳定标识，缺省时回退到 case_序号。
    for index_case, raw_case in enumerate(list_raw_cases, start=1):

        # 先准备当前 payload 项的缺省 case 名，兼容没有显式 id 的情况。
        str_case_id = f"case_{index_case}"  # 当前 payload 项的缺省 case 标识文本

        # payload 用字典输入时，优先尊重业务显式给出的 case 标识。
        if isinstance(raw_case, dict):

            # 用字典里显式给出的 id/name 覆盖缺省 case 名。
            str_case_id = str(raw_case.get("id") or raw_case.get("name") or f"case_{index_case}")  # 当前字典 payload 项解析出的 case 标识文本

        # 只保留第一次出现的 case 标识。
        if str_case_id not in list_case_ids:

            # 把当前 payload 中首次出现的 case 标识加入去重结果。
            list_case_ids.append(str_case_id)

    # 返回从负载中提取完成的 case 标识列表。
    return list_case_ids

# 读取向量合同并检查其中的 hash 是否真实出现在 testbench 中。
def _validate_vector_contracts(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    """检查 reference vector 合同的 hash 是否落到 HLS testbench 中。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。

    返回:
        list[ValidationIssue]: vector hash 缺失时生成的问题列表。
    """

    # 读取产物树中的向量合同，作为后续 hash 对照基线。
    list_contracts = find_vector_contracts(root)  # 当前产物树扫描得到的向量合同列表

    # 没有向量合同就无需继续做 hash 对照检查。
    if not list_contracts:

        # 返回空列表，表示本轮没有 vector contract 约束。
        return []

    # 解析所有可能承载 hash 标签的 testbench 文件路径。
    list_testbench_paths = _testbench_files_for(spec, root)  # 当前可用于 hash 校验的 testbench 文件路径列表

    # 有向量合同却没有 testbench 时，无法验证 hash 是否真正落到产物中。
    if not list_testbench_paths:

        # 直接返回缺少 testbench 的阻断问题，提示无法完成向量 hash 验证。
        return [
            ValidationIssue(
                "error",
                "Reference vectors exist but no HLS testbench file was found for "
                "vector hash validation.",
                stage="static",
                source="testbench_issue",
            )
        ]

    # 汇总所有 testbench 中实际出现过的 vector hash 标签。
    list_hashes: list[str] = []  # testbench 中提取到的 vector hash 列表

    # 逐个 testbench 抽取已嵌入的 vector hash。
    for path_testbench in list_testbench_paths:

        # 读取当前 testbench 文本并解析其中的 hash 标签。
        list_detected_hashes = extract_vector_hashes(path_testbench.read_text(encoding="utf-8", errors="ignore"))  # 当前 testbench 中抽取到的 vector hash 列表

        # 去重追加当前 testbench 中发现的 hash。
        for str_hash_value in list_detected_hashes:

            # 只保留第一次出现的 hash，避免重复告警统计膨胀。
            if str_hash_value not in list_hashes:

                # 把当前 testbench 中首次识别出的 hash 加入去重结果。
                list_hashes.append(str_hash_value)

    # 为缺失 hash 的 vector contract 准备统一的问题收集列表。
    list_issues: list[ValidationIssue] = []  # vector hash 合同缺失问题列表

    # 逐条合同确认期望 hash 是否已经进入 testbench。
    for dict_contract in list_contracts:

        # 提取当前合同要求的 sha256 文本。
        str_expected_hash = str(dict_contract.get("sha256"))  # 当前 vector contract 期望出现的 hash 文本

        # testbench 中找不到该 hash 时，记录缺失问题。
        if str_expected_hash not in list_hashes:

            # 把缺失目标 hash 的 vector contract 问题登记到结果列表。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Reference vector contract hash is missing from HLS testbench; "
                    f"expected `{VECTOR_HASH_TAG} {str_expected_hash}`.",
                    dict_contract.get("path"),
                    "static",
                    "testbench_issue",
                )
            )

    # 返回 vector contract 的 hash 对照结果。
    return list_issues

# 从 spec 声明和实际文件树中定位全部 testbench 文件。
def _testbench_files_for(spec: dict[str, Any], root: Path) -> list[Path]:
    """定位当前产物树里可用于验证的 testbench 文件。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。

    返回:
        list[Path]: 现存 testbench 文件路径列表。
    """

    # 累积 spec 明确声明过的 testbench 路径。
    list_requested_paths: list[Path] = []  # spec 中声明的 testbench 绝对路径列表

    # 逐条检查 outputs，识别其中的 testbench 文件。
    for dict_output in spec.get("outputs", []):

        # 非法结构或缺少 path 时，无法参与 testbench 判定。
        if not isinstance(dict_output, dict) or not dict_output.get("path"):

            # 跳过结构不完整的输出条目，避免污染结果集。
            continue

        # 把 spec 中声明的路径转成绝对路径对象。
        path_output = root / str(dict_output["path"])  # 当前输出条目的绝对路径

        # 命中显式 kind 或 _tb 命名模式时，视作 testbench 候选。
        if dict_output.get("kind") == "testbench" or "_tb." in path_output.name.lower():

            # 把 spec 明确声明的 testbench 路径加入候选集合。
            list_requested_paths.append(path_output)

    # 优先返回 spec 中声明且真实存在的 testbench 文件。
    list_existing_requested = [path_item for path_item in list_requested_paths if path_item.exists()]  # spec 声明且实际存在的 testbench 文件列表

    # spec 候选存在时直接使用，否则回退到文件树中的 _tb 模式扫描结果。
    return list_existing_requested or [
        path_file
        for path_file in _hls_files(root)
        if "_tb." in path_file.name.lower()
    ]

# 检查核心 HLS 源码、pragma 与 cfg 是否满足基础合同要求。
def _validate_hls(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    """执行 HLS 源码级基础合同检查。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。

    返回:
        list[ValidationIssue]: HLS 源码、接口 pragma 和 cfg 对应的问题列表。
    """

    # 汇总 HLS 源码级检查发现的全部问题。
    list_issues: list[ValidationIssue] = []  # HLS 源码、接口与 cfg 合同检查累计的问题

    # 收集所有 C/C++ HLS 文件，供后续区分 source 与 testbench。
    set_cpp_suffixes = {".cpp", ".cc", ".cxx", ".h", ".hpp"}  # HLS C/C++ 文件后缀集合

    # 先筛出所有 C/C++ 后缀的 HLS 文件，作为 source/testbench 划分基线。
    list_cpp_files = [path_file for path_file in _hls_files(root) if path_file.suffix.lower() in set_cpp_suffixes]  # 产物树中的 HLS C/C++ 文件列表

    # 再从 C/C++ HLS 文件里排除 testbench，只保留核心 kernel 源文件。
    list_source_files = [path_file for path_file in list_cpp_files if "_tb" not in path_file.stem.lower()]  # 核心 kernel 源文件列表

    # 先按文件顺序读取核心源文本，供后续统一拼接扫描。
    list_source_text_parts = [path_file.read_text(encoding="utf-8", errors="ignore") for path_file in list_source_files]  # 核心 HLS 源文件的分段文本

    # 把分段源文本拼成单个字符串，便于统一做关键字与正则扫描。
    str_source_text = "\n".join(list_source_text_parts)  # 核心 HLS 源文件的合并文本

    # 解析当前规范要求的 top function 名称。
    str_top = str(spec.get("interfaces", {}).get("top_function") or spec["name"])  # 当前 HLS 规范要求的 top function 名称

    # 源码中找不到 top function 时，说明核心入口没有真正生成。
    if not re.search(rf"\b{re.escape(str_top)}\s*\(", str_source_text):

        # 把缺失 top function 的问题登记到源码级检查结果。
        list_issues.append(
            ValidationIssue(
                "error",
                f"Top HLS function {str_top!r} was not found.",
            )
        )

    # 完全缺少 HLS pragma 时，保留 warning 提醒综合控制信息不足。
    if "#pragma HLS" not in str_source_text:

        # 把缺少 pragma 的告警登记到源码级检查结果。
        list_issues.append(
            ValidationIssue(
                "warning",
                "No Vitis HLS pragmas were found.",
            )
        )

    # 解析源码中的 INTERFACE pragma，供端口级约束检查复用。
    list_interface_pragmas = _parse_hls_interface_pragmas(str_source_text)  # 从源码中解析出的 INTERFACE pragma 列表

    # 按 port 聚合 pragma，便于逐参数比对接口合同。
    dict_pragmas_by_port: dict[str, list[dict[str, str]]] = {}  # 按端口聚合的 INTERFACE pragma 字典

    # 遍历所有 pragma，把带 port 的条目挂到对应端口名下。
    for dict_pragma in list_interface_pragmas:

        # 只有声明了 port 的 pragma 才能参与端口级对照。
        if dict_pragma.get("port"):

            # 为当前 port 名建立或复用 pragma 累积列表。
            list_port_pragmas = dict_pragmas_by_port.setdefault(str(dict_pragma["port"]), [])  # 当前端口名对应的 pragma 列表

            # 把当前 pragma 挂到所属端口名下，供后续合同比对。
            list_port_pragmas.append(dict_pragma)

    # 逐个接口参数校验 pragma 是否满足合同要求。
    for dict_argument in spec.get("interfaces", {}).get("arguments", []):

        # 缺少结构化名称的参数无法参与 pragma 比对，这里直接跳过。
        if not isinstance(dict_argument, dict) or not dict_argument.get("name"):

            # 跳过结构不完整的参数条目，避免误报。
            continue

        # 把当前参数的 pragma 检查结果并入总问题列表。
        list_issues.extend(
            _argument_pragma_issues(
                dict_argument,
                dict_pragmas_by_port,
                spec,
            )
        )

    # 读取控制接口合同，后续检查 return 端口 pragma 是否匹配。
    str_control_interface = spec.get("interfaces", {}).get("control")  # 当前 HLS 规范声明的控制接口模式

    # 声明了控制接口时，必须在 return 端口看到匹配 pragma。
    if str_control_interface:

        # 取出 return 端口上的全部 pragma 供模式比对。
        list_return_pragmas = dict_pragmas_by_port.get("return", [])  # return 端口对应的 pragma 列表

        # 控制接口返回端口找不到匹配模式时，记录合同缺失问题。
        if not any(
            _canonical_hls_mode(dict_item.get("mode"))
            == _canonical_hls_mode(str_control_interface)
            for dict_item in list_return_pragmas
        ):

            # 把控制接口缺失 return pragma 的问题登记到结果列表。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "HLS control interface must include "
                    f"`{str_control_interface}` on `port=return`.",
                )
            )

    # 规范要求 pipeline 时，源码里至少要存在一处 PIPELINE pragma。
    if spec.get("pipeline_required", True) and not re.search(
        r"#pragma\s+HLS\s+PIPELINE\b",
        str_source_text,
    ):

        # 把未满足流水线合同的 kernel 记录成阻断问题，防止遗漏关键 pragma。
        list_issues.append(
            ValidationIssue(
                "error",
                "Pipeline-required HLS kernels must include at least one "
                "`#pragma HLS PIPELINE`.",
            )
        )

    # 把禁用 C++ 模式扫描结果并入总问题列表。
    list_issues.extend(_forbidden_cpp_issues(root, str_source_text))

    # 把 cfg 文件合同检查结果并入总问题列表。
    list_issues.extend(_cfg_issues(spec, root))

    # 返回 HLS 源码级基础合同检查结果。
    return list_issues

# 校验单个接口参数的 pragma 是否满足 interface、bundle 与 family 限制。
def _argument_pragma_issues(
    argument: dict[str, Any],
    pragmas_by_port: dict[str, list[dict[str, str]]],
    spec: dict[str, Any],
) -> list[ValidationIssue]:
    """检查单个参数的接口 pragma 是否符合合同。

    参数:
        argument: 当前参数的接口合同字典。
        pragmas_by_port: 按端口聚合后的 pragma 字典。
        spec: 当前 HLS 规范字典。

    返回:
        list[ValidationIssue]: 当前参数对应的 pragma 问题列表。
    """

    # 收集当前参数的 pragma 检查结果。
    list_issues: list[ValidationIssue] = []  # 单参数 pragma 检查的问题列表

    # 读取当前参数名，后续所有错误信息都围绕它展开。
    str_argument_name = str(argument["name"])  # 当前接口参数名称

    # 读取参数上显式声明的接口模式合同。
    str_explicit_interface = argument.get("interface")  # 当前参数要求的接口模式

    # 读取参数要求的 bundle 名称。
    str_bundle = argument.get("bundle")  # 当前参数要求使用的 bundle 名称

    # 找出当前参数端口上实际存在的 pragma 列表。
    list_matching_pragmas = pragmas_by_port.get(str_argument_name, [])  # 当前参数命中的 pragma 列表

    # 显式要求了接口模式时，必须看到对应 pragma。
    if str_explicit_interface:

        # 一个 pragma 都没找到时，直接记录缺失问题。
        if not list_matching_pragmas:

            # 把缺少接口 pragma 的参数问题登记到结果列表。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"HLS argument {str_argument_name!r} is missing the required "
                    f"{str_explicit_interface!r} interface pragma.",
                )
            )

        # 找到了 pragma 但模式不匹配时，提示实际看到的模式集合。
        elif not any(
            _canonical_hls_mode(dict_item.get("mode"))
            == _canonical_hls_mode(str_explicit_interface)
            for dict_item in list_matching_pragmas
        ):

            # 先准备当前参数实际命中的接口模式集合。
            set_found_modes: set[str] = set()  # 当前参数实际命中的接口模式集合

            # 逐条提取 pragma 里声明过的接口模式。
            for dict_item in list_matching_pragmas:

                # 只有真正声明了 mode 的 pragma 才参与模式集合统计。
                if dict_item.get("mode"):

                    # 把规范化后的接口模式加入集合，供错误消息汇总。
                    set_found_modes.add(_canonical_hls_mode(dict_item.get("mode")))

            # 把接口模式集合拼成可直接写入报错文本的稳定字符串。
            str_found_modes = ", ".join(sorted(set_found_modes)) or "none"  # 供接口模式 mismatch 报错复用的模式列表文本

            # 把接口模式不匹配的问题登记到结果列表。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"HLS argument {str_argument_name!r} must use interface mode "
                    f"{str_explicit_interface!r}, found {str_found_modes}.",
                )
            )

        # bundle 有明确要求时，还要逐条确认 pragma 的 bundle 值。
        if str_bundle and not any(
            str(dict_item.get("bundle") or "") == str(str_bundle)
            for dict_item in list_matching_pragmas
        ):

            # 把 bundle 不匹配的问题登记到结果列表。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"HLS argument {str_argument_name!r} must use bundle "
                    f"{str_bundle!r}.",
                )
            )

    # 没声明显式接口模式且完全没有 pragma 时，保留 warning 提醒补全。
    elif not list_matching_pragmas:

        # 把缺失接口 pragma 的告警登记到当前参数结果列表。
        list_issues.append(
            ValidationIssue(
                "warning",
                f"No HLS interface pragma was found for argument "
                f"{str_argument_name!r}.",
            )
        )

    # 汇总当前参数实际观察到的标准化接口模式集合。
    list_mode_values = [dict_item.get("mode") for dict_item in list_matching_pragmas if dict_item.get("mode")]  # 当前参数命中的原始接口模式值列表

    # 把原始接口模式值统一标准化，供接口族约束直接判断。
    set_observed_modes = {_canonical_hls_mode(str_mode_value) for str_mode_value in list_mode_values}  # 当前参数命中的标准化接口模式集合

    # interface_family 是 axi4 时，不允许误用 AXI-Stream pragma。
    if spec.get("interface_family") == "axi4" and "axis" in set_observed_modes:

        # 把 axi4 误用 AXI-Stream pragma 的问题登记到当前参数结果列表。
        list_issues.append(
            ValidationIssue(
                "error",
                f"HLS argument {str_argument_name!r} must not use AXI-Stream "
                "pragmas when spec.interface_family is axi4.",
            )
        )

    # interface_family 是 axi_stream 时，不允许混入 m_axi pragma。
    if spec.get("interface_family") == "axi_stream" and "m_axi" in set_observed_modes:

        # 把 axi_stream 混入 m_axi pragma 的问题登记到当前参数结果列表。
        list_issues.append(
            ValidationIssue(
                "error",
                f"HLS argument {str_argument_name!r} must not use m_axi pragmas "
                "when spec.interface_family is axi_stream.",
            )
        )

    # 返回当前参数的 pragma 检查结果。
    return list_issues

# 检查源码中是否出现不适合当前 Vitis HLS 流程的 C++ 结构。
def _forbidden_cpp_issues(root: Path, source_text: str) -> list[ValidationIssue]:
    """扫描 HLS 源码中的禁用 C++ 结构。

    参数:
        root: 当前验证产物树根目录；该函数当前只保留签名兼容性。
        source_text: 已合并的 HLS 源码文本。

    返回:
        list[ValidationIssue]: 命中禁用模式时生成的问题列表。
    """

    # 根目录参数仅用于保持现有函数签名兼容，这里显式标记未使用。
    del root

    # 固定定义当前 Vitis HLS 流程中要阻止的 C++ 模式。
    # 先定义常见动态容器与运行期对象的阻断提示文本。
    str_vector_message = (
        "std::vector is usually not synthesizable in "
        "Vitis HLS kernels."
    )  # std::vector 的阻断提示文本

    # 先定义其他动态容器与运行期对象的统一阻断提示文本。
    str_dynamic_type_message = (
        "Unsupported standard library container or dynamic type used in "
        "HLS kernel."
    )  # 动态容器与运行期对象的阻断提示文本

    # 把前面准备好的阻断文案绑定到具体禁用模式上。
    dict_banned_patterns = {
        r"\bstd::vector\b": str_vector_message,  # 匹配 std::vector 动态容器
        r"\bstd::(?:map|unordered_map|list|deque|string|function)\b": str_dynamic_type_message,  # 匹配其他动态容器与运行期对象
        r"\b(?:malloc|free)\s*\(": ("Dynamic memory allocation is not suitable for this Vitis HLS flow."),  # 匹配 malloc/free 动态内存接口
        r"\b(?:new|delete)\b": (  # 匹配 C++ new/delete 动态分配接口
            "C++ dynamic allocation is not suitable for this Vitis HLS flow."  # 命中堆分配关键字时返回的综合阻断文案
        ),
        r"\bthrow\b|\bcatch\s*\(": (  # 匹配异常抛出与捕获语义
            "Exceptions are not suitable for this Vitis HLS flow."  # 命中异常语义时返回的综合阻断文案
        ),
        r"\b[A-Za-z_][A-Za-z0-9_:<>]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\[[A-Za-z_][A-Za-z0-9_]*\]\s*;": (  # 匹配依赖运行期长度的栈数组声明
            "Variable-length stack arrays are not suitable for this Vitis HLS "  # 命中运行期长度数组声明时返回的综合阻断文案
            "flow."  # 延续上方禁用模式提示文本
        ),
    }  # 当前流程禁止出现的 C++ 模式与诊断文本映射表

    # 仅保留真正命中的禁用模式，并映射成统一问题对象。
    return [
        ValidationIssue("error", str_message)
        for str_pattern, str_message in dict_banned_patterns.items()
        if re.search(str_pattern, source_text)
    ]

# 读取主 cfg 文件并解析出后续合同检查需要的结构化上下文。
def _cfg_validation_context(root: Path) -> tuple[dict[str, Any], str] | None:
    """返回 cfg 合同检查要复用的解析结果与相对路径。

    参数:
        root: 当前验证产物树根目录。

    返回:
        tuple[dict[str, Any], str] | None: 命中主 cfg 时返回解析结果与相对路径，否则返回 None。
    """

    # 先定位产物树中的全部 cfg 文件。
    list_cfg_files = sorted(root.glob("**/*.cfg"))  # 当前产物树中的 cfg 文件列表

    # 缺少 cfg 时，当前函数返回空上下文让调用方生成统一错误。
    if not list_cfg_files:

        # 当前产物树中没有可用的主 cfg 文件。
        return None

    # 读取第一个 cfg 文件文本，当前流程约定它是主配置入口。
    str_cfg_text = list_cfg_files[0].read_text(encoding="utf-8", errors="ignore")  # 当前主 cfg 文件文本

    # 解析 cfg 文本中的结构化键值，供后续逐项对照。
    dict_cfg_entries = parse_hls_cfg_entries(str_cfg_text)  # 当前 cfg 文件解析出的结构化条目字典

    # 预先整理 cfg 文件相对路径，避免重复计算。
    str_cfg_rel_path = list_cfg_files[0].relative_to(root).as_posix()  # 主 cfg 文件的相对路径文本

    # 返回主 cfg 的解析结果与展示路径。
    return dict_cfg_entries, str_cfg_rel_path

# 把 cfg 解析阶段已经发现的语法或结构错误转换成 ValidationIssue。
def _cfg_parse_issues(
    dict_cfg_entries: dict[str, Any],
    str_cfg_rel_path: str,
) -> list[ValidationIssue]:
    """返回 cfg 解析阶段已经发现的问题列表。

    参数:
        dict_cfg_entries: 当前主 cfg 文件解析出的结构化条目字典。
        str_cfg_rel_path: 主 cfg 文件相对当前产物树根目录的展示路径。

    返回:
        list[ValidationIssue]: cfg 解析阶段已经发现的问题列表。
    """

    # 收集 cfg 解析阶段已经暴露出的错误。
    list_issues: list[ValidationIssue] = []  # cfg 解析阶段的问题列表

    # 把 cfg 解析阶段的错误逐条并入统一问题对象。
    for str_parse_error in dict_cfg_entries.get("parse_errors", []):

        # 把 cfg 解析阶段发现的错误逐条并入结果列表。
        list_issues.append(
            ValidationIssue(
                "error",
                str(str_parse_error),
                str_cfg_rel_path,
            )
        )

    # 返回 cfg 解析阶段的问题列表。
    return list_issues

# 提取 spec 中声明的 source、testbench 与全集输出路径，供 cfg 合同复用。
def _cfg_declared_outputs(
    spec: dict[str, Any],
) -> tuple[list[str], list[str], set[str]]:
    """返回 cfg 合同检查需要的三类 spec 输出集合。

    参数:
        spec: 当前 HLS 规范字典。

    返回:
        tuple[list[str], list[str], set[str]]: source 输出、testbench 输出和全集输出集合。
    """

    # 收集 spec 中声明的核心 source 输出路径。
    list_source_outputs = [
        output["path"]  # spec 中声明的核心综合输入路径
        for output in spec["outputs"]  # 逐个遍历 spec 输出条目
        if Path(str(output["path"])).suffix.lower() in {".cpp", ".cc", ".cxx", ".h", ".hpp"}  # 只保留 C/C++ 综合输入后缀
        and output.get("kind") != "testbench"  # 排除显式声明为 testbench 的输出
        and "_tb." not in str(output["path"]).lower()  # 排除命名上属于 testbench 的路径
    ]  # spec 中将被视为核心综合输入的源码路径列表

    # 收集 spec 中声明的 testbench 输出路径。
    list_testbench_outputs = [
        output["path"]  # spec 中声明的 testbench 路径
        for output in spec["outputs"]  # 遍历 spec 输出并筛出测试侧路径
        if Path(str(output["path"])).suffix.lower() in {".cpp", ".cc", ".cxx"}  # 只保留 testbench 允许的 C++ 文件
        and (output.get("kind") == "testbench" or "_tb." in str(output["path"]).lower())  # 接受显式 testbench 标记或 _tb 命名路径
    ]  # spec 中将被视为 testbench 的源码路径列表

    # 汇总 spec 中声明过的全部输出路径，供 cfg 条目越界判断复用。
    set_expected_outputs = {
        str(output["path"])  # 当前 spec 输出路径文本
        for output in spec["outputs"]  # 逐个遍历 spec 中声明的输出条目
    }  # spec 中声明的全部输出路径集合

    # 返回 cfg 合同检查要复用的三个输出集合。
    return list_source_outputs, list_testbench_outputs, set_expected_outputs

# 检查 cfg 的 syn.file 或 tb.file 条目是否越界、缺失或未声明。
def _cfg_file_reference_issues(
    list_observed_paths: list[str],
    *,
    root: Path,
    set_expected_outputs: set[str],
    str_cfg_rel_path: str,
    str_cfg_label: str,
) -> list[ValidationIssue]:
    """返回单类 cfg 文件引用条目的路径合同问题列表。

    参数:
        list_observed_paths: 当前 syn.file 或 tb.file 声明出的路径列表。
        root: 当前验证产物树根目录。
        set_expected_outputs: spec 中声明过的全部合法输出路径集合。
        str_cfg_rel_path: 主 cfg 文件相对当前产物树根目录的展示路径。
        str_cfg_label: 当前检查的 cfg 字段标签，如 syn.file 或 tb.file。

    返回:
        list[ValidationIssue]: 当前 cfg 字段命中的路径合同问题列表。
    """

    # 收集当前 syn.file 或 tb.file 条目命中的全部路径合同问题。
    list_issues: list[ValidationIssue] = []  # 当前 cfg 文件引用条目的问题列表

    # 逐条检查 cfg 引用是否使用合法相对路径并指向真实已声明文件。
    for str_observed_path in list_observed_paths:

        # 先让 cfg 路径审计器检查该路径是否越界或非法。
        str_path_issue = cfg_relative_path_issue(str_observed_path)  # 当前 cfg 引用路径的合法性问题

        # 路径本身不合规时，优先登记路径错误。
        if str_path_issue:

            # 记录 cfg 引用路径本身不合规的问题。
            list_issues.append(
                ValidationIssue(
                    "error",
                    str_path_issue,
                    str_cfg_rel_path,
                )
            )

            # 路径格式已判定非法，不再继续做存在性与声明边界检查。
            continue

        # 引用的文件不存在时，说明 cfg 指向了缺失产物。
        if not (root / str_observed_path).exists():

            # 记录 cfg 引用指向缺失文件的问题。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"HLS cfg {str_cfg_label} references missing file {str_observed_path!r}.",
                    str_cfg_rel_path,
                )
            )

            # 缺失文件已经足够说明条目失效，不再继续做声明边界检查。
            continue

        # 文件存在但未在 spec 中声明时，说明 cfg 越界引用了未声明产物。
        if str_observed_path not in set_expected_outputs:

            # 记录 cfg 越界引用未声明产物的问题。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"HLS cfg {str_cfg_label} references undeclared artifact {str_observed_path!r}.",
                    str_cfg_rel_path,
                )
            )

    # 返回当前 syn.file 或 tb.file 条目的路径问题列表。
    return list_issues

# 检查 spec 已声明输出是否都被 cfg 的 syn.file 或 tb.file 覆盖。
def _cfg_declared_output_mapping_issues(
    list_declared_paths: list[str],
    list_observed_paths: list[str],
    *,
    str_cfg_rel_path: str,
    str_cfg_label: str,
    str_output_label: str,
) -> list[ValidationIssue]:
    """返回 cfg 对 spec 已声明输出的缺失映射问题列表。

    参数:
        list_declared_paths: spec 中当前类别已经声明的输出路径列表。
        list_observed_paths: cfg 中对应字段当前实际声明的路径列表。
        str_cfg_rel_path: 主 cfg 文件相对当前产物树根目录的展示路径。
        str_cfg_label: 当前检查的 cfg 字段标签，如 syn.file 或 tb.file。
        str_output_label: 当前输出类别的人类可读标签。

    返回:
        list[ValidationIssue]: cfg 漏掉 spec 已声明输出时的问题列表。
    """

    # 收集 cfg 漏掉 spec 已声明输出时产生的问题。
    list_issues: list[ValidationIssue] = []  # cfg 声明覆盖缺口的问题列表

    # 反向检查 spec 声明的路径是否都已出现在 cfg 对应条目里。
    for str_declared_path in list_declared_paths:

        # spec 已声明但 cfg 漏掉对应条目时，记录配置不完整问题。
        if str_declared_path not in list_observed_paths:

            # 把 cfg 漏登记已声明输出的情况写入结果列表。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"HLS cfg must include {str_cfg_label} for declared {str_output_label} {str_declared_path!r}.",
                    str_cfg_rel_path,
                )
            )

    # 返回 cfg 对 spec 已声明输出的缺失映射问题列表。
    return list_issues

# 检查 cfg 的 clock 字段是否兑现 spec 中声明的时钟目标。
def _cfg_clock_issues(
    spec: dict[str, Any],
    dict_cfg_entries: dict[str, Any],
    str_cfg_rel_path: str,
) -> list[ValidationIssue]:
    """返回 cfg 与 spec 的时钟合同问题列表。

    参数:
        spec: 当前 HLS 规范字典。
        dict_cfg_entries: 当前主 cfg 文件解析出的结构化条目字典。
        str_cfg_rel_path: 主 cfg 文件相对当前产物树根目录的展示路径。

    返回:
        list[ValidationIssue]: cfg 与 spec 的时钟合同问题列表。
    """

    # 收集 clock 字段与 spec 时钟目标不一致时产生的问题。
    list_issues: list[ValidationIssue] = []  # cfg 时钟合同问题列表

    # 提取 spec 声明的时钟目标，后续对照 cfg 里的 clock 值。
    float_target_clock = _spec_hls_clock_period(spec)  # spec 中声明的目标时钟周期

    # 没有声明时钟目标时，不需要检查 cfg 中的 clock 字段。
    if float_target_clock is None:

        # 当前 spec 没有声明时钟目标，这里直接结束 clock 合同检查。
        return list_issues

    # 把 cfg 中的 clock 字段安全转换成浮点数。
    float_cfg_clock = _safe_float(dict_cfg_entries.get("clock"))  # cfg 中声明的时钟周期

    # 缺少 clock 时，说明 cfg 没有兑现 spec 的时钟合同。
    if float_cfg_clock is None:

        # 记录 cfg 缺少 clock 字段的问题。
        list_issues.append(
            ValidationIssue(
                "error",
                "HLS cfg must include `clock=` when spec.clock.period_ns is declared.",
                str_cfg_rel_path,
            )
        )

        # 缺少 clock 字段时，后续无需再做数值偏差比较。
        return list_issues

    # clock 与 spec 偏差超出容差时，记录时钟不一致问题。
    if abs(float_cfg_clock - float_target_clock) > 1e-6:

        # 记录 cfg 时钟与 spec 时钟不一致的问题。
        list_issues.append(
            ValidationIssue(
                "error",
                f"HLS cfg clock={float_cfg_clock} does not match spec.clock.period_ns={float_target_clock}.",
                str_cfg_rel_path,
            )
        )

    # 返回 cfg 与 spec 的时钟合同问题列表。
    return list_issues

# 检查 cfg 的 flow_target 与 part 是否与 spec 声明一致。
def _cfg_flow_and_part_issues(
    spec: dict[str, Any],
    dict_cfg_entries: dict[str, Any],
    str_cfg_rel_path: str,
) -> list[ValidationIssue]:
    """返回 cfg 的 flow_target 与 part 合同问题列表。

    参数:
        spec: 当前 HLS 规范字典。
        dict_cfg_entries: 当前主 cfg 文件解析出的结构化条目字典。
        str_cfg_rel_path: 主 cfg 文件相对当前产物树根目录的展示路径。

    返回:
        list[ValidationIssue]: cfg 的 flow_target 与 part 合同问题列表。
    """

    # 收集 flow_target 与 part 相关的不一致问题。
    list_issues: list[ValidationIssue] = []  # cfg flow_target/part 合同问题列表

    # 读取 spec 声明的 flow_target，统一转成小写后对比。
    str_expected_flow = str((spec.get("workflow") or {}).get("flow_target") or "").strip().lower()  # spec 中归一化后的 flow_target

    # 读取 cfg 实际声明的 flow_target，并统一转成可比较的小写文本。
    str_observed_flow = str(dict_cfg_entries.get("flow_target") or "").strip().lower()  # cfg 中归一化后的实际 flow_target

    # flow_target 存在但不在允许集合内时，记录非法取值问题。
    if str_observed_flow and str_observed_flow not in {"vivado", "vitis"}:

        # 记录 cfg flow_target 落在允许集合之外的问题。
        list_issues.append(
            ValidationIssue(
                "error",
                "HLS cfg flow_target must be `vivado` or `vitis`, found "
                f"{str_observed_flow!r}.",
                str_cfg_rel_path,
            )
        )

    # spec 和 cfg 都声明了 flow_target 且不一致时，记录合同不匹配。
    if (
        str_expected_flow
        and str_observed_flow
        and str_observed_flow != str_expected_flow
    ):

        # 记录 spec 与 cfg 的 flow_target 不一致问题。
        list_issues.append(
            ValidationIssue(
                "error",
                f"HLS cfg flow_target={str_observed_flow!r} does not match "
                f"spec.workflow.flow_target={str_expected_flow!r}.",
                str_cfg_rel_path,
            )
        )

    # 读取 spec 中声明的目标 part。
    str_expected_part = str((spec.get("workflow") or {}).get("part") or spec.get("part") or "").strip()  # spec 中归一化后的目标器件 part

    # 读取 cfg 实际声明的目标器件 part，供后续与 spec 逐字对照。
    str_observed_part = str(dict_cfg_entries.get("part") or "").strip()  # cfg 中归一化后的实际目标器件 part

    # spec 和 cfg 同时声明 part 且不一致时，记录目标器件不匹配。
    if (
        str_expected_part
        and str_observed_part
        and str_observed_part != str_expected_part
    ):

        # 记录 spec 与 cfg 的目标器件 part 不一致问题。
        list_issues.append(
            ValidationIssue(
                "error",
                f"HLS cfg part={str_observed_part!r} does not match spec workflow part={str_expected_part!r}.",
                str_cfg_rel_path,
            )
        )

    # 返回 cfg 的 flow_target 与 part 合同问题列表。
    return list_issues

# 检查 cfg 的 burst 长度字段是否兑现 spec 的 interface_profile 合同。
def _cfg_burst_issues(
    spec: dict[str, Any],
    dict_cfg_entries: dict[str, Any],
    str_cfg_rel_path: str,
) -> list[ValidationIssue]:
    """返回 cfg 的 burst 长度合同问题列表。

    参数:
        spec: 当前 HLS 规范字典。
        dict_cfg_entries: 当前主 cfg 文件解析出的结构化条目字典。
        str_cfg_rel_path: 主 cfg 文件相对当前产物树根目录的展示路径。

    返回:
        list[ValidationIssue]: cfg 的 burst 长度合同问题列表。
    """

    # 收集与 m_axi burst 长度兑现情况直接相关的不一致问题。
    list_issues: list[ValidationIssue] = []  # cfg burst 长度合同问题列表

    # 先读取 interface_profile 的原始值，保留 spec 原始声明形态。
    obj_interface_profile = spec.get("interface_profile")  # 当前 spec 声明的 interface_profile 原始值

    # 只保留字典形态的 interface_profile，避免后续字段访问落到非映射对象。
    dict_interface_profile = obj_interface_profile if isinstance(obj_interface_profile, dict) else {}  # 供字段读取使用的 interface_profile 字典

    # 只有 burst_support 明确开启时，才检查 burst 长度合同。
    if not dict_interface_profile.get("burst_support"):

        # 当前 spec 没有开启 burst_support，这里直接结束 burst 合同检查。
        return list_issues

    # 读取 spec 中要求的 burst 长度。
    value_expected_burst = dict_interface_profile.get("max_burst_len")  # spec 中声明的最大 burst 长度

    # 读取 cfg 中实际声明的 burst 长度字段。
    str_burst_key = "m_axi_max_read_burst_length"  # cfg 中记录最大 burst 长度的键名

    # 准备 interface 区块原始值，供后续决定优先读取哪一级配置。
    obj_interface_section = dict_cfg_entries.get("interface")  # cfg 中 interface 区块的原始值

    # 先按顶层键回退读取 burst 长度，兼容没有 interface 区块的 cfg。
    value_observed_burst = dict_cfg_entries.get(str_burst_key)  # cfg 顶层声明的最大 burst 长度

    # interface 区块确实是字典时，再用区块内显式声明覆盖回退值。
    if isinstance(obj_interface_section, dict):

        # 从 interface 区块读取最大 burst 长度。
        value_observed_burst = obj_interface_section.get(str_burst_key)  # interface 区块里的最大 burst 长度

    # spec 开启 burst_support 却没给出长度时，合同本身不完整。
    if value_expected_burst in (None, ""):

        # 把 burst_support 缺少长度声明的问题写入结果列表。
        list_issues.append(
            ValidationIssue(
                "error",
                "HLS spec enables burst_support but omits interface_profile.max_burst_len.",
                str_cfg_rel_path,
            )
        )

        # 缺少期望 burst 长度时，后续无需再继续比较 cfg 取值。
        return list_issues

    # cfg 的 burst 长度与 spec 不一致时，记录合同不匹配。
    if str(value_observed_burst or "") != str(value_expected_burst):

        # 把 cfg 与 spec 的 burst 长度不匹配登记为错误。
        list_issues.append(
            ValidationIssue(
                "error",
                f"HLS cfg burst length {value_observed_burst!r} does not match "
                f"spec.interface_profile.max_burst_len={value_expected_burst!r}.",
                str_cfg_rel_path,
            )
        )

    # 返回 cfg 的 burst 长度合同问题列表。
    return list_issues

# 检查 hls_config.cfg 与 spec 合同是否保持一致。
def _cfg_issues(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    """检查 hls_config.cfg 与 spec 合同是否保持一致。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。

    返回:
        list[ValidationIssue]: cfg 文件对应的问题列表。
    """

    # 读取主 cfg 的解析结果与展示路径，供后续全部子检查复用。
    tuple_cfg_context = _cfg_validation_context(root)  # 主 cfg 的解析结果与相对路径上下文

    # 缺少 cfg 时，后续所有 HLS 配置合同都无法被验证。
    if tuple_cfg_context is None:

        # 直接返回缺少主 cfg 文件的阻断问题。
        return [ValidationIssue("error", "No Vitis HLS .cfg file found.")]

    # 解包主 cfg 的解析结果与展示路径。
    dict_cfg_entries, str_cfg_rel_path = tuple_cfg_context  # cfg 合同检查要复用的解析结果与相对路径

    # 先吸收 cfg 解析阶段已经发现的语法或结构错误。
    list_issues = _cfg_parse_issues(dict_cfg_entries, str_cfg_rel_path)  # cfg 合同检查的问题列表

    # 读取 spec 声明的 top function 名称，用于 syn.top 对照。
    str_top = str(spec.get("interfaces", {}).get("top_function") or spec["name"])  # 当前 spec 要求的 top function 名称

    # cfg 中 syn.top 与 spec 不一致时，说明综合入口配置错误。
    if dict_cfg_entries.get("syn.top") != str_top:

        # 把综合入口名称不匹配的问题登记到结果列表。
        list_issues.append(
            ValidationIssue(
                "error",
                f"HLS cfg syn.top must be {str_top!r}.",
                str_cfg_rel_path,
            )
        )

    # 提取 spec 中声明的各类输出集合，供 cfg 引用与覆盖检查复用。
    tuple_declared_outputs = _cfg_declared_outputs(spec)  # cfg 合同检查需要的三类 spec 输出集合

    # 把三类 spec 输出集合拆回局部变量，便于后续按职责分段校验。
    list_source_outputs, list_testbench_outputs, set_expected_outputs = tuple_declared_outputs  # source、testbench 与全集输出集合

    # 读取 cfg 中 syn.files 的全部条目文本。
    list_syn_files = [str(item) for item in dict_cfg_entries.get("syn.files", [])]  # cfg 综合入口引用的 syn.file 字符串列表

    # 收集 cfg 中 tb.files 的全部条目文本，供 testbench 对照复用。
    list_tb_files = [str(item) for item in dict_cfg_entries.get("tb.files", [])]  # cfg 测试入口引用的 tb.file 字符串列表

    # 检查 syn.file 的路径合法性、存在性与 spec 声明边界。
    list_issues.extend(
        _cfg_file_reference_issues(
            list_syn_files,
            root=root,
            set_expected_outputs=set_expected_outputs,
            str_cfg_rel_path=str_cfg_rel_path,
            str_cfg_label="syn.file",
        )
    )

    # 再检查 tb.file 是否把测试入口约束在合法且已声明的边界内。
    list_issues.extend(
        _cfg_file_reference_issues(
            list_tb_files,
            root=root,
            set_expected_outputs=set_expected_outputs,
            str_cfg_rel_path=str_cfg_rel_path,
            str_cfg_label="tb.file",
        )
    )

    # 反向检查 spec 声明的 source 输出是否都进入了 syn.file。
    list_issues.extend(
        _cfg_declared_output_mapping_issues(
            list_source_outputs,
            list_syn_files,
            str_cfg_rel_path=str_cfg_rel_path,
            str_cfg_label="syn.file",
            str_output_label="source output",
        )
    )

    # 最后反查测试侧声明，确认每个 testbench 输出都已经映射进 tb.file。
    list_issues.extend(
        _cfg_declared_output_mapping_issues(
            list_testbench_outputs,
            list_tb_files,
            str_cfg_rel_path=str_cfg_rel_path,
            str_cfg_label="tb.file",
            str_output_label="testbench output",
        )
    )

    # 追加 clock 合同检查结果，确认 cfg 落实了 spec 的时钟目标。
    list_issues.extend(_cfg_clock_issues(spec, dict_cfg_entries, str_cfg_rel_path))

    # 再追加 flow_target 与 part 的合同检查结果。
    list_issues.extend(_cfg_flow_and_part_issues(spec, dict_cfg_entries, str_cfg_rel_path))

    # 最后追加 burst 长度合同检查结果。
    list_issues.extend(_cfg_burst_issues(spec, dict_cfg_entries, str_cfg_rel_path))

    # 返回 cfg 文件的全部合同检查结果。
    return list_issues

# 检查高级 HLS 头文件是否与当前 pattern 合同一致。
def _validate_advanced_library_alignment(
    spec: dict[str, Any],
    root: Path,
) -> list[ValidationIssue]:
    """校验高级 HLS 头文件与 pattern 合同的一致性。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。

    返回:
        list[ValidationIssue]: 高级头文件越界使用时的问题列表。
    """

    # 收集当前 pattern 合同允许使用的高级头文件集合。
    set_required_headers = set(required_pattern_headers(spec))  # pattern 合同允许引用的高级头文件集合

    # 解析当前 spec 对应的 pattern 名称，供错误信息展示。
    str_pattern_name = canonical_pattern_name(spec)  # 当前 spec 选择的 pattern 名称

    # 汇总高级头文件对齐检查过程中发现的问题。
    list_issues: list[ValidationIssue] = []  # 高级头文件对齐检查的问题列表

    # 逐个 HLS 文件检查是否引入了高级头文件。
    for path_file in _hls_files(root):

        # 仅对 C/C++ HLS 文件执行高级头文件扫描。
        if path_file.suffix.lower() not in {".cpp", ".cc", ".cxx", ".h", ".hpp"}:

            # 跳过 cfg 等非 C/C++ 文件，避免无意义扫描。
            continue

        # 读取当前 HLS 文件全文，供 include 检查复用。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 供 include 扫描使用的当前 HLS 文件全文

        # 统一生成相对路径文本，供高级头文件对齐诊断稳定定位。
        str_rel_path = path_file.relative_to(root).as_posix()  # 用于高级头文件对齐诊断定位的相对路径

        # 逐个高级头文件检查当前文件是否包含它。
        for str_header in ADVANCED_LIBRARY_HEADERS:

            # 文件未包含当前高级头文件时，直接检查下一个头文件。
            if (
                f"#include <{str_header}>" not in str_text
                and f'#include "{str_header}"' not in str_text
            ):

                # 跳过未出现的高级头文件，避免无效判断。
                continue

            # 当前高级头文件不在合同允许集合中时，记录越界使用问题。
            if str_header not in set_required_headers:

                # 把高级头文件越界使用的问题登记到结果列表。
                list_issues.append(
                    ValidationIssue(
                        "error",
                        f"Advanced HLS header {str_header!r} is not justified by "
                        "the selected pattern "
                        f"{str_pattern_name or 'none'!r}.",
                        str_rel_path,
                        "static",
                        "spec_issue",
                    )
                )

    # 返回高级头文件对齐检查结果。
    return list_issues

# 从合并源码文本中解析全部 `#pragma HLS INTERFACE` 条目。
def _parse_hls_interface_pragmas(source_text: str) -> list[dict[str, str]]:
    """解析源码中的 INTERFACE pragma 列表。

    参数:
        source_text: 已合并的 HLS 源码文本。

    返回:
        list[dict[str, str]]: 每条 INTERFACE pragma 的结构化字段列表。
    """

    # 汇总从源码中解析到的 INTERFACE pragma 条目。
    list_pragmas: list[dict[str, str]] = []  # INTERFACE pragma 的结构化条目列表

    # 逐行扫描源码，只处理包含 INTERFACE 关键字的 pragma。
    for str_line in source_text.splitlines():

        # 不是 INTERFACE pragma 时，直接跳过该行。
        if "#pragma HLS INTERFACE" not in str_line:

            # 跳过非 INTERFACE pragma 行，避免无意义解析。
            continue

        # 先确定当前 pragma 的接口模式，缺省时回退到语句头部模式。
        str_mode_value = _pragma_value(str_line, "mode") or _pragma_interface_mode(str_line)  # 当前 pragma 经回退后的接口模式

        # 为当前 pragma 生成接口合同记录，后续直接追加到 pragma 列表。
        dict_pragma = {
            "line": str_line.strip(),  # 原始 pragma 文本，供报告定位
            "mode": str_mode_value,  # 解析后的接口模式，供模式比对
            "port": _pragma_value(str_line, "port"),  # 端口名，供参数接口约束比对
            "bundle": _pragma_value(str_line, "bundle"),  # bundle 名称，供总线分组核对
        }

        # 把结构化 pragma 条目追加到结果列表。
        list_pragmas.append(dict_pragma)

    # 返回全部 INTERFACE pragma 的解析结果。
    return list_pragmas

# 从单行 pragma 文本中提取指定键的值。
def _pragma_value(line: str, key: str) -> str:
    """提取 pragma 指定键的值。

    参数:
        line: 单行 pragma 文本。
        key: 需要提取的键名。

    返回:
        str: 提取到的键值；不存在时返回空字符串。
    """

    # 使用固定正则抽取当前键的简单字面量值。
    match_key_value = re.search(rf"\b{re.escape(key)}\s*=\s*([A-Za-z0-9_]+)", line)  # 当前键对应的正则匹配结果

    # 返回命中的键值文本；没有命中时回退为空字符串。
    return match_key_value.group(1) if match_key_value else ""

# 从 `#pragma HLS INTERFACE` 语句头部提取缺省接口模式。
def _pragma_interface_mode(line: str) -> str:
    """提取 INTERFACE pragma 的默认模式字段。

    参数:
        line: 单行 INTERFACE pragma 文本。

    返回:
        str: 解析出的接口模式；不存在时返回空字符串。
    """

    # 从 pragma 头部提取紧随 INTERFACE 之后的接口模式标识。
    match_interface_mode = re.search(r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z0-9_]+)", line)  # INTERFACE pragma 头部模式的匹配结果

    # 返回命中的模式文本；未命中时回退为空字符串。
    return match_interface_mode.group(1) if match_interface_mode else ""

# 把 HLS 接口模式文本标准化成便于比较的统一形式。
def _canonical_hls_mode(mode: Any) -> str:
    """标准化 HLS 接口模式文本。

    参数:
        mode: 原始接口模式值。

    返回:
        str: 小写下划线风格的标准化模式文本。
    """

    # 统一去空白、转小写并把连字符替换成下划线。
    return str(mode or "").strip().lower().replace("-", "_")

# 兼容保留 parse_hls_cfg_entries 的轻量代理入口。
def _parse_hls_cfg_entries(cfg_text: str) -> dict[str, Any]:
    """代理调用 cfg 解析器。

    参数:
        cfg_text: cfg 文件完整文本。

    返回:
        dict[str, Any]: 解析后的 cfg 结构化字典。
    """

    # 直接复用现有 cfg 解析器，保持调用点统一。
    return parse_hls_cfg_entries(cfg_text)

# 从 spec 中安全读取目标时钟周期。
def _spec_hls_clock_period(spec: dict[str, Any]) -> float | None:
    """提取 spec 里的时钟周期设置。

    参数:
        spec: 当前 HLS 规范字典。

    返回:
        float | None: 解析出的时钟周期；缺失或无效时返回 None。
    """

    # 读取 spec 的 clock 子结构，后续检查 period_ns 是否存在。
    dict_clock = spec.get("clock")  # spec 中声明的 clock 子结构

    # clock 缺失或 period_ns 为空时，直接视作未声明时钟目标。
    if not isinstance(dict_clock, dict) or dict_clock.get("period_ns") in (None, ""):

        # 返回 None，表示该 spec 没有可比较的时钟目标。
        return None

    # 把 period_ns 安全转换成浮点数。
    return _safe_float(dict_clock.get("period_ns"))

# 安全把任意时钟文本或数值转换成浮点数。
def _safe_float(value: Any) -> float | None:
    """安全解析时钟数值。

    参数:
        value: 待解析的原始数值或文本。

    返回:
        float | None: 解析成功的浮点数；失败时返回 None。
    """

    # 复用现有时钟解析器，保持所有时钟值解析口径一致。
    return clock_period_ns(value)

# 扫描产物树中的 Vitis 专项规则违规项。
def _validate_vitis_rules(root: Path) -> list[ValidationIssue]:
    """运行 Vitis 规则扫描器并转换结果。

    参数:
        root: 当前验证产物树根目录。

    返回:
        list[ValidationIssue]: Vitis 规则扫描发现的问题列表。
    """

    # 汇总 Vitis 规则扫描过程中发现的问题。
    list_issues: list[ValidationIssue] = []  # Vitis 规则扫描的问题列表

    # 逐个 HLS 文件运行 Vitis 规则扫描器。
    for path_file in _hls_files(root):

        # 读取当前文件文本，供规则扫描器分析。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 当前 HLS 文件的完整源码文本

        # 准备相对路径文本，便于规则扫描器和报告复用。
        str_rel_path = path_file.relative_to(root).as_posix()  # 规则扫描问题写回报告时使用的相对路径

        # 根据文件名和后缀推断规则扫描器使用的语言标签。
        str_language = "testbench" if "_tb" in path_file.stem.lower() else path_file.suffix.lower().lstrip(".")  # 当前 HLS 文件对应的扫描语言标签

        # 对当前文件执行 Vitis 规则扫描。
        for dict_issue in scan_vitis_rule_violations(
            str_text,
            path=str_rel_path,
            language=str_language,
        ):

            # 把扫描器原始 issue 字典转成统一问题对象。
            list_issues.append(
                ValidationIssue(
                    str(dict_issue.get("severity", "warning")),
                    str(dict_issue.get("message", "Vitis HLS rule violation.")),
                    dict_issue.get("path"),
                    str(dict_issue.get("stage", "static")),
                    str(dict_issue.get("source", "current_module_issue")),
                )
            )

    # 返回 Vitis 规则扫描结果。
    return list_issues

# 检查实现阶段的估算时钟是否满足 spec 声明的时钟目标。
def _validate_hls_clock_goal(
    spec: dict[str, Any],
    metrics: dict[str, Any],
    readiness: str,
) -> list[ValidationIssue]:
    """对比实现阶段估算时钟与 spec 目标时钟。

    参数:
        spec: 当前 HLS 规范字典。
        metrics: 已聚合的验证指标字典。
        readiness: 当前验证允许执行到的 readiness 阶段。

    返回:
        list[ValidationIssue]: 时钟目标不满足时生成的问题列表。
    """

    # 提取 performance 合同比对要用的目标时钟周期。
    float_target_clock = _spec_hls_clock_period(spec)  # 目标时钟周期（ns）

    # 未声明目标时钟或尚未执行到 implement 时，无需比较时钟指标。
    if float_target_clock is None or not readiness_at_least(readiness, "implement"):

        # 返回空列表，表示当前没有可比较的时钟目标。
        return []

    # 读取 csynth 子指标，后续从中提取 estimated clock。
    dict_csynth_metrics = metrics.get("csynth") if isinstance(metrics.get("csynth"), dict) else {}  # metrics 中可供时钟比对使用的 csynth 子字典

    # 先取出 csynth 下 timing 字段的原始值，便于后续单独校验类型。
    obj_timing_metrics: object = dict_csynth_metrics.get("timing")  # csynth 下 timing 字段的原始值

    # 先判断 timing 字段是否已经是可复用的字典结构。
    bool_has_timing_metrics = isinstance(obj_timing_metrics, dict)  # timing 字段是否为有效字典

    # 默认回退到空字典，只有校验通过时才覆盖为真实 timing 子字典。
    dict_timing_metrics: dict[str, Any] = {}  # 供后续时钟比对读取的 timing 子字典

    # timing 字段类型正确时，直接复用这个子字典。
    if bool_has_timing_metrics:

        # 用通过类型校验的 timing 子字典覆盖默认空值。
        dict_timing_metrics = obj_timing_metrics  # 复用有效的 timing 子字典

    # 提取实现阶段估算出的时钟周期。
    value_estimated_clock = dict_timing_metrics.get("estimated_clock_period_ns")  # csynth 报告里的 estimated clock 数值

    # 估算时钟存在且超过目标时钟时，记录实现阶段未达标。
    if (
        value_estimated_clock is not None
        and float(value_estimated_clock) > float_target_clock
    ):

        # 直接返回超出目标时钟的阻断问题，避免继续构造空结果列表。
        return [
            ValidationIssue(
                "error",
                f"HLS estimated clock period {value_estimated_clock}ns exceeds "
                f"target {float_target_clock}ns.",
                stage="implement",
                source="toolchain_issue",
            )
        ]

    # 返回时钟目标检查结果。
    return []

# 检查 performance 合同在实现阶段是否获得可复核的综合指标。
def _validate_performance(
    spec: dict[str, Any],
    metrics: dict[str, Any],
    readiness: str,
) -> list[ValidationIssue]:
    """检查 performance 合同与综合指标之间的对应关系。

    参数:
        spec: 当前 HLS 规范字典。
        metrics: 已聚合的验证指标字典。
        readiness: 当前验证允许执行到的 readiness 阶段。

    返回:
        list[ValidationIssue]: performance 合同对应的问题或信息列表。
    """

    # 读取 spec 中的 performance 子结构；结构异常时回退为空字典。
    dict_performance = spec.get("performance") if isinstance(spec.get("performance"), dict) else {}  # spec 中声明的 performance 子字典

    # 没有 performance 合同或尚未到 implement 阶段时，无需继续检查。
    if not dict_performance or not readiness_at_least(readiness, "implement"):

        # 返回空列表，表示当前没有 performance 合同检查任务。
        return []

    # 缺少 csynth 指标时，说明 performance 合同还没有可复核依据。
    if not isinstance(metrics.get("csynth"), dict):

        # 直接返回缺少综合指标的 warning，提示 performance 合同暂时无法落地复核。
        return [
            ValidationIssue(
                "warning",
                "Performance constraints are present but no Vitis HLS synthesis "
                "metrics were found.",
                stage="implement",
                source="toolchain_issue",
            )
        ]

    # 找到 csynth 指标时，留下信息级提示说明已有复核依据。
    return [
        ValidationIssue(
            "info",
            "Performance constraints have collected HLS metrics for review.",
            stage="implement",
            source="toolchain_issue",
        )
    ]

# 检查 HLS 源码是否满足基础可审阅性要求与注释合同。
def _validate_hls_reviewability(
    spec: dict[str, Any],
    root: Path,
    comment_language: str,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """执行 HLS 可审阅性与注释合同检查。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。
        comment_language: 当前要求的注释语言代码。

    返回:
        tuple[list[ValidationIssue], dict[str, Any]]: 可审阅性问题列表与注释指标字典。
    """

    # 汇总可审阅性检查过程中发现的问题。
    list_issues: list[ValidationIssue] = []  # 注释语言与可审阅性合同累计的问题

    # 准备承接参与可审阅性检查的 HLS 文件路径。
    list_hls_files: list[Path] = []  # 参与可审阅性检查的 HLS 文件路径列表

    # 逐个检查 HLS 文件后缀，只保留 C/C++ 相关文件。
    for path_file in _hls_files(root):

        # 命中 C/C++ 后缀的文件才纳入可审阅性检查。
        if path_file.suffix.lower() in {".cpp", ".cc", ".cxx", ".h", ".hpp"}:

            # 把需要做注释治理的 HLS 文件加入待检查列表。
            list_hls_files.append(path_file)

    # 准备统一承接所有 HLS 文件里抽取出的注释文本。
    list_all_comments: list[str] = []  # 当前 HLS 文件集合提取出的全部注释文本

    # 逐个文件抽取注释文本，汇总成统一语言检查输入。
    for path_file in list_hls_files:

        # 读取当前 HLS 文件文本并拆成逐行注释抽取输入。
        list_source_lines = path_file.read_text(encoding="utf-8", errors="ignore").splitlines()  # 当前 HLS 文件的逐行文本

        # 把当前 HLS 文件抽出的注释文本追加到总列表。
        list_all_comments.extend(_all_comment_texts(list_source_lines))

    # 期望中文注释却完全没有中文字符时，记录 warning。
    if (
        comment_language == "zh"
        and list_all_comments
        and not any(_contains_cjk(str_comment) for str_comment in list_all_comments)
    ):

        # 把缺少中文注释的问题登记到可审阅性检查结果。
        list_issues.append(
            ValidationIssue(
                "warning",
                "Reviewability warning: expected Chinese comments, but no "
                "Chinese comment text was found.",
                stage="static",
            )
        )

    # 期望英文注释却出现中文字符时，记录 warning。
    if comment_language == "en" and any(
        _contains_cjk(str_comment)
        for str_comment in list_all_comments
    ):

        # 把出现中文注释的语言偏差登记到可审阅性检查结果。
        list_issues.append(
            ValidationIssue(
                "warning",
                "Reviewability warning: expected English comments, but Chinese "
                "comment text was found.",
                stage="static",
            )
        )

    # 优先用 interfaces.top_function 锁定注释合同目标，缺失时回退到 spec.name。
    str_top = str(spec.get("interfaces", {}).get("top_function") or spec["name"])  # 当前注释合同对应的 top function 名称

    # top function 已经出现在文件里但注释文本为空时，提醒补充说明性注释。
    if any(
        str_top in path_file.read_text(encoding="utf-8", errors="ignore")
        for path_file in list_hls_files
    ) and not list_all_comments:

        # 把完全缺少说明性注释的问题登记到可审阅性检查结果。
        list_issues.append(
            ValidationIssue(
                "warning",
                "Reviewability warning: HLS files should contain explanatory "
                "comments.",
                stage="static",
            )
        )

    # 运行 HLS 注释合同检查器，获取合同问题和指标。
    list_policy_issues, dict_policy_metrics = validate_hls_comment_policy(root, list_hls_files, top_function=str_top)  # HLS 注释合同检查器返回的问题与指标

    # 逐条把注释合同问题映射成统一问题对象。
    for issue_policy in list_policy_issues:

        # 把注释合同问题统一转换成 ValidationIssue 后加入结果列表。
        list_issues.append(
            ValidationIssue(
                "error",
                f"{issue_policy.message} "
                f"({issue_policy.path}:{issue_policy.line}: "
                f"{issue_policy.detail})",
                issue_policy.path,
                stage="static",
                source="comment_policy",
                detail=issue_policy.detail,
            )
        )

    # 返回可审阅性问题列表和注释指标字典。
    return list_issues, dict_policy_metrics

# 统计单个 HLS 文件的注释覆盖情况。
def _hls_comment_coverage(lines: list[str], rel_path: str) -> dict[str, Any]:
    """统计单个 HLS 文件的注释覆盖率。

    参数:
        lines: 待检查文件的逐行文本列表。
        rel_path: 当前文件的相对路径文本。

    返回:
        dict[str, Any]: 注释覆盖统计结果字典。
    """

    # 统计进入覆盖检查的代码行数量。
    int_checked = 0  # 当前文件被纳入覆盖统计的代码行数量

    # 统计已被同行或前置注释覆盖的代码行数量。
    int_covered = 0  # 当前文件被注释覆盖的代码行数量

    # 收集缺少注释覆盖的代码行记录。
    list_missing: list[dict[str, Any]] = []  # 当前文件缺少注释覆盖的代码行记录列表

    # 标记上一条注释是否应该覆盖下一条代码行。
    bool_pending_preceding_comment = False  # 前置注释是否仍在等待覆盖下一条代码行

    # 标记当前是否处于多行块注释内部。
    bool_in_block_comment = False  # 当前游标是否处于块注释内部

    # 逐行扫描文件文本，统计注释覆盖情况。
    for line_number, raw_line in enumerate(lines, start=1):

        # 去掉当前行首尾空白，便于后续判断注释和空行。
        str_stripped = raw_line.strip()  # 当前行去空白后的文本

        # 空行会打断“前置注释覆盖下一行”的语义。
        if not str_stripped:

            # 空行出现时，前置注释不再继续覆盖后续代码行。
            bool_pending_preceding_comment = False  # 空行会消费掉前置注释覆盖状态

            # 空行本身不参与统计，继续扫描下一行。
            continue

        # 处于块注释内部时，要先等到块注释结束再继续统计。
        if bool_in_block_comment:

            # 块注释尚未闭合时，继续跳过当前行。
            if "*/" not in str_stripped:

                # 当前行仍属于未闭合块注释，暂时不进入代码统计。
                continue

            # 块注释结束后，恢复到普通代码扫描状态。
            bool_in_block_comment = False  # 当前块注释已经闭合，恢复普通扫描状态

            # 取出块注释闭合后同一行剩余的代码片段。
            str_after_block = str_stripped.split("*/", 1)[1].strip()  # 当前块注释闭合后剩余的代码片段

            # 块注释之后没有代码时，让前置注释去覆盖下一条代码行。
            if not str_after_block:

                # 当前块注释行没有后续代码时，让该注释覆盖下一条代码行。
                bool_pending_preceding_comment = True  # 块注释可继续覆盖下一条代码行

                # 当前行没有可统计代码，继续处理下一行。
                continue

            # 用块注释后的代码片段继续后续覆盖统计。
            str_stripped = str_after_block  # 当前行只保留块注释闭合后的代码片段

        # 单行注释本身只会覆盖下一条代码行，不计入代码统计。
        if str_stripped.startswith("//"):

            # 单行注释会把覆盖资格留给下一条真实代码行。
            bool_pending_preceding_comment = True  # 单行注释可覆盖下一条代码行

            # 注释行自身不算代码，继续处理下一行。
            continue

        # 以块注释开头的行需要单独处理覆盖语义。
        if str_stripped.startswith("/*"):

            # 块注释没有在同一行闭合时，进入块注释内部状态。
            if "*/" not in str_stripped:

                # 后续多行仍属于同一块注释，先切换到块注释扫描模式。
                bool_in_block_comment = True  # 后续扫描进入块注释内部状态

                # 当前行暂不计入代码统计，继续处理下一行。
                continue

            # 取出同一行块注释闭合后的剩余代码片段。
            str_after_block = str_stripped.split("*/", 1)[1].strip()  # 单行块注释闭合后剩余的代码片段

            # 块注释之后没有代码时，交给下一条代码继承该注释。
            if not str_after_block:

                # 同行块注释后没有代码时，让下一条代码继承这段注释。
                bool_pending_preceding_comment = True  # 同行块注释可覆盖下一条代码行

                # 当前行没有实际代码片段，继续处理下一行。
                continue

            # 当前行包含代码，因此先累计已检查的代码片段数量。
            int_checked += 1  # 当前代码片段已经纳入检查统计

            # 当前行又被同行块注释直接覆盖，因此同步累计覆盖数量。
            int_covered += 1  # 当前代码片段已经被同行块注释覆盖

            # 当前行已经就地覆盖，不再向下一行透传前置注释。
            bool_pending_preceding_comment = False  # 当前行已就地覆盖，无需继续透传前置注释

            # 同行块注释场景已经统计完成，继续处理下一行。
            continue

        # 普通代码行进入覆盖统计。
        int_checked += 1  # 当前普通代码行已纳入检查统计

        # 同行注释或前置注释存在时，把当前代码行记为已覆盖。
        if _has_same_line_comment(raw_line) or bool_pending_preceding_comment:

            # 把当前已获得注释覆盖的普通代码行计入已覆盖数量。
            int_covered += 1  # 当前普通代码行满足注释覆盖条件

        # 否则把当前代码行登记到缺失列表。
        else:

            # 把缺少注释覆盖的代码行登记到缺失列表。
            list_missing.append(
                {
                    "path": rel_path,
                    "line": line_number,
                    "code": str_stripped,
                }
            )

        # 无论当前行是否覆盖成功，前置注释状态都在此处消费完毕。
        bool_pending_preceding_comment = False  # 当前代码行已经消费掉前置注释覆盖状态

    # 返回当前文件的注释覆盖统计结果。
    return {
        "file": rel_path,
        "checked_lines": int_checked,
        "covered_lines": int_covered,
        "missing_lines": list_missing,
    }

# 判断一行代码是否已经携带同一行注释。
def _has_same_line_comment(line: str) -> bool:
    """判断单行文本中是否包含同一行注释。

    参数:
        line: 单行源码文本。

    返回:
        bool: 包含同一行注释时返回 True，否则返回 False。
    """

    # 只要命中 `//`、`/*` 或 `*/` 之一，就视作当前行带有注释。
    return "//" in line or "/*" in line or "*/" in line

# 从逐行源码文本中提取所有注释正文。
def _all_comment_texts(lines: list[str]) -> list[str]:
    """提取源码中的全部注释文本。

    参数:
        lines: 待提取注释的逐行源码文本列表。

    返回:
        list[str]: 去空白后的注释正文列表。
    """

    # 收集从源码文本里提取出的注释正文。
    list_comments: list[str] = []  # 当前源码文本提取出的注释正文列表

    # 逐行提取 `//` 注释和单行块注释正文。
    for str_line in lines:

        # 命中 `//` 时，提取分隔符之后的注释正文。
        if "//" in str_line:

            # 把当前行的 `//` 注释正文加入注释文本列表。
            list_comments.append(str_line.split("//", 1)[1].strip())

        # 继续提取当前行中的所有单行块注释正文。
        list_comments.extend(
            match_comment.group(1).strip()
            for match_comment in re.finditer(r"/\*(.*?)\*/", str_line)
        )

    # 过滤掉空注释正文，返回有效注释文本列表。
    return [str_comment for str_comment in list_comments if str_comment]

# 判断一段文本里是否包含中日韩统一表意文字。
def _contains_cjk(text: str) -> bool:
    """检查文本中是否包含 CJK 字符。

    参数:
        text: 待检查的文本内容。

    返回:
        bool: 包含 CJK 字符时返回 True，否则返回 False。
    """

    # 命中 CJK 区间字符时，说明文本包含中文等表意文字。
    return bool(re.search(r"[\u4e00-\u9fff]", text))

# 按 readiness 上限决定是否以及如何运行外部 Vitis 工具链。
def _run_hls_readiness(
    spec: dict[str, Any],
    root: Path,
    readiness: str,
    run_external: bool,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """执行 readiness 阶段对应的外部工具链流程。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。
        readiness: 当前验证允许执行到的 readiness 阶段。
        run_external: 是否允许运行外部工具链。

    返回:
        tuple[list[ValidationIssue], dict[str, Any]]: 工具链执行问题与指标字典。
    """

    # static-only 模式下，不进入任何外部工具链阶段。
    if readiness == "static":

        # 直接返回未执行工具链的静态模式指标快照。
        return (
            [],
            {
                "toolchain": {
                    "executed": False,
                    "readiness": readiness,
                    "reason": "static_only",
                }
            },
        )

    # 调用方显式禁用外部执行时，直接返回阻断问题。
    if not run_external:

        # 直接返回外部执行被禁用的阻断问题与工具链状态。
        return (
            [
                ValidationIssue(
                    "error",
                    "External Vitis execution is disabled but "
                    f"{readiness!r} readiness was requested.",
                    stage=readiness,
                    source="toolchain_issue",
                )
            ],
            {
                "toolchain": {
                    "executed": False,
                    "readiness": readiness,
                    "reason": "external_disabled",
                }
            },
        )

    # 选择当前环境中第一个可用的 Vitis 工具定义。
    dict_tool = _select_vitis_tool()  # 当前环境中可用的 Vitis 工具定义

    # 找不到任何可用工具时，返回缺失工具的阻断问题。
    if dict_tool is None:

        # 拼接用户可见的工具名列表，帮助他们修复 PATH 环境。
        str_tool_names = " or ".join(f"`{str_name}`" for str_name in vitis_tool_names())  # 当前环境要求可用的 Vitis 工具名列表文本

        # 直接返回缺少 Vitis 工具的阻断问题与工具链状态。
        return (
            [
                ValidationIssue(
                    "error",
                    "Required AMD-Xilinx HLS tool not found on PATH. "
                    f"Install/source Vitis so {str_tool_names} is available.",
                    stage="compile",
                    source="toolchain_issue",
                    tool=missing_vitis_tool_id(),
                )
            ],
            {
                "toolchain": {
                    "executed": False,
                    "readiness": readiness,
                    "reason": "missing_vitis",
                }
            },
        )

    # 工具存在时，进入实际的 Vitis 工具链运行分支。
    return _run_vitis_tool(dict_tool, spec, root, readiness)

# 选择当前环境里第一个实际可执行的 Vitis 工具。
def _select_vitis_tool() -> dict[str, Any] | None:
    """从工具定义列表中选择可用的 Vitis 工具。

    参数:
        无额外业务参数。

    返回:
        dict[str, Any] | None: 命中的工具定义；没有可用工具时返回 None。
    """

    # 逐个检查候选工具是否真的能在当前 PATH 中找到。
    for dict_tool in vitis_tools():

        # 优先用 which 字段，其次回退到 name 字段匹配可执行文件。
        str_executable = str(dict_tool.get("which") or dict_tool.get("name") or "")  # 当前候选工具对应的可执行文件名

        # 命中 PATH 的第一个候选工具直接返回。
        if str_executable and shutil.which(str_executable):

            # 返回首个在 PATH 中可执行的工具候选。
            return dict_tool

    # 没有任何候选工具可用时，返回 None。
    return None

# 运行选中的 Vitis 工具并汇总其问题与指标。
def _run_vitis_tool(
    tool: dict[str, Any],
    spec: dict[str, Any],
    root: Path,
    readiness: str,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """执行实际的 Vitis Tcl 流程。

    参数:
        tool: 选中的 Vitis 工具定义字典。
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。
        readiness: 当前验证允许执行到的 readiness 阶段。

    返回:
        tuple[list[ValidationIssue], dict[str, Any]]: 工具执行问题与指标字典。
    """

    # 读取当前工具名称，供 metrics 和错误信息统一复用。
    str_tool_name = str(tool["name"])  # 当前选中的 Vitis 工具名称

    # 先定位第一份 cfg，作为 Tcl 渲染和工具执行的配置入口。
    path_cfg = _first_cfg(root)  # 当前产物树中的首个 cfg 文件路径

    # 没有 cfg 时，当前 readiness 阶段无法进入 Vitis 工具链。
    if path_cfg is None:

        # 直接返回缺 cfg 的工具链错误和未执行 metrics。
        return (
            [
                ValidationIssue(
                    "error",
                    "No Vitis HLS .cfg file found for required readiness.",
                    stage="compile",
                    source="toolchain_issue",
                    tool=str_tool_name,
                )
            ],
            {
                "toolchain": {
                    "executed": False,
                    "tool": str_tool_name,
                    "readiness": readiness,
                    "reason": "missing_cfg",
                }
            },
        )

    # 准备工具链基础 metrics，后续逐步补全 executed 和语义指标。
    dict_toolchain_metrics = {
        "executed": False,  # 标记当前工具链流程是否真的执行过外部工具
        "tool": str_tool_name,  # 记录本轮工具链选中的工具名
        "readiness": readiness,  # 记录本轮工具链执行所属的 readiness 阶段
    }  # 当前工具链 metrics 主体

    # 封装 toolchain metrics 外层字典，供后续合并 transcript 指标。
    dict_metrics: dict[str, Any] = {"toolchain": dict_toolchain_metrics}  # 当前 Vitis 工具执行阶段的基础 metrics

    # 渲染 Tcl 和临时工程目录；失败时直接返回 spec 级错误。
    try:

        # 为当前 readiness 渲染 Tcl 脚本并创建临时工程目录。
        tuple_tcl_artifacts = _write_vitis_hls_tcl(spec, root, path_cfg, readiness)  # 当前 Vitis 流程生成的 Tcl 脚本和工程目录元组

        # 取出 Tcl 脚本路径，供后续 vitis_command 直接引用。
        path_tcl = tuple_tcl_artifacts[0]  # 当前 Vitis 流程生成的 Tcl 脚本路径

        # 取出工程目录路径，供 finally 阶段统一清理。
        path_project_dir = tuple_tcl_artifacts[1]  # 当前 Vitis 流程生成的工程目录路径

    # Tcl 渲染失败时，说明 spec 与 cfg 合同本身不足以启动工具链。
    except ValueError as exc:

        # 直接返回 Tcl 渲染失败对应的 spec 级错误。
        return (
            [
                ValidationIssue(
                    "error",
                    str(exc),
                    stage="compile",
                    source="spec_issue",
                    tool=str_tool_name,
                )
            ],
            {},
        )

    # 进入实际工具运行阶段，并在 finally 中统一清理临时文件。
    try:

        # 根据 readiness 计算本轮工具执行对应的阶段标签。
        str_stage = _tool_stage_for(readiness)  # 当前工具执行的阶段标签

        # 计算用户可见的工具流程标签，优先使用显式 label。
        str_tool_label = str(tool.get("label") or f"{str_tool_name} Tcl flow")  # 当前工具执行在报告中显示的标签文本

        # 生成当前 Tcl 流程的完整工具命令。
        list_command = vitis_command(tool, tcl=path_tcl)  # 当前 Vitis 工具流程的完整命令行参数列表

        # 运行外部工具，并收集问题列表与原始输出文本。
        int_timeout = _tool_timeout_for(readiness)  # 当前工具执行的超时秒数

        # 运行工具并保留问题与输出的原始解包结果。
        tuple_tool_result = _run_tool(list_command, root, str_tool_label, str_stage, timeout=int_timeout)  # 当前工具执行返回的问题列表与输出文本元组

        # 取出工具执行返回的问题列表。
        list_issues = tuple_tool_result[0]  # 当前工具执行返回的问题列表

        # 取出工具执行返回的原始输出文本。
        str_output = tuple_tool_result[1]  # 当前工具执行返回的原始输出文本

        # 标记工具链阶段已经实际执行过。
        dict_metrics["toolchain"]["executed"] = True  # 工具链执行标记置为真

        # 从工具输出中提取可用的语义 transcript 指标。
        _merge_semantic_metrics(dict_metrics, str_output)

        # 返回工具执行的问题列表与聚合后的 metrics。
        return list_issues, dict_metrics

    # 无论成功还是失败，都清理 Tcl 脚本和临时工程目录。
    finally:

        # 删除临时 Tcl 与工程目录，避免污染工作树。
        _cleanup_vitis_temp(path_tcl, path_project_dir)

# 渲染本轮 Vitis 流程需要的 Tcl 文件和工程目录。
def _write_vitis_hls_tcl(
    spec: dict[str, Any],
    root: Path,
    cfg: Path,
    readiness: str,
) -> tuple[Path, Path]:
    """生成本轮 Vitis Tcl 文件与工程目录。

    参数:
        spec: 当前 HLS 规范字典。
        root: 当前验证产物树根目录。
        cfg: 当前流程使用的 cfg 文件路径。
        readiness: 当前验证允许执行到的 readiness 阶段。

    返回:
        tuple[Path, Path]: 生成的 Tcl 文件路径与工程目录路径。
    """

    # 先把 cfg 文件全文读入内存，再交给结构化解析器。
    str_cfg_text = cfg.read_text(encoding="utf-8", errors="ignore")  # 当前 cfg 文件全文

    # 解析 cfg 文本，得到 Tcl 渲染所需的结构化条目。
    dict_cfg_entries = parse_hls_cfg_entries(str_cfg_text)  # 供 Tcl 渲染阶段直接消费的 cfg 条目字典

    # 读取 Tcl 渲染配置，保证临时文件命名和流程约束一致。
    dict_tcl_config = vitis_tcl_config()  # 当前 Vitis Tcl 渲染配置字典

    # 先渲染 Tcl 文本与工程目录的组合结果，后续再逐项取出。
    tuple_render_result = render_vitis_hls_tcl(  # Tcl 渲染器生成的 Tcl 文本与工程目录路径元组
        spec,  # 当前 HLS 规范字典
        root,  # 让 Tcl 渲染器按当前产物树定位工程输入与输出
        dict_cfg_entries,  # 供 Tcl 渲染阶段消费的 cfg 条目
        readiness,  # 当前执行阶段上限
        dict_tcl_config,  # Tcl 渲染器使用的配置字典
    )

    # 取出渲染后的 Tcl 文本，供临时文件落盘。
    str_tcl_text = tuple_render_result[0]  # Tcl 渲染器生成的 Tcl 文本

    # 取出渲染后的工程目录路径，供返回和清理阶段复用。
    path_project_dir = tuple_render_result[1]  # Tcl 渲染器生成的工程目录路径

    # 创建临时 Tcl 文件，供后续 Vitis 命令直接引用。
    file_tcl_handle = tempfile.NamedTemporaryFile(  # 当前生成的临时 Tcl 文件句柄
        "w",  # 以文本写入模式生成 Tcl 临时文件
        suffix=".tcl",  # 临时文件后缀固定为 Tcl
        prefix=dict_tcl_config["temp_tcl_prefix"],  # 临时文件名前缀沿用配置约定
        dir=root,  # 把临时 Tcl 文件放到当前产物树根目录
        delete=False,  # 进程结束后仍保留文件，供外部工具读取
        encoding="utf-8",  # 临时 Tcl 文件统一按 UTF-8 写入
    )

    # 把渲染好的 Tcl 文本落盘到临时文件。
    with file_tcl_handle:

        # 把渲染后的 Tcl 文本写入临时文件。
        file_tcl_handle.write(str_tcl_text)

    # 返回落盘后的 Tcl 文件路径和工程目录路径。
    return Path(file_tcl_handle.name), path_project_dir

# 清理本轮 Vitis Tcl 流程生成的临时脚本和工程目录。
def _cleanup_vitis_temp(tcl: Path, project_dir: Path) -> None:
    """删除临时 Tcl 文件和工程目录。

    参数:
        tcl: 临时 Tcl 文件路径。
        project_dir: 临时工程目录路径。

    返回:
        None: 仅执行清理副作用。
    """

    # 删除本轮生成的 Tcl 脚本文件。
    tcl.unlink(missing_ok=True)

    # 递归删除临时工程目录，避免污染工作树。
    shutil.rmtree(project_dir, ignore_errors=True)

# 根据 readiness 选择本轮工具执行要标注的阶段名。
def _tool_stage_for(readiness: str) -> str:
    """推导工具执行对应的阶段标签。

    参数:
        readiness: 当前验证允许执行到的 readiness 阶段。

    返回:
        str: 对应的工具执行阶段标签。
    """

    # readiness 覆盖 cosim 时，优先标记为 cosim 阶段。
    if readiness_at_least(readiness, "cosim"):

        # 把工具阶段标签直接定格为 cosim。
        return "cosim"

    # readiness 覆盖 implement 时，标记为 implement 阶段。
    if readiness_at_least(readiness, "implement"):

        # 把工具阶段标签降落到 implement。
        return "implement"

    # 若已经进入 execute 但尚未达到 implement，则阶段标签落在 execute。
    if readiness_at_least(readiness, "execute"):

        # 把工具阶段标签固定为 execute。
        return "execute"

    # 其余情况统一回退到 compile 阶段。
    # 默认把工具阶段标签回退到 compile。
    return "compile"

# 根据 readiness 选择当前工具执行应使用的超时时间。
def _tool_timeout_for(readiness: str) -> int:
    """推导工具执行的超时秒数。

    参数:
        readiness: 当前验证允许执行到的 readiness 阶段。

    返回:
        int: 对应阶段的超时秒数。
    """

    # cosim 阶段通常最慢，优先使用 cosim 超时配置。
    if readiness_at_least(readiness, "cosim"):

        # cosim 会跑到最完整的联合验证阶段，因此采用最高档超时预算。
        return vitis_tool_timeout("cosim")

    # implement 阶段次之，使用 implement 超时配置。
    if readiness_at_least(readiness, "implement"):

        # implement 需要等待综合与估时结果，因此使用次一级超时预算。
        return vitis_tool_timeout("implement")

    # execute 阶段使用 execute 超时配置。
    if readiness_at_least(readiness, "execute"):

        # execute 只跑执行阶段脚本，因此沿用 execute 档超时预算。
        return vitis_tool_timeout("execute")

    # 其余情况回退到 compile 阶段超时配置。
    # compile 场景只需基础工具链超时预算，这里回退到最短档配置。
    return vitis_tool_timeout("compile")

# 把普通字符串安全包成 Tcl 花括号字面量。
def _tcl_quote(value: str) -> str:
    """生成 Tcl 花括号字符串字面量。

    参数:
        value: 原始字符串文本。

    返回:
        str: 适合嵌入 Tcl 的花括号包裹字符串。
    """

    # 把右花括号转义后，再整体包进 Tcl 花括号字面量。
    return "{" + value.replace("}", "\\}") + "}"

# 返回产物树中的第一份 cfg 文件路径。
def _first_cfg(root: Path) -> Path | None:
    """定位当前产物树中的第一份 cfg 文件。

    参数:
        root: 当前验证产物树根目录。

    返回:
        Path | None: 命中的第一份 cfg 文件路径；不存在时返回 None。
    """

    # 直接返回按字典序排序后的第一份 cfg 文件。
    return next(iter(sorted(root.glob("**/*.cfg"))), None)

# 运行单次外部工具命令并转换成统一问题对象。
def _run_tool(
    command: list[str],
    cwd: Path,
    label: str,
    stage: str,
    *,
    timeout: int,
) -> tuple[list[ValidationIssue], str]:
    """执行单次外部工具命令。

    参数:
        command: 完整命令行参数列表。
        cwd: 命令执行目录。
        label: 用户可见的工具流程标签。
        stage: 当前问题归属的阶段标签。
        timeout: 命令超时时间，单位秒。

    返回:
        tuple[list[ValidationIssue], str]: 命令对应的问题列表与输出文本。
    """

    # 尝试执行外部命令，并抓取 stdout/stderr。
    try:

        # 运行外部命令，保留完整输出供后续错误摘要使用。
        completed_process_result: subprocess.CompletedProcess[str] = subprocess.run(  # 单次外部工具命令的执行结果
            command,  # 当前要执行的外部命令参数序列
            cwd=cwd,  # 当前命令对应的工作目录
            capture_output=True,  # 同时捕获 stdout 与 stderr
            text=True,  # 以文本模式接收工具输出
            timeout=timeout,  # 超时时间沿用调用方配置
            check=False,  # 由后续分支统一把失败映射成 ValidationIssue
        )

    # 命令超时时，返回工具链超时错误。
    except subprocess.TimeoutExpired:

        # 直接返回超时错误与空输出，避免继续读取不存在的进程结果。
        return (
            [
                ValidationIssue(
                    "error",
                    f"{label} timed out after {timeout}s.",
                    stage=stage,
                    source="toolchain_issue",
                    tool=command[0],
                )
            ],
            "",
        )

    # 命令无法启动时，返回底层 OSError 摘要。
    except OSError as exc:

        # 直接返回启动失败错误与空输出，提示底层环境问题。
        return (
            [
                ValidationIssue(
                    "error",
                    f"{label} failed to start: {exc}",
                    stage=stage,
                    source="toolchain_issue",
                    tool=command[0],
                )
            ],
            "",
        )

    # 合并 stdout 与 stderr，形成统一输出文本。
    str_output = (completed_process_result.stdout + "\n" + completed_process_result.stderr).strip()  # 当前外部命令合并后的完整输出文本

    # 命令返回码非零时，截取首行作为错误摘要。
    if completed_process_result.returncode != 0:

        # 优先使用输出首行；没有输出时回退到 exit code 提示。
        str_detail = str_output.splitlines()[0] if str_output else f"exit code {completed_process_result.returncode}"  # 当前失败命令用于摘要展示的首行细节

        # 直接返回失败命令的统一错误对象与合并输出文本。
        return (
            [
                ValidationIssue(
                    "error",
                    f"{label} failed: {str_detail}",
                    stage=stage,
                    source="current_module_issue",
                    tool=command[0],
                    detail=_short_output(str_output),
                )
            ],
            str_output,
        )

    # 命令成功时，留下信息级问题记录，便于报告工具执行状态。
    return (
        [
            ValidationIssue(
                "info",
                f"{label} completed successfully.",
                stage=stage,
                source="toolchain_issue",
                tool=command[0],
                detail=_short_output(str_output),
            )
        ],
        str_output,
    )

# 从工具输出里提取语义 transcript 指标。
def _merge_semantic_metrics(metrics: dict[str, Any], output: str) -> None:
    """把工具输出中的语义 transcript 合并到 metrics。

    参数:
        metrics: 当前聚合中的指标字典。
        output: 外部工具合并后的输出文本。

    返回:
        None: 仅更新 metrics 字典。
    """

    # 旧 Python reference transcript 对照链已移除，当前 helper 保持空操作以兼容工具链调用点。
    del metrics
    del output

# 当前实现暂不从问题列表反推语义执行摘要，保留兼容入口。
def _semantic_execution_from_issues(
    issues: list[ValidationIssue],
) -> dict[str, Any] | None:
    """兼容保留的语义执行摘要入口。

    参数:
        issues: 当前聚合的问题列表。

    返回:
        dict[str, Any] | None: 当前实现始终返回 None。
    """

    # 当前实现暂不使用问题列表反推语义执行摘要。
    del issues

    # 返回 None，表示没有从问题列表恢复出额外语义指标。
    return None

# 裁剪外部工具输出，避免超长文本直接进入报告。
def _short_output(text: str, *, limit: int = 20000) -> str:
    """裁剪并清洗外部工具输出文本。

    参数:
        text: 原始输出文本。
        limit: 允许保留的最大字符数。

    返回:
        str: 裁剪后的输出摘要文本。
    """

    # 去掉回车并按上限裁剪，保持报告输出紧凑稳定。
    return text.strip().replace("\r", "")[:limit]
