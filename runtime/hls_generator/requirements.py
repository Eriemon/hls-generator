"""HLS 需求确认、需求载荷整理与 codegen plan 构造工具。"""

# 延迟注解求值，避免运行时解析前向引用。
from __future__ import annotations

# 深拷贝用于保护调用方输入，正则用于文本证据判定。
import copy
import re
from typing import Any

# 模式模块提供 pattern 元数据与开放问题补充能力。
from .patterns import canonical_pattern_name, pattern_definition, pattern_open_questions

# JSON 风格对象在本模块中频繁出现，集中定义别名便于阅读。
JsonDict = dict[str, Any]  # JSON 风格对象别名

# 校验阶段统一返回字符串问题列表。
IssueList = list[str]  # 需求确认问题列表别名

# streamability 只能落在这三个离散值内。
STREAMABILITY_VALUES = ("streamable", "non_streamable", "unknown")  # 流式能力合法取值

# transport_interface 表示顶层数据搬运接口族。
TRANSPORT_INTERFACES = (  # 传输接口合法取值
    "axis",  # 顶层使用 AXI-Stream 口完成流式传输
    "hls_stream",  # 顶层使用 hls::stream 容器表达流式通道
    "m_axi",  # 顶层使用 AXI4 memory-mapped 主口搬运数据
    "s_axilite",  # 顶层使用 AXI4-Lite 控制口传递标量
    "native",  # 顶层直接暴露原生数组或标量接口
    "custom",  # 顶层接口需要调用方给出自定义合同
    "unknown",  # 当前文本证据尚不足以稳定判定接口类型
)

# DATAFLOW 阶段额外关心流式/访存/批处理语义。
DATAFLOW_STREAMABILITY_VALUES = (  # DATAFLOW 语义合法取值
    "streamable",  # DATAFLOW 子阶段通过流通道逐拍传递数据
    "memory_mapped",  # DATAFLOW 子阶段主要围绕访存接口组织搬运
    "batch",  # DATAFLOW 子阶段按批处理模式消费和产出数据
    "unknown",  # DATAFLOW 语义仍缺少足够证据判定
)

# interface_family 是 codegen plan 使用的更高层接口家族标签。
INTERFACE_FAMILIES = ("native", "axi_stream", "axi4", "custom")  # 接口家族合法取值

# AXI4 profile 使用的子类型枚举。
AXI4_VARIANTS = ("axi4_full", "axi4_lite")  # AXI4 变体枚举

# AXI4 profile 需要区分主从角色。
AXI4_ROLES = ("master", "slave")  # AXI4 角色枚举

# AXI4 profile 需要区分读写模式。
AXI4_MODES = ("read", "write", "read_write")  # AXI4 读写模式枚举

# AXI-Stream profile 只允许这些键。
AXI_STREAM_PROFILE_KEYS = ("keep_ready", "keep_last", "data_width")  # AXI-Stream 画像键名

# AXI4 profile 允许的键集中维护，避免散落在校验逻辑里。
AXI4_PROFILE_KEYS = (  # 供 AXI4 画像校验复用的允许键集合
    "axi4_variant",  # AXI4 full 或 lite 变体标签
    "role",  # AXI4 端口在系统中的 master/slave 角色
    "read_write_mode",  # AXI4 访问方向声明
    "data_width",  # AXI4 数据总线位宽
    "addr_width",  # AXI4 地址总线位宽
    "id_width",  # AXI4 full 模式下的事务 ID 位宽
    "burst_support",  # AXI4 端口是否允许 burst 事务
    "max_burst_len",  # AXI4 burst 事务允许的最大长度
)

# 文本判定 streamable 时优先关注这些语义词。
STREAM_KEYWORDS = (  # 流式语义关键词
    "stream",  # 明示流通道或流接口
    "packet",  # 明示按包传递数据
    "frame",  # 明示按帧组织数据
    "sample",  # 明示按样本逐项流动
    "line",  # 明示按线扫描或逐行传递
    "token",  # 明示按 token 顺序流动
    "sequence",  # 明示按序列顺序流动
    "valid",  # 明示 valid 握手信号
    "ready",  # 明示 consumer 反压使用的 ready 握手
    "last",  # 明示包尾或帧尾标志
)

# apply_requirement_defaults 只接受这些命名覆盖项。
APPLY_REQUIREMENT_OVERRIDE_KEYS = (  # apply_requirement_defaults 允许的命名覆盖键
    "design_requirements",  # 调用方显式提供的 design_requirements 覆盖对象
    "pipeline_required",  # 调用方显式指定的 pipeline_required 标志
    "streamability",  # 调用方显式指定的流式能力标签
    "interface_family",  # 调用方显式指定的接口家族标签
    "interface_profile",  # 调用方显式指定的接口画像对象
    "confirmation_notes",  # 调用方显式提供的确认说明文本
    "confirmed_by_user",  # 调用方显式提供的已确认标志
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

# design_requirements 的合并逻辑集中在 helper 中，避免主流程堆积细节。
def _merged_design_requirements(
    spec: JsonDict,
    design_requirements: JsonDict | None,
) -> JsonDict:
    """
    合并规范自带与调用方显式传入的 design_requirements。

    :param spec: 深拷贝后的 HLS 规范字典。
    :param design_requirements: 调用方额外提供的设计需求覆盖项。
    :return: 合并后的 design_requirements 副本。
    """

    # 先复制规范自带的 design_requirements，保护输入对象。
    dict_base_requirements = copy.deepcopy(_design_requirements(spec) or {})  # 当前需求基线对象

    # 调用方显式覆盖项优先级更高。
    if design_requirements:

        # 深拷贝后 update，避免共享可变子对象。
        dict_base_requirements.update(copy.deepcopy(design_requirements))

    # 返回合并完成的需求基线。
    return dict_base_requirements

# interface_profile 允许同时来自 spec、design_requirements 与调用方覆盖项。
def _resolved_interface_profile(
    spec: JsonDict,
    merged_requirements: JsonDict,
    interface_profile: JsonDict | None,
) -> JsonDict:
    """
    解析最终生效的 interface_profile。

    :param spec: 深拷贝后的 HLS 规范字典。
    :param merged_requirements: 已合并的 design_requirements 对象。
    :param interface_profile: 调用方显式提供的 interface_profile 覆盖项。
    :return: 深拷贝后的最终 interface_profile 对象。
    """

    # 顶层 interface_profile 是第一层默认值来源。
    dict_resolved_profile = copy.deepcopy(_dict_field_or_empty(spec, "interface_profile"))  # 最终接口画像对象

    # design_requirements 里的 interface_profile 可以覆盖顶层值。
    dict_requirement_profile = _dict_field(merged_requirements, "interface_profile")  # 需求中的接口画像对象

    # 有需求画像时按对象语义合并。
    if dict_requirement_profile is not None:

        # 需求画像优先级高于顶层 spec。
        dict_resolved_profile.update(copy.deepcopy(dict_requirement_profile))

    # 调用方显式 override 优先级最高。
    if interface_profile:

        # 深拷贝后合并，保护调用方原始对象。
        dict_resolved_profile.update(copy.deepcopy(interface_profile))

    # 返回最终接口画像副本。
    return dict_resolved_profile

# dataflow_streamability 的解析依赖前面已经解析出的 streamability/transport。
def _resolved_dataflow_streamability(
    spec: JsonDict,
    merged_requirements: JsonDict,
    streamability: str,
    transport_interface: str,
) -> str:
    """
    解析最终生效的 dataflow_streamability。

    :param spec: 深拷贝后的 HLS 规范字典。
    :param merged_requirements: 已合并的 design_requirements 对象。
    :param streamability: 已解析完成的 streamability。
    :param transport_interface: 已解析完成的 transport_interface。
    :return: 最终生效的 DATAFLOW 流式能力标签。
    """

    # design_requirements 可以直接给出明确值。
    str_requirement_dataflow = _string_field(merged_requirements, "dataflow_streamability")  # 需求中的 DATAFLOW 流式能力

    # 合法需求值优先返回。
    if str_requirement_dataflow in DATAFLOW_STREAMABILITY_VALUES:

        # 需求中的显式值优先级最高。
        return str_requirement_dataflow

    # 顶层 spec 也允许直接给出显式值。
    str_spec_dataflow = _string_field(spec, "dataflow_streamability")  # 顶层 DATAFLOW 流式能力

    # 顶层显式值合法时直接使用。
    if str_spec_dataflow in DATAFLOW_STREAMABILITY_VALUES:

        # 顶层 spec 的显式值优先于派生逻辑。
        return str_spec_dataflow

    # 否则回退到 transport + streamability 派生逻辑。
    return detect_dataflow_streamability(
        {
            **spec,
            "transport_interface": transport_interface,
            "streamability": streamability,
        }
    )

# streamability / transport / interface 相关字段共用同一条解析路径。
def _resolved_requirement_identity_defaults(
    spec: JsonDict,
    json_dict_base_requirements: JsonDict,
    override_options: JsonDict,
) -> JsonDict:
    """
    解析 requirements 默认化阶段的身份与接口字段。

    :param spec: 深拷贝后的 HLS 规范字典。
    :param json_dict_base_requirements: 合并后的 design_requirements 对象。
    :param override_options: 调用方传入的命名覆盖项对象。
    :return: streamability、transport 与接口相关的解析结果对象。
    """

    # streamability 的最终结果会同时写回顶层 spec 和 design_requirements。
    str_resolved_streamability = (
        override_options.get("streamability")  # 调用方显式覆盖的流式能力
        or _string_field(json_dict_base_requirements, "streamability")  # 需求镜像中的流式能力
        or _string_field(spec, "streamability")  # 顶层 spec 中的流式能力
        or detect_streamability(spec)  # 启发式推断的默认流式能力
    )

    # transport_interface 决定后续 DATAFLOW 派生语义。
    str_resolved_transport_interface = (
        _string_field(json_dict_base_requirements, "transport_interface")  # 需求镜像中的接口类型
        or _string_field(spec, "transport_interface")  # 顶层 spec 中的接口类型
        or detect_transport_interface(spec)  # 启发式推断出的默认接口类型
    )

    # dataflow_streamability 需要结合 streamability 与 transport_interface 共同决定。
    str_resolved_dataflow_streamability = _resolved_dataflow_streamability(  # 写回 spec 的 DATAFLOW 流式语义标签
        spec,  # 待写回默认值的规范对象
        json_dict_base_requirements,  # 需求镜像对象
        str_resolved_streamability,  # 已解析的粗粒度流式能力
        str_resolved_transport_interface,  # 已解析的接口类型
    )

    # interface_family 保持“显式覆盖优先、否则保留未确认”的旧策略。
    str_resolved_interface_family = (
        override_options.get("interface_family")  # 调用方显式覆盖的接口家族
        or _string_field(json_dict_base_requirements, "interface_family")  # 需求镜像中的接口家族
        or _string_field(spec, "interface_family")  # 顶层 spec 中的接口家族
    )

    # interface_profile 会被 workflow、adapter 和 plan 阶段重复消费。
    json_dict_resolved_interface_profile = _resolved_interface_profile(  # 写回 spec 的接口画像对象
        spec,  # 待合并接口画像来源的规范对象
        json_dict_base_requirements,  # 已合并的需求镜像对象
        override_options.get("interface_profile"),  # 调用方显式覆盖的接口画像
    )

    # 返回接口与语义相关的解析结果对象。
    return {
        "streamability": str_resolved_streamability,
        "transport_interface": str_resolved_transport_interface,
        "dataflow_streamability": str_resolved_dataflow_streamability,
        "interface_family": str_resolved_interface_family,
        "interface_profile": json_dict_resolved_interface_profile,
    }

# pipeline / confirmation 相关字段共用另一条解析路径，避免单函数过大。
def _resolved_requirement_confirmation_defaults(
    spec: JsonDict,
    json_dict_base_requirements: JsonDict,
    override_options: JsonDict,
) -> JsonDict:
    """
    解析 requirements 默认化阶段的确认与执行控制字段。

    :param spec: 深拷贝后的 HLS 规范字典。
    :param json_dict_base_requirements: 合并后的 design_requirements 对象。
    :param override_options: 调用方传入的命名覆盖项对象。
    :return: pipeline_required、confirmed_by_user 与 confirmation_notes 结果对象。
    """

    # pipeline_required 保持 True 默认值，不允许因为缺字段而回落到 False。
    if override_options.get("pipeline_required") is not None:

        # 调用方显式覆盖时直接使用传入布尔语义。
        bool_resolved_pipeline_required = bool(override_options["pipeline_required"])  # 调用方显式覆盖的 pipeline_required

    # 调用方未显式覆盖时，继续沿用 requirements 镜像或顶层默认值。
    else:

        # 缺少显式覆盖时回退到 requirements 镜像或顶层 spec 默认值。
        bool_resolved_pipeline_required = bool(  # 从 requirements 镜像或顶层默认值回退得到的 pipeline_required
            json_dict_base_requirements.get(  # requirements 镜像中的 pipeline_required 或默认回退值
                "pipeline_required",  # requirements 镜像中的 pipeline_required 键名
                spec.get("pipeline_required", True),  # 顶层 spec 的最终默认值
            )
        )  # 需求镜像或顶层 spec 推断的 pipeline_required

    # confirmed_by_user 缺失时仍保持 False，避免静默确认。
    if override_options.get("confirmed_by_user") is not None:

        # 调用方显式给出确认标记时优先保留该结论。
        bool_resolved_confirmed_by_user = bool(override_options["confirmed_by_user"])  # 调用方显式确认标记

    # 调用方没有显式 confirmed_by_user 时，回退到 design_requirements 镜像。
    else:

        # 否则沿用 design_requirements 中已有的确认状态。
        bool_resolved_confirmed_by_user = bool(json_dict_base_requirements.get("confirmed_by_user", False))  # requirements 镜像中的确认标记

    # confirmation_notes 统一保存为字符串，便于持久化和摘要展示。
    if override_options.get("confirmation_notes") is not None:

        # 显式覆盖的确认说明优先写回 design_requirements。
        str_resolved_confirmation_notes = str(override_options["confirmation_notes"])  # 调用方覆盖写入的确认说明文本

    # 调用方没有显式 confirmation_notes 时，回退到需求镜像中的说明文本。
    else:

        # 缺少显式覆盖时保留 requirements 镜像中的确认说明。
        str_resolved_confirmation_notes = str(json_dict_base_requirements.get("confirmation_notes", "") or "")  # requirements 镜像中的确认说明文本

    # 返回执行控制与确认相关的解析结果对象。
    return {
        "pipeline_required": bool_resolved_pipeline_required,
        "confirmed_by_user": bool_resolved_confirmed_by_user,
        "confirmation_notes": str_resolved_confirmation_notes,
    }

# 把默认值解析集中到 helper 中，减少 apply_requirement_defaults 的主流程长度。
def _resolved_requirement_defaults(
    spec: JsonDict,
    override_options: JsonDict,
) -> JsonDict:
    """
    解析 requirements 默认化阶段需要写回 spec 的所有值。

    :param spec: 深拷贝后的 HLS 规范字典。
    :param override_options: 调用方传入的命名覆盖项对象。
    :return: 所有待写回字段组成的解析结果对象。
    """

    # 先合并 design_requirements，后续解析统一读取这份镜像。
    json_dict_base_requirements = _merged_design_requirements(spec, override_options.get("design_requirements"))  # requirements/default 解析共用的需求基线对象

    # 先解析接口与语义字段。
    json_dict_resolved_defaults = _resolved_requirement_identity_defaults(  # 汇总接口与语义字段的解析结果对象
        spec,  # 提供默认身份字段来源的 HLS 规范字典
        json_dict_base_requirements,  # design_requirements 合并后的镜像对象
        override_options,  # 调用方显式覆盖项
    )  # requirements/default 的接口与语义字段结果对象

    # 再补齐确认与执行控制字段。
    json_dict_resolved_defaults.update(
        _resolved_requirement_confirmation_defaults(
            spec,
            json_dict_base_requirements,
            override_options,
        )
    )

    # 返回后续写回 spec 所需的全部解析结果对象。
    return json_dict_resolved_defaults

# design_requirements 持久化对象的组装逻辑独立封装，避免主流程重复展开。
def _design_requirements_payload(
    resolved_defaults: JsonDict,
) -> JsonDict:
    """
    根据解析结果构造 design_requirements 持久化对象。

    :param resolved_defaults: requirements/default 解析结果对象。
    :return: 可直接写回 spec 的 design_requirements 对象。
    """

    # 返回 requirements/validation/codegen plan 共用的主合同对象。
    return {
        "target": "hls",
        "pipeline_required": bool(
            resolved_defaults["pipeline_required"]  # 已解析的 pipeline_required 布尔值
        ),
        "streamability": str(resolved_defaults["streamability"]),  # 已解析的流式能力标签
        "transport_interface": str(resolved_defaults["transport_interface"]),  # 已解析的接口类型标签
        "dataflow_streamability": str(resolved_defaults["dataflow_streamability"]),  # 已解析的 DATAFLOW 流式能力
        "interface_family": resolved_defaults["interface_family"],  # 已解析的接口家族标签
        "interface_profile": resolved_defaults["interface_profile"],  # 已解析的接口画像对象
        "confirmed_by_user": resolved_defaults["confirmed_by_user"],  # 已解析的用户确认标记
        "confirmation_notes": resolved_defaults["confirmation_notes"],  # 已解析的确认说明文本
    }

# 顶层 spec 的写回逻辑单独封装，减少 apply_requirement_defaults 的长度。
def _write_requirement_defaults_to_spec(
    spec: JsonDict,
    resolved_defaults: JsonDict,
) -> None:
    """
    把已解析的需求默认值写回顶层 spec。

    :param spec: 待写回的 HLS 规范字典。
    :param resolved_defaults: requirements/default 解析结果对象。
    :return: 无；写回结果会直接原地更新 spec。
    """

    # 顶层 target 在 requirements 默认化阶段统一钉死为 hls。
    spec["target"] = "hls"  # 固定当前技能的 HLS 目标标签

    # pipeline_required 需要在顶层 spec 和 design_requirements 间保持镜像。
    spec["pipeline_required"] = bool(resolved_defaults["pipeline_required"])  # 写回顶层 pipeline_required 布尔值

    # streamability 结果同步写回顶层 spec。
    spec["streamability"] = str(resolved_defaults["streamability"])  # 写回顶层 streamability 标签

    # transport_interface 会直接影响 workflow 选择的接口模板。
    spec["transport_interface"] = str(resolved_defaults["transport_interface"])  # 写回顶层的接口协议标签

    # DATAFLOW 流式能力同步写回顶层 spec。
    spec["dataflow_streamability"] = str(resolved_defaults["dataflow_streamability"])  # 写回顶层 DATAFLOW 流式能力标签

    # interface_family 可能仍为 None；旧行为允许保留未确认状态。
    spec["interface_family"] = resolved_defaults["interface_family"]  # 保留待确认或已确认的接口家族状态

    # interface_profile 在顶层 spec 中始终保持对象形态。
    spec["interface_profile"] = resolved_defaults["interface_profile"]  # 写回顶层 interface_profile 对象

    # codegen_plan_required 缺失时默认 True。
    spec["codegen_plan_required"] = bool(spec.get("codegen_plan_required", True))  # 写回顶层 codegen_plan_required 开关

    # design_requirements 是后续 validation 和 plan 构造的主合同来源。
    spec["design_requirements"] = _design_requirements_payload(resolved_defaults)  # requirements/validation/codegen plan 共享的主合同对象

# apply_requirement_defaults 是 adapter / workflow / CLI 共用的归一化入口。
def apply_requirement_defaults(
    raw_spec: JsonDict,
    **override_options: Any,
) -> JsonDict:
    """
    合并显式需求覆盖项并补齐 HLS 需求默认值。

    :param raw_spec: 原始 HLS 规范字典。
    :param override_options: design_requirements、pipeline_required 等命名覆盖项。
    :return: 补齐默认值后的规范副本。
    :raises TypeError: 当出现未知覆盖键时抛出错误。
    """

    # 深拷贝输入规范，保证调用方对象不会被原地修改。
    dict_spec = copy.deepcopy(raw_spec)  # 归一化后的规范副本

    # 只允许旧公共接口约定的覆盖键，避免静默吞掉拼写错误。
    set_allowed_override_keys = set(APPLY_REQUIREMENT_OVERRIDE_KEYS)  # apply_requirement_defaults 允许的命名覆盖键集合

    # 检查是否出现未知覆盖键，保持接口错误尽早暴露。
    set_unknown_override_keys = set(override_options) - set_allowed_override_keys  # 未知覆盖键集合

    # 未知键出现时用稳定错误文本直接阻止继续执行。
    if set_unknown_override_keys:

        # 保持错误文本聚焦在“未知覆盖键”这一事实。
        raise TypeError(
            "> ERR: [Python] apply_requirement_defaults received unexpected override keys: "
            + ", ".join(sorted(set_unknown_override_keys))
        )

    # 把所有默认值解析集中到 helper 中，主流程只负责写回。
    json_dict_resolved_defaults = _resolved_requirement_defaults(dict_spec, override_options)  # 待写回 spec 的 requirements/default 解析结果集合

    # 把解析结果统一写回顶层 spec。
    _write_requirement_defaults_to_spec(dict_spec, json_dict_resolved_defaults)

    # 返回补齐默认值后的规范副本。
    return dict_spec

# requirements 阶段必须确保用户确认已经显式落盘。
def validate_requirement_confirmation(spec: JsonDict) -> None:
    """
    校验规范是否满足生成前的需求确认合同。

    :param spec: 待校验的 HLS 规范字典。
    :return: 无；需求确认合同满足时静默返回。
    :raises ValueError: 当需求确认合同不满足时抛出首个问题。
    """

    # 所有问题统一由内部 helper 生成。
    issue_list_confirmation = _requirement_confirmation_issues(spec, require_confirmed=True)  # 本次生成前校验收集到的需求确认问题列表

    # 有问题时保持旧行为：抛出首个错误文本。
    if issue_list_confirmation:

        # 首个问题就是 CLI / workflow 暴露给用户的错误文本。
        raise ValueError(
            f"> ERR: [Python] Requirement confirmation failed: {issue_list_confirmation[0]}"
        )

# codegen_plan 的对象字段校验拆出 helper，降低主函数复杂度。
def _validate_codegen_plan_structure(payload: JsonDict) -> None:
    """
    校验 codegen plan 的固定结构字段。

    :param payload: 待校验的 codegen plan JSON 对象。
    :return: 无；结构字段满足合同时静默返回。
    :raises ValueError: 当固定结构字段不满足合同时抛出错误。
    """

    # 三个主策略段都必须是对象。
    for field_name in (
        "interface_decision",
        "pipeline_strategy",
        "verification_strategy",
    ):

        # 每个字段都必须保持对象结构。
        if not isinstance(payload.get(field_name), dict):

            # 三个主策略段缺任一对象字段都会破坏 codegen plan 合同。
            raise ValueError(
                f"> ERR: [Python] Explicit codegen plan must include object field `{field_name}`."
            )

    # open_questions 统一要求列表类型。
    if not isinstance(payload.get("open_questions", []), list):

        # open_questions 必须保持列表类型，便于后续统一拼接阻塞项。
        raise ValueError("> ERR: [Python] Explicit codegen plan open_questions must be a list.")

    # ready_for_generation 必须是布尔值。
    if not isinstance(payload.get("ready_for_generation"), bool):

        # ready_for_generation 必须显式为布尔值，不能依赖 truthy/falsy 猜测。
        raise ValueError(
            "> ERR: [Python] Explicit codegen plan ready_for_generation must be a boolean."
        )

# ready_for_generation 的校验拆出 helper，降低主函数分支数量。
def _validate_codegen_plan_ready_state(
    payload: JsonDict,
    *,
    require_ready: bool,
) -> None:
    """
    校验 codegen plan 是否满足可立即生成的就绪条件。

    :param payload: 待校验的 codegen plan JSON 对象。
    :param require_ready: 是否要求当前 plan 必须 ready。
    :return: 无；ready 状态满足要求时静默返回。
    :raises ValueError: 当 require_ready=True 且 plan 未就绪时抛出错误。
    """

    # 非 ready 校验场景直接返回。
    if not require_ready:

        # 调用方未要求 ready 时无需继续检查。
        return

    # open_questions 缺失时按空列表处理。
    list_blockers = payload.get("open_questions", []) or ["Confirm the remaining HLS design requirements."]  # 当前未决阻塞项列表

    # ready_for_generation 为 False 或仍有 open_questions 时都不能继续生成。
    if not payload.get("ready_for_generation") or payload.get("open_questions"):

        # 拼接后的错误文本保持旧协议。
        raise ValueError(
            "> ERR: [Python] Explicit codegen plan is not ready for generation: "
            + "; ".join(str(item) for item in list_blockers)
        )

# codegen_plan 外部 JSON 载荷需要满足稳定的对象结构合同。
def validate_codegen_plan_payload(
    spec: JsonDict,
    payload: JsonDict,
    *,
    require_ready: bool,
) -> None:
    """
    校验外部传入的 codegen plan JSON 是否满足合同。

    :param spec: 当前 HLS 规范字典。
    :param payload: 待校验的 codegen plan JSON 对象。
    :param require_ready: 是否要求该 plan 立即可用于生成。
    :return: 无；载荷满足合同与 ready 要求时静默返回。
    :raises ValueError: 当载荷不满足合同要求时抛出错误。
    """

    # 顶层必须是 JSON 对象，不能是列表或字符串。
    if not isinstance(payload, dict):

        # 顶层类型错误说明 codegen_plan_path 指向了错误的 JSON 结构。
        raise ValueError("> ERR: [Python] Explicit codegen_plan_path must point to a JSON object.")

    # version=1 是当前唯一允许的计划版本。
    if payload.get("version") != 1:

        # version 不匹配意味着当前计划载荷不属于受支持的合同版本。
        raise ValueError("> ERR: [Python] Explicit codegen plan must use version=1.")

    # 外部 plan 名称必须与 spec.name 对齐。
    if payload.get("name") != spec.get("name"):

        # 外部 plan 名称必须与 spec.name 对齐，避免串错工程上下文。
        raise ValueError("> ERR: [Python] Explicit codegen plan name must match spec.name.")

    # 当前技能只接受 HLS 目标。
    if payload.get("target") != "hls":

        # target 不为 hls 时，说明调用方把其他生成目标误送到了当前技能。
        raise ValueError("> ERR: [Python] Explicit codegen plan target must be `hls`.")

    # 固定结构字段必须完整、类型正确。
    _validate_codegen_plan_structure(payload)

    # 调用方要求 ready 时，不允许保留未决问题。
    _validate_codegen_plan_ready_state(payload, require_ready=require_ready)

# requirements stage 的 JSON 载荷供 workflow 和 adapter 持久化使用。
def build_requirements_payload(spec: JsonDict) -> JsonDict:
    """
    构造 requirements 阶段使用的稳定 JSON 载荷。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: requirements 阶段消费的 JSON 对象。
    """

    # design_requirements 需要深拷贝，避免下游修改回写到调用方对象。
    dict_requirements = copy.deepcopy(_design_requirements(spec) or {})  # requirements 持久化阶段使用的 design_requirements 副本

    # 返回 requirements 阶段约定的稳定对象结构。
    return {
        "version": 1,
        "name": spec.get("name"),
        "target": "hls",
        "pipeline_required": bool(spec.get("pipeline_required", True)),
        "streamability": spec.get("streamability"),
        "transport_interface": spec.get("transport_interface"),
        "dataflow_streamability": spec.get("dataflow_streamability"),
        "interface_family": spec.get("interface_family"),
        "interface_profile": copy.deepcopy(_dict_field_or_empty(spec, "interface_profile")),
        "requirements_summary": _requirements_summary(spec),
        "design_requirements": dict_requirements,
        "confirmed_by_user": bool(dict_requirements.get("confirmed_by_user")),
    }

# codegen plan 的 module_partition 字段拆出 helper，降低主流程复杂度。
def _module_partition_section(spec: JsonDict) -> JsonDict:
    """
    构造 codegen plan 的 module_partition 字段。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: module_partition 对象。
    """

    # module_partition.top 需要优先读取 interfaces.top_function，缺失时再回退到 spec.name。
    json_dict_interfaces = _dict_field_or_empty(spec, "interfaces")  # 承载 top_function 主来源的顶层 interfaces 镜像对象

    # subfunctions 列表决定分解后的 helper 名集合。
    list_subfunctions = _list_field(spec, "subfunctions")  # module_partition.subfunctions 原始条目列表

    # 只提取子函数对象中的 name 字段。
    list_subfunction_names = [
        item.get("name")  # 子函数对象中的 name 字段
        for item in list_subfunctions  # 遍历声明过的全部子函数条目
        if isinstance(item, dict)  # 仅接受对象形态的子函数声明
    ] or [spec.get("name")]  # 缺少子函数列表时回退到顶层函数名

    # 返回 module_partition 的稳定结构。
    return {
        "top": json_dict_interfaces.get("top_function") or spec.get("name"),
        "subfunctions": list_subfunction_names,
        "decomposition_strategy": (
            "Keep HLS helper functions explicit and synthesizable."
        ),
    }

# codegen plan 的默认主体拆出 helper，降低 build_codegen_plan 的长度。
def _default_codegen_plan(spec: JsonDict) -> JsonDict:
    """
    构造不含 workflow override 的默认 codegen plan。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: 默认 codegen plan 对象。
    """

    # requirements payload 为 plan 提供稳定摘要输入。
    json_dict_requirements_payload = build_requirements_payload(spec)  # codegen plan 摘要字段的上游 requirements 载荷

    # open_questions 决定 ready_for_generation 的默认状态。
    list_open_questions = _codegen_open_questions(spec)  # 当前 plan 的未决问题列表

    # 顶层 interface_profile 需要深拷贝到 plan 中。
    dict_interface_profile = copy.deepcopy(_dict_field_or_empty(spec, "interface_profile"))  # interface_decision.profile 输出用的接口画像副本

    # design_requirements 只用于读取 confirmed_by_user。
    dict_requirements = _design_requirements(spec) or {}  # interface_decision.confirmed 读取用的需求镜像对象

    # 返回默认 codegen plan 的主体结构。
    return {
        "version": 1,
        "name": spec.get("name"),
        "target": "hls",
        "requirements_summary": json_dict_requirements_payload["requirements_summary"],
        "interface_decision": {
            "family": spec.get("interface_family"),
            "profile": dict_interface_profile,
            "confirmed": bool(dict_requirements.get("confirmed_by_user")),
        },
        "pipeline_strategy": {
            "required": bool(spec.get("pipeline_required", True)),
            "strategy": (
                "pipeline_required"
                if spec.get("pipeline_required", True)
                else "pipeline_optional"
            ),
            "notes": (
                "Use HLS PIPELINE/DATAFLOW only where it matches "
                "dependencies and memory bandwidth."
            ),
        },
        "module_partition": _module_partition_section(spec),
        "signal_width_strategy": {
            "policy": (
                "Use ap_int/ap_uint/ap_fixed or scalar C++ types that preserve "
                "the required numeric range."
            ),
        },
        "reset_clock_strategy": {
            "clock": copy.deepcopy(_dict_field_or_empty(spec, "clock")),
            "reset": copy.deepcopy(_dict_field_or_empty(spec, "reset")),
        },
        "verification_strategy": {
            "deterministic_vectors_required": True,
            "self_checking_hls_testbench_required": True,
            "vitis_readiness_required": True,
        },
        "syntax_risk_checks": _syntax_risk_checks(spec),
        "open_questions": list_open_questions,
        "ready_for_generation": not list_open_questions,
    }

# workflow override 读取逻辑拆出 helper，避免 build_codegen_plan 主流程堆积细节。
def _workflow_codegen_plan_override(spec: JsonDict) -> JsonDict | None:
    """
    读取 workflow 中的 codegen_plan_override 对象。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: workflow.codegen_plan_override 为对象时返回其值，否则返回 None。
    """

    # workflow 可能缺失，因此先做对象缩窄。
    dict_workflow = _dict_field(spec, "workflow")  # workflow 覆盖区读取用的顶层 workflow 对象

    # 只有对象 workflow 才可能包含有效 override。
    if dict_workflow is None:

        # 缺失 workflow 时直接返回 None。
        return None

    # 读取 workflow.codegen_plan_override 并保持对象缩窄。
    return _dict_field(
        dict_workflow,
        "codegen_plan_override",
    )

# codegen_plan 在 requirements 之上补齐接口、分解和验证策略。
def build_codegen_plan(spec: JsonDict) -> JsonDict:
    """
    根据已确认需求构造默认 codegen plan。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: codegen_plan JSON 对象。
    """

    # 先构造默认 plan，再允许 workflow override 局部覆盖。
    json_dict_plan = _default_codegen_plan(spec)  # 未应用 workflow override 的默认 codegen plan 对象

    # workflow override 前先保留默认 open_questions，供 setdefault 回退使用。
    list_default_open_questions = list(json_dict_plan.get("open_questions", []))  # 默认 codegen plan 计算出的 open_questions 副本

    # workflow override 允许上层工作流补充或替换默认 plan 片段。
    dict_codegen_plan_override = _workflow_codegen_plan_override(spec)  # workflow 中的 codegen_plan_override 对象

    # 覆盖项出现时允许替换默认字段。
    if dict_codegen_plan_override is not None:

        # 深拷贝后合并，保护上层 workflow 对象。
        json_dict_plan.update(copy.deepcopy(dict_codegen_plan_override))

        # open_questions 缺失时仍回退到自动生成的问题列表。
        json_dict_plan.setdefault("open_questions", list_default_open_questions)

        # ready_for_generation 缺失时按 open_questions 自动推断。
        json_dict_plan.setdefault(
            "ready_for_generation",
            not json_dict_plan.get("open_questions"),
        )

    # 返回最终 codegen plan 对象。
    return json_dict_plan

# requirements_summary 是 requirements 和 codegen plan 共享的摘要片段。
def _requirements_summary(spec: JsonDict) -> JsonDict:
    """
    构造 requirements/codegen plan 共用的需求摘要。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: 需求摘要对象。
    """

    # design_requirements 中的 confirmation_notes 需要保留到摘要里。
    dict_requirements = _design_requirements(spec) or {}  # requirements_summary.confirmation_notes 读取用的 design_requirements 镜像对象

    # interfaces 对象供读取 top_function。
    json_dict_interfaces = _dict_field_or_empty(spec, "interfaces")  # requirements_summary.top_function 读取用的顶层 interfaces 镜像对象

    # 返回最小稳定摘要字段集。
    return {
        "target": "hls",
        "pipeline_required": bool(spec.get("pipeline_required", True)),
        "streamability": spec.get("streamability"),
        "transport_interface": spec.get("transport_interface"),
        "dataflow_streamability": spec.get("dataflow_streamability"),
        "interface_family": spec.get("interface_family"),
        "top_function": json_dict_interfaces.get("top_function"),
        "confirmation_notes": dict_requirements.get("confirmation_notes", ""),
    }

# target 合同是需求确认校验的第一道边界。
def _append_target_issues(
    issues: IssueList,
    spec: JsonDict,
    requirements: JsonDict,
) -> None:
    """
    追加 target 相关的需求确认问题。

    :param issues: 待追加问题的列表。
    :param spec: 当前 HLS 规范字典。
    :param requirements: design_requirements 对象。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # 规范和需求对象都必须显式声明 hls 目标。
    if requirements.get("target") != "hls" or spec.get("target") != "hls":

        # target 错误说明调用方把非 HLS 任务误送到了当前技能。
        issues.append("HLS generator accepts only target=`hls`.")

# confirmed_by_user 只有在 require_confirmed 时才构成错误。
def _append_confirmation_issues(
    issues: IssueList,
    requirements: JsonDict,
    *,
    require_confirmed: bool,
) -> None:
    """
    追加 confirmed_by_user 相关的需求确认问题。

    :param issues: 待追加问题的列表。
    :param requirements: design_requirements 对象。
    :param require_confirmed: 是否要求显式确认。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # 未要求确认时，不把 confirmed_by_user 缺失视作错误。
    if require_confirmed and not requirements.get("confirmed_by_user"):

        # 生成前调用要求 confirmed_by_user 已经被明确置为 True。
        issues.append(
            "Generation calls require design_requirements.confirmed_by_user=true."
        )

# pipeline_required 需要同时满足类型和镜像一致性。
def _append_pipeline_issues(
    issues: IssueList,
    spec: JsonDict,
    requirements: JsonDict,
) -> None:
    """
    追加 pipeline_required 相关的需求确认问题。

    :param issues: 待追加问题的列表。
    :param spec: 当前 HLS 规范字典。
    :param requirements: design_requirements 对象。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # pipeline_required 直接决定是否强制要求 PIPELINE 策略，因此只接受布尔值。
    if not isinstance(requirements.get("pipeline_required"), bool):

        # 缺少显式布尔值会破坏生成阶段的 pipeline 合同。
        issues.append("design_requirements.pipeline_required must be a boolean.")

    # 类型合法时继续检查与顶层 spec 的镜像一致性。
    elif bool(requirements["pipeline_required"]) != bool(
        spec.get("pipeline_required", True)
    ):

        # 顶层 spec 与 design_requirements 的镜像不一致时必须阻断生成。
        issues.append(
            "design_requirements.pipeline_required must match spec.pipeline_required."
        )

# streamability 是强制字段，不允许缺失或越界。
def _append_streamability_issues(
    issues: IssueList,
    spec: JsonDict,
    requirements: JsonDict,
) -> None:
    """
    追加 streamability 相关的需求确认问题。

    :param issues: 待追加问题的列表。
    :param spec: 当前 HLS 规范字典。
    :param requirements: design_requirements 对象。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # streamability 在 design_requirements 中必须存在且合法。
    str_streamability = _string_field(requirements, "streamability")  # 需求中的 streamability

    # 非法取值直接报错。
    if str_streamability not in STREAMABILITY_VALUES:

        # 非法 streamability 值会让接口和 DATAFLOW 推断失去稳定输入。
        issues.append(
            f"streamability must be one of {', '.join(STREAMABILITY_VALUES)}."
        )

    # 合法值还需要与顶层 spec 保持一致。
    elif str_streamability != spec.get("streamability"):

        # 需求镜像与顶层 spec 的 streamability 必须保持同值。
        issues.append("design_requirements.streamability must match spec.streamability.")

# transport_interface 允许缺失，但出现时必须合法并与顶层镜像。
def _append_transport_issues(
    issues: IssueList,
    spec: JsonDict,
    requirements: JsonDict,
) -> None:
    """
    追加 transport_interface 相关的需求确认问题。

    :param issues: 待追加问题的列表。
    :param spec: 当前 HLS 规范字典。
    :param requirements: design_requirements 对象。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # transport_interface 在需求对象中可选。
    str_transport_interface = _string_field(requirements, "transport_interface")  # design_requirements 中显式填写的接口类型

    # 提供了值时必须落在合法集合内。
    if (
        str_transport_interface is not None
        and str_transport_interface not in TRANSPORT_INTERFACES
    ):

        # 非法 transport_interface 会破坏接口族推断与开放问题生成。
        issues.append(
            "transport_interface must be one of "
            + f"{', '.join(TRANSPORT_INTERFACES)}."
        )

    # 否则要求 design_requirements 与顶层 spec 保持镜像。
    elif str_transport_interface != spec.get("transport_interface"):

        # design_requirements 中的接口类型必须与顶层镜像一致。
        issues.append(
            "design_requirements.transport_interface must match spec.transport_interface."
        )

# DATAFLOW 能力字段主要约束 transport 推断结果与顶层镜像的一致性。
def _append_dataflow_issues(
    issues: IssueList,
    spec: JsonDict,
    requirements: JsonDict,
) -> None:
    """
    追加 dataflow_streamability 相关的需求确认问题。

    :param issues: 待追加问题的列表。
    :param spec: 当前 HLS 规范字典。
    :param requirements: design_requirements 对象。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # design_requirements 中可以不填 dataflow_streamability，由派生逻辑补齐。
    str_dataflow_streamability = _string_field(requirements, "dataflow_streamability")  # design_requirements 中显式填写的 DATAFLOW 能力

    # 一旦显式填写 dataflow_streamability，就必须命中受控枚举集合。
    if (
        str_dataflow_streamability is not None
        and str_dataflow_streamability not in DATAFLOW_STREAMABILITY_VALUES
    ):

        # 非法 DATAFLOW 能力会让计划阶段无法稳定推断数据通路。
        issues.append(
            "dataflow_streamability must be one of "
            + f"{', '.join(DATAFLOW_STREAMABILITY_VALUES)}."
        )

    # 显式 DATAFLOW 能力合法时，还要与顶层 spec 的镜像字段完全一致。
    elif str_dataflow_streamability != spec.get("dataflow_streamability"):

        # 顶层 spec 与需求镜像的 DATAFLOW 能力必须保持同值。
        issues.append(
            "design_requirements.dataflow_streamability "
            "must match spec.dataflow_streamability."
        )

# interface_family 决定 profile 走哪条家族分支，因此出现时必须落在受控集合。
def _append_interface_family_issues(
    issues: IssueList,
    spec: JsonDict,
    requirements: JsonDict,
) -> None:
    """
    追加 interface_family 相关的需求确认问题。

    :param issues: 待追加问题的列表。
    :param spec: 当前 HLS 规范字典。
    :param requirements: design_requirements 对象。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # interface_family 在未确认流接口方案前允许缺失。
    str_interface_family = _string_field(requirements, "interface_family")  # design_requirements 中显式填写的接口家族

    # 一旦显式填写 interface_family，就必须命中受控家族集合。
    if (
        str_interface_family is not None
        and str_interface_family not in INTERFACE_FAMILIES
    ):

        # 非法 interface_family 会让 profile 合同分支失去明确目标。
        issues.append(
            f"interface_family must be one of {', '.join(INTERFACE_FAMILIES)}."
        )

    # 接口家族合法时，还要与顶层 spec 的镜像字段完全一致。
    elif str_interface_family != spec.get("interface_family"):

        # 需求镜像中的接口家族必须与顶层 spec 完整对齐。
        issues.append(
            "design_requirements.interface_family must match spec.interface_family."
        )

# interface_profile 必须是对象，并与顶层 spec 完全镜像。
def _append_interface_profile_contract_issues(
    issues: IssueList,
    spec: JsonDict,
    requirements: JsonDict,
) -> JsonDict | None:
    """
    追加 interface_profile 基础结构问题，并返回 profile 对象。

    :param issues: 待追加问题的列表。
    :param spec: 当前 HLS 规范字典。
    :param requirements: design_requirements 对象。
    :return: 合法 profile 对象；若类型错误则返回 None。
    """

    # interface_profile 缺失时按空对象处理。
    raw_profile = requirements.get("interface_profile", {})  # design_requirements 中尚未缩窄类型的 interface_profile 原始值

    # 非对象 profile 无法继续做字段级校验。
    if not isinstance(raw_profile, dict):

        # 非对象 profile 无法进入字段级合同检查。
        issues.append("design_requirements.interface_profile must be an object.")

        # 类型错误时提前停止 profile 细化校验。
        return None

    # 顶层 spec 中的 interface_profile 也按空对象比较。
    json_dict_spec_profile = _dict_field_or_empty(spec, "interface_profile")  # 顶层 spec 中参与镜像比较的 interface_profile 对象

    # design_requirements 与顶层 spec 必须镜像一致。
    if raw_profile != json_dict_spec_profile:

        # interface_profile 的字段级确认必须与顶层 spec 严格镜像。
        issues.append(
            "design_requirements.interface_profile must match spec.interface_profile."
        )

    # 返回结构合法的 profile 对象。
    return raw_profile

# requirements 校验按问题簇拆分，避免单个函数复杂度过高。
def _requirement_confirmation_issues(
    spec: JsonDict,
    *,
    require_confirmed: bool,
) -> IssueList:
    """
    汇总 design_requirements 与顶层规范之间的合同问题。

    :param spec: 待校验的 HLS 规范字典。
    :param require_confirmed: 是否要求 confirmed_by_user=true。
    :return: 所有需求确认问题列表。
    """

    # generation 调用要求存在 design_requirements 对象。
    dict_requirements = _design_requirements(spec)  # 本轮需求镜像校验读取用的 design_requirements 对象

    # 缺失 design_requirements 时仅在 require_confirmed=True 下视作错误。
    if dict_requirements is None:

        # 保持旧行为：非确认模式下允许返回空问题列表。
        return (
            ["Generation calls require a `design_requirements` object."]
            if require_confirmed
            else []
        )

    # 所有问题统一累积到同一个列表中。
    issue_list_requirement_checks: IssueList = []  # 当前 spec 触发的需求确认问题集合

    # target 必须始终为 hls。
    _append_target_issues(issue_list_requirement_checks, spec, dict_requirements)

    # confirmed_by_user 只在生成前强制要求。
    _append_confirmation_issues(
        issue_list_requirement_checks,
        dict_requirements,
        require_confirmed=require_confirmed,
    )

    # pipeline_required 既要类型正确，也要与顶层镜像。
    _append_pipeline_issues(issue_list_requirement_checks, spec, dict_requirements)

    # streamability 是必填离散字段。
    _append_streamability_issues(issue_list_requirement_checks, spec, dict_requirements)

    # transport_interface 是可选离散字段。
    _append_transport_issues(issue_list_requirement_checks, spec, dict_requirements)

    # DATAFLOW 能力字段只在显式确认后才需要做枚举和值镜像检查。
    _append_dataflow_issues(issue_list_requirement_checks, spec, dict_requirements)

    # interface_family 只有在流接口方案被确认后才会进入严格镜像检查。
    _append_interface_family_issues(
        issue_list_requirement_checks,
        spec,
        dict_requirements,
    )

    # interface_profile 需要先做对象和镜像校验。
    dict_interface_profile = _append_interface_profile_contract_issues(  # 已通过基础结构校验、可继续细化字段的 interface_profile 对象
        issue_list_requirement_checks,  # 当前需求确认问题列表
        spec,  # 顶层 HLS 规范字典
        dict_requirements,  # design_requirements 镜像对象
    )

    # streamable 任务在生成前必须确认 interface_family。
    if (
        _string_field(dict_requirements, "streamability") == "streamable"
        and not _string_field(dict_requirements, "interface_family")
    ):

        # 流式任务在真正生成前必须先锁定接口家族。
        issue_list_requirement_checks.append(
            "Streamable tasks require an explicit interface_family "
            "confirmation before generation."
        )

    # profile 类型错误时不再继续字段级细化校验。
    if dict_interface_profile is None:

        # 直接返回当前问题列表。
        return issue_list_requirement_checks

    # 继续校验 interface_profile 的家族特定合同。
    issue_list_requirement_checks.extend(
        _interface_profile_issues(
            _string_field(dict_requirements, "interface_family"),
            dict_interface_profile,
            strict=require_confirmed,
        )
    )

    # 返回汇总后的需求确认问题列表。
    return issue_list_requirement_checks

# custom interface 在生成前必须给出非空 profile。
def _custom_interface_profile_issues(
    interface_family: str | None,
    profile: JsonDict,
) -> IssueList:
    """
    生成 custom 接口家族的 profile 问题。

    :param interface_family: 当前接口家族。
    :param profile: interface_profile 对象。
    :return: custom 接口相关问题列表。
    """

    # 所有问题统一收集到本地列表。
    issue_list_custom_profile: IssueList = []  # custom 接口家族触发的 profile 问题列表

    # custom 接口必须显式给出画像对象内容。
    if interface_family == "custom" and not profile:

        # custom 接口缺少画像时，后续代码生成无法确定协议边界。
        issue_list_custom_profile.append(
            "Custom HLS interfaces require a non-empty interface_profile."
        )

    # 返回 custom 接口问题列表。
    return issue_list_custom_profile

# native interface 不允许夹带任何 AXI 专属字段。
def _native_interface_profile_issues(
    interface_family: str | None,
    profile: JsonDict,
) -> IssueList:
    """
    生成 native 接口家族的 profile 问题。

    :param interface_family: 当前接口家族。
    :param profile: interface_profile 对象。
    :return: native 接口相关问题列表。
    """

    # 这个列表只记录 native 接口误带 AXI 字段的合同问题。
    issue_list_native_profile: IssueList = []  # native 家族触发的 AXI 禁用键问题列表

    # 只有 native 接口才需要拦截 AXI 专属键。
    if interface_family != "native":

        # 非 native 接口无需此项校验。
        return issue_list_native_profile

    # 收集所有 AXI 专属键，保持错误文本顺序稳定。
    list_forbidden_keys = sorted(  # native 接口下不允许出现的 AXI 键
        key_name  # 当前 profile 中出现的字段名
        for key_name in profile  # 扫描 native profile 中实际出现的键名
        if key_name in {*AXI_STREAM_PROFILE_KEYS, *AXI4_PROFILE_KEYS}  # 只保留 AXI 专属键
    )

    # 命中 AXI 专属键时直接报错。
    if list_forbidden_keys:

        # native 接口禁止复用任何 AXI 专属配置键。
        issue_list_native_profile.append(
            "Native interfaces must not use AXI-specific keys: "
            + ", ".join(list_forbidden_keys)
            + "."
        )

    # native 接口只返回“是否误带 AXI 配置键”这一类问题。
    return issue_list_native_profile

# AXI-Stream 的严格校验只在 require_confirmed=True 时启用。
def _axi_stream_profile_issues(
    interface_family: str | None,
    profile: JsonDict,
    *,
    strict: bool,
) -> IssueList:
    """
    生成 AXI-Stream 接口家族的 profile 问题。

    :param interface_family: 当前接口家族。
    :param profile: interface_profile 对象。
    :param strict: 是否启用生成前严格校验。
    :return: AXI-Stream 接口相关问题列表。
    """

    # 这个列表只记录 AXI-Stream 家族缺失布尔位和位宽合同的问题。
    issue_list_axi_stream_profile: IssueList = []  # AXI-Stream 家族触发的 profile 合同问题列表

    # 非严格模式或非 AXI-Stream 家族时无需继续。
    if not strict or interface_family != "axi_stream":

        # 当前 profile 校验不适用于非 AXI-Stream 或宽松模式。
        return issue_list_axi_stream_profile

    # ready / last 是 AXI-Stream 最关键的布尔语义位。
    for field_name in ("keep_ready", "keep_last"):

        # 每个字段都必须是显式布尔值。
        if not isinstance(profile.get(field_name), bool):

            # keep_ready 和 keep_last 都需要显式布尔语义，不能依赖缺省值猜测。
            issue_list_axi_stream_profile.append(
                f"AXI-Stream interface_profile requires boolean `{field_name}`."
            )

    # data_width 必须是正整数。
    if not isinstance(profile.get("data_width"), int) or int(profile["data_width"]) <= 0:

        # AXI-Stream 顶层数据口宽度必须明确为正整数。
        issue_list_axi_stream_profile.append(
            "AXI-Stream interface_profile requires a positive integer `data_width`."
        )

    # AXI-Stream 校验只输出 ready/last/data_width 三类核心合同问题。
    return issue_list_axi_stream_profile

# AXI4 的多个整数宽度字段共用同一套正整数校验逻辑。
def _append_positive_axi4_integer_issues(
    issues: IssueList,
    profile: JsonDict,
    field_names: tuple[str, ...],
) -> None:
    """
    追加 AXI4 profile 中需要为正整数的字段问题。

    :param issues: 待追加问题的列表。
    :param profile: interface_profile 对象。
    :param field_names: 需要校验的字段名元组。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # 逐字段检查正整数合同。
    for field_name in field_names:

        # 数值字段必须是正整数。
        if not isinstance(profile.get(field_name), int) or int(profile[field_name]) <= 0:

            # data_width 和 addr_width 都必须明确到正整数宽度。
            issues.append(
                f"AXI4 interface_profile requires a positive integer `{field_name}`."
            )

# AXI4 的离散选择字段共用同一套问题构造逻辑。
def _append_axi4_choice_issues(
    issues: IssueList,
    profile: JsonDict,
) -> None:
    """
    追加 AXI4 profile 的离散选择字段问题。

    :param issues: 待追加问题的列表。
    :param profile: interface_profile 对象。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # axi4_variant 必须明确区分 full / lite。
    if profile.get("axi4_variant") not in AXI4_VARIANTS:

        # axi4_variant 缺失会让 full/lite 分支策略无法确定。
        issues.append(
            f"AXI4 interface_profile requires `axi4_variant` in {', '.join(AXI4_VARIANTS)}."
        )

    # role 决定总线主动发起侧与被动响应侧的职责边界。
    if profile.get("role") not in AXI4_ROLES:

        # role 缺失会让 host/kernel 侧主从职责失去边界。
        issues.append(
            f"AXI4 interface_profile requires `role` in {', '.join(AXI4_ROLES)}."
        )

    # read_write_mode 必须明确说明访问方向。
    if profile.get("read_write_mode") not in AXI4_MODES:

        # read_write_mode 必须明确说明访问方向，避免默认假设。
        issues.append(
            "AXI4 interface_profile requires `read_write_mode` in "
            + f"{', '.join(AXI4_MODES)}."
        )

# AXI4 的 id_width 与 burst_support 约束单独集中，降低主函数复杂度。
def _append_axi4_burst_issues(
    issues: IssueList,
    profile: JsonDict,
) -> None:
    """
    追加 AXI4 profile 的 id_width 与 burst 策略问题。

    :param issues: 待追加问题的列表。
    :param profile: interface_profile 对象。
    :return: 无；发现的问题会原地追加到 issues。
    """

    # AXI4 full 变体还必须显式给出 id_width。
    if (
        profile.get("axi4_variant") == "axi4_full"
        and (
            not isinstance(profile.get("id_width"), int)
            or int(profile["id_width"]) <= 0
        )
    ):

        # axi4_full 需要 id_width 才能确定事务标识宽度合同。
        issues.append(
            "AXI4 full interface_profile requires a positive integer `id_width`."
        )

    # burst_support 必须明确为布尔值。
    if not isinstance(profile.get("burst_support"), bool):

        # burst_support 需要显式布尔值，不能依赖缺省推断。
        issues.append("AXI4 interface_profile requires boolean `burst_support`.")

    # 开启 burst_support 时必须给出最大 burst 长度。
    if profile.get("burst_support") and (
        not isinstance(profile.get("max_burst_len"), int)
        or int(profile["max_burst_len"]) <= 0
    ):

        # 开启 burst_support 后必须给出合法的最大 burst 长度。
        issues.append(
            "AXI4 interface_profile requires positive integer `max_burst_len` "
            "when burst_support=true."
        )

# AXI4 profile 的字段更多，因此单独拆出专门 helper。
def _axi4_profile_issues(
    interface_family: str | None,
    profile: JsonDict,
    *,
    strict: bool,
) -> IssueList:
    """
    生成 AXI4 接口家族的 profile 问题。

    :param interface_family: 当前接口家族。
    :param profile: interface_profile 对象。
    :param strict: 是否启用生成前严格校验。
    :return: AXI4 接口相关问题列表。
    """

    # 这个列表只记录 AXI4 家族的离散字段、宽度和 burst 合同问题。
    issue_list_axi4_profile: IssueList = []  # AXI4 家族触发的离散字段、位宽与 burst 合同问题列表

    # 只有严格模式下的 AXI4 家族才需要执行后续字段级合同检查。
    if not strict or interface_family != "axi4":

        # 非 AXI4 或宽松模式下不需要展开 AXI4 专属合同检查。
        return issue_list_axi4_profile

    # 先校验离散选择字段。
    _append_axi4_choice_issues(issue_list_axi4_profile, profile)

    # data_width 与 addr_width 都必须是正整数。
    _append_positive_axi4_integer_issues(
        issue_list_axi4_profile,
        profile,
        ("data_width", "addr_width"),
    )

    # 再校验 id_width 与 burst 策略字段。
    _append_axi4_burst_issues(issue_list_axi4_profile, profile)

    # AXI4 校验只输出离散字段、位宽和 burst 策略相关的问题。
    return issue_list_axi4_profile

# interface_profile 校验按家族拆开，降低复杂度并保持文本合同稳定。
def _interface_profile_issues(
    interface_family: Any,
    profile: JsonDict,
    *,
    strict: bool,
) -> IssueList:
    """
    汇总 interface_profile 的家族特定问题。

    :param interface_family: 当前接口家族。
    :param profile: interface_profile 对象。
    :param strict: 是否启用生成前严格校验。
    :return: 所有 interface_profile 问题列表。
    """

    # 先把 Any 缩窄成字符串或 None，避免后续逻辑携带模糊类型。
    str_interface_family = interface_family if isinstance(interface_family, str) else None  # 归一化后的接口家族

    # 所有问题统一汇总到一个列表中返回。
    issue_list_interface_profile: IssueList = []  # interface_profile 家族特定问题列表

    # custom 接口要求显式 profile 内容。
    issue_list_interface_profile.extend(
        _custom_interface_profile_issues(str_interface_family, profile)
    )

    # native 接口不允许出现 AXI 专属字段。
    issue_list_interface_profile.extend(
        _native_interface_profile_issues(str_interface_family, profile)
    )

    # AXI-Stream 严格校验只在 require_confirmed=True 时启用。
    issue_list_interface_profile.extend(
        _axi_stream_profile_issues(
            str_interface_family,
            profile,
            strict=strict,
        )
    )

    # AXI4 严格校验同样只在 require_confirmed=True 时启用。
    issue_list_interface_profile.extend(
        _axi4_profile_issues(
            str_interface_family,
            profile,
            strict=strict,
        )
    )

    # 返回所有 interface_profile 问题。
    return issue_list_interface_profile

# open_questions 需要去重，避免 pattern 问题和需求问题重复出现。
def _append_unique_question(questions: list[str], question: str) -> None:
    """
    向 open_questions 列表中追加去重后的问题。

    :param questions: 待追加问题的列表。
    :param question: 候选问题文本。
    :return: 无；未重复的问题会原地追加到 questions。
    """

    # 仅当问题尚未出现时才追加。
    if question not in questions:

        # 首次出现的问题按自然顺序落入 open_questions。
        questions.append(question)

# AXI-Stream 缺字段问题拆出 helper，降低接口问题构造函数复杂度。
def _append_axi_stream_open_questions(
    questions: list[str],
    interface_profile: JsonDict,
) -> None:
    """
    追加 AXI-Stream 接口画像缺口问题。

    :param questions: 待追加问题的列表。
    :param interface_profile: 顶层 interface_profile 对象。
    :return: 无；缺失字段会原地追加到 questions。
    """

    # keep_ready / keep_last / data_width 是 AXI-Stream 的必需字段。
    for field_name in ("keep_ready", "keep_last", "data_width"):

        # 缺失字段都需要单独询问。
        if field_name not in interface_profile:

            # keep_ready、keep_last 和 data_width 都属于 AXI-Stream 最小合同字段。
            _append_unique_question(
                questions,
                f"Confirm the AXI-Stream `{field_name}` field.",
            )

# AXI4 开放问题 helper 负责把缺口字段转成逐项确认问题。
def _append_axi4_open_questions(
    questions: list[str],
    interface_profile: JsonDict,
) -> None:
    """
    追加 AXI4 接口画像缺口问题。

    :param questions: 待追加问题的列表。
    :param interface_profile: 顶层 interface_profile 对象。
    :return: 无；缺失字段会原地追加到 questions。
    """

    # AXI4 的这些字段在生成前都必须确认。
    for field_name in (
        "axi4_variant",
        "role",
        "read_write_mode",
        "data_width",
        "addr_width",
        "burst_support",
    ):

        # AXI4 基础字段缺失时，要为每个字段单独生成确认问题。
        if field_name not in interface_profile:

            # AXI4 六个基础字段缺失任一项都需要单独补问。
            _append_unique_question(
                questions,
                f"Confirm the AXI4 `{field_name}` field.",
            )

    # AXI4 full 变体还需要显式确认 id_width。
    if (
        interface_profile.get("axi4_variant") == "axi4_full"
        and "id_width" not in interface_profile
    ):

        # axi4_full 额外要求确认 id_width，避免遗漏事务标识宽度。
        _append_unique_question(questions, "Confirm the AXI4 full id width.")

    # 开启 burst_support 时还需要确认最大 burst 长度。
    if interface_profile.get("burst_support") and "max_burst_len" not in interface_profile:

        # 启用 burst_support 后还必须补齐最大 burst 长度。
        _append_unique_question(
            questions,
            "Confirm the AXI4 maximum burst length.",
        )

# interface family 与 profile 的未确认项会进入 open_questions。
def _append_interface_open_questions(
    questions: list[str],
    spec: JsonDict,
) -> None:
    """
    追加接口相关的待确认问题。

    :param questions: 待追加问题的列表。
    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: 无；待确认接口问题会原地追加到 questions。
    """

    # open_questions 阶段没有 interface_profile 时按空对象扫描缺口。
    json_dict_profile = _dict_field_or_empty(spec, "interface_profile")  # 参与 open_questions 构造的顶层 interface_profile 对象

    # streamable 且未确认家族时，必须先问清接口家族。
    if spec.get("streamability") == "streamable" and not spec.get("interface_family"):

        # 流式任务未锁定接口家族时，先补问顶层协议方向。
        _append_unique_question(
            questions,
            "Confirm whether the streamable HLS task should use AXI-Stream, "
            "AXI4, native, or custom interfaces.",
        )

    # AXI-Stream 缺字段时逐项生成待确认问题。
    if spec.get("interface_family") == "axi_stream":

        # AXI-Stream 场景继续补齐 ready/last/data_width 等画像字段。
        _append_axi_stream_open_questions(questions, json_dict_profile)

    # AXI4 家族的开放问题需要继续补齐变体、角色、位宽与 burst 字段。
    if spec.get("interface_family") == "axi4":

        # AXI4 场景继续补齐变体、角色、宽度和 burst 字段。
        _append_axi4_open_questions(questions, json_dict_profile)

# open_questions 默认会暴露“未确认需求”这一类最小阻塞项。
def _append_confirmation_open_questions(
    questions: list[str],
    spec: JsonDict,
) -> None:
    """
    追加需求确认相关的待确认问题。

    :param questions: 待追加问题的列表。
    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: 无；待确认需求问题会原地追加到 questions。
    """

    # open_questions 阶段只读取 design_requirements 中的 confirmed_by_user 状态。
    dict_requirements = _design_requirements(spec) or {}  # open_questions 阶段读取 confirmed_by_user 的需求镜像

    # confirmed_by_user 不为 True 时需要生成统一确认问题。
    if not dict_requirements.get("confirmed_by_user"):

        # confirmed_by_user 仍为 False 时，需要把最小确认问题暴露给调用方。
        _append_unique_question(
            questions,
            "Confirm the HLS target, pipeline requirement, and interface choice "
            "with the user.",
        )

# codegen_plan open_questions 汇总需求缺口、pattern 缺口和镜像问题。
def _codegen_open_questions(spec: JsonDict) -> list[str]:
    """
    构造 codegen plan 阶段的 open_questions 列表。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: 生成前仍需确认的问题列表。
    """

    # 所有待确认问题都汇总到这个列表中。
    list_questions: list[str] = []  # codegen plan 阶段仍需人工确认的问题列表

    # 先追加统一的确认问题。
    _append_confirmation_open_questions(list_questions, spec)

    # 再追加接口家族和 profile 缺口问题。
    _append_interface_open_questions(list_questions, spec)

    # pattern 模块会补充 pattern-specific 的开放问题。
    for str_question in pattern_open_questions(spec):

        # pattern 模块生成的问题也要复用统一去重入口。
        _append_unique_question(list_questions, str_question)

    # require_confirmed=False 时的需求问题会作为开放问题暴露出来。
    for str_issue in _requirement_confirmation_issues(
        spec,
        require_confirmed=False,
    ):

        # require_confirmed=False 的合同缺口也会进入开放问题列表。
        _append_unique_question(list_questions, str_issue)

    # 返回汇总后的 open_questions 列表。
    return list_questions

# syntax_risk_checks 是 prompt/codegen plan 共享的生成边界清单。
def _syntax_risk_checks(spec: JsonDict) -> list[str]:
    """
    构造 codegen plan 中的 syntax_risk_checks 列表。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: 生成阶段需要保持的语法与结构风险检查项。
    """

    # 先放入所有任务通用的基础风险检查项。
    list_checks = [  # 基础语法风险检查项
        "Reject placeholder text, undefined symbols, missing output artifacts, "
        "and non-HLS source extensions.",  # 生成结果的基本卫生检查
        "Keep the HLS implementation aligned with the confirmed vectors and "
        "self-checking HLS validation path.",  # HLS 实现与当前 HLS 验证路径一致性
        "Keep hls_config.cfg syn.top and syn.file entries exact.",  # hls_config.cfg 顶层入口一致性
    ]

    # pipeline_required=true 时必须至少保留一个有理由的 PIPELINE pragma。
    if spec.get("pipeline_required", True):

        # pipeline_required=true 时必须保留至少一个有理由的 PIPELINE pragma。
        list_checks.append(
            "Require at least one justified #pragma HLS PIPELINE when "
            "pipeline_required=true."
        )

    # AXI-Stream 任务需要保留 ready/last/data_width 语义。
    if spec.get("interface_family") == "axi_stream":

        # AXI-Stream 场景下要守住 ready/last/data_width 三类确认语义。
        list_checks.append(
            "Preserve confirmed AXI-Stream ready/last/data-width semantics."
        )

    # AXI4 任务需要保留变体、角色和 burst 策略。
    if spec.get("interface_family") == "axi4":

        # AXI4 场景下要守住变体、角色、宽度与 burst 策略。
        list_checks.append(
            "Preserve confirmed AXI4 variant, role, widths, and burst policy."
        )

    # pattern_definition 提供更适合展示给模型的 label。
    dict_pattern_definition = pattern_definition(spec)  # pattern 定义对象

    # 有 label 时把 pattern 元数据保持要求写进检查项。
    if dict_pattern_definition.get("label"):

        # pattern label 已被确认时，需要继续保持对应元数据和注释语义一致。
        list_checks.append(
            "Preserve the confirmed "
            + f"{dict_pattern_definition['label']} pattern metadata "
            + "and keep comments aligned with it."
        )

    # canonical_pattern_name 用于决定 pattern-specific 检查项。
    str_pattern_name = canonical_pattern_name(spec)  # 归一化后的 pattern 名称

    # array_partition / array_reshape 都要限制同变量混用。
    if str_pattern_name in {"array_partition", "array_reshape"}:

        # array_partition 与 array_reshape 都必须绑定到已确认的瓶颈维度。
        list_checks.append(
            "Tie partition/reshape choices to the confirmed memory bottleneck "
            "dimension and do not combine ARRAY_PARTITION with ARRAY_RESHAPE "
            "on the same variable."
        )

    # DATAFLOW pattern 需要固定 stage 边界和通道深度。
    if str_pattern_name == "dataflow":

        # DATAFLOW 场景下必须保留 stage 边界、通道深度与协同仿真要求。
        list_checks.append(
            "Preserve explicit read/compute/write stage boundaries, channel "
            "depths, and the requirement for DATAFLOW co-simulation review."
        )

    # 多路 m_axi pattern 需要保留 bundle 映射与并发关系。
    if str_pattern_name == "multi_m_axi":

        # multi_m_axi 场景下必须保留 bundle 映射和读写并发关系。
        list_checks.append(
            "Keep the confirmed bundle map and independent traffic groups "
            "aligned with the intended read/write concurrency."
        )

    # fixed_point pattern 需要保留数值格式相关合同。
    if str_pattern_name == "fixed_point":

        # fixed_point 场景下必须保留数值格式、误差预算与测试向量合同。
        list_checks.append(
            "Preserve fixed-point numeric range, integer bits, quantization "
            "mode, overflow mode, and error budget in code comments and "
            "test vectors."
        )

    # minimal_vitis_pipeline pattern 需要保持 compile/link 边界清晰。
    if str_pattern_name == "minimal_vitis_pipeline":

        # minimal_vitis_pipeline 场景下要维持 compile/link 边界清晰。
        list_checks.append(
            "Keep the compile/link boundary clear and avoid mixing package "
            "or host orchestration into the generated HLS source."
        )

    # host_kernel_split pattern 需要保持文件职责分离。
    if str_pattern_name == "host_kernel_split":

        # host_kernel_split 场景下要保持主核、辅助头文件、测试和 cfg 的职责分离。
        list_checks.append(
            "Keep the main kernel source, helper headers, testbench, and cfg "
            "roles distinct, and concentrate dense pragmas in hotspot files "
            "instead of every file."
        )

    # 返回完整的 syntax_risk_checks 列表。
    return list_checks
