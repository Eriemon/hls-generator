#!/usr/bin/env python3
"""提供远端验收入口共享的状态、配置、探测和报告工具。"""

# 未来注解用于保持类型标注不触发运行时导入。
from __future__ import annotations

# 标准库导入覆盖 CLI 参数、路径、压缩包、远端 shell 和进程执行。
import argparse
import base64
import datetime as dt
import io
import json

# 系统路径、正则和 shell 引号用于本地配置解析与远端命令拼装。
import os
import re
import shlex
import shutil

# site 用于直接运行入口时追加 skill 本地导入目录。
import site
import subprocess
import sys
import tarfile
import time
import uuid

# dataclass 标记 helper 是显式初始化的状态容器。
from dataclasses import dataclass

# 本地和远端路径分别使用 Path 与 PurePosixPath 表达。
from pathlib import Path, PurePosixPath

# Any 只保留在远端 JSON/profile 这类动态边界。
from typing import Any

# 当前 remote 脚本目录用于兼容同目录短导入。
MODULE_DIR = Path(__file__).resolve().parent  # remote 脚本目录

# skill 根目录用于直接运行子入口时导入 integration/runtime 包。
SKILL_ROOT = Path(__file__).resolve().parents[3]  # erie-hls-generator 技能根目录

# 直接执行旧入口时仍需兼容历史短模块导入路径。
site.addsitedir(str(MODULE_DIR))

# runtime 与 integration 包位于 skill 根目录之下。
site.addsitedir(str(SKILL_ROOT))

# HLS workflow adapter 用于生成远端验收前置工件。
from integration.hls_adapter import run_hls_workflow

# board acceptance 配置负责渲染 host scaffold 与运行 profile。
from runtime.hls_generator.board_acceptance import (
    BOARD_RUNNABLE_PROFILE,
    board_acceptance_config,
    resolve_host_template_path,
)

# U55C 平台 payload 工具只处理本地 payload 校验和归档。
from runtime.hls_generator.board_platform_payload import (
    U55C_PLATFORM_NAME,
    default_local_u55c_payload_root,
    prepare_local_u55c_platform_archive,
    validate_local_board_platform_payload,
)

# runtime 配置函数读取 skill 治理配置和 Vitis 超时阈值。
from runtime.hls_generator.config import (
    remote_validation_config,
    repo_root,
    skill_config_path,
    skill_dependencies_config,
    skill_root,
    vitis_tool_timeout,
)

# 远端目录契约生成 runs/backups/conda 等标准路径。
from runtime.hls_generator.remote_directory_contract import remote_directory_layout_for_workdir

# 恢复工具用于从历史 run id 和远端输出补齐本地报告。
from runtime.hls_generator.remote_recovery import (
    field_from_equals_output,
    infer_target_part_from_platform_selection,
    recover_example_spec,
    recover_local_run_dir,
    resolve_recovery_target,
)

# 依赖检查保证远端验收前本地 skill 依赖完整。
from runtime.hls_generator.skill_dependencies import SkillDependencyError, require_skill_dependencies

# 用户配置读写保存 Vitis 版本和 board 平台选择。
from runtime.hls_generator.user_config import (
    get_board_platform_selection,
    get_vitis_selection,
    set_board_platform_selection,
    set_vitis_selection,
    user_config_path,
)

# readiness 等级定义由 Vitis 验收入口共享。
from runtime.hls_generator.validation import READINESS_LEVELS

# 成功状态被多个入口用于退出码判断。
PASS_STATUS = "passed"  # 远端验收通过状态

# dry-run 状态表示只输出计划不执行远端动作。
DRY_RUN_STATUS = "dry_run"  # 远端验收预演状态

# Vitis 工具缺失时保持 blocked，而不是降级为 passed。
BLOCKED_VITIS_STATUS = "blocked_vitis_server"  # Vitis 服务器阻塞状态

# 版本选择无法收敛时要求用户明确指定版本。
BLOCKED_VERSION_STATUS = "blocked_remote_version_choice"  # Vitis 版本选择阻塞状态

# profile 缺失或字段不完整时阻塞远端执行。
BLOCKED_PROFILE_STATUS = "blocked_remote_profile_config"  # 远端 profile 配置阻塞状态

# board 事实证据不足时阻塞硬件验收。
BLOCKED_BOARD_STATUS = "blocked_board_validation"  # board 验收阻塞状态

# 运行失败状态用于报告真实执行错误。
FAILED_STATUS = "failed"  # 远端验收失败状态

# UTF-8 提示用于 erie-remote-ssh 输出解码失败时给用户可执行修复建议。
UTF8_HINT = "Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 when calling erie-remote-ssh."  # UTF-8 环境修复提示

# board host 输出中的状态字段标记。
BOARD_STATUS_MARKER = "HLS_BOARD_STATUS"  # board host 状态行标记

# 承载可以直接展示给用户的远端验收异常。
class RemoteAcceptanceError(RuntimeError):
    """表示可展示给用户的远端验收失败。"""

@dataclass(init=False)

# 封装 erie-remote-ssh 的常用调用，统一 request 与 exec 行为。
class ErieHelper:
    """封装 erie-remote-ssh 的命令、请求和 detached job 调用。"""

    # 根据治理配置准备 erie-remote-ssh 的本地调用上下文。
    def __init__(self, config: dict[str, Any], timeout: int) -> None:
        """根据远端验收配置初始化 erie-remote-ssh 调用上下文。

        参数:
            config: 远端验收治理配置。
            timeout: 单次 erie-remote-ssh 操作超时秒数。

        返回:
            无业务返回值。

        异常:
            RemoteAcceptanceError: helper 脚本或 settings 文件缺失时抛出。
        """

        # 原始配置保留 python_env、server list 等调用参数。
        self.config = config  # 远端验收配置字典

        # 超时时间同时用于 exec、request 和 detached job 轮询。
        self.timeout = timeout  # erie 命令超时秒数

        # erie skill 根目录用于定位 remote_ssh.py。
        self.erie_skill_dir = Path(config["erie_skill_dir"])  # erie 远端命令脚本定位根目录

        # settings 文件控制 erie-remote-ssh 的本地私有配置。
        self.settings = Path(config["erie_settings_path"])  # erie settings 文件路径

        # 可选 server list config 会传给 remote_ssh.py 子命令。
        self.server_list_config = (  # server list 配置路径
            Path(config["erie_server_list_config"]).resolve() if config.get("erie_server_list_config") else None  # 可选 server list 绝对路径
        )

        # remote_ssh.py 是所有远端命令的唯一执行入口。
        self.script = self.erie_skill_dir / "scripts" / "remote_ssh.py"  # erie remote_ssh 脚本路径

        # 缺少 helper 时必须阻塞，避免调用错误环境。
        if not self.script.exists():

            # helper 缺失时立刻停止，避免后续所有 erie 调用都落空。
            raise RemoteAcceptanceError(f"> ERR: [Python] erie-remote-ssh helper was not found: {self.script}")

        # 缺少 settings 时要求用户先配置远端服务器。
        if not self.settings.exists():

            # settings 缺失时不允许继续执行任何远端验收命令。
            raise RemoteAcceptanceError(f"> ERR: [Python] erie-remote-ssh settings were not found: {self.settings}")

    # 预先跑完 discover/check/workspace-check，尽早暴露远端连通性问题。
    def preflight(self, server: str, *, settings: Path | None = None) -> None:
        """执行远端服务器发现、连通性和 workspace 预检。

        参数:
            server: 需要预检的 erie server 名称。
            settings: 可选覆盖 settings 文件路径。

        返回:
            无业务返回值。
        """

        # settings 覆盖用于历史 run 恢复和临时 overlay。
        path_active_settings = settings or self.settings  # 当前 erie settings 路径

        # 发现阶段刷新 erie-remote-ssh 可见服务器清单。
        self._run(["discover", "--settings", str(path_active_settings), "--json"])

        # list 输出用于确认 server 别名仍可解析。
        self._run(["list", "--settings", str(path_active_settings)])

        # check 验证 SSH 基础连接。
        self._run(["check", "--settings", str(path_active_settings), "--server", server])

        # workspace-check 验证远端 workspace 契约。
        self._run(
            [
                "workspace-check",
                "--settings",
                str(path_active_settings),
                "--server",
                server,
                "--timeout",
                str(self.timeout),
            ]
        )

    # 运行同步只读命令，并返回 erie 合并后的文本输出。
    def exec(self, server: str, command: list[str], *, settings: Path | None = None) -> str:
        """在远端服务器执行同步命令并返回合并输出。

        参数:
            server: 远端服务器名称。
            command: 传给 erie exec 的命令参数列表。
            settings: 可选覆盖 settings 文件路径。

        返回:
            str: erie-remote-ssh 合并 stdout/stderr 后的文本。
        """

        # exec 支持临时 settings，用于把只读探测绑定到指定 overlay。
        path_active_settings = settings or self.settings  # 本次 exec 使用的 settings 路径

        # exec 子命令不创建 request 文件，只执行只读探测或同步任务。
        return self._run(
            [
                "exec",
                "--settings",
                str(path_active_settings),
                "--server",
                server,
                "--timeout",
                str(self.timeout),
                "--",
                *command,
            ]
        )

    # 读取远端软件清单，为 profile 选择和就绪性判断提供原始信息。
    def scan_software(self, server: str, *, settings: Path | None = None) -> str:
        """扫描远端服务器软件清单并返回原始输出。

        参数:
            server: 远端服务器名称。
            settings: 可选覆盖 settings 文件路径。

        返回:
            str: scan-software 命令的合并输出文本。
        """

        # scan-software 也允许切到历史 overlay，复用同一份软件清单环境。
        path_active_settings = settings or self.settings  # 软件探测阶段使用的 settings 路径

        # scan-software 输出由 Vitis profile 选择逻辑消费。
        return self._run(
            [
                "scan-software",
                "--settings",
                str(path_active_settings),
                "--server",
                server,
                "--timeout",
                str(self.timeout),
            ]
        )

    # 创建 request 后立即执行，统一返回 request 文件路径。
    def request_and_run(
        self, settings: Path, server: str, operation: str, payload: list[str] | str, reason: str
    ) -> str:
        """创建 erie request 并按治理要求执行。

        参数:
            settings: erie settings 文件路径。
            server: 远端服务器名称。
            operation: 请求类型，支持 mkdir、delete 和 command。
            payload: 请求负载。
            reason: 写入 request 的人工可读原因。

        返回:
            str: 生成的 request 文件路径。

        异常:
            RemoteAcceptanceError: 请求类型不受支持或 erie 子命令失败时抛出。
        """

        # mkdir request 只创建远端目录。
        if operation == "mkdir":

            # 创建目录 request 后，stdout 会带回这次 request 的本地落盘路径。
            str_request_stdout = self._run(  # 保存 mkdir request 返回的 request 路径输出
                [
                    "request-mkdir",  # 生成远端目录创建 request 的子命令
                    "--settings",  # 指定 mkdir request 使用的 settings 选项
                    str(settings),  # mkdir request 要读取的本地 settings 文件
                    "--server",  # 把建目录请求绑定到目标远端服务器字段
                    server,  # 目录创建动作最终会发往这台远端机器
                    "--path",  # 声明下一项是这次要创建的远端目录路径
                    payload[0],  # 本次 mkdir 要创建的远端目录
                    "--reason",  # 声明下一项写入 mkdir request 的审计原因
                    reason,  # 把建目录原因写入 request 审计记录
                ]
            )

        # delete request 需要保留 recursive 开关位置。
        elif operation == "delete":

            # 先构造删除 request 的基础骨架，后面只在需要时插入递归开关。
            args = [  # delete request 的稳定参数骨架
                "request-delete",  # 生成远端删除 request 的子命令
                "--settings",  # 指向这次删除动作要读取的 settings 文件
                str(settings),  # 删除 request 继续沿用当前 overlay settings
                "--server",  # 声明删除动作要提交到哪台远端主机
                server,  # 真正执行删除的目标远端服务器名称
                    "--path",  # 声明下一项是这次要删除的远端路径
                payload[0],  # 本次 delete 要删除的远端路径
                "--reason",  # 下一项会记录“为什么要删这个路径”的审计文本
                reason,  # 把删除原因带进 request 的留痕字段
            ]

            # 递归删除必须显式透传 recursive 标记。
            if "--recursive" in payload:

                # recursive 标记插到 reason 前，保持 CLI 参数顺序稳定。
                args.insert(-2, "--recursive")

            # delete request 的 stdout 同样承载 request 路径。
            str_request_stdout = self._run(args)  # delete request 原始输出

        # command request 是远端验收 payload 的主要执行形式。
        elif operation == "command":

            # command request 需要把列表 payload 拼成单条 shell 命令。
            str_request_command = payload if isinstance(payload, str) else " ".join(payload)  # 提交给 request-command 的 shell 命令串

            # 创建命令 request 后，stdout 会回传 request 文件路径供后续执行。
            str_request_stdout = self._run(  # 保存命令 request 的 request 路径输出
                [
                    "request-command",  # 生成远端命令执行 request 的子命令
                    "--settings",  # 指向命令 request 要读取的 overlay settings
                    str(settings),  # 命令 request 继续复用当前 run 的 settings
                    "--server",  # 声明命令 payload 要提交到哪台远端主机
                    server,  # 远端 shell 命令最终运行在这台服务器上
                    "--reason",  # 下一项承接这次远端命令的操作说明，便于 request 审计复盘
                    reason,  # 把命令执行原因写入 request 审计字段
                    "--",  # 把后续内容视为远端 shell 命令正文而非 erie 选项
                    str_request_command,  # 需要在远端实际执行的 shell 命令
                ]
            )

        # 未知 request 类型必须立刻暴露给调用者。
        else:

            # operation 不在受支持集合内时直接拒绝，避免创建错误 request。
            raise RemoteAcceptanceError(f"> ERR: [Python] unsupported request operation: {operation}")

        # request stdout 中的路径用于后续 run-request 执行。
        str_request_path = _parse_request_path(str_request_stdout)  # 当前 request 文件的本地落盘路径

        # 幂等请求允许对超时状态进行一次保守重试。
        self._run_request_execute(
            settings,
            str_request_path,
            retries=1 if self._is_idempotent_request(operation, reason) else 0,
        )

        # 调用方需要记录 request 路径作为验收证据。
        return str_request_path

    # 上传本地 payload 到远端并立即执行 request。
    def request_upload_and_run(
        self, settings: Path, server: str, local_path: Path, remote_path: str, reason: str
    ) -> str:
        """创建上传 request，执行后返回 request 路径。

        参数:
            settings: erie settings 文件路径。
            server: 远端服务器名称。
            local_path: 需要上传的本地文件路径。
            remote_path: 远端目标路径。
            reason: 写入 request 的人工可读原因。

        返回:
            str: 生成并执行的 request 文件路径。
        """

        # 上传 request 会把本地 payload 送到远端活动 run，并带回 request 文件路径。
        str_request_stdout = self._run(  # 保存 upload request 的 request 路径输出
            [
                "request-upload",  # 生成本地文件上传 request 的子命令
                "--settings",  # 指向上传动作要读取的 overlay settings
                str(settings),  # 上传 request 继续沿用本轮 run 的 settings
                "--server",  # 声明文件会被推送到哪台远端服务器
                server,  # 本地 payload 最终要传输到这台远端主机
                "--local",  # 声明下一项是本地待上传 payload 的文件路径
                str(local_path),  # 需要上传的本地 payload 文件
                "--remote",  # 声明下一项是远端活动 run 内的目标落点
                remote_path,  # 远端活动 run 内的目标文件路径
                "--reason",  # 下一项承接本次上传动作写入 request 的审计说明
                reason,  # 把上传原因写入 request 的审计字段
            ]
        )

        # 解析出的 request 路径会写入验收报告。
        str_request_path = _parse_request_path(str_request_stdout)  # upload request 文件的本地路径

        # 上传 request 需要立即执行，确保后续远端命令能看到刚上传的 payload。
        self._run(
            [
                "run-request",  # 立即执行刚刚创建好的 upload request
                "--settings",  # 指向 upload request 执行阶段要读取的 settings
                str(settings),  # run-request 阶段继续复用上传前的同一份 overlay
                "--request",
                str_request_path,  # 本轮 upload 对应的 request 文件
                "--execute",
                "--timeout",  # 声明下一项是 detached job 提交阶段的超时秒数
                str(self.timeout),  # upload request 执行允许的最长秒数
            ]
        )

        # 返回 request 文件路径作为可追溯证据。
        return str_request_path

    # 创建 detached job，用于长时间远端编译或主机程序执行。
    def exec_detached(
        self,
        server: str,
        reason: str,
        command: str,
        *,
        settings: Path | None = None,
        # 自动化调用通过此字段声明 detached job 用途。
        task_purpose: str | None = None,
    ) -> dict[str, Any]:
        """启动远端 detached job 并返回 job 元数据。

        参数:
            server: 远端服务器名称。
            reason: 写入 detached job 的人工可读原因。
            command: 通过 bash -lc 执行的远端命令文本。
            settings: 可选覆盖 settings 文件路径。
            task_purpose: 可选 detached job 用途；自动化流程应显式传 `automated_test`。

        返回:
            dict[str, Any]: 包含 job_id、remote_job_dir、manifest 和 output 的元数据。
        """

        # detached job 允许切换到指定 overlay，确保日志和运行目录落在对应 run 下。
        path_active_settings = settings or self.settings  # detached job 使用的 settings 路径

        # 远端 detached job 始终通过固定的 bash -lc 三段参数解释命令文本。
        list_detached_shell_command = ["bash", "-lc", command]  # detached job 的 shell 执行参数组

        # 先组装 detached job 的固定命令骨架。
        list_command = [  # detached job 的底层命令参数序列
            "exec-detached",  # 提交远端 detached job 的子命令
            "--settings",  # 指向 detached job 运行时要读取的 overlay settings
            str(path_active_settings),  # detached job 日志与路径隔离都跟随这份 overlay
            "--server",  # 声明下一项是 detached job 的目标服务器字段
            server,  # detached job 将启动在这台远端服务器上
            "--reason",  # 声明下一项写入 detached job 的原因文本
            reason,  # 把 detached 提交原因写进远端审计信息
        ]

        # 自动化流程需要显式传入 task purpose，避免残留 job 被记成 user_initiated。
        if task_purpose:

            # 透传 detached job 用途，交给 erie-remote-ssh 选择残留 job 清理策略。
            list_command.extend(["--task-purpose", task_purpose])

        # 再追加 detached job 提交超时与远端 shell 命令。
        list_command.extend(
            [
                "--timeout",  # 下一项限定 detached job 提交这一步最多可等待多久
                str(self.timeout),  # detached job 提交阶段允许的最长秒数
                "--",  # 把后续参数整体交给远端 bash -lc 执行
                *list_detached_shell_command,  # 展开 detached job 固定 shell 参数组
            ]
        )

        # exec-detached 会回传 job_id、远端目录和 manifest 等可追溯字段。
        str_detached_output = self._run(list_command)  # 保存 detached job 提交后的元数据输出

        # job id 是后续轮询状态的主键。
        str_job_id = _field_from_output(str_detached_output, "job_id")  # detached job 唯一标识

        # remote job dir 保存远端 detached 日志和 manifest。
        str_remote_job_dir = _field_from_output(str_detached_output, "remote_job_dir")  # 远端 job 目录

        # manifest 文件路径可直接定位远端 job 的完整元数据。
        str_manifest = _field_from_output(str_detached_output, "manifest")  # detached job 的 manifest 落盘位置

        # 返回结构保持与历史调用方兼容。
        return {
            "job_id": str_job_id,
            "remote_job_dir": str_remote_job_dir,
            "manifest": str_manifest,
            "output": str_detached_output,
        }

    # 轮询 detached job 直到进入成功、失败、丢失或超时终态。
    def wait_for_job(
        self, server: str, job_id: str, *, settings: Path | None = None, poll_s: int = 10, max_wait_s: int | None = None
    ) -> dict[str, Any]:
        """轮询 detached job 直到成功、失败、丢失或超时。

        参数:
            server: 远端服务器名称。
            job_id: detached job 标识。
            settings: 可选覆盖 settings 文件路径。
            poll_s: 两次状态轮询之间的等待秒数。
            max_wait_s: 覆盖默认总等待秒数。

        返回:
            dict[str, Any]: 终态 status、原始 output 和 returncode。

        异常:
            RemoteAcceptanceError: 状态查询失败或等待超时时抛出。
        """

        # wait-for-job 允许针对历史 overlay 恢复轮询。
        path_active_settings = settings or self.settings  # 状态轮询使用的 settings 路径

        # deadline 控制整体等待时间，不由单次 status 命令决定。
        float_deadline = time.time() + float(max_wait_s or self.timeout)  # 轮询截止时间戳

        # 最近一次 status 输出用于超时或失败时报错。
        str_last_output = ""  # 最近一次 status 合并输出

        # status 子命令采用较短超时，避免单次轮询卡死。
        int_status_timeout = self._status_timeout()  # status 轮询超时秒数

        # detached job 运行期间持续轮询状态。
        while time.time() < float_deadline:

            # status 输出和退出码共同判断是否需要继续轮询。
            str_last_output, int_returncode = self._run_with_returncode(  # status 命令输出与退出码
                [
                    "status",  # 查询 detached job 当前状态的子命令
                "--settings",  # 声明下一项是 status 查询要读取的 settings 文件
                    str(path_active_settings),  # status 查询要读取的 overlay settings
                    "--server",  # 声明下一项是要查询 job 状态的远端服务器
                    server,  # 当前 job 所在的远端服务器
                    "--job",  # 声明下一项是这次要轮询状态的 detached job 编号
                    job_id,  # 当前正在等待的 detached job 编号
                    "--timeout",  # 下一项限定单次 status 查询最多等待多久
                    str(int_status_timeout),  # 单次 status 命令允许的最长秒数
                ]
            )

            # erie status 字段是最终状态判定来源。
            str_status = _field_from_output(str_last_output, "status")  # 当前轮询得到的 job 状态

            # 终态直接返回给调用方写入报告。
            if str_status in {"succeeded", "failed", "not_found"}:

                # 成功、失败和 not_found 都属于终态，直接交给上层处理。
                return {"status": str_status, "output": str_last_output, "returncode": int_returncode}

            # 非零退出码中只有短暂 SSH 失败允许继续轮询。
            if int_returncode != 0:

                # 短暂网络抖动允许进入下一轮轮询。
                if _is_transient_status_failure(str_last_output):

                    # 瞬态失败只延迟重试，不中断整个等待流程。
                    time.sleep(poll_s)

                    # 继续等待下一次 status 轮询结果。
                    continue

                # 非瞬态失败必须暴露真实 status 输出。
                raise RemoteAcceptanceError(
                    f"> ERR: [Python] erie-remote-ssh status command failed: {str_last_output.strip()}"
                )

            # 远端 job 未结束时等待下一个轮询周期。
            time.sleep(poll_s)

        # 超时后追加日志尾部，方便用户定位卡住阶段。
        str_tail = self.tail_log(server, job_id, settings=path_active_settings, lines=40)  # 超时诊断日志尾部

        # detached job 超时不能冒充失败或成功。
        raise RemoteAcceptanceError(
            f"> ERR: [Python] detached remote job {job_id} did not finish within {max_wait_s or self.timeout}s.\n"
            f"{str_tail}"
        )

    # 读取 detached job 的日志尾部，给超时或失败场景补充诊断材料。
    def tail_log(self, server: str, job_id: str, *, settings: Path | None = None, lines: int = 40) -> str:
        """读取 detached job 日志尾部作为诊断信息。

        参数:
            server: 远端服务器名称。
            job_id: detached job 标识。
            settings: 可选覆盖 settings 文件路径。
            lines: 需要读取的日志尾部行数。

        返回:
            str: tail-log 命令返回的日志尾部文本。
        """

        # tail-log 针对指定 overlay 读取日志，便于恢复历史 run。
        path_active_settings = settings or self.settings  # tail-log 查询所用的 settings 路径

        # tail-log 只读取日志，不改变远端状态。
        return self._run(
            [
                "tail-log",
                "--settings",
                str(path_active_settings),
                "--server",
                server,
                "--job",
                job_id,
                "--lines",
                str(lines),
                "--timeout",
                str(self._status_timeout()),
            ]
        )

    # 约束 status 轮询超时，避免单次状态查询无限挂起。
    def _status_timeout(self) -> int:

        """计算 status 轮询子命令使用的超时秒数。

        参数:
            无。

        返回:
            约束在 30 到 180 秒之间的轮询超时值。
        """

        # status 轮询超时比总超时更保守，避免单次查询卡死。
        return min(max(int(self.timeout), 30), 180)

    # 约束 run-request 执行超时，保证 request 执行窗口稳定。
    def _request_timeout(self) -> int:

        """计算 run-request 子命令使用的超时秒数。

        参数:
            无。

        返回:
            约束在 30 到 180 秒之间的 request 执行超时值。
        """

        # request 执行超时与 status 轮询共用同一安全边界。
        return min(max(int(self.timeout), 30), 180)

    # 该判断只依赖传入参数，不读取实例状态，因此保持为静态工具函数。
    @staticmethod

    # 识别哪些 request 可以在超时后安全重试一次，避免重复执行危险动作。
    def _is_idempotent_request(operation: str, reason: str) -> bool:

        """判断 request 是否可以安全重试。

        参数:
            operation: request 操作类型。
            reason: request 的人工可读原因。

        返回:
            可以安全重试时返回 True，否则返回 False。
        """

        # 归一化 reason，便于匹配允许重试的命令描述。
        str_normalized_reason = reason.lower()  # 归一化后的 request 原因

        # mkdir 和 delete 天然具备幂等性，可以直接允许重试。
        if operation in {"mkdir", "delete"}:

            # 目录创建和删除在治理流程里允许做一次超时重试。
            return True

        # command 只在明确列出的初始化步骤中允许保守重试。
        return operation == "command" and any(
            marker in str_normalized_reason
            for marker in (
                "initialize remote package payload",
                "prepare remote",
            )
        )

    # 执行 request 文件，并按需要在超时场景做有限次重试。
    def _run_request_execute(self, settings: Path, request_path: str, *, retries: int = 0) -> str:

        """执行 request 文件并返回最终输出。

        参数:
            settings: erie settings 文件路径。
            request_path: 待执行的 request 文件路径。
            retries: 允许的额外重试次数。

        返回:
            request 成功执行后的合并输出文本。

        异常:
            RemoteAcceptanceError: request 最终执行失败时抛出。
        """

        # run-request 使用专门的 request 超时，不直接复用总超时。
        int_timeout_s = self._request_timeout()  # run-request 超时秒数

        # run-request 参数顺序保持稳定，方便把失败命令原样复现出来。
        args = [  # run-request 的稳定命令骨架
            "run-request",  # 执行本地 request 文件的 erie 子命令
            "--settings",  # 指向 request 执行阶段要读取的 settings 文件
            str(settings),  # run-request 阶段读取的 settings 文件
            "--request",  # 指向这次真正要执行的 request 文件
            request_path,  # 这次要执行的 request 文件路径
                    "--execute",  # 让 run-request 立即执行而不是只生成计划
            "--timeout",  # 声明下一项是 run-request 执行阶段的超时秒数
            str(int_timeout_s),  # run-request 命令允许的最长秒数
        ]

        # 总尝试次数等于首次执行加上允许的重试次数。
        int_attempt_count = max(retries, 0) + 1  # run-request 最大尝试次数

        # 保留最后一次输出，用于最终失败时给出真实上下文。
        str_last_output = ""  # 最后一次 run-request 合并输出

        # 按治理允许的次数执行 request，并对超时场景做有限重试。
        for attempt in range(int_attempt_count):

            # 执行本轮 run-request，并记录输出与退出码供重试判断复用。
            str_combined_output, int_returncode = self._run_with_returncode(args, timeout_s=int_timeout_s)  # 本轮 run-request 输出与退出码

            # 请求成功后直接返回本次输出，不再进入后续重试判断。
            if int_returncode == 0:

                # 成功执行后把 request 输出直接交回调用方。
                return str_combined_output

            # 记录本轮失败输出，供最终异常消息复用。
            str_last_output = str_combined_output  # 最近一次失败的 run-request 输出

            # 只有明确的超时失败才允许继续尝试下一轮执行。
            if "timed out" in str_combined_output.lower() and attempt + 1 < int_attempt_count:

                # 可重试超时直接进入下一轮 attempt。
                continue

            # 非超时失败或已耗尽重试次数时结束循环。
            break

        # 超过允许尝试次数后，把最后一次失败输出原样上抛。
        raise RemoteAcceptanceError(
            f"> ERR: [Python] erie-remote-ssh command failed (run-request): {str_last_output.strip()}"
        )

    # 运行 erie 命令并在非零退出码时转成统一的远端验收异常。
    def _run(self, args: list[str]) -> str:

        """运行 erie 子命令并返回成功输出。

        参数:
            args: 传给 remote_ssh.py 的子命令参数列表。

        返回:
            erie 子命令成功时的合并输出文本。

        异常:
            RemoteAcceptanceError: 子命令返回非零退出码时抛出。
        """

        # 调用底层执行器，拿到 erie 子命令的原始结果。
        str_combined_output, int_returncode = self._run_with_returncode(args)  # erie 子命令输出与退出码

        # 非零退出码统一转成 RemoteAcceptanceError，便于上层直接报告。
        if int_returncode != 0:

            # 遇到永久失败时立即抛出，保留真实子命令名与原始输出。
            raise RemoteAcceptanceError(
                f"> ERR: [Python] erie-remote-ssh command failed ({args[0]}): {str_combined_output.strip()}"
            )

        # 成功时返回合并输出供调用方继续解析字段。
        return str_combined_output

    # 运行 remote_ssh.py，并同时返回输出文本与退出码。
    def _run_with_returncode(self, args: list[str], *, timeout_s: int | None = None) -> tuple[str, int]:

        """运行 remote_ssh.py 并返回输出及退出码。

        参数:
            args: 传给 remote_ssh.py 的子命令参数列表。
            timeout_s: 可选覆盖的子命令超时秒数。

        返回:
            由合并输出文本和退出码组成的二元组。
        """

        # 复制当前进程环境，避免污染全局 shell 状态。
        dict_process_env = os.environ.copy()  # remote_ssh.py 子进程环境

        # 合并治理配置里的 Python 环境变量，保证 erie 子进程一致性。
        dict_process_env.update(self.config["python_env"])

        # 子进程总是通过当前 Python 解释器调用 remote_ssh.py。
        list_command = [sys.executable, str(self.script), *self._with_config(args)]  # remote_ssh.py 完整命令

        # 给 remote_ssh.py 额外预留 10 秒收尾窗口，避免刚超时就被本地强杀。
        int_process_timeout = max(int(timeout_s if timeout_s is not None else self.timeout) + 10, 30)  # 子进程硬超时上限秒数

        # subprocess.run 的执行开关集中成单独字典，避免多行关键字参数分散语义注释。
        dict_subprocess_run_kwargs = dict(  # remote_ssh.py 子进程执行参数
            capture_output=True,  # 同时捕获 stdout 与 stderr 供统一诊断
            text=True,  # 让 subprocess 直接返回字符串而不是 bytes
            encoding="utf-8",  # 按 UTF-8 解码 remote_ssh.py 的标准文本输出
            errors="replace",  # 遇到非法字节时用替代字符保留原始输出结构
            env=dict_process_env,  # 把治理配置要求的 Python 环境变量透传给子进程
            timeout=int_process_timeout,  # 让本地 subprocess 按治理计算出的硬超时自动收尾
            check=False,  # 失败时由上层统一转换成远端验收异常
        )

        # 执行 remote_ssh.py 子进程，并保留完整 CompletedProcess 供后续拼接输出。
        completed_process_result: subprocess.CompletedProcess[str] = subprocess.run(  # remote_ssh.py 子进程执行结果
            list_command,  # 完整的 Python + remote_ssh.py 调用命令
            **dict_subprocess_run_kwargs,  # 展开统一整理好的 subprocess 运行参数
        )

        # 把 stdout 与 stderr 合并，便于统一做字段解析和错误展示。
        str_combined_output = (completed_process_result.stdout or "") + (  # 合并后的 remote_ssh.py 输出文本
            completed_process_result.stderr or ""  # 把 stderr 也并入同一份诊断正文
        )

        # 输出里如有 UTF-8 修复提示，必须立刻转成显式失败。
        _reject_decode_noise(str_combined_output)

        # 返回输出与退出码，让上层自己决定是否抛异常。
        return str_combined_output, completed_process_result.returncode

    # 在启用 server list config 时，把 config 参数插到 erie 子命令前缀里。
    def _with_config(self, args: list[str]) -> list[str]:

        """按需把 server list config 注入到 erie 子命令参数中。

        参数:
            args: 原始 erie 子命令参数列表。

        返回:
            已按需插入 --config 的参数列表。
        """

        # 缺少 server list config 时保持原始参数不变。
        if not self.server_list_config or not args:

            # 缺少 server list config 时返回参数副本，避免调用方意外原地修改原列表。
            return list(args)

        # 仅在存在配置文件时插入 --config，保持原始子命令名不变。
        return [args[0], "--config", str(self.server_list_config), *args[1:]]

# 统一解析单机模式和分离式模式对应的服务器参数。
def _resolve_topology(args: argparse.Namespace) -> dict[str, Any]:

    """把单机或分离式参数统一解析成拓扑字典。

    参数:
        args: 待解析的命令行参数命名空间。

    返回:
        统一后的拓扑字段字典。

    异常:
        ValueError: 用户同时传入互斥参数，或 split 拓扑缺少成对服务器参数时抛出。
    """

    # 识别是否走单机模式。
    bool_single = bool(getattr(args, "server", None))  # 是否声明了单机 server

    # 识别 split 模式里的 build 服务器参数。
    bool_split_build = bool(getattr(args, "build_server", None))  # 是否声明了 build_server

    # split 拓扑下是否明确提供了验证阶段要用的第二台服务器。
    bool_split_validate = bool(getattr(args, "validate_server", None))  # 用来判断 build/validate 参数是否成对出现

    # 单机模式和 split 模式互斥，不能混传。
    if bool_single and (bool_split_build or bool_split_validate):

        # 同时传单机与 split 参数会让拓扑含义不明确。
        raise ValueError("> ERR: [Python] use either --server or the pair --build-server/--validate-server, not both.")

    # split 模式必须成对提供 build 和 validate 服务器。
    if bool_split_build != bool_split_validate:

        # 缺少任一 split 参数都要直接阻塞。
        raise ValueError("> ERR: [Python] split topology requires both --build-server and --validate-server.")

    # split 参数齐全时返回 build/validate 分离拓扑。
    if bool_split_build and bool_split_validate:

        # 返回 split 拓扑下的主服务器、构建服务器和验证服务器信息。
        return {
            "topology": "split_build_validate",
            "server": str(args.build_server),
            "build_server": str(args.build_server),
            "validate_server": str(args.validate_server),
        }

    # 仅声明 --server 时回落到单机拓扑。
    if bool_single:

        # 单机模式只需要返回 topology 和 server。
        return {"topology": "single_server", "server": str(args.server)}

    # 两类参数都缺失时直接要求用户补齐。
    raise ValueError("> ERR: [Python] provide either --server or both --build-server and --validate-server.")

# 展开 settings 中的占位符并解析出稳定可用的绝对路径。
def _expand_settings_path(value: str, *, skill_dir: Path, settings_dir: Path) -> Path:

    """展开 settings 占位符并解析绝对路径。

    参数:
        value: 待展开的原始路径字符串。
        skill_dir: 用于替换 `${skill_dir}` 占位符的 skill 根目录。
        settings_dir: 用于解析相对路径的 settings 目录。

    返回:
        展开占位符后的绝对路径对象。
    """

    # 按约定顺序展开 skill/settings/home/cwd/project_root 等路径占位符。
    str_expanded_settings_path = (
        str(value)  # settings 里声明的原始路径文本
        .replace("${skill_dir}", str(skill_dir))  # 把 skill 根目录占位符替换成本地绝对路径
        .replace("${settings_dir}", str(settings_dir))  # 把 settings 所在目录占位符展开为真实目录
        .replace("${home}", str(Path.home()))  # 把 home 占位符展开成当前用户主目录
        .replace("${cwd}", str(Path.cwd()))  # 把 cwd 占位符展开成当前工作目录
        .replace("${project_root}", str(SKILL_ROOT.parents[1]))  # 把项目根占位符展开成仓库根目录
    )  # 展开占位符后的设置路径文本

    # 再交给 expanduser/expandvars 处理用户目录和环境变量。
    path_path = Path(os.path.expandvars(os.path.expanduser(str_expanded_settings_path)))  # 初步解析后的路径对象

    # 相对路径默认相对于 settings 所在目录解析。
    if not path_path.is_absolute():

        # 相对路径默认锚定在 settings 所在目录，避免 cwd 漂移影响解析结果。
        path_path = settings_dir / path_path  # 为相对 settings 路径补上稳定的父目录前缀

    # 最终返回标准化后的绝对路径。
    return path_path.resolve()

# 复制当前 run 使用的 server list，避免直接引用本地私有配置原件。
def _prepare_erie_server_list_copy(config: dict[str, Any]) -> Path:

    """复制本地 server list，避免直接引用私有配置原件。

    参数:
        config: 远端验收治理配置。

    返回:
        写入 reports 目录的 server list 副本路径。

    异常:
        RemoteAcceptanceError: settings 未声明 server list，或声明的源文件不存在时抛出。
    """

    # settings 文件是解析 default_server_list 的唯一来源。
    path_settings = Path(config["erie_settings_path"]).resolve()  # 已解析的 erie settings 路径

    # 读取 settings JSON，后续要从里面解析默认 server list 的声明路径。
    dict_settings = json.loads(path_settings.read_text(encoding="utf-8"))  # 已加载的 erie settings 配置字典

    # 从 settings.paths.default_server_list 读取 server list 的原始声明值。
    str_configured_server_list = str(  # settings 中声明的 server list 路径文本
        ((dict_settings.get("paths") or {}).get("default_server_list") or "")  # settings 中的 default_server_list 原始字段
    ).strip()

    # 未声明 default_server_list 时不允许继续生成副本。
    if not str_configured_server_list:

        # server list 缺失会让后续 overlay 无法稳定复现。
        raise RemoteAcceptanceError("> ERR: [Python] erie-remote-ssh settings do not define paths.default_server_list.")

    # 把 settings 中的 server list 路径展开成当前机器上的真实源文件位置。
    path_source_path = _expand_settings_path(  # 当前 run 要复制的 server list 源文件路径
        str_configured_server_list,  # settings 中声明的 server list 原始路径
        skill_dir=Path(config["erie_skill_dir"]).resolve(),  # 用 erie skill 根目录展开 ${skill_dir}
        settings_dir=path_settings.parent,  # 用 settings 文件所在目录展开相对 server list 路径
    )

    # 源文件不存在时必须阻塞，避免复制空路径。
    if not path_source_path.exists():

        # 直接上抛源文件缺失，提示用户修正本地私有配置。
        raise RemoteAcceptanceError(f"> ERR: [Python] erie-remote-ssh server list does not exist: {path_source_path}")

    # 所有 server list 副本统一落到 reports 的受管目录。
    path_config_copies_dir = repo_root() / "reports" / "remote-validation" / "_config_copies"  # 配置副本输出目录

    # 先创建副本目录，确保本轮 server list 快照可以稳定落盘。
    path_config_copies_dir.mkdir(parents=True, exist_ok=True)

    # 目标副本名带时间戳和随机后缀，避免覆盖历史证据。
    path_target_copy = (  # 当前 server list 快照要写入的唯一文件路径
        path_config_copies_dir  # 副本固定落到 remote-validation 的配置快照目录
        / f"{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}-server_list.local.json"  # 文件名同时编码 UTC 时间戳和随机后缀
    )

    # 复制原始 server list，供本轮验收留痕与复现。
    shutil.copy2(path_source_path, path_target_copy)

    # 返回副本路径，后续 overlay 只引用副本而不再触碰原件。
    return path_target_copy

# 根据 profile 组装远端 Vitis 工具探测命令文本。
def _vitis_probe_command_text(profile: dict[str, Any]) -> dict[str, str]:
    """根据 Vitis profile 生成远端工具探测脚本。

    参数:
        profile: 已选择的 Vitis 远端 profile 配置。

    返回:
        dict[str, str]: 包含 expected_tool 和 command_text 的探测上下文。
    """

    # profile 中的 expected_tool 是优先探测的 Vitis 命令名称。
    str_expected_tool = str(profile["expected_tool"])  # Vitis 期望工具名称

    # settings64.sh 是本轮 Vitis 探测的主环境入口。
    str_settings_script = str(profile["settings_script"])  # Vitis 主环境初始化脚本路径

    # 可选 env setup 只在 profile 显式配置时追加 source。
    str_env_setup_script = str(profile.get("env_setup_script") or "").strip()  # 附加环境脚本路径

    # 基础脚本先 source settings，再探测 expected_tool。
    str_command_text = _vitis_probe_base_script(str_settings_script, str_expected_tool)  # Vitis 基础探测脚本

    # profile 配置了附加环境脚本时，改用双 source 探测脚本。
    if str_env_setup_script:

        # 附加环境脚本存在时，重新拼装包含双 source 的探测脚本。
        str_command_text = _vitis_probe_base_script(  # 让 env setup 与 settings64.sh 在同一 shell 内共同生效
            str_settings_script,  # Vitis 主环境入口脚本
            str_expected_tool,  # 优先探测的目标命令名
            env_setup_script=str_env_setup_script,  # 额外补充的站点环境脚本
        )

    # expected_tool_path 允许用户指定绝对工具路径作为强约束。
    str_expected_tool_path = str(profile.get("expected_tool_path") or "").strip()  # Vitis 期望工具绝对路径

    # 显式工具路径存在时，补充绝对路径可执行性探测片段。
    if str_expected_tool_path:

        # 指定路径探测片段会保留在同一个远端 shell 中执行。
        str_command_text += _vitis_expected_tool_path_probe(str_expected_tool_path)  # Vitis 显式路径探测片段

    # fallback 探测覆盖新旧 Vitis 命令名称。
    str_command_text += "printf '\nfallback_vitis_run='; command -v vitis-run || true; "  # vitis-run fallback 探测片段

    # 旧版机器如果只暴露 vitis_hls，也要把那条命令采进探测证据里。
    str_command_text += "printf '\nfallback_vitis_hls='; command -v vitis_hls || true"  # 追加旧版 HLS 命令的 fallback 探测片段

    # 返回远端探测脚本与期望工具名，供上层执行和报告。
    return {
        "expected_tool": str_expected_tool,
        "command_text": str_command_text,
    }

# 生成 Vitis settings/env 初始化后的基础探测脚本。
def _vitis_probe_base_script(
    settings_script: str,
    expected_tool: str,
    *,
    env_setup_script: str = "",
) -> str:
    """生成 source 环境后探测 expected_tool 的 bash 片段。

    参数:
        settings_script: Vitis settings 脚本路径。
        expected_tool: 优先探测的工具命令名。
        env_setup_script: 可选附加环境脚本路径。

    返回:
        可直接交给 `bash -lc` 执行的探测脚本文本。
    """

    # settings source 片段必须先于所有 Vitis 命令探测。
    str_settings_source = (
        f"if [ -f {shlex.quote(settings_script)} ]; then "
        f"source {shlex.quote(settings_script)} >/dev/null 2>&1; fi; "
    )  # Vitis settings 脚本 source 片段

    # 附加环境为空时不生成额外 source 片段。
    str_env_source = ""  # Vitis 附加环境 source 片段

    # 附加环境脚本存在时，补上第二段 source 逻辑。
    if env_setup_script:

        # 这段 source 只在 profile 提供站点脚本时注入，用来补齐额外的 Vitis 环境前置条件。
        str_env_source = (
            f"if [ -f {shlex.quote(env_setup_script)} ]; then "
            f"source {shlex.quote(env_setup_script)} >/dev/null 2>&1; fi; "
        )  # 仅在存在 env_setup_script 时拼接的第二段 source 命令

    # expected_tool 探测输出采用 key=value 方便后续解析。
    str_tool_probe = f"printf 'expected_tool='; command -v {shlex.quote(expected_tool)} || true; "  # Vitis 工具命令探测片段

    # 返回完整的 source 与 expected_tool 探测片段。
    return str_settings_source + str_env_source + str_tool_probe

# 生成 expected_tool_path 的绝对路径探测脚本片段。
def _vitis_expected_tool_path_probe(expected_tool_path: str) -> str:
    """生成指定 Vitis 工具绝对路径的可执行性探测片段。

    参数:
        expected_tool_path: profile 中声明的绝对工具路径。

    返回:
        可追加到远端探测脚本末尾的 bash 片段。
    """

    # 绝对路径探测只在文件存在且可执行时输出路径。
    return (
        f"printf '\nexpected_tool_path='; if [ -x {shlex.quote(expected_tool_path)} ]; "
        f"then printf %s {shlex.quote(expected_tool_path)}; fi; "
    )

# 解析远端 Vitis 探测输出，抽取各候选工具路径字段。
def _parse_vitis_probe_output(output: str) -> dict[str, str]:
    """解析 Vitis 工具探测输出中的 key=value 字段。

    参数:
        output: 远端 Vitis 工具探测返回的原始文本。

    返回:
        包含 expected_tool 与各 fallback 路径字段的字典。
    """

    # 解析结果按首选工具、显式路径和两个 fallback 路径分槽保存。
    dict_probe_fields: dict[str, str] = {  # Vitis 工具探测字段
        "expected_tool": "",  # PATH 中首选工具命令的命中结果
        "expected_tool_path": "",  # profile 显式工具路径的命中结果
        "fallback_vitis_run": "",  # 新版 vitis-run 的命中结果
        "fallback_vitis_hls": "",  # 旧版 Vitis HLS 命令的命中结果
    }

    # 逐行扫描远端输出，只保留 key=value 形式的字段。
    for str_output_line in output.splitlines():

        # 非 key=value 行对工具路径选择没有帮助，直接跳过。
        if "=" not in str_output_line:

            # 跳过纯日志行，避免污染字段字典。
            continue

        # 每行只按第一个等号切分，避免路径里出现特殊字符时误切。
        str_field_name, str_field_value = str_output_line.split("=", 1)  # Vitis 探测字段和值

        # 只接收预先声明的字段名，避免意外日志键干扰解析。
        if str_field_name in dict_probe_fields:

            # 空白会影响后续路径存在性判断，因此统一裁剪。
            dict_probe_fields[str_field_name] = str_field_value.strip()  # Vitis 探测字段清理后取值

    # 返回清理后的所有工具候选字段。
    return dict_probe_fields

# 从 expected/fallback 候选里挑出最终使用的 Vitis 工具。
def _select_vitis_tool_path(expected_tool: str, probe_fields: dict[str, str]) -> dict[str, str]:
    """从 Vitis 探测候选中选择最终工具名称和路径。

    参数:
        expected_tool: profile 声明的首选工具名称。
        probe_fields: `_parse_vitis_probe_output` 解析得到的字段字典。

    返回:
        包含最终工具名和最终工具路径的选择结果字典。
    """

    # expected_tool 命令路径优先级最高。
    str_tool_path = probe_fields["expected_tool"]  # Vitis 已解析工具路径

    # resolved_tool 记录最终选择的命令名称。
    str_resolved_tool = expected_tool  # Vitis 最终工具名称

    # 用户显式绝对路径在 expected_tool 命令缺失时优先兜底。
    if not str_tool_path and probe_fields["expected_tool_path"]:

        # 用户显式工具路径优先于 fallback 命令。
        str_tool_path = probe_fields["expected_tool_path"]  # Vitis 显式工具路径

    # 新版本 vitis-run 是 expected_tool 失败后的首选 fallback。
    if not str_tool_path and probe_fields["fallback_vitis_run"]:

        # vitis-run 是新版本 Vitis CLI 的 fallback。
        str_tool_path = probe_fields["fallback_vitis_run"]  # vitis-run 命令 fallback 路径

        # fallback 命中时同步更新工具名称。
        str_resolved_tool = "vitis-run"  # Vitis fallback 工具名称

    # 旧版本 vitis_hls 作为最后一级 fallback。
    elif not str_tool_path and probe_fields["fallback_vitis_hls"]:

        # 旧版工具链里只暴露 vitis_hls 时，也要接受这条可执行路径。
        str_tool_path = probe_fields["fallback_vitis_hls"]  # 旧版 vitis_hls 在当前机器上命中的真实路径

        # 向最终报告明确这次是回落到旧版 HLS 命令名。
        str_resolved_tool = "vitis_hls"  # 报告里要展示的旧版工具名称

    # 返回最终确定的工具名称与工具路径。
    return {
        "resolved_tool": str_resolved_tool,
        "tool_path": str_tool_path,
    }

# 在远端执行 Vitis 探测脚本，并生成可审计的探测结果。
def _probe_vitis(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any]) -> dict[str, Any]:
    """在远端服务器探测 Vitis 命令并返回可审计结果。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        profile: 当前选中的 Vitis profile 配置。

    返回:
        包含探测状态、工具名、工具路径和原始输出的结果字典。
    """

    # 先根据 profile 组装远端工具探测脚本上下文。
    dict_command_context = _vitis_probe_command_text(profile)  # 本轮远端 Vitis 探测要执行的命令上下文

    # 期望工具名进入最终报告和 fallback 选择逻辑。
    str_expected_tool = dict_command_context["expected_tool"]  # profile 指定的首选工具名

    # shell 脚本文本由 erie exec 在远端执行。
    str_command_text = dict_command_context["command_text"]  # Vitis 探测 shell 脚本

    # erie exec 固定通过 bash -lc 执行探测脚本。
    list_command = ["bash", "-lc", str_command_text]  # 交给远端 bash -lc 执行的 Vitis 探测命令

    # 远端输出包含 expected 和 fallback 工具路径。
    # 执行远端探测命令，保留原始输出供报告和 fallback 判断复用。
    str_probe_output = helper.exec(server, list_command, settings=settings)  # 远端 shell 返回的 Vitis 探测正文

    # 探测输出里如出现解码噪声，立刻中止后续字段解析。
    _reject_decode_noise(str_probe_output)

    # key=value 解析结果用于工具优先级选择。
    dict_probe_fields = _parse_vitis_probe_output(str_probe_output)  # 已解析的工具候选字段

    # 解析出的工具名称和路径决定最终状态。
    dict_tool_selection = _select_vitis_tool_path(str_expected_tool, dict_probe_fields)  # 按 expected 与 fallback 优先级选出的工具结果

    # 最终工具名称用于解释 fallback 是否发生。
    str_resolved_tool = dict_tool_selection["resolved_tool"]  # 最终选中的工具名

    # 最终工具路径为空时说明服务器缺少 Vitis。
    str_tool_path = dict_tool_selection["tool_path"]  # 最终选中的工具路径

    # 返回最终状态以及报告所需的工具解析结果。
    return {
        "status": PASS_STATUS if str_tool_path else BLOCKED_VITIS_STATUS,
        "expected_tool": str_expected_tool,
        "resolved_tool": str_resolved_tool,
        "tool_path": str_tool_path,
        "output": str_probe_output,
    }

# 读取 lspci 证据，确认远端机器当前是否暴露 Xilinx FPGA 设备。
def _probe_fpga_presence(server: str, settings: Path, helper: ErieHelper) -> dict[str, Any]:

    """检查远端机器是否暴露 Xilinx FPGA 设备。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。

    返回:
        FPGA 存在性探测结果字典。
    """

    # 通过 lspci + grep 读取是否存在 Xilinx 设备证据。
    list_command = [
        "bash",  # 用 bash 承接多段 lspci/grep 探测逻辑
        "-lc",  # 通过登录 shell 运行整段 FPGA 探测脚本
        "if lspci | grep -iq 'xilinx'; then "
        "printf 'fpga_present=yes\\n'; lspci | grep -i 'xilinx' | head -n 12; "
        "else printf 'fpga_present=no\\n'; fi",
    ]

    # 保留 lspci 原始输出，供报告确认服务器此刻是否真的暴露 FPGA 设备。
    str_probe_output = helper.exec(server, list_command, settings=settings)  # 远端 lspci/grep 返回的 FPGA 证据正文

    # 先过滤掉乱码或 UTF-8 噪声，再继续状态判断。
    _reject_decode_noise(str_probe_output)

    # 根据 fpga_present 标记返回通过或阻塞状态。
    return {
        "status": PASS_STATUS if "fpga_present=yes" in str_probe_output else BLOCKED_VITIS_STATUS,
        "output": str_probe_output,
    }

# 从远端固件目录推断当前机器更接近哪一种 target part。
def _probe_target_part_hint(server: str, settings: Path, helper: ErieHelper) -> str:

    """从固件目录推断可能的 target part。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。

    返回:
        推断出的 target part 提示；无法判断时返回空字符串。
    """

    # 组合基于固件目录的 target part 探测命令。
    list_command = [
        "bash",  # 用 bash 承接多段固件目录判断逻辑
        "-lc",  # 通过登录 shell 运行 target part 推断脚本
        "if [ -d /opt/xilinx/firmware/u55c ] || [ -d /tools/Xilinx/firmware/u55c ]; "
        "then printf 'target_part=xcu55c-fsvh2892-2L-e'; "
        "elif [ -d /opt/xilinx/firmware/u50 ] || [ -d /tools/Xilinx/firmware/u50 ]; "
        "then printf 'target_part=xcu50-fsvh2104-2-e'; "
        "fi",
    ]

    # 执行固件目录探测，保留原始输出用于 target part 字段解析。
    str_probe_output = helper.exec(server, list_command, settings=settings)  # 固件目录扫描返回的 target_part 探测正文

    # 过滤掉无效解码结果，避免误判字段内容。
    _reject_decode_noise(str_probe_output)

    # 逐行扫描 target_part 字段，只接受显式的 key=value 结果。
    for line in str_probe_output.splitlines():

        # 命中 target_part 字段后直接返回其取值。
        if line.startswith("target_part="):

            # 命中 target_part 后直接把芯片型号提示交给上层平台族判断逻辑。
            return line.split("=", 1)[1].strip()

    # 没有 target_part 证据时返回空字符串，交给上层继续判断。
    return ""

# 汇总 CPU、PCIe、固件和管理工具输出，形成 U55C 事实证据集合。
def _probe_hardware_fingerprint(
    server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any] | None = None
) -> dict[str, Any]:

    """收集 CPU、PCIe、固件和板卡工具输出作为硬件指纹证据。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        profile: 当前使用的远端 profile 配置；缺失时只执行通用探测。

    返回:
        硬件指纹探测结果字典。
    """

    # 预留远端环境初始化片段，按 profile 动态补充 source 命令。
    str_source_settings = ""  # 远端环境初始化脚本片段

    # 默认不指定 xbmgmt 绝对路径，只有 profile 提供时才覆盖。
    str_xbmgmt_tool_path = ""  # profile 指定的 xbmgmt 绝对路径

    # profile 存在时补齐 Vitis 和 XRT 的环境初始化来源。
    if profile:

        # 读取 profile 指定的 Vitis 主环境脚本，用于还原板卡工具的执行环境。
        str_settings_script = str(profile.get("settings_script") or "").strip()  # 硬件指纹探测使用的 Vitis 主环境脚本

        # 读取 XRT 站点初始化脚本路径。
        str_xrt_setup_script = str(profile.get("xrt_setup_script") or "").strip()  # XRT 初始化脚本路径

        # 读取 profile 指定的 xbmgmt 绝对路径。
        str_xbmgmt_tool_path = str(profile.get("xbmgmt_tool_path") or "").strip()  # xbmgmt 工具路径

        # 有 settings64.sh 时先把 Vitis 环境 source 到当前 shell。
        if str_settings_script:

            # 把 Vitis settings64.sh 接到探测命令前缀，保证版本和工具链探测走同一环境。
            str_source_settings += f"source {shlex.quote(str_settings_script)} >/dev/null 2>&1 || true; "  # 先补齐 Vitis CLI 与编译工具所在环境

        # 有 XRT 初始化脚本时继续补齐板卡工具环境。
        if str_xrt_setup_script:

            # 把 XRT 初始化脚本接到探测命令前缀，确保板卡管理工具能在同一 shell 中被发现。
            str_source_settings += (
                f"source {shlex.quote(str_xrt_setup_script)} >/dev/null 2>&1 || true; "  # 把 XRT 站点脚本 source 到当前硬件探测 shell
            )  # 再补齐 xrt-smi/xbutil/xbmgmt 所需的站点环境

    # 生成 xbmgmt 探测片段，优先执行 profile 指定的工具路径。
    if str_xbmgmt_tool_path:

        # profile 提供绝对路径时，先尝试那份 xbmgmt，再回落到 PATH 中的默认命令。
        str_xbmgmt_probe = (  # 优先显式路径的 xbmgmt 检测脚本
            f"if [ -x {shlex.quote(str_xbmgmt_tool_path)} ]; then "  # 先检查 profile 指定的 xbmgmt 绝对路径是否可执行
            f"{shlex.quote(str_xbmgmt_tool_path)} examine 2>/dev/null; "  # 显式路径可用时直接运行那份 xbmgmt
            "else xbmgmt examine 2>/dev/null; fi"  # 显式路径不可用时回落到 PATH 中的默认 xbmgmt
        )

    # profile 没给显式 xbmgmt 路径时，只能回落到 PATH 里的默认命令。
    else:

        # 没有显式路径时只依赖 PATH 里的 xbmgmt，并允许命令缺失。
        str_xbmgmt_probe = "xbmgmt examine 2>/dev/null || true"  # 仅使用 PATH 中默认 xbmgmt 的探测脚本

    # 汇总 CPU、PCIe、固件和管理工具的联合探测命令。
    list_command = [
        "bash",  # 用 bash 承接多段 CPU/板卡联合探测逻辑
        "-lc",  # 通过登录 shell 执行硬件指纹收集脚本
        f"{str_source_settings}"
        "printf 'cpu_model='; (lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -n 1); "
        "printf '\\nlspci='; (lspci | grep -Ei 'xilinx|alveo' | head -n 20 || true); "
        "printf '\\nfirmware_scan='; (find /opt/xilinx/firmware -maxdepth 2 -type d 2>/dev/null | head -n 40 || true); "
        "printf '\\nboard_scan='; ((xrt-smi examine 2>/dev/null || xbutil examine 2>/dev/null || true) | head -n 120); "
        f"printf '\\nmgmt_scan='; (({str_xbmgmt_probe}) | head -n 120)",
    ]

    # 执行硬件探测命令，保留整段输出作为最终证据文本。
    str_probe_output = helper.exec(server, list_command, settings=settings)  # 硬件指纹原始输出

    # 先过滤 UTF-8 噪声，再继续拆分不同证据段。
    _reject_decode_noise(str_probe_output)

    # 提取 PCIe 设备段，判断是否看到 Alveo 或 Xilinx 板卡。
    str_lspci_text = _section_value(str_probe_output, "lspci")  # PCIe 设备扫描文本

    # 提取固件目录段，判断是否存在 U55C 固件痕迹。
    str_firmware_text = _section_value(str_probe_output, "firmware_scan")  # 固件目录扫描文本

    # 提取 XRT 板卡扫描段，观察活动设备信息。
    str_board_text = _section_value(str_probe_output, "board_scan")  # 板卡扫描文本

    # 提取管理工具扫描段，补充 shell/板卡管理视角证据。
    str_mgmt_text = _section_value(str_probe_output, "mgmt_scan")  # 管理工具扫描文本

    # 合并主要证据段，统一转小写后做 U55C 关键字判断。
    str_normalized_probe_text = " ".join((str_lspci_text, str_board_text, str_mgmt_text)).lower()  # 归一化后的硬件证据文本

    # 单独记录固件目录里是否出现 U55C 线索。
    bool_firmware_hint = any(  # 固件目录扫描是否至少暴露出 U55C 家族痕迹
        token in str_firmware_text.lower() for token in ("u55c", "xcu55c", "xilinx_u55c")  # 只接受能代表 U55C 家族的固件关键字
    )

    # 根据主要证据段是否出现 U55C 关键字决定最终状态。
    if any(token in str_normalized_probe_text for token in ("u55c", "xcu55c", "xilinx_u55c")):

        # PCIe、board_scan 或 mgmt_scan 已经直接出现 U55C 关键字，可视为事实设备证据成立。
        str_status = PASS_STATUS  # 当前主要硬件证据已经足以证明活动 U55C 设备存在

    # 主要证据没有直接命中 U55C 关键字时，必须保留阻塞结论。
    else:

        # 只有固件目录线索或空结果时，仍不足以证明当前活跃板卡就是 U55C。
        str_status = BLOCKED_BOARD_STATUS  # 现有证据还不能完成 U55C 事实闭环

    # 预留附加证据说明，只有阻塞时才写入提示文本。
    str_evidence_path = ""  # 附加证据说明文本

    # 缺少明确 U55C 证据时，给出阻塞原因描述。
    if str_status != PASS_STATUS:

        # 没有活动 U55C 证据时，把阻塞原因写进返回结果。
        str_evidence_path = "hardware fingerprint does not yet prove an active U55C device"  # U55C 证据不足说明

    # 返回硬件指纹结论以及可追溯的原始证据。
    return {
        "status": str_status,
        "output": str_probe_output,
        "evidence": str_evidence_path,
        "firmware_hint": bool_firmware_hint,
    }

# 确认 board 验收阶段依赖的 v++、g++ 和 XRT 工具都已经就绪。
def _probe_board_toolchain(server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any]) -> dict[str, Any]:

    """确认 board 验收所需的 vpp 与 XRT 工具链是否齐备。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        profile: 当前使用的远端 profile 配置。

    返回:
        board 工具链探测结果字典。
    """

    # 读取 Vitis settings64.sh 路径，后续统一 source 它。
    str_settings_script = str(profile["settings_script"])  # board 验收的 Vitis settings64.sh 路径

    # 读取可选的 XRT 站点初始化脚本路径。
    str_xrt_setup_script = str(profile.get("xrt_setup_script") or "").strip()  # board 验收的 XRT 初始化脚本路径

    # 读取 profile 显式声明的 v++ 绝对路径。
    str_vpp_path = str(profile.get("vpp_path") or "").strip()  # profile 指定的 v++ 路径

    # 读取 profile 显式声明的 XRT 工具绝对路径。
    str_xrt_tool_path = str(profile.get("xrt_tool_path") or "").strip()  # profile 指定的 XRT 工具路径

    # 仅在声明了 XRT 初始化脚本时追加额外 source 片段。
    if str_xrt_setup_script:

        # profile 提供 XRT 站点脚本时，把它追加到 board 工具链探测前缀。
        str_source_xrt = f"source {shlex.quote(str_xrt_setup_script)} >/dev/null 2>&1 || true; "  # board 探测阶段额外补齐的 XRT 环境前缀

    # profile 没给 XRT 初始化脚本时，board 探测只能依赖当前 shell 已有环境。
    else:

        # 缺少 XRT 站点脚本时不再额外 source，保持命令前缀为空串。
        str_source_xrt = ""  # board 工具链探测不追加额外 XRT 初始化

    # 组合 board 验收所需的 v++、g++ 和 XRT 探测命令。
    list_command = [
        "bash",  # 用 bash 承接 v++/g++/XRT 联合探测脚本
        "-lc",  # 通过登录 shell 运行 board 工具链探测逻辑
        f"source {shlex.quote(str_settings_script)} >/dev/null 2>&1 || true; "
        f"{str_source_xrt}"
        "printf 'vpp='; command -v v++ || true; "
        f"printf '\\nvpp_path='; if [ -x {shlex.quote(str_vpp_path)} ]; "
        f"then printf %s {shlex.quote(str_vpp_path)}; fi; "
        "printf '\\ngpp='; command -v g++ || true; "
        "printf '\\nxrt='; command -v xrt-smi || command -v xbutil || true; "
        f"printf '\\nxrt_path='; if [ -x {shlex.quote(str_xrt_tool_path)} ]; "
        f"then printf %s {shlex.quote(str_xrt_tool_path)}; fi",
    ]

    # 执行工具链探测命令，保留原始输出供状态判断和报告复用。
    str_probe_output = helper.exec(server, list_command, settings=settings)  # board 工具链探测原始输出

    # 过滤掉编码噪声，避免误判工具存在性。
    _reject_decode_noise(str_probe_output)

    # v++ 既允许通过 PATH 命中，也允许命中 profile 显式路径。
    bool_has_vpp = "vpp=/" in str_probe_output or "vpp_path=/" in str_probe_output  # 远端当前 shell 是否已经能直接找到 v++

    # board 主机运行除了 v++ 以外，还必须能在当前 shell 里解析到 XRT 管理工具。
    bool_has_xrt = "xrt=/" in str_probe_output or "xrt_path=/" in str_probe_output  # board 主机运行这一步是否已经能调用 xrt-smi 或 xbutil

    # 同时具备 v++、g++ 和 XRT 时才允许 board 工具链通过。
    if bool_has_vpp and "gpp=/" in str_probe_output and bool_has_xrt:

        # 编译、链接和运行所需的三类关键工具都已被远端 shell 直接解析到。
        str_status = PASS_STATUS  # board 编译与主机运行所需工具链已经齐备

    # 关键工具有缺口时，board 验收状态必须继续保持阻塞。
    # profile 没给显式 xbmgmt 路径时，shell 名探测只能依赖 PATH 里的默认命令。
    else:

        # 只要缺少 v++、g++ 或 XRT 任一关键工具，就不能宣称 board 阶段可执行。
        str_status = BLOCKED_BOARD_STATUS  # board 阶段关键工具链仍不完整

    # 返回 board 工具链判定结果和原始探测输出。
    return {"status": str_status, "output": str_probe_output}

# 提取 profile 中显式声明的平台名。
def _configured_platform_name(profile: dict[str, Any] | None) -> str:
    """提取 profile 中显式声明的平台名。

    参数:
        profile: 当前远端 profile 配置；允许为 None。

    返回:
        profile 中声明的平台名；未声明时返回空字符串。
    """

    # 空 profile 或空平台名都视为未配置。
    return str(profile.get("platform_name") or "").strip() if profile else ""

# 根据 profile 显式平台配置决定探测结果。
def _configured_platform_probe_result(
    server: str,
    settings: Path,
    helper: ErieHelper,
    profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """处理 profile 显式声明 platform_name 的平台探测结果。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        profile: 当前远端 profile 配置；允许为 None。

    返回:
        命中显式平台配置时返回结构化探测结果；否则返回 None。
    """

    # profile 指定的平台名是优先返回的候选名称。
    str_platform_name = _configured_platform_name(profile)  # profile 显式平台名称

    # 未声明 platform_name 时直接交给自动发现路径处理。
    if not str_platform_name:

        # 缺少显式平台名时返回 None，让上层继续走 discover 分支。
        return None

    # 上传平台探测确认 xpfm 是否已经在远端可用。
    dict_upload_probe = _probe_uploaded_platform(server, settings, helper, profile)  # 上传平台探测结果

    # 已经命中上传平台时，直接返回显式平台名作为最终选择。
    if dict_upload_probe.get("status") == PASS_STATUS:

        # 构造显式平台命中的通过结果，保留上传探测证据。
        return {
            "status": PASS_STATUS,
            "selected_platform": str_platform_name,
            "selected_xpfm": str(dict_upload_probe.get("selected_xpfm") or ""),
            "candidates": [str_platform_name],
            "all_platforms": [str_platform_name],
            "output": str(dict_upload_probe.get("output") or "platform_name=provided"),
        }

    # 未声明远端平台载荷时，不把显式平台名视为阻塞条件。
    if not _profile_declares_remote_platform_payload(profile):

        # 没有上传平台载荷时返回 None，让自动发现逻辑继续尝试。
        return None

    # shell 名称用于给用户补充建议平台名。
    dict_shell_probe = _probe_shell_name(server, settings, helper, profile)  # shell 名称探测结果

    # 返回阻塞结果，提示显式平台名对应的远端平台载荷尚未就绪。
    return {
        "status": BLOCKED_BOARD_STATUS,
        "selected_platform": "",
        "selected_xpfm": "",
        "candidates": [],
        "all_platforms": [],
        "reason": str(dict_upload_probe.get("reason") or "missing_uploaded_platform_payload"),
        "shell_name": str(dict_shell_probe.get("shell_name") or ""),
        "suggested_platform_name": str(dict_shell_probe.get("suggested_platform_name") or ""),
        "output": str(dict_upload_probe.get("output") or "platform_name=provided"),
    }

# 判断 profile 是否显式声明了远端平台载荷位置。
def _profile_declares_remote_platform_payload(profile: dict[str, Any]) -> bool:
    """判断 profile 是否声明了远端 xpfm 或平台根目录。

    参数:
        profile: 当前远端 profile 配置。

    返回:
        声明了 remote_xpfm 或 remote_platform_root 时返回 True。
    """

    # remote_xpfm 表示用户指定单个 xpfm 文件。
    str_remote_xpfm = str(profile.get("remote_xpfm") or "").strip()  # profile 远端 xpfm 路径

    # remote_platform_root 表示用户指定 xpfm 搜索根目录。
    str_remote_platform_root = str(profile.get("remote_platform_root") or "").strip()  # profile 远端平台根目录

    # remote_xpfm 或 remote_platform_root 任一存在都说明声明了平台载荷来源。
    return bool(str_remote_xpfm or str_remote_platform_root)

# 扫描远端 xpfm 列表，并选择与目标板卡匹配的平台结果。
def _discover_platform_probe_result(
    server: str,
    settings: Path,
    helper: ErieHelper,
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """扫描远端 xpfm 文件并选择与目标板卡匹配的平台。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        profile: 当前远端 profile 配置；允许为 None。

    返回:
        包含候选平台、阻塞原因和原始输出的探测结果字典。
    """

    # target_part 用于把平台候选收敛到 U55C 或 U50 家族。
    str_target_part = str(profile.get("target_part") or "").strip().lower() if profile else ""  # profile 中声明的目标器件 part 文本

    # expected_family 为空时扫描 U55C/U50 两类候选。
    str_expected_family = _expected_platform_family(str_target_part)  # 期望平台族名称

    # Vitis 与 opt 目录是受治理支持的 xpfm 扫描根。
    list_command = [
        "bash",  # 用 bash 承接受治理的 xpfm 扫描命令
        "-lc",  # 让 find 在远端 shell 里完整执行 xpfm 搜索与截断
        "find /tools/Xilinx/Vitis /opt/xilinx -type f -name '*.xpfm' 2>/dev/null | head -n 200",  # 只扫描受治理的 Vitis 安装根并把结果截断到前 200 条
    ]  # 平台 xpfm 扫描命令

    # 扫描输出每行对应一个 xpfm 路径。
    str_probe_output = helper.exec(server, list_command, settings=settings)  # 平台扫描原始输出

    # 清理解码噪声后再做平台路径解析。
    _reject_decode_noise(str_probe_output)

    # xpfm 路径列表用于提取平台 stem 名称。
    list_xpfm_paths = [line.strip() for line in str_probe_output.splitlines() if line.strip()]  # xpfm 路径列表

    # 平台名去重排序后进入报告。
    list_platform_names = sorted({PurePosixPath(path).stem for path in list_xpfm_paths})  # 远端平台名称列表

    # 按期望族或默认 U55C/U50 规则筛选候选平台。
    list_matched_platforms = _matched_platform_names(list_platform_names, str_expected_family)  # 匹配平台名称列表

    # 唯一匹配的平台可以直接作为最终平台名返回。
    if len(list_matched_platforms) == 1:

        # 返回唯一命中的平台结果，避免后续再走阻塞分支。
        return {
            "status": PASS_STATUS,
            "selected_platform": list_matched_platforms[0],
            "selected_xpfm": "",
            "candidates": list_matched_platforms,
            "all_platforms": list_platform_names,
            "output": str_probe_output,
        }

    # shell 探测用于在无法唯一匹配时补充更贴近设备实际状态的平台建议名。
    dict_shell_probe = _probe_shell_name(server, settings, helper, profile)  # xbmgmt 表格里提取出的 shell 平台建议

    # 匹配数量决定阻塞原因。
    str_block_reason = _platform_block_reason(list_matched_platforms, dict_shell_probe)  # 平台阻塞原因

    # 返回阻塞结果，并附上 shell 探测和候选平台信息。
    return {
        "status": BLOCKED_BOARD_STATUS,
        "selected_platform": "",
        "selected_xpfm": "",
        "candidates": list_matched_platforms,
        "all_platforms": list_platform_names,
        "reason": str_block_reason,
        "shell_name": str(dict_shell_probe.get("shell_name") or ""),
        "suggested_platform_name": str(dict_shell_probe.get("suggested_platform_name") or ""),
        "output": str_probe_output,
    }

# 从 target_part 文本推断预期平台族。
def _expected_platform_family(target_part: str) -> str:
    """从 target_part 推断平台族名称。

    参数:
        target_part: 远端 profile 或探测得到的 target part 文本。

    返回:
        命中 U55C 或 U50 时返回对应平台族，否则返回空字符串。
    """

    # 命中 U55C target part 时优先返回 u55c 平台族。
    if "u55c" in target_part:

        # U55C 平台族用于后续筛选同系列 xpfm。
        return "u55c"

    # 命中 U50 target part 时返回 u50 平台族。
    if "u50" in target_part:

        # 命中 u50 分支后，只保留名称里带 u50 的平台候选，避免误把 U55C 平台混进来。
        return "u50"

    # 未命中已知平台族时返回空字符串，交给默认匹配规则处理。
    return ""

# 根据平台族过滤远端扫描到的平台名列表。
def _matched_platform_names(platform_names: list[str], expected_family: str) -> list[str]:
    """按期望平台族筛选平台名列表。

    参数:
        platform_names: 远端扫描得到的平台名列表。
        expected_family: 期望命中的平台族名称。

    返回:
        过滤后的候选平台名列表。
    """

    # 显式平台族存在时，只保留该平台族内的候选。
    if expected_family:

        # 返回命中指定平台族关键字的平台名称集合。
        return [name for name in platform_names if expected_family in name.lower()]

    # 未指定平台族时，仅保留常见 U55C/U50 平台候选。
    return [name for name in platform_names if any(token in name.lower() for token in ("u55c", "u50"))]

# 根据候选平台数量和 shell 结果生成阻塞原因。
def _platform_block_reason(matched_platforms: list[str], shell_probe: dict[str, Any]) -> str:
    """根据匹配数量和 shell 探测结果生成平台阻塞原因。

    参数:
        matched_platforms: 当前规则命中的平台候选列表。
        shell_probe: shell 名称探测结果字典。

    返回:
        用于报告和阻塞判断的平台原因字符串。
    """

    # 没有候选与多个候选需要不同阻塞原因。
    str_block_reason = "no_matching_platform" if not matched_platforms else "multiple_matching_platforms"  # 平台匹配阻塞原因

    # shell 探测命中时，把附加诊断信息编码进阻塞原因。
    if shell_probe.get("shell_name"):

        # 检测到 shell 时追加后缀，提示用户可根据 shell 名修正配置。
        str_block_reason = f"{str_block_reason}_shell_detected"  # shell 辅助诊断阻塞原因

    # 返回最终的平台阻塞原因文本。
    return str_block_reason

# 组合显式配置与自动发现逻辑，选出最终平台名。
def _probe_platform_name(
    server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any] | None = None
) -> dict[str, Any]:
    """选择远端 Vitis 平台名称并返回可审计探测结果。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        profile: 当前远端 profile 配置；允许为 None。

    返回:
        包含最终平台选择或阻塞原因的探测结果字典。
    """

    # profile 显式平台配置拥有最高优先级。
    dict_configured_result = _configured_platform_probe_result(server, settings, helper, profile)  # 显式平台探测结果

    # 显式配置能产出结论时，直接返回该结论。
    if dict_configured_result is not None:

        # 返回显式平台配置对应的探测结果。
        return dict_configured_result

    # 回落到远端扫描结果，从 xpfm 列表里自动推断平台名。
    return _discover_platform_probe_result(server, settings, helper, profile)

# 根据显式 xpfm 或平台根目录探测已上传的平台载荷。
def _probe_uploaded_platform(
    server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any] | None = None
) -> dict[str, Any]:

    """根据显式 xpfm 或平台根目录探测已上传的平台载荷。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        profile: 当前远端 profile 配置；允许为 None。

    返回:
        包含平台载荷探测状态、原因和原始输出的结果字典。
    """

    # 缺少 profile 时无法知道远端平台载荷位置。
    if not profile:

        # profile 缺失时直接返回阻塞结果。
        return {"status": BLOCKED_BOARD_STATUS, "reason": "missing_profile"}

    # 读取 profile 中显式声明的 xpfm 文件路径。
    str_remote_xpfm = str(profile.get("remote_xpfm") or "").strip()  # profile 远端 xpfm 文件路径

    # 读取 profile 中显式声明的平台根目录。
    str_remote_platform_root = str(profile.get("remote_platform_root") or "").strip()  # profile 远端平台根目录路径

    # 读取 profile 中显式声明的平台名，用于根目录扫描后的精确筛选。
    str_platform_name = str(profile.get("platform_name") or "").strip()  # 配置里要求精确命中的平台名称

    # 指定了 remote_xpfm 时优先做单文件存在性探测。
    if str_remote_xpfm:

        # 显式 xpfm 路径优先走单文件存在性探测。
        return _probe_uploaded_platform_file(server, settings, helper, str_remote_xpfm)

    # 指定了平台根目录时，改走目录扫描探测。
    if str_remote_platform_root:

        # 平台根目录存在时改走目录扫描探测。
        return _probe_uploaded_platform_root(
            server,
            settings,
            helper,
            str_remote_platform_root,
            str_platform_name,
        )

    # 显式平台载荷位置都未声明时返回阻塞结果。
    return {"status": BLOCKED_BOARD_STATUS, "reason": "missing_uploaded_platform_payload"}

# 用显式 remote_xpfm 路径确认远端平台文件是否已经可用。
def _probe_uploaded_platform_file(
    server: str,
    settings: Path,
    helper: ErieHelper,
    str_remote_xpfm: str,
) -> dict[str, Any]:
    """探测显式 remote_xpfm 是否已经上传到远端。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        str_remote_xpfm: profile 中声明的远端 xpfm 文件路径。

    返回:
        包含探测状态、可选 selected_xpfm 与原始输出的结果字典。
    """

    # remote_xpfm 直接校验单个文件是否存在。
    list_command = [
        "bash",  # 远端 shell 程序名
        "-lc",  # bash 登录执行模式
        f"if [ -f {shlex.quote(str_remote_xpfm)} ]; "
        f"then printf 'selected_xpfm=%s' {shlex.quote(str_remote_xpfm)}; fi",
    ]  # 远端 xpfm 探测命令

    # 执行单文件存在性探测并保留原始输出。
    str_probe_output = helper.exec(server, list_command, settings=settings)  # 远端单文件检查返回的 xpfm 探测正文

    # 清理解码噪声后再解析 selected_xpfm 字段。
    _reject_decode_noise(str_probe_output)

    # selected_xpfm 段存在时表示该文件可直接用于后续验收。
    str_selected_xpfm = _section_value(str_probe_output, "selected_xpfm")  # 探测成功的 xpfm 路径

    # 命中 selected_xpfm 时直接返回可用的远端平台文件路径。
    if str_selected_xpfm:

        # 返回通过结果，让上层直接复用该 xpfm 文件。
        return {"status": PASS_STATUS, "selected_xpfm": str_selected_xpfm, "output": str_probe_output}

    # 单文件未命中时返回缺少上传 xpfm 的阻塞结果。
    return {"status": BLOCKED_BOARD_STATUS, "reason": "missing_uploaded_xpfm", "output": str_probe_output}

# 扫描 remote_platform_root，解析可复用的 xpfm 候选。
def _probe_uploaded_platform_root(
    server: str,
    settings: Path,
    helper: ErieHelper,
    str_remote_platform_root: str,
    str_platform_name: str,
) -> dict[str, Any]:
    """探测 remote_platform_root 下可用的 xpfm 列表。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        str_remote_platform_root: profile 中声明的远端平台目录。
        str_platform_name: profile 中声明的平台名，用于筛选唯一 xpfm。

    返回:
        包含探测状态、可选 selected_xpfm 与原始输出的结果字典。
    """

    # 平台根目录扫描只保留前 40 个 xpfm，避免远端输出过长。
    list_command = [
        "bash",  # 让远端通过 bash 执行 xpfm 根目录扫描
        "-lc",  # 在同一个 shell 里运行 find 与输出截断逻辑
        f"find {shlex.quote(str_remote_platform_root)} -maxdepth 3 "
        "-type f -name '*.xpfm' 2>/dev/null | sed -n '1,40p'",
    ]  # 远端平台根目录扫描命令

    # 执行平台根目录扫描，并保留原始输出供报告复用。
    str_probe_output = helper.exec(server, list_command, settings=settings)  # 平台根目录扫描原始输出

    # 清理解码噪声后再解析 xpfm 路径列表。
    _reject_decode_noise(str_probe_output)

    # 把 find 输出去掉空白行后，还原成可继续做 stem 匹配的平台文件清单。
    list_xpfm_paths = [line.strip() for line in str_probe_output.splitlines() if line.strip()]  # remote_platform_root 下实际找到的 xpfm 列表

    # 根目录下未找到任何 xpfm 时直接返回阻塞结果。
    if not list_xpfm_paths:

        # 返回缺少上传平台载荷的阻塞结果。
        return {
            "status": BLOCKED_BOARD_STATUS,
            "reason": "missing_uploaded_platform_payload",
            "output": str_probe_output,
        }

    # 配置了显式平台名时，优先按 stem 做唯一匹配。
    if str_platform_name:

        # 配置平台名时优先匹配同 stem 的唯一 xpfm。
        list_matched_xpfm_paths = [  # 与 profile.platform_name 完全同 stem 的 xpfm 候选列表
            path for path in list_xpfm_paths if PurePosixPath(path).stem == str_platform_name  # 只保留 stem 与配置平台名完全一致的候选
        ]

        # 唯一命中的平台文件可以直接作为最终 xpfm 返回。
        if len(list_matched_xpfm_paths) == 1:

            # 返回与显式平台名匹配的唯一 xpfm。
            return {"status": PASS_STATUS, "selected_xpfm": list_matched_xpfm_paths[0], "output": str_probe_output}

    # 根目录下只有一个 xpfm 时，即使未显式命名也可以直接采用。
    if len(list_xpfm_paths) == 1:

        # 返回唯一扫描到的 xpfm 文件路径。
        return {"status": PASS_STATUS, "selected_xpfm": list_xpfm_paths[0], "output": str_probe_output}

    # 多个候选无法唯一判定时返回阻塞结果，让用户补充平台信息。
    return {
        "status": BLOCKED_BOARD_STATUS,
        "reason": "multiple_uploaded_xpfm_candidates",
        "output": str_probe_output,
    }

# 读取 xbmgmt examine 输出，推断当前 shell 对应的平台名称。
def _probe_shell_name(
    server: str, settings: Path, helper: ErieHelper, profile: dict[str, Any] | None = None
) -> dict[str, Any]:

    """读取 xbmgmt 输出并提取 shell 名称。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        profile: 可选 profile 字段，用于指定 xbmgmt 工具路径。

    返回:
        包含 shell_name、output 与建议 platform_name 的结果字典。
    """

    # 读取 profile 中可选的 xbmgmt 绝对路径。
    str_xbmgmt_tool_path = str(profile.get("xbmgmt_tool_path") or "").strip() if profile else ""  # profile 指定的 xbmgmt 可执行文件路径

    # 优先使用 profile 指定的 xbmgmt 路径，否则回落到 PATH 查询。
    if str_xbmgmt_tool_path:

        # profile 给了显式 xbmgmt 路径时，先尝试那份工具再回落到 PATH 中的默认命令。
        str_command_text = (  # 优先显式 xbmgmt 路径的 shell 探测脚本
            f"if [ -x {shlex.quote(str_xbmgmt_tool_path)} ]; then "  # 先判断 profile 给出的 xbmgmt 绝对路径是否可执行
            f"{shlex.quote(str_xbmgmt_tool_path)} examine 2>/dev/null; "  # 显式路径可用时直接用那份 xbmgmt 做探测
            "else xbmgmt examine 2>/dev/null; fi"  # 显式路径失效时继续尝试系统 PATH 里的 xbmgmt，避免把有工具的机器误判成无工具
        )

    # 没有显式路径分支命中时，继续用 PATH 里的 xbmgmt 完成 shell 名探测。
    else:

        # 没有显式路径时只依赖 PATH 里的 xbmgmt，并允许命令本身不存在。
        str_command_text = "xbmgmt examine 2>/dev/null || true"  # 仅走 PATH 默认 xbmgmt 的 shell 探测脚本

    # 执行 shell 名称探测命令，并保留原始输出供后续解析。
    str_probe_output = helper.exec(server, ["bash", "-lc", str_command_text], settings=settings)  # shell 名称探测原始输出

    # 先过滤解码噪声，再解析 xbmgmt 表格内容。
    _reject_decode_noise(str_probe_output)

    # 预置空 shell 名，未命中时保持空字符串。
    str_shell_name = ""  # xbmgmt 解析出的 shell 名称

    # 逐行扫描 xbmgmt 表格输出，提取第一条 shell 名称。
    for line in str_probe_output.splitlines():

        # 正则匹配 xbmgmt 表格中的 shell 名称列。
        list_shell_name_matches = re.findall(r"\|\[[^\]]+\]\s+\|\s*([A-Za-z0-9_]+)\s+\|", line)  # 当前行命中的 shell 名列表

        # 命中 shell 名后只保留第一项即可。
        if list_shell_name_matches:

            # 保存第一条 shell 名结果，后续不再继续扫描。
            str_shell_name = list_shell_name_matches[0].strip()  # 当前 shell 表格里解析出的首个 shell 名

            # 第一条 shell 名已经足够生成平台建议，不再继续扫描剩余行。
            break

    # 返回 shell 名、平台建议名和原始探测输出。
    return {
        "shell_name": str_shell_name,
        "suggested_platform_name": _suggest_platform_name_from_shell(str_shell_name),
        "output": str_probe_output,
    }

# 把 shell 探测到的平台标签映射成仓库里使用的平台名称建议。
def _suggest_platform_name_from_shell(shell_name: str) -> str:

    """根据 shell 探测结果给出平台名称建议。

    参数:
        shell_name: shell 环境中解析到的平台名称。

    返回:
        根据 shell 结果推断的平台名称建议；无法匹配时返回空字符串。
    """

    # 统一做去空白和小写化，方便按固定 shell 名匹配。
    str_normalized_shell_name = str(shell_name or "").strip().lower()  # 归一化后的 shell 名称

    # U55C base_3 shell 名需要映射到仓库内使用的平台名。
    if str_normalized_shell_name == "xilinx_u55c_gen3x16_xdma_base_3":

        # 返回仓库内 board 验收使用的平台名。
        return "xilinx_u55c_gen3x16_xdma_3_202210_1"

    # 无已知映射时返回空字符串，交给上层继续阻塞处理。
    return ""

# 合并 profile overlay，只用有效字段覆盖基础配置。
def _merge_profile_fields(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:

    """把 overlay 字段合并回基础 profile。

    参数:
        base: 基础 profile 字段字典。
        overlay: 待覆盖回基础 profile 的增量字段。

    返回:
        合并后的 profile 字段字典。
    """

    # 复制基础 profile，避免原地修改调用方对象。
    dict_merged = dict(base)  # 合并后的 profile 副本

    # 逐项吸收 overlay 字段，只覆盖真正有效的值。
    for str_profile_key, str_profile_field_value in overlay.items():

        # 字符串字段只在非空白时覆盖基础 profile。
        if isinstance(str_profile_field_value, str):

            # 空白字符串不覆盖已有值，避免把有效配置抹空。
            if str_profile_field_value.strip():

                # 非空白字符串才能真正覆盖掉基础 profile 里的原值。
                dict_merged[str_profile_key] = str_profile_field_value  # 把 overlay 中有效的字符串字段写回结果 profile

            # 字符串字段无论是否覆盖成功，都不需要再落入非字符串分支。
            continue

        # 非字符串字段只在非 None 时覆盖基础 profile。
        if str_profile_field_value is not None:

            # 用明确提供的非空对象覆盖基础 profile 字段。
            dict_merged[str_profile_key] = str_profile_field_value  # 用非 None 值覆盖同名 profile 字段

    # 返回合并后的 profile 字典。
    return dict_merged

# 从多段 key=value 输出里提取指定段的连续正文。
def _section_value(output: str, key: str) -> str:

    """从分段输出里提取指定 section 的正文。

    参数:
        output: 待解析的原始输出文本。
        key: 需要提取的 section 或字段名。

    返回:
        指定 section 对应的正文文本；未命中时返回空字符串。
    """

    # 构造 section 前缀，后续只匹配以该字段起始的行。
    str_section_prefix = f"{key}="  # section 行匹配前缀

    # 逐行拆分原始输出，便于提取连续 section 正文。
    list_output_lines = output.splitlines()  # 原始输出的逐行列表

    # 扫描所有输出行，定位目标 section 的起始位置。
    for int_line_index, str_output_line in enumerate(list_output_lines):

        # 当前行不是目标字段起点时，继续向后扫描。
        if not str_output_line.startswith(str_section_prefix):

            # 当前行不属于目标 section，继续扫描下一行。
            continue

        # 先记录 section 首行里等号后的正文。
        list_parts = [str_output_line.split("=", 1)[1].strip()]  # section 正文片段列表

        # 继续吸收该 section 后续的连续正文行。
        for extra in list_output_lines[int_line_index + 1 :]:

            # 命中下一个 key=value 段时结束当前 section 收集。
            if re.match(r"^[A-Za-z0-9_]+=", extra):

                # 下一段已经开始，当前 section 的正文到此为止。
                break

            # 追加当前 section 的补充正文行。
            list_parts.append(extra.strip())

        # 返回拼接后的连续 section 正文。
        return "\n".join(item for item in list_parts if item)

    # 未找到目标 section 时返回空字符串。
    return ""

# 读取远端 pwd 输出，拿到本轮验收实际使用的工作目录。
def _probe_remote_workdir(server: str, settings: Path, helper: ErieHelper) -> str:

    """读取远端工作目录并校验治理边界。

    参数:
        server: 远端服务器名称。
        settings: erie settings 文件路径。
        helper: 负责执行 erie-remote-ssh 子命令的 helper。

    返回:
        远端工作目录字符串。

    异常:
        RemoteAcceptanceError: 远端未返回任何可解析工作目录时抛出。
    """

    # 通过 pwd 读取远端实际工作目录。
    str_probe_output = helper.exec(server, ["bash", "-lc", "pwd"], settings=settings)  # 远端 shell 返回的 pwd 正文

    # 清理解码噪声后再提取目录行。
    _reject_decode_noise(str_probe_output)

    # 过滤空行后保留可能的工作目录输出。
    list_workdir_lines = [line.strip() for line in str_probe_output.splitlines() if line.strip()]  # 过滤后的工作目录候选行

    # 一行都没有时说明远端没有返回可解析目录。
    if not list_workdir_lines:

        # 缺少工作目录输出时直接阻塞，避免误用空路径。
        raise RemoteAcceptanceError(f"> ERR: [Python] could not determine remote workdir for server {server}.")

    # 返回最后一条非空行，作为远端实际工作目录。
    return list_workdir_lines[-1]

# 一次性准备远端项目根、conda 前缀和活动 run 目录。
def _ensure_remote_project_layout(helper: ErieHelper, settings: Path, server: str, layout: dict[str, str]) -> list[str]:

    """确保远端项目根、conda 前缀和 run 目录全部就绪。

    参数:
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        settings: erie settings 文件路径。
        server: 远端服务器名称。
        layout: 远端目录契约计算得到的布局字典。

    返回:
        执行过程中生成的 request 路径列表。
    """

    # 收集目录准备过程中产生的 request 路径，供最终报告复用。
    list_request_paths: list[str] = []  # 目录准备 request 路径列表

    # 先对项目根相对路径做 shell 转义。
    str_project_root_arg = shlex.quote(layout["project_root_relative"])  # 远端项目根目录参数

    # 对 conda 前缀目录做 shell 转义。
    str_conda_prefix_arg = shlex.quote(layout["conda_prefix_relative"])  # 远端 conda 前缀目录参数

    # 对活动 run 的父目录做 shell 转义，确保 `runs/` 目录可安全创建。
    str_runs_parent_arg = shlex.quote(str(PurePosixPath(layout["active_run_relative"]).parent))  # active run 所属 runs 父目录的 shell 参数

    # 对归档目录父路径做 shell 转义，确保 `backups/` 目录可安全创建。
    str_backups_parent_arg = shlex.quote(str(PurePosixPath(layout["backup_run_relative"]).parent))  # 远端 backups 父目录参数

    # 对活动 run 目录做 shell 转义，确保本轮写入目录按契约落位。
    str_active_run_arg = shlex.quote(layout["active_run_relative"])  # 远端活动 run 目录参数

    # 一次性创建项目根、conda 前缀、runs 父目录、backups 父目录和本轮活动 run 目录。
    str_layout_command = (
        f"mkdir -p {str_project_root_arg} {str_conda_prefix_arg} "
        f"{str_runs_parent_arg} {str_backups_parent_arg} {str_active_run_arg}"
    )  # 远端布局准备命令

    # 执行受治理目录准备命令，并把 request 路径纳入最终报告。
    list_request_paths.append(
        helper.request_and_run(
            settings,
            server,
            "command",
            str_layout_command,
            "prepare governed remote project root, conda prefix path, and active run directory",
        )
    )

    # 返回目录准备阶段全部 request 路径，供结果汇总直接复用。
    return list_request_paths

# 把已验证通过的活动 run 移入受治理的 backups 目录。
def _archive_remote_run(helper: ErieHelper, settings: Path, server: str, layout: dict[str, str]) -> str:

    """把已验证通过的远端 run 归档到 backups 目录。

    参数:
        helper: 负责执行 erie-remote-ssh 子命令的 helper。
        settings: erie settings 文件路径。
        server: 远端服务器名称。
        layout: 远端目录契约计算得到的布局字典。

    返回:
        归档远端 run 时生成的 request 路径。
    """

    # 对活动 run 目录做 shell 转义，准备执行归档移动。
    str_active_run_arg = shlex.quote(layout["active_run_relative"])  # 待归档活动 run 参数

    # 对目标归档 run 目录做 shell 转义，确保迁移落到治理目录。
    str_backup_run_arg = shlex.quote(layout["backup_run_relative"])  # 归档目标 run 参数

    # 对归档父目录做 shell 转义，确保 `backups/` 先存在。
    str_backup_parent_arg = shlex.quote(str(PurePosixPath(layout["backup_run_relative"]).parent))  # 归档父目录参数

    # 先创建 backups 父目录，再替换旧归档，最后把活动 run 迁移过去。
    str_archive_command = (
        f"mkdir -p {str_backup_parent_arg} && rm -rf {str_backup_run_arg} "
        f"&& mv {str_active_run_arg} {str_backup_run_arg}"
    )  # 远端 run 归档命令

    # 返回归档命令的 request 路径，便于最终报告关联该次迁移操作。
    return helper.request_and_run(
        settings, server, "command", str_archive_command, "archive verified remote run into governed backups directory"
    )

# 为当前 run 生成独立的 erie settings overlay，隔离请求和下载目录。
def _write_erie_settings_overlay(config: dict[str, Any], run_dir: Path) -> Path:

    """把本次 run 专用的 erie settings overlay 写入本地目录。

    参数:
        config: 远端验收治理配置。
        run_dir: 本地 run 目录路径。

    返回:
        写出的 overlay settings 文件路径。
    """

    # 读取 erie-remote-ssh 的基础 settings，后续只在 overlay 中覆盖本轮路径。
    path_base_settings_path = Path(config["erie_settings_path"])  # 这轮 overlay 要复制并覆盖的基础 settings 文件

    # 载入基础 settings，作为 overlay 的可变字典副本。
    dict_settings = json.loads(path_base_settings_path.read_text(encoding="utf-8"))  # 当前 run 要在其上覆盖路径字段的基础 settings 可变副本

    # 缺失 `paths` 时先补空字典，避免后续覆盖 requests/downloads/tmp 等目录字段时报错。
    dict_settings.setdefault("paths", {})  # 确保 overlay 里始终存在可写入路径字段的 paths 字典

    # 解析并固化 default_server_list，避免 overlay 在不同 cwd 下重复展开占位符。
    dict_settings["paths"]["default_server_list"] = str(  # 固化成当前机器可直接访问的 server list 绝对路径
        _resolve_erie_server_list(dict_settings, path_base_settings_path, Path(config["erie_skill_dir"]))  # 解析并展开 settings 里声明的 default_server_list
    )  # 解析后的 server list 绝对路径

    # 把 request 输出隔离到本轮 run 目录，避免不同验收互相覆盖。
    dict_settings["paths"]["requests_dir"] = str(run_dir / "requests")  # 这轮 request 文件统一落到 run_dir/requests

    # 把下载结果隔离到本轮 run 目录，便于复盘和归档。
    dict_settings["paths"]["downloads_dir"] = str(run_dir / "downloads")  # 这轮下载产物统一落到 run_dir/downloads

    # 把临时校验目录放到本轮 run 下，避免污染共享临时路径。
    dict_settings["paths"]["validation_tmp_dir"] = str(run_dir / "tmp")  # 本轮 validation tmp 目录

    # 先放入当前仓库根，确保本地治理目录始终允许上传。
    list_upload_roots = [str(skill_root().parents[1])]  # 本轮 overlay 允许上传的根目录列表

    # 保留原有 settings 中额外声明的上传根，但避免重复路径。
    for item in dict_settings["paths"].get("upload_roots", []):

        # 仅接收未重复的字符串路径，避免把非法对象或重复路径写回 overlay。
        if isinstance(item, str) and item not in list_upload_roots:

            # 只追加基础 settings 中额外定义且尚未收录的上传根。
            list_upload_roots.append(item)

    # 用去重后的上传根覆盖 overlay 配置，保持请求打包范围稳定。
    dict_settings["paths"]["upload_roots"] = list_upload_roots  # overlay 上传根目录集合

    # 本轮 overlay 固定写到 run 目录，便于请求与报告一并归档。
    path_overlay_settings = run_dir / "erie_settings.overlay.json"  # overlay settings 输出路径

    # 以 UTF-8 JSON 写出 overlay，确保后续 erie 命令直接可复用。
    path_overlay_settings.write_text(json.dumps(dict_settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 返回 overlay settings 路径，供后续 request/exec 统一引用。
    return path_overlay_settings

# 解析 erie settings 中声明的 server list 路径，并展开约定占位符。
def _resolve_erie_server_list(settings: dict[str, Any], settings_path: Path, erie_skill_dir: Path) -> Path:

    """解析 settings 中声明的 server list 路径。

    参数:
        settings: 已加载的 erie settings 字典。
        settings_path: 当前 settings 文件路径。
        erie_skill_dir: erie-remote-ssh skill 根目录。

    返回:
        解析后的 server list 绝对路径。

    异常:
        RemoteAcceptanceError: settings 未声明 default_server_list 时抛出。
    """

    # 读取 settings 中声明的 server list 原始路径，并去掉首尾空白。
    str_raw_server_list_path = str(settings.get("paths", {}).get("default_server_list") or "").strip()  # 原始 server list 路径文本

    # 未声明 server list 时直接阻塞，避免误用默认或空配置。
    if not str_raw_server_list_path:

        # 缺少 default_server_list 会让 overlay 无法绑定稳定的 server 配置来源。
        raise RemoteAcceptanceError(
            "> ERR: [Python] erie-remote-ssh settings are missing paths.default_server_list. "
            "Ask the user to configure the remote server list before continuing."
        )

    # 统一准备可展开的占位符值，兼容 skill_dir、settings_dir 和 home。
    dict_replacements = {
        "skill_dir": str(erie_skill_dir),  # 给 ${skill_dir} 提供 erie-remote-ssh skill 根目录
        "settings_dir": str(settings_path.parent),  # 给 ${settings_dir} 提供当前 settings 所在目录
        "home": str(Path.home()),  # 给 ${home} 提供当前用户主目录
    }  # server list 路径占位符映射

    # 逐个替换约定占位符，把 settings 中的逻辑路径还原成真实本地绝对路径。
    for str_placeholder_key, str_placeholder_value in dict_replacements.items():

        # 每轮只替换一个占位符，确保替换过程保持可追踪。
        str_raw_server_list_path = str_raw_server_list_path.replace(  # 当前占位符替换后的 server list 中间路径
            "${" + str_placeholder_key + "}", str_placeholder_value  # 用当前占位符对应的真实路径值替换模板片段
        )

    # 继续展开用户目录与环境变量，并返回最终的绝对 server list 路径。
    return Path(os.path.expandvars(os.path.expanduser(str_raw_server_list_path))).resolve()

# 生成带时间戳和随机后缀的本地 run 目录，避免多次验收相互覆盖。
def _new_run_dir(config: dict[str, Any], prefix: str) -> Path:
    """创建带时间戳和随机后缀的本地 run 目录。

    参数:
        config: 远端验收治理配置。
        prefix: run 目录名前缀。

    返回:
        新建的本地 run 目录路径。
    """

    # 使用 UTC 时间戳命名 run 目录，避免跨时区日志排序混乱。
    str_timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")  # run 目录 UTC 时间戳

    # 组合本地 run 根、前缀、时间戳和随机后缀，确保多次验收互不覆盖。
    path_run_dir = repo_root() / str(config["local_run_root"]) / f"{prefix}-{str_timestamp}-{uuid.uuid4().hex[:8]}"  # 当前验收会话唯一对应的本地 run 目录

    # 提前创建 run 目录，后续 requests、downloads、report 可直接写入。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 返回新建好的本地 run 目录。
    return path_run_dir

# 把本轮远端验收结果写入 run 目录下的标准结果文件。
def _write_report(run_dir: Path, result: dict[str, Any]) -> None:

    """把远端验收结果写入 result.json。

    参数:
        run_dir: 本地 run 目录路径。
        result: 待写出的结果字典。

    返回:
        无业务返回值。
    """

    # 把标准结果文件固定写到 `result.json`，便于后续工具统一读取。
    _write_json(run_dir / "result.json", result)

# 以统一的 UTF-8 JSON 格式写出结构化文件。
def _write_json(path: Path, payload: dict[str, Any]) -> None:

    """以 UTF-8 JSON 形式写出结构化数据。

    参数:
        path: 目标文件路径。
        payload: 待写出的 JSON 载荷。

    返回:
        无业务返回值。
    """

    # 先确保目标文件父目录存在，避免 JSON 写出时因目录缺失失败。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 以统一缩进和 UTF-8 编码写出 JSON，保证报告可读且稳定。
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# 生成 discover、check 与 workspace-check 组成的基础计划。
def _base_planned_steps(server: str, str_topology: str, str_validate_server: str) -> list[str]:
    """生成所有模式共用的远端验收基础步骤。

    参数:
        server: 主执行服务器名称。
        str_topology: 当前远端执行拓扑。
        str_validate_server: split 拓扑下的验证服务器名称。

    返回:
        当前模式共用的基础步骤列表。
    """

    # 基础步骤先覆盖 discover、list、check 和 workspace-check。
    list_steps = ["erie discover", "erie list", f"erie check {server}", f"erie workspace-check {server}"]  # 远端验收基础步骤

    # split 拓扑需要把 validation 服务器的基础检查一起列入计划。
    if str_topology == "split_build_validate" and str_validate_server:

        # validation 服务器也必须补齐 check 与 workspace-check 步骤。
        list_steps.extend([f"erie check {str_validate_server}", f"erie workspace-check {str_validate_server}"])

    # 返回所有模式共享的基础步骤。
    return list_steps

# 根据 mode、拓扑和清理策略展开模式专属计划步骤。
def _mode_planned_steps(
    mode: str, profile: str, readiness: str, str_example_spec: str,
    str_topology: str, str_validate_server: str, bool_cleanup_remote: bool,
) -> list[str]:
    """生成各 mode 专属的远端验收步骤。

    参数:
        mode: 当前远端验收模式。
        profile: 当前使用的 profile 名称。
        readiness: Vitis 验证所需的就绪级别。
        str_example_spec: 本轮本地 mock 工件使用的示例规格。
        str_topology: 当前远端执行拓扑。
        str_validate_server: split 拓扑下的验证服务器名称。
        bool_cleanup_remote: 是否在计划中说明远端清理策略。

    返回:
        当前 mode 需要追加的专属计划步骤列表。
    """

    # profile 为空时回落到占位文本，避免计划说明缺字段。
    str_profile_label = profile or "<user-configured-profile>"  # 计划展示用 profile 名称

    # link 模式只做只读探测，不进入完整构建流程。
    if mode == "link":

        # 返回 link 模式最小化的只读验证步骤。
        return ["erie exec read-only UTF-8 link probe"]

    # board 模式需要补齐硬件证据、主机程序和远端归档步骤。
    if mode == "board":

        # 返回 board 验收专用的完整执行步骤。
        return [
            f"erie exec board profile probe {str_profile_label}",
            "erie exec hardware fingerprint probe for 9950X/U55C evidence",
            f"generate local HLS mock artifacts from {str_example_spec or 'default example'}",
            "render validation-only board host scaffold",
            "ensure governed remote project root and project-local conda prefix",
            "prepare governed remote run directory under runs/<run-id>",
            "erie request command payload transfer",
            "erie exec detached board compile/link/host-run sequence",
            "archive verified remote run into backups/<run-id>",
        ]

    # Vitis 模式默认按“探测、生成、传输、执行、归档”顺序组织主服务器步骤。
    list_steps = [
        f"erie exec Vitis profile probe {str_profile_label}",  # 先确认主服务器上的 Vitis profile 与工具链入口
        f"generate local HLS mock artifacts from {str_example_spec or 'default example'}",  # 先在本地准备这轮远端验收要上传的示例载荷
        "ensure governed remote project root and project-local conda prefix",  # 先在远端准备项目根和隔离 conda 前缀
        "prepare governed remote run directory under runs/<run-id>",  # 给这轮上传、执行和留痕准备独立的远端 run 目录
        "erie request command payload transfer",  # 把本地准备好的验收载荷按受治理 request 流程上传到远端 run 目录
        f"erie request command Vitis {readiness}",  # 在远端 run 目录里执行这轮 readiness 对应的 Vitis 验证命令
        "archive verified remote run into backups/<run-id>",  # 验证通过后把活动 run 按治理契约转存到 backups 目录
    ]  # vitis 模式专属步骤

    # split 拓扑时把 validation 服务器执行阶段附加到 Vitis 计划中。
    if str_topology == "split_build_validate" and str_validate_server:

        # validation 服务器需要独立执行设备探测、传输和验证步骤。
        list_steps.extend(
            [
                "erie exec validation server device probe",
                "prepare governed validation run directory",
                "erie request command payload transfer validation",
                f"erie request command validation Vitis {readiness}",
            ]
        )

    # 开启 cleanup_remote 时，在计划里明确保留归档而不是直接删除活动目录。
    if bool_cleanup_remote:

        # 把远端保留策略写进计划，避免误解 cleanup 的真实含义。
        list_steps.append("keep archived backup and skip active-directory deletion because archive is mandatory")

    # 返回当前 mode 组装好的计划步骤列表。
    return list_steps

# 汇总基础步骤和模式专属步骤，形成最终的远端执行计划。
def _planned_steps(
    mode: str,
    server: str,
    profile: str,
    readiness: str,
    **options: Any,
) -> list[str]:

    """汇总基础步骤和模式步骤，形成完整计划。

    参数:
        mode: 远端验收运行模式。
        server: 远端服务器名称。
        profile: 当前使用的 profile 名称。
        readiness: Vitis 验证所需的就绪级别。
        options: 包含 topology、validate_server、example_spec、cleanup_remote 等附加选项。

    返回:
        汇总后的完整计划步骤列表。
    """

    # cleanup_remote 控制计划文本是否说明远端保留策略。
    bool_cleanup_remote = bool(options.get("cleanup_remote", False))  # 是否计划清理远端产物

    # example_spec 展示本轮验收使用的示例 spec 名称。
    str_example_spec = str(options.get("example_spec", ""))  # 计划展示用示例 spec 名称

    # split 拓扑下 validate_server 会生成额外检查步骤。
    str_validate_server = options.get("validate_server")  # split 拓扑中的验证服务器名称

    # topology 控制 single 与 split 两种远端计划模板。
    str_topology = str(options.get("topology", "single_server"))  # 远端验收拓扑名称

    # 基础步骤覆盖 discover、check 和拓扑相关的 validate 服务器检查。
    list_steps = _base_planned_steps(server, str_topology, str_validate_server)  # 所有模式都要先执行的远端基础检查步骤

    # mode 专属步骤由下层 helper 统一生成，避免基础计划和模式计划逻辑混在一起。
    list_mode_steps = _mode_planned_steps(  # 当前 mode 额外追加的远端执行步骤
        mode, profile, readiness, str_example_spec, str_topology, str_validate_server, bool_cleanup_remote  # 把模式、示例、拓扑和清理策略一起交给步骤生成器
    )

    # 追加 mode 专属步骤，形成完整的计划文本。
    list_steps.extend(list_mode_steps)

    # 返回已经拼好的完整计划步骤列表，供最终报告直接引用。
    return list_steps

# 从 erie request 输出中提取真正的 request 文件路径。
def _parse_request_path(stdout: str) -> str:

    """从 erie 输出中提取 request 文件路径。

    参数:
        stdout: erie 命令输出文本。

    返回:
        从输出中解析出的 request 路径。

    异常:
        RemoteAcceptanceError: 输出里缺少 request 路径字段时抛出。
    """

    # 逐行扫描 erie 输出，定位 `request:` 前缀对应的 request 文件路径。
    for line in stdout.splitlines():

        # 命中 `request:` 前缀时，截取冒号后的真实路径文本。
        if line.startswith("request:"):

            # 返回 `request:` 后面的真实路径，供 run-request 等后续步骤直接复用。
            return line.split(":", 1)[1].strip()  # erie 输出里声明的 request 文件路径

    # 整段输出都找不到 request 字段时直接报错，避免后续执行空路径。
    raise RemoteAcceptanceError(f"> ERR: [Python] could not find request path in erie output: {stdout}")

# 从 key: value 风格的输出里抽取指定字段。
def _field_from_output(output: str, key: str) -> str:

    """从 key: value 输出中提取指定字段值。

    参数:
        output: 待解析的原始输出文本。
        key: 需要提取的字段名。

    返回:
        解析到的字段值；缺失时返回空字符串。
    """

    # 统一构造 `key: ` 前缀，便于逐行提取目标字段。
    str_field_prefix = f"{key}: "  # 目标字段在输出中的固定前缀文本

    # 逐行扫描命令输出，查找指定字段对应的那一行。
    for line in output.splitlines():

        # 命中目标字段时，截掉前缀并返回剩余的纯值文本。
        if line.startswith(str_field_prefix):

            # 返回该字段在当前输出里对应的纯值文本。
            return line.split(str_field_prefix, 1)[1].strip()  # 目标字段解析出的值文本

    # 缺少该字段时返回空串，让调用方自己决定是否属于异常。
    return ""

# 遇到解码异常痕迹时立即阻断，避免把乱码当成有效探测证据。
def _reject_decode_noise(output: str) -> None:

    """识别 erie 输出中的 UTF-8 解码异常。

    参数:
        output: 待检查的原始输出文本。

    返回:
        无业务返回值。

    异常:
        RemoteAcceptanceError: 输出包含解码失败痕迹时抛出。
    """

    # 只要看到已知解码失败痕迹，就终止当前探测并要求修正编码链路。
    if "UnicodeDecodeError" in output or "_readerthread" in output:

        # 解码链路已经失真时立即阻塞，避免把乱码当成硬件或工具证据。
        raise RemoteAcceptanceError(f"> ERR: [Python] erie-remote-ssh output decoding failed. {UTF8_HINT}")

# 判断 status 子命令失败是否属于允许重试的瞬时网络问题。
def _is_transient_status_failure(output: str) -> bool:

    """判断 status 失败是否属于可重试的瞬时网络问题。

    参数:
        output: 待检查的 status 输出文本。

    返回:
        当前输出是否属于可重试的瞬时失败。
    """

    # 先把输出转成小写，便于后续统一匹配瞬时网络错误片段。
    str_lowered_output = output.lower()  # 归一化后的 status 输出文本

    # 这些片段都对应 SSH 层瞬时抖动，允许上层保守重试状态查询。
    tuple_transient_markers = (
        "timed out",  # 连接或读取超时
        "banner exchange",  # SSH banner 交换失败
        "connection aborted",  # 连接被中途终止
        "connection reset",  # 对端重置连接
        "connection closed",  # 对端提前关闭连接
        "kex_exchange_identification",  # SSH 密钥交换阶段失败
    )  # status 可重试失败标记集合

    # 命中任一瞬时失败片段时返回 True，通知上层继续有限重试。
    return any(marker in str_lowered_output for marker in tuple_transient_markers)

# 按固定字段顺序把结果字典压缩成人类可读摘要。
def _format_result(result: dict[str, Any]) -> str:

    """把结果字典整理成人类可读的摘要文本。

    参数:
        result: 待格式化的结果字典。

    返回:
        适合终端展示的结果摘要文本。
    """

    # 第一行固定输出 status，保证终端摘要始终先给出最终结论。
    list_lines = [f"status: {result.get('status')}"]  # 结果摘要输出行列表

    # 仅按固定字段顺序补充存在的上下文，保持摘要稳定且便于比较。
    for key in (
        "mode",  # 远端验收模式
        "topology",  # 远端执行拓扑
        "server",  # 单机模式服务器
        "build_server",  # split 拓扑的构建服务器
        "validate_server",  # split 拓扑的验证服务器
        "profile",  # 远端 profile 名称
        "vitis_version",  # 探测到的 Vitis 版本
        "readiness",  # 期望的验收就绪级别
        "example_spec",  # 本轮 mock 工件规格
        "run_dir",  # 本地 run 目录
        "run_id",  # 本轮 run 标识
        "remote_project_root",  # 远端项目根目录
        "remote_conda_prefix",  # 远端 conda 前缀目录
        "remote_run_dir",  # 远端活动 run 目录
        "remote_backup_dir",  # 远端归档 run 目录
        "remote_dir",  # 兼容历史字段的远端目录
        "remote_vitis_version_request",  # Vitis 版本探测 request 路径
        "remote_vitis_profile_request",  # profile 探测 request 路径
    ):

        # 仅输出实际存在的字段，避免摘要里出现大量空值占位。
        if result.get(key) is not None:

            # 把当前存在的字段写入摘要，保持固定键顺序下的人类可读输出。
            list_lines.append(f"{key}: {result[key]}")  # 追加当前存在的摘要字段

    # 失败时把错误文本显式输出到摘要，方便终端直接查看。
    if result.get("error"):

        # 输出失败原因，帮助终端直接看到最终错误文本。
        list_lines.append(f"error: {result['error']}")

    # 缺字段信息对诊断探测不完整很关键，需要在摘要里直接展开。
    if result.get("missing_fields"):

        # 把缺失字段展开成单行文本，帮助确认哪段探测证据尚未产出。
        list_lines.append("missing_fields: " + ", ".join(str(item) for item in result["missing_fields"]))

    # probe 段存在时补一行 probe 状态，帮助快速判断探测阶段是否已完成。
    if result.get("probe"):

        # 输出 probe 状态，帮助区分探测失败和后续执行失败。
        list_lines.append(f"probe: {result['probe'].get('status')}")

    # 远端产物保留策略需要显式体现在摘要里，避免误解 cleanup 结果。
    if result.get("remote_artifacts_retained") is not None:

        # 记录远端产物是否保留，避免 cleanup 语义被误读。
        list_lines.append(f"remote_artifacts_retained: {result['remote_artifacts_retained']}")

    # cleanup 执行结果独立输出，便于区分“计划保留”和“已实际清理”。
    if result.get("cleanup_performed") is not None:

        # 记录是否真的执行了 cleanup，而不是仅停留在计划层。
        list_lines.append(f"cleanup_performed: {result['cleanup_performed']}")

    # 归档状态单独输出，确保受治理的 backups 行为一眼可见。
    if result.get("archived_after_verification") is not None:

        # 记录验证后是否完成归档，直接暴露受治理备份状态。
        list_lines.append(f"archived_after_verification: {result['archived_after_verification']}")

    # 按行拼接最终摘要文本，供 CLI 直接打印。
    return "\n".join(list_lines)

# 脚本以独立 CLI 方式运行时，统一从 `main()` 进入完整治理流程。
if __name__ == "__main__":

    # 以 CLI 退出码形式返回主流程结果，保持脚本入口行为标准化。
    raise SystemExit(main())
