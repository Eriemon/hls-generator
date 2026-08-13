"""提供 FIR DATAFLOW helper 的函数签名注释规则。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
# 延迟类型注解，避免运行时提前解析规则函数的返回类型。
from __future__ import annotations

# 判断当前 helper 是否属于 FIR DATAFLOW 三阶段。
def is_fir_dataflow_helper_function_name(str_function_name: str) -> bool:
    """判断 helper 名称是否属于 staged FIR DATAFLOW 阶段。

    参数:
        str_function_name: 当前待判断的 helper 名称，dtype=str，unit=function name。

    返回:
        命中 FIR read、compute 或 write helper 时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 三个阶段使用显式名称，保证签名规则不会误伤其他 dataflow 家族。
    return str_function_name in {
        "read_fir_dataflow",
        "compute_fir_dataflow",
        "write_fir_dataflow",
    }

# 返回 FIR helper 函数头的阶段说明规则表。
def signature_fir_dataflow_header_rules() -> tuple[tuple[str, str, str], ...]:
    """返回 FIR DATAFLOW helper 的函数头说明规则表。

    参数:
        无显式业务参数；规则表固定描述 FIR 的 read、compute 和 write 阶段。

    返回:
        FIR helper 函数头规则表，dtype=tuple[tuple[str, str, str], ...]，unit=signature header rules。
    """

    # 每个函数头都同时说明阶段边界和数据流方向，避免回退到通用 HLS 句式。
    # 返回 read、compute、write 三个函数头的阶段说明。
    return (
        (
            "read_fir_dataflow",
            "static void read_fir_dataflow(",
            "read_fir_dataflow 在这里声明 FIR 读入 helper，负责把 ptr_input_values 的样本逐项送入 FIR 输入 FIFO。",
        ),
        (
            "compute_fir_dataflow",
            "static void compute_fir_dataflow(",
            "compute_fir_dataflow 在这里声明 FIR 计算 helper，负责从 FIR 输入 FIFO 取样、完成递增并产出结果 FIFO token。",
        ),
        (
            "write_fir_dataflow",
            "static void write_fir_dataflow(",
            "write_fir_dataflow 在这里声明 FIR 写回 helper，负责从结果 FIFO 取回递增样本并顺序写入 ptr_output_values。",
        ),
    )

# 返回 FIR 参数规则表，供签名参数摘要路由调用。
def signature_fir_dataflow_parameter_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回 FIR DATAFLOW helper 的参数说明规则表。

    参数:
        无显式业务参数；规则表固定描述 FIR 的主存端口、FIFO 和事务长度。

    返回:
        FIR helper 参数规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，unit=signature parameter rules。
    """

    # 参数说明把每条主存端口或 FIFO 绑定到对应阶段，不让三阶段共用同一说明。
    # 返回主存端口、FIFO 和事务长度的参数说明。
    return (
        (
            "read_fir_dataflow",
            ("ptr_input_values",),
            "ptr_input_values 在这里暴露 FIR 的输入窗口，让 read_fir_dataflow 按事务索引读取原始样本。",
        ),
        (
            "read_fir_dataflow",
            ("stream_mid_stream",),
            "stream_mid_stream 在这里承接 read_fir_dataflow 推送的输入样本，作为 FIR 计算阶段的 FIFO 边界。",
        ),
        (
            "read_fir_dataflow",
            ("int int_length",),
            "int_length 在这里限定 FIR 读入阶段的有效样本数，避免输入窗口越过本轮事务边界。",
        ),
        (
            "compute_fir_dataflow",
            ("stream_mid_stream",),
            "stream_mid_stream 在这里作为 FIR 计算阶段的输入 FIFO，提供读入阶段已经确认的样本 token。",
        ),
        (
            "compute_fir_dataflow",
            ("stream_result_stream",),
            "stream_result_stream 在这里接收 FIR 计算阶段的递增结果，等待写回阶段按序消费。",
        ),
        (
            "compute_fir_dataflow",
            ("int int_length",),
            "int_length 在这里约束 FIR 计算阶段只处理本轮有效 token 数，保持输入和结果 FIFO 的事务长度一致。",
        ),
        (
            "write_fir_dataflow",
            ("stream_result_stream",),
            "stream_result_stream 在这里作为 FIR 写回阶段的输入 FIFO，提供已经完成递增的结果样本。",
        ),
        (
            "write_fir_dataflow",
            ("ptr_output_values",),
            "ptr_output_values 在这里暴露 FIR 的输出窗口，让 write_fir_dataflow 按原索引写回结果样本。",
        ),
        (
            "write_fir_dataflow",
            ("int int_length",),
            "int_length 在这里限定 FIR 写回阶段只落盘本轮有效结果数，避免结果 FIFO 尾部越过输出边界。",
        ),
    )

# 返回 FIR 参数尾注规则表，供第二观察面路由调用。
def signature_fir_dataflow_inline_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回 FIR DATAFLOW helper 的参数尾注规则表。

    参数:
        无显式业务参数；规则表固定描述 FIR 参数的第二观察面。

    返回:
        FIR helper 尾注规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，unit=signature inline rules。
    """

    # 尾注换用不同措辞，分别补充 FIFO 生产/消费和主存边界观察面。
    # 返回参数尾注的第二观察面说明。
    return (
        (
            "read_fir_dataflow",
            ("ptr_input_values",),
            "读入阶段从这个输入窗口逐项提取 FIR 原始样本。",
        ),
        (
            "read_fir_dataflow",
            ("stream_mid_stream",),
            "读入阶段把确认后的样本压入这条 FIR 输入 FIFO。",
        ),
        (
            "read_fir_dataflow",
            ("int int_length",),
            "这里只转发当前 FIR 事务真正覆盖的输入样本数。",
        ),
        (
            "compute_fir_dataflow",
            ("stream_mid_stream",),
            "计算阶段从这条 FIR 输入 FIFO 领取待处理样本。",
        ),
        (
            "compute_fir_dataflow",
            ("stream_result_stream",),
            "计算完成的 FIR 样本先在这条结果 FIFO 中等待写回。",
        ),
        (
            "compute_fir_dataflow",
            ("int int_length",),
            "这里只对当前 FIR 事务的有效 token 执行计算。",
        ),
        (
            "write_fir_dataflow",
            ("stream_result_stream",),
            "写回阶段从这条结果 FIFO 逐项取出 FIR 递增结果。",
        ),
        (
            "write_fir_dataflow",
            ("ptr_output_values",),
            "写回阶段把结果按事务索引落到这个 FIR 输出窗口。",
        ),
        (
            "write_fir_dataflow",
            ("int int_length",),
            "这里只写出与 FIR 有效事务长度对应的结果 token。",
        ),
    )

# 返回 FIR 函数体入口规则表，标记三阶段的执行顺序。
def signature_fir_dataflow_body_entry_rules() -> tuple[tuple[str, str], ...]:
    """返回 FIR DATAFLOW helper 的函数体入口说明规则表。

    参数:
        无显式业务参数；规则表固定描述 FIR 三阶段进入函数体后的动作。

    返回:
        FIR helper 函数体入口规则表，dtype=tuple[tuple[str, str], ...]，unit=body-entry rules。
    """

    # 函数体入口分别指出样本进入、样本计算和结果落盘的顺序。
    # 返回三个阶段进入函数体后的职责说明。
    return (
        (
            "read_fir_dataflow",
            "进入 read_fir_dataflow 的函数体，开始把输入窗口样本逐项搬入 FIR 输入 FIFO。",
        ),
        (
            "compute_fir_dataflow",
            "进入 compute_fir_dataflow 的函数体，开始从 FIR 输入 FIFO 取样并把递增结果送入结果 FIFO。",
        ),
        (
            "write_fir_dataflow",
            "进入 write_fir_dataflow 的函数体，开始从结果 FIFO 取回样本并按索引写回 FIR 输出窗口。",
        ),
    )

# 汇总 FIR 函数头、参数和函数体入口的摘要说明。
def signature_fir_dataflow_comment_text(str_function_name: str, str_code: str) -> str:
    """生成 FIR helper 函数签名的摘要注释。

    参数:
        str_function_name: 当前签名片段所属的 FIR helper 名称，dtype=str，unit=function name。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中 FIR 函数头、参数或函数体入口时返回具体说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 先匹配函数头和参数，确保每个 FIR 阶段都绑定自己的 FIFO 或主存语义。
    for str_expected_name, str_code_prefix, str_comment_text in signature_fir_dataflow_header_rules():

        # FIR 函数头必须同时命中 helper 名称和签名前缀。
        if str_function_name == str_expected_name and str_code.startswith(str_code_prefix):

            # 返回当前 FIR 阶段的函数头说明。
            return str_comment_text

    # 参数规则区分输入窗口、FIFO 和事务长度。
    for str_expected_name, tuple_needles, str_comment_text in signature_fir_dataflow_parameter_rules():

        # 当前参数只有在阶段名称和角色关键字都命中时才使用专属说明。
        if str_function_name == str_expected_name and all(
            str_needle in str_code for str_needle in tuple_needles
        ):

            # 返回当前 FIR 参数的专属说明。
            return str_comment_text

    # 长度参数是三阶段共享的事务边界，也必须保留 FIR 专属语义。
    if "int int_length" in str_code:

        # 只按 helper 名称区分 read、compute 和 write 的有效边界。
        for str_expected_name, str_comment_text in (
            (
                "read_fir_dataflow",
                "int_length 在这里限定 FIR 读入 helper 只转发有效输入样本，避免输入窗口越过事务边界。",
            ),
            (
                "compute_fir_dataflow",
                "int_length 在这里限定 FIR 计算 helper 只消费本轮有效 token，保持结果 FIFO 与输入事务对齐。",
            ),
            (
                "write_fir_dataflow",
                "int_length 在这里限定 FIR 写回 helper 只落盘有效结果，避免输出窗口接收尾部无效 token。",
            ),
        ):

            # 当前长度参数命中对应阶段时，返回具体的 FIR 事务边界说明。
            if str_function_name == str_expected_name:

                # 返回当前 FIR 阶段的长度边界说明。
                return str_comment_text

    # 函数体入口说明把 FIR 的三阶段顺序明确写入生成源码。
    for str_expected_name, str_comment_text in signature_fir_dataflow_body_entry_rules():

        # 只有签名闭合行才进入函数体入口说明。
        if str_function_name == str_expected_name and str_code == ") {":

            # 返回当前 FIR helper 的函数体入口说明。
            return str_comment_text

    # 其他签名片段不在 FIR 专属路由中改写。
    return ""

# 生成 FIR helper 的签名参数尾注，保持摘要与尾注使用不同观察面。
def signature_fir_dataflow_inline_comment_text(str_function_name: str, str_code: str) -> str:
    """生成 FIR helper 函数签名参数的 inline 尾注。

    参数:
        str_function_name: 当前签名片段所属的 FIR helper 名称，dtype=str，unit=function name。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中 FIR 参数尾注规则时返回具体说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # FIR 尾注按阶段和参数角色提供不同于摘要的第二观察面。
    for str_expected_name, tuple_needles, str_comment_text in signature_fir_dataflow_inline_rules():

        # 当前尾注只有同时命中阶段名称和参数关键字时才成立。
        if str_function_name == str_expected_name and all(
            str_needle in str_code for str_needle in tuple_needles
        ):

            # 返回当前 FIR 参数的专属尾注。
            return str_comment_text

    # 其他签名尾注不在 FIR 专属路由中改写。
    return ""
