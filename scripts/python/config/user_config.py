"""管理 HLS generator 的用户级偏好配置。"""

# 启用 Python 3.10+ 的延迟注解，避免运行时解析类型。
from __future__ import annotations

# 标准库用于时间戳、JSON 持久化、环境变量和用户目录解析。
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

# 指向用户配置文件的环境变量名称，便于测试和本地覆盖。
USER_CONFIG_ENV = "HLS_GENERATOR_USER_CONFIG"  # 用户配置路径覆盖环境变量

# HLS 生成阶段允许的注释语言集合。
COMMENT_LANGUAGES = ("en", "zh")  # 注释语言协议值

# 用户配置路径解析入口。
def user_config_path() -> Path:
    """
    解析当前用户配置文件的位置。

    :param 无业务参数: 当前函数直接从环境变量和用户目录推导配置路径。
    :return: 配置文件绝对路径，dtype=Path，unit=filesystem path。
    """

    # 读取环境变量覆盖值，让测试和临时工作区不污染真实用户目录。
    str_override = os.environ.get(USER_CONFIG_ENV)  # 环境变量中的配置文件路径

    # 显式覆盖优先于默认家目录配置。
    if str_override:

        # 返回调用方指定的配置文件绝对路径。
        return Path(str_override).expanduser().resolve()

    # 使用用户主目录下的稳定默认位置。
    path_default_config = Path.home() / ".hls-generator" / "config.json"  # 默认配置文件路径

    # 返回规范化后的默认配置文件路径。
    return path_default_config.resolve()

# 用户配置读取入口。
def load_user_config() -> dict[str, Any]:
    """
    读取用户配置 JSON 并补齐版本字段。

    :param 无业务参数: 当前函数直接读取用户级配置文件。
    :return: 用户配置字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :raises ValueError: JSON 语法错误、顶层不是对象或版本不受支持时抛出。
    """

    # 解析实际读取的配置文件位置。
    path_config = user_config_path()  # 本次保存要写入的本地偏好文件

    # 未创建配置文件时使用当前 schema 的空配置。
    if not path_config.exists():

        # 返回最小可用配置，保持调用方无需关心文件是否存在。
        return {"version": 1}

    # 将磁盘上的 JSON 配置反序列化为 Python 对象。
    try:

        # 读取用户配置文本并解析 JSON。
        obj_config = json.loads(path_config.read_text(encoding="utf-8"))  # 用户配置原始对象

    # 用户配置 JSON 语法损坏时，转换成统一的用户级错误文本。
    except json.JSONDecodeError as exc:

        # 将 JSON 语法位置附加到面向用户的配置错误中。
        raise ValueError(f"> ERR: [Python] Invalid HLS generator user config JSON in {path_config}: {exc}") from exc

    # 用户配置必须是对象，避免列表或标量破坏字段读取。
    if not isinstance(obj_config, dict):

        # 阻止非对象 JSON 继续进入配置合并流程。
        raise ValueError(f"> ERR: [Python] HLS generator user config must be a JSON object: {path_config}")

    # 当前只接受 version=1，便于未来 schema 迁移时显式阻断。
    if int(obj_config.get("version", 1)) != 1:

        # 报告不受支持的版本号，提示用户清理或迁移配置。
        raise ValueError(
            f"> ERR: [Python] Unsupported HLS generator user config version in {path_config}: "
            f"{obj_config.get('version')!r}"
        )

    # 补齐缺省版本号，保证后续保存仍使用显式 schema。
    obj_config.setdefault("version", 1)

    # 返回已经验证过顶层结构的用户配置。
    return obj_config

# 用户配置写入入口。
def save_user_config(config: dict[str, Any]) -> Path:
    """
    保存用户配置并强制写入当前 schema 版本。

    :param config: 待保存配置，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :return: 写入完成的配置文件路径，dtype=Path，unit=filesystem path。
    """

    # 锁定本次保存真正落盘的用户配置文件位置。
    path_config = user_config_path()  # 用户配置文件路径

    # 确保用户配置目录存在。
    path_config.parent.mkdir(parents=True, exist_ok=True)

    # 复制调用方配置，避免保存过程修改传入字典。
    dict_payload = dict(config)  # 待序列化的配置副本

    # 写入当前 schema 版本，避免旧文件缺少版本字段。
    dict_payload["version"] = 1  # 用户配置 schema 版本

    # 使用稳定排序和 UTF-8 写入，便于人工审阅配置差异。
    str_payload = json.dumps(dict_payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"  # 配置文件文本

    # 将完整 JSON 配置写入目标文件。
    path_config.write_text(str_payload, encoding="utf-8")

    # 返回写入路径，供 CLI 或远端脚本回显摘要。
    return path_config

# 注释语言偏好读取入口。
def get_comment_language(config: dict[str, Any] | None = None) -> str | None:
    """
    从显式配置或用户配置中读取已保存的注释语言。

    :param config: 可选配置字典，shape=(n fields)，dtype=dict[str, Any] or None，unit=JSON object。
    :return: 合法注释语言或 None，dtype=str or None，unit=dimensionless。
    """

    # 提取并规整用户保存的注释语言字段。
    str_language = str((config or load_user_config()).get("comment_language") or "").strip().lower()  # 注释语言候选值

    # 只接受协议声明过的注释语言。
    if str_language in COMMENT_LANGUAGES:

        # 返回调用链可直接传给 prompt/render 阶段的语言值。
        return str_language

    # 非法或缺失配置视为未设置。
    return None

# 注释语言偏好保存入口。
def set_comment_language(language: str) -> Path:
    """
    校验并保存用户选择的注释语言。

    :param language: 用户请求的注释语言，dtype=str，unit=dimensionless。
    :return: 写入完成的配置文件路径，dtype=Path，unit=filesystem path。
    :raises ValueError: 注释语言不在允许集合中时抛出。
    """

    # 校验语言值，避免无效配置落盘。
    str_normalized_language = require_comment_language(language)  # 规范化注释语言

    # 读取已有配置，保留 Vitis 与 board 选择等其他字段。
    dict_config = load_user_config()  # 包含现有服务器缓存的完整用户偏好

    # 保存注释语言偏好。
    dict_config["comment_language"] = str_normalized_language  # 用户选择的注释语言

    # 记录用户最近一次选择时间，便于诊断配置来源。
    dict_config["comment_language_selected_at"] = _utc_now()  # 注释语言选择时间

    # 将更新后的用户配置写回磁盘。
    return save_user_config(dict_config)

# 注释语言运行时解析入口。
def resolve_comment_language(value: str | None) -> str | None:
    """
    将 CLI 或配置中的注释语言请求解析为生成阶段使用的语言。

    :param value: 用户传入的语言值；None 或 auto 表示默认中文，dtype=str or None，unit=dimensionless。
    :return: 已解析语言或 None，dtype=str or None，unit=dimensionless。
    :raises ValueError: 非 auto 且不在允许集合中时抛出。
    """

    # 统一空值和大小写，让 CLI、配置文件和测试使用同一解析规则。
    str_normalized_language = str(value or "auto").strip().lower()  # 规范化语言请求

    # auto 在当前项目中固定解析为中文注释。
    if str_normalized_language == "auto":

        # 返回 HLS 生成和 mock 输出默认使用的中文注释。
        return "zh"

    # 校验显式语言值并返回。
    return require_comment_language(str_normalized_language)

# 注释语言协议校验入口。
def require_comment_language(language: str) -> str:
    """
    要求注释语言属于当前支持集合。

    :param language: 待校验注释语言，dtype=str，unit=dimensionless。
    :return: 规范化后的注释语言，dtype=str，unit=dimensionless。
    :raises ValueError: 注释语言不在 COMMENT_LANGUAGES 中时抛出。
    """

    # 去除空白并统一大小写，保护公开 API 的宽松输入兼容性。
    str_normalized_language = str(language or "").strip().lower()  # 规范化语言值

    # 仅允许 prompt 和 mock 层已实现的注释语言。
    if str_normalized_language not in COMMENT_LANGUAGES:

        # 报告允许集合，帮助 CLI 用户修正输入。
        raise ValueError(f"> ERR: [Python] Comment language must be one of {', '.join(COMMENT_LANGUAGES)}.")

    # 返回可直接传递给下游渲染逻辑的语言值。
    return str_normalized_language

# 注释语言请求载荷构造入口。
def comment_language_request() -> dict[str, Any]:
    """
    构造旧交互流程需要的注释语言确认载荷。

    :param 无业务参数: 当前函数只根据本地默认策略生成请求载荷。
    :return: 注释语言请求载荷，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    """

    # 返回兼容旧 workflow 的请求载荷，同时声明当前默认中文策略。
    return {
        "version": 1,  # 请求载荷 schema 版本
        "action": "record_comment_language_default",  # 兼容旧 workflow 的动作名
        "primary_source": "strict_chinese_comment_policy",  # 默认中文策略来源
        "question": "当前 HLSGenerator 默认使用中文注释；无需再为 auto 模式选择语言。",  # 面向用户的确认说明
        "options": [{"value": "zh", "label": "中文注释"}],  # 旧交互界面可展示的唯一推荐选项
        "user_config_path": str(user_config_path()),  # 当前本地偏好文件的绝对路径文本
        "persistence": "可继续保存 comment_language，但生成、校验和 mock 输出都会执行中文注释规范。",  # 持久化兼容说明
        "recommended_commands": [  # 用户手动保存偏好的兼容命令
            "python -m scripts.python.cli.hls_generator user-config --set-comment-language zh",  # 保存中文注释偏好的命令
        ],
    }

# 服务器维度的 Vitis 选择读取入口。
def get_vitis_selection(server: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """
    读取指定服务器缓存的 Vitis 工具链选择。

    :param server: 服务器标识或名称，dtype=str，unit=dimensionless。
    :param config: 可选配置字典，shape=(n fields)，dtype=dict[str, Any] or None，unit=JSON object。
    :return: Vitis 选择字典或 None，shape=(n fields)，dtype=dict[str, Any] or None，unit=JSON object。
    """

    # 读取 Vitis 选择分区，缺失时按空分区处理。
    dict_selections_candidate = (config or load_user_config()).get("vitis_version_selection", {})  # Vitis 选择分区候选值

    # 非字典分区视为损坏缓存，不向调用方泄露异常结构。
    if not isinstance(dict_selections_candidate, dict):

        # 返回 None 让远端验收流程重新发现工具链。
        return None

    # 取出当前服务器对应的选择记录。
    dict_selected_candidate = dict_selections_candidate.get(server)  # 服务器对应的 Vitis 选择候选值

    # 只返回字典记录，避免旧配置中的标量值进入远端流程。
    if isinstance(dict_selected_candidate, dict):

        # 返回可供远端验收脚本合并的选择字段。
        return dict_selected_candidate

    # 当前服务器没有有效缓存。
    return None

# 服务器维度的 Vitis 选择保存入口。
def set_vitis_selection(server: str, selection: dict[str, Any]) -> Path:
    """
    保存指定服务器的 Vitis 工具链选择。

    :param server: 服务器标识或名称，dtype=str，unit=dimensionless。
    :param selection: Vitis 选择字段，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :return: 写入完成的配置文件路径，dtype=Path，unit=filesystem path。
    :raises ValueError: 服务器标识为空或必要 Vitis 字段缺失时抛出。
    """

    # 空服务器名会导致不同远端缓存互相覆盖，必须阻断。
    if not server:

        # 提醒调用方传入明确的服务器维度。
        raise ValueError("> ERR: [Python] Vitis version selection requires a server id or name.")

    # 装载整份用户偏好，准备只更新当前服务器的 Vitis 记录。
    dict_config = load_user_config()  # 包含现有 board 缓存的完整用户偏好

    # 获取或重建 Vitis 选择分区。
    dict_selections = _config_section(dict_config, "vitis_version_selection")  # Vitis 选择分区

    # 清洗远端发现得到的 Vitis 字段，确保落盘内容都是字符串。
    dict_sanitized = _sanitize_vitis_selection(selection)  # 可持久化的 Vitis 选择记录

    # 必需字段缺失时阻断保存，防止后续远端验收误用半成品 profile。
    if _missing_required_vitis_fields(dict_sanitized):

        # 报告缺少工具版本、settings 脚本或预期工具名。
        raise ValueError("> ERR: [Python] Vitis selection requires version, settings_script, and expected_tool.")

    # 按服务器维度保存 Vitis 工具链选择。
    dict_selections[server] = dict_sanitized  # 服务器对应的 Vitis 选择记录

    # 持久化包含新 Vitis 记录的整份用户配置。
    return save_user_config(dict_config)

# 服务器维度的 board 平台选择读取入口。
def get_board_platform_selection(server: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """
    读取指定服务器缓存的 FPGA board 平台选择。

    :param server: 服务器标识或名称，dtype=str，unit=dimensionless。
    :param config: 可选配置字典，shape=(n fields)，dtype=dict[str, Any] or None，unit=JSON object。
    :return: board 平台选择字典或 None，shape=(n fields)，dtype=dict[str, Any] or None，unit=JSON object。
    """

    # 读取 board 平台选择分区，缺失时按空分区处理。
    dict_selections_candidate = (config or load_user_config()).get("board_platform_selection", {})  # board 平台选择分区候选值

    # 非字典分区视为无效缓存，由调用方重新解析 CLI 或远端 payload。
    if not isinstance(dict_selections_candidate, dict):

        # 返回 None 表示没有可用的服务器平台缓存。
        return None

    # 取出当前服务器对应的 board 平台记录。
    dict_selected_candidate = dict_selections_candidate.get(server)  # 服务器对应的 board 平台候选值

    # 只返回结构化平台记录，避免无效旧值进入路径拼接。
    if isinstance(dict_selected_candidate, dict):

        # 返回远端 board 验收可复用的平台字段。
        return dict_selected_candidate

    # 当前服务器没有有效 board 平台缓存。
    return None

# 服务器维度的 board 平台选择保存入口。
def set_board_platform_selection(server: str, selection: dict[str, Any]) -> Path:
    """
    保存指定服务器的 FPGA board 平台选择。

    :param server: 服务器标识或名称，dtype=str，unit=dimensionless。
    :param selection: board 平台选择字段，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :return: 写入完成的配置文件路径，dtype=Path，unit=filesystem path。
    :raises ValueError: 服务器标识为空或 platform_name 缺失时抛出。
    """

    # 空服务器名会破坏不同远端平台缓存的隔离。
    if not server:

        # 提醒调用方补充服务器维度。
        raise ValueError("> ERR: [Python] Board platform selection requires a server id or name.")

    # 清洗 board 平台记录中的公开字段。
    dict_sanitized = _sanitize_board_platform_selection(selection)  # 可持久化的 board 平台记录

    # platform_name 是本地 payload 和远端 xpfm 路径解析的锚点。
    if not dict_sanitized["platform_name"]:

        # 阻止缺少平台名称的缓存写入。
        raise ValueError("> ERR: [Python] Board platform selection requires platform_name.")

    # 装载整份用户偏好，准备只替换当前服务器的平台缓存。
    dict_config = load_user_config()  # 当前用户配置

    # 获取或重建 board 平台选择分区。
    dict_selections = _config_section(dict_config, "board_platform_selection")  # board 平台选择分区

    # 按服务器维度保存 board 平台选择。
    dict_selections[server] = dict_sanitized  # 服务器对应的 board 平台记录

    # 持久化包含新 board 平台记录的整份用户配置。
    return save_user_config(dict_config)

# 配置分区规范化辅助函数。
def _config_section(config: dict[str, Any], key: str) -> dict[str, Any]:
    """
    获取用户配置中的字典分区，必要时重建损坏分区。

    :param config: 用户配置字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :param key: 配置分区字段名，dtype=str，unit=dimensionless。
    :return: 可写入的配置分区，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    """

    # 读取现有分区，保留合法字典结构。
    dict_section_candidate = config.setdefault(key, {})  # 配置分区候选值

    # 非字典分区说明旧配置损坏，重建为空分区。
    if not isinstance(dict_section_candidate, dict):

        # 用空字典替换损坏分区，保证后续字段写入安全。
        dict_section_candidate = {}  # 重建后的配置分区

        # 将重建后的分区放回用户配置。
        config[key] = dict_section_candidate  # 已规范化的配置分区

    # 返回可变字典，供调用方写入服务器维度记录。
    return dict_section_candidate

# Vitis 选择字段清洗辅助函数。
def _sanitize_vitis_selection(selection: dict[str, Any]) -> dict[str, Any]:
    """
    将 Vitis 选择字段转换为可持久化字符串记录。

    :param selection: 远端发现得到的 Vitis 字段，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :return: 清洗后的 Vitis 选择记录，shape=(12 fields)，dtype=dict[str, Any]，unit=JSON object。
    """

    # 返回固定字段集合，保持配置文件 schema 对远端脚本稳定。
    return {
        "version": _selection_text(selection, "version"),  # Vitis 版本号
        "settings_script": _selection_text(selection, "settings_script"),  # Vitis settings 脚本
        "expected_tool": _selection_text(selection, "expected_tool"),  # 预期可执行工具名
        "target_part": _selection_text(selection, "target_part"),  # 目标 FPGA part
        "expected_tool_path": _selection_text(selection, "expected_tool_path"),  # 预期工具绝对路径
        "env_setup_script": _selection_text(selection, "env_setup_script"),  # 额外环境初始化脚本
        "tool_path": _selection_text(selection, "tool_path"),  # 已发现的 HLS 工具路径
        "vpp_path": _selection_text(selection, "vpp_path"),  # v++ 工具路径
        "xrt_tool_path": _selection_text(selection, "xrt_tool_path"),  # XRT 管理工具路径
        "xrt_setup_script": _selection_text(selection, "xrt_setup_script"),  # XRT 环境脚本
        "xbmgmt_tool_path": _selection_text(selection, "xbmgmt_tool_path"),  # 板卡管理工具 xbmgmt 的绝对路径
        "selected_at": _utc_now(),  # 本地保存 Vitis 选择的时间戳
    }

# Vitis 必填字段校验辅助函数。
def _missing_required_vitis_fields(selection: dict[str, Any]) -> bool:
    """
    判断 Vitis 选择记录是否缺少远端验收必需字段。

    :param selection: 已清洗的 Vitis 选择记录，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :return: 缺少必填字段时为 True，dtype=bool，unit=dimensionless。
    """

    # 逐项检查远端工具链恢复所需的最小字段。
    bool_missing_required = (
        not selection["version"]  # 缺少显式 Vitis 版本号
        or not selection["settings_script"]  # 缺少环境初始化脚本
        or not selection["expected_tool"]  # 缺少预期工具名
    )  # Vitis 必填字段缺失标志

    # 返回校验结果供保存入口决定是否阻断。
    return bool_missing_required

# board 平台字段清洗辅助函数。
def _sanitize_board_platform_selection(selection: dict[str, Any]) -> dict[str, Any]:
    """
    将 board 平台选择字段转换为可持久化字符串记录。

    :param selection: 远端平台选择字段，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :return: 清洗后的 board 平台记录，shape=(5 fields)，dtype=dict[str, Any]，unit=JSON object。
    """

    # 返回固定字段集合，保持本地缓存和远端验收脚本的字段契约不变。
    return {
        "platform_name": _selection_text(selection, "platform_name"),  # Alveo 平台名称
        "remote_platform_root": _selection_text(selection, "remote_platform_root"),  # 远端平台目录
        "remote_xpfm": _selection_text(selection, "remote_xpfm"),  # 远端 xpfm 文件路径
        "source": _selection_text(selection, "source"),  # 平台记录来源
        "selected_at": _utc_now(),  # 本地保存 board 平台选择的时间戳
    }

# 字段文本清洗辅助函数。
def _selection_text(selection: dict[str, Any], key: str) -> str:
    """
    从选择记录中读取字段并转换为去空白字符串。

    :param selection: 原始选择记录，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    :param key: 字段名，dtype=str，unit=dimensionless。
    :return: 字段字符串，dtype=str，unit=dimensionless。
    """

    # 统一将缺失值和 None 转换为空字符串。
    str_field = str(selection.get(key) or "").strip()  # 选择记录中的字段文本

    # 返回可安全写入 JSON 的字符串值。
    return str_field

# UTC 时间戳辅助函数。
def _utc_now() -> str:
    """
    生成用户配置记录使用的 UTC 时间戳。

    :param 无业务参数: 当前函数只依赖系统时钟生成时间文本。
    :return: ISO-8601 UTC 时间戳，dtype=str，unit=UTC timestamp。
    """

    # 生成无微秒的 UTC 时间，兼容 Python 3.10 的 timezone 常量写法。
    datetime_current_utc: dt.datetime = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)  # 当前 UTC 时间

    # 返回以 Z 结尾的标准时间戳文本。
    return datetime_current_utc.isoformat().replace("+00:00", "Z")
