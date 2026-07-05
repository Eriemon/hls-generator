"""解析人工介入答复，并生成工作流可复用的决策记忆。"""

# 启用推迟求值的类型标注，保持运行时导入轻量。
from __future__ import annotations

# Any 用于接收 JSON object 中无法提前收窄的字段值。
from typing import Any

# 人工介入答复必须提供这些字段，缺失时保持统一错误路径。
REQUIRED_ANSWER_FIELDS = (
    "decision",  # 人工设计结论字段
    "evidence",  # 支撑人工结论的证据字段
    "constraints",  # 需要保留的人工约束字段
    "affected_subfunctions",  # 决策作用域字段
)  # 人工介入答复必填字段

# 将人工答复转换成决策对象和可追加的记忆对象。
def resolve_intervention(intervention: dict[str, Any], answer: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    校验人工答复并生成 workflow 后续阶段可读取的决策与记忆。

    参数:
        intervention: workflow 写出的 intervention JSON 对象。
        answer: 用户或上层系统提供的人工决策答复对象。

    返回:
        二元组，第一项是规范化决策对象，第二项是按子函数展开的记忆对象。
    """

    # 先校验答复结构，避免后续字段读取产生模糊 KeyError。
    _validate_answer(answer)

    # 受影响子函数为空时使用通配符，表示决策应用于整个 HLS 任务。
    list_affected_subfunctions = _affected_subfunctions(answer)  # 决策覆盖的子函数名称列表

    # 决策对象保留原始问题来源，便于审计人工答复对应的阻塞点。
    dict_decision = _build_decision_payload(  # 规范化人工决策对象
        intervention,  # 原始人工介入请求
        answer,  # 当前人工答复对象
        list_affected_subfunctions,  # 当前决策覆盖的子函数列表
    )

    # 记忆对象按子函数展开，供后续 attempt 自动复用人工约束。
    dict_memory = _build_memory_payload(  # 可写入 workflow memory 的决策条目
        dict_decision,  # 规范化后的人工决策对象
        list_affected_subfunctions,  # 需要写入记忆的子函数列表
    )

    # workflow 调用端需要把 decision 和 memory 分别写入不同状态文件。
    return dict_decision, dict_memory

# 判断已有人工决策是否覆盖当前子函数。
def decision_applies(decision: dict[str, Any] | None, subfunction: str | None) -> bool:
    """
    检查某个决策是否应该应用到指定子函数。

    参数:
        decision: 可能为空的规范化人工决策对象。
        subfunction: 当前待检查的子函数名称；为空表示调用方没有更细粒度上下文。

    返回:
        True 表示决策覆盖该子函数或全局通配符，False 表示不能复用该决策。
    """

    # 没有决策对象时不能复用任何人工约束。
    if not decision:

        # 返回 False 让调用方继续走正常阻塞或询问流程。
        return False

    # affected_subfunctions 控制决策的作用域。
    list_affected_subfunctions = [  # 决策声明覆盖的子函数名称列表
        str(obj_item)  # 单个受影响子函数名称
        for obj_item in decision.get("affected_subfunctions", []) or []  # 决策中声明的作用域来源
    ]

    # 空列表和通配符都表示该决策对当前上下文有效。
    bool_global_decision = not list_affected_subfunctions or "*" in list_affected_subfunctions  # 决策是否覆盖整个任务

    # 调用方没有子函数上下文时，只要存在决策作用域即可复用。
    bool_missing_subfunction_context = subfunction is None  # 调用方是否缺少子函数上下文

    # 具名子函数必须出现在受影响列表中才可复用。
    bool_named_subfunction_hit = str(subfunction) in list_affected_subfunctions  # 当前子函数是否被决策覆盖

    # 返回最终作用域判断结果。
    return bool_global_decision or bool_missing_subfunction_context or bool_named_subfunction_hit

# 构造规范化人工决策对象。
def _build_decision_payload(
    intervention: dict[str, Any],
    answer: dict[str, Any],
    list_affected_subfunctions: list[str],
) -> dict[str, Any]:
    """
    组装后续 workflow 读取的 decision JSON 对象。

    参数:
        intervention: 原始 intervention JSON 对象。
        answer: 已通过结构校验的人工答复对象。
        list_affected_subfunctions: 已规范化的子函数作用域列表。

    返回:
        包含决策正文、证据、约束和来源问题的字典。
    """

    # 来源对象只保留定位人工问题所需的最小字段。
    dict_source_intervention = _source_intervention_payload(intervention)  # 人工问题来源摘要

    # 答复证据保持列表形态，便于报告端逐条展示。
    list_evidence = _as_list(answer.get("evidence", []))  # 人工决策证据列表

    # 约束列表用于后续生成 memory constraint 文本。
    list_constraints = _as_list(answer.get("constraints", []))  # 人工决策约束列表

    # decision 字段是后续流程真正复用的人工设计结论。
    str_decision = str(answer["decision"])  # 人工确认的设计决策文本

    # 决策 payload 固定 version/status，方便下游兼容读取。
    dict_decision = {
        "version": 1,  # 决策对象结构版本
        "status": "resolved",  # 当前人工阻塞已经解决
        "decision": str_decision,  # 人工确认的设计结论
        "evidence": list_evidence,  # 支撑决策的证据列表
        "constraints": list_constraints,  # 需要持续保留的人工约束
        "affected_subfunctions": list_affected_subfunctions,  # 决策覆盖的子函数作用域
        "source_intervention": dict_source_intervention,  # 原始人工问题来源摘要
    }  # workflow 用来解除人工阻塞的 decision JSON

    # 返回规范化后的人工决策对象。
    return dict_decision

# 构造人工问题来源摘要。
def _source_intervention_payload(intervention: dict[str, Any]) -> dict[str, Any]:
    """
    提取 intervention 中用于追溯问题来源的字段。

    参数:
        intervention: workflow 产生的原始 intervention JSON 对象。

    返回:
        只包含 primary_source 和 question 的来源摘要字典。
    """

    # 返回最小来源摘要，避免把完整 intervention 复制进 decision。
    return {
        "primary_source": intervention.get("primary_source"),
        "question": intervention.get("question"),
    }

# 按受影响子函数生成 memory 条目。
def _build_memory_payload(dict_decision: dict[str, Any], list_affected_subfunctions: list[str]) -> dict[str, Any]:
    """
    将单个人工决策展开成 workflow memory JSON 对象。

    参数:
        dict_decision: 已规范化的人工决策对象。
        list_affected_subfunctions: 需要写入 memory 的子函数作用域列表。

    返回:
        含 version 和 entries 字段的 memory 对象。
    """

    # 每个子函数得到一条 memory，保证后续匹配逻辑无需解析通配输入。
    list_entries = [  # 按子函数展开的人工决策记忆条目
        _memory_entry(dict_decision, str_subfunction)  # 当前子函数的人工记忆条目
        for str_subfunction in list_affected_subfunctions  # 需要展开成记忆项的子函数名称
    ]

    # memory 根对象保持版本字段，便于未来兼容演进。
    dict_memory = {
        "version": 1,  # memory 对象结构版本
        "entries": list_entries,  # 按子函数展开的决策记忆列表
    }  # workflow 后续 attempt 读取的 memory JSON

    # 返回可直接落盘的 memory 对象。
    return dict_memory

# 构造单个子函数的 memory 条目。
def _memory_entry(dict_decision: dict[str, Any], str_subfunction: str) -> dict[str, Any]:
    """
    为一个子函数生成可复用的人工决策记忆条目。

    参数:
        dict_decision: 已规范化的人工决策对象。
        str_subfunction: 当前 memory 条目覆盖的子函数名称。

    返回:
        包含 stage、error_signature、constraint 和 decision 的 memory entry。
    """

    # constraint 文本合并决策和约束，供后续 prompt 或报告引用。
    str_constraint = _constraint_text(dict_decision)  # memory 中保存的人工约束文本

    # decision 字段保留原始结论，便于机器逻辑直接读取。
    str_decision = str(dict_decision["decision"])  # 人工设计决策原文

    # 返回单条 memory 记录。
    return {
        "subfunction": str_subfunction,
        "stage": "*",
        "error_signature": "human_decision",
        "constraint": str_constraint,
        "decision": str_decision,
    }

# 校验人工答复是否满足 workflow 约定。
def _validate_answer(answer: dict[str, Any]) -> None:
    """
    验证人工答复对象包含必填字段和非空决策。

    参数:
        answer: 用户或上层系统提供的人工决策答复对象。

    返回:
        无业务返回值；校验失败时抛出 ValueError。
    """

    # 人工答复必须是 JSON object，其他类型不能安全读取字段。
    if not isinstance(answer, dict):

        # 报告固定前缀错误，满足当前项目异常文本规范。
        raise ValueError("> ERR: [Python] Human intervention answer must be a JSON object.")

    # 只检查缺失字段，字段内容合法性由后续分支分别处理。
    list_missing_fields = [  # 人工答复缺失的必填字段
        str_field  # 当前缺失的必填字段名
        for str_field in REQUIRED_ANSWER_FIELDS  # 逐个检查所有必填字段
        if str_field not in answer  # 当前字段尚未出现在人工答复中
    ]

    # 缺失任一必填字段时阻止生成不完整 decision。
    if list_missing_fields:

        # 缺失字段列表直接拼入错误文本，便于调用方修复输入。
        raise ValueError(
            "> ERR: [Python] Human intervention answer is missing required fields: "
            + ", ".join(list_missing_fields)
        )

    # decision 必须有实际文本，否则 memory 无法表达设计结论。
    if not str(answer.get("decision", "")).strip():

        # 空决策会让后续 workflow 误以为阻塞已经解决。
        raise ValueError("> ERR: [Python] Human intervention answer decision must not be empty.")

# 把 answer 中的作用域字段规整为子函数名称列表。
def _affected_subfunctions(answer: dict[str, Any]) -> list[str]:
    """
    从人工答复中提取受影响子函数列表。

    参数:
        answer: 已通过结构校验的人工答复对象。

    返回:
        至少包含一个元素的子函数作用域列表；空输入会转换为全局通配符。
    """

    # 用户输入可能是单值或列表，统一后再转成字符串名称。
    list_raw_subfunctions = _as_list(answer.get("affected_subfunctions"))  # 原始子函数作用域列表

    # 空列表代表用户希望该决策覆盖整个 HLS 任务。
    if not list_raw_subfunctions:

        # 返回通配符，和旧实现的默认作用域保持一致。
        return ["*"]

    # 将所有作用域项转换为字符串，避免 JSON 数值等类型影响后续匹配。
    return [str(obj_item) for obj_item in list_raw_subfunctions]

# 将约束列表转换为 memory 可读文本。
def _constraint_text(dict_decision: dict[str, Any]) -> str:
    """
    根据人工决策和约束列表生成 memory constraint 文本。

    参数:
        dict_decision: 已规范化的人工决策对象。

    返回:
        面向后续 prompt 和报告展示的英文 constraint 文本。
    """

    # 过滤空白约束，避免 memory 中出现无意义分号。
    str_constraints = "; ".join(  # 拼接后的非空约束文本
        str(obj_item)  # 单条非空人工约束文本
        for obj_item in dict_decision.get("constraints", [])  # 人工决策中的原始约束项
        if str(obj_item).strip()  # 只保留具备实际文本的约束项
    )

    # 有约束时将决策和约束一起写入记忆文本。
    if str_constraints:

        # 返回兼容旧 memory 文案的英文描述。
        return f"Human decision: {dict_decision['decision']}. Constraints: {str_constraints}."

    # 没有额外约束时只记录人工决策正文。
    return f"Human decision: {dict_decision['decision']}."

# 将单值、列表或空值规整为列表。
def _as_list(obj_value: Any) -> list[Any]:
    """
    把可能来自 JSON 的单值字段统一转换为列表。

    参数:
        obj_value: None、列表或任意单个 JSON 字段值。

    返回:
        统一后的列表；None 会转换为空列表。
    """

    # None 表示用户未提供该字段内容。
    if obj_value is None:

        # 空输入保持为空列表，交由调用方决定默认语义。
        return []

    # 原本就是列表时无需包装。
    if isinstance(obj_value, list):

        # 返回原列表以保持旧实现对列表对象的语义。
        return obj_value

    # 单值输入包装成一项列表，便于后续统一遍历。
    return [obj_value]
