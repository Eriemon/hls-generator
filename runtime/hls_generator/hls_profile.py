"""Vitis HLS profile 的静态校验与修复提示词构造工具。"""

# 延迟注解求值，避免运行时解析前向引用。
from __future__ import annotations

# JSON 用于报告裁剪，正则用于源码和 pragma 扫描。
import json
import re
from pathlib import Path
from typing import Any

# 模式规则模块提供高级头文件约束与 pattern 名归一化能力。
from .patterns import ADVANCED_LIBRARY_HEADERS, canonical_pattern_name, required_pattern_headers

# 默认禁用特性覆盖动态内存、异常与常见不受支持的 STL 容器。
DEFAULT_FORBIDDEN_FEATURES = (  # 默认禁止的 C++/STL 特性
    "std::vector",  # 动态扩容容器
    "new",  # C++ 动态分配操作符
    "malloc",  # C 风格堆内存分配
    "free",  # C 风格堆内存释放
    "throw",  # C++ 异常抛出
    "catch",  # C++ 异常捕获
    "std::map",  # 有序关联容器
    "std::unordered_map",  # 哈希关联容器
    "std::list",  # 链表容器
    "std::deque",  # 双端队列容器
    "std::string",  # 动态字符串容器
)

# HLS 源码扫描只关注这些 C/C++ 文件后缀。
SOURCE_FILE_SUFFIXES = (".cpp", ".cc", ".cxx", ".h", ".hpp")  # 参与源码合并扫描的文件后缀

# HLS cfg 扫描统一只读取 cfg 文件。
CFG_FILE_SUFFIXES = (".cfg",)  # 参与配置扫描的文件后缀

# 统一构造带词边界的正则，避免把静态字符串散落在检查逻辑里。
def _word_pattern(token: str) -> str:
    """
    为普通标识符构造带词边界的正则。

    :param token: 需要精确匹配的标识符文本。
    :return: 带词边界的正则字符串。
    """

    # 使用 re.escape 保护 C++ 作用域符号和特殊字符。
    return rf"\b{re.escape(token)}\b"

# new / malloc / free / catch 需要更具体的语义片段匹配。
def _forbidden_feature_patterns() -> dict[str, str]:
    """
    返回默认禁用特性的正则模式映射。

    参数:
        无显式输入参数；模式内容来自模块内建禁用特性集合。
    返回:
        特性名到源码扫描正则的映射字典，dtype=dict[str, str]，unit=regex mapping。
    """

    # 映射集中维护，便于 profile 扩展或局部覆写。
    return {
        "std::vector": _word_pattern("std::vector"),
        "new": r"\bnew\s+[A-Za-z_]",
        "malloc": r"\bmalloc\s*\(",
        "free": r"\bfree\s*\(",
        "throw": _word_pattern("throw"),
        "catch": r"\bcatch\s*\(",
        "std::map": _word_pattern("std::map"),
        "std::unordered_map": _word_pattern("std::unordered_map"),
        "std::list": _word_pattern("std::list"),
        "std::deque": _word_pattern("std::deque"),
        "std::string": _word_pattern("std::string"),
    }

# glob 模式按后缀拼接，避免把具体扫描路径硬编码在主逻辑里。
def _glob_patterns_from_suffixes(suffixes: tuple[str, ...]) -> list[str]:
    """
    根据后缀列表构造递归 glob 模式。

    :param suffixes: 需要扫描的文件后缀元组。
    :return: 对应的 `**/*suffix` 模式列表。
    """

    # 所有扫描都采用当前 root 下递归查找的固定模式。
    return [f"**/*{str_suffix}" for str_suffix in suffixes]

# 对 profile 进行源码和 cfg 双通道静态校验。
def validate_hls_profile(
    profile: dict[str, Any],
    root: Path,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    校验 HLS profile 是否和当前工件内容一致。

    :param profile: 当前 spec 中的 hls_profile 字典。
    :param root: 待检查 HLS 工件所在目录。
    :param spec: 完整 spec 字典，供接口字段和 pattern 元数据校验使用。
    :return: 静态校验问题列表；空列表表示未发现 profile 违例。
    """

    # 空 profile 视为没有附加约束，不产生错误。
    if not profile:

        # 没有 profile 时无需继续扫描源码和 cfg。
        return []

    # 源码检查需要先合并所有 C/C++ 与头文件文本。
    str_source_text = _source_text(root)  # 聚合后的源码与头文件文本

    # cfg 检查只读取工程配置文本。
    str_cfg_text = _cfg_text(root)  # 聚合后的 cfg 文本

    # 所有静态校验问题都汇总到同一个列表返回。
    list_issues: list[dict[str, Any]] = []  # 当前 profile 的静态问题列表

    # 元数据缺失会直接影响 pattern-specific 合同。
    list_issues.extend(_check_required_metadata(profile))

    # 头文件检查用于约束高级 HLS 库的显式依赖。
    list_issues.extend(_check_headers(profile, str_source_text))

    # 允许库检查保证不会引入未授权的高级库头文件。
    list_issues.extend(_check_allowed_libraries(profile, str_source_text))

    # 禁用特性检查负责卡住动态内存、异常和不受支持 STL。
    list_issues.extend(_check_forbidden_features(profile, str_source_text))

    # 接口模式检查用于约束 pragma 中的 INTERFACE mode。
    list_issues.extend(_check_interface_modes(profile, str_source_text))

    # 必需 pragma 检查负责约束 profile 明示的 pragma 合同。
    list_issues.extend(_check_required_pragmas(profile, str_source_text, spec))

    # pattern 语义检查用于补强 task_graph / rle_axis / fft / cordic 等特殊合同。
    list_issues.extend(_check_pattern_semantics(profile, str_source_text))

    # 静态数组检查负责拦住未定长栈数组。
    list_issues.extend(_check_static_arrays(profile, str_source_text))

    # 禁止组合检查负责卡住 profile 声明的不兼容 pragma 或模式并存。
    list_issues.extend(_check_forbidden_combinations(profile, str_source_text))

    # cfg 检查最后补齐 syn.file 和其他 cfg 条目合同。
    list_issues.extend(_check_cfg(profile, str_cfg_text))

    # 返回所有静态 profile 问题，供 validation 和 prompt 共用。
    return list_issues

# 把 profile 与当前相关 issue 裁剪成模型可消费的修复 prompt。
def build_hls_optimizer_prompt(validation_json: dict[str, Any], profile: dict[str, Any]) -> str:
    """
    构造给修复模型使用的 HLS profile 修复提示词。

    :param validation_json: 完整 validation 报告字典。
    :param profile: 当前 spec 的 hls_profile 字典。
    :return: 可直接交给修复模型的 Markdown 提示词文本。
    """

    # profile 原文需要保留可读缩进，方便模型对照约束。
    str_profile_json = json.dumps(  # 序列化后的 profile JSON 文本
        profile,  # 待嵌入提示词的 profile 对象
        indent=2,  # 用两空格缩进保持 JSON 可读
        ensure_ascii=False,  # 保留中文键值原样输出
        sort_keys=True,  # 固定键顺序便于模型稳定对照
    )

    # 只保留和 HLS profile 直接相关的问题，降低提示词噪声。
    list_profile_issues = _profile_related_issues(validation_json)  # 过滤后的 profile 相关问题

    # 相关问题也保持 JSON 结构，便于修复模型按字段理解。
    str_issues_json = json.dumps(  # 序列化后的 profile 相关 issue JSON 文本
        list_profile_issues,  # 已筛出的 profile 相关问题列表
        indent=2,  # 用两空格缩进保持 issue JSON 易读
        ensure_ascii=False,  # 保留中文错误原文
    )

    # 返回带 profile、相关 issue 和修复约束的完整提示词。
    return (
        "# HLS profile repair prompt\n\n"
        "You are repairing Vitis HLS C++ artifacts to satisfy the project HLS profile. "
        "Do not change the algorithm unless an issue explicitly requires it.\n\n"
        "## HLS profile\n\n"
        "```json\n"
        f"{str_profile_json}\n"
        "```\n\n"
        "## Profile-related validation issues\n\n"
        "```json\n"
        f"{str_issues_json}\n"
        "```\n\n"
        "## Repair constraints\n\n"
        "- Align `hls_config.cfg` with `syn.top` and every required `syn.file`.\n"
        "- Emit `#pragma HLS INTERFACE` pragmas for all external arguments using only allowed interface modes.\n"
        "- Remove forbidden C++ features from kernel code: dynamic memory, exceptions, "
        "recursion, and unsupported STL containers.\n"
        "- Replace dynamic arrays with static bounded arrays or stream/buffer structures "
        "that Vitis HLS can synthesize.\n"
        "- Preserve the manifest/code-fence output contract and regenerate only the affected HLS files.\n"
    )

# 禁用特性检查集中扫描源码中的 STL、异常与动态内存痕迹。
def _check_forbidden_features(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """
    检查源码中是否出现 profile 禁止的语言或库特性。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :return: 命中禁用特性时生成的问题列表。
    """

    # profile 可以覆写禁用特性列表；否则使用默认集合。
    list_forbidden_features = (
        profile.get("forbidden_features") or DEFAULT_FORBIDDEN_FEATURES  # 优先采用 profile 显式覆写，否则回退默认特性表
    )  # 当前 profile 生效的禁用特性列表

    # 正则映射集中定义，便于对个别 token 使用更精准模式。
    dict_feature_patterns = _forbidden_feature_patterns()  # 禁用特性到正则模式的映射

    # 命中的禁用特性都折叠成统一 issue 结构。
    list_issues: list[dict[str, Any]] = []  # 禁用特性检查产生的问题列表

    # 逐项扫描 profile 声明的所有禁用特性。
    for obj_feature in list_forbidden_features:

        # 特性名统一转成字符串，兼容上游混入非字符串值。
        str_feature = str(obj_feature)  # 当前正在检查的禁用特性名

        # 未内建的特性模式退化为精确转义匹配。
        str_feature_pattern = dict_feature_patterns.get(  # 当前特性的源码扫描模式
            str_feature,  # 当前禁用特性名
            re.escape(str_feature),  # 未建模特性退化为字面量匹配
        )

        # 命中特性时追加标准化错误项。
        if re.search(str_feature_pattern, source_text):

            # 错误消息保留原始特性名，方便用户快速定位。
            list_issues.append(
                _issue(
                    "error",
                    f"HLS profile violation: forbidden feature {str_feature!r} was found.",
                )
            )

    # 返回禁用特性扫描结果。
    return list_issues

# INTERFACE pragma 的 mode 必须落在 profile 允许的模式集合里。
def _check_interface_modes(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """
    检查源码中的 INTERFACE pragma mode 是否被 profile 允许。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :return: 命中非法 interface mode 时生成的问题列表。
    """

    # profile 支持两个兼容字段名，二者都允许传入。
    list_allowed_modes = (
        profile.get("allowed_interface_modes") or profile.get("interface_modes") or []  # 兼容 allowed_interface_modes/interface_modes 两种字段
    )  # 当前 profile 允许的 interface mode 列表

    # 没有 mode 限制时无需继续扫描 pragma。
    if not list_allowed_modes:

        # 空约束表示放行所有 mode。
        return []

    # 允许集合使用字符串化结果，避免大小写或类型噪声。
    set_allowed_modes = {str(obj_mode) for obj_mode in list_allowed_modes}  # 允许的 interface mode 集合

    # 所有命中的非法 mode 都折叠成统一 issue。
    list_issues: list[dict[str, Any]] = []  # interface mode 检查产生的问题列表

    # 逐行扫描源码中的 pragma，避免误读多行上下文。
    for str_line in source_text.splitlines():

        # 只处理 HLS INTERFACE pragma 行。
        if "#pragma HLS INTERFACE" not in str_line:

            # 非 interface pragma 行直接跳过。
            continue

        # 从 pragma 中解析 mode=... 或紧随 INTERFACE 的模式名。
        str_mode = _pragma_mode(str_line)  # 当前 pragma 解析出的 interface mode

        # 只对能解析出的 mode 做白名单校验。
        if str_mode and str_mode not in set_allowed_modes:

            # 追加非法 mode 问题，方便 validation 统一展示。
            list_issues.append(
                _issue(
                    "error",
                    f"HLS profile violation: interface mode {str_mode!r} is not allowed.",
                )
            )

    # 返回 interface mode 校验结果。
    return list_issues

# required_pragmas 和接口 port pragma 都属于 profile 的硬合同。
def _check_required_pragmas(
    profile: dict[str, Any],
    source_text: str,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    检查源码是否包含 profile 要求的 pragma。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :param spec: 完整 spec 字典，用于读取接口参数名。
    :return: 缺失必需 pragma 时生成的问题列表。
    """

    # 所有 pragma 相关问题都累积到同一列表。
    list_issues: list[dict[str, Any]] = []  # pragma 合同检查产生的问题列表

    # 逐条检查 profile 显式声明的必需 pragma 片段。
    for obj_pragma in profile.get("required_pragmas", []) or []:

        # pragma 片段统一转成字符串，便于直接子串匹配。
        str_pragma_token = str(obj_pragma)  # 当前必需 pragma 文本

        # 非空 token 且源码缺失时才追加错误。
        if str_pragma_token and str_pragma_token not in source_text:

            # 保留原始 pragma 文本，方便用户定位缺口。
            list_issues.append(
                _issue(
                    "error",
                    f"HLS profile violation: required pragma {str_pragma_token!r} was not found.",
                )
            )

    # profile 明确关闭接口 pragma 检查时，保留已收集的问题直接返回。
    if not profile.get("require_interface_pragmas", True):

        # 仅跳过接口端口 pragma 检查，不影响 required_pragmas 结果。
        return list_issues

    # 从 spec 中读取接口参数列表，逐个确认 port pragma。
    for obj_argument in spec.get("interfaces", {}).get("arguments", []) or []:

        # 只有带名字的参数对象才参与接口 pragma 检查。
        if not isinstance(obj_argument, dict) or not obj_argument.get("name"):

            # 跳过无法提供 port 名的异常条目。
            continue

        # 参数名用于构造 port=... 的正则匹配。
        str_argument_name = str(obj_argument["name"])  # 当前接口参数名

        # 缺失接口 pragma 时追加错误，要求每个外部参数都显式声明。
        if not re.search(
            rf"#pragma\s+HLS\s+INTERFACE[^\n]*\bport\s*=\s*{re.escape(str_argument_name)}\b",
            source_text,
        ):

            # 错误消息指出缺少 pragma 的具体参数名。
            list_issues.append(
                _issue(
                    "error",
                    f"HLS profile violation: missing interface pragma for argument {str_argument_name!r}.",
                )
            )

    # 返回所有 pragma 合同问题。
    return list_issues

# required_metadata_fields 是 pattern 语义能否成立的前置条件。
def _check_required_metadata(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """
    检查 profile 必填元数据是否已经确认。

    :param profile: 当前 hls_profile 字典。
    :return: 缺失必填元数据时生成的问题列表。
    """

    # metadata 不是字典时按空对象处理，统一缺失行为。
    dict_metadata = (
        profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}  # 非字典 metadata 退回空对象参与缺失检查
    )  # 当前 profile 中已确认的元数据

    # 缺失元数据都折叠成统一 issue 列表。
    list_issues: list[dict[str, Any]] = []  # 元数据缺失问题列表

    # 逐项检查必填字段是否已经有非空值。
    for obj_field in profile.get("required_metadata_fields", []) or []:

        # 字段名统一转字符串，兼容上游混入数字或其他类型。
        str_key = str(obj_field)  # 当前必填元数据字段名

        # None、空串、空列表和空对象都视为未确认。
        if dict_metadata.get(str_key) in (None, "", [], {}):

            # 追加缺失元数据问题，供 planning 与 validation 统一消费。
            list_issues.append(
                _issue(
                    "error",
                    f"HLS profile violation: missing metadata field {str_key!r}.",
                )
            )

    # 返回元数据检查结果。
    return list_issues

# 按 pattern 类型做更具体的语义约束补充。
def _check_pattern_semantics(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """
    检查 pattern-specific 的语义合同。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :return: pattern 语义违例问题列表。
    """

    # pattern 名先归一化，避免大小写或连字符差异。
    str_pattern = canonical_pattern_name(profile)  # 当前 profile 的规范化 pattern 名

    # task_graph 需要显式 hls::task 和 pipeline 风格声明。
    if str_pattern == "task_graph":

        # 交给专门 helper 处理 task_graph 合同。
        return _check_task_graph_pattern(source_text)

    # rle_axis 需要显式 AXIS payload、TLAST 和 keep/strb 语义。
    if str_pattern == "rle_axis":

        # 交给专门 helper 处理 AXIS 编解码合同。
        return _check_rle_axis_pattern(source_text)

    # fft / cordic 在声明 error_tolerance 时要体现 tolerance 检查。
    if str_pattern in {"fft", "cordic"}:

        # 交给 tolerance helper 处理频域和 CORDIC 的误差约束。
        return _check_tolerance_pattern(profile, source_text, str_pattern)

    # 其他 pattern 暂无额外静态语义检查。
    return []

# task_graph 的 profile 必须显式体现 hls::task 与 pipeline 风格。
def _check_task_graph_pattern(source_text: str) -> list[dict[str, Any]]:
    """
    检查 task_graph pattern 的特定语义合同。

    :param source_text: 聚合后的源码文本。
    :return: task_graph 相关问题列表。
    """

    # task_graph 相关问题单独收集，便于局部返回。
    list_issues: list[dict[str, Any]] = []  # task_graph pattern 的问题列表

    # task_graph 必须显式实例化 hls::task。
    if "hls::task" not in source_text:

        # 未出现 hls::task 说明任务图语义没有真正落地。
        list_issues.append(
            _issue(
                "error",
                "HLS profile violation: task_graph pattern must instantiate hls::task explicitly.",
            )
        )

    # task actor 需要 flushing 或 free-running pipeline 风格。
    if "style=flp" not in source_text and "style=frp" not in source_text:

        # 缺少 style=flp/frp 会让任务 actor 的运行语义不明确。
        list_issues.append(
            _issue(
                "error",
                (
                    "HLS profile violation: task_graph task actors must use a flushing "
                    "or free-running pipeline style."
                ),
            )
        )

    # 返回 task_graph pattern 检查结果。
    return list_issues

# rle_axis 需要明确 AXIS payload 结构与 TLAST/keep/strb 传递。
def _check_rle_axis_pattern(source_text: str) -> list[dict[str, Any]]:
    """
    检查 rle_axis pattern 的特定语义合同。

    :param source_text: 聚合后的源码文本。
    :return: rle_axis 相关问题列表。
    """

    # rle_axis 检查会同时跟踪 AXIS 包结构、TLAST 语义和 testbench 线索。
    list_issues: list[dict[str, Any]] = []  # rle_axis 约束命中的静态诊断集合

    # 小写副本用于检测 tlast 这类大小写不敏感片段。
    str_lowered_source_text = source_text.lower()  # 小写化后的源码文本

    # 允许自定义 axis_byte_t/axis_word_t 结构替代直接的 ap_axiu。
    bool_has_axis_packet_struct = (
        "axis_byte_t" in source_text and "axis_word_t" in source_text  # 两个自定义 AXIS 包结构名都已出现
    )  # 是否存在自定义 AXIS packet 结构

    # ap_axiu 或等价 packet 结构至少要出现一种。
    if "ap_axiu<" not in source_text and not bool_has_axis_packet_struct:

        # 缺少 AXIS payload 类型时无法证明流接口语义。
        list_issues.append(
            _issue(
                "error",
                "HLS profile violation: rle_axis pattern must use ap_axiu-based AXI-Stream payload types.",
            )
        )

    # TLAST 传递必须显式可见，避免帧边界语义丢失。
    if ".last" not in source_text and "tlast" not in str_lowered_source_text:

        # 缺少 TLAST 语义时无法证明帧边界完整传递。
        list_issues.append(
            _issue(
                "error",
                "HLS profile violation: rle_axis pattern must model TLAST propagation explicitly.",
            )
        )

    # keep/strb 字段也必须被显式初始化。
    if ".keep" not in source_text or ".strb" not in source_text:

        # 缺少 keep/strb 初始化时 AXIS payload 语义不完整。
        list_issues.append(
            _issue(
                "error",
                "HLS profile violation: rle_axis pattern must initialize AXIS keep/strb fields explicitly.",
            )
        )

    # 把 rle_axis 专属约束命中结果交回给上层 profile 校验。
    return list_issues

# fft / cordic 在声明误差预算时应出现 tolerance 相关检查痕迹。
def _check_tolerance_pattern(
    profile: dict[str, Any],
    source_text: str,
    pattern: str,
) -> list[dict[str, Any]]:
    """
    检查 fft 或 cordic pattern 的误差容差语义。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :param pattern: 当前规范化后的 pattern 名。
    :return: tolerance 相关问题列表。
    """

    # tolerance 规则只在 profile 显式声明误差预算时累计诊断。
    list_issues: list[dict[str, Any]] = []  # tolerance 误差预算相关的静态诊断集合

    # metadata 不是字典时按空对象处理，保持行为稳定。
    dict_metadata = (
        profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}  # 非字典 metadata 退回空对象避免模式判断崩溃
    )  # 当前 profile 的元数据对象

    # 只有显式声明 error_tolerance 时，才要求代码或 testbench 提及 tolerance。
    if dict_metadata.get("error_tolerance") not in (None, "", [], {}):

        # 将源码标准化成小写副本，专门服务 tolerance/tol 关键字存在性判定。
        str_lowered_source_text = source_text.lower()  # 供 tolerance 语义探测使用的小写源码副本

        # 代码与 testbench 都没有 tolerance 痕迹时才报错。
        if "tolerance" not in str_lowered_source_text and "tol" not in str_lowered_source_text:

            # 缺少 tolerance 语义时很难证明误差预算被真正落实。
            list_issues.append(
                _issue(
                    "error",
                    (
                        f"HLS profile violation: {pattern} pattern must mention an explicit "
                        "tolerance check in code or testbench text."
                    ),
                )
            )

    # 将 tolerance 相关命中收束成一份结果列表交还给调用方。
    return list_issues

# required_pattern_headers 把 pattern 侧要求的高级头文件集中暴露给这里。
def _check_headers(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """
    检查 profile 要求的头文件是否已出现在源码中。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :return: 缺失必需头文件时生成的问题列表。
    """

    # pattern 规则会给出当前 profile 必需的头文件列表。
    list_required_headers = required_pattern_headers(profile)  # 当前 profile 要求的头文件列表

    # 头文件缺失都折叠成统一 issue 结构。
    list_issues: list[dict[str, Any]] = []  # 必需头文件检查产生的问题列表

    # 逐个检查角括号 include 和引号 include 两种形式。
    for str_header in list_required_headers:

        # 只要两种 include 形式都没出现，就认为头文件缺失。
        if (
            f"#include <{str_header}>" not in source_text
            and f'#include "{str_header}"' not in source_text
        ):

            # 错误消息保留头文件名，方便用户快速补齐。
            list_issues.append(
                _issue(
                    "error",
                    f"HLS profile violation: required header {str_header!r} was not found.",
                )
            )

    # 返回必需头文件检查结果。
    return list_issues

# allowed_libraries 负责限制高级 HLS 头文件的白名单范围。
def _check_allowed_libraries(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """
    检查高级 HLS 头文件是否落在 profile 允许范围内。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :return: 命中未授权高级头文件时生成的问题列表。
    """

    # 允许库集合先从 profile 读取，再并入 pattern 强制头文件。
    set_allowed_headers = {
        str(obj_header)  # 转成字符串后的显式允许头文件名
        for obj_header in profile.get("allowed_libraries", []) or []  # 遍历 profile 声明的 allowed_libraries
    }  # profile 显式允许的高级头文件集合

    # pattern 强制要求的头文件也应自动视为允许。
    set_required_headers = set(required_pattern_headers(profile))  # 当前 pattern 强制要求的头文件集合

    # 合并白名单和强制依赖集合。
    set_allowed_headers.update(set_required_headers)

    # 没有任何高级头文件约束时直接放行。
    if not set_allowed_headers:

        # 空白名单表示当前 profile 不限制高级头文件。
        return []

    # 未授权高级头文件都折叠成统一 issue 列表。
    list_issues: list[dict[str, Any]] = []  # 高级头文件白名单检查问题列表

    # ADVANCED_LIBRARY_HEADERS 提供所有受控高级头文件全集。
    for str_header in ADVANCED_LIBRARY_HEADERS:

        # 未 include 当前头文件时无需继续做白名单校验。
        if (
            f"#include <{str_header}>" not in source_text
            and f'#include "{str_header}"' not in source_text
        ):

            # 没有引用当前高级头文件时直接跳过。
            continue

        # 被 include 但不在允许集合里时追加错误。
        if str_header not in set_allowed_headers:

            # 保留头文件名，方便定位违规依赖。
            list_issues.append(
                _issue(
                    "error",
                    f"HLS profile violation: advanced header {str_header!r} is not allowed for this pattern.",
                )
            )

    # 返回高级头文件白名单检查结果。
    return list_issues

# require_static_arrays 用于卡住由变量控制大小的栈数组。
def _check_static_arrays(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """
    检查源码中是否出现未定长栈数组。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :return: 命中未定长栈数组时生成的问题列表。
    """

    # profile 显式放宽时才跳过 static_bound 约束。
    if (
        not profile.get("require_static_arrays", True)
        and profile.get("static_memory_rule") != "static_bound"
    ):

        # 当前 profile 明确允许跳过静态数组检查。
        return []

    # 变量控制的栈数组是 HLS 常见不稳定写法，这里直接正则拦截。
    if re.search(
        r"\b[A-Za-z_][A-Za-z0-9_:<>]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\[[A-Za-z_][A-Za-z0-9_]*\]\s*;",
        source_text,
    ):

        # 命中未定长栈数组时直接返回单个错误项。
        return [
            _issue(
                "error",
                "HLS profile violation: dynamic stack array was found; use static bounds.",
            )
        ]

    # 未发现未定长栈数组时返回空列表。
    return []

# forbidden_combinations 用于声明 pattern 内部互斥的 pragma 或结构。
def _check_forbidden_combinations(profile: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    """
    检查 profile 声明的禁止组合是否同时出现。

    :param profile: 当前 hls_profile 字典。
    :param source_text: 聚合后的源码文本。
    :return: 命中禁止组合时生成的问题列表。
    """

    # 禁止组合问题统一折叠到一个列表里返回。
    list_issues: list[dict[str, Any]] = []  # 禁止组合检查产生的问题列表

    # forbidden_combinations 支持多个 all_of 规则对象。
    for obj_rule in profile.get("forbidden_combinations", []) or []:

        # 只有对象规则才有 all_of 和 message 结构。
        if not isinstance(obj_rule, dict):

            # 非对象条目无法安全解析，直接跳过。
            continue

        # all_of 中的非空 marker 都统一转成字符串。
        list_markers = [
            str(obj_marker)  # 当前禁止组合规则里的单个 marker
            for obj_marker in obj_rule.get("all_of", []) or []  # 顺序读取 all_of 条目
            if str(obj_marker)  # 跳过空字符串和空值 marker
        ]  # 当前规则要求同时命中的 marker 列表

        # 只有 marker 全部命中时，才认为触发了禁止组合。
        if list_markers and all(str_marker in source_text for str_marker in list_markers):

            # 优先使用规则自带 message，缺失时回退默认消息。
            list_issues.append(
                _issue(
                    "error",
                    str(
                        obj_rule.get("message")
                        or "HLS profile violation: forbidden combination was found."
                    ),
                )
            )

    # 返回禁止组合检查结果。
    return list_issues

# cfg 校验负责对 syn.file 和 required_cfg_entries 做最小静态收口。
def _check_cfg(profile: dict[str, Any], cfg_text: str) -> list[dict[str, Any]]:
    """
    检查 cfg 文本是否满足 profile 要求。

    :param profile: 当前 hls_profile 字典。
    :param cfg_text: 聚合后的 cfg 文本。
    :return: cfg 相关问题列表。
    """

    # cfg 规则需要同时承接 syn.file、额外条目和 pattern 约束诊断。
    list_issues: list[dict[str, Any]] = []  # cfg 合同校验命中的静态诊断集合

    # profile 未关闭 syn.file 合同时，必须在 cfg 中看到 syn.file 条目。
    if profile.get("require_syn_file", True):

        # 缺失 syn.file 时说明 cfg 不能完整驱动 HLS 工程。
        if not re.search(r"(?m)^\s*syn\.file\s*=", cfg_text):

            # 追加 cfg 缺少 syn.file 的错误项。
            list_issues.append(
                _issue(
                    "error",
                    "HLS profile violation: cfg is missing syn.file.",
                )
            )

    # required_cfg_entries 支持声明额外必须出现的 cfg 片段。
    for obj_entry in profile.get("required_cfg_entries", []) or []:

        # 配置片段统一转字符串，兼容上游传入数字等类型。
        str_cfg_token = str(obj_entry)  # 当前必需 cfg 片段

        # 非空 token 且未出现在 cfg 中时追加错误。
        if str_cfg_token and str_cfg_token not in cfg_text:

            # 错误消息保留缺失 token，方便快速补齐。
            list_issues.append(
                _issue(
                    "error",
                    f"HLS profile violation: cfg is missing required entry {str_cfg_token!r}.",
                )
            )

    # 将 cfg 通道累积的配置违例返回给 profile 总校验。
    return list_issues

# INTERFACE pragma 既支持 mode=...，也支持紧跟在 INTERFACE 后面的模式名。
def _pragma_mode(line: str) -> str:
    """
    从单行 INTERFACE pragma 中提取 mode。

    :param line: 单行 pragma 文本。
    :return: 解析出的 mode；未命中时返回空字符串。
    """

    # 优先解析显式的 mode=... 形式。
    str_mode = _pragma_value(line, "mode")  # mode=... 形式解析出的值

    # 解析到显式 mode 时直接返回。
    if str_mode:

        # 保留原始 mode 文本，供白名单校验使用。
        return str_mode

    # 回退解析紧随 INTERFACE 的第一个标识符。
    list_mode_matches = re.findall(  # INTERFACE pragma 紧随模式名的候选列表
        r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z0-9_]+)",  # 匹配紧随 INTERFACE 的模式标识符
        line,  # 待回退解析模式名的 INTERFACE pragma 原文
    )

    # 命中时返回首个候选模式，否则回退空字符串。
    return list_mode_matches[0] if list_mode_matches else ""

# pragma 键值解析供 mode、port 等字段复用。
def _pragma_value(line: str, key: str) -> str:
    """
    从 pragma 行中提取 `key=value` 形式的值。

    :param line: 单行 pragma 文本。
    :param key: 需要提取的键名。
    :return: 匹配到的值；未命中时返回空字符串。
    """

    # 使用转义后的键名，避免特殊字符破坏正则。
    list_value_matches = re.findall(  # key=value 形式提取出的候选值列表
        rf"\b{re.escape(key)}\s*=\s*([A-Za-z0-9_]+)",  # 匹配 key=value 里的值片段
        line,  # 待提取 key=value 片段的 pragma 原文
    )

    # 命中时返回首个值，否则回退空字符串。
    return list_value_matches[0] if list_value_matches else ""

# 修复提示词只需要 HLS profile 直接相关的问题，其他 validation 噪声要裁掉。
def _profile_related_issues(validation_json: dict[str, Any]) -> list[dict[str, Any]]:
    """
    从 validation 报告中过滤出和 HLS profile 直接相关的问题。

    :param validation_json: 完整 validation 报告字典。
    :return: 仅保留 profile 相关 issue 的列表。
    """

    # 非字典输入视为没有 issue 列表，保持调用端稳健。
    list_raw_issues = (
        validation_json.get("issues", []) if isinstance(validation_json, dict) else []  # 非字典 validation 输入视为没有 issue 列表
    )  # validation 报告中的原始 issue 列表

    # 过滤后结果单独保存，供 prompt 序列化使用。
    list_selected_issues: list[dict[str, Any]] = []  # 和 profile 直接相关的 issue 列表

    # 逐条检查 issue 文本是否提到 profile 修复关注点。
    for obj_issue in list_raw_issues or []:

        # dict issue 保留 JSON 结构；其他值退化成字符串检查。
        str_issue_text = (
            json.dumps(obj_issue, ensure_ascii=False).lower()  # dict issue 序列化后转成小写文本
            if isinstance(obj_issue, dict)  # dict issue 保留结构化字段语义
            else str(obj_issue).lower()  # 其他 issue 值退化成普通小写字符串
        )  # 当前 issue 的小写文本表示

        # 只保留会影响 HLS profile 修复决策的关键字。
        if (
            "hls profile" in str_issue_text
            or "pragma" in str_issue_text
            or "syn.file" in str_issue_text
            or "std::vector" in str_issue_text
            or "dynamic" in str_issue_text
        ):

            # 保持原始 dict issue；非 dict issue 则包装成 message 对象。
            list_selected_issues.append(
                obj_issue if isinstance(obj_issue, dict) else {"message": str(obj_issue)}
            )

    # 返回裁剪后的 profile 相关 issue。
    return list_selected_issues

# 源码扫描会聚合所有 C/C++ 和头文件文本，供 profile 规则统一匹配。
def _source_text(root: Path) -> str:
    """
    聚合 root 下所有 HLS 源码与头文件文本。

    :param root: 待扫描目录。
    :return: 以换行拼接后的聚合源码文本。
    """

    # 聚合文本先按文件顺序累积到列表，再统一 join。
    list_texts: list[str] = []  # 聚合后的源码片段列表

    # 根据源码后缀动态生成递归 glob 模式。
    for str_pattern in _glob_patterns_from_suffixes(SOURCE_FILE_SUFFIXES):

        # 每种后缀都按排序后的路径顺序读取，保持结果稳定。
        for path_source in sorted(root.glob(str_pattern)):

            # 单个文件读取失败时忽略非法字符，避免 profile 校验被编码噪声阻断。
            list_texts.append(
                path_source.read_text(encoding="utf-8", errors="ignore")
            )

    # 返回所有源码文本的换行拼接结果。
    return "\n".join(list_texts)

# cfg 聚合同样采用稳定排序，避免结果随文件系统顺序抖动。
def _cfg_text(root: Path) -> str:
    """
    聚合 root 下所有 cfg 文件文本。

    :param root: 待扫描目录。
    :return: 以换行拼接后的聚合 cfg 文本。
    """

    # cfg 文本片段先累积到列表，再统一 join。
    list_cfg_texts: list[str] = []  # 聚合后的 cfg 文本片段列表

    # cfg 文件模式也按后缀动态生成，避免硬编码路径片段。
    for str_pattern in _glob_patterns_from_suffixes(CFG_FILE_SUFFIXES):

        # 按稳定排序顺序读取所有 cfg 文件。
        for path_cfg in sorted(root.glob(str_pattern)):

            # 忽略非法字符，避免个别 cfg 编码问题阻断整体校验。
            list_cfg_texts.append(path_cfg.read_text(encoding="utf-8", errors="ignore"))

    # 返回所有 cfg 文本的换行拼接结果。
    return "\n".join(list_cfg_texts)

# issue 结构统一由这里构造，避免不同 helper 的字段布局漂移。
def _issue(severity: str, message: str) -> dict[str, Any]:
    """
    构造统一的静态校验问题对象。

    :param severity: 问题严重级别。
    :param message: 面向用户的错误消息文本。
    :return: 标准化的 issue 字典。
    """

    # 返回统一的静态 issue 结构，供 validation 与 prompt 共享。
    return {
        "severity": severity,
        "message": message,
        "stage": "static",
        "source": "current_module_issue",
    }
