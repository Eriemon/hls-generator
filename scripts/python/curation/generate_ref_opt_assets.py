#!/usr/bin/env python3
"""生成 ref/Opt 派生的 HLS 模板、示例和参考文档资产。"""

# 启用推迟求值的类型标注，避免运行期解析复杂泛型。
from __future__ import annotations

# 标准库用于写入 JSON、复制模板字典并定位技能目录。
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class SpecTemplate:
    """描述一个可写入模板或示例 JSON 的 HLS 资产。

    参数:
        name: 写入 spec 内部的模板名称。
        top: 模板对应的 HLS top function 名称。
        pattern: 模板所属的 HLS 算法族或结构族。
        description: 面向用户展示的模板用途说明。
        interface_family: 模板采用的接口族名称。
        arguments: top function 参数契约列表。
        metadata: 模板族专属的结构化元数据。
        confirmation_notes: 记录已确认设计事实的说明文本。
        behavior: 生成代码时必须保留的行为约束说明。
        constraints: 生成代码时不得越界的限制说明。
        headers: 该模板要求的 HLS/C++ 头文件。
        pragmas: 该模板必须保留或解释的 HLS pragma。
        cfg_entries: 可选的 hls_config.cfg 必备条目。
        performance: 可选的性能目标字段。
        workflow: 可选的验证或板卡验收工作流字段。

    返回:
        dataclass 实例本身不直接返回业务结果，由 make_spec 转成 JSON payload。
    """

    # spec 名称用于区分 templates 与 examples 中的资产身份。
    name: str  # HLS spec 内部名称

    # top function 名称决定生成 C++ 文件和 testbench 的命名。
    top: str  # HLS 顶层函数名称

    # pattern 让运行时知道该资产属于哪类 HLS 生成策略。
    pattern: str  # HLS 算法族标识

    # description 面向审阅者解释模板适用的设计场景。
    description: str  # 模板用途说明

    # interface_family 决定 AXI4 或 AXIS 的接口画像选择。
    interface_family: str  # 接口族名称

    # arguments 描述 top function 的端口、方向和接口绑定。
    arguments: list[dict[str, Any]]  # 顶层参数契约

    # metadata 保存算法族专属的确认事实，供验证器读取。
    metadata: dict[str, Any]  # 模板族结构化元数据

    # confirmation_notes 记录人类确认过的边界，防止生成器臆测。
    confirmation_notes: str  # 已确认设计事实说明

    # behavior 约束生成代码必须保留的行为故事。
    behavior: list[str]  # 生成代码行为约束

    # constraints 记录该模板不能跨越的结构或验证边界。
    constraints: list[str]  # 生成代码限制说明

    # headers 约束 HLS/C++ 源码必须包含的头文件集合。
    headers: list[str]  # 必备头文件列表

    # pragmas 保存模板必须解释或保留的 HLS 指令。
    pragmas: list[str]  # 必备 HLS pragma 列表

    # cfg_entries 允许个别模板覆盖默认 hls_config 条目。
    cfg_entries: list[str] | None = None  # 可选配置条目覆盖

    # performance 允许个别模板覆盖默认 II 目标。
    performance: dict[str, Any] | None = None  # 可选性能目标

    # workflow 允许示例补充 mock vectors 或板卡验收说明。
    workflow: dict[str, Any] | None = None  # 可选验证工作流

# 解析当前脚本所在技能根，所有输出路径都从这里派生。
def skill_root() -> Path:
    """返回当前 erie-hls-generator 技能根目录。

    参数:
        无外部业务参数。

    返回:
        返回脚本所在技能包的根目录路径。
    """

    # 该脚本位于 scripts/python/curation，向上三层就是技能根。
    return Path(__file__).resolve().parents[3]

# 定位 references 目录，生成的 Markdown 知识文件写入这里。
def references_dir() -> Path:
    """返回生成参考文档时使用的 references 目录。

    参数:
        无外部业务参数。

    返回:
        返回技能内 references 子目录路径。
    """

    # references 与 SKILL.md 同级，供技能按需加载。
    return skill_root() / "references"

# 定位 examples 目录，生成的可运行示例 spec 写入这里。
def examples_dir() -> Path:
    """返回生成示例 spec 时使用的 examples 目录。

    参数:
        无外部业务参数。

    返回:
        返回技能资产中的 examples 子目录路径。
    """

    # examples 保持在 assets 下，避免把可运行样例混入参考文档。
    return skill_root() / "assets" / "examples"

# 定位 templates 目录，生成的模板 spec 写入这里。
def templates_dir() -> Path:
    """返回生成模板 spec 时使用的 templates 目录。

    参数:
        无外部业务参数。

    返回:
        返回技能资产中的 templates 子目录路径。
    """

    # templates 保存可被代码生成流程复用的第一类模板。
    return skill_root() / "assets" / "templates"

# 写入 Markdown 文本时统一补齐父目录和末尾换行。
def write_text(path: Path, text: str) -> None:
    """把参考文档文本写入目标路径。

    参数:
        path: 需要写入的 Markdown 文件路径。
        text: 准备落盘的 UTF-8 文本文档。

    返回:
        无业务返回值；函数通过文件系统写入产生副作用。
    """

    # 生成资产可能首次创建目录，写入前先补齐父目录。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 参考文档统一保留单个末尾换行，减少重复生成 diff。
    path.write_text(text.rstrip() + "\n", encoding="utf-8")

# 写入 JSON 时统一使用可读缩进和非 ASCII 原文输出。
def write_json(path: Path, payload: dict[str, Any]) -> None:
    """把结构化 HLS 资产写入 JSON 文件。

    参数:
        path: 需要写入的 JSON 文件路径。
        payload: 模板或示例 spec 的结构化数据。

    返回:
        无业务返回值；函数通过文件系统写入产生副作用。
    """

    # JSON 资产通常首次落盘到新目录，写入前先补齐父目录。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 缩进后的 JSON 便于审阅模板字段变更。
    str_json_text = (
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"  # 带缩进和末尾换行的模板 JSON 文本
    )

    # 写入 UTF-8 JSON，保留中文或 HLS 符号原貌。
    path.write_text(str_json_text, encoding="utf-8")

# 为 top function 推导生成器应输出的四类文件。
def outputs(top: str) -> list[dict[str, str]]:
    """生成模板对应的输出文件清单。

    参数:
        top: HLS top function 名称，用于派生源文件和 testbench 名称。

    返回:
        返回 header、source、testbench 和 hls_config 的文件描述列表。
    """

    # 输出文件名围绕同一个 top function，方便测试和报告定位。
    return [
        {"path": f"src/{top}.h", "kind": "header", "language": "cpp"},
        {"path": f"src/{top}.cpp", "kind": "source", "language": "cpp"},
        {"path": f"tb/{top}_tb.cpp", "kind": "testbench", "language": "cpp"},
        {"path": "hls_config.cfg", "kind": "config", "language": "ini"},
    ]

# 板卡验收未覆盖的模板显式保留不假设 host harness 的原因。
def board_stub(reason: str) -> dict[str, Any]:
    """生成非板卡运行模板的 workflow 占位说明。

    参数:
        reason: 说明当前模板为什么不直接声明板卡验收流程。

    返回:
        返回 workflow 中的 board_acceptance 字段。
    """

    # 该字段阻止模板被误解为已经具备真实板卡 host 验收。
    return {"board_acceptance": {"profile": "not_board_runnable", "reason": reason}}

# AXI4 memory-mapped 模板共享同一组接口画像。
def axi4_profile() -> dict[str, Any]:
    """生成 AXI4 memory-mapped 接口画像。

    参数:
        无外部业务参数。

    返回:
        返回描述 AXI4 master 读写接口的结构化画像。
    """

    # 固定画像反映当前模板资产的默认 memory-mapped 约束。
    return {
        "axi4_variant": "axi4_full",
        "role": "master",
        "read_write_mode": "read_write",
        "data_width": 32,
        "addr_width": 32,
        "id_width": 1,
        "burst_support": False,
        "clock_reset_domain": {"clock": "ap_clk", "reset": "ap_rst_n"},
    }

# AXI-Stream 模板共享同一组 stream 端口画像。
def axis_profile() -> dict[str, Any]:
    """生成 AXI-Stream 接口画像。

    参数:
        无外部业务参数。

    返回:
        返回描述 AXIS 数据宽度、TLAST 和时钟复位的画像。
    """

    # 当前流式模板都要求 ready/last 语义显式存在。
    return {
        "keep_ready": True,
        "keep_last": True,
        "data_width": 32,
        "clock_reset_domain": {"clock": "ap_clk", "reset": "ap_rst_n"},
    }

# 将 SpecTemplate 转换成技能运行时可消费的 JSON payload。
def make_spec(spec_template: SpecTemplate) -> dict[str, Any]:
    """把模板配置对象转换成完整 HLS spec payload。

    参数:
        spec_template: 已确认算法族、接口族和验证意图的模板配置。

    返回:
        返回可写入 templates 或 examples 的结构化 HLS spec。
    """

    # 根据接口族选择对应画像，保持 design_requirements 和顶层字段一致。
    dict_interface_profile = axis_profile() if spec_template.interface_family == "axi_stream" else axi4_profile()  # 当前模板接口画像

    # 未声明板卡流程的模板保持验证导向的安全默认值。
    str_workflow_fallback_reason = "Curated template stays validation-focused and does not assume a board host harness."  # 默认板卡占位原因说明

    # 显式工作流优先，否则退回 reference-first 板卡占位流程。
    dict_workflow = spec_template.workflow or board_stub(str_workflow_fallback_reason)  # 当前模板最终采用的工作流画像

    # 未声明性能字段时保留 II=1 的一阶流水目标。
    dict_performance = spec_template.performance or {"target_ii": 1}  # 模板性能目标

    # 未声明配置项时保留默认时钟约束。
    list_cfg_entries = spec_template.cfg_entries or ["clock=8.0"]  # 必备 hls_config 条目

    # 汇总模板所有契约字段，供生成器和验证器共同消费。
    return {
        "name": spec_template.name,
        "target": "hls",
        "design_requirements": {
            "target": "hls",
            "pipeline_required": True,
            "streamability": "streamable",
            "interface_family": spec_template.interface_family,
            "interface_profile": dict_interface_profile,
            "confirmed_by_user": True,
            "confirmation_notes": spec_template.confirmation_notes,
        },
        "streamability": "streamable",
        "interface_family": spec_template.interface_family,
        "interface_profile": dict_interface_profile,
        "pipeline_required": True,
        "codegen_plan_required": True,
        "description": spec_template.description,
        "interfaces": {
            "top_function": spec_template.top,
            "arguments": spec_template.arguments,
            "control": "s_axilite",
        },
        "behavior": spec_template.behavior,
        "clock": {"period_ns": 8.0, "uncertainty_ns": 0.8},
        "reset": {"strategy": "tool_default"},
        "constraints": spec_template.constraints,
        "outputs": outputs(spec_template.top),
        "notes": [],
        "subfunctions": [],
        "workflow": dict_workflow,
        "performance": dict_performance,
        "hls_profile": {
            "example_pattern": spec_template.pattern,
            "allowed_libraries": spec_template.headers,
            "required_headers": spec_template.headers,
            "required_pragmas": spec_template.pragmas,
            "required_metadata_fields": list(spec_template.metadata.keys()),
            "metadata": spec_template.metadata,
            "forbidden_combinations": [],
            "required_cfg_entries": list_cfg_entries,
        },
    }

# 单输入数组名义样例保留线性递增输入序列。
LIST_ARRAY_NOMINAL_INPUT = [1, 2, 3, 4]  # 单输入数组名义输入序列

# 单输入数组名义样例显式记录 mock 向量长度。
INT_ARRAY_NOMINAL_LENGTH = 4  # 单输入数组名义长度

# 单输入数组名义样例保留加一后的期望输出。
LIST_ARRAY_NOMINAL_OUTPUT = [2, 3, 4, 5]  # 单输入数组名义期望输出

# 单输入数组边界样例覆盖零值和非零尾元素。
LIST_ARRAY_BOUNDARY_INPUT = [0, 7]  # 单输入数组边界输入序列

# 单输入数组边界样例显式记录最小双元素长度。
INT_ARRAY_BOUNDARY_LENGTH = 2  # 单输入数组边界长度

# 单输入数组边界样例保留加一后的边界输出。
LIST_ARRAY_BOUNDARY_OUTPUT = [1, 8]  # 单输入数组边界期望输出

# 双输入数组名义样例保留第一路输入序列。
LIST_BINARY_NOMINAL_INPUT_A = [1, 2, 3]  # 双输入数组名义第一路输入

# 双输入数组名义样例保留第二路输入序列。
LIST_BINARY_NOMINAL_INPUT_B = [4, 5, 6]  # 双输入数组名义第二路输入

# 双输入数组名义样例显式记录向量长度。
INT_BINARY_NOMINAL_LENGTH = 3  # 双输入数组名义长度

# 双输入数组名义样例保留按位相加后的输出。
LIST_BINARY_NOMINAL_OUTPUT = [5, 7, 9]  # 双输入数组名义期望输出

# 双输入数组边界样例覆盖零值与较大元素组合。
LIST_BINARY_BOUNDARY_INPUT_A = [9, 0]  # 双输入数组边界第一路输入

# 双输入数组边界样例覆盖交错值组合。
LIST_BINARY_BOUNDARY_INPUT_B = [1, 7]  # 双输入数组边界第二路输入

# 双输入数组边界样例显式记录双元素长度。
INT_BINARY_BOUNDARY_LENGTH = 2  # 双输入数组边界长度

# 双输入数组边界样例保留边界相加结果。
LIST_BINARY_BOUNDARY_OUTPUT = [10, 7]  # 双输入数组边界期望输出

# CSR SpMV 名义样例复用最小双输入数组数据。
LIST_SPMV_NOMINAL_INPUT_A = [1, 2]  # CSR SpMV 名义第一路输入

# CSR SpMV 名义样例复用最小输出系数组合。
LIST_SPMV_NOMINAL_INPUT_B = [3, 4]  # CSR SpMV 名义第二路输入

# CSR SpMV 名义样例显式记录最小两元素长度。
INT_SPMV_NOMINAL_LENGTH = 2  # CSR SpMV 名义长度

# CSR SpMV 名义样例保留参考输出结果。
LIST_SPMV_NOMINAL_OUTPUT = [4, 6]  # CSR SpMV 名义期望输出

# CSR SpMV 边界样例覆盖单元素稀疏输入。
LIST_SPMV_BOUNDARY_INPUT_A = [0]  # CSR SpMV 边界第一路输入

# CSR SpMV 边界样例保留单元素权重。
LIST_SPMV_BOUNDARY_INPUT_B = [5]  # CSR SpMV 边界第二路输入

# CSR SpMV 边界样例显式记录单元素长度。
INT_SPMV_BOUNDARY_LENGTH = 1  # CSR SpMV 边界长度

# CSR SpMV 边界样例保留单元素参考输出。
LIST_SPMV_BOUNDARY_OUTPUT = [5]  # CSR SpMV 边界期望输出

# memory-mapped 单输入模板共享同一组最小 mock vectors。
def array_vectors() -> list[dict[str, Any]]:
    """生成数组读写模板使用的 mock vectors。

    参数:
        无外部业务参数。

    返回:
        返回 nominal 与 boundary 两组数组输入输出样例。
    """

    # 这些样例只验证模板 wiring，不宣称完整算法语义。
    return [
        {
            "id": "case_nominal",
            "inputs": {"input": LIST_ARRAY_NOMINAL_INPUT, "length": INT_ARRAY_NOMINAL_LENGTH},
            "expected_outputs": {"output": LIST_ARRAY_NOMINAL_OUTPUT},
        },
        {
            "id": "case_boundary",
            "inputs": {"input": LIST_ARRAY_BOUNDARY_INPUT, "length": INT_ARRAY_BOUNDARY_LENGTH},
            "expected_outputs": {"output": LIST_ARRAY_BOUNDARY_OUTPUT},
        },
    ]

# AXI-Stream 模板共享同一组流输入输出样例。
def axis_vectors() -> list[dict[str, Any]]:
    """生成 AXI-Stream 模板使用的 mock vectors。

    参数:
        无外部业务参数。

    返回:
        返回 nominal 与 boundary 两组 stream 输入输出样例。
    """

    # 样例保留流端口命名，方便生成器检查接口族映射。
    return [
        {
            "id": "case_nominal",
            "inputs": {"in_stream": [1, 1, 2], "length": 3},
            "expected_outputs": {"out_stream": [2, 2, 3]},
        },
        {
            "id": "case_boundary",
            "inputs": {"in_stream": [0, 15], "length": 2},
            "expected_outputs": {"out_stream": [1, 16]},
        },
    ]

# 双输入 memory-mapped 模板共享同一组加法型样例。
def binary_vectors() -> list[dict[str, Any]]:
    """生成双输入数组模板使用的 mock vectors。

    参数:
        无外部业务参数。

    返回:
        返回 nominal 与 boundary 两组双数组输入输出样例。
    """

    # 双输入样例覆盖 matmul 和 reference-only 示例的基础端口形状。
    return [
        {
            "id": "case_nominal",
            "inputs": {
                "input_a": LIST_BINARY_NOMINAL_INPUT_A,
                "input_b": LIST_BINARY_NOMINAL_INPUT_B,
                "length": INT_BINARY_NOMINAL_LENGTH,
            },
            "expected_outputs": {"output": LIST_BINARY_NOMINAL_OUTPUT},
        },
        {
            "id": "case_boundary",
            "inputs": {
                "input_a": LIST_BINARY_BOUNDARY_INPUT_A,
                "input_b": LIST_BINARY_BOUNDARY_INPUT_B,
                "length": INT_BINARY_BOUNDARY_LENGTH,
            },
            "expected_outputs": {"output": LIST_BINARY_BOUNDARY_OUTPUT},
        },
    ]

# 单输入 memory-mapped 模板复用 input/output/length 端口。
def axi_memory_arguments() -> list[dict[str, Any]]:
    """生成单输入 AXI4 memory-mapped 模板参数。

    参数:
        无外部业务参数。

    返回:
        返回 input、output 和 length 三个 top function 参数描述。
    """

    # 端口组合反映多数一元数组模板的最小读写形状。
    return [
        {
            "name": "input",
            "type": "const ap_uint<32> *",
            "direction": "input",
            "interface": "m_axi",
            "bundle": "gmem0",
            "depth": 256,
        },
        {
            "name": "output",
            "type": "ap_uint<32> *",
            "direction": "output",
            "interface": "m_axi",
            "bundle": "gmem1",
            "depth": 256,
        },
        {
            "name": "length",
            "type": "int",
            "direction": "input",
            "interface": "s_axilite",
        },
    ]

# 流式模板复用 in_stream/out_stream/length 端口。
def axis_stream_arguments() -> list[dict[str, Any]]:
    """生成 AXI-Stream 模板参数。

    参数:
        无外部业务参数。

    返回:
        返回 in_stream、out_stream 和 length 三个 top function 参数描述。
    """

    # 端口组合保持 stream 数据面和 s_axilite 控制面分离。
    return [
        {
            "name": "in_stream",
            "type": "hls::stream<ap_uint<32> >&",
            "direction": "input",
            "interface": "axis",
        },
        {
            "name": "out_stream",
            "type": "hls::stream<ap_uint<32> >&",
            "direction": "output",
            "interface": "axis",
        },
        {
            "name": "length",
            "type": "int",
            "direction": "input",
            "interface": "s_axilite",
        },
    ]

# 稠密双输入模板统一复用两路读口、一路写口和长度控制端口。
def binary_memory_arguments() -> list[dict[str, Any]]:
    """生成双输入 AXI4 memory-mapped 模板参数。

    参数:
        无外部业务参数。

    返回:
        返回两个输入数组、一个输出数组和 length 控制参数描述。
    """

    # 端口组合覆盖 dense matmul 等需要两路输入数组的模板。
    return [
        {
            "name": "input_a",
            "type": "const ap_uint<32> *",
            "direction": "input",
            "interface": "m_axi",
            "bundle": "gmem_a",
            "depth": 256,
        },
        {
            "name": "input_b",
            "type": "const ap_uint<32> *",
            "direction": "input",
            "interface": "m_axi",
            "bundle": "gmem_b",
            "depth": 256,
        },
        {
            "name": "output",
            "type": "ap_uint<32> *",
            "direction": "output",
            "interface": "m_axi",
            "bundle": "gmem_out",
            "depth": 256,
        },
        {
            "name": "length",
            "type": "int",
            "direction": "input",
            "interface": "s_axilite",
        },
    ]

# 批量模板写入逻辑统一把 SpecTemplate 转成 JSON payload。
def _template_payload_map(
    list_template_entries: list[tuple[str, SpecTemplate]],
) -> dict[str, dict[str, Any]]:
    """
    批量生成模板或示例 JSON payload 映射。

    参数:
        list_template_entries: 由文件名和 SpecTemplate 组成的顺序条目列表。

    返回:
        返回以 JSON 文件名为键、spec payload 为值的映射。
    """

    # 汇总映射需要按调用方给定顺序依次写入。
    dict_payloads: dict[str, dict[str, Any]] = {}  # 批量生成后的 spec payload 映射

    # 逐条执行 make_spec，避免每个算法族重复写同一层样板。
    for tuple_template_entry in list_template_entries:

        # 文件名和模板定义来自单个算法族的 builder 函数。
        str_filename = tuple_template_entry[0]  # 当前条目的目标 JSON 文件名

        # SpecTemplate 描述当前条目的结构化 HLS 资产契约。
        spec_template_spec_template: SpecTemplate = tuple_template_entry[1]  # 当前条目的 HLS 模板定义

        # 生成后的 payload 会被模板族 wrapper 继续合并。
        dict_payloads[str_filename] = make_spec(spec_template_spec_template)  # 单个模板条目的 JSON payload

    # 返回完整映射供模板族 wrapper 直接使用。
    return dict_payloads

# FIR memory 资产覆盖 pipeline 与 symmetric 两种直接数组模板。
def fir_memory_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 memory-backed FIR 模板 payload。

    参数:
        无外部业务参数。

    返回:
        返回 FIR pipeline 与 symmetric 模板映射。
    """

    # memory-backed FIR 模板共用 AXI4 数组端口。
    return {
        "hls_fir_pipeline_template.json": make_spec(
            SpecTemplate(
                name="fir_pipeline_template",
                top="fir_pipeline_kernel",
                pattern="fir",
                description="Template for a memory-backed FIR sample pipeline kernel.",
                interface_family="axi4",
                arguments=deepcopy(axi_memory_arguments()),
                metadata={
                    "tap_count": 16,
                    "coefficient_symmetry": "none",
                    "structure_style": "direct_form",
                    "interface_style": "memory_mapped",
                    "ii_target": 1,
                },
                confirmation_notes="Tap count, FIR structure, interface style, and II target have been confirmed.",
                behavior=[
                    "Replace the placeholder datapath with the confirmed FIR convolution semantics.",
                    "Keep the FIR sample loop reviewable against the II target.",
                ],
                constraints=[
                    "Do not change FIR structure style without updated metadata.",
                    "Keep local delay-line comments aligned with the confirmed tap count.",
                ],
                headers=["ap_int.h"],
                pragmas=["#pragma HLS PIPELINE II=1"],
            )
        ),
        "hls_fir_symmetric_template.json": make_spec(
            SpecTemplate(
                name="fir_symmetric_template",
                top="fir_symmetric_kernel",
                pattern="fir",
                description="Template for a symmetric-coefficient FIR kernel.",
                interface_family="axi4",
                arguments=deepcopy(axi_memory_arguments()),
                metadata={
                    "tap_count": 16,
                    "coefficient_symmetry": "symmetric",
                    "structure_style": "direct_form",
                    "interface_style": "memory_mapped",
                    "ii_target": 1,
                },
                confirmation_notes="Symmetric FIR coefficient reuse has been confirmed.",
                behavior=[
                    "Preserve the symmetric coefficient reuse story in comments and helper structure."
                ],
                constraints=[
                    "Do not fall back to unconstrained FIR comments when symmetry is explicit."
                ],
                headers=["ap_int.h"],
                pragmas=["#pragma HLS PIPELINE II=1"],
            )
        ),
    }

# FIR stream/dataflow 资产覆盖 AXIS 与 staged DATAFLOW 模板。
def fir_stream_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 stream 与 DATAFLOW FIR 模板 payload。

    参数:
        无外部业务参数。

    返回:
        返回 FIR AXIS 与 DATAFLOW 模板映射。
    """

    # stream/dataflow FIR 模板保留样本边界和阶段边界事实。
    return {
        "hls_fir_axis_template.json": make_spec(
            SpecTemplate(
                name="fir_axis_template",
                top="fir_axis_kernel",
                pattern="fir",
                description="Template for an AXI-Stream FIR kernel.",
                interface_family="axi_stream",
                arguments=deepcopy(axis_stream_arguments()),
                metadata={
                    "tap_count": 16,
                    "coefficient_symmetry": "none",
                    "structure_style": "streaming_direct_form",
                    "interface_style": "axi_stream",
                    "ii_target": 1,
                },
                confirmation_notes="AXI-Stream FIR surface and sample-rate target have been confirmed.",
                behavior=[
                    "Preserve one output token contract per confirmed sample semantics."
                ],
                constraints=[
                    "Keep stream framing comments aligned with the interface style metadata."
                ],
                headers=["ap_int.h", "hls_stream.h"],
                pragmas=["#pragma HLS PIPELINE II=1"],
            )
        ),
        "hls_fir_dataflow_template.json": make_spec(
            SpecTemplate(
                name="fir_dataflow_template",
                top="fir_dataflow_kernel",
                pattern="fir",
                description="Template for a staged FIR dataflow kernel.",
                interface_family="axi4",
                arguments=deepcopy(axi_memory_arguments()),
                metadata={
                    "tap_count": 16,
                    "coefficient_symmetry": "none",
                    "structure_style": "dataflow_staged",
                    "interface_style": "memory_mapped",
                    "ii_target": 1,
                },
                confirmation_notes="DATAFLOW FIR staging has been confirmed.",
                behavior=["Keep read, FIR compute, and write stages explicit."],
                constraints=[
                    "Do not claim DATAFLOW correctness without explicit stage-boundary comments."
                ],
                headers=["ap_int.h", "hls_stream.h"],
                pragmas=["#pragma HLS DATAFLOW", "#pragma HLS PIPELINE II=1"],
            )
        ),
    }

# FIR 资产按结构风格集中维护，避免 template_payloads 过长。
def fir_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 FIR 家族 template-ready payload。

    参数:
        无外部业务参数。

    返回:
        返回 FIR pipeline、symmetric、AXIS 和 DATAFLOW 模板映射。
    """

    # 合并 memory-backed 与 stream/dataflow 两组 FIR 模板。
    dict_templates: dict[str, dict[str, Any]] = {}  # FIR 模板 payload 映射

    # 先保留 memory-backed FIR 模板顺序。
    dict_templates.update(fir_memory_template_payloads())

    # 再补充 AXIS 与 DATAFLOW FIR 模板。
    dict_templates.update(fir_stream_template_payloads())

    # 返回完整 FIR 模板集合供上层合并。
    return dict_templates

# FFT fixed-point 模板保留点数、twiddle 表和 packed-IQ 事实。
def _fft_fixed_point_template_entry() -> tuple[str, SpecTemplate]:
    """返回 fixed-point FFT 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 fixed-point FFT 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 fixed-point FFT 的模板文件名和 twiddle/table 配置。
    return (
        "hls_fft_fixed_point_template.json",
        SpecTemplate(
            name="fft_fixed_point_template",
            top="fft_fixed_point_kernel",
            pattern="fft",
            description="Template for a fixed-point FFT or DFT-style transform kernel.",
            interface_family="axi4",  # CSR reference-only 接口族
            arguments=deepcopy(axi_memory_arguments()),
            metadata={
                "point_count": 64,
                "scaling_strategy": "none",
                "twiddle_representation": "q1_15_table",
                "complex_data_mode": "packed_iq",
                "error_tolerance": "1 lsb",
            },
            confirmation_notes="FFT point count, twiddle table, and fixed-point tolerance have been confirmed.",
            behavior=[  # 保留 per-stage scaling 的验证意图与容差叙事
                "Keep the placeholder transform comments aligned with the confirmed point count and twiddle policy.",
                "Document the error tolerance in the future testbench comparison.",
            ],
            constraints=[
                "Do not silently change the scaling strategy.",
                "Keep packed-IQ assumptions explicit.",
            ],
            headers=["ap_int.h"],
            pragmas=[
                "#pragma HLS PIPELINE II=1",
                "#pragma HLS ARRAY_PARTITION variable=twiddle complete dim=1",
            ],
        ),
    )

# FFT per-stage scaled 模板保留每级缩放故事。
def _fft_scaled_template_entry() -> tuple[str, SpecTemplate]:
    """返回 per-stage scaled FFT 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 per-stage scaled FFT 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 per-stage scaled FFT 的模板文件名和逐级缩放配置。
    return (
        "hls_fft_scaled_template.json",
        SpecTemplate(
            name="fft_scaled_template",
            top="fft_scaled_kernel",
            pattern="fft",
            description="Template for a per-stage scaled FFT kernel.",
            interface_family="axi4",  # CSR 走 AXI4 memory-mapped 端口族
            arguments=deepcopy(axi_memory_arguments()),
            metadata={
                "point_count": 64,
                "scaling_strategy": "per_stage_scale",
                "twiddle_representation": "q1_15_table",
                "complex_data_mode": "packed_iq",
                "error_tolerance": "1 lsb",
            },
            confirmation_notes="Per-stage FFT scaling has been confirmed.",
            behavior=[  # CSR 行指针/列索引/数值调度说明
                "Preserve the per-stage scaling story and tolerance-aware verification intent."
            ],
            constraints=[
                "Do not mix per-stage scaling and block-floating assumptions."
            ],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
    )

# FFT power-spectrum 模板保留窗函数和功率谱语义。
def _fft_power_spectrum_template_entry() -> tuple[str, SpecTemplate]:
    """返回 power-spectrum FFT 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 power-spectrum FFT 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 power-spectrum FFT 的模板文件名和窗函数/功率语义配置。
    return (
        "hls_fft_power_spectrum_template.json",
        SpecTemplate(
            name="fft_power_spectrum_template",
            top="fft_power_spectrum_kernel",
            pattern="fft",
            description="Template for a power-spectrum FFT kernel.",
            interface_family="axi4",  # CSR 采用 AXI4 memory-mapped 接口族
            arguments=deepcopy(axi_memory_arguments()),
            metadata={
                "point_count": 64,
                "scaling_strategy": "window_then_power",
                "twiddle_representation": "q1_15_table",
                "complex_data_mode": "real_input_hann",
                "error_tolerance": "0.004",
            },
            confirmation_notes="FFT power-spectrum flow and tolerance have been confirmed.",
            behavior=[  # 记录 CORDIC 旋转模式下误差容忍与归一化边界
                "Keep the window-power-spectrum intent explicit in comments and validation notes."
            ],
            constraints=[
                "Do not erase the real-input or window assumptions from the asset."
            ],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
    )

# FFT 资产集中维护 point count、scaling 与 twiddle 事实。
def fft_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 FFT 家族 template-ready payload。

    参数:
        无外部业务参数。

    返回:
        返回 fixed-point、scaled 和 power-spectrum FFT 模板映射。
    """

    # FFT 模板都保留 fixed-point tolerance 与复数数据模式。
    return _template_payload_map(
        [
            _fft_fixed_point_template_entry(),
            _fft_scaled_template_entry(),
            _fft_power_spectrum_template_entry(),
        ]
    )

# CORDIC rotation 模板保留角度范围和 fixed-point 格式。
def _cordic_rotation_template_entry() -> tuple[str, SpecTemplate]:
    """返回 rotation-mode CORDIC 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 rotation-mode CORDIC 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 rotation-mode CORDIC 的模板文件名和角度范围配置。
    return (
        "hls_cordic_rotation_template.json",
        SpecTemplate(
            name="cordic_rotation_template",
            top="cordic_rotation_kernel",
            pattern="cordic",
            description="Template for a CORDIC rotation-mode kernel.",
            interface_family="axi4",  # sin/cos 版本继续复用 memory-backed 数组端口，不切到流式触发面
            arguments=deepcopy(axi_memory_arguments()),
            metadata={
                "cordic_mode": "rotation",
                "iteration_count": 16,
                "angle_range": "[-pi, pi]",
                "fixed_point_format": "ap_fixed<24,4>",
                "error_tolerance": "0.004",
            },
            confirmation_notes="CORDIC rotation mode, angle range, and fixed-point format have been confirmed.",
            behavior=[  # CSR row_ptr/col_idx/value 调度说明
                "Keep the tolerance-aware rotation contract visible in comments and future testbench text."
            ],
            constraints=["Do not hide quadrant normalization assumptions."],
            headers=["ap_int.h", "ap_fixed.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
    )

# CORDIC vectoring 模板保留幅角解释语义。
def _cordic_vectoring_template_entry() -> tuple[str, SpecTemplate]:
    """返回 vectoring-mode CORDIC 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 vectoring-mode CORDIC 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 vectoring-mode CORDIC 的模板文件名和幅角解释配置。
    return (
        "hls_cordic_vectoring_template.json",
        SpecTemplate(
            name="cordic_vectoring_template",
            top="cordic_vectoring_kernel",
            pattern="cordic",
            description="Template for a CORDIC vectoring-mode kernel.",
            interface_family="axi4",  # CSR 使用数组端口风格的接口族
            arguments=deepcopy(axi_memory_arguments()),
            metadata={
                "cordic_mode": "vectoring",
                "iteration_count": 16,
                "angle_range": "[-pi, pi]",
                "fixed_point_format": "ap_fixed<24,4>",
                "error_tolerance": "0.004",
            },
            confirmation_notes="CORDIC vectoring mode has been confirmed.",
            behavior=[  # 说明 CSR 行指针与非零值调度的 reference-only 边界
                "Preserve magnitude-phase interpretation comments and tolerance-aware validation intent."
            ],
            constraints=[
                "Keep vectoring mode explicit instead of generic trig wording."
            ],
            headers=["ap_int.h", "ap_fixed.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
    )

# CORDIC sincos 模板保留成对输出契约。
def _cordic_sincos_template_entry() -> tuple[str, SpecTemplate]:
    """返回 sin/cos CORDIC 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 sin/cos CORDIC 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 sin/cos CORDIC 的模板文件名和成对输出配置。
    return (
        "hls_cordic_sincos_template.json",
        SpecTemplate(
            name="cordic_sincos_template",
            top="cordic_sincos_kernel",
            pattern="cordic",
            description="Template for a CORDIC sin/cos generator.",
            interface_family="axi4",  # CORDIC sin/cos memory 模板仍走数组端口接口族
            arguments=deepcopy(axi_memory_arguments()),
            metadata={
                "cordic_mode": "sincos",
                "iteration_count": 16,
                "angle_range": "[-pi, pi]",
                "fixed_point_format": "ap_fixed<24,4>",
                "error_tolerance": "0.004",
            },
            confirmation_notes="CORDIC sin/cos generation has been confirmed.",
            behavior=["Keep tolerance-aware sin/cos comparison intent explicit."],
            constraints=["Do not hide fixed-point format assumptions."],
            headers=["ap_int.h", "ap_fixed.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
    )

# CORDIC memory 资产把 rotation、vectoring 和 sincos 三类数组端口模板归成一组。
def cordic_memory_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 memory-backed CORDIC 模板 payload。

    参数:
        无外部业务参数。

    返回:
        返回 rotation、vectoring 和 sincos CORDIC 模板映射。
    """

    # memory-backed CORDIC 模板共享 AXI4 数组端口。
    return _template_payload_map(
        [
            _cordic_rotation_template_entry(),
            _cordic_vectoring_template_entry(),
            _cordic_sincos_template_entry(),
        ]
    )

# CORDIC AXIS 资产单独维护 stream 边界事实。
def cordic_axis_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 AXI-Stream CORDIC 模板 payload。

    参数:
        无外部业务参数。

    返回:
        返回 AXIS CORDIC 模板映射。
    """

    # AXIS CORDIC 模板保留流帧边界和三角函数容差事实。
    return {
        "hls_cordic_axis_template.json": make_spec(
            SpecTemplate(
                name="cordic_axis_template",
                top="cordic_axis_kernel",
                pattern="cordic",
                description="Template for an AXI-Stream CORDIC kernel.",
                interface_family="axi_stream",
                arguments=deepcopy(axis_stream_arguments()),
                metadata={
                    "cordic_mode": "sincos",
                    "iteration_count": 16,
                    "angle_range": "[-pi, pi]",
                    "fixed_point_format": "ap_fixed<24,4>",
                    "error_tolerance": "0.004",
                },
                confirmation_notes="AXIS CORDIC streaming surface has been confirmed.",
                behavior=[
                    "Preserve stream framing and tolerance-aware trig expectations."
                ],
                constraints=["Keep AXIS boundaries explicit."],
                headers=["ap_int.h", "ap_fixed.h", "hls_stream.h"],
                pragmas=["#pragma HLS PIPELINE II=1"],
            )
        ),
    }

# CORDIC 总入口把 memory-backed 与 AXIS 流式模板汇成同一张模板表。
def cordic_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 CORDIC 家族 template-ready payload。

    参数:
        无外部业务参数。

    返回:
        返回 rotation、vectoring、sincos 和 AXIS CORDIC 模板映射。
    """

    # 这里收集的是 CORDIC 模板文件名到 payload 的总映射。
    dict_templates: dict[str, dict[str, Any]] = {}  # 合并后的 CORDIC 模板文件名到 payload 映射

    # 先写入 memory-backed CORDIC 模板，保留数组端口版本。
    dict_templates.update(cordic_memory_template_payloads())

    # 再补入 AXIS CORDIC 模板，保留流式端口版本。
    dict_templates.update(cordic_axis_template_payloads())

    # 把汇总后的 CORDIC 模板表交给 transform 聚合层复用。
    return dict_templates

# FFT 与 CORDIC 同属 transform 资产，统一维护数值容差和角度事实。
def transform_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 FFT 和 CORDIC 家族 template-ready payload。

    参数:
        无外部业务参数。

    返回:
        返回 FFT、CORDIC memory 与 CORDIC AXIS 模板映射。
    """

    # 这里收集的是 transform 族模板文件名到 payload 的总映射。
    dict_templates: dict[str, dict[str, Any]] = {}  # transform 族模板文件名到 payload 总映射

    # 先放入 FFT 模板，保留频域变换资产。
    dict_templates.update(fft_template_payloads())

    # 再补入 CORDIC 模板，补齐角度变换资产。
    dict_templates.update(cordic_template_payloads())

    # 向上层返回完整 transform 模板总表。
    return dict_templates

# Matmul blocked 模板保留 tile 形状和阻塞式调度语义。
def _matmul_blocked_template_entry() -> tuple[str, SpecTemplate]:
    """
    返回 blocked matmul 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 blocked matmul 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 blocked matmul 的模板文件名和完整 tile 配置。
    return (
        "hls_matmul_blocked_template.json",
        SpecTemplate(
            name="matmul_blocked_template",
            top="matmul_blocked_kernel",
            pattern="matmul",
            description="Template for a blocked matrix multiply kernel.",
            interface_family="axi4",  # CSR reference-only 采用数组端口风格的接口族
            arguments=deepcopy(binary_memory_arguments()),
            metadata={
                "tile_shape": "4x4x4",
                "layout": "row_major",
                "accumulator_type": "ap_int<40>",
                "memory_schedule": "blocked",
                "ii_target": 1,
            },
            confirmation_notes="Blocked matmul tile shape and accumulator strategy have been confirmed.",
            behavior=["Preserve blocked local-buffer roles in comments and pragmas."],
            constraints=["Do not blur tile shape and memory schedule facts."],
            headers=["ap_int.h"],
            pragmas=[
                "#pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1",
                "#pragma HLS ARRAY_PARTITION variable=tile_b complete dim=1",
            ],
        ),
    )

# Matmul partitioned 模板保留分块并行局部缓存语义。
def _matmul_partitioned_template_entry() -> tuple[str, SpecTemplate]:
    """
    返回 partitioned matmul 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 partitioned matmul 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 partitioned matmul 的模板文件名和并行分块配置。
    return (
        "hls_matmul_partitioned_template.json",
        SpecTemplate(
            name="matmul_partitioned_template",
            top="matmul_partitioned_kernel",
            pattern="matmul",
            description="Template for a partitioned matrix multiply kernel.",
            interface_family="axi4",  # partitioned matmul 模板仍走双数组 memory 接口族
            arguments=deepcopy(binary_memory_arguments()),
            metadata={
                "tile_shape": "4x4x4",
                "layout": "row_major",
                "accumulator_type": "ap_int<40>",
                "memory_schedule": "partitioned_tiles",
                "ii_target": 1,
            },
            confirmation_notes="Partitioned matmul tile schedule has been confirmed.",
            behavior=["Keep local partitioning motivation explicit."],
            constraints=[
                "Do not collapse the partitioned schedule into the blocked baseline."
            ],
            headers=["ap_int.h"],
            pragmas=[
                "#pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1",
                "#pragma HLS ARRAY_PARTITION variable=tile_b complete dim=1",
            ],
        ),
    )

# Matmul DATAFLOW 模板保留 load-compute-store 分段语义。
def _matmul_dataflow_template_entry() -> tuple[str, SpecTemplate]:
    """
    返回 DATAFLOW matmul 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 DATAFLOW matmul 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 DATAFLOW matmul 的模板文件名和分阶段执行配置。
    return (
        "hls_matmul_dataflow_template.json",
        SpecTemplate(
            name="matmul_dataflow_template",
            top="matmul_dataflow_kernel",
            pattern="matmul",
            description="Template for a load-compute-store matrix multiply kernel.",
            interface_family="axi4",
            arguments=deepcopy(binary_memory_arguments()),
            metadata={
                "tile_shape": "4x4x4",
                "layout": "row_major",
                "accumulator_type": "ap_int<40>",
                "memory_schedule": "load_compute_store",
                "ii_target": 1,
            },
            confirmation_notes="Matmul load-compute-store schedule has been confirmed.",
            behavior=[  # 说明 CSR 示例只描述 row_ptr/col_idx/value 调度事实，不承诺默认 runtime 支持
                "Keep read, compute, and write stages explicit in comments and pragmas."
            ],
            constraints=["Do not claim DATAFLOW without named stage boundaries."],
            headers=["ap_int.h", "hls_stream.h"],
            pragmas=[
                "#pragma HLS DATAFLOW",
                "#pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1",
            ],
        ),
    )

# Matmul 资产集中维护 tile、layout 和 memory schedule 事实。
def matmul_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 matmul 家族 template-ready payload。

    参数:
        无外部业务参数。

    返回:
        返回 blocked、partitioned 和 DATAFLOW matmul 模板映射。
    """

    # matmul 模板都使用双输入 AXI4 数组端口。
    return _template_payload_map(
        [
            _matmul_blocked_template_entry(),
            _matmul_partitioned_template_entry(),
            _matmul_dataflow_template_entry(),
        ]
    )

# Prefix scan 模板集中记录递推模式与 block offset 传播差异。
def prefix_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 prefix scan 家族 template-ready payload。

    参数:
        无外部业务参数。

    返回:
        返回 basic 与 blocked prefix scan 模板映射。
    """

    # prefix 模板保留 recurrence 与 block offset 语义差异。
    return {
        "hls_prefix_basic_template.json": make_spec(
            SpecTemplate(
                name="prefix_basic_template",
                top="prefix_basic_kernel",
                pattern="prefix_scan",
                description="Template for a basic inclusive prefix-scan kernel.",
                interface_family="axi4",
                arguments=deepcopy(axi_memory_arguments()),
                metadata={
                    "block_size": 1,
                    "scan_mode": "inclusive",
                    "offset_propagation": "none",
                    "latency_strategy": "sequential_chain",
                    "boundary_policy": "clamp_length",
                },
                confirmation_notes="Basic prefix-scan recurrence has been confirmed.",
                behavior=["Preserve the inclusive scan contract and recurrence story."],
                constraints=[
                    "Do not convert inclusive scan semantics into a generic reduction."
                ],
                headers=["ap_int.h"],
                pragmas=["#pragma HLS PIPELINE II=1"],
            )
        ),
        "hls_prefix_blocked_template.json": make_spec(
            SpecTemplate(
                name="prefix_blocked_template",
                top="prefix_blocked_kernel",
                pattern="prefix_scan",
                description="Template for a blocked prefix-scan kernel.",
                interface_family="axi4",
                arguments=deepcopy(axi_memory_arguments()),
                metadata={
                    "block_size": 16,
                    "scan_mode": "inclusive",
                    "offset_propagation": "block_offsets",
                    "latency_strategy": "blocked_parallel",
                    "boundary_policy": "clamp_length",
                },
                confirmation_notes="Blocked prefix-scan offset propagation has been confirmed.",
                behavior=["Keep local scan and block-offset propagation explicit."],
                constraints=["Do not hide block-offset correctness assumptions."],
                headers=["ap_int.h"],
                pragmas=["#pragma HLS PIPELINE II=1", "#pragma HLS UNROLL factor=4"],
            )
        ),
    }

# 线性代数资产覆盖矩阵乘和 prefix scan 两类结构。
def linear_template_payloads() -> dict[str, dict[str, Any]]:
    """生成线性代数家族 template-ready payload。

    参数:
        无外部业务参数。

    返回:
        返回 matmul 与 prefix scan 模板映射。
    """

    # 这里收集的是线性代数模板文件名到 payload 的总映射。
    dict_templates: dict[str, dict[str, Any]] = {}  # 线性代数模板文件名到 payload 总映射

    # 先装入 matmul 模板，保留矩阵乘资产。
    dict_templates.update(matmul_template_payloads())

    # 再装入 prefix scan 模板，补齐扫描资产。
    dict_templates.update(prefix_template_payloads())

    # 把线性代数模板总表交给更上层资产汇总。
    return dict_templates

# RLE AXIS encode 模板保留帧边界和 run-length 上限事实。
def _rle_axis_encode_template_entry() -> tuple[str, SpecTemplate]:
    """
    返回 RLE AXIS encode 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 RLE AXIS encode 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 RLE AXIS encode 的模板文件名和帧边界配置。
    return (
        "hls_rle_axis_encode_template.json",
        SpecTemplate(
            name="rle_axis_encode_template",
            top="rle_axis_encode_kernel",
            pattern="rle_axis",
            description="Template for an AXI-Stream RLE encoder.",
            interface_family="axi_stream",
            arguments=[
                {
                    "name": "in_stream",
                    "type": "hls::stream<ap_axiu<8,0,0,0> >&",
                    "direction": "input",
                    "interface": "axis",
                },
                {
                    "name": "out_stream",
                    "type": "hls::stream<ap_axiu<16,0,0,0> >&",
                    "direction": "output",
                    "interface": "axis",
                },
                {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
            ],
            metadata={
                "frame_boundary_mode": "tlast_terminated",
                "tlast_policy": "propagate_final_pair",
                "run_length_limit": 255,
                "empty_frame_policy": "reject",
                "ii_target": 1,
            },
            confirmation_notes="RLE AXIS encode frame and TLAST policy have been confirmed.",
            behavior=[
                "Preserve TLAST, TKEEP, and TSTRB handling explicitly in the generated scaffold comments."
            ],
            constraints=["Do not claim empty-frame behavior without metadata."],
            headers=["ap_int.h", "ap_axi_sdata.h", "hls_stream.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
    )

# RLE AXIS decode 模板保留终止符号传播语义。
def _rle_axis_decode_template_entry() -> tuple[str, SpecTemplate]:
    """
    返回 RLE AXIS decode 模板条目。

    参数:
        无外部业务参数。

    返回:
        返回 RLE AXIS decode 模板的文件名与 `SpecTemplate` 配置二元组。
    """

    # 返回 RLE AXIS decode 的模板文件名和终止符号传播配置。
    return (
        "hls_rle_axis_decode_template.json",
        SpecTemplate(
            name="rle_axis_decode_template",
            top="rle_axis_decode_kernel",
            pattern="rle_axis",
            description="Template for an AXI-Stream RLE decoder.",
            interface_family="axi_stream",
            arguments=[
                {
                    "name": "in_stream",
                    "type": "hls::stream<ap_axiu<16,0,0,0> >&",
                    "direction": "input",
                    "interface": "axis",
                },
                {
                    "name": "out_stream",
                    "type": "hls::stream<ap_axiu<8,0,0,0> >&",
                    "direction": "output",
                    "interface": "axis",
                },
                {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
            ],
            metadata={
                "frame_boundary_mode": "tlast_terminated",
                "tlast_policy": "propagate_final_symbol",
                "run_length_limit": 255,
                "empty_frame_policy": "reject",
                "ii_target": 1,
            },
            confirmation_notes="RLE AXIS decode frame and TLAST policy have been confirmed.",
            behavior=[
                "Preserve explicit AXIS boundary handling in comments and validation."
            ],
            constraints=["Keep run splitting and final-symbol framing explicit."],
            headers=["ap_int.h", "ap_axi_sdata.h", "hls_stream.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
    )

# Stream codec 资产集中维护 AXIS 帧边界和 RLE 元数据。
def stream_codec_template_payloads() -> dict[str, dict[str, Any]]:
    """生成 stream codec 家族 template-ready payload。

    参数:
        无外部业务参数。

    返回:
        返回 RLE AXIS encode 与 decode 模板映射。
    """

    # RLE 模板显式保留 TLAST、TKEEP、TSTRB 和 run-length 边界。
    return _template_payload_map(
        [
            _rle_axis_encode_template_entry(),
            _rle_axis_decode_template_entry(),
        ]
    )

# 第一批模板资产按算法族汇总为写入 templates/ 的 payload。
def template_payloads() -> dict[str, dict[str, Any]]:
    """生成所有 template-ready HLS 模板 payload。

    参数:
        无外部业务参数。

    返回:
        返回以 JSON 文件名为键、HLS spec payload 为值的模板映射。
    """

    # 单个入口合并各算法族模板，保持旧调用方不需要知道拆分细节。
    dict_templates: dict[str, dict[str, Any]] = {}  # 全量模板 payload 映射

    # 先合并 FIR 资产，维持历史文件名顺序靠前。
    dict_templates.update(fir_template_payloads())

    # 再合并 transform 资产，覆盖 FFT 与 CORDIC 模板。
    dict_templates.update(transform_template_payloads())

    # 随后合并线性代数资产，覆盖 matmul 与 prefix 模板。
    dict_templates.update(linear_template_payloads())

    # 最后合并 stream codec 资产，保留 RLE 模板在尾部。
    dict_templates.update(stream_codec_template_payloads())

    # 返回合并后的模板映射供 examples 继续派生。
    return dict_templates

# 参考文档正文以现有 references 文件为源，避免把大段 Markdown 塞进 Python。
def docs_payloads() -> dict[str, str]:
    """读取需要由资产脚本保持同步的参考文档正文。

    参数:
        无外部业务参数。

    返回:
        返回以 Markdown 文件名为键、当前文档正文为值的映射。
    """

    # 这些文档由本脚本负责重写，文件名顺序保持历史生成顺序。
    list_reference_names = [  # 需要保持同步的参考文档名称
        "ref-opt-catalog.md",  # 模板目录总览页
        "hls-fir-template-family.md",  # FIR 模板家族细分说明页
        "hls-fft-cordic-template-family.md",  # 频域与三角变换模板说明页
        "hls-stream-codec-template-family.md",  # RLE 与流式编解码模板说明页
        "hls-linear-algebra-template-family.md",  # 矩阵乘与 prefix-scan 模板说明页
    ]

    # references 目录只解析一次，避免循环中重复计算脚本根路径。
    path_references_dir = references_dir()  # 参考文档目录

    # 返回当前文档正文，render_assets 会按同名文件重新写入。
    return {
        str_name: (path_references_dir / str_name).read_text(encoding="utf-8")
        for str_name in list_reference_names
    }

# 示例覆盖表集中维护模板到 example spec 的派生关系。
def example_overrides() -> dict[str, tuple[str, list[dict[str, Any]]]]:
    """生成模板示例对默认 mock vectors 的覆盖映射。

    参数:
        无外部业务参数。

    返回:
        返回以示例文件名为键、模板文件名和 mock vectors 为值的映射。
    """

    # 覆盖映射将模板 payload 复制为 examples 资产，并补齐测试向量。
    return {
        "hls_fir_pipeline_spec.json": (
            "hls_fir_pipeline_template.json",
            array_vectors(),
        ),
        "hls_fir_symmetric_spec.json": (
            "hls_fir_symmetric_template.json",
            array_vectors(),
        ),
        "hls_fir_axis_spec.json": ("hls_fir_axis_template.json", axis_vectors()),
        "hls_fir_dataflow_spec.json": (
            "hls_fir_dataflow_template.json",
            array_vectors(),
        ),
        "hls_fft_fixed_point_spec.json": (
            "hls_fft_fixed_point_template.json",
            array_vectors(),
        ),
        "hls_fft_scaled_spec.json": ("hls_fft_scaled_template.json", array_vectors()),
        "hls_fft_power_spectrum_spec.json": (
            "hls_fft_power_spectrum_template.json",
            array_vectors(),
        ),
        "hls_cordic_rotation_spec.json": (
            "hls_cordic_rotation_template.json",
            array_vectors(),
        ),
        "hls_cordic_vectoring_spec.json": (
            "hls_cordic_vectoring_template.json",
            array_vectors(),
        ),
        "hls_cordic_sincos_spec.json": (
            "hls_cordic_sincos_template.json",
            array_vectors(),
        ),
        "hls_cordic_axis_spec.json": ("hls_cordic_axis_template.json", axis_vectors()),
        "hls_matmul_blocked_spec.json": (
            "hls_matmul_blocked_template.json",
            binary_vectors(),
        ),
        "hls_matmul_partitioned_spec.json": (
            "hls_matmul_partitioned_template.json",
            binary_vectors(),
        ),
        "hls_matmul_dataflow_spec.json": (
            "hls_matmul_dataflow_template.json",
            binary_vectors(),
        ),
        "hls_prefix_basic_spec.json": (
            "hls_prefix_basic_template.json",
            array_vectors(),
        ),
        "hls_prefix_blocked_spec.json": (
            "hls_prefix_blocked_template.json",
            array_vectors(),
        ),
        "hls_rle_axis_encode_spec.json": (
            "hls_rle_axis_encode_template.json",
            axis_vectors(),
        ),
        "hls_rle_axis_decode_spec.json": (
            "hls_rle_axis_decode_template.json",
            axis_vectors(),
        ),
    }

# SpMV CSR 仍是 reference-first 示例，不进入默认 runtime pattern。
def spmv_reference_example() -> dict[str, Any]:
    """生成 CSR SpMV reference-only 示例 payload。

    参数:
        无外部业务参数。

    返回:
        返回附带 mock vectors 的 CSR SpMV 示例 spec。
    """

    # CSR 示例用于保留稀疏矩阵事实，不声明一线 runtime 支持。
    dict_payload = make_spec(  # CSR SpMV reference-only 资产的完整 spec payload
        SpecTemplate(  # CSR 稀疏矩阵 reference-first 模板定义主体
            name="spmv_csr_reference",  # CSR reference-only 示例名称
            top="spmv_csr_reference_kernel",  # CSR reference-only 顶层函数名
            pattern="spmv_csr",  # CSR reference-first 示例模式名
            description="Reference-first CSR SpMV example kept out of Tier 1 runtime pattern support.",  # CSR reference-first 示例定位说明
            interface_family="axi4",  # CSR 示例刻意保留 memory-mapped 数组端口，不切成流式 reference
            arguments=deepcopy(binary_memory_arguments()),  # CSR reference-only 顶层参数定义
            metadata={"representation": "csr", "tier": "reference_first"},  # CSR 稀疏表示与分层定位元数据
            confirmation_notes="Keep CSR SpMV as a reference-first example until sparse validation coverage grows.",  # CSR reference-first 保留原因说明
            behavior=[  # 收纳 CSR reference-only 示例允许暴露的行为说明
                "Describe CSR row_ptr, col_idx, and value scheduling without claiming first-class runtime support."  # 约束文案只描述 CSR 调度事实，不把示例提升为默认 runtime 能力
            ],
            constraints=[  # 限定 CSR 稀疏矩阵示例只能停留在 examples 资产层，不进入 provider 发现路径
                "Do not upgrade this example into default mock or provider behavior yet."  # 提醒维护者 sparse 验证还不完整，当前禁止把此示例接入默认 mock/provider 链路
            ],
            headers=["ap_int.h"],  # CSR reference-only 示例依赖的最小 HLS 头文件集合
            pragmas=["#pragma HLS PIPELINE II=1"],  # CSR reference-only 示例固定保留的流水化 pragma
        )
    )

    # CSR mock vectors 描述稀疏矩阵最小示例和边界输入。
    dict_payload["workflow"]["mock_vectors"] = [  # 把 case_nominal 与 case_boundary 固定进 workflow 节点，查看 CSR reference spec 时就能直接核对正常路径和长度为 1 的边界路径
        {
            "id": "case_nominal",  # CSR 名义路径用例标识
            "inputs": {  # CSR 名义输入向量
                "input_a": LIST_SPMV_NOMINAL_INPUT_A,  # CSR 名义稀疏值占位输入
                "input_b": LIST_SPMV_NOMINAL_INPUT_B,  # CSR 名义列索引占位输入
                "length": INT_SPMV_NOMINAL_LENGTH,  # CSR 名义向量长度
            },
            "expected_outputs": {"output": LIST_SPMV_NOMINAL_OUTPUT},  # CSR 名义结果占位输出
        },
        {
            "id": "case_boundary",  # CSR 边界路径用例标识
            "inputs": {  # CSR 边界输入向量
                "input_a": LIST_SPMV_BOUNDARY_INPUT_A,  # CSR 边界值/索引占位输入 A
                "input_b": LIST_SPMV_BOUNDARY_INPUT_B,  # CSR 边界列索引占位输入
                "length": INT_SPMV_BOUNDARY_LENGTH,  # CSR 边界向量长度
            },
            "expected_outputs": {"output": LIST_SPMV_BOUNDARY_OUTPUT},  # CSR 边界结果占位输出
        },
    ]

    # 返回带 mock vectors 的 CSR SpMV reference-first 示例。
    return dict_payload

# LZ77 reference-first 示例保留 token-stream 语义，不进入默认 runtime pattern。
def lz77_reference_example() -> dict[str, Any]:
    """生成 LZ77 reference-only 示例 payload。

    参数:
        无外部业务参数。

    返回:
        返回附带 AXIS mock vectors 的 LZ77 示例 spec。
    """

    # LZ77 示例用于保留 token stream 事实，不声明一线 runtime 支持。
    dict_payload = make_spec(  # LZ77 reference-first 示例的完整 token-stream spec payload
        SpecTemplate(  # LZ77 token-stream reference-first 模板定义主体
            name="lz77_reference",  # LZ77 token-stream reference-first 示例标识名
            top="lz77_reference_kernel",  # LZ77 reference scaffold 的顶层函数名
            pattern="lz77",  # LZ77 压缩参考样例的模式标识
            description="Reference-first LZ77 example kept out of Tier 1 runtime pattern support.",  # LZ77 保持 reference-first、避免提升为默认 runtime 模式的定位说明
            interface_family="axi_stream",  # LZ77 必须保留流式 token 入口与出口
            arguments=[  # LZ77 在这里显式展开 in_stream、out_stream 和 length 三个 token-stream 参数
                {
                    "name": "in_stream",  # LZ77 token 输入流端口
                    "type": "hls::stream<ap_uint<32> >&",  # LZ77 token 输入流类型
                    "direction": "input",  # LZ77 token 输入流方向
                    "interface": "axis",  # LZ77 token 输入流接口协议
                },
                {
                    "name": "out_stream",  # LZ77 token 输出流端口
                    "type": "hls::stream<ap_uint<32> >&",  # LZ77 token 输出流类型
                    "direction": "output",  # LZ77 token 输出流方向
                    "interface": "axis",  # LZ77 token 输出流接口协议
                },
                {
                    "name": "length",  # LZ77 长度控制端口
                    "type": "int",  # LZ77 长度控制参数类型
                    "direction": "input",  # LZ77 长度控制参数方向
                    "interface": "s_axilite",  # LZ77 长度控制接口协议
                },
            ],
            metadata={"representation": "token_stream", "tier": "reference_first"},  # LZ77 token 表示与 reference-first 分层元数据
            confirmation_notes=(  # 记录 LZ77 暂不提升为默认 runtime 模式的保留理由
                "Keep LZ77 as a reference-first example until dictionary-window "
                "validation coverage grows."
            ),
            behavior=[  # 说明 LZ77 示例只保留滑窗压缩边界语义，不提升为默认 runtime 模式
                "Describe tokenized sliding-window compression boundaries without claiming first-class runtime support."  # 约束文案只描述滑窗压缩边界，不把示例提升为默认 runtime 能力
            ],
            constraints=[  # 限定 LZ77 token-stream 样例只用于 examples 展示，不进入 runtime 默认模式发现路径
                "Do not upgrade this example into default mock or provider behavior yet."  # 提醒维护者字典窗口验证尚未补齐，当前禁止把此示例接入默认 mock/provider 链路
            ],
            headers=["ap_int.h", "hls_stream.h"],  # LZ77 样例需要同时暴露整数 token 位宽声明和 stream 接口声明所依赖的头文件
            pragmas=["#pragma HLS PIPELINE II=1"],  # LZ77 样例把单周期启动间隔显式写死在 spec 中，保证 reference 输出描述一致
        )
    )

    # LZ77 示例复用 AXIS mock vectors，仅验证接口形态。
    dict_payload["workflow"]["mock_vectors"] = axis_vectors()  # LZ77 token-stream 示例复用的 AXIS mock vectors

    # 返回保留 token-stream 接口事实的 LZ77 reference-first 示例。
    return dict_payload

# reference-only 示例作为 examples 资产输出，但不升级为模板族。
def make_reference_only_examples() -> dict[str, dict[str, Any]]:
    """生成 reference-only 示例 payload 映射。

    参数:
        无外部业务参数。

    返回:
        返回以示例 JSON 文件名为键、示例 spec 为值的映射。
    """

    # 聚合非 Tier 1 示例，调用方无需了解每个示例的构造细节。
    dict_examples = {  # 把 hls_spmv_csr_reference_spec.json 和 hls_lz77_reference_spec.json 这两个最终文件名固定在这里，避免 reference-only 资产写盘时串到别的 examples 文件
        "hls_spmv_csr_reference_spec.json": spmv_reference_example(),  # CSR 稀疏矩阵 reference-first 示例载荷
        "hls_lz77_reference_spec.json": lz77_reference_example(),  # LZ77 token-stream reference-first 示例载荷
    }

    # 返回 reference-only 示例集合供 render_assets 写入 examples 目录。
    return dict_examples

# 资产渲染入口统一负责 references、templates 和 examples 三类输出。
def render_assets() -> None:
    """将参考文档、模板和示例 payload 写入技能资产目录。

    参数:
        无外部业务参数。

    返回:
        无返回值，直接更新 references、templates 和 examples 目录文件。
    """

    # 三类资产目录在循环前统一解析，避免循环体首句直接调用路径函数。
    path_references_dir = references_dir()  # references 资产输出目录

    # templates 输出目录单独缓存，后面每个模板文件都复用这个根路径。
    path_templates_dir = templates_dir()  # 模板 JSON 资产输出目录

    # examples 输出目录同时服务模板派生和 reference-first 两段写入。
    path_examples_dir = examples_dir()  # 示例 spec 资产输出目录

    # 参考文档先按当前文件正文重新写入，保持脚本输出顺序稳定。
    for str_name, str_text in docs_payloads().items():

        # 当前参考文档输出路径由 references 目录和文件名组成。
        path_output = path_references_dir / str_name  # 当前参考文档输出路径

        # 写回同名参考文档，保持脚本生成资产可复现。
        write_text(path_output, str_text)

    # 先整体生成模板 payload，后续示例派生直接从这张表深拷贝。
    dict_templates = template_payloads()  # 供示例派生复用的全量模板 payload 映射

    # 模板资产写入独立目录，供 runtime 和文档验证复用。
    for str_name, dict_payload in dict_templates.items():

        # 当前模板输出路径由 templates 目录和 JSON 文件名组成。
        path_output = path_templates_dir / str_name  # 当前模板 JSON 输出路径

        # 写出单个模板 payload，供后续 runtime 读取。
        write_json(path_output, dict_payload)

    # 示例覆盖从模板深拷贝，避免 mock vectors 修改污染模板资产。
    for str_name, (str_template_name, list_vectors) in example_overrides().items():

        # 当前示例先定位来源模板 payload，再进行深拷贝改写。
        dict_template_payload = dict_templates[str_template_name]  # 当前示例来源模板 payload

        # 从模板映射复制当前示例的源 payload。
        dict_payload = deepcopy(dict_template_payload)  # 模板派生 example spec payload 副本

        # workflow 字段可能缺失，示例写入前统一补齐可变映射。
        dict_payload["workflow"] = deepcopy(dict_payload.get("workflow") or {})  # 当前 example spec 的 workflow 配置

        # mock vectors 需要深拷贝，避免不同示例共享可变列表。
        dict_payload["workflow"]["mock_vectors"] = deepcopy(list_vectors)  # 当前示例独立 mock vectors

        # 示例名称去掉 template 后缀，和输出文件名保持一致语义。
        dict_payload["name"] = dict_payload["name"].removesuffix("_template")  # 当前 example spec 名称

        # 顶层函数名同样去掉 template 后缀，匹配 example spec。
        str_template_suffix = "_template"  # example 命名中需要移除的模板后缀

        # 先得到去掉 template 后缀后的 example 顶层函数名。
        str_example_top_function = dict_payload["interfaces"]["top_function"].removesuffix(str_template_suffix)  # 当前 example 顶层函数名

        # 把 example 顶层函数名写回 interfaces 字段。
        dict_payload["interfaces"]["top_function"] = str_example_top_function  # 回写 example 顶层函数名

        # outputs 按更新后的顶层函数名重新生成。
        dict_payload["outputs"] = outputs(dict_payload["interfaces"]["top_function"])  # 当前 example 预期输出文件映射

        # 当前派生示例输出路径由 examples 目录和文件名组成。
        path_output = path_examples_dir / str_name  # 当前模板派生示例输出路径

        # 写出由模板派生的 example spec。
        write_json(path_output, dict_payload)

    # reference-only 示例直接写入 examples，不进入模板覆盖流程。
    for str_name, dict_payload in make_reference_only_examples().items():

        # 当前 reference-first 示例输出路径保留原始文件名，不再走模板派生命名逻辑。
        path_output = path_examples_dir / str_name  # 当前 reference-first 示例输出路径

        # 写出 reference-first 示例，保持其与模板派生示例分离。
        write_json(path_output, dict_payload)

# 仅在脚本直跑时执行资产重渲染，避免导入阶段触发写文件副作用。
if __name__ == "__main__":

    # 入口标记让脚本调用语句具备项目要求的赋值说明结构。
    bool_entrypoint_active = True  # 脚本入口执行标记

    # 脚本入口只执行资产渲染，便于质量门和测试直接导入函数。
    if bool_entrypoint_active:

        # 当前文件作为脚本执行时才渲染资产。
        render_assets()
