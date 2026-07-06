"""渲染 AMD/Xilinx Vitis HLS 最终 prompt 与 staged prompt 合同。"""
# 使用未来注解避免前向类型在运行时过早求值。
from __future__ import annotations

# json 负责把 spec、manifest 与上下文字段稳定序列化为 JSON 代码块。
import json

# dataclass 让 render_prompt 的受控选项保持只读结构。
from dataclasses import dataclass

# Path 与纯路径类型共同承担 staged 上下文文件的安全解析。
from pathlib import Path, PurePosixPath, PureWindowsPath

# Any 覆盖 spec、manifest 与 JSON-like 载荷的混合值类型。
from typing import Any

# Vitis skill 偏好配置会被转成 prompt 里的推荐规则。
from scripts.python.config.hls_config import resolve_vitis_skill_preference

# pattern 规则和必需头文件由 patterns.py 统一提供。
from scripts.python.generation.patterns import pattern_prompt_rules, required_pattern_headers

# normalize_spec 负责把 prompt 入口看到的 spec 统一到稳定结构。
from scripts.python.generation.spec import normalize_spec

# 注释语言与合法语言枚举由 user_config 统一管理。
from scripts.python.config.user_config import COMMENT_LANGUAGES, require_comment_language

# 向量契约 hash 标签需要被注入到 testbench 规则中。
from scripts.python.generation.vectors import VECTOR_HASH_TAG

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
    "requirements": ((("plan", "{name}_requirements.json"), "requirements", "json"),),  # 需求确认阶段只输出规范化需求 JSON
    "codegen_plan": ((("plan", "{name}_codegen_plan.json"), "codegen_plan", "json"),),  # 代码规划阶段输出实现计划 JSON
    "tests": ((("plan", "{name}_test_vectors.json"), "test_vectors", "json"),),  # 测试向量阶段输出语义向量 JSON
}

# prompt 上下文预算统一映射到固定字符上限。
CONTEXT_CHAR_LIMITS = {  # staged 工件上下文的字符预算上限
    "compact": 12000,  # 紧凑预算限制上游上下文体积
    "normal": 24000,  # 常规预算允许更完整的阶段上下文
    "repair": 24000,  # 修复预算与常规预算保持同上限
}

# memory 只保留最近且相关的固定条数，避免 staged prompt 膨胀。
MEMORY_ENTRY_LIMIT = 20  # staged prompt 最多保留的 memory 条目数

# 某些 JSON 合同类工件应优先保留完整正文。
FULL_CONTEXT_TOKENS = (  # 需要完整上下文优先策略的路径关键词
    "vector",  # 向量文件优先保留全文
    "semantic_transcript",  # 语义转录记录优先保留全文
    "contract",  # 通用合同类文件优先保留全文
)

# 常见文件后缀到 manifest language 字段的稳定映射。
PATH_LANGUAGE_BY_SUFFIX = {  # 输出文件后缀到语言名的映射
    "cpp": "cpp",  # C++ 源文件后缀映射
    "cc": "cpp",  # GCC 风格 C++ 源文件后缀映射
    "cxx": "cpp",  # 扩展 C++ 源文件后缀映射
    "h": "cpp",  # 头文件默认按 C++ 语言处理
    "hpp": "cpp",  # C++ 头文件扩展后缀映射
    "cfg": "ini",  # cfg 文件按 ini 语义标记
    "json": "json",  # JSON 文件语言标签
    "py": "python",  # Python 参考模型文件标签
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

# 渲染 staged prompt 的输出合同说明。
def _stage_output_contract_text(manifest: dict[str, Any]) -> str:
    """
    渲染 staged prompt 的输出合同说明。

    :param manifest: 当前 stage 对应的 manifest。
    :return: staged prompt 输出合同说明文本。
    """

    # staged 输出合同先说明 fence 规则，再嵌入 manifest JSON 正文。
    list_contract_lines = [  # staged 输出合同正文行
        "Return only fenced code blocks: first the manifest JSON, "
        "then one file block per manifest file.",
        "Every file block must use `path=<relative/path>`, and every path "
        "must match the manifest exactly.",
        "",  # 规则说明与 manifest 正文之间的空行
        _json_code_block(manifest),  # 输出合同的 manifest JSON 正文
    ]

    # 返回单段 markdown 文本。
    return "\n".join(list_contract_lines)

# 渲染最终 HLS prompt 的基础合同。
def _base_prompt(
    *,
    spec: dict[str, Any],
    title: str,
    target_line: str,
    rules: list[str],
    manifest: dict[str, Any],
) -> str:
    """
    渲染最终 HLS prompt 的基础合同。

    :param spec: 规范化后的 HLS spec。
    :param title: prompt 标题行文本。
    :param target_line: 目标生成边界说明文本。
    :param rules: 设计规则列表。
    :param manifest: 最终输出合同 manifest。
    :return: 基础 prompt 合同文本。
    """

    # 最终 HLS prompt 保持固定 markdown 结构，便于测试断言与人工审阅。
    list_prompt_lines = [
        f"# {title}",  # prompt 顶部标题行
        "",  # 标题与身份说明之间的空行
        "You are an expert AMD-Xilinx HLS design generator. "
        f"{target_line}",
        "Do not generate Verilog or SystemVerilog. Do not output analysis.",  # HLS-only 输出边界
        "",  # 身份说明与规格章节之间的空行
        "## Generation spec",  # 规格章节标题
        "",  # 规格标题与 JSON 正文之间的空行
        _json_code_block(spec),  # 规格 JSON 正文
        "",  # 规格与规则章节之间的空行
        "## Design rules",  # 规则章节标题
        "",  # 规则标题与列表正文之间的空行
        _bullet_list(rules),  # 设计规则列表正文
        "",  # 规则与输出合同之间的空行
        "## Output contract",  # 输出合同章节标题
        "",  # 合同标题与正文之间的空行
        "Return only fenced code blocks: first the manifest JSON, "
        "then one file block per manifest file.",
        "The manifest must preserve the `files` array exactly and may fill "
        "the `checks` arrays with concise strings.",
        "",  # 合同规则与 manifest 正文之间的空行
        _json_code_block(manifest),  # 输出合同里要求模型原样回填的 manifest 树
        "",  # manifest 与路径规则之间的空行
        "Then return one fenced code block for every manifest file, and no "
        "extra file blocks. Put the exact relative file path in the fence "
        "info as `path=<relative/path>`.",
        "",  # 文件 fence 说明与路径规则之间的空行
        "Path rules:",  # 路径规则小节标题
        "",  # 路径规则标题与列表之间的空行
        "- Every manifest path must have exactly one matching code fence.",  # manifest 路径与 code fence 一一对应
        "- Every code fence path must appear in the manifest.",  # code fence 路径必须先出现在 manifest
        "- Paths must be relative, unique, case-exact, slash-exact, and must not contain `..`.",  # 路径格式与安全边界
        "",  # 路径规则与示例之间的空行
        "Example fence header:",  # fence 头示例标题
        "",  # 示例标题与示例正文之间的空行
        "```cpp path=src/example_kernel.cpp",  # fence 头示例正文
        "```",  # 示例 fence 结束标记
    ]  # 最终 HLS prompt 的完整正文行序列

    # 返回基础 HLS prompt 文本。
    return "\n".join(list_prompt_lines)

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

# 根据 stage 生成 staged workflow 期望的 manifest。
def _stage_manifest_for(spec: dict[str, Any], stage: str) -> dict[str, Any]:
    """
    根据 stage 生成 staged workflow 期望的 manifest。

    :param spec: 规范化后的 HLS spec。
    :param stage: 已校验通过的 stage 名称。
    :return: 当前 stage 应返回的 manifest 字典。
    异常:
        ValueError: 当模板映射中找不到 stage 对应的文件合同条目时抛出。
    """

    # hls 阶段直接沿用 spec.outputs，保持和最终生成合同完全一致。
    if stage == "hls":

        # 从 spec.outputs 派生最终 HLS 输出文件条目。
        list_files = _output_file_entries(spec["outputs"])  # HLS 阶段输出文件条目列表

    # 其它 stage 使用固定模板，只替换 spec.name 形成稳定输出路径。
    else:

        # 读取当前 stage 对应的文件模板元组。
        tuple_stage_templates = STAGED_FILE_TEMPLATES.get(stage)  # 当前 stage 的文件模板集合

        # 找不到模板时直接阻断非法 stage。
        if tuple_stage_templates is None:

            # 报告未知 stage，保持 HLS-only 边界清晰。
            raise ValueError(
                "> ERR: [Python] This skill is HLS-only; unknown stage "
                + repr(stage)
                + "."
            )

        # 模板阶段把输出路径、kind 与 language 固定展开为 manifest 条目。
        list_files = [  # requirements/codegen_plan/tests 阶段展开后的 manifest 文件清单
            {
                "path": PurePosixPath(*tuple_path_parts).as_posix().format(  # 按模板展开当前 stage 的相对输出路径
                    name=spec["name"]  # 用 spec.name 替换模板中的 {name}
                ),
                "kind": str_kind,  # 当前模板条目的文件角色
                "language": str_language,  # 当前模板条目的语言标签
            }
            for tuple_path_parts, str_kind, str_language in tuple_stage_templates  # 逐个展开 stage 模板条目
        ]

    # 返回带 stage 字段的 manifest 载荷。
    return _manifest_payload(spec, stage=stage, files=list_files)

# 根据输出列表生成最终 HLS prompt 使用的 manifest。
def _manifest_for(spec: dict[str, Any]) -> dict[str, Any]:
    """
    根据输出列表生成最终 HLS prompt 使用的 manifest。

    :param spec: 规范化后的 HLS spec。
    :return: 最终 HLS prompt 应使用的 manifest 字典。
    """

    # 最终 prompt 直接复用 spec.outputs，避免 staged/hls 合同分叉。
    list_files = _output_file_entries(spec["outputs"])  # 最终 HLS 输出文件条目列表

    # 返回不带 stage 字段的最终 manifest。
    return _manifest_payload(spec, stage=None, files=list_files)

# 拼装 manifest 的公共字段。
def _manifest_payload(
    spec: dict[str, Any],
    *,
    stage: str | None,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    拼装 manifest 的公共字段。

    :param spec: 规范化后的 HLS spec。
    :param stage: 当前 stage 名称；为空表示最终 HLS prompt。
    :param files: manifest 中的文件条目列表。
    :return: 带公共字段的 manifest 字典。
    """

    # top 函数名遵守 interfaces.top_function 优先、spec.name 回退的既有合同。
    str_top_function = spec["interfaces"].get("top_function", spec["name"])  # 当前 manifest 应声明的 top 函数名

    # 先构造所有 stage 共用的 manifest 公共主体。
    dict_manifest = {  # 把模型后续必须返回的目标域、设计名、顶层函数、文件清单和检查槽位一次性打包成统一输出合同
        "target": "hls",  # 目标生成域恒定为 HLS
        "name": spec["name"],  # 当前设计名称
        "top": str_top_function,  # 约束 manifest 指向当前设计的顶层入口函数
        "files": files,  # 输出文件条目列表
        "checks": _checks_template(),  # 预留检查结果槽位
    }

    # staged manifest 需要显式标记 stage，最终 HLS prompt 则省略该字段。
    if stage is not None:

        # 写入当前 stage 名称，供 staged workflow 检查。
        dict_manifest["stage"] = stage  # staged workflow 需要的当前阶段标记

    # 返回完整 manifest 载荷。
    return dict_manifest

# 把 spec.outputs 规范化为 manifest 文件条目。
def _output_file_entries(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    把 spec.outputs 规范化为 manifest 文件条目。

    :param outputs: spec 中声明的输出文件条目列表。
    :return: manifest 使用的规范化文件条目列表。
    """

    # 每个输出条目都会被补齐 kind 与 language。
    list_file_entries = [  # manifest 使用的文件条目列表
        {
            "path": dict_output["path"],  # 直接复用 spec 声明的相对路径
            "kind": dict_output.get("kind", "source"),  # 未显式声明时回退 source 类别
            "language": dict_output.get(  # 语言字段优先复用显式声明
                "language",  # 优先复用 spec 显式给出的语言标签
                _language_from_path(dict_output["path"]),  # 无显式 language 时从路径推断
            ),
        }
        for dict_output in outputs  # 逐个遍历 spec.outputs 条目
    ]

    # 返回规范化后的 manifest 文件条目。
    return list_file_entries

# 返回 manifest checks 字段的稳定模板。
def _checks_template() -> dict[str, list[str]]:
    """
    返回 manifest checks 字段的稳定模板。

    参数:
        无额外业务参数；当前函数只返回固定模板。
    返回:
        带固定 checks 键集合的 manifest 模板字典。
    """

    # checks 字段保持固定键顺序，便于 workflow 和模型按稳定槽位回填。
    return {
        "spec_coverage": [],
        "verification_plan": [],
        "execution_plan": [],
        "implementation_assessment": [],
        "reviewability_assessment": [],
        "assumptions": [],
        "known_limitations": [],
    }

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

# 在最终 prompt 末尾追加可选 JSON 章节。
def _append_optional_sections(
    prompt: str,
    *,
    hls_profile: dict[str, Any] | None,
    decision: dict[str, Any] | None,
) -> str:
    """
    在最终 prompt 末尾追加可选 JSON 章节。

    :param prompt: 已渲染好的基础 prompt 文本。
    :param hls_profile: 当前生效的 HLS profile。
    :param decision: 人工决策约束对象。
    :return: 追加可选 JSON 章节后的最终 prompt 文本。
    """

    # 可选章节通过列表增量构建，保持没有 profile/decision 时的旧输出结构。
    list_optional_sections: list[str] = []  # prompt 末尾的可选章节集合

    # HLS profile 存在时追加 profile 章节。
    if hls_profile:

        # 追加 HLS profile JSON 章节。
        list_optional_sections.append(
            _optional_json_section("HLS profile constraints", hls_profile)
        )

    # decision 存在时追加人工决策章节。
    if decision:

        # 追加人工决策 JSON 章节。
        list_optional_sections.append(
            _optional_json_section("Human decision constraints", decision)
        )

    # 没有任何可选章节时直接返回基础 prompt。
    if not list_optional_sections:

        # 保持没有附加章节时的旧输出格式。
        return prompt

    # 返回拼接好可选章节的最终 prompt。
    return prompt.rstrip() + "\n\n" + "\n\n".join(list_optional_sections) + "\n"

# 渲染末尾追加用的 JSON 章节。
def _optional_json_section(title: str, payload: dict[str, Any]) -> str:
    """
    渲染末尾追加用的 JSON 章节。

    :param title: JSON 章节标题。
    :param payload: 章节对应的 JSON 对象。
    :return: 单个 markdown JSON 章节文本。
    """

    # 返回 markdown 标题加 JSON fenced block 组合文本。
    return f"## {title}\n\n{_json_code_block(payload)}"

# 根据文件后缀推断 manifest language 字段。
def _language_from_path(path: str) -> str:
    """
    根据文件后缀推断 manifest language 字段。

    :param path: manifest 文件相对路径。
    :return: 对应的 language 字段值；未识别时回退 `text`。
    """

    # 不带点号的路径视为未知文本类型，保持旧行为回退 text。
    str_suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""  # 从路径推断出的文件后缀

    # 返回稳定的后缀到语言映射结果。
    return PATH_LANGUAGE_BY_SUFFIX.get(str_suffix, "text")

# 构造 staged prompt 的上游产物上下文字典。
def _artifact_context(
    manifest: dict[str, Any] | None,
    context_dir: Path | None,
    *,
    budget: str,
) -> dict[str, Any]:
    """
    构造 staged prompt 的上游产物上下文字典。

    :param manifest: 上游阶段生成的 manifest。
    :param context_dir: 上游阶段工件根目录。
    :param budget: 当前 prompt 预算档位。
    :return: staged prompt 可消费的上游产物上下文字典。
    """

    # 没有 manifest 时直接回退空上下文。
    if not manifest:

        # 保持 staged prompt 在无上游时的最小上下文。
        return {}

    # 只保留模型真正会消费的稳定字段，避免把无关状态扩散进 prompt。
    dict_context = {  # staged prompt 消费的上游阶段摘要字典
        "stage": manifest.get("stage"),  # 上游阶段标记
        "target": manifest.get("target"),  # 目标生成域
        "top": manifest.get("top"),  # 让模型识别上游产物绑定到哪个 top function
        "files": [  # 输出文件摘要入口列表
            {
                "path": dict_file_entry.get("path"),  # 文件相对路径
                "kind": dict_file_entry.get("kind"),  # 文件角色类别
                "language": dict_file_entry.get("language"),  # 让模型按正确语法读取该文件
            }
            for dict_file_entry in manifest.get("files", [])  # 维持 manifest 文件顺序与摘要顺序一致
            if isinstance(dict_file_entry, dict)  # 只保留结构正确的文件字典
        ],
        "checks": manifest.get("checks", {}),  # 上游阶段已经记录的检查结果
    }

    # 只有显式提供目录时才读取工件摘要，避免默认触发磁盘访问。
    if context_dir:

        # 注入上游工件的摘要内容。
        dict_context["artifacts"] = _artifact_summaries(manifest, context_dir, budget=budget)  # 上游工件摘要列表

    # 返回 staged prompt 的上游产物上下文字典。
    return dict_context

# 提取 staged prompt 需要的上游产物摘要。
def _artifact_summaries(
    manifest: dict[str, Any],
    context_dir: Path,
    *,
    budget: str,
) -> list[dict[str, Any]]:
    """
    提取 staged prompt 需要的上游产物摘要。

    :param manifest: 上游阶段生成的 manifest。
    :param context_dir: 上游阶段工件根目录。
    :param budget: 当前 prompt 预算档位。
    :return: staged prompt 需要的工件摘要列表。
    """

    # 输出列表按 manifest 文件顺序累积摘要对象。
    list_artifact_summaries: list[dict[str, Any]] = []  # staged prompt 的工件摘要列表

    # 所有相对路径都必须锚定到调用方提供的 context_dir。
    path_root = context_dir.resolve()  # 上下文目录的规范绝对路径

    # 按 manifest 文件顺序提取工件摘要。
    for dict_file_entry in manifest.get("files", []):

        # 只处理带 path 的字典型 manifest 文件条目。
        if not isinstance(dict_file_entry, dict) or not dict_file_entry.get("path"):

            # 非法文件条目直接跳过，保持 staged prompt 尽量可继续。
            continue

        # 读取 manifest 中声明的相对路径文本。
        str_relative_path = str(dict_file_entry["path"])  # manifest 声明的相对路径

        # 先把 manifest 相对路径安全锚定到 context_dir 下。
        path_artifact = _safe_context_path(path_root, str_relative_path)  # 安全解析后的工件路径

        # 每个工件至少记录路径与存在性，便于模型判断是否可引用上游内容。
        dict_summary: dict[str, Any] = {"path": str_relative_path, "exists": path_artifact.exists()}  # 当前工件摘要骨架

        # 仅在工件真实存在且为普通文件时才读取正文。
        if path_artifact.exists() and path_artifact.is_file():

            # 文本读取统一忽略非法字节，避免单个文件编码问题打断 prompt 渲染。
            str_text = path_artifact.read_text(encoding="utf-8", errors="ignore")  # 工件原始文本内容

            # 根据预算与文件类型决定保留全文还是分块。
            dict_summary.update(
                _context_payload_for(
                    str_relative_path,
                    str_text,
                    budget=budget,
                )
            )

        # 追加当前工件摘要到输出列表。
        list_artifact_summaries.append(dict_summary)

    # 返回 staged prompt 的工件摘要列表。
    return list_artifact_summaries

# 根据预算和文件类型决定 staged prompt 中的工件内容载荷。
def _context_payload_for(
    rel_path: str,
    text: str,
    *,
    budget: str,
) -> dict[str, Any]:
    """
    根据预算和文件类型决定 staged prompt 中的工件内容载荷。

    :param rel_path: 工件相对路径。
    :param text: 工件原始文本内容。
    :param budget: 当前 prompt 预算档位。
    :return: staged prompt 应注入的工件内容载荷。
    """

    # budget 映射到固定字符上限，未识别档位仍回退 normal 大小。
    int_limit = CONTEXT_CHAR_LIMITS.get(budget, CONTEXT_CHAR_LIMITS["normal"])  # 当前预算对应的字符上限

    # 合同类 JSON 或本就很短的文件可以直接保留全文。
    if _needs_full_context(rel_path) or len(text) <= int_limit:

        # 内容长度不超预算时直接保留全文。
        if len(text) <= int_limit:

            # 返回完整正文载荷。
            return {"content": text}

        # 对优先保留全文但过长的内容，改用切块承载，避免单字段过大。
        return {
            "content_chunks": _chunk_text(text, int_limit),
            "content_truncated": False,
        }

    # 其它较长内容统一按预算切块。
    return {
        "content_chunks": _chunk_text(text, int_limit),
        "content_truncated": False,
    }

# 判断指定工件是否应优先保留完整上下文。
def _needs_full_context(rel_path: str) -> bool:
    """
    判断指定工件是否应优先保留完整上下文。

    :param rel_path: 工件相对路径。
    :return: 是否命中完整上下文优先策略。
    """

    # 只对 JSON 契约/向量类文件启用完整上下文优先策略。
    str_lowered_path = rel_path.lower()  # 用于关键词判断的归一化路径

    # 返回是否命中完整上下文优先策略。
    return str_lowered_path.endswith(".json") and any(
        str_token in str_lowered_path for str_token in FULL_CONTEXT_TOKENS
    )

# 把长文本切成带索引的分块。
def _chunk_text(text: str, chunk_size: int) -> list[dict[str, Any]]:
    """
    把长文本切成带索引的分块。

    :param text: 需要切块的原始文本。
    :param chunk_size: 每个分块的最大字符数。
    :return: 带 1-based 索引的文本分块列表。
    """

    # 按顺序累计文本分块。
    list_chunks: list[dict[str, Any]] = []  # 顺序分块后的文本片段列表

    # 逐段切出固定大小的文本窗口。
    for int_index, int_start in enumerate(
        range(0, len(text), chunk_size),
        start=1,
    ):

        # 追加当前分块的索引和正文。
        list_chunks.append(
            {
                "index": int_index,
                "text": text[int_start : int_start + chunk_size],
            }
        )

    # 返回带索引的分块列表。
    return list_chunks

# 筛选与当前 stage 相关的历史 memory 约束。
def _memory_constraints(
    memory: dict[str, Any] | None,
    stage: str,
    *,
    budget: str,
) -> list[dict[str, Any]]:
    """
    筛选与当前 stage 相关的历史 memory 约束。

    :param memory: 历史 memory 约束对象。
    :param stage: 当前正在渲染的 stage。
    :param budget: 当前 prompt 预算档位；仅保留兼容入参，不改变筛选语义。
    :return: 当前 stage 相关的 memory 条目列表。
    """

    # budget 目前不参与 memory 筛选逻辑，这里只保留接口兼容。
    del budget

    # 没有 memory 时直接返回空列表。
    if not memory:

        # staged prompt 在无历史记忆时保持最小输入。
        return []

    # 只保留当前 stage 或全局相关的条目，减少模型看到的历史噪音。
    list_entries: list[dict[str, Any]] = []  # 当前 stage 可见的 memory 条目

    # 遍历 memory.entries 过滤相关条目。
    for dict_entry in memory.get("entries", []):

        # 仅接受字典型条目，其余噪音值直接跳过。
        if not isinstance(dict_entry, dict):

            # 跳过非法 memory 条目。
            continue

        # stage 字段统一小写比较，兼容旧数据中的大小写差异。
        str_entry_stage = str(dict_entry.get("stage", "")).lower()  # 当前 memory 条目的目标 stage

        # 与当前 stage 无关的 memory 条目不进入 prompt。
        if str_entry_stage and str_entry_stage not in {
            stage,
            "*",
            "unknown",
            "validate",
            "execute",
            "implement",
            "cosim",
        }:

            # 跳过与当前 stage 无关的 memory 条目。
            continue

        # 仅保留当前 prompt 真正会消费的稳定字段。
        list_entries.append(
            {
                "stage": dict_entry.get("stage"),
                "error_signature": dict_entry.get("error_signature"),
                "constraint": dict_entry.get("constraint"),
            }
        )

    # 返回限制条数后的相关 memory 条目。
    return list_entries[:MEMORY_ENTRY_LIMIT]

# 把 manifest 中的相对路径安全地约束到 context_dir 下。
def _safe_context_path(root: Path, relative_path: str) -> Path:
    """
    把 manifest 中的相对路径安全地约束到 context_dir 下。

    :param root: 上游 context_dir 的绝对根路径。
    :param relative_path: manifest 中声明的相对路径。
    :return: 约束到 root 下的安全路径；非法路径时返回无效占位路径。
    """

    # Windows 反斜杠输入会破坏 manifest 约定的 posix 相对路径语义，因此直接判定为无效。
    if "\\" in relative_path:

        # 反斜杠输入统一映射到专用占位路径，便于上层定位来源。
        return root / "__invalid_backslash_path__"

    # 同时用 Posix/Windows 视角检查绝对路径和驱动器前缀，避免越界。
    path_posix = PurePosixPath(relative_path)  # 用于检查相对路径片段与目录穿越风险

    # 再以 Windows 规则解析同一路径，补充盘符与绝对路径检测。
    path_windows = PureWindowsPath(relative_path)  # 用于补充盘符与 Windows 绝对路径检查

    # 含绝对路径、盘符或目录穿越片段时返回无效占位路径。
    if (
        path_posix.is_absolute()
        or path_windows.is_absolute()
        or path_windows.drive
        or any(str_part in ("", ".", "..") for str_part in path_posix.parts)
    ):

        # 返回不安全路径对应的无效占位路径。
        return root / "__invalid_unsafe_path__"

    # 合法相对路径必须在 resolve 之后仍然落在 context_dir 内。
    path_candidate = (root / Path(*path_posix.parts)).resolve()  # 解析后的候选绝对路径

    # 再次确认 resolve 后的路径没有逃出 root。
    try:

        # 仅用于触发 root 边界检查副作用。
        path_candidate.relative_to(root)

    # 超出 root 时回退为无效占位路径。
    except ValueError:

        # 返回越界路径对应的无效占位路径。
        return root / "__invalid_outside_path__"

    # 返回安全锚定后的上下文工件路径。
    return path_candidate

# 把对象格式化为 JSON fenced block。
def _json_code_block(payload: Any) -> str:
    """
    把对象格式化为 JSON fenced block。

    :param payload: 需要序列化为 JSON 的对象。
    :return: JSON fenced block 文本。
    """

    # 返回带固定缩进与 UTF-8 中文直出的 JSON 代码块文本。
    return "```json\n" + json.dumps(
        payload,
        indent=2,
        ensure_ascii=False,
    ) + "\n```"

# 把字符串列表渲染为 Markdown 项目符号列表。
def _bullet_list(items: list[str]) -> str:
    """
    把字符串列表渲染为 Markdown 项目符号列表。

    :param items: 需要渲染的字符串列表。
    :return: Markdown 项目符号列表文本。
    """

    # 返回按顺序拼接的 markdown bullet 文本。
    return "\n".join(f"- {str_item}" for str_item in items)
