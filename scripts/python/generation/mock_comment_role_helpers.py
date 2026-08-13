"""提供 mock HLS testbench 的代码角色识别和语义路由。"""

# 兼容不同 Python 版本的类型注解延迟求值。
from __future__ import annotations

# 正则识别函数签名和 testbench 数组声明。
import re

# 将注释序位转换为不会被 ASCII 数字归一化掉的中文序位。
def _chinese_sequence_label_for(int_sequence_index: int) -> str:
    """
    返回当前语义角色的中文序位标签。

    :param int_sequence_index: 当前注释类别内的序位编号。
    :return: 可直接写入语义角色的中文序位。
    """

    # 首次使用独立词语，避免与后续序位混淆。
    if int_sequence_index <= 1:

        # 零值和首次都回退到首个事务语义。
        return "首次"

    # 后续序位使用中文符号编码，避免数字归一化抹掉相邻角色差异。
    str_sequence_symbols = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"  # 后续序位中文编码表

    # 将第二个语义映射为零基编码值，保证首次之后仍保持连续性。
    int_symbol_value = int_sequence_index - 2  # 后续序位相对编码值

    # 缓存编码字符，稍后反转恢复高位到低位的中文序位。
    list_sequence_symbols: list[str] = []  # 后续序位字符缓存

    # 使用中文符号表为任意后续序位生成稳定编码。
    while int_symbol_value:

        # 取出当前最低位符号并更新待编码值。
        int_symbol_value, int_symbol_index = divmod(int_symbol_value, len(str_sequence_symbols))  # 当前序位编码拆分

        # 暂存最低位符号，最终反转为正常顺序。
        list_sequence_symbols.append(str_sequence_symbols[int_symbol_index])  # 当前序位编码字符

    # 返回带序位边界的中文编码，不依赖 ASCII 序号。
    return f"序位{''.join(reversed(list_sequence_symbols))}"

# 让同一类事务动作按真实出现顺序使用不同的语义句式。
def _role_from_sequence_for(
    int_sequence_index: int,
    tuple_roles: tuple[tuple[str, str], ...],
) -> tuple[str, str]:
    """
    根据当前动作在 testbench 中的出现顺序选择双语角色。

    :param int_sequence_index: 当前动作在同类语义中的顺序编号。
    :param tuple_roles: 可复用的中英文角色句式序列。
    :return: 当前顺序对应的中英文角色。
    """

    # 空角色表表示调用方没有提供可用语义，保持安全回退。
    if not tuple_roles:

        # 未提供角色模板时返回空二元组。
        return "", ""

    # 顺序编号从一开始折叠到角色表下标，兼容零值调用。
    int_role_index = max(0, int_sequence_index - 1) % len(tuple_roles)  # 当前动作角色下标

    # 返回当前顺序的完整双语动作说明。
    return tuple_roles[int_role_index]

# 判断 HLS 函数声明或定义的结构边界。
def is_function_signature_line(stripped: str) -> bool:
    """
    识别普通 HLS 函数声明或定义行。

    :param stripped: 去空白后的 HLS 代码行。
    :return: 是否匹配函数签名模式。
    """

    # 函数签名必须包含括号且以分号或左花括号结束。
    if not (
        "(" in stripped
        and ")" in stripped
        and (stripped.endswith(";") or stripped.endswith("{"))
    ):

        # 不满足基本形态时不是函数签名。
        return False

    # stream/task 变量声明包含括号但不是函数签名。
    if stripped.startswith(("hls::stream", "hls::task")):

        # dataflow 对象声明不作为函数签名处理。
        return False

    # 控制语句和 return 调用不是函数签名。
    if stripped.split("(", 1)[0].strip().split(" ")[0] in {
        "if",
        "for",
        "while",
        "switch",
        "return",
    }:

        # 控制语句不作为函数签名处理。
        return False

    # 函数签名正则覆盖命名空间、模板、指针和数组参数。
    str_pattern = (
        r"^(?:[\w:<>~,\*&\[\]\s]+)\s+[A-Za-z_]\w*"
        r"(?:::[A-Za-z_]\w*)?\s*\([^;{}]*\)\s*(?:const\s*)?(?:;|\{)$"
    )

    # 返回函数签名匹配结果。
    return bool(re.match(str_pattern, stripped))

# 判断变量名是否处于带类型前缀的数组声明位置。
def _is_typed_array_declaration(stripped: str, str_name_pattern: str) -> bool:
    """
    识别真正的数组声明，排除 `(double)array[index]` 读取表达式。

    :param stripped: 去空白后的 testbench 代码行。
    :param str_name_pattern: 需要匹配的数组变量名正则。
    :return: 当前行是否声明目标数组。
    """

    # 类型前缀必须直接位于数组变量之前，读取表达式不会满足该结构。
    str_type_prefix = r"(?:\b[\w:]+(?:<[^;\n>]+>)?\s+)+"  # 数组声明类型前缀

    # 使用带类型前缀的模式确认当前行确实声明数组。
    return bool(re.search(str_type_prefix + str_name_pattern + r"\s*\[", stripped))

# 识别 testbench 入口和函数签名角色。
def _testbench_entry_role_for(stripped: str) -> tuple[str, str]:
    """
    返回 testbench 主入口或辅助函数的双语角色。

    :param stripped: 去空白后的 testbench 代码行。
    :return: 函数入口角色；未命中时返回空字符串。
    """

    # main 入口承载事务准备、比较和进程状态返回。
    if stripped.startswith(("int main(", "int main (")):

        # 返回主入口角色。
        return (
            "testbench 主入口承载事务准备、比较和状态返回。",
            "defines the testbench entry for setup, comparison, and status return.",
        )

    # 其他函数签名不能回退成局部 buffer 的泛化模板。
    if is_function_signature_line(stripped):

        # 返回辅助函数入口角色。
        return (
            "testbench 辅助函数入口承载局部事务流程。",
            "defines a helper entry for a local transaction flow.",
        )

    # 当前行不是函数入口。
    return "", ""

# 识别 memory testbench 的数组、比较和顶层调用角色。
def _testbench_memory_role_for(stripped: str) -> tuple[str, str]:
    """
    返回 memory 数组和输出比较相关的双语角色。

    :param stripped: 去空白后的 testbench 代码行。
    :return: memory 角色；未命中时返回空字符串。
    """

    # 输出数组与 oracle 比较必须先于数组声明判断。
    if (
        ("arr_output_values" in stripped or "ptr_output_values" in stripped)
        and "arr_expected_values" in stripped
        and any(str_operator in stripped for str_operator in ("!=", "=="))
    ):

        # 返回内存输出与 oracle 的数值比较角色。
        return (
            "写回样本与 oracle 参考值执行数值比较。",
            "compares writeback samples with the oracle values.",
        )

    # 左侧 lane 先承接被乘矩阵的行样本，明确其数据路径职责。
    if _is_typed_array_declaration(stripped, r"\barr_input_a_values"):

        # 返回第一输入样本数组声明角色。
        return (
            "左矩阵行样本数组声明承接被乘数据的内存载荷。",
            "declares the left-matrix row samples for the multiplicand data path.",
        )

    # 右侧 lane 单独承接乘数矩阵的列样本，避免与左侧数组复用模板。
    if _is_typed_array_declaration(stripped, r"\barr_input_b_values"):

        # 返回第二输入样本数组声明角色，区别于第一输入数组。
        return (
            "右矩阵列样本数组声明承接乘数数据的内存载荷。",
            "declares the right-matrix column samples for the multiplier data path.",
        )

    # 输入数组使用 arr_ 前缀，明确区别于顶层指针参数。
    if _is_typed_array_declaration(stripped, r"\barr_input(?:_a|_b)?_values"):

        # 无 lane 后缀的单通道声明进入单输入 memory contract。
        return (
            "输入样本数组声明承接当前事务的原始内存载荷。",
            "declares the input sample array for this transaction.",
        )

    # 输出数组声明承接内核写回载荷。
    if _is_typed_array_declaration(
        stripped,
        r"\b(?:arr_output_values|ptr_output_values)",
    ):

        # 返回输出写回数组声明角色。
        return (
            "输出样本数组声明承接当前事务的内核写回载荷。",
            "declares the kernel writeback sample array.",
        )

    # memory 场景的 oracle 数组保存参考结果。
    if _is_typed_array_declaration(stripped, r"\barr_expected_values"):

        # 返回 oracle 参考数组声明角色。
        return (
            "参考样本数组声明保存当前事务的 oracle 结果。",
            "declares the oracle reference sample array.",
        )

    # typed-prefix 后的 memory 调用提交输入并接收输出。
    if (
        "(" in stripped
        and stripped.endswith(";")
        and "arr_input" in stripped
        and "arr_output" in stripped
    ):

        # 返回 memory 顶层调用角色。
        return (
            "内存顶层调用提交输入样本数组并接收输出写回数组。",
            "calls the memory top function with input and writeback arrays.",
        )

    # 当前行不是 memory 数组或调用。
    return "", ""

# 识别 AXIS testbench 的包序和 sideband 字段角色。
def _axis_sideband_role_for(stripped: str) -> tuple[str, str]:
    """
    返回 AXIS 字段赋值的包序和 sideband 双语角色。

    :param stripped: 去空白后的 testbench 代码行。
    :return: AXIS 字段角色；未命中时返回空字符串。
    """

    # AXIS 字段匹配保留变量名，供后续提取包序和字段名称。
    match_axis_field: re.Match[str] | None = re.search(  # AXIS 字段赋值匹配结果
        r"\b(axis_[A-Za-z0-9_]+|out_pkt)\.(data|keep|strb|last)\s*=",  # AXIS sideband 匹配模式
        stripped,  # 当前代码行待识别的 AXIS 赋值文本
    )

    # 未命中 AXIS 字段时交回 stream 的其他角色分类。
    if match_axis_field is None:

        # 当前代码行不是 AXIS sideband 赋值。
        return "", ""

    # 将匹配对象拆成包变量名和 sideband 名称。
    str_packet_name = match_axis_field.group(1)  # 当前 AXIS 包变量名供序位判断

    # 提取 sideband 字段名，供中文职责表选择具体含义。
    str_field_name = match_axis_field.group(2)  # 当前 AXIS 字段名供职责表选择

    # 未编号的输出包使用单独的默认方向名称。
    str_packet_order = "输出"  # 未编号输出包的默认序位

    # 输入包变量名包含序位时使用明确的中文包序。
    if str_packet_name.startswith("axis_"):

        # 提取输入包末尾的数字序位。
        str_packet_index = re.search(r"_(\d+)$", str_packet_name)  # 输入包数字序位

        # 将常见输入包序位映射到中文职责。
        str_packet_order = {  # 当前输入包职责
            "0": "首个输入",  # 第一个输入包的中文方向
            "1": "第二个输入",  # 第二个输入包的中文方向
            "2": "第三个输入",  # 第三个输入包的中文方向
        }.get(
            str_packet_index.group(1) if str_packet_index else "",  # 提取到的输入包序号
            "后续输入",  # 未覆盖序号的后续输入方向
        )

    # 为不同输入包保留发送序列中的真实位置职责。
    str_packet_context = {  # AXIS 包序位置语义
        "首个输入": "建立发送序列起点",  # 首个输入包的位置职责
        "第二个输入": "延续发送序列中段",  # 第二个输入包的位置职责
        "第三个输入": "承接发送序列后续段",  # 第三个输入包的位置职责
        "后续输入": "保留发送序列后续位置",  # 后续输入包的位置职责
        "输出": "承接内核响应序列",  # 输出响应包的位置职责
    }.get(str_packet_order, "保留当前 AXIS 序列位置")  # 当前包的序列位置说明

    # 每个 sideband 使用独立动作，避免四类协议字段退化成同一模板。
    str_field_role = {  # AXIS 字段中文动作表
        "data": "数据载荷写入待发令牌，保存当前编码样本",  # 数据字段保存编码值
        "keep": "有效字节掩码裁定令牌的可传输范围",  # keep 字段裁定字节范围
        "strb": "字节使能掩码声明各字节的写入资格",  # strb 字段声明写入资格
        "last": "帧尾边界标记决定发送序列是否收束",  # last 字段决定帧边界
    }[str_field_name]  # 当前字段的中文动作

    # 英文动作与中文职责一一对应，保留协议字段的可审计含义。
    str_field_english = {  # AXIS 字段英文动作表
        "data": "stores the current encoded sample in the pending payload",  # 英文数据字段动作
        "keep": "constrains the transferable byte range with the valid-byte mask",  # 英文 keep 字段动作
        "strb": "declares byte-write eligibility with the byte-enable mask",  # 英文句说明每字节是否允许写入
        "last": "marks whether the transmitted sequence reaches its frame boundary",  # 英文句说明 AXIS 序列终止
    }[str_field_name]  # 当前字段的英文动作

    # 返回带包序和字段动作的 AXIS 写入角色。
    return (
        f"{str_packet_order}包{str_packet_context}，{str_field_role}。",
        f"The {str_packet_order} packet {str_packet_context}; {str_field_english}.",
    )

# 识别 AXIS packet 声明，区分输入包序和输出包对象。
def _axis_packet_declaration_role_for(stripped: str) -> tuple[str, str]:
    """
    返回 AXIS packet 声明的方向和序位角色。

    :param stripped: 去空白后的 testbench 代码行。
    :return: AXIS packet 声明角色；未命中时返回空字符串。
    """

    # stream 容器内部虽然包含 ap_axiu，但其对象语义仍由 stream 分支负责。
    if "hls::stream" in stripped:

        # 不把 stream 容器误判成独立 packet 对象。
        return "", ""

    # 只识别真正的 ap_axiu 对象声明，避免把普通字段赋值当成 packet 声明。
    match_axis_declaration: re.Match[str] | None = re.search(  # AXIS packet 声明匹配结果
        r"\bap_axiu<[^;\n]+>\s+([A-Za-z_]\w*)\s*(?:=|;)",  # AXIS packet 类型和对象名匹配模式
        stripped,  # 当前待识别的 AXIS 声明文本
    )

    # 未命中 packet 对象声明时交回其他 testbench 角色分类。
    if match_axis_declaration is None:

        # 当前代码行不是 AXIS packet 对象声明。
        return "", ""

    # 读取当前 AXIS packet 的治理后对象名。
    str_packet_name = match_axis_declaration.group(1)  # 当前 AXIS packet 对象名

    # 带序号的输入包声明需要显式保留输入包顺序。
    if str_packet_name.startswith("axis_in_pkt_"):

        # 提取治理后输入包名末尾的序号。
        match_packet_index = re.search(r"_(\d+)$", str_packet_name)  # 当前输入包序号

        # 把常用输入包序号映射成稳定中文角色。
        dict_packet_orders = {"0": "首个", "1": "第二个", "2": "第三个"}  # 当前输入包序位映射

        # 读取当前输入包的中文序位。
        str_packet_order = dict_packet_orders.get(  # 当前输入包的中文序位
            match_packet_index.group(1) if match_packet_index else "",  # 当前输入包的序号文本
            "后续",  # 未覆盖序号的后续输入
        )

        # 为不同输入包赋予不同的协议阶段职责。
        dict_packet_roles = {  # 输入 packet 的阶段职责映射
            "首个": (  # 首个 packet 阶段键
                "首个输入 AXIS packet 对象声明建立起始载荷包络。",  # 首个 packet 中文职责
                "declares the first input AXIS packet as the opening payload envelope.",  # 首个 packet 英文职责
            ),
            "第二个": (  # 第二个 packet 阶段键
                "第二个输入 AXIS packet 对象声明承接续接载荷包络。",  # 第二个 packet 中文职责
                "declares the second input AXIS packet as the continuation-check envelope.",  # 第二个 packet 英文职责
            ),
            "第三个": (  # 第三个 packet 阶段键
                "第三个输入 AXIS packet 对象声明准备收尾载荷包络。",  # 第三个 packet 中文职责
                "declares the third input AXIS packet as the closing payload envelope.",  # 第三个 packet 英文职责
            ),
        }  # 输入 packet 的协议阶段角色表

        # 返回当前输入 packet 的阶段职责。
        return dict_packet_roles.get(
            str_packet_order,
            (
                f"{str_packet_order}输入 AXIS packet 对象声明建立后续包络。",
                f"declares the {str_packet_order} input AXIS packet as a later payload envelope.",
            ),
        )

    # 输出 packet 对象承接内核返回的完整响应包。
    if str_packet_name.startswith("axis_out_pkt"):

        # 返回输出 packet 的独立协议角色。
        return (
            "输出 AXIS packet 对象声明承接当前响应包。",
            "declares the AXIS output packet for the current response.",
        )

    # 其他 packet 名称仍保留协议对象职责，不回退成普通变量模板。
    return (
        "AXIS packet 对象声明建立当前事务的侧带容器，等待协议字段填充。",
        "declares the AXIS packet sideband container before protocol fields are filled.",
    )

# 识别 stream testbench 的 oracle、观测、读写角色。
def _testbench_stream_role_for(
    stripped: str,
    semantic_index: int = 0,
) -> tuple[str, str]:
    """
    返回 stream 观测和通道操作的双语角色。

    :param stripped: 去空白后的 testbench 代码行。
    :param semantic_index: 当前 stream 动作在同类语义中的顺序编号。
    :return: stream 角色；未命中时返回空字符串。
    """

    # 流式观测与 oracle 的比较需要独立于数组声明。
    if "arr_observed" in stripped and "expected" in stripped and any(
        str_operator in stripped for str_operator in ("!=", "==")
    ):

        # 返回流式响应比较角色。
        return (
            "流式观测令牌与 oracle 样本执行逐项核验。",
            "compares observed stream tokens with the oracle samples.",
        )

    # stream 场景的 expected 数组保存响应参考令牌。
    if re.search(r"\b(?:const\s+)?(?:unsigned|double)\s+expected\s*\[", stripped):

        # 返回流式 oracle 数组声明角色。
        return (
            "流式 oracle 数组声明保存响应参考令牌。",
            "declares the oracle array for response tokens.",
        )

    # AXIS packet 声明必须先于 stream 操作分类，保留输入包序和输出包方向。
    tuple_axis_packet_role = _axis_packet_declaration_role_for(stripped)  # 当前 AXIS packet 声明角色

    # 命中 packet 声明时直接返回协议容器职责。
    if tuple_axis_packet_role[0]:

        # AXIS packet 声明不再回退到通用局部变量说明。
        return tuple_axis_packet_role

    # 输入和输出 stream 声明必须区分通道方向，避免同一事务内重复角色。
    if "hls::stream" in stripped:

        # 输入通道声明承接待发送的事务令牌。
        if any(str_token in stripped for str_token in ("stream_in", "in_stream", "input_stream")):

            # 返回输入 stream 的通道声明角色。
            return (
                "输入流对象声明承接待发送令牌，形成内核输入数据路径。",
                "declares the input stream that carries tokens into the kernel data path.",
            )

        # 输出通道声明承接内核返回的事务令牌。
        if any(str_token in stripped for str_token in ("stream_out", "out_stream", "output_stream")):

            # 返回输出 stream 的通道声明角色。
            return (
                "输出流对象声明接收内核响应令牌，形成观测数据路径。",
                "declares the output stream that receives kernel responses for observation.",
            )

        # 未命名方向的 stream 仍说明其通道对象职责。
        return (
            "流对象声明建立当前事务的令牌通道，保留 dataflow 边界。",
            "declares the transaction token channel while preserving the dataflow boundary.",
        )

    # 观测数组声明保存读取到的真实响应令牌。
    if _is_typed_array_declaration(stripped, r"\b(?:arr_observed|observed)"):

        # 返回观测数组声明角色。
        return (
            "观测数组声明保存读取到的真实响应令牌，作为后续比较缓冲。",
            "declares storage for observed response tokens used by the later comparison buffer.",
        )

    # 观测槽位赋值承接流读取结果，但数组初始化仍属于声明边界。
    if "arr_observed" in stripped and "=" in stripped and "{}" not in stripped:

        # 空输出分支登记缺失响应，避免把保护状态误写成真实读取。
        if re.search(r"=\s*0\b", stripped):

            # 返回空输出通道的缺失响应角色。
            return (
                "空输出通道登记缺失响应，保留观测缓冲的保护值。",
                "records a missing response and keeps the guarded value in the observation buffer.",
            )

        # 真实读取按出现顺序选择不同的观测动作，保留数据路径差异。
        tuple_observation_roles = (  # 真实读取的观测动作序列
            (
                "首个流式响应写入观测槽，开启 oracle 对齐。",  # 首个响应观测动作
                "writes the first stream response into the observation slot for oracle alignment.",  # 首个响应英文动作
            ),
            (
                "后续流式响应填充观测槽，延续 oracle 对齐。",  # 后续响应观测动作
                "fills the observation slot with a later stream response for continued oracle alignment.",  # 后续响应英文动作
            ),
            (
                "收尾流式响应封存观测槽，准备边界核验。",  # 收尾响应观测动作
                "seals the observation slot with the closing stream response before boundary validation.",  # 收尾响应英文动作
            ),
        )  # 观测动作序列结束

        # 返回当前真实读取的观测动作。
        return _role_from_sequence_for(semantic_index, tuple_observation_roles)

    # AXIS 字段角色先于通用 write 分类，确保 sideband 不落入令牌模板。
    tuple_axis_role = _axis_sideband_role_for(stripped)  # 当前 AXIS 字段语义

    # 命中 AXIS 字段时直接返回包序和 sideband 角色。
    if tuple_axis_role[0]:

        # 保留 AXIS 字段的具体 sideband 语义。
        return tuple_axis_role

    # write 语句按输入通道和中间通道区分。
    if ".write(" in stripped:

        # 输入流写入注入当前事务的下一个令牌。
        if "stream_in" in stripped or "in_stream" in stripped:

            # 返回输入流令牌注入角色。
            tuple_input_roles = (  # 输入流注入动作序列
                (
                    "输入流注入首个待处理令牌，启动当前数据路径。",  # 首个输入动作
                    "injects the first pending token to start the current data path.",  # 首个输入英文动作
                ),
                (
                    "输入流追加后续待处理令牌，保持发送顺序。",  # 后续输入动作
                    "appends a later pending token while preserving send order.",  # 后续输入英文动作
                ),
                (
                    "输入流提交本事务的收尾令牌，完成输入段。",  # 收尾输入动作
                    "commits the transaction's closing token to finish the input segment.",  # 收尾输入英文动作
                ),
            )  # 输入流注入动作结束

            # 返回当前输入令牌的动作角色。
            return _role_from_sequence_for(semantic_index, tuple_input_roles)

        # 其他 write 语句属于中间或输出通道。
        tuple_write_roles = (  # 中间/输出流提交动作序列
            (
                "中间流提交首个转发令牌，建立 dataflow 连接。",  # 首个转发动作
                "commits the first forwarding token to establish the dataflow link.",  # 首个转发英文动作
            ),
            (
                "输出流提交后续响应令牌，延续内核结果序列。",  # 后续响应动作
                "commits a later response token to continue the kernel result sequence.",  # 英文句突出结果序列延续
            ),
        )  # 中间/输出流提交动作结束

        # 以序位索引选择 dataflow 提交说明。
        return _role_from_sequence_for(semantic_index, tuple_write_roles)

    # 读取分支先锁定响应方向，避免把通道消费误判为输入注入。
    if ".read()" in stripped:

        # 输出流读取承接内核响应令牌。
        if "stream_out" in stripped or "out_stream" in stripped:

            # 输出流返回响应方向的双语角色。
            tuple_read_roles = (  # 输出流读取动作序列
                (
                    "输出流读取首个响应令牌，打开观测数据段。",  # 首个响应打开观测段
                    "reads the first response token to open the observed data segment.",  # 英文句突出观测段开启
                ),
                (
                    "输出流读取后续响应令牌，继续填充观测序列。",  # 后续响应填充观测序列
                    "reads a later response token to continue the observed sequence.",  # 英文句突出响应序列延续
                ),
                (
                    "输出流读取收尾响应令牌，准备完成边界核验。",  # 收尾响应准备边界核验
                    "reads the closing response token before boundary verification.",  # 英文句突出边界核验前收束
                ),
            )  # 输出流读取动作结束

            # 以序位索引选择观测读取说明。
            return _role_from_sequence_for(semantic_index, tuple_read_roles)

        # 其他 read 语句属于输入或中间通道消费。
        return (
            "中间或输入流消费当前事务的通道令牌，保持数据路径推进。",
            "consumes a channel token while keeping the data path moving.",
        )

    # 当前行不是 stream 操作。
    return "", ""

# 识别 testbench transcript 输出序列化角色。
def _testbench_output_role_for(
    stripped: str,
    next_code: str = "",
) -> tuple[str, str]:
    """
    返回输出样本、分隔符和 transcript 起点的双语角色。

    :param stripped: 去空白后的 testbench 代码行。
    :param next_code: 当前输出语句之后的下一条有效代码行。
    :return: transcript 角色；未命中时返回空字符串。
    """

    # memory 输出数组的序列化按普通样本与首项 checkpoint 区分。
    if (
        ("arr_output_values" in stripped or "ptr_output_values" in stripped)
        and ("std::cout" in stripped or "<<" in stripped)
    ):

        # 输出字段起点先建立观测序列容器，不等同于后续样本写出。
        if "outputs" in stripped and "[" in stripped:

            # 返回 transcript 输出字段起点角色。
            return (
                "transcript 输出字段建立当前事务的观测序列容器。",
                "opens the transaction's serialized observation container.",
            )

        # 首项写回值进入 first_output checkpoint。
        if "[0]" in stripped:

            # 首项分支保持 checkpoint 语义。
            return (
                "首个写回样本序列化进入 transcript 的首项检查点。",
                "serializes the first writeback sample into the first-output checkpoint.",
            )

        # 普通写回值进入 outputs 序列。
        return (
            "写回样本序列化进入 transcript 的输出序列。",
            "serializes a writeback sample into the output sequence.",
        )

    # stream 观测值也需要独立的 transcript 角色。
    if "arr_observed" in stripped and "std::cout" in stripped:

        # 观测分支保持 stream 样本语义。
        return (
            "观测令牌序列化进入 transcript 的输出序列。",
            "serializes an observed token into the output sequence.",
        )

    # 输出序列的逗号只承担字段分隔职责。
    if "std::cout" in stripped and "i != 0" in stripped:

        # 返回输出分隔符角色。
        return (
            "输出序列分隔符写入当前 transcript。",
            "writes the separator for the output sequence.",
        )

    # 裸 cout 行根据后继 JSON 字段区分结果记录和 checkpoint 记录。
    if stripped == "std::cout":

        # 结果标签后接机器可读结果对象的起点。
        if "HLS-GEN-RESULT" in next_code:

            # 返回机器结果对象起点角色。
            return (
                "结构化 transcript 输出流开启当前事务的机器结果记录。",
                "opens the machine-readable result record for this transaction.",
            )

        # checkpoint 字段后接当前事务的维度摘要。
        if "checkpoints" in next_code:

            # 返回 checkpoint 结果对象续接角色。
            return (
                "结构化 transcript 输出流续接当前事务的 checkpoint 记录。",
                "continues the checkpoint record for this transaction.",
            )

        # 其他裸 cout 行仍保持通用结果记录起点语义。
        return (
            "结构化 transcript 输出流开启当前结果记录。",
            "opens the structured transcript for the current result record.",
        )

    # 当前行不是 transcript 序列化。
    return "", ""

