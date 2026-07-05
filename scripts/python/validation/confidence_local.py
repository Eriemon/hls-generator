#!/usr/bin/env python3
"""执行 Erie HLS Generator 本地 confidence gate 的基础检查函数。"""

# future annotations 保持运行期类型注解轻量。
from __future__ import annotations

# 标准库导入覆盖 JSON、进程、路径和归档扫描。
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 记录本地 confidence 默认围绕的技能根目录。
SKILL_ROOT = Path(__file__).resolve().parents[3]  # 技能根目录路径

# confidence helper 目录供本地包装 CLI 委托到 confidence_loop 时复用。
MODULE_DIR = Path(__file__).resolve().parent  # validation helper 模块目录

# 脚本按文件路径直接执行时，需要先暴露技能根和当前 helper 目录。
def _configure_import_path() -> None:
    """把技能根目录和 validation helper 目录加入模块搜索路径。

    参数:
        无。

    返回:
        无；函数只在必要时更新 ``sys.path``。
    """

    # 按优先级依次注入 helper 目录和技能根目录，兼容脚本直跑入口。
    for path_entry in (MODULE_DIR, SKILL_ROOT):

        # sys.path 使用字符串路径，因此先统一规整成文本。
        str_path_entry = str(path_entry)  # 待注入的模块搜索路径文本

        # 仅在缺失时前插，避免重复污染导入顺序。
        if str_path_entry not in sys.path:

            # 把当前仓库里的 helper/runtime 放到最前面，避免误用其他安装副本。
            sys.path.insert(0, str_path_entry)

# runtime 与 integration 导入前必须先完成脚本直跑所需的路径自举。
_configure_import_path()

# runtime 与 integration 模块由调用入口提前配置导入路径。
from runtime.hls_generator import __version__

# HLS 适配层提供 mock 工作流和生成产物校验入口。
from integration.hls_adapter import (
    run_hls_workflow,
    validate_hls_artifacts,
)

# runtime 配置入口负责解析技能内固定目录。
from runtime.hls_generator.config import (
    generated_roots,
    repo_root as configured_repo_root,
    skill_config_path,
    skill_dependencies_config,
)

# 依赖检查器复用 runtime 的技能依赖配置。
from runtime.hls_generator.skill_dependencies import check_skill_dependencies

# workspace helper 负责本地 confidence 输出 JSON 的生成目录边界校验。
from runtime.hls_generator.workspace import require_configured_output_path

# 记录禁止出现在 release 文本扫描中的引用词。
FORBIDDEN_REFERENCE_TERMS = ("vitis-hls-introductory-examples",)  # 禁用引用扫描配置

# 把敏感版权术语拆成片段后再拼接匹配，避免源码里直接出现完整禁用词。
COPYRIGHT_TERM_PARTS = (  # 版权术语拆分片段
    ("off", "icial"),  # 官方来源敏感词的拆分片段
    ("tuto", "rials"),  # 教程来源敏感词的拆分片段
    ("Vitis-", "Tuto", "rials"),  # 组合仓库名称敏感词的拆分片段
    ("UG", "1399"),  # 官方文档编号敏感词的拆分片段
)

# 限定文本扫描只覆盖仓库里需要做敏感词检查的文件类型。
TEXT_SCAN_EXTENSIONS = {".md", ".py", ".json", ".yaml", ".yml", ".txt"}  # 发布扫描匹配结果

# SKIP_SCAN_DIRS 汇总 confidence 扫描时必须排除的生成或测试目录。
SKIP_SCAN_DIRS = {  # confidence 文本扫描排除目录
    ".git",  # Git 元数据目录
    "__pycache__",  # Python 字节码缓存目录
    ".pytest_cache",  # pytest 运行缓存目录
    "reports",  # 已生成报告目录
    "tests",  # 测试代码目录
    *generated_roots(),  # runtime 生成产物目录
}

# 记录 release 文本里必须拦截的本地路径和平台敏感指纹模式。
RELEASE_SENSITIVITY_PATTERNS: tuple[re.Pattern[str], ...] = (  # 发布敏感路径状态
    re.compile(re.escape("/tools/Xilinx/"), re.IGNORECASE),  # Linux 工具安装路径
    re.compile(re.escape("".join(["C", ":", "\\", "Users", "\\"])), re.IGNORECASE),  # Windows 用户目录路径
    re.compile(re.escape("server_list.local.json"), re.IGNORECASE),  # 本地服务器清单文件名
    re.compile(re.escape("xcu50-fsvh2104-2-e"), re.IGNORECASE),  # 受限平台型号指纹
)

# 这些路径承担远端验收说明或测试夹具角色，允许保留受控本地样例。
RELEASE_SENSITIVITY_EXEMPT_REL_PATHS = {  # 允许保留本地样例的相对路径
    "scripts/python/remote/remote_acceptance_common.py",  # 远端验收公共说明脚本
    "scripts/python/remote/remote_acceptance_vitis.py",  # 远端 Vitis 验收入口脚本
    "scripts/python/remote/remote_vitis_acceptance.py",  # 远端 Vitis 验收兼容入口
    "scripts/python/validation/confidence_local.py",  # 本地 confidence 验收脚本
    "tests/smoke/run_smoke.py",  # smoke 验收入口脚本
    "tests/test_remote_vitis_acceptance.py",  # 远端验收测试夹具
    "tests/test_user_config.py",  # 用户配置测试夹具
}

# PASS_STATUS 统一表示 confidence 子检查通过状态。
PASS_STATUS = "passed"  # 质量门通过状态

# REMOTE_SMOKE_SPEC 固定指向远端 smoke 使用的示例规格载荷。
REMOTE_SMOKE_SPEC = "hls_host_kernel_split_spec.json"  # 示例规格载荷

# repo_root 包装 runtime 配置，便于本文件测试时替换根目录解析逻辑。
def repo_root() -> Path:
    """返回当前仓库根目录。

    参数:
        无。

    返回:
        当前技能所属仓库根路径。
    """

    # 返回 runtime 配置解析出的仓库根目录。
    return configured_repo_root()

# 本地命令执行包装统一转换 timeout 和 returncode 状态。
def _run_command(command: list[str], *, cwd: Path, timeout_s: int = 900) -> dict[str, Any]:
    """执行子命令并统一映射 timeout 与完成状态。

    参数:
        command: 要执行的命令行参数列表。
        cwd: 命令执行时使用的工作目录。
        timeout_s: 命令超时秒数。

    返回:
        统一结构的命令执行结果字典。
    """

    # 执行底层子进程并收集 stdout、stderr 与超时状态。
    dict_result = _run_process(command, cwd=cwd, timeout_s=timeout_s)  # 子进程执行结果

    # 超时场景需要补齐截断日志和 timeout 元信息。
    if dict_result["timed_out"]:

        # 返回 timeout 结构化结果，便于上层报告直接展示。
        return _timeout_command_result(command, dict_result, timeout_s)

    # 返回正常完成的结构化结果，统一映射 passed/failed。
    return _completed_command_result(command, dict_result, timeout_s)

# timeout 场景保留原始命令和截断日志，方便上层报告定位卡住的阶段。
def _timeout_command_result(
    command: list[str],
    dict_result: dict[str, Any],
    timeout_s: int,
) -> dict[str, Any]:
    """构造超时场景的标准化命令结果。

    参数:
        command: 被执行的命令行参数列表。
        dict_result: `_run_process` 返回的原始执行结果。
        timeout_s: 命令超时秒数。

    返回:
        表示 timeout 的结构化结果字典。
    """

    # 返回 timeout 场景的统一结果结构。
    return {
        "status": "timeout",
        "command": command,
        "returncode": None,
        "timeout_s": timeout_s,
        "stdout_tail": _tail(dict_result["stdout"]),
        "stderr_tail": _tail(dict_result["stderr"]),
    }

# 正常结束场景统一把 returncode 映射为本地 confidence 状态。
def _completed_command_result(
    command: list[str],
    dict_result: dict[str, Any],
    timeout_s: int,
) -> dict[str, Any]:
    """构造正常完成场景的标准化命令结果。

    参数:
        command: 被执行的命令行参数列表。
        dict_result: `_run_process` 返回的原始执行结果。
        timeout_s: 命令超时秒数。

    返回:
        表示 passed 或 failed 的结构化结果字典。
    """

    # 返回正常结束场景的统一结果结构。
    return {
        "status": "passed" if dict_result["returncode"] == 0 else "failed",
        "command": command,
        "returncode": dict_result["returncode"],
        "timeout_s": timeout_s,
        "stdout_tail": _tail(dict_result["stdout"]),
        "stderr_tail": _tail(dict_result["stderr"]),
    }

# 底层进程执行保留 stdout/stderr，以供上层 confidence 报告截断展示。
def _run_process(command: list[str], *, cwd: Path, timeout_s: int) -> dict[str, Any]:
    """执行子进程并保留完整 stdout/stderr。

    参数:
        command: 要执行的命令行参数列表。
        cwd: 命令执行时使用的工作目录。
        timeout_s: 命令超时秒数。

    返回:
        包含超时状态、返回码和标准输出内容的结果字典。
    """

    # 根据平台补齐子进程组控制参数，确保 timeout 后能清理整棵进程树。
    dict_popen_kwargs: dict[str, Any] = {}  # 子进程平台控制参数

    # Windows 通过 CREATE_NEW_PROCESS_GROUP 建立独立进程组。
    if os.name == "nt":

        # 记录 Windows 子进程组创建标志，供 timeout 清理复用。
        dict_popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)  # Windows 进程组创建标志

    # 非 Windows 平台改用新 session 承载整棵子进程树。
    else:

        # 让 timeout 清理可以覆盖当前命令拉起的整棵 POSIX 子进程树。
        dict_popen_kwargs["start_new_session"] = True  # 供 killpg 复用的新 session 配置

    # 启动底层子进程，并保留句柄给 timeout 清理逻辑复用。
    popen_obj_process_handle: subprocess.Popen[str] = subprocess.Popen(  # 供 communicate 和终止流程复用的进程句柄
        command,  # 实际要执行的命令参数列表
        cwd=cwd,  # 子进程工作目录
        stdout=subprocess.PIPE,  # 捕获标准输出
        stderr=subprocess.PIPE,  # 捕获标准错误
        text=True,  # 以文本模式读取输出
        encoding="utf-8",  # 子进程输出解码编码
        errors="replace",  # 非法字符替换策略
        **dict_popen_kwargs,  # 平台相关的进程组参数
    )

    # 先尝试在给定 timeout 内完成命令执行。
    try:

        # 收集命令正常结束时的 stdout 与 stderr。
        tuple_stdout, tuple_stderr = popen_obj_process_handle.communicate(timeout=timeout_s)  # 正常结束的 stdout/stderr

        # 返回正常结束场景的原始执行结果。
        return {
            "timed_out": False,
            "returncode": popen_obj_process_handle.returncode,
            "stdout": tuple_stdout or "",
            "stderr": tuple_stderr or "",
        }

    # 超时场景要先清理进程树，再尽量回收剩余输出。
    except subprocess.TimeoutExpired:

        # 终止整个进程树，避免 HLS 子流程继续残留。
        _terminate_process_tree(popen_obj_process_handle.pid)

        # 尝试回收进程树被终止后尚未读取的输出。
        try:

            # 收集进程树终止后的剩余 stdout 与 stderr。
            tuple_stdout, tuple_stderr = popen_obj_process_handle.communicate(timeout=10)  # 终止后的 stdout/stderr

        # 子进程若仍无法回收输出，则降级为空串，避免 timeout 处理再次超时。
        except subprocess.TimeoutExpired:

            # 以空串占位未能回收的输出内容。
            tuple_stdout, tuple_stderr = "", ""  # 未回收输出的降级占位值

        # 返回 timeout 场景的原始执行结果。
        return {
            "timed_out": True,
            "returncode": None,
            "stdout": tuple_stdout or "",
            "stderr": tuple_stderr or "",
        }

# 进程树清理按平台选择 Windows taskkill 或 POSIX 进程组终止。
def _terminate_process_tree(pid: int) -> None:
    """按平台清理指定 pid 对应的整棵进程树。

    参数:
        pid: 要终止的根进程 pid。

    返回:
        无。
    """

    # Windows 直接委托 taskkill 递归终止进程树。
    if os.name == "nt":

        # 调用 taskkill 清理目标 pid 及其子进程。
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )

        # Windows 清理完成后直接返回。
        return

    # POSIX 平台通过进程组清理整棵子进程树。
    try:

        # 终止以 pid 为组长的进程组。
        os.killpg(pid, 9)

    # 进程组已退出时忽略异常，避免 timeout 清理链再次失败。
    except ProcessLookupError:

        # 进程组已不存在时无需重复处理。
        return

# quick_validate 位于系统 skill-creator 技能中，本地 confidence 只解析固定入口。
def _quick_validate_path() -> Path:
    """返回系统 `quick_validate.py` 的固定入口路径。

    参数:
        无。

    返回:
        系统 skill-creator 中 `quick_validate.py` 的绝对路径。
    """

    # 拼装系统 skill-creator 提供的 quick_validate 固定入口路径。
    path_quick_validate = (  # 本地 confidence 调用的 quick_validate 绝对路径
        Path.home()  # 当前用户主目录
        / ".codex"  # Codex 本地技能根目录
        / "skills"  # 用户技能集合目录
        / ".system"  # 系统技能命名空间目录
        / "skill-creator"  # skill-creator 技能目录
        / "scripts"  # skill-creator 脚本目录
        / "quick_validate.py"  # quick_validate 固定入口文件
    )

    # 返回 quick_validate 的固定入口路径。
    return path_quick_validate

# 依赖门只检查 core scope，避免本地 confidence 误要求远端验收依赖。
def _skill_dependency_gate() -> dict[str, Any]:
    """执行仅覆盖 core scope 的技能依赖检查。

    参数:
        无。

    返回:
        包含依赖数量与检查报告的结构化结果字典。
    """

    # 尝试读取依赖配置并执行 core scope 检查。
    try:

        # 读取技能依赖配置，供 core scope 检查复用。
        list_dependencies = skill_dependencies_config()  # 技能依赖配置列表

        # 执行 core scope 依赖检查并保留结构化报告。
        dict_report = check_skill_dependencies(list_dependencies, scopes={"core"})  # core scope 依赖检查报告

    # 依赖配置不合法时，直接返回 failed 并保留原始错误信息。
    except ValueError as exc:

        # 返回依赖配置错误对应的失败结果。
        return {"status": "failed", "error": str(exc)}

    # 返回 core scope 依赖检查的结构化结果。
    return {
        "status": "passed" if dict_report["status"] == "ok" else "failed",
        "dependency_count": len(list_dependencies),
        "report": dict_report,
    }

# 版权术语扫描覆盖路径名与文本内容，阻止受限参考资料残留在技能仓库中。
def _copyright_term_scan(*, root: Path | None = None) -> dict[str, Any]:
    """扫描路径名与文本内容中的受限版权术语。

    参数:
        root: 可选的扫描根目录；省略时使用技能根目录。

    返回:
        包含扫描根目录与命中记录的结构化结果字典。
    """

    # 解析本次版权术语扫描的实际根目录。
    scan_root = (root or SKILL_ROOT).resolve()  # 本次扫描的根目录

    # 记录路径名或文件内容里命中的版权术语。
    list_matches: list[str] = []  # 版权术语命中记录

    # 先准备空列表，再逐项编译版权术语的大小写无关正则。
    list_term_patterns: list[tuple[str, re.Pattern[str]]] = []  # 供路径和正文复用的版权术语正则表

    # 逐个把版权术语编译成大小写无关正则，供路径和正文扫描复用。
    for str_term in _copyright_terms():

        # 追加当前版权术语和对应的大小写无关正则对象。
        list_term_patterns.append((str_term, re.compile(re.escape(str_term), re.IGNORECASE)))

    # 遍历扫描根目录下的所有候选路径。
    for path in _iter_scan_paths(scan_root):

        # 命中排除目录时直接跳过该路径。
        if path != scan_root and any(part in SKIP_SCAN_DIRS for part in path.relative_to(scan_root).parts):

            # 跳过生成目录和测试目录，避免无效命中污染结果。
            continue

        # 对非根路径额外检查路径名本身是否携带受限术语。
        if path != scan_root:

            # 逐个术语匹配当前相对路径。
            for str_term, pattern_re_term in list_term_patterns:

                # 路径名命中受限术语时，记录对应的 path 命中来源。
                if pattern_re_term.search(path.relative_to(scan_root).as_posix()):

                    # 追加路径名命中的术语记录。
                    list_matches.append(f"path:{path.relative_to(scan_root).as_posix()}:{str_term}")

        # 读取文件类型前，先确认路径仍然存在。
        try:

            # 判断当前路径是否仍然是文件。
            bool_is_file = path.is_file()  # 当前路径是否是文件

        # 并发生成/清理导致路径消失时，直接跳过当前条目。
        except FileNotFoundError:

            # 跳过已消失的路径，继续扫描下一个条目。
            continue

        # 只继续扫描存在且扩展名受支持的文本文件。
        if not bool_is_file or path.suffix.lower() not in TEXT_SCAN_EXTENSIONS:

            # 跳过非文本文件与不在白名单内的扩展名。
            continue

        # 读取文本文件内容，供版权术语正文扫描使用。
        try:

            # 读取当前文本文件内容。
            str_text = path.read_text(encoding="utf-8", errors="replace")  # 当前文件的文本内容

        # 扫描期间文件消失时，跳过当前条目并继续后续文件。
        except FileNotFoundError:

            # 跳过读取时消失的文件。
            continue

        # 逐个术语匹配当前文件正文内容。
        for str_term, pattern_re_term in list_term_patterns:

            # 正文命中受限术语时，记录对应的 content 命中来源。
            if pattern_re_term.search(str_text):

                # 追加正文命中的术语记录。
                list_matches.append(f"content:{path.relative_to(scan_root).as_posix()}:{str_term}")

    # 返回版权术语扫描的结构化结果。
    return {
        "status": "passed" if not list_matches else "failed",
        "root": str(scan_root),
        "matches": list_matches,
    }

# 禁用引用名扫描覆盖仓库文本，阻止教程样例仓库名残留在技能内容中。
def _forbidden_reference_name_scan() -> dict[str, Any]:
    """扫描仓库文本中是否残留禁用引用名称。

    参数:
        无。

    返回:
        包含 ripgrep 命令、命中列表与异常命中列表的结构化结果字典。
    """

    # 先探测当前环境是否提供 ripgrep，缺失时退回 Python 文本扫描。
    str_ripgrep_path = shutil.which("rg") or ""  # 当前环境可用的 ripgrep 可执行路径

    # ripgrep 缺失时改用纯 Python 回退扫描，避免门禁直接抛出 traceback。
    if not str_ripgrep_path:

        # 用纯 Python 扫描保持禁用引用门仍然有效。
        list_lines = _python_forbidden_reference_name_matches()  # ripgrep 缺失时的命中记录

        # Python 回退扫描只要没有 ref 外异常命中，就视为通过。
        return {
            "status": "passed" if not list_lines else "failed",
            "command": ["python-fallback", "forbidden_reference_name_scan"],
            "matches": list_lines,
            "unexpected_matches": list_lines,
        }

    # 累积 ripgrep 排除参数，保持扫描范围与 confidence 目录排除规则一致。
    list_scan_globs: list[str] = []  # ripgrep 排除参数

    # 逐个展开 ripgrep 需要使用的目录排除参数。
    for glob_item in _scan_exclude_globs():

        # 把当前排除规则写入 ripgrep 参数序列。
        list_scan_globs.extend(["--glob", glob_item])

    # 组装禁用引用名称扫描要执行的完整 ripgrep 命令。
    list_scan_command = ["rg", "-n", *list_scan_globs, "|".join(FORBIDDEN_REFERENCE_TERMS), "."]  # 供 ripgrep 直接执行的禁用引用扫描命令

    # 执行 ripgrep 扫描并保留 stdout 供命中结果解析。
    dict_workflow_result = subprocess.run(  # 包含 stdout/stderr 的 ripgrep 原始执行结果
        list_scan_command,  # ripgrep 命令参数列表
        cwd=SKILL_ROOT,  # ripgrep 扫描工作目录
        capture_output=True,  # 同时捕获 stdout 和 stderr
        text=True,  # 让 communicate 返回字符串而不是字节串
        encoding="utf-8",  # ripgrep 输出解码编码
        errors="replace",  # 保留异常字节附近日志而不是直接解码失败
        check=False,  # 非零退出码改由调用方自行判定
    )

    # 提取 ripgrep 输出里的非空命中行。
    list_lines = [line for line in dict_workflow_result.stdout.splitlines() if line.strip()]  # 供白名单过滤复用的原始命中记录

    # 只保留 ref/ 目录之外的异常命中，避免合法参考区被误报。
    list_unexpected = [line for line in list_lines if not line.split(":", 1)[0].replace("\\", "/").startswith("ref/")]  # 会阻断当前 gate 的 ref 目录外命中记录

    # 同时满足 ripgrep 正常结束且无异常命中时，才视为通过。
    bool_scan_passed = dict_workflow_result.returncode in {0, 1} and not list_unexpected  # 禁用引用扫描通过状态

    # 返回禁用引用名称扫描的结构化结果。
    return {
        "status": "passed" if bool_scan_passed else "failed",
        "command": list_scan_command,
        "matches": list_lines,
        "unexpected_matches": list_unexpected,
    }

# ripgrep 缺失时改用纯 Python 文本扫描，保持禁用引用名称门不退化成崩溃。
def _python_forbidden_reference_name_matches() -> list[str]:
    """在没有 ripgrep 的环境里扫描禁用引用名称命中记录。

    参数:
        无。

    返回:
        ref 目录外命中的 `相对路径:行号:术语` 记录列表。
    """

    # 逐项累计 Python 回退扫描命中的记录。
    list_matches: list[str] = []  # Python 回退扫描命中记录

    # 逐项遍历技能根目录下仍需参与文本检查的路径。
    for path_candidate in _iter_scan_paths(SKILL_ROOT):

        # 根目录本身和目录节点都不参与正文匹配。
        if path_candidate == SKILL_ROOT or not path_candidate.is_file():

            # 跳过不具备正文内容的路径节点。
            continue

        # 只扫描 confidence 已声明的文本文件扩展名。
        if path_candidate.suffix.lower() not in TEXT_SCAN_EXTENSIONS:

            # 跳过非文本文件，避免无意义读取二进制内容。
            continue

        # 统一生成技能根相对路径，供 ref 白名单和结果文本复用。
        str_rel_path = path_candidate.relative_to(SKILL_ROOT).as_posix()  # 当前候选文件的技能根相对路径

        # ref 参考区和当前两份验证脚本都不应参与禁用引用阻断。
        if str_rel_path.startswith("ref/") or str_rel_path in {
            "scripts/python/validation/confidence_local.py",
            "scripts/python/validation/confidence_loop.py",
        }:

            # 跳过白名单路径，避免把受控参考区或当前门脚本本身算作异常命中。
            continue

        # 读取当前文本文件内容，供逐行术语匹配使用。
        try:

            # 以 UTF-8 容错方式读取当前文本文件内容。
            str_text = path_candidate.read_text(encoding="utf-8", errors="replace")  # 当前候选文件文本

        # 扫描期间文件消失时跳过当前条目，保持回退扫描稳定。
        except FileNotFoundError:

            # 跳过读取时已被并发清理的文件。
            continue

        # 逐行枚举文本内容，便于返回稳定的行号证据。
        for int_line_number, str_line in enumerate(str_text.splitlines(), start=1):

            # 逐个检查当前行是否包含禁用引用名称。
            for str_term in FORBIDDEN_REFERENCE_TERMS:

                # 当前行命中禁用术语时登记 `路径:行号:术语` 证据。
                if str_term in str_line:

                    # 追加当前行的禁用引用命中记录。
                    list_matches.append(f"{str_rel_path}:{int_line_number}:{str_term}")

    # 返回 Python 回退扫描整理出的全部命中记录。
    return list_matches

# 发布敏感扫描同时覆盖源码根目录与可选 dist 产物，阻止本地路径泄漏到交付件。
def _release_sensitivity_scan(*, root: Path | None = None) -> dict[str, Any]:
    """扫描源码目录和发布产物中的敏感路径或文本片段。

    参数:
        root: 可选的扫描根目录；省略时自动追加当前版本 dist 产物。

    返回:
        包含扫描根集合与命中记录的结构化结果字典。
    """

    # 解析本次发布敏感扫描的起始根目录。
    scan_root = (root or SKILL_ROOT).resolve()  # 发布敏感扫描起始根目录

    # 组合源码目录与当前版本发布产物的实际扫描根集合。
    list_roots = _release_sensitivity_roots(scan_root, root)  # 发布敏感扫描根目录集合

    # 汇总所有扫描根返回的敏感命中记录。
    list_matches: list[str] = []  # 发布敏感命中记录

    # 逐个扫描源码目录和发布产物对应的根路径。
    for active_root in list_roots:

        # 合并当前扫描根返回的敏感命中记录。
        list_matches.extend(_scan_release_root(active_root))

    # 返回发布敏感扫描的结构化结果。
    return {
        "status": "passed" if not list_matches else "failed",
        "roots": [str(item) for item in list_roots],
        "matches": list_matches,
    }

# 发布扫描默认覆盖技能源码根目录，并在存在时追加当前版本 dist 产物。
def _release_sensitivity_roots(scan_root: Path, root: Path | None) -> list[Path]:
    """整理发布敏感扫描需要覆盖的根路径集合。

    参数:
        scan_root: 当前显式指定或默认解析得到的扫描根目录。
        root: 用户是否显式限制扫描根目录的原始参数。

    返回:
        需要执行发布敏感扫描的路径列表。
    """

    # 默认先把源码扫描根加入候选扫描集合。
    list_roots = [scan_root]  # 发布敏感扫描根列表

    # 显式传入 root 时，只扫描调用方指定的目录。
    if root is not None:

        # 直接返回显式指定的扫描根集合。
        return list_roots

    # 解析当前版本 dist 目录形式的发布产物路径。
    path_release_dir = repo_root() / "dist" / f"erie-hls-generator-v{__version__}"  # 目录形式发布产物路径

    # 如果存在同版本 zip 归档，也把它纳入敏感文本扫描范围。
    path_release_zip = repo_root() / "dist" / f"erie-hls-generator-v{__version__}.zip"  # 归档版发布产物路径

    # dist 目录存在时，把目录版发布产物纳入扫描集合。
    if path_release_dir.exists():

        # 追加目录版发布产物到扫描根集合。
        list_roots.append(path_release_dir)

    # zip 包存在时，把归档版发布产物纳入扫描集合。
    if path_release_zip.exists():

        # 追加归档版发布产物到扫描根集合。
        list_roots.append(path_release_zip)

    # 返回最终整理好的扫描根集合。
    return list_roots

# 单个发布根目录可能是源码目录，也可能是已打包的 zip 归档。
def _scan_release_root(active_root: Path) -> list[str]:
    """扫描单个发布根路径中的敏感路径或正文内容。

    参数:
        active_root: 当前要扫描的源码目录或 zip 归档路径。

    返回:
        当前发布根命中的敏感记录列表。
    """

    # 汇总当前发布根扫描得到的命中记录。
    list_matches: list[str] = []  # 当前发布根的敏感命中记录

    # zip 归档交由专用扫描函数处理成员内容。
    if active_root.is_file() and active_root.suffix.lower() == ".zip":

        # 返回 zip 归档扫描得到的命中记录。
        return _scan_release_zip(active_root)

    # 遍历当前源码根下的目录与文件路径。
    for path in _iter_scan_paths(active_root):

        # 命中排除目录的路径直接跳过，避免测试与缓存内容干扰发布检查。
        if path != active_root and any(part in SKIP_SCAN_DIRS for part in path.relative_to(active_root).parts):

            # 跳过被排除目录中的候选路径。
            continue

        # 规范化当前候选路径相对发布根的展示形式。
        str_rel_path = path.relative_to(active_root).as_posix() if path != active_root else "."  # 当前候选相对路径

        # 先检查路径名本身是否带有受限平台或本地路径指纹。
        for re_pattern_term in RELEASE_SENSITIVITY_PATTERNS:

            # 路径名命中敏感模式时记录 path 类型命中。
            if re_pattern_term.search(str_rel_path):

                # 追加路径名对应的敏感命中记录。
                list_matches.append(f"path:{active_root.name}:{str_rel_path}:{re_pattern_term.pattern}")

        # 进入正文扫描前，先确认候选路径仍然存在且为文件。
        try:

            # 判断当前候选路径是否是可读取文件。
            bool_is_file = path.is_file()  # 当前候选是否是文件

        # 扫描期间路径已消失时，直接跳过当前条目。
        except FileNotFoundError:

            # 跳过扫描过程中已被删除的路径。
            continue

        # 非文本文件不参与发布正文敏感词扫描。
        if not bool_is_file or path.suffix.lower() not in TEXT_SCAN_EXTENSIONS:

            # 跳过不可读文件和不在白名单内的扩展名。
            continue

        # 远端验收样例与测试夹具中的受控本地路径不计入阻断。
        if _release_sensitivity_is_exempt(str_rel_path):

            # 跳过允许保留本地样例的受控相对路径。
            continue

        # 读取文本文件内容，检查正文里的敏感路径和平台指纹。
        try:

            # 读取当前文本文件的正文内容。
            str_text = path.read_text(encoding="utf-8", errors="replace")  # 当前文本文件正文

        # 正文读取期间文件消失时，跳过当前条目并继续后续扫描。
        except FileNotFoundError:

            # 跳过读取阶段已消失的文本文件。
            continue

        # 再检查正文里是否出现受限平台或本地路径指纹。
        for re_pattern_term in RELEASE_SENSITIVITY_PATTERNS:

            # 正文命中敏感模式时记录 content 类型命中。
            if re_pattern_term.search(str_text):

                # 追加正文对应的敏感命中记录。
                list_matches.append(f"content:{active_root.name}:{str_rel_path}:{re_pattern_term.pattern}")

    # 返回当前发布根整理出的全部命中记录。
    return list_matches

# zip 归档扫描复用发布敏感规则，但读取内容必须通过归档成员接口完成。
def _scan_release_zip(archive_path: Path) -> list[str]:
    """扫描 zip 发布归档中的敏感路径或正文内容。

    参数:
        archive_path: 当前要扫描的 zip 归档路径。

    返回:
        当前 zip 归档命中的敏感记录列表。
    """

    # 汇总 zip 成员路径与正文内容的敏感命中记录。
    list_matches: list[str] = []  # zip 归档敏感命中记录

    # 打开 zip 归档，按成员名逐项执行路径和正文扫描。
    with zipfile.ZipFile(archive_path) as archive:

        # 逐项处理 zip 中登记的成员名，兼顾路径命中和正文命中两类检查。
        for name in archive.namelist():

            # 先去掉目录成员结尾的斜杠，得到稳定的相对路径字符串。
            str_rel_name = name.rstrip("/")  # 归档成员相对路径

            # 空成员名不具备扫描意义，直接跳过。
            if not str_rel_name:

                # 跳过归档里的空目录占位成员。
                continue

            # 统一归档成员路径分隔符，便于敏感模式匹配。
            str_rel_path = str_rel_name.replace("\\", "/")  # 归档成员规范化相对路径

            # 先检查归档成员路径名本身是否泄漏受限指纹。
            for obj_pattern_term in RELEASE_SENSITIVITY_PATTERNS:

                # 路径字符串一旦命中敏感模式，就记录成 path 级证据。
                if obj_pattern_term.search(str_rel_path):

                    # 追加归档成员路径名对应的敏感命中记录。
                    list_matches.append(f"path:{archive_path.name}:{str_rel_path}:{obj_pattern_term.pattern}")

            # 非文本成员和目录成员都不需要进入正文扫描。
            if Path(str_rel_path).suffix.lower() not in TEXT_SCAN_EXTENSIONS or str_rel_name.endswith("/"):

                # 跳过归档中的目录成员或非文本成员。
                continue

            # 受控样例路径即使命中本地示例，也不计入发布阻断。
            if _release_sensitivity_is_exempt(str_rel_path):

                # 跳过允许保留本地样例的归档成员。
                continue

            # 读取归档成员文本内容，继续执行正文敏感模式扫描。
            str_text = archive.read(name).decode("utf-8", errors="replace")  # 归档成员正文内容

            # 再检查归档成员正文中是否出现敏感路径或平台指纹。
            for obj_pattern_term in RELEASE_SENSITIVITY_PATTERNS:

                # 文本正文命中敏感模式时，记录成 content 级证据。
                if obj_pattern_term.search(str_text):

                    # 追加归档成员正文对应的敏感命中记录。
                    list_matches.append(f"content:{archive_path.name}:{str_rel_path}:{obj_pattern_term.pattern}")

    # 返回当前 zip 归档整理出的全部命中记录。
    return list_matches

# 扫描路径枚举器同时返回根目录、自目录和文件，供多个扫描器复用。
def _iter_scan_paths(root: Path):
    """枚举扫描根下的目录与文件路径。

    参数:
        root: 当前要展开的扫描根路径。

    返回:
        逐项产出扫描根、自目录和文件路径的生成器。
    """

    # 先产出扫描根本身，便于调用方统一处理根目录级检查。
    yield root

    # 继续按目录树顺序枚举根目录下的子目录和文件。
    for current_root, list_dirnames, list_filenames in os.walk(root, topdown=True, onerror=lambda _exc: None):

        # 把 os.walk 返回的当前目录字符串转成 Path 对象。
        path_current_root = Path(current_root)  # 当前遍历目录路径

        # 原地剔除不需要展开的缓存、测试和生成目录。
        list_dirnames[:] = [name for name in list_dirnames if name not in SKIP_SCAN_DIRS]  # 保留继续下钻的目录名

        # 逐个产出当前目录下仍需递归扫描的子目录。
        for dirname in list_dirnames:

            # 返回当前子目录路径，供调用方继续执行路径级检查。
            yield path_current_root / dirname

        # 逐个产出当前目录下需要扫描的文件路径。
        for filename in list_filenames:

            # 返回当前文件路径，供调用方执行正文或路径名检查。
            yield path_current_root / filename

# 受控样例白名单用于排除远端验收与测试夹具中允许出现的本地示例路径。
def _release_sensitivity_is_exempt(rel_path: str) -> bool:
    """判断相对路径是否属于发布敏感扫描白名单。

    参数:
        rel_path: 相对扫描根的规范化或待规范化路径字符串。

    返回:
        当前相对路径是否允许保留受控本地样例。
    """

    # 先统一路径分隔符并移除前导 ./，得到稳定的比较键。
    str_normalized = rel_path.replace("\\", "/").lstrip("./")  # 规范化后的相对路径

    # 解析技能根相对于仓库根的路径标记，兼容不同扫描根的相对路径形式。
    str_skill_marker = f"/{SKILL_ROOT.relative_to(repo_root()).as_posix().rstrip('/')}/"  # 技能根相对路径标记

    # 路径里若包含技能根前缀，则裁掉前缀后再与白名单比较。
    if str_skill_marker in f"/{str_normalized}":

        # 把技能根前缀裁掉，得到真正用于白名单比对的仓库内相对路径。
        str_normalized = f"/{str_normalized}".split(str_skill_marker, 1)[1]  # 去掉技能根前缀后的相对路径

    # 返回当前路径是否位于受控样例白名单中。
    return str_normalized in RELEASE_SENSITIVITY_EXEMPT_REL_PATHS

# 匹配结果里的路径规范化为相对技能根的形式，便于上层报告和白名单比较。
def _relative_match_path(line: str) -> str:
    """把扫描命中行中的路径折叠成相对技能根路径。

    参数:
        line: ripgrep 或其他扫描输出的一整行命中记录。

    返回:
        相对技能根的规范化路径字符串。
    """

    # 提取命中行里的路径部分并统一分隔符。
    str_path_text = line.split(":", 1)[0].replace("\\", "/")  # 命中行中的路径部分

    # 去掉 ripgrep 可能携带的前导 ./ 前缀。
    if str_path_text.startswith("./"):

        # 裁掉前导 ./，避免影响后续相对路径比较。
        str_path_text = str_path_text[2:]  # 去掉前导 ./ 后的路径

    # 准备技能根的绝对路径前缀，优先匹配本地绝对路径输出。
    str_root_prefix = SKILL_ROOT.as_posix().rstrip("/") + "/"  # 技能根绝对路径前缀

    # 命中行若以技能根绝对路径开头，则直接裁成相对路径。
    if str_path_text.startswith(str_root_prefix):

        # 返回相对技能根的路径字符串。
        return str_path_text[len(str_root_prefix) :]

    # 再准备技能根相对于仓库根的路径标记，兼容部分工具只输出仓库内相对路径。
    str_marker = SKILL_ROOT.relative_to(repo_root()).as_posix().rstrip("/") + "/"  # 技能根仓库相对路径标记

    # 命中行若包含技能根相对路径标记，则从该标记后裁出相对路径。
    if str_marker in str_path_text:

        # 返回裁掉技能根前缀后的相对路径。
        return str_path_text.split(str_marker, 1)[1]

    # 返回无法进一步归一化时的原始规范化路径。
    return str_path_text

# static mock workflow helper 保持本地 confidence 不触发外部 HLS 工具。
def _run_static_mock_workflow(dict_spec: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """执行仅依赖 mock provider 的静态 HLS 工作流。

    参数:
        dict_spec: 当前待验证的 HLS 规格字典。
        out_dir: 当前规格的工作流输出目录。

    返回:
        `run_hls_workflow` 返回的结构化工作流结果。
    """

    # 返回关闭外部工具执行后的 mock 工作流结果。
    return run_hls_workflow(
        dict_spec,
        out_dir=out_dir,
        provider_name="mock",
        readiness="static",
        run_external=False,
        comment_language="zh",
    )

# static artifact 校验 helper 统一缺失产物时的失败报告。
def _validate_static_artifacts(
    dict_spec: dict[str, Any],
    artifact_dir: Path,
    dict_missing_report: dict[str, Any],
) -> dict[str, Any]:
    """校验静态工作流产物，缺失时返回调用方提供的失败载荷。

    参数:
        dict_spec: 当前待校验的 HLS 规格字典。
        artifact_dir: 预期生成 `artifacts` 的目录路径。
        dict_missing_report: 产物不存在时直接返回的保底失败结果。

    返回:
        静态产物校验结果，或缺失场景下的预置失败载荷。
    """

    # 产物目录存在时，继续执行正式的静态产物校验。
    if artifact_dir.exists():

        # 返回静态产物校验器生成的结构化报告。
        return validate_hls_artifacts(
            dict_spec,
            artifact_dir,
            readiness="static",
            run_external=False,
            comment_language="zh",
        )

    # 返回调用方提供的缺失产物失败载荷。
    return dict_missing_report

# 本地示例验证覆盖技能内 HLS JSON 规格，并返回参与验证的文件清单。
def _validate_examples(run_root: Path) -> tuple[dict[str, Any], list[str]]:
    """执行 examples 目录下 HLS 规格的本地静态验证。

    参数:
        run_root: 本轮示例验证写入工作目录的根路径。

    返回:
        示例验证汇总报告，以及实际参与验证的规格文件名列表。
    """

    # 解析 examples 目录，供后续枚举示例规格文件使用。
    path_examples_dir = skill_config_path("examples_dir")  # 供文件名枚举复用的 examples 根目录

    # 收集 examples 目录下属于 HLS 规格的 JSON 文件。
    list_spec_paths = [path for path in sorted(path_examples_dir.glob("*.json")) if _is_example_spec_path(path)]  # 参与示例验证的规格路径列表

    # 汇总每个示例规格的工作流与静态产物验证结果。
    list_results: list[dict[str, Any]] = []  # 示例规格验证结果列表

    # 逐个执行示例规格对应的 mock 工作流和静态产物校验。
    for spec_path in list_spec_paths:

        # 读取当前示例规格的 JSON 载荷。
        dict_spec = json.loads(spec_path.read_text(encoding="utf-8"))  # 当前示例规格内容

        # 为当前规格准备独立的工作流输出目录。
        path_output_dir = run_root / spec_path.stem  # 当前规格输出目录

        # 执行当前规格对应的 mock HLS 工作流。
        dict_workflow_result = _run_static_mock_workflow(dict_spec, path_output_dir)  # 当前规格工作流结果

        # 解析当前规格首轮尝试产出的 artifacts 目录。
        path_artifact_dir = Path(dict_workflow_result["run_dir"]) / "attempt-001" / "hls" / "artifacts"  # 当前规格产物目录

        # dict_missing_report 保存产物缺失时的静态校验失败载荷。
        dict_missing_report = {"ok": False, "errors": 1, "warnings": 0}  # 缺失产物失败报告

        # 校验当前规格生成的静态产物是否完整可用。
        dict_report = _validate_static_artifacts(dict_spec, path_artifact_dir, dict_missing_report)  # 当前规格静态校验报告

        # 追加当前规格的聚合验证结果。
        list_results.append(
            {
                "spec": spec_path.name,
                "workflow_status": dict_workflow_result.get("status"),
                "validation_ok": bool(dict_report.get("ok")),
                "errors": dict_report.get("errors"),
                "warnings": dict_report.get("warnings"),
            }
        )

    # 只有所有规格都通过工作流和静态产物校验时，整体示例验证才算通过。
    bool_passed = all(item["workflow_status"] == "passed" and item["validation_ok"] for item in list_results)  # 示例验证总体通过标记

    # list_validated_specs 保存本轮实际参与本地示例验证的规格文件名。
    list_validated_specs = [path.name for path in list_spec_paths]  # 示例规格文件名清单

    # dict_examples_report 保存示例验证聚合状态。
    dict_examples_report = {"status": "passed" if bool_passed else "failed", "results": list_results}  # 供 confidence 汇总复用的示例验证报告

    # 返回示例验证的聚合报告和参与验证的规格清单。
    return dict_examples_report, list_validated_specs

# 规格文件名集合用于远端覆盖模式和报告摘要，不直接携带完整路径。
def _example_spec_names() -> list[str]:
    """返回 examples 目录中有效 HLS 规格的文件名列表。

    参数:
        无。

    返回:
        通过 HLS 规格筛选的 JSON 文件名列表。
    """

    # 定位 examples 根目录，作为文件名筛选逻辑的输入来源。
    path_examples_dir = skill_config_path("examples_dir")  # 示例规格目录路径

    # 返回通过 HLS 规格筛选的文件名列表。
    return [path.name for path in sorted(path_examples_dir.glob("*.json")) if _is_example_spec_path(path)]

# 远端默认规格集合按覆盖模式切换 smoke、tier1 或全部示例。
def _remote_default_example_specs(coverage_mode: str) -> list[str]:
    """根据覆盖模式返回远端默认示例规格集合。

    参数:
        coverage_mode: 远端验收采用的覆盖模式名称。

    返回:
        与覆盖模式对应的示例规格文件名列表。

    异常:
        ValueError: 当 coverage_mode 不在支持列表中时抛出。
    """

    # smoke 模式只保留最小远端冒烟规格。
    if coverage_mode == "smoke":

        # 返回 smoke 模式对应的单个远端规格。
        return [REMOTE_SMOKE_SPEC]

    # tier1 模式从板卡覆盖矩阵中提取代表性和高风险规格。
    if coverage_mode == "tier1":

        # 读取 tier1 板卡覆盖矩阵，供规格集合提取逻辑使用。
        dict_matrix = _load_tier1_board_matrix()  # tier1 板卡覆盖矩阵

        # 提取矩阵中的 families 配置段。
        dict_families = dict_matrix.get("families", {}) if isinstance(dict_matrix, dict) else {}  # families 配置映射

        # 汇总代表性规格和高风险规格对应的文件名。
        list_spec_names: list[str] = []  # tier1 默认规格文件名列表

        # 遍历每个 family 配置，提取代表性与高风险规格。
        for config in dict_families.values():

            # 非字典 family 配置不参与 tier1 规格提取。
            if not isinstance(config, dict):

                # 跳过结构异常的 family 配置条目。
                continue

            # 依次读取代表性规格和高风险规格字段。
            for key in ("representative", "high_risk"):

                # 读取当前 family 在指定字段下声明的规格文件名。
                str_spec_value = str(config.get(key) or "").strip()  # 当前 family 的规格文件名

                # 非空且未去重的规格文件名需要加入返回集合。
                if str_spec_value and str_spec_value not in list_spec_names:

                    # 追加当前 family 提供的唯一规格文件名。
                    list_spec_names.append(str_spec_value)

        # 返回 tier1 模式整理出的默认规格集合。
        return list_spec_names

    # all_examples 模式直接返回 examples 目录下的全部 HLS 规格。
    if coverage_mode == "all_examples":

        # 返回 examples 目录中的完整规格文件名列表。
        return _example_spec_names()

    # 抛出不支持的覆盖模式错误，阻止远端验收使用未知策略。
    raise ValueError(f"> ERR: [Python] Unsupported remote coverage mode: {coverage_mode!r}")

# 显式规格列表优先于覆盖模式，且需要先去空值并保持输入顺序去重。
def _resolve_remote_example_specs(explicit_specs: list[str] | None, coverage_mode: str) -> tuple[list[str], str]:
    """解析远端验收要使用的示例规格集合与来源标签。

    参数:
        explicit_specs: 调用方显式指定的规格文件名列表，允许为空。
        coverage_mode: 未显式指定规格时使用的覆盖模式名称。

    返回:
        去重后的规格文件名列表，以及规格来源标签。
    """

    # 先清理显式规格列表中的空字符串和空白项。
    list_cleaned_explicit_specs: list[str] = []  # 清洗后的显式规格文件名列表

    # 遍历调用方显式传入的规格候选项。
    for spec_name in explicit_specs or []:

        # 只保留去空白后仍然非空的规格文件名。
        if str(spec_name).strip():

            # 追加通过清洗的显式规格文件名。
            list_cleaned_explicit_specs.append(spec_name)

    # 显式规格列表非空时，优先按输入顺序去重后返回。
    if list_cleaned_explicit_specs:

        # 汇总保持输入顺序的唯一规格文件名。
        list_unique_specs: list[str] = []  # 去重后的显式规格文件名列表

        # 逐项去重显式规格文件名，保留首次出现顺序。
        for spec_name in list_cleaned_explicit_specs:

            # 首次出现的规格文件名才加入最终结果。
            if spec_name not in list_unique_specs:

                # 追加当前唯一显式规格文件名。
                list_unique_specs.append(spec_name)

        # 返回显式规格列表及其来源标签。
        return list_unique_specs, "explicit_specs"

    # 未显式指定规格时，退回覆盖模式对应的默认规格集合。
    return _remote_default_example_specs(coverage_mode), coverage_mode

# tier1 板卡覆盖矩阵固定保存在 examples 目录下的专用 JSON 文件中。
def _tier1_board_matrix_path() -> Path:
    """返回 tier1 板卡覆盖矩阵 JSON 的固定路径。

    参数:
        无。

    返回:
        `tier1_board_coverage_matrix.json` 的绝对路径。
    """

    # 返回 examples 目录中 tier1 覆盖矩阵的固定路径。
    return skill_config_path("examples_dir") / "tier1_board_coverage_matrix.json"

# tier1 覆盖矩阵读取包装成独立函数，便于默认规格选择逻辑复用。
def _load_tier1_board_matrix() -> dict[str, Any]:
    """读取并解析 tier1 板卡覆盖矩阵 JSON。

    参数:
        无。

    返回:
        tier1 板卡覆盖矩阵对应的字典结构。
    """

    # 返回解析后的 tier1 板卡覆盖矩阵内容。
    return json.loads(_tier1_board_matrix_path().read_text(encoding="utf-8"))

# HLS 规格识别器通过 target 字段过滤 examples 目录中的 JSON 文件。
def _is_example_spec_path(path: Path) -> bool:
    """判断指定 JSON 文件是否属于 HLS 示例规格。

    参数:
        path: 当前要判断的 JSON 文件路径。

    返回:
        当前文件是否声明 `target=hls`。
    """

    # 尝试读取并解析当前 JSON 文件内容。
    try:

        # 解析当前 JSON 文件对应的规格载荷。
        dict_payload = json.loads(path.read_text(encoding="utf-8"))  # 当前 JSON 规格载荷

    # JSON 语法无效的文件不能视为合法 HLS 规格。
    except json.JSONDecodeError:

        # 返回 False，表示当前文件不属于可用 HLS 规格。
        return False

    # 返回 target 字段是否明确声明为 hls。
    return str(dict_payload.get("target") or "").strip().lower() == "hls"

# forward test 规格集合覆盖较接近真实交付的代表性 JSON 样本。
def _forward_test_gate(run_root: Path) -> dict[str, Any]:
    """执行近真实规格集合的静态 forward test。

    参数:
        run_root: 本轮 forward test 写入工作目录的根路径。

    返回:
        forward test 的聚合执行结果字典。
    """

    # 固定挑选覆盖主要接口和模式组合的代表性规格集合。
    list_spec_names = [  # near-real forward test 使用的代表性规格文件名
        "hls_2d_block_transform_spec.json",  # 2D block 变换代表规格
        "hls_array_reshape_vector_scale_spec.json",  # array reshape 代表规格
        "hls_axi4_burst_vector_scale_spec.json",  # 覆盖 AXI4 burst 搬运场景
        "hls_dataflow_axis_spec.json",  # 覆盖 DATAFLOW 分阶段并行场景
        "hls_directio_freerun_axis_spec.json",  # 覆盖 direct-io freerun 接口场景
        "hls_fixed_point_scale_spec.json",  # 定点缩放代表规格
        "hls_host_kernel_split_spec.json",  # 覆盖 host 与 kernel 拆分场景
        "hls_minimal_vitis_pipeline_spec.json",  # 最小 Vitis pipeline 代表规格
        "hls_multi_m_axi_add_spec.json",  # 多 m_axi 加法代表规格
        "hls_partition_vector_scale_spec.json",  # 覆盖 array partition 访存场景
        "hls_streamofblocks_axis_spec.json",  # 覆盖 stream-of-blocks 传输场景
        "hls_task_graph_axis_spec.json",  # 覆盖 task graph 编排场景
    ]

    # 汇总每个代表性规格的工作流与静态产物验证结果。
    list_results: list[dict[str, Any]] = []  # forward test 结果列表

    # 逐个执行代表性规格的 mock 工作流和静态产物校验。
    for spec_name in list_spec_names:

        # 解析当前代表性规格在 examples 目录中的路径。
        path_spec = skill_config_path("examples_dir") / spec_name  # 当前代表性规格路径

        # 读取当前代表性规格的 JSON 载荷。
        dict_spec = json.loads(path_spec.read_text(encoding="utf-8"))  # 当前代表性规格内容

        # 为当前规格准备独立输出目录，避免不同规格的工件相互覆盖。
        path_output_dir = run_root / "forward-test" / path_spec.stem  # 当前规格独占的 forward test 输出目录

        # 执行 mock 工作流，收集当前规格的结构化执行结果。
        dict_workflow_result = _run_static_mock_workflow(dict_spec, path_output_dir)  # 当前规格的工作流结果字典

        # 定位首轮尝试的静态产物目录，供后续工件校验复用。
        path_artifact_dir = Path(dict_workflow_result["run_dir"]) / "attempt-001" / "hls" / "artifacts"  # 当前规格首轮尝试的产物目录

        # 产物目录缺失时，用这个兜底结果保持 forward test 汇总结构稳定。
        dict_missing_report = {"ok": False, "errors": 1, "warnings": 0}  # 缺失产物时返回的保底失败载荷

        # 把当前规格的静态产物交给统一校验器，补齐 workflow 之后的工件验收。
        dict_report = _validate_static_artifacts(dict_spec, path_artifact_dir, dict_missing_report)  # 当前规格的静态产物校验报告

        # 追加当前代表性规格的聚合验证结果。
        list_results.append(
            {
                "spec": spec_name,
                "workflow_status": dict_workflow_result.get("status"),
                "validation_ok": bool(dict_report.get("ok")),
                "errors": dict_report.get("errors"),
                "warnings": dict_report.get("warnings"),
                "mode": "near_real_spec_static",
            }
        )

    # 只有全部代表性规格通过时，forward test 才视为通过。
    bool_passed = all(item["workflow_status"] == "passed" and item["validation_ok"] for item in list_results)  # forward test 总通过标记

    # 返回 forward test 的聚合执行结果。
    return {"status": "passed" if bool_passed else "failed", "results": list_results}

# 注释策略门同时验证正向样例能通过、负向泛化注释样例会被拦截。
def _comment_policy_gate(run_root: Path) -> dict[str, Any]:
    """执行注释策略正负样例门禁检查。

    参数:
        run_root: 本轮 comment-policy 验证写入工作目录的根路径。

    返回:
        comment-policy 门禁的聚合执行结果字典。
    """

    # 选择 vector scale 规格作为注释策略门的正负样例基础输入。
    path_spec = skill_config_path("examples_dir") / "hls_vector_scale_spec.json"  # comment-policy 基础规格路径

    # 读取 comment-policy 门使用的基础规格内容。
    dict_spec = json.loads(path_spec.read_text(encoding="utf-8"))  # comment-policy 基础规格内容

    # 准备 comment-policy 门的总输出目录。
    path_output_dir = run_root / "comment-policy"  # comment-policy 输出根目录

    # path_good_dir 保存 comment-policy 正向样例的工作流输出目录。
    path_good_dir = path_output_dir / "good"  # comment-policy 正向输出目录

    # 执行正向样例工作流，生成基线 artifacts。
    dict_workflow_result = _run_static_mock_workflow(dict_spec, path_good_dir)  # HLS 工作流执行结果

    # 解析正向样例首轮尝试产出的 artifacts 目录。
    path_artifact_dir = Path(dict_workflow_result["run_dir"]) / "attempt-001" / "hls" / "artifacts"  # 正向样例产物目录

    # dict_missing_good_report 保存正向产物缺失时的 comment-policy 失败载荷。
    dict_missing_good_report = {"ok": False, "issues": [], "metrics": {}}  # 正向缺失失败报告

    # 校验正向样例产物是否满足严格注释策略要求。
    dict_good_report = _validate_static_artifacts(dict_spec, path_artifact_dir, dict_missing_good_report)  # 正向校验报告

    # 准备用于注入泛化注释的负向样例目录。
    path_bad_dir = path_output_dir / "bad"  # comment-policy 负向输出目录

    # 正向 artifacts 存在时，复制一份并注入泛化注释作为负向样例。
    if path_artifact_dir.exists():

        # 复制正向 artifacts 到负向样例目录。
        shutil.copytree(path_artifact_dir, path_bad_dir)

        # 逐个处理负向目录中的源码文件，把语义注释改写成 generic 模板。
        for path in sorted(path_bad_dir.glob("**/*")):

            # 只篡改 HLS 源文件和头文件，避免无关文件影响负向样例。
            if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".cc", ".cxx"}:

                # 跳过非 HLS 源码类文件。
                continue

            # source_text 保存待篡改的生成源码文本。
            source_text = path.read_text(encoding="utf-8")  # comment-policy 负向样本文本

            # 把源码中的语义注释统一替换成泛化模板，构造负向样例。
            str_rewritten_text = re.sub(  # 已注入 generic 注释的负向样本文本
                r"//.*$",  # 要被替换的原始行注释模式
                "// generic generated line, not hardware intent",  # 注入到负向样例里的泛化注释文本
                source_text,  # 待改写的原始源码文本
                flags=re.MULTILINE,  # 按多行模式替换整份源码
            )

            # 写回负向样例文件，供静态产物门验证拦截。
            path.write_text(str_rewritten_text, encoding="utf-8")

    # dict_missing_bad_report 保存负向目录不存在时的保守通过载荷。
    dict_missing_bad_report = {"ok": True, "issues": [], "metrics": {}}  # 负向缺失兜底报告

    # 校验负向样例目录是否如预期触发注释策略阻断。
    dict_bad_report = _validate_static_artifacts(dict_spec, path_bad_dir, dict_missing_bad_report)  # 负向校验报告

    # 汇总负向样例触发的问题消息，便于检查 generic 注释诊断是否生效。
    str_bad_messages = "\n".join(str(issue.get("message", "")) for issue in dict_bad_report.get("issues", []))  # 负向样例问题消息文本

    # 提取负向样例命中的规则编号集合。
    set_bad_rule_ids = {str(issue.get("rule_id", "")) for issue in dict_bad_report.get("issues", [])}  # 负向样例命中规则编号集合

    # str_comment_policy 保存正向校验报告中的注释策略名。
    str_comment_policy = dict_good_report.get("metrics", {}).get("comment_policy", {}).get("policy")  # 注释策略名称

    # bool_bad_report_detected 保存负向样本是否触发预期泛化注释诊断。
    bool_bad_report_detected = "generic" in str_bad_messages.lower() or "HG003" in set_bad_rule_ids  # 负向诊断命中状态

    # 同时满足正向通过、策略名正确、负向失败且命中预期规则时才视为通过。
    bool_passed = (  # comment-policy 正负样例整体通过标记
        dict_workflow_result.get("status") == "passed"  # 正向 workflow 必须成功
        and bool(dict_good_report.get("ok"))  # 正向静态产物必须通过校验
        and str_comment_policy == "strict_chinese_hls_comment_spacing"  # 策略名必须匹配严格中文注释策略
        and not bool(dict_bad_report.get("ok"))  # 负向样例必须被校验器拦截
        and bool_bad_report_detected  # 负向报告里必须出现预期 generic 诊断
    )

    # 返回 comment-policy 门禁的聚合执行结果。
    return {
        "status": "passed" if bool_passed else "failed",
        "good_workflow_status": dict_workflow_result.get("status"),
        "good_validation_ok": bool(dict_good_report.get("ok")),
        "bad_validation_ok": bool(dict_bad_report.get("ok")),
        "bad_issue_count": len(dict_bad_report.get("issues", [])),
    }

# 版权术语生成器把拆分片段重新拼接成完整敏感词列表，供扫描器统一复用。
def _copyright_terms() -> tuple[str, ...]:
    """返回版权术语扫描器要匹配的完整敏感词列表。

    参数:
        无。

    返回:
        由拆分片段重新拼接得到的完整敏感词元组。
    """

    # 返回由拆分片段拼接而成的完整版权术语集合。
    return tuple("".join(tuple_parts) for tuple_parts in COPYRIGHT_TERM_PARTS)

# 扫描排除规则统一复用于 ripgrep 与本地路径遍历，保持目录边界一致。
def _scan_exclude_globs() -> tuple[str, ...]:
    """返回文本扫描需要使用的 glob 排除规则。

    参数:
        无。

    返回:
        供 ripgrep 复用的排除 glob 元组。
    """

    # 返回与本地路径遍历保持一致的 ripgrep 排除规则元组。
    return (
        "!ref/**",
        "!.git/**",
        "!reports/**",
        "!tests/**",
        "!scripts/python/validation/confidence_local.py",
        "!scripts/python/validation/confidence_loop.py",
    )

# 日志尾部裁剪器统一限制 stdout/stderr 片段长度，避免报告载荷过大。
def _tail(text: str, *, limit: int = 4000) -> str:
    """按上限截取文本尾部，供失败和超时报告复用。

    参数:
        text: 待裁剪的完整日志文本。
        limit: 允许保留的最大尾部字符数。

    返回:
        长度不超过 `limit` 的日志尾部文本。
    """

    # 返回限制长度后的日志尾部文本。
    return text[-limit:] if len(text) > limit else text

# JSON 输出路径解析器兼容绝对路径、技能相对路径和仓库相对路径三种输入形式。
def _resolve_json_output(path_text: str) -> Path:
    """把 CLI 传入的 JSON 输出路径解析成仓库内绝对路径。

    参数:
        path_text: CLI 传入的输出路径字符串。

    返回:
        归一化后的绝对输出路径。
    """

    # 先把原始字符串包装成 `Path` 对象，作为归一化起点。
    path_output_path = Path(path_text)  # 待归一化的输出路径对象

    # 已经是绝对路径时，无需再拼接仓库根目录。
    if path_output_path.is_absolute():

        # 直接返回调用方显式给出的绝对输出路径。
        return path_output_path

    # 拆出输入路径的各层级片段，便于识别是否已包含技能根前缀。
    tuple_parts = path_output_path.parts  # 输入路径的层级片段

    # 解析技能根相对于仓库根的前缀片段，用来兼容技能相对路径输入。
    tuple_skill_prefix = tuple(SKILL_ROOT.relative_to(repo_root()).parts)  # 技能根的仓库相对前缀片段

    # 判断当前输入是否已经至少包含完整的技能根前缀长度。
    bool_has_skill_prefix = len(tuple_parts) >= len(tuple_skill_prefix)  # 技能前缀长度匹配状态

    # 把输入路径前缀折叠成小写，便于跨平台大小写无关比较。
    tuple_lower_parts = tuple(part.lower() for part in tuple_parts[: len(tuple_skill_prefix)])  # 输入路径前缀的小写片段

    # 同步准备技能根前缀的小写形式，与输入前缀做稳定比较。
    tuple_lower_skill_prefix = tuple(part.lower() for part in tuple_skill_prefix)  # 技能根前缀的小写片段

    # 输入已包含完整技能根前缀时，先裁掉这层前缀再回拼仓库根。
    if bool_has_skill_prefix and tuple_lower_parts == tuple_lower_skill_prefix:

        # 技能根后面仍有片段时，只保留后缀片段作为仓库内相对路径。
        if len(tuple_parts) > len(tuple_skill_prefix):

            # 保存裁掉技能根前缀后的仓库内相对路径。
            path_output_path = Path(*tuple_parts[len(tuple_skill_prefix) :])  # 去除技能根前缀后的输出路径

        # 只有技能根本身时，输出路径退回仓库根目录。
        else:

            # 保存空相对路径，表示输出目标就是仓库根。
            path_output_path = Path()  # 指向仓库根本身的空相对路径

    # 另一种常见输入只带技能目录名开头，也需要裁掉这一层目录名。
    elif tuple_parts and tuple_parts[0].lower() == SKILL_ROOT.name.lower():

        # 保留技能目录名之后的相对片段，统一回拼到仓库根下。
        path_output_path = Path(*tuple_parts[1:]) if len(tuple_parts) > 1 else Path()  # 去除技能目录名后的仓库内相对路径

    # 返回相对于仓库根归一化后的绝对输出路径。
    return (repo_root() / path_output_path).resolve()

# confidence_local 只覆盖公开仓库里可直接验证的本地 gate，因此单独维护轻量 CLI。
def _build_argument_parser() -> argparse.ArgumentParser:
    """构造公开仓库本地 confidence gate 的命令行参数解析器。

    参数:
        无。

    返回:
        已注册本地 gate 相关 CLI 参数的解析器。
    """

    # parser 负责声明公开仓库本地 confidence 的稳定 CLI 协议。
    parser = argparse.ArgumentParser(description="Run Erie HLS Generator public-repo local confidence gates.")  # 本地 confidence CLI 解析器

    # gate subprocess 超时用于 quick_validate 包装命令。
    parser.add_argument(
        "--gate-timeout-s",
        type=int,
        default=900,
        help="Timeout for the quick_validate command gate.",
    )

    # JSON 输出文件路径用于落盘本地 confidence 摘要。
    parser.add_argument("--json-out", help="Optional JSON output path inside the skill workspace.")

    # 调试时允许跳过 quick_validate，避免入口修复阶段递归阻断。
    parser.add_argument("--skip-quick-validate", action="store_true")

    # 返回完成参数注册的解析器。
    return parser

# 默认 run_root 固定落在技能仓库的 reports/confidence-local 下，保持生成物边界清晰。
def _create_run_root() -> Path:
    """创建本轮本地 confidence 使用的报告目录。

    参数:
        无。

    返回:
        当前运行专属的 reports/confidence-local 子目录。
    """

    # 使用 UTC 时间戳和 pid 生成稳定且不冲突的报告目录名。
    str_run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-pid{os.getpid()}"  # 本轮本地 confidence 运行标识

    # 公开仓库的本地 confidence 产物统一落在技能仓库内的 reports/confidence-local 下。
    path_run_root = SKILL_ROOT / "reports" / "confidence-local" / str_run_id  # 本轮本地 confidence 报告目录

    # 先创建目录，供样例 gate 和 forward test 后续写入。
    path_run_root.mkdir(parents=True, exist_ok=True)

    # 返回本轮报告目录路径。
    return path_run_root

# 轻量命令 gate 统一通过这里执行，保持返回结构与其他 gate 一致。
def _record_command_gate(
    dict_gates: dict[str, dict[str, Any]],
    gate_name: str,
    command: list[str],
    *,
    cwd: Path,
    timeout_s: int,
) -> None:
    """执行单个本地命令 gate 并写入 gate 汇总字典。

    参数:
        dict_gates: 本轮 gate 聚合字典。
        gate_name: 当前 gate 在结果中的键名。
        command: 需要执行的命令参数列表。
        cwd: 命令执行目录。
        timeout_s: 命令超时秒数。

    返回:
        无；结果会直接写入 ``dict_gates``。
    """

    # 调用统一命令执行包装并把结果挂到聚合字典。
    dict_gates[gate_name] = _run_command(command, cwd=cwd, timeout_s=timeout_s)

# 本地 confidence 只保留公开仓库可直接验证的 gate 集合。
def _run_local_gates(namespace_args: argparse.Namespace, path_run_root: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """执行公开仓库本地 confidence 需要的全部 gate。

    参数:
        namespace_args: 已解析的 CLI 参数。
        path_run_root: 本轮本地 confidence 的报告目录。

    返回:
        gate 结果字典，以及样例 gate 实际覆盖到的 spec 列表。
    """

    # dict_gates 汇总所有本地 confidence 子 gate 的结构化结果。
    dict_gates: dict[str, dict[str, Any]] = {}  # 本地 confidence gate 聚合结果

    # quick_validate 仍负责技能结构和关键入口的快速自检。
    if not namespace_args.skip_quick_validate:

        # 按当前公开仓库技能根路径调用 quick_validate。
        _record_command_gate(
            dict_gates,
            "quick_validate",
            [sys.executable, "scripts/python/validation/quick_validate.py", str(SKILL_ROOT)],
            cwd=SKILL_ROOT,
            timeout_s=namespace_args.gate_timeout_s,
        )

    # 技能依赖一致性 gate 检查 runtime_config 和当前安装态是否漂移。
    dict_gates["skill_dependencies"] = _skill_dependency_gate()

    # 版权术语扫描只覆盖当前技能根目录，不扩展到工作区外部。
    dict_gates["copyright_term_scan"] = _copyright_term_scan(root=SKILL_ROOT)

    # 发布敏感扫描先只检查技能源码根；release zip 会在正式重建后单独复验。
    dict_gates["release_sensitivity_scan"] = _release_sensitivity_scan(root=SKILL_ROOT)

    # 禁用引用名称扫描覆盖技能根文本内容。
    dict_gates["forbidden_reference_names"] = _forbidden_reference_name_scan()

    # 样例 mock validation 负责验证随包 example spec 的本地生成链路。
    tuple_example_validation = _validate_examples(path_run_root)  # 样例 gate 返回的结果与 spec 覆盖列表

    # 保存样例 gate 的结构化结果。
    dict_gates["example_mock_validation"] = tuple_example_validation[0]

    # list_example_specs 记录本轮样例 gate 实际覆盖到的 spec 名称。
    list_example_specs = tuple_example_validation[1]  # 样例 mock validation 覆盖的 spec 列表

    # 注释策略 gate 覆盖 comment-only 相关的正负样例门禁。
    dict_gates["comment_policy"] = _comment_policy_gate(path_run_root)

    # forward test 覆盖一组更接近真实交付的代表性 HLS 规格。
    dict_gates["forward_test"] = _forward_test_gate(path_run_root)

    # 返回所有 gate 结果和样例覆盖列表。
    return dict_gates, list_example_specs

# gate 汇总器把结构化结果折叠成本地 confidence 最终 payload。
def _build_payload(
    dict_gates: dict[str, dict[str, Any]],
    list_example_specs: list[str],
    path_run_root: Path,
) -> tuple[dict[str, Any], int]:
    """根据本地 gate 结果生成最终 JSON payload 和退出码。

    参数:
        dict_gates: 本轮本地 confidence 的全部 gate 结果。
        list_example_specs: 样例 gate 实际覆盖到的 spec 列表。
        path_run_root: 本轮本地 confidence 报告目录。

    返回:
        最终 JSON payload，以及应返回给 shell 的退出码。
    """

    # 汇总所有非 passed gate 名称，供最终状态和残余风险同时复用。
    list_failed_gate_names = [
        str_gate_name
        for str_gate_name, dict_gate in dict_gates.items()
        if str(dict_gate.get("status") or "") != PASS_STATUS
    ]  # 未通过 gate 名称列表

    # 只有全部 gate 通过时，才给出本地高信心状态。
    str_confidence_status = "local_high_confidence" if not list_failed_gate_names else "needs_attention"  # 本地 confidence 总状态

    # 未通过 gate 会逐项展开为残余风险说明。
    list_residual_risks = [
        f"Local confidence gate did not pass: {str_gate_name}."
        for str_gate_name in list_failed_gate_names
    ]  # 本地 confidence 残余风险列表

    # 组装对 CLI 和 JSON 落盘都稳定的最终 payload。
    dict_payload = {
        "version": 1,
        "confidence_status": str_confidence_status,
        "confidence_scope": "local",
        "run_root": str(path_run_root),
        "gates": dict_gates,
        "example_specs": list_example_specs,
        "residual_risks": list_residual_risks,
    }  # 本地 confidence 最终 JSON 载荷

    # 返回 payload 和对应退出码。
    return dict_payload, 0 if not list_failed_gate_names else 1

# JSON 输出路径需要保持在技能工作区的生成目录边界内。
def _write_payload_outputs(dict_payload: dict[str, Any], json_out: str | None) -> None:
    """输出本地 confidence payload，并按需写入 JSON 文件。

    参数:
        dict_payload: 最终本地 confidence JSON 载荷。
        json_out: 可选 JSON 输出路径。

    返回:
        无。
    """

    # 调用方显式要求写盘时，先解析并创建目标路径。
    if json_out:

        # workspace 输出路径只允许落在当前技能仓库允许的生成目录里。
        path_output = require_configured_output_path(Path(json_out), purpose="local confidence json output")  # 本地 confidence JSON 输出路径

        # 先创建父目录，保证后续 JSON 写入成功。
        path_output.parent.mkdir(parents=True, exist_ok=True)

        # 把结构化 payload 以稳定格式写入文件。
        path_output.write_text(
            json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # stdout 保持单个 JSON 文档协议，便于后续自动化消费。
    sys.stdout.write(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n")

# main 执行公开仓库本地 confidence 的全部可用 gate，并输出 JSON 结果。
def main(argv: list[str] | None = None) -> int:
    """执行公开仓库本地 confidence gate。

    参数:
        argv: 可选 CLI 参数列表；为空时使用当前进程参数。

    返回:
        本地 confidence 的进程退出码。
    """

    # 先解析 CLI 参数，冻结本轮 gate 的执行配置。
    namespace_args = _build_argument_parser().parse_args(argv)  # 当前本地 confidence CLI 参数

    # 为本轮 gate 创建独立报告目录，避免多次运行互相覆盖。
    path_run_root = _create_run_root()  # 当前本地 confidence 报告目录

    # 执行全部公开仓库可用的本地 gate。
    tuple_gate_result = _run_local_gates(namespace_args, path_run_root)  # gate 结果与样例覆盖列表

    # 拆出 gate 聚合结果，供 payload 汇总复用。
    dict_gates = tuple_gate_result[0]  # 本地 confidence gate 聚合结果

    # 拆出样例覆盖列表，写入最终 payload。
    list_example_specs = tuple_gate_result[1]  # 样例 gate 实际覆盖到的 spec 列表

    # 组装最终 payload 和退出码。
    tuple_payload_result = _build_payload(dict_gates, list_example_specs, path_run_root)  # 最终 payload 与退出码

    # 提取最终 JSON 载荷，供 stdout 和可选文件写入。
    dict_payload = tuple_payload_result[0]  # 本地 confidence 最终 JSON 载荷

    # 提取最终 CLI 退出码。
    int_returncode = tuple_payload_result[1]  # 本地 confidence 最终退出码

    # 输出最终 payload，并按需落盘 JSON 文件。
    _write_payload_outputs(dict_payload, namespace_args.json_out)

    # 返回给 shell 的最终退出码。
    return int_returncode

# 脚本入口把 main 返回值转换成进程退出码，供 CLI 和自动化直接消费。
if __name__ == "__main__":

    # 按主流程返回码结束当前 CLI 进程。
    raise SystemExit(main())
