"""渲染 mock provider 使用的 HLS 源码、测试平台和配置文件。"""

# 启用延迟求值注解，避免类型提示在导入阶段提前展开。
from __future__ import annotations

# 导入正则、路径和宽泛类型提示，支撑 mock HLS 文本拼装过程。
import re
from pathlib import Path
from typing import Any

# 导入注释渲染器和模式工具，供 mock 代码生成阶段复用仓库契约。
from .mock_comment_rendering import _comment
from .mock_vectors import _example_pattern
from scripts.python.generation.patterns import required_pattern_headers
from scripts.python.generation.vectors import VECTOR_HASH_TAG

# HLS testbench 继续输出结构化语义 transcript，供执行阶段采集 case 结果。
SEMANTIC_RESULT_TAG = "HLS-GEN-RESULT"  # HLS 语义 transcript 的结果标签

# 统一解析 spec 里的顶层函数名，避免各处重复拼接同一套回退逻辑。
def _top_function_name(spec: dict[str, Any]) -> str:
    """从 spec 中解析 mock HLS 顶层函数名。

    参数:
        spec: 生成 mock HLS 文本时使用的规范字典。

    返回:
        当前 mock HLS 产物应当使用的顶层函数名。
    """

    # 读取接口段，供 top_function 与 name 的回退链统一复用。
    dict_interfaces = spec.get("interfaces", {})  # spec 中的接口描述段

    # 解析最终要写入 HLS 产物的顶层函数名。
    str_top_function_name = str(dict_interfaces.get("top_function") or spec.get("name") or "kernel")  # mock HLS 顶层函数名

    # 返回统一解析后的顶层函数名。
    return str_top_function_name

# 收集 spec 中声明的参数字典列表，方便多个渲染函数共享遍历逻辑。
def _argument_dicts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """提取 spec 中有效的参数字典列表。

    参数:
        spec: 生成 mock HLS 文本时使用的规范字典。

    返回:
        仅保留字典项的参数列表，供后续参数名和接口拼装逻辑复用。
    """

    # 准备有效参数列表，后续逐项筛掉非字典输入。
    list_argument_dicts: list[dict[str, Any]] = []  # 通过类型检查后的参数列表

    # 逐项扫描 interfaces.arguments，过滤掉异常条目。
    for dict_argument in spec.get("interfaces", {}).get("arguments", []):

        # 只保留结构合法的参数字典。
        if isinstance(dict_argument, dict):

            # 把合法参数字典纳入返回列表。
            list_argument_dicts = [*list_argument_dicts, dict_argument]  # 继续保留的参数字典集合

    # 返回过滤后的参数字典列表。
    return list_argument_dicts

# 归并 spec 中可用的参数名集合，便于模式分支快速判断接口形态。
def _argument_name_set(spec: dict[str, Any]) -> set[str]:
    """提取 spec 中有效参数名的集合。

    参数:
        spec: 生成 mock HLS 文本时使用的规范字典。

    返回:
        已转成字符串并去重后的参数名集合。
    """

    # 准备参数名集合，后续按合法 name 字段逐项填充。
    set_argument_names: set[str] = set()  # 当前 spec 中声明的参数名集合

    # 复用统一参数过滤逻辑，避免直接遍历原始混合列表。
    for dict_argument in _argument_dicts(spec):

        # 只吸收带 name 的参数条目。
        if dict_argument.get("name"):

            # 把当前参数名合并进集合。
            set_argument_names = set_argument_names | {str(dict_argument.get("name"))}  # 已确认存在的参数名集合

    # 返回去重后的参数名集合。
    return set_argument_names

# 按参数名整理参数字典，方便后续直接索引 type、depth 和接口约束。
def _argument_lookup(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """构建按参数名索引的参数查找表。

    参数:
        spec: 生成 mock HLS 文本时使用的规范字典。

    返回:
        只包含有效参数名的参数字典映射，便于按名称直接读取参数配置。
    """

    # 初始化参数查找表，准备按名称登记每个有效参数。
    dict_argument_lookup: dict[str, dict[str, Any]] = {}  # 以参数名索引的参数配置映射

    # 遍历经过类型过滤的参数字典列表，逐项提取合法参数名。
    for dict_argument in _argument_dicts(spec):

        # 归一化当前参数名，避免空白字符串进入查找表。
        str_argument_name = str(dict_argument.get("name") or "").strip()  # 当前参数的规范化名称

        # 跳过缺少有效名称的参数条目。
        if not str_argument_name:

            # 继续检查后续具名参数。
            continue

        # 记录当前名称对应的参数配置，供后续按名直接索引。
        dict_argument_lookup[str_argument_name] = dict_argument  # 当前参数名对应的参数字典

    # 返回按参数名整理好的参数查找表。
    return dict_argument_lookup

# 统一渲染带缩进的 C++ 单行注释，避免模板字符串重复拼接格式细节。
def _cpp_line_comment(
    comment_language: str,
    english_text: str,
    chinese_text: str,
    indent: str = "  ",
) -> str:
    """生成一行带缩进的 C++ 注释文本。

    参数:
        comment_language: 生成 C++ 注释时使用的注释语言标识。
        english_text: 英文注释文本。
        chinese_text: 中文注释文本。
        indent: 注释行前要保留的缩进字符串。

    返回:
        带尾部换行的 C++ 单行注释文本。
    """

    # 渲染当前注释行的注释正文，复用统一语言切换逻辑。
    str_comment_body = _comment(comment_language, english_text, chinese_text)  # 当前注释行的正文文本

    # 返回带缩进和换行的完整 C++ 注释行。
    return f"{indent}// {str_comment_body}\n"

# 生成 mock HLS 头文件内容，统一收敛 include 集与顶层函数声明。
def _mock_hls_header_text(spec: dict[str, Any], comment_language: str) -> str:
    """渲染 mock HLS 头文件文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        可直接写入 `.h` 头文件的完整文本。
    """

    # 解析头文件中要声明的顶层函数名。
    str_top_function_name = _top_function_name(spec)  # 头文件中的顶层函数名

    # 先放入基础 HLS 头文件，再按模式补充额外依赖。
    list_header_names = ["ap_fixed.h", "ap_int.h"]  # mock HLS 头文件依赖顺序

    # 逐项补齐当前模式要求的头文件。
    for str_required_header in required_pattern_headers(spec):

        # 只在尚未纳入时追加，保持 include 列表去重。
        if str_required_header not in list_header_names:

            # 把缺失的模式头文件追加到 include 列表末尾。
            list_header_names = [*list_header_names, str_required_header]  # 已收集的头文件顺序列表

    # 确保 hls::stream 相关定义总能拿到对应头文件。
    if "hls_stream.h" not in list_header_names:

        # 把 stream 头文件放到最终 include 列表中。
        list_header_names = [*list_header_names, "hls_stream.h"]  # 含 stream 依赖的最终头文件列表

    # 渲染所有 include 行，供头文件正文复用。
    str_include_block = "".join(f"#include <{str_header_name}>\n" for str_header_name in list_header_names)  # 头文件 include 语句块

    # 渲染顶层函数声明前的说明注释。
    str_declaration_comment = _comment(comment_language, "Vitis HLS top function declaration.", "Vitis HLS 顶层函数声明。")  # 顶层函数声明注释

    # 返回拼装好的 mock HLS 头文件文本。
    return (
        "#pragma once\n"
        f"{str_include_block}\n"
        f"// {str_declaration_comment}\n"
        f"void {str_top_function_name}({_cpp_arguments(spec)});\n"
    )

# 生成 mock HLS 源文件内容，负责把 helper、pragma 和核心 body 串成完整 `.cpp`。
def _mock_hls_source_text(
    spec: dict[str, Any],
    header_name: str,
    comment_language: str,
) -> str:
    """渲染 mock HLS 源文件文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。
        header_name: 当前源文件要包含的头文件名。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        可直接写入 `.cpp` 源文件的完整文本。
    """

    # 解析源文件定义阶段要使用的顶层函数名。
    str_top_function_name = _top_function_name(spec)  # 源文件中的顶层函数名

    # 渲染可选 helper 函数定义，供主函数前置复用。
    str_helper_text = _mock_hls_helpers_text(spec, comment_language)  # helper 函数文本

    # 只在 helper 非空时补一个换行，避免源文件出现多余空段。
    str_helper_block = f"{str_helper_text}\n" if str_helper_text else ""  # 主函数前可选插入的 helper 文本块

    # 预先渲染顶层函数参数列表，避免主模板里重复拼接长表达式。
    str_argument_text = _cpp_arguments(spec)  # 顶层函数参数列表文本

    # 预先渲染 pragma 文本块，保持主模板职责只负责串接。
    str_pragma_block = _hls_pragmas(spec)  # 顶层函数需要插入的 pragma 文本

    # 预先渲染端口与流水线约束说明注释，避免返回模板过长。
    str_port_comment = _cpp_line_comment(  # 顶层端口与 pragma 约束说明注释
        comment_language,  # 顶层函数说明沿用当前 mock 代码的注释语言
        "Port protocols and pipeline constraints follow "
        "the confirmed HLS spec.",
        "端口协议和流水线约束由确认后的 HLS spec 驱动。",  # 中文端口与流水线约束说明
    )

    # 渲染容差标记注释，保持向量期望与 HLS mock 的对应关系。
    str_tolerance_comment = _mock_tolerance_marker(spec, comment_language, indent="  ")  # 核心计算前的容差提示注释

    # 只在存在容差提示时写入对应文本行。
    str_tolerance_block = f"{str_tolerance_comment}\n" if str_tolerance_comment else ""  # 主函数中的容差提示文本块

    # 预先渲染核心计算说明注释，让主模板保持线性拼接结构。
    str_body_comment = _cpp_line_comment(  # 顶层核心计算说明注释
        comment_language,  # 核心计算说明也要保持和当前 mock 输出同一种语言
        "Core computation stays synthesizable and aligned "
        "with the expected vectors.",
        "核心计算保持可综合并与期望向量对齐。",  # 中文核心计算说明
    )

    # 预先渲染函数主体，避免返回模板中嵌套调用过深。
    str_body_text = _mock_hls_body(spec)  # 顶层函数主体文本

    # 返回拼装好的 mock HLS 源文件文本。
    return (
        f'#include "{header_name}"\n\n'
        f"{str_helper_block}"
        f"void {str_top_function_name}({str_argument_text}) {{\n"
        f"{str_port_comment}"
        f"{str_pragma_block}\n"
        f"{str_tolerance_block}"
        f"{str_body_comment}"
        f"{str_body_text}\n"
        "}\n"
    )

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

# 根据模式生成 mock HLS helper 函数，给 DATAFLOW、task_graph 等分支补齐局部骨架。
def _mock_hls_helpers_text(spec: dict[str, Any], comment_language: str) -> str:
    """渲染 mock HLS helper 函数文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        主函数前需要插入的 helper 函数字符串；若当前模式不需要 helper，则返回空串。
    """

    # 识别当前 spec 对应的示例模式名称。
    str_pattern_name = _example_pattern(spec)  # 当前 mock HLS 的模式名称

    # 解析 kernel 名称，供 task_graph 等 helper 函数名拼装使用。
    str_kernel_name = str(spec.get("name") or "kernel")  # helper 函数名中的 kernel 前缀

    # 收集参数名集合，后续按接口组合决定 helper 模板。
    set_argument_names = _argument_name_set(spec)  # 当前 spec 的参数名集合

    # 当 matmul 既要求 DATAFLOW 又具备双输入输出接口时，生成专用分阶段 helper。
    if (
        str_pattern_name == "matmul"
        and _requires_dataflow_pragma(spec)
        and {"input_a", "input_b", "output", "length"}.issubset(set_argument_names)
    ):

        # 返回 matmul DATAFLOW helper 函数集合。
        return _mock_matmul_dataflow_helpers(comment_language)

    # 当模式不需要流式 helper 骨架时，直接返回空串。
    if str_pattern_name not in {"dataflow", "task_graph"}:

        # 返回空串，表示主函数无需前置 helper。
        return ""

    # 为 DATAFLOW 或 task_graph 分支准备统一的中间流命名。
    str_stream_name = "task_stream" if str_pattern_name == "task_graph" else "mid_stream"  # 输入到计算阶段之间的中间流名称

    # 为 DATAFLOW 或 task_graph 分支准备统一的结果流命名。
    str_result_stream_name = "task_result_stream" if str_pattern_name == "task_graph" else "result_stream"  # 计算阶段输出结果流名称

    # 当模式是二维块处理 dataflow 时，生成 read/row/reorder/col/write helper。
    if (
        str_pattern_name == "dataflow"
        and {"input", "output", "rows", "cols"}.issubset(set_argument_names)
    ):

        # 返回二维块 dataflow 所需的完整 helper 骨架。
        return _mock_block_dataflow_helpers(comment_language)

    # 当模式进入 task_graph 时，需要再区分 memory 版和 axis 版 helper。
    if str_pattern_name == "task_graph":

        # 如果是 memory 输入输出的 task_graph，则渲染 load/compute/store helper。
        if {"input", "output", "length"}.issubset(set_argument_names):

            # 返回 memory 接口版 task_graph 所需的 helper 组合。
            return _mock_task_graph_memory_helpers(
                str_kernel_name,
                str_stream_name,
                str_result_stream_name,
                comment_language,
            )

        # 返回 AXIS task_graph 场景的 seed/read/compute/write actor 骨架。
        return _mock_task_graph_axis_helpers(
            str_kernel_name,
            str_stream_name,
            str_result_stream_name,
            comment_language,
        )

    # 对普通流式 DATAFLOW 分支，输出单中间流三段式 read/compute/write helper。
    return _mock_stream_dataflow_helpers(
        str_kernel_name,
        str_stream_name,
        str_result_stream_name,
        comment_language,
    )

# 根据 axis + length 的模式名选择 testbench 主体模板。
def _mock_axis_length_testbench_body(
    spec: dict[str, Any],
    top_function_name: str,
    vectors: list[dict[str, Any]],
    comment_language: str,
) -> str:
    """为带 `length` 的 axis 接口选择 testbench 主体。

    参数:
        spec: 描述当前 mock HLS 接口与 pattern 的规范字典。
        top_function_name: testbench 中要调用的顶层函数名。
        vectors: 当前 testbench 要消费的向量用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于带 `length` 的 axis 接口场景的 testbench 主体文本。
    """

    # 读取当前 axis 场景的 pattern 名称，决定是否需要专用驱动模板。
    str_pattern_name = _example_pattern(spec)  # 当前 axis-length 场景的模式名

    # task_graph 需要保留 actor 链路顺序与 token 数量校验。
    if str_pattern_name == "task_graph":

        # 返回 task_graph 的 actor 链路专用驱动体。
        return _mock_task_graph_axis_cases(top_function_name, vectors, comment_language)

    # rle_axis 需要覆盖字节包到字包的特定编码路径。
    if str_pattern_name == "rle_axis":

        # 返回 rle_axis 的编码路径专用驱动体。
        return _mock_rle_axis_cases(spec, top_function_name, vectors, comment_language)

    # 其他带 length 的 axis 场景统一回退到通用流模板。
    return _mock_axis_cases(top_function_name, vectors, comment_language)

# 根据无 length 的 axis 模式选择最小 testbench 主体。
def _mock_axis_stream_testbench_body(
    spec: dict[str, Any],
    top_function_name: str,
    vectors: list[dict[str, Any]],
    comment_language: str,
) -> str:
    """为无 `length` 的 axis 接口选择 testbench 主体。

    参数:
        spec: 描述当前 mock HLS 接口与 pattern 的规范字典。
        top_function_name: testbench 中要调用的顶层函数名。
        vectors: 当前 testbench 要消费的向量用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于无 `length` 的 axis 接口场景的 testbench 主体文本。
    """

    # freerun 场景需要覆盖持续流动的 directio 单元测试模板。
    if _example_pattern(spec) == "directio_freerun":

        # 返回 directio freerun 场景的持续流驱动体。
        return _mock_directio_unit_cases(top_function_name, vectors, comment_language)

    # 普通 directio 场景只需要保留一次最小顶层调用。
    return f"  {top_function_name}(in_stream, out_stream);\n"

# 根据参数组合选择 mock HLS testbench 主体模板。
def _select_mock_hls_testbench_body(
    spec: dict[str, Any],
    top_function_name: str,
    vectors: list[dict[str, Any]],
    argument_names: set[str],
    comment_language: str,
) -> str:
    """按接口参数组合选择 mock HLS testbench 主体。

    参数:
        spec: 描述当前 mock HLS 接口与 pattern 的规范字典。
        top_function_name: testbench 中要调用的顶层函数名。
        vectors: 当前 testbench 要消费的向量用例列表。
        argument_names: 已规范化后的参数名集合。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适用于当前参数组合的 testbench 主体文本。
    """

    # 双输入 m_axi 组合需要覆盖乘加访存与长度控制路径。
    if {"input_a", "input_b", "output", "length"}.issubset(argument_names):

        # 返回双输入 m_axi 乘加路径的驱动体。
        return _mock_multi_m_axi_cases(spec, top_function_name, vectors, comment_language)

    # 向量缩放组合需要验证 scale 参数是否参与计算。
    if {"input", "output", "scale", "length"}.issubset(argument_names):

        # 返回 scale 参数参与计算的驱动体。
        return _mock_vector_scale_cases(spec, top_function_name, vectors, comment_language)

    # 二维 block 组合需要覆盖行列维度下的访存变换逻辑。
    if {"input", "output", "rows", "cols"}.issubset(argument_names):

        # 返回二维 block 访存变换的驱动体。
        return _mock_block_transform_cases(spec, top_function_name, vectors, comment_language)

    # 普通 input/output/length 组合使用线性内存向量模板。
    if {"input", "output", "length"}.issubset(argument_names):

        # 返回线性内存读写路径的驱动体。
        return _mock_input_output_cases(spec, top_function_name, vectors, comment_language)

    # 带 length 的 axis 接口继续按 pattern 细分子模板。
    if {"in_stream", "out_stream", "length"}.issubset(argument_names):

        # 返回带 length 的 axis 驱动体。
        return _mock_axis_length_testbench_body(spec, top_function_name, vectors, comment_language)

    # 无 length 的 axis 接口使用轻量 directio 模板。
    if {"in_stream", "out_stream"}.issubset(argument_names):

        # 返回无 length 的 axis 驱动体。
        return _mock_axis_stream_testbench_body(spec, top_function_name, vectors, comment_language)

    # 其余无参场景只保留最小顶层函数调用。
    return f"  {top_function_name}();\n"

# 根据接口组合渲染 mock HLS 测试平台主程序。
def _mock_hls_testbench_text(
    spec: dict[str, Any],
    vectors: list[dict[str, Any]],
    vector_hash: str,
    comment_language: str,
) -> str:
    """渲染 mock HLS testbench 文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。
        vectors: 当前 testbench 要消费的向量用例列表。
        vector_hash: 写入 testbench 注释的向量合同 hash。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        可直接写入 `_tb.cpp` 的完整测试平台文本。
    """

    # 解析 testbench 要包含和调用的顶层函数名。
    str_top_function_name = _top_function_name(spec)  # testbench 调用的顶层函数名

    # 收集参数名集合，决定 testbench 主体采用哪一种接口模板。
    set_argument_names = _argument_name_set(spec)  # 用于分发模板的参数名集合

    # 只在存在向量合同 hash 时写入对应注释。
    str_hash_comment = f"  // {VECTOR_HASH_TAG} {vector_hash}\n" if vector_hash else ""  # 向量合同 hash 注释

    # 把每个 case id 渲染成 PASS/FAIL 占位注释，便于后续验收脚本识别。
    str_case_comments = "\n".join(f'  // {dict_vector["id"]} PASS FAIL' for dict_vector in vectors)  # 各测试用例的验收占位注释

    # 渲染容差提示注释，保持 testbench 与生成 spec 的误差设定一致。
    str_tolerance_comment = _mock_tolerance_marker(spec, comment_language, indent="  ")  # testbench 的容差提示注释

    # 只在容差提示存在时补出完整注释行。
    str_tolerance_block = f"{str_tolerance_comment}\n" if str_tolerance_comment else ""  # testbench 容差注释块

    # 按参数组合挑选当前 testbench 需要拼接的主体模板。
    str_body = _select_mock_hls_testbench_body(  # 当前接口组合对应的 testbench 主体
        spec,  # 当前 mock HLS 规范字典
        str_top_function_name,  # 生成 testbench 时使用的顶层函数名
        vectors,  # reference 用例向量列表
        set_argument_names,  # 规范里声明过的参数名集合
        comment_language,  # testbench 注释语言
    )

    # 返回拼装好的 mock HLS testbench 文本。
    return f'''#include <iostream>
#include "../src/{str_top_function_name}.h"

int main() {{
{str_hash_comment}{str_tolerance_block}{str_case_comments}
  int failures = 0;
{str_body}
  if (failures != 0) {{
    std::cout << "FAIL\\n";
    return 1;
  }}
  std::cout << "PASS\\n";
  return 0;
}}
'''

# 生成 mock HLS 的 hls_config.cfg 文本，汇总源文件、测试平台和时钟配置。
def _mock_hls_cfg_text(spec: dict[str, Any], files: list[dict[str, Any]]) -> str:
    """渲染 mock HLS 的 `hls_config.cfg` 文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。
        files: 本次 mock 产物清单，包含源文件、头文件和 testbench 条目。

    返回:
        可直接写入 `hls_config.cfg` 的完整文本。
    """

    # 解析 cfg 中要声明的顶层函数名。
    str_top_function_name = _top_function_name(spec)  # cfg 使用的 syn.top 名称

    # 准备 cfg 基础行，先写入 HLS 段和 syn.top。
    list_cfg_lines = ["[HLS]", f"syn.top={str_top_function_name}"]  # cfg 基础行集合

    # 先收集综合要消费的源文件与头文件。
    for dict_file in files:

        # 读取当前文件路径字符串，便于后续扩展名和 stem 判断。
        str_path = str(dict_file["path"])  # 当前产物文件路径

        # 解析当前文件的扩展名，统一转成小写比较。
        str_suffix = Path(str_path).suffix.lower()  # 当前文件扩展名

        # 对于综合源文件，排除 `_tb` 测试平台条目。
        if str_suffix in {".cpp", ".cc", ".cxx"} and "_tb" not in Path(str_path).stem:

            # 把当前综合源文件登记到 syn.file。
            list_cfg_lines = [*list_cfg_lines, f"syn.file={str_path}"]  # 已收集的综合文件行

        # 对于头文件，同样登记到 syn.file 供 Vitis HLS 读取。
        if str_suffix in {".h", ".hpp"}:

            # 把头文件路径也写入 syn.file。
            list_cfg_lines = [*list_cfg_lines, f"syn.file={str_path}"]  # 已收集的综合与头文件行

    # 再单独收集测试平台文件，保持 syn.file 与 tb.file 语义分离。
    for dict_file in files:

        # 读取当前文件路径字符串，供 `_tb` 识别与扩展名判断。
        str_path = str(dict_file["path"])  # 当前待判断的 testbench 路径

        # 识别 `_tb` C++ 文件并登记到 tb.file。
        if "_tb" in Path(str_path).stem and Path(str_path).suffix.lower() in {
            ".cpp",
            ".cc",
            ".cxx",
        }:

            # 把测试平台文件路径写入 tb.file。
            list_cfg_lines = [*list_cfg_lines, f"tb.file={str_path}"]  # 已收集的 testbench 文件行

    # 读取时钟段，准备在 cfg 中补 period_ns。
    dict_clock = spec.get("clock", {})  # spec 中的时钟配置段

    # 如果时钟周期存在，就写入 clock 项。
    if isinstance(dict_clock, dict) and dict_clock.get("period_ns") not in (None, ""):

        # 把时钟周期写入 cfg。
        list_cfg_lines = [*list_cfg_lines, f"clock={dict_clock['period_ns']}"]  # 已补齐时钟项的 cfg 行集合

    # 解析目标器件 part，优先使用 workflow.part 再回退到顶层 part。
    str_part_name = str((spec.get("workflow") or {}).get("part") or spec.get("part") or "")  # cfg 中的目标器件 part

    # 当 part 有值时，把它写入 cfg。
    if str_part_name:

        # 把目标器件 part 追加到 cfg 行集合。
        list_cfg_lines = [*list_cfg_lines, f"part={str_part_name}"]  # 已补齐 part 的 cfg 行集合

    # 读取接口 profile，便于决定是否开启 burst 配置段。
    dict_interface_profile = spec.get("interface_profile") if isinstance(spec.get("interface_profile"), dict) else {}  # 接口 profile 配置段

    # 当 profile 明确启用 burst_support 时，写入 interface 段。
    if (
        dict_interface_profile.get("burst_support")
        and dict_interface_profile.get("max_burst_len")
    ):

        # 追加 interface 段与 m_axi burst 长度配置。
        list_cfg_lines = [  # 已补齐 burst 配置的 cfg 行集合
            *list_cfg_lines,  # 保留此前已经累积好的 cfg 行
            "",  # interface 段前的分隔空行
            "[interface]",  # cfg 的 interface 小节标题
            f"m_axi_max_read_burst_length={int(dict_interface_profile['max_burst_len'])}",  # m_axi 读突发长度配置
        ]

    # 以保留末尾换行的方式返回完整 hls_config.cfg 文本。
    return "\n".join(list_cfg_lines) + "\n"

# 把 spec 中的参数声明拼成 C++ 顶层函数参数列表。
def _cpp_arguments(spec: dict[str, Any]) -> str:
    """渲染顶层函数参数列表文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。

    返回:
        适合直接写入 C++ 函数声明和定义的参数列表字符串。
    """

    # 准备参数声明列表，稍后按合法参数顺序逐项追加。
    list_argument_texts: list[str] = []  # C++ 参数声明字符串列表

    # 遍历已过滤的参数字典，收集具名参数声明。
    for dict_argument in _argument_dicts(spec):

        # 只为带参数名的条目生成 C++ 参数声明。
        if dict_argument.get("name"):

            # 组装当前参数的 `type name` 声明文本。
            str_argument_text = f'{dict_argument.get("type", "int")} {dict_argument["name"]}'  # 单个 C++ 参数声明文本

            # 把当前参数声明追加到参数列表末尾。
            list_argument_texts = [*list_argument_texts, str_argument_text]  # 已收集的参数声明文本列表

    # 返回逗号分隔的参数列表；若没有参数则写成 void。
    return ", ".join(list_argument_texts) or "void"

# 根据 spec 渲染顶层函数体前的 HLS pragma 列表。
def _hls_pragmas(spec: dict[str, Any]) -> str:
    """渲染 mock HLS 顶层函数的 pragma 文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。

    返回:
        适合直接插入 C++ 函数体的多行 pragma 字符串。
    """

    # 准备 pragma 行列表，稍后按接口和模式顺序逐项拼装。
    list_pragma_lines: list[str] = []  # 顶层函数的 pragma 行集合

    # 识别当前 spec 对应的模式名称。
    str_pattern_name = _example_pattern(spec)  # 顶层函数 pragma 的模式名称

    # 先为每个参数渲染接口 pragma。
    for dict_argument in _argument_dicts(spec):

        # 缺少参数名的条目无法渲染 pragma，直接跳过。
        if not dict_argument.get("name"):

            # 跳过没有 name 的参数条目。
            continue

        # 解析当前参数声明要求保留的接口类型。
        str_interface_name = str(dict_argument.get("interface") or "s_axilite")  # 决定当前参数该渲染成哪类接口 pragma

        # m_axi 参数需要补 bundle 和 depth。
        if str_interface_name == "m_axi":

            # 按 m_axi 约束渲染当前参数的接口 pragma。
            str_pragma_line = (  # 带 bundle 与 depth 约束的 m_axi pragma
                "#pragma HLS INTERFACE "
                f"m_axi port={dict_argument['name']} "
                f"bundle={dict_argument.get('bundle', 'gmem')} "
                f"depth={_m_axi_depth(spec, dict_argument)}"
            )

        # AXIS、FIFO 和 ap_none 直接按接口名写入。
        elif str_interface_name in {"axis", "ap_fifo", "ap_none"}:

            # 为流式或无握手协议参数保留原始接口 pragma。
            str_pragma_line = (  # 保留流式或无握手协议的接口 pragma
                f"#pragma HLS INTERFACE {str_interface_name} "
                f"port={dict_argument['name']}"
            )

        # 其他接口一律回退为 s_axilite。
        else:

            # 渲染默认的 s_axilite 接口 pragma。
            str_pragma_line = f"#pragma HLS INTERFACE s_axilite port={dict_argument['name']}"  # 默认 s_axilite 接口 pragma 行

        # 把当前参数的接口 pragma 写入列表。
        list_pragma_lines = [*list_pragma_lines, str_pragma_line]  # 已收集的接口 pragma 行

    # 渲染控制接口 pragma，默认走 s_axilite。
    str_control_pragma = "#pragma HLS INTERFACE " f"{spec.get('interfaces', {}).get('control', 's_axilite')} port=return"  # 顶层函数控制接口 pragma 行

    # 把控制接口 pragma 追加到列表末尾。
    list_pragma_lines = [*list_pragma_lines, str_control_pragma]  # 已追加控制接口的 pragma 行集合

    # DATAFLOW 类模式需要显式追加 DATAFLOW pragma。
    if str_pattern_name in {"dataflow", "task_graph", "streamofblocks"}:

        # 把 DATAFLOW pragma 写入列表。
        list_pragma_lines = [*list_pragma_lines, "#pragma HLS DATAFLOW"]  # 已补齐 DATAFLOW 的 pragma 行集合

    # 非 DATAFLOW 模式且要求 pipeline 时，补默认 PIPELINE pragma。
    if (
        spec.get("pipeline_required", True)
        and str_pattern_name not in {"dataflow", "task_graph", "streamofblocks"}
        and not _requires_dataflow_pragma(spec)
    ):

        # 把默认 PIPELINE pragma 追加到列表末尾。
        list_pragma_lines = [*list_pragma_lines, "#pragma HLS PIPELINE II=1"]  # 已补齐默认 PIPELINE 的 pragma 行集合

    # 预先缓存已经落盘的 pragma 指纹，后面只对首次出现的新条目追加输出。
    set_seen_pragma_keys = set(  # 已落盘 pragma 的去重指纹集合
        _pragma_identity(str_pragma_line) for str_pragma_line in list_pragma_lines  # 为每条 pragma 生成去重身份键
    )

    # 合并 spec 额外要求的 pragma，同时跳过重复和局部变量 pragma。
    for str_required_pragma in _required_pragmas(spec):

        # 顶层 pragma 列表不在这里合并局部变量 pragma。
        if "variable=" in str_required_pragma:

            # 跳过局部变量 pragma，由具体 body 模板自行负责。
            continue

        # 解析当前追加 pragma 的去重身份键。
        tuple_pragma_key = _pragma_identity(str_required_pragma)  # 追加 pragma 的身份键

        # 已出现过的 pragma 不再重复写入。
        if tuple_pragma_key in set_seen_pragma_keys:

            # 跳过重复 pragma。
            continue

        # 仅在完整文本尚未出现时才真正加入列表。
        if str_required_pragma not in list_pragma_lines:

            # 把新增 pragma 追加到列表。
            list_pragma_lines = [*list_pragma_lines, str_required_pragma]  # 已合并额外 pragma 的列表

            # 把刚追加的新 pragma 身份键并入去重集合。
            set_seen_pragma_keys = set_seen_pragma_keys | {tuple_pragma_key}  # 更新后的 pragma 身份集合

    # 返回带统一缩进的 pragma 文本块。
    return "\n".join(f"  {str_pragma_line}" for str_pragma_line in list_pragma_lines)

# 归一化单条 pragma 的身份键，供去重逻辑复用。
def _pragma_identity(pragma: str) -> tuple[str, str] | tuple[str, str, str]:
    """计算 pragma 的去重身份键。

    参数:
        pragma: 单条 HLS pragma 文本。

    返回:
        供去重逻辑比较的元组身份键。
    """

    # 先压缩空白，保证同义 pragma 拿到一致文本。
    str_normalized = " ".join(str(pragma).strip().split())  # 归一化后的 pragma 文本

    # 对 INTERFACE pragma 提取接口类型与端口名，避免同端口重复声明。
    obj_match = re.match(  # INTERFACE pragma 的端口匹配结果
        r"#pragma\s+HLS\s+INTERFACE\s+(\S+)\s+port=([A-Za-z_][A-Za-z0-9_]*)",  # INTERFACE pragma 的正则模式
        str_normalized,  # 已压缩空白后的 pragma 文本
    )

    # 匹配到接口 pragma 时，按端口维度去重。
    if obj_match:

        # 返回 interface pragma 的去重键。
        return ("interface", obj_match.group(2), obj_match.group(1))

    # PIPELINE pragma 统一归到固定类别键，避免同类指令重复进入 pragma 列表。
    if str_normalized.startswith("#pragma HLS PIPELINE"):

        # 这里返回 pipeline 的去重身份键，供上层集合直接判重。
        return ("pipeline", str_normalized)

    # DATAFLOW pragma 也按固定类别键归一，避免同类指令反复堆叠。
    if str_normalized.startswith("#pragma HLS DATAFLOW"):

        # DATAFLOW 在这里只需要固定类别键，避免同一类指令叠加多次。
        return ("dataflow", str_normalized)

    # 其他 pragma 退回到通用文本去重键。
    return ("pragma", str_normalized)

# 解析 m_axi 端口的 depth，优先使用参数声明值，再回退到性能配置。
def _m_axi_depth(spec: dict[str, Any], argument: dict[str, Any]) -> int:
    """解析 m_axi 端口应写入 pragma 的 depth。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。
        argument: 当前待计算 depth 的参数字典。

    返回:
        m_axi pragma 中应使用的 depth 整数值。
    """

    # 参数自身显式声明了合法 depth 时，优先沿用该值。
    if isinstance(argument.get("depth"), int) and int(argument["depth"]) > 0:

        # 返回参数级别显式声明的 depth。
        return int(argument["depth"])

    # 读取性能配置段，供默认 depth 回退逻辑复用。
    dict_performance = spec.get("performance") if isinstance(spec.get("performance"), dict) else {}  # spec 中的性能配置段

    # 按常见性能字段顺序查找可用的 depth 值。
    for str_key in ("max_length", "vector_length", "depth"):

        # 遇到合法正整数配置时，直接作为 m_axi depth 返回。
        if isinstance(dict_performance.get(str_key), int) and int(
            dict_performance[str_key]
        ) > 0:

            # 返回性能配置中给出的 depth。
            return int(dict_performance[str_key])

    # 当前 spec 未提供更细 depth 时，回退到保守默认值。
    return 1024

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

# 提取 board acceptance 使用的源规范标识。
def _board_source_spec(spec: dict[str, Any]) -> str:
    """提取 board acceptance 使用的源规范标识。

    参数:
        spec: 描述 mock HLS 接口、流程与板卡验收约束的规范字典。

    返回:
        `workflow.board_acceptance.source_spec` 的去空白字符串；未声明时返回空字符串。
    """

    # 读取 workflow 配置段，供后续定位 board_acceptance 子结构。
    dict_workflow = spec.get("workflow") if isinstance(spec.get("workflow"), dict) else {}  # 用于查找 board_acceptance 的 workflow 子字典

    # 先准备一个类型明确的候选字典，只有确认原值是映射时才写入。
    dict_board_acceptance_candidate: dict[str, Any] | None = None  # 尚未确认是否可用的 board_acceptance 候选字典

    # 原始 board_acceptance 已经是字典时，再把它收进类型明确的候选变量。
    if isinstance(dict_workflow.get("board_acceptance"), dict):

        # 把已确认类型的 board_acceptance 收进候选变量，避免后续继续看到不确定对象。
        dict_board_acceptance_candidate = dict_workflow["board_acceptance"]  # 已确认可索引的 board_acceptance 候选字典

    # 再把可空候选字典折叠成最终配置段，保证 source_spec 提取端总能安全索引。
    dict_board_acceptance = dict_board_acceptance_candidate or {}  # 最终用于读取 source_spec 的 board_acceptance 子字典

    # 返回清洗后的 source_spec 文本。
    return str(dict_board_acceptance.get("source_spec") or "").strip()

# 提取 spec 显式声明的 required_pragmas 列表，供 pragma 驱动分支复用。
def _required_pragmas(spec: dict[str, Any]) -> list[str]:
    """收集当前 spec 需要保留的 pragma 文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。

    返回:
        已转成字符串并剔除空白项的 pragma 文本列表。
    """

    # 先拿到 hls_profile 子字典，后续只从这里读取 required_pragmas 声明。
    dict_hls_profile = spec.get("hls_profile") if isinstance(spec.get("hls_profile"), dict) else {}  # 仅用于提取 pragma 声明的 hls_profile 子字典

    # 先保留 required_pragmas 的原始列表值，便于单独做字符串化与空白过滤。
    list_raw_pragmas = dict_hls_profile.get("required_pragmas", []) or []  # hls_profile 中声明的原始 pragma 项列表

    # 再把原始 pragma 项收敛成非空字符串，供 DATAFLOW 和分区匹配逻辑复用。
    list_required_pragmas = [str(item) for item in list_raw_pragmas if str(item).strip()]  # 过滤空白后的 pragma 文本列表

    # 返回整理后的 pragma 文本列表。
    return list_required_pragmas

# 判断当前 spec 是否显式要求 DATAFLOW pragma，供主体模板切换。
def _requires_dataflow_pragma(spec: dict[str, Any]) -> bool:
    """判断当前 spec 是否要求 DATAFLOW pragma。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。

    返回:
        只要 required_pragmas 中出现 DATAFLOW 文本就返回 `True`。
    """

    # 收集当前 spec 暴露的 pragma 文本，供 DATAFLOW 存在性判断复用。
    list_required_pragmas = _required_pragmas(spec)  # 当前 spec 暴露的 pragma 文本列表

    # 返回当前 spec 是否显式要求 DATAFLOW。
    return any("DATAFLOW" in str_pragma for str_pragma in list_required_pragmas)

# 判断指定变量是否显式要求 ARRAY_PARTITION pragma，供局部缓冲模板切换。
def _requires_partition_pragma(spec: dict[str, Any], variable: str) -> bool:
    """判断指定变量是否需要 ARRAY_PARTITION pragma。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。
        variable: 要匹配的局部缓冲变量名。

    返回:
        当 required_pragmas 同时包含 ARRAY_PARTITION 和目标变量名时返回 `True`。
    """

    # 生成 pragma 文本中要匹配的变量锚点。
    str_partition_token = f"variable={variable}"  # ARRAY_PARTITION 中的变量匹配片段

    # 收集当前 spec 的 pragma 文本，供 ARRAY_PARTITION 匹配逻辑遍历。
    list_required_pragmas = _required_pragmas(spec)  # 用于匹配 ARRAY_PARTITION 的 pragma 文本列表

    # 返回目标变量是否被 ARRAY_PARTITION 显式要求。
    return any(
        "ARRAY_PARTITION" in str_pragma and str_partition_token in str_pragma
        for str_pragma in list_required_pragmas
    )

# 在 FFT 或 CORDIC 场景中补充误差门限注释，保持 mock 产物可审阅。
def _mock_tolerance_marker(spec: dict[str, Any], comment_language: str, *, indent: str = "") -> str:
    """渲染 FFT 或 CORDIC mock 主体中的误差门限注释。

    参数:
        spec: 描述 mock HLS 接口、模式与容差元数据的规范字典。
        comment_language: 生成 C++ 注释时使用的注释语言标识。
        indent: 需要写入注释行前的缩进字符串。

    返回:
        带缩进的容差注释文本；当前模式不需要容差提示时返回空字符串。
    """

    # 读取 hls_profile 段，供容差元数据提取逻辑复用。
    dict_hls_profile = spec.get("hls_profile") if isinstance(spec.get("hls_profile"), dict) else {}  # 用于读取误差门限元数据的 HLS profile 段

    # metadata 子结构只在类型正确时继续下钻，避免异常输入把容差注释逻辑带偏。
    dict_metadata = dict_hls_profile.get("metadata") if isinstance(dict_hls_profile.get("metadata"), dict) else {}  # hls_profile 下的 metadata 子结构

    # 提取要直接透传到容差提示注释的原始阈值。
    raw_error_tolerance = dict_metadata.get("error_tolerance")  # 直接透传到容差提示注释的原始阈值

    # 非 FFT/CORDIC 或未声明误差门限时，不额外输出容差注释。
    if _example_pattern(spec) not in {"fft", "cordic"} or raw_error_tolerance in (None, "", [], {}):

        # 返回空字符串，保持无容差场景的 mock 主体简洁。
        return ""

    # 中文注释模式下输出面向当前项目的误差门限说明。
    if comment_language == "zh":

        # 返回中文误差门限注释文本。
        return f"{indent}// tolerance: FFT/CORDIC 定点输出比较使用 {raw_error_tolerance} 作为数值误差门限。"

    # 返回英文误差门限注释文本。
    return (
        f"{indent}// tolerance: keep the explicit numeric error threshold "
        f"{raw_error_tolerance} visible for FFT/CORDIC self-checks."
    )

# 解析 hls::stream 参数承载的数据 payload 类型，供局部包结构渲染复用。
def _stream_payload_type(argument: dict[str, Any]) -> str:
    """提取 stream 参数模板内部的 payload 类型。

    参数:
        argument: 描述 stream 参数类型与接口属性的参数字典。

    返回:
        `hls::stream<...>` 中尖括号内部的 payload 类型；无法解析时回退到 `ap_uint<32>`。
    """

    # 读取 stream 参数的原始类型文本，未声明时使用通用默认类型。
    str_raw_type = str(argument.get("type") or "hls::stream<ap_uint<32> >&")  # stream 参数的原始类型文本

    # 去掉 const 和引用修饰，便于稳定解析模板内部 payload 类型。
    str_cleaned_stream_type = str_raw_type.replace("const ", "").replace("&", "").strip()  # 归一化后的 stream 类型文本

    # 缺少模板尖括号时，回退到通用的 ap_uint<32> payload 类型。
    if "<" not in str_cleaned_stream_type or ">" not in str_cleaned_stream_type:

        # 返回默认 payload 类型，保持 mock stream 主体可编译。
        return "ap_uint<32>"

    # 返回模板尖括号内部的 payload 类型文本。
    return str_cleaned_stream_type[
        str_cleaned_stream_type.find("<") + 1 : str_cleaned_stream_type.rfind(">")
    ].strip()

# 解析 stream 参数用于声明局部变量的完整存储类型。
def _stream_storage_type(argument: dict[str, Any]) -> str:
    """提取 stream 参数的局部存储类型文本。

    参数:
        argument: 描述 stream 参数类型与接口属性的参数字典。

    返回:
        去掉 const 与引用修饰后的 stream 存储类型文本。
    """

    # 读取 stream 参数用于局部声明的原始类型文本。
    str_raw_type = str(argument.get("type") or "hls::stream<ap_uint<32> >&")  # 用于局部 stream 声明的原始类型文本

    # 返回去掉 const 与引用修饰后的 stream 存储类型文本。
    return str_raw_type.replace("const ", "").replace("&", "").strip()

# 渲染 input/output/scale/length 形态的 mock 向量用例。
def _mock_vector_scale_cases(
    spec: dict[str, Any],
    top: str,
    vectors: list[dict[str, Any]],
    comment_language: str,
) -> str:
    """渲染 input/output/scale/length 形态的 mock 向量用例。

    参数:
        spec: 描述 mock HLS 接口、模式与深度约束的规范字典。
        top: 要写入测试平台中的顶层函数名。
        vectors: 需要转成 reference case 的向量用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        可直接拼进 reference testbench 的 C++ 用例片段文本。
    """

    # scale 用例固定围绕 input、output 和 scale 三类端口展开，先建立按名称回查的参数索引。
    dict_arguments = _argument_lookup(spec)  # 供 scale 场景回查端口配置的参数映射

    # input 端口的 depth 决定 testbench 至少要给输入数组分配多少槽位。
    int_input_depth = _m_axi_depth(spec, dict_arguments.get("input", {}))  # input 数组声明需要满足的最小深度

    # output 端口的 depth 决定结果缓冲区至少要预留多少写回槽位。
    int_output_depth = _m_axi_depth(spec, dict_arguments.get("output", {}))  # 结果写回缓冲至少要覆盖 output 端口声明的容量

    # 合并 scale 场景输入输出端口里更严格的 depth 约束。
    int_interface_depth = max(int_input_depth, int_output_depth)  # 输入输出共享的接口深度下界

    # 解析 scale 场景 input 数组在 C++ testbench 中使用的存储类型。
    str_input_type = _argument_storage_type(dict_arguments.get("input", {}))  # input 端口的存储类型

    # output 端口的存储类型必须和 mock 顶层签名保持一致，比较时才不会引入额外类型偏差。
    str_output_type = _argument_storage_type(dict_arguments.get("output", {}))  # output 数组在 testbench 中使用的存储类型

    # 提取 scale 标量实参对应的值类型，保持构造表达式和接口声明一致。
    str_scale_type = _argument_value_type(dict_arguments.get("scale", {}))  # scale 参数的值类型

    # 收集 scale 场景里每个向量用例要输出的 C++ case 文本块。
    list_case_blocks: list[str] = []  # 逐个向量用例生成的 C++ 文本块

    # 逐个展开 scale 用例，把输入、缩放因子和输出比对逻辑写成独立 case。
    for dict_case in vectors:

        # 每个 scale 用例都把输入样本、缩放因子和逻辑长度放在 inputs 字典里统一读取。
        dict_inputs = dict_case.get("inputs", {})  # 当前 scale 用例的输入载荷

        # 把当前 scale 用例的输入样本转成浮点数组，保持 Python 与 C++ 字面量一致。
        list_input_values = [float(item) for item in dict_inputs.get("input", [])]  # 当前用例的输入向量

        # 读取当前 scale 用例声明的期望输出向量。
        list_expected_values = [  # 当前用例的期望输出向量
            float(item) for item in dict_case.get("expected_outputs", {}).get("output", [])  # oracle 提供的 output 样本
        ]

        # 解析当前 scale 用例要传入顶层函数的缩放因子。
        float_scale = float(dict_inputs.get("scale", 1))  # 当前用例的缩放因子

        # 解析当前 scale 用例顶层调用的逻辑长度。
        int_length = int(dict_inputs.get("length", len(list_input_values)))  # 当前用例的逻辑长度

        # 估算当前 scale 用例 input、output 和 expected 都能容纳的数组深度。
        int_array_depth = max(  # 当前用例的数组分配深度
            1,  # 兜底避免生成零长度数组声明
            int_interface_depth,  # 满足接口 depth 约束要求
            len(list_input_values),  # 容纳全部输入样本
            int_length,  # 容纳顶层调用声明的逻辑长度
            len(list_expected_values),  # 容纳全部期望输出样本
        )

        # 渲染当前 scale 用例 input 数组初始化需要的字面量文本。
        str_input_values_text = ", ".join(_literal_number(item) for item in list_input_values) or "0"  # 输入向量的字面量文本

        # 把期望输出向量转成 C++ 字面量，后续可以直接写进 reference 数组初始化。
        str_expected_values_text = (  # expected 数组初始化使用的字面量文本
            ", ".join(_literal_number(item) for item in list_expected_values) or "0"  # 逐项展开后的期望输出字面量
        )

        # 计算当前 scale 用例 expected 数组至少需要保留的观测长度。
        int_observed_bound = max(1, len(list_expected_values))  # 期望输出数组的最小观测长度

        # 先拆出用例编号，供双语标题共享同一个 case 标识。
        str_case_identifier = str(dict_case["id"])  # 当前 scale 用例标识

        # 英文标题强调这里会执行向量用例并比较 observed output。
        str_case_comment_en = (  # 当前 scale case 的英文执行说明
            f"Run vector case {str_case_identifier} "
            "and compare the observed output."
        )

        # 中文标题强调执行向量用例后要对真实输出做逐项比较。
        str_case_comment_zh = f"执行向量用例 {str_case_identifier} 并比较真实输出。"  # 当前 scale case 的中文执行说明

        # 按注释语言路由标题文本，避免把双语逻辑塞进超长模板行。
        str_case_header_comment = _comment(comment_language, str_case_comment_en, str_case_comment_zh)  # 当前 scale case 的标题注释

        # 这里把当前 scale 用例渲染成独立 C++ 校验块，后续统一拼接返回。
        list_case_blocks.append(f'''  {{
    // {str_case_header_comment}
    {str_input_type} input[{int_array_depth}] = {{{str_input_values_text}}};
    {str_output_type} output[{int_array_depth}] = {{}};
    const double expected[{int_observed_bound}] = {{{str_expected_values_text}}};
    {top}(input, output, {_constructor_expr(str_scale_type, float_scale)}, {int_length});
    bool pass = true;
    for (int i = 0; i < {int_length}; ++i) {{
      if ((double)output[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout
        << "{SEMANTIC_RESULT_TAG} {{\\"case_id\\":\\"{dict_case["id"]}\\","
        << "\\"status\\":\\""
        << (pass ? "PASS" : "FAIL")
        << "\\",\\"outputs\\":{{\\"output\\":[";
    for (int i = 0; i < {int_length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << (double)output[i];
    }}
    std::cout
        << "]}},\\"checkpoints\\":{{\\"length\\":{int_length},"
        << "\\"first_output\\":"
        << (double)output[0]
        << "}}}}\\n";
    if (!pass) failures++;
  }}''')

    # 按生成顺序拼回所有 scale case 代码块，供上层模板直接嵌入。
    return "\n".join(list_case_blocks)

# 渲染基础 input/output/length 场景的逐向量 reference 用例。
def _mock_input_output_cases(
    spec: dict[str, Any],
    top: str,
    vectors: list[dict[str, Any]],
    comment_language: str,
) -> str:
    """渲染 input/output/length 形态的基础向量用例。

    参数:
        spec: 描述 mock HLS 接口、模式与深度约束的规范字典。
        top: 要写入测试平台中的顶层函数名。
        vectors: 需要转成 reference case 的向量用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适合直接拼接进 reference testbench 的 C++ 用例文本。
    """

    # 先把一进一出场景用到的端口参数建成索引表，后面好统一回查 input 和 output 配置。
    dict_arguments = _argument_lookup(spec)  # input/output 场景的端口参数映射

    # 输入端口的 depth 下界直接决定 testbench 输入缓冲区的最小容量。
    int_input_depth = _m_axi_depth(spec, dict_arguments.get("input", {}))  # input 缓冲区需要满足的最小深度

    # 输出端口的 depth 下界决定结果缓冲区至少要预留多少写回槽位。
    int_output_depth = _m_axi_depth(spec, dict_arguments.get("output", {}))  # output 结果缓冲区的最小写回深度

    # 输入输出任一侧声明了更大的 depth，局部数组就必须按那个上界统一分配。
    int_interface_depth = max(int_input_depth, int_output_depth)  # input/output 共用的容量下界

    # 输入数组的局部类型必须和顶层签名一致，避免 reference case 自己引入额外转换。
    str_input_type = _argument_storage_type(dict_arguments.get("input", {}))  # input 局部数组声明使用的类型

    # 输出数组的局部类型也要贴合顶层签名，防止比较阶段被隐式类型转换干扰。
    str_output_type = _argument_storage_type(dict_arguments.get("output", {}))  # 让 reference 观测数组沿用顶层输出端口的声明类型

    # 这里缓存每个基础向量用例生成的独立代码块，结尾再按顺序拼接。
    list_case_blocks: list[str] = []  # 待汇总的 input or output 用例代码块

    # 逐个展开基础 input/output 用例，把数组初始化和逐元素比较逻辑写成独立 case。
    for dict_case in vectors:

        # 当前用例的 inputs 字典同时携带输入样本和逻辑 length，后面统一从这里取值。
        dict_inputs = dict_case.get("inputs", {})  # 当前基础 input or output 用例的输入字段

        # 先把 Python 侧输入样本归一成浮点列表，后续才能稳定渲染成 C++ 字面量数组。
        list_input_values = [float(item) for item in dict_inputs.get("input", [])]  # 当前用例的输入浮点序列

        # 这里同步抽出期望输出序列，后面的 observed 比对会逐项对齐它。
        list_expected_values = [  # 当前用例的期望输出浮点序列
            float(item) for item in dict_case.get("expected_outputs", {}).get("output", [])  # oracle 声明的 output 样本
        ]

        # expected 为空时也要保留 1 个槽位，避免生成非法的零长度 C++ 数组声明。
        int_observed_bound = max(1, len(list_expected_values))  # expected 数组声明使用的最小观测长度

        # 如果用例没有显式 length，就退回到输入样本数，让 reference case 仍能形成有效顶层调用。
        int_length = int(dict_inputs.get("length", len(list_input_values)))  # 顶层调用实际使用的逻辑长度

        # 估算当前基础 input/output 用例的数组分配深度。
        int_array_depth = max(  # 同时覆盖接口约束、输入样本、期望输出和逻辑长度的数组容量上界
            1,  # 防止生成零长度数组
            int_interface_depth,  # 满足接口声明的容量门槛
            len(list_input_values),  # 装下全部输入样本
            int_observed_bound,  # 覆盖全部期望输出槽位
            int_length,  # 覆盖逻辑 length 指定的访问范围
        )

        # 把输入样本渲染成数组字面量，供模板里的 input 初始化直接复用。
        str_input_values_text = ", ".join(_literal_number(item) for item in list_input_values) or "0"  # input 数组的字面量文本

        # 把期望输出渲染成数组字面量，供模板里的 expected 常量直接使用。
        str_expected_values_text = (  # 供 C++ expected 常量数组直接内联初始化的字面量串
            ", ".join(_literal_number(item) for item in list_expected_values) or "0"  # 按顺序展开的 expected 常量文本
        )

        # 先取出当前用例编号，后面的双语标题和输出 JSON 都会复用它。
        str_case_identifier = str(dict_case["id"])  # 当前基础向量用例标识

        # 英文标题重点说明这里是基础 input/output 路径的真实输出比对。
        str_case_comment_en = (  # 当前基础 case 的英文执行说明
            f"Run vector case {str_case_identifier} "
            "and compare the observed output."
        )

        # 中文标题要强调这里比较的是实际写回 output，而不是中间缓冲内容。
        str_case_comment_zh = f"执行向量用例 {str_case_identifier} 并比较真实输出。"  # 当前基础 case 的中文执行说明

        # 按注释语言挑选 case 标题，避免把双语路由逻辑挤进模板正文。
        str_case_header_comment = _comment(comment_language, str_case_comment_en, str_case_comment_zh)  # 当前基础 case 的标题注释

        # 这里把当前基础 input or output 用例渲染成独立 C++ 校验块。
        list_case_blocks.append(f'''  {{
    // {str_case_header_comment}
    {str_input_type} input[{int_array_depth}] = {{{str_input_values_text}}};
    {str_output_type} output[{int_array_depth}] = {{}};
    const double expected[{int_observed_bound}] = {{{str_expected_values_text}}};
    {top}(input, output, {int_length});
    bool pass = true;
    for (int i = 0; i < {int_observed_bound}; ++i) {{
      if ((double)output[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout
        << "{SEMANTIC_RESULT_TAG} {{\\"case_id\\":\\"{dict_case["id"]}\\","
        << "\\"status\\":\\""
        << (pass ? "PASS" : "FAIL")
        << "\\",\\"outputs\\":{{\\"output\\":[";
    for (int i = 0; i < {int_observed_bound}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << (double)output[i];
    }}
    std::cout
        << "]}},\\"checkpoints\\":{{\\"length\\":{int_length},"
        << "\\"first_output\\":"
        << (double)output[0]
        << "}}}}\\n";
    if (!pass) failures++;
  }}''')

    # 基础 input/output 场景保持向量顺序返回，便于 reference case 与输入 JSON 一一对应。
    return "\n".join(list_case_blocks)

# 渲染 rows/cols 形态的二维块变换用例，供 DATAFLOW 模式 reference testbench 复用。
def _mock_block_transform_cases(
    spec: dict[str, Any],
    top: str,
    vectors: list[dict[str, Any]],
    comment_language: str,
) -> str:
    """渲染 block-transform 场景的 rows/cols 向量用例。

    参数:
        spec: 描述 mock HLS 接口、模式与深度约束的规范字典。
        top: 要写入测试平台中的顶层函数名。
        vectors: 需要转成 reference case 的二维块变换用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        可直接写入 reference testbench 的 block-transform C++ 文本块。
    """

    # block-transform 场景需要同时回查二维输入输出端口配置，先建立按名称索引的参数表。
    dict_arguments = _argument_lookup(spec)  # 让 rows/cols 场景按 input/output 两路端口名读取深度与类型约束

    # 输入端口 depth 决定二维输入缓冲至少要能容纳多少样本。
    int_input_depth = _m_axi_depth(spec, dict_arguments.get("input", {}))  # block-transform 输入缓冲的最小深度

    # 输出端口 depth 决定二维观测缓冲至少要能容纳多少结果样本。
    int_output_depth = _m_axi_depth(spec, dict_arguments.get("output", {}))  # block-transform 输出缓冲的最小深度

    # 合并二维 block-transform 场景输入输出的深度约束。
    int_interface_depth = max(int_input_depth, int_output_depth)  # 二维输入缓冲和输出缓冲都必须满足的联合容量下界

    # 二维输入数组的局部声明类型必须和 mock 顶层签名匹配，避免案例本身产生额外类型偏差。
    str_input_type = _argument_storage_type(dict_arguments.get("input", {}))  # block-transform 输入数组的存储类型

    # 二维输出数组的局部声明类型也要跟顶层签名匹配，后续比较才只反映内核行为。
    str_output_type = _argument_storage_type(dict_arguments.get("output", {}))  # block-transform 输出数组的存储类型

    # 收集二维 block-transform 场景里每个用例生成的 C++ 文本块。
    list_case_blocks: list[str] = []  # block-transform 场景的 C++ 用例文本块

    # 逐个展开 block-transform 用例，把二维布局恢复和输出比较逻辑写成独立 case。
    for dict_case in vectors:

        # 每个 block-transform 用例都把矩阵样本、尺寸和其他标量放在 inputs 字典里统一解析。
        dict_inputs = dict_case.get("inputs", {})  # 当前二维样本、行列尺寸和其他标量字段的统一输入载荷

        # 转成当前二维 block-transform 用例的输入样本向量。
        list_input_values = [float(item) for item in dict_inputs.get("input", [])]  # 当前用例的输入样本

        # 读取当前二维 block-transform 用例声明的期望输出样本向量。
        list_expected_values = [  # 当前用例的期望输出样本
            float(item) for item in dict_case.get("expected_outputs", {}).get("output", [])  # 期望向量给出的二维变换结果样本
        ]

        # rows 决定二维输入如何切片回矩阵布局，缺省时退回单行模式保持 case 可执行。
        int_rows = int(dict_inputs.get("rows", 1))  # 当前用例的逻辑行数

        # 解析当前二维 block-transform 用例的逻辑列数。
        int_cols = int(dict_inputs.get("cols", len(list_input_values)))  # 当前用例的逻辑列数

        # 估算当前二维 block-transform 用例至少需要容纳的样本总数。
        int_total_samples = max(  # 当前用例的样本总数下界
            1,  # 即使输入为空也保留一个合法的本地数组长度
            int_rows * int_cols,  # 还原矩阵布局至少需要的样本数
            len(list_input_values),  # 覆盖真实输入向量已经给出的全部样本
            len(list_expected_values),  # 覆盖 oracle 输出向量要比较的全部样本
        )

        # 计算当前二维 block-transform 用例 expected 数组至少需要的观测长度。
        int_observed_bound = max(1, len(list_expected_values))  # observed 与 expected 逐项比对时至少要保留的输出槽位数

        # 计算当前二维 block-transform 用例的数组分配深度。
        int_array_depth = max(1, int_interface_depth, int_total_samples)  # 同时满足接口深度与二维样本总量的本地数组容量

        # 渲染当前二维 block-transform 用例 input 数组初始化需要的字面量文本。
        str_input_values_text = ", ".join(_literal_number(item) for item in list_input_values) or "0"  # 输入样本的字面量文本

        # 当前 block-transform 用例要先把 oracle 输出压成一行数组初始化文本。
        str_expected_values_text = (  # 期望样本的字面量文本
            ", ".join(_literal_number(item) for item in list_expected_values) or "0"  # 写入 expected 数组初始化的逗号分隔字面量串
        )

        # 先单独渲染用例注释，避免直接把长 `_comment(...)` 表达式塞进三引号模板。
        str_case_comment = _comment(  # 当前 block-transform 用例的执行说明注释
            comment_language,  # 当前 block-transform 用例的注释语言
            f"Run block-transform case {dict_case['id']} and compare the staged DATAFLOW output.",  # 英文标题强调 staged DATAFLOW 输出比对
            f"执行二维块变换用例 {dict_case['id']} 并比较分段 DATAFLOW 输出。",  # 中文标题强调二维块输出会按分段结果比对
        )

        # 追加当前二维 block-transform 用例对应的 C++ 校验文本块。
        list_case_blocks.append(f'''  {{
    // {str_case_comment}
    {str_input_type} input[{int_array_depth}] = {{{str_input_values_text}}};
    {str_output_type} output[{int_array_depth}] = {{}};
    const double expected[{int_observed_bound}] = {{{str_expected_values_text}}};
    {top}(input, output, {int_rows}, {int_cols});
    bool pass = true;
    for (int i = 0; i < {int_observed_bound}; ++i) {{
      if ((double)output[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout
        << "{SEMANTIC_RESULT_TAG} {{\\"case_id\\":\\"{dict_case["id"]}\\","
        << "\\"status\\":\\""
        << (pass ? "PASS" : "FAIL")
        << "\\",\\"outputs\\":{{\\"output\\":[";
    for (int i = 0; i < {int_observed_bound}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << (double)output[i];
    }}
    std::cout
        << "]}},\\"checkpoints\\":{{\\"rows\\":{int_rows},\\"cols\\":{int_cols},"
        << "\\"first_output\\":"
        << (double)output[0]
        << "}}}}\\n";
    if (!pass) failures++;
  }}''')

    # 返回按 block-transform 向量顺序拼接好的全部 C++ case 文本。
    return "\n".join(list_case_blocks)

# 渲染双 m_axi 输入场景的 reference 用例，验证双通道访存主体输出。
def _mock_multi_m_axi_cases(
    spec: dict[str, Any],
    top: str,
    vectors: list[dict[str, Any]],
    comment_language: str,
) -> str:
    """渲染 multi-m_axi 场景的双输入向量用例。

    参数:
        spec: 描述 mock HLS 接口、模式与深度约束的规范字典。
        top: 要写入测试平台中的顶层函数名。
        vectors: 需要转成 reference case 的双输入向量用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        可直接写入 reference testbench 的 multi-m_axi C++ 用例片段。
    """

    # multi-m_axi 需要同时读取两路输入和一路输出端口定义，所以先建按名称索引的参数表。
    dict_arguments = _argument_lookup(spec)  # 后续按 input_a、input_b、output 三个固定键回查参数配置

    # input_a 通道的 depth 下界决定第一个输入缓冲至少要预留多少槽位。
    int_input_a_depth = _m_axi_depth(spec, dict_arguments.get("input_a", {}))  # 第一条 m_axi 输入缓冲至少要覆盖 input_a 端口声明的容量

    # 第二路输入可能声明了不同的 depth，下游数组分配必须单独记住这条下界。
    int_input_b_depth = _m_axi_depth(spec, dict_arguments.get("input_b", {}))  # 第二条 m_axi 输入缓冲至少要容纳的接口深度

    # output 通道的 depth 下界决定观测数组至少要给返回结果预留多少槽位。
    int_output_depth = _m_axi_depth(spec, dict_arguments.get("output", {}))  # 输出缓冲声明至少要满足的接口深度

    # 三个通道里只要有一个声明了更大的 depth，局部数组就必须按那个上界分配。
    int_interface_depth = max(  # 双输入单输出 case 共享的数组深度下界
        int_input_a_depth,  # input_a 通道声明要求的最小深度
        int_input_b_depth,  # 第二路输入可能把共享数组深度继续抬高
        int_output_depth,  # 输出端口的 depth 也可能成为主导上界
    )

    # 解析 multi-m_axi 场景 input_a 在 C++ case 中使用的存储类型。
    str_input_a_type = _argument_storage_type(dict_arguments.get("input_a", {}))  # input_a 通道的存储类型

    # 第二路输入局部数组同样要跟顶层签名一致，避免双通道 case 额外引入类型偏差。
    str_input_b_type = _argument_storage_type(dict_arguments.get("input_b", {}))  # input_b 在本地测试数组里采用的存储类型

    # 输出缓冲的局部类型也要贴合顶层签名，比较结果才只反映内核行为。
    str_output_type = _argument_storage_type(dict_arguments.get("output", {}))  # 让本地观测数组沿用顶层 output 端口的声明类型

    # 这里按向量顺序累积 multi-m_axi reference case 的 C++ 文本块。
    list_case_blocks: list[str] = []  # 每个 multi-m_axi 向量对应的一段独立 case 文本

    # 逐个展开双输入向量用例，保持 input_a、input_b 与 output 的对应关系清晰可见。
    for dict_case in vectors:

        # 当前 multi-m_axi 用例把两路输入样本和 length 都放在 inputs 里，这里先统一解包。
        dict_inputs = dict_case.get("inputs", {})  # 当前用例里双输入与长度字段的原始载荷

        # 先把 input_a 通道样本转成浮点列表，后续才能稳定渲染成 C++ 字面量数组。
        list_input_a_values = [float(item) for item in dict_inputs.get("input_a", [])]  # input_a 通道的浮点样本

        # 第二路输入单独保留自己的浮点列表，后面要独立渲染 input_b 数组。
        list_input_b_values = [float(item) for item in dict_inputs.get("input_b", [])]  # input_b 通道独立保留的浮点样本

        # 当前 case 的 oracle 输出稍后会逐项对齐 observed 数组，所以先整体取出。
        list_expected_values = [  # 这条 multi-m_axi 用例对应的 expected 输出样本
            float(item) for item in dict_case.get("expected_outputs", {}).get("output", [])  # 只读取 output 通道的 oracle 样本
        ]

        # 统计当前 multi-m_axi 用例 input_a 通道的样本数量。
        int_input_a_count = len(list_input_a_values)  # input_a 通道的样本数量

        # 第二路输入的样本数既影响 length 回退，也会影响数组深度估算。
        int_input_b_count = len(list_input_b_values)  # input_b 通道本轮实际提供的样本数量

        # 统计当前 multi-m_axi 用例期望输出的样本数量。
        int_expected_count = len(list_expected_values)  # 期望输出的样本数量

        # 缺少显式 length 时，回退到两路输入都具备样本的最短公共长度。
        int_length = int(dict_inputs.get("length", min(int_input_a_count, int_input_b_count)))  # 顶层调用在当前用例里实际采用的逻辑长度

        # 局部数组深度要同时满足接口契约、真实样本数和逻辑长度三类约束。
        int_array_depth = max(  # 当前用例生成本地数组声明时采用的统一深度
            1,  # 兜底避免生成零长度的 C++ 局部数组声明
            int_interface_depth,  # 先满足接口层声明的共享 depth 下界
            int_input_a_count,  # 必须能装下 input_a 这一路的全部样本
            int_input_b_count,  # 若 B 通道样本更长，这一项负责继续扩容本地数组深度
            int_length,  # 顶层调用可能显式声明比样本数更长的逻辑长度
            int_expected_count,  # expected 样本数量同样需要被完整容纳
        )

        # 渲染当前 multi-m_axi 用例 input_a 初始化数组需要的字面量文本。
        str_input_a_values_text = ", ".join(_literal_number(item) for item in list_input_a_values) or "0"  # input_a 向量的字面量文本

        # input_b 数组初始化文本与 input_a 分开渲染，便于直观看到双通道差异。
        str_input_b_values_text = ", ".join(_literal_number(item) for item in list_input_b_values) or "0"  # 写入 input_b 数组初始化的字面量序列

        # expected 数组也要预先转成一行文本，后面才能直接塞进 case 模板。
        str_expected_values_text = (  # expected 数组初始化需要的字面量文本
            ", ".join(_literal_number(item) for item in list_expected_values) or "0"  # expected 数组初始化时使用的逐项字面量串
        )

        # 即使 oracle 没给任何样本，也要保留一个安全长度让 expected 数组可声明。
        int_observed_bound = max(1, int_expected_count)  # `expected[]` 在当前 case 中采用的安全观测下界

        # 先生成双通道用例标题，明确当前 case 会同时核对 A/B 两路存储结果。
        str_case_comment = _comment(  # 双 m_axi 用例的双通道校验标题注释
            comment_language,  # multi-m_axi 用例的注释语言
            f"Run multi-m_axi case {dict_case['id']} and compare both memory channels.",  # 英文标题强调双通道输出会同时校验
            f"执行 multi-m_axi 用例 {dict_case['id']} 并比较两个存储通道。",  # 中文标题强调双路存储通道会一起比较
        )

        # 追加当前 multi-m_axi 用例对应的 C++ 校验文本块。
        list_case_blocks.append(f'''  {{
    // {str_case_comment}
    {str_input_a_type} input_a[{int_array_depth}] = {{{str_input_a_values_text}}};
    {str_input_b_type} input_b[{int_array_depth}] = {{{str_input_b_values_text}}};
    {str_output_type} output[{int_array_depth}] = {{}};
    const double expected[{int_observed_bound}] = {{{str_expected_values_text}}};
    {top}(input_a, input_b, output, {int_length});
    bool pass = true;
    for (int i = 0; i < {int_length}; ++i) {{
      if ((double)output[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout
        << "{SEMANTIC_RESULT_TAG} {{\\"case_id\\":\\"{dict_case["id"]}\\","
        << "\\"status\\":\\""
        << (pass ? "PASS" : "FAIL")
        << "\\",\\"outputs\\":{{\\"output\\":[";
    for (int i = 0; i < {int_length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << (double)output[i];
    }}
    std::cout
        << "]}},\\"checkpoints\\":{{\\"length\\":{int_length},"
        << "\\"first_output\\":"
        << (double)output[0]
        << "}}}}\\n";
    if (!pass) failures++;
  }}''')

    # 返回按 multi-m_axi 向量顺序拼好的全部 C++ case 文本。
    return "\n".join(list_case_blocks)

# 提取参数在局部数组或标量声明中使用的存储类型，供 reference case 的局部变量声明复用。
def _argument_storage_type(argument: dict[str, Any]) -> str:
    """提取参数在局部数组或标量声明中使用的存储类型。

    参数:
        argument: 描述参数类型与接口属性的参数字典。

    返回:
        去掉 `const`、指针和引用修饰后的存储类型文本。
    """

    # 先取出参数类型文本，后续统一去掉 `const`、指针和引用修饰。
    str_argument_type = str(argument.get("type") or "ap_uint<32>")  # 用于局部数组或标量声明的原始类型文本

    # 返回清洗后的局部存储类型文本。
    return _strip_cpp_storage_type(str_argument_type)

# 提取参数在构造表达式中使用的值类型，供标量实参拼装复用。
def _argument_value_type(argument: dict[str, Any]) -> str:
    """提取参数用于值构造时的基础类型文本。

    参数:
        argument: 描述参数类型与接口属性的参数字典。

    返回:
        适合用于构造表达式的基础类型文本；未声明时回退为 `int`。
    """

    # 这里先保留原始类型文本，后面值构造路径还要在去修饰前执行 `int` 回退。
    str_argument_type = str(argument.get("type") or "int")  # 用于构造表达式回退的原始类型文本

    # 返回清洗后的值类型文本。
    return _strip_cpp_storage_type(str_argument_type)

# 去掉 C++ 类型中的 const、volatile、引用和指针修饰，得到可直接声明的基础类型。
def _strip_cpp_storage_type(raw_type: str) -> str:
    """清洗 C++ 类型文本中的修饰符与引用指针符号。

    参数:
        raw_type: 原始的 C++ 类型文本。

    返回:
        去掉 const、volatile、`&` 和 `*` 后的基础类型文本；空结果时回退为 `int`。
    """

    # 先去掉 const 和 volatile 修饰，保留核心类型名。
    str_base_type = raw_type.replace("const ", "").replace("volatile ", "").strip()  # 去修饰后的基础类型文本

    # 继续去掉指针和引用符号，得到可直接声明的局部类型。
    str_storage_type = str_base_type.replace("&", "").replace("*", "").strip()  # 去掉引用和指针后的类型文本

    # 返回压缩空白后的最终类型文本。
    return " ".join(str_storage_type.split()) or "int"

# 按目标 C++ 类型渲染标量构造表达式，避免自定义类型直接写裸字面量。
def _constructor_expr(cpp_type: str, value: float) -> str:
    """生成给定 C++ 类型对应的标量构造表达式。

    参数:
        cpp_type: 需要构造的 C++ 目标类型文本。
        value: 要写入构造表达式的数值。

    返回:
        内建标量类型返回裸字面量，自定义类型返回 `type(literal)` 形式。
    """

    # 先把 Python 数值转成稳定的 C++ 字面量文本。
    str_literal = _literal_number(value)  # 当前数值的 C++ 字面量文本

    # 原生标量类型可以直接使用裸字面量。
    if cpp_type in {"int", "unsigned", "unsigned int", "long", "float", "double"}:

        # 返回可直接内联到调用参数中的裸字面量。
        return str_literal

    # 返回显式类型构造形式，保持自定义数值类型可编译。
    return f"{cpp_type}({str_literal})"

# 把 Python 浮点数转成稳定的 C++ 数字字面量文本。
def _literal_number(value: float) -> str:
    """把 Python 数值转成 mock C++ 文本中的稳定字面量。

    参数:
        value: 要写入 C++ 文本的 Python 数值。

    返回:
        整数值返回无小数点文本，其余返回 `repr(float(...))` 的稳定结果。
    """

    # 数值恰好为整数时，优先输出更紧凑的整数字面量。
    if float(value).is_integer():

        # 返回紧凑的整数字面量文本。
        return str(int(value))

    # 返回保留浮点语义的字面量文本。
    return repr(float(value))

# 渲染基础 AXI-Stream 场景的 reference 用例，覆盖标准流输入输出比对路径。
def _mock_axis_cases(top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    """渲染基础 AXI-Stream 场景的 reference 用例。

    参数:
        top: 要写入测试平台中的顶层函数名。
        vectors: 需要转成 reference case 的 AXI-Stream 用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适合直接写入 reference testbench 的 AXI-Stream C++ 文本块。
    """

    # 这里按输入向量顺序累积 AXI-Stream reference case 的 C++ 文本块。
    list_case_blocks: list[str] = []  # 依照向量顺序缓存的 AXI-Stream reference case 代码块

    # 逐个展开 AXI-Stream 用例，把输入流写入和输出比对逻辑生成到独立 case 里。
    for dict_case in vectors:

        # AXI-Stream 用例把 token 序列和 length 放在 inputs 里，这里先统一解包。
        dict_inputs = dict_case.get("inputs", {})  # 当前用例里输入 token 与长度字段的原始载荷

        # 先把 Python 侧 in_stream 样本转成整数列表，后续再渲染成逐 token 的写流语句。
        list_input_values = [int(item) for item in dict_inputs.get("in_stream", [])]  # 输入 stream 的整数样本序列

        # observed 数组后面只会和 out_stream 这一路对比，所以这里直接抽取对应 oracle。
        list_expected_values = [  # 当前 AXI-Stream 用例的 out_stream 期望样本
            int(item) for item in dict_case.get("expected_outputs", {}).get("out_stream", [])  # 仅保留 out_stream 对应的 oracle 样本
        ]

        # 缺少显式 length 时，用输入 token 数作为本轮顶层调用的默认长度。
        int_length = int(dict_inputs.get("length", len(list_input_values)))  # 顶层函数在当前用例里要消费的逻辑长度

        # 即便期望样本为空，也要给 `expected[]` 留一个可声明的最小长度。
        int_observed_bound = max(1, len(list_expected_values))  # expected 数组声明时采用的安全观测下界

        # 渲染当前 AXI-Stream 用例 expected 数组初始化需要的整数文本。
        str_expected_values_text = ", ".join(str(item) for item in list_expected_values) or "0"  # expected 数组初始化时使用的逗号分隔整数文本

        # 生成当前 AXI-Stream 用例逐项写入 in_stream 的 C++ 语句块。
        str_write_statements = "\n".join(  # 输入 stream 的写入语句块
            f"    in_stream.write(ap_uint<32>({int_value}));" for int_value in list_input_values  # 每个输入样本对应一条写流语句模板
        )

        # 先生成当前流式 case 的标题，强调这里只比较 out_stream 的观测结果。
        str_case_comment = _comment(  # AXI-Stream 输出比对标题注释
            comment_language,  # 用例说明文本要跟当前 mock 注释语言保持一致
            f"Run AXI-Stream case {dict_case['id']} and compare the observed output.",  # 英文标题强调 observed output 会被逐项比较
            f"执行 AXI-Stream 用例 {dict_case['id']} 并比较真实输出。",  # 中文标题强调当前流输出会逐项对比
        )

        # 把流写入、顶层调用和输出比对逻辑展开成当前 AXI-Stream case 的完整文本块。
        list_case_blocks.append(f'''  {{
    // {str_case_comment}
    hls::stream<ap_uint<32> > in_stream;
    hls::stream<ap_uint<32> > out_stream;
{str_write_statements}
    const unsigned expected[{int_observed_bound}] = {{{str_expected_values_text}}};
    unsigned observed[{max(1, int_length)}] = {{}};
    {top}(in_stream, out_stream, {int_length});
    bool pass = true;
    for (int i = 0; i < {int_length}; ++i) {{
      if (out_stream.empty()) {{
        pass = false;
        observed[i] = 0;
      }} else {{
        observed[i] = (unsigned)out_stream.read();
      }}
      if (observed[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout
        << "{SEMANTIC_RESULT_TAG} {{\\"case_id\\":\\"{dict_case["id"]}\\","
        << "\\"status\\":\\""
        << (pass ? "PASS" : "FAIL")
        << "\\",\\"outputs\\":{{\\"out_stream\\":[";
    for (int i = 0; i < {int_length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << observed[i];
    }}
    std::cout
        << "]}},\\"checkpoints\\":{{\\"length\\":{int_length},"
        << "\\"first_output\\":"
        << observed[0]
        << "}}}}\\n";
    if (!pass) failures++;
  }}''')

    # 把所有 AXI-Stream 用例按向量顺序拼接成一整段 C++ case 文本。
    return "\n".join(list_case_blocks)

# 渲染 AXIS RLE 场景的 reference 用例，覆盖 payload 与帧边界断言路径。
def _mock_rle_axis_cases(
    spec: dict[str, Any],
    top: str,
    vectors: list[dict[str, Any]],
    comment_language: str,
) -> str:
    """渲染 AXIS RLE 场景的 payload 与帧边界校验用例。

    参数:
        spec: 描述 mock HLS 接口、模式与 stream 类型的规范字典。
        top: 要写入测试平台中的顶层函数名。
        vectors: 需要转成 reference case 的 AXIS RLE 用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        适合直接拼接进 reference testbench 的 AXIS RLE C++ 文本块。
    """

    # 先把顶层实参转成按名称索引的表，便于 AXIS RLE 路径同时解析流口和标量口。
    dict_arguments = _argument_lookup(spec)  # AXIS RLE 用到的端口参数映射

    # 解析 AXIS RLE 场景输入 stream 在 C++ case 中使用的完整存储类型。
    str_in_stream_type = _stream_storage_type(dict_arguments.get("in_stream", {}))  # 输入 stream 的存储类型

    # 输出流的完整存储类型必须和 mock 顶层签名匹配，后续 empty/read 校验才不受声明差异干扰。
    str_out_stream_type = _stream_storage_type(dict_arguments.get("out_stream", {}))  # 输出 stream 的完整存储类型

    # 解析 AXIS RLE 场景输入 stream 包体使用的 payload 类型。
    str_in_payload_type = _stream_payload_type(dict_arguments.get("in_stream", {}))  # 输入 stream 的 payload 类型

    # 这里缓存每个 AXIS RLE 压缩用例的完整校验片段，最后统一回传给 reference testbench。
    list_case_blocks: list[str] = []  # AXIS RLE 压缩输出校验片段缓存

    # 逐个展开 AXIS RLE 用例，把分包输入和压缩后输出的 reference 校验逻辑写成独立 case。
    for dict_case in vectors:

        # 当前用例的 inputs 字典同时携带压缩前 token 序列和逻辑有效长度。
        dict_inputs = dict_case.get("inputs", {})  # 当前 AXIS RLE 用例的输入字段

        # 先把原始输入 token 归一成整数，后续构造 data 字段和 last 位都依赖它。
        list_input_values = [int(item) for item in dict_inputs.get("in_stream", [])]  # 输入 token 的整数序列

        # 这里抽出压缩后 oracle token，后面的 observed 只会逐项比较 payload.data。
        list_expected_values = [  # 按 payload.data 顺序比对 observed 输出时使用的期望压缩 token 序列
            int(item) for item in dict_case.get("expected_outputs", {}).get("out_stream", [])  # oracle 声明的压缩输出 token
        ]

        # length 决定哪些输入样本属于有效帧，不能直接假设等于输入数组长度。
        int_length = int(dict_inputs.get("length", len(list_input_values)))  # 当前用例的逻辑有效长度

        # expected 数组至少保留一个元素，避免空输出时生成非法的 C++ 数组声明。
        int_observed_bound = max(1, len(list_expected_values))  # expected 数组的安全长度下界

        # 把压缩后的 oracle token 摊平成 `expected[]` 初始化串，后面直接按 payload.data 顺序比较。
        str_expected_values_text = ", ".join(str(item) for item in list_expected_values) or "0"  # AXIS RLE payload 对比使用的 expected 初始化字面量串

        # 这里逐包缓存写流语句，便于同时写入 data、keep、strb 和 last 字段。
        list_write_lines: list[str] = []  # 当前用例的写流语句列表

        # 逐个输入样本展开成 AXIS 包，并按逻辑 length 计算每个包的帧尾标记。
        for int_index, int_value in enumerate(list_input_values):

            # 只有逻辑范围内的最后一个输入包才会被标记为 last=1。
            int_last_flag = 1 if int_index == max(0, int_length - 1) else 0  # 当前输入包的 last 位

            # 这里为当前输入包补齐 AXIS 字段，再把完整包对象压入输入流。
            list_write_lines.extend(
                [
                    f"    {str_in_payload_type} in_pkt_{int_index};",
                    f"    in_pkt_{int_index}.data = {int_value};",
                    f"    in_pkt_{int_index}.keep = -1;",
                    f"    in_pkt_{int_index}.strb = -1;",
                    f"    in_pkt_{int_index}.last = {int_last_flag};",
                    f"    in_stream.write(in_pkt_{int_index});",
                ]
            )

        # 把逐包写流语句拼成多行块，供 case 模板直接插入输入准备段。
        str_write_block = "\n".join(list_write_lines)  # 把 data、keep、strb、last 全部补齐后的输入流构造语句块

        # 先拆出 case 标识，供双语标题复用同一个用例编号。
        str_case_identifier = str(dict_case["id"])  # 供双语 case 标题和结果 JSON 共同复用的用例编号

        # 英文标题需要明确说明同时校验 payload 数值和 frame marker。
        str_case_comment_en = (  # 英文标题强调 payload 数值和帧尾标记都会被校验
            f"Run AXIS RLE case {str_case_identifier} "
            "and compare payload plus frame markers."
        )

        # 中文标题需要点出数据字段和帧边界标记会一起校验。
        str_case_comment_zh = f"执行 AXIS RLE 用例 {str_case_identifier} 并比较数据与帧边界标记。"  # 中文标题明确点出数据字段与帧边界会同时校验

        # 按注释语言路由标题文本，避免把双语逻辑挤进超长模板行。
        str_case_header_comment = _comment(comment_language, str_case_comment_en, str_case_comment_zh)  # 按注释语言路由后的 AXIS RLE case 标题

        # 这里把当前 AXIS RLE 用例渲染成独立代码块，后续统一拼接返回。
        list_case_blocks.append(f'''  {{
    // {str_case_header_comment}
    {str_in_stream_type} in_stream;
    {str_out_stream_type} out_stream;
{str_write_block}
    const unsigned expected[{int_observed_bound}] = {{{str_expected_values_text}}};
    unsigned observed[{max(1, int_length)}] = {{}};
    bool last_seen = false;
    {top}(in_stream, out_stream, {int_length});
    bool pass = true;
    for (int i = 0; i < {int_length}; ++i) {{
      if (out_stream.empty()) {{
        pass = false;
        observed[i] = 0;
      }} else {{
        auto out_pkt = out_stream.read();
        observed[i] = (unsigned)out_pkt.data;
        if (out_pkt.keep == 0 || out_pkt.strb == 0) {{
          pass = false;
        }}
        if (out_pkt.last != 0) {{
          last_seen = true;
        }}
      }}
      if (observed[i] != expected[i]) {{
        pass = false;
      }}
    }}
    if (!last_seen) {{
      pass = false;
    }}
    std::cout
        << "{SEMANTIC_RESULT_TAG} {{\\"case_id\\":\\"{dict_case["id"]}\\","
        << "\\"status\\":\\""
        << (pass ? "PASS" : "FAIL")
        << "\\",\\"outputs\\":{{\\"out_stream\\":[";
    for (int i = 0; i < {int_length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << observed[i];
    }}
    std::cout
        << "]}},\\"checkpoints\\":{{\\"length\\":{int_length},"
        << "\\"first_output\\":"
        << observed[0]
        << "}}}}\\n";
    if (!pass) failures++;
  }}''')

    # 返回时保留原始向量顺序，这样 AXIS RLE 的 payload/last 报告顺序才能和输入用例一一对应。
    return "\n".join(list_case_blocks)

# 渲染 task-graph AXI-Stream 场景的合并事务用例，保持一次顶层调用的 cosim 语义。
def _mock_task_graph_axis_cases(top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    """渲染 task-graph AXI-Stream 场景的分段校验用例。

    参数:
        top: 要写入测试平台中的顶层函数名。
        vectors: 需要转成 reference case 的 task-graph AXI-Stream 用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        使用一次顶层调用并分段校验输出切片的 C++ 文本块。
    """

    # 收集 task-graph 场景里要合并成一次事务的全部输入样本。
    list_all_input_values: list[int] = []  # 合并后的全部输入样本

    # 记录 task-graph 每个分段在统一观测数组中的切片信息。
    list_case_slices: list[dict[str, Any]] = []  # 各用例在统一观测数组中的切片信息

    # 维护当前 task-graph 分段写入合并输入流时的起始偏移。
    int_offset = 0  # 合并输入流中的当前偏移

    # 逐个展开 task-graph 分段用例，把各分段输入和期望输出先压进统一事务描述里。
    for dict_case in vectors:

        # 当前 task-graph 分段也统一从 inputs 里读取 token 序列与逻辑长度。
        dict_inputs = dict_case.get("inputs", {})  # 当前分段里输入 token 与长度字段的原始载荷

        # 先把当前分段的输入 token 转成整数列表，后续要合并进统一输入流。
        list_input_values = [int(item) for item in dict_inputs.get("in_stream", [])]  # 当前分段的输入样本序列

        # 当前分段的 oracle 输出稍后会映射到 observed 的一个窗口，因此这里先整体取出。
        list_expected_values = [  # 当前 task-graph 分段对应的期望输出样本
            int(item) for item in dict_case.get("expected_outputs", {}).get("out_stream", [])  # 当前分段的 oracle 输出样本
        ]

        # 没有显式 length 时，当前分段默认消费它实际提供的输入 token 数。
        int_length = int(dict_inputs.get("length", len(list_input_values)))  # 当前 task-graph 分段的逻辑长度

        # 把当前 task-graph 用例输入样本并入统一输入流。
        list_all_input_values.extend(list_input_values)

        # 把当前分段的切片元数据登记进列表，后续统一事务跑完后再逐段回放验证。
        list_case_slices.append(
            {
                "id": dict_case["id"],  # 分段校验报告里使用的 case 标识
                "length": int_length,  # 当前分段在统一事务中实际消费的 token 数
                "offset": int_offset,  # 当前分段输出在 observed 数组中的起始偏移
                "expected": list_expected_values,  # 供统一事务回放时按窗口比对的期望输出序列
            }
        )

        # 更新下一个 task-graph 分段在统一输入流中的起始偏移。
        int_offset += int_length  # 下一段从当前分段消费完的末尾位置继续累计

    # 记录统一 task-graph 顶层事务需要处理的总样本数。
    int_total_length = int_offset  # 合并事务中的总样本数

    # 生成统一 task-graph 输入流的逐样本写入语句块。
    str_write_statements = "\n".join(  # 合并输入流的写入语句块
        f"  in_stream.write(ap_uint<32>({int_value}));" for int_value in list_all_input_values  # 合并事务里每个样本对应一条写流语句
    )

    # 收集各个 task-graph 分段的输出校验文本块。
    list_case_blocks: list[str] = []  # task-graph 分段校验文本块

    # 统一事务结束后，再逐个回放各分段切片去验证 observed 数组里的对应窗口。
    for dict_case_slice in list_case_slices:

        # 把当前分段的期望输出渲染成字面量文本，后续可直接写进局部 expected 数组。
        str_expected_values_text = (  # 当前分段的 expected 数组字面量文本
            ", ".join(str(item) for item in dict_case_slice["expected"]) or "0"  # 当前分段的期望输出字面量序列
        )

        # 读取当前 task-graph 分段在统一观测数组中的起始偏移。
        int_start_offset = int(dict_case_slice["offset"])  # 当前分段的起始偏移

        # 读取当前 task-graph 分段的逻辑长度。
        int_length = int(dict_case_slice["length"])  # 当前分段在统一 observed 窗口里实际要比较的样本数

        # 计算当前 task-graph 分段 expected 数组至少需要的观测长度。
        int_observed_bound = max(1, len(dict_case_slice["expected"]))  # 当前分段的期望观测长度

        # 渲染当前 task-graph 分段对应的合并事务校验说明注释。
        str_slice_comment = _comment(  # 当前分段的一次合并事务校验注释
            comment_language,  # 分段校验注释也沿用当前 mock 渲染语言
            f"Validate task-graph slice {dict_case_slice['id']} after one combined kernel transaction.",  # 英文标题强调统一事务后再回放分段校验
            f"在一次合并 kernel 事务后校验 task-graph 分段 {dict_case_slice['id']}。",  # 中文标题强调分段结果来自同一次顶层事务
        )

        # 追加当前 task-graph 分段对应的 C++ 校验文本块。
        list_case_blocks.append(f'''  {{
    // {str_slice_comment}
    const unsigned expected[{int_observed_bound}] = {{{str_expected_values_text}}};
    bool pass = true;
    for (int i = 0; i < {int_length}; ++i) {{
      if (observed[{int_start_offset} + i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout
        << "{SEMANTIC_RESULT_TAG} {{\\"case_id\\":\\"{dict_case_slice["id"]}\\","
        << "\\"status\\":\\""
        << (pass ? "PASS" : "FAIL")
        << "\\",\\"outputs\\":{{\\"out_stream\\":[";
    for (int i = 0; i < {int_length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << observed[{int_start_offset} + i];
    }}
    std::cout
        << "]}},\\"checkpoints\\":{{\\"length\\":{int_length},"
        << "\\"first_output\\":"
        << observed[{int_start_offset}]
        << "}}}}\\n";
    if (!pass) failures++;
  }}''')

    # 所有分段校验块最终都会插入同一个 top-level case 模板，所以先合并成整段文本。
    str_case_blocks_text = "\n".join(list_case_blocks)  # 一次统一事务后的全部分段校验文本

    # 渲染 task-graph cosim 的单次顶层调用说明注释。
    str_task_graph_comment = _comment(  # task-graph 单次顶层调用说明注释
        comment_language,  # 顶层 task-graph 说明注释跟随当前 mock 输出语言
        "Task-graph cosim uses one top-level invocation so the task actor restart contract stays explicit.",  # 英文标题强调只保留一次顶层调用
        "task-graph cosim 只做一次顶层调用，以保持 task actor 的重启契约显式可控。",  # 中文标题强调单次调用是为了保持 actor 重启契约可见
    )

    # 返回 task-graph cosim 对应的完整 C++ 文本块。
    return f'''  // {str_task_graph_comment}
  hls::stream<ap_uint<32> > in_stream;
  hls::stream<ap_uint<32> > out_stream;
{str_write_statements}
  unsigned observed[{max(1, int_total_length)}] = {{}};
  bool stream_underflow = false;
  {top}(in_stream, out_stream, {int_total_length});
  for (int i = 0; i < {int_total_length}; ++i) {{
    if (out_stream.empty()) {{
      stream_underflow = true;
      observed[i] = 0;
    }} else {{
      observed[i] = (unsigned)out_stream.read();
    }}
  }}
{str_case_blocks_text}'''

# 渲染 free-running direct-I/O 单元场景的逐 token 调用用例。
def _mock_directio_unit_cases(top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    """渲染 free-running direct-I/O 场景的逐 token reference 用例。

    参数:
        top: 要写入测试平台中的顶层函数名。
        vectors: 需要转成 reference case 的 direct-I/O 用例列表。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        逐 token 调用内核并比较输出的 C++ 用例文本块。
    """

    # 这里缓存每个 direct-I/O reference case 的完整 C++ 片段，稍后统一拼接返回。
    list_case_blocks: list[str] = []  # 待拼接的 direct-I/O 用例代码块

    # 逐个展开 direct-I/O 用例，把逐 token 调用路径写成独立 reference case。
    for dict_case in vectors:

        # 先取出当前用例声明的输入载荷，后面只从这里读取 in_stream token。
        dict_inputs = dict_case.get("inputs", {})  # 当前用例的输入字段映射

        # 这里把 in_stream 原始样本转成整数，便于直接生成写流语句和调用次数。
        list_input_values = [int(item) for item in dict_inputs.get("in_stream", [])]  # 归一化后的输入 token 序列

        # 这里同步抽出 oracle 输出，保证 observed 比较使用相同的 token 顺序。
        list_expected_values = [  # 期望向量给出的 direct-I/O out_stream 目标序列
            int(item) for item in dict_case.get("expected_outputs", {}).get("out_stream", [])  # 直接从 oracle 结果里抽取要逐 token 比对的输出样本
        ]

        # 这里先缓存逐 token 的写流语句，再统一拼成模板片段需要的多行文本。
        list_write_lines = [  # 当前用例的逐 token 写流语句
            f"    in_stream.write(ap_uint<32>({int_value}));" for int_value in list_input_values  # 单个 token 的写流语句
        ]

        # 再把写流语句拼成多行文本，供 case 模板直接插入到生成的 C++ 代码块。
        str_write_statements = "\n".join(list_write_lines)  # 写入 in_stream 的 C++ 语句块

        # 先把 direct-I/O oracle 输出摊平成 `expected[]` 初始化串，逐次 kernel 调用后可直接按索引核对。
        str_expected_values_text = ", ".join(str(item) for item in list_expected_values) or "0"  # direct-I/O 逐 token 对比使用的 expected 初始化字面量串

        # 记录当前 direct-I/O 用例需要逐 token 调用的次数。
        int_token_count = len(list_input_values)  # 当前用例的 token 数量

        # 这里为 expected 数组保留至少一个元素，避免空数组在生成的 C++ 中非法。
        int_observed_bound = max(1, len(list_expected_values))  # 避免 direct-I/O 用例在空输出时生成非法 expected 数组

        # 先把原始 case_id 规范成字符串，标题文本和结果 JSON 都会复用这一个稳定标识。
        str_case_identifier = str(dict_case["id"])  # 标题与结果日志共用的稳定 case 标识

        # 这里准备英文标题，供英文注释模式把执行语义写到 case 头部。
        str_case_comment_en = (  # 英文标题突出逐 token 调用节奏
            f"Run free-running direct-I/O case {str_case_identifier} "
            "by invoking the kernel once per token."
        )

        # 中文标题要明确点出逐 token 调用节奏，避免中文模式下看不出执行粒度。
        str_case_comment_zh = f"逐 token 调用 free-running direct-I/O 内核以执行用例 {str_case_identifier}。"  # 中文标题明确点出逐 token 调用内核的执行方式

        # 最后按注释语言选择标题文本，供生成的 C++ 用例块直接复用。
        str_case_execution_comment = _comment(comment_language, str_case_comment_en, str_case_comment_zh)  # 双语 case 标题注释

        # 这里把当前用例渲染成独立代码块，后面统一汇总成 reference case 文本。
        list_case_blocks.append(f'''  {{
    // {str_case_execution_comment}
    hls::stream<ap_uint<32> > in_stream;
    hls::stream<ap_uint<32> > out_stream;
{str_write_statements}
    const unsigned expected[{int_observed_bound}] = {{{str_expected_values_text}}};
    unsigned observed[{max(1, int_token_count)}] = {{}};
    bool pass = true;
    for (int i = 0; i < {int_token_count}; ++i) {{
      {top}(in_stream, out_stream);
      if (out_stream.empty()) {{
        pass = false;
        observed[i] = 0;
      }} else {{
        observed[i] = (unsigned)out_stream.read();
      }}
      if (observed[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout
        << "{SEMANTIC_RESULT_TAG} {{\\"case_id\\":\\"{dict_case["id"]}\\","
        << "\\"status\\":\\""
        << (pass ? "PASS" : "FAIL")
        << "\\",\\"outputs\\":{{\\"out_stream\\":[";
    for (int i = 0; i < {int_token_count}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << observed[i];
    }}
    std::cout
        << "]}},\\"checkpoints\\":{{\\"token_count\\":{int_token_count},"
        << "\\"first_output\\":"
        << observed[0]
        << "}}}}\\n";
    if (!pass) failures++;
  }}''')

    # 统一按生成顺序拼回所有 case 片段，供上层模板直接嵌入 testbench。
    return "\n".join(list_case_blocks)

