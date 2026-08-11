"""收拢 mock HLS 治理入口需要的文件角色、参数视图与公共重写路由。"""

# 启用延迟注解，避免类型提示在导入阶段过早求值。
from __future__ import annotations

# 路径对象和宽泛类型提示负责支撑文件角色判断与 spec 读取。
from pathlib import Path
from typing import Any

# 标识符重写模块负责 typed-prefix 改名与字符串外安全替换。
from .mock_hls_identifier_rewrite import rename_source_identifiers

# 打印前缀模块负责为 HLS transcript 补齐固定的 `[HLS]` 级别前缀。
from .mock_hls_print_normalization import normalize_hls_print_prefixes

# 声明解析与 family 推断模块负责 typed-prefix 所需的基础语义判断。
from .mock_hls_type_inference import family_from_declaration_text, typed_name_for_identifier

# 根据相对路径识别 header/source/testbench 三类 mock HLS 产物。
def file_kind(rel_path: str) -> str:
    """根据 manifest 相对路径识别 mock HLS 产物角色。

    参数:
        rel_path: 当前 mock HLS 文件的相对路径，shape=scalar，dtype=str，unit=filesystem path。

    返回:
        `header`、`source` 或 `testbench` 三类文件角色之一，shape=scalar，dtype=str，unit=file kind。
    """

    # 把相对路径转换成 Path，统一复用 suffix 和 stem 判断。
    path_relative = Path(rel_path)  # 当前 manifest 相对路径对象

    # 头文件后缀直接归入 header 角色。
    if path_relative.suffix.lower() in {".h", ".hh", ".hpp"}:

        # 返回头文件角色，供 header 治理分支直接使用。
        return "header"

    # `_tb` 命名模式统一归入 testbench 角色。
    if "_tb" in path_relative.stem.lower():

        # 返回 testbench 角色，供 transcript 和向量哈希治理使用。
        return "testbench"

    # 其余 C/C++ 文本默认视为 kernel source。
    return "source"

# 从 spec 中解析统一的 top function 名称，避免各模块重复维护同一套回退链。
def top_function_name(spec: dict[str, Any]) -> str:
    """读取当前 spec 的 top function 名称。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        当前 mock HLS 产物应使用的顶层函数名，shape=scalar，dtype=str，unit=function name。
    """

    # 读取 interfaces 字段，供 top_function 与 name 的回退链共用。
    dict_interfaces = spec.get("interfaces", {})  # spec 中的接口配置字段

    # 返回规范化后的顶层函数名。
    return str(dict_interfaces.get("top_function") or spec.get("name") or "kernel")

# 提取结构合法的参数字典列表，供 typed-prefix 与合同逻辑共享。
def argument_dicts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """返回 spec 中通过结构校验的参数字典列表。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        仅包含字典参数项的列表，shape=(n items)，dtype=list[dict[str, Any]]，unit=argument list。
    """

    # 先读取 interfaces 里的原始参数列表，供类型过滤复用。
    list_raw_arguments = spec.get("interfaces", {}).get("arguments", [])  # spec 中登记的原始参数列表

    # 再返回经过类型过滤的参数字典列表，避免后续逻辑反复判空判型。
    return [dict_argument for dict_argument in list_raw_arguments if isinstance(dict_argument, dict)]

# 公开顶层参数名到 typed-prefix 名称的映射，供验证层和 stage verifier 复用。
def governed_top_argument_name_map(spec: dict[str, Any]) -> dict[str, str]:
    """生成 workflow 顶层参数的 typed-prefix 名称映射。

    参数:
        spec: 当前 HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        原始参数名到治理后参数名的映射字典，shape=(n items)，dtype=dict[str, str]，unit=name map。
    """

    # 初始化顶层参数名映射，后续逐项登记原名与治理名。
    dict_argument_names: dict[str, str] = {}  # 顶层参数原名到治理名的映射表

    # 逐个处理结构合法的接口参数。
    for dict_argument in argument_dicts(spec):

        # 归一化当前参数名，避免空白字符串进入映射表。
        str_original_name = str(dict_argument.get("name") or "").strip()  # 当前顶层参数的原始名称

        # 缺少有效参数名时直接跳过该条目。
        if not str_original_name:

            # 参数名为空时直接继续，避免映射表里出现伪造键。
            continue

        # 根据声明文本推断当前参数应使用的 typed-prefix family。
        str_family = family_from_declaration_text(f"{dict_argument.get('type', 'int')} {str_original_name}")  # 当前参数的类型家族

        # 记录当前参数对应的治理后名称。
        dict_argument_names[str_original_name] = typed_name_for_identifier(str_original_name, str_family)  # 当前顶层参数的治理后名称

    # 返回顶层参数原名到治理名的映射结果。
    return dict_argument_names

# 为单个接口参数生成验证阶段允许接受的端口名候选。
def governed_argument_candidate_names(argument: dict[str, Any]) -> tuple[str, ...]:
    """返回单个接口参数允许匹配的原名与治理名候选。

    参数:
        argument: 当前接口参数字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。

    返回:
        至少包含原名，必要时追加 typed-prefix 治理名的候选元组，shape=(n items)，dtype=tuple[str, ...]，unit=name candidates。
    """

    # 先读取并规整参数原名，供候选名构造复用。
    str_original_name = str(argument.get("name") or "").strip()  # 当前接口参数的原始名称

    # 没有有效参数名时返回空候选，避免伪造接口名称。
    if not str_original_name:

        # 返回空元组，表示当前参数无法参与端口名匹配。
        return ()

    # 根据参数类型和名称推断当前参数的 family。
    str_family = family_from_declaration_text(f"{argument.get('type', 'int')} {str_original_name}")  # 当前接口参数的类型家族

    # 生成当前参数的 typed-prefix 治理名。
    str_typed_name = typed_name_for_identifier(str_original_name, str_family)  # 当前接口参数的治理后名称

    # 原名与治理名相同的场景只保留一个候选，避免生成重复项。
    if str_typed_name == str_original_name:

        # 返回只含原名的候选元组。
        return (str_original_name,)

    # 返回原名和治理名，兼容老产物与新产物的端口名比对。
    return (str_original_name, str_typed_name)
