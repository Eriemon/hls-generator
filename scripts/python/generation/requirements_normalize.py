"""requirements 默认值解析、摘要组装与 plan 归一化 helper。"""

# 延迟注解求值，避免运行时解析前向引用。
from __future__ import annotations

# 深拷贝用于保护调用方输入副本与嵌套对象。
import copy

# 根 requirements 模块提供共享类型别名与稳定公共入口。
from scripts.python.generation.requirements import (
    DATAFLOW_STREAMABILITY_VALUES,
    JsonDict,
    build_requirements_payload,
)

# parse 子模块承接 spec 读取、字段缩窄和显式需求推断逻辑。
from scripts.python.generation.requirements_parse import (
    _design_requirements,
    _dict_field,
    _dict_field_or_empty,
)

# parse 子模块继续提供列表/字符串字段缩窄 helper。
from scripts.python.generation.requirements_parse import (
    _list_field,
    _string_field,
)

# parse 子模块继续提供显式需求语义推断入口。
from scripts.python.generation.requirements_parse import (
    detect_dataflow_streamability,
    detect_streamability,
    detect_transport_interface,
)

# validate 子模块继续维护开放问题与语法风险生成逻辑。
from scripts.python.generation.requirements_validate import (
    _codegen_open_questions,
    _syntax_risk_checks,
)

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
    json_dict_interfaces: JsonDict = _dict_field_or_empty(spec, "interfaces")  # requirements_summary.top_function 读取用的顶层 interfaces 镜像对象

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

# codegen plan 的 module_partition 字段拆出 helper，降低主流程复杂度。
def _module_partition_section(spec: JsonDict) -> JsonDict:
    """
    构造 codegen plan 的 module_partition 字段。

    :param spec: 已补齐默认值的 HLS 规范字典。
    :return: module_partition 对象。
    """

    # module_partition.top 需要优先读取 interfaces.top_function，缺失时再回退到 spec.name。
    json_dict_interfaces: JsonDict = _dict_field_or_empty(spec, "interfaces")  # 承载 top_function 主来源的顶层 interfaces 镜像对象

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
