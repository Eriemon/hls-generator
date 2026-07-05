#!/usr/bin/env python3
"""包装 agents-md-generator 的文档治理命令，并补齐本仓库 release 打包修复流程。

本脚本默认把 stdout/stderr 交给被委托的治理脚本；`package-release` 分支会输出单个
JSON 状态对象，供上层自动化读取发布修复和 post gate 结果。
"""

# 未来注解让标准库类型提示在运行时保持轻量。
from __future__ import annotations

# CLI、JSON、哈希、压缩包和子进程模块构成本脚本的全部运行依赖。
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile

# 时间戳只用于刷新 release receipt 的生成时间。
from datetime import datetime

# Path 统一处理仓库、skill、release 和 zip 路径。
from pathlib import Path

# Any 用于缓存运行时导入的 sanitization 函数。
from typing import Any

# 委托 helper 负责定位 agents-md-generator 中的真实治理脚本。
from _skill_tool_delegate import agents_md_generator_script, run_delegate_retrying_transient_fs

# release 打包时不应进入 dist 的顶层治理和缓存目录。
TOP_LEVEL_EXCLUDES = {  # release 顶层排除项集合
    "AGENTS.md",  # 根级治理说明文件
    "_smoke_runs",  # 技能本地烟测运行目录
    "reports",  # 本地报告与中间产物目录
    "workflow-state.json",  # 工作流状态快照文件
    "__pycache__",  # Python 字节码缓存目录
    ".pytest_cache",  # pytest 最近一次运行的本地缓存目录
    ".mypy_cache",  # mypy 静态类型分析结果缓存目录
    ".ruff_cache",  # ruff lint 与格式检查缓存目录
}

# sanitization 函数按需从委托脚本目录导入，避免普通命令承担额外导入风险。
func_sanitize_release_text: Any | None = None  # release 文本脱敏函数缓存

# 运行委托脚本时保留 stdout/stderr，调用方按 returncode 决定后续处理。
def _run(path_script: Path, list_args: list[str]) -> subprocess.CompletedProcess[str]:
    """运行委托脚本并返回完整进程结果。

    参数:
        path_script: 需要执行的委托脚本路径。
        list_args: 传递给委托脚本的参数列表。

    返回:
        当前子进程的完整执行结果。
    """

    # 子命令使用当前 Python 解释器，保持虚拟环境和依赖解析一致。
    return subprocess.run(
        [sys.executable, str(path_script), *list_args],
        check=False,
        capture_output=True,
        text=True,
    )

# git 调用固定在项目根目录执行，避免 dist staging 落到错误仓库。
def _git(path_project: Path, list_args: list[str]) -> subprocess.CompletedProcess[str]:
    """在项目目录执行 git 命令并返回结果。

    参数:
        path_project: 执行 git 命令时使用的项目根目录。
        list_args: 传递给 git 的参数列表。

    返回:
        当前 git 子进程的完整执行结果。
    """

    # 返回 CompletedProcess，调用方保留 stdout/stderr 作为失败证据。
    return subprocess.run(
        ["git", *list_args],
        cwd=path_project,
        check=False,
        capture_output=True,
        text=True,
    )

# 将被委托脚本的输出原样转发给当前进程。
def _print_completed(completed_process_result: subprocess.CompletedProcess[str]) -> None:
    """原样转发委托进程的标准输出与标准错误。

    参数:
        completed_process_result: 需要回放输出的委托进程结果。

    返回:
        无返回值，仅负责把已有输出写回当前进程。
    """

    # stdout 直接写回 stdout，保持被委托脚本的人类或机器协议。
    if completed_process_result.stdout:

        # 直接透传委托脚本 stdout，避免额外插入包装层格式。
        sys.stdout.write(completed_process_result.stdout)

    # stderr 继续写到 stderr，避免错误信息混入 JSON stdout。
    if completed_process_result.stderr:

        # 先补包装层错误摘要，再把委托脚本 stderr 原文附在后面。
        print(
            f"> ERR: [Python] Delegated docs stderr follows.\n"
            f"{completed_process_result.stderr.rstrip()}",
            file=sys.stderr,
        )

# 尝试把委托脚本 stdout 解析为 JSON 对象。
def _load_json(str_text: str) -> dict[str, Any] | None:
    """把委托脚本 stdout 解析成 JSON 对象。

    参数:
        str_text: 委托脚本输出的原始文本。

    返回:
        解析成功时返回 JSON 对象，否则返回 `None`。
    """

    # JSON 解析失败说明被委托脚本输出了人类可读文本。
    try:

        # 解析委托脚本输出，package-release 分支依赖其中的 errors 字段。
        dict_payload = json.loads(str_text)  # 委托脚本 JSON 载荷

    # 非 JSON 输出由调用方原样转发，不在这里制造失败。
    except json.JSONDecodeError:

        # 返回 None 表示当前 stdout 不是机器可读状态对象。
        return None

    # 只接受 JSON object，其他 JSON 类型不符合治理协议。
    return dict_payload if isinstance(dict_payload, dict) else None

# package-release 的参数需要在包装层解析一次，以便修复 dist 目录。
def _parse_package_args(list_argv: list[str]) -> argparse.Namespace:
    """解析 package-release 修复路径需要的包装层参数。

    参数:
        list_argv: 当前 CLI 传入的原始参数列表。

    返回:
        只包含包装层关心字段的参数命名空间。
    """

    # 只解析本包装层关心的参数，其他语义仍交给委托脚本。
    parser = argparse.ArgumentParser(add_help=False)  # 包装层只解析 release 修复所需参数

    # project 缺省为当前工作目录，兼容原 manage_docs CLI。
    parser.add_argument("project", nargs="?", default=".")

    # release 版本用于定位 dist/<skill>-<version>。
    parser.add_argument("--version", required=True)

    # skill-dir 用于从源 skill 目录重建 release manifest。
    parser.add_argument("--skill-dir", required=True)

    # 返回去掉子命令后的参数命名空间。
    return parser.parse_args(list_argv[1:])

# 判断某个路径是否应该进入 installable release 内容。
def _is_release_member(path_candidate: Path, path_prefix: Path, *, str_receipt_name: str | None = None) -> bool:
    """判断候选路径是否属于 installable release 正文。

    参数:
        path_candidate: 当前待判断的文件系统路径。
        path_prefix: 计算 release 相对路径时使用的根目录。
        str_receipt_name: 需要额外排除的 receipt 文件名。

    返回:
        候选路径属于 release 正文时返回 `True`。
    """

    # release manifest 只记录普通文件。
    if not path_candidate.is_file():

        # 目录和特殊文件不进入 release 文件清单。
        return False

    # release 内部路径统一使用 POSIX 分隔符，便于 zip 和 receipt 对齐。
    str_relative = path_candidate.relative_to(path_prefix).as_posix()  # release 内相对路径

    # 路径片段用于识别 .git、缓存目录和顶层排除项。
    list_parts = str_relative.split("/")  # release 相对路径片段

    # 项目治理文件、git 内容和 Python 缓存不能进入 installable release。
    bool_blocked_release_path = (  # release 路径排除命中标记
        str_relative == "AGENTS.md"  # 受管 AGENTS 文件不进入安装包
        or ".git" in list_parts  # git 元数据目录不进入安装包
        or "__pycache__" in list_parts  # Python 缓存目录不进入安装包
        or str_relative.endswith(".pyc")  # Python 字节码文件不进入安装包
    )  # release 打包排除路径命中标记

    # 被治理规则排除的路径不进入 release body。
    if bool_blocked_release_path:

        # 返回 false 表示该文件不属于 release payload。
        return False

    # receipt 自身不参与 manifest 校验，避免刷新 receipt 时形成自引用。
    if str_receipt_name and str_relative == str_receipt_name:

        # 排除 release receipt 本体。
        return False

    # 根级 reports、缓存和 smoke 目录由治理规则排除。
    if list_parts and list_parts[0] in TOP_LEVEL_EXCLUDES:

        # 排除不应打包的顶层目录或文件。
        return False

    # 其余普通文件属于 release body。
    return True

# 从源 skill 目录构建 release 内应存在的文件映射。
def _source_release_map(path_skill_dir: Path) -> dict[str, Path]:
    """构建源 skill 目录到 release 相对路径的文件映射。

    参数:
        path_skill_dir: 作为 release 正文来源的 skill 根目录。

    返回:
        以 release 相对路径为键、源文件路径为值的映射。
    """

    # release 文件映射的键为 POSIX 相对路径，值为源文件路径。
    dict_files: dict[str, Path] = {}  # 源 release 文件映射

    # 逐个顶层条目扫描，先排除治理和缓存边界。
    for path_top in sorted(path_skill_dir.iterdir(), key=lambda path_item: path_item.name.lower()):

        # 顶层排除项不进入 installable skill body。
        if path_top.name in TOP_LEVEL_EXCLUDES:

            # 跳过治理或缓存目录。
            continue

        # 顶层普通文件可以直接作为 release 候选。
        if path_top.is_file():

            # 单文件候选转成列表，便于下方统一过滤。
            list_walk: list[Path] = [path_top]  # 当前顶层文件候选序列

        # 顶层目录需要递归枚举内部成员。
        else:

            # 目录候选先固化为列表，避免延迟迭代影响类型门禁。
            list_walk = list(path_top.rglob("*"))  # 当前顶层目录候选序列

        # 对每个候选路径应用 release 成员规则。
        for path_member in list_walk:

            # 只把合规 release 文件放入映射。
            if _is_release_member(path_member, path_skill_dir):

                # 记录源文件的 release 相对路径。
                str_release_path = path_member.relative_to(path_skill_dir).as_posix()  # 源文件在 release 中的相对路径

                # 映射值保留源路径，后续用于拷贝和脱敏命中扫描。
                dict_files[str_release_path] = path_member  # release 相对路径到源文件路径的映射项

    # 返回源目录应有的 release 文件集合。
    return dict_files

# 列出某个 release 根目录中实际存在的 release 文件。
def _release_file_list(path_root: Path, *, str_receipt_name: str | None = None) -> list[str]:
    """列出 release 根目录下当前实际存在的正文文件。

    参数:
        path_root: 当前 release 根目录。
        str_receipt_name: 需要从清单中排除的 receipt 文件名。

    返回:
        按稳定顺序排列的 release 正文相对路径列表。
    """

    # 文件列表按路径排序，保证 manifest 和 zip 写入顺序稳定。
    list_files: list[str] = []  # release 实际文件清单

    # 递归扫描 release 目录中的候选文件。
    for path_member in sorted(path_root.rglob("*")):

        # 只保留符合 release 成员规则的文件。
        if _is_release_member(path_member, path_root, str_receipt_name=str_receipt_name):

            # 追加 POSIX 相对路径，供 manifest 和差异修复共用。
            list_files.append(path_member.relative_to(path_root).as_posix())

    # 返回排序后的 release 文件清单。
    return list_files

# 计算文件 sha256，用于 release receipt 和 sanitization 记录。
def _sha256_file(path_file: Path) -> str:
    """计算单个文件的 sha256 十六进制摘要。

    参数:
        path_file: 需要计算摘要的文件路径。

    返回:
        当前文件的 sha256 十六进制文本。
    """

    # release body 中的文件规模受 skill 包约束，读取字节后生成摘要更利于静态治理。
    bytes_file_content = path_file.read_bytes()  # 待写入 receipt 的文件原始字节

    # 返回十六进制摘要字符串。
    return hashlib.sha256(bytes_file_content).hexdigest()

# 构建 release receipt 中的 files manifest。
def _build_release_manifest(path_release_dir: Path, *, str_receipt_name: str) -> list[dict[str, str]]:
    """构建 release receipt 需要的文件路径与摘要清单。

    参数:
        path_release_dir: 当前 release 根目录。
        str_receipt_name: 需要从 manifest 中排除的 receipt 文件名。

    返回:
        每个文件的相对路径与 sha256 摘要列表。
    """

    # 每个 release 文件记录路径和 sha256。
    return [
        {"path": str_relative, "sha256": _sha256_file(path_release_dir / str_relative)}
        for str_relative in _release_file_list(path_release_dir, str_receipt_name=str_receipt_name)
    ]

# 按需导入 agents-md-generator 的 release 文本脱敏函数。
def _sanitize_release_text(str_text: str) -> tuple[str, list[dict[str, str]]]:
    """调用委托治理脚本中的 release 文本脱敏 helper。

    参数:
        str_text: 需要执行脱敏检查的原始文本。

    返回:
        脱敏后的文本与命中明细二元组。
    """

    # 全局缓存会在首次调用时绑定真实脱敏 helper，后续直接复用。
    global func_sanitize_release_text

    # 首次调用时才定位委托脚本目录并导入 release sanitization helper。
    if func_sanitize_release_text is None:

        # manage_docs_release 与被委托 manage_docs.py 位于同一脚本目录。
        path_scripts_dir = agents_md_generator_script("manage_docs.py").parent  # 委托治理脚本目录

        # 将委托脚本目录加入 import path，兼容外部安装布局。
        if str(path_scripts_dir) not in sys.path:

            # 插入路径只影响当前 release 修复流程。
            sys.path.insert(0, str(path_scripts_dir))

        # 运行时导入可选 helper；本仓库普通命令不依赖它。
        from manage_docs_release import sanitize_release_text as imported_sanitize  # release helper 由 agents-md-generator 安装目录提供

        # 缓存脱敏函数，避免每个文件重复导入。
        func_sanitize_release_text = imported_sanitize  # release 文本脱敏函数

    # 调用真实脱敏函数，返回脱敏文本和命中明细。
    return func_sanitize_release_text(str_text)

# 删除 release 修复后留下的空目录。
def _prune_empty_dirs(path_root: Path) -> None:
    """删除 release 修复后留下的空目录。

    参数:
        path_root: 需要递归清理空目录的 release 根目录。

    返回:
        无返回值，仅就地删除空目录。
    """

    # 深层目录先删，父目录才能在子目录清空后删除。
    list_empty_dir_candidates = sorted(  # 按深度逆序排列的空目录候选
        (path_candidate for path_candidate in path_root.rglob("*") if path_candidate.is_dir()),  # release 根下的目录候选
        key=lambda path_item: len(path_item.parts),  # 目录深度决定删除顺序
        reverse=True,  # 先删深层目录，避免父目录仍含子目录
    )  # 便于先删除深层目录的候选序列

    # 尝试删除每个空目录。
    for path_dir in list_empty_dir_candidates:

        # rmdir 只会删除空目录，非空或占用目录会抛出 OSError。
        try:

            # 删除当前空目录。
            path_dir.rmdir()

        # 非空目录或瞬时文件占用不影响 release 修复结果。
        except OSError:

            # 保留当前目录，继续处理其他候选。
            continue

# 修复 release 目录与源 skill body 的文件集合差异。
def _repair_release_tree(path_skill_dir: Path, path_release_dir: Path, *, str_receipt_name: str) -> bool:
    """修复 release 目录与源 skill 正文之间的文件集合差异。

    参数:
        path_skill_dir: release 正文来源的 skill 根目录。
        path_release_dir: 当前需要修复的 release 根目录。
        str_receipt_name: 需要从比较范围排除的 receipt 文件名。

    返回:
        release 目录文件集合发生变化时返回 `True`。
    """

    # 源目录映射定义 release 应有文件。
    dict_expected = _source_release_map(path_skill_dir)  # 应存在的 release 文件

    # 实际 release 文件集合来自当前 dist 目录。
    set_actual = set(_release_file_list(path_release_dir, str_receipt_name=str_receipt_name))  # 实际 release 文件

    # changed 标记用于决定后续 commit message。
    bool_changed = False  # release 文件集合是否被修复

    # 补齐源目录中存在但 release 目录缺失的文件。
    for str_relative in sorted(set(dict_expected) - set_actual):

        # 当前缺失文件的源路径。
        path_source = dict_expected[str_relative]  # 缺失 release 文件源路径

        # 当前缺失文件在 release 目录中的目标路径。
        path_target = path_release_dir / str_relative  # 缺失 release 文件目标路径

        # 创建父目录，保证 copy2 可以写入嵌套路径。
        path_target.parent.mkdir(parents=True, exist_ok=True)

        # 复制文件并保留元数据，保持 release body 与源文件一致。
        shutil.copy2(path_source, path_target)

        # 标记 release 目录已经发生变化。
        bool_changed = True  # release 文件集合已修复

    # 删除 release 中多余的文件，反向排序让深层文件先处理。
    for str_relative in sorted(set_actual - set(dict_expected), reverse=True):

        # 当前多余文件的 release 路径。
        path_target = path_release_dir / str_relative  # 多余 release 文件路径

        # 文件仍存在时删除，避免并发或前序修复造成假失败。
        if path_target.exists():

            # 删除不应进入 release body 的文件。
            path_target.unlink()

            # 记录“删除多余文件”这条修复路径已经改变了 release 文件集合。
            bool_changed = True  # release 已移除多余文件并发生集合变更

    # 文件集合变化后清理空目录。
    if bool_changed:

        # 删除修复过程中产生的空目录。
        _prune_empty_dirs(path_release_dir)

    # 返回 release 目录是否被修改。
    return bool_changed

# 按修复后的 release 目录重写 zip 包。
def _rewrite_release_zip(path_release_dir: Path, path_zip: Path) -> None:
    """基于修复后的 release 目录重写 zip 包。

    参数:
        path_release_dir: 已经修复完成的 release 根目录。
        path_zip: 需要重写的 zip 输出路径。

    返回:
        无返回值，仅重写 zip 文件内容。
    """

    # zip 内容使用 deflate 压缩，并按路径排序保证输出稳定。
    with zipfile.ZipFile(path_zip, "w", compression=zipfile.ZIP_DEFLATED) as zip_archive:

        # zip 重写只采集修复后 release 目录中的文件条目。
        for path_member in sorted(path_release_dir.rglob("*")):

            # 只写入文件，目录由 zip 条目路径隐式表达。
            if path_member.is_file():

                # arcname 使用 release 根目录下的 POSIX 相对路径。
                zip_archive.write(path_member, arcname=path_member.relative_to(path_release_dir).as_posix())

# 刷新 release receipt 中的生成时间、文件摘要和脱敏命中记录。
def _refresh_receipt(path_receipt: Path, path_skill_dir: Path, path_release_dir: Path) -> None:
    """刷新 release receipt 的时间戳、文件清单和脱敏命中信息。

    参数:
        path_receipt: 需要刷新的 release receipt 路径。
        path_skill_dir: 作为脱敏命中来源的 skill 根目录。
        path_release_dir: 当前 release 根目录。

    返回:
        无返回值，刷新后的 receipt 会直接写回磁盘。

    异常:
        ValueError: receipt 解析后不是 JSON object 时抛出。
    """

    # 读取现有 release receipt，保留委托脚本生成的其他字段。
    dict_receipt = json.loads(path_receipt.read_text(encoding="utf-8"))  # 现有 receipt JSON 对象

    # receipt 必须是 JSON object，否则无法进行增量刷新。
    if not isinstance(dict_receipt, dict):

        # 抛出带固定前缀的错误，满足当前项目 CLI 错误边界。
        raise ValueError(f"> ERR: [Python] Invalid release receipt JSON: {path_receipt}")

    # receipt 文件名用于 manifest 排除自身。
    str_receipt_name = path_receipt.name  # manifest 刷新时排除的 receipt 文件名

    # 更新时间戳，表明 receipt 与修复后的 release body 对齐。
    dict_receipt["generated_at"] = datetime.now().isoformat(timespec="seconds")  # receipt 刷新时间

    # 用修复后的 release 目录重建 receipt files 字段。
    dict_receipt["files"] = _build_release_manifest(  # receipt 刷新后的文件摘要清单
        path_release_dir,  # 当前 release 根目录
        str_receipt_name=str_receipt_name,  # 构建 manifest 时排除 receipt 自身
    )  # 修复后 release 文件 sha256 清单

    # sanitization 字段存在时同步重建脱敏命中信息。
    dict_sanitization = dict_receipt.get("sanitization")  # 可能包含 files 明细的脱敏记录对象

    # 只有字典形式的 sanitization 记录需要刷新 files 明细。
    if isinstance(dict_sanitization, dict):

        # 重新扫描源 skill body，收集会被脱敏规则命中的文本文件。
        list_rebuilt: list[dict[str, object]] = []  # 重建后的脱敏文件记录

        # 每个源文件与 release 文件一一对应，用源文本识别脱敏命中。
        for str_relative, path_source in _source_release_map(path_skill_dir).items():

            # 当前源文件对应的 release 路径。
            path_release_file = path_release_dir / str_relative  # release 文件路径

            # release 文件不存在时跳过，避免 receipt 记录悬空条目。
            if not path_release_file.is_file():

                # 跳过缺失的 release 文件。
                continue

            # 二进制读取用于识别不可按 UTF-8 文本处理的文件。
            bytes_source = path_source.read_bytes()  # 源文件字节内容

            # 含 NUL 的文件视作二进制资源，不走文本脱敏规则。
            if b"\x00" in bytes_source:

                # 跳过二进制资源文件。
                continue

            # 源文件按 UTF-8 解码，沿用仓库文本编码约定。
            str_source_text = bytes_source.decode("utf-8")  # 源文件文本内容

            # 运行脱敏规则，包装层只需要命中明细。
            tuple_sanitized_text_and_matches = _sanitize_release_text(str_source_text)  # 脱敏文本与命中明细二元组

            # 从二元组取第二项，避免把脱敏文本写入 receipt。
            list_matches: list[dict[str, str]] = tuple_sanitized_text_and_matches[1]  # 当前源文件脱敏命中项

            # 未命中脱敏规则的文件无需进入 sanitization.files。
            if not list_matches:

                # 跳过没有脱敏命中的文本文件。
                continue

            # 记录该文件触发的脱敏规则、占位符和 release 文件摘要。
            list_rebuilt.append(
                dict(
                    path=str_relative,
                    rules=sorted({dict_item["rule"] for dict_item in list_matches}),
                    placeholders=sorted({dict_item["placeholder"] for dict_item in list_matches}),
                    sha256=_sha256_file(path_release_file),
                )
            )

        # 写回 sanitization 文件明细。
        dict_sanitization["files"] = list_rebuilt  # receipt sanitization 文件明细

    # 将刷新后的 receipt 写回 release 目录。
    path_receipt.write_text(json.dumps(dict_receipt, indent=2, ensure_ascii=False), encoding="utf-8")

# 将 dist 目录强制 stage，并在存在差异时提交 release 修复结果。
def _stage_and_commit_dist(
    path_project: Path,
    str_skill_name: str,
    str_version: str,
    *,
    bool_repair_only: bool,
) -> None:
    """强制 stage dist 目录，并在有差异时提交 release 修复结果。

    参数:
        path_project: 当前 git 项目的根目录。
        str_skill_name: 当前 release 对应的 skill 名称。
        str_version: 当前 release 版本号。
        bool_repair_only: 是否使用 repair-only 提交文案。

    返回:
        无返回值，成功时只留下已 stage 或已 commit 的 release 产物。

    异常:
        RuntimeError: git add、git commit 或 staged diff 检查失败时抛出。
    """

    # dist 在 .gitignore 场景下仍需要强制加入索引。
    completed_process_add = _git(  # dist 强制加入索引的进程状态
        path_project,  # git 操作所在的项目根目录
        ["add", "-f", "--all", "--", "dist"],  # 强制加入 dist 目录的 git add 参数
    )

    # git add 失败时，release 产物无法完成 checkpoint。
    if completed_process_add.returncode != 0:

        # 汇总 git 输出作为失败原因。
        str_error_text = (completed_process_add.stderr or completed_process_add.stdout).strip()  # git add 失败文本

        # 抛出固定前缀错误，供上层 JSON failure 捕获。
        raise RuntimeError(
            "> ERR: [Python] package release failed to stage dist artifacts even after forced git add -f: "
            + str_error_text
        )

    # 检查暂存区是否确实存在 dist 差异。
    completed_process_diff = _git(path_project, ["diff", "--cached", "--quiet"])  # git staged diff 检查结果

    # returncode 1 表示暂存区存在差异，需要提交。
    if completed_process_diff.returncode == 1:

        # repair-only 与正常 release 使用不同提交消息。
        str_message = (  # release 提交消息
            f"package-release: repair parity for {str_skill_name} {str_version}"  # parity 修复专用提交文案
            if bool_repair_only  # repair-only 模式沿用 parity 修复专用提交文案
            else f"package-release: {str_skill_name} {str_version}"  # 正常打包完成时的提交文案
        )

        # 提交当前暂存区中的 dist 产物。
        completed_process_commit = _git(  # dist release 提交的进程状态
            path_project,  # git 提交所在的项目根目录
            ["commit", "-m", str_message],  # 复用上方生成的 release 提交文案
        )

        # commit 失败时向上报告 git 原始诊断。
        if completed_process_commit.returncode != 0:

            # 这里优先保留 stderr，缺失时再回退到 stdout，避免丢失 git 原始报错。
            str_error_text = (completed_process_commit.stderr or completed_process_commit.stdout).strip()  # 拼接 package-release 异常时保留的 git 原始诊断文本

            # 把 commit 失败诊断包装成当前脚本要求的固定错误前缀。
            raise RuntimeError("> ERR: [Python] package release failed to commit dist artifacts: " + str_error_text)

    # returncode 0 表示没有 staged diff，无需提交。
    elif completed_process_diff.returncode not in {0, 1}:

        # diff 检查异常时不能继续判断 release 是否完成。
        raise RuntimeError("> ERR: [Python] package release could not inspect staged release artifacts")

# 运行委托脚本的 release post gate。
def _run_post_gate(
    path_script: Path,
    path_project: Path,
    str_version: str,
    str_skill_dir: str,
) -> tuple[int, dict[str, Any]]:
    """执行委托脚本的 release post gate 并解析 JSON 结果。

    参数:
        path_script: 被委托执行的 manage_docs 脚本路径。
        path_project: 当前项目根目录。
        str_version: 当前 release 版本号。
        str_skill_dir: 传递给委托脚本的 skill 目录参数。

    返回:
        委托脚本退出码与解析后 JSON 载荷二元组。
    """

    # post gate 复用委托脚本的 release-gate 子命令。
    completed_process_post = _run(  # 委托 release-gate post 阶段进程状态
        path_script,  # 被委托执行的治理脚本路径
        [
            "release-gate",  # 委托脚本的 release-gate 子命令
            str(path_project),  # 作为 release-gate 工作根目录的项目路径
            "--version",  # 传入目标 release 版本参数名
            str_version,  # 当前 package-release 处理的版本号
            "--skill-dir",  # 传入 skill 目录参数名
            str_skill_dir,  # 继续沿用用户提供的 skill-dir 文本
            "--phase",  # 指定 post gate 阶段参数名
            "post",  # 要求委托脚本只执行 post 阶段校验
        ],
    )

    # 返回 returncode 和解析后的 JSON payload。
    return completed_process_post.returncode, (_load_json(completed_process_post.stdout or "") or {})

# 先执行一次 package-release，并判断是否属于可修复的 stage-only 失败。
def _initial_package_release_state(
    path_script: Path,
    list_argv: list[str],
) -> dict[str, Any]:
    """执行首次 package-release，并提取后续修复所需状态。

    参数:
        path_script: 被委托执行的 manage_docs 脚本路径。
        list_argv: 当前 package-release 子命令的原始参数列表。

    返回:
        包含首次执行进程、JSON 载荷和修复判定的状态字典。
    """

    # 先运行原始 package-release，只有特定 staging 失败才进入修复路径。
    completed_process_first = _run(path_script, list_argv)  # 初次 package-release 结果

    # 尝试读取委托脚本输出的 JSON 状态。
    dict_payload = _load_json(completed_process_first.stdout or "")  # 初次 package-release JSON 载荷

    # errors 字段用于识别是否仅因 dist staging 失败而需要包装层修复。
    list_errors = dict_payload.get("errors", []) if isinstance(dict_payload, dict) else []  # 委托脚本错误清单

    # 仅当委托脚本已经生成 release 但 git stage 失败时，包装层才修复。
    bool_stage_only_failure = (  # 是否为可修复 stage-only 失败
        completed_process_first.returncode != 0  # 首次 package-release 必须先表现为失败
        and "package release failed to stage dist artifacts" in list_errors  # 失败原因必须明确指向 dist staging
    )

    # 返回后续修复流程需要复用的首次执行状态。
    return {
        "completed_process": completed_process_first,
        "payload": dict_payload,
        "stage_only_failure": bool_stage_only_failure,
    }

# 非可修复失败或缺失 receipt 时，保持原始 package-release 结果直接返回。
def _passthrough_package_release_failure(
    completed_process_first: subprocess.CompletedProcess[str],
    *,
    bool_stage_only_failure: bool,
    path_receipt: Path,
) -> int | None:
    """判断是否应直接透传原始 package-release 失败结果。

    参数:
        completed_process_first: 首次 package-release 的完整进程结果。
        bool_stage_only_failure: 当前失败是否属于可修复的 stage-only 场景。
        path_receipt: 预期生成的 receipt 文件路径。

    返回:
        需要直接透传时返回原始退出码，否则返回 `None`。
    """

    # 非 stage-only 失败保持原样转发，不改变委托脚本诊断。
    if completed_process_first.returncode != 0 and not bool_stage_only_failure:

        # 转发原始失败输出。
        _print_completed(completed_process_first)

        # 非可修复失败沿用委托脚本的原始退出语义。
        return int(completed_process_first.returncode)

    # 如果 receipt 不存在，说明 release body 未按预期生成。
    if not path_receipt.is_file():

        # 转发原始输出，保留委托脚本的失败上下文。
        _print_completed(completed_process_first)

        # 缺失 receipt 时继续沿用委托脚本的原始退出码。
        return int(completed_process_first.returncode)

    # 返回 None 表示可以继续执行包装层修复逻辑。
    return None

# package-release 最终 JSON 要输出相对项目根的 release 路径。
def _release_result_paths(
    path_project: Path,
    path_release_dir: Path,
    path_zip: Path,
    path_receipt: Path,
) -> dict[str, str]:
    """生成相对项目根的 release 结果路径映射。

    参数:
        path_project: 当前项目根目录。
        path_release_dir: release 根目录。
        path_zip: release zip 文件路径。
        path_receipt: release receipt 文件路径。

    返回:
        相对项目根的 release 路径文本映射。
    """

    # 统一把 release 相关路径转换为相对项目根的 POSIX 路径。
    return {
        "release_dir": path_release_dir.relative_to(path_project).as_posix(),
        "release_zip": path_zip.relative_to(path_project).as_posix(),
        "receipt_path": path_receipt.relative_to(path_project).as_posix(),
    }

# package-release 的项目路径、skill 路径和 dist 路径统一在这里归一化。
def _package_release_context(
    namespace_parsed: argparse.Namespace,
) -> argparse.Namespace:
    """归一化 package-release 修复流程所需的路径上下文。

    参数:
        namespace_parsed: 包装层解析后的 package-release 参数命名空间。

    返回:
        包含项目、skill、release 和 receipt 路径的上下文命名空间。
    """

    # 项目路径归一化，后续 git 和 dist 路径都以它为根。
    path_project = Path(namespace_parsed.project).resolve()  # 项目根目录

    # skill-dir 既支持相对项目根，也支持绝对路径。
    path_skill_dir = (  # 用于重建 release body 的 skill 源目录
        (path_project / namespace_parsed.skill_dir).resolve()  # 相对 skill-dir 以项目根为基准解析
        if not Path(namespace_parsed.skill_dir).is_absolute()  # 相对 skill-dir 需要挂到项目根下解析
        else Path(namespace_parsed.skill_dir).resolve()  # 绝对 skill-dir 直接规范化
    )

    # skill 名称用于 release 目录和提交消息。
    str_skill_name = path_skill_dir.name  # dist 目录命名中的 skill 名称

    # release 目录由委托脚本的 dist 命名规则决定。
    path_release_dir = (  # 包装层校验和修复文件 parity 的 release 根目录
        path_project / "dist" / f"{str_skill_name}-{namespace_parsed.version}"  # 与委托脚本保持一致的 release 目录命名
    )

    # release zip 与 release 目录同名。
    path_zip = path_project / "dist" / f"{str_skill_name}-{namespace_parsed.version}.zip"  # installable release 压缩包路径

    # receipt 是 installable release 的必要凭据。
    path_receipt = path_release_dir / "RELEASE_RECEIPT.json"  # installable release 凭据文件路径

    # 返回 package-release 修复路径上下文。
    return argparse.Namespace(
        project=path_project,
        skill_dir=path_skill_dir,
        skill_name=str_skill_name,
        release_dir=path_release_dir,
        release_zip=path_zip,
        receipt=path_receipt,
    )

# package-release 的 dist stage/commit 异常要转成统一 JSON failure 载荷。
def _package_release_dist_failure(
    dict_payload: dict[str, Any] | None,
    exc: RuntimeError,
) -> dict[str, Any]:
    """把 dist stage 或 commit 异常转换成统一 JSON failure 载荷。

    参数:
        dict_payload: 首次 package-release 产出的 JSON 载荷。
        exc: 当前捕获到的 dist stage 或 commit 异常。

    返回:
        供上层流程消费的统一失败 JSON 对象。
    """

    # 构造失败 JSON，保留 pre_gate 方便上层判断 release 阶段。
    return dict(
        ok=False,
        errors=[str(exc)],
        pre_gate=dict_payload.get("pre_gate") if isinstance(dict_payload, dict) else None,
    )

# package-release 修复完成后统一组装最终 JSON，避免主流程堆叠状态字段。
def _package_release_result(
    *,
    int_post_returncode: int,
    dict_post_payload: dict[str, Any],
    dict_release_paths: dict[str, str], dict_payload: dict[str, Any] | None,
    bool_stage_only_failure: bool, bool_parity_repaired: bool,
) -> dict[str, Any]:
    """组装 package-release 修复流程的最终 JSON 结果。

    参数:
        int_post_returncode: post gate 的退出码。
        dict_post_payload: post gate 返回的 JSON 载荷。
        dict_release_paths: release 目录、zip 和 receipt 的相对路径映射。
        dict_payload: 首次 package-release 的 JSON 载荷。
        bool_stage_only_failure: 是否经历了 stage-only 失败修复路径。
        bool_parity_repaired: release 文件集合是否发生过修复。

    返回:
        汇总 post gate 结果与修复状态的最终 JSON 对象。
    """

    # 汇总最终 package-release 修复结果。
    return dict(
        ok=int_post_returncode == 0 and not dict_post_payload.get("errors"),
        errors=dict_post_payload.get("errors", []),
        release_dir=dict_release_paths["release_dir"],
        release_zip=dict_release_paths["release_zip"],
        receipt_path=dict_release_paths["receipt_path"],
        pre_gate=dict_payload.get("pre_gate") if isinstance(dict_payload, dict) else None,
        post_gate=dict_post_payload,
        forced_stage=bool_stage_only_failure,
        parity_repaired=bool_parity_repaired,
    )

# package-release 需要在委托失败后修复 dist parity 并重新跑 post gate。
def _package_release_with_repo_fixes(path_script: Path, list_argv: list[str]) -> int:
    """修复 release parity 后重新执行 post gate，并返回最终退出码。

    参数:
        path_script: 被委托执行的 manage_docs 脚本路径。
        list_argv: 当前 package-release 子命令的原始参数列表。

    返回:
        package-release 包装层最终应返回的退出码。
    """

    # 解析本包装层需要的 project、version 和 skill-dir。
    namespace_parsed = _parse_package_args(list_argv)  # 包装层识别的 package-release 命名空间

    # package-release 的路径上下文先统一解析，避免函数内重复拼接。
    namespace_context = _package_release_context(namespace_parsed)  # release 修复流程所需路径上下文

    # 首次 package-release 结果决定后续是透传失败还是进入修复流程。
    dict_initial_state = _initial_package_release_state(path_script, list_argv)  # 初次 package-release 状态

    # 先拆出首次执行进程结果，后续要原样保留委托脚本的退出语义。
    completed_process_first: subprocess.CompletedProcess[str] = dict_initial_state["completed_process"]  # 失败时需要原样透传 stdout/stderr 与退出码的首个子进程结果

    # 保留首次 package-release 的 JSON 载荷，修复成功后也要沿用其基础上下文。
    dict_payload = dict_initial_state["payload"]  # 修复成功后仍要并入最终 stdout JSON 的基础载荷

    # 记录当前失败是否只卡在 dist staging，决定是否进入包装层修复分支。
    bool_stage_only_failure = bool(dict_initial_state["stage_only_failure"])  # 控制是否允许进入 dist parity 自动修复分支的布尔标志

    # 需要透传失败时直接结束，避免继续执行 release 修复逻辑。
    int_passthrough_returncode = _passthrough_package_release_failure(  # 透传失败时的原始退出码
        completed_process_first,  # 首次 package-release 的完整进程结果
        bool_stage_only_failure=bool_stage_only_failure,  # 当前是否属于可修复的 stage-only 失败
        path_receipt=namespace_context.receipt,  # 用于判断 receipt 是否已经正确生成的目标路径
    )

    # 透传路径返回具体退出码，None 表示可以继续修复。
    if int_passthrough_returncode is not None:

        # 这里保持委托脚本的原始退出语义，不进入后续修复流程。
        return int_passthrough_returncode

    # 修复 release 文件集合与源 skill body 的差异，只补齐允许包装层处理的缺口。
    bool_parity_repaired = _repair_release_tree(  # 包装层本轮是否实际补齐了 release tree 差异
        namespace_context.skill_dir,  # 作为 release body 真值来源的 skill 根目录
        namespace_context.release_dir,  # 需要补齐与校正内容的 release 目录
        str_receipt_name=namespace_context.receipt.name,  # 修复时必须保留的 receipt 文件名
    )

    # receipt 必须在文件集合修复后刷新。
    _refresh_receipt(
        namespace_context.receipt,
        namespace_context.skill_dir,
        namespace_context.release_dir,
    )

    # zip 也必须基于修复后的 release 目录重写。
    _rewrite_release_zip(
        namespace_context.release_dir,
        namespace_context.release_zip,
    )

    # dist stage/commit 失败会被转换成 JSON failure。
    try:

        # 根据初次失败类型选择 release 提交消息。
        _stage_and_commit_dist(
            namespace_context.project,
            namespace_context.skill_name,
            namespace_parsed.version,
            bool_repair_only=not bool_stage_only_failure or bool_parity_repaired,
        )

    # git stage/commit 异常需要返回机器可读失败状态。
    except RuntimeError as exc:

        # 把 dist stage 或 commit 异常转换成统一的机器可读失败载荷。
        dict_failure = _package_release_dist_failure(dict_payload, exc)  # dist stage/commit 失败载荷

        # package-release 包装层声明单 JSON stdout 协议。
        sys.stdout.write(json.dumps(dict_failure, indent=2, ensure_ascii=False) + "\n")

        # 返回失败退出码。
        return 1

    # 修复完成后重新运行 post gate，确认包装层补齐后的 release 重新达标。
    tuple_post_gate_result = _run_post_gate(  # post gate 退出码与 JSON 载荷二元组
        path_script,  # 提供 release-gate 子命令入口的委托治理脚本路径
        namespace_context.project,  # post gate 所在项目根目录
        namespace_parsed.version,  # 需要重新校验的 release 版本号
        namespace_parsed.skill_dir,  # 继续传回委托脚本的原始 skill-dir 参数
    )

    # 二元组第一项是 release-gate post 的退出码。
    int_post_returncode = tuple_post_gate_result[0]  # 决定包装层最终退出码的 post gate 状态码

    # 二元组第二项是 release-gate post 的解析后 JSON。
    dict_post_payload = tuple_post_gate_result[1]  # 填充最终 JSON 的 post gate 诊断载荷

    # 最终 JSON 统一输出相对项目根的 release 路径。
    dict_release_paths = _release_result_paths(  # release 结果路径文本
        namespace_context.project,  # 路径相对化时使用的项目根目录
        namespace_context.release_dir,  # 最终输出中的 release 目录路径
        namespace_context.release_zip,  # 最终输出中的 release zip 路径
        namespace_context.receipt,  # 供最终 JSON 标记 receipt 位置的相对路径来源
    )

    # 汇总 post gate、修复结果与路径信息，生成包装层 stdout 需要的最终 JSON。
    dict_result = _package_release_result(  # 含 post gate 诊断、release 路径与修复标志的最终 stdout 结果对象
        int_post_returncode=int_post_returncode,  # 决定包装层最终成功或失败状态的 post gate 退出码
        dict_post_payload=dict_post_payload,  # post gate 重新执行后的诊断载荷
        dict_release_paths=dict_release_paths,  # 统一相对化后的 release 路径集合
        dict_payload=dict_payload,  # 首次 package-release 返回的基础 JSON 载荷
        bool_stage_only_failure=bool_stage_only_failure,  # 本次失败是否源于可修复的 stage-only 场景
        bool_parity_repaired=bool_parity_repaired,  # 让最终 JSON 明确声明本轮是否真的执行了 parity 补齐动作
    )

    # package-release 包装层 stdout 是单 JSON 状态对象。
    sys.stdout.write(json.dumps(dict_result, indent=2, ensure_ascii=False) + "\n")

    # ok 为真时返回 0，否则返回 1。
    return 0 if dict_result["ok"] else 1

# CLI 入口只分流 package-release，其余命令交给委托脚本。
if __name__ == "__main__":

    # 保存原始参数，便于完整转发给委托脚本。
    list_args = sys.argv[1:]  # 需要原样转发给委托脚本的 CLI 参数

    # 定位 agents-md-generator 的 manage_docs.py 委托目标。
    path_delegate_script = agents_md_generator_script("manage_docs.py")  # 委托脚本路径

    # package-release 需要包装层修复 dist parity。
    if list_args[:1] == ["package-release"]:

        # 退出码来自包装层 package-release 修复流程。
        raise SystemExit(_package_release_with_repo_fixes(path_delegate_script, list_args))

    # 其他子命令直接委托，并带瞬时文件系统重试。
    raise SystemExit(run_delegate_retrying_transient_fs(path_delegate_script))
