"""为 mock provider 提供确定性的测试向量。"""

# 启用延迟注解，避免运行期解析类型标注。
from __future__ import annotations

# deepcopy 用于返回独立向量副本，避免调用方污染模块级样例表。
from copy import deepcopy

# Any 描述 mock 向量中的异构 JSON 字段。
from typing import Any

# _case 统一构造 mock 样例，避免在选择逻辑里堆叠大段字面量。
def _case(
    str_case_id: str,
    dict_inputs: dict[str, Any],
    dict_expected_outputs: dict[str, Any],
    dict_checkpoints: dict[str, Any],
) -> dict[str, Any]:
    """构造单个 mock 参考向量。

    参数:
        str_case_id: 样例标识，例如 case_nominal。
        dict_inputs: 样例输入端口映射。
        dict_expected_outputs: 样例期望输出端口映射。
        dict_checkpoints: 样例检查点映射。

    返回:
        可写入 vectors.json 的向量字典。
    """

    # 按固定键顺序组装样例字典，确保 mock 文本与 JSON 输出保持同一结构。
    return {
        "id": str_case_id,
        "inputs": dict_inputs,
        "expected_outputs": dict_expected_outputs,
        "checkpoints": dict_checkpoints,
    }

# 单输入加一族覆盖 FIR、FFT、CORDIC、prefix、task graph 和 host/kernel split。
MEMORY_INCREMENT_VECTORS = [  # 单输入 memory mock 向量集合
    _case(  # 三点输入覆盖最常见的递增路径
        "case_nominal",  # 标识常规递增路径
        {"input": [1, 2, 3], "length": 3},  # 提供三点输入并显式声明长度
        {"output": [2, 3, 4]},  # 期望每个元素在 mock 中统一加一
        {"length": 3, "first_output": 2},  # 用长度和首个输出约束结果形态
    ),
    _case(  # 零值与高值混合输入覆盖边界递增路径
        "case_boundary",  # 标识边界递增分支
        {"input": [0, 15], "length": 2},  # 提供双元素边界输入
        {"output": [1, 16]},  # 验证边界值在递增后仍保持顺序
        {"length": 2, "first_output": 1},  # 用边界长度和首个结果校验样例
    ),
]

# 短序列加一族保留历史 FIR/FFT 等模式的四元素 nominal 样例。
WIDE_INCREMENT_VECTORS = [  # 宽输入 memory mock 向量集合
    _case(  # 四元素输入对齐历史 FIR 与 FFT 基线
        "case_nominal",  # 标识宽口径主路径
        {"input": [1, 2, 3, 4], "length": 4},  # 提供四拍输入供宽口径模式复用
        {"output": [2, 3, 4, 5]},  # 维持宽输入模式下的逐项递增结果
        {"length": 4, "first_output": 2},  # 记录宽口径长度与首个输出
    ),
    _case(  # 双元素回退样例覆盖宽口径的最小边界
        "case_boundary",  # 标识宽口径边界路径
        {"input": [0, 7], "length": 2},  # 提供缩短后的宽输入边界序列
        {"output": [1, 8]},  # 观察宽口径回退到双元素后的递增结果
        {"length": 2, "first_output": 1},  # 用缩短长度和首个输出约束回退路径
    ),
]

# 双输入加法族覆盖 matmul 和通用 input_a/input_b mock。
PAIR_ADD_VECTORS = [  # 双输入逐元素相加 mock 向量集合
    _case(  # 两路三元素样例用于核对逐项配对相加
        "case_nominal",  # 标识双输入常规路径
        {"input_a": [1, 2, 3], "input_b": [4, 5, 6], "length": 3},  # 提供两路等长输入序列
        {"output": [5, 7, 9]},  # 逐项配对相加后得到期望输出
        {"length": 3, "first_output": 5},  # 用长度和首项和校验配对结果
    ),
    _case(  # 含零输入的双路样例覆盖配对加法边界
        "case_boundary",  # 标识双输入边界路径
        {"input_a": [9, 0], "input_b": [1, 7], "length": 2},  # 提供含零值的双路边界输入
        {"output": [10, 7]},  # 验证边界输入配对相加后的输出
        {"length": 2, "first_output": 10},  # 用双路边界长度和首项和校验结果
    ),
]

# 这一组专门观察 token 顺序在 AXI stream mock 中是否被原样推进。
STREAM_INCREMENT_VECTORS = [  # AXI stream mock 向量集合
    _case(  # 重复 token 序列便于观察流式顺序保持
        "case_nominal",  # 标识 AXI stream 常规路径
        {"in_stream": [1, 1, 2], "length": 3},  # 提供包含重复值的输入 stream
        {"out_stream": [2, 2, 3]},  # 输出流仍按 token 先后次序整体加一
        {"length": 3, "first_output": 2},  # 用长度和首个 token 校验流式结果
    ),
    _case(  # 双 token 边界样例观察 stream 递增回退路径
        "case_boundary",  # 标识 AXI stream 边界路径
        {"in_stream": [0, 15], "length": 2},  # 提供双 token 边界输入流
        {"out_stream": [1, 16]},  # 验证边界 token 递增后的输出顺序
        {"length": 2, "first_output": 1},  # 用 token 数量和首个输出锁定边界结果
    ),
]

# 这组 stencil 样例直接固化窗口结果，用来盯住邻域展开后的数值排列。
LINE_BUFFER_VECTORS = [  # line buffer 的窗口回归样例表
    _case(  # 四点窗口样例直接回归主路径结果
        "case_nominal",  # 标识 stencil 常规窗口路径
        {"input": [1, 2, 3, 4], "length": 4},  # 提供四点输入触发完整窗口展开
        {"output": [4, 6, 9, 11]},  # 固定窗口卷积后的参考输出序列
        {"length": 4, "first_output": 4},  # 记录窗口路径的输入长度和首个结果
    ),
    _case(  # 双元素窗口样例专门回归邻域边界行为
        "case_boundary",  # 标识 stencil 边界窗口路径
        {"input": [5, 1], "length": 2},  # 提供只够形成边界窗口的输入
        {"output": [11, 7]},  # 固定边界窗口的参考输出值
        {"length": 2, "first_output": 11},  # 用窗口长度和首个卷积值约束结果
    ),
]

# 归约树样例只保留最终收敛值，重点验证不同输入规模的汇聚终点。
REDUCTION_TREE_VECTORS = [  # reduction tree 的终值核对样例表
    _case(  # 四输入路径用于观察完整归约收敛
        "case_nominal",  # 标识归约树满宽路径
        {"input": [1, 2, 3, 4], "length": 4},  # 提供四个节点供归约树完整汇聚
        {"output": [10]},  # 把归约树的最终总和写成单元素输出
        {"length": 4, "first_output": 10},  # 用输入规模和归约终值锁定主路径
    ),
    _case(  # 单节点样例验证归约树的退化路径
        "case_boundary",  # 标识归约树单元素路径
        {"input": [9], "length": 1},  # 提供单节点输入触发退化归约
        {"output": [9]},  # 退化归约时输出应等于原始输入
        {"length": 1, "first_output": 9},  # 用单节点长度和首个值校验退化结果
    ),
]

# 这里不去模拟真实 GEMM 调度，而是用逐元素乘法结果锁住接口输出合同。
TILED_GEMM_VECTORS = [  # tiled GEMM 的乘法合同样例表
    _case(  # 四元素乘法样例覆盖主计算路径
        "case_nominal",  # 标识逐元素乘法主路径
        {"input_a": [1, 2, 3, 4], "input_b": [5, 6, 7, 8], "length": 4},  # 提供两路四元素输入模拟乘法合同
        {"output": [5, 12, 21, 32]},  # 直接写出逐元素乘法后的参考结果
        {"length": 4, "first_output": 5},  # 用长度和首个乘积约束输出形态
    ),
    _case(  # 含零乘法样例专门覆盖乘法合同边界
        "case_boundary",  # 标识逐元素乘法边界路径
        {"input_a": [2, 0], "input_b": [4, 9], "length": 2},  # 提供带零值的两路边界输入
        {"output": [8, 0]},  # 验证零值参与乘法后的边界输出
        {"length": 2, "first_output": 8},  # 用边界长度和首个乘积锁定结果
    ),
]

# 二维 dataflow 样例把形状元数据显式带进 checkpoint，方便核对网格参数直通。
DATAFLOW_VECTORS = [  # dataflow 的二维事务样例表
    _case(  # 二乘二网格样例覆盖标准二维事务
        "case_nominal",  # 标识二维网格常规路径
        {"input": [1, 2, 3, 4], "rows": 2, "cols": 2},  # 提供四点输入与二乘二网格参数
        {"output": [2, 3, 4, 5]},  # 输出值沿二维事务主路径整体加一
        {"rows": 2, "cols": 2, "first_output": 2},  # 保留网格尺寸与首个输出做验收锚点
    ),
    _case(  # 一行三列样例覆盖非方阵网格事务
        "case_boundary",  # 标识二维网格边界路径
        {"input": [5, 7, 9], "rows": 1, "cols": 3},  # 提供单行三列的边界网格参数
        {"output": [6, 8, 10]},  # 验证非方阵网格下的递增输出
        {"rows": 1, "cols": 3, "first_output": 6},  # 保留边界网格尺寸与首个输出
    ),
]

# 这个 free-run 组合完全靠 token_count 校验流长，故意避开旧式 length 合同。
DIRECTIO_FREERUN_VECTORS = [  # direct I/O 的 token 校验样例表
    _case(  # 三 token 序列覆盖 free-run 主路径
        "case_nominal",  # 标识 token 流常规路径
        {"in_stream": [1, 2, 3]},  # 提供三 token 输入流用于 free-run 校验
        {"out_stream": [2, 3, 4]},  # 输出 token 在顺序不变时统一递增
        {"token_count": 3, "first_output": 2},  # 用 token 数量和首个输出锁定结果
    ),
    _case(  # 双 token 样例覆盖 free-run 的边界流长
        "case_boundary",  # 标识 token 流边界路径
        {"in_stream": [0, 15]},  # 提供最小边界 token 输入流
        {"out_stream": [1, 16]},  # 验证边界 token 递增后的输出序列
        {"token_count": 2, "first_output": 1},  # 用 token 数量与首项值核对边界结果
    ),
]

# 缩放族覆盖 input/scale/length 端口组合。
SCALE_VECTORS = [  # 缩放 mock 向量集合
    _case(  # 倍率为二的样例覆盖显式 scale 输入
        "case_nominal",  # 标识缩放主路径
        {"input": [1, 2, 3], "scale": 2, "length": 3},  # 提供输入值、倍率和显式长度
        {"output": [2, 4, 6]},  # 期望输出体现倍率为二的缩放结果
        {"length": 3, "first_output": 2},  # 记录缩放路径的长度与首个输出
    ),
    _case(  # 零倍率样例覆盖 scale 路径的极限边界
        "case_boundary",  # 标识零倍率边界路径
        {"input": [9, 8, 7], "scale": 0, "length": 3},  # 提供非零输入与零倍率组合
        {"output": [0, 0, 0]},  # 验证零倍率会把全部输出压成零
        {"length": 3, "first_output": 0},  # 用长度和首个零值锁定边界缩放结果
    ),
]

# 默认 passthrough 样例用于缺少可识别端口组合的最小 mock。
DEFAULT_VECTORS = [  # 默认 passthrough mock 向量集合
    _case(  # 单值透传样例保持最小兜底合同
        "case_passthrough",  # 标识透传兜底路径
        {"value": 1},  # 提供最小单值输入
        {"value": 1},  # 输出值保持与输入一致
        {"value": 1},  # 检查点只验证透传值未被修改
    ),
]

# pattern 级向量绑定先于端口组合 fallback，确保专用样例优先生效。
PATTERN_VECTOR_BINDINGS = {  # example_pattern 到必需端口集合和向量集合的映射
    "fir": ({"input", "output", "length"}, WIDE_INCREMENT_VECTORS),  # FIR 关注定长顺序读写是否成立
    "fft": ({"input", "output", "length"}, WIDE_INCREMENT_VECTORS),  # FFT mock 只借用四元素输入出口合同
    "cordic": ({"input", "output", "length"}, WIDE_INCREMENT_VECTORS),  # CORDIC mock 保留标量更新接口形态
    "prefix_scan": ({"input", "output", "length"}, WIDE_INCREMENT_VECTORS),  # prefix scan 重点校验累积依赖的输出顺序
    "matmul": ({"input_a", "input_b", "output", "length"}, PAIR_ADD_VECTORS),  # matmul 复用双输入相加族
    "rle_axis": ({"in_stream", "out_stream", "length"}, STREAM_INCREMENT_VECTORS),  # rle_axis 复用 stream 递增族
    "line_buffer_stencil": (set(), LINE_BUFFER_VECTORS),  # line buffer 直接使用专用窗口样例
    "reduction_tree": (set(), REDUCTION_TREE_VECTORS),  # reduction tree 直接使用专用归约样例
    "tiled_gemm": (set(), TILED_GEMM_VECTORS),  # tiled GEMM 直接使用专用乘法样例
    "task_graph": ({"input", "output", "length"}, MEMORY_INCREMENT_VECTORS),  # task graph 主要验证节点串接后的递增结果
    "dataflow": ({"input", "output", "rows", "cols"}, DATAFLOW_VECTORS),  # dataflow 使用带网格元数据的样例
    "directio_freerun": ({"in_stream", "out_stream"}, DIRECTIO_FREERUN_VECTORS),  # direct I/O 使用 token 流样例
    "host_kernel_split": ({"input", "output", "length"}, MEMORY_INCREMENT_VECTORS),  # host/kernel split 只核对搬运后的递增合同
}

# 端口组合 fallback 处理未显式命名但接口形态已知的 mock 规格。
FALLBACK_VECTOR_BINDINGS = (  # 必需端口集合到向量集合的 fallback 顺序
    ({"input", "output", "scale", "length"}, SCALE_VECTORS),  # 先匹配显式 scale 端口组合
    ({"input_a", "input_b", "output", "length"}, PAIR_ADD_VECTORS),  # 再匹配双输入相加端口组合
    ({"in_stream", "out_stream", "length"}, STREAM_INCREMENT_VECTORS),  # 最后匹配 stream 递增端口组合
)

# _mock_vectors 根据规格选择确定性 mock 向量。
def _mock_vectors(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """根据 HLS 规格返回稳定的 mock 参考向量。

    参数:
        spec: HLS 生成规格，包含 workflow、hls_profile 和 interfaces 字段。

    返回:
        可用于 testbench 的向量列表。
    """

    # workflow 字段只有为 dict 时才读取内联 mock_vectors。
    dict_workflow = spec.get("workflow") if isinstance(spec.get("workflow"), dict) else {}  # workflow 配置映射

    # 用户显式给出的 mock_vectors 具有最高优先级。
    list_configured_vectors = dict_workflow.get("mock_vectors")  # workflow 中显式配置的向量列表

    # 命中用户自带向量时直接返回原始列表，保留对象身份和字段顺序。
    if isinstance(list_configured_vectors, list) and list_configured_vectors:

        # 直接沿用用户给出的向量对象，避免改写现成的样例布局。
        return list_configured_vectors

    # example_pattern 决定优先选择的专用样例族。
    str_pattern = _example_pattern(spec)  # 规范化后的 example_pattern

    # 参数名集合用于确认样例族是否和当前接口兼容。
    set_argument_names = _argument_names(spec)  # 顶层函数参数名集合

    # pattern 专用样例会覆盖通用端口组合 fallback。
    list_pattern_vectors = _vectors_for_pattern(str_pattern, set_argument_names)  # pattern 匹配得到的向量集合

    # 命中专用样例时复制模块常量，隔离调用方的后续修改。
    if list_pattern_vectors is not None:

        # 复制专用样例后再返回，防止上层修改污染共享常量。
        return _copy_vectors(list_pattern_vectors)

    # 未命名 pattern 仍可通过端口组合选择通用样例。
    list_fallback_vectors = _vectors_for_ports(set_argument_names)  # 端口组合匹配得到的向量集合

    # 端口组合命中时同样返回独立副本，保护 fallback 常量不被污染。
    if list_fallback_vectors is not None:

        # 复制 fallback 样例后再交给上层，保持后续匹配表稳定。
        return _copy_vectors(list_fallback_vectors)

    # 无法识别时使用最小 passthrough 样例。
    return _copy_vectors(DEFAULT_VECTORS)

# _argument_names 提取 HLS 顶层参数名集合。
def _argument_names(spec: dict[str, Any]) -> set[str]:
    """从规格 interfaces.arguments 中提取参数名。

    参数:
        spec: HLS 生成规格。

    返回:
        规格中声明的顶层函数参数名集合。
    """

    # interfaces 只有为 dict 时才继续读取 arguments。
    dict_interfaces = spec.get("interfaces") if isinstance(spec.get("interfaces"), dict) else {}  # 接口配置映射

    # arguments 只有为 list 时才扫描参数对象。
    list_arguments = dict_interfaces.get("arguments") if isinstance(dict_interfaces.get("arguments"), list) else []  # 参数声明列表

    # 用显式循环收集参数名，保持过滤条件和去重意图都可直接读懂。
    set_argument_names: set[str] = set()  # 去重后的参数名集合

    # 逐个检查参数对象，只接纳带 name 字段的字典条目。
    for dict_argument in list_arguments:

        # 只有合法命名字典才会进入结果集合。
        if isinstance(dict_argument, dict) and dict_argument.get("name"):

            # 把确认存在的参数名放进集合，供后续 pattern 和端口匹配使用。
            set_argument_names.add(str(dict_argument["name"]))

    # 向上游返回当前规格声明过的全部参数名。
    return set_argument_names

# _vectors_for_pattern 按 example_pattern 选择专用样例。
def _vectors_for_pattern(str_pattern: str, set_argument_names: set[str]) -> list[dict[str, Any]] | None:
    """返回指定 pattern 的兼容向量集合。

    参数:
        str_pattern: 规范化后的 example_pattern。
        set_argument_names: 顶层函数参数名集合。

    返回:
        兼容的向量集合；pattern 未命中或端口不兼容时返回 None。
    """

    # 未登记 pattern 交给端口组合 fallback。
    tuple_binding = PATTERN_VECTOR_BINDINGS.get(str_pattern)  # pattern 对应的端口约束和向量集合

    # 未命中专用 pattern 时交给后续端口组合规则兜底。
    if tuple_binding is None:

        # 用 None 标记当前 pattern 没有专用样例，交给 fallback 再判断。
        return None

    # 拆出必需端口集合和向量集合，便于兼容性判断。
    set_required_arguments, list_vectors = tuple_binding  # 当前 pattern 的端口约束和向量集合

    # 端口约束为空或已被当前规格满足时，可以直接采用专用样例。
    if not set_required_arguments or set_required_arguments.issubset(set_argument_names):

        # 返回当前 pattern 对应的专用样例集合，保持最细粒度匹配优先。
        return list_vectors

    # pattern 命中但端口不匹配时不强行套用样例。
    return None

# _vectors_for_ports 按端口组合选择通用样例。
def _vectors_for_ports(set_argument_names: set[str]) -> list[dict[str, Any]] | None:
    """根据端口名集合选择通用 fallback 向量。

    参数:
        set_argument_names: 顶层函数参数名集合。

    返回:
        匹配的 fallback 向量集合；未匹配时返回 None。
    """

    # 依次尝试 fallback 规则，保持优先级与常量声明顺序一致。
    for set_required_arguments, list_vectors in FALLBACK_VECTOR_BINDINGS:

        # 当前端口集合覆盖所需参数时立即采用这组样例。
        if set_required_arguments.issubset(set_argument_names):

            # 一旦命中 fallback 约束就立即返回，避免后续规则覆盖既定优先级。
            return list_vectors

    # 所有 fallback 都未命中。
    return None

# _copy_vectors 返回向量深拷贝，保护模块级常量。
def _copy_vectors(list_vectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """复制 mock 向量集合。

    参数:
        list_vectors: 模块级向量常量或调用方传入的向量集合。

    返回:
        可由调用方安全修改的独立向量集合。
    """

    # 深拷贝返回值，避免调用方修改共享的模块级样例表。
    return deepcopy(list_vectors)

# _example_pattern 从规格中读取并规范化示例模式名称。
def _example_pattern(spec: dict[str, Any]) -> str:
    """读取规格中的 example_pattern。

    参数:
        spec: HLS 生成规格。

    返回:
        小写并把短横线替换成下划线后的 pattern 名称。
    """

    # hls_profile 字段只有为 dict 时才参与 pattern 解析。
    dict_profile = spec.get("hls_profile") if isinstance(spec.get("hls_profile"), dict) else {}  # example_pattern 的主配置映射

    # workflow 里的 pattern 只在旧规格缺少 hls_profile 配置时承担兼容兜底角色。
    dict_workflow = spec.get("workflow") if isinstance(spec.get("workflow"), dict) else {}  # 兼容旧规格的 workflow 片段

    # hls_profile 优先，workflow 作为兼容旧规格的 fallback。
    str_pattern = dict_profile.get("example_pattern") or dict_workflow.get("example_pattern") or ""  # 原始 pattern 名称

    # 统一归一化大小写和连接符格式，方便后续直接查 pattern 绑定表。
    return str(str_pattern).strip().lower().replace("-", "_")
