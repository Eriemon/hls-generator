"""收拢 mock HLS testbench 主体选择与样例分派逻辑。"""

# 启用延迟求值注解，避免类型提示在导入阶段提前展开。
from __future__ import annotations

# 宽泛类型提示和 pattern 识别工具负责支撑 testbench 样例分派分支。
from typing import Any
from .mock_vectors import _example_pattern

# 根模块提供 testbench 顶层入口需要共享的参数视图和语义标记工具。
from .mock_hls_artifacts import _argument_name_set, _mock_tolerance_marker, _top_function_name

# vector case 子模块负责各 pattern 的样例文本拼装。
from . import mock_hls_vector_cases as vector_cases

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
        return vector_cases._mock_task_graph_axis_cases(top_function_name, vectors, comment_language)

    # rle_axis 需要覆盖字节包到字包的特定编码路径。
    if str_pattern_name == "rle_axis":

        # 返回 rle_axis 的编码路径专用驱动体。
        return vector_cases._mock_rle_axis_cases(spec, top_function_name, vectors, comment_language)

    # 其他带 length 的 axis 场景统一回退到通用流模板。
    return vector_cases._mock_axis_cases(top_function_name, vectors, comment_language)

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
        return vector_cases._mock_directio_unit_cases(top_function_name, vectors, comment_language)

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
        return vector_cases._mock_multi_m_axi_cases(spec, top_function_name, vectors, comment_language)

    # 向量缩放组合需要验证 scale 参数是否参与计算。
    if {"input", "output", "scale", "length"}.issubset(argument_names):

        # 返回 scale 参数参与计算的驱动体。
        return vector_cases._mock_vector_scale_cases(spec, top_function_name, vectors, comment_language)

    # 二维 block 组合需要覆盖行列维度下的访存变换逻辑。
    if {"input", "output", "rows", "cols"}.issubset(argument_names):

        # 返回二维 block 访存变换的驱动体。
        return vector_cases._mock_block_transform_cases(spec, top_function_name, vectors, comment_language)

    # 普通 input/output/length 组合使用线性内存向量模板。
    if {"input", "output", "length"}.issubset(argument_names):

        # 返回线性内存读写路径的驱动体。
        return vector_cases._mock_input_output_cases(spec, top_function_name, vectors, comment_language)

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
