"""收拢 mock HLS source 输出窗口写回的 inline 尾注规则。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 返回 fence/reduction/stencil 这组输出寄存器写回的尾注。
def assignment_inline_output_register_comment_text(str_right_text: str) -> str:
    """返回 fence/reduction/stencil 这类结果寄存器写回的尾注。

    参数:
        str_right_text: 当前输出窗口右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中结果寄存器写回尾注场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # fence ordering 的输出写回只要强调这里落盘的是已经排好顺序的本地结果。
    if str_right_text == "uint_ordered_writeback":

        # 把排序合并后的待写回值落盘语义压成一行尾注交回。
        return "这里把本地排序合并后的待写回值正式落到输出窗口。"

    # reduction tree 的最终写回只需要强调这是整轮归约后的汇总值。
    if str_right_text == "uint_tree_accum":

        # 把整轮归约累计结果落盘语义压成一行尾注交回。
        return "这里把整轮归约的累计结果正式写回输出窗口。"

    # stencil 的输出尾注要强调三个邻域槽位已经在这里合成一个结果。
    if all(str_term in str_right_text for str_term in ("arr_line_buf[0]", "arr_line_buf[1]", "arr_line_buf[2]")):

        # 把三个邻域槽位合成 stencil 输出的尾注交回调用方。
        return "三个邻域槽位会在这里合成一个 3-tap stencil 输出样本。"

    # 其他结果寄存器写回不在这里补 inline 尾注。
    return ""

# 返回本地缓冲和直通输出写回的尾注。
def assignment_inline_output_buffer_comment_text(str_right_text: str) -> str:
    """返回本地缓冲和直通输出写回的尾注。

    参数:
        str_right_text: 当前输出窗口右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中本地缓冲或直通输出写回尾注场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 向量缩放的输出尾注要保留“本地样本乘缩放因子再写回”的观察面。
    if "arr_wide_buf[" in str_right_text and "uint_scale_factor" in str_right_text:

        # 把本地槽位样本乘缩放因子后落盘的尾注交回调用方。
        return "当前本地槽位样本会在这里乘上缩放因子再写回输出窗口。"

    # partition 缩放路径也要单独保留局部块样本的落盘语义。
    if "arr_local_buf[" in str_right_text and "uint_scale_factor" in str_right_text:

        # 把 partition 局部块样本乘缩放因子后落盘的尾注交回调用方。
        return "partition 块当前槽位的样本会在这里乘上缩放因子后落盘。"

    # lane add 的输出尾注只要说明这里写回的是两路 lane 的和。
    if "arr_lane_buf_a[" in str_right_text and "arr_lane_buf_b[" in str_right_text:

        # 把当前 lane 上 A/B 两路局部样本求和落盘的尾注交回调用方。
        return "这里写回的是当前 lane 上 A/B 两路局部样本的和。"

    # 旧式 vector_scale 参数名也要明确表达乘法，而不能落入递增兜底文案。
    if "uint_scale_factor" in str_right_text and "ptr_input" in str_right_text:

        # 返回输入样本乘运行时因子后写回输出窗口的尾注。
        return "把输入样本乘上运行时缩放因子后写入输出窗口。"

    # 输入窗口直写输出窗口时，只需要强调当前样本已经完成递增并即将落回外部窗口。
    if "ptr_input" in str_right_text or "arr_input" in str_right_text:

        # 当前写回是输入样本递增后的直通路径时，直接返回最短尾注说明。
        return "把递增后的样本写入输出窗口。"

    # 其他缓冲或直通写回不在这里补 inline 尾注。
    return ""

# 返回 AXIS/FIFO/task/block 等输出写回的尾注。
def assignment_inline_output_stream_comment_text(str_right_text: str) -> str:
    """返回 AXIS/FIFO/task/block 等输出写回的尾注。

    参数:
        str_right_text: 当前输出窗口右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 AXIS/FIFO/task/block 输出写回尾注场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # result FIFO 写回要说明当前读出的就是待落盘样本。
    if "stream_result_stream.read()" in str_right_text:

        # 当前写回来自 result FIFO 时，直接返回待写回样本说明。
        return "从 result FIFO 读取待写回样本。"

    # task_graph 的 result stream 写回要强调当前读出的已经是本轮最终输出样本。
    if "stream_task_result_stream.read()" in str_right_text:

        # 当前写回来自 task result stream 时，直接返回 task_graph 专属尾注。
        return "从 task result stream 读取本轮待写回样本。"

    # blocked matmul 的输出写回要强调这里只写出 A/B lane 的逐项求和结果。
    if "arr_tile_a[" in str_right_text and "arr_tile_b[" in str_right_text:

        # 当前写回命中 blocked tile 的逐 lane 求和路径时，直接返回对应尾注。
        return "把 A/B tile 对应 lane 的和写回输出窗口。"

    # AXIS packet 拆包写回时，只强调当前取出的是 data 域载荷。
    if "axis_out_pkt.data" in str_right_text:

        # 当前写回来自 axis packet 的 data 域时，直接返回拆包尾注。
        return "这里只把 axis_word_t 的 data 域拆出来写回主存窗口。"

    # dataflow 输出 FIFO 写回时，要强调当前消费的是已经完成 tile 求和的结果。
    if "stream_out_stream.read()" in str_right_text:

        # 当前写回来自 stream_out_stream 时，直接返回 dataflow 结果尾注。
        return "从 stream_out_stream 取出已经完成 tile 求和的结果。"

    # block transform 的列向写回要强调这里取出的是列向处理完成的块样本。
    if "stream_col_stream.read()" in str_right_text:

        # 把列向处理完成的块样本写回输出窗口的尾注交回调用方。
        return "这里从 stream_col_stream 取回已经完成列向处理的块样本。"

    # 其他 stream/task/block 写回不在这里补 inline 尾注。
    return ""

# 汇总输出窗口写回的 inline 尾注路由，优先命中专属子规则，再回退为空串。
def assignment_inline_output_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """按输出窗口写回语义生成尾注说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前输出窗口右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中输出窗口尾注规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 非输出窗口写回不在这里生成尾注，直接回退给后续局部状态或 AXIS 字段规则。
    if not str_symbol_name.startswith(("ptr_output", "arr_output")):

        # 当前左值不是输出窗口时，不在这里追加写回尾注。
        return ""

    # 输出窗口尾注依次匹配结果寄存器、本地缓冲/直通和 stream/task/block 路径。
    for str_comment_text in (
        assignment_inline_output_register_comment_text(str_right_text),
        assignment_inline_output_buffer_comment_text(str_right_text),
        assignment_inline_output_stream_comment_text(str_right_text),
    ):

        # 只要命中任何一条输出窗口尾注，就立即返回。
        if str_comment_text:

            # 把首条命中的输出窗口尾注交回调用方。
            return str_comment_text

    # 其他输出窗口写回不在这里强制补尾注。
    return ""
