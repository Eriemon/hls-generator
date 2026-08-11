"""收拢 mock HLS source 中与 pragma 解释和 pipeline 语义相关的注释重写逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 正则负责解析 pragma 文本中的 bundle、depth、II 和接口模式字段。
import re

# flow 模块提供通用 stream 角色映射，供 STREAM pragma 解释复用。
from .mock_hls_source_flow import generic_stream_comment_maps

# 为各类 pragma 统一路由到对应的协议、吞吐或分块说明。
def pragma_comment_text(str_code: str, str_stage_code: str) -> str:
    """为各类 pragma 统一路由到对应的协议、吞吐或分块说明。

    参数:
        str_code: 当前 pragma 的净代码文本，dtype=str，unit=pragma text。
        str_stage_code: 当前 pragma 后方首条代表阶段职责的代码文本，dtype=str，unit=code text。

    返回:
        命中 pragma 规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # INTERFACE pragma 需要直接落回端口协议和 bundle 语义。
    if str_code.startswith("#pragma HLS INTERFACE"):

        # 把接口 pragma 绑定到端口级 contract 的说明正文。
        return interface_pragma_comment_text(str_code)

    # PIPELINE pragma 需要把 II 目标和真实阶段动作组合成一句说明。
    if str_code.startswith("#pragma HLS PIPELINE"):

        # 把当前阶段代码一起交给 PIPELINE 说明生成器。
        return pipeline_pragma_comment_text(str_code, str_stage_code)

    # STREAM pragma 需要强调 FIFO depth 和阶段解耦边界。
    if str_code.startswith("#pragma HLS STREAM"):

        # 让 STREAM 说明落回当前 FIFO 的深度职责。
        return stream_pragma_comment_text(str_code)

    # ARRAY_PARTITION pragma 需要点明当前局部缓冲的并行访问方式。
    if str_code.startswith("#pragma HLS ARRAY_PARTITION"):

        # 把 buffer banking 说明绑定到当前 tile 缓冲。
        return array_partition_pragma_comment_text(str_code)

    # UNROLL pragma 需要结合紧随其后的阶段动作说明并行 lane。
    if str_code.startswith("#pragma HLS UNROLL"):

        # 把当前阶段动作交给 UNROLL 说明生成器。
        return unroll_pragma_comment_text(str_stage_code)

    # 其他 pragma 在这里不追加说明。
    return ""

# 为 INTERFACE pragma 生成端口级别的具体中文说明，避免重复模板句。
def interface_pragma_comment_text(str_pragma_code: str) -> str:
    """为单条 INTERFACE pragma 生成端口级说明。

    参数:
        str_pragma_code: 当前 INTERFACE pragma 的净代码文本，dtype=str，unit=pragma text。

    返回:
        当前端口对应的中文语义说明，dtype=str，unit=comment text。
    """

    # 先取回端口、模式和 bundle 的最终文本三元组。
    tuple_field_values = interface_pragma_fields(str_pragma_code)  # 当前 INTERFACE pragma 的核心字段三元组

    # 后面的角色分支先看端口名字，所以这里先取第一个槽位判断它是不是输入口、输出口或 return 控制口。
    str_port_name = tuple_field_values[0]  # 输入口/输出口/return 分支判定用的端口名

    # 接口模式会直接写进返回文案，没有它就无法把 m_axi 和 s_axilite 的职责说清楚。
    str_mode_name = tuple_field_values[1]  # 写入合同句里的接口协议名

    # bundle 归属只会在最终说明句出现一次，这里单独取第三个槽位保持总线分组可见。
    str_bundle_name = tuple_field_values[2]  # 合同句需要点明的总线分组名

    # 输入指针端口要强调读取窗口与原始样本来源。
    if str_port_name == "ptr_input_a":

        # 说明当前接口承担 A 输入片段的外部读窗口职责。
        return f"{str_port_name} 作为 {str_mode_name} 端口绑定到 bundle={str_bundle_name} 的输入接口，让内核沿 A 路窗口逐块取回左操作数行片段。"

    # B 路输入端口要显式区别于 A 路输入窗口。
    if str_port_name == "ptr_input_b":

        # 这里要突出 B 路口承接的是右操作数配对窗口，而不是复述上一条 A 路说明。
        return f"{str_port_name} 作为 {str_mode_name} 端口绑定到 bundle={str_bundle_name} 的输入接口，让内核沿 B 路配对窗口逐块取回右操作数片段。"

    # 其余输入指针统一回退成“外部样本读窗口”视角，避免误写成某个特定矩阵通道。
    if str_port_name.startswith("ptr_input"):

        # 说明当前接口承担输入样本读窗口职责。
        return f"{str_port_name} 作为 {str_mode_name} 端口绑定到 bundle={str_bundle_name} 的输入接口，让内核按索引取得待缩放的原始样本。"

    # 输出指针端口要强调写回窗口与结果观测边界。
    if str_port_name.startswith("ptr_output"):

        # 说明当前接口承担输出结果写回窗口职责。
        return f"{str_port_name} 作为 {str_mode_name} 端口绑定到 bundle={str_bundle_name} 的输出接口，让 testbench 观察缩放结果是否写回目标窗口。"

    # AXIS 输入流端口要强调 token 从上游逐拍进入当前 kernel。
    if str_port_name.startswith("stream_in"):

        # 说明当前 axis 输入流端口负责把样本 token 送入首级计算路径。
        return f"{str_port_name} 作为 {str_mode_name} 输入流端口，把上游样本逐 token 送入 kernel 的首级计算路径。"

    # AXIS 输出流端口要强调 token 从 kernel 逐拍送回下游。
    if str_port_name.startswith("stream_out"):

        # 说明当前 axis 输出流端口负责把结果 token 送回下游观测边界。
        return f"{str_port_name} 作为 {str_mode_name} 输出流端口，把本轮递增后的样本逐 token 送回下游观测边界。"

    # scale 控制口要强调 host 可写的运行时乘法因子。
    if "scale" in str_port_name:

        # 说明当前控制口负责暴露运行时缩放因子。
        return f"{str_port_name} 作为 {str_mode_name} 控制端口暴露运行时缩放因子，方便 host 在启动前写入乘法系数。"

    # rows 控制口要明确它限定的是二维块的行向边界。
    if str_port_name == "int_rows":

        # 说明当前控制口负责给二维块 helper 链路提供统一的行数边界。
        return f"{str_port_name} 作为 {str_mode_name} 控制端口提供当前二维块的行数边界，让 read/row/reorder/col/write 五个 stage 共享同一组行向事务范围。"

    # cols 控制口要明确它限定的是二维块的列宽边界。
    if str_port_name == "int_cols":

        # 把列宽控制口约束重排与写回共享块宽边界的语义交回调用方。
        return f"{str_port_name} 作为 {str_mode_name} 控制端口提供当前二维块的列宽边界，让扁平读取、块重排和最终写回沿同一块宽度推进。"

    # length 控制口要强调事务边界与有效区间长度。
    if "length" in str_port_name:

        # 说明当前控制口负责限制有效事务长度。
        return f"{str_port_name} 作为 {str_mode_name} 控制端口限制有效事务长度，避免内核访问超出 smoke 样本边界的索引。"

    # 返回控制口固定解释成 ap_ctrl 握手边界。
    if str_port_name == "return":

        # 说明当前控制口通过 s_axilite 暴露 ap_ctrl_hs 生命周期状态。
        return "return 控制口通过 s_axilite 控制寄存器保留 ap_ctrl_hs 启停握手，让 host 观察 start、done 与 idle 生命周期状态。"

    # 其他端口保守回退到协议、bundle 和端口名三元组说明。
    return f"{str_port_name} 作为 {str_mode_name} 端口绑定到 bundle={str_bundle_name} 的接口协议，保持当前 top function 的端口边界可读。"

# 从 INTERFACE pragma 文本里提取端口名、接口模式和 bundle 名称三元组。
def interface_pragma_fields(str_pragma_code: str) -> tuple[str, str, str]:
    """从 INTERFACE pragma 文本里提取端口名、接口模式和 bundle 名称。

    参数:
        str_pragma_code: 当前 INTERFACE pragma 的净代码文本，dtype=str，unit=pragma text。

    返回:
        端口名、接口模式和 bundle 名称组成的三元组，dtype=tuple[str, str, str]，unit=pragma fields。
    """

    # 第一条正则只盯 `port=` 后面的标识符，给端口角色分支提供名字来源。
    obj_port_match = re.search(r"\bport=([A-Za-z_]\w*)", str_pragma_code)  # `port=` 后标识符的匹配对象

    # 第二条正则专门解析 `mode=`，让说明句知道该写成存储接口、流接口还是控制接口。
    obj_mode_match = re.search(r"\bmode=([A-Za-z_]\w*)", str_pragma_code)  # `mode=` 后协议名的匹配对象

    # 第三条正则抽取 bundle 分组，因为合同句需要点名 host 看到的是哪条总线。
    obj_bundle_match = re.search(r"\bbundle=([A-Za-z_]\w*)", str_pragma_code)  # `bundle=` 后分组名的匹配对象

    # 汇总当前 INTERFACE pragma 的三个最终文本字段。
    return (
        obj_port_match.group(1) if obj_port_match else "unnamed_port",
        obj_mode_match.group(1) if obj_mode_match else _inline_interface_mode_name(str_pragma_code),
        obj_bundle_match.group(1) if obj_bundle_match else "default_bundle",
    )

# 从 `#pragma HLS INTERFACE ...` 文本里提取紧随 INTERFACE 之后的内联模式名称。
def _inline_interface_mode_name(str_pragma_code: str) -> str:
    """从 INTERFACE pragma 文本里提取内联模式名。

    参数:
        str_pragma_code: 当前 INTERFACE pragma 的净代码文本，dtype=str，unit=pragma text。

    返回:
        提取到的模式名；缺失时回退为 `interface`，dtype=str，unit=mode name。
    """

    # 读取紧随 `INTERFACE` 关键字之后的首个 token，兼容 `INTERFACE m_axi port=...` 写法。
    obj_inline_match = re.search(r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z_]\w*)", str_pragma_code)  # INTERFACE 后首个 token 的模式匹配结果

    # 命中时返回真实接口模式，否则回退成保守占位值。
    return obj_inline_match.group(1) if obj_inline_match else "interface"

# 按 matmul 特征匹配 PIPELINE pragma 的吞吐说明。
def pipeline_matmul_comment_text(str_stage_code: str, str_ii_value: str) -> str:
    """按 matmul 特征匹配 PIPELINE pragma 的吞吐说明。

    参数:
        str_stage_code: 当前 PIPELINE pragma 后方首条代表阶段职责的代码文本，dtype=str，unit=code text。
        str_ii_value: 当前 PIPELINE pragma 解析出的 II 文本，dtype=str，unit=ii value。

    返回:
        命中 matmul 阶段时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐条匹配 matmul 的稳定阶段特征，把 II 说明绑定到 A 路、B 路和写回阶段。
    for tuple_stage_needles, str_comment_text in (
        (
            ("stream_a_stream.write(", "ptr_input_a["),
            f"PIPELINE pragma 让 A 路加载循环保持 II={str_ii_value}，按拍把 input_a 样本推入 stream_a_stream。",
        ),
        (
            ("stream_b_stream.write(", "ptr_input_b["),
            f"PIPELINE pragma 让右操作数预取循环保持 II={str_ii_value}，稳定把配对列片段连续灌入 stream_b_stream。",
        ),
        (
            ("stream_out_stream.read()", "ptr_output_values["),
            f"PIPELINE pragma 让输出写回循环保持 II={str_ii_value}，按拍把 stream_out_stream 的求和结果落到 ptr_output_values。",
        ),
        (
            ("axis_out_pkt = stream_out_stream.read()",),
            (
                f"PIPELINE pragma 让拆包写回循环保持 II={str_ii_value}，"
                "按拍消费 axis_word_t token 并把 data 域落回 ptr_output_values。"
            ),
        ),
    ):

        # 只有当前阶段代码同时满足这组特征片段时，才采用对应的 matmul 吞吐说明。
        if all(str_stage_needle in str_stage_code for str_stage_needle in tuple_stage_needles):

            # 这组特征片段已经把 pragma 锁定到 matmul 的固定搬运链路上，下面直接交付对应 II 说明。
            return str_comment_text

    # 当前阶段不属于 matmul 特征路径时回退为空字符串。
    return ""

# 按通用 stream-flow 特征匹配 PIPELINE pragma 的吞吐说明。
def pipeline_stream_flow_comment_text(str_stage_code: str, str_ii_value: str) -> str:
    """按通用 stream-flow 特征匹配 PIPELINE pragma 的吞吐说明。

    参数:
        str_stage_code: 当前 PIPELINE pragma 后方首条代表阶段职责的代码文本，dtype=str，unit=code text。
        str_ii_value: 当前 PIPELINE pragma 解析出的 II 文本，dtype=str，unit=ii value。

    返回:
        命中通用 stream-flow 阶段时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 依次尝试输入侧搬运路径和输出侧写回路径，命中首个结果后立即返回。
    for str_comment_text in (
        pipeline_stream_input_comment_text(str_stage_code, str_ii_value),
        pipeline_stream_output_comment_text(str_stage_code, str_ii_value),
    ):

        # 当前阶段一旦已经命中某一类通用 stream-flow 路径，就不再继续回退。
        if str_comment_text:

            # 返回首个命中的通用 stream-flow 吞吐说明。
            return str_comment_text

    # 当前阶段不属于通用 stream-flow 路径时回退为空字符串。
    return ""

# 为通用 stream-flow 的输入侧路径生成 PIPELINE pragma 吞吐说明。
def pipeline_stream_input_comment_text(str_stage_code: str, str_ii_value: str) -> str:
    """为通用 stream-flow 的输入侧路径生成 PIPELINE pragma 吞吐说明。

    参数:
        str_stage_code: 当前 PIPELINE pragma 后方首条代表阶段职责的代码文本，dtype=str，unit=code text。
        str_ii_value: 当前 PIPELINE pragma 解析出的 II 文本，dtype=str，unit=ii value。

    返回:
        命中输入侧路径时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 主存封包循环要单独说明它会把主存样本按拍组装成 axis_byte_t token。
    if str_stage_code.startswith("axis_byte_t axis_in_pkt;"):

        # 当前阶段命中主存封包路径时，直接返回封包吞吐说明。
        return (
            f"PIPELINE pragma 让主存封包循环保持 II={str_ii_value}，"
            "按拍把 ptr_input_values 的样本组装成 axis_byte_t token。"
        )

    # block transform 的 read_block 要说明它逐拍把二维块样本送进 read FIFO。
    if "stream_read_stream.write(ptr_input_values[" in str_stage_code:

        # 交回 read_block 逐拍把二维块样本送入 read FIFO 的吞吐说明。
        return (
            f"PIPELINE pragma 让 read_block 保持 II={str_ii_value}，"
            "按扁平索引逐拍把当前二维块样本送入 stream_read_stream。"
        )

    # axis 读入 helper 要明确这里只做 token 转发，不在此阶段计算。
    if "stream_mid_stream.write(stream_in_stream.read())" in str_stage_code:

        # 交回 axis 输入 token 原样转交给中间 FIFO 的吞吐说明。
        return (
            f"PIPELINE pragma 让 axis 读入 helper 保持 II={str_ii_value}，"
            "按拍把输入 token 原样转交给 stream_mid_stream。"
        )

    # row_pass 需要说明它逐拍从 read stream 取样并推进到下一条 FIFO。
    if "stream_row_stream.write(stream_read_stream.read())" in str_stage_code:

        # 交回 row_pass 逐拍消费 read FIFO 并推进到下一条 FIFO 的吞吐说明。
        return (
            f"PIPELINE pragma 让 row_pass 保持 II={str_ii_value}，"
            "按拍消费 stream_read_stream 并把块样本送入 stream_row_stream。"
        )

    # 块重排阶段需要显式保留 row FIFO 到 reorder FIFO 的逐拍转发关系。
    if "stream_reorder_stream.write(stream_row_stream.read())" in str_stage_code:

        # 交回块重排阶段逐拍把 row FIFO 结果送进 reorder FIFO 的吞吐说明。
        return (
            f"PIPELINE pragma 让 transpose_or_reorder 保持 II={str_ii_value}，"
            "按拍消费 stream_row_stream 并把重排后的块样本送入 stream_reorder_stream。"
        )

    # axis 计算 helper 要说明它逐拍完成递增并把结果送进 result FIFO。
    if "stream_result_stream.write(uint_sample + 1)" in str_stage_code:

        # 交回递增计算 helper 逐拍产出 result FIFO token 的吞吐说明。
        return (
            f"PIPELINE pragma 让递增计算 helper 保持 II={str_ii_value}，"
            "按拍消费中间样本并把加一后的结果送入 stream_result_stream。"
        )

    # block transform 的列向阶段要明确它在本地取样后逐拍把结果送往 col FIFO。
    if "stream_col_stream.write(uint_sample + 1)" in str_stage_code:

        # 交回列向阶段逐拍把处理结果送往 col FIFO 的吞吐说明。
        return (
            f"PIPELINE pragma 让 col_pass 保持 II={str_ii_value}，"
            "按拍把列向处理完成的样本送入 stream_col_stream。"
        )

    # 输入窗口到 load FIFO 的搬运阶段要说明它按拍把原始样本推入下游 FIFO。
    if "stream_load_stream.write(" in str_stage_code and (
        "ptr_input_values[" in str_stage_code or "arr_input_values[" in str_stage_code
    ):

        # 当前阶段命中输入搬运路径时，直接返回搬运吞吐说明。
        return f"PIPELINE pragma 让输入搬运循环保持 II={str_ii_value}，按拍把输入窗口样本送入 load FIFO。"

    # load FIFO 到 result FIFO 的中间计算阶段要说明它按拍完成递增处理。
    if "stream_load_stream.read()" in str_stage_code or "stream_result_stream.write(" in str_stage_code:

        # 当前阶段命中中间计算路径时，直接返回 load/result FIFO 吞吐说明。
        return (
            f"PIPELINE pragma 让中间计算循环保持 II={str_ii_value}，"
            "按拍消费 load FIFO 并产出 result FIFO token。"
        )

    # 当前阶段不属于输入侧通用 stream-flow 路径时回退为空字符串。
    return ""

# 为通用 stream-flow 的输出侧路径生成 PIPELINE pragma 吞吐说明。
def pipeline_stream_output_comment_text(str_stage_code: str, str_ii_value: str) -> str:
    """为通用 stream-flow 的输出侧路径生成 PIPELINE pragma 吞吐说明。

    参数:
        str_stage_code: 当前 PIPELINE pragma 后方首条代表阶段职责的代码文本，dtype=str，unit=code text。
        str_ii_value: 当前 PIPELINE pragma 解析出的 II 文本，dtype=str，unit=ii value。

    返回:
        命中输出侧路径时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # result FIFO 到输出窗口的写回阶段要说明它按拍把结果落回外部窗口。
    if "stream_result_stream.read()" in str_stage_code and (
        "ptr_output_values[" in str_stage_code or "arr_output_values[" in str_stage_code
    ):

        # 当前阶段命中结果写回路径时，直接返回写回吞吐说明。
        return f"PIPELINE pragma 让写回循环保持 II={str_ii_value}，按拍把 result FIFO token 落到输出窗口。"

    # block transform 的写回 helper 要明确这里逐拍把列向结果落回扁平输出窗口。
    if "ptr_output_values[i] = stream_col_stream.read()" in str_stage_code:

        # 交回 write_block 逐拍把列向结果写回扁平输出窗口的吞吐说明。
        return (
            f"PIPELINE pragma 让 write_block 保持 II={str_ii_value}，"
            "按拍把 stream_col_stream 中的块结果写回扁平输出窗口。"
        )

    # axis 写出 helper 要显式保留 result FIFO 到输出流的逐拍发送边界。
    if "stream_out_stream.write(stream_result_stream.read())" in str_stage_code:

        # 交回 axis 写出 helper 逐拍把 result FIFO 结果送回输出流的吞吐说明。
        return (
            f"PIPELINE pragma 让 axis 写出 helper 保持 II={str_ii_value}，"
            "按拍把 stream_result_stream 中的递增结果送回输出流。"
        )

    # 最小 axis 样本路径要说明它逐拍消费输入 token 并送出输出 token。
    if "stream_in_stream.read()" in str_stage_code or "stream_out_stream.write(" in str_stage_code:

        # 当前阶段命中最小 axis 样本路径时，直接返回流式事务吞吐说明。
        return (
            f"PIPELINE pragma 让 axis 样本路径保持 II={str_ii_value}，"
            "按拍消费输入 token 并送出递增后的输出 token。"
        )

    # 当前阶段不属于输出侧通用 stream-flow 路径时回退为空字符串。
    return ""

# 专门为 task_graph 的 read/compute/write actor 解释 PIPELINE pragma 的逐拍调度语义。
def pipeline_task_graph_comment_text(str_stage_code: str, str_ii_value: str) -> str:
    """按 task_graph 特征匹配 PIPELINE pragma 的吞吐说明。

    参数:
        str_stage_code: 当前 PIPELINE pragma 后方首条代表阶段职责的代码文本，dtype=str，unit=code text。
        str_ii_value: 当前 PIPELINE pragma 解析出的 II 文本，dtype=str，unit=ii value。

    返回:
        命中 task_graph 阶段时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # task_graph 的 memory 读入阶段要说明它按拍把主存样本推入 task stream。
    if "stream_task_stream.write(" in str_stage_code and "ptr_input_values[" in str_stage_code:

        # 当前阶段命中 task_graph memory 读入路径时，直接返回对应吞吐说明。
        return (
            f"PIPELINE pragma 让 task_graph 读入循环保持 II={str_ii_value}，"
            "按拍把 ptr_input_values 的样本送入 stream_task_stream。"
        )

    # task_graph 的 AXIS 读入阶段要说明它按拍把输入 token 转发到 task stream。
    if "stream_task_stream.write(" in str_stage_code and "in_stream.read()" in str_stage_code:

        # 当前阶段命中 task_graph 的 AXIS 读入 actor 时，直接返回输入转发链路的吞吐说明。
        return (
            f"PIPELINE pragma 让 task_graph 读入 actor 保持 II={str_ii_value}，"
            "按拍消费 in_stream token 并写入 stream_task_stream。"
        )

    # task_graph 的计算阶段要说明它按拍消费 task stream 并产生 result stream token。
    if "stream_task_stream.read()" in str_stage_code or "stream_task_result_stream.write(" in str_stage_code:

        # 当前阶段命中 task_graph 计算路径时，直接返回结果生成吞吐说明。
        return (
            f"PIPELINE pragma 让 task_graph 计算阶段保持 II={str_ii_value}，"
            "按拍消费 stream_task_stream 并把递增结果送入 stream_task_result_stream。"
        )

    # task_graph 的 memory 写回阶段要说明它按拍把 result stream 样本落回输出窗口。
    if "stream_task_result_stream.read()" in str_stage_code and "ptr_output_values[" in str_stage_code:

        # 当前阶段命中 task_graph memory 写回路径时，直接返回写回吞吐说明。
        return (
            f"PIPELINE pragma 让 task_graph 写回循环保持 II={str_ii_value}，"
            "按拍把 stream_task_result_stream 的样本落回 ptr_output_values。"
        )

    # task_graph 的 AXIS 写出阶段要说明它按拍把 result stream token 送回 out_stream。
    if "out_stream.write(" in str_stage_code and "stream_task_result_stream.read()" in str_stage_code:

        # 当前阶段命中 task_graph AXIS 写出路径时，直接返回写出吞吐说明。
        return (
            f"PIPELINE pragma 让 task_graph 写出 actor 保持 II={str_ii_value}，"
            "按拍把 stream_task_result_stream 的 token 送回 out_stream。"
        )

    # 当前阶段不属于 task_graph 路径时回退为空字符串。
    return ""

# 把 PIPELINE pragma 的注释收口到吞吐节拍本身，并在需要时补充当前循环阶段职责。
def pipeline_pragma_comment_text(str_pragma_code: str, str_stage_code: str) -> str:
    """为单条 PIPELINE pragma 生成吞吐目标说明。

    参数:
        str_pragma_code: 当前 PIPELINE pragma 的净代码文本，dtype=str，unit=pragma text。
        str_stage_code: 当前 PIPELINE pragma 后方首条代表阶段职责的代码文本，dtype=str，unit=code text。

    返回:
        包含 II 目标的中文说明，dtype=str，unit=comment text。
    """

    # 先解析 pragma 中是否显式声明了 `II=`，后续所有吞吐说明都要复用这个文本。
    obj_ii_match = re.search(r"\bII=(\d+)", str_pragma_code)  # `II=` 数值片段的匹配对象

    # 再把匹配对象转换成说明文本，没命中时统一回退成“未显式声明”。
    str_ii_value = obj_ii_match.group(1) if obj_ii_match else "未显式声明"  # 供三类吞吐说明复用的 II 文本

    # 依次尝试 matmul、通用 stream-flow 和 task_graph 三类吞吐说明。
    for str_comment_text in (
        pipeline_matmul_comment_text(str_stage_code, str_ii_value),
        pipeline_stream_flow_comment_text(str_stage_code, str_ii_value),
        pipeline_task_graph_comment_text(str_stage_code, str_ii_value),
    ):

        # 当前阶段一旦命中某一类吞吐说明，就不再回退到更泛化的默认文案。
        if str_comment_text:

            # 返回首个命中的 PIPELINE 吞吐说明。
            return str_comment_text

    # 其余阶段统一回退到保守的逐拍推进说明。
    return f"PIPELINE pragma 把当前主事务约束在 II={str_ii_value}，让向量缩放路径保持逐拍推进的吞吐节奏。"

# 读取 STREAM pragma 时，把 FIFO depth 和“谁与谁解耦”这件事绑定成一句硬件说明。
def stream_pragma_comment_text(str_pragma_code: str) -> str:
    """为单条 HLS STREAM pragma 生成带角色区分的说明。

    参数:
        str_pragma_code: 当前 STREAM pragma 的净代码文本，dtype=str，unit=pragma text。

    返回:
        当前 FIFO 深度约束对应的中文说明，dtype=str，unit=comment text。
    """

    # 先匹配这轮 block/dataflow 失败簇里的通用 stream 名称，把 depth 约束绑定到真实阶段边界。
    for str_stream_name, _, _, str_comment_text in generic_stream_comment_maps():

        # 命中通用 stream 名字时立刻把 FIFO 深度约束落到对应 stage 边界。
        if f"variable={str_stream_name}" in str_pragma_code:

            # 把当前通用 stream 的 depth 约束绑定到对应阶段边界。
            return str_comment_text

    # 再逐条匹配其他 FIFO 变量名，把深度约束绑定到具体阶段边界。
    for str_variable_needle, str_comment_text in (
        (
            "variable=stream_in_stream",
            "为 stream_in_stream 指定显式 depth，让主存封包阶段和 AXIS 编码阶段保持拍级解耦。",
        ),
        (
            "variable=stream_a_stream",
            "为 stream_a_stream 指定显式 depth，让 A 路加载阶段和 tile 计算阶段保持拍级解耦。",
        ),
        (
            "variable=stream_b_stream",
            "为 stream_b_stream 指定显式 depth，让右侧配对列的预取节奏和 tile 求和阶段彼此独立。",
        ),
        (
            "variable=stream_out_stream",
            "为 stream_out_stream 指定显式 depth，让 tile 求和阶段和输出写回阶段保持拍级解耦。",
        ),
        (
            "variable=stream_load_stream",
            "为 load FIFO 指定显式 depth，让输入搬运阶段和中间计算阶段保持拍级解耦。",
        ),
        (
            "variable=stream_result_stream",
            "为 result FIFO 指定显式 depth，让中间计算阶段和输出写回阶段保持拍级解耦。",
        ),
        (
            "variable=stream_task_stream",
            "为 stream_task_stream 指定显式 depth，让 memory/输入读入阶段和 task_graph 计算阶段在样本 token 上保持拍级解耦。",
        ),
        (
            "variable=stream_task_result_stream",
            "为 stream_task_result_stream 指定显式 depth，让 task_graph 计算阶段和最终写回阶段在结果 token 上保持拍级解耦。",
        ),
        (
            "variable=stream_task_count_stream",
            "为 stream_task_count_stream 指定显式 depth，让事务长度 token 在顶层编排和下游 task_graph helper 之间独立传递。",
        ),
        (
            "variable=read_count_stream",
            "为 read_count_stream 指定显式 depth，让 restart 边界先于输入 token 被 read actor 稳定锁定。",
        ),
        (
            "variable=compute_count_stream",
            "为 compute_count_stream 指定显式 depth，让 read actor 用过的事务长度 token 能独立传给 compute actor。",
        ),
        (
            "variable=write_count_stream",
            "为 write_count_stream 指定显式 depth，让 compute actor 用过的事务长度 token 能独立传给 write actor。",
        ),
    ):

        # 命中具体 FIFO 名称后，直接返回对应说明。
        if str_variable_needle in str_pragma_code:

            # 把当前 depth 约束直接落成对应 FIFO 的解耦说明。
            return str_comment_text

    # 其他 FIFO 深度约束回退到保守说明。
    return "为当前 stream FIFO 指定显式 depth，避免相邻阶段在握手时互相阻塞。"

# 根据 ARRAY_PARTITION 的 variable= 字段，把 `mat_a_tile`、`mat_b_tile` 这类块缓冲映射成可并行访问的 bank 展开说明。
def array_partition_pragma_comment_text(str_pragma_code: str) -> str:
    """为 ARRAY_PARTITION pragma 生成带目标缓冲区分的说明。

    参数:
        str_pragma_code: 当前 ARRAY_PARTITION pragma 的净代码文本，dtype=str，unit=pragma text。

    返回:
        当前局部缓冲 banking 约束对应的中文说明，dtype=str，unit=comment text。
    """

    # 先取出 pragma 里显式声明的目标变量名。
    obj_variable_match = re.search(r"\bvariable=([A-Za-z_]\w*)", str_pragma_code)  # ARRAY_PARTITION 目标变量的匹配对象

    # 命中时保留真实变量名，缺失时回退成稳定占位值。
    str_variable_name = obj_variable_match.group(1) if obj_variable_match else "unnamed_buffer"  # 当前 ARRAY_PARTITION 绑定的变量名

    # 只对已经确认过角色的局部缓冲生成专属 banking 说明。
    for str_expected_name, str_comment_text in (
        (
            "arr_tile_a",
            "把 arr_tile_a 完全拆成 4 条独立 lane，让当前 blocked tile 的 A 样本能在求和阶段被并行读取。",
        ),
        (
            "arr_tile_b",
            "把 arr_tile_b 完全拆成 4 个独立 bank，保证右操作数配对缓冲在逐 lane 求和时不会争用同一条读端口。",
        ),
        (
            "arr_lane_buf_a",
            "把 arr_lane_buf_a 完全拆成独立 lane，让 A 路局部向量在当前块内能并行读出并参与逐槽位求和。",
        ),
        (
            "arr_lane_buf_b",
            "把 arr_lane_buf_b 完全拆成独立 bank，保证 B 路配对向量在逐槽位相加时不会和 A 路缓冲复用同一访存端口。",
        ),
    ):

        # 命中专属局部缓冲后，直接返回对应 banking 说明。
        if str_variable_name == str_expected_name:

            # 把当前 banking 约束直接落成目标缓冲的并行访存说明。
            return str_comment_text

    # 其他局部分块缓冲回退到保守 banking 说明。
    return f"对 {str_variable_name} 应用显式 ARRAY_PARTITION，让当前局部缓冲按确认过的并行访存策略展开独立 bank。"

# 返回精确匹配的 UNROLL 阶段规则表，避免把 lane 级并行语义散落在主判定流程里。
def unroll_exact_stage_rules() -> tuple[tuple[tuple[str, ...], str], ...]:
    """返回精确匹配的 UNROLL 阶段规则表。

    参数:
        无显式业务参数；当前规则表只依赖 tile 写回和 stream 输出阶段的稳定片段。

    返回:
        精确匹配的 UNROLL 阶段规则表，dtype=tuple[tuple[tuple[str, ...], str], ...]，unit=pragma stage rules。
    """

    # 只收拢已经确认需要明确 lane 并行语义的 UNROLL 场景。
    return (
        (
            ("stream_out_stream.write(", "arr_tile_a[", "arr_tile_b["),
            "展开 tile 求和内层循环，让 4 个 lane 并行把 A/B 样本相加后送入 stream_out_stream。",
        ),
        (("ptr_output", "arr_tile_a[", "arr_tile_b["), "展开 tile 写回内层循环，让 4 个 lane 并行完成 A/B 样本求和并写回输出窗口。"),
    )

# 先匹配已经确认过语义的精确 UNROLL 场景。
def unroll_exact_stage_comment_text(str_stage_code: str) -> str:
    """返回精确命中的 UNROLL 并行说明。

    参数:
        str_stage_code: 当前 UNROLL pragma 后方的阶段代码文本，dtype=str，unit=code text。

    返回:
        命中精确展开场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐条匹配已经确认过含义的精确展开场景。
    for tuple_stage_needles, str_comment_text in unroll_exact_stage_rules():

        # 当前阶段动作完整命中规则时，直接返回对应说明。
        if all(str_stage_needle in str_stage_code for str_stage_needle in tuple_stage_needles):

            # 把命中的精确展开说明交回调用方。
            return str_comment_text

    # 没有命中精确规则时返回空字符串。
    return ""

# 再匹配 reshape 与 partition 这组缩放缓冲展开路径。
def unroll_scale_buffer_comment_text(str_stage_code: str) -> str:
    """返回 reshape 与 partition 缩放路径的 UNROLL 说明。

    参数:
        str_stage_code: 当前 UNROLL pragma 后方的阶段代码文本，dtype=str，unit=code text。

    返回:
        命中缩放缓冲展开场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # reshape 载入循环展开后，要强调 16 路本地槽位会并行锁住输入样本。
    if "arr_wide_buf[" in str_stage_code and "ptr_input_values[" in str_stage_code:

        # 交回 reshape 载入内层循环并行锁住 16 路输入样本的展开说明。
        return "展开 reshape 块的内层循环，让 16 个本地槽位并行锁住输入窗口样本。"

    # reshape 块的写回循环展开后，要强调 16 路缩放结果会并行落盘。
    if "ptr_output_values[base + j] = arr_wide_buf[j] * uint_scale_factor" in str_stage_code:

        # 交回 reshape 写回内层循环并行落盘 16 路缩放结果的展开说明。
        return "展开 reshape 块的写回内层循环，让 16 路本地样本并行乘上缩放因子后落盘。"

    # partition 块的载入循环展开后，要强调 16 个局部槽位会并行锁住输入样本。
    if "arr_local_buf[" in str_stage_code and "ptr_input_values[" in str_stage_code:

        # 交回 partition 载入内层循环并行锁住局部样本的展开说明。
        return "展开 partition 块的载入内层循环，让 16 个局部槽位并行锁住输入窗口样本。"

    # partition 块的写回循环展开后，要强调 16 个局部槽位会并行写回缩放结果。
    if "ptr_output_values[base + j] = arr_local_buf[j] * uint_scale_factor" in str_stage_code:

        # 把 partition 写回环节的并行语义改写成独立句式，强调缩放结果按槽位并发落盘。
        return "展开 partition 块的写回内层循环，让 16 个局部槽位并行乘上缩放因子后写回。"

    # 当前阶段不属于缩放缓冲类 UNROLL 规则时返回空字符串。
    return ""

# 然后匹配 stream block、lane-add 和 reduction 这组展开路径。
def unroll_stream_or_lane_comment_text(str_stage_code: str) -> str:
    """返回 stream block、lane-add 与 reduction 的 UNROLL 说明。

    参数:
        str_stage_code: 当前 UNROLL pragma 后方的阶段代码文本，dtype=str，unit=code text。

    返回:
        命中 stream/lane/reduction 展开场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # streamofblocks 载入循环展开后，要强调 block 内每个槽位并行领取输入 token。
    if "arr_block_buf[" in str_stage_code and "stream_in_stream.read()" in str_stage_code:

        # 交回 streamofblocks 载入内层循环并行读取 block token 的展开说明。
        return "展开 axis block 的载入内层循环，让 block 内各个槽位并行从输入流领取 token。"

    # streamofblocks 写回循环展开后，要强调 block 内每个有效槽位并行送出递增结果。
    if "stream_out_stream.write(arr_block_buf[j] + 1)" in str_stage_code:

        # 交回 streamofblocks 写回内层循环并行送出 block 结果的展开说明。
        return "展开 axis block 的写回内层循环，让各个有效槽位并行把递增结果送回输出流。"

    # lane-add 的载入循环展开后，要强调 A/B 两路 lane 会并行装入本地缓冲。
    if "arr_lane_buf_a[" in str_stage_code and "ptr_input_a[" in str_stage_code:

        # 交回 lane-add 载入内层循环并行装入两路样本的展开说明。
        return "展开 lane-add 的载入内层循环，让 A/B 两路 lane 样本并行装入本地缓冲。"

    # lane-add 的写回循环展开后，要强调逐 lane 求和结果会并行写回。
    if "ptr_output_values[base + j] = arr_lane_buf_a[j] + arr_lane_buf_b[j]" in str_stage_code:

        # 交回 lane-add 写回内层循环并行落盘逐 lane 求和结果的展开说明。
        return "展开 lane-add 的写回内层循环，让每个有效 lane 并行把 A/B 局部样本求和后写回。"

    # reduction tree 的子块展开后，要强调四路 partial 会并行准备给后续折叠。
    if "uint_partial0" in str_stage_code and "ptr_input_values[" in str_stage_code:

        # 交回 reduction tree 子块准备循环并行装入四路 partial 的展开说明。
        return "展开 reduction tree 的子块准备循环，让 4 路 partial 样本并行装入本地折叠节点。"

    # 当前阶段不属于 stream/lane/reduction 类 UNROLL 规则时返回空字符串。
    return ""

# 最后匹配 blocked tile，未命中时回退到通用 UNROLL 说明。
def unroll_tile_or_fallback_comment_text(str_stage_code: str) -> str:
    """返回 blocked tile 或最终兜底的 UNROLL 说明。

    参数:
        str_stage_code: 当前 UNROLL pragma 后方的阶段代码文本，dtype=str，unit=code text。

    返回:
        命中 blocked tile 时返回对应中文说明，否则返回最终兜底说明，dtype=str，unit=comment text。
    """

    # blocked matmul 的载入循环展开后，要强调 A/B 两路 lane 会并行搬运。
    if "arr_tile_a[" in str_stage_code or "arr_tile_b[" in str_stage_code:

        # 返回 blocked matmul 载入循环的展开说明。
        return "展开 tile 载入内层循环，让 4 个 lane 并行把 A/B 输入样本装入局部块缓冲。"

    # 其他场景回退到原有并行 lane 说明。
    return "展开有界内层循环以暴露并行 lane 并提升周期吞吐。"

# 汇总 UNROLL 各子规则，按精确度从高到低返回首条命中的说明。
def unroll_pragma_comment_text(str_stage_code: str) -> str:
    """为 UNROLL pragma 生成与当前阶段动作绑定的说明。

    参数:
        str_stage_code: 当前 UNROLL pragma 后方首条代表阶段职责的净代码文本，dtype=str，unit=code text。

    返回:
        当前循环展开策略对应的中文说明，dtype=str，unit=comment text。
    """

    # UNROLL 说明按精确场景、缩放缓冲、stream/lane/reduction 和 tile 兜底依次匹配。
    for str_comment_text in (
        unroll_exact_stage_comment_text(str_stage_code),
        unroll_scale_buffer_comment_text(str_stage_code),
        unroll_stream_or_lane_comment_text(str_stage_code),
        unroll_tile_or_fallback_comment_text(str_stage_code),
    ):

        # 只要当前 helper 命中说明，就立即返回。
        if str_comment_text:

            # 把首个命中的 UNROLL 说明交回调用方。
            return str_comment_text

    # tile/fallback helper 已经覆盖所有路径，这里只保留理论上的空串兜底。
    return ""
