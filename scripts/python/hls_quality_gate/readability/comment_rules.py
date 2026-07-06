"""检查 HLS C/C++ 注释语言、位置和硬件意图。"""
# 延迟解析类型注解，保持 Python 3.10+ 运行兼容。
from __future__ import annotations

# 标准库用于正则判断、路径定位和通用报告载荷。
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
    "文件", "头文件", "源码", "测试",  # 通用文件形态角色词
    "testbench", "内核", "接口", "声明",  # HLS 入口、接口与声明类角色词
    "实现", "验证", "配置",  # 实现、验证与配置类角色词
)

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
    """检查 HLS 文件头中文角色注释。

    Args:
        lines: 当前文件的源码物理行。
        rel_path: 报告中使用的文件相对路径。
        config: 当前 profile 的文件头规则配置。

    Returns:
        文件头规则命中的诊断列表；未启用或通过时为空列表。
    """

    # 未启用文件头要求时不产生任何诊断。
    if not config.require_file_header:

        # 配置允许缺省文件头，直接通过该规则。
        return []

    # 逐行寻找第一条非空内容，判定它是否是合格中文文件头注释。
    for int_line_number, str_raw_line in enumerate(lines, start=1):

        # 空行不承载文件角色信息，继续寻找第一条有意义内容。
        if not str_raw_line.strip():

            # 跳过文件开头的空白行。
            continue

        # 第一条非空内容必须是注释，避免 HLS 文件缺少角色说明。
        if not is_comment_only(str_raw_line):

            # 返回文件头缺失诊断，定位到第一条实际代码。
            return [
                make_issue(
                    "HG007",
                    "error",
                    rel_path,
                    int_line_number,
                    "HLS 文件必须先用中文文件角色注释说明内核、头文件或 testbench 职责。",
                    detail=str_raw_line.strip(),
                    node_kind="file_header",
                )
            ]

        # 提取去掉注释符号后的文件头文本。
        str_comment = normalize_comment_text(str_raw_line)  # 文件头注释正文

        # 文件头必须包含中文，工具标记不能替代文件角色说明。
        if not contains_cjk(str_comment):

            # 返回文件头语言诊断，提示使用中文说明角色。
            return [
                make_issue(
                    "HG007",
                    "error",
                    rel_path,
                    int_line_number,
                    "HLS 文件头注释必须使用中文。",
                    detail=str_comment,
                    node_kind="file_header",
                )
            ]

        # 文件头还必须能说明具体文件角色，不能只是泛泛描述。
        bool_generic_header = _is_generic_comment(str_comment, config)  # 文件头是否模板化

        # 文件角色关键词用于区分普通注释和真实文件头。
        bool_has_role_keyword = _contains_any(str_comment, FILE_HEADER_KEYWORDS)  # 文件头是否含角色词

        # 模板化或缺少角色词都会削弱 HLS 文件职责可读性。
        if bool_generic_header or not bool_has_role_keyword:

            # 返回文件头意图不足诊断。
            return [
                make_issue(
                    "HG007",
                    "error",
                    rel_path,
                    int_line_number,
                    "HLS 文件头注释必须说明具体文件角色，而不是模板化描述。",
                    detail=str_comment,
                    node_kind="file_header",
                )
            ]

        # 第一条非空注释合格后即可结束文件头检查。
        return []

    # 完全空文件无法继续做 HLS 注释可读性判断。
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
