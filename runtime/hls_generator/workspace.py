"""约束 HLS 生成流程的工作区路径和 workflow 状态索引。"""

# 标准库导入集中处理 JSON、上下文变量、时间戳和路径边界。
from __future__ import annotations

# JSON 用于 workflow-state 持久化。
import json

# contextmanager 用于把局部生成器包装成标准上下文管理器。
from contextlib import contextmanager

# ContextVar 让嵌套调用可以局部覆盖工作区根目录。
from contextvars import ContextVar

# datetime/timezone 记录 workflow 事件的 UTC 时间。
from datetime import datetime, timezone

# Path 系列类型同时处理本机路径和 artifact POSIX 路径。
from pathlib import Path, PurePosixPath, PureWindowsPath

# Any、ContextManager 和 Iterator 标注 JSON 载荷与上下文管理器接口。
from typing import Any, ContextManager, Iterator

# 配置入口定义仓库根、技能根、生成目录和受保护写入目标。
from .config import generated_roots, protected_write_targets, repo_root, skill_root, workflow_state_path

# SpecError 用于把路径治理失败作为用户可见规范错误抛出。
from .spec import SpecError

# 保存当前调用上下文中的临时工作区根目录覆盖值。
context_var_workspace_root_override: ContextVar[Path | None] = ContextVar(  # 当前调用栈共享的 workspace root 覆盖槽位
    "hls_generator_workspace_root",  # ContextVar 的稳定注册名
    default=None,  # 未显式覆盖时继续使用当前进程工作目录
)  # 当前上下文中的 workspace root 覆盖值

# 返回当前 HLS 生成流程使用的工作区根目录。
def workspace_root() -> Path:
    """
    返回当前上下文解析出的工作区根目录。

    :param: 无外部业务参数，根目录来自上下文变量或当前进程目录。
    :return: 已 resolve 的工作区根目录路径。
    """

    # 读取上下文覆盖值，支持 workflow 在 run_dir 内安全执行。
    path_override = context_var_workspace_root_override.get()  # 当前上下文覆盖路径

    # 覆盖存在时优先使用调用方显式设置的工作区根目录。
    if path_override is not None:

        # 返回规范化后的覆盖路径，避免相对路径影响边界判断。
        return path_override.resolve()

    # 使用当前进程目录作为默认 workspace root。
    path_current_root = Path.cwd().resolve()  # 默认工作区根目录

    # 返回给所有路径约束函数复用。
    return path_current_root

# 临时覆盖 workspace root，供嵌套 workflow 在指定根目录下解析路径。
def use_workspace_root(root: Path) -> ContextManager[Path]:
    """
    在上下文内临时切换 workspace root。

    :param root: 调用方确认的工作区根目录。
    :return: 迭代器产出已 resolve 的根目录，退出时恢复旧上下文。
    """

    # 将调用方传入根目录规范化，保证后续 relative_to 判定稳定。
    path_resolved_root = Path(root).resolve()  # 上下文内使用的工作区根目录

    # 构造真正执行 enter/exit 的局部上下文，避免 workspace root 覆盖逻辑散落到调用方。
    def _workspace_root_scope() -> Iterator[Path]:
        """在 with 进入和退出时维护 workspace root 覆盖状态。

        参数:
            无显式业务参数；外层闭包提供已解析的 ``path_resolved_root``。

        返回:
            产出当前 with 作用域内生效的 workspace 根目录路径。
        """

        # 保存 ContextVar token，确保 finally 中能恢复外层状态。
        token_workspace_root = context_var_workspace_root_override.set(path_resolved_root)  # 上下文恢复令牌

        # 进入用户代码前暴露解析后的根目录。
        try:

            # 将生效根目录交给调用方使用。
            yield path_resolved_root

        # 无论调用方是否抛错，都恢复外层工作区根目录。
        finally:

            # 重置上下文变量，防止根目录覆盖泄漏到后续 workflow。
            context_var_workspace_root_override.reset(token_workspace_root)

    # 把局部生成器包装成标准上下文管理器后返回给调用方。
    return contextmanager(_workspace_root_scope)()

# 解析并校验路径必须留在当前工作区内。
def require_workspace_path(path: Path, *, purpose: str = "path", must_exist: bool = False) -> Path:
    """
    将路径解析到当前 workspace 内，并拒绝越界路径。

    :param path: 待解析的绝对路径或相对 workspace 的路径。
    :param purpose: 错误消息中展示的路径用途。
    :param must_exist: 是否要求目标在解析时已经存在。
    :return: 解析后的绝对路径。
    :raises SpecError: 路径不存在、无法解析或越出 workspace。
    """

    # 获取当前工作区根目录，作为所有相对路径的锚点。
    path_root = workspace_root()  # 当前 workspace 根目录

    # 绝对路径保持原样，相对路径挂到 workspace root 下。
    path_candidate = path if path.is_absolute() else path_root / path  # 待 resolve 的候选路径

    # resolve 可能因为 must_exist 或系统路径问题失败。
    try:

        # 按 must_exist 策略解析最终路径。
        path_resolved = path_candidate.resolve(strict=must_exist)  # 解析后的候选路径

    # 缺失路径需要给出用途和原始输入，便于用户修正 spec。
    except FileNotFoundError:

        # 抛出项目规范错误，阻止后续流程读取不存在输入。
        raise SpecError(f"> ERR: [Python] Missing workspace path for {purpose}: {path}") from None

    # 其他 OS 错误保留原始异常上下文。
    except OSError as error:

        # 抛出项目规范错误，提示路径解析失败的系统原因。
        raise SpecError(f"> ERR: [Python] Failed to resolve workspace path for {purpose}: {path}: {error}") from error

    # 确认解析后的路径没有越出当前 workspace root。
    try:

        # relative_to 成功即说明路径仍在 workspace 内。
        path_resolved.relative_to(path_root)

    # 越界路径必须阻断，防止读取或写入仓库外部内容。
    except ValueError as error:

        # 抛出项目规范错误，说明路径边界要求。
        raise SpecError(
            f"> ERR: [Python] Workspace path must stay inside the current workspace for {purpose}: {path}"
        ) from error

    # 返回边界检查通过的绝对路径。
    return path_resolved

# 基于某个文件或目录锚点解析工作区内路径。
def require_workspace_path_from(
    anchor: Path,
    path: Path,
    *,
    purpose: str = "path",
    must_exist: bool = False,
) -> Path:
    """
    从指定 anchor 旁边解析路径，并保持 workspace 边界。

    :param anchor: 作为相对路径起点的文件或目录。
    :param path: 待解析路径；绝对路径仍需位于 workspace 内。
    :param purpose: 错误消息中展示的路径用途。
    :param must_exist: 是否要求目标已经存在。
    :return: 解析后且通过 workspace 边界检查的路径。
    """

    # 目录 anchor 直接作为起点，文件 anchor 使用父目录。
    path_base = anchor if anchor.is_dir() else anchor.parent  # 相对路径的首选锚点

    # 绝对路径不拼接 anchor。
    if path.is_absolute():

        # 保留调用方给出的绝对候选路径，后续统一边界检查。
        path_candidate = path  # 绝对候选路径

    # 相对路径默认相对 anchor 所在目录解析。
    else:

        # 先按 anchor 邻近位置拼接候选路径。
        path_candidate = path_base / path  # anchor 相对候选路径

        # 目标必须存在但首选候选不存在时，沿 anchor 父链向上搜索。
        if must_exist and not path_candidate.exists():

            # 在 workspace 内向上查找已存在的相对路径候选。
            path_existing_candidate = _existing_relative_candidate_from(path_base, path)  # 父链命中的已存在路径

            # 命中父链候选时替换默认 anchor 相对路径。
            if path_existing_candidate is not None:

                # 使用最靠近 anchor 的已存在路径进入统一边界校验。
                path_candidate = path_existing_candidate  # 最终候选路径

    # 统一走 workspace 边界和存在性校验。
    return require_workspace_path(path_candidate, purpose=purpose, must_exist=must_exist)

# 解析可写路径并拒绝受保护源码目录。
def require_write_path(path: Path, *, purpose: str = "output path") -> Path:
    """
    校验输出路径可写且不落入受保护源码边界。

    :param path: 待写入的绝对路径或相对 workspace 的路径。
    :param purpose: 错误消息中展示的输出用途。
    :return: 可用于写入的绝对路径。
    :raises SpecError: 输出路径越界或指向受保护目录。
    """

    # 输出路径不要求提前存在，但必须能解析到 workspace 内。
    path_resolved = require_workspace_path(path, purpose=purpose, must_exist=False)  # 已解析输出路径

    # 受保护目录不能作为 workflow 输出目标。
    _reject_protected_write(path_resolved, purpose)

    # 返回通过写入边界检查的路径。
    return path_resolved

# 校验输出路径同时满足运行时配置的生成目录契约。
def require_configured_output_path(path: Path, *, purpose: str = "output path") -> Path:
    """
    校验输出路径位于当前配置允许的生成目录中。

    :param path: 待写入路径。
    :param purpose: 错误消息中展示的输出用途。
    :return: 满足写入边界和生成目录约束的路径。
    :raises SpecError: 输出不在允许的 generated_roots 中。
    """

    # 先执行普通写入边界检查。
    path_resolved = require_write_path(path, purpose=purpose)  # 已通过写入检查的输出路径

    # 只有在仓库根或技能根直接执行时，才强制检查顶层生成目录契约。
    path_workspace = workspace_root()  # 用于判断是否处在需要强制 generated_roots 的仓库级执行上下文

    # 非仓库根或技能根上下文允许 run_dir 内部自洽输出。
    if path_workspace not in {skill_root(), repo_root()}:

        # 返回已通过基础写入检查的路径。
        return path_resolved

    # 计算输出路径相对当前 workspace 的片段。
    tuple_parts = path_resolved.relative_to(path_workspace).parts  # 输出路径相对片段

    # 顶层目录必须属于配置允许的生成目录。
    if not tuple_parts or tuple_parts[0] not in generated_roots():

        # 输出目录不合规时阻断写入。
        raise SpecError(
            f"> ERR: [Python] Output path must stay under one of configured generated roots for {purpose}: "
            f"{', '.join(sorted(generated_roots()))}."
        )

    # 返回满足配置契约的输出路径。
    return path_resolved

# 校验 artifact manifest 中使用的相对 POSIX 路径。
def require_relative_artifact_path(path: str, *, purpose: str = "artifact path") -> str:
    """
    校验 artifact 路径是安全的相对 POSIX 路径。

    :param path: manifest 或 workflow 中记录的 artifact 相对路径。
    :param purpose: 错误消息中展示的路径用途。
    :return: 原始路径字符串；调用方可继续作为 manifest 键使用。
    :raises SpecError: 路径绝对、包含反斜杠、遍历片段或受保护顶层目录。
    """

    # artifact 路径必须使用 POSIX 分隔符，避免跨平台歧义。
    if "\\" in path:

        # 阻断 Windows 分隔符进入 manifest。
        raise SpecError(f"> ERR: [Python] Artifact path must use forward slashes for {purpose}: {path!r}")

    # POSIX 解析用于检查 / 和 .. 片段。
    path_posix = PurePosixPath(path)  # POSIX artifact 路径视图

    # Windows 解析用于识别驱动器和 Windows 绝对路径。
    path_windows = PureWindowsPath(path)  # 用于识别盘符和 Windows 绝对路径的视图

    # artifact 必须是相对路径，不能携带系统根或盘符。
    if path_posix.is_absolute() or path_windows.is_absolute() or path_windows.drive:

        # 阻断绝对路径进入 artifact manifest。
        raise SpecError(f"> ERR: [Python] Artifact path must stay relative for {purpose}: {path!r}")

    # 空片段、当前目录和父目录遍历都不允许。
    if any(str_part in ("", ".", "..") for str_part in path_posix.parts):

        # 阻断不安全路径片段。
        raise SpecError(f"> ERR: [Python] Artifact path contains unsafe segment for {purpose}: {path!r}")

    # 受保护目录不能作为生成 artifact 目标。
    if path_posix.parts and path_posix.parts[0] in protected_write_targets():

        # 阻断写入技能源码、参考目录或其他受保护边界。
        raise SpecError(f"> ERR: [Python] Artifact path targets protected directory for {purpose}: {path!r}")

    # 返回原始相对路径，保持 manifest 精确性。
    return path

# 写入文本内容到经过治理的输出路径。
def write_text(path: Path, text: str) -> Path:
    """
    将文本写入 workspace 内允许的输出路径。

    :param path: 输出文件路径。
    :param text: 需要按 UTF-8 写入的文本内容。
    :return: 实际写入的绝对路径。
    """

    # 校验输出路径边界并得到绝对路径。
    path_output = require_write_path(path)  # 已校验文本输出路径

    # 创建父目录，确保后续 write_text 不因目录缺失失败。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 按项目约定使用 UTF-8 写入文本。
    path_output.write_text(text, encoding="utf-8")

    # 返回写入位置，便于调用方记录 artifact。
    return path_output

# 写入 JSON 对象到经过治理的输出路径。
def write_json(path: Path, data: dict[str, Any]) -> Path:
    """
    将 JSON 对象写入 workspace 内允许的输出路径。

    :param path: 输出 JSON 文件路径。
    :param data: 需要序列化的 JSON 对象。
    :return: 实际写入的绝对路径。
    """

    # 序列化为稳定缩进的 UTF-8 JSON 文本。
    str_json_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"  # 待写入 JSON 文本

    # 复用文本写入路径治理和文件 IO。
    return write_text(path, str_json_text)

# 追加 workflow 事件并维护按事件类型分组的索引。
def update_workflow_state(
    state_path: Path | None,
    event: str,
    payload: dict[str, Any],
    *,
    enabled: bool = True,
) -> None:
    """
    将 workflow 事件写入状态文件并更新派生索引。

    :param state_path: 自定义状态文件路径；为空时使用运行时默认路径，dtype=Path 或 None，unit=workspace path。
    :param event: workflow 事件名称，dtype=str，unit=dimensionless。
    :param payload: 事件载荷，shape=(n fields)，dtype=dict[str, Any]，unit=JSON-like workflow metadata；路径会脱敏为 workspace 相对路径。
    :param enabled: 为 False 时跳过写入，dtype=bool，unit=dimensionless，便于测试或禁用状态记录。
    :return: 无业务返回值；状态文件作为副作用更新。
    """

    # 调用方显式禁用状态记录时立即返回。
    if not enabled:

        # 保持无副作用行为。
        return

    # 解析状态文件路径，缺省使用运行时配置中的 workflow state。
    path_state = require_write_path(state_path or workflow_state_path(), purpose="workflow state path")  # 状态文件路径

    # 读取或初始化 workflow state JSON。
    dict_state = _read_state(path_state)  # 当前 workflow 状态对象

    # 组装事件记录并对载荷中的 Path 做相对化。
    dict_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),  # 当前事件写入时的 UTC 时间戳
        "event": event,  # 当前 workflow 事件名称
        **_sanitize(payload),  # 已脱敏的事件载荷字段
    }  # 当前 workflow 事件记录

    # 将事件追加到完整事件流。
    dict_state.setdefault("events", []).append(dict_record)

    # 根据事件类型同步维护派生索引。
    _index_payload(dict_state, event, dict_record)

    # 确保状态文件父目录存在。
    path_state.parent.mkdir(parents=True, exist_ok=True)

    # 写入排序后的 JSON，便于版本管理和人工审查。
    path_state.write_text(
        json.dumps(dict_state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

# 返回缺失状态文件时使用的默认 workflow state 骨架。
def _empty_workflow_state() -> dict[str, Any]:
    """
    返回缺省 workflow state，供首次写入前直接追加事件和索引。

    :param: 无显式业务参数；该骨架由运行时在状态文件缺失时直接复用。
    :return: 包含 events/plans/traces 等全部索引桶的可写 state 字典。
    """

    # 返回首个 workflow-state.json 写入前使用的完整索引骨架。
    return {
        "version": 1,  # workflow state JSON 版本号
        "evidence": [],  # 证据类事件索引
        "summaries": [],  # 摘要类事件索引
        "plans": [],  # 计划与工作流推进事件索引
        "artifact_manifests": [],  # 产物清单相关事件索引
        "validation_reports": [],  # 验证报告类事件索引
        "traces": [],  # trace 路径或标识索引
        "prompt_memory": [],  # prompt 优化记忆索引
        "human_interventions": [],  # 人工介入类事件索引
        "events": [],  # 原始事件流
    }

# 读取 workflow state，缺失时返回空状态骨架。
def _read_state(path: Path) -> dict[str, Any]:
    """
    读取 workflow state JSON，并补齐必须存在的列表字段。

    :param path: workflow state 文件路径。
    :return: 可安全追加事件和索引的状态对象。
    :raises SpecError: 文件不是 JSON 对象或 JSON 解析失败。
    """

    # 状态文件尚未创建时返回初始结构。
    if not path.exists():

        # 目标 state 文件不存在时，直接返回可安全追加事件与索引的默认骨架。
        return _empty_workflow_state()

    # 读取并解析已有状态文件。
    try:

        # JSON 文本必须使用 UTF-8。
        dict_loaded = json.loads(path.read_text(encoding="utf-8"))  # 已解析 workflow 状态

    # JSON 语法错误需要阻断状态更新，避免覆盖坏文件。
    except json.JSONDecodeError as error:

        # 抛出项目规范错误，保留 JSON 解析位置。
        raise SpecError(f"> ERR: [Python] Workflow state JSON is invalid at {path}: {error}") from error

    # 状态根必须是 JSON object。
    if not isinstance(dict_loaded, dict):

        # 阻断非对象状态，防止后续索引写入失败。
        raise SpecError(f"> ERR: [Python] Workflow state root must be a JSON object: {path}")

    # 旧状态可能缺少 version 字段，需要补默认值。
    dict_loaded.setdefault("version", 1)

    # 所有索引桶都补齐为空列表，保证 append 调用安全。
    for str_key in (
        "evidence",
        "summaries",
        "plans",
        "artifact_manifests",
        "validation_reports",
        "traces",
        "prompt_memory",
        "human_interventions",
        "events",
    ):

        # 缺失索引桶保持向后兼容。
        dict_loaded.setdefault(str_key, [])

    # 返回补齐后的状态对象。
    return dict_loaded

# 在 anchor 父链内查找第一个存在的相对路径候选。
def _existing_relative_candidate_from(path_base: Path, path_relative: Path) -> Path | None:
    """
    沿 anchor 父链寻找已存在的相对路径。

    :param path_base: anchor 文件所在目录或 anchor 目录。
    :param path_relative: 需要沿父链查找的相对路径。
    :return: 命中的已存在候选路径；未命中时返回 None。
    """

    # 获取 workspace root，限制向上搜索不能越出工作区。
    path_root = workspace_root()  # 限制父链搜索不能越界的 workspace 根目录

    # 构造从 anchor 到根部的搜索序列。
    list_search_roots = [path_base, *path_base.parents]  # 候选搜索目录序列

    # 逐级查找第一个存在的相对路径候选。
    for path_search_root in list_search_roots:

        # 越出 workspace 的父目录不参与候选拼接。
        if not _is_within_workspace(path_search_root, path_root):

            # 继续检查更靠近 workspace 内部的其他候选。
            continue

        # 生成当前搜索根下的候选目标。
        path_resolved_candidate = path_search_root / path_relative  # 当前父级搜索候选路径

        # 找到存在的候选时交给上层做最终边界校验。
        if path_resolved_candidate.exists():

            # 返回最靠近 anchor 的命中候选，避免更外层路径覆盖近邻结果。
            return path_resolved_candidate

    # 没有命中时保留调用方原始候选。
    return None

# 判断搜索根是否仍落在当前 workspace 内部。
def _is_within_workspace(path_candidate: Path, path_root: Path) -> bool:
    """
    判断候选路径是否位于 workspace root 内。

    :param path_candidate: 需要检查的候选目录。
    :param path_root: 当前 workspace root。
    :return: True 表示候选目录没有越出 workspace。
    """

    # relative_to 成功即说明候选目录仍在 workspace 内。
    try:

        # 当前搜索根通过边界检查后才允许用于拼接。
        path_candidate.resolve().relative_to(path_root)

    # 越出 workspace 的路径不能参与父链搜索。
    except ValueError:

        # 返回 False 让调用方跳过该候选。
        return False

    # 候选目录可安全用于拼接相对路径。
    return True

# 根据 workflow 事件名称更新对应的状态索引桶。
def _index_payload(state: dict[str, Any], event: str, record: dict[str, Any]) -> None:
    """
    把事件记录同步写入 workflow state 的派生索引。

    :param state: 正在更新的 workflow state 对象。
    :param event: 当前事件名称。
    :param record: 已追加到 events 的事件记录。
    :return: 无业务返回值；state 会被原地更新。
    """

    # 事件名称到索引桶名称的映射保持 workflow 查询稳定。
    dict_event_buckets = {  # workflow 事件到派生索引桶的稳定路由表
        "ingest_spec": "evidence",  # spec 读入事件归档到证据索引
        "decompose": "plans",  # 计划分解事件归档到计划索引
        "prompt": "artifact_manifests",  # prompt 产物事件归档到 artifact 清单索引
        "model_generate": "artifact_manifests",  # 模型生成产物事件归档到 artifact 清单索引
        "extract": "artifact_manifests",  # 提取结果事件归档到 artifact 清单索引
        "validate": "validation_reports",  # 校验结果事件归档到验证报告索引
        "reflect": "plans",  # 反思与重规划事件归档到计划索引
        "optimize_prompt": "prompt_memory",  # prompt 优化事件归档到 prompt 记忆索引
        "eval": "validation_reports",  # 单次评测事件归档到验证报告索引
        "eval_suite": "validation_reports",  # 评测集事件归档到验证报告索引
        "human_intervention": "human_interventions",  # 人工介入事件归档到人工介入索引
        "resolve_intervention": "human_interventions",  # 已解决的人工介入事件继续归档到人工介入索引，便于回看处置闭环
        "audit_interface": "artifact_manifests",  # 接口审计事件归档到 artifact 清单索引
        "verify_stage": "validation_reports",  # 分阶段验证事件归档到验证报告索引
        "optimize_hls_prompt": "prompt_memory",  # HLS 专项提示词调优事件沉淀到 prompt 记忆索引，供后续同类 run 复用
        "run_workflow": "plans",  # workflow 新建运行事件归档到计划索引
        "resume_workflow": "plans",  # workflow 恢复运行事件归档到计划索引
        "workflow_attempt": "validation_reports",  # workflow 尝试轮次事件归档到验证报告索引
    }  # workflow 事件到索引桶的映射

    # 查找当前事件所属索引桶。
    str_bucket = dict_event_buckets.get(event)  # 当前事件索引桶名称

    # 有索引桶的事件需要同步进派生列表。
    if str_bucket:

        # 将记录追加到对应索引桶，便于后续按类型检索。
        state.setdefault(str_bucket, []).append(record)

    # trace 字段额外进入 traces 索引。
    if record.get("trace"):

        # trace 索引用于跨阶段追踪同一生成流程。
        state.setdefault("traces", []).append(record["trace"])

# 拒绝 workflow 输出写入受保护的技能源码目录。
def _reject_protected_write(path: Path, purpose: str) -> None:
    """
    检查输出路径是否落入受保护顶层目录。

    :param path: 已解析的输出路径。
    :param purpose: 错误消息中展示的输出用途。
    :return: 无业务返回值；违规时抛出 SpecError。
    """

    # 取当前 workspace root 作为相对路径基准。
    path_root = workspace_root()  # 用于识别受保护顶层目录的相对化基准

    # 尝试计算相对片段；越界路径已经由上游检查，这里仍保留兜底。
    try:

        # workspace 内路径用相对片段检查顶层目录。
        tuple_parts = path.relative_to(path_root).parts  # workspace 内相对片段

    # 理论越界路径使用原始 parts，保证错误仍能被发现。
    except ValueError:

        # 兜底使用绝对路径片段。
        tuple_parts = path.parts  # 路径原始片段

    # 顶层命中受保护目录时阻断写入。
    if tuple_parts and tuple_parts[0] in protected_write_targets():

        # 直接抛出带目录名的边界错误，避免生成物污染技能源码树。
        raise SpecError(
            f"> ERR: [Python] Protected output path for {purpose}: top-level target "
            f"{tuple_parts[0]!r} is read-only."
        )

# 递归脱敏状态载荷中的路径对象。
def _sanitize(value: Any) -> Any:
    """
    将状态载荷中的 Path 转成安全字符串，并递归处理容器。

    :param value: 任意 JSON-like 载荷值。
    :return: 脱敏后的载荷值。
    """

    # Path 值需要转换为 workspace 相对路径或外部占位。
    if isinstance(value, Path):

        # 返回安全路径字符串。
        return _safe_path(value)

    # 字典需要递归处理每个值。
    if isinstance(value, dict):

        # 保留原 key，仅清洗 value。
        return {str_key: _sanitize(item_value) for str_key, item_value in value.items()}

    # 列表需要逐项清洗。
    if isinstance(value, list):

        # 返回新的清洗后列表。
        return [_sanitize(item_value) for item_value in value]

    # 元组按 JSON 数组语义转换为列表。
    if isinstance(value, tuple):

        # 返回符合 JSON 语义的清洗后列表表示。
        return [_sanitize(item_value) for item_value in value]

    # 其他 JSON 标量原样保留。
    return value

# 将路径转成 workflow state 中安全保存的字符串。
def _safe_path(path: Path) -> str:
    """
    将 Path 转换为不泄漏本机绝对路径的状态字符串。

    :param path: 待记录的路径对象。
    :return: workspace 相对 POSIX 路径，或外部路径占位。
    """

    # 当前 workspace root 用于相对化本地路径。
    path_root = workspace_root()  # 用于脱敏本地绝对路径的 workspace 基准目录

    # workspace 内路径可以安全记录为相对 POSIX 路径。
    try:

        # 返回相对路径，避免保存本机绝对路径。
        return path.resolve().relative_to(path_root).as_posix()

    # workspace 外路径只保留文件名，避免泄露本机目录结构。
    except ValueError:

        # 外部路径使用固定占位前缀。
        return f"<external>/{path.name}"
