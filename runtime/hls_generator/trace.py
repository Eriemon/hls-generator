"""为分阶段 HLS 生成工作流读写 JSONL trace。"""

# future 注解延迟解析，避免运行时解析复杂类型。
from __future__ import annotations

# json 用于稳定写入 JSONL trace 记录。
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 公开入口负责追加单条 trace 事件。
def append_trace_event(
    trace_path: Path | None,
    event: dict[str, Any],
    *,
    cwd: Path | None = None,
) -> None:
    """把事件追加为一行 JSONL 记录。

    参数:
        trace_path: trace 文件路径；为 None 时跳过写入。
        event: 待写入的事件字段。
        cwd: 可选工作根目录，用于把路径脱敏为相对路径。

    返回:
        无业务返回值；函数只在启用 trace 时向 JSONL 文件追加事件。
    """

    # 未配置 trace 文件时保持静默，方便调用方复用同一流程。
    if trace_path is None:

        # None 表示调用方关闭 trace 输出。
        return

    # 根目录用于把内部路径转成相对路径，避免 trace 泄漏本机绝对路径。
    path_root: Path = (cwd or Path.cwd()).resolve()  # trace 路径脱敏根目录

    # event 是调用方传入的顶层字典，递归脱敏后仍保持字典结构。
    dict_sanitized_event: dict[str, Any] = _sanitize_mapping(event, path_root)  # 脱敏事件字段

    # timestamp 始终放在记录中，其他字段沿用调用方事件内容。
    dict_record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),  # 记录写盘时刻的 UTC 时间文本
        **dict_sanitized_event,  # 调用方提供的脱敏事件字段
    }  # 最终 JSONL 记录

    # 写入前确保父目录存在，保持原有自动创建行为。
    trace_path.parent.mkdir(parents=True, exist_ok=True)

    # 逐行追加 JSON，sort_keys 让 trace 便于 diff 和审计。
    with trace_path.open("a", encoding="utf-8") as obj_handle:

        # ensure_ascii=False 保留中文字段，末尾换行保持 JSONL 格式。
        obj_handle.write(json.dumps(dict_record, ensure_ascii=False, sort_keys=True) + "\n")

# 公开入口负责读取 JSONL trace 文件。
def read_trace(trace_path: Path) -> list[dict[str, Any]]:
    """读取 JSONL trace 文件并返回事件列表。

    参数:
        trace_path: trace 文件路径。

    返回:
        逐行解析后的事件字典列表；文件不存在时返回空列表。
    """

    # 事件列表保持文件顺序，供后续诊断重放。
    list_events: list[dict[str, Any]] = []  # trace 事件列表

    # trace 尚未生成时保持历史空列表行为。
    if not trace_path.exists():

        # 空列表表示没有任何可读事件。
        return list_events

    # 逐行读取 JSONL，允许文件中存在空行。
    for str_line in trace_path.read_text(encoding="utf-8").splitlines():

        # 空白行没有事件语义，直接跳过。
        if not str_line.strip():

            # 继续读取下一行 trace。
            continue

        # 非空行必须是合法 JSON 对象，解析失败时让异常暴露给调用方。
        list_events.append(json.loads(str_line))

    # 返回按文件顺序解析出的事件。
    return list_events

# 公开入口负责把完整 spec 压缩成 trace 摘要。
def spec_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """提取适合写入 trace 的 HLS spec 摘要。

    参数:
        spec: HLS 规范或实现计划字典。

    返回:
        只包含名称、目标、子函数名和输出路径的摘要字典。
    """

    # 摘要只保留诊断所需字段，避免 trace 携带完整设计内容。
    return {
        "name": spec.get("name"),
        "target": spec.get("target"),
        "subfunctions": [
            obj_item.get("name")
            for obj_item in spec.get("subfunctions", [])
            if isinstance(obj_item, dict)
        ],
        "outputs": [
            obj_item.get("path")
            for obj_item in spec.get("outputs", [])
            if isinstance(obj_item, dict)
        ],
    }

# 公开入口负责把路径显示为相对根目录或外部占位路径。
def safe_path(path: Path | str, root: Path | None = None) -> str:
    """返回适合 trace 输出的安全路径字符串。

    参数:
        path: 待转换的路径。
        root: 可选工作根目录；默认使用当前目录。

    返回:
        root 下路径返回 POSIX 相对路径，root 外路径返回 `<external>/<name>`。
    """

    # base 是路径脱敏根，所有内部路径都相对它输出。
    path_base: Path = (root or Path.cwd()).resolve()  # 脱敏根目录

    # 调用方可能传入字符串或 Path，统一转为 Path 后处理。
    path_candidate: Path = Path(path)  # 待脱敏路径

    # 优先解析真实路径；不存在或解析失败时退回 absolute。
    try:

        # resolved 用于计算相对路径，最大限度消除 `..` 和符号链接影响。
        path_resolved: Path = path_candidate.resolve()  # 解析后的候选路径

    # 路径无法解析时，回退到绝对路径兜底。
    except OSError:

        # 无法 resolve 的路径仍可用 absolute 给 relative_to 判断。
        path_resolved = path_candidate.absolute()  # 兜底绝对路径

    # 能归入 root 的路径输出相对路径，减少本地绝对路径泄漏。
    try:

        # POSIX 分隔符让 trace 在不同平台上更稳定。
        return path_resolved.relative_to(path_base).as_posix()

    # 路径不在当前工作根目录下时，改用外部路径占位符。
    except ValueError:

        # root 外路径只暴露文件名，隐藏外部目录结构。
        return f"<external>/{path_candidate.name}"

# 内部辅助函数负责保证顶层事件仍是字典。
def _sanitize_mapping(mapping: dict[str, Any], root: Path) -> dict[str, Any]:
    """
    递归脱敏事件字典中的 Path 值。

    参数:
        mapping: 待脱敏的事件字典。
        root: 当前 trace 路径脱敏根目录。

    返回:
        保留原键名、仅替换值中路径表现形式的新字典。
    """

    # 字典推导保持键不变，只递归转换值。
    return {str_key: _sanitize_value(obj_item, root) for str_key, obj_item in mapping.items()}

# 内部辅助函数负责递归脱敏任意 JSON 兼容值。
def _sanitize_value(value: Any, root: Path) -> Any:
    """
    递归转换 Path、dict、list 和 tuple 中的路径值。

    参数:
        value: 待脱敏的任意 JSON 兼容值。
        root: 当前 trace 路径脱敏根目录。

    返回:
        与输入结构等价、但路径值已经脱敏的新对象。
    """

    # Path 值转换为安全的 trace 路径字符串。
    if isinstance(value, Path):

        # 使用统一入口处理 root 内外路径。
        return safe_path(value, root)

    # 字典值递归处理，保留原有键名。
    if isinstance(value, dict):

        # 返回新的字典，避免修改调用方事件对象。
        return {str_key: _sanitize_value(obj_item, root) for str_key, obj_item in value.items()}

    # 列表值递归处理，保持顺序不变。
    if isinstance(value, list):

        # JSONL 中 list 可以直接序列化。
        return [_sanitize_value(obj_item, root) for obj_item in value]

    # tuple 值转为 list，延续原实现的 JSON 兼容输出。
    if isinstance(value, tuple):

        # JSON 没有 tuple 类型，使用 list 表达相同顺序。
        return [_sanitize_value(obj_item, root) for obj_item in value]

    # 其他 JSON 兼容值保持原样。
    return value
