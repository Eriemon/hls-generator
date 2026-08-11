"""检查 HLS C/C++ 注释语言、位置和硬件意图。"""
# 延迟解析类型注解，保持 Python 3.10+ 运行兼容。
from __future__ import annotations

# 标准库用于相似度、正则判断、路径定位和通用报告载荷。
import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 词法辅助函数提供行级 HLS/C++ 语句和注释识别能力。
from .cpp_lexer import (
    code_part,
    contains_cjk,
    extract_comments,
    has_blank_plus_chinese_comment_above,

    # 行内注释和相邻注释用于声明、赋值、pragma 的上下文判断。
    has_inline_comment,
    immediate_preceding_comment,
    inline_comment_text,
    is_assignment,
    is_comment_only,

    # 语句类型识别用于区分函数签名、pragma、loop 和局部声明。
    is_function_signature,
    is_hls_pragma,
    is_local_declaration,
    is_loop,
    next_meaningful_code_index,
    normalize_comment_text,

    # 上下文定位和特殊语句识别用于空行块、控制语句规则。
    parse_functions,
    previous_meaningful_code_index,
    special_statement_kind,
    statement_infos,
)

# profile 配置决定本轮 HLS 注释规则是否启用。
from .profiles import HlsProfileConfig

# 报告对象保持 HLS readability gate 的稳定 JSON 形状。
from .report import HlsGateIssue, make_issue

# StatementContext 收拢单条 HLS/C++ 语句的行级上下文。
@dataclass(frozen=True)
class StatementContext:
    """保存语句级注释检查需要的上下文字段。

    Args:
        lines: 当前文件的源码物理行。
        rel_path: 报告中使用的文件相对路径。
        line_number: 当前语句的一基行号。
        line_index: 当前语句的零基下标。
        depth: 当前语句在 C/C++ 代码块中的缩进深度。
        code: 当前语句的有效代码片段。
        preceding_comment: 紧邻当前语句上方的注释正文。
        statement_kind: 当前语句的特殊语句类别。

    Returns:
        数据类实例本身不返回业务值。
    """

    # lines 保留源码行，供注释位置和行尾注释检查复用。
    lines: list[str]  # 源码行列表

    # rel_path 保留报告路径，避免 helper 重复传散字段。
    rel_path: str  # 报告相对路径

    # line_number 用于生成一基行号诊断。
    line_number: int  # 语句一基行号

    # line_index 用于访问源码列表中的当前行。
    line_index: int  # 语句零基下标

    # depth 用于区分顶层声明和函数体内局部状态。
    depth: int  # C/C++ 代码块深度

    # code 是去掉注释后的有效 C/C++ 语句。
    code: str  # 有效语句代码

    # preceding_comment 是紧邻上方的注释正文。
    preceding_comment: str | None  # 上方注释正文

    # statement_kind 标记 if、for、return 等特殊语句类别。
    statement_kind: str | None  # 特殊语句类别

# PragmaIntentSpec 描述某类 pragma 必须具备的注释关键词。
@dataclass(frozen=True)
class PragmaIntentSpec:
    """保存 pragma 类型触发词和必需意图词。

    Args:
        trigger_terms: 用于识别 pragma 类型的关键词。
        required_terms: 当前 pragma 类型要求出现在注释里的关键词。
        rule: 缺少关键词时使用的 HLS 规则编号。
        message: 缺少关键词时展示给用户的诊断消息。

    Returns:
        数据类实例本身不返回业务值。
    """

    # trigger_terms 决定当前 spec 是否适用于某条 pragma。
    trigger_terms: tuple[str, ...]  # pragma 类型触发词

    # required_terms 决定注释是否覆盖该 pragma 类型的硬件意图。
    required_terms: tuple[str, ...]  # 必需意图关键词

    # rule 保留 HLS readability gate 的稳定规则编号。
    rule: str  # 诊断规则编号

    # message 是缺失意图词时的用户可读诊断。
    message: str  # 诊断消息

# PragmaContext 收拢单条 pragma 的代码和注释上下文。
@dataclass(frozen=True)
class PragmaContext:
    """保存 pragma 意图检查需要的上下文字段。

    Args:
        rel_path: 报告中使用的文件相对路径。
        line: 当前 pragma 的一基行号。
        code: 当前 pragma 的有效代码片段。
        comment: 紧邻 pragma 上方的注释正文。
        lowered_code: 小写后的 pragma 代码。
        lowered_comment: 小写后的 pragma 注释。

    Returns:
        数据类实例本身不返回业务值。
    """

    # rel_path 用于生成稳定报告路径。
    rel_path: str  # issue.filepath 使用的 POSIX 相对路径

    # line 用于定位 pragma 所在源码行。
    line: int  # pragma 一基行号

    # code 保留原始 pragma 指令文本用于诊断摘录。
    code: str  # 原始 pragma 指令文本

    # comment 保留 pragma 上方说明以定位缺失的硬件意图词。
    comment: str  # pragma 硬件意图说明

    # lowered_code 用于大小写无关的 pragma 类型识别。
    lowered_code: str  # 小写 pragma 代码

    # lowered_comment 用于大小写无关的关键词匹配。
    lowered_comment: str  # pragma 意图匹配使用的小写注释文本

# 文件头关键词用于确认注释描述了 HLS 文件角色。
FILE_HEADER_KEYWORDS = (  # 允许文件头注释命中的文件角色关键词
    "核心对象", "top function", "kernel", "testbench",  # 顶层对象说明词
    "文件", "头文件", "源码", "测试",  # 通用文件形态角色词
    "内核", "接口", "声明", "实现", "验证", "配置",  # 实现、验证与配置类角色词
)

# 文件头 contract 必须显式包含这些固定字段。
FILE_HEADER_REQUIRED_FIELDS = ("职责：", "输入/输出：", "打印协议：")  # 文件头 contract 固定字段

# 文件头还需要明确写出核心对象或 top function。
FILE_HEADER_CORE_OBJECT_TERMS = ("核心对象：", "top function")  # 文件头核心对象字段

# pragma 关键词用于判断注释是否解释硬件或吞吐意图。
PRAGMA_INTENT_KEYWORDS = (  # 通用 pragma 注释允许覆盖的硬件/吞吐语义词
    "接口", "端口", "协议", "bundle", "axi",  # 接口与总线形态词
    "axis", "m_axi", "s_axilite", "控制",  # 流接口、访存接口与控制接口词
    "流水", "ii", "周期", "吞吐", "dataflow",  # 时序与吞吐目标词
    "阶段", "stream", "通道", "fifo", "维度",  # 数据流阶段、缓冲与维度词
    "因子", "factor", "depth", "缓存", "分组", "并行", "硬件",  # 并行化与本地存储结构词
)

# 循环关键词用于确认 loop 注释是否描述迭代边界或数据事务。
LOOP_INTENT_KEYWORDS = (  # loop 注释允许覆盖的迭代范围和数据事务词
    "循环", "遍历", "范围", "边界", "长度",  # 迭代空间与边界词
    "事务", "样本", "token", "读", "写",  # 数据事务与读写方向词
    "输入", "输出", "累加", "比较", "检查",  # 数据处理与校验动作词
    "ii", "tripcount", "吞吐", "索引",  # 调度约束与索引控制词
)

# 非中文工具注释前缀保留给 lint、format 和版权类声明。
ALLOWED_NON_CHINESE_PREFIXES = (  # 允许原样保留的工具类非中文注释前缀
    "nolint", "noqa", "type:", "pragma:",  # lint、类型检查与 pragma 保留前缀
    "fmt:", "license", "copyright",  # 格式化与版权声明前缀
    "clang-format", "iwyu pragma",  # C/C++ 专用工具控制前缀
)

# INTERFACE pragma 需要说明接口形态或控制绑定。
INTERFACE_PRAGMA_TERMS = (  # INTERFACE pragma 必须覆盖的接口形态关键词
    "port", "bundle", "protocol", "axi",  # 端口命名、分组与总线协议词
    "axis", "m_axi", "s_axilite", "control",  # AXIS 流口、AXI 访存口与控制口词
    "端口", "协议", "接口", "控制",  # 中文接口说明词
)

# PIPELINE pragma 需要说明 II、延迟或循环吞吐目标。
PIPELINE_PRAGMA_TERMS = (  # PIPELINE pragma 必须覆盖的时序或吞吐关键词
    "ii", "initiation", "latency", "tripcount",  # II、延迟与 tripcount 词
    "loop", "stage", "cycle", "throughput",  # 循环阶段、周期与吞吐词
    "迭代", "流水", "循环", "周期", "吞吐",  # 中文时序与吞吐说明词
)

# DATAFLOW pragma 需要说明阶段、通道或生产消费关系。
DATAFLOW_PRAGMA_TERMS = (  # DATAFLOW pragma 必须覆盖的阶段或通道关键词
    "stage", "channel", "stream", "fifo",  # 阶段划分与通道缓冲词
    "producer", "consumer", "阶段", "通道",  # 生产消费角色与中文阶段词
    "流", "重叠", "生产", "消费",  # 中文数据流与重叠执行说明词
)

# 数组 pragma 需要说明维度、因子、bank 或缓存并行目的。
ARRAY_PRAGMA_TERMS = (  # ARRAY_PARTITION/RESHAPE 注释必须覆盖的维度或并行化词
    "factor", "dim", "dimension", "bank",  # 因子、维度与 bank 划分词
    "lane", "buffer", "维度",  # lane 并行与缓存类说明词
    "因子", "缓存", "分组",  # 中文并行化与缓存说明词
)

# STREAM pragma 需要说明 FIFO 深度或通道缓冲关系。
STREAM_PRAGMA_TERMS = (  # STREAM pragma 必须覆盖的 FIFO 或生产消费关键词
    "depth", "fifo", "stream", "producer",  # FIFO 深度、通道与生产端词
    "consumer", "深度", "通道",  # 消费端与中文深度/通道词
    "缓冲", "生产", "消费",  # 中文缓冲与生产消费说明词
)

# pragma 细分规则表让类型检查逻辑保持数据驱动。
PRAGMA_INTENT_SPECS = (  # 各类 pragma 专属意图规则映射表
    PragmaIntentSpec(  # INTERFACE pragma 关键词要求
        trigger_terms=("interface",),  # 命中 INTERFACE pragma 时启用本条规则
        required_terms=INTERFACE_PRAGMA_TERMS,  # 注释必须覆盖的接口类关键词
        rule="HG009",  # 缺失接口意图时使用的规则编号
        message="INTERFACE pragma 注释必须说明端口、协议、bundle 或控制接口意图。",  # 接口意图缺失提示
    ),
    PragmaIntentSpec(  # PIPELINE pragma 关键词要求
        trigger_terms=("pipeline",),  # pipeline 指令命中后启用本条规则
        required_terms=PIPELINE_PRAGMA_TERMS,  # 注释必须覆盖的流水线类关键词
        rule="HG009",  # 缺失流水线意图时使用的规则编号
        message="PIPELINE pragma 注释必须说明 II、延迟、循环或吞吐目标。",  # 流水线意图缺失提示
    ),
    PragmaIntentSpec(  # DATAFLOW pragma 关键词要求
        trigger_terms=("dataflow",),  # 只有出现阶段重叠 pragma 时才检查这组词
        required_terms=DATAFLOW_PRAGMA_TERMS,  # 注释必须覆盖的阶段/通道关键词
        rule="HG022",  # 缺失数据流意图时使用的规则编号
        message="DATAFLOW 注释必须说明阶段、通道或 producer/consumer 重叠关系。",  # 数据流意图缺失提示
    ),
    PragmaIntentSpec(  # ARRAY pragma 关键词要求
        trigger_terms=("array_partition", "array_reshape"),  # 命中数组 pragma 时启用本条规则
        required_terms=ARRAY_PRAGMA_TERMS,  # 注释必须覆盖的数组并行化关键词
        rule="HG009",  # 缺失数组并行意图时使用的规则编号
        message="数组 pragma 注释必须说明维度、因子、bank 或缓存并行意图。",  # 数组并行化意图缺失提示
    ),
    PragmaIntentSpec(  # STREAM pragma 关键词要求
        trigger_terms=("#pragma hls stream",),  # 只有出现 stream 缓冲 pragma 时才检查这组词
        required_terms=STREAM_PRAGMA_TERMS,  # 注释必须覆盖的 FIFO/通道关键词
        rule="HG022",  # 缺失 stream 意图时使用的规则编号
        message="STREAM pragma 注释必须说明 FIFO 深度、通道缓冲或生产消费关系。",  # STREAM 意图缺失提示
    ),
)

# check_comment_rules 是本模块对外的 HLS 注释规则入口。
def check_comment_rules(
    root: Path,
    path: Path,
    config: HlsProfileConfig,
    *,
    top_function: str | None = None,
) -> list[HlsGateIssue]:
    """检查单个 HLS C/C++ 文件的注释可读性。

    Args:
        root: 报告相对路径使用的扫描根目录。
        path: 当前被检查的 HLS 源文件路径。
        config: 当前 profile 下的 HLS 可读性规则配置。
        top_function: testbench 需要调用并说明事务目的的 top function 名称。

    Returns:
        当前文件命中的 HLS 注释和空行结构问题列表。
    """

    # 将文件路径转换为报告中稳定展示的 POSIX 相对路径。
    str_rel_path = path.relative_to(root).as_posix()  # 当前 issue 记录使用的相对路径

    # 读取源码文本，忽略少量非 UTF-8 字节以便门禁继续报告。
    str_text = path.read_text(encoding="utf-8", errors="ignore")  # HLS 源码文本

    # 按物理行切分，供行级注释和语句规则复用。
    list_lines = str_text.splitlines()  # 行级规则遍历使用的物理行列表

    # 收集所有注释规则诊断，保持旧接口返回 list。
    list_issues: list[HlsGateIssue] = []  # 注释规则诊断集合

    # 文件头规则先执行，便于报告最外层文件角色问题。
    list_issues.extend(_file_header_issues(list_lines, str_rel_path, config))

    # 普通注释语言和质量规则覆盖所有提取到的注释。
    list_issues.extend(_comment_language_and_quality_issues(str_text, str_rel_path, config))

    # 旧式块注释在 current-project HLS 风格下被统一禁止。
    list_issues.extend(_block_comment_syntax_issues(str_text, str_rel_path, config))

    # HLS 面向人的 transcript 必须使用固定前缀。
    list_issues.extend(_hls_print_prefix_issues(list_lines, str_rel_path, config))

    # 注释相似度去重链用于阻断 exact duplicate、near duplicate 和函数内模板换皮。
    list_issues.extend(_repeated_comment_text_issues(str_text, list_lines, str_rel_path, config))

    # 空行切块规则要求下方 HLS 代码块有中文目的说明。
    list_issues.extend(_blank_line_block_issues(list_lines, str_rel_path, config))

    # 语句级规则检查特殊语句、局部声明、pragma、loop 和 top 调用。
    list_issues.extend(
        _statement_comment_issues(
            list_lines,
            str_rel_path,
            config,
            top_function=top_function,
        )
    )

    # testbench 规则补充 PASS、FAIL 与向量哈希契约说明。
    list_issues.extend(_testbench_comment_issues(list_lines, str_rel_path))

    # 汇总后的诊断会并入当前文件的 HLS readability report。
    return list_issues

# _file_header_issues 检查文件首条注释是否说明 HLS 文件角色。
def _file_header_issues(
    lines: list[str],
    rel_path: str,
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """检查 HLS 文件头是否满足连续 `//` contract 约束。

    参数:
        lines: 当前 HLS 文件的物理行列表。
        rel_path: 报告中使用的相对文件路径。
        config: 当前 profile 的文件头 contract 配置。

    返回:
        文件头 contract 触发的问题列表；通过时返回空列表。
    """

    # 未启用文件头要求时直接跳过。
    if not config.require_file_header:

        # 文件头 contract 规则关闭时不生成任何诊断。
        return []

    # 首个非空物理行决定文件头 contract 的合法起点。
    int_first_content_index = _first_nonempty_line_index(lines)  # 首个非空物理行零基下标

    # 空文件由 HG000 统一兜底。
    if int_first_content_index < 0:

        # 空文件没有可验证的 contract，只能直接报 HG000。
        return [
            make_issue(
                "HG000",
                "error",
                rel_path,
                1,
                "HLS 文件为空，无法进行可读性检查。",
                node_kind="translation_unit",
            )
        ]

    # 第一段必须先出现 comment-only 行，不能直接从代码开始。
    if not is_comment_only(lines[int_first_content_index]):

        # 首个可见内容不是注释块时，文件头 contract 立即判定缺失。
        return [
            make_issue(
                "HG007",
                "error",
                rel_path,
                int_first_content_index + 1,
                "每个 governed HLS 文件都必须先写连续 `//` 文件头 contract，说明职责、核心对象、输入/输出和打印协议。",
                detail=lines[int_first_content_index].strip(),
                node_kind="file_header",
            )
        ]

    # 把首段注释块收集出来，同时记录是否混入了块注释语法。
    tuple_header_scan = _scan_file_header_block(lines, int_first_content_index)  # 文件头扫描结果

    # 扫描结果拆成文件头正文和非法注释形态标志，供后续 contract 判断复用。
    list_header_lines, bool_has_non_line_comment = tuple_header_scan  # 文件头正文与非法注释标志

    # 旧式块注释或混合注释形态不能作为合法文件头 contract。
    if bool_has_non_line_comment:

        # 非 `//` 注释混入文件头时，直接按非法 contract 形态阻断。
        return [
            make_issue(
                "HG007",
                "error",
                rel_path,
                int_first_content_index + 1,
                "文件头 contract 必须使用连续 `//` 注释块，不能使用块注释或混合注释形态。",
                detail="\n".join(list_header_lines).strip(),
                node_kind="file_header",
            )
        ]

    # 把文件头 contract 合并成多行文本，供字段和语言检查复用。
    str_header_block = "\n".join(list_header_lines).strip()  # 文件头 contract 正文

    # 文件头必须覆盖职责、核心对象、输入输出和打印协议。
    bool_has_required_fields = all(str_field in str_header_block for str_field in FILE_HEADER_REQUIRED_FIELDS)  # 是否具备职责/输入输出/打印协议三段

    # 核心对象词决定当前文件是否真正写明了 top function 或关键对象。
    bool_has_core_object = any(str_term in str_header_block for str_term in FILE_HEADER_CORE_OBJECT_TERMS)  # 是否写出核心对象或 top function

    # 字段不全时直接阻断文件头 contract。
    if not bool_has_required_fields or not bool_has_core_object:

        # 合同字段残缺时，要求补齐职责、对象、边界和 transcript 协议。
        return [
            make_issue(
                "HG007",
                "error",
                rel_path,
                int_first_content_index + 1,
                "文件头 contract 必须显式写出职责、核心对象或 top function、输入/输出边界，以及打印或仿真 transcript 协议。",
                detail=str_header_block,
                node_kind="file_header",
            )
        ]

    # 中文和角色词都缺失时说明只是模板化占位。
    if not contains_cjk(str_header_block) or not _contains_any(str_header_block, FILE_HEADER_KEYWORDS):

        # 只有字段标签却没有中文语义时，文件头仍然视作占位模板。
        return [
            make_issue(
                "HG007",
                "error",
                rel_path,
                int_first_content_index + 1,
                "文件头 contract 必须使用中文，并明确描述当前 HLS 文件的角色与顶层对象。",
                detail=str_header_block,
                node_kind="file_header",
            )
        ]

    # 文件头 contract 通过所有固定字段检查。
    return []

# 先定位首个非空物理行，供文件头 contract 起点判断复用。
def _first_nonempty_line_index(lines: list[str]) -> int:
    """定位文件中首个非空物理行。

    参数:
        lines: 当前 HLS 文件的物理行列表。

    返回:
        首个非空物理行的零基下标；若整文件为空白则返回 `-1`。
    """

    # 按物理行顺序扫描，首个非空行决定文件头 contract 的法定起点。
    for int_index, str_line in enumerate(lines):

        # 命中首个非空行后立即返回其零基下标。
        if str_line.strip():

            # 当前行已经是首个可见内容，无需继续扫描后续物理行。
            return int_index

    # 整个文件都是空白时统一返回 -1。
    return -1

# 首段连续注释块的采样与合法性判断集中在这里完成。
def _scan_file_header_block(lines: list[str], int_start_index: int) -> tuple[list[str], bool]:
    """收集文件头首段注释块及其注释形态。

    参数:
        lines: 当前 HLS 文件的物理行列表。
        int_start_index: 文件头 contract 起始行的零基下标。

    返回:
        依次返回归一化后的文件头正文行列表，以及是否混入了非 `//` 注释。
    """

    # 文件头 contract 逐行累积为归一化正文，供后续字段检查直接复用。
    list_header_lines: list[str] = []  # 文件头 contract 正文行

    # 只要混入块注释或其他非 `//` 形态，就不能视作合法文件头。
    bool_has_non_line_comment = False  # 文件头块中是否混入了非法注释形态

    # 只扫描首段连续 comment-only 块，遇到代码或空行就停止。
    for str_raw_line in lines[int_start_index:]:

        # 首段连续注释块结束后，后续内容不再属于文件头 contract。
        if not is_comment_only(str_raw_line):

            # 文件头边界已经确定，停止收集后续物理行。
            break

        # 当前注释行用于区分合法 `//` 与旧式块注释形态。
        str_stripped_line = str_raw_line.strip()  # 当前文件头候选行

        # 不是 `//` 开头时，记录为非法文件头注释形态。
        if not str_stripped_line.startswith("//"):

            # 一旦命中块注释风格，调用方就必须把整段文件头判为非法。
            bool_has_non_line_comment = True  # 文件头混入了非 `//` 注释形态

        # 文件头 contract 正文统一归一化后再进入字段检查。
        list_header_lines.append(normalize_comment_text(str_raw_line))

    # 返回文件头正文与注释形态标志，供主流程决定是否报 HG007。
    return list_header_lines, bool_has_non_line_comment

# _comment_language_and_quality_issues 检查所有注释的语言和语义质量。
def _comment_language_and_quality_issues(
    text: str,
    rel_path: str,
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """检查 HLS 注释是否使用中文且避免模板化表达。

    Args:
        text: 当前 HLS 文件的完整源码文本。
        rel_path: 报告中使用的文件相对路径。
        config: 当前 profile 的注释语言和质量配置。

    Returns:
        注释语言或语义质量命中的诊断列表。
    """

    # 所有注释语言和模板化诊断汇总到该列表。
    list_issues: list[HlsGateIssue] = []  # 注释语言质量诊断

    # 词法提取注释，覆盖独立注释与行尾注释。
    for obj_comment in extract_comments(text):

        # 去掉首尾空白后再判断注释正文。
        str_body = obj_comment.text.strip()  # 当前注释正文

        # 空注释不提供语义，也不作为本规则诊断对象。
        if not str_body:

            # 跳过空白注释片段。
            continue

        # lint/format/copyright 等工具注释允许保留非中文。
        if _allowed_non_chinese_comment(str_body):

            # 工具注释不承担 HLS 语义说明职责。
            continue

        # 当前 profile 要求中文注释时，普通英文注释直接报错。
        if config.require_chinese_comments and not contains_cjk(str_body):

            # 记录非中文普通注释问题。
            list_issues.append(
                make_issue(
                    "HG001",
                    "error",
                    rel_path,
                    obj_comment.line,
                    "HLS 注释必须使用中文；工具保留标记除外。",
                    detail=str_body,
                    node_kind="comment",
                )
            )

            # 语言不合格时无需继续判断该注释是否模板化。
            continue

        # 中文注释仍需体现端口、循环、缓存、事务或数据路径目的。
        bool_weak_comment = _is_generic_comment(str_body, config) or _comment_looks_vague(str_body, config)  # 注释是否空泛

        # 空泛注释不能满足 HLS 可读性门禁。
        if bool_weak_comment:

            # 记录模板化或空泛中文注释。
            list_issues.append(
                make_issue(
                    "HG006",
                    "error",
                    rel_path,
                    obj_comment.line,
                    "HLS 注释过于模板化或空泛，必须结合端口、循环、缓存、事务或数据路径说明具体目的。",
                    detail=str_body,
                    node_kind="comment",
                )
            )

    # 返回所有注释语言和质量诊断。
    return list_issues

# _block_comment_syntax_issues 阻断旧式块注释语法。
def _block_comment_syntax_issues(
    text: str,
    rel_path: str,
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """检查源码是否仍包含被禁止的块注释语法。

    参数:
        text: 当前 HLS 文件的完整源码文本。
        rel_path: 报告中使用的相对文件路径。
        config: 当前 profile 的块注释语法配置。

    返回:
        块注释语法触发的问题列表；通过时返回空列表。
    """

    # 规则关闭时不扫描块注释。
    if not config.forbid_block_comment_syntax:

        # 块注释语法规则关闭时不生成任何诊断。
        return []

    # 逐条扫描提取到的注释跨度，直接阻断 kind=block 的旧式注释。
    return [
        make_issue(
            "HG030",
            "error",
            rel_path,
            obj_comment.line,
            "HLS 注释只允许 `//` 单行注释和连续 `//` 注释块；`/* ... */` 与 `/** ... */` 一律禁止。",
            detail=obj_comment.raw.strip(),
            node_kind="block_comment",
        )
        for obj_comment in extract_comments(text)
        if obj_comment.kind == "block"
    ]

# _hls_print_prefix_issues 检查面向人的 transcript 是否带固定前缀。
def _hls_print_prefix_issues(
    lines: list[str],
    rel_path: str,
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """检查 printf、puts、stdout/stderr 与 iostream transcript 前缀。

    参数:
        lines: 当前 HLS 文件的物理行列表。
        rel_path: 报告中使用的相对文件路径。
        config: 当前 profile 的 HLS transcript 前缀配置。

    返回:
        面向人的 HLS 打印前缀问题列表；通过时返回空列表。
    """

    # profile 关闭时不做 HLS print 边界检查。
    if not config.require_hls_print_prefix:

        # 打印前缀规则关闭时不生成任何诊断。
        return []

    # 问题列表按源码顺序累计。
    list_issues: list[HlsGateIssue] = []  # HLS print 边界问题

    # 逐行检查常见面向人的打印语句。
    for int_line_number, str_raw_line in enumerate(lines, start=1):

        # 去掉尾注释后的代码部分用于识别打印语句。
        str_code = code_part(str_raw_line)  # 当前行的有效代码

        # 当前行不包含面向人的打印语句时直接跳过。
        if not _looks_like_hls_human_print(str_code):

            # 非人类 transcript 行不参与 HG028 前缀检查。
            continue

        # 允许前缀任一命中即可通过当前打印边界。
        if any(str_prefix in str_raw_line for str_prefix in config.allowed_hls_print_prefixes):

            # 合法 transcript 前缀已经命中，当前打印行无需继续报错。
            continue

        # 裸 PASS/FAIL 或无前缀打印统一归入 HG028。
        list_issues.append(
            make_issue(
                "HG028",
                "error",
                rel_path,
                int_line_number,
                "HLS 面向人的打印必须使用 `> INFO: [HLS] ...`、`> WARNING: [HLS] ...` 或 `> ERR: [HLS] ...` 前缀。",
                detail=str_code.strip(),
                node_kind="hls_print_output",
                code_excerpt=str_code.strip(),
            )
        )

    # 返回全部打印前缀问题。
    return list_issues

# _repeated_comment_text_issues 复用 Python current-project 的相似度去重链。
def _repeated_comment_text_issues(
    text: str,
    lines: list[str],
    rel_path: str,
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """检查 exact duplicate、near duplicate 与函数内高相似注释。

    参数:
        text: 当前 HLS 文件的完整源码文本。
        lines: 当前 HLS 文件的物理行列表。
        rel_path: 报告中使用的相对文件路径。
        config: 当前 profile 的 HG029 相似度配置。

    返回:
        注释重复或高相似触发的问题列表；规则关闭或无命中时返回空列表。
    """

    # 规则关闭时跳过整条去重链。
    if not config.forbid_repeated_comment_text:

        # HG029 关闭时不生成任何相似度诊断。
        return []

    # 注释候选由统一 helper 负责过滤非中文、工具注释和空骨架。
    list_candidates = _repeated_comment_candidates(text)  # 可参与去重比较的注释候选

    # 不足两条注释时无法构成重复或相似比较。
    if len(list_candidates) < 2:

        # 单条注释无法形成重复对比关系，直接返回空列表。
        return []

    # 统一记录已报告行，避免 exact/near/function 三条链重复报同一行。
    set_reported_lines: set[int] = set()  # 已登记 HG029 的注释行

    # 问题列表按 exact -> near -> function-similarity 的顺序稳定追加。
    list_issues: list[HlsGateIssue] = []  # HG029 注释相似度问题集合

    # 精确重复先处理，后出现的模板句承担修复责任。
    _append_exact_repeated_comment_issues(
        list_candidates,
        rel_path,
        config.max_exact_comment_reuse,
        set_reported_lines,
        list_issues,
    )

    # 文件级 near duplicate 只保留中文信息量足够的候选。
    list_near_candidates = _near_duplicate_comment_candidates(list_candidates, config)  # 文件级近似重复候选

    # 文件级 near duplicate 负责挡住共享模板标记的轻改写句子。
    _append_near_duplicate_comment_issues(
        list_near_candidates,
        rel_path,
        config,
        set_reported_lines,
        list_issues,
    )

    # 函数内高相似分组补住 marker 没完全覆盖的模板换皮。
    dict_function_groups = _function_comment_candidate_groups(lines, list_candidates, config)  # 函数级注释候选分组

    # 函数内部的高相似句仍按后出现者承担修复责任。
    _append_function_similarity_comment_issues(
        dict_function_groups,
        rel_path,
        config,
        set_reported_lines,
        list_issues,
    )

    # 返回 HG029 诊断集合。
    return list_issues

# exact duplicate 先行登记，确保完全重复句优先承担 HG029 修复责任。
def _append_exact_repeated_comment_issues(
    list_candidates: list[tuple[int, str, str]],
    rel_path: str,
    int_allowed_reuse: int,
    set_reported_lines: set[int],
    list_issues: list[HlsGateIssue],
) -> None:
    """把 exact duplicate 命中的 HG029 追加到结果列表。

    参数:
        list_candidates: 已归一化的注释候选列表。
        rel_path: 报告中使用的相对文件路径。
        int_allowed_reuse: 单条归一化注释允许出现的最大次数。
        set_reported_lines: 已登记 HG029 的注释行集合。
        list_issues: 需要原地追加的问题列表。

    返回:
        无返回值；问题直接追加到 `list_issues`。
    """

    # 精确重复计数器按归一化骨架累计出现次数。
    dict_exact_seen: dict[str, int] = {}  # 精确重复计数器

    # 后出现的完全重复注释承担修复责任。
    for int_line_number, str_normalized_text, _str_original_text in list_candidates:

        # 先读取当前骨架此前已出现的次数。
        int_seen_count = dict_exact_seen.get(str_normalized_text, 0)  # 当前骨架此前出现次数

        # 当前骨架的出现次数随后立即回写计数器。
        dict_exact_seen[str_normalized_text] = int_seen_count + 1  # 当前骨架累计出现次数

        # 仍在允许复用次数以内时不报 HG029。
        if int_seen_count < int_allowed_reuse:

            # 当前重复度仍在容忍范围内，继续检查下一个候选。
            continue

        # 超出允许复用次数后，当前注释行承担 exact duplicate 修复责任。
        list_issues.append(_repeated_comment_issue(rel_path, int_line_number))

        # 报告过的行号写入集合，避免后续链路重复报同一行。
        set_reported_lines.add(int_line_number)

# 文件级 near duplicate 先做信息量过滤，避免短句噪声放大相似度比较。
def _near_duplicate_comment_candidates(
    list_candidates: list[tuple[int, str, str]],
    config: HlsProfileConfig,
) -> list[tuple[int, str, str]]:
    """筛出文件级 near duplicate 需要比较的注释候选。

    参数:
        list_candidates: 已归一化的注释候选列表。
        config: 当前 profile 的 HG029 相似度配置。

    返回:
        满足 near-duplicate 最低中文信息量要求的候选列表。
    """

    # 文件级 near duplicate 只比较中文信息量足够的注释。
    list_near_candidates: list[tuple[int, str, str]] = []  # 通过信息量门槛的 near-duplicate 候选

    # 逐条筛掉中文信息量过低的注释骨架。
    for tuple_candidate in list_candidates:

        # 先统计当前归一化文本中的中文信息量。
        int_cjk_count = _count_cjk_characters(tuple_candidate[1])  # 当前候选的中文字符数

        # 中文信息量不足时不进入文件级 near duplicate 比较。
        if int_cjk_count < config.min_near_duplicate_cjk_chars:

            # 低信息量短句交给函数级链路或其他规则处理。
            continue

        # 满足最小中文信息量后再纳入文件级 near duplicate 候选。
        list_near_candidates.append(tuple_candidate)

    # 返回文件级 near duplicate 的候选集合。
    return list_near_candidates

# 文件级 near duplicate 负责拦住共享模板标记的轻改写句子。
def _append_near_duplicate_comment_issues(
    list_near_candidates: list[tuple[int, str, str]],
    rel_path: str,
    config: HlsProfileConfig,
    set_reported_lines: set[int],
    list_issues: list[HlsGateIssue],
) -> None:
    """把文件级 near duplicate 命中的 HG029 追加到结果列表。

    参数:
        list_near_candidates: 已通过中文信息量筛选的 near-duplicate 候选。
        rel_path: 报告中使用的相对文件路径。
        config: 当前 profile 的 HG029 相似度配置。
        set_reported_lines: 已登记 HG029 的注释行集合。
        list_issues: 需要原地追加的问题列表。

    返回:
        无返回值；问题直接追加到 `list_issues`。
    """

    # 向前比较保证后出现的句子承担修复责任。
    for int_index, tuple_candidate in enumerate(list_near_candidates):

        # 当前候选拆成行号和归一化文本，便于后续相似度比较。
        int_line_number, str_normalized_text, _str_original_text = tuple_candidate  # 当前 near 候选

        # 已在 exact duplicate 链里报过的行号不再重复进入 near duplicate。
        if int_line_number in set_reported_lines:

            # 当前行已经被 earlier chain 接管，继续检查下一个候选。
            continue

        # 只与当前候选之前出现过的注释比较，保持后出现者承担修复责任。
        for _int_previous_line, str_previous_text, _str_previous_original in list_near_candidates[:int_index]:

            # 完全相同的骨架已由 exact duplicate 链处理，这里不重复报错。
            if str_normalized_text == str_previous_text:

                # 完全相同的骨架已在 earlier chain 覆盖，继续看下一条前文。
                continue

            # near duplicate 只在共享模板信号词时才继续比较相似度。
            if not _comments_share_template_marker(
                str_normalized_text,
                str_previous_text,
                config.function_similarity_template_terms,
            ):

                # 没有共享模板信号词时，不把两条注释视作 near duplicate 候选对。
                continue

            # 当前两条候选的相似度决定是否命中文件级 near duplicate。
            float_similarity = _comment_similarity(str_normalized_text, str_previous_text)  # 当前 near 候选对的相似度

            # 相似度低于阈值时，不生成 near duplicate 诊断。
            if float_similarity < config.near_duplicate_similarity_threshold:

                # 当前句对还没达到 near duplicate 阈值，继续比较下一条前文。
                continue

            # 命中文件级 near duplicate 后，由当前较晚出现的注释行承担修复责任。
            list_issues.append(_repeated_comment_issue(rel_path, int_line_number))

            # 已报告的 near duplicate 行号写回集合，避免函数级链路再重复报它。
            set_reported_lines.add(int_line_number)

            # 当前候选已经命中 near duplicate，无需再比较更早的注释。
            break

# 把注释候选按函数体分桶后，函数级高相似链才有稳定的比较边界。
def _function_comment_candidate_groups(
    lines: list[str],
    list_candidates: list[tuple[int, str, str]],
    config: HlsProfileConfig,
) -> dict[int, list[tuple[int, str, str]]]:
    """按函数跨度把注释候选分组，供函数内高相似链复用。

    参数:
        lines: 当前 HLS 文件的物理行列表。
        list_candidates: 已归一化的注释候选列表。
        config: 当前 profile 的 HG029 相似度配置。

    返回:
        函数下标到该函数内注释候选列表的映射。
    """

    # 函数跨度表只保留真正带函数体的实现，声明原型不参与函数内相似比较。
    list_function_spans = _function_similarity_spans(lines)  # 当前文件的函数跨度表

    # 分组结果按函数下标收拢注释候选，模块级注释不进入函数内相似链。
    dict_function_groups: dict[int, list[tuple[int, str, str]]] = {}  # 函数下标到注释候选列表

    # 逐条把足够长的中文注释归入最近函数。
    for tuple_candidate in list_candidates:

        # 当前候选拆成行号与归一化文本，便于做信息量和跨度判断。
        int_line_number, str_normalized_text, _str_original_text = tuple_candidate  # 当前函数级候选

        # 中文信息量不足的短句不参与函数内高相似比较。
        if _count_cjk_characters(str_normalized_text) < config.min_function_similarity_cjk_chars:

            # 低信息量短句不会进入函数级高相似链。
            continue

        # 只有落在函数体跨度内的注释才进入函数级分组。
        for int_function_index, int_start_line, int_end_line in list_function_spans:

            # 命中当前函数跨度后，候选就归入该函数并停止继续搜索。
            if int_start_line <= int_line_number <= int_end_line:

                # 当前注释候选挂到所属函数组，供函数内高相似链复用。
                dict_function_groups.setdefault(int_function_index, []).append(tuple_candidate)

                # 每条注释最多只属于一个函数体，命中后立即结束跨度搜索。
                break

    # 返回函数级注释候选分组结果。
    return dict_function_groups

# 函数跨度表集中提取出来，避免高相似链重复解析函数边界。
def _function_similarity_spans(lines: list[str]) -> list[tuple[int, int, int]]:
    """提取函数内高相似比较需要的函数跨度表。

    参数:
        lines: 当前 HLS 文件的物理行列表。

    返回:
        依次包含函数下标、起始行和结束行的跨度列表。
    """

    # 只保留带函数体的实现跨度，函数声明原型不参与函数内相似比较。
    return [
        (int_index, obj_function.start_line, obj_function.end_line)
        for int_index, obj_function in enumerate(parse_functions(lines))
        if not obj_function.is_declaration
    ]

# 函数级高相似链负责补住模板标记未完全覆盖的函数内换皮注释。
def _append_function_similarity_comment_issues(
    dict_function_groups: dict[int, list[tuple[int, str, str]]],
    rel_path: str,
    config: HlsProfileConfig,
    set_reported_lines: set[int],
    list_issues: list[HlsGateIssue],
) -> None:
    """把函数内高相似注释命中的 HG029 追加到结果列表。

    参数:
        dict_function_groups: 函数下标到注释候选列表的映射。
        rel_path: 报告中使用的相对文件路径。
        config: 当前 profile 的 HG029 相似度配置。
        set_reported_lines: 已登记 HG029 的注释行集合。
        list_issues: 需要原地追加的问题列表。

    返回:
        无返回值；问题直接追加到 `list_issues`。
    """

    # 每个函数内部只报告后出现的高相似注释。
    for list_group in dict_function_groups.values():

        # 单函数组不足两条注释时，不足以形成高相似比较。
        if len(list_group) < 2:

            # 当前函数的注释样本不足，继续检查下一个函数组。
            continue

        # 当前函数组内按源码顺序比较后出现的注释。
        for int_index, tuple_candidate in enumerate(list_group):

            # 当前候选拆成行号与归一化文本，供相似度链复用。
            int_line_number, str_normalized_text, _str_original_text = tuple_candidate  # 当前函数组候选

            # 已由 earlier chain 接管的行号不再重复进入函数级高相似比较。
            if int_line_number in set_reported_lines:

                # 当前行已经报过 HG029，继续看函数组内下一条候选。
                continue

            # 只与本函数组中更早出现的注释比较，保持后出现者承担修复责任。
            for _int_previous_line, str_previous_text, _str_previous_original in list_group[:int_index]:

                # 完全相同的骨架已由 exact duplicate 链处理，不在这里重复报错。
                if str_normalized_text == str_previous_text:

                    # 完全相同的骨架无需再进函数级高相似判定。
                    continue

                # 当前两条函数内注释的相似度供高相似与模板相似双阈值复用。
                float_similarity = _comment_similarity(str_normalized_text, str_previous_text)  # 当前函数组候选对的相似度

                # 纯高相似阈值负责兜住没有模板标记的近乎同义句。
                bool_high_similarity = float_similarity >= config.function_comment_similarity_threshold  # 是否命中函数内高相似阈值

                # 模板相似阈值负责兜住共享模板标记的轻改写句子。
                bool_template_hit = _template_hit(str_normalized_text, str_previous_text, config, float_similarity)  # 模板相似命中

                # 两条阈值都未命中时，不生成函数级 HG029。
                if not (bool_high_similarity or bool_template_hit):

                    # 当前句对还没达到函数内高相似标准，继续比较更早的前文。
                    continue

                # 命中函数级高相似后，由当前较晚出现的注释行承担修复责任。
                list_issues.append(_repeated_comment_issue(rel_path, int_line_number))

                # 已报告的函数级高相似行号写回集合，避免后续重复报它。
                set_reported_lines.add(int_line_number)

                # 当前候选已经在本函数组命中 HG029，无需再比较更早的注释。
                break

# _repeated_comment_candidates 整理参与相似度比较的普通注释。
def _repeated_comment_candidates(text: str) -> list[tuple[int, str, str]]:
    """收集中文注释的相似度比较候选。

    参数:
        text: 当前 HLS 文件的完整源码文本。

    返回:
        依次包含行号、归一化文本和原始注释正文的候选列表。
    """

    # 候选保持源码顺序，便于后出现者承担修复责任。
    list_candidates: list[tuple[int, str, str]] = []  # 注释去重候选

    # 逐条遍历注释跨度，过滤非中文、工具注释和空骨架。
    for obj_comment in extract_comments(text):

        # 原始注释正文需要先裁掉首尾空白，再决定是否保留。
        str_original_text = obj_comment.text.strip()  # 原始注释正文

        # 非中文或空注释不会进入 HG029 的相似度比较链。
        if not str_original_text or not contains_cjk(str_original_text):

            # 空骨架或非中文注释不参与 HG029 相似度比较。
            continue

        # 白名单里的特殊注释不应进入重复/高相似阻断链。
        if _allowed_non_chinese_comment(str_original_text):

            # 当前注释属于白名单形态，继续检查下一条注释跨度。
            continue

        # 归一化骨架统一去掉数字、ASCII 标识符与标点差异。
        str_normalized_text = _normalized_comment_similarity_content(str_original_text)  # 相似度比较文本骨架

        # 归一化后已经没有中文语义时，不进入 HG029 候选集合。
        if not str_normalized_text:

            # 归一化结果为空说明当前注释只剩模板噪声，不参与相似度比较。
            continue

        # 合法候选按源码顺序保留行号、骨架与原始正文。
        list_candidates.append((obj_comment.line, str_normalized_text, str_original_text))

    # 返回按源码顺序排列的注释候选。
    return list_candidates

# _normalized_comment_similarity_content 生成去重链使用的强归一化骨架。
def _normalized_comment_similarity_content(comment_text: str) -> str:
    """移除数字、ASCII 标识符和常见标点，只保留中文语义骨架。

    参数:
        comment_text: 当前待归一化的注释正文。

    返回:
        供 exact/near/function similarity 复用的中文语义骨架。
    """

    # 先用现有 helper 去掉注释符号，再统一成小写比较文本。
    str_normalized_text = normalize_comment_text(comment_text).casefold()  # 去掉注释边界后的正文

    # 先去掉 ASCII 标识符，避免变量名差异把模板句伪装成不同注释。
    str_normalized_text = re.sub(r"\b[a-z_][a-z0-9_]*\b", "", str_normalized_text)  # 去掉 ASCII 标识符后的骨架文本

    # 再去掉数字和序号，避免行号变化把同一句模板伪装成不同文本。
    str_normalized_text = re.sub(r"\d+", "", str_normalized_text)  # 去掉数字后的骨架文本

    # 去掉空白和常见中英文标点，只保留中文句意骨架。
    str_normalized_text = _strip_comment_similarity_punctuation(str_normalized_text)  # 去掉空白和标点后的骨架文本

    # 返回可用于 exact/near/function similarity 的骨架文本。
    return str_normalized_text.strip()

# _count_cjk_characters 统计文本中的中文字符数量。
def _count_cjk_characters(text: str) -> int:
    """统计文本中的 CJK 统一表意文字数量。

    参数:
        text: 当前待统计的文本内容。

    返回:
        文本中的中文字符数量。
    """

    # 只统计中文字符，避免 ASCII token 拉高信息量。
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))

# _comments_share_template_marker 判断两条注释是否共享低信息模板标记。
def _comments_share_template_marker(
    left_text: str,
    right_text: str,
    markers: tuple[str, ...],
) -> bool:
    """判断两条归一化注释是否共享模板信号词。

    参数:
        left_text: 左侧归一化注释文本。
        right_text: 右侧归一化注释文本。
        markers: 低信息模板信号词集合。

    返回:
        共享任一模板信号词时返回 `True`，否则返回 `False`。
    """

    # 共享任一模板信号词时，近似高相似更可能来自换皮模板。
    return any(str_marker in left_text and str_marker in right_text for str_marker in markers)

# 归一化后的中文骨架还要统一剥掉标点和空白，避免格式差异影响相似度比较。
def _strip_comment_similarity_punctuation(text: str) -> str:
    """去掉注释骨架中的空白和常见中英文标点。

    参数:
        text: 已去掉注释符号、ASCII 标识符和数字后的文本骨架。

    返回:
        去掉空白与常见标点后的中文语义骨架。
    """

    # 空白和标点只会制造格式差异，不应该改变中文语义骨架的相似度结论。
    return re.sub(r"[\s`'\"：:，,。；;、（）()\[\]{}<>《》!！?？+\-*/=|\\]+", "", text)

# 模板相似判定集中到这里，避免函数级高相似链把阈值与标记逻辑写成长表达式。
def _template_hit(
    left_text: str,
    right_text: str,
    config: HlsProfileConfig,
    float_similarity: float,
) -> bool:
    """判断一对注释是否命中模板相似阈值。

    参数:
        left_text: 左侧归一化注释文本。
        right_text: 右侧归一化注释文本。
        config: 当前 profile 的 HG029 相似度配置。
        float_similarity: 当前注释对已经计算好的相似度分数。

    返回:
        相似度超过阈值且共享模板信号词时返回 `True`。
    """

    # 相似度达标且共享模板标记时，当前句对就属于模板换皮风险。
    return float_similarity >= config.function_template_similarity_threshold and _comments_share_template_marker(
        left_text,
        right_text,
        config.function_similarity_template_terms,
    )

# _comment_similarity 用标准库估计两条中文注释的相似度。
def _comment_similarity(left_text: str, right_text: str) -> float:
    """计算两条归一化注释之间的相似度。

    参数:
        left_text: 左侧归一化注释文本。
        right_text: 右侧归一化注释文本。

    返回:
        `SequenceMatcher` 计算得到的相似度分数。
    """

    # SequenceMatcher 对短中文句足够稳定，也不引入第三方依赖。
    return difflib.SequenceMatcher(None, left_text, right_text).ratio()

# _repeated_comment_issue 构造统一的 HG029 问题对象。
def _repeated_comment_issue(rel_path: str, line_number: int) -> HlsGateIssue:
    """构造重复或高相似注释的 HG029 诊断。

    参数:
        rel_path: 报告中使用的相对文件路径。
        line_number: 当前命中 HG029 的注释行号。

    返回:
        统一形状的 HG029 诊断对象。
    """

    # HG029 统一提示调用方写出上下文相关而非模板复用的注释。
    return make_issue(
        "HG029",
        "error",
        rel_path,
        line_number,
        "注释重复或高度复用了另一条注释；必须改写成当前端口、缓存、循环或事务语义专属的说明。",
        node_kind="comment_similarity",
    )

# 这里专门识别面向人的 transcript 语句，供 HG028 打印前缀规则复用。
def _looks_like_hls_human_print(code: str) -> bool:
    """判断当前代码片段是否为面向人的打印语句。

    参数:
        code: 去掉注释后的单行 HLS/C++ 代码片段。

    返回:
        命中 printf、puts、stdout/stderr 或 iostream transcript 时返回 `True`。
    """

    # 空片段不可能承载打印语句。
    if not code.strip():

        # 没有任何有效代码时，不可能构成人类 transcript 语句。
        return False

    # printf、puts、stdout/stderr 与常见 iostream 流都属于人类 transcript。
    # 只要命中任一已知打印接口，就把当前语句视作 HG028 检查对象。
    return bool(
        re.search(r"\bprintf\s*\(", code)
        or re.search(r"\bputs\s*\(", code)
        or re.search(r"\bfprintf\s*\(\s*(?:stdout|stderr)\s*,", code)
        or "std::cout" in code
        or "std::cerr" in code
        or "std::clog" in code
    )

# _blank_line_block_issues 检查空行切分出的下方代码块是否有中文说明。
def _blank_line_block_issues(
    lines: list[str],
    rel_path: str,
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """检查空行分隔的 HLS 代码块说明。

    Args:
        lines: 当前文件的源码物理行。
        rel_path: 报告中使用的文件相对路径。
        config: 当前 profile 的空行块规则配置。

    Returns:
        空行块缺少中文目的注释时产生的诊断列表。
    """

    # 未启用空行块说明时直接跳过该规则。
    if not config.require_blank_line_comments:

        # 配置关闭时不生成空行块诊断。
        return []

    # 空行块诊断在扫描过程中逐项追加。
    list_issues: list[HlsGateIssue] = []  # 空行块诊断集合

    # int_index 是当前扫描到的物理行下标。
    int_index = 0  # 当前源码行下标

    # 顺序扫描全部物理行，遇到连续空行时检查下方代码块。
    while int_index < len(lines):

        # 非空行不形成分隔块，继续向后扫描。
        if lines[int_index].strip():

            # 推进到下一行继续寻找空行。
            int_index += 1  # 下一条待扫描源码行下标

            # 当前行不是空行，后续逻辑无需执行。
            continue

        # 找到空行前最近的有意义代码或注释行。
        int_previous_code = previous_meaningful_code_index(lines, int_index)  # 空行前有意义行下标

        # 合并连续空行，避免同一空白段重复报错。
        while int_index < len(lines) and not lines[int_index].strip():

            # 跳过当前连续空白段中的一行。
            int_index += 1  # 连续空白段后的候选源码行下标

        # 定位空行之后第一个需要说明关系的有效行。
        int_next_code = next_meaningful_code_index(lines, int_index)  # 空行后有意义行下标

        # 文件开头或结尾的空白不分隔两个代码块。
        if int_previous_code is None or int_next_code is None:

            # 缺少上下文时不判定为空行块问题。
            continue

        # 下方代码块前一行应当是中文独立注释。
        int_comment_index = int_next_code - 1  # 下方代码块说明注释下标

        # 检查该说明注释是否存在且包含中文。
        bool_has_block_comment = (  # 下方代码块是否有中文说明
            int_comment_index >= 0  # 先确认空行后仍能回溯到候选注释行
            and is_comment_only(lines[int_comment_index])  # 候选行必须是纯注释行
            and contains_cjk(normalize_comment_text(lines[int_comment_index]))  # 注释正文必须包含中文
        )

        # 缺少中文说明时报告空行块问题。
        if not bool_has_block_comment:

            # 记录下方代码块缺少目的说明的问题。
            list_issues.append(
                make_issue(
                    "HG002",
                    "error",
                    rel_path,
                    int_next_code + 1,
                    "空行分隔 HLS 代码块时，下方代码块必须先有独立中文目的注释。",
                    detail=lines[int_next_code].strip(),
                    node_kind="blank_line_block",
                    code_excerpt=lines[int_next_code].strip(),
                )
            )

    # 返回空行块检查产生的全部诊断。
    return list_issues

# _statement_comment_issues 汇总语句级注释规则。
def _statement_comment_issues(
    lines: list[str],
    rel_path: str,
    config: HlsProfileConfig,
    *,
    top_function: str | None,
) -> list[HlsGateIssue]:
    """检查 HLS 特殊语句、局部声明、pragma、loop 和 testbench 调用注释。

    Args:
        lines: 当前文件的源码物理行。
        rel_path: 报告中使用的文件相对路径。
        config: 当前 profile 的语句级规则配置。
        top_function: testbench 调用前需要说明事务目的的 top function 名称。

    Returns:
        语句级注释规则命中的诊断列表。
    """

    # 语句级规则诊断按源码顺序收集。
    list_issues: list[HlsGateIssue] = []  # 语句级诊断集合

    # statement_infos 负责把 HLS/C++ 物理行解析为可检查语句。
    for obj_info in statement_infos(lines):

        # 当前语句上下文集中保存，避免多个 helper 反复传递散字段。
        statement_context_obj_statement_context = _statement_context(lines, rel_path, obj_info)  # 语句检查上下文

        # 顶层声明和 case/break 等上下文不要求同样的局部注释。
        if _is_ignorable_declaration_context(
            lines,
            statement_context_obj_statement_context.line_index,
            statement_context_obj_statement_context.depth,
        ):

            # 跳过不承担本规则注释职责的语句。
            continue

        # 检查特殊语句是否有空行加中文目的注释。
        _append_statement_spacing_issue(
            list_issues,
            statement_context_obj_statement_context,
            config,
        )

        # 检查局部声明或赋值的上方注释和右侧注释。
        _append_declaration_comment_issues(
            list_issues,
            statement_context_obj_statement_context,
            config,
        )

        # 检查 HLS pragma 是否由中文注释解释硬件意图。
        _append_pragma_comment_issues(
            list_issues,
            statement_context_obj_statement_context,
            config,
        )

        # 检查 loop 注释是否说明迭代边界或数据事务。
        _append_loop_comment_issue(
            list_issues,
            statement_context_obj_statement_context,
            config,
        )

        # testbench top function 调用必须说明用例事务和观测目的。
        _append_testbench_call_issue(
            list_issues,
            statement_context_obj_statement_context,
            top_function,
        )

    # 返回所有语句级规则诊断。
    return list_issues

# _statement_context 从 lexer 语句对象整理检查上下文。
def _statement_context(lines: list[str], rel_path: str, info: Any) -> StatementContext:
    """构造单条语句的注释检查上下文。

    Args:
        lines: 当前文件的源码物理行。
        rel_path: 报告中使用的文件相对路径。
        info: statement_infos 产出的语句信息对象。

    Returns:
        收拢行号、代码、上方注释和特殊语句类别的上下文对象。
    """

    # 语句下标用于读取原始行和周边注释。
    int_line_index = info.line - 1  # 当前语句下标

    # 语句代码只包含注释前的有效 C/C++ 片段。
    str_code = info.code  # 当前语句代码

    # 读取紧邻上方注释，用于局部声明、pragma 和 loop 意图检查。
    str_preceding_comment = immediate_preceding_comment(lines, int_line_index)  # 紧邻上方注释

    # special_statement_kind 将控制语句、函数调用等归为稳定类别。
    str_statement_kind = special_statement_kind(str_code)  # if/loop/return/调用等稳定语句分类

    # 返回不可变上下文，供后续 helper 共享。
    return StatementContext(
        lines=lines,
        rel_path=rel_path,
        line_number=info.line,

        # 零基位置和块深度用于后续忽略顶层声明、case 等上下文。
        line_index=int_line_index,
        depth=info.depth,

        # 代码和注释字段承载各类语义规则的共同输入。
        code=str_code,
        preceding_comment=str_preceding_comment,
        statement_kind=str_statement_kind,
    )

# _append_statement_spacing_issue 负责特殊语句上方说明检查。
def _append_statement_spacing_issue(
    issues: list[HlsGateIssue],
    context: StatementContext,
    config: HlsProfileConfig,
) -> None:
    """在缺少特殊语句说明时追加诊断。

    Args:
        issues: 调用方维护的诊断列表，会被原地追加。
        context: 当前语句的行号、代码和注释上下文。
        config: 当前 profile 的语句间距规则配置。

    Returns:
        该函数只追加诊断，不返回业务值。
    """

    # 缺少特殊语句类别时无需检查 PG032/HG003 类约束。
    if context.statement_kind is None:

        # 非特殊语句不需要本规则处理。
        return

    # 配置未启用特殊语句间距规则时跳过。
    if not config.require_special_statement_spacing:

        # 调用方 profile 不要求特殊语句注释。
        return

    # 只对 profile 声明的特殊语句类别执行检查。
    if context.statement_kind not in set(config.special_statement_kinds):

        # 该语句类别不在当前 profile 的强制范围内。
        return

    # 当前语句上方必须有空行加中文独立注释。
    if has_blank_plus_chinese_comment_above(context.lines, context.line_index):

        # 已满足语句上方说明规则。
        return

    # 函数签名允许使用紧邻的连续 // contract 块，而不是强制退化成单行说明。
    if context.statement_kind == "function_signature" and _has_line_comment_block_above(
        context.lines,
        context.line_index,
    ):

        # 连续 contract 块本身就是函数签名的合法上方说明。
        return

    # 缺少说明时追加 HG003 诊断。
    issues.append(
        make_issue(
            "HG003",
            "error",
            context.rel_path,
            context.line_number,
            "特殊 HLS 语句上方必须保留一个空行和一条邻近中文目的注释。",
            detail=context.code,
            node_kind=context.statement_kind,
            code_excerpt=context.code,
        )
    )

# 这个 helper 用来判断函数签名前面的连续 `//` 注释块是否真正独立存在。
def _has_line_comment_block_above(lines: list[str], line_index: int) -> bool:
    """判断函数签名前是否紧邻连续 `//` 注释块。

    参数:
        lines: 当前 HLS 文件的物理行列表。
        line_index: 目标代码行的零基下标。

    返回:
        目标行上方存在独立连续 `//` 注释块时返回 `True`。
    """

    # 目标行至少要有上一行，才可能存在注释块。
    if line_index <= 0:

        # 文件开头之前没有上一行，因此不可能存在紧邻注释块。
        return False

    # 紧邻上一行必须是纯 // 注释。
    int_comment_index = line_index - 1  # 紧邻目标行的候选注释行

    # 紧邻上一行若不是 `//` 注释，目标行上方就不存在合法注释块。
    if not lines[int_comment_index].strip().startswith("//"):

        # 当前签名前面的紧邻行已经断开注释块，因此这里直接判定为 False。
        return False

    # 向上回溯整个连续 // 块。
    while int_comment_index >= 0 and lines[int_comment_index].strip().startswith("//"):

        # 当前循环命中的仍是注释行，因此游标继续向上回退一行。
        int_comment_index -= 1  # 连续注释块向上回溯

    # 注释块上方是文件开头或空行时，说明该块独立成立。
    return int_comment_index < 0 or not lines[int_comment_index].strip()

# _append_declaration_comment_issues 负责局部声明和赋值注释检查。
def _append_declaration_comment_issues(
    issues: list[HlsGateIssue],
    context: StatementContext,
    config: HlsProfileConfig,
) -> None:
    """检查局部变量声明或赋值的上下文注释。

    Args:
        issues: 调用方维护的诊断列表，会被原地追加。
        context: 当前语句的行号、代码和注释上下文。
        config: 当前 profile 的声明注释规则配置。

    Returns:
        该函数只追加诊断，不返回业务值。
    """

    # 只有函数体或代码块内部的局部声明/赋值需要此规则。
    bool_local_state = (is_local_declaration(context.code) or is_assignment(context.code)) and context.depth > 0  # 是否局部状态语句

    # 非局部状态语句不承担局部数据路径说明职责。
    if not bool_local_state:

        # 跳过非声明赋值语句。
        return

    # 检查局部状态语句上方是否有合格中文目的说明。
    _append_declaration_above_comment_issue(issues, context, config)

    # 检查局部状态语句右侧是否有合格中文用途说明。
    _append_declaration_inline_comment_issue(issues, context, config)

# _append_declaration_above_comment_issue 检查局部状态上方注释。
def _append_declaration_above_comment_issue(
    issues: list[HlsGateIssue],
    context: StatementContext,
    config: HlsProfileConfig,
) -> None:
    """检查局部声明或赋值上方的中文目的注释。

    Args:
        issues: 调用方维护的诊断列表，会被原地追加。
        context: 当前语句的行号、代码和注释上下文。
        config: 当前 profile 的声明上方注释配置。

    Returns:
        该函数只追加诊断，不返回业务值。
    """

    # 未启用上方注释要求时跳过该规则。
    if not config.require_declaration_above_comment:

        # 当前 profile 不要求局部状态上方说明。
        return

    # 缺少中文上方注释时追加 HG004。
    if not context.preceding_comment or not contains_cjk(context.preceding_comment):

        # 记录局部状态缺少上方用途说明的问题。
        issues.append(
            make_issue(
                "HG004",
                "error",
                context.rel_path,
                context.line_number,
                "局部变量声明或赋值必须在上方用中文说明该状态、缓存或数据路径用途。",
                detail=context.code,
                node_kind="local_declaration_or_assignment",
                code_excerpt=context.code,
            )
        )

        # 缺少注释时无需继续判断该注释质量。
        return

    # 先判断上方注释是否命中 profile 禁止的模板短语。
    bool_generic_above_comment = _is_generic_comment(  # 是否命中 profile 禁止的模板短语
        context.preceding_comment,  # 当前上方注释正文
        config,  # 模板短语配置来源
    )

    # 再判断上方注释是否过短到无法说明局部状态用途。
    bool_vague_above_comment = _comment_looks_vague(  # 中文信息量是否低到无法说明变量用途
        context.preceding_comment,  # 用原始上方注释文本统计中文信息量
        config,  # 读取 profile 中的空泛短语配置
    )

    # 只要任一检查命中，就视作上方注释质量不足。
    bool_weak_above_comment = bool_generic_above_comment or bool_vague_above_comment  # 上方注释是否过弱

    # 合格的上方注释不产生诊断。
    if not bool_weak_above_comment:

        # 局部状态上方说明已经足够具体。
        return

    # 记录局部状态上方注释语义不足的问题。
    issues.append(
        make_issue(
            "HG006",
            "error",
            context.rel_path,
            context.line_number,
            "变量上方注释必须说明硬件/数据路径用途，不能只写“保存结果”等模板句。",
            detail=context.preceding_comment,
            node_kind="local_declaration_or_assignment",
            code_excerpt=context.code,
        )
    )

# _append_declaration_inline_comment_issue 检查局部状态右侧注释。
def _append_declaration_inline_comment_issue(
    issues: list[HlsGateIssue],
    context: StatementContext,
    config: HlsProfileConfig,
) -> None:
    """检查局部声明或赋值行右侧的中文用途注释。

    Args:
        issues: 调用方维护的诊断列表，会被原地追加。
        context: 当前语句的行号、代码和注释上下文。
        config: 当前 profile 的声明右侧注释配置。

    Returns:
        该函数只追加诊断，不返回业务值。
    """

    # profile 可单独控制当前规则是否强制声明右侧注释。
    bool_profile_requires_inline_comment = config.require_declaration_inline_comment  # profile 是否启用右侧注释强制要求

    # 语句形态本身也要满足“适合检查右侧注释”的条件。
    bool_statement_needs_inline_comment = _needs_inline_comment(  # 语句形态是否需要右侧中文注释
        context.code,  # 去掉注释后的当前语句代码
        context.lines[context.line_index],  # 当前语句所在的原始物理行
        config,  # 当前 profile 的右侧注释判定配置
    )

    # 只有 profile 开启且语句形态符合时，才要求右侧中文注释。
    bool_needs_inline_comment = bool_profile_requires_inline_comment and bool_statement_needs_inline_comment  # 当前语句是否需要右侧中文用途注释

    # 不需要右侧注释的长行或多行声明由其它规则覆盖。
    if not bool_needs_inline_comment:

        # 该语句不进入 HG005/HG006 右侧注释检查。
        return

    # 提取当前源码行右侧注释正文。
    str_raw_line = context.lines[context.line_index]  # 当前源码物理行

    # 行尾注释不存在时使用空字符串进入统一判断。
    str_inline_comment = inline_comment_text(str_raw_line) if has_inline_comment(str_raw_line) else ""  # 行尾注释正文

    # 缺少中文右侧注释时追加 HG005。
    if not str_inline_comment or not contains_cjk(str_inline_comment):

        # 记录局部状态行缺少右侧中文用途注释的问题。
        issues.append(
            make_issue(
                "HG005",
                "error",
                context.rel_path,
                context.line_number,
                "局部变量声明或赋值行右侧必须补中文用途注释；多行声明按配置豁免并由 HG024 单独检查。",
                detail=context.code,
                node_kind="inline_declaration_comment",
                code_excerpt=str_raw_line.strip(),
            )
        )

    # 已有右侧注释也需要说明真实用途。
    elif _is_generic_comment(str_inline_comment, config) or _comment_looks_vague(str_inline_comment, config):

        # 记录右侧注释模板化或空泛的问题。
        issues.append(
            make_issue(
                "HG006",
                "error",
                context.rel_path,
                context.line_number,
                "变量右侧注释必须解释用途，不能是模板化或空泛中文。",
                detail=str_inline_comment,
                node_kind="inline_declaration_comment",
                code_excerpt=str_raw_line.strip(),
            )
        )

# _append_pragma_comment_issues 负责 HLS pragma 硬件意图说明检查。
def _append_pragma_comment_issues(
    issues: list[HlsGateIssue],
    context: StatementContext,
    config: HlsProfileConfig,
) -> None:
    """检查 HLS pragma 上方注释是否说明硬件意图。

    Args:
        issues: 调用方维护的诊断列表，会被原地追加。
        context: 当前 pragma 语句的行级检查上下文。
        config: 当前 profile 的 pragma 意图规则配置。

    Returns:
        该函数只追加诊断，不返回业务值。
    """

    # 非 HLS pragma 语句不进入硬件意图检查。
    if not is_hls_pragma(context.code):

        # 跳过普通 C/C++ 语句。
        return

    # pragma 必须由中文注释说明硬件意图。
    if not context.preceding_comment or not contains_cjk(context.preceding_comment):

        # 记录 pragma 缺少上方中文说明的问题。
        issues.append(
            make_issue(
                "HG009",
                "error",
                context.rel_path,
                context.line_number,
                "#pragma HLS 必须由上方中文注释解释硬件意图。",
                detail=context.code,
                node_kind="pragma",
                code_excerpt=context.code,
            )
        )

        # 缺少基础中文说明时无需继续判断细分意图词。
        return

    # profile 启用时才做 INTERFACE/PIPELINE 等细分关键词检查。
    if config.require_pragma_hardware_intent:

        # 追加具体 pragma 类型的意图诊断。
        issues.extend(_pragma_intent_issues(context))

# _append_loop_comment_issue 负责 loop 注释意图检查。
def _append_loop_comment_issue(
    issues: list[HlsGateIssue],
    context: StatementContext,
    config: HlsProfileConfig,
) -> None:
    """检查循环注释是否说明迭代或数据事务目的。

    Args:
        issues: 调用方维护的诊断列表，会被原地追加。
        context: 当前循环语句的行级检查上下文。
        config: 当前 profile 的循环意图规则配置。

    Returns:
        该函数只追加诊断，不返回业务值。
    """

    # 只在 profile 要求且语句确认为 loop 时检查。
    if not (
        is_loop(context.code)
        and context.preceding_comment
        and config.require_loop_intent
    ):

        # 不满足 loop 意图检查前置条件。
        return

    # 循环注释需提到边界、事务、读写对象或吞吐约束。
    if _contains_any(context.preceding_comment, LOOP_INTENT_KEYWORDS):

        # loop 注释已经包含可接受的意图关键词。
        return

    # 缺少 loop 意图关键词时追加 HG010。
    issues.append(
        make_issue(
            "HG010",
            "error",
            context.rel_path,
            context.line_number,
            "循环注释必须说明迭代边界、事务范围、读写对象或累加/比较目的。",
            detail=context.preceding_comment,
            node_kind="loop",
            code_excerpt=context.code,
        )
    )

# _append_testbench_call_issue 检查 testbench top function 调用说明。
def _append_testbench_call_issue(
    issues: list[HlsGateIssue],
    context: StatementContext,
    top_function: str | None,
) -> None:
    """检查 testbench 调用 top function 前是否说明观测目的。

    Args:
        issues: 调用方维护的诊断列表，会被原地追加。
        context: 当前调用语句的行级检查上下文。
        top_function: 需要被 testbench 调用的 top function 名称。

    Returns:
        该函数只追加诊断，不返回业务值。
    """

    # 非 top function 调用不需要 testbench 专属事务说明。
    if not (top_function and _is_testbench_top_call(context.code, top_function)):

        # 当前语句不是目标 top function 调用。
        return

    # 已有中文注释时视为具备事务或观测目的说明。
    if context.preceding_comment and contains_cjk(context.preceding_comment):

        # top function 调用前说明存在。
        return

    # 缺少中文说明时追加 testbench 调用诊断。
    issues.append(
        make_issue(
            "HG011",
            "error",
            context.rel_path,
            context.line_number,
            "testbench 调用 top function 前必须说明用例事务和观测目的。",
            detail=context.code,
            node_kind="testbench_call",
            code_excerpt=context.code,
        )
    )

# _needs_inline_comment 判断当前局部状态语句是否需要行尾用途注释。
def _needs_inline_comment(code: str, raw_line: str, config: HlsProfileConfig) -> bool:
    """判断局部声明或赋值是否应强制右侧中文注释。

    Args:
        code: 去掉注释后的有效 C/C++ 语句。
        raw_line: 当前源码物理行原文。
        config: 当前 profile 的多行声明豁免配置。

    Returns:
        需要右侧中文用途注释时返回 True。
    """

    # 去掉首尾空白后判断语句形态。
    str_stripped_code = code.strip()  # 当前语句紧凑文本

    # 过长或尚未闭合的物理行不适合强制当前行写右侧注释。
    bool_long_or_open_statement = (  # 当前语句是否属于过长或未闭合的多行形态
        len(str_stripped_code) > config.inline_comment_max_code_chars  # 代码段太长不适合强塞行尾注释
        or not raw_line.strip().endswith(";")  # 当前物理行尚未闭合完整语句
    )

    # 只有 profile 允许时，长行或未闭合行才会触发多行豁免。
    bool_multiline_exempted = config.allow_multiline_inline_comment_exemption and bool_long_or_open_statement  # 当前语句是否命中多行右侧注释豁免

    # 多行豁免启用时不强制当前物理行右侧注释。
    if bool_multiline_exempted:

        # 多行声明由单独规则或人工语义检查覆盖。
        return False

    # 预处理指令和 return 不属于局部状态声明右侧注释规则。
    if str_stripped_code.startswith("#") or str_stripped_code.startswith("return"):

        # 跳过预处理和返回语句。
        return False

    # 其它局部声明或赋值默认需要右侧中文用途注释。
    return True

# _pragma_intent_issues 根据 pragma 类型检查对应硬件意图词。
def _pragma_intent_issues(context: StatementContext) -> list[HlsGateIssue]:
    """检查 pragma 注释是否覆盖具体硬件意图。

    Args:
        context: 当前 pragma 语句的行级检查上下文。

    Returns:
        缺少具体硬件意图关键词时产生的诊断列表。
    """

    # pragma 细分规则诊断按当前 pragma 类型累计。
    list_issues: list[HlsGateIssue] = []  # pragma 意图诊断集合

    # pragma 上下文统一保存大小写归一化结果，供规则表复用。
    pragma_context_pragma_context: PragmaContext = PragmaContext(  # 当前 pragma 的统一检查上下文
        rel_path=context.rel_path,  # 报告路径沿用当前语句上下文
        line=context.line_number,  # 诊断定位沿用当前语句行号
        code=context.code,  # 供 issue.code_excerpt 直接回放的原始 pragma 文本
        comment=context.preceding_comment or "",  # 紧邻上方的 pragma 注释正文
        lowered_code=context.code.lower(),  # 规范化 pragma 文本供 trigger_terms 匹配
        lowered_comment=(context.preceding_comment or "").lower(),  # 小写注释文本供关键词匹配
    )

    # 逐条应用 INTERFACE、PIPELINE、DATAFLOW 等专属意图规则。
    for pragma_spec in PRAGMA_INTENT_SPECS:

        # 每个 spec 只在命中对应 pragma 类型时追加诊断。
        _append_required_pragma_terms_issue(list_issues, pragma_context_pragma_context, pragma_spec)

    # 所有 pragma 都至少要包含一类通用硬件意图关键词。
    if not _contains_any(pragma_context_pragma_context.lowered_comment, PRAGMA_INTENT_KEYWORDS):

        # 记录通用 pragma 硬件意图不足问题。
        list_issues.append(
            make_issue(
                "HG009",
                "error",
                pragma_context_pragma_context.rel_path,
                pragma_context_pragma_context.line,
                "HLS pragma 注释缺少具体硬件、接口或吞吐意图。",
                detail=pragma_context_pragma_context.comment,
                node_kind="pragma",
                code_excerpt=pragma_context_pragma_context.code,
            )
        )

    # 返回该 pragma 的所有细分意图诊断。
    return list_issues

# _append_required_pragma_terms_issue 复用 pragma 类型关键词检查。
def _append_required_pragma_terms_issue(
    issues: list[HlsGateIssue],
    context: PragmaContext,
    spec: PragmaIntentSpec,
) -> None:
    """在 pragma 类型命中但注释缺少必需意图词时追加诊断。

    Args:
        issues: 调用方维护的诊断列表，会被原地追加。
        context: 当前 pragma 的代码、注释和报告位置上下文。
        spec: 当前 pragma 类型的触发词、必需意图词和诊断文本。

    Returns:
        该函数只追加诊断，不返回业务值。
    """

    # 只有 pragma 代码命中当前 spec 的触发词时，才继续检查所需关键词。
    if not _contains_any(context.lowered_code, spec.trigger_terms):

        # pragma 类型不匹配，跳过该组要求。
        return

    # 注释含有必需意图词时视为该类型说明充分。
    if _contains_any(context.lowered_comment, spec.required_terms):

        # 当前注释已经覆盖这类 pragma 的必需意图词。
        return

    # 类型匹配但注释缺少必需关键词时追加诊断。
    issues.append(
        make_issue(
            spec.rule,
            "error",
            context.rel_path,
            context.line,
            spec.message,
            detail=context.comment,
            node_kind="pragma",
            code_excerpt=context.code,
        )
    )

# _testbench_comment_issues 检查 testbench 结果契约是否有注释说明。
def _testbench_comment_issues(lines: list[str], rel_path: str) -> list[HlsGateIssue]:
    """检查 testbench PASS、FAIL 和向量哈希注释契约。

    Args:
        lines: 当前文件的源码物理行。
        rel_path: 报告中使用的文件相对路径。

    Returns:
        testbench 契约缺少中文注释说明时产生的诊断列表。
    """

    # 文件名或 int main 结构用于判断当前文件是否像 testbench。
    bool_is_testbench = "_tb" in rel_path.lower() or any("int main" in code_part(str_line) for str_line in lines)  # 是否 testbench 文件

    # 非 testbench 文件不检查 PASS/FAIL 观测契约。
    if not bool_is_testbench:

        # 普通 HLS 文件不需要 testbench 契约说明。
        return []

    # testbench 诊断在结果契约检查中追加。
    list_issues: list[HlsGateIssue] = []  # testbench 契约诊断集合

    # 先提取 testbench 中所有需要纳入契约判断的注释正文。
    list_joined_comment_parts = [  # testbench 中收集到的归一化注释片段
        normalize_comment_text(str_line)  # 单条注释的归一化正文
        for str_line in lines  # 遍历 testbench 全部源码物理行
        if is_comment_only(str_line) or has_inline_comment(str_line)  # 仅保留纯注释和行尾注释
    ]

    # 再把所有注释正文拼成单个文本，供 PASS/FAIL/hash 检查复用。
    str_joined_comments = "\n".join(list_joined_comment_parts)  # testbench 注释正文全集

    # 汇总完整源码文本，用于判断是否存在 PASS/FAIL/VECTOR_HASH。
    str_text = "\n".join(lines)  # 用于搜索 PASS/FAIL/hash 标记的 testbench 全文

    # PASS 输出出现时，注释必须说明通过条件。
    if "PASS" in str_text and not re.search(r"PASS|通过", str_joined_comments, flags=re.IGNORECASE):

        # 记录 PASS 条件缺少说明的问题。
        list_issues.append(
            make_issue(
                "HG011",
                "error",
                rel_path,
                1,
                "testbench 必须用中文注释说明 PASS 条件。",
                node_kind="testbench_contract",
            )
        )

    # FAIL 输出出现时，注释必须说明失败条件。
    if "FAIL" in str_text and not re.search(r"FAIL|失败", str_joined_comments, flags=re.IGNORECASE):

        # FAIL 标记出现但注释未解释失败条件时追加诊断。
        list_issues.append(
            make_issue(
                "HG011",
                "error",
                rel_path,
                1,
                "testbench 必须用中文注释说明 FAIL 条件。",
                node_kind="testbench_contract",
            )
        )

    # VECTOR_HASH 出现时，注释必须说明向量哈希绑定关系。
    if "VECTOR_HASH" in str_text and not re.search(r"hash|哈希|向量", str_joined_comments, flags=re.IGNORECASE):

        # 记录 vector hash 契约缺少说明的问题。
        list_issues.append(
            make_issue(
                "HG011",
                "error",
                rel_path,
                1,
                "testbench 必须注释说明 vector hash 与参考向量绑定关系。",
                node_kind="testbench_contract",
            )
        )

    # 返回 testbench 契约检查结果。
    return list_issues

# _is_testbench_top_call 判断语句是否调用指定 top function。
def _is_testbench_top_call(code: str, top_function: str) -> bool:
    """判断当前语句是否是 testbench 对 top function 的调用。

    Args:
        code: 当前 C/C++ 语句代码。
        top_function: 需要识别的 top function 名称。

    Returns:
        语句包含 top function 调用且不是函数签名时返回 True。
    """

    # top function 调用必须包含函数名加左括号。
    bool_calls_top = bool(top_function and f"{top_function}(" in code)  # 是否包含 top 调用形态

    # 函数签名不是 testbench 调用点。
    bool_is_signature = is_function_signature(code)  # 是否函数签名

    # 只有真实调用语句才需要 testbench 事务说明。
    return bool_calls_top and not bool_is_signature

# _is_ignorable_declaration_context 过滤无需局部注释规则的声明上下文。
def _is_ignorable_declaration_context(lines: list[str], index: int, depth: int) -> bool:
    """判断当前语句是否应跳过局部声明注释检查。

    Args:
        lines: 当前文件的源码物理行。
        index: 当前语句的零基行下标。
        depth: 当前语句在 C/C++ 代码块中的缩进深度。

    Returns:
        顶层声明或 case/break/continue 等无需局部状态注释的语句返回 True。
    """

    # 提取当前行的有效代码片段用于形态判断。
    str_code = code_part(lines[index]).strip()  # 当前行有效代码

    # 顶层声明通常是函数原型、全局变量或接口声明，不按局部状态检查。
    if depth == 0 and is_local_declaration(str_code):

        # 顶层声明跳过局部声明注释规则。
        return True

    # switch 标签和控制跳转语句不属于局部数据路径状态。
    if re.match(r"^\s*(?:case\b|default:|break;|continue;)", str_code):

        # case/default/break/continue 跳过局部声明注释规则。
        return True

    # 其它语句继续接受常规注释检查。
    return False

# _allowed_non_chinese_comment 识别允许保留英文的工具注释。
def _allowed_non_chinese_comment(text: str) -> bool:
    """判断注释是否属于允许非中文的工具或版权声明。

    Args:
        text: 去掉注释符号后的注释正文。

    Returns:
        注释属于 lint、format、版权等工具保留信息时返回 True。
    """

    # 统一大小写后匹配工具前缀和常见工具关键词。
    str_lowered_text = text.strip().casefold()  # 小写注释正文

    # 前缀匹配只判断是否属于允许透传的工具保留注释。
    bool_has_allowed_prefix = str_lowered_text.startswith(ALLOWED_NON_CHINESE_PREFIXES)  # 是否允许前缀

    # 部分工具标记可能出现在注释中间。
    bool_has_tool_marker = "nolint" in str_lowered_text or "clang-format" in str_lowered_text  # 是否包含工具标记

    # 允许的非中文注释不能用来满足 HLS 语义说明规则。
    return bool_has_allowed_prefix or bool_has_tool_marker

# _is_generic_comment 检查注释是否命中 profile 的模板化短语。
def _is_generic_comment(comment: str, config: HlsProfileConfig) -> bool:
    """判断注释是否包含被配置禁止的模板化表达。

    Args:
        comment: 待检查的注释正文。
        config: 当前 profile 中的泛化注释短语配置。

    Returns:
        注释命中模板化短语时返回 True。
    """

    # 压缩标点和空白后降低绕过模板短语的风险。
    str_compact_comment = re.sub(  # 模板短语匹配使用的紧凑注释文本
        r"[\s`'\"：:，,。；;、（）()\[\]【】]+",  # 需要被折叠掉的空白与标点模式
        "",  # 删除命中的空白与标点
        comment.casefold(),  # 模板匹配统一使用小写注释文本
    )

    # 逐个检查 profile 配置的泛化短语。
    for obj_phrase in config.generic_comment_phrases:

        # 将配置项转成字符串，兼容 JSON/TOML 中的非字符串值。
        str_phrase = str(obj_phrase).casefold()  # 小写泛化短语

        # 去掉空白后和注释紧凑文本对比。
        str_phrase_compact = re.sub(r"\s+", "", str_phrase)  # 紧凑泛化短语

        # 空配置项不参与判断。
        if not str_phrase_compact:

            # 跳过空短语。
            continue

        # 原始和紧凑两种匹配都命中时视为模板化注释。
        if str_phrase_compact in str_compact_comment or str_phrase in comment.casefold():

            # 当前注释命中禁止短语。
            return True

    # 未命中任何泛化短语。
    return False

# _comment_looks_vague 判断注释是否短到无法表达 HLS 语义。
def _comment_looks_vague(comment: str, config: HlsProfileConfig) -> bool:
    """判断注释是否过短或属于空泛名词。

    Args:
        comment: 待检查的注释正文。
        config: 当前 profile 中的空泛注释短语配置。

    Returns:
        注释中文信息量过低或命中空泛短语时返回 True。
    """

    # 归一化注释符号和常见标点，保留中文内容用于长度判断。
    str_compact_comment = re.sub(  # 长度与空泛性判断使用的紧凑注释文本
        r"[\s`'\"：:，,。；;、（）()\[\]【】]+",  # 先移除会干扰中文计数的空白与标点
        "",  # 用空串删除这些噪声字符
        normalize_comment_text(comment),  # 保留归一化后的中文注释正文
    )

    # 统计中文字符数量，避免一两个字的“结果”“变量”通过。
    list_cjk_chars = re.findall(r"[\u4e00-\u9fff]", str_compact_comment)  # 中文字符列表

    # 有中文但少于四个字时通常无法说明硬件或数据路径目的。
    if 0 < len(list_cjk_chars) < 4:

        # 过短中文注释视为空泛。
        return True

    # profile 中的空泛短语集合也会触发该规则。
    set_vague_phrases = set(config.vague_comment_phrases)  # 空泛注释短语集合

    # 完全等于空泛短语时返回 True。
    return str_compact_comment in set_vague_phrases

# _contains_any 提供大小写无关的关键词包含判断。
def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """判断文本是否包含任一关键词。

    Args:
        text: 待检查的文本。
        keywords: 候选关键词集合。

    Returns:
        文本包含任一关键词时返回 True。
    """

    # None 或空字符串统一视作空文本。
    str_lowered_text = (text or "").casefold()  # 小写待检文本

    # 任一关键词出现即可满足该类意图要求。
    return any(str(obj_keyword).casefold() in str_lowered_text for obj_keyword in keywords)

# comment_is_generic_or_vague 是 rewrite plan 复用的注释质量判定入口。
def comment_is_generic_or_vague(comment: str, config: HlsProfileConfig) -> bool:
    """判断注释是否模板化或过于空泛。

    Args:
        comment: 待检查的注释正文。
        config: 当前 profile 的注释质量配置。

    Returns:
        注释命中模板化短语或空泛短语时返回 True。
    """

    # 两类弱注释都需要进入 rewrite plan 的人工语义重写目标。
    return _is_generic_comment(comment, config) or _comment_looks_vague(comment, config)

# collect_comment_quality_targets 为 rewrite plan 收集需要人工重写的注释。
def collect_comment_quality_targets(
    root: Path,
    path: Path,
    config: HlsProfileConfig,
) -> list[dict[str, Any]]:
    """收集模板化或空泛注释的重写目标。

    Args:
        root: 报告相对路径使用的扫描根目录。
        path: 当前被扫描的 HLS 源文件路径。
        config: 当前 profile 的注释质量配置。

    Returns:
        每个目标包含路径、起止行、原因和原始注释详情。
    """

    # 将文件路径转换为 rewrite plan 使用的相对路径。
    str_rel_path = path.relative_to(root).as_posix()  # rewrite plan 输出使用的相对路径

    # 读取源码文本并允许轻微编码问题继续报告。
    str_text = path.read_text(encoding="utf-8", errors="ignore")  # rewrite plan 扫描用源码全文

    # rewrite plan 只记录目标，不生成替换注释文本。
    list_targets: list[dict[str, Any]] = []  # 注释重写目标集合

    # 遍历所有提取到的注释，筛选需要人工处理的弱注释。
    for obj_comment in extract_comments(str_text):

        # 只收集模板化或空泛的注释目标。
        if comment_is_generic_or_vague(obj_comment.text, config):

            # 重写目标保留原始详情，供人工理解上下文后改写。
            list_targets.append(
                {
                    "path": str_rel_path,
                    "start_line": obj_comment.line,
                    "end_line": obj_comment.end_line,
                    "reason": "generic_or_vague_comment",
                    "detail": obj_comment.text,
                }
            )

    # 返回 rewrite plan builder 需要的目标列表。
    return list_targets
