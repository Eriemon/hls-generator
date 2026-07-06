"""校验相邻 HLS 生成阶段之间的接口、测试向量和语义契约。"""
# 启用延迟注解，避免运行时解析嵌套字典类型。
from __future__ import annotations
# 标准库用于规整 C/C++ 接口类型文本。
import re
from typing import Any
# planning 入口负责把旧版 spec 补齐为 verifier 可读取的计划结构。
from scripts.python.generation.planning import decompose_spec

# verifier 输出的错误源按优先级映射到 workflow 的下一步动作。
ACTION_BY_ERROR_SOURCE = (
    ("spec_issue", "revise_plan"),  # 规格问题回到规划阶段
    ("dependency_issue", "fix_dependency"),  # 依赖问题优先修复依赖
    ("testbench_issue", "fix_testbench"),  # 测试台问题优先修复测试台
    ("current_module_issue", "regenerate_current"),  # 当前模块问题触发重生成
    ("insufficient_debug", "augment_tests"),  # 调试证据不足时补充测试
    ("toolchain_issue", "fix_toolchain"),  # 工具链问题先修复环境
    ("needs_human_intervention", "ask_human"),  # 需要人工决策时回退给用户
)

# 跨阶段校验入口，供 workflow 生成 verify_stage 报告。
def verify_stage(plan: dict[str, Any], from_contract: dict[str, Any], to_contract: dict[str, Any]) -> dict[str, Any]:
    """
    汇总相邻阶段的 HLS 契约漂移并返回 workflow 兼容报告。

    :param plan: 当前 HLS 计划或原始 spec，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :param from_contract: 上游阶段导出的契约，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :param to_contract: 下游阶段导出的契约，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :return: verify_stage 报告，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    """

    # 先补齐计划字段，保证后续接口比较读取同一结构。
    dict_normalized_plan: dict[str, Any] = decompose_spec(plan)  # 归一化 HLS 计划

    # 收集契约结构、接口漂移、测试向量和语义执行问题。
    list_issues: list[dict[str, Any]] = _stage_issues(dict_normalized_plan, from_contract, to_contract)  # verify_stage 问题列表

    # 语义摘要需要原样透出到报告顶层，供 workflow 和 trace 展示。
    dict_semantic_summary: dict[str, Any] = _semantic_summary(from_contract, to_contract)  # 语义执行摘要

    # 错误源顺序决定 recommended_action 的优先级。
    list_error_sources: list[str] = _error_sources(list_issues)  # 去重后的错误源列表

    # 只要存在 error 级别问题，当前阶段就不能进入 ready 状态。
    bool_ready: bool = not any(dict_issue.get("severity") == "error" for dict_issue in list_issues)  # 阶段就绪标志

    # 返回字段保持历史 JSON 契约，避免 workflow/report 消费方失配。
    return {
        "version": 1,
        "ready": bool_ready,
        "from": _contract_summary(from_contract),
        "to": _contract_summary(to_contract),
        "issues": list_issues,
        "error_sources": list_error_sources,
        "recommended_action": _recommended_action(list_error_sources),
        "semantic_ready": dict_semantic_summary.get("semantic_ready"),
        "mismatched_cases": dict_semantic_summary.get("mismatched_cases", []),
        "checkpoint_drift": dict_semantic_summary.get("checkpoint_drift", []),
        "failed_cases": dict_semantic_summary.get("failed_cases", []),
        "localization_confidence": dict_semantic_summary.get("localization_confidence"),
    }

# HLS 计划与下游接口契约的公开比较入口。
def plan_contract_interface_issues(plan: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    """
    检查计划声明的 HLS 顶层接口是否被下游契约保留。

    :param plan: 已归一化或可读取 interfaces 的 HLS 计划，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :param contract: 下游 HLS 接口契约，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :return: 接口漂移问题列表，shape=(n issues)，dtype=list[dict[str, Any]]，unit=JSON array。
    """

    # 非 HLS 契约不参与本模块的接口字段比较。
    if contract.get("target") != "hls":

        # 非 HLS 目标没有可比较的接口契约，直接返回空问题列表。
        return []

    # 从计划中提取期望的接口参数数组。
    list_plan_arguments = plan.get("interfaces", {}).get("arguments", [])  # 计划参数数组

    # 从下游契约中提取已经观测到的接口参数数组。
    list_contract_arguments = contract.get("arguments", [])  # 下游参数数组

    # 固定接口比对字段，避免在调用行重复展开字段元组。
    tuple_fields = ("type", "interface", "bundle")  # 接口比对字段

    # 用统一字段集合比较计划参数和下游参数的接口差异。
    list_issues = _exact_named_interface_issues(list_plan_arguments, list_contract_arguments, fields=tuple_fields)  # 接口差异问题

    # 顶层函数名必须在计划与接口审计结果之间保持一致。
    list_issues.extend(_top_function_issues(plan, contract))

    # control 协议漂移会破坏 Vitis HLS 顶层调用约定。
    list_issues.extend(_control_interface_issues(plan, contract))

    # 返回当前契约中的所有接口漂移问题。
    return list_issues

# 汇总 verify_stage 需要报告的所有问题来源。
def _stage_issues(
    plan: dict[str, Any],
    from_contract: dict[str, Any],
    to_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """收集 verify_stage 需要汇总的全部问题来源。

    参数:
        plan: 已归一化的 HLS 计划结构。
        from_contract: 上游阶段导出的结构化契约。
        to_contract: 下游阶段导出的结构化契约。

    返回:
        按历史报告顺序合并后的 verifier 问题列表。
    """

    # 这个聚合列表会原样写进 verify_stage["issues"]，所以追加顺序必须稳定。
    list_issues: list[dict[str, Any]] = []  # verify_stage 最终聚合结果容器

    # 上游契约自带问题先进入报告，便于定位源头。
    list_issues.extend(_contract_issues(from_contract, "from"))

    # 下游契约自带问题紧随其后，便于比较生成后状态。
    list_issues.extend(_contract_issues(to_contract, "to"))

    # 对比计划中的 HLS 接口声明和下游接口审计结果。
    list_issues.extend(plan_contract_interface_issues(plan, to_contract))

    # 检查参考测试用例和向量 hash 是否跨阶段保留。
    list_issues.extend(_check_cases_and_vectors(from_contract, to_contract))

    # 语义执行失败需要作为 current_module_issue 进入统一动作选择。
    list_issues.extend(_semantic_issues(_semantic_summary(from_contract, to_contract)))

    # 返回合并后的问题列表。
    return list_issues

# 将契约自带 issue 规范化为 verifier 报告 issue。
def _contract_issues(contract: dict[str, Any], side: str) -> list[dict[str, Any]]:
    """把契约自带问题转换为 verifier 统一格式。

    参数:
        contract: 当前阶段携带的结构化契约。
        side: 当前契约所在侧别，通常为 from 或 to。

    返回:
        可直接写入 verifier 报告的规范化问题列表。
    """

    # 契约 issue 可能来自审计器或上游阶段，先统一输出结构。
    list_issues: list[dict[str, Any]] = []  # 规范化后的契约问题列表

    # 逐项保留字典型 issue，忽略无法携带 message/source 的脏数据。
    for dict_issue in contract.get("issues", []) or []:

        # 非字典 issue 缺少稳定字段，不能进入 JSON 报告。
        if not isinstance(dict_issue, dict):

            # 脏数据缺少稳定字段，跳过后继续处理后续合法 issue。
            continue

        # 把契约侧别写入 message，保留原始 source 和 path。
        list_issues.append(
            {
                "severity": dict_issue.get("severity", "warning"),
                "source": dict_issue.get("source", "current_module_issue"),
                "message": f"{side} contract issue: {dict_issue.get('message', 'unspecified issue')}",
                "path": dict_issue.get("path"),
            }
        )

    # 返回当前契约侧的规范化问题。
    return list_issues

# 比较计划与契约中的命名参数集合和字段。
def _exact_named_interface_issues(
    expected_items: Any,
    observed_items: Any,
    *,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """比较同名 HLS 参数是否缺失、增多或字段漂移。

    参数:
        expected_items: 计划侧声明的接口项集合。
        observed_items: 下游契约侧观测到的接口项集合。
        fields: 需要逐项比较的接口字段名元组。

    返回:
        参数集合差异与字段漂移问题列表。
    """

    # 按 name 建立计划声明参数索引。
    dict_expected_by_name: dict[str, dict[str, Any]] = _items_by_name(expected_items)  # 计划参数索引

    # 按 name 建立下游接口审计参数索引。
    dict_observed_by_name: dict[str, dict[str, Any]] = _items_by_name(observed_items)  # 下游参数索引

    # 缺失或额外参数会导致顶层函数 ABI 不一致。
    list_issues: list[dict[str, Any]] = _named_item_membership_issues(dict_expected_by_name, dict_observed_by_name)  # 参数集合差异问题

    # 同名参数继续比较类型、接口模式和 bundle 字段。
    list_issues.extend(
        _named_item_field_issues(
            dict_expected_by_name,
            dict_observed_by_name,
            fields,
        )
    )

    # 返回参数集合与字段漂移问题。
    return list_issues

# 构造以 name 字段为键的接口项索引。
def _items_by_name(items: Any) -> dict[str, dict[str, Any]]:
    """过滤非字典接口项，并按参数名建立索引。

    参数:
        items: 待清洗的接口项序列或任意脏数据。

    返回:
        以 name 字段为键、接口项字典为值的索引。
    """

    # 接口审计输入可能为 None、列表或脏数据，统一输出字典索引。
    dict_items_by_name: dict[str, dict[str, Any]] = {}  # name 到接口项的映射

    # 逐项读取接口项，只保留带 name 的字典。
    for dict_item in items or []:

        # 非字典项没有稳定字段，不能参与接口比较。
        if not isinstance(dict_item, dict):

            # 非字典输入无法提供 name/type 等字段，直接跳过该接口项。
            continue

        # name 是 HLS 顶层参数的唯一比较锚点。
        str_name: str = str(dict_item.get("name") or "")  # 接口项名称

        # 空名称无法建立可靠索引。
        if not str_name:

            # 缺少参数名时无法建立稳定索引，忽略该接口项避免误报。
            continue

        # 保留最后一次同名记录，沿用字典推导式的历史行为。
        dict_items_by_name[str_name] = dict_item  # 已索引接口项

    # 返回可供集合和字段比较的接口索引。
    return dict_items_by_name

# 检查计划声明参数和下游参数集合是否一致。
def _named_item_membership_issues(
    expected_by_name: dict[str, dict[str, Any]],
    observed_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成缺失参数和额外参数的问题。

    参数:
        expected_by_name: 计划侧按名称索引的接口项。
        observed_by_name: 下游契约侧按名称索引的接口项。

    返回:
        参数集合缺失或新增对应的问题列表。
    """

    # 当前 helper 只处理参数集合差异。
    list_issues: list[dict[str, Any]] = []  # 参数集合问题列表

    # 找出计划声明但下游契约缺失的参数名。
    list_missing_names: list[str] = [str_name for str_name in expected_by_name if str_name not in observed_by_name]  # 缺失参数名

    # 缺失参数会导致测试或调用端无法匹配顶层接口。
    if list_missing_names:

        # 报告缺失的声明参数集合。
        list_issues.append(
            _issue(
                "error",
                "current_module_issue",
                "HLS argument contract is missing declared entries: " + ", ".join(list_missing_names) + ".",
            )
        )

    # 找出下游契约新增但计划未声明的参数名。
    list_unexpected_names: list[str] = [str_name for str_name in observed_by_name if str_name not in expected_by_name]  # 额外参数名

    # 额外参数说明生成接口偏离计划。
    if list_unexpected_names:

        # 报告下游契约新增的参数集合。
        list_issues.append(
            _issue(
                "error",
                "current_module_issue",
                "HLS argument contract added undeclared entries: " + ", ".join(list_unexpected_names) + ".",
            )
        )

    # 返回参数集合差异。
    return list_issues

# 检查同名 HLS 参数的关键字段是否保持一致。
def _named_item_field_issues(
    expected_by_name: dict[str, dict[str, Any]],
    observed_by_name: dict[str, dict[str, Any]],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """生成同名参数字段缺失或漂移的问题。

    参数:
        expected_by_name: 计划侧按名称索引的接口项。
        observed_by_name: 下游契约侧按名称索引的接口项。
        fields: 需要比较的字段名元组。

    返回:
        同名参数的字段缺失或字段漂移问题列表。
    """

    # 当前 helper 只处理同名参数字段比较。
    list_issues: list[dict[str, Any]] = []  # 参数字段问题列表

    # 只遍历计划声明的参数，缺失项已由集合比较报告。
    for str_name, dict_expected_item in expected_by_name.items():

        # 获取同名下游参数；缺失时无需重复报告。
        dict_observed_item: dict[str, Any] | None = observed_by_name.get(str_name)  # 下游同名参数

        # 缺失参数已经在 membership helper 中报告。
        if not dict_observed_item:

            # 同名参数不存在时已由集合差异登记，这里跳过重复字段比较。
            continue

        # 将当前参数的所有字段差异追加到问题列表。
        list_issues.extend(
            _single_item_field_issues(
                str_name,
                dict_expected_item,
                dict_observed_item,
                fields,
            )
        )

    # 返回所有同名参数字段问题。
    return list_issues

# 检查单个参数在指定字段上的漂移。
def _single_item_field_issues(
    name: str,
    expected_item: dict[str, Any],
    observed_item: dict[str, Any],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """比较单个 HLS 参数在关键字段上的漂移。

    参数:
        name: 当前比较的参数名称。
        expected_item: 计划侧该参数的接口定义。
        observed_item: 下游契约侧该参数的接口定义。
        fields: 需要比较的字段名元组。

    返回:
        当前参数对应的字段缺失或漂移问题列表。
    """

    # 当前参数的字段漂移问题按 fields 顺序输出。
    list_issues: list[dict[str, Any]] = []  # 单参数字段问题列表

    # 逐字段比较已经规范化后的文本值。
    for str_field in fields:

        # 计划侧字段值用于判断该字段是否为必需字段。
        str_expected_value: str = _normalized_interface_field(expected_item.get(str_field), str_field)  # 计划字段值

        # 下游侧字段值用于检测缺失或漂移。
        str_observed_value: str = _normalized_interface_field(observed_item.get(str_field), str_field)  # 下游字段值

        # 计划声明了字段但下游没有携带，属于接口契约缺失。
        if str_expected_value and not str_observed_value:

            # 报告字段缺失。
            list_issues.append(_missing_field_issue(name, str_field))

        # 两侧均声明但值不同，属于接口字段漂移。
        elif str_expected_value and str_observed_value and str_expected_value != str_observed_value:

            # 报告字段漂移。
            list_issues.append(_drifted_field_issue(name, str_field, str_expected_value, str_observed_value))

    # 返回当前参数的字段问题。
    return list_issues

# 构造字段缺失问题。
def _missing_field_issue(name: str, field: str) -> dict[str, Any]:
    """生成下游契约缺少必需参数字段的问题。

    参数:
        name: 缺少字段的 HLS 参数名称。
        field: 当前缺失的字段名称。

    返回:
        一条描述字段缺失的 verifier 问题。
    """

    # message 保持历史英文文本，避免测试或外部解析器受中文文案影响。
    str_message: str = f"HLS argument {name!r} is missing required field {field!r} in the downstream contract."  # 字段缺失消息

    # 统一通过 issue helper 生成字段缺失问题对象。
    return _issue("error", "current_module_issue", str_message)

# 构造字段漂移问题。
def _drifted_field_issue(name: str, field: str, expected_value: str, observed_value: str) -> dict[str, Any]:
    """生成下游契约参数字段值漂移的问题。

    参数:
        name: 发生漂移的 HLS 参数名称。
        field: 当前比较的字段名称。
        expected_value: 计划侧规范化后的字段值。
        observed_value: 下游契约侧规范化后的字段值。

    返回:
        一条描述字段值漂移的 verifier 问题。
    """

    # message 保持历史英文文本，避免改变报告协议。
    str_message: str = f"HLS argument {name!r} {field} drifted from {expected_value!r} to {observed_value!r}."  # 字段漂移消息

    # 统一通过 issue helper 生成字段漂移问题对象。
    return _issue("error", "current_module_issue", str_message)

# 规范化接口字段，避免空白和大小写差异造成误报。
def _normalized_interface_field(value: Any, field: str) -> str:
    """把接口字段转换为可比较的稳定文本。

    参数:
        value: 原始接口字段值。
        field: 当前字段名称，用于决定规整策略。

    返回:
        可直接参与字符串比较的规范化字段文本。
    """

    # 空值统一视为未声明字段。
    if value in (None, ""):

        # 缺失字段在字符串比较阶段统一视为空字符串。
        return ""

    # C/C++ 类型字段需要压缩空白，并规整指针和引用符号周围空格。
    if field == "type":

        # 先压缩普通空白，保留类型 token 顺序。
        str_canonical_type: str = re.sub(r"\s+", " ", str(value)).strip()  # 压缩空白后的类型文本

        # 再移除 * 和 & 周围空白，兼容 HLS 审计器不同打印风格。
        return re.sub(r"\s*([*&])\s*", r"\1", str_canonical_type)

    # 接口模式和 control mode 不区分大小写。
    if field in {"interface", "control_mode"}:

        # 返回小写后的接口模式文本。
        return str(value).strip().lower()

    # 其他字段只去除首尾空白。
    return str(value).strip()

# 检查顶层函数名是否在计划和契约中保持一致。
def _top_function_issues(plan: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    """生成 HLS top function 漂移问题。

    参数:
        plan: 当前阶段的 HLS 计划结构。
        contract: 下游阶段导出的接口契约。

    返回:
        顶层函数名缺失或漂移对应的问题列表。
    """

    # 计划优先使用 interfaces.top_function，缺失时退回 name。
    str_expected_top: str = str(plan.get("interfaces", {}).get("top_function") or plan.get("name") or "")  # 计划顶层函数名

    # 空计划名无法比较，保持历史宽松行为。
    if not str_expected_top:

        # 计划未给出顶层函数名时无法形成稳定比较，直接返回空问题列表。
        return []

    # 下游契约中的 top 字段来自接口审计结果。
    str_observed_top: str | None = contract.get("top")  # 下游顶层函数名

    # 顶层函数名一致时无需报告。
    if str_observed_top == str_expected_top:

        # 顶层函数名完全一致时不需要额外记录问题。
        return []

    # 保留历史英文消息文本，便于既有 trace 检索。
    str_message: str = f"HLS top mismatch: expected {str_expected_top!r}, observed {str_observed_top!r}."  # 顶层函数漂移消息

    # 返回单条 top 漂移问题。
    return [_issue("error", "current_module_issue", str_message)]

# 检查 HLS control 协议是否缺失或漂移。
def _control_interface_issues(plan: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    """生成 HLS control interface 缺失或漂移问题。

    参数:
        plan: 当前阶段的 HLS 计划结构。
        contract: 下游阶段导出的接口契约。

    返回:
        control 协议缺失或漂移对应的问题列表。
    """

    # 从计划接口声明中读取期望 control 协议。
    str_control_expected: str = str(plan.get("interfaces", {}).get("control") or "").strip().lower()  # 计划 control 协议

    # 计划未声明 control 时保持历史宽松行为。
    if not str_control_expected:

        # 未声明 control 协议时沿用历史宽松策略，不额外报错。
        return []

    # 从下游契约中读取实际 control 协议。
    str_control_observed: str = str(contract.get("control_mode") or "").strip().lower()  # 下游 control 协议

    # 下游缺少 control_mode 字段时报告缺失。
    if not str_control_observed:

        # 返回 control 缺失问题。
        return [
            _issue(
                "error",
                "current_module_issue",
                "HLS control interface is missing from the downstream contract.",
            )
        ]

    # 两侧 control 协议一致时通过。
    if str_control_expected == str_control_observed:

        # 计划与契约的 control 协议一致时不需要生成问题。
        return []

    # 保持英文漂移消息，便于复用既有 trace 和日志关键词检索。
    str_message: str = f"HLS control interface drifted from {str_control_expected!r} to {str_control_observed!r}."  # control 协议漂移消息

    # 统一通过 issue helper 返回 control 协议漂移问题。
    return [_issue("error", "current_module_issue", str_message)]

# 检查测试用例 ID 与参考向量 hash 是否跨阶段保留。
def _check_cases_and_vectors(from_contract: dict[str, Any], to_contract: dict[str, Any]) -> list[dict[str, Any]]:
    """生成测试用例和参考向量漂移问题。

    参数:
        from_contract: 上游阶段导出的测试与接口契约。
        to_contract: 下游阶段导出的测试与接口契约。

    返回:
        用例 ID、向量 hash 和证据缺失相关的问题列表。
    """

    # 读取上游参考用例 ID。
    list_from_cases: list[str] = _string_list(from_contract.get("case_ids", []))  # 上游用例 ID 列表

    # 读取下游参考用例 ID。
    list_to_cases: list[str] = _string_list(to_contract.get("case_ids", []))  # 下游用例 ID 列表

    # 读取上游参考向量 hash。
    list_from_hashes: list[str] = _string_list(from_contract.get("vector_hashes", []))  # 上游向量 hash 列表

    # 读取下游参考向量 hash。
    list_to_hashes: list[str] = _string_list(to_contract.get("vector_hashes", []))  # 下游向量 hash 列表

    # 分别检查 case id 和 vector hash，保持历史问题顺序。
    list_issues = _case_id_issues(list_from_cases, list_to_cases, list_from_hashes, list_to_hashes)  # 用例证据问题

    # hash 漂移问题在 case id 问题之后追加。
    list_issues.extend(_vector_hash_issues(list_from_hashes, list_to_hashes))

    # 返回测试契约问题。
    return list_issues

# 将任意列表字段转换为字符串列表。
def _string_list(value: Any) -> list[str]:
    """把契约中的序列字段转换为字符串列表。

    参数:
        value: 契约字段中的原始列表值、标量值或空值。

    返回:
        保持原有顺序的字符串列表表示。
    """

    # None 或空值视为空列表。
    if not value:

        # 缺少列表值时统一返回空列表，避免调用方再做空值分支。
        return []

    # 字符串属于单个标量，不按字符拆分。
    if isinstance(value, str):

        # 单个字符串字段要保留为单元素列表，不能按字符拆开。
        return [value]

    # 非可迭代对象按单个值处理。
    if not isinstance(value, list | tuple | set):

        # 返回单元素字符串列表。
        return [str(value)]

    # 逐项转换为字符串，保持原有顺序。
    return [str(obj_item) for obj_item in value]

# 检查 case id 证据是否完整。
def _case_id_issues(
    from_cases: list[str],
    to_cases: list[str],
    from_hashes: list[str],
    to_hashes: list[str],
) -> list[dict[str, Any]]:
    """生成 case id 缺失或无证据问题。

    参数:
        from_cases: 上游阶段保留的参考用例 ID 列表。
        to_cases: 下游阶段保留的参考用例 ID 列表。
        from_hashes: 上游阶段保留的参考向量 hash 列表。
        to_hashes: 下游阶段保留的参考向量 hash 列表。

    返回:
        用例 ID 丢失或测试证据不足对应的问题列表。
    """

    # 没有上游 case id 时，历史行为是不强制下游提供用例。
    if not from_cases:

        # 上游没有给出 case id 时，下游也不必额外补充该类证据。
        return []

    # 两侧都有 case id 时检查是否存在缺失。
    if to_cases:

        # 返回缺失 case id 问题或空列表。
        return _missing_case_issues(from_cases, to_cases)

    # 没有 case id 时，允许通过共享向量 hash 作为替代证据。
    bool_shared_vector_hash: bool = bool(from_hashes and to_hashes and set(from_hashes).intersection(to_hashes))  # 共享向量 hash 标志

    # 既没有 case id 也没有共享 hash，说明测试契约没有可追踪证据。
    if not bool_shared_vector_hash:

        # 返回缺少结构化测试证据的问题。
        return [
            _issue(
                "error",
                "testbench_issue",
                "HLS testbench has no structured vector contract or semantic transcript case evidence.",
            )
        ]

    # 共享 hash 可作为替代证据。
    return []

# 生成缺失 case id 的问题。
def _missing_case_issues(from_cases: list[str], to_cases: list[str]) -> list[dict[str, Any]]:
    """找出上游存在但下游缺失的测试用例 ID。

    参数:
        from_cases: 上游阶段保留的参考用例 ID 列表。
        to_cases: 下游阶段保留的参考用例 ID 列表。

    返回:
        缺失 case id 对应的问题列表。
    """

    # 保持上游 case 顺序，方便报告直接定位缺失项。
    list_missing_cases: list[str] = [str_case for str_case in from_cases if str_case not in to_cases]  # 缺失用例 ID

    # 所有 case id 都保留下来时不产生问题。
    if not list_missing_cases:

        # 所有参考用例都已保留时，不需要补登记缺失问题。
        return []

    # 拼接历史英文错误文本。
    str_message: str = "HLS testbench is missing reference vector case ids: " + ", ".join(list_missing_cases)  # 缺失用例消息

    # 返回单条缺失 case id 问题。
    return [_issue("error", "testbench_issue", str_message)]

# 检查参考向量 hash 是否漂移或丢失。
def _vector_hash_issues(from_hashes: list[str], to_hashes: list[str]) -> list[dict[str, Any]]:
    """生成参考向量 hash 漂移或缺失问题。

    参数:
        from_hashes: 上游阶段保留的参考向量 hash 列表。
        to_hashes: 下游阶段保留的参考向量 hash 列表。

    返回:
        参考向量 hash 丢失或漂移对应的问题列表。
    """

    # 没有上游 hash 时不强制下游携带 hash。
    if not from_hashes:

        # 上游未提供向量 hash 时，当前阶段无需强制保留该证据。
        return []

    # 上游有 hash 但下游没有，说明测试向量证据丢失。
    if not to_hashes:

        # 下游完全缺少向量 hash 时，立即返回证据丢失问题。
        return [_issue("error", "testbench_issue", "HLS testbench does not carry the reference vector hash.")]

    # 两侧 hash 均存在但没有交集，说明参考向量漂移。
    if not set(from_hashes).intersection(to_hashes):

        # 两侧 hash 没有任何交集时，直接返回向量漂移问题。
        return [_issue("error", "testbench_issue", "Reference vector hash drifted between stages.")]

    # 至少一个 hash 匹配即可认为向量证据延续。
    return []

# 提取语义执行摘要，供顶层报告和问题生成复用。
def _semantic_summary(from_contract: dict[str, Any], to_contract: dict[str, Any]) -> dict[str, Any]:
    """读取下游 metrics.semantic_execution 摘要。

    参数:
        from_contract: 上游阶段契约，当前仅保留接口以兼容未来扩展。
        to_contract: 下游阶段契约，语义摘要从该对象中读取。

    返回:
        只包含 verifier 历史报告字段的语义执行摘要字典。
    """

    # 当前语义摘要只来自下游执行报告，上游参数保留给未来双侧比较。
    del from_contract

    # metrics 必须是字典，否则视为没有语义执行信息。
    dict_metrics: dict[str, Any] = _dict_field(to_contract, "metrics")  # 下游 metrics 字段

    # semantic_execution 是语义执行器写入的结构化摘要。
    dict_semantic: dict[str, Any] = _dict_field(dict_metrics, "semantic_execution")  # 语义执行字段

    # 缺少语义执行摘要时保持旧行为，返回空字典。
    if not dict_semantic:

        # 没有语义执行数据时返回空摘要，保持非语义阶段的旧行为。
        return {}

    # 只透出 verify_stage 历史报告字段。
    return {
        "semantic_ready": dict_semantic.get("semantic_ready"),
        "mismatched_cases": dict_semantic.get("mismatched_cases", []),
        "checkpoint_drift": dict_semantic.get("checkpoint_drift", []),
        "failed_cases": dict_semantic.get("failed_cases", []),
        "localization_confidence": dict_semantic.get("localization_confidence"),
    }

# 安全读取字典字段。
def _dict_field(source: dict[str, Any], key: str) -> dict[str, Any]:
    """字段值为字典时返回原值，否则返回空字典。

    参数:
        source: 需要读取字段的源字典。
        key: 目标字段名称。

    返回:
        目标字段为字典时返回原值，否则返回空字典。
    """

    # 只有字典字段才适合后续结构化读取。
    if isinstance(source.get(key), dict):

        # 返回原始字典，保留所有下游字段。
        return source[key]

    # 非字典字段视为缺失。
    return {}

# 将语义执行摘要转换为 verifier 问题。
def _semantic_issues(semantic_summary: dict[str, Any]) -> list[dict[str, Any]]:
    """生成语义输出漂移和失败用例问题。

    参数:
        semantic_summary: `_semantic_summary` 产出的结构化语义摘要。

    返回:
        语义输出漂移与失败用例对应的问题列表。
    """

    # 缺少语义摘要时不产生问题。
    if not semantic_summary:

        # 当前阶段没有语义摘要时，不额外生成语义类问题。
        return []

    # 语义问题按 mismatched cases 和 failed cases 的顺序输出。
    list_issues: list[dict[str, Any]] = []  # 语义执行问题列表

    # 输出值不一致的 case 说明当前模块实现偏离参考行为。
    list_issues.extend(_mismatched_case_issues(semantic_summary.get("mismatched_cases", [])))

    # 语义 transcript 中显式失败的 case 也归入当前模块问题。
    list_issues.extend(_failed_case_issues(semantic_summary.get("failed_cases", [])))

    # 返回语义执行问题。
    return list_issues

# 生成语义输出漂移问题。
def _mismatched_case_issues(mismatched_cases: Any) -> list[dict[str, Any]]:
    """把 mismatched_cases 转换为 current_module_issue。

    参数:
        mismatched_cases: 语义执行摘要中的漂移用例列表或任意脏数据。

    返回:
        每个漂移用例对应的一条 current_module_issue 列表。
    """

    # 收集每个漂移 case 的问题。
    list_issues: list[dict[str, Any]] = []  # 语义输出漂移问题列表

    # 逐项保留 case_id，非字典项按 None 兼容历史行为。
    for obj_item in mismatched_cases or []:

        # 生成当前模块语义漂移问题。
        list_issues.append(
            _issue(
                "error",
                "current_module_issue",
                "Semantic output drift was detected across stages.",
                case_id=_mismatch_case_id(obj_item),
            )
        )

    # 返回所有语义输出漂移问题。
    return list_issues

# 提取 mismatched case 的 case_id 字段。
def _mismatch_case_id(item: Any) -> Any:
    """返回语义漂移项中的 case_id，非字典项返回 None。

    参数:
        item: 单个漂移用例对象或非结构化值。

    返回:
        结构化输入中的 case_id；无法提取时返回 None。
    """

    # 字典项携带 case_id，其他项无法定位具体用例。
    if isinstance(item, dict):

        # 保留原始 case_id 类型，避免改变报告协议。
        return item.get("case_id")

    # 非字典项没有结构化 case_id。
    return None

# 生成语义执行失败问题。
def _failed_case_issues(failed_cases: Any) -> list[dict[str, Any]]:
    """把 failed_cases 转换为 current_module_issue。

    参数:
        failed_cases: 语义执行摘要中的失败用例列表或任意脏数据。

    返回:
        每个失败用例对应的一条 current_module_issue 列表。
    """

    # 收集每个失败 case 的问题。
    list_issues: list[dict[str, Any]] = []  # 语义失败问题列表

    # 逐项把 case id 写入问题字段。
    for obj_case_id in failed_cases or []:

        # 生成当前模块语义失败问题。
        list_issues.append(
            _issue(
                "error",
                "current_module_issue",
                "Semantic transcript reported FAIL for a reference case.",
                case_id=obj_case_id,
            )
        )

    # 返回所有语义失败问题。
    return list_issues

# 从问题列表中提取去重后的 source。
def _error_sources(issues: list[dict[str, Any]]) -> list[str]:
    """按首次出现顺序提取问题 source。

    参数:
        issues: verifier 当前汇总的问题列表。

    返回:
        去重后保留首次出现顺序的错误源列表。
    """

    # 保持首次出现顺序，避免改变 recommended_action 选择。
    list_sources: list[str] = []  # 去重后的 source 列表

    # 逐项读取 source，缺失时归入 current_module_issue。
    for dict_issue in issues:

        # source 字段用于 workflow 决定下一步动作。
        str_source: str = str(dict_issue.get("source") or "current_module_issue")  # 问题来源

        # 保留首次出现的 source。
        if str_source not in list_sources:

            # 追加新的错误源。
            list_sources.append(str_source)

    # 返回去重后的 source 顺序。
    return list_sources

# 根据错误源选择 workflow 建议动作。
def _recommended_action(error_sources: list[str]) -> str:
    """返回第一个匹配错误源的推荐动作。

    参数:
        error_sources: 已去重且保留顺序的错误源列表。

    返回:
        workflow 可直接消费的推荐动作字符串。
    """

    # 按固定优先级遍历 source/action 对。
    for tuple_source_action in ACTION_BY_ERROR_SOURCE:

        # 拆出当前优先级的 source 和动作。
        str_source: str = tuple_source_action[0]  # 错误源名称

        # 动作值必须保持历史协议字符串。
        str_action: str = tuple_source_action[1]  # 推荐动作名称

        # 命中错误源时立即返回对应动作。
        if str_source in error_sources:

            # 返回 workflow 可识别的动作值。
            return str_action

    # 默认动作保持旧版行为：重新生成当前模块。
    return "regenerate_current"

# 构造契约摘要，限制 verify_stage 报告中的透出字段。
def _contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    """返回 verify_stage 顶层 from/to 字段使用的契约摘要。

    参数:
        contract: 当前阶段导出的完整契约对象。

    返回:
        仅保留顶层报告需要字段的契约摘要。
    """

    # 返回字段集合保持历史 JSON 契约。
    return {
        "target": contract.get("target"),
        "top": contract.get("top"),
        "interface_sha256": contract.get("interface_sha256"),
        "case_ids": contract.get("case_ids", []),
        "vector_hashes": contract.get("vector_hashes", []),
    }

# 构造统一结构的问题字典。
def _issue(severity: str, source: str, message: str, **extra: Any) -> dict[str, Any]:
    """生成 verifier 报告中的 issue 对象。

    参数:
        severity: 问题级别，例如 error 或 warning。
        source: workflow 使用的问题来源标识。
        message: 需要保留给日志和报告的消息文本。
        extra: 额外透传到报告对象中的定位字段。

    返回:
        含基础字段和扩展字段的 verifier 问题对象。
    """

    # 基础字段保持所有 issue 共有结构。
    dict_issue: dict[str, Any] = {"severity": severity, "source": source, "message": message}  # verifier 问题对象

    # 额外字段用于 case_id、path 等定位信息。
    dict_issue.update(extra)

    # 返回完整 issue 对象。
    return dict_issue
