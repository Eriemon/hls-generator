"""收拢 mock HLS source 里 blocked tile、stream flow 和直通阶段循环说明逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 返回 stream/AXIS 直通阶段的循环说明规则表。
def direct_stage_loop_stream_rules() -> tuple[tuple[tuple[str, ...], str], ...]:
    """返回 stream/AXIS 直通阶段的循环说明规则表。

    参数:
        无显式业务参数；当前规则表只依赖稳定的 stream/AXIS 阶段特征。

    返回:
        stream/AXIS 直通阶段的 needle 规则表，dtype=tuple[tuple[tuple[str, ...], str], ...]，unit=stage comment rules。
    """

    # 先收拢最稳定的 stream/AXIS 直通路径，避免 dataflow 主链说明被局部 buffer 规则误吞。
    return (
        (("stream_a_stream.write(", "ptr_input_a["), "遍历 input_a 的有效索引，把 A 路左操作数样本逐项推入 stream_a_stream。"),
        (("stream_in_stream.write(axis_in_pkt)",), "遍历输入窗口的有效索引，把主存样本封装成 axis_byte_t token 后逐项推入 stream_in_stream。"),
        (("stream_b_stream.write(", "ptr_input_b["), "遍历 input_b 的有效索引，把 B 路右操作数样本逐项推入 stream_b_stream。"),
        (
            ("stream_read_stream.write(", "ptr_input_values["),
            "遍历当前二维块的扁平索引，把输入窗口样本逐项送入 stream_read_stream，交给后续 row_pass 消费。",
        ),
        (
            ("stream_row_stream.write(", "stream_read_stream.read()"),
            "遍历当前二维块的样本顺序，逐项从 stream_read_stream 领取数据并转交给 stream_row_stream。",
        ),
        (("stream_reorder_stream.write(", "stream_row_stream.read()"), "遍历行向阶段产出的块样本，逐项完成重排并写入 stream_reorder_stream。"),
        (("stream_col_stream.write(", "uint_sample + 1"), "遍历重排后的块样本，逐项完成列向处理并把结果送入 stream_col_stream。"),
        (
            ("ptr_output_values[i] = stream_col_stream.read()",),
            "遍历当前二维块的扁平输出索引，把 stream_col_stream 中完成列向处理的样本逐项写回输出窗口。",
        ),
        (
            ("stream_mid_stream.write(", "stream_in_stream.read()"),
            "遍历本轮 axis token，逐项把输入样本从 stream_in_stream 转交给 stream_mid_stream。",
        ),
        (
            ("stream_result_stream.write(", "uint_sample + 1"),
            "遍历中间样本 token，逐项完成递增计算并把结果送入 stream_result_stream。",
        ),
        (
            ("stream_out_stream.write(", "stream_result_stream.read()"),
            "遍历 result stream 的有效 token，把已经递增的结果逐项送回输出流。",
        ),
        (
            ("stream_out_stream.read()", "ptr_output_values["),
            "遍历有效输出索引，把 stream_out_stream 中的 tile 求和结果逐项写回 ptr_output_values。",
        ),
        (
            ("axis_out_pkt = stream_out_stream.read()",),
            "遍历输出 token 的有效索引，从 stream_out_stream 取出 axis_word_t 封包并把 data 域写回 ptr_output_values。",
        ),
        (
            ("stream_task_stream.write(", "ptr_input_values["),
            "遍历输入窗口的有效索引，把主存样本逐项推入 stream_task_stream，供 task_graph 计算阶段继续消费。",
        ),
        (
            ("stream_task_stream.write(", "in_stream.read()"),
            "遍历本轮事务的有效 token，把上游输入流逐项送入 stream_task_stream，交给下游 compute actor。",
        ),
        (("stream_task_stream.read()",), "遍历 stream_task_stream 的有效 token，逐项取出待递增样本并交给 task_graph 计算阶段处理。"),
        (
            ("stream_task_result_stream.read()", "ptr_output_values["),
            "遍历 stream_task_result_stream 的有效 token，把已经递增的样本逐项写回 ptr_output_values。",
        ),
        (
            ("out_stream.write(", "stream_task_result_stream.read()"),
            "遍历 stream_task_result_stream 的有效 token，把已经递增的结果逐项送回 out_stream。",
        ),
    )

# 返回本地缓冲和窗口写回这类直通阶段的循环说明规则表。
def direct_stage_loop_buffer_rules() -> tuple[tuple[tuple[str, ...], str], ...]:
    """返回本地缓冲和窗口写回这类直通阶段的循环说明规则表。

    参数:
        无显式业务参数；当前规则表只依赖本地 buffer、window 和 reduction/stencil 的稳定片段。

    返回:
        本地缓冲直通阶段的 needle 规则表，dtype=tuple[tuple[tuple[str, ...], str], ...]，unit=stage comment rules。
    """

    # 再收拢本地 buffer、stencil 和 reduction 这一侧的直通循环，补齐非纯 stream 流水线场景。
    return (
        (("arr_wide_buf[", "ptr_input_values["), "遍历当前 16-lane reshape 块的局部槽位，把输入窗口样本逐项锁进 arr_wide_buf，供后续缩放写回复用。"),
        (
            ("ptr_output_values[base + j] = arr_wide_buf[j] * uint_scale_factor",),
            "遍历当前 16-lane reshape 块的有效槽位，把本地样本乘上缩放因子后逐项写回输出窗口。",
        ),
        (("arr_local_buf[", "ptr_input_values["), "遍历当前 partition 块的局部槽位，把输入窗口样本逐项锁进 arr_local_buf，供后续缩放阶段复用。"),
        (
            ("ptr_output_values[base + j] = arr_local_buf[j] * uint_scale_factor",),
            "遍历当前 partition 块的有效槽位，把本地样本乘上缩放因子后逐项写回输出窗口。",
        ),
        (("arr_block_buf[", "ptr_input_values["), "遍历当前 block 事务的局部槽位，把输入窗口样本逐项锁进 arr_block_buf，供块级处理阶段复用。"),
        (("arr_line_buf[0]", "ptr_input_values["), "遍历有效输出索引，把左邻居、中心样本和右邻居依次装入 arr_line_buf，供本轮 stencil 求和复用。"),
        (
            ("ptr_output_values[i] = arr_line_buf[0] + arr_line_buf[1] + arr_line_buf[2]",),
            "遍历有效输出索引，把 line buffer 三个邻域槽位的局部和逐项写回输出窗口。",
        ),
        (("uint_partial0", "ptr_input_values["), "遍历输入窗口的 4-sample 子块，逐项装入 partial 槽位并为后续 reduction tree 折叠准备本地样本。"),
        (("arr_lane_buf_a[", "ptr_input_a["), "遍历当前 lane-add 子块的局部槽位，把 A/B 两路输入样本逐项锁进本地 lane 缓冲。"),
        (
            ("ptr_output_values[base + j] = arr_lane_buf_a[j] + arr_lane_buf_b[j]",),
            "遍历当前 lane-add 子块的有效槽位，把 A/B 两路局部样本的和逐项写回输出窗口。",
        ),
    )

# 复用统一的规则表匹配器，避免多个循环说明函数各自维护一段长 for/if 样板。
def first_matching_stage_comment(
    str_stage_code: str,
    tuple_rules: tuple[tuple[tuple[str, ...], str], ...],
) -> str:
    """返回首条命中的阶段说明规则。

    参数:
        str_stage_code: 当前待匹配的阶段代码文本，dtype=str，unit=code text。
        tuple_rules: needle 规则与中文说明的规则表，dtype=tuple[tuple[tuple[str, ...], str], ...]，unit=stage comment rules。

    返回:
        命中首条规则时返回对应中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐条检查当前阶段代码是否命中给定的 needle 规则。
    for tuple_stage_needles, str_comment_text in tuple_rules:

        # 当前规则的全部 needle 都命中时，直接返回对应说明。
        if all(str_stage_needle in str_stage_code for str_stage_needle in tuple_stage_needles):

            # 把首条命中的说明文本交回调用方。
            return str_comment_text

    # 没有命中任何规则时返回空字符串。
    return ""

# 先按直接阶段特征匹配 `for` 循环职责，优先收拢最稳定的 dataflow 和 AXIS 场景。
def loop_comment_text_for_direct_stage(str_stage_code: str) -> str:
    """先按直接阶段特征匹配 `for` 循环职责。

    参数:
        str_stage_code: 当前循环体首条代表阶段职责的净代码文本，dtype=str，unit=code text。

    返回:
        命中直接阶段规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 先尝试 stream/AXIS 主链，再回退到本地缓冲主链，避免局部规则误吞更稳定的 dataflow 场景。
    for tuple_rules in (
        direct_stage_loop_stream_rules(),
        direct_stage_loop_buffer_rules(),
    ):

        # 先用当前规则表尝试匹配直接阶段代码，拿到这一轮回退的候选说明。
        str_comment_text = first_matching_stage_comment(str_stage_code, tuple_rules)  # 针对当前规则表抓取首条命中的阶段说明。

        # 当前规则表一旦命中说明，就立刻停止后续回退并返回结果。
        if str_comment_text:

            # 返回首个命中的直接阶段循环说明。
            return str_comment_text

    # 两组直通阶段规则都没有命中时回退为空字符串。
    return ""

# 按 blocked matmul 的 tile 结构匹配 `for` 循环职责，单独收拢块推进、载入、求和与写回。
def loop_comment_text_for_blocked_tile(str_stage_code: str) -> str:
    """按 blocked matmul 的 tile 结构匹配 `for` 循环职责。

    参数:
        str_stage_code: 当前循环体首条代表阶段职责的净代码文本，dtype=str，unit=code text。

    返回:
        命中 blocked tile 场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 先尝试结果发送/写回，再回退到推进/载入，避免输出循环被较宽泛的载入规则提前截获。
    for str_comment_text in (
        loop_comment_text_for_blocked_tile_output(str_stage_code),
        loop_comment_text_for_blocked_tile_progress(str_stage_code),
    ):

        # 当前循环一旦已经命中 blocked tile 的某一类职责，就不再继续回退。
        if str_comment_text:

            # 返回首个命中的 blocked tile 循环说明。
            return str_comment_text

    # 当前循环不属于 blocked tile 规则时回退为空字符串。
    return ""

# 按 base 推进和局部载入两类行为生成 blocked tile 循环说明。
def loop_comment_text_for_blocked_tile_progress(str_stage_code: str) -> str:
    """按 base 推进和局部载入两类行为生成 blocked tile 循环说明。

    参数:
        str_stage_code: 当前循环体首条代表阶段职责的净代码文本，dtype=str，unit=code text。

    返回:
        命中推进或载入规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 外层块推进循环要强调 base 以固定 tile 步长前移。
    if "int_chunk" in str_stage_code and "int_length" in str_stage_code:

        # 当前循环命中 blocked matmul 的外层分块推进路径时，直接返回对应说明。
        return (
            "让 base 在 [0, int_length) 范围内按 4 个样本一组推进，"
            "每轮都读取 ptr_input_a/ptr_input_b 的当前块并写回同索引的 ptr_output_values。"
        )

    # tile 载入循环要强调 A/B 两路输入块被搬进本地缓冲，但不能误吞输出或写回阶段。
    if (
        ("arr_tile_a[" in str_stage_code or "arr_tile_b[" in str_stage_code)
        and "stream_out_stream.write(" not in str_stage_code
        and "ptr_output" not in str_stage_code
        and "arr_output" not in str_stage_code
    ):

        # 当前循环命中 blocked tile 的局部缓冲载入路径时，直接返回对应说明。
        return "遍历当前 blocked tile 的 4 个 lane，把 A/B 两路输入窗口样本搬进本地块缓冲。"

    # 当前循环不属于 blocked tile 的推进或载入路径时回退为空字符串。
    return ""

# 按结果发送和结果写回两类行为生成 blocked tile 循环说明。
def loop_comment_text_for_blocked_tile_output(str_stage_code: str) -> str:
    """按结果发送和结果写回两类行为生成 blocked tile 循环说明。

    参数:
        str_stage_code: 当前循环体首条代表阶段职责的净代码文本，dtype=str，unit=code text。

    返回:
        命中结果发送或写回规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # tile 输出循环要说明局部 A/B lane 的求和发送。
    if "stream_out_stream.write(" in str_stage_code and (
        "arr_tile_a[" in str_stage_code and "arr_tile_b[" in str_stage_code
    ):

        # 当前循环命中 blocked tile 的结果发送路径时，直接返回对应说明。
        return "遍历当前 blocked tile 的有效 lane，把 A/B 局部样本求和后逐项送入 stream_out_stream。"

    # tile 写回循环要强调局部 A/B lane 的逐项落盘。
    if ("ptr_output" in str_stage_code or "arr_output" in str_stage_code) and (
        "arr_tile_a[" in str_stage_code and "arr_tile_b[" in str_stage_code
    ):

        # 当前循环命中 blocked tile 的结果写回路径时，直接返回对应说明。
        return "遍历当前 blocked tile 的有效 lane，把 A/B 局部样本求和后逐项写回输出窗口。"

    # 当前循环不属于 blocked tile 的输出或写回路径时回退为空字符串。
    return ""

# 按向量缩放与 axis 样本路径匹配 `for` 循环职责，收拢搬运、中间计算和最终写回。
def loop_comment_text_for_stream_flow(str_stage_code: str) -> str:
    """按向量缩放与 axis 样本路径匹配 `for` 循环职责。

    参数:
        str_stage_code: 当前循环体首条代表阶段职责的净代码文本，dtype=str，unit=code text。

    返回:
        命中向量缩放或 axis 流式场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 输入窗口到 load FIFO 的循环承担数据搬运职责。
    if "stream_load_stream.write(" in str_stage_code and (
        "ptr_input_values[" in str_stage_code or "arr_input_values[" in str_stage_code
    ):

        # 返回输入搬运循环说明。
        return "遍历输入窗口的有效索引，把外部样本逐项推入 load FIFO。"

    # load FIFO 到 result FIFO 的循环承担中间计算职责。
    if "stream_load_stream.read()" in str_stage_code or "stream_result_stream.write(" in str_stage_code:

        # 返回中间计算循环说明。
        return "遍历 load FIFO 的有效 token，完成递增计算并把结果送入 result FIFO。"

    # result FIFO 到输出窗口的循环承担最终写回职责。
    if "stream_result_stream.read()" in str_stage_code and (
        "ptr_output_values[" in str_stage_code or "arr_output_values[" in str_stage_code
    ):

        # 返回输出写回循环说明。
        return "遍历 result FIFO 的有效 token，把计算后的样本逐项写回输出窗口。"

    # streamofblocks 载入循环要强调每个 block 槽位都从 axis 输入流接住一个 token。
    if "arr_block_buf[" in str_stage_code and "stream_in_stream.read()" in str_stage_code:

        # 返回 streamofblocks block 载入循环说明。
        return "遍历当前 axis block 的局部槽位，把输入流里的有效 token 逐项锁进 arr_block_buf。"

    # streamofblocks 写回循环要强调递增结果按 block 槽位逐项送回输出流。
    if "stream_out_stream.write(arr_block_buf[j] + 1)" in str_stage_code:

        # 返回 streamofblocks block 写回循环说明。
        return "遍历当前 axis block 的有效槽位，把本地样本递增后逐项送回 axis 输出流。"

    # axis 输入到输出的单级循环要强调 token 级透传和计算。
    if "stream_in_stream.read()" in str_stage_code or "stream_out_stream.write(" in str_stage_code:

        # 返回 axis 样本处理循环说明。
        return "遍历 axis 样本 token，逐项完成读取、递增与输出发送。"

    # 当前循环不属于流式向量场景时回退为空字符串。
    return ""

# 为 `for` 循环生成带阶段区分的说明。
def loop_comment_text(str_stage_code: str) -> str:
    """为 `for` 循环生成带阶段区分的说明。

    参数:
        str_stage_code: 当前循环体首条代表阶段职责的净代码文本，dtype=str，unit=code text。

    返回:
        当前循环对应的中文阶段说明，dtype=str，unit=comment text。
    """

    # 先尝试匹配那些最稳定、最容易一眼定位的直接阶段规则。
    str_direct_stage_comment = loop_comment_text_for_direct_stage(str_stage_code)  # 当前循环命中的直接阶段说明

    # 只要直接阶段 helper 已经给出结果，就不必再继续落到更宽泛的 tile 或流式规则。
    if str_direct_stage_comment:

        # 返回直接阶段规则给出的循环说明。
        return str_direct_stage_comment

    # 再尝试匹配 blocked matmul 这组 tile 级循环职责。
    str_blocked_tile_comment = loop_comment_text_for_blocked_tile(str_stage_code)  # 当前循环命中的 blocked tile 说明

    # 命中 blocked tile 规则后，直接返回对应说明。
    if str_blocked_tile_comment:

        # 返回 blocked tile 规则给出的循环说明。
        return str_blocked_tile_comment

    # 最后再检查向量缩放与 axis 样本路径的搬运、计算和写回循环。
    str_stream_flow_comment = loop_comment_text_for_stream_flow(str_stage_code)  # 当前循环命中的流式事务说明

    # 命中流式事务规则后，直接返回对应说明。
    if str_stream_flow_comment:

        # 返回流式事务规则给出的循环说明。
        return str_stream_flow_comment

    # 其他循环保守回退到通用事务范围说明。
    return "遍历有界事务范围。"
