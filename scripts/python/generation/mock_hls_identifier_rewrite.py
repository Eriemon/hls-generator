"""在 mock HLS 文本里执行 typed-prefix 改名与安全标识符替换。"""

# 启用延迟注解，避免类型提示在导入阶段过早求值。
from __future__ import annotations

# 正则负责在纯代码片段里做 whole-word 标识符替换。
import re

# 宽泛类型提示用于承接外层治理入口传入的 spec 映射。
from typing import Any

# 轻量 C/C++ 解析器负责抽取函数参数和局部声明。
from scripts.python.hls_quality_gate.readability.cpp_lexer import code_part
from scripts.python.hls_quality_gate.readability.cpp_lexer import is_local_declaration
from scripts.python.hls_quality_gate.readability.cpp_lexer import parse_functions

# 声明解析与 family 推断 helper 负责提供 typed-prefix 所需的基础语义判断。
from .mock_hls_type_inference import (
    AXIS_PROTOCOL_FIELDS,
    family_from_declaration_text,
    identifier_from_declaration_text,
    identifier_from_parameter_text,
)

# 继续导入轻量声明识别与 typed-prefix 生成 helper。
from .mock_hls_type_inference import (
    looks_like_typed_declaration,
    split_parameter_texts,
    typed_name_for_identifier,
)

# 对 mock source 文本里的顶层参数、helper 参数与局部声明做 typed-prefix 重命名。
def rename_source_identifiers(text: str, dict_argument_names: dict[str, str]) -> tuple[str, dict[str, str]]:
    """对 source 文本执行顶层参数、helper 参数和局部声明的 typed-prefix 重写。

    参数:
        text: 原始 mock source 文本，shape=scalar，dtype=str，unit=text。
        dict_argument_names: 已知顶层参数原名到治理名的映射表，shape=(n items)，dtype=dict[str, str]，unit=name map。

    返回:
        重写后的 source 文本和完整替换字典组成的二元组，
        shape=(2 items)，dtype=tuple[str, dict[str, str]]，unit=rewritten text and replacements。
    """

    # 先按物理行拆分源码，供函数签名和局部声明扫描复用。
    list_lines = text.splitlines()  # 原始 mock source 的源码行列表

    # 顶层参数映射先作为替换表基线，后续再并入 helper 参数和局部声明。
    dict_replacements = dict(dict_argument_names)  # 当前 source 的完整标识符替换表

    # 先并入 helper 参数的 typed-prefix 替换项。
    merge_helper_parameter_replacements(list_lines, dict_replacements)

    # 再并入局部声明的 typed-prefix 替换项。
    merge_local_declaration_replacements(list_lines, dict_replacements)

    # 先在字符串字面量和注释之外执行标识符替换。
    str_rewritten_text = replace_identifiers_outside_strings(text, dict_replacements)  # 一轮 typed-prefix 替换后的 source 文本

    # AXIS 包装结构体存在时，把协议字段恢复成 data/keep/strb/last 原名。
    if "axis_byte_t" in str_rewritten_text or "axis_word_t" in str_rewritten_text:

        # 先构造 AXIS 协议字段的恢复映射，避免 data/keep/strb/last 被误挂 uint_ 前缀。
        dict_axis_field_rewrites = axis_field_restore_map()  # AXIS 协议字段的恢复映射表

        # 再把协议字段恢复到 HLS gate 允许的 data/keep/strb/last 原名。
        str_rewritten_text = replace_identifiers_outside_strings(str_rewritten_text, dict_axis_field_rewrites)  # 协议字段恢复后的 source 文本

    # 返回治理后的 source 文本和完整替换表。
    return (str_rewritten_text, dict_replacements)

# 把 helper 参数的 typed-prefix 替换项并入共享替换表。
def merge_helper_parameter_replacements(list_lines: list[str], dict_replacements: dict[str, str]) -> None:
    """把 helper 参数的 typed-prefix 替换项并入共享替换表。

    参数:
        list_lines: 原始 mock source 的源码行列表，shape=(n lines)，dtype=list[str]，unit=source lines。
        dict_replacements: 当前 source 的完整标识符替换表，shape=(n items)，dtype=dict[str, str]，unit=name map。

    返回:
        无返回；直接原地更新 `dict_replacements`，shape=scalar，dtype=None，unit=not applicable。
    """

    # 逐个函数签名扫描 helper 参数，补齐普通参数的 typed-prefix 名称。
    for function_info in parse_functions(list_lines):

        # 按模板深度安全拆分当前函数签名参数文本。
        for str_parameter_text in split_parameter_texts(function_info.signature):

            # 从当前参数文本中抽取参数名。
            str_name = identifier_from_parameter_text(str_parameter_text)  # 当前 helper 参数的原始名称

            # 缺少可识别参数名时跳过当前参数片段。
            if not str_name:

                # 参数名无法识别时直接继续，避免给替换表塞入伪条目。
                continue

            # 推断当前 helper 参数的 family。
            str_family = family_from_declaration_text(str_parameter_text)  # 当前 helper 参数的类型家族

            # 生成当前 helper 参数的治理名。
            str_typed_name = typed_name_for_identifier(str_name, str_family)  # 当前 helper 参数的治理后名称

            # 只有治理名和原名不同的情况下才登记替换项。
            if str_typed_name != str_name:

                # helper 参数先占住替换位，避免后续局部声明反向覆盖签名语义。
                dict_replacements.setdefault(str_name, str_typed_name)

# 把局部声明的 typed-prefix 替换项并入共享替换表。
def merge_local_declaration_replacements(list_lines: list[str], dict_replacements: dict[str, str]) -> None:
    """把局部声明的 typed-prefix 替换项并入共享替换表。

    参数:
        list_lines: 原始 mock source 的源码行列表，shape=(n lines)，dtype=list[str]，unit=source lines。
        dict_replacements: 当前 source 的完整标识符替换表，shape=(n items)，dtype=dict[str, str]，unit=name map。

    返回:
        无返回；直接原地更新 `dict_replacements`，shape=scalar，dtype=None，unit=not applicable。
    """

    # 再扫描局部声明和轻量声明形态，补齐局部变量的 typed-prefix 替换项。
    for str_raw_line in list_lines:

        # 去掉行尾注释后读取当前代码片段。
        str_code = code_part(str_raw_line).strip()  # 当前源代码行的净代码文本

        # 只处理明确局部声明或轻量声明的代码行。
        if not (is_local_declaration(str_code) or looks_like_typed_declaration(str_code)):

            # 不是局部声明时直接继续，避免误改普通表达式或控制流。
            continue

        # 从局部声明文本中抽取变量名。
        str_name = identifier_from_declaration_text(str_code)  # 当前局部声明的变量名

        # 无法抽取变量名时不追加替换项。
        if not str_name:

            # 变量名不可识别时直接继续，避免给替换表塞入伪条目。
            continue

        # 推断当前局部声明的 family。
        str_family = family_from_declaration_text(str_code)  # 当前局部声明的类型家族

        # 生成当前局部变量的治理名。
        str_typed_name = typed_name_for_identifier(str_name, str_family)  # 当前局部变量的治理后名称

        # 只有治理名发生变化时才登记替换表。
        if str_typed_name != str_name:

            # 局部声明只补充缺失映射，避免覆盖前面已经确认的参数改名。
            dict_replacements.setdefault(str_name, str_typed_name)

# 构造 AXIS 协议字段恢复映射表。
def axis_field_restore_map() -> dict[str, str]:
    """构造 AXIS 协议字段恢复映射表。

    参数:
        无参数。

    返回:
        把 `uint_data` 一类名字恢复成协议原名的映射表，shape=(n items)，dtype=dict[str, str]，unit=name map。
    """

    # 返回 AXIS 协议字段的恢复映射，确保协议字段保持 HLS 允许的原名。
    return {f"uint_{str_field_name}": str_field_name for str_field_name in AXIS_PROTOCOL_FIELDS}

# 只在代码片段中替换标识符，避免误改注释和字符串字面量。
def replace_identifiers_outside_strings(text: str, replacements: dict[str, str]) -> str:
    """在字符串和注释之外执行 whole-word 标识符替换。

    参数:
        text: 原始 HLS 文本，shape=scalar，dtype=str，unit=text。
        replacements: 原名到新名的替换映射，shape=(n items)，dtype=dict[str, str]，unit=name map。

    返回:
        只在代码区做过替换的 HLS 文本，shape=scalar，dtype=str，unit=text。
    """

    # 逐行执行安全替换，再按原始写盘习惯拼回完整文本。
    return "\n".join(replace_identifiers_in_line(str_line, replacements) for str_line in text.splitlines()) + "\n"

# 对单行 HLS 代码执行安全替换，保留字符串和行尾注释原样。
def replace_identifiers_in_line(line: str, replacements: dict[str, str]) -> str:
    """对单行 HLS 文本执行字符串外的安全标识符替换。

    参数:
        line: 单行 HLS 文本，shape=scalar，dtype=str，unit=source line。
        replacements: 原名到新名的替换映射，shape=(n items)，dtype=dict[str, str]，unit=name map。

    返回:
        当前单行替换后的文本，shape=scalar，dtype=str，unit=source line。
    """

    # 初始化输出片段和当前代码缓冲。
    list_chunks: list[str] = []  # 当前单行替换过程中已经写出的文本片段

    # 当前仍在累积的纯代码片段字符缓冲。
    list_code_buffer: list[str] = []  # 尚未进入替换阶段的源码字符缓冲

    # 初始时尚未进入字符串。
    bool_in_string = False  # 当前扫描位置是否位于字符串内部

    # 初始时尚未命中转义状态。
    bool_escape = False  # 当前字符串状态下是否刚刚读到反斜杠

    # 初始扫描位置从单行起点开始。
    int_index = 0  # 当前扫描到的字符下标

    # 逐字符扫描单行文本，区分字符串、注释和纯代码区。
    while int_index < len(line):

        # 读取当前字符，供字符串和注释状态判断复用。
        str_character = line[int_index]  # 当前扫描到的字符

        # 再读取下一个字符，供 `//` 注释起始判断复用。
        str_next_character = line[int_index + 1] if int_index + 1 < len(line) else ""  # 下一位置上的字符

        # 非字符串状态下命中 `//` 时，后续都视为注释并保持原样。
        if not bool_in_string and str_character == "/" and str_next_character == "/":

            # 先写出已经积累的纯代码片段替换结果。
            list_chunks.append(replace_code_fragment("".join(list_code_buffer), replacements))

            # 再把注释原样拼回输出并结束当前单行扫描。
            list_chunks.append(line[int_index:])

            # 当前单行已经处理完成，直接返回最终结果。
            return "".join(list_chunks)

        # 非转义的双引号会切换字符串状态。
        if str_character == '"' and not bool_escape:

            # 进入或离开字符串前先把引号本身写进当前缓冲。
            list_code_buffer.append(str_character)

            # 这里翻转字符串态，保证后续 `//` 只会在字符串外被识别成注释。
            bool_in_string = not bool_in_string  # 后续字符是否按字符串正文处理

            # 最后把扫描游标推进到下一个字符位置。
            int_index += 1  # 双引号处理后的下一个字符下标

            # 当前双引号已经处理完成，本轮直接进入下一次循环。
            continue

        # 字符串内部的字符保持原样，不参与标识符替换。
        if bool_in_string:

            # 继续把字符串字符写进当前缓冲。
            list_code_buffer.append(str_character)

            # 只有未配对的反斜杠才继续占用转义位，避免误吞真正的结束引号。
            bool_escape = str_character == "\\" and not bool_escape  # 下一字符是否需要按转义后的普通字符处理

            # 非反斜杠字符会清除上一轮留下的转义状态。
            if str_character != "\\":

                # 普通字符串字符会结束上一拍遗留的转义态。
                bool_escape = False  # 普通字符到来后的转义状态

            # 字符串字符处理完后把游标推进一位。
            int_index += 1  # 字符串内继续扫描的下一个字符下标

            # 当前字符已经处理完成，本轮直接进入下一次循环。
            continue

        # 纯代码区字符直接写入当前缓冲。
        list_code_buffer.append(str_character)

        # 当前字符处理完后把游标推进一位。
        int_index += 1  # 纯代码区继续扫描的下一个字符下标

    # 单行扫描结束后，把最后一段纯代码片段替换并写回输出。
    list_chunks.append(replace_code_fragment("".join(list_code_buffer), replacements))

    # 返回当前单行的最终替换结果。
    return "".join(list_chunks)

# 在纯代码片段里做 whole-word 标识符替换。
def replace_code_fragment(fragment: str, replacements: dict[str, str]) -> str:
    """在不含字符串和注释的代码片段中执行 whole-word 标识符替换。

    参数:
        fragment: 纯代码片段文本，shape=scalar，dtype=str，unit=code fragment。
        replacements: 原名到新名的替换映射，shape=(n items)，dtype=dict[str, str]，unit=name map。

    返回:
        当前代码片段的替换结果，shape=scalar，dtype=str，unit=code fragment。
    """

    # 没有替换项时直接保留原片段，避免多余正则扫描。
    if not replacements:

        # 直接返回原片段，表示当前代码区不需要任何改名。
        return fragment

    # 通过回调只改写完整标识符，避免误伤关键字片段或字符串残片。
    return code_fragment_with_replacements(fragment, replacements)

# 把 whole-word 替换逻辑单独拆开，便于集中维护正则命中与回调语义。
def code_fragment_with_replacements(fragment: str, replacements: dict[str, str]) -> str:
    """通过回调只改写完整标识符。

    参数:
        fragment: 纯代码片段文本，shape=scalar，dtype=str，unit=code fragment。
        replacements: 原名到新名的替换映射，shape=(n items)，dtype=dict[str, str]，unit=name map。

    返回:
        当前代码片段应用映射后的文本，shape=scalar，dtype=str，unit=code fragment。
    """

    # 先保存 whole-word 标识符的统一匹配模式，便于复用同一条替换规则。
    str_identifier_pattern = r"\b[A-Za-z_]\w*\b"  # pure code 片段里的标识符匹配模式

    # 用正则回调逐个替换完整标识符，避免误伤关键字片段。
    return re.sub(
        str_identifier_pattern,
        lambda obj_match: replacements.get(
            obj_match.group(0),
            obj_match.group(0),
        ),
        fragment,
    )
