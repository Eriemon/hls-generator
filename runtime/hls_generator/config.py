"""读取并校验 HLS generator runtime_config.json 的运行时配置入口。"""

# 延迟注解避免导入期解析复杂容器类型。
from __future__ import annotations

# JSON、环境变量与正则替换负责配置载荷解析和路径模板展开。
import json
import os
import re

# 深拷贝保护调用方收到的配置副本。
from copy import deepcopy

# LRU 缓存避免反复读取同一份 runtime 配置文件。
from functools import lru_cache

# Path 负责仓库边界和配置路径解析。
from pathlib import Path

# Any 仅用于 JSON 风格对象和动态配置项标注。
from typing import Any

# 依赖扫描入口负责 remote-ssh 与 Vitis 技能发现。
from .skill_dependencies import (
    check_skill_dependencies,
    find_installed_skill,
    validate_skill_dependency_config,
)

# 环境变量允许调用方覆盖 runtime_config.json 位置。
CONFIG_ENV_VAR = "HLS_GENERATOR_RUNTIME_CONFIG"  # runtime 配置覆盖环境变量名

# 默认配置文件与本模块位于同一目录。
DEFAULT_CONFIG_NAME = "runtime_config.json"  # 默认 runtime 配置文件名

# 返回技能目录，供所有 skill-root 相对路径校验复用。
def skill_root() -> Path:
    """
    返回当前技能的根目录。

    参数:
        无外部业务参数；目录层级完全由当前文件位置推导。
    返回:
        已解析的技能根目录路径。
    """

    # runtime/hls_generator/config.py 固定位于技能根目录下两层。
    return Path(__file__).resolve().parents[2]

# 返回仓库根目录，供环境变量覆盖路径做仓库边界约束。
def repo_root() -> Path:
    """
    返回当前工作仓库的根目录。

    参数:
        无外部业务参数；仓库位置由 skill_root 的父层级推导。
    返回:
        已解析的仓库根目录路径。
    """

    # 当前技能固定位于仓库根下的 skills/erie-hls-generator 位置。
    return skill_root().parents[1]

# 解析 runtime 配置文件路径，并拒绝越出仓库的环境变量覆盖值。
def config_path() -> Path:
    """
    返回当前应读取的 runtime 配置文件路径。

    参数:
        无外部业务参数；可选覆盖值来自环境变量 ``HLS_GENERATOR_RUNTIME_CONFIG``。
    返回:
        已解析的 runtime 配置文件绝对路径。
    异常:
        ValueError: 环境变量覆盖路径越出仓库边界时抛出。
    """

    # 读取调用方传入的可选配置文件覆盖值。
    str_raw_override = os.environ.get(CONFIG_ENV_VAR) or ""  # 原始环境变量覆盖值

    # 只有显式覆盖时才需要执行仓库边界校验。
    if str_raw_override:

        # 先把原始字符串转换成路径对象，便于统一解析。
        path_override_candidate = Path(str_raw_override)  # 环境变量提供的候选配置路径

        # 相对路径默认挂到仓库根目录下解析。
        if not path_override_candidate.is_absolute():

            # 让相对覆盖路径与仓库根对齐，保持 CI 与本地行为一致。
            path_override_candidate = repo_root() / path_override_candidate  # 仓库根对齐后的覆盖配置路径

        # 统一得到绝对规范路径，避免后续 relative_to 受符号路径影响。
        path_resolved_override = path_override_candidate.resolve()  # 已解析的覆盖配置路径

        # 通过 relative_to 保证覆盖路径仍留在当前仓库内。
        try:

            # 仓库内路径允许继续作为 runtime 配置来源。
            path_resolved_override.relative_to(repo_root())

        # 越界路径会破坏仓库治理边界，因此立即阻断。
        except ValueError as exc:

            # 抛出带统一前缀的用户可见错误，说明越界环境变量值。
            raise ValueError(
                "> ERR: [Python] Runtime config override must point inside this "
                "repository: "
                + f"{CONFIG_ENV_VAR}={str_raw_override}"
            ) from exc

        # 返回已校验通过的覆盖配置路径。
        return path_resolved_override

    # 未显式覆盖时回退到与本模块同目录的默认配置文件。
    return Path(__file__).with_name(DEFAULT_CONFIG_NAME).resolve()

# 真实的配置文件读取逻辑集中在这个未缓存 helper 中。
def _load_runtime_config_uncached() -> dict[str, Any]:
    """
    读取并解析 runtime 配置 JSON 文件。

    参数:
        无外部业务参数；目标文件路径由 ``config_path()`` 决定。
    返回:
        顶层 JSON 对象形式的 runtime 配置字典。
    异常:
        ValueError: 配置文件缺失、JSON 非法或顶层不是对象时抛出。
    """

    # 先解析当前有效的 runtime 配置文件位置。
    path_runtime_config = config_path()  # 当前 runtime 配置文件路径

    # 读取并解析 JSON 文本时需要分别处理缺失和语法错误。
    try:

        # 只接受 UTF-8 JSON 文本，保持仓库配置编码统一。
        obj_payload = json.loads(path_runtime_config.read_text(encoding="utf-8"))  # JSON 解析后的原始载荷

    # 缺失配置文件时直接给出清晰的阻断原因。
    except FileNotFoundError:

        # 告知调用方当前未找到 runtime 配置文件。
        raise ValueError(
            f"> ERR: [Python] HLS generator runtime config was not found: "
            f"{path_runtime_config}"
        ) from None

    # 非法 JSON 需要保留底层解析错误文本，便于修复逗号和引号问题。
    except json.JSONDecodeError as exc:

        # 直接暴露文件路径和 JSON 错误位置，方便维护者定位。
        raise ValueError(
            f"> ERR: [Python] Invalid HLS generator runtime config JSON in "
            f"{path_runtime_config}: {exc}"
        ) from exc

    # 顶层结构只允许对象，后续所有读取逻辑都依赖 dict 语义。
    if not isinstance(obj_payload, dict):

        # 非对象顶层会破坏所有 key-based 校验入口。
        raise ValueError(
            f"> ERR: [Python] HLS generator runtime config must be a JSON object: "
            f"{path_runtime_config}"
        )

    # 返回通过基础结构校验的配置字典。
    return obj_payload

# 对外暴露带 cache_clear 的配置读取入口，兼容测试与调用方复用。
_cached_runtime_config = lru_cache(maxsize=1)(  # 带缓存的 runtime 配置读取函数
    _load_runtime_config_uncached  # 未缓存的配置读取实现
)

# 向调用方提供深拷贝配置，避免外部原地修改污染缓存。
def runtime_config() -> dict[str, Any]:
    """
    返回 runtime 配置的可修改副本。

    参数:
        无外部业务参数；配置内容来自缓存后的 JSON 读取结果。
    返回:
        与缓存配置解耦的深拷贝字典。
    """

    # 深拷贝让调用方可以安全覆写局部字段而不影响缓存源。
    return deepcopy(_cached_runtime_config())

# 统一触发所有关键配置入口，确保 runtime_config.json 在启动前可用。
def validate_runtime_config() -> None:
    """
    校验 runtime 配置中的关键路径、Vitis 入口和远程验证字段。

    参数:
        无外部业务参数；函数直接扫描当前生效的 runtime 配置。
    返回:
        无业务返回值；所有检查通过时静默结束。
    异常:
        ValueError: 任一关键字段缺失或越界时抛出。
    """

    # 校验默认 workflow 配置路径字段是否可解析到技能根内。
    skill_config_path("default_workflow_config")

    # 校验 examples 目录字段是否可解析到技能根内。
    skill_config_path("examples_dir")

    # 校验 workflow 状态文件路径是否满足 workspace 相对路径约束。
    workflow_state_path()

    # 校验 smoke 输出根路径是否满足 reports 根约束。
    smoke_root_name()

    # 校验允许生成产物写入的顶层目录集合。
    generated_roots()

    # 校验运行期禁止覆盖的受保护顶层目录集合。
    protected_roots()

    # 校验运行期禁止覆盖的受保护顶层文件集合。
    protected_files()

    # 校验“本机缺少 Vitis 工具”对应的阻断工具 ID。
    missing_vitis_tool_id()

    # 校验每个 Vitis 工具定义都具备 name 和 command。
    vitis_tools()

    # 校验 Vitis 技能路由配置的首选与回退列表。
    vitis_skill_routing()

    # 校验 Vitis Tcl 目录前缀与 solution 名称配置。
    vitis_tcl_config()

    # compile/execute/implement/cosim 四个阶段都必须给出超时配置。
    for str_stage_name in ("compile", "execute", "implement", "cosim"):

        # 逐阶段读取超时配置，确保运行时不会缺字段。
        vitis_tool_timeout(str_stage_name)

    # 远程验证配置还会串联检查 remote-ssh 技能目录与目录合同。
    remote_validation_config()

    # 依赖技能配置需要通过统一 schema 校验。
    skill_dependencies_config()

# 读取 skill-root 相对路径字段，并限制其仍位于技能目录内。
def skill_config_path(key: str) -> Path:
    """
    返回指向技能目录内部的配置路径。

    参数:
        key: ``runtime_config.paths`` 中要求相对 skill_root 的字段名。
    返回:
        已解析且位于技能根目录内的绝对路径。
    异常:
        ValueError: 路径为绝对路径或越出技能根目录时抛出。
    """

    # 先读取字符串化后的路径配置值。
    str_path_value = _path_config_value(key)  # 目标配置字段的原始路径字符串

    # Path 对象统一负责绝对/相对和越界判断。
    path_candidate = Path(str_path_value)  # 以技能根为锚点的候选路径

    # 这类路径必须写成 skill_root 相对路径，避免环境依赖。
    if path_candidate.is_absolute():

        # 绝对路径会破坏技能打包可移植性，因此直接拒绝。
        raise ValueError(
            f"> ERR: [Python] Configured path {key!r} must be relative to the "
            f"skill root: {str_path_value}"
        )

    # 解析技能内最终路径，并处理诸如 a/../b 的标准化场景。
    path_resolved = (skill_root() / path_candidate).resolve()  # 已解析的技能内配置路径

    # 解析后的路径仍必须落在技能根目录内部。
    try:

        # 只有技能根内部路径才允许继续参与读取。
        path_resolved.relative_to(skill_root())

    # 越界路径通常意味着配置出现了 traversal 风险。
    except ValueError as exc:

        # 抛出明确错误，告知调用方当前字段越出了技能边界。
        raise ValueError(
            f"> ERR: [Python] Configured path {key!r} must stay inside the "
            f"skill root: {str_path_value}"
        ) from exc

    # 返回通过边界校验的技能内路径。
    return path_resolved

# workflow-state 文件必须保持为 workspace 根相对路径，避免写出仓库外。
def workflow_state_path() -> Path:
    """
    返回 workflow-state 文件的相对路径配置。

    参数:
        无外部业务参数；字段固定读取 ``paths.workflow_state_file``。
    返回:
        仍保持相对 workspace 根目录语义的路径对象。
    异常:
        ValueError: 路径包含绝对定位或 ``.`` / ``..`` 穿越片段时抛出。
    """

    # 读取 workflow-state 相对路径字段。
    path_state_file = Path(_path_config_value("workflow_state_file"))  # workflow-state 相对路径

    # workflow-state 只允许普通相对路径，不允许绝对路径或目录穿越。
    if path_state_file.is_absolute() or any(
        part in {"", ".", ".."} for part in path_state_file.parts
    ):

        # 统一阻断不安全的 workflow-state 路径配置。
        raise ValueError(
            "> ERR: [Python] Runtime config paths.workflow_state_file must be "
            "relative to the workspace root."
        )

    # 返回仍保留 workspace 相对语义的路径对象。
    return path_state_file

# smoke 根目录最终会用于 reports 下的生成产物目录治理。
def smoke_root_name() -> str:
    """
    返回 smoke 产物根目录的 workspace 相对路径字符串。

    参数:
        无外部业务参数；字段固定读取 ``paths.smoke_root``。
    返回:
        规范化为 POSIX 分隔符的 workspace 相对路径字符串。
    异常:
        ValueError: 路径包含绝对定位、家目录展开或目录穿越时抛出。
    """

    # 先把 Windows 反斜杠统一为 POSIX 风格，方便 JSON 与远端复用。
    str_smoke_root = _path_config_value("smoke_root").replace("\\", "/")  # 规范化后的 smoke 根路径字符串

    # Path 仅用于做绝对路径和片段合法性检查。
    path_smoke_root = Path(str_smoke_root)  # smoke 根路径片段对象

    # smoke_root 必须保持普通 workspace 相对路径语义。
    if path_smoke_root.is_absolute() or str_smoke_root.startswith("~/") or any(
        part in {"", ".", ".."} for part in path_smoke_root.parts
    ):

        # 阻断任何可能写出工作区或依赖用户目录的路径配置。
        raise ValueError(
            "> ERR: [Python] Runtime config paths.smoke_root must be a relative "
            "workspace path without traversal."
        )

    # 返回统一为 POSIX 分隔符的相对路径字符串。
    return str_smoke_root

# generated_roots 描述允许运行期写入生成产物的顶层目录集合。
def generated_roots() -> set[str]:
    """
    返回允许运行期写入的顶层生成目录集合。

    参数:
        无外部业务参数；字段固定读取 ``paths.generated_roots``。
    返回:
        只包含顶层目录名的集合。
    """

    # generated_roots 与受保护清单共享同一套顶层名称合同校验。
    return _path_name_set("generated_roots")

# protected_roots 描述禁止运行期覆盖的顶层技能主体目录集合。
def protected_roots() -> set[str]:
    """
    返回禁止运行期写入的顶层目录集合。

    参数:
        无外部业务参数；字段固定读取 ``paths.protected_roots``。
    返回:
        只包含顶层目录名的集合。
    """

    # protected_roots 通过顶层目录名校验守住技能主体边界。
    return _path_name_set("protected_roots")

# protected_files 描述禁止运行期覆盖的顶层文件集合。
def protected_files() -> set[str]:
    """
    返回禁止运行期写入的顶层文件集合。

    参数:
        无外部业务参数；字段固定读取 ``paths.protected_files``。
    返回:
        只包含顶层文件名的集合。
    """

    # protected_files 通过顶层文件名校验守住关键入口文件。
    return _path_name_set("protected_files")

# 运行期写入边界同时覆盖受保护目录和受保护文件。
def protected_write_targets() -> set[str]:
    """
    返回所有禁止运行期写入的顶层目标集合。

    参数:
        无外部业务参数；结果由受保护目录和文件集合并得到。
    返回:
        受保护目录名与文件名的并集。
    """

    # 统一把目录和文件保护名单折叠到一个集合中。
    return protected_roots() | protected_files()

# 读取并校验 Vitis 工具列表，保证每个条目都可参与命令拼装。
def vitis_tools() -> list[dict[str, Any]]:
    """
    返回经过基础校验的 Vitis 工具配置列表。

    参数:
        无外部业务参数；字段固定读取 ``vitis.tools``。
    返回:
        每个条目都具备 ``name`` 和非空 ``command`` 列表的工具配置副本。
    异常:
        ValueError: 列表缺失、为空或条目结构不合法时抛出。
    """

    # 原始工具列表来自 vitis 配置对象。
    list_raw_tools = _vitis_config().get("tools", [])  # 原始 Vitis 工具列表配置

    # vitis.tools 至少需要一个工具定义，供本机探测或回退使用。
    if not isinstance(list_raw_tools, list) or not list_raw_tools:

        # 空工具列表会让本机 Vitis 检测和命令构造完全失效。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.tools must be a non-empty list."
        )

    # 逐项校验后返回深拷贝，避免调用方原地修改底层配置。
    list_validated_tools: list[dict[str, Any]] = []  # 已校验通过的 Vitis 工具配置列表

    # 每个条目都必须是对象，并且同时具备 name 与 command。
    for obj_tool_item in list_raw_tools:

        # 非对象条目无法承载 name/command 结构。
        if not isinstance(obj_tool_item, dict):

            # 立即阻断错误结构，避免后续 get 调用掩盖问题。
            raise ValueError(
                "> ERR: [Python] Each Vitis tool config must be a JSON object."
            )

        # 工具名需要保留为去空格后的稳定字符串。
        str_tool_name = str(obj_tool_item.get("name") or "").strip()  # 当前工具的规范化名称

        # command 字段用于后续把 Tcl 路径填充到命令模板中。
        list_command_template_candidate = obj_tool_item.get("command")  # 当前工具的命令模板候选值

        # name 和 command 任一缺失都会让工具无法执行。
        if (
            not str_tool_name
            or not isinstance(list_command_template_candidate, list)
            or not list_command_template_candidate
        ):

            # 明确要求每个工具项都提供 name 与非空 command 列表。
            raise ValueError(
                "> ERR: [Python] Each Vitis tool config requires name and "
                "command list."
            )

        # 追加深拷贝副本，避免调用方影响缓存中的原始配置。
        list_validated_tools.append(deepcopy(obj_tool_item))

    # 返回所有通过校验的工具配置副本。
    return list_validated_tools

# 对外暴露工具名元组，便于拼接阻断 ID 集合与 CLI 选项。
def vitis_tool_names() -> tuple[str, ...]:
    """
    返回所有 Vitis 工具的名称元组。

    参数:
        无外部业务参数；结果来自 ``vitis_tools()`` 的校验结果。
    返回:
        按配置顺序排列的工具名称元组。
    """

    # 只保留工具名称字段，供探测与报告层使用。
    return tuple(str(obj_tool["name"]) for obj_tool in vitis_tools())

# 读取当前仓库约定的 Vitis 技能路由优先级。
def vitis_skill_routing() -> dict[str, Any]:
    """
    返回 Vitis 技能的首选与回退路由配置。

    参数:
        无外部业务参数；字段固定读取 ``vitis.skill_routing``。
    返回:
        包含 ``preferred_skill`` 与 ``fallback_skills`` 的字典。
    异常:
        ValueError: 路由对象或字段结构不合法时抛出。
    """

    # 原始路由配置来自 vitis.skill_routing。
    dict_raw_routing = _vitis_config().get("skill_routing", {})  # Vitis 技能路由原始对象

    # 路由配置必须是对象，后续需要读取多个命名字段。
    if not isinstance(dict_raw_routing, dict):

        # 非对象路由会破坏 preferred/fallback 读取逻辑。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.skill_routing must be a JSON "
            "object."
        )

    # 首选技能名需要先规整为无首尾空格的字符串。
    str_preferred_skill = str(dict_raw_routing.get("preferred_skill") or "").strip()  # 首选 Vitis 技能名

    # 回退技能列表允许按配置顺序逐项尝试。
    list_fallback_skills_candidate = dict_raw_routing.get("fallback_skills", [])  # 原始回退技能列表候选值

    # 首选技能名缺失时无法形成稳定的路由主入口。
    if not str_preferred_skill:

        # 阻断缺失首选技能的配置，避免上层路由结果不确定。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.skill_routing.preferred_skill "
            "must be set."
        )

    # 回退列表必须存在，确保首选技能未安装时仍有明确顺序。
    if (
        not isinstance(list_fallback_skills_candidate, list)
        or not list_fallback_skills_candidate
    ):

        # 空回退列表会削弱本地技能发现的容错能力。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.skill_routing.fallback_skills "
            "must be a non-empty list."
        )

    # 每个回退项都转成剔除空白后的字符串。
    list_fallback_skills = [str(item).strip() for item in list_fallback_skills_candidate]  # 规范化后的回退技能名列表

    # 回退列表中的空字符串会让逐项探测逻辑失真。
    if any(not str_skill_name for str_skill_name in list_fallback_skills):

        # 阻断含空项的回退列表，避免生成无意义的探测候选。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.skill_routing.fallback_skills "
            "must contain only non-empty strings."
        )

    # 返回当前仓库约定的首选与回退技能集合。
    return {
        "preferred_skill": str_preferred_skill,
        "fallback_skills": list_fallback_skills,
    }

# 根据当前已安装技能集合解析实际可用的 Vitis 技能入口。
def resolve_vitis_skill_preference(
    *,
    skill_dirs: list[Path] | None = None,
    plugin_cache_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """
    解析当前环境应优先使用的 Vitis 技能。

    参数:
        skill_dirs: 可选的技能目录列表，用于测试或显式覆盖技能搜索路径。
        plugin_cache_dirs: 可选的插件缓存目录列表，用于搜索插件缓存中的技能。
    返回:
        包含首选技能、回退技能、最终选中技能、状态和已安装命中项的字典。
    """

    # 先读取当前仓库声明的技能路由优先级。
    dict_skill_routing = vitis_skill_routing()  # 当前生效的 Vitis 技能路由配置

    # 探测顺序固定为首选技能在前、回退技能依次在后。
    list_candidate_skills = [dict_skill_routing["preferred_skill"], *dict_skill_routing["fallback_skills"]]  # 首选后接回退的技能探测顺序

    # 已命中的安装项会按发现顺序记录到结果中。
    list_installed_matches: list[dict[str, Any]] = []  # 已命中的安装技能记录

    # 逐个候选技能名执行本地安装态探测。
    for str_candidate_skill in list_candidate_skills:

        # 复用 skill_dependencies 中的技能发现逻辑，保证行为一致。
        dict_installed_match = find_installed_skill(  # 当前候选技能的安装命中记录
            str_candidate_skill,  # 当前候选技能名
            skill_dirs=skill_dirs,  # 显式技能目录搜索路径
            plugin_cache_dirs=plugin_cache_dirs,  # 显式插件缓存搜索路径
        )

        # 只有 frontmatter name 与期望技能名一致时才算有效命中。
        if (
            dict_installed_match
            and str(dict_installed_match.get("frontmatter_name") or "")
            == str_candidate_skill
        ):

            # 保存命中的技能元数据，供调用方生成安装来源报告。
            list_installed_matches.append(dict_installed_match)

            # 找到首个合法命中后即可返回成功路由结果。
            return {
                "preferred_skill": dict_skill_routing["preferred_skill"],
                "fallback_skills": dict_skill_routing["fallback_skills"],
                "selected_skill": str_candidate_skill,
                "status": "ok",
                "installed": list_installed_matches,
            }

    # 无命中时仍返回首选技能名，方便上层生成缺失依赖提示。
    return {
        "preferred_skill": dict_skill_routing["preferred_skill"],
        "fallback_skills": dict_skill_routing["fallback_skills"],
        "selected_skill": dict_skill_routing["preferred_skill"],
        "status": "missing",
        "installed": list_installed_matches,
    }

# 把缺失工具提示 ID 与可用工具名合并成一组阻断标识。
def vitis_blocking_tool_ids() -> set[str]:
    """
    返回用于本地 Vitis 工具阻断判断的标识集合。

    参数:
        无外部业务参数；结果来自缺失提示 ID 与工具名列表。
    返回:
        含缺失标识和所有工具名称的集合。
    """

    # 上层阻断逻辑需要同时识别“缺工具”与“具体工具名”两类标识。
    return {missing_vitis_tool_id(), *vitis_tool_names()}

# 读取“缺少本机 Vitis”场景对应的工具 ID。
def missing_vitis_tool_id() -> str:
    """
    返回本机 Vitis 工具缺失时使用的阻断标识。

    参数:
        无外部业务参数；字段固定读取 ``vitis.missing_tool_id``。
    返回:
        去除首尾空白后的阻断工具 ID。
    异常:
        ValueError: 字段缺失或为空时抛出。
    """

    # 统一把缺失标识读取为无首尾空白的字符串。
    str_missing_tool_id = str(_vitis_config().get("missing_tool_id") or "").strip()  # 缺失 Vitis 工具标识

    # 缺失标识为空会让上层无法稳定区分本机工具不可用的阻断原因。
    if not str_missing_tool_id:

        # 阻断缺失的工具 ID 配置，保持工具报告结构完整。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.missing_tool_id must be set."
        )

    # 返回配置中的缺失工具标识。
    return str_missing_tool_id

# 按 Tcl 路径替换当前工具命令模板中的占位符。
def vitis_command(tool: dict[str, Any], *, tcl: Path) -> list[str]:
    """
    将 Tcl 文件路径填充到指定 Vitis 工具的命令模板中。

    参数:
        tool: 单个 Vitis 工具配置对象。
        tcl: 需要注入命令模板的 Tcl 文件路径。
    返回:
        已完成格式化的命令参数列表。
    异常:
        ValueError: ``command`` 字段不是非空列表时抛出。
    """

    # 当前工具的 command 列表保存待替换的参数模板。
    list_command_template_candidate = tool.get("command")  # 原始命令模板候选值

    # command 必须是非空列表，否则无法生成可执行命令。
    if (
        not isinstance(list_command_template_candidate, list)
        or not list_command_template_candidate
    ):

        # 报告具体工具名，便于维护者修正配置项。
        raise ValueError(
            f"> ERR: [Python] Vitis tool {tool.get('name')!r} has no command "
            "template."
        )

    # 目前唯一需要注入的模板变量是 Tcl 文件路径。
    dict_replacements = {"tcl": str(tcl)}  # 命令模板占位符替换表

    # 逐段格式化命令模板，保持原有参数顺序不变。
    return [
        str(obj_command_part).format(**dict_replacements)
        for obj_command_part in list_command_template_candidate
    ]

# 读取指定阶段的 Vitis 超时配置，供 compile/execute/cosim 流程复用。
def vitis_tool_timeout(stage: str) -> int:
    """
    返回指定 Vitis 阶段的超时秒数。

    参数:
        stage: 需要读取超时值的阶段名，例如 ``compile`` 或 ``cosim``。
    返回:
        当前阶段对应的超时秒数。
    异常:
        ValueError: ``timeouts_s`` 缺失、不是对象或未声明目标阶段时抛出。
    """

    # timeouts_s 保存所有阶段到秒数的映射关系。
    dict_timeout_map = _vitis_config().get("timeouts_s", {})  # Vitis 阶段超时映射对象候选值

    # 超时配置必须是对象，便于按阶段名读取。
    if not isinstance(dict_timeout_map, dict):

        # 非对象超时映射无法支撑阶段级超时治理。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.timeouts_s must be a JSON "
            "object."
        )

    # 所有需要执行的阶段都必须显式提供超时秒数。
    if stage not in dict_timeout_map:

        # 直接指出缺失的阶段名，便于补齐配置。
        raise ValueError(
            f"> ERR: [Python] Runtime config vitis.timeouts_s.{stage} must be "
            "set."
        )

    # 返回当前阶段声明的超时秒数。
    return int(dict_timeout_map[stage])

# 读取 Vitis Tcl 目录和 solution 命名约定，供本地 Tcl 临时文件生成复用。
def vitis_tcl_config() -> dict[str, str]:
    """
    返回经过完整性校验的 Vitis Tcl 配置对象。

    参数:
        无外部业务参数；字段固定读取 ``vitis.tcl``。
    返回:
        仅包含 ``temp_tcl_prefix``、``project_dir_prefix`` 和 ``solution_name`` 的字典。
    异常:
        ValueError: 对象结构不合法或任一必需字段缺失时抛出。
    """

    # 原始 Tcl 配置对象承载临时脚本和工程目录命名约定。
    obj_raw_tcl_config = _vitis_config().get("tcl", {})  # Vitis Tcl 原始配置对象

    # tcl 字段必须是对象，便于逐项读取命名规则。
    if not isinstance(obj_raw_tcl_config, dict):

        # 非对象配置无法表达多个命名字段。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.tcl must be a JSON object."
        )

    # 三个字段是临时 Tcl 与工程目录生成所必需的最小集合。
    tuple_required_keys = ("temp_tcl_prefix", "project_dir_prefix", "solution_name")  # Tcl 配置必需字段名

    # 收集所有缺失或空字符串字段，便于一次性给出修复指引。
    list_missing_keys = [key for key in tuple_required_keys if not str(obj_raw_tcl_config.get(key) or "").strip()]  # 缺失的 Tcl 配置字段名列表

    # 任一关键字段缺失都无法生成稳定的本地 Vitis 工程。
    if list_missing_keys:

        # 一次性报告所有缺失字段，减少来回修配置的轮次。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis.tcl is missing: "
            + ", ".join(list_missing_keys)
        )

    # 只返回当前模块需要的三个 Tcl 命名字段。
    return {key: str(obj_raw_tcl_config[key]) for key in tuple_required_keys}

# 远程验证配置会串联读取 erie-remote-ssh 技能和目录合同约束。
def remote_validation_config() -> dict[str, Any]:
    """
    返回经过完整校验和路径解析的远程验证配置副本。

    参数:
        无外部业务参数；字段固定读取 ``remote_validation``。
    返回:
        已补全远程技能路径、设置文件路径和目录合同的配置字典。
    异常:
        ValueError: 远程验证配置缺字段、类型错误或路径不满足合同约束时抛出。
    """

    # 原始远程验证配置来自 runtime_config.json。
    dict_remote_validation_source = runtime_config().get("remote_validation", {})  # 远程验证原始配置对象

    # remote_validation 必须是对象，后续需要补全多个命名字段。
    if not isinstance(dict_remote_validation_source, dict):

        # 非对象远程配置无法进入目录解析和超时治理流程。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation must be a JSON "
            "object."
        )

    # 深拷贝后再做路径补全，避免修改原始缓存配置。
    dict_remote_validation = deepcopy(dict_remote_validation_source)  # 可原地补全的远程验证配置副本

    # 解析 remote-ssh 技能目录，支持环境变量覆盖和依赖发现。
    dict_remote_validation["erie_skill_dir"] = str(  # 远程技能目录
        _resolve_erie_remote_skill_dir(dict_remote_validation)  # 解析后的远程技能目录路径
    )

    # 解析默认设置文件位置，兼容 assets/defaults 与 legacy config/defaults。
    dict_remote_validation["erie_settings_path"] = str(  # 远程 settings 文件路径
        _resolve_erie_settings_path(dict_remote_validation)  # 解析后的远程 settings 文件路径
    )

    # local_run_root 必须位于 generated_roots 声明的生成目录之下。
    dict_remote_validation["local_run_root"] = _remote_local_run_root(  # 本地远程验证 run 根路径
        str(_remote_required(dict_remote_validation, "local_run_root"))  # 原始 local_run_root 配置
    )

    # remote_tmp_dir 只允许顶层目录名，避免远端工作区越界。
    dict_remote_validation["remote_tmp_dir"] = _remote_top_level_name(  # 远端临时目录名
        str(_remote_required(dict_remote_validation, "remote_tmp_dir")),  # 待校验的远端临时目录原文
        "remote_tmp_dir",  # remote_tmp_dir 对应的字段键名
    )

    # default_timeout_s 统一转换成正整数，方便上层直接消费。
    dict_remote_validation["default_timeout_s"] = _remote_positive_int(  # 远端默认超时秒数
        dict_remote_validation.get("default_timeout_s"),  # 原始默认超时配置
        "default_timeout_s",  # 当前超时字段名
    )

    # 远端 Python 环境变量需要是非空对象，供子进程稳定继承。
    dict_python_env_source = dict_remote_validation.get("python_env", {})  # 远端 Python 环境变量映射候选值

    # 空对象或非对象都会让远端执行环境不明确。
    if not isinstance(dict_python_env_source, dict) or not dict_python_env_source:

        # 阻断缺失的 python_env，避免远端编码环境不稳定。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation.python_env must be "
            "a non-empty object."
        )

    # 统一把环境变量键和值都转成字符串，避免 JSON 数值进入 os.environ。
    dict_remote_validation["python_env"] = {  # 规范化后的远端 Python 环境变量映射
        str(key): str(value)  # 单个环境变量键值对
        for key, value in dict_python_env_source.items()  # 原始环境变量条目迭代器
    }

    # link_probe_command 用于远端连通性和环境探针执行。
    list_link_probe_command = dict_remote_validation.get("link_probe_command", [])  # 连通性探针命令模板候选值

    # 该命令必须是非空字符串列表，才能安全传给 subprocess。
    if (
        not isinstance(list_link_probe_command, list)
        or not list_link_probe_command
        or not all(
            isinstance(item, str) and item for item in list_link_probe_command
        )
    ):

        # 阻断非法探针命令配置，避免远端探测阶段失败得过于隐蔽。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation.link_probe_command "
            "must be a non-empty list of strings."
        )

    # 远程 Vitis profile 是可选的对象映射，空对象也允许存在。
    dict_vitis_profiles = dict_remote_validation.get("vitis_profiles", {})  # 远端 Vitis profile 配置对象候选值

    # 配置存在时必须保持对象形态，便于按 profile 名读取。
    if not isinstance(dict_vitis_profiles, dict):

        # 非对象 profile 集合无法表达命名 profile。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation.vitis_profiles "
            "must be a JSON object when set."
        )

    # 每个 profile 都必须具备非空名称并且以对象承载详细字段。
    for obj_profile_name, obj_profile_payload in dict_vitis_profiles.items():

        # profile 名或 profile 载荷不合法时直接阻断。
        if not str(obj_profile_name).strip() or not isinstance(
            obj_profile_payload,
            dict,
        ):

            # 远端 profile 需要稳定的命名键和值对象结构。
            raise ValueError(
                "> ERR: [Python] Each remote Vitis profile must be a named JSON "
                "object."
            )

    # 目录合同会进一步约束远端 run、backup 和 platform 路径模板。
    dict_remote_validation["directory_contract"] = _remote_directory_contract(  # 已校验的远端目录合同
        dict_remote_validation  # 需要校验目录合同的远程验证配置
    )

    # 返回已完成路径解析和字段校验的远程验证配置。
    return dict_remote_validation

# skill_dependencies 统一复用 skill_dependencies 模块的 schema 校验逻辑。
def skill_dependencies_config() -> list[dict[str, Any]]:
    """
    返回经过 schema 校验的技能依赖配置列表。

    参数:
        无外部业务参数；字段固定读取 ``skill_dependencies``。
    返回:
        通过 ``validate_skill_dependency_config`` 校验后的依赖配置列表。
    """

    # 让 skill_dependencies 模块统一负责依赖配置合法性校验。
    return validate_skill_dependency_config(runtime_config().get("skill_dependencies", []))

# 解析远程技能目录时优先考虑显式环境变量和已安装依赖发现结果。
def _resolve_erie_remote_skill_dir(config: dict[str, Any]) -> Path:
    """
    解析 erie-remote-ssh 技能目录的最终路径。

    参数:
        config: 远程验证配置字典。
    返回:
        已解析的 erie-remote-ssh 技能目录路径。
    """

    # 先解析配置中声明的远程技能目录模板。
    path_configured_skill_dir = _expand_remote_value(  # 配置中声明的远程技能目录
        _remote_required(config, "erie_skill_dir")  # erie_skill_dir 的模板原文
    )

    # 环境变量显式声明技能搜索目录时，优先尝试依赖发现结果。
    if (
        os.environ.get("HLS_GENERATOR_SKILLS_DIRS") is not None
        or os.environ.get("CODEX_HOME")
    ):

        # 尝试从已安装技能中发现更准确的 remote-ssh 目录。
        path_discovered_skill_dir = _discover_erie_remote_skill_dir()  # 依赖发现得到的远程技能目录

        # 成功发现时优先使用真实安装目录。
        if path_discovered_skill_dir is not None:

            # 返回已安装技能的实际目录，避免继续依赖默认模板值。
            return path_discovered_skill_dir

    # 针对当前配置技能目录生成一组 settings 文件候选路径。
    list_settings_candidates = _erie_settings_candidates(  # 基于配置技能目录的 settings 候选路径列表
        config,  # 远程验证配置对象
        path_configured_skill_dir,  # 已解析的远程技能目录
    )

    # 兼容标准 remote_ssh.py 布局，并要求至少存在一个 settings 文件候选。
    if (
        (path_configured_skill_dir / "scripts" / "remote_ssh.py").exists()
        and any(path_candidate.exists() for path_candidate in list_settings_candidates)
    ):

        # 当前配置目录已满足 helper 文件与 settings 文件存在条件。
        return path_configured_skill_dir

    # 如果配置目录不完整，再尝试从安装依赖中回退发现。
    path_discovered_skill_dir = _discover_erie_remote_skill_dir()  # 回退发现得到的远程技能目录

    # 发现成功时优先使用真实安装目录。
    if path_discovered_skill_dir is not None:

        # 返回已安装技能目录，兼容 legacy 布局和手工迁移场景。
        return path_discovered_skill_dir

    # 最后仍回退到配置声明值，交给上层在缺文件时继续报错。
    return path_configured_skill_dir

# 通过 skill_dependencies 的安装态报告发现已安装的 remote-ssh 技能目录。
def _discover_erie_remote_skill_dir() -> Path | None:
    """
    从技能依赖报告中发现已安装的 erie-remote-ssh 技能目录。

    参数:
        无外部业务参数；依赖配置来自 ``skill_dependencies_config()``。
    返回:
        找到安装记录时返回技能目录路径，否则返回 ``None``。
    """

    # 逐项扫描技能依赖配置，定位 remote-ssh 条目。
    for dict_dependency in skill_dependencies_config():

        # 只处理 remote-ssh 依赖，其他技能与远程配置目录无关。
        if dict_dependency["id"] != "remote-ssh":

            # 非 remote-ssh 依赖直接跳过，继续扫描后续条目。
            continue

        # 复用依赖检查逻辑确认当前 remote-ssh 是否已安装且结构有效。
        dict_dependency_report = check_skill_dependencies([dict_dependency])  # 单条 remote-ssh 依赖检查结果

        # 当前实现只关心首条依赖明细中的安装记录。
        if dict_dependency_report["dependencies"]:

            # 取出首条依赖明细，供安装状态与 installed 字段读取。
            dict_dependency_item = dict_dependency_report["dependencies"][0]  # remote-ssh 依赖明细项

        # 缺少依赖明细时回退到空对象，保持后续 get 逻辑稳定。
        else:

            # 空对象让后续状态读取自然回退到未命中路径。
            dict_dependency_item = {}  # 空的 remote-ssh 依赖明细回退值

        # 只有状态为 ok 且存在 installed 列表时才接受发现结果。
        if (
            dict_dependency_item.get("status") == "ok"
            and dict_dependency_item.get("installed")
        ):

            # 返回第一条安装记录的解析路径，满足当前配置发现需求。
            return Path(dict_dependency_item["installed"][0]["path"]).resolve()

    # 未发现任何可用安装记录时返回 None，让调用方决定回退策略。
    return None

# 在配置技能目录、assets/defaults 和 legacy config/defaults 间选择 settings 文件。
def _resolve_erie_settings_path(config: dict[str, Any]) -> Path:
    """
    解析远程验证应使用的 settings 文件路径。

    参数:
        config: 已补全 ``erie_skill_dir`` 的远程验证配置字典。
    返回:
        首个存在的 settings 文件路径；若都不存在则返回首个候选路径。
    """

    # 基于技能目录生成所有兼容布局的 settings 候选路径。
    list_settings_candidates = _erie_settings_candidates(  # settings 文件候选路径列表
        config,  # 生成候选时使用的完整远程配置
        Path(str(config["erie_skill_dir"])),  # 已规范化的远程技能目录
    )

    # 按声明顺序优先返回实际存在的 settings 文件。
    for path_candidate in list_settings_candidates:

        # 命中的现存文件即可作为当前远程验证配置入口。
        if path_candidate.exists():

            # 返回首个存在的候选路径，保持选择顺序稳定。
            return path_candidate

    # 全部不存在时仍返回首个候选，供上层报错时展示期望位置。
    return list_settings_candidates[0]

# settings 文件同时兼容配置模板路径、assets/defaults 和 legacy config/defaults。
def _erie_settings_candidates(
    config: dict[str, Any],
    erie_skill_dir: Path,
) -> list[Path]:
    """
    生成 erie-remote-ssh settings 文件的候选路径列表。

    参数:
        config: 远程验证配置字典。
        erie_skill_dir: 已解析的 erie-remote-ssh 技能目录。
    返回:
        去重后的候选路径列表，顺序保留为配置路径、assets 默认值、legacy 默认值。
    """

    # 先按显式模板求得 settings 主候选位置，便于与兼容路径一起排序。
    path_configured_settings = _expand_remote_value(  # 配置中声明的 settings 文件路径
        _remote_required(config, "erie_settings_path"),  # settings 文件模板路径的原始文本
        {"erie_skill_dir": str(erie_skill_dir)},  # 模板展开所需的技能目录变量
    )

    # 去重后仍需保留原始声明顺序，避免选择结果抖动。
    list_unique_paths: list[Path] = []  # 去重后的 settings 候选路径列表

    # 用字符串键记录已见路径，兼容不同 Path 实例的同值比较。
    set_seen_paths: set[str] = set()  # 已去重的候选路径字符串集合

    # 依次遍历显式路径、标准 assets 默认值和 legacy 默认值三个候选。
    for path_candidate in [
        path_configured_settings,  # 显式 settings 路径候选
        (erie_skill_dir / "assets" / "defaults.json").resolve(),  # 标准 assets/defaults 候选
        (erie_skill_dir / "config" / "defaults.json").resolve(),  # legacy config/defaults 候选
    ]:

        # 统一把候选路径转换成可哈希字符串键。
        str_path_key = str(path_candidate)  # 候选路径的去重键

        # 未出现过的路径才需要保留到结果列表。
        if str_path_key not in set_seen_paths:

            # 登记当前路径键，避免后续重复追加。
            set_seen_paths.add(str_path_key)

            # 把首次出现的候选路径加入最终结果。
            list_unique_paths.append(path_candidate)

    # 返回按声明顺序去重后的候选路径列表。
    return list_unique_paths

# paths 对象中的单个字符串字段都通过这个 helper 统一读取。
def _path_config_value(key: str) -> str:
    """
    读取 ``runtime_config.paths`` 下的单个字符串字段。

    参数:
        key: 需要读取的 ``paths`` 子字段名。
    返回:
        去除首尾空白后的字段字符串值。
    异常:
        ValueError: ``paths`` 不是对象或目标字段缺失时抛出。
    """

    # 所有路径类配置都集中放在顶层 paths 对象中。
    dict_paths = runtime_config().get("paths", {})  # 当前 runtime 配置中的 paths 总表快照

    # paths 必须是对象，便于后续逐字段读取。
    if not isinstance(dict_paths, dict):

        # 非对象 paths 会破坏所有路径字段的读取假设。
        raise ValueError(
            "> ERR: [Python] Runtime config paths must be a JSON object."
        )

    # 把目标字段统一读取成无首尾空白的字符串。
    str_path_value = str(dict_paths.get(key) or "").strip()  # 当前 paths 子字段的字符串值

    # 空字符串视作未声明，无法支撑路径解析逻辑。
    if not str_path_value:

        # 直接指出缺失字段名，便于维护者补齐 paths 配置。
        raise ValueError(
            f"> ERR: [Python] Runtime config paths.{key} must be set."
        )

    # 返回通过非空校验的路径字符串。
    return str_path_value

# generated_roots / protected_roots / protected_files 都要求是顶层名称列表。
def _path_name_set(key: str) -> set[str]:
    """
    读取并校验 ``paths`` 下只允许顶层名称的字符串列表。

    参数:
        key: 需要读取的 ``paths`` 子字段名。
    返回:
        去重后的顶层名称集合。
    异常:
        ValueError: 字段不是非空列表，或条目不是合法顶层名称时抛出。
    """

    # 从 paths 总表提取当前键对应的名字列表，再执行顶层边界校验。
    dict_paths = runtime_config().get("paths", {})  # runtime 配置中的 paths 对象

    # 只有对象形态的 paths 才可能包含目标列表字段。
    list_raw_names = dict_paths.get(key) if isinstance(dict_paths, dict) else None  # 目标名称列表的原始配置

    # 顶层名称集合至少要有一个成员，且必须以列表承载。
    if not isinstance(list_raw_names, list) or not list_raw_names:

        # 空列表无法表达有效的目录或文件边界合同。
        raise ValueError(
            f"> ERR: [Python] Runtime config paths.{key} must be a non-empty list."
        )

    # 逐项校验名称合法性后构造去重集合。
    set_valid_names: set[str] = set()  # 通过校验的顶层名称集合

    # 每个条目都必须是无路径分隔符的顶层名称。
    for obj_name_item in list_raw_names:

        # 统一规范化为 POSIX 风格名称，便于跨平台比较。
        str_name_value = str(obj_name_item).strip().replace("\\", "/")  # 规范化后的顶层名称

        # 顶层名称不允许为空、包含斜杠或使用当前/父目录别名。
        if (
            not str_name_value
            or "/" in str_name_value
            or str_name_value in {".", ".."}
        ):

            # 直接报告非法条目文本，便于修复具体配置值。
            raise ValueError(
                f"> ERR: [Python] Runtime config paths.{key} entries must be "
                f"top-level names: {obj_name_item!r}"
            )

        # 记录通过校验的顶层名称，自动完成去重。
        set_valid_names.add(str_name_value)

    # 返回去重后的顶层名称集合。
    return set_valid_names

# vitis 对象是多个 Vitis 相关 helper 的共同入口。
def _vitis_config() -> dict[str, Any]:
    """
    读取顶层 ``vitis`` 配置对象。

    参数:
        无外部业务参数；字段固定读取 ``runtime_config.vitis``。
    返回:
        Vitis 配置对象。
    异常:
        ValueError: ``vitis`` 不是对象时抛出。
    """

    # 统一读取顶层 vitis 配置对象。
    dict_vitis_config = runtime_config().get("vitis", {})  # 顶层 Vitis 配置对象候选值

    # vitis 必须是对象，后续各 helper 都依赖 key-based 读取。
    if not isinstance(dict_vitis_config, dict):

        # 非对象 vitis 配置会破坏工具、超时和路由读取逻辑。
        raise ValueError(
            "> ERR: [Python] Runtime config vitis must be a JSON object."
        )

    # 返回通过结构校验的 Vitis 配置对象。
    return dict_vitis_config

# 远程验证配置中的必填字符串字段统一通过这个 helper 读取。
def _remote_required(config: dict[str, Any], key: str) -> str:
    """
    读取远程验证配置中的必填字符串字段。

    参数:
        config: 远程验证配置字典。
        key: 需要读取的字段名。
    返回:
        去除首尾空白后的字段字符串值。
    异常:
        ValueError: 字段缺失或为空字符串时抛出。
    """

    # 这里直接把字段规整成纯净字符串，方便后续路径和命令 helper 复用。
    str_required_value = str(config.get(key) or "").strip()  # 远程验证必填字段值

    # 空值视作未配置，无法支撑后续路径解析和命令构造。
    if not str_required_value:

        # 直接指出缺失字段名，便于补齐远程验证配置。
        raise ValueError(
            f"> ERR: [Python] Runtime config remote_validation.{key} must be set."
        )

    # 返回通过非空校验的字段值。
    return str_required_value

# 远程路径模板允许引用 ${home}、${skill_root}、${erie_skill_dir} 和环境变量。
def _expand_remote_value(
    value: str,
    extra: dict[str, str] | None = None,
) -> Path:
    """
    展开远程配置中的模板路径并返回绝对路径。

    参数:
        value: 可能包含 ``${...}``、环境变量或 ``~`` 的路径模板字符串。
        extra: 额外的模板变量映射，例如 ``erie_skill_dir``。
    返回:
        完成模板展开后的绝对路径。
    """

    # 内置替换变量覆盖 home 与当前技能根目录。
    dict_replacements = {  # 内置路径模板变量映射
        "home": str(Path.home()),  # 当前用户主目录
        "skill_root": str(skill_root()),  # 当前技能根目录
    }

    # 调用方可额外注入 erie_skill_dir 等模板变量。
    if extra:

        # 追加外部变量映射，优先级高于默认表。
        dict_replacements.update(extra)

    # 正则回调负责按键名替换 ${...} 模板片段。
    def replace(match: re.Match[str]) -> str:
        """
        替换单个 ``${...}`` 模板片段。

        参数:
            match: 正则捕获到的模板变量匹配结果。
        返回:
            命中的替换值；未知变量时保留原始模板文本。
        """

        # 捕获组内容是模板变量名或 env: 前缀字段。
        str_template_key = match.group(1)  # 当前模板变量键名

        # env: 前缀允许直接读取宿主环境变量。
        if str_template_key.startswith("env:"):

            # 环境变量缺失时回退为空字符串，保持旧行为兼容。
            return os.environ.get(str_template_key[4:], "")

        # 其余变量优先从替换表中读取，未知变量保留原模板文本。
        return dict_replacements.get(str_template_key, match.group(0))

    # 依次执行 ${...}、环境变量与用户目录展开，并返回绝对路径。
    return Path(
        os.path.expandvars(
            os.path.expanduser(re.sub(r"\$\{([^}]+)\}", replace, value))
        )
    ).resolve()

# local_run_root 必须落在 generated_roots 允许写入的顶层目录之下。
def _remote_local_run_root(value: str) -> str:
    """
    校验远程验证在本地工作区中的 run 根路径。

    参数:
        value: ``remote_validation.local_run_root`` 的原始字符串值。
    返回:
        规范化为 POSIX 分隔符的相对路径字符串。
    异常:
        ValueError: 路径不是 generated_roots 下的相对路径时抛出。
    """

    # 使用 Path 解析片段，便于检查绝对路径和 traversal。
    path_local_run_root = Path(value)  # 远程验证本地 run 根路径片段

    # 该路径必须保持相对 generated_root 的普通相对路径形态。
    if path_local_run_root.is_absolute() or any(
        part in {"", ".", ".."} for part in path_local_run_root.parts
    ):

        # 阻断任何绝对路径或目录穿越配置。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation.local_run_root must "
            "be a relative path inside a generated root."
        )

    # 第一个路径片段必须命中 generated_roots 白名单。
    str_first_component = path_local_run_root.parts[0] if path_local_run_root.parts else ""  # local_run_root 的顶层目录名

    # 顶层目录不在白名单内时说明 run 路径可能写入受保护区域。
    if str_first_component not in generated_roots():

        # 直接给出允许的 generated_roots 列表，方便修配置。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation.local_run_root must "
            "start with one of: "
            + ", ".join(sorted(generated_roots()))
            + "."
        )

    # 返回统一为 POSIX 分隔符的本地 run 根路径字符串。
    return value.replace("\\", "/")

# 某些远程目录字段只允许顶层目录名，不允许子路径和 traversal。
def _remote_top_level_name(value: str, key: str) -> str:
    """
    校验远程验证中的顶层目录名字段。

    参数:
        value: 目标字段的原始字符串值。
        key: 当前字段名，用于错误消息。
    返回:
        通过校验的顶层目录名字串。
    异常:
        ValueError: 字段为空、包含路径分隔符或使用 ``.`` / ``..`` 时抛出。
    """

    # 统一使用 POSIX 分隔符检查顶层目录名结构。
    str_normalized_name = value.replace("\\", "/")  # 规范化后的顶层目录名字串

    # 顶层目录名不允许为空、含斜杠或使用当前/父目录别名。
    if "/" in str_normalized_name or str_normalized_name in {"", ".", ".."}:

        # 直接报告字段名，便于维护者修正该配置项。
        raise ValueError(
            f"> ERR: [Python] Runtime config remote_validation.{key} must be a "
            "top-level relative directory name."
        )

    # 返回通过校验的顶层目录名字串。
    return str_normalized_name

# 远程超时和整数型目录合同字段都复用这个正整数解析 helper。
def _remote_positive_int(value: Any, key: str) -> int:
    """
    将远程验证字段解析为正整数。

    参数:
        value: 待解析的原始字段值。
        key: 当前字段名，用于错误消息。
    返回:
        通过校验的正整数值。
    异常:
        ValueError: 值无法转成整数或小于 1 时抛出。
    """

    # 先尝试统一转成整数，兼容 JSON 数字和字符串数字。
    try:

        # int() 负责完成最基础的数字解析。
        int_value = int(value)  # 解析后的整数值

    # 非数字输入在这里统一转成配置错误。
    except (TypeError, ValueError) as exc:

        # 当前字段必须是正整数，供超时与目录合同安全使用。
        raise ValueError(
            f"> ERR: [Python] Runtime config remote_validation.{key} must be a "
            "positive integer."
        ) from exc

    # 正整数下限固定为 1，禁止 0 和负数进入运行时。
    if int_value < 1:

        # 负数或零会让超时或数量语义失真，因此直接阻断。
        raise ValueError(
            f"> ERR: [Python] Runtime config remote_validation.{key} must be a "
            "positive integer."
        )

    # 返回通过校验的正整数值。
    return int_value

# 远程目录合同统一约束远端技能目录、run 目录和平台路径模板。
def _remote_directory_contract(config: dict[str, Any]) -> dict[str, Any]:
    """
    读取并校验远程验证目录合同配置。

    参数:
        config: 远程验证配置字典。
    返回:
        含项目根、conda 前缀、run 模板和 archive 策略的目录合同字典。
    异常:
        ValueError: 目录合同对象或其字段结构不合法时抛出。
    """

    # 目录合同所有字段都集中在 remote_validation.directory_contract 下。
    dict_directory_contract = config.get("directory_contract", {})  # 远程目录合同原始对象候选值

    # 目录合同必须是对象，便于逐字段应用不同路径规则。
    if not isinstance(dict_directory_contract, dict):

        # 非对象目录合同无法承载命名模板字段。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation.directory_contract "
            "must be a JSON object."
        )

    # 远端项目根目录名只允许顶层目录名语义。
    str_project_root_dirname = _remote_top_level_name(  # 远端项目根目录名
        str(dict_directory_contract.get("project_root_dirname") or "").strip(),  # 原始项目根目录名配置
        "directory_contract.project_root_dirname",  # project_root_dirname 的字段键名
    )

    # conda 前缀路径必须是普通相对路径。
    str_conda_prefix_path = _remote_relative_path(  # 远端 conda 前缀相对路径
        str(dict_directory_contract.get("conda_prefix_path") or "").strip(),  # 原始 conda 前缀路径配置
        "directory_contract.conda_prefix_path",  # 当前相对路径字段
    )

    # 活动 run 路径模板必须包含 <run-id> 占位符。
    str_active_run_path_template = _remote_relative_path(  # 活动 run 相对路径模板
        str(dict_directory_contract.get("active_run_path_template") or "").strip(),  # 原始活动 run 模板
        "directory_contract.active_run_path_template",  # 当前活动 run 字段
        require_run_id=True,  # 备份 run 模板必须携带实例编号
    )

    # 备份 run 路径模板同样必须包含 <run-id> 占位符。
    str_backup_run_path_template = _remote_relative_path(  # 备份 run 相对路径模板
        str(dict_directory_contract.get("backup_run_path_template") or "").strip(),  # 原始备份 run 模板
        "directory_contract.backup_run_path_template",  # 当前备份 run 字段
        require_run_id=True,  # 该模板必须携带 run-id 占位符
    )

    # 平台目录模板必须包含 <platform-name> 占位符。
    str_platform_root_path_template = _remote_relative_path(  # 平台目录相对路径模板
        str(dict_directory_contract.get("platform_root_path_template") or "").strip(),  # 原始平台目录模板
        "directory_contract.platform_root_path_template",  # 当前平台目录字段
        require_platform_name=True,  # 平台目录模板必须携带板卡名占位符
    )

    # archive_after_verification 决定远端产物是否在通过验证后归档。
    bool_archive_after_verification = dict_directory_contract.get("archive_after_verification")  # 归档开关原始值

    # 归档开关必须显式使用布尔值，避免字符串 true/false 混入。
    if not isinstance(bool_archive_after_verification, bool):

        # 非布尔归档开关会让自动归档条件不明确。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation.directory_contract."
            "archive_after_verification must be a boolean."
        )

    # archive_trigger 用于把归档条件写入远端报告和回执。
    str_archive_trigger = str(dict_directory_contract.get("archive_trigger") or "").strip()  # 归档触发条件文本

    # 归档条件文本缺失时无法向用户解释何时发生备份。
    if not str_archive_trigger:

        # 直接阻断缺失的 archive_trigger 字段。
        raise ValueError(
            "> ERR: [Python] Runtime config remote_validation.directory_contract."
            "archive_trigger must be set."
        )

    # 返回通过校验的目录合同对象，供远端验证流程直接消费。
    return {
        "project_root_dirname": str_project_root_dirname,
        "conda_prefix_path": str_conda_prefix_path,
        "active_run_path_template": str_active_run_path_template,
        "backup_run_path_template": str_backup_run_path_template,
        "platform_root_path_template": str_platform_root_path_template,
        "archive_after_verification": bool_archive_after_verification,
        "archive_trigger": str_archive_trigger,
    }

# 远程相对路径字段支持额外的 run-id 和 platform-name 占位符合同。
def _remote_relative_path(
    value: str,
    key: str,
    *,
    require_run_id: bool = False,
    require_platform_name: bool = False,
) -> str:
    """
    校验远程验证目录合同中的相对 POSIX 路径字段。

    参数:
        value: 目标字段的原始字符串值。
        key: 当前字段名，用于错误消息。
        require_run_id: 是否要求路径模板包含 ``<run-id>``。
        require_platform_name: 是否要求路径模板包含 ``<platform-name>``。
    返回:
        通过校验的相对 POSIX 路径字符串。
    异常:
        ValueError: 路径为空、为绝对路径、包含 traversal，或缺少必需占位符时抛出。
    """

    # 统一转换成去首尾空白的 POSIX 路径文本。
    str_normalized_path = value.replace("\\", "/").strip()  # 规范化后的相对路径字符串

    # 空路径无法表达目录合同中的任何有效位置。
    if not str_normalized_path:

        # 直接指出缺失字段名，便于补齐目录合同配置。
        raise ValueError(
            f"> ERR: [Python] Runtime config remote_validation.{key} must be set."
        )

    # Path 仅用于判断绝对路径与 traversal 片段。
    path_relative_value = Path(str_normalized_path)  # 远程目录合同路径片段对象

    # 目录合同路径必须保持普通相对 POSIX 路径语义。
    if path_relative_value.is_absolute() or str_normalized_path.startswith("~/") or any(
        part in {"", ".", ".."} for part in path_relative_value.parts
    ):

        # 拒绝所有绝对路径和目录穿越写法，避免远端工作区越界。
        raise ValueError(
            f"> ERR: [Python] Runtime config remote_validation.{key} must be a "
            "relative POSIX path."
        )

    # 需要 run-id 占位符时必须显式出现在模板中。
    if require_run_id and "<run-id>" not in str_normalized_path:

        # 缺失 run-id 会让多轮运行产物无法按实例区分。
        raise ValueError(
            f"> ERR: [Python] Runtime config remote_validation.{key} must include "
            "<run-id>."
        )

    # 平台目录模板缺少 platform-name 时，会失去不同板卡目录的区分能力。
    if require_platform_name and "<platform-name>" not in str_normalized_path:

        # 缺失 platform-name 会让 U50/U55C 等平台目录映射失效。
        raise ValueError(
            f"> ERR: [Python] Runtime config remote_validation.{key} must include "
            "<platform-name>."
        )

    # 返回通过校验的相对 POSIX 路径字符串。
    return str_normalized_path
