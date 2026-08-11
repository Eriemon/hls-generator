"""用薄入口协调 mock HLS source 行级注释重写。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 轻量 C/C++ 词法工具负责抽取代码正文，供行级注释重写入口复用。
from scripts.python.hls_quality_gate.readability.cpp_lexer import code_part

# 赋值子模块负责声明、赋值与结构字段的局部语义推导。
from . import mock_hls_source_assignments as source_assignments
from .mock_hls_source_assignments import (
    contextual_assignment_inline_comment_text,
    struct_field_inline_comment_text,
)

# flow 子模块负责函数签名、loop 阶段和 stream 边界的专用说明。
from . import mock_hls_source_flow as source_flow
from .mock_hls_source_flow import function_signature_inline_comment_text

# pragma 子模块负责接口、pipeline、stream 和 unroll 等 pragma 解释。
from . import mock_hls_source_pragmas as source_pragmas

# 针对 source 里的 pragma、循环、dataflow 和局部数据通路语句重写过于模板化的注释。
def rewrite_source_line_comments(list_lines: list[str]) -> list[str]:
    """按下方代码语义重写 source 相邻注释与关键尾注。

    参数:
        list_lines: 已经过行级注释治理的 source 物理行列表，dtype=list[str]，unit=source lines。

    返回:
        已按 pragma 和返回占位语义重写相邻注释的 source 行列表，dtype=list[str]，unit=source lines。
    """

    # 先复制一份源码行列表，避免调用方持有的列表被就地改写。
    list_rewritten_lines = list(list_lines)  # 当前 source 可按语义重写的物理行列表副本

    # 逐行扫描注释专用行和关键尾注，再按绑定代码的语义决定是否改写。
    for int_index, str_line in enumerate(list_rewritten_lines):

        # 只有 `//` 注释专用行才参与当前语义重写步骤。
        if str_line.strip().startswith("//"):

            # 读取当前注释下方最近的有效代码位置和文本，供语义化重写复用。
            tuple_next_code = next_meaningful_code_position(list_rewritten_lines, int_index + 1)  # 当前注释下方最近的有效代码位置与文本

            # 没有后继有效代码时不需要重写当前注释。
            if tuple_next_code is None:

                # 已经到达摘要尾部时直接跳过，不凭空补语义说明。
                continue

            # 把后继有效代码的行号与净文本拆开，供后续注释改写规则复用。
            int_code_index, str_next_code = tuple_next_code  # 当前注释绑定到的有效代码位置与文本

            # 读取当前注释的缩进，确保替换后的说明仍和下方代码对齐。
            str_indent = str_line[: len(str_line) - len(str_line.lstrip())]  # 当前注释行的前导缩进

            # 按代码语义决定新的注释正文。
            str_comment_text = rewritten_source_comment_text(list_rewritten_lines, int_code_index, str_next_code)  # 当前代码对应的语义化注释正文

            # 当前代码不属于需要重写的场景时直接跳过。
            if not str_comment_text:

                # 非目标语句继续保留原始摘要注释。
                continue

            # 把当前摘要注释替换成更具体的语义说明。
            list_rewritten_lines[int_index] = f"{str_indent}// {str_comment_text}"  # 当前注释行的语义化重写结果

            # 当前注释已经完成替换后，直接推进到下一行，避免再进入 inline comment 分支。
            continue

        # 只有带尾注的代码行才参与 inline comment 语义收紧。
        if not has_inline_source_comment(str_line):

            # 普通代码行保持原样，继续推进到下一行。
            continue

        # 抽取当前代码行的净代码文本，供 inline comment 重写规则复用。
        str_code = code_part(str_line).strip()  # 当前代码行去尾注后的净代码文本

        # 根据代码本体生成更具体的尾注正文。
        str_inline_comment_text = rewritten_inline_comment_text(list_rewritten_lines, int_index, str_code)  # 当前代码行对应的尾注重写结果

        # 当前尾注不属于需要收紧的模板场景时直接跳过。
        if not str_inline_comment_text:

            # 保留原始尾注，避免无关语句被误改。
            continue

        # 只保留代码前缀，并把尾注改写成当前阶段的专属说明。
        str_code_prefix = str_line.split("//", 1)[0].rstrip()  # 当前代码行保留到尾注之前的代码前缀

        # 写回语义化后的 inline comment。
        list_rewritten_lines[int_index] = f"{str_code_prefix} // {str_inline_comment_text}"  # 当前代码行的语义化尾注结果

    # 返回已经按 pragma/return 语义收紧后的 source 行列表。
    return list_rewritten_lines

# 为目标代码行生成更具体的注释正文；不需要改写时返回空字符串。
def rewritten_source_comment_text(
    list_lines: list[str],
    int_code_index: int,
    str_next_code: str,
) -> str:
    """为目标代码行生成更具体的注释正文。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前绑定代码所在的零基行号，dtype=int，unit=line index。
        str_next_code: 当前注释直接绑定的代码文本，dtype=str，unit=code text。

    返回:
        需要改写时返回新的中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 先读取紧随当前注释之后的阶段代码，供 pragma、循环和条件分支共用。
    str_stage_code = next_stage_code_text(list_lines, int_code_index + 1)  # 当前注释下方的阶段代码文本

    # 依次尝试签名、结构体、pragma、阶段动作和赋值边界说明，命中首个非空结果就立即返回。
    for str_comment_text in (
        source_flow.function_signature_comment_text(list_lines, int_code_index, str_next_code),
        source_assignments.struct_header_comment_text(str_next_code),
        source_assignments.struct_field_comment_text(list_lines, int_code_index, str_next_code),

        # 再尝试 pragma、阶段动作和上下文赋值边界说明。
        source_pragmas.pragma_comment_text(str_next_code, str_stage_code),
        source_flow.stage_comment_text(str_next_code, str_stage_code),
        source_assignments.contextual_assignment_comment_text(list_lines, int_code_index, str_next_code),
        source_assignments.assignment_or_return_comment_text(str_next_code, str_stage_code),
    ):

        # 当前候选一旦已经绑定到真实语义，就不再继续退回更宽泛的后续规则。
        if str_comment_text:

            # 返回首个命中的 source 注释重写结果。
            return str_comment_text

    # 其他代码行继续保留原有注释。
    return ""

# 为关键代码行生成不同于摘要注释的 inline comment，避免上下双注释落成同一句模板。
def rewritten_inline_comment_text(
    list_lines: list[str],
    int_code_index: int,
    str_code: str,
) -> str:
    """为关键代码行生成语义化尾注。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前代码所在的零基行号，dtype=int，unit=line index。
        str_code: 当前代码行的净代码文本，dtype=str，unit=code text。

    返回:
        需要改写时返回新的中文尾注，否则返回空字符串，dtype=str，unit=comment text。
    """

    # helper 多行签名里的参数尾注要先按 API 连接语义单独收口。
    str_signature_inline_comment = function_signature_inline_comment_text(list_lines, int_code_index, str_code)  # helper 多行签名参数的尾注重写结果

    # 命中 helper 签名尾注时直接返回，避免和局部 stream 声明共用一套尾注模板。
    if str_signature_inline_comment:

        # 返回当前 helper 参数的专属尾注说明。
        return str_signature_inline_comment

    # board wrapper 的 struct 字段尾注要和上方摘要形成一对一的字段角色补充。
    str_struct_field_inline_comment = struct_field_inline_comment_text(list_lines, int_code_index, str_code)  # 当前 struct 字段声明的尾注重写结果

    # 命中 struct 字段尾注时直接返回。
    if str_struct_field_inline_comment:

        # 返回当前封包字段的专属尾注。
        return str_struct_field_inline_comment

    # 某些局部赋值需要借助所在 helper 上下文去重，先在这里优先命中。
    str_contextual_inline_comment = contextual_assignment_inline_comment_text(list_lines, int_code_index, str_code)  # 当前局部赋值命中的上下文专属尾注

    # 命中上下文专属尾注时直接返回。
    if str_contextual_inline_comment:

        # 返回当前局部赋值的上下文专属尾注。
        return str_contextual_inline_comment

    # stream FIFO 声明需要用尾注补充该 FIFO 的局部阶段职责。
    if str_code.startswith("hls::stream<"):

        # 返回阶段语义化后的 FIFO 尾注说明。
        return source_flow.stream_declaration_inline_comment_text(str_code)

    # hls::task actor 的尾注要补充它连接的输入输出边界。
    if str_code.startswith("hls::task "):

        # 当前行已经是 hls::task actor 声明，直接复用专属尾注生成器。
        return source_flow.task_actor_inline_comment_text(str_code)

    # 普通局部声明也要补上和上方摘要不同的尾注语义。
    if source_assignments.is_local_declaration_statement(str_code):

        # 返回局部声明对应的语义化尾注说明。
        return source_assignments.declaration_inline_comment_text(str_code)

    # AXIS packet 的 `last` 三元赋值虽然包含比较符，仍要补成字段专属尾注。
    if ".last =" in str_code and str_code.endswith(";"):

        # 返回 AXIS `last` 字段对应的语义化尾注。
        return source_assignments.assignment_inline_comment_text(str_code)

    # 简单赋值与带初始化的局部声明需要用尾注补充读写动作本身。
    if source_assignments.is_assignment_statement(str_code):

        # 返回局部赋值/初始化语句对应的语义化尾注。
        return source_assignments.assignment_inline_comment_text(str_code)

    # `+=` 归约更新同样要补上和摘要不同的短尾注。
    if source_assignments.is_accumulation_update_statement(str_code):

        # 返回当前累计更新语句对应的语义化尾注。
        return source_assignments.accumulation_update_inline_comment_text(str_code)

    # 其他尾注继续保留原始文本。
    return ""

# 读取某个位置下方最近的一条有效代码，供相邻注释语义化改写复用。
def next_meaningful_code_position(list_lines: list[str], int_start_index: int) -> tuple[int, str] | None:
    """读取给定位置下方最近的一条有效代码位置和文本。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_start_index: 搜索起始的零基下标，dtype=int，unit=line index。

    返回:
        下方最近一条有效代码的行号与去空白文本；找不到时返回 None。
    """

    # 从调用方提供的起始下标向下扫描，直到命中下一条非空物理行为止。
    for int_offset, str_line in enumerate(list_lines[int_start_index:], start=int_start_index):

        # 纯空行不会和当前注释形成直接绑定关系。
        if not str_line.strip():

            # 空行只承担分隔作用，不影响后继代码绑定。
            continue

        # 去掉注释后的有效代码文本决定当前非空物理行是否真的是代码。
        str_code = code_part(str_line).strip()  # 当前物理行去注释后的有效代码

        # 下一条非空物理行如果仍然是注释，说明当前注释只是摘要。
        if not str_code:

            # 摘要注释下方还是注释时，不把它们误判成同一段代码绑定。
            return None

        # 找到第一条有效代码后立刻返回，保持和相邻注释的一一对应。
        return int_offset, str_code

    # 扫描到文件尾仍未命中有效代码时返回空字符串。
    return None

# 兼容旧调用方，返回给定位置下方最近一条有效代码的净文本。
def next_meaningful_code_text(list_lines: list[str], int_start_index: int) -> str:
    """读取给定位置下方最近一条有效代码的净文本。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_start_index: 搜索起始的零基下标，dtype=int，unit=line index。

    返回:
        下方最近一条有效代码的净文本；找不到时返回空字符串，dtype=str，unit=code text。
    """

    # 先读取最近的有效代码位置与文本。
    tuple_next_code = next_meaningful_code_position(list_lines, int_start_index)  # 最近有效代码的位置与文本

    # 命中时返回净文本，未命中时回退为空字符串。
    return tuple_next_code[1] if tuple_next_code is not None else ""

# 判断当前代码行是否包含可重写的 inline comment。
def has_inline_source_comment(str_line: str) -> bool:
    """判断当前代码行是否包含可重写的 inline comment。

    参数:
        str_line: 当前待检查的原始代码行文本，dtype=str，unit=line text。

    返回:
        当前代码行存在可重写的 `//` 尾注时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 只有非注释起始、且代码部分非空的 `//` 尾注才进入 inline rewrite 分支。
    return "//" in str_line and not str_line.strip().startswith("//") and bool(code_part(str_line).strip())

# 读取某条语句之后真正代表阶段语义的下一条代码，必要时跳过 PIPELINE pragma 与花括号。
def next_stage_code_text(list_lines: list[str], int_start_index: int) -> str:
    """读取某条语句之后真正代表阶段语义的下一条代码。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_start_index: 搜索起始的零基下标，dtype=int，unit=line index。

    返回:
        下一条真正代表阶段职责的净代码文本；找不到时返回空字符串，dtype=str，unit=code text。
    """

    # 直接逐行扫描，允许跳过中间的注释专用行，只保留真正代表阶段负载的代码语句。
    for str_line in list_lines[int_start_index:]:

        # 纯空行只承担段落分隔，不应中断阶段代码搜索。
        if not str_line.strip():

            # 空行直接跳过，继续寻找真正的阶段语句。
            continue

        # 注释专用行只用于说明，不代表真正执行的阶段负载。
        if str_line.strip().startswith("//"):

            # 注释行直接跳过，继续向下搜索真实代码。
            continue

        # 读取当前物理行去尾注后的净代码文本。
        str_code = code_part(str_line).strip()  # 当前物理行对应的净代码文本

        # 去尾注后为空时说明整行不是有效代码，继续向下扫描。
        if not str_code:

            # 当前物理行不提供可执行代码，继续搜索下一行。
            continue

        # 花括号、普通 HLS pragma 与 if/else 包装头不代表真正的阶段动作，继续向后扫描。
        if (
            str_code in ("{", "}")
            or str_code.startswith("#pragma HLS")
            or str_code.startswith(("if ", "else"))
        ):

            # 继续跳过结构边界、pragma 头和条件包装，寻找真实的阶段负载。
            continue

        # 命中真正代表阶段职责的语句后立刻返回。
        return str_code

    # 扫描到文件尾仍未找到真实阶段语句时回退为空字符串。
    return ""
