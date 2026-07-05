#!/usr/bin/env python3
"""恢复远端 board acceptance 运行结果并补齐本地报告。"""

# 未来注解确保 helper 类型不会改变远端恢复模块的导入顺序。
from __future__ import annotations

# 标准库导入提供 CLI 参数、shell 引号和路径类型。
import argparse
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, cast

# board 模块只提供平台元数据和平台选择合并入口。
from remote_acceptance_board import (
    _board_metadata_for_spec,
    _resolve_board_platform_selection,
)

# common 模块集中承载状态常量、远端 helper 和报告写入能力。
from remote_acceptance_common import (
    BOARD_STATUS_MARKER,
    FAILED_STATUS,
    PASS_STATUS,
)

# common 的 helper 和异常类型用于远端命令执行边界。
from remote_acceptance_common import (
    ErieHelper,
    RemoteAcceptanceError,
)

# common 的字段解析和 profile 工具用于报告与恢复判断。
from remote_acceptance_common import (
    field_from_equals_output,
    get_vitis_selection,
    infer_target_part_from_platform_selection,
)

# common 的恢复解析函数和配置函数保持独立分组，降低导入密度。
from remote_acceptance_common import (
    recover_example_spec,
    recover_local_run_dir,
    remote_directory_layout_for_workdir,
    remote_validation_config,
)

# common 的项目路径解析函数只读取本地治理配置。
from remote_acceptance_common import (
    repo_root,
    resolve_recovery_target,
    skill_config_path,
)

# common 的远端副作用和报告写入函数集中在最后一组。
from remote_acceptance_common import (
    _archive_remote_run,
    _merge_profile_fields,
)

# common 的远端探测函数提供板卡验收事实来源。
from remote_acceptance_common import (
    _probe_board_toolchain,
    _probe_hardware_fingerprint,
    _probe_platform_name,
    _probe_remote_workdir,
)

# common 的文件写入函数只负责 settings overlay 和报告落盘。
from remote_acceptance_common import (
    _write_erie_settings_overlay,
    _write_report,
)

# Vitis 模块提供恢复 profile 时复用的版本候选和 target_part 推断。
from remote_acceptance_vitis import (
    _find_candidate,
    _infer_target_part_from_server,
    _vitis_version_candidates,
)

# 通过态恢复报告里的 host-run 证据固定指向 board runner 生成的日志文件。
BOARD_RUN_LOG_RELATIVE_PARTS = ("artifacts", "board_run.log")  # board run 日志相对路径片段

# kernel 编译与链接证据来自 V++ 日志，相对路径要在恢复报告里稳定复现。
VPP_KERNEL_LOG_RELATIVE_PARTS = ("artifacts", "v++_kernel.log")  # V++ kernel 日志在远端目录中的固定定位片段

# `_recover_board_mode` 是 remote_vitis_acceptance.py 对外复用的恢复入口。
def _recover_board_mode(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: ErieHelper,
    topology: dict[str, Any],
) -> dict[str, Any]:
    """从已有远端运行目录恢复 board 模式验收结果。

    参数:
        args: CLI 参数命名空间。
        config: 远端验收治理配置。
        helper: erie-remote-ssh 调用封装。
        topology: 已解析的远端服务器拓扑。

    返回:
        dict[str, Any]: 恢复后的验收报告字典。
    """

    # 恢复上下文承载本地路径、远端布局和板卡探测事实。
    dict_context = _build_board_recovery_context(args, config, helper, topology)  # 恢复报告共享上下文

    # 远端状态决定本次报告采用 active 还是 backup 证据。
    dict_remote_state = _select_recovered_remote_state(dict_context, helper)  # 远端证据选择结果

    # board 探测字段是判断 recovered/pass 的唯一状态来源。
    dict_recovered_probe = cast(dict[str, Any], dict_remote_state["board_probe"])  # board 探测状态字典

    # 未恢复到 passed 时保留失败证据，不冒充验收完成。
    if dict_recovered_probe.get("board_status") != PASS_STATUS:

        # failed 报告保留 active/backup 双路径证据，供用户继续定位。
        return _write_failed_recovery_report(args, config, topology, dict_context, dict_remote_state)

    # 已恢复为 passed 时写入完整 board 验收证据字段。
    return _write_passed_recovery_report(args, config, topology, dict_context, dict_remote_state)

# `_build_board_recovery_context` 只做事实采集，不决定报告成败。
def _build_board_recovery_context(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: ErieHelper,
    topology: dict[str, Any],
) -> dict[str, Any]:
    """收集恢复流程所需的本地、远端和板卡探测上下文。

    参数:
        args: CLI 参数命名空间。
        config: 远端验收治理配置。
        helper: erie-remote-ssh 调用封装。
        topology: 已解析的远端服务器拓扑。

    返回:
        dict[str, Any]: 后续报告构造所需的恢复上下文。
    """

    # 恢复目标元组保留 run id 和用户显式传入的远端目录。
    tuple_recovery_target = _resolve_recovery_target(args)  # 恢复目标二元组

    # run id 作为本地目录和远端布局的稳定关联键。
    str_run_id = tuple_recovery_target[0]  # 历史运行标识

    # 用户显式远端目录为空时后续回退到标准 active 目录。
    str_requested_remote_run_dir = tuple_recovery_target[1]  # 用户指定远端目录

    # 本地 run 目录保存旧输入、overlay settings 和最终恢复报告。
    path_local_run_dir = _recover_local_run_dir(config, str_run_id)  # 本地历史运行目录

    # settings 路径用于所有 erie-remote-ssh 探测命令。
    path_settings = _recover_settings_path(config, path_local_run_dir)  # 恢复用 settings 路径

    # 拓扑中的主服务器是 board 恢复的唯一远端执行目标。
    str_server = str(topology["server"])  # 远端验收服务器名称

    # 远端 helper 先确认连接和软件清单，避免读取陈旧缓存。
    helper.preflight(str_server, settings=path_settings)

    # 软件扫描结果会支撑后续 Vitis 和板卡 profile 选择。
    helper.scan_software(str_server, settings=path_settings)

    # 远端 workdir 是 runs/backups/conda 相对路径的根。
    str_remote_workdir = _probe_remote_workdir(str_server, path_settings, helper)  # 远端项目工作目录

    # 目录布局统一 active 与 backup 的绝对路径和相对路径。
    dict_layout = remote_directory_layout_for_workdir(str_remote_workdir, str_run_id)  # 远端恢复目录布局

    # example spec 影响板卡元数据和报告中可追溯的输入名称。
    str_example_spec = _recover_requested_example_spec(args, path_local_run_dir)  # 恢复使用的 example spec

    # Vitis/board profile 需要合并持久选择、远端事实和平台推断。
    dict_selected_profile = _recover_board_profile(args, str_server, str_remote_workdir, path_settings)  # 从 CLI 参数、服务器事实和历史选择恢复出的 board profile

    # 返回的上下文只保存稳定事实，后续函数负责报告形态。
    return {
        "run_id": str_run_id,
        "requested_remote_run_dir": str_requested_remote_run_dir,
        "local_run_dir": path_local_run_dir,
        "settings_path": path_settings,
        "server": str_server,
        "remote_workdir": str_remote_workdir,
        "layout": dict_layout,
        "example_spec": str_example_spec,
        "board_metadata": _board_metadata_for_spec(str_example_spec),
        "selected_profile": dict_selected_profile,
        "platform_probe": _probe_platform_name(
            str_server,
            path_settings,
            helper,
            dict_selected_profile,
        ),
        "hardware_probe": _probe_hardware_fingerprint(
            str_server,
            path_settings,
            helper,
            dict_selected_profile,
        ),
        "toolchain_probe": _probe_board_toolchain(
            str_server,
            path_settings,
            helper,
            dict_selected_profile,
        ),
    }

# `_recover_requested_example_spec` 统一处理 CLI 显式值和历史输入反查。
def _recover_requested_example_spec(args: argparse.Namespace, local_run_dir: Path) -> str:
    """解析恢复模式使用的 example spec 文件名。

    参数:
        args: CLI 参数命名空间。
        local_run_dir: 本地历史运行目录。

    返回:
        str: 可追溯的 example spec 文件名。
    """

    # CLI 显式 example spec 优先，空值和默认 mock 值走历史输入反查。
    str_example_spec = str(args.example_spec or "").strip()  # 用户提供的 example spec 名称

    # 非默认 spec 已经具有明确语义，可以直接进入恢复报告。
    if str_example_spec and str_example_spec != "hls_vector_scale_mock_spec.json":

        # 显式 spec 是最可靠的恢复来源，直接返回。
        return str_example_spec

    # 默认 spec 可能只是占位值，需要从本地 run 输入反查真实来源。
    return _recover_example_spec(local_run_dir)

# `_select_recovered_remote_state` 只执行远端探测和必要归档。
def _select_recovered_remote_state(
    context: dict[str, Any],
    helper: ErieHelper,
) -> dict[str, Any]:
    """选择可恢复的远端运行目录，必要时归档 active 运行目录。

    参数:
        context: 恢复上下文字典。
        helper: erie-remote-ssh 调用封装。

    返回:
        dict[str, Any]: 被选中的远端目录、探测结果和请求清单。
    """

    # 远端布局提供 active 与 backup 两类可恢复目录。
    dict_layout = cast(dict[str, Any], context["layout"])  # 远端目录布局

    # settings 路径用于后续所有远端探测和归档请求。
    path_settings = cast(Path, context["settings_path"])  # 远端 helper settings 路径

    # 恢复流程只针对上下文中确认过的远端服务器执行。
    str_server = str(context["server"])  # 远端服务器名称

    # active 目录允许用户传入显式路径覆盖标准 runs 路径。
    str_active_remote_dir = str(context["requested_remote_run_dir"] or dict_layout["active_run_dir"])  # active 远端目录

    # backup 目录代表已经归档的稳定证据副本，优先服务历史恢复。
    str_backup_remote_dir = str(dict_layout["backup_run_dir"])  # 归档后优先读取的稳定证据目录

    # active 探测判断当前 runs 目录是否已经包含通过证据。
    dict_active_probe = _probe_recoverable_board_result(str_server, path_settings, helper, str_active_remote_dir)  # active runs 目录的 board 探测结果

    # backup 探测改为读取归档目录里的长期证据，避免把临时 active 状态误当作最终结果。
    dict_backup_probe = _probe_recoverable_board_result(str_server, path_settings, helper, str_backup_remote_dir)  # backup 归档目录的 board 探测结果

    # request 路径记录本轮触发过的 erie-remote-ssh 请求文件。
    list_request_paths: list[str] = []  # 远端请求路径清单

    # backup 已通过时不需要再次移动 active 目录。
    bool_backup_passed = dict_backup_probe.get("board_status") == PASS_STATUS  # backup 是否已有 passed 证据

    # 归档标记影响报告中的 remote_dir 和 archived_after_verification 字段。
    bool_archived_after_verification = bool_backup_passed  # 是否已完成验收后归档

    # active 通过但 backup 未通过时，立即归档以满足目录契约。
    if dict_active_probe.get("board_status") == PASS_STATUS and not bool_backup_passed:

        # 归档请求路径写入报告，便于追踪本轮远端副作用。
        list_request_paths.append(_archive_remote_run(helper, path_settings, str_server, dict_layout))

        # active passed 已被移动到 backup，报告需标记归档完成。
        bool_archived_after_verification = True  # active 通过后触发的归档状态

        # backup 探测在归档后刷新，避免报告仍携带旧状态。
        dict_backup_probe = _probe_recoverable_board_result(str_server, path_settings, helper, str_backup_remote_dir)  # 归档刷新后的 backup 目录探测结果

    # 报告中的 remote_dir 必须跟最终使用的证据目录一致。
    str_recovered_remote_rel = _recovered_remote_relative_path(  # 最终恢复目录相对路径解析调用
        dict_layout,  # active 与 backup 的远端布局信息
        bool_archived_after_verification,  # 是否已经完成通过后归档
    )  # 最终恢复目录相对路径

    # recovered_probe 是报告和状态判断使用的最终 board 探测结果。
    dict_recovered_probe = (  # 最终 board 探测结果选择表达式
        dict_backup_probe  # 已归档时使用 backup 目录探测结果
        if bool_archived_after_verification  # 通过后归档意味着 backup 才是最终证据
        else dict_active_probe  # 未归档时继续以 active 目录探测结果为准
    )  # 最终 board 探测结果

    # 远端状态集中返回，避免报告函数重新执行探测命令。
    return {
        "active_probe": dict_active_probe,
        "backup_probe": dict_backup_probe,
        "board_probe": dict_recovered_probe,
        "remote_dir": str_recovered_remote_rel,
        "archived_after_verification": bool_archived_after_verification,
        "requests": list_request_paths,
    }

# `_recovered_remote_relative_path` 封装 active/backup 相对目录选择规则。
def _recovered_remote_relative_path(
    layout: dict[str, Any],
    archived_after_verification: bool,
) -> str:
    """根据归档状态选择报告中的相对远端目录。

    参数:
        layout: 远端目录布局字典。
        archived_after_verification: 是否已归档到 backup 目录。

    返回:
        str: 报告使用的远端相对目录。
    """

    # 已完成归档时，报告中的相对目录必须切换到 backup 证据路径。
    if archived_after_verification:

        # 归档后证据固定在 backup 目录下。
        return str(layout["backup_run_relative"])

    # 未归档时仍保留 active 相对目录，便于用户定位失败证据。
    return str(layout["active_run_relative"])

# `_write_failed_recovery_report` 记录不可恢复状态和所有缺失证据。
def _write_failed_recovery_report(
    args: argparse.Namespace,
    config: dict[str, Any],
    topology: dict[str, Any],
    context: dict[str, Any],
    remote_state: dict[str, Any],
) -> dict[str, Any]:
    """写入无法恢复为 passed 的 board 验收报告。

    参数:
        args: CLI 参数命名空间。
        config: 远端验收治理配置，保留以维持报告构造签名一致。
        topology: 已解析的远端服务器拓扑。
        context: 恢复上下文字典。
        remote_state: 远端目录选择结果。

    返回:
        dict[str, Any]: failed 状态报告。
    """

    # failed 报告不使用 archive_trigger，但保留 config 参数维持调用形状。
    del config

    # 失败报告里的目录布局要同时展开 active 与 backup，方便对照哪一侧缺证据。
    dict_layout = cast(dict[str, Any], context["layout"])  # failed 报告使用的 active/backup 布局快照

    # 这里的本地目录专门承载恢复报告，不承担远端布局语义。
    path_local_run_dir = cast(Path, context["local_run_dir"])  # failed 恢复报告写回的本地 run 目录

    # failed 报告保留所有探测证据，供后续人工定位缺失项。
    dict_result = {  # failed 恢复报告字段集合
        "status": FAILED_STATUS,  # 恢复结论状态
        "mode": "board",  # 恢复报告对应的验收模式
        "server": context["server"],  # 产生这份恢复报告的远端服务器
        "topology": topology["topology"],  # 当前恢复使用的服务器拓扑
        "profile": args.profile,  # 用户请求的验收 profile
        "vitis_version": _selected_vitis_version(context),  # 恢复出的 Vitis 版本
        "readiness": args.readiness,  # 本轮恢复面向的 readiness 深度
        "example_spec": context["example_spec"],  # 恢复报告关联的示例规格名
        "run_dir": str(path_local_run_dir),  # 本地 run 目录
        "run_id": context["run_id"],  # 用于把本地报告与远端证据目录重新对齐的历史 run id
        "remote_run_dir": dict_layout["active_run_relative"],  # active 远端目录相对路径
        "remote_backup_dir": dict_layout["backup_run_relative"],  # 归档后长期保留证据的 backup 相对目录
        "remote_dir": remote_state["remote_dir"],  # 本次恢复最终采用的远端证据目录
        "platform_probe": context["platform_probe"],  # 远端平台探测结果
        "hardware_probe": context["hardware_probe"],  # 远端硬件指纹探测结果
        "toolchain_probe": context["toolchain_probe"],  # 远端板卡工具链探测结果
        "board_metadata": context["board_metadata"],  # 示例规格对应的板卡元数据
        "board_probe": remote_state["board_probe"],  # 最终采用的 board 日志探测结果
        "requests": remote_state["requests"],  # 本轮恢复产生的远端 request 路径
        "recovered_from_run_id": context["run_id"],  # 恢复报告对应的历史 run id
        "recovered_from_remote_logs": False,  # 当前失败报告尚未恢复出通过态远端日志
        "evidence_sources": [  # active 与 backup 目录的证据来源列表
            f"remote:{dict_layout['active_run_relative']}",  # active 远端证据目录
            f"remote:{dict_layout['backup_run_relative']}",  # backup 目录里长期保留的远端证据根路径
        ],
        "uses_erie_remote_ssh": True,  # 恢复流程仍通过 erie-remote-ssh 执行
    }

    # 报告写回历史 run 目录，避免覆盖其他运行结果。
    _write_report(path_local_run_dir, dict_result)

    # 调用方继续使用该字典作为 CLI JSON 输出源。
    return dict_result

# `_write_passed_recovery_report` 负责补齐 passed 报告的目录和证据字段。
def _write_passed_recovery_report(
    args: argparse.Namespace,
    config: dict[str, Any],
    topology: dict[str, Any],
    context: dict[str, Any],
    remote_state: dict[str, Any],
) -> dict[str, Any]:
    """写入已恢复为 passed 的 board 验收报告。

    参数:
        args: CLI 参数命名空间。
        config: 远端验收治理配置。
        topology: 已解析的远端服务器拓扑。
        context: 恢复上下文字典。
        remote_state: 远端目录选择结果。

    返回:
        dict[str, Any]: passed 状态报告。
    """

    # passed 报告里的目录布局需要补齐 project_root、conda_prefix 和 active/backup 审计字段。
    dict_layout = cast(dict[str, Any], context["layout"])  # passed 报告使用的远端布局快照

    # 恢复通过后仍然写回原始本地 run 目录，保持一次运行只对应一份事实记录。
    path_local_run_dir = cast(Path, context["local_run_dir"])  # passed 验收事实写回的历史 run 目录

    # 基础报告先收敛公共字段，再追加路径和证据字段。
    dict_result = _base_passed_recovery_report(args, config, topology, context, remote_state)  # passed 报告基础字典

    # 目录字段和证据字段依赖最终选择的 active/backup 状态。
    dict_result.update(
        {
            "remote_project_root": dict_layout["project_root_relative"],
            "remote_project_root_abs": dict_layout["project_root"],
            "remote_conda_prefix": dict_layout["conda_prefix_relative"],
            "remote_conda_prefix_abs": dict_layout["conda_prefix"],
            "remote_run_dir": dict_layout["active_run_relative"],
            "remote_run_dir_abs": dict_layout["active_run_dir"],
            "remote_backup_dir": dict_layout["backup_run_relative"],
            "remote_backup_dir_abs": dict_layout["backup_run_dir"],
            "remote_dir": remote_state["remote_dir"],
            "board_profile": _selected_board_profile(context),
            "evidence_sources": _passed_recovery_evidence(remote_state),
        }
    )

    # passed 报告写回历史 run 目录，作为恢复后的事实记录。
    _write_report(path_local_run_dir, dict_result)

    # 返回完整报告字典供上层 CLI 输出。
    return dict_result

# `_base_passed_recovery_report` 保持 passed 报告公共字段集中维护。
def _base_passed_recovery_report(
    args: argparse.Namespace,
    config: dict[str, Any],
    topology: dict[str, Any],
    context: dict[str, Any],
    remote_state: dict[str, Any],
) -> dict[str, Any]:
    """构造 passed 报告中与目录布局无关的字段。

    参数:
        args: CLI 参数命名空间。
        config: 远端验收治理配置。
        topology: 已解析的远端服务器拓扑。
        context: 恢复上下文字典。
        remote_state: 远端目录选择结果。

    返回:
        dict[str, Any]: passed 报告基础字段。
    """

    # artifact_dir 继续落在原始本地 run 目录下，兼容旧工具读取路径的方式。
    path_local_run_dir = cast(Path, context["local_run_dir"])  # passed 公共字段共享的本地 run 目录

    # 基础字段覆盖验收状态、远端探测事实和恢复来源。
    return {
        "status": PASS_STATUS,
        "mode": "board",
        "server": context["server"],
        "topology": topology["topology"],
        "profile": args.profile,
        "vitis_version": _selected_vitis_version(context),
        "readiness": args.readiness,
        "example_spec": context["example_spec"],
        "run_dir": str(path_local_run_dir),
        "artifact_dir": str(path_local_run_dir / "local-generation" / "attempt-001" / "hls" / "artifacts"),
        "run_id": context["run_id"],
        "cleanup_performed": False,
        "remote_artifacts_retained": True,
        "archived_after_verification": remote_state["archived_after_verification"],
        "archive_trigger": config["directory_contract"]["archive_trigger"],
        "requests": remote_state["requests"],
        "job_id": "recovered-from-existing-run",
        "job_status": "recovered",
        "platform_probe": context["platform_probe"],
        "platform_upload": {},
        "hardware_probe": context["hardware_probe"],
        "toolchain_probe": context["toolchain_probe"],
        "board_metadata": context["board_metadata"],
        "board_status_marker": BOARD_STATUS_MARKER,
        "recovered_from_run_id": context["run_id"],
        "recovered_from_remote_logs": True,
        "uses_erie_remote_ssh": True,
    }

# `_selected_vitis_version` 把 profile 字典转换为稳定版本字符串。
def _selected_vitis_version(context: dict[str, Any]) -> str:
    """读取已选 Vitis profile 的版本字符串。

    参数:
        context: 恢复上下文字典。

    返回:
        str: Vitis 版本；缺失时为空字符串。
    """

    # selected_profile 来自恢复上下文，可能由持久选择或远端候选推导。
    dict_selected_profile = cast(dict[str, Any], context["selected_profile"])  # 恢复上下文中的 Vitis profile 字段

    # 缺失版本保持空字符串，避免报告字段出现 None。
    return str(dict_selected_profile.get("version") or "")

# `_selected_board_profile` 只输出报告契约需要的板卡字段。
def _selected_board_profile(context: dict[str, Any]) -> dict[str, str]:
    """把已选 board profile 压缩成报告所需字段。

    参数:
        context: 恢复上下文字典。

    返回:
        dict[str, str]: 板卡平台报告字段。
    """

    # selected_profile 包含平台根目录、xpfm 和 target_part 等板卡事实。
    dict_selected_profile = cast(dict[str, Any], context["selected_profile"])  # 恢复上下文中的 board 平台字段

    # 报告只暴露稳定字符串字段，避免把临时探测结构写入结果。
    return {
        "platform_name": str(dict_selected_profile.get("platform_name") or ""),
        "remote_platform_root": str(dict_selected_profile.get("remote_platform_root") or ""),
        "remote_xpfm": str(dict_selected_profile.get("remote_xpfm") or ""),
        "target_part": str(dict_selected_profile.get("target_part") or ""),
    }

# `_passed_recovery_evidence` 构造通过状态必须保留的两类远端日志。
def _passed_recovery_evidence(remote_state: dict[str, Any]) -> list[str]:
    """生成恢复成功报告的远端证据路径。

    参数:
        remote_state: 远端目录选择结果。

    返回:
        list[str]: 远端日志证据路径。
    """

    # remote_dir 已在 active/backup 选择阶段固定。
    str_remote_dir = str(remote_state["remote_dir"])  # 最终远端相对目录

    # 用 POSIX 纯路径统一拼接恢复报告中的远端日志路径。
    path_remote_dir = PurePosixPath(str_remote_dir)  # 最终远端相对目录对象

    # board run 日志路径证明 host-run 证据存在。
    str_board_log = path_remote_dir.joinpath(*BOARD_RUN_LOG_RELATIVE_PARTS).as_posix()  # board run 日志相对路径

    # kernel 日志路径证明构建与链接证据存在。
    str_vpp_kernel_log = path_remote_dir.joinpath(*VPP_KERNEL_LOG_RELATIVE_PARTS).as_posix()  # V++ kernel 证据日志的 POSIX 相对路径

    # 两个日志路径分别证明 host run 与 kernel link/build 证据。
    return [
        f"remote:{str_board_log}",
        f"remote:{str_vpp_kernel_log}",
    ]

# `_resolve_recovery_target` 把底层 ValueError 统一提升为验收异常。
def _resolve_recovery_target(args: argparse.Namespace) -> tuple[str, str]:
    """解析恢复目标，优先使用明确的 run id。

    参数:
        args: CLI 参数命名空间。

    返回:
        tuple[str, str]: run id 与用户指定的远端运行目录。

    异常:
        RemoteAcceptanceError: 缺少可恢复目标时抛出。
    """

    # 先让底层解析函数执行 run_id/remote_dir 二选一规则，再把输入错误包装成统一异常。
    try:

        # 底层解析函数保留 run id 优先于远端目录的治理规则。
        return resolve_recovery_target(
            str(getattr(args, "recover_run_id", "") or ""),
            str(getattr(args, "recover_remote_run_dir", "") or ""),
        )

    # 用户输入无法确定恢复目标时，统一转成验收异常边界。
    except ValueError as exc:

        # 抛出带固定前缀的恢复目标异常，避免上层 CLI 输出不一致。
        raise RemoteAcceptanceError(f"> ERR: [Python] resolve recovery target failed: {exc}") from exc

# `_recover_local_run_dir` 确认本地历史运行目录可被恢复。
def _recover_local_run_dir(config: dict[str, Any], run_id: str) -> Path:
    """定位待恢复 run id 对应的本地运行目录。

    参数:
        config: 远端验收治理配置。
        run_id: 历史运行标识。

    返回:
        Path: 本地运行目录。

    异常:
        RemoteAcceptanceError: 本地运行目录不可恢复时抛出。
    """

    # 先按治理配置定位 run 根目录，再把底层恢复错误提升成统一验收异常。
    try:

        # 本地 run 根目录来自治理配置，避免扫描非项目目录。
        return recover_local_run_dir(repo_root() / str(config["local_run_root"]), run_id)

    # 本地运行目录不存在或非法时，统一转成带前缀的验收异常。
    except ValueError as exc:

        # 抛出带固定前缀的本地恢复异常，方便 CLI 和报告共用同一错误协议。
        raise RemoteAcceptanceError(f"> ERR: [Python] resolve local run directory failed: {exc}") from exc

# `_recover_settings_path` 优先复用历史 settings overlay。
def _recover_settings_path(config: dict[str, Any], local_run_dir: Path) -> Path:
    """选择历史 overlay settings，缺失时重新生成一份。

    参数:
        config: 远端验收治理配置。
        local_run_dir: 本地历史运行目录。

    返回:
        Path: 可用于远端 helper 的 settings 路径。
    """

    # 历史 overlay 优先复用，保证恢复命令与原运行保持一致。
    path_overlay = local_run_dir / "erie_settings.overlay.json"  # 历史 settings overlay 路径

    # 已存在 overlay 时不重新生成，避免改变远端配置来源。
    if path_overlay.is_file():

        # 历史 overlay 能最大限度复现原始远端验收环境。
        return path_overlay

    # 缺失 overlay 时按当前治理配置生成最小可用 settings。
    return _write_erie_settings_overlay(config, local_run_dir)

# `_recover_example_spec` 从 adapter 输入恢复示例文件名。
def _recover_example_spec(local_run_dir: Path) -> str:
    """从历史运行输入中反查 example spec 名称。

    参数:
        local_run_dir: 本地历史运行目录。

    返回:
        str: example spec 文件名。

    异常:
        RemoteAcceptanceError: 历史输入无法映射到 example spec 时抛出。
    """

    # 先按 examples_dir 配置反查历史输入，再把缺失映射包装成统一恢复异常。
    try:

        # examples_dir 是 spec 反查的唯一技能内配置来源。
        return recover_example_spec(skill_config_path("examples_dir"), local_run_dir)

    # 历史输入无法映射到 spec 时，统一转成带前缀的验收异常。
    except ValueError as exc:

        # 抛出带固定前缀的 spec 恢复异常，保持恢复链路的错误文本风格一致。
        raise RemoteAcceptanceError(f"> ERR: [Python] recover example spec failed: {exc}") from exc

# `_recover_board_profile` 串联 Vitis profile 和板卡平台恢复。
def _recover_board_profile(
    args: argparse.Namespace,
    server: str,
    remote_workdir: str,
    settings: Path,
) -> dict[str, Any]:
    """恢复 Vitis 与板卡平台 profile。

    参数:
        args: CLI 参数命名空间。
        server: 远端服务器名称。
        remote_workdir: 远端项目工作目录。
        settings: erie-remote-ssh settings 路径。

    返回:
        dict[str, Any]: 合并后的 Vitis 与 board profile。
    """

    # 候选列表来自远端软件扫描与治理配置共同约束的 Vitis 版本。
    list_candidates = _vitis_version_candidates(remote_validation_config(), settings, server)  # Vitis 候选 profile 列表

    # 先恢复 Vitis profile，再用 server/platform 信息补齐 target_part。
    dict_selected = _recover_vitis_profile_from_candidates(args, server, list_candidates)  # 待补齐 target_part 的 Vitis profile

    # server 事实优先于平台名推断，可以减少错误 target_part。
    _fill_missing_target_part_from_server(dict_selected, settings, server)

    # board 平台字段在最后合并，避免覆盖已确认的 Vitis 字段。
    return _recover_board_platform_profile(args, server, remote_workdir, dict_selected)

# `_recover_vitis_profile_from_candidates` 实现 Vitis 版本选择优先级。
def _recover_vitis_profile_from_candidates(
    args: argparse.Namespace,
    server: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """从显式版本、持久选择和探测候选中恢复 Vitis profile。

    参数:
        args: CLI 参数命名空间。
        server: 远端服务器名称。
        candidates: 当前可用的 Vitis 候选列表。

    返回:
        dict[str, Any]: 选中的 Vitis profile。
    """

    # CLI 显式版本优先，便于用户恢复指定 Vitis 环境的历史运行。
    str_explicit_version = str(getattr(args, "vitis_version", "") or "").strip()  # 用户指定 Vitis 版本

    # 显式版本先匹配当前候选，找不到时回退到持久选择。
    if str_explicit_version:

        # 候选命中说明远端当前仍能找到该版本。
        dict_candidate = _find_candidate(candidates, str_explicit_version)  # 显式版本候选项

        # 当前显式版本仍在候选列表中时，直接沿用该候选配置。
        if dict_candidate:

            # 当前候选命中时直接复制，避免调用方修改原列表元素。
            return dict(dict_candidate)

        # 候选缺失时保留服务器持久选择，报告后续仍可展示 profile。
        return dict(get_vitis_selection(server) or {})

    # 未显式指定时先沿用用户上次确认过的 Vitis 选择。
    dict_selected = dict(get_vitis_selection(server) or {})  # 持久化 Vitis 选择

    # 没有持久选择时采用探测候选的第一个稳定项。
    if not dict_selected and candidates:

        # 第一个候选来自远端扫描顺序，是无持久选择时的默认值。
        return dict(candidates[0])

    # 返回当前可用选择，可能为空字典并由后续平台合并补足。
    return dict_selected

# `_fill_missing_target_part_from_server` 只补缺失字段，不覆盖显式选择。
def _fill_missing_target_part_from_server(
    selected_profile: dict[str, Any],
    settings: Path,
    server: str,
) -> None:
    """用远端服务器事实补齐缺失的 target_part。

    参数:
        selected_profile: 待补齐的 Vitis profile。
        settings: erie-remote-ssh settings 路径。
        server: 远端服务器名称。

    返回:
        None: 本函数只在传入字典上原地补齐 target_part。
    """

    # 只要 profile 已经携带 target_part，就不再用服务器事实覆盖该显式选择。
    if str(selected_profile.get("target_part") or "").strip():

        # 已存在 target_part 时尊重用户或持久 profile 的明确选择。
        return

    # 远端 server 配置中的 part 是恢复时最直接的硬件目标事实。
    str_inferred_target_part = _infer_target_part_from_server(settings, server)  # 远端服务器配置推断出的 target_part

    # 推断成功后就地补齐 profile，调用方继续使用同一个字典。
    if str_inferred_target_part:

        # 只写入缺失的 target_part，不改变其他 Vitis profile 字段。
        selected_profile["target_part"] = str_inferred_target_part  # profile 中补齐的硬件 part 字段

# `_recover_board_platform_profile` 合并板卡平台选择并兜底推断 part。
def _recover_board_platform_profile(
    args: argparse.Namespace,
    server: str,
    remote_workdir: str,
    selected_profile: dict[str, Any],
) -> dict[str, Any]:
    """合并用户配置、远端平台选择和平台名推断信息。

    参数:
        args: CLI 参数命名空间。
        server: 远端服务器名称。
        remote_workdir: 远端项目工作目录。
        selected_profile: 已恢复的 Vitis profile。

    返回:
        dict[str, Any]: 合并后的 board profile。
    """

    # 远端治理配置提供 directory_contract 和平台根目录策略。
    dict_config = remote_validation_config()  # 远端验收治理配置

    # 板卡平台选择来自 CLI、持久配置和远端 workdir 综合解析。
    dict_board_selection = _resolve_board_platform_selection(  # 结合目录契约解析出的 board 平台选择字段
        args, server, remote_workdir, selected_profile, dict_config["directory_contract"]  # CLI、服务器、workdir、Vitis profile 与目录契约的联合输入
    )

    # 平台字段覆盖补齐 selected_profile，但不丢弃已确认的 Vitis 信息。
    dict_selected = _merge_profile_fields(selected_profile, dict_board_selection)  # 合并后的 board profile

    # target_part 仍缺失时最后尝试从 xpfm/platform 名称推断。
    if not str(dict_selected.get("target_part") or "").strip():

        # 平台选择通常携带 xpfm 路径，可用于 U55C part 推断。
        str_inferred_target_part = infer_target_part_from_platform_selection(dict_selected)  # 平台推断 target_part

        # 只有平台信息确实推出有效 part 时，才把该结果写回选择字典。
        if str_inferred_target_part:

            # 平台推断只在 profile 缺少 part 时写入，避免覆盖 server 事实。
            dict_selected["target_part"] = str_inferred_target_part  # 平台选择推断出的硬件 part 字段

    # 返回合并结果，供探测和报告生成复用。
    return dict_selected

# `_probe_recoverable_board_result` 是唯一读取远端日志状态的函数。
def _probe_recoverable_board_result(
    server: str,
    settings: Path,
    helper: ErieHelper,
    remote_run_dir: str,
) -> dict[str, Any]:
    """探测远端目录中是否已有可恢复的 board 运行证据。

    参数:
        server: 远端服务器名称。
        settings: erie-remote-ssh settings 路径。
        helper: erie-remote-ssh 调用封装。
        remote_run_dir: 待探测的远端运行目录。

    返回:
        dict[str, Any]: board 日志、xclbin 和 v++ 日志探测状态。
    """

    # 远端目录必须经过 shell quote，避免路径字符影响 bash 探测脚本。
    str_remote_run_dir = shlex.quote(remote_run_dir)  # shell 安全的远端目录

    # 探测脚本只读取日志和 artifact 存在性，不修改远端状态。
    str_probe_script = f"""
if [ -f {str_remote_run_dir}/artifacts/board_run.log ]; then
  if grep -q '{BOARD_STATUS_MARKER} passed' {str_remote_run_dir}/artifacts/board_run.log; then
    echo board_status=passed
  elif grep -q '{BOARD_STATUS_MARKER} failed' {str_remote_run_dir}/artifacts/board_run.log; then
    echo board_status=failed
  else
    echo board_status=unknown
  fi
  echo board_log=1
else
  echo board_status=missing
  echo board_log=0
fi
if [ -f {str_remote_run_dir}/artifacts/kernel.xclbin ]; then echo xclbin=1; else echo xclbin=0; fi
if [ -f {str_remote_run_dir}/artifacts/v++_kernel.log ]; then echo vpp_log=1; else echo vpp_log=0; fi
"""  # 远端 board 证据探测脚本

    # helper.exec 返回 key=value 输出，后续统一用字段解析器读取。
    str_output = helper.exec(server, ["bash", "-lc", str_probe_script], settings=settings)  # 远端探测输出

    # 返回原始 output 便于报告保留远端探测细节。
    return {
        "board_status": field_from_equals_output(str_output, "board_status"),
        "board_log": field_from_equals_output(str_output, "board_log"),
        "xclbin": field_from_equals_output(str_output, "xclbin"),
        "vpp_log": field_from_equals_output(str_output, "vpp_log"),
        "output": str_output,
    }
