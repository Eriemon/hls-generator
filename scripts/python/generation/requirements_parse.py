"""requirements 解析与文本推断 helper。"""

# 延迟注解求值，避免运行时解析前向引用。
from __future__ import annotations

# 正则用于文本证据判定；Any 覆盖 JSON 风格容器里的混合值类型。
import re
from typing import Any

# 根 requirements 模块提供共享类型别名与稳定枚举常量。
from scripts.python.generation.requirements import (
    DATAFLOW_STREAMABILITY_VALUES,
    STREAMABILITY_VALUES,
    STREAM_KEYWORDS,
    TRANSPORT_INTERFACES,
    JsonDict,
)

# 安全读取对象字段；非对象输入统一返回 None。
def _dict_field(mapping: JsonDict, field_name: str) -> JsonDict | None:
    """
    从 JSON 风格对象中读取字典字段。

    :param mapping: 待读取的 JSON 风格对象。
    :param field_name: 目标字段名。
    :return: 字段为字典时返回其值，否则返回 None。
    """

    # 只有对象值才可继续作为结构化字段消费。
    if isinstance(mapping.get(field_name), dict):

        # 返回结构化字典，供后续逻辑直接使用。
        return mapping[field_name]

    # 其余情况统一视作不存在。
    return None

# 安全读取对象字段；缺失时返回空字典，便于只读路径使用。
def _dict_field_or_empty(mapping: JsonDict, field_name: str) -> JsonDict:
    """
    从 JSON 风格对象中读取字典字段，缺失时回退为空对象。

    :param mapping: 待读取的 JSON 风格对象。
    :param field_name: 目标字段名。
    :return: 字段为字典时返回其值，否则返回空字典。
    """

    # 复用统一对象读取入口，保持类型缩窄一致。
    json_dict_value = _dict_field(mapping, field_name)  # 已缩窄类型的结构化字段

    # 缺失字段在只读路径上统一回退为空对象。
    return json_dict_value or {}

# 安全读取字符串字段，避免局部变量长期携带 Any。
def _string_field(mapping: JsonDict, field_name: str) -> str | None:
    """
    从 JSON 风格对象中读取字符串字段。

    :param mapping: 待读取的 JSON 风格对象。
    :param field_name: 目标字段名。
    :return: 字段为字符串时返回其值，否则返回 None。
    """

    # 只有字符串才保留原始语义。
    if isinstance(mapping.get(field_name), str):

        # 返回原始字符串，避免非字符串被静默转型。
        return mapping[field_name]

    # 其余类型统一按缺失处理。
    return None

# 安全读取列表字段，缺失或类型不符时回退为空列表。
def _list_field(mapping: JsonDict, field_name: str) -> list[Any]:
    """
    从 JSON 风格对象中读取列表字段。

    :param mapping: 待读取的 JSON 风格对象。
    :param field_name: 目标字段名。
    :return: 字段为列表时返回其值，否则返回空列表。
    """

    # 只保留真实列表，避免误把字符串当作可迭代容器。
    if isinstance(mapping.get(field_name), list):

        # 返回原始列表即可，调用方按只读方式消费。
        return mapping[field_name]

    # 其余情况统一回退为空列表。
    return []

# design_requirements 是大多数推断逻辑的首选覆盖源。
def _design_requirements(spec: JsonDict) -> JsonDict | None:
    """
    读取规范中的 design_requirements 对象。

    :param spec: HLS 规范字典。
    :return: design_requirements 为对象时返回其值，否则返回 None。
    """

    # design_requirements 采用统一对象读取逻辑。
    return _dict_field(spec, "design_requirements")

# 接口参数统一从 interfaces.arguments 中读取。
def _interface_arguments(spec: JsonDict) -> list[JsonDict]:
    """
    读取规范中的接口参数列表。

    :param spec: HLS 规范字典。
    :return: 已过滤为字典条目的接口参数列表。
    """

    # interfaces 允许缺失，此时按空对象处理。
    json_dict_interfaces = _dict_field_or_empty(  # 顶层 interfaces 镜像对象
        spec,  # 当前 HLS 规范字典
        "interfaces",  # 顶层 interfaces 字段名
    )

    # arguments 可能混入非字典条目，需要过滤。
    list_arguments = _list_field(json_dict_interfaces, "arguments")  # 原始接口参数条目列表

    # 只保留结构化参数对象，避免后续 values() 调用报错。
    return [item for item in list_arguments if isinstance(item, dict)]

# evidence.items 是外部抽取得到的文本证据集合。
def _evidence_items(evidence: JsonDict | None) -> list[JsonDict]:
    """
    读取证据对象中的条目列表。

    :param evidence: 可选证据对象。
    :return: 已过滤为字典条目的证据条目列表。
    """

    # 缺失证据时直接返回空列表。
    if evidence is None:

        # 无证据时不参与文本推断。
        return []

    # items 是唯一需要消费的证据字段。
    list_items = _list_field(evidence, "items")  # 原始证据条目列表

    # 只保留结构化证据对象。
    return [item for item in list_items if isinstance(item, dict)]

# info-dict 风格字段允许字符串或 {"text": "..."} 两种语义输入。
def _append_info_field_texts(
    fragments: list[str],
    spec: JsonDict,
    field_names: tuple[str, ...],
) -> None:
    """
    从 behavior/constraints/notes 等字段提取文本片段。

    :param fragments: 待追加文本片段的列表。
    :param spec: HLS 规范字典。
    :param field_names: 需要提取的字段名元组。
    :return: 无；提取到的文本片段会原地追加到 fragments。
    """

    # 逐字段读取，兼容字符串条目和信息字典条目。
    for field_name in field_names:

        # 每个字段都按列表语义读取。
        list_items = _list_field(spec, field_name)  # 当前信息字段条目列表

        # 条目可以是字典或原始标量。
        for item in list_items:

            # 字典条目优先读取 text 字段。
            if isinstance(item, dict):

                # text 缺失时回退为整个字典的字符串表达。
                str_text = str(item.get("text") if item.get("text") is not None else item)  # 归一化后的条目文本

            # 非字典条目直接把自身文本写入片段池。
            else:

                # 标量条目直接转成文本后参与后续关键词匹配。
                str_text = str(item)  # 标量条目的文本表达

            # 所有条目文本都进入推断证据池。
            fragments.append(str_text)

# 接口参数对象的全部值都可能包含接口提示信息。
def _append_argument_texts(fragments: list[str], spec: JsonDict) -> None:
    """
    从接口参数对象中提取文本片段。

    :param fragments: 待追加文本片段的列表。
    :param spec: HLS 规范字典。
    :return: 无；接口参数中的文本会原地追加到 fragments。
    """

    # 参数对象中的字段值可能包含 interface/bundle/type 线索。
    for dict_argument in _interface_arguments(spec):

        # 参数对象的所有值都转成字符串参与匹配。
        for raw_value in dict_argument.values():

            # 逐值转为字符串，保持与旧行为一致。
            fragments.append(str(raw_value))

# 证据文本只取前 12 条，保持 prompt 与判定成本稳定。
def _append_evidence_texts(fragments: list[str], evidence: JsonDict | None) -> None:
    """
    从证据对象中提取文本片段。

    :param fragments: 待追加文本片段的列表。
    :param evidence: 可选证据对象。
    :return: 无；命中的证据文本会原地追加到 fragments。
    """

    # 旧行为只消费前 12 条文本证据。
    for dict_item in _evidence_items(evidence)[:12]:

        # 只读取 text 字段，避免证据对象结构噪声进入 blob。
        str_text = _string_field(dict_item, "text")  # 证据文本

        # 空文本不参与判定。
        if str_text:

            # 保留原始证据文本内容。
            fragments.append(str_text)

# 某些推断会额外读取顶层显式字段文本。
def _append_string_field_texts(
    fragments: list[str],
    spec: JsonDict,
    field_names: tuple[str, ...],
) -> None:
    """
    从指定字符串字段中提取文本片段。

    :param fragments: 待追加文本片段的列表。
    :param spec: HLS 规范字典。
    :param field_names: 需要读取的字符串字段名元组。
    :return: 无；命中的字符串字段文本会原地追加到 fragments。
    """

    # 逐字段读取，避免调用点重复书写类型判断。
    for field_name in field_names:

        # 只保留真实字符串字段。
        str_value = _string_field(spec, field_name)  # 当前字段文本

        # 缺失文本时无需追加。
        if str_value is not None:

            # 保留字段原始文本。
            fragments.append(str_value)

# 文本 blob 的片段来源集中在一个 helper 中维护。
def _spec_text_fragments(
    spec: JsonDict,
    evidence: JsonDict | None = None,
    *,
    include_string_fields: tuple[str, ...],
) -> list[str]:
    """
    提取参与流式与接口判定的文本片段。

    :param spec: HLS 规范字典。
    :param evidence: 可选证据对象。
    :param include_string_fields: 需要直接读取的顶层字符串字段。
    :return: 所有参与判定的文本片段列表。
    """

    # 所有文本线索都汇总到这个列表中。
    list_fragments: list[str] = []  # 文本片段收集列表

    # 先读取 description 等显式字符串字段。
    _append_string_field_texts(list_fragments, spec, include_string_fields)

    # 再读取 behavior/constraints/notes 等信息字段。
    _append_info_field_texts(
        list_fragments,
        spec,
        ("behavior", "constraints", "notes"),
    )

    # 接口参数会贡献 interface/type/bundle 等关键词。
    _append_argument_texts(list_fragments, spec)

    # 最后追加外部证据文本，保持旧的优先级和截断策略。
    _append_evidence_texts(list_fragments, evidence)

    # 返回原始文本片段列表，供调用方决定是否 lower()。
    return list_fragments

# 优先读取顶层显式值，再读取 design_requirements 中的覆盖值。
def _explicit_requirement_value(
    spec: JsonDict,
    field_name: str,
    allowed_values: tuple[str, ...],
) -> str | None:
    """
    读取规范或 design_requirements 中的显式需求值。

    :param spec: HLS 规范字典。
    :param field_name: 需求字段名。
    :param allowed_values: 合法离散值集合。
    :return: 命中合法值时返回其字符串，否则返回 None。
    """

    # 顶层字段优先，允许调用方直接覆盖推断结果。
    str_explicit_value = _string_field(spec, field_name)  # 顶层显式需求值

    # 顶层值合法时直接返回。
    if str_explicit_value in allowed_values:

        # 保持返回值为原始字符串。
        return str_explicit_value

    # design_requirements 是第二优先级覆盖源。
    dict_requirements = _design_requirements(spec)  # 设计需求对象

    # 只有对象形态的 design_requirements 才参与读取。
    if dict_requirements is None:

        # 不存在设计需求对象时直接结束。
        return None

    # 读取 design_requirements 中的覆盖值。
    str_requirement_value = _string_field(dict_requirements, field_name)  # 需求覆盖值

    # 命中合法值时返回，否则仍按缺失处理。
    return str_requirement_value if str_requirement_value in allowed_values else None

# streamable 的启发式判定额外依赖关键词匹配。
def _contains_stream_keywords(blob: str) -> bool:
    """
    判断文本是否包含流式语义关键词。

    :param blob: 已归一化为小写的文本 blob。
    :return: 命中任一流式关键词时返回 True。
    """

    # 使用带词边界的正则，避免误命中更长单词片段。
    return any(
        re.search(rf"\b{re.escape(str_keyword)}\b", blob)
        for str_keyword in STREAM_KEYWORDS
    )

# memory-mapped 语义会把 streamability 拉回 non_streamable。
def _contains_memory_mapped_markers(blob: str) -> bool:
    """
    判断文本是否明确指向 memory-mapped 传输。

    :param blob: 已归一化为小写的文本 blob。
    :return: 命中 memory-mapped 语义时返回 True。
    """

    # 这些标记代表更偏向 m_axi 的存储器搬运语义。
    return (
        "m_axi" in blob
        or "memory-mapped" in blob
        or "memory mapped" in blob
    )

# 推断 streamability 前先把文本来源统一收敛为小写 blob。
def detect_streamability(
    spec: JsonDict,
    evidence: JsonDict | None = None,
) -> str:
    """
    推断当前 HLS 任务是否应视为流式任务。

    :param spec: HLS 规范字典。
    :param evidence: 可选证据对象。
    :return: `streamable`、`non_streamable` 或 `unknown` 之一。
    """

    # 显式确认优先于启发式判定。
    str_explicit_streamability = _explicit_requirement_value(  # 需求或顶层 spec 中显式声明的流式能力标签
        spec,  # 从当前规范中抽取 streamability 候选
        "streamability",  # 需要读取的流式能力字段
        STREAMABILITY_VALUES,  # 允许的流式能力枚举
    )

    # 显式值合法时直接返回。
    if str_explicit_streamability is not None:

        # 需求明确时不再继续启发式分析。
        return str_explicit_streamability

    # 未确认时回退到文本证据推断。
    str_spec_blob = _spec_text_blob(spec, evidence)  # 供关键词匹配复用的统一小写文本证据

    # 明确的流接口线索优先判定为 streamable。
    if _has_stream_transport(str_spec_blob):

        # AXIS 或 hls::stream 已足够表明流式语义。
        return "streamable"

    # 关键词匹配覆盖 packet/frame/token 等较弱线索。
    if _contains_stream_keywords(str_spec_blob):

        # 命中流式语义词时仍按 streamable 处理。
        return "streamable"

    # memory-mapped 线索明确时保持非流式语义。
    if _contains_memory_mapped_markers(str_spec_blob):

        # m_axi / memory-mapped 默认归为非流式。
        return "non_streamable"

    # 保持旧行为：没有明确流语义时默认 non_streamable。
    return "non_streamable"

# 传输接口推断负责给 requirements/codegen plan 提供默认接口标签。
def detect_transport_interface(
    spec: JsonDict,
    evidence: JsonDict | None = None,
) -> str:
    """
    推断当前 HLS 任务的默认传输接口类型。

    :param spec: HLS 规范字典。
    :param evidence: 可选证据对象。
    :return: `TRANSPORT_INTERFACES` 中的一个合法值。
    """

    # 显式 transport_interface 优先于文本启发式。
    str_explicit_transport = _explicit_requirement_value(  # 需求或顶层 spec 中显式声明的接口类型
        spec,  # 提供 transport_interface 显式值来源的规范对象
        "transport_interface",  # 需要读取的接口类型字段
        TRANSPORT_INTERFACES,  # 允许的接口类型枚举
    )

    # 有显式值时不再进行启发式推断。
    if str_explicit_transport is not None:

        # 返回调用方确认过的接口类型。
        return str_explicit_transport

    # 启发式推断统一基于归一化文本 blob。
    str_spec_blob = _spec_text_blob(spec, evidence)  # 供接口关键词判定复用的统一小写文本证据

    # hls::stream 比 axis 更具体，优先单独标记。
    if "hls::stream" in str_spec_blob:

        # 明确使用 hls::stream 容器时返回对应标签。
        return "hls_stream"

    # AXIS 语义统一收敛到 axis。
    if _has_axis_transport(str_spec_blob):

        # 命中 axis/axi-stream 关键词时按 axis 处理。
        return "axis"

    # m_axi 是最常见的 memory-mapped 顶层接口。
    if "m_axi" in str_spec_blob:

        # 明确命中 m_axi 时直接返回。
        return "m_axi"

    # s_axilite 通常用于控制口与标量口。
    if "s_axilite" in str_spec_blob:

        # 命中 s_axilite 时返回对应标签。
        return "s_axilite"

    # 无法从文本稳定推断时保持 unknown。
    return "unknown"

# DATAFLOW 语义依赖 transport_interface 与 streamability 的组合判断。
def detect_dataflow_streamability(
    spec: JsonDict,
    evidence: JsonDict | None = None,
) -> str:
    """
    推断 DATAFLOW 视角下的流式能力标签。

    :param spec: HLS 规范字典。
    :param evidence: 可选证据对象。
    :return: `DATAFLOW_STREAMABILITY_VALUES` 中的一个合法值。
    """

    # 显式 dataflow_streamability 优先于派生判定。
    str_explicit_dataflow = _explicit_requirement_value(  # 需求或顶层 spec 中显式声明的 DATAFLOW 流式能力
        spec,  # 提供 DATAFLOW 能力显式确认与顶层镜像的读取来源
        "dataflow_streamability",  # 需要读取的 DATAFLOW 能力字段
        DATAFLOW_STREAMABILITY_VALUES,  # 允许的 DATAFLOW 能力枚举
    )

    # 有显式值时直接返回。
    if str_explicit_dataflow is not None:

        # 用户确认值优先级最高。
        return str_explicit_dataflow

    # transport_interface 是 DATAFLOW 判定的第一信号源。
    str_transport_interface = detect_transport_interface(  # 参与 DATAFLOW 派生判断的接口类型
        spec,  # 待分析 transport 线索的规范对象
        evidence,  # 补充接口关键词判断的证据对象
    )

    # 流接口默认对应 streamable DATAFLOW 语义。
    if str_transport_interface in {"axis", "hls_stream"}:

        # AXIS / hls_stream 都应走流式 DATAFLOW。
        return "streamable"

    # m_axi 对应 memory_mapped 语义。
    if str_transport_interface == "m_axi":

        # 显式访存接口默认归入 memory_mapped。
        return "memory_mapped"

    # 其余情况回退到 streamability 的粗粒度判定。
    str_streamability = detect_streamability(spec, evidence)  # 粗粒度流式能力

    # 流式任务保持 streamable，否则按 batch 处理。
    return "streamable" if str_streamability == "streamable" else "batch"

# 所有启发式判定都复用同一份归一化文本 blob。
def _spec_text_blob(
    spec: JsonDict,
    evidence: JsonDict | None = None,
) -> str:
    """
    把规范与证据归一化成单个小写文本 blob。

    :param spec: HLS 规范字典。
    :param evidence: 可选证据对象。
    :return: 合并后的全小写文本 blob。
    """

    # 这三个顶层字段最直接影响接口和 DATAFLOW 判定。
    list_fragments = _spec_text_fragments(  # 后续会被拼成统一 blob 的原始文本片段列表
        spec,  # 提供 description/transport/dataflow 字段的规范对象
        evidence,  # 额外证据对象
        include_string_fields=(  # 需要拼入 blob 的顶层字符串字段
            "description",  # 任务描述文本
            "transport_interface",  # 接口类型线索
            "dataflow_streamability",  # DATAFLOW 语义线索
        ),
    )

    # 统一转为小写后再拼接，保持关键词匹配稳定。
    return " ".join(str_fragment.lower() for str_fragment in list_fragments)

# AXIS 及其常见写法共用一个判定入口。
def _has_axis_transport(blob: str) -> bool:
    """
    判断文本是否包含 AXIS 传输语义。

    :param blob: 已归一化为小写的文本 blob。
    :return: 命中 AXIS 相关关键词时返回 True。
    """

    # 兼容 axis、axi-stream 和 axi stream 三种常见写法。
    return "axis" in blob or "axi-stream" in blob or "axi stream" in blob

# 流接口判定在 AXIS 基础上额外识别 hls::stream。
def _has_stream_transport(blob: str) -> bool:
    """
    判断文本是否包含显式流接口语义。

    :param blob: 已归一化为小写的文本 blob。
    :return: 命中 AXIS 或 hls::stream 时返回 True。
    """

    # AXIS 与 hls::stream 都代表显式流接口。
    return _has_axis_transport(blob) or "hls::stream" in blob
