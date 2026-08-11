"""收拢 mock HLS source 主体 block 的模式专用模板逻辑。"""

# 启用延迟求值注解，避免类型提示在导入阶段提前展开。
from __future__ import annotations

# 宽泛类型提示和 pattern 识别工具负责支撑 source body 模板分派。
from typing import Any
from .mock_vectors import _example_pattern

# 根模块继续提供 source body 共享的参数视图、接口判断和注释渲染工具。
from .mock_hls_artifacts import (
    _argument_lookup,
    _argument_storage_type,
    _board_source_spec,
    _requires_dataflow_pragma,
    _requires_partition_pragma,
    _stream_payload_type,
)

# 生成单输入 memory 版 task_graph 主体，避免主分发函数里堆叠长字符串行。
def _mock_hls_memory_task_graph_body(str_kernel_name: str) -> str:
    """渲染单输入 memory 接口的 task_graph 主体。

    参数:
        str_kernel_name: 当前 mock kernel 的函数名前缀。

    返回:
        适用于 `input/output/length` 接口的 task_graph 主体文本。
    """

    # 返回 memory task_graph 场景的 actor 串接骨架。
    return "\n".join(
        (
            "  hls::stream<ap_uint<32> > task_stream;",
            "  hls::stream<ap_uint<32> > task_result_stream;",
            "  hls::stream<int> task_count_stream;",
            "  #pragma HLS STREAM variable=task_stream depth=16",
            "  #pragma HLS STREAM variable=task_result_stream depth=16",
            "  #pragma HLS STREAM variable=task_count_stream depth=2",
            f"  load_{str_kernel_name}(input, task_stream, task_count_stream, length);",
            (
                "  hls::task compute_stage("
                f"compute_{str_kernel_name}, task_stream, "
                "task_result_stream, task_count_stream);"
            ),
            f"  store_{str_kernel_name}(task_result_stream, output, length);",
        )
    )

# 生成带 scale 参数的单输入主体，把常见局部缓冲和顺序缩放模板集中收口。
def _mock_hls_scaled_memory_body(
    dict_arguments: dict[str, dict[str, Any]],
    str_pattern: str,
) -> str:
    """渲染带 `scale` 参数的单输入单输出主体。

    参数:
        dict_arguments: 以参数名索引的参数配置映射。
        str_pattern: 当前 spec 命中的示例模式名称。

    返回:
        与 `input/output/length/scale` 组合匹配的主体文本。
    """

    # 遇到 array_partition 时，切到显式局部分块缓冲模板。
    if str_pattern == "array_partition":

        # 为局部 tile 缓冲推导元素类型，保证读写类型和输入端口一致。
        str_partition_value_type = _argument_storage_type(dict_arguments["input"])  # array_partition tile 缓冲的元素类型

        # 返回 array_partition 缩放场景的局部并行缓冲主体。
        return f"""  {str_partition_value_type} local_buf[16];
  // Local partition exposes parallel element access inside each tile.
  #pragma HLS ARRAY_PARTITION variable=local_buf complete dim=1
  for (int base = 0; base < length; base += 16) {{
    int chunk = (length - base < 16) ? (length - base) : 16;
    for (int j = 0; j < 16; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        local_buf[j] = input[base + j];
      }}
    }}
    for (int j = 0; j < 16; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        output[base + j] = local_buf[j] * scale;
      }}
    }}
  }}"""

    # 命中 array_reshape 时，保留重排缓冲对访问位宽的显式表达。
    if str_pattern == "array_reshape":

        # 为重排缓冲挑选元素类型，确保模板继续复用输入存储位宽。
        str_reshape_value_type = _argument_storage_type(dict_arguments["input"])  # array_reshape 宽访存缓冲的元素类型

        # 返回 array_reshape 缩放场景的局部重排主体。
        return f"""  {str_reshape_value_type} wide_buf[16];
  // Local reshape widens adjacent element access without also partitioning the buffer.
  #pragma HLS ARRAY_RESHAPE variable=wide_buf complete dim=1
  for (int base = 0; base < length; base += 16) {{
    int chunk = (length - base < 16) ? (length - base) : 16;
    for (int j = 0; j < 16; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        wide_buf[j] = input[base + j];
      }}
    }}
    for (int j = 0; j < 16; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        output[base + j] = wide_buf[j] * scale;
      }}
    }}
  }}"""

    # axi4_burst 只需要最小顺序访存骨架，不再额外引入局部缓冲。
    if str_pattern == "axi4_burst":

        # 返回顺序访存的缩放主体，保持 burst 访问语义直观。
        return "  for (int i = 0; i < length; ++i) {\n    output[i] = input[i] * scale;\n  }"

    # 其他带 scale 的单输入场景统一回退到逐元素乘法骨架。
    return "  for (int i = 0; i < length; ++i) {\n    output[i] = input[i] * scale;\n  }"

# 处理双输入单输出 memory 接口，把二元向量和局部 tile 模板从主分发函数里拆出来。
def _mock_hls_dual_memory_body(
    spec: dict[str, Any],
    dict_arguments: dict[str, dict[str, Any]],
    set_argument_names: set[str],
    str_pattern: str,
) -> str | None:
    """渲染双输入单输出 memory-mapped 场景的主体。

    参数:
        spec: 描述 mock HLS 接口、模式、pragma 与板卡约束的规范字典。
        dict_arguments: 以参数名索引的参数配置映射。
        set_argument_names: 当前 spec 暴露的参数名集合。
        str_pattern: 当前 spec 命中的示例模式名称。

    返回:
        命中双输入 memory 接口时返回对应主体文本；否则返回 `None`。
    """

    # 当前参数组合不是双输入 memory 接口时，把处理机会交给后续 helper。
    if not {"input_a", "input_b", "output", "length"}.issubset(set_argument_names):

        # 返回空值，表示本 helper 不负责当前接口形态。
        return None

    # matmul 明确要求 DATAFLOW 时，优先转到已经准备好的流式阶段骨架。
    if str_pattern == "matmul" and _requires_dataflow_pragma(spec):

        # 交付 DATAFLOW matmul 所需的 stream 串接主体。
        return """  hls::stream<ap_uint<32> > a_stream;
  hls::stream<ap_uint<32> > b_stream;
  hls::stream<ap_uint<32> > out_stream;
  #pragma HLS STREAM variable=a_stream depth=16
  #pragma HLS STREAM variable=b_stream depth=16
  #pragma HLS STREAM variable=out_stream depth=16
  load_matmul_a(input_a, a_stream, length);
  load_matmul_b(input_b, b_stream, length);
  compute_matmul_tile(a_stream, b_stream, out_stream, length);
  store_matmul(out_stream, output, length);"""

    # matmul 显式要求 tile 分区时，切到局部缓冲版主体。
    if str_pattern == "matmul" and (
        _requires_partition_pragma(spec, "tile_a")
        or _requires_partition_pragma(spec, "tile_b")
    ):

        # 为 tile_a 与 tile_b 统一推导元素类型，减少模板里的重复表达式。
        str_tile_value_type = _argument_storage_type(dict_arguments["input_a"])  # matmul tile_a 与 tile_b 共用的元素类型

        # 交付带 ARRAY_PARTITION 的分块 matmul 主体。
        return f"""  {str_tile_value_type} tile_a[4];
  {str_tile_value_type} tile_b[4];
  #pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1
  #pragma HLS ARRAY_PARTITION variable=tile_b complete dim=1
  for (int base = 0; base < length; base += 4) {{
    int chunk = (length - base < 4) ? (length - base) : 4;
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL
      tile_a[j] = (j < chunk) ? input_a[base + j] : {str_tile_value_type}(0);
      tile_b[j] = (j < chunk) ? input_b[base + j] : {str_tile_value_type}(0);
    }}
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        output[base + j] = tile_a[j] + tile_b[j];
      }}
    }}
  }}"""

    # tiled_gemm 继续保留乘法版 tile 缓冲骨架。
    if str_pattern == "tiled_gemm":

        # 为 GEMM 的局部 tile 选择元素类型，避免模板写死成固定位宽。
        str_gemm_value_type = _argument_storage_type(dict_arguments["input_a"])  # tiled_gemm 局部乘法 tile 的元素类型

        # 交付 tiled_gemm 场景的局部乘法主体。
        return f"""  {str_gemm_value_type} tile_a[4];
  {str_gemm_value_type} tile_b[4];
  #pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1
  #pragma HLS ARRAY_PARTITION variable=tile_b complete dim=1
  for (int base = 0; base < length; base += 4) {{
    int chunk = (length - base < 4) ? (length - base) : 4;
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL
      tile_a[j] = (j < chunk) ? input_a[base + j] : {str_gemm_value_type}(0);
      tile_b[j] = (j < chunk) ? input_b[base + j] : {str_gemm_value_type}(0);
    }}
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        output[base + j] = tile_a[j] * tile_b[j];
      }}
    }}
  }}"""

    # vector_lane 需要显式 lane buffer，保留并行 lane 的局部表达。
    if str_pattern == "vector_lane":

        # 为 lane buffer 锁定元素类型，保持读写两路输入的存储类型一致。
        str_lane_value_type = _argument_storage_type(dict_arguments["input_a"])  # vector_lane 四路 lane buffer 共用的元素类型

        # 交付 vector_lane 的并行 lane buffer 主体。
        return f"""  {str_lane_value_type} lane_buf_a[4];
  {str_lane_value_type} lane_buf_b[4];
  #pragma HLS ARRAY_PARTITION variable=lane_buf_a complete dim=1
  #pragma HLS ARRAY_PARTITION variable=lane_buf_b complete dim=1
  for (int base = 0; base < length; base += 4) {{
    int chunk = (length - base < 4) ? (length - base) : 4;
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL factor=4
      lane_buf_a[j] = (j < chunk) ? input_a[base + j] : {str_lane_value_type}(0);
      lane_buf_b[j] = (j < chunk) ? input_b[base + j] : {str_lane_value_type}(0);
    }}
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL factor=4
      if (j < chunk) {{
        output[base + j] = lane_buf_a[j] + lane_buf_b[j];
      }}
    }}
  }}"""

    # fence_ordering 只需要把顺序写回关系显式保留下来。
    if str_pattern == "fence_ordering":

        # 返回顺序写回约束更清晰的 fence_ordering 主体。
        return """  for (int i = 0; i < length; ++i) {
    ap_uint<32> ordered_writeback = input_a[i] + input_b[i];
    output[i] = ordered_writeback;
  }"""

    # 剩余双输入 memory 场景统一回退到逐元素加法骨架。
    return "  for (int i = 0; i < length; ++i) {\n    output[i] = input_a[i] + input_b[i];\n  }"

# 处理单输入单输出 memory 接口，把板卡验收、scale 和 stencil 等分支从主分发函数里拆出来。
def _mock_hls_single_memory_body(
    spec: dict[str, Any],
    dict_arguments: dict[str, dict[str, Any]],
    set_argument_names: set[str],
    str_pattern: str,
) -> str | None:
    """渲染单输入单输出 memory-mapped 场景的主体。

    参数:
        spec: 描述 mock HLS 接口、模式、pragma 与板卡约束的规范字典。
        dict_arguments: 以参数名索引的参数配置映射。
        set_argument_names: 当前 spec 暴露的参数名集合。
        str_pattern: 当前 spec 命中的示例模式名称。

    返回:
        命中单输入 memory 接口时返回对应主体文本；否则返回 `None`。
    """

    # 当前参数组合不是单输入 memory 接口时，直接退出本 helper。
    if not {"input", "output", "length"}.issubset(set_argument_names):

        # 返回空值，让其他 helper 继续判断更合适的接口模板。
        return None

    # 先取出板卡验收来源标识，供后续两个 board 专用分支复用。
    str_board_source_spec = _board_source_spec(spec)  # board_acceptance 中声明的 source_spec 标识

    # 板卡验收的 FIR/CORDIC 场景需要显式 stream 管线骨架。
    if str_board_source_spec and str_pattern in {"fir", "cordic"}:

        # 返回 FIR/CORDIC 板卡验收场景的 stream 管线主体。
        return """  hls::stream<ap_uint<32> > load_stream;
  hls::stream<ap_uint<32> > result_stream;
  #pragma HLS STREAM variable=load_stream depth=16
  #pragma HLS STREAM variable=result_stream depth=16
  for (int i = 0; i < length; ++i) {
    #pragma HLS PIPELINE II=1
    load_stream.write(input[i]);
  }
  for (int i = 0; i < length; ++i) {
    #pragma HLS PIPELINE II=1
    ap_uint<32> token = load_stream.read();
    result_stream.write(token + 1);
  }
  for (int i = 0; i < length; ++i) {
    #pragma HLS PIPELINE II=1
    output[i] = result_stream.read();
  }"""

    # 板卡验收的 rle_axis 需要带包字段的 memory-to-stream 包装主体。
    if str_board_source_spec and str_pattern == "rle_axis":

        # 返回带 AXIS 包字段的 rle_axis 板卡验收主体。
        return """  // Wrapper byte-packet type contract keeps the memory-to-stream ingress reviewable.
  struct axis_byte_t {
    ap_uint<8> data;
    ap_uint<1> last;
    ap_uint<1> keep;
    ap_uint<1> strb;
  };
  // Wrapper word-packet type contract keeps the stream-to-memory egress reviewable.
  struct axis_word_t {
    ap_uint<16> data;
    ap_uint<1> last;
    ap_uint<2> keep;
    ap_uint<2> strb;
  };
  // AXIS compatibility note: this wrapper mirrors
  // ap_axiu<8,0,0,0> and ap_axiu<16,0,0,0> keep/strb/last handling at the memory boundary.
  hls::stream<axis_byte_t> in_stream;
  hls::stream<axis_word_t> out_stream;
  #pragma HLS STREAM variable=in_stream depth=16
  #pragma HLS STREAM variable=out_stream depth=16
  for (int i = 0; i < length; ++i) {
    #pragma HLS PIPELINE II=1
    axis_byte_t in_pkt;
    in_pkt.data = input[i];
    in_pkt.keep = -1;
    in_pkt.strb = -1;
    in_pkt.last = (i == length - 1) ? 1 : 0;
    in_stream.write(in_pkt);
  }
  for (int i = 0; i < length; ++i) {
    #pragma HLS PIPELINE II=1
    axis_byte_t in_pkt = in_stream.read();
    axis_word_t out_pkt;
    out_pkt.data = in_pkt.data + 1;
    out_pkt.keep = -1;
    out_pkt.strb = -1;
    out_pkt.last = in_pkt.last;
    out_stream.write(out_pkt);
  }
  for (int i = 0; i < length; ++i) {
    #pragma HLS PIPELINE II=1
    axis_word_t out_pkt = out_stream.read();
    output[i] = out_pkt.data;
  }"""

    # task_graph 的 memory 接口要显式串起 load、compute 和 store actor。
    if str_pattern == "task_graph":

        # 先锁定 helper 函数名使用的 kernel 前缀，避免后续模板重复访问 spec。
        str_kernel_name = str(spec.get("name") or "kernel")  # task_graph memory 主体使用的 kernel 前缀

        # 返回单输入 memory 版 task_graph 主体。
        return _mock_hls_memory_task_graph_body(str_kernel_name)

    # 带 scale 参数时，继续交给缩放专用 helper 选择更细模板。
    if "scale" in set_argument_names:

        # 返回带 scale 的单输入主体，包含局部缓冲和顺序访存两类模板。
        return _mock_hls_scaled_memory_body(dict_arguments, str_pattern)

    # line_buffer_stencil 需要显式 3 点邻域缓冲，保留边界回退逻辑。
    if str_pattern == "line_buffer_stencil":

        # 为邻域 line buffer 推导元素类型，保证缓冲读写与输入端口兼容。
        str_stencil_value_type = _argument_storage_type(dict_arguments["input"])  # stencil 三点邻域缓冲的元素类型

        # 返回 line_buffer_stencil 的局部邻域缓冲主体。
        return f"""  {str_stencil_value_type} line_buf[3];
  #pragma HLS ARRAY_PARTITION variable=line_buf complete dim=1
  for (int i = 0; i < length; ++i) {{
    line_buf[0] = input[(i == 0) ? 0 : (i - 1)];
    line_buf[1] = input[i];
    line_buf[2] = input[(i + 1 < length) ? (i + 1) : (length - 1)];
    output[i] = line_buf[0] + line_buf[1] + line_buf[2];
  }}"""

    # reduction_tree 继续保留 4 路部分和归并骨架。
    if str_pattern == "reduction_tree":

        # 返回 reduction_tree 场景的树形归并主体。
        return """  ap_uint<32> tree_accum = 0;
  for (int i = 0; i < length; i += 4) {
    #pragma HLS UNROLL factor=4
    ap_uint<32> partial0 = (i + 0 < length) ? input[i + 0] : ap_uint<32>(0);
    ap_uint<32> partial1 = (i + 1 < length) ? input[i + 1] : ap_uint<32>(0);
    ap_uint<32> partial2 = (i + 2 < length) ? input[i + 2] : ap_uint<32>(0);
    ap_uint<32> partial3 = (i + 3 < length) ? input[i + 3] : ap_uint<32>(0);
    tree_accum += (partial0 + partial1) + (partial2 + partial3);
  }
  output[0] = tree_accum;"""

    # host_kernel_split 只保留最小顺序自增骨架，便于 host/kernel 边界验收。
    if str_pattern == "host_kernel_split":

        # 返回 host_kernel_split 场景的顺序自增主体。
        return "  for (int i = 0; i < length; ++i) {\n    output[i] = input[i] + 1;\n  }"

    # fft 模式保留固定 twiddle 骨架，方便 mock 侧表达周期访问。
    if str_pattern == "fft":

        # 返回 fft 场景的固定 twiddle 访问主体。
        return """  ap_uint<32> twiddle[4] = {1, 1, 1, 1};
  #pragma HLS ARRAY_PARTITION variable=twiddle complete dim=1
  for (int i = 0; i < length; ++i) {
    output[i] = input[i] + twiddle[i & 3];
  }"""

    # 其他普通单输入 memory 场景统一回退到逐元素自增骨架。
    return "  for (int i = 0; i < length; ++i) {\n    output[i] = input[i] + 1;\n  }"

# 处理成对 stream 接口，把 RLE、task graph 和 block stream 的骨架从主分发函数里拆出来。
def _mock_hls_stream_pair_body(
    spec: dict[str, Any],
    dict_arguments: dict[str, dict[str, Any]],
    set_argument_names: set[str],
    str_pattern: str,
) -> str | None:
    """渲染 `in_stream/out_stream` 场景的主体。

    参数:
        spec: 描述 mock HLS 接口、模式、pragma 与板卡约束的规范字典。
        dict_arguments: 以参数名索引的参数配置映射。
        set_argument_names: 当前 spec 暴露的参数名集合。
        str_pattern: 当前 spec 命中的示例模式名称。

    返回:
        命中成对 stream 接口时返回对应主体文本；否则返回 `None`。
    """

    # 当前接口不同时具备输入流和输出流时，交给其他 helper 继续判断。
    if not {"in_stream", "out_stream"}.issubset(set_argument_names):

        # 返回空值，表示本 helper 不接手当前接口形态。
        return None

    # rle_axis 的流接口需要保留输入输出 payload 类型的显式声明。
    if str_pattern == "rle_axis" and "length" in set_argument_names:

        # 先提取输入流 payload 类型，保证生成包变量与接口模板一致。
        str_input_payload_type = _stream_payload_type(dict_arguments["in_stream"])  # rle_axis 输入流包承载的数据 payload 类型

        # 再提取输出流 payload 类型，避免输出包变量退化为固定类型。
        str_output_payload_type = _stream_payload_type(dict_arguments["out_stream"])  # rle_axis 输出流包写回的数据 payload 类型

        # 返回 rle_axis 流接口的读包、加工与写包主体。
        return f"""  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    {str_input_payload_type} in_pkt = in_stream.read();
    {str_output_payload_type} out_pkt;
    out_pkt.data = in_pkt.data + 1;
    out_pkt.keep = -1;
    out_pkt.strb = -1;
    out_pkt.last = (i == length - 1) ? 1 : 0;
    out_stream.write(out_pkt);
  }}"""

    # dataflow 和 task_graph 的流接口都要先准备中间通道与 helper 名称。
    if str_pattern in {"dataflow", "task_graph"} and "length" in set_argument_names:

        # 为当前流式 kernel 固定 helper 名使用的前缀，避免后续模板重复访问 spec。
        str_kernel_name = str(spec.get("name") or "kernel")  # 流式 helper 函数名中的 kernel 前缀

        # 为输入到计算阶段之间的中间通道选取变量名。
        str_mid_stream_name = "task_stream" if str_pattern == "task_graph" else "mid_stream"  # read 阶段吐出的中间流变量名

        # 为计算结果通道准备名称，让 task_graph 与 dataflow 模式复用一套模板。
        str_result_stream_name = "task_result_stream" if str_pattern == "task_graph" else "result_stream"  # write 阶段消费的结果流变量名

        # task_graph 需要 read/compute/write 三个 task actor 串接。
        if str_pattern == "task_graph":

            # 返回 task_graph 流接口的多 task 主体。
            return "\n".join(
                (
                    f"  hls::stream<ap_uint<32> > {str_mid_stream_name};",
                    f"  hls::stream<ap_uint<32> > {str_result_stream_name};",
                    "  hls::stream<int> read_count_stream;",
                    "  hls::stream<int> compute_count_stream;",
                    "  hls::stream<int> write_count_stream;",
                    f"  #pragma HLS STREAM variable={str_mid_stream_name} depth=16",
                    f"  #pragma HLS STREAM variable={str_result_stream_name} depth=16",
                    "  #pragma HLS STREAM variable=read_count_stream depth=2",
                    "  #pragma HLS STREAM variable=compute_count_stream depth=2",
                    "  #pragma HLS STREAM variable=write_count_stream depth=2",
                    f"  seed_{str_kernel_name}_counts(length, read_count_stream);",
                    (
                        "  hls::task read_stage("
                        f"read_{str_kernel_name}, in_stream, {str_mid_stream_name}, "
                        "read_count_stream, compute_count_stream);"
                    ),
                    (
                        "  hls::task compute_stage("
                        f"compute_{str_kernel_name}, {str_mid_stream_name}, "
                        f"{str_result_stream_name}, compute_count_stream, "
                        "write_count_stream);"
                    ),
                    (
                        "  hls::task write_stage("
                        f"write_{str_kernel_name}, {str_result_stream_name}, "
                        "out_stream, write_count_stream);"
                    ),
                )
        )

        # dataflow 流接口只需要 read/compute/write 的顺序 helper 串接。
        return "\n".join(
            (
                f"  hls::stream<ap_uint<32> > {str_mid_stream_name};",
                f"  hls::stream<ap_uint<32> > {str_result_stream_name};",
                f"  #pragma HLS STREAM variable={str_mid_stream_name} depth=16",
                f"  #pragma HLS STREAM variable={str_result_stream_name} depth=16",
                f"  read_{str_kernel_name}(in_stream, {str_mid_stream_name}, length);",
                (
                    f"  compute_{str_kernel_name}("
                    f"{str_mid_stream_name}, {str_result_stream_name}, length);"
                ),
                (
                    f"  write_{str_kernel_name}("
                    f"{str_result_stream_name}, out_stream, length);"
                ),
            )
        )

    # streamofblocks 继续保留显式块缓冲，方便表达块内并行与边界填零。
    if str_pattern == "streamofblocks" and "length" in set_argument_names:

        # 返回 block stream 场景的显式局部块缓冲主体。
        return """  ap_uint<32> block_buf[4];
  #pragma HLS ARRAY_PARTITION variable=block_buf complete dim=1
  for (int base = 0; base < length; base += 4) {
    #pragma HLS PIPELINE II=1
    int chunk = (length - base < 4) ? (length - base) : 4;
    for (int j = 0; j < 4; ++j) {
      #pragma HLS UNROLL factor=4
      block_buf[j] = (j < chunk) ? in_stream.read() : ap_uint<32>(0);
    }
    for (int j = 0; j < 4; ++j) {
      #pragma HLS UNROLL factor=4
      if (j < chunk) {
        out_stream.write(block_buf[j] + 1);
      }
    }
  }"""

    # 普通流接口只要声明了 length，就回退到顺序读写骨架。
    if "length" in set_argument_names:

        # 返回带长度控制的顺序流式主体。
        return "\n".join(
            (
                "  for (int i = 0; i < length; ++i) {",
                "    ap_uint<32> stream_sample = in_stream.read();",
                "    out_stream.write(stream_sample + 1);",
                "  }",
            )
        )

    # 没有 length 时，仅保留最小的空流判断与一次样本传递。
    return "\n".join(
        (
            "  if (!in_stream.empty()) {",
            "    ap_uint<32> stream_sample = in_stream.read();",
            "    out_stream.write(stream_sample + 1);",
            "  }",
        )
    )

# 按接口形态分发 mock HLS 主体渲染，保持主入口只负责路由而不堆叠细节模板。
def _mock_hls_body(spec: dict[str, Any]) -> str:
    """按接口形态与 pattern 选择 mock HLS 顶层函数主体。

    参数:
        spec: 描述 mock HLS 接口、模式、pragma 与板卡约束的规范字典。

    返回:
        可直接拼进 mock HLS `.cpp` 顶层函数中的主体文本。
    """

    # 先拿到按参数名索引的参数表，后续所有 helper 都直接复用这一份结构。
    dict_arguments = _argument_lookup(spec)  # 当前 mock HLS 主体使用的参数索引映射

    # 再把参数名压成集合，便于快速判断当前 spec 命中的接口形态。
    set_argument_names = set(dict_arguments)  # 当前 spec 暴露的参数名集合

    # 最后抽取 pattern 文本，让后续分发不再重复访问 spec 深层字段。
    str_pattern = _example_pattern(spec)  # 当前 mock HLS 主体对应的示例模式名

    # 先让双输入 memory helper 尝试接手，尽快排掉常见的二元向量场景。
    str_dual_memory_body = _mock_hls_dual_memory_body(spec, dict_arguments, set_argument_names, str_pattern)  # 双输入命中主体

    # 双输入 memory helper 已命中时，直接交付对应主体。
    if str_dual_memory_body is not None:

        # 把双输入 helper 已经选好的模板原样交回主渲染流程。
        return str_dual_memory_body

    # 接着尝试单输入 memory helper，把 board、scale 与 stencil 分支集中处理掉。
    str_single_memory_body = _mock_hls_single_memory_body(spec, dict_arguments, set_argument_names, str_pattern)  # 单输入命中主体

    # 单输入 memory helper 命中时，直接采用该主体。
    if str_single_memory_body is not None:

        # 把单输入 helper 确认过的模板直接交回主渲染流程。
        return str_single_memory_body

    # 二维块 dataflow 仍然使用独立 helper 函数骨架，主入口只保留路由判断。
    if str_pattern == "dataflow" and {"input", "output", "rows", "cols"}.issubset(
        set_argument_names
    ):

        # 返回二维 block dataflow 的 read/row/reorder/col/write 主体。
        return """  hls::stream<ap_uint<32> > read_stream;
  hls::stream<ap_uint<32> > row_stream;
  hls::stream<ap_uint<32> > reorder_stream;
  hls::stream<ap_uint<32> > col_stream;
  #pragma HLS STREAM variable=read_stream depth=16
  #pragma HLS STREAM variable=row_stream depth=16
  #pragma HLS STREAM variable=reorder_stream depth=16
  #pragma HLS STREAM variable=col_stream depth=16
  read_block(input, read_stream, rows, cols);
  row_pass(read_stream, row_stream, rows, cols);
  transpose_or_reorder(row_stream, reorder_stream, rows, cols);
  col_pass(reorder_stream, col_stream, rows, cols);
  write_block(col_stream, output, rows, cols);"""

    # 最后再让 stream pair helper 处理流接口和 block-stream 这类场景。
    str_stream_pair_body = _mock_hls_stream_pair_body(spec, dict_arguments, set_argument_names, str_pattern)  # 流接口命中主体

    # 流接口 helper 命中时，直接返回对应主体。
    if str_stream_pair_body is not None:

        # 返回成对 stream 接口对应的主体文本。
        return str_stream_pair_body

    # 所有专门分支都未命中时，保留最小 fallback，保证生成顶层函数语法完整。
    return (
        "  // Mock fallback keeps the top function syntactically complete.\n"
        "  return;"
    )
