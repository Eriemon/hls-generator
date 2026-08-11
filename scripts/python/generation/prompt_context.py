"""prompt staged 上下文与 memory 过滤 helper。"""

# 使用未来注解避免前向类型在运行时过早求值。
from __future__ import annotations

# Path 与纯路径类型共同承担 staged 上下文文件的安全解析。
from pathlib import Path, PurePosixPath, PureWindowsPath

# Any 覆盖 staged 上下文 JSON-like 载荷的混合值类型。
from typing import Any

# 根 prompt 模块提供共享常量与稳定外观合同。
from scripts.python.generation.prompt import (
    CONTEXT_CHAR_LIMITS,
    FULL_CONTEXT_TOKENS,
    MEMORY_ENTRY_LIMIT,
)

# 构造 staged prompt 的上游产物上下文字典。
def _artifact_context(
    manifest: dict[str, Any] | None,
    context_dir: Path | None,
    *,
    budget: str,
) -> dict[str, Any]:
    """
    构造 staged prompt 的上游产物上下文字典。

    :param manifest: 上游阶段生成的 manifest。
    :param context_dir: 上游阶段工件根目录。
    :param budget: 当前 prompt 预算档位。
    :return: staged prompt 可消费的上游产物上下文字典。
    """

    # 没有 manifest 时直接回退空上下文。
    if not manifest:

        # 保持 staged prompt 在无上游时的最小上下文。
        return {}

    # 只保留模型真正会消费的稳定字段，避免把无关状态扩散进 prompt。
    dict_context = {  # staged prompt 消费的上游阶段摘要字典
        "stage": manifest.get("stage"),  # 上游阶段标记
        "target": manifest.get("target"),  # 目标生成域
        "top": manifest.get("top"),  # 让模型识别上游产物绑定到哪个 top function
        "files": [  # 输出文件摘要入口列表
            {
                "path": dict_file_entry.get("path"),  # 文件相对路径
                "kind": dict_file_entry.get("kind"),  # 文件角色类别
                "language": dict_file_entry.get("language"),  # 让模型按正确语法读取该文件
            }
            for dict_file_entry in manifest.get("files", [])  # 维持 manifest 文件顺序与摘要顺序一致
            if isinstance(dict_file_entry, dict)  # 只保留结构正确的文件字典
        ],
        "checks": manifest.get("checks", {}),  # 上游阶段已经记录的检查结果
    }

    # 只有显式提供目录时才读取工件摘要，避免默认触发磁盘访问。
    if context_dir:

        # 注入上游工件的摘要内容。
        dict_context["artifacts"] = _artifact_summaries(manifest, context_dir, budget=budget)  # 上游工件摘要列表

    # 返回 staged prompt 的上游产物上下文字典。
    return dict_context

# 提取 staged prompt 需要的上游产物摘要。
def _artifact_summaries(
    manifest: dict[str, Any],
    context_dir: Path,
    *,
    budget: str,
) -> list[dict[str, Any]]:
    """
    提取 staged prompt 需要的上游产物摘要。

    :param manifest: 上游阶段生成的 manifest。
    :param context_dir: 上游阶段工件根目录。
    :param budget: 当前 prompt 预算档位。
    :return: staged prompt 需要的工件摘要列表。
    """

    # 输出列表按 manifest 文件顺序累积摘要对象。
    list_artifact_summaries: list[dict[str, Any]] = []  # staged prompt 的工件摘要列表

    # 所有相对路径都必须锚定到调用方提供的 context_dir。
    path_root = context_dir.resolve()  # 上下文目录的规范绝对路径

    # 按 manifest 文件顺序提取工件摘要。
    for dict_file_entry in manifest.get("files", []):

        # 只处理带 path 的字典型 manifest 文件条目。
        if not isinstance(dict_file_entry, dict) or not dict_file_entry.get("path"):

            # 非法文件条目直接跳过，保持 staged prompt 尽量可继续。
            continue

        # 读取 manifest 中声明的相对路径文本。
        str_relative_path = str(dict_file_entry["path"])  # manifest 声明的相对路径

        # 先把 manifest 相对路径安全锚定到 context_dir 下。
        path_artifact = _safe_context_path(path_root, str_relative_path)  # 安全解析后的工件路径

        # 每个工件至少记录路径与存在性，便于模型判断是否可引用上游内容。
        dict_summary: dict[str, Any] = {"path": str_relative_path, "exists": path_artifact.exists()}  # 当前工件摘要骨架

        # 仅在工件真实存在且为普通文件时才读取正文。
        if path_artifact.exists() and path_artifact.is_file():

            # 文本读取统一忽略非法字节，避免单个文件编码问题打断 prompt 渲染。
            str_text = path_artifact.read_text(encoding="utf-8", errors="ignore")  # 工件原始文本内容

            # 根据预算与文件类型决定保留全文还是分块。
            dict_summary.update(
                _context_payload_for(
                    str_relative_path,
                    str_text,
                    budget=budget,
                )
            )

        # 追加当前工件摘要到输出列表。
        list_artifact_summaries.append(dict_summary)

    # 返回 staged prompt 的工件摘要列表。
    return list_artifact_summaries

# 根据预算和文件类型决定 staged prompt 中的工件内容载荷。
def _context_payload_for(
    rel_path: str,
    text: str,
    *,
    budget: str,
) -> dict[str, Any]:
    """
    根据预算和文件类型决定 staged prompt 中的工件内容载荷。

    :param rel_path: 工件相对路径。
    :param text: 工件原始文本内容。
    :param budget: 当前 prompt 预算档位。
    :return: staged prompt 应注入的工件内容载荷。
    """

    # budget 映射到固定字符上限，未识别档位仍回退 normal 大小。
    int_limit = CONTEXT_CHAR_LIMITS.get(budget, CONTEXT_CHAR_LIMITS["normal"])  # 当前预算对应的字符上限

    # 合同类 JSON 或本就很短的文件可以直接保留全文。
    if _needs_full_context(rel_path) or len(text) <= int_limit:

        # 内容长度不超预算时直接保留全文。
        if len(text) <= int_limit:

            # 返回完整正文载荷。
            return {"content": text}

        # 对优先保留全文但过长的内容，改用切块承载，避免单字段过大。
        return {
            "content_chunks": _chunk_text(text, int_limit),
            "content_truncated": False,
        }

    # 其它较长内容统一按预算切块。
    return {
        "content_chunks": _chunk_text(text, int_limit),
        "content_truncated": False,
    }

# 判断指定工件是否应优先保留完整上下文。
def _needs_full_context(rel_path: str) -> bool:
    """
    判断指定工件是否应优先保留完整上下文。

    :param rel_path: 工件相对路径。
    :return: 是否命中完整上下文优先策略。
    """

    # 只对 JSON 契约/向量类文件启用完整上下文优先策略。
    str_lowered_path = rel_path.lower()  # 用于关键词判断的归一化路径

    # 返回是否命中完整上下文优先策略。
    return str_lowered_path.endswith(".json") and any(
        str_token in str_lowered_path for str_token in FULL_CONTEXT_TOKENS
    )

# 把长文本切成带索引的分块。
def _chunk_text(text: str, chunk_size: int) -> list[dict[str, Any]]:
    """
    把长文本切成带索引的分块。

    :param text: 需要切块的原始文本。
    :param chunk_size: 每个分块的最大字符数。
    :return: 带 1-based 索引的文本分块列表。
    """

    # 按顺序累计文本分块。
    list_chunks: list[dict[str, Any]] = []  # 顺序分块后的文本片段列表

    # 逐段切出固定大小的文本窗口。
    for int_index, int_start in enumerate(
        range(0, len(text), chunk_size),
        start=1,
    ):

        # 追加当前分块的索引和正文。
        list_chunks.append(
            {
                "index": int_index,
                "text": text[int_start : int_start + chunk_size],
            }
        )

    # 返回带索引的分块列表。
    return list_chunks

# 筛选与当前 stage 相关的历史 memory 约束。
def _memory_constraints(
    memory: dict[str, Any] | None,
    stage: str,
    *,
    budget: str,
) -> list[dict[str, Any]]:
    """
    筛选与当前 stage 相关的历史 memory 约束。

    :param memory: 历史 memory 约束对象。
    :param stage: 当前正在渲染的 stage。
    :param budget: 当前 prompt 预算档位；仅保留兼容入参，不改变筛选语义。
    :return: 当前 stage 相关的 memory 条目列表。
    """

    # budget 目前不参与 memory 筛选逻辑，这里只保留接口兼容。
    del budget

    # 没有 memory 时直接返回空列表。
    if not memory:

        # staged prompt 在无历史记忆时保持最小输入。
        return []

    # 只保留当前 stage 或全局相关的条目，减少模型看到的历史噪音。
    list_entries: list[dict[str, Any]] = []  # 当前 stage 可见的 memory 条目

    # 遍历 memory.entries 过滤相关条目。
    for dict_entry in memory.get("entries", []):

        # 仅接受字典型条目，其余噪音值直接跳过。
        if not isinstance(dict_entry, dict):

            # 跳过非法 memory 条目。
            continue

        # stage 字段统一小写比较，兼容旧数据中的大小写差异。
        str_entry_stage = str(dict_entry.get("stage", "")).lower()  # 当前 memory 条目的目标 stage

        # 与当前 stage 无关的 memory 条目不进入 prompt。
        if str_entry_stage and str_entry_stage not in {
            stage,
            "*",
            "unknown",
            "validate",
            "execute",
            "implement",
            "cosim",
        }:

            # 跳过与当前 stage 无关的 memory 条目。
            continue

        # 仅保留当前 prompt 真正会消费的稳定字段。
        list_entries.append(
            {
                "stage": dict_entry.get("stage"),
                "error_signature": dict_entry.get("error_signature"),
                "constraint": dict_entry.get("constraint"),
            }
        )

    # 返回限制条数后的相关 memory 条目。
    return list_entries[:MEMORY_ENTRY_LIMIT]

# 把 manifest 中的相对路径安全地约束到 context_dir 下。
def _safe_context_path(root: Path, relative_path: str) -> Path:
    """
    把 manifest 中的相对路径安全地约束到 context_dir 下。

    :param root: 上游 context_dir 的绝对根路径。
    :param relative_path: manifest 中声明的相对路径。
    :return: 约束到 root 下的安全路径；非法路径时返回无效占位路径。
    """

    # Windows 反斜杠输入会破坏 manifest 约定的 posix 相对路径语义，因此直接判定为无效。
    if "\\" in relative_path:

        # 反斜杠输入统一映射到专用占位路径，便于上层定位来源。
        return root / "__invalid_backslash_path__"

    # 同时用 Posix/Windows 视角检查绝对路径和驱动器前缀，避免越界。
    path_posix = PurePosixPath(relative_path)  # 用于检查相对路径片段与目录穿越风险

    # 再以 Windows 规则解析同一路径，补充盘符与绝对路径检测。
    path_windows = PureWindowsPath(relative_path)  # 用于补充盘符与 Windows 绝对路径检查

    # 含绝对路径、盘符或目录穿越片段时返回无效占位路径。
    if (
        path_posix.is_absolute()
        or path_windows.is_absolute()
        or path_windows.drive
        or any(str_part in ("", ".", "..") for str_part in path_posix.parts)
    ):

        # 返回不安全路径对应的无效占位路径。
        return root / "__invalid_unsafe_path__"

    # 合法相对路径必须在 resolve 之后仍然落在 context_dir 内。
    path_candidate = (root / Path(*path_posix.parts)).resolve()  # 解析后的候选绝对路径

    # 再次确认 resolve 后的路径没有逃出 root。
    try:

        # 仅用于触发 root 边界检查副作用。
        path_candidate.relative_to(root)

    # 超出 root 时回退为无效占位路径。
    except ValueError:

        # 返回越界路径对应的无效占位路径。
        return root / "__invalid_outside_path__"

    # 返回安全锚定后的上下文工件路径。
    return path_candidate
