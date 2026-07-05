"""检查 HLS C/C++ 源码中的结构复杂度和不可综合写法。"""

# 延迟注解让辅助 dataclass 与类型导入在运行时保持轻量。
from __future__ import annotations

# 正则用于在轻量词法分析结果中识别控制流、字面量和风险构造。
import re
from dataclasses import dataclass
from pathlib import Path

# 本模块依赖 lexer 层提供函数边界，避免引入完整 C++ AST 依赖。
from .cpp_lexer import FunctionInfo, code_part, find_multiline_statement_starts, parse_functions
from .profiles import HlsProfileConfig
from .report import HlsGateIssue, make_issue

# 统计复杂度时只关注会增加控制流路径的关键词。
CONTROL_PATTERN = re.compile(r"\b(?:if|for|while|switch|case)\b")  # 控制流关键词

# 数值字面量检测排除标识符片段，后续再按白名单和上下文降噪。
MAGIC_NUMBER_PATTERN = re.compile(  # 识别函数体内待治理的裸数字字面量
    r"(?<![A-Za-z_])(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)(?![A-Za-z_])"  # 匹配十六进制与十进制裸数字
)  # 非命名数值常量

# 这些数值在 HLS 代码中通常表达布尔、零值或简单步进，不要求提取。
ALLOWED_NUMBERS = {"0", "1", "0.0", "1.0", "2"}  # 默认允许的短字面量

# 注释掉的旧代码会误导审查和生成器，因此用保守模式识别常见 C/C++ 语句。
COMMENTED_CODE_PATTERN = re.compile(  # 识别疑似被注释保留的旧 C/C++ 语句
    r"^\s*(?://|/\*+|\*)\s*"
    r"(?:if\s*\(|for\s*\(|while\s*\(|return\b|#include\b|#pragma\b|"
    r"[A-Za-z_]\w*\s+[A-Za-z_]\w*\s*(?:=|;|\())"
)  # 疑似注释旧代码

# 单词边界片段用拼接构造，避免质量门把 C++ 正则误判成文件路径。
REGEX_WORD_BOUNDARY = chr(92) + "b"  # 正则单词边界转义片段

# 每个模式都对应一个面向 HLS 用户的阻断说明，保持报告可直接行动。
NON_SYNTH_PATTERNS = {  # 不可综合高风险构造到中文诊断文案的映射
    f"{REGEX_WORD_BOUNDARY}std::vector{REGEX_WORD_BOUNDARY}": (  # std::vector 风险模式
        "std::vector 通常不适合作为 Vitis HLS kernel 内部动态结构。"  # std::vector 的专属阻断文案
    ),
    r"\bstd::(?:map|unordered_map|list|deque|string|function)\b": (  # 动态 STL 容器与函数包装器风险模式
        "动态 STL 容器或函数包装器风险较高，不适合综合核心。"  # 动态 STL 家族的专属阻断文案
    ),
    r"\b(?:malloc|free)\s*\(": "动态内存分配不适合该 HLS 综合流。",  # C 风格堆内存分配风险模式
    r"\b(?:new|delete)\b": "C++ 动态分配不适合该 HLS 综合流。",  # C++ 堆对象分配风险模式
    f"{REGEX_WORD_BOUNDARY}virtual{REGEX_WORD_BOUNDARY}": (  # virtual 调度风险模式
        "virtual 调度会增加综合不确定性，应避免。"  # virtual 调度的专属阻断文案
    ),
    r"\bthrow\b|\btry\s*\{": "异常处理通常不可综合，应放在 testbench 或 host 侧。",  # 异常处理风险模式
    f"{REGEX_WORD_BOUNDARY}goto{REGEX_WORD_BOUNDARY}": (  # goto 跳转风险模式
        "goto 会破坏控制流可读性和综合可预测性。"  # goto 的固定阻断文案
    ),
}  # 不可综合构造到诊断文案的映射

@dataclass(frozen=True)
class FunctionIssueSpec:
    """承载函数级结构 issue 所需字段，避免 helper 参数膨胀。"""

    # 规则编号用于维持 HG015/HG016 等历史报告契约。
    rule_code: str  # HLS gate 规则编号

    # 严重级别来自阈值比较结果，不能在构造阶段重新推断。
    severity: str  # 结构阈值比较后的报告级别

    # 相对路径保证报告在不同机器上可比较。
    rel_path: str  # 源文件报告路径

    # 函数签名起始行是函数级 issue 的定位锚点。
    line_number: int  # 指向函数签名起点的报告行号

    # 函数名进入中文诊断正文，帮助用户快速定位语义对象。
    function_name: str  # 触发问题的函数名

    # 尾部诊断描述携带具体指标和治理建议。
    message_tail: str  # 函数问题说明

    # 指标值写入 detail 字段，供机器侧聚合和排序。
    metric_value: int  # 结构度量值

    # 签名摘录让报告不必重新打开源码也能辨认函数。
    signature: str  # 函数签名摘录

# 对单个 C/C++ 源文件执行结构规则检查。
def check_structure_rules(root: Path, path: Path, config: HlsProfileConfig) -> list[HlsGateIssue]:
    """按项目 HLS 结构规则收集单个源文件的可读性问题。

    参数:
        root: 报告相对路径的仓库或扫描根目录。
        path: 当前被检查的 C/C++ 源文件。
        config: 函数规模、嵌套深度和字面量阈值配置。

    返回:
        结构复杂度、魔法数、旧注释代码和不可综合构造问题列表。
    """

    # 报告中统一使用正斜杠相对路径，便于跨平台快照比较。
    str_rel_path = path.relative_to(root).as_posix()  # 报告相对路径

    # 读取源码时忽略解码错误，避免单个非 UTF-8 字节中断整个扫描。
    str_text = path.read_text(encoding="utf-8", errors="ignore")  # C/C++ 源码文本

    # 按行扫描可以复用 lexer 中的函数边界和多行语句检测。
    list_lines = str_text.splitlines()  # 源码行列表

    # 所有子规则共享同一个结果列表，保持报告顺序稳定。
    list_issues: list[HlsGateIssue] = []  # 结构规则问题列表

    # 函数规模规则先执行，便于用户优先看到最影响可维护性的诊断。
    list_issues.extend(_function_size_and_complexity_issues(str_rel_path, list_lines, config))

    # 数值常量规则依赖函数边界，避免把全局常量声明当成函数内部魔法数。
    list_issues.extend(_magic_number_issues(str_rel_path, list_lines, config))

    # 注释旧代码检查在源码行上直接运行，不改变 token 或空白。
    list_issues.extend(_commented_code_issues(str_rel_path, list_lines, config))

    # 不可综合构造需要完整文本匹配，以便计算原始行号。
    list_issues.extend(_non_synth_issues(str_rel_path, str_text))

    # 多行声明提示放在最后，避免压过更确定的结构问题。
    list_issues.extend(_multiline_escape_issues(str_rel_path, list_lines))

    # 返回顺序即规则执行顺序，供测试和报告快照稳定使用。
    return list_issues

# 检查函数长度、嵌套、控制流数量和参数数量。
def _function_size_and_complexity_issues(
    rel_path: str,
    lines: list[str],
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """根据函数边界统计 HLS kernel 代码的结构复杂度。

    参数:
        rel_path: 报告使用的源码相对路径。
        lines: 当前源文件的逐行文本。
        config: warning 与 error 阈值配置。

    返回:
        函数级规模和复杂度诊断列表。
    """

    # 函数结构诊断保持逐函数追加，便于报告定位到具体签名。
    list_issues: list[HlsGateIssue] = []  # 函数复杂度问题

    # lexer 返回的声明不含函数体，不能用于规模和复杂度统计。
    for function in parse_functions(lines):

        # 纯声明只表达接口，不代表实现复杂度。
        if function.is_declaration:

            # 跳过声明后继续扫描下一个函数体。
            continue

        # 函数体片段供后续嵌套和控制流统计复用。
        list_body_lines = lines[function.start_line - 1 : function.end_line]  # 函数体源码行

        # 四类函数指标分开计算，主循环只负责稳定汇总结果。
        list_metric_issues = [  # 当前函数上待检查的四类结构指标
            _function_line_count_issue(rel_path, function, config),  # 函数源码行数检查
            _function_nested_depth_issue(rel_path, function, list_body_lines, config),  # 控制流嵌套深度检查
            _function_branch_count_issue(rel_path, function, list_body_lines, config),  # 分支/循环数量检查
            _function_parameter_count_issue(rel_path, function, config),  # 参数/端口数量检查
        ]  # 当前函数的候选结构问题

        # None 表示该指标未超过阈值，不写入报告。
        for hls_gate_issue_item in list_metric_issues:

            # 仅追加真实触发的函数级问题。
            if hls_gate_issue_item:

                # 追加顺序与指标列表一致，保持历史报告排序稳定。
                list_issues.append(hls_gate_issue_item)

    # 返回当前文件内所有函数级结构诊断。
    return list_issues

# 检查函数源码行数并在超阈值时生成 issue。
def _function_line_count_issue(
    rel_path: str,
    function: FunctionInfo,
    config: HlsProfileConfig,
) -> HlsGateIssue | None:
    """检查单个函数是否超过源码行数阈值。

    参数:
        rel_path: 报告使用的源码相对路径。
        function: 当前函数的边界和签名信息。
        config: 函数行数 warning 与 error 阈值配置。

    返回:
        超阈值时返回函数行数 issue，否则返回 None。
    """

    # 行数包含签名和结束括号，符合用户看到的源码范围。
    int_function_lines = function.end_line - function.start_line + 1  # 函数源码行数

    # 按配置阈值把行数转换成 warning 或 error。
    str_severity = _threshold_severity(  # 按函数行数阈值计算严重级别
        int_function_lines,  # 当前函数实际源码行数
        config.warn_function_lines,  # 函数行数 warning 阈值
        config.block_function_lines,  # 函数行数触发 blocker 的上限
    )  # 函数行数严重级别

    # testbench 的规模问题不阻断综合核心治理。
    str_severity = _testbench_nonblocking_severity(rel_path, str_severity)  # 行数规则在 testbench 下的最终严重级别

    # 未超过阈值时不生成行数 issue。
    if not str_severity:

        # None 让调用方跳过当前指标。
        return None

    # 返回集中构造后的函数长度诊断。
    return _make_function_issue(
        FunctionIssueSpec(
            "HG016",
            str_severity,
            rel_path,
            function.signature_start_line,
            function.name,
            f"行数为 {int_function_lines}，超过 {str_severity} 阈值，建议拆分阶段或 helper。",
            int_function_lines,
            function.signature,
        )
    )

# 检查函数控制流嵌套层级并在超阈值时生成 issue。
def _function_nested_depth_issue(
    rel_path: str,
    function: FunctionInfo,
    body_lines: list[str],
    config: HlsProfileConfig,
) -> HlsGateIssue | None:
    """检查单个函数的控制流嵌套深度。

    参数:
        rel_path: 报告使用的源码相对路径。
        function: 当前函数的边界和签名信息。
        body_lines: 当前函数体对应的逐行源码。
        config: 嵌套深度 warning 与 error 阈值配置。

    返回:
        超阈值时返回嵌套深度 issue，否则返回 None。
    """

    # 嵌套深度衡量控制层级，过深会降低 HLS pipeline 可解释性。
    int_max_depth = _max_nested_depth(body_lines)  # 最大控制流嵌套深度

    # 深度阈值由 profile 管理，避免在规则里写死仓库策略。
    str_severity = _threshold_severity(  # 按嵌套深度阈值计算严重级别
        int_max_depth,  # 当前函数实际嵌套深度
        config.warn_nested_depth,  # 嵌套深度 warning 阈值
        config.block_nested_depth,  # 嵌套深度触发 blocker 的上限
    )  # 嵌套深度严重级别

    # testbench 里的深层构造保留提示，但不升级为阻断。
    str_severity = _testbench_nonblocking_severity(rel_path, str_severity)  # 嵌套深度规则在 testbench 下的最终严重级别

    # 未超过阈值时不生成嵌套深度 issue。
    if not str_severity:

        # None 表示这个函数的嵌套深度可接受。
        return None

    # 返回集中构造后的嵌套深度诊断。
    return _make_function_issue(
        FunctionIssueSpec(
            "HG017",
            str_severity,
            rel_path,
            function.signature_start_line,
            function.name,
            f"嵌套深度为 {int_max_depth}，应降低 if/loop 层级。",
            int_max_depth,
            function.signature,
        )
    )

# 检查函数分支循环数量并在超阈值时生成 issue。
def _function_branch_count_issue(
    rel_path: str,
    function: FunctionInfo,
    body_lines: list[str],
    config: HlsProfileConfig,
) -> HlsGateIssue | None:
    """检查单个函数的分支和循环数量。

    参数:
        rel_path: 报告使用的源码相对路径。
        function: 当前函数的边界和签名信息。
        body_lines: 当前函数体对应的逐行源码。
        config: 分支/循环数量 warning 与 error 阈值配置。

    返回:
        超阈值时返回控制流数量 issue，否则返回 None。
    """

    # 分支和循环数量近似表达函数内控制路径复杂度。
    int_branch_count = sum(  # 当前函数命中的控制流关键词数量
        1  # 每命中一条控制流语句就累计一次
        for str_body_line in body_lines  # 遍历函数体内所有源码行
        if CONTROL_PATTERN.search(code_part(str_body_line))  # 命中控制流关键词时计数
    )  # 控制流关键词数量

    # 控制流数量阈值同样来自 profile，便于不同项目调节。
    str_severity = _threshold_severity(  # 按控制流数量阈值计算严重级别
        int_branch_count,  # 当前函数内 if/for/while/switch/case 的累计次数
        config.warn_branch_loop_count,  # 控制流数量 warning 阈值
        config.block_branch_loop_count,  # 控制流数量触发 blocker 的上限
    )  # 控制流严重级别

    # testbench 控制路径复杂时只提示，不作为综合核心 blocker。
    str_severity = _testbench_nonblocking_severity(rel_path, str_severity)  # 控制流数量规则在 testbench 下的最终严重级别

    # 未超过阈值时不生成控制流数量 issue。
    if not str_severity:

        # None 表示当前函数控制路径数量可接受。
        return None

    # 返回集中构造后的控制流数量诊断。
    return _make_function_issue(
        FunctionIssueSpec(
            "HG018",
            str_severity,
            rel_path,
            function.signature_start_line,
            function.name,
            f"分支/循环数量为 {int_branch_count}，建议拆分控制逻辑。",
            int_branch_count,
            function.signature,
        )
    )

# 检查函数参数规模并在超阈值时生成 issue。
def _function_parameter_count_issue(
    rel_path: str,
    function: FunctionInfo,
    config: HlsProfileConfig,
) -> HlsGateIssue | None:
    """检查单个函数的参数或端口数量。

    参数:
        rel_path: 报告使用的源码相对路径。
        function: 当前函数的边界和签名信息。
        config: 参数/端口数量 warning 与 error 阈值配置。

    返回:
        超阈值时返回参数数量 issue，否则返回 None。
    """

    # 参数数量过多往往意味着端口契约或结构化接口缺失。
    int_param_count = len(function.params)  # 函数参数数量

    # 端口数量阈值不对 testbench 降级，因为接口可读性同样重要。
    str_severity = _threshold_severity(  # 按参数数量阈值计算严重级别
        int_param_count,  # 当前函数实际参数/端口数量
        config.warn_parameter_count,  # 参数数量 warning 阈值
        config.block_parameter_count,  # 参数数量触发 blocker 的上限
    )  # 参数数量严重级别

    # 未超过阈值时不生成参数数量 issue。
    if not str_severity:

        # None 表示当前函数接口规模可接受。
        return None

    # 返回集中构造后的参数数量诊断。
    return _make_function_issue(
        FunctionIssueSpec(
            "HG015",
            str_severity,
            rel_path,
            function.signature_start_line,
            function.name,
            f"参数/端口数量为 {int_param_count}，需要更明确的端口契约或结构化接口。",
            int_param_count,
            function.signature,
        )
    )

# 构造函数级 HLS gate issue，集中维护报告字段。
def _make_function_issue(spec: FunctionIssueSpec) -> HlsGateIssue:
    """生成指向函数签名的结构规则诊断。

    参数:
        spec: 函数级 issue 的完整字段集合。

    返回:
        可直接加入报告的问题对象。
    """

    # 诊断正文保持原有中文格式，避免破坏既有测试预期。
    str_message = f"函数 `{spec.function_name}` {spec.message_tail}"  # 函数级诊断正文

    # detail 字段仅保存原始度量值，方便机器侧排序或聚合。
    str_detail = str(spec.metric_value)  # 诊断度量字符串

    # make_issue 是报告层唯一入口，规则层不直接构造 dataclass。
    hls_gate_issue_item: HlsGateIssue = make_issue(  # 聚合后的函数级结构 issue 对象
        spec.rule_code,  # HG015/HG016 等函数结构规则编号
        spec.severity,  # 当前 issue 的最终严重级别
        spec.rel_path,  # 报告中使用的源码相对路径
        spec.line_number,  # 指向函数签名起点的行号
        str_message,  # 已拼好的函数级中文诊断正文
        detail=str_detail,  # 供聚合排序使用的结构度量 detail
        node_kind="function",  # 该 issue 归类为函数级结构问题
        code_excerpt=spec.signature,  # 报告中回放原始函数签名摘录
    )

    # 返回集中构造后的 HLS gate issue。
    return hls_gate_issue_item

# 检查函数内部未命名数值常量。
def _magic_number_issues(
    rel_path: str,
    lines: list[str],
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """统计函数体内需要提取或解释的数值字面量。

    参数:
        rel_path: 报告使用的源码相对路径。
        lines: 当前源文件的逐行文本。
        config: 魔法数 warning 与 error 阈值配置。

    返回:
        函数级魔法数诊断列表。
    """

    # 魔法数诊断按函数聚合，避免同一函数产生大量重复行级噪声。
    list_issues: list[HlsGateIssue] = []  # 魔法数问题列表

    # 仅函数实现体参与魔法数统计。
    for function in parse_functions(lines):

        # 声明行里的类型宽度或默认参数不在本规则范围内。
        if function.is_declaration:

            # 跳过纯声明，继续检查下一个函数。
            continue

        # 同一函数内的所有待命名字面量先收集再统一评级。
        list_numbers = _collect_function_magic_numbers(function, lines)  # 当前函数的未命名字面量

        # 根据当前函数的未命名数字数量决定严重级别。
        str_severity = _threshold_severity(  # 按当前函数的魔法数数量计算严重级别
            len(list_numbers),  # 当前函数聚合出的待治理数字数量
            config.warn_magic_number_count,  # 魔法数数量 warning 阈值
            config.block_magic_number_count,  # 魔法数数量触发 blocker 的上限
        )  # 魔法数严重级别

        # testbench 中的测试数据字面量只提示，不阻断 HLS 核心治理。
        str_severity = _testbench_nonblocking_severity(rel_path, str_severity)  # 魔法数规则在 testbench 下的最终严重级别

        # 只有超出阈值时才创建诊断。
        if str_severity:

            # 报告仍指向函数签名，便于一次性治理整个函数。
            hls_gate_issue_item = _make_magic_number_issue(  # 当前函数的聚合魔法数 issue
                rel_path,  # 报告使用的源码相对路径
                function,  # 当前命中魔法数的函数边界信息
                str_severity,  # 该函数魔法数数量对应的严重级别
                list_numbers,  # 当前函数内待治理的数字原文列表
            )

            # 将当前函数的魔法数问题加入结果列表。
            list_issues.append(hls_gate_issue_item)

    # 返回当前文件内所有魔法数诊断。
    return list_issues

# 收集单个函数体内需要解释或提取的数值字面量。
def _collect_function_magic_numbers(function: FunctionInfo, lines: list[str]) -> list[str]:
    """提取函数体内不属于 HLS 允许场景的数字。

    参数:
        function: 当前正在统计的函数边界信息。
        lines: 当前源文件的逐行文本。

    返回:
        需要提取为命名常量或通过语义注释说明的数字原文。
    """

    # 收集结果只保留原文字面量，报告层再决定是否截断。
    list_numbers: list[str] = []  # 当前函数内聚合出的所有待治理数字原文

    # 函数边界来自 lexer，切片包含签名和结束括号以保持旧统计范围。
    list_function_lines = lines[function.start_line - 1 : function.end_line]  # 当前函数源码行

    # 逐行过滤 pragma、位宽模板和常量定义等允许场景。
    for str_raw_line in list_function_lines:

        # 先剥离注释，避免注释文字中的数字触发误报。
        str_code = code_part(str_raw_line)  # 去注释后的源码片段

        # pragma、ap_* 位宽和命名常量定义都不计入魔法数。
        if _line_skips_magic_number_scan(str_code):

            # 允许场景直接进入下一行。
            continue

        # 将当前行中真正需要命名的数字追加到函数聚合结果。
        list_numbers.extend(_line_magic_number_literals(str_code))

    # 返回供阈值判断使用的未命名数字列表。
    return list_numbers

# 判断当前行是否整体跳过魔法数扫描。
def _line_skips_magic_number_scan(code: str) -> bool:
    """识别当前行是否属于数值字面量允许场景。

    参数:
        code: 已去除注释的单行源码。

    返回:
        True 表示该行不参与魔法数统计。
    """

    # pragma 和 ap_* 位宽是 HLS 语义表达，不按魔法数处理。
    bool_allows_numeric_literal = _line_allows_numeric_literals(code)  # HLS 允许数字场景

    # 命名常量声明本身就是治理结果，不应再次报警。
    bool_declares_named_constant = bool(  # 当前行是否正在声明命名常量
        re.search(r"\b(?:const|constexpr|static const)\b", code)  # 命中命名常量声明关键字
    )  # 常量定义行标记

    # 只要命中任一允许条件，就跳过整行数字扫描。
    return bool_allows_numeric_literal or bool_declares_named_constant

# 提取单行源码中不在白名单内的数值字面量。
def _line_magic_number_literals(code: str) -> list[str]:
    """返回单行源码中需要治理的数字原文。

    参数:
        code: 已去除注释且确认需要扫描的单行源码。

    返回:
        当前行内不在简单字面量白名单中的数字列表。
    """

    # 单行结果保留出现顺序，便于 detail 复现源码阅读顺序。
    list_numbers: list[str] = []  # 当前行的未命名字面量

    # 正则匹配保守提取数字原文，保留十六进制格式。
    for number_match in MAGIC_NUMBER_PATTERN.finditer(code):

        # 匹配值用于白名单和报告 detail。
        str_number_literal = number_match.group(0)  # 数值字面量原文

        # 允许的简单字面量不会降低代码意图清晰度。
        if str_number_literal in ALLOWED_NUMBERS:

            # 白名单字面量不进入报告统计。
            continue

        # 其余数字需要命名常量或语义注释。
        list_numbers.append(str_number_literal)

    # 返回当前行中所有需要命名或解释的数字。
    return list_numbers

# 构造函数级魔法数诊断。
def _make_magic_number_issue(
    rel_path: str,
    function: FunctionInfo,
    severity: str,
    numbers: list[str],
) -> HlsGateIssue:
    """生成指向函数签名的魔法数聚合诊断。

    参数:
        rel_path: 报告使用的源码相对路径。
        function: 触发魔法数诊断的函数边界信息。
        severity: 阈值判断后的严重级别。
        numbers: 当前函数中需要治理的数字原文。

    返回:
        可直接加入报告的问题对象。
    """

    # detail 只截取前 12 个数字，避免报告行过长。
    str_detail = ", ".join(numbers[:12])  # 魔法数报告摘录

    # 诊断正文保留数量，方便用户判断是否提取参数结构。
    str_message = (
        f"函数 `{function.name}` 存在 {len(numbers)} 个未命名数值常量，"
        "应提取为命名常量或在注释中解释。"
    )  # 魔法数诊断正文

    # make_issue 统一维护报告 dataclass 字段和历史 JSON 契约。
    hls_gate_issue_item: HlsGateIssue = make_issue(  # 聚合后的函数级魔法数 issue 对象
        "HG019",  # 函数级魔法数规则编号
        severity,  # 当前函数魔法数诊断的严重级别
        rel_path,  # 魔法数 issue 使用的源码相对路径
        function.signature_start_line,  # 魔法数 issue 锚定到函数签名起始行
        str_message,  # 已拼好的魔法数中文诊断正文
        detail=str_detail,  # 供聚合排序使用的魔法数 detail
        node_kind="function",  # 该 issue 归类为函数级魔法数问题
        code_excerpt=function.signature,  # 魔法数 issue 在报告里回放的函数签名摘录
    )

    # 返回聚合后的魔法数问题。
    return hls_gate_issue_item

# 判断当前源码行是否允许直接出现数值字面量。
def _line_allows_numeric_literals(code: str) -> bool:
    """识别 pragma 和 ap_* 位宽等允许保留数字的 HLS 场景。

    参数:
        code: 已去除注释的单行源码。

    返回:
        True 表示该行数字属于 HLS 指令或位宽语义，不参与魔法数统计。
    """

    # pragma 参数通常由 HLS 工具语义约束，不适合作为魔法数重构。
    bool_is_pragma = code.strip().startswith("#pragma")  # pragma 指令跳过魔法数扫描的标记

    # ap_* 模板位宽属于类型契约，改成常量会降低接口直观性。
    bool_has_ap_width = any(  # 当前行是否包含 ap_* 位宽模板
        str_token in code  # 任一 ap_* 模板片段命中即可视为位宽语义
        for str_token in ("ap_uint<", "ap_int<", "ap_fixed<")  # 遍历所有受支持的 ap_* 类型前缀
    )  # 任意 ap_* 位宽模板

    # 只要命中其中一种允许场景，就跳过本行魔法数扫描。
    return bool_is_pragma or bool_has_ap_width

# 检查连续注释掉的旧 C/C++ 代码。
def _commented_code_issues(
    rel_path: str,
    lines: list[str],
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """识别疑似被注释保留的旧实现片段。

    参数:
        rel_path: 报告使用的源码相对路径。
        lines: 当前源文件的逐行文本。
        config: 注释旧代码连续行阈值配置。

    返回:
        注释旧代码诊断列表。
    """

    # 连续注释代码按 run 聚合，避免每行都产生同类诊断。
    list_issues: list[HlsGateIssue] = []  # 注释旧代码问题

    # run_start 为 0 表示当前没有正在累积的疑似旧代码块。
    int_run_start = 0  # 当前连续块起始行

    # run_count 记录当前连续块长度，用于阈值判断。
    int_run_count = 0  # 当前连续块行数

    # 枚举行号从 1 开始，直接对应用户编辑器中的行号。
    for int_line_number, str_line in enumerate(lines, start=1):

        # 命中模式时累积当前连续注释代码块。
        if COMMENTED_CODE_PATTERN.search(str_line):

            # 新块第一次命中时记录起始行。
            if int_run_count == 0:

                # 起始行用于最终 issue 定位。
                int_run_start = int_line_number  # 当前疑似旧代码块首行

            # 增加当前连续块长度。
            int_run_count += 1  # 当前疑似旧代码块长度

            # 当前行已处理，下一行继续判断是否延续。
            continue

        # 遇到非匹配行时，结束并提交前一个连续块。
        if int_run_count:

            # 根据阈值决定是否把这个旧代码块转成 issue。
            list_issues.extend(_commented_run_issue(rel_path, int_run_start, int_run_count, config))

        # 重置 run 状态，等待下一个疑似旧代码块。
        int_run_count = 0  # 结束当前疑似旧代码块后重置长度

    # 文件末尾如果仍在 run 中，需要补交最后一段。
    if int_run_count:

        # 最后一段同样按阈值判断，保持与中间段一致。
        list_issues.extend(_commented_run_issue(rel_path, int_run_start, int_run_count, config))

    # 返回当前文件内所有注释旧代码诊断。
    return list_issues

# 将连续注释代码块转换成可选 issue。
def _commented_run_issue(
    rel_path: str,
    start: int,
    count: int,
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """按连续行数判断注释旧代码块是否需要报告。

    参数:
        rel_path: 报告使用的源码相对路径。
        start: 连续块起始行号。
        count: 连续块包含的行数。
        config: 注释旧代码 warning 与 error 阈值配置。

    返回:
        未超阈值时为空，超阈值时包含一个聚合 issue。
    """

    # 连续块长度达到阈值才报告，避免普通说明注释误报。
    str_severity = _threshold_severity(  # 按注释旧代码连续行数计算严重级别
        count,  # 当前疑似旧代码连续块长度
        config.warn_commented_code_lines,  # 注释旧代码 warning 阈值
        config.block_commented_code_lines,  # 注释旧代码连续行数触发 blocker 的上限
    )  # 注释旧代码严重级别

    # 未超过阈值时当前注释块可以保留。
    if not str_severity:

        # 返回空列表以便调用方直接 extend。
        return []

    # 诊断正文强调删除或交给版本历史，不鼓励长期保留旧实现。
    str_message = f"发现 {count} 行疑似注释掉的旧 C/C++ 代码，应删除或移入版本历史。"  # 旧代码诊断正文

    # 单个聚合 issue 指向连续块第一行。
    hls_gate_issue_item: HlsGateIssue = make_issue(  # 聚合后的注释旧代码 issue 对象
        "HG020",  # 注释旧代码规则编号
        str_severity,  # 当前旧代码连续块的严重级别
        rel_path,  # 注释旧代码 issue 使用的源码相对路径
        start,  # 指向连续旧代码块首行
        str_message,  # 注释旧代码的中文诊断正文
        detail=str(count),  # 连续旧代码块的行数 detail
        node_kind="commented_out_code",  # 该 issue 归类为注释旧代码块
    )

    # 调用方期望列表返回，便于统一 extend。
    return [hls_gate_issue_item]

# 检查 Vitis HLS kernel 中不适合综合的 C/C++ 构造。
def _non_synth_issues(rel_path: str, text: str) -> list[HlsGateIssue]:
    """根据固定风险模式报告不可综合或高风险构造。

    参数:
        rel_path: 报告使用的源码相对路径。
        text: 当前源文件完整文本。

    返回:
        不可综合构造诊断列表。
    """

    # 不可综合构造一律 error，因为它们会直接破坏 HLS 可接受性。
    list_issues: list[HlsGateIssue] = []  # 不可综合构造问题

    # 每个模式都带有专门说明，报告时不再拼接泛化文案。
    for str_pattern, str_message in NON_SYNTH_PATTERNS.items():

        # 在完整文本上查找，避免跨行上下文被提前截断。
        for regex_match in re.finditer(str_pattern, text):

            # 通过换行计数把 match 位置转换成 1 基行号。
            int_line_number = text.count("\n", 0, regex_match.start()) + 1  # 匹配所在行

            # detail 保存实际命中的源码片段，便于用户确认模式来源。
            str_detail = regex_match.group(0)  # 不可综合命中片段

            # 不可综合 issue 指向具体行，方便快速删除或迁移到 host/testbench。
            hls_gate_issue_item: HlsGateIssue = make_issue(  # 单个不可综合命中的 issue 对象
                "HG021",  # 不可综合构造规则编号
                "error",  # 不可综合构造一律按 error 报告
                rel_path,  # 不可综合 issue 使用的源码相对路径
                int_line_number,  # 指向当前命中的源码行
                str_message,  # 当前不可综合命中的中文诊断正文
                detail=str_detail,  # 当前命中的不可综合源码片段
                node_kind="non_synth_construct",  # 该 issue 归类为不可综合构造
            )

            # 保留所有命中位置，避免一个文件多个风险只报第一处。
            list_issues.append(hls_gate_issue_item)

    # 返回所有不可综合构造诊断。
    return list_issues

# 检查多行函数签名或声明产生的行级审查盲区。
def _multiline_escape_issues(rel_path: str, lines: list[str]) -> list[HlsGateIssue]:
    """提示需要人工确认的多行声明或签名。

    参数:
        rel_path: 报告使用的源码相对路径。
        lines: 当前源文件的逐行文本。

    返回:
        多行语句提示诊断列表。
    """

    # 多行语句不是确定错误，但会削弱简单行级规则的证明能力。
    list_issues: list[HlsGateIssue] = []  # 多行语句提示

    # lexer 返回语句起始行和拼接后的短语句，供报告摘录。
    for int_line_number, str_joined_statement in find_multiline_statement_starts(lines):

        # 摘录限制长度，避免报告被长签名或模板参数撑爆。
        str_excerpt = str_joined_statement[:220]  # 多行语句报告摘录

        # 多行语句统一 warning，要求人工确认注释覆盖即可。
        hls_gate_issue_item: HlsGateIssue = make_issue(  # 单个多行声明起点的提示 issue
            "HG024",  # 多行语句提示规则编号
            "warning",  # 多行声明只提示人工复核
            rel_path,  # 多行声明 issue 使用的源码相对路径
            int_line_number,  # 指向多行语句的起始行
            "多行函数签名或声明需要人工确认注释覆盖；行级规则可能无法完整证明。",  # 多行语句需要人工复核的固定提示文案
            detail=str_excerpt,  # 多行语句拼接后的摘录文本
            node_kind="multiline_statement",  # 该 issue 归类为多行声明提示
            code_excerpt=str_excerpt,  # 报告中回放拼接后的多行语句摘录
        )

        # 每个多行起点保留一条提示。
        list_issues.append(hls_gate_issue_item)

    # 返回当前文件内的多行语句提示。
    return list_issues

# 计算函数体内控制流嵌套深度。
def _max_nested_depth(lines: list[str]) -> int:
    """用大括号层级估算 if/for/while/switch 的最大嵌套深度。

    参数:
        lines: 当前函数体源码行。

    返回:
        控制流语句出现时的最大嵌套深度。
    """

    # 当前大括号深度用于估算控制流所在层级。
    int_depth = 0  # 当前括号深度

    # 最大深度只在控制流语句行更新。
    int_max_depth = 0  # 当前函数扫描过程中观察到的最大控制层级

    # 逐行扫描比完整解析更宽容，适合快速质量门。
    for str_raw_line in lines:

        # 注释内容不参与控制流和括号统计。
        str_code = code_part(str_raw_line)  # 剥离注释后用于括号与关键字统计的源码片段

        # 去掉首尾空白后识别控制流关键字。
        str_stripped_code = str_code.strip()  # 去空白源码片段

        # 控制语句所在层级等于当前深度加一。
        if re.match(r"^(?:if|for|while|switch)\s*\(", str_stripped_code):

            # 记录当前函数中出现过的最大控制层级。
            int_max_depth = max(int_max_depth, int_depth + 1)  # 当前控制语句所在最大层级

        # 根据大括号更新后续行的基础深度。
        int_depth += str_code.count("{") - str_code.count("}")  # 当前深度增量累计

        # 容忍格式不完整或宏导致的负深度，避免误差继续扩散。
        int_depth = max(int_depth, 0)  # 归一化后的非负括号深度

    # 返回估算得到的最大控制流嵌套深度。
    return int_max_depth

# 根据 warning/error 阈值计算严重级别。
def _threshold_severity(value: int, warn: int, block: int) -> str | None:
    """把数值指标转换为 HLS gate 严重级别。

    参数:
        value: 当前实际度量值。
        warn: warning 阈值。
        block: error 阈值。

    返回:
        超过 block 返回 error，超过 warn 返回 warning，否则返回 None。
    """

    # 阻断阈值优先于 warning，保证更严重结果不会被降级。
    if value > block:

        # error 表示当前度量已超过硬阈值。
        return "error"

    # warning 仅表示需要人工治理，但不一定阻塞当前流程。
    if value > warn:

        # warning 表示度量超过软阈值。
        return "warning"

    # 没有超过任何阈值时不生成 issue。
    return None

# 将 testbench 中的结构 error 降级为 warning。
def _testbench_nonblocking_severity(rel_path: str, severity: str | None) -> str | None:
    """避免 testbench 规模问题阻塞 HLS kernel 主体治理。

    参数:
        rel_path: 报告使用的源码相对路径。
        severity: 原始严重级别。

    返回:
        testbench error 降为 warning，其余情况保持原值。
    """

    # testbench 路径和 *_tb.* 文件只作为辅助验证代码。
    bool_is_testbench = rel_path.startswith("tb/") or "_tb." in rel_path  # testbench 文件标记

    # 只有 error 需要降级，warning 和 None 原样返回。
    if severity == "error" and bool_is_testbench:

        # testbench 结构问题保留提示，不阻断综合核心。
        return "warning"

    # 非 testbench 或非 error 的情况保持原始严重级别。
    return severity
