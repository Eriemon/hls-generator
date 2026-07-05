#!/usr/bin/env python3
"""远端板级 HLS 验收辅助流程。

本模块只负责 board mode 的平台选择、U55C payload 上传、板级 host 生成、
远端编译运行命令拼装，以及验收报告整理。机器可读输出由调用方写入报告文件，
本模块不直接向 stdout 打印结构化内容。
"""
# 允许类型注解引用运行时尚未导入的 helper 类型。
from __future__ import annotations

# 标准库依赖覆盖 CLI 参数、路径处理、压缩包生成和远端 shell 转义。
import argparse
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

# 共用远端验收设施提供状态常量、配置读写、探测函数和报告落盘能力。
from remote_acceptance_common import (
    # board 报告和 runner 共用的状态标记。
    BOARD_STATUS_MARKER,
    BOARD_RUNNABLE_PROFILE,
    BLOCKED_BOARD_STATUS,
    BLOCKED_PROFILE_STATUS,
    BLOCKED_VERSION_STATUS,
    BLOCKED_VITIS_STATUS,
    FAILED_STATUS,
    PASS_STATUS,
    # 领域异常和技能根目录用于模板解析。
    RemoteAcceptanceError,
    SKILL_ROOT,
    # 平台配置和本地 U55C payload 管理接口。
    board_acceptance_config,
    U55C_PLATFORM_NAME,
    default_local_u55c_payload_root,
    get_board_platform_selection,
    # 平台名、payload 和远端目录解析保持与 common 模块一致。
    infer_target_part_from_platform_selection,
    prepare_local_u55c_platform_archive,
    remote_directory_layout_for_workdir,
    resolve_host_template_path,
    set_board_platform_selection,
    validate_local_board_platform_payload,
    # 远端执行、探测和报告写入的内部复用函数。
    _archive_remote_run,
    _ensure_remote_project_layout,
    _merge_profile_fields,
    _new_run_dir,
    _parse_request_path,
    # board mode 前置探测包括平台、硬件、workdir 和 Vitis 环境。
    _probe_board_toolchain,
    _probe_hardware_fingerprint,
    _probe_platform_name,
    _probe_remote_workdir,
    _probe_target_part_hint,
    _probe_vitis,
    # JSON、settings overlay 和报告写入由 common 模块统一实现。
    _write_json,
    _write_erie_settings_overlay,
    _write_report,
)

# Vitis 远端验收模块复用本地 HLS 资产生成、版本选择和日志截取逻辑。
from remote_acceptance_vitis import (
    # board mode 复用 Vitis mode 的本地资产和 profile 解析能力。
    _generate_local_hls_artifacts,
    _infer_target_part_from_server,
    _load_example_spec,
    _resolve_profile_config,
    # 远端包传输和失败日志截取保持与 Vitis mode 一致。
    _safe_tail_log,
    _select_vitis_profile,
    _transfer_package_by_request_commands,
    _vitis_version_candidates,
)

# board 执行包内的 host 源码路径是固定契约。
BOARD_HOST_ARCNAME = Path("board") / "host.cpp"  # board host 在 tar 包中的相对路径

# board mode 的主入口只保留调度职责，具体探测和执行交给下层 helper。
def _run_board_mode(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    plan: list[str],
    topology: dict[str, Any],
) -> dict[str, Any]:
    """
    运行板级 HLS 远端验收。

    :param args: CLI 解析后的 board mode 参数，包含服务器、平台和样例选择。
    :param config: 远端验收配置，包含 Vitis profile 与目录契约。
    :param helper: erie-remote-ssh helper，负责预检、上传、远端执行和轮询。
    :param plan: 当前验收计划步骤，用于报告复现。
    :param topology: 已解析的服务器拓扑，board mode 使用其中的单服务器。
    :return: 可写入 JSON 报告的验收状态字典。
    """

    # 建立本次 board run 的目录、settings 和 Vitis profile 上下文。
    dict_context = _prepare_board_run_context(args, config, helper, topology)  # 远端验收基础上下文

    # profile 或版本选择被治理规则阻断时，直接落盘当前阻断报告。
    dict_early_result = dict_context.get("early_result")  # profile 解析阶段可能产生的早停报告

    # 保持原有早停行为，避免缺 profile 时继续访问远端板卡。
    if dict_early_result:

        # 报告仍写入本次 run 目录，便于上游汇总审查。
        _write_report(dict_context["run_dir"], dict_early_result)

        # 返回 profile/version 阻断结果给上层 CLI。
        return dict_early_result

    # 平台、硬件指纹和板级工具链探测都需要已选定的 Vitis profile。
    dict_readiness = _prepare_board_readiness(args, config, helper, dict_context)  # 板级验收就绪状态

    # 任一板级前置条件不满足时，输出可操作的平台上传计划和探测证据。
    if dict_readiness["blocking_reasons"]:

        # 阻断报告保留上传计划、硬件探测和工具链探测细节。
        dict_blocked = _blocked_board_result(args, config, plan, topology, dict_readiness)  # board 前置条件阻断报告

        # 早停报告与远端 job 报告使用同一写入位置。
        _write_report(dict_readiness["run_dir"], dict_blocked)

        # 返回阻断结果，调用方据此提示用户补齐平台或硬件证据。
        return dict_blocked

    # 前置条件通过后才生成 HLS 资产并启动远端 board job。
    dict_result = _run_board_validation_job(args, config, helper, plan, topology, dict_readiness)  # board job 最终报告

    # 把远端执行结果写入 run 目录，保持原 CLI 报告契约。
    _write_report(dict_readiness["run_dir"], dict_result)

    # 返回远端验收结果给聚合入口。
    return dict_result

# 准备 board mode 的本地 run 目录、远端 settings 和可用 Vitis profile。
def _prepare_board_run_context(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    topology: dict[str, Any],
) -> dict[str, Any]:
    """
    准备板级验收的基础上下文。

    :param args: CLI 参数，提供 profile、版本和服务器选择。
    :param config: 远端验收配置，提供 Vitis profile 与 run 根目录。
    :param helper: 远端执行 helper，用于预检和软件扫描。
    :param topology: board mode 服务器拓扑。
    :return: 包含 run 目录、settings、远端 workdir、候选版本和已选 profile 的字典。
    """

    # 读取配置中的 Vitis profile 表，供版本和必填字段检查使用。
    dict_profiles = config.get("vitis_profiles", {})  # 配置文件声明的 Vitis profile 集合

    # 为 board mode 创建独立 run 目录，避免与 Vitis-only 验收报告混合。
    path_run_dir = _new_run_dir(config, "board")  # 本次 board 验收的本地 run 目录

    # 写出 erie-remote-ssh settings overlay，供 helper 后续命令复用。
    path_settings = _write_erie_settings_overlay(config, path_run_dir)  # 本次远端命令使用的 settings 文件

    # board mode 只使用拓扑中的单一服务器。
    str_server = str(topology["server"])  # 承担 board compile/link/host-run 的服务器名

    # 远端预检先确认服务器和 settings 可用。
    helper.preflight(str_server, settings=path_settings)

    # 软件扫描为后续 Vitis 版本候选生成提供远端事实。
    helper.scan_software(str_server, settings=path_settings)

    # 解析远端 workspace 根目录，后续平台和 run 目录都按治理契约放置。
    str_remote_workdir = _probe_remote_workdir(str_server, path_settings, helper)  # 远端工作目录绝对路径

    # 收集当前服务器上可用的 Vitis 版本候选。
    list_candidates = _vitis_version_candidates(config, path_settings, str_server)  # Vitis 候选 profile 列表

    # 根据 CLI profile 和候选版本解析 board 所需的基础字段。
    dict_board_profile = _resolve_profile_config(  # board mode 所需基础字段解析结果
        args,  # CLI 中的 profile、version 与 readiness 选择
        path_run_dir,  # 缺字段时把阻断请求直接写进当前 run 目录
        candidates=list_candidates,  # 远端软件扫描得到的 Vitis 候选列表
        configured_profiles=dict_profiles,  # 配置文件里声明的 board profile 表
        required_fields=("settings_script", "expected_tool"),  # 本轮 board 验收要求补齐的字段
    )

    # profile 字段不完整时保留原有阻断结果。
    if dict_board_profile.get("status") == BLOCKED_PROFILE_STATUS:

        # 返回上下文和早停报告，主入口负责统一写报告。
        return {
            "run_dir": path_run_dir,
            "early_result": dict_board_profile,
        }

    # 从候选版本、用户显式选择或缓存选择中确定本次 Vitis profile。
    dict_selected_profile = _select_vitis_profile(args, path_run_dir, list_candidates, dict_board_profile)  # 绑定本次远端 Vitis 版本和 settings_script 的 profile

    # 多版本需要人工确认时沿用 Vitis mode 的阻断结果。
    if dict_selected_profile.get("status") == BLOCKED_VERSION_STATUS:

        # 返回版本选择请求，避免在版本未确认时启动板级流程。
        return {
            "run_dir": path_run_dir,
            "early_result": dict_selected_profile,
        }

    # 将 profile 默认字段补到最终选择中。
    dict_selected_profile = _merge_profile_fields(dict_selected_profile, dict_board_profile)  # 合并后的 board profile

    # 返回后续探测阶段需要的所有上下文字段。
    return {
        "run_dir": path_run_dir,
        "settings": path_settings,
        "server": str_server,
        "remote_workdir": str_remote_workdir,
        "selected_profile": dict_selected_profile,
    }

# 补齐 target part、平台选择，并执行 board 前置条件探测。
def _prepare_board_readiness(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    dict_context: dict[str, Any],
) -> dict[str, Any]:
    """
    整理 board job 启动前必须满足的远端事实。

    :param args: CLI 参数，可能显式覆盖 target part 或平台路径。
    :param config: 验收配置，提供目录契约。
    :param helper: 远端 helper，用于平台、硬件和工具链探测。
    :param dict_context: `_prepare_board_run_context` 返回的基础上下文。
    :return: 包含 profile、探测结果、上传结果和阻断原因的字典。
    """

    # 从上下文中取出频繁使用的路径和服务器字段。
    path_run_dir = dict_context["run_dir"]  # 当前 run 目录 Path 对象

    # settings 文件贯穿所有远端探测命令。
    path_settings = dict_context["settings"]  # 传给平台、硬件和工具链探测命令的 settings overlay

    # 服务器名用于读写本机缓存的平台选择。
    str_server = str(dict_context["server"])  # 远端服务器标识

    # 远端 workdir 决定平台 payload 和 active run 的治理路径。
    str_remote_workdir = str(dict_context["remote_workdir"])  # 远端 workspace 根路径

    # profile 会在本函数内补齐 target part 和平台字段。
    dict_selected_profile = dict(dict_context["selected_profile"])  # 可变的 board profile 副本

    # 先补齐 target part，平台默认名需要依赖器件型号。
    dict_selected_profile = _profile_with_target_part(  # 确定 Vitis 编译器件型号后的 board profile
        args,  # CLI 中可能显式覆盖 target part
        path_settings,  # 远端探测 target part 使用的 settings overlay
        helper,  # 执行远端 target part 探测的 helper
        str_server,  # 当前 board 验收使用的目标服务器
        dict_selected_profile,  # 待补齐 target part 的 profile 副本
    )

    # 根据 CLI、缓存或目录契约解析平台路径并合并到 profile。
    dict_selected_profile = _profile_with_platform_selection(  # 已合并平台选择的 board profile
        args,  # CLI 中可能显式覆盖平台名或 xpfm 路径
        str_server,  # 平台缓存按服务器维度读取
        str_remote_workdir,  # 相对平台路径要基于该 workdir 规整
        dict_selected_profile,  # 已带 target part 的 profile 副本
        config["directory_contract"],  # 平台目录必须符合治理契约
    )

    # 探测远端平台名或 xpfm 是否已经可用。
    dict_platform_probe = _probe_platform_name(str_server, path_settings, helper, dict_selected_profile)  # 平台可用性探测结果

    # 平台缺失时尝试上传治理内置的 U55C payload。
    dict_platform_upload = _maybe_upload_board_platform(  # 平台自动上传结果
        config,  # 提供目录契约与平台上传控制开关
        helper,  # 负责远端 request-command 与上传的 helper
        dict_context,  # run_dir、settings 与 remote_workdir 上下文
        dict_selected_profile,  # 当前待落位的平台选择
        dict_platform_probe,  # 上传前的平台探测结果
    )

    # 上传成功后需要把选择结果合并回 profile 并重新探测。
    if dict_platform_upload.get("status") == PASS_STATUS:

        # 上传返回的 selection 是远端已落位的平台根路径。
        dict_selected_profile = _merge_profile_fields(dict_selected_profile, dict_platform_upload["selection"])  # 上传后的 profile

        # 重新探测用于确认 xpfm 文件已经存在。
        dict_platform_probe = _probe_platform_name(str_server, path_settings, helper, dict_selected_profile)  # 上传后的平台探测结果

    # 平台探测结果可补齐 profile 中尚为空的平台名或 xpfm。
    dict_selected_profile = _profile_with_platform_probe_fields(  # 已吸收平台探测字段的 board profile
        dict_selected_profile,  # 当前 board profile 副本
        dict_platform_probe,  # 平台探测得到的平台名和 xpfm 结果
    )

    # 读取板卡硬件指纹，最终信心需要同机 U55C 事实。
    dict_hardware_probe = _probe_hardware_fingerprint(str_server, path_settings, helper, dict_selected_profile)  # 板卡硬件探测结果

    # 检查 v++、g++、XRT 等 board runner 所需工具。
    dict_toolchain_probe = _probe_board_toolchain(str_server, path_settings, helper, dict_selected_profile)  # 板级工具链探测结果

    # 汇总所有阻断原因，报告中保持机器可读枚举。
    list_blocking_reasons = _board_blocking_reasons(  # board job 启动前的阻断原因列表
        dict_selected_profile,  # 当前选中的 board profile
        dict_platform_probe,  # 平台名与 xpfm 的可用性证据
        dict_hardware_probe,  # 板卡硬件探测证据
        dict_toolchain_probe,  # v++、XRT 与 g++ 工具链探测证据
    )

    # 返回 board job 或阻断报告需要的完整上下文。
    return {
        "run_dir": path_run_dir,
        "settings": path_settings,
        "server": str_server,
        "remote_workdir": str_remote_workdir,
        "selected_profile": dict_selected_profile,
        "platform_probe": dict_platform_probe,
        "platform_upload": dict_platform_upload,
        "hardware_probe": dict_hardware_probe,
        "toolchain_probe": dict_toolchain_probe,
        "blocking_reasons": list_blocking_reasons,
    }

# 按 CLI、远端 hint 和服务器配置顺序补齐 target part。
def _profile_with_target_part(
    args: argparse.Namespace,
    path_settings: Path,
    helper: "ErieHelper",
    str_server: str,
    dict_selected_profile: dict[str, Any],
) -> dict[str, Any]:
    """
    为 board profile 补齐器件型号。

    :param args: CLI 参数，可能带有显式 target part。
    :param path_settings: 远端命令 settings overlay。
    :param helper: 远端 helper，用于读取 target part hint。
    :param str_server: 目标服务器名。
    :param dict_selected_profile: 待补齐的 board profile。
    :return: 带 target part 的 profile 副本。
    """

    # 在副本上修改，避免调用方持有的原始 profile 被半途污染。
    dict_profile = dict(dict_selected_profile)  # target part 补齐过程使用的 profile 副本

    # CLI 显式 target part 优先，避免远端推断覆盖用户意图。
    if args.target_part and not str(dict_profile.get("target_part") or "").strip():

        # 写入 target part 供 Vitis 编译命令和平台默认名推断共同使用。
        dict_profile["target_part"] = str(args.target_part)  # CLI 覆盖的 board 编译器件型号

    # 缺 target part 时优先读取远端 hint，再退回服务器配置推断。
    if not str(dict_profile.get("target_part") or "").strip():

        # 组合两种推断来源，保持原有兜底顺序。
        str_inferred_part = (
            _probe_target_part_hint(str_server, path_settings, helper)  # 远端即时探测出的 target part hint
            or _infer_target_part_from_server(path_settings, str_server)  # 服务器登记信息推断出的 target part
        )  # 远端 hint 或服务器配置推断出的器件型号

        # 只有拿到有效 hint 才写回 profile。
        if str_inferred_part:

            # target part 会影响平台名默认值和最终 Vitis 命令。
            dict_profile["target_part"] = str_inferred_part  # 远端推断得到的 Vitis target part

    # 返回已补齐 target part 的 profile。
    return dict_profile

# 合并 CLI、缓存和目录契约得到的 board 平台选择。
def _profile_with_platform_selection(
    args: argparse.Namespace,
    str_server: str,
    str_remote_workdir: str,
    dict_selected_profile: dict[str, Any],
    dict_directory_contract: dict[str, Any],
) -> dict[str, Any]:
    """
    为 board profile 合并平台选择。

    :param args: CLI 参数，可能显式提供平台名或 xpfm 路径。
    :param str_server: 目标服务器名，用于读取平台选择缓存。
    :param str_remote_workdir: 远端 workspace 根目录。
    :param dict_selected_profile: 已选 Vitis profile。
    :param dict_directory_contract: 目录治理契约。
    :return: 带平台字段和可能 target part 的 profile。
    """

    # 解析本次 board 验收采用的平台名、平台根目录和 xpfm 路径。
    dict_platform_selection = _resolve_board_platform_selection(  # CLI、缓存或契约推导得到的平台选择
        args,  # CLI 中可能显式覆盖平台路径
        str_server,  # 平台缓存按服务器粒度读取
        str_remote_workdir,  # 相对平台路径要规整到该 workdir 下
        dict_selected_profile,  # 已补齐 target part 的 board profile
        dict_directory_contract,  # 平台落位必须遵守的目录契约
    )

    # 将平台选择合并回 profile，供平台探测和命令组装使用。
    dict_profile = _merge_profile_fields(dict_selected_profile, dict_platform_selection)  # 带平台字段的 profile

    # 如果平台选择能反推出 target part，则补齐缺失的器件信息。
    if not str(dict_profile.get("target_part") or "").strip():

        # 平台名中的 U50/U55C 信息可作为 target part 的治理兜底。
        str_platform_part = _infer_target_part_from_platform_selection(dict_profile)  # 平台字段推断出的器件型号

        # 推断成功时写回 profile。
        if str_platform_part:

            # 后续硬件指纹和 Vitis 命令都需要 target part。
            dict_profile["target_part"] = str_platform_part  # 平台名反推出的 target part

    # 返回已合并平台字段的 profile。
    return dict_profile

# 用平台探测结果补齐 profile 中缺失的平台名和 xpfm。
def _profile_with_platform_probe_fields(
    dict_selected_profile: dict[str, Any],
    dict_platform_probe: dict[str, Any],
) -> dict[str, Any]:
    """
    吸收平台探测返回的规范字段。

    :param dict_selected_profile: 当前 board profile。
    :param dict_platform_probe: `_probe_platform_name` 返回的平台探测结果。
    :return: 补齐 selected_platform 或 selected_xpfm 后的 profile。
    """

    # 在副本上回填探测字段，避免意外修改外部引用。
    dict_profile = dict(dict_selected_profile)  # 平台探测字段回填用 profile 副本

    # 平台探测若返回规范平台名，则补齐 profile 中的空字段。
    if dict_platform_probe.get("selected_platform") and not str(dict_profile.get("platform_name") or "").strip():

        # 规范平台名会进入最终报告和 Vitis 命令。
        dict_profile["platform_name"] = str(dict_platform_probe["selected_platform"])  # 平台探测返回的规范平台名

    # 探测到的 xpfm 路径优先作为远端平台文件。
    if dict_platform_probe.get("selected_xpfm") and not str(dict_profile.get("remote_xpfm") or "").strip():

        # xpfm 路径比平台名更精确，可直接传给 Vitis。
        dict_profile["remote_xpfm"] = str(dict_platform_probe["selected_xpfm"])  # 平台探测返回的 xpfm 路径

    # 返回补齐探测字段后的 profile。
    return dict_profile

# 在平台探测失败时尝试使用本地受治理的 U55C payload 自动修复远端平台目录。
def _maybe_upload_board_platform(
    config: dict[str, Any],
    helper: "ErieHelper",
    dict_context: dict[str, Any],
    dict_selected_profile: dict[str, Any],
    dict_platform_probe: dict[str, Any],
) -> dict[str, Any]:
    """
    按目录契约尝试上传本地 U55C 平台 payload。

    :param config: 验收配置，提供 directory_contract。
    :param helper: 远端 helper，用于请求上传和解压命令。
    :param dict_context: board run 基础上下文，提供目录、settings 和服务器名。
    :param dict_selected_profile: 当前 board profile。
    :param dict_platform_probe: 首次平台探测结果。
    :return: 平台上传结果；未触发上传时返回空字典。
    """

    # 已经探测到平台时无需上传 payload。
    if dict_platform_probe["status"] == PASS_STATUS:

        # 空字典表示没有额外上传动作。
        return {}

    # 平台归档写入本次 run 目录，便于与报告一起审计。
    path_run_dir = dict_context["run_dir"]  # U55C 平台归档的本地父目录

    # 上传和解压命令都使用同一个远端 settings overlay。
    path_settings = dict_context["settings"]  # 平台 payload 上传命令使用的 settings 文件

    # 平台选择缓存按服务器维度保存。
    str_server = str(dict_context["server"])  # 接收 U55C payload 的远端服务器

    # 远端 workdir 用于把绝对平台路径转换成 request 相对路径。
    str_remote_workdir = str(dict_context["remote_workdir"])  # 平台 payload 目标 workspace

    # 仅在可确定本地 U55C payload 时生成上传选择。
    dict_upload_selection = _local_board_platform_upload_selection(  # 可自动上传的平台选择
        str_remote_workdir,  # 平台 payload 要上传到的远端 workspace
        dict_selected_profile,  # 当前平台选择上下文
        dict_platform_probe,  # 现有远端平台探测结果
        config["directory_contract"],  # 平台目录仍要符合治理契约
    )

    # 没有受治理本地 payload 时交给阻断报告输出人工步骤。
    if not dict_upload_selection:

        # 返回空上传结果，后续阻断报告会生成 upload plan。
        return {}

    # 上传失败也要被报告捕获，所以只吞掉领域内 RemoteAcceptanceError。
    try:

        # 上传、解压并验证 xpfm 文件存在。
        return _upload_local_board_platform_payload(
            helper,
            path_settings,
            str_server,
            path_run_dir,
            str_remote_workdir,
            dict_upload_selection,
        )

    # 把平台上传错误转成结构化失败结果，避免异常跳过探测证据。
    except RemoteAcceptanceError as exc:

        # 失败结果保留用户可复用的 selection 字段。
        return {
            "status": FAILED_STATUS,
            "error": str(exc),
            "selection": dict_upload_selection,
        }

# 把 profile 字段和探测状态转换成稳定的阻断原因枚举。
def _board_blocking_reasons(
    dict_selected_profile: dict[str, Any],
    dict_platform_probe: dict[str, Any],
    dict_hardware_probe: dict[str, Any],
    dict_toolchain_probe: dict[str, Any],
) -> list[str]:
    """
    汇总 board job 的启动阻断原因。

    :param dict_selected_profile: 已补齐平台和 target part 的 profile。
    :param dict_platform_probe: 平台探测结果。
    :param dict_hardware_probe: 板卡硬件指纹探测结果。
    :param dict_toolchain_probe: 板级工具链探测结果。
    :return: 机器可读的阻断原因列表。
    """

    # 阻断原因保持原有枚举字符串，便于历史报告兼容。
    list_blocking_reasons: list[str] = []  # board 前置条件缺口列表

    # 平台名缺失会导致 Vitis 无法解析目标平台。
    if not str(dict_selected_profile.get("platform_name") or "").strip():

        # 记录平台名缺失，报告中再给出上传计划。
        list_blocking_reasons.append("missing_platform_name")

    # target part 缺失会削弱平台默认推断和硬件匹配。
    if not str(dict_selected_profile.get("target_part") or "").strip():

        # 记录器件型号缺失，调用方需要补 CLI 参数或服务器配置。
        list_blocking_reasons.append("missing_target_part")

    # 平台探测失败通常意味着 xpfm 尚未上传或路径不匹配。
    if dict_platform_probe["status"] != PASS_STATUS:

        # 保留平台探测失败枚举，报告中附完整 probe 细节。
        list_blocking_reasons.append("platform_probe")

    # 硬件指纹失败时不能声明真实板级验收完成。
    if dict_hardware_probe["status"] != PASS_STATUS:

        # 硬件缺失或不可见会阻断 final confidence。
        list_blocking_reasons.append("hardware_probe")

    # 工具链缺失会导致远端 runner 无法编译或执行 host。
    if dict_toolchain_probe["status"] != PASS_STATUS:

        # 工具链问题单独列出，便于区分平台文件和软件安装问题。
        list_blocking_reasons.append("toolchain_probe")

    # 返回稳定顺序的阻断原因。
    return list_blocking_reasons

# 生成 board 前置条件阻断报告，并附带平台上传建议。
def _blocked_board_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    plan: list[str],
    topology: dict[str, Any],
    dict_readiness: dict[str, Any],
) -> dict[str, Any]:
    """
    组装 board 前置条件失败时的结构化报告。

    :param args: CLI 参数，提供 profile、readiness 和样例信息。
    :param config: 验收配置，提供目录契约和归档触发规则。
    :param plan: 当前验收计划步骤。
    :param topology: 已解析服务器拓扑。
    :param dict_readiness: `_prepare_board_readiness` 返回的探测上下文。
    :return: 可写入 JSON 的 board 阻断报告。
    """

    # 平台缺失时要明确写出本地来源、远端目标和人工补传步骤。
    dict_upload_plan = _board_platform_upload_plan(  # 缺平台时列出 remote_platform_root、remote_xpfm 和手工补传命令
        dict_readiness["run_dir"],  # 在这个本地 run 目录生成 remote_board_platform_request.json
        dict_readiness["server"],  # 手工补传命令最终要指向的目标服务器
        dict_readiness["remote_workdir"],  # 远端平台目录和 xpfm 要落在该 workdir 下
        dict_readiness["selected_profile"],  # 计划里要回显的平台名与 target_part 来源
        dict_readiness["platform_probe"],  # 计划里要说明当前平台名和 xpfm 缺失到了什么程度
        config["directory_contract"],  # 计划里要按契约计算 platforms/alveo 的远端落位
    )

    # 这里把 board 阻断状态、证据和补传计划一起固化，便于 handoff 直接引用。
    dict_result = dict(  # 阻断报告里同时保留 blocking_reasons、platform_probe、hardware_probe 和 toolchain_probe
        status=BLOCKED_BOARD_STATUS,  # 前置条件未满足时的固定状态
        mode="board",  # 让上层知道这里阻断的是 board 而不是 vitis readiness 路径
        server=dict_readiness["server"],  # 阻断实际发生在哪台远端机器上
        profile=args.profile,  # 阻断报告里回显用户这次请求的 profile 名
        readiness=args.readiness,  # 阻断报告里继续保留本轮 readiness 标签
        example_spec=args.example_spec,  # 阻断报告里保留当前要验收的样例规格
        run_dir=str(dict_readiness["run_dir"]),  # handoff 回填使用的本地执行目录
        topology=topology["topology"],  # 解析后的拓扑快照
        steps=plan,  # 阻断前已经完成的前置探测步骤列表
        blocking_reasons=dict_readiness["blocking_reasons"],  # 上层据此区分缺平台、缺板卡还是缺工具链
        platform_probe=dict_readiness["platform_probe"],  # 阻断时平台名和 xpfm 的探测回包
        platform_upload=dict_readiness["platform_upload"],  # 是否尝试过自动上传平台 payload 的结果
        hardware_probe=dict_readiness["hardware_probe"],  # 阻断时采集到的板卡型号与设备指纹
        toolchain_probe=dict_readiness["toolchain_probe"],  # 阻断时采集到的 v++、XRT 和 g++ 可用性
        platform_upload_plan=dict_upload_plan,  # 缺平台时给用户的手工上传计划
        uses_erie_remote_ssh=True,  # 标记整条远端探测链是通过 erie-remote-ssh 执行的
    )

    # 返回阻断报告给主入口统一落盘。
    return dict_result

# 生成 board 执行包、创建远端目录、上传并等待后台 job。
def _prepare_local_board_package(
    args: argparse.Namespace,
    dict_readiness: dict[str, Any],
) -> dict[str, Any]:
    """
    生成 board job 需要的本地 HLS 资产和执行包。

    :param args: CLI 参数，提供样例规格和注释语言。
    :param dict_readiness: board 前置条件探测上下文。
    :return: 本地资产目录、执行包路径和 board host 元数据。
    """

    # 复用普通 HLS 资产生成流程，确保 board host 与 kernel 源来自同一 run。
    path_artifact_dir = _generate_local_hls_artifacts(  # 本地 HLS kernel 资产目录
        dict_readiness["run_dir"],  # 当前 board run 的本地输出根目录
        comment_language=args.comment_language,  # 注释语言配置
        example_spec=args.example_spec,  # 驱动资产生成的样例选择
    )

    # 打包 board host、runner 和 HLS 资产，供远端 active run 解包。
    tuple_package = _create_board_package(  # board 远端执行包与元数据
        dict_readiness["run_dir"],  # 执行包所属的本地 run 目录
        path_artifact_dir,  # 已生成的本地 HLS 资产目录
        example_spec=args.example_spec,  # 让执行包绑定同一个样例
    )

    # 返回远端执行要复用的本地资产和打包结果。
    return dict(
        artifact_dir=path_artifact_dir,
        package_path=tuple_package[0],
        board_metadata=tuple_package[1],
    )

# 把本地包准备、远端布局、上传和后台 job 轮询串成 board 执行上下文。
def _prepare_board_job_execution(
    args: argparse.Namespace,
    helper: "ErieHelper",
    dict_readiness: dict[str, Any],
) -> dict[str, Any]:
    """
    生成 board job 执行上下文。

    :param args: CLI 参数，提供样例规格和注释语言。
    :param helper: 远端 helper，负责上传、后台执行和轮询。
    :param dict_readiness: board 前置条件探测上下文。
    :return: 本地资产、远端布局、请求记录和 job 结果。
    """

    # 本地资产和执行包先统一准备，避免远端阶段同时关心生成细节。
    dict_local_package = _prepare_local_board_package(args, dict_readiness)  # board job 本地资产和执行包

    # 远端目录布局必须来自目录契约，不能手写任意路径。
    dict_layout = remote_directory_layout_for_workdir(  # 远端 active/backup run 目录布局
        dict_readiness["remote_workdir"],  # 远端项目 workdir 根目录
        dict_readiness["run_dir"].name,  # 当前 run 目录名
    )

    # 记录所有通过 request 审批执行的远端路径和 manifest。
    list_request_paths: list[str] = []  # 远端请求 manifest 与归档路径列表

    # 确保远端 project、conda 和 run 目录符合治理约束。
    list_layout_requests = _ensure_remote_project_layout(  # 远端目录创建请求记录
        helper,  # 负责 request 批准与执行的远端 helper
        dict_readiness["settings"],  # 远端项目设置
        dict_readiness["server"],  # 目标服务器
        dict_layout,  # mkdir 和归档共用的目录布局快照
    )

    # 汇总目录创建请求，供报告追踪。
    list_request_paths.extend(list_layout_requests)

    # 上传 board 执行包到 active run。
    list_upload_requests = _transfer_package_by_request_commands(  # board 执行包上传请求记录
        helper,  # 负责上传命令审批与执行的远端 helper
        dict_readiness["settings"],  # 上传 request 使用的项目设置
        dict_readiness["server"],  # 接收执行包的目标主机
        dict_layout["active_run_relative"],  # active run 相对目录
        dict_local_package["package_path"],  # 本地 board 执行包路径
    )

    # 汇总上传请求，保持报告与 helper manifest 对齐。
    list_request_paths.extend(list_upload_requests)

    # 拼装远端 board runner 命令。
    str_command = _remote_board_command(  # detached job 真实执行的板级 runner 命令
        dict_layout["active_run_dir"],  # 远端 active run 绝对目录
        dict_readiness["selected_profile"],  # runner 需要读取的平台与器件选择
        dict_local_package["board_metadata"],  # runner 需要的 top function 与 host 模板元数据
    )

    # 通过 detached job 执行真实板级编译、链接和 host-run。
    dict_detached = helper.exec_detached(  # 远端后台 job 描述
        dict_readiness["server"],  # 执行 board acceptance 的目标服务器
        "run board-level HLS acceptance",  # detached job 的用途标签
        str_command,  # 实际执行的远端命令
        settings=dict_readiness["settings"],  # detached job 使用的项目设置
        task_purpose="automated_test",  # 自动化板级扫例必须声明可自动清理的 detached 用途
    )

    # board link 和 host-run 可能较慢，因此保留至少 5400 秒等待窗口。
    int_wait_seconds = max(helper.timeout, 5400)  # board job 最大等待秒数

    # 轮询后台 job 直到结束或超时。
    dict_job_result = helper.wait_for_job(  # 这里拿到 wait_for_job 的 status/output，用于决定抓尾日志还是归档 active run
        dict_readiness["server"],  # wait_for_job 要轮询的那台远端机器
        dict_detached["job_id"],  # detached runner 启动后返回的 job 标识
        settings=dict_readiness["settings"],  # helper 读取远端 manifest 时要套用的项目 overlay
        max_wait_s=int_wait_seconds,  # 覆盖默认超时以适应板级 link 和 host-run
    )

    # detached manifest 是远端执行证据的一部分。
    list_request_paths.append(dict_detached["manifest"])

    # 返回通过/失败分支都要复用的 board job 执行上下文。
    return dict(
        artifact_dir=dict_local_package["artifact_dir"],
        layout=dict_layout,
        request_paths=list_request_paths,
        detached=dict_detached,
        job_result=dict_job_result,
        board_metadata=dict_local_package["board_metadata"],
    )

# 生成本地 HLS 资产、上传远端包并等待 board runner 完成。
def _run_board_validation_job(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: "ErieHelper",
    plan: list[str],
    topology: dict[str, Any],
    dict_readiness: dict[str, Any],
) -> dict[str, Any]:
    """
    执行真实远端 board compile/link/host-run。

    :param args: CLI 参数，提供样例、注释语言和 readiness 标签。
    :param config: 验收配置，提供目录归档契约。
    :param helper: 远端 helper，负责上传、后台执行和轮询。
    :param plan: 当前验收计划步骤。
    :param topology: 已解析服务器拓扑。
    :param dict_readiness: board 前置条件探测上下文。
    :return: board job 失败或通过时的结构化报告。
    """

    # board 执行上下文复用本地资产、远端布局、请求记录和 job 轮询结果。
    dict_job_context = _prepare_board_job_execution(args, helper, dict_readiness)  # board job 共享执行上下文

    # 分支判断直接依赖 job 轮询结果，失败时抓日志，通过时执行归档。
    dict_job_result = dict_job_context["job_result"]  # 控制失败/归档分支的 job 结果

    # 失败分支还要回收 manifest 和 job_id，所以先把 detached 元数据单独取出。
    dict_detached = dict_job_context["detached"]  # 失败诊断要复用的 detached 元数据

    # job 失败时截取远端日志尾部，便于定位编译或 host-run 问题。
    if dict_job_result["status"] != "succeeded":

        # 尾部日志保留失败上下文，但不在终端直接打印结构化内容。
        str_tail_log = _safe_tail_log(  # 失败报告要附带的远端日志尾段
            helper,  # 用于回读远端日志的 helper
            dict_readiness["server"],  # 远端目标服务器
            dict_detached["job_id"],  # tail 日志要定位的 detached job
            dict_readiness["settings"],  # 回收日志使用的项目设置
        )

        # 返回失败报告给主入口落盘。
        return _failed_board_job_result(args, plan, topology, dict_readiness, dict_job_result, str_tail_log)

    # 验证通过后才归档 active run 到 backups。
    str_archive_path = _archive_remote_run(  # 成功 run 归档后写入报告与 request 清单的 manifest 路径
        helper,  # 负责执行归档 request 的远端 helper
        dict_readiness["settings"],  # archive 阶段读取项目配置的 settings overlay
        dict_readiness["server"],  # 执行归档的目标主机
        dict_job_context["layout"],  # 本轮 active/backup run 目录布局
    )

    # 归档路径也进入 request 列表，形成完整远端证据链。
    dict_job_context["request_paths"].append(str_archive_path)

    # 返回通过报告，包含远端目录、平台、硬件和工具链证据。
    return _passed_board_job_result(
        args,
        config,
        topology,
        dict_readiness,
        dict_job_context,
    )

# 整理 board job 失败时需要保留的远端日志和探测证据。
def _failed_board_job_result(
    args: argparse.Namespace,
    plan: list[str],
    topology: dict[str, Any],
    dict_readiness: dict[str, Any],
    dict_job_result: dict[str, Any],
    str_tail_log: str,
) -> dict[str, Any]:
    """
    组装 board job 失败报告。

    :param args: CLI 参数，提供 profile、readiness 与样例信息。
    :param plan: 当前验收步骤。
    :param topology: 服务器拓扑。
    :param dict_readiness: board 前置探测上下文。
    :param dict_job_result: helper.wait_for_job 返回的 job 状态。
    :param str_tail_log: 失败 job 的远端日志尾部。
    :return: 可写入 JSON 的失败报告。
    """

    # 使用 dict() 让失败报告结构与阻断报告分离。
    dict_result = dict(  # board job 失败报告
        status=FAILED_STATUS,  # 远端 board job 执行失败后的统一状态
        mode="board",  # 当前失败报告对应的验收分支
        server=dict_readiness["server"],  # 实际执行 board 验收的目标服务器
        profile=args.profile,  # 用户请求的 board profile
        readiness=args.readiness,  # 当前 readiness 标签
        example_spec=args.example_spec,  # 本轮执行的样例规格
        run_dir=str(dict_readiness["run_dir"]),  # 失败 job 对应的本地执行目录
        topology=topology["topology"],  # 服务器拓扑快照
        steps=plan,  # 已执行的 board 验收步骤
        hardware_probe=dict_readiness["hardware_probe"],  # 失败前采集到的板卡硬件指纹证据
        toolchain_probe=dict_readiness["toolchain_probe"],  # 失败前采集到的 v++/XRT/g++ 工具链证据
        job_status=dict_job_result["status"],  # detached job 最终状态
        job_output=dict_job_result["output"],  # helper 记录的远端输出
        tail_log=str_tail_log,  # 失败时回收的远端日志尾段
        uses_erie_remote_ssh=True,  # 远端执行通过 erie-remote-ssh helper 完成
    )

    # 返回失败报告给统一写报告入口。
    return dict_result

# 通过报告里的 board profile 摘要只保留平台和器件关键信息。
def _board_profile_summary(dict_readiness: dict[str, Any]) -> dict[str, str]:
    """
    生成通过报告中的 board profile 摘要。

    :param dict_readiness: board 前置探测上下文。
    :return: 仅包含平台和器件字段的 profile 子字典。
    """

    # profile 子报告只暴露平台相关字段，避免把完整 settings 泄漏进摘要。
    return dict(
        platform_name=str(dict_readiness["selected_profile"].get("platform_name") or ""),
        remote_platform_root=str(dict_readiness["selected_profile"].get("remote_platform_root") or ""),
        remote_xpfm=str(dict_readiness["selected_profile"].get("remote_xpfm") or ""),
        target_part=str(dict_readiness["selected_profile"].get("target_part") or ""),
    )

# 整理 board job 通过后的远端目录、平台和板卡证据。
def _passed_board_job_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    topology: dict[str, Any],
    dict_readiness: dict[str, Any],
    dict_job_context: dict[str, Any],
) -> dict[str, Any]:
    """
    组装 board job 通过报告。

    :param args: CLI 参数，提供 profile、readiness 和样例名。
    :param config: 验收配置，提供目录归档触发规则。
    :param topology: 服务器拓扑。
    :param dict_readiness: board 前置探测上下文。
    :param dict_job_context: 远端 job 产物，包含资产目录、布局、请求记录和元数据。
    :return: 可写入 JSON 的通过报告。
    """

    # 本地 HLS 资产目录回显到报告，便于复查 host 与 kernel 来源。
    path_artifact_dir = dict_job_context["artifact_dir"]  # 通过报告中的本地资产目录

    # 布局快照后续会拆成 remote_project_root、remote_run_dir 和 remote_backup_dir 等路径字段。
    dict_layout = dict_job_context["layout"]  # 通过报告要拆分引用的远端目录布局快照

    # 请求路径列表串联目录创建、上传、detached manifest 和归档记录。
    list_request_paths = dict_job_context["request_paths"]  # 远端请求证据列表

    # detached job 描述提供报告中的 job_id 和远端执行 manifest。
    dict_detached = dict_job_context["detached"]  # 通过报告引用的远端后台 job 证据

    # wait_for_job 的状态进入最终报告。
    dict_job_result = dict_job_context["job_result"]  # board job 轮询结果

    # board host 元数据包含 top function 和模板来源。
    dict_board_metadata = dict_job_context["board_metadata"]  # board host 生成元数据

    # profile 子报告只保留平台名、xpfm 和 target part 等摘要字段。
    dict_board_profile = _board_profile_summary(dict_readiness)  # 报告中的 board 平台摘要

    # 通过报告保留原字段名，兼容已有 confidence_loop 聚合逻辑。
    dict_result = dict(  # 把 board 成功后的远端目录、request 证据、平台摘要和 job 结果统一交给 confidence_loop 聚合
        status=PASS_STATUS,  # 远端 board job 全部通过后的统一状态
        mode="board",  # 告诉聚合层这份结果属于 board acceptance 分支
        server=dict_readiness["server"],  # 真正执行完编译、链接和 host-run 的远端机器
        topology=topology["topology"],  # 这次命中路由后保留下来的服务器拓扑快照
        profile=args.profile,  # 用户当前要求执行的 board profile 名称
        vitis_version=str(dict_readiness["selected_profile"].get("version") or ""),  # settings script 最终加载到 runner 里的 Vitis 版本
        readiness=args.readiness,  # 通过报告对应的 readiness 标签
        example_spec=args.example_spec,  # 真正触发本轮编译与 host-run 的样例名
        run_dir=str(dict_readiness["run_dir"]),  # 这次 board 验收对应的本地 run 目录路径
        artifact_dir=str(path_artifact_dir),  # 本地 artifacts 目录，里面放着 kernel、host 和 runner 输入
        run_id=dict_layout["run_id"],  # active run 在本轮证据链里的逻辑标识
        remote_project_root=dict_layout["project_root_relative"],  # 远端项目根目录的相对路径
        remote_project_root_abs=dict_layout["project_root"],  # 远端项目根目录的绝对路径
        remote_conda_prefix=dict_layout["conda_prefix_relative"],  # 远端 conda 前缀的相对路径
        remote_conda_prefix_abs=dict_layout["conda_prefix"],  # runner 激活环境前真正要 source 的 conda 前缀绝对路径
        remote_run_dir=dict_layout["active_run_relative"],  # active run 在远端 workdir 下的相对目录
        remote_run_dir_abs=dict_layout["active_run_dir"],  # active run 的远端绝对目录
        remote_backup_dir=dict_layout["backup_run_relative"],  # archive 后 backup run 在 workdir 下的相对目录
        remote_backup_dir_abs=dict_layout["backup_run_dir"],  # archive 后 backup run 的远端绝对落位目录
        remote_dir=dict_layout["backup_run_relative"],  # 兼容旧 confidence 聚合逻辑仍保留的 backup 目录别名
        cleanup_performed=False,  # 这轮通过后没有额外执行远端清理动作
        remote_artifacts_retained=True,  # host、xo、xclbin 等远端产物保留下来供复查
        archived_after_verification=True,  # 已把 active run 归档到了 backup 目录
        archive_trigger=config["directory_contract"]["archive_trigger"],  # 目录契约要求的归档触发条件
        requests=list_request_paths,  # mkdir、upload、archive 等 request manifest 路径列表
        job_id=dict_detached["job_id"],  # detached runner 启动后返回、后续轮询持续使用的 job 标识
        job_status=dict_job_result["status"],  # detached runner 轮询得到的最终状态
        platform_probe=dict_readiness["platform_probe"],  # 平台名与 xpfm 是否可用的完整探测回包
        platform_upload=dict_readiness["platform_upload"],  # 平台上传结果
        hardware_probe=dict_readiness["hardware_probe"],  # 板卡型号、BDF 与设备指纹等硬件探测结果
        toolchain_probe=dict_readiness["toolchain_probe"],  # 通过报告保留的 v++/XRT/g++ 探测证据
        board_profile=dict_board_profile,  # 精简后的 platform/xpfm/target_part 摘要
        board_metadata=dict_board_metadata,  # board host 模板名与 top_function 等生成元数据
        board_status_marker=BOARD_STATUS_MARKER,  # host.cpp 成功输出必须包含的状态前缀
        uses_erie_remote_ssh=True,  # 说明整个远端执行链依赖 erie-remote-ssh helper
    )

    # 返回通过报告给统一写报告入口。
    return dict_result

# 按显式参数、缓存和目录契约顺序解析 board 平台选择。
def _resolve_board_platform_selection(
    args: argparse.Namespace,
    server: str,
    remote_workdir: str,
    selected_profile: dict[str, Any],
    directory_contract: dict[str, Any],
) -> dict[str, Any]:
    """
    解析本次 board 验收要使用的远端平台。

    :param args: CLI 参数，可能提供平台名或 xpfm 路径。
    :param server: 远端服务器名，用于读写平台选择缓存。
    :param remote_workdir: 远端 workspace 根目录。
    :param selected_profile: 已选 Vitis profile。
    :param directory_contract: 平台目录治理契约。
    :return: 包含 platform_name、remote_platform_root、remote_xpfm 的选择字典。
    """

    # CLI 显式平台选择优先，并写入服务器缓存。
    dict_explicit = _explicit_board_platform_selection(args, remote_workdir)  # 用户命令行指定的平台字段

    # 显式选择已经足够启动后续平台探测。
    if dict_explicit:

        # 缓存显式选择，下一次同服务器验收可以复用。
        set_board_platform_selection(server, dict_explicit)

        # 返回 CLI 指定的平台路径。
        return dict_explicit

    # 读取此前确认过的远端平台选择。
    dict_saved_selection = get_board_platform_selection(server)  # 本地缓存中的服务器平台选择

    # 缓存命中时需要把相对路径规整成远端绝对路径。
    if dict_saved_selection:

        # 复制缓存内容，避免规整路径时修改持久化对象。
        dict_normalized_saved = dict(dict_saved_selection)  # 归一化后的缓存平台选择

        # remote_platform_root 允许相对 workdir 记录。
        str_saved_root = str(dict_saved_selection.get("remote_platform_root") or "")  # 缓存中的平台根目录

        # remote_xpfm 同样按 workdir 补成可执行命令使用的路径。
        str_saved_xpfm = str(dict_saved_selection.get("remote_xpfm") or "")  # 缓存中的 xpfm 文件路径

        # 写回归一化后的平台根目录。
        dict_normalized_saved["remote_platform_root"] = _normalize_remote_platform_path(remote_workdir, str_saved_root)  # 归一化后的缓存平台根目录

        # 写回归一化后的 xpfm 路径。
        dict_normalized_saved["remote_xpfm"] = _normalize_remote_platform_path(remote_workdir, str_saved_xpfm)  # 归一化后的缓存 xpfm 路径

        # 返回缓存平台选择，保持用户已确认的远端路径。
        return dict_normalized_saved

    # profile 中的平台名优先于 target part 默认推断。
    str_platform_name = str(selected_profile.get("platform_name") or "").strip()  # profile 声明的平台名

    # 缺平台名时，从 target part 推出当前项目约定的平台名。
    if not str_platform_name:

        # U50/U55C target part 对应固定 Alveo 平台名。
        str_platform_name = _default_platform_name_for_part(str(selected_profile.get("target_part") or ""))  # target part 对应的默认平台名

    # 仍无法确定平台时交给上层生成阻断报告。
    if not str_platform_name:

        # 空选择表示当前阶段无法自动定位平台。
        return {}

    # 按目录契约生成远端平台根目录和 xpfm 路径。
    return _governed_remote_platform_selection(remote_workdir, str_platform_name, directory_contract)

# 从 CLI 参数中提取显式 board 平台选择。
def _explicit_board_platform_selection(args: argparse.Namespace, remote_workdir: str) -> dict[str, Any]:
    """
    读取命令行显式传入的平台字段。

    :param args: CLI 参数对象。
    :param remote_workdir: 远端 workspace 根目录，用于补齐相对路径。
    :return: 显式平台选择；没有任何平台字段时返回空字典。
    """

    # 显式字段只要出现一个，就认为用户正在覆盖平台选择。
    bool_has_explicit = any(  # 任一平台覆盖字段非空都视为用户显式指定
        str(getattr(args, str_field, "") or "").strip()  # 当前平台覆盖字段的文本值
        for str_field in ("platform_name", "remote_platform_root", "remote_xpfm")  # 允许显式覆盖的平台相关字段
    )  # CLI 是否提供了平台相关覆盖项

    # 没有显式字段时不影响缓存和契约推断。
    if not bool_has_explicit:

        # 空字典让调用方继续尝试缓存或默认平台名。
        return {}

    # 读取平台名，允许只提供路径而不提供名称。
    str_platform_name = str(getattr(args, "platform_name", "") or "").strip()  # CLI 指定的平台名称

    # CLI 平台根目录可以是相对 workdir 的路径。
    str_platform_root = _normalize_remote_platform_path(  # 规整后的远端平台根目录
        remote_workdir,  # 相对平台根目录要基于远端 workdir 规整
        str(getattr(args, "remote_platform_root", "") or "").strip(),  # CLI 提供的平台根目录文本
    )

    # CLI xpfm 路径同样规整为远端绝对路径。
    str_remote_xpfm = _normalize_remote_platform_path(  # 规整后的远端 xpfm 文件路径
        remote_workdir,  # 相对 xpfm 路径要基于远端 workdir 规整
        str(getattr(args, "remote_xpfm", "") or "").strip(),  # CLI 提供的 xpfm 路径文本
    )

    # 返回显式选择，source 保留既有报告语义。
    return {
        "platform_name": str_platform_name,
        "remote_platform_root": str_platform_root,
        "remote_xpfm": str_remote_xpfm,
        "source": "upload",
    }

# 把远端平台路径规整到 workdir 下的绝对 POSIX 路径。
def _normalize_remote_platform_path(remote_workdir: str, raw: str) -> str:
    """
    规整远端平台路径。

    :param remote_workdir: 远端 workspace 根目录。
    :param raw: CLI、缓存或配置中记录的原始路径。
    :return: 远端绝对 POSIX 路径；空输入返回空字符串。
    """

    # 去掉用户输入或缓存值两端空白。
    str_candidate_path = str(raw or "").strip()  # 待规整的平台路径文本

    # 空路径保持空字符串，避免拼出无意义 workdir。
    if not str_candidate_path:

        # 空字段交给后续平台探测或阻断报告处理。
        return ""

    # PurePosixPath 与远端 Linux 路径契约一致。
    path_candidate = PurePosixPath(str_candidate_path)  # POSIX 形式的平台候选路径

    # 绝对路径可以直接传给远端 shell。
    if path_candidate.is_absolute():

        # 返回原始绝对路径的规范 POSIX 字符串。
        return path_candidate.as_posix()

    # 相对路径按远端 workdir 补齐。
    return (PurePosixPath(remote_workdir) / path_candidate).as_posix()

# 根据 target part 推断项目治理约定的平台名。
def _default_platform_name_for_part(target_part: str) -> str:
    """
    从器件型号推断默认 Alveo 平台名。

    :param target_part: Vitis target part 或服务器 hint。
    :return: U50/U55C 对应的平台名；无法识别时返回空字符串。
    """

    # 统一大小写，兼容服务器 hint 的不同写法。
    str_normalized_part = str(target_part or "").strip().lower()  # 小写化后的器件型号

    # U55C 是当前 board acceptance 的主治理目标。
    if "u55c" in str_normalized_part:

        # 返回项目固定的 U55C 平台名。
        return "xilinx_u55c_gen3x16_xdma_3_202210_1"

    # U50 保留兼容旧验收路径。
    if "u50" in str_normalized_part:

        # 返回兼容旧 U50 验收流程的固定 xdma_5 平台名。
        return "xilinx_u50_gen3x16_xdma_5_202210_1"

    # 未识别器件型号时不猜测平台。
    return ""

# 把平台名映射到 remote_workdir 下受治理的 platforms/alveo 路径与 xpfm。
def _governed_remote_platform_selection(
    remote_workdir: str,
    platform_name: str,
    directory_contract: dict[str, Any],
) -> dict[str, Any]:
    """
    构造受目录治理约束的远端平台选择。

    :param remote_workdir: 远端 workspace 根目录。
    :param platform_name: Vitis 平台名称。
    :param directory_contract: 目录治理契约。
    :return: 平台名、远端平台根目录和 xpfm 路径。
    """

    # 平台 payload 固定放在远端项目根的 platforms/alveo 下。
    path_project_root = PurePosixPath(remote_workdir) / str(directory_contract["project_root_dirname"])  # 远端项目根路径

    # 契约模板中的平台名占位符由实际 platform_name 替换。
    str_root_template = str(directory_contract["platform_root_path_template"]).replace("<platform-name>", platform_name)  # 平台根目录模板

    # 生成远端平台根目录的 POSIX 字符串。
    str_remote_root = (path_project_root / PurePosixPath(str_root_template)).as_posix()  # 远端平台 payload 根目录

    # xpfm 默认位于平台根目录下并与平台名同名。
    str_remote_xpfm = (PurePosixPath(str_remote_root) / f"{platform_name}.xpfm").as_posix()  # 远端 xpfm 文件路径

    # 返回保持历史字段名的平台选择。
    return {
        "platform_name": platform_name,
        "remote_platform_root": str_remote_root,
        "remote_xpfm": str_remote_xpfm,
        "source": "upload",
    }

# 平台探测失败时，决定是否可以自动上传本地 U55C payload。
def _local_board_platform_upload_selection(
    remote_workdir: str,
    selected_profile: dict[str, Any],
    platform_probe: dict[str, Any],
    directory_contract: dict[str, Any],
) -> dict[str, Any]:
    """
    生成本地 U55C payload 的上传选择。

    :param remote_workdir: 远端 workspace 根目录。
    :param selected_profile: 当前 board profile。
    :param platform_probe: 平台探测结果，可能包含 suggested_platform_name。
    :param directory_contract: 平台目录治理契约。
    :return: 可自动上传的平台选择；非 U55C 返回空字典。
    """

    # 平台名优先来自 profile，其次来自探测建议，最后从 target part 推断。
    str_platform_name = _platform_name_for_upload_selection(selected_profile, platform_probe)  # 候选平台名

    # 只有治理内置 U55C payload 可以自动上传。
    if str_platform_name != U55C_PLATFORM_NAME:

        # 其他平台必须由用户提供远端路径或手工上传。
        return {}

    # 按目录契约生成默认远端落点。
    dict_selection = _governed_remote_platform_selection(remote_workdir, str_platform_name, directory_contract)  # 自动上传平台选择

    # 显式 remote_platform_root 优先于契约默认路径。
    if str(selected_profile.get("remote_platform_root") or "").strip():

        # 用户或 profile 指定的远端根目录需要保留。
        dict_selection["remote_platform_root"] = str(selected_profile["remote_platform_root"]).strip()  # profile 覆盖的平台根目录

    # 显式 remote_xpfm 优先于平台名默认推导。
    if str(selected_profile.get("remote_xpfm") or "").strip():

        # 用户或 profile 指定的 xpfm 文件路径需要保留。
        dict_selection["remote_xpfm"] = str(selected_profile["remote_xpfm"]).strip()  # profile 覆盖的 xpfm 路径

    # 返回可用于上传和缓存的平台选择。
    return dict_selection

# 从 profile 和平台探测结果中提取上传候选平台名。
def _platform_name_for_upload_selection(
    selected_profile: dict[str, Any],
    platform_probe: dict[str, Any],
) -> str:
    """
    选择平台上传流程使用的平台名。

    :param selected_profile: 当前 board profile。
    :param platform_probe: 平台探测结果。
    :return: 候选平台名，无法推断时为空字符串。
    """

    # profile 显式平台名优先。
    str_profile_platform = str(selected_profile.get("platform_name") or "").strip()  # profile 中声明的平台名

    # 探测器可能根据 target part 给出建议平台名。
    str_probe_platform = str(platform_probe.get("suggested_platform_name") or "").strip()  # 平台探测建议名

    # target part 是最后的默认平台名来源。
    str_part_platform = _default_platform_name_for_part(str(selected_profile.get("target_part") or ""))  # target part 推导的平台名

    # 返回第一个非空平台名。
    return str_profile_platform or str_probe_platform or str_part_platform

# 上传 U55C payload 需要的远端目录、上传和解压请求统一在这里执行。
def _upload_u55c_platform_requests(
    helper: "ErieHelper",
    settings: Path,
    server: str,
    remote_workdir: str,
    selection: dict[str, Any],
    path_archive_path: Path,
) -> dict[str, Any]:
    """
    执行 U55C payload 的远端目录准备、上传和解压验证。

    :param helper: 远端 helper，用于 request-command 和 request-upload。
    :param settings: erie-remote-ssh settings overlay。
    :param server: 目标服务器名。
    :param remote_workdir: 远端 workspace 根目录。
    :param selection: 平台名、远端根目录和 xpfm 路径。
    :param path_archive_path: 本地 payload 压缩包路径。
    :return: 远端归档、xpfm 和 request manifest 字典。
    """

    # 远端平台根目录来自治理选择。
    path_remote_root = PurePosixPath(str(selection["remote_platform_root"]))  # 远端平台 payload 解压目录

    # 压缩包上传到平台根目录的父目录，再在远端解压。
    path_remote_parent = path_remote_root.parent  # 远端平台父目录

    # 远端压缩包绝对路径用于解压命令。
    path_remote_archive = path_remote_parent / path_archive_path.name  # 远端 payload 压缩包路径

    # request-upload 使用相对 workdir 的远端路径。
    str_remote_archive_rel = _remote_relative_to_workdir(remote_workdir, path_remote_archive)  # 上传命令使用的相对压缩包路径

    # mkdir 请求也按远端 workdir 转相对路径。
    str_remote_parent_rel = _remote_relative_to_workdir(remote_workdir, path_remote_parent)  # 平台父目录相对路径

    # 先创建远端平台父目录。
    str_mkdir_command = f"mkdir -p {shlex.quote(str_remote_parent_rel)}"  # 远端 mkdir 命令

    # mkdir 通过 request 审批执行，保留 manifest。
    str_mkdir_request = helper.request_and_run(  # 记录创建远端平台父目录动作对应的 request manifest 路径
        settings,  # mkdir 动作用到的 server/project overlay
        server,  # 执行创建目录请求的目标服务器
        "command",  # 通过命令请求执行 mkdir
        str_mkdir_command,  # 远端创建平台父目录的 mkdir 命令
        "prepare governed remote board platform directory",  # request 记录中的用途标签
    )  # 创建远端平台父目录的请求记录

    # 上传本地归档到远端父目录。
    str_upload_request = helper.request_upload_and_run(  # 这里保留 tar.gz 平台包上传的 manifest 路径，稍后写进 requests 证据链
        settings,  # 上传平台包时 helper 读取 server/project 配置用的 overlay
        server,  # 真正接收 tar.gz 平台包的远端机器
        path_archive_path,  # 本地刚打好的 U55C 平台 tar.gz 路径
        str_remote_archive_rel,  # tar.gz 上传后在远端 workdir 下的相对落位
        "upload U55C platform payload",  # request 记录里区分这次平台包上传动作的用途标签
    )  # U55C payload 上传请求记录

    # 远端 xpfm 路径用于解压后的存在性验证。
    str_remote_xpfm = str(selection["remote_xpfm"])  # 解压后必须存在的 xpfm 文件

    # 解压命令确保目录存在、展开归档，并验证 xpfm 文件。
    str_extract_command = _u55c_payload_extract_command(path_remote_parent, path_remote_archive, str_remote_xpfm)  # 远端 payload 解压验证命令

    # 解压验证同样走 request-command，形成可审计记录。
    str_extract_request = helper.request_and_run(  # 记录解压归档并校验 xpfm 动作对应的 request manifest 路径
        settings,  # extract 校验动作用到的 server/project overlay
        server,  # 执行解压验证请求的目标服务器
        "command",  # 通过命令请求执行解压与校验
        str_extract_command,  # 远端解压归档并验证 xpfm 的命令
        "extract U55C platform payload",  # request 记录中的解压用途标签
    )  # 解压并验证 U55C payload 的请求记录

    # 返回上传链路需要复用的远端路径与 request manifest。
    return dict(
        remote_archive=path_remote_archive.as_posix(),
        remote_archive_relative=str_remote_archive_rel,
        remote_xpfm=str_remote_xpfm,
        requests=[str_mkdir_request, str_upload_request, str_extract_request],
    )

# 上传、解压并验证本地 U55C 平台 payload。
def _upload_local_board_platform_payload(
    helper: "ErieHelper",
    settings: Path,
    server: str,
    run_dir: Path,
    remote_workdir: str,
    selection: dict[str, Any],
    *,
    local_root: Path | None = None,
) -> dict[str, Any]:
    """
    将本地 U55C 平台 payload 上传到治理约束的远端位置。

    :param helper: 远端 helper，用于请求命令和上传文件。
    :param settings: erie-remote-ssh settings overlay。
    :param server: 目标服务器名。
    :param run_dir: 本地 run 目录。
    :param remote_workdir: 远端 workspace 根目录。
    :param selection: 平台名、远端根目录和 xpfm 路径。
    :param local_root: 可选的本地 payload 根目录覆盖项。
    :return: 上传结果和 request manifest 列表。
    """

    # 平台名决定是否允许使用内置 payload。
    str_platform_name = str(selection.get("platform_name") or "").strip()  # 待上传的平台名称

    # 非 U55C 平台没有受治理的本地固定来源。
    if str_platform_name != U55C_PLATFORM_NAME:

        # 返回 skipped 结果，调用方会继续生成阻断说明。
        return {
            "status": "skipped",
            "reason": "only the governed U55C payload has a fixed local dependency source",
            "selection": selection,
        }

    # 准备本地 tar.gz 归档并校验 payload 结构。
    dict_prepared_payload = prepare_local_u55c_platform_archive(  # 本地 U55C payload 归档准备结果
        run_dir / "platform-upload",  # 临时生成 tar.gz 归档的本地工作目录
        local_root=local_root,  # 用户可选覆盖的本地 payload 根目录
    )

    # 本地 payload 不完整时阻断远端上传。
    if dict_prepared_payload.get("status") != PASS_STATUS:

        # 返回本地 payload 校验细节，帮助用户修复平台包。
        return {
            "status": BLOCKED_BOARD_STATUS,
            "reason": "invalid_local_u55c_platform_payload",
            "local_payload": dict_prepared_payload,
            "selection": selection,
        }

    # 归档路径来自准备结果，用于后续 request-upload。
    path_archive_path = Path(str(dict_prepared_payload["archive_path"]))  # 本地 U55C payload 压缩包

    # U55C payload 的远端目录、上传和解压请求统一走治理 helper。
    dict_upload_result = _upload_u55c_platform_requests(  # 汇总 mkdir/upload/extract 三段请求后的远端落位结果
        helper,  # 负责 request-command 与 request-upload 的远端 helper
        settings,  # 上传链路使用的远端项目设置 overlay
        server,  # 接收并解压平台 payload 的目标服务器
        remote_workdir,  # 远端 workspace 根目录
        selection,  # 平台名与 xpfm 的远端落位选择
        path_archive_path,  # 本地准备好的 tar.gz payload 路径
    )

    # 上传成功后缓存平台选择，下一轮同服务器可直接复用。
    set_board_platform_selection(server, selection)

    # 汇总上传结果，保持原 JSON 字段名。
    return dict(
        status=PASS_STATUS,
        platform_name=str_platform_name,
        archive_path=str(path_archive_path),
        remote_archive=dict_upload_result["remote_archive"],
        remote_archive_relative=dict_upload_result["remote_archive_relative"],
        remote_platform_root=str(selection["remote_platform_root"]),
        remote_xpfm=dict_upload_result["remote_xpfm"],
        selection=selection,
        local_payload=dict_prepared_payload,
        requests=dict_upload_result["requests"],
    )

# 构造远端 U55C payload 解压和 xpfm 验证命令。
def _u55c_payload_extract_command(
    path_remote_parent: PurePosixPath,
    path_remote_archive: PurePosixPath,
    str_remote_xpfm: str,
) -> str:
    """
    生成远端平台 payload 解压命令。

    :param path_remote_parent: 远端平台父目录。
    :param path_remote_archive: 远端 payload 压缩包绝对路径。
    :param str_remote_xpfm: 解压后必须存在的 xpfm 文件路径。
    :return: 可交给远端 shell 的命令字符串。
    """

    # mkdir 使用绝对父目录，保证 tar 解压目标存在。
    str_prepare_parent = f"mkdir -p {shlex.quote(path_remote_parent.as_posix())}"  # 创建远端平台父目录命令

    # tar 命令把上传的 payload 解压到平台父目录。
    str_extract_archive = (
        f"tar -xzf {shlex.quote(path_remote_archive.as_posix())} "
        f"-C {shlex.quote(path_remote_parent.as_posix())}"
    )  # 解压 U55C payload 的远端命令片段

    # xpfm 校验确保平台文件真正落位。
    str_verify_xpfm = f"test -f {shlex.quote(str_remote_xpfm)}"  # xpfm 存在性校验命令

    # 使用 && 保持任一阶段失败都会传递非零退出码。
    return f"{str_prepare_parent} && {str_extract_archive} && {str_verify_xpfm}"

# 将远端绝对路径转换为相对 workspace 的 request 路径。
def _remote_relative_to_workdir(remote_workdir: str, remote_path: PurePosixPath) -> str:
    """
    生成 erie-remote-ssh request 接口使用的远端相对路径。

    :param remote_workdir: 远端 workspace 根目录。
    :param remote_path: 远端绝对 POSIX 路径。
    :return: 相对 workdir 的路径；不在 workdir 下时退化为去掉开头斜杠的路径。
    """

    # workdir 用 PurePosixPath 表达，避免本地 Windows 路径规则介入。
    path_workdir = PurePosixPath(remote_workdir)  # 远端 workspace 路径对象

    # 优先生成相对 workdir 的 request 路径。
    try:

        # 相对路径最符合 erie-remote-ssh 的远端 request 契约。
        return remote_path.relative_to(path_workdir).as_posix()

    # 平台路径不在 workdir 下时保留可传输的无根路径。
    except ValueError:

        # 去掉开头斜杠，避免 request 接口把绝对路径误解成非法远端目标。
        return remote_path.as_posix().lstrip("/")

# 创建平台缺失时的人工上传计划。
def _board_platform_upload_plan(
    run_dir: Path,
    server: str,
    remote_workdir: str,
    selected_profile: dict[str, Any],
    platform_probe: dict[str, Any],
    directory_contract: dict[str, Any],
) -> dict[str, Any]:
    """
    生成 board 平台缺失时的人类可执行上传计划。

    :param run_dir: 本地 run 目录，用于写出 request JSON。
    :param server: 目标服务器名。
    :param remote_workdir: 远端 workspace 根目录。
    :param selected_profile: 当前 board profile。
    :param platform_probe: 平台探测结果。
    :param directory_contract: 平台目录治理契约。
    :return: 平台上传计划；无法推断平台名时为空字典。
    """

    # 上传计划平台名与自动上传选择使用同一推断逻辑。
    str_platform_name = _platform_name_for_upload_selection(selected_profile, platform_probe)  # 上传计划面向的平台名

    # 没有平台名时无法给出具体路径和命令。
    if not str_platform_name:

        # 空计划让阻断报告只保留探测失败事实。
        return {}

    # 按目录契约计算推荐远端位置。
    dict_selection = _governed_remote_platform_selection(remote_workdir, str_platform_name, directory_contract)  # 推荐平台远端落点

    # 仅 U55C 能检查本地固定 payload，其它平台返回空本地校验结果。
    dict_local_payload = _local_payload_status_for_upload_plan(str_platform_name)  # 上传计划中的本地 payload 校验摘要

    # 人工步骤使用短句，报告给人类审查。
    list_recommended_steps = _board_platform_recommended_steps(server, str_platform_name, dict_selection)  # 平台人工上传步骤

    # 远程命令保留原工具路径和 request-command 形态。
    list_recommended_commands = _board_platform_recommended_commands(server, str_platform_name, dict_selection)  # 平台人工上传参考命令

    # 汇总上传计划主体。
    dict_upload_plan = dict(  # 平台缺失时写入报告的上传计划
        server=server,  # 需要补传平台包的目标服务器
        platform_name=str_platform_name,  # 远端应落位的平台目录名
        source="upload",  # 计划来源保持 upload 语义
        expected_local_directory=str_platform_name,  # 本地平台目录应与平台名一致
        local_payload=dict_local_payload,  # 本地固定 payload 的完整性摘要
        remote_platform_root=dict_selection["remote_platform_root"],  # 远端平台根目录落位路径
        remote_xpfm=dict_selection["remote_xpfm"],  # 远端必须存在的 xpfm 文件路径
        recommended_steps=list_recommended_steps,  # 人工补传时建议执行的步骤
        recommended_commands=list_recommended_commands,  # 人工补传时可直接参考的命令
    )

    # 计划写入 run 目录，便于用户按文件执行和审查。
    path_request = run_dir / "remote_board_platform_request.json"  # 平台上传计划 JSON 路径

    # 写出机器可读上传计划。
    _write_json(path_request, dict_upload_plan)

    # 报告内记录计划文件路径。
    dict_upload_plan["request_path"] = str(path_request)  # 上传计划 JSON 文件路径

    # 返回阻断报告引用的上传计划。
    return dict_upload_plan

# 只对 U55C 上传计划附带本地 payload 校验摘要。
def _local_payload_status_for_upload_plan(str_platform_name: str) -> dict[str, Any]:
    """
    获取上传计划中的本地 payload 状态。

    :param str_platform_name: 平台名称。
    :return: 本地 payload 校验结果；非 U55C 返回空字典。
    """

    # 非 U55C 平台没有本仓库固定 payload 根目录。
    if str_platform_name != U55C_PLATFORM_NAME:

        # 空字典表示无法自动验证本地 payload。
        return {}

    # 校验本地默认 U55C payload，帮助用户提前发现目录缺失。
    return validate_local_board_platform_payload(
        default_local_u55c_payload_root(),
        expected_platform_name=U55C_PLATFORM_NAME,
    )

# 生成人类可读的平台补齐步骤。
def _board_platform_recommended_steps(
    server: str,
    str_platform_name: str,
    dict_selection: dict[str, Any],
) -> list[str]:
    """
    生成平台上传计划中的说明步骤。

    :param server: 目标服务器名。
    :param str_platform_name: 平台名称。
    :param dict_selection: 推荐远端平台选择。
    :return: 人类可执行步骤列表。
    """

    # rerun 命令单独拼接，避免推荐步骤过长难读。
    str_rerun_command = (
        "rerun scripts/python/remote/remote_vitis_acceptance.py --mode board "
        f"--server {server} --platform-name {str_platform_name} "
        f"--remote-platform-root {dict_selection['remote_platform_root']} "
        f"--remote-xpfm {dict_selection['remote_xpfm']}"
    )  # 上传完成后的 board mode 重跑命令

    # 返回原有四步说明，只拆分构造方式。
    return [
        f"tar the local platform directory {str_platform_name}/ into a single archive",
        f"upload the archive to {server} under {dict_selection['remote_platform_root']}",
        f"extract the archive so that {dict_selection['remote_xpfm']} exists on the remote host",
        str_rerun_command,
    ]

# 生成 erie-remote-ssh 的参考 request 命令。
def _board_platform_recommended_commands(
    server: str,
    str_platform_name: str,
    dict_selection: dict[str, Any],
) -> list[str]:
    """
    生成平台上传计划中的参考命令。

    :param server: 目标服务器名。
    :param str_platform_name: 平台名称。
    :param dict_selection: 推荐远端平台选择。
    :return: request-upload 和 request-command 两条参考命令。
    """

    # request-upload 命令保留原相对远端路径。
    str_upload_command = (
        "python %CODEX_HOME%/skills/erie-remote-ssh/scripts/remote_ssh.py request-upload "
        f"--settings <erie-settings.json> --server {server} "
        "--local <local-platform-archive> "
        f"--remote erie-hls-generator/platforms/alveo/{str_platform_name}.tar.gz "
        "--reason \"upload U55C platform payload\""
    )  # 平台归档上传参考命令

    # 解压命令中的远端平台根目录必须经过 shell quote。
    str_extract_inner = (
        f"mkdir -p {shlex.quote(dict_selection['remote_platform_root'])} && "
        f"tar -xzf erie-hls-generator/platforms/alveo/{str_platform_name}.tar.gz "
        f"-C {shlex.quote(dict_selection['remote_platform_root'])} --strip-components=1"
    )  # 远端平台归档解压命令体

    # request-command 包装 bash -lc 解压命令。
    str_extract_command = (
        "python %CODEX_HOME%/skills/erie-remote-ssh/scripts/remote_ssh.py request-command "
        f"--settings <erie-settings.json> --server {server} "
        "--reason \"extract U55C platform payload\" "
        f"-- bash -lc \"{str_extract_inner}\""
    )  # 平台归档解压参考命令

    # 返回两条可复制执行的参考命令。
    return [str_upload_command, str_extract_command]

# 生成 board 远端执行包。
def _create_board_package(run_dir: Path, artifact_dir: Path, *, example_spec: str) -> tuple[Path, dict[str, Any]]:
    """
    打包 board host、runner 和 HLS 资产。

    :param run_dir: 本地 run 目录。
    :param artifact_dir: HLS kernel 资产目录。
    :param example_spec: 样例规格名称。
    :return: board 执行包路径和 host 元数据。
    """

    # 打包前先固定 top function 和 host_template，避免 host.cpp 与 runner 读取不一致。
    dict_metadata = _board_metadata_for_spec(example_spec)  # 当前样例对应的 board host 元数据

    # top function 会写入 host.cpp 和 runner 环境变量。
    str_top_function = str(dict_metadata["top_function"])  # HLS kernel 顶层函数名

    # host 模板决定动态 host 的输入输出布局。
    str_host_template = str(dict_metadata["host_template"])  # board host 模板名称

    # 根据样例 mock vector 和模板类型生成可在 XRT 上校验 kernel 输出的 host.cpp。
    str_host_source = _render_board_host(example_spec, str_top_function, str_host_template)  # 远端 host-run 使用的 C++ 校验程序源码

    # board 目录只保存 host.cpp。
    path_board_dir = run_dir / "board"  # board host 输出目录

    # 创建 board host 输出目录。
    path_board_dir.mkdir(parents=True, exist_ok=True)

    # host.cpp 随包上传到远端。
    path_host = path_board_dir / "host.cpp"  # 本地 board host 源码路径

    # 写入 host.cpp，保持 LF 换行。
    path_host.write_text(str_host_source, encoding="utf-8", newline="\n")

    # runner 脚本位于 run 根目录，远端解包后直接执行。
    path_runner = run_dir / "run_board_validation.sh"  # board 远端 runner 脚本路径

    # 写入 runner 脚本。
    path_runner.write_text(_board_runner_script(str_top_function), encoding="utf-8", newline="\n")

    # board 执行包包含 HLS 资产、host.cpp 和 runner。
    path_package = run_dir / "board_artifacts.tar.gz"  # board 远端执行压缩包路径

    # 使用 tar.gz 保持与现有远端解包命令兼容。
    with tarfile.open(path_package, "w:gz") as file_tar:

        # 按路径排序保证归档内容顺序稳定。
        for path_artifact in sorted(artifact_dir.rglob("*")):

            # 只打包文件，目录由 tar 自动表达。
            if path_artifact.is_file():

                # kernel 资产统一放到远端 artifacts/ 下。
                file_tar.add(path_artifact, arcname=Path("artifacts") / path_artifact.relative_to(artifact_dir))

        # 添加 board host 源码。
        file_tar.add(path_host, arcname=BOARD_HOST_ARCNAME)

        # 添加远端 runner 脚本。
        file_tar.add(path_runner, arcname="run_board_validation.sh")

    # 返回包路径和元数据给远端 job 阶段。
    return path_package, dict_metadata

# 读取样例规格中的 board host 元数据。
def _board_metadata_for_spec(example_spec: str) -> dict[str, Any]:
    """
    从样例规格中提取 board acceptance 元数据。

    :param example_spec: 样例规格名称。
    :return: example_spec、top_function、host_template 和 profile。
    :raises RemoteAcceptanceError: 样例未声明为 board-runnable 时抛出。
    """

    # 样例规格由 Vitis mode 的加载函数统一解析。
    dict_spec = _load_example_spec(example_spec)  # HLS 示例规格

    # board acceptance 配置决定 host 模板和 profile。
    dict_board_config = board_acceptance_config(dict_spec)  # 样例的 board acceptance 配置

    # 只有声明为 board-runnable 的样例可以进入真实板级验收。
    if str(dict_board_config.get("profile") or "").strip() != BOARD_RUNNABLE_PROFILE:

        # 阻止非 board 样例生成误导性的 host-run 报告。
        raise RemoteAcceptanceError(f"> ERR: [Python] Example spec {example_spec} is not declared board-runnable.")

    # top function 默认回退到样例名，最后才用 kernel。
    str_top_function = str(  # board host 调用的 kernel 函数名
        dict_spec.get("interfaces", {}).get("top_function")  # 样例显式声明的 top function
        or dict_spec.get("name")  # 样例名作为兼容 fallback
        or "kernel"  # 最后的兜底 kernel 名
    )

    # 返回 host 渲染和报告需要的元数据。
    return {
        "example_spec": example_spec,
        "top_function": str_top_function,
        "host_template": str(dict_board_config.get("host_template") or "").strip(),
        "profile": str(dict_board_config.get("profile") or ""),
    }

# 根据模板名渲染 board host 源码。
def _render_board_host(example_spec: str, top_function: str, template_name: str) -> str:
    """
    渲染 board host C++ 源码。

    :param example_spec: 样例规格名称。
    :param top_function: HLS kernel 顶层函数名。
    :param template_name: board host 模板名称。
    :return: host.cpp 源码文本。
    :raises RemoteAcceptanceError: 模板未完全替换或动态模板上下文无效时抛出。
    """

    # 样例规格提供 mock vector 和接口参数表。
    dict_spec = _load_example_spec(example_spec)  # host 渲染使用的样例规格

    # 内置动态模板直接由 mock vector 驱动生成。
    if template_name in {"unary_memory_host", "binary_memory_host", "matrix_memory_host", "wrapper_unary_memory_host"}:

        # 返回动态 host 源码。
        return _render_vector_driven_board_host(dict_spec, top_function, template_name)

    # 其它模板从 assets host 模板目录读取。
    path_template = resolve_host_template_path(SKILL_ROOT, template_name)  # 静态 host 模板路径

    # 读取模板文本。
    str_template_text = path_template.read_text(encoding="utf-8")  # 静态 host 模板原文

    # 替换 top function 占位符。
    str_rendered_text = str_template_text.replace("{{TOP_FUNCTION}}", top_function)  # 已替换 kernel 名称的 host 源码

    # 未替换干净说明模板契约不完整。
    if "{{TOP_FUNCTION}}" in str_rendered_text:

        # 阻止生成带占位符的 host.cpp。
        raise RemoteAcceptanceError(
            f"> ERR: [Python] Board host template {template_name!r} was not rendered completely for {example_spec}."
        )

    # 返回静态模板渲染结果。
    return str_rendered_text

# 渲染由 mock vector 驱动的内置 board host。
def _render_vector_driven_board_host(spec: dict[str, Any], top_function: str, template_name: str) -> str:
    """
    根据 mock vector 渲染内置 board host。

    :param spec: HLS 样例规格。
    :param top_function: HLS kernel 顶层函数名。
    :param template_name: 内置 board host 模板名称。
    :return: host.cpp 源码文本。
    :raises RemoteAcceptanceError: 模板不支持或样例缺少必须的 mock vector / 参数时抛出。
    """

    # 先整理 mock vector、期望输出和接口参数表。
    dict_host_context = _vector_host_context(spec, template_name)  # 动态 host 渲染上下文

    # 一元 memory host 覆盖普通 kernel 和 wrapper-backed kernel。
    if template_name in {"unary_memory_host", "wrapper_unary_memory_host"}:

        # 返回一输入一输出 memory host。
        return _render_unary_memory_host(dict_host_context, top_function, template_name)

    # 二元 memory host 用于 input_a/input_b/output 接口。
    if template_name == "binary_memory_host":

        # 返回双输入 memory host。
        return _render_binary_memory_host(dict_host_context, top_function)

    # 矩阵 memory host 用 rows/cols 驱动 kernel 调用。
    if template_name == "matrix_memory_host":

        # 返回矩阵 memory host。
        return _render_matrix_memory_host(dict_host_context, top_function)

    # 不支持的动态模板必须显式失败。
    raise RemoteAcceptanceError(f"> ERR: [Python] Unsupported dynamic board host template {template_name!r}.")

# 提取动态 host 渲染所需的 mock vector 上下文。
def _vector_host_context(spec: dict[str, Any], template_name: str) -> dict[str, Any]:
    """
    从样例规格中提取动态 host 上下文。

    :param spec: HLS 样例规格。
    :param template_name: 内置 board host 模板名称，用于错误消息。
    :return: 包含 inputs、expected_outputs、arguments 和 spec_name 的字典。
    :raises RemoteAcceptanceError: 样例未提供 mock_vectors 时抛出。
    """

    # mock_vectors 是动态 host 的输入数据来源。
    list_vectors = (spec.get("workflow") or {}).get("mock_vectors")  # 样例 mock vector 列表

    # 缺少 mock vector 时无法生成自校验 host。
    if not isinstance(list_vectors, list) or not list_vectors:

        # 抛出带前缀的错误，满足 current-project CLI 错误文本要求。
        raise RemoteAcceptanceError(
            f"> ERR: [Python] Board host template {template_name!r} requires "
            f"workflow.mock_vectors in {spec.get('name')!r}."
        )

    # 仅取首个 mock vector 作为板级 smoke case。
    dict_case = list_vectors[0]  # board host 使用的首个 mock vector

    # 非字典 case 退化为空输入输出，后续参数检查会阻断。
    dict_inputs = dict_case.get("inputs", {}) if isinstance(dict_case, dict) else {}  # host 输入向量配置

    # 期望输出用于 host 端逐元素比较。
    dict_expected_outputs = (
        dict_case.get("expected_outputs", {}) if isinstance(dict_case, dict) else {}  # 非字典 case 退化为空输出期望
    )  # host 输出期望值配置

    # 接口参数表用于确认 host 模板所需参数存在。
    dict_arguments = {
        str(dict_item.get("name")): dict_item  # 参数名作为索引键，保留完整参数描述
        for dict_item in spec.get("interfaces", {}).get("arguments", [])  # 样例声明的 kernel 参数表
        if isinstance(dict_item, dict) and dict_item.get("name")  # 仅保留具名参数项
    }  # HLS kernel 参数名到参数描述的映射

    # 返回动态 host 渲染上下文。
    return {
        "inputs": dict_inputs,
        "expected_outputs": dict_expected_outputs,
        "arguments": dict_arguments,
        "spec_name": spec.get("name"),
    }

# 确认动态 host 所需的接口参数齐全。
def _require_host_arguments(
    dict_context: dict[str, Any],
    set_required_names: set[str],
    template_name: str,
) -> None:
    """
    校验动态 host 模板所需接口参数。

    :param dict_context: `_vector_host_context` 返回的上下文。
    :param set_required_names: 模板必须存在的参数名集合。
    :param template_name: 当前模板名，用于错误消息。
    :return: 无业务返回；参数缺失时抛出异常。
    """

    # 参数表来自样例 interfaces.arguments。
    dict_arguments = dict_context["arguments"]  # kernel 参数名映射

    # 任一必需参数缺失都会让生成的 host 调用 ABI 错位。
    if not set_required_names.issubset(dict_arguments):

        # 用明确错误阻止生成无法编译或调用的 host。
        raise RemoteAcceptanceError(
            f"> ERR: [Python] Board host template {template_name!r} requires "
            f"arguments {sorted(set_required_names)!r} in {dict_context['spec_name']!r}."
        )

# 渲染一输入一输出的 memory host。
def _render_unary_memory_host(dict_context: dict[str, Any], top_function: str, template_name: str) -> str:
    """
    渲染 unary 或 wrapper unary board host。

    :param dict_context: 动态 host 上下文。
    :param top_function: HLS kernel 顶层函数名。
    :param template_name: unary 模板名。
    :return: host.cpp 源码文本。
    """

    # unary host 必须有 input/output 两个 memory 参数。
    _require_host_arguments(dict_context, {"input", "output"}, template_name)

    # 输入向量驱动 host 写入输入 BO。
    list_input_values = [int(item) for item in dict_context["inputs"].get("input", [])]  # unary 输入数据

    # 期望输出用于 host 端校验。
    list_expected_values = [int(item) for item in dict_context["expected_outputs"].get("output", [])]  # unary 期望输出

    # length 允许样例显式覆盖，否则使用输入向量长度。
    int_length = int(dict_context["inputs"].get("length", len(list_input_values)))  # unary kernel 处理长度

    # wrapper 模板保留原说明注释。
    str_wrapper_comment = (
        "  // Wrapper-backed board validation keeps a memory-facing host "
        "while the kernel preserves internal stream staging.\n"
        if template_name == "wrapper_unary_memory_host"  # 仅 wrapper 模板需要额外解释内部 stream staging
        else ""  # 非 wrapper 模板不额外插入说明注释
    )  # wrapper host 的 C++ 说明注释

    # 返回拼接后的 C++ host 源码。
    return (
        "#include <algorithm>\n"
        "#include <cstdint>\n"
        "#include <cstdlib>\n"
        "#include <iostream>\n"
        "#include <vector>\n"
        "\n"
        "#include <xrt/xrt_bo.h>\n"
        "#include <xrt/xrt_device.h>\n"
        "#include <xrt/xrt_kernel.h>\n"
        "\n"
        "int main(int argc, char** argv) {\n"
        "  if (argc < 2) {\n"
        "    std::cerr << \"usage: host <xclbin>\\n\";\n"
        "    return 2;\n"
        "  }\n"
        "  const std::string xclbin_path = argv[1];\n"
        f"  const int length = {int_length};\n"
        f"{str_wrapper_comment}"
        f"  std::vector<std::uint32_t> input = {{{_vector_literal(list_input_values)}}};\n"
        "  std::vector<std::uint32_t> output(length, 0U);\n"
        f"  std::vector<std::uint32_t> expected = {{{_vector_literal(list_expected_values)}}};\n"
        "\n"
        "  auto device = xrt::device(0);\n"
        "  auto uuid = device.load_xclbin(xclbin_path);\n"
        f"  auto kernel = xrt::kernel(device, uuid, \"{top_function}\");\n"
        "  auto in_bo = xrt::bo(device, sizeof(std::uint32_t) * input.size(), kernel.group_id(0));\n"
        "  auto out_bo = xrt::bo(device, sizeof(std::uint32_t) * output.size(), kernel.group_id(1));\n"
        "  auto in_map = in_bo.map<std::uint32_t*>();\n"
        "  auto out_map = out_bo.map<std::uint32_t*>();\n"
        "  std::copy(input.begin(), input.end(), in_map);\n"
        "  std::fill(out_map, out_map + output.size(), 0U);\n"
        "  in_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);\n"
        "  out_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);\n"
        "\n"
        "  auto run = kernel(in_bo, out_bo, length);\n"
        "  run.wait();\n"
        "  out_bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE);\n"
        "  std::copy(out_map, out_map + output.size(), output.begin());\n"
        "\n"
        "  bool pass = true;\n"
        "  for (int i = 0; i < length; ++i) {\n"
        "    if (output[i] != expected[i]) {\n"
        "      pass = false;\n"
        "      break;\n"
        "    }\n"
        "  }\n"
        "  std::cout << \"HLS_BOARD_STATUS \" << (pass ? \"passed\" : \"failed\") << \"\\n\";\n"
        "  return pass ? 0 : 1;\n"
        "}\n"
    )

# 渲染双输入 memory host。
def _render_binary_memory_host(dict_context: dict[str, Any], top_function: str) -> str:
    """
    渲染 binary memory board host。

    :param dict_context: 动态 host 上下文。
    :param top_function: HLS kernel 顶层函数名。
    :return: host.cpp 源码文本。
    """

    # binary host 必须包含 input_a、input_b 和 output。
    _require_host_arguments(dict_context, {"input_a", "input_b", "output"}, "binary_memory_host")

    # input_a 向量写入第一个输入 BO。
    list_input_a_values = [int(item) for item in dict_context["inputs"].get("input_a", [])]  # A 路输入数据

    # input_b 向量写入第二个输入 BO。
    list_input_b_values = [int(item) for item in dict_context["inputs"].get("input_b", [])]  # binary host 的第二路输入数据

    # 期望输出用于逐元素比较。
    list_expected_values = [int(item) for item in dict_context["expected_outputs"].get("output", [])]  # binary host 逐元素比较的期望输出

    # length 默认取三组向量的最小长度，保持旧逻辑。
    int_length = int(  # binary host 只比较三组向量共同覆盖的最短长度
        dict_context["inputs"].get(  # 优先尊重样例显式给出的 length
            "length",  # 样例里可显式覆盖比较长度的字段名
            min(len(list_input_a_values), len(list_input_b_values), len(list_expected_values)),  # 默认比较三组向量共同覆盖的最短长度
        )
    )  # binary host 统一使用的元素长度

    # 下面拼出三缓冲 binary host.cpp，保持 in_a/in_b/out 的 XRT BO 顺序不变。
    return (
        "#include <algorithm>\n"
        "#include <cstdint>\n"
        "#include <cstdlib>\n"
        "#include <iostream>\n"
        "#include <vector>\n"
        "\n"
        "#include <xrt/xrt_bo.h>\n"
        "#include <xrt/xrt_device.h>\n"
        "#include <xrt/xrt_kernel.h>\n"
        "\n"
        "int main(int argc, char** argv) {\n"
        "  if (argc < 2) {\n"
        "    std::cerr << \"usage: host <xclbin>\\n\";\n"
        "    return 2;\n"
        "  }\n"
        "  const std::string xclbin_path = argv[1];\n"
        f"  const int length = {int_length};\n"
        f"  std::vector<std::uint32_t> input_a = {{{_vector_literal(list_input_a_values)}}};\n"
        f"  std::vector<std::uint32_t> input_b = {{{_vector_literal(list_input_b_values)}}};\n"
        "  std::vector<std::uint32_t> output(length, 0U);\n"
        f"  std::vector<std::uint32_t> expected = {{{_vector_literal(list_expected_values)}}};\n"
        "\n"
        "  auto device = xrt::device(0);\n"
        "  auto uuid = device.load_xclbin(xclbin_path);\n"
        f"  auto kernel = xrt::kernel(device, uuid, \"{top_function}\");\n"
        "  auto in_a_bo = xrt::bo(device, sizeof(std::uint32_t) * input_a.size(), kernel.group_id(0));\n"
        "  auto in_b_bo = xrt::bo(device, sizeof(std::uint32_t) * input_b.size(), kernel.group_id(1));\n"
        "  auto out_bo = xrt::bo(device, sizeof(std::uint32_t) * output.size(), kernel.group_id(2));\n"
        "  auto in_a_map = in_a_bo.map<std::uint32_t*>();\n"
        "  auto in_b_map = in_b_bo.map<std::uint32_t*>();\n"
        "  auto out_map = out_bo.map<std::uint32_t*>();\n"
        "  std::copy(input_a.begin(), input_a.end(), in_a_map);\n"
        "  std::copy(input_b.begin(), input_b.end(), in_b_map);\n"
        "  std::fill(out_map, out_map + output.size(), 0U);\n"
        "  in_a_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);\n"
        "  in_b_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);\n"
        "  out_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);\n"
        "\n"
        "  auto run = kernel(in_a_bo, in_b_bo, out_bo, length);\n"
        "  run.wait();\n"
        "  out_bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE);\n"
        "  std::copy(out_map, out_map + output.size(), output.begin());\n"
        "\n"
        "  bool pass = true;\n"
        "  for (int i = 0; i < length; ++i) {\n"
        "    if (output[i] != expected[i]) {\n"
        "      pass = false;\n"
        "      break;\n"
        "    }\n"
        "  }\n"
        "  std::cout << \"HLS_BOARD_STATUS \" << (pass ? \"passed\" : \"failed\") << \"\\n\";\n"
        "  return pass ? 0 : 1;\n"
        "}\n"
    )

# 渲染矩阵输入 memory host。
def _render_matrix_memory_host(dict_context: dict[str, Any], top_function: str) -> str:
    """
    渲染 matrix memory board host。

    :param dict_context: 动态 host 上下文。
    :param top_function: HLS kernel 顶层函数名。
    :return: host.cpp 源码文本。
    """

    # matrix host 必须包含 input/output memory 参数。
    _require_host_arguments(dict_context, {"input", "output"}, "matrix_memory_host")

    # 扁平化矩阵输入按 row-major 顺序传入 host。
    list_input_values = [int(item) for item in dict_context["inputs"].get("input", [])]  # 矩阵输入扁平数据

    # 期望输出同样使用扁平向量表达。
    list_expected_values = [int(item) for item in dict_context["expected_outputs"].get("output", [])]  # 矩阵期望输出

    # rows 默认 1，兼容向量退化为单行矩阵。
    int_rows = int(dict_context["inputs"].get("rows", 1))  # 矩阵行数

    # cols 默认输入向量长度。
    int_cols = int(dict_context["inputs"].get("cols", len(list_input_values)))  # 矩阵列数

    # host 校验长度等于 rows * cols。
    int_length = int_rows * int_cols  # 矩阵扁平化元素总数

    # 下面拼出 rows/cols 驱动的 matrix host.cpp，保持矩阵扁平化约定不变。
    return (
        "#include <algorithm>\n"
        "#include <cstdint>\n"
        "#include <cstdlib>\n"
        "#include <iostream>\n"
        "#include <vector>\n"
        "\n"
        "#include <xrt/xrt_bo.h>\n"
        "#include <xrt/xrt_device.h>\n"
        "#include <xrt/xrt_kernel.h>\n"
        "\n"
        "int main(int argc, char** argv) {\n"
        "  if (argc < 2) {\n"
        "    std::cerr << \"usage: host <xclbin>\\n\";\n"
        "    return 2;\n"
        "  }\n"
        "  const std::string xclbin_path = argv[1];\n"
        f"  const int rows = {int_rows};\n"
        f"  const int cols = {int_cols};\n"
        "  const int length = rows * cols;\n"
        f"  std::vector<std::uint32_t> input = {{{_vector_literal(list_input_values)}}};\n"
        "  std::vector<std::uint32_t> output(length, 0U);\n"
        f"  std::vector<std::uint32_t> expected = {{{_vector_literal(list_expected_values)}}};\n"
        "\n"
        "  auto device = xrt::device(0);\n"
        "  auto uuid = device.load_xclbin(xclbin_path);\n"
        f"  auto kernel = xrt::kernel(device, uuid, \"{top_function}\");\n"
        "  auto in_bo = xrt::bo(device, sizeof(std::uint32_t) * input.size(), kernel.group_id(0));\n"
        "  auto out_bo = xrt::bo(device, sizeof(std::uint32_t) * output.size(), kernel.group_id(1));\n"
        "  auto in_map = in_bo.map<std::uint32_t*>();\n"
        "  auto out_map = out_bo.map<std::uint32_t*>();\n"
        "  std::copy(input.begin(), input.end(), in_map);\n"
        "  std::fill(out_map, out_map + output.size(), 0U);\n"
        "  in_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);\n"
        "  out_bo.sync(XCL_BO_SYNC_BO_TO_DEVICE);\n"
        "\n"
        "  auto run = kernel(in_bo, out_bo, rows, cols);\n"
        "  run.wait();\n"
        "  out_bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE);\n"
        "  std::copy(out_map, out_map + output.size(), output.begin());\n"
        "\n"
        "  bool pass = true;\n"
        "  for (int i = 0; i < length; ++i) {\n"
        "    if (output[i] != expected[i]) {\n"
        "      pass = false;\n"
        "      break;\n"
        "    }\n"
        "  }\n"
        "  std::cout << \"HLS_BOARD_STATUS \" << (pass ? \"passed\" : \"failed\") << \"\\n\";\n"
        "  return pass ? 0 : 1;\n"
        "}\n"
    )

# 把整数向量渲染成 C++ initializer list。
def _vector_literal(values: list[int]) -> str:
    """
    渲染 C++ uint32_t 向量字面量。

    :param values: Python 整数列表。
    :return: 逗号分隔的 C++ 初始化值；空列表返回 0。
    """

    # 空向量保留旧行为，生成单个 0 以保持 C++ initializer 合法。
    if not values:

        # 返回占位 0。
        return "0"

    # 非空向量逐项转成 int 字符串。
    return ", ".join(str(int(item)) for item in values)

# 拼装远端 board runner 的 shell 命令。
def _remote_board_command(remote_dir: str, profile: dict[str, Any], metadata: dict[str, Any]) -> str:
    """
    生成远端 board 验收命令。

    :param remote_dir: 远端 active run 绝对路径。
    :param profile: 已选 board profile。
    :param metadata: board host 元数据。
    :return: 可交给远端后台 job 执行的 shell 命令。
    """

    # settings script 负责在远端 runner 中加载 Vitis 环境变量。
    str_settings_script = shlex.quote(str(profile["settings_script"]))  # HLS_SETTINGS_SCRIPT 环境变量值

    # Vitis platform 优先使用 platform_spec，其次使用 xpfm，再退回平台名。
    str_platform_value = str(  # Vitis --platform 参数原始值
        profile.get("platform_spec")  # 用户或配置显式声明的 platform 规格
        or profile.get("remote_xpfm")  # 没有 platform_spec 时回退到 xpfm
        or profile["platform_name"]  # 最后才使用平台名称
    )

    # platform 参数进入 shell 前必须 quote。
    str_platform_name = shlex.quote(str_platform_value)  # runner 环境里传给 v++ --platform 的 shell-quoted 平台值

    # target part 作为 runner 环境变量传递。
    str_target_part = shlex.quote(str(profile.get("target_part", "")))  # runner 环境里传给脚本的 shell-quoted target part

    # metadata 中的 top function 同时驱动 v++ -k 参数和 XRT kernel 构造。
    str_top_function = shlex.quote(str(metadata["top_function"]))  # runner 中的 HLS_TOP_FUNCTION 环境变量值

    # XRT setup script 可选，runner 内部会判断文件是否存在。
    str_xrt_setup_script = str(profile.get("xrt_setup_script") or "").strip()  # XRT setup 脚本路径

    # setup script 作为环境变量传入 shell。
    str_xrt_setup_arg = shlex.quote(str_xrt_setup_script)  # shell quote 后的 XRT setup 路径

    # v++ 工具路径允许 profile 覆盖。
    str_vpp_tool = shlex.quote(str(profile.get("vpp_path") or "v++"))  # shell quote 后的 v++ 工具

    # XRT 探测工具路径允许 profile 覆盖。
    str_xrt_tool = shlex.quote(str(profile.get("xrt_tool_path") or ""))  # runner 用于 XRT 探测的可选工具路径

    # active run 目录用于解包和执行 runner。
    str_remote_dir = shlex.quote(remote_dir)  # shell quote 后的远端 active run 目录

    # 环境变量片段集中拼接，避免远端命令过长难读。
    str_env_args = (
        f"HLS_SETTINGS_SCRIPT={str_settings_script} "
        f"HLS_PLATFORM_NAME={str_platform_name} "
        f"HLS_TARGET_PART={str_target_part} "
        f"HLS_TOP_FUNCTION={str_top_function} "
        f"HLS_XRT_SETUP_SCRIPT={str_xrt_setup_arg} "
        f"HLS_VPP_TOOL={str_vpp_tool} "
        f"HLS_XRT_TOOL={str_xrt_tool}"
    )  # board runner 需要的环境变量片段

    # 返回完整远端命令。
    return (
        f"cd {str_remote_dir} && "
        "base64 -d hls_artifacts.tar.gz.b64 > board_artifacts.tar.gz && "
        "tar -xzf board_artifacts.tar.gz && "
        f"{str_env_args} bash run_board_validation.sh"
    )

# 生成远端 active run 内执行的 board 验收脚本。
def _board_runner_script(top_function: str) -> str:
    """
    生成 board compile/link/host-run 的 bash 脚本。

    :param top_function: HLS kernel 顶层函数名；实际脚本通过 HLS_TOP_FUNCTION 环境变量读取。
    :return: bash 脚本文本。
    """

    # 返回脚本文本，远端执行前会随 board 包一起上传。
    return f"""#!/usr/bin/env bash
set -euo pipefail
: "${{HLS_SETTINGS_SCRIPT:?}}"
: "${{HLS_PLATFORM_NAME:?}}"
: "${{HLS_TOP_FUNCTION:?}}"
HLS_TARGET_PART="${{HLS_TARGET_PART:-}}"
HLS_XRT_SETUP_SCRIPT="${{HLS_XRT_SETUP_SCRIPT:-}}"
HLS_VPP_TOOL="${{HLS_VPP_TOOL:-v++}}"
HLS_XRT_TOOL="${{HLS_XRT_TOOL:-}}"
source "$HLS_SETTINGS_SCRIPT" >/dev/null 2>&1 || true
if [ -n "$HLS_XRT_SETUP_SCRIPT" ] && [ -f "$HLS_XRT_SETUP_SCRIPT" ]; then
  source "$HLS_XRT_SETUP_SCRIPT" >/dev/null 2>&1 || true
fi
if ! command -v "$HLS_VPP_TOOL" >/dev/null 2>&1 && [ ! -x "$HLS_VPP_TOOL" ]; then
  echo "{BOARD_STATUS_MARKER} blocked_vpp"
  exit 45
fi
if ! command -v g++ >/dev/null 2>&1; then
  echo "{BOARD_STATUS_MARKER} blocked_gpp"
  exit 46
fi
if ! command -v xrt-smi >/dev/null 2>&1 && \\
   ! command -v xbutil >/dev/null 2>&1 && \\
   {{ [ -z "$HLS_XRT_TOOL" ] || [ ! -x "$HLS_XRT_TOOL" ]; }}; then
  echo "{BOARD_STATUS_MARKER} blocked_xrt"
  exit 47
fi
XRT_INCLUDE_DIR="${{XILINX_XRT:-/opt/xilinx/xrt}}/include"
XRT_LIB_DIR="${{XILINX_XRT:-/opt/xilinx/xrt}}/lib"
export LD_LIBRARY_PATH="$XRT_LIB_DIR${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
cd artifacts
SRC_FILE="$(find src -maxdepth 1 -type f \\
  \\( -name '*.cpp' -o -name '*.cc' -o -name '*.cxx' \\) | head -n 1)"
if [ -z "$SRC_FILE" ]; then
  echo "{BOARD_STATUS_MARKER} missing_kernel_source"
  exit 48
fi
"$HLS_VPP_TOOL" -c -t hw --platform "$HLS_PLATFORM_NAME" \\
  -k "$HLS_TOP_FUNCTION" "$SRC_FILE" -o kernel.xo
"$HLS_VPP_TOOL" -l -t hw --platform "$HLS_PLATFORM_NAME" kernel.xo -o kernel.xclbin
g++ -std=c++17 -O2 ../board/host.cpp \\
  -I"$XRT_INCLUDE_DIR" -L"$XRT_LIB_DIR" -Wl,-rpath,"$XRT_LIB_DIR" \\
  -lxrt_coreutil -pthread -o host.exe
set +e
./host.exe kernel.xclbin 2>&1 | tee board_run.log
host_rc=${{PIPESTATUS[0]}}
set -e
if [ "$host_rc" -ne 0 ] && \\
   grep -qi "Permission denied Device index 0" board_run.log && \\
   command -v sudo >/dev/null 2>&1 && \\
   sudo -n true >/dev/null 2>&1; then
  set +e
  sudo -n env LD_LIBRARY_PATH="$LD_LIBRARY_PATH" \\
    XILINX_XRT="${{XILINX_XRT:-/opt/xilinx/xrt}}" \\
    ./host.exe kernel.xclbin 2>&1 | tee board_run.log
  host_rc=${{PIPESTATUS[0]}}
  set -e
fi
if [ "$host_rc" -ne 0 ]; then
  exit "$host_rc"
fi
grep -q "{BOARD_STATUS_MARKER} passed" board_run.log
echo "{BOARD_STATUS_MARKER} passed"
"""
