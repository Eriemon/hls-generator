"""把规范化后的 HLS 需求拆解为实现计划。"""

# future 注解延迟解析，降低运行时类型引用成本。
from __future__ import annotations

# copy 用于保留调用方输入，避免计划补全时修改原始规范。
import copy
from typing import Any

# spec 模块提供输入规范化和信息项清洗能力。
from .spec import normalize_info_items, normalize_spec

# 公开入口负责把规范化需求补齐为 runtime 可执行计划。
def decompose_spec(
    spec: dict[str, Any],
    target: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回补齐 subfunctions 与 workflow 的 HLS 实现计划。

    参数:
        spec: 调用方提供的原始 HLS 需求或已归一化计划。
        target: 传给规范化阶段的目标后端名称。
        evidence: 可选证据清单，用于生成 source_references。

    返回:
        已深拷贝并补齐默认子函数、测试意图和 workflow 的计划字典。
    """

    # 先通过统一入口规整名称、接口和约束，便于后续补齐默认计划。
    dict_normalized: dict[str, Any] = normalize_spec(spec, target=target)  # 归一化后的 HLS 规范

    # 深拷贝后再填充计划字段，避免污染调用方传入的 spec 对象。
    dict_plan: dict[str, Any] = copy.deepcopy(dict_normalized)  # 可变的实现计划副本

    # 缺少子函数时，根据顶层接口生成单函数默认实现计划。
    if not dict_plan.get("subfunctions"):

        # 接口方向拆分结果会同时用于默认子函数的输入和输出字段。
        tuple_hls_io: tuple[list[Any], list[Any]] = _hls_io(dict_plan)  # 顶层接口拆分结果

        # 从顶层接口拆分结果中取出会被子函数读取的端口集合。
        list_inputs: list[Any] = tuple_hls_io[0]  # 默认子函数读取的顶层端口参数

        # 从顶层接口拆分结果中取出需要写回调用方的端口集合。
        list_outputs: list[Any] = tuple_hls_io[1]  # 默认子函数写出的顶层端口参数

        # 子函数缺省测试意图必须同时覆盖行为项、边界项和 HLS C++ testbench。
        list_test_intent: list[str] = [
            (
                "Cover normal cases, boundary cases, and every behavior item "
                "with deterministic vectors and an HLS C++ testbench."
            )
        ]  # 默认测试意图原文

        # 默认计划保持旧版单子函数结构，供 workflow 与 verifier 继续读取。
        dict_plan["subfunctions"] = [
            {
                "name": dict_plan["name"],  # 默认子函数沿用顶层函数名称
                "inputs": list_inputs,  # 默认子函数输入端口列表
                "outputs": list_outputs,  # 默认子函数输出端口列表
                "behavior": normalize_info_items(dict_plan.get("behavior", []), "behavior"),  # 归一化后的行为约束条目
                "constraints": normalize_info_items(dict_plan.get("constraints", []), "constraints"),  # 归一化后的设计约束条目
                "dependencies": [],  # 默认单子函数计划暂不声明额外依赖
                "source_references": _source_refs(evidence),  # 轻量证据引用摘要
                "test_intent": normalize_info_items(list_test_intent, "test_intent"),  # 归一化后的默认测试意图
            }
        ]  # 默认单子函数计划

    # workflow 默认阶段是运行时流水线契约，已有字段可以覆盖默认值。
    dict_plan["workflow"] = {
        "stages": ["requirements", "codegen_plan", "tests", "hls"],  # 默认 HLS 生成阶段顺序
        **(dict_plan.get("workflow") or {}),  # 允许调用方覆写默认 workflow 字段
    }  # HLS 生成工作流配置

    # 返回完整计划给 workflow/verifier 使用。
    return dict_plan

# 内部辅助函数只负责拆分接口方向，不修改 spec。
def _hls_io(spec: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    """按 HLS interface direction 拆分输入输出参数。

    参数:
        spec: 当前 HLS 规范字典，内部应包含 interfaces.arguments 列表。

    返回:
        tuple[list[Any], list[Any]]: 按 direction 拆分后的输入参数列表与输出参数列表。
    """

    # input 与 inout 参数会进入默认子函数输入列表。
    list_inputs: list[Any] = []  # 默认子函数输入列表

    # 写回型端口单独积累，后面映射到默认子函数的 outputs 字段。
    list_outputs: list[Any] = []  # 默认子函数输出列表

    # 逐项解析接口参数，忽略无法解析为字典的脏数据。
    for obj_argument in spec.get("interfaces", {}).get("arguments", []):

        # 非字典接口项缺少 direction/name 等结构信息，不能参与计划拆分。
        if not isinstance(obj_argument, dict):

            # 继续检查后续参数，最大限度保留可用接口信息。
            continue

        # 未声明方向的参数按 input 处理，兼容旧规格输入。
        str_direction: str = str(obj_argument.get("direction") or "input").lower()  # 接口方向

        # output 只进入输出列表。
        if str_direction == "output":

            # 记录 HLS 输出参数，供默认子函数声明 outputs。
            list_outputs.append(obj_argument)

        # input 只进入输入列表。
        elif str_direction == "input":

            # 纯输入端口只进入 inputs，保持默认子函数的数据流入边。
            list_inputs.append(obj_argument)

        # inout 或未知方向保守同时进入输入与输出列表。
        else:

            # 未知方向需要保留读写两侧信息，避免丢失接口约束。
            list_inputs.append(obj_argument)

            # 同步写回输出侧，确保未知方向端口仍保留写路径约束。
            list_outputs.append(obj_argument)

    # 返回拆分后的输入与输出参数。
    return list_inputs, list_outputs

# 内部辅助函数只复制证据索引字段，避免计划携带大段正文。
def _source_refs(evidence: dict[str, Any] | None) -> list[Any]:
    """从证据清单中提取最多八条 source_references。

    参数:
        evidence: 可选证据字典，内部可包含 items 列表。

    返回:
        list[Any]: 仅保留 source_id、location 与 kind 的轻量引用摘要列表。
    """

    # 缺少证据时保持历史行为，返回空引用列表。
    if not evidence:

        # 空列表表示计划没有外部证据锚点。
        return []

    # source_references 只保留 workflow 消费的轻量定位字段。
    list_refs: list[Any] = []  # 证据引用摘要列表

    # 限制引用数量，避免计划 JSON 被长证据清单膨胀。
    for obj_item in evidence.get("items", [])[:8]:

        # 只有字典型证据项才含有 source_id/location/kind 字段。
        if isinstance(obj_item, dict):

            # 保留稳定字段，不把完整证据正文复制进计划。
            list_refs.append(
                {
                    "source_id": obj_item.get("source_id"),
                    "location": obj_item.get("location"),
                    "kind": obj_item.get("kind", "text"),
                }
            )

    # 返回提取出的轻量证据引用。
    return list_refs
