"""为 HLS 生成、修改和解释请求提供轻量级任务分流。"""
# 启用延迟注解，避免运行期解析联合类型造成额外依赖。
from __future__ import annotations
# 导入标准库结构化数据与路径类型。
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# 识别仅注释请求的中英文关键词。
COMMENT_ONLY_MARKERS = (  # 仅注释请求关键词
    "comment-only",  # 英文仅注释请求
    "only comments",  # 英文仅注释表达
    "只补注释",  # 中文直接仅注释请求
    "仅补注释",  # 中文强调只改注释请求
    "补注释",  # 中文补充注释表达
    "改注释",  # 中文修改注释表达
    "中文注释",  # 中文注释要求表达
    "rewrite comments",  # 英文重写注释表达
)

# 识别修改类请求的中英文关键词。
MODIFY_MARKERS = (  # 修改请求关键词
    "modify",  # 英文修改请求
    "edit",  # 英文编辑请求
    "patch",  # 英文补丁请求
    "fix",  # 英文修复请求
    "修改",  # 中文修改请求
    "修复",  # 中文修复请求
    "更新",  # 中文更新请求
    "重构",  # 中文重构请求
)

# 识别解释和审查类请求的中英文关键词。
EXPLAIN_MARKERS = (  # 解释请求关键词
    "explain",  # 英文解释请求
    "review",  # 英文审查请求
    "analyze",  # 英文分析请求
    "解释",  # 中文解释请求
    "分析",  # 中文分析请求
    "审查",  # 中文审查请求
    "说明",  # 中文说明请求
)

# 识别生成类请求的中英文关键词。
GENERATE_MARKERS = (  # 生成请求关键词
    "generate",  # 英文生成请求
    "create",  # 英文创建请求
    "scaffold",  # 英文脚手架请求
    "生成",  # 中文生成请求
    "创建",  # 中文创建请求
    "新增",  # 中文新增请求
)

# HLS 分流结果只承载决策，不触发文件修改或模型调用。
@dataclass(frozen=True)
class HlsDispatchDecision:
    """记录 HLS 请求分流结果和后续检查要求。"""

    # mode 表示 generate、modify 或 explain 三类执行路径。
    mode: str  # 分流模式

    # target 固定标记当前分流面向 HLS 域。
    target: str = "hls"  # 目标领域

    # comment_only 标记是否只允许改写注释。
    comment_only: bool = False  # 仅注释标记

    # needs_target_or_code 提示上层流程补齐目标路径或代码片段。
    needs_target_or_code: bool = False  # 目标补齐要求

    # baseline_required 标记仅注释改写时是否缺少基线目录。
    baseline_required: bool = False  # 基线补齐要求

    # check_matrix 汇总上层流程应执行的质量门禁。
    check_matrix: dict[str, str] | None = None  # 检查矩阵

    # recommended_commands 给出人工或自动流程可复用的本地命令。
    recommended_commands: tuple[str, ...] = ()  # 推荐命令

    # 字典化结果用于 CLI JSON 输出和测试断言。
    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典，并把命令元组转换为列表。

        参数:
            self: 当前分流决策对象。

        返回:
            可直接写入 JSON 的分流结果字典。
        """

        # dataclass 结构先转换为普通字典，保留字段名称。
        dict_payload = asdict(self)  # 分流结果字典

        # 推荐命令转换为 JSON 更自然的列表形式。
        dict_payload["recommended_commands"] = list(self.recommended_commands)  # 推荐命令列表

        # 返回可直接写入 JSON 的结构。
        return dict_payload

# classify_hls_task 是上层工作流调用的公开分流入口。
def classify_hls_task(
    request_text: str,
    *,
    target_path: str | Path | None = None,
    baseline_path: str | Path | None = None,
) -> HlsDispatchDecision:
    """根据用户请求文本判断 HLS 工作流模式。

    Args:
        request_text: 用户自然语言请求。
        target_path: 已知的 HLS 目标文件或目录路径。
        baseline_path: 仅注释改写时用于语义对照的基线路径。

    Returns:
        HlsDispatchDecision，包含分流模式、补齐要求和建议检查命令。
    """

    # 统一大小写，便于同时匹配英文和中文关键词。
    str_request_text = (request_text or "").casefold()  # 规范化请求文本

    # 仅注释任务需要额外保护基线，避免改动非注释语义。
    bool_comment_only = _contains_marker(str_request_text, COMMENT_ONLY_MARKERS)  # 仅注释请求标记

    # 模式选择保持解释优先、修改其次、生成兜底的原有语义。
    str_mode = _select_mode(str_request_text, bool_comment_only=bool_comment_only, target_path=target_path)  # HLS 任务模式

    # 缺少目标时，上层工作流必须继续询问或读取代码上下文。
    bool_needs_target_or_code = _needs_target_or_code(str_mode, target_path=target_path)  # 目标或代码补齐标记

    # 仅注释改写必须带 baseline，以便后续 AST/语义保护。
    bool_baseline_required = str_mode == "modify" and bool_comment_only and baseline_path is None  # 基线补齐标记

    # 检查矩阵由模式和仅注释标记共同决定。
    dict_matrix = _check_matrix(str_mode, comment_only=bool_comment_only)  # 质量检查矩阵

    # 推荐命令保留原有调用面，供上层流程提示或执行。
    tuple_commands = _recommended_commands(str_mode, comment_only=bool_comment_only)  # 推荐命令元组

    # 汇总所有分流事实，保持返回结构稳定。
    return HlsDispatchDecision(
        mode=str_mode,
        comment_only=bool_comment_only,
        needs_target_or_code=bool_needs_target_or_code,
        baseline_required=bool_baseline_required,
        check_matrix=dict_matrix,
        recommended_commands=tuple_commands,
    )

# 关键词匹配保持简单，避免把调度器扩展成自然语言解析器。
def _contains_marker(str_text: str, tuple_markers: tuple[str, ...]) -> bool:
    """判断请求文本是否包含任一关键词。

    参数:
        str_text: 已规范化的请求文本。
        tuple_markers: 需要匹配的关键词元组。

    返回:
        命中任一关键词时返回 True，否则返回 False。
    """

    # any 保持短路匹配，避免把关键词集合扩展成复杂规则。
    return any(str_marker in str_text for str_marker in tuple_markers)

# 模式选择集中在一个函数内，便于测试复合关键词优先级。
def _select_mode(
    str_request_text: str,
    *,
    bool_comment_only: bool,
    target_path: str | Path | None,
) -> str:
    """选择 HLS 请求的 generate、modify 或 explain 模式。

    参数:
        str_request_text: 已规范化的用户请求文本。
        bool_comment_only: 是否命中了仅注释请求标记。
        target_path: 已知的目标路径，可能为文件、目录或空值。

    返回:
        generate、modify 或 explain 三类模式之一。
    """

    # 解释类关键词只有在没有修改和生成意图时才独占 explain 模式。
    bool_explain_requested = _contains_marker(str_request_text, EXPLAIN_MARKERS)  # 解释请求标记

    # 修改和生成关键词用于避免 explain 覆盖复合任务。
    bool_modify_requested = _contains_marker(str_request_text, MODIFY_MARKERS)  # 修改请求标记

    # 生成关键词参与解释模式的排他判断。
    bool_generate_requested = _contains_marker(str_request_text, GENERATE_MARKERS)  # 生成请求标记

    # 纯解释任务不需要进入修改或生成工作流。
    if bool_explain_requested and not bool_modify_requested and not bool_generate_requested:

        # 返回纯解释模式，调用方不应进入修改流程。
        return "explain"

    # 有目标路径、修改关键词或仅注释意图时统一走 modify。
    if bool_modify_requested or bool_comment_only or target_path:

        # 返回修改模式，后续流程会要求目标上下文。
        return "modify"

    # 其余请求默认进入生成工作流。
    return "generate"

# 目标补齐判断独立出来，避免调用方重复解释模式集合。
def _needs_target_or_code(str_mode: str, *, target_path: str | Path | None) -> bool:
    """判断当前模式是否还缺少目标路径或代码上下文。

    参数:
        str_mode: 当前已经选出的 HLS 任务模式。
        target_path: 调用方当前已知的目标文件或目录路径。

    返回:
        当前模式需要既有目标且目标为空时返回 True，否则返回 False。
    """

    # 只有修改和解释需要已有目标；生成任务可从规格继续。
    bool_requires_existing_target = str_mode in {"modify", "explain"}  # 目标依赖标记

    # 没有目标路径时，上层流程需要补齐输入。
    return bool_requires_existing_target and target_path is None

# 检查矩阵集中表达不同模式下的门禁强度。
def _check_matrix(str_mode: str, *, comment_only: bool) -> dict[str, str]:
    """构造 HLS 工作流需要执行的质量检查矩阵。

    参数:
        str_mode: 当前已经选出的 HLS 任务模式。
        comment_only: 当前任务是否仅允许改写注释。

    返回:
        上层工作流可直接消费的检查矩阵字典。
    """

    # 基础矩阵覆盖注释、命名和 HLS 可读性检查。
    dict_matrix = {
        "comment_quality_gate": "required",  # 注释质量门禁默认必跑
        "hls_ast_comment_guard": "parse-after",  # AST 守卫默认做解析后校验
        "hls_naming_gate": "required",  # HLS 命名门禁默认必跑
        "readability_gate": "required",  # HLS 可读性门禁默认必跑
    }  # 基础检查矩阵

    # 解释模式不改文件，因此多数门禁降级为建议。
    if str_mode == "explain":

        # 纯解释任务只提示检查，不强制执行。
        dict_matrix.update(
            {str_key: "recommended" for str_key in dict_matrix},
        )

    # 仅注释模式必须保留基线保护。
    if comment_only:

        # 仅注释任务需要基线 AST 保护和显式改写计划。
        dict_matrix.update(
            {
                "hls_ast_comment_guard": "required with baseline",
                "comment_rewrite_plan": "required",
            },
        )

    # 普通任务不强制生成注释改写计划。
    else:

        # 非仅注释任务把注释改写计划保留为可选项。
        dict_matrix.update(
            {"comment_rewrite_plan": "optional"},
        )

    # 返回上层工作流可直接读取的检查矩阵。
    return dict_matrix

# 基础命令拆分为独立函数，确保推荐命令字符串可单点维护。
def _base_recommended_commands() -> list[str]:
    """返回生成、修改和解释模式共享的基础命令。

    参数:
        无。

    返回:
        生成、修改和解释模式共用的命令列表。
    """

    # 可读性门禁命令用于检查 HLS 目录结构和注释命名约束。
    str_readability_command = (
        "python -m runtime.hls_generator readability-gate "
        "--target hls --path <hls-dir> --profile kernel "
        "--style current-project --json"
    )  # 可读性门禁命令

    # 静态验证命令用于无外部工具条件下完成基础合同检查。
    str_static_validate_command = (
        "python -m runtime.hls_generator validate --target hls "
        "--spec <spec.json> --path <hls-dir> --readiness static "
        "--no-external"
    )  # 静态验证命令

    # 使用列表便于仅注释模式按原有顺序插入命令。
    return [
        str_readability_command,
        str_static_validate_command,
    ]

# 注释计划命令保持独立，便于仅注释流程按固定顺序插入。
def _comment_plan_command() -> str:
    """返回仅注释改写模式的注释计划命令。

    参数:
        无。

    返回:
        仅注释改写模式使用的注释计划命令字符串。
    """

    # 注释计划命令必须带 baseline，防止语义改写混入注释任务。
    return (
        "python -m runtime.hls_generator comment-plan --target hls "
        "--path <commented-dir> --baseline-path <baseline-dir> "
        "--out reports/hls_comment_rewrite_plan.json"
    )

# 仅注释验证命令与普通验证命令分开，避免路径占位符混用。
def _comment_only_validate_command() -> str:
    """返回仅注释改写模式的静态验证命令。

    参数:
        无。

    返回:
        仅注释改写模式使用的静态验证命令字符串。
    """

    # 仅注释验证命令使用 commented-dir 和 baseline-dir 做对照。
    return (
        "python -m runtime.hls_generator validate --target hls "
        "--spec <spec.json> --path <commented-dir> "
        "--baseline-path <baseline-dir> --readiness static --no-external"
    )

# 推荐命令生成函数保持返回元组，避免外部修改内部列表。
def _recommended_commands(str_mode: str, *, comment_only: bool) -> tuple[str, ...]:
    """根据分流模式生成推荐命令元组。

    参数:
        str_mode: 当前已经选出的 HLS 任务模式。
        comment_only: 当前任务是否仅允许改写注释。

    返回:
        供上层流程展示或执行的推荐命令元组。
    """

    # 基础命令保持原有顺序，后续分支只做必要替换和追加。
    list_commands = _base_recommended_commands()  # 当前模式的推荐命令草稿

    # 仅注释模式先生成计划，再用带 baseline 的验证命令替换普通验证。
    if comment_only:

        # 注释计划命令必须排在所有验证命令之前。
        list_commands.insert(0, _comment_plan_command())

        # 带 baseline 的验证命令替换普通静态验证命令。
        str_comment_validate_command = _comment_only_validate_command()  # 仅注释验证命令

        # 保持原有命令位置，让调用方无需改解析逻辑。
        list_commands[2] = str_comment_validate_command  # 替换后的验证命令

    # 返回不可变元组，避免调用方无意修改共享决策。
    return tuple(list_commands)
