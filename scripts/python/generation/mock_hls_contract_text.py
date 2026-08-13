"""生成 mock HLS 的文件级、函数级与端口级合同文本。"""

# 启用延迟注解，避免类型提示在导入阶段过早求值。
from __future__ import annotations

# 宽泛函数信息类型用于兼容轻量 C/C++ 解析器返回对象。
from typing import Any

# pattern 头文件规则负责补齐 mock header 的依赖集合。
from scripts.python.generation.patterns import required_pattern_headers

# 协议层统一提供顶层参数列表和 top function 名称。
from .mock_hls_protocols import argument_dicts, top_function_name

# m_axi 缺省深度必须由 pragma、端口合同和 testbench 共同复用，避免 co-simulation 缓冲区分叉。
DEFAULT_M_AXI_DEPTH = 1024  # 未声明 depth 的 m_axi 端口统一使用的 co-simulation 窗口长度

# 生成 mock HLS 文件头 contract，覆盖 header/source/testbench 三种角色。
def file_header_lines(spec: dict[str, Any], file_kind_name: str) -> list[str]:
    """生成 HG007 要求的文件头 contract 行列表。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        file_kind_name: 当前文件角色，dtype=str，unit=file kind。

    返回:
        满足 HG007 的文件头 contract 行列表，dtype=list[str]，unit=comment lines。
    """

    # 先解析当前文件共享的 top function 名称。
    str_top_function_name = top_function_name(spec)  # 当前文件绑定的 top function 名称

    # 头文件只声明接口，不承担运行时事务或 transcript 输出。
    if file_kind_name == "header":

        # 交付 header 角色的 HG007 合同，明确声明边界和 transcript 禁令。
        return [
            f"// 职责：声明 `{str_top_function_name}` 的顶层接口，固定 workflow 写盘后的 HLS 顶层边界。",
            f"// 核心对象：top function `{str_top_function_name}` 与其全部顶层端口声明。",
            "// 输入/输出：头文件只暴露顶层参数类型与顺序，不承载运行时数据事务。",
            "// 打印协议：头文件不产生任何人类可读 transcript；带 [HLS] 前缀的输出只能出现在 testbench。",
        ]

    # testbench 需要额外说明 PASS/FAIL transcript 和向量哈希语义。
    if file_kind_name == "testbench":

        # 交付 testbench 角色的 HG007 合同，固定 smoke transcript 的输出边界。
        return [
            f"// 职责：构造 `{str_top_function_name}` 的 workflow 静态 smoke testbench，保留 PASS/FAIL 与向量哈希契约。",
            f"// 核心对象：testbench `main`、top function `{str_top_function_name}`，以及 case/vector hash 观测边界。",
            "// 输入/输出：准备最小输入载荷与输出缓冲，调用 top function 后输出带 [HLS] 前缀的 PASS/FAIL transcript。",
            "// 打印协议：所有人类可读输出都必须使用 `> INFO: [HLS]`、`> WARNING: [HLS]` 或 `> ERR: [HLS]` 固定前缀，后接简短状态文本。",
        ]

    # source 文件默认承担 pragma、顶层接口和 helper 实现的治理边界。
    return [
        f"// 职责：实现 `{str_top_function_name}` 的 workflow mock source，保留接口 pragma、pattern pragma 与顶层 contract。",
        f"// 核心对象：top function `{str_top_function_name}`、顶层端口 pragma，以及当前 pattern 需要的 helper/循环结构。",
        "// 输入/输出：根据 spec 读取输入端口或流通道，并把结果写回输出端口、输出流或局部缓冲。",
        "// 打印协议：source 文件默认不直接输出 transcript；如需调试输出，必须带 > INFO: [HLS] / > WARNING: [HLS] / > ERR: [HLS] 前缀。",
    ]

# 生成单个函数的四段 contract；top function 额外承担逐端口合同。
def function_contract_lines(
    function_info: Any,
    spec: dict[str, Any],
    dict_argument_names: dict[str, str],
    dict_replacements: dict[str, str],
    *,
    is_top_function: bool,
) -> list[str]:
    """生成单个函数的四段 contract 行列表。

    参数:
        function_info: 轻量解析器返回的函数信息对象，dtype=Any，unit=function info。
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument_names: 顶层参数原名到治理名的映射字典，dtype=dict[str, str]，unit=name map。
        dict_replacements: 当前 source 的完整标识符替换字典，dtype=dict[str, str]，unit=name map。
        is_top_function: 当前函数是否 top function，dtype=bool，unit=flag。

    返回:
        满足 HG008 的四段 contract 行列表，dtype=list[str]，unit=comment lines。
    """

    # top function 使用逐端口 contract 模板，普通 helper 走通用模板。
    if is_top_function:

        # 让 top function 直接走逐端口模板，避免 helper 合同掩盖 HG015 事实。
        return top_function_contract_lines(spec, dict_argument_names, declaration=False)

    # main 入口固定声明无参数、返回退出码和 transcript 副作用。
    if function_info.name == "main":

        # 让 testbench `main` 复用固定合同，保持 PASS/FAIL 语义稳定。
        return testbench_main_contract_lines(top_function_name(spec))

    # helper 存在参数时，把每个参数写成稳定的中文语义短句。
    if function_info.params:

        # 先准备可逐项追加的 helper 参数合同片段列表。
        list_parameter_segments: list[str] = []  # helper 参数合同片段的累积列表

        # 逐个参数生成治理后名称与局部语义描述。
        for str_parameter_name in function_info.params:

            # 先把参数名映射到治理后的标识符，避免合同回落到旧名字。
            str_parameter_alias = dict_replacements.get(str_parameter_name, str_parameter_name)  # 当前 helper 参数的治理后名字

            # 再把当前参数的局部职责写进合同片段列表。
            list_parameter_segments.append(
                f"{str_parameter_alias} 承载当前 helper 的局部输入、输出或缓冲边界"
            )

        # 把全部参数片段拼成 HG008 参数段。
        str_parameter_line = "参数：" + "；".join(list_parameter_segments) + "。"  # helper 的参数合同正文

    # 没有参数的 helper 也必须显式写出无参数。
    else:

        # 给无参 helper 固定写出“无参数”，避免 HG008 参数段留空。
        str_parameter_line = "参数：无参数。"  # 无参 helper 的参数合同正文

    # 非 void helper 必须显式说明返回的是中间结果或局部判断值。
    str_return_line = "返回：无返回；当前 helper 的结果通过引用参数、指针或流通道继续向下游传播。"  # 默认返回合同正文

    # 命中非 void helper 时，把返回合同改成中间值描述。
    if "void" not in function_info.return_type.casefold():

        # 把非 void helper 的返回段收紧到“中间结果或局部判断值”语义。
        str_return_line = "返回：返回当前 helper 的中间结果、局部判断值或计算片段。"  # 非 void helper 的返回合同正文

    # 返回通用 helper contract，避免把大量 mock helper 继续堆回治理入口。
    return [
        f"// 职责：执行 helper `{function_info.name}` 的局部事务，保持当前 HLS 数据路径可读且边界明确。",
        f"// {str_parameter_line}",
        f"// {str_return_line}",
        "// 副作用：读取、写入或推进当前 helper 负责的局部通道、缓冲或控制状态。",
    ]

# 生成 top function 的四段 contract，并在参数段逐项写出端口事实。
def top_function_contract_lines(
    spec: dict[str, Any],
    dict_argument_names: dict[str, str],
    *,
    declaration: bool,
) -> list[str]:
    """生成 top function 的四段 contract 行列表。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument_names: 顶层参数原名到治理名的映射字典，dtype=dict[str, str]，unit=name map。
        declaration: 当前 contract 是否用于头文件声明，dtype=bool，unit=flag。

    返回:
        满足 HG008 和 HG015 的四段 contract 行列表，dtype=list[str]，unit=comment lines。
    """

    # 初始化逐端口 contract 片段列表，后续按 spec 参数顺序依次追加。
    list_port_segments: list[str] = []  # top function 参数段中的逐端口合同片段列表

    # 逐个顶层参数写出名称、方向、协议和 depth/shape/unit 事实。
    for dict_argument in argument_dicts(spec):

        # 读取当前端口在 spec 里的原始名称。
        str_original_name = str(dict_argument.get("name") or "").strip()  # 当前顶层端口的原始名称

        # 空名称端口不参与 HG015 输出，避免伪造合同。
        if not str_original_name:

            # 跳过匿名端口，防止参数段凭空生成不可追踪的 HG015 片段。
            continue

        # 追加当前端口的完整 HG015 合同片段。
        list_port_segments.append(
            top_port_contract_segment(spec, dict_argument, dict_argument_names, str_original_name)
        )

    # 把逐端口片段收拢成参数字段正文。
    str_parameter_line = "参数：" + "；".join(list_port_segments) + "。" if list_port_segments else "参数：无参数。"  # 汇总后的顶层参数合同正文

    # 头文件声明和 source 定义的职责/副作用语义略有不同。
    str_responsibility = "声明" if declaration else "执行"  # top function 职责段里的动作动词

    # 按声明态或定义态选择副作用说明，避免头文件合同误写成运行时事务。
    if declaration:

        # 头文件版本只声明共享边界，不触碰真实数据流动。
        str_side_effect_line = "// 副作用：声明顶层接口顺序、端口协议与约束边界，供 source/testbench/validation 共享同一份合同。"  # header contract 的副作用说明

    # source 版本必须显式说明会读取输入、推进局部状态并写回结果边界。
    else:

        # 这里描述的是运行态副作用，而不是声明态共享边界。
        str_side_effect_line = "// 副作用：读取输入端口或通道、推进 pattern 所需的局部状态，并把结果写回输出边界。"  # source 定义态的副作用说明

    # 返回 top function 的四段 contract。
    return [
        f"// 职责：{str_responsibility} top function `{top_function_name(spec)}` 的顶层硬件合同。",
        f"// {str_parameter_line}",
        "// 返回：无返回；顶层结果通过输出端口或输出流对外可见。",
        str_side_effect_line,
    ]

# 为单个顶层端口生成 HG015 约束片段，避免参数段拼接逻辑继续堆大。
def top_port_contract_segment(
    spec: dict[str, Any],
    dict_argument: dict[str, Any],
    dict_argument_names: dict[str, str],
    str_original_name: str,
) -> str:
    """为单个顶层端口生成 HG015 约束片段。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument_names: 顶层参数原名到治理名的映射字典，dtype=dict[str, str]，unit=name map。
        str_original_name: 当前顶层参数的原始名称，dtype=str，unit=identifier name。

    返回:
        当前端口的 HG015 合同片段，dtype=str，unit=contract segment。
    """

    # 先读取当前端口的治理后名称。
    str_port_name = dict_argument_names.get(str_original_name, str_original_name)  # 当前顶层端口的治理后名称

    # 再读取方向、协议、shape、unit 和 depth 合同事实。
    # 方向字段决定合同里要把当前端口写成输入边界还是输出边界。
    str_direction = str(dict_argument.get("direction") or "input")  # HG015 里的端口方向文本

    # 协议字段决定当前端口属于 m_axi、axis 还是 s_axilite 一类接口角色。
    str_protocol = str(dict_argument.get("interface") or "s_axilite")  # HG015 里的接口协议文本

    # shape 文本负责告诉读者这是标量、数组还是流通道。
    str_shape = shape_text_for_argument(dict_argument)  # HG015 里的 shape 描述文本

    # 这里保留净化后的载荷类型，让读合同的人能直接看出数据家族。
    str_unit = unit_text_for_argument(dict_argument)  # 端口 contract 里的净化类型文本

    # 这里补的是访问窗口深度，用来解释 host 侧如何理解 m_axi 缓冲边界。
    str_depth = depth_text_for_argument(spec, dict_argument)  # 端口 contract 里的访问深度文本

    # 返回端口级合同片段，供参数段逐项拼接。
    return (
        f"端口：{str_port_name} 方向：{str_direction} 协议：{str_protocol} "
        f"depth：{str_depth} shape：{str_shape} unit：{str_unit}"
    )

# 渲染带 typed-prefix 端口名的 top function 签名，供 header 和 source 共用。
def top_function_signature_text(
    spec: dict[str, Any],
    dict_argument_names: dict[str, str],
    *,
    declaration: bool,
) -> str:
    """渲染带 typed-prefix 端口名的 top function 签名。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument_names: 顶层参数原名到治理名的映射字典，dtype=dict[str, str]，unit=name map。
        declaration: 当前签名是否用于头文件声明，dtype=bool，unit=flag。

    返回:
        带 typed-prefix 参数名的 top function 签名文本，dtype=str，unit=signature text。
    """

    # 初始化签名参数列表，后续按 spec 参数顺序逐项追加。
    list_arguments: list[str] = []  # top function 签名里的参数文本列表

    # 逐个顶层参数渲染类型和治理后名字，保持 header/source 的顶层签名一致。
    for dict_argument in argument_dicts(spec):

        # 读取当前参数的原始名称。
        str_original_name = str(dict_argument.get("name") or "").strip()  # 当前顶层参数的原始名称

        # 空名称参数不应该落入签名文本。
        if not str_original_name:

            # 跳过匿名参数，避免签名里混入空白标识符。
            continue

        # 读取当前参数的治理后名称。
        str_port_name = dict_argument_names.get(str_original_name, str_original_name)  # 当前顶层参数的治理后名称

        # 把当前参数写进签名列表，保持 header 与 source 使用同一份端口顺序。
        list_arguments.append(f"{dict_argument.get('type', 'int')} {str_port_name}")

    # 头文件声明使用分号结尾，source 定义使用左花括号起始。
    str_suffix = ";" if declaration else " {"  # 当前顶层签名的尾部文本

    # 返回完整顶层签名文本。
    return f"void {top_function_name(spec)}({', '.join(list_arguments) or 'void'}){str_suffix}"

# 汇总 mock header 所需头文件，保持 pattern 依赖和 stream 依赖显式可见。
def required_header_names(spec: dict[str, Any]) -> list[str]:
    """汇总治理后 mock header 必须包含的头文件列表。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        当前 header 需要写入的头文件名列表，dtype=list[str]，unit=header names。
    """

    # 先放入 ap_fixed/ap_int 两个基础 HLS 类型头文件。
    list_header_names = ["ap_fixed.h", "ap_int.h"]  # 治理后 header 的基础头文件列表

    # 逐项补齐 pattern 声明要求的额外头文件。
    for str_header_name in required_pattern_headers(spec):

        # 未收录的 pattern 头文件需要按首次出现顺序追加。
        if str_header_name not in list_header_names:

            # 只在首次出现时追加 pattern 依赖，保持 include 顺序稳定且不重复。
            list_header_names.append(str_header_name)

    # 任意 stream 参数都需要显式补齐 hls_stream.h。
    if any("hls::stream<" in str(dict_argument.get("type") or "") for dict_argument in argument_dicts(spec)):

        # 当前 header 尚未包含 hls_stream.h 时才追加一次。
        if "hls_stream.h" not in list_header_names:

            # 发现 stream 端口后再补齐 hls_stream.h，避免无关 pattern 平白增加 include。
            list_header_names.append("hls_stream.h")

    # 返回最终去重后的头文件顺序列表。
    return list_header_names

# 为 header include 行生成更具体的中文说明，避免落回空泛“引入依赖”模板。
def header_comment_text(header_name: str) -> str:
    """为 include 生成更具体的中文说明文本。

    参数:
        header_name: 当前 include 的头文件名，dtype=str，unit=header name。

    返回:
        当前 include 对应的中文说明文本，dtype=str，unit=comment text。
    """

    # 统一把头文件名小写化，便于按 HLS 依赖家族判断说明语义。
    str_lower_name = header_name.casefold()  # 当前头文件名的小写归一化结果

    # 按基础 HLS 类型、stream 和 AXIS 头文件分别输出稳定说明。
    if "ap_fixed" in str_lower_name:

        # 命中 ap_fixed 依赖时，返回定点类型专属说明。
        return "引入定点类型定义，保持 fixed/ufixed 端口与局部变量的类型边界可见。"

    # 任意精度整数类型需要单独说明其用途。
    if "ap_int" in str_lower_name:

        # 命中 ap_int 依赖时，返回整数位宽家族的专属说明。
        return "引入任意精度整数类型，支撑 ap_int/ap_uint 端口与中间值声明。"

    # hls_streamofblocks 头文件需要强调块级 token 所有权，而不是退回普通 stream 说明。
    if "hls_streamofblocks" in str_lower_name:

        # 命中 block stream 依赖时，返回块级 token 边界的专属说明。
        return "引入块流通道类型，支撑 streamofblocks pattern 的 block token 声明与块级事务边界。"

    # hls::stream 通道依赖需要显式说明。
    if "hls_stream" in str_lower_name:

        # 命中 stream 依赖时，返回通道声明相关的专属说明。
        return "引入 HLS 流通道类型，支撑逐 token stream/task_graph pattern 的通道声明。"

    # AXIS 头文件依赖需要强调载荷结构体边界。
    if "ap_axi_sdata" in str_lower_name:

        # 命中 AXIS 载荷依赖时，返回流载荷结构体的专属说明。
        return "引入 AXIS 载荷结构体类型，支撑 ap_axiu 风格的 AXI-Stream 端口。"

    # 其余头文件回退到 pattern 依赖说明。
    return f"引入 `{header_name}` 依赖，保持当前 pattern 所需的类型或 helper 可见。"

# 为顶层端口 contract 生成 shape 描述。
def shape_text_for_argument(argument: dict[str, Any]) -> str:
    """为顶层端口 contract 生成 shape 描述。

    参数:
        argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        当前参数对应的 shape 文本，dtype=str，unit=shape text。
    """

    # 读取当前参数的类型文本，供 shape 推断复用。
    str_type = str(argument.get("type") or "")  # 当前顶层参数的类型文本

    # 指针参数视为一维数组。
    if "*" in str_type:

        # 命中指针类型后，把 shape 固定写成一维数组。
        return "一维数组"

    # stream 参数视为单向流。
    if "hls::stream<" in str_type:

        # 命中 hls::stream 类型后，把 shape 固定写成单向流。
        return "单向流"

    # 其余参数默认按标量描述。
    return "标量"

# 把端口载荷类型清洗成可写进 HG015 的 unit 文本，去掉 const、指针和引用噪声。
def unit_text_for_argument(argument: dict[str, Any]) -> str:
    """为顶层端口 contract 生成 unit 描述。

    参数:
        argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        当前参数对应的 unit 文本，dtype=str，unit=unit text。
    """

    # 归一化类型文本，去掉 const、指针和引用标记，只保留核心载荷类型。
    return str(argument.get("type") or "int").replace("const ", "").replace("*", "").replace("&", "").strip()

# 解析 m_axi 顶层端口的有效 depth，供 pragma、合同和 testbench 共用。
def m_axi_depth_for_argument(spec: dict[str, Any], argument: dict[str, Any]) -> int:
    """解析 m_axi 端口的统一访问深度。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        当前 m_axi 端口应同时用于 pragma、合同和局部 testbench 数组的正整数深度。
    """

    # 参数级 depth 是最接近真实端口的约束，应优先覆盖全局回退配置。
    obj_argument_depth = argument.get("depth")  # 当前端口显式声明的访问深度

    # 接受 JSON 数值和数字文本两种稳定表示，避免序列化过程制造深度分叉。
    if isinstance(obj_argument_depth, int) and obj_argument_depth > 0:

        # 返回端口级正整数深度。
        return int(obj_argument_depth)

    # 数字字符串同样属于可直接落盘的显式端口合同。
    if isinstance(obj_argument_depth, str) and obj_argument_depth.isdigit() and int(obj_argument_depth) > 0:

        # 把数字文本规范化成整数，供所有 HLS 输出路径复用。
        return int(obj_argument_depth)

    # 性能字段可以给出没有逐端口标注时的访问窗口上界。
    dict_performance = spec.get("performance") if isinstance(spec.get("performance"), dict) else {}  # spec 性能配置段

    # 按现有 mock provider 约定保留性能字段优先级。
    for str_key in ("max_length", "vector_length", "depth"):

        # 把数值和数字文本统一成字符串，后续只接受正整数形式的访问窗口。
        str_performance_depth: str = str(dict_performance.get(str_key) or "")  # 当前性能键对应的候选 m_axi 深度文本

        # 合法正整数性能值可以成为所有 m_axi 相关产物的共同深度。
        if str_performance_depth.isdigit() and int(str_performance_depth) > 0:

            # 把性能配置文本规范化成整数，供 pragma、合同和 testbench 共同使用。
            return int(str_performance_depth)

    # 全局 interface_profile.depth 是逐端口合同缺失时的第二层显式约束。
    obj_interface_profile = spec.get("interface_profile")  # 全局 interface_profile 配置对象

    # profile 对象存在时继续检查其中的全局 depth 约束。
    if isinstance(obj_interface_profile, dict):

        # 只接受正整数 profile 深度，拒绝空值和非数值配置。
        obj_profile_depth = obj_interface_profile.get("depth")  # profile 级候选 m_axi 深度

        # 正整数 profile 值可以作为缺少端口级声明时的访问窗口。
        if isinstance(obj_profile_depth, int) and obj_profile_depth > 0:

            # 返回 profile 级访问窗口深度。
            return int(obj_profile_depth)

        # profile 深度的数字文本形式同样需要规范化。
        if (
            isinstance(obj_profile_depth, str)
            and obj_profile_depth.isdigit()
            and int(obj_profile_depth) > 0
        ):

            # 把 profile 文本转换为统一整数。
            return int(obj_profile_depth)

    # 没有显式合同时保留 co-simulation 使用的保守默认窗口。
    return DEFAULT_M_AXI_DEPTH

# 为 m_axi 顶层端口生成 depth 描述；非 m_axi 场景显式写出“无”。
def depth_text_for_argument(spec: dict[str, Any], argument: dict[str, Any]) -> str:
    """为顶层端口 contract 生成 depth 描述。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        argument: 当前顶层参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        当前参数对应的 depth 文本，dtype=str，unit=depth text。
    """

    # 非 m_axi 端口没有显式 depth 合同，直接写成“无”。
    if str(argument.get("interface") or "") != "m_axi":

        # 非存储映射接口不需要深度事实，直接返回“无”。
        return "无"

    # 统一复用 m_axi 深度解析器，保证合同文字与 pragma、testbench 数组共享同一数值。
    return str(m_axi_depth_for_argument(spec, argument))

# 生成 testbench `main` 的四段 contract，满足 HG008 要求。
def testbench_main_contract_lines(str_top_function_name: str) -> list[str]:
    """生成 testbench `main` 的四段 contract 行列表。

    参数:
        str_top_function_name: 当前 testbench 要调用的 top function 名称，dtype=str，unit=function name。

    返回:
        满足 HG008 的 `main` contract 行列表，dtype=list[str]，unit=comment lines。
    """

    # 返回 testbench `main` 的固定四段 contract。
    return [
        f"// 职责：准备 workflow 静态 smoke 载荷，调用 `{str_top_function_name}`，并输出 PASS/FAIL transcript。",
        "// 参数：无参数。",
        "// 返回：返回 0 表示 PASS，返回 1 表示 FAIL。",
        f"// 副作用：调用 `{str_top_function_name}`，并向 stdout 输出带 [HLS] 前缀的 PASS/FAIL transcript。",
    ]
