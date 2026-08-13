"""收拢 mock HLS source 里函数签名识别、规则表和签名尾注逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 正则、宽泛类型提示和代码片段提取器负责支撑签名扫描。
import re
from typing import Any
from scripts.python.hls_quality_gate.readability.cpp_lexer import code_part

# 赋值聚合入口继续提供函数签名回溯 helper。
from .mock_hls_source_assignments import enclosing_signature_function_name

# FIR 子模块承接 staged DATAFLOW helper 的专属签名和参数角色规则。
from .mock_hls_fir_signatures import (
    is_fir_dataflow_helper_function_name,
    signature_fir_dataflow_comment_text,
    signature_fir_dataflow_inline_comment_text,
)

# 判断当前 helper 名称是否属于 task_graph 家族，便于签名和调用规则复用同一套分类。
def is_task_graph_helper_function_name(str_function_name: str) -> bool:
    """判断 helper 名称是否属于 task_graph 家族。

    参数:
        str_function_name: 当前待判断的 helper 名称，dtype=str，unit=function name。

    返回:
        命中 task_graph helper 名称时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # task_graph memory helper 和 axis actor helper 都沿用统一的命名前缀。
    return (
        str_function_name.startswith(
            (
                "load_task_graph_",
                "compute_task_graph_",
                "store_task_graph_",
                "read_task_graph_",
                "write_task_graph_",
            )
        )
        or (
            str_function_name.startswith("seed_task_graph_")
            and str_function_name.endswith("_counts")
        )
    )

# 判断 helper 名称是否属于当前 mock dataflow 常见的通用阶段家族。
def is_generic_flow_helper_function_name(str_function_name: str) -> bool:
    """判断 helper 名称是否属于通用 dataflow/stream 阶段家族。

    参数:
        str_function_name: 当前待判断的 helper 名称，dtype=str，unit=function name。

    返回:
        命中通用阶段 helper 名称时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 当前规则只收敛这轮 mock source 里稳定存在的 read/compute/write 与 block stage helper。
    return str_function_name in {
        "read_block",
        "row_pass",
        "transpose_or_reorder",
        "col_pass",
        "write_block",
        "read_dataflow_axis_increment",
        "compute_dataflow_axis_increment",
        "write_dataflow_axis_increment",
    }

# 返回当前通用 dataflow helper 的函数头说明规则表。
def signature_generic_flow_header_rules() -> tuple[tuple[str, str, str], ...]:
    """返回通用 dataflow helper 的函数头说明规则表。

    参数:
        无显式业务参数；当前规则表只依赖通用 block/dataflow helper 的稳定命名。

    返回:
        通用 helper 函数头规则表，dtype=tuple[tuple[str, str, str], ...]，unit=signature header rules。
    """

    # 按 helper 名称返回这轮失败簇对应的具体函数头说明。
    return (
        (
            "read_block",
            "static void read_block(",
            "read_block 在这里声明块读入 helper，负责按扁平索引读取输入窗口，并把样本送入 stream_read_stream。",
        ),
        (
            "row_pass",
            "static void row_pass(",
            "row_pass 在这里声明第一段行向处理 helper，负责消费 stream_read_stream，并把块样本转交给 stream_row_stream。",
        ),
        (
            "transpose_or_reorder",
            "static void transpose_or_reorder(",
            "transpose_or_reorder 在这里声明二维块重排 helper，负责把行向阶段输出重组后送入 stream_reorder_stream。",
        ),
        (
            "col_pass",
            "static void col_pass(",
            "col_pass 在这里声明第二段列向处理 helper，负责消费 stream_reorder_stream，并把结果送入 stream_col_stream。",
        ),
        (
            "write_block",
            "static void write_block(",
            "write_block 在这里声明块写回 helper，负责从 stream_col_stream 读取处理后的样本，并顺序写回 ptr_output_values。",
        ),
        (
            "read_dataflow_axis_increment",
            "static void read_dataflow_axis_increment(",
            "read_dataflow_axis_increment 在这里声明 axis 读入 helper，负责从输入 stream 逐项取 token，并把样本转发到 stream_mid_stream。",
        ),
        (
            "compute_dataflow_axis_increment",
            "static void compute_dataflow_axis_increment(",
            "compute_dataflow_axis_increment 在这里声明递增计算 helper，负责消费 stream_mid_stream，并把结果送入 stream_result_stream。",
        ),
        (
            "write_dataflow_axis_increment",
            "static void write_dataflow_axis_increment(",
            "write_dataflow_axis_increment 在这里声明 axis 写出 helper，负责从 stream_result_stream 读取递增结果，并送回 stream_out_stream。",
        ),
    )

# 返回 block-style dataflow helper 的签名参数说明规则表。
def signature_generic_flow_block_parameter_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回 block-style dataflow helper 的签名参数说明规则表。

    参数:
        无显式业务参数；当前规则表只依赖 read_block/row_pass/reorder/col_pass/write_block 的固定参数角色。

    返回:
        block-style helper 参数规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，unit=signature parameter rules。
    """

    # 先收拢二维块 dataflow 主链上的参数说明，避免 block helper 和 axis helper 混成一张超长表。
    return (
        ("read_block", ("ptr_input_values",), "ptr_input_values 在这里暴露扁平输入窗口，让 read_block 按行列乘积顺序取出当前二维块的原始样本。"),
        (
            "read_block",
            ("stream_read_stream",),
            "stream_read_stream 在这里作为 read_block 的输出通道，扁平读取出的块样本会先通过这条 FIFO 交给 row_pass。",
        ),
        ("read_block", ("int int_rows",), "int_rows 在这里声明当前二维块的行数，让 read_block 计算输入块的总样本量时保留正确的行向边界。"),
        ("read_block", ("int int_cols",), "int_cols 在这里声明当前二维块的列数，让 read_block 读取总量与后续 row/col 阶段共享同一块宽度。"),
        (
            "row_pass",
            ("stream_read_stream",),
            "stream_read_stream 在这里作为 row_pass 的输入通道，read_block 送来的块样本会先从这条 FIFO 被逐项取出。",
        ),
        ("row_pass", ("stream_row_stream",), "stream_row_stream 在这里作为 row_pass 的输出通道，完成第一段行向处理的样本会从这里继续交给重排阶段。"),
        ("row_pass", ("int int_rows",), "int_rows 在这里告诉 row_pass 当前块共有多少行，避免行向阶段跨出本轮二维块的有效边界。"),
        ("row_pass", ("int int_cols",), "int_cols 在这里告诉 row_pass 每行包含多少列样本，让行向阶段和后续重排阶段共享同一列宽。"),
        (
            "transpose_or_reorder",
            ("stream_row_stream",),
            "stream_row_stream 在这里作为重排 helper 的输入通道，行向阶段产出的块样本会先从这条 FIFO 被逐项读取。",
        ),
        (
            "transpose_or_reorder",
            ("stream_reorder_stream",),
            "stream_reorder_stream 在这里作为重排 helper 的输出通道，重排后的块样本会通过这条 FIFO 交给 col_pass。",
        ),
        ("transpose_or_reorder", ("int int_rows",), "int_rows 在这里给重排 helper 标明块行数，确保二维块的行向跨度在重排阶段仍然可见。"),
        ("transpose_or_reorder", ("int int_cols",), "int_cols 在这里给重排 helper 标明块列数，让列向阶段继续沿同一二维宽度消费样本。"),
        ("col_pass", ("stream_reorder_stream",), "stream_reorder_stream 在这里作为 col_pass 的输入通道，重排后的块样本会先从这条 FIFO 被逐项取回。"),
        ("col_pass", ("stream_col_stream",), "stream_col_stream 在这里作为 col_pass 的输出通道，列向处理后的样本会从这里继续交给 write_block。"),
        ("col_pass", ("int int_rows",), "int_rows 在这里告诉 col_pass 当前块的行数，让列向阶段和前面的 row/reorder 阶段保持同一二维边界。"),
        ("col_pass", ("int int_cols",), "int_cols 在这里告诉 col_pass 当前块的列数，方便列向阶段按正确的块宽度继续推进。"),
        ("write_block", ("stream_col_stream",), "stream_col_stream 在这里作为 write_block 的输入通道，列向阶段生成的块样本会从这条 FIFO 被顺序取回。"),
        ("write_block", ("ptr_output_values",), "ptr_output_values 在这里暴露最终输出窗口，让 write_block 把处理后的二维块结果按扁平索引写回主存。"),
        ("write_block", ("int int_rows",), "int_rows 在这里告诉 write_block 当前块的行数，让最终写回总量与前面各阶段共享同一二维边界。"),
        ("write_block", ("int int_cols",), "int_cols 在这里告诉 write_block 当前块的列数，保证写回索引不会偏离当前二维块的真实宽度。"),
    )

# 单独收拢 AXIS 递增链路的参数说明，避免 token 事务边界和二维块主链混在同一张规则表里。
def signature_generic_flow_axis_parameter_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回 axis increment dataflow helper 的签名参数说明规则表。

    参数:
        无显式业务参数；当前规则表只依赖 read/compute/write axis increment helper 的固定参数角色。

    返回:
        axis increment helper 参数规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，unit=signature parameter rules。
    """

    # 再收拢 AXIS 递增链路上的参数说明，确保 token 事务边界和 FIFO 角色单独成组。
    return (
        (
            "read_dataflow_axis_increment",
            ("stream_in_stream",),
            "stream_in_stream 在这里作为读入 helper 的输入通道，外部 axis 样本会先从这条流逐 token 进入当前 dataflow 路径。",
        ),
        (
            "read_dataflow_axis_increment",
            ("stream_mid_stream",),
            "stream_mid_stream 在这里作为读入 helper 的输出通道，输入 token 会先通过这条 FIFO 转交给递增计算阶段。",
        ),
        (
            "read_dataflow_axis_increment",
            ("int int_length",),
            "int_length 在这里限定读入 helper 只转发本轮有效 token 数，避免输入流在静态 workflow 里越过事务边界。",
        ),
        (
            "compute_dataflow_axis_increment",
            ("stream_mid_stream",),
            "stream_mid_stream 在这里作为递增 helper 的输入通道，读入阶段转发过来的样本会先从这条 FIFO 被逐项取回。",
        ),
        (
            "compute_dataflow_axis_increment",
            ("stream_result_stream",),
            "stream_result_stream 在这里作为递增 helper 的输出通道，完成 +1 处理的样本会先写进这条 FIFO 等待写出阶段消费。",
        ),
        (
            "compute_dataflow_axis_increment",
            ("int int_length",),
            "int_length 在这里限定递增 helper 只处理本轮有效 token 数，确保 result stream 和输入事务长度保持一致。",
        ),
        (
            "write_dataflow_axis_increment",
            ("stream_result_stream",),
            "stream_result_stream 在这里作为写出 helper 的输入通道，已经递增完成的样本会先从这条 FIFO 被逐项取回。",
        ),
        (
            "write_dataflow_axis_increment",
            ("stream_out_stream",),
            "stream_out_stream 在这里作为写出 helper 的输出通道，让本轮递增后的 token 顺序送回最终 axis 边界。",
        ),
        (
            "write_dataflow_axis_increment",
            ("int int_length",),
            "int_length 在这里限定写出 helper 只发送本轮有效 token 数，避免输出流多发超出事务边界的样本。",
        ),
    )

# 返回当前通用 dataflow helper 的签名参数说明规则表。
def signature_generic_flow_parameter_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回通用 dataflow helper 的签名参数说明规则表。

    参数:
        无显式业务参数；当前规则表只依赖通用 block/dataflow helper 的固定参数角色。

    返回:
        通用 helper 参数规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，unit=signature parameter rules。
    """

    # 把二维块主链和 AXIS 递增主链的参数规则拼成一张统一视图，供签名注释重写逻辑稳定复用。
    return signature_generic_flow_block_parameter_rules() + signature_generic_flow_axis_parameter_rules()

# 返回当前通用 dataflow helper 的签名尾注规则表。
def signature_generic_flow_inline_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回通用 dataflow helper 的签名尾注规则表。

    参数:
        无显式业务参数；当前规则表只依赖通用 block/dataflow helper 的参数角色。

    返回:
        通用 helper 签名尾注规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，unit=signature inline rules。
    """

    # 这些尾注只保留和摘要不同的第二观察面，避免再退回统一模板句。
    return (
        ("read_block", ("ptr_input_values",), "read_block 会按扁平索引从这个输入窗口逐项取样。"),
        ("read_block", ("stream_read_stream",), "读取出的块样本会先写进这条 read stream。"),
        ("read_block", ("int int_rows",), "这里显式给出二维块的行数边界。"),
        ("read_block", ("int int_cols",), "这里显式给出二维块的列数边界。"),
        ("row_pass", ("stream_read_stream",), "row_pass 会先从这条 FIFO 取回 read_block 推来的样本。"),
        ("row_pass", ("stream_row_stream",), "行向处理后的样本会通过这条 row stream 继续下传。"),
        ("row_pass", ("int int_rows",), "当前 row_pass 只覆盖这轮块内的有效行。"),
        ("row_pass", ("int int_cols",), "列宽参数让 row_pass 和后续重排阶段保持同一块宽度。"),
        ("transpose_or_reorder", ("stream_row_stream",), "行向阶段产出的块样本会先从这里被读取。"),
        ("transpose_or_reorder", ("stream_reorder_stream",), "重排后的样本会通过这条 reorder stream 继续交给列向阶段。"),
        ("transpose_or_reorder", ("int int_rows",), "这个参数保留重排阶段的块行数。"),
        ("transpose_or_reorder", ("int int_cols",), "这个参数保留重排阶段的块列宽。"),
        ("col_pass", ("stream_reorder_stream",), "列向阶段会先从这条 reorder stream 取回样本。"),
        ("col_pass", ("stream_col_stream",), "列向处理后的样本会通过这条 col stream 继续交给写回阶段。"),
        ("col_pass", ("int int_rows",), "这个参数让列向阶段继续沿同一块行数推进。"),
        ("col_pass", ("int int_cols",), "这个参数让列向阶段继续沿同一块列宽推进。"),
        ("write_block", ("stream_col_stream",), "write_block 会先从这条 col stream 领取待写回样本。"),
        ("write_block", ("ptr_output_values",), "最终块结果会顺序落到这个输出窗口。"),
        ("write_block", ("int int_rows",), "写回阶段沿用同一块行数计算总输出量。"),
        ("write_block", ("int int_cols",), "写回阶段沿用同一块列宽计算总输出量。"),
        ("read_dataflow_axis_increment", ("stream_in_stream",), "读入 helper 会逐 token 消费这条输入流。"),
        ("read_dataflow_axis_increment", ("stream_mid_stream",), "输入 token 会先原样转交给这条中间 FIFO。"),
        ("read_dataflow_axis_increment", ("int int_length",), "这里只允许转发本轮有效 token 数。"),
        ("compute_dataflow_axis_increment", ("stream_mid_stream",), "递增 helper 会先从这条中间 FIFO 读取样本。"),
        ("compute_dataflow_axis_increment", ("stream_result_stream",), "递增后的样本会先写进这条 result FIFO。"),
        ("compute_dataflow_axis_increment", ("int int_length",), "这里只处理本轮有效 token 数。"),
        ("write_dataflow_axis_increment", ("stream_result_stream",), "写出 helper 会先从这条 result FIFO 领取结果。"),
        ("write_dataflow_axis_increment", ("stream_out_stream",), "最终 token 会通过这条输出流顺序送回下游。"),
        ("write_dataflow_axis_increment", ("int int_length",), "这里只发送本轮有效 token 数。"),
    )

# 返回当前通用 dataflow helper 的函数体入口说明规则表。
def signature_generic_flow_body_entry_rules() -> tuple[tuple[str, str], ...]:
    """返回通用 dataflow helper 的函数体入口说明规则表。

    参数:
        无显式业务参数；当前规则表只依赖通用 block/dataflow helper 的稳定命名。

    返回:
        通用 helper 入口规则表，dtype=tuple[tuple[str, str], ...]，unit=signature body-entry rules。
    """

    # 把 helper 的函数体入口动作写成显式阶段说明，避免闭合行继续退回模板句。
    return (
        ("read_block", "进入 read_block 的函数体，开始按扁平索引读取当前二维块，并把样本送入 stream_read_stream。"),
        ("row_pass", "进入 row_pass 的函数体，开始消费 stream_read_stream 的块样本，并把第一段结果送入 stream_row_stream。"),
        ("transpose_or_reorder", "进入 transpose_or_reorder 的函数体，开始重组 row_pass 的输出样本，并把结果送入 stream_reorder_stream。"),
        ("col_pass", "进入 col_pass 的函数体，开始消费重排后的块样本，并把列向处理结果送入 stream_col_stream。"),
        ("write_block", "进入 write_block 的函数体，开始从 stream_col_stream 逐项取回样本，并按扁平索引写回输出窗口。"),
        (
            "read_dataflow_axis_increment",
            "进入 read_dataflow_axis_increment 的函数体，开始逐 token 读取输入流，"
            "并把样本转发到 stream_mid_stream。",
        ),
        (
            "compute_dataflow_axis_increment",
            "进入 compute_dataflow_axis_increment 的函数体，开始消费中间样本，"
            "并把递增结果送入 stream_result_stream。",
        ),
        ("write_dataflow_axis_increment", "进入 write_dataflow_axis_increment 的函数体，开始逐 token 读取 result stream，并顺序送回输出流。"),
    )

# 为 helper 多行签名里的函数头、参数和签名收尾生成专属说明。
def function_signature_comment_text(
    list_lines: list[str],
    int_code_index: int,
    str_next_code: str,
) -> str:
    """为 helper 多行签名里的函数头、参数和签名收尾生成专属说明。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前绑定代码所在的零基行号，dtype=int，unit=line index。
        str_next_code: 当前注释直接绑定的代码文本，dtype=str，unit=code text。

    返回:
        命中 helper 签名片段时返回具体说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 只有真正属于多行签名的代码片段才进入当前分支，避免函数体语句误吃签名注释规则。
    if not is_function_signature_fragment(str_next_code):

        # 非签名代码继续走后续的循环、赋值和 stream 写说明分支。
        return ""

    # 局部声明如果已经落到函数体里，就不能再复用签名参数的注释规则。
    if not is_active_signature_fragment(list_lines, int_code_index, str_next_code):

        # 当前候选只是普通局部声明或赋值，不属于活跃签名范围。
        return ""

    # 先回溯当前参数或函数头属于哪个 helper，避免不同签名片段复用同一句模板。
    str_function_name = enclosing_signature_function_name(list_lines, int_code_index)  # 当前签名片段所属的 helper 名称

    # 不在当前 dataflow helper 白名单内时直接跳过。
    if (
        str_function_name not in {
            "load_matmul_a",
            "load_matmul_b",
            "compute_matmul_tile",
            "store_matmul",
        }
        and not is_task_graph_helper_function_name(str_function_name)
        and not is_generic_flow_helper_function_name(str_function_name)
        and not is_fir_dataflow_helper_function_name(str_function_name)
    ):

        # 当前代码不属于需要改写的 helper 多行签名。
        return ""

    # 依次尝试函数头、参数、长度边界和函数体入口说明，命中首个结果后立即返回。
    for str_comment_text in (
        signature_fir_dataflow_comment_text(str_function_name, str_next_code),
        signature_header_comment_text(str_function_name, str_next_code),
        signature_parameter_comment_text(str_function_name, str_next_code),
        signature_length_comment_text(str_function_name, str_next_code),
        signature_body_entry_comment_text(str_function_name, str_next_code),
    ):

        # 当前签名片段一旦已经绑定到具体 helper 角色，就不再继续退回更宽泛的后续规则。
        if str_comment_text:

            # 返回首个命中的签名说明。
            return str_comment_text

    # 其他签名片段不在这里强制改写。
    return ""

# 为 helper 多行签名的函数头生成按家族区分的起始说明。
def signature_header_comment_text(str_function_name: str, str_code: str) -> str:
    """为 helper 多行签名的函数头生成按家族区分的起始说明。

    参数:
        str_function_name: 当前签名片段所属的 helper 名称，dtype=str，unit=function name。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中函数头规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # matmul helper 的函数头需要分别点明 load、compute 和 store 角色。
    for str_expected_name, str_header_prefix, str_comment_text in (
        (
            "load_matmul_a",
            "static void load_matmul_a(",
            "load_matmul_a 在这里声明 A 路加载 helper，负责把 input_a 样本按索引推入 stream_a_stream。",
        ),
        (
            "load_matmul_b",
            "static void load_matmul_b(",
            "load_matmul_b 在这里声明右操作数预取 helper，专门把 input_b 的配对加数窗口连续灌入 stream_b_stream。",
        ),
        (
            "compute_matmul_tile",
            "static void compute_matmul_tile(",
            "compute_matmul_tile 在这里声明 tile 计算 helper，负责从 A/B FIFO 取 token，逐 lane 求和后写入 stream_out_stream。",
        ),
        (
            "store_matmul",
            "static void store_matmul(",
            "store_matmul 在这里声明输出写回 helper，负责从 stream_out_stream 读取结果并顺序写回 ptr_output_values。",
        ),
    ):

        # 当前函数头同时命中 helper 名称和签名起始前缀时，就把说明绑定到这个 matmul 角色。
        if str_function_name == str_expected_name and str_code.startswith(str_header_prefix):

            # 返回当前 matmul helper 的函数头说明。
            return str_comment_text

    # block/dataflow 失败簇里的 helper 也要绑定成专属函数头说明。
    for str_expected_name, str_header_prefix, str_comment_text in signature_generic_flow_header_rules():

        # 当前函数头只要同时命中 helper 名称和签名前缀，就直接返回对应阶段说明。
        if str_function_name == str_expected_name and str_code.startswith(str_header_prefix):

            # 返回当前通用 helper 的函数头说明。
            return str_comment_text

    # task_graph helper 的函数头需要区分 load、compute、store 与 actor 调度角色。
    for str_name_prefix, str_header_prefix, str_comment_text in (
        (
            "load_task_graph_",
            "static void load_task_graph_",
            (
                "load_task_graph_* 在这里声明 memory 读入 helper，"
                "负责把 ptr_input_values 的样本按索引推入 stream_task_stream，"
                "并把事务长度同步给下游 count stream。"
            ),
        ),
        (
            "compute_task_graph_",
            "static void compute_task_graph_",
            "compute_task_graph_* 在这里声明 task_graph 的计算 helper，负责消费 task stream 样本、完成递增并把结果送入 result stream。",
        ),
        (
            "store_task_graph_",
            "static void store_task_graph_",
            "store_task_graph_* 在这里声明结果写回 helper，负责从 result stream 读取递增后的样本并顺序写回 ptr_output_values。",
        ),
        (
            "seed_task_graph_",
            "static void seed_task_graph_",
            "seed_task_graph_*_counts 在这里声明计数播种 helper，负责把一次事务长度显式送入 read_count_stream，保留 task_graph 的 restart 边界。",
        ),
        (
            "read_task_graph_",
            "static void read_task_graph_",
            "read_task_graph_* 在这里声明读入 actor，负责消费上游输入 token，并把事务长度与样本通道同时传给下游 compute actor。",
        ),
        (
            "write_task_graph_",
            "static void write_task_graph_",
            "write_task_graph_* 在这里声明写出 actor，负责消费 result stream token，并在 write_count_stream 约束下把结果逐项送回 out_stream。",
        ),
    ):

        # 当前函数头只要确认属于这类 task_graph helper，就把函数头说明绑定到对应阶段角色。
        if str_function_name.startswith(str_name_prefix) and str_code.startswith(str_header_prefix):

            # 当前函数头已经确认命中 task_graph helper 前缀，直接返回对应阶段说明。
            return str_comment_text

    # 其他函数头不在这里补说明。
    return ""

# 为 helper 多行签名的参数片段生成按 FIFO、count stream 和边界区分的说明。
def signature_parameter_comment_text(str_function_name: str, str_code: str) -> str:
    """为 helper 多行签名的参数片段生成按 FIFO、count stream 和边界区分的说明。

    参数:
        str_function_name: 当前签名片段所属的 helper 名称，dtype=str，unit=function name。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中参数规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # matmul helper 的参数片段需要按输入窗口、输出窗口和 FIFO 角色分别说明。
    for str_expected_name, tuple_needles, str_comment_text in signature_matmul_parameter_rules():

        # 当前参数片段只有同时命中 helper 名称和参数关键字时，才返回具体 matmul 参数说明。
        if str_function_name == str_expected_name and all(
            str_needle in str_code for str_needle in tuple_needles
        ):

            # 返回当前 matmul 参数片段的说明。
            return str_comment_text

    # block/dataflow helper 的参数片段需要按块窗口、stream 边界和 shape/int_length 角色分别说明。
    for str_expected_name, tuple_needles, str_comment_text in signature_generic_flow_parameter_rules():

        # 当前参数片段只有同时命中 helper 名称和参数关键字时，才返回对应的阶段参数说明。
        if str_function_name == str_expected_name and all(
            str_needle in str_code for str_needle in tuple_needles
        ):

            # 返回当前通用 helper 参数片段的说明。
            return str_comment_text

    # task_graph helper 的参数片段需要按 task stream、count stream 和外部边界分别说明。
    for str_name_prefix, tuple_needles, str_comment_text in signature_task_graph_parameter_rules():

        # 当前参数片段一旦确认属于这类 task_graph 通道或边界，就直接返回对应说明。
        if str_function_name.startswith(str_name_prefix) and all(
            str_needle in str_code for str_needle in tuple_needles
        ):

            # 当前参数片段已经命中 task_graph 通道角色，直接返回对应参数说明。
            return str_comment_text

    # 其他参数片段不在这里补说明。
    return ""

# 返回 matmul helper 签名参数的规则表，避免参数说明函数内联过长的字面量列表。
def signature_matmul_parameter_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回 matmul helper 签名参数的规则表。

    参数:
        无显式业务参数；当前规则表只依赖 matmul helper 的固定参数角色。

    返回:
        matmul helper 参数规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，unit=signature parameter rules。
    """

    # 统一返回 matmul helper 的输入窗口、输出窗口和 FIFO 角色规则。
    return (
        (
            "load_matmul_a",
            ("ptr_input_a",),
            (
                "ptr_input_a 在这里暴露 A 路输入窗口，"
                "让 load_matmul_a 顺序取出左操作数样本并送往 stream_a_stream。"
            ),
        ),
        (
            "load_matmul_b",
            ("ptr_input_b",),
            (
                "ptr_input_b 在这里暴露 B 路输入窗口，"
                "让 load_matmul_b 顺序取出右操作数样本并送往 stream_b_stream。"
            ),
        ),
        (
            "load_matmul_a",
            ("stream_a_stream",),
            (
                "stream_a_stream 在这里作为 load_matmul_a 的输出形参暴露给调用方，"
                "顶层会把 A 路 FIFO 句柄接到这里。"
            ),
        ),
        (
            "load_matmul_b",
            ("stream_b_stream",),
            (
                "stream_b_stream 在这里作为 load_matmul_b 的外送句柄，"
                "把右侧配对样本流回传给顶层持有的 B 路 FIFO。"
            ),
        ),
        (
            "compute_matmul_tile",
            ("stream_a_stream",),
            (
                "stream_a_stream 在这里作为 compute helper 的输入形参，"
                "调用方会把 A 路 FIFO 接到这里供逐 lane 读取。"
            ),
        ),
        (
            "compute_matmul_tile",
            ("stream_b_stream",),
            (
                "stream_b_stream 在这里作为 compute helper 的右侧输入句柄，"
                "逐 lane 提供与 A 路相配的加数 token。"
            ),
        ),
        (
            "compute_matmul_tile",
            ("stream_out_stream",),
            (
                "stream_out_stream 在这里作为 compute helper 的输出形参，"
                "把逐 lane 求和结果回传给调用方持有的输出 FIFO。"
            ),
        ),
        (
            "store_matmul",
            ("stream_out_stream",),
            (
                "stream_out_stream 在这里作为 store helper 的输入形参，"
                "调用方会把输出 FIFO 句柄接到这里供顺序写回。"
            ),
        ),
        (
            "store_matmul",
            ("ptr_output_values",),
            (
                "ptr_output_values 在这里暴露最终输出窗口，"
                "让 store_matmul 把 dataflow 结果按原索引顺序写回主存。"
            ),
        ),
    )

# 返回 task_graph 中 load/compute/store 类 helper 的签名参数规则表。
def signature_task_graph_data_parameter_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回 task_graph 中 load/compute/store 类 helper 的签名参数规则表。

    参数:
        无显式业务参数；当前规则表只依赖 task_graph 的数据通道与 memory 边界角色。

    返回:
        task_graph 数据通道参数规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，
        unit=signature parameter rules。
    """

    # 统一返回 task_graph 的 memory 边界、样本通道和结果通道参数规则。
    return (
        (
            "load_task_graph_",
            ("ptr_input_values",),
            (
                "ptr_input_values 在这里暴露 memory 输入窗口，"
                "让 load helper 按索引读取原始样本并推入 stream_task_stream。"
            ),
        ),
        (
            "load_task_graph_",
            ("stream_task_stream",),
            (
                "stream_task_stream 在这里作为 load helper 的输出形参暴露给调用方，"
                "主存读入的样本会通过这条 FIFO 交给 compute helper。"
            ),
        ),
        (
            "load_task_graph_",
            ("stream_count_stream",),
            (
                "stream_count_stream 在这里承接本次事务长度 token，"
                "让下游 compute helper 不必再直接持有非 stream 形参。"
            ),
        ),
        (
            "compute_task_graph_",
            ("stream_task_stream",),
            (
                "stream_task_stream 在这里作为 compute helper 的输入通道，"
                "递增 actor 会从这条 FIFO 逐拍领取待处理样本。"
            ),
        ),
        (
            "compute_task_graph_",
            ("stream_task_result_stream",),
            (
                "stream_task_result_stream 在这里作为 compute helper 的结果通道，"
                "递增后的样本会从这里继续交给写回阶段。"
            ),
        ),
        (
            "compute_task_graph_",
            ("stream_count_stream",),
            (
                "stream_count_stream 在这里为 compute helper 提供本次事务长度 token，"
                "使 hls::task 保持 stream-only 连接方式。"
            ),
        ),
        (
            "store_task_graph_",
            ("stream_task_result_stream",),
            (
                "stream_task_result_stream 在这里作为写回 helper 的输入通道，"
                "store 阶段会从这条 FIFO 逐项取回已递增的样本。"
            ),
        ),
        (
            "store_task_graph_",
            ("ptr_output_values",),
            (
                "ptr_output_values 在这里暴露最终输出窗口，"
                "让 store helper 把 result stream 的样本按原索引顺序写回主存。"
            ),
        ),
    )

# 汇总 task_graph 里 seed/read/write actor 这几段调度边界使用的签名参数规则。
def signature_task_graph_actor_parameter_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """返回 task_graph 中 seed/read/write actor 类 helper 的签名参数规则表。

    参数:
        无显式业务参数；当前规则表只依赖 task_graph actor 的输入、输出与计数边界角色。

    返回:
        task_graph actor 参数规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，
        unit=signature parameter rules。
    """

    # 统一返回 seed/read/write actor 需要使用的事务长度与样本通道规则。
    return (
        (
            "seed_task_graph_",
            ("read_count_stream",),
            (
                "read_count_stream 在这里承接一次事务长度 token，"
                "供 read actor 在真正消费输入 token 之前先锁定边界。"
            ),
        ),
        (
            "read_task_graph_",
            ("in_stream",),
            (
                "in_stream 在这里暴露 task_graph 的上游输入流，"
                "read actor 会按事务边界顺序读取外部 token。"
            ),
        ),
        (
            "read_task_graph_",
            ("stream_task_stream",),
            (
                "stream_task_stream 在这里作为 read actor 的输出通道，"
                "输入 token 会先写进这条 task stream，再交给 compute actor。"
            ),
        ),
        (
            "read_task_graph_",
            ("read_count_stream",),
            (
                "read_count_stream 在这里为 read actor 提供一次事务长度 token，"
                "确保输入读取和本轮 restart 边界保持一致。"
            ),
        ),
        (
            "read_task_graph_",
            ("compute_count_stream",),
            (
                "compute_count_stream 在这里把已经消费过的事务长度转交给 compute actor，"
                "使样本路径和边界路径继续对齐。"
            ),
        ),
        (
            "compute_task_graph_",
            ("compute_count_stream",),
            (
                "compute_count_stream 在这里为 compute actor 提供本轮事务长度 token，"
                "让递增阶段在 stream-only 约束下仍能拿到循环边界。"
            ),
        ),
        (
            "compute_task_graph_",
            ("write_count_stream",),
            (
                "write_count_stream 在这里接住 compute actor 用完的事务长度 token，"
                "供 write actor 按同一边界回放输出结果。"
            ),
        ),
        (
            "write_task_graph_",
            ("out_stream",),
            (
                "out_stream 在这里暴露 task_graph 的下游输出流，"
                "write actor 会把 result stream 里的 token 顺序送回这条边界。"
            ),
        ),
        (
            "write_task_graph_",
            ("write_count_stream",),
            (
                "write_count_stream 在这里为 write actor 提供本轮事务长度 token，"
                "确保输出 token 数和上游输入事务完全对齐。"
            ),
        ),
    )

# 汇总 task_graph helper 的全部参数规则，供签名参数说明函数复用。
def signature_task_graph_parameter_rules() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """汇总 task_graph helper 的全部参数规则。

    参数:
        无显式业务参数；当前函数只负责拼接 task_graph 的规则子表。

    返回:
        完整的 task_graph 参数规则表，dtype=tuple[tuple[str, tuple[str, ...], str], ...]，
        unit=signature parameter rules。
    """

    # 把 task_graph 的数据通道规则和 actor 规则合并成一张完整参数规则表。
    return signature_task_graph_data_parameter_rules() + signature_task_graph_actor_parameter_rules()

# 为 helper 多行签名里的 `int_length` 参数生成按角色区分的边界说明。
def signature_length_comment_text(str_function_name: str, str_code: str) -> str:
    """为 helper 多行签名里的 `int_length` 参数生成按角色区分的边界说明。

    参数:
        str_function_name: 当前签名片段所属的 helper 名称，dtype=str，unit=function name。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中长度参数规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 非 `int_length` 参数直接跳过，避免把其他 `int_` 形参误判成长度边界。
    if not re.search(r"\bint\s+int_length\b", str_code):

        # 当前参数行没有出现长度边界，不需要额外说明。
        return ""

    # matmul helper 的长度参数需要分别约束读取、补零和写回的有效范围。
    for str_expected_name, str_comment_text in (
        ("load_matmul_a", "int_length 在这里限定 A 路加载 helper 只推进有效样本数，避免 input_a 读取越过事务长度。"),
        ("load_matmul_b", "int_length 在这里裁剪右操作数窗口的连续读取范围，防止 input_b 把配对列片段读过事务尾部。"),
        ("compute_matmul_tile", "int_length 在这里限定 tile 计算 helper 的全局有效长度，并为尾块补零判断提供边界。"),
        ("store_matmul", "int_length 在这里限定写回 helper 只消费有效结果数，避免尾块外的无效 token 被写回输出窗口。"),
    ):

        # 当前长度参数一旦已经定位到具体 matmul helper，就直接返回边界说明。
        if str_function_name == str_expected_name:

            # 返回当前 matmul helper 的长度边界说明。
            return str_comment_text

    # task_graph 的 `int_length` 只在 memory 版 load/store helper 上出现。
    for str_name_prefix, str_comment_text in (
        ("load_task_graph_", "int_length 在这里限定 load helper 只从 ptr_input_values 读取本轮有效样本数，并把同一边界同步给下游 count stream。"),
        ("store_task_graph_", "int_length 在这里限定写回 helper 只消费本轮有效结果数，避免 result stream 尾部之外的 token 被误写回输出窗口。"),
    ):

        # 当前长度参数已经定位到 load 或 store 这类 memory helper 时，直接返回边界说明。
        if str_function_name.startswith(str_name_prefix):

            # 当前长度参数已经命中 task_graph memory helper，直接返回边界说明。
            return str_comment_text

    # 其他长度参数在这里不追加说明。
    return ""

# 为 helper 多行签名的闭合行生成函数体入口说明。
def signature_body_entry_comment_text(str_function_name: str, str_code: str) -> str:
    """为 helper 多行签名的闭合行生成函数体入口说明。

    参数:
        str_function_name: 当前签名片段所属的 helper 名称，dtype=str，unit=function name。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中 `) {` 入口规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 非签名闭合行不需要函数体入口说明。
    if str_code != ") {":

        # 当前签名片段还没进入函数体，不追加入口说明。
        return ""

    # matmul helper 的函数体入口需要分别指出输入搬运、计算和写回动作从哪里开始。
    for str_expected_name, str_comment_text in (
        ("load_matmul_a", "进入 load_matmul_a 的函数体，开始把 A 路窗口样本搬运到 dataflow FIFO。"),
        ("load_matmul_b", "进入 load_matmul_b 的函数体，开始把右侧配对窗口连续泵入 dataflow 的 B 路 FIFO。"),
        ("compute_matmul_tile", "进入 compute_matmul_tile 的函数体，开始按 blocked tile 读取 A/B token 并生成求和结果。"),
        ("store_matmul", "进入 store_matmul 的函数体，开始把输出 FIFO 里的结果 token 顺序写回主存窗口。"),
    ):

        # 当前签名闭合行已经确认属于这个 matmul helper 时，直接返回对应入口说明。
        if str_function_name == str_expected_name:

            # 返回当前 matmul helper 的函数体入口说明。
            return str_comment_text

    # block/dataflow helper 的闭合行也要落成专属阶段入口说明。
    for str_expected_name, str_comment_text in signature_generic_flow_body_entry_rules():

        # 当前签名闭合行命中目标 helper 后，直接返回对应阶段入口说明。
        if str_function_name == str_expected_name:

            # 把命中的通用 helper 函数体入口说明交回调用方。
            return str_comment_text

    # task_graph helper 的函数体入口需要区分 load、compute、store 与 actor 编排角色。
    for str_name_prefix, str_comment_text in (
        (
            "load_task_graph_",
            "进入 load_task_graph_* 的函数体，开始把主存输入窗口的样本搬进 task stream，并同步本轮事务长度。",
        ),
        (
            "compute_task_graph_",
            "进入 compute_task_graph_* 的函数体，开始消费 task stream 的样本并把递增结果送入 result stream。",
        ),
        (
            "store_task_graph_",
            "进入 store_task_graph_* 的函数体，开始从 result stream 逐项取回样本并写回输出窗口。",
        ),
        ("seed_task_graph_", "进入 seed_task_graph_*_counts 的函数体，开始播种这次事务的长度 token。"),
        (
            "read_task_graph_",
            "进入 read_task_graph_* 的函数体，开始消费输入 token 并把样本与长度边界同时传给 compute actor。",
        ),
        (
            "write_task_graph_",
            "进入 write_task_graph_* 的函数体，开始在 write_count_stream 限定下把 result stream token 顺序送往输出边界。",
        ),
    ):

        # 这条闭合签名已经锁定到某个 task_graph helper 前缀，下面直接交付对应的函数体入口说明。
        if str_function_name.startswith(str_name_prefix):

            # 当前闭合行已经明确属于 task_graph helper 的入口边界，直接返回对应说明。
            return str_comment_text

    # 其他签名闭合行在这里不补说明。
    return ""

# 判断当前代码片段是否属于可重写的函数签名内容。
def is_function_signature_fragment(str_code: str) -> bool:
    """判断当前代码片段是否属于可重写的函数签名内容。

    参数:
        str_code: 当前待判断的净代码片段，dtype=str，unit=code text。

    返回:
        命中函数头、参数行或签名闭合行时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 先统一规整代码文本，避免首尾空白影响签名片段判断。
    str_stripped_code = str_code.strip()  # 当前代码片段的规整文本

    # 只把函数头、参数行和 `) {` 收口成签名片段。
    return str_stripped_code.startswith(
        (
            "static ",
            "const ",
            "hls::stream<",
            "ap_",
            "int ",
        )
    ) or str_stripped_code == ") {"

# 判断当前候选片段是否真的还处在最近函数的多行签名范围内，避免把函数体里的局部声明误判成签名参数。
def is_active_signature_fragment(
    list_lines: list[str],
    int_code_index: int,
    str_code: str,
) -> bool:
    """判断当前候选片段是否真的属于最近函数的签名范围。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前代码所在的零基行号，dtype=int，unit=line index。
        str_code: 当前待判断的净代码文本，dtype=str，unit=code text。

    返回:
        当前片段仍位于函数签名区域时返回 True，否则返回 False，dtype=bool，unit=flag。
    """

    # 当前代码如果连签名片段候选都不是，就不可能属于函数签名。
    if not is_function_signature_fragment(str_code):

        # 非签名候选直接回退 False。
        return False

    # 函数头和签名闭合行本身天然属于有效签名片段。
    if str_code.strip().startswith("static ") or str_code.strip() == ") {":

        # 直接返回 True，允许函数头和 `) {` 继续走签名改写规则。
        return True

    # 从当前片段向上回溯；如果先撞到 `) {`，说明已经进入函数体内部，当前片段并不属于签名。
    for int_scan_index in range(int_code_index - 1, -1, -1):

        # 读取回溯位置去尾注后的净代码文本，供签名边界判断复用。
        str_scan_code = code_part(list_lines[int_scan_index]).strip()  # 当前回溯行的净代码文本

        # 空行和注释专用行不提供语义边界，继续向上看真正的代码。
        if not str_scan_code:

            # 当前行只承担分隔作用。
            continue

        # 先碰到签名闭合行说明当前代码已经位于函数体内部，不再算签名片段。
        if str_scan_code == ") {":

            # 当前片段落在函数体而不是多行签名里。
            return False

        # 一旦回溯到真正的函数头，说明当前片段仍位于这段多行签名之中。
        if re.search(r"([A-Za-z_]\w*)\s*\($", str_scan_code) and not str_scan_code.startswith(
            ("if ", "for ", "while ", "switch ")
        ):

            # 命中最近函数头后，确认当前片段确实属于该签名范围。
            return True

        # 遇到前一个函数体闭合说明已经越界，不应继续把当前片段当作签名内容。
        if str_scan_code == "}":

            # 当前片段没有落在任何活跃的多行签名范围里。
            return False

    # 回溯到文件头仍未确认签名范围时，保守回退为 False。
    return False

# 为 helper 多行签名里的参数尾注生成与摘要不同的具体说明。
def function_signature_inline_comment_text(
    list_lines: list[str],
    int_code_index: int,
    str_code: str,
) -> str:
    """为 helper 多行签名里的参数尾注生成与摘要不同的具体说明。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        int_code_index: 当前绑定代码所在的零基行号，dtype=int，unit=line index。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中签名尾注规则时返回具体说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 非签名片段不参与当前尾注改写。
    if not is_function_signature_fragment(str_code):

        # 非签名代码继续走普通 inline comment 分支。
        return ""

    # 函数体里的局部声明虽然可能以 `int` 或 `ap_` 开头，但不应该误判成签名尾注。
    if not is_active_signature_fragment(list_lines, int_code_index, str_code):

        # 当前候选不在真实的签名范围内，跳过签名尾注改写。
        return ""

    # 回溯当前参数片段所属的 helper，供不同签名尾注角色复用。
    str_function_name = enclosing_signature_function_name(list_lines, int_code_index)  # 当前签名尾注所属的 helper 名称

    # block/dataflow helper 的签名尾注先按显式参数角色收口，避免继续回退成统一 buffer 模板。
    for str_expected_name, tuple_needles, str_comment_text in signature_generic_flow_inline_rules():

        # 当前尾注片段只有同时命中 helper 名称和参数关键字时，才返回对应的第二观察面说明。
        if str_function_name == str_expected_name and all(
            str_needle in str_code for str_needle in tuple_needles
        ):

            # 返回当前通用 helper 参数的签名尾注说明。
            return str_comment_text

    # 依次尝试 FIFO/通道尾注和长度边界尾注，命中首个结果后立即返回。
    for str_comment_text in (
        signature_fir_dataflow_inline_comment_text(str_function_name, str_code),
        signature_inline_channel_comment_text(str_function_name, str_code),
        signature_inline_length_comment_text(str_function_name, str_code),
    ):

        # 当前尾注片段一旦已经绑定到具体 helper 角色，就不再继续回退到更宽泛的规则。
        if str_comment_text:

            # 返回首个命中的签名尾注说明。
            return str_comment_text

    # 其他签名尾注不在这里强制改写。
    return ""

# 为 helper 多行签名里的 FIFO 和 stream 参数尾注生成专属说明。
def signature_inline_channel_comment_text(str_function_name: str, str_code: str) -> str:
    """为 helper 多行签名里的 FIFO 和 stream 参数尾注生成专属说明。

    参数:
        str_function_name: 当前签名片段所属的 helper 名称，dtype=str，unit=function name。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中通道尾注规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # matmul helper 的签名尾注要按 A/B/output FIFO 的消费或生产角色拆开说明。
    for str_expected_name, tuple_needles, str_comment_text in (
        ("load_matmul_a", ("stream_a_stream",), "A 路加载 helper 会把读出的左操作数顺序压进这条 stream。"),
        ("load_matmul_b", ("stream_b_stream",), "右操作数预取 helper 会把配对加数按列顺序压进这条 stream。"),
        ("compute_matmul_tile", ("stream_a_stream",), "tile 计算阶段会从这条 stream 逐 lane 取出 A 路 token。"),
        ("compute_matmul_tile", ("stream_b_stream",), "tile 计算阶段会从这条 stream 逐 lane 取出右侧配对加数 token。"),
        ("compute_matmul_tile", ("stream_out_stream",), "逐 lane 求和结果会先压进这条输出 stream。"),
        ("store_matmul", ("stream_out_stream",), "写回 helper 会从这条输出 stream 逐项取回求和结果。"),
    ):

        # matmul 签名尾注只有同时命中 helper 名称和参数关键字时才成立。
        if str_function_name == str_expected_name and all(
            str_needle in str_code for str_needle in tuple_needles
        ):

            # 返回当前 matmul 通道参数的签名尾注说明。
            return str_comment_text

    # task_graph helper 的签名尾注需要把样本 stream、结果 stream 和 count stream 分开表达。
    for str_name_prefix, tuple_needles, str_comment_text in (
        ("load_task_graph_", ("stream_task_stream",), "load helper 会把主存样本逐拍压进这条 task stream。"),
        ("load_task_graph_", ("stream_count_stream",), "这条 count stream 只负责把事务长度 token 交给下游 compute helper。"),
        ("compute_task_graph_", ("stream_task_stream",), "计算 helper 会从这条 task stream 逐拍领取待递增样本。"),
        ("compute_task_graph_", ("stream_task_result_stream",), "递增后的样本会立刻通过这条 result stream 送往写回阶段。"),
        ("compute_task_graph_", ("stream_count_stream",), "当前 compute helper 会先从这条 count stream 取回本轮事务长度。"),
        ("store_task_graph_", ("stream_task_result_stream",), "写回 helper 会从这条 result stream 逐拍取出输出样本。"),
        ("seed_task_graph_", ("read_count_stream",), "这条 read_count_stream 只播种一次事务长度 token。"),
        ("read_task_graph_", ("stream_task_stream",), "read actor 会把上游 token 先送进这条 task stream。"),
        ("read_task_graph_", ("read_count_stream",), "read actor 会先从这里读出本轮事务长度。"),
        ("read_task_graph_", ("compute_count_stream",), "读取完成后的长度 token 会接着送给 compute actor。"),
        ("compute_task_graph_", ("compute_count_stream",), "compute actor 会先从这里取回本轮事务长度。"),
        ("compute_task_graph_", ("write_count_stream",), "compute actor 会把用过的长度 token 再转交给 write actor。"),
        ("write_task_graph_", ("write_count_stream",), "write actor 依赖这条 count stream 保证输出 token 数不漂移。"),
    ):

        # task_graph 签名尾注用稳定前缀匹配 helper，再用参数关键字确认具体通道。
        if str_function_name.startswith(str_name_prefix) and all(
            str_needle in str_code for str_needle in tuple_needles
        ):

            # 当前签名尾注已经锁定到某条 task_graph 通道角色，直接返回对应的短说明。
            return str_comment_text

    # 其他签名尾注不在这里解释。
    return ""

# 为 helper 多行签名里的长度参数尾注生成具体边界说明。
def signature_inline_length_comment_text(str_function_name: str, str_code: str) -> str:
    """为 helper 多行签名里的长度参数尾注生成具体边界说明。

    参数:
        str_function_name: 当前签名片段所属的 helper 名称，dtype=str，unit=function name。
        str_code: 当前签名片段的净代码文本，dtype=str，unit=code text。

    返回:
        命中长度尾注规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 非长度参数片段不需要在这里补充边界尾注。
    if "int_length" not in str_code:

        # 当前签名片段没有涉及事务长度边界。
        return ""

    # matmul helper 的长度尾注要分别绑定到读取、补零和写回边界。
    for str_expected_name, str_comment_text in (
        ("load_matmul_a", "A 路读取只覆盖这次事务的有效长度。"),
        ("load_matmul_b", "右侧配对窗口只读取这次事务真正覆盖的列数。"),
        ("compute_matmul_tile", "尾块是否补零由这个总长度决定。"),
        ("store_matmul", "只回写与有效长度对应的结果样本。"),
    ):

        # matmul helper 一旦命中具体名称，就返回对应的长度边界尾注。
        if str_function_name == str_expected_name:

            # 返回当前 matmul 长度参数的尾注说明。
            return str_comment_text

    # task_graph memory helper 的长度尾注要说明只覆盖本轮事务的样本窗口。
    for str_name_prefix, str_comment_text in (
        ("load_task_graph_", "只读取这次事务长度覆盖到的输入样本。"),
        ("store_task_graph_", "只写回这次事务长度覆盖到的输出样本。"),
    ):

        # task_graph memory helper 用稳定前缀即可区分 load/store 两种边界语义。
        if str_function_name.startswith(str_name_prefix):

            # 当前长度参数已经锁定到 task_graph 的 memory helper，直接返回对应边界说明。
            return str_comment_text

    # 其他长度参数不在这里强制改写。
    return ""

# 记录每个函数签名从起始行到结束行的范围，供多行签名整体跳过复用。
def function_signature_ranges(
    list_lines: list[str],
    list_functions: list[Any],
) -> dict[int, int]:
    """记录每个函数签名从起始行到结束行的范围。

    参数:
        list_lines: 当前 source 的物理行列表，dtype=list[str]，unit=source lines。
        list_functions: 轻量解析器识别出的函数列表，dtype=list[Any]，unit=function info list。

    返回:
        函数签名起始行到结束行的一基行号映射，dtype=dict[int, int]，unit=line range map。
    """

    # 初始化签名范围映射，后续逐个函数写入起止行号。
    dict_ranges: dict[int, int] = {}  # 函数签名起止行范围映射表

    # 逐个函数扫描其签名结束行，兼容多行签名写法。
    for function_info in list_functions:

        # 当前函数签名起始行先作为扫描基线。
        int_start_line = function_info.signature_start_line  # 当前函数签名的起始行号

        # 当前扫描游标从签名起始行出发，向下定位真正的结束行。
        int_end_line = int_start_line  # 当前函数签名的结束行号扫描游标

        # 逐行向下扫描，直到命中 `{` 或 `;` 为止。
        while int_end_line <= len(list_lines):

            # 读取当前扫描行的净代码文本，判断签名是否已经结束。
            str_line = code_part(list_lines[int_end_line - 1]).strip()  # 当前签名扫描行的净代码文本

            # 命中左花括号或分号时，说明当前签名已经完整闭合。
            if str_line.endswith("{") or str_line.endswith(";"):

                # 扫描到签名闭合点后立即结束当前函数的向下搜索。
                break

            # 继续向下扫描下一行，兼容多行签名。
            int_end_line += 1  # 当前函数签名仍未闭合，扫描游标继续向下推进一行。

        # 记录当前函数签名的起止行范围。
        dict_ranges[int_start_line] = int_end_line  # 当前函数签名的一基起止行号映射。

    # 返回完整的签名起止行映射表。
    return dict_ranges
