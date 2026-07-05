#!/usr/bin/env python3
"""执行远端 HLS link 探针并生成验收报告。"""

# 启用后续类型标注所需的解释器特性。
from __future__ import annotations

# 导入 link 模式类型标注所需的标准库能力。
import argparse
from typing import Any

# 复用远端验收通用状态、运行目录和报告写入能力。
from remote_acceptance_common import (
    FAILED_STATUS,
    PASS_STATUS,
    _new_run_dir,
    _reject_decode_noise,
    _write_report,
)

# link 模式只负责验证远端基础链路和 Python 执行环境。
def _run_link_mode(
    _args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    plan: list[str],
    topology: dict[str, Any],
) -> dict[str, Any]:
    """执行远端 link 探针并返回结构化验收结果。

    参数:
        _args: 预留的命令行参数对象；当前 link 模式不直接读取它。
        config: 当前验收流程使用的配置字典。
        helper: 负责执行远端命令的 SSH 辅助对象。
        plan: 当前验收阶段的步骤列表。
        topology: 已解析的远端拓扑信息。

    返回:
        返回 link 模式生成的结构化验收结果。
    """

    # 为本次 link 探针创建独立运行目录。
    path_run_dir = _new_run_dir(config, "link")  # link 模式运行目录

    # 先验证远端 SSH、工作区和基础工具链可访问。
    helper.preflight(topology["server"])

    # 执行最小 link 探针命令，后续用标记校验输出完整性。
    str_output = helper.exec(topology["server"], list(config["link_probe_command"]))  # link 探针输出

    # 乱码或解码提示意味着远端命令输出不可靠。
    _reject_decode_noise(str_output)

    # link 探针必须同时给出状态、主机、目录和 Python 信息。
    tuple_required = ("HLS_REMOTE_LINK_OK", "host=", "pwd=", "python=")  # 必需输出标记

    # 缺失任意标记都视为 link 验收失败。
    list_missing_markers = [item for item in tuple_required if item not in str_output]  # 缺失标记列表

    # link 模式只区分 passed/failed，阻塞原因由上层验收流程处理。
    str_status = PASS_STATUS if not list_missing_markers else FAILED_STATUS  # link 模式验收状态

    # 分步构建报告，避免大字典遮住字段含义。
    dict_result: dict[str, Any] = {}  # link 模式报告内容

    # 记录 link 探针最终状态。
    dict_result["status"] = str_status  # link 验收状态

    # 固定报告模式，便于上层聚合区分验收阶段。
    dict_result["mode"] = "link"  # 验收模式名称

    # 保留实际执行探针的远端服务器标识。
    dict_result["server"] = topology["server"]  # 远端服务器名称

    # 保留解析后的拓扑信息，便于后续审计路由选择。
    dict_result["topology"] = topology["topology"]  # 远端拓扑名称

    # 记录本次验收证据所在的本地运行目录。
    dict_result["run_dir"] = str(path_run_dir)  # link 运行目录文本

    # 保存执行计划，便于报告还原验收步骤。
    dict_result["steps"] = plan  # 计划步骤列表

    # 保存远端命令原始输出，供失败诊断复核。
    dict_result["output"] = str_output  # link 探针原始输出

    # 明确列出缺失标记，供调用方生成错误说明。
    dict_result["missing_markers"] = list_missing_markers  # 缺失输出标记

    # 标明该验收路径确实经过 erie-remote-ssh。
    dict_result["uses_erie_remote_ssh"] = True  # 是否使用远端 SSH 工具

    # 报告文件作为后续验收和审计证据。
    _write_report(path_run_dir, dict_result)

    # 返回结构化结果给上层 remote_vitis_acceptance 流程。
    return dict_result
