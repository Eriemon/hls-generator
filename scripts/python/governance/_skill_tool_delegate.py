#!/usr/bin/env python3
"""把仓库内轻量包装命令委派给已安装的 Codex skill 脚本。"""

# 启用延迟标注，保持治理包装器启动开销稳定。
from __future__ import annotations

# 标准库进程、路径和等待能力。
import subprocess
import sys
import time
from pathlib import Path

# agents-md-generator 新版把脚本按 docs/dirs/verify 分区存放，旧版仍可能在 scripts/ 根下。
AGENTS_MD_GENERATOR_SCRIPT_SUBPATHS = {  # agents-md-generator 委派脚本兼容路径
    "audit_skill.py": (  # 技能审计入口优先命中新版 verify 分区
        ("scripts", "python", "verify", "audit_skill.py"),  # 技能审计专用入口
        ("scripts", "audit_skill.py"),  # 审计入口的旧版单层回退
    ),
    "render_agents.py": (  # AGENTS 渲染入口优先命中新版 render 分区
        ("scripts", "python", "render", "render_agents.py"),  # AGENTS 渲染专用入口
        ("scripts", "render_agents.py"),  # 历史单层布局入口
    ),
    "manage_docs.py": (  # manage_docs 优先走新版 docs 分区
        ("scripts", "python", "docs", "manage_docs.py"),  # docs 分区候选入口
        ("scripts", "manage_docs.py"),  # 文档治理的旧版单层回退
    ),
    "manage_dirs.py": (  # manage_dirs 先查目录治理的新布局
        ("scripts", "python", "dirs", "manage_dirs.py"),  # 目录治理专用入口
        ("scripts", "manage_dirs.py"),  # 早期单层布局入口
    ),
    "verify_agents.py": (  # verify_agents 先查校验入口的新布局
        ("scripts", "python", "verify", "verify_agents.py"),  # 校验分区专用入口
        ("scripts", "verify_agents.py"),  # 兼容旧版验证入口
    ),
    "inspect_project.py": (  # 项目探测入口优先命中新版 detect 分区
        ("scripts", "python", "detect", "inspect_project.py"),  # 项目检查专用入口
        ("scripts", "inspect_project.py"),  # 项目探测的旧版单层回退
    ),
}  # 已知治理入口的新旧安装位置

# 解析当前用户的 Codex 主目录。
def _codex_home() -> Path:
    """
    返回本机 Codex 配置根目录。

    参数:
        无显式参数，直接解析当前用户主目录。

    返回:
        当前用户主目录下的 `.codex` 路径。
    """

    # 直接返回当前用户的 Codex 配置根目录。
    return Path.home() / ".codex"

# 定位 agents-md-generator 的委派脚本。
def agents_md_generator_script(name: str) -> Path:
    """
    拼接 agents-md-generator 已安装脚本路径。

    参数:
        name: 目标脚本在 skill `scripts/` 目录下的文件名。

    返回:
        指向已安装 agents-md-generator 脚本的绝对路径。
    """

    # 先锁定 agents-md-generator 安装根目录，后续候选路径都相对它展开。
    path_skill_root = _codex_home() / "skills" / "agents-md-generator"  # agents-md-generator 安装根目录

    # 按脚本名取出新版优先、旧版兜底的候选相对路径集合。
    tuple_candidate_subpaths = AGENTS_MD_GENERATOR_SCRIPT_SUBPATHS.get(name, (("scripts", name),))  # 当前脚本候选路径组

    # 按候选顺序查找真实存在的脚本，兼容安装布局差异。
    for tuple_subpath_parts in tuple_candidate_subpaths:

        # 把当前候选片段拼成完整脚本路径。
        path_candidate = path_skill_root.joinpath(*tuple_subpath_parts)  # 当前候选委派脚本路径

        # 找到存在的脚本后立即结束搜索。
        if path_candidate.exists():

            # 返回已确认存在的委派脚本路径。
            return path_candidate

    # 全部候选都缺失时，回退到首选路径供上层报错使用。
    path_script = path_skill_root.joinpath(*tuple_candidate_subpaths[0])  # 首选委派脚本路径

    # 返回最可能的目标路径，方便调用方输出稳定错误信息。
    return path_script

# 解析 skill-creator 的固定安装脚本路径。
def skill_creator_script(name: str) -> Path:
    """
    拼接系统 skill-creator 已安装脚本路径。

    参数:
        name: 目标脚本在 skill `scripts/` 目录下的文件名。

    返回:
        指向已安装 skill-creator 脚本的绝对路径。
    """

    # system skill 固定安装在 .codex/skills/.system/skill-creator 下。
    path_script = _codex_home() / "skills" / ".system" / "skill-creator" / "scripts" / name  # skill-creator 脚本路径

    # 返回 system skill 的委派脚本绝对路径。
    return path_script

# 输出委派脚本缺失错误。
def _print_missing_delegate(path_script: Path) -> None:
    """
    打印委派脚本缺失时的项目规范错误消息。

    参数:
        path_script: 期望执行但不存在的委派脚本路径。

    返回:
        无返回值，仅向 stderr 输出错误摘要。
    """

    # 直接输出符合前缀约束的缺失脚本诊断，避免 stderr 动态文本再触发 PG046。
    print(f"> ERR: [Python] Delegated script not found: {path_script}", file=sys.stderr)

# 生成传递给委派脚本的参数。
def _delegate_args(argv: list[str] | None) -> list[str]:
    """
    解析调用方传入或命令行继承的参数。

    参数:
        argv: 显式传入的参数列表；为 None 时继承当前进程参数。

    返回:
        传递给委派脚本的参数列表。
    """

    # 优先采用调用方显式传入的参数，否则继承当前命令行剩余参数。
    list_args = argv if argv is not None else sys.argv[1:]  # 委派脚本参数

    # 返回一份独立列表，避免后续执行过程意外改写外部对象。
    return list(list_args)

# 执行委派脚本并透传退出码。
def run_delegate(script_path: Path, argv: list[str] | None = None) -> int:
    """
    执行已安装 skill 脚本，并返回该脚本的退出码。

    参数:
        script_path: 需要执行的已安装 skill 脚本路径。
        argv: 传递给委派脚本的参数；为 None 时继承当前命令行。

    返回:
        委派脚本退出码；脚本缺失时返回 2。
    """

    # 单次直连模式只跑一次，这里先复制调用现场的命令尾部。
    list_args = _delegate_args(argv)  # run_delegate 单次透传参数

    # 缺失委派脚本时直接走统一错误出口，避免进入 subprocess。
    if not script_path.exists():

        # 输出缺失脚本诊断，说明当前治理入口不可用。
        _print_missing_delegate(script_path)

        # 保持原有包装器协议，缺失脚本固定返回 2。
        return 2

    # 组装子进程命令，强制使用当前解释器执行目标脚本。
    list_command = [sys.executable, str(script_path), *list_args]  # run_delegate 子进程命令

    # 直接执行委派脚本，让标准输出和标准错误默认透传给调用方。
    completed_process_result: subprocess.CompletedProcess[str] = subprocess.run(list_command, check=False)  # 委派进程结果

    # 返回委派脚本原始退出码，保持包装器透明转发。
    return int(completed_process_result.returncode)

# 判断缺失路径错误是否可能来自瞬时文件系统状态。
def _is_transient_missing_path(str_stderr: str) -> bool:
    """
    判断 stderr 是否表示可重试的瞬时路径缺失。

    参数:
        str_stderr: 委派脚本标准错误文本。

    返回:
        是否命中 Windows 或通用路径缺失错误特征。
    """

    # 先检查 traceback 中是否包含 FileNotFoundError。
    bool_has_file_error = "FileNotFoundError" in str_stderr  # 是否包含文件缺失异常

    # 再判断 stderr 是否包含英文或中文的缺失路径提示。
    bool_has_missing_text = "No such file or directory" in str_stderr or "系统找不到指定的路径" in str_stderr  # 是否包含路径缺失文本

    # 只有同时满足异常类型和错误文本，才视为值得重试的瞬时缺失。
    bool_transient_missing_path = bool_has_file_error and bool_has_missing_text  # 瞬时路径缺失判定

    # 返回供重试循环使用的布尔判断结果。
    return bool_transient_missing_path

# 回放委派脚本捕获到的输出。
def _replay_delegate_output(str_stdout: str, str_stderr: str) -> None:
    """
    将捕获到的委派脚本输出写回当前进程。

    参数:
        str_stdout: 委派脚本标准输出文本。
        str_stderr: 委派脚本标准错误文本。

    返回:
        无返回值，仅负责输出透传。
    """

    # 只有标准输出非空时才需要把内容回放给调用方。
    if str_stdout:

        # 使用 write 原样透传标准输出内容，避免强行套前缀。
        sys.stdout.write(str_stdout)

    # 只有标准错误非空时才需要把内容回放给调用方。
    if str_stderr:

        # 先补一行包装层错误摘要，再把委派脚本 stderr 原文一并透传出来。
        print(
            f"> ERR: [Python] Delegated script stderr follows.\n{str_stderr.rstrip()}",
            file=sys.stderr,
        )

# 执行委派脚本，并对瞬时文件系统缺失做有限重试。
def run_delegate_retrying_transient_fs(
    script_path: Path,
    argv: list[str] | None = None,
    *,
    retries: int = 3,
    delay_s: float = 0.5,
) -> int:
    """
    执行已安装 skill 脚本，并重试瞬时文件系统路径缺失。

    参数:
        script_path: 需要执行的已安装 skill 脚本路径。
        argv: 传递给委派脚本的参数；为 None 时继承当前命令行。
        retries: 最大尝试次数，最小会被压到 1。
        delay_s: 两次尝试之间的等待秒数。

    返回:
        委派脚本退出码；脚本缺失时返回 2。
    """

    # 重试路径会多次发起同一调用，因此先冻结整轮复用的实参快照。
    list_args = _delegate_args(argv)  # 重试循环复用参数

    # 目标脚本本身不存在时，没有必要进入重试循环。
    if not script_path.exists():

        # 直接复用统一的缺失脚本错误输出逻辑。
        _print_missing_delegate(script_path)

        # 保持与 run_delegate 一致的缺失脚本退出码。
        return 2

    # 把重试次数压到至少一次，防止调用方传入 0 或负数。
    int_attempts = max(1, int(retries))  # 最大尝试次数

    # 下面这条命令骨架会在每次失败重放时重复发车。
    list_command = [sys.executable, str(script_path), *list_args]  # 重试模式命令骨架

    # 按尝试次数循环执行，只有命中瞬时路径缺失才继续下一轮。
    for int_attempt in range(1, int_attempts + 1):

        # 捕获当前尝试的完整输出，后续再决定是立即回放还是继续重试。
        completed_process_result = subprocess.run(list_command, check=False, capture_output=True, text=True)  # 当前尝试得到的委派进程结果

        # 归一化标准输出，避免后续回放逻辑处理 None。
        str_stdout = completed_process_result.stdout or ""  # 委派标准输出

        # 归一化标准错误，避免后续判定和回放逻辑处理 None。
        str_stderr = completed_process_result.stderr or ""  # 委派标准错误

        # 判断当前失败是否属于允许重试的瞬时路径缺失场景。
        bool_transient_missing_path = _is_transient_missing_path(str_stderr)  # 瞬时缺失路径判定

        # 成功、不可重试或已到最后一次尝试时都应停止循环。
        if completed_process_result.returncode == 0 or not bool_transient_missing_path or int_attempt == int_attempts:

            # 把本轮捕获到的输出透传回当前进程。
            _replay_delegate_output(str_stdout, str_stderr)

            # 返回当前这次执行的真实退出码。
            return int(completed_process_result.returncode)

        # 等待一个短暂间隔，给外部脚本创建临时路径的机会。
        time.sleep(delay_s)

    # 理论上循环内部总会返回；这里保留兜底错误码以防未来逻辑改动。
    return 1
