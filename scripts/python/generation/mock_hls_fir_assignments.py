"""提供 staged FIR DATAFLOW 赋值和尾注规则。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
# FIR 赋值函数保持独立路由，供摘要与尾注分别调用。
from __future__ import annotations

# 生成 FIR 赋值摘要和结果写回语义。
def fir_assignment_comment_text(str_symbol_name: str, str_right_text: str) -> str:
    """生成 FIR 输出 FIFO 写回的专属长说明。

    参数:
        str_symbol_name: 当前输出窗口左值对应的主标识符，dtype=str，unit=identifier。
        str_right_text: 当前输出窗口右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 FIR 结果 FIFO 写回时返回中文说明，否则返回空字符串，dtype=str，unit=comment text。
    """

    # FIR 计算阶段的本地操作数要绑定到输入 FIFO 和 +1 映射，避免回退到通用赋值模板。
    if str_symbol_name == "uint_fir_sample" and "stream_mid_stream.read()" in str_right_text:

        # 返回 FIR 计算阶段本地操作数的具体数据路径说明。
        return (
            "uint_fir_sample 在这里从 stream_mid_stream 读取本轮 FIR 输入 token，"
            "作为计算阶段的本地操作数并参与 +1 映射。"
        )

    # FIR 写回只允许由显式输出窗口和结果 FIFO 同时确认，避免误伤其他 stream 场景。
    if (
        str_symbol_name.startswith(("ptr_output", "arr_output"))
        and "stream_result_stream.read()" in str_right_text
    ):

        # 返回 FIR 结果 FIFO 到输出窗口的事务边界说明。
        return (
            f"{str_symbol_name} 在这里从 stream_result_stream 取回 FIR 计算阶段的递增结果，"
            "并按原索引顺序写回输出窗口，保持 FIR DATAFLOW 的结果边界可观测。"
        )

    # 其他赋值不在 FIR 输出规则中解释。
    return ""

# 生成 FIR 输入 FIFO 的局部样本尾注。
def fir_assignment_inline_comment_text(str_right_text: str) -> str:
    """生成 FIR 输入 FIFO 读取的专属尾注。

    参数:
        str_right_text: 当前赋值右值表达式的净文本，dtype=str，unit=expression text。

    返回:
        命中 FIR 输入 FIFO 读取时返回中文尾注，否则返回空字符串，dtype=str，unit=comment text。
    """

    # 使用完整的治理后通道名，防止短名称子串误判其他 stream。
    if "stream_mid_stream.read()" in str_right_text:

        # 返回 FIR 计算阶段局部样本的来源说明。
        return "从 FIR 输入 FIFO 读取待递增样本。"

    # 其他局部样本来源不在 FIR 专用尾注中解释。
    return ""
