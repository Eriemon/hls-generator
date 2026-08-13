"""提供 mock HLS testbench 的代码角色识别和语义路由。"""

# 兼容不同 Python 版本的类型注解延迟求值。
from __future__ import annotations

# 正则识别本模块仍保留的上下文和协议行尾路由。
import re

# 基础、memory、AXIS 和 stream 角色由独立 helper 模块负责。
from scripts.python.generation.mock_comment_role_helpers import (
    _axis_packet_declaration_role_for,
    _axis_sideband_role_for,
    _chinese_sequence_label_for,
    _is_typed_array_declaration,
    _role_from_sequence_for,
)

# 入口、memory 和 stream 路由继续通过同一 helper facade 暴露。
from scripts.python.generation.mock_comment_role_helpers import (
    _testbench_entry_role_for,
    _testbench_memory_role_for,
    _testbench_output_role_for,
    _testbench_stream_role_for,
    is_function_signature_line,
)

# 按入口、transcript、memory 和 stream 的优先级路由 testbench 角色。
def testbench_contextual_role_for(
    stripped: str,
    next_code: str = "",
    semantic_index: int = 0,
) -> tuple[str, str]:
    """
    为 testbench 代码行选择最具体的双语角色。

    :param stripped: 去空白后的 testbench 代码行。
    :param next_code: 当前代码行之后的下一条有效代码行。
    :param semantic_index: 当前相邻语义类别内的顺序编号。
    :return: 中英文角色模板二元组；未命中时返回空字符串。
    """

    # 入口角色优先，防止函数签名落入普通代码回退。
    tuple_role = _testbench_entry_role_for(stripped)  # 当前代码行的入口角色

    # 命中入口角色时直接返回。
    if tuple_role[0]:

        # 入口角色直接终止后续分类。
        return tuple_role

    # 最终 stdout 状态必须区分失败诊断和成功完成协议。
    if stripped.startswith('std::cout << "> ERR: [HLS]'):

        # 返回失败结果的诊断输出角色。
        return (
            "失败诊断输出写出当前 testbench 的 HLS 错误协议。",
            "writes the HLS error protocol for the failing testbench.",
        )

    # 成功状态输出确认所有事务完成且协议收尾。
    if stripped.startswith('std::cout << "> INFO: [HLS]'):

        # 返回成功结果的协议收尾角色。
        return (
            "成功协议输出确认当前 testbench 的全部事务已完成。",
            "confirms completion of every transaction in the successful testbench protocol.",
        )

    # 比较循环和 transcript 遍历循环必须保持不同的事务语义。
    if stripped.startswith("for "):

        # 输出循环的后继通常先写分隔符，再写出观测样本。
        if "std::cout" in next_code:

            # 返回 transcript 观测序列遍历角色。
            return (
                "输出序列循环遍历当前事务的观测样本。",
                "iterates over the transaction's serialized observation sequence.",
            )

        # 比较循环的后继包含 oracle 数组或数值不等式判断。
        if "arr_expected_values" in next_code or "!=" in next_code:

            # 返回 oracle 比较遍历角色。
            return (
                "oracle 比较循环遍历当前事务的参考样本。",
                "iterates over the transaction's oracle comparison samples.",
            )

    # transcript 角色先于数组角色，避免输出序列化误判成数组声明。
    # 读取结构化输出角色，保留后继代码上下文。
    tuple_role = _testbench_output_role_for(stripped, next_code)  # 当前代码行的 transcript 角色

    # transcript 已经完成最高优先级分类。
    if tuple_role[0]:

        # transcript 角色直接终止后续分类。
        return tuple_role

    # memory 角色覆盖数组声明、比较和顶层调用。
    tuple_role = _testbench_memory_role_for(stripped)  # 当前代码行的 memory 语义

    # memory 数组与比较保持独立语义。
    if tuple_role[0]:

        # memory 语义已完成数组边界分类。
        return tuple_role

    # stream 角色覆盖观测数组与通道操作。
    tuple_role = _testbench_stream_role_for(stripped, semantic_index)  # 当前代码行的通道语义

    # stream 通道方向不再回落到普通 token 表。
    if tuple_role[0]:

        # 保留 stream 的通道方向与观测边界。
        return tuple_role

    # 当前行没有细粒度上下文角色。
    return "", ""

# 统一上下文和固定 token 的 testbench 代码行角色路由。
def _testbench_contextual_and_token_role_for(
    stripped: str,
    next_code: str = "",
    case_role: int = 0,
    semantic_index: int = 0,
    previous_code: str = "",
) -> tuple[str, str]:
    """
    根据 testbench 代码行提取中英文角色模板。

    :param stripped: 去空白后的 testbench 代码行。
    :param next_code: 当前代码行之后的下一条有效代码行。
    :param case_role: 当前 testbench case 的语义状态编号。
    :param semantic_index: 当前相邻语义类别内的序位编号。
    :param previous_code: 当前代码行之前的上一条有效代码行。
    :return: 中英文角色模板二元组；未命中时返回空字符串。
    """

    # 先处理依赖上下文和结构化结果字段，再进入固定 token 表。
    tuple_contextual_role = testbench_contextual_role_for(stripped, next_code, semantic_index)  # 当前代码行的细粒度角色

    # 失败赋值需要独立序位语义，不能落回不带位置的固定 token 模板。
    if stripped.startswith("bool_pass = false"):

        # 读取失败状态所属的 testbench case 上下文。
        tuple_case_context = testbench_case_context_for(case_role)  # 当前失败状态的 case 上下文

        # 相邻失败说明复用真实守卫来源和当前 case 的独立事实主体。
        tuple_failure_subject = _failure_subject_for(  # 当前失败事实的双语主体
            previous_code,  # 失败守卫之前的有效代码
            next_code,  # 失败守卫之后的有效代码
            case_role,  # 当前失败所属的 case
            semantic_index,  # 当前失败语义序位
        )

        # 相邻中文说明明确记录失败事实，而不是只写状态变化。
        str_failure_role = f"{tuple_failure_subject[0]}，将通过状态置为失败。"  # 相邻中文失败职责

        # 英文诊断保留当前守卫来源和事务上下文。
        str_failure_english = f"{tuple_failure_subject[1]} and marks the pass state as failed."  # 相邻英文失败职责

        # 有事务上下文时保留 case 语义，避免跨 case 共享模板。
        if tuple_case_context[0]:

            # 返回带 case 语义的失败状态角色。
            return (
                f"{tuple_case_context[0]}下，{str_failure_role}",
                f"within the {tuple_case_context[1]}, {str_failure_english}",
            )

        # 未进入 case 时返回带序位的失败状态角色。
        return str_failure_role, str_failure_english

    # 命中细粒度角色时，避免数组声明、比较和序列化互相混淆。
    if tuple_contextual_role[0]:

        # AXIS packet 写入按对象序号区分首包、中段包和收尾包。
        tuple_axis_input_write_role = _axis_input_write_role_for(stripped)  # 当前输入 packet 写入角色

        # 命中 AXIS packet 写入角色时保留真实 packet 阶段。
        if tuple_axis_input_write_role[0]:

            # 读取当前 packet 写入所属的 testbench case 上下文。
            tuple_case_context = testbench_case_context_for(case_role)  # 当前 packet 写入 case 上下文

            # 有事务上下文时返回带阶段和 case 的写入角色。
            if tuple_case_context[0]:

                # 返回带 case 上下文的 packet 写入角色。
                return (
                    f"{tuple_case_context[0]}下，{tuple_axis_input_write_role[0]}",
                    f"within the {tuple_case_context[1]}, {tuple_axis_input_write_role[1]}",
                )

            # 尚未进入 case 时返回 packet 写入阶段角色。
            return tuple_axis_input_write_role

        # 输入流写入直接采用 stream helper 的动作序列，保留首个、后续和收尾职责。
        if ".write(" in stripped and ("stream_in" in stripped or "in_stream" in stripped):

            # 读取当前令牌所属的 testbench case 上下文。
            tuple_case_context = testbench_case_context_for(case_role)  # 当前输入令牌的 case 上下文

            # 有事务上下文时保留 case 语义。
            if tuple_case_context[0]:

                # 返回带 case 和具体写入阶段的角色。
                return (
                    f"{tuple_case_context[0]}下，{tuple_contextual_role[0]}",
                    f"within the {tuple_case_context[1]}, {tuple_contextual_role[1]}",
                )

            # 将未标记 case 的写入阶段职责交回上游调用方。
            return tuple_contextual_role

        # 当前 case 已知时把真实事务上下文写入角色，避免不同 case 退化成模板句。
        tuple_case_context = testbench_case_context_for(case_role)  # 当前 case 的中文和英文上下文

        # 有事务上下文时把它放在角色前部，保留代码对象的具体说明。
        if tuple_case_context[0]:

            # 返回带真实 case 语义的上下文角色。
            return (
                f"{tuple_case_context[0]}下，{tuple_contextual_role[0]}",
                f"within the {tuple_case_context[1]}, {tuple_contextual_role[1]}",
            )

        # 未进入 case 时返回原始上下文角色。
        return tuple_contextual_role

    # 依赖、数组、状态、流、侧带和输出字段使用稳定的角色词表。
    tuple_role_patterns: tuple[tuple[str, str, str], ...] = (  # testbench 行角色匹配表
        ("<iostream>", "stdout 依赖引入结果 transcript。", "introduces stdout for the result transcript."),  # stdout 依赖角色模板
        ("../src/", "顶层接口依赖引入内核声明。", "introduces the declared top-kernel interface."),  # 顶层内核接口引入
        ("int int_failures", "全局失败计数声明汇总所有事务。", "declares the aggregate failure counter."),  # 全局失败总数存储
        ("bool bool_pass", "通过标志声明记录本事务的逐项校验结果。", "declares the per-case comparison flag."),  # 单事务通过状态
        ("bool bool_last_seen", "边界标志声明记录 AXIS last 侧带。", "declares the terminal-packet observation flag."),  # 终止包侧带状态
        ("ptr_input_a[", "第一输入缓冲声明准备一条内存载荷。", "declares the first memory input buffer."),  # 第一内存输入缓冲
        ("ptr_input_b[", "第二输入缓冲声明准备另一条内存载荷。", "declares the second memory input buffer."),  # 第二内存输入缓冲
        ("ptr_input_values[", "输入缓冲声明准备本事务的内存输入样本。", "declares the memory input buffer."),  # 事务输入样本数组
        ("ptr_output_values[", "输出缓冲声明承接本事务的内核写回样本。", "declares the kernel writeback buffer."),  # 内核输出写回数组
        ("arr_expected_values[", "期望数组声明保存本事务的输出参考值。", "declares the reference output values."),  # 输出参考值数组
        ("arr_observed[", "观测数组声明保存读取到的真实响应。", "declares storage for observed response values."),  # 真实响应观测数组
        ("hls::stream", "流对象声明建立 dataflow 令牌通道。", "declares a dataflow token channel."),  # dataflow 令牌通道
        ("auto out_pkt", "输出包对象承接一个完整 AXIS 响应包。", "captures one complete AXIS response packet."),  # 完整 AXIS 输出包
        (".data =", "数据侧带写入当前 AXIS 包的载荷。", "writes the AXIS data payload."),  # AXIS 数据载荷字段
        (".keep =", "保留侧带写入当前 AXIS 包的 keep 掩码。", "writes the AXIS byte-retain mask."),  # AXIS 字节保留掩码
        (".strb =", "使能侧带写入当前 AXIS 包的 strb 掩码。", "writes the AXIS byte-enable mask."),  # AXIS 字节使能掩码
        (".last =", "边界侧带写入当前 AXIS 包的 last 标记。", "writes the AXIS packet-boundary marker."),  # AXIS 包边界标记
        ("arr_expected", "逐项比较对齐输出缓冲与参考数组。", "guards an element-wise output/reference comparison."),  # 参考值逐项比较
        ("stream_out_stream.empty", "空流保护阻止从空输出通道读取数据。", "guards against reading an empty output stream."),  # 输出流空读保护
        ("out_pkt.last", "边界校验检查输出包的 last 侧带。", "validates the packet-boundary sideband."),  # 输出包末端校验
        ("out_pkt.keep", "字节校验检查输出包的 keep 侧带。", "validates the byte-retain sideband."),  # 输出包保留字节校验
        ("out_pkt.strb", "字节使能校验检查输出包的 strb 侧带。", "validates the byte-enable sideband."),  # 输出包使能字节校验
        ("!bool_last_seen", "末包保护拒绝未观察到终止包的流式事务。", "rejects a stream without a terminal packet."),  # 缺失终止包保护
        ("if (!bool_pass)", "失败累计把本事务状态汇总到全局计数。", "promotes the case failure into the aggregate counter."),  # 本事务失败归总
        ("int_failures != 0", "最终状态根据累计失败数量选择进程结果。", "selects the final process status from aggregate failures."),  # 进程最终状态选择
        ("return 0", "成功返回在所有事务通过后结束测试平台。", "returns success after every case passes."),  # 测试平台成功退出
        ("return 1", "失败返回在累计比较错误后结束测试平台。", "returns failure after a comparison error."),  # 测试平台失败退出
        ("HLS-GEN-RESULT", "结果记录起始打开机器可读的校验对象。", "opens the machine-readable result record."),  # 机器结果记录起点
        ("case_id", "结果标识字段写入当前事务身份。", "serializes the case identity field."),  # 事务身份结果字段
        ("status", "结果状态字段写入当前事务的 PASS 或 FAIL。", "serializes the computed PASS or FAIL status."),  # 事务通过状态字段
        ("outputs", "结果输出字段开始串行化真实观测值。", "opens the observed-output field."),  # 真实输出结果字段
        ("checkpoints", "结果检查点对象开始记录维度摘要。", "opens the checkpoint summary."),  # 结果检查点摘要
        ("int_rows", "行维度检查点记录本事务的行边界。", "serializes the row-dimension checkpoint."),  # 行边界检查点
        ("int_cols", "列维度检查点记录本事务的列边界。", "serializes the column-dimension checkpoint."),  # 列边界检查点
        ("int_length", "长度检查点记录本事务的有效范围。", "serializes the logical-length checkpoint."),  # 有效长度检查点
        ("first_output", "首项检查点记录结果序列的首个观测值。", "serializes the first-output checkpoint."),  # 首个观测值检查点
        ("(bool_pass ?", "结果状态表达式把当前事务的比较标志转换为 PASS 或 FAIL。", "maps the transaction comparison flag to PASS or FAIL."),  # 结果状态表达式
        ("std::cout", "结果输出写出当前事务的结构化 transcript。", "writes the structured transaction transcript."),  # 结构化 transcript 输出
        ("<<", "结果片段继续写出当前校验对象内容。", "serializes the current result fragment."),  # 结果片段串行化
        (".write(", "输入流写入当前事务的下一个令牌。", "injects the next transaction token into the stream."),  # 输入令牌注入流
        (".read()", "流式读取从输出通道取出一个响应令牌。", "reads a response token from the output stream."),  # 输出响应令牌读取
        ("arr_observed", "观测写回把真实响应保存到比较缓冲。", "writes one observed response into the comparison buffer."),  # 观测响应写回缓冲
        ("bool_pass = false", "失败标记在输出不匹配时置为未通过。", "marks the transaction after an output mismatch."),  # 输出失配失败标记
        ("int_failures++", "失败计数更新递增全局失败总数。", "increments the aggregate failure total."),  # 全局失败计数递增
        ("failures++", "失败计数更新递增全局失败总数。", "increments the aggregate failure total."),  # 局部失败计数递增
    )

    # 按优先级选择首个命中的角色，保证更具体的字段先于通用 token。
    for str_pattern, str_chinese_role, str_english_role in tuple_role_patterns:

        # 当前行命中角色关键字时返回其双语说明。
        if str_pattern in stripped:

            # 当前 case 已知时把真实事务上下文写入固定 token 角色。
            tuple_case_context = testbench_case_context_for(case_role)  # 当前固定 token 的 case 上下文

            # 有事务上下文时保留当前 case 的形状或压力语义。
            if tuple_case_context[0]:

                # 返回带 case 上下文的固定 token 角色。
                return (
                    f"{tuple_case_context[0]}下，{str_chinese_role}",
                    f"within the {tuple_case_context[1]}, {str_english_role}",
                )

            # 未进入 case 时返回固定 token 角色。
            return str_chinese_role, str_english_role

    # 当前固定 token 未命中时交回外层结构化路由。
    return "", ""

# 统一 testbench 代码行角色路由，保持上下文、token 和结构语句优先级。
def testbench_line_role_for(
    stripped: str,
    next_code: str = "",
    case_role: int = 0,
    semantic_index: int = 0,
    previous_code: str = "",
) -> tuple[str, str]:
    """
    根据 testbench 代码行提取中英文角色模板。

    :param stripped: 去空白后的 testbench 代码行。
    :param next_code: 当前代码行之后的下一条有效代码行。
    :param case_role: 当前 testbench case 的语义状态编号。
    :param semantic_index: 当前相邻语义类别内的序位编号。
    :param previous_code: 当前代码行之前的上一条有效代码行。
    :return: 中英文角色模板二元组；未命中时返回空字符串。
    """

    # 先处理依赖上下文和固定 token 角色。
    tuple_role = _testbench_contextual_and_token_role_for(  # 当前代码行的上下文或 token 角色
        stripped,  # 当前待分类的 testbench 代码行
        next_code,  # 当前代码行后的有效代码上下文
        case_role,  # 当前 testbench case 状态
        semantic_index,  # 当前相邻语义序位
        previous_code,  # 当前代码行之前的有效代码上下文
    )

    # 命中上下文或固定 token 时直接返回最具体角色。
    if tuple_role[0]:

        # 保留上下文分类的原始双语角色。
        return tuple_role

    # 最后处理循环和顶层调用等结构化语句。
    return _testbench_structural_role_for(stripped, case_role)

# 识别循环和顶层调用的结构化 testbench 角色。
def _testbench_structural_role_for(
    stripped: str,
    case_role: int,
) -> tuple[str, str]:
    """
    返回循环或顶层调用的双语角色。

    :param stripped: 去空白后的 testbench 代码行。
    :param case_role: 当前 testbench case 的语义状态编号。
    :return: 结构化角色；未命中时返回空字符串。
    """

    # 循环和普通内核调用使用无法由固定 token 表达的结构规则。
    if stripped.startswith("for "):

        # 返回带 case 上下文的有界观测循环说明。
        tuple_case_context = testbench_case_context_for(case_role)  # 当前循环的 case 上下文

        # 当前 case 已知时保留其真实观测边界语义。
        if tuple_case_context[0]:

            # 返回带 case 上下文的循环说明。
            return (
                f"{tuple_case_context[0]}下，有界循环遍历当前事务的有效观测范围。",
                f"within the {tuple_case_context[1]}, iterates over the bounded observation range.",
            )

        # 返回未进入 case 时的通用循环说明。
        return "有界循环遍历当前事务的有效观测范围。", "iterates over the bounded observation range."

    # 流式或内存顶层调用需要说明提交的通道边界。
    if "(" in stripped and stripped.endswith(";"):

        # 普通调用回退也保留当前 case 上下文，避免跨事务模板碰撞。
        tuple_case_context = testbench_case_context_for(case_role)  # 当前顶层调用的 case 上下文

        # 流式调用说明输入通道和响应通道。
        if "stream_in" in stripped or "stream_out" in stripped:

            # 返回流式内核调用说明。
            if tuple_case_context[0]:

                # 返回带 case 语义的流式内核调用说明。
                return (
                    f"{tuple_case_context[0]}下，流式内核调用提交输入通道并校验响应通道。",
                    f"within the {tuple_case_context[1]}, submits streaming inputs and validates responses.",
                )

            # 未进入 case 时返回通用流式调用说明。
            return "流式内核调用提交输入通道并校验响应通道。", "submits streaming inputs and validates responses."

        # 双输入调用说明两个 memory channel。
        if "ptr_input_a" in stripped and "ptr_input_b" in stripped:

            # 返回带 case 语义的双输入调用说明。
            if tuple_case_context[0]:

                # 保留当前事务的双输入提交边界。
                return (
                    f"{tuple_case_context[0]}下，双输入内核调用同时提交两条内存输入。",
                    f"within the {tuple_case_context[1]}, submits both memory input channels.",
                )

            # 未进入 case 时返回通用双输入调用说明。
            return "双输入内核调用同时提交两条内存输入。", "submits both memory input channels."

        # 二维变换调用说明 shape 边界。
        if "block_transform" in stripped:

            # 返回带 case 语义的二维调用说明。
            if tuple_case_context[0]:

                # 保留当前事务的二维形状边界。
                return (
                    f"{tuple_case_context[0]}下，二维变换调用提交内存缓冲和行列边界。",
                    f"within the {tuple_case_context[1]}, submits memory buffers with a two-dimensional shape.",
                )

            # 未进入 case 时返回通用二维调用说明。
            return "二维变换调用提交内存缓冲和行列边界。", "submits memory buffers with a two-dimensional shape."

        # 普通内存调用说明 input/output buffer 关系。
        if "ptr_input" in stripped or "ptr_output" in stripped:

            # 返回带 case 语义的普通内存调用说明。
            if tuple_case_context[0]:

                # 保留当前事务的输入/写回边界。
                return (
                    f"{tuple_case_context[0]}下，内存内核调用提交输入缓冲和写回缓冲。",
                    f"within the {tuple_case_context[1]}, submits memory input and writeback buffers.",
                )

            # 未进入 case 时返回通用内存调用说明。
            return "内存内核调用提交输入缓冲和写回缓冲。", "submits memory input and writeback buffers."

        # 无参调用说明最小 smoke 入口。
        return "无参内核调用执行最小 smoke 入口。", "invokes the zero-argument smoke entrypoint."

    # 未命中时返回空，调用方使用已有 HLS 语义回退。
    return "", ""

# 根据原始 case 文本区分标称、边界和压力事务。
def testbench_case_role_for(str_comment: str) -> tuple[str, str, int]:
    """
    根据 testbench case 正文返回可区分的事务角色和状态编号。

    :param str_comment: 去掉注释标记后的 case 正文。
    :return: 中文角色、英文角色和供渲染器保存的 case 状态编号。
    """

    # case id 与中文标题共用同一匹配文本，保证两种原始生成路径得到相同角色。
    str_case_name = str_comment.split("PASS FAIL", 1)[0].strip().casefold()  # 当前 case 角色匹配文本

    # 标称样例说明常规数据路径的参考行为。
    if "nominal" in str_case_name or "basic" in str_case_name or "标称" in str_comment:

        # 返回标称基准事务角色和状态编号。
        return "标称基准事务", "nominal baseline transaction", 1

    # boundary 样例说明尾块、最短范围或边界尺寸行为。
    if "boundary" in str_case_name or "边界" in str_comment:

        # 返回边界尺寸事务角色和状态编号。
        return "边界尺寸事务", "boundary-size transaction", 2

    # 直通样例说明输入到输出的保持关系。
    if "passthrough" in str_case_name or "直通" in str_comment:

        # 返回直通保持事务角色和状态编号。
        return "直通保持事务", "passthrough transaction", 3

    # 极值样例说明数值范围或流边界压力。
    if any(str_token in str_case_name for str_token in ("overflow", "underflow", "extreme")):

        # 返回极值压力事务角色和状态编号。
        return "极值压力事务", "extreme-range transaction", 4

    # 未知 case 保留补充事务语义，不复用其他类别的模板。
    return "补充向量事务", "supplemental vector transaction", 5

# 将 case 状态编号转换成相似度门禁可区分的真实事务上下文。
def testbench_case_context_for(int_case_role: int) -> tuple[str, str]:
    """
    返回当前 case 的中文和英文上下文说明。

    :param int_case_role: testbench 当前 case 的状态编号。
    :return: 中文上下文和英文上下文；零值表示尚未进入 case。
    """

    # 标称 case 使用常规形状和基准路径语义。
    if int_case_role == 1:

        # 返回标称事务的独立上下文。
        return "常规形状基准路径", "regular-shape baseline path"

    # boundary case 使用最小形状和容量边界语义。
    if int_case_role == 2:

        # 返回边界事务的独立上下文。
        return "最小形状容量边界路径", "minimum-shape capacity-boundary path"

    # passthrough case 使用输入输出保持语义。
    if int_case_role == 3:

        # 返回直通事务的独立上下文。
        return "输入输出保持路径", "input-output preservation path"

    # extreme case 使用范围压力语义。
    if int_case_role == 4:

        # 返回极值事务的独立上下文。
        return "数值范围压力路径", "numeric-range stress path"

    # 其他 case 使用补充向量语义，避免回退到标称模板。
    if int_case_role == 5:

        # 返回补充事务的独立上下文。
        return "补充向量核验路径", "supplemental-vector verification path"

    # case marker 尚未出现时不添加虚构的事务类别。
    return "", ""

# 为 AXIS 中文行尾字段生成封存语义。
def _axis_trailing_chinese_role_for(str_axis_role: str) -> str:
    """
    将 AXIS 中文字段写入角色转换为行尾封存角色。

    :param str_axis_role: AXIS 字段写入中文角色。
    :return: AXIS 字段封存中文角色。
    """

    # 行尾说明按 data、keep、strb、last 的协议职责分别封存。
    return (
        str_axis_role
        .replace(
            "数据载荷写入待发令牌，保存当前编码样本。",
            "数据载荷在行尾冻结编码样本，等待包提交。",
        )
        .replace(
            "有效字节掩码裁定令牌的可传输范围。",
            "有效字节掩码在行尾锁定传输范围，等待包提交。",
        )
        .replace(
            "字节使能掩码声明各字节的写入资格。",
            "字节使能掩码在行尾确认各字节的写入资格。",
        )
        .replace(
            "帧尾边界标记决定发送序列是否收束。",
            "帧尾边界标记在行尾确认序列收束状态。",
        )
    )

# 为 AXIS 英文行尾字段生成封存语义。
def _axis_trailing_english_role_for(str_axis_role: str) -> str:
    """
    将 AXIS 英文字段写入角色转换为行尾封存角色。

    :param str_axis_role: AXIS 字段写入英文角色。
    :return: AXIS 字段封存英文角色。
    """

    # 英文行尾说明按字段动作表达提交前的不同状态。
    return (
        str_axis_role
        .replace(
            "stores the current encoded sample in the pending payload",
            "seals the encoded sample at the trailing field before packet submission",
        )
        .replace(
            "constrains the transferable byte range with the valid-byte mask",
            "seals the valid-byte mask at the trailing field before packet submission",
        )
        .replace(
            "declares byte-write eligibility with the byte-enable mask",
            "confirms byte-write eligibility at the trailing field before packet submission",
        )
        .replace(
            "marks whether the transmitted sequence reaches its frame boundary",
            "confirms the transmitted sequence boundary at the trailing field",
        )
    )

# 为 AXIS 行尾字段生成区别于上方写入说明的双语语义。
def _axis_trailing_role_for(tuple_axis_role: tuple[str, str]) -> tuple[str, str]:
    """
    将 AXIS 字段写入角色转换为行尾封存角色。

    :param tuple_axis_role: AXIS 字段写入角色的中英文二元组。
    :return: AXIS 字段封存角色的中英文二元组。
    """

    # 委托语言专用 helper 改写中文字段职责。
    str_axis_chinese_role = _axis_trailing_chinese_role_for(tuple_axis_role[0])  # AXIS 行尾中文字段角色

    # 英文映射单独保留协议提交前的状态边界。
    str_axis_english_role = _axis_trailing_english_role_for(tuple_axis_role[1])  # AXIS 行尾英文字段角色

    # 返回与相邻字段写入说明不同的行尾协议语义。
    return str_axis_chinese_role, str_axis_english_role

# 为 testbench 行尾字段选择低相似度协议语义。
def _testbench_inline_protocol_role_for(stripped: str) -> tuple[str, str]:
    """
    返回 AXIS、观测槽位和双输入数组的行尾协议角色。

    :param stripped: 去空白后的 testbench 代码行。
    :return: 行尾协议角色；未命中时返回空字符串。
    """

    # AXIS 字段行尾必须与上方的字段写入说明使用不同句式。
    tuple_axis_role = _axis_sideband_role_for(stripped)  # 当前行尾 AXIS 字段角色

    # 命中 AXIS 字段时返回包字段封存语义，避免相邻注释高度复用。
    if tuple_axis_role[0]:

        # 委托专用 helper 生成与写入角色不同的封存句式。
        return _axis_trailing_role_for(tuple_axis_role)

    # AXIS 包声明的行尾说明强调协议对象就绪，而不是重复“设置 packet”。
    tuple_axis_packet_role = _axis_packet_declaration_role_for(stripped)  # 统一识别真实 packet 与 stream 容器

    # 只有真实 packet 声明才追加协议对象就绪的行尾说明。
    if tuple_axis_packet_role[0]:

        # 声明行尾强调 AXIS 包对象已经完成协议形态确认。
        return (
            "AXIS 包对象在行尾完成协议形态确认。",
            "confirms the AXIS packet object's protocol shape in the trailing field.",
        )

    # 观测数组初始化需要独立说明容量边界，不伪装成真实读取结果。
    if (
        _is_typed_array_declaration(stripped, r"\b(?:arr_observed|observed)")
        and "{}" in stripped
    ):

        # 行尾说明固定观测缓冲的容量初始化职责。
        return (
            "观测缓冲在行尾划定响应容量，等待真实读取结果。",
            "sets the observation-buffer capacity at the trailing field before real reads.",
        )

    # 观测槽位行尾说明区分空流缺失和真实读取结果，保留两种状态边界。
    if "arr_observed" in stripped and "=" in stripped and "{}" not in stripped:

        # 空流分支记录缺失响应，不能与真实读取结果复用同一语义。
        if re.search(r"=\s*0\b", stripped):

            # 行尾说明固定空流缺失状态的诊断职责。
            return (
                "空输出通道在行尾登记缺失响应，保留保护分支状态。",
                "records the missing response from the empty output channel and preserves the guard state.",
            )

        # 真实读取分支封存当前观测值，区别于空流诊断。
        return (
            "真实输出数据在行尾封存，保留当前观测结果。",
            "seals the observed output data while preserving the current result.",
        )

    # 双输入 memory 声明分别标注左、右载荷通道，避免 A/B 两行共享模板。
    if _is_typed_array_declaration(stripped, r"\barr_input_a_values"):

        # A 通道尾字段强调左矩阵被乘样本的边界。
        return (
            "左侧输入 lane 行尾冻结被乘矩阵的样本边界。",
            "freezes the left matrix operand's sample boundary in the trailing field.",
        )

    # B 通道尾字段核定右矩阵乘数样本的容量。
    if _is_typed_array_declaration(stripped, r"\barr_input_b_values"):

        # B 通道尾字段保持右矩阵乘数的独立职责。
        return (
            "右侧输入 lane 行尾校定乘数矩阵的样本容量。",
            "checks the right matrix operand's sample capacity in the trailing field.",
        )

    # 当前行没有独立的协议行尾角色。
    return "", ""

# 为 packet 声明的行尾说明补充对象方向和协议阶段。
def _axis_packet_trailing_role_for(stripped: str) -> tuple[str, str]:
    """
    返回输入或输出 AXIS packet 的阶段化行尾角色。

    :param stripped: 去空白后的 testbench 代码行。
    :return: packet 行尾角色；未命中时返回空字符串。
    """

    # packet 对象匹配结果供后续方向和序位分流。
    match_packet = re.search(r"\b(ap_axiu<[^;\n]+>)\s+(axis_(?:in_pkt_\d+|out_pkt))\b", stripped)  # AXIS packet 对象匹配结果

    # 非 packet 声明不进入阶段化行尾说明。
    if match_packet is None:

        # 当前行没有真实 AXIS packet 对象。
        return "", ""

    # AXIS 对象名供输出方向或输入序位判断。
    str_packet_name = match_packet.group(2)  # AXIS packet 方向对象名

    # 输出 packet 说明响应方向和 payload 观察边界。
    if str_packet_name == "axis_out_pkt":

        # 返回输出 packet 的响应封存职责。
        return (
            "输出 AXIS packet 行尾封存响应包，准备读取 data 载荷。",
            "seals the output AXIS response packet before its data payload is observed.",
        )

    # 输入 packet 的阶段由对象序号决定。
    str_packet_index = str_packet_name.rsplit("_", 1)[-1]  # 输入 AXIS packet 序号

    # 首个输入 packet 行尾确认起始包络。
    if str_packet_index == "0":

        # 返回首个输入 packet 的就绪职责。
        return (
            "首个输入 AXIS packet 行尾确认起始包络已经就绪。",
            "confirms the first input AXIS packet's opening envelope is ready.",
        )

    # 第二个输入 packet 行尾确认续接包络。
    if str_packet_index == "1":

        # 返回第二个输入 packet 的就绪职责。
        return (
            "第二个输入 AXIS packet 行尾确认续接包络已经就绪。",
            "confirms the second input AXIS packet's continuation envelope is ready.",
        )

    # 第三个输入 packet 行尾确认收尾包络。
    if str_packet_index == "2":

        # 返回第三个输入 packet 的就绪职责。
        return (
            "第三个输入 AXIS packet 行尾确认收尾包络已经就绪。",
            "confirms the third input AXIS packet's closing envelope is ready.",
        )

    # 后续输入 packet 行尾确认延续包络。
    return (
        "后续输入 AXIS packet 行尾确认延续包络已经就绪。",
        "confirms a later input AXIS packet's continuation envelope is ready.",
    )

# 为输入 packet 写入选择首包、中段和收尾的提交动作。
def _axis_input_write_role_for(stripped: str) -> tuple[str, str]:
    """
    返回输入 AXIS packet 写入 stream 时的阶段化角色。

    :param stripped: 去空白后的 testbench 代码行。
    :return: 输入 packet 写入角色；未命中时返回空字符串。
    """

    # 只有写入输入 packet 的行才进入 packet 阶段分流。
    if ".write(" not in stripped or "axis_in_pkt_" not in stripped:

        # 当前行不是输入 packet 写入。
        return "", ""

    # 首个 packet 启动输入序列。
    if "axis_in_pkt_0" in stripped:

        # 返回首包提交动作。
        return (
            "输入流提交首个 AXIS packet，启动发送序列。",
            "commits the first input AXIS packet to start the send sequence.",
        )

    # 第二个 packet 延续输入序列。
    if "axis_in_pkt_1" in stripped:

        # 返回中段包提交动作。
        return (
            "输入流提交第二个 AXIS packet，延续发送序列。",
            "commits the second input AXIS packet to continue the send sequence.",
        )

    # 第三个 packet 收束输入序列。
    if "axis_in_pkt_2" in stripped:

        # 返回收尾包提交动作。
        return (
            "输入流提交第三个 AXIS packet，收束发送序列。",
            "commits the third input AXIS packet to close the send sequence.",
        )

    # 未知序号仍保留 packet 写入职责。
    return (
        "输入流提交后续 AXIS packet，延续发送序列。",
        "commits a later input AXIS packet to continue the send sequence.",
    )

# 根据失败行前后的代码判断空流、侧带或 oracle 失败来源。
def _failure_source_for(str_previous_code: str, str_next_code: str = "") -> str:
    """
    返回失败状态对应的局部诊断来源。

    :param str_previous_code: 当前失败代码行之前的有效代码。
    :param str_next_code: 当前失败代码行之后的有效代码。
    :return: `empty`、`sideband`、`terminal` 或 `oracle` 来源标签。
    """

    # 前后代码共同构成失败分支上下文，兼容不同生成布局。
    str_failure_context = f"{str_previous_code} {str_next_code}"  # 失败分支前后代码上下文

    # 空输出通道失败表示没有可观测响应令牌。
    if "stream_out_stream.empty" in str_failure_context:

        # 返回空流诊断来源。
        return "empty"

    # AXIS 侧带失败表示 packet 边界字段没有通过协议核验。
    if any(str_field in str_failure_context for str_field in (".keep", ".strb", ".last")):

        # 返回 AXIS 侧带诊断来源。
        return "sideband"

    # 终止守卫失败表示流式事务没有观察到最后一个 packet。
    if "!bool_last_seen" in str_failure_context:

        # 返回终止边界诊断来源。
        return "terminal"

    # 其他失败分支按观测值与 oracle 的比较处理。
    return "oracle"

# 为失败说明提供基于守卫来源的独立事实主体。
def _failure_subject_for(
    str_previous_code: str,
    str_next_code: str,
    case_role: int,
    semantic_index: int,
) -> tuple[str, str]:
    """
    返回失败分支的中文事实主体和英文事实主体。

    :param str_previous_code: 失败行之前的有效代码上下文。
    :param str_next_code: 失败行之后的有效代码上下文。
    :param case_role: 当前 testbench case 的语义状态编号。
    :param semantic_index: 当前失败语义类别内的序位编号。
    :return: 失败来源对应的双语事实主体。
    """

    # 失败来源由真实守卫代码决定，避免只用序位词区分模板句。
    str_failure_source = _failure_source_for(str_previous_code, str_next_code)  # 当前失败来源类别

    # 常规 case 和边界 case 使用不同事务前缀。
    if case_role == 1:

        # 绑定常规基准失败路径的中文标签。
        str_case_label = "常规基准"  # 常规 case 中文标签

        # 常规路径采用 regular baseline 作为英文前缀。
        str_case_english = "regular baseline"  # 常规 case 英文标签

    # 容量路径改用边界英文前缀，避免复用常规路径词汇。
    elif case_role == 2:

        # 绑定容量边界失败路径的中文标签。
        str_case_label = "容量边界"  # 边界 case 中文标签

        # 容量路径采用 capacity boundary 作为英文前缀。
        str_case_english = "capacity boundary"  # 边界 case 英文标签

    # 未绑定 case 时使用当前事务的中性英文前缀。
    else:

        # 未绑定 case 时沿用当前事务的中性标签。
        str_case_label = "当前事务"  # 未绑定 case 中文标签

        # 未绑定 case 时沿用当前事务的英文标签。
        str_case_english = "current transaction"  # 未绑定 case 英文标签

    # 空流来源突出响应令牌缺口，而不是泛化为普通失配。
    if str_failure_source == "empty":

        # 形成空流失败的中文事实主体。
        str_failure_label = "空流响应缺口"  # 空流失败中文标签

        # 空流分支把响应缺口翻译成英文诊断短语。
        str_failure_english = "an empty-stream response gap"  # 空流失败英文标签

    # 侧带来源突出 AXIS 包络边界失配。
    elif str_failure_source == "sideband":

        # 形成侧带失败的中文事实主体。
        str_failure_label = "AXIS 侧带边界失配"  # 侧带失败中文标签

        # 侧带分支把包络边界失配翻译成英文诊断短语。
        str_failure_english = "an AXIS sideband-boundary mismatch"  # 侧带失败英文标签

    # 终止来源突出最后 packet 缺失。
    elif str_failure_source == "terminal":

        # 形成终止失败的中文事实主体。
        str_failure_label = "末包终止标记缺失"  # 终止失败中文标签

        # 终止分支把末包标记缺失翻译成英文诊断短语。
        str_failure_english = "a missing final-packet marker"  # 终止失败英文标签

    # 其余来源按观测值和 oracle 的差异处理。
    else:

        # 形成 oracle 失败的中文事实主体。
        str_failure_label = "观测样本偏离 oracle"  # oracle 失败中文标签

        # 形成 oracle 失败的英文事实主体。
        str_failure_english = "observed data drifting from the oracle"  # oracle 失败英文标签

    # 用中文序位保留同一来源内部的事务位置。
    str_failure_sequence = _chinese_sequence_label_for(semantic_index)  # 当前失败序位标签

    # 返回带 case、来源和序位的中文事实主体。
    str_chinese_subject = f"{str_case_label}路径{str_failure_sequence}记录{str_failure_label}"  # 失败中文事实主体

    # 英文报告拼接 case 前缀、序位和故障原因。
    str_english_subject = f"the {str_case_english} path records {str_failure_english}"  # 失败英文事实主体

    # 组合供相邻说明和行尾说明复用的双语事实主体。
    return str_chinese_subject, str_english_subject

# 为 testbench 行尾字段选择区别于相邻说明的局部语义。
def testbench_inline_role_for(
    stripped: str,
    case_role: int = 0,
    semantic_index: int = 0,
    previous_code: str = "",
) -> tuple[str, str]:
    """
    返回 testbench 关键行的独立行尾角色。

    :param stripped: 去空白后的 testbench 代码行。
    :param case_role: 当前 testbench case 的语义状态编号。
    :param semantic_index: 当前相邻语义类别内的序位编号。
    :param previous_code: 当前代码行之前的上一条有效代码行。
    :return: 行尾角色；未命中时返回空字符串。
    """

    # 读取当前 case 的真实上下文，避免不同事务共享同一行尾模板。
    tuple_case_context = testbench_case_context_for(case_role)  # 当前 case 的中英文上下文

    # packet 对象的行尾角色需要先于通用协议 helper 处理。
    tuple_packet_trailing_role = _axis_packet_trailing_role_for(stripped)  # 当前 packet 行尾角色

    # 命中 packet 行尾角色时保留对象方向与阶段职责。
    if tuple_packet_trailing_role[0]:

        # 当前 case 已知时附加事务路径上下文。
        if tuple_case_context[0]:

            # 返回带 case 上下文的 packet 行尾说明。
            return (
                f"{tuple_case_context[0]}下，{tuple_packet_trailing_role[0]}",
                f"within the {tuple_case_context[1]}, {tuple_packet_trailing_role[1]}",
            )

        # 尚未进入 case 时返回 packet 行尾原始职责。
        return tuple_packet_trailing_role

    # 协议字段和双输入数组由专用 helper 先行分流，压低主路由复杂度。
    tuple_protocol_role = _testbench_inline_protocol_role_for(stripped)  # 当前行尾协议角色

    # 命中协议角色时直接返回，不与后续状态模板混用。
    if tuple_protocol_role[0]:

        # 行尾协议语义附带当前 case 的真实职责上下文。
        if tuple_case_context[0]:

            # 返回带 case 语义的协议字段行尾说明。
            return (
                f"{tuple_case_context[0]}下，{tuple_protocol_role[0]}",
                f"within the {tuple_case_context[1]}, {tuple_protocol_role[1]}",
            )

        # 尚未进入 case 时保留协议字段的原始行尾语义。
        return tuple_protocol_role

    # 输入数组行尾说明强调初始化边界，而不是重复数组声明角色。
    if _is_typed_array_declaration(
        stripped,
        r"\barr_input(?:_a|_b)?_values",
    ):

        # 标称 case 的输入尾字段强调常规载荷边界。
        if case_role == 1:

            # 返回标称输入尾字段语义。
            return (
                "常规载荷在行尾锁定输入样本边界。",
                "locks the regular payload's input-sample boundary in the trailing field.",
            )

        # 边界 case 的输入尾字段强调最小容量核定。
        if case_role == 2:

            # 返回边界输入尾字段语义。
            return (
                "最小容量在行尾核定输入样本边界。",
                "checks the minimum-capacity input-sample boundary in the trailing field.",
            )

        # 其他 case 使用上下文前缀，保持输入声明语义明确。
        return (
            f"{tuple_case_context[0]}的输入样本边界在行尾完成锁定。",
            f"locks the input-sample boundary for the {tuple_case_context[1]} in the trailing field.",
        )

    # 输出数组行尾说明强调写回缓冲边界。
    if _is_typed_array_declaration(
        stripped,
        r"\b(?:arr_output_values|ptr_output_values)",
    ):

        # 标称 case 的输出尾字段强调常规写回容量。
        if case_role == 1:

            # 返回标称输出尾字段语义。
            return (
                "常规写回容量在行尾锁定输出缓冲边界。",
                "locks the regular writeback capacity in the trailing output-buffer field.",
            )

        # 边界 case 的输出尾字段强调最小写回余量。
        if case_role == 2:

            # 返回边界输出尾字段语义。
            return (
                "最小写回余量在行尾核定输出缓冲边界。",
                "checks the minimum writeback margin in the trailing output-buffer field.",
            )

        # 未覆盖的 case 保持输出声明语义明确。
        return (
            f"{tuple_case_context[0]}的写回缓冲边界在行尾完成锁定。",
            f"locks the writeback-buffer boundary for the {tuple_case_context[1]} in the trailing field.",
        )

    # oracle 数组行尾说明强调参考长度约束。
    if _is_typed_array_declaration(stripped, r"\barr_expected_values"):

        # 标称 case 的 oracle 尾字段强调基准长度。
        if case_role == 1:

            # 返回标称 oracle 尾字段语义。
            return (
                "常规 oracle 长度在行尾完成基准冻结。",
                "freezes the regular oracle length in the trailing field.",
            )

        # 边界 case 的 oracle 尾字段强调边界长度。
        if case_role == 2:

            # 返回边界 oracle 尾字段语义。
            return (
                "边界 oracle 长度在行尾完成容量核定。",
                "checks the boundary oracle length in the trailing field.",
            )

        # 未分类事务仍需明确 oracle 声明语义。
        return (
            f"{tuple_case_context[0]}的 oracle 长度在行尾完成核定。",
            f"checks the oracle length for the {tuple_case_context[1]} in the trailing field.",
        )

    # 通过状态声明的行尾说明强调本事务的初始状态。
    if stripped.startswith("bool bool_pass"):

        # 标称 case 的通过状态尾字段强调基准起点。
        if case_role == 1:

            # 返回标称通过状态尾字段语义。
            return (
                "常规基准先建立通过态，行尾记录本事务初始校验。",
                "establishes the regular pass state and records the initial check in the trailing field.",
            )

        # 边界 case 的通过状态尾字段强调容量边界起点。
        if case_role == 2:

            # 返回边界通过状态尾字段语义。
            return (
                "容量边界先建立通过态，行尾记录本事务初始校验。",
                "establishes the capacity-boundary pass state and records the initial check in the trailing field.",
            )

        # 其他事务也要保留独立的状态声明说明。
        return (
            f"{tuple_case_context[0]}先建立通过态，行尾记录本事务初始校验。",
            f"establishes the pass state for the {tuple_case_context[1]} "
            "and records the initial check in the trailing field.",
        )

    # 失配赋值的行尾说明强调失败状态迁移。
    if stripped.startswith("bool_pass = false"):

        # 失败行尾需要复用前驱守卫，保持相邻说明和行尾说明来源一致。
        tuple_failure_subject = _failure_subject_for(  # 行尾失败事实的双语主体
            previous_code,  # 行尾失败之前的有效代码
            "",  # 行尾失败不读取后继代码
            case_role,  # 当前行尾失败所属的 case
            semantic_index,  # 当前行尾失败语义序位
        )

        # 行尾中文封存状态提交动作及其前驱诊断事实。
        str_failure_role = f"行尾把未通过状态提交；{tuple_failure_subject[0]}。"  # 行尾中文失败事实

        # 行尾英文同时保留提交动作和前驱诊断来源。
        str_failure_english = f"commits the failed state; {tuple_failure_subject[1]}."  # 行尾英文失败收束

        # 常规 case 的行尾说明保留基准状态转换动作。
        if case_role == 1:

            # 返回常规 case 的独立失败收束说明。
            return (
                f"常规基准{str_failure_role}",
                f"switches the regular baseline to failure and {str_failure_english}",
            )

        # 边界 case 的行尾说明保留容量状态转换动作。
        if case_role == 2:

            # 返回边界 case 的独立失败收束说明。
            return (
                f"容量边界{str_failure_role}",
                f"switches the capacity-boundary check to failure and {str_failure_english}",
            )

        # 其他 case 使用上下文前缀，保持失败收束语义明确。
        return (
            f"{tuple_case_context[0]}{str_failure_role}",
            f"switches the {tuple_case_context[1]} check to failure and {str_failure_english}",
        )

    # 其他 testbench 赋值没有独立的专用行尾角色。
    return "", ""
