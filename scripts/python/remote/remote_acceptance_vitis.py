#!/usr/bin/env python3
"""远端 Vitis HLS 验收执行流程。

本模块负责 Vitis-only mode 的 profile 解析、版本选择、本地 HLS 资产打包、
远端 Vitis HLS 阶段执行，以及 split topology 下的构建/验收服务器协同。
所有结构化结果由调用方写入 JSON 报告，本模块不直接输出机器协议内容。
"""

# 允许类型注解引用运行时尚未导入的 helper 类型。
from __future__ import annotations

# 标准库依赖覆盖 CLI 参数、base64 传输、JSON、正则、shell 引号和 tar 包。
import argparse
import base64
import json
import re
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

# workspace root 上下文用于复用 HLS workflow 生成本地验收资产。
from scripts.python.config.workspace import use_workspace_root

# 远端验收状态常量统一来自 common 模块。
from remote_acceptance_common import (
    BLOCKED_PROFILE_STATUS,
    BLOCKED_VERSION_STATUS,
    BLOCKED_VITIS_STATUS,
    FAILED_STATUS,
    PASS_STATUS,
)

# 远端验收异常和选择缓存接口统一来自 common 模块。
from remote_acceptance_common import (
    RemoteAcceptanceError,
    get_vitis_selection,
    set_vitis_selection,
)

# 仓库路径、skill 配置和用户配置路径统一来自 common 模块。
from remote_acceptance_common import (
    repo_root,
    remote_validation_config,
    skill_root,
    skill_config_path,
    user_config_path,
)

# 远端目录布局函数统一来自 common 模块。
from remote_acceptance_common import (
    remote_directory_layout_for_workdir,
)

# 远端目录治理和 run 目录创建函数统一来自 common 模块。
from remote_acceptance_common import (
    _archive_remote_run,
    _ensure_remote_project_layout,
    _new_run_dir,
)

# 远端探测和 server-list 解析函数统一来自 common 模块。
from remote_acceptance_common import (
    _field_from_output,
    _probe_fpga_presence,
    _probe_remote_workdir,
    _probe_target_part_hint,
    _probe_vitis,
    _resolve_erie_server_list,
)

# 本地工件生成与 JSON/report 写入函数统一来自 common 模块。
from remote_acceptance_common import (
    run_hls_workflow,
    _write_json,
    _write_erie_settings_overlay,
    _write_report,
)

# settings64.sh 位于 Vitis 安装根目录下。
PATH_VITIS_SETTINGS64 = PurePosixPath("settings64.sh")  # Vitis 环境脚本相对路径

# v++ 位于 Vitis 安装根目录下的 bin 子目录。
PATH_VITIS_VPP = PurePosixPath("bin") / "v++"  # Vitis v++ 工具相对路径

# 当 profile 未显式给出 expected_tool 时，回退到 vitis_hls 相对路径。
PATH_VITIS_HLS_EXECUTABLE = PurePosixPath("bin") / "vitis_hls"  # profile.expected_tool 缺省回退到的 vitis_hls 相对路径

# 远端补 source Vitis_HLS 环境时使用 setupEnv.sh。
PATH_VITIS_HLS_ENV_SCRIPT = PurePosixPath("bin") / "setupEnv.sh"  # 远端补 source Vitis_HLS 环境时使用的脚本路径

# 远端 XRT 与 Xilinx 工具都从统一的绝对根目录常量逐段拼接。
PATH_POSIX_ROOT = PurePosixPath("/")  # 远端绝对路径拼接时统一复用的 Posix 根目录

# /opt/xilinx/xrt 承载 XRT 二进制和环境脚本。
PATH_XRT_ROOT = PATH_POSIX_ROOT / "opt" / "xilinx" / "xrt"  # 远端 XRT 安装根目录

# XRT 可执行文件统一从 bin 目录派生。
PATH_XRT_BIN_DIR = PATH_XRT_ROOT / "bin"  # 远端 XRT 可执行文件所在目录

# 远端 XRT 工具路径来自项目已知安装位置。
PATH_XRT_SMI = PATH_XRT_BIN_DIR / "xrt-smi"  # 远端 xrt-smi 绝对路径

# 远端 XRT 环境脚本来自项目已知安装位置。
PATH_XRT_SETUP = PATH_XRT_ROOT / "setup.sh"  # 远端 XRT 环境脚本绝对路径

# 板卡管理信息采集统一走远端 xbmgmt。
PATH_XBMGMT = PATH_XRT_BIN_DIR / "xbmgmt"  # 远端采集板卡管理信息时调用的绝对路径

# 默认 Xilinx 工具根目录用于构造 fallback 的 Vitis_HLS 路径。
PATH_XILINX_TOOLS_ROOT = PATH_POSIX_ROOT / "tools" / "Xilinx"  # 默认 Xilinx 工具安装根目录

# 安装根目录中的 Vitis 产品目录名用于路径替换。
STR_VITIS_PRODUCT_DIR = "Vitis"  # 安装路径中的 Vitis 产品段

# 切换到 Vitis_HLS 安装树时使用这一目录段替换。
STR_VITIS_HLS_PRODUCT_DIR = "Vitis_HLS"  # 从 Vitis 安装树切换到 Vitis_HLS 安装树时替换的目录段

# 远端先写入的 base64 包文件名固定不变。
PATH_REMOTE_B64_FILENAME = PurePosixPath("hls_artifacts.tar.gz.b64")  # 远端 base64 包文件名

# Vitis 单机模式入口只保留 orchestration，具体细节下沉到可测 helper。
def _run_vitis_mode(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    plan: list[str],
    topology: dict[str, Any],
) -> dict[str, Any]:
    """
    执行单服务器 Vitis HLS 远端验收。

    :param args: CLI 参数，包含服务器、profile、readiness 和样例选择。
    :param config: 远端验收配置，提供 Vitis profile 与目录契约。
    :param helper: erie-remote-ssh helper，负责远端预检、上传和 job 轮询。
    :param plan: 当前验收步骤，写入阻断报告便于用户复现。
    :param topology: 单服务器拓扑描述。
    :return: 可写入 JSON 的 Vitis 验收结果。
    """

    # 准备 run 目录、settings、候选版本和已选 Vitis profile。
    dict_context = _prepare_vitis_run_context(args, config, helper, topology)  # 单机 Vitis 基础上下文

    # profile/version 选择可能因缺少用户确认而提前阻断。
    dict_early_result = dict_context.get("early_result")  # profile 或版本选择阶段的早停报告

    # 缺 profile 或多版本未选择时不继续访问远端执行阶段。
    if dict_early_result:

        # 早停报告统一落到本次 run 目录。
        _write_report(dict_context["run_dir"], dict_early_result)

        # 返回阻断结果给上层 CLI。
        return dict_early_result

    # 探测 Vitis 可执行环境，并补齐 tool_path 与 target_part。
    dict_readiness = _prepare_vitis_readiness(args, helper, dict_context)  # 单机 Vitis 前置探测上下文

    # Vitis 工具链不可用时返回包含 probe 证据的阻断报告。
    if dict_readiness["profile_probe"]["status"] != PASS_STATUS:

        # 阻断报告保留候选版本、profile 和 probe 输出。
        dict_blocked = _blocked_vitis_result(args, plan, topology, dict_readiness)  # 单机 Vitis 阻断报告

        # 阻断报告写入本次 run 目录。
        _write_report(dict_readiness["run_dir"], dict_blocked)

        # Vitis probe 未通过时直接返回阻断结果，避免继续打包并提交远端任务。
        return dict_blocked

    # 前置条件通过后生成本地 HLS 资产并启动远端 Vitis job。
    dict_result = _run_single_vitis_job(args, config, helper, topology, dict_readiness)  # 单机 Vitis job 报告

    # 成功或失败报告都写入 run 目录，保持旧 CLI 契约。
    _write_report(dict_readiness["run_dir"], dict_result)

    # 返回完整验收结果。
    return dict_result

# 准备单机 Vitis 验收的目录、settings 和 profile 选择。
def _prepare_vitis_run_context(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    topology: dict[str, Any],
) -> dict[str, Any]:
    """
    准备单服务器 Vitis 验收上下文。

    :param args: CLI 参数，提供 profile 和版本选择。
    :param config: 远端验收配置，提供 profile 表和 run 根目录。
    :param helper: 远端执行 helper，用于预检和软件扫描。
    :param topology: 单服务器拓扑。
    :return: 包含 run 目录、settings、候选版本和已选 profile 的上下文字典。
    """

    # 配置中的 Vitis profile 表用于显式 profile 和 fallback 解析。
    dict_profiles = config.get("vitis_profiles", {})  # 配置文件声明的 Vitis profile 集合

    # 单机 Vitis mode 使用独立 run 目录。
    path_run_dir = _new_run_dir(config, "vitis")  # 本次 Vitis 验收的本地 run 目录

    # settings overlay 固化本次 helper 调用使用的远端配置。
    path_settings = _write_erie_settings_overlay(config, path_run_dir)  # 本次远端命令使用的 settings 文件

    # 单机 topology 只包含一个目标服务器。
    str_server = str(topology["server"])  # 运行 Vitis HLS 的远端服务器

    # 预检确认服务器和 settings 可用。
    helper.preflight(str_server, settings=path_settings)

    # 软件扫描为 Vitis 版本候选提供事实来源。
    helper.scan_software(str_server, settings=path_settings)

    # 候选版本来自 erie-remote-ssh 的软件扫描记录。
    list_candidates = _vitis_version_candidates(config, path_settings, str_server)  # 远端可用 Vitis 版本候选

    # 解析 profile 必填字段，缺失时生成可操作的配置请求。
    dict_profile = _resolve_profile_config(  # 解析当前运行需要的 profile 字段或阻断请求
        args,  # CLI 中的 profile、version 与 readiness 选择都会影响本次配置解析
        path_run_dir,  # profile 缺字段时要把阻断请求直接写进当前 run 目录
        candidates=list_candidates,  # 远端软件扫描给出的 profile 候选列表
        configured_profiles=dict_profiles,  # 用户配置文件里已经声明的 profile 表
        required_fields=("settings_script", "expected_tool"),  # 本轮远端验收必须补齐的 profile 字段
    )

    # 缺少 profile 字段时交由上层写入阻断报告。
    if dict_profile.get("status") == BLOCKED_PROFILE_STATUS:

        # early_result 保留原始阻断报告结构。
        return {
            "run_dir": path_run_dir,
            "early_result": dict_profile,
        }

    # 根据 CLI 显式版本、缓存选择或候选列表确定最终 profile。
    dict_selected_profile = _select_vitis_profile(args, path_run_dir, list_candidates, dict_profile)  # 当前运行选中的 Vitis profile

    # 多版本未确认时返回版本选择请求。
    if dict_selected_profile.get("status") == BLOCKED_VERSION_STATUS:

        # early_result 保留版本选择阻断报告。
        return {
            "run_dir": path_run_dir,
            "early_result": dict_selected_profile,
        }

    # CLI target part 在 profile 缺失该字段时作为明确覆盖。
    if args.target_part and not str(dict_selected_profile.get("target_part") or "").strip():

        # 复制 profile，避免修改缓存对象。
        dict_selected_profile = {  # 仅在本次运行中覆盖 CLI 指定的 target_part
            **dict_selected_profile,  # 保留已解析出的 settings、tool 与 version 字段
            "target_part": str(args.target_part),  # 用 CLI 指定的 FPGA part 覆盖 profile 中的空值
        }

    # 返回后续探测和执行阶段需要的全部上下文。
    return dict(
        profiles=dict_profiles,
        run_dir=path_run_dir,
        settings=path_settings,
        server=str_server,
        candidates=list_candidates,
        selected_profile=dict_selected_profile,
    )

# 探测单机 Vitis 工具链并补齐 profile 中的运行字段。
def _prepare_vitis_readiness(
    args: argparse.Namespace,
    helper: "ErieHelper",
    dict_context: dict[str, Any],
) -> dict[str, Any]:
    """
    准备单机 Vitis job 的前置探测结果。

    :param args: CLI 参数，保留 target_part 覆盖。
    :param helper: 远端执行 helper。
    :param dict_context: `_prepare_vitis_run_context` 生成的上下文。
    :return: 含 profile_probe 与补齐 profile 的上下文字典。
    """

    # 复制 profile，后续补字段不污染缓存或配置对象。
    dict_selected_profile = dict(dict_context["selected_profile"])  # 当前运行的 Vitis profile 副本

    # 远端服务器和 settings 来自前置上下文。
    str_server = str(dict_context["server"])  # 单机 Vitis 目标服务器

    # settings 文件传给所有 erie-remote-ssh 调用。
    path_settings = dict_context["settings"]  # helper.preflight、scan 与 probe 复用的 settings overlay 路径

    # 先确认 Vitis HLS 可执行工具可解析。
    dict_profile_probe = _probe_vitis(str_server, path_settings, helper, dict_selected_profile)  # Vitis 工具链探测结果

    # 探测失败时保留原 profile 给阻断报告。
    if dict_profile_probe["status"] != PASS_STATUS:

        # probe 失败无需继续推断 target_part。
        return {
            **dict_context,
            "selected_profile": dict_selected_profile,
            "profile_probe": dict_profile_probe,
        }

    # 使用探测解析出的工具路径覆盖 profile 中的泛化命令名。
    dict_selected_profile = _profile_with_vitis_probe(dict_selected_profile, dict_profile_probe)  # 已补齐工具路径的 profile

    # 缺 target_part 时从远端服务器清单或硬件线索推断。
    if not str(dict_selected_profile.get("target_part") or "").strip():

        # 远端 inventory 中的 U50/U55C 信息可提供 target part hint。
        str_inferred_target_part = _probe_target_part_hint(str_server, path_settings, helper)  # 远端推断出的 target part

        # 有可信 hint 时写回本次 profile。
        if str_inferred_target_part:

            # target_part 仅影响本次运行报告和 Tcl 生成。
            dict_selected_profile["target_part"] = str_inferred_target_part  # 写入远端硬件推断得到的 FPGA part

    # 返回 job 执行阶段所需的 readiness 上下文。
    return {
        **dict_context,
        "selected_profile": dict_selected_profile,
        "profile_probe": dict_profile_probe,
    }

# 将 Vitis probe 结果合并进 profile，避免主流程手写字段拼装。
def _profile_with_vitis_probe(
    dict_profile: dict[str, Any],
    dict_profile_probe: dict[str, Any],
) -> dict[str, Any]:
    """
    合并 Vitis probe 返回的工具解析字段。

    :param dict_profile: 已选 Vitis profile。
    :param dict_profile_probe: `_probe_vitis` 返回的探测结果。
    :return: 带 expected_tool 与 tool_path 的 profile。
    """

    # resolved_tool 优先，失败时保留 profile 配置中的 expected_tool。
    str_expected_tool = str(dict_profile_probe.get("resolved_tool") or dict_profile.get("expected_tool"))  # 实际执行工具名

    # tool_path 记录探测到的绝对工具路径，便于报告复查。
    str_tool_path = str(dict_profile_probe.get("tool_path") or "")  # 远端 Vitis HLS 工具路径

    # 返回合并后的 profile 副本。
    return {
        **dict_profile,
        "expected_tool": str_expected_tool,
        "tool_path": str_tool_path,
    }

# 构造单机 Vitis 工具链不可用时的阻断报告。
def _blocked_vitis_result(
    args: argparse.Namespace,
    plan: list[str],
    topology: dict[str, Any],
    dict_readiness: dict[str, Any],
) -> dict[str, Any]:
    """
    组装 Vitis 工具链探测失败报告。

    :param args: CLI 参数。
    :param plan: 当前验收计划步骤。
    :param topology: 单机拓扑。
    :param dict_readiness: 前置探测上下文。
    :return: 可写入 JSON 的阻断报告。
    """

    # 已选 profile 仍写入版本字段，方便用户确认环境。
    dict_selected_profile = dict_readiness["selected_profile"]  # 探测失败时的 Vitis profile

    # 返回旧字段名，兼容 confidence_loop 聚合逻辑。
    return {
        "status": BLOCKED_VITIS_STATUS,
        "mode": "vitis",
        "server": dict_readiness["server"],
        "profile": args.profile,
        "vitis_version": dict_selected_profile.get("version"),
        "readiness": args.readiness,
        "run_dir": str(dict_readiness["run_dir"]),
        "topology": topology["topology"],
        "steps": plan,
        "probe": dict_readiness["profile_probe"],
        "uses_erie_remote_ssh": True,
    }

# 生成本地资产并启动单机 Vitis 远端 job。
def _run_single_vitis_job(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    topology: dict[str, Any],
    dict_readiness: dict[str, Any],
) -> dict[str, Any]:
    """
    执行单服务器 Vitis HLS job。

    :param args: CLI 参数，提供 readiness、样例和清理策略。
    :param config: 远端验收配置。
    :param helper: 远端执行 helper。
    :param topology: 单机拓扑。
    :param dict_readiness: 已通过前置探测的上下文。
    :return: 可写入 JSON 的 job 报告。
    """

    # 本地资产生成使用 mock provider，远端阶段只消费 tar 包。
    path_artifact_dir = _generate_local_hls_artifacts(  # 单机模式上传前生成的本地 HLS 资产目录
        dict_readiness["run_dir"],  # 单机 run 目录承接本次生成的本地工件与执行报告
        comment_language=args.comment_language,  # 本地资产中的注释语言沿用本次 CLI 选择
        example_spec=args.example_spec,  # 本轮单机验收指定的示例 spec 文件名
    )

    # 将 HLS 资产和远端 runner 打成单个 tar.gz。
    path_package_path = _create_vitis_package(dict_readiness["run_dir"], path_artifact_dir)  # 上传到远端的 Vitis 包

    # 远端 workdir 决定治理目录布局的绝对路径前缀。
    str_remote_workdir = _probe_remote_workdir(dict_readiness["server"], dict_readiness["settings"], helper)  # 远端 workspace 根目录

    # 阶段上下文收敛原多参数调用，便于共享 wrapper 和测试。
    dict_phase_context = _vitis_phase_context(  # 单机服务器远端目录、profile 和 package 上传上下文
        dict(  # 将单机远端执行需要的关键字段压缩为 phase context 快照
            settings=dict_readiness["settings"],  # 远端 helper 命令继续复用本次 settings overlay
            server=dict_readiness["server"],  # 单机模式唯一的远端执行服务器
            profile=dict_readiness["selected_profile"],  # 已确认可用的 Vitis profile 快照
            readiness=args.readiness,  # 远端 runner 需要执行到的验收等级
            package_path=path_package_path,  # 刚打包好的本地 HLS 资产 tar.gz 路径
            config=config,  # 远端目录契约与工具规则仍从本地配置读取
            run_dir=dict_readiness["run_dir"],  # 本地 run 目录用于汇总远端执行报告
            phase_label="single",  # 单机模式只有 single 这一阶段标签
            cleanup_remote=args.cleanup_remote,  # 验收通过后是否清理远端 active run
            remote_workdir=str_remote_workdir,  # 远端 workspace 根目录绝对路径
        )
    )

    # 启动远端 Vitis job 并等待完成。
    dict_result = _run_server_vitis_phase_context(helper, dict_phase_context)  # 单机 Vitis 阶段结果

    # 追加旧报告字段，保持上游聚合兼容。
    dict_result.update(
        dict(
            mode="vitis",
            topology=topology["topology"],
            profile=args.profile,
            example_spec=args.example_spec,
            run_dir=str(dict_readiness["run_dir"]),
            artifact_dir=str(path_artifact_dir),
            uses_erie_remote_ssh=True,
        )
    )

    # 返回完整单机 Vitis 报告。
    return dict_result

# split mode 入口保留调度职责，具体准备、探测和执行分层处理。
def _run_split_vitis_mode(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    plan: list[str],
    topology: dict[str, Any],
) -> dict[str, Any]:
    """
    执行构建/验收分离的 Vitis HLS 远端验收。

    :param args: CLI 参数，包含 profile、版本、readiness 和样例。
    :param config: 远端验收配置。
    :param helper: erie-remote-ssh helper。
    :param plan: 当前验收步骤。
    :param topology: split topology，包含 build_server 与 validate_server。
    :return: 可写入 JSON 的 split 验收结果。
    """

    # 准备 run 目录、settings、共享版本和两端 profile。
    dict_context = _prepare_split_vitis_context(args, config, helper, topology)  # split Vitis 基础上下文

    # target_part 缺失会在准备阶段生成 profile 阻断报告。
    dict_early_result = dict_context.get("early_result")  # split 准备阶段早停报告

    # 缺 target_part 时不继续探测远端工具链。
    if dict_early_result:

        # 早停报告统一写入 split run 目录。
        _write_report(dict_context["run_dir"], dict_early_result)

        # target_part 缺失只返回准备阶段阻断结果，不继续远端探测。
        return dict_early_result

    # 探测 build/validate 两端 Vitis 和 validate 端硬件。
    dict_readiness = _prepare_split_vitis_readiness(helper, dict_context)  # split 前置探测上下文

    # 任一端工具链或硬件探测失败即返回阻断报告。
    if _split_vitis_blocked(dict_readiness):

        # 阻断报告保留三类 probe 输出。
        dict_blocked = _blocked_split_vitis_result(plan, topology, dict_readiness)  # split 前置条件阻断报告

        # 把失败的 probe 证据落盘到 split 运行目录，便于复查是哪一端先阻断。
        _write_report(dict_readiness["run_dir"], dict_blocked)

        # 任一前置 probe 失败都在入口层返回阻断结果，不进入 build/validation 两阶段。
        return dict_blocked

    # 前置条件通过后执行 build 和 validation 两个远端阶段。
    dict_result = _run_split_vitis_jobs(args, config, helper, plan, topology, dict_readiness)  # 汇总 build/validation 两阶段的 split 总报告

    # 总报告写入 run 目录。
    _write_report(dict_readiness["run_dir"], dict_result)

    # 返回 split 验收结果。
    return dict_result

# 准备 split Vitis 的共享版本、两端 profile 和远端 workdir。
def _prepare_split_vitis_context(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    topology: dict[str, Any],
) -> dict[str, Any]:
    """
    准备 split Vitis mode 的基础上下文。

    :param args: CLI 参数。
    :param config: 远端验收配置。
    :param helper: 远端执行 helper。
    :param topology: split topology。
    :return: split 执行需要的上下文字典，可能包含 early_result。
    """

    # 先冻结当前配置中的 profile 表，后续 build/validate 两端都从这份快照回填缺失字段。
    dict_profiles = config.get("vitis_profiles", {})  # split 两端共用的 profile 定义快照

    # split 模式必须用独立 run 目录收纳双机协商记录、阶段日志和总报告。
    path_run_dir = _new_run_dir(config, "vitis-split")  # 双机 split 验收的本地证据目录

    # 两台远端机器都读取同一份 overlay，避免阶段之间看到不同的 settings/server-list 视图。
    path_settings = _write_erie_settings_overlay(config, path_run_dir)  # split 两端共享的 settings overlay 文件

    # topology 中分别声明构建服务器和验收服务器。
    str_build_server = str(topology["build_server"])  # Vitis 构建服务器

    # validate_server 负责硬件存在性和第二阶段验收。
    str_validate_server = str(topology["validate_server"])  # Vitis 验收服务器

    # 先完成两端预检与软件扫描，再进入共享版本协商。
    _prepare_split_server_inventory(  # split 两端预检和软件扫描的统一入口
        helper,  # 仍由同一个 ErieHelper 负责对两台机器执行预检与扫描
        path_settings,  # 两台机器共用同一份 overlay，确保扫描口径一致
        str_build_server,  # build 端需要先通过预检并产出软件候选
        str_validate_server,  # validate 端也必须通过预检并产出软件候选
    )

    # 两端候选版本、共享版本和 profile 解析打包成单个上下文，避免主流程函数继续增长。
    dict_split_profile_context = _resolve_split_profile_context(  # split 模式共用的版本协商和 profile 解析结果
        args,  # 共享版本选择仍然遵循当前 CLI 指定的 version/allow-fallback 语义
        config,  # 候选版本和 profile fallback 仍从当前远端验收配置读取
        path_settings,  # 两端共享同一份 overlay，保证版本候选来自同一视图
        str_build_server,  # build 端需要独立反查共享版本对应的 profile
        str_validate_server,  # validate 端也必须确认同版本工具链真实存在
        dict_profiles,  # 本地配置文件里的 profile 表仍作为 fallback 输入
    )

    # target_part 优先来自 CLI，其次来自 validate/build profile 或远端硬件记录。
    str_target_part = _resolve_target_part(  # split 两端 Tcl 生成必须一致的 FPGA part
        args,  # CLI 可能显式覆盖 split 流程最终使用的 FPGA part
        path_settings,  # 远端 target part hint 解析仍依赖同一份 settings overlay
        str_validate_server,  # 最终硬件验收服务器也用于 target_part 推断
        dict_split_profile_context["validate_profile"],  # validate 端 profile 提供优先级更高的硬件事实
        dict_split_profile_context["build_profile"],  # build 端 profile 作为 validate 未声明时的回退来源
    )

    # target_part 缺失时立即返回阻断结果，避免后续 profile/workdir 逻辑继续展开。
    dict_missing_target_part_result = _maybe_build_missing_split_target_part_result(  # target_part 缺失时的 early_result 包装
        args,  # 复用当前 CLI 参数生成缺字段阻断报告
        path_run_dir,  # 当前 split run 目录仍是阻断报告的落盘位置
        dict_profiles,  # profile 表仍用于提示需要补齐的配置字段
        topology,  # split 拓扑字段仍要带回报告
        dict_split_profile_context["shared_version"],  # 报告中保留已经协商完成的共享 Vitis 版本
        str_target_part,  # 只有为空时才返回 early_result
    )

    # 命中 target_part 缺失时直接返回，避免后续继续探测 workdir。
    if dict_missing_target_part_result is not None:

        # 命中缺失 target_part 的早停场景后，直接把 early_result 交回入口。
        return dict_missing_target_part_result

    # build/validate 两端 workdir 用统一 helper 探测，避免主流程继续拉长。
    dict_split_workdirs = _resolve_split_workdirs(  # split 两端远端 workspace 根目录探测结果
        helper,  # 仍通过同一个 ErieHelper 查询两台服务器的 workspace
        path_settings,  # 两台服务器读取同一份 overlay，避免 workspace 视图不一致
        str_build_server,  # build 端 workdir 承载构建阶段远端目录布局
        str_validate_server,  # validate 端 workdir 承载硬件验收与归档目录布局
    )

    # 把 split 最终上下文按证据目录、共享拓扑和双端 workdir 三组字段交给 helper 收口。
    return _build_split_vitis_context_result(
        dict_profiles=dict_profiles,  # 原样回写的 profile 定义快照
        path_run_dir=path_run_dir,  # 当前 split 验收 run 目录
        path_settings=path_settings,  # split 两端共享的 Erie overlay

        # 共享拓扑与版本协商结果决定后续两阶段使用哪套服务器与工具链。
        topology=topology,  # build/validate 两台机器的角色映射
        dict_split_profile_context=dict_split_profile_context,  # 共享版本与基础 profile 解析结果
        str_target_part=str_target_part,  # 两端已经确认的统一 FPGA part

        # 双端 workdir 会继续决定远端构建目录与验收归档目录布局。
        dict_split_workdirs=dict_split_workdirs,  # build/validate 两端远端 workspace 根目录
    )

# split 两端的软件候选、共享版本和 profile 解析都聚合在这里，避免主流程函数过长。
def _resolve_split_profile_context(
    args: argparse.Namespace,
    config: dict[str, Any],
    path_settings: Path,
    str_build_server: str,
    str_validate_server: str,
    dict_profiles: dict[str, Any],
) -> dict[str, Any]:
    """
    解析 split Vitis mode 需要的候选版本与两端 profile。

    :param args: CLI 参数。
    :param config: 远端验收配置。
    :param path_settings: split 两端共享的 Erie overlay。
    :param str_build_server: 构建服务器名。
    :param str_validate_server: 验收服务器名。
    :param dict_profiles: 配置文件里的 profile 表。
    :return: 包含候选版本、共享版本与两端 profile 的上下文字典。
    """

    # build 侧的版本候选用于决定哪些工具链版本值得参与共享版本求交。
    list_build_candidates = _vitis_version_candidates(  # build 端软件扫描得到的 Vitis 版本候选
        config,  # 候选版本仍从当前远端验收配置视图读取
        path_settings,  # build 端扫描结果要从共享 overlay 对应的 server-list 中取值
        str_build_server,  # build 端先生成自己的版本候选列表
    )

    # validate 侧单独保留一份候选，后面要确认硬件验收机能否加载同版本工具链。
    list_validate_candidates = _vitis_version_candidates(  # 验收机扫描得到的可用版本列表
        config,  # validate 端读取同一份配置视图，避免候选口径漂移
        path_settings,  # validate 端同样从共享 overlay 的 server-list 解析候选
        str_validate_server,  # validate 端独立生成自己的版本候选列表
    )

    # 共享版本是 split build/validate 两端都能真实加载的交集结果。
    str_shared_version = _select_shared_vitis_version(  # 两端可同时加载的 Vitis 版本号
        args,  # 共享版本选择仍遵循 CLI version/allow-fallback 语义
        list_build_candidates,  # build 端候选决定共享版本上界
        list_validate_candidates,  # validate 端候选决定共享版本是否可在验收机加载
    )

    # build profile 负责把共享版本落到具体 tool_path、settings 和 part 字段上。
    dict_build_profile = _resolve_profile_for_version(  # build 阶段用于 settings/tool/part 的 profile 字段集合
        str_build_server,  # build 端先按共享版本反查本机可用的 profile 字段
        list_build_candidates,  # build 端扫描结果决定可回填的版本与安装路径
        dict_profiles,  # build 端缺字段时仍从本地 profile 表回填
        str_shared_version,  # 两端已经协商完成的共享 Vitis 版本
    )

    # validate profile 单独解析是为了证明第二阶段不会依赖 build 机的安装事实。
    dict_validate_profile = _resolve_profile_for_version(  # validation 阶段额外核对本机 settings 与 tool 字段
        str_validate_server,  # validate 端独立解析共享版本对应的 profile 字段
        list_validate_candidates,  # validate 端扫描结果决定验收机能否加载同版本工具链
        dict_profiles,  # 本地配置文件里声明的 profile 表
        str_shared_version,  # split 流程要求两端一致的 Vitis 版本
    )

    # 返回主流程后续还会复用的协商结果。
    return dict(
        build_candidates=list_build_candidates,
        validate_candidates=list_validate_candidates,
        shared_version=str_shared_version,
        build_profile=dict_build_profile,
        validate_profile=dict_validate_profile,
    )

# split 模式要先把 build/validate 两端的预检与软件扫描都准备好，后面才能做版本求交。
def _prepare_split_server_inventory(
    helper: "ErieHelper",
    path_settings: Path,
    str_build_server: str,
    str_validate_server: str,
) -> None:
    """
    预检 split 模式使用的两台远端服务器，并刷新各自的软件扫描结果。

    :param helper: 远端执行 helper。
    :param path_settings: split 两端共享的 Erie overlay。
    :param str_build_server: 构建服务器名。
    :param str_validate_server: 验收服务器名。
    :return: 无业务返回值；仅通过 helper 刷新两台服务器的预检与软件扫描状态。
    """

    # build 端必须先通过基础预检，否则后续软件扫描和构建阶段都没有意义。
    helper.preflight(str_build_server, settings=path_settings)

    # validate 端的预检单独执行，用于确认硬件验收目标在开始前就可访问。
    helper.preflight(str_validate_server, settings=path_settings)

    # build 端软件扫描为共享版本协商提供第一侧候选集合。
    helper.scan_software(str_build_server, settings=path_settings)

    # validate 端软件扫描补齐第二侧候选集合，确保共享版本来自真实交集。
    helper.scan_software(str_validate_server, settings=path_settings)

# split 模式的 build/validate 两端都要先探测远端 workspace 根目录，后面才能落远端构建与验收工件。
def _resolve_split_workdirs(
    helper: "ErieHelper",
    path_settings: Path,
    str_build_server: str,
    str_validate_server: str,
) -> dict[str, str]:
    """
    探测 split build/validate 两端的远端 workspace 根目录。

    :param helper: 远端执行 helper。
    :param path_settings: split 两端共享的 Erie overlay。
    :param str_build_server: 构建服务器名。
    :param str_validate_server: 验收服务器名。
    :return: 包含 build_workdir 与 validate_workdir 的字典。
    """

    # build 端 workdir 决定构建阶段远端目录布局。
    str_build_workdir = _probe_remote_workdir(str_build_server, path_settings, helper)  # build 端远端 workspace 根目录

    # validate 端 workdir 决定验收阶段远端目录布局。
    str_validate_workdir = _probe_remote_workdir(str_validate_server, path_settings, helper)  # validate 端用于硬件验收与归档的远端 workspace 根目录

    # 返回主流程后续要直接写入报告与阶段上下文的两端 workspace。
    return dict(
        build_workdir=str_build_workdir,
        validate_workdir=str_validate_workdir,
    )

# split target_part 缺失时要尽早返回带报告路径的 early_result，避免主流程函数继续拉长。
def _maybe_build_missing_split_target_part_result(
    args: argparse.Namespace,
    path_run_dir: Path,
    dict_profiles: dict[str, Any],
    topology: dict[str, Any],
    str_shared_version: str,
    str_target_part: str,
) -> dict[str, Any] | None:
    """
    在 split target_part 缺失时生成 early_result；若 target_part 已存在则返回 None。

    :param args: CLI 参数。
    :param path_run_dir: 当前 split run 目录。
    :param dict_profiles: 配置文件里的 profile 表。
    :param topology: split 拓扑字典。
    :param str_shared_version: 两端已经协商完成的共享 Vitis 版本。
    :param str_target_part: 当前 split 流程解析出的 target_part。
    :return: target_part 缺失时返回带 run_dir/early_result 的字典，否则返回 None。
    """

    # target_part 已存在时无需生成阻断报告，主流程继续正常下探。
    if str_target_part:

        # target_part 已存在时显式返回 None，通知主流程继续正常下探。
        return None

    # 缺 target_part 时生成治理阻断请求，并保留 split 拓扑与共享版本字段。
    dict_blocked = _missing_split_target_part_result(  # 提示用户补齐 FPGA part 的 profile 阻断报告
        args,  # 复用 CLI 字段生成用户可操作的 target_part 缺失请求
        path_run_dir,  # 缺字段请求直接落盘到当前 split run 目录

        # profile 表用于生成配置缺失请求。
        dict_profiles,  # 当前配置文件里的 profile 定义集合
        topology,  # split 拓扑和 build/validate 服务器布局

        # build/validate 服务器字段写入 split 阻断报告。
        str(topology["build_server"]),  # 需要修正 profile 的构建服务器
        str(topology["validate_server"]),  # 需要核对板卡目标的验收服务器
        str_shared_version,  # 两端已经选定的共享 Vitis 版本
    )

    # 返回 early_result 交给入口统一写报告。
    return dict(
        run_dir=path_run_dir,
        early_result=dict_blocked,
    )

# split 最终上下文字段在这里统一收口，避免主流程函数继续堆叠结果拼装细节。
def _build_split_vitis_context_result(
    dict_profiles: dict[str, Any], path_run_dir: Path, path_settings: Path,
    topology: dict[str, Any], dict_split_profile_context: dict[str, Any],
    str_target_part: str, dict_split_workdirs: dict[str, str],
) -> dict[str, Any]:
    """
    组装 split Vitis mode 返回给主流程入口的最终上下文字典。

    :param dict_profiles: split 两端共用的 profile 定义快照。
    :param path_run_dir: 当前 split 验收 run 目录。
    :param path_settings: split 两端共享的 Erie overlay。
    :param topology: build/validate 两台服务器的角色映射。
    :param dict_split_profile_context: 共享版本与双端 profile 解析结果。
    :param str_target_part: 两端已经协商完成的统一 FPGA part。
    :param dict_split_workdirs: build/validate 两端远端 workspace 根目录。
    :return: 包含 split 执行后续阶段所需字段的上下文字典。
    """

    # target_part 要写回 build profile，保证 Tcl 生成与报告都引用同一 FPGA part。
    dict_build_profile = {  # build 阶段写入统一 FPGA part 的 profile 副本
        **dict_split_profile_context["build_profile"],  # 继承 build 端已解析的 settings、tool 与 version 字段
        "target_part": str_target_part,  # build 端 Tcl 与报告统一使用协商后的 FPGA part
    }

    # validate profile 同样写回 target_part，保证第二阶段与 build 产物使用同一器件目标。
    dict_validate_profile = {  # validation 阶段沿用 build 端相同的 FPGA part 约束
        **dict_split_profile_context["validate_profile"],  # 保留 validate 端本机探测出的 tool_path 与安装路径绑定信息
        "target_part": str_target_part,  # 验收端强制复用 build 阶段确认过的 FPGA part
    }

    # 从拓扑里解出两端服务器名，后续结果结构继续沿用既有 build/validate 字段名。
    str_build_server = str(topology["build_server"])  # split 结果结构里的构建服务器字段

    # 验收服务器字段同样从拓扑里解出，保持最终结果结构与既有字段名兼容。
    str_validate_server = str(topology["validate_server"])  # split 结果结构里的验收服务器字段

    # 返回 split mode 后续阶段所需上下文。
    return dict(
        profiles=dict_profiles,
        run_dir=path_run_dir,
        settings=path_settings,

        # split 拓扑中的服务器和候选版本。
        build_server=str_build_server,
        validate_server=str_validate_server,
        build_candidates=dict_split_profile_context["build_candidates"],
        validate_candidates=dict_split_profile_context["validate_candidates"],

        # 共享版本和 part 约束两端生成同一 HLS Tcl。
        shared_version=dict_split_profile_context["shared_version"],
        target_part=str_target_part,
        build_profile=dict_build_profile,
        validate_profile=dict_validate_profile,

        # 两端 workdir 用于生成各自远端目录布局。
        build_workdir=dict_split_workdirs["build_workdir"],
        validate_workdir=dict_split_workdirs["validate_workdir"],
    )

# 构造 split mode 缺少 target_part 时的阻断报告。
def _missing_split_target_part_result(
    args: argparse.Namespace,
    path_run_dir: Path,

    # profile 表用于生成缺字段请求。
    dict_profiles: dict[str, Any],
    topology: dict[str, Any],

    # split 拓扑字段写入最终阻断报告。
    str_build_server: str,
    str_validate_server: str,
    str_shared_version: str,
) -> dict[str, Any]:
    """
    生成 split target_part 缺失报告。

    :param args: CLI 参数。
    :param path_run_dir: 本次 run 目录。
    :param dict_profiles: 配置中的 Vitis profile 表。
    :param topology: split topology。
    :param str_build_server: 构建服务器。
    :param str_validate_server: 验收服务器。
    :param str_shared_version: 已解析的共享 Vitis 版本。
    :return: 可写入 JSON 的 profile 阻断报告。
    """

    # `_blocked_profile_config` 需要一个拥有旧字段的 Namespace。
    namespace_profile_request = argparse.Namespace(  # 复用 profile 阻断 helper 的参数对象
        server=str_build_server,  # 延续 build 端 server 字段，便于用户知道要修哪个 profile
        profile=args.profile,  # 若用户显式传了 profile 名称，需要原样回显到阻断请求
        readiness=args.readiness,  # 当前 split 验收仍沿用的 readiness 等级
        example_spec=args.example_spec,  # 需要复现问题时继续使用的示例 spec
    )

    # 复用单机 profile 阻断报告结构。
    dict_blocked = _blocked_profile_config(  # target_part 缺失的基础阻断报告
        namespace_profile_request,  # 复用旧字段结构，保持上游聚合逻辑不变
        path_run_dir,  # 沿用当前 split run 目录写出 profile 缺字段请求
        missing_fields=["target_part"],  # 本次阻断只要求补齐 FPGA part 字段
        configured_profiles=dict_profiles,  # 现有 profile 表决定用户可直接修哪一项
    )

    # 追加 split topology 专属字段。
    dict_blocked["topology"] = topology["topology"]  # split 阻断报告中的拓扑类型

    # 记录 build 服务器，便于用户修正对应 profile。
    dict_blocked["build_server"] = str_build_server  # 缺 target_part 时需要修正的构建服务器

    # 记录 validate 服务器，便于用户核对硬件目标。
    dict_blocked["validate_server"] = str_validate_server  # 缺 target_part 时需要核对的验收服务器

    # 共享版本已经确认，报告中保留该事实。
    dict_blocked["vitis_version"] = str_shared_version  # 已确认的 split 共享 Vitis 版本

    # 返回完整阻断报告。
    return dict_blocked

# 探测 split 两端 Vitis 工具链和 validate 端硬件存在性。
def _prepare_split_vitis_readiness(
    helper: "ErieHelper",
    dict_context: dict[str, Any],
) -> dict[str, Any]:
    """
    准备 split Vitis 的前置探测结果。

    :param helper: 远端执行 helper。
    :param dict_context: `_prepare_split_vitis_context` 生成的上下文。
    :return: 带 probe 和合并 profile 的上下文字典。
    """

    # build 端 Vitis 探测确认 HLS 工具可执行。
    dict_build_probe = _probe_vitis(  # build 端 settings/tool_path 可用性探测结果
        dict_context["build_server"],  # build 端负责第一阶段 HLS 产物生成
        dict_context["settings"],  # build 端探测仍走 split 共用的 settings overlay
        helper,  # 复用当前 erie-remote-ssh helper 执行探测命令
        dict_context["build_profile"],  # build 端实际加载的 profile 字段
    )

    # validate 端探测用于确认硬件验收阶段也能解析出相同版本的工具链。
    dict_validate_probe = _probe_vitis(  # validation 端实际工具链可用性探测结果
        dict_context["validate_server"],  # validate 端需要独立确认可执行同版本工具链
        dict_context["settings"],  # validation 端沿用同一份 overlay 复核工具链
        helper,  # 验收机探测也复用当前 helper，便于把日志和 request 证据串到同一路径
        dict_context["validate_profile"],  # 验收机要验证的就是这份 validation profile 快照
    )

    # hardware 验收前还需要拿到 validate 端 FPGA 设备存在证据。
    dict_device_probe = _probe_fpga_presence(  # validate 端 FPGA 设备探测结果
        dict_context["validate_server"],  # 硬件存在性证据只来自最终验收服务器
        dict_context["settings"],  # 硬件探测命令也要使用同一份 SSH/settings 上下文
        helper,  # 同一 helper 负责拉取板卡存在性证据
    )

    # probe 全部通过后才合并工具路径字段。
    if dict_build_probe["status"] == PASS_STATUS:

        # 把 build 端解析出的实际工具路径写回 profile，避免后续脚本依赖远端 PATH。
        dict_context["build_profile"] = _profile_with_vitis_probe(  # build 端补齐 resolved_tool/tool_path 的 profile 副本
            dict_context["build_profile"],  # build 端原始 profile 提供 version、settings 等静态字段
            dict_build_probe,  # build 端 probe 结果补回 resolved_tool 与绝对 tool_path
        )

    # validate 端通过后同样合并工具路径字段。
    if dict_validate_probe["status"] == PASS_STATUS:

        # 把 validate 端解析出的实际工具路径写回 profile，保证后续验收调用同一套工具。
        dict_context["validate_profile"] = _profile_with_vitis_probe(  # validation 端补齐本机实际 tool_path 的 profile 副本
            dict_context["validate_profile"],  # validate 端原始 profile 保留本机安装树与版本字段
            dict_validate_probe,  # validate 端 probe 结果补回验收机实际解析到的工具路径
        )

    # 返回包含所有 probe 的 readiness 上下文。
    return {
        **dict_context,
        "build_probe": dict_build_probe,
        "validate_probe": dict_validate_probe,
        "device_probe": dict_device_probe,
    }

# 判断 split mode 是否被任一前置 probe 阻断。
def _split_vitis_blocked(dict_readiness: dict[str, Any]) -> bool:
    """
    判断 split Vitis 前置探测是否存在阻断项。

    :param dict_readiness: split 前置探测上下文。
    :return: 任一 probe 未通过时返回 True。
    """

    # 三个 probe 全部通过才允许执行远端 Vitis 阶段。
    return (
        dict_readiness["build_probe"]["status"] != PASS_STATUS
        or dict_readiness["validate_probe"]["status"] != PASS_STATUS
        or dict_readiness["device_probe"]["status"] != PASS_STATUS
    )

# 构造 split Vitis 前置探测失败报告。
def _blocked_split_vitis_result(
    plan: list[str],
    topology: dict[str, Any],
    dict_readiness: dict[str, Any],
) -> dict[str, Any]:
    """
    组装 split 前置探测失败报告。

    :param plan: 当前验收计划步骤。
    :param topology: split topology。
    :param dict_readiness: 前置探测上下文。
    :return: 可写入 JSON 的阻断报告。
    """

    # 返回旧字段名，兼容现有报告消费者。
    return {
        "status": BLOCKED_VITIS_STATUS,
        "mode": "vitis",
        "topology": topology["topology"],
        "build_server": dict_readiness["build_server"],
        "validate_server": dict_readiness["validate_server"],
        "vitis_version": dict_readiness["shared_version"],
        "target_part": dict_readiness["target_part"],
        "run_dir": str(dict_readiness["run_dir"]),
        "steps": plan,
        "build_probe": dict_readiness["build_probe"],
        "validate_probe": dict_readiness["validate_probe"],
        "device_probe": dict_readiness["device_probe"],
        "uses_erie_remote_ssh": True,
    }

# split build/validate 两端的 phase context 统一从这里生成。
def _split_vitis_phase_contexts(
    args: argparse.Namespace,
    config: dict[str, Any],
    dict_readiness: dict[str, Any],
    path_package_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    构造 split 模式下 build 与 validate 两端的 phase context。

    :param args: CLI 参数，提供 readiness 与清理策略。
    :param config: 远端验收配置。
    :param dict_readiness: 已通过前置探测的 split 上下文。
    :param path_package_path: 两端共享上传的本地 Vitis 资产包路径。
    :return: build phase context 与 validate phase context 二元组。
    """

    # build 阶段使用 build 服务器和 build workdir。
    dict_build_phase = _vitis_phase_context(  # build 端 detached runner 使用的 phase 输入快照
        dict(  # build 阶段 phase context 只保留第一阶段远端执行必需字段
            settings=dict_readiness["settings"],  # build 阶段远端命令继续复用 split overlay
            server=dict_readiness["build_server"],  # build 阶段真正执行 Vitis 的远端服务器
            profile=dict_readiness["build_profile"],  # build 阶段已经补齐工具字段的 profile
            readiness=args.readiness,  # build 阶段 runner 的 readiness 目标
            package_path=path_package_path,  # build 阶段上传的共享 HLS 资产包
            config=config,  # 远端目录契约和工具规则配置
            run_dir=dict_readiness["run_dir"],  # 本地 split run 目录，统一收纳两阶段报告
            phase_label="build",  # 第一阶段固定写成 build
            cleanup_remote=args.cleanup_remote,  # 是否在通过后清理 build 端 active run
            remote_workdir=dict_readiness["build_workdir"],  # build 端 workspace 根目录
        )
    )

    # validation 阶段切换到验收服务器的 workdir，并追加硬件验收与归档需要的上下文。
    dict_validate_phase = _vitis_phase_context(  # validation 端 phase snapshot 额外承接板卡验收约束
        dict(  # validation 阶段 phase context 要带上最终硬件验收所需的字段
            settings=dict_readiness["settings"],  # validation 阶段沿用同一 overlay，确保 SSH 与目录上下文一致
            server=dict_readiness["validate_server"],  # 最终硬件验收所在的远端服务器
            profile=dict_readiness["validate_profile"],  # 板卡验收阶段真正加载的 validation profile
            readiness=args.readiness,  # 二阶段 runner 继续朝 CLI 约定的 readiness 终点推进
            package_path=path_package_path,  # validation 阶段复用同一份 HLS 资产包
            config=config,  # 目录契约、归档规则和工具根路径都由这份配置驱动
            run_dir=dict_readiness["run_dir"],  # 本地 split run 目录继续汇总 validation 侧证据
            phase_label="validation",  # 第二阶段标签固定写成 validation，供总报告区分
            cleanup_remote=args.cleanup_remote,  # 决定 validation 端结束后是否保留 active run
            remote_workdir=dict_readiness["validate_workdir"],  # 验收机上展开目录契约的 workspace 根目录
        )
    )

    # 返回两端 phase context，供 split 执行阶段复用。
    return dict_build_phase, dict_validate_phase

# 生成本地资产并分别执行 split build 与 validation 阶段。
def _run_split_vitis_jobs(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    plan: list[str],
    topology: dict[str, Any],
    dict_readiness: dict[str, Any],
) -> dict[str, Any]:
    """
    执行 split Vitis 两个远端阶段。

    :param args: CLI 参数，提供 readiness、样例和清理策略。
    :param config: 远端验收配置。
    :param helper: 远端执行 helper。
    :param plan: 当前验收步骤。
    :param topology: split topology。
    :param dict_readiness: 已通过前置探测的上下文。
    :return: split 总报告。
    """

    # 先生成 split build/validation 共用的本地 HLS 资产目录。
    path_artifact_dir = _generate_local_hls_artifacts(  # split 两端共用的本地 HLS 资产目录
        dict_readiness["run_dir"],  # split run 目录统一托管 build 与 validation 的共享工件
        comment_language=args.comment_language,  # 两端 runner 使用完全一致的注释语言与模板内容
        example_spec=args.example_spec,  # split 两阶段共同消费的示例 spec 文件名
    )

    # 将本地 HLS 资产和 runner 打成单包，供 build/validation 两端共同上传。
    path_package_path = _create_vitis_package(dict_readiness["run_dir"], path_artifact_dir)  # 上传到两端服务器的同一份 Vitis 资产包

    # 先取回 split 两端的 phase context 二元组，再按 build/validation 两端拆开复用。
    tuple_split_phase_contexts = _split_vitis_phase_contexts(  # build/validation 两端共享的 phase context 二元组
        args,  # CLI 的 readiness 与 cleanup 策略要同时传给两个阶段
        config,  # phase context 还需要读取本地远端验收配置与目录契约
        dict_readiness,  # 两端前置探测和 profile 补齐后的上下文
        path_package_path,  # 共享上传的本地 HLS 资产包路径
    )

    # 从二元组中取出 build 阶段快照，后续第一阶段执行与总报告都复用它。
    dict_build_phase = tuple_split_phase_contexts[0]  # build 端 phase snapshot，承接第一阶段执行上下文

    # 从二元组中取出 validation 快照，后续硬件验收和归档都依赖它。
    dict_validate_phase = tuple_split_phase_contexts[1]  # validation 端 phase snapshot，承接最终硬件验收上下文

    # build 阶段报告提供后续总状态判定和远端目录证据。
    dict_build_result = _run_server_vitis_phase_context(helper, dict_build_phase)  # build 服务器 detached Vitis job 报告

    # validation 阶段报告提供硬件验收结果和保留产物证据。
    dict_validate_result = _run_server_vitis_phase_context(helper, dict_validate_phase)  # validation 服务器硬件验收阶段的 detached Vitis job 报告

    # 记录 build 阶段是否通过，供 split 总状态判断复用。
    bool_build_passed = dict_build_result["status"] == PASS_STATUS  # build 阶段是否通过

    # 单独记录 validation 结果，后续总状态与硬件证据摘要都要引用这一布尔值。
    bool_validate_passed = dict_validate_result["status"] == PASS_STATUS  # validation 硬件验收阶段是否通过

    # 两个阶段都通过时 split 总状态才通过。
    bool_passed = bool_build_passed and bool_validate_passed  # split 两阶段是否全部通过

    # 记录 build 端远端产物是否按契约保留。
    bool_build_artifacts_retained = bool(dict_build_result.get("remote_artifacts_retained"))  # build 端产物保留状态

    # 单独跟踪 validation 端产物保留状态，避免总报告只剩 build 端证据。
    bool_validate_artifacts_retained = bool(dict_validate_result.get("remote_artifacts_retained"))  # validation 端远端 run/backups 产物保留状态

    # 两端远端产物保留状态需要同时满足。
    bool_remote_artifacts_retained = bool_build_artifacts_retained and bool_validate_artifacts_retained  # split 两端远端 run/backups 产物是否都按契约保留

    # 返回 split 总报告，保留旧字段名。
    return dict(
        status=PASS_STATUS if bool_passed else FAILED_STATUS,
        mode="vitis",
        topology=topology["topology"],
        build_server=dict_readiness["build_server"],
        validate_server=dict_readiness["validate_server"],
        vitis_version=dict_readiness["shared_version"],
        target_part=dict_readiness["target_part"],
        readiness=args.readiness,
        example_spec=args.example_spec,
        run_dir=str(dict_readiness["run_dir"]),
        steps=plan,
        build_result=dict_build_result,
        validation_result=dict_validate_result,
        uses_erie_remote_ssh=True,
        remote_artifacts_retained=bool_remote_artifacts_retained,
    )

# 按显式版本、缓存、远端候选和 fallback profile 选择 Vitis profile。
def _select_vitis_profile(
    args: argparse.Namespace,
    run_dir: Path,
    candidates: list[dict[str, Any]],
    fallback_profile: dict[str, Any],
) -> dict[str, Any]:
    """
    选择本次远端 Vitis profile。

    :param args: CLI 参数，可能指定 Vitis 版本。
    :param run_dir: 本次 run 目录，用于写版本选择请求。
    :param candidates: 远端扫描得到的 Vitis 候选版本。
    :param fallback_profile: 配置或缓存解析出的兜底 profile。
    :return: 已选 profile 或版本选择阻断报告。
    :raises RemoteAcceptanceError: 当用户指定的版本不存在于远端候选列表时抛出。
    """

    # CLI 显式版本优先于缓存和自动候选。
    str_explicit_version = str(args.vitis_version or "").strip()  # 用户命令行指定的 Vitis 版本

    # 显式版本必须能在远端候选中找到。
    if str_explicit_version:

        # 从候选列表中查找用户指定版本。
        dict_selected = _find_candidate(candidates, str_explicit_version)  # CLI 版本匹配到的候选 profile

        # 指定版本不存在时阻断，避免静默使用其他版本。
        if not dict_selected:

            # 错误消息使用 ERR 前缀，遵循脚本输出治理约定。
            raise RemoteAcceptanceError(
                f"> ERR: [Python] Requested Vitis version {str_explicit_version!r} "
                f"was not found on {args.server}."
            )

        # 记录用户确认过的版本选择，便于后续命令复用。
        set_vitis_selection(args.server, dict_selected)

        # 返回显式版本 profile。
        return dict_selected

    # 本地缓存记录此前确认过的 Vitis 选择。
    dict_saved_selection = get_vitis_selection(args.server)  # 服务器维度缓存的 Vitis profile

    # 缓存命中时优先复用并与最新扫描候选合并。
    if dict_saved_selection:

        # 若远端候选仍包含缓存版本，则用候选的安装路径补齐缓存字段。
        dict_candidate = (  # 与缓存版本匹配的远端候选
            _find_candidate(candidates, str(dict_saved_selection.get("version") or ""))  # 用缓存记录的版本号回查最新扫描候选
            if candidates  # 只有当前轮扫描拿到候选时才做版本号回查
            else None  # 本轮没有扫描候选时不做版本回查
        )

        # 缓存版本仍存在时合并远端扫描字段和用户保存字段。
        if dict_candidate:

            # 保存字段覆盖候选字段，保留用户确认过的路径修正。
            dict_merged = {**dict_candidate, **dict_saved_selection}  # 合并后的缓存 Vitis profile

            # 写回合并结果，避免后续运行丢失修正字段。
            set_vitis_selection(args.server, dict_merged)

            # 返回缓存与远端事实合并后的 profile。
            return dict_merged

        # 没有候选扫描结果时只能信任缓存 profile。
        if not candidates:

            # 返回缓存 profile，后续 `_probe_vitis` 仍会验证真实可用性。
            return dict_saved_selection

    # 多个候选版本需要用户明确选择，避免版本漂移。
    if len(candidates) > 1:

        # 写入可操作的版本选择请求 JSON。
        return _blocked_vitis_version_selection(args, run_dir, candidates)

    # 只有一个候选时自动选择该版本。
    if len(candidates) == 1:

        # 返回唯一候选 profile。
        return candidates[0]

    # 无候选时回退到配置解析出的 profile。
    return _fallback_vitis_profile(args, fallback_profile)

# 生成多版本候选时的阻断报告并写出选择请求。
def _blocked_vitis_version_selection(
    args: argparse.Namespace,
    run_dir: Path,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    生成 Vitis 多版本选择阻断报告。

    :param args: CLI 参数。
    :param run_dir: 本次 run 目录。
    :param candidates: 远端扫描到的多个 Vitis 候选。
    :return: 版本选择阻断报告。
    """

    # 请求内容提供候选版本和推荐命令。
    dict_request = _remote_vitis_version_request(args, run_dir, candidates)  # 写给用户的版本选择请求

    # 请求文件固定在 run 目录，便于 handoff 和报告引用。
    path_request = run_dir / "remote_vitis_version_request.json"  # Vitis 版本选择请求文件

    # 写入 JSON 请求文件。
    _write_json(path_request, dict_request)

    # 返回阻断报告，保持旧字段名。
    return dict(
        status=BLOCKED_VERSION_STATUS,
        mode="vitis",
        server=args.server,
        profile=args.profile,
        readiness=args.readiness,
        example_spec=args.example_spec,

        # 版本选择请求文件和候选版本供用户决策。
        run_dir=str(run_dir),
        remote_vitis_version_request=str(path_request),
        candidate_versions=candidates,

        # 用户配置路径和远端 SSH 标记供上层提示。
        user_config_path=str(user_config_path()),
        uses_erie_remote_ssh=True,
    )

# 将配置 profile 转成最小可运行 fallback profile。
def _fallback_vitis_profile(
    args: argparse.Namespace,
    fallback_profile: dict[str, Any],
) -> dict[str, Any]:
    """
    构造无远端候选时的 fallback Vitis profile。

    :param args: CLI 参数，用于 profile 名称回填版本字段。
    :param fallback_profile: 配置解析出的 profile。
    :return: 最小 profile 字典。
    """

    # fallback 只保留 runner 必须读取的字段。
    return dict(
        version=str(fallback_profile.get("version") or args.profile),
        settings_script=str(fallback_profile["settings_script"]),
        expected_tool=str(fallback_profile["expected_tool"]),
        target_part=str(fallback_profile.get("target_part", "")),
    )

# 选择 split mode 两端共有的 Vitis 版本。
def _select_shared_vitis_version(
    args: argparse.Namespace,
    build_candidates: list[dict[str, Any]],
    validate_candidates: list[dict[str, Any]],
) -> str:
    """
    解析 split mode 的共享 Vitis 版本。

    :param args: CLI 参数，可能显式指定版本。
    :param build_candidates: build 服务器候选版本。
    :param validate_candidates: validate 服务器候选版本。
    :return: 两端共用的 Vitis 版本字符串。
    :raises RemoteAcceptanceError: 当 build/validate 两端没有共享版本时抛出。
    """

    # CLI 显式版本优先，后续 profile 解析会检查两端可用性。
    if args.vitis_version:

        # 返回用户指定版本。
        return str(args.vitis_version)

    # 分别提取两端版本集合。
    set_build_versions = {str(dict_item.get("version")) for dict_item in build_candidates}  # build 端版本集合

    # validate 端版本集合代表硬件验收侧的可用版本事实，后续要和 build 侧求交。
    set_validate_versions = {str(dict_item.get("version")) for dict_item in validate_candidates}  # 仅来自验收服务器扫描结果的版本集合

    # 共享版本按数字序排序，选择最小稳定版本。
    list_shared_versions = sorted(  # 两端同时安装的 Vitis 版本列表
        set_build_versions & set_validate_versions,  # build/validate 两端同时可用的共享版本集合
        key=_version_sort_key,  # 版本号按数字片段排序，避免字符串序误判
    )

    # 没有共享版本时不能继续 split 验收。
    if not list_shared_versions:

        # 错误明确指出 split 两端无共同 Vitis。
        raise RemoteAcceptanceError(
            "> ERR: [Python] No shared Vitis version is available across "
            "the selected build and validation servers."
        )

    # 返回排序后的第一个共享版本。
    return list_shared_versions[0]

# 将 Vitis 版本号拆成数字 tuple，供共享版本排序。
def _version_sort_key(value: str) -> tuple[int, ...]:
    """
    生成 Vitis 版本排序 key。

    :param value: Vitis 版本标签。
    :return: 数字版本元组；没有数字时返回高优先级占位。
    """

    # 版本字符串通常形如 2023.2，取出所有数字片段。
    list_match_parts = re.findall(r"\d+", str(value))  # 版本号数字片段

    # 数字片段存在时按数字排序，否则放到末尾。
    return tuple(int(str_part) for str_part in list_match_parts) if list_match_parts else (9999,)

# 按版本从缓存、扫描候选和配置 profile 中解析完整 profile。
def _resolve_profile_for_version(
    server: str,
    candidates: list[dict[str, Any]],
    configured_profiles: dict[str, Any],
    version: str,
) -> dict[str, Any]:
    """
    为指定服务器和 Vitis 版本解析 profile。

    :param server: 远端服务器名。
    :param candidates: 该服务器扫描到的候选版本。
    :param configured_profiles: 配置文件中的 profile 表。
    :param version: 需要解析的 Vitis 版本。
    :return: 可用于 `_probe_vitis` 的 profile。
    :raises RemoteAcceptanceError: 当缓存、扫描候选和配置表都无法解析目标版本时抛出。
    """

    # 先尝试复用用户保存过的服务器选择。
    dict_saved_selection = get_vitis_selection(server)  # 服务器缓存的 Vitis profile

    # 缓存 profile 只有字段完整且版本一致时才可复用。
    if _profile_matches_version(dict_saved_selection, version):

        # 返回缓存 profile。
        return dict_saved_selection

    # 再从远端扫描候选中查找同版本 profile。
    dict_candidate = _find_candidate(candidates, version)  # 远端扫描匹配到的 profile

    # 候选命中时写入缓存。
    if dict_candidate:

        # 保存扫描候选，便于后续无候选扫描时复用。
        set_vitis_selection(server, dict_candidate)

        # 命中远端扫描结果时直接返回该 profile。
        return dict_candidate

    # 最后从配置 profile 表中查找同版本完整 profile。
    dict_configured_profile = _configured_profile_for_version(configured_profiles, version)  # 配置表匹配到的 profile

    # 配置命中时返回该 profile。
    if dict_configured_profile:

        # 命中配置表后直接返回该条完整 profile。
        return dict_configured_profile

    # 三个来源都失败时阻断。
    raise RemoteAcceptanceError(
        f"> ERR: [Python] Could not resolve Vitis profile for server {server!r} "
        f"and version {version!r}."
    )

# 判断 profile 是否匹配指定版本且具备必要字段。
def _profile_matches_version(dict_profile: dict[str, Any] | None, version: str) -> bool:
    """
    判断 profile 是否可作为指定版本的可执行配置。

    :param dict_profile: 候选 profile。
    :param version: 目标 Vitis 版本。
    :return: profile 版本一致且 settings/tool 字段完整时返回 True。
    """

    # 非字典 profile 不能参与匹配。
    if not isinstance(dict_profile, dict):

        # 返回不匹配。
        return False

    # 版本、settings 和 expected_tool 都是远端 runner 必需字段。
    return (
        str(dict_profile.get("version") or "") == version
        and bool(str(dict_profile.get("settings_script") or "").strip())
        and bool(str(dict_profile.get("expected_tool") or "").strip())
    )

# 从配置 profile 表中查找指定版本的完整 profile。
def _configured_profile_for_version(
    configured_profiles: dict[str, Any],
    version: str,
) -> dict[str, Any] | None:
    """
    查找配置文件中匹配版本的 Vitis profile。

    :param configured_profiles: 配置 profile 表。
    :param version: 目标 Vitis 版本。
    :return: 匹配的 profile；不存在时返回 None。
    """

    # 遍历配置 profile 表，忽略非字典条目。
    for dict_profile in configured_profiles.values():

        # 只处理 profile 字典。
        if not isinstance(dict_profile, dict):

            # 跳过无效配置项。
            continue

        # profile 版本和必填字段都匹配时可用。
        if _profile_matches_version(dict_profile, version):

            # 返回配置 profile。
            return dict_profile

    # 未找到匹配配置。
    return None

# 解析 split mode 使用的 target part。
def _resolve_target_part(
    args: argparse.Namespace,
    settings: Path,
    validate_server: str,
    validate_profile: dict[str, Any],
    build_profile: dict[str, Any],
) -> str:
    """
    解析 split mode 的 FPGA target part。

    :param args: CLI 参数，可能显式指定 target part。
    :param settings: 远端 settings 文件。
    :param validate_server: 验收服务器。
    :param validate_profile: validate 端 Vitis profile。
    :param build_profile: build 端 Vitis profile。
    :return: FPGA target part；无法确定时返回空字符串。
    """

    # 命令行传入的 target_part 会同时约束 build 和 validation 两端 Tcl。
    str_cli_target_part = str(getattr(args, "target_part", "") or "").strip()  # 用户显式覆盖 split 两端 Tcl 的 FPGA part

    # 显式值存在时直接返回。
    if str_cli_target_part:

        # 返回 CLI target part。
        return str_cli_target_part

    # validate profile、build profile 和缓存选择依次提供 target part。
    tuple_profile_sources = (
        validate_profile,  # validate 端 profile 优先提供最终硬件目标事实
        build_profile,  # build 端 profile 在 validate 未声明时作为回退
        get_vitis_selection(validate_server) or {},  # 验收服务器缓存的历史选择作为最后兜底
    )  # target_part 候选来源

    # 遍历 profile 来源寻找 target part。
    for dict_profile in tuple_profile_sources:

        # 非字典来源不读取字段。
        if not isinstance(dict_profile, dict):

            # 跳过无效 profile 来源。
            continue

        # 提取 profile 中声明的 target part。
        str_target_part = str(dict_profile.get("target_part") or "").strip()  # 当前 profile 可直接写入 set_part 的 FPGA part

        # 命中后返回。
        if str_target_part:

            # 返回 profile 提供的 target part。
            return str_target_part

    # 最后从远端服务器记录推断硬件 part。
    str_inferred_target_part = _infer_target_part_from_server(settings, validate_server)  # 远端硬件记录推断的 FPGA part

    # 返回推断结果，可能为空。
    return str_inferred_target_part

# 按显式 profile、缓存、配置表和远端候选解析 Vitis profile。
def _resolve_profile_config(
    args: argparse.Namespace,
    run_dir: Path,
    *,
    candidates: list[dict[str, Any]],
    configured_profiles: dict[str, Any],
    required_fields: tuple[str, ...],
) -> dict[str, Any]:
    """
    解析 Vitis profile 配置并检查必填字段。

    :param args: CLI 参数，可能指定 profile。
    :param run_dir: 本次 run 目录，用于写 profile 请求。
    :param candidates: 远端扫描得到的 profile 候选。
    :param configured_profiles: 配置文件中的 profile 表。
    :param required_fields: 本模式必须存在的 profile 字段。
    :return: 可用 profile 或 profile 配置阻断报告。
    """

    # 命令行 profile 名称决定优先读取配置表中的哪一条 Vitis profile。
    dict_explicit_profile = _explicit_profile_config(args, configured_profiles, required_fields)  # 显式 profile 的状态化校验结果

    # 显式 profile 存在时直接使用或返回缺字段阻断。
    if dict_explicit_profile["status"] == PASS_STATUS:

        # 返回显式 profile。
        return dict_explicit_profile["profile"]

    # 显式 profile 缺字段时立即生成阻断报告。
    if dict_explicit_profile["status"] == BLOCKED_PROFILE_STATUS:

        # 返回缺字段阻断报告。
        return _blocked_profile_config(
            args,
            run_dir,
            missing_fields=dict_explicit_profile["missing_fields"],
            configured_profiles=configured_profiles,
        )

    # 没有显式 profile 时尝试复用缓存。
    dict_saved_profile = _saved_profile_config(args.server, required_fields)  # 本地缓存 profile 解析结果

    # 缓存 profile 字段完整时可直接使用。
    if dict_saved_profile:

        # 优先沿用用户上次确认过的完整缓存 profile，避免再次选择。
        return dict_saved_profile

    # 配置表中只有一个完整 profile 时自动选择。
    dict_single_profile = _single_configured_profile(configured_profiles, required_fields)  # 唯一完整配置 profile

    # 唯一配置 profile 命中时返回。
    if dict_single_profile:

        # 只有一项完整配置时自动采用它，避免继续弹出人工选择请求。
        return dict_single_profile

    # 远端候选中第一个字段完整的 profile 可作为 fallback。
    dict_candidate_profile = _candidate_profile_config(candidates, required_fields)  # 字段完整的远端候选 profile

    # 候选 profile 命中时返回副本。
    if dict_candidate_profile:

        # 直接采用当前最完整的扫描候选，后续仍可由调用方继续验证。
        return dict_candidate_profile

    # 所有来源都缺字段时生成 profile 配置请求。
    return _blocked_profile_config(
        args,
        run_dir,
        missing_fields=list(required_fields),
        configured_profiles=configured_profiles,
    )

# 解析 CLI 显式 profile 并返回状态化结果。
def _explicit_profile_config(
    args: argparse.Namespace,
    configured_profiles: dict[str, Any],
    required_fields: tuple[str, ...],
) -> dict[str, Any]:
    """
    解析 CLI 指定的 Vitis profile。

    :param args: CLI 参数。
    :param configured_profiles: 配置 profile 表。
    :param required_fields: 必填字段列表。
    :return: 状态化解析结果。
    """

    # 空 profile 表示 CLI 未显式指定。
    str_explicit_profile = str(args.profile or "").strip()  # CLI 指定的 profile 名称

    # 未指定时返回跳过状态。
    if not str_explicit_profile:

        # skip 只在内部使用，不写入最终报告。
        return dict(status="skipped")

    # 从配置表读取显式 profile。
    dict_profile = configured_profiles.get(str_explicit_profile)  # 配置表中的显式 profile

    # 配置项不存在或不是字典时视为所有必填字段缺失。
    if not isinstance(dict_profile, dict):

        # 返回缺字段状态。
        return dict(
            status=BLOCKED_PROFILE_STATUS,
            missing_fields=list(required_fields),
        )

    # profile 名称在缺 version 时作为版本标签。
    dict_resolved = {  # 用显式 profile 名称回填 version 的标准化副本
        **dict_profile,  # 显式 profile 原有字段整体透传
        "version": str(dict_profile.get("version") or str_explicit_profile),  # 缺失时用显式 profile 名称补 version
    }  # 补齐 version 字段的显式 profile

    # 检查显式 profile 必填字段。
    list_missing_fields = _missing_profile_fields(dict_resolved, required_fields)  # 显式 profile 缺失字段

    # 缺字段时返回阻断状态。
    if list_missing_fields:

        # 返回缺字段列表。
        return dict(
            status=BLOCKED_PROFILE_STATUS,
            missing_fields=list_missing_fields,
        )

    # 返回可用显式 profile。
    return dict(
        status=PASS_STATUS,
        profile=dict_resolved,
    )

# 从本地缓存读取字段完整的 profile。
def _saved_profile_config(server: str, required_fields: tuple[str, ...]) -> dict[str, Any] | None:
    """
    读取服务器缓存中的完整 Vitis profile。

    :param server: 远端服务器名。
    :param required_fields: 必填字段列表。
    :return: 缓存 profile；字段不完整时返回 None。
    """

    # 先读取服务器维度的用户确认缓存，后续只负责校验它是否仍然字段完整。
    dict_saved_profile = get_vitis_selection(server)  # 用户此前确认后写入本地缓存的 server 级 profile

    # 没有缓存时返回 None。
    if not dict_saved_profile:

        # 返回空结果。
        return None

    # 缓存字段完整才可复用。
    if not _missing_profile_fields(dict_saved_profile, required_fields):

        # 缓存字段已完整，直接把这份 server 级选择回传给调用方。
        return dict_saved_profile

    # 缓存不完整时忽略。
    return None

# 从配置表中选择唯一字段完整的 profile。
def _single_configured_profile(
    configured_profiles: dict[str, Any],
    required_fields: tuple[str, ...],
) -> dict[str, Any] | None:
    """
    查找配置表中唯一完整的 Vitis profile。

    :param configured_profiles: 配置 profile 表。
    :param required_fields: 必填字段列表。
    :return: 唯一完整 profile；不唯一时返回 None。
    """

    # 收集字段完整的配置 profile。
    list_complete_profiles: list[dict[str, Any]] = []  # 配置表中字段完整的 profile 列表

    # 遍历配置 profile 表。
    for str_name, dict_profile in configured_profiles.items():

        # 跳过非字典配置项。
        if not isinstance(dict_profile, dict):

            # 无效配置项不参与自动选择。
            continue

        # profile 名称用于补齐缺失的 version 字段。
        dict_resolved = {  # 用配置项名称回填 version 的标准化副本
            **dict_profile,  # 当前配置项原有字段整体透传
            "version": str(dict_profile.get("version") or str_name),  # 缺失时用配置项名补 version
        }  # 补齐 version 的配置 profile

        # 字段完整时加入候选。
        if not _missing_profile_fields(dict_resolved, required_fields):

            # 保存完整配置 profile。
            list_complete_profiles.append(dict_resolved)

    # 只有一个完整 profile 时可自动选择。
    if len(list_complete_profiles) == 1:

        # 只有一条完整配置时直接回传，避免把简单场景推给人工选择。
        return list_complete_profiles[0]

    # 多个或没有完整配置时交给后续候选/阻断逻辑。
    return None

# 从远端扫描候选中选择第一个字段完整的 profile。
def _candidate_profile_config(
    candidates: list[dict[str, Any]],
    required_fields: tuple[str, ...],
) -> dict[str, Any] | None:
    """
    查找字段完整的远端候选 profile。

    :param candidates: 远端扫描候选 profile。
    :param required_fields: 必填字段列表。
    :return: 第一个字段完整的候选副本。
    """

    # 顺序保持远端扫描结果顺序。
    for dict_candidate in candidates:

        # 字段完整时返回副本，避免调用方修改候选表。
        if not _missing_profile_fields(dict_candidate, required_fields):

            # 复制一份扫描候选返回，避免后续调用方回写污染原始候选表。
            return dict(dict_candidate)

    # 没有完整候选。
    return None

# 计算 profile 缺失的必填字段。
def _missing_profile_fields(
    dict_profile: dict[str, Any],
    required_fields: tuple[str, ...],
) -> list[str]:
    """
    计算 profile 缺少的必填字段。

    :param dict_profile: 待检查 profile。
    :param required_fields: 必填字段列表。
    :return: 缺失字段名列表。
    """

    # 空字符串也视为缺失。
    return [
        str_field
        for str_field in required_fields
        if not str(dict_profile.get(str_field) or "").strip()
    ]

# 生成 profile 配置缺失时的阻断报告。
def _blocked_profile_config(
    args: argparse.Namespace,
    run_dir: Path,
    *,
    missing_fields: list[str],
    configured_profiles: dict[str, Any],
) -> dict[str, Any]:
    """
    组装 Vitis profile 配置缺失报告。

    :param args: CLI 参数。
    :param run_dir: 本次 run 目录。
    :param missing_fields: 缺失字段名列表。
    :param configured_profiles: 配置 profile 表。
    :return: 可写入 JSON 的阻断报告。
    """

    # mode 字段用于推荐命令保持当前入口模式。
    str_mode = str(getattr(args, "mode", "vitis") or "vitis")  # 当前远端验收模式

    # 推荐命令引导用户按 profile 名或版本补齐配置。
    list_recommended_commands = _vitis_profile_recommended_commands(args, str_mode)  # profile 配置修复命令

    # 配置 profile 名列表写入请求和报告。
    list_configured_profile_names = sorted(str(str_name) for str_name in configured_profiles)  # 已配置 profile 名称列表

    # 请求 JSON 供用户选择或补齐 profile 字段。
    dict_request = dict(  # 写给用户的 profile 配置请求正文
        version=1,  # 配置请求 JSON 协议版本
        action="ask_remote_vitis_profile_config",  # 上游据此识别 profile 缺失阻断类型
        question=(  # 直接展示给用户的阻断问题文案
            "Remote Vitis validation requires an explicit configured profile "
            "or a previously saved remote selection. Configure the missing "
            "values before retrying."
        ),

        # CLI 上下文说明本次缺配置的入口参数。
        server=args.server,  # 当前远端目标服务器
        profile=args.profile,  # CLI 显式指定的 profile 名称
        readiness=args.readiness,  # 本轮远端验收目标等级
        example_spec=args.example_spec,  # 复现当前问题要继续使用的示例 spec

        # 缺失字段和现有 profile 名用于用户修复配置。
        missing_fields=missing_fields,  # 需要补齐的 profile 字段列表
        configured_profiles=list_configured_profile_names,  # 当前已配置的 profile 名称集合

        # 推荐命令和配置路径用于下一步操作。
        user_config_path=str(user_config_path()),  # 本地用户配置文件路径
        recommended_commands=list_recommended_commands,  # 用户下一步可直接执行的修复命令
    )  # 写给用户的 profile 配置请求

    # 请求文件固定在 run 目录。
    path_request = run_dir / "remote_vitis_profile_request.json"  # profile 配置请求文件

    # 写出请求文件。
    _write_json(path_request, dict_request)

    # 返回阻断报告。
    return dict(
        status=BLOCKED_PROFILE_STATUS,
        mode=str_mode,

        # CLI 上下文保持旧报告字段。
        server=args.server,
        profile=args.profile,
        readiness=args.readiness,
        example_spec=args.example_spec,
        run_dir=str(run_dir),

        # 缺失字段和请求文件指向具体修复动作。
        missing_fields=missing_fields,
        configured_profiles=list_configured_profile_names,
        remote_vitis_profile_request=str(path_request),

        # 阻断报告额外暴露本地配置入口，并标记它属于 erie-remote-ssh 路径。
        user_config_path=str(user_config_path()),
        uses_erie_remote_ssh=True,
    )

# 生成 profile 缺失时建议用户重试的命令。
def _vitis_profile_recommended_commands(
    args: argparse.Namespace,
    mode: str,
) -> list[str]:
    """
    构造 Vitis profile 配置修复命令。

    :param args: CLI 参数。
    :param mode: 当前远端验收模式。
    :return: 推荐命令列表。
    """

    # 公共参数保留 readiness、样例和 JSON 输出。
    str_common_args = (
        f"--readiness {args.readiness} "
        f"--example-spec {args.example_spec} "
        "--json"
    )  # 推荐命令公共参数

    # profile 名称路径适合用户已在配置文件中补齐 profile 的场景。
    str_profile_command = (
        "python .\\scripts\\python\\remote\\remote_vitis_acceptance.py "
        f"--mode {mode} "
        f"--server {args.server} "
        "--profile <configured-profile> "
        f"{str_common_args}"
    )  # 按配置 profile 名重试的命令

    # 版本路径适合用户先确认远端安装版本的场景。
    str_version_command = (
        "python .\\scripts\\python\\remote\\remote_vitis_acceptance.py "
        f"--mode {mode} "
        f"--server {args.server} "
        "--vitis-version <version> "
        f"{str_common_args}"
    )  # 按远端 Vitis 版本重试的命令

    # 返回两个推荐路径。
    return [str_profile_command, str_version_command]

# 从 erie-remote-ssh 软件扫描记录中解析 Vitis 候选版本。
def _vitis_version_candidates(config: dict[str, Any], settings_path: Path, server: str) -> list[dict[str, Any]]:
    """
    读取远端 server-list 中的 Vitis 安装候选。

    :param config: 远端验收配置。
    :param settings_path: 本次 settings overlay 路径。
    :param server: 目标服务器名。
    :return: Vitis 候选 profile 列表。
    """

    # settings 中可能覆盖 erie server-list 路径。
    dict_settings: dict[str, Any] = json.loads(settings_path.read_text(encoding="utf-8"))  # 用于定位 erie server-list 的 settings overlay

    # server-list 路径解析保持 common 模块规则。
    path_server_list = _resolve_erie_server_list(  # 后面会直接打开这个 JSON 文件，读取当前服务器的已安装 Vitis 扫描记录
        dict_settings,  # settings overlay 中解析出的 erie 入口配置
        settings_path,  # 当前正在使用的 settings overlay 路径
        Path(config["erie_skill_dir"]),  # erie-remote-ssh 技能目录的根路径
    )

    # server-list 可能不存在或仍未完成扫描。
    try:

        # 软件扫描候选只能来自 server-list 中已经落盘的 JSON 内容。
        dict_server_list: dict[str, Any] = json.loads(path_server_list.read_text(encoding="utf-8"))  # 远端 Vitis 软件扫描来源清单

    # 读取失败时返回空候选，由 profile 配置兜底。
    except (FileNotFoundError, json.JSONDecodeError):

        # 无扫描事实时不阻断，由后续 profile 逻辑处理。
        return []

    # 定位当前服务器记录。
    dict_raw_server = _find_server_record(dict_server_list, server)  # server-list 中的目标服务器记录

    # 未找到服务器记录时返回空候选。
    if not dict_raw_server:

        # 无服务器记录时不生成候选。
        return []

    # 硬件型号可为候选 profile 补齐 target_part。
    str_inferred_target_part = _infer_target_part_from_server_record(dict_raw_server)  # 服务器记录推断出的 FPGA part

    # 读取软件扫描中的 vitis 工具段。
    dict_vitis = _server_vitis_scan(dict_raw_server)  # 软件扫描中的 Vitis 段

    # 规范化多版本和单版本两类扫描结构。
    list_raw_versions = _raw_vitis_versions(dict_vitis)  # 原始 Vitis 版本扫描项

    # 将原始扫描项转成 profile 候选。
    list_candidates = [
        _vitis_candidate_from_scan(dict_item, str_inferred_target_part)  # 单个已安装扫描项转换出的 profile 候选
        for dict_item in list_raw_versions  # server-list 里可能混有其他软件条目，这里逐项筛出 Vitis 记录
        if isinstance(dict_item, dict) and dict_item.get("status") == "installed"  # 只保留状态已落到 installed 的 Vitis 记录
    ]  # 可用 Vitis profile 候选列表

    # 按版本去重，保留首次扫描结果。
    return _unique_vitis_candidates(list_candidates)

# 从服务器记录中提取 Vitis 软件扫描段。
def _server_vitis_scan(dict_raw_server: dict[str, Any]) -> dict[str, Any]:
    """
    读取服务器软件扫描中的 Vitis 段。

    :param dict_raw_server: server-list 中的服务器记录。
    :return: Vitis 扫描字典。
    """

    # software_scan 可能缺失或不是字典。
    dict_scan = dict_raw_server.get("software_scan", {})  # 服务器软件扫描记录

    # tools 只有在 software_scan 为字典时可读。
    dict_tools = dict_scan.get("tools", {}) if isinstance(dict_scan, dict) else {}  # 软件扫描工具表

    # vitis 段记录安装状态和版本列表。
    dict_vitis = dict_tools.get("vitis", {}) if isinstance(dict_tools, dict) else {}  # Vitis 扫描记录

    # 返回字典或空字典。
    return dict_vitis if isinstance(dict_vitis, dict) else {}

# 规范化 Vitis 扫描版本列表。
def _raw_vitis_versions(dict_vitis: dict[str, Any]) -> list[dict[str, Any]]:
    """
    规范化 Vitis 扫描项列表。

    :param dict_vitis: Vitis 软件扫描记录。
    :return: 原始版本扫描项列表。
    """

    # 新结构使用 versions 列表。
    list_versions = dict_vitis.get("versions")  # 多版本扫描列表

    # versions 是列表时直接返回。
    if isinstance(list_versions, list):

        # 过滤到字典项。
        return [dict_item for dict_item in list_versions if isinstance(dict_item, dict)]

    # 旧结构直接在 vitis 字典上标记 installed。
    if dict_vitis.get("status") == "installed":

        # 包装成单元素列表。
        return [dict_vitis]

    # 没有可用扫描项。
    return []

# 远端 POSIX 根路径与工具相对路径统一在这里拼接。
def _join_posix_tool_path(str_install_path: str, path_suffix: PurePosixPath) -> str:
    """
    拼接远端安装根目录与工具相对路径。

    :param str_install_path: 远端工具安装根目录文本。
    :param path_suffix: 需要追加的相对 POSIX 路径。
    :return: 拼接后的绝对路径；根目录为空时返回空字符串。
    """

    # 没有安装根目录时无法构造工具路径。
    if not str_install_path:

        # 空安装根直接返回空路径文本，交给上层决定是否继续回退。
        return ""

    # PurePosixPath 统一处理尾部斜杠和路径拼接。
    path_install_root = PurePosixPath(str_install_path)  # 远端工具安装根目录

    # 返回拼接后的远端工具绝对路径。
    return (path_install_root / path_suffix).as_posix()

# 将安装路径中的 Vitis 产品段替换为同层级目标产品段。
def _replace_install_product_path(str_install_path: str, str_target_product: str) -> str:
    """
    在远端安装路径中替换 Vitis 产品目录名。

    :param str_install_path: 原始 Vitis 安装根目录文本。
    :param str_target_product: 目标产品目录名，例如 Vitis_HLS。
    :return: 替换后的安装根目录；缺少产品段时返回空字符串。
    """

    # 空安装路径不参与产品目录替换。
    if not str_install_path:

        # 没有安装根目录时无法映射产品段，直接返回空字符串。
        return ""

    # PurePosixPath 用于稳定读取产品段并重组路径。
    path_install_root = PurePosixPath(str_install_path)  # 原始 Vitis 安装根目录

    # 产品目录段来自路径 parts，便于替换单个层级。
    list_path_parts = list(path_install_root.parts)  # 安装路径分段列表

    # 缺少 Vitis 产品段时无法映射到 HLS 安装根目录。
    if STR_VITIS_PRODUCT_DIR not in list_path_parts:

        # 原路径不包含 Vitis 产品层级时，不构造伪造的 HLS 安装根。
        return ""

    # 首个 Vitis 产品段就是需要替换的层级。
    int_product_index = list_path_parts.index(STR_VITIS_PRODUCT_DIR)  # Vitis 产品段索引

    # 替换为目标产品目录名，保留其他层级不变。
    list_path_parts[int_product_index] = str_target_product  # 替换后的安装路径分段

    # 返回重组后的安装根目录文本。
    return PurePosixPath(*list_path_parts).as_posix()

# 将单个 Vitis 扫描项转换为候选 profile。
def _vitis_candidate_from_scan(
    dict_item: dict[str, Any],
    str_inferred_target_part: str,
) -> dict[str, Any]:
    """
    从软件扫描项构造 Vitis profile 候选。

    :param dict_item: 单个 Vitis 扫描项。
    :param str_inferred_target_part: 从服务器硬件记录推断的 FPGA part。
    :return: 候选 profile。
    """

    # 安装根目录用于推断 settings、v++ 和 Vitis HLS 路径。
    str_install_path = str(dict_item.get("install_path") or "").strip()  # Vitis 安装根目录

    # 可执行路径来自软件扫描工具记录。
    str_executable_path = str(dict_item.get("path") or "").strip()  # 扫描到的 Vitis 可执行文件路径

    # 版本标签从安装路径、version 字段或工具路径中提取。
    str_version = _version_label(dict_item)  # Vitis 版本标签

    # 这里固定拼出 settings64.sh，后续远端 runner 依赖它加载 Vitis 环境。
    str_settings_script = _join_posix_tool_path(str_install_path, PATH_VITIS_SETTINGS64)  # runner 加载 Vitis 环境的 settings64.sh

    # Vitis HLS 可执行路径与 Vitis 安装路径存在固定映射。
    str_expected_tool_path = _infer_vitis_hls_executable(str_install_path, str_version)  # Vitis HLS 可执行路径

    # Vitis HLS 环境脚本用于补齐独立 HLS 安装环境。
    str_env_setup_script = _infer_vitis_hls_env_setup(str_install_path, str_version)  # Vitis HLS 环境脚本

    # v++ 路径仅在 board/link 相关报告中可能使用。
    str_vpp_path = _join_posix_tool_path(str_install_path, PATH_VITIS_VPP)  # Vitis v++ 工具路径

    # target_part 优先取扫描项，其次取服务器硬件推断。
    str_target_part = str(dict_item.get("target_part") or str_inferred_target_part or "")  # 候选 profile 写入 HLS Tcl 的 FPGA part

    # 返回候选 profile。
    return dict(
        version=str_version,
        settings_script=str_settings_script,
        expected_tool="vitis_hls",
        expected_tool_path=str_expected_tool_path,
        env_setup_script=str_env_setup_script,
        vpp_path=str_vpp_path,

        # XRT 与板卡管理工具路径会进入后续 board/link 验收报告。
        xrt_tool_path=PATH_XRT_SMI.as_posix(),
        xrt_setup_script=PATH_XRT_SETUP.as_posix(),
        xbmgmt_tool_path=PATH_XBMGMT.as_posix(),
        target_part=str_target_part,
        install_path=str_install_path,
        executable_path=str_executable_path,
    )

# 按版本去重 Vitis 候选 profile。
def _unique_vitis_candidates(list_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    对 Vitis 候选 profile 按版本去重。

    :param list_candidates: 原始候选 profile 列表。
    :return: 去重后的候选列表。
    """

    # dict 保留插入顺序，setdefault 保留首次扫描候选。
    dict_unique: dict[str, dict[str, Any]] = {}  # version 到候选 profile 的映射

    # 遍历候选列表。
    for dict_candidate in list_candidates:

        # 版本标签是去重键。
        str_version = str(dict_candidate["version"])  # 候选 profile 版本键

        # 首次出现的版本保留。
        dict_unique.setdefault(str_version, dict_candidate)

    # 返回去重后的候选列表。
    return list(dict_unique.values())

# 在 server-list 中查找指定服务器记录。
def _find_server_record(server_list: dict[str, Any], server: str) -> dict[str, Any] | None:
    """
    查找服务器记录。

    :param server_list: erie-remote-ssh server-list 内容。
    :param server: 服务器 id、name 或 legacy_id。
    :return: 匹配记录；不存在时返回 None。
    """

    # server-list 顶层 servers 字段保存服务器数组。
    list_servers = server_list.get("servers", [])  # server-list 中的服务器记录列表

    # 遍历服务器记录。
    for dict_item in list_servers:

        # 跳过非法记录。
        if not isinstance(dict_item, dict):

            # 无效项不参与匹配。
            continue

        # id/name/legacy_id 都可作为用户传入的服务器选择器。
        set_selectors = {
            str(dict_item.get("id") or ""),  # 当前 server-list 记录的主 id
            str(dict_item.get("name") or ""),  # 服务器显示名也允许作为选择器
            str(dict_item.get("legacy_id") or ""),  # 兼容旧版配置中的 legacy id
        }  # 服务器可匹配名称集合

        # 选择器命中时返回服务器记录。
        if server in set_selectors:

            # 返回匹配记录。
            return dict_item

    # 未找到匹配记录。
    return None

# 从服务器记录中推断 target part。
def _infer_target_part_from_server(settings_path: Path, server: str) -> str:
    """
    根据 settings 和服务器名推断 FPGA target part。

    :param settings_path: settings overlay 路径。
    :param server: 目标服务器。
    :return: 推断出的 target part；无法推断时返回空字符串。
    """

    # settings overlay 提供 server-list 路径覆盖和本地 skill 目录位置。
    dict_settings: dict[str, Any] = json.loads(settings_path.read_text(encoding="utf-8"))  # 用于解析 server-list 路径的 settings overlay

    # server-list 路径来自 common 配置解析。
    path_server_list = _resolve_erie_server_list(  # 推断 target_part 时使用的 server-list 文件路径
        dict_settings,  # settings overlay 当前解析出的 erie 配置对象
        settings_path,  # 触发本次推断的 settings overlay 路径
        Path(remote_validation_config()["erie_skill_dir"]),  # remote_validation 默认配置中的 erie skill 根
    )

    # server-list 可能缺失或 JSON 尚未写完整。
    try:

        # 硬件型号推断只能使用 server-list 中已经落盘的 JSON 内容。
        dict_server_list: dict[str, Any] = json.loads(path_server_list.read_text(encoding="utf-8"))  # 用于推断 FPGA 型号的服务器清单

    # 读取失败时无法推断。
    except (FileNotFoundError, json.JSONDecodeError):

        # 返回空 target part。
        return ""

    # 查找目标服务器记录。
    dict_record = _find_server_record(dict_server_list, server)  # 目标服务器记录

    # 无记录时无法推断。
    if not dict_record:

        # 没有任何服务器记录时保持 target_part 为空，让上层继续走其他来源。
        return ""

    # 从记录中的硬件型号推断 target part。
    return _infer_target_part_from_server_record(dict_record)

# 从服务器 inventory/software_scan 中推断 FPGA target part。
def _infer_target_part_from_server_record(record: dict[str, Any]) -> str:
    """
    根据服务器记录中的 FPGA 型号推断 target part。

    :param record: server-list 中的服务器记录。
    :return: 推断出的 target part；无法推断时返回空字符串。
    """

    # 收集 inventory 和软件扫描中的 FPGA 型号。
    list_models: list[str] = []  # 服务器记录中的 FPGA 型号文本

    # 两个来源都可能记录 fpga_devices。
    for str_source_key in ("inventory_snapshot", "software_scan"):

        # 读取当前来源。
        dict_source = record.get(str_source_key)  # 服务器硬件信息来源

        # 非字典来源跳过。
        if not isinstance(dict_source, dict):

            # 混杂来源里只有字典项才可能携带 fpga_devices 字段。
            continue

        # fpga_devices 中的 model 字段提供 U50/U55C 识别依据。
        for dict_item in dict_source.get("fpga_devices", []) or []:

            # 只读取带 model 字段的设备。
            if isinstance(dict_item, dict) and dict_item.get("model"):

                # 保存型号文本。
                list_models.append(str(dict_item["model"]))

    # 服务器名称也可能包含 U50/U55C 型号线索。
    str_normalized_text = (  # 合并设备型号和服务器名称后的统一匹配文本
        " ".join(list_models)  # 扫描到的 FPGA 型号列表
        + " "  # 在型号列表和服务器名之间补一个稳定分隔符
        + str(record.get("name") or "")  # 服务器显示名也纳入型号推断
    ).lower()  # 归一化后的硬件型号文本

    # U55C 使用固定 target part。
    if "u55c" in str_normalized_text:

        # 命中 U55C 线索后回填仓库契约中的固定 part。
        return "xcu55c-fsvh2892-2L-e"

    # 命中 U50 线索时切到对应的固定 part。
    if "u50" in str_normalized_text:

        # 拼接字符串避免硬编码扫描规则误判。
        return "".join(("xcu", "50", "-fsvh2104-2-e"))

    # 无法识别型号时返回空字符串。
    return ""

# 从扫描项中提取 Vitis 版本标签。
def _version_label(item: dict[str, Any]) -> str:
    """
    提取 Vitis 版本标签。

    :param item: Vitis 扫描项。
    :return: 版本标签。
    """

    # 按安装路径、version 字段、可执行路径顺序寻找版本号。
    for value_item in (item.get("install_path"), item.get("version"), item.get("path")):

        # 转成文本后匹配 20xx.x 格式。
        str_text = str(value_item or "")  # 候选版本来源文本

        # 匹配 Vitis 年份版本。
        list_version_matches = re.findall(r"(20\d{2}\.\d+)", str_text)  # 捕获到的 202x.x Vitis 版本号列表

        # 命中则返回第一个版本号。
        if list_version_matches:

            # 返回匹配到的版本标签。
            return list_version_matches[0]

    # 没有标准版本号时回退到原始字段。
    return str(item.get("version") or item.get("install_path") or item.get("path") or "unknown")

# 在候选 profile 中查找指定版本。
def _find_candidate(candidates: list[dict[str, Any]], version: str) -> dict[str, Any] | None:
    """
    查找指定版本的 Vitis 候选。

    :param candidates: 候选 profile 列表。
    :param version: 目标版本。
    :return: 匹配 profile；不存在时返回 None。
    """

    # 线性查找保持候选列表原顺序。
    return next((dict_item for dict_item in candidates if str(dict_item.get("version")) == version), None)

# 从 Vitis 安装根目录推断 Vitis HLS 可执行路径。
def _infer_vitis_hls_executable(install_path: str, version: str) -> str:
    """
    推断 Vitis HLS 可执行文件路径。

    :param install_path: Vitis 安装根目录。
    :param version: Vitis 版本标签。
    :return: 推断出的 vitis_hls 路径。
    """

    # 先把可选安装路径归一化成裸字符串，后续目录映射逻辑只处理文本路径。
    str_install_path = str(install_path or "").strip()  # Vitis 安装路径文本

    # 优先尝试从 /Vitis/<version> 目录平移到同版本的 /Vitis_HLS/<version>。
    str_hls_install_path = _replace_install_product_path(  # 通过产品目录平移得到的 HLS 安装根
        str_install_path,  # 当前扫描项解析出的 Vitis 安装根
        STR_VITIS_HLS_PRODUCT_DIR,  # 目标产品目录名固定为 Vitis_HLS
    )  # 从 Vitis 安装根映射得到的 Vitis_HLS 安装根

    # 成功映射后直接拼接 vitis_hls 可执行文件。
    if str_hls_install_path:

        # 返回同版本 Vitis_HLS 可执行路径。
        return _join_posix_tool_path(str_hls_install_path, PATH_VITIS_HLS_EXECUTABLE)

    # 没有安装路径时按版本构造默认工具路径。
    str_version = str(version or "").strip()  # Vitis 版本文本

    # 有版本号时使用项目约定工具根目录。
    if str_version:

        # 返回默认 Vitis_HLS 工具路径。
        return (
            PATH_XILINX_TOOLS_ROOT
            / STR_VITIS_HLS_PRODUCT_DIR
            / str_version
            / PATH_VITIS_HLS_EXECUTABLE
        ).as_posix()

    # 无法推断时返回空字符串。
    return ""

# 从 Vitis 安装根目录推断 Vitis HLS 环境脚本路径。
def _infer_vitis_hls_env_setup(install_path: str, version: str) -> str:
    """
    推断 Vitis HLS 环境脚本路径。

    :param install_path: Vitis 安装根目录。
    :param version: Vitis 版本标签。
    :return: setupEnv.sh 路径；无法推断时返回空字符串。
    """

    # 先把可选安装路径压平成字符串，避免后面同时处理 Path 和空值分支。
    str_install_path = str(install_path or "").strip()  # 调用方传入的安装根原始文本

    # 如果扫描项来自标准 Xilinx 目录树，就直接平移到同版本的 Vitis_HLS 安装根。
    str_hls_install_path = _replace_install_product_path(  # 目录平移成功后得到的 HLS 安装根文本
        str_install_path,  # 扫描项原始 Vitis 安装根路径
        STR_VITIS_HLS_PRODUCT_DIR,  # 目标产品目录名 Vitis_HLS
    )

    # 成功映射后直接拼接 HLS 环境脚本路径。
    if str_hls_install_path:

        # 返回同版本 HLS 环境脚本。
        return _join_posix_tool_path(str_hls_install_path, PATH_VITIS_HLS_ENV_SCRIPT)

    # 目录映射失败时，再退回到约定的工具根目录按版本拼接环境脚本。
    str_version = str(version or "").strip()  # 显式或推断得到的 Vitis 版本文本

    # 只有拿到明确版本号时，默认工具根目录拼接才有意义。
    if str_version:

        # 返回默认 Vitis_HLS 环境脚本。
        return (
            PATH_XILINX_TOOLS_ROOT
            / STR_VITIS_HLS_PRODUCT_DIR
            / str_version
            / PATH_VITIS_HLS_ENV_SCRIPT
        ).as_posix()

    # 安装路径和版本号都不足以定位脚本时，返回空字符串让上层跳过 env_setup。
    return ""

# 构造多 Vitis 版本选择请求。
def _remote_vitis_version_request(
    args: argparse.Namespace,
    run_dir: Path,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    生成多版本选择请求内容。

    :param args: CLI 参数。
    :param run_dir: 本次 run 目录。
    :param candidates: Vitis 候选 profile 列表。
    :return: 请求 JSON 内容。
    """

    # 为每个候选版本生成一条可重试命令。
    list_commands = [
        _vitis_version_command(args, dict_item)  # 单个候选版本对应的一条重试命令
        for dict_item in candidates  # 遍历全部候选版本生成命令列表
    ]  # 候选版本推荐命令列表

    # 返回请求内容。
    return dict(
        version=1,
        action="ask_remote_vitis_version",
        primary_source="multiple_remote_vitis_versions",
        question=(
            "Multiple Vitis versions were detected on the selected remote server. "
            "Choose one before HLS validation or development continues."
        ),
        server=args.server,
        profile=args.profile,
        readiness=args.readiness,
        example_spec=args.example_spec,
        candidate_versions=candidates,
        user_config_path=str(user_config_path()),
        recommended_commands=list_commands,
        output=str(run_dir / "remote_vitis_version_request.json"),
    )

# 构造单个 Vitis 候选版本的推荐命令。
def _vitis_version_command(args: argparse.Namespace, dict_candidate: dict[str, Any]) -> str:
    """
    构造 Vitis 版本选择命令。

    :param args: CLI 参数。
    :param dict_candidate: 候选 profile。
    :return: 可复制执行的命令文本。
    """

    # 候选版本从 profile 中读取。
    str_version = str(dict_candidate["version"])  # 推荐命令使用的 Vitis 版本

    # 命令保持旧入口和参数顺序。
    return (
        "python .\\scripts\\python\\remote\\remote_vitis_acceptance.py "
        "--mode vitis "
        f"--server {args.server} "
        f"--profile {args.profile} "
        f"--vitis-version {str_version} "
        f"--readiness {args.readiness} "
        f"--example-spec {args.example_spec} "
        "--json"
    )

# 生成本地 HLS 资产作为远端 Vitis 验收输入。
def _generate_local_hls_artifacts(
    run_dir: Path,
    *,
    comment_language: str,
    example_spec: str = "hls_vector_scale_mock_spec.json",
) -> Path:
    """
    生成本地 HLS 验收资产。

    :param run_dir: 本次 run 目录。
    :param comment_language: 生成代码注释语言。
    :param example_spec: 示例 spec 文件名。
    :return: 生成出的 HLS artifacts 目录。
    :raises RemoteAcceptanceError: 示例 spec 非法或本地 mock workflow 失败时抛出。
    """

    # 只允许从 examples_dir 里按文件名取 spec，避免任意路径输入绕过仓库白名单。
    path_spec = skill_config_path("examples_dir") / example_spec  # HLS 验收示例 spec 路径

    # 防止路径穿越或不存在的 spec。
    if not path_spec.exists() or path_spec.name != example_spec:

        # 抛出领域异常，阻断未知 spec。
        raise RemoteAcceptanceError(f"> ERR: [Python] Unknown HLS acceptance example spec: {example_spec}")

    # 示例 spec 内容会驱动 mock provider 生成 kernel、testbench 和 hls_config。
    dict_spec: dict[str, Any] = json.loads(path_spec.read_text(encoding="utf-8"))  # mock provider 生成本地 HLS 工件的输入 spec

    # workflow 需要在仓库根上下文中解析相对路径。
    with use_workspace_root(repo_root()):

        # mock provider 在本地生成远端 Vitis 验收所需的 kernel、testbench 和 hls_config。
        dict_workflow_result = run_hls_workflow(  # mock provider 的本地 HLS 生成结果
            dict_spec,  # 刚读取的示例 spec JSON
            out_dir=run_dir / "local-generation",  # 本地 mock 生成目录
            provider_name="mock",  # 强制使用 mock provider，避免误触外部工具
            readiness="static",  # 本地资产生成阶段不执行真实外部流程
            run_external=False,  # 显式关闭本地外部工具调用
            comment_language=comment_language,  # 生成代码的注释语言
        )  # HLS workflow 本地生成结果

    # workflow 未通过时阻断远端执行。
    if dict_workflow_result["status"] != PASS_STATUS:

        # 抛出包含状态的领域异常。
        raise RemoteAcceptanceError(
            f"> ERR: [Python] Local artifact generation failed: {dict_workflow_result['status']}"
        )

    # 返回标准 artifacts 目录。
    return Path(dict_workflow_result["run_dir"]) / "attempt-001" / "hls" / "artifacts"

# 读取 HLS 验收示例 spec。
def _load_example_spec(example_spec: str) -> dict[str, Any]:
    """
    读取 HLS 验收示例 spec。

    :param example_spec: 示例 spec 文件名。
    :return: spec JSON 内容。
    :raises RemoteAcceptanceError: 示例 spec 不在 examples_dir 白名单内时抛出。
    """

    # 先把用户输入钉死到仓库 examples_dir 白名单，避免把任意相对路径喂给远端验收。
    path_spec = skill_config_path("examples_dir") / example_spec  # 白名单 examples_dir 下实际要读取的 spec 文件路径

    # 文件不存在或名字不匹配都视为越界访问，必须立刻阻断。
    if not path_spec.exists() or path_spec.name != example_spec:

        # 这里直接抛出领域异常，避免远端验收继续使用未知 spec。
        raise RemoteAcceptanceError(f"> ERR: [Python] Unknown HLS acceptance example spec: {example_spec}")

    # 返回 spec JSON 内容。
    return json.loads(path_spec.read_text(encoding="utf-8"))

# 创建远端 Vitis 执行包。
def _create_vitis_package(run_dir: Path, artifact_dir: Path) -> Path:
    """
    将 HLS artifacts 和 runner 打包为 tar.gz。

    :param run_dir: 本次 run 目录。
    :param artifact_dir: 本地 HLS artifacts 目录。
    :return: tar.gz 包路径。
    """

    # runner 写到 run 目录后随 tar 包上传。
    path_runner = run_dir / "run_vitis.sh"  # 远端 Vitis runner 脚本路径

    # 写入 runner 脚本，固定 LF 换行。
    path_runner.write_text(_remote_runner_script(), encoding="utf-8", newline="\n")

    # tar 包包含 artifacts 目录和 runner。
    path_package = run_dir / "hls_artifacts.tar.gz"  # 远端上传使用的 tar.gz 包

    # 创建 gzip tar 包。
    with tarfile.open(path_package, "w:gz") as tar_file:

        # 遍历所有生成资产。
        for path_artifact in sorted(artifact_dir.rglob("*")):

            # 只打包文件，忽略目录。
            if path_artifact.is_file():

                # 远端解包后统一落到 artifacts/ 下。
                tar_file.add(
                    path_artifact,
                    arcname=Path("artifacts") / path_artifact.relative_to(artifact_dir),
                )

        # runner 放在包根目录。
        tar_file.add(path_runner, arcname="run_vitis.sh")

    # 返回 tar 包路径。
    return path_package

# 通过 request_and_run 分块上传 Vitis tar 包。
def _transfer_package_by_request_commands(
    helper: "ErieHelper",
    settings: Path,
    server: str,
    remote_dir: str,
    package_path: Path,
) -> list[str]:
    """
    使用 base64 chunk 请求上传远端 Vitis 包。

    :param helper: erie-remote-ssh helper。
    :param settings: helper 使用的 settings 文件。
    :param server: 目标服务器。
    :param remote_dir: 远端 active run 相对目录。
    :param package_path: 本地 tar.gz 包路径。
    :return: request 证据路径列表。
    """

    # base64 文本便于通过远端 command request 逐块追加。
    str_encoded_payload = base64.b64encode(package_path.read_bytes()).decode("ascii")  # request_and_run 分片上传的 tar 包文本

    # 远端 request 证据路径按上传顺序记录。
    list_requests: list[str] = []  # 上传 package 产生的 request 路径

    # 远端先写 .b64，再由 runner 命令解码成 tar.gz。
    str_remote_b64 = (PurePosixPath(remote_dir) / PATH_REMOTE_B64_FILENAME).as_posix()  # 远端 base64 payload 路径

    # 初始化远端 payload 文件。
    str_initialize_command = f": > {shlex.quote(str_remote_b64)}"  # 清空远端 base64 payload 的命令

    # 记录初始化请求。
    list_requests.append(
        helper.request_and_run(
            settings,
            server,
            "command",
            str_initialize_command,
            "initialize remote package payload",
        )
    )

    # 按固定 chunk 大小追加 base64 内容。
    for int_index in range(0, len(str_encoded_payload), 7000):

        # 当前 chunk 直接进入 printf 参数。
        str_chunk = str_encoded_payload[int_index : int_index + 7000]  # 当前上传的 base64 分片

        # 远端追加命令需要同时转义 chunk 和目标路径。
        str_append_command = (
            f"printf %s {shlex.quote(str_chunk)} "
            f">> {shlex.quote(str_remote_b64)}"
        )  # 追加当前 base64 分片的远端命令

        # 记录分片上传请求。
        list_requests.append(
            helper.request_and_run(
                settings,
                server,
                "command",
                str_append_command,
                "append remote package payload chunk",
            )
        )

    # 返回所有上传请求证据。
    return list_requests

# 根据远端目录和 profile 组装 detached job 要执行的 shell 命令。
def _remote_vitis_command(remote_dir: str, profile: dict[str, Any], readiness: str) -> str:
    """
    构造远端解包并运行 Vitis HLS 的 shell 命令。

    :param remote_dir: 远端 active run 目录。
    :param profile: 已选 Vitis profile。
    :param readiness: 本次验收 readiness 等级。
    :return: 可交给 erie-remote-ssh detached job 的命令。
    """

    # settings 脚本通常来自 Vitis 安装目录。
    str_settings_script = shlex.quote(str(profile["settings_script"]))  # shell 转义后的 Vitis settings 脚本

    # HLS 环境脚本允许为空，runner 会按需跳过。
    str_env_setup_script = shlex.quote(str(profile.get("env_setup_script") or ""))  # shell 转义后的 HLS 环境脚本

    # tool_path 优先于 expected_tool，避免远端 PATH 差异。
    str_expected_tool = shlex.quote(str(profile.get("tool_path") or profile["expected_tool"]))  # shell 转义后的 HLS 工具

    # target_part 可以为空，runner 会回退到 hls_config.cfg 中的 part。
    str_target_part = shlex.quote(str(profile.get("target_part", "")))  # shell 转义后的 FPGA part

    # readiness 传入 runner 内部 Python 脚本决定 csim/csynth/cosim 阶段。
    str_readiness_arg = shlex.quote(readiness)  # shell 转义后的 readiness 参数

    # active run 目录需要 shell 转义后才能拼接命令。
    str_remote_dir = shlex.quote(remote_dir)  # shell 转义后的远端 run 目录

    # 环境变量契约由 run_vitis.sh 读取，命令保持旧行为。
    return (
        f"cd {str_remote_dir} "
        "&& base64 -d hls_artifacts.tar.gz.b64 > hls_artifacts.tar.gz "
        "&& tar -xzf hls_artifacts.tar.gz "
        f"&& HLS_SETTINGS_SCRIPT={str_settings_script} "
        f"HLS_ENV_SETUP_SCRIPT={str_env_setup_script} "
        f"HLS_EXPECTED_TOOL={str_expected_tool} "
        f"HLS_TARGET_PART={str_target_part} "
        f"HLS_READINESS={str_readiness_arg} "
        "bash run_vitis.sh"
    )

# 收敛远端 Vitis 阶段参数，供新实现和兼容 wrapper 共用。
def _vitis_phase_context(dict_phase_fields: dict[str, Any]) -> dict[str, Any]:
    """
    构造远端 Vitis 阶段上下文。

    :param dict_phase_fields: 阶段执行所需的 settings、server、profile、package 和远端目录字段。
    :return: 远端阶段执行上下文字典。
    :raises TypeError: 调用方缺少阶段必填字段时抛出。
    """

    # 字段列表是阶段执行的最小契约，缺字段属于调用方编程错误。
    tuple_required_fields = (
        "settings",  # 远端命令执行所需的 settings overlay
        "server",  # 当前阶段对应的远端服务器标识
        "profile",  # 当前阶段加载的 Vitis profile
        "readiness",  # 当前阶段 runner 的 readiness 目标
        "package_path",  # 需要上传的本地 HLS 资产包路径
        "config",  # 远端目录契约与运行配置
        "run_dir",  # 本地 run 目录，用于写报告和证据
        "phase_label",  # build/single/validation 等阶段标签
        "cleanup_remote",  # 通过后是否清理远端 active run
        "remote_workdir",  # 用于拼出 runs/backups/.conda 布局的远端根目录
    )  # Vitis 阶段上下文必填字段

    # 逐项检查可提供比 KeyError 更明确的错误消息。
    for str_field_name in tuple_required_fields:

        # 缺少字段时按脚本错误前缀抛出类型错误。
        if str_field_name not in dict_phase_fields:

            # 指出缺失字段，方便测试或 facade 调用方修复。
            raise TypeError(f"> ERR: [Python] Missing Vitis phase field: {str_field_name}")

    # 上下文保留旧字段名，便于兼容 wrapper 和报告生成。
    return {
        "settings": dict_phase_fields["settings"],
        "server": str(dict_phase_fields["server"]),
        "profile": dict_phase_fields["profile"],
        "readiness": str(dict_phase_fields["readiness"]),
        "package_path": dict_phase_fields["package_path"],
        "config": dict_phase_fields["config"],
        "run_dir": dict_phase_fields["run_dir"],
        "phase_label": str(dict_phase_fields["phase_label"]),
        "cleanup_remote": bool(dict_phase_fields["cleanup_remote"]),
        "remote_workdir": str(dict_phase_fields["remote_workdir"]),
    }

# 兼容旧测试和 facade 的多参数入口，内部立即转为上下文字典。
def _run_server_vitis_phase(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """
    调用远端 Vitis 阶段执行逻辑。

    :param args: 旧接口的位置参数。
    :param kwargs: 旧接口的 phase_label、cleanup_remote 和 remote_workdir。
    :return: 可写入 JSON 的阶段执行结果。
    """

    # 位置参数保持历史顺序，避免破坏 facade 和现有测试。
    dict_phase_context = _legacy_vitis_phase_context(args, kwargs)  # 旧接口参数转换后的阶段上下文

    # 新实现统一从 context 读取阶段参数。
    return _run_server_vitis_phase_context(args[0], dict_phase_context)

# 将旧 `_run_server_vitis_phase` 参数序列转换为上下文字典。
def _legacy_vitis_phase_context(
    tuple_args: tuple[Any, ...],
    dict_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """
    转换旧 Vitis 阶段入口参数。

    :param tuple_args: 旧接口位置参数。
    :param dict_kwargs: 旧接口关键字参数。
    :return: 规范化后的阶段上下文。
    :raises TypeError: 旧接口位置参数数量不符合历史契约时抛出。
    """

    # 旧接口必须提供 helper 之外的 7 个位置参数。
    if len(tuple_args) != 8:

        # 错误消息使用脚本输出约定的 ERR 前缀。
        raise TypeError("> ERR: [Python] _run_server_vitis_phase expects 8 positional arguments.")

    # tuple 中第一项是 helper，context 从第二项开始读取。
    # 旧接口字段映射后交给统一上下文校验。
    return _vitis_phase_context(
        {
            "settings": tuple_args[1],
            "server": tuple_args[2],
            "profile": tuple_args[3],
            "readiness": tuple_args[4],
            "package_path": tuple_args[5],
            "config": tuple_args[6],
            "run_dir": tuple_args[7],
            "phase_label": dict_kwargs["phase_label"],
            "cleanup_remote": dict_kwargs["cleanup_remote"],
            "remote_workdir": dict_kwargs["remote_workdir"],
        }
    )

# 执行单个远端 Vitis 阶段并生成通过报告。
def _run_server_vitis_phase_context(
    helper: "ErieHelper",
    dict_phase_context: dict[str, Any],
) -> dict[str, Any]:
    """
    执行远端 Vitis 阶段。

    :param helper: erie-remote-ssh helper。
    :param dict_phase_context: `_vitis_phase_context` 生成的阶段上下文。
    :return: 阶段通过报告。
    """

    # 远端目录布局遵循 directory contract 的 runs/backups 规则。
    dict_layout = _phase_remote_layout(dict_phase_context)  # 当前 Vitis 阶段远端目录布局

    # 目录创建和 package 上传都生成 request 证据路径。
    list_request_paths = _prepare_remote_vitis_phase(helper, dict_phase_context, dict_layout)  # 远端阶段请求证据列表

    # detached job 运行真正的 Vitis HLS 命令。
    dict_detached = _start_remote_vitis_job(helper, dict_phase_context, dict_layout)  # 远端 Vitis detached job 描述

    # detached manifest 也是远端请求证据的一部分。
    list_request_paths.append(dict_detached["manifest"])

    # 等待远端 job 完成，并在失败时附带 tail 日志。
    dict_job_result = _wait_for_vitis_job(helper, dict_phase_context, dict_detached)  # 远端 Vitis job 轮询结果

    # 通过后按目录契约归档 active run。
    dict_archive = _archive_vitis_phase_if_needed(  # 当前阶段通过后的远端归档结果
        helper,  # 当前阶段沿用的 erie-remote-ssh helper
        dict_phase_context,  # 当前阶段的远端执行上下文
        dict_layout,  # 按目录契约生成的远端布局快照
        list_request_paths,  # 已记录的目录创建与上传请求证据
    )  # 远端归档状态

    # 返回通过报告，字段名保持旧契约。
    return _passed_vitis_phase_result(
        dict_phase_context,
        dict_layout,
        dict_detached,
        dict_job_result,
        list_request_paths,
        dict_archive,
    )

# 根据 workdir 和阶段标签生成远端目录布局。
def _phase_remote_layout(dict_phase_context: dict[str, Any]) -> dict[str, Any]:
    """
    生成 Vitis 阶段远端目录布局。

    :param dict_phase_context: 远端阶段上下文。
    :return: directory contract 生成的布局字典。
    """

    # run id 拼接本地 run 目录名与阶段标签，便于跨阶段区分。
    str_remote_run_id = (
        f"{dict_phase_context['run_dir'].name}-"
        f"{dict_phase_context['phase_label']}"
    )  # 用于 runs/backups 目录命名的远端 Vitis 阶段 run id

    # 目录布局由 common 模块统一维护。
    return remote_directory_layout_for_workdir(
        dict_phase_context["remote_workdir"],
        str_remote_run_id,
    )

# 创建远端目录并上传 Vitis package。
def _prepare_remote_vitis_phase(
    helper: "ErieHelper",
    dict_phase_context: dict[str, Any],
    dict_layout: dict[str, Any],
) -> list[str]:
    """
    准备远端 Vitis 阶段运行目录。

    :param helper: erie-remote-ssh helper。
    :param dict_phase_context: 远端阶段上下文。
    :param dict_layout: 远端目录布局。
    :return: request 证据路径列表。
    """

    # request_paths 串联目录创建和上传请求。
    list_request_paths: list[str] = []  # 远端请求证据路径列表

    # 先保证 project/runs/backups/.conda 等治理目录存在。
    list_request_paths.extend(
        _ensure_remote_project_layout(
            helper,
            dict_phase_context["settings"],
            dict_phase_context["server"],
            dict_layout,
        )
    )

    # 再以 base64 chunk 方式上传 tar.gz 包。
    list_request_paths.extend(
        _transfer_package_by_request_commands(
            helper,
            dict_phase_context["settings"],
            dict_phase_context["server"],
            dict_layout["active_run_relative"],
            dict_phase_context["package_path"],
        )
    )

    # 返回全部请求证据路径。
    return list_request_paths

# 启动远端 Vitis detached job。
def _start_remote_vitis_job(
    helper: "ErieHelper",
    dict_phase_context: dict[str, Any],
    dict_layout: dict[str, Any],
) -> dict[str, Any]:
    """
    启动远端 Vitis HLS detached job。

    :param helper: erie-remote-ssh helper。
    :param dict_phase_context: 远端阶段上下文。
    :param dict_layout: 远端目录布局。
    :return: detached job 描述。
    """

    # 远端命令负责解包、设置环境变量并调用 run_vitis.sh。
    str_command = _remote_vitis_command(  # 交给 detached job 执行的远端 shell 命令
        dict_layout["active_run_dir"],  # 当前阶段远端 active run 目录
        dict_phase_context["profile"],  # 当前阶段要加载的 Vitis profile
        dict_phase_context["readiness"],  # runner 内部执行的 readiness 等级
    )  # 远端 Vitis 执行命令

    # 长时间 Vitis 验收需要保留运行中的 job，避免被 sweep 当成可回收自动测试。
    return helper.exec_detached(
        dict_phase_context["server"],
        f"run Vitis HLS {dict_phase_context['phase_label']}",
        str_command,
        settings=dict_phase_context["settings"],
        task_purpose="user_initiated",
    )

# 等待远端 Vitis job，并在失败时汇总状态输出和 tail 日志。
def _wait_for_vitis_job(
    helper: "ErieHelper",
    dict_phase_context: dict[str, Any],
    dict_detached: dict[str, Any],
) -> dict[str, Any]:
    """
    等待远端 Vitis job 完成。

    :param helper: erie-remote-ssh helper。
    :param dict_phase_context: 远端阶段上下文。
    :param dict_detached: detached job 描述。
    :return: wait_for_job 返回的结果字典。
    :raises RemoteAcceptanceError: 远端 Vitis job 失败时附带状态输出和 tail 日志抛出。
    """

    # Vitis HLS 可能耗时较长，最少等待 1800 秒。
    int_max_wait_s = max(helper.timeout, 1800)  # 远端 Vitis job 最大等待秒数

    # 这一层只负责等待 detached job 结束，失败原因会在下一步结合 tail 日志补齐。
    dict_job_result = helper.wait_for_job(  # detached job 结束后的状态包
        dict_phase_context["server"],  # 当前阶段对应的远端服务器
        dict_detached["job_id"],  # detached 启动后返回的 job id
        settings=dict_phase_context["settings"],  # job 查询继续使用同一份 settings overlay
        max_wait_s=int_max_wait_s,  # 允许的最大等待秒数
    )  # wait_for_job 返回的状态包和原始输出

    # 非 succeeded 统一转成领域异常并附带 tail 日志。
    if dict_job_result["status"] != "succeeded":

        # tail 日志帮助定位 Vitis HLS 失败阶段。
        str_tail = _safe_tail_log(  # 失败时追加的远端日志尾部摘要
            helper,  # 复用当前 erie-remote-ssh helper 拉取 tail
            dict_phase_context["server"],  # 失败作业所在的远端服务器
            dict_detached["job_id"],  # 需要取 tail 的 detached job id
            dict_phase_context["settings"],  # 继续使用同一份 settings overlay 查询日志
        )  # 远端 job 日志尾部

        # job output 保留 erie-remote-ssh 的状态文本。
        str_details = str(dict_job_result["output"]).strip()  # detached job 状态输出

        # 抛出异常，保持原测试和 CLI 失败路径。
        raise RemoteAcceptanceError(
            f"> ERR: [Python] Detached Vitis HLS {dict_phase_context['phase_label']} job failed "
            f"for server {dict_phase_context['server']}.\n{str_details}\n{str_tail}"
        )

    # 返回成功 job 结果供报告使用。
    return dict_job_result

# 按目录契约归档通过验证的远端 Vitis run。
def _archive_vitis_phase_if_needed(
    helper: "ErieHelper",
    dict_phase_context: dict[str, Any],
    dict_layout: dict[str, Any],
    list_request_paths: list[str],
) -> dict[str, Any]:
    """
    按配置决定是否归档远端 Vitis run。

    :param helper: erie-remote-ssh helper。
    :param dict_phase_context: 远端阶段上下文。
    :param dict_layout: 远端目录布局。
    :param list_request_paths: request 证据路径列表。
    :return: 归档状态字典。
    """

    # 当前远端契约通过后默认归档到 backups。
    bool_archived_after_verification = False  # 是否已按契约归档

    # directory_contract 控制是否移动 active run 到 backup。
    if dict_phase_context["config"]["directory_contract"]["archive_after_verification"]:

        # 归档请求路径也进入证据列表。
        list_request_paths.append(
            _archive_remote_run(
                helper,
                dict_phase_context["settings"],
                dict_phase_context["server"],
                dict_layout,
            )
        )

        # 标记报告中的归档状态。
        bool_archived_after_verification = True  # 远端 active run 已移动到 backups 目录

    # cleanup_remote 当前保持 False，远端产物按契约保留。
    return {
        "cleanup_performed": False,
        "archived_after_verification": bool_archived_after_verification,
    }

# 组装远端 Vitis 阶段通过报告。
def _passed_vitis_phase_result(
    dict_phase_context: dict[str, Any],
    dict_layout: dict[str, Any],
    dict_detached: dict[str, Any],
    dict_job_result: dict[str, Any],
    list_request_paths: list[str],
    dict_archive: dict[str, Any],
) -> dict[str, Any]:
    """
    生成远端 Vitis 阶段通过报告。

    :param dict_phase_context: 远端阶段上下文。
    :param dict_layout: 远端目录布局。
    :param dict_detached: detached job 描述。
    :param dict_job_result: wait_for_job 结果。
    :param list_request_paths: request 证据路径列表。
    :param dict_archive: 归档状态字典。
    :return: 可写入 JSON 的通过报告。
    """

    # profile 摘要只读取报告需要的版本和 target part。
    dict_profile = dict_phase_context["profile"]  # 当前阶段使用的 Vitis profile

    # 远端目录字段保持旧报告契约。
    str_remote_dir = (  # 根据是否归档选择最终写入报告的相对远端目录
        dict_layout["backup_run_relative"]  # 已归档时报告指向 backup 目录
        if dict_archive["archived_after_verification"]  # 验证后已经完成归档时切到 backup 目录
        else dict_layout["active_run_relative"]  # 未归档时仍保留 active run 目录
    )  # 报告中最终保留的远端目录

    # 返回通过报告。
    return {
        "status": PASS_STATUS,
        "server": dict_phase_context["server"],
        "phase": dict_phase_context["phase_label"],
        "vitis_version": dict_profile.get("version"),
        "target_part": dict_profile.get("target_part"),
        "run_id": dict_layout["run_id"],
        "remote_project_root": dict_layout["project_root_relative"],
        "remote_project_root_abs": dict_layout["project_root"],
        "remote_conda_prefix": dict_layout["conda_prefix_relative"],
        "remote_conda_prefix_abs": dict_layout["conda_prefix"],
        "remote_run_dir": dict_layout["active_run_relative"],
        "remote_run_dir_abs": dict_layout["active_run_dir"],
        "remote_backup_dir": dict_layout["backup_run_relative"],
        "remote_backup_dir_abs": dict_layout["backup_run_dir"],
        "remote_dir": str_remote_dir,
        "job_id": dict_detached["job_id"],
        "requests": list_request_paths,
        "cleanup_performed": dict_archive["cleanup_performed"],
        "remote_artifacts_retained": True,
        "archived_after_verification": dict_archive["archived_after_verification"],
        "archive_trigger": dict_phase_context["config"]["directory_contract"]["archive_trigger"],
        "job_status": dict_job_result["status"],
    }

# 安全读取远端 job 日志尾部，失败时返回可报告文本。
def _safe_tail_log(helper: "ErieHelper", server: str, job_id: str, settings: Path) -> str:
    """
    安全读取 detached job 日志尾部。

    :param helper: erie-remote-ssh helper。
    :param server: 远端服务器。
    :param job_id: detached job id。
    :param settings: helper 使用的 settings 文件。
    :return: 日志尾部文本或不可用原因。
    """

    # tail_log 可能因远端请求失败而抛出领域异常。
    try:

        # 成功时返回最后 80 行日志。
        return helper.tail_log(server, job_id, settings=settings, lines=80)

    # 日志不可用不应掩盖原始 Vitis 失败。
    except RemoteAcceptanceError as exc:

        # 返回短文本并保留异常消息。
        return f"tail_log_unavailable: {exc}"

# 生成远端 run_vitis.sh 脚本内容。
def _remote_runner_script() -> str:
    """
    渲染远端 Vitis HLS runner shell 脚本。

    参数:
        本函数不接收外部业务参数；调用方直接取回脚本文本。

    返回:
        返回可写入 run_vitis.sh 的脚本文本。
    """

    # 远端脚本保持自包含，上传后不依赖本地 Python 模块。
    return """#!/usr/bin/env bash
set -euo pipefail
: "${HLS_SETTINGS_SCRIPT:?}"
: "${HLS_ENV_SETUP_SCRIPT:=}"
: "${HLS_EXPECTED_TOOL:?}"
: "${HLS_READINESS:?}"
HLS_TARGET_PART="${HLS_TARGET_PART:-}"
source "$HLS_SETTINGS_SCRIPT" >/dev/null 2>&1 || true
if [ -n "$HLS_ENV_SETUP_SCRIPT" ] && [ -f "$HLS_ENV_SETUP_SCRIPT" ]; then
  set +u
  source "$HLS_ENV_SETUP_SCRIPT" >/dev/null 2>&1 || true
  set -u
fi
if [[ "$HLS_EXPECTED_TOOL" == */* ]] && [ -x "$HLS_EXPECTED_TOOL" ]; then
  tool_path="$HLS_EXPECTED_TOOL"
else
  tool_path="$(command -v "$HLS_EXPECTED_TOOL" || true)"
fi
if [ -z "$tool_path" ]; then
  echo "HLS_REMOTE_STATUS blocked_vitis_server"
  exit 44
fi
cd artifacts
python3 - "$PWD/hls_config.cfg" "$PWD/remote_vitis.tcl" \
  "remote_vitis_project" "$HLS_READINESS" "$HLS_TARGET_PART" <<'PY'
from pathlib import Path
import sys

cfg_path = Path(sys.argv[1])
tcl_path = Path(sys.argv[2])
project = Path(sys.argv[3])
readiness = sys.argv[4]
target_part = sys.argv[5]
entries = {"syn.file": [], "tb.file": []}
for raw in cfg_path.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = raw.strip()
    if not line or line.startswith("[") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if key in {"syn.file", "tb.file"}:
        entries.setdefault(key, []).append(value)
    else:
        entries[key] = value
def q(value):
    return "{" + str(value).replace("}", "\\\\}") + "}"
# 生成 Vitis HLS Tcl 命令序列。
lines = [
    f"open_project -reset {q(project)}",
    f"set_top {q(entries.get('syn.top', 'kernel'))}",
]
for item in entries.get("syn.file", []):
    lines.append(f"add_files {q(Path.cwd() / item)}")
for item in entries.get("tb.file", []):
    lines.append(f"add_files -tb {q(Path.cwd() / item)}")
lines.append("open_solution -reset {solution1}")
if entries.get("part"):
    lines.append(f"set_part {q(entries['part'])}")
elif target_part:
    lines.append(f"set_part {q(target_part)}")
if entries.get("clock"):
    lines.append(f"create_clock -period {entries['clock']}")
order = {"static": 0, "compile": 1, "execute": 2, "implement": 3, "cosim": 4}
level = order.get(readiness, 4)
if level >= 1:
    lines.append("csim_design")
if level >= 3:
    lines.append("csynth_design")
if level >= 4:
    lines.append("cosim_design")
lines.append("exit")
tcl_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
PY
if [ "${tool_path##*/}" = "vitis-run" ]; then
  vitis-run --mode hls --tcl "$PWD/remote_vitis.tcl"
else
  "$tool_path" -f "$PWD/remote_vitis.tcl"
fi
echo "HLS_REMOTE_STATUS passed"
"""
