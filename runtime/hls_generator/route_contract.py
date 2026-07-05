"""解析 AGENTS.md 中的远端 HLS 验收路由契约。"""

# future 注解推迟解析，保持运行时导入成本稳定。
from __future__ import annotations

# re 用于从 AGENTS.md 文本中抽取 route 与 server 表。
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

# 从技能根目录加载并解析远端路由契约。
def load_remote_route_contract(
    skill_root: Path,
    *,
    route_name: str = REMOTE_ROUTE_NAME,
) -> dict[str, Any]:
    """读取根级 AGENTS.md 并解析远端路由契约。

    参数:
        skill_root: `skills/<skill-name>` 形式的技能根目录路径。
        route_name: 需要解析的远端验收路由名称。

    返回:
        包含 AGENTS.md 路径和路由契约字段的字典。
    """

    # 根级 AGENTS.md 是远端路由表的唯一可信来源。
    path_agents: Path = root_agents_path(skill_root)  # 根级 AGENTS.md 路径

    # AGENTS 正文交给纯解析函数处理，便于单元测试注入文本。
    str_agents_text: str = path_agents.read_text(encoding="utf-8")  # AGENTS.md 文本内容

    # 返回时附带文件路径，方便 confidence report 指向契约来源。
    return {
        "agents_path": str(path_agents),
        **parse_remote_route_contract(str_agents_text, route_name=route_name),
    }

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
