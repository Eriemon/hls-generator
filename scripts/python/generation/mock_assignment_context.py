"""收拢 mock HLS source 里依赖 struct 和局部上下文的赋值说明逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 正则和代码片段提取器负责识别结构字段与签名上下文边界。
import re
from scripts.python.hls_quality_gate.readability.cpp_lexer import code_part

# 模式子模块承接赋值语句识别和累计更新说明。
from .mock_assignment_patterns import (
    accumulation_update_comment_text,
    assigned_symbol_name,
    is_accumulation_update_statement,
    is_assignment_statement,
)

# 角色子模块继续负责普通赋值语义说明的主调度入口。
from .mock_assignment_roles import assignment_comment_text

# 为 board wrapper 的 struct 头提供固定的封包角色说明。
def struct_header_comment_text(str_code: str) -> str:
    """为 board wrapper 的 struct 头提供固定的封包角色说明。

    参数:
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。

    返回:
        命中 AXIS packet 结构体头时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐个匹配 board wrapper 使用的 AXIS packet 结构体头，避免在总调度函数里重复堆分支。
    for str_struct_head, str_comment_text in (
        (
            "struct axis_byte_t {",
            "axis_byte_t 在这里定义 board wrapper 的单字节 AXIS 输入 token，本地封包阶段会逐项填入 data、last、keep 和 strb 字段。",
        ),
        (
            "struct axis_word_t {",
            "axis_word_t 在这里定义 board wrapper 的 16-bit AXIS 输出 token，编码阶段会把结果与侧带一起写入这个本地封包格式。",
        ),
    ):

        # 当前结构体头和目标 packet 角色完全匹配时，直接返回对应说明。
        if str_code == str_struct_head:

            # 把当前 packet 结构体头绑定到专属封包说明。
            return str_comment_text

    # 其他 struct 头不在这里强制改写。
    return ""

# 为赋值和占位 return 统一路由到当前写入或骨架边界说明。
def assignment_or_return_comment_text(str_code: str, str_stage_code: str) -> str:
    """为赋值和占位 return 统一路由到当前写入或骨架边界说明。

    参数:
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。
        str_stage_code: 当前代码下方首条代表阶段职责的代码文本，dtype=str，unit=code text。

    返回:
        命中赋值或 return 规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 遇到 `.last =` 三元赋值时，优先复用字段级赋值说明。
    if ".last =" in str_code and str_code.endswith(";"):

        # 三元帧尾赋值直接交给字段级赋值说明生成器。
        return assignment_comment_text(str_code)

    # 普通简单赋值继续落回左值节点的写入职责。
    if is_assignment_statement(str_code):

        # 返回当前赋值语句的局部写入说明。
        return assignment_comment_text(str_code)

    # `+=` 这类归约更新也需要显式保留当前局部状态的折叠语义。
    if is_accumulation_update_statement(str_code):

        # 返回当前累计更新语句对应的局部折叠说明。
        return accumulation_update_comment_text(str_code)

    # 占位 return 需要说明当前 mock source 仍停留在最小合同骨架。
    if str_code.startswith("return"):

        # 明确当前函数体还只是最小可综合骨架，没有继续展开真实算子逻辑。
        return "当前 mock source 暂时只保留接口 contract 和 pragma 边界，函数体在这里以 return 维持最小可综合骨架。"

    # 其他代码行在这里不追加说明。
    return ""

# 回溯当前代码片段所属的函数签名起点，提取 helper 名称供签名注释改写复用。
def enclosing_signature_function_name(list_lines: list[str], int_code_index: int) -> str:
    """回溯当前代码片段所属的函数签名起点，提取 helper 名称。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前绑定代码所在的零基行号，dtype=int，unit=line index。

    返回:
        命中的函数名；未命中时返回空字符串，dtype=str，unit=function name。
    """

    # 从当前签名片段位置向上回溯，直到找到真正的函数头或遇到前一个函数体结尾。
    for int_scan_index in range(int_code_index, -1, -1):

        # 读取这一次倒序回溯真正看到的净代码文本，供函数头和越界边界判断复用。
        str_scan_code = code_part(list_lines[int_scan_index]).strip()  # 当前倒序扫描拿到的净代码片段

        # 空行和纯注释行不提供函数名信息，继续向上扫描。
        if not str_scan_code:

            # 当前扫描行只是空白或纯注释，继续向上寻找真正的函数头。
            continue

        # 遇到前一个函数体闭合后立即停止，避免串到上一个 helper。
        if str_scan_code == "}":

            # 已经越过当前签名所在区域，回退为空字符串。
            break

        # 只抽取形如 `name(` 的函数头尾部标识符，过滤控制流关键字。
        obj_match = re.search(r"([A-Za-z_]\w*)\s*\($", str_scan_code)  # 当前扫描行末尾函数名的匹配对象

        # 命中真实函数名时直接返回。
        if obj_match and obj_match.group(1) not in {"if", "for", "while", "switch"}:

            # 返回当前签名片段所属的函数名称。
            return obj_match.group(1)

    # 没有命中函数头时回退为空字符串。
    return ""

# 为需要依赖所在 helper 上下文的局部赋值生成更细的说明。
def contextual_assignment_comment_text(
    list_lines: list[str],
    int_code_index: int,
    str_code: str,
) -> str:
    """为依赖 helper 上下文的局部赋值生成更细的说明。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前代码所在的零基行号，dtype=int，unit=line index。
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。

    返回:
        命中上下文专属赋值规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 当前只有二维块 helper 里的总样本量寄存器需要借助所在函数名去重。
    if str_code != "int int_total = int_rows * int_cols;":

        # 当前赋值不是二维块 helper 的总样本量寄存器时直接回退为空。
        return ""

    # 回溯当前赋值所属的 helper 名称，便于把总样本量寄存器和真实阶段绑定。
    str_function_name = enclosing_signature_function_name(list_lines, int_code_index)  # 当前总样本量赋值所属的 helper 名称

    # 按 helper 名称返回不重复的总样本量说明，避免 5 个 stage 共用同一句模板。
    return {
        "read_block": "int_total 在这里把当前二维块的总读取样本数固定成行列乘积，让 read_block 能沿统一的扁平边界顺序取样。",
        "row_pass": "int_total 在这里锁定 row_pass 需要消费的块样本总量，确保第一段行向处理不会越过当前二维块边界。",
        "transpose_or_reorder": "int_total 在这里固定重排阶段要回放的块样本总量，让 transpose_or_reorder 维持和 read/row 阶段一致的二维事务范围。",
        "col_pass": "int_total 在这里给列向阶段锁定本轮块样本总量，确保 col_pass 只处理当前重排后的有效二维块。",
        "write_block": "int_total 在这里锁定 write_block 需要回写的块样本总量，让最终扁平写回和前面各 stage 保持同一二维边界。",
    }.get(str_function_name, "")

# 为需要依赖所在 helper 上下文的局部赋值生成不同于摘要的短尾注。
def contextual_assignment_inline_comment_text(
    list_lines: list[str],
    int_code_index: int,
    str_code: str,
) -> str:
    """为依赖 helper 上下文的局部赋值生成不同于摘要的短尾注。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前代码所在的零基行号，dtype=int，unit=line index。
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。

    返回:
        命中上下文专属尾注规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # inline 尾注路径只在二维块 helper 的总样本量寄存器上按 helper 名称区分语义。
    if str_code != "int int_total = int_rows * int_cols;":

        # 当前赋值不是二维块 helper 的总样本量寄存器时不补专属尾注。
        return ""

    # 回溯当前赋值所属的 helper 名称，便于给尾注补上阶段差异。
    str_function_name = enclosing_signature_function_name(list_lines, int_code_index)  # 当前尾注需要绑定的 helper 名称

    # 按 helper 名称返回更短的阶段差异说明。
    return {
        "read_block": "这里把块读取阶段的总取样数压成统一的扁平边界。",
        "row_pass": "这里把行向阶段本轮需要消费的块样本总量固定下来。",
        "transpose_or_reorder": "这里把重排阶段本轮要回放的块样本总量锁定下来。",
        "col_pass": "这里把列向阶段本轮要处理的块样本总量锁定下来。",
        "write_block": "这里把最终写回阶段本轮要落盘的块样本总量固定下来。",
    }.get(str_function_name, "")

# 为 board wrapper 的 struct 字段声明生成字段职责说明。
def struct_field_comment_text(
    list_lines: list[str],
    int_code_index: int,
    str_code: str,
) -> str:
    """为 board wrapper 的 struct 字段声明生成字段职责说明。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前字段声明所在的零基行号，dtype=int，unit=line index。
        str_code: 当前字段声明的净代码文本，dtype=str，unit=code text。

    返回:
        命中 board wrapper 字段规则时返回字段职责说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 只有像 `ap_uint<...> field;` 这样的 struct 字段声明才进入当前分支。
    if not re.match(r"ap_uint<\d+>\s+[A-Za-z_]\w*;$", str_code.strip()):

        # 非 struct 字段声明直接跳过。
        return ""

    # 回溯当前字段属于哪个本地 struct，区分 byte token 和 word token。
    str_struct_name = enclosing_struct_name(list_lines, int_code_index)  # 当前字段所属的本地 struct 名称

    # 复用左值提取 helper 拿到当前字段名。
    str_field_name = assigned_symbol_name(str_code.rstrip(";"))  # 当前 struct 字段的字段名

    # 逐条匹配 board wrapper 字段职责，避免重复堆叠一长串 if/return。
    for str_expected_struct, str_expected_field, str_comment_text in (
        ("axis_byte_t", "data", "data 字段在这里保存单字节输入 token 的有效载荷，后续会从 ptr_input_values[i] 截取样本写入这里。"),
        ("axis_byte_t", "last", "last 字段在这里标记输入 token 的帧尾位置，封包阶段只会在最后一个样本上把它拉高。"),
        ("axis_byte_t", "keep", "keep 字段在这里记录单字节输入 token 的字节有效标记，封包时固定写成 1 表示这一字节有效。"),
        ("axis_byte_t", "strb", "strb 字段在这里记录单字节输入 token 的写 strobe，封包时会和 keep 一起保持有效。"),
        ("axis_word_t", "data", "data 字段在这里承接 16-bit 输出 token 的主载荷，编码阶段会把递增后的样本值写进这里。"),
        ("axis_word_t", "last", "last 字段在这里保存输出 token 的帧尾标记，确保 board wrapper 写回主存时不会丢最后一个样本边界。"),
        ("axis_word_t", "keep", "keep 字段在这里记录 16-bit 输出 token 的双字节有效掩码，编码阶段会把两个字节都标成有效。"),
        ("axis_word_t", "strb", "strb 字段在这里记录 16-bit 输出 token 的双字节写 strobe，并和 keep 保持同样的有效范围。"),
    ):

        # 只有结构体名和字段名同时命中时，才返回当前字段的职责说明。
        if str_struct_name == str_expected_struct and str_field_name == str_expected_field:

            # 命中字段规则后，直接把当前字段职责返回给调用方。
            return str_comment_text

    # 其他 struct 字段暂时不在这里强制改写。
    return ""

# 为 board wrapper 的 struct 字段声明生成与摘要不同的尾注说明。
def struct_field_inline_comment_text(
    list_lines: list[str],
    int_code_index: int,
    str_code: str,
) -> str:
    """为 board wrapper 的 struct 字段声明生成与摘要不同的尾注说明。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前字段声明所在的零基行号，dtype=int，unit=line index。
        str_code: 当前字段声明的净代码文本，dtype=str，unit=code text。

    返回:
        命中 board wrapper 字段尾注规则时返回具体说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 只有 struct 字段声明才进入当前尾注分支。
    if not re.match(r"ap_uint<\d+>\s+[A-Za-z_]\w*;$", str_code.strip()):

        # 非 struct 字段声明不在这里生成尾注。
        return ""

    # 先沿字段声明所在位置向上追溯，确认它究竟归属哪一个局部 struct。
    str_struct_name = enclosing_struct_name(list_lines, int_code_index)  # 当前字段回溯命中的局部 struct 名称

    # 再从字段声明尾部提取真实成员名，避免类型前缀遮蔽 data/keep/last 这些角色词。
    str_field_name = assigned_symbol_name(str_code.rstrip(";"))  # 当前字段声明最终对应的成员名

    # 逐条匹配字段尾注，确保摘要和尾注分别强调不同的局部观察面。
    for str_expected_struct, str_expected_field, str_comment_text in (
        ("axis_byte_t", "data", "单字节输入样本最终会落到这个载荷字段。"),
        ("axis_byte_t", "last", "这个位只在最后一个输入 token 上被置高。"),
        ("axis_byte_t", "keep", "单字节输入 token 的唯一一个字节在这里标记为有效。"),
        ("axis_byte_t", "strb", "单字节写 strobe 在这里和 keep 同步拉高。"),
        ("axis_word_t", "data", "递增后的 16-bit 输出样本会落到这个主载荷字段。"),
        ("axis_word_t", "last", "输出 token 的帧尾边界由这个位透传到写回阶段。"),
        ("axis_word_t", "keep", "两个输出字节都通过这个掩码声明为有效。"),
        ("axis_word_t", "strb", "双字节写 strobe 在这里和 keep 保持一致。"),
    ):

        # 只有结构体名和字段名同时命中时，才返回当前字段的尾注说明。
        if str_struct_name == str_expected_struct and str_field_name == str_expected_field:

            # 命中字段尾注规则后，直接把当前尾注说明返回给调用方。
            return str_comment_text

    # 其他字段不在这里强制改写尾注。
    return ""

# 回溯当前字段或语句所在的本地 struct 名称，供 board wrapper 字段注释复用。
def enclosing_struct_name(list_lines: list[str], int_code_index: int) -> str:
    """回溯当前字段或语句所在的本地 struct 名称。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前字段或语句所在的零基行号，dtype=int，unit=line index。

    返回:
        命中最近本地 struct 时返回结构体名，否则返回空字符串，dtype=str，unit=struct name。
    """

    # 从当前代码位置向上扫描，直到命中最近的本地 struct 头或遇到更高层语义边界。
    for int_scan_index in range(int_code_index, -1, -1):

        # 先拿到这一行去掉尾注后的代码片段，供 struct 边界判断直接复用。
        str_scan_code = code_part(list_lines[int_scan_index]).strip()  # 当前向上回溯时看到的净代码片段

        # 空行和纯注释行不提供 struct 边界信息。
        if not str_scan_code:

            # 当前扫描行只承担段落分隔作用，不参与 struct 名称判断。
            continue

        # 命中局部 struct 头时直接返回结构体名。
        obj_match = re.match(r"struct\s+([A-Za-z_]\w*)\s*\{$", str_scan_code)  # 当前回溯代码是否正好是 struct 头

        # 一旦向上回溯命中局部 struct 头，就可以直接回传真实结构体名。
        if obj_match:

            # 命中最近的本地 struct 后，直接把结构体名返回给调用方。
            return obj_match.group(1)

        # 遇到函数签名或控制流起点后停止，避免跨越到无关区域。
        if str_scan_code.startswith(("for ", "if ", "while ", "switch ", "void ", "static ")):

            # 一旦越过更高层语义边界，就停止继续向上回溯 struct 名称。
            break

    # 未命中本地 struct 时回退为空字符串。
    return ""
