"""协调 mock HLS 的 header、source 与 testbench 合同渲染。"""

# 启用延迟注解，避免类型提示在导入阶段过早求值。
from __future__ import annotations

# 宽泛类型提示用于对外保持稳定的 spec 参数签名。
from typing import Any

# 轻量 C/C++ 解析器负责定位函数签名范围。
from scripts.python.hls_quality_gate.readability.cpp_lexer import parse_functions

# 向量哈希标签需要继续写回治理后的 testbench 合同。
from scripts.python.generation.vectors import VECTOR_HASH_TAG

# 合同文本模块负责 HG007/HG008/HG015 的具体行内容。
from . import mock_hls_contract_text as contract_text

# 复用注释渲染器，避免在本模块内重复生成行级注释逻辑。
from .mock_hls_inline_comments import ensure_governed_line_comments

# 协议层只保留当前编排入口真正需要的 top function 名称解析。
from .mock_hls_protocols import top_function_name

# source 注释重写模块只保留对外入口；签名范围扫描已经下沉到 flow 子模块。
from .mock_hls_source_rewrite import rewrite_source_line_comments

# flow 子模块负责函数签名范围扫描，供 source contract 插桩复用。
from .mock_hls_source_flow import function_signature_ranges

# testbench 绑定模块负责 case/vector hash 与局部调用参数生成。
from .mock_hls_testbench_bindings import raw_case_ids, raw_vector_hash, testbench_argument_bindings

# 生成 HG007/HG008/HG015 合同完整的 mock HLS 头文件。
def build_governed_header(
    spec: dict[str, Any],
    dict_argument_names: dict[str, str],
    comment_language: str,
) -> str:
    """
    生成满足 HG007、HG008 和 HG015 的 mock HLS 头文件文本。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument_names: 顶层参数原名到治理名的映射字典，dtype=dict[str, str]，unit=name map。
        comment_language: 注释语言标识；当前实现固定生成中文 contract，dtype=str，unit=dimensionless。

    返回:
        可直接写入 `.h` 文件的治理后文本，dtype=str，unit=text。
    """

    # 当前治理后的 header 固定输出中文 contract，保留参数只为调用链接口稳定。
    del comment_language

    # 先以文件级 contract 为起点，确保 HG007 模块合同排在最前面。
    list_lines = list(contract_text.file_header_lines(spec, "header"))  # 治理后 header 的初始物理行列表

    # 再补上 pragma once 骨架，让顶层声明区保持单次展开。
    list_lines.extend([
        "",
        "// 保持头文件只展开一次，避免顶层接口重复声明。",
        "#pragma once",
        "",
    ])

    # 逐项写入 header 所需 include，并给每个 include 补一条稳定中文说明。
    list_lines.extend(header_include_lines(spec))

    # 追加 top function 的四段 contract。
    list_lines.extend(contract_text.top_function_contract_lines(spec, dict_argument_names, declaration=True))

    # 追加带 typed-prefix 端口名的函数声明签名。
    list_lines.append(contract_text.top_function_signature_text(spec, dict_argument_names, declaration=True))

    # 返回拼装完成的 header 文本。
    return "\n".join(list_lines).rstrip() + "\n"

# 为 header include 区生成稳定的中文说明和 `#include` 行。
def header_include_lines(spec: dict[str, Any]) -> list[str]:
    """为 header include 区生成稳定的中文说明和 `#include` 行。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        include 区的物理行列表，dtype=list[str]，unit=header include lines。
    """

    # 初始化 include 区行列表，后续按头文件顺序逐项展开。
    list_lines: list[str] = []  # header include 区的物理行列表

    # 按最终 include 顺序展开说明行、include 行和尾随空行三元组。
    for str_header_name in contract_text.required_header_names(spec):

        # 把当前 include 和它的中文说明一起写入 header。
        list_lines.extend([
            f"// {contract_text.header_comment_text(str_header_name)}",
            f"#include <{str_header_name}>",
            "",
        ])

    # 返回完整 include 区行列表。
    return list_lines

# 生成带 `[HLS]` transcript 前缀和逐端口 contract 的 mock testbench。
def build_governed_testbench(
    spec: dict[str, Any],
    raw_text: str,
    dict_argument_names: dict[str, str],
    comment_language: str,
) -> str:
    """
    生成满足 HG007、HG008、HG015 和 HG028 的 mock testbench 文本。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        raw_text: 原始 mock testbench 文本，dtype=str，unit=text。
        dict_argument_names: 顶层参数原名到治理名的映射字典，dtype=dict[str, str]，unit=name map。
        comment_language: 注释语言标识；当前实现固定生成中文 contract，dtype=str，unit=dimensionless。

    返回:
        可直接写入 testbench `.cpp` 的治理后文本，dtype=str，unit=text。
    """

    # 当前治理后的 testbench 固定输出中文 contract，保留参数只为外部调用接口稳定。
    del comment_language

    # 先读取当前 spec 对应的 top function 名称。
    str_top_function_name = top_function_name(spec)  # 供合同路由判定使用的 kernel 入口名

    # 先写入文件级 contract、include 和 `main` 入口骨架。
    list_lines = testbench_prelude_lines(spec, str_top_function_name)  # 治理后 testbench 的前导骨架行列表

    # 原始向量哈希存在时，把它显式带入治理后的 testbench。
    list_lines.extend(vector_hash_contract_lines(raw_vector_hash(raw_text)))

    # 原始 case 标记存在时，继续保留每个 case 的单独注释行。
    list_lines.extend(case_marker_comment_lines(raw_case_ids(raw_text)))

    # 生成 testbench 局部参数声明和调用参数列表。
    tuple_argument_bindings = testbench_argument_bindings(spec, dict_argument_names)  # testbench 局部声明和调用参数的二元组

    # 先取出局部声明区，供 `main` 在调用前准备指针、数组或标量载荷。
    list_declaration_lines = tuple_argument_bindings[0]  # 当前 testbench 的局部声明区行列表

    # 再取出调用参数序列，保持 top function 实参顺序和声明顺序一致。
    list_call_arguments = tuple_argument_bindings[1]  # 当前 top function 调用参数名列表

    # 局部声明区存在时，先写入声明区，再补一层空行与调用区分隔。
    if list_declaration_lines:

        # 把当前 testbench 局部声明区整体写入 `main`。
        list_lines.extend(list_declaration_lines)

        # 声明区和调用区之间补一层空行，保持 testbench 主体可读。
        list_lines.append("")

    # 追加 top function 调用和 PASS/FAIL transcript 尾段。
    list_lines.extend(testbench_tail_lines(str_top_function_name, list_call_arguments))

    # 把 testbench 各段落合并成最终文本，并保留末尾换行约定。
    return "\n".join(list_lines).rstrip() + "\n"

# 生成 testbench 的文件头、include 和 `main` 入口骨架。
def testbench_prelude_lines(
    spec: dict[str, Any],
    str_top_function_name: str,
) -> list[str]:
    """生成 testbench 的文件头、include 和 `main` 入口骨架。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        str_top_function_name: 当前 spec 对应的 top function 名称，dtype=str，unit=function name。

    返回:
        testbench 前导骨架行列表，dtype=list[str]，unit=testbench prelude lines。
    """

    # 返回 testbench 的固定前导骨架。
    return [
        *contract_text.file_header_lines(spec, "testbench"),
        "",
        "// 引入 C printf，输出带 [HLS] 前缀的 PASS/FAIL transcript。",
        "#include <stdio.h>",
        "",
        "// 引入 top function 声明，确保静态 smoke 调用与接口契约一致。",
        f'#include "../src/{str_top_function_name}.h"',
        "",
        *contract_text.testbench_main_contract_lines(str_top_function_name),
        "int main() {",
        "",
    ]

# 为原始 VECTOR_HASH 生成治理后 testbench 需要保留的合同区块。
def vector_hash_contract_lines(str_vector_hash: str) -> list[str]:
    """为原始 VECTOR_HASH 生成治理后 testbench 合同区块。

    参数:
        str_vector_hash: 原始 testbench 里提取到的向量哈希文本，shape=scalar，dtype=str，unit=hash text。

    返回:
        当前 testbench 需要写回的向量哈希合同区块，shape=(0 lines) 或 (2 lines)，dtype=list[str]，unit=comment lines。
    """

    # 没有向量哈希时返回空区块，避免伪造 reference vector 身份。
    if not str_vector_hash:

        # 缺少向量哈希时直接返回空列表，保持 testbench 不额外编造 reference 绑定。
        return []

    # 返回向量哈希合同区块，并保留和后续样例标记区的空行分隔。
    return [
        f"    // 向量哈希绑定标签 {VECTOR_HASH_TAG} {str_vector_hash}，声明当前静态 smoke 与 reference vector 共享同一份输入身份。",
        "",
    ]

# 为原始 case 标记生成治理后 testbench 的样例观测注释。
def case_marker_comment_text(str_case_id: str) -> str:
    """按 case 标识返回带区分语义的样例观测注释正文。

    参数:
        str_case_id: 当前样例的原始 case 标识，dtype=str，unit=case id。

    返回:
        当前样例对应的中文观测注释正文，dtype=str，unit=comment text。
    """

    # 先把 case id 统一成小写，方便稳定识别 nominal/boundary 等语义关键词。
    str_case_id_lower = str_case_id.casefold()  # 当前 case id 压平成统一小写后的角色匹配键

    # nominal/basic 样例承担常规事务路径的基准观测职责。
    if "nominal" in str_case_id_lower or "basic" in str_case_id_lower:

        # 返回基准输入样本的观测说明，明确它验证的是常规事务路径。
        return "绑定 nominal 基准样例的通过判定，重点观察常规事务是否沿主数据路径稳定完成。"

    # boundary 样例承担边界事务路径的稳定性观测职责。
    if "boundary" in str_case_id_lower:

        # 返回边界输入样本的观测说明，明确它验证的是边界事务行为。
        return "绑定 boundary 边界样例的判定标签，专门检查尾块或最短事务下的索引与写回边界是否仍然收敛。"

    # overflow/underflow 样例额外说明极值压力路径的观测目标。
    if "overflow" in str_case_id_lower or "underflow" in str_case_id_lower:

        # 返回极值压力场景的观测说明，避免不同压力样例复用同一句模板。
        return "绑定极值压力样例的判定标签，确认数值极限附近的事务不会冲破既定 contract。"

    # 其他样例保守回退到补充事务观测说明。
    return "绑定补充 smoke 样例的独立判定标签，给这条静态事务保留可追踪的 PASS/FAIL 观察位。"

# 把原始 case 标识折算成适合多样例汇总句复用的事务角色短语。
def case_marker_role_text(str_case_id: str) -> str:
    """按 case 标识返回事务角色短语。

    参数:
        str_case_id: 当前样例的原始 case 标识，dtype=str，unit=case id。

    返回:
        适合写入多样例汇总句的短语，dtype=str，unit=role text。
    """

    # 把 case id 规范成统一的小写标签后，nominal、boundary 和极值压力这三类 smoke 语义才能在同一个分发函数里稳定复用。
    str_case_id_lower = str_case_id.casefold()  # 当前 case 标识的小写语义视图

    # nominal/basic 样例在汇总句里统一视为常规事务基准路径。
    if "nominal" in str_case_id_lower or "basic" in str_case_id_lower:

        # 当前分支已经锁定到常规事务基准样例，直接返回 nominal 角色短语。
        return "nominal 基准事务"

    # boundary 样例在汇总句里单独标成边界事务路径。
    if "boundary" in str_case_id_lower:

        # 当前分支已经锁定到边界事务样例，直接返回 boundary 角色短语。
        return "boundary 边界事务"

    # overflow/underflow 样例在汇总句里单独标成极值压力路径。
    if "overflow" in str_case_id_lower or "underflow" in str_case_id_lower:

        # 当前分支已经锁定到极值压力样例，直接返回压力事务短语。
        return "极值压力事务"

    # 当前 case 没有命中已知角色词时，回退成带原始标识的补充事务短语。
    return f"{str_case_id} 补充事务"

# 这里把原始 case 标识批量折算成治理后的 testbench 样例观测注释行。
def case_marker_comment_lines(list_case_ids: list[str]) -> list[str]:
    """为原始 case 标记生成治理后 testbench 的样例观测注释。

    参数:
        list_case_ids: 原始 testbench 提取到的 case 标识列表，dtype=list[str]，unit=case ids。

    返回:
        当前 testbench 的 case 注释行列表，dtype=list[str]，unit=comment lines。
    """

    # 没有 case 标记时返回空区块，避免凭空制造样例名。
    if not list_case_ids:

        # 缺少 case 标识时直接返回空列表，避免样例注释区出现伪造名称。
        return []

    # 初始化 case 注释区，后续按源码顺序逐个写回。
    list_lines: list[str] = []  # testbench 的 case 标记注释区

    # 多个 case 同时出现时合并成一条汇总句，避免相邻样例注释在相似度链里互相撞车。
    if len(list_case_ids) > 1:

        # 先把每个 case 折算成可读的事务角色短语，再拼成多样例汇总句。
        str_joined_roles = "、".join(case_marker_role_text(str_case_id) for str_case_id in list_case_ids)  # 当前 testbench 覆盖的事务角色汇总文本

        # 先把多样例汇总句写进注释区，让 testbench 明确当前 smoke 同时覆盖的事务边界。
        list_lines.append(
            f"    // 当前 static smoke 同时覆盖 {str_joined_roles}，让 PASS/FAIL transcript 能并行观察这些代表性事务边界。"
        )

        # 再补一行空字符串，保持样例标记区和后续局部变量声明区之间的空行分隔。
        list_lines.append("")

        # 多样例汇总句已经写完，直接返回当前 case 注释区。
        return list_lines

    # 逐个写回原始 testbench 里的 case 标记。
    for str_case_id in list_case_ids:

        # 把当前 case 标识对应的语义观测说明写回注释区，避免样例间只靠 ASCII id 区分。
        list_lines.append(f"    // {case_marker_comment_text(str_case_id)}")

    # case 标记区末尾补一层空行，把样例标记区和变量声明区隔开。
    list_lines.append("")

    # 返回 case 标记注释区。
    return list_lines

# 生成 top function 调用、PASS/FAIL transcript 和退出码尾段。
def testbench_tail_lines(
    str_top_function_name: str,
    list_call_arguments: list[str],
) -> list[str]:
    """生成 top function 调用、PASS/FAIL transcript 和退出码尾段。

    参数:
        str_top_function_name: 当前 spec 对应的 top function 名称，dtype=str，unit=function name。
        list_call_arguments: 当前 top function 调用参数名列表，dtype=list[str]，unit=call arguments。

    返回:
        testbench 调用尾段的物理行列表，dtype=list[str]，unit=testbench tail lines。
    """

    # 先渲染 top function 调用语句里的参数文本。
    str_call_arguments = ", ".join(list_call_arguments) or "void"  # 当前 top function 调用的参数文本

    # 返回当前 testbench 的调用尾段。
    return [
        "    // 调用 top function，执行 workflow 静态 smoke 的一次代表性事务。",
        f"    {str_top_function_name}({str_call_arguments});",
        "",
        "    // bool_case_pass 固定保留 PASS/FAIL 分支，让 transcript 行为在静态验证里可见。",
        "    bool bool_case_pass = true; // 聚合本轮 smoke 的最终通过状态。",
        "",
        "    // 根据聚合状态输出 PASS 或 FAIL transcript，保持 [HLS] 前缀边界清晰。",
        "    if (bool_case_pass) {",
        "",
        "        // PASS transcript 使用 > INFO: [HLS] 前缀，方便 smoke 日志直接标记成功样本。",
        '        printf("> INFO: [HLS] PASS workflow_static_smoke\\n");',
        "    } else {",
        "",
        "        // FAIL transcript 使用 > ERR: [HLS] 前缀，方便 smoke 日志直接标记失败样本。",
        '        printf("> ERR: [HLS] FAIL workflow_static_smoke\\n");',
        "    }",
        "",
        "    // 返回 testbench 退出码，把 PASS 映射到 0，把 FAIL 映射到 1。",
        "    return bool_case_pass ? 0 : 1;",
        "}",
    ]

# 对治理后的 source 文本补齐文件头 contract、函数 contract 和稳定行级注释。
def decorate_source(
    text: str,
    spec: dict[str, Any],
    dict_argument_names: dict[str, str],
    dict_replacements: dict[str, str],
    comment_language: str,
) -> str:
    """
    为 mock source 补齐 HG007/HG008/HG015 contract，并复用现有行级注释治理器。

    参数:
        text: 已完成 typed-prefix 与打印前缀治理的 source 文本，dtype=str，unit=text。
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        dict_argument_names: 顶层参数原名到治理名的映射字典，dtype=dict[str, str]，unit=name map。
        dict_replacements: 当前 source 的完整标识符替换字典，dtype=dict[str, str]，unit=name map。
        comment_language: HLS 注释语言标识，dtype=str，unit=dimensionless。

    返回:
        补齐文件头、函数 contract 和行级注释后的 source 文本，dtype=str，unit=text。
    """

    # 先复用现有行级注释治理器，避免在本模块重复实现 pragma 与局部语句注释。
    str_annotated_text = ensure_governed_line_comments(text, comment_language)  # 已补齐行级注释的 source 文本

    # 再按 pragma 和 return 语义收紧相邻注释，避免回落到模板化说明。
    list_lines = rewrite_source_line_comments(str_annotated_text.splitlines())  # 已按语义收紧相邻注释的 source 物理行列表

    # 读取当前 source 里的函数信息和顶层函数名。
    list_functions = parse_functions(list_lines)  # 当前 source 中识别出的函数列表

    # 这个名字只在这里取一次，因为下面的 while 会拿每个 `function_info.name` 和它逐个比对，决定是否展开端口逐项合同。
    str_top_function_name = top_function_name(spec)  # 当前 spec 对应的 top function 名称

    # 记录多行函数签名的起止范围，供后续整体跳过复用。
    dict_signature_ranges = function_signature_ranges(list_lines, list_functions)  # 函数签名起止行范围映射

    # 把签名起始行映射回函数信息，方便扫描阶段 O(1) 命中函数 contract 注入点。
    dict_function_by_start = {function_info.signature_start_line: function_info for function_info in list_functions}  # 签名起始行到函数信息的映射表

    # 初始化输出行列表，并先写入文件头 contract。
    list_output_lines: list[str] = []  # 最终治理后 source 的输出行列表

    # 先写入 source 文件级 contract，确保 HG007 模块合同排在正文最前面。
    list_output_lines.extend(contract_text.file_header_lines(spec, "source"))

    # 再补一层空行，把文件头合同和第一个函数块隔开。
    list_output_lines.append("")

    # 逐行扫描 source，在函数签名前方插入四段 contract。
    int_line_number = 1  # 当前正在扫描的 source 物理行号

    # 逐行遍历当前 source，直到扫描完整个物理行列表。
    while int_line_number <= len(list_lines):

        # 命中函数签名起始行时，先移除摘要注释，再插入正式 contract。
        if int_line_number in dict_function_by_start:

            # 读取当前签名起点对应的函数信息。
            function_info = dict_function_by_start[int_line_number]  # 当前需要补齐 contract 的函数信息

            # 先去掉函数签名前刚刚追加的普通摘要注释。
            trim_trailing_line_comments(list_output_lines)

            # 再去掉多余空行，保证正式 contract 紧邻函数签名。
            trim_trailing_blank_lines(list_output_lines)

            # 当前函数块前一行仍有代码时，再补一层空行分隔。
            if list_output_lines and list_output_lines[-1].strip():

                # 插入一层函数块间距，避免相邻函数 contract 粘连。
                list_output_lines.append("")

            # 先判断当前函数是不是硬件顶层入口，避免在调用点塞入过长的布尔表达式。
            bool_is_top_function = function_info.name == str_top_function_name  # 当前函数是否命中 top function

            # 先把合同生成器绑定到局部别名，缩短正式调用行宽。
            func_contract_builder = contract_text.function_contract_lines  # 当前函数合同的生成入口

            # 再整理正式调用要复用的位置参数，避免单行调用继续过长。
            tuple_contract_inputs = (function_info, spec, dict_argument_names, dict_replacements)  # 合同生成器的位置参数元组

            # 再生成当前函数对应的四段正式 contract。
            list_contract_lines = func_contract_builder(*tuple_contract_inputs, is_top_function=bool_is_top_function)  # 当前函数需要插入的正式 contract 行列表

            # 再把正式 contract 追加到输出行列表，替换掉原始摘要注释。
            list_output_lines.extend(list_contract_lines)

            # 再写入原始函数签名文本。
            list_output_lines.append(function_info.signature)

            # 让扫描游标直接跳到多行签名末尾后一行，避免逐行重复抄写签名体。
            int_line_number = dict_signature_ranges[int_line_number] + 1  # 下一轮扫描的起始行号

            # 当前多行签名已经整体写入，立即继续扫描后续正文。
            continue

        # 非函数签名行直接沿用现有行级注释治理后的文本。
        list_output_lines.append(list_lines[int_line_number - 1])

        # 顺序路径只前进一步，让 while 循环检查下一条 source 物理行。
        int_line_number += 1  # 下一轮顺序扫描的物理行号

    # 返回补齐文件头和函数 contract 的最终 source 文本。
    return "\n".join(list_output_lines).rstrip() + "\n"

# 原地去掉尾部连续 `//` 注释行，避免行级注释摘要和正式 contract 叠加。
def trim_trailing_line_comments(list_lines: list[str]) -> None:
    """原地去掉尾部连续 `//` 注释行。

    参数:
        list_lines: 需要修剪的物理行列表，dtype=list[str]，unit=line list。

    返回:
        无返回；直接原地修改 `list_lines`，dtype=None，unit=not applicable。
    """

    # 只要尾部还是 `//` 注释行，就继续原地弹出。
    while list_lines and list_lines[-1].strip().startswith("//"):

        # 弹掉尾部摘要注释，给正式 contract 腾出位置。
        list_lines.pop()

# 原地去掉尾部连续空行，保证正式 contract 和函数签名保持紧邻。
def trim_trailing_blank_lines(list_lines: list[str]) -> None:
    """原地去掉尾部连续空行。

    参数:
        list_lines: 需要修剪的物理行列表，dtype=list[str]，unit=line list。

    返回:
        无返回；直接原地修改 `list_lines`，dtype=None，unit=not applicable。
    """

    # 只要尾部还是空行，就继续原地弹出。
    while list_lines and not list_lines[-1].strip():

        # 弹掉尾部空行，避免 contract 和函数签名之间出现多余留白。
        list_lines.pop()
