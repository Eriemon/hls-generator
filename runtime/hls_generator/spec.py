"""HLS 生成规范的脚手架、归一化与结构校验工具。"""

# 延迟注解求值，避免运行时解析前向引用。
from __future__ import annotations

# 深拷贝用于保护调用方输入，JSON 读写用于规范文件落盘。
import copy
import json
import re
from pathlib import Path
from typing import Any

# 当前技能只接受 HLS 目标，保留元组常量供校验逻辑复用。
TARGETS = ("hls",)  # 允许的目标类型集合

# 主规范必须具备这些字段，normalize_spec 会按此集合补齐默认值。
SPEC_FIELDS = (  # HLS 顶层规范字段清单
    "name",  # 主设计名字段
    "target",  # 显式声明目标类型
    "design_requirements",  # 收纳顶层设计约束
    "streamability",  # 描述主计算的流式倾向
    "transport_interface",  # 声明顶层传输接口类型
    "dataflow_streamability",  # 描述 dataflow 阶段组织方式
    "interface_family",  # 归类高层接口家族
    "interface_profile",  # 记录细粒度接口参数
    "pipeline_required",  # 声明是否必须启用 pipeline
    "codegen_plan_required",  # 声明是否必须先生成代码计划
    "codegen_plan_path",  # 记录代码计划文件路径
    "description",  # 提供任务主语义说明
    "interfaces",  # 汇总顶层接口合同
    "behavior",  # 列出核心行为条目
    "clock",  # 描述时钟配置
    "reset",  # 描述复位策略
    "constraints",  # 汇总约束条目
    "outputs",  # 声明预期交付物
    "notes",  # 保留补充备注
    "subfunctions",  # 收纳子函数拆分
    "workflow",  # 记录流程级配置
    "performance",  # 记录性能目标
    "hls_profile",  # 保存 HLS 配置画像
)

# 子函数描述使用更窄的字段合同，供规范化和校验共用。
SUBFUNCTION_FIELDS = (  # 子函数条目必需字段
    "name",  # 子函数名字段
    "inputs",  # 列出子函数输入
    "outputs",  # 列出子函数输出
    "behavior",  # 描述子函数行为
    "constraints",  # 记录子函数约束
    "dependencies",  # 声明依赖关系
    "source_references",  # 保留参考来源
    "test_intent",  # 记录验证意图
)

# 这些字段允许字符串或对象输入，最终都会标准化成字典列表。
INFO_DICTIONARY_FIELDS = ("behavior", "constraints", "test_intent")  # 需要归一化为信息字典列表的字段

# 允许输出的 HLS 源文件后缀集中在 C/C++ 与头文件。
HLS_SOURCE_SUFFIXES = {".cpp", ".cc", ".cxx", ".h", ".hpp"}  # HLS 源码与头文件后缀

# HLS 工程配置统一使用 cfg 文件保存。
HLS_CONFIG_SUFFIXES = {".cfg"}  # HLS 配置文件后缀

# HLS-only 边界明确拒绝手写 RTL 产物。
REJECTED_HARDWARE_LANGUAGES = {"verilog", "systemverilog", "sv", "rtl"}  # 禁止的硬件语言标识

# 规范错误统一使用 SpecError 暴露给 CLI 和 workflow。
class SpecError(ValueError):
    """表示 HLS 生成规范不满足当前技能合同的异常。"""

# 生成默认接口时，统一使用最小的三参数 kernel 形态。
def _default_interfaces(str_top_function: str) -> dict[str, Any]:
    """
    构造默认的 HLS 顶层接口定义。

    :param str_top_function: 顶层 kernel 函数名。
    :return: 带有 m_axi 与 s_axilite 默认参数的接口字典。
    """

    # 返回最小可工作的示例接口，供脚手架与补默认值使用。
    return {
        "top_function": str_top_function,
        "arguments": [
            {
                "name": "input",
                "type": "const ap_uint<32> *",
                "direction": "input",
                "interface": "m_axi",
                "bundle": "gmem0",
            },
            {
                "name": "output",
                "type": "ap_uint<32> *",
                "direction": "output",
                "interface": "m_axi",
                "bundle": "gmem1",
            },
            {
                "name": "length",
                "type": "int",
                "direction": "input",
                "interface": "s_axilite",
            },
        ],
        "control": "s_axilite",
    }

# 默认行为说明提醒调用方补充计算语义和边界条件。
def _default_behavior() -> list[str]:
    """
    返回脚手架默认的行为说明列表。

    参数:
        无外部业务参数；函数仅返回脚手架默认值。

    返回:
        供新建规范直接落盘的默认行为描述。
    """

    # 初始说明强调吞吐、访存与边界条件三类信息。
    return [
        "Describe kernel computation, memory access pattern, throughput goal, and edge cases here."
    ]

# 默认约束用于提醒模型保持 Vitis HLS 可综合边界。
def _default_constraints() -> list[str]:
    """
    返回脚手架默认的 HLS 约束列表。

    参数:
        无外部业务参数；函数仅返回脚手架默认约束。

    返回:
        供新建规范直接复用的默认约束说明。
    """

    # 这些约束覆盖类型选择、pragma 与不支持特性。
    return [
        "Use Vitis HLS compatible C++.",
        "Use fixed-width ap_int/ap_uint/ap_fixed types where appropriate.",
        "Add interface pragmas and pipeline/dataflow pragmas justified by the access pattern.",
        "Avoid dynamic memory, recursion, exceptions, RTTI, and unsupported standard library features.",
    ]

# 默认输出布局固定生成头文件、源码、testbench 与 cfg。
def _default_outputs(str_top_function: str) -> list[dict[str, str]]:
    """
    构造脚手架默认的输出文件布局。

    :param str_top_function: 顶层 kernel 函数名。
    :return: 供规范落盘的默认输出文件条目列表。
    """

    # 输出路径和文件种类与现有 runtime 工作流保持一致。
    return [
        {"path": f"src/{str_top_function}.h", "kind": "header", "language": "cpp"},
        {"path": f"src/{str_top_function}.cpp", "kind": "source", "language": "cpp"},
        {"path": f"tb/{str_top_function}_tb.cpp", "kind": "testbench", "language": "cpp"},
        {"path": "hls_config.cfg", "kind": "config", "language": "ini"},
    ]

# 对名称做最小合法化处理，避免直接把原始文本写进文件名和符号名。
def sanitize_name(name: str) -> str:
    """
    把输入名称转成适合 HLS 规范使用的标识符。

    :param name: 原始名称文本。
    :return: 仅保留字母数字和下划线的规范化名称；空名时回退到默认 kernel 名。
    """

    # 先去掉前后空白，再把非单词字符压缩成下划线。
    str_cleaned_name = re.sub(r"\W+", "_", name.strip()).strip("_")  # 基础规范化后的名称

    # 空字符串无法作为后续 kernel 名称使用，直接回退默认值。
    if not str_cleaned_name:

        # 返回稳定默认名，避免生成空文件名。
        return "hls_kernel"

    # 数字开头的名称会破坏 C/C++ 标识符合法性。
    if str_cleaned_name[0].isdigit():

        # 追加 design_ 前缀，保留原始数字主体。
        str_cleaned_name = f"design_{str_cleaned_name}"  # 数字前缀保护后的名称

    # 返回最终可用的规范名称。
    return str_cleaned_name

# 新建脚手架时集中补齐 runtime 期望的所有主字段。
def scaffold_spec(target: str = "hls", name: str | None = None) -> dict[str, Any]:
    """
    创建一份最小可运行的 HLS 规范脚手架。

    :param target: 期望目标类型；当前只允许 hls。
    :param name: 可选的 kernel 名称。
    :return: 补齐默认字段后的 HLS 规范字典。
    """

    # 脚手架阶段先拦住非 HLS 目标，避免落地错误模板。
    _require_target(target)

    # 规范化顶层名称，确保后续路径和函数名稳定。
    str_spec_name = sanitize_name(name or "hls_kernel")  # 规范化后的主设计名

    # 顶层函数名需要保留 _kernel 后缀，与当前示例与工作流对齐。
    str_top_function = (
        str_spec_name if str_spec_name.endswith("_kernel") else f"{str_spec_name}_kernel"  # 补齐 _kernel 后缀后的顶层函数名
    )  # 默认顶层函数名

    # 返回完整脚手架，供 CLI scaffold 和 normalize 默认值复用。
    return {
        "name": str_spec_name,
        "target": "hls",
        "design_requirements": {},
        "streamability": "unknown",
        "transport_interface": "unknown",
        "dataflow_streamability": "unknown",
        "interface_family": None,
        "interface_profile": {},
        "pipeline_required": True,
        "codegen_plan_required": True,
        "codegen_plan_path": None,
        "description": "Implement a Vitis HLS C++ kernel.",
        "interfaces": _default_interfaces(str_top_function),
        "behavior": _default_behavior(),
        "clock": {"period_ns": 10.0, "uncertainty_ns": 1.0},
        "reset": {"strategy": "tool_default"},
        "constraints": _default_constraints(),
        "outputs": _default_outputs(str_top_function),
        "notes": [],
        "subfunctions": [],
        "workflow": {},
        "performance": {},
        "hls_profile": {},
    }

# 从外部输入构造 runtime 可消费的稳定 spec 结构。
def normalize_spec(raw: dict[str, Any], target: str | None = None) -> dict[str, Any]:
    """
    归一化外部传入的 HLS 规范。

    :param raw: 用户或上游流程提供的原始 spec 字典。
    :param target: 可选目标类型覆盖；当前只允许 hls。
    :return: 字段齐全、结构合法的 HLS 规范字典。
    :raises SpecError: 输入不是对象、target 冲突或字段结构不合法时抛出。
    """

    # 归一化入口只接受对象，避免列表或标量输入污染后续逻辑。
    if not isinstance(raw, dict):

        # 非对象输入无法继续做字段级校验。
        raise SpecError("> ERR: [Python] Spec must be a JSON object.")

    # 用户显式 target 优先，其次回退到原始 spec 或默认 hls。
    str_requested_target = _require_target(str(target or raw.get("target") or "hls"))  # 归一化后的目标类型

    # 单独保留原始 target 字段，便于报冲突错误。
    obj_raw_target = raw.get("target")  # 原始输入中的 target 字段

    # 调用方 target 与 spec target 不能相互矛盾。
    if obj_raw_target and str(obj_raw_target).lower() != str_requested_target:

        # 直接报冲突，避免在错误 target 下继续规范化。
        raise SpecError(
            "> ERR: [Python] "
            f"Spec target {obj_raw_target!r} does not match requested target {str_requested_target!r}."
        )

    # 历史 RTL 字段在 HLS-only 技能中必须被显式拦截。
    _reject_legacy_target_fields(raw)

    # 原始 name 缺失时沿用脚手架默认主设计名。
    str_name = sanitize_name(str(raw.get("name") or scaffold_spec(str_requested_target)["name"]))  # 原始输入回退脚手架后的主设计名

    # 先构造完整默认脚手架，再把用户字段覆写进去。
    dict_spec = scaffold_spec(str_requested_target, name=str_name)  # 待归一化的完整 spec

    # 只复制白名单字段，防止未知字段悄悄进入 runtime 合同。
    for str_key in raw:

        # 命中主字段白名单时保留调用方原始数据。
        if str_key in SPEC_FIELDS:

            # 深拷贝避免后续原地修改反向影响调用方对象。
            dict_spec[str_key] = copy.deepcopy(raw[str_key])  # 当前白名单字段的深拷贝值

    # 名称最终仍要再次清洗，避免用户覆写后带入非法字符。
    dict_spec["name"] = sanitize_name(str(dict_spec["name"]))  # 重新清洗后的主设计名

    # 目标字段固定收敛为 hls，防止大小写差异传播。
    dict_spec["target"] = "hls"  # 固定收敛后的目标类型

    # 设计需求字段允许空值，但最终必须是对象。
    dict_spec["design_requirements"] = _normalize_design_requirements(  # 归一化后的设计需求对象
        dict_spec.get("design_requirements")  # 上游补充的设计约束对象
    )

    # streamability 统一折叠到有限枚举。
    dict_spec["streamability"] = _normalize_streamability(  # 归一化后的流式能力枚举
        dict_spec.get("streamability")  # 用户声明的主计算流式倾向
    )

    # 传输接口字段也收敛到受控枚举。
    dict_spec["transport_interface"] = _normalize_transport_interface(  # 归一化后的传输接口枚举
        dict_spec.get("transport_interface")  # 用户声明的顶层传输接口类型
    )

    # dataflow 对应的流式能力使用独立枚举集合。
    dict_spec["dataflow_streamability"] = _normalize_dataflow_streamability(  # 归一化后的 dataflow 流式能力
        dict_spec.get("dataflow_streamability")  # 用户声明的 dataflow 组织方式
    )

    # 接口家族字段允许缺失，但存在时必须合法。
    dict_spec["interface_family"] = _normalize_interface_family(  # 归一化后的接口家族
        dict_spec.get("interface_family")  # 用户声明的高层接口家族
    )

    # 接口 profile 保持为字典，供更细粒度流程继续解析。
    dict_spec["interface_profile"] = _normalize_interface_profile(  # 归一化后的接口 profile 对象
        dict_spec.get("interface_profile")  # 用户声明的细粒度接口参数
    )

    # pipeline 配置缺失时仍默认开启，保持当前 HLS 生成偏好。
    raw_pipeline_value: Any = dict_spec.get("pipeline_required")  # 读取上游传入的 pipeline 取值

    # 把 pipeline 偏好写回统一布尔值，便于调度阶段直接读取。
    dict_spec["pipeline_required"] = _normalize_bool(  # 写回统一布尔化后的 pipeline 配置
        raw_pipeline_value,  # 当前待归一化的 pipeline 开关值
        "pipeline_required",  # pipeline 布尔字段名
        default=True,  # 缺省时回退为开启
    )

    # codegen plan 缺失时仍要求先产出计划，避免直接跳过拆解与审查。
    raw_codegen_plan_value: Any = dict_spec.get("codegen_plan_required")  # 读取上游声明的计划前置开关

    # 把计划前置要求收敛成布尔值，便于下游统一判断是否先出计划。
    dict_spec["codegen_plan_required"] = _normalize_bool(  # 写回统一布尔化后的计划前置要求
        raw_codegen_plan_value,  # 当前待归一化的计划开关值
        "codegen_plan_required",  # 计划前置要求字段名
        default=True,  # 缺省时仍要求先生成计划
    )

    # 空路径统一折叠成 None，避免下游区分空字符串与缺失。
    dict_spec["codegen_plan_path"] = None if dict_spec.get("codegen_plan_path") in (None, "") else str(  # 归一化后的 codegen plan 路径
        dict_spec.get("codegen_plan_path")  # 非空时收敛为稳定字符串路径
    )

    # 子函数字段递归归一化，保证结构和内部列表合同一致。
    dict_spec["subfunctions"] = [  # 归一化后的子函数列表
        normalize_subfunction(obj_subfunction, int_index)  # 当前子函数条目交给递归归一化
        for int_index, obj_subfunction in enumerate(dict_spec.get("subfunctions", []))  # 保留原始顺序生成稳定索引
    ]

    # 最终结构校验负责拦住缺字段与类型错误。
    _validate_shape(dict_spec)

    # 返回 runtime 与 integration 都可复用的稳定 spec。
    return dict_spec

# 子函数合同允许部分简写输入，这里统一折叠成完整对象。
def normalize_subfunction(subfunction: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """
    归一化单个 subfunction 规范条目。

    :param subfunction: 原始子函数字典。
    :param index: 当前子函数在列表中的位置，用于生成默认名称。
    :return: 字段齐全的子函数规范字典。
    :raises SpecError: 子函数不是对象时抛出。
    """

    # 子函数必须是对象，否则无法逐字段补齐。
    if not isinstance(subfunction, dict):

        # 非对象输入直接视为合同错误。
        raise SpecError("> ERR: [Python] Each subfunction must be an object.")

    # 深拷贝后再补字段，避免修改调用方持有的原始对象。
    dict_normalized_subfunction = copy.deepcopy(subfunction)  # 待归一化的子函数对象

    # 子函数缺名时自动生成稳定顺序名。
    dict_normalized_subfunction["name"] = sanitize_name(  # 归一化后的子函数名称
        str(dict_normalized_subfunction.get("name") or f"subfunction_{index + 1}")  # 缺名时按索引生成稳定子函数名
    )

    # 这些字段统一落成列表，方便后续迭代和验证。
    for str_field_name in ("inputs", "outputs", "dependencies", "source_references"):

        # 缺字段时回退空列表，避免后续 isinstace 分支分散。
        dict_normalized_subfunction[str_field_name] = _as_list(  # 当前字段归一化后的列表值
            dict_normalized_subfunction.get(str_field_name, [])  # 当前列表字段缺省时回退空列表
        )

    # 这些字段统一落成带 id/text/evidence 的信息字典列表。
    for str_field_name in INFO_DICTIONARY_FIELDS:

        # 归一化后结构可直接被 planning 和 validation 复用。
        dict_normalized_subfunction[str_field_name] = normalize_info_items(  # 当前信息字段的标准化字典列表
            dict_normalized_subfunction.get(str_field_name, []),  # 当前信息字段的原始条目集合
            str_field_name,  # 字段名决定默认信息条目前缀
        )

    # 返回归一化后的子函数结构。
    return dict_normalized_subfunction

# 把 behavior / constraints / test_intent 等字段统一转换成字典列表。
def normalize_info_items(value: Any, field: str) -> list[dict[str, Any]]:
    """
    归一化信息条目列表字段。

    :param value: 原始字段值，可以是单条、列表或字典。
    :param field: 当前字段名，用于生成默认 id。
    :return: 标准化后的信息条目字典列表。
    """

    # 逐项复用单条规范化逻辑，保证 id/text/evidence 结构一致。
    return [
        _normalize_info_item(obj_item, field, int_index)
        for int_index, obj_item in enumerate(_as_list(value))
    ]

# 读取 JSON spec 后立即执行完整规范化，避免上层忘记补校验。
def read_spec(path: Path, target: str | None = None) -> dict[str, Any]:
    """
    从磁盘读取并归一化 HLS 规范文件。

    :param path: 规范文件路径。
    :param target: 可选目标类型覆盖；当前只允许 hls。
    :return: 归一化后的 HLS 规范字典。
    :raises SpecError: JSON 非法或归一化失败时抛出。
    """

    # JSON 解析失败时保留原始异常文本，方便用户定位文件问题。
    try:

        # 读取 UTF-8 文本并解析为 JSON 对象。
        dict_raw_spec = json.loads(path.read_text(encoding="utf-8"))  # 从文件读取的原始 spec

    # JSON 文本非法时转换成统一的 SpecError。
    except json.JSONDecodeError as exc:

        # 透传原始 JSON 解析上下文，方便定位语法错误。
        raise SpecError(f"> ERR: [Python] Invalid JSON in {path}: {exc}") from exc

    # 读取成功后交给 normalize_spec 执行字段补齐和结构校验。
    return normalize_spec(dict_raw_spec, target=target)

# 写规范时统一使用带缩进的 UTF-8 JSON，减少手写差异。
def write_spec(path: Path, spec: dict[str, Any]) -> None:
    """
    把规范字典写入目标 JSON 文件。

    :param path: 输出规范文件路径。
    :param spec: 已归一化或待持久化的规范字典。
    :return: 无业务返回值。
    """

    # 先确保父目录存在，避免首次写文件时失败。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 使用固定缩进和换行规则，保持版本控制下的 diff 稳定。
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# 单条信息项允许字符串、单键字典或完整对象，这里统一落成标准结构。
def _normalize_info_item(item: Any, field: str, index: int) -> dict[str, Any]:
    """
    归一化单条信息项。

    :param item: 原始信息项，可以是标量或字典。
    :param field: 当前字段名，用于生成默认 id。
    :param index: 当前条目序号，用于生成稳定默认 id。
    :return: 带 id、text、evidence 与 verification_cases 的标准化对象。
    """

    # 默认 id 使用字段名与顺序号组合，保证无显式 id 时也稳定。
    str_default_id = f"{field}_{index + 1}"  # 当前条目的默认标识

    # 字典输入允许带额外元数据，优先按对象路径解析。
    if isinstance(item, dict):

        # text / description / functionality 三个键按顺序兜底取值。
        obj_text_value = item.get("text", item.get("description", item.get("functionality", "")))  # 原始文本内容

        # 单键字典常用于短写，值本身可以直接当作 text。
        if not obj_text_value and len(item) == 1:

            # 取唯一 value 作为文本，兼容最简对象写法。
            obj_text_value = next(iter(item.values()))  # 单键字典的唯一文本值

        # 返回完整对象，供 planning 与验证逻辑直接消费。
        return {
            "id": sanitize_name(str(item.get("id") or str_default_id)),
            "text": str(obj_text_value),
            "evidence": _as_list(item.get("evidence", [])),
            "verification_cases": _as_list(item.get("verification_cases", [])),
        }

    # 非字典输入直接折叠成只有 text 的标准对象。
    return {
        "id": str_default_id,
        "text": str(item),
        "evidence": [],
        "verification_cases": [],
    }

# 把 None、标量和列表统一折叠成列表，减少上游分支。
def _as_list(value: Any) -> list[Any]:
    """
    把输入值转换成列表。

    :param value: 原始字段值，可以为 None、标量或列表。
    :return: 深拷贝后的列表表示。
    """

    # 缺值统一折叠为空列表，便于调用方直接迭代。
    if value is None:

        # 返回空列表，表示无条目输入。
        return []

    # 已经是列表时只做深拷贝，保护调用方原始对象。
    if isinstance(value, list):

        # 列表内容可能嵌套对象，因此保留深拷贝。
        return copy.deepcopy(value)

    # 标量或字典输入包装成单元素列表。
    return [copy.deepcopy(value)]

# 当前技能边界只接受 hls 目标，其他 target 必须立即阻断。
def _require_target(target: str) -> str:
    """
    校验并规范化目标类型。

    :param target: 原始目标类型文本。
    :return: 归一化后的小写目标类型。
    :raises SpecError: 目标不是 hls 时抛出。
    """

    # target 比较统一使用小写，避免大小写差异影响逻辑。
    str_normalized_target = target.lower()  # 用于 HLS-only 判定的小写目标值

    # 非 HLS 目标越早阻断越能避免混入 RTL 工作流。
    if str_normalized_target not in TARGETS:

        # 明确提示当前技能的 HLS-only 边界。
        raise SpecError("> ERR: [Python] This skill is HLS-only; target must be `hls`.")

    # 返回受控目标值，供后续代码继续复用。
    return str_normalized_target

# 历史 RTL 字段一旦出现，说明输入正在越过 HLS-only 边界。
def _reject_legacy_target_fields(raw: dict[str, Any]) -> None:
    """
    拒绝与 RTL 相关的历史字段。

    :param raw: 原始 spec 字典。
    :return: 无业务返回值；检测到 RTL 字段时抛出异常。
    :raises SpecError: 存在 RTL 相关字段时抛出。
    """

    # 仅统计非空历史字段，避免空占位误伤。
    list_legacy_fields = [
        str_key  # 当前命中的历史 RTL 字段名
        for str_key in ("rtl_dialect", "rtl_style_profile")  # 只扫描当前禁止的历史字段名
        if str_key in raw and raw.get(str_key) not in (None, "")  # 仅保留真正携带值的字段
    ]  # 触碰 HLS-only 边界的历史字段

    # 检测到 RTL 字段时立刻阻断。
    if list_legacy_fields:

        # 保持错误文本稳定，避免上层依赖被破坏。
        raise SpecError("> ERR: [Python] This skill is HLS-only; RTL dialect/style fields are not supported.")

# 设计需求字段允许缺失，但存在时必须保持对象形态。
def _normalize_design_requirements(value: Any) -> dict[str, Any]:
    """
    归一化 design_requirements 字段。

    :param value: 原始 design_requirements 值。
    :return: 合法的 design_requirements 字典。
    :raises SpecError: 字段不是对象时抛出。
    """

    # 空值统一折叠为空对象，方便下游补充确认状态。
    if value in (None, ""):

        # 返回空对象而不是 None，减少调用方分支。
        return {}

    # design_requirements 必须是对象，便于扩展键值语义。
    if not isinstance(value, dict):

        # 保持现有错误文本，避免行为漂移。
        raise SpecError("> ERR: [Python] Spec design_requirements must be an object.")

    # 深拷贝后返回，保护调用方原始对象。
    return copy.deepcopy(value)

# 把字符串字段约束到固定枚举集合，便于 prompt 和验证共享。
def _normalize_choice(
    value: Any,
    *,
    field: str,
    allowed_values: set[str],
    error_message: str,
    default: str | None,
) -> str | None:
    """
    归一化枚举型字符串字段。

    :param value: 原始字段值。
    :param field: 当前字段名，仅用于语义说明。
    :param allowed_values: 允许的小写枚举值集合。
    :param error_message: 非法值时抛出的错误文本。
    :param default: 空值时返回的默认值；允许为 None。
    :return: 合法的小写枚举值或默认值。
    :raises SpecError: 值不在允许集合中时抛出。
    """

    # 空值使用字段默认值，避免上层反复判空。
    if value in (None, ""):

        # 返回显式默认值，让调用方拿到稳定结构。
        return default

    # 非空值统一做字符串化和小写折叠。
    str_normalized_value = str(value).lower()  # 当前字段的归一化枚举值

    # 超出枚举集合时直接阻断，避免非法文本传播。
    if str_normalized_value not in allowed_values:

        # 在保留字段原始语义的同时，补齐统一的错误前缀和静态正文。
        raise SpecError("> ERR: [Python] Invalid choice. " + error_message)

    # 返回合法枚举值，供后续逻辑直接消费。
    return str_normalized_value

# streamability 反映主计算是否偏流式。
def _normalize_streamability(value: Any) -> str:
    """
    归一化 streamability 字段。

    :param value: 原始 streamability 值。
    :return: 合法的小写 streamability 枚举值。
    :raises SpecError: 值不在允许集合中时抛出。
    """

    # 复用枚举字段规范化逻辑，保持错误文本稳定。
    return str(
        _normalize_choice(
            value,
            field="streamability",
            allowed_values={"streamable", "non_streamable", "unknown"},
            error_message="Spec streamability must be `streamable`, `non_streamable`, or `unknown`.",
            default="unknown",
        )
    )

# transport_interface 表示顶层数据传输语义。
def _normalize_transport_interface(value: Any) -> str:
    """
    归一化 transport_interface 字段。

    :param value: 原始 transport_interface 值。
    :return: 合法的小写 transport_interface 枚举值。
    :raises SpecError: 值不在允许集合中时抛出。
    """

    # 允许的接口类型与现有 prompt / validation 枚举对齐。
    return str(
        _normalize_choice(
            value,
            field="transport_interface",
            allowed_values={"axis", "hls_stream", "m_axi", "s_axilite", "native", "custom", "unknown"},
            error_message=(
                "Spec transport_interface must be one of `axis`, `hls_stream`, `m_axi`, "
                "`s_axilite`, `native`, `custom`, or `unknown`."
            ),
            default="unknown",
        )
    )

# dataflow_streamability 表示 dataflow 阶段的流式组织方式。
def _normalize_dataflow_streamability(value: Any) -> str:
    """
    归一化 dataflow_streamability 字段。

    :param value: 原始 dataflow_streamability 值。
    :return: 合法的小写 dataflow_streamability 枚举值。
    :raises SpecError: 值不在允许集合中时抛出。
    """

    # dataflow 场景使用单独的枚举集合，避免和主 streamability 混淆。
    return str(
        _normalize_choice(
            value,
            field="dataflow_streamability",
            allowed_values={"streamable", "memory_mapped", "batch", "unknown"},
            error_message=(
                "Spec dataflow_streamability must be one of `streamable`, `memory_mapped`, "
                "`batch`, or `unknown`."
            ),
            default="unknown",
        )
    )

# interface_family 提供比 transport_interface 更高层的接口归类。
def _normalize_interface_family(value: Any) -> str | None:
    """
    归一化 interface_family 字段。

    :param value: 原始 interface_family 值。
    :return: 合法的小写 interface_family 枚举值；空值时返回 None。
    :raises SpecError: 值不在允许集合中时抛出。
    """

    # 该字段允许缺失，因此默认值保持 None。
    return _normalize_choice(
        value,
        field="interface_family",
        allowed_values={"native", "axi_stream", "axi4", "custom"},
        error_message="Spec interface_family must be one of `native`, `axi_stream`, `axi4`, or `custom`.",
        default=None,
    )

# interface_profile 保存更细粒度的接口契约参数。
def _normalize_interface_profile(value: Any) -> dict[str, Any]:
    """
    归一化 interface_profile 字段。

    :param value: 原始 interface_profile 值。
    :return: 合法的 interface_profile 字典。
    :raises SpecError: 字段不是对象时抛出。
    """

    # 空值统一折叠为空对象，便于上层直接追加字段。
    if value in (None, ""):

        # 返回空对象以保持结构稳定。
        return {}

    # interface_profile 必须是对象，便于表达命名参数。
    if not isinstance(value, dict):

        # 保留既有错误文本，避免影响上层断言。
        raise SpecError("> ERR: [Python] Spec interface_profile must be an object.")

    # 深拷贝后返回，避免外层对象被原地修改。
    return copy.deepcopy(value)

# 布尔型字段允许空值回退默认值，但不接受其他类型。
def _normalize_bool(value: Any, field: str, *, default: bool) -> bool:
    """
    归一化布尔字段。

    :param value: 原始字段值。
    :param field: 当前字段名，用于拼接错误信息。
    :param default: 空值时使用的默认布尔值。
    :return: 合法的布尔值。
    :raises SpecError: 字段不是布尔值时抛出。
    """

    # 空值直接回退默认值，避免上层重复处理。
    if value in (None, ""):

        # 返回调用方声明的默认布尔值。
        return default

    # 非布尔输入会让后续配置语义变得不清晰。
    if not isinstance(value, bool):

        # 错误文本包含字段名，方便定位问题来源。
        raise SpecError(f"> ERR: [Python] Spec {field} must be a boolean.")

    # 已经是布尔值时原样返回。
    return value

# 顶层字段缺失时先统一定位，避免后续类型校验报出噪声错误。
def _validate_required_spec_fields(spec: dict[str, Any]) -> None:
    """
    校验顶层规范是否缺少主字段。

    :param spec: 已归一化的顶层 spec 字典。
    :return: 无业务返回值；缺字段时抛出异常。
    :raises SpecError: 缺少主字段时抛出。
    """

    # 主字段清单来自 SPEC_FIELDS，顺序也用于错误展示。
    list_missing_fields = [
        str_field_name for str_field_name in SPEC_FIELDS if str_field_name not in spec  # 当前缺失的顶层字段名
    ]  # 顶层缺失字段列表

    # 只要有缺字段，就不再继续做更细校验。
    if list_missing_fields:

        # 保持原有错误文本格式，方便上层比对。
        raise SpecError(f"> ERR: [Python] Spec is missing required fields: {', '.join(list_missing_fields)}.")

# 顶层标量与容器字段类型在这里集中校验。
def _validate_top_level_field_types(spec: dict[str, Any]) -> None:
    """
    校验顶层关键字段的类型合同。

    :param spec: 已归一化的顶层 spec 字典。
    :return: 无业务返回值；字段类型错误时抛出异常。
    :raises SpecError: 任一关键字段类型不合法时抛出。
    """

    # description 不能为空字符串，否则 prompt 和计划都失去主语义。
    if not spec["description"]:

        # 空描述直接阻断，避免继续生成无语义 spec。
        raise SpecError("> ERR: [Python] Spec description must not be empty.")

    # interfaces 必须是对象，便于后续读取 top_function 和 arguments。
    if not isinstance(spec["interfaces"], dict):

        # 顶层接口结构错误时无法继续生成代码。
        raise SpecError("> ERR: [Python] Spec interfaces must be an object.")

    # behavior 统一约束为列表，保持 prompt 合成顺序稳定。
    if not isinstance(spec["behavior"], list):

        # 非列表行为字段会破坏后续迭代逻辑。
        raise SpecError("> ERR: [Python] Spec behavior must be a list.")

    # constraints 也使用列表，便于逐条注入提示词。
    if not isinstance(spec["constraints"], list):

        # 非列表约束字段无法参与逐条处理。
        raise SpecError("> ERR: [Python] Spec constraints must be a list.")

    # outputs 至少要有一个条目，否则无法描述交付物。
    if not isinstance(spec["outputs"], list) or not spec["outputs"]:

        # 输出为空时生成流程没有明确产物。
        raise SpecError("> ERR: [Python] Spec outputs must be a non-empty list.")

    # 这些字段在顶层合同中必须保持列表结构。
    for str_field_name in ("notes", "subfunctions"):

        # 缺字段时用默认空列表参与类型判断。
        if not isinstance(spec.get(str_field_name, []), list):

            # 错误文本沿用原格式，减少行为变化。
            raise SpecError(f"> ERR: [Python] Spec {str_field_name} must be a list.")

    # 这些字段必须保持对象结构，便于后续按键读取。
    for str_field_name in (
        "workflow",
        "performance",
        "hls_profile",
        "design_requirements",
        "interface_profile",
    ):

        # 缺字段时使用默认空对象参与类型判断。
        if not isinstance(spec.get(str_field_name, {}), dict):

            # 键值字段不是对象时立即阻断。
            raise SpecError(f"> ERR: [Python] Spec {str_field_name} must be an object.")

# 顶层 outputs 和 subfunctions 使用独立 helper 递归校验。
def _validate_nested_items(spec: dict[str, Any]) -> None:
    """
    校验输出条目和子函数条目。

    :param spec: 已归一化的顶层 spec 字典。
    :return: 无业务返回值；嵌套条目不合法时抛出异常。
    :raises SpecError: 任一输出或子函数条目不合法时抛出。
    """

    # 每个输出条目都要满足 HLS-only 路径合同。
    for obj_output in spec["outputs"]:

        # 输出条目使用专门 helper 检查路径和语言边界。
        _validate_output(obj_output)

    # 每个子函数条目都要满足字段和列表合同。
    for obj_subfunction in spec.get("subfunctions", []):

        # 子函数递归校验交给专门 helper 处理。
        _validate_subfunction(obj_subfunction)

# 顶层 shape 校验把缺字段、类型和嵌套条目检查串成一个闭环。
def _validate_shape(spec: dict[str, Any]) -> None:
    """
    校验归一化后 spec 的整体结构。

    :param spec: 已归一化的顶层 spec 字典。
    :return: 无业务返回值；结构不合法时抛出异常。
    :raises SpecError: 顶层字段或嵌套条目不满足合同时抛出。
    """

    # 先定位缺字段，避免后续类型校验产生级联噪声。
    _validate_required_spec_fields(spec)

    # 然后校验顶层标量与容器字段类型。
    _validate_top_level_field_types(spec)

    # 最后递归检查输出与子函数条目。
    _validate_nested_items(spec)

# 输出条目校验负责卡住 RTL 文件和非法后缀。
def _validate_output(output: Any) -> None:
    """
    校验单个输出条目是否合法。

    :param output: 原始输出条目对象。
    :return: 无业务返回值；条目不合法时抛出异常。
    :raises SpecError: 路径缺失、语言越界或后缀非法时抛出。
    """

    # 输出条目必须是带 path 的对象，否则无法定位目标文件。
    if not isinstance(output, dict) or not output.get("path"):

        # 缺 path 时无法继续做 HLS-only 路径校验。
        raise SpecError("> ERR: [Python] Each output must be an object with a path.")

    # 输出路径统一转成字符串，便于后缀判断与报错展示。
    str_output_path = str(output["path"])  # 输出条目的目标路径

    # 后缀统一使用小写判断，避免大小写差异影响规则。
    str_output_suffix = Path(str_output_path).suffix.lower()  # 输出路径的小写后缀

    # 语言字段也统一转成小写，兼容大小写混用输入。
    str_output_language = str(output.get("language") or "").lower()  # 输出条目的语言标识

    # 一旦命中 Verilog/SystemVerilog，就说明越过了 HLS-only 边界。
    if (
        str_output_suffix in {".v", ".sv"}
        or str_output_language in REJECTED_HARDWARE_LANGUAGES
    ):

        # 明确拒绝 RTL 产物，避免技能职责漂移。
        raise SpecError("> ERR: [Python] This skill is HLS-only; Verilog/SystemVerilog outputs are not allowed.")

    # 输出文件必须是受控的 C/C++ 源头文件或 cfg。
    if str_output_suffix not in HLS_SOURCE_SUFFIXES | HLS_CONFIG_SUFFIXES:

        # 错误文本保留原路径，方便定位非法条目。
        raise SpecError(
            "> ERR: [Python] "
            f"HLS output path {str_output_path!r} must be C/C++ source/header or .cfg."
        )

    # kind=config 时后缀必须真的是 cfg，避免语义和文件名不一致。
    if output.get("kind") == "config" and str_output_suffix not in HLS_CONFIG_SUFFIXES:

        # 配置输出的后缀合同单独收紧。
        raise SpecError("> ERR: [Python] HLS config outputs must use a .cfg suffix.")

# 子函数条目校验负责保证字段完整和列表结构稳定。
def _validate_subfunction(subfunction: Any) -> None:
    """
    校验单个子函数条目是否合法。

    :param subfunction: 原始子函数条目对象。
    :return: 无业务返回值；条目不合法时抛出异常。
    :raises SpecError: 子函数对象结构或内部列表字段不合法时抛出。
    """

    # 子函数必须是对象，否则无法检查字段合同。
    if not isinstance(subfunction, dict):

        # 非对象输入直接阻断。
        raise SpecError("> ERR: [Python] Each subfunction must be an object.")

    # 子函数字段缺失时优先统一报出完整缺口。
    list_missing_fields = [
        str_field_name  # 当前缺失的子函数字段名
        for str_field_name in SUBFUNCTION_FIELDS  # 逐项检查子函数合同字段
        if str_field_name not in subfunction  # 只保留当前缺失的字段
    ]  # 子函数缺失字段列表

    # 缺字段时不再继续做更细粒度类型校验。
    if list_missing_fields:

        # 错误文本沿用既有格式，方便回归。
        raise SpecError(
            "> ERR: [Python] "
            f"Subfunction is missing required fields: {', '.join(list_missing_fields)}."
        )

    # 除 name 之外的子函数字段必须统一是列表。
    for str_field_name in SUBFUNCTION_FIELDS[1:]:

        # 列表合同让 planning 和 prompt 拼装更稳定。
        if not isinstance(subfunction[str_field_name], list):

            # 字段名写入错误消息，方便快速定位。
            raise SpecError(f"> ERR: [Python] Subfunction field {str_field_name} must be a list.")

    # 信息字典字段的每个元素都必须是对象。
    for str_field_name in INFO_DICTIONARY_FIELDS:

        # 逐项检查 behavior / constraints / test_intent 的标准结构。
        for obj_item in subfunction[str_field_name]:

            # 归一化后这些列表里的元素都应当是字典。
            if not isinstance(obj_item, dict):

                # 非字典元素会破坏后续字段读取。
                raise SpecError(f"> ERR: [Python] Subfunction field {str_field_name} entries must be objects.")
