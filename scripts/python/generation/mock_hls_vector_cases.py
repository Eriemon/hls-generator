"""收拢 mock HLS 向量样例与期望结果片段的渲染逻辑。"""

# 启用延迟求值注解，避免类型提示在导入阶段提前展开。
from __future__ import annotations

# 宽泛类型提示和注释渲染器负责支撑 case 文本中的局部语义说明。
from typing import Any
from .mock_comment_rendering import _comment

# 根模块继续提供 case 渲染共用的参数视图、数值格式化和语义 transcript 标签。
from .mock_hls_artifacts import (
    SEMANTIC_RESULT_TAG,
    _argument_lookup,
    _argument_storage_type,
    _argument_value_type,
)

# 数值构造辅助函数负责把 Python 侧样例值转成稳定的 C++ 文本。
from .mock_hls_artifacts import (
    _constructor_expr,
    _literal_number,
    _m_axi_depth,
)

# stream 相关辅助函数负责复用 AXIS 与 task_graph 样例的 payload/staging 约束。
from .mock_hls_artifacts import (
    _stream_payload_type,
    _stream_storage_type,
)

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

        # 先把 rows/cols checkpoint 字段拆成独立片段，避免把整段 JSON 键串写成单个字面量。
        str_rows_checkpoint_field = '\\"' + "rows" + f'\\":{int_rows}'  # 供结果串把当前用例的行数写进 checkpoint 键值。

        # 再单独拼 cols checkpoint 字段，确保 JSON 断言锁定的是当前用例的列维度。
        str_cols_checkpoint_field = '\\"' + "cols" + f'\\":{int_cols}'  # 供结果串把当前用例的列跨度锁进 checkpoint 键值。

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
        << "]}},\\"checkpoints\\":{{{str_rows_checkpoint_field},"
        << "{str_cols_checkpoint_field},"
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
