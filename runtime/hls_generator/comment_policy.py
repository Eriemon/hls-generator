"""校验生成的 HLS C/C++ 产物是否满足中文注释和空行策略。"""

# 延迟注解避免导入期解析复杂容器类型。
from __future__ import annotations

# 正则模块负责识别 C/C++ 语句形态、注释文本和中文字符。
import re

# dataclass、路径和类型辅助用于返回稳定的注释策略报告。
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 注释策略只覆盖 HLS workflow 会生成或验证的 C/C++ 源码后缀。
set_hls_source_suffixes = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}  # HLS 源码后缀集合

# 这些模板化短语说明注释没有绑定到具体 HLS 语句语义。
tuple_generic_comment_patterns = (  # 泛化注释黑名单短语；旧 smoke/eval 会搜索 `_GENERIC_COMMENT_PATTERNS` 历史关键词。
    "generic generated line",  # 英文模板注释示例
    "not hardware intent",  # 英文泛化注释短语
    "keep the generated hls artifact line reviewable",  # 英文泛化可读性口号
    "preserve the generated data movement or computation step",  # 英文泛化数据流口号
    "open or close the generated hardware scope",  # 英文泛化作用域口号
    "misplaced top function",  # 英文泛化顶层定位短语
    "do the operation",  # 英文泛化操作短语
    "perform operation",  # 英文泛化执行短语
    "process data",  # 英文泛化数据处理短语
    "handle data",  # 英文泛化数据搬运短语
    "main logic",  # 英文泛化主逻辑短语
    "important code",  # 英文泛化重要性短语
    "generated code",  # 英文泛化生成代码短语
    "this line",  # 英文泛化当前行短语
    "这个语句",  # 中文泛化当前语句短语
    "定义xxx",  # 中文模板化定义短语
    "定义变量",  # 中文泛化变量定义短语
    "保存结果",  # 中文泛化保存结果短语
    "初始化变量",  # 中文泛化初始化短语
    "计算结果",  # 中文泛化计算结果短语
    "处理数据",  # 中文泛化数据处理短语
    "主要逻辑",  # 中文泛化主逻辑短语
    "重要代码",  # 中文泛化重要性短语
    "下方代码",  # 中文泛化下方代码短语
    "下方逻辑",  # 中文泛化下方逻辑短语
    "代码块功能",  # 中文泛化代码块功能短语
    "说明下方代码",  # 中文泛化说明性短语
    "执行函数",  # 中文泛化执行函数短语
    "调用函数",  # 中文泛化调用函数短语
    "返回结果",  # 中文泛化返回短语
    "判断当前分支",  # 中文泛化分支判断短语
    "供后续逻辑使用",  # 中文泛化后续使用短语
    "当前步骤",  # 中文泛化步骤短语
    "模板",  # 中文模板占位短语
    "占位",  # 中文占位短语
)  # 泛化或模板化注释判定短语

# 单字或泛化名词不能作为变量/语句目的说明。
tuple_vague_comment_patterns = (  # 过短中文注释黑名单短语
    "值",  # 单字泛化名词示例
    "代码",  # 泛化到只剩“代码”的短注释
    "结果",  # 泛化到只剩“结果”的短注释
    "数据",  # 泛化到只剩“数据”的短注释
    "逻辑",  # 泛化到只剩“逻辑”的短注释
    "步骤",  # 泛化到只剩“步骤”的短注释
    "变量",  # 泛化到只剩“变量”的短注释
    "函数",  # 泛化到只剩“函数”的短注释
)  # 过于空泛的中文注释短语

# 文件头必须说明 HLS 文件角色，不能只写普通生成说明。
tuple_file_header_keywords = (  # 文件头角色说明关键词
    "文件",  # 通用文件角色关键词
    "头文件",  # 头文件角色关键词
    "源码",  # 源码角色关键词
    "测试",  # 测试角色关键词
    "testbench",  # testbench 角色关键词
    "内核",  # 内核角色关键词
    "接口",  # 接口角色关键词
    "声明",  # 声明角色关键词
    "实现",  # 实现角色关键词
    "验证",  # 验证角色关键词
)  # 文件角色注释中可接受的关键词

# pragma 注释需要覆盖接口、吞吐、通道或缓存等硬件意图。
tuple_pragma_intent_keywords = (  # 通用 pragma 硬件意图关键词；旧 smoke/eval 会搜索 `PRAGMA_INTENT_KEYWORDS` 历史关键词。
    "接口",  # 接口类硬件意图关键词
    "端口",  # 端口类硬件意图关键词
    "协议",  # 协议类硬件意图关键词
    "bundle", "axi", "axis",  # 接口归组与 AXI 总线族关键词
    "m_axi", "s_axilite",  # 访存端口与轻量控制端口关键词
    "流水", "ii", "周期", "吞吐",  # 流水调度与性能指标关键词
    "dataflow", "阶段", "stream", "通道", "fifo",  # 数据流并发与通道缓冲关键词
    "维度", "因子", "factor", "depth", "缓存", "分组", "并行",  # 布局参数、缓冲深度和并行度关键词
)  # HLS pragma 注释必须覆盖的硬件意图关键词

# 循环注释要说明边界、事务范围或读写计算目的。
tuple_loop_intent_keywords = (  # 循环语义覆盖关键词
    "循环",  # 循环类语义关键词
    "遍历",  # 循环遍历动作关键词
    "范围",  # 循环范围关键词
    "边界",  # 循环边界关键词
    "长度",  # 长度类循环关键词
    "事务",  # 事务类循环关键词
    "样本",  # 样本类循环关键词
    "token",  # token 类循环关键词
    "读",  # 读路径循环关键词
    "写",  # 写路径循环关键词
    "累加",  # 累加类循环关键词
    "比较",  # 比较类循环关键词
    "检查",  # 检查类循环关键词
    "ii",  # 循环 II 关键词
    "tripcount",  # tripcount 估计关键词
    "吞吐",  # 循环吞吐关键词
)  # 循环注释可接受的语义关键词

# 简单声明前缀用于从 C/C++ 文本中识别变量声明。
tuple_declaration_prefixes = (  # 局部声明判定前缀
    "const ",  # const 局部声明前缀
    "static ",  # 用于识别静态生命周期局部变量声明
    "volatile ",  # 命中带易变语义的局部声明
    "ap_uint<", "ap_int<",  # 任意宽整数模板声明
    "ap_fixed<", "ap_ufixed<",  # 定点数模板声明
    "hls::stream<", "hls::task",  # stream 或 task 节点声明
    "bool ", "char ", "short ", "int ", "long ", "unsigned ",  # 逻辑位与整数族局部变量
    "float ", "double ",  # 浮点局部变量
    "size_t ", "auto ",  # 索引变量或类型推导局部变量
)  # C/C++ 局部声明常见起始片段

# 控制流关键字用于避免把 if/for 等语句误判为函数签名。
tuple_control_prefixes = (  # 控制流判定关键字
    "if",  # 条件分支关键字
    "for",  # for 控制流前缀
    "while",  # while 循环入口
    "switch", "return",  # 分派与提前返回入口
    "catch", "try", "assert",  # 异常处理与断言入口
)  # 控制流语句关键字

# 函数调用判定需要排除这些语句开头。
tuple_special_starts = ("for", "if", "while", "return", "try", "assert")  # 非普通调用的语句前缀

# INTERFACE pragma 必须说明端口、协议、bundle 或控制接口意图。
tuple_interface_required_keywords = (  # INTERFACE pragma 语义关键词
    "port",  # 端口命名关键词
    "bundle",  # 接口 bundle 关键词
    "protocol", "axi", "axis",  # 协议名与 AXI 总线族关键词
    "m_axi", "s_axilite", "control",  # 访存口、轻量控制口与 control 关键词
    "端口", "协议", "接口",  # 中文接口角色关键词
    "bundle", "控制",  # 分组术语与中文控制语义
)  # INTERFACE pragma 注释关键词

# PIPELINE pragma 必须说明 II、延迟、循环或吞吐目的。
tuple_pipeline_required_keywords = (  # PIPELINE 注释必须交代 II、延迟预算和吞吐目标用词
    "ii",  # 启动间隔关键词
    "initiation",  # initiation interval 英文关键词
    "latency", "tripcount", "loop",  # 延迟、循环规模与迭代背景术语
    "stage", "cycle", "throughput",  # 阶段调度、周期和吞吐术语
    "迭代", "流水", "循环", "周期", "吞吐",  # 中文性能与循环语义术语
)  # PIPELINE pragma 注释关键词

# DATAFLOW pragma 必须说明阶段、通道、流或重叠执行意图。
tuple_dataflow_required_keywords = (  # DATAFLOW 注释必须交代阶段切分、通道搬运和重叠执行语义
    "stage", "channel", "stream",  # 英文阶段与通道搬运关键词
    "fifo", "producer", "consumer",  # FIFO 及生产消费角色关键词
    "阶段", "通道", "流", "重叠",  # 中文 dataflow 并发语义关键词
)  # DATAFLOW pragma 注释关键词

# 数组 pragma 必须说明维度、因子、bank 或缓存布局。
tuple_array_required_keywords = (  # 数组布局 pragma 语义关键词
    "factor",  # 分区因子关键词
    "dim",  # 数组维度英文缩写
    "dimension", "bank", "lane", "buffer",  # 维度、bank、lane 和缓存布局关键词
    "维度", "因子", "缓存", "分组",  # 中文数组布局语义关键词
)  # ARRAY_PARTITION/ARRAY_RESHAPE 注释关键词

# STREAM pragma 必须说明 FIFO 深度、通道或生产消费缓冲关系。
tuple_stream_required_keywords = (  # STREAM pragma 语义关键词
    "depth",  # FIFO 深度关键词
    "fifo",  # FIFO 缓冲关键词
    "stream", "producer", "consumer",  # 通道类型与生产消费角色关键词
    "深度", "通道", "缓冲",  # 中文 STREAM 缓冲语义关键词
)  # STREAM pragma 注释关键词

# 单条注释策略问题，最终会进入 validation 的 HG001 诊断。
@dataclass(frozen=True)
class CommentPolicyIssue:
    """描述一个生成 HLS C/C++ 文件中的注释策略问题。

    参数:
        message: 人类可读问题说明，dtype=str，unit=diagnostic text。
        path: 相对 run 根目录的文件路径，dtype=str，unit=filesystem path。
        line: 问题行号，dtype=int，unit=line number。
        detail: 触发问题的源码或注释片段，dtype=str，unit=source snippet。
    返回:
        dataclass 实例本身作为只读诊断记录，无额外业务返回值。
    """

    # validation 层展示的策略问题说明。
    message: str  # 注释策略问题说明

    # 报告使用相对路径，避免暴露本地绝对路径。
    path: str  # 相对 run 根目录的文件路径

    # 行号保持一基索引，便于用户定位源码。
    line: int  # 一基问题行号

    # detail 保存触发片段，供报告和测试断言定位。
    detail: str  # 触发问题的源码或注释片段

    # 将 dataclass 转换为稳定 JSON 载荷。
    def to_dict(self) -> dict[str, Any]:
        """返回 validation metrics 可序列化的问题字典。

        参数:
            无显式业务参数；字段来自当前 CommentPolicyIssue 实例。
        返回:
            问题字典，shape=(4 fields)，dtype=dict[str, Any]，unit=JSON object。
        """

        # 字段名是 validation metrics 的稳定契约。
        return {
            "path": self.path,
            "line": self.line,
            "detail": self.detail,
            "message": self.message,
        }

# 单行语句策略检查所需的上下文，避免 helper 传入过多并列参数。
@dataclass(frozen=True)
class LinePolicyContext:
    """保存单行 HLS 语句策略检查所需的源码上下文。

    参数:
        lines: 当前 HLS 源文件行列表，shape=(n lines)，dtype=list[str]。
        rel_path: 当前文件相对 run 根目录路径，dtype=str，unit=filesystem path。
        top_function: HLS 顶层函数名，dtype=str，unit=dimensionless。
        line_index: 当前代码行零基索引，dtype=int，unit=line index。
        line_number: 当前代码行一基行号，dtype=int，unit=line number。
        raw_line: 当前原始源码行，dtype=str，unit=source text。
        code: 当前行去注释后的代码片段，dtype=str，unit=source text。
        preceding_comment: 紧邻上方注释文本，dtype=str or None，unit=comment text。
    返回:
        dataclass 实例本身作为单行策略检查上下文。
    """

    # 源文件完整行列表用于回看空行和前置注释。
    lines: list[str]  # 当前 HLS 源文件行列表

    # 相对路径用于构造稳定问题报告。
    rel_path: str  # 当前文件相对 run 根目录的路径

    # 顶层函数名用于识别 testbench 调用。
    top_function: str  # HLS 顶层函数名

    # 当前行零基索引用于读取前后文。
    line_index: int  # 当前代码行零基索引

    # 当前行一基行号用于报告。
    line_number: int  # 当前代码行一基行号

    # 原始源码行用于错误 detail。
    raw_line: str  # 当前原始源码行

    # 去注释后的代码片段用于语句分类。
    code: str  # 当前行代码片段

    # 紧邻上方独立注释文本用于语义策略判断。
    preceding_comment: str | None  # 当前语句前置注释文本

# 单个 pragma 类型的关键词约束。
@dataclass(frozen=True)
class PragmaKeywordRule:
    """描述单类 HLS pragma 注释必须满足的关键词规则。

    参数:
        marker: pragma 类型匹配标记，dtype=str，unit=dimensionless。
        required_keywords: 注释必须包含的关键词，shape=(n words)，dtype=tuple[str, ...]。
        message: 规则失败时的诊断文本，dtype=str，unit=diagnostic text。
    返回:
        dataclass 实例本身作为只读 pragma 规则。
    """

    # marker 用于判断当前 pragma 是否适用该规则。
    marker: str  # pragma 类型匹配标记

    # keywords 定义该 pragma 注释必须包含的语义线索。
    required_keywords: tuple[str, ...]  # pragma 注释关键词集合

    # message 是命中规则失败时写入报告的稳定文本。
    message: str  # pragma 规则失败说明

# pragma 规则检查的运行上下文。
@dataclass(frozen=True)
class PragmaCheckContext:
    """保存单条 HLS pragma 关键词检查所需的规整文本。

    参数:
        rel_path: 当前文件相对路径，dtype=str，unit=filesystem path。
        line_number: pragma 一基行号，dtype=int，unit=line number。
        lowered_code: 小写 pragma 代码，dtype=str，unit=source text。
        lowered_comment: 小写 pragma 注释，dtype=str，unit=comment text。
        original_comment: 原始 pragma 注释，dtype=str，unit=comment text。
    返回:
        dataclass 实例本身作为 pragma 规则检查上下文。
    """

    # rel_path 用于构造问题报告路径。
    rel_path: str  # 当前文件相对路径

    # line_number 是 pragma 所在源码行。
    line_number: int  # pragma 一基行号

    # lowered_code 用于匹配 pragma 类型。
    lowered_code: str  # 小写 pragma 代码

    # lowered_comment 用于匹配英文关键词。
    lowered_comment: str  # 规整为小写后的 pragma 注释

    # original_comment 保留用户看到的原注释文本。
    original_comment: str  # 原始 pragma 注释文本

# 特定 pragma 类型的关键词规则按旧实现顺序执行。
tuple_pragma_keyword_rules = (  # _pragma_policy_issues 会顺序遍历这张表，先用 marker 选中规则，再取对应关键词集合和英文报错文本生成诊断
    PragmaKeywordRule(  # INTERFACE pragma 需要端口、协议或 control 语义
        marker="interface",  # 只有 pragma 源码里出现 interface 标记时才套用端口与协议语义检查
        required_keywords=tuple_interface_required_keywords,  # INTERFACE 注释必须覆盖的关键词集合
        message="HLS INTERFACE pragma comment must name port/protocol/bundle/control intent.",  # INTERFACE 缺失语义时的诊断文本
    ),
    PragmaKeywordRule(  # PIPELINE pragma 需要 II、延迟或吞吐语义
        marker="pipeline",  # 只有带 pipeline 标记的 pragma 才进入本规则
        required_keywords=tuple_pipeline_required_keywords,  # 用 II、latency、loop 或 throughput 词汇证明流水调度意图
        message="HLS PIPELINE pragma comment must state II/latency/loop/throughput intent.",  # 注释漏掉流水调度目标时返回的英文诊断
    ),
    PragmaKeywordRule(  # DATAFLOW pragma 需要阶段、通道或重叠执行语义
        marker="dataflow",  # 仅在 pragma 明确声明 dataflow 并发阶段时触发该规则
        required_keywords=tuple_dataflow_required_keywords,  # 用阶段、通道或重叠执行词汇证明 dataflow 并发意图
        message="HLS DATAFLOW pragma comment must name stages/channels/stream overlap intent.",  # 注释漏掉阶段并发语义时返回的英文诊断
    ),
)  # HLS pragma 专用关键词规则表

# 公开入口：审查本轮生成的所有 HLS C/C++ 产物。
def validate_hls_comment_policy(
    root: Path,
    hls_files: list[Path],
    *,
    top_function: str,
) -> tuple[list[CommentPolicyIssue], dict[str, Any]]:
    """按项目中文注释策略校验生成的 HLS 源码。

    参数:
        root: HLS run 根目录，dtype=Path，unit=filesystem path。
        hls_files: 候选 HLS 源码路径列表，shape=(n files)，dtype=list[Path]。
        top_function: HLS 顶层函数名，dtype=str，unit=dimensionless。
    返回:
        问题列表和 metrics 字典，shape=(issues, metrics)，unit=JSON-ready tuple。
    """

    # 所有文件问题先汇总，再写入 metrics["issues"]。
    list_issues: list[CommentPolicyIssue] = []  # 当前 run 的注释策略问题列表

    # metrics 字段名由 validation.py 和 smoke 测试消费，必须保持稳定。
    dict_metrics: dict[str, Any] = {  # 注释策略汇总指标字典
        "policy": "strict_chinese_hls_comment_spacing",  # 当前策略标识
        "checked_files": [],  # 已检查的 HLS 文件相对路径列表
        "checked_structures": 0,  # 已覆盖的语义结构数量
        "issues": [],  # 累积的注释策略问题列表
        "top_function": top_function,  # 顶层函数名上下文
    }  # 注释策略检查汇总指标

    # 逐文件审查，跳过非 C/C++ 源码候选。
    for path_hls_file in hls_files:

        # 后缀过滤保持旧版只审查 HLS 源文件的行为边界。
        if path_hls_file.suffix.lower() not in set_hls_source_suffixes:

            # 非 HLS 源码不计入 checked_files。
            continue

        # 报告路径始终使用 run 根目录相对路径。
        str_rel_path: str = path_hls_file.relative_to(root).as_posix()  # 报告中的相对文件路径

        # 文件读取容忍编码异常，避免单个脏字符阻塞静态策略检查。
        list_lines: list[str] = path_hls_file.read_text(encoding="utf-8", errors="ignore").splitlines()  # 当前 HLS 文件逐行文本

        # _validate_file 会同时返回问题列表和命中的结构数量，供文件级指标累计。
        tuple_file_result: tuple[list[CommentPolicyIssue], int] = _validate_file(  # 单文件检查结果二元组
            list_lines,  # 提供给 _validate_file 的原始逐行文本
            str_rel_path,  # 回填到报告的 run 内相对路径
            top_function=top_function,  # testbench 顶层调用校验使用的函数名
        )  # 单文件注释策略审查结果

        # 解包后分别累积问题列表和结构计数。
        list_file_issues: list[CommentPolicyIssue] = tuple_file_result[0]  # 当前文件问题列表

        # checked_structures 用于报告策略覆盖规模。
        int_checked_structures: int = tuple_file_result[1]  # 当前文件已检查结构数

        # 汇总当前文件问题。
        list_issues.extend(list_file_issues)

        # 记录已审查文件，便于用户定位策略覆盖范围。
        dict_metrics["checked_files"].append(str_rel_path)

        # 累加结构计数。
        dict_metrics["checked_structures"] += int_checked_structures  # 累加当前文件结构计数

    # metrics 内的问题以稳定字典形态输出。
    dict_metrics["issues"] = [issue.to_dict() for issue in list_issues]  # 注释策略问题 JSON 列表

    # 返回对象列表供 validation 生成 HG001，同时返回 metrics 供报告消费。
    return list_issues, dict_metrics

# 校验单个 HLS 源文件的文件头、注释语言、空行块和语句级策略。
def _validate_file(
    lines: list[str],
    rel_path: str,
    *,
    top_function: str,
) -> tuple[list[CommentPolicyIssue], int]:
    """校验单个 HLS 源文件的文件头、注释语言、空行块和语句级策略。

    参数:
        lines: 当前 HLS 源文件的逐行文本列表。
        rel_path: 当前文件相对 run 根目录的路径。
        top_function: 用于识别 testbench 顶层调用的 HLS 顶层函数名。
    返回:
        当前文件的问题列表和已检查结构数。
    """

    # 单文件问题列表按源码顺序追加，保证报告稳定。
    list_issues: list[CommentPolicyIssue] = []  # 当前文件的注释策略问题

    # 这个计数既统计合格文件头，也统计通过入口筛选的有效语句。
    int_checked_structures: int = 0  # 当前文件覆盖到的结构数量

    # 文件头检查把单个可选问题收束成列表，后续可以直接统一 extend。
    list_header_issues: list[CommentPolicyIssue] = [  # 文件头问题列表
        comment_policy_issue  # 文件头 helper 返回的问题对象
        for comment_policy_issue in [_file_header_issue(lines, rel_path)]  # 文件头 helper 的可选返回值
        if comment_policy_issue is not None  # 过滤掉文件头合格时的空结果
    ]  # 文件头注释策略问题列表

    # 文件头缺失或不合格时直接记录问题。
    if list_header_issues:

        # 文件头问题进入同一问题列表。
        list_issues.extend(list_header_issues)

    # 文件头合格后才把文件头本身计入覆盖规模。
    else:

        # 合格文件头计入覆盖统计。
        int_checked_structures += 1  # 合格文件头计入结构统计

    # 全文件注释语言和模板化注释检查不依赖具体语句。
    list_issues.extend(_comment_language_issues(lines, rel_path))

    # 空行分隔块必须由中文语义注释开启。
    list_issues.extend(_blank_line_block_issues(lines, rel_path))

    # 行级策略返回问题列表和语句覆盖数量，供文件级汇总复用。
    tuple_line_result: tuple[list[CommentPolicyIssue], int] = _line_policy_issues(lines, rel_path, top_function)  # 行级检查结果二元组

    # 行级问题追加到文件问题列表。
    list_line_issues: list[CommentPolicyIssue] = tuple_line_result[0]  # 行级问题列表

    # 行级结构计数单独解包，避免 tuple 语义不清。
    int_line_checked_structures: int = tuple_line_result[1]  # 行级已检查结构数

    # 合并所有行级诊断。
    list_issues.extend(list_line_issues)

    # 合并结构计数。
    int_checked_structures += int_line_checked_structures  # 累加行级结构统计

    # 返回单文件问题和覆盖规模。
    return list_issues, int_checked_structures

# 扫描每一行有意义代码并分派到语句级策略。
def _line_policy_issues(
    lines: list[str],
    rel_path: str,
    top_function: str,
) -> tuple[list[CommentPolicyIssue], int]:
    """扫描文件中的可审查代码行并汇总语句级注释策略问题。

    参数:
        lines: 当前 HLS 源文件的逐行文本列表。
        rel_path: 当前文件相对 run 根目录的路径。
        top_function: 用于识别 testbench 顶层调用的 HLS 顶层函数名。
    返回:
        行级问题列表和已检查的语句结构数量。
    """

    # 行级问题列表只保存依赖具体代码行的策略失败。
    list_issues: list[CommentPolicyIssue] = []  # 行级注释策略问题

    # 每条进入语句级审查的有效代码都要计入覆盖规模。
    int_checked_structures: int = 0  # 行级覆盖到的语句数量

    # enumerate 保持源码顺序，行号在内部转换为一基索引。
    for int_index, str_raw_line in enumerate(lines):

        # 去掉行内注释后的代码部分用于语句分类。
        str_code: str = _code_part(str_raw_line).strip()  # 当前行去注释后的代码片段

        # 空行、纯注释和闭合括号不需要单独目的注释。
        if not _should_check_line(str_code, str_raw_line):

            # 当前行没有可审查语义结构。
            continue

        # 每个可审查语句都计入结构覆盖。
        int_checked_structures += 1  # 当前语句计入结构统计

        # 报告行号使用一基索引。
        int_line_number: int = int_index + 1  # 当前源码行号

        # 仅允许紧邻上一行的独立注释解释当前语句。
        str_preceding_comment: str | None = _immediate_preceding_comment(lines, int_index)  # 当前语句紧邻上一行的独立注释

        # 行策略上下文把当前语句的源码、路径和注释信息合在一起。
        line_policy_context_current: LinePolicyContext = LinePolicyContext(  # 当前语句标准化检查上下文
            # 文件级上下文用于回看注释和输出相对路径。
            lines=lines,  # 用于回看相邻注释与空行边界的整文件文本
            rel_path=rel_path,  # 诊断输出使用的相对文件路径
            top_function=top_function,  # 顶层函数名用于 testbench 调用语义判断

            # 行级上下文用于构造诊断和语句分类。
            line_index=int_index,  # 当前源码行的零基索引
            line_number=int_line_number,  # 当前源码行的一基行号
            raw_line=str_raw_line,  # 保留原始源码文本用于 detail
            code=str_code,  # 去掉尾注释后的可执行代码片段

            # 前置注释用于判断中文语义覆盖。
            preceding_comment=str_preceding_comment,  # 与当前语句直接绑定的上一行中文注释
        )  # 当前语句策略上下文

        # 单行的各类策略检查拆到 helper，降低主循环复杂度。
        list_statement_issues: list[CommentPolicyIssue] = _statement_policy_issues(line_policy_context_current)  # 当前语句的策略问题列表

        # 保留 helper 内部追加顺序。
        list_issues.extend(list_statement_issues)

    # 返回行级问题和结构数量。
    return list_issues, int_checked_structures

# 判断当前行是否属于需要注释策略审查的非平凡代码。
def _should_check_line(code: str, raw_line: str) -> bool:
    """判断当前代码行是否需要进入语句级注释策略审查。

    参数:
        code: 去掉注释后的代码片段。
        raw_line: 原始源码行文本。
    返回:
        True 表示当前行属于需要继续检查的非平凡代码；False 表示可直接跳过。
    """

    # 空代码片段不参与审查。
    if not code:

        # 空行或只有注释。
        return False

    # 纯注释行已由注释语言策略处理。
    if _is_comment_only(raw_line):

        # 纯注释不是代码结构。
        return False

    # 闭合括号和 else 这类结构标记不需要目的注释。
    if _is_trivial_line(code):

        # 平凡结构符号不计入策略覆盖。
        return False

    # 其余代码行需要进入语句级检查。
    return True

# 针对单条代码语句生成所有相关注释策略问题。
def _statement_policy_issues(context: LinePolicyContext) -> list[CommentPolicyIssue]:
    """为单条 HLS 代码语句生成所有适用的注释策略问题。

    参数:
        context: 当前语句的源码、路径和前置注释上下文。
    返回:
        当前语句触发的注释策略问题列表。
    """

    # 当前语句问题列表按旧逻辑顺序追加。
    list_issues: list[CommentPolicyIssue] = []  # 当前语句注释策略问题

    # 复用空字符串避免多个分支重复处理 None。
    str_comment_text: str = context.preceding_comment or ""  # 当前语句上方注释文本

    # 通用模板注释不能作为任何 HLS 语句说明。
    if _is_generic_comment(str_comment_text):

        # 报告仍包含原始注释文本。
        list_issues.append(
            _issue(
                context.rel_path,
                context.line_number,
                "HLS comment is generic or template-like; rewrite it from the concrete code purpose.",
                str_comment_text,
            )
        )

    # 关键语句必须有空行加中文目的注释。
    if _requires_blank_plus_comment(context.code, context.top_function):

        # 缺失空行或中文注释都会触发同一策略问题。
        if not _has_blank_plus_chinese_comment_above(context.lines, context.line_index):

            # detail 使用当前源码行，便于用户定位。
            list_issues.append(
                _issue(
                    context.rel_path,
                    context.line_number,
                    (
                        "HLS statement must have one blank line plus an immediate "
                        "Chinese purpose comment above it."
                    ),
                    context.raw_line.strip(),
                )
            )

    # HLS pragma 需要独立说明硬件意图。
    list_issues.extend(
        _pragma_policy_issues(
            context.rel_path,
            context.line_number,
            context.code,
            context.preceding_comment,
        )
    )

    # 声明和赋值必须说明在 datapath 中的目的。
    list_variable_issues: list[CommentPolicyIssue] = _variable_policy_issues(  # 变量声明与赋值问题列表
        context.rel_path,  # 变量问题回报到原始 HLS 文件相对路径
        context.line_number,  # 触发变量或赋值问题的源码行号
        context.raw_line,  # 原始源码行文本
        context.code,  # 去掉尾注释后的代码片段
        context.preceding_comment,  # 与当前语句绑定的前置中文注释
    )  # 当前语句变量声明或赋值注释问题

    # 变量问题单独累加，避免长实参块过密。
    list_issues.extend(list_variable_issues)

    # testbench 顶层调用条件拆成具名布尔值，便于阅读和压缩行宽。
    bool_missing_top_call_comment: bool = (  # 顶层调用是否缺少中文目的注释
        _is_testbench_top_call(context.code, context.top_function)  # 顶层调用形态是否命中
        and not _has_chinese_comment(context.preceding_comment)  # 前置注释是否已经覆盖顶层调用目的
    )  # 顶层函数调用是否缺少中文测试说明

    # 循环注释需要覆盖边界、事务范围或读写意图。
    if _is_loop(context.code) and context.preceding_comment:

        # 循环专用策略保留原错误文本。
        list_issues.extend(
            _specific_loop_issues(
                context.rel_path,
                context.line_number,
                context.preceding_comment,
            )
        )

    # testbench 顶层调用必须说明测试用例或 transaction。
    if bool_missing_top_call_comment:

        # 顶层调用没有中文说明时阻塞。
        list_issues.append(
            _issue(
                context.rel_path,
                context.line_number,
                (
                    "Testbench top-function call must have a Chinese comment "
                    "explaining the case/transaction being exercised."
                ),
                context.code,
            )
        )

    # 返回当前语句所有问题。
    return list_issues

# 这个 helper 会先做通用硬件意图检查，再按 pragma marker 追加专项缺词诊断。
def _pragma_policy_issues(
    rel_path: str,
    line_number: int,
    code: str,
    preceding_comment: str | None,
) -> list[CommentPolicyIssue]:
    """检查 HLS pragma 语句是否具备独立中文硬件意图注释。

    参数:
        rel_path: 当前文件相对 run 根目录的路径。
        line_number: 当前 pragma 的一基行号。
        code: 当前 pragma 代码文本。
        preceding_comment: 紧邻 pragma 上方的独立注释文本。
    返回:
        当前 pragma 触发的注释策略问题列表。
    """

    # 非 HLS pragma 不进入该策略。
    if not _is_hls_pragma(code):

        # 普通语句不产生 pragma 问题。
        return []

    # pragma 问题列表按旧顺序返回。
    list_issues: list[CommentPolicyIssue] = []  # 当前 pragma 注释策略问题

    # HLS pragma 必须由上一行独立中文注释说明。
    if not preceding_comment:

        # 缺少独立注释时保留旧错误文本。
        list_issues.append(
            _issue(
                rel_path,
                line_number,
                "#pragma HLS must be explained by the standalone Chinese comment directly above it, not only by an inline comment.",
                code,
            )
        )

        # 无注释时无法继续做语义关键词检查。
        return list_issues

    # 有注释时继续检查特定 pragma 类型关键词。
    list_issues.extend(_specific_pragma_issues(rel_path, line_number, code, preceding_comment))

    # 返回 pragma 策略问题。
    return list_issues

# 检查变量声明和赋值上方注释是否足够具体。
def _variable_policy_issues(
    rel_path: str,
    line_number: int,
    raw_line: str,
    code: str,
    preceding_comment: str | None,
) -> list[CommentPolicyIssue]:
    """检查变量声明或赋值语句上方注释是否满足中文具体性要求。

    参数:
        rel_path: 当前文件相对 run 根目录的路径。
        line_number: 当前语句的一基行号。
        raw_line: 当前原始源码行文本。
        code: 去掉注释后的代码片段。
        preceding_comment: 紧邻上方的独立注释文本。
    返回:
        当前变量语句触发的注释策略问题列表。
    """

    # 非变量声明或赋值无需进入该策略。
    if not (_is_local_declaration(code) or _is_assignment(code)):

        # 当前语句不是变量策略对象。
        return []

    # 变量策略最多返回一个问题。
    list_issues: list[CommentPolicyIssue] = []  # 当前变量语句注释策略问题

    # 变量语句必须有中文目的注释。
    if not _has_chinese_comment(preceding_comment):

        # detail 使用原始行，包含声明或赋值上下文。
        list_issues.append(
            _issue(
                rel_path,
                line_number,
                "Variable declaration or assignment must have a Chinese purpose comment above it.",
                raw_line.strip(),
            )
        )

        # 没有中文注释时不再判断注释是否空泛。
        return list_issues

    # 空泛注释同样视为策略问题。
    if preceding_comment and _comment_looks_vague(preceding_comment):

        # detail 保留原注释，便于用户重写。
        list_issues.append(
            _issue(
                rel_path,
                line_number,
                "Variable comment must explain purpose/function in this datapath rather than a vague noun or action.",
                preceding_comment,
            )
        )

    # 返回变量策略问题。
    return list_issues

# 判断可选注释文本是否包含中文语义。
def _has_chinese_comment(comment: str | None) -> bool:
    """判断可选注释文本是否包含中文语义。

    参数:
        comment: 待检查的注释文本，可为 `None`。
    返回:
        True 表示注释包含中文字符；False 表示没有中文语义。
    """

    # None 或空字符串都不满足中文注释要求。
    if not comment:

        # 没有注释文本。
        return False

    # 只要包含中文字符即可通过语言层检查。
    return _contains_cjk(comment)

# 校验文件首个有效行是否为具体中文文件角色注释。
def _file_header_issue(lines: list[str], rel_path: str) -> CommentPolicyIssue | None:
    """检查文件首个有效行是否为具体中文文件角色注释。

    参数:
        lines: 当前 HLS 源文件的逐行文本列表。
        rel_path: 当前文件相对 run 根目录的路径。
    返回:
        发现问题时返回单条文件头诊断；否则返回 `None`。
    """

    # 从文件顶部跳过空行，寻找首个有效行。
    for int_index, str_line in enumerate(lines):

        # 顶部空行不影响文件头判定。
        if not str_line.strip():

            # 继续寻找首个非空行。
            continue

        # 首个有效行必须是独立注释。
        if not _is_comment_only(str_line):

            # 文件头缺失时报告首个代码行。
            return _issue(
                rel_path,
                int_index + 1,
                "HLS file must begin with a Chinese file-role header comment before code.",
                str_line.strip(),
            )

        # 去掉注释符号后做语言和具体性检查。
        str_comment: str = _comment_text(str_line)  # 文件头注释正文

        # 文件头必须使用中文。
        if not _contains_cjk(str_comment):

            # 非中文文件头不满足项目风格。
            return _issue(
                rel_path,
                int_index + 1,
                "HLS file header comment must be Chinese.",
                str_comment,
            )

        # 文件头需要描述具体文件角色。
        if _is_generic_comment(str_comment) or not _contains_any(
            str_comment,
            tuple_file_header_keywords,
        ):

            # 泛化文件头无法说明 artifact 职责。
            return _issue(
                rel_path,
                int_index + 1,
                "HLS file header must describe the concrete file role.",
                str_comment,
            )

        # 找到合格文件头。
        return None

    # 空文件没有可审查头注释。
    return _issue(rel_path, 1, "HLS file must not be empty.", "")

# 全文件扫描注释语言和模板化注释。
def _comment_language_issues(lines: list[str], rel_path: str) -> list[CommentPolicyIssue]:
    """扫描全文件注释并收集语言或模板化问题。

    参数:
        lines: 当前 HLS 源文件的逐行文本列表。
        rel_path: 当前文件相对 run 根目录的路径。
    返回:
        注释语言和模板化相关的问题列表。
    """

    # 注释语言问题按出现顺序输出。
    list_issues: list[CommentPolicyIssue] = []  # 注释语言策略问题列表

    # 每一行可能包含独立注释或行内注释。
    for int_index, str_line in enumerate(lines):

        # 拆出当前行所有可见注释片段。
        for str_comment in _comments_in_line(str_line):

            # 去掉注释符号后做策略判断。
            str_text: str = _comment_text(str_comment)  # 当前注释正文

            # 工具控制注释允许使用英文协议文本。
            if _allowed_non_chinese_comment(str_text):

                # 当前注释属于工具控制协议。
                continue

            # 生成 HLS 注释必须使用中文。
            if not _contains_cjk(str_text):

                # 非中文注释阻塞当前策略。
                list_issues.append(
                    _issue(
                        rel_path,
                        int_index + 1,
                        "All generated HLS comments must be Chinese.",
                        str_text,
                    )
                )

                # 非中文注释不再做中文空泛性判断。
                continue

            # 中文注释仍需绑定具体代码职责。
            if _is_generic_comment(str_text) or _comment_looks_vague(str_text):

                # 模板化或空泛注释也会导致 HLS 产物不可审。
                list_issues.append(
                    _issue(
                        rel_path,
                        int_index + 1,
                        (
                            "HLS comment must be specific to the surrounding code "
                            "responsibility and must not be a fixed/template phrase."
                        ),
                        str_text,
                    )
                )

    # 返回全文件注释语言问题。
    return list_issues

# 检查空行分隔的下一个代码块是否以独立中文注释开头。
def _blank_line_block_issues(lines: list[str], rel_path: str) -> list[CommentPolicyIssue]:
    """检查空行分隔后的代码块是否以独立中文注释开头。

    参数:
        lines: 当前 HLS 源文件的逐行文本列表。
        rel_path: 当前文件相对 run 根目录的路径。
    返回:
        空行块分隔策略触发的问题列表。
    """

    # 空行块问题列表按源码顺序输出。
    list_issues: list[CommentPolicyIssue] = []  # 空行块注释策略问题

    # 手动索引允许跳过连续空行。
    int_index: int = 0  # 当前扫描行索引

    # 扫描整个文件，识别空行分隔上下代码块的边界。
    while int_index < len(lines):

        # 非空行直接前进。
        if lines[int_index].strip():

            # 移动到下一行。
            int_index += 1  # 跳过非空行

            # 当前行不是空行分隔符。
            continue

        # 空行上方最近的有效代码决定上半块是否真正存在。
        int_previous_code: int | None = _previous_meaningful_code_index(lines, int_index)  # 空行上方最近的有效代码索引

        # 跳过连续空行，定位下方代码块。
        while int_index < len(lines) and not lines[int_index].strip():

            # 消费当前空行。
            int_index += 1  # 消费连续空行

        # 借助下方最近的有效代码判断这段空行是否真的把两个代码块隔开。
        int_next_code: int | None = _next_meaningful_code_index(lines, int_index)  # 用来确认空行后方是否开启新代码块

        # 文件边缘空行不构成两个代码块之间的分隔。
        if int_previous_code is None or int_next_code is None:

            # 没有上下两个代码块。
            continue

        # 下方代码块应该由其上一行的独立中文注释开启。
        int_comment_index: int = int_next_code - 1  # 下方代码块的候选注释行索引

        # 候选注释缺失或不是中文时报告下方代码行。
        if not _is_chinese_comment_line(lines, int_comment_index):

            # 下方代码块缺少独立中文目的注释。
            list_issues.append(
                _issue(
                    rel_path,
                    int_next_code + 1,
                    (
                        "When a blank line separates HLS code blocks, the lower code "
                        "block must begin with a standalone Chinese purpose comment."
                    ),
                    lines[int_next_code].strip(),
                )
            )

    # 返回空行块策略问题。
    return list_issues

# 判断指定行是否存在独立中文注释。
def _is_chinese_comment_line(lines: list[str], index: int) -> bool:
    """判断指定源码行是否是带中文正文的独立注释。

    参数:
        lines: 按行拆分后的 HLS 源码文本。
        index: 待判断的一基前零基源码行索引。

    返回:
        True 表示目标行是独立中文注释；False 表示越界、不是独立注释或缺少中文正文。
    """

    # 越界索引无法作为注释行。
    if index < 0 or index >= len(lines):

        # 没有候选注释行。
        return False

    # 候选行必须是纯注释。
    if not _is_comment_only(lines[index]):

        # 不是独立注释行。
        return False

    # 注释正文必须包含中文。
    return _contains_cjk(_comment_text(lines[index]))

# 对不同 HLS pragma 类型执行关键词检查。
def _specific_pragma_issues(
    rel_path: str,
    line_number: int,
    code: str,
    comment: str,
) -> list[CommentPolicyIssue]:
    """收集单条 HLS pragma 注释的专用语义问题。

    参数:
        rel_path: 当前 HLS 文件相对 run 根目录的路径。
        line_number: pragma 所在的一基源码行号。
        code: 当前 pragma 对应的源码文本。
        comment: pragma 上方或同行关联的注释文本。

    返回:
        当前 pragma 触发的专用注释策略问题列表；无问题时返回空列表。
    """

    # pragma 问题按旧实现顺序累计。
    list_issues: list[CommentPolicyIssue] = []  # pragma 语义注释问题列表

    # pragma 源码先转小写，便于匹配 interface/pipeline/dataflow 等指令标记。
    str_lowered_code: str = code.lower()  # 归一化后的 pragma 源码

    # pragma 注释转小写后主要服务英文术语、缩写和协议词的命中判断。
    str_lowered_comment: str = comment.lower()  # 供 pragma 关键词规则复用的小写注释视图

    # 统一封装 pragma 的源码、注释与定位信息，避免多套规则重复拆参。
    pragma_check_context_current: PragmaCheckContext = PragmaCheckContext(  # 表驱动规则与专项规则共享的 pragma 诊断上下文
        rel_path=rel_path,  # issue 输出回到原始 HLS 文件路径
        line_number=line_number,  # issue 需要回指的 pragma 原始源码行号
        lowered_code=str_lowered_code,  # 用于匹配 interface/pipeline/dataflow 标记的规整源码
        lowered_comment=str_lowered_comment,  # 用于匹配 port、ii、fifo 等关键词的小写注释
        original_comment=comment,  # 诊断 detail 回显使用的原始注释文本
    )

    # 先按 INTERFACE、PIPELINE、DATAFLOW 的旧顺序执行表驱动规则。
    for pragma_rule in tuple_pragma_keyword_rules:

        # 每条规则只在 marker 命中时产生问题。
        list_issues.extend(_pragma_keyword_issue(pragma_check_context_current, pragma_rule))

    # 数组布局类 pragma 还要单独核对维度、因子和 bank 语义。
    list_issues.extend(_array_pragma_issues(pragma_check_context_current))

    # STREAM pragma 还要补查 FIFO 深度和缓冲通道语义。
    list_issues.extend(_stream_pragma_issues(pragma_check_context_current))

    # 所有 HLS pragma 都需要至少一个通用硬件意图关键词。
    if not _contains_any(str_lowered_comment, tuple_pragma_intent_keywords):

        # 泛化 pragma 注释不能通过。
        list_issues.append(
            _issue(
                    rel_path,
                    line_number,
                    "HLS pragma comment must explain concrete hardware/interface/throughput intent.",
                    comment,
                )
        )

    # 返回所有 pragma 问题。
    return list_issues

# 根据单个 pragma 关键字规则返回问题列表。
def _pragma_keyword_issue(
    context: PragmaCheckContext,
    rule: PragmaKeywordRule,
) -> list[CommentPolicyIssue]:
    """按单条 pragma 关键字规则生成缺失语义问题。

    参数:
        context: 当前 pragma 的标准化检查上下文。
        rule: 当前要应用的 pragma 关键字规则。

    返回:
        命中规则但缺少必需关键词时返回单条问题列表；否则返回空列表。
    """

    # 非当前 pragma 类型无需检查。
    if rule.marker not in context.lowered_code:

        # 该规则不适用于当前 pragma。
        return []

    # 命中类型后必须包含对应关键词。
    if _contains_any(context.lowered_comment, rule.required_keywords):

        # 注释包含当前 pragma 所需语义。
        return []

    # 返回单条特定 pragma 问题。
    return [
        _issue(
            context.rel_path,
            context.line_number,
            rule.message,
            context.original_comment,
        )
    ]

# 检查数组布局 pragma 是否说明了分区或 reshape 的硬件意图。
def _array_pragma_issues(context: PragmaCheckContext) -> list[CommentPolicyIssue]:
    """检查数组布局 pragma 是否覆盖维度和布局语义。

    参数:
        context: 当前 pragma 的标准化检查上下文。

    返回:
        数组布局 pragma 缺少维度、因子或 bank 语义时返回问题列表；否则返回空列表。
    """

    # 非数组布局 pragma 无需检查。
    if "array_partition" not in context.lowered_code and "array_reshape" not in context.lowered_code:

        # lowered_code 未命中 array_partition 或 array_reshape 时直接短路返回。
        return []

    # 只要注释提到维度、因子、bank 或缓冲布局中的任一语义即可通过。
    if _contains_any(context.lowered_comment, tuple_array_required_keywords):

        # 注释已说明维度、因子或缓存布局。
        return []

    # 返回数组布局 pragma 的缺失语义问题。
    return [
        _issue(
            context.rel_path,
            context.line_number,
            "HLS array pragma comment must state dimension/factor/banking/buffer intent.",
            context.original_comment,
        )
    ]

# STREAM pragma 额外要求注释交代 FIFO 深度或生产者/消费者缓冲角色。
def _stream_pragma_issues(context: PragmaCheckContext) -> list[CommentPolicyIssue]:
    """检查 HLS STREAM pragma 是否说明 FIFO 缓冲意图。

    参数:
        context: 当前 pragma 的标准化检查上下文。

    返回:
        STREAM pragma 缺少 FIFO 深度或通道缓冲语义时返回问题列表；否则返回空列表。
    """

    # 只有精确的 HLS STREAM pragma 进入该策略。
    if "stream" not in context.lowered_code or "#pragma hls stream" not in context.lowered_code:

        # 只有显式 HLS STREAM pragma 才需要检查 FIFO 缓冲语义。
        return []

    # stream 注释包含 FIFO、深度或通道关键词即可通过。
    if _contains_any(context.lowered_comment, tuple_stream_required_keywords):

        # 注释已经覆盖 FIFO 深度或通道缓冲语义。
        return []

    # 生成 stream pragma 的缺失诊断，提醒补齐 FIFO 深度或通道缓冲职责。
    return [
        _issue(
            context.rel_path,
            context.line_number,
            "HLS STREAM pragma comment must state FIFO/depth/channel buffering intent.",
            context.original_comment,
        )
    ]

# 检查循环注释是否覆盖循环相关语义。
def _specific_loop_issues(rel_path: str, line_number: int, comment: str) -> list[CommentPolicyIssue]:
    """检查循环注释是否覆盖边界和读写类意图。

    参数:
        rel_path: 当前 HLS 文件相对 run 根目录的路径。
        line_number: 循环语句所在的一基源码行号。
        comment: 当前循环关联的注释文本。

    返回:
        循环注释缺少边界、事务或读写目的时返回问题列表；否则返回空列表。
    """

    # 命中任一循环意图关键词即可通过。
    if _contains_any(comment, tuple_loop_intent_keywords):

        # 循环注释足够具体。
        return []

    # 缺少边界、事务或读写说明时报告。
    return [
        _issue(
            rel_path,
            line_number,
            "Loop comment must explain iteration bounds, transaction range, or concrete read/write/compare purpose.",
            comment,
        )
    ]

# 检查语句上方是否满足空行加中文独立注释。
def _has_blank_plus_chinese_comment_above(lines: list[str], index: int) -> bool:
    """判断目标语句上方是否存在合格的空行与中文独立注释。

    参数:
        lines: 按行拆分后的 HLS 源码文本。
        index: 待检查语句的零基源码行索引。

    返回:
        True 表示语句上方满足空行加中文独立注释契约；False 表示缺少其中任一条件。
    """

    # 语句上一行必须是注释。
    int_comment_index: int = index - 1  # 候选注释行索引

    # 缺少上一行或上一行不是独立注释时失败。
    if not _is_chinese_comment_line(lines, int_comment_index):

        # 没有合格中文注释。
        return False

    # 中文注释上方必须是空行或文件开头。
    int_blank_index: int = int_comment_index - 1  # 候选空行索引

    # 文件开头或空行都满足块分隔要求。
    return int_blank_index < 0 or not lines[int_blank_index].strip()

# 提取当前语句的紧邻上一行独立注释正文。
def _immediate_preceding_comment(lines: list[str], index: int) -> str | None:
    """提取目标语句紧邻上一行的独立注释正文。

    参数:
        lines: 按行拆分后的 HLS 源码文本。
        index: 目标语句的零基源码行索引。

    返回:
        存在紧邻独立注释时返回去掉注释符号后的正文；否则返回 None。
    """

    # 上一行是唯一允许解释当前语句的位置。
    int_previous: int = index - 1  # 上一行索引

    # 文件首行或上一行不是独立注释时没有前置注释。
    if int_previous < 0 or not _is_comment_only(lines[int_previous]):

        # 当前语句没有紧邻前置注释。
        return None

    # 返回去掉注释符号后的正文。
    return _comment_text(lines[int_previous])

# 判断当前语句是否必须具备空行加中文目的注释。
def _requires_blank_plus_comment(code: str, top_function: str) -> bool:
    """判断当前 HLS 语句是否落在强制注释边界内。

    参数:
        code: 当前待检查的 HLS 代码行文本。
        top_function: 当前文件推断出的顶层函数名。

    返回:
        True 表示该语句必须具备空行加中文目的注释；False 表示当前语句不受该契约约束。
    """

    # 预处理和类型边界需要独立说明。
    bool_declaration_boundary: bool = (  # 声明类结构是否要求独立中文目的注释
        _is_include(code)  # include 行属于独立结构边界
        or _is_pragma(code)  # pragma 行也是独立结构边界
        or _is_macro(code)  # 宏定义行也是独立结构边界
        or _is_type_definition(code)  # typedef、struct 等类型定义边界
        or _is_function_signature(code)  # 函数签名边界需要独立说明接口职责
    )  # 预处理、类型和函数边界是否需要独立注释

    # 局部可执行语句同样需要目的注释。
    bool_statement_boundary: bool = (  # 语句类结构是否要求独立中文目的注释
        _is_special_statement(code)  # 局部控制语句属于独立结构边界
        or _is_local_declaration(code)  # 局部声明需要单独目的说明
        or _is_assignment(code)  # 赋值语句需要单独目的说明
        or _is_function_call_statement(code)  # 裸函数调用也需要说明外部可见动作
    )  # 局部控制、变量和调用语句是否需要独立注释

    # testbench 顶层调用是额外的 HLS 验证语义边界。
    bool_top_call_boundary: bool = _is_testbench_top_call(code, top_function)  # 顶层函数调用是否构成额外验证边界

    # 任一可审查边界都必须具备空行加中文注释。
    return bool_declaration_boundary or bool_statement_boundary or bool_top_call_boundary

# 判断行是否只包含结构性闭合符号。
def _is_trivial_line(code: str) -> bool:
    """判断当前代码行是否只是结构性闭合符号。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行只承担闭合结构职责；False 表示仍包含需要语义说明的有效代码。
    """

    # 去掉外侧空白后匹配常见闭合符号。
    str_stripped: str = code.strip()  # 规整后的代码文本

    # 单独闭合符号不需要语义注释。
    return str_stripped in {"{", "}", "};", ");", "};"} or bool(
        re.fullmatch(r"}+\s*(?:else\s*\{)?", str_stripped)
    )

# 判断 C/C++ include 语句。
def _is_include(code: str) -> bool:
    """判断当前代码行是否是 C 或 C++ include 指令。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行以 `#include` 开头；False 表示不是 include 指令。
    """

    # include 总是预处理指令。
    return code.startswith("#include")

# 判断任意 pragma 语句。
def _is_pragma(code: str) -> bool:
    """判断当前代码行是否是任意 pragma 指令。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行以 `#pragma` 开头；False 表示不是 pragma 指令。
    """

    # pragma 行把编译约束直接暴露给预处理阶段。
    return code.startswith("#pragma")

# 判断 HLS vendor pragma 语句。
def _is_hls_pragma(code: str) -> bool:
    """判断当前代码行是否是大小写精确匹配的 HLS pragma。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行以 `#pragma HLS` 开头；False 表示不是旧逻辑要求的 HLS pragma。
    """

    # 旧逻辑要求大小写精确匹配 "#pragma HLS"。
    return code.startswith("#pragma HLS")

# 判断宏定义语句。
def _is_macro(code: str) -> bool:
    """判断当前代码行是否是宏定义语句。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行以 `#define` 开头；False 表示不是宏定义语句。
    """

    # 只识别 define，保持原策略范围。
    return code.startswith("#define")

# 判断类型定义或声明块开头。
def _is_type_definition(code: str) -> bool:
    """判断当前代码行是否是类型定义或声明块开头。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行命中 typedef、using、struct、class 或 enum 开头；False 表示不是类型定义边界。
    """

    # typedef/using/struct/class/enum 都需要文件级语义说明。
    return bool(re.match(r"^(?:typedef\b|using\b|struct\b|class\b|enum\b)", code))

# 判断普通函数签名，排除控制流和 hls::stream/task 变量声明。
def _is_function_signature(code: str) -> bool:
    """判断当前代码行是否像普通函数签名。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行符合普通函数签名外形且不属于控制流或 HLS 对象声明；False 表示不是函数签名。
    """

    # 先规整空白，降低后续正则复杂度。
    str_stripped: str = code.strip()  # 待判断的 C/C++ 代码文本

    # 函数签名必须包含括号并以分号或左大括号结束。
    if not ("(" in str_stripped and ")" in str_stripped and _ends_like_signature(str_stripped)):

        # 不具备函数签名外形。
        return False

    # hls::stream/task 常用于对象声明，不按函数签名处理。
    if str_stripped.startswith(("hls::stream", "hls::task")):

        # HLS 对象声明由变量策略处理。
        return False

    # 取括号前第一个 token，排除 if/for 等控制流。
    str_first_token: str = str_stripped.split("(", 1)[0].strip().split(" ")[0]  # 签名前置 token

    # 控制流不是函数签名。
    if str_first_token in tuple_control_prefixes:

        # 控制语句由 special statement 处理。
        return False

    # 正则覆盖返回类型、命名空间、指针引用和 const 声明。
    return bool(
        re.match(
            r"^(?:[\w:<>~,\*&\[\]\s]+)\s+"
            r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?\s*"
            r"\([^;{}]*\)\s*(?:const\s*)?(?:;|\{)$",
            str_stripped,
        )
    )

# 判断函数签名允许的行尾。
def _ends_like_signature(stripped: str) -> bool:
    """判断当前代码行尾是否符合函数声明或定义外形。

    参数:
        stripped: 已去除首尾空白的 HLS 代码文本。

    返回:
        True 表示当前文本以声明分号或定义左大括号结束；False 表示不像函数签名行尾。
    """

    # 声明和定义开头都属于函数签名。
    return stripped.endswith(";") or stripped.endswith("{")

# 判断 return/try/assert/if/for/while 等特殊语句。
def _is_special_statement(code: str) -> bool:
    """判断当前代码行是否属于需要独立语义注释的特殊语句。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行属于 return、try、assert 或控制流特殊语句；False 表示不是该类语句。
    """

    # 规整空白后匹配语句前缀。
    str_stripped: str = code.strip()  # 待判断语句文本

    # 返回路径会直接暴露当前数据流或验证结果边界。
    if str_stripped.startswith("return"):

        # 显式返回语句纳入特殊语句契约。
        return True

    # 异常保护块会标记专门的异常收敛边界，也需要独立说明。
    if str_stripped.startswith("try"):

        # 异常保护入口按特殊语句处理。
        return True

    # 断言语句常用于 testbench 约束，需要说明校验目标。
    if str_stripped.startswith("assert"):

        # 断言语句同样纳入特殊语句集合。
        return True

    # 控制流需要说明分支或循环目的。
    return bool(re.match(r"^(?:for|if|while)\s*\(", str_stripped))

# 判断循环语句。
def _is_loop(code: str) -> bool:
    """判断当前代码行是否是 `for` 或 `while` 循环。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行是 `for` 或 `while` 循环头；False 表示不是循环语句。
    """

    # 仅覆盖 for/while，保持旧策略范围。
    return bool(re.match(r"^(?:for|while)\s*\(", code.strip()))

# 判断局部变量声明。
def _is_local_declaration(code: str) -> bool:
    """判断当前代码行是否像局部变量声明语句。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行符合局部声明外形；False 表示当前行应由其他结构规则处理。
    """

    # 规整空白后执行声明识别。
    str_stripped: str = code.strip()  # 待判断代码文本

    # 函数签名、类型定义和预处理指令不按局部声明处理。
    if _is_function_signature(str_stripped) or _is_type_definition(str_stripped) or str_stripped.startswith("#"):

        # 这些结构由其他策略处理。
        return False

    # 声明策略只覆盖以分号结束或包含初始化的单行语句。
    if ";" not in str_stripped:

        # 非完整声明语句。
        return False

    # 折叠空白后便于前缀匹配。
    str_compact: str = re.sub(r"\s+", " ", str_stripped)  # 单空格规整后的代码文本

    # 常见 C/C++ 类型前缀直接视为声明。
    if str_compact.startswith(tuple_declaration_prefixes):

        # 命中声明前缀。
        return True

    # 泛化匹配支持自定义类型和模板类型声明。
    return bool(
        re.match(
            r"^(?:const\s+)?"
            r"(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?(?:<[^;{}]+>)?)\s+"
            r"[A-Za-z_]\w*(?:\[[^\]]*\])?\s*(?:=|\{|;)",
            str_compact,
        )
    )

# 判断赋值语句。
def _is_assignment(code: str) -> bool:
    """判断当前代码行是否像赋值或复合赋值语句。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行符合赋值语句外形；False 表示当前行不是需要按赋值规则处理的语句。
    """

    # 先规整空白，再匹配普通赋值和复合赋值运算符。
    str_stripped: str = code.strip()  # 赋值判定用规整代码文本

    # 赋值语句必须是分号结尾的单行语句。
    if not str_stripped.endswith(";"):

        # 非完整语句不视为赋值。
        return False

    # 函数签名和预处理指令不属于变量写入语句。
    if _is_function_signature(str_stripped) or str_stripped.startswith("#"):

        # 这类结构会交给签名或预处理规则单独处理。
        return False

    # 覆盖普通赋值和复合赋值，排除比较运算符。
    return bool(re.search(r"(?<![=!<>])=(?!=)|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>=", str_stripped))

# 判断普通函数调用语句。
def _is_function_call_statement(code: str) -> bool:
    """判断当前代码行是否是普通函数或方法调用语句。

    参数:
        code: 当前待检查的 HLS 代码行文本。

    返回:
        True 表示当前行符合普通调用语句外形；False 表示当前行应由签名、声明或控制流规则处理。
    """

    # 先规整空白，再按调用语句外形做判断。
    str_stripped: str = code.strip()  # 调用判定用规整代码文本

    # 函数调用语句必须有括号并以分号结束。
    if not ("(" in str_stripped and str_stripped.endswith(";")):

        # 不具备调用语句形态。
        return False

    # 函数签名和变量声明由其他策略覆盖。
    if _is_function_signature(str_stripped) or _is_local_declaration(str_stripped):

        # 当前语句不是普通函数调用。
        return False

    # 控制语句不是普通调用。
    if str_stripped.startswith(tuple_special_starts):

        # 控制流入口会交给特殊语句规则处理。
        return False

    # 带赋值的调用也需要目的注释。
    if any(str_operator in str_stripped for str_operator in ("=", "+=", "-=", "*=", "/=")):

        # 调用结果被写入或更新。
        return True

    # 支持普通函数调用和对象方法调用。
    return bool(
        re.match(
            r"^(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?|"
            r"[A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\(",
            str_stripped,
        )
    )

# 判断 testbench 是否调用 HLS 顶层函数。
def _is_testbench_top_call(code: str, top_function: str) -> bool:
    """判断 testbench 代码行是否调用了 HLS 顶层函数。

    参数:
        code: 当前待检查的 HLS 代码行文本。
        top_function: 当前文件推断出的顶层函数名。

    返回:
        True 表示当前行包含顶层函数调用且不是函数签名；False 表示不满足该条件。
    """

    # 顶层函数名为空时不做匹配。
    return bool(top_function and f"{top_function}(" in code and not _is_function_signature(code))

# 返回去掉 C/C++ 注释后的代码部分，同时保留字符串字面量内容。
def _code_part(line: str) -> str:
    """提取单行 C 或 C++ 文本中去掉注释后的代码部分。

    参数:
        line: 原始单行 HLS 源码文本。

    返回:
        去掉 `//` 或 `/*` 起始注释后的代码文本，同时保留字符串字面量中的字符内容。
    """

    # 输出字符列表避免频繁字符串拼接。
    list_output: list[str] = []  # 当前行的代码字符

    # 手动扫描索引用于处理转义字符和双字符注释起始。
    int_index: int = 0  # 当前字符索引

    # state 标记当前是否处于字符串字面量内部。
    str_state: str = "code"  # 当前扫描状态

    # quote 记录当前字符串使用的引号类型。
    str_quote: str = ""  # 当前字符串引号

    # 逐字符扫描，遇到注释起始即停止收集代码。
    while int_index < len(line):

        # 当前扫描字符来自原始源码行，决定注释或字符串状态迁移。
        str_char: str = line[int_index]  # 当前扫描字符

        # 下一个字符用于识别 // 和 /*。
        str_next: str = line[int_index + 1] if int_index + 1 < len(line) else ""  # 下一个字符

        # 代码状态下识别字符串和注释起始。
        if str_state == "code":

            # 进入字符串字面量，之后的 // 不应被当作注释。
            if str_char in {'"', "'"}:

                # 记录字符串状态和引号。
                str_state = "string"  # 扫描器进入字符串字面量状态

                # 当前引号决定字符串结束条件。
                str_quote = str_char  # 当前字符串结束引号

                # 引号本身仍属于代码片段。
                list_output.append(str_char)

                # 移动到下一个字符。
                int_index += 1  # 扫描索引前进到字符串内容

                # 字符串内部由下一轮处理。
                continue

            # 代码状态遇到行注释或块注释起始即停止。
            if str_char == "/" and str_next in {"/", "*"}:

                # 注释及其后内容不属于代码部分。
                break

            # 普通代码字符直接保留。
            list_output.append(str_char)

            # 当前普通代码字符处理完后前进到下一个位置。
            int_index += 1  # 扫描索引前进到下一代码字符

            # 当前字符处理完毕。
            continue

        # 字符串状态下所有字符都是代码的一部分。
        list_output.append(str_char)

        # 反斜杠转义会吞掉下一个字符。
        if str_char == "\\" and int_index + 1 < len(line):

            # 保留被转义字符。
            list_output.append(line[int_index + 1])

            # 跳过转义字符对。
            int_index += 2  # 扫描索引跳过转义字符对

            # 继续扫描字符串。
            continue

        # 匹配到同类引号时退出字符串状态。
        if str_char == str_quote:

            # 回到普通代码扫描。
            str_state = "code"  # 扫描器回到普通代码状态

            # 清空当前引号。
            str_quote = ""  # 当前字符串引号已闭合

            # 消费闭合引号后继续扫描后续代码。
            int_index += 1  # 扫描索引前进到下一个字符

            # 当前闭合引号已经消费完成，继续后续扫描。
            continue

        # 普通字符串内容同样要消费，否则扫描器会停在同一字符上打转。
        int_index += 1  # 扫描索引前进到下一个字符串字符

    # 拼接并返回去注释后的代码片段。
    return "".join(list_output)

# 提取单行中的所有注释片段。
def _comments_in_line(line: str) -> list[str]:
    """提取单行源码里的全部注释片段。

    参数:
        line: 原始单行 HLS 源码文本。
    返回:
        按出现顺序收集到的注释片段列表。
    """

    # 去掉首尾空白用于识别独立注释。
    str_stripped: str = line.strip()  # 当前行规整文本

    # 注释片段按出现顺序返回。
    list_comments: list[str] = []  # 当前行注释片段列表

    # 独立 // 注释直接返回整行。
    if str_stripped.startswith("//"):

        # 保存独立行注释。
        list_comments.append(str_stripped)

    # 块注释起始行和块注释中间行也算当前行可见注释。
    elif str_stripped.startswith("/*") or str_stripped.startswith("*"):

        # 保存块注释行或块注释中间行。
        list_comments.append(str_stripped)

    # 其他普通代码行只提取行内注释片段。
    else:

        # 行内 // 注释按旧逻辑用 split 提取。
        if "//" in line:

            # 保留 // 前缀，供 _comment_text 统一处理。
            list_comments.append("//" + line.split("//", 1)[1])

        # 同行块注释通过非贪婪正则提取。
        for match_comment in re.finditer(r"/\*(.*?)\*/", line):

            # 保存完整块注释片段。
            list_comments.append(match_comment.group(0))

    # 返回当前行所有注释片段。
    return list_comments

# 判断一行是否为独立注释行。
def _is_comment_only(line: str) -> bool:
    """判断单行源码是否只包含注释。

    参数:
        line: 原始单行 HLS 源码文本。
    返回:
        True 表示当前行属于独立注释；False 表示当前行含有可执行代码或为空。
    """

    # 去掉缩进后判断注释起始符。
    str_stripped: str = line.strip()  # 独立注释判定用规整文本

    # C/C++ 独立注释包括 //、/* 和块注释中间的 *。
    return str_stripped.startswith(("//", "/*", "*"))

# 去掉 C/C++ 注释符号，返回注释正文。
def _comment_text(comment_line: str) -> str:
    """去掉注释符号并返回注释正文。

    参数:
        comment_line: 原始注释片段文本。
    返回:
        去掉 `//`、`/* */` 或前导 `*` 后的注释正文。
    """

    # 去掉外侧空白后统一处理注释起始符。
    str_stripped: str = comment_line.strip()  # 注释行规整文本

    # 行注释去掉 //。
    if str_stripped.startswith("//"):

        # 返回行注释正文。
        return str_stripped[2:].strip()

    # 块注释去掉首尾符号和星号。
    if str_stripped.startswith("/*"):

        # 返回块注释正文。
        return str_stripped.removeprefix("/*").removesuffix("*/").strip(" *")

    # 块注释中间行去掉前导星号。
    if str_stripped.startswith("*"):

        # 返回中间行正文。
        return str_stripped.lstrip("*").strip()

    # 非标准注释片段按原文本返回。
    return str_stripped

# 判断文本是否包含中文 CJK 字符。
def _contains_cjk(text: str) -> bool:
    """判断文本是否包含中文 CJK 字符。

    参数:
        text: 待检查的普通文本或注释文本。
    返回:
        True 表示文本中出现中文字符；False 表示未出现中文字符。
    """

    # 中文字符范围覆盖当前项目注释策略所需汉字。
    return bool(re.search(r"[\u4e00-\u9fff]", text))

# 判断文本是否包含任一关键词。
def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """判断文本是否至少包含给定关键词集合中的一个成员。

    参数:
        text: 待匹配的原始文本，通常是源码注释或 pragma 注释正文。
        keywords: 允许命中的关键词元组，中文和英文关键词可混用。
    返回:
        True 表示至少命中一个关键词；False 表示全部未命中。
    """

    # 统一小写后匹配英文关键词，中文不受影响。
    str_lowered: str = text.lower()  # 小写待匹配文本

    # 任一关键词命中即可通过。
    return any(str_keyword.lower() in str_lowered for str_keyword in keywords)

# 判断非中文注释是否属于允许的工具控制协议。
def _allowed_non_chinese_comment(text: str) -> bool:
    """判断非中文注释是否属于允许保留的工具控制协议。

    参数:
        text: 原始注释文本，可能包含大小写英文前缀。
    返回:
        True 表示注释属于 `noqa`、`fmt` 等协议前缀；False 表示仍应接受中文语义检查。
    """

    # 工具控制注释常以固定英文前缀出现。
    str_stripped: str = text.strip().lower()  # 规整后的注释正文

    # 这些前缀不是生成 HLS 语义注释。
    return str_stripped.startswith(("noqa", "type:", "pragma:", "fmt:", "noline", "license"))

# 判断注释是否属于模板化或泛化说明。
def _is_generic_comment(comment: str) -> bool:
    """判断注释是否落入模板化或泛化短语黑名单。

    参数:
        comment: 待检查的原始注释文本。
    返回:
        True 表示注释属于泛化模板说明；False 表示注释至少具备进一步语义辨识价值。
    """

    # 压缩标点和空白后匹配中文/英文模板短语。
    str_compact: str = re.sub(  # 去标点后的注释文本
        r"[\s`'\"：:，,。；;、（）()\[\]【】]+",  # 需要清理的空白与常见中文标点
        "",  # 用空串压缩标点与空白
        comment.lower(),  # 原始注释的小写视图
    )

    # 原始小写文本用于匹配带空格英文短语。
    str_lowered_comment: str = comment.lower()  # 小写注释文本

    # 任一模板短语命中即视为泛化注释。
    return any(
        str_pattern.replace(" ", "") in str_compact or str_pattern in str_lowered_comment
        for str_pattern in tuple_generic_comment_patterns
    )

# 判断中文注释是否短到只剩空泛名词。
def _comment_looks_vague(comment: str) -> bool:
    """判断中文注释是否短到无法表达具体 HLS 语义。

    参数:
        comment: 待检查的原始注释文本或完整注释行。
    返回:
        True 表示注释过短或只剩泛化名词；False 表示注释长度和词义至少达到基本阈值。
    """

    # 先去掉注释符号，兼容调用方传入完整注释行。
    str_text: str = _comment_text(comment)  # 注释正文

    # 先清掉空白和常见标点，再判断注释里是否只剩抽象名词。
    str_compact: str = re.sub(  # 去标点后的注释正文
        r"[\s`'\"：:，,。；;、（）()\[\]【】]+",  # 同时吞掉英文反引号与中文全角标点
        "",  # 用空串拼接出紧凑正文
        str_text,  # 去掉注释符号后的正文
    )

    # 中文字符数量用于识别一两个字的泛化注释。
    list_cjk_chars: list[str] = re.findall(r"[\u4e00-\u9fff]", str_compact)  # 注释中的中文字符

    # 少于四个中文字的注释通常无法说明 HLS 语义。
    if 0 < len(list_cjk_chars) < 4:

        # 注释过短。
        return True

    # 完全等于泛化名词时也视为过于空泛。
    return str_compact in tuple_vague_comment_patterns

# 查找给定位置上方最近的有效代码行。
def _previous_meaningful_code_index(lines: list[str], start: int) -> int | None:
    """查找给定行号上方最近的非平凡代码行索引。

    参数:
        lines: 当前 HLS 源码的逐行文本列表。
        start: 当前扫描起点，通常是空行或候选分隔位置的零基索引。
    返回:
        找到时返回上方最近的非平凡代码零基索引；未找到时返回 None。
    """

    # 从空行上方一行开始向前扫描。
    int_index: int = start - 1  # 当前向前扫描索引

    # 到达文件开头前持续查找。
    while int_index >= 0:

        # 提取去注释后的代码片段。
        str_code: str = _code_part(lines[int_index]).strip()  # 当前候选代码片段

        # 非平凡代码行即为上方代码块边界。
        if str_code and not _is_comment_only(lines[int_index]) and not _is_trivial_line(str_code):

            # 返回有效代码行索引。
            return int_index

        # 继续向上扫描。
        int_index -= 1  # 向上扫描索引前移

    # 文件开头前未找到有效代码。
    return None

# 查找给定位置下方最近的有效代码行。
def _next_meaningful_code_index(lines: list[str], start: int) -> int | None:
    """查找给定行号下方最近的非平凡代码行索引。

    参数:
        lines: 当前 HLS 源码的逐行文本列表。
        start: 当前扫描起点，通常是空行后的零基索引。
    返回:
        找到时返回下方最近的非平凡代码零基索引；未找到时返回 None。
    """

    # 从空行下方或当前扫描位置开始向后扫描。
    int_index: int = start  # 当前向后扫描索引

    # 到达文件末尾前持续查找。
    while int_index < len(lines):

        # 这里保留原行顺序，专门为下方代码块边界定位代码内容。
        str_code: str = _code_part(lines[int_index]).strip()  # 向后扫描时提取出的候选代码片段

        # 非平凡代码行即为下方代码块边界。
        if str_code and not _is_comment_only(lines[int_index]) and not _is_trivial_line(str_code):

            # 命中后立即返回下方边界位置。
            return int_index

        # 继续向下扫描。
        int_index += 1  # 向下扫描索引后移

    # 文件末尾前未找到有效代码。
    return None

# 构造单条注释策略问题。
def _issue(rel_path: str, line_number: int, message: str, detail: str) -> CommentPolicyIssue:
    """构造稳定的注释策略问题对象。

    参数:
        rel_path: 当前文件相对 run 根目录的路径。
        line_number: 触发问题的一基源码行号。
        message: 人类可读的问题说明文本。
        detail: 触发问题的源码或注释片段。
    返回:
        封装完成的 CommentPolicyIssue 实例，供 metrics 和报告复用。
    """

    # CommentPolicyIssue 字段名与 validation metrics 输出保持一致。
    return CommentPolicyIssue(
        message=message,
        path=rel_path,
        line=line_number,
        detail=detail,
    )
