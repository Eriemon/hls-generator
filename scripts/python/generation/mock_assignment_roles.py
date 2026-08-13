"""收拢 mock HLS source 里赋值角色规则和 inline 尾注生成逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 正则匹配负责识别槽位索引和局部变量尾缀。
import re

# 模式子模块继续提供左值主标识符抽取能力。
from .mock_assignment_patterns import assigned_symbol_name

# 输出窗口写回的 inline 尾注另拆成子模块，避免角色规则文件继续膨胀到尺寸上限之外。
from .mock_inline_outputs import assignment_inline_output_comment_text

# FIR DATAFLOW 的输出 FIFO 和输入 FIFO 尾注另拆成子模块，避免角色规则文件超过尺寸门禁。
from .mock_hls_fir_assignments import (
    fir_assignment_comment_text,
    fir_assignment_inline_comment_text,
)

# 通用 stream 来源尾注另拆成子模块，保留本模块作为兼容聚合入口。
from .mock_assignment_streams import assignment_inline_stream_source_comment_text

# 把常见 lane/slot 索引翻译成更具体的中文角色词，避免只靠数字区分重复注释。
def indexed_slot_role_text(str_left_text: str, str_symbol_name: str) -> str:
    """把数组槽位或带后缀的局部名翻译成角色词。

    参数:
        str_left_text: 当前赋值左值的净文本，dtype=str，unit=left-hand-side text。
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。

    返回:
        更具体的槽位角色词；未命中时返回空字符串，dtype=str，unit=slot role text。
    """

    # 先尝试从显式数组索引里抽取槽位序号。
    obj_index_match = re.search(r"\[(\d+)\]", str_left_text)  # 当前左值里抽取到的显式数组槽位序号

    # 命中索引时把常见的 0/1/2/3 翻译成更自然的槽位角色词。
    if obj_index_match:

        # 把显式数组槽位折算成后续注释可复用的中文角色词。
        return {
            "0": "首槽位",
            "1": "第二槽位",
            "2": "第三槽位",
            "3": "末槽位",
        }.get(obj_index_match.group(1), f"索引 {obj_index_match.group(1)} 槽位")

    # reduction tree 的 partial 缓冲常用名字后缀表达 lane 序号，这里也翻译成角色词。
    obj_suffix_match = re.search(r"(\d+)$", str_symbol_name)  # 当前符号尾缀里抽取到的局部序号

    # 命中尾部数字时同样返回稳定的角色词。
    if obj_suffix_match:

        # 把 partial 名字尾缀里的数字换成稳定的角色描述。
        return {
            "0": "首 partial",
            "1": "第二 partial",
            "2": "第三 partial",
            "3": "末 partial",
        }.get(obj_suffix_match.group(1), f"第 {obj_suffix_match.group(1)} 路 partial")

    # 其他左值没有显式槽位信息时返回空字符串。
    return ""

# 按左值里的槽位标记查表返回专属说明，避免 line buffer 这类重复结构反复写分支。
def slot_lookup_text(
    str_left_text: str,
    dict_slot_messages: dict[str, str],
) -> str:
    """按槽位标记查表返回专属说明。

    参数:
        str_left_text: 当前赋值左值的净文本，dtype=str，unit=left-hand-side text。
        dict_slot_messages: 槽位标记到中文说明的映射，dtype=dict[str, str]，unit=slot message map。

    返回:
        命中槽位标记时返回对应中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐个检查当前左值里是否包含预设的槽位标记。
    for str_slot_token, str_slot_message in dict_slot_messages.items():

        # 命中槽位标记时立即取回对应的专属说明。
        if str_slot_token in str_left_text:

            # 把当前槽位标记对应的中文说明交回调用方。
            return str_slot_message

    # 没有命中任何槽位标记时返回空字符串。
    return ""

# 返回 pattern 级赋值里最常见的标量/寄存器专属说明。
def specialized_assignment_scalar_comment_text(
    str_symbol_name: str,
    str_right_text: str,
) -> str:
    """返回 pattern 级赋值里最常见的标量/寄存器专属说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中标量/寄存器专属场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 2D block transform 的总样本量寄存器要明确它承接的是行列乘积。
    if str_symbol_name == "int_total" and "int_rows * int_cols" in str_right_text:

        # 直接返回 block transform 总样本量寄存器的专属长说明。
        return "int_total 在这里把当前二维块的总样本量固定成行数与列数的乘积，供后续各个 stage 共享同一条扁平遍历边界。"

    # dataflow/block helper 里的局部样本读取要点明来自哪条 stream。
    if str_symbol_name == "uint_sample" and "stream_mid_stream.read()" in str_right_text:

        # 直接返回递增阶段从中间 FIFO 取样的专属说明。
        return "uint_sample 在这里从 stream_mid_stream 取回当前 axis 样本，供递增计算阶段先在本地寄存器里完成 +1 处理。"

    # dataflow 重排后的列向阶段要明确当前样本来自 reorder stream。
    if str_symbol_name == "uint_sample" and "stream_reorder_stream.read()" in str_right_text:

        # 直接返回列向阶段从 reorder stream 取样的专属说明。
        return "uint_sample 在这里从 stream_reorder_stream 取回已经完成块重排的样本，供 col_pass 在本地寄存器里继续做列向处理。"

    # fence ordering 需要显式保留“先合并再写回”的中间结果节点。
    if (
        str_symbol_name == "uint_ordered_writeback"
        and "ptr_input_a[" in str_right_text
        and "ptr_input_b[" in str_right_text
    ):

        # 直接返回 fence writeback 中间寄存器的专属说明。
        return "uint_ordered_writeback 在这里先把 A/B 两路当前索引样本合成一个待写回值，显式保留 fence 前后的本地结果边界。"

    # reduction tree 的累加器初始化要显式说明它承担整轮归约结果的汇总职责。
    if str_symbol_name == "uint_tree_accum" and str_right_text == "0":

        # 直接返回树形归约主累加器的专属说明。
        return "uint_tree_accum 在这里清零当前归约事务的树形累加器，后续每个 4-sample 子块都会把部分和折叠回这个本地状态。"

    # 其他标量/寄存器赋值不在这里补专属说明。
    return ""

# 返回 partial 槽位和 line buffer 这类槽位敏感赋值的专属说明。
def specialized_assignment_slot_comment_text(
    str_symbol_name: str,
    str_left_text: str,
    str_right_text: str,
    str_slot_role: str,
) -> str:
    """返回 partial 槽位和 line buffer 这类槽位敏感赋值的专属说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_left_text: 当前赋值左值的净文本，dtype=str，unit=left-hand-side text。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。
        str_slot_role: 当前赋值命中的槽位角色词，dtype=str，unit=slot role text。

    返回:
        命中槽位敏感场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # reduction tree 的 partial 槽位要分别说明来自当前 4-sample 子块的哪一路输入。
    if str_symbol_name.startswith("uint_partial") and "ptr_input_values[" in str_right_text:

        # 先把四个固定 partial 寄存器的长说明收拢成查表映射。
        dict_partial_comments = {  # partial 寄存器名到长说明的查表映射。
            "uint_partial0": "uint_partial0 在这里锁住当前归约子块左半边的首个输入样本；越过 int_length 的尾部位置会直接补 0，避免无效值混入第一层左侧求和。",  # 左半边首样本寄存器的固定长说明。
            "uint_partial1": "uint_partial1 在这里锁住当前归约子块左半边的第二个输入样本，随后会和 uint_partial0 先折叠成左侧部分和。",  # 左半边第二个样本寄存器的固定长说明。
            "uint_partial2": "uint_partial2 在这里锁住当前归约子块右半边的第一个输入样本，供第二组局部求和先准备右侧首项。",  # 为右半边第一次折叠保留首个输入样本。
            "uint_partial3": "uint_partial3 在这里锁住当前归约子块右半边的末尾输入样本；尾块超出范围时会补 0，避免右侧部分和混入旧值。",  # 为右半边收尾折叠保留末尾样本或补零。
        }

        # 命中固定 partial 寄存器名字时直接返回查表结果。
        if str_symbol_name in dict_partial_comments:

            # 取回当前 partial 寄存器对应的固定长说明。
            return dict_partial_comments[str_symbol_name]

        # 其余 partial 名字回退到基于槽位角色词的长说明。
        return f"{str_symbol_name} 在这里锁住当前归约子块的{str_slot_role}输入样本；越过 int_length 的尾部位置会直接补 0，避免无效值混入树形求和。"

    # line buffer 的三个槽位要分别说清左邻居、中心样本和右邻居。
    if str_symbol_name == "arr_line_buf":

        # 先把 line buffer 三个槽位的长说明收拢成稳定查表。
        dict_line_buffer_comments = {  # line buffer 槽位到长说明的查表映射。
            "[0]": "arr_line_buf[0] 在这里装入当前输出点左侧的邻居样本；行首时会回退到边界值，保证 stencil 左边界可综合。",  # 左邻居槽位的固定长说明。
            "[1]": "arr_line_buf[1] 在这里锁住当前输出点对应的中心样本，供 3-tap stencil 的本地求和阶段直接复用。",  # 中心槽位的固定长说明。
            "[2]": "arr_line_buf[2] 在这里装入当前输出点右侧的邻居样本；行尾时会钳住最后一个有效输入，保留右边界行为。",  # 右邻居槽位的固定长说明。
        }

        # 按当前左值命中的槽位标记取回 line buffer 的专属说明。
        return slot_lookup_text(str_left_text, dict_line_buffer_comments)

    # 其他槽位敏感赋值不在这里补专属说明。
    return ""

# 返回本地缓冲类 pattern 赋值的专属说明。
def specialized_assignment_buffer_comment_text(
    str_symbol_name: str,
    str_right_text: str,
) -> str:
    """返回本地缓冲类 pattern 赋值的专属说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中本地缓冲场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 向量缩放/partition/stream block 的局部缓冲要点明它们承接的是当前块里的输入样本。
    if str_symbol_name in {"arr_wide_buf", "arr_local_buf", "arr_block_buf"} and "ptr_input_values[" in str_right_text:

        # 先把几类本地缓冲对应的事务角色词收拢成查表映射。
        dict_buffer_roles = {  # 本地缓冲名到事务角色词的查表映射。
            "arr_wide_buf": "一个 16-lane reshape 块的并行输入样本",  # reshape 本地块的事务角色词。
            "arr_local_buf": "一个 partition 块的局部输入样本",  # partition 缩放路径承接的局部输入块。
            "arr_block_buf": "当前 block 事务里的局部样本",  # block 事务本地块的角色词。
        }

        # 按当前缓冲名字取回更具体的事务角色词并拼成长说明。
        return f"{str_symbol_name} 在这里接住{dict_buffer_roles[str_symbol_name]}，让后续缩放或块级处理阶段直接复用这批本地数据。"

    # 其他本地缓冲赋值不在这里补专属说明。
    return ""

# 返回 lane-add 本地缓冲赋值的专属说明。
def specialized_assignment_lane_comment_text(
    str_symbol_name: str,
    str_right_text: str,
) -> str:
    """返回 lane-add 本地缓冲赋值的专属说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 lane-add 本地缓冲场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # lane add 的 A/B 本地缓冲要区分左右两路并显式点出尾块补零。
    dict_lane_inputs = {  # lane-add 缓冲名到输入来源标记的查表映射。
        "arr_lane_buf_a": "ptr_input_a[",  # A 路输入指针前缀。
        "arr_lane_buf_b": "ptr_input_b[",  # 右操作数窗口的指针前缀。
    }

    # 先把 lane-add 两路缓冲的长说明收拢成查表映射。
    dict_lane_comments = {  # lane-add 缓冲名到长说明的查表映射。
        "arr_lane_buf_a": "arr_lane_buf_a 在这里锁住当前 lane-add 子块的 A 路样本；超出 int_chunk 的尾部 lane 会直接补 0，避免左操作数越界读取。",  # A 路本地缓冲的固定长说明。
        "arr_lane_buf_b": "arr_lane_buf_b 在这里锁住当前 lane-add 子块的 B 路配对样本；尾块超界时会补 0，避免右操作数旧值误入逐 lane 合并。",  # B 路配对样本与尾块补零的长说明。
    }

    # 命中 lane-add 两路缓冲之一且右值来源匹配时直接返回对应说明。
    if str_symbol_name in dict_lane_inputs and dict_lane_inputs[str_symbol_name] in str_right_text:

        # 按 lane-add 缓冲名字取回左右两路的专属长说明。
        return dict_lane_comments[str_symbol_name]

    # 其他缓冲赋值不在这里补专属说明。
    return ""

# 为当前赋值补充更具体的 pattern 级说明，优先消除 block/vector/stencil/reduction 的模板化注释。
def specialized_assignment_comment_text(
    str_symbol_name: str,
    str_left_text: str,
    str_right_text: str,
) -> str:
    """为当前赋值补充更具体的 pattern 级说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_left_text: 当前赋值左值的净文本，dtype=str，unit=left-hand-side text。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中专属 pattern 时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 先把常见槽位语义取出来，供 partial/line buffer 这类重复结构复用。
    str_slot_role = indexed_slot_role_text(str_left_text, str_symbol_name)  # 当前赋值命中的槽位角色词

    # 先尝试标量寄存器这组 pattern 级说明。
    str_scalar_comment_text = specialized_assignment_scalar_comment_text(  # 先判断纯标量寄存器是否命中固定 pattern 长说明。
        str_symbol_name,  # 当前主标识符。
        str_right_text,  # 当前右值文本。
    )

    # 命中标量寄存器说明时直接返回。
    if str_scalar_comment_text:

        # 把标量寄存器说明交回调用方。
        return str_scalar_comment_text

    # 再尝试槽位敏感结构这组 pattern 级说明。
    str_slot_comment_text = specialized_assignment_slot_comment_text(  # 槽位型长说明候选
        str_symbol_name,  # 需要补长说明的主标识符。
        str_left_text,  # 命中槽位模式的左值片段。
        str_right_text,  # 触发槽位长说明判断的右值表达式。
        str_slot_role,  # partial 或 line buffer 的槽位角色词。
    )

    # 命中槽位敏感结构说明时直接返回。
    if str_slot_comment_text:

        # 把槽位敏感结构说明交回调用方。
        return str_slot_comment_text

    # 然后尝试本地缓冲这组 pattern 级说明。
    str_buffer_comment_text = specialized_assignment_buffer_comment_text(str_symbol_name, str_right_text)  # 然后判断本地输入缓冲是否需要点明承接的是哪一批事务样本。

    # 命中本地缓冲说明时直接返回。
    if str_buffer_comment_text:

        # 把本地缓冲说明交回调用方。
        return str_buffer_comment_text

    # 最后尝试 lane-add 缓冲这组 pattern 级说明。
    str_lane_comment_text = specialized_assignment_lane_comment_text(str_symbol_name, str_right_text)  # 最后判断 lane-add 缓冲是否需要区分两路来源和尾块补零。

    # 命中 lane-add 缓冲说明时直接返回。
    if str_lane_comment_text:

        # 把 lane-add 缓冲说明交回调用方。
        return str_lane_comment_text

    # 其他 pattern 级赋值在这里不强制补专属说明。
    return ""

# 返回 pattern 级 inline 尾注里最常见的标量/寄存器专属说明。
def specialized_assignment_inline_scalar_comment_text(
    str_symbol_name: str,
    str_right_text: str,
) -> str:
    """返回 pattern 级 inline 尾注里最常见的标量/寄存器专属说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中标量/寄存器专属尾注场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # block transform 的总样本量寄存器需要保留统一的扁平遍历语义。
    if str_symbol_name == "int_total" and "int_rows * int_cols" in str_right_text:

        # 把 block transform 总样本量的扁平边界尾注交回调用方。
        return "这里把当前块的总样本量压成统一的扁平遍历边界。"

    # 中间 FIFO 的本地取样需要明确它只服务递增阶段。
    if str_symbol_name == "uint_sample" and "stream_mid_stream.read()" in str_right_text:

        # 把中间 FIFO 取样并递增的尾注交回调用方。
        return "先从中间 FIFO 取回当前样本，再在本地完成递增。"

    # reorder stream 的本地取样需要明确它接下来会转入列向处理。
    if str_symbol_name == "uint_sample" and "stream_reorder_stream.read()" in str_right_text:

        # 把 reorder stream 取样后转入列向阶段的尾注交回调用方。
        return "先从 reorder stream 取回当前块样本，再交给列向阶段处理。"

    # fence ordering 的中间写回值需要明确它先合并再落地。
    if (
        str_symbol_name == "uint_ordered_writeback"
        and "ptr_input_a[" in str_right_text
        and "ptr_input_b[" in str_right_text
    ):

        # 把 fence writeback 中间值的压缩说明交回调用方。
        return "先把两路输入样本合成一个待写回值。"

    # 归约主累加器清零时要明确后续所有部分和都会回折到这里。
    if str_symbol_name == "uint_tree_accum" and str_right_text == "0":

        # 把归约主累加器持续回折部分和的尾注交回调用方。
        return "整轮归约结果会持续折叠回这个本地累加器。"

    # 其他标量/寄存器赋值不在这里补 inline 尾注。
    return ""

# 返回 partial 槽位和 line buffer 这类槽位敏感 inline 尾注。
def specialized_assignment_inline_slot_comment_text(
    str_symbol_name: str,
    str_left_text: str,
    str_right_text: str,
    str_slot_role: str,
) -> str:
    """返回 partial 槽位和 line buffer 这类槽位敏感 inline 尾注。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_left_text: 当前赋值左值的净文本，dtype=str，unit=left-hand-side text。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。
        str_slot_role: 当前赋值命中的槽位角色词，dtype=str，unit=slot role text。

    返回:
        命中槽位敏感 inline 尾注场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # reduction tree 的 partial 尾注也按固定寄存器优先查表。
    if str_symbol_name.startswith("uint_partial") and "ptr_input_values[" in str_right_text:

        # 先收拢四个固定 partial 寄存器的短尾注查表。
        dict_partial_inline_comments = {  # partial 寄存器名到短尾注的查表映射。
            "uint_partial0": "左半边首样本超出边界时会直接补 0。",  # 左半边首样本的短尾注。
            "uint_partial1": "左半边第二个样本会和首样本先折成左侧部分和。",  # 左半边第二个样本的短尾注。
            "uint_partial2": "右半边首样本会先参与右侧部分和的准备。",  # 右半边首样本的短尾注。
            "uint_partial3": "右半边尾样本超出边界时会直接补 0。",  # 右半边尾样本的短尾注。
        }

        # 命中固定 partial 寄存器时直接返回短尾注。
        if str_symbol_name in dict_partial_inline_comments:

            # 取回当前 partial 寄存器的固定短尾注。
            return dict_partial_inline_comments[str_symbol_name]

        # 其他 partial 名字回退到基于槽位角色词的短尾注。
        return f"当前 4-sample 子块的{str_slot_role}超出边界时会直接补 0。"

    # line buffer 的三个槽位使用查表生成更稳定的短尾注。
    if str_symbol_name == "arr_line_buf":

        # 先把 line buffer 三个槽位的短尾注收拢成查表映射。
        dict_line_buffer_inline_comments = {  # line buffer 槽位到短尾注的查表映射。
            "[0]": "左邻居槽位在边界处会回退到首个有效输入。",  # 左邻居槽位的短尾注。
            "[1]": "中心槽位始终锁住当前输出点对应的输入样本。",  # 中心槽位的短尾注。
            "[2]": "右邻居槽位在边界处会钳住最后一个有效输入。",  # 右邻居槽位的短尾注。
        }

        # 按当前左值命中的槽位标记取回 line buffer 的短尾注。
        return slot_lookup_text(str_left_text, dict_line_buffer_inline_comments)

    # 其他槽位敏感赋值不在这里补 inline 尾注。
    return ""

# 返回本地缓冲类 pattern 赋值的 inline 尾注。
def specialized_assignment_inline_buffer_comment_text(
    str_symbol_name: str,
    str_right_text: str,
) -> str:
    """返回本地缓冲类 pattern 赋值的 inline 尾注。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中本地缓冲 inline 尾注场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 先收拢三类本地输入缓冲的短尾注映射，避免主函数继续堆叠并行缓冲分支。
    dict_buffer_inline_comments = {  # 本地缓冲名到短尾注的查表映射。
        "arr_wide_buf": "reshape 块的 16 路输入样本先锁进这组本地槽位。",  # reshape 本地块的短尾注。
        "arr_local_buf": "partition 块的局部输入样本会先锁进这组缓冲。",  # partition 缩放路径的局部输入短尾注。
        "arr_block_buf": "当前 block 的局部样本会先锁进这 4 个槽位。",  # block 事务工作集的短尾注。
    }

    # 命中三类本地输入缓冲之一时直接返回对应短尾注。
    if str_symbol_name in dict_buffer_inline_comments and "ptr_input_values[" in str_right_text:

        # 按当前并行缓冲名字取回对应的事务尾注。
        return dict_buffer_inline_comments[str_symbol_name]

    # 其他本地缓冲赋值不在这里补 inline 尾注。
    return ""

# 返回 lane-add 本地缓冲赋值的 inline 尾注。
def specialized_assignment_inline_lane_comment_text(
    str_symbol_name: str,
    str_right_text: str,
) -> str:
    """返回 lane-add 本地缓冲赋值的 inline 尾注。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 lane-add 本地缓冲 inline 尾注场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 先收拢 lane-add 尾注要区分的 A/B 两路输入前缀，后面只根据来源匹配就能挑中正确短尾注。
    dict_lane_inline_inputs = {  # lane-add 两路尾注先靠输入前缀分流，避免 A/B 缓冲继续复用同一句短注。
        "arr_lane_buf_a": "ptr_input_a[",  # 左操作数窗口前缀。
        "arr_lane_buf_b": "ptr_input_b[",  # 右操作数窗口前缀。
    }

    # 按左右两路缓冲收拢各自的补零尾注。
    dict_lane_inline_comments = {  # lane-add 缓冲名到短尾注的查表映射。
        "arr_lane_buf_a": "A 路尾块超出的 lane 会直接补 0。",  # A 路缓冲的补零尾注。
        "arr_lane_buf_b": "B 路尾块超出的 lane 会补 0，避免右操作数旧值混进逐 lane 求和。",  # B 路缓冲在尾块超界时的补零尾注。
    }

    # 命中 lane-add 两路缓冲之一且输入来源匹配时返回对应短尾注。
    if str_symbol_name in dict_lane_inline_inputs and dict_lane_inline_inputs[str_symbol_name] in str_right_text:

        # 按左右两路缓冲名字取回对应的补零尾注。
        return dict_lane_inline_comments[str_symbol_name]

    # 其他缓冲赋值不在这里补 inline 尾注。
    return ""

# 为当前赋值补充和摘要不同的短尾注，避免 inline comment 继续复用模板化 buffer 文案。
def specialized_assignment_inline_comment_text(
    str_symbol_name: str,
    str_left_text: str,
    str_right_text: str,
) -> str:
    """为当前赋值补充 pattern 级短尾注。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_left_text: 当前赋值左值的净文本，dtype=str，unit=left-hand-side text。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中专属 pattern 时返回中文尾注，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 先取回数组索引或尾缀序号对应的角色词，供 line buffer / partial 复用。
    str_slot_role = indexed_slot_role_text(str_left_text, str_symbol_name)  # 当前尾注命中的槽位角色词

    # 先尝试标量寄存器这组 inline 尾注。
    str_scalar_inline_comment = specialized_assignment_inline_scalar_comment_text(str_symbol_name, str_right_text)  # 先判断纯标量寄存器是否需要补和摘要不同的短尾注。

    # 命中标量寄存器 inline 尾注时直接返回。
    if str_scalar_inline_comment:

        # 把标量寄存器 inline 尾注交回调用方。
        return str_scalar_inline_comment

    # 再尝试槽位敏感结构这组 inline 尾注。
    str_slot_inline_comment = specialized_assignment_inline_slot_comment_text(  # 槽位型尾注候选
        str_symbol_name,  # 需要补尾注的主标识符。
        str_left_text,  # 用来识别槽位尾注的左值片段。
        str_right_text,  # 触发槽位尾注判断的右值表达式。
        str_slot_role,  # 当前尾注对应的槽位角色词。
    )

    # 命中槽位敏感结构 inline 尾注时直接返回。
    if str_slot_inline_comment:

        # 把槽位敏感结构 inline 尾注交回调用方。
        return str_slot_inline_comment

    # 然后尝试本地缓冲这组 inline 尾注。
    str_buffer_inline_comment = specialized_assignment_inline_buffer_comment_text(str_symbol_name, str_right_text)  # 然后判断本地缓冲是否需要补输入块来源的短尾注。

    # 命中本地缓冲 inline 尾注时直接返回。
    if str_buffer_inline_comment:

        # 把本地缓冲 inline 尾注交回调用方。
        return str_buffer_inline_comment

    # 最后尝试 lane-add 缓冲这组 inline 尾注。
    str_lane_inline_comment = specialized_assignment_inline_lane_comment_text(str_symbol_name, str_right_text)  # 最后判断 lane-add 缓冲是否需要补两路来源和尾块补零尾注。

    # 命中 lane-add 缓冲 inline 尾注时直接返回。
    if str_lane_inline_comment:

        # 把 lane-add 缓冲 inline 尾注交回调用方。
        return str_lane_inline_comment

    # 其他 pattern 没有命中专属短尾注时返回空字符串。
    return ""

# 返回 fence/reduction/stencil 这组输出寄存器写回的专属说明。
def assignment_output_register_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """返回 fence/reduction/stencil 这类结果寄存器写回的长说明。

    参数:
        str_symbol_name: 当前输出窗口左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前输出窗口右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中结果寄存器写回场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # fence ordering 的输出写回要明确这里落盘的是已经排好顺序的本地结果。
    if str_right_text == "uint_ordered_writeback":

        # 交回 fence 顺序化结果写回输出窗口的长说明。
        return (
            f"{str_symbol_name} 在这里把已经完成本地排序合并的 uint_ordered_writeback 写回输出窗口，"
            "让外部观测边界直接看到 fence 之后的顺序化结果。"
        )

    # reduction tree 的最终写回要明确这里只落盘整个归约事务的累计结果。
    if str_right_text == "uint_tree_accum":

        # 交回 reduction tree 最终累计结果写回输出窗口的长说明。
        return (
            f"{str_symbol_name} 在这里把整轮 reduction tree 折叠得到的累计结果写回输出窗口，"
            "让 host 或 testbench 直接观察本次归约事务的最终和。"
        )

    # stencil 的写回要明确这里落盘的是三个邻域槽位的局部和。
    if all(str_term in str_right_text for str_term in ("arr_line_buf[0]", "arr_line_buf[1]", "arr_line_buf[2]")):

        # 交回 3-tap stencil 邻域求和写回输出窗口的长说明。
        return (
            f"{str_symbol_name} 在这里把 line buffer 的左邻居、中心样本和右邻居求和后写回输出窗口，"
            "显式保留 3-tap stencil 的局部邻域写回语义。"
        )

    # 其他结果寄存器写回不在这里补专属说明。
    return ""

# 返回本地缓冲和直通输出写回的长说明。
def assignment_output_buffer_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """返回本地缓冲和直通输出写回的长说明。

    参数:
        str_symbol_name: 当前输出窗口左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前输出窗口右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中本地缓冲或直通输出写回场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 向量缩放的写回要明确局部缓冲样本在这里乘上运行时缩放因子。
    if "arr_wide_buf[" in str_right_text and "uint_scale_factor" in str_right_text:

        # 交回 reshape 缓冲乘缩放因子后写回输出窗口的长说明。
        return (
            f"{str_symbol_name} 在这里把 arr_wide_buf 当前槽位的样本乘上运行时缩放因子后写回输出窗口，"
            "让 16-lane reshape 块的缩放结果按原索引顺序落盘。"
        )

    # partition 缩放的写回也要显式保留局部块边界。
    if "arr_local_buf[" in str_right_text and "uint_scale_factor" in str_right_text:

        # 交回 partition 局部块乘缩放因子后写回输出窗口的长说明。
        return (
            f"{str_symbol_name} 在这里把 arr_local_buf 当前槽位的样本乘上运行时缩放因子后写回输出窗口，"
            "显式保留 partition 块的局部缩放写回边界。"
        )

    # lane add 的写回要明确当前落盘的是 A/B 两路局部缓冲的逐 lane 求和结果。
    if "arr_lane_buf_a[" in str_right_text and "arr_lane_buf_b[" in str_right_text:

        # 交回 A/B 两路 lane 缓冲求和后写回输出窗口的长说明。
        return (
            f"{str_symbol_name} 在这里把 A/B 两路 lane 缓冲的当前槽位求和后写回输出窗口，"
            "让外部边界直接观察当前子块的逐 lane 合并结果。"
        )

    # 乘法分支
    if "uint_scale_factor" in str_right_text and "ptr_input" in str_right_text:

        # 乘法
        return f"{str_symbol_name} 输入乘因子写回输出"

    # 输入窗口到输出窗口的直通写回要保留最小 mock 数据路径。
    if "ptr_input" in str_right_text or "arr_input" in str_right_text:

        # 返回输入样本直写输出窗口的说明。
        return (
            f"{str_symbol_name} 在这里把输入窗口当前索引的样本递增后写回输出窗口，"
            "明确保留 mock workflow 的输入到输出映射路径。"
        )

    # 其他缓冲或直通写回不在这里补专属说明。
    return ""

# 返回 AXIS/FIFO/task/block 等输出写回的长说明。
def assignment_output_stream_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """返回 AXIS/FIFO/task/block 等输出写回的长说明。

    参数:
        str_symbol_name: 当前输出窗口左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前输出窗口右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 AXIS/FIFO/task/block 输出写回场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # FIR 专用输出规则先于通用 stream 规则执行，确保 staged DATAFLOW 结果不退回泛化句式。
    str_fir_comment_text = fir_assignment_comment_text(str_symbol_name, str_right_text)  # FIR 输出 FIFO 说明候选

    # 命中 FIR 结果 FIFO 写回时直接返回专属说明。
    if str_fir_comment_text:

        # 把 FIR 输出窗口的具体数据流边界交回调用方。
        return str_fir_comment_text

    # AXIS packet 拆包写回时要指出这里只取 data 域。
    if "axis_out_pkt.data" in str_right_text:

        # 返回 axis_word_t 拆包写回说明。
        return (
            f"{str_symbol_name} 在这里把 axis_out_pkt 的 data 域拆出来写回主存窗口，"
            "让 board wrapper 的外部缓冲看到编码后的 16-bit 结果。"
        )

    # dataflow 输出 FIFO 写回时要说明结果来自跨阶段 FIFO。
    if "stream_out_stream.read()" in str_right_text:

        # 返回 dataflow 输出 FIFO 到主存窗口的写回说明。
        return (
            f"{str_symbol_name} 在这里从 stream_out_stream 读取已经完成 tile 求和的结果，"
            "并顺序写回输出窗口，保持 dataflow 写回边界可观测。"
        )

    # 2D block transform 的列向结果写回要单独保留 stream_col_stream 的块语义。
    if "stream_col_stream.read()" in str_right_text:

        # 交回列向 stage 样本写回输出窗口的长说明。
        return (
            f"{str_symbol_name} 在这里从 stream_col_stream 取回已经完成列向处理的块样本，"
            "并按扁平索引顺序写回输出窗口，保持 block transform 的最终回写边界显式可见。"
        )

    # task_graph 的 result stream 写回要点明当前样本已经完成递增。
    if "stream_task_result_stream.read()" in str_right_text:

        # 返回 task_graph result stream 到输出窗口的写回说明。
        return (
            f"{str_symbol_name} 在这里从 stream_task_result_stream 取回已经递增的样本，"
            "并按原索引顺序写回输出窗口，保持 task_graph 写回边界可观测。"
        )

    # blocked matmul 的写回要指出这里落盘的是 A/B 对应 lane 的求和结果。
    if "arr_tile_a[" in str_right_text and "arr_tile_b[" in str_right_text:

        # 返回 blocked matmul 的逐 lane 求和写回说明。
        return (
            f"{str_symbol_name} 在这里把当前 tile 的 A/B 对应 lane 求和后写回输出窗口，"
            "让 host 或 testbench 直接观察 blocked 路径的写回结果。"
        )

    # 其他 stream/task/block 写回不在这里补专属说明。
    return ""

# 汇总输出窗口写回的长说明路由，优先命中专属子规则，再回退通用观测边界。
def assignment_output_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """按输出窗口写回语义生成赋值说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中输出窗口写回场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 非输出窗口写回不在这里解释，直接回退给后续子规则。
    if not str_symbol_name.startswith(("ptr_output", "arr_output")):

        # 当前左值不是输出窗口，交给其他赋值子规则继续判断。
        return ""

    # 输出窗口写回依次匹配结果寄存器、本地缓冲/直通和 stream/task/block 路径。
    for str_comment_text in (
        assignment_output_register_comment_text(str_symbol_name, str_right_text),
        assignment_output_buffer_comment_text(str_symbol_name, str_right_text),
        assignment_output_stream_comment_text(str_symbol_name, str_right_text),
    ):

        # 只要命中任何一条输出写回说明，就立即返回。
        if str_comment_text:

            # 把首条命中的输出写回说明交回调用方。
            return str_comment_text

    # 其他输出窗口写回统一回退到观测边界说明。
    return (
        f"{str_symbol_name} 在这里写回当前输出窗口，"
        "明确下游 host 或 testbench 会从这个端口读取本轮写回样本。"
    )

# 按局部状态与右值来源生成赋值说明，收拢 tile 缓冲、packet 与块长寄存器规则。
def assignment_local_state_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """按局部状态与右值来源生成赋值说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中局部状态写入场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐条匹配局部状态与右值来源的组合，避免在主规则函数里重复堆叠分支。
    for str_expected_name, tuple_right_needles, str_comment_text in (
        (
            "axis_out_pkt",
            ("stream_out_stream.read()",),
            "axis_out_pkt 在这里从 stream_out_stream 取回已经编码完成的 16-bit 输出 token，随后会拆出 data 域写回主存窗口。",
        ),
        (
            "arr_tile_a",
            ("stream_a_stream.read()",),
            "arr_tile_a 在这里从 stream_a_stream 取回当前 tile 的 A 路 token；尾块超出的 lane 直接补零，避免左操作数从 FIFO 过读。",
        ),
        (
            "arr_tile_b",
            ("stream_b_stream.read()",),
            "arr_tile_b 在这里从 stream_b_stream 取回当前 tile 的 B 路 token；尾块超出的 lane 直接补零，避免右操作数把无效值带进求和。",
        ),
        (
            "int_chunk",
            ("int_length - base",),
            "int_chunk 在这里计算当前局部块仍然有效的样本数，避免最后一个尾块越过输入长度继续读外部窗口。",
        ),
        (
            "arr_tile_a",
            ("ptr_input_a[",),
            "arr_tile_a 在这里把 input_a 的当前 tile lane 搬进本地 A 缓冲，让后续写回阶段逐 lane 复用左操作数样本。",
        ),
        (
            "arr_tile_b",
            ("ptr_input_b[",),
            "arr_tile_b 在这里把 input_b 的当前 tile lane 搬进本地 B 缓冲，给随后配对求和的右操作数路径提供样本。",
        ),
    ):

        # 命中专属左值角色与右值来源组合后，直接返回对应说明。
        if str_symbol_name == str_expected_name and all(
            str_right_needle in str_right_text for str_right_needle in tuple_right_needles
        ):

            # 返回当前局部写入的专属说明。
            return str_comment_text

    # 当前局部状态不属于这一组规则时回退为空字符串。
    return ""

# 返回赋值说明里复用的 AXIS 字段规则表，避免 data/keep/strb/last 规则堆在单个函数里。
def assignment_axis_field_rules() -> tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]:
    """返回赋值说明里复用的 AXIS 字段规则表。

    参数:
        无显式业务参数；当前规则表只依赖模块内硬编码的 AXIS 字段语义。

    返回:
        data/keep/strb/last 四类字段的匹配规则表，dtype=tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]，
        unit=assignment comment rules。
    """

    # 把载荷类 sideband 规则和帧尾边界规则拼成完整的 AXIS 字段规则表。
    return assignment_axis_payload_field_rules() + assignment_axis_last_field_rules()

# 返回 AXIS 字段里 data/keep/strb 这组载荷相关规则。
def assignment_axis_payload_field_rules() -> tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]:
    """返回 AXIS 字段里 data/keep/strb 这组载荷相关规则。

    参数:
        无显式业务参数；当前规则表只依赖 AXIS 载荷字段和有效字节 sideband 语义。

    返回:
        AXIS 载荷与有效字节规则表，dtype=tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]，
        unit=assignment comment rules。
    """

    # 统一返回 AXIS 的载荷字段和有效字节 sideband 规则。
    return (
        (
            "data",
            ("axis_in_pkt.data",),
            ("ptr_input_values[",),
            "data 在这里把主存窗口读出的当前样本截成单字节载荷，写入 axis_in_pkt 的 data 域供后续 AXIS 编码阶段消费。",
        ),
        (
            "data",
            tuple(),
            ("axis_in_pkt.data + 1",),
            "data 在这里把输入 token 的载荷递增后写入输出 packet 的 data 域，作为本轮编码后的 16-bit 样本值。",
        ),
        (
            "keep",
            ("axis_in_pkt.keep",),
            ("-1",),
            "keep 在这里把单字节输入 token 的唯一有效字节标成 1，供后续 AXIS 编码阶段按标准侧带读取。",
        ),
        (
            "keep",
            ("axis_out_pkt.keep",),
            ("-1",),
            "keep 在这里把 16-bit 输出 packet 的双字节有效掩码写成全有效，告诉下游两个 byte 都属于编码结果。",
        ),
        (
            "keep",
            tuple(),
            ("-1",),
            "keep 在这里把输出 packet 的字节有效掩码全部拉高，表示 16-bit 结果的两个字节都有效。",
        ),
        (
            "strb",
            ("axis_in_pkt.strb",),
            ("-1",),
            "strb 在这里把单字节输入 token 的写 strobe 拉高，和 keep 一起说明这一字节是真实输入载荷。",
        ),
        (
            "strb",
            ("axis_out_pkt.strb",),
            ("-1",),
            "strb 在这里把 16-bit 输出 packet 的双字节写 strobe 全部打开，让写回侧把两个输出字节都当作真实编码值。",
        ),
        (
            "strb",
            tuple(),
            ("-1",),
            "strb 在这里把输出 packet 的写 strobe 全部拉高，让下游把两个输出字节都视作真实载荷。",
        ),
    )

# 返回 AXIS 字段里 `last` 帧尾边界相关规则。
def assignment_axis_last_field_rules() -> tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]:
    """返回 AXIS 字段里 `last` 帧尾边界相关规则。

    参数:
        无显式业务参数；当前规则表只依赖 AXIS 帧尾边界的透传与显式拉高语义。

    返回:
        AXIS 帧尾边界规则表，dtype=tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]，
        unit=assignment comment rules。
    """

    # 统一返回输入帧尾、透传帧尾和输出帧尾这三条边界规则。
    return (
        (
            "last",
            ("axis_in_pkt.last",),
            ("i == int_length - 1",),
            "last 在这里仅在最后一个主存样本对应的输入 token 上拉高，给 stream_in_stream 明确标出本次事务的帧尾。",
        ),
        (
            "last",
            ("axis_out_pkt.last",),
            ("axis_in_pkt.last",),
            "last 在这里把输入 token 的帧尾标记原样透传到 16-bit 输出 packet，保证后续写回阶段保留同一条事务边界。",
        ),
        (
            "last",
            tuple(),
            ("i == int_length - 1",),
            "last 在这里仅在最后一个输出 token 上拉高，给下游显式标出当前事务的帧尾边界。",
        ),
    )

# 按 AXIS 字段角色生成赋值说明，集中处理 data/keep/strb/last 的 sideband 语义。
def assignment_axis_field_comment_text(str_symbol_name: str, str_left_text: str, str_right_text: str) -> str:
    """按 AXIS 字段角色生成赋值说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_left_text: 当前赋值左值的净文本，dtype=str，unit=left-hand-side text。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 AXIS 字段写入场景时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐条扫描 AXIS sideband 规则表，匹配当前赋值的字段名、成员链和右值来源。
    for str_expected_name, tuple_left_needles, tuple_right_needles, str_comment_text in assignment_axis_field_rules():

        # 只有字段名、左值成员链和右值片段同时对齐时，才采用这一条 sideband 说明。
        if (
            str_symbol_name == str_expected_name
            and all(str_left_needle in str_left_text for str_left_needle in tuple_left_needles)
            and all(str_right_needle in str_right_text for str_right_needle in tuple_right_needles)
        ):

            # 当前赋值已经命中一条 AXIS sideband 规则，直接返回对应说明。
            return str_comment_text

    # 当前赋值不属于 AXIS sideband 字段写入时回退为空字符串。
    return ""

# 按标量、stream 来源和局部状态生成赋值说明，避免主赋值函数继续膨胀。
def assignment_stream_or_state_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """按标量、stream 来源和局部状态生成赋值说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中局部状态或 stream 来源规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # axis 输入流直读到局部寄存器时，要说明当前样本直接来自外部流接口。
    if "stream_in_stream.read()" in str_right_text:

        # 当前右值来自 axis 输入流时，直接返回局部锁存说明。
        return f"{str_symbol_name} 在这里锁存 axis 输入流弹出的当前样本，作为本轮递增计算的唯一载荷。"

    # load FIFO 直读到局部寄存器时，要说明样本已经进入中间计算阶段。
    if "stream_load_stream.read()" in str_right_text:

        # 当前右值来自 load FIFO 时，直接返回样本读取说明。
        return f"{str_symbol_name} 在这里从 load FIFO 取出当前样本，交给中间计算阶段完成递增处理。"

    # count stream 读取要强调它只负责回传本轮事务长度 token。
    if any(
        str_count_read in str_right_text
        for str_count_read in (
            "stream_count_stream.read()",
            "stream_task_count_stream.read()",
            "read_count_stream.read()",
            "compute_count_stream.read()",
            "write_count_stream.read()",
        )
    ):

        # 当前右值来自 count stream 时，直接返回事务边界锁定说明。
        return f"{str_symbol_name} 在这里从 count stream 取回本轮事务长度，给当前 task_graph actor 锁定后续循环要消费的有效 token 数。"

    # task stream 读取到局部样本时，要说明它正进入递增计算路径。
    if "stream_task_stream.read()" in str_right_text:

        # 当前右值来自 task stream 时，直接返回“把待递增样本拉回本地”的专属说明。
        return f"{str_symbol_name} 在这里从 stream_task_stream 取出当前待递增样本，交给 task_graph 计算路径继续完成本轮处理。"

    # task result stream 读取到局部寄存器时，要说明这个样本已经完成递增。
    if "stream_task_result_stream.read()" in str_right_text:

        # 当前右值来自 result stream 时，直接返回输出样本回放说明。
        return f"{str_symbol_name} 在这里从 stream_task_result_stream 取回已经递增完成的样本，准备继续写回或转发到输出边界。"

    # stream 或 AXIS token 的局部节点要强调当前事务正在交付一个可继续传递的载荷。
    if str_symbol_name.startswith(("stream_", "axis_")):

        # 当前左值本身就是流式节点时，直接返回载荷装载说明。
        return f"{str_symbol_name} 在这里装载当前事务的输出 token，让后续 stage 读取到这次计算生成的载荷。"

    # 常见累加器、和寄存器或结果寄存器要强调局部运算状态的刷新动作。
    if any(str_keyword in str_symbol_name for str_keyword in ("acc", "sum", "result", "product")):

        # 当前左值属于局部运算状态时，直接返回状态刷新说明。
        return (
            f"{str_symbol_name} 在这里刷新当前事务的局部运算状态，"
            "保证后续写回阶段读取到最新中间结果。"
        )

    # 其他局部节点不在这里强制追加说明。
    return ""

# 为简单赋值语句生成更具体的写入语义说明。
def assignment_comment_text(str_assignment_code: str) -> str:
    """为简单赋值语句生成局部写入说明。

    参数:
        str_assignment_code: 当前赋值语句的净代码文本，dtype=str，unit=code text。

    返回:
        当前赋值语句对应的中文写入说明，dtype=str，unit=comment text。
    """

    # 先拆出左值文本，后续所有赋值角色判断都依赖这一段净文本。
    str_left_text = str_assignment_code.split("=", 1)[0].strip()  # 当前赋值语句的左值净文本

    # 再拆出右值表达式，供数据来源和写回方向判断复用。
    str_right_text = str_assignment_code.split("=", 1)[1].rsplit(";", 1)[0].strip()  # 当前赋值语句的右值净文本

    # 最后抽取左值里的主标识符，避免成员链和索引遮蔽真正角色名。
    str_symbol_name = assigned_symbol_name(str_left_text)  # 当前赋值左值对应的主标识符

    # twiddle 常量表要明确它承担的是固定旋转系数边界。
    if "twiddle" in str_symbol_name:

        # 当前左值是 twiddle 常量表时，直接返回固定系数说明。
        return (
            f"{str_symbol_name} 在这里固定声明 FFT 计算复用的 twiddle 常量表，"
            "让后续索引访问共享同一组定点旋转系数。"
        )

    # 其他局部数组字面量要说明它们是当前 pattern 复用的固定查找表。
    if str_symbol_name.startswith("arr_") and "{" in str_right_text:

        # 当前赋值在初始化局部查找表时，直接返回常量样本说明。
        return (
            f"{str_symbol_name} 在这里固定声明当前 pattern 复用的局部查找表，"
            "让后续索引访问共享同一组常量样本。"
        )

    # 先尝试 pattern 级专属赋值说明，避免已知失败簇继续退回泛化模板。
    str_fir_comment_text = fir_assignment_comment_text(str_symbol_name, str_right_text)  # FIR 局部操作数说明候选

    # 命中 FIR 本地操作数或结果写回时，直接返回阶段专属说明。
    if str_fir_comment_text:

        # 把 FIR 赋值语义交回 source 注释重写入口。
        return str_fir_comment_text

    # 继续尝试 pattern 级专属赋值说明，避免已知失败簇退回泛化模板。
    str_specialized_comment_text = specialized_assignment_comment_text(  # 当前赋值命中的 pattern 级长说明
        str_symbol_name,  # 当前这次长说明要绑定的主标识符
        str_left_text,  # 当前这次长说明要查看的左值净文本
        str_right_text,  # 当前这次长说明要查看的右值表达式净文本
    )

    # 命中 pattern 级专属赋值说明时，直接返回。
    if str_specialized_comment_text:

        # 返回当前赋值的 pattern 级说明。
        return str_specialized_comment_text

    # 依次尝试输出窗口、局部状态、AXIS 字段和流来源这四组专属赋值说明。
    for str_comment_text in (
        assignment_output_comment_text(str_symbol_name, str_right_text),
        assignment_local_state_comment_text(str_symbol_name, str_right_text),
        assignment_axis_field_comment_text(str_symbol_name, str_left_text, str_right_text),
        assignment_stream_or_state_comment_text(str_symbol_name, str_right_text),
    ):

        # 当前赋值一旦命中某一组专属说明，就不再继续回退到更泛化的节点说明。
        if str_comment_text:

            # 返回首个命中的赋值语义说明。
            return str_comment_text

    # 其余赋值统一回退到“局部数据通路节点写入”这一保守说明。
    return f"{str_symbol_name} 在这里接住当前事务的局部计算结果，明确这次写入落在哪个数据通路节点。"

# 按局部 tile 状态和 packet 来源生成尾注说明，避免主尾注函数承载细粒度局部规则。
def assignment_inline_local_state_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """按局部 tile 状态和 packet 来源生成尾注说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中局部状态尾注规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐条扫描局部 tile 状态和 packet 来源规则，把短尾注绑定到当前左值角色。
    for str_expected_name, tuple_right_needles, str_comment_text in (
        (
            "arr_tile_a",
            ("stream_a_stream.read()",),
            "只有当前 lane 仍在有效长度内时，才从 A 路 stream 取 token；否则直接补零。",
        ),
        (
            "arr_tile_b",
            ("stream_b_stream.read()",),
            "右侧配对槽位只有在本轮仍落入有效长度时才消费 token；尾块外的位置直接写成 0。",
        ),
        ("int_chunk", ("int_length - base",), "只保留当前局部块里真正有效的样本数。"),
        (
            "arr_tile_a",
            ("ptr_input_a[",),
            "超出有效长度的 A lane 直接补零，避免左操作数越界读窗口。",
        ),
        (
            "arr_tile_b",
            ("ptr_input_b[",),
            "超出有效长度的 B lane 直接补零，避免右操作数把尾块外旧值带进求和。",
        ),
        (
            "axis_out_pkt",
            ("stream_out_stream.read()",),
            "先把完整的 16-bit 输出 packet 从 stream_out_stream 取回本地。",
        ),
    ):

        # 只有左值角色和右值来源同时命中这条局部状态规则时，才采用对应尾注。
        if str_symbol_name == str_expected_name and all(
            str_right_needle in str_right_text for str_right_needle in tuple_right_needles
        ):

            # 当前局部状态已经命中一条专属尾注规则，直接返回对应说明。
            return str_comment_text

    # 其他局部状态写入不在这里强制追加尾注。
    return ""

# 按 AXIS 字段角色生成尾注说明，补足 data/keep/strb/last 的短语义提示。
def assignment_inline_axis_field_comment_text(
    str_symbol_name: str,
    str_left_text: str,
    str_right_text: str,
) -> str:
    """按 AXIS 字段角色生成尾注说明。

    参数:
        str_symbol_name: 当前赋值左值对应的主标识符，dtype=str，unit=identifier。
        str_left_text: 当前赋值左值的净文本，dtype=str，unit=left-hand-side text。
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 AXIS 字段尾注规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 逐条扫描 AXIS 字段尾注规则，把 data/keep/strb/last 的短语义绑定到当前赋值。
    for str_expected_name, tuple_left_needles, tuple_right_needles, str_comment_text in (
        ("data", ("axis_in_pkt.data",), ("ptr_input_values[",), "主存读出的样本先封装进输入 packet 的 data 域。"),
        ("data", tuple(), ("axis_in_pkt.data + 1",), "输出 packet 的 data 域携带递增后的样本值。"),
        ("keep", ("axis_in_pkt.keep",), ("-1",), "单字节输入 token 的 keep 位固定为有效。"),
        ("keep", ("axis_out_pkt.keep",), ("-1",), "16-bit 输出 token 的两个字节都在 keep 里标成有效。"),
        ("keep", tuple(), ("-1",), "两个输出字节都标记为有效。"),
        ("strb", ("axis_in_pkt.strb",), ("-1",), "这里单独把单字节输入 token 的写 strobe 使能打开。"),
        ("strb", ("axis_out_pkt.strb",), ("-1",), "双字节输出 strobe 全开，并与 16-bit keep 掩码保持一致。"),
        ("strb", tuple(), ("-1",), "写 strobe 全部打开，并与 keep 保持一致。"),
        ("last", ("axis_in_pkt.last",), ("i == int_length - 1",), "只有最后一个输入 token 才带这个帧尾标记。"),
        ("last", ("axis_out_pkt.last",), ("axis_in_pkt.last",), "输入 packet 的帧尾位会在这里原样透传。"),
        ("last", tuple(), ("i == int_length - 1",), "只有最后一个 token 才带帧尾标记。"),
    ):

        # 只有字段名、左值成员链和右值来源同时对齐时，才采用这一条 AXIS 尾注。
        if (
            str_symbol_name == str_expected_name
            and all(str_left_needle in str_left_text for str_left_needle in tuple_left_needles)
            and all(str_right_needle in str_right_text for str_right_needle in tuple_right_needles)
        ):

            # 当前 AXIS 字段写入已经命中一条专属尾注规则，直接返回对应说明。
            return str_comment_text

    # 其他 AXIS 字段写入不在这里强制追加短尾注。
    return ""

# 为简单赋值或带初始化的局部声明生成语义化尾注，避免和上方摘要注释重复。
def assignment_inline_comment_text(str_assignment_code: str) -> str:
    """为简单赋值或带初始化的局部声明生成语义化尾注。

    参数:
        str_assignment_code: 当前赋值语句或带初始化声明的净代码文本，dtype=str，unit=code text。

    返回:
        当前语句对应的中文尾注说明，dtype=str，unit=comment text。
    """

    # 先拆出左值文本，供尾注分支识别字段写回还是普通局部寄存器。
    str_left_text = str_assignment_code.split("=", 1)[0].strip()  # 当前尾注规则使用的左值净文本

    # 再拆出右值表达式，专门拿来判断样本来源、窗口方向和 AXIS sideband 角色。
    str_right_text = str_assignment_code.split("=", 1)[1].rsplit(";", 1)[0].strip()  # 当前尾注分派使用的右值片段

    # 最后抽取左值里的主标识符，避免成员链遮蔽真正的角色名。
    str_symbol_name = assigned_symbol_name(str_left_text)  # 当前尾注规则命中的主标识符

    # twiddle 常量表尾注要强调它是固定系数，而不是运行时重新计算的值。
    if "twiddle" in str_symbol_name:

        # 当前尾注命中 twiddle 常量表时，直接返回固定系数说明。
        return "本地 twiddle 常量表固定复用 4 个定点系数。"

    # 其他局部数组字面量尾注要强调表项会在整个函数体内保持固定初始化。
    if str_symbol_name.startswith("arr_") and "{" in str_right_text:

        # 当前尾注命中局部数组常量表时，直接返回固定初始化说明。
        return "局部表项在函数体内保持固定初始化。"

    # 先尝试 pattern 级尾注，避免已知失败簇继续退回到模板化短尾注。
    str_specialized_comment_text = specialized_assignment_inline_comment_text(  # 当前赋值命中的 pattern 级短尾注
        str_symbol_name,  # 当前尾注要绑定的主标识符
        str_left_text,  # 当前尾注要判断的左值净文本
        str_right_text,  # 当前尾注要判断的右值表达式净文本
    )

    # 命中 pattern 级尾注时直接返回。
    if str_specialized_comment_text:

        # 返回当前赋值的专属短尾注。
        return str_specialized_comment_text

    # 依次尝试输出窗口、局部 tile、AXIS 字段和 stream 来源这四组短尾注说明。
    for str_comment_text in (
        assignment_inline_output_comment_text(str_symbol_name, str_right_text),
        assignment_inline_local_state_comment_text(str_symbol_name, str_right_text),
        assignment_inline_axis_field_comment_text(str_symbol_name, str_left_text, str_right_text),
        assignment_inline_stream_source_comment_text(str_right_text),
    ):

        # 当前尾注一旦已经命中专属规则，就不再回退到更泛化的尾注模板。
        if str_comment_text:

            # 返回首个命中的赋值尾注说明。
            return str_comment_text

    # 其他赋值不在这里强制追加尾注。
    return ""
