"""生成 mock HLS testbench 的局部绑定、case 标记与向量哈希合同。"""

# 启用延迟注解，避免类型提示在导入阶段过早求值。
from __future__ import annotations

# 正则负责从原始 testbench 文本里提取 case 标记和向量哈希。
import re
from typing import Any

# 向量哈希标签需要和现有 mock provider 输出保持一致。
from scripts.python.generation.vectors import VECTOR_HASH_TAG

# 合同层统一提供 depth 合同文本。
from .mock_hls_contract_text import depth_text_for_argument

# 协议层统一提供顶层参数枚举。
from .mock_hls_protocols import argument_dicts

# 从原始 testbench 文本里提取 case 标记，便于治理后继续保留样例边界。
def raw_case_ids(raw_text: str) -> list[str]:
    """从原始 testbench 文本中提取 PASS/FAIL case 标记。

    参数:
        raw_text: 原始 mock testbench 文本，dtype=str，unit=text。

    返回:
        按源码顺序提取到的 case 标识列表，dtype=list[str]，unit=case ids。
    """

    # 返回所有 `// case PASS FAIL` 风格标记中的 case 名称。
    return re.findall(r"//\s*([A-Za-z0-9_]+)\s+PASS FAIL", raw_text)

# 从原始 testbench 文本里提取 VECTOR_HASH，供治理后继续写回契约。
def raw_vector_hash(raw_text: str) -> str:
    """从原始 testbench 文本中提取 VECTOR_HASH 文本。

    参数:
        raw_text: 原始 mock testbench 文本，dtype=str，unit=text。

    返回:
        命中的 vector hash 文本；缺失时返回空字符串，dtype=str，unit=hash text。
    """

    # 通过固定标签匹配 64 位十六进制哈希。
    obj_match = re.search(rf"{re.escape(VECTOR_HASH_TAG)}\s+([0-9a-fA-F]{{64}})", raw_text)  # 原始文本里的向量哈希匹配结果

    # 命中时返回 hash 文本，否则返回空字符串。
    return obj_match.group(1) if obj_match else ""

# 为 testbench 局部缓冲生成更具体的数组初始化和注释，避免回落到模板化 smoke 说明。
def pointer_binding_lines(
    spec: dict[str, Any],
    dict_argument: dict[str, Any],
    str_parameter_name: str,
) -> tuple[list[str], str]:
    """为指针端口生成 testbench 局部数组声明与调用参数名。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        str_parameter_name: 当前 testbench 使用的治理后参数名，dtype=str，unit=identifier name。

    返回:
        局部声明行列表与调用参数名组成的二元组，dtype=tuple[list[str], str]，unit=declaration lines and call argument。
    """

    # 读取当前指针端口的原始名字。
    str_original_name = str(dict_argument.get("name") or "").strip()  # 当前指针端口的原始名称

    # 读取当前指针端口的方向字段。
    str_direction = str(dict_argument.get("direction") or "input")  # 当前指针端口的方向字段

    # 读取当前指针端口的类型文本。
    str_argument_type = str(dict_argument.get("type") or "int")  # 当前指针端口的类型文本

    # testbench 中统一把指针端口落成本地数组，便于观察内核对窗口的读写行为。
    str_array_name = pointer_array_name(str_parameter_name)  # 当前指针端口对应的本地数组名

    # depth 合同优先复用 spec 里的明确值，缺失时按默认 smoke 窗口长度回退。
    str_depth_literal = pointer_depth_literal(spec, dict_argument)  # 当前 testbench 局部数组使用的字面量长度

    # 输入数组用首元素非零样本覆盖真实读路径。
    if str_direction == "input":

        # matmul 的 A 路输入窗口要显式说明它承接左操作数样本。
        if str_original_name == "input_a":

            # 返回 A 路输入窗口的局部声明行与调用参数名。
            return (
                [
                    "    // arr_input_a 预装 `input_a` 的 A 路输入窗口，让内核首个 blocked tile 先读到一个可手算的左操作数样本。",
                    (
                        "    "
                        f"{str_argument_type.replace('*', '').strip()} "
                        f"{str_array_name}[{str_depth_literal}] = {{1}}; "
                        "// 首个 A 样本固定为 1，方便核对左操作数是否被正确搬进局部 tile。"
                    ),
                ],
                str_array_name,
            )

        # matmul 的 B 路输入窗口在这里强调右操作数列片段会怎样预装到本地数组。
        if str_original_name == "input_b":

            # 这里返回 B 路预装数组声明，并把“右操作数配对列”这个角色写进尾注。
            return (
                [
                    "    // arr_input_b 预装 `input_b` 的 B 路配对窗口，专门给首个 blocked tile 提供可手算的右操作数并验证双输入配对路径。",
                    (
                        "    "
                        f"{str_argument_type.replace('*', '').strip()} "
                        f"{str_array_name}[{str_depth_literal}] = {{1}}; "
                        "// 首个 B 样本固定为 1，方便确认右操作数会和 A 路样本在同一 lane 配对相加。"
                    ),
                ],
                str_array_name,
            )

        # 通用输入窗口分支在这里保留最小 smoke 装载语义，不再复用 A/B 专用表述。
        return (
            [
                (
                    f"    // {str_array_name} 预装 `{str_original_name}` 的输入读窗口，"
                    "让内核在首个事务里拿到一个可手算的原始样本。"
                ),
                (
                    "    "
                    f"{str_argument_type.replace('*', '').strip()} "
                    f"{str_array_name}[{str_depth_literal}] = {{1}}; "
                    "// 首元素固定为 1，方便把缩放结果与输入读路径直接对应起来。"
                ),
            ],
            str_array_name,
        )

    # 输出数组专门观察 top function 是否真的把结果写回目标窗口。
    return (
        [
            (
                f"    // {str_array_name} 接住 `{str_original_name}` 的输出写回窗口，"
                "方便 testbench 只核对内核是否写出了缩放结果。"
            ),
            (
                f"    {str_argument_type.replace('*', '').strip()} {str_array_name}[{str_depth_literal}] = {{0}}; "
                "// 先把输出窗口清零，后面更容易识别 top function 是否发生了有效写回。"
            ),
        ],
        str_array_name,
    )

# 统一生成指针端口对应的本地数组名，避免局部命名分支散落在主逻辑中。
def pointer_array_name(str_parameter_name: str) -> str:
    """统一生成指针端口对应的本地数组名。

    参数:
        str_parameter_name: 当前治理后的参数名，dtype=str，unit=identifier name。

    返回:
        当前 testbench 局部数组名，dtype=str，unit=identifier name。
    """

    # ptr_ 端口优先替换成 arr_。
    if str_parameter_name.startswith("ptr_"):

        # 返回与指针参数一一对应的数组名。
        return str_parameter_name.replace("ptr_", "arr_", 1)

    # 不是 ptr_ 前缀时仍然显式补 arr_，保证 testbench 局部缓冲语义可读。
    return f"arr_{str_parameter_name}"

# 把 depth 合同转换成 testbench 局部数组可以直接使用的长度字面量。
def pointer_depth_literal(
    spec: dict[str, Any],
    dict_argument: dict[str, Any],
) -> str:
    """把 depth 合同转换成 testbench 局部数组长度字面量。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        当前局部数组长度字面量，dtype=str，unit=array length literal。
    """

    # 先读取当前指针端口的 depth 合同文本。
    str_depth = depth_text_for_argument(spec, dict_argument)  # 当前指针端口的 depth 合同文本

    # 合同里已经是纯数字时直接复用原值。
    if str_depth.isdigit():

        # 返回可直接写进数组声明的数字字面量。
        return str_depth

    # 合同未显式写成数字时回退到稳定的 smoke 默认长度。
    return "256"

# 为 testbench 标量控制口生成更具体的初始化和注释，避免“占位值”模板语义。
def scalar_binding_lines(
    dict_argument: dict[str, Any],
    str_parameter_name: str,
) -> tuple[list[str], str]:
    """为标量端口生成 testbench 局部标量声明与调用参数名。

    参数:
        dict_argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        str_parameter_name: 当前 testbench 使用的治理后参数名，dtype=str，unit=identifier name。

    返回:
        局部声明行列表与调用参数名组成的二元组，dtype=tuple[list[str], str]，unit=declaration lines and call argument。
    """

    # 读取当前标量端口的原始名字。
    str_original_name = str(dict_argument.get("name") or "").strip()  # 当前标量端口的原始名称

    # 读取当前标量端口的类型文本。
    str_argument_type = str(dict_argument.get("type") or "int")  # 当前标量端口的类型文本

    # scale 因子要显式给非零值，确保 mock smoke 覆盖实际乘法路径。
    if "scale" in str_original_name:

        # 返回缩放因子的局部绑定结果。
        return scale_binding_lines(str_argument_type, str_original_name, str_parameter_name)

    # 向量长度要显式限制成单事务，保持当前 smoke 输出易于人工核算。
    if "length" in str_original_name:

        # 返回事务长度的局部绑定结果。
        return length_binding_lines(str_argument_type, str_original_name, str_parameter_name)

    # 二维块的行数控制口要显式保留“单行事务”语义，避免和列宽控制口复用同一句模板。
    if "rows" in str_original_name or str_parameter_name == "int_rows":

        # 返回行数控制口的局部绑定结果。
        return (
            [
                (
                    f"    // {str_parameter_name} 固定复用 `{str_original_name}` 的行数 smoke 控制量，"
                    "让二维块 helper 链路看到最小但非零的行向事务跨度。"
                ),
                (
                    f"    {str_argument_type} {str_parameter_name} = 1; "
                    "// 先把块高度锁成 1 行，便于把当前静态 smoke 收敛到单行事务边界。"
                ),
            ],
            str_parameter_name,
        )

    # 二维块的列宽控制口要显式保留“单列事务”语义，避免和行数控制口继续撞上相似注释。
    if "cols" in str_original_name or str_parameter_name == "int_cols":

        # 返回列宽控制口的局部绑定结果。
        return (
            [
                (
                    f"    // {str_parameter_name} 固定复用 `{str_original_name}` 的列宽 smoke 控制量，"
                    "让二维块写回路径沿一个最小列向跨度推进。"
                ),
                (
                    f"    {str_argument_type} {str_parameter_name} = 1; "
                    "// 先把块列宽锁成 1 列，方便把当前静态 smoke 约束成单列写回窗口。"
                ),
            ],
            str_parameter_name,
        )

    # 其他标量端口默认给 1，并明确这是为了让控制路径具备非零触发条件。
    return (
        [
            f"    // {str_parameter_name} 复用 `{str_original_name}` 的标量控制边界，让当前 smoke 事务保留一个明确的非零控制输入。",
            f"    {str_argument_type} {str_parameter_name} = 1; // 默认控制值取 1，确保当前静态 smoke 的控制路径不是空载状态。",
        ],
        str_parameter_name,
    )

# 为缩放因子控制口生成非零初始化，确保最小 smoke 覆盖真实乘法路径。
def scale_binding_lines(
    str_argument_type: str,
    str_original_name: str,
    str_parameter_name: str,
) -> tuple[list[str], str]:
    """为缩放因子控制口生成非零初始化。

    参数:
        str_argument_type: 当前标量端口的类型文本，dtype=str，unit=type text。
        str_original_name: 当前标量端口的原始名称，dtype=str，unit=identifier name。
        str_parameter_name: 当前治理后的参数名，dtype=str，unit=identifier name。

    返回:
        当前缩放因子的局部声明行与调用参数名，dtype=tuple[list[str], str]，unit=declaration lines and call argument。
    """

    # ap_* 类型需要显式构造；普通标量直接使用整数字面量即可。
    str_scale_value = f"{str_argument_type}(2)" if "ap_" in str_argument_type else "2"  # 当前缩放因子的非零初始化值

    # 返回缩放因子的局部声明行与调用参数名。
    return (
        [
            f"    // {str_parameter_name} 固定复用 `{str_original_name}` 的运行时缩放因子，让 smoke 事务覆盖真实乘法路径而不是零乘法旁路。",
            f"    {str_argument_type} {str_parameter_name} = {str_scale_value}; // 把缩放因子锁定为 2，方便把输入样本 1 对应到输出样本 2。",
        ],
        str_parameter_name,
    )

# 为事务长度控制口生成单事务初始化，避免静态 smoke 越界。
def length_binding_lines(
    str_argument_type: str,
    str_original_name: str,
    str_parameter_name: str,
) -> tuple[list[str], str]:
    """为事务长度控制口生成单事务初始化。

    参数:
        str_argument_type: 当前标量端口的类型文本，dtype=str，unit=type text。
        str_original_name: 当前标量端口的原始名称，dtype=str，unit=identifier name。
        str_parameter_name: 当前治理后的参数名，dtype=str，unit=identifier name。

    返回:
        当前事务长度的局部声明行与调用参数名，dtype=tuple[list[str], str]，unit=declaration lines and call argument。
    """

    # 返回事务长度的局部声明行与调用参数名。
    return (
        [
            f"    // {str_parameter_name} 固定复用 `{str_original_name}` 的事务长度，让静态 smoke 只覆盖首个有效索引。",
            f"    {str_argument_type} {str_parameter_name} = 1; // 把有效长度锁定为 1，避免当前 smoke 读写超出已经准备好的首个样本。",
        ],
        str_parameter_name,
    )

# 为 stream 顶层端口生成最小局部 stream 实例，供静态 smoke 直接调用。
def stream_binding_lines(
    str_argument_type: str,
    str_original_name: str,
    str_parameter_name: str,
) -> tuple[list[str], str]:
    """为 stream 顶层端口生成最小局部 stream 实例。

    参数:
        str_argument_type: 当前 stream 端口的类型文本，dtype=str，unit=type text。
        str_original_name: 当前 stream 端口的原始名称，dtype=str，unit=identifier name。
        str_parameter_name: 当前治理后的参数名，dtype=str，unit=identifier name。

    返回:
        当前 stream 端口的局部声明行与调用参数名，dtype=tuple[list[str], str]，unit=declaration lines and call argument。
    """

    # 同时观察原始名和治理后名，兼容 `in_stream/out_stream` 与 `stream_in_stream/stream_out_stream` 两类命名。
    str_binding_hint = f"{str_original_name} {str_parameter_name}".casefold()  # 当前 stream 端口的联合语义提示文本

    # 输入流端口在 testbench 中负责把样本 token 注入 top function。
    if (
        "input" in str_binding_hint
        or "stream_in" in str_binding_hint
        or str_binding_hint.startswith("in_")
        or " in_stream" in str_binding_hint
    ):

        # 返回输入流端口的局部实例声明。
        return (
            [
                f"    // {str_parameter_name} 承载静态 smoke 注入 top function 的输入流通道。",
                f"    {str_argument_type.replace('&', '').strip()} {str_parameter_name}; // 本地流实例向 kernel 提供待处理样本。",
            ],
            str_parameter_name,
        )

    # 输出流端口在 testbench 中负责接住 top function 送出的结果 token。
    if (
        "output" in str_binding_hint
        or "stream_out" in str_binding_hint
        or str_binding_hint.startswith("out_")
        or " out_stream" in str_binding_hint
    ):

        # 这里专门返回输出流端口的本地观测实例，强调它只负责承接 kernel 写出的 token。
        return (
            [
                f"    // {str_parameter_name} 承载静态 smoke 观测 top function 的输出流通道。",
                (
                    "    "
                    f"{str_argument_type.replace('&', '').strip()} "
                    f"{str_parameter_name}; // 本地流实例接住 kernel 写出的结果 token。"
                ),
            ],
            str_parameter_name,
        )

    # 其他 stream 端口回退到保守的局部流边界说明。
    return (
        [
            f"    // {str_parameter_name} 承载当前静态 smoke 的局部流通道边界。",
            f"    {str_argument_type.replace('&', '').strip()} {str_parameter_name}; // 当前 top function 复用的最小局部流实例。",
        ],
        str_parameter_name,
    )

# 为单个顶层参数生成 testbench 绑定行，统一分发到指针、stream 或标量路径。
def binding_for_argument(
    spec: dict[str, Any],
    dict_argument: dict[str, Any],
    str_parameter_name: str,
    str_argument_type: str,
) -> tuple[list[str], str]:
    """为单个顶层参数生成 testbench 局部绑定。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        str_parameter_name: 当前治理后的参数名，dtype=str，unit=identifier name。
        str_argument_type: 当前顶层参数的类型文本，dtype=str，unit=type text。

    返回:
        当前顶层参数的局部声明行与调用参数名，dtype=tuple[list[str], str]，unit=declaration lines and call argument。
    """

    # 指针参数在 testbench 中落成局部数组。
    if "*" in str_argument_type:

        # 返回指针端口的局部数组绑定结果。
        return pointer_binding_lines(spec, dict_argument, str_parameter_name)

    # stream 参数在 testbench 中落成局部 stream 实例。
    if "hls::stream<" in str_argument_type:

        # 读取当前 stream 端口的原始名称，供输入流/输出流角色判断复用。
        str_original_name = str(dict_argument.get("name") or "").strip()  # 当前 stream 端口的原始名称

        # 返回 stream 端口的局部实例绑定结果。
        return stream_binding_lines(str_argument_type, str_original_name, str_parameter_name)

    # 其余标量参数按控制角色生成更具体的初始化和注释。
    return scalar_binding_lines(dict_argument, str_parameter_name)

# 生成 testbench 的局部参数声明区和调用参数列表。
def testbench_argument_bindings(
    spec: dict[str, Any],
    dict_argument_names: dict[str, str],
) -> tuple[list[str], list[str]]:
    """生成 testbench 的局部参数声明区和调用参数列表。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument_names: 顶层参数原名到治理名的映射字典，dtype=dict[str, str]，unit=name map。

    返回:
        局部声明行列表和调用参数列表组成的二元组，dtype=tuple[list[str], list[str]]，unit=declarations and call arguments。
    """

    # 初始化局部声明区的输出行列表。
    list_declaration_lines: list[str] = []  # testbench 局部声明区的输出行列表

    # 初始化 top function 调用时使用的参数名列表。
    list_call_arguments: list[str] = []  # top function 调用时使用的参数名列表

    # 逐个顶层参数渲染最小可调用的局部变量或缓冲。
    for dict_argument in argument_dicts(spec):

        # 读取当前顶层参数的原名。
        str_original_name = str(dict_argument.get("name") or "").strip()  # 当前顶层参数的原始名称

        # 空名称参数不参与 testbench 局部绑定。
        if not str_original_name:

            # 没有有效名字的端口不应生成局部变量或调用参数。
            continue

        # 读取当前参数的治理后名称。
        str_parameter_name = dict_argument_names.get(str_original_name, str_original_name)  # 当前 testbench 中的治理后参数名

        # 读取当前顶层参数的类型文本。
        str_argument_type = str(dict_argument.get("type") or "int")  # 当前顶层参数的类型文本

        # 先生成当前参数的局部声明行与调用参数名。
        tuple_binding = binding_for_argument(spec, dict_argument, str_parameter_name, str_argument_type)  # 当前参数的 testbench 绑定结果

        # 把当前局部声明行追加到声明区。
        list_declaration_lines.extend(tuple_binding[0])

        # 把当前调用参数名追加到 top function 调用列表。
        list_call_arguments.append(tuple_binding[1])

        # 不同参数之间追加一层空行，保持声明区可读。
        list_declaration_lines.append("")

    # 去掉声明区末尾多余的空行，避免函数体尾部出现连续空白段。
    trim_trailing_blank_lines(list_declaration_lines)

    # 返回局部声明行列表和调用参数列表。
    return list_declaration_lines, list_call_arguments

# 原地去掉声明区末尾连续空行，避免 testbench 函数体尾部残留空白段。
def trim_trailing_blank_lines(list_lines: list[str]) -> None:
    """原地去掉列表末尾的连续空行。

    参数:
        list_lines: 需要修剪的物理行列表，dtype=list[str]，unit=line list。

    返回:
        无返回；直接原地修改 `list_lines`，dtype=None，unit=not applicable。
    """

    # 只要尾部还是空行，就继续原地弹出。
    while list_lines and not list_lines[-1].strip():

        # 弹掉尾部空行，给调用方留下紧凑的声明区。
        list_lines.pop()
