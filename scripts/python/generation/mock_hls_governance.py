"""用薄入口协调 mock HLS 的 typed-prefix、contract、打印前缀与行级注释治理。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 宽泛类型提示用于对外保持稳定的 spec 参数签名。
from typing import Any

# 协议治理模块负责 typed-prefix、文件角色和打印前缀修复。
from .mock_hls_protocols import (
    file_kind,
    governed_argument_candidate_names,
    governed_top_argument_name_map,
    normalize_hls_print_prefixes,
    rename_source_identifiers,
)

# source/header/testbench 合同渲染模块负责 HG007/HG008/HG015 输出。
from .mock_hls_source_comments import (
    build_governed_header,
    build_governed_testbench,
    decorate_source,
)

# 对 workflow 即将写盘的 mock HLS 文本执行统一治理。
def govern_mock_hls_text(
    text: str,
    spec: dict[str, Any],
    rel_path: str,
    comment_language: str,
) -> str:
    """
    对 mock HLS 文本补齐 typed-prefix、contract 与 `[HLS]` 输出边界。

    参数:
        text: 原始 mock HLS 文本，dtype=str，unit=text。
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        rel_path: 当前 manifest 文件的相对路径，dtype=str，unit=filesystem path。
        comment_language: HLS 注释语言标识，dtype=str，unit=dimensionless。

    返回:
        满足当前 workflow 治理契约的 HLS 文本，dtype=str，unit=text。
    """

    # 先识别当前文本对应的 HLS 产物角色。
    str_file_kind = file_kind(rel_path)  # 当前 mock HLS 文本的产物角色

    # 再计算顶层参数原名到 typed-prefix 治理名的映射。
    dict_argument_names = governed_top_argument_name_map(spec)  # 顶层参数的治理名映射表

    # 头文件直接走专用渲染器，避免在原始文本上做脆弱修补。
    if str_file_kind == "header":

        # 返回专用 header 治理结果。
        return build_governed_header(spec, dict_argument_names, comment_language)

    # testbench 同样走专用渲染器，统一 PASS/FAIL transcript 和 vector hash 契约。
    if str_file_kind == "testbench":

        # testbench 分支直接交给专用渲染器，避免 source 注释流程误改 transcript 约束。
        return build_governed_testbench(spec, text, dict_argument_names, comment_language)

    # 先执行 typed-prefix 改名阶段，并保留一份供后续 contract 插桩复用的替换表。
    tuple_rename_result = rename_source_identifiers(text, dict_argument_names)  # source 文本改名后的结果二元组

    # 读取 typed-prefix 改名后的 source 文本。
    str_renamed_text = tuple_rename_result[0]  # 当前 source 的 typed-prefix 改名结果

    # 读取本轮 source 治理使用的完整替换字典。
    dict_replacements = tuple_rename_result[1]  # 当前 source 的原名到新名替换表

    # 再补齐当前 source 文本里的 `[HLS]` 打印前缀边界。
    str_prefixed_text = normalize_hls_print_prefixes(str_renamed_text)  # 已补齐打印前缀的 source 文本

    # 最后补齐文件头 contract、函数 contract 与行级注释覆盖。
    return decorate_source(
        str_prefixed_text,
        spec,
        dict_argument_names,
        dict_replacements,
        comment_language,
    )
