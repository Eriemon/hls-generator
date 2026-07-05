"""检查 HLS C/C++ 标识符是否表达端口、缓存和数据路径语义。"""

# 启用延迟注解，避免运行期解析类型标注。
from __future__ import annotations

# 正则库用于识别 snake_case、常量和协议类型片段。
import re
from pathlib import Path

# C/C++ 轻量词法工具负责剥离注释并提取函数、声明和赋值目标。
from .cpp_lexer import (
    code_part,
    extract_assignment_target,
    extract_identifier_from_declaration,
    is_assignment,
    is_local_declaration,
    parse_functions,
)

# profile 和报告对象保持 HLS 可读性门禁的公共接口稳定。
from .profiles import HlsProfileConfig
from .report import HlsGateIssue, make_issue

# 这些短名无法说明 HLS 数据路径责任，除非出现在豁免场景。
VAGUE_NAMES = {"data", "info", "temp", "tmp", "result", "value", "obj", "buf", "buffer", "val", "x", "y"}  # 这些短名会遮蔽端口、缓存和累加器等 HLS 责任语义

# 短循环下标和协议字段名属于 HLS 代码中的常见可读约定。
EXEMPT_NAMES = {"i", "j", "k", "n", "m", "r", "c", "ii", "tb", "ap", "axis", "last", "idx", "len"}  # 这些短名来自循环下标、协议字段或测试夹具约定，默认不阻断

# 简单 C/C++ 类型集合保留给后续类型语义扩展。
ALLOWED_SIMPLE_TYPES = {"int", "bool", "float", "double", "char", "short", "long", "size_t"}  # 这些基础类型名可直接参与后续类型语义扩展

# check_naming_rules 是 HLS 命名规则的目录级入口。
def check_naming_rules(root: Path, path: Path, config: HlsProfileConfig) -> list[HlsGateIssue]:
    """检查单个 HLS 源文件中的函数、参数、局部声明和赋值目标命名。

    参数:
        root: HLS 可读性门禁扫描根目录，用于生成相对报告路径。
        path: 当前正在检查的 HLS 源文件路径。
        config: HLS profile 配置；当前命名规则保留该参数以兼容规则接口。

    返回:
        当前文件命名规则发现的问题列表。
    """

    # 当前规则暂不依赖 profile 细项，但保留参数避免破坏规则调用接口。
    del config

    # 报告路径统一使用 POSIX 分隔符，便于跨平台比较。
    str_rel_path = path.relative_to(root).as_posix()  # 当前文件相对扫描根目录的报告路径

    # 源码按行读取，后续 C++ 轻量解析器按行定位诊断位置。
    list_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()  # HLS 源码物理行

    # 命名诊断列表按源码出现顺序追加，保持报告稳定。
    list_issues: list[HlsGateIssue] = []  # 当前文件命名问题列表

    # 函数解析结果同时包含签名、参数和行号。
    list_functions = parse_functions(list_lines)  # HLS 函数签名解析结果

    # 先检查函数签名和参数，避免局部变量问题淹没公共接口问题。
    list_issues.extend(_function_signature_issues(str_rel_path, list_functions))

    # 再检查局部声明和赋值目标，补足函数体内部的数据路径命名。
    for int_line_number, str_raw_line in enumerate(list_lines, start=1):

        # 当前源码行的声明或赋值目标检查交给 helper，降低入口函数嵌套深度。
        list_issues.extend(_body_line_name_issues(str_rel_path, int_line_number, str_raw_line))

    # 返回当前文件累计的命名诊断。
    return list_issues

# _function_signature_issues 汇总函数名和参数命名诊断。
def _function_signature_issues(str_rel_path: str, list_functions: list[object]) -> list[HlsGateIssue]:
    """检查全部函数签名中的函数名和参数名。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        list_functions: cpp_lexer.parse_functions 返回的函数描述对象列表。

    返回:
        函数签名相关诊断列表。
    """

    # 函数签名诊断先按源码顺序累计。
    list_issues: list[HlsGateIssue] = []  # 函数签名诊断列表

    # 逐个函数检查名称和参数。
    for function in list_functions:

        # testbench main 保持 C/C++ 入口约定，不强制改名。
        if function.name == "main":

            # main 是 testbench 常见入口名，这里不参与 HLS 业务语义命名检查。
            continue

        # 函数名结构和语义都需要独立检查。
        list_issues.extend(_function_name_issues(str_rel_path, function))

        # 函数参数代表端口或控制输入，命名应优先体现接口语义。
        for str_param in function.params:

            # 参数问题复用通用名称检查逻辑。
            list_issues.extend(
                _name_issues(
                    str_rel_path,
                    function.signature_start_line,
                    str_param,
                    "parameter",
                    function.signature,
                ),
            )

    # 返回函数签名相关诊断。
    return list_issues

# _body_line_name_issues 检查单行函数体中的声明或赋值目标。
def _body_line_name_issues(str_rel_path: str, int_line_number: int, str_raw_line: str) -> list[HlsGateIssue]:
    """检查单行 HLS 函数体代码中的局部声明或赋值目标。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        int_line_number: 当前源码行的一基行号。
        str_raw_line: 当前 HLS 源码原始行文本。

    返回:
        当前行触发的命名诊断列表。
    """

    # 规则只分析去掉注释后的代码部分，避免注释文本误触发。
    str_code = code_part(str_raw_line).strip()  # 当前源码行的可执行 C/C++ 片段

    # 局部声明能提供类型语义，因此优先走声明检查。
    if is_local_declaration(str_code):

        # 声明行检查封装在独立 helper 中，避免主流程嵌套过深。
        return _declaration_name_issues(str_rel_path, int_line_number, str_code)

    # 赋值目标缺少类型上下文，只做基础命名和空泛语义检查。
    if is_assignment(str_code):

        # 赋值行检查封装在独立 helper 中。
        return _assignment_name_issues(str_rel_path, int_line_number, str_code)

    # 非声明、非赋值行不产生命名诊断。
    return []

# _declaration_name_issues 检查单个局部声明的名称。
def _declaration_name_issues(str_rel_path: str, int_line_number: int, str_code: str) -> list[HlsGateIssue]:
    """检查单个局部声明中的标识符名称。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        int_line_number: 声明所在的一基源码行号。
        str_code: 去掉注释后的声明源码片段。

    返回:
        当前声明触发的命名诊断列表。
    """

    # 取出声明中的标识符名称，无法解析时跳过该行。
    str_name = extract_identifier_from_declaration(str_code)  # 局部声明标识符名称

    # 空名称说明该声明形态超出轻量解析器能力。
    if not str_name:

        # 轻量声明解析器没有拿到标识符时，这一行暂时不产生命名诊断。
        return []

    # 声明名称先做通用语义检查。
    list_issues = _name_issues(  # 局部声明基础命名诊断
        str_rel_path,  # 当前源文件相对扫描根目录的报告路径
        int_line_number,  # 当前局部声明所在的一基源码行号
        str_name,  # 当前声明里提取到的标识符名称
        _declaration_kind(str_code),  # 当前声明对应的标识符类别
        str_code,  # 供报告回显的声明源码片段
    )

    # 特定 HLS 类型还需要检查 stream、axis、acc 等语义后缀。
    list_issues.extend(_semantic_suffix_issues(str_rel_path, int_line_number, str_name, str_code))

    # 返回当前声明的全部命名诊断。
    return list_issues

# _assignment_name_issues 检查单个赋值目标名称。
def _assignment_name_issues(str_rel_path: str, int_line_number: int, str_code: str) -> list[HlsGateIssue]:
    """检查单个赋值目标的名称。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        int_line_number: 赋值所在的一基源码行号。
        str_code: 去掉注释后的赋值源码片段。

    返回:
        当前赋值目标触发的命名诊断列表。
    """

    # 取出赋值左侧目标名，属性或数组下标会被解析器过滤。
    str_name = extract_assignment_target(str_code)  # 赋值目标标识符名称

    # 没有可检查目标时跳过当前赋值行。
    if not str_name:

        # 赋值左侧不是可命名标识符时，这一行不产生 assignment_target 命名诊断。
        return []

    # 赋值目标使用通用名称检查逻辑。
    return _name_issues(str_rel_path, int_line_number, str_name, "assignment_target", str_code)

# _function_name_issues 只处理函数名本身的结构和语义问题。
def _function_name_issues(str_rel_path: str, function: object) -> list[HlsGateIssue]:
    """检查单个 HLS 函数名并生成诊断。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        function: cpp_lexer.parse_functions 返回的函数描述对象。

    返回:
        函数名相关诊断列表。
    """

    # 函数名问题通常最多两条，列表直接累计即可。
    list_issues: list[HlsGateIssue] = []  # 函数名诊断列表

    # 非 snake_case 会降低 HLS pipeline/dataflow 阶段名称的一致性。
    if not _is_snake_case(function.name):

        # 函数命名结构问题登记为 warning，保留旧门禁严重级别。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                function.signature_start_line,
                "HLS 函数名建议使用 snake_case，避免混合大小写降低可读性。",
                detail=function.name,
                node_kind="function",
                code_excerpt=function.signature,
            ),
        )

    # 空泛函数名会掩盖内核、阶段或数据路径责任。
    if _is_vague_name(function.name):

        # 空泛函数名直接阻断，因为生成代码应暴露 HLS 顶层责任。
        list_issues.append(
            make_issue(
                "HG014",
                "error",
                str_rel_path,
                function.signature_start_line,
                "HLS 函数名过于空泛，必须体现内核、阶段或数据路径责任。",
                detail=function.name,
                node_kind="function",
                code_excerpt=function.signature,
            ),
        )

    # 返回函数名诊断，调用方继续检查参数。
    return list_issues

# _name_issues 复用到参数、局部变量和赋值目标。
def _name_issues(str_rel_path: str, int_line: int, str_name: str, str_kind: str, str_code: str) -> list[HlsGateIssue]:
    """检查单个标识符的结构命名和业务语义。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        int_line: 标识符所在的一基源码行号。
        str_name: 待检查的 HLS 标识符名称。
        str_kind: 标识符在源码中的类别，例如 parameter 或 local_identifier。
        str_code: 用于报告上下文的源码片段。

    返回:
        当前标识符触发的命名诊断列表。
    """

    # 每个标识符独立累计，便于调用方按源码顺序合并。
    list_issues: list[HlsGateIssue] = []  # 单个标识符诊断列表

    # 豁免名和私有辅助名不参与 HLS 业务语义检查。
    if str_name in EXEMPT_NAMES or str_name.startswith("_"):

        # 返回空诊断，避免对常见下标和内部辅助名制造噪声。
        return list_issues

    # AXIS packet 的 data 字段是协议固定字段，不应被当作空泛命名。
    if str_name == "data" and _is_axis_packet_field(str_code):

        # 协议字段命名由 AXIS 类型定义约束。
        return list_issues

    # 普通变量建议 snake_case，常量允许 UPPER_CASE。
    if not _is_snake_case(str_name) and not _is_upper_constant(str_name):

        # 结构命名不一致登记为 warning。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                int_line,
                "HLS 标识符应使用 snake_case；常量可使用 UPPER_CASE。",
                detail=str_name,
                node_kind=str_kind,
                code_excerpt=str_code,
            ),
        )

    # 空泛名称会隐藏端口、缓存、索引或累加器责任。
    if _is_vague_name(str_name):

        # 语义过空泛登记为 error，促使调用方修改生成结果。
        list_issues.append(
            make_issue(
                "HG014",
                "error",
                str_rel_path,
                int_line,
                "HLS 标识符过于空泛，必须包含端口、缓存、索引、通道、累加器或业务含义。",
                detail=str_name,
                node_kind=str_kind,
                code_excerpt=str_code,
            ),
        )

    # 返回当前标识符的全部诊断。
    return list_issues

# _semantic_suffix_issues 检查 HLS 类型和变量名是否互相印证。
def _semantic_suffix_issues(str_rel_path: str, int_line: int, str_name: str, str_code: str) -> list[HlsGateIssue]:
    """根据 HLS 类型片段检查变量名是否携带协议或累加器语义。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        int_line: 声明所在的一基源码行号。
        str_name: 声明中提取到的标识符名称。
        str_code: 去掉注释后的声明源码片段。

    返回:
        当前声明触发的协议语义命名诊断列表。
    """

    # 大小写归一化后检查类型和名称中的语义 token。
    str_lowered_code = str_code.casefold()  # 小写化后的声明源码片段

    # 名称归一化后用于查找 stream、axis、acc 等语义片段。
    str_lowered_name = str_name.casefold()  # 小写化后的标识符名称

    # 协议语义诊断通常很少，使用列表保持顺序。
    list_issues: list[HlsGateIssue] = []  # HLS 类型语义命名诊断列表

    # hls::stream 变量名应直接暴露 FIFO/channel 语义。
    if "hls::stream" in str_lowered_code and not _contains_any(str_lowered_name, ("stream", "channel", "fifo")):

        # stream 语义缺失会让 dataflow 通道审查更困难。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                int_line,
                "hls::stream 变量名应体现 stream/channel/FIFO 语义。",
                detail=str_name,
                node_kind="stream_identifier",
                code_excerpt=str_code,
            ),
        )

    # AXIS token 变量名应保留协议词，方便区分 payload 和普通标量。
    if _looks_like_axis_token(str_lowered_code) and not _contains_any(
        str_lowered_name,
        ("axis", "word", "packet", "token", "pkt"),
    ):

        # AXIS 协议语义缺失登记为 warning。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                int_line,
                "AXIS token 变量名应体现 axis/word/packet/token 协议语义。",
                detail=str_name,
                node_kind="axis_identifier",
                code_excerpt=str_code,
            ),
        )

    # 累加器声明需要在变量名中体现 acc 或 sum，避免和普通中间值混淆。
    if _looks_like_accumulator(str_lowered_code) and not _contains_any(str_lowered_name, ("acc", "sum")):

        # 累加器语义缺失登记为 warning。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                int_line,
                "累加器变量名应包含 acc 或 sum 及被累加对象。",
                detail=str_name,
                node_kind="accumulator_identifier",
                code_excerpt=str_code,
            ),
        )

    # 返回类型语义相关的全部诊断。
    return list_issues

# _contains_any 封装名称 token 命中判断。
def _contains_any(str_text: str, tuple_tokens: tuple[str, ...]) -> bool:
    """判断文本中是否包含任一候选语义 token。

    参数:
        str_text: 已归一化大小写的待检查文本。
        tuple_tokens: 候选语义 token 集合。

    返回:
        命中任一 token 时返回 True。
    """

    # 逐项匹配候选 token，保持调用处条件表达式简洁。
    return any(str_token in str_text for str_token in tuple_tokens)

# _looks_like_axis_token 识别 AXIS 协议相关声明。
def _looks_like_axis_token(str_code: str) -> bool:
    """判断声明源码是否像 AXIS token 类型。

    参数:
        str_code: 已归一化大小写的声明源码片段。

    返回:
        包含 ap_axiu 或 axis 片段时返回 True。
    """

    # ap_axiu 和 axis 都表示流式 token 或协议包装。
    return "ap_axiu" in str_code or "axis" in str_code

# _looks_like_accumulator 识别累加器相关声明。
def _looks_like_accumulator(str_code: str) -> bool:
    """判断声明源码是否包含累加器语义。

    参数:
        str_code: 已归一化大小写的声明源码片段。

    返回:
        出现 sum、acc、accum 或 accumulator 词根时返回 True。
    """

    # 使用词边界避免把普通长词中的 acc 误判为累加器。
    return re.search(r"\b(?:sum|acc|accum|accumulator)\b", str_code) is not None

# _declaration_kind 区分常量声明和普通局部标识符。
def _declaration_kind(str_code: str) -> str:
    """根据声明源码判断报告中的节点类别。

    参数:
        str_code: 去掉注释后的声明源码片段。

    返回:
        constant 或 local_identifier。
    """

    # const 前缀和全大写赋值形态都按常量处理。
    if str_code.strip().startswith("const ") or re.match(r"^\s*[A-Z0-9_]+\s*=", str_code):

        # 常量允许 UPPER_CASE 命名。
        return "constant"

    # 其他声明按普通局部标识符检查。
    return "local_identifier"

# _is_snake_case 检查普通 HLS 标识符结构。
def _is_snake_case(str_name: str) -> bool:
    """判断名称是否符合 snake_case。

    参数:
        str_name: 待检查的标识符名称。

    返回:
        小写字母开头并只包含小写字母、数字和下划线时返回 True。
    """

    # re.fullmatch 保证整个名称都满足 snake_case 结构。
    return re.fullmatch(r"^[a-z][a-z0-9_]*$", str_name) is not None

# _is_upper_constant 检查常量式标识符结构。
def _is_upper_constant(str_name: str) -> bool:
    """判断名称是否符合 UPPER_CASE 常量约定。

    参数:
        str_name: 待检查的标识符名称。

    返回:
        全大写字母、数字和下划线组成时返回 True。
    """

    # re.fullmatch 保证整个名称都是常量式结构。
    return re.fullmatch(r"^[A-Z][A-Z0-9_]*$", str_name) is not None

# _is_vague_name 检查名称是否只有空泛占位含义。
def _is_vague_name(str_name: str) -> bool:
    """判断标识符名称是否过于空泛。

    参数:
        str_name: 待检查的标识符名称。

    返回:
        名称是黑名单词或带数字后缀的空泛词时返回 True。
    """

    # 去掉首尾下划线后比较，避免 `_tmp` 绕过语义检查。
    str_lowered_name = str_name.casefold().strip("_")  # 归一化后的标识符名称

    # 精确命中黑名单时直接判定为空泛命名。
    if str_lowered_name in VAGUE_NAMES:

        # 空泛词没有携带 HLS 数据路径责任。
        return True

    # tmp1、value2 等数字后缀仍然是空泛占位名。
    return re.fullmatch(r"(?:tmp|temp|data|result|value|buf)\d*", str_lowered_name) is not None

# _is_axis_packet_field 保留 AXIS data 字段的协议豁免。
def _is_axis_packet_field(str_code: str) -> bool:
    """判断源码行是否声明了 AXIS packet 的 data 字段。

    参数:
        str_code: 去掉注释后的源码片段。

    返回:
        命中 ap_int/ap_uint data 字段声明时返回 True。
    """

    # AXIS payload 字段名 data 来自协议结构，不代表空泛业务命名。
    return re.match(r"^\s*ap_u?int<\d+>\s+data\s*;", str_code) is not None
