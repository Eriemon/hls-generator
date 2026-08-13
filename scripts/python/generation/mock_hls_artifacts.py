"""渲染 mock provider 使用的 HLS 源码、测试平台和配置文件。"""

# 启用延迟求值注解，避免类型提示在导入阶段提前展开。
from __future__ import annotations

# 导入正则、路径和宽泛类型提示，支撑 mock HLS 文本拼装过程。
import re
from pathlib import Path
from typing import Any

# 导入注释渲染器和模式工具，供 mock 代码生成阶段复用仓库契约。
from .mock_comment_rendering import _comment
from .mock_hls_contract_text import m_axi_depth_for_argument
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

    # source body 子模块负责按 pattern 选择 kernel 主体模板。
    from . import mock_hls_source_blocks as source_blocks

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
    str_body_text = source_blocks._mock_hls_body(spec)  # 顶层函数主体文本

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

# 根据模式生成 mock HLS helper 函数，给 DATAFLOW、task_graph 等分支补齐局部骨架。
def _mock_hls_helpers_text(spec: dict[str, Any], comment_language: str) -> str:
    """渲染 mock HLS helper 函数文本。

    参数:
        spec: 描述 mock HLS 接口、模式与 pragma 约束的规范字典。
        comment_language: 生成 C++ 注释时使用的注释语言标识。

    返回:
        主函数前需要插入的 helper 函数字符串；若当前模式不需要 helper，则返回空串。
    """

    # helper block 子模块负责各 pattern 的 dataflow/task_graph 局部模板。
    from . import mock_hls_helper_blocks as helper_blocks

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
        return helper_blocks._mock_matmul_dataflow_helpers(comment_language)

    # staged FIR 也需要显式的 read、compute、write helper 才能承载顶层 DATAFLOW。
    if str_pattern_name == "fir" and _requires_dataflow_pragma(spec):

        # 返回 FIR DATAFLOW 的三阶段 helper 函数集合。
        return helper_blocks._mock_fir_dataflow_helpers(comment_language)

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
        return helper_blocks._mock_block_dataflow_helpers(comment_language)

    # 当模式进入 task_graph 时，需要再区分 memory 版和 axis 版 helper。
    if str_pattern_name == "task_graph":

        # 如果是 memory 输入输出的 task_graph，则渲染 load/compute/store helper。
        if {"input", "output", "length"}.issubset(set_argument_names):

            # 返回 memory 接口版 task_graph 所需的 helper 组合。
            return helper_blocks._mock_task_graph_memory_helpers(
                str_kernel_name,
                str_stream_name,
                str_result_stream_name,
                comment_language,
            )

        # 返回 AXIS task_graph 场景的 seed/read/compute/write actor 骨架。
        return helper_blocks._mock_task_graph_axis_helpers(
            str_kernel_name,
            str_stream_name,
            str_result_stream_name,
            comment_language,
        )

    # 对普通流式 DATAFLOW 分支，输出单中间流三段式 read/compute/write helper。
    return helper_blocks._mock_stream_dataflow_helpers(
        str_kernel_name,
        str_stream_name,
        str_result_stream_name,
        comment_language,
    )

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

    # testbench 子模块负责按接口组合选择样例主体模板。
    from . import mock_hls_testbench as testbench_blocks

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
    str_body = testbench_blocks._select_mock_hls_testbench_body(  # 当前接口组合对应的 testbench 主体
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
    std::cout << "> ERR: [HLS] FAIL workflow_static_smoke\\n";
    return 1;
  }}
  std::cout << "> INFO: [HLS] PASS workflow_static_smoke\\n";
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

    # 先固定当前 top function 是否存在 m_axi，供 AXI-Lite 控制 bundle 归一化。
    list_arguments = _argument_dicts(spec)  # 当前顶层函数的接口参数列表

    # 统计当前 top function 是否包含 memory-mapped 主口。
    bool_has_m_axi = any(dict_argument.get("interface") == "m_axi" for dict_argument in list_arguments)  # 当前 top function 是否含 m_axi

    # 识别当前 spec 对应的模式名称。
    str_pattern_name = _example_pattern(spec)  # 顶层函数 pragma 的模式名称

    # 读取 spec 是否显式要求 DATAFLOW，避免只按 pattern 名称猜测作用域。
    bool_requires_dataflow_pragma = _requires_dataflow_pragma(spec)  # 当前 spec 是否声明了顶层 DATAFLOW

    # 识别需要把 DATAFLOW 留在 top、把 PIPELINE 留给 actor 的模式。
    bool_is_dataflow_pattern = (  # DATAFLOW 模式的顶层作用域标记
        str_pattern_name in {  # 先检查已知的 DATAFLOW pattern 名称
        "dataflow",  # 普通 DATAFLOW 模式名称
        "task_graph",  # 任务图模式名称
        "streamofblocks",  # stream-of-blocks 模式名称
        }
        or bool_requires_dataflow_pragma  # spec 显式声明 DATAFLOW 时同样按顶层模式处理
    )

    # 先为每个参数渲染接口 pragma。
    for dict_argument in list_arguments:

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

            # Vitis kernel 要求含 m_axi 的 top function 统一使用 control bundle。
            if bool_has_m_axi:

                # 给普通标量控制口显式补上与 m_axi 地址口相同的 bundle。
                str_pragma_line = f"{str_pragma_line} bundle=control"  # 统一 AXI-Lite 控制 bundle

        # 把当前参数的接口 pragma 写入列表。
        list_pragma_lines = [*list_pragma_lines, str_pragma_line]  # 已收集的接口 pragma 行

        # Vitis kernel flow 需要把 m_axi pointer 的 offset 注册到统一 control bundle。
        if str_interface_name == "m_axi":

            # pointer control 端口与 m_axi 地址端口成对出现，避免 v++ 把 ap_uint pointer 当成 opaque struct。
            str_pointer_control_pragma = f"#pragma HLS INTERFACE s_axilite port={dict_argument['name']} bundle=control"  # m_axi pointer 对应的 control pragma 文本

            # 把 m_axi pointer 的 control pragma 追加到接口 pragma 列表。
            list_pragma_lines = [*list_pragma_lines, str_pointer_control_pragma]  # 已追加 m_axi pointer 的 control pragma

    # 渲染控制接口 pragma，默认走 s_axilite。
    str_control_pragma = "#pragma HLS INTERFACE " f"{spec.get('interfaces', {}).get('control', 's_axilite')} port=return"  # 顶层函数控制接口 pragma 行

    # 含 m_axi 的 Vitis kernel 还必须把 return 控制口放进同一个 control bundle。
    if bool_has_m_axi and str_control_pragma.endswith("port=return"):

        # 防止 return 隐式落到 control_r，写入 Vitis kernel 唯一控制组。
        str_control_pragma = f"{str_control_pragma} bundle=control"  # 防止 return 产生第二个 AXI-Lite 控制组

    # 把控制接口 pragma 追加到列表末尾。
    list_pragma_lines = [*list_pragma_lines, str_control_pragma]  # 已追加控制接口的 pragma 行集合

    # DATAFLOW 类模式需要显式追加 DATAFLOW pragma。
    if bool_is_dataflow_pattern:

        # 把 DATAFLOW pragma 写入列表。
        list_pragma_lines = [*list_pragma_lines, "#pragma HLS DATAFLOW"]  # 已补齐 DATAFLOW 的 pragma 行集合

    # 非 DATAFLOW 模式且要求 pipeline 时，补默认 PIPELINE pragma。
    if (
        spec.get("pipeline_required", True)
        and not bool_is_dataflow_pattern
    ):

        # 把默认 PIPELINE pragma 追加到列表末尾。
        list_pragma_lines = [*list_pragma_lines, "#pragma HLS PIPELINE II=1"]  # 已补齐默认 PIPELINE 的 pragma 行集合

    # 预先缓存已经落盘的 pragma 指纹，后面只对首次出现的新条目追加输出。
    set_seen_pragma_keys = set(  # 已落盘 pragma 的去重指纹集合
        _pragma_identity(str_pragma_line) for str_pragma_line in list_pragma_lines  # 为每条 pragma 生成去重身份键
    )

    # 合并 spec 额外要求的 pragma，同时跳过重复和非顶层作用域 pragma。
    for str_required_pragma in _required_pragmas(spec):

        # 顶层 DATAFLOW 与 actor 局部 PIPELINE 不能同时挂在 top function 上。
        bool_is_local_pipeline = (  # 当前 pragma 是否属于 dataflow actor 局部流水线
            bool_is_dataflow_pattern  # 当前模式是否把 DATAFLOW 放在 top function
            and str_required_pragma.lstrip().startswith("#pragma HLS PIPELINE")  # 仅识别 spec 中的 actor 流水线声明
        )

        # 局部 variable pragma 与 dataflow actor 的 PIPELINE 由 body/helper 模板负责。
        if "variable=" in str_required_pragma or bool_is_local_pipeline:

            # 跳过非顶层 pragma，避免把 actor 约束错误提升到 top function。
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

    # 统一复用合同层深度解析器，保证 pragma 与 testbench 局部数组使用同一上界。
    return m_axi_depth_for_argument(spec, argument)

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
