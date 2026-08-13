"""提供赋值规则中的通用 stream 来源尾注。"""

# 延迟类型注解，避免运行时提前解析本模块的字符串规则。
from __future__ import annotations

# FIR 输入 FIFO 尾注由专用模块提供，保持 staged DATAFLOW 的语义边界独立。
from .mock_hls_fir_assignments import fir_assignment_inline_comment_text

# 生成通用 stream 来源尾注。
def assignment_inline_stream_source_comment_text(str_right_text: str) -> str:
    """按 stream 样本来源生成尾注说明。

    参数:
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 stream 来源尾注规则时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # FIR 专用输入 FIFO 规则先执行，避免短名称或通用 stream 规则掩盖阶段语义。
    str_fir_comment_text = fir_assignment_inline_comment_text(str_right_text)  # FIR 输入 FIFO 尾注候选

    # 命中 FIR 输入 FIFO 读取时直接返回专属尾注。
    if str_fir_comment_text:

        # 把 FIR 局部样本来源交回尾注聚合函数。
        return str_fir_comment_text

    # axis 输入流直读时，用尾注强调当前局部寄存器拿到的是外部输入样本。
    if "stream_in_stream.read()" in str_right_text:

        # 当前右值来自 axis 输入流时，直接返回输入样本尾注。
        return "从输入 axis 流读取当前样本。"

    # load FIFO 读取时，用尾注说明当前样本正进入递增路径。
    if "stream_load_stream.read()" in str_right_text:

        # 当前右值来自 load FIFO 时，直接返回待递增样本尾注。
        return "从 load FIFO 读取待递增样本。"

    # count stream 读取时，要强调这个值只承担事务长度边界角色。
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

        # 当前右值来自 count stream 时，直接返回事务长度尾注。
        return "先从 count stream 取回这次事务长度。"

    # task stream 读取时，要强调当前拿到的是待递增样本。
    if "stream_task_stream.read()" in str_right_text:

        # 当前右值来自 task stream 时，直接返回样本领取尾注。
        return "从 task stream 领取当前待递增样本。"

    # task result stream 读取时，要强调当前样本已经完成递增。
    if "stream_task_result_stream.read()" in str_right_text:

        # 当前右值来自 task result stream 时，直接返回结果回放尾注。
        return "从 task result stream 取回已经递增完成的样本。"

    # 其他 stream 来源不在这里强制追加尾注。
    return ""
