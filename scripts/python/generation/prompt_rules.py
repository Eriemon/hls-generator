"""prompt 规则列表与阶段 guidance helper。"""

# 使用未来注解避免前向类型在运行时过早求值。
from __future__ import annotations

# json 负责把 profile、performance 等对象稳定序列化进规则文本。
import json
from typing import Any

# Vitis skill 偏好配置会被转成 prompt 里的推荐规则。
from scripts.python.config.hls_config import resolve_vitis_skill_preference

# pattern 规则和必需头文件由 patterns.py 统一提供。
from scripts.python.generation.patterns import pattern_prompt_rules, required_pattern_headers

# 注释语言枚举与向量契约 hash 标签会被注入规则文本。
from scripts.python.generation.prompt import COMMENT_LANGUAGE_CHOICES
from scripts.python.generation.vectors import VECTOR_HASH_TAG

# 生成最终 HLS prompt 的规则列表。
def _hls_rules(
    spec: dict[str, Any],
    comment_language: str,
    hls_profile: dict[str, Any],
) -> list[str]:
    """
    生成最终 HLS prompt 的规则列表。

    :param spec: 规范化后的 HLS spec。
    :param comment_language: 外层请求的注释语言策略。
    :param hls_profile: 当前 prompt 生效的 HLS profile。
    :return: 最终 HLS prompt 应使用的规则列表。
    """

    # pattern 规则从 patterns.py 注入，保持与 profile 元数据统一。
    list_pattern_rules = pattern_prompt_rules(spec)  # pattern 派生的额外规则

    # 某些模式要求显式包含特定头文件，这里把它们前置为专门规则。
    list_required_headers = required_pattern_headers(hls_profile)  # 当前 pattern 要求的头文件集合

    # 组装最终 HLS prompt 使用的基础规则与扩展规则。
    list_rules = [
        "Target Vitis HLS 2022.2+ compatible C/C++ and script/config artifacts.",  # HLS 目标工具版本边界
        "Use the stable Tcl/.cfg execution flow only; do not generate alternate execution-flow artifacts.",  # 只允许稳定 Tcl/cfg 流程
        "Implement the top function named exactly as interfaces.top_function when present; otherwise use spec.name.",  # top function 命名合同
        "Use fixed-width ap_int/ap_uint/ap_fixed types where they improve hardware intent.",  # 定宽数值类型优先策略
        (
            "Use HLS libraries deliberately: default to ap_int.h, ap_fixed.h, "
            "hls_stream.h, and hls_math.h; use advanced libraries such as "
            "hls_task.h, hls_vector.h, or hls_streamofblocks.h only for explicit "
            "requirements."
        ),
        "Add #pragma HLS INTERFACE pragmas for all external arguments and the return control interface.",  # 外部接口 pragma 全覆盖要求
        (
            "For AXI4 memory ports use m_axi with explicit bundles and concrete "
            "depth values for C/RTL co-simulation; for AXI4-Stream ports use "
            "hls::stream with axis interfaces; for native scalar controls use "
            "s_axilite or the requested native control mode."
        ),
        (
            "Identify the intended HLS pattern before choosing pragmas: scalar "
            "pipeline, local-buffer partition/reshape, read-compute-write "
            "dataflow, multi-m_axi bandwidth, or fixed/float numeric strategy."
        ),
        (
            "Start from a validated sequential baseline and a self-checking C "
            "simulation before introducing performance pragmas."
        ),
        (
            "Add PIPELINE, DATAFLOW, ARRAY_PARTITION, ARRAY_RESHAPE, UNROLL, or "
            "STREAM pragmas only when justified by loop structure, memory access "
            "pattern, or explicit performance evidence."
        ),
        (
            "Use report-driven reasoning: target II, achieved II, loop interval, "
            "load/store bottlenecks, timing slack, interface bandwidth, and "
            "resource growth should explain each optimization choice."
        ),
        (
            "When pipelining an outer loop, account for implied inner-loop "
            "concurrency; if the bottleneck is parallel memory access, choose "
            "partition, reshape, or banking based on the accessed dimension."
        ),
        (
            "Keep compile/link boundaries conceptually clear: generated HLS "
            "source should express kernel behavior and interface intent without "
            "absorbing host or package-stage orchestration."
        ),
        (
            "For variable-bound loops, keep the control structure honest: "
            "require a justified maximum bound before aggressive unroll or "
            "complete banking, and use tripcount guidance only as reporting "
            "support."
        ),
        (
            "Treat pointer aliasing, template expansion, and vector-style packed "
            "operations as modeling choices that must preserve explicit "
            "interface intent and testability."
        ),
        (
            "Place #pragma HLS directives at the function or loop scope they "
            "control, keep dataflow regions free of global-state coupling and "
            "recursion, and do not combine array_partition and array_reshape on "
            "the same variable."
        ),
        (
            "Prefer concentrating dense pragma usage in a small number of "
            "hotspot helper/source files instead of spreading complex "
            "directives uniformly across every file in a multi-module kernel "
            "layout."
        ),
        (
            "For DATAFLOW designs, split read/compute/write stages with clear "
            "hls::stream FIFO boundaries and explicit stream depth when "
            "producer and consumer rates can differ."
        ),
        (
            "Distinguish control-driven orchestration from data-driven task "
            "graphs; only introduce task-level parallel structure when restart "
            "behavior, channel ownership, and stage boundaries are explicit."
        ),
        (
            "For fixed-point or floating-point designs, document the "
            "range/precision tradeoff and explicitly decide whether "
            "unsafe_math_optimizations is allowed."
        ),
        (
            "Treat target-part migration as a QoR portability review: preserve "
            "interface and numeric intent while comparing interval, latency, "
            "slack, and resource deltas across devices."
        ),
        (
            "Treat DSP-oriented transforms and filters as explicit "
            "requirements; do not inject FFT, FIR, or intrinsic-heavy "
            "structures unless the spec calls for them."
        ),
        (
            "Do not use deprecated Vivado/Vitis HLS commands or pragmas: "
            "config_sdx, set_directive_data_pack, set_directive_resource, "
            "DATA_PACK, or hls_linear_algebra.h."
        ),
        "Ensure hls_config.cfg includes exact syn.top and syn.file entries when a cfg file is requested.",  # cfg 顶层与输入文件条目合同
        (
            "Avoid dynamic allocation, recursion, exceptions, RTTI, "
            "std::vector, and unsupported standard library features."
        ),
        "Include a self-checking C++ testbench and hls_config.cfg when requested by outputs.",  # 输出需求触发 testbench 与 cfg 交付
        "Make generated HLS suitable for Vitis C simulation, synthesis, and co-simulation.",  # 覆盖 csim、synth、cosim 三类流程
        *_vitis_skill_rules(),  # Vitis skill 选择建议
        *_performance_rules_for(spec),  # 性能目标补充规则
        *_hls_profile_rules(hls_profile),  # HLS profile 附加规则
        *_required_header_rules(list_required_headers),  # 头文件必备规则
        *list_pattern_rules,  # 模式识别附加规则
        *_comment_rules_for(comment_language),  # 注释语言治理规则
    ]  # 最终 HLS prompt 的完整规则集

    # 返回供最终 HLS prompt 正文引用。
    return list_rules

# 补充 Vitis 相关 skill 选择建议。
def _vitis_skill_rules() -> list[str]:
    """
    补充 Vitis 相关 skill 选择建议。

    参数:
        无额外业务参数；当前函数仅读取本地 skill 偏好配置。
    返回:
        面向模型的 Vitis skill 选择规则列表。
    """

    # skill 偏好配置由 config.py 统一解析，这里只负责转成 prompt 规则。
    dict_preference = resolve_vitis_skill_preference()  # 解析后的 Vitis skill 偏好配置

    # fallback skill 名单拼成稳定字符串，减少模型对顺序的自由发挥。
    str_fallback_skills = ", ".join(dict_preference["fallback_skills"])  # fallback skill 列表文本

    # 返回面向模型的 Vitis skill 推荐规则。
    return [
        (
            "For Vitis development, simulation, co-simulation, and HLS debug "
            "guidance, prefer the "
            f"`{dict_preference['selected_skill']}` Codex skill when available."
        ),
        f"If `{dict_preference['preferred_skill']}` is not installed, fall back to: {str_fallback_skills}.",
    ]

# 返回 staged prompt 的标题、目标和规则列表。
def _stage_guidance(
    spec: dict[str, Any],
    stage: str,
    comment_language: str,
    vector_contract: dict[str, Any] | None,
    hls_profile: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """
    返回 staged prompt 的标题、目标和规则列表。

    :param spec: 规范化后的 HLS spec。
    :param stage: 已校验通过的 stage 名称。
    :param comment_language: 注释语言策略。
    :param vector_contract: 参考向量契约。
    :param hls_profile: 当前 prompt 生效的 HLS profile。
    :return: 标题、目标和规则列表组成的三元组。
    """

    # 所有 stage 都共享基础边界规则，避免局部 stage 漏掉路径或 case-id 合同。
    list_common_rules = [
        "Do not use TODO, FIXME, ellipses, placeholder text, or unsupported HLS features.",  # 禁止模板残留与不支持特性
        "Preserve interfaces, case ids, and file paths exactly.",  # 接口、case-id 与路径必须稳定
    ]  # 所有 stage 共用的基础规则

    # requirements 阶段负责固化用户已经确认过的设计合同。
    if stage == "requirements":

        # requirements 阶段只固化需求合同，不提前承诺实现细节。
        return (
            "Confirmed HLS requirement normalization",
            "Normalize user-confirmed HLS requirements into a stable pre-generation contract.",
            [
                "Do not invent missing confirmation data; record unresolved items as open questions.",
                *list_common_rules,
            ],
        )

    # codegen_plan 阶段负责生成结构化实现计划。
    if stage == "codegen_plan":

        # codegen_plan 阶段为后续代码生成输出固定规划槽位。
        return (
            "HLS pre-generation code plan",
            "Produce a structured implementation plan before HLS code is generated.",
            [
                (
                    "Create requirements_summary, interface_decision, "
                    "pipeline_strategy, module_partition, width strategy, "
                    "verification_strategy, syntax_risk_checks, "
                    "open_questions, and ready_for_generation."
                ),
                "Keep ready_for_generation false when any interface or pipeline decision is unresolved.",
                *list_common_rules,
            ],
        )

    # tests 阶段负责生成确定性的语义向量合同。
    if stage == "tests":

        # tests 阶段把共享验证基准固化为确定性语义向量合同。
        return (
            "Semantic HLS test oracle generation",
            "Create deterministic HLS validation vectors and expected checkpoints.",
            [
                "Generate stable case ids, nominal cases, boundary cases, and invalid-input cases when relevant.",
                "Define expected outputs and checkpoints for each case.",
                *list_common_rules,
            ],
        )

    # 其余情况统一视为 hls 阶段。
    return (
        "Vitis HLS implementation generation",
        "Create HLS C/C++ source, header, self-checking testbench, and cfg artifacts.",
        [
            *_hls_rules(spec, comment_language, hls_profile),
            *_vector_contract_rules(vector_contract),
            *list_common_rules,
        ],
    )

# 根据外层语言策略补全 HLS 注释治理规则。
def _comment_rules_for(comment_language: str) -> list[str]:
    """
    返回注释语言与注释治理规则。

    :param comment_language: 外层请求的注释语言策略。
    :return: HLS 注释治理规则列表。
    """

    # HLS 代码注释合同始终固定为中文；这里仅保留外层语言策略的可见痕迹。
    str_language_note = (
        "外层 comment_language 可以控制 staged/python 协调上下文，但 HLS 注释合同固定要求中文。"  # 保留跨阶段协调语境
        if comment_language in COMMENT_LANGUAGE_CHOICES  # 已知语言策略沿用完整边界说明
        else "HLS 注释合同固定要求中文。"  # 非法策略值时只保留核心中文边界
    )  # 注释语言边界说明

    # 返回 HLS C/C++ 注释治理规则。
    return [
        "所有生成的 HLS C/C++ 注释必须使用中文；标识符、Vitis/HLS 工具名、协议名、pragma 关键字和 bundle 名可以保留英文。",
        str_language_note,
        "注释必须解释具体硬件意图或验证职责，不得使用“定义变量”“保存结果”“计算结果”“判断当前分支”“执行函数”“返回结果”等模板化套话。",
        "每个生成的 C/C++ 源文件或头文件都必须以中文文件角色注释开头，说明它是接口声明、内核实现、测试文件或其它明确职责。",
        "函数和方法契约注释放在紧邻上方的注释行，说明硬件边界、top function 角色、接口摘要、helper 阶段职责或 testbench 入口职责。",
        "#pragma HLS 上方必须有独立中文注释；INTERFACE 说明端口/协议/bundle/control，PIPELINE 说明 II/循环/吞吐，DATAFLOW/STREAM 说明阶段、通道、FIFO 深度或生产消费关系。",
        "变量定义、赋值、循环、条件分支、函数调用、assert/try/return 等有语义的代码块上方使用独立中文目的注释，并用空行分隔代码块。",
        "短控制头、普通局部声明和普通赋值在不超过项目行宽时必须保持单行；不要为了行宽猜测或注释布局拆开普通 for 头和变量初始化。",
        "循环注释必须说明迭代边界、事务长度、读写对象、token 或样本范围以及累加/比较目的；不要只写“遍历循环”。",
        "C++ testbench 必须用中文注释 main、用例准备、期望值、内核调用、观测输出、PASS/FAIL 上报和向量哈希。",
        "仅补注释的改写必须先保持去注释 token 指纹不变，再用可用 AST provider 证明结构不变；无可用 provider 时不能冒充已证明行为不变。",
        (
            "Use the manifest checks.reviewability_assessment field to "
            "summarize strict Chinese comment placement, AST guard status, "
            "and any limitations."
        ),
    ]

# 在 spec.performance 存在时补充性能约束。
def _performance_rules_for(spec: dict[str, Any]) -> list[str]:
    """
    在 spec.performance 存在时补充性能约束。

    :param spec: 规范化后的 HLS spec。
    :return: 由 performance 字段派生的附加规则列表。
    """

    # performance 为空时不追加规则，避免无意义占用 prompt 预算。
    dict_performance = spec.get("performance") or {}  # spec 中声明的性能约束

    # 未声明 performance 时返回空规则列表。
    if not dict_performance:

        # 保持没有性能字段时的最小 prompt 体积。
        return []

    # 返回由 performance 字段派生的规则列表。
    return [
        (
            "Honor explicit performance constraints in spec.performance and "
            "summarize latency, II, resource, and timing handling in the "
            "manifest."
        ),
        f"Performance constraints: {json.dumps(dict_performance, ensure_ascii=False, sort_keys=True)}",
    ]

# 在 profile 存在时补充 HLS profile 约束。
def _hls_profile_rules(profile: dict[str, Any]) -> list[str]:
    """
    在 profile 存在时补充 HLS profile 约束。

    :param profile: 当前 prompt 生效的 HLS profile。
    :return: 由 HLS profile 派生的附加规则列表。
    """

    # 未声明 profile 时不追加附加规则。
    if not profile:

        # 当前没有向量契约时直接回退为空规则集合。
        return []

    # 返回由 HLS profile 派生的规则列表。
    return [
        (
            "Honor the explicit hls_profile compatibility rules for "
            "interfaces, pragma policy, memory policy, and forbidden C++ "
            "features."
        ),
        (
            "Treat hls_profile.required_metadata_fields as mandatory design "
            "facts that must be reflected in comments, pragmas, and cfg "
            "behavior."
        ),
        f"HLS profile: {json.dumps(profile, ensure_ascii=False, sort_keys=True)}",
    ]

# 把模式要求的头文件集合转成 prompt 规则。
def _required_header_rules(required_headers: list[str]) -> list[str]:
    """
    把模式要求的头文件集合转成 prompt 规则。

    :param required_headers: 模式要求的头文件名列表。
    :return: 头文件约束规则列表。
    """

    # 没有必需头文件时不追加任何规则。
    if not required_headers:

        # 返回空列表保持 prompt 最小化。
        return []

    # 头文件顺序保持调用方返回顺序，便于测试断言具体名称。
    str_required_headers = ", ".join(required_headers)  # 需要出现在 prompt 中的头文件列表文本

    # 返回头文件约束规则。
    return [
        f"Include and justify the required HLS headers for this pattern: {str_required_headers}."
    ]

# 当存在向量合同输入时，把 case-id 与约束边界显式注入 prompt。
def _vector_contract_rules(
    vector_contract: dict[str, Any] | None,
) -> list[str]:
    """
    把参考向量契约扩展为模型必须遵守的附加规则。

    :param vector_contract: 参考向量契约对象；为空时不追加规则。
    :return: 向量契约相关的附加规则列表。
    """

    # 没有向量契约时不追加约束。
    if not vector_contract:

        # 返回空规则列表保持旧行为。
        return []

    # 返回向量契约派生的附加规则。
    return [
        "Mirror the reference vector contract exactly: "
        f"case_count={vector_contract.get('case_count')}, "
        f"case_ids={vector_contract.get('case_ids')}.",
        "Every generated HLS testbench must include a Chinese adjacent "
        f"comment that preserves `{VECTOR_HASH_TAG} {vector_contract.get('sha256')}` "
        "and explains this vector-contract hash.",
    ]
