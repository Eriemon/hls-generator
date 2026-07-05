"""提供 HLS-only 工作流的稳定集成适配入口。"""

# 延迟类型标注求值，避免 facade 导入时触发额外运行期依赖。
from __future__ import annotations

# 标准库依赖负责 JSON 编解码、对象复制和路径表达。
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

# 运行期配置提供技能根目录、受保护路径和依赖声明。
from runtime.hls_generator.config import (
    protected_files,
    protected_roots,
    skill_config_path,
    skill_dependencies_config,
    skill_root,
)

# prompt、workflow 和验证模块承接实际 HLS 生成语义。
from runtime.hls_generator.prompt import render_prompt

# HLS 注释治理模块只生成计划和门禁报告，不直接改写源码。
from runtime.hls_generator.readability_gate.rewrite_plan import build_hls_comment_rewrite_plan
from runtime.hls_generator.readability_gate.runner import (
    run_hls_readability_gate as _run_hls_readability_gate,
)

# requirements 模块负责把 facade 输入转换为 workflow 可消费的需求载荷。
from runtime.hls_generator.requirements import (
    apply_requirement_defaults,
    build_codegen_plan,
    build_requirements_payload,
    validate_requirement_confirmation,
)

# 依赖、spec、语言和验证模块构成 facade 调用 runtime 的核心边界。
from runtime.hls_generator.skill_dependencies import require_skill_dependencies
from runtime.hls_generator.spec import normalize_spec, read_spec, write_spec
from runtime.hls_generator.user_config import resolve_comment_language
from runtime.hls_generator.validation import ValidationRunOptions, validate_generated
from runtime.hls_generator.workflow import WorkflowRunRequest, run_workflow

# workspace helper 统一约束输入读取和输出写入的仓库边界。
from runtime.hls_generator.workspace import (
    require_configured_output_path,
    require_workspace_path,
    use_workspace_root,
)

# 默认配置路径通过技能配置解析，避免集成层绑定具体文件布局。
DEFAULT_CONFIG_PATH = skill_config_path("default_workflow_config")  # 默认 workflow 配置路径

# 技能根目录用于约束相对输入路径只能落在当前技能内。
SKILL_ROOT = skill_root()  # 当前技能源码根目录

# _reject_unknown_options 依靠这份白名单把 run_hls_workflow 兼容层限制在历史允许的关键字范围内。
WORKFLOW_OPTION_NAMES = {
    "out_dir",  # 允许旧调用方指定新建 workflow 的产物目录。
    "resume_dir",  # 允许旧调用方把流程恢复到既有 run 目录继续执行。
    "workflow_config",  # 允许兼容层覆写默认 workflow 配置文件路径或内容。
    "evidence",  # 允许把外部证据 JSON 直接并入 workflow 判定输入。
    "decision",  # 允许把人工决策 JSON 透传给 workflow 的中断恢复逻辑。
    "provider_name",  # 允许兼容层显式选择下游模型提供者标识。
    "provider_command",  # 允许兼容层覆写调用外部模型时使用的命令行。
    "target",  # 兼容历史 facade 契约时仍要求目标值固定为 hls。
    "design_requirements",  # 允许旧入口补充结构化设计约束并传给 runtime。
    "pipeline_required",  # 允许调用方声明本轮生成必须满足 pipeline 要求。
    "streamability",  # 允许调用方补充数据流式处理能力的设计声明。
    "interface_family",  # 允许旧入口约束生成方案必须落在指定接口族内。
    "interface_profile",  # 允许透传更细的接口 profile JSON 给 runtime 规划器。
    "confirmation",  # 允许把用户确认结果并入 workflow 的需求收敛阶段。
    "readiness",  # 允许调用方声明本轮 workflow 需要达到的验证就绪级别。
    "max_attempts",  # 允许兼容层限制 workflow 自动重试的最大轮数。
    "stop_on_human",  # 允许在遇到人工确认节点时选择立即停下等待输入。
    "run_external",  # 允许调用方决定是否真正执行外部工具链验证。
    "comment_language",  # 允许兼容层把注释语言要求一路透传到生成阶段。
    "hls_profile",  # 允许旧入口选择不同的 HLS 生成约束配置档。
    "model_timeout_s",  # 允许兼容层控制单次模型调用的超时秒数。
}

# _reject_unknown_options 依靠这份白名单把 render_hls_prompt 兼容层限制在 prompt 渲染阶段允许的关键字范围内。
PROMPT_OPTION_NAMES = {
    "target",  # 兼容旧 prompt facade 时仍要求目标值固定为 hls。
    "design_requirements",  # 允许 prompt 渲染阶段读取结构化设计需求摘要。
    "pipeline_required",  # 允许 prompt 明确强调本轮生成必须具备 pipeline 语义。
    "streamability",  # 允许 prompt 注入数据流处理能力相关的约束描述。
    "interface_family",  # 允许 prompt 锁定候选接口方案的家族边界。
    "interface_profile",  # 允许 prompt 展开更细粒度的接口 profile 约束。
    "confirmation",  # 允许 prompt 带入用户已经确认过的需求结论。
    "stage",  # 允许旧入口声明当前渲染的是哪一个 workflow 阶段 prompt。
    "context_manifest",  # 允许 prompt 读取上下文清单来组织引用资料。
    "context_dir",  # 允许 prompt 从指定上下文目录加载补充素材。
    "evidence",  # 允许把外部证据摘要写进 prompt 供模型参考。
    "memory",  # 允许 prompt 合并长期记忆中保留的约束或偏好。
    "comment_language",  # 允许 prompt 明确约束生成注释必须使用的语言。
    "vector_contract",  # 允许 prompt 描述测试向量契约和验证预期。
    "budget",  # 允许旧入口向 prompt 暴露本轮预算档位。
    "hls_profile",  # 允许 prompt 读取当前 HLS 生成 profile 的额外约束。
    "decision",  # 允许 prompt 复用人工决策 JSON 中已确认的取舍结果。
}

# _reject_unknown_options 依靠这份白名单把 validate_hls_artifacts 兼容层限制在 artifact 验证阶段允许的关键字范围内。
VALIDATION_OPTION_NAMES = {
    "target",  # 兼容旧验证 facade 时仍要求目标值固定为 hls。
    "design_requirements",  # 允许验证前先合并最新的结构化设计需求约束。
    "pipeline_required",  # 允许验证逻辑按调用方声明检查 pipeline 相关承诺。
    "streamability",  # 允许验证逻辑读取流式处理能力的目标声明。
    "interface_family",  # 允许验证阶段核对产物是否仍落在指定接口族内。
    "interface_profile",  # 允许验证阶段使用细粒度接口 profile 做一致性比对。
    "confirmation",  # 允许把用户确认结论一并送入验证前的需求归并。
    "run_external",  # 允许调用方控制是否执行外部工具链和板级验证步骤。
    "readiness",  # 允许验证阶段依据目标就绪级别裁剪检查范围。
    "comment_language",  # 允许验证阶段检查生成注释是否满足语言约束。
    "hls_profile",  # 允许验证逻辑读取当前 HLS profile 的附加门禁要求。
    "baseline_path",  # 允许验证阶段装载对比基线目录做回归比对。
    "report_json",  # 允许旧入口指定结构化验证报告的输出路径。
}

# HLS 可读性门禁入口的关键字集合保持与 runtime runner 对齐。
READABILITY_OPTION_NAMES = {  # run_hls_readability_gate 用它约束 HLS 可读性门禁阶段可接受的关键字
    "target",  # readability gate 检查的目标名，固定要求 hls
    "profile",  # readability gate 选择的 HLS 可读性 profile
    "style",  # HLS 注释风格
    "baseline_path",  # readability gate 对比用的 HLS baseline 目录
    "top_function",  # 顶层函数名
    "fail_on_warning",  # warning 是否视为失败
    "report_json",  # 门禁报告输出路径
}

# HLS 注释计划入口只暴露路径、目标和 profile 相关选项。
COMMENT_PLAN_OPTION_NAMES = {  # build_hls_comment_plan 用它约束注释重写计划阶段可接受的关键字
    "target",  # comment-plan facade 处理的目标名，固定要求 hls
    "baseline_path",  # 生成 rewrite plan 时对比的注释基线路径
    "profile",  # HLS 注释治理 profile
    "out_path",  # 注释计划输出路径
}

# 对外导出名单固定集成层可用 API，避免调用方依赖内部 helper。
__all__ = [  # 集成层公开符号列表
    "run_hls_workflow",  # workflow 执行入口
    "render_hls_prompt",  # prompt 渲染入口
    "validate_hls_artifacts",  # HLS artifact 验证入口
    "run_hls_readability_gate",  # HLS 可读性门禁入口
    "build_hls_comment_plan",  # HLS 注释计划入口
    "load_default_workflow_config",  # 默认 workflow 配置读取入口
    "load_workflow_result",  # workflow 结果读取入口
]

# 默认 workflow 配置是集成层所有 workflow 包装器的共同输入。
def load_default_workflow_config() -> dict[str, Any]:
    """
    读取技能内置的默认 workflow 配置。

    参数:
        无外部业务参数；配置路径由技能配置表解析。

    返回:
        默认 workflow 配置字典，供 facade 合并用户覆盖项。
    """

    # 从技能配置路径读取 JSON，确保默认值来源只有一处。
    dict_default_config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))  # 默认 workflow 配置

    # 返回给 workflow 入口继续合并用户覆盖项。
    return dict_default_config

# workflow 结果读取入口只接受技能内的历史 run 目录。
def load_workflow_result(run_dir: str | Path) -> dict[str, Any]:
    """
    读取 workflow_result.json 并返回结构化字典。

    参数:
        run_dir: workflow 运行目录，必须位于当前技能目录内。

    返回:
        workflow_result.json 解析后的结果字典。
    """

    # 将调用方传入的 run 目录归一到技能内部绝对路径。
    path_run_dir = _resolve_skill_input_path(  # 已解析且位于技能根内的 workflow 运行目录
        run_dir,  # 调用方请求读取结果的 run 目录输入
        purpose="workflow result directory",  # 供边界校验报错使用的用途标签
        must_exist=True,  # 读取历史结果时要求该 run 目录已经存在
    )

    # workflow_result.json 是 workflow 对外稳定结果文件。
    path_result_file = path_run_dir / "workflow_result.json"  # workflow 结果文件路径

    # 返回已解析的 workflow 结果，避免调用方重复处理编码。
    return json.loads(path_result_file.read_text(encoding="utf-8"))

# 公开 workflow 入口保留旧关键字调用方式，内部再转换为受控选项字典。
def run_hls_workflow(
    spec: str | Path | dict[str, Any] | None = None,
    **dict_options: Any,
) -> dict[str, Any]:
    """
    执行或恢复 HLS-only 生成 workflow。

    参数:
        spec: 新 workflow 的输入 spec；恢复已有 run 时可以省略。
        dict_options: 兼容旧 facade 的关键字集合，包括 out_dir、resume_dir、readiness 等。

    返回:
        workflow 状态、run 目录、结果文件路径和完整 workflow_result 的摘要字典。

    异常:
        ValueError: 目标不是 HLS、缺少新 run 必要输入或传入未知关键字时抛出。
    """

    # 先拒绝未声明关键字，防止 **kwargs 兼容层吞掉拼写错误。
    _reject_unknown_options(dict_options, WORKFLOW_OPTION_NAMES, "run_hls_workflow")

    # workflow 入口需要确认核心依赖、远程辅助和 Vitis 工具契约已声明。
    require_skill_dependencies(skill_dependencies_config(), scopes={"core", "remote", "vitis"})

    # facade 只服务 HLS，不允许调用方把 RTL 目标混入本技能。
    _reject_non_hls_target(dict_options.get("target"))

    # 收集默认配置和调用方覆盖项，供新 run 与恢复 run 共用。
    dict_runtime_options = _collect_workflow_runtime_options(dict_options)  # workflow 运行参数集合

    # resume_dir 存在时进入恢复流程，不再要求 spec 和 out_dir。
    if dict_options.get("resume_dir") is not None:

        # 恢复流程复用原 run 目录中的 workspace 根。
        return _resume_workflow_from_options(dict_options, dict_runtime_options)

    # 新 workflow 必须同时提供 spec 和 out_dir。
    if spec is None or dict_options.get("out_dir") is None:

        # 明确提示调用方缺失新 run 的必要输入。
        raise ValueError("> ERR: [Python] New HLS workflow runs require both `spec` and `out_dir`.")

    # 新建流程负责物化 spec、requirements、codegen plan 和可选证据。
    return _start_workflow_from_options(spec, dict_options, dict_runtime_options)

# prompt 入口保留 spec/out_path 两个稳定位置参数，其余旧关键字通过受控字典处理。
def render_hls_prompt(
    spec: str | Path | dict[str, Any],
    out_path: str | Path,
    **dict_options: Any,
) -> dict[str, Any]:
    """
    渲染 HLS-only prompt 并写入指定输出文件。

    参数:
        spec: prompt 使用的 HLS spec，路径输入必须位于技能目录内；shape/dtype/unit 不适用。
        out_path: prompt 文本输出路径，必须落在允许的 workspace 输出范围内。
        dict_options: 兼容旧 facade 的 prompt 关键字集合，如 stage、context_manifest、budget。

    返回:
        包含 prompt 输出路径和 prompt 文本的字典。
    """

    # 先拒绝 prompt 层未知关键字，避免输出内容被静默忽略。
    _reject_unknown_options(dict_options, PROMPT_OPTION_NAMES, "render_hls_prompt")

    # prompt 渲染只依赖核心技能能力，不要求外部 Vitis 可用。
    require_skill_dependencies(skill_dependencies_config(), scopes={"core"})

    # prompt facade 同样保持 HLS-only 边界。
    _reject_non_hls_target(dict_options.get("target"))

    # spec 会先应用需求、接口和确认信息，再进入 prompt renderer。
    dict_resolved_spec = _prepare_facade_spec(  # 已叠加 facade 覆盖项并通过 normalize 的 HLS spec
        spec,  # prompt 阶段准备消费的原始 HLS spec
        design_requirements=_load_optional_json(dict_options.get("design_requirements")),  # 并入 prompt spec 的设计需求覆盖
        pipeline_required=dict_options.get("pipeline_required"),  # prompt 阶段显式要求的流水线约束
        streamability=dict_options.get("streamability"),  # prompt 阶段显式要求的流式能力声明
        interface_family=dict_options.get("interface_family"),  # prompt 阶段显式约束的接口族
        interface_profile=_load_optional_json(dict_options.get("interface_profile")),  # prompt 阶段附带的接口 profile JSON
        confirmation=_load_confirmation(dict_options.get("confirmation")),  # prompt 阶段可选携带的用户确认信息
    )

    # prompt 文本包含完整 codegen plan 和可选上下文。
    str_prompt_text = render_prompt(  # 已拼入上下文与 codegen plan 的 HLS prompt 文本
        dict_resolved_spec,  # 已标准化并补齐约束的 prompt spec
        target="hls",  # prompt 渲染固定使用的 HLS 目标
        stage=str(dict_options.get("stage") or "hls"),  # prompt 当前要服务的 workflow 阶段
        context_manifest=_load_optional_json(dict_options.get("context_manifest")),  # prompt 可见的上下文 manifest
        context_dir=_optional_path(dict_options.get("context_dir")),  # prompt 可选读取的上下文目录
        evidence=_load_optional_json(dict_options.get("evidence")),  # prompt 可选吸收的外部证据
        memory=_load_optional_json(dict_options.get("memory")),  # prompt 可选吸收的长期记忆摘要
        comment_language=_require_resolved_comment_language(dict_options.get("comment_language")),  # prompt 生成时采用的注释语言
        vector_contract=_load_optional_json(dict_options.get("vector_contract")),  # prompt 需要遵守的测试向量合同
        codegen_plan=build_codegen_plan(dict_resolved_spec),  # prompt 同步嵌入的 codegen plan
        budget=str(dict_options.get("budget") or "normal"),  # prompt 预算档位
        hls_profile=_load_optional_json(dict_options.get("hls_profile")),  # prompt 参考的 HLS profile 覆盖项
        decision=_load_optional_json(dict_options.get("decision")),  # prompt 可见的人工决策输入
    )

    # 输出路径必须经过 workspace 写入边界校验。
    path_output = _resolve_generated_path(out_path, purpose="prompt output path")  # prompt 输出文件路径

    # 写入前创建父目录，便于调用方直接给出新的 reports/runs 子路径。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # prompt 正文使用 UTF-8 保存，保留中文注释和 HLS 说明。
    path_output.write_text(str_prompt_text, encoding="utf-8")

    # 返回 prompt 文本便于测试或上层工具直接展示摘要。
    return {"path": str(path_output), "prompt": str_prompt_text}

# artifact 验证入口负责把 facade spec 规范化后交给 runtime validator。
def validate_hls_artifacts(
    spec: str | Path | dict[str, Any],
    artifacts_path: str | Path,
    **dict_options: Any,
) -> dict[str, Any]:
    """
    验证 HLS 生成 artifact 是否满足当前技能契约。

    参数:
        spec: 验证使用的 HLS spec，路径输入必须位于技能目录内。
        artifacts_path: 待验证 artifact 目录或文件路径，必须位于 workspace 允许范围内。
        dict_options: 兼容旧 facade 的验证关键字集合，如 readiness、run_external、report_json。

    返回:
        runtime validation report 转换得到的字典。
    """

    # 旧 Python reference 兼容关键字命中时，必须先显式要求调用方重跑 HLS-only 流程。
    _reject_legacy_reference_contract_option(dict_options)

    # 验证入口不接受未声明关键字，防止报告缺项。
    _reject_unknown_options(dict_options, VALIDATION_OPTION_NAMES, "validate_hls_artifacts")

    # artifact 验证只要求核心 runtime 能力可用。
    require_skill_dependencies(skill_dependencies_config(), scopes={"core"})

    # 验证入口仍然禁止非 HLS target。
    _reject_non_hls_target(dict_options.get("target"))

    # facade spec 先补齐需求默认值和显式确认字段。
    dict_resolved_spec = _prepare_facade_spec(  # 已补齐需求默认值并准备用于验证的 HLS spec
        spec,  # validator 读取的原始 HLS spec 输入
        design_requirements=_load_optional_json(dict_options.get("design_requirements")),  # validator 前置并入的设计需求覆盖
        pipeline_required=dict_options.get("pipeline_required"),  # validator 前置并入的流水线要求
        streamability=dict_options.get("streamability"),  # validator 前置并入的流式能力声明
        interface_family=dict_options.get("interface_family"),  # validator 前置并入的接口族约束
        interface_profile=_load_optional_json(dict_options.get("interface_profile")),  # validator 前置并入的接口 profile
        confirmation=_load_confirmation(dict_options.get("confirmation")),  # validator 前置并入的确认信息
    )

    # artifact 输入必须存在并位于 workspace 边界内。
    path_artifacts = _resolve_workspace_input_path(  # 已确认存在且位于 workspace 边界内的 artifact 路径
        artifacts_path,  # validator 将要检查的 artifact 目标路径
        purpose="artifacts path",  # workspace 输入校验使用的用途标签
        must_exist=True,  # validator 要求 artifact 目标已经存在
    )

    # baseline 是可选输入，缺省时 validator 使用默认比较策略。
    path_baseline = _optional_workspace_input_path(  # validator 可选读取的 artifact 基线路径
        dict_options.get("baseline_path"),  # validator 可选对比的 baseline 输入
        purpose="baseline path",  # artifact validator 读取 baseline 时使用的用途标签
    )

    # validation report 对象保留 issues、metrics 和状态字段。
    validation_report = validate_generated(  # validate_generated 返回的 HLS artifact 验证报告对象
        dict_resolved_spec,  # 已补齐需求默认值并规范化后的 HLS spec
        path_artifacts,  # validator 将检查的 artifact 路径
        target="hls",  # 验证器固定执行 HLS artifact 规则
        options=ValidationRunOptions(  # artifact 验证阶段的运行选项对象
            run_external=bool(dict_options.get("run_external", True)),  # 是否允许调用外部验证工具
            readiness=str(dict_options.get("readiness") or "static"),  # 本轮验证需要达到的就绪深度
            comment_language=_require_resolved_comment_language(dict_options.get("comment_language")),  # 已解析出的注释语言要求
            hls_profile=_load_optional_json(dict_options.get("hls_profile")),  # 调用方提供的 HLS 验证 profile 覆盖
            baseline_path=path_baseline,  # artifact 对比基线路径
        ),
    )  # HLS artifact 验证报告对象

    # validator 对象转换为稳定 JSON payload。
    dict_payload = validation_report.to_dict()  # 验证报告字典

    # report_json 存在时把完整报告写入文件，调用方 stdout 只需看路径。
    if dict_options.get("report_json") is not None:

        # 报告输出路径必须通过 workspace 写入边界校验。
        path_report = _resolve_generated_path(  # validator 最终写出的 JSON 报告路径
            dict_options["report_json"],  # 调用方指定的验证报告输出位置
            purpose="validation report path",  # 生成路径校验使用的用途标签
        )

        # 写入前创建报告父目录。
        path_report.parent.mkdir(parents=True, exist_ok=True)

        # 报告文件保留非 ASCII 内容，方便中文问题描述回读。
        path_report.write_text(_json_text(dict_payload), encoding="utf-8")

    # 返回完整 payload 供 Python 调用方直接判断 ok/errors。
    return dict_payload

# HLS 可读性门禁 facade 只做路径约束、目标约束和 JSON 输出。
def run_hls_readability_gate(
    artifacts_path: str | Path,
    **dict_options: Any,
) -> dict[str, Any]:
    """
    运行 HLS artifact 可读性门禁并返回 JSON payload。

    参数:
        artifacts_path: 待检查 HLS artifact 路径，必须位于 workspace 允许范围内。
        dict_options: 兼容旧 facade 的门禁关键字集合，如 profile、style、report_json。

    返回:
        HLS readability gate report 转换得到的字典。
    """

    # 门禁入口先校验关键字集合，避免 report_json 等选项拼错。
    _reject_unknown_options(dict_options, READABILITY_OPTION_NAMES, "run_hls_readability_gate")

    # HLS readability gate 不接受 RTL 或其他目标名。
    _reject_non_hls_target(dict_options.get("target"))

    # artifact 目标路径必须存在并处于 workspace 管控内。
    path_artifacts = _resolve_workspace_input_path(  # runner 将读取的 HLS 可读性检查目标路径
        artifacts_path,  # readability gate 需要扫描的 artifact 输入
        purpose="HLS readability target path",  # readability gate 校验 artifact 输入时使用的用途标签
        must_exist=True,  # 只有已有 artifact 才能进入可读性门禁检查
    )

    # baseline 输入可选，存在时同样必须通过 workspace 输入检查。
    path_baseline = _optional_workspace_input_path(  # readability gate 可选对比的 baseline 路径
        dict_options.get("baseline_path"),  # readability gate 可选接收的 baseline 输入
        purpose="baseline path",  # HLS 可读性门禁读取 baseline 时使用的用途标签
    )

    # runtime runner 负责实际 HLS AST 与注释规则检查。
    readability_report = _run_hls_readability_gate(  # runtime runner 返回的 HLS 可读性报告对象
        path_artifacts,  # HLS 可读性门禁实际扫描的 artifact 路径
        profile=str(dict_options.get("profile") or "kernel"),  # HLS 可读性门禁采用的 profile 名称
        style=str(dict_options.get("style") or "current-project"),  # HLS 注释风格与项目风格选择
        baseline_root=path_baseline,  # 可读性门禁对比的 baseline 根路径
        top_function=dict_options.get("top_function"),  # 可选顶层函数名过滤
        fail_on_warning=bool(dict_options.get("fail_on_warning", False)),  # warning 是否直接视为失败
    )

    # report 对象转换为可序列化字典。
    dict_payload = readability_report.to_dict()  # HLS 可读性报告字典

    # 可选 JSON 报告由 runtime report 自身写出，保持格式一致。
    if dict_options.get("report_json") is not None:

        # 报告目标必须是允许写入的 workspace 路径。
        path_report = _resolve_generated_path(  # readability gate 输出的 JSON 报告路径
            dict_options["report_json"],  # 调用方指定的 HLS 可读性报告输出位置
            purpose="HLS readability report path",  # HLS 可读性报告写入时使用的用途标签
        )

        # runtime report 负责生成门禁 JSON 文件。
        readability_report.write_json(path_report)

    # 返回 payload 便于 Python 调用方直接消费。
    return dict_payload

# 注释计划 facade 只构建 rewrite plan，不自动写回源码。
def build_hls_comment_plan(
    artifacts_path: str | Path,
    **dict_options: Any,
) -> dict[str, Any]:
    """
    构建 HLS 注释治理计划，供人工语义改写使用。

    参数:
        artifacts_path: 待分析 HLS artifact 路径，必须位于 workspace 允许范围内。
        dict_options: 兼容旧 facade 的注释计划关键字集合，如 baseline_path、profile、out_path。

    返回:
        包含保留注释、移除范围和 rewrite targets 的注释计划字典。
    """

    # 注释计划入口不允许未知关键字，防止自动化误以为已写出文件。
    _reject_unknown_options(dict_options, COMMENT_PLAN_OPTION_NAMES, "build_hls_comment_plan")

    # 注释计划同样只服务 HLS artifact。
    _reject_non_hls_target(dict_options.get("target"))

    # artifact 输入必须存在且处于 workspace 边界内。
    path_artifacts = _resolve_workspace_input_path(  # rewrite plan 扫描的 HLS 注释计划目标路径
        artifacts_path,  # 注释计划需要扫描的 artifact 目录或文件路径
        purpose="HLS comment-plan target path",  # 注释计划扫描 artifact 时使用的用途标签
        must_exist=True,  # 注释计划只能针对现有 artifact 生成
    )

    # baseline 可选，用于比较纯注释变更。
    path_baseline = _optional_workspace_input_path(  # 注释差异比较时使用的可选 baseline 路径
        dict_options.get("baseline_path"),  # 注释计划可选对比的 baseline 输入
        purpose="baseline path",  # 注释计划读取 baseline 时使用的用途标签
    )

    # rewrite plan 只报告位置和类别，不生成模板注释。
    dict_plan = build_hls_comment_rewrite_plan(  # rewrite plan 生成的 HLS 注释治理计划字典
        path_artifacts,  # 注释计划实际分析的 artifact 路径
        baseline_root=path_baseline,  # 注释计划对比的 baseline 根路径
        profile=str(dict_options.get("profile") or "kernel"),  # 注释治理计划使用的 profile 名称
    )

    # out_path 存在时写出计划文件，便于后续人工审查。
    if dict_options.get("out_path") is not None:

        # 注释计划输出路径需要通过生成路径保护规则。
        path_output = _resolve_generated_path(  # comment-plan JSON 的目标输出路径
            dict_options["out_path"],  # 调用方指定的注释计划 JSON 输出位置
            purpose="HLS comment rewrite plan path",  # 注释计划输出路径校验时使用的用途标签
        )

        # 创建父目录后写入结构化计划。
        path_output.parent.mkdir(parents=True, exist_ok=True)

        # 计划文件保留中文字段和说明。
        path_output.write_text(_json_text(dict_plan), encoding="utf-8")

    # 返回计划字典供调用方继续筛选 rewrite targets。
    return dict_plan

# workflow 运行参数来自默认配置和调用方覆盖项。
def _collect_workflow_runtime_options(dict_options: dict[str, Any]) -> dict[str, Any]:
    """
    合并 workflow 默认配置和 facade 关键字覆盖项。

    参数:
        dict_options: run_hls_workflow 传入并已校验过名称的关键字字典。

    返回:
        已解析 readiness、attempts、provider 和 timeout 的运行参数字典。
    """

    # 默认配置提供 provider、readiness 和执行策略的基准值。
    dict_defaults = load_default_workflow_config()  # facade 合并前的默认 workflow 配置

    # workflow_config 允许调用方覆盖默认 JSON 字段。
    dict_overrides = _load_optional_json(dict_options.get("workflow_config")) or {}  # 调用方提供的 workflow 配置覆盖项

    # 合并后字典只服务本次 facade 调用，不会写回默认配置。
    dict_merged = {**dict_defaults, **dict_overrides}  # 本次 workflow 合并配置

    # readiness 决定 workflow 需要走到 static、execute 或 cosim 等阶段。
    str_readiness = str(dict_options.get("readiness") or dict_merged.get("readiness", "execute"))  # workflow 最终要推进到的 readiness 阶段

    # max_attempts 统一转换为 int，避免 runtime 接收字符串。
    int_max_attempts = int(dict_options.get("max_attempts") or dict_merged.get("max_attempts", 3))  # 单次 workflow 允许的最大生成尝试次数

    # stop_on_human 的显式关键字优先级高于默认配置。
    bool_stop_on_human = _bool_option(  # 命中人工确认点后是否立即暂停 workflow
        dict_options.get("stop_on_human"),  # facade 显式传入的人机阻断策略覆盖值
        dict_merged.get("stop_on_human", True),  # 默认配置中的 stop_on_human 回退值
    )

    # run_external 控制是否实际调用 Vitis 等外部工具。
    bool_run_external = _bool_option(  # 是否允许 workflow 继续调用外部工具链
        dict_options.get("run_external"),  # facade 显式传入的外部工具执行策略覆盖值
        dict_merged.get("run_external", True),  # workflow 默认配置中的外部工具执行回退值
    )

    # comment_language 保留 auto 默认值，由 runtime 再解析为 zh/en。
    str_comment_language = str(  # workflow 传给 prompt 与验证阶段的注释语言选项
        dict_options.get("comment_language") or dict_merged.get("comment_language", "auto")  # 注释语言显式值或默认配置回退值
    )

    # provider_name 缺省时沿用默认配置中的模型 provider。
    str_provider_name = str(  # 本次 workflow 选择的模型 provider 名称
        dict_options.get("provider_name") or dict_merged.get("model_provider", "command")  # facade 显式 provider 或默认模型 provider
    )

    # model timeout 统一转换为 int 秒数。
    int_model_timeout = int(  # 单次模型调用允许等待的最长秒数
        dict_options.get("model_timeout_s") or dict_merged.get("model_timeout_s", 120)  # facade 显式超时或默认模型超时秒数
    )

    # 运行参数字典集中传给新建或恢复流程，减少公开入口分支。
    dict_runtime_payload = dict(  # workflow 新建和恢复流程共用的运行参数
        readiness=str_readiness,  # runtime workflow 需要达到的验证阶段
        max_attempts=int_max_attempts,  # 模型生成循环允许的最大尝试次数
        stop_on_human=bool_stop_on_human,  # 人工确认点是否立即停止 workflow
        # 外部执行和注释语言决定 workflow 后半段验证与渲染策略。
        run_external=bool_run_external,  # 是否允许 runtime 调用 Vitis 等外部工具
        comment_language=str_comment_language,  # 传递给 prompt 和验证阶段的注释语言
        # provider 与 timeout 控制单次模型调用的选择和等待边界。
        provider_name=str_provider_name,  # runtime workflow 选择模型 provider 的名称
        model_timeout_s=int_model_timeout,  # workflow 透传给 provider 的模型超时秒数
    )

    # 返回本次 workflow 调用的运行参数集合。
    return dict_runtime_payload

# 旧 Python reference 兼容关键字已经移除，命中时统一要求调用方重跑 HLS-only 流程。
def _reject_legacy_reference_contract_option(dict_options: dict[str, Any]) -> None:
    """
    拒绝已经移除的 reference_contract 兼容关键字。

    参数:
        dict_options: facade 调用已经收集到的关键字字典。

    返回:
        None。命中旧关键字时直接抛出 ValueError。
    """

    # reference_contract 旧入口已经被移除，不允许继续下沉到 runtime。
    if "reference_contract" in dict_options:

        # 直接返回统一的 HLS-only 重跑错误，避免旧概念残留在 facade 行为里。
        raise ValueError(
            "> ERR: [Python] reference_contract is no longer supported. "
            "This skill is HLS-only now; rerun from a fresh HLS-only workflow run."
        )

# resume workflow 只需要 run 目录、决策文件和 runtime 选项。
def _resume_workflow_from_options(
    dict_options: dict[str, Any],
    dict_runtime_options: dict[str, Any],
) -> dict[str, Any]:
    """
    根据 facade 选项恢复已有 workflow。

    参数:
        dict_options: 已校验的 run_hls_workflow 关键字字典。
        dict_runtime_options: 已合并默认配置后的运行参数字典。

    返回:
        恢复流程的 workflow 摘要字典。
    """

    # resume_dir 必须解析为已存在的允许生成目录。
    path_run_dir = _resolve_generated_path(  # 已通过输出边界校验的 workflow 恢复目录
        dict_options["resume_dir"],  # facade 指定的历史 workflow run 目录
        purpose="workflow resume directory",  # 恢复流程输出路径校验时使用的用途标签
        must_exist=True,  # 恢复目录必须已经存在
    )

    # decision 输入若是字典则物化到 run 目录的 adapter inputs 下。
    path_decision = _materialize_optional_json(  # 恢复流程将读取的人工决策文件路径
        dict_options.get("decision"),  # facade 传入的人工决策路径或内联字典
        path_run_dir / "_adapter_inputs" / "decision.json",  # 恢复流程内联决策落盘的默认目标路径
    )

    # workflow 执行期间把 workspace root 切换到当前 run 目录。
    with use_workspace_root(path_run_dir):

        # runtime workflow 根据 resume_dir 继续未完成阶段。
        dict_workflow_result = run_workflow(  # runtime resume 分支返回的完整 workflow_result 字典
            WorkflowRunRequest(  # 恢复模式下传给 runtime workflow 的请求对象
                resume_dir=path_run_dir,  # runtime 继续执行所依赖的历史 run 根目录
                decision_path=path_decision,  # 恢复阶段可选注入的人工决策文件路径
                stop_on_human=dict_runtime_options["stop_on_human"],  # 恢复阶段命中人工点时是否停止
                run_external=dict_runtime_options["run_external"],  # 恢复阶段是否允许继续调外部工具
                comment_language=dict_runtime_options["comment_language"],  # 恢复阶段沿用的注释语言
                model_timeout_s=dict_runtime_options["model_timeout_s"],  # 恢复阶段单次模型调用超时
            ),
        )

    # 恢复流程只需要返回状态、run 目录和结果路径。
    return _workflow_result_payload(
        path_run_dir,
        dict_workflow_result,
        path_requirements=None,
        path_codegen_plan=None,
    )

# 新建 workflow 负责物化所有 adapter 输入并调用 runtime workflow。
def _start_workflow_from_options(
    spec: str | Path | dict[str, Any],
    dict_options: dict[str, Any],
    dict_runtime_options: dict[str, Any],
) -> dict[str, Any]:
    """
    根据 facade 选项启动新的 HLS workflow。

    参数:
        spec: 新 workflow 使用的 HLS spec 路径或字典。
        dict_options: 已校验的 run_hls_workflow 关键字字典。
        dict_runtime_options: 已合并默认配置后的运行参数字典。

    返回:
        新 workflow 的摘要字典，包含 requirements 和 codegen plan 路径。
    """

    # out_dir 是新建 workflow 的 workspace 根。
    path_run_dir = _resolve_generated_path(  # 新 workflow 将写入的 run 根目录
        dict_options["out_dir"],  # facade 指定的新 workflow 输出目录
        purpose="workflow output directory",  # 新 workflow 输出目录校验时使用的用途标签
    )

    # adapter 输入文件由独立 helper 物化，保持启动函数只负责编排 workflow。
    dict_input_paths = _prepare_workflow_input_files(spec, dict_options, path_run_dir)  # facade 物化出的新 workflow 输入文件路径集合

    # hls_profile 路径或字典在调用 runtime 前解析。
    dict_hls_profile = _load_optional_json(dict_options.get("hls_profile"))  # 调用方提供的 HLS profile 覆盖字典

    # workflow 执行期间把 workspace root 约束在本次 run 目录。
    with use_workspace_root(path_run_dir):

        # runtime workflow 负责 requirements、prompt、模型调用和验证阶段。
        dict_workflow_result = run_workflow(  # runtime 新建流程返回的完整 workflow_result 字典
            WorkflowRunRequest(  # 新建模式下传给 runtime workflow 的请求对象
                spec_path=dict_input_paths["spec"],  # runtime 将读取的物化 spec 文件路径
                target="hls",  # runtime workflow 固定执行 HLS-only 流程
                out_dir=path_run_dir,  # 当前新 run 的 workspace 根目录
                decision_path=dict_input_paths["decision"],  # 新 run 可选人工决策输入文件
                evidence_path=dict_input_paths["evidence"],  # 新 run 可选外部证据输入文件
                provider_name=dict_runtime_options["provider_name"],  # 本轮 workflow 使用的模型 provider 名称
                provider_command=dict_options.get("provider_command"),  # command provider 需要执行的外部命令
                readiness=dict_runtime_options["readiness"],  # workflow 希望推进到的验证深度
                max_attempts=dict_runtime_options["max_attempts"],  # 模型生成循环最多允许的尝试次数
                stop_on_human=dict_runtime_options["stop_on_human"],  # 命中人工确认点时是否立刻停止
                run_external=dict_runtime_options["run_external"],  # 是否允许调用外部 HLS/Vitis 工具
                comment_language=dict_runtime_options["comment_language"],  # prompt 与验证阶段使用的注释语言
                hls_profile=dict_hls_profile if isinstance(dict_hls_profile, dict) else None,  # 传给 runtime 的 HLS profile 覆盖
                confirmation=_load_confirmation(dict_options.get("confirmation")),  # 新 run 显式用户确认信息
                model_timeout_s=dict_runtime_options["model_timeout_s"],  # 单次模型调用的超时秒数
            ),
        )

    # 返回新建流程摘要，并包含 requirements/codegen plan 路径。
    return _workflow_result_payload(
        path_run_dir,
        dict_workflow_result,
        path_requirements=dict_input_paths["requirements"],
        path_codegen_plan=dict_input_paths["codegen_plan"],
    )

# 新 workflow 的 adapter 输入文件集中物化，便于启动流程保持短小。
def _prepare_workflow_input_files(
    spec: str | Path | dict[str, Any],
    dict_options: dict[str, Any],
    path_run_dir: Path,
) -> dict[str, Path | None]:
    """
    物化新 workflow 所需的 facade 输入文件。

    参数:
        spec: 新 workflow 使用的 HLS spec 路径或字典。
        dict_options: 已校验的 run_hls_workflow 关键字字典。
        path_run_dir: 新 workflow 的 run 目录。

    返回:
        spec、requirements、codegen_plan、evidence 和 decision 的路径集合。
    """

    # adapter_inputs 目录保存 facade 物化的 spec、requirements 和决策文件。
    path_inputs_dir = path_run_dir / "_adapter_inputs"  # 当前 run 用于保存 facade 输入快照的目录

    # 创建输入目录，后续 JSON 文件都写入这里。
    path_inputs_dir.mkdir(parents=True, exist_ok=True)

    # facade spec 应用用户确认、设计需求和接口配置。
    dict_prepared_spec = _prepare_facade_spec(  # 已补齐 defaults 且准备用于新 workflow 的 HLS spec 字典
        spec,  # facade 原始传入的 spec 路径或字典
        design_requirements=_load_optional_json(dict_options.get("design_requirements")),  # facade 传入的设计需求覆盖字典
        pipeline_required=dict_options.get("pipeline_required"),  # facade 显式要求的 pipeline 策略
        streamability=dict_options.get("streamability"),  # facade 显式要求的流式能力声明
        interface_family=dict_options.get("interface_family"),  # facade 显式要求的接口族
        interface_profile=_load_optional_json(dict_options.get("interface_profile")),  # facade 传入的接口 profile 覆盖
        confirmation=_load_confirmation(dict_options.get("confirmation")),  # facade 传入的用户确认信息
    )

    # requirements payload 是 workflow 的需求阶段输入。
    path_requirements = _write_json_object(  # requirements 阶段将直接读取的 JSON 文件路径
        path_inputs_dir / "requirements.json",  # adapter inputs 下保存的 requirements 文件路径
        build_requirements_payload(dict_prepared_spec),  # 从 HLS spec 推导出的 requirements 载荷
    )

    # codegen plan 是 prompt 和 workflow 共同使用的生成计划。
    path_codegen_plan = _write_json_object(  # prompt 渲染与 workflow 执行共用的 codegen plan 文件路径
        path_inputs_dir / "codegen_plan.json",  # 本次 run 里给 prompt 和生成阶段复用的 codegen plan 文件路径
        build_codegen_plan(dict_prepared_spec),  # 面向 prompt 与生成阶段的结构化 codegen 计划载荷
    )

    # spec 内部只保存相对 plan 路径，保证 run 目录可搬移。
    str_plan_relative_path = path_codegen_plan.relative_to(path_run_dir).as_posix()  # run 目录内的 plan 相对路径

    # runtime workflow 根据该字段在 run 目录内重新定位 codegen plan。
    dict_prepared_spec["codegen_plan_path"] = str_plan_relative_path  # spec 内嵌的 codegen plan 相对路径

    # spec 最终物化为 runtime workflow 接收的 JSON 文件。
    path_spec = _materialize_spec(  # runtime workflow 将读取的 spec JSON 输入文件
        dict_prepared_spec,  # 已补齐 defaults 并可安全物化的 HLS spec 字典
        path_inputs_dir / "spec.json",  # adapter inputs 下保存的规范化 spec 文件路径
    )

    # evidence 可以是路径或字典；字典会写入 adapter inputs。
    path_evidence = _materialize_optional_json(  # planning 或验证阶段可选读取的证据 JSON 文件路径
        dict_options.get("evidence"),  # facade 传入的证据路径或内联字典
        path_inputs_dir / "evidence.json",  # adapter inputs 下保存的证据文件路径
    )

    # decision 可以用于跳过或确认人工分支。
    path_decision = _materialize_optional_json(  # workflow 人工分支恢复时可选读取的决策 JSON 文件路径
        dict_options.get("decision"),  # 新 workflow 启动前可选注入的人工决策路径或内联字典
        path_inputs_dir / "decision.json",  # adapter inputs 下保存的人工决策文件路径
    )

    # 返回 runtime workflow 启动所需的全部输入路径。
    return {
        "spec": path_spec,  # runtime workflow 读取的 spec 文件
        "requirements": path_requirements,  # requirements 阶段输入文件
        "codegen_plan": path_codegen_plan,  # prompt 与生成阶段共用计划文件
        "evidence": path_evidence,  # 可选外部证据文件
        "decision": path_decision,  # 可选人工决策文件
    }

# workflow 摘要 payload 独立组装，减少 run_* 入口中的大字典字面量。
def _workflow_result_payload(
    path_run_dir: Path,
    dict_workflow_result: dict[str, Any],
    *,
    path_requirements: Path | None,
    path_codegen_plan: Path | None,
) -> dict[str, Any]:
    """
    组装 workflow facade 返回 payload。

    参数:
        path_run_dir: workflow run 目录。
        dict_workflow_result: runtime workflow 返回的完整结果字典。
        path_requirements: 新建流程的 requirements 文件路径；恢复流程为空。
        path_codegen_plan: 新建流程的 codegen plan 文件路径；恢复流程为空。

    返回:
        对外稳定的 workflow 摘要字典。
    """

    # 初始化摘要字典时先写入 runtime workflow 的终态字段。
    dict_payload: dict[str, Any] = {
        "status": dict_workflow_result["status"],  # 对外摘要中的 workflow 终态
    }  # 包含 status 的 workflow facade 摘要字典

    # run_dir 使用字符串，便于 JSON 序列化和 CLI 展示。
    dict_payload["run_dir"] = str(path_run_dir)  # workflow run 目录文本

    # result_path 固定指向 run 目录下的 workflow_result.json。
    dict_payload["result_path"] = str(path_run_dir / "workflow_result.json")  # workflow 结果文件文本路径

    # workflow_result 保留完整 runtime 结果，供高级调用方继续分析。
    dict_payload["workflow_result"] = dict_workflow_result  # runtime workflow 完整结果

    # 新建流程才有 requirements_path。
    if path_requirements is not None:

        # requirements_path 指向 facade 物化的需求 JSON。
        dict_payload["requirements_path"] = str(path_requirements)  # requirements 文件文本路径

    # 只有新建 workflow 才会把 codegen plan 文件路径回传给 facade 调用方。
    if path_codegen_plan is not None:

        # codegen_plan_path 让上层直接定位本次 run 里保存的生成计划文件。
        dict_payload["codegen_plan_path"] = str(path_codegen_plan)  # workflow facade 回传的 codegen plan 文本路径

    # 返回 workflow facade 摘要 payload。
    return dict_payload

# facade spec 准备统一处理需求默认值、确认字段和 HLS 规范化。
def _prepare_facade_spec(
    spec: str | Path | dict[str, Any],
    *,
    # 需求覆盖项来自 facade 调用方，缺省时由 runtime 默认值补齐。
    design_requirements: dict[str, Any] | None,
    # pipeline 和 streamability 控制 HLS 生成策略。
    pipeline_required: bool | None,
    streamability: str | None,
    # 接口族和接口 profile 限定生成 kernel 的外部连接方式。
    interface_family: str | None,
    interface_profile: dict[str, Any] | None,
    # confirmation 明确记录用户已确认需求可进入生成阶段。
    confirmation: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    为 facade 调用准备规范化 HLS spec。

    参数:
        spec: 原始 spec 字典或技能内 JSON 路径。
        design_requirements: 用户显式提供的设计需求覆盖项。
        pipeline_required: 用户显式提供的流水线要求。
        streamability: 用户显式提供的流式能力声明。
        interface_family: 用户显式提供的接口族。
        interface_profile: 用户显式提供的接口 profile。
        confirmation: 用户确认信息；提供时必须包含 confirmed_by_user 和 confirmation_notes。

    返回:
        已应用需求默认值并通过 HLS target 规范化的 spec 字典。
    """

    # 原始 spec 读取时复制字典，避免修改调用方对象。
    dict_raw_spec = _load_raw_spec(spec)  # 需求默认值合并前的调用方 spec 副本

    # 用户确认字段只有在显式 confirmation 存在时才写入 spec。
    bool_confirmed_by_user = confirmation.get("confirmed_by_user") if confirmation else None  # 用户确认布尔值

    # confirmation_notes 保留用户确认上下文，供需求验证使用。
    str_confirmation_notes = confirmation.get("confirmation_notes") if confirmation else None  # 用户确认说明文本

    # requirements 默认值阶段合并设计需求、接口和确认字段。
    dict_enriched_spec = apply_requirement_defaults(  # 已叠加需求覆盖、接口约束和确认字段的 spec
        dict_raw_spec,  # 复制后的原始 spec 副本
        design_requirements=design_requirements,  # 用户提供的设计需求覆盖字典
        # pipeline 与 streamability 共同描述计算结构和数据传输语义。
        pipeline_required=pipeline_required,  # 用户提供的 pipeline 约束开关
        streamability=streamability,  # 用户提供的流式传输或消费能力声明
        # interface 选项把 facade 的外部接口约束传给需求层。
        interface_family=interface_family,  # 用户提供的接口族约束
        interface_profile=interface_profile,  # 用户给出的端口打包、协议细节与带宽约束配置
        # confirmation 字段只在用户显式确认后才参与需求验证。
        confirmed_by_user=bool_confirmed_by_user,  # 用户是否已经显式确认当前需求可进入生成
        confirmation_notes=str_confirmation_notes,  # 用户确认时留下的补充说明文本
    )

    # normalize_spec 保证 target 固定为 HLS 并填充 runtime 需要的规范字段。
    dict_normalized_spec = normalize_spec(dict_enriched_spec, target="hls")  # runtime 可消费的 HLS spec 字典

    # 未确认的需求会在这里明确阻断，而不是进入后续生成。
    validate_requirement_confirmation(dict_normalized_spec)

    # 返回 runtime 可直接消费的 HLS spec。
    return dict_normalized_spec

# comment_language 统一通过 runtime 配置解析，避免 facade 自行解释 auto。
def _require_resolved_comment_language(obj_comment_language: Any) -> str:
    """
    解析 facade 传入的注释语言选项。

    参数:
        obj_comment_language: 调用方传入的 comment_language 值；None 表示 auto。

    返回:
        runtime 接受的注释语言字符串。
    """

    # None 和空值都回退到 auto，由 user_config 解析最终语言。
    str_comment_language = str(obj_comment_language or "auto")  # 注释语言原始选项

    # runtime 解析器负责把 auto 转成项目配置要求的语言。
    str_resolved_language = resolve_comment_language(str_comment_language)  # 已解析注释语言

    # 返回解析后的语言值。
    return str_resolved_language

# HLS-only 边界在所有 public facade 入口前置检查。
def _reject_non_hls_target(obj_target: Any) -> None:
    """
    拒绝非 HLS target。

    参数:
        obj_target: 调用方传入的 target 选项；None 表示默认 HLS。

    返回:
        无返回值；非 HLS target 会抛出 ValueError。
    """

    # None 表示调用方使用默认 HLS target，字符串 hls 为唯一显式合法值。
    if obj_target not in (None, "hls"):

        # 目标不符时保留 HLS-only 技能边界。
        raise ValueError("> ERR: [Python] This skill is HLS-only; target must be `hls`.")

# spec 物化负责把路径或字典输入转换为 runtime workflow 的 spec 文件。
def _materialize_spec(spec: str | Path | dict[str, Any], path_out: Path) -> Path:
    """
    将 spec 输入写入指定 JSON 文件。

    参数:
        spec: 已准备好的 spec 字典，或技能内 spec JSON 路径。
        path_out: spec JSON 的输出路径。

    返回:
        写入后的 spec JSON 路径。
    """

    # 路径输入先通过技能内边界校验再读取。
    if isinstance(spec, (str, Path)):

        # read_spec 会同时执行 HLS target 规范化。
        dict_normalized_spec = read_spec(  # 路径 spec 读取后得到的规范化 HLS spec 字典
            _resolve_skill_input_path(spec, purpose="spec path", must_exist=True),  # 已通过技能根边界检查的 spec 路径
            target="hls",  # 路径 spec 读取时固定按 HLS target 规范化
        )  # 路径输入规范化 spec

    # 字典输入直接交给 normalize_spec，避免重复文件读写。
    else:

        # normalize_spec 返回 runtime workflow 需要的字段布局。
        dict_normalized_spec = normalize_spec(spec, target="hls")  # 字典输入规范化 spec

    # spec 输出父目录由调用方提供，写入前确保存在。
    path_out.parent.mkdir(parents=True, exist_ok=True)

    # write_spec 统一 JSON 格式，减少手写序列化差异。
    write_spec(path_out, dict_normalized_spec)

    # 返回 spec 文件路径供 workflow 调用。
    return path_out

# 小型 JSON 写入 helper 复用统一编码和缩进策略。
def _write_json_object(path_output: Path, dict_payload: dict[str, Any]) -> Path:
    """
    写入 JSON 字典并返回输出路径。

    参数:
        path_output: JSON 输出路径。
        dict_payload: 需要写入的结构化字典。

    返回:
        已写入的 JSON 文件路径。
    """

    # 写入前创建父目录，适配新的 run 子目录。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 统一 JSON 文本格式，方便人工 diff。
    path_output.write_text(_json_text(dict_payload), encoding="utf-8")

    # 返回输出路径供调用方放入摘要 payload。
    return path_output

# 可选 JSON 输入支持路径复用或字典物化。
def _materialize_optional_json(obj_value: Any, path_out: Path) -> Path | None:
    """
    将可选 JSON 输入转换为路径。

    参数:
        obj_value: None、技能内 JSON 路径或待写出的字典。
        path_out: 字典输入需要写入的目标路径。

    返回:
        JSON 路径；输入为 None 时返回 None。

    异常:
        TypeError: 当输入既不是 None、路径也不是字典时抛出。
    """

    # None 表示调用方没有提供该类输入。
    if obj_value is None:

        # 上层 runtime 会按缺省输入处理。
        return None

    # 路径输入只做边界校验，不复制文件。
    if isinstance(obj_value, (str, Path)):

        # JSON 输入路径必须位于技能目录内并且已经存在。
        return _resolve_skill_input_path(obj_value, purpose="JSON input path", must_exist=True)

    # 字典输入会物化为 adapter_inputs 下的 JSON 文件。
    if isinstance(obj_value, dict):

        # 写入前确保 adapter 输入目录存在。
        path_out.parent.mkdir(parents=True, exist_ok=True)

        # 保存结构化输入，便于恢复和审计。
        path_out.write_text(_json_text(obj_value), encoding="utf-8")

        # 返回物化后的 JSON 路径。
        return path_out

    # 其他类型无法安全解释为 JSON 输入。
    raise TypeError("> ERR: [Python] JSON input must be a path, dict, or None.")

# 可选 JSON 读取 helper 返回字典，供 prompt/workflow/validation 统一使用。
def _load_optional_json(obj_value: Any) -> dict[str, Any] | None:
    """
    读取可选 JSON 字典输入。

    参数:
        obj_value: None、字典或技能内 JSON 路径。

    返回:
        JSON 字典；输入为 None 时返回 None。
    """

    # None 表示该类输入没有提供。
    if obj_value is None:

        # 调用方会按缺省参数继续执行。
        return None

    # 字典输入直接返回，避免破坏调用方已构造的结构。
    if isinstance(obj_value, dict):

        # 返回原字典对象；上层若会修改必须自行复制。
        return obj_value

    # 路径输入必须位于技能目录内并且存在。
    path_json = _resolve_skill_input_path(obj_value, purpose="JSON input path", must_exist=True)  # 已解析并通过技能根边界检查的 JSON 输入文件路径

    # 返回解析后的 JSON 字典。
    return json.loads(path_json.read_text(encoding="utf-8"))

# confirmation 是安全边界输入，必须显式确认且提供说明。
def _load_confirmation(obj_value: Any) -> dict[str, Any] | None:
    """
    读取并校验用户确认信息。

    参数:
        obj_value: None、确认字典或技能内 JSON 路径。

    返回:
        规范化后的确认字典；输入为 None 时返回 None。

    异常:
        ValueError: 当 confirmed_by_user 不为真或 confirmation_notes 为空时抛出。
    """

    # 先复用通用 JSON 读取逻辑。
    dict_confirmation = _load_optional_json(obj_value)  # 原始确认字典

    # None 表示调用方没有提供确认信息。
    if dict_confirmation is None:

        # 未提供确认时不向 spec 注入确认字段。
        return None

    # notes 兼容旧字段名，最终统一写入 confirmation_notes。
    str_notes = str(  # 归一化后的用户确认说明文本
        dict_confirmation.get("confirmation_notes") or dict_confirmation.get("notes") or ""  # 兼容 confirmation_notes 与旧 notes 字段
    ).strip()

    # confirmed_by_user 必须为真值，避免隐式 override 伪装成人工确认。
    if not dict_confirmation.get("confirmed_by_user"):

        # 明确指出 confirmation 中缺少确认布尔值。
        raise ValueError("> ERR: [Python] confirmation.confirmed_by_user must be true when confirmation is provided.")

    # 确认说明不能为空，否则审计时无法判断用户确认了什么。
    if not str_notes:

        # 明确指出 confirmation 中缺少说明文本。
        raise ValueError("> ERR: [Python] confirmation.confirmation_notes is required when confirmation is provided.")

    # 返回 workflow/runtime 统一使用的确认字段。
    return {"confirmed_by_user": True, "confirmation_notes": str_notes}

# 原始 spec 读取时保护调用方字典不被 facade 原地修改。
def _load_raw_spec(spec: str | Path | dict[str, Any]) -> dict[str, Any]:
    """
    读取原始 spec 字典。

    参数:
        spec: 技能内 spec JSON 路径或已解析字典。

    返回:
        可安全修改的 spec 字典副本。
    """

    # 字典输入复制后再返回，避免 apply_requirement_defaults 修改调用方对象。
    if isinstance(spec, dict):

        # deepcopy 保留嵌套字段并隔离调用方对象。
        return deepcopy(spec)

    # 路径输入通过技能内输入边界校验。
    path_spec = _resolve_skill_input_path(spec, purpose="spec path", must_exist=True)  # spec JSON 输入路径

    # 返回 JSON 文件解析得到的 spec 字典。
    return json.loads(path_spec.read_text(encoding="utf-8"))

# 相对技能输入路径只能落在技能根目录下。
def _resolve_skill_input_path(path_input: str | Path, *, purpose: str, must_exist: bool) -> Path:
    """
    解析技能内输入路径并执行边界检查。

    参数:
        path_input: 调用方传入的相对或绝对路径。
        purpose: 错误信息中的路径用途说明。
        must_exist: 是否要求路径已经存在。

    返回:
        已解析的绝对 Path。

    异常:
        ValueError: 当路径不存在、无法解析或逃逸技能根目录时抛出。
    """

    # 先把调用方输入转换为 Path，便于统一处理相对路径。
    path_raw = Path(path_input)  # 原始输入路径

    # 相对路径按技能根目录解析，绝对路径保持原样再做边界检查。
    path_candidate = path_raw if path_raw.is_absolute() else SKILL_ROOT / path_raw  # 待解析候选路径

    # resolve(strict=...) 同时处理不存在路径和符号链接。
    try:

        # 解析后的路径用于后续 relative_to 边界判断。
        path_resolved = path_candidate.resolve(strict=must_exist)  # 解析后的输入路径

    # 不存在路径转换成带用途的 ValueError，便于 facade 调用方处理。
    except FileNotFoundError:

        # 隐藏底层 traceback，只报告输入路径不在预期位置。
        raise ValueError(f"> ERR: [Python] Missing {purpose} inside skill: {path_input}") from None

    # 其他系统级解析错误保留原始异常作为 cause。
    except OSError as error:

        # 报告 resolve 失败的具体系统错误。
        raise ValueError(f"> ERR: [Python] Failed to resolve {purpose}: {path_input}; {error}") from error

    # 技能输入必须位于技能根目录下。
    try:

        # relative_to 成功说明 path_resolved 未逃逸技能根。
        path_resolved.relative_to(SKILL_ROOT)

    # 越界路径会被明确拒绝。
    except ValueError as error:

        # 错误信息保留技能根目录，便于调用方修正输入路径。
        raise ValueError("> ERR: [Python] {} must stay inside skill root: {}".format(purpose, path_input)) from error

    # 返回已通过技能根边界检查的绝对路径。
    return path_resolved

# 生成路径必须走 workspace 配置，同时不得写入受保护源码目录。
def _resolve_generated_path(path_input: str | Path, *, purpose: str, must_exist: bool = False) -> Path:
    """
    解析并校验 facade 生成路径。

    参数:
        path_input: 调用方传入的输出或 run 路径。
        purpose: 错误信息中的路径用途说明。
        must_exist: 是否要求路径已经存在。

    返回:
        已通过 workspace 和保护目录校验的 Path。

    异常:
        ValueError: 当目标不存在、命中技能根或写入受保护源码入口时抛出。
    """

    # workspace helper 会处理仓库允许输出边界。
    path_resolved = require_configured_output_path(Path(path_input), purpose=purpose)  # 已解析生成路径

    # 恢复流程等场景需要确认目标已经存在。
    if must_exist and not path_resolved.exists():

        # 不存在的生成路径不能用于 resume 或输入读取。
        raise ValueError(f"> ERR: [Python] Missing generated path for {purpose}: {path_input}")

    # 尝试判断生成路径是否落在技能源码树内。
    try:

        # 相对路径分段用于检查第一层是否受保护。
        tuple_relative_parts = path_resolved.relative_to(SKILL_ROOT).parts  # 技能内相对路径片段

    # 不在技能根内的 workspace 输出由 workspace helper 负责边界。
    except ValueError:

        # 外部 workspace 输出不参与技能源码保护目录判断。
        return path_resolved

    # 技能根本身不能作为输出目标。
    if not tuple_relative_parts:

        # 阻止调用方把文件直接写到技能根。
        raise ValueError("> ERR: [Python] {} must not target the skill root.".format(purpose))

    # 受保护根和受保护文件共同组成禁止写入集合。
    set_protected_entries = protected_roots() | protected_files()  # 受保护技能源码入口集合

    # 第一层路径命中受保护入口时拒绝写入。
    if tuple_relative_parts[0] in set_protected_entries:

        # 错误信息指出被保护的首层目录或文件名。
        raise ValueError(
            "> ERR: [Python] {} must not write into protected entry {!r}.".format(
                purpose,
                tuple_relative_parts[0],
            )
        )

    # 返回通过保护目录检查的生成路径。
    return path_resolved

# workspace 输入路径统一转换为 ValueError，方便 facade 调用方捕获。
def _resolve_workspace_input_path(path_input: str | Path, *, purpose: str, must_exist: bool) -> Path:
    """
    解析 workspace 输入路径。

    参数:
        path_input: 调用方传入的 workspace 路径。
        purpose: 错误信息中的路径用途说明。
        must_exist: 是否要求路径已经存在。

    返回:
        已通过 workspace 输入边界检查的 Path。

    异常:
        ValueError: 当 workspace helper 拒绝该输入路径时抛出。
    """

    # workspace helper 可能抛出不同异常，facade 统一为 ValueError。
    try:

        # require_workspace_path 执行实际 workspace 边界检查。
        path_resolved = require_workspace_path(  # 已通过 workspace 边界检查的输入路径
            Path(path_input),  # 调用方提供的 workspace 输入路径对象
            purpose=purpose,  # workspace helper 报错时使用的路径用途说明
            must_exist=must_exist,  # 调用方要求该 workspace 输入必须预先存在与否
        )

    # 捕获 workspace helper 的路径错误并保持原始说明。
    except Exception as error:

        # facade 对外只暴露 ValueError，避免调用方依赖内部异常类型。
        raise ValueError(f"> ERR: [Python] Invalid workspace input for {purpose}: {error}") from error

    # 返回通过 workspace 边界检查的路径。
    return path_resolved

# 可选 workspace 输入路径在缺省时保持 None。
def _optional_workspace_input_path(obj_path: Any, *, purpose: str) -> Path | None:
    """
    解析可选 workspace 输入路径。

    参数:
        obj_path: None 或 workspace 输入路径。
        purpose: 错误信息中的路径用途说明。

    返回:
        已解析 Path；输入为 None 时返回 None。
    """

    # None 表示调用方未启用该可选输入。
    if obj_path is None:

        # validator 或 runner 将按缺省策略处理。
        return None

    # 非 None 输入必须存在并通过 workspace 边界检查。
    return _resolve_workspace_input_path(obj_path, purpose=purpose, must_exist=True)

# 可选普通路径只在 prompt context_dir 中使用，不要求存在性。
def _optional_path(obj_path: Any) -> Path | None:
    """
    将可选路径值转换为 Path。

    参数:
        obj_path: None 或可被 Path 接受的路径值。

    返回:
        Path 对象；输入为 None 时返回 None。
    """

    # None 表示 prompt 没有提供 context_dir。
    if obj_path is None:

        # render_prompt 会按缺省上下文继续执行。
        return None

    # context_dir 交给 runtime prompt 层处理存在性和读取语义。
    return Path(obj_path)

# bool option 需要区分“未传入”和“显式 False”。
def _bool_option(obj_value: Any, obj_default: Any) -> bool:
    """
    解析布尔 facade 选项。

    参数:
        obj_value: 调用方显式传入的选项值；None 表示未传。
        obj_default: 默认配置中的回退值。

    返回:
        解析后的 bool 值。
    """

    # 显式 None 才回退默认配置，False 必须被保留。
    if obj_value is None:

        # 默认值同样转成 bool，兼容 JSON 中的真假值。
        return bool(obj_default)

    # 显式传入值直接按 Python bool 语义转换。
    return bool(obj_value)

# 统一 JSON 文本格式，避免多个 helper 各自拼接换行。
def _json_text(dict_payload: dict[str, Any]) -> str:
    """
    序列化 JSON 字典并补齐末尾换行。

    参数:
        dict_payload: 需要写入文件的 JSON 字典。

    返回:
        使用 UTF-8 友好格式的 JSON 文本。
    """

    # ensure_ascii=False 保留中文诊断和注释内容。
    str_json_text = json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n"  # JSON 文件文本

    # 返回可直接写入文件的 JSON 文本。
    return str_json_text

# **kwargs 兼容层必须显式拒绝未知选项。
def _reject_unknown_options(
    dict_options: dict[str, Any],
    set_allowed_names: set[str],
    str_function_name: str,
) -> None:
    """
    检查 facade 关键字是否都在允许集合内。

    参数:
        dict_options: 调用方传入的关键字字典。
        set_allowed_names: 当前 facade 入口允许的关键字名称集合。
        str_function_name: 当前 facade 函数名，用于错误信息。

    返回:
        无返回值；发现未知关键字时抛出 ValueError。
    """

    # 集合差值定位调用方传入但 facade 未声明的关键字。
    set_unknown_names = set(dict_options) - set_allowed_names  # 调用方传入但当前 facade 未声明的关键字集合

    # 没有未知关键字时直接结束检查。
    if not set_unknown_names:

        # 正常路径不需要返回额外状态。
        return

    # 排序后错误信息稳定，便于测试断言和人工比对。
    str_unknown_names = ", ".join(sorted(set_unknown_names))  # 未知关键字展示文本

    # 报错时带上 facade 函数名，便于调用方定位入口。
    raise ValueError(
        "> ERR: [Python] {} got unknown option(s): {}".format(
            str_function_name,
            str_unknown_names,
        )
    )
