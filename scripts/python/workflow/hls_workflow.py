"""HLS 端到端 workflow 运行器，负责新建、恢复和推进 staged 生成流程。"""

# 延迟注解避免导入期解析复杂容器与前向引用。
from __future__ import annotations

# dataclass 负责把 stage 运行上下文收敛成可复用对象。
from dataclasses import dataclass, field

# copy/json/Path/Any 分别负责深拷贝、JSON 读写、路径治理与动态载荷标注。
import copy
import json
from pathlib import Path
from typing import Any

# 运行期配置负责远端 Vitis 路由偏好与工具阻断提示。
from scripts.python.config.hls_config import resolve_vitis_skill_preference, vitis_blocking_tool_ids

# 模型响应抽取器负责把大模型输出回收成阶段工件。
from scripts.python.generation.extractor import ExtractionError, extract_response

# 接口合同检查负责在生成后校验顶层接口形态。
from scripts.python.validation.interface_contract import audit_interface

# provider 工厂、协议和异常负责具体模型调用。
from scripts.python.generation.model_provider import (
    GenerationContext,
    ManualResponseRequired,
    ModelProvider,
    ModelProviderError,
    build_model_provider,
)

# planning 与 prompt 负责中间阶段计划和阶段提示渲染。
from scripts.python.generation.planning import decompose_spec
from scripts.python.generation.prompt import _manifest_for, _stage_manifest_for, render_prompt

# requirements、spec 与 trace 负责合同、确认和运行痕迹。
from scripts.python.generation.requirements import (
    apply_requirement_defaults,
    build_codegen_plan,
    build_requirements_payload,
    validate_codegen_plan_payload,
    validate_requirement_confirmation,
)

# spec/trace 负责运行输入解析与执行痕迹落盘。
from scripts.python.generation.spec import SpecError, read_spec, write_spec
from scripts.python.workflow.trace import append_trace_event, read_trace, safe_path, spec_summary

# 用户配置、最终验证与向量检查共同组成后半程闭环。
from scripts.python.config.user_config import comment_language_request, resolve_comment_language
from scripts.python.validation.hls_artifacts import ValidationRunOptions, validate_generated

# 工作区 helper 负责所有输入输出路径、状态文件与文本产物落盘。
from scripts.python.config.workspace import (
    require_configured_output_path,
    require_workspace_path,
    require_workspace_path_from,
    require_write_path,
    # 状态与工作区复用工具负责 workflow 生命周期内的状态同步。
    update_workflow_state,
    use_workspace_root,
    write_json,
    write_text,
)

# workflow 最终状态集合决定 result["status"] 的合法取值。
WORKFLOW_STATUSES = (  # workflow 允许写入的最终状态枚举
    "passed",  # 所有阶段与最终验证均已通过
    "failed",  # 某个阶段执行失败且不满足人工或工具链阻断条件
    "blocked_human",  # 需要人工确认 comment language 或阶段决策
    "blocked_toolchain",  # 需要外部工具链或远端能力补完验证闭环
    "max_attempts",  # 达到最大尝试次数后仍未生成可接受结果
    "invalid_response",  # 模型响应结构不满足阶段提取合同
)

# 默认阶段顺序覆盖 requirements 到最终 HLS 产物生成。
DEFAULT_STAGES = [  # workflow 的默认阶段执行顺序
    "requirements",  # 先澄清需求与阻断条件
    "codegen_plan",  # 再生成面向后续阶段的结构化实现计划
    "tests",  # 然后准备验证口径和最小测试约束
    "hls",  # 最后交付 HLS 代码与相关工件
]

# HLS 是当前 skill 唯一允许的最终阶段。
FINAL_STAGE = "hls"  # workflow 的最终阶段名

# 统一封装 workflow 配置与恢复阶段的非法状态异常。
class WorkflowError(ValueError):
    """workflow 配置或恢复状态非法时抛出的统一异常。"""

# 收敛公共 run_workflow 入口参数，避免外部接口继续扩散超长关键字列表。
@dataclass(frozen=True)
class WorkflowRunRequest:
    """启动或恢复 workflow 时使用的公共请求对象。"""

    # 新建流程会从这份 spec 文件开始分解阶段任务。
    spec_path: Path | None = None  # 输入 spec 文件路径

    # 目标值仍保留在入口对象里，用来守住 HLS-only 边界。
    target: str | None = None  # 调用方声明的目标类型

    # 新 run 的所有中间产物和结果文件都会写入这个目录。
    out_dir: Path | None = None  # 本轮 workflow 的输出根目录

    # 恢复模式下从这个历史 run 目录继续推进后续阶段。
    resume_dir: Path | None = None  # 需要恢复的既有 run 目录

    # 人工决策 JSON 会在新建或恢复流程里覆写阻断节点状态。
    decision_path: Path | None = None  # 可选人工决策文件路径

    # 规划阶段可选读取这份证据 JSON 来补充分解上下文。
    evidence_path: Path | None = None  # 供 planning 参考的证据文件路径

    # provider 选择直接决定 workflow 下游如何发起模型调用。
    provider_name: str = "manual"  # 本轮使用的模型 provider 名称

    # command provider 会把这个命令串透传给外部执行层。
    provider_command: str | None = None  # command provider 的命令覆盖

    # readiness 控制最终验证要推进到静态、综合还是执行层级。
    readiness: str = "execute"  # workflow 目标就绪级别

    # 超过这个尝试上限后，workflow 会返回 max_attempts 终态。
    max_attempts: int = 3  # 自动重试允许的最大轮数

    # 命中人工确认点时是否立即暂停而不是继续自动推进。
    stop_on_human: bool = True  # 人工阻断点的暂停策略

    # 这个开关决定是否放行 Vitis 等外部工具链调用。
    run_external: bool = True  # 是否允许外部验证链路

    # prompt 与验证阶段统一读取这里声明的注释语言策略。
    comment_language: str = "auto"  # 本轮请求的注释语言

    # profile 覆盖会补充 prompt 和验证阶段共享的 HLS 约束。
    hls_profile: dict[str, Any] | None = None  # 可选 HLS profile 覆盖字典

    # 用户确认过的 requirements 会通过这里并回原始 spec。
    confirmation: dict[str, object] | None = None  # 可选 requirement 确认载荷

    # provider.generate 调用都会继承这个秒级超时边界。
    model_timeout_s: int = 120  # 单次模型调用超时秒数

    # 关闭时只保留 result/trace，不再刷新 workflow-state.json。
    state_updates: bool = True  # 是否继续写 workflow-state

# 收敛恢复流程所需的运行策略，避免恢复入口继续堆散参。
@dataclass(frozen=True)
class ResumeWorkflowRequest:
    """恢复已有 workflow run 时使用的请求对象。"""

    # 恢复流程始终围绕这个历史 run 目录回读计划、状态和产物。
    resume_dir: Path  # 要继续执行的历史 run 根目录

    # blocked_human 恢复时才会读取这份人工决策 JSON。
    decision_path: Path | None  # blocked_human 恢复使用的决策文件路径

    # 恢复后若再次遇到人工分叉，按这个开关决定是否暂停。
    stop_on_human: bool  # 人工分叉暂停策略

    # 恢复阶段是否继续放行本地或远端外部工具链验证。
    run_external: bool  # 恢复流程是否允许外部验证链路

    # 恢复入口可以显式覆写历史 run 中保存的注释语言策略。
    comment_language: str  # 恢复流程请求的注释语言

    # 恢复后 provider.generate 继续沿用或覆写的超时上限。
    model_timeout_s: int  # 恢复流程沿用的单次模型调用超时秒数

    # 恢复流程是否继续把状态快照写回 workflow-state.json。
    state_updates: bool  # 恢复流程是否继续写 workflow-state

# 收敛 workflow_config 所需的运行策略，避免配置构造函数继续堆散参。
@dataclass(frozen=True)
class WorkflowConfigRequest:
    """构造 workflow_config.json 时使用的运行策略对象。"""

    # provider 名称会被写进 workflow_config.json，供恢复流程复用。
    provider_name: str  # 配置文件里的 provider 名称

    # 只有 command provider 会消费这条命令覆盖字符串。
    provider_command: str | None  # command provider 的命令行覆盖

    # 这份配置声明当前 run 至少要达到哪一层 readiness。
    readiness: str  # workflow 目标验证深度

    # 恢复流程会继续沿用这里记录的最大尝试轮数。
    max_attempts: int  # workflow 最大尝试次数

    # 这个布尔值决定人工阻断点是否立即返回 blocked_human。
    stop_on_human: bool  # 人工确认点的停机策略

    # 这里记录是否允许后续阶段继续触发外部工具链验证。
    run_external: bool  # 外部验证链路开关

    # 注释语言会被 prompt 和 validator 共同读取。
    comment_language: str  # 持久化后的注释语言策略

    # HLS profile 约束会直接写入 workflow 配置供各阶段共享。
    hls_profile: dict[str, Any]  # 当前 run 生效的 HLS profile 字典

    # 若 spec 绑定了外部 codegen plan，这里保存其配置副本。
    external_codegen_plan: dict[str, Any] | None  # 可选外部 codegen plan

    # 模型超时值需要持久化，恢复后才能沿用同一边界。
    model_timeout_s: int  # 单次模型调用超时上限

# 统一收敛 _execute_workflow 需要持续改写的运行上下文。
@dataclass
class WorkflowExecutionContext:
    """workflow 主循环执行时复用的顶层上下文。"""

    # 所有 attempt 都以这个 run 根目录作为路径基准。
    run_dir: Path  # 当前 workflow 的 run 根目录

    # 结构化计划会被 prompt、验证和 gate 逻辑反复读取。
    plan: dict[str, Any]  # 当前 workflow 的分解计划字典

    # 跨阶段共享的配置会在恢复和验证阶段持续复用。
    config: dict[str, Any]  # 当前 workflow 的配置字典

    # 这个结果对象会在主循环中被反复改写并实时写盘。
    result: dict[str, Any]  # workflow_result 的内存态字典

    # 所有结果快照最终都持久化到这份 workflow_result.json。
    result_path: Path  # workflow 最终状态与 attempt 摘要写回的结果文件路径

    # 事件时间线统一追加到这条 trace.jsonl 文件。
    trace_path: Path  # 记录阶段事件时间线的 trace 文件路径

    # workflow-state.json 负责记录恢复时需要的阶段快照。
    state_path: Path  # 恢复流程依赖的 workflow 状态快照文件路径

    # 人工决策存在时会覆盖 codegen plan 或 comment-language 阻断点。
    decision: dict[str, Any] | None  # 可选人工决策载荷

    # 关闭状态写回时，只保留 result 和 trace 两类核心产物。
    state_updates: bool  # 是否持续刷新 workflow-state

# 收敛单个 attempt 在主循环中的中间状态，方便 helper 之间传递。
@dataclass
class WorkflowAttemptState:
    """单个 workflow attempt 的目录、记录与阶段输出视图。"""

    # 这个编号同时出现在目录名、trace 和 workflow_result 里。
    attempt_id: str  # 当前尝试的稳定标识

    # 每轮尝试都会在这里落盘 prompt、response 和 artifact。
    attempt_dir: Path  # 当前尝试的专属目录

    # workflow_result 中对应的 attempt 记录会通过它持续回写。
    attempt_record: dict[str, Any]  # 当前尝试的结果记录字典

    # codegen_plan 阶段产出的最新计划会暂存在这里供后续阶段复用。
    active_codegen_plan: dict[str, Any] | None  # 当前尝试生效的 codegen plan

    # 所有 stage helper 都通过这个上下文访问 provider、plan 和路径。
    stage_context: StageRunContext  # 本轮尝试共享的 stage 上下文

    # 已完成阶段的原始输出都会缓存在这个映射里供后续验证消费。
    stage_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)  # 各阶段完整输出映射

# 收敛恢复流程要重复读取的一组关键文件路径。
@dataclass(frozen=True)
class ResumeWorkflowFiles:
    """恢复 workflow run 时统一使用的关键文件路径集合。"""

    # 恢复流程会先重读这份 workflow 配置文件。
    path_workflow_config_file: Path  # 恢复时先重读的 workflow 配置文件路径

    # 这份结果文件决定恢复入口当前是 failed 还是 blocked_human。
    path_result_file: Path  # 判断恢复入口状态所依赖的结果文件路径

    # 恢复时必须沿用之前已经确认过的 plan.json。
    path_plan_file: Path  # 恢复后继续沿用的结构化计划文件路径

    # 新的恢复事件仍然要继续追加到同一条 trace 时间线。
    path_trace_file: Path  # 恢复阶段持续追加事件的 trace 文件路径

    # workflow-state 会在恢复推进过程中持续补写新快照。
    path_state_file: Path  # 恢复阶段持续补写状态快照的 state 文件路径

# 收敛新建 workflow 入口要反复写回的一组关键路径与配置载荷。
@dataclass(frozen=True)
class NewWorkflowEntryContext:
    """新建 workflow run 入口阶段复用的稳定上下文。"""

    # 新建 run 的根目录负责承接入口文件与后续 attempt 目录。
    path_run_dir: Path  # 当前新建 run 的根目录路径

    # trace.jsonl 会从入口阶段起持续记录整个 workflow 时间线。
    path_trace_file: Path  # 新建 run 入口阶段使用的 trace 文件路径

    # workflow-state.json 会从入口阶段起持续覆写快照。
    path_state_file: Path  # 新建 run 入口阶段使用的状态文件路径

    # workflow_result.json 会先写入初始失败态，再交给执行循环接管。
    path_result_file: Path  # 新建 run 入口阶段使用的结果文件路径

    # workflow_config.json 会在注释语言确认后落盘。
    path_workflow_config_file: Path  # 新建 run 入口阶段使用的配置文件路径

    # planning 阶段生成的结构化 workflow 计划要交给入口与主循环共用。
    dict_workflow_plan: dict[str, Any]  # 当前新建 run 的结构化 workflow 计划

    # 当前 run 生效的 workflow 配置会由入口阶段补写后继续沿用。
    dict_workflow_config: dict[str, Any]  # 当前新建 run 的 workflow 配置字典

# 收敛人工阻断 helper 需要的稳定路径与 provider 元数据。
@dataclass(frozen=True)
class HumanBlockContext:
    """blocked_human 写盘阶段需要复用的稳定上下文。"""

    # blocked_human 终态会先写回这份 workflow_result.json。
    result_path: Path  # workflow_result.json 输出路径

    # 人工补决策前的请求文件都会落在当前 attempt 目录中。
    attempt_dir: Path  # 当前 attempt 的工作目录

    # 若开启状态同步，还需要把阻断快照写到 workflow-state.json。
    state_path: Path  # blocked_human 需要补写的 workflow-state 文件路径

    # 人工阻断事件会继续追加到这条 trace 时间线。
    trace_path: Path  # blocked_human 事件要追加到的 trace 文件路径

    # provider 名称要写进请求文件，帮助用户判断下一步操作。
    provider_name: str  # 触发本次阻断的 provider 标识

    # 这个开关决定 blocked_human 是否同步到 workflow-state。
    state_updates: bool  # 控制人工阻断时是否同步写 state 文件

# 统一构造 workflow_result.json 的基础骨架，避免入口函数内联大段结果字典。
def _build_workflow_result_skeleton(
    workflow_name: str,
    status: str,
    *,
    path_comment_language_request: Path | None = None,
    path_run_dir: Path | None = None,
) -> dict[str, Any]:
    """
    生成 workflow_result.json 的基础骨架。

    参数:
        workflow_name: 当前 workflow 计划名称。
        status: 需要写入结果骨架的 workflow 状态值。
        path_comment_language_request: 可选的 comment-language 请求文件路径。
        path_run_dir: 计算 comment-language 请求相对路径时使用的 run 根目录。

    返回:
        可直接写入 workflow_result.json 的基础结果字典。

    异常:
        ValueError: 当传入 comment-language 请求文件却缺少 run 根目录时抛出。
    """

    # comment-language 请求只有在同时给出 run 根目录时才能计算安全相对路径。
    if path_comment_language_request is not None and path_run_dir is None:

        # 缺少 run 根目录时无法把请求文件安全写回结果骨架。
        raise ValueError(
            "> ERR: [Python] path_run_dir is required when a comment-language request path is provided.",
        )

    # 先构造所有 workflow 结果都会复用的基础字段。
    dict_result = {  # workflow_result.json 的基础骨架字典
        "version": 1,  # workflow 结果载荷版本号
        "name": workflow_name,  # 当前 workflow 的计划名称
        "target": "hls",  # 技能固定的 HLS-only 目标
        "status": status,  # 当前结果骨架对应的初始状态
        "plan_path": "plan.json",  # 计划文件的相对固定路径
        "workflow_config": "workflow_config.json",  # 配置文件的相对固定路径
        "trace_path": "trace.jsonl",  # trace 时间线文件的相对固定路径
        "attempts": [],  # 后续会逐轮追加的 attempt 结果列表
    }

    # 若当前结果需要携带 comment-language 请求文件，则补入安全相对路径。
    if path_comment_language_request is not None:

        # 把待用户补决策的请求文件路径写回结果骨架。
        dict_result["comment_language_request"] = safe_path(  # 人工语言请求文件的安全相对路径
            path_comment_language_request,  # 待写回结果文件的语言请求 JSON 路径
            path_run_dir,  # 计算相对路径时使用的 run 根目录
        )

    # 返回供入口函数继续写盘的结果骨架。
    return dict_result

# 统一解析恢复流程要回读和续写的一组关键文件路径。
def _resolve_resume_files(path_run_dir: Path) -> ResumeWorkflowFiles:
    """
    解析恢复 workflow run 需要使用的关键文件路径。

    参数:
        path_run_dir: 已完成边界校验的 workflow run 根目录。

    返回:
        包含 config/result/plan/trace/state 路径的只读路径集合。
    """

    # 恢复流程必须重新读取 workflow_config.json。
    path_workflow_config_file = require_workspace_path(  # 恢复时要重读的 workflow 配置文件路径
        path_run_dir / "workflow_config.json",  # 恢复阶段要回读的 workflow 配置文件
        purpose="workflow config",  # 供边界检查报错使用的用途标签
        must_exist=True,  # 恢复流程要求这份配置文件已经存在
    )

    # 恢复流程要先读取 workflow_result.json，判断是否停在人工阻断点。
    path_result_file = require_workspace_path(  # 恢复前一次写出的 workflow 结果文件路径
        path_run_dir / "workflow_result.json",  # 恢复入口当前状态对应的结果文件
        purpose="workflow result",  # 向路径治理层声明这里读取的是恢复入口结果文件
        must_exist=True,  # 恢复流程要求这份结果文件已经存在
    )

    # 恢复执行必须沿用上轮确认过的 plan.json。
    path_plan_file = require_workspace_path(  # 恢复时继续沿用的 plan.json 路径
        path_run_dir / "plan.json",  # 恢复后继续沿用的结构化计划文件
        purpose="workflow plan",  # 向路径治理层声明这里读取的是恢复计划文件
        must_exist=True,  # 恢复流程要求这份计划文件已经存在
    )

    # trace.jsonl 会继续追加新的阶段事件。
    path_trace_file = require_write_path(  # 恢复阶段继续追加事件的 trace.jsonl 路径
        path_run_dir / "trace.jsonl",  # 恢复阶段继续追加事件的 trace 文件
        purpose="workflow trace",  # 供写路径治理标识 trace 文件用途
    )

    # workflow-state.json 会持续写回新的阶段状态。
    path_state_file = require_write_path(  # 恢复阶段继续补写状态快照的 workflow-state 路径
        path_run_dir / "workflow-state.json",  # 恢复阶段继续补写状态快照的文件
        purpose="workflow state",  # 向写路径治理层声明这里续写的是状态快照文件
    )

    # 返回恢复流程统一复用的一组关键文件路径。
    return ResumeWorkflowFiles(
        path_workflow_config_file=path_workflow_config_file,
        path_result_file=path_result_file,
        path_plan_file=path_plan_file,
        path_trace_file=path_trace_file,
        path_state_file=path_state_file,
    )

# 统一启动或恢复 staged workflow，负责新 run 初始化与 resume 路由。
def run_workflow(workflow_request: WorkflowRunRequest) -> dict[str, Any]:
    """
    运行或恢复 staged HLS workflow。

    参数:
        workflow_request: 收敛新建/恢复参数后的 workflow 请求对象。
            shape/dtype/unit: 不涉及数组张量；该参数表示 staged workflow 的结构化入口请求。

    返回:
        workflow 的结构化结果字典。

    异常:
        WorkflowError: 当 target 非 hls，或新建运行缺少 spec_path/out_dir 时抛出。
    """

    # 该技能是 HLS-only，target 不允许偏离 hls。
    if workflow_request.target not in (None, "hls"):

        # 非 HLS target 会破坏整个 prompt/validator/toolchain 假设。
        raise WorkflowError(
            "> ERR: [Python] workflow target must stay within the HLS-only boundary.",
        )

    # 传入 resume_dir 时，当前调用应转入恢复流程。
    if workflow_request.resume_dir is not None:

        # 恢复流程复用同一套 comment-language 与 external 行为配置。
        return _resume_workflow(
            resume_request=ResumeWorkflowRequest(  # 恢复已有 run 时复用的策略请求对象
                resume_dir=workflow_request.resume_dir,  # 需要恢复的历史 run 目录
                decision_path=workflow_request.decision_path,  # 恢复入口额外补入的人工决策文件

                # 恢复调用继续沿用人工阻断与外部工具链的执行策略。
                stop_on_human=workflow_request.stop_on_human,  # 恢复后命中人工阻断时是否立即停下
                run_external=workflow_request.run_external,  # 恢复后是否继续放行外部工具链

                # 恢复调用显式覆写语言、超时和状态写回开关。
                comment_language=workflow_request.comment_language,  # 恢复入口显式请求的注释语言
                model_timeout_s=workflow_request.model_timeout_s,  # 恢复后沿用的单次模型调用超时
                state_updates=workflow_request.state_updates,  # 恢复流程是否继续同步 workflow-state
            ),
        )

    # 新建运行必须同时具备 spec 和输出目录。
    if workflow_request.spec_path is None or workflow_request.out_dir is None:

        # 缺少任一参数都会导致 run 目录与输入合同不完整。
        raise WorkflowError(
            "> ERR: [Python] new workflow runs require both spec_path and out_dir.",
        )

    # 新建 run 的准备和进入执行循环交给专用 helper，避免入口函数承载过多细节。
    return _run_new_workflow(workflow_request)

# 准备新建 workflow run 的计划、配置与入口文件，再交给执行循环消费。
def _run_new_workflow(workflow_request: WorkflowRunRequest) -> dict[str, Any]:
    """准备新建 workflow run 所需的计划、配置和入口文件。

    参数:
        workflow_request: 新建 workflow run 所需的结构化入口请求对象。

    返回:
        dict[str, Any]: 入口文件写回完成后交由执行循环返回的 workflow 结果字典。
    """

    # 把 spec 输入解析成后续 planning 必须依赖的实际文件路径。
    path_spec_file = require_workspace_path(workflow_request.spec_path, purpose="spec path", must_exist=True)  # 输入 spec 文件

    # 把输出目录解析成当前 workflow 全部工件共享的 run 根目录。
    path_run_dir = require_configured_output_path(workflow_request.out_dir, purpose="workflow output directory")  # 当前 run 根目录

    # 先确保 run 根目录存在，避免后续 trace/result/state 落盘失败。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 这个文件负责串起所有阶段事件，供后续人工回溯执行轨迹。
    path_trace_file = path_run_dir / "trace.jsonl"  # 当前 workflow 的 trace 文件路径

    # 这个文件保留当前推进状态，供中断恢复时继续从上一次位置接续。
    path_state_file = path_run_dir / "workflow-state.json"  # 当前 workflow 的状态文件路径

    # 这个文件保存顶层结果摘要，供调用方直接读取最终状态。
    path_result_file = path_run_dir / "workflow_result.json"  # 当前 workflow 的结果文件路径

    # 这个文件冻结本次 run 的 provider、语言和 readiness 等执行配置。
    path_workflow_config_file = path_run_dir / "workflow_config.json"  # 当前 workflow 的配置文件路径

    # 这个文件持久化 planning 产出的结构化计划，供 resume 时直接回读。
    path_plan_file = path_run_dir / "plan.json"  # 当前 workflow 的分解计划文件路径

    # 读取原始 spec，并在需要时补入显式确认信息。
    dict_raw_spec = read_spec(path_spec_file, target="hls")  # 从输入 spec 读取的原始计划载荷

    # 调用方如果已补齐 requirements 决策，就先把确认值并回原始 spec。
    if workflow_request.confirmation:

        # 把人工确认过的 requirements 默认值并回原始 spec。
        dict_raw_spec = apply_requirement_defaults(dict_raw_spec, **workflow_request.confirmation)  # 已叠加显式确认信息的原始 spec

    # 无论是否补默认值，都要先通过 requirement 确认校验。
    validate_requirement_confirmation(dict_raw_spec)

    # spec 相邻目录里如果存在外部 codegen 计划，这里先把它读入统一配置。
    dict_external_codegen_plan = _resolve_external_codegen_plan(dict_raw_spec, path_spec_file)  # spec 邻接的 codegen 计划

    # 如有外部证据文件，则在规划阶段一并读入供分解器参考。
    dict_evidence_payload = _read_json(workflow_request.evidence_path) if workflow_request.evidence_path else None  # 供 planning 阶段参考的可选证据载荷

    # planning 阶段把原始 spec 分解成可逐阶段执行的 workflow 计划。
    dict_workflow_plan = decompose_spec(dict_raw_spec, target="hls", evidence=dict_evidence_payload)  # stage 循环消费的分解计划

    # 把分解后的 workflow 计划写入 run 目录，便于后续 resume 复用。
    write_spec(path_plan_file, dict_workflow_plan)

    # 先把跨阶段执行策略收敛成请求对象，避免配置构造点承载过长参数列。
    workflow_config_request = WorkflowConfigRequest(  # 持久化 workflow_config 所需的运行策略对象
        provider_name=workflow_request.provider_name,  # 记录本轮要实例化的 provider 名称
        provider_command=workflow_request.provider_command,  # 保存 command provider 额外命令串
        readiness=workflow_request.readiness,  # 声明本轮至少要达到哪一层验证深度
        max_attempts=workflow_request.max_attempts,  # 限制自动重试最多还能跑多少轮
        stop_on_human=workflow_request.stop_on_human,  # 人工阻断点是否立即暂停
        run_external=workflow_request.run_external,  # 是否允许外部工具链验证
        comment_language=workflow_request.comment_language,  # prompt 与验证阶段共享的注释语言
        hls_profile=workflow_request.hls_profile or dict_workflow_plan.get("hls_profile") or {},  # 当前 run 生效的 HLS profile
        external_codegen_plan=dict_external_codegen_plan,  # spec 邻接解析到的外部 codegen plan
        model_timeout_s=workflow_request.model_timeout_s,  # 单次模型调用的秒级超时
    )

    # 再把请求对象折叠成后续所有 stage 共用的 workflow 配置字典。
    dict_workflow_config = _workflow_config(dict_workflow_plan, workflow_config_request)  # 后续所有 stage 共用的 workflow 配置字典

    # 先把入口阶段反复共用的路径与载荷收敛起来，减少 helper 散参。
    new_workflow_entry_context = NewWorkflowEntryContext(  # 新建 run 入口阶段复用的上下文对象
        path_run_dir=path_run_dir,  # 当前新建 run 的根目录
        path_trace_file=path_trace_file,  # 当前新建 run 的 trace 文件路径
        path_state_file=path_state_file,  # 当前新建 run 的状态文件路径
        path_result_file=path_result_file,  # 当前新建 run 的结果文件路径
        path_workflow_config_file=path_workflow_config_file,  # 当前新建 run 的配置文件路径

        # 这两份结构化载荷会贯穿入口阶段与后续执行循环。
        dict_workflow_plan=dict_workflow_plan,  # planning 阶段生成的 workflow 计划
        dict_workflow_config=dict_workflow_config,  # 当前 run 生效的 workflow 配置字典
    )

    # 把新建 run 的入口文件写回和执行循环接管放到单独 helper，保持准备阶段职责单一。
    return _enter_new_workflow_execution(
        workflow_request=workflow_request,  # 传入当前新建 run 的结构化入口请求对象
        entry_context=new_workflow_entry_context,  # 传入入口阶段共用的路径与配置上下文
    )

# 接管新建 workflow run 的入口写回、人工阻断分支和执行循环切换。
def _enter_new_workflow_execution(
    *,
    workflow_request: WorkflowRunRequest,
    entry_context: NewWorkflowEntryContext,
) -> dict[str, Any]:
    """写回新建 workflow run 的入口文件并切换到执行循环。

    参数:
        workflow_request: 新建 workflow run 的结构化入口请求对象。
        entry_context: 新建 run 入口阶段复用的路径与配置上下文。

    返回:
        dict[str, Any]: 人工阻断或执行循环返回的 workflow 结果字典。
    """

    # comment-language 需要尽早解析成显式值，避免后续阶段反复分支。
    str_resolved_comment_language = resolve_comment_language(  # 解析后的显式注释语言
        str(entry_context.dict_workflow_config.get("comment_language", "auto"))  # workflow 配置里的 comment-language 原始值
    )

    # 未解析出显式注释语言时，要改写结果并请求人工确认。
    if str_resolved_comment_language is None:

        # 未配置注释语言时，要向 run 目录写出人工确认请求。
        path_comment_language_request = entry_context.path_run_dir / "comment_language_request.json"  # comment-language 的人工请求文件路径

        # 把注释语言确认请求先落到 run 目录，供用户后续补决策。
        write_json(path_comment_language_request, comment_language_request())

        # 结果文件需要明确记录当前被人工阻断的原因和相关产物路径。
        dict_result_status = _build_workflow_result_skeleton(  # comment-language 阻断时写回的结果骨架
            entry_context.dict_workflow_plan["name"],  # 当前被阻断 workflow 的名称
            "blocked_human",  # 标记这次入口因为注释语言未决而暂停
            path_comment_language_request=path_comment_language_request,  # 待用户补决策的请求文件路径
            path_run_dir=entry_context.path_run_dir,  # 计算请求文件安全相对路径时使用的 run 根目录
        )

        # 先把当前 workflow 配置写回 run 目录。
        write_json(entry_context.path_workflow_config_file, entry_context.dict_workflow_config)

        # 再把人工阻断结果写回 result 文件。
        _write_result(entry_context.path_result_file, dict_result_status)

        # 同步记录这次 comment-language 请求事件到 trace。
        append_trace_event(
            entry_context.path_trace_file,
            {
                "event": "comment_language_request",
                "output": path_comment_language_request,
                "preferred_values": ["en", "zh"],
            },
        )

        # 最后把人工阻断状态同步到 workflow-state。
        _record_state(
            entry_context.path_state_file,
            "comment_language_request",
            {"output": path_comment_language_request},
            enabled=workflow_request.state_updates,
        )

        # 返回人工阻断状态，等待用户补充注释语言决策。
        return dict_result_status

    # 成功解析注释语言后，把显式值写回 workflow 配置。
    entry_context.dict_workflow_config["comment_language"] = str_resolved_comment_language  # 后续所有 stage 统一使用的显式注释语言

    # 把补全后的 workflow 配置持久化到 run 目录。
    write_json(entry_context.path_workflow_config_file, entry_context.dict_workflow_config)

    # 先生成新建 workflow 的初始结果骨架，默认状态从 failed 起步。
    dict_result_status = _build_workflow_result_skeleton(entry_context.dict_workflow_plan["name"], "failed")  # 新建 run 初始写回的失败态结果骨架

    # 先把新建运行的初始结果写回 result 文件。
    _write_result(entry_context.path_result_file, dict_result_status)

    # 再把本次新建运行的入口状态写入 workflow-state。
    _record_state(
        entry_context.path_state_file,
        "run_workflow",
        {
            "out_dir": entry_context.path_run_dir,
            "target": "hls",
            "name": entry_context.dict_workflow_plan["name"],
        },
        enabled=workflow_request.state_updates,
    )

    # 人工决策 JSON 在新建运行时属于可选输入。
    dict_human_decision = _read_json(workflow_request.decision_path) if workflow_request.decision_path else None  # workflow 新建运行的可选人工决策载荷

    # 把 result、trace、state 与人工决策收敛成统一执行上下文。
    workflow_execution_context = WorkflowExecutionContext(  # 新建 run 交给主循环消费的执行上下文
        run_dir=entry_context.path_run_dir,  # stage provider 复用的 run 根目录
        plan=entry_context.dict_workflow_plan,  # stage 循环消费的结构化计划
        config=entry_context.dict_workflow_config,  # stage 读取的已定稿 workflow 配置
        result=dict_result_status,  # 当前新建 run 的内存态结果字典快照

        # 这三类路径句柄会在执行循环中持续写回对应工件。
        result_path=entry_context.path_result_file,  # 主结果 JSON 的持续写回句柄
        trace_path=entry_context.path_trace_file,  # trace 事件流会持续追加到这个句柄
        state_path=entry_context.path_state_file,  # workflow-state 快照会覆盖写回到这个句柄

        # 人工决策与状态写回开关会继续影响后续 attempt 的推进方式。
        decision=dict_human_decision,  # 可选的人工作业决策会影响恢复分支
        state_updates=workflow_request.state_updates,  # 控制是否持续刷新 workflow-state
    )

    # 主循环从这里接手多轮 attempt、阻断分支和终态写回。
    return _execute_workflow(workflow_execution_context)

# 恢复既有 run，并把人工决策与注释语言请求重新并入统一执行循环。
def _resume_workflow(
    *,
    resume_request: ResumeWorkflowRequest,
) -> dict[str, Any]:
    """
    恢复已有 workflow run，并在需要时注入人工决策。

    参数:
        resume_request: 恢复已有 run 所需的全部显式策略对象。
            shape/dtype/unit: 不涉及数组张量；该参数表示恢复流程的路径与运行策略集合。

    返回:
        恢复执行后的 workflow 结果字典。

    异常:
        WorkflowError: 当 blocked_human 恢复缺少决策 JSON 时抛出。
    """

    # 解析待恢复 workflow 的 run 根目录。
    path_run_dir = require_workspace_path(  # 待恢复 workflow 的 run 根目录
        resume_request.resume_dir,  # 恢复入口明确指定的历史 run 目录
        purpose="workflow resume directory",  # 报错时用于说明当前校验的是恢复目录
        must_exist=True,  # 恢复流程只能继续一个已经存在的历史 run 目录
    )

    # 恢复流程需要统一回读 plan/result/config/trace/state 这些关键文件。
    resume_workflow_files_resume_snapshot_paths = _resolve_resume_files(path_run_dir)  # 恢复 run 持续复用的关键文件路径集合

    # 读取恢复前保存的 workflow 配置。
    dict_workflow_config = _read_json(  # 恢复前保存的 workflow 配置字典
        resume_workflow_files_resume_snapshot_paths.path_workflow_config_file  # 恢复前 workflow_config.json 路径
    )

    # 读取恢复前保存的 workflow 结果。
    dict_result_status = _read_json(  # 恢复前保存的 workflow 结果字典
        resume_workflow_files_resume_snapshot_paths.path_result_file  # 恢复前累计的 workflow 终态结果文件
    )

    # 读取恢复前保存的 workflow 计划。
    dict_workflow_plan = read_spec(  # 恢复前保存的 workflow 计划字典
        resume_workflow_files_resume_snapshot_paths.path_plan_file,  # 恢复前 plan 规范文件路径
        target="hls",  # 按 HLS 目标读取历史计划
    )

    # 旧五阶段配置或旧 Python reference 合同一旦进入恢复入口，必须直接阻断并要求新跑。
    _reject_legacy_python_stage_payloads(
        dict_workflow_config,
        dict_workflow_plan,
        dict_result_status,
    )

    # 读取恢复调用额外传入的人工决策。
    dict_human_decision = _read_json(resume_request.decision_path) if resume_request.decision_path else None  # 当前恢复调用提供的可选人工决策载荷

    # blocked_human 状态恢复时必须附带决策 JSON。
    if (
        dict_result_status.get("status") == "blocked_human"
        and dict_human_decision is None
    ):

        # 没有决策文件时无法推进人工阻断点之后的流程。
        raise WorkflowError(
            "> ERR: [Python] blocked_human resume requires a decision JSON file.",
        )

    # 提供人工决策时，需要把决策同步回 plan 和外部 codegen plan。
    if (
        dict_result_status.get("status") == "blocked_human"
        and dict_human_decision is not None
    ):

        # 先把人工决策写回 workflow plan。
        dict_workflow_plan = _apply_human_decision_to_plan(dict_workflow_plan, dict_human_decision)  # 已合并人工决策的 workflow 计划

        # 外部 codegen plan 为 dict 时，同样需要应用人工决策。
        if isinstance(dict_workflow_config.get("external_codegen_plan"), dict):

            # 保持 plan 与 external_codegen_plan 的人工决策视图一致。
            dict_workflow_config["external_codegen_plan"] = _apply_human_decision_to_codegen_plan(  # 恢复后继续沿用的外部 codegen 计划
                dict_workflow_config["external_codegen_plan"],  # 恢复前保存的外部 codegen 计划
                dict_human_decision,  # 本次恢复调用补入的人工决策
            )

        # 把更新后的 plan 写回 run 目录。
        write_spec(resume_workflow_files_resume_snapshot_paths.path_plan_file, dict_workflow_plan)

    # 覆写恢复后命中人工阻断时是否立刻暂停的执行策略。
    dict_workflow_config["stop_on_human"] = resume_request.stop_on_human  # 恢复后遇到人工阻断时是否立即停下

    # 覆写恢复阶段是否继续调用外部工具链的执行策略。
    dict_workflow_config["run_external"] = resume_request.run_external  # 恢复后是否继续放行外部工具链调用

    # 记录恢复调用最终请求的注释语言值。
    str_requested_comment_language = resume_request.comment_language or dict_workflow_config.get(  # 恢复入口显式请求或沿用的注释语言
        "comment_language",  # 历史 run 里已保存的注释语言键
        "auto",  # 缺省时回退到 auto 再交给解析器判断
    )

    # 把请求值解析成当前流程可执行的显式注释语言。
    str_resolved_comment_language = resolve_comment_language(str(str_requested_comment_language))  # 恢复后解析出的显式注释语言

    # 注释语言仍不明确时，需要重新生成请求文件并维持人工阻断。
    if str_resolved_comment_language is None:

        # 注释语言仍未确定时，需要继续维持人工阻断状态。
        path_comment_language_request = path_run_dir / "comment_language_request.json"  # 恢复流程的 comment-language 请求文件路径

        # 先把新的注释语言确认请求写回 run 目录。
        write_json(
            path_comment_language_request,
            comment_language_request(),
        )

        # 再把结果状态显式改回 blocked_human。
        dict_result_status["status"] = "blocked_human"  # 恢复后继续维持人工阻断状态

        # 把新的 comment-language 请求路径登记进结果字典。
        dict_result_status["comment_language_request"] = safe_path(  # 恢复阶段重新生成的注释语言请求路径
            path_comment_language_request,  # 当前恢复阶段重新写出的请求文件
            path_run_dir,  # 计算相对路径时依赖的 run 根目录
        )

        # 先写回更新后的 workflow 配置。
        write_json(
            resume_workflow_files_resume_snapshot_paths.path_workflow_config_file,
            dict_workflow_config,
        )

        # 再把更新后的阻断结果写回 result 文件。
        _write_result(path_result_file, dict_result_status)

        # 把新的 comment-language 请求事件追加进 trace。
        append_trace_event(
            resume_workflow_files_resume_snapshot_paths.path_trace_file,
            {
                "event": "comment_language_request",
                "output": path_comment_language_request,
                "preferred_values": ["en", "zh"],
            },
        )

        # 把恢复阶段的人工阻断状态同步到 workflow-state。
        _record_state(
            resume_workflow_files_resume_snapshot_paths.path_state_file,
            "comment_language_request",
            {"output": path_comment_language_request},
            enabled=resume_request.state_updates,
        )

        # 返回阻断结果，等待用户提供注释语言决策。
        return dict_result_status

    # 成功解析注释语言后，把显式值和超时策略写回 config。
    dict_workflow_config["comment_language"] = str_resolved_comment_language  # 恢复后所有 stage 都读取的显式注释语言

    # 恢复调用允许显式覆盖历史 run 里保存的模型超时策略。
    int_default_model_timeout_s = int(dict_workflow_config.get("model_timeout_s", 120))  # 历史 run 保存的模型超时回退值

    # 优先采用恢复请求显式给出的超时，否则沿用历史 run 的默认值。
    int_effective_model_timeout_s = resume_request.model_timeout_s or int_default_model_timeout_s  # 恢复后最终采用的模型超时秒数

    # 把恢复后最终采用的模型超时策略写回 workflow 配置。
    dict_workflow_config["model_timeout_s"] = int_effective_model_timeout_s  # 恢复流程写回的模型超时秒数

    # 把恢复后更新过的 workflow 配置写回 run 目录。
    write_json(
        resume_workflow_files_resume_snapshot_paths.path_workflow_config_file,
        dict_workflow_config,
    )

    # 把恢复入口状态同步到 workflow-state。
    _record_state(
        resume_workflow_files_resume_snapshot_paths.path_state_file,
        "resume_workflow",
        {"resume_dir": path_run_dir, "decision": resume_request.decision_path},
        enabled=resume_request.state_updates,
    )

    # 把恢复后读回的 plan/config/result 重新收敛成统一执行上下文。
    workflow_execution_context = WorkflowExecutionContext(  # 恢复 run 交给主循环消费的执行上下文
        run_dir=path_run_dir,  # 当前恢复 run 的根目录
        plan=dict_workflow_plan,  # 恢复后继续执行的结构化阶段计划
        config=dict_workflow_config,  # 恢复后继续共享的 workflow 配置
        result=dict_result_status,  # 恢复入口读回的结果字典快照

        # 统一登记当前恢复 run 持续写回的 result/trace/state 三类文件句柄。
        result_path=resume_workflow_files_resume_snapshot_paths.path_result_file,  # 恢复 run 延续使用的结果 JSON 句柄
        trace_path=resume_workflow_files_resume_snapshot_paths.path_trace_file,  # 恢复 run 延续使用的 trace.jsonl 句柄
        state_path=resume_workflow_files_resume_snapshot_paths.path_state_file,  # 恢复 run 继续覆写的状态快照句柄

        # 主循环继续共享恢复入口补入的人工决策与状态刷新开关。
        decision=dict_human_decision,  # 恢复 run 补入的人工作业决策
        state_updates=resume_request.state_updates,  # 恢复 run 是否继续刷新 workflow-state
    )  # 主循环统一消费的恢复 run 执行上下文

    # 主循环从这里接管恢复 run 的后续重试、阻断和终态收敛。
    return _execute_workflow(workflow_execution_context)

# 为单个 attempt 构造复用的 stage 运行上下文，减少主循环内联对象初始化。
def _build_attempt_stage_context(
    execution_context: WorkflowExecutionContext,
    model_provider_instance: ModelProvider,
    path_attempt_dir: Path,
    attempt_id: str,
) -> StageRunContext:
    """
    为单个 attempt 构造复用的 stage 运行上下文。

    参数:
        execution_context: workflow 主循环共享的顶层执行上下文。
        model_provider_instance: 当前 workflow 复用的模型 provider 实例。
        path_attempt_dir: 当前 attempt 的专属输出目录。
        attempt_id: 当前 attempt 的稳定标识。

    返回:
        供单个 attempt 所有 stage 复用的稳定 stage 上下文对象。
    """

    # 组装当前 attempt 所有 stage 共用的稳定上下文对象。
    shared_stage_context = StageRunContext(  # 单个 attempt 内所有 stage 复用的共享上下文
        execution_context.run_dir,  # 供 StageRunContext 派生路径时使用的 workflow 根目录
        path_attempt_dir,  # 当前 attempt 的专属工作目录
        attempt_id,  # 当前 attempt 的稳定标识

        # 这一组字段描述所有 stage 共读的计划、provider 与 workflow 配置。
        execution_context.plan,  # 当前 workflow 的结构化计划
        model_provider_instance,  # 当前 workflow 复用的模型 provider
        execution_context.config,  # 所有 stage 共读的 workflow 配置

        # 这一组字段承接人工决策与 trace/state 写回所需的稳定句柄。
        execution_context.decision,  # 供所有 stage 读取的人工决策对象
        execution_context.trace_path,  # 所有 stage 追加事件的 trace.jsonl 句柄
        execution_context.state_path,  # 所有 stage 共写的 workflow-state 句柄
        execution_context.state_updates,  # 控制是否继续落盘状态快照
    )  # 单个 attempt 复用的 StageRunContext 实例

    # 把单个 attempt 的共享 stage 上下文返回给上层循环。
    return shared_stage_context

# 初始化单个 workflow attempt 的目录、记录和共享上下文。
def _create_attempt_state(
    execution_context: WorkflowExecutionContext,
    model_provider_instance: ModelProvider,
    attempt_number: int,
) -> WorkflowAttemptState:
    """
    初始化单个 workflow attempt 的目录、记录和共享上下文。

    参数:
        execution_context: workflow 主循环共享的顶层执行上下文。
        model_provider_instance: 当前 workflow 复用的模型 provider 实例。
        attempt_number: 当前 attempt 的顺序编号。

    返回:
        已完成目录初始化并写回结果快照的 attempt 状态对象。
    """

    # 把当前顺序编号转换成目录和结果里复用的稳定 attempt 标识。
    str_attempt_id: str = f"attempt-{attempt_number:03d}"  # 当前尝试的目录与记录标识

    # 为本轮尝试准备专属输出目录。
    path_attempt_dir = require_write_path(  # 当前 attempt 的专属输出目录路径
        execution_context.run_dir / str_attempt_id,  # run 根目录下的本轮 attempt 子目录
        purpose="attempt directory",  # 供 require_write_path 生成更准确的报错语义
    )

    # 确保本轮尝试目录存在，便于后续 prompt/response/artifact 落盘。
    path_attempt_dir.mkdir(parents=True, exist_ok=True)

    # 初始化当前尝试的结果记录并追加到总结果中。
    dict_attempt_record = _new_attempt_record(str_attempt_id, model_provider_instance.name)  # 本轮 attempt 将持续回写的结果记录骨架

    # 把当前 attempt 记录追加到 workflow 结果列表。
    execution_context.result.setdefault("attempts", []).append(dict_attempt_record)  # 把本轮 attempt 记录纳入 workflow 总结果列表

    # 把追加尝试记录后的结果快照先写回 result 文件。
    _write_result(execution_context.result_path, execution_context.result)

    # 如配置中已经携带外部 codegen plan，则先作为本轮激活计划。
    raw_external_codegen_plan = execution_context.config.get("external_codegen_plan")  # workflow 配置里可选保存的外部 codegen plan

    # 返回当前 attempt 的全部共享状态。
    return WorkflowAttemptState(
        attempt_id=str_attempt_id,
        attempt_dir=path_attempt_dir,
        attempt_record=dict_attempt_record,

        # 把外部 codegen plan 与当前 attempt 的 stage 上下文一起封装进返回对象。
        active_codegen_plan=raw_external_codegen_plan if isinstance(raw_external_codegen_plan, dict) else None,
        stage_context=_build_attempt_stage_context(
            execution_context,
            model_provider_instance,
            path_attempt_dir,
            str_attempt_id,
        ),
    )

# 执行单个 attempt 的 stage 循环，并在异常或人工阻断时返回终态结果。
def _run_attempt_stages(
    execution_context: WorkflowExecutionContext,
    attempt_state: WorkflowAttemptState,
    list_stages: list[str],
) -> dict[str, Any] | None:
    """
    执行单个 attempt 的 stage 循环。

    参数:
        execution_context: workflow 主循环共享的顶层执行上下文。
        attempt_state: 当前 attempt 的共享状态对象。
        list_stages: 当前 workflow 要执行的阶段顺序列表。

    返回:
        若本轮 stage 循环已经产生终态结果则返回结果字典；否则返回 None。
    """

    # 进入本轮尝试的阶段执行闭环，并在异常时及时落盘。
    try:

        # 按阶段顺序推进生成流程，并累积各阶段输出。
        for str_stage in list_stages:

            # 读取当前阶段的上一阶段输出，供 prompt 渲染与合同衔接复用。
            dict_previous_stage_output = attempt_state.stage_outputs.get(  # 当前 stage 可复用的上一阶段输出
                _previous_stage(str_stage, list_stages),  # 与当前 stage 对接的上一阶段名称
            )

            # 执行当前阶段生成，并把前序阶段输出一并传入。
            dict_stage_output = _run_generation_stage(  # 当前 stage 的完整生成输出
                attempt_state.stage_context,  # 当前 attempt 共享的 stage 运行上下文
                str_stage,  # 当前准备执行生成的 stage 名称
                dict_previous_stage_output,  # 与本 stage 衔接的上一阶段输出
                attempt_state.active_codegen_plan,  # 当前轮次已经激活的 codegen plan
            )

            # 记录当前阶段的完整输出。
            attempt_state.stage_outputs[str_stage] = dict_stage_output  # 当前 stage 的完整输出对象

            # 记录当前阶段的摘要，便于 workflow result 直接消费。
            attempt_state.attempt_record.setdefault("stage_outputs", {}).update(
                {str_stage: dict_stage_output["summary"]},
            )

            # codegen_plan 阶段需要更新激活计划，并在未 ready 时转人工阻断。
            if str_stage == "codegen_plan":

                # 刷新当前尝试生效的 codegen plan。
                attempt_state.active_codegen_plan = dict_stage_output.get("codegen_plan")  # 当前尝试后续阶段要沿用的 codegen plan

                # 未 ready 或仍有开放问题时，需要交给人工确认。
                if attempt_state.active_codegen_plan and (
                    not attempt_state.active_codegen_plan.get("ready_for_generation", False)
                    or attempt_state.active_codegen_plan.get("open_questions")
                ):

                    # 立刻返回人工阻断结果，等待用户补齐决策。
                    return _block_current_attempt_for_human(
                        attempt_state.stage_context,
                        attempt_state.attempt_record,
                        execution_context.result,
                        execution_context.result_path,
                        attempt_state.active_codegen_plan,
                    )

            # 最终 HLS 阶段结束后，要把关键工件路径写入当前尝试记录。
            if str_stage == FINAL_STAGE:

                # 保存最终阶段 prompt/response/artifact 的关键路径摘要。
                attempt_state.attempt_record.update(
                    {
                        "prompt_path": dict_stage_output["summary"]["prompt_path"],
                        "response_path": dict_stage_output["summary"]["response_path"],
                        "artifact_dir": dict_stage_output["summary"]["artifact_dir"],
                        "stage": str_stage,
                    },
                )

                # 刷新 result 中的最后 attempt 标识。
                execution_context.result.update({"last_attempt_id": attempt_state.attempt_id})

                # 把最终阶段完成后的结果快照写回 result 文件。
                _write_result(execution_context.result_path, execution_context.result)

    # 当 provider 需要人工补响应时，返回 invalid_response 并保留当前 attempt 痕迹。
    except ManualResponseRequired as exc:

        # 当 provider 需要人工补响应时，把当前尝试标成 invalid_response。
        attempt_state.attempt_record.update(
            {
                "status": "invalid_response",
                "error": str(exc),
            },
        )

        # 把 invalid_response 状态同步到 workflow 结果。
        execution_context.result.update({"status": "invalid_response"})

        # 把 invalid_response 结果写回 result 文件。
        _write_result(execution_context.result_path, execution_context.result)

        # 返回当前 invalid_response 结果，等待人工补响应。
        return execution_context.result

    # 当提取、provider 或 spec 失败时，返回 failed/invalid_response 状态。
    except (ExtractionError, ModelProviderError, SpecError, ValueError) as exc:

        # 根据异常类型把当前尝试标记为 invalid_response 或 failed。
        str_attempt_status = "invalid_response" if isinstance(exc, ExtractionError) else "failed"  # 当前异常对应的 attempt 状态

        # 把异常状态与错误信息写回当前 attempt。
        attempt_state.attempt_record.update(
            {
                "status": str_attempt_status,
                "error": str(exc),
            },
        )

        # 把失败状态同步到 workflow 结果。
        execution_context.result.update({"status": str_attempt_status})

        # 把失败结果写回 result 文件。
        _write_result(execution_context.result_path, execution_context.result)

        # 返回当前失败结果，停止继续尝试。
        return execution_context.result

    # stage 循环未产生终态时，交给后续验证与 gate 流程继续处理。
    return None

# 写入最终验证 JSON、workflow-state 和 trace 事件，供后续 gate 复用。
def _record_attempt_validation_artifacts(
    execution_context: WorkflowExecutionContext,
    attempt_state: WorkflowAttemptState,
    dict_final_output: dict[str, Any],
    validation_report: Any,
) -> Path:
    """
    写入单个 attempt 的最终验证产物，并返回 validation.json 路径。

    参数:
        execution_context: workflow 主循环共享的顶层执行上下文。
        attempt_state: 当前 attempt 的共享状态对象。
        dict_final_output: 最终 HLS stage 的完整输出字典。
        validation_report: 当前 attempt 的最终验证报告对象。

    返回:
        当前 attempt validation.json 的输出路径。
    """

    # 约定最终验证 JSON 的落盘位置。
    path_validation_json = attempt_state.attempt_dir / "validation.json"  # 最终验证结果 JSON 路径

    # 将最终验证详情写入专属 JSON 文件。
    write_json(path_validation_json, validation_report.to_dict())

    # 把验证结果路径写回 attempt 记录。
    attempt_state.attempt_record.update(
        {"validation_json": safe_path(path_validation_json)},
    )

    # 准备 workflow-state 的验证阶段快照。
    dict_validate_state = _build_validate_state(  # workflow-state 中 validate 事件对应的状态快照
        dict_final_output["artifact_dir"],  # 交给最终验证扫描的 HLS 工件目录
        path_validation_json,  # 当前 attempt 写出的 validation.json 路径
        execution_context.config.get("readiness"),  # 当前 workflow 要求达到的验证深度
        validation_report.ok(),  # 最终验证是否整体通过
    )

    # 记录最终验证状态到 workflow state。
    _record_state(
        execution_context.state_path,  # validate 阶段要同步更新的 workflow-state.json 路径
        "validate",
        dict_validate_state,
        enabled=execution_context.state_updates,
    )

    # 准备最终验证 trace 事件，便于后续定位工具链或语义阻断。
    dict_validation_trace_event = _build_validation_trace_event(  # trace.jsonl 中 validate 事件的结构化载荷
        attempt_state.attempt_id,  # 当前写入验证轨迹的 attempt 标识
        dict_final_output["artifact_dir"],  # validate trace 里引用的最终 HLS 工件目录
        execution_context.config.get("readiness"),  # validate trace 里记录的目标 readiness 层级
        validation_report,  # 本轮最终验证报告对象
        attempt_state.stage_context.provider.name,  # 执行本轮生成的 provider 名称
    )

    # 记录最终验证 trace 事件。
    append_trace_event(execution_context.trace_path, dict_validation_trace_event)

    # 返回 validation.json 路径供后续状态写回复用。
    return path_validation_json

# 本地工具链缺失时写出远端求助材料，并把当前 workflow 阻断为 blocked_toolchain。
def _handle_blocked_toolchain_attempt(
    execution_context: WorkflowExecutionContext,
    attempt_state: WorkflowAttemptState,
    validation_report: Any,
    path_validation_json: Path,
) -> dict[str, Any] | None:
    """
    处理单个 attempt 的 blocked_toolchain 分支。

    参数:
        execution_context: workflow 主循环共享的顶层执行上下文。
        attempt_state: 当前 attempt 的共享状态对象。
        validation_report: 当前 attempt 的最终验证报告对象。
        path_validation_json: 当前 attempt 的 validation.json 路径。

    返回:
        若命中 blocked_toolchain 则返回终态结果字典；否则返回 None。
    """

    # 本地工具链未阻断时，继续走后续 gate 聚合流程。
    if not _blocked_toolchain(validation_report):

        # 当前验证报告未命中工具链阻断条件，主流程继续后续 gate 聚合。
        return None

    # 生成远端工具链协助请求文件，供用户切换 erie-remote-ssh 流程。
    path_remote_toolchain_request = _write_remote_toolchain_request(  # 远端工具链补完请求文件路径
        attempt_state.attempt_dir,  # 当前 attempt 的专属输出目录
        attempt_state.attempt_id,  # 当前需要远端补完的 attempt 标识
        execution_context.config,  # 远端协助需要读取的 workflow 配置
        validation_report,  # 触发工具链阻断的最终验证报告
    )

    # 回写 attempt 级别的阻断状态与摘要。
    attempt_state.attempt_record.update(
        {
            "status": "blocked_toolchain",
            "validation_summary": validation_report.format(),
            "remote_toolchain_request": safe_path(
                path_remote_toolchain_request,
                execution_context.run_dir,
            ),
        },
    )

    # 回写 workflow 级别的阻断状态与请求路径。
    execution_context.result.update(
        {
            "status": "blocked_toolchain",
            "last_attempt_id": attempt_state.attempt_id,
            "remote_toolchain_request": safe_path(
                path_remote_toolchain_request,
                execution_context.run_dir,
            ),
        },
    )

    # 把阻断结果写回 result 文件。
    _write_result(execution_context.result_path, execution_context.result)

    # 准备 blocked_toolchain 的 state 快照。
    dict_blocked_toolchain_state = _build_workflow_attempt_state(  # blocked_toolchain attempt 的状态快照
        attempt_state.attempt_id,  # 当前被工具链阻断的 attempt 标识
        "blocked_toolchain",  # 当前 attempt 在 workflow-state 中登记的终态
        path_validation_json,  # 当前 attempt 的 validation.json 路径
        path_remote_toolchain_request,  # 写给用户的远端工具链求助文件
    )

    # 记录 blocked_toolchain 的 workflow attempt 状态。
    _record_state(
        execution_context.state_path,
        "workflow_attempt",
        dict_blocked_toolchain_state,
        enabled=execution_context.state_updates,
    )

    # 准备 remote toolchain 请求事件，便于 trace 追踪。
    dict_remote_toolchain_event = _build_remote_toolchain_event(  # remote toolchain 请求的 trace 事件载荷
        attempt_state.attempt_id,  # 当前请求远端协助的 attempt 标识
        path_remote_toolchain_request,  # 当前 attempt 写出的远端工具链求助文件
    )

    # 记录 remote toolchain 请求事件。
    append_trace_event(execution_context.trace_path, dict_remote_toolchain_event)

    # 返回当前 blocked_toolchain 结果，等待外部工具链补完。
    return execution_context.result

# 把最终 stage 的合同路径统一写回 attempt 记录。
def _record_attempt_contract_paths(
    attempt_state: WorkflowAttemptState,
    dict_final_output: dict[str, Any],
) -> None:
    """
    把最终 stage 产物路径统一写回 attempt 记录。

    参数:
        attempt_state: 当前 attempt 的共享状态对象。
        dict_final_output: 最终 HLS stage 的完整输出字典。
    返回:
        None。结果直接写回 attempt_state.attempt_record。
    """

    # 只保留最终 HLS stage 产出的合同路径。
    attempt_state.attempt_record.update(
        {"contract_paths": dict(dict_final_output["contract_paths"])},
    )

# 把当前 attempt 标记为 passed，并同步写回 workflow 顶层状态。
def _mark_attempt_passed(
    execution_context: WorkflowExecutionContext,
    attempt_state: WorkflowAttemptState,
    path_validation_json: Path,
) -> dict[str, Any]:
    """
    把当前 attempt 标记为 passed，并同步写回 workflow 顶层状态。

    参数:
        execution_context: workflow 主循环共享的顶层执行上下文。
        attempt_state: 当前 attempt 的共享状态对象。
        path_validation_json: 当前 attempt 的 validation.json 路径。

    返回:
        当前 workflow 的 passed 结果字典。
    """

    # 回写 attempt 与 result 的 passed 状态。
    attempt_state.attempt_record.update({"status": "passed"})

    # 把 workflow 顶层状态刷新为 passed 并记录最后一次成功 attempt。
    execution_context.result.update(
        {
            "status": "passed",  # workflow 顶层状态切换为最终通过
            "last_attempt_id": attempt_state.attempt_id,  # 记录最后一次成功通过的 attempt 标识
        },
    )

    # 把通过结果写回 workflow result 文件。
    _write_result(execution_context.result_path, execution_context.result)

    # 为 workflow-state 生成 passed attempt 的最终快照。
    dict_passed_state = _build_workflow_attempt_state(  # passed attempt 写入 workflow-state 的最终快照
        attempt_state.attempt_id,  # 当前成功通过的 attempt 标识
        "passed",  # workflow-state 中登记的通过终态
        path_validation_json,  # 供恢复和审阅复用的验证报告路径
    )

    # 记录 passed attempt 的 state 快照。
    _record_state(
        execution_context.state_path,
        "workflow_attempt",
        dict_passed_state,
        enabled=execution_context.state_updates,
    )

    # 返回当前 passed 结果，结束 workflow。
    return execution_context.result

# 执行单个 attempt 的最终验证与 toolchain 阻断判定。
def _finalize_attempt(
    execution_context: WorkflowExecutionContext,
    attempt_state: WorkflowAttemptState,
) -> dict[str, Any] | None:
    """
    执行单个 attempt 的最终验证与 toolchain 阻断判定。

    参数:
        execution_context: workflow 主循环共享的顶层执行上下文。
        attempt_state: 当前 attempt 的共享状态对象。

    返回:
        若本轮 attempt 已经得出终态则返回结果字典；否则返回 None 进入下一轮尝试。
    """

    # 读取最终 HLS 阶段输出，供最终验证消费。
    dict_final_output = attempt_state.stage_outputs[FINAL_STAGE]  # 最终 HLS stage 输出字典

    # 基于最终 HLS 工件执行综合验证。
    validation_report = _run_final_validation(  # 最终 HLS 工件验证报告
        execution_context.plan,  # 驱动最终 HLS 验证的结构化设计计划
        dict_final_output["artifact_dir"],  # 最终 HLS 工件目录
        execution_context.config,  # 最终验证阶段共享的 workflow 配置
    )

    # 把 validation.json、workflow-state 与 trace 事件一次性写齐，避免终态收敛时再分散补写。
    path_validation_json = _record_attempt_validation_artifacts(  # 后续 blocked_toolchain / passed / failed 分支都会复用的 validation.json 路径
        execution_context,  # 负责一次性写齐 validation.json、state 和 trace 的执行上下文
        attempt_state,  # 需要写回验证工件与状态的 attempt 对象
        dict_final_output,  # 最终 HLS stage 的输出字典
        validation_report,  # 最终验证阶段生成的验证报告
    )

    # 本地工具链缺失时，生成 remote 请求并阻断当前 workflow。
    dict_blocked_toolchain_result = _handle_blocked_toolchain_attempt(  # 工具链阻断时直接返回的 workflow 终态
        execution_context,  # 负责在工具链阻断时回写 workflow 终态的执行上下文
        attempt_state,  # 需要回写工具链阻断终态的 attempt 对象
        validation_report,  # 用于判断工具链是否阻断的验证报告
        path_validation_json,  # 已落盘的 validation.json 路径
    )

    # 命中 blocked_toolchain 后，不再继续收敛当前 attempt。
    if dict_blocked_toolchain_result is not None:

        # blocked_toolchain 已经给出 workflow 终态，不再执行后续逻辑。
        return dict_blocked_toolchain_result

    # 把最终 stage 产物路径统一写回 attempt 记录。
    _record_attempt_contract_paths(
        attempt_state,
        dict_final_output,
    )

    # 最终验证通过时，把当前 attempt 记为 passed。
    if validation_report.ok():

        # 当前 attempt 已通过验证与 gate，主循环在这里直接结束为 passed。
        return _mark_attempt_passed(execution_context, attempt_state, path_validation_json)

    # 验证或 gate 未通过时，把当前 attempt 标记为 failed。
    attempt_state.attempt_record.update(
        {
            "status": "failed",
            "validation_summary": validation_report.format(),
        },
    )

    # 把 failed 结果写回 result 文件，允许 while 继续下一轮尝试。
    _write_result(execution_context.result_path, execution_context.result)

    # 当前 attempt 失败但 workflow 仍可继续下一轮尝试。
    return None

# 执行统一的 staged workflow 循环，负责多轮生成、验证和阻断分支收敛。
def _execute_workflow(execution_context: WorkflowExecutionContext) -> dict[str, Any]:
    """
    执行 workflow 的统一尝试循环。

    参数:
        execution_context: 主循环共享的 workflow 顶层执行上下文。

    返回:
        完成、阻断或达到尝试上限后的 workflow 结果字典。
    """

    # 构造当前 workflow 要使用的模型 provider。
    model_provider_instance: ModelProvider = build_model_provider(  # 当前 workflow 复用的模型 provider 实例
        str(execution_context.config["provider"]["name"]),  # workflow 配置中声明的 provider 名称
        command=execution_context.config["provider"].get("command"),  # command provider 需要附带的命令串
        timeout_s=int(execution_context.config.get("model_timeout_s", 120)),  # provider 单次生成调用允许占用的秒级超时
        config=execution_context.config,  # provider 初始化还要读取的 workflow 配置
    )

    # 解析本轮 workflow 要执行的阶段序列。
    list_stages = [
        str(item) for item in execution_context.config.get("stages", []) or DEFAULT_STAGES  # workflow 配置里启用的阶段名
    ]  # 当前 workflow 的阶段列表

    # 旧 python stage 不允许继续进入当前 HLS-only workflow 主循环。
    _reject_legacy_python_stage_names(list_stages)

    # 解析本轮 workflow 允许的最大尝试次数。
    int_max_attempts = int(execution_context.config.get("max_attempts", 3))  # workflow 的最大尝试轮数

    # 在达到最大尝试次数之前，持续执行生成和验证闭环。
    while len(execution_context.result.get("attempts", [])) < int_max_attempts:

        # 先计算当前 attempt 的顺序编号。
        int_attempt_number = len(execution_context.result.get("attempts", [])) + 1  # 当前尝试的顺序编号

        # 初始化当前 attempt 的目录、记录和共享上下文。
        workflow_attempt_state_attempt_cycle_state = _create_attempt_state(  # 当前 while 轮次对应的 attempt 共享状态
            execution_context,  # 当前轮次创建 attempt 时要读取的 workflow 执行上下文
            model_provider_instance,  # 本次 while 轮次沿用的模型 provider
            int_attempt_number,  # 当前轮次要落盘的 attempt 顺序编号
        )

        # 先执行本轮 attempt 的 stage 循环。
        dict_stage_result = _run_attempt_stages(  # 本轮 attempt 的阶段循环终态结果
            execution_context,  # 负责回写本轮 stage 结果的 workflow 执行上下文
            workflow_attempt_state_attempt_cycle_state,  # 当前 while 轮次对应的 attempt 状态
            list_stages,  # 当前 workflow 允许执行的阶段顺序列表
        )

        # stage 循环若已产生终态结果，则立刻返回。
        if dict_stage_result is not None:

            # stage 循环已经返回 workflow 终态，主循环无需继续下一轮尝试。
            return dict_stage_result

        # 再执行最终验证、gate 聚合和阻断判断。
        dict_final_result = _finalize_attempt(execution_context, workflow_attempt_state_attempt_cycle_state)  # 当前 attempt 最终验证后的终态结果

        # 一旦验证阶段得到终态结果，就结束主循环。
        if dict_final_result is not None:

            # 最终验证已经给出 workflow 终态，主循环到此结束。
            return dict_final_result

    # 达到最大尝试次数后仍未收敛时，写入 max_attempts 状态。
    execution_context.result.update({"status": "max_attempts"})

    # 把尝试耗尽后的 max_attempts 结论持久化到 workflow_result.json。
    _write_result(execution_context.result_path, execution_context.result)

    # 返回达到尝试上限后的 workflow 结果。
    return execution_context.result

# 统一封装 stage 执行时的稳定上下文，减少私有 helper 的散参传递。
@dataclass(frozen=True)
class StageRunContext:
    """单个 workflow stage 执行时复用的稳定上下文。"""

    # workflow 根目录，用于 provider 上下文和远端请求引用。
    run_dir: Path  # 当前 run 的根目录路径

    # 当前 attempt 的输出目录，承接各阶段工件与中间文件。
    attempt_dir: Path  # 当前 attempt 的工作目录路径

    # 当前 attempt 的稳定标识，用于 trace 与 result 关联。
    attempt_id: str  # 当前 attempt 的唯一标识字符串

    # 本轮 workflow 使用的分解计划。
    plan: dict[str, Any]  # 当前 attempt 对应的结构化计划

    # 本轮 workflow 绑定的模型 provider。
    provider: Any  # stage 调用共享的 provider 实例

    # 本轮 workflow 的运行配置。
    config: dict[str, Any]  # stage 读取的 workflow 配置字典

    # 可选的人类决策载荷，用于 prompt 合并与 codegen_plan 收敛。
    decision: dict[str, Any] | None  # 当前 run 读取到的人类确认决策字典

    # workflow 全局 trace.jsonl 的固定输出位置。
    trace_path: Path  # 各阶段与验证事件统一追加到的 trace 文件路径

    # workflow-state.json 的固定持久化位置。
    state_path: Path  # 各阶段状态快照统一写入的状态文件路径

    # 控制当前 run 是否继续刷新 workflow-state.json。
    state_updates: bool  # 关闭时只保留 result/trace，不再追加状态快照

# 统一描述单个 stage 的目录与 manifest 视图，避免主流程散落路径细节。
@dataclass(frozen=True)
class StageWorkspace:
    """单个 stage 的目录、artifact 目录与 manifest 绑定视图。"""

    # 当前 stage 的逻辑名称。
    str_stage: str  # stage 的标识字符串

    # 这个目录负责承接 stage 内 prompt、response、state 等所有中间文件。
    path_stage_dir: Path  # 当前 stage 的工作目录根路径

    # 当前 stage 的 artifact 输出目录。
    path_artifact_dir: Path  # 解包后工件实际落盘的 artifact 目录

    # 当前 stage 允许抽取的 manifest。
    dict_manifest: dict[str, Any]  # stage 的提取合同字典

# 为单个 stage 准备目录与 manifest，避免主流程重复拼接路径和 mkdir。
def _prepare_stage_workspace(
    *,
    stage_context: StageRunContext,
    str_stage: str,
) -> StageWorkspace:
    """
    创建并返回单个 stage 的目录与 manifest 视图。

    参数:
        stage_context: 当前 attempt 复用的稳定 stage 上下文。
        str_stage: 当前准备执行的 stage 名称。

    返回:
        包含 stage 目录、artifact 目录与 manifest 的工作区视图。
    """

    # 计算当前 stage 的目录路径。
    path_stage_dir = stage_context.attempt_dir / str_stage  # 本 stage 独立目录，保存 prompt、response 与阶段状态

    # 计算当前 stage 的 artifact 输出目录。
    path_artifact_dir = path_stage_dir / "artifacts"  # 当前 stage 解包工件的实际落盘目录

    # 创建 stage 工作目录，承接 prompt/response 等中间产物。
    path_stage_dir.mkdir(parents=True, exist_ok=True)

    # 创建 artifact 目录，承接抽取得到的实际工件。
    path_artifact_dir.mkdir(parents=True, exist_ok=True)

    # 解析当前 stage 允许抽取的 manifest。
    dict_manifest = _stage_manifest(stage_context.plan, str_stage)  # 当前 stage 的提取 manifest

    # 返回统一的 stage 工作区视图。
    return StageWorkspace(
        str_stage=str_stage,
        path_stage_dir=path_stage_dir,
        path_artifact_dir=path_artifact_dir,
        dict_manifest=dict_manifest,
    )

# 构造统一的 stage 输出骨架，避免不同阶段重复拼 summary 与 contract_paths。
def _new_stage_output(
    *,
    stage_workspace: StageWorkspace,
    path_prompt: Path,
    path_response: Path,
    dict_summary_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    生成单个 stage 的基础输出字典。

    参数:
        stage_workspace: 当前 stage 的目录与 manifest 视图。
        path_prompt: 当前 stage prompt 的落盘路径。
        path_response: 当前 stage response 的落盘路径。
        dict_summary_extra: 需要并入 summary 的可选额外字段。

    返回:
        包含 manifest、contract_paths 与 summary 的 stage 输出骨架。
    """

    # 先准备所有 stage 共享的 summary 路径字段。
    dict_summary = {  # stage 级 summary 默认携带的路径字段
        "prompt_path": safe_path(path_prompt),  # prompt Markdown 的相对安全路径
        "response_path": safe_path(path_response),  # 回放模型原始回复所用的安全路径
        "artifact_dir": safe_path(stage_workspace.path_artifact_dir),  # stage 工件目录的相对安全路径
    }

    # 追加调用方补充的 summary 字段。
    if dict_summary_extra:

        # 把额外 summary 字段并入基础摘要。
        dict_summary.update(dict_summary_extra)

    # 返回统一结构的 stage 输出骨架。
    return {
        "stage": stage_workspace.str_stage,
        "prompt_path": path_prompt,
        "response_path": path_response,
        "artifact_dir": stage_workspace.path_artifact_dir,
        "manifest": stage_workspace.dict_manifest,
        "contract_paths": {},
        "summary": dict_summary,
    }

# 为 HLS stage 输出补齐接口合同产物。
def _populate_hls_stage_output(
    *,
    stage_workspace: StageWorkspace,
    dict_output: dict[str, Any],
) -> None:
    """
    补全 HLS stage 的接口合同与路径产物。

    参数:
        stage_workspace: 当前 HLS stage 的目录与 manifest 视图。
        dict_output: 待回填接口合同与路径信息的 stage 输出字典。

    返回:
        None。结果直接写回 dict_output。
    """

    # 基于 HLS 工件目录生成接口合同载荷。
    dict_interface_contract = audit_interface("hls", stage_workspace.path_artifact_dir)  # 从 HLS 工件目录审计得到的接口合同载荷

    # 为 HLS 接口合同约定固定的 JSON 落盘位置。
    path_interface_contract = stage_workspace.path_stage_dir / "hls_interface.json"  # stage 目录内固定保留的 HLS 接口合同 JSON 路径

    # 把 HLS 接口合同写入 stage 工作目录。
    write_json(path_interface_contract, dict_interface_contract)

    # 在 stage 输出中回填接口合同内容。
    dict_output.update({"interface_contract": dict_interface_contract})

    # 在 stage 输出中登记接口合同文件路径。
    dict_output["contract_paths"].update(
        {"hls_interface": safe_path(path_interface_contract)}
    )

# 构造 provider.generate 需要的上下文对象，避免主流程重复拼接固定字段。
def _build_gen_context(
    stage_context: StageRunContext,
    str_stage: str,
    path_prompt: Path, path_response: Path,
    dict_stage_manifest: dict[str, Any],
    dict_vector_contract: dict[str, Any] | None, str_comment_language: str,
) -> GenerationContext:
    """
    生成单个 stage 调用 provider.generate 所需的上下文对象。

    参数:
        stage_context: 当前 attempt 复用的 stage 执行上下文。
        str_stage: 当前执行的 stage 名称。
        path_prompt: 当前 stage prompt 的落盘路径。
        path_response: 当前 stage response 的落盘路径。
        dict_stage_manifest: 当前 stage 允许抽取的 manifest。
        dict_vector_contract: 可选的上游向量合同。
        str_comment_language: 当前 stage 请求使用的注释语言。

    返回:
        可直接传给 provider.generate 的 GenerationContext。
    """

    # 返回 provider.generate 需要的完整上下文对象。
    return GenerationContext(
        stage_context.attempt_id, str_stage, path_prompt, path_response,
        stage_context.run_dir, stage_context.attempt_dir,
        stage_context.plan, dict_stage_manifest, stage_context.config,
        vector_contract=dict_vector_contract,
        comment_language=str_comment_language,
    )

# 渲染普通 stage 的 prompt，统一 target/codegen-plan/comment-language 等固定参数。
def _render_stage_prompt(
    stage_context: StageRunContext,
    str_stage: str,
    dict_previous_stage: dict[str, Any] | None,
    dict_vector_contract: dict[str, Any] | None,
    dict_active_codegen_plan: dict[str, Any] | None,
    str_comment_language: str,
) -> str:
    """
    生成当前普通 stage 交给 provider 的 prompt 文本。

    参数:
        stage_context: 当前 attempt 复用的 stage 执行上下文。
        str_stage: 当前执行的 stage 名称。
        dict_previous_stage: 供 prompt 读取上游 manifest 与 artifact 目录的阶段输出。
        dict_vector_contract: 可选的上游向量合同。
        dict_active_codegen_plan: 可选的当前生效 codegen plan。
        str_comment_language: 当前 stage 请求使用的注释语言。

    返回:
        可直接写入 prompt markdown 的完整文本。
    """

    # 从上一个 stage 输出里读取 prompt 需要的 manifest 上下文。
    dict_context_manifest = dict_previous_stage.get("manifest") if dict_previous_stage else None  # prompt 使用的上游 manifest

    # 从上一个 stage 输出里读取 prompt 需要的 artifact 目录。
    path_context_dir = dict_previous_stage.get("artifact_dir") if dict_previous_stage else None  # prompt 使用的上游 artifact 目录

    # 返回当前普通 stage 使用的完整 prompt 文本。
    return render_prompt(
        stage_context.plan,
        target="hls",
        stage=str_stage,
        context_manifest=dict_context_manifest,
        context_dir=path_context_dir,
        evidence=None,
        memory=None,
        comment_language=str_comment_language,
        vector_contract=dict_vector_contract,
        codegen_plan=dict_active_codegen_plan,
        budget="normal",
        hls_profile=stage_context.config.get("hls_profile") or {},
        decision=stage_context.decision,
    )

# 构造 prompt trace 事件，统一普通 stage 写入 trace.jsonl 的字段格式。
def _build_stage_prompt_trace_event(
    stage_context: StageRunContext,
    str_stage: str,
    path_prompt: Path,
) -> dict[str, Any]:
    """
    生成普通 stage 的 prompt trace 事件字典。

    参数:
        stage_context: 当前 attempt 复用的 stage 执行上下文。
        str_stage: 当前执行的 stage 名称。
        path_prompt: prompt markdown 的落盘路径。

    返回:
        可直接追加到 trace.jsonl 的 prompt 事件字典。
    """

    # 返回普通 stage 的 prompt trace 事件载荷。
    return {
        "event": "prompt",
        "attempt_id": stage_context.attempt_id,
        "target": "hls",
        "stage": str_stage,
        "spec": spec_summary(stage_context.plan),
        "output": path_prompt,
        "provider": stage_context.provider.name,
    }

# 构造 extract trace 事件，统一记录 response 与 artifact 目录的映射。
def _build_stage_extract_trace_event(
    stage_context: StageRunContext,
    path_response: Path,
    path_artifact_dir: Path,
    list_written_paths: list[Path],
) -> dict[str, Any]:
    """
    生成普通 stage 的 extract trace 事件字典。

    参数:
        stage_context: 当前 attempt 复用的 stage 执行上下文。
        path_response: response markdown 的落盘路径。
        path_artifact_dir: 当前 stage 的 artifact 输出目录。
        list_written_paths: 当前 stage 实际抽取出的工件路径列表。

    返回:
        可直接追加到 trace.jsonl 的 extract 事件字典。
    """

    # 返回记录 response 与 artifacts 映射关系的 trace 事件载荷。
    return {
        "event": "extract",
        "attempt_id": stage_context.attempt_id,
        "response": path_response,
        "out_dir": path_artifact_dir,
        "written_files": [safe_path(path_item) for path_item in list_written_paths],
    }

# 构造内部 stage 的 prompt trace 事件，标记该阶段来自内部合成流程。
def _build_internal_stage_prompt_trace_event(
    *,
    stage_context: StageRunContext,
    stage_workspace: StageWorkspace,
    path_prompt: Path,
) -> dict[str, Any]:
    """
    生成内部 stage 的 prompt trace 事件字典。

    参数:
        stage_context: 当前 attempt 复用的 stage 执行上下文。
        stage_workspace: 当前内部 stage 的目录与 manifest 视图。
        path_prompt: prompt markdown 的落盘路径。

    返回:
        可直接追加到 trace.jsonl 的内部 prompt 事件字典。
    """

    # 返回内部 stage 的 prompt trace 事件载荷。
    return {
        "event": "prompt",
        "attempt_id": stage_context.attempt_id,
        "target": "hls",
        "stage": stage_workspace.str_stage,
        "spec": spec_summary(stage_context.plan),
        "output": path_prompt,
        "provider": "internal",
    }

# 构造内部 stage 的 extract state 快照，统一 workflow-state 的字段格式。
def _build_internal_stage_extract_state(
    *,
    stage_workspace: StageWorkspace,
    path_response: Path,
    path_artifact: Path,
) -> dict[str, Any]:
    """
    生成内部 stage 写入 workflow-state 的 extract 状态快照。

    参数:
        stage_workspace: 当前内部 stage 的目录与 manifest 视图。
        path_response: response markdown 的落盘路径。
        path_artifact: 内部 stage 实际写出的工件路径。

    返回:
        适合写入 workflow-state.json 的 extract 状态快照字典。
    """

    # 返回内部 stage 的 extract 状态快照。
    return {
        "response": path_response,
        "out_dir": stage_workspace.path_artifact_dir,
        "written_files": [path_artifact],
    }

# 执行单个 stage 的 prompt 渲染、模型生成、抽取与阶段内合同补全。
def _run_generation_stage(
    stage_context: StageRunContext,
    str_stage: str,
    dict_previous_stage: dict[str, Any] | None,
    dict_active_codegen_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    执行单个 stage 的生成、抽取与阶段内合同校验。

    参数:
        stage_context: 当前 attempt 复用的 stage 执行上下文。
        str_stage: 当前准备执行的 stage 名称。
        dict_previous_stage: 上一阶段可见的输出字典；首阶段时为 None。
        dict_active_codegen_plan: 当前轮次激活的可选 codegen plan 字典。

    返回:
        当前 stage 完整输出的结构化字典。
    """

    # 准备当前 stage 的目录与 manifest 视图。
    stage_workspace_view = _prepare_stage_workspace(stage_context=stage_context, str_stage=str_stage)  # 当前 stage 的目录与 manifest 视图

    # requirements 阶段属于内部合成 JSON stage，不调用外部 provider。
    if str_stage == "requirements":

        # 把已确认的 workflow plan 压成 requirements JSON 载荷。
        dict_requirements_payload = build_requirements_payload(stage_context.plan)  # requirements stage 的内部 JSON 载荷

        # requirements stage 直接复用内部 JSON 写盘流程。
        return _run_internal_json_stage(
            stage_context=stage_context,
            stage_workspace=stage_workspace_view,
            dict_payload=dict_requirements_payload,
            payload_key="requirements",
        )

    # codegen_plan 缺少外部覆盖时，先由内部规划器给出结构化计划。
    if str_stage == "codegen_plan" and dict_active_codegen_plan is None:

        # 生成本轮尚未外部确认时使用的默认 codegen plan 载荷。
        dict_codegen_plan_payload = build_codegen_plan(stage_context.plan)  # 缺省 codegen_plan.json 的内部计划载荷

        # 把新生成的 codegen plan 交给内部 stage 统一落盘。
        return _run_internal_json_stage(
            stage_context=stage_context,
            stage_workspace=stage_workspace_view,
            dict_payload=dict_codegen_plan_payload,
            payload_key="codegen_plan",
        )

    # codegen_plan 已有外部覆盖时，先验证载荷再回填成内部 stage 输出。
    if str_stage == "codegen_plan" and dict_active_codegen_plan is not None:

        # 先校验外部注入的 codegen plan 至少满足基本结构合同。
        validate_codegen_plan_payload(
            stage_context.plan,
            dict_active_codegen_plan,
            require_ready=False,
        )

        # 把外部 codegen plan 包装成内部 stage 输出，保持结构一致。
        return _run_internal_json_stage(
            stage_context=stage_context,
            stage_workspace=stage_workspace_view,
            dict_payload=dict_active_codegen_plan,
            payload_key="codegen_plan",
        )

    # 把当前 stage 发送给 provider 的 prompt 单独落盘，便于后续复盘模型输入。
    path_prompt = stage_workspace_view.path_stage_dir / f"{str_stage}_prompt.md"  # 当前 stage 的 prompt Markdown 文件

    # 把 provider 的原始回复正文单独落盘，便于回放提取前的原始输出。
    path_response = stage_workspace_view.path_stage_dir / f"{str_stage}_response.md"  # 当前 stage 保存原始回复正文的 markdown 路径

    # 把当前 stage 的 manifest 先提取成本地变量，减少后续 helper 重复取属性。
    dict_manifest = stage_workspace_view.dict_manifest  # 当前 stage 使用的提取 manifest

    # 把当前 stage 的 artifact 目录先提取成本地变量，减少 extract/trace 的重复属性访问。
    path_stage_artifact_dir = stage_workspace_view.path_artifact_dir  # 当前 stage 用于落盘工件的目录

    # 从上一阶段输出里提取向量合同，供当前 prompt 拼接参考约束。
    if dict_previous_stage:

        # 从上一阶段输出中提取可复用的向量合同。
        dict_vector_contract = dict_previous_stage.get("vector_contract")  # 上一阶段产出的向量合同

    # 前序阶段缺席时，明确回退为“没有向量合同可复用”。
    else:

        # 首阶段或前序阶段无向量输出时，不向 prompt 追加向量合同。
        dict_vector_contract = None  # 当前 stage 没有可复用的向量合同

    # 解析当前 stage 希望使用的注释语言。
    str_comment_lang = str(stage_context.config.get("comment_language", "zh"))  # 当前 stage 的注释语言请求

    # 渲染当前 stage 的 prompt。
    str_prompt_text = _render_stage_prompt(  # 当前 stage 交给 provider 的 prompt 文本
        stage_context,  # 当前 run 的目录、配置与 provider 上下文
        str_stage,  # 当前准备生成的 stage 名称
        dict_previous_stage,  # 上一阶段可供拼接的摘要与合同
        dict_vector_contract,  # 上一阶段留下的可选向量合同
        dict_active_codegen_plan,  # 当前轮次生效中的 codegen plan
        str_comment_lang,  # 本 stage 请求使用的注释语言
    )

    # 把 prompt 写入 stage 目录，便于人工审阅与复现。
    write_text(path_prompt, str_prompt_text)

    # 组织当前 stage 的 prompt trace 事件，记录输入来源和写盘位置。
    dict_prompt_event = _build_stage_prompt_trace_event(  # 记录 prompt 输入来源的 trace 事件
        stage_context,  # 当前 run 的稳定 stage 上下文
        str_stage,  # 当前 prompt 对应的 stage 名称
        path_prompt,  # 已写盘的 prompt Markdown 路径
    )

    # 记录 prompt trace 事件。
    append_trace_event(stage_context.trace_path, dict_prompt_event)

    # 先把 provider.generate 所需的上下文对象组装完整，便于后面单点调用模型。
    generation_context_call = _build_gen_context(  # 发给 provider.generate 的阶段上下文对象
        stage_context,  # 当前 run 的目录、provider 与 trace 上下文
        str_stage,  # 当前准备调用生成的 stage 名称
        path_prompt,  # prompt Markdown 的落盘路径
        path_response,  # response Markdown 的目标路径

        # 这一组字段约束本阶段允许生成的工件合同、上游向量线索与注释语言。
        dict_manifest,  # 当前 stage 允许返回的工件合同
        dict_vector_contract,  # 当前 stage 可选复用的上游向量合同
        str_comment_lang,  # 当前 stage 请求使用的注释语言
    )

    # 调用 provider 生成当前 stage 的响应文本。
    text_response = stage_context.provider.generate(str_prompt_text, generation_context_call)  # 当前 stage 的模型响应文本

    # 把响应文本写回 stage 目录。
    write_text(path_response, text_response)

    # 从响应文本中抽取当前 stage 的工件。
    list_written_paths = extract_response(  # 当前 stage 成功抽取的工件路径列表
        text_response,  # 当前 stage 的模型响应正文
        path_stage_artifact_dir,  # 当前 stage 工件需要落盘到的目录
        expected_manifest=dict_manifest,  # 当前 stage 允许抽取的工件合同
    )

    # 组织 response -> artifacts 的 trace 事件，便于后续回放本阶段抽取结果。
    dict_extract_event = _build_stage_extract_trace_event(  # 记录本次 response 抽取到了哪些工件
        stage_context,  # 复用当前 attempt 的目录与 trace 上下文
        path_response,  # 当前 stage response Markdown 路径
        path_stage_artifact_dir,  # 本次抽取工件写入的 artifact 目录
        list_written_paths,  # 写入 trace 的真实工件路径清单
    )

    # 把本次抽取映射追加到 trace.jsonl。
    append_trace_event(stage_context.trace_path, dict_extract_event)

    # 把 stage 目录与 prompt/response 路径折叠成统一输出骨架。
    dict_output = _new_stage_output(  # 当前 stage 输出给后续步骤复用的基础字典
        stage_workspace=stage_workspace_view,  # 用于 stage summary 的目录与 manifest 视图
        path_prompt=path_prompt,  # 当前 stage prompt 的落盘路径
        path_response=path_response,  # 当前 stage response 的实际文件路径
    )

    # HLS stage 需要补做接口合同。
    if str_stage == "hls":

        # 把 HLS 工件的接口合同补回当前 stage 输出。
        _populate_hls_stage_output(
            stage_workspace=stage_workspace_view,
            dict_output=dict_output,
        )

    # 返回单个 stage 的完整输出字典。
    return dict_output

# 以内部合成 JSON 形式执行 requirements/codegen_plan 这类非 provider stage。
def _run_internal_json_stage(
    *,
    stage_context: StageRunContext,
    stage_workspace: StageWorkspace,
    dict_payload: dict[str, Any],
    payload_key: str,
) -> dict[str, Any]:
    """
    执行内部 JSON stage，并返回与外部 stage 对齐的输出结构。

    参数:
        stage_context: 当前 attempt 复用的 stage 执行上下文。
        stage_workspace: 当前内部 stage 的目录与 manifest 视图。
        dict_payload: 需要直接写入工件文件的内部 JSON 载荷。
        payload_key: 回填到 stage 输出字典中的键名。

    返回:
        与外部 provider stage 保持相同结构的 stage 输出字典。
    """

    # 约定内部 stage 的 prompt markdown 路径，便于说明该阶段来自内部合成。
    path_prompt = stage_workspace.path_stage_dir / f"{stage_workspace.str_stage}_prompt.md"  # 内部 stage 的 prompt markdown 路径

    # 约定内部 stage 的 response markdown 路径，便于对外保持统一回放格式。
    path_response = stage_workspace.path_stage_dir / f"{stage_workspace.str_stage}_response.md"  # 内部 stage 的伪响应 Markdown 路径

    # 生成内部 stage 固定使用的提示文本，明确该阶段不走外部模型。
    text_prompt = (
        f"# Internal {stage_workspace.str_stage} stage\n\n"
        "This stage is synthesized from confirmed HLS inputs.\n"
    )  # 内部 stage 的固定 prompt 文本

    # 把内部 stage prompt 写入 stage 目录。
    write_text(path_prompt, text_prompt)

    # 读取 manifest 中约定的唯一输出文件条目。
    dict_file_entry = stage_workspace.dict_manifest["files"][0]  # 内部 stage 的目标文件条目

    # 按 manifest 相对路径还原出内部工件的实际写盘路径。
    path_artifact = stage_workspace.path_artifact_dir / Path(*Path(str(dict_file_entry["path"])).parts)  # 内部 stage 生成的工件文件路径

    # 确保工件父目录存在。
    path_artifact.parent.mkdir(parents=True, exist_ok=True)

    # 将内部 stage 载荷直接写成目标工件。
    write_json(path_artifact, dict_payload)

    # 把 manifest 渲染成伪 response 的第一段 JSON 片段。
    text_manifest_block = json.dumps(stage_workspace.dict_manifest, indent=2, ensure_ascii=False)  # 伪 response 中的 manifest JSON 片段

    # 把内部 payload 渲染成伪 response 的第二段 JSON 片段。
    text_payload_block = json.dumps(dict_payload, indent=2, ensure_ascii=False)  # 伪 response 中承载业务载荷的 JSON 片段

    # 构造与外部 provider 响应结构兼容的内部 response 文本。
    text_response = (
        "```json\n"
        f"{text_manifest_block}\n"
        "```\n"
        f"```json path={dict_file_entry['path']}\n"
        f"{text_payload_block}\n"
        "```\n"
    )  # 内部 stage 的伪响应文本

    # 把内部 stage response 写回 stage 目录。
    write_text(path_response, text_response)

    # 组织内部 stage 的 prompt trace 事件，标记该阶段来自内部合成流程。
    dict_prompt_event = _build_internal_stage_prompt_trace_event(  # 内部 stage 的 prompt trace 事件
        stage_context=stage_context,  # 当前 run 的目录、trace 与状态上下文
        stage_workspace=stage_workspace,  # 当前内部 stage 的工作区视图
        path_prompt=path_prompt,  # 内部 stage prompt Markdown 路径
    )

    # 记录内部 stage 的 prompt trace 事件。
    append_trace_event(stage_context.trace_path, dict_prompt_event)

    # 组织内部 stage 的 extract 状态快照，明确伪 response 与工件的配对关系。
    dict_extract_state = _build_internal_stage_extract_state(  # 内部 stage 输出映射到 state.json 的 extract 快照
        stage_workspace=stage_workspace,  # 内部 stage 当前写入的工作区路径视图
        path_response=path_response,  # 伪 response Markdown 的路径
        path_artifact=path_artifact,  # 由内部 payload 直接生成的工件路径
    )

    # 记录内部 stage 的 extract state 快照。
    _record_state(
        stage_context.state_path,
        "extract",
        dict_extract_state,
        enabled=stage_context.state_updates,
    )

    # 先准备 summary 里额外暴露的 artifact_path 字段。
    dict_summary_extra = {  # 需要并入内部 stage summary 的额外路径字段
        "artifact_path": safe_path(path_artifact),  # 内部 stage 直接写出的工件相对路径
    }

    # 把内部 stage 的关键路径与补充摘要折叠成统一输出骨架。
    dict_output = _new_stage_output(  # 内部 stage 组装给后续步骤复用的完整输出骨架
        stage_workspace=stage_workspace,  # 用于内部 stage summary 的目录与 manifest 视图
        path_prompt=path_prompt,  # 内部 stage prompt 的落盘路径
        path_response=path_response,  # 内部 stage response 的实际文件路径
        dict_summary_extra=dict_summary_extra,  # 内部 stage 额外需要暴露的 artifact 路径摘要
    )

    # 把内部 JSON 载荷写回 stage 输出，供后续 workflow 直接复用。
    dict_output.update({payload_key: dict_payload})

    # 返回内部 stage 的完整输出字典。
    return dict_output

# 用 stage 上下文补齐人工阻断所需参数，收敛调用点的超长参数列。
def _block_current_attempt_for_human(
    stage_context: StageRunContext,
    dict_attempt_record: dict[str, Any],
    result: dict[str, Any],
    result_path: Path,
    dict_active_codegen_plan: dict[str, Any],
) -> dict[str, Any]:
    """
    基于当前 stage 上下文生成 blocked_human 结果。

    参数:
        stage_context: 当前 attempt 的 stage 运行上下文。
        dict_attempt_record: 当前 attempt 的状态记录字典。
        result: 当前 workflow 的结果字典。
        result_path: workflow_result.json 的落盘路径。
        dict_active_codegen_plan: 当前轮次生效的 codegen plan 载荷。

    返回:
        已写入 blocked_human 状态后的 workflow 结果字典。
    """

    # 把人工阻断阶段复用的路径与 provider 信息收敛成稳定上下文。
    human_block_context = HumanBlockContext(  # 当前人工阻断分支共用的稳定写盘上下文
        result_path=result_path,  # workflow_result.json 的统一写回路径
        attempt_dir=stage_context.attempt_dir,  # 当前被人工阻断的 attempt 目录
        state_path=stage_context.state_path,  # 当前人工阻断分支持续同步的 workflow-state.json 路径
        trace_path=stage_context.trace_path,  # trace.jsonl 的统一追加路径
        provider_name=stage_context.provider.name,  # 当前 attempt 使用的 provider 名称
        state_updates=stage_context.state_updates,  # 是否继续同步 workflow-state
    )

    # 返回包含 intervention 文件与 blocked_human 状态的 workflow 结果。
    return _block_for_human(
        dict_attempt_record=dict_attempt_record,
        result=result,
        dict_codegen_plan=dict_active_codegen_plan,
        human_block_context=human_block_context,
    )

# 统一执行最终 HLS 验证，避免主流程堆叠过长的 validate_generated 参数列。
def _run_final_validation(
    plan: dict[str, Any],
    path_artifact_dir: Path,
    config: dict[str, Any],
) -> Any:
    """
    执行最终 HLS 工件验证并返回验证报告。

    参数:
        plan: 当前 workflow 的结构化计划字典。
        path_artifact_dir: 最终 HLS 工件目录路径。
        config: workflow 的运行配置字典。
    返回:
        validate_generated 返回的最终验证报告对象。
    """

    # 返回最终 HLS 工件的综合验证报告对象。
    return validate_generated(
        plan,
        path_artifact_dir,
        target="hls",
        options=ValidationRunOptions(
            run_external=bool(config.get("run_external", True)),
            readiness=str(config.get("readiness", "execute")),
            comment_language=str(config.get("comment_language", "zh")),
            hls_profile=config.get("hls_profile") or {},
        ),
    )

# 构造 validate 阶段写入 workflow-state 的状态快照，统一字段口径。
def _build_validate_state(
    path_artifact_dir: Path,
    path_validation_json: Path,
    readiness: Any,
    bool_ok: bool,
) -> dict[str, Any]:
    """
    生成 validate 阶段写入 workflow-state 的状态快照。

    参数:
        path_artifact_dir: 最终 HLS 工件目录路径。
        path_validation_json: validation_report.json 的落盘路径。
        readiness: 当前 workflow 请求达到的验证深度。
        bool_ok: 最终验证是否通过。

    返回:
        适合写入 workflow-state.json 的 validate 状态快照字典。
    """

    # 返回 validate 阶段约定字段的状态快照字典。
    return {
        "path": path_artifact_dir,
        "output": path_validation_json,
        "readiness": readiness,
        "ok": bool_ok,
    }

# 构造最终验证 trace 事件，统一验证阶段的字段命名和内容。
def _build_validation_trace_event(
    str_attempt_id: str,
    path_artifact_dir: Path,
    readiness: Any,
    validation_report: Any,
    provider_name: str,
) -> dict[str, Any]:
    """
    生成最终验证阶段写入 trace.jsonl 的事件字典。

    参数:
        str_attempt_id: 当前 attempt 的稳定编号。
        path_artifact_dir: 最终 HLS 工件目录路径。
        readiness: 当前 workflow 请求达到的验证深度。
        validation_report: validate_generated 返回的验证报告对象。
        provider_name: 生成当前 attempt 的 provider 名称。

    返回:
        可直接追加到 trace.jsonl 的 validate 事件字典。
    """

    # 返回最终验证阶段完整的 trace 事件载荷。
    return {
        "event": "validate",
        "attempt_id": str_attempt_id,
        "target": "hls",
        "readiness": readiness,
        "path": path_artifact_dir,
        "ok": validation_report.ok(),
        "errors": validation_report.errors,
        "warnings": validation_report.warnings,
        "skips": validation_report.skips,
        "issues": [issue.to_dict() for issue in validation_report.issues],
        "metrics": validation_report.metrics or {},
        "provider": provider_name,
    }

# 构造 workflow_attempt 状态快照，统一 passed/blocked_toolchain 的状态格式。
def _build_workflow_attempt_state(
    str_attempt_id: str,
    str_status: str,
    path_validation_json: Path,
    path_remote_toolchain_request: Path | None = None,
) -> dict[str, Any]:
    """
    生成 workflow_attempt 阶段写入 workflow-state 的状态快照。

    参数:
        str_attempt_id: 当前 attempt 的稳定编号。
        str_status: 当前 attempt 的最终状态字符串。
        path_validation_json: validation_report.json 的落盘路径。
        path_remote_toolchain_request: 可选的远端工具链求助文件路径。

    返回:
        适合写入 workflow-state.json 的 attempt 状态快照字典。
    """

    # 先写入所有 attempt 状态都共享的基础字段。
    dict_attempt_state = {"attempt_id": str_attempt_id, "status": str_status, "validation_json": path_validation_json}  # workflow_attempt 的基础状态快照

    # 仅在远端工具链阻断时追加 remote 请求路径。
    if path_remote_toolchain_request is not None:

        # 回填 blocked_toolchain 需要的 remote 请求文件路径。
        dict_attempt_state["remote_toolchain_request"] = path_remote_toolchain_request  # blocked_toolchain 关联的远端求助文件路径

    # 返回统一结构的 workflow_attempt 状态快照。
    return dict_attempt_state

# 构造 remote_toolchain_request 事件，统一 trace 中的字段命名。
def _build_remote_toolchain_event(
    str_attempt_id: str,
    path_remote_toolchain_request: Path,
) -> dict[str, Any]:
    """
    生成 remote_toolchain_request 的 trace 事件字典。

    参数:
        str_attempt_id: 当前 attempt 的稳定编号。
        path_remote_toolchain_request: 远端工具链求助文件路径。

    返回:
        可直接追加到 trace.jsonl 的远端工具链事件字典。
    """

    # 返回远端工具链求助阶段的 trace 事件载荷。
    return {
        "event": "remote_toolchain_request",
        "attempt_id": str_attempt_id,
        "output": path_remote_toolchain_request,
        "preferred_skill": "erie-remote-ssh",
    }

# 在 codegen plan 未 ready 时写出人工介入请求，并把 workflow 标记为 blocked_human。
def _block_for_human(
    dict_attempt_record: dict[str, Any],
    result: dict[str, Any],
    dict_codegen_plan: dict[str, Any],
    human_block_context: HumanBlockContext,
) -> dict[str, Any]:
    """
    写出人工介入请求，并把 workflow 结果标记为 blocked_human。

    参数:
        dict_attempt_record: 当前 attempt 的状态记录字典。
        result: 当前 workflow 的结果字典。
        dict_codegen_plan: 当前轮次生效的 codegen plan 载荷。
        human_block_context: blocked_human 写盘阶段复用的稳定上下文。

    返回:
        已写入 blocked_human 状态后的 workflow 结果字典。
    """

    # intervention.json 固定放在当前 attempt 目录下。
    path_intervention_file = human_block_context.attempt_dir / "intervention.json"  # 当前人工介入请求文件路径

    # 收集 codegen plan 尚未关闭的开放问题列表。
    list_open_questions = dict_codegen_plan.get("open_questions", [])  # codegen plan 中待人工确认的问题列表

    # 若没有显式问题，则回退到通用的 HLS 需求确认提示。
    str_primary_question = str((list_open_questions or ["Confirm the remaining HLS requirements."])[0])  # 当前人工介入的主问题文本

    # 约束人工决策 JSON 至少给出设计决策和需要保留的约束。
    dict_expected_answer_format = dict(  # intervention 期望的回答结构
        decision="one concise HLS design decision",  # 人工回答里的主决策文本
        constraints="interface or pipeline constraints to preserve",  # 人工回答里需要保留的接口或流水约束
    )

    # 构造要写入 intervention.json 的人工介入请求载荷。
    dict_intervention = dict(  # human intervention 请求 JSON 载荷
        version=1,  # 请求合同版本
        action="ask_human",  # 当前请求动作
        primary_source="needs_human_intervention",  # 触发人工介入的主来源标签
        question=str_primary_question,  # 当前需要用户回答的主问题
        observations=list_open_questions,  # 辅助用户理解上下文的开放问题列表
        expected_answer_format=dict_expected_answer_format,  # 人工决策 JSON 的期望结构
    )

    # 先把人工介入请求落到当前 attempt 目录。
    write_json(path_intervention_file, dict_intervention)

    # 在 attempt 记录中登记 intervention.json 的相对路径。
    dict_attempt_record["intervention_path"] = safe_path(path_intervention_file)  # 当前 attempt 的人工介入文件相对路径

    # 当前 attempt 命中人工阻断后要同步改写 attempt 状态。
    dict_attempt_record["status"] = "blocked_human"  # 当前 attempt 的最终状态改为 blocked_human

    # workflow 总结果也要同步标记为 blocked_human。
    result["status"] = "blocked_human"  # 顶层 workflow 状态同步改为 blocked_human

    # 把阻断后的 workflow 结果重新写回 workflow_result.json。
    _write_result(human_block_context.result_path, result)

    # 组织 workflow-state 里的人工阻断快照，明确 intervention 文件和阻断来源。
    dict_state_payload = dict(  # human_intervention 分支写入 workflow-state 的状态快照
        output=path_intervention_file,  # intervention.json 的实际路径
        attempt_id=dict_attempt_record["attempt_id"],  # 被阻断的 attempt 编号
        primary_source="needs_human_intervention",  # 当前阻断来源标签
    )

    # 把人工阻断状态同步写入 workflow-state.json。
    _record_state(
        human_block_context.state_path,
        "human_intervention",
        dict_state_payload,
        enabled=human_block_context.state_updates,
    )

    # 组织 trace.jsonl 里的人工作业阻断事件。
    dict_trace_event = dict(  # trace.jsonl 中记录的人工阻断事件载荷
        event="human_intervention",  # 当前 trace 事件类型
        attempt_id=dict_attempt_record["attempt_id"],  # trace 里关联的被阻断 attempt 编号
        output=path_intervention_file,  # trace 中引用的 intervention.json 实际路径
        primary_source="needs_human_intervention",  # trace 中标记本次 blocked_human 来自人工收敛需求
        provider=human_block_context.provider_name,  # 触发本次阻断的 provider 名称
    )

    # 把人工阻断事件追加到 trace.jsonl。
    append_trace_event(human_block_context.trace_path, dict_trace_event)

    # 返回已更新为 blocked_human 的 workflow 结果。
    return result

# 把人工确认写回 workflow 计划，关闭 codegen plan override 的开放问题。
def _apply_human_decision_to_plan(
    plan: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """
    把人工决策写回 workflow 计划副本。

    参数:
        plan: 当前 workflow 的结构化计划字典。
        decision: 用户提供的人类确认决策字典。

    返回:
        已合并人工决策后的 workflow 计划副本。
    """

    # 先复制整个 plan，避免直接修改调用方持有的对象。
    dict_resolved_plan = copy.deepcopy(plan)  # 合并人工决策后的 workflow 计划副本

    # 先取出 plan 中原始的 workflow 子对象。
    raw_workflow_section = dict_resolved_plan.get("workflow")  # plan 中原始的 workflow 子对象

    # workflow 子对象只在其本身是字典时才允许继续合并。
    if isinstance(raw_workflow_section, dict):

        # 复制 workflow 子对象，避免后续改写影响 plan 原始载荷。
        dict_workflow_section = copy.deepcopy(raw_workflow_section)  # plan 中 workflow 子对象的可编辑副本

    # workflow 字段缺失或形态异常时，退回到可写回的空 workflow 容器。
    else:

        # 缺失或非字典的 workflow 子对象统一回退为空字典。
        dict_workflow_section = {}  # 当前 plan 不可编辑的 workflow 回退值

    # 读取 workflow 节点下尚未整理的 codegen_plan_override 原始值。
    raw_codegen_plan_override = dict_workflow_section.get("codegen_plan_override")  # workflow 节点里尚未处理的 override 原值

    # 只有字典形态的 override 才允许继续深拷贝和写回。
    if isinstance(raw_codegen_plan_override, dict):

        # 复制 override，避免后续改写污染 workflow 子对象中的原始引用。
        dict_codegen_plan_override = copy.deepcopy(raw_codegen_plan_override)  # 准备回写 human_resolution 的 override 副本

    # override 字段缺失或形态异常时，退回到可编辑的空 override 容器。
    else:

        # 缺失或非字典的 override 统一回退为空字典。
        dict_codegen_plan_override = {}  # 缺失 override 时使用的空覆盖容器

    # 检查 override 的 open_questions 列表是否仍残留待人工回答的问题。
    bool_has_open_questions = bool(dict_codegen_plan_override.get("open_questions"))  # override 是否还保留待回答的问题列表

    # 检查 override 是否显式声明当前轮次还不能继续生成。
    bool_not_ready_for_generation = not bool(dict_codegen_plan_override.get("ready_for_generation", True))  # override 是否仍阻止继续生成

    # 汇总 override 是否仍需要把本次人工确认写回。
    bool_needs_human_resolution = bool_has_open_questions or bool_not_ready_for_generation  # 本轮是否需要把人工确认折回 override

    # 仅在 override 仍未收敛时回填人工确认结果。
    if bool_needs_human_resolution:

        # 关闭 override 的开放问题，表明当前轮次已人工确认。
        dict_codegen_plan_override["open_questions"] = []  # 人工确认后清空 override 的开放问题列表

        # 人工确认完成后，允许后续阶段继续生成。
        dict_codegen_plan_override["ready_for_generation"] = True  # 人工确认后允许后续 stage 继续生成

        # 记录人工决策及需要沿用的约束和证据。
        dict_human_resolution = dict(  # 写入 override 的人工确认摘要
            decision=decision.get("decision"),  # 本轮人工确认选定的主设计决策
            constraints=decision.get("constraints", []),  # 需要继续带入后续 stage 的约束清单
            evidence=decision.get("evidence", []),  # 支撑本轮确认结论的补充证据
        )

        # 把人工确认摘要写回 codegen_plan_override。
        dict_codegen_plan_override["human_resolution"] = dict_human_resolution  # override 中的人类确认摘要

        # 把更新后的 override 写回 workflow 子对象。
        dict_workflow_section["codegen_plan_override"] = dict_codegen_plan_override  # 已更新的 override 子对象

        # 再把 workflow 子对象写回 plan 副本。
        dict_resolved_plan["workflow"] = dict_workflow_section  # 把已写入 override 的 workflow 节点放回计划副本

    # 返回已合并人工决策的 workflow 计划副本。
    return dict_resolved_plan

# 把人工确认写回 codegen plan，关闭开放问题并补齐 human_resolution。
def _apply_human_decision_to_codegen_plan(
    dict_codegen_plan: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """
    把人工决策写回 codegen plan 副本。

    参数:
        dict_codegen_plan: 当前轮次使用的 codegen plan 字典。
        decision: 用户提供的人类确认决策字典。

    返回:
        已合并人工决策后的 codegen plan 副本。
    """

    # 复制 codegen plan，避免直接污染调用方持有的原始对象。
    dict_resolved_codegen_plan = copy.deepcopy(dict_codegen_plan)  # 合并人工决策后的 codegen plan 副本

    # 检查 codegen plan 的 open_questions 列表是否仍非空。
    bool_has_open_questions = bool(dict_resolved_codegen_plan.get("open_questions"))  # codegen plan 是否还留有待回答问题

    # 检查 codegen plan 是否显式声明当前还不能继续生成。
    bool_not_ready_for_generation = not bool(dict_resolved_codegen_plan.get("ready_for_generation", True))  # codegen plan 是否仍禁止继续生成

    # 汇总 codegen plan 是否仍需要当前这轮人工决策补齐。
    bool_needs_human_resolution = bool_has_open_questions or bool_not_ready_for_generation  # 当前 codegen plan 是否需要人工闭环

    # 仅在 codegen plan 还留有未闭环项时，才把人工决策回填到计划里。
    if bool_needs_human_resolution:

        # 用空列表明确声明当前人工回答已经关掉所有待确认问题。
        dict_resolved_codegen_plan["open_questions"] = []  # 本轮人工决策后不再保留未回答的 open questions

        # 同步把 codegen plan 切回可继续生成状态。
        dict_resolved_codegen_plan["ready_for_generation"] = True  # 人工确认后允许该 codegen plan 继续生成

        # 记录这次 codegen plan 人工确认真正采纳的约束与证据。
        dict_human_resolution = dict(  # 直接回填到 codegen plan 的人工确认摘要
            decision=decision.get("decision"),  # 本轮写回 codegen plan 的主设计决策
            constraints=decision.get("constraints", []),  # 后续生成仍需沿用的接口或流水约束
            evidence=decision.get("evidence", []),  # 支撑该 codegen plan 决策的补充证据
        )

        # 把本轮人工确认摘要挂回 codegen plan，便于恢复流程直接重放这次闭环结果。
        dict_resolved_codegen_plan["human_resolution"] = dict_human_resolution  # 当前 codegen plan 挂回的人类闭环决策摘要

    # 返回已合并人工决策的 codegen plan 副本。
    return dict_resolved_codegen_plan

# 识别最终验证里的本地工具链阻断，决定是否改走 remote Vitis 闭环。
def _blocked_toolchain(validation_report: Any) -> bool:
    """
    判断最终验证是否命中了本地工具链阻断。

    参数:
        validation_report: validate_generated 返回的验证报告对象。

    返回:
        True 表示应转入远端工具链求助；False 表示未命中该类阻断。
    """

    # 逐条检查验证报告里的 issue，寻找工具链阻断错误。
    for issue in getattr(validation_report, "issues", []) or []:

        # 非 error 级或非 toolchain_issue 来源的问题直接跳过。
        if getattr(issue, "severity", None) != "error" or getattr(issue, "source", None) != "toolchain_issue":

            # 当前 issue 不属于工具链阻断，继续检查下一个。
            continue

        # 命中已知 Vitis 阻断工具后立即返回 True。
        if getattr(issue, "tool", None) in vitis_blocking_tool_ids():

            # 当前验证失败属于本地工具链阻断。
            return True

    # 所有 issue 检查完后仍未命中工具链阻断。
    return False

# 生成 remote_toolchain_request.json，指导用户转到远端 Vitis 验证闭环。
def _write_remote_toolchain_request(
    path_attempt_dir: Path,
    str_attempt_id: str,
    config: dict[str, Any],
    validation_report: Any,
) -> Path:
    """
    生成 remote_toolchain_request.json。

    参数:
        path_attempt_dir: 当前 attempt 的工作目录路径。
        str_attempt_id: 当前 attempt 的稳定编号。
        config: workflow 的运行配置字典。
        validation_report: validate_generated 返回的验证报告对象。

    返回:
        已写出的 remote_toolchain_request.json 路径。
    """

    # 当前远端求助文件沿用 attempt 目录，便于和本轮验证工件并排归档。
    path_request_file = path_attempt_dir / "remote_toolchain_request.json"  # 远端工具链请求文件路径

    # workflow 配置中的 readiness 会决定远端验收要达到的深度。
    str_readiness = str(config.get("readiness", "execute"))  # 远端 Vitis 验收目标深度

    # 从运行期配置读取优先推荐的 Vitis 技能路由。
    dict_vitis_skill_routing = resolve_vitis_skill_preference()  # Vitis 技能偏好与路由信息

    # 读取当前偏好里被选中的 Vitis 指导技能名。
    str_selected_vitis_skill = str(dict_vitis_skill_routing.get("selected_skill", "vitis-developer"))  # 当前推荐的 Vitis 指导技能名

    # 先读取验证报告中的原始 issue 列表。
    list_validation_issues = getattr(validation_report, "issues", [])  # 验证报告中的原始 issue 列表

    # 仅保留验证报告里来自 toolchain_issue 的本地错误详情。
    list_local_toolchain_errors = []  # 本地工具链错误的序列化列表

    # 逐条过滤出真正属于本地工具链缺失的 issue。
    for issue in list_validation_issues:

        # 非 toolchain_issue 来源的问题不会写入远端求助请求。
        if getattr(issue, "source", None) != "toolchain_issue":

            # 当前 issue 与本地工具链求助无关，继续看下一条。
            continue

        # 把本地工具链错误序列化后写入远端求助请求。
        list_local_toolchain_errors.append(issue.to_dict())

    # 提供 erie-remote-ssh 侧用于选服和检查环境的命令建议。
    list_selection_commands = [  # 远端服务器发现与环境检查命令列表
        "python <erie-skill-dir>\\scripts\\remote_ssh.py discover --settings <erie-settings.json>",  # 发现可用远端服务器
        "python <erie-skill-dir>\\scripts\\remote_ssh.py choices --settings <erie-settings.json>",  # 查看候选服务器与路由选项
        "python <erie-skill-dir>\\scripts\\remote_ssh.py check --settings <erie-settings.json> --server <erie-server>",  # 检查目标服务器连通性
        (
            "python <erie-skill-dir>\\scripts\\remote_ssh.py workspace-check "
            "--settings <erie-settings.json> --server <erie-server>"
        ),  # 检查远端工作区是否已经可用
        (
            "python <erie-skill-dir>\\scripts\\remote_ssh.py scan-software "
            "--settings <erie-settings.json> --server <erie-server>"
        ),  # 拉取远端软件清单用于后续选版本
        (
            "python <erie-skill-dir>\\scripts\\remote_ssh.py software "
            "--settings <erie-settings.json> --server <erie-server> --name vitis"
        ),  # 核对远端是否装有可用 Vitis
    ]

    # 提供 HLS generator 侧用于远端验收的命令建议。
    list_remote_commands = [  # 远端 HLS 验收命令列表
        "python .\\scripts\\python\\remote\\remote_vitis_acceptance.py --mode link --server <erie-server> --json",  # 执行远端 link 验收
        (
            "python .\\scripts\\python\\remote\\remote_vitis_acceptance.py "
            f"--mode vitis --server <erie-server> --readiness {str_readiness} --json"  # 执行带 readiness 目标的远端 Vitis 验收
        ),
    ]

    # 给出远端 Vitis 收敛的推荐下一步，指导后续人工操作顺序。
    text_expected_next_step = (  # 远端工具链求助后的推荐推进说明
        "Use erie-remote-ssh discovery/choices first; "
        f"use {str_selected_vitis_skill} for Vitis flow guidance when available; "
        "after the user selects a server, run scan-software and the HLS remote "
        "acceptance helper. If multiple Vitis versions are detected, ask the user "
        "to choose one before continuing. By default, inspect the retained "
        "remote_dir under the selected server workdir after Vitis validation."
    )

    # 组织 remote_toolchain_request.json 的完整请求载荷。
    dict_remote_request = {  # 远端工具链求助请求 JSON 载荷
        "version": 1,  # 请求载荷版本号
        "action": "ask_remote_server",  # 请求远端服务器协助的动作类型
        "primary_source": "local_vitis_missing",  # 标记本次远端求助由本地 Vitis 缺失触发
        "preferred_skill": "erie-remote-ssh",  # 默认建议先走 erie-remote-ssh 选服与执行
        "vitis_skill_routing": dict_vitis_skill_routing,  # 记录当前解析到的 Vitis 技能偏好与路由
        "question": (  # 给人工远端求助提示的完整自然语言问题
            "Local Vitis HLS tools were not found. Ask the user to choose a "
            "configured erie-remote-ssh server with Vitis/Vivado available, "
            "then run remote HLS validation there."
        ),
        "attempt_id": str_attempt_id,  # 关联当前本地 attempt 的稳定编号
        "readiness": str_readiness,  # 告诉远端验收需要跑到哪一层深度
        "local_toolchain_errors": list_local_toolchain_errors,  # 原样透传本地缺失的 toolchain 错误明细
        "erie_remote_ssh": {  # 远端选服前需要展示给用户的 SSH 选择辅助信息
            "selection_commands": list_selection_commands,  # 引导用户先列出并选择可用 erie-remote-ssh 服务器的命令集合
            "user_decision_required": (  # 提醒用户必须先选定一台可用 erie 服务器
                "Select one enabled erie server id or name before any SSH execution."  # 在任何 SSH 操作前必须先明确选定目标服务器
            ),
        },
        "hls_generator_remote_commands": list_remote_commands,  # 远端服务器选定后需要依次执行的 hls-generator 命令
        "remote_artifact_policy": {  # 远端验证目录默认保留并允许用户显式覆盖清理策略
            "default": "retain",  # 远端验证成功后默认保留目录供人工复核
            "location": (  # helper 报告里返回的 remote_dir 相对工作区位置说明
                "The helper reports `remote_dir`, relative to the selected erie "
                "server workdir."
            ),
            "cleanup_override": (  # 用户显式要求时才允许删除远端验证目录
                "Pass --cleanup-remote only when the user explicitly wants the "
                "remote validation directory deleted after success."
            ),
        },
        "remote_vitis_version_policy": {  # 远端 Vitis 版本发现、多版本选择与配置持久化策略
            "default": "scan_and_require_choice_when_multiple",  # 多版本时先扫描再要求用户确认
            "user_config_path": "~/.hls-generator/config.json",  # 保存远端版本偏好的本地配置路径
            "selection_override": (  # 用户显式指定时优先保存并复用远端 Vitis 版本
                "Pass --vitis-version <version> to save and use a specific remote "
                "Vitis version for the selected server."
            ),
        },
        "expected_next_step": text_expected_next_step,  # 提醒本地阻断后用户下一步应执行的动作
    }

    # 把远端工具链求助请求写入当前 attempt 目录。
    write_json(path_request_file, dict_remote_request)

    # 返回已经写出的 remote_toolchain_request.json 路径。
    return path_request_file

# 检查当前阶段列表中是否仍然混入已移除的 python stage。
def _reject_legacy_python_stage_names(list_stages: list[str]) -> None:
    """
    拒绝仍然包含已移除 python stage 的阶段配置。

    参数:
        list_stages: 当前 workflow 打算执行的阶段名列表。

    返回:
        None。命中旧阶段时直接抛出 WorkflowError。
    """

    # 旧 workflow 阶段一旦继续流入执行循环，必须显式失败并要求新跑。
    if "python" in list_stages:

        # 当前配置仍然携带已移除的 python stage。
        raise WorkflowError(
            "> ERR: [Python] legacy workflow state still contains the removed "
            "python stage or Python reference contracts. This skill is HLS-only "
            "now; rerun from a fresh HLS-only workflow run."
        )

# 检查恢复流程载荷中是否仍然残留旧 python stage 或旧合同产物路径。
def _reject_legacy_python_stage_payloads(
    dict_workflow_config: dict[str, Any],
    dict_workflow_plan: dict[str, Any],
    dict_result_status: dict[str, Any],
) -> None:
    """
    拒绝恢复旧五阶段 run 或旧 Python reference 合同快照。

    参数:
        dict_workflow_config: 历史 workflow_config.json 载荷。
        dict_workflow_plan: 历史 plan.json 载荷。
        dict_result_status: 历史 workflow_result.json 载荷。

    返回:
        None。命中旧载荷时直接抛出 WorkflowError。
    """

    # 历史 workflow_config 中若仍然登记 python stage，说明当前 run 仍属于旧链路。
    if "python" in [str(item) for item in dict_workflow_config.get("stages", []) or []]:

        # 已移除的 python stage 不能继续通过恢复入口进入当前版本。
        raise WorkflowError(
            "> ERR: [Python] legacy workflow state still contains the removed "
            "python stage or Python reference contracts. This skill is HLS-only "
            "now; rerun from a fresh HLS-only workflow run."
        )

    # 历史 plan 中的 workflow.stages 同样不允许继续保留 python stage。
    raw_workflow_section = dict_workflow_plan.get("workflow")  # 历史 plan 的 workflow 分段

    # 只有 workflow 分段仍是字典时，才继续检查是否残留 python stage。
    if isinstance(raw_workflow_section, dict):

        # 从历史 plan.workflow.stages 提取阶段列表，供旧阶段拒绝逻辑复用。
        list_plan_stages = [str(item) for item in raw_workflow_section.get("stages", []) or []]  # 历史 plan 中登记的阶段列表

        # plan 里一旦还残留 python stage，就说明该 run 仍属于旧五阶段链路。
        if "python" in list_plan_stages:

            # 恢复入口不再兼容旧五阶段 run，必须显式提示 fresh rerun。
            raise WorkflowError(
                "> ERR: [Python] legacy workflow state still contains the removed "
                "python stage or Python reference contracts. This skill is HLS-only "
                "now; rerun from a fresh HLS-only workflow run."
            )

    # 任何 attempt 只要仍然引用旧 Python reference 合同路径，也必须要求 fresh rerun。
    for dict_attempt in dict_result_status.get("attempts", []) or []:

        # 读取当前 attempt 已登记的合同路径字典，检查是否残留旧 reference 产物键。
        dict_contract_paths = dict_attempt.get("contract_paths")  # 当前 attempt 的合同路径字典

        # 只有合同路径仍是字典时，才继续检查旧 reference 产物键。
        if not isinstance(dict_contract_paths, dict):

            # 非字典合同路径无法包含旧键，直接跳过当前 attempt。
            continue

        # 旧合同键一旦仍在历史 attempt 中出现，就必须拒绝恢复并要求新跑。
        if any(
            str_key in dict_contract_paths
            for str_key in ("reference_contract", "python_interface", "python_quality_gate")
        ):

            # 旧 reference 工件已经不再被当前版本接受，恢复时直接报错退出。
            raise WorkflowError(
                "> ERR: [Python] legacy workflow state still contains the removed "
                "python stage or Python reference contracts. This skill is HLS-only "
                "now; rerun from a fresh HLS-only workflow run."
            )

# 根据分解计划生成 workflow 运行配置，供新建与恢复流程统一复用。
def _workflow_config(
    plan: dict[str, Any],
    workflow_config_request: WorkflowConfigRequest,
) -> dict[str, Any]:
    """
    根据分解计划构造 workflow 运行配置。

    参数:
        plan: 当前 workflow 的结构化计划字典。
        workflow_config_request: 构造 workflow_config.json 所需的运行策略对象。

    返回:
        可直接写入 workflow_config.json 的配置字典。
    """

    # 先取出 plan 中原始的 workflow 子对象，后续所有配置都从这段节点继续派生。
    raw_workflow_section = plan.get("workflow")  # 分解计划里记录的 workflow 配置原值

    # workflow 子对象里承载 mock_behavior 等附加策略。
    dict_workflow_section = raw_workflow_section if isinstance(raw_workflow_section, dict) else {}  # 供 workflow_config 读取附加策略的工作流节点

    # 外部 codegen plan 只有在 dict 形态下才允许进入 workflow config。
    if isinstance(workflow_config_request.external_codegen_plan, dict):

        # 对外部 codegen plan 做深拷贝，避免后续写配置时回写原对象。
        dict_external_codegen_plan = copy.deepcopy(workflow_config_request.external_codegen_plan)  # 可安全写入 workflow config 的外部 codegen plan 副本

    # 非字典输入不能作为可落盘的外部 codegen plan。
    else:

        # 当前 workflow 没有可落盘的外部 codegen plan。
        dict_external_codegen_plan = None  # workflow 配置里不写外部 codegen plan

    # provider 配置单独折叠成子对象，便于后续 provider 构造逻辑复用。
    dict_provider_config = {
        "name": workflow_config_request.provider_name,  # provider 名称供恢复流程重建相同模型入口
        "command": workflow_config_request.provider_command,  # command provider 额外使用的命令串
    }  # workflow 中的 provider 子配置

    # budgets 为每个默认阶段分配统一的 normal 预算。
    dict_stage_budgets = {stage: "normal" for stage in DEFAULT_STAGES}  # workflow 默认阶段预算映射

    # 先收敛计划派生出的稳定身份与设计约束字段。
    dict_plan_identity = {
        "version": 1,  # workflow_config 的版本号
        "name": plan["name"],  # 当前 workflow 的稳定名称
        "target": "hls",  # 当前 workflow 固定面向的目标域
        "design_requirements": copy.deepcopy(plan.get("design_requirements", {})),  # 计划里声明的设计约束副本
        "streamability": plan.get("streamability"),  # 流式处理能力要求
        "transport_interface": plan.get("transport_interface"),  # 传输接口约束
        "dataflow_streamability": plan.get("dataflow_streamability"),  # DATAFLOW 相关的流化要求
        "interface_family": plan.get("interface_family"),  # 接口族约束
        "interface_profile": copy.deepcopy(plan.get("interface_profile", {})),  # 接口细节配置副本
        "pipeline_required": bool(plan.get("pipeline_required", True)),  # 是否强制要求 pipeline
        "codegen_plan_required": bool(plan.get("codegen_plan_required", True)),  # 是否必须先具备可执行的 codegen 计划
        "codegen_plan_path": plan.get("codegen_plan_path"),  # 外部 codegen plan 的来源路径
    }

    # 再收敛执行阶段共享的运行策略字段。
    dict_runtime_policy = {
        "stages": list(DEFAULT_STAGES),  # workflow 依次执行的默认阶段序列
        "readiness": workflow_config_request.readiness,  # 当前 run 需要达到的验证深度
        "max_attempts": workflow_config_request.max_attempts,  # 当前 run 在主循环里最多允许尝试的轮数
        "stop_on_human": workflow_config_request.stop_on_human,  # 命中人工阻断时是否立刻暂停
        "run_external": workflow_config_request.run_external,  # 是否允许继续调用外部工具链
        "comment_language": workflow_config_request.comment_language,  # 所有 stage 共用的注释语言
        "hls_profile": workflow_config_request.hls_profile,  # 生成与验证阶段共同读取的 HLS profile
        "external_codegen_plan": dict_external_codegen_plan,  # 可选落盘的外部 codegen plan
        "model_timeout_s": workflow_config_request.model_timeout_s,  # provider.generate 在当前 run 中实际沿用的秒级超时
        "provider": dict_provider_config,  # provider 构造所需的子配置
        "budgets": dict_stage_budgets,  # 每个 stage 的默认预算口径
        "mock_behavior": dict_workflow_section.get("mock_behavior"),  # workflow 节点携带的可选 mock 行为
    }

    # 返回可落盘的 workflow 配置字典。
    return {
        **dict_plan_identity,
        **dict_runtime_policy,
    }

# 返回指定阶段的 prompt manifest，空 stage 时回退到全量 manifest。
def _stage_manifest(plan: dict[str, Any], stage: str) -> dict[str, Any]:
    """
    返回指定阶段的 prompt manifest。

    参数:
        plan: 当前 workflow 的结构化计划字典。
        stage: 需要查询的阶段名；空字符串表示请求完整 manifest。

    返回:
        与目标阶段对应的 manifest 字典。
    """

    # stage 为空时回退到完整 manifest；否则只返回单阶段 manifest。
    return _stage_manifest_for(plan, stage) if stage else _manifest_for(plan)

# 创建新的 workflow attempt 记录，统一初始化可回填字段。
def _new_attempt_record(attempt_id: str, provider: str) -> dict[str, Any]:
    """
    创建新的 workflow attempt 记录。

    参数:
        attempt_id: 当前 attempt 的稳定编号。
        provider: 生成当前 attempt 的 provider 名称。

    返回:
        包含 prompt/response/artifact/validation 初始字段的 attempt 字典。
    """

    # attempt 记录承载当前轮次的 prompt/response/artifact/validation 位置。
    return {
        "attempt_id": attempt_id,
        "stage": FINAL_STAGE,
        "prompt_path": None,
        "response_path": None,
        "artifact_dir": None,
        "validation_json": None,
        "contract_paths": {},
        "status": "failed",
        "provider": provider,
    }

# 写入 workflow 结果文件，并校验最终状态值是否合法。
def _write_result(path: Path, result: dict[str, Any]) -> None:
    """
    写入 workflow 结果文件，并校验 status 合法性。

    参数:
        path: workflow_result.json 的落盘路径。
        result: 待写入的 workflow 结果字典。

    返回:
        None。结果会直接写入 path。

    异常:
        WorkflowError: 当结果中的 status 不在允许集合内时抛出。
    """

    # 已存在 attempts 时，status 必须属于受支持的最终状态枚举。
    if result.get("status") not in WORKFLOW_STATUSES and result.get("attempts"):

        # 非法状态会破坏后续 resume 与报告消费逻辑。
        raise WorkflowError(
            f"> ERR: [Python] workflow status must be one of {', '.join(WORKFLOW_STATUSES)}.",
        )

    # 通过校验后再把结果写入 JSON 文件。
    write_json(path, result)

# 返回当前阶段的上一个阶段名；当前阶段不存在时返回 None。
def _previous_stage(stage: str, stages: list[str]) -> str | None:
    """
    返回当前阶段之前的阶段名。

    参数:
        stage: 当前阶段名。
        stages: workflow 允许的阶段顺序列表。

    返回:
        上一个阶段名；若当前阶段不存在或已经是首阶段，则返回 None。
    """

    # 先定位当前阶段在阶段列表中的索引。
    try:

        # index 用于回退到上一阶段。
        int_stage_index = stages.index(stage)  # 当前阶段在 stages 中的索引

    # 当前阶段不在列表中时，说明不存在“上一阶段”。
    except ValueError:

        # 未知阶段没有可回退的上一阶段。
        return None

    # 索引大于 0 时返回前一个阶段，否则说明当前已是首阶段。
    return stages[int_stage_index - 1] if int_stage_index > 0 else None

# 读取一个可选 JSON 文件，并强制要求顶层必须是对象字典。
def _read_json(path: Path | None) -> dict[str, Any]:
    """
    读取一个可选 JSON 文件；空路径时返回空字典。

    参数:
        path: 可选的 JSON 文件路径。

    返回:
        解析后的 JSON 对象字典；空路径时返回空字典。

    异常:
        WorkflowError: 当 JSON 非法或顶层不是对象字典时抛出。
    """

    # 空路径通常表示当前调用没有提供该类可选输入。
    if path is None:

        # 没有可选 JSON 输入时返回空字典语义。
        return {}

    # 先通过工作区边界检查定位实际 JSON 文件。
    path_json_file = require_workspace_path(  # 已校验的 JSON 文件路径
        path,  # 调用方传入的原始 JSON 路径
        purpose="JSON path",  # 工作区边界检查使用的路径用途标签
        must_exist=True,  # 当前 JSON 文件必须已经存在
    )

    # 读取并解析 UTF-8 JSON 文本。
    try:

        # workflow 只接受顶层对象形态的 JSON 输入。
        dict_json_payload = json.loads(  # 解析后的 JSON 载荷
            path_json_file.read_text(encoding="utf-8"),  # 从磁盘读取的 UTF-8 JSON 文本
        )

    # 非法 JSON 需要把文件路径和底层异常一起暴露给调用方。
    except json.JSONDecodeError as exc:

        # 统一用 Python 错误前缀暴露非法 JSON 位置。
        raise WorkflowError(
            f"> ERR: [Python] invalid JSON in {path_json_file}: {exc}",
        ) from exc

    # workflow 只允许对象顶层，避免下游 key-based 读取失败。
    if not isinstance(dict_json_payload, dict):

        # 非对象顶层无法满足后续按键读取的合同。
        raise WorkflowError(
            f"> ERR: [Python] expected JSON object in {path_json_file}.",
        )

    # 返回通过结构校验的 JSON 对象。
    return dict_json_payload

# 在启用状态更新时写入 workflow-state.json。
def _record_state(
    state_path: Path,
    event: str,
    payload: dict[str, Any],
    *,
    enabled: bool,
) -> None:
    """
    在启用状态更新时写入 workflow-state.json。

    参数:
        state_path: workflow-state.json 的路径。
        event: 当前要写入的状态事件名。
        payload: 当前事件对应的状态载荷字典。
        enabled: 是否允许真正写入状态文件。

    返回:
        None。状态写入逻辑直接委托给 workspace helper。
    """

    # 底层状态写入逻辑集中复用 workspace 层 helper。
    update_workflow_state(state_path, event, payload, enabled=enabled)

# 解析 spec 中引用的外部 codegen plan，并做基础合同校验。
def _resolve_external_codegen_plan(
    spec: dict[str, Any],
    spec_file: Path,
) -> dict[str, Any] | None:
    """
    解析 spec 中引用的外部 codegen plan。

    参数:
        spec: 当前 workflow 的原始 spec 字典。
        spec_file: 当前 spec 文件路径。

    返回:
        通过校验的外部 codegen plan 字典；未配置时返回 None。
    """

    # codegen_plan_path 缺失时，说明当前 spec 未引用外部计划文件。
    raw_path = spec.get("codegen_plan_path")  # spec 中的外部 codegen plan 路径配置

    # 未声明外部 plan 时，不需要继续做路径解析。
    if not raw_path:

        # 当前 spec 未配置外部 codegen plan，直接返回 None。
        return None

    # 该路径需要相对 spec 文件解析，并受工作区边界约束。
    path_codegen_plan = require_workspace_path_from(  # 已校验的外部 codegen plan 文件路径
        spec_file,  # 作为相对路径锚点的 spec 文件
        Path(str(raw_path)),  # spec 中声明的外部 codegen plan 相对位置
        purpose="codegen plan path",  # 告诉工作区边界检查这是 codegen plan 路径
        must_exist=True,  # 强制要求这份外部 codegen plan 已经存在
    )

    # 读取外部 codegen plan，并通过现有 schema 校验。
    dict_codegen_plan_payload = _read_json(  # 外部 codegen plan JSON 载荷
        path_codegen_plan,  # 已完成工作区校验的 codegen plan 文件路径
    )

    # 用现有 schema 校验外部 codegen plan 的结构。
    validate_codegen_plan_payload(spec, dict_codegen_plan_payload, require_ready=False)

    # 返回通过校验的外部 codegen plan 字典。
    return dict_codegen_plan_payload
