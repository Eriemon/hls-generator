#!/usr/bin/env python3
"""运行 Erie HLS Generator 可重复的本地与远端 confidence gate。

机器可读 stdout 协议:
    stdout_protocol: json
"""

# future annotations 保持 CLI 编排脚本的类型注解轻量。
from __future__ import annotations

# 标准库导入覆盖 CLI、时间戳、JSON、文件系统和进程入口。
import argparse
import datetime as dt
import json
import os
import sys
from functools import partial
from pathlib import Path
from typing import Any, Callable

# 记录当前脚本所在目录，供 helper 模块导入路径注入复用。
MODULE_DIR = Path(__file__).resolve().parent  # 当前脚本模块目录

# 记录当前技能根目录，供 scripts.python 和 validation 模块导入复用。
SKILL_ROOT = Path(__file__).resolve().parents[3]  # 当前技能根目录路径

# 把给定目录提前放进 sys.path，确保按文件执行时也能解析同仓库模块。
def _prepend_import_path(path_entry: Path) -> None:
    """把指定路径前插到 Python 模块搜索路径中。

    参数:
        path_entry: 当前要插入 `sys.path` 前端的目录路径。

    返回:
        无。
    """

    # str_path_entry 保存待加入 Python 模块搜索路径的绝对路径。
    str_path_entry = str(path_entry)  # 模块搜索路径文本

    # 该分支用于区分 confidence loop 的执行路径。
    if str_path_entry not in sys.path:

        # 该调用推进当前 confidence gate 的副作用步骤。
        sys.path.insert(0, str_path_entry)

# _path_arg 保持命令里的 repo 相对路径使用稳定的正斜杠文本。
def _path_arg(path_entry: Path) -> str:
    """把 Path 转成命令参数里稳定的 POSIX 斜杠文本。

    参数:
        path_entry: 要写入命令参数的路径对象。

    返回:
        适合 subprocess argv 的正斜杠路径文本。
    """

    # as_posix 保持跨平台命令契约稳定，避免 Windows 反斜杠扰动测试与报告。
    return path_entry.as_posix()

# 本脚本以文件路径方式运行时，需要先暴露同目录 helper 模块。
_prepend_import_path(MODULE_DIR)

# scripts.python 包位于技能根目录，confidence_local 导入前必须可见。
_prepend_import_path(SKILL_ROOT)

# 本地 confidence 模块提供可 monkeypatch 的门禁包装函数。
import confidence_local as _cl

# 远端 confidence 模块提供 Vitis 和 board acceptance 执行入口。
import confidence_remote as _cr

# 报告模块负责最终 confidence 状态和剩余风险判定。
import confidence_report as _cp

# runtime 版本号用于定位 dist 发布产物和报告版本上下文。
from scripts.python.config.version import __version__

# PASS_STATUS 统一表示 confidence 子门禁通过状态。
PASS_STATUS = "passed"  # 质量门通过状态文本

# 所有远端验收都复用同一个 skill 内脚本入口。
REMOTE_VITIS_ACCEPTANCE_SCRIPT = Path("scripts") / "python" / "remote" / "remote_vitis_acceptance.py"  # 远端验收脚本路径

# smoke gate 固定走技能测试目录下的 smoke 入口。
SMOKE_GATE_SCRIPT = Path("tests") / "smoke" / "run_smoke.py"  # 本地 smoke 脚本路径

# quick_validate 负责技能目录结构和关键资源快速自检。
QUICK_VALIDATE_SCRIPT = Path("scripts") / "python" / "validation" / "quick_validate.py"  # quick_validate 脚本路径

# verify_agents 负责 AGENTS 指令治理验证。
VERIFY_AGENTS_SCRIPT = Path("scripts") / "python" / "governance" / "verify_agents.py"  # AGENTS 治理脚本路径

# manage_docs verify 负责 handoff、memory 和文档治理验证。
MANAGE_DOCS_SCRIPT = Path("scripts") / "python" / "governance" / "manage_docs.py"  # 文档治理脚本路径

# manage_dirs verify 负责目录契约治理验证。
MANAGE_DIRS_SCRIPT = Path("scripts") / "python" / "governance" / "manage_dirs.py"  # 目录治理脚本路径

# 这些错误片段表示 SSH 或远端命令出现了可重试的瞬时失败。
TRANSIENT_REMOTE_FAILURE_MARKERS = (  # 瞬时远端失败关键字
    "ssh: connect to host",  # SSH 建连失败片段
    "unknown error",  # 通用未知瞬时错误片段
    "connection reset by peer",  # 对端主动重置连接片段
    "connection closed by remote host",  # 对端主动关闭连接片段
    "broken pipe",  # 写入已断开连接片段
)

# func_orig_iter_scan_paths 保留 confidence_local 的原始扫描函数，避免 monkeypatch 后递归。
func_orig_iter_scan_paths: Callable[..., Any] = _cl._iter_scan_paths  # 原始扫描路径生成函数

# func_orig_tier1_board_matrix_path 保留 confidence_local 的原始矩阵路径解析函数。
func_orig_tier1_board_matrix_path: Callable[..., Any] = _cl._tier1_board_matrix_path  # 原始 tier1 矩阵路径函数

# func_orig_residual_risks 保留 confidence_report 的原始风险汇总函数，避免回写后递归。
func_orig_residual_risks: Callable[..., Any] = _cp._residual_risks  # 原始剩余风险汇总函数

# _iter_scan_paths 保留测试可 patch 的扫描路径入口。
def _iter_scan_paths(root: Path) -> Any:
    """代理 `confidence_local` 的扫描路径枚举入口。

    参数:
        root: 需要扫描的文件或目录根路径。

    返回:
        `confidence_local` 生成的路径迭代对象。
    """

    # 返回原始扫描函数的结果，避免 wrapper 被写回 _cl 后自递归。
    return func_orig_iter_scan_paths(root)

# _tier1_board_matrix_path 保留测试可 patch 的 tier1 矩阵路径入口。
def _tier1_board_matrix_path() -> Path:
    """返回 tier1 板卡覆盖矩阵 JSON 的路径。

    参数:
        无。

    返回:
        `tier1_board_coverage_matrix.json` 的路径。
    """

    # 返回 confidence_local 原始矩阵路径函数的结果，避免 wrapper 回写后自递归。
    return func_orig_tier1_board_matrix_path()

# _run_command 保留测试可 patch 的本地命令执行入口。
def _run_command(command: list[str], *, cwd: Path, timeout_s: int) -> dict[str, Any]:
    """运行本地 confidence gate 命令。

    参数:
        command: 需要执行的命令参数列表。
        cwd: 命令执行目录。
        timeout_s: 命令超时秒数。

    返回:
        `confidence_local` 返回的结构化命令结果。
    """

    # 返回底层本地命令执行包装结果。
    return _cl._run_command(command, cwd=cwd, timeout_s=timeout_s)

# _skill_dependency_gate 保留技能依赖门禁入口。
def _skill_dependency_gate() -> dict[str, Any]:
    """执行技能依赖一致性检查。

    参数:
        无。

    返回:
        技能依赖 gate 的结构化结果。
    """

    # 返回 confidence_local 的依赖检查结果。
    return _cl._skill_dependency_gate()

# _forbidden_reference_name_scan 保留禁止引用名称扫描入口。
def _forbidden_reference_name_scan() -> dict[str, Any]:
    """扫描技能内容中的禁止引用名称。

    参数:
        无。

    返回:
        禁止引用名称扫描结果。
    """

    # 返回 confidence_local 的禁止引用扫描结果。
    return _cl._forbidden_reference_name_scan()

# _example_spec_names 保留 example spec 枚举入口。
def _example_spec_names() -> list[str]:
    """枚举随技能发布的 example spec 名称。

    参数:
        无。

    返回:
        example spec 文件名列表。
    """

    # 返回 confidence_local 汇总的样例 spec 名称。
    return _cl._example_spec_names()

# _validate_examples 保留示例 mock validation 入口。
def _validate_examples(run_root: Path) -> tuple[dict[str, Any], list[str]]:
    """运行 example spec 的 mock validation。

    参数:
        run_root: 本轮 confidence loop 的报告目录。

    返回:
        示例验证 gate 结果和参与验证的 spec 名称列表。
    """

    # 返回 confidence_local 的示例验证结果。
    return _cl._validate_examples(run_root)

# _comment_policy_gate 保留注释策略 gate 入口。
def _comment_policy_gate(run_root: Path) -> dict[str, Any]:
    """执行技能注释策略检查。

    参数:
        run_root: 本轮 confidence loop 的报告目录。

    返回:
        注释策略 gate 的结构化结果。
    """

    # 返回 confidence_local 的注释策略检查结果。
    return _cl._comment_policy_gate(run_root)

# _forward_test_gate 保留 forward test gate 入口。
def _forward_test_gate(run_root: Path) -> dict[str, Any]:
    """执行技能 forward test gate。

    参数:
        run_root: 本轮 confidence loop 的报告目录。

    返回:
        forward test gate 的结构化结果。
    """

    # 返回 confidence_local 的 forward test 结果。
    return _cl._forward_test_gate(run_root)

# _resolve_json_output 保留 JSON 输出路径解析入口。
def _resolve_json_output(path_text: str) -> Path:
    """解析 confidence loop JSON 输出路径。

    参数:
        path_text: CLI 传入的 JSON 输出路径文本。

    返回:
        治理允许的绝对输出路径。
    """

    # 返回 confidence_local 的安全输出路径解析结果。
    return _cl._resolve_json_output(path_text)

# _tail 保留日志尾部截断入口。
def _tail(text: str, *, limit: int = 4000) -> str:
    """截取长日志尾部，避免 gate payload 过大。

    参数:
        text: 原始 stdout 或 stderr 文本。
        limit: 最多保留的字符数。

    返回:
        截断后的日志尾部文本。
    """

    # 返回 confidence_local 的日志尾部截断结果。
    return _cl._tail(text, limit=limit)

# _route_contract_gate 保留远端路由契约 gate 入口。
def _route_contract_gate(
    server: str | None,
    build_server: str | None,
    validate_server: str | None,
    *,
    remote_requested: bool,
) -> dict[str, Any]:
    """检查远端服务器选择是否符合 AGENTS 路由契约。

    参数:
        server: 单服务器远端目标。
        build_server: split 拓扑构建服务器。
        validate_server: split 拓扑验证服务器。
        remote_requested: 是否请求真实远端验收。

    返回:
        远端路由契约 gate 的结构化结果。
    """

    # 返回 confidence_remote 的路由契约检查结果。
    return _cr._route_contract_gate(
        server,
        build_server,
        validate_server,
        remote_requested=remote_requested,
    )

# _board_acceptance_partition_gate 保留 board 声明分组 gate 入口。
def _board_acceptance_partition_gate() -> dict[str, Any]:
    """读取并校验 example spec 的 board acceptance 声明分组。

    参数:
        无。

    返回:
        `board_specs`、`exempt_specs` 和 `invalid_specs` 分组结果。
    """

    # 返回 confidence_remote 的 board acceptance 分组结果。
    return _cr._board_acceptance_partition_gate()

# _remote_directory_contract_gate 保留远端目录契约 gate 入口。
def _remote_directory_contract_gate(
    remote_results: list[dict[str, Any]],
    *,
    remote_requested: bool,
) -> dict[str, Any]:
    """
    校验远端 runs/backups 目录和归档契约。

    :param remote_results: 远端 Vitis acceptance 的执行结果集合。
    :param remote_requested: 是否请求真实远端验收。
    :return: 远端目录契约 gate 的结构化结果。
    """

    # 返回 confidence_remote 的远端目录契约检查结果。
    return _cr._remote_directory_contract_gate(remote_results, remote_requested=remote_requested)

# _residual_risks 保留最终 confidence 剩余风险入口。
def _residual_risks(
    confidence_status: str,
    *,
    remote_requested: bool,
    remote_skipped: bool,
    gates: dict[str, dict[str, Any]],
) -> list[str]:
    """
    汇总最终 confidence 报告中的剩余风险。

    :param confidence_status: 最终 confidence 状态。
    :param remote_requested: 是否请求真实远端验收。
    :param remote_skipped: 是否通过 CLI 显式跳过远端。
    :param gates: 已执行的全部 gate 结果。
    :return: 剩余风险说明列表。
    """

    # 返回 confidence_report 原始函数的剩余风险计算结果。
    return func_orig_residual_risks(
        confidence_status,
        remote_requested=remote_requested,
        remote_skipped=remote_skipped,
        gates=gates,
    )

# 把 confidence_local 的版权术语扫描切到当前模块可 patch 的依赖入口。
def _copyright_term_scan(*, root: Path | None = None) -> dict[str, Any]:
    """运行带可 patch 依赖入口的版权术语扫描。

    参数:
        root: 可选的扫描根目录；省略时使用 confidence_local 默认根目录。

    返回:
        版权术语扫描的结构化结果。
    """

    # 把扫描路径枚举入口切换到当前模块的 wrapper。
    _cl._iter_scan_paths = _iter_scan_paths  # 可 patch 的扫描路径入口

    # 把仓库根解析入口切换到当前模块的 wrapper。
    _cl.repo_root = repo_root  # 可 patch 的仓库根解析入口

    # 返回版权术语扫描的结构化结果。
    return _cl._copyright_term_scan(root=root)

# 发布敏感扫描需要在显式 root 模式下临时替换路径枚举器，避免扫到 dist 之外内容。
def _release_sensitivity_scan(*, root: Path | None = None) -> dict[str, Any]:
    """运行带可 patch 依赖入口的发布敏感扫描。

    参数:
        root: 可选的扫描根目录；传入时只扫描该目录。

    返回:
        发布敏感扫描的结构化结果。
    """

    # 显式传入 root 时，局部重写路径枚举器以限制扫描边界。
    if root is not None:

        # 局部扫描器只枚举显式 root 下的目录和文件。
        def _scan_paths(scan_root: Path):
            """枚举显式扫描根下的目录与文件路径。

            参数:
                scan_root: 当前要展开的显式扫描根目录。

            返回:
                逐项产出显式扫描根下的目录与文件路径。
            """

            # 先产出显式扫描根本身，便于调用方统一处理根目录级检查。
            yield scan_root

            # 再按目录树顺序枚举显式扫描根下的子目录和文件。
            for current_root, list_dirnames, list_filenames in os.walk(
                scan_root,
                topdown=True,
                onerror=lambda _exc: None,
            ):

                # 把 os.walk 返回的当前目录字符串转成 Path 对象。
                path_current_root = Path(current_root)  # 当前遍历目录路径

                # 原地剔除缓存和生成目录，避免无关内容进入发布敏感扫描。
                list_dirnames[:] = [name for name in list_dirnames if name not in _cl.SKIP_SCAN_DIRS]  # 待递归目录名称

                # 逐个产出当前目录下仍需递归扫描的子目录。
                for dirname in list_dirnames:

                    # 返回当前子目录路径，供发布敏感扫描继续处理。
                    yield path_current_root / dirname

                # 逐个产出当前目录下需要检查的文件路径。
                for filename in list_filenames:
                    yield path_current_root / filename

        # 把路径枚举入口切换到显式 root 限定版扫描器。
        _cl._iter_scan_paths = _scan_paths  # 显式 root 限定扫描器

    # 未显式指定 root 时，恢复 confidence_local 的原始扫描器行为。
    else:

        # 把路径枚举入口恢复成 confidence_local 的原始实现。
        _cl._iter_scan_paths = func_orig_iter_scan_paths  # confidence_local 原始扫描器

    # 发布敏感扫描后续会通过 repo_root 反查当前工作区根目录。
    _cl.repo_root = repo_root  # 发布敏感扫描仓库根解析入口

    # 继续复用 confidence_local 的发布敏感扫描实现。
    return _cl._release_sensitivity_scan(root=root)

# tier1 覆盖模式依赖板卡矩阵路径，因此先接入当前模块的可 patch 路径入口。
def _remote_default_example_specs(coverage_mode: str) -> list[str]:
    """返回远端覆盖模式对应的默认示例规格列表。

    参数:
        coverage_mode: 当前远端验收采用的覆盖模式名称。

    返回:
        该覆盖模式对应的默认示例规格文件名列表。
    """

    # 先记住显式规格解析进入前的矩阵入口，后面需要把 `_cl` 还原给其他远端阶段复用。
    func_previous_tier1_board_matrix_path = _cl._tier1_board_matrix_path  # 显式规格解析前的矩阵入口

    # 进入临时接管矩阵路径入口的局部作用域。
    try:

        # 把 tier1 板卡矩阵路径解析入口切到当前模块的 wrapper。
        _cl._tier1_board_matrix_path = _tier1_board_matrix_path  # 可 patch 的矩阵路径入口

        # 返回远端覆盖模式对应的默认示例规格列表。
        return _cl._remote_default_example_specs(coverage_mode)

    # 无论底层解析成功还是失败，都恢复 confidence_local 原有入口。
    finally:

        # 把矩阵路径入口恢复到进入函数前的状态，避免污染共享模块状态。
        _cl._tier1_board_matrix_path = func_previous_tier1_board_matrix_path  # 恢复的矩阵路径入口

# 显式规格解析同样依赖 tier1 板卡矩阵路径，因此复用当前模块的路径入口。
def _resolve_remote_example_specs(explicit_specs: list[str] | None, coverage_mode: str) -> tuple[list[str], str]:
    """解析远端验收实际要使用的规格列表与来源标签。

    参数:
        explicit_specs: 调用方显式指定的规格文件名列表。
        coverage_mode: 未显式指定规格时采用的覆盖模式名称。

    返回:
        实际要使用的规格文件名列表，以及规格来源标签。
    """

    # 先快照 confidence_local 当前持有的矩阵路径入口，避免临时替换泄漏到后续调用。
    func_previous_tier1_board_matrix_path = _cl._tier1_board_matrix_path  # 替换前的矩阵路径入口

    # 显式规格既可能为空也可能触发 tier1 默认展开，因此这里单独包住局部路径覆盖。
    try:

        # 把局部路径钩子接到 `_cl` 上，让显式规格解析走当前脚本可 patch 的 tier1 路径。
        _cl._tier1_board_matrix_path = _tier1_board_matrix_path  # 显式规格解析期间的局部矩阵入口

        # 继续复用 confidence_local 的规格解析实现。
        return _cl._resolve_remote_example_specs(explicit_specs, coverage_mode)

    # 显式规格解析结束后必须撤回局部钩子，否则后续远端阶段会继续带着这次覆盖状态运行。
    finally:

        # 把 `_cl` 恢复到调用前的矩阵入口，保证下一次远端规格决策重新从干净状态开始。
        _cl._tier1_board_matrix_path = func_previous_tier1_board_matrix_path  # 供后续远端阶段复用的已恢复矩阵入口

# 最终 confidence 结果汇总需要接入当前模块的 PASS_STATUS 和剩余风险入口。
def _confidence_outcome(
    gates: dict[str, dict[str, Any]],
    *,
    remote_requested: bool,
    remote_skipped: bool,
) -> tuple[str, str, list[str], int]:
    """汇总最终 confidence 状态、摘要和剩余风险。

    参数:
        gates: 已执行 gate 的结构化结果映射。
        remote_requested: 是否请求真实远端验收。
        remote_skipped: 是否通过 CLI 显式跳过远端阶段。

    返回:
        最终状态、摘要、剩余风险列表和失败 gate 数量。
    """

    # 把通过状态常量接入 confidence_report 模块。
    _cp.PASS_STATUS = PASS_STATUS  # confidence_report 通过状态常量

    # 把剩余风险计算入口切到当前模块的 wrapper。
    _cp._residual_risks = _residual_risks  # 可 patch 的剩余风险入口

    # 返回 confidence_report 汇总出的最终结果。
    return _cp._confidence_outcome(gates, remote_requested=remote_requested, remote_skipped=remote_skipped)

# 远端命令 timeout 载荷抽成独立 helper，避免执行函数内部堆叠配置型字典。
def _timeout_remote_payload(
    command: list[str],
    dict_process_result: dict[str, Any],
    timeout_s: int,
) -> dict[str, Any]:
    """构造远端命令超时时的结构化结果。

    参数:
        command: 触发超时的本地远端包装命令参数列表。
        dict_process_result: `_cl._run_process` 返回的原始执行结果。
        timeout_s: 本地 subprocess 超时秒数。

    返回:
        供 confidence 聚合层直接消费的 timeout 结果字典。
    """

    # 返回保留命令、阈值与日志尾部的超时诊断载荷。
    return {
        "status": "timeout",  # 远端命令超时状态
        "command": command,  # 触发超时的命令参数列表
        "returncode": None,  # 超时时没有有效退出码
        "timeout_s": timeout_s,  # 本地 subprocess 超时阈值
        "stdout_tail": _tail(dict_process_result["stdout"]),  # 标准输出尾部摘要
        "stderr_tail": _tail(dict_process_result["stderr"]),  # 标准错误尾部摘要
    }

# 远端命令包装负责把本地 subprocess 结果统一转成 confidence 可聚合载荷。
def _run_remote_command(command: list[str], *, timeout_s: int = 900) -> dict[str, Any]:
    """执行远端命令并统一转换输出载荷。

    参数:
        command: 要执行的本地远端包装命令参数列表。
        timeout_s: 本地 subprocess 超时秒数。

    返回:
        可直接参与 confidence 聚合的结构化结果字典。
    """

    # 所有远端子命令都固定在技能根目录执行，保证相对脚本路径稳定。
    dict_process_result: dict[str, Any] = _cl._run_process(command, cwd=SKILL_ROOT, timeout_s=timeout_s)  # 远端子进程执行结果

    # 子进程超时意味着远端脚本还没产出完整 JSON 文档。
    if dict_process_result["timed_out"]:

        # 超时场景直接回传裁剪后的 stdout/stderr 证据。
        return _timeout_remote_payload(command, dict_process_result, timeout_s)

    # 远端验收脚本约定 stdout 是单个 JSON 文档。
    try:

        # 解析成功时保留远端脚本原始字段，供上层继续聚合。
        dict_payload = json.loads(dict_process_result["stdout"])  # 远端阶段 JSON 载荷

    # 非 JSON 输出通常意味着 shell 层或远端脚本已经异常退出。
    except json.JSONDecodeError:

        # 解析失败时退化成最小失败摘要，但继续保留尾部日志证据。
        dict_payload = {
            "status": "failed",  # 远端脚本未返回可解析 JSON
            "stdout_tail": _tail(dict_process_result["stdout"]),  # 标准输出尾部证据
            "stderr_tail": _tail(dict_process_result["stderr"]),  # 标准错误尾部证据
        }  # 非 JSON 远端输出摘要

    # 返回码是本地进程事实证据，不会覆盖远端业务状态。
    dict_payload["returncode"] = dict_process_result["returncode"]  # 本地子进程返回码

    # 超时阈值写入结果，便于报告重现实验设置。
    dict_payload["timeout_s"] = timeout_s  # 本次远端命令超时阈值

    # 返回标准化后的远端命令结果。
    return dict_payload

# 只把连接层抖动识别为可重试，综合失败和 board 失败不能被隐藏。
def _is_transient_remote_failure(payload: dict[str, Any]) -> bool:
    """判断远端失败是否属于可重试的瞬时故障。

    参数:
        payload: 远端命令执行后的结构化结果载荷。

    返回:
        当前失败是否命中瞬时重试条件。
    """

    # 已通过的阶段不需要进入 SSH 抖动重试判断。
    if str(payload.get("status") or "") == PASS_STATUS:

        # 通过状态说明这次远端调用已经结束。
        return False

    # 合并常见错误字段，统一匹配 SSH 连接层错误片段。
    str_failure_text = "\n".join(  # 汇总全部失败文本字段
        str(payload.get(str_role_key) or "")  # 当前错误字段文本
        for str_role_key in ("error", "message", "stdout_tail", "stderr_tail")  # 参与瞬时故障判断的字段名
    ).lower()  # 统一转成小写后匹配连接故障关键字

    # 只要命中连接抖动关键字，就允许上层做有限重试。
    return any(marker in str_failure_text for marker in TRANSIENT_REMOTE_FAILURE_MARKERS)

# 远端命令重试包装仅处理瞬时故障，不会无条件重复执行失败命令。
def _run_remote_command_with_retry(command: list[str], *, timeout_s: int = 900, retries: int = 1) -> dict[str, Any]:
    """执行远端命令，并对瞬时失败做有限次重试。

    参数:
        command: 要执行的本地远端包装命令参数列表。
        timeout_s: 本地 subprocess 超时秒数。
        retries: 瞬时失败时允许追加的重试次数。

    返回:
        最终一次执行得到的结构化结果载荷。
    """

    # 首轮先执行一次远端命令，后续是否重试取决于 SSH 层错误类型。
    dict_payload = _run_remote_command(command, timeout_s=timeout_s)  # 当前远端命令载荷

    # 重试计数从零开始，最终写回 payload 供报告侧审计。
    int_attempt = 0  # 已完成重试次数

    # 只要还有重试额度且失败属于连接抖动，就再次执行同一命令。
    while int_attempt < retries and _is_transient_remote_failure(dict_payload):

        # 递增重试序号，确保最终结果能解释重复远端调用。
        int_attempt += 1  # 当前重试序号

        # 重试使用相同命令和超时，保持验收条件一致。
        dict_payload = _run_remote_command(command, timeout_s=timeout_s)  # 重试后的远端命令载荷

        # 把实际发生的重试次数写回最终结果。
        dict_payload["retry_count"] = int_attempt  # 命中的远端重试次数

    # 返回原始执行或最后一次重试得到的标准化结果。
    return dict_payload

# 单机远端 Vitis 验收包装负责绑定 mode、readiness 和 example spec。
def _run_remote(server: str, readiness: str, spec_name: str, *, vitis_version: str | None = None) -> dict[str, Any]:
    """执行单机远端 Vitis acceptance。

    参数:
        server: 远端验收使用的目标服务器名称。
        readiness: 当前远端验收要求的深度级别。
        spec_name: 当前远端验收使用的 example spec 文件名。
        vitis_version: 可选的远端 Vitis 版本锁定值。

    返回:
        单机远端 Vitis acceptance 的结构化结果载荷。
    """

    # 远端 Vitis 命令先固定解释器与脚本入口，再补齐服务器、深度和样例参数。
    list_command = [sys.executable, _path_arg(REMOTE_VITIS_ACCEPTANCE_SCRIPT)]  # Vitis 远端脚本入口

    # 单机模式需要显式声明 Vitis 执行模式和目标服务器。
    list_command.extend(["--mode", "vitis", "--server", server])  # 单机服务器与 Vitis 执行模式

    # readiness 和 example-spec 共同决定当前要跑哪一个样例与深度。
    list_command.extend(["--readiness", readiness, "--example-spec", spec_name])  # 当前验收深度与样例名

    # 当前项目要求远端脚本返回中文注释和 JSON 机器输出。
    list_command.extend(["--comment-language", "zh", "--json"])  # 中文注释与 JSON 输出约束

    # 指定版本时，把版本选择显式传给远端脚本。
    if vitis_version:

        # 追加 Vitis 版本参数，避免远端 profile 漂移。
        list_command.extend(["--vitis-version", vitis_version])

    # 综合类远端验收使用更长超时，并允许一次连接层重试。
    dict_payload = _run_remote_command_with_retry(list_command, timeout_s=5400, retries=1)  # Vitis 样例远端载荷

    # 写回样例名，便于覆盖率和目录契约聚合。
    dict_payload["example_spec"] = spec_name  # 当前远端样例名

    # 返回当前样例的远端 Vitis 结果。
    return dict_payload

# 单机远端 pytest 包装负责把仓库级回归路由到目标服务器。
def _run_remote_pytest(server: str) -> dict[str, Any]:
    """执行单机远端 pytest 回归。

    参数:
        server: 远端 pytest 使用的目标服务器名称。

    返回:
        单机远端 pytest 的结构化结果载荷。
    """

    # 先建立远端验收脚本的基础 argv，后面只追加 pytest 路由参数。
    list_command = [sys.executable, _path_arg(REMOTE_VITIS_ACCEPTANCE_SCRIPT)]  # 远端验收脚本基础 argv

    # 单机模式固定把 pytest 路由到声明的目标服务器。
    list_command.extend(["--mode", "pytest", "--server", server, "--json"])  # 单机 pytest 路由参数

    # 远端 pytest 使用扩展超时，并允许一次连接层重试。
    return _run_remote_command_with_retry(list_command, timeout_s=5400, retries=1)

# 单机远端 smoke 包装负责在目标服务器执行 smoke 入口。
def _run_remote_smoke(server: str) -> dict[str, Any]:
    """执行单机远端 smoke 回归。

    参数:
        server: 远端 smoke 使用的目标服务器名称。

    返回:
        单机远端 smoke 的结构化结果载荷。
    """

    # smoke gate 需要同 pytest 一样绑定当前源码快照。
    list_command = [sys.executable, _path_arg(REMOTE_VITIS_ACCEPTANCE_SCRIPT)]  # 远端 smoke 脚本基础 argv

    # 单机 smoke 必须落在 AGENTS 路由解析出的服务器上。
    list_command.extend(["--mode", "smoke", "--server", server, "--json"])  # 单机 smoke argv 路由片段

    # smoke 入口内部会调度 pytest 子集，因此沿用扩展超时和一次连接层重试。
    return _run_remote_command_with_retry(list_command, timeout_s=5400, retries=1)

# 单机 board acceptance 包装在远端命令前补齐 board 模式专属配置。
def _run_remote_board(
    server: str,
    readiness: str,
    spec_name: str,
    *,
    vitis_version: str | None = None,
) -> dict[str, Any]:
    """执行单机远端 board acceptance。

    参数:
        server: 远端 board 验收使用的目标服务器名称。
        readiness: 当前 board 验收要求的前置深度级别。
        spec_name: 当前 board 验收使用的 example spec 文件名。
        vitis_version: 可选的远端 Vitis 版本锁定值。

    返回:
        单机远端 board acceptance 的结构化结果载荷。
    """

    # board 命令除了样例与服务器信息，还要显式传入上板专用超时。
    list_command = [sys.executable, _path_arg(REMOTE_VITIS_ACCEPTANCE_SCRIPT)]  # 真实上板命令入口

    # 单机 board 验收必须固定为真实上板模式并绑定目标服务器。
    list_command.extend(["--mode", "board", "--server", server])  # 单机服务器与真实上板模式

    # readiness 和 example-spec 决定当前要上板执行的样例与验收深度。
    list_command.extend(["--readiness", readiness, "--example-spec", spec_name])  # 当前上板样例与验收深度

    # board 阶段要求中文注释、5400 秒脚本自管超时以及 JSON 输出。
    list_command.extend(["--comment-language", "zh", "--timeout", "5400", "--json"])  # 上板链路输出和时限约束

    # 版本参数可选，未指定时沿用远端默认 Vitis 配置。
    if vitis_version:

        # 追加 Vitis 版本选择，确保 board 与前置 Vitis 阶段一致。
        list_command.extend(["--vitis-version", vitis_version])

    # 上板阶段同样使用长超时，并允许一次 SSH 层重试。
    dict_payload = _run_remote_command_with_retry(list_command, timeout_s=5400, retries=1)  # 上板阶段标准化执行结果

    # 写回样例名，让 board 结果可与 Vitis 覆盖率交叉匹配。
    dict_payload["example_spec"] = spec_name  # 当前 board 样例名

    # 把当前 board 样例结果交回上层聚合。
    return dict_payload

# split build/validate 拓扑会把单个样例路由到构建端和验证端两台服务器。
def _run_split_remote(
    build_server: str,
    validate_server: str,
    readiness: str,
    spec_name: str,
    *,
    vitis_version: str | None = None,
) -> dict[str, Any]:
    """执行 split build/validate 拓扑的远端 Vitis acceptance。

    参数:
        build_server: split 拓扑中的构建服务器名称。
        validate_server: split 拓扑中的验证服务器名称。
        readiness: 当前远端验收要求的深度级别。
        spec_name: 当前远端验收使用的 example spec 文件名。
        vitis_version: 可选的远端 Vitis 版本锁定值。

    返回:
        split build/validate 拓扑的结构化结果载荷。
    """

    # split 命令先固定解释器与脚本入口，再写入双机拓扑参数。
    list_command = [sys.executable, _path_arg(REMOTE_VITIS_ACCEPTANCE_SCRIPT)]  # 双机拓扑命令入口

    # split 拓扑需要同时声明 Vitis 模式、构建服务器和验证服务器。
    list_command.extend(["--mode", "vitis", "--build-server", build_server, "--validate-server", validate_server])  # 双机拓扑路由参数

    # readiness 和 example-spec 共同标识当前分离构建验证要处理的样例。
    list_command.extend(["--readiness", readiness, "--example-spec", spec_name])  # 当前 split 样例与验收深度

    # split 远端脚本仍要求中文注释和 JSON 输出，便于和单机场景统一聚合。
    list_command.extend(["--comment-language", "zh", "--json"])  # split 模式输出约束

    # 指定版本时，build 和 validate 两端都由远端脚本统一处理。
    if vitis_version:

        # 追加 Vitis 版本参数，避免 split 模式使用隐式默认版本。
        list_command.extend(["--vitis-version", vitis_version])

    # split 拓扑同样只允许一次连接层重试。
    dict_payload = _run_remote_command_with_retry(list_command, retries=1)  # 当前 split 样例远端执行结果

    # 为后续 family coverage 和最终报告补上当前 split 样例名。
    dict_payload["example_spec"] = spec_name  # split 结果归档键

    # 把当前 split 样例结果返回给调用方。
    return dict_payload

# split 远端 pytest 只在 validate 服务器执行，但要保留双机拓扑事实。
def _run_split_remote_pytest(build_server: str, validate_server: str) -> dict[str, Any]:
    """执行 split build/validate 拓扑的远端 pytest 回归。

    参数:
        build_server: split 拓扑中的构建服务器名称。
        validate_server: split 拓扑中的验证服务器名称。

    返回:
        split build/validate 拓扑的远端 pytest 结构化结果载荷。
    """

    # split pytest 继续复用同一脚本入口，但执行目标固定在 validate 服务器。
    list_command = [sys.executable, _path_arg(REMOTE_VITIS_ACCEPTANCE_SCRIPT)]  # split pytest 命令入口

    # 同时保留 split 双机路由事实，并让远端脚本在 validate 服务器执行 pytest。
    list_command.extend(  # split pytest 路由参数
        [
            "--mode",
            "pytest",
            "--build-server",
            build_server,
            "--validate-server",
            validate_server,
            "--json",
        ]
    )

    # split pytest 也允许一次连接层重试，并沿用扩展超时。
    return _run_remote_command_with_retry(list_command, timeout_s=5400, retries=1)

# split 远端 smoke 只在 validate 服务器执行，但保留 build/validate 拓扑事实。
def _run_split_remote_smoke(build_server: str, validate_server: str) -> dict[str, Any]:
    """执行 split build/validate 拓扑的远端 smoke 回归。

    参数:
        build_server: split 拓扑中的构建服务器名称。
        validate_server: split 拓扑中的验证服务器名称。

    返回:
        split build/validate 拓扑的远端 smoke 结构化结果载荷。
    """

    # split smoke 借 validate 节点提供和 pytest 相同的执行侧证据。
    list_command = [sys.executable, _path_arg(REMOTE_VITIS_ACCEPTANCE_SCRIPT)]  # split smoke 远端脚本入口命令

    # build 节点只作为拓扑事实保留，实际 smoke 命令由 validate 节点承接。
    list_command.extend(  # split smoke build/validate 路由片段
        [
            "--mode",
            "smoke",
            "--build-server",
            build_server,
            "--validate-server",
            validate_server,
            "--json",
        ]
    )

    # smoke 入口会再调度 pytest 子集，因此沿用长超时策略。
    return _run_remote_command_with_retry(list_command, timeout_s=5400, retries=1)

# _remote_result_retained 定义远端产物保留判定的统一入口。
def _remote_result_retained(dict_result: dict[str, Any]) -> bool:
    """判断远端验收结果是否同时通过并保留可审查产物。

    参数:
        dict_result: 单个远端验收阶段返回的结构化结果。

    返回:
        结果状态为 passed 且 remote_artifacts_retained 为真时返回 True。
    """

    # 先判断当前远端阶段是否明确报告通过。
    bool_passed_state = str(dict_result.get("status") or "") == PASS_STATUS  # 质量门状态判定

    # 再确认远端 run/backups 产物已按要求保留下来。
    bool_artifacts_retained = bool(dict_result.get("remote_artifacts_retained"))  # 远端产物保留状态

    # 只有状态和产物证据都满足时才允许进入最终 confidence。
    return bool_passed_state and bool_artifacts_retained

# _remote_acceptance_payload 定义单服务器 acceptance 的报告结构。
def _remote_acceptance_payload(
    *,
    status: str,
    server: str,
    vitis_version: str | None,
    dict_link_payload: dict[str, Any],
    list_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """汇总单服务器远端 Vitis acceptance 的输出载荷。

    参数:
        status: 当前远端 acceptance 聚合状态。
        server: 单服务器远端目标名称。
        vitis_version: 本次远端验收使用的 Vitis 版本。
        dict_link_payload: 远端连接预检的结构化结果。
        list_results: 每个 example spec 的 Vitis 验收结果。

    返回:
        confidence loop 可直接放入 gates 的字典。
    """

    # 返回单服务器远端验收的统一聚合结构。
    return {
        "status": status,
        "server": server,
        "vitis_version": vitis_version,
        "link": dict_link_payload,
        "results": list_results,
    }

# _run_remote_acceptance 定义 confidence loop 的一个可测试执行边界。
def _run_remote_acceptance(
    server: str,
    readiness: str,
    example_specs: list[str],
    *,
    vitis_version: str | None = None,
    parallelism: int = 1,
) -> dict[str, Any]:
    """执行单服务器远端 Vitis acceptance。

    参数:
        server: 远端验收使用的目标服务器名称。
        readiness: 当前远端验收要求的深度级别。
        example_specs: 需要依次执行的 example spec 文件名列表。
        vitis_version: 可选的远端 Vitis 版本锁定值。
        parallelism: 并行调度 example spec 的最大并发数。

    返回:
        单服务器远端 Vitis acceptance 的聚合结果。
    """

    # link 预检命令只验证 SSH、工作区和远端脚本入口，不进入样例执行阶段。
    list_link_command = [  # link 预检命令
        sys.executable,  # 调用当前 Python 解释器执行远端包装脚本
        _path_arg(REMOTE_VITIS_ACCEPTANCE_SCRIPT),  # 远端 Vitis 验收包装脚本路径
        "--mode",  # 指定远端脚本执行模式的参数名
        "link",  # 只做连通性和入口检查，不跑样例
        "--server",  # 指定目标服务器的参数名
        server,  # 当前要做 link 预检的远端服务器名
        "--timeout",  # 指定远端脚本超时秒数的参数名
        "300",  # link 预检阶段允许的最大等待秒数
        "--json",  # 要求远端脚本返回 JSON 载荷
    ]

    # 预检命令只允许一次连接层重试，避免把真实配置错误误判成瞬时故障。
    dict_link_payload = _run_remote_command_with_retry(list_link_command, retries=1)  # link 预检 JSON 回包

    # link 失败时不再继续占用远端资源跑样例验收。
    if dict_link_payload.get("status") != PASS_STATUS:

        # 返回空结果，明确说明样例阶段还没有开始。
        return _remote_acceptance_payload(
            status="failed",
            server=server,
            vitis_version=vitis_version,
            dict_link_payload=dict_link_payload,
            list_results=[],
        )

    # worker 只接收样例名，服务器、readiness 和版本由上下文预先绑定。
    func_worker = partial(_run_remote, server, readiness, vitis_version=vitis_version)  # 单样例远端 Vitis worker

    # 对已选择的样例运行远端 Vitis 验收，必要时并行。
    list_vitis_results = _cr._run_parallel_specs(example_specs, func_worker, parallelism=parallelism)  # 每个样例的 Vitis 验收结果

    # Vitis 阶段必须全部通过，并确认远端产物已被保留。
    bool_passed = all(_remote_result_retained(item) for item in list_vitis_results)  # 单机 Vitis 验收整体状态

    # 返回单服务器远端 Vitis 验收结果。
    return _remote_acceptance_payload(
        status=PASS_STATUS if bool_passed else "failed",
        server=server,
        vitis_version=vitis_version,
        dict_link_payload=dict_link_payload,
        list_results=list_vitis_results,
    )

# split 聚合结果要同时保留双机身份、版本锁定和逐样例远端证据。
def _split_remote_payload(
    *,
    status: str,
    build_server: str, validate_server: str,
    vitis_version: str | None,
    list_results: list[dict[str, Any]],
    dict_first_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总 split build/validate 远端拓扑的 acceptance 载荷。

    参数:
        status: 当前 split 拓扑聚合状态。
        build_server: split 拓扑中的构建服务器名称。
        validate_server: split 拓扑中的验证服务器名称。
        vitis_version: 本次 split 验收使用的 Vitis 版本。
        list_results: 每个 example spec 的远端验收结果。
        dict_first_result: 首个样例预检失败时保留的失败现场。

    返回:
        confidence loop 可直接放入 gates 的字典。
    """

    # 先记录 split 聚合的基础事实，后续字段继续按职责逐项补充。
    dict_payload = {"status": status, "topology": "split_build_validate"}  # split 聚合基础载荷

    # build_server 明确记录 split 拓扑中的构建侧机器身份。
    dict_payload["build_server"] = build_server  # split 构建服务器

    # validate_server 明确记录 split 拓扑中的验证侧机器身份。
    dict_payload["validate_server"] = validate_server  # split 验证服务器

    # vitis_version 保留本轮 split 验收使用的版本锁定信息。
    dict_payload["vitis_version"] = vitis_version  # split 验收版本

    # results 保存所有样例的远端执行结果，供最终报告和 coverage 复用。
    dict_payload["results"] = list_results  # split 逐样例结果

    # 首个样例预检失败时要保留失败现场，方便定位 build/validate 哪一侧先出错。
    if dict_first_result is not None:

        # 把第一条失败证据写回聚合结果，供最终报告引用。
        dict_payload["first_result"] = dict_first_result  # split 首轮失败现场

    # 返回 split 拓扑聚合后的结构化结果。
    return dict_payload

# _run_split_remote_acceptance 把 split 远端验收兼容层收敛成单个可测试入口。
def _run_split_remote_acceptance(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """执行 build/validate 分离拓扑的远端 Vitis acceptance。

    参数:
        args: 兼容旧接口的 build_server、validate_server、readiness、example_specs。
        kwargs: 可选 vitis_version 和 parallelism 配置。

    返回:
        split topology acceptance 的结构化 gate 结果。
    """

    # 兼容旧调用时，先解析 split 拓扑中的构建服务器名称。
    str_build_server = args[0] if len(args) >= 1 else kwargs["build_server"]  # 远端构建服务器名称

    # 兼容旧调用时，再解析 split 拓扑中的验证服务器名称。
    str_validate_server = args[1] if len(args) >= 2 else kwargs["validate_server"]  # 远端验证服务器名称

    # 兼容旧调用时，读取本轮远端验收深度。
    str_readiness = args[2] if len(args) >= 3 else kwargs["readiness"]  # 远端验收深度

    # 兼容旧调用时，读取本轮远端样例列表。
    list_example_specs = args[3] if len(args) >= 4 else kwargs["example_specs"]  # 本轮远端样例列表

    # 关键字参数中的版本选择会透传给所有 split 子任务。
    str_vitis_version = kwargs.get("vitis_version")  # Vitis 版本选择

    # 并发度缺省为 1，保持默认串行拓扑更易审计。
    int_parallelism = int(kwargs.get("parallelism", 1))  # 远端并行任务数

    # 没有样例时无法构造 split 预检，直接返回失败并保留空结果表。
    if not list_example_specs:

        # 调用方需要先补齐 split 远端样例矩阵。
        return _split_remote_payload(
            status="failed",
            build_server=str_build_server,
            validate_server=str_validate_server,
            vitis_version=str_vitis_version,
            list_results=[],
        )

    # 首个样例承担 split 拓扑的预检角色，失败时直接停止后续双机调度。
    dict_first_result = _run_split_remote(  # 用首个样例探测 split 双机链路是否走通
        str_build_server,  # 提供负责构建 xclbin 的 build 服务器名
        str_validate_server,  # 提供负责验证和运行的 validate 服务器名
        str_readiness,  # 指定当前只跑 smoke 还是完整 acceptance 深度
        list_example_specs[0],  # 选首个样例作为 split 拓扑探路请求
        vitis_version=str_vitis_version,  # 把用户锁定的 Vitis 版本透传给远端
    )

    # 首个样例失败时停止后续调度，避免继续消耗双机远端资源。
    if dict_first_result.get("status") != PASS_STATUS:

        # 返回空结果表，并补充首个失败样例现场。
        return _split_remote_payload(
            status="failed",
            build_server=str_build_server,
            validate_server=str_validate_server,
            vitis_version=str_vitis_version,
            list_results=[],
            dict_first_result=dict_first_result,
        )

    # 剩余样例直接复用已经验证过的 split 双机上下文。
    func_worker = partial(  # 把已验证的 split 双机上下文固化成并行 worker
        _run_split_remote,  # 后续样例统一复用的 split 远端入口
        str_build_server,  # 固定负责构建阶段的 build 服务器名
        str_validate_server,  # 固定负责验证阶段的 validate 服务器名
        str_readiness,  # 沿用首个样例已确认可行的验收深度
        vitis_version=str_vitis_version,  # 沿用首个样例已确认的版本约束
    )

    # 通过并行器收集首个预检样例之外的所有 split 远端结果。
    list_remaining_results = _cr._run_parallel_specs(  # 批量收集剩余 split 样例的远端结果
        list_example_specs[1:],  # 首个探路样例之外仍需远端执行的样例列表
        func_worker,  # 已绑定双机和版本约束的 split worker
        parallelism=int_parallelism,  # split 批次一次允许并发的样例数量
    )

    # 最终结果列表要把首个预检样例放回最前面。
    list_results = [dict_first_result, *list_remaining_results]  # split 拓扑完整远端结果

    # split 拓扑下每个样例都需要通过并保留远端产物。
    bool_passed = all(_remote_result_retained(item) for item in list_results)  # split 验收整体状态

    # 返回 split 拓扑的完整聚合结果。
    return _split_remote_payload(
        status=PASS_STATUS if bool_passed else "failed",
        build_server=str_build_server,
        validate_server=str_validate_server,
        vitis_version=str_vitis_version,
        list_results=list_results,
    )

# board gate 兼容层把旧入口参数规整成单个上下文字典，避免业务逻辑继续拆散。
def _remote_board_context_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """把兼容层参数归一化为 board gate 所需的上下文对象。

    参数:
        args: 兼容旧接口保留的位置参数元组。
        kwargs: 调用方传入的关键字参数字典。

    返回:
        供 board gate 实际实现消费的结构化上下文字典。

    异常:
        TypeError: 位置参数数量不正确或出现未声明关键字参数时抛出。
    """

    # board gate 的兼容入口固定只接受 server 和 readiness 两个位置参数。
    if len(args) != 2:

        # 位置参数不完整时直接阻断，避免把异常调用形态带入远端验收逻辑。
        raise TypeError(
            "> ERR: [Python] _remote_board_acceptance_gate requires server and readiness positional args"
        )

    # pop 会修改原字典，这里先复制一份关键字参数副本。
    dict_options = dict(kwargs)  # board gate 关键字参数副本

    # server 可以为空；非空值统一转成字符串。
    str_server = None if args[0] is None else str(args[0])  # 单机远端服务器名

    # readiness 表示远端验收深度，保持字符串形式传给远端脚本。
    str_readiness = str(args[1])  # 远端验收 readiness 模式

    # 版本、远端请求标记和前置 Vitis 门禁都会直接进入 board 上下文。
    str_vitis_version = dict_options.pop("vitis_version")  # board 阶段沿用的 Vitis 版本

    # remote_requested 标记决定 board gate 是否真的去远端执行。
    bool_remote_requested = bool(dict_options.pop("remote_requested"))  # 是否执行真实远端 board 阶段

    # 前置 Vitis gate 结果会决定 board 阶段是 blocked 还是继续执行。
    dict_remote_vitis_gate = dict_options.pop("remote_vitis_gate")  # 前置 Vitis 门禁结果

    # board 分区、样例列表和并发度共同决定实际的上板目标集合。
    dict_board_partition = dict_options.pop("board_partition")  # board 声明分区

    # 选中的样例列表限定本轮 board gate 实际要考虑的 spec。
    list_selected_specs = list(dict_options.pop("selected_specs"))  # 本轮选中的样例名

    # 并发度决定 board 样例在远端的最大并行数。
    int_parallelism = int(dict_options.pop("parallelism"))  # board 阶段请求的并发数

    # 遗留未知关键字说明调用方和 board gate 契约已经不同步。
    if dict_options:

        # 显式报告多余字段，防止新增控制项被静默忽略。
        raise TypeError(f"> ERR: [Python] unexpected board acceptance options: {sorted(dict_options)}")

    # 返回压缩后的 board 验收上下文，后续逻辑只消费这份结构化对象。
    return {
        "server": str_server,
        "readiness": str_readiness,
        "vitis_version": str_vitis_version,
        "remote_requested": bool_remote_requested,
        "remote_vitis_gate": dict_remote_vitis_gate,
        "board_partition": dict_board_partition,
        "selected_specs": list_selected_specs,
        "parallelism": int_parallelism,
    }

# board gate 只保留本轮选中且声明要求上板或豁免的样例条目。
def _selected_board_partition_entries(
    dict_context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """筛选当前批次真正相关的 board 必验与豁免条目。

    参数:
        dict_context: board gate 已归一化的执行上下文字典。

    返回:
        由必验条目列表和豁免条目列表组成的元组。
    """

    # 选中样例转为集合后复用，避免两个分区筛选重复构造集合。
    set_selected_specs = set(dict_context["selected_specs"])  # 本轮选中样例集合

    # 本轮真正需要上板的样例从静态声明分区里筛出来。
    list_board_specs = [  # 本轮需要 board 验收的样例声明
        entry  # 当前命中的 board 必验声明
        for entry in dict_context["board_partition"].get("board_specs", [])  # board 必验声明全集
        if entry["spec"] in set_selected_specs  # 只保留本轮远端请求实际选中的样例
    ]

    # 豁免样例只进入报告，不会触发真实远端 board 执行。
    list_exempt_specs = [  # 本轮仅做报告记录的 board 豁免声明
        entry  # 当前命中的 board 豁免声明
        for entry in dict_context["board_partition"].get("exempt_specs", [])  # board 豁免声明全集
        if entry["spec"] in set_selected_specs  # 只保留本轮被点名且声明为豁免的样例
    ]

    # 返回本轮有效的 board 必验和豁免样例声明。
    return list_board_specs, list_exempt_specs

# board 结果聚合需要区分通过、阻塞和失败三种状态。
def _board_gate_status(list_results: list[dict[str, Any]]) -> str:
    """把多个 board 子结果聚合为最终状态。

    参数:
        list_results: 已完成的 board 样例结果列表。

    返回:
        `passed`、`blocked` 或 `failed` 之一。
    """

    # 所有 board 返回状态都会进入聚合判定。
    set_statuses = {str(item.get("status")) for item in list_results}  # board 阶段状态集合

    # 所有样例都明确通过时，board gate 才能返回通过。
    if set_statuses == {PASS_STATUS}:

        # 保持最终状态和远端样例结果里的通过文本一致。
        return PASS_STATUS

    # 路由缺失、profile 配置缺失或版本不可选等前置问题应保持阻塞。
    if any(
        item in set_statuses
        for item in {"blocked_board_validation", "blocked_remote_profile_config", "blocked_remote_version_choice"}
    ):

        # 阻塞态要求用户先补齐前置证据，再重新触发 board 阶段。
        return "blocked"

    # 其他非通过状态都视为真实上板失败。
    return "failed"

# 单机 board 验收只处理声明需要上板且本轮被选中的样例。
def _remote_board_acceptance_from_context(dict_context: dict[str, Any]) -> dict[str, Any]:
    """基于归一化上下文执行单机 board 验收门禁。

    参数:
        dict_context: board gate 已归一化的执行上下文字典。

    返回:
        包含 board 声明、执行结果与最终状态的结构化结果字典。
    """

    # board_acceptance 元数据一旦声明错误，远端执行无法补救。
    list_invalid_specs = dict_context["board_partition"].get("invalid_specs", [])  # board_acceptance 元数据错误样例

    # 有声明错误时直接失败，避免继续启动远端 board 任务。
    if list_invalid_specs:

        # 把非法声明列表原样带回报告，方便修复元数据。
        return {"status": "failed", "reason": "invalid_board_acceptance_metadata", "invalid_specs": list_invalid_specs}

    # 先取回本轮需要上板和被豁免的样例声明元组。
    tuple_partition_lists = _selected_board_partition_entries(dict_context)  # board 必验与豁免样例分组

    # 提取本轮必须进入真实上板阶段的样例声明。
    list_board_specs = tuple_partition_lists[0]  # board 必验样例声明

    # 提取本轮只做报告记录的豁免样例声明。
    list_exempt_specs = tuple_partition_lists[1]  # board 豁免样例声明

    # 未请求远端时只校验声明分区，不执行硬件阶段。
    if not dict_context["remote_requested"]:

        # 返回声明模式结果，显式列出 board 必验和豁免清单。
        return {
            "status": PASS_STATUS,
            "mode": "declarations_only",
            "board_specs": list_board_specs,
            "exempt_specs": list_exempt_specs,
            "results": [],
        }

    # board 阶段必须绑定单一远端服务器，split 模式不在这里执行。
    if not dict_context["server"]:

        # 缺少单机服务器时没有办法产生真实上板证据。
        return {"status": "failed", "reason": "board acceptance requires a single remote server", "results": []}

    # board 阶段依赖前置 Vitis 阶段先通过，不能跳过 bitstream 构建事实。
    if not dict_context["remote_vitis_gate"] or dict_context["remote_vitis_gate"].get("status") != PASS_STATUS:

        # 前置证据不足时保持 blocked，而不是冒充真实失败。
        return {
            "status": "blocked",
            "reason": "board acceptance requires successful remote vitis acceptance first",
            "results": [],
        }

    # 本轮没有 board 必验样例时，board 阶段自然通过。
    if not list_board_specs:

        # 返回空执行结果，并保留豁免样例信息供报告引用。
        return {
            "status": PASS_STATUS,
            "mode": "no_board_specs_selected",
            "board_specs": [],
            "exempt_specs": list_exempt_specs,
            "results": [],
        }

    # 先抽取样例名，再把服务器、深度和版本绑定成 board worker。
    list_target_board_specs = [entry["spec"] for entry in list_board_specs]  # 需要真实上板的样例名列表

    # 预绑定 server、readiness 和版本，后续并行器只需传入 spec 名称。
    func_board_worker = partial(  # 把 board 执行上下文固化成单样例 worker
        _run_remote_board,  # 统一走真实上板执行入口
        dict_context["server"],  # 绑定这批 board 请求要连接的服务器
        dict_context["readiness"],  # 绑定这批 board 请求要跑的验收深度
        vitis_version=dict_context["vitis_version"],  # 绑定这批 board 请求锁定的版本
    )

    # 对所有需上板样例运行 board 远端验收，并保留输入顺序。
    list_results = _cr._run_parallel_specs(  # 收集本轮全部 board 样例的远端回包
        list_target_board_specs,  # 当前批次确实需要真实上板的样例名序列
        func_board_worker,  # 已绑定服务器和版本要求的 board worker
        parallelism=dict_context["parallelism"],  # board 阶段允许使用的并发上限
    )

    # 聚合 board 返回状态，区分阻塞和真实失败。
    str_status = _board_gate_status(list_results)  # board gate 聚合状态

    # 返回 board gate 的完整审查载荷。
    return {
        "status": str_status,
        "mode": "remote_board_validation",
        "board_specs": list_board_specs,
        "exempt_specs": list_exempt_specs,
        "results": list_results,
    }

# _remote_board_acceptance_gate 把兼容旧签名的 board gate 包装成单一入口。
def _remote_board_acceptance_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """兼容旧调用方式并执行 board acceptance gate。

    参数:
        args: 旧接口中的 server、readiness 位置参数。
        kwargs: board acceptance 所需关键字配置。

    返回:
        board acceptance gate 的结构化结果。
    """

    # 兼容层只负责整理上下文，真正的 board 业务逻辑交给内部实现。
    dict_context = _remote_board_context_from_args(args, kwargs)  # board 验收调用上下文

    # 返回内部实现生成的 board acceptance gate 结果。
    return _remote_board_acceptance_from_context(dict_context)

# _skipped_family_coverage_payload 生成非 tier1 覆盖模式的跳过报告。
def _skipped_family_coverage_payload(coverage_mode: str) -> dict[str, Any]:
    """生成 smoke 或 all_examples 模式下的 family coverage 跳过载荷。

    参数:
        coverage_mode: 当前远端覆盖模式。

    返回:
        family coverage gate 的结构化跳过结果。
    """

    # 非 tier1 模式仍保留完整字段，方便最终报告统一读取。
    return {
        "status": "skipped",
        "mode": coverage_mode,
        "required_specs": [],
        "vitis_passed_specs": [],
        "board_passed_specs": [],
        "missing_specs": [],
    }

# _tier1_required_specs 提取 tier1 family coverage 的必须覆盖目标。
def _tier1_required_specs(dict_matrix: dict[str, Any]) -> list[str]:
    """从 tier1 board coverage matrix 中提取代表样例和高风险样例。

    参数:
        dict_matrix: tier1_board_coverage_matrix.json 的结构化内容。

    返回:
        去重且保持矩阵顺序的必须覆盖 spec 名称。
    """

    # families 配置损坏时按空矩阵处理，避免单个坏项中断整个覆盖率报告。
    dict_families = dict_matrix.get("families", {}) if isinstance(dict_matrix, dict) else {}  # 板卡覆盖矩阵配置

    # 用顺序列表累计 tier1 模式下必须闭合的代表样例。
    list_required_specs: list[str] = []  # tier1 必须覆盖样例列表

    # 每个 family 至多贡献 representative 和 high_risk 两个样例。
    for dict_family_config in dict_families.values():

        # family 配置不是字典时直接跳过，继续处理其他家族。
        if not isinstance(dict_family_config, dict):

            # 坏配置不应该阻塞其他 family 的 coverage 统计。
            continue

        # 按固定角色顺序读取代表样例和高风险样例。
        for str_role_key in ("representative", "high_risk"):

            # 读取当前角色对应的样例名，并去掉配置里的多余空白。
            str_spec_name = str(dict_family_config.get(str_role_key) or "").strip()  # 当前角色要求的样例名

            # 同一样例可能同时承担两个角色，覆盖要求中只保留一次。
            if str_spec_name and str_spec_name not in list_required_specs:

                # 按矩阵声明顺序追加新的必验样例。
                list_required_specs.append(str_spec_name)

    # 返回 tier1 覆盖所需的样例名列表。
    return list_required_specs

# _passed_specs_for_phase 汇总指定远端阶段已经通过的 spec。
def _passed_specs_for_phase(results: list[dict[str, Any]], phase: str) -> set[str]:
    """筛选远端验收结果中指定阶段已通过的 example spec。

    参数:
        results: Vitis 和 board 两类远端验收结果的扁平集合。
        phase: 需要筛选的阶段名称，例如 vitis 或 board。

    返回:
        指定阶段状态为 passed 的 spec 名称集合。
    """

    # 只收集阶段名和状态都匹配的样例。
    return {
        str(dict_item.get("example_spec") or "")  # 当前结果对应的样例名
        for dict_item in results  # 待筛选的远端阶段结果
        if (
            str(dict_item.get("phase") or "") == phase
            and str(dict_item.get("status") or "") == PASS_STATUS
            and str(dict_item.get("example_spec") or "")
        )  # 只保留目标阶段且状态通过的结果
    }

# _family_coverage_payload 汇总 tier1 family coverage 的最终报告。
def _family_coverage_payload(
    *,
    dict_matrix: dict[str, Any],
    coverage_mode: str,
    list_required_specs: list[str],
    set_vitis_passed: set[str],
    set_board_passed: set[str],
) -> dict[str, Any]:
    """根据 tier1 必选 spec 和远端通过集合生成覆盖率报告。

    参数:
        dict_matrix: tier1 board coverage matrix 的结构化内容。
        coverage_mode: 当前远端覆盖模式。
        list_required_specs: tier1 模式必须覆盖的 spec 名称。
        set_vitis_passed: Vitis 阶段已通过的 spec 名称集合。
        set_board_passed: board 阶段已通过的 spec 名称集合。

    返回:
        family coverage gate 的结构化结果。
    """

    # 缺少任一阶段证据的 tier1 样例都要进入缺口清单。
    list_missing_specs = [  # tier1 覆盖缺失样例
        str_spec_name  # 当前仍缺至少一个阶段证据的样例
        for str_spec_name in list_required_specs  # tier1 必验样例全集
        if str_spec_name not in set_vitis_passed or str_spec_name not in set_board_passed  # 任一阶段缺证据就记为覆盖缺口
    ]

    # 返回 tier1 覆盖率门禁结果。
    return {
        "status": PASS_STATUS if not list_missing_specs else "failed",
        "mode": str(dict_matrix.get("mode") or coverage_mode),
        "required_specs": list_required_specs,
        "vitis_passed_specs": sorted(set_vitis_passed),
        "board_passed_specs": sorted(set_board_passed),
        "missing_specs": list_missing_specs,
    }

# _remote_family_coverage_gate 把 family 覆盖闭合检查收敛成可测试入口。
def _remote_family_coverage_gate(results: list[dict[str, Any]], *, coverage_mode: str) -> dict[str, Any]:
    """检查远端验收结果是否满足 tier1 family 覆盖要求。

    参数:
        results: 已收集到的远端阶段结果列表。
        coverage_mode: 当前启用的覆盖率模式。

    返回:
        包含必验样例、已通过样例和缺口清单的结构化结果字典。
    """

    # 先快照 family coverage 判定前的矩阵入口，避免测试 patch 或前序 gate 状态泄漏到后续流程。
    func_previous_tier1_board_matrix_path = _cl._tier1_board_matrix_path  # family coverage 判定前的矩阵入口

    # family coverage 只在当前函数体内借用可 patch 的矩阵路径 wrapper。
    try:

        # 测试会 patch 本地矩阵路径入口，这里把底层 loader 指向当前模块的可 patch wrapper。
        _cl._tier1_board_matrix_path = _tier1_board_matrix_path  # family coverage 判定期间的矩阵入口

        # 非 tier1 模式只报告跳过，不制造覆盖缺口。
        if coverage_mode != "tier1":

            # smoke 和 all_examples 模式都不要求 family 矩阵闭合。
            return _skipped_family_coverage_payload(coverage_mode)

        # 读取 tier1 矩阵，获得代表样例和高风险样例要求。
        dict_matrix: dict[str, Any] = _cl._load_tier1_board_matrix()  # tier1 board 覆盖矩阵

        # 矩阵对象包含多类元数据，这里只提取 family coverage 需要闭合的样例清单。
        list_required_specs = _tier1_required_specs(dict_matrix)  # family coverage 必验样例清单

        # 汇总已经拿到远端 Vitis 成功证据的样例集合。
        set_vitis_passed = _passed_specs_for_phase(results, "vitis")  # 已具备远端 Vitis 成功证据的样例集合

        # 汇总已经拿到真实上板成功证据的样例集合。
        set_board_passed = _passed_specs_for_phase(results, "board")  # 已具备真实上板成功证据的样例集合

        # 返回 tier1 family coverage 的闭合性报告。
        return _family_coverage_payload(
            dict_matrix=dict_matrix,
            coverage_mode=coverage_mode,
            list_required_specs=list_required_specs,
            set_vitis_passed=set_vitis_passed,
            set_board_passed=set_board_passed,
        )

    # 无论 family coverage 是否判定成功，都要把 `_cl` 还原到调用前的矩阵入口。
    finally:

        # 把 `_cl` 恢复到进入判定前的矩阵入口，避免后续 tier1 调用读到上一轮测试的临时矩阵路径。
        _cl._tier1_board_matrix_path = func_previous_tier1_board_matrix_path  # family coverage 结束后恢复的矩阵入口

# repo_root 统一给 reports、tests 和 smoke 等仓库级产物提供根路径。
def repo_root() -> Path:
    """返回当前技能仓库的工作区根目录。

    参数:
        无。

    返回:
        HLSGenerator 仓库根路径。
    """

    # 返回技能根的上两级目录，保持 reports/smoke/tests 位于工作区根。
    return SKILL_ROOT.parents[1]

# _remove_cache_entry 删除 pytest cache 内单个文件或目录。
def _remove_cache_entry(path_cache_entry: Path) -> None:
    """删除 pytest cache 目录中的单个路径。

    参数:
        path_cache_entry: 待删除的 cache 文件、符号链接或空目录。

    返回:
        无。
    """

    # 该保护块隔离并发删除或文件系统瞬时错误。
    try:

        # 文件和符号链接都可以直接 unlink，不需要先区分文件类型细节。
        if path_cache_entry.is_file() or path_cache_entry.is_symlink():

            # 删除 cache 文件或符号链接，避免后续 governance 误扫临时产物。
            path_cache_entry.unlink(missing_ok=True)

        # 目录路径只会在子项已经清空后走到这里。
        elif path_cache_entry.is_dir():

            # 删除已经清空的 pytest cache 子目录。
            path_cache_entry.rmdir()

    # 并发删除和文件系统瞬时错误都不应该阻断主 gate。
    except (FileNotFoundError, OSError):

        # 并发清理失败不影响 confidence gate 主流程。
        return

# _remove_pytest_cache_dir 删除指定根目录下的 pytest cache。
def _remove_pytest_cache_dir(path_base: Path) -> None:
    """清理指定目录中的 .pytest_cache 临时内容。

    参数:
        path_base: 可能包含 .pytest_cache 的工作区或技能根路径。

    返回:
        无。
    """

    # path_cache_dir 指向 pytest 在该根目录下生成的临时目录。
    path_cache_dir = path_base / ".pytest_cache"  # 本地文件系统路径

    # 目标根目录没有 pytest cache 时无需再继续扫描。
    if not path_cache_dir.exists():

        # 没有 cache 目录时直接返回，避免创建额外文件系统痕迹。
        return

    # 先逆序删除子路径，保证目录删除时已为空。
    for path_cache_entry in sorted(path_cache_dir.rglob("*"), reverse=True):

        # 删除 cache 子项，失败时内部按可恢复清理处理。
        _remove_cache_entry(path_cache_entry)

    # 根目录可能被并发删除；这类清理竞争不影响主流程真实性。
    try:

        # 删除空的 pytest cache 根目录。
        path_cache_dir.rmdir()

    # 目录不存在或仍被占用时保持静默，让 gate 继续向前执行。
    except (FileNotFoundError, OSError):

        # 清理失败不影响主流程 gate 的真实状态。
        return

# 临时清理逻辑只处理 pytest cache，避免前一轮治理产物污染当前验证。
def _cleanup_ephemeral_validation_dirs() -> None:
    """删除 confidence loop 运行中可能产生的临时 pytest cache。

    参数:
        无。

    返回:
        无。
    """

    # 依次清理工作区根和技能根，覆盖 pytest 两种常见落点。
    for path_base in (repo_root(), SKILL_ROOT):

        # 删除当前根目录下的 pytest cache 临时产物。
        _remove_pytest_cache_dir(path_base)

# _configure_text_streams 设置 Windows 终端的 UTF-8 输出。
def _configure_text_streams() -> None:
    """在支持 reconfigure 的终端上启用 UTF-8 replacement 输出。

    参数:
        无。

    返回:
        无。
    """

    # Windows 终端可重配时，优先固定 stdout 的 UTF-8 编码。
    if hasattr(sys.stdout, "reconfigure"):

        # 标准输出使用 UTF-8，避免中文报告在 Windows 终端乱码。
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # stderr 同样保持 UTF-8，避免异常摘要出现乱码。
    if hasattr(sys.stderr, "reconfigure"):

        # 标准错误同样使用 UTF-8，保证异常摘要可读。
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# _build_argument_parser 构造 confidence loop 的 CLI 参数。
def _build_argument_parser() -> argparse.ArgumentParser:
    """构造本地和远端 confidence gate 的命令行参数解析器。

    参数:
        无。

    返回:
        已注册所有 CLI 参数的 ArgumentParser。
    """

    # parser 描述本脚本的本地/远端 confidence gate 用法。
    parser = argparse.ArgumentParser(description="Run Erie HLS Generator local and optional remote confidence gates.")  # CLI 参数解析对象

    # 单服务器远端验收目标。
    parser.add_argument("--server", help="Optional erie-remote-ssh server for real remote Vitis validation.")

    # split 拓扑的构建服务器。
    parser.add_argument("--build-server", help="Optional split-topology build server for real remote Vitis validation.")

    # split 拓扑的验证服务器。
    parser.add_argument(
        "--validate-server",
        help="Optional split-topology validation server for real remote Vitis validation.",
    )

    # 显式远端 Vitis 版本。
    parser.add_argument("--vitis-version", help="Explicit remote Vitis version to use for remote matrix validation.")

    # readiness 控制远端 Vitis 验收深度。
    parser.add_argument(
        "--readiness",
        default="cosim",
        choices=("static", "compile", "execute", "implement", "cosim"),
    )

    # example spec 可重复传入以覆盖默认远端矩阵。
    parser.add_argument(
        "--example-spec",
        action="append",
        help="Example spec to use for optional remote validation. Can be repeated.",
    )

    # 跳过 smoke gate，常用于局部调试。
    parser.add_argument("--skip-smoke", action="store_true")

    # 跳过 compileall gate，常用于远端路径单独调试。
    parser.add_argument("--skip-compileall", action="store_true")

    # 跳过 quick_validate gate，常用于修复治理脚本自身。
    parser.add_argument("--skip-quick-validate", action="store_true")

    # 保留兼容的 skip-pytest 参数；本地链路已不再执行 pytest。
    parser.add_argument("--skip-pytest", action="store_true")

    # 跳过所有真实远端验收。
    parser.add_argument("--skip-remote", action="store_true")

    # 远端并行度仍会被底层 runtime 限制，CLI 参数用于显式记录意图。
    parser.add_argument(
        "--remote-parallelism",
        type=int,
        default=3,
        help="Requested concurrent remote review jobs; the runtime hard-caps actual Vivado/Vitis fan-out at 3.",
    )

    # 默认远端覆盖模式决定没有 --example-spec 时选哪些样例。
    parser.add_argument(
        "--remote-coverage",
        default="smoke",
        choices=("smoke", "tier1", "all_examples"),
        help="Default remote example set when --example-spec is not provided.",
    )

    # 本地 gate subprocess 超时。
    parser.add_argument(
        "--gate-timeout-s",
        type=int,
        default=900,
        help="Timeout for each local confidence gate command.",
    )

    # JSON 输出文件路径。
    parser.add_argument("--json-out", help="Write JSON summary to this path.")

    # 返回已完成参数注册的 parser。
    return parser

# _create_run_root 创建本轮 confidence loop 的报告目录。
def _create_run_root() -> Path:
    """创建带 UTC 时间戳和进程号的 confidence loop 报告目录。

    参数:
        无。

    返回:
        本轮 gate 运行的 reports/confidence-loop 子目录。
    """

    # str_run_id 使用 UTC 时间戳和 pid 避免并发运行互相覆盖。
    str_run_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-pid{os.getpid()}"  # 运行标识

    # reports/confidence-loop/<run-id> 是本轮治理产物的唯一落点。
    path_run_root = repo_root() / "reports" / "confidence-loop" / str_run_id  # 本轮报告目录

    # 创建报告目录，供 mock validation 和 forward test 写入产物。
    path_run_root.mkdir(parents=True, exist_ok=True)

    # 返回本轮 confidence loop 的报告目录。
    return path_run_root

# _record_command_gate 执行一个本地命令型 gate 并写入 gates。
def _record_command_gate(
    dict_gates: dict[str, dict[str, Any]],
    gate_name: str,
    list_command: list[str],
    *,
    cwd: Path,
    timeout_s: int,
) -> None:
    """执行本地 subprocess gate 并保存结构化结果。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。
        gate_name: 当前 gate 在结果载荷中的字段名。
        list_command: 需要执行的命令参数。
        cwd: 命令执行目录。
        timeout_s: 命令超时秒数。

    返回:
        无。
    """

    # dict_gates[gate_name] 保存当前命令 gate 的结构化执行结果。
    dict_gates[gate_name] = _run_command(list_command, cwd=cwd, timeout_s=timeout_s)  # 质量门输出载荷

# _record_remote_smoke_and_pytest_gates 负责登记远端 smoke 与 remote_pytest。
def _record_remote_smoke_and_pytest_gates(
    dict_gates: dict[str, dict[str, Any]],
    namespace_args: argparse.Namespace,
    *,
    split_requested: bool,
    remote_requested: bool,
) -> None:
    """在路由契约通过后登记远端 smoke 与 remote_pytest gate。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。
        namespace_args: 已解析的 CLI 参数。
        split_requested: 是否请求 build/validate 分离拓扑。
        remote_requested: 是否请求任何真实远端验收。

    返回:
        无。
    """

    # 路由契约未通过或本轮未请求真实远端时，不登记任何远端 Python gate。
    if dict_gates["route_contract"]["status"] != PASS_STATUS or not remote_requested:

        # 此分支明确表示本轮没有进入远端 Python gate 执行阶段。
        return

    # skip_smoke 用于把必需 smoke gate 改由远端执行，以遵守“pytest 只在远端跑”的约束。
    if namespace_args.skip_smoke:

        # split 拓扑下的 smoke 固定跑在 validate 服务器。
        if split_requested:

            # split 拓扑会把 smoke 日志固定沉淀到最终验收服务器。
            dict_gates["smoke"] = _run_split_remote_smoke(  # split smoke gate 直接记录验收端证据
                namespace_args.build_server,  # build 端仅用于补齐 split 路由拓扑
                namespace_args.validate_server,  # validate 端实际承载 smoke 执行与日志
            )

        # 单机拓扑时，直接在命中的 route_contract server 跑远端 smoke。
        else:

            # 单机模式把 smoke 直接落在 route_contract 命中的同一台服务器。
            dict_gates["smoke"] = _run_remote_smoke(namespace_args.server)  # 单机 smoke gate 直接复用命中的远端服务器

    # remote_pytest 是最终信心里的完整远端 Python 回归证据。
    if split_requested:

        # split 模式的远端 pytest 需要同时覆盖 build 和 validate 两台机器的最新树。
        dict_gates["remote_pytest"] = _run_split_remote_pytest(  # split remote_pytest gate 汇总双机 Python 回归
            namespace_args.build_server,  # build 端校验构建机上的最新树与脚本入口
            namespace_args.validate_server,  # validate 端补齐验收机上的最新树回归证据
        )

    # 单机拓扑直接在命中的 route_contract server 执行完整远端 pytest。
    else:

        # 单机模式只需在 route_contract 选中的服务器上完成整套远端 Python 回归。
        dict_gates["remote_pytest"] = _run_remote_pytest(namespace_args.server)  # 单机 remote_pytest gate 复用同一台远端服务器

# _run_local_command_gates 执行可跳过的本地基础 gate。
def _run_local_command_gates(dict_gates: dict[str, dict[str, Any]], namespace_args: argparse.Namespace) -> None:
    """执行 smoke、compileall 和 quick_validate 三个本地 gate。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。
        namespace_args: 已解析的 CLI 参数。

    返回:
        无。
    """

    # smoke 负责覆盖技能级端到端最小链路。
    if not namespace_args.skip_smoke:

        # 运行 smoke 流程，覆盖技能级端到端最小验收。
        _record_command_gate(
            dict_gates,
            "smoke",
            [sys.executable, _path_arg(SMOKE_GATE_SCRIPT)],
            cwd=repo_root(),
            timeout_s=namespace_args.gate_timeout_s,
        )

    # compileall 只验证迁移后的 Python 功能域能否完成语法编译。
    if not namespace_args.skip_compileall:

        # 运行 scripts/python compileall，确保技能核心功能域可语法编译。
        _record_command_gate(
            dict_gates,
            "compileall",
            [sys.executable, "-m", "compileall", "scripts/python"],
            cwd=SKILL_ROOT,
            timeout_s=namespace_args.gate_timeout_s,
        )

    # quick_validate 负责资源布局与关键入口的快速自检。
    if not namespace_args.skip_quick_validate:

        # 运行 quick_validate，覆盖技能结构和关键资源快速检查。
        _record_command_gate(
            dict_gates,
            "quick_validate",
            [sys.executable, _path_arg(QUICK_VALIDATE_SCRIPT), str(SKILL_ROOT)],
            cwd=SKILL_ROOT,
            timeout_s=namespace_args.gate_timeout_s,
        )

    # 本地链路不再执行 pytest；最终 factual high confidence 改由 remote_pytest gate 提供。

# _run_governance_gates 执行不可省略的治理 gate。
def _run_governance_gates(dict_gates: dict[str, dict[str, Any]], timeout_s: int) -> None:
    """执行 agents、docs 和 dirs 三个治理验证 gate。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。
        timeout_s: 每个治理命令的超时秒数。

    返回:
        无。
    """

    # 清理 pytest cache，避免治理扫描把临时文件当作仓库内容。
    _cleanup_ephemeral_validation_dirs()

    # 运行 AGENTS 指令治理验证。
    _record_command_gate(
        dict_gates,
        "verify_agents",
        [sys.executable, _path_arg(VERIFY_AGENTS_SCRIPT), str(repo_root())],
        cwd=SKILL_ROOT,
        timeout_s=timeout_s,
    )

    # 运行文档治理验证。
    _record_command_gate(
        dict_gates,
        "manage_docs_verify",
        [sys.executable, _path_arg(MANAGE_DOCS_SCRIPT), "verify", str(repo_root())],
        cwd=SKILL_ROOT,
        timeout_s=timeout_s,
    )

    # 运行目录治理验证。
    _record_command_gate(
        dict_gates,
        "manage_dirs_verify",
        [sys.executable, _path_arg(MANAGE_DIRS_SCRIPT), "verify", str(repo_root())],
        cwd=SKILL_ROOT,
        timeout_s=timeout_s,
    )

# _run_static_project_gates 执行不依赖远端的项目静态 gate。
def _run_static_project_gates(dict_gates: dict[str, dict[str, Any]]) -> None:
    """执行依赖扫描、版权扫描和发布敏感内容扫描等本地静态 gate。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。

    返回:
        无。
    """

    # 依赖 gate 用来确认 skill 自身声明和实际依赖没有漂移。
    dict_gates["skill_dependencies"] = _skill_dependency_gate()  # 技能依赖门禁结果

    # 版权词扫描专门拦截 references 和文档里的敏感来源表述。
    dict_gates["copyright_term_scan"] = _copyright_term_scan()  # 版权敏感词扫描结果

    # 发布敏感扫描检查仓库内容是否泄漏本地工具链与板卡细节。
    dict_gates["release_sensitivity_scan"] = _release_sensitivity_scan()  # 发布敏感内容扫描结果

    # 禁止引用名称扫描负责挡住不允许出现在 skill 内容里的外部名字。
    dict_gates["forbidden_reference_names"] = _forbidden_reference_name_scan()  # 禁止引用名称扫描结果

# _dependency_failure_blocks_governance 判断当前依赖失败是否应该提前跳过治理链。
def _dependency_failure_blocks_governance(dict_gates: dict[str, dict[str, Any]]) -> bool:
    """判断 skill_dependencies 失败是否已经足以阻断后续治理 gate。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。

    返回:
        当 skill_dependencies 明确以 core scope 失败时返回 True。
    """

    # 先读取 skill_dependencies gate，缺失时保持原有治理链继续执行。
    dict_skill_dependency_gate = dict_gates.get("skill_dependencies", {})  # 当前 skill_dependencies gate 载荷

    # 依赖 gate 已经通过时，不需要阻断治理 gate。
    if dict_skill_dependency_gate.get("status") == PASS_STATUS:

        # 通过状态继续执行完整治理链。
        return False

    # report 里保存依赖检查具体失败范围和缺失明细。
    dict_dependency_report = dict_skill_dependency_gate.get("report", {})  # skill_dependencies 的报告载荷

    # 缺少结构化报告时，把失败视为足以阻断治理链的事实。
    if not isinstance(dict_dependency_report, dict):

        # 没有可靠 report 时，保守地跳过后续昂贵治理链。
        return True

    # scopes 指出本轮缺失依赖命中的治理范围。
    list_scopes = dict_dependency_report.get("scopes", [])  # skill_dependencies 报告里的作用域列表

    # report 结构异常时，同样保守地阻断治理链。
    if not isinstance(list_scopes, list):

        # scopes 异常意味着依赖结论不可细分，直接阻断治理链。
        return True

    # core 依赖未满足时，最终结论已无法形成高信心，无需继续昂贵治理链。
    return any(str(item).strip() == "core" for item in list_scopes)

# _record_skipped_governance_gates 把被依赖阻断的治理 gate 统一记为 skipped。
def _record_skipped_governance_gates(dict_gates: dict[str, dict[str, Any]]) -> None:
    """把 verify_agents、manage_docs_verify 和 manage_dirs_verify 统一标记为 skipped。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。

    返回:
        无。
    """

    # 三个治理 gate 共用同一份阻断原因，便于最终报告追溯。
    dict_skipped_gate = {  # 统一复用的治理 gate skipped 载荷
        "status": "skipped",  # 当前治理 gate 未执行
        "reason": "blocked_dependency",  # 阻断原因来自 skill_dependencies
        "blocked_by": "skill_dependencies",  # 明确指出上游阻断 gate
    }

    # verify_agents 在缺核心依赖时没有必要继续执行。
    dict_gates["verify_agents"] = dict_skipped_gate.copy()  # AGENTS 治理 gate 的 skipped 载荷

    # docs 治理同样被核心依赖缺失阻断。
    dict_gates["manage_docs_verify"] = dict_skipped_gate.copy()  # handoff 与 memory 文档治理的 skipped 载荷

    # 目录治理也沿用同一份 blocked_dependency 事实。
    dict_gates["manage_dirs_verify"] = dict_skipped_gate.copy()  # 本地与远端目录契约治理的 skipped 载荷

# _run_example_gates 执行示例 mock、注释策略和 forward test gate。
def _run_example_gates(
    dict_gates: dict[str, dict[str, Any]],
    path_run_root: Path,
) -> list[str]:
    """根据依赖 gate 状态执行示例相关本地 gate。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。
        path_run_root: 本轮报告输出目录。

    返回:
        本地样例 spec 名称列表。
    """

    # 默认使用技能自带样例列表；依赖就绪后再用真实验证覆盖结果回填。
    list_example_specs: list[str] = _example_spec_names()  # 技能自带样例列表

    # skill_dependencies 不通过时，样例验证及其下游 gate 都只能保持 skipped。
    bool_dependencies_ready = dict_gates["skill_dependencies"]["status"] == PASS_STATUS  # 是否允许进入样例阶段

    # 依赖准备完成后，mock validation 才能提供真实样例覆盖结果。
    if bool_dependencies_ready:

        # validate_examples 会同时返回门禁结果和本轮真正覆盖到的样例名列表。
        tuple_example_validation = _validate_examples(path_run_root)  # 样例验证返回元组

        # 元组第一个元素是 mock validation gate 的完整结果。
        dict_examples_gate = tuple_example_validation[0]  # 示例验证结果

        # 元组第二个元素是本轮真实覆盖到的样例名列表。
        list_example_specs = tuple_example_validation[1]  # 样例覆盖列表

        # 样例验证有真实产物后，注释策略 gate 才有审查对象。
        dict_comment_policy_gate = _comment_policy_gate(path_run_root)  # 注释策略门禁结果

        # forward test 同样依赖示例阶段成功启动后留下的产物。
        dict_forward_test_gate = _forward_test_gate(path_run_root)  # forward test 门禁结果

    # 依赖未通过时，示例相关 gate 全部保持 skipped，而不是伪造失败现场。
    else:

        # skipped 结果需要明确说明阻塞来自依赖门禁。
        dict_examples_gate = {"status": "skipped", "reason": "blocked_dependency", "results": []}  # 示例验证阻塞说明

        # 注释策略 gate 在没有样例产物时只能报告阻塞原因。
        dict_comment_policy_gate = {"status": "skipped", "reason": "blocked_dependency"}  # 注释策略阻塞说明

        # forward test 也要显式保留阻塞事实和空结果集。
        dict_forward_test_gate = {"status": "skipped", "reason": "blocked_dependency", "results": []}  # forward test 阻塞说明

    # 把 mock 样例验证明细登记到 gate 聚合器。
    dict_gates["example_mock_validation"] = dict_examples_gate  # 汇总给最终报告的 mock 样例验证明细

    # 把样例产物的注释审查结论登记到 gate 聚合器。
    dict_gates["comment_policy"] = dict_comment_policy_gate  # 汇总给最终报告的样例注释审查结论

    # 把动态样例回归结果登记到 gate 聚合器。
    dict_gates["forward_test"] = dict_forward_test_gate  # 汇总给最终报告的动态样例回归结果

    # 返回本地样例列表，供最终 payload 记录本轮实际覆盖范围。
    return list_example_specs

# _run_remote_gates 执行远端 Vitis、目录和 board gate。
def _run_remote_gates(
    dict_gates: dict[str, dict[str, Any]],
    namespace_args: argparse.Namespace,
    list_remote_specs: list[str],
) -> list[dict[str, Any]]:
    """根据 CLI 参数执行可选远端 confidence gate。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。
        namespace_args: 已解析的 CLI 参数。
        list_remote_specs: 本轮远端验收选中的 example spec 列表。

    返回:
        远端 Vitis acceptance 的结果集合。
    """

    # split 拓扑必须同时提供 build 和 validate 两台机器。
    bool_has_split_servers = bool(namespace_args.build_server and namespace_args.validate_server)  # CLI 是否同时给出了 build_server 与 validate_server

    # split 只有在双机齐备且没有显式 skip_remote 时才算真正请求。
    bool_split_remote_requested = bool(bool_has_split_servers and not namespace_args.skip_remote)  # 双机配置在未 skip_remote 时是否升级为真实 split 请求

    # 先判断本轮是否至少指定了一个真实远端目标。
    bool_has_any_remote_target = bool(namespace_args.server or bool_split_remote_requested)  # 本轮配置里是否至少存在一个可执行的远端目标

    # 只有存在真实远端目标且没有显式 skip_remote，才算请求远端验收。
    bool_remote_requested = bool(bool_has_any_remote_target and not namespace_args.skip_remote)  # 最终 scope 计算时是否应视为发起过真实远端验收

    # route_contract 先确认远端目标是否符合 AGENTS 路由契约。
    dict_route_contract_gate = _route_contract_gate(  # 核对本轮远端目标是否满足 AGENTS 路由契约
        namespace_args.server,  # 单机模式下直接执行的服务器名
        namespace_args.build_server,  # split 拓扑里负责产出 xclbin 的 build 节点
        namespace_args.validate_server,  # split 拓扑里负责校验和运行的 validate 节点
        remote_requested=bool_remote_requested,  # 最终报告是否应视为发起过真实远端验收
    )

    # 保存路由契约 gate，后续所有远端阶段都以它为前置事实。
    dict_gates["route_contract"] = dict_route_contract_gate  # 路由契约 gate

    # board_acceptance_declarations 负责静态拆分必验、豁免和非法声明。
    dict_board_partition = _board_acceptance_partition_gate()  # board_acceptance 静态声明分区

    # 把声明分区结果写入 gate 聚合器，供最终报告和 board gate 继续消费。
    dict_gates["board_acceptance_declarations"] = dict_board_partition  # board_acceptance 声明结果

    # 路由契约通过后，根据拓扑登记远端 smoke 与 remote_pytest。
    _record_remote_smoke_and_pytest_gates(
        dict_gates,
        namespace_args,
        split_requested=bool_split_remote_requested,
        remote_requested=bool_remote_requested,
    )

    # 路由契约通过后才允许真正触发远端 Vitis 阶段。
    if dict_route_contract_gate["status"] == PASS_STATUS:

        # dispatch_remote_vitis_gate 会按单机或 split 拓扑返回实际远端结果。
        list_remote_results = _dispatch_remote_vitis_gate(  # 根据拓扑选择真正执行的远端 Vitis 阶段
            dict_gates,  # 用于回填 remote_vitis_acceptance 的 gate 聚合器
            namespace_args,  # 提供服务器、版本和并行度等 CLI 配置
            list_remote_specs,  # 当前批次确实要送去远端执行的样例清单
            split_requested=bool_split_remote_requested,  # 命中 build/validate 双机拓扑时走 split 路径
            remote_requested=bool_remote_requested,  # 命中任一真实远端路径时允许实际发车
        )

    # 路由契约失败时，目录契约和 board gate 仍要报告“未执行或被阻断”的事实形态。
    else:

        # 这里显式回落到空结果集，避免后续目录契约读取旧值。
        list_remote_results = []  # 未执行远端时的空结果集

    # remote_directory_contract 负责核对远端 run/backups 产物边界。
    dict_remote_directory_gate = _remote_directory_contract_gate(  # 核对远端 runs/backups 产物是否落在契约边界内
        list_remote_results,  # 实际执行后拿回来的远端样例结果集合
        remote_requested=bool_remote_requested,  # 只有真实远端请求才应留下对应目录痕迹
    )

    # 保存远端目录契约 gate，确保最终报告能回溯产物边界检查结果。
    dict_gates["remote_directory_contract"] = dict_remote_directory_gate  # 远端目录契约 gate

    # remote_board_acceptance 需要结合执行形态、声明分区和上游 Vitis 结果生成 board 事实。
    bool_single_server_board_requested = bool(bool_remote_requested and not bool_split_remote_requested)  # 是否允许单机上板

    # 基于单机上板资格和声明分区生成 remote_board_acceptance 载荷。
    dict_remote_board_gate = _remote_board_acceptance_gate(  # 组装真实上板阶段的最终 gate 载荷
        namespace_args.server,  # 单机上板路径真正连接的服务器名
        namespace_args.readiness,  # 决定 board 阶段跑 smoke 还是完整 acceptance
        vitis_version=namespace_args.vitis_version,  # 把用户锁定的版本继续透传到 board 阶段
        remote_requested=bool_single_server_board_requested,  # 只有单机路径才会真正触发 board 执行
        remote_vitis_gate=dict_gates.get("remote_vitis_acceptance"),  # 提供上游 remote_vitis_acceptance 的通过/失败事实
        board_partition=dict_board_partition,  # 提供 board 必验、豁免和非法声明的拆分结果
        selected_specs=list_remote_specs,  # 仅对本轮远端选择命中的样例尝试上板
        parallelism=namespace_args.remote_parallelism,  # 控制真实上板阶段的远端并发上限
    )

    # 保存 board gate，供最终 confidence 和 family coverage 继续消费。
    dict_gates["remote_board_acceptance"] = dict_remote_board_gate  # 汇总给最终报告的真实上板阶段结果

    # 返回远端 Vitis acceptance 结果集合，供最终 payload 记录。
    return list_remote_results

# _dispatch_remote_vitis_gate 根据拓扑执行远端 Vitis acceptance。
def _dispatch_remote_vitis_gate(
    dict_gates: dict[str, dict[str, Any]],
    namespace_args: argparse.Namespace,
    list_remote_specs: list[str],
    *,
    split_requested: bool,
    remote_requested: bool,
) -> list[dict[str, Any]]:
    """执行单服务器或 split 拓扑的远端 Vitis acceptance。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。
        namespace_args: 已解析的 CLI 参数。
        list_remote_specs: 本轮远端验收选中的 example spec 列表。
        split_requested: 是否请求 build/validate 分离拓扑。
        remote_requested: 是否请求任何远端验收。

    返回:
        远端 Vitis acceptance 的结果集合。
    """

    # split 拓扑优先于单机路径，因为它会显式占用两台远端机器。
    if split_requested:

        # split 拓扑会返回双机远端结果和首个失败样例现场。
        dict_remote_vitis_gate = _run_split_remote_acceptance(  # 运行 split 双机路径的远端 Vitis 验收批次
            namespace_args.build_server,  # 提供负责综合与链接的 build 服务器名
            namespace_args.validate_server,  # 提供负责校验与运行的 validate 服务器名
            namespace_args.readiness,  # 指定远端只跑 smoke 还是完整 acceptance
            list_remote_specs,  # 当前批次确实要远端执行的样例名集合
            vitis_version=namespace_args.vitis_version,  # 把用户锁定的版本继续传给远端脚本
            parallelism=namespace_args.remote_parallelism,  # 限制 split 批次一次能并发多少样例
        )

        # 保存 split 远端 gate，供目录契约、board gate 和最终报告消费。
        dict_gates["remote_vitis_acceptance"] = dict_remote_vitis_gate  # 汇总 split 双机路径返回的 remote_vitis_acceptance 载荷

        # 返回 split 拓扑实际执行的远端结果。
        return dict_remote_vitis_gate.get("results", [])

    # 单机远端路径只在 split 未命中且用户确实请求远端时执行。
    if remote_requested:

        # 单机路径返回 link 预检和逐样例 Vitis 验收结果。
        dict_remote_vitis_gate = _run_remote_acceptance(  # 在单服务器拓扑下完成 link 预检和逐样例 Vitis 验收
            namespace_args.server,  # 把单机模式唯一的执行主机传给远端包装脚本
            namespace_args.readiness,  # 决定单机模式只做 smoke 还是继续完整验收
            list_remote_specs,  # 这批样例会在同一台机器上顺序或并行跑完
            vitis_version=namespace_args.vitis_version,  # 若用户锁版则要求远端脚本使用这版工具链
            parallelism=namespace_args.remote_parallelism,  # 控制单机模式下一次最多同时发出的样例任务数
        )

        # 保存单机远端 gate，供下游目录契约、board gate 和最终报告复用。
        dict_gates["remote_vitis_acceptance"] = dict_remote_vitis_gate  # 汇总单机路径返回的 remote_vitis_acceptance 载荷

        # 返回单服务器实际执行的远端结果。
        return dict_remote_vitis_gate["results"]

    # 未请求远端时不产生 remote_vitis_acceptance gate。
    return []

# _coverage_entries_from_gate 把远端结果转换成 family coverage 输入。
def _coverage_entries_from_gate(dict_gate: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    """从远端 gate 结果中提取 family coverage 需要的扁平条目。

    参数:
        dict_gate: remote_vitis_acceptance 或 remote_board_acceptance 载荷。
        phase: 当前条目来源阶段，取值为 vitis 或 board。

    返回:
        包含 example_spec、phase、status 的扁平列表。
    """

    # 只保留 family coverage 真正需要的三个字段，忽略非字典残留项。
    return [
        {"example_spec": dict_item.get("example_spec"), "phase": phase, "status": dict_item.get("status")}
        for dict_item in dict_gate.get("results", []) or []
        if isinstance(dict_item, dict)
    ]

# _record_family_coverage_gate 汇总远端 Vitis 和 board coverage。
def _record_family_coverage_gate(
    dict_gates: dict[str, dict[str, Any]],
    coverage_mode: str,
) -> None:
    """汇总远端 Vitis 和 board 结果并执行 family coverage gate。

    参数:
        dict_gates: confidence loop 的 gate 聚合字典。
        coverage_mode: 本轮远端覆盖模式。

    返回:
        无。
    """

    # 先读取远端 Vitis gate，后续按阶段把条目扁平化。
    dict_remote_vitis_gate = dict_gates.get("remote_vitis_acceptance", {})  # 提供 family coverage 读取的 Vitis 阶段聚合 gate

    # 再读取远端 board gate，供 tier1 双阶段覆盖闭合使用。
    dict_remote_board_gate = dict_gates.get("remote_board_acceptance", {})  # 供 family coverage 读取的真实上板阶段 gate 载荷

    # Vitis 条目和 board 条目都要汇总进同一条 family coverage 输入列表。
    list_coverage_results = []  # 远端覆盖率扁平条目

    # 只有字典形态的 gate 才能安全提取 coverage 条目。
    if isinstance(dict_remote_vitis_gate, dict):

        # 先拼入 Vitis 阶段条目，保留双阶段来源。
        list_coverage_results.extend(_coverage_entries_from_gate(dict_remote_vitis_gate, "vitis"))

    # board gate 同样需要通过 example_spec/phase/status 三元组参与覆盖率闭合。
    if isinstance(dict_remote_board_gate, dict):

        # 再拼入 board 阶段条目，供 tier1 双阶段覆盖判定使用。
        list_coverage_results.extend(_coverage_entries_from_gate(dict_remote_board_gate, "board"))

    # family coverage gate 会把两个阶段的扁平条目重新聚合成最终闭合结果。
    dict_remote_family_coverage = _remote_family_coverage_gate(  # 用双阶段条目重新闭合远端 family coverage
        list_coverage_results,  # 已扁平化的远端 Vitis 与 board 条目
        coverage_mode=coverage_mode,  # 当前要求的 coverage 解释模式
    )

    # 保存 family coverage gate，供最终 confidence 汇总远端闭合状态。
    dict_gates["remote_family_coverage"] = dict_remote_family_coverage  # 汇总远端双阶段覆盖闭合后的最终结论

# _build_confidence_payload 构造最终 JSON 报告。
def _build_confidence_payload(dict_config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """根据所有 gate 结果生成最终 confidence payload 和退出码。

    参数:
        dict_config: 最终报告所需的运行目录、gate、样例、远端结果和远端状态。

    返回:
        最终 JSON payload 和 CLI 退出码。
    """

    # 先冻结“是否请求过远端”的事实，供最终结论汇总使用。
    bool_remote_requested = bool(dict_config["remote_requested"])  # 汇总最终 scope 时是否应按“已请求远端”解释

    # 再冻结 skip_remote 事实，避免后续汇总直接回看裸配置。
    bool_remote_skipped = bool(dict_config["remote_skipped"])  # 汇总最终 scope 时是否应保留 skip_remote 事实

    # 基于全部 gate 和远端事实计算最终结论与退出码。
    tuple_confidence_outcome = _confidence_outcome(  # 计算最终 confidence 结论与 CLI 退出码
        dict_config["dict_gates"],  # 供结论汇总器审阅的全部 gate 原始结果
        remote_requested=bool_remote_requested,  # 让结论汇总器区分是否真的发起过远端验收
        remote_skipped=bool_remote_skipped,  # 让结论汇总器保留用户显式跳过远端的事实
    )

    # 读取最终状态字符串，供 payload 输出 confidence_status。
    str_confidence_status = tuple_confidence_outcome[0]  # 最终 confidence 状态

    # 读取最终覆盖范围字符串，供 payload 输出 confidence_scope。
    str_confidence_scope = tuple_confidence_outcome[1]  # 最终 confidence 范围

    # 读取仍需提示给用户的残余风险列表。
    list_residual_risks = tuple_confidence_outcome[2]  # 残余风险列表

    # 读取最终需要返回给 shell 的进程退出码。
    int_returncode = tuple_confidence_outcome[3]  # 透传给调用进程的退出码

    # payload 只保留 CLI 需要输出和写盘的最终审查信息。
    dict_payload = {  # 最终 confidence JSON 载荷
        "version": 1,  # 供读取方判定当前 JSON 协议结构版本
        "confidence_status": str_confidence_status,  # 直接展示给调用方的总体结论状态
        "confidence_scope": str_confidence_scope,  # 说明结论只覆盖本地还是已经覆盖到远端
        "run_root": str(dict_config["path_run_root"]),  # 关联本轮报告目录以便回查落盘产物
        "gates": dict_config["dict_gates"],  # 保留全部 gate 原始结果供审计和追溯
        "example_specs": dict_config["list_example_specs"],  # 记录本地样例阶段真实触达的 spec 集合
        "remote_example_specs": dict_config["list_remote_specs"],  # 记录远端阶段计划覆盖的 spec 集合
        "remote_coverage_mode": dict_config["remote_coverage_mode"],  # 指出远端采用的 coverage 解释模式
        "remote_results": dict_config["list_remote_results"],  # 保留远端 Vitis 验收逐样例回包
        "residual_risks": list_residual_risks,  # 汇总最终仍未消解的风险说明
    }

    # 返回最终 JSON 载荷和与之对应的 CLI 退出码。
    return dict_payload, int_returncode

# _write_payload_outputs 写入可选 JSON 文件并输出 stdout 协议载荷。
def _write_payload_outputs(dict_payload: dict[str, Any], json_out: str | None) -> None:
    """将最终 confidence payload 写入文件并输出 CLI JSON 协议。

    参数:
        dict_payload: 最终 confidence JSON 载荷。
        json_out: 可选 JSON 输出路径。

    返回:
        无。
    """

    # 调用方显式要求落盘时，先把 JSON 写到治理允许的目标路径。
    if json_out:

        # 先把调用方提供的路径解析到治理允许的 reports 边界内。
        path_output = _resolve_json_output(json_out)  # 受治理约束的 JSON 输出路径

        # 创建输出目录，允许调用方传入新的 reports 子路径。
        path_output.parent.mkdir(parents=True, exist_ok=True)

        # 写入格式化 JSON，便于人工审查和 CI 附件保存。
        path_output.write_text(
            json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # stdout 已在模块 docstring 声明 JSON 协议，这里直接输出单个 JSON 文档。
    sys.stdout.write(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n")

# main 串联本地 gate、远端 gate 和最终 JSON 输出协议。
def main(argv: list[str] | None = None) -> int:
    """执行本地 gate、可选远端 gate，并输出最终 confidence JSON。

    参数:
        argv: 可选 CLI 参数列表；为空时使用 sys.argv。

    返回:
        confidence loop 的进程退出码。
    """

    # 先固定终端编码，避免中文治理报告在 Windows 终端乱码。
    _configure_text_streams()

    # 先解析 CLI 参数，固定本轮治理请求的执行配置。
    namespace_args: argparse.Namespace = _build_argument_parser().parse_args(argv)  # 当前 CLI 调用参数

    # 为本轮执行创建独立报告目录，避免并发运行互相覆盖。
    path_run_root = _create_run_root()  # 当前这轮治理专属的 confidence 报告目录

    # dict_gates 统一收集所有本地、治理和远端 gate 的结构化结果。
    dict_gates: dict[str, dict[str, Any]] = {}  # 本轮所有 gate 聚合结果

    # 先跑本地命令型 gate，尽早暴露依赖或执行入口问题。
    _run_local_command_gates(dict_gates, namespace_args)

    # 先补上静态项目 gate，尽早暴露依赖与静态事实问题。
    _run_static_project_gates(dict_gates)

    # core 依赖未满足时，治理 gate 只会重复报告“无法形成高信心”的已知事实。
    if _dependency_failure_blocks_governance(dict_gates):

        # 依赖明确阻断时，把治理 gate 统一记成 skipped，避免昂贵 fail path。
        _record_skipped_governance_gates(dict_gates)

    # 依赖准备完成后，继续执行完整治理链。
    else:

        # 再跑治理脚本 gate，确认 AGENTS、docs 和目录契约没有破损。
        _run_governance_gates(dict_gates, namespace_args.gate_timeout_s)

    # 本地样例 gate 会给出最终 payload 需要记录的本地覆盖范围。
    list_example_specs = _run_example_gates(dict_gates, path_run_root)  # 本地样例阶段最终确认覆盖到的 spec 列表

    # 远端样例解析器同时返回样例列表和 coverage 模式。
    tuple_remote_spec_selection = _resolve_remote_example_specs(  # 解析远端样例选择与 coverage 模式
        namespace_args.example_spec,  # 用户显式点名的远端样例选择输入
        namespace_args.remote_coverage,  # 用户要求的远端覆盖率模式
    )

    # 元组第一个元素是本轮远端验收要覆盖的样例列表。
    list_remote_specs = tuple_remote_spec_selection[0]  # 远端阶段真正计划覆盖的 spec 列表

    # 元组第二个元素是最终报告要记录的远端覆盖模式。
    str_remote_coverage_mode = tuple_remote_spec_selection[1]  # 最终报告需要记录的远端 coverage 模式

    # 先跑远端 gate，收集 Vitis、目录契约和 board 的结构化结果。
    list_remote_results = _run_remote_gates(dict_gates, namespace_args, list_remote_specs)  # 真实远端结果列表

    # 再基于远端结果闭合 family coverage。
    _record_family_coverage_gate(dict_gates, str_remote_coverage_mode)

    # split 请求状态会影响最终 confidence 是否算作“请求过真实远端”。
    bool_has_split_servers = bool(namespace_args.build_server and namespace_args.validate_server)  # 最终 payload 判断是否存在 split 拓扑线索

    # 只有双机齐备且未显式 skip_remote，split 才算真正请求。
    bool_split_remote_requested = bool(bool_has_split_servers and not namespace_args.skip_remote)  # 最终 payload 判断是否把本轮认定为真实 split 请求

    # 单机或 split 任一成立，都表示本轮至少给出了一个远端目标。
    bool_has_any_remote_target = bool(namespace_args.server or bool_split_remote_requested)  # 最终 payload 判断配置里是否至少出现过一个远端目标

    # 只有存在真实远端目标且未显式 skip_remote，才算请求远端验收。
    bool_remote_requested = bool(bool_has_any_remote_target and not namespace_args.skip_remote)  # 最终 payload 判断本轮是否真的发起过远端验收

    # 把最终汇总器需要的上下文字段整理成显式配置字典。
    dict_payload_config = {  # 交给最终 payload 汇总器的显式配置字典
        "path_run_root": path_run_root,  # 提供本轮报告目录给最终 JSON 的 run_root 字段
        "dict_gates": dict_gates,  # 提供全部 gate 的原始结构化结果给最终汇总器
        "list_example_specs": list_example_specs,  # 提供本地样例阶段真实覆盖到的 spec 集合
        "list_remote_specs": list_remote_specs,  # 提供远端阶段计划覆盖的 spec 集合
        "remote_coverage_mode": str_remote_coverage_mode,  # 提供最终报告要声明的 coverage 语义
        "list_remote_results": list_remote_results,  # 提供远端 Vitis 验收逐样例回包
        "remote_requested": bool_remote_requested,  # 提供是否真正发起过远端验收的事实
        "remote_skipped": bool(namespace_args.skip_remote),  # 提供用户是否显式跳过远端阶段的事实
    }

    # 组装最终 payload 所需上下文，然后交给汇总器统一计算状态和退出码。
    tuple_payload_result = _build_confidence_payload(dict_payload_config)  # 让最终汇总器计算 JSON 载荷与 CLI 退出码

    # 元组第一个元素是要输出和可选写盘的最终 JSON 载荷。
    dict_payload = tuple_payload_result[0]  # 供 stdout 输出与可选写盘复用的最终报告载荷

    # 元组第二个元素是 CLI 需要返回给调用方的退出码。
    int_returncode = tuple_payload_result[1]  # 当前 CLI 运行最终要返回给 shell 的退出码

    # 写入可选报告文件并输出 CLI JSON 协议载荷。
    _write_payload_outputs(dict_payload, namespace_args.json_out)

    # 返回最终 confidence 退出码。
    return int_returncode

# 脚本直接运行时把最终 gate 退出码透传给调用进程。
if __name__ == "__main__":

    # 抛出入口执行结果，保持 CLI 退出码可见。
    raise SystemExit(main())
