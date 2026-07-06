#!/usr/bin/env python3
"""通过 erie-remote-ssh 执行 link、Vitis 和 board 远端验收。

机器可读 stdout 协议:
    stdout_protocol: json
"""

# 未来注解避免 facade wrapper 的类型引用影响运行时导入顺序。
from __future__ import annotations

# 标准库导入只承担 CLI、JSON 输出和本地路径解析职责。
import argparse
import hashlib
import json

# shell quoting、动态导入路径和 tar 归档分别服务远端命令与源码快照。
import shlex
import site
import sys
import tarfile

# 路径、模块和类型工具用于 facade 兼容层与远端 POSIX 路径拼装。
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Callable, Iterator

# 当前脚本既可作为模块导入，也可作为文件直接执行。
path_module_dir = Path(__file__).resolve().parent  # 当前 remote 脚本目录

# skill 根目录用于直接执行脚本时导入 scripts.python 包。
path_skill_root = Path(__file__).resolve().parents[3]  # erie-hls-generator 技能根目录

# 直接按文件路径加载 facade 时，也要能解析同目录 short import 与 skill 内包导入。
site.addsitedir(str(path_module_dir))

# 把技能根目录一并加入 site 路径，保证脚本直跑时也能解析 scripts.python 包。
site.addsitedir(str(path_skill_root))

# 优先使用 package 路径导入，脚本直跑时回退到同目录短导入。
try:
    from scripts.python.remote import remote_acceptance_common as _rc

# 文件直跑时改用同目录短名称导入 common 模块。
except ImportError:
    import remote_acceptance_common as _rc

# short name 映射让后续子模块内部的同目录导入命中已加载 common。
sys.modules["remote_acceptance_common"] = _rc  # 子模块短导入使用的 common 映射

# link 模块依赖 common 的短模块名已经存在。
try:
    from scripts.python.remote import remote_acceptance_link as _rl

# link 模式只做 SSH 连通性验收，脚本独立运行时从当前目录取这段实现。
except ImportError:
    import remote_acceptance_link as _rl

# 历史调用方可能先按短模块名索引 link，这里把已加载对象回填到模块注册表。
sys.modules["remote_acceptance_link"] = _rl  # 让旧 link 导入路径命中当前对象

# Vitis 模块依赖 common，并会被 board/recovery 短名称复用。
try:
    from scripts.python.remote import remote_acceptance_vitis as _rv

# Vitis 路径承载 profile 解析和远端执行封装，独立脚本入口要直接拿到本地实现。
except ImportError:
    import remote_acceptance_vitis as _rv

# 恢复链路会沿用旧 Vitis 模块名读取阶段函数，这里提前登记兼容别名。
sys.modules["remote_acceptance_vitis"] = _rv  # 让恢复链路按旧 Vitis 名称拿到阶段实现

# board 模块依赖 common 和已映射的 Vitis 短模块。
try:
    from scripts.python.remote import remote_acceptance_board as _rb

# board 流程依赖前面映射好的 common 与 Vitis，独立运行时仍需在本目录解析该模块。
except ImportError:
    import remote_acceptance_board as _rb

# 旧测试和恢复逻辑会直接触达 board 短模块名，这里保持兼容注册入口。
sys.modules["remote_acceptance_board"] = _rb  # 让历史 board 入口继续复用当前模块对象

# recovery 模块依赖 common、Vitis 和 board 的短模块映射。
try:
    from scripts.python.remote import remote_acceptance_recovery as _rr

# 恢复分支要读取历史 run、日志和板卡证据，独立入口直接从本目录加载实现。
except ImportError:
    import remote_acceptance_recovery as _rr

# 老的恢复调用链按短模块名查找 recovery，这里显式注册兼容别名。
sys.modules["remote_acceptance_recovery"] = _rr  # 让旧 recovery 恢复入口保持短模块名兼容

# common 的状态常量决定 CLI 退出码和 JSON status 字段。
from remote_acceptance_common import (
    BLOCKED_BOARD_STATUS,
    BLOCKED_PROFILE_STATUS,
    BLOCKED_VERSION_STATUS,
    BLOCKED_VITIS_STATUS,
)

# common 的成功、失败和 readiness 常量用于分派和退出码。
from remote_acceptance_common import (
    DRY_RUN_STATUS,
    FAILED_STATUS,
    PASS_STATUS,
    READINESS_LEVELS,
)

# common 的异常和 helper 维持远端调用边界。
from remote_acceptance_common import (
    ErieHelper,
    RemoteAcceptanceError,
    SkillDependencyError,
)

# common 的用户配置读写函数由 facade wrapper 注入到子模块。
from remote_acceptance_common import (
    get_board_platform_selection,
    get_vitis_selection,
    set_board_platform_selection,
    set_vitis_selection,
)

# common 的配置和依赖入口由 run_acceptance 统一调用。
from remote_acceptance_common import (
    remote_validation_config,
    require_skill_dependencies,
    skill_dependencies_config,
    vitis_tool_timeout,
)

# common 的报告、探测和格式化函数保持向旧测试公开。
from remote_acceptance_common import (
    infer_target_part_from_platform_selection,
    subprocess,
    time,
    _expand_settings_path,
    _format_result,
    _is_transient_status_failure,
)

# common 的计划、server list 和远端探测函数保留给旧测试。
from remote_acceptance_common import (
    _planned_steps,
    _prepare_erie_server_list_copy,
    _probe_board_toolchain,
    _probe_hardware_fingerprint,
    _probe_platform_name,
)

# common 的 Vitis 探测和 topology 函数由 facade 直接复用。
from remote_acceptance_common import (
    _probe_remote_workdir,
    _probe_vitis,
    _reject_decode_noise,
    _resolve_topology,
)

# common 的低层目录和文本解析函数保留给兼容调用方。
from remote_acceptance_common import (
    _archive_remote_run,
    _ensure_remote_project_layout,
    _field_from_output,
    _merge_profile_fields,
    _new_run_dir,
)

# common 的请求路径和 FPGA/shell 探测函数保持兼容导出。
from remote_acceptance_common import (
    _parse_request_path,
    _probe_fpga_presence,
    _probe_shell_name,
    _probe_target_part_hint,
    _probe_uploaded_platform,
)

# common 的远端平台建议和写文件函数保持兼容导出。
from remote_acceptance_common import (
    _resolve_erie_server_list,
    _section_value,
    _suggest_platform_name_from_shell,
    _write_erie_settings_overlay,
    _write_json,
    _write_report,
)

# link 模式入口只负责 SSH 连通性验收。
from remote_acceptance_link import _run_link_mode

# Vitis 模块导入拆分为 profile 解析、artifact 生成和远端执行三组。
from remote_acceptance_vitis import (
    _blocked_profile_config,
    _find_candidate,
    _find_server_record,
    _infer_target_part_from_server,
    _infer_target_part_from_server_record,
)

# Vitis profile 解析函数保留给测试和旧调用方。
from remote_acceptance_vitis import (
    _resolve_profile_config,
    _resolve_profile_for_version,
    _resolve_target_part,
    _select_shared_vitis_version,
    _select_vitis_profile,
)

# Vitis 版本标签和候选排序函数保持兼容导出。
from remote_acceptance_vitis import (
    _version_label,
    _version_sort_key,
    _vitis_version_candidates,
)

# Vitis artifact 生成函数保持测试和旧入口可访问。
from remote_acceptance_vitis import (
    _create_vitis_package,
    _generate_local_hls_artifacts,
    _infer_vitis_hls_env_setup,
    _infer_vitis_hls_executable,
    _load_example_spec,
)

# Vitis 远端脚本和命令构造函数保持兼容导出。
from remote_acceptance_vitis import (
    _remote_runner_script,
    _remote_vitis_command,
    _remote_vitis_version_request,
    _safe_tail_log,
    _transfer_package_by_request_commands,
)

# Vitis 模式执行入口由 run_acceptance 分派调用。
from remote_acceptance_vitis import (
    _run_server_vitis_phase,
    _run_split_vitis_mode,
    _run_vitis_mode,
)

# board 模块的 package、host 渲染和平台选择函数保持 facade 兼容。
from remote_acceptance_board import (
    _board_metadata_for_spec,
    _board_platform_upload_plan,
    _board_runner_script,
    _create_board_package,
    _default_platform_name_for_part,
)

# board 平台选择和远端路径工具保持兼容导出。
from remote_acceptance_board import (
    _explicit_board_platform_selection,
    _governed_remote_platform_selection,
    _local_board_platform_upload_selection,
    _normalize_remote_platform_path,
    _remote_board_command,
)

# board host 渲染和执行入口保持兼容导出。
from remote_acceptance_board import (
    _remote_relative_to_workdir,
    _render_board_host,
    _render_vector_driven_board_host,
    _resolve_board_platform_selection,
)

# board 执行和平台上传入口保持兼容导出。
from remote_acceptance_board import (
    _run_board_mode,
    _upload_local_board_platform_payload,
    _vector_literal,
)

# recovery 模块函数支持历史 board run 恢复。
from remote_acceptance_recovery import (
    _probe_recoverable_board_result,
    _recover_board_mode,
    _recover_board_profile,
    _recover_example_spec,
)

# recovery 的本地路径和恢复目标解析函数保持兼容导出。
from remote_acceptance_recovery import (
    _recover_local_run_dir,
    _recover_settings_path,
    _resolve_recovery_target,
)

# board 运行日志中的通过/失败标记必须与 runner 输出保持一致。
BOARD_STATUS_MARKER = "HLS_BOARD_STATUS"  # board runner 写入日志的状态标记

# UTF-8 提示保留给旧错误路径和测试断言使用。
UTF8_HINT = "Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 when calling erie-remote-ssh."  # erie 输出解码失败提示

# 原始 Vitis profile 选择函数供 wrapper 注入 facade 配置函数后调用。
func_orig_select_vitis_profile: Callable[..., Any] = _rv._select_vitis_profile  # 原始 Vitis profile 选择入口

# 原始 profile 配置解析函数供 wrapper 复用。
func_orig_resolve_profile_config: Callable[..., Any] = _rv._resolve_profile_config  # 原始 profile 配置解析入口

# 原始 blocked profile 构造函数供 wrapper 复用。
func_orig_blocked_profile_config: Callable[..., Any] = _rv._blocked_profile_config  # 原始 profile 阻塞报告入口

# 原始 board 平台选择函数供 wrapper 注入用户配置读写。
func_orig_resolve_board_platform_selection: Callable[..., Any] = _rb._resolve_board_platform_selection  # 原始 board 平台选择入口

# 原始 board 平台上传函数供 wrapper 注入持久化写入函数。
func_orig_upload_local_board_platform_payload: Callable[..., Any] = _rb._upload_local_board_platform_payload  # 原始平台上传入口

# 远端 pytest 源码快照只包含当前仓库的受控源文件和测试资产。
TUPLE_REMOTE_PYTEST_SOURCE_INCLUDED_DIRS = (  # 允许进入远端 pytest 快照的源码目录
    Path("skills") / "erie-hls-generator",  # 当前技能主体源码与资产
    Path("tests"),  # 仓库级 pytest 回归用例
)

# 远端 pytest 只带当前治理断言会读取的轻量根文件，避免上传历史归档和本地参考库。
TUPLE_REMOTE_PYTEST_SOURCE_INCLUDED_FILES = (  # 允许进入远端 pytest 快照的轻量治理文件
    Path("AGENTS.md"),  # 根级治理约束供测试读取
    Path(".agents") / "agents-control.json",  # 当前强控制配置
    Path(".agents") / "global-rule-overrides.json",  # 当前本地治理覆盖配置
    Path("docs") / "handoff" / "HANDOFF.md",  # handoff 引用污染回归测试读取
    Path("docs") / "git_manager" / "CHANGELOG.md",  # 当前 changelog 污染检测输入
    Path("docs") / "dir_manager" / "DIR_MANAGER.md",  # 目录治理说明轻量输入
    Path("docs") / "dir_manager" / "planned_structure.json",  # 远端结构契约轻量输入
)

# 排除名单继续保护白名单目录里的缓存、私有设置和运行产物。
TUPLE_REMOTE_PYTEST_SOURCE_EXCLUDED_NAMES = (  # 不允许上传到远端 pytest 快照的目录或文件名
    ".codebase-memory",  # codebase-memory 索引产物不属于源码验证输入
    ".git",  # git 内部对象不能作为远端 pytest 输入上传
    ".mypy_cache",  # 类型检查缓存不属于当前源码
    ".pytest_cache",  # pytest 缓存会污染远端回归证据
    ".ruff_cache",  # lint 缓存不属于当前源码
    ".settings",  # 本地私有设置必须留在本机
    "__pycache__",  # Python 字节码缓存不属于源码快照
    "backups",  # 远端运行归档不能进入测试源包
    "dist",  # 发布产物不能反向进入源码回归
    "reports",  # 本地报告会膨胀并污染远端源码快照
    "runs",  # 运行产物不能进入当前源码证据
)

# Python 字节码和临时归档不是源码证据，不能进入远端 pytest 快照。
TUPLE_REMOTE_PYTEST_SOURCE_EXCLUDED_SUFFIXES = (".pyc", ".pyo")  # 远端 pytest 快照排除的文件后缀

# 原始 Vitis server phase 函数供测试替换 transfer/command 依赖。
func_orig_run_server_vitis_phase: Callable[..., Any] = _rv._run_server_vitis_phase  # 原始远端 Vitis 阶段入口

# 委托模块列表替代 wildcard import，仍允许旧调用方按属性读取子模块符号。
tuple_delegate_modules: tuple[ModuleType, ...] = (_rc, _rl, _rv, _rb, _rr)  # facade 兼容委托模块

# `_select_vitis_profile` 在调用原始实现前同步 facade 的用户配置函数。
def _select_vitis_profile(*args: Any, **kwargs: Any) -> Any:
    """调用原始 Vitis profile 选择逻辑并注入 facade 配置读写函数。

    参数:
        *args: 传给原始实现的位置参数。
        **kwargs: 传给原始实现的关键字参数。

    返回:
        Any: 原始实现返回的 profile 选择结果。
    """

    # 子模块使用 facade 当前导入的读写函数，便于测试 patch。
    _rv.get_vitis_selection = get_vitis_selection  # 注入可 patch 的 Vitis 选择读取函数

    # set 函数同样保持可替换，避免直接绑定旧对象。
    _rv.set_vitis_selection = set_vitis_selection  # 注入可 patch 的 Vitis 选择写入函数

    # 原始实现负责实际版本选择和阻塞报告构造。
    return func_orig_select_vitis_profile(*args, **kwargs)

# `_resolve_profile_config` 让 profile 配置解析复用 facade 的持久选择读取。
def _resolve_profile_config(*args: Any, **kwargs: Any) -> Any:
    """调用原始 profile 配置解析逻辑。

    参数:
        *args: 传给原始实现的位置参数。
        **kwargs: 传给原始实现的关键字参数。

    返回:
        Any: 原始实现返回的 profile 配置。
    """

    # Vitis 子模块从 facade 读取当前可 patch 的选择函数。
    _rv.get_vitis_selection = get_vitis_selection  # profile 解析使用的 Vitis 选择读取函数

    # 解析细节仍由原始 Vitis 模块维护。
    return func_orig_resolve_profile_config(*args, **kwargs)

# `_blocked_profile_config` 保持旧测试可直接调用的 wrapper 名字。
def _blocked_profile_config(*args: Any, **kwargs: Any) -> Any:
    """调用原始 blocked profile 报告构造逻辑。

    参数:
        *args: 传给原始实现的位置参数。
        **kwargs: 传给原始实现的关键字参数。

    返回:
        Any: 原始实现返回的 blocked profile 报告。
    """

    # 该赋值保持旧 facade 的 monkeypatch 行为，不改变实际路径解析。
    _rv.user_config_path = _rv.user_config_path  # 保留旧 facade 的 user_config_path 绑定点

    # 原始实现负责缺失字段和用户提示内容。
    return func_orig_blocked_profile_config(*args, **kwargs)

# `_resolve_board_platform_selection` 在 board 模块中注入 facade 配置函数。
def _resolve_board_platform_selection(*args: Any, **kwargs: Any) -> Any:
    """调用原始 board 平台选择逻辑。

    参数:
        *args: 传给原始实现的位置参数。
        **kwargs: 传给原始实现的关键字参数。

    返回:
        Any: 原始实现返回的 board 平台选择。
    """

    # board 模块读取 facade 当前绑定的 selection getter。
    _rb.get_board_platform_selection = get_board_platform_selection  # board 平台选择读取函数

    # board 模块写入 facade 当前绑定的 selection setter。
    _rb.set_board_platform_selection = set_board_platform_selection  # board 平台选择写入函数

    # 原始实现继续负责治理约束和远端 workdir 解析。
    return func_orig_resolve_board_platform_selection(*args, **kwargs)

# `_upload_local_board_platform_payload` 维持上传路径的 facade 可 patch 行为。
def _upload_local_board_platform_payload(*args: Any, **kwargs: Any) -> Any:
    """调用原始本地 board 平台 payload 上传逻辑。

    参数:
        *args: 传给原始实现的位置参数。
        **kwargs: 传给原始实现的关键字参数。

    返回:
        Any: 原始实现返回的平台上传结果。
    """

    # 上传成功后的平台选择写入必须使用 facade 当前 setter。
    _rb.set_board_platform_selection = set_board_platform_selection  # 上传后持久化 board 平台选择的函数

    # 原始实现负责请求文件、远端目录和 payload 传输细节。
    return func_orig_upload_local_board_platform_payload(*args, **kwargs)

# `_run_server_vitis_phase` 让测试可替换 transfer 和 command helper。
def _run_server_vitis_phase(*args: Any, **kwargs: Any) -> Any:
    """调用原始远端 Vitis 阶段执行逻辑。

    参数:
        *args: 传给原始实现的位置参数。
        **kwargs: 传给原始实现的关键字参数。

    返回:
        Any: 原始实现返回的阶段执行结果。
    """

    # transfer helper 从 facade 读取，便于测试注入上传请求。
    _rv._transfer_package_by_request_commands = _transfer_package_by_request_commands  # Vitis package 上传请求函数

    # command helper 从 facade 读取，便于测试检查远端命令。
    _rv._remote_vitis_command = _remote_vitis_command  # Vitis 远端执行命令构造函数

    # 原始实现负责 detached job、日志 tail 和失败消息汇总。
    return func_orig_run_server_vitis_phase(*args, **kwargs)

# `_build_parser` 集中定义 CLI 参数，避免 main 承担参数表细节。
def _build_parser() -> argparse.ArgumentParser:
    """构造远端验收 CLI 参数解析器。

    返回:
        argparse.ArgumentParser: 配置完整的 CLI parser。
    """

    # parser 描述用于命令行帮助和错误提示。
    parser = argparse.ArgumentParser(  # 远端验收 CLI parser 构造调用
        description="Validate HLS generator remote confidence through erie-remote-ssh."  # 帮助文本里的总说明
    )  # 远端验收 CLI parser

    # mode 决定本次验收分派到 link、pytest、smoke、vitis 还是 board。
    parser.add_argument("--mode", required=True, choices=("link", "pytest", "smoke", "vitis", "board"))

    # server 是单机拓扑和 board 模式的目标服务器。
    parser.add_argument("--server", help="Single-server target id or name from erie-remote-ssh config.")

    # build-server 用于 split Vitis 拓扑的构建侧。
    parser.add_argument("--build-server", help="Build-server id or name for split build/validate topology.")

    # validate-server 用于 split Vitis 拓扑的验收侧。
    parser.add_argument("--validate-server", help="Validation-server id or name for split build/validate topology.")

    # profile 可选择治理配置中的 Vitis profile。
    parser.add_argument("--profile", help="Optional remote_validation.vitis_profiles key for Vitis mode.")

    # vitis-version 允许用户显式锁定远端 Vitis 版本。
    parser.add_argument("--vitis-version", help="Explicit remote Vitis version to use and remember for this server.")

    # target-part 覆盖远端 HLS synthesis 的硬件 part。
    parser.add_argument("--target-part", help="Optional explicit target part override for remote HLS synthesis.")

    # platform-name 指定 board 模式的平台名或平台 spec。
    parser.add_argument("--platform-name", help="Explicit board platform name or platform spec for board mode.")

    # remote-platform-root 指定远端已上传平台目录。
    parser.add_argument(
        "--remote-platform-root",
        help="Remote directory containing an uploaded board platform for board mode.",
    )

    # remote-xpfm 指定 board 模式使用的远端 XPFM 路径。
    parser.add_argument("--remote-xpfm", help="Explicit remote XPFM path for board mode.")

    # readiness 控制 Vitis acceptance 的深度。
    parser.add_argument("--readiness", default="cosim", choices=READINESS_LEVELS)

    # example-spec 指定本地生成 HLS acceptance artifact 的示例。
    parser.add_argument(
        "--example-spec",
        default="hls_vector_scale_mock_spec.json",
        help="Example spec from assets/examples used for Vitis acceptance artifacts.",
    )

    # recover-run-id 用于恢复历史 detached board 验收结果。
    parser.add_argument(
        "--recover-run-id",
        help="Recover a prior detached remote acceptance result by local/remote run id.",
    )

    # recover-remote-run-dir 允许用户直接指定远端运行目录恢复。
    parser.add_argument(
        "--recover-remote-run-dir",
        help="Recover a prior detached remote acceptance result from an explicit remote run directory.",
    )

    # comment-language 控制本地生成 acceptance artifact 的注释语言。
    parser.add_argument(
        "--comment-language",
        default="auto",
        choices=("auto", "en", "zh"),
        help="Comment language for locally generated HLS acceptance artifacts.",
    )

    # timeout 覆盖远端命令默认超时。
    parser.add_argument("--timeout", type=int, help="Override remote command timeout in seconds.")

    # cleanup-remote 控制 Vitis 成功后是否删除远端验证目录。
    parser.add_argument(
        "--cleanup-remote",
        action="store_true",
        help="Delete the remote validation directory after a successful Vitis run.",
    )

    # dry-run 只输出计划，不连接远端服务器。
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned erie helper steps without connecting.",
    )

    # json 控制 stdout 使用机器可读格式。
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    # parser 返回给 main 解析 argv。
    return parser

# `main` 是脚本入口，负责异常转换、输出格式和退出码。
def main(argv: list[str] | None = None) -> int:
    """执行远端验收 CLI。

    参数:
        argv: 可选命令行参数列表；None 时使用 sys.argv。

    返回:
        int: 进程退出码。
    """

    # parser 单独构造，便于测试直接传 argv。
    parser = _build_parser()  # CLI 参数解析器

    # args 是后续 run_acceptance 的唯一输入对象。
    args = parser.parse_args(argv)  # 解析后的 CLI 参数

    # CLI 入口统一把业务异常折叠成结构化结果，避免把 traceback 暴露给协议消费者。
    try:

        # run_acceptance 返回所有模式统一的结果字典。
        dict_result = run_acceptance(args)  # 远端验收结果

    # 依赖缺失时直接复用异常里已经整理好的阻塞报告。
    except SkillDependencyError as exc:

        # 依赖缺失异常自带可输出的 blocked/failed 报告。
        dict_result = exc.report  # 依赖异常中的报告

    # 其他运行时错误统一降级成 failed 结果，保持 CLI 输出协议稳定。
    except (OSError, RemoteAcceptanceError, ValueError) as exc:

        # 运行时错误统一转换为 failed JSON，避免 traceback 污染协议输出。
        dict_result = {"status": FAILED_STATUS, "error": str(exc)}  # failed 错误报告

    # 用户要求 JSON 时输出机器可读结果。
    if args.json:

        # JSON 协议输出使用 stdout.write，避免 print 被 current-project 门禁视为人类终端输出。
        sys.stdout.write(json.dumps(dict_result, indent=2, ensure_ascii=False) + "\n")

    # 非 JSON 模式只输出带固定前缀的人类可读摘要。
    else:

        # 先把结果字典压成摘要文本，避免把结构化载荷直接暴露给人类可读 print。
        str_summary_text = _format_result(dict_result)  # 终端展示用结果摘要文本

        # 人类可读输出复用 common 中的统一格式化器。
        print(f"> INFO: [Python] acceptance summary: {str_summary_text}")

    # status 到退出码的映射保持旧脚本契约。
    return _exit_code_for_status(str(dict_result["status"]))

# `_exit_code_for_status` 集中维护 status 与进程退出码的对应关系。
def _exit_code_for_status(status: str) -> int:
    """根据验收状态返回 CLI 退出码。

    参数:
        status: 验收结果状态字符串。

    返回:
        int: 对应的进程退出码。
    """

    # passed 和 dry-run 都表示命令成功完成。
    if status in {PASS_STATUS, DRY_RUN_STATUS}:

        # 成功状态使用 shell 约定的 0。
        return 0

    # profile 缺失需要用户配置，使用专门退出码。
    if status == BLOCKED_PROFILE_STATUS:

        # 5 表示 profile 配置阻塞。
        return 5

    # Vitis 版本缺失需要用户选择或安装。
    if status == BLOCKED_VERSION_STATUS:

        # 4 表示 Vitis 版本阻塞。
        return 4

    # Vitis 工具链不可用或探测失败。
    if status == BLOCKED_VITIS_STATUS:

        # 3 表示 Vitis 工具链阻塞。
        return 3

    # board 验收缺少板卡或平台证据。
    if status == BLOCKED_BOARD_STATUS:

        # 6 表示 board 验收阻塞。
        return 6

    # 其他失败统一返回 1。
    return 1

# `run_acceptance` 是测试和 CLI 共享的模式分派入口。
def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    """根据 CLI 参数执行 link、Vitis、board 或恢复模式。

    参数:
        args: 已解析的 CLI 参数命名空间。

    返回:
        dict[str, Any]: 远端验收结果报告。
    """

    # core 依赖必须先满足，避免远端流程执行到一半才失败。
    require_skill_dependencies(skill_dependencies_config(), scopes={"core"})

    # 远端验收配置来自技能治理配置和用户本地配置。
    dict_config = remote_validation_config()  # 远端验收配置

    # 非 dry-run 需要复制 server list，避免把本地私有配置直接传远端。
    if not args.dry_run:

        # server list copy 路径写入配置，供 ErieHelper 使用。
        str_server_list_config = str(_prepare_erie_server_list_copy(dict_config))  # 临时 server list 配置路径

        # 合并后的配置只覆盖 helper 需要的 server list 来源。
        dict_config = {
            **dict_config,  # 保留远端验收原始配置字段
            "erie_server_list_config": str_server_list_config,  # 把 helper 要读取的 server list copy 路径写回配置
        }  # 注入 server list copy 后的远端配置

    # timeout 以 CLI 为准，否则使用治理配置默认值。
    int_base_timeout = int(args.timeout or dict_config["default_timeout_s"])  # 远端命令基础超时秒数

    # Vitis/board 模式要覆盖工具运行时间，避免 cosim 或 link 被过早杀掉。
    if args.mode in {"vitis", "board"}:

        # readiness timeout 加缓冲后作为远端 helper 超时下限。
        int_vitis_timeout = int(vitis_tool_timeout(args.readiness)) + 30  # Vitis readiness 超时下限

        # 取较大值，保留用户显式增大的 timeout。
        int_base_timeout = max(int_base_timeout, int_vitis_timeout)  # 最终远端命令超时秒数

    # 远端 pytest/smoke 会上传源码并执行 Python 回归，因此也需要抬高 helper 超时上限。
    if args.mode in {"pytest", "smoke"}:

        # 取较大值，避免完整远端 Python 回归被默认超时过早截断。
        int_base_timeout = max(int_base_timeout, 1800)  # 远端 Python 回归的基础超时下限

    # ErieHelper 封装 server list copy 和 timeout 后的远端命令执行上下文。
    erie_helper_helper: ErieHelper = ErieHelper(dict_config, int_base_timeout)  # 远端验收命令执行 helper

    # topology 标准化 single 和 split 两种服务器输入。
    dict_topology = _resolve_topology(args)  # 远端服务器拓扑

    # plan 只描述将执行的步骤，dry-run 和报告都会使用。
    list_plan = _acceptance_plan(args, dict_topology)  # 远端验收计划步骤

    # dry-run 不连接远端，只输出计划和保留策略。
    if args.dry_run:

        # dry-run 报告复用统一字段，便于 CI 和人工检查。
        return _dry_run_result(args, dict_topology, list_plan)

    # link 模式只验证 SSH helper 链路。
    if args.mode == "link":

        # link 入口负责 preflight 和 helper 探测。
        return _run_link_mode(args, dict_config, erie_helper_helper, list_plan, dict_topology)

    # pytest/smoke 模式只在目标服务器执行 Python 回归，不进入 Vitis / board 资产链。
    if args.mode in {"pytest", "smoke"}:

        # Python 回归入口负责 preflight、项目根探测和远端源码快照命令。
        return _run_pytest_mode(args, dict_config, erie_helper_helper, list_plan, dict_topology)

    # board 模式包含新运行和历史恢复两条路径。
    if args.mode == "board":

        # board 当前只允许单服务器，避免板卡证据跨主机混淆。
        return _run_board_acceptance(args, dict_config, erie_helper_helper, list_plan, dict_topology)

    # split 拓扑下 Vitis build 和 validation 分别在两台服务器执行。
    if dict_topology["topology"] == "split_build_validate":

        # split Vitis 入口负责共享版本选择和跨服务器 package 流转。
        return _run_split_vitis_mode(args, dict_config, erie_helper_helper, list_plan, dict_topology)

    # 默认 Vitis 模式在单服务器上完成 build/cosim。
    return _run_vitis_mode(args, dict_config, erie_helper_helper, list_plan, dict_topology)

# `_acceptance_plan` 生成 dry-run 和实际报告共用的步骤说明。
def _acceptance_plan(args: argparse.Namespace, topology: dict[str, Any]) -> list[dict[str, Any]]:
    """生成远端验收计划步骤。

    参数:
        args: 已解析的 CLI 参数命名空间。
        topology: 已标准化的远端服务器拓扑。

    返回:
        list[dict[str, Any]]: 计划步骤列表。
    """

    # cleanup flag 需要转为 bool，避免 argparse 默认值进入报告时歧义。
    bool_cleanup_remote = bool(getattr(args, "cleanup_remote", False))  # 是否清理远端 artifact

    # example spec 始终转成字符串，便于 JSON 报告稳定输出。
    str_example_spec = str(getattr(args, "example_spec", ""))  # 验收示例 spec 名称

    # validate_server 仅 split 拓扑存在，传 None 给 common 以保持兼容。
    str_validate_server = topology.get("validate_server")  # split 验收服务器名称

    # pytest/smoke 计划只需要覆盖目标服务器的基础检查和 Python 回归执行。
    if args.mode in {"pytest", "smoke"}:

        # split 拓扑要固定使用 validate 服务器；单机则直接使用 server。
        str_target_server = str(topology.get("validate_server") or topology["server"])  # Python 回归实际执行服务器

        # smoke 模式执行迁移后的 smoke 入口脚本，pytest 模式执行完整 tests 套件。
        if args.mode == "smoke":

            # smoke 计划展示真实入口脚本，便于和结果 JSON 的 command 字段对照。
            str_remote_command = "python tests/smoke/run_smoke.py"  # 远端 smoke 计划命令摘要

        # pytest 模式仍展示完整测试目录回归命令。
        else:

            # pytest 计划展示完整 tests 目录，区别于 smoke 的 curated 子集。
            str_remote_command = "python -m pytest -q tests"  # 远端完整 pytest 计划命令摘要

        # 返回 Python 回归模式的最小可审查计划步骤。
        return [
            "erie discover",
            "erie list",
            f"erie check {str_target_server}",
            f"erie workspace-check {str_target_server}",
            f"erie exec remote {str_remote_command}",
        ]

    # common 计划函数负责保持步骤文案一致。
    return _planned_steps(
        args.mode,
        topology["server"],
        args.profile,
        args.readiness,
        cleanup_remote=bool_cleanup_remote,
        example_spec=str_example_spec,
        validate_server=str_validate_server,
        topology=topology["topology"],
    )

# `_dry_run_result` 构造不连接远端时的机器可读结果。
def _dry_run_result(
    args: argparse.Namespace,
    topology: dict[str, Any],
    plan: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造 dry-run 报告。

    参数:
        args: 已解析的 CLI 参数命名空间。
        topology: 已标准化的远端服务器拓扑。
        plan: 计划步骤列表。

    返回:
        dict[str, Any]: dry-run 状态报告。
    """

    # 基础报告先声明状态，后续逐项补齐拓扑和计划字段。
    dict_result: dict[str, Any] = {}  # dry-run 报告累积字典

    # status 标记本次没有连接远端服务器。
    dict_result["status"] = DRY_RUN_STATUS  # dry-run 状态字段

    # mode 保留用户选择的验收模式。
    dict_result["mode"] = args.mode  # 验收模式字段

    # server 是分派后的主服务器名称。
    dict_result["server"] = topology["server"]  # 主服务器字段

    # build_server 指向 split 拓扑里负责 package 构建的那台服务器。
    dict_result["build_server"] = topology.get("build_server")  # split 拓扑中的 package 构建节点

    # validate_server 指向 split 拓扑里负责版本探测和验收闭环的那台服务器。
    dict_result["validate_server"] = topology.get("validate_server")  # split 验收服务器字段

    # topology 标明 single 或 split，供报告阅读方判断证据来源。
    dict_result["topology"] = topology["topology"]  # 服务器拓扑字段

    # steps 是 dry-run 最核心的执行计划输出。
    dict_result["steps"] = plan  # dry-run 执行计划字段

    # uses_erie_remote_ssh 明确该计划属于 erie helper 路径。
    dict_result["uses_erie_remote_ssh"] = True  # helper 路由标记

    # Vitis 和 board 模式都需要声明远端 artifact 保留策略。
    if args.mode in {"vitis", "board"}:

        # dry-run 没有实际清理动作，artifact 也未创建。
        dict_result.update(
            {
                "cleanup_performed": False,
                "remote_artifacts_retained": True,
            }
        )

    # 返回 dry-run 报告供 CLI 输出。
    return dict_result

# `_run_board_acceptance` 统一处理 board 新运行和历史恢复。
def _run_board_acceptance(
    args: argparse.Namespace,
    config: dict[str, Any],
    helper: ErieHelper,
    plan: list[dict[str, Any]],
    topology: dict[str, Any],
) -> dict[str, Any]:
    """执行 board 模式验收或恢复。

    参数:
        args: 已解析的 CLI 参数命名空间。
        config: 远端验收配置。
        helper: 远端 SSH helper。
        plan: 计划步骤列表。
        topology: 已标准化的远端服务器拓扑。

    返回:
        dict[str, Any]: board 模式验收结果。

    异常:
        ValueError: 当用户错误地把 board 模式配置成 split 拓扑时抛出。
    """

    # board 模式必须在同一服务器上拥有平台和硬件证据。
    if topology["topology"] != "single_server":

        # split board 会导致硬件证据来源不唯一，因此直接拒绝。
        raise ValueError("> ERR: [Python] board acceptance requires --server and does not support split topology.")

    # recover 参数任一存在时进入历史恢复流程。
    if _has_recovery_target(args):

        # 恢复流程只读取已有远端日志并按需归档。
        return _recover_board_mode(args, config, helper, topology)

    # 普通 board 模式会生成 package 并在远端执行 host run。
    return _run_board_mode(args, config, helper, plan, topology)

# 判断本地路径是否应从远端 pytest 源码快照中排除。
def _is_remote_pytest_source_excluded(path_repo_root: Path, path_candidate: Path) -> bool:
    """
    判断本地路径是否不应进入远端 pytest 源码快照。

    参数:
        path_repo_root: 当前工作树根目录。
        path_candidate: 待检查的本地路径。

    返回:
        bool: True 表示该路径应排除在快照之外。
    """

    # 相对路径部件用于统一过滤顶层目录和深层缓存目录。
    path_relative = path_candidate.relative_to(path_repo_root)  # 候选路径相对仓库根的位置

    # 任意路径部件命中排除名单时，整棵子树都不能进入远端快照。
    if any(str_part in TUPLE_REMOTE_PYTEST_SOURCE_EXCLUDED_NAMES for str_part in path_relative.parts):

        # 命中私有配置、运行产物或缓存目录时直接排除。
        return True

    # 符号链接可能逃逸当前工作树边界，因此不作为远端 pytest 源证据上传。
    if path_candidate.is_symlink():

        # 保守排除符号链接，避免把本地外部路径带到远端。
        return True

    # 字节码后缀不能作为当前源码快照证据。
    return path_candidate.suffix in TUPLE_REMOTE_PYTEST_SOURCE_EXCLUDED_SUFFIXES

# 迭代远端 pytest 快照允许收入的本地源码文件。
def _iter_remote_pytest_source_candidates(path_repo_root: Path) -> Iterator[Path]:
    """
    迭代远端 pytest 源码快照的白名单候选文件。

    参数:
        path_repo_root: 当前工作树根目录。

    返回:
        Iterator[Path]: 允许进一步过滤并写入 tar 的候选源码文件迭代器。
    """

    # 去重集合防止轻量文件和目录递归重复写入 tar。
    set_seen_files: set[Path] = set()  # 白名单候选文件去重集合

    # 轻量治理文件按声明顺序优先纳入，缺失文件不阻塞远端 pytest 快照创建。
    for path_relative_file in TUPLE_REMOTE_PYTEST_SOURCE_INCLUDED_FILES:

        # 当前轻量治理文件以仓库根为基准解析。
        path_candidate = path_repo_root / path_relative_file  # 当前轻量治理文件候选路径

        # 只收入真实文件，缺失的历史兼容文件由相关测试自行报告。
        if path_candidate.is_file():

            # 先登记再 yield，避免目录递归阶段重复收入同一路径。
            set_seen_files.add(path_candidate)

            # 存在的轻量治理文件直接进入候选流。
            yield path_candidate

    # 源码目录递归收入，历史文档、ref 和本地工件目录不再从仓库根递归进入快照。
    for path_relative_dir in TUPLE_REMOTE_PYTEST_SOURCE_INCLUDED_DIRS:

        # 当前白名单目录只允许来自受控源码和测试根。
        path_source_dir = path_repo_root / path_relative_dir  # 当前白名单源码目录

        # 缺失目录交给远端 pytest 的结构验证和测试失败报告，不在打包阶段静默伪造。
        if not path_source_dir.exists():

            # 缺失源码目录不在打包阶段补造，由后续结构验证报错。
            continue

        # 稳定排序让归档顺序可复核。
        for path_candidate in sorted(path_source_dir.rglob("*")):

            # 只收入普通文件，目录和符号链接由后续过滤规则处理。
            if not path_candidate.is_file():

                # 目录不会形成 tar 成员，文件 arcname 会恢复必要层级。
                continue

            # 已通过轻量文件白名单收入的候选不重复写入。
            if path_candidate in set_seen_files:

                # 已登记文件不重复写入 tar，保持归档成员唯一。
                continue

            # 新源码文件先记录，再交给归档调用方。
            set_seen_files.add(path_candidate)

            # 白名单源码文件进入后续排除规则复核。
            yield path_candidate

# 生成当前工作树的远端 pytest 源码快照归档。
def _create_remote_pytest_source_archive(path_run_dir: Path) -> Path:
    """
    生成远端 pytest 使用的当前工作树源码快照。

    参数:
        path_run_dir: 本轮 pytest gate 的本地运行目录。

    返回:
        Path: 生成的 tar.gz 源码快照路径。
    """

    # 当前技能根固定在 repo_root/skills/erie-hls-generator。
    path_repo_root = path_skill_root.parents[1]  # 当前工作树根目录

    # 归档名称带 run id，避免多轮远端 pytest 上传互相覆盖。
    path_archive = path_run_dir / f"{path_run_dir.name}-source.tar.gz"  # 本轮远端 pytest 源码快照归档

    # 创建 tar.gz 时只收入普通文件，目录由 tarfile 在解包时自动生成。
    with tarfile.open(path_archive, "w:gz") as tar_file:

        # 白名单候选已经避开大型历史文档和本地参考库，再做缓存/私有路径过滤。
        for path_candidate in _iter_remote_pytest_source_candidates(path_repo_root):

            # 排除私有配置、运行产物、缓存、符号链接和字节码。
            if _is_remote_pytest_source_excluded(path_repo_root, path_candidate):

                # 当前候选不属于远端 pytest 的源码证据，直接跳过。
                continue

            # 远端 pytest 只需要文件内容；目录不单独打包。
            if not path_candidate.is_file():

                # 目录结构会随文件 arcname 自动恢复，不需要单独打包。
                continue

            # arcname 保持相对仓库根结构，远端解包后可直接运行 tests。
            tar_file.add(
                path_candidate,
                arcname=path_candidate.relative_to(path_repo_root).as_posix(),
            )

    # 返回已生成的归档路径。
    return path_archive

# 计算本地文件 sha256，写入远端 pytest 快照审计结果。
def _sha256_file(path_file: Path) -> str:
    """
    计算文件 sha256 摘要。

    参数:
        path_file: 待计算摘要的本地文件路径。

    返回:
        str: 小写十六进制 sha256 摘要。
    """

    # 分块读取避免对较大的源码快照一次性占用内存。
    sha256_file_hasher: Any = hashlib.sha256()  # 远端 pytest 源码快照归档的 sha256 累积器

    # 以二进制方式读取归档内容。
    with path_file.open("rb") as file_obj:

        # 固定块大小足以覆盖当前源码快照且保持实现简单。
        for bytes_chunk in iter(lambda: file_obj.read(1024 * 1024), b""):

            # 当前块纳入归档摘要，供结果报告复核上传内容。
            sha256_file_hasher.update(bytes_chunk)

    # 返回标准十六进制摘要。
    return sha256_file_hasher.hexdigest()

# 上传并解压当前源码快照，返回远端 pytest 实际执行根目录。
def _prepare_remote_pytest_source_snapshot(
    helper: ErieHelper,
    path_settings: Path,
    str_target_server: str,
    path_run_dir: Path,
    str_remote_workdir: str,
    path_remote_project_root: PurePosixPath,
) -> dict[str, Any]:
    """
    将当前工作树源码快照上传到远端，并准备 pytest 执行目录。

    参数:
        helper: 负责 request 上传和命令执行的远端 helper。
        path_settings: 本轮 pytest 使用的 settings overlay。
        str_target_server: 执行 pytest 的远端服务器。
        path_run_dir: 本轮 pytest gate 的本地运行目录。
        str_remote_workdir: 远端 workspace 根目录。
        path_remote_project_root: 远端受治理项目根目录。

    返回:
        dict[str, Any]: 包含远端源码根、归档摘要和 request 证据的字典。
    """

    # 先生成本地当前工作树快照，保证 remote_pytest 对准本轮源码。
    path_archive = _create_remote_pytest_source_archive(path_run_dir)  # 本地源码快照归档

    # 当前源码快照在远端项目根下独立成目录，避免污染正式 runtime 路径。
    path_remote_source_root = path_remote_project_root / f"remote-pytest-src-{path_run_dir.name}"  # 远端源码快照目录

    # 上传归档放在项目根下，便于后续命令按绝对路径解压。
    path_remote_archive = path_remote_project_root / path_archive.name  # 远端源码快照归档路径

    # request 接口上传路径使用相对 workspace 的路径。
    str_remote_project_root_relative = _remote_relative_to_workdir(str_remote_workdir, path_remote_project_root)  # 远端项目根相对路径

    # 远端归档上传目标同样转换成相对 workspace 的 request 路径。
    str_remote_archive_relative = _remote_relative_to_workdir(str_remote_workdir, path_remote_archive)  # 远端归档相对路径

    # 所有 request manifest 都记录到结果里，形成可追溯上传链路。
    list_requests: list[str] = []  # 源码快照准备阶段 request manifest 列表

    # 先确保远端项目根存在。
    list_requests.append(
        helper.request_and_run(
            path_settings,
            str_target_server,
            "command",
            f"mkdir -p {shlex.quote(str_remote_project_root_relative)}",
            "prepare remote pytest source root",
        )
    )

    # 上传当前源码快照归档。
    list_requests.append(
        helper.request_upload_and_run(
            path_settings,
            str_target_server,
            path_archive,
            str_remote_archive_relative,
            "upload remote pytest source snapshot",
        )
    )

    # 解压到本轮专属目录，并验证 tests 与技能根都存在。
    str_extract_command = (  # 远端源码快照解压和结构验证命令
        f"rm -rf {shlex.quote(path_remote_source_root.as_posix())} && "
        f"mkdir -p {shlex.quote(path_remote_source_root.as_posix())} && "
        f"tar -xzf {shlex.quote(path_remote_archive.as_posix())} "
        f"-C {shlex.quote(path_remote_source_root.as_posix())} && "
        f"test -d {shlex.quote((path_remote_source_root / 'tests').as_posix())} && "
        f"test -d {shlex.quote((path_remote_source_root / 'skills' / 'erie-hls-generator').as_posix())}"
    )

    # 解压和校验也走 request-command，避免不可审计的远端状态变化。
    list_requests.append(
        helper.request_and_run(
            path_settings,
            str_target_server,
            "command",
            str_extract_command,
            "extract remote pytest source snapshot",
        )
    )

    # 返回源码快照证据，供 pytest 结果报告和命令拼接复用。
    return dict(
        local_archive=str(path_archive),
        local_archive_sha256=_sha256_file(path_archive),
        remote_archive=path_remote_archive.as_posix(),
        remote_source_root=path_remote_source_root.as_posix(),
        requests=list_requests,
    )

# 构造远端源码快照中的 Python 回归命令。
def _remote_snapshot_python_command(str_mode: str, str_remote_source_root: str) -> str:
    """
    构造在远端源码快照目录中执行的 Python 回归命令。

    参数:
        str_mode: 当前远端 Python 回归模式，支持 `pytest` 或 `smoke`。
        str_remote_source_root: 已解压的远端源码快照根目录。

    返回:
        str: 可交给 `bash -lc` 执行的远端命令文本。
    """

    # smoke 模式执行迁移后的 smoke 入口；pytest 模式执行完整仓库测试目录。
    if str_mode == "smoke":

        # smoke 路径必须运行仓库迁移后的入口脚本，而不是 pytest 直接收集。
        str_python_command = "python tests/smoke/run_smoke.py"  # 远端 smoke 入口命令

    # 其他 Python 回归模式保持完整 tests 目录收集。
    else:

        # pytest 路径覆盖仓库完整测试目录，用作最终远端回归证据。
        str_python_command = "python -m pytest -q tests"  # 远端完整 pytest 命令

    # 命令统一进入当前源码快照根，并固定 UTF-8 环境，避免远端日志编码漂移。
    return (
        "set -euo pipefail; "
        f"cd {shlex.quote(str_remote_source_root)}; "
        f"PYTHONUTF8=1 PYTHONIOENCODING=utf-8 {str_python_command}"
    )

# `_run_pytest_mode` 在目标服务器执行仓库级 pytest 回归。
def _run_pytest_mode(
    _args: argparse.Namespace,
    config: dict[str, Any],
    helper: ErieHelper,
    plan: list[str],
    topology: dict[str, Any],
) -> dict[str, Any]:
    """执行远端 pytest 回归并返回结构化结果。

    参数:
        _args: 预留的命令行参数对象；当前 pytest 模式不直接读取它。
        config: 当前验收流程使用的配置字典。
        helper: 负责执行远端命令的 SSH 辅助对象。
        plan: 当前验收阶段的步骤列表。
        topology: 已解析的远端拓扑信息。

    返回:
        返回 pytest 模式生成的结构化验收结果。
    """

    # smoke 和 pytest 共用源码快照流程，只在远端执行命令上区分。
    str_result_mode = "smoke" if getattr(_args, "mode", "pytest") == "smoke" else "pytest"  # 当前 Python 回归模式

    # 为本次远端 Python 回归创建独立运行目录。
    path_run_dir = _new_run_dir(config, str_result_mode)  # Python 回归模式运行目录

    # 为本次 pytest run 固化独立 settings overlay，隔离 request/downloads/tmp。
    path_settings = _write_erie_settings_overlay(config, path_run_dir)  # pytest 模式使用的 settings overlay

    # split 拓扑要固定把 pytest 路由到 validate 服务器；单机则直接使用 server。
    str_target_server = str(topology.get("validate_server") or topology["server"])  # 远端 pytest 的实际执行服务器

    # 远端项目根目录名来自治理配置，而不是在脚本里写死。
    str_project_root_dirname = str(config["directory_contract"]["project_root_dirname"])  # 远端项目根目录名

    # 预先组装最小结果骨架，便于异常路径也能带回可审查上下文。
    dict_result: dict[str, Any] = {}  # pytest 模式结果累积字典

    # status 默认先置为 failed，异常路径也能稳定回传。
    dict_result["status"] = FAILED_STATUS  # pytest 初始失败状态

    # mode 固定标记当前结果来自 pytest 或 smoke 路线。
    dict_result["mode"] = str_result_mode  # Python 回归结果模式字段

    # server 记录真正执行 pytest 的远端目标。
    dict_result["server"] = str_target_server  # pytest 目标服务器字段

    # build_server 在 split 拓扑下标识负责编译 package 的服务器。
    dict_result["build_server"] = topology.get("build_server")  # split 构建服务器字段

    # validate_server 在 split 拓扑下标识最终执行 pytest 的服务器。
    dict_result["validate_server"] = topology.get("validate_server")  # split 验证服务器字段

    # topology 保留 single/split 形态，便于调用方判断证据来源。
    dict_result["topology"] = topology["topology"]  # 执行拓扑字段

    # run_dir 指向本次 pytest 路线生成的本地运行目录。
    dict_result["run_dir"] = str(path_run_dir)  # 本地运行目录字段

    # steps 回填当前计划步骤，方便 handoff 直接展示执行链路。
    dict_result["steps"] = plan  # pytest 计划步骤字段

    # uses_erie_remote_ssh 显式声明当前流程走 erie helper 通道。
    dict_result["uses_erie_remote_ssh"] = True  # erie helper 使用标记

    # 远端 pytest 需要先完成 SSH、workspace 和远端项目根探测。
    try:

        # 先完成基础连通性与 workspace 预检。
        helper.preflight(str_target_server, settings=path_settings)

        # 解析当前服务器上的受治理 workspace 根目录。
        str_remote_workdir = _probe_remote_workdir(str_target_server, path_settings, helper)  # 远端 workspace 根目录

        # 远端项目根目录固定落在 workspace 根下的受治理子目录。
        path_remote_project_root = PurePosixPath(str_remote_workdir) / str_project_root_dirname  # 远端项目根目录

        # 上传当前工作树快照，确保 remote_pytest 验证的是本轮源码而不是远端历史目录。
        dict_source_snapshot = _prepare_remote_pytest_source_snapshot(  # 当前源码快照上传与解压证据
            helper,  # 负责上传与远端 request 执行的 helper
            path_settings,  # 本轮 pytest 使用的 settings overlay
            str_target_server,  # 当前实际执行 pytest 的远端服务器
            path_run_dir,  # 当前 pytest gate 的本地报告目录
            str_remote_workdir,  # 当前服务器探测出的 workspace 根
            path_remote_project_root,  # 远端受治理项目根目录
        )

        # 远端 Python 回归的实际执行根目录来自刚解压的源码快照。
        str_remote_pytest_source_root = str(dict_source_snapshot["remote_source_root"])  # 远端 Python 回归源码快照根

        # 用最小 shell 脚本进入源码快照根并执行对应的 Python 回归。
        str_command_text = _remote_snapshot_python_command(str_result_mode, str_remote_pytest_source_root)  # 远端 Python 回归命令文本

        # 回填 ssh helper 探测出的工作根，后续可区分 overlay 根与仓库根。
        dict_result["remote_workdir"] = str_remote_workdir  # 远端工作区根目录

        # 再记录项目根，便于复核 pytest 实际进入的仓库位置。
        dict_result["remote_project_root"] = path_remote_project_root.as_posix()  # pytest 进入的治理仓库根

        # 记录本轮实际执行 pytest 的源码快照根。
        dict_result["remote_pytest_source_root"] = str_remote_pytest_source_root  # 远端 pytest 当前源码快照根

        # 泛化字段供 smoke 和 pytest 两种模式共同引用同一源码快照根。
        dict_result["remote_source_root"] = str_remote_pytest_source_root  # 远端 Python 回归当前源码快照根

        # 保存源码快照上传、解压和校验证据。
        dict_result["source_snapshot"] = dict_source_snapshot  # 远端 pytest 源码快照证据

        # request 列表上浮到结果顶层，方便 handoff 和 gate 摘要直接定位。
        dict_result["requests"] = list(dict_source_snapshot.get("requests", []))  # 远端 pytest 源码快照 request manifest

        # 保存远端 pytest 的完整命令文本，便于后续复现同一条执行路径。
        dict_result["command"] = str_command_text  # 远端 pytest 实际执行命令

        # 通过固定 bash -lc 包装执行远端 pytest，并保留完整输出。
        str_output = helper.exec(  # 远端 pytest 原始输出
            str_target_server,  # 远端 pytest 目标服务器
            ["bash", "-lc", str_command_text],  # 远端 pytest 执行命令
            settings=path_settings,  # 远端 pytest 使用的 settings overlay
        )

        # 显式回写成功态，避免调用方把默认失败值误判为执行未完成。
        dict_result["status"] = PASS_STATUS  # 远端 pytest 通过状态

        # 成功路径也保留完整终端输出，后续 handoff 和 receipt 可直接引用。
        dict_result["output"] = str_output  # 远端 pytest 完整终端输出

    # 任何远端链路失败都折叠成结构化 failed 结果，避免把 traceback 暴露给调用方。
    except RemoteAcceptanceError as exc:

        # 保留可直接展示的 erie/远端错误信息。
        dict_result["error"] = str(exc)  # 远端 pytest 失败原因

    # 无论通过还是失败，都把结果写入本轮 run 目录供审计。
    _write_report(path_run_dir, dict_result)

    # 返回远端 pytest 的结构化结果。
    return dict_result

# `_has_recovery_target` 判断用户是否要求恢复历史远端运行。
def _has_recovery_target(args: argparse.Namespace) -> bool:
    """判断 CLI 参数是否包含恢复目标。

    参数:
        args: 已解析的 CLI 参数命名空间。

    返回:
        bool: True 表示应进入恢复模式。
    """

    # run id 是首选恢复入口。
    str_recover_run_id = str(getattr(args, "recover_run_id", "") or "").strip()  # 恢复用历史 run id

    # 显式远端目录用于本地 run id 不完整的恢复场景。
    str_recover_remote_run_dir = str(getattr(args, "recover_remote_run_dir", "") or "").strip()  # 恢复用远端目录

    # 任一字段存在就应进入恢复模式。
    return bool(str_recover_run_id or str_recover_remote_run_dir)

# `__getattr__` 替代 wildcard import，延迟委托未显式导入的旧符号。
def __getattr__(name: str) -> Any:
    """从子模块延迟查找 facade 未显式导入的兼容符号。

    参数:
        name: 调用方请求的模块属性名。

    返回:
        Any: 子模块中找到的属性。

    异常:
        AttributeError: 所有委托模块都没有该属性时抛出。
    """

    # 按旧 wildcard import 顺序查找，保持同名符号解析优先级。
    for module_item in tuple_delegate_modules:

        # hasattr 会触发子模块自身的动态属性逻辑。
        if hasattr(module_item, name):

            # 找到后直接返回子模块属性，避免复制大量 facade 全局变量。
            return getattr(module_item, name)

    # 所有委托模块都缺失时按 Python 模块协议抛出 AttributeError。
    raise AttributeError(f"> ERR: [Python] module {__name__!r} has no attribute {name!r}")

# 文件直接执行时转入 CLI main。
if __name__ == "__main__":

    # SystemExit 保持标准 CLI 退出码行为。
    raise SystemExit(main())
