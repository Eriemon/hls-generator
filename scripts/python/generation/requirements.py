"""HLS 需求确认、需求载荷整理与 codegen plan 构造工具。"""

# 延迟注解求值，避免运行时解析前向引用。
from __future__ import annotations

# 深拷贝用于保护调用方输入。
import copy
from typing import Any

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
    json_dict_resolved_defaults: JsonDict = _resolved_requirement_defaults(dict_spec, override_options)  # 待写回 spec 的 requirements/default 解析结果集合

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
    issue_list_confirmation: IssueList = _requirement_confirmation_issues(spec, require_confirmed=True)  # 本次生成前校验收集到的需求确认问题列表

    # 有问题时保持旧行为：抛出首个错误文本。
    if issue_list_confirmation:

        # 首个问题就是 CLI / workflow 暴露给用户的错误文本。
        raise ValueError(
            f"> ERR: [Python] Requirement confirmation failed: {issue_list_confirmation[0]}"
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

# codegen_plan 在 requirements 之上补齐接口、分解和验证策略。
def build_codegen_plan(spec: JsonDict) -> JsonDict:
    """
    根据已确认需求构造默认 codegen plan。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: codegen_plan JSON 对象。
    """

    # 先构造默认 plan，再允许 workflow override 局部覆盖。
    json_dict_plan: JsonDict = _default_codegen_plan(spec)  # 未应用 workflow override 的默认 codegen plan 对象

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

# 解析与文本推断 helper 收敛到 parse 子模块，根文件只保留稳定导出面。
from scripts.python.generation.requirements_parse import (
    _design_requirements,
    _dict_field_or_empty,
    detect_dataflow_streamability,
    detect_streamability,
    detect_transport_interface,
)

# 校验与开放问题生成逻辑收敛到 validate 子模块，根文件保留稳定入口。
from scripts.python.generation.requirements_validate import (
    _requirement_confirmation_issues,
    _validate_codegen_plan_ready_state,
    _validate_codegen_plan_structure,
)

# 默认值解析、摘要组装与 plan 构造逻辑收敛到 normalize 子模块。
from scripts.python.generation.requirements_normalize import (
    _default_codegen_plan,
    _requirements_summary,
    _resolved_requirement_defaults,
    _workflow_codegen_plan_override,
    _write_requirement_defaults_to_spec,
)
