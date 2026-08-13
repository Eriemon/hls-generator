"""收拢 mock HLS helper block 片段的模板渲染逻辑。"""

# 启用延迟求值注解，避免类型提示在导入阶段提前展开。
from __future__ import annotations

# 根模块提供统一的 C++ 行注释渲染器，避免 helper block 重复拼接注释格式。
from .mock_hls_artifacts import _cpp_line_comment

# 渲染 matmul + DATAFLOW 场景的 helper 文本，避免主入口函数堆叠大块模板。
def _mock_matmul_dataflow_helpers(comment_language: str) -> str:
    """渲染 matmul DATAFLOW 场景的 helper 函数集合。

    参数:
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于双输入单输出 matmul 场景的 load、compute、store helper 文本。
    """

    # 返回 matmul DATAFLOW 场景的完整 helper 骨架。
    return f'''static void load_matmul_a(
  const ap_uint<32>* input_a,
  hls::stream<ap_uint<32> >& a_stream,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Load the first matrix operand into a dedicated "
    "dataflow stream.",
    "将第一路矩阵操作数加载到独立 "
    "dataflow stream。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    a_stream.write(input_a[i]);
  }}
}}

static void load_matmul_b(
  const ap_uint<32>* input_b,
  hls::stream<ap_uint<32> >& b_stream,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Load the second matrix operand into a dedicated "
    "dataflow stream.",
    "将第二路矩阵操作数加载到独立 "
    "dataflow stream。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    b_stream.write(input_b[i]);
  }}
}}

static void compute_matmul_tile(
  hls::stream<ap_uint<32> >& a_stream,
  hls::stream<ap_uint<32> >& b_stream,
  hls::stream<ap_uint<32> >& out_stream,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Compute stage keeps the blocked tile buffers local "
    "while DATAFLOW overlaps load and store.",
    "计算阶段保持分块 tile buffer 为局部资源，"
    "同时让 DATAFLOW 与加载、回写重叠。",
)}
  for (int base = 0; base < length; base += 4) {{
    ap_uint<32> tile_a[4];
    ap_uint<32> tile_b[4];
    #pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1
    #pragma HLS ARRAY_PARTITION variable=tile_b complete dim=1
    int chunk = (length - base < 4) ? (length - base) : 4;
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL
      tile_a[j] = (j < chunk) ? a_stream.read() : ap_uint<32>(0);
      tile_b[j] = (j < chunk) ? b_stream.read() : ap_uint<32>(0);
    }}
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        out_stream.write(tile_a[j] + tile_b[j]);
      }}
    }}
  }}
}}

static void store_matmul(
  hls::stream<ap_uint<32> >& out_stream,
  ap_uint<32>* output,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Store the computed tile outputs back to memory.",
    "将计算得到的 tile 输出回写到存储。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    output[i] = out_stream.read();
  }}
}}'''

# 渲染二维块 DATAFLOW 场景的 helper 文本，把 2D block 骨架拆到独立函数中。
def _mock_fir_dataflow_helpers(comment_language: str) -> str:
    """渲染 staged FIR DATAFLOW 场景的 read、compute、write helper。

    参数:
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于 staged FIR 场景的 read、compute、write helper 文本。
    """

    # 返回 FIR mock 的三阶段骨架，使顶层 DATAFLOW 有可重叠的函数边界。
    return f'''static void read_fir_dataflow(
  const ap_uint<32>* input,
  hls::stream<ap_uint<32> >& mid_stream,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Read stage transfers each confirmed input sample into the FIR stream.",
    "读取阶段把每个确认后的输入样本转入 FIR 中间流。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    mid_stream.write(input[i]);
  }}
}}

static void compute_fir_dataflow(
  hls::stream<ap_uint<32> >& mid_stream,
  hls::stream<ap_uint<32> >& result_stream,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Compute stage preserves the mock FIR sample mapping while DATAFLOW overlaps stages.",
    "计算阶段保持 mock FIR 样本映射，并让 DATAFLOW 重叠各阶段。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    ap_uint<32> fir_sample = mid_stream.read();
    result_stream.write(fir_sample + 1);
  }}
}}

static void write_fir_dataflow(
  hls::stream<ap_uint<32> >& result_stream,
  ap_uint<32>* output,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Write stage returns one result sample for every input sample.",
    "写出阶段为每个输入样本返回一个结果样本。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    output[i] = result_stream.read();
  }}
}}'''

# 渲染二维块 DATAFLOW 场景的 helper 函数集合。
def _mock_block_dataflow_helpers(comment_language: str) -> str:
    """渲染二维块 DATAFLOW 场景的 helper 函数集合。

    参数:
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于 `input/output/rows/cols` 场景的 read、row、reorder、col、write helper 文本。
    """

    # 返回二维块 DATAFLOW 场景的完整 helper 骨架。
    return f'''static void read_block(
  const ap_uint<32>* input,
  hls::stream<ap_uint<32> >& read_stream,
  int rows,
  int cols
) {{
{_cpp_line_comment(
    comment_language,
    "Read block isolates the flat memory walk before "
    "the row transform stage.",
    "read_block 在行变换前先隔离扁平存储读取。",
)}
  int total = rows * cols;
  for (int i = 0; i < total; ++i) {{
    #pragma HLS PIPELINE II=1
    read_stream.write(input[i]);
  }}
}}

static void row_pass(
  hls::stream<ap_uint<32> >& read_stream,
  hls::stream<ap_uint<32> >& row_stream,
  int rows,
  int cols
) {{
{_cpp_line_comment(
    comment_language,
    "Row pass models the first block-local transform "
    "stage under DATAFLOW.",
    "row_pass 模拟 DATAFLOW 下的第一段块内行变换。",
)}
  int total = rows * cols;
  for (int i = 0; i < total; ++i) {{
    #pragma HLS PIPELINE II=1
    row_stream.write(read_stream.read());
  }}
}}

static void transpose_or_reorder(
  hls::stream<ap_uint<32> >& row_stream,
  hls::stream<ap_uint<32> >& reorder_stream,
  int rows,
  int cols
) {{
{_cpp_line_comment(
    comment_language,
    "Transpose or reorder keeps the 2D block skeleton "
    "explicit even in the mock implementation.",
    "transpose_or_reorder 让二维块重排骨架在 "
    "mock 中仍保持显式。",
)}
  int total = rows * cols;
  for (int i = 0; i < total; ++i) {{
    #pragma HLS PIPELINE II=1
    reorder_stream.write(row_stream.read());
  }}
}}

static void col_pass(
  hls::stream<ap_uint<32> >& reorder_stream,
  hls::stream<ap_uint<32> >& col_stream,
  int rows,
  int cols
) {{
{_cpp_line_comment(
    comment_language,
    "Column pass models the second transform stage "
    "after the reorder boundary.",
    "col_pass 模拟重排边界后的第二段列变换。",
)}
  int total = rows * cols;
  for (int i = 0; i < total; ++i) {{
    #pragma HLS PIPELINE II=1
    ap_uint<32> stream_sample = reorder_stream.read();
    col_stream.write(stream_sample + 1);
  }}
}}

static void write_block(
  hls::stream<ap_uint<32> >& col_stream,
  ap_uint<32>* output,
  int rows,
  int cols
) {{
{_cpp_line_comment(
    comment_language,
    "Write block drains the transformed block back "
    "to flat memory.",
    "write_block 将变换后的块结果回写到扁平存储。",
)}
  int total = rows * cols;
  for (int i = 0; i < total; ++i) {{
    #pragma HLS PIPELINE II=1
    output[i] = col_stream.read();
  }}
}}'''

# 渲染 memory 输入输出版 task_graph helper，把长度同步逻辑集中复用。
def _mock_task_graph_memory_helpers(
    str_kernel_name: str,
    str_stream_name: str,
    str_result_stream_name: str,
    comment_language: str,
) -> str:
    """渲染 memory 输入输出版 task_graph helper 组合。

    参数:
        str_kernel_name: 当前 mock kernel 的函数名前缀。
        str_stream_name: load 到 compute 阶段之间使用的中间流名称。
        str_result_stream_name: compute 到 store 阶段之间使用的结果流名称。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于 `input/output/length` task_graph 场景的 helper 文本。
    """

    # 返回 memory 版 task_graph 需要的 load、compute、store helper。
    return f'''static void load_{str_kernel_name}(
  const ap_uint<32>* input,
  hls::stream<ap_uint<32> >& {str_stream_name},
  hls::stream<int>& count_stream,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Load stage captures the bounded transaction length before "
    "streaming memory data into the task actor.",
    "加载阶段先锁定有界事务长度，再把存储数据流送入 "
    "task actor。",
)}
  count_stream.write(length);
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    {str_stream_name}.write(input[i]);
  }}
}}

static void compute_{str_kernel_name}(
  hls::stream<ap_uint<32> >& {str_stream_name},
  hls::stream<ap_uint<32> >& {str_result_stream_name},
  hls::stream<int>& count_stream
) {{
{_cpp_line_comment(
    comment_language,
    "Compute actor consumes the streamed transaction count "
    "so hls::task remains stream-only.",
    "计算 actor 通过流式事务计数保持 hls::task 仅流参数约束。",
)}
  int length = count_stream.read();
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1 style=flp
    ap_uint<32> stream_sample = {str_stream_name}.read();
    {str_result_stream_name}.write(stream_sample + 1);
  }}
}}

static void store_{str_kernel_name}(
  hls::stream<ap_uint<32> >& {str_result_stream_name},
  ap_uint<32>* output,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Store stage drains the task result stream back to "
    "memory under the same bounded transaction length.",
    "回写阶段在相同有界事务长度下把 task 结果流写回存储。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    output[i] = {str_result_stream_name}.read();
  }}
}}'''

# 渲染 AXIS 版 task_graph helper，把计数播种与三段 actor 骨架单独收口。
def _mock_task_graph_axis_helpers(
    str_kernel_name: str,
    str_stream_name: str,
    str_result_stream_name: str,
    comment_language: str,
) -> str:
    """渲染 AXIS 版 task_graph helper 组合。

    参数:
        str_kernel_name: 当前 mock kernel 的函数名前缀。
        str_stream_name: read 到 compute 阶段之间使用的中间流名称。
        str_result_stream_name: compute 到 write 阶段之间使用的结果流名称。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于 AXIS task_graph 场景的 seed、read、compute、write helper 文本。
    """

    # 把单次事务计数播种与 AXIS token 的读算写 actor 一并展开成 helper 文本。
    return f'''static void seed_{str_kernel_name}_counts(
  int length,
  hls::stream<int>& read_count_stream
) {{
{_cpp_line_comment(
    comment_language,
    "Seed one bounded transaction count into the task graph "
    "so restart semantics stay explicit.",
    "将一次有界事务的计数写入 task graph，使重启语义保持显式。",
)}
  read_count_stream.write(length);
}}

static void read_{str_kernel_name}(
  hls::stream<ap_uint<32> >& in_stream,
  hls::stream<ap_uint<32> >& {str_stream_name},
  hls::stream<int>& read_count_stream,
  hls::stream<int>& compute_count_stream
) {{
{_cpp_line_comment(
    comment_language,
    "Read actor consumes one seeded transaction count before "
    "streaming AXI tokens.",
    "读取 actor 先消费一次预置事务计数，再顺序吸收 AXI token。",
)}
  int length = read_count_stream.read();
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1 style=flp
    {str_stream_name}.write(in_stream.read());
  }}
  compute_count_stream.write(length);
}}

static void compute_{str_kernel_name}(
  hls::stream<ap_uint<32> >& {str_stream_name},
  hls::stream<ap_uint<32> >& {str_result_stream_name},
  hls::stream<int>& compute_count_stream,
  hls::stream<int>& write_count_stream
) {{
{_cpp_line_comment(
    comment_language,
    "Compute actor uses a streamed transaction count so "
    "Vitis 2022.2 hls::task stays stream-only.",
    "计算 actor 通过流式事务计数保持 Vitis 2022.2 的 "
    "hls::task 仅流参数约束。",
)}
  int length = compute_count_stream.read();
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1 style=flp
    ap_uint<32> stream_sample = {str_stream_name}.read();
    {str_result_stream_name}.write(stream_sample + 1);
  }}
  write_count_stream.write(length);
}}

static void write_{str_kernel_name}(
  hls::stream<ap_uint<32> >& {str_result_stream_name},
  hls::stream<ap_uint<32> >& out_stream,
  hls::stream<int>& write_count_stream
) {{
{_cpp_line_comment(
    comment_language,
    "Write actor consumes the streamed transaction count "
    "before draining result tokens.",
    "写出 actor 先消费流式事务计数，再按边界取走结果 token。",
)}
  int length = write_count_stream.read();
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1 style=flp
    out_stream.write({str_result_stream_name}.read());
  }}
}}'''

# 渲染普通 AXI-Stream DATAFLOW helper，把 read/compute/write 模板从主入口抽离。
def _mock_stream_dataflow_helpers(
    str_kernel_name: str,
    str_stream_name: str,
    str_result_stream_name: str,
    comment_language: str,
) -> str:
    """渲染普通 AXI-Stream DATAFLOW helper 组合。

    参数:
        str_kernel_name: 当前 mock kernel 的函数名前缀。
        str_stream_name: read 到 compute 阶段之间使用的中间流名称。
        str_result_stream_name: compute 到 write 阶段之间使用的结果流名称。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于普通 AXI-Stream DATAFLOW 场景的 read、compute、write helper 文本。
    """

    # 返回普通 AXI-Stream DATAFLOW 场景的 helper 骨架。
    return f'''static void read_{str_kernel_name}(
  hls::stream<ap_uint<32> >& in_stream,
  hls::stream<ap_uint<32> >& {str_stream_name},
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Read stage isolates external AXI-Stream input from "
    "compute latency.",
    "读取阶段将外部 AXI-Stream 输入与计算延迟解耦。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    {str_stream_name}.write(in_stream.read());
  }}
}}

static void compute_{str_kernel_name}(
  hls::stream<ap_uint<32> >& {str_stream_name},
  hls::stream<ap_uint<32> >& {str_result_stream_name},
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Compute stage owns the token transform so DATAFLOW can "
    "overlap stages.",
    "计算阶段独立负责 token 变换，便于 DATAFLOW 重叠执行。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    ap_uint<32> stream_sample = {str_stream_name}.read();
    {str_result_stream_name}.write(stream_sample + 1);
  }}
}}

static void write_{str_kernel_name}(
  hls::stream<ap_uint<32> >& {str_result_stream_name},
  hls::stream<ap_uint<32> >& out_stream,
  int length
) {{
{_cpp_line_comment(
    comment_language,
    "Write stage preserves one output token for each input "
    "token.",
    "写出阶段确保每个输入 token 对应一个输出 token。",
)}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    out_stream.write({str_result_stream_name}.read());
  }}
}}'''
