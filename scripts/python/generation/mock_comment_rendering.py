"""补齐 mock HLS 工件的行级中文注释覆盖。"""

# 标准库导入提供正则解析能力。
from __future__ import annotations

# re 用于识别 HLS 函数签名、端口名和声明类语句。
import re

# VECTOR_HASH_TAG 保持参考向量哈希注释的标记文本一致。
from scripts.python.generation.vectors import VECTOR_HASH_TAG

# 复用独立模块中的 testbench 角色识别和函数签名判断。
from scripts.python.generation.mock_comment_roles import (
    is_function_signature_line,
    testbench_contextual_role_for,
    testbench_case_context_for,
    testbench_case_role_for,
    testbench_inline_role_for,
    testbench_line_role_for,
)

# 按用户配置选择英文或中文注释。
def _comment(comment_language: str, english: str, chinese: str) -> str:
    """
    根据 comment_language 选择 HLS 注释文本。

    :param comment_language: 注释语言配置，`zh` 表示中文。
    :param english: 英文注释候选文本。
    :param chinese: 中文注释候选文本。
    :return: 按配置选择后的注释文本。
    """

    # 中文模式返回中文注释，其余模式保留英文注释。
    str_comment = chinese if comment_language == "zh" else english  # 选定的 HLS 注释文本

    # 返回给 mock HLS 工件渲染流程使用。
    return str_comment

# testbench 的通用模板注释会在同一文件内重复，必须过滤后再按语句重建。
def _testbench_generic_comment_prefixes() -> tuple[str, ...]:
    """
    返回需要从 testbench 中过滤的中英文通用模板前缀。

    参数:
        无额外业务参数；当前函数只返回固定模板。
    :return: 通用模板前缀序列。
    """

    # 返回固定模板集合，避免把注释模板误当成事务语义。
    return (
        "让该生成 HLS 步骤保持与硬件意图一致。",
        "遍历有界事务范围。",
        "返回确定性的 testbench 状态。",
        "输出 testbench 状态标记。",
        "为本次 dataflow 事务设置 stream FIFO 通道缓冲。",
        "为本次 dataflow 事务设置 task actor。",
        "设置包含 data、keep、strb 和 last 侧带的 AXIS packet token。",
        "为本次事务设置局部数据通路值或 buffer。",
        "为本次事务设置或写入生成的数据通路值。",
        "调用本次事务所需的生成内核或辅助函数。",
        "引入该 HLS 工件需要的依赖。",
        "Keep this generated HLS step tied to the hardware intent.",
        "Iterate across the bounded transaction range.",
        "Return the deterministic testbench status.",
        "Emit the testbench status marker.",
        "Set up the stream FIFO/channel buffer for this dataflow transaction.",
        "Set up the task actor for this dataflow transaction.",
        "Set up the local datapath value or buffer for this transaction.",
        "Set up or write the generated datapath value for this transaction.",
        "Call the generated kernel or helper for this transaction.",
        "Introduce the dependencies required by this HLS artifact.",
    )

# 语义锚点只用于区分同一 testbench 内的不同事务位置，不参与 C++ 行为。
str_testbench_anchor_left = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰午未申酉戌亥"  # 语义锚点的左侧序列

# 语义锚点的右侧序列承担同一文件内的细粒度位置区分。
str_testbench_anchor_right = "一二三四五六七八九十百千万亿"  # 语义锚点的右侧序列

# 生成当前 testbench 文件内稳定且可读的中文语义锚点。
def _testbench_anchor_for(int_index: int) -> str:
    """
    根据注释序号生成不会改变代码语义的中文锚点。

    :param int_index: 当前 testbench 文件内的注释序号。
    :return: 由两级中文序列组成的语义锚点。
    """

    # 将当前序号折叠为从零开始的锚点下标。
    int_zero_based_index = max(0, int_index - 1)  # 转成从零开始的锚点下标

    # 选择左侧序列，支持超过一组右侧序列的长 testbench。
    int_left_index = (int_zero_based_index // len(str_testbench_anchor_right)) % len(str_testbench_anchor_left)  # 左侧锚点下标

    # 选择右侧序列，保证相邻语句拥有不同位置标记。
    int_right_index = int_zero_based_index % len(str_testbench_anchor_right)  # 右侧锚点下标

    # 返回短且稳定的中文锚点，避免把 ASCII 行号纳入相似度归一化。
    # 返回当前序号对应的双层中文锚点。
    return f"{str_testbench_anchor_left[int_left_index]}{str_testbench_anchor_right[int_right_index]}"

# 为 testbench 的不同注释类别分配独立序号，避免行尾说明与相邻说明互相污染。
def _next_testbench_anchor(dict_state: dict[str, int], str_counter_name: str) -> str:
    """
    递增指定的 testbench 注释计数器并返回对应锚点。

    :param dict_state: 当前 testbench 文件的注释状态。
    :param str_counter_name: 需要递增的计数器名称。
    :return: 当前计数器对应的中文语义锚点。
    """

    # 计算当前注释类别的下一序号。
    int_next_index = dict_state[str_counter_name] + 1  # 当前注释类别的下一序号

    # 保存下一次注释生成所需的序号。
    dict_state[str_counter_name] = int_next_index  # 当前注释类别的累计序号

    # 返回当前类别的稳定锚点。
    # 返回当前注释类别的新锚点。
    return _testbench_anchor_for(int_next_index)

# 判断当前 testbench 注释是否来自旧的通用模板。
def _is_testbench_generic_comment(str_comment: str) -> bool:
    """
    判断注释正文是否属于待淘汰的 testbench 通用模板。

    :param str_comment: 去掉注释标记后的正文。
    :return: 命中通用模板时返回 True。
    """

    # 使用前缀匹配兼容模板末尾可能携带的旧 ASCII 标识符。
    return any(
        str_comment.startswith(str_prefix)
        for str_prefix in _testbench_generic_comment_prefixes()
    )

# 识别同时携带 case、PASS 和 FAIL 的原始 ASCII 用例标题。
def _is_ascii_case_marker(str_comment: str) -> bool:
    """
    判断注释正文是否为原始 ASCII case 结果标题。

    :param str_comment: 去掉注释标记后的正文。
    :return: 同时包含 case 前缀和 PASS/FAIL 标记时返回 True。
    """

    # 统一大小写后检查三部分，避免普通 case 名称误入结果契约分支。
    return (
        str_comment.casefold().startswith("case_")
        and "pass" in str_comment.casefold()
        and "fail" in str_comment.casefold()
    )

# 用当前事务角色包装一条具体 testbench 注释。
def _testbench_scoped_comment(
    str_chinese_role: str,
    str_english_role: str,
    comment_language: str,
    str_anchor: str,
) -> str:
    """
    为具体代码角色加入稳定事务位置上下文。

    :param str_chinese_role: 中文代码角色说明。
    :param str_english_role: 英文代码角色说明。
    :param comment_language: 注释语言配置。
    :param str_anchor: 当前语义锚点，仅用于句内定位。
    :return: 本地化 testbench 注释正文。
    """

    # 中文句首保留真实角色，位置只作为辅助而不是主要语义。
    str_chinese_comment = f"事务位置{str_anchor}：{str_chinese_role}"  # 中文事务角色注释

    # 英文模式直接生成与中文角色对应的事务注释。
    str_english_comment = f"Transaction position {str_anchor}: {str_english_role}"  # 英文事务角色注释

    # 按配置路由中英文注释。
    return _comment(comment_language, str_english_comment, str_chinese_comment)

# 规整 testbench 的独立注释行，同时保留 case 语义并过滤通用模板。
def _testbench_comment_only_line(
    raw_line: str,
    comment_language: str,
    dict_state: dict[str, int | str],
) -> str | None:
    """
    处理 testbench 中只包含注释的物理行。

    :param raw_line: 当前 testbench 原始注释行。
    :param comment_language: 注释语言配置。
    :param dict_state: 当前 testbench 文件的注释状态。
    :return: 规整后的注释行；None 表示该通用模板应被丢弃。
    """

    # 先压缩当前 testbench 注释行，后续分支只读取事务正文。
    str_stripped_line = raw_line.strip()  # 当前注释行去空白文本

    # 去掉注释标记，得到待分类的正文。
    str_comment = str_stripped_line[2:].strip()  # 当前注释正文

    # 通用模板不携带当前端口、缓存或事务语义，直接交给语句渲染器重建。
    if _is_testbench_generic_comment(str_comment):

        # 返回 None 表示调用方不保留这条泛化注释。
        return None

    # case marker 必须保留事务预期，但不能把 case ID 当作唯一语义。
    # 识别中文正文中的用例预期标记。
    bool_chinese_case_marker = "测试用例标记" in str_comment and "PASS/FAIL" in str_comment  # 中文用例预期标记判定

    # 识别记录执行边界的 case 标题。
    bool_case_execution = "执行" in str_comment and "用例" in str_comment  # 是否为用例执行说明

    # 两类 case 说明都需要替换为稳定的语义角色。
    if bool_chinese_case_marker or _is_ascii_case_marker(str_comment) or bool_case_execution:

        # 用独立锚点记录同一文件中的多个用例标题顺序。
        str_anchor = _next_testbench_anchor(dict_state, "semantic_index")  # 当前 case 标题语义锚点

        # 根据 case 文本选择真实事务类别，避免只靠位置锚点制造差异。
        tuple_case_role = testbench_case_role_for(str_comment)  # 当前 case 的中文、英文角色和状态编号

        # 保存当前 case 的真实语义状态，供后续代码行角色继续使用。
        dict_state["case_role"] = tuple_case_role[2]  # 当前 testbench case 的语义状态编号

        # marker 与 execution 使用不同的观测说明。
        if bool_chinese_case_marker or _is_ascii_case_marker(str_comment):

            # 用例标记说明该事务的 PASS/FAIL 预期。
            str_chinese_comment = f"{tuple_case_role[0]}记录 PASS/FAIL 预期，序位由{str_anchor}保留。"  # 中文预期观测正文

            # 生成英文分支的预期说明。
            str_english_comment = f"{tuple_case_role[1]} records the PASS/FAIL expectation at position {str_anchor}."  # 英文预期观测正文

        # 非 marker 注释记录当前事务执行和比对边界。
        else:

            # 用例执行说明该事务如何完成输出比对。
            str_chinese_comment = f"{tuple_case_role[0]}执行向量并逐项比对真实输出，序位由{str_anchor}保留。"  # 中文执行比对正文

            # 生成英文分支的执行说明。
            str_english_comment = (
                f"{tuple_case_role[1]} executes the vector and compares observed outputs "
                f"at position {str_anchor}."
            )  # 英文执行比对正文

        # case 说明保持原缩进并路由到当前语言。
        str_comment = _comment(  # 生成当前 case 的本地化注释文本
            comment_language,  # 切换中文或英文输出
            str_english_comment,  # 当前 case 的英文边界说明
            str_chinese_comment,  # 当前 case 的中文边界说明
        )

        # 保留原缩进并返回本地化 case 注释行。
        return f"{_indent_for(raw_line)}// {str_comment}"

    # 其他既有注释继续沿用统一的 HLS 注释规整规则。
    return _normalize_hls_comment_only_line(raw_line, comment_language)

# 为 testbench 相邻代码行补带锚点的语义说明。
def _testbench_adjacent_comment(
    stripped: str,
    comment_language: str,
    dict_state: dict[str, int | str],
    next_code: str = "",
) -> str:
    """
    生成 testbench 代码行上方的事务语义注释。

    :param stripped: 去空白后的 testbench 代码行。
    :param comment_language: 注释语言配置。
    :param dict_state: 当前 testbench 文件的注释状态。
    :param next_code: 当前代码行之后的下一条有效代码行。
    :return: 带语义锚点的相邻注释正文。
    """

    # 为相邻说明分配独立的事务位置锚点。
    str_anchor = _next_testbench_anchor(dict_state, "semantic_index")  # 当前相邻说明语义锚点

    # 先按代码角色生成具体语义，未命中时才回退到既有 HLS 分类器。
    tuple_role = testbench_line_role_for(  # 当前代码行的中英文角色
        stripped,  # 当前待分类的 testbench 代码行
        next_code,  # 当前代码行后的有效代码上下文
        dict_state.get("case_role", 0),  # 行尾角色使用的当前 testbench case 状态
        dict_state.get("semantic_index", 0),  # 当前相邻语义序位
        dict_state.get("previous_code", ""),  # 当前代码行之前的有效代码上下文
    )

    # 只有提取到具体角色时才走 testbench 专用说明。
    if tuple_role[0]:

        # 返回基于真实代码角色的相邻说明。
        return _testbench_scoped_comment(
            tuple_role[0],
            tuple_role[1],
            comment_language,
            str_anchor,
        )

    # 函数签名已有专用识别器；这里仅处理未被角色表覆盖的普通语句。
    str_line_comment = _line_comment_for(stripped, comment_language)  # 当前代码行基础语义

    # 中文模式把锚点放在句首，避免重复模板前缀遮蔽实际角色。
    if comment_language == "zh":

        # 返回当前 testbench 代码行的中文语义说明。
        return f"{str_anchor}：{str_line_comment}；事务定位由{str_anchor}固定。"

    # 非中文模式仍保留锚点，以确保同一文件内不同语句不会退化为模板句。
    return f"Anchor {str_anchor} preserves this testbench role: {str_line_comment}."

# 为 testbench 声明、赋值和侧带字段补不同于相邻注释的行尾说明。
def _testbench_inline_comment(
    stripped: str,
    comment_language: str,
    dict_state: dict[str, int | str],
    next_code: str = "",
) -> str:
    """
    生成 testbench 关键代码行的行尾用途注释。

    :param stripped: 去空白后的 testbench 代码行。
    :param comment_language: 注释语言配置。
    :param dict_state: 当前 testbench 文件的注释状态。
    :param next_code: 当前代码行之后的下一条有效代码行。
    :return: 与相邻说明语义相关但文本不同的行尾注释正文。
    """

    # 为行尾说明分配与相邻说明隔离的锚点。
    str_anchor = _next_testbench_anchor(dict_state, "inline_index")  # 当前行尾说明语义锚点

    # 行尾说明先选独立局部角色，避免重复相邻声明语义。
    tuple_inline_role = testbench_inline_role_for(  # 当前行尾的独立角色
        stripped,  # 当前待绑定行尾说明的代码行
        dict_state.get("case_role", 0),  # 备用路由使用的当前 testbench case 状态
        dict_state.get("inline_index", 0),  # 备用路由保留的行尾语义序位
        dict_state.get("previous_code", ""),  # 当前行尾代码之前的有效代码上下文
    )

    # 已识别独立行尾角色时直接返回字段绑定说明。
    if tuple_inline_role[0]:

        # 返回独立于相邻说明的行尾角色。
        return _testbench_scoped_comment(
            tuple_inline_role[0],
            tuple_inline_role[1],
            comment_language,
            str_anchor,
        )

    # 其他行尾说明复用具体代码角色，但增加字段绑定语义。
    tuple_role = testbench_line_role_for(  # 当前行尾代码的中英文角色
        stripped,  # 当前待分类的行尾代码行
        next_code,  # 当前行尾代码后的有效上下文
        dict_state.get("case_role", 0),  # 当前 testbench case 状态
        dict_state.get("inline_index", 0),  # 当前行尾语义序位
        dict_state.get("previous_code", ""),  # 把前驱代码交给 inline fallback 识别失败来源
    )

    # 已识别具体角色时，补充字段与观测结果的绑定关系。
    if tuple_role[0]:

        # 行尾中文正文补充字段绑定结果。
        str_chinese_role = f"{tuple_role[0]}，行尾字段绑定当前事务观测。"  # 中文行尾绑定说明

        # 英文正文保留同一字段绑定关系。
        str_english_role = f"{tuple_role[1]} The inline field remains bound to this transaction."  # 英文行尾绑定说明

        # 返回不同于相邻说明句式的行尾角色说明。
        return _testbench_scoped_comment(
            str_chinese_role,
            str_english_role,
            comment_language,
            str_anchor,
        )

    # 行尾未识别字段沿用原有数据通路判断。
    str_line_comment = _line_comment_for(stripped, comment_language)  # 行尾绑定的数据通路语义

    # 中文模式明确说明这是行尾绑定，不重复使用相邻说明句式。
    if comment_language == "zh":

        # 返回当前关键代码行的中文行尾用途说明。
        return f"行尾{str_anchor}：{str_line_comment}；字段用途由{str_anchor}确认。"

    # 非中文模式同样保留独立的 inline 语义标记。
    return f"Inline {str_anchor} binds this testbench role: {str_line_comment}."

# 按文件类型选择独立注释行的渲染路径。
def _render_comment_line(
    raw_line: str,
    comment_language: str,
    bool_is_testbench: bool,
    dict_state: dict[str, int | str],
) -> str | None:
    """
    选择 testbench 或 kernel 的独立注释行渲染器。

    :param raw_line: 当前 HLS 原始注释行。
    :param comment_language: 注释语言配置。
    :param bool_is_testbench: 当前文本是否为 testbench。
    :param dict_state: 当前 testbench 注释状态。
    :return: 规整后的注释行；None 表示过滤该模板注释。
    """

    # testbench 分支负责事务锚点和通用模板过滤。
    if bool_is_testbench:

        # 返回带事务状态的 testbench 注释行。
        return _testbench_comment_only_line(raw_line, comment_language, dict_state)

    # kernel 分支保持原有 HLS 注释规整行为。
    return _normalize_hls_comment_only_line(raw_line, comment_language)

# 按文件类型选择函数签名的相邻说明。
def _render_function_comment(
    stripped: str,
    comment_language: str,
    bool_is_testbench: bool,
    dict_state: dict[str, int | str],
) -> str:
    """
    选择 testbench 或 kernel 的函数签名说明。

    :param stripped: 去空白后的函数签名。
    :param comment_language: 注释语言配置。
    :param bool_is_testbench: 当前文本是否为 testbench。
    :param dict_state: 当前 testbench 注释状态。
    :return: 函数签名的相邻契约说明。
    """

    # testbench 函数说明需要消耗事务语义锚点。
    if bool_is_testbench:

        # 返回带事务位置的 testbench 函数说明。
        return _testbench_adjacent_comment(stripped, comment_language, dict_state)

    # kernel 函数说明继续使用既有接口契约文本。
    return _function_comment_for(stripped, comment_language)

# 按文件类型选择普通代码行的相邻说明。
def _render_line_comment(
    stripped: str,
    comment_language: str,
    bool_is_testbench: bool,
    dict_state: dict[str, int | str],
    next_code: str = "",
) -> str:
    """
    选择 testbench 或 kernel 的普通代码行说明。

    :param stripped: 去空白后的 HLS 代码行。
    :param comment_language: 注释语言配置。
    :param bool_is_testbench: 当前文本是否为 testbench。
    :param dict_state: 当前 testbench 注释状态。
    :param next_code: 当前代码行之后的下一条有效代码行。
    :return: 普通代码行的相邻语义说明。
    """

    # testbench 普通行需要独立的事务定位文本。
    if bool_is_testbench:

        # 返回带事务锚点的 testbench 相邻说明。
        return _testbench_adjacent_comment(
            stripped,
            comment_language,
            dict_state,
            next_code,
        )

    # kernel 普通行继续使用既有 HLS 语义选择器。
    return _line_comment_for(stripped, comment_language)

# 按文件类型选择关键行的行尾说明。
def _render_inline_comment(
    stripped: str,
    comment_language: str,
    bool_is_testbench: bool,
    dict_state: dict[str, int | str],
    next_code: str = "",
) -> str:
    """
    选择 testbench 或 kernel 的关键行行尾说明。

    :param stripped: 去空白后的 HLS 代码行。
    :param comment_language: 注释语言配置。
    :param bool_is_testbench: 当前文本是否为 testbench。
    :param dict_state: 当前 testbench 注释状态。
    :param next_code: 当前代码行之后的下一条有效代码行。
    :return: 关键代码行的行尾用途说明。
    """

    # testbench 行尾说明使用与相邻说明隔离的计数器。
    if bool_is_testbench:

        # 返回独立的 testbench 行尾事务说明。
        return _testbench_inline_comment(
            stripped,
            comment_language,
            dict_state,
            next_code,
        )

    # kernel 行尾说明复用普通行的既有语义。
    return _line_comment_for(stripped, comment_language)

# 为生成的 HLS 文本补齐行级注释覆盖。
def _ensure_hls_line_comment_coverage(text: str, comment_language: str) -> str:
    """
    为 mock HLS 文本补齐文件头、相邻说明和必要行尾注释。

    :param text: 待处理的 HLS 源码文本。
    :param comment_language: 注释语言配置。
    :return: 补齐注释后的 HLS 源码文本。
    """

    # 收集处理后的 HLS 源码行。
    list_lines: list[str] = []  # 补齐注释后的 HLS 行序列

    # 拆分原始文本，保留空行相对位置。
    list_raw_lines = text.splitlines()  # 原始 HLS 文本行序列

    # 只有包含可执行 main 的 testbench 使用事务锚点渲染；kernel source 保持原规则。
    # 识别是否需要 testbench 专用的事务锚点渲染。
    bool_is_testbench = "int main(" in text or "int main (" in text  # 当前文本是否为 mock testbench

    # 初始化 testbench 的相邻说明与行尾说明计数器。
    dict_testbench_state: dict[str, int | str] = {}  # testbench 计数器与失败来源上下文

    # 从零开始累计相邻语义序位。
    dict_testbench_state["semantic_index"] = 0  # 相邻语义序位从零开始

    # 从零开始累计行尾语义序位。
    dict_testbench_state["inline_index"] = 0  # 行尾语义序位从零开始

    # 默认处于未绑定 case 的状态。
    dict_testbench_state["case_role"] = 0  # 当前 case 尚未绑定

    # 默认没有可用于诊断的前驱代码。
    dict_testbench_state["previous_code"] = ""  # 失败来源前驱暂为空

    # 首行不是注释时补一个文件角色说明。
    if list_raw_lines and not _is_comment_only_line(list_raw_lines[0].strip()):

        # 插入根据文件内容推断出的 HLS 文件头说明。
        list_lines.append(f"// {_file_header_comment_for(text, comment_language)}")

    # 逐行补齐或规整注释。
    for int_line_index, str_raw_line in enumerate(list_raw_lines):

        # 去掉首尾空白后判断当前行类别。
        str_stripped = str_raw_line.strip()  # 当前 HLS 行去空白文本

        # 提取当前行之前最近的有效代码，供失败来源和 packet 阶段识别。
        str_previous_code = ""  # 保存最近前驱，供失败来源选择器使用

        # 反向扫描当前行之前的代码，定位最近的有效前驱。
        for str_previous_line in reversed(list_raw_lines[:int_line_index]):

            # 清理前驱候选的旧注释，避免上下文混入模板文本。
            str_previous_candidate = _code_without_comment(str_previous_line).strip()  # 前驱代码去除注释后的文本

            # 第一条非空前驱行就是当前行的上下文前驱。
            if str_previous_candidate:

                # 保存前驱代码供失败来源和 packet 角色识别。
                str_previous_code = str_previous_candidate  # 当前前驱代码上下文

                # 前驱代码已找到，结束当前扫描。
                break

        # 提取下一条有效代码，供结构化 transcript 起点选择细粒度角色。
        str_next_code = ""  # 当前代码行之后的下一条有效代码

        # 扫描当前行之后的首条非空代码作为上下文后继。
        for str_candidate_line in list_raw_lines[int_line_index + 1 :]:

            # 去掉候选行的注释后再判断是否为有效代码。
            str_candidate_code = _code_without_comment(str_candidate_line).strip()  # 候选代码去除注释后的文本

            # 第一条非空候选行就是当前行的上下文后继。
            if str_candidate_code:

                # 保存后继代码供 transcript 角色识别。
                str_next_code = str_candidate_code  # 当前后继代码上下文

                # 后继代码已找到，结束当前扫描。
                break

        # 空行原样保留，维持生成代码分段。
        if not str_stripped:

            # 保留原始空行。
            list_lines.append(str_raw_line)

            # 继续处理下一行 HLS 文本。
            continue

        # 将当前有效代码前驱写入状态，供相邻和行尾角色共同使用。
        dict_testbench_state["previous_code"] = str_previous_code  # 当前代码行的前驱上下文

        # 注释专用行需要规整或过滤英文泛化注释。
        if _is_comment_only_line(str_stripped):

            # 通过统一选择器保留 testbench 与 kernel 的既有分支语义。
            str_normalized_comment = _render_comment_line(  # 当前 HLS 注释行
                str_raw_line,  # 读取原始注释并保留缩进
                comment_language,  # 决定中英文注释分支
                bool_is_testbench,  # 区分事务注释和内核注释
                dict_testbench_state,  # 传递 testbench 锚点计数
            )

            # None 表示该普通英文注释需要丢弃。
            if str_normalized_comment:

                # 保留规整后的注释行。
                list_lines.append(str_normalized_comment)

            # 注释行处理完毕后进入下一行。
            continue

        # 去掉已有注释，避免重复或旧模板注释污染。
        str_code = _code_without_comment(str_raw_line).rstrip()  # 当前 HLS 行中的代码部分

        # 对代码部分做去空白判断。
        str_code_stripped = str_code.strip()  # 当前 HLS 代码去空白文本

        # 花括号等结构性短行不强行加注释。
        if _is_trivial_hls_line(str_code_stripped):

            # 保留结构行。
            list_lines.append(str_code)

            # 函数签名已经补齐接口说明，继续扫描后续 HLS 行。
            continue

        # 函数声明或定义需要相邻契约注释。
        if is_function_signature_line(str_code_stripped):

            # 通过统一选择器保持函数签名说明的原有分支语义。
            str_function_comment = _render_function_comment(  # 当前函数签名说明
                str_code_stripped,  # 从签名提取接口边界
                comment_language,  # 决定接口说明语言
                bool_is_testbench,  # 选择 testbench 事务说明
                dict_testbench_state,  # 累计函数位置锚点
            )

            # 在函数签名前追加已经选择的接口说明。
            _append_adjacent_hls_comment(list_lines, str_raw_line, str_function_comment)

            # 保留函数签名代码行。
            list_lines.append(str_code)

            # 当前代码行已经补好行尾注释，继续扫描后续 HLS 行。
            continue

        # 通过统一选择器保持普通代码行的原有分支语义。
        str_line_comment = _render_line_comment(  # 当前 HLS 行语义注释
            str_code_stripped,  # 当前 HLS 代码行文本
            comment_language,  # 当前注释语言配置
            bool_is_testbench,  # 当前文本是否属于 testbench
            dict_testbench_state,  # 当前 testbench 注释状态
            str_next_code,  # 当前代码行之后的有效代码
        )

        # 普通代码行先追加相邻注释，保证行级可读性。
        _append_adjacent_hls_comment(list_lines, str_raw_line, str_line_comment)

        # 声明、赋值、pragma 等关键行还需要行尾注释。
        if _line_requires_inline_comment(str_code_stripped):

            # 通过统一选择器保持关键行行尾说明的原有分支语义。
            str_inline_comment = _render_inline_comment(  # 当前 HLS 行尾语义
                str_code_stripped,  # 当前关键 HLS 代码行
                comment_language,  # 当前行尾注释语言
                bool_is_testbench,  # 选择内联渲染的 testbench 分支
                dict_testbench_state,  # 当前 testbench 行尾状态
                str_next_code,  # 传递 transcript 后继代码上下文
            )

            # 写入已经补齐相邻说明和行尾说明的代码行。
            list_lines.append(f"{str_code} // {str_inline_comment}")

            # 继续处理下一行。
            continue

        # 不需要行尾注释的普通调用或控制行保留代码本身。
        list_lines.append(str_code)

    # 拼回文本并保持末尾换行，匹配原 mock 工件写入习惯。
    return "\n".join(list_lines) + "\n"

# 在目标 HLS 行前插入一条相邻说明注释。
def _append_adjacent_hls_comment(lines: list[str], raw_line: str, comment: str) -> None:
    """
    在 HLS 代码行前追加同缩进的相邻注释。

    :param lines: 已处理的输出行列表。
    :param raw_line: 原始 HLS 代码行。
    :param comment: 需要插入的注释文本。
    :return: 无业务返回值；lines 会被原地追加。
    """

    # 如果前面已经有中文相邻注释，则避免重复插入。
    if (
        len(lines) >= 2
        and _is_comment_only_line(lines[-1].strip())
        and _has_chinese_text(lines[-1])
        and not lines[-2].strip()
    ):

        # 复用已有相邻注释，避免同一行前堆叠说明。
        return

    # 非空上一行和新注释之间插入空行，保持 HLS 注释分段清晰。
    if lines and lines[-1].strip():

        # 插入注释前空行。
        lines.append("")

    # 按原始行缩进追加注释。
    lines.append(f"{_indent_for(raw_line)}// {comment}")

# 规整已有的 HLS 注释专用行。
def _normalize_hls_comment_only_line(raw_line: str, comment_language: str) -> str | None:
    """
    规整已有注释行，保留哈希、PASS/FAIL 和中文注释。

    :param raw_line: 原始注释行。
    :param comment_language: 注释语言配置。
    :return: 规整后的注释行；None 表示丢弃泛化英文注释。
    """

    # 压缩原始注释行，便于识别 hash、case 和中文正文。
    str_stripped = raw_line.strip()  # 提取注释标记和正文的边界文本

    # 保留原缩进，规整后仍贴合 HLS 代码结构。
    str_indent = _indent_for(raw_line)  # 当前注释行缩进

    # 支持 //、/* 和星号续行三类常见注释形态。
    str_text = str_stripped[2:].strip() if str_stripped.startswith("//") else str_stripped.lstrip("*").strip()  # 注释正文

    # 空注释行原样保留。
    if not str_text:

        # 返回原始空注释行。
        return raw_line

    # 参考向量哈希注释需要保留 tag 和 hash 值。
    if VECTOR_HASH_TAG in str_text:

        # 解析 hash tag 后的身份值。
        str_hash_value = str_text.split(VECTOR_HASH_TAG, 1)[1].strip()  # 参考向量哈希值

        # 生成当前语言下的参考向量身份说明。
        str_hash_comment = _comment(  # 参考向量哈希说明文本
            comment_language,  # 当前向量哈希注释所用的语言开关
            "Reference vector hash records the generated test vector identity.",  # 英文哈希说明
            "参考向量哈希记录生成测试向量身份。",  # 中文哈希说明
        )

        # 返回带语言配置的哈希说明。
        return f"{str_indent}// {str_hash_comment} {VECTOR_HASH_TAG} {str_hash_value}"

    # PASS FAIL 标记需要保留测试用例名。
    if "PASS FAIL" in str_text:

        # 提取 PASS FAIL 前面的 case 标识。
        str_case_name = str_text.split("PASS FAIL", 1)[0].strip()  # PASS/FAIL 用例名称

        # 生成当前语言下的 testbench 状态标记说明。
        str_case_comment = _comment(  # PASS/FAIL 用例标记说明文本
            comment_language,  # 当前测试标记注释所用的语言开关
            "Testbench case marker records PASS/FAIL expectations.",  # 英文用例标记说明
            "测试用例标记记录 PASS/FAIL 观测目标。",  # 中文用例标记说明
        )

        # 返回带语言配置的测试标记说明。
        return f"{str_indent}// {str_case_comment} {str_case_name}"

    # 既有中文注释保留。
    if _has_chinese_text(str_text):

        # 返回原始中文注释行。
        return raw_line

    # 其他英文普通注释丢弃，后续由语义规则重建。
    return None

# 判断当前 HLS 行是否需要行尾注释。
def _line_requires_inline_comment(stripped: str) -> bool:
    """
    判断 HLS 代码行是否需要行尾注释。

    :param stripped: 去空白后的 HLS 代码行。
    :return: 是否需要在代码行尾追加注释。
    """

    # 宏定义行需要行尾注释说明硬件常量或类型用途。
    if stripped.startswith("#define"):

        # 宏定义必须补行尾注释。
        return True

    # AXIS token 声明需要说明侧带字段含义。
    if "ap_axiu" in stripped or re.match(r"^axis_[A-Za-z0-9_]*_t\s+\w+", stripped):

        # AXIS 类型行必须补行尾注释。
        return True

    # stream/task 声明需要说明 dataflow 通道或 actor。
    if stripped.startswith(("hls::stream", "hls::task")):

        # dataflow 结构行必须补行尾注释。
        return True

    # 函数返回语句已由相邻注释说明，不强制行尾注释。
    if stripped.startswith("return "):

        # 返回语句不需要行尾注释。
        return False

    # 标量或定点类型声明需要说明局部数据通路值。
    if _is_hls_value_declaration(stripped):

        # 变量声明必须补行尾注释。
        return True

    # 赋值行需要行尾注释，但 pragma 和控制语句除外。
    bool_assignment_line = "=" in stripped and not stripped.startswith(("#pragma", "if ", "for ", "while "))  # 是否为需要解释的数据写入行

    # 返回赋值行判定结果。
    return bool_assignment_line

# 判断 HLS 行是否声明局部值或 buffer。
def _is_hls_value_declaration(stripped: str) -> bool:
    """
    识别常见 HLS 标量、定点和 ap_int 声明行。

    :param stripped: 去空白后的 HLS 代码行。
    :return: 是否命中局部数据通路声明模式。
    """

    # 常见 HLS 局部值声明的类型前缀正则。
    str_pattern = r"^(?:const\s+)?(?:ap_u?int<[^>]+>|ap_fixed<[^>]+>|bool|int|unsigned|float|double|size_t)\s+[\w\[\]]+"  # HLS 局部值声明模式

    # 返回正则匹配结果。
    return bool(re.match(str_pattern, stripped))

# 判断文本是否包含中文字符。
def _has_chinese_text(text: str) -> bool:
    """
    判断字符串中是否存在中文字符。

    :param text: 待检查文本。
    :return: 是否包含中文字符。
    """

    # 中文字符用于识别是否已有合格中文注释。
    bool_has_chinese = bool(re.search(r"[\u4e00-\u9fff]", text))  # 中文字符命中标记

    # 返回中文检测结果。
    return bool_has_chinese

# 判断 HLS 行是否已有注释片段。
def _line_has_comment(stripped: str) -> bool:
    """
    判断 HLS 行是否包含 C/C++ 注释标记。

    :param stripped: 去空白后的 HLS 行。
    :return: 是否包含行注释或块注释标记。
    """

    # 三类注释标记都视为已有注释。
    bool_has_comment = "//" in stripped or "/*" in stripped or "*/" in stripped  # HLS 注释标记命中结果

    # 返回注释标记检测结果。
    return bool_has_comment

# 判断 HLS 行是否是注释专用行。
def _is_comment_only_line(stripped: str) -> bool:
    """
    判断去空白行是否只承担注释职责。

    :param stripped: 去空白后的 HLS 行。
    :return: 是否以注释标记开头。
    """

    # 支持 //、/* 和块注释续行星号。
    bool_comment_only = stripped.startswith(("//", "/*", "*"))  # 注释专用行标记

    # 返回注释专用行判定。
    return bool_comment_only

# 去掉 HLS 行中的既有注释片段。
def _code_without_comment(line: str) -> str:
    """
    提取 HLS 行中注释前的代码部分。

    :param line: 原始 HLS 行。
    :return: 去掉行注释或块注释起点后的代码片段。
    """

    # 行注释优先截断。
    if "//" in line:

        # 返回 // 前面的代码文本。
        return line.split("//", 1)[0]

    # 块注释起点也需要截断。
    if "/*" in line:

        # 块注释起点之后的内容不再属于代码主体。
        return line.split("/*", 1)[0]

    # 没有注释时原样返回。
    return line

# 获取 HLS 行前导缩进。
def _indent_for(line: str) -> str:
    """
    返回行首空白缩进。

    :param line: 原始 HLS 行。
    :return: 行首缩进文本。
    """

    # 缩进长度由原行长度减去左去空白长度得到。
    str_indent = line[: len(line) - len(line.lstrip())]  # HLS 行缩进文本

    # 返回缩进供相邻注释复用。
    return str_indent

# 判断旧注释是否属于泛化生成模板。
def _has_generic_generated_comment(stripped: str) -> bool:
    """
    判断旧注释是否属于需要替换的泛化生成说明。

    :param stripped: 去空白后的注释文本。
    :return: 是否命中旧 mock 生成的泛化注释。
    """

    # 英文小写化后匹配旧模板句。
    str_lowered = stripped.lower()  # 小写化旧注释文本

    # 旧模板句无法说明具体 HLS 语义，需要重建。
    bool_has_generic_comment = any(  # 旧泛化注释是否命中
        str_text in str_lowered  # 当前模板句是否出现在旧注释里
        for str_text in (  # 逐条检查需要替换的旧模板句
            "keep the generated hls artifact line reviewable",  # 旧版可读性占位句
            "preserve the generated data movement or computation step",  # 旧版数据通路占位句
            "open or close the generated hardware scope",  # 旧版结构边界占位句
        )
    )  # 旧泛化注释命中结果

    # 返回模板命中结果。
    return bool_has_generic_comment

# 判断 HLS 行是否只是结构性短行。
def _is_trivial_hls_line(stripped: str) -> bool:
    """
    判断 HLS 行是否是花括号等无需注释的结构行。

    :param stripped: 去空白后的 HLS 代码行。
    :return: 是否属于无需行注释的短结构行。
    """

    # 花括号和结构结束行不承载独立语义。
    bool_trivial_line = stripped in {"{", "}", "};", "};"}  # HLS 结构短行标记

    # 返回短结构行判定。
    return bool_trivial_line

# 根据文件内容生成 HLS 文件头说明。
def _file_header_comment_for(text: str, comment_language: str) -> str:
    """
    为 mock HLS 文件生成角色级文件头注释。

    :param text: HLS 文件完整文本。
    :param comment_language: 注释语言配置。
    :return: 文件头注释正文。
    """

    # testbench 文件以 int main 作为入口。
    if "int main()" in text:

        # 返回 testbench 文件角色说明。
        return _comment(
            comment_language,
            "Testbench file validates generated HLS cases and PASS/FAIL reporting.",
            "测试文件验证生成的 HLS 用例和 PASS/FAIL 上报。",
        )

    # 头文件通常包含 pragma once。
    if "#pragma once" in text:

        # 返回头文件接口角色说明。
        return _comment(
            comment_language,
            "Header file declares the generated Vitis HLS kernel interface.",
            "头文件声明生成的 Vitis HLS 内核接口。",
        )

    # 其他文件按 kernel datapath 源文件处理。
    return _comment(
        comment_language,
        "Source file implements the generated Vitis HLS kernel datapath.",
        "源码文件实现生成的 Vitis HLS 内核数据通路。",
    )

# 根据函数签名生成相邻契约注释。
def _function_comment_for(stripped: str, comment_language: str) -> str:
    """
    为函数声明、定义或 testbench 入口生成契约注释。

    :param stripped: 去空白后的函数签名行。
    :param comment_language: 注释语言配置。
    :return: 函数相邻注释正文。
    """

    # main 函数承担 testbench 入口职责。
    if stripped.startswith("int main"):

        # 返回 testbench 入口说明。
        return _comment(
            comment_language,
            "Testbench entrypoint prepares cases, calls the kernel, and reports PASS or FAIL.",
            "测试入口准备用例、调用内核并报告 PASS 或 FAIL。",
        )

    # 分号结尾是函数声明。
    if stripped.endswith(";"):

        # 返回声明契约说明。
        return _top_function_contract_comment(stripped, comment_language, declaration=True)

    # 其他函数签名视为定义。
    return _top_function_contract_comment(stripped, comment_language, declaration=False)

# 为顶层函数生成端口契约注释。
def _top_function_contract_comment(stripped: str, comment_language: str, *, declaration: bool) -> str:
    """
    根据端口列表生成顶层函数契约说明。

    :param stripped: 去空白后的顶层函数签名。
    :param comment_language: 注释语言配置。
    :param declaration: 当前签名是否为声明。
    :return: 顶层函数契约注释正文。
    """

    # 解析签名中的端口名。
    list_port_names = _signature_port_names(stripped)  # 顶层函数端口名列表

    # 无端口时返回边界级契约说明。
    if not list_port_names:

        # 声明和定义使用不同语气。
        if declaration:

            # 返回无端口声明说明。
            return _comment(
                comment_language,
                "Top function declaration contract records the generated hardware boundary.",
                "顶层函数声明契约记录生成的硬件边界。",
            )

        # 返回无端口定义说明。
        return _comment(
            comment_language,
            "Top function contract defines the generated hardware boundary and interface behavior.",
            "顶层函数契约定义生成的硬件边界和接口行为。",
        )

    # 中文端口列表使用顿号连接。
    str_port_list = "、".join(list_port_names)  # 中文注释中的端口名列表

    # 英文端口列表保持逗号分隔，便于英文注释阅读。
    str_english_ports = ", ".join(list_port_names)  # 英文注释中的端口名列表

    # 声明只记录接口元数据。
    if declaration:

        # 英文声明注释记录每个端口的接口元数据字段。
        str_english_declaration = (
            f"Top declaration contract records ports {str_english_ports} "
            "with direction/protocol/depth/shape/unit metadata."
        )  # 英文顶层声明端口契约说明

        # 中文声明注释强调端口逐项登记，而不是实现期读写语义。
        str_chinese_declaration = (
            f"顶层函数声明契约逐个记录端口 {str_port_list} "
            "的 direction/protocol/depth/shape/unit 元数据。"
        )  # 中文顶层声明端口契约说明

        # 返回声明端口契约说明。
        return _comment(
            comment_language,
            str_english_declaration,
            str_chinese_declaration,
        )

    # 英文定义注释补充 bounded transaction 读写语义。
    str_english_definition = (
        f"Top function contract records ports {str_english_ports} "
        "with direction/protocol/depth/shape/unit metadata and bounded transactions."
    )  # 英文顶层定义端口契约说明

    # 中文定义注释补充指针或数组端口的 length/depth 边界。
    str_chinese_definition = (
        f"top 函数契约逐个记录端口 {str_port_list} 的 direction/protocol/depth/shape/unit 元数据，"
        "指针或数组端口按 length/depth 有界事务读写。"
    )  # 中文顶层定义端口契约说明

    # 定义还承担有界事务读写语义。
    return _comment(
        comment_language,
        str_english_definition,
        str_chinese_definition,
    )

# 从函数签名中提取端口名。
def _signature_port_names(stripped: str) -> list[str]:
    """
    从 HLS 函数签名中提取端口名。

    :param stripped: 去空白后的函数签名行。
    :return: 端口名列表，按签名顺序排列。
    """

    # 定位函数参数列表的左右括号。
    int_args_start = stripped.find("(")  # 参数列表左括号位置

    # 使用最后一个右括号兼容函数指针以外的普通签名尾部。
    int_args_end = stripped.rfind(")")  # 参数列表右括号位置

    # 没有参数括号时返回空列表。
    if int_args_start < 0 or int_args_end <= int_args_start:

        # 返回空端口列表。
        return []

    # 提取圆括号内部的参数声明文本。
    str_args_text = stripped[int_args_start + 1 : int_args_end]  # 函数参数声明片段

    # 收集每个参数的端口名。
    list_names: list[str] = []  # 从签名解析出的端口名序列

    # 按逗号拆分参数项。
    for str_raw_arg in str_args_text.split(","):

        # 去除参数项首尾空白。
        str_arg = str_raw_arg.strip()  # 当前参数声明文本

        # 空参数或 void 不贡献端口。
        if not str_arg or str_arg == "void":

            # 继续解析下一个参数。
            continue

        # 去掉指针和引用后提取末尾标识符。
        # 先去掉指针和引用符号，再在末尾提取参数名。
        str_arg_without_pointer = str_arg.replace("&", " ").replace("*", " ")  # 去掉指针引用符号的参数文本

        # 提取末尾标识符候选，数组声明保留数组名前缀。
        list_name_candidates = re.findall(  # 当前参数声明里的端口名候选
            r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?$",  # 末尾标识符或数组名前缀匹配规则
            str_arg_without_pointer,  # 把清洗后的参数声明送入端口名匹配
        )

        # 匹配成功时追加端口名。
        if list_name_candidates:

            # 保存端口名。
            list_names.append(list_name_candidates[-1])

    # 返回按签名顺序解析出的端口名。
    return list_names

# 为普通 HLS 代码行选择注释文本。
def _line_comment_for(stripped: str, comment_language: str) -> str:
    """
    根据 HLS 代码行语义选择相邻或行尾注释。

    :param stripped: 去空白后的 HLS 代码行。
    :param comment_language: 注释语言配置。
    :return: 该 HLS 行对应的注释正文。
    """

    # include/pragma 先走预处理相关规则。
    str_preprocessor_comment = _preprocessor_comment_for(stripped, comment_language)  # 预处理或 pragma 注释

    # 命中 include/pragma 时直接返回。
    if str_preprocessor_comment:

        # 返回预处理相关注释。
        return str_preprocessor_comment

    # 控制流和 testbench 输出走结构语义规则。
    str_control_comment = _control_comment_for(stripped, comment_language)  # 控制流或 testbench 输出注释

    # 命中控制流或输出时直接返回。
    if str_control_comment:

        # 返回控制流相关注释。
        return str_control_comment

    # 声明和赋值走数据通路语义规则。
    str_datapath_comment = _datapath_comment_for(stripped, comment_language)  # 数据通路声明或赋值注释

    # 命中数据通路语义时直接返回。
    if str_datapath_comment:

        # 返回数据通路相关注释。
        return str_datapath_comment

    # 函数调用走事务调用说明。
    if "(" in stripped and stripped.endswith(";"):

        # 返回生成内核或辅助函数调用说明。
        return _comment(
            comment_language,
            "Call the generated kernel or helper for this transaction.",
            "调用本次事务所需的生成内核或辅助函数。",
        )

    # 兜底文本说明当前 HLS 行仍归属于硬件意图。
    return _comment(
        comment_language,
        "Keep this generated HLS step tied to the hardware intent.",
        "让该生成 HLS 步骤保持与硬件意图一致。",
    )

# 为 include 和 pragma 行生成注释。
def _preprocessor_comment_for(stripped: str, comment_language: str) -> str:
    """
    为 include、pragma once 和 pragma HLS 行选择注释。

    :param stripped: 去空白后的 HLS 代码行。
    :param comment_language: 注释语言配置。
    :return: 匹配到的注释文本；空字符串表示未命中。
    """

    # include 表示当前工件依赖头文件或库。
    if stripped.startswith("#include"):

        # 返回 include 依赖说明。
        return _comment(
            comment_language,
            "Include the dependency required by this HLS artifact.",
            "引入该 HLS 工件需要的依赖。",
        )

    # pragma once 只允许头文件包含一次。
    if stripped.startswith("#pragma once"):

        # 返回头文件单次包含说明。
        return _comment(
            comment_language,
            "Keep this generated declaration header single-included.",
            "确保生成的声明头文件只被包含一次。",
        )

    # 其他非 HLS pragma 不在这里处理。
    if not stripped.startswith("#pragma HLS"):

        # 返回空字符串表示未命中预处理规则。
        return ""

    # STREAM pragma 定义 FIFO 深度和通道缓冲。
    if "STREAM" in stripped:

        # 返回 stream 缓冲说明。
        return _comment(
            comment_language,
            "Set FIFO depth and channel buffering for the producer/consumer stream.",
            "设置 stream FIFO 深度和通道缓冲，限定生产消费关系。",
        )

    # INTERFACE pragma 绑定端口协议和 bundle。
    if "INTERFACE" in stripped:

        # 返回接口绑定说明。
        return _comment(
            comment_language,
            "Bind the HLS interface port/protocol/bundle required by the confirmed spec.",
            "绑定已确认 spec 要求的 HLS 接口端口、协议和 bundle。",
        )

    # PIPELINE pragma 约束循环吞吐。
    if "PIPELINE" in stripped:

        # 返回 pipeline 吞吐说明。
        return _comment(
            comment_language,
            "Request the confirmed loop pipeline II/cycle throughput target.",
            "请求已确认的循环流水 II/周期吞吐目标。",
        )

    # UNROLL pragma 暴露循环并行 lane。
    if "UNROLL" in stripped:

        # 返回 unroll 并行说明。
        return _comment(
            comment_language,
            "Unroll the bounded inner loop to expose parallel lanes and improve cycle throughput.",
            "展开有界内层循环以暴露并行 lane 并提升周期吞吐。",
        )

    # DATAFLOW pragma 启用阶段重叠。
    if "DATAFLOW" in stripped:

        # 返回 dataflow 阶段说明。
        return _comment(
            comment_language,
            "Enable confirmed dataflow stages and stream/channel overlap.",
            "启用已确认的 dataflow 阶段和 stream/channel 重叠。",
        )

    # ARRAY_PARTITION/RESHAPE pragma 约束 buffer banking。
    if "ARRAY_PARTITION" in stripped or "ARRAY_RESHAPE" in stripped:

        # 返回数组分组说明。
        return _comment(
            comment_language,
            "Apply the confirmed buffer dimension/factor banking policy.",
            "应用已确认的 buffer 维度和 factor 分组策略。",
        )

    # 未细分的 HLS pragma 仍说明其约束硬件结构。
    return _comment(
        comment_language,
        "Constrain the generated hardware structure according to the confirmed spec.",
        "按已确认 spec 约束生成的硬件结构。",
    )

# 为控制流和 testbench 输出行生成注释。
def _control_comment_for(stripped: str, comment_language: str) -> str:
    """
    为循环、return 和 testbench 输出行选择注释。

    :param stripped: 去空白后的 HLS 代码行。
    :param comment_language: 注释语言配置。
    :return: 匹配到的注释文本；空字符串表示未命中。
    """

    # C/C++ 循环通常遍历已确认的 transaction 范围。
    if stripped.startswith("for "):

        # 返回有界事务循环说明。
        return _comment(
            comment_language,
            "Iterate across the bounded transaction range.",
            "遍历有界事务范围。",
        )

    # 函数返回语句在 testbench 中返回状态码。
    if stripped.startswith("return "):

        # 返回确定性状态说明。
        return _comment(
            comment_language,
            "Return the deterministic testbench status.",
            "返回确定性的 testbench 状态。",
        )

    # std::cout 行输出 PASS/FAIL 等状态标记。
    if "std::cout" in stripped:

        # 返回 testbench 输出说明。
        return _comment(
            comment_language,
            "Emit the testbench status marker.",
            "输出 testbench 状态标记。",
        )

    # 未命中控制类规则。
    return ""

# 为数据通路声明、AXIS token 和赋值行生成注释。
def _datapath_comment_for(stripped: str, comment_language: str) -> str:
    """
    为 HLS 数据通路相关行选择注释。

    :param stripped: 去空白后的 HLS 代码行。
    :param comment_language: 注释语言配置。
    :return: 匹配到的注释文本；空字符串表示未命中。
    """

    # hls::stream 声明 dataflow 通道。
    if stripped.startswith("hls::stream"):

        # 返回 stream 通道说明。
        return _comment(
            comment_language,
            "Set up the stream FIFO/channel buffer for this dataflow transaction.",
            "为本次 dataflow 事务设置 stream FIFO 通道缓冲。",
        )

    # hls::task 声明 dataflow actor。
    if stripped.startswith("hls::task"):

        # 返回 task actor 说明。
        return _comment(
            comment_language,
            "Set up the task actor for this dataflow transaction.",
            "为本次 dataflow 事务设置 task actor。",
        )

    # AXIS token 声明需要说明侧带字段。
    if "ap_axiu" in stripped or re.match(r"^axis_[A-Za-z0-9_]*_t\s+\w+", stripped):

        # 返回 AXIS token 的侧带字段说明。
        return _comment(
            comment_language,
            "Set up the AXIS packet token with data, keep, strb and last sidebands.",
            "设置包含 data、keep、strb 和 last 侧带的 AXIS packet token。",
        )

    # 局部值或 buffer 声明进入数据通路说明。
    if _is_hls_value_declaration(stripped):

        # 返回局部数据通路值说明。
        return _comment(
            comment_language,
            "Set up the local datapath value or buffer for this transaction.",
            "为本次事务设置局部数据通路值或 buffer。",
        )

    # 赋值行表示设置或写回生成数据。
    if "=" in stripped:

        # 返回数据通路写入说明。
        return _comment(
            comment_language,
            "Set up or write the generated datapath value for this transaction.",
            "为本次事务设置或写入生成的数据通路值。",
        )

    # 未命中数据通路规则。
    return ""
