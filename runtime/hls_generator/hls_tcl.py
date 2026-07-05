"""根据规范与 cfg 条目渲染 run-local Vitis HLS Tcl 脚本。"""

# 兼容后续类型注解语法。
from __future__ import annotations

# tempfile 负责在 run 根目录下创建唯一的临时 HLS project。
import tempfile

# Path 负责路径拼装，Any 用于承载 cfg/spec 的宽类型字段。
from pathlib import Path
from typing import Any

# 复用 cfg 相对路径约束检查，避免 Tcl 中出现越界路径。
from .hls_cfg import cfg_relative_path_issue

# 维护 readiness 阶段的严格先后顺序。
DICT_READINESS_ORDER = {  # readiness 阶段顺序映射
    "static": 0,  # 仅做静态检查
    "compile": 1,  # 允许执行 csim 编译阶段
    "execute": 2,  # 预留执行级阶段位次
    "implement": 3,  # 允许执行 csynth 与报告输出
    "cosim": 4,  # 允许执行 cosim 与 export
}

# Tcl 中当前目录统一写作单个点号。
TCL_CURRENT_DIR = "."  # Tcl 当前目录片段

# Tcl 路径分隔符固定使用正斜杠。
TCL_PATH_SEPARATOR = "/"  # Tcl 路径分隔符

# 报告统一写入 project 下的 report 目录。
REPORT_DIR_NAME = "report"  # Vitis HLS 报告目录名

# 判断当前 readiness 是否已经覆盖目标阶段。
def readiness_at_least(readiness: str, stage: str) -> bool:
    """
    比较 Vitis HLS readiness 阶段顺序。

    :param readiness: 当前验证 readiness 名称。
    :param stage: 需要判断是否已覆盖的目标阶段。
    :return: 当前阶段是否不低于目标阶段。
    """

    # 比较当前阶段与目标阶段的序号关系。
    bool_reaches_stage = DICT_READINESS_ORDER[readiness] >= DICT_READINESS_ORDER[stage]  # 当前 readiness 是否覆盖目标阶段

    # 返回阶段覆盖判断结果。
    return bool_reaches_stage

# 渲染完整的 Vitis HLS Tcl 文本与临时 project 目录。
def render_vitis_hls_tcl(
    spec: dict[str, Any],
    root: Path,
    entries: dict[str, Any],
    readiness: str,
    tcl_config: dict[str, str],
) -> tuple[str, Path]:
    """
    根据 spec 与 cfg 条目生成 Vitis HLS Tcl 文本。

    :param spec: 已校验的 HLS spec 数据。
    :param root: 运行产物所在的 run 根目录。
    :param entries: 规范化后的 hls_config.cfg 条目。
    :param readiness: 目标验证阶段，用于决定是否追加 csim/csynth/cosim/export。
    :param tcl_config: Tcl 渲染过程所需的临时配置。
    :return: Tcl 文本与对应的 HLS project 目录。
    """

    # 解析本次 HLS 运行的顶层函数名。
    str_top = _top_function_name(  # Vitis HLS 顶层函数名
        spec,  # 已校验的 HLS spec 数据
        entries,  # 规范化后的 cfg 条目
    )

    # 创建本次 Tcl 运行专属的临时 project 目录。
    path_project = _create_project_dir(root, tcl_config)  # 供调用方定位本次 Vitis HLS 工程目录并承载后续产物的 Path 对象

    # 解析 project 与 solution 共用的 flow_target 片段。
    str_flow = _flow_option(entries)  # project 与 solution 共用的 flow_target 片段

    # 初始化 open_project 与 set_top 两条基础命令。
    list_lines = _project_open_lines(path_project, str_top, str_flow)  # 按行累积 open_project/set_top 及后续所有 Tcl 命令的可变字符串列表

    # 追加综合源文件与 testbench 文件的 add_files 命令。
    _append_file_lines(list_lines, root, entries)

    # 追加 solution、part 与时钟配置命令。
    _append_solution_setup(list_lines, entries, tcl_config, str_flow)

    # 追加 config_* 与 directive 相关命令。
    _append_config_and_directive_lines(list_lines, entries)

    # 追加 readiness 对应的执行阶段命令。
    _append_readiness_lines(list_lines, entries, readiness, tcl_config)

    # 收尾时显式退出 Vitis HLS Tcl 解释器。
    list_lines.append("exit")

    # 返回 Tcl 文本与 project 目录。
    return "\n".join(list_lines) + "\n", path_project

# 解析 Vitis HLS 需要的顶层函数名。
def _top_function_name(spec: dict[str, Any], entries: dict[str, Any]) -> str:
    """
    根据 cfg、interface 与 spec 信息确定 Vitis HLS 顶层函数名。

    :param spec: 已校验的 HLS spec 数据。
    :param entries: 规范化后的 hls_config.cfg 条目。
    :return: Vitis HLS `set_top` 使用的函数名。
    """

    # 先从 cfg 与 interface 段挑选优先级最高的顶层函数候选。
    raw_top_candidate = entries.get("syn.top") or spec.get("interfaces", {}).get("top_function")  # cfg 或 interface 提供的顶层函数候选

    # 当前两级都未声明时，退回到 spec 名称或默认值。
    if raw_top_candidate is None:

        # 选用 spec 名称或 kernel 作为最终回退候选。
        raw_top_candidate = spec.get("name") or "kernel"  # spec 名称链路的顶层函数回退值

    # 把最终候选统一转换成 set_top 需要的字符串。
    str_top = str(raw_top_candidate)  # 在 syn.top/interface/spec name/default 链路中决出的 Vitis HLS 顶层函数名

    # 返回顶层函数名。
    return str_top

# 在 run 根目录下创建当前 Tcl 使用的临时 project 目录。
def _create_project_dir(root: Path, tcl_config: dict[str, str]) -> Path:
    """
    在 run 根目录下创建当前 Tcl 运行使用的临时 project 目录。

    :param root: 运行产物所在的 run 根目录。
    :param tcl_config: Tcl 渲染过程所需的临时配置。
    :return: 新建的 Vitis HLS project 目录。
    """

    # 调用 tempfile 在 run 根目录下创建带前缀的唯一目录。
    path_project = Path(  # 受 run 根目录约束的临时 project 目录
        tempfile.mkdtemp(  # 生成带前缀的唯一临时目录
            prefix=tcl_config["project_dir_prefix"],  # project 目录名前缀
            dir=root,  # project 目录所属的 run 根目录
        )
    )

    # 返回刚创建好的 project 目录。
    return path_project

# 生成打开 project 与设置顶层函数的初始化命令。
def _project_open_lines(path_project: Path, str_top: str, str_flow: str) -> list[str]:
    """
    生成打开 Vitis project 并设置顶层函数的 Tcl 命令。

    :param path_project: 当前 Tcl 运行的 HLS project 目录。
    :param str_top: `set_top` 使用的函数名。
    :param str_flow: `open_project` 需要的 flow_target 片段。
    :return: Tcl 初始化命令列表。
    """

    # 组织 open_project 与 set_top 两条开场命令。
    list_lines = [  # project 打开与 set_top 命令
        f"open_project -reset{str_flow} {_tcl_quote(path_project.name)}",  # 创建或重置当前 project
        f"set_top {_tcl_quote(str_top)}",  # 指定 HLS 顶层函数
    ]

    # 返回初始化命令列表。
    return list_lines

# 将 cfg 中的源文件与 testbench 文件追加为 add_files 命令。
def _append_file_lines(list_lines: list[str], root: Path, entries: dict[str, Any]) -> None:
    """
    将 cfg 中的源文件与 testbench 文件追加为 `add_files` 命令。

    :param list_lines: 当前构造中的 Tcl 命令列表。
    :param root: 运行产物所在的 run 根目录。
    :param entries: 规范化后的 hls_config.cfg 条目。
    :return: 不返回值，直接扩展 `list_lines`。
    """

    # 读取 source 与 testbench 共用的文件选项。
    dict_file_options = entries.get("files", {})  # source 与 testbench 的公共编译选项

    # 逐个渲染综合源文件的 add_files 命令。
    for raw_source in entries.get("syn.files", []):

        # 基于当前综合源文件拼出 add_files 行文本。
        str_source_line = _add_files_line(  # 当前 syn.files 条目的 add_files 行文本
            root,  # 用于解析 artifact 相对路径的 run 根目录
            str(raw_source),  # 当前 syn.files 条目的原始文件路径
            cflags=dict_file_options.get("cflags"),  # 透传给 add_files -cflags 的公共编译参数
        )

        # 把综合源文件命令写入结果列表。
        list_lines.append(str_source_line)

    # 逐个渲染 testbench 文件的 add_files 命令。
    for raw_testbench in entries.get("tb.files", []):

        # 组合 testbench 这一路独有的 add_files 命令参数。
        str_testbench_line = _add_files_line(  # 最终写入 Tcl 的 testbench add_files 行
            root,  # 交给 _tcl_path_expr 解析相对路径时使用的根目录
            str(raw_testbench),  # tb.files 中记录的原始 testbench 路径文本
            tb=True,  # 强制按 testbench 文件处理
            cflags=dict_file_options.get("cflags"),  # 编译 testbench 时沿用的公共 C/C++ 选项
            csimflags=dict_file_options.get("csimflags"),  # 透传给 add_files -csimflags 的 testbench 参数
        )

        # 把 testbench 文件命令写入结果列表。
        list_lines.append(str_testbench_line)

# 将 solution、器件与时钟配置追加到 Tcl 命令列表。
def _append_solution_setup(
    list_lines: list[str],
    entries: dict[str, Any],
    tcl_config: dict[str, str],
    str_flow: str,
) -> None:
    """
    将 solution、part 与时钟配置追加到 Tcl 命令列表。

    :param list_lines: 当前构造中的 Tcl 命令列表。
    :param entries: 规范化后的 hls_config.cfg 条目。
    :param tcl_config: Tcl 渲染过程所需的临时配置。
    :param str_flow: `open_solution` 需要的 flow_target 片段。
    :return: 不返回值，直接扩展 `list_lines`。
    """

    # 先打开本次运行对应的 solution。
    list_lines.append(f"open_solution -reset{str_flow} {_tcl_quote(tcl_config['solution_name'])}")

    # 仅在 cfg 声明器件 part 时追加 set_part。
    if entries.get("part"):

        # 写入器件型号设置命令。
        list_lines.append(f"set_part {_tcl_quote(str(entries['part']))}")

    # 仅在 cfg 提供时钟周期时追加 create_clock。
    if entries.get("clock"):

        # 写入时钟周期设置命令。
        list_lines.append(f"create_clock -period {entries['clock']}")

    # 仅在 cfg 提供时钟不确定度时追加相关命令。
    if entries.get("clock_uncertainty"):

        # 写入时钟不确定度设置命令。
        list_lines.append(f"set_clock_uncertainty {entries['clock_uncertainty']}")

# 将 config_* 与 directive 相关命令追加到 Tcl 列表。
def _append_config_and_directive_lines(list_lines: list[str], entries: dict[str, Any]) -> None:
    """
    将 cfg 配置段与 directive 列表追加到 Tcl 命令列表。

    :param list_lines: 当前构造中的 Tcl 命令列表。
    :param entries: 规范化后的 hls_config.cfg 条目。
    :return: 不返回值，直接扩展 `list_lines`。
    """

    # 先批量写入 config_* 命令。
    list_lines.extend(_config_lines(entries))

    # 预渲染 csim 段对应的 config_csim 文本。
    str_csim_config_line = _csim_config_line(entries.get("csim", {}))  # 非空时写入的 config_csim 命令

    # 仅在存在 csim 配置时追加对应命令。
    if str_csim_config_line:

        # 写入 config_csim 命令。
        list_lines.append(str_csim_config_line)

    # 基于 cosim 段是否包含全局开关预生成命令文本。
    str_cosim_config_line = _cosim_config_line(entries.get("cosim", {}))  # 仅在存在全局 cosim 开关时写入的命令文本

    # 仅在生成了 cosim 配置文本时写入命令。
    if str_cosim_config_line:

        # 把 cosim 配置文本追加到 Tcl 列表。
        list_lines.append(str_cosim_config_line)

    # 追加所有 set_directive_* 命令。
    list_lines.extend(_directive_lines(entries))

# 按 readiness 追加需要执行的 Vitis HLS 阶段命令。
def _append_readiness_lines(
    list_lines: list[str],
    entries: dict[str, Any],
    readiness: str,
    tcl_config: dict[str, str],
) -> None:
    """
    将 csim、csynth、cosim 与 export 命令按 readiness 追加到 Tcl。

    :param list_lines: 当前构造中的 Tcl 命令列表。
    :param entries: 规范化后的 hls_config.cfg 条目。
    :param readiness: 目标验证阶段。
    :param tcl_config: Tcl 渲染过程所需的临时配置。
    :return: 不返回值，直接扩展 `list_lines`。
    """

    # compile 及以上阶段需要执行 csim。
    if readiness_at_least(readiness, "compile"):

        # 追加 csim_design 命令。
        list_lines.append(_csim_line(entries.get("csim", {})))

    # implement 及以上阶段需要执行 csynth 与报告输出。
    if readiness_at_least(readiness, "implement"):

        # 启动综合实现阶段的主命令。
        list_lines.append("csynth_design")

        # 追加实现阶段的各类 report_* 命令。
        list_lines.extend(_report_lines(tcl_config["solution_name"]))

    # cosim 阶段需要执行协同仿真与导出命令。
    if readiness_at_least(readiness, "cosim"):

        # 启动协同仿真阶段的主命令。
        list_lines.append(_cosim_line(entries.get("cosim", {})))

        # 追加 export 相关命令。
        _append_export_lines(list_lines, entries.get("export", {}))

# 追加 export 阶段所需的配置与导出命令。
def _append_export_lines(list_lines: list[str], dict_export_values: dict[str, str]) -> None:
    """
    将 `config_export` 与 `export_design` 相关命令追加到 Tcl。

    :param list_lines: 当前构造中的 Tcl 命令列表。
    :param dict_export_values: cfg 中的 export 配置段。
    :return: 不返回值，直接扩展 `list_lines`。
    """

    # 先渲染 export 阶段前置的 config_export 命令。
    str_export_config_line = _export_config_line(  # export 阶段前置的 config_export 命令
        dict_export_values,  # export 配置段原始键值
    )

    # 仅在存在 export 前置配置时写入对应命令。
    if str_export_config_line:

        # 把 config_export 命令写入结果列表。
        list_lines.append(str_export_config_line)

    # 再渲染最终的 export_design 命令。
    str_export_line = _export_line(dict_export_values)  # 最终导出所需的 export_design 命令

    # 仅在存在导出配置时写入 export_design。
    if str_export_line:

        # 把真正执行导出的步骤接到 Tcl 队尾。
        list_lines.append(str_export_line)

# 解析 cfg 中的 flow_target 选项。
def _flow_option(entries: dict[str, Any]) -> str:
    """
    从 cfg 条目中解析 Vitis HLS `flow_target` 选项。

    :param entries: 规范化后的 hls_config.cfg 条目。
    :return: 为空字符串或 ` -flow_target xxx` 片段。
    :raises ValueError: flow_target 不是 vivado 或 vitis。
    """

    # 标准化 flow_target 文本，方便后续判断。
    str_flow = str(entries.get("flow_target") or "").strip().lower()  # 归一化后的 flow_target 值

    # 未声明 flow_target 时直接返回空片段。
    if not str_flow:

        # 返回空字符串，保持 Tcl 命令不携带 flow_target。
        return ""

    # 仅允许 vivado 与 vitis 两种 flow_target。
    if str_flow not in {"vivado", "vitis"}:

        # 抛出统一格式的 flow_target 非法错误。
        raise ValueError(f"> ERR: [Python] Unsupported Vitis HLS flow_target {str_flow!r}.")

    # 返回追加到 open_project 与 open_solution 的选项片段。
    return f" -flow_target {str_flow}"

# 把 cfg 配置段渲染为 config_* Tcl 命令。
def _config_lines(entries: dict[str, Any]) -> list[str]:
    """
    将 cfg 配置段转换为 Vitis HLS `config_*` 命令。

    :param entries: 规范化后的 hls_config.cfg 条目。
    :return: 已渲染的 `config_*` Tcl 命令列表。
    """

    # 汇总所有 config_* 命令。
    list_lines: list[str] = []  # 汇总后的 config_* Tcl 命令

    # 标记 true 时只输出 flag 本身的配置项。
    set_flag_only = {  # true 时只输出 flag 本体的布尔配置
        ("compile", "enable_auto_rewind"),  # 自动回绕只需要 flag
        ("compile", "unsafe_math_optimizations"),  # 不安全数学优化只需要 flag
    }

    # 按固定顺序遍历 Vitis HLS 支持的配置段。
    for str_section in ("compile", "schedule", "interface", "rtl", "dataflow"):

        # 读取当前配置段的键值数据。
        dict_values = entries.get(str_section, {})  # 当前配置段的键值数据

        # 跳过空配置段，避免生成无参数命令。
        if not dict_values:

            # 继续处理下一个配置段。
            continue

        # 展开当前配置段需要的参数列表。
        list_args = _config_line_args(  # 当前 config_* 命令参数
            str_section,  # 当前配置段名称
            dict_values,  # 当前配置段中待展开的 option 键值
            set_flag_only,  # flag-only 配置集合
        )

        # 组装当前 config_* 命令并写入结果列表。
        list_lines.append(f"config_{str_section} {' '.join(list_args)}")

    # 返回所有 config_* 命令。
    return list_lines

# 展开单个 config 段对应的 Tcl 参数列表。
def _config_line_args(
    str_section: str,
    dict_values: dict[str, Any],
    set_flag_only: set[tuple[str, str]],
) -> list[str]:
    """
    将单个 config 段的键值对转换为 Tcl 参数列表。

    :param str_section: 当前 config 段名称。
    :param dict_values: 当前 config 段键值对。
    :param set_flag_only: 值为 true 时只输出 flag 的参数集合。
    :return: 当前 config 段对应的 Tcl 参数列表。
    """

    # 汇总当前 config 段展开后的参数。
    list_args: list[str] = []  # 当前 config 段最终拼出的 Tcl 参数

    # 逐个处理当前配置段中的 option 键值对。
    for str_key, raw_config_value in dict_values.items():

        # 追加当前 option 展开后的参数片段。
        list_args.extend(_config_value_args(str_section, str_key, raw_config_value, set_flag_only))

    # 返回当前配置段的完整参数列表。
    return list_args

# 渲染单个 config option 对应的 Tcl 参数片段。
def _config_value_args(
    str_section: str,
    str_key: str,
    raw_config_value: Any,
    set_flag_only: set[tuple[str, str]],
) -> list[str]:
    """
    生成单个 config 选项对应的 Tcl flag 与取值片段。

    :param str_section: 当前 config 段名称。
    :param str_key: 当前 option 名称。
    :param raw_config_value: cfg 中记录的原始 option 值。
    :param set_flag_only: 值为 true 时只输出 flag 的参数集合。
    :return: 当前 option 对应的 Tcl 参数片段。
    """

    # 组装当前配置项的 Tcl flag 名称。
    str_flag = f"-{str_key}"  # 当前配置项对应的 Tcl flag

    # 把原始配置值统一转换成字符串。
    str_value = str(raw_config_value)  # 当前配置项的字符串化值

    # 对 flag-only 配置项在 true 时只保留 flag 本体。
    if str_value.lower() == "true" and (str_section, str(str_key)) in set_flag_only:

        # 返回只包含 flag 的参数列表。
        return [str_flag]

    # 对 false 值显式输出 false 文本，保持 Tcl 语义稳定。
    if str_value.lower() == "false":

        # 返回 flag 与 false 文本。
        return [str_flag, "false"]

    # 返回 flag 与普通字符串值。
    return [str_flag, str_value]

# 渲染单条 add_files Tcl 命令。
def _add_files_line(
    root: Path,
    path: str,
    *,
    tb: bool = False,
    cflags: str | None = None,
    csimflags: str | None = None,
) -> str:
    """
    渲染 Vitis HLS `add_files` 命令。

    :param root: artifact 根目录。
    :param path: cfg 中声明的源文件或 testbench 路径。
    :param tb: 是否按 testbench 文件追加。
    :param cflags: 可选 C/C++ 编译参数。
    :param csimflags: 可选 csim 编译参数。
    :return: `add_files` Tcl 命令。
    """

    # 以 add_files 作为命令头初始化参数列表。
    list_args = ["add_files"]  # add_files 命令的参数序列

    # 仅在 testbench 文件场景下追加 -tb。
    if tb:

        # 把 testbench 标记写入参数列表。
        list_args.append("-tb")

    # 仅在 cfg 提供 cflags 时写入对应参数。
    if cflags:

        # 把 cflags 选项与转义后的值写入参数列表。
        list_args.extend(["-cflags", _tcl_quote(str(cflags))])

    # 仅在 cfg 单独声明 csimflags 时透传给 csim 阶段。
    if csimflags:

        # 追加 csim 阶段专用的编译参数。
        list_args.extend(["-csimflags", _tcl_quote(str(csimflags))])

    # 追加源文件或 testbench 的 Tcl 路径表达式。
    list_args.append(_tcl_path_expr(root, path))

    # 返回完整的 add_files 命令。
    return " ".join(list_args)

# 把 cfg 中的文件路径渲染为 Tcl 可执行表达式。
def _tcl_path_expr(root: Path, path: str) -> str:
    """
    渲染 Tcl 文件路径表达式，支持 glob 通配符。

    :param root: artifact 根目录。
    :param path: cfg 中记录的源文件路径。
    :return: Tcl quote 后的路径表达式或 `glob -nocomplain` 表达式。
    :raises ValueError: 路径违反 cfg 允许的相对路径约束。
    """

    # 先执行 cfg 约束定义的相对路径安全检查。
    str_issue = cfg_relative_path_issue(path)  # cfg 路径安全检查结果

    # 路径不合法时立即抛出统一格式错误。
    if str_issue:

        # 抛出带标准前缀的路径校验异常。
        raise ValueError(f"> ERR: [Python] Invalid cfg path: {str_issue}")

    # 把路径解析成 Tcl 需要的绝对 POSIX 形式。
    str_resolved = (root / path).resolve().as_posix()  # 转换后的绝对 POSIX 路径

    # 对 glob 模式生成 Tcl 的 glob -nocomplain 表达式。
    if any(str_marker in path for str_marker in ("*", "?", "[")):

        # 返回 glob 路径表达式。
        return f"[glob -nocomplain {_tcl_quote(str_resolved)}]"

    # 对普通路径直接返回 Tcl quote 结果。
    return _tcl_quote(str_resolved)

# 把 cfg 中的 directives 列表渲染为 set_directive_* 命令。
def _directive_lines(entries: dict[str, Any]) -> list[str]:
    """
    将 cfg 中的 directives 列表转换为 Tcl directive 命令。

    :param entries: 规范化后的 hls_config.cfg 条目。
    :return: `set_directive_*` 命令列表。
    """

    # 汇总 directives 转换后的 Tcl 命令。
    list_lines: list[str] = []  # directives 转换后的 Tcl 命令列表

    # 逐个处理 cfg 中的 directive 配置项。
    for dict_directive in entries.get("directives", []):

        # 读取 directive 名称后缀。
        str_name = str(dict_directive["name"])  # directive 名称后缀

        # 读取 directive 目标位置。
        str_location = str(dict_directive.get("location") or "")  # directive 目标位置

        # 按原顺序收集 directive 参数片段。
        list_args = [str(raw_item) for raw_item in dict_directive.get("args", [])]  # directive 参数片段列表

        # 仅在存在参数时追加前导空格。
        str_suffix = (" " + " ".join(list_args)) if list_args else ""  # directive 参数拼接后的后缀文本

        # 组装完整的 set_directive_* 命令。
        list_lines.append(f"set_directive_{str_name}{str_suffix} {_tcl_quote(str_location)}")

    # 返回全部 directive 命令。
    return list_lines

# 根据 csim 配置渲染 csim_design 命令。
def _csim_line(values: dict[str, str]) -> str:
    """
    根据 csim 配置渲染 `csim_design` 命令。

    :param values: cfg 中的 csim 配置段。
    :return: `csim_design` Tcl 命令。
    """

    # 汇总 csim_design 允许出现的运行参数。
    list_args: list[str] = []  # 汇总 csim_design 的清理、编译与 argv 参数

    # clean=true 时追加 -clean。
    if str(values.get("clean", "")).lower() == "true":

        # 追加清理旧构建目录的开关。
        list_args.append("-clean")

    # 当用户只想编译 testbench 而不运行时启用 compile_only。
    if str(values.get("compile_only", "")).lower() == "true":

        # 追加只编译不运行 testbench 的开关。
        list_args.append("-compile_only")

    # O 或 o=true 时追加 -O。
    if str(values.get("O", values.get("o", ""))).lower() == "true":

        # 追加编译优化开关。
        list_args.append("-O")

    # argv 存在时把 testbench 运行参数透传给 csim。
    if values.get("argv"):

        # 追加 argv 选项与转义后的参数文本。
        list_args.extend(["-argv", _tcl_quote(str(values["argv"]))])

    # 把累计的 csim 参数拼成最终命令文本。
    return "csim_design" + ((" " + " ".join(list_args)) if list_args else "")

# 把 csim 配置中的链接参数转换成 config_csim 命令。
def _csim_config_line(values: dict[str, str]) -> str:
    """
    根据 csim 配置渲染 `config_csim` 命令。

    :param values: cfg 中的 csim 配置段。
    :return: `config_csim` 命令；无参数时为空字符串。
    """

    # 汇总 config_csim 需要透传给链接阶段的参数。
    list_args: list[str] = []  # 汇总 config_csim 的链接参数片段

    # ldflags 存在时写入对应选项。
    if values.get("ldflags"):

        # 把链接参数作为 ldflags 透传给 config_csim。
        list_args.extend(["-ldflags", _tcl_quote(str(values["ldflags"]))])

    # 返回 config_csim 命令或空字符串。
    return "config_csim " + " ".join(list_args) if list_args else ""

# 把 cosim 配置中的全局开关转换成 config_cosim 命令。
def _cosim_config_line(values: dict[str, str]) -> str:
    """
    根据 cosim 配置渲染 `config_cosim` 命令。

    :param values: cfg 中的 cosim 配置段。
    :return: `config_cosim` 命令；无参数时为空字符串。
    """

    # 汇总 config_cosim 允许透传的协同仿真参数。
    list_args: list[str] = []  # 汇总 config_cosim 的协同仿真参数

    # 仅在显式声明时透传 enable_tasks_with_m_axi。
    if values.get("enable_tasks_with_m_axi"):

        # 追加 m_axi task 相关的协同仿真开关。
        list_args.extend(["-enable_tasks_with_m_axi", str(values["enable_tasks_with_m_axi"])])

    # 把累计的 cosim 全局参数拼成最终命令文本。
    return "config_cosim " + " ".join(list_args) if list_args else ""

# 把 cosim 运行参数转换成 cosim_design 命令。
def _cosim_line(values: dict[str, str]) -> str:
    """
    根据 cosim 配置渲染 `cosim_design` 命令。

    :param values: cfg 中的 cosim 配置段。
    :return: `cosim_design` Tcl 命令。
    """

    # 汇总 cosim_design 允许出现的仿真参数。
    list_args: list[str] = []  # 汇总 cosim_design 的 RTL、trace 与调试参数

    # 逐个处理需要带值的 cosim 选项。
    for str_key in ("rtl", "tool", "trace_level"):

        # 仅在 cfg 给出取值时写入当前选项。
        if values.get(str_key):

            # 追加带值的 cosim 参数。
            list_args.extend([f"-{str_key}", str(values[str_key])])

    # 逐个处理只在 true 时出现的布尔开关。
    for str_key in ("wave_debug", "random_stall"):

        # 仅在显式为 true 时写入当前 flag。
        if str(values.get(str_key, "")).lower() == "true":

            # 追加当前布尔开关。
            list_args.append(f"-{str_key}")

    # 返回带 RTL、trace 与调试开关的 cosim_design 命令文本。
    return "cosim_design" + ((" " + " ".join(list_args)) if list_args else "")

# 把 export 配置转换成 export_design 命令。
def _export_line(values: dict[str, str]) -> str:
    """
    根据 export 配置渲染 `export_design` 命令。

    :param values: cfg 中的 export 配置段。
    :return: `export_design` 命令；无参数时为空字符串。
    """

    # 没有 export 配置时直接返回空命令。
    if not values:

        # 返回空字符串，表示无需执行 export_design。
        return ""

    # 汇总 export_design 的导出格式与元数据参数。
    list_args: list[str] = []  # 汇总 export_design 的格式、RTL 与元数据参数

    # 按固定顺序遍历 export 允许的字段。
    for str_key in ("format", "rtl", "vendor", "library", "version", "display_name"):

        # 仅在字段存在时把它渲染进命令。
        if values.get(str_key):

            # 把当前字段值统一转换成字符串。
            str_value = str(values[str_key])  # 当前 export 字段的字符串值

            # 对 vendor、library、version 与 display_name 使用 Tcl quote。
            if str_key in {"vendor", "library", "version", "display_name"}:

                # 追加需要 Tcl quote 的元数据字段。
                list_args.extend([f"-{str_key}", _tcl_quote(str_value)])

            # 对 format 与 rtl 直接透传原始字符串值。
            else:

                # 追加不需要 Tcl quote 的控制字段。
                list_args.extend([f"-{str_key}", str_value])

    # 把累计的 export 字段拼成最终命令文本。
    return "export_design" + ((" " + " ".join(list_args)) if list_args else "")

# 把 export 前置配置转换成 config_export 命令。
def _export_config_line(values: dict[str, str]) -> str:
    """
    根据 export 配置渲染 `config_export` 命令。

    :param values: cfg 中的 export 配置段。
    :return: `config_export` 命令；无参数时为空字符串。
    """

    # 汇总 config_export 需要的策略与 XDC 参数。
    list_args: list[str] = []  # 汇总 config_export 的策略与 XDC 参数

    # 按固定顺序处理 config_export 支持的字段。
    for str_key in ("vivado_synth_strategy", "ip_xdc_file"):

        # 仅在字段存在时追加对应参数。
        if values.get(str_key):

            # 追加字段名与转义后的字段值。
            list_args.extend([f"-{str_key}", _tcl_quote(str(values[str_key]))])

    # 把累计的 export 前置参数拼成最终命令文本。
    return "config_export" + ((" " + " ".join(list_args)) if list_args else "")

# 生成实现阶段需要输出的各类 report_* 命令。
def _report_lines(solution_name: str) -> list[str]:
    """
    生成 csynth 后需要输出的报告命令。

    :param solution_name: 当前 Vitis HLS solution 名称。
    :return: report 目录创建与各类 `report_*` 命令列表。
    """

    # 计算 report 目录在 Tcl 中的相对路径。
    str_report_dir = TCL_CURRENT_DIR + TCL_PATH_SEPARATOR + REPORT_DIR_NAME  # report 目录的 Tcl 相对路径

    # 计算报告文件统一复用的路径前缀。
    str_report_prefix = str_report_dir + TCL_PATH_SEPARATOR + solution_name  # report 文件路径公共前缀

    # 返回实现阶段需要输出的全部报告命令。
    return [
        f"file mkdir {str_report_dir}",
        f"report_utilization -file {str_report_prefix}_utilization.rpt",
        f"report_timing -file {str_report_prefix}_timing.rpt",
        f"report_directive -file {str_report_prefix}_directive.rpt",
        f"report_dataflow -file {str_report_prefix}_dataflow.rpt",
        f"report_interface -file {str_report_prefix}_interface.rpt",
    ]

# 对普通字符串做 Tcl 安全引用。
def _tcl_quote(value: str) -> str:
    """
    将字符串转换为 Vitis Tcl 安全引用。

    :param value: 需要写入 Tcl 的普通字符串。
    :return: 大括号包裹后的 Tcl 字符串。
    """

    # 转义 Tcl 字面量中会破坏大括号结构的字符。
    str_escaped = value.replace("}", "\\}")  # 转义 Tcl 字面量中的右大括号

    # 返回大括号包裹后的 Tcl 安全文本。
    return "{" + str_escaped + "}"
