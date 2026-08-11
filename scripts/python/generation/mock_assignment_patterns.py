"""收拢 mock HLS source 里赋值模式识别、声明说明和标识符抽取逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 正则负责识别赋值、声明和左值主标识符片段。
import re

# 判断当前代码行是否属于可安全重写摘要注释的简单赋值语句。
def is_assignment_statement(str_code: str) -> bool:
    """判断当前代码行是否属于简单赋值语句。

    参数:
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。

    返回:
        当前代码属于简单赋值语句时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 先把代码文本规整成单行视图，方便做稳定的语法片段判断。
    str_stripped_code = str_code.strip()  # 当前代码行的去首尾空白文本

    # 只处理以分号结束的普通语句，避免把控制流头部误判成赋值行。
    if not str_stripped_code.endswith(";"):

        # 缺少语句结束分号时，当前代码不属于目标赋值形态。
        return False

    # pragma、return 与控制流头部都不应落到赋值摘要重写分支。
    if str_stripped_code.startswith(("#pragma", "return", "for ", "if ", "while ", "switch ", "case ")):

        # 当前代码不是需要重写摘要注释的简单赋值语句。
        return False

    # 没有简单赋值号时直接排除，避免把普通函数调用或声明误判成赋值。
    if not re.search(r"(?<![=!<>+\-*/%&|^])=(?!=)", str_stripped_code):

        # 不含赋值号的代码不进入当前重写分支。
        return False

    # 复合赋值和移位赋值会改变语义，不适合套用当前的简单赋值说明。
    if any(
        str_operator in str_stripped_code
        for str_operator in ("+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")
    ):

        # 命中非简单赋值操作符后，保留原始注释文本。
        return False

    # 其余单等号语句按简单赋值处理。
    return True

# 判断当前代码行是否属于需要专门解释的 `+=` 累计更新语句。
def is_accumulation_update_statement(str_code: str) -> bool:
    """判断当前代码行是否属于 `+=` 累计更新语句。

    参数:
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。

    返回:
        当前代码属于 `+=` 累计更新语句时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 先规整成单行视图，方便稳定识别 `+=` 语义。
    str_stripped_code = str_code.strip()  # 当前候选语句的规整单行文本

    # 非普通语句、控制流头或 pragma 都不属于这里的累计更新目标。
    if (
        not str_stripped_code.endswith(";")
        or str_stripped_code.startswith(("#pragma", "return", "for ", "if ", "while ", "switch ", "case "))
    ):

        # 当前代码不具备可重写的累计更新语句形态。
        return False

    # 只有显式 `+=` 才按累计更新处理。
    return "+=" in str_stripped_code

# 为 `+=` 这类累计更新语句生成局部折叠说明。
def accumulation_update_comment_text(str_code: str) -> str:
    """为 `+=` 累计更新语句生成局部折叠说明。

    参数:
        str_code: 当前累计更新语句的净代码文本，dtype=str，unit=code text。

    返回:
        当前累计更新语句对应的中文说明，dtype=str，unit=comment text。
    """

    # 拆出左值文本，供累计状态角色识别复用。
    str_left_text = str_code.split("+=", 1)[0].strip()  # 当前累计更新语句的左值净文本

    # 再拆出本轮回灌到累计节点的右值表达式，供 reduction tree 来源判断复用。
    str_right_text = str_code.split("+=", 1)[1].rsplit(";", 1)[0].strip()  # 当前这轮 += 回灌表达式去掉分号后的净文本

    # 抽取左值主标识符，避免成员链遮蔽真正的累计状态名。
    str_symbol_name = assigned_symbol_name(str_left_text)  # 当前累计更新语句左值对应的主标识符

    # reduction tree 的块级累加要明确它把四路 partial 再折叠回全局累加器。
    if (
        str_symbol_name == "uint_tree_accum"
        and "uint_partial0" in str_right_text
        and "uint_partial3" in str_right_text
    ):

        # 交回 reduction tree 把两级部分和继续回折到主累加器的说明。
        return "uint_tree_accum 在这里把当前 4-sample 子块的两级部分和继续折叠回全局归约累加器，显式保留 reduction tree 的块级汇总边界。"

    # 其他累计状态至少要说明当前右值仍在回灌本地累加节点。
    if any(str_keyword in str_symbol_name for str_keyword in ("acc", "sum", "result")):

        # 交回普通累计节点把当前子表达式继续回灌本地状态的说明。
        return f"{str_symbol_name} 在这里把当前子表达式结果继续折叠回本地累计状态，保证后续写回阶段读取到最新汇总值。"

    # 其余 `+=` 更新回退到保守的累计语义说明。
    return f"{str_symbol_name} 在这里把当前局部结果继续累加回目标状态，显式保留这次更新的折叠边界。"

# 为 `+=` 累计更新语句生成不同于摘要注释的短尾注。
def accumulation_update_inline_comment_text(str_code: str) -> str:
    """为 `+=` 累计更新语句生成语义化尾注。

    参数:
        str_code: 当前累计更新语句的净代码文本，dtype=str，unit=code text。

    返回:
        当前累计更新语句对应的中文尾注，dtype=str，unit=comment text。
    """

    # 先拆出 `+=` 左边的累计节点文本，便于判断是谁在承接这次回灌。
    str_left_text = str_code.split("+=", 1)[0].strip()  # 当前累计节点的左值净文本

    # 再拆出 `+=` 右边这次要回灌的表达式，便于识别专属累计来源。
    str_right_text = str_code.split("+=", 1)[1].rsplit(";", 1)[0].strip()  # 当前这次回灌到累计节点的右值净文本

    # 最后抽取左值里真正承担累计职责的主标识符。
    str_symbol_name = assigned_symbol_name(str_left_text)  # 当前累计节点对应的主标识符

    # reduction tree 的累加尾注要直接点明“把当前 4-sample 子块折叠回累加器”。
    if (
        str_symbol_name == "uint_tree_accum"
        and "uint_partial0" in str_right_text
        and "uint_partial3" in str_right_text
    ):

        # 交回 reduction tree 子块部分和继续回折到主累加器的短尾注。
        return "当前 4-sample 子块的部分和会在这里继续折叠回全局累加器。"

    # 其他累计状态统一保留简短的回灌说明。
    if any(str_keyword in str_symbol_name for str_keyword in ("acc", "sum", "result")):

        # 交回普通累计节点继续回灌局部结果的短尾注。
        return "这里继续把当前局部结果回灌到累计状态里。"

    # 未命中专属模式时不强制追加尾注。
    return ""

# 判断当前代码行是否属于不带初始化的局部声明语句。
def is_local_declaration_statement(str_code: str) -> bool:
    """判断当前代码行是否属于不带初始化的局部声明语句。

    参数:
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。

    返回:
        当前代码属于不带初始化的局部声明时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 先把当前语句压成规整单行，后面的声明正则才能稳定识别类型前缀和变量位点。
    str_stripped_code = str_code.strip()  # 当前声明候选语句的规整单行文本

    # 只处理以分号结束的普通语句，避免把控制流头部误判成声明行。
    if not str_stripped_code.endswith(";"):

        # 缺少语句结束分号时，当前代码不属于目标声明形态。
        return False

    # pragma、return 与控制流头部都不应落到当前局部声明分支。
    if str_stripped_code.startswith(
        (
            "#pragma",
            "return",
            "for ",
            "if ",
            "while ",
            "switch ",
            "case ",
            "else",
            "do ",
        )
    ):

        # 当前代码不是需要重写的局部声明语句。
        return False

    # 带初始化的声明已由赋值分支处理，这里只收不带等号的声明。
    if "=" in str_stripped_code:

        # 带初始化的语句继续交给赋值说明路径处理。
        return False

    # 纯函数调用、assert 和普通表达式通常包含括号，不应误判成本地声明。
    if "(" in str_stripped_code or ")" in str_stripped_code:

        # 带调用形态的代码不进入当前局部声明分支。
        return False

    # 使用稳定的类型前缀集合识别常见 HLS/C++ 局部声明。
    return bool(
        re.match(
            r"^(?:const\s+)?(?:static\s+)?(?:unsigned\s+|signed\s+)?"
            r"(?:bool|char|short|int|long|float|double|ap_[A-Za-z0-9_]+<[^;>]+>|hls::stream<[^;>]+>|[A-Za-z_]\w*)"
            r"\s+[*&]*[A-Za-z_]\w*(?:\[[^\]]+\])?;$",
            str_stripped_code,
        )
    )

# 为不带初始化的局部声明生成更具体的缓冲或 scratchpad 说明。
def declaration_comment_text(str_declaration_code: str) -> str:
    """为不带初始化的局部声明生成局部状态说明。

    参数:
        str_declaration_code: 当前局部声明语句的净代码文本，dtype=str，unit=code text。

    返回:
        当前局部声明对应的中文说明，dtype=str，unit=comment text。
    """

    # 先抽取局部声明最终对应的主标识符，方便按硬件角色分类。
    str_symbol_name = assigned_symbol_name(str_declaration_code.rstrip(";"))  # 当前局部声明解析出的主标识符

    # 先匹配已经有明确硬件职责的局部声明。
    for str_expected_name, str_comment_text in (
        (
            "arr_tile_a",
            "arr_tile_a 在这里暂存当前 blocked tile 的 A 路输入片段，让后续逐 lane 写回时直接复用左操作数窗口。",
        ),
        (
            "arr_tile_b",
            "arr_tile_b 在这里暂存当前 blocked tile 的 B 路配对片段，专门给后续逐 lane 求和准备右操作数窗口。",
        ),
        (
            "axis_out_pkt",
            "axis_out_pkt 在这里暂存即将发往下游的 16-bit AXIS 输出 token，后续会逐项补齐 data、keep、strb 和 last 字段。",
        ),
        (
            "axis_in_pkt",
            "axis_in_pkt 在这里暂存从主存窗口封装出的单字节 AXIS 输入 token，后续会逐项补齐 data、keep、strb 和 last 字段。",
        ),
        (
            "arr_wide_buf",
            "arr_wide_buf 在这里暂存一个 reshape 块里的并行输入样本，让缩放阶段按 16 路宽度复用当前块数据。",
        ),
        (
            "arr_local_buf",
            "arr_local_buf 在这里暂存一个 partition 块里的输入样本，让后续缩放写回沿用同一组局部 lane 数据。",
        ),
        (
            "arr_block_buf",
            "arr_block_buf 在这里暂存当前 stream-of-blocks 事务的本地块样本，让块内处理和输出发送共享同一组中间数据。",
        ),
        (
            "arr_line_buf",
            "arr_line_buf 在这里保存 3-tap stencil 需要的左、中、右邻域样本，避免当前输出点重复回读输入窗口。",
        ),
        (
            "arr_lane_buf_a",
            "arr_lane_buf_a 在这里暂存当前 lane-add 块的 A 路输入样本，让后续逐 lane 相加时直接复用本地块数据。",
        ),
        (
            "arr_lane_buf_b",
            "arr_lane_buf_b 在这里暂存当前 lane-add 块的 B 路输入样本，供逐 lane 相加阶段和 A 路缓冲配对消费。",
        ),
    ):

        # 命中专属局部声明后，直接返回对应说明。
        if str_symbol_name == str_expected_name:

            # 返回当前局部状态的专属说明。
            return str_comment_text

    # 其他数组声明回退到保守 scratchpad 说明。
    if str_symbol_name.startswith("arr_"):

        # 返回局部数组缓冲的保守说明。
        return f"{str_symbol_name} 在这里保留当前事务复用的局部数组缓冲，避免后续循环重复回读外部接口。"

    # 其他声明暂时不强制改写。
    return ""

# 为不带初始化的局部声明生成与上方摘要不同的尾注说明。
def declaration_inline_comment_text(str_declaration_code: str) -> str:
    """为不带初始化的局部声明生成语义化尾注。

    参数:
        str_declaration_code: 当前局部声明语句的净代码文本，dtype=str，unit=code text。

    返回:
        当前局部声明对应的中文尾注说明，dtype=str，unit=comment text。
    """

    # 先抽出需要补尾注的局部对象名，后面才能判断它究竟是 tile 缓冲还是 AXIS packet。
    str_symbol_name = assigned_symbol_name(str_declaration_code.rstrip(";"))  # 当前尾注分支命中的局部对象名

    # 只对少数需要尾注补充视角的局部声明追加说明。
    for str_expected_name, str_comment_text in (
        ("arr_tile_a", "先锁住当前块从 input_a 读入的 4 个左操作数 lane。"),
        ("arr_tile_b", "先锁住当前块从 input_b 读入的 4 个右操作数配对 lane。"),
        ("axis_out_pkt", "先把本轮输出 token 的各个 AXIS 字段装进这个本地 packet。"),
        ("axis_in_pkt", "先把主存样本封装成单字节输入 packet，再送进 stream_in_stream。"),
        ("arr_wide_buf", "这 16 个槽位会先锁住当前 reshape 块里的并行输入样本。"),
        ("arr_local_buf", "这个 partition 缓冲会先锁住当前块里的待缩放样本。"),
        ("arr_block_buf", "当前 block 的局部样本会先收进这个 4-lane 缓冲。"),
        ("arr_line_buf", "这条 line buffer 会同时保存左、中、右三个邻域槽位。"),
        ("arr_lane_buf_a", "当前块的 A 路 lane 样本会先锁进这个本地缓冲。"),
        ("arr_lane_buf_b", "当前块的 B 路配对样本会先锁进这个本地缓冲，等待和 A 路 lane 一一合并。"),
    ):

        # 命中需要额外尾注的局部声明后，直接返回对应说明。
        if str_symbol_name == str_expected_name:

            # 返回当前局部声明的补充尾注。
            return str_comment_text

    # 其他声明不在这里强制改写尾注。
    return ""

# 从赋值语句左值里抽取主标识符，避免索引和成员访问干扰角色判断。
def assigned_symbol_name(str_left_text: str) -> str:
    """从赋值左值表达式里抽取主标识符。

    参数:
        str_left_text: 当前赋值语句等号左侧文本，dtype=str，unit=left-hand-side text。

    返回:
        左值对应的主标识符；无法抽取时回退为 `unnamed_value`，dtype=str，unit=identifier。
    """

    # 先去掉数组索引片段，避免最后一个标识符被循环索引变量抢占。
    str_base_text = re.sub(r"\[[^\]]*\]", "", str_left_text).strip()  # 去掉索引片段后的左值主体文本

    # 成员访问时优先保留最终成员名，方便按输出 token 或局部状态角色分类。
    if "->" in str_base_text:

        # 取结构体指针访问链的末端成员名。
        str_base_text = str_base_text.split("->")[-1].strip()  # 箭头访问链末端的真实成员名

    # 普通对象点访问也只保留最终字段名，避免结构体前缀遮蔽真正角色词。
    if "." in str_base_text:

        # 取结构体点访问链的末端成员名。
        str_base_text = str_base_text.split(".")[-1].strip()  # 点访问链末端的真实成员名

    # 从剩余文本里抽取全部标识符，并优先取最后一个真实左值名字。
    list_identifier_tokens = re.findall(r"[A-Za-z_]\w*", str_base_text)  # 左值主文本中的标识符列表

    # 命中标识符时返回最后一个候选；否则回退到稳定占位名。
    return list_identifier_tokens[-1] if list_identifier_tokens else "unnamed_value"
