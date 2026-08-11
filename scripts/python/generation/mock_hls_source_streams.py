"""收拢 mock HLS source 里 FIFO 声明、通道映射和 stream 写回说明逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 返回通用 stream 名称到阶段边界说明的映射表。
def generic_stream_comment_maps() -> tuple[tuple[str, str, str, str], ...]:
    """返回通用 stream 名称的摘要、尾注和 depth 说明映射表。

    参数:
        无显式业务参数；当前映射表只依赖通用 dataflow/block helper 使用的 stream 名称。

    返回:
        stream 名称到摘要、尾注和 depth 说明的规则表，dtype=tuple[tuple[str, str, str, str], ...]，unit=stream comment maps。
    """

    # 统一保存摘要注释、inline 尾注和 STREAM depth 说明，避免三处再写三套名称分支。
    return (
        (
            "stream_mid_stream",
            "stream_mid_stream 在这里暂存读入阶段刚取出的 axis 样本，作为输入读取到递增计算之间的 FIFO 边界。",
            "读入阶段先把外部 token 暂存在这条中间 FIFO 里。",
            "为 stream_mid_stream 指定显式 depth，让输入读取阶段和递增计算阶段在 token 上保持拍级解耦。",
        ),
        (
            "stream_read_stream",
            "stream_read_stream 在这里暂存按扁平索引读出的块样本，作为 read_block 到 row_pass 的 FIFO 边界。",
            "read_block 读出的块样本会先在这条 read stream 里等待 row_pass 消费。",
            "为 stream_read_stream 指定显式 depth，让块读入阶段和 row_pass 在样本 token 上保持拍级解耦。",
        ),
        (
            "stream_row_stream",
            "stream_row_stream 在这里暂存第一段行向处理后的块样本，作为 row_pass 到 transpose_or_reorder 的 FIFO 边界。",
            "row_pass 处理后的块样本会先在这条 row stream 里等待重排阶段消费。",
            "为 stream_row_stream 指定显式 depth，让 row_pass 和重排阶段在块样本上保持拍级解耦。",
        ),
        (
            "stream_reorder_stream",
            "stream_reorder_stream 在这里暂存重排后的块样本，作为 transpose_or_reorder 到 col_pass 的 FIFO 边界。",
            "重排后的块样本会先在这条 reorder stream 里等待列向阶段消费。",
            "为 stream_reorder_stream 指定显式 depth，让块样本先在重排边界里完成行序到列序的转接，再交给 col_pass 按新的块顺序继续消费。",
        ),
        (
            "stream_col_stream",
            "stream_col_stream 在这里暂存列向处理后的块样本，作为 col_pass 到 write_block 的 FIFO 边界。",
            "列向处理后的块样本会先在这条 col stream 里等待写回阶段消费。",
            "为 stream_col_stream 指定显式 depth，让 col_pass 和 write_block 在块样本上保持拍级解耦。",
        ),
    )

# 为 hls::stream 声明生成带 load/result 角色区分的说明。
def stream_declaration_comment_text(str_code: str) -> str:
    """为 hls::stream 声明生成带角色区分的说明。

    参数:
        str_code: 当前 hls::stream 声明的净代码文本，dtype=str，unit=code text。

    返回:
        当前 stream 声明对应的中文边界说明，dtype=str，unit=comment text。
    """

    # 先匹配这轮 block/dataflow 失败簇里的通用 stream 名称，把声明绑定到真实阶段边界。
    for str_stream_name, str_comment_text, _, _ in generic_stream_comment_maps():

        # 一旦命中当前通用 stream 名称，就直接返回对应的摘要说明。
        if str_stream_name in str_code:

            # 把当前通用 stream 名称直接绑定到对应的阶段边界说明。
            return str_comment_text

    # 再逐条匹配其他已经有专属说明的 stream 名称，把声明绑定到真实阶段边界。
    for str_stream_name, str_comment_text in (
        (
            "stream_a_stream",
            "stream_a_stream 在这里暂存 A 路加载阶段推送的左操作数样本，作为输入搬运到 tile 计算之间的 FIFO 边界。",
        ),
        (
            "stream_in_stream",
            "stream_in_stream 在这里缓存封装好的 axis_byte_t 输入 token，让主存读样本和后续编码阶段按各自节奏推进。",
        ),
        (
            "stream_b_stream",
            "stream_b_stream 在这里缓存右侧配对列片段，让 compute 阶段按自己的节奏领取加数 token。",
        ),
        (
            "stream_out_stream",
            "stream_out_stream 在这里暂存 tile 逐 lane 求和结果，作为 compute_matmul_tile 到 store_matmul 的 FIFO 边界。",
        ),
        (
            "stream_load_stream",
            "stream_load_stream 在这里暂存从输入窗口搬运出的样本，作为 load 阶段到计算阶段的 FIFO 边界。",
        ),
        (
            "stream_result_stream",
            "stream_result_stream 在这里暂存递增后的样本，作为计算阶段到写回阶段的 FIFO 边界。",
        ),
        (
            "stream_task_stream",
            "stream_task_stream 在这里暂存从输入窗口读出的待处理样本，作为 task_graph 读入阶段到计算阶段的 FIFO 边界。",
        ),
        (
            "stream_task_result_stream",
            "stream_task_result_stream 在这里暂存已经递增的样本，作为 task_graph 计算阶段到写回阶段的 FIFO 边界。",
        ),
        (
            "stream_task_count_stream",
            "stream_task_count_stream 在这里暂存本轮事务长度 token，让顶层编排把长度边界单独传给下游 task_graph helper。",
        ),
        (
            "read_count_stream",
            "read_count_stream 在这里暂存本轮事务长度 token，让 read actor 先锁定 restart 边界再开始读输入流。",
        ),
        (
            "compute_count_stream",
            "compute_count_stream 在这里暂存 read actor 已确认的事务长度 token，专门交给 compute actor 决定后续循环边界。",
        ),
        (
            "write_count_stream",
            "write_count_stream 在这里暂存 compute actor 转交的事务长度 token，专门约束 write actor 要写出的结果个数。",
        ),
    ):

        # 一旦命中具体 stream 名称，就立刻把这条声明落到对应的阶段边界。
        if str_stream_name in str_code:

            # 把命中的 actor 专属 stream 声明翻译成对应阶段的 FIFO 边界说明。
            return str_comment_text

    # 其他 stream 声明回退到保守说明。
    return "当前 stream 在这里承担相邻 dataflow 阶段之间的 FIFO 边界。"

# 为 hls::stream 声明生成与摘要不同的 inline comment。
def stream_declaration_inline_comment_text(str_code: str) -> str:
    """为 hls::stream 声明生成与摘要不同的 inline comment。

    参数:
        str_code: 当前 hls::stream 声明的净代码文本，dtype=str，unit=code text。

    返回:
        当前 stream 声明对应的补充尾注；不需要时返回空字符串，dtype=str，unit=comment text。
    """

    # 先对这轮 block/dataflow 失败簇里的通用 stream 声明追加专属尾注。
    for str_stream_name, _, str_comment_text, _ in generic_stream_comment_maps():

        # 命中当前通用 stream 名称后，直接返回对应的第二观察面说明。
        if str_stream_name in str_code:

            # 返回当前通用 stream 声明的补充尾注。
            return str_comment_text

    # 再对其他已经有专属尾注的 stream 声明追加补充观察面。
    for str_stream_name, str_comment_text in (
        ("stream_a_stream", "A 路左操作数样本先在这个 FIFO 里和 tile 计算阶段解耦。"),
        ("stream_in_stream", "主存样本先在这个 FIFO 里变成 axis_byte_t token，再交给编码阶段。"),
        ("stream_b_stream", "右侧配对加数会先在这个 FIFO 里排队，等 tile 求和阶段逐项领取。"),
        ("stream_out_stream", "tile 求和结果先在这个 FIFO 里等待写回阶段消费。"),
        ("stream_load_stream", "load 阶段把待处理样本暂存在这个 FIFO。"),
        ("stream_result_stream", "result 阶段把待写回样本暂存在这个 FIFO。"),
        ("stream_task_stream", "这条 task stream 先缓冲读入阶段刚搬来的待处理样本。"),
        ("stream_task_result_stream", "这条 result stream 先缓冲已经递增完成的输出样本。"),
        ("stream_task_count_stream", "这条 count stream 单独传递本轮事务长度 token。"),
        ("read_count_stream", "read actor 先从这里领取本轮事务长度 token。"),
        ("compute_count_stream", "compute actor 先从这里领取本轮事务长度 token。"),
        ("write_count_stream", "write actor 先从这里领取本轮事务长度 token。"),
    ):

        # 命中需要尾注补充的 stream 声明后，直接返回对应说明。
        if str_stream_name in str_code:

            # 返回当前 stream 声明的补充尾注。
            return str_comment_text

    # 未命中专属尾注的 stream 声明就保持空串，避免把泛化句再塞回源码。
    return ""

# 为 stream/axis 写调用生成阶段语义说明。
def stream_write_comment_text(str_code: str) -> str:
    """为 stream/axis 写调用生成阶段语义说明。

    参数:
        str_code: 当前 stream 或 axis 写调用的净代码文本，dtype=str，unit=code text。

    返回:
        当前写调用对应的中文阶段说明，dtype=str，unit=comment text。
    """

    # 先匹配已经能稳定映射到具体阶段入口的写调用。
    for tuple_stage_needles, str_comment_text in (
        (
            ("stream_read_stream.write(", "ptr_input_values["),
            "把当前二维块的输入样本送入 stream_read_stream，交给 row_pass 作为后续行向处理的输入边界。",
        ),
        (
            ("stream_row_stream.write(", "stream_read_stream.read()"),
            "把 read_block 刚取出的块样本转交给 stream_row_stream，显式保留行向阶段的输出 FIFO 边界。",
        ),
        (
            ("stream_reorder_stream.write(", "stream_row_stream.read()"),
            "把 row_pass 产出的块样本重排后送入 stream_reorder_stream，交给列向阶段继续消费。",
        ),
        (
            ("stream_col_stream.write(", "uint_sample + 1"),
            "把列向阶段刚处理完的块样本送入 stream_col_stream，等待 write_block 顺序写回输出窗口。",
        ),
        (
            ("stream_mid_stream.write(", "stream_in_stream.read()"),
            "把输入流当前 token 原样转交给 stream_mid_stream，显式保留 axis 读入阶段和递增计算阶段的 FIFO 边界。",
        ),
        (
            ("stream_result_stream.write(", "uint_sample + 1"),
            "把当前中间样本完成递增后的结果送入 stream_result_stream，等待下游写出阶段继续消费。",
        ),
        (
            ("stream_out_stream.write(", "stream_result_stream.read()"),
            "把 stream_result_stream 中已经递增完成的 token 顺序送回 stream_out_stream，保持 axis 写出阶段的事务顺序可观测。",
        ),
        (
            ("stream_a_stream.write(", "ptr_input_a["),
            "把 input_a 当前索引的左操作数样本送入 stream_a_stream，给后续 tile 计算阶段准备 A 路 token。",
        ),
        (
            ("stream_in_stream.write(axis_in_pkt)",),
            "把刚封装好的 axis_byte_t 输入 token 送入 stream_in_stream，交给后续 AXIS 编码阶段逐拍消费。",
        ),
        (
            ("stream_b_stream.write(", "ptr_input_b["),
            "把 input_b 当前索引的右操作数样本送入 stream_b_stream，给后续 tile 计算阶段准备 B 路 token。",
        ),
        (
            ("stream_load_stream.write(", "ptr_input_values["),
            "把输入窗口当前索引的样本送入 load FIFO，给后续计算阶段做流式解耦。",
        ),
        (
            ("stream_load_stream.write(", "arr_input_values["),
            "把输入窗口当前索引的样本送入 load FIFO，给后续计算阶段做流式解耦。",
        ),
        (
            ("stream_result_stream.write(",),
            "把递增后的样本送入 result FIFO，等待写回阶段按索引落到输出窗口。",
        ),
        (
            ("stream_out_stream.write(", "arr_tile_a[", "arr_tile_b["),
            "把当前 tile 的 A/B 对应 lane 求和结果送入 stream_out_stream，等待 store helper 顺序写回输出窗口。",
        ),
        (
            ("stream_count_stream.write(",),
            "把这次事务长度 token 送入 stream_count_stream，让下游 compute helper 在 stream-only 约束下也能锁定有效循环边界。",
        ),
        (
            ("stream_task_stream.write(", "ptr_input_values["),
            "把 ptr_input_values 当前索引的样本送入 stream_task_stream，交给 task_graph 计算阶段继续消费。",
        ),
        (
            ("stream_task_stream.write(", "in_stream.read()"),
            "把当前输入 token 送入 stream_task_stream，让 task_graph 的 read actor 和 compute actor 在样本通道上解耦。",
        ),
        (
            ("stream_task_result_stream.write(",),
            "把递增后的样本送入 stream_task_result_stream，等待下游写回阶段按同一事务边界继续消费。",
        ),
        (
            ("compute_count_stream.write(",),
            "把 read actor 已经消费过的事务长度 token 转交给 compute_count_stream，供 compute actor 复用同一条边界。",
        ),
        (
            ("write_count_stream.write(",),
            "把 compute actor 用过的事务长度 token 转交给 write_count_stream，供 write actor 回放同样数量的输出结果。",
        ),
        (
            ("out_stream.write(", "stream_task_result_stream.read()"),
            "把 stream_task_result_stream 中已经递增完成的 token 送回 out_stream，保持 task_graph 的输出顺序与输入事务对齐。",
        ),
    ):

        # 命中具体写调用场景后，直接返回对应阶段说明。
        if all(str_stage_needle in str_code for str_stage_needle in tuple_stage_needles):

            # 返回当前写调用的专属说明。
            return str_comment_text

    # axis 输出流写回时，要说明 token 正在送回下游。
    if "stream_out_stream.write(" in str_code:

        # 返回 axis 输出 token 发送说明。
        return "把递增后的样本送回 axis 输出流，让下游按 token 观察当前事务结果。"

    # 其他写调用回退到保守说明。
    return "把当前阶段生成的样本继续送往下一条流式边界。"
