"""解析 AGENTS.md 中的远端 HLS 验收路由契约。"""

# future 注解推迟解析，保持运行时导入成本稳定。
from __future__ import annotations

# json 用于读取 agents-md-generator 控制档，re 用于兼容旧 AGENTS route 文本。
import json
import re
from pathlib import Path
from typing import Any

# 默认远端验收路由名称来自项目 AGENTS 契约。
REMOTE_ROUTE_NAME = "remote-hls-validation"  # HLS 远端验收路由名

# 根据技能根目录回推当前仓库根目录。
def repo_root_from_skill_root(skill_root: Path) -> Path:
    """从技能根目录推导仓库根目录。

    参数:
        skill_root: `skills/<skill-name>` 形式的技能根目录路径。

    返回:
        当前工作仓库根目录路径。
    """

    # erie-hls-generator 位于 skills/ 下，向上两级是仓库根。
    return skill_root.parents[1]

# 根据技能根目录定位根级 AGENTS.md。
def root_agents_path(skill_root: Path) -> Path:
    """返回仓库根级 AGENTS.md 路径。

    参数:
        skill_root: `skills/<skill-name>` 形式的技能根目录路径。

    返回:
        仓库根目录下的 `AGENTS.md` 路径。
    """

    # 路由契约统一写在仓库根级 AGENTS.md 中。
    return repo_root_from_skill_root(skill_root) / "AGENTS.md"

# 根据技能根目录定位 agents-md-generator 控制档。
def agents_control_path(skill_root: Path) -> Path:
    """返回仓库根级 agents-control.json 路径。

    参数:
        skill_root: `skills/<skill-name>` 形式的技能根目录路径。

    返回:
        仓库根目录下的 `.agents/agents-control.json` 路径。
    """

    # 新版 AGENTS.md 只保留 route source 指针，真实路由表在控制档中。
    return repo_root_from_skill_root(skill_root) / ".agents" / "agents-control.json"

# 从 AGENTS.md 正文中解析指定远端路由。
def parse_remote_route_contract(
    agents_text: str,
    *,
    route_name: str = REMOTE_ROUTE_NAME,
) -> dict[str, Any]:
    """解析远端 HLS 验收路由和已登记服务器列表。

    参数:
        agents_text: 根级 AGENTS.md 的完整文本。
        route_name: 需要解析的远端验收路由名称。

    返回:
        包含 route_name、primary、fallbacks 和 registered_servers 的契约字典。

    异常:
        ValueError: 当 AGENTS.md 中缺少指定 route 定义时抛出。
    """

    # route 正则只匹配 AGENTS 生成契约中的固定表述，避免误读其他段落。
    pattern_route: re.Pattern[str] = re.compile(  # 提取指定远端路由的主服务器与 fallback 原始文本
        rf"Task route `{re.escape(route_name)}`:\s*primary `([^`]+)`;\s*fallbacks:\s*([^\n]+)\.",  # 匹配 route 行里的主服务器与 fallback 字段
        re.IGNORECASE,  # 允许 AGENTS 契约文本大小写差异
    )  # 远端任务路由定义正则

    # 缺少 route 定义时不能继续推断服务器映射。
    if pattern_route.search(agents_text) is None:

        # 错误文本带 current-project 前缀，便于调用方统一呈现。
        raise ValueError(f"> ERR: [Python] Could not find route definition for {route_name!r}.")

    # primary 是 route 表中的唯一主服务器。
    str_primary: str = pattern_route.search(agents_text).group(1).strip()  # route 主服务器标识

    # fallback 文本可能是 none，也可能是逗号分隔服务器列表。
    str_fallbacks_text: str = pattern_route.search(agents_text).group(2).strip()  # route fallback 原始文本

    # fallback 列表需要保留空列表语义，表示禁止静默切换服务器。
    list_fallbacks: list[str] = _parse_fallbacks(str_fallbacks_text)  # route fallback 服务器列表

    # server 正则读取已登记服务器行，供调用方生成诊断上下文。
    pattern_server: re.Pattern[str] = re.compile(r"Registered server `([^`]+)`: ([^\n]+)")  # 已登记服务器定义正则

    # 注册服务器列表保留 id 和说明，不参与 route 判断本身。
    list_registered_servers: list[dict[str, str]] = []  # AGENTS.md 中的已登记服务器摘要

    # 逐条登记服务器说明，保留原始顺序供报告引用。
    for match_server in pattern_server.finditer(agents_text):

        # 当前条目同时记录 server id 与人类可读说明。
        dict_registered_server = {  # 当前服务器摘要
            "id": match_server.group(1).strip(),  # 已登记服务器标识
            "description": match_server.group(2).strip(),  # 已登记服务器说明
        }

        # 把当前服务器摘要加入契约返回结果。
        list_registered_servers.append(dict_registered_server)

    # 返回字段名是 confidence_remote 调用侧依赖的稳定契约。
    return {
        "route_name": route_name,
        "primary": str_primary,
        "fallbacks": list_fallbacks,
        "registered_servers": list_registered_servers,
    }

# 从 agents-control.json 载荷中解析指定远端路由。
def parse_remote_route_contract_from_profile(
    profile: dict[str, Any],
    *,
    route_name: str = REMOTE_ROUTE_NAME,
) -> dict[str, Any]:
    """解析控制档中的远端 HLS 验收路由。

    参数:
        profile: `.agents/agents-control.json` 解析后的顶层对象。
        route_name: 需要解析的远端验收路由名称。

    返回:
        包含 route_name、primary、fallbacks 和 registered_servers 的契约字典。

    异常:
        ValueError: 当控制档中缺少指定 route 定义时抛出。
    """

    # 远端服务器契约集中保存在控制档的 remote_server_contract 字段。
    dict_remote_contract = profile.get("remote_server_contract", {})  # 远端服务器契约载荷

    # 字段类型不对时按缺失契约处理，避免后续索引报错失焦。
    if not isinstance(dict_remote_contract, dict):

        # 保持错误文本前缀一致，便于调用方统一呈现。
        raise ValueError(f"> ERR: [Python] Could not find route definition for {route_name!r}.")

    # task_routes 是控制档中的任务到服务器映射表。
    list_task_routes = dict_remote_contract.get("task_routes", [])  # 远端任务路由列表

    # 只接受列表形态，其他形态视为没有可用 route。
    if not isinstance(list_task_routes, list):

        # 阻断无效路由表，避免静默选错服务器。
        raise ValueError(f"> ERR: [Python] Could not find route definition for {route_name!r}.")

    # 控制档可能同时保留稳定 key 和展示名，两个字段任一命中即视为目标 route。
    for obj_route in list_task_routes:

        # 非对象条目不能提供路由字段，直接跳过。
        if not isinstance(obj_route, dict):

            # 跳过坏条目后继续寻找真正的 route 对象。
            continue

        # task_key 是 profile 内部稳定标识，用它锁定远端任务路由。
        str_task_key = str(obj_route.get("task_key") or "").strip()  # 当前路由 task_key

        # 早期配置可能缺 task_key，展示名兜底只用于查找同一路由。
        str_task_name = str(obj_route.get("task_name") or "").strip()  # 旧控制档展示字段

        # 当前路由不匹配目标名称时继续查找。
        if route_name not in {str_task_key, str_task_name}:

            # 当前条目不是目标 route，继续扫描剩余映射。
            continue

        # server_6 绑定来自这个字段，不再从 AGENTS 文本猜测。
        str_primary = str(obj_route.get("primary_server_id") or "").strip()  # 远端主机主键

        # 主服务器缺失时契约不可用，直接阻断。
        if not str_primary:

            # 明确指出当前 route 没有 primary。
            raise ValueError(f"> ERR: [Python] Route {route_name!r} has no primary server.")

        # fallback_server_ids 在当前项目通常为空，但仍按列表契约解析。
        list_raw_fallbacks = obj_route.get("fallback_server_ids", [])  # route fallback 原始列表

        # 非列表 fallback 视为空列表，避免无效类型误触发 fallback。
        if not isinstance(list_raw_fallbacks, list):

            # 使用空列表表达当前 route 没有可信 fallback 来源。
            list_raw_fallbacks = []  # 无效 fallback 字段的归一化结果

        # 后续目标校验需要一组已经清洗过的备用主机候选。
        list_fallbacks: list[str] = []  # 已清洗备用服务器标识

        # 逐项归一化 fallback，避免列表推导里重复转换。
        for obj_fallback in list_raw_fallbacks:

            # 当前 fallback 候选先转成字符串 id。
            str_fallback = str(obj_fallback).strip()  # 当前 fallback 服务器标识

            # 空白 fallback 不能进入契约结果。
            if str_fallback:

                # 记录可用 fallback 服务器 id。
                list_fallbacks.append(str_fallback)

        # 注册服务器摘要只用于诊断上下文，不参与目标校验。
        list_registered_servers = _registered_servers_from_profile(dict_remote_contract)  # 控制档服务器摘要

        # 返回与旧 AGENTS 解析一致的字段契约。
        return {
            "route_name": route_name,
            "primary": str_primary,
            "fallbacks": list_fallbacks,
            "registered_servers": list_registered_servers,
        }

    # 所有 route 都检查完仍未命中，说明控制档缺少本任务的远端契约。
    raise ValueError(f"> ERR: [Python] Could not find route definition for {route_name!r}.")

# 加载当前技能根可见的远端路由契约。
def load_remote_route_contract(
    skill_root: Path,
    *,
    route_name: str = REMOTE_ROUTE_NAME,
) -> dict[str, Any]:
    """读取控制档或根级 AGENTS.md 并解析远端路由契约。

    参数:
        skill_root: `skills/<skill-name>` 形式的技能根目录路径。
        route_name: 需要解析的远端验收路由名称。

    返回:
        包含 AGENTS.md 路径和路由契约字段的字典。

    异常:
        ValueError: 当配置文件结构无效或目标 route 缺失时抛出。
    """

    # 根级 AGENTS.md 仍作为旧布局兼容来源。
    path_agents: Path = root_agents_path(skill_root)  # 根级 AGENTS.md 路径

    # 控制档是新版 agents-md-generator 的远端 route source of truth。
    path_profile: Path = agents_control_path(skill_root)  # 远端路由控制档路径

    # 控制档存在时优先读取，避免 AGENTS 压缩渲染后丢失 route 详情。
    if path_profile.is_file():

        # 读取 JSON 控制档；语法错误应直接暴露为治理配置问题。
        dict_profile = json.loads(path_profile.read_text(encoding="utf-8"))  # 控制档顶层载荷

        # 顶层必须是 JSON object 才能承载 remote_server_contract。
        if not isinstance(dict_profile, dict):

            # 非对象控制档无法表达 route 契约。
            raise ValueError(f"> ERR: [Python] agents-control.json must be a JSON object: {path_profile}")

        # 返回时同时保留 AGENTS 路径与 profile 路径，方便报告定位真实来源。
        return {
            "agents_path": str(path_agents),
            "profile_path": str(path_profile),
            **parse_remote_route_contract_from_profile(dict_profile, route_name=route_name),
        }

    # 缺少控制档时回退到 AGENTS 正文解析，兼容旧生成块。
    str_agents_text: str = path_agents.read_text(encoding="utf-8")  # AGENTS.md 文本内容

    # 返回时附带文件路径，方便 confidence report 指向契约来源。
    return {
        "agents_path": str(path_agents),
        **parse_remote_route_contract(str_agents_text, route_name=route_name),
    }

# 从控制档服务器注册表生成诊断摘要。
def _registered_servers_from_profile(remote_contract: dict[str, Any]) -> list[dict[str, str]]:
    """提取控制档中的服务器摘要。

    参数:
        remote_contract: `remote_server_contract` 字段载荷。

    返回:
        包含 id 和 description 的服务器摘要列表。
    """

    # server_registry 是控制档中的已登记服务器列表。
    list_server_registry = remote_contract.get("server_registry", [])  # 已登记服务器原始列表

    # 非列表 registry 不能提供有效服务器摘要。
    if not isinstance(list_server_registry, list):

        # 返回空摘要，保持 route 主校验不受诊断字段影响。
        return []

    # 收集规范化后的服务器摘要。
    list_registered_servers: list[dict[str, str]] = []  # 已登记服务器摘要列表

    # 逐项提取 id、name、category 和 functions。
    for obj_server in list_server_registry:

        # 非对象条目跳过，避免坏数据污染诊断。
        if not isinstance(obj_server, dict):

            # 继续检查后续服务器条目。
            continue

        # 服务器 id 是报告中最重要的稳定标识。
        str_server_id = str(obj_server.get("id") or "").strip()  # 注册服务器唯一标识

        # 缺少 id 的服务器条目没有诊断价值。
        if not str_server_id:

            # 跳过无 id 的服务器条目。
            continue

        # 服务器名称单独保留，便于报告中直接看出远端主机。
        str_server_name = str(obj_server.get("name") or "").strip()  # 服务器名称

        # 服务器类别补充说明该主机在治理中的角色。
        str_server_category = str(obj_server.get("category") or "").strip()  # 服务器类别

        # functions 可能是列表，转换成人类可读的分号分隔文本。
        obj_functions_raw: object = obj_server.get("functions", [])  # 服务器能力原始字段

        # 非列表 functions 不参与摘要，避免把坏类型强行字符串化。
        list_functions = obj_functions_raw if isinstance(obj_functions_raw, list) else []  # 服务器能力列表

        # 汇总服务器能力，供 confidence 报告展示该主机为什么可用。
        str_functions = "; ".join(str(item).strip() for item in list_functions if str(item).strip())  # 服务器能力摘要

        # 当前服务器说明由名称、类别和能力摘要按顺序拼接。
        list_description_parts: list[str] = []  # 服务器说明片段

        # 逐段过滤空值，避免最终 description 出现空分隔符。
        for str_description_part in (str_server_name, str_server_category, str_functions):

            # 非空片段才进入人类可读摘要。
            if str_description_part:

                # 记录当前服务器的一个说明片段。
                list_description_parts.append(str_description_part)

        # 错误报告只展示两列信息：服务器 id 与可读说明。
        dict_registered_server = {  # 服务器诊断记录
            "id": str_server_id,  # route 报告使用的服务器 id
            "description": " / ".join(list_description_parts),  # route 报告展示的服务器说明
        }

        # 把当前服务器摘要加入返回列表。
        list_registered_servers.append(dict_registered_server)

    # 返回控制档中解析出的服务器摘要。
    return list_registered_servers

# 校验用户传入的远端服务器是否满足 route 契约。
def validate_remote_route_target(
    contract: dict[str, Any],
    *,
    server: str | None = None,
    build_server: str | None = None,
    validate_server: str | None = None,
) -> list[str]:
    """检查单服务器或拆分拓扑是否固定到 AGENTS 主服务器。

    参数:
        contract: `load_remote_route_contract()` 返回的路由契约字典。
        server: 单服务器远端验收目标。
        build_server: 拆分拓扑中的构建服务器。
        validate_server: 拆分拓扑中的验证服务器。

    返回:
        违反 route 契约的错误文本列表；空列表表示通过。
    """

    # primary 缺失表示 AGENTS 契约本身不可用。
    str_primary: str = str(contract.get("primary") or "").strip()  # AGENTS route 主服务器

    # 没有主服务器时立刻返回契约错误。
    if not str_primary:

        # 保持历史错误文本，便于 confidence gate 识别。
        return ["route contract primary server is empty"]

    # 收集所有拓扑违规项，拆分拓扑需要同时报告 build 和 validate。
    list_errors: list[str] = []  # route 目标违规说明

    # 单服务器模式必须直接命中 primary。
    if server:

        # 用户指定 server 与 AGENTS primary 不一致时阻塞远端验收。
        if server != str_primary:

            # 保持历史错误文本格式，调用侧会把它写入 gate 结果。
            list_errors.append(f"server must match AGENTS route primary {str_primary}")

        # 单服务器模式不再检查拆分拓扑参数。
        return list_errors

    # 拆分拓扑任一端出现时，两端都必须显式等于 primary。
    if build_server or validate_server:

        # 构建服务器不能绕过 AGENTS 指定的主服务器。
        if build_server != str_primary:

            # 构建阶段必须固定命中 AGENTS 主服务器，不能绕开既定路由。
            list_errors.append(f"build_server must match AGENTS route primary {str_primary}")

        # 验证服务器不能绕过 AGENTS 指定的主服务器。
        if validate_server != str_primary:

            # 验证阶段也必须落在同一主服务器，避免跨机伪造通过证据。
            list_errors.append(f"validate_server must match AGENTS route primary {str_primary}")

        # 拆分拓扑检查完成后返回全部违规项。
        return list_errors

    # 未提供任何远端目标时无法确认 route 是否被遵守。
    list_errors.append("remote route validation requires server or split-topology targets")

    # 返回缺少远端目标的契约错误。
    return list_errors

# 将 fallback 原始文本转换为服务器标识列表。
def _parse_fallbacks(str_fallbacks_text: str) -> list[str]:
    """解析 route 表中的 fallback 字段。

    参数:
        str_fallbacks_text: AGENTS route 行中的 fallback 原始文本。

    返回:
        fallback 服务器标识列表；`none` 返回空列表。
    """

    # AGENTS 使用 none 表示没有可用 fallback。
    if str_fallbacks_text.lower() == "none":

        # 空列表保留“不允许自动切换”的契约含义。
        return []

    # 先准备 fallback 容器，逐项保留清洗后的服务器标识。
    list_fallbacks: list[str] = []  # 解析后的 fallback 服务器标识

    # 逗号分隔列表可能带反引号和空格，需要逐项清洗。
    for str_item in str_fallbacks_text.split(","):

        # 去掉 route 文本里包裹服务器名的空格与反引号。
        str_clean_item: str = str_item.strip(" `")  # 单个 fallback 服务器标识

        # 跳过清洗后为空的异常片段，避免把空字符串写入契约。
        if not str_clean_item:

            # 忽略清洗后没有服务器标识的空片段。
            continue

        # 按原始顺序保留合法 fallback，便于后续报告回放。
        list_fallbacks.append(str_clean_item)

    # 返回清洗后的 fallback 列表。
    return list_fallbacks
