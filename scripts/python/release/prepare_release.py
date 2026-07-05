#!/usr/bin/env python3
"""准备 erie-hls-generator 的版本化发布目录和 zip 包。

stdout_protocol: json
本模块的 CLI stdout 是 machine-readable stdout protocol；调用方依赖完整 JSON 对象读取发布摘要。
"""

# 发布脚本需要先启用注解延迟解析，避免类型提示影响运行时导入顺序。
from __future__ import annotations

# 标准库导入按用途分组，便于快速定位参数解析、时间、哈希与子进程能力。
import argparse
import datetime as dt
import hashlib
import importlib
import json

# 运行环境、文本规则和文件系统操作放在同一组，便于核对发布副作用。
import os
import re
import shutil
import subprocess
import sys
import zipfile

# 集合与路径类型只用于可读性和静态表达，不参与发布行为本身。
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

# 主技能根目录固定为 skill body，所有发布输入都从这里向下收敛。
SKILL_ROOT = Path(__file__).resolve().parents[3]  # 主技能根目录

# 仓库根目录负责 dist、reports 与 git 元数据查询边界。
REPO_ROOT = SKILL_ROOT.parents[1]  # 当前仓库根目录

# 包名同时用于发布目录、zip 文件名和收据中的 package 字段。
PACKAGE_NAME = "erie-hls-generator"  # 发布包名

# 发布版本必须是严格 SemVer，不能混入 latest 或宽松版本占位。
SEMVER_RE = re.compile(  # `--version` 参数输入使用的严格 SemVer 正则
    r"^(0|[1-9]\d*)\."  # SemVer 主版本数字段
    r"(0|[1-9]\d*)\."  # SemVer 次版本数字段
    r"(0|[1-9]\d*)"  # SemVer 修订版本数字段
    r"(?:-[0-9A-Za-z.-]+)?"  # 可选预发布标签
    r"(?:\+[0-9A-Za-z.-]+)?$"  # 可选构建元数据
)

# 这些目录只服务开发、缓存或验证流程，不允许进入 installable release。
EXCLUDED_DIR_NAMES = {
    ".git",  # git 仓库元数据目录
    ".mypy_cache",  # mypy 类型检查缓存
    ".pytest_cache",  # pytest 运行缓存
    ".ruff_cache",  # ruff 静态检查缓存
    ".Xil",  # Xilinx 本地工程缓存
    "__pycache__",  # Python 字节码缓存目录
    "_smoke_runs",  # smoke 派生运行目录
    "dist",  # 历史发布产物目录
    "ref",  # 本地参考资料目录
    "reports",  # 过程报告与门禁快照目录
    "temp",  # 临时文件目录
    "tmp",  # 临时缓存目录
    "xsim.dir",  # 仿真工具输出目录
}  # 发布时排除的目录名

# Python 字节码文件属于运行时缓存，不属于技能交付内容。
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo"}  # 发布时排除的文件后缀

# 这些文件名要么是派生清单，要么是设计草稿，不应进入最终包。
EXCLUDED_FILE_NAMES = {
    "checksums.sha256",  # 派生校验清单
    "DESIGN_GOALS.md",  # 设计草稿文档
}  # 发布时排除的文件名

# 这些通配模式覆盖 Vivado/Vitis 日志、中间脚本与 solution 工件。
EXCLUDED_GLOBS = (
    "*.jou",  # Vivado/Vitis 命令日志文件
    "*.log",  # 构建与运行过程日志
    "*.str",  # 工具状态追踪文件
    ".hls_generator_*.tcl",  # 生成器临时 Tcl 脚本
    ".hls_generator_vitis_*",  # 生成器临时 Vitis 目录
    "solution*",  # HLS solution 产物目录
)  # 发布时排除的通配模式

# 发布收据里需要保留建议验证命令，提醒安装后如何做最低限度回归检查。
VALIDATION_COMMANDS = [
    r"python .\tests\smoke\run_smoke.py",  # smoke 回归命令
    r"python -m compileall .\skills\erie-hls-generator\runtime\hls_generator",  # runtime 语法编译检查
    (
        r"python %CODEX_HOME%\skills\.system\skill-creator\scripts\quick_validate.py "
        r".\skills\erie-hls-generator"
    ),  # 安装前 quick_validate 命令
    (
        r"python .\skills\erie-hls-generator\scripts\python\validation\confidence_loop.py "
        r"--server <remote-hls-validation-primary> --vitis-version <configured-vitis-version> "
        r"--readiness cosim --remote-parallelism 3 --json-out "
        r".\reports\confidence-loop\latest-remote.json"
    ),  # 远端 confidence loop 命令
]  # 发布后建议保留的验证命令

# `ReleaseError` 统一承载发布治理失败，供 CLI 和仓库脚本链路复用。
class ReleaseError(RuntimeError):
    """表示发布准备过程中的可恢复治理错误。"""

# `main` 负责参数解析、错误协议和 JSON stdout 交付。
def main(argv: list[str] | None = None) -> int:
    """解析命令行参数并输出发布摘要 JSON。

    Args:
        argv: 可选参数列表；为 None 时从当前进程命令行读取。

    Returns:
        0 表示发布准备成功；1 表示发生可预期的治理失败。
    """

    # 命令行只暴露版本号，dist-root 仍保留给仓库内脚本链路使用。
    parser = argparse.ArgumentParser(description="Prepare a versioned erie-hls-generator release directory and zip.")  # 发布脚本参数解析器

    # 版本号必须由调用方显式给出，避免从环境或当前分支隐式推断。
    parser.add_argument(
        "--version",
        required=True,
        help="Explicit SemVer release version, for example 0.1.4.",
    )

    # dist 根目录默认为仓库内 dist，但不对普通用户公开显示。
    parser.add_argument(
        "--dist-root",
        type=Path,
        default=REPO_ROOT / "dist",
        help=argparse.SUPPRESS,
    )

    # argparse 输出的 Namespace 只在当前函数内消费。
    namespace_obj_args: argparse.Namespace = parser.parse_args(argv)  # 命令行参数结果

    # 发布准备的业务异常统一收敛成 ReleaseError，便于对外稳定输出。
    try:

        # 生成完整发布摘要；stdout 只承载这一份 JSON 协议内容。
        dict_payload = prepare_release(namespace_obj_args.version, namespace_obj_args.dist_root)  # 发布摘要

    # ReleaseError 已经封装好面向终端的错误文案，这里只负责稳定落到 stderr。
    except ReleaseError as exc:

        # 先输出稳定摘要，再把 ReleaseError 的正文原样附在下一行。
        print(
            f"> ERR: [Python] Release prepare failed.\n"
            f"{str(exc).rstrip()}",
            file=sys.stderr,
        )

        # 治理失败使用非零退出码返回给调用方。
        return 1

    # stdout 只写 machine-readable JSON，不混入额外人类可读前缀。
    sys.stdout.write(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n")

    # 成功时返回 0，便于外层流水线继续后续阶段。
    return 0

# `prepare_release` 是发布目录、收据和 zip 生成的主流程。
def prepare_release(version: str, dist_root: Path) -> dict[str, object]:
    """准备发布目录、校验依赖清单、写出收据并生成 zip 包。

    Args:
        version: 调用方显式指定的 SemVer 发布版本号。
        dist_root: 发布目录根路径，可以是仓库内相对路径或绝对路径。

    Returns:
        包含发布目录、zip 路径、文件统计与依赖统计的摘要字典。

    Raises:
        ReleaseError: 版本不一致、路径越界、依赖清单非法或验证失败时抛出。
        OSError: 复制、删除、写文件或创建目录时的底层文件系统异常向上传递。
    """

    # 用户输入版本先做去空白归一化，避免尾随空格污染目录名与标签。
    str_version = version.strip()  # 归一化后的版本号

    # 非 SemVer 输入会直接破坏 release contract，因此要在最前面阻断。
    if not SEMVER_RE.fullmatch(str_version):

        # 发布版本格式非法时立即停止，避免生成错误目录与标签。
        raise ReleaseError(
            f"> ERR: [Python] Release version must be explicit SemVer X.Y.Z, got {str_version!r}."
        )

    # runtime 版本是技能源码侧对外声明的权威版本。
    str_source_version = _read_runtime_version()  # runtime 源码版本号

    # CLI 版本校验可以防止命令行入口与 runtime 元数据脱节。
    str_cli_version = _read_cli_version()  # CLI 暴露版本号

    # runtime 版本与发布目标不一致时，安装后 API 元数据会漂移。
    if str_source_version != str_version:

        # 源码版本不匹配时拒绝继续，避免打出自相矛盾的 release。
        raise ReleaseError(
            "> ERR: [Python] runtime/hls_generator/__init__.py version "
            f"{str_source_version!r} does not match release version {str_version!r}."
        )

    # CLI 版本与发布目标不一致时，用户入口会表现出不同版本。
    if str_cli_version != str_version:

        # CLI 版本漂移时直接阻断，避免安装后 `--version` 与元数据不一致。
        raise ReleaseError(
            f"> ERR: [Python] hls-gen --version reported {str_cli_version!r}, expected {str_version!r}."
        )

    # 发布前必须先确认技能依赖清单没有破坏 blocking 策略。
    dict_dependency_summary = _validate_skill_dependency_manifest()  # 依赖清单校验摘要

    # dist 根目录必须保持在仓库内，避免删除或写出到未知位置。
    path_dist_root = _resolve_dist_root(dist_root)  # 解析后的 dist 根目录

    # 版本化目录名固定包含包名与版本号，便于保留历史产物。
    path_release_dir = path_dist_root / f"{PACKAGE_NAME}-v{str_version}"  # 发布目录路径

    # zip 文件名与目录保持同源，便于人工核对版本产物。
    path_zip = path_dist_root / f"{PACKAGE_NAME}-v{str_version}.zip"  # 发布 zip 路径

    # 先清理当前版本旧产物，再生成新的目录与 zip 占位边界。
    _replace_release_outputs(path_release_dir, path_zip)

    # 复制 skill body 到发布目录，并返回稳定的仓库相对路径清单。
    list_included_files = _copy_skill_tree(path_release_dir)  # 被包含的发布文件清单

    # 发布目录中的 Markdown 必须满足 UTF-8 与无 BOM 的文本约束。
    _validate_release_markdown(path_release_dir)

    # RELEASE_MANIFEST 记录来源、校验命令与依赖摘要，供安装前后核对。
    dict_manifest = {  # 发布清单内容
        "version": str_version,  # 目标发布版本
        "tag": f"v{str_version}",  # 目标发布标签
        "package": PACKAGE_NAME,  # 供目录名、zip 名和收据共用的包标识
        "source_commit": _optional_git_output(["rev-parse", "HEAD"]),  # 源仓库提交哈希
        "source_branch": _optional_git_output(["branch", "--show-current"]),  # 源仓库分支名
        "built_at_utc": _utc_timestamp(),  # 发布时间戳
        "included_files": list_included_files,  # 被打包的文件清单
        "excluded_paths": sorted(  # 被排除的路径规则清单
            EXCLUDED_DIR_NAMES | set(EXCLUDED_GLOBS) | EXCLUDED_FILE_SUFFIXES  # 目录名、通配模式与后缀规则并集
        ),
        "validation_commands": VALIDATION_COMMANDS,  # 写入 manifest 供安装后回放的验证命令快照
        "skill_dependencies": dict_dependency_summary,  # 技能依赖摘要
    }

    # Manifest 固定落在发布目录根部，便于人工和自动化工具直接读取。
    path_manifest = path_release_dir / "RELEASE_MANIFEST.json"  # 发布清单路径

    # 先写 manifest，再继续补充 checksum、receipt 与 zip 产物。
    _write_json_file(path_manifest, dict_manifest)

    # 为当前发布目录生成每个文件的 sha256 清单。
    list_checksum_entries = _write_checksums(path_release_dir)  # checksum 条目列表

    # RELEASE_RECEIPT 是 installable release 的强制治理凭据。
    path_receipt = _write_release_receipt(path_release_dir, str_version)  # 发布收据路径

    # 目录内部验证完成后，最后一步才打成 zip 包。
    _write_zip(path_release_dir, path_zip)

    # 对外摘要只暴露调用方需要继续编排的核心字段。
    return {
        "version": str_version,
        "release_dir": str(path_release_dir),
        "zip_path": str(path_zip),
        "file_count": len(
            _release_file_manifest(path_release_dir, receipt_name="RELEASE_RECEIPT.json")
        )
        + 1,
        "checksum_count": len(list_checksum_entries),
        "receipt_path": str(path_receipt),
        "source_commit": dict_manifest["source_commit"],
        "skill_dependency_count": dict_dependency_summary["count"],
    }

# `_load_skill_dependencies_config` 只负责安全导入依赖配置函数。
def _load_skill_dependencies_config() -> Callable[[], list[dict[str, Any]]]:
    """加载 skill 依赖配置函数，并在必要时补充 sys.path。

    Args:
        无。

    Returns:
        指向 `runtime.hls_generator.config.skill_dependencies_config` 的可调用对象。

    Raises:
        ImportError: runtime 配置模块导入失败时由底层导入逻辑向上传递。
        AttributeError: 配置模块缺少目标函数时由底层属性访问向上传递。
    """

    # skill 根目录不在 sys.path 时，需要先把源码根插到最前面。
    if str(SKILL_ROOT) not in sys.path:

        # 保证导入到的是当前工作树源码，而不是环境里旧安装副本。
        sys.path.insert(0, str(SKILL_ROOT))

    # 只在真正需要时导入配置模块，避免模块导入副作用提前发生。
    return cast(
        Callable[[], list[dict[str, Any]]],
        importlib.import_module("runtime.hls_generator.config").skill_dependencies_config,
    )

# `_validate_skill_dependency_manifest` 把原始依赖配置收敛成可发布摘要。
def _validate_skill_dependency_manifest() -> dict[str, object]:
    """校验技能依赖清单，确保 required 项满足 blocking 约束。

    Args:
        无。

    Returns:
        包含依赖数量、依赖 ID、blocking 策略和 recommended 警告列表的摘要字典。

    Raises:
        ReleaseError: 依赖清单缺失、结构无效或 required 项不符合 blocking 约束时抛出。
    """

    # 依赖配置函数延迟加载，避免导入本脚本时强制修改 sys.path。
    func_skill_dependencies_config = _load_skill_dependencies_config()  # 依赖配置函数

    # 依赖配置函数抛出 ValueError 时，需要统一改写成发布治理错误。
    try:

        # 配置函数返回的每一项都应该包含 id、level 与 blocking 语义。
        list_dependencies = func_skill_dependencies_config()  # 依赖项列表

    # ValueError 说明配置内容本身不合法，应阻止继续打包。
    except ValueError as exc:

        # 将配置层异常转成稳定的发布错误，便于 CLI 与流水线统一处理。
        raise ReleaseError(
            f"> ERR: [Python] Invalid skill dependency configuration: {exc}"
        ) from exc

    # 没有任何依赖清单时，安装合同缺少最基本的能力边界。
    if not list_dependencies:

        # 发布前必须至少声明一项依赖，避免安装后能力说明为空。
        raise ReleaseError(
            "> ERR: [Python] Release requires at least one configured skill dependency."
        )

    # required 项一旦不是 blocking，就会让安装方误判关键能力的可用性。
    list_required_nonblocking: list[str] = []  # required 但未 blocking 的依赖 ID

    # 显式循环比复杂列表推导更容易附着规则注释，也方便后续扩展。
    for dict_item in list_dependencies:

        # 这里只收集 required 且没有开启 blocking 的异常配置。
        if dict_item.get("level") == "required" and not dict_item["blocking"]:

            # 记录违规依赖 ID，供后续错误消息直接展示。
            list_required_nonblocking.append(dict_item["id"])

    # required 项发现非 blocking 时，必须在发布前立即阻断。
    if list_required_nonblocking:

        # 这些依赖会破坏安装期的强制约束，因此不能进入 release。
        raise ReleaseError(
            "> ERR: [Python] Required skill dependencies must be blocking: "
            f"{', '.join(list_required_nonblocking)}"
        )

    # recommended 且未 blocking 的项只做披露，不影响发布成功。
    list_recommended_warnings: list[str] = []  # recommended 且未 blocking 的依赖 ID

    # recommended 项只记录披露信息，不会升级成阻塞错误。
    for dict_item in list_dependencies:

        # 这里只收集 recommended 且未 blocking 的提示项。
        if dict_item.get("level") == "recommended" and not dict_item["blocking"]:

            # 记录提示依赖 ID，供 manifest 与 receipt 做透明披露。
            list_recommended_warnings.append(dict_item["id"])

    # 返回给 manifest 与 receipt 的是归一化后的依赖摘要，而不是原始配置体。
    return {
        "count": len(list_dependencies),
        "ids": [dict_item["id"] for dict_item in list_dependencies],
        "blocking_policy": "required_only",
        "recommended_warnings": list_recommended_warnings,
    }

# `_read_runtime_version` 只从源码读取权威版本号，不触发运行时导入副作用。
def _read_runtime_version() -> str:
    """从 runtime 源码读取版本号。

    Args:
        无。

    Returns:
        `runtime/hls_generator/__init__.py` 中声明的版本字符串。

    Raises:
        ReleaseError: 初始化文件里缺少 `__version__` 时抛出。
        OSError: 读取版本文件时的底层文件系统异常向上传递。
    """

    # runtime __init__ 是源码侧对外声明版本号的权威位置。
    path_init = SKILL_ROOT / "runtime" / "hls_generator" / "__init__.py"  # runtime 初始化文件

    # 这里只读文本，不导入 runtime 模块，避免引入额外副作用。
    str_init_text = path_init.read_text(encoding="utf-8")  # runtime 初始化源码

    # 如果源码里找不到 __version__，说明发布元数据已经不完整。
    if not (
        match_runtime_version := re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']',
            str_init_text,
            re.MULTILINE,
        )
    ):

        # 缺少版本号会直接破坏 release identity，因此必须立即阻断。
        raise ReleaseError(f"> ERR: [Python] Could not find __version__ in {path_init}.")

    # 返回 runtime 源码声明的版本号，供主流程与目标版本做一致性校验。
    return match_runtime_version.group(1)

# `_read_cli_version` 验证命令行入口对外暴露的版本仍与源码一致。
def _read_cli_version() -> str:
    """通过 CLI 入口读取版本号，验证命令行入口仍与源码一致。

    Args:
        无。

    Returns:
        从 `python -m runtime.hls_generator --version` 输出中提取的版本号。

    Raises:
        ReleaseError: CLI 运行失败或 stdout 里无法解析出版本号时抛出。
    """

    # 子进程环境基于当前环境复制，再把当前 skill 源码放到 PYTHONPATH 最前面。
    dict_env = os.environ.copy()  # CLI 子进程环境

    # 这样可以确保 CLI 读取的是当前工作树源码，而不是别处安装副本。
    dict_env["PYTHONPATH"] = str(SKILL_ROOT) + os.pathsep + dict_env.get("PYTHONPATH", "")  # 当前 skill 源码优先导入

    # 用当前 Python 解释器执行 runtime 模块，避免跨环境差异。
    completed_process_obj_process: subprocess.CompletedProcess[str] = subprocess.run(  # CLI 版本查询结果
        [sys.executable, "-m", "runtime.hls_generator", "--version"],  # CLI 版本查询命令
        cwd=SKILL_ROOT,  # 在当前 skill 根目录执行
        env=dict_env,  # 使用前面准备好的 CLI 导入环境
        capture_output=True,  # 同时抓取 stdout 与 stderr 供版本失败诊断
        text=True,  # 以文本模式读取 CLI 输出
        encoding="utf-8",  # 用 UTF-8 解码 CLI 输出
        errors="replace",  # 解码异常时用替换字符保住错误上下文
        check=False,  # 由当前函数统一检查返回码并转成治理错误
    )

    # CLI 返回非零说明入口本身已不稳定，必须在发布前阻断。
    if completed_process_obj_process.returncode != 0:

        # 错误摘要优先取 stderr；如果 stderr 为空，则回退到 stdout。
        str_error = completed_process_obj_process.stderr.strip() or completed_process_obj_process.stdout.strip()  # CLI 失败摘要

        # CLI 无法运行时，发布物即使打出来也会在用户入口处失效。
        raise ReleaseError(
            f"> ERR: [Python] Could not read CLI version: {str_error}"
        )

    # CLI 输出里可能包含前缀文本，因此这里按 SemVer 片段提取。
    if not (
        match_cli_version := re.search(
            r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)",
            completed_process_obj_process.stdout.strip(),
        )
    ):

        # 如果 stdout 连 SemVer 都解析不到，说明 CLI 输出契约已经漂移。
        raise ReleaseError(
            "> ERR: [Python] Could not parse CLI version from "
            f"{completed_process_obj_process.stdout.strip()!r}."
        )

    # 返回 CLI 入口对外暴露的版本号，供主流程比较是否一致。
    return match_cli_version.group(1)

# `_resolve_dist_root` 把用户输入路径收敛到仓库内的安全 dist 根目录。
def _resolve_dist_root(path: Path) -> Path:
    """规范化 dist 根目录，并确保目标仍位于仓库边界内。

    Args:
        path: 用户提供的 dist 根目录，可以是相对路径或绝对路径。

    Returns:
        解析后的 dist 根目录绝对路径；函数会确保目录存在。

    Raises:
        ReleaseError: 解析后的目录越出仓库边界时抛出。
        OSError: 创建 dist 目录时的底层文件系统异常向上传递。
    """

    # 相对路径一律相对仓库根解析，避免不同 cwd 得到不同结果。
    path_candidate = path if path.is_absolute() else REPO_ROOT / path  # 候选 dist 目录

    # 解析绝对路径后再做边界判断，可以统一消解 `..` 等跳转。
    path_resolved = path_candidate.resolve()  # 规范化后的 dist 目录

    # 只允许在仓库内创建或替换 dist 产物，避免误写出到未知位置。
    try:

        # relative_to 成功才说明目标路径仍然位于仓库边界内。
        path_resolved.relative_to(REPO_ROOT.resolve())

    # 一旦越界就转成发布错误，阻止后续目录创建与删除动作。
    except ValueError as exc:

        # dist 根目录越界会让删除和写文件逻辑失去安全边界。
        raise ReleaseError(
            f"> ERR: [Python] dist root must stay inside the repository: {path}"
        ) from exc

    # dist 根目录不存在时允许自动创建，避免要求调用方先手工准备。
    path_resolved.mkdir(parents=True, exist_ok=True)

    # 返回已通过边界校验的 dist 根目录给主流程继续使用。
    return path_resolved

# `_replace_release_outputs` 只替换当前目标版本对应的目录和 zip。
def _replace_release_outputs(release_dir: Path, zip_path: Path) -> None:
    """删除同版本旧产物，并准备新的发布目录。

    Args:
        release_dir: 当前版本的目标发布目录路径。
        zip_path: 当前版本的目标 zip 文件路径。

    Returns:
        None；函数只操作目标目录与 zip 路径。

    Raises:
        ReleaseError: 任一路径越出仓库边界时抛出。
        OSError: 删除旧目录、删除旧 zip 或创建新目录时的文件系统异常向上传递。
    """

    # 删除前要逐个复核输出路径，防止同版本覆盖动作越出仓库边界。
    for path_output in (release_dir, zip_path):

        # 每个待处理路径都先解析成绝对路径，再做统一边界判断。
        path_resolved = path_output.resolve()  # 输出目标绝对路径

        # 目录与 zip 都必须位于仓库内，否则删除动作不安全。
        try:

            # 只接受仍位于仓库根目录之下的输出目标。
            path_resolved.relative_to(REPO_ROOT.resolve())

        # 越界时立刻阻断，避免删除用户未授权的外部路径。
        except ValueError as exc:

            # 任何落在仓库外的 release output 都不允许被当前脚本替换。
            raise ReleaseError(
                "> ERR: [Python] Refusing to replace release output outside repository: "
                f"{path_output}"
            ) from exc

    # 旧版发布目录存在时要先删除，避免旧文件残留混入新包。
    if release_dir.exists():

        # 递归清理旧版目录，让新一轮 copy 从空目录开始。
        shutil.rmtree(release_dir)

    # 同版本旧 zip 也必须移除，保证目录与压缩包来源完全一致。
    if zip_path.exists():

        # 删除旧 zip，确保重建后产物只对应本轮发布内容。
        zip_path.unlink()

    # 在清理完旧产物之后再创建新的发布目录。
    release_dir.mkdir(parents=True)

# `_copy_skill_tree` 负责复制发布源文件并记录稳定的仓库相对路径。
def _copy_skill_tree(release_dir: Path) -> list[str]:
    """复制 skill body 到发布目录，并返回稳定的仓库相对路径清单。

    Args:
        release_dir: 已通过路径边界校验的目标发布目录。

    Returns:
        使用 POSIX 风格记录的仓库相对路径列表。

    Raises:
        OSError: 创建目录、复制文件或读取文件系统元数据时的异常向上传递。
    """

    # included_files 会写入 manifest，必须与最终 zip 内容保持稳定一致。
    list_included: list[str] = []  # 被包含的仓库相对路径

    # 源文件顺序必须稳定，后续 checksum、manifest 与 zip 才能一致。
    for path_source in _sorted_release_sources(SKILL_ROOT):

        # manifest 记录的是仓库相对路径，而不是发布目录内相对路径。
        path_repo_relative = path_source.relative_to(REPO_ROOT)  # 源文件仓库相对路径

        # 目标路径保留原始仓库层级，方便安装后回溯来源位置。
        path_destination = release_dir / path_repo_relative  # 发布目录目标路径

        # 父目录必须先存在，copy2 才能稳定落盘。
        path_destination.parent.mkdir(parents=True, exist_ok=True)

        # copy2 保留基础 metadata，便于人工排查时对比源文件。
        shutil.copy2(path_source, path_destination)

        # manifest 使用 POSIX 路径，保证跨平台下比较结果稳定。
        list_included.append(path_repo_relative.as_posix())

    # 返回稳定路径清单给 manifest 与 CLI 摘要复用。
    return list_included

# `_validate_release_markdown` 强制文档满足无 BOM 的 UTF-8 编码约束。
def _validate_release_markdown(release_dir: Path) -> None:
    """校验发布目录里的 Markdown 编码，拒绝 BOM 与非 UTF-8 文本。

    Args:
        release_dir: 已复制完成的发布目录。

    Returns:
        None；校验通过时不返回额外数据。

    Raises:
        ReleaseError: Markdown 带有 UTF-8 BOM 或无法按 UTF-8 解码时抛出。
        OSError: 读取 Markdown 文件时的底层文件系统异常向上传递。
    """

    # 所有 Markdown 都必须满足统一文本约束，避免安装端出现编码漂移。
    for path_markdown in _sorted_markdown_files(release_dir):

        # 先按字节读取，便于在真正解码前检查 BOM。
        bytes_markdown = path_markdown.read_bytes()  # Markdown 原始字节

        # 错误信息只暴露发布目录内相对路径，避免泄漏本地绝对路径。
        str_relative_path = path_markdown.relative_to(release_dir).as_posix()  # Markdown 相对路径

        # BOM 会污染部分渲染器与比对流程，因此明确禁止。
        if bytes_markdown.startswith(b"\xef\xbb\xbf"):

            # 一旦发现 BOM，立刻终止发布准备，避免把有问题的文档打包出去。
            raise ReleaseError(
                f"> ERR: [Python] Release Markdown must not use a UTF-8 BOM: {str_relative_path}"
            )

        # UTF-8 解码验证只关注编码合法性，不需要保留解码后的内容。
        try:

            # 解码失败说明文档编码不符合 release contract。
            bytes_markdown.decode("utf-8")

        # 把底层解码异常转换成面向发布治理的稳定错误消息。
        except UnicodeDecodeError as exc:

            # 非 UTF-8 Markdown 会破坏安装端文本可读性，因此必须阻断。
            raise ReleaseError(
                f"> ERR: [Python] Release Markdown must be UTF-8: {str_relative_path}: {exc}"
            ) from exc

# `_iter_release_files` 递归产出最终允许进入发布包的普通文件。
def _iter_release_files(root: Path) -> Iterable[Path]:
    """遍历未被排除规则过滤掉的发布候选文件。

    Args:
        root: 需要扫描的发布源目录，通常是 `SKILL_ROOT`。

    Returns:
        一个延迟生成的文件迭代器，只产出应进入发布包的普通文件。

    Raises:
        OSError: 递归遍历目录或访问文件系统元数据时的异常向上传递。
    """

    # 递归扫描 root 下所有实体，再统一交给排除规则做裁决。
    for path_candidate in root.rglob("*"):

        # 相对路径用于目录名、通配模式与文件名规则的统一匹配。
        path_relative = path_candidate.relative_to(root)  # 相对源根目录路径

        # 命中排除规则的实体不会继续参与 copy、checksum 与 zip 流程。
        if _is_excluded(path_relative, path_candidate):

            # 直接跳过排除项，保证后续阶段只看到允许发布的文件。
            continue

        # 只有普通文件才会进入 release sources；目录本身不会被产出。
        if path_candidate.is_file():
            yield path_candidate

# `_is_excluded` 统一裁决目录名、文件名、后缀和 glob 的排除逻辑。
def _is_excluded(rel: Path, path: Path) -> bool:
    """判断路径是否应该从发布内容中排除。

    Args:
        rel: 从发布源根目录开始计算的相对路径。
        path: 当前文件系统实体对应的 Path 对象。

    Returns:
        True 表示该路径应被排除；False 表示允许继续进入发布流程。

    Raises:
        OSError: 访问 `path.is_file()` 等文件系统元数据时的异常向上传递。
    """

    # 目录名一旦命中排除集，整条路径都应视为不可发布。
    if any(str_part in EXCLUDED_DIR_NAMES for str_part in rel.parts):

        # 命中目录排除名单后无需继续检查更细粒度规则。
        return True

    # 字节码缓存只对本地解释器有意义，不属于技能交付物。
    if path.is_file() and path.suffix in EXCLUDED_FILE_SUFFIXES:

        # 文件后缀命中缓存规则时直接排除。
        return True

    # 某些文件名即便不在排除目录里，也明确不应进入安装包。
    if path.name in EXCLUDED_FILE_NAMES:

        # 文件名排除规则优先于后续 glob 判断。
        return True

    # 通配排除覆盖日志、中间脚本和 HLS solution 目录等派生工件。
    bool_matches_excluded_glob = any(  # glob 排除匹配结果
        path.match(str_pattern) or rel.match(str_pattern) for str_pattern in EXCLUDED_GLOBS  # 对每条通配规则执行匹配
    )

    # 返回最终的 glob 匹配结果，供上游统一决定是否跳过该路径。
    return bool_matches_excluded_glob

# `_write_checksums` 生成发布目录内所有文件的 sha256 清单文本。
def _write_checksums(release_dir: Path) -> list[str]:
    """为发布目录里每个文件写出 sha256 校验清单。

    Args:
        release_dir: 已准备完成的发布目录。

    Returns:
        写入 `checksums.sha256` 之前的字符串条目列表。

    Raises:
        OSError: 读取文件字节或写出 checksum 文件时的异常向上传递。
    """

    # checksum 条目先在内存里汇总，再一次性写入清单文件。
    list_entries: list[str] = []  # 最终写入 checksums.sha256 的文本行

    # 文件顺序必须稳定，才能让 checksum、receipt 与 zip 的视角一致。
    for path_file in _sorted_files(release_dir):

        # checksum 文件里统一使用发布目录内 POSIX 相对路径。
        str_relative_path = path_file.relative_to(release_dir).as_posix()  # 发布目录内相对路径

        # sha256 直接基于文件字节计算，适用于文本与二进制工件。
        str_digest = hashlib.sha256(path_file.read_bytes()).hexdigest()  # 文件 sha256 摘要

        # 每个条目写成 `digest  path`，便于人工与工具直接校验。
        list_entries.append(f"{str_digest}  {str_relative_path}")

    # checksum 清单固定写在发布目录根部，便于安装前快速核对。
    path_checksums = release_dir / "checksums.sha256"  # checksum 文件路径

    # 用 UTF-8 文本写出 checksum 清单，保持跨平台可读性。
    path_checksums.write_text("\n".join(list_entries) + "\n", encoding="utf-8")

    # 返回条目列表给主流程统计数量并写入对外摘要。
    return list_entries

# `_write_release_receipt` 写出 installable release 必需的治理收据。
def _write_release_receipt(release_dir: Path, version: str) -> Path:
    """写出 installable release 必需的发布收据。

    Args:
        release_dir: 当前版本的发布目录。
        version: 要写入收据中的 SemVer 发布版本号。

    Returns:
        已写出的 `RELEASE_RECEIPT.json` 路径。

    Raises:
        OSError: 收据写盘、遍历文件清单或读取文件内容时的异常向上传递。
    """

    # 收据文件名是 release contract 明确要求的固定名称。
    path_receipt = release_dir / "RELEASE_RECEIPT.json"  # 收据文件路径

    # 收据除了记录版本和文件清单，还要声明 sanitization 已完成。
    dict_receipt = {  # 发布收据内容
        "version": version,  # 收据记录的发布版本
        "tag": f"v{version}",  # 收据记录的发布标签
        "package": PACKAGE_NAME,  # 收据记录的包名
        "generated_at": _utc_timestamp(),  # 收据生成时间
        "files": _release_file_manifest(release_dir, receipt_name=path_receipt.name),  # 收据文件清单
        "sanitization": {  # 脱敏治理摘要
            "required": True,  # 发布前必须执行脱敏
            "mode": "auto-redact-dist-copy",  # 当前脱敏模式
            "status": "completed",  # 当前脱敏状态
            "files": [],  # 暂无额外逐文件脱敏记录
        },
    }

    # 先把收据落盘，再让上游把它计入最终摘要与 zip。
    _write_json_file(path_receipt, dict_receipt)

    # 返回收据路径给主流程生成对外 JSON 摘要。
    return path_receipt

# `_release_file_manifest` 生成收据使用的稳定文件清单。
def _release_file_manifest(
    release_dir: Path,
    *,
    receipt_name: str,
) -> list[dict[str, str]]:
    """生成发布目录文件清单，并排除收据自身。

    Args:
        release_dir: 当前版本的发布目录。
        receipt_name: 需要从清单中排除的收据文件名。

    Returns:
        由 POSIX 路径和 sha256 摘要组成的字典列表。

    Raises:
        OSError: 读取发布文件或计算 sha256 时的异常向上传递。
    """

    # 收据自身不应进入收据里的 files 列表，否则会产生自引用漂移。
    list_manifest = [  # 发布文件清单
        {
            "path": path_file.relative_to(release_dir).as_posix(),  # 文件相对路径
            "sha256": hashlib.sha256(path_file.read_bytes()).hexdigest(),  # 文件内容摘要
        }
        for path_file in _sorted_files(release_dir)  # 收据之外的发布文件
        if path_file.name != receipt_name  # 排除收据自身，避免自引用
    ]

    # 返回稳定文件清单给 receipt 与主流程统计复用。
    return list_manifest

# `_write_zip` 把已验证的发布目录封装成带顶层版本目录的 zip 包。
def _write_zip(release_dir: Path, zip_path: Path) -> None:
    """把发布目录压缩成 zip，并保留顶层版本目录。

    Args:
        release_dir: 需要压缩的发布目录。
        zip_path: 输出 zip 文件路径。

    Returns:
        None；函数只负责写出 zip 文件。

    Raises:
        OSError: 写入 zip 期间访问文件系统失败时向上传递。
    """

    # zip 顶层必须保留版本目录名，避免用户解压后文件直接散落。
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:

        # 归档顺序保持稳定，才能让 zip 内容与其他清单顺序对齐。
        for path_file in _sorted_files(release_dir):

            # 压缩包内路径以发布目录名为根，保持解压后的层级清晰。
            path_archive = Path(release_dir.name) / path_file.relative_to(release_dir)  # zip 内归档路径

            # 把当前文件写入 zip，并绑定到稳定的 archive path。
            archive.write(path_file, path_archive)

# `_git_output` 负责严格模式下读取 manifest 需要的 git 元数据。
def _git_output(args: list[str]) -> str:
    """执行 git 命令并返回去掉首尾空白的 stdout。

    Args:
        args: 传给 `git` 的参数列表，会在 `REPO_ROOT` 中执行。

    Returns:
        去掉首尾空白后的 git stdout 字符串。

    Raises:
        ReleaseError: git 命令返回非零状态时抛出。
    """

    # git 元数据只作为 manifest 辅助字段，因此统一在仓库根目录执行。
    completed_process_obj_process: subprocess.CompletedProcess[str] = subprocess.run(  # git 命令结果
        ["git", *args],  # git 查询命令
        cwd=REPO_ROOT,  # 在仓库根目录执行
        capture_output=True,  # 同时抓取 stdout 与 stderr 供错误拼接
        text=True,  # 让提交哈希与分支名直接以字符串形式回收
        encoding="utf-8",  # 保证 git 的非 ASCII 输出也能稳定回收
        errors="replace",  # 解码异常时保留尽可能完整的 git 错误文本
        check=False,  # 由当前函数统一检查返回码并封装 git 错误
    )

    # git 失败时要把命令和 stderr 一并抛出，方便定位仓库状态问题。
    if completed_process_obj_process.returncode != 0:

        # 这里继续使用 ReleaseError，让调用方维持统一的错误协议。
        raise ReleaseError(
            "> ERR: [Python] git "
            f"{' '.join(args)} failed: {completed_process_obj_process.stderr.strip()}"
        )

    # 成功时返回标准化 stdout，供 manifest 直接写入字段。
    return completed_process_obj_process.stdout.strip()

# `_optional_git_output` 在元数据不可用时提供稳定的 unavailable 占位。
def _optional_git_output(args: list[str]) -> str:
    """读取可选 git 信息；失败时降级为 unavailable。

    Args:
        args: 传给 `git` 的参数列表，会在 `REPO_ROOT` 中执行。

    Returns:
        git stdout；如果 git 调用失败或结果为空，则返回 `unavailable`。

    Raises:
        无；内部会吞掉 `ReleaseError` 并转成稳定占位值。
    """

    # git 元数据不是发布成功的硬前置，所以允许用占位值降级。
    try:

        # 先按严格模式读取 git 字段，再由当前函数决定是否降级。
        str_value = _git_output(args)  # git 输出文本

    # git 失败时回退到 unavailable，避免 metadata 阻断目录打包。
    except ReleaseError:

        # 使用固定占位值，保证 manifest 结构稳定不缺字段。
        return "unavailable"

    # 空字符串同样视为 unavailable，避免写入含义不明的空值。
    return str_value or "unavailable"

# `_sorted_release_sources` 为复制阶段提供稳定的发布源文件顺序。
def _sorted_release_sources(root: Path) -> list[Path]:
    """按稳定 POSIX 路径顺序返回发布源文件列表。

    Args:
        root: 需要生成发布源文件列表的目录。

    Returns:
        以 POSIX 路径小写排序后的源文件列表。

    Raises:
        OSError: 遍历源目录或访问文件系统元数据时的异常向上传递。
    """

    # 统一的稳定排序是 manifest、copy 与 zip 顺序一致的前提。
    list_sources = sorted(  # 稳定排序后的发布源文件
        _iter_release_files(root),  # 允许发布的源文件迭代器
        key=lambda path_item: path_item.as_posix().lower(),  # 让 Markdown 编码检查结果保持稳定顺序
    )

    # 返回稳定顺序给复制阶段直接消费。
    return list_sources

# `_sorted_markdown_files` 为编码校验提供稳定的 Markdown 遍历顺序。
def _sorted_markdown_files(root: Path) -> list[Path]:
    """按稳定 POSIX 路径顺序返回 Markdown 文件列表。

    Args:
        root: 需要扫描 Markdown 的目录。

    Returns:
        以 POSIX 路径小写排序后的 Markdown 文件列表。

    Raises:
        OSError: 递归扫描 Markdown 文件时的异常向上传递。
    """

    # Markdown 校验顺序稳定后，报告与调试输出才更容易比对。
    list_markdown_files = sorted(  # 稳定排序后的 Markdown 文件
        root.rglob("*.md"),  # 当前目录下的 Markdown 文件
        key=lambda path_item: path_item.as_posix().lower(),  # 让 checksum 与 zip 的文件顺序保持确定性
    )

    # 返回稳定顺序给 Markdown 编码校验复用。
    return list_markdown_files

# `_sorted_files` 为 checksum、receipt 和 zip 共享同一份稳定文件顺序。
def _sorted_files(root: Path) -> list[Path]:
    """按稳定 POSIX 路径顺序返回普通文件列表。

    Args:
        root: 需要扫描普通文件的目录。

    Returns:
        以 POSIX 路径小写排序后的普通文件列表。

    Raises:
        OSError: 遍历目录或访问 `is_file()` 元数据时的异常向上传递。
    """

    # checksum、receipt 与 zip 需要共享同一份稳定文件顺序。
    list_files = sorted(  # 稳定排序后的普通文件
        (path_item for path_item in root.rglob("*") if path_item.is_file()),  # 当前目录下的普通文件
        key=lambda path_item: path_item.as_posix().lower(),  # 按不区分大小写的 POSIX 路径排序
    )

    # 返回稳定顺序给多个下游步骤共同消费。
    return list_files

# `_write_json_file` 用统一格式落盘 manifest、receipt 等治理 JSON。
def _write_json_file(path_output: Path, payload: dict[str, object]) -> None:
    """用统一格式写出 UTF-8 JSON 文件。

    Args:
        path_output: 目标 JSON 文件路径。
        payload: 需要序列化并写入的字典对象。

    Returns:
        None；函数只负责把 JSON 文本落到文件系统。

    Raises:
        OSError: 写文件失败时的底层文件系统异常向上传递。
    """

    # 所有发布 JSON 都采用相同缩进、UTF-8 编码和尾随换行。
    str_payload = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"  # JSON 文件文本

    # 统一通过单点 helper 写盘，保证 manifest 与 receipt 格式一致。
    path_output.write_text(str_payload, encoding="utf-8")

# `_utc_timestamp` 统一生成 manifest 与 receipt 共用的 UTC 时间戳格式。
def _utc_timestamp() -> str:
    """生成不含微秒的 UTC ISO-8601 时间戳。

    Args:
        无。

    Returns:
        以 `Z` 结尾的 UTC 时间戳字符串。

    Raises:
        无；当前函数只依赖标准库时间能力。
    """

    # 发布元数据只需要秒级精度，去掉微秒可以减少无意义 diff。
    datetime_obj_now: dt.datetime = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)  # 当前 UTC 时间

    # 统一转成 `Z` 结尾格式，便于 manifest 与 receipt 保持一致。
    return datetime_obj_now.isoformat().replace("+00:00", "Z")

# `__main__` 分支只负责把主流程返回值转成进程退出码。
if __name__ == "__main__":

    # 作为 CLI 直接执行时，把 main 的返回值转换成进程退出码。
    raise SystemExit(main())
