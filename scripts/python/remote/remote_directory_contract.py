"""提供 HLS 远端验收目录契约的共享计算与校验函数。"""

# future 注解延迟解析，保持运行时导入轻量。
from __future__ import annotations

# PurePosixPath 用于生成远端 Linux 风格相对路径。
from pathlib import PurePosixPath
from typing import Any

# config 模块提供远端验收目录模板和归档策略。
from scripts.python.config.hls_config import remote_validation_config

# 公开入口返回远端目录契约配置副本。
def remote_directory_contract() -> dict[str, Any]:
    """读取远端 HLS 验收目录契约。

    参数:
        无。

    返回:
        `remote_validation_config()` 中 directory_contract 的浅拷贝。
    """

    # 返回副本，避免调用方意外修改全局配置缓存。
    return dict(remote_validation_config()["directory_contract"])

# 公开入口根据 run_id 生成远端相对目录布局。
def remote_directory_layout(run_id: str) -> dict[str, str]:
    """生成当前 run_id 对应的远端相对目录布局。

    参数:
        run_id: 远端验收运行编号。

    返回:
        包含项目根、conda 前缀、active run 和 backup run 的相对路径字典。
    """

    # 目录契约包含项目根、conda 前缀和运行目录模板。
    dict_contract: dict[str, Any] = remote_directory_contract()  # 远端目录契约配置

    # active run 模板需要替换当前 run_id。
    str_active_rel: str = _render_run_path(str(dict_contract["active_run_path_template"]), run_id)  # 当前 run 对应的 active 相对目录

    # 归档目录模板同样要注入当前 run_id。
    str_backup_rel: str = _render_run_path(str(dict_contract["backup_run_path_template"]), run_id)  # 当前 run 的归档目录相对路径

    # 返回值字段名是远端验收脚本和 confidence gate 的稳定契约。
    return {
        "run_id": run_id,
        "project_root_relative": str(dict_contract["project_root_dirname"]),
        "conda_prefix_relative": _join(
            str(dict_contract["project_root_dirname"]),
            str(dict_contract["conda_prefix_path"]),
        ),
        "active_run_relative": _join(str(dict_contract["project_root_dirname"]), str_active_rel),
        "backup_run_relative": _join(str(dict_contract["project_root_dirname"]), str_backup_rel),
        "archive_after_verification": str(dict_contract["archive_after_verification"]).lower(),
        "archive_trigger": str(dict_contract["archive_trigger"]),
    }

# 公开入口根据远端 workdir 生成绝对目录布局。
def remote_directory_layout_for_workdir(remote_workdir: str, run_id: str) -> dict[str, str]:
    """生成远端 workdir 下的完整目录布局。

    参数:
        remote_workdir: 远端工作目录绝对路径。
        run_id: 远端验收运行编号。

    返回:
        相对布局字段加上 project_root、conda_prefix、active_run_dir 和 backup_run_dir。
    """

    # 先生成相对布局，绝对路径字段在此基础上拼接 workdir。
    dict_rel: dict[str, str] = remote_directory_layout(run_id)  # 远端相对目录布局

    # 远端项目根固定在 workdir 下的 project_root_relative。
    str_project_root_abs: str = _join(remote_workdir, dict_rel["project_root_relative"])  # 远端项目根绝对路径

    # conda 前缀固定在 workdir 下的 conda_prefix_relative。
    str_conda_prefix_abs: str = _join(remote_workdir, dict_rel["conda_prefix_relative"])  # 远端 conda 前缀绝对路径

    # active run 固定在 workdir 下的 active_run_relative。
    str_active_run_abs: str = _join(remote_workdir, dict_rel["active_run_relative"])  # 远端 active run 绝对路径

    # backup run 绝对路径用于归档已完成的远端验收结果。
    str_backup_run_abs: str = _join(remote_workdir, dict_rel["backup_run_relative"])  # 远端归档目录绝对路径

    # 保留相对字段并追加绝对字段，供远端脚本直接使用。
    return {
        **dict_rel,
        "project_root": str_project_root_abs,
        "conda_prefix": str_conda_prefix_abs,
        "active_run_dir": str_active_run_abs,
        "backup_run_dir": str_backup_run_abs,
    }

# 公开入口校验远端验收结果是否满足目录契约。
def validate_remote_result_contract(result: dict[str, Any]) -> list[str]:
    """校验远端验收结果中的目录字段。

    参数:
        result: 远端验收结果 JSON 字典。

    返回:
        目录契约错误列表；为空表示通过。
    """

    # 收集全部错误，便于 confidence gate 一次性报告。
    list_errors: list[str] = []  # 目录契约错误列表

    # run_id 是计算期望目录布局的关键输入。
    str_run_id: str = str(result.get("run_id") or "").strip()  # 远端运行编号

    # 缺少 run_id 时无法继续计算目录布局。
    if not str_run_id:

        # 保持历史错误文本，避免影响调用方展示和测试断言。
        list_errors.append("missing run_id")

        # 没有 run_id 时后续检查没有可靠基准。
        return list_errors

    # 根据 run_id 计算应返回的远端相对路径。
    dict_expected: dict[str, str] = remote_directory_layout(str_run_id)  # 期望远端相对布局

    # 将结果字段映射到契约中的期望字段。
    dict_checks: dict[str, str] = {
        "remote_project_root": dict_expected["project_root_relative"],  # 结果中的项目根相对路径字段
        "remote_conda_prefix": dict_expected["conda_prefix_relative"],  # 结果中的 conda 前缀相对路径字段
        "remote_run_dir": dict_expected["active_run_relative"],  # 结果中的 active run 相对路径字段
        "remote_backup_dir": dict_expected["backup_run_relative"],  # 结果中的归档目录相对路径字段
    }  # 远端结果字段校验表

    # 逐项比较远端结果和目录契约期望值。
    for str_key, str_expected_value in dict_checks.items():

        # 结果字段必须与相对路径契约完全一致。
        if str(result.get(str_key) or "").strip() != str_expected_value:

            # 保持历史错误文本格式，方便调用方直接展示。
            list_errors.append(f"{str_key} must equal {str_expected_value}")

    # 归档标记必须是真正的 bool True，不能接受 1 或字符串 true。
    bool_archived_after_verification: bool = (
        isinstance(result.get("archived_after_verification"), bool)  # 先要求字段类型严格为 bool
        and result.get("archived_after_verification")  # 再要求字段值本身为 True
    )  # 归档完成标记是否严格为 True

    # 严格布尔检查保留旧版 `is not True` 的语义。
    if not bool_archived_after_verification:

        # 保持历史错误文本，避免影响 confidence gate 输出。
        list_errors.append("archived_after_verification must be true")

    # 返回全部目录契约错误。
    return list_errors

# 内部辅助函数负责把 run_id 渲染进运行目录模板。
def _render_run_path(template: str, run_id: str) -> str:
    """替换目录模板中的 run_id 占位符。

    参数:
        template: 目录契约里记录的运行目录模板。
        run_id: 当前远端验收运行编号。

    返回:
        已经把 run_id 占位符替换完成的相对目录路径。
    """

    # 同时兼容新旧两种 run_id 占位符。
    return template.replace("<run-id>", run_id).replace("__run_id__", run_id)

# 内部辅助函数负责生成远端 POSIX 风格路径。
def _join(left: str, right: str) -> str:
    """拼接远端 POSIX 路径并返回字符串。

    参数:
        left: 远端路径左半部分。
        right: 远端路径右半部分。

    返回:
        使用 POSIX 分隔符拼接后的远端路径字符串。
    """

    # PurePosixPath 避免 Windows 本地分隔符进入远端路径。
    return PurePosixPath(left).joinpath(PurePosixPath(right)).as_posix()
