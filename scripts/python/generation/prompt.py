"""渲染 AMD/Xilinx Vitis HLS 最终 prompt 与 staged prompt 合同。"""

# 使用未来注解避免前向类型在运行时过早求值。
from __future__ import annotations

# dataclass 让 render_prompt 的受控选项保持只读结构。
from dataclasses import dataclass

# Path 承担 staged 上下文目录的类型表达。
from pathlib import Path

# Any 覆盖 spec、manifest 与 JSON-like 载荷的混合值类型。
from typing import Any

# normalize_spec 负责把 prompt 入口看到的 spec 统一到稳定结构。
from scripts.python.generation.spec import normalize_spec

# 注释语言与合法语言枚举由 user_config 统一管理。
from scripts.python.config.user_config import COMMENT_LANGUAGES, require_comment_language

# staged workflow 允许的稳定阶段枚举。
PROMPT_STAGES = ("requirements", "codegen_plan", "tests", "hls")  # staged prompt 合法阶段列表

# comment_language 对外保留 auto 兼容层，其余选项复用 user_config 枚举。
COMMENT_LANGUAGE_CHOICES = ("auto", *COMMENT_LANGUAGES)  # prompt facade 支持的注释语言候选值

# prompt 预算档位只暴露稳定枚举，避免外层自由拼写。
PROMPT_BUDGETS = ("normal", "compact", "repair")  # prompt 上下文预算枚举

# 兼容旧调用方的关键字参数白名单。
RENDER_PROMPT_OPTION_NAMES = frozenset(  # render_prompt 允许透传的 legacy kwargs 名称集合
    {
        "context_manifest",  # legacy kwargs 中的上游 manifest 键
        "context_dir",  # legacy kwargs 中的上游工件目录键
        "evidence",  # legacy kwargs 中的验证证据键
        "memory",  # legacy kwargs 中的历史记忆键
        "comment_language",  # legacy kwargs 中的注释语言键
        "vector_contract",  # legacy kwargs 中的向量合同键
        "codegen_plan",  # legacy kwargs 中的代码计划键
        "subfunction",  # legacy kwargs 中的兼容子函数键
        "budget",  # legacy kwargs 中的预算档位键
        "hls_profile",  # legacy kwargs 中的显式 profile 键
        "decision",  # legacy kwargs 中的人工决策键
    }
)

# 非 hls stage 使用目录片段模板，保持 staged workflow 输出合同稳定。
STAGED_FILE_TEMPLATES = {  # staged workflow 固定目录片段模板映射
    'requirements': ((('plan', '{name}_requirements.json'), 'requirements', 'json'),),  # 需求阶段只要求模型回填规范化需求 JSON
    'codegen_plan': ((('plan', '{name}_codegen_plan.json'), 'codegen_plan', 'json'),),  # 规划阶段单独沉淀实现计划，供后续生成阶段复用
    'tests': ((('plan', '{name}_test_vectors.json'), 'test_vectors', 'json'),),  # 测试阶段输出语义向量合同，约束后续 testbench 设计
}

# prompt 上下文预算统一映射到固定字符上限。
CONTEXT_CHAR_LIMITS = {  # staged 工件上下文的字符预算上限
    'compact': 12000,  # 紧凑预算限制上游上下文体积
    'normal': 24000,  # 常规预算允许更完整的阶段上下文
    'repair': 24000,  # 修复预算与常规预算保持同上限
}

# memory 只保留最近且相关的固定条数，避免 staged prompt 膨胀。
MEMORY_ENTRY_LIMIT = 20  # staged prompt 最多保留的 memory 条目数

# 某些 JSON 合同类工件应优先保留完整正文。
FULL_CONTEXT_TOKENS = (  # 需要完整上下文优先策略的路径关键词
    'vector',  # 向量文件优先保留全文
    'semantic_transcript',  # 语义转录记录优先保留全文
    'contract',  # 通用合同类文件优先保留全文
)

# 常见文件后缀到 manifest language 字段的稳定映射。
PATH_LANGUAGE_BY_SUFFIX = {  # 输出文件后缀到语言名的映射
    'cpp': 'cpp',  # C++ 源文件后缀映射
    'cc': 'cpp',  # GCC 风格 C++ 源文件后缀映射
    'cxx': 'cpp',  # 扩展 C++ 源文件后缀映射
    'h': 'cpp',  # 头文件默认按 C++ 语言处理
    'hpp': 'cpp',  # C++ 头文件扩展后缀映射
    'cfg': 'ini',  # cfg 文件按 ini 语义标记
    'json': 'json',  # JSON 文件语言标签
    'py': 'python',  # Python 参考模型文件标签
}

# 保存 render_prompt 的受控可选参数集合。
@dataclass(frozen=True)
class RenderPromptOptions:
    """
    保存 render_prompt 的受控可选参数集合。

    :param context_manifest: 上一阶段产物 manifest；为空时不注入 staged 产物上下文。
    :param context_dir: 上一阶段产物根目录；为空时不读取上游工件文本。
    :param evidence: 已有验证证据；为空时回退空对象。
    :param memory: 历史错误与约束记忆；为空时回退空对象。
    :param comment_language: 注释语言策略；会先经过 user_config 校验。
    :param vector_contract: 参考向量契约；为空时不追加额外向量规则。
    :param codegen_plan: 代码生成计划；staged prompt 可直接复用。
    :param subfunction: 旧 facade 兼容字段；当前仅保留入参兼容，不参与逻辑。
    :param budget: prompt 预算档位；影响 staged 工件摘要字符上限。
    :param hls_profile: 显式 HLS profile；优先级高于 spec 内嵌 profile。
    :param decision: 人工决策补丁；为空时不追加可选 JSON 章节。
    :return: 无业务返回值；仅用于承载受控可选参数。
    """

    # staged prompt 可直接消费的上游 manifest。
    context_manifest: dict[str, Any] | None = None  # staged 上游产物 manifest

    # staged prompt 读取工件正文时使用的根目录。
    context_dir: Path | None = None  # staged 上下文工件目录

    # 已有验证证据会被注入为独立 JSON 章节。
    evidence: dict[str, Any] | None = None  # staged 证据上下文字典

    # 历史约束记忆会按 stage 过滤后再注入 prompt。
    memory: dict[str, Any] | None = None  # staged memory 约束字典

    # 注释语言策略由 facade 传入，再交给 user_config 统一校验。
    comment_language: str = "zh"  # prompt 使用的注释语言策略

    # 参考向量契约为 tests/hls 阶段提供附加规则。
    vector_contract: dict[str, Any] | None = None  # 参考向量契约对象

    # codegen plan 由 requirements 阶段产物或 workflow 注入。
    codegen_plan: dict[str, Any] | None = None  # 代码生成计划对象

    # 旧 facade 仍可能传入 subfunction，这里只保留兼容入口。
    subfunction: str | None = None  # 旧调用方兼容的子函数名

    # 预算档位决定 staged 上下文读取与切块上限。
    budget: str = "normal"  # prompt 上下文预算档位

    # 显式 HLS profile 可以覆盖 spec 内联 profile。
    hls_profile: dict[str, Any] | None = None  # 显式 HLS profile 字典

    # 人工决策补丁会追加到最终 prompt 的可选 JSON 章节。
    decision: dict[str, Any] | None = None  # 人工决策约束字典

# 根据 HLS spec 渲染最终 prompt 或 staged prompt。
def render_prompt(
    spec: dict[str, Any],
    target: str | None = None,
    stage: str | None = None,
    *,
    options: RenderPromptOptions | None = None,
    **legacy_options: Any,
) -> str:
    """
    根据 HLS spec 渲染最终 prompt 或 staged prompt。

    :param spec: 已确认的 HLS 规格字典。
    :param target: 目标生成域；当前仅支持 HLS。
    :param stage: staged workflow 的阶段名；为空时渲染最终 HLS prompt。
    :param options: 结构化受控选项对象；为空时使用默认选项基线。
    :param legacy_options: 兼容旧 facade 的关键字参数集合。
    :return: 可直接写入文件或发送给模型的提示词文本。
    """

    # 先统一归一化 spec，确保 prompt、workflow 与 validation 看到同一份结构。
    dict_normalized_spec = normalize_spec(spec, target=target)  # 规范化后的 HLS spec

    # 把旧 kwargs 折叠为结构化受控选项，避免公开函数继续增长参数面。
    render_prompt_options_prompt_options = _normalize_render_options(  # 规范化后的 prompt 选项对象
        options,  # 调用方显式提供的结构化选项
        legacy_options,  # 兼容旧 facade 的关键字参数集合
    )

    # stage 存在时走 staged workflow 渲染分支。
    if stage:

        # staged workflow 只接受固定枚举阶段。
        str_stage = _require_stage(stage)  # 归一化后的 stage 名称

        # 返回当前阶段专用 prompt。
        return _render_staged_prompt(
            dict_normalized_spec,
            str_stage,
            render_prompt_options_prompt_options,
        )

    # 返回最终 HLS 代码生成 prompt。
    return _render_hls_prompt(
        dict_normalized_spec,
        render_prompt_options_prompt_options,
    )

# 校验并归一化 prompt 预算档位。
def require_prompt_budget(budget: str) -> str:
    """
    校验并归一化 prompt 预算档位。

    :param budget: 外层传入的 prompt 预算字符串。
    :return: 归一化后的小写预算档位。
    异常:
        ValueError: 当 budget 不是 compact、normal、repair 三档之一时抛出。
    """

    # 预算名统一转小写，兼容 CLI 或 facade 的大小写输入。
    str_budget = budget.lower()  # 归一化后的预算档位

    # 非法预算值需要尽早阻断，避免 staged 上下文分支悄悄回退。
    if str_budget not in PROMPT_BUDGETS:

        # 报告允许的固定预算枚举。
        raise ValueError(
            f"> ERR: [Python] Prompt budget must be one of {', '.join(PROMPT_BUDGETS)}."
        )

    # 返回供 staged context 与 memory 筛选逻辑复用。
    return str_budget

# 把 legacy kwargs 合并为结构化 RenderPromptOptions。
def _normalize_render_options(
    options: RenderPromptOptions | None,
    legacy_options: dict[str, Any],
) -> RenderPromptOptions:
    """
    把旧关键字参数折叠为结构化渲染选项。

    :param options: 调用方显式提供的 RenderPromptOptions；为空时使用默认对象。
    :param legacy_options: 兼容旧 facade 的关键字参数集合。
    :return: 合并 legacy kwargs 后的 RenderPromptOptions。
    异常:
        TypeError: 当 legacy kwargs 含有未注册字段时抛出。
    """

    # 未注册的 kwargs 必须尽早失败，避免调用方误以为选项已经生效。
    set_unknown_option_names = set(legacy_options) - set(RENDER_PROMPT_OPTION_NAMES)  # 未注册的 legacy 关键字名集合

    # 发现未知关键字时直接阻断。
    if set_unknown_option_names:

        # 把未知字段名按稳定顺序拼成异常文本。
        str_unknown_option_names = ", ".join(sorted(set_unknown_option_names))  # 未知 legacy 关键字名列表文本

        # 阻断未知 kwargs，避免 facade 误用静默漂移。
        raise TypeError(
            f"> ERR: [Python] Unexpected render_prompt option(s): {str_unknown_option_names}."
        )

    # 调用方未显式提供 options 时，使用默认值对象作为合并基线。
    render_prompt_config_base = options or RenderPromptOptions()  # legacy 参数合并前的选项基线

    # subfunction 只保留兼容层，不参与当前 prompt 逻辑。
    str_legacy_subfunction = legacy_options.get("subfunction", render_prompt_config_base.subfunction)  # 兼容旧 facade 的子函数名
    del str_legacy_subfunction

    # comment_language 统一先经过 user_config 校验，保证 staged prompt 协同规则稳定。
    str_comment_language = require_comment_language(  # 统一校验 legacy 或默认注释语言值
        str(legacy_options.get("comment_language", render_prompt_config_base.comment_language))  # 原始注释语言输入
    )

    # budget 统一走固定枚举校验，避免上下文预算出现静默分叉。
    str_budget = require_prompt_budget(str(legacy_options.get("budget", render_prompt_config_base.budget)))  # 规范化后的预算档位

    # 返回完整合并后的 RenderPromptOptions。
    return RenderPromptOptions(
        context_manifest=legacy_options.get(
            "context_manifest",
            render_prompt_config_base.context_manifest,
        ),
        context_dir=legacy_options.get(
            "context_dir",
            render_prompt_config_base.context_dir,
        ),
        evidence=legacy_options.get(
            "evidence",
            render_prompt_config_base.evidence,
        ),
        memory=legacy_options.get("memory", render_prompt_config_base.memory),
        comment_language=str_comment_language,
        vector_contract=legacy_options.get(
            "vector_contract",
            render_prompt_config_base.vector_contract,
        ),
        codegen_plan=legacy_options.get(
            "codegen_plan",
            render_prompt_config_base.codegen_plan,
        ),
        subfunction=render_prompt_config_base.subfunction,
        budget=str_budget,
        hls_profile=legacy_options.get(
            "hls_profile",
            render_prompt_config_base.hls_profile,
        ),
        decision=legacy_options.get(
            "decision",
            render_prompt_config_base.decision,
        ),
    )

# 校验并归一化 staged prompt 的阶段名。
def _require_stage(stage: str) -> str:
    """
    校验并归一化 staged prompt 阶段名。

    :param stage: 调用方请求的阶段名。
    :return: 归一化后的小写阶段名。
    异常:
        ValueError: 当 stage 不属于 staged workflow 固定阶段枚举时抛出。
    """

    # stage 与 CLI 选项共享同一套枚举，因此统一做大小写折叠。
    str_stage = stage.lower()  # 当前调用请求的 stage 规范化结果

    # 非法阶段需要尽早阻断，避免 stage contract 漂移。
    if str_stage not in PROMPT_STAGES:

        # 报告 HLS-only 阶段枚举边界。
        raise ValueError(
            "> ERR: [Python] This skill is HLS-only; stage must be one of "
            + ", ".join(PROMPT_STAGES)
            + "."
        )

    # 返回供 staged 渲染分支继续使用。
    return str_stage

# 渲染最终 HLS 代码生成 prompt。
def _render_hls_prompt(
    spec: dict[str, Any],
    options: RenderPromptOptions,
) -> str:
    """
    渲染最终 HLS 代码生成 prompt。

    :param spec: 规范化后的 HLS spec。
    :param options: 受控 prompt 选项对象。
    :return: 最终 HLS 代码生成 prompt 文本。
    """

    # 显式 hls_profile 优先；未提供时回退到 spec 内联 profile。
    dict_hls_profile = _effective_hls_profile(spec, options.hls_profile)  # 当前 prompt 使用的 HLS profile

    # 先渲染基础 prompt 合同，再按需附加 profile 与 decision 章节。
    str_prompt = _base_prompt(  # 最终 HLS prompt 的基础正文
        spec=spec,  # 当前渲染使用的规范化 HLS spec
        title="Vitis HLS generation task",  # 最终 prompt 的标题行
        target_line="Generate AMD-Xilinx Vitis HLS compatible C/C++ artifacts only.",  # HLS-only 目标边界说明
        rules=_hls_rules(spec, options.comment_language, dict_hls_profile),  # 最终 prompt 规则集合
        manifest=_manifest_for(spec),  # 最终输出合同 manifest
    )

    # 返回带可选 JSON 章节的最终 prompt 文本。
    return _append_optional_sections(
        str_prompt,
        hls_profile=dict_hls_profile,
        decision=options.decision,
    )

# 统一解析 prompt 应使用的 HLS profile。
def _effective_hls_profile(
    spec: dict[str, Any],
    explicit_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    统一解析当前 prompt 应使用的 HLS profile。

    :param spec: 规范化后的 HLS spec。
    :param explicit_profile: 显式传入的 HLS profile。
    :return: 当前 prompt 应使用的 HLS profile 字典。
    """

    # 显式 profile 有内容时优先级最高。
    if explicit_profile:

        # 返回外层显式指定的 HLS profile。
        return explicit_profile

    # 未显式指定时回退到 spec 内联 profile；缺失则回退空对象。
    obj_spec_profile = spec.get("hls_profile") or {}  # spec 内联的 HLS profile 字段

    # 只有字典类型才视为合法 profile。
    if isinstance(obj_spec_profile, dict):

        # 返回 spec 内联的 HLS profile。
        return obj_spec_profile

    # 其它异常类型一律回退为空对象，保持旧行为。
    return {}

# 渲染 requirements/codegen_plan/tests/hls 阶段 prompt。
def _render_staged_prompt(
    spec: dict[str, Any],
    stage: str,
    options: RenderPromptOptions,
) -> str:
    """
    渲染 requirements/codegen_plan/tests/hls 阶段 prompt。

    :param spec: 规范化后的 HLS spec。
    :param stage: 已校验通过的 stage 名称。
    :param options: 受控 prompt 选项对象。
    :return: 对应 stage 的 staged prompt 文本。
    """

    # stage manifest 决定模型必须返回哪些文件，是 staged workflow 的核心合同。
    dict_manifest = _stage_manifest_for(spec, stage)  # 当前 stage 对应的 manifest

    # staged guidance 统一生成标题、目标和规则列表。
    tuple_stage_guidance = _stage_guidance(  # 当前阶段的标题、目标和规则三元组
        spec,  # 当前阶段共用的规范化 HLS spec
        stage,  # 已校验通过的 stage 名称
        options.comment_language,  # 注释语言策略
        options.vector_contract,  # 参考向量合同
        _effective_hls_profile(spec, options.hls_profile),  # 阶段生效的 HLS profile
    )

    # 解包当前阶段的标题、目标和规则列表。
    str_stage_title, str_stage_goal, list_stage_rules = tuple_stage_guidance  # 当前阶段 guidance 解包结果

    # staged prompt 的 JSON 章节保持固定顺序，便于 workflow 与测试快照比较。
    list_sections = [  # staged prompt 的固定章节列表
        ("HLS spec", _json_code_block(spec)),  # 原始 HLS 规格 JSON 章节
        ("Stage rules", _bullet_list(list_stage_rules)),  # 当前阶段规则列表章节
        (
            "Prior artifact context",  # 上游产物摘要章节
            _json_code_block(  # 把上游产物上下文编码为 JSON 章节正文
                _artifact_context(  # 汇总 staged prompt 需要的上游工件上下文
                    options.context_manifest,  # 上游 manifest 输入
                    options.context_dir,  # 上游工件目录输入
                    budget=options.budget,  # 限制上游工件摘要长度的预算档位
                )
            ),
        ),
        ("Evidence context", _json_code_block(options.evidence or {})),  # 验证证据章节
        (
            "Prompt memory constraints",  # 历史约束记忆章节
            _json_code_block(  # 把历史记忆约束编码为 JSON 章节正文
                _memory_constraints(  # 按当前阶段过滤可见的历史约束条目
                    options.memory,  # 历史错误与约束记忆
                    stage,  # 当前阶段名
                    budget=options.budget,  # 控制记忆筛选规模的预算档位
                )
            ),
        ),
        ("Code generation plan", _json_code_block(options.codegen_plan or {})),  # 预生成计划章节
        (
            "Reference vector contract",  # 参考向量合同章节
            _json_code_block(options.vector_contract or {}),  # 向量合同 JSON 正文
        ),
        (
            "HLS profile constraints",  # HLS profile 约束章节
            _json_code_block(  # 把规范化后的 HLS profile 编码为 JSON 章节正文
                _effective_hls_profile(spec, options.hls_profile)  # 规范化后的 HLS profile 约束
            ),
        ),
        ("Human decision constraints", _json_code_block(options.decision or {})),  # 人工决策补丁章节
        ("Output contract", _stage_output_contract_text(dict_manifest)),  # 返回文件合同章节
    ]

    # 标题段显式说明阶段目标、返回边界和预算。
    list_prompt_lines = [  # staged prompt 头部行序列
        f"# {str_stage_title}",  # 当前阶段标题行
        "",  # 标题与正文之间的空行
        "You are executing an HLS-only staged generator. "
        f"Stage goal: {str_stage_goal}",  # 当前阶段目标说明
        "Think internally, then return only the requested fenced blocks.",  # 输出边界约束
        f"Prompt budget: {options.budget}.",  # 当前上下文预算说明
        "",  # 头部说明与章节列表之间的空行
    ]

    # 逐段展开固定章节，保持 markdown 结构稳定。
    for str_section_title, str_section_body in list_sections:

        # 以固定顺序追加 markdown 标题与正文。
        list_prompt_lines.extend(
            [f"## {str_section_title}", "", str_section_body, ""]
        )

    # 返回 staged prompt 最终文本。
    return "\n".join(list_prompt_lines).rstrip() + "\n"

# 输出合同与 Markdown 章节 helper 收敛到 sections 子模块，根文件保留稳定入口。
from scripts.python.generation.prompt_sections import (
    _append_optional_sections,
    _base_prompt,
    _bullet_list,
    _json_code_block,
    _stage_output_contract_text,
)

# staged manifest 组装 helper 收敛到 manifest 子模块，同时保留根模块导出面。
from scripts.python.generation.prompt_manifest import (
    _manifest_for,
    _stage_manifest_for,
)

# prompt 规则组合逻辑收敛到 rules 子模块。
from scripts.python.generation.prompt_rules import (
    _hls_rules,
    _stage_guidance,
)

# staged 上下文与历史 memory 过滤逻辑收敛到 context 子模块。
from scripts.python.generation.prompt_context import (
    _artifact_context,
    _memory_constraints,
)
