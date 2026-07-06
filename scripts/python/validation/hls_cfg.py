"""解析并规范化 Vitis HLS cfg 配置文本。"""

# 启用延迟注解，避免运行期提前求值类型标注。
from __future__ import annotations

# 正则表达式用于识别 section 标题和时钟周期文本。
import re

# PurePath 帮助统一检查跨平台相对路径约束。
from pathlib import PurePosixPath, PureWindowsPath

# Any 描述 cfg 解析结果中的异构字段值。
from typing import Any

# Vitis 规则白名单负责校验 section、option 和 directive 名称。
from scripts.python.validation.vitis_rules import (
    require_allowed_config_option,
    require_allowed_config_section,
    require_allowed_directive,
)

# 汇总 Vitis HLS cfg 允许出现的 section 名称。
KNOWN_SECTIONS = {
    "hls",  # 全局器件与顶层函数配置段
    "files",  # 源文件和 testbench 输入配置段
    "compile",  # 编译期开关配置段
    "interface",  # 接口 pragma 配置段
    "rtl",  # RTL 导出相关配置段
    "dataflow",  # DATAFLOW 调度配置段
    "schedule",  # 调度策略配置段
    "csim",  # C 仿真参数配置段
    "cosim",  # 联合仿真参数配置段
    "export",  # 导出工件参数配置段
    "directive",  # 指令条目配置段
}  # parse_hls_cfg_entries 认可的全部 section 名称集合

# 这些 section 需要先规范化 section 名称，再校验 option 名称。
STRUCTURED_OPTION_SECTIONS = {"compile", "interface", "rtl", "dataflow", "schedule"}  # 结构化 section 集合

# 这些 section 直接沿用原 section 名称，只校验 option 白名单。
DIRECT_OPTION_SECTIONS = {"csim", "cosim", "export"}  # 直接 option section 集合

# 提供 cfg 文本到结构化条目的主解析入口。
def parse_hls_cfg_entries(cfg_text: str) -> dict[str, Any]:
    """
    解析 syn.* 兼容配置和分段式 Vitis HLS cfg 配置。

    参数:
        cfg_text: hls_config.cfg 的原始文本，包含 legacy syn.* 字段或新版 [section] 键值对。

    返回:
        规范化后的 cfg 条目字典，包含文件列表、全局配置、directive 列表、parse_errors 和 raw_sections。

    异常:
        无显式异常抛出；解析阶段遇到的白名单或格式问题会写入 parse_errors 字段。
    """

    # 初始化向下游暴露的统一解析结果容器。
    dict_entries = _empty_entries()  # 规范化 cfg 条目集合

    # 未显式声明 section 的键值对默认归入全局 hls 段。
    str_section = "hls"  # 当前正在接收条目的 section 名称

    # 逐行扫描原始 cfg 文本，兼容 legacy 和 section 化配置写法。
    for str_raw_line in cfg_text.splitlines():

        # 去掉行内注释并裁剪首尾空白，便于识别真实配置语义。
        str_line = _strip_comment(str_raw_line).strip()  # 当前有效 cfg 行文本

        # 跳过空行和纯注释行，避免写入无效条目。
        if not str_line:

            # 当前行没有可解析内容，继续处理下一行输入。
            continue

        # 尝试把当前行识别为 [section] 标题。
        str_section_from_line = _section_name(str_line)  # 当前行声明的 section 名称

        # 命中 section 标题时切换后续键值对的归属位置。
        if str_section_from_line:

            # 更新当前解析游标，后续 key=value 将写入该 section。
            str_section = str_section_from_line  # 当前生效的 section 名称

            # 预留 raw_sections 子字典，便于后续保留原始输入痕迹。
            dict_entries["raw_sections"].setdefault(str_section, {})

            # section 标题本身不再参与键值对解析。
            continue

        # 只接受显式 key=value 形式的配置行。
        if "=" not in str_line:

            # 非键值对内容不纳入结构化结果，保持旧行为静默跳过。
            continue

        # 仅按首个等号拆分，保留值侧可能继续出现的等号内容。
        tuple_key_value = _split_key_value(str_line)  # 当前配置行拆分出的键和值

        # 读取配置键名，空键名后续会直接丢弃。
        str_key = tuple_key_value[0]  # 当前配置键名

        # 读取配置值文本，保留值中的原始空格以维持兼容性。
        str_value = tuple_key_value[1]  # 当前配置值文本

        # 忽略空键名，避免写入无意义字段。
        if not str_key:

            # 当前行缺少合法键名，不向结果容器写入任何内容。
            continue

        # 把当前键值对路由到对应 section 的规范化存储逻辑。
        _store_entry(dict_entries, str_section, str_key, str_value)

    # 返回供后续验证和生成流程复用的 cfg 解析结果。
    return dict_entries

# 把时钟字段统一转换为 ns 数值。
def clock_period_ns(value: Any) -> float | None:
    """
    解析 Vitis HLS cfg 中的时钟周期字段。

    参数:
        value: cfg 中读取到的 clock 字段值，允许为数字文本、带 ns 后缀文本或空值。

    返回:
        成功解析时返回时钟周期的 ns 数值；空值或格式不匹配时返回 None。

    异常:
        无显式异常抛出；格式不匹配会直接返回 None。
    """

    # 空值不参与数值换算，沿用 None 语义表示缺失。
    if value in (None, ""):

        # 调用方可据此继续使用默认时钟约束或上游诊断。
        return None

    # 收集正则命中的时钟数值文本，兼容可选 ns 后缀。
    list_clock_values = re.findall(  # 从 clock 文本中提取可换算的数值片段
        r"^\s*(\d+(?:\.\d+)?)\s*(?:ns)?\s*$",  # 匹配可选 ns 后缀的数值文本
        str(value),  # 转成统一字符串后参与正则匹配
        flags=re.IGNORECASE,  # 忽略 ns 大小写差异
    )  # 时钟周期数值候选列表

    # 输出首个匹配结果，对无匹配输入保持 None 兼容语义。
    return float(list_clock_values[0]) if list_clock_values else None

# 检查 cfg 中的文件路径是否满足相对路径边界。
def cfg_relative_path_issue(path: str) -> str | None:
    """
    检查 hls_config.cfg 中声明的文件路径是否安全。

    参数:
        path: cfg 中声明的源文件、testbench 或其他文件路径文本。

    返回:
        路径非法时返回诊断文本；路径可接受时返回 None。

    异常:
        无显式异常抛出；所有路径问题都通过返回诊断字符串表达。
    """

    # 统一为正斜杠文本，便于识别空段和父目录跳转。
    str_normalized_path = str(path).replace("\\", "/")  # 归一化后的路径文本

    # 借助 POSIX 语义专门捕捉 ../、./ 和空段这类跳转片段。
    path_posix = PurePosixPath(str_normalized_path)  # POSIX 语义路径对象

    # 借助 Windows 语义额外识别盘符和绝对盘路径。
    path_windows = PureWindowsPath(str(path))  # 用于识别盘符和绝对盘路径的 Windows 语义对象

    # 拒绝绝对路径和带盘符路径，防止越出生成工件目录。
    if path_posix.is_absolute() or path_windows.is_absolute() or path_windows.drive:

        # 返回给调用方的诊断文本保持现有英文协议，避免影响上游断言。
        return f"HLS cfg file path must be relative and stay inside generated artifacts: {path}"

    # 拒绝空段、当前目录段和父目录段，防止路径逃逸。
    if any(str_part in {"", ".", ".."} for str_part in path_posix.parts):

        # 返回精确原因，帮助上游指出具体路径违规方式。
        return f"HLS cfg file path must not contain empty, current, or parent path segments: {path}"

    # 路径未触发边界问题时返回 None，表示当前值可接受。
    return None

# 构造下游统一依赖的空解析结果结构。
def _empty_entries() -> dict[str, Any]:
    """
    创建 cfg 解析结果容器。

    参数:
        无外部业务参数；函数直接返回新的空结果字典。

    返回:
        包含文件列表、分段配置、directive 列表、parse_errors 和 raw_sections 的空结果字典。

    异常:
        无显式异常抛出。
    """

    # 返回与下游调用契约一致的完整初始字段集合。
    return {
        "syn.files": [],  # legacy 源文件列表
        "tb.files": [],  # legacy testbench 文件列表
        "files": {},
        "compile": {},
        "interface": {},
        "rtl": {},
        "dataflow": {},
        "schedule": {},
        "csim": {},
        "cosim": {},
        "export": {},
        "directives": [],  # directive 条目列表
        "parse_errors": [],  # 解析阶段错误列表
        "raw_sections": {},  # 原始 section/key/value 记录
    }

# 从单行文本中提取 [section] 标题名称。
def _section_name(str_line: str) -> str:
    """
    读取 cfg section 标题。

    参数:
        str_line: 去注释后的 cfg 行文本。

    返回:
        当前行是 section 标题时返回小写 section 名称；否则返回空字符串。

    异常:
        无显式异常抛出。
    """

    # 捕获方括号中的 section 名称，限制为 Vitis cfg 常见字符集。
    list_section_names = re.findall(r"^\[([A-Za-z0-9_.-]+)\]$", str_line)  # section 名称候选列表

    # 返回规范化后的 section 名称，未命中时输出空字符串。
    return list_section_names[0].strip().lower() if list_section_names else ""

# 按首个等号拆分 cfg 键值对。
def _split_key_value(str_line: str) -> tuple[str, str]:
    """
    拆分包含等号的 cfg 配置行。

    参数:
        str_line: 已确认包含等号的 cfg 行文本。

    返回:
        去掉边界空白后的 key 和 value 二元组。

    异常:
        无显式异常抛出；调用方保证输入行至少包含一个等号。
    """

    # 只拆分首个等号，保持值侧原始内容不被过度切碎。
    list_key_value = [str_item.strip() for str_item in str_line.split("=", 1)]  # cfg 键值片段列表

    # 返回给主流程继续执行 section 路由和字段写入。
    return list_key_value[0], list_key_value[1]

# 按 section 和键名语义把条目写入结果容器。
def _store_entry(dict_entries: dict[str, Any], section: str, key: str, value: str) -> None:
    """
    把单个 cfg 条目写入规范化结果。

    参数:
        dict_entries: cfg 解析结果容器。
        section: 当前条目所在的 section 名称。
        key: 当前条目的原始键名。
        value: 当前条目的原始值文本。

    返回:
        无业务返回值；结果直接写入 dict_entries。

    异常:
        无显式异常抛出；白名单问题通过 parse_errors 记录。
    """

    # 统一把键名转为小写，避免大小写差异扩散到后续逻辑。
    str_lower_key = key.lower()  # 小写化后的 cfg 键名

    # 先保留原始 section/key/value 关系，供诊断和兼容检查复用。
    _remember_raw_entry(dict_entries, section, str_lower_key, value)

    # 未知 section 直接登记错误并停止当前条目处理。
    if _reject_unknown_section(dict_entries, section):

        # 当前条目已被未知 section 规则拦截，无需继续路由。
        return

    # 优先识别 legacy 文件字段和 files section 特殊键。
    if _store_file_entry(dict_entries, section, str_lower_key, value):

        # 文件类条目已经落入目标字段，当前流程到此结束。
        return

    # 再处理 syn.top、clock、part 等全局 HLS 配置项。
    if _store_global_hls_entry(dict_entries, section, str_lower_key, value):

        # 全局配置项已经写入结果，不再进入后续 section 分支。
        return

    # 继续处理 compile/interface/rtl 等分段 option。
    if _store_section_option(dict_entries, section, str_lower_key, value):

        # 当前条目已被对应 section 处理完成。
        return

    # 仅 directive section 会落入 directive 解析分支。
    if section == "directive":

        # directive 分支在这里完成最终的结构化入表。
        _store_directive_entry(dict_entries, str_lower_key, value)

# 记录原始 section、键名和值的对应关系。
def _remember_raw_entry(dict_entries: dict[str, Any], section: str, str_lower_key: str, value: str) -> None:
    """
    保存原始 section/key/value 轨迹。

    参数:
        dict_entries: cfg 解析结果容器。
        section: 当前条目所在的 section 名称。
        str_lower_key: 已经小写化的键名。
        value: 当前条目的原始值文本。

    返回:
        无业务返回值；结果直接追加到 dict_entries["raw_sections"]。

    异常:
        无显式异常抛出。
    """

    # 逐级创建 raw_sections 容器，保证原始输入顺序可追溯。
    dict_entries["raw_sections"].setdefault(section, {}).setdefault(str_lower_key, []).append(value)

# 判断当前 section 是否在支持范围内。
def _reject_unknown_section(dict_entries: dict[str, Any], section: str) -> bool:
    """
    检查 section 是否属于 Vitis HLS 白名单。

    参数:
        dict_entries: cfg 解析结果容器。
        section: 当前条目所在的 section 名称。

    返回:
        section 受支持时返回 False；section 未知时记录错误并返回 True。

    异常:
        无显式异常抛出。
    """

    # 已知 section 直接放行给后续路由逻辑处理。
    if section in KNOWN_SECTIONS:

        # 当前 section 合法，调用方可以继续处理当前条目。
        return False

    # 将未知 section 错误记录到 parse_errors，保持无异常兼容行为。
    dict_entries["parse_errors"].append(f"Unsupported Vitis HLS cfg section {section!r}.")

    # 告知调用方当前条目应立即停止后续路由。
    return True

# 处理源文件、testbench 和 files section 相关字段。
def _store_file_entry(dict_entries: dict[str, Any], section: str, str_lower_key: str, value: str) -> bool:
    """
    尝试写入 cfg 文件类条目。

    参数:
        dict_entries: cfg 解析结果容器。
        section: 当前条目所在的 section 名称。
        str_lower_key: 已经小写化的键名。
        value: 当前条目的原始值文本。

    返回:
        当前条目属于文件类配置时返回 True；否则返回 False。

    异常:
        无显式异常抛出。
    """

    # 识别 legacy syn.file 和 files section 中的源文件键。
    if str_lower_key == "syn.file" or (section == "files" and str_lower_key in {"src", "source"}):

        # 向 legacy 源文件列表追加不重复路径。
        _append_unique(dict_entries["syn.files"], value)

        # 保留首个 syn.file 兼容字段，满足旧调用方读取路径的方式。
        dict_entries.setdefault("syn.file", value)

        # 告知调用方当前条目已经按源文件语义处理完毕。
        return True

    # 识别 legacy tb.file 和 files section 中的 testbench 键。
    if str_lower_key == "tb.file" or (section == "files" and str_lower_key in {"tb", "testbench"}):

        # 向 legacy testbench 列表追加不重复路径。
        _append_unique(dict_entries["tb.files"], value)

        # 保留首个 tb.file 兼容字段，维持旧结果结构。
        dict_entries.setdefault("tb.file", value)

        # testbench 路径已经落盘，不再向后续分支继续传播。
        return True

    # 识别 files section 中的编译参数类键名。
    if section == "files" and str_lower_key in {"cflags", "csimflags"}:

        # 把文件级编译参数收纳到 files 子字典。
        dict_entries["files"][str_lower_key] = value  # files section 编译参数值

        # 告知调用方当前条目已经被 files section 捕获。
        return True

    # 当前条目不属于文件类配置，继续交给其他路由分支。
    return False

# 处理全局 HLS section 和 legacy syn.* 全局字段。
def _store_global_hls_entry(dict_entries: dict[str, Any], section: str, str_lower_key: str, value: str) -> bool:
    """
    尝试写入顶层 HLS 配置项。

    参数:
        dict_entries: cfg 解析结果容器。
        section: 当前条目所在的 section 名称。
        str_lower_key: 已经小写化的键名。
        value: 当前条目的原始值文本。

    返回:
        当前条目属于顶层 HLS 配置时返回 True；否则返回 False。

    异常:
        无显式异常抛出。
    """

    # 兼容 legacy syn.top 和 hls section 中的 top 键。
    if str_lower_key == "syn.top" or (section == "hls" and str_lower_key == "top"):

        # 保存顶层函数名，供后续 Tcl 渲染和验证逻辑复用。
        dict_entries["syn.top"] = value  # HLS 顶层函数名

        # 告知调用方当前条目已经作为全局 top 字段落盘。
        return True

    # 捕获顶层设备和时序相关公共字段。
    if str_lower_key in {"part", "clock", "flow_target", "clock_uncertainty"}:

        # 直接把全局字段写入结果顶层，保持旧结构兼容。
        dict_entries[str_lower_key] = value  # HLS 全局配置项值

        # 告知调用方当前条目已经被全局字段分支接收。
        return True

    # 当前条目不属于顶层 HLS 配置，继续交给其他处理器。
    return False

# 根据 section 类型处理带白名单的 option。
def _store_section_option(dict_entries: dict[str, Any], section: str, str_lower_key: str, value: str) -> bool:
    """
    尝试写入分段式 cfg option。

    参数:
        dict_entries: cfg 解析结果容器。
        section: 当前条目所在的 section 名称。
        str_lower_key: 已经小写化的键名。
        value: 当前条目的原始值文本。

    返回:
        当前条目属于受支持的 section option 时返回 True；否则返回 False。

    异常:
        无显式异常抛出；白名单问题由下游 helper 记录到 parse_errors。
    """

    # 结构化 section 需要同时规范化 section 名称和 option 名称。
    if section in STRUCTURED_OPTION_SECTIONS:

        # 把结构化 section 的 option 写入对应子字典。
        _store_structured_option(dict_entries, section, str_lower_key, value)

        # 告知调用方当前条目已经由结构化 section 分支接管。
        return True

    # 直接 section 仅需要校验 option 名称。
    if section in DIRECT_OPTION_SECTIONS:

        # 把 direct option 写入 csim、cosim 或 export 子字典。
        _store_direct_option(dict_entries, section, str_lower_key, value)

        # 告知调用方当前条目已经由 direct section 分支处理。
        return True

    # 当前 section 不在 option 白名单处理范围内。
    return False

# 校验并写入 compile/interface/rtl/dataflow/schedule option。
def _store_structured_option(dict_entries: dict[str, Any], section: str, str_lower_key: str, value: str) -> None:
    """
    写入需要 section 规范化的 cfg option。

    参数:
        dict_entries: cfg 解析结果容器。
        section: 当前条目所在的 section 名称。
        str_lower_key: 已经小写化的键名。
        value: 当前条目的原始值文本。

    返回:
        无业务返回值；成功时直接写入目标 section 子字典。

    异常:
        无显式异常抛出；白名单校验失败会转写到 parse_errors。
    """

    # 通过白名单同时规范化 section 和 option 名称。
    try:

        # 先规范化 section 名称，避免别名和大小写差异进入结果。
        str_normalized_section = require_allowed_config_section(section)  # 规范化后的 section 名称

        # 再在目标 section 下校验 option 名称是否合法。
        str_normalized_key = require_allowed_config_option(  # 当前 section 下规范化后的合法 option 名称
            str_normalized_section,  # 当前命中的结构化 section 名称
            str_lower_key,  # 当前待校验的 option 键名
        )

    # structured option 校验失败时，把异常降级为 parse_errors 记录。
    except ValueError as exc:

        # 记录本次结构化 option 的白名单违规原因。
        dict_entries["parse_errors"].append(str(exc))

        # 当前 option 校验失败后不再写入结果子字典。
        return

    # 把规范化后的 option 写入对应 section 子字典。
    dict_entries[str_normalized_section][str_normalized_key] = value  # 结构化 section 的最终 option 值

# 校验并写入 csim、cosim 和 export option。
def _store_direct_option(dict_entries: dict[str, Any], section: str, str_lower_key: str, value: str) -> None:
    """
    写入直接使用 section 名称的 cfg option。

    参数:
        dict_entries: cfg 解析结果容器。
        section: 当前条目所在的 section 名称。
        str_lower_key: 已经小写化的键名。
        value: 当前条目的原始值文本。

    返回:
        无业务返回值；成功时直接写入 section 子字典。

    异常:
        无显式异常抛出；白名单校验失败会转写到 parse_errors。
    """

    # 在保持 section 原名的前提下校验 option 名称是否合法。
    try:

        # 直接 section 只需要校验当前键名是否可作为该段 option。
        str_normalized_key = require_allowed_config_option(section, str_lower_key)  # 该 direct section 对应的合法 option 名称

    # 捕获 option 白名单失败并保留错误文本。
    except ValueError as exc:

        # 登记当前 direct option 的校验失败原因。
        dict_entries["parse_errors"].append(str(exc))

        # 校验失败时不再写入目标 section。
        return

    # 把合法 option 写入原 section 名称对应的子字典。
    dict_entries[section][str_normalized_key] = value  # direct section 下的最终配置值

# 解析 directive section 中的单个条目。
def _store_directive_entry(dict_entries: dict[str, Any], str_lower_key: str, value: str) -> None:
    """
    写入 directive section 条目。

    参数:
        dict_entries: cfg 解析结果容器。
        str_lower_key: 已经小写化的 directive 名称。
        value: directive 的原始参数文本。

    返回:
        无业务返回值；成功时把解析后的 directive 追加到 directives 列表。

    异常:
        无显式异常抛出；directive 白名单问题会转写到 parse_errors。
    """

    # 尝试把 directive 文本拆解为名称、位置和参数列表。
    try:

        # 将合法 directive 追加到规范化结果列表。
        dict_entries["directives"].append(_parse_directive(str_lower_key, value))

    # 捕获 directive 解析或白名单失败并保留错误文本。
    except ValueError as exc:

        # 把当前 directive 的失败原因写入 parse_errors。
        dict_entries["parse_errors"].append(str(exc))

# 把 directive 文本拆成名称、位置和参数列表。
def _parse_directive(name: str, value: str) -> dict[str, Any]:
    """
    解析 directive section 的单个条目。

    参数:
        name: directive 名称文本。
        value: directive 值文本，首个 token 表示位置，其余 token 作为参数。

    返回:
        包含 name、location、args 和 raw 字段的 directive 字典。

    异常:
        ValueError: 当 directive 名称不在允许白名单中时抛出。
    """

    # 先把 directive 名称限制在支持的白名单范围内。
    str_normalized_name = require_allowed_directive(name)  # 白名单确认后的 directive 名称

    # 按空白拆分 directive 文本，首个 token 作为位置。
    list_parts = value.split()  # directive 值拆分后的 token 列表

    # 缺少位置时保持空字符串，沿用旧解析兼容行为。
    str_location = list_parts[0] if list_parts else ""  # directive 作用的源码或函数位置

    # 除位置外的剩余 token 原样保留为参数列表。
    list_args = list_parts[1:] if len(list_parts) > 1 else []  # directive 位置之后保留的参数序列

    # 组织供下游消费的结构化 directive 表达。
    return {
        "name": str_normalized_name,
        "location": str_location,
        "args": list_args,
        "raw": value,
    }

# 向列表追加不重复条目。
def _append_unique(list_items: list[str], value: str) -> None:
    """
    把值追加到列表末尾，并保持列表元素唯一。

    参数:
        list_items: 需要维护唯一性的字符串列表。
        value: 待追加的字符串值。

    返回:
        无业务返回值；list_items 会在需要时被原地修改。

    异常:
        无显式异常抛出。
    """

    # 仅当目标值首次出现时才追加，避免重复路径污染结果。
    if value not in list_items:

        # 原地追加新的唯一条目，保持原始出现顺序。
        list_items.append(value)

# 剥离 cfg 行中的 # 和 ; 注释。
def _strip_comment(line: str) -> str:
    """
    删除 cfg 行中的行内注释。

    参数:
        line: 原始 cfg 行文本。

    返回:
        去掉注释后的行文本；整行注释时返回空字符串。

    异常:
        无显式异常抛出。
    """

    # 先获得裁剪后的文本，便于识别整行注释。
    str_stripped_line = line.strip()  # 去除边界空白后的行文本

    # 以 # 或 ; 开头的整行注释直接视为空内容。
    if str_stripped_line.startswith("#") or str_stripped_line.startswith(";"):

        # 返回空字符串，让上游按空行统一跳过。
        return ""

    # 删除行内 # 或 ; 之后的注释部分，保留注释前主体文本。
    return line.split("#", 1)[0].split(";", 1)[0]
