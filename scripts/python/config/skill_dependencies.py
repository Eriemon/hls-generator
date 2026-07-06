"""技能依赖发现、阻断请求和安装辅助逻辑。"""
# 启用前向注解，避免类型提示在运行时过早求值。
from __future__ import annotations

# 标准库依赖用于文件系统扫描、进程调用和 JSON 编解码。
import json
import os
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

# 统一描述单条依赖或检查结果的字典结构。
DependencyRecord = dict[str, Any]  # 依赖记录通用别名

# 统一描述技能名称到候选安装记录的索引结构。
SkillIndex = dict[str, list[DependencyRecord]]  # 技能名称索引映射

# 标记依赖已经满足。
DEPENDENCY_OK = "ok"  # 依赖满足状态

# 标记依赖尚未安装。
DEPENDENCY_MISSING = "missing"  # 依赖缺失状态

# 标记依赖已安装但内容无效。
DEPENDENCY_INVALID = "invalid"  # 依赖无效状态

# 标记至少一个必需依赖阻断当前流程。
BLOCKED_DEPENDENCY = "blocked_dependency"  # 依赖阻断状态

# 在必需依赖缺失或失效时抛出统一异常。
class SkillDependencyError(RuntimeError):
    """
    表示当前技能依赖无法满足执行前提的异常。

    :param message: 面向 CLI 与用户的短错误消息。
    :param report: 机器可读的依赖检查报告。
    :return: 无业务返回值；实例化时会保存报告并构造异常消息。
    """

    # 基于依赖报告构造阻断异常。
    def __init__(self, message: str, report: DependencyRecord) -> None:
        """
        保存依赖报告并生成阻断错误文本。

        :param message: 面向 CLI 与用户的短错误消息。
        :param report: 需要传递给上层调用方的依赖报告。
        :return: 无业务返回值。
        """

        # 保存原始依赖报告供 CLI 或上层流程继续输出。
        self.report = report  # 当前阻断异常对应的依赖报告

        # 复用统一格式化逻辑生成异常文本，并在需要时补上短错误摘要。
        str_error_message = _format_blocked_error(report)  # 结构化依赖阻断报文

        # 这里把短错误摘要和结构化依赖报告一起交给 RuntimeError。
        super().__init__(f"{message}\n{str_error_message}")

# 检查传入依赖配置在当前技能目录和插件缓存中是否满足。
def check_skill_dependencies(
    dependencies: list[DependencyRecord] | None,
    *,
    skill_dirs: list[Path] | None = None,
    plugin_cache_dirs: list[Path] | None = None,
    scopes: list[str] | tuple[str, ...] | set[str] | None = None,
) -> DependencyRecord:
    """
    生成机器可读的技能依赖状态报告。

    :param dependencies: 运行时配置中的依赖列表；为 `None` 时按空列表处理。
    :param skill_dirs: 显式指定的技能搜索目录；为空时使用默认技能目录。
    :param plugin_cache_dirs: 显式指定的插件缓存目录；为空时使用默认插件缓存目录。
    :param scopes: 需要检查的作用域集合；为空时检查全部作用域。
    :return: 包含整体状态、扫描目录和逐项依赖结果的报告字典。
    """

    # 规范化调用方请求的作用域集合，便于后续统一过滤。
    set_requested_scopes = _normalize_requested_scopes(scopes)  # 请求的作用域集合

    # 收集真正需要进入检查流程的依赖条目。
    list_dependencies: list[DependencyRecord] = []  # 作用域过滤后的依赖列表

    # 逐条规范化依赖配置并应用作用域过滤条件。
    for dict_item in dependencies or []:

        # 将原始配置条目转成完整且受校验的依赖字典。
        dependency_record_candidate = _normalize_dependency(dict_item)  # 规范化后的依赖条目

        # 跳过与当前请求作用域无关的依赖配置。
        if not _dependency_matches_scopes(
            dependency_record_candidate,
            set_requested_scopes,
        ):

            # 当前依赖和请求作用域无交集时直接跳过，避免无关条目污染后续报告。
            continue

        # 将当前命中的依赖条目加入后续检查列表，供后续安装索引逐条核对。
        list_dependencies.append(dependency_record_candidate)

    # 建立已安装技能索引，供单条依赖检查共享。
    skill_index_installed: SkillIndex = _discover_installed_skills(  # 当前环境的已安装技能索引
        skill_dirs=skill_dirs,  # 本轮依赖检查显式指定的技能目录
        plugin_cache_dirs=plugin_cache_dirs,  # 本轮依赖检查显式指定的插件缓存目录
    )

    # 收集每一条依赖的检查结果。
    list_reports: list[DependencyRecord] = []  # 单条依赖检查结果列表

    # 逐条检查规范化后的依赖是否已经满足。
    for dict_dependency in list_dependencies:

        # 生成单条依赖的安装、缺失和失效报告。
        dependency_record_report = _check_one(  # 单条依赖报告
            dict_dependency,  # 当前准备判定状态的依赖条目
            skill_index_installed,  # 当前环境里已发现的技能安装索引
        )

        # 记录当前依赖的检查结果，供最终报告统一返回。
        list_reports.append(dependency_record_report)

    # 提取真正会阻断执行流程的必需依赖。
    list_blocked_reports: list[DependencyRecord] = []  # 触发阻断状态的依赖列表

    # 遍历单条依赖结果并筛出 required 且 blocking 的失败项。
    for dict_dependency_report in list_reports:

        # 仅当依赖既是必需项又未满足时才进入阻断集合。
        if (
            dict_dependency_report["blocking"]
            and dict_dependency_report["level"] == "required"
            and dict_dependency_report["status"] != DEPENDENCY_OK
        ):

            # 将当前阻断项加入最终的必需依赖失败列表。
            list_blocked_reports.append(dict_dependency_report)

    # 解析最终报告需要记录的技能搜索目录。
    list_skill_dirs = _resolve_skill_dirs(skill_dirs)  # 报告中的技能目录列表

    # 解析最终报告需要记录的插件缓存目录。
    list_plugin_cache_dirs = _resolve_plugin_cache_dirs(plugin_cache_dirs)  # 报告中的插件缓存目录列表

    # 组装并返回完整的机器可读依赖报告。
    return {
        "version": 1,
        "status": BLOCKED_DEPENDENCY if list_blocked_reports else DEPENDENCY_OK,
        "scopes": sorted(set_requested_scopes) if set_requested_scopes else ["all"],
        "skills_dirs": [str(path_skill_dir) for path_skill_dir in list_skill_dirs],
        "plugin_cache_dirs": [str(path_cache_dir) for path_cache_dir in list_plugin_cache_dirs],
        "dependencies": list_reports,
    }

# 按技能名或别名查找当前已安装的技能记录。
def find_installed_skill(
    name: str,
    *,
    aliases: list[str] | tuple[str, ...] | set[str] | None = None,
    skill_dirs: list[Path] | None = None,
    plugin_cache_dirs: list[Path] | None = None,
) -> DependencyRecord | None:
    """
    在本地技能目录与插件缓存中查找已安装技能。

    :param name: 优先匹配的技能名称。
    :param aliases: 允许参与匹配的别名集合。
    :param skill_dirs: 显式指定的技能搜索目录；为空时使用默认技能目录。
    :param plugin_cache_dirs: 显式指定的插件缓存目录；为空时使用默认插件缓存目录。
    :return: 找到时返回技能记录副本；未找到时返回 `None`。
    """

    # 建立当前环境的技能索引供名称解析复用。
    skill_index_installed: SkillIndex = _discover_installed_skills(  # 名称解析复用的已安装技能索引
        skill_dirs=skill_dirs,  # 名称查找时显式指定的技能目录
        plugin_cache_dirs=plugin_cache_dirs,  # 名称查找时显式指定的插件缓存目录
    )

    # 使用主名称和别名从索引中寻找命中的技能记录。
    return _find_skill(name, set(aliases or []), skill_index_installed)

# 在依赖不满足时直接抛出阻断异常，供上层流程快速失败。
def require_skill_dependencies(
    dependencies: list[DependencyRecord] | None,
    *,
    scopes: list[str] | tuple[str, ...] | set[str] | None = None,
) -> DependencyRecord:
    """
    检查技能依赖并在阻断时抛出异常。

    :param dependencies: 运行时配置中的依赖列表；为 `None` 时按空列表处理。
    :param scopes: 需要检查的作用域集合；为空时检查全部作用域。
    :return: 未阻断时返回完整依赖报告。
    """

    # 生成依赖检查报告供调用方继续判断。
    dependency_record_report = check_skill_dependencies(  # 当前作用域的依赖报告
        dependencies,  # 当前调用方传入的依赖配置
        scopes=scopes,  # 当前调用方请求检查的作用域集合
    )

    # 在存在阻断依赖时抛出带完整载荷的专用异常。
    if dependency_record_report["status"] == BLOCKED_DEPENDENCY:

        # 抛出统一异常，确保上层可以直接读取 JSON 风格报文。
        raise SkillDependencyError(
            "> ERR: [Python] Required skill dependencies are missing or invalid.",
            dependency_record_report,
        )

    # 返回未阻断的依赖报告供调用方继续使用。
    return dependency_record_report

# 将阻断依赖报告转成面向安装确认流程的请求载荷。
def build_dependency_request(report: DependencyRecord) -> DependencyRecord:
    """
    生成提示用户安装或修复依赖的请求字典。

    :param report: `check_skill_dependencies` 生成的依赖状态报告。
    :return: 包含推荐命令和阻断依赖列表的请求载荷。
    """

    # 收集真正阻断执行的必需依赖条目。
    list_blocked_dependencies: list[DependencyRecord] = []  # 需要安装或修复的依赖列表

    # 从依赖报告中筛出 required 且未满足的依赖。
    for dict_dependency_report in report.get("dependencies", []):

        # 仅保留会阻断当前能力路径的依赖结果。
        if (
            dict_dependency_report.get("blocking")
            and dict_dependency_report.get("level") == "required"
            and dict_dependency_report.get("status") != DEPENDENCY_OK
        ):
            
            # 将当前阻断依赖纳入安装请求载荷。
            list_blocked_dependencies.append(dict_dependency_report)

    # 返回供 CLI 和异常载荷共同复用的安装请求结构。
    return {
        "version": 1,
        "action": "ask_install_skill_dependencies",
        "status": BLOCKED_DEPENDENCY if list_blocked_dependencies else DEPENDENCY_OK,
        "question": (
            "Required HLSGenerator skill dependencies are missing or invalid. "
            "Ask the user whether to install or repair them before continuing."
        ),
        "missing_or_invalid": list_blocked_dependencies,
        "recommended_commands": [
            "python -m scripts.python.cli.hls_generator deps check --json",
            "python -m scripts.python.cli.hls_generator deps request --out reports/skill_dependency_request.json",
            "python -m scripts.python.cli.hls_generator deps install --all",
        ],
        "restart_required": (
            "Restart Codex after installing new skills so trigger metadata is reloaded."
        ),
        # 说明必需依赖与推荐依赖对流程阻断的不同影响。
        "policy": (
            "Only required dependencies block the matching capability path; "
            "recommended dependencies produce warnings."
        ),
        "scopes": report.get("scopes", ["all"]),
    }

# 按指定依赖 ID 或全部模式安装缺失的技能依赖。
def install_skill_dependencies(
    dependencies: list[DependencyRecord],
    *,
    ids: list[str] | None = None,
    install_all: bool = False,
    dest_root: Path | None = None, skill_dirs: list[Path] | None = None,
    plugin_cache_dirs: list[Path] | None = None,
) -> DependencyRecord:
    """
    安装选定的技能依赖到目标技能目录。

    :param dependencies: 运行时配置中的依赖列表。
    :param ids: 需要安装的依赖 ID 列表；仅在 `install_all` 为 `False` 时生效。
    :param install_all: 是否安装当前配置命中的全部依赖。
    :param dest_root: 技能安装目标根目录；为空时使用默认技能目录。
    :param skill_dirs: 显式指定的技能搜索目录；为空时使用默认技能目录。
    :param plugin_cache_dirs: 显式指定的插件缓存目录；为空时使用默认插件缓存目录。
    :return: 包含安装结果、跳过项和修复建议的报告字典。
    异常:
        ValueError: 当未选择任何依赖、依赖配置不合法或未命中可安装依赖时抛出。
    """

    # 拒绝没有选择条件的安装请求，避免误执行空操作。
    if not install_all and not ids:

        # 明确提示调用方必须提供 --all 或至少一个依赖 ID。
        raise ValueError(
            "> ERR: [Python] Dependency installation requires --all or at least one --ids value."
        )

    # 先统一拿到规范化依赖列表与本轮命中的依赖列表。
    tuple_dependency_selection = _normalize_install_dependencies(  # 规范化依赖与命中依赖的二元组
        dependencies,  # 当前运行时配置中的依赖列表
        ids,  # 调用方显式指定需要安装的依赖 ID 列表
        install_all,  # 是否直接安装当前命中的全部依赖
    )

    # 取出供预检查共享的规范化依赖列表。
    list_normalized_dependencies = tuple_dependency_selection[0]  # 规范化后的依赖列表

    # 取出本轮真正需要执行安装规划的依赖列表。
    list_selected_dependencies = tuple_dependency_selection[1]  # 需要执行安装规划的依赖列表

    # 在没有任何命中依赖时立即报告配置或入参问题。
    if not list_selected_dependencies:

        # 明确提示调用方没有选中任何可安装依赖。
        raise ValueError(
            "> ERR: [Python] No matching skill dependencies were selected for installation."
        )

    # 统一解析安装目标目录并生成安装前检查上下文。
    tuple_install_context = _dependency_install_context(  # 安装目录与检查结果组成的上下文三元组
        list_normalized_dependencies,  # 规范化后的依赖配置列表
        dest_root=dest_root,  # 调用方指定的安装目标根目录
        skill_dirs=skill_dirs,  # 本地技能目录搜索根列表
        plugin_cache_dirs=plugin_cache_dirs,  # 插件缓存目录搜索根列表
    )

    # 取出最终的技能安装目标目录。
    path_destination = tuple_install_context[0]  # 依赖安装根目录

    # 取出安装前的依赖检查报告。
    dependency_record_report = tuple_install_context[1]  # 安装前的依赖检查报告

    # 取出依赖 ID 到检查结果的映射。
    dict_report_by_id = tuple_install_context[2]  # 依赖 ID 到检查结果的映射

    # 先拿到动作规划三元组，再拆成最终安装流程要消费的三个列表。
    tuple_install_actions = _plan_dependency_install_actions(  # 当前依赖安装流程的动作规划三元组
        list_selected_dependencies,  # 本轮真正需要处理的依赖列表
        dict_report_by_id,  # 依赖 ID 到检查结果的快速索引
    )

    # 取出无需安装的已满足依赖列表。
    list_skipped = tuple_install_actions[0]  # 已满足而跳过安装的依赖列表

    # 取出已安装但内容失效、需要人工修复的依赖列表。
    list_repair_required = tuple_install_actions[1]  # 需要修复的依赖列表

    # 取出真正需要执行安装的依赖计划。
    list_install_plan = tuple_install_actions[2]  # 待安装依赖及缺失技能名

    # 执行实际安装计划并收集每条依赖的安装结果。
    list_installed_results = _install_dependency_plan(  # 已执行安装的结果列表
        list_install_plan,  # 待执行复制或修复的依赖计划
        path_destination,  # 最终执行安装的目标技能目录
    )

    # 收集由 alternative provider 满足而无需复制的技能映射。
    list_install_skipped = _collect_install_skipped(  # 由替代提供者满足的技能列表
        dependency_record_report["dependencies"],  # 安装前依赖检查返回的结果列表
        list_selected_dependencies,  # 本轮实际命中的依赖配置列表
    )

    # 计算最终安装结果状态，优先反映需要修复的情形。
    str_status = _resolve_install_status(  # 当前安装流程的总体状态
        list_repair_required,  # 已安装但仍需人工修复的依赖
        list_installed_results,  # 本轮真正完成安装的结果列表
    )

    # 返回完整安装结果供 CLI、日志和上层流程复用。
    return _install_dependency_result(
        str_status=str_status,
        path_destination=path_destination,
        list_installed_results=list_installed_results,
        list_skipped=list_skipped,
        list_install_skipped=list_install_skipped,
        list_repair_required=list_repair_required,
    )

# 校验运行时配置中的 skill_dependencies 字段格式。
def validate_skill_dependency_config(dependencies: Any) -> list[DependencyRecord]:
    """
    校验运行时依赖配置并返回规范化结果。

    :param dependencies: 配置中的 `skill_dependencies` 原始值。
    :return: 规范化后的依赖列表；当原值为 `None` 时返回空列表。
    异常:
        ValueError: 当 `skill_dependencies` 不是列表或其中条目不合法时抛出。
    """

    # 将缺省配置视为没有外部技能依赖。
    if dependencies is None:

        # 对缺省配置返回空依赖列表，表示无需外部技能支持。
        return []

    # 拒绝非列表结构，避免后续流程误把标量当作依赖集合。
    if not isinstance(dependencies, list):

        # 明确指出运行时配置字段必须是列表。
        raise ValueError("> ERR: [Python] Runtime config skill_dependencies must be a list.")

    # 逐条规范化依赖配置并返回结果。
    return [_normalize_dependency(dict_item) for dict_item in dependencies]

# 按 install_all 或显式依赖 ID 过滤本轮要处理的依赖条目。
def _select_dependencies_for_install(
    dependencies: list[DependencyRecord],
    requested_ids: set[str],
    install_all: bool,
) -> list[DependencyRecord]:
    """
    过滤本轮真正需要参与安装规划的依赖条目。

    :param dependencies: 已规范化的依赖配置列表。
    :param requested_ids: 调用方显式请求的依赖 ID 集合。
    :param install_all: 是否安装当前配置命中的全部依赖。
    :return: 通过安装条件筛选后的依赖列表。
    """

    # install_all 为真时直接保留全部依赖；否则按显式 ID 过滤。
    return [
        dict_dependency
        for dict_dependency in dependencies
        if install_all or dict_dependency["id"] in requested_ids
    ]

# 规范化依赖配置并筛选本轮要安装的依赖列表。
def _normalize_install_dependencies(
    dependencies: list[DependencyRecord],
    ids: list[str] | None,
    install_all: bool,
) -> tuple[list[DependencyRecord], list[DependencyRecord]]:
    """
    规范化依赖配置并筛选本轮真正命中的安装条目。

    :param dependencies: 运行时配置中的原始依赖列表。
    :param ids: 调用方显式请求的依赖 ID 列表。
    :param install_all: 是否安装当前配置命中的全部依赖。
    :return: 规范化依赖列表与命中依赖列表组成的二元组。
    """

    # 规范化调用方显式请求的依赖 ID 集合。
    set_requested_ids = set(ids or [])  # 需要参与安装筛选的依赖 ID 集合

    # 先把依赖配置全部转成统一结构，供安装与预检查共享。
    list_normalized_dependencies = [
        _normalize_dependency(dict_item)  # 把单条原始依赖补齐成统一键集合
        for dict_item in dependencies  # 遍历调用方传入的每一条待安装依赖配置
    ]  # 供安装筛选与安装前预检共同复用的完整依赖列表

    # 再按 install_all 或显式依赖 ID 过滤真正要处理的条目。
    list_selected_dependencies = _select_dependencies_for_install(  # 按 install_all 与显式 ID 过滤后的待安装依赖集合
        list_normalized_dependencies,  # 全量规范化依赖供筛选函数判断可安装候选
        set_requested_ids,  # 调用方显式点名的依赖 ID 集合
        install_all,  # 是否绕过 ID 过滤并纳入全部命中依赖
    )  # 本轮真正会进入安装规划的依赖条目

    # 返回规范化依赖列表与命中依赖列表。
    return list_normalized_dependencies, list_selected_dependencies

# 解析安装目标目录并生成安装前检查上下文。
def _dependency_install_context(
    dependencies: list[DependencyRecord],
    *,
    dest_root: Path | None,
    skill_dirs: list[Path] | None,
    plugin_cache_dirs: list[Path] | None,
) -> tuple[Path, DependencyRecord, dict[str, DependencyRecord]]:
    """
    解析安装目标目录并生成依赖安装前检查上下文。

    :param dependencies: 需要参与安装前检查的规范化依赖列表。
    :param dest_root: 技能安装目标根目录；为空时使用默认技能目录。
    :param skill_dirs: 显式指定的技能搜索目录。
    :param plugin_cache_dirs: 显式指定的插件缓存目录。
    :return: 安装目标目录、安装前检查报告与 ID 映射组成的三元组。
    """

    # 解析最终的技能安装目标目录。
    path_destination = (dest_root or _default_install_root()).expanduser().resolve()  # 本轮准备写入技能副本的最终目标根目录

    # 确保安装目标目录存在，便于后续复制技能内容。
    path_destination.mkdir(parents=True, exist_ok=True)

    # 预先扫描当前环境，避免对已满足或已损坏依赖重复安装。
    dependency_record_report = check_skill_dependencies(  # 安装前依赖检查结果
        dependencies,  # 本轮待核查的依赖配置列表
        skill_dirs=skill_dirs,  # 本地技能目录搜索根
        plugin_cache_dirs=plugin_cache_dirs,  # 插件缓存目录搜索根
    )

    # 建立依赖 ID 到检查结果的映射，方便后续按依赖查状态。
    dict_report_by_id: dict[str, DependencyRecord] = {
        report["id"]: report  # 单个依赖 ID 对应的检查记录
        for report in dependency_record_report["dependencies"]  # 逐条读取安装前扫描得到的依赖状态
    }

    # 返回安装目录、检查报告与 ID 映射三元组。
    return path_destination, dependency_record_report, dict_report_by_id

# 把选中依赖划分为跳过、修复和真正安装三类动作。
def _plan_dependency_install_actions(
    selected_dependencies: list[DependencyRecord],
    report_by_id: dict[str, DependencyRecord],
) -> tuple[
    list[DependencyRecord],
    list[DependencyRecord],
    list[tuple[DependencyRecord, list[str]]],
]:
    """
    根据安装前检查结果规划依赖安装动作。

    :param selected_dependencies: 本轮选中的依赖配置列表。
    :param report_by_id: 依赖 ID 到检查结果的映射。
    :return: 跳过列表、修复列表与真正安装计划三元组。
    """

    # 收纳 status=ok 的依赖，最终报告会直接输出 already_installed 跳过原因。
    list_skipped: list[DependencyRecord] = []  # 已满足依赖的跳过摘要

    # 收纳 status=invalid 的本地副本，提醒调用方先修复目录内容再重试安装。
    list_repair_required: list[DependencyRecord] = []  # 校验失真的本地副本摘要

    # 保存“依赖记录 + 缺失技能名”组合，供执行阶段逐项 clone 仓库并复制技能目录。
    list_install_plan: list[tuple[DependencyRecord, list[str]]] = []  # 待执行安装的依赖计划

    # 按选中依赖逐条决定是跳过、修复还是安装。
    for dict_dependency in selected_dependencies:

        # 取出该依赖在安装前检查中的状态记录。
        dict_existing_report = report_by_id.get(dict_dependency["id"])  # 当前依赖的已有检查结果

        # 对已经满足的依赖直接登记跳过结果。
        if dict_existing_report and dict_existing_report["status"] == DEPENDENCY_OK:

            # 记录已经满足的依赖，避免重复下载和复制。
            list_skipped.append(
                {
                    "id": dict_dependency["id"],
                    "reason": "already_installed",
                }
            )

            # 当前依赖已经满足，不再进入修复或安装分支。
            continue

        # 对已安装但内容不完整的依赖登记修复要求。
        if dict_existing_report and dict_existing_report["status"] == DEPENDENCY_INVALID:

            # 记录已损坏依赖，提示调用方优先修复而不是覆盖安装。
            list_repair_required.append(
                {
                    "id": dict_dependency["id"],
                    "reason": "installed_dependency_invalid",
                    "invalid": dict_existing_report.get("invalid", []),
                    "installed": dict_existing_report.get("installed", []),
                }
            )

            # 目录校验已经失败，这里停在规划阶段，让最终报告明确暴露本地内容损坏。
            continue

        # 推导当前依赖缺失的技能名集合，供仓库克隆后定向复制。
        list_missing_names = _missing_names_for_install(dict_existing_report, dict_dependency)  # 当前依赖仍缺失的技能名列表

        # 记录需要真正执行下载和复制的依赖条目。
        list_install_plan.append((dict_dependency, list_missing_names))

    # 返回依赖安装动作规划三元组。
    return list_skipped, list_repair_required, list_install_plan

# 逐条执行依赖安装计划并收集安装结果。
def _install_dependency_plan(
    install_plan: list[tuple[DependencyRecord, list[str]]],
    destination: Path,
) -> list[DependencyRecord]:
    """
    执行依赖安装计划中的仓库拉取与技能复制动作。

    :param install_plan: 待安装依赖及缺失技能名的列表。
    :param destination: 技能安装目标根目录。
    :return: 每条依赖对应的安装结果列表。
    """

    # 按 install_plan 顺序缓存 _install_one 回执，最终报告据此回放实际安装结果。
    list_installed_results: list[DependencyRecord] = []  # _install_one 的顺序回执

    # 逐条执行安装计划中的仓库拉取与技能复制。
    for dict_dependency, list_missing_names in install_plan:

        # 安装当前依赖缺失的技能并返回安装结果。
        dependency_record_install_result = _install_one(dict_dependency, destination, list_missing_names)  # 当前依赖的安装结果

        # 将当前依赖的安装结果加入最终安装报告。
        list_installed_results.append(dependency_record_install_result)

    # 返回按执行顺序收集的安装结果列表。
    return list_installed_results

# 组装依赖安装流程的最终返回载荷。
def _install_dependency_result(
    *,
    str_status: str,
    path_destination: Path, list_installed_results: list[DependencyRecord],
    list_skipped: list[DependencyRecord], list_install_skipped: list[DependencyRecord],
    list_repair_required: list[DependencyRecord],
) -> DependencyRecord:
    """
    组装依赖安装流程的统一结果字典。

    :param str_status: 当前安装流程的总体状态。
    :param path_destination: 技能安装目标根目录。
    :param list_installed_results: 已执行安装的结果列表。
    :param list_skipped: 已满足而跳过安装的依赖列表。
    :param list_install_skipped: 由替代提供者满足的技能列表。
    :param list_repair_required: 已安装但内容失效、需要人工修复的依赖列表。
    :return: 供 CLI、日志和上层流程复用的安装结果字典。
    """

    # 先写入所有安装路径与执行结果字段。
    dependency_record_result: DependencyRecord = {
        "version": 1,  # 报告结构版本
        "status": str_status,  # 当前安装流程总体状态
        "destination": str(path_destination),  # 技能安装目标目录
        "installed": list_installed_results,  # 已执行安装动作的回执列表
    }  # 安装结果载荷的基础字段

    # 单独整理跳过项、修复项与重启提示字段，避免基础字段与附加字段粘在一起。
    dict_follow_up_fields = {
        "skipped": list_skipped,  # 安装前已经满足的依赖，不需要进入复制流程
        "install_skipped": list_install_skipped,  # 由替代 provider 满足、因此跳过安装的技能记录
        "repair_required": list_repair_required,  # 已安装但内容损坏、需要人工修复的本地技能副本
        "restart_required": True,  # 安装后需要重启 Codex
        "next_step": "Restart Codex to pick up newly installed skills.",  # 提示调用方的后续动作
    }  # 安装结果载荷的附加字段

    # 再补齐跳过项、修复项与重启提示字段。
    dependency_record_result.update(
        {
            str_key: obj_value
            for str_key, obj_value in dict_follow_up_fields.items()
        }
    )

    # 把合并好的安装摘要字典交回 CLI 和上层调用方继续消费。
    return dependency_record_result

# 将机器可读依赖报告渲染成人类可读的简短文本。
def format_dependency_report(report: DependencyRecord) -> str:
    """
    将依赖报告转成人类可读的纯文本摘要。

    :param report: `check_skill_dependencies` 生成的机器可读报告。
    :return: 逐行展示依赖状态的文本字符串。
    """

    # 准备输出文本的首行摘要。
    list_lines = [f"Skill dependency status: {report.get('status')}"]  # 依赖摘要文本行列表

    # 逐条格式化依赖结果，保留缺失、满足者和失效细节。
    for dict_dependency_report in report.get("dependencies", []):

        # 先组装每条依赖的基础状态描述。
        str_detail = (
            f"- {dict_dependency_report['id']}: "
            f"{dict_dependency_report['status']} ({dict_dependency_report['level']})"
        )  # 当前依赖的基础说明

        # 在存在缺失技能时追加缺失名称列表。
        if dict_dependency_report.get("missing"):

            # 将缺失技能名附加到人类可读摘要中。
            str_detail += f"; missing={', '.join(dict_dependency_report['missing'])}"  # 缺失技能摘要

        # 在存在替代满足者时追加满足来源说明。
        if dict_dependency_report.get("satisfied_by"):

            # 将替代提供者来源附加到人类可读摘要中。
            str_detail += "; satisfied_by=" + _format_satisfied_by(dict_dependency_report["satisfied_by"])  # 替代提供者摘要

        # 在存在失效项时追加无效细节列表。
        if dict_dependency_report.get("invalid"):

            # 将失效细节附加到人类可读摘要中。
            str_detail += f"; invalid={', '.join(dict_dependency_report['invalid'])}"  # 失效项摘要

        # 记录当前依赖的完整可读摘要。
        list_lines.append(str_detail)

    # 返回拼接后的多行文本，供 CLI 直接打印。
    return "\n".join(list_lines)

# 检查单条依赖是否在当前技能索引中满足。
def _check_one(entry: DependencyRecord, index: SkillIndex) -> DependencyRecord:
    """
    检查单条依赖在当前技能索引中的满足情况。

    :param entry: 已规范化的单条依赖配置。
    :param index: 当前环境的已安装技能索引。
    :return: 单条依赖的安装、缺失与失效报告。
    """

    # 复制期望技能名，避免后续修改原始配置列表。
    list_expected_skill_names = list(entry["expected_skill_names"])  # 依赖要求的技能名列表

    # 将别名集合化，便于快速匹配目录名与 frontmatter 名。
    set_aliases = set(entry.get("aliases", []))  # 当前依赖允许的别名集合

    # 汇总允许出现的技能名称，既包含主名称也包含别名。
    set_allowed_names = set(list_expected_skill_names) | set_aliases  # 允许接受的技能名称集合

    # 记录命中的已安装技能条目。
    list_installed: list[DependencyRecord] = []  # 已直接命中的技能记录

    # 记录由替代提供者满足的技能条目。
    list_satisfied_by: list[DependencyRecord] = []  # 替代提供者满足记录

    # 记录仍然缺失的目标技能名。
    list_missing: list[str] = []  # 缺失的技能名称列表

    # 记录 frontmatter 不匹配或缺少必要文件的失效项。
    list_invalid: list[str] = []  # 无效依赖明细列表

    # 逐个检查依赖声明中要求的技能名称。
    for str_expected_name in list_expected_skill_names:

        # 先尝试按主名称或别名直接寻找已安装技能。
        dict_match = _find_skill(str_expected_name, set_aliases, index)  # 按期望技能名在已安装索引里查找直接命中的技能记录

        # 在没有直接命中时，尝试寻找可接受的替代提供者。
        if not dict_match:

            # 查询能否由其他技能满足当前技能名的职责要求。
            dict_alternative = _find_alternative_provider(  # 在直接命中失败后尝试解析可接受的替代技能
                str_expected_name,  # 当前正在补查的目标技能名
                entry.get("alternative_providers", []),  # 允许声明该技能名的替代 provider 列表
                index,  # 已安装技能索引用于解析替代 provider 是否存在
            )  # 当前技能名对应的替代提供者命中结果

            # 在替代提供者存在时记录满足来源并跳过缺失判断。
            if dict_alternative:

                # 记录由替代技能满足的依赖目标。
                list_satisfied_by.append(dict_alternative)

                # 当前技能名已由替代技能满足，不再进入缺失项登记分支。
                continue

        # 在直接命中和替代提供者都不存在时登记缺失。
        if not dict_match:

            # 记录当前依赖中仍然缺失的目标技能名。
            list_missing.append(str_expected_name)

            # 当前技能名已经登记为缺失项，不再进入已安装记录分支。
            continue

        # 记录命中的已安装技能，供报告和诊断复用。
        list_installed.append(dict_match)

        # 提取 frontmatter 中声明的真实技能名供合法性校验。
        str_frontmatter_name = str(dict_match.get("frontmatter_name") or "")  # 技能声明名

        # 拒绝目录名命中但 frontmatter 名不在允许集合内的技能。
        if str_frontmatter_name not in set_allowed_names:

            # 记录 frontmatter 名与允许集合不一致的失效项。
            list_invalid.append(f"{str_expected_name}:frontmatter_name={str_frontmatter_name}")

        # 检查该技能目录下是否缺少依赖要求的辅助文件。
        list_missing_files = _missing_required_files(  # 计算命中技能目录里仍然缺失的必需辅助文件
            dict_match["path"],  # 已命中技能目录的实际安装路径
            entry.get("required_files", []),  # 依赖契约要求该技能目录必须具备的辅助文件清单
        )  # 当前技能安装内容尚未满足依赖契约的文件清单

        # 将缺失文件转换成带技能名前缀的无效项。
        for str_missing_file in list_missing_files:

            # 记录缺失的必要文件，便于上层提示用户修复安装内容。
            list_invalid.append(f"{str_expected_name}:{str_missing_file}")

    # 根据缺失与失效情况推导最终依赖状态。
    str_status = _resolve_dependency_status(list_missing, list_invalid)  # 当前依赖的最终状态

    # 返回单条依赖的完整检查结果。
    return {
        "id": entry["id"],
        "level": entry["level"],
        "purpose": entry["purpose"],
        "repo_url": entry["repo_url"],
        "ref": entry["ref"],
        "paths": entry["paths"],
        "destination_names": entry["destination_names"],
        "expected_skill_names": list_expected_skill_names,
        "aliases": sorted(set_aliases),
        "adapter": entry["adapter"],
        "blocking": entry["blocking"],
        "status": str_status,
        "installed": list_installed,
        "satisfied_by": list_satisfied_by,
        "missing": list_missing,
        "invalid": list_invalid,
    }

# 在依赖缺失时检查是否存在可接受的替代提供者。
def _find_alternative_provider(
    name: str,
    providers: list[DependencyRecord],
    index: SkillIndex,
) -> DependencyRecord | None:
    """
    为指定技能名查找允许的替代提供者。

    :param name: 当前缺失的技能名称。
    :param providers: 依赖配置中声明的替代提供者列表。
    :param index: 当前环境的已安装技能索引。
    :return: 命中时返回替代提供者记录；否则返回 `None`。
    """

    # 逐条检查替代提供者规则是否覆盖当前技能名。
    for dict_provider in providers:

        # 跳过与当前缺失技能无关的替代提供者配置。
        if dict_provider["for"] != name:

            # 这条 provider 规则不负责当前缺失技能名，继续检查下一条规则。
            continue

        # 汇总替代提供者允许接受的技能名与别名。
        set_allowed_names = set(dict_provider["skill_names"]) | set(dict_provider["aliases"])  # 替代提供者允许名集合

        # 逐个尝试命中当前替代提供者声明的技能名称。
        for str_skill_name in dict_provider["skill_names"]:

            # 在已安装技能索引中寻找该替代技能。
            dict_match = _find_skill(  # 按替代技能名在已安装索引里查找候选 provider
                str_skill_name,  # 当前正在尝试命中的替代技能名称
                set(dict_provider["aliases"]),  # 该替代技能允许复用的别名集合
                index,  # 已安装技能索引用于检查替代技能是否真实存在
            )  # 替代技能的命中结果

            # 在未命中替代技能时继续尝试下一个候选名。
            if not dict_match:

                # 当前候选替代技能未安装，继续尝试同一 provider 规则里的下一个技能名。
                continue

            # 拒绝 frontmatter 名不在允许集合中的替代技能。
            if str(dict_match.get("frontmatter_name") or "") not in set_allowed_names:

                # 当前候选技能的 frontmatter 名不在允许集合里，不能作为合法替代提供者。
                continue

            # 返回满足当前缺失技能职责的替代提供者记录。
            return {
                "name": name,
                "provider": dict_match,
                "install_policy": dict_provider["install_policy"],
                "purpose": dict_provider["purpose"],
            }

    # 在没有任何替代提供者满足条件时返回空值。
    return None

# 在技能索引中按主名称与别名查找候选技能。
def _find_skill(name: str, aliases: set[str], index: SkillIndex) -> DependencyRecord | None:
    """
    在已安装技能索引中查找首个匹配记录。

    :param name: 优先匹配的技能名称。
    :param aliases: 允许回退匹配的别名集合。
    :param index: 当前环境的已安装技能索引。
    :return: 找到时返回候选技能记录的深拷贝；否则返回 `None`。
    """

    # 先收集主名称对应的候选技能列表。
    list_candidates = list(index.get(name, []))  # 主名称命中的候选技能列表

    # 在主名称未命中时回退到别名集合查找。
    if not list_candidates:

        # 逐个尝试别名并累积对应的候选技能记录。
        for str_alias in aliases:

            # 将当前别名命中的候选技能补充到待选列表中。
            list_candidates.extend(index.get(str_alias, []))

    # 在没有任何候选技能时直接返回空值。
    if not list_candidates:

        # 说明当前名称和别名均未命中任何已安装技能。
        return None

    # 返回首个候选技能的深拷贝，避免调用方意外修改索引内容。
    return deepcopy(list_candidates[0])

# 检查技能目录下是否缺少依赖规则要求的辅助文件。
def _missing_required_files(skill_path: str, required_files: list[str]) -> list[str]:
    """
    找出技能目录中缺失的必需文件或候选文件组。

    :param skill_path: 技能目录的绝对或相对路径。
    :param required_files: 依赖声明中的必需文件列表；`|` 表示任一候选路径即可。
    :return: 未满足的原始文件规则字符串列表。
    """

    # 解析技能目录路径，供后续拼接候选文件使用。
    path_root = Path(skill_path)  # 当前技能根目录路径

    # 收集未命中的必需文件规则。
    list_missing_rules: list[str] = []  # 缺失的文件规则列表

    # 逐条检查必需文件规则是否已经满足。
    for str_required_rule in required_files:

        # 先把单条文件规则拆成原始候选片段，后续再过滤空白项。
        list_candidate_parts = str_required_rule.split("|")  # 由 | 分隔得到的原始候选片段

        # 去掉空白后保留真正可检查的候选路径。
        list_candidates = [str_candidate.strip() for str_candidate in list_candidate_parts if str_candidate.strip()]  # 当前规则允许的候选路径列表

        # 在候选列表为空时跳过这条无效规则。
        if not list_candidates:

            # 当前规则没有给出任何可检查路径，不参与缺失判定。
            continue

        # 只要任一候选路径存在，就视为该文件规则已经满足。
        if any((path_root / str_candidate).exists() for str_candidate in list_candidates):

            # 当前规则已有命中路径，无需登记到缺失规则列表。
            continue

        # 记录未被任何候选路径满足的文件规则。
        list_missing_rules.append(str_required_rule)

    # 返回缺失的文件规则列表。
    return list_missing_rules

# 扫描技能目录和插件缓存目录，建立当前环境的技能索引。
def _discover_installed_skills(
    *,
    skill_dirs: list[Path] | None,
    plugin_cache_dirs: list[Path] | None,
) -> SkillIndex:
    """
    扫描所有候选目录并建立技能名称索引。

    :param skill_dirs: 显式指定的技能目录列表；为空时使用默认技能目录。
    :param plugin_cache_dirs: 显式指定的插件缓存目录；为空时使用默认插件缓存目录。
    :return: 技能名称到候选技能记录列表的索引字典。
    """

    # 先准备最终返回的统一索引容器，后续会持续写入技能目录和插件缓存目录的记录。
    skill_index_installed: SkillIndex = {}  # 汇总全部已发现技能的名称索引

    # 解析显式传入或默认规则推导出的技能目录集合。
    list_skill_dirs = _resolve_skill_dirs(skill_dirs)  # 需要扫描的技能目录列表

    # 解析插件缓存目录集合，后续用来补齐 bunded plugin 内的技能记录。
    list_plugin_cache_dirs = _resolve_plugin_cache_dirs(plugin_cache_dirs)  # 需要扫描的插件缓存目录列表

    # 遍历技能目录并建立非递归技能索引。
    for path_root in list_skill_dirs:

        # 将当前技能目录中的技能记录写入统一索引。
        _index_skill_root(
            path_root,
            skill_index_installed,
            recursive=False,
            source="skills",
        )

    # 遍历插件缓存目录并建立递归技能索引。
    for path_root in list_plugin_cache_dirs:

        # 将当前插件缓存目录中的技能记录写入统一索引。
        _index_skill_root(
            path_root,
            skill_index_installed,
            recursive=True,
            source="plugin-cache",
        )

    # 返回完成扫描后的技能索引。
    return skill_index_installed

# 将单个根目录下的技能记录写入统一索引。
def _index_skill_root(
    root: Path,
    index: SkillIndex,
    *,
    recursive: bool,
    source: str,
) -> None:
    """
    扫描指定根目录并把技能记录写入索引。

    :param root: 待扫描的技能根目录。
    :param index: 需要原地写入的技能索引。
    :param recursive: 是否递归查找所有 `SKILL.md`。
    :param source: 记录当前索引来源的标签字符串。
    :return: 无业务返回值。
    """

    # 在根目录不存在时直接结束扫描。
    if not root.exists():

        # 当根目录不存在时无需继续扫描任何技能文件。
        return

    # 根据扫描模式选择递归或顶层技能发现策略。
    list_skill_files = (
        list(root.rglob("SKILL.md")) if recursive else _direct_skill_files(root)  # recursive 为真时扫描全部子目录，否则只看顶层与一级子目录
    )  # 当前根目录下发现的技能描述文件

    # 将每个技能描述文件写入名称索引。
    for path_skill_file in list_skill_files:

        # 读取 frontmatter 以获得技能显式声明名。
        dict_frontmatter = _read_skill_frontmatter(path_skill_file)  # 技能 frontmatter 字典

        # 优先使用 frontmatter 中的 name 字段，缺失时回退到目录名。
        str_frontmatter_name = str(  # 将 frontmatter name 缺省回退为技能目录名
            dict_frontmatter.get("name") or path_skill_file.parent.name  # 优先取显式声明名，没有时退回父目录名
        ).strip()  # 去除名称首尾空白后得到索引键

        # 组装写入索引的技能记录。
        dict_record = {
            "name": path_skill_file.parent.name,  # 技能目录名
            "frontmatter_name": str_frontmatter_name,  # 技能 frontmatter 显式声明名
            "path": str(path_skill_file.parent.resolve()),  # 技能目录绝对路径
            "skill_file": str(path_skill_file.resolve()),  # SKILL.md 文件绝对路径
            "source": source,  # 当前扫描来源标签
        }  # 单条技能索引记录

        # 让目录名和 frontmatter 名都能命中当前技能。
        for str_key in {path_skill_file.parent.name, str_frontmatter_name}:

            # 将当前技能记录挂到对应名称索引下。
            index.setdefault(str_key, []).append(dict_record)

# 仅发现根目录自身及其一级子目录中的技能描述文件。
def _direct_skill_files(root: Path) -> list[Path]:
    """
    查找根目录与一级子目录中的 `SKILL.md` 文件。

    :param root: 待扫描的技能根目录。
    :return: 命中的 `SKILL.md` 文件路径列表。
    """

    # 准备返回给调用方的技能描述文件列表。
    list_files: list[Path] = []  # 顶层技能描述文件列表

    # 在根目录自身包含技能文件时优先记录。
    if (root / "SKILL.md").exists():

        # 将根目录自身的技能描述文件加入返回结果。
        list_files.append(root / "SKILL.md")

    # 扫描一级子目录，兼容常见的 skills/<skill-name>/SKILL.md 布局。
    for path_child in root.iterdir():

        # 在子目录中发现技能描述文件时追加到结果列表。
        if path_child.is_dir() and (path_child / "SKILL.md").exists():

            # 将一级子目录中的技能描述文件加入返回结果。
            list_files.append(path_child / "SKILL.md")

    # 返回顶层布局下发现的全部技能文件。
    return list_files

# 读取技能描述文件中的 YAML frontmatter 键值。
def _read_skill_frontmatter(skill_file: Path) -> dict[str, str]:
    """
    读取 `SKILL.md` 头部 frontmatter 中的扁平键值对。

    :param skill_file: 技能描述文件路径。
    :return: 解析出的 frontmatter 字典；读取失败或缺失时返回空字典。
    """

    # 以 UTF-8 读取技能文件文本，兼容当前仓库编码约定。
    try:

        # 读取技能文件的完整文本，供后续 frontmatter 扫描使用。
        str_text = skill_file.read_text(encoding="utf-8")  # 技能文件全文

    # 在文件无法读取时返回空 frontmatter，保持扫描流程健壮。
    except OSError:

        # 在读取失败时回退为空 frontmatter，避免中断整体扫描。
        return {}

    # 只解析以标准 frontmatter 起始标记开头的技能文件。
    if not str_text.startswith("---"):

        # 对没有 frontmatter 的技能文件返回空字典。
        return {}

    # 将文本拆成逐行内容，便于扫描头部键值。
    list_lines = str_text.splitlines()  # 技能文件的逐行文本

    # 准备承接解析结果的 frontmatter 字典。
    dict_data: dict[str, str] = {}  # frontmatter 键值映射

    # 从第二行开始解析直到 frontmatter 结束标记。
    for str_line in list_lines[1:]:

        # 在再次遇到 `---` 时结束 frontmatter 解析。
        if str_line.strip() == "---":

            # frontmatter 结束标记已经出现，不再继续读取后续正文行。
            break

        # 仅解析形如 `key: value` 的简单键值行。
        if ":" not in str_line:

            # 不含冒号的正文行不是 frontmatter 键值对，继续读取下一行。
            continue

        # 拆分字段名和值，并去除首尾空白与成对引号。
        str_key, str_value = str_line.split(":", 1)  # 当前 frontmatter 键值对

        # 记录清洗后的 frontmatter 键值。
        dict_data[str_key.strip()] = str_value.strip().strip("'\"")  # 清洗后的 frontmatter 值

    # 返回成功解析出的 frontmatter 数据。
    return dict_data

# 安装单条依赖缺失的技能内容到目标目录。
def _install_one(
    entry: DependencyRecord,
    destination: Path,
    missing_names: list[str],
) -> DependencyRecord:
    """
    克隆依赖仓库并复制缺失技能到目标目录。

    :param entry: 已规范化的单条依赖配置。
    :param destination: 技能安装根目录。
    :param missing_names: 当前依赖仍缺失的技能名称列表。
    :return: 单条依赖的安装结果字典。
    :raises ValueError: 当目标目录已存在，或依赖仓库路径不包含合法 `SKILL.md` 时抛出。
    """

    # 收集当前依赖实际写入的技能路径。
    list_installed_paths: list[str] = []  # 已安装技能目录路径列表

    # 计算缺失技能名对应的仓库路径与目标目录名。
    list_selected_pairs = _install_selections(entry, missing_names)  # 需要复制的仓库路径与目标名列表

    # 在安装前检查目标目录是否已存在，避免覆盖本地内容。
    for _, str_destination_name in list_selected_pairs:

        # 组合当前技能将要写入的目标目录路径。
        path_target = destination / str_destination_name  # 技能复制目标目录

        # 在目标目录已存在时拒绝覆盖安装。
        if path_target.exists():

            # 阻止覆盖已有目录，避免误伤用户本地内容。
            raise ValueError(
                "> ERR: [Python] Skill destination already exists and will not be overwritten: "
                f"{path_target}"
            )

    # 使用临时目录克隆远端仓库并复制技能内容。
    with tempfile.TemporaryDirectory(prefix="hls-skill-deps-") as str_tmp_dir:

        # 在临时目录中约定仓库检出路径。
        path_checkout = Path(str_tmp_dir) / "repo"  # 仓库临时检出目录

        # 拉取指定分支的浅克隆仓库以降低安装成本。
        _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                entry["ref"],
                entry["repo_url"],
                str(path_checkout),
            ]
        )

        # 记录需要复制的源目录与目标技能目录名。
        list_sources: list[tuple[Path, str]] = []  # 已解析的复制源目录列表

        # 根据依赖配置映射每个缺失技能的仓库路径。
        for str_repo_path, str_destination_name in list_selected_pairs:

            # 在 repo_path 为 `.` 时表示整个仓库根目录就是技能目录。
            path_source = (  # 根据 repo_path 决定使用仓库根还是仓库内子目录作为复制源
                path_checkout if str_repo_path == "." else path_checkout / str_repo_path  # `.` 表示整个仓库根就是技能目录，否则进入声明的子路径
            )  # 当前技能的仓库源目录

            # 拒绝不包含 `SKILL.md` 的源目录，避免复制错误内容。
            if not (path_source / "SKILL.md").exists():

                # 明确指出依赖仓库路径没有形成合法技能目录。
                raise ValueError(
                    "> ERR: [Python] Dependency "
                    f"{entry['id']} path {str_repo_path!r} does not contain SKILL.md."
                )

            # 记录已解析完成的复制源目录与目标目录名。
            list_sources.append((path_source, str_destination_name))

        # 将每个解析好的技能目录复制到安装目标根目录下。
        for path_source, str_destination_name in list_sources:

            # 组合当前技能的最终安装目录。
            path_target = destination / str_destination_name  # 当前技能的目标安装目录

            # 复制技能目录，同时忽略仓库元数据与 Python 缓存文件。
            shutil.copytree(
                path_source,
                path_target,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )

            # 记录已经成功安装的技能目录路径。
            list_installed_paths.append(str(path_target))

    # 返回单条依赖的安装结果。
    return {
        "id": entry["id"],
        "status": DEPENDENCY_OK,
        "installed_paths": list_installed_paths,
    }

# 将缺失技能名映射为仓库路径和目标技能目录名。
def _install_selections(
    entry: DependencyRecord,
    missing_names: list[str],
) -> list[tuple[str, str]]:
    """
    根据缺失技能名选择需要复制的仓库路径。

    :param entry: 已规范化的单条依赖配置。
    :param missing_names: 当前依赖仍然缺失的技能名称列表。
    :return: `(repo_path, destination_name)` 二元组列表。
    :raises ValueError: 当缺失技能名无法映射到依赖配置声明的安装路径时抛出。
    """

    # 集合化缺失技能名，便于快速判断映射是否需要保留。
    set_missing_names = set(missing_names)  # 缺失技能名集合

    # 收集缺失技能对应的仓库路径与目标目录名。
    list_selections: list[tuple[str, str]] = []  # 需要复制的路径映射列表

    # 按一一对应关系遍历技能名、仓库路径和目标目录名。
    for str_expected_name, str_repo_path, str_destination_name in zip(
        entry["expected_skill_names"],
        entry["paths"],
        entry["destination_names"],
        strict=True,
    ):

        # 只保留当前仍然缺失的技能映射。
        if str_expected_name in set_missing_names:

            # 将缺失技能对应的仓库路径映射加入复制计划。
            list_selections.append((str_repo_path, str_destination_name))

    # 在缺失技能名无法映射到仓库路径时直接报错。
    if not list_selections and set_missing_names:

        # 提示依赖配置缺少缺失技能到安装路径的对应关系。
        raise ValueError(
            "> ERR: [Python] Dependency "
            f"{entry['id']} has missing skills not mapped to install paths: "
            f"{', '.join(sorted(set_missing_names))}"
        )

    # 返回缺失技能对应的仓库路径映射。
    return list_selections

# 执行外部命令并在失败时抛出带输出内容的异常。
def _run(command: list[str]) -> None:
    """
    执行外部命令并在失败时抛出异常。

    :param command: 需要执行的命令及参数列表。
    :return: 无业务返回值。
    """

    # 执行命令并捕获标准输出与标准错误文本。
    completed_process_result: subprocess.CompletedProcess[str] = subprocess.run(  # 执行外部命令并保留 stdout/stderr 供失败诊断
        command,  # 待执行的命令及参数列表
        capture_output=True,  # 同时捕获标准输出与标准错误
        text=True,  # 以文本模式读取子进程输出
        encoding="utf-8",  # 按 UTF-8 解码子进程输出
        errors="replace",  # 解码失败字符替换为占位符，避免编码异常中断流程
        check=False,  # 由当前函数统一检查返回码并构造失败异常
    )  # 外部命令执行结果

    # 在命令执行失败时抛出包含输出细节的异常。
    if completed_process_result.returncode != 0:

        # 合并标准输出与标准错误，便于调用方快速诊断失败原因。
        raise ValueError(
            "> ERR: [Python] Command failed: "
            f"{' '.join(command)}\n"
            f"{completed_process_result.stdout}"
            f"{completed_process_result.stderr}"
        )

# 将原始依赖配置条目校验并规范化为统一结构。
def _normalize_dependency(item: Any) -> DependencyRecord:
    """
    校验单条依赖配置并补齐默认字段。

    :param item: 原始依赖配置对象。
    :return: 规范化后的依赖配置字典。
    异常:
        ValueError: 当依赖条目不是对象、缺字段或列表长度关系不合法时抛出。
    """

    # 只接受 JSON 对象风格的依赖配置条目。
    if not isinstance(item, dict):

        # 明确提示每条依赖都必须是对象结构。
        raise ValueError("> ERR: [Python] Each skill dependency must be a JSON object.")

    # 必填字段校验与错误文本统一由独立 helper 负责。
    _require_dependency_fields(item)

    # 先取回三个列表组成的三元组，再按语义拆成独立变量。
    tuple_path_lists = _dependency_path_lists(item)  # 路径映射相关列表三元组

    # 取出规范化后的仓库路径列表。
    list_paths = tuple_path_lists[0]  # 依赖仓库中声明的 repo_path 列表

    # 取出规范化后的期望技能名列表。
    list_expected_skill_names = tuple_path_lists[1]  # 当前环境里必须命中的技能名列表

    # 取出规范化后的技能安装目标目录名列表。
    list_destination_names = tuple_path_lists[2]  # 安装到本地 skills 根目录时使用的目标目录名列表

    # 组装规范化后的依赖配置字典。
    dict_normalized = {
        "id": _non_empty_string(item["id"], "id"),  # 依赖条目的稳定标识
        "level": _non_empty_string(item["level"], "level"),  # required 或 recommended 依赖级别
        "purpose": _non_empty_string(item["purpose"], "purpose"),  # 依赖存在的业务用途说明
        "repo_url": _non_empty_string(item["repo_url"], "repo_url"),  # 依赖仓库地址
        "ref": _non_empty_string(item["ref"], "ref"),  # 依赖仓库分支或标签
        "paths": list_paths,  # 仓库内待复制技能目录的路径列表
        "expected_skill_names": list_expected_skill_names,  # 当前环境必须识别到的技能名列表
        "destination_names": list_destination_names,  # 本地安装时写入 skills 根目录的目标名列表
        "aliases": _string_list(item["aliases"], "aliases", allow_empty=True),  # 允许匹配当前依赖的技能别名列表
        "adapter": _non_empty_string(item["adapter"], "adapter"),  # 调用方使用的依赖适配器类型
        "blocking": bool(item["blocking"]) and str(item["level"]).strip().lower() == "required",  # required 依赖在 blocking 为真时才会阻断流程
        "scopes": _string_list(item.get("scopes", ["all"]), "scopes"),  # 当前依赖允许参与的作用域集合
        "required_files": _string_list(  # 校验后得到依赖目录里必须存在的附加文件清单
            item.get("required_files", []),  # 依赖目录中必须存在的附加文件清单
            "required_files",  # required_files 字段名用于构造错误消息
            allow_empty=True,  # 允许当前依赖不声明额外必需文件
        ),  # 进入安装有效性校验的必需文件列表
        "alternative_providers": _normalize_alternative_providers(  # 校验并清洗允许顶替目标技能的 provider 规则列表
            item.get("alternative_providers", []),  # 原始替代 provider 配置列表
            list_expected_skill_names,  # 仅允许替代当前依赖声明过的技能名
        ),  # 通过校验后的替代 provider 规则列表
    }  # 规范化后的依赖配置字典

    # 仅允许 required 或 recommended 两种依赖级别。
    if dict_normalized["level"] not in {"required", "recommended"}:

        # 指出非法的依赖级别取值。
        raise ValueError(
            "> ERR: [Python] Skill dependency "
            f"{dict_normalized['id']!r} level must be required or recommended."
        )

    # 返回补齐默认值且通过校验的依赖配置。
    return dict_normalized

# 校验依赖条目是否补齐了运行时所需的必填字段。
def _require_dependency_fields(item: dict[str, Any]) -> None:
    """
    校验依赖条目是否具备必填字段。

    :param item: 已确认是字典的原始依赖配置对象。
    :return: 无业务返回值；通过表示字段齐全。
    异常:
        ValueError: 当依赖条目缺少任一必填字段时抛出。
    """

    # 声明当前依赖配置必须具备的字段集合。
    tuple_required_fields = (
        "id",  # 依赖条目的稳定标识字段
        "level",  # required 或 recommended 级别字段
        "purpose",  # 依赖用途说明字段
        "repo_url",  # 依赖仓库地址字段
        "ref",  # 依赖仓库分支或标签字段
        "paths",  # 仓库内技能路径列表字段
        "expected_skill_names",  # 期望命中的技能名列表字段
        "destination_names",  # 本地安装目标目录名列表字段
        "aliases",  # 技能别名列表字段
        "adapter",  # 依赖适配器类型字段
        "blocking",  # 是否在 required 场景下阻断流程的布尔字段
    )  # 依赖配置的必填字段元组

    # 找出当前依赖配置中缺失的必填字段。
    list_missing_fields = [
        str_field_name  # 当前检测到缺失的必填字段名
        for str_field_name in tuple_required_fields  # 遍历所有强制要求出现的字段名
        if str_field_name not in item  # 仅保留当前依赖配置中尚未提供的字段
    ]  # 缺失的必填字段列表

    # 在存在缺失字段时立刻终止规范化流程。
    if list_missing_fields:

        # 提示调用方补齐依赖配置的必填字段。
        raise ValueError(
            "> ERR: [Python] Skill dependency is missing required fields: "
            f"{', '.join(list_missing_fields)}"
        )

# 规范化依赖条目的路径映射并校验三个列表的数量关系。
def _dependency_path_lists(
    item: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """
    规范化依赖条目的路径、技能名与目标目录名列表。

    :param item: 已通过必填字段校验的原始依赖配置对象。
    :return: 仓库路径、期望技能名和目标目录名三元组。
    异常:
        ValueError: 当三个列表数量关系不合法或缺少目标技能名时抛出。
    """

    # 规范化仓库路径列表。
    list_paths = _string_list(item["paths"], "paths")  # 依赖仓库中待复制技能目录的路径列表

    # 规范化期望技能名列表。
    list_expected_skill_names = _string_list(  # 校验并清洗依赖配置要求命中的技能名列表
        item["expected_skill_names"],  # 原始依赖条目里声明的 expected_skill_names 值
        "expected_skill_names",  # 报错时用于指出目标技能名字段
    )  # 后续会拿它去校验已安装技能与替代 provider 目标是否合法

    # 规范化技能安装目标目录名列表。
    list_destination_names = _string_list(  # 校验并清洗本地安装时要使用的目标目录名列表
        item["destination_names"],  # 原始依赖条目里声明的本地 skills 目录落盘名字列表
        "destination_names",  # 报错时用于指出安装目标名字段
    )  # 本地 skills 根目录下的安装目标名列表

    # 要求仓库路径与目标目录名保持一一对应。
    if len(list_paths) != len(list_destination_names):

        # 提示调用方修正安装路径映射数量不一致的问题。
        raise ValueError(
            "> ERR: [Python] Skill dependency "
            f"{item.get('id')!r} paths and destination_names must have the same length."
        )

    # 要求期望技能名与仓库路径也保持一一对应。
    if len(list_expected_skill_names) != len(list_paths):

        # 提示调用方修正期望技能名与仓库路径数量不一致的问题。
        raise ValueError(
            "> ERR: [Python] Skill dependency "
            f"{item.get('id')!r} expected_skill_names and paths must have the same length."
        )

    # 至少要求当前依赖声明一个目标技能名。
    if not list_expected_skill_names:

        # 拒绝没有任何技能目标的空依赖声明。
        raise ValueError(
            "> ERR: [Python] Skill dependency "
            f"{item.get('id')!r} must list expected_skill_names."
        )

    # 返回三个已规范化且通过数量关系校验的列表。
    return list_paths, list_expected_skill_names, list_destination_names

# 规范化替代提供者配置并验证其目标技能名是否合法。
def _normalize_alternative_providers(
    value: Any,
    expected: list[str],
) -> list[DependencyRecord]:
    """
    校验并规范化依赖的替代提供者列表。

    :param value: 原始替代提供者配置。
    :param expected: 当前依赖允许被替代的技能名称列表。
    :return: 规范化后的替代提供者配置列表。
    :raises ValueError: 当替代提供者条目不是对象，或目标技能名不在允许集合中时抛出。
    """

    # 将缺省值或空列表视为没有替代提供者。
    if value in (None, []):

        # 缺省空值表示当前依赖没有声明替代提供者。
        return []

    # 只接受列表形式的替代提供者配置。
    if not isinstance(value, list):

        # 明确提示替代提供者字段必须是列表。
        raise ValueError(
            "> ERR: [Python] Skill dependency alternative_providers must be a list."
        )

    # 集合化期望技能名，便于校验 `for` 字段是否合法。
    set_expected_names = set(expected)  # 允许被替代的技能名集合

    # 收集规范化后的替代提供者条目。
    list_result: list[DependencyRecord] = []  # 规范化替代提供者列表

    # 逐条校验替代提供者配置。
    for dict_item in value:

        # 拒绝列表里混入非对象成员，避免后续字段读取直接崩溃。
        if not isinstance(dict_item, dict):

            # 明确提示每个替代提供者都必须是对象结构。
            raise ValueError(
                "> ERR: [Python] Each skill dependency alternative provider must be a JSON object."
            )

        # 读取替代关系所指向的目标技能名。
        str_target_name = _non_empty_string(  # 校验当前 provider 规则到底要替代哪个目标技能名
            dict_item.get("for"),  # provider 规则中 `for` 字段给出的目标技能名原值
            "alternative_providers.for",  # 报错时用于点名替代 provider 的 `for` 字段
        )  # 当前 provider 规则准备替代的目标技能名

        # 拒绝引用未出现在 expected_skill_names 中的目标技能。
        if str_target_name not in set_expected_names:

            # 指出替代提供者目标与依赖声明不一致。
            raise ValueError(
                "> ERR: [Python] Alternative provider target "
                f"{str_target_name!r} is not listed in expected_skill_names."
            )

        # 记录规范化后的替代提供者配置。
        list_result.append(
            {
                "for": str_target_name,
                "skill_names": _string_list(
                    dict_item.get("skill_names"),
                    "alternative_providers.skill_names",
                ),
                "aliases": _string_list(
                    dict_item.get("aliases", []),
                    "alternative_providers.aliases",
                    allow_empty=True,
                ),
                "install_policy": (
                    str(dict_item.get("install_policy") or "skip_if_present").strip()
                    or "skip_if_present"
                ),
                "purpose": str(dict_item.get("purpose") or "").strip(),
            }
        )

    # 返回通过校验的替代提供者配置列表。
    return list_result

# 将任意字段值规范化为非空字符串列表。
def _string_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    """
    将字段值校验并转换为字符串列表。

    :param value: 需要检查的原始字段值。
    :param name: 当前字段名，用于构造错误消息。
    :param allow_empty: 是否允许空列表。
    :return: 去掉首尾空白后的字符串列表。
    :raises ValueError: 当字段值不是合法列表，或列表内包含空字符串成员时抛出。
    """

    # 要求字段值必须是列表，并根据配置决定是否允许空列表。
    if not isinstance(value, list) or (not value and not allow_empty):

        # 明确指出当前字段必须使用列表结构。
        raise ValueError(f"> ERR: [Python] Skill dependency {name} must be a list.")

    # 收集清洗后的字符串列表结果。
    list_result = [str(item_value).strip() for item_value in value]  # 清洗后的字符串列表

    # 拒绝包含空字符串成员的列表，避免后续生成脏路径或脏名称。
    if any(not str_item for str_item in list_result):

        # 明确指出列表成员必须全部为非空字符串。
        raise ValueError(
            f"> ERR: [Python] Skill dependency {name} must contain only non-empty strings."
        )

    # 返回通过校验的字符串列表。
    return list_result

# 将任意字段值规范化为非空字符串。
def _non_empty_string(value: Any, name: str) -> str:
    """
    将字段值转成去空白后的非空字符串。

    :param value: 原始字段值。
    :param name: 当前字段名，用于构造错误消息。
    :return: 清洗后的非空字符串。
    :raises ValueError: 当字段值清洗后为空字符串时抛出。
    """

    # 将原始字段值转成字符串并去除首尾空白。
    str_text = str(value or "").strip()  # 清洗后的字符串值

    # 拒绝空字符串字段，避免关键配置缺失。
    if not str_text:

        # 明确指出当前字段必须提供有效文本。
        raise ValueError(f"> ERR: [Python] Skill dependency {name} must be set.")

    # 返回通过校验的非空字符串。
    return str_text

# 解析默认的技能安装根目录。
def _default_install_root() -> Path:
    """
    推导默认技能安装根目录。

    :param 无: 当前辅助函数不需要外部业务参数。
    :return: 默认技能安装根目录路径。
    """

    # 优先使用显式环境变量覆盖的技能目录列表。
    list_override_dirs = _path_list_env("HLS_GENERATOR_SKILLS_DIRS")  # 技能目录环境变量结果

    # 在存在覆盖目录时选择第一项作为默认安装根目录。
    if list_override_dirs:

        # 使用环境变量给出的首个技能目录作为默认安装根目录。
        return list_override_dirs[0]

    # 读取 CODEX_HOME 环境变量以兼容自定义 Codex 安装路径。
    str_codex_home = os.environ.get("CODEX_HOME")  # Codex 根目录环境变量

    # 在自定义或默认 `.codex` 目录下返回标准 skills 子目录。
    return (Path(str_codex_home) if str_codex_home else Path.home() / ".codex") / "skills"

# 解析默认或显式覆盖的技能搜索目录列表。
def _default_skill_dirs() -> list[Path]:
    """
    获取默认的技能搜索目录列表。

    :param 无: 当前辅助函数不需要外部业务参数。
    :return: 去重后的技能搜索目录列表。
    """

    # 优先尊重显式环境变量覆盖的技能目录集合。
    list_override_dirs = _path_list_env("HLS_GENERATOR_SKILLS_DIRS")  # HLS_GENERATOR_SKILLS_DIRS 解析得到的技能目录覆盖列表

    # 若调用方显式配置了插件缓存覆盖目录，就直接沿用该结果，不再拼默认缓存路径。
    if list_override_dirs is not None:

        # 直接返回环境变量指定的技能目录列表。
        return list_override_dirs

    # 收集需要参与默认搜索的技能根目录。
    list_roots: list[Path] = []  # 默认技能根目录列表

    # 单独读取 CODEX_HOME，用它推导 `plugins/cache` 所在根路径。
    str_codex_home = os.environ.get("CODEX_HOME")  # 当前会话可选的 Codex 根目录环境变量原值

    # 在自定义 CODEX_HOME 存在时优先加入其 skills 子目录。
    if str_codex_home:

        # 将自定义 CODEX_HOME 下的技能目录加入搜索列表。
        list_roots.append(Path(str_codex_home) / "skills")

    # 把用户主目录下的默认 `.codex/skills` 目录补进搜索列表，保证未配置 CODEX_HOME 时仍能找到本地技能。
    list_roots.append(Path.home() / ".codex" / "skills")

    # 返回去重后的默认技能目录列表。
    return _dedupe_paths(list_roots)

# 解析默认或显式覆盖的插件缓存目录列表。
def _default_plugin_cache_dirs() -> list[Path]:
    """
    获取默认的插件缓存目录列表。

    :param 无: 当前辅助函数不需要外部业务参数。
    :return: 去重后的插件缓存目录列表。
    """

    # 优先尊重显式环境变量覆盖的插件缓存目录集合。
    list_override_dirs = _path_list_env("HLS_GENERATOR_PLUGIN_CACHE_DIRS")  # HLS_GENERATOR_PLUGIN_CACHE_DIRS 解析得到的插件缓存覆盖列表

    # 在环境变量显式提供时直接返回覆盖结果。
    if list_override_dirs is not None:

        # 直接返回环境变量指定的插件缓存目录列表。
        return list_override_dirs

    # 收集需要参与默认搜索的插件缓存根目录。
    list_roots: list[Path] = []  # 默认插件缓存根目录列表

    # 读取自定义 CODEX_HOME，兼容非默认用户目录布局。
    str_codex_home = os.environ.get("CODEX_HOME")  # 调用方自定义的 Codex 根目录环境变量值

    # 在自定义 CODEX_HOME 存在时优先加入其插件缓存目录。
    if str_codex_home:

        # 将自定义 CODEX_HOME 下的插件缓存目录加入搜索列表。
        list_roots.append(Path(str_codex_home) / "plugins" / "cache")

    # 把用户主目录下的默认 `.codex/plugins/cache` 目录补进搜索列表，保证未配置 CODEX_HOME 时仍能发现本地插件缓存。
    list_roots.append(Path.home() / ".codex" / "plugins" / "cache")

    # 返回去重后的默认插件缓存目录列表。
    return _dedupe_paths(list_roots)

# 对路径列表做展开、解析和去重处理。
def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """
    规范化路径列表并去除重复项。

    :param paths: 待去重的路径列表。
    :return: 展开并解析后的唯一路径列表。
    """

    # 记录已经出现过的规范化路径字符串。
    set_seen_paths: set[str] = set()  # 已见路径集合

    # 收集去重后的规范化路径对象。
    list_result: list[Path] = []  # 去重后的路径列表

    # 按输入顺序遍历路径并保留首个命中项。
    for path_item in paths:

        # 规范化路径字符串，确保同一路径只保留一份。
        str_resolved_path = str(path_item.expanduser().resolve())  # 规范化后的路径字符串

        # 跳过已经记录过的重复路径。
        if str_resolved_path in set_seen_paths:

            # 当前路径已经在结果集中出现过，不再重复追加。
            continue

        # 记录当前路径已经出现过。
        set_seen_paths.add(str_resolved_path)

        # 保留当前路径对象，供调用方继续使用。
        list_result.append(Path(str_resolved_path))

    # 返回去重后的路径对象列表。
    return list_result

# 读取以系统路径分隔符连接的环境变量路径列表。
def _path_list_env(name: str) -> list[Path] | None:
    """
    读取并解析路径列表型环境变量。

    :param name: 环境变量名。
    :return: 未设置时返回 `None`，显式为空时返回空列表，否则返回去重后的路径列表。
    """

    # 读取原始环境变量文本。
    str_raw_value = os.environ.get(name)  # 原始环境变量值

    # 在环境变量未设置时返回空值，交由上层决定默认行为。
    if str_raw_value is None:

        # 说明当前环境变量没有显式配置路径列表。
        return None

    # 在环境变量显式设置为空字符串时返回空列表。
    if not str_raw_value.strip():

        # 用空列表表示调用方有意清空默认搜索路径。
        return []

    # 按系统路径分隔符拆分并返回去重后的路径列表。
    return _dedupe_paths(
        [
            Path(str_item)
            for str_item in str_raw_value.split(os.pathsep)
            if str_item.strip()
        ]
    )

# 规范化调用方请求的依赖作用域集合。
def _normalize_requested_scopes(
    scopes: list[str] | tuple[str, ...] | set[str] | None,
) -> set[str]:
    """
    将调用方传入的作用域参数规范化为小写集合。

    :param scopes: 调用方请求检查的作用域集合。
    :return: 清洗后的小写作用域集合；为空时返回空集合或 `{all}`。
    """

    # 在未提供作用域过滤时返回空集合，表示检查全部作用域。
    if scopes is None:

        # 用空集合表示当前调用不需要作用域过滤。
        return set()

    # 清洗作用域文本并统一转成小写集合。
    set_normalized_scopes = {
        str(scope_item).strip().lower()  # 去掉空白并统一成小写作用域名
        for scope_item in scopes  # 遍历调用方传入的每个原始作用域项
        if str(scope_item).strip()  # 过滤清洗后为空白的作用域文本
    }  # 规范化后的作用域集合

    # 在调用方仅提供空白项时回退到 all 作用域。
    return set_normalized_scopes or {"all"}

# 判断单条依赖是否应当参与当前作用域的检查。
def _dependency_matches_scopes(
    entry: DependencyRecord,
    requested_scopes: set[str],
) -> bool:
    """
    判断依赖配置是否命中当前请求的作用域。

    :param entry: 已规范化的依赖配置。
    :param requested_scopes: 调用方请求的作用域集合。
    :return: 命中当前作用域时返回 `True`，否则返回 `False`。
    """

    # 在没有作用域过滤条件时默认接受所有依赖。
    if not requested_scopes:

        # 直接接受所有依赖，避免额外作用域判断。
        return True

    # 规范化依赖条目自身声明的作用域集合。
    set_entry_scopes = {
        str(scope_item).strip().lower()  # 去掉空白并统一成小写的条目作用域名
        for scope_item in entry.get("scopes", [])  # 遍历依赖条目声明的每个作用域项
        if str(scope_item).strip()  # 过滤清洗后为空白的条目作用域文本
    }  # 依赖条目的作用域集合

    # 在条目未声明作用域或显式声明 all 时视为命中全部作用域。
    if not set_entry_scopes or "all" in set_entry_scopes:

        # 将未限定作用域的依赖视为命中当前请求。
        return True

    # 只有在两侧作用域存在交集时才参与当前检查。
    return bool(set_entry_scopes & requested_scopes)

# 将阻断依赖报告转成异常或日志可直接输出的 JSON 文本。
def _format_blocked_error(report: DependencyRecord) -> str:
    """
    将阻断依赖报告格式化成 JSON 文本。

    :param report: `check_skill_dependencies` 生成的依赖检查报告。
    :return: 供异常消息直接使用的 JSON 文本。
    """

    # 基于依赖报告构造安装请求载荷。
    dependency_record_request = build_dependency_request(report)  # 安装请求载荷

    # 返回带统一错误前缀的 JSON 字符串，便于 CLI 和用户阅读。
    return "> ERR: [Python] " + json.dumps(dependency_record_request, indent=2, ensure_ascii=False)

# 解析最终报告中应记录的技能目录列表。
def _resolve_skill_dirs(skill_dirs: list[Path] | None) -> list[Path]:
    """
    解析要写入报告和扫描流程的技能目录列表。

    :param skill_dirs: 调用方显式传入的技能目录列表。
    :return: 实际用于扫描和报告的技能目录列表。
    """

    # 在调用方显式提供技能目录时原样使用。
    if skill_dirs is not None:

        # 直接返回调用方传入的技能目录列表。
        return skill_dirs

    # 回退到环境变量和默认规则推导的技能目录列表。
    return _default_skill_dirs()

# 解析最终报告中应记录的插件缓存目录列表。
def _resolve_plugin_cache_dirs(plugin_cache_dirs: list[Path] | None) -> list[Path]:
    """
    解析要写入报告和扫描流程的插件缓存目录列表。

    :param plugin_cache_dirs: 调用方显式传入的插件缓存目录列表。
    :return: 实际用于扫描和报告的插件缓存目录列表。
    """

    # 在调用方显式提供插件缓存目录时原样使用。
    if plugin_cache_dirs is not None:

        # 直接返回调用方传入的插件缓存目录列表。
        return plugin_cache_dirs

    # 回退到环境变量和默认规则推导的插件缓存目录列表。
    return _default_plugin_cache_dirs()

# 根据单条依赖结果判断安装阶段缺失的技能名。
def _missing_names_for_install(
    existing_report: DependencyRecord | None,
    dependency: DependencyRecord,
) -> list[str]:
    """
    推导安装某条依赖时仍缺失的技能名称列表。

    :param existing_report: 安装前检查得到的单条依赖结果；可能为空。
    :param dependency: 已规范化的依赖配置。
    :return: 仍需从仓库复制的技能名称列表。
    """

    # 在存在检查结果时优先复用其中明确列出的缺失技能名。
    if existing_report:

        # 返回检查结果已经明确指出的缺失技能名列表。
        return list(existing_report.get("missing", dependency["expected_skill_names"]))

    # 在没有检查结果时回退到依赖声明中的全部期望技能名。
    return list(dependency["expected_skill_names"])

# 汇总由替代提供者满足而无需安装的技能映射。
def _collect_install_skipped(
    reports: list[DependencyRecord],
    selected_dependencies: list[DependencyRecord],
) -> list[DependencyRecord]:
    """
    收集被替代提供者满足的技能条目。

    :param reports: 安装前依赖检查的单条结果列表。
    :param selected_dependencies: 当前选择参与安装规划的依赖列表。
    :return: 由替代提供者满足而跳过安装的条目列表。
    """

    # 预先收集当前选中依赖的 ID 集合，便于过滤无关报告。
    set_selected_ids = {
        dict_dependency["id"]  # 当前被纳入安装规划的依赖 ID
        for dict_dependency in selected_dependencies  # 遍历本轮选择参与安装规划的依赖
    }  # 当前选中依赖 ID 集合

    # 收集由替代提供者满足的安装跳过项。
    list_install_skipped: list[DependencyRecord] = []  # 替代提供者跳过项列表

    # 遍历依赖检查结果，找出已经由替代技能满足的目标技能。
    for dict_dependency_report in reports:

        # 跳过不在当前安装选择范围内的依赖报告。
        if dict_dependency_report["id"] not in set_selected_ids:

            # 当前报告对应的依赖没有被选中安装，不进入替代提供者跳过项汇总。
            continue

        # 将每个替代满足记录转成安装结果中的跳过条目。
        for dict_satisfied in dict_dependency_report.get("satisfied_by", []):

            # 将替代提供者满足关系登记到安装跳过列表中。
            list_install_skipped.append(
                {
                    "dependency_id": dict_dependency_report["id"],
                    **dict_satisfied,
                }
            )

    # 返回由替代提供者满足的技能跳过记录。
    return list_install_skipped

# 计算安装流程的最终整体状态。
def _resolve_install_status(
    repair_required: list[DependencyRecord],
    installed_results: list[DependencyRecord],
) -> str:
    """
    根据修复项和安装结果推导总体安装状态。

    :param repair_required: 需要人工修复的依赖列表。
    :param installed_results: 实际执行安装后的结果列表。
    :return: 安装流程的总体状态字符串。
    """

    # 在存在已损坏依赖时优先返回 repair_required。
    if repair_required:

        # 优先报告需要人工修复的依赖状态。
        return "repair_required"

    # 在所有安装结果都成功时返回 ok。
    if all(dict_result["status"] == DEPENDENCY_OK for dict_result in installed_results):

        # 在所有安装动作均成功时返回整体成功状态。
        return DEPENDENCY_OK

    # 在出现非成功安装结果时返回 failed。
    return "failed"

# 将替代提供者列表渲染成 `name->provider` 形式的摘要文本。
def _format_satisfied_by(satisfied_by: list[DependencyRecord]) -> str:
    """
    格式化替代提供者满足记录列表。

    :param satisfied_by: 单条依赖报告中的替代满足者列表。
    :return: 逗号分隔的 `name->frontmatter_name` 摘要字符串。
    """

    # 将每个替代满足者记录渲染成单条摘要文本。
    list_entries = [
        f"{dict_item['name']}->{dict_item['provider']['frontmatter_name']}"  # 单条 `目标技能->实际提供者` 摘要
        for dict_item in satisfied_by  # 逐条读取 satisfied_by 里的目标技能与实际提供者配对
    ]  # 替代提供者摘要列表

    # 返回供 format_dependency_report 直接拼接的文本。
    return ", ".join(list_entries)

# 根据缺失和失效列表推导依赖状态码。
def _resolve_dependency_status(
    missing: list[str],
    invalid: list[str],
) -> str:
    """
    根据缺失和失效明细计算依赖状态。

    :param missing: 缺失技能名称列表。
    :param invalid: 失效条目列表。
    :return: `ok`、`missing` 或 `invalid` 状态码。
    """

    # 默认将依赖状态视为已经满足。
    str_status = DEPENDENCY_OK  # 当前依赖状态码

    # 在存在缺失技能时先标记为 missing。
    if missing:

        # 将状态切换为缺失依赖，提示仍需安装目标技能。
        str_status = DEPENDENCY_MISSING  # 缺失技能时的状态码

    # 在存在失效项时提升为 invalid，优先级高于 missing。
    if invalid:

        # 将状态提升为无效依赖，提示已安装内容仍需修复。
        str_status = DEPENDENCY_INVALID  # 无效依赖时的状态码

    # 返回最终推导出的依赖状态码。
    return str_status
