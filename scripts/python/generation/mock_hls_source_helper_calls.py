"""收拢 mock HLS source 里 helper 调度、task actor 和条件分支说明逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 正则匹配负责识别 dataflow helper 调用名模式。
import re

# 赋值聚合入口继续提供局部声明识别与声明说明逻辑。
from .mock_hls_source_assignments import (
    declaration_comment_text,
    is_local_declaration_statement,
)

# loop 子模块继续提供阶段循环说明入口。
from .mock_hls_source_loops import loop_comment_text

# stream 子模块继续提供 FIFO 声明和写回说明入口。
from .mock_hls_source_streams import (
    stream_declaration_comment_text,
    stream_write_comment_text,
)

# 为循环、声明、调用和条件分支统一路由到阶段动作说明。
def stage_comment_text(str_code: str, str_stage_code: str) -> str:
    """为循环、声明、调用和条件分支统一路由到阶段动作说明。

    参数:
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。
        str_stage_code: 当前代码下方首条代表阶段职责的代码文本，dtype=str，unit=code text。

    返回:
        命中阶段动作规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 外层 blocked tile 推进循环要优先返回最具体的阶段职责说明。
    if str_code.startswith("for (int base = 0; base < int_length; base += 4)"):

        # 直接返回 blocked tile 推进循环的专属说明，避免退回普通 `for` 规则。
        return "让 base 在 [0, int_length) 范围内按 4-lane tile 推进，每轮都从 A/B 输入 FIFO 拉取一个块，并把对应求和结果送往输出 FIFO。"

    # 普通 `for` 循环统一交给循环职责路由器判定 load、compute 或 writeback 角色。
    if str_code.startswith("for "):

        # 让循环说明生成器根据真实阶段代码挑出对应职责。
        return loop_comment_text(str_stage_code)

    # hls::stream 声明需要强调相邻阶段之间的 FIFO 边界。
    if str_code.startswith("hls::stream<"):

        # 返回当前 FIFO 声明的阶段边界说明。
        return stream_declaration_comment_text(str_code)

    # hls::task actor 声明需要说明它消费和产出的边界。
    if str_code.startswith("hls::task "):

        # 返回当前 task actor 的调度阶段说明。
        return task_actor_comment_text(str_code)

    # 不带初始化的局部声明仍要落回当前缓冲或 scratchpad 的真实角色。
    if is_local_declaration_statement(str_code):

        # 返回当前局部声明的职责说明。
        return declaration_comment_text(str_code)

    # stream/axis 写调用需要指出数据正从哪一侧继续流向哪一侧。
    if ".write(" in str_code:

        # 把当前写调用绑定到真实的数据流向说明。
        return stream_write_comment_text(str_code)

    # helper 调用需要显式落回顶层编排顺序。
    if is_helper_function_call(str_code):

        # 返回当前 helper 调用的编排说明。
        return helper_function_call_comment_text(str_code)

    # 条件分支需要说明它守护的是哪个尾块边界或写出动作。
    if str_code.startswith("if "):

        # 把 if 条件和下游阶段动作一起交给条件说明生成器。
        return if_condition_comment_text(str_code, str_stage_code)

    # 其他阶段动作在这里不追加说明。
    return ""

# 判断当前代码行是否是顶层 dataflow helper 的调用语句。
def is_helper_function_call(str_code: str) -> bool:
    """判断当前代码行是否是顶层 dataflow helper 的调用语句。

    参数:
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。

    返回:
        命中 dataflow helper 调用时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 识别当前 mock source 固定使用的 matmul/task_graph/block/FIR/dataflow helper 调用。
    return bool(
        re.match(
            (
                r"(?:load_matmul_a|load_matmul_b|compute_matmul_tile|store_matmul|"
                r"load_task_graph_[A-Za-z0-9_]+|store_task_graph_[A-Za-z0-9_]+|"
                r"seed_task_graph_[A-Za-z0-9_]+_counts|read_task_graph_[A-Za-z0-9_]+|"
                r"write_task_graph_[A-Za-z0-9_]+|read_fir_dataflow|compute_fir_dataflow|"
                r"write_fir_dataflow|read_block|row_pass|transpose_or_reorder|"
                r"col_pass|write_block|read_dataflow_axis_increment|"
                r"compute_dataflow_axis_increment|write_dataflow_axis_increment)\s*\("
            ),
            str_code,
        )
    )

# 为顶层 helper 调用生成阶段编排说明。
def helper_function_call_comment_text(str_code: str) -> str:
    """为顶层 helper 调用生成阶段编排说明。

    参数:
        str_code: 当前 helper 调用语句的净代码文本，dtype=str，unit=code text。

    返回:
        命中 helper 调用时返回阶段说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 按顶层 dataflow 编排顺序匹配当前 helper 调用的阶段说明。
    for str_prefix, str_comment_text in (
        ("load_matmul_a(", "先启动 A 路加载 helper，把 input_a 样本流式送进左操作数 FIFO。"),
        ("load_matmul_b(", "再启动 B 路加载 helper，把 input_b 样本流式送进右操作数 FIFO。"),
        ("compute_matmul_tile(", "随后启动 tile 计算 helper，从 A/B FIFO 取 token 做逐 lane 求和，并把结果送进输出 FIFO。"),
        ("store_matmul(", "最后启动写回 helper，把输出 FIFO 中的求和结果按原索引顺序写回 ptr_output_values。"),
        ("read_fir_dataflow(", "先启动 FIR 读入 helper，把 ptr_input_values 样本逐项送入 FIR 输入 FIFO。"),
        ("compute_fir_dataflow(", "随后启动 FIR 计算 helper，从输入 FIFO 取样并把递增结果送入 FIR 结果 FIFO。"),
        ("write_fir_dataflow(", "最后启动 FIR 写回 helper，把结果 FIFO 中的递增样本按原索引写回 ptr_output_values。"),
    ):

        # 只有调用前缀命中当前 helper 名称时，才返回对应的阶段编排说明。
        if str_code.startswith(str_prefix):

            # 命中调用前缀后，直接把阶段编排说明返回给调用方。
            return str_comment_text

    # task_graph 顶层编排要显式区分长度播种、样本读入和最终写回。
    for str_prefix, str_comment_text in (
        ("load_task_graph_", "先启动 memory 读入 helper，把 ptr_input_values 的样本送入 task stream，并同步这次事务长度。"),
        ("store_task_graph_", "最后启动写回 helper，把 result stream 中已经递增的样本按原索引顺序写回 ptr_output_values。"),
        ("seed_task_graph_", "先播种一次事务长度 token，让下游 task_graph actor 共享同一条 restart 边界。"),
        ("read_task_graph_", "随后启动读入 actor，按事务长度顺序消费输入 token，并把样本送入 task stream。"),
        ("write_task_graph_", "最后启动写出 actor，在同一条事务边界下把 result stream token 顺序送回输出流。"),
    ):

        # task_graph helper 调用只要命中稳定前缀，就返回当前阶段编排说明。
        if str_code.startswith(str_prefix):

            # 返回当前 task_graph helper 调用对应的顶层编排说明。
            return str_comment_text

    # block_transform 与 dataflow_axis 这类通用 helper 调用也要保留阶段编排语义。
    for str_prefix, str_comment_text in (
        ("read_block(", "先启动 read_block，把输入窗口当前二维块的样本按扁平索引送入 stream_read_stream。"),
        ("row_pass(", "再启动 row_pass，让 read_block 送来的块样本先完成第一段行向处理。"),
        ("transpose_or_reorder(", "随后启动 transpose_or_reorder，把行向阶段输出重组成列向阶段可继续消费的块顺序。"),
        ("col_pass(", "接着启动 col_pass，让重排后的样本完成第二段列向处理并送入 stream_col_stream。"),
        ("write_block(", "最后启动 write_block，把 stream_col_stream 中的块样本按扁平索引写回 ptr_output_values。"),
        ("read_dataflow_axis_increment(", "先启动 axis 读入 helper，把输入流 token 顺序转交给 stream_mid_stream。"),
        ("compute_dataflow_axis_increment(", "随后启动递增计算 helper，从 stream_mid_stream 逐项取样并把结果送入 stream_result_stream。"),
        ("write_dataflow_axis_increment(", "最后启动 axis 写出 helper，把 stream_result_stream 中的递增结果顺序送回 stream_out_stream。"),
    ):

        # 当前 helper 调用命中稳定前缀后，直接返回对应阶段编排说明。
        if str_code.startswith(str_prefix):

            # 把命中的通用 helper 调度顺序说明交回调用方。
            return str_comment_text

    # 其他调用不在这里强制改写。
    return ""

# 为 hls::task actor 声明生成阶段语义说明，避免 task_graph 行退回泛化的数据通路注释。
def task_actor_comment_text(str_code: str) -> str:
    """为 hls::task actor 声明生成阶段语义说明。

    参数:
        str_code: 当前 hls::task 声明的净代码文本，dtype=str，unit=code text。

    返回:
        当前 actor 声明对应的中文说明；未命中时返回空字符串，dtype=str，unit=comment text。
    """

    # 非 hls::task 声明不在这里解释。
    if not str_code.startswith("hls::task "):

        # 当前代码行不是 task actor 声明，交给其他分支处理。
        return ""

    # task_graph 的 compute actor 要显式说明它把 helper 绑定成独立调度阶段。
    if "compute_task_graph_" in str_code:

        # 返回 task_graph compute actor 的专属说明。
        return "compute_stage 在这里把 compute_task_graph_* 绑定成独立 task actor，让样本递增阶段和 load/store 阶段通过 FIFO 边界并行推进。"

    # task_graph 的 read actor 要说明输入 token 会先汇入 task stream。
    if "read_task_graph_" in str_code:

        # 当前 actor 是 read_task_graph_*，直接返回读入阶段的专属说明。
        return "read_stage 在这里实例化 task_graph 的读入 actor，让上游输入 token 和下游 compute actor 通过 task stream 解耦。"

    # task_graph 的 write actor 要说明它只负责消费 result stream 并向外发送。
    if "write_task_graph_" in str_code:

        # 当前 actor 是 write_task_graph_*，直接返回写出阶段的专属说明。
        return "write_stage 在这里实例化 task_graph 的写出 actor，让 result stream 的 token 在独立阶段里顺序送回输出边界。"

    # 其他 task actor 保守说明它仍然保持显式阶段边界。
    return "当前 hls::task actor 在这里把 helper 绑定成独立调度阶段，显式保留 task_graph 的通道所有权边界。"

# 为 hls::task actor 声明生成补充尾注，避免和上方摘要注释复用同一句模板。
def task_actor_inline_comment_text(str_code: str) -> str:
    """为 hls::task actor 声明生成补充尾注。

    参数:
        str_code: 当前 hls::task 声明的净代码文本，dtype=str，unit=code text。

    返回:
        当前 actor 声明对应的尾注说明；未命中时返回空字符串，dtype=str，unit=comment text。
    """

    # 非 hls::task 声明不在这里追加尾注。
    if not str_code.startswith("hls::task "):

        # 当前代码行不是 task actor 声明，跳过尾注改写。
        return ""

    # compute actor 的尾注强调它消费哪两条输入边界，并把结果送往哪条输出边界。
    if "compute_task_graph_" in str_code:

        # 返回 task_graph compute actor 的补充尾注。
        return "这个 actor 只消费 task stream 与 count stream，再把递增后的样本送进 result stream。"

    # read actor 的尾注强调输入 token 与下游 task stream 的连接关系。
    if "read_task_graph_" in str_code:

        # 当前尾注命中 read_task_graph_* actor，直接返回输入到 task stream 的连接说明。
        return "这个 actor 先读外部输入 token，再把样本逐拍写进下游 task stream。"

    # write actor 的尾注强调 result stream 与最终输出边界的连接关系。
    if "write_task_graph_" in str_code:

        # 当前尾注命中 write_task_graph_* actor，直接返回写出边界说明。
        return "这个 actor 在 write_count_stream 限定下把 result stream token 顺序送回输出边界。"

    # 其他 actor 不在这里强制追加尾注。
    return ""

# 为关键 if 条件生成尾块边界语义说明。
def if_condition_comment_text(str_condition_code: str, str_stage_code: str) -> str:
    """为关键 if 条件生成尾块边界语义说明。

    参数:
        str_condition_code: 当前 if 条件对应的净代码文本，dtype=str，unit=condition text。
        str_stage_code: 当前 if 下方直接绑定的阶段代码文本，dtype=str，unit=code text。

    返回:
        命中专属条件规则时返回具体说明，否则返回保守边界说明，dtype=str，unit=comment text。
    """

    # streamofblocks 的尾块写出判断要明确只有有效 block 槽位会把递增结果送回输出流。
    if (
        str_condition_code.startswith("if (j < int_chunk)")
        and "stream_out_stream.write(arr_block_buf[j] + 1)" in str_stage_code
    ):

        # 返回 streamofblocks block 写出判断说明。
        return "只有当前 block 槽位仍落在有效样本范围内时，才把递增后的本地样本送回 axis 输出流。"

    # matmul dataflow 的尾块写出判断要明确只让有效 lane 继续送出结果。
    if str_condition_code.startswith("if (j < int_chunk)") and "stream_out_stream.write(" in str_stage_code:

        # 返回尾块写出判断说明。
        return "只让当前 tile 里仍在有效长度内的 lane 把 A/B 求和结果送入 stream_out_stream，尾块外 lane 直接跳过写出。"

    # reshape 块写回阶段的有效性判断要强调这里只落盘真实存在的缩放结果。
    if (
        str_condition_code.startswith("if (j < int_chunk)")
        and "ptr_output_values[base + j] = arr_wide_buf[j] * uint_scale_factor" in str_stage_code
    ):

        # 交回 reshape 缩放结果只在有效 lane 上写回的条件说明。
        return "只有当前 lane 仍落在 reshape 块的有效样本范围内时，才把乘上缩放因子的结果写回输出窗口。"

    # partition 块写回阶段的有效性判断要强调这里只写回当前 partition 仍真实存在的槽位。
    if (
        str_condition_code.startswith("if (j < int_chunk)")
        and "ptr_output_values[base + j] = arr_local_buf[j] * uint_scale_factor" in str_stage_code
    ):

        # 把 partition 尾块只写回真实槽位的条件语义单独交回，避免混同 reshape 那组尾块说明。
        return "只有当前 partition 槽位仍落在有效样本范围内时，才把本地样本乘上缩放因子后写回输出窗口。"

    # reshape 块载入阶段的有效性判断要强调这里只锁住真实存在的输入样本。
    if str_condition_code.startswith("if (j < int_chunk)") and "arr_wide_buf[" in str_stage_code:

        # 交回 reshape 输入样本只在有效 lane 上装入本地缓冲的条件说明。
        return "只有当前 lane 仍落在 reshape 块的有效样本范围内时，才把输入窗口样本锁进 arr_wide_buf。"

    # partition 尾块载入分支要点明最后一块只搬运真实命中的窗口样本，不沿用 reshape 的说法。
    if str_condition_code.startswith("if (j < int_chunk)") and "arr_local_buf[" in str_stage_code:

        # 把 partition 局部缓存只接住有效槽位样本的守卫语义交回给调用方。
        return "只有当前 partition 槽位仍落在有效样本范围内时，才把输入窗口样本锁进 arr_local_buf。"

    # lane-add 写回阶段的有效性判断要强调这里只写回当前子块仍有效的 lane。
    if (
        str_condition_code.startswith("if (j < int_chunk)")
        and "ptr_output_values[base + j] = arr_lane_buf_a[j] + arr_lane_buf_b[j]" in str_stage_code
    ):

        # 把 lane-add 子块只在有效 lane 上落盘求和结果的条件语义交回调用方。
        return "只有当前 lane 仍落在 lane-add 子块的有效范围内时，才把 A/B 局部样本的和写回输出窗口。"

    # 其他条件分支回退到保守的事务边界说明。
    return "只有当前样本仍处于有效事务边界内时，才继续执行下方的数据通路动作。"
