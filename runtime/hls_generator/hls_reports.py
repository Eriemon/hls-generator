"""解析 Vitis HLS 报告中的延迟、资源、时序和 cosim 指标。"""

# 延迟注解解析，避免运行期解析复杂类型别名
from __future__ import annotations

# 标准库依赖
import re
from pathlib import Path
from typing import Any

# HLS 报告目录指标采集入口
def collect_hls_report_metrics(root: Path) -> dict[str, Any]:
    """
    遍历 HLS 输出目录并合并 synthesis 与 cosim 报告指标。

    参数:
        root: Vitis HLS 运行输出根目录。
    返回:
        去除空字段后的指标字典，可能包含 csynth 和 cosim 两类报告。
    异常:
        报告文件读取异常由 Path.read_text 原样抛出，便于调用方定位文件权限或编码问题。
    """

    # 保存按报告类型合并后的指标
    dict_metrics: dict[str, Any] = {}  # HLS 报告指标集合

    # 按稳定路径顺序扫描所有候选报告文件
    for path_report_file in sorted(root.glob("**/*")):

        # 只处理普通 .rpt 和 .log 文件，跳过目录和其它构建产物
        if not path_report_file.is_file() or path_report_file.suffix.lower() not in {".rpt", ".log"}:

            # 跳过非 HLS 文本报告候选
            continue

        # 使用小写文件名识别报告类型
        str_lower_name = path_report_file.name.lower()  # 小写报告文件名

        # synthesis/csynth 报告提供延迟、II、资源和时序信息
        if "csynth" in str_lower_name or "synth" in str_lower_name:

            # 读取 synthesis 报告并合并到 csynth 分组
            dict_csynth_report = {  # 当前 synthesis 报告对应的分组载荷
                "csynth": parse_hls_report(path_report_file.read_text(encoding="utf-8", errors="ignore"))  # 汇总该报告里的延迟、资源与时序字段
            }

            # 合并当前 synthesis 报告，保留已有字段
            _merge(dict_metrics, dict_csynth_report)

        # cosim 报告主要提供协同仿真通过状态
        elif "cosim" in str_lower_name:

            # 先把 cosim 文本整理成独立分组，避免覆盖 synthesis 结果
            dict_cosim_report = {  # 当前 cosim 报告对应的局部更新片段
                "cosim": parse_hls_report(path_report_file.read_text(encoding="utf-8", errors="ignore"))  # 提取该报告里的 pass 或 fail 结论及相关指标
            }

            # 合并当前 cosim 报告，保留其它报告类型
            _merge(dict_metrics, dict_cosim_report)

    # 返回清理空分组后的报告指标
    return _drop_empty(dict_metrics)

# 单个 HLS 报告文本解析入口
def parse_hls_report(text: str) -> dict[str, Any]:
    """
    从单个 HLS 文本报告中提取可识别指标。

    参数:
        text: HLS 报告或日志的文本内容。
    返回:
        去除空字段后的指标字典，字段包括 latency、interval、resources、timing 和 cosim。
    异常:
        本函数仅执行正则解析，不主动抛出业务异常。
    """

    # 初始化报告指标，字段名保持历史对外结构
    dict_report: dict[str, Any] = {}  # 单个报告的解析结果

    # 解析延迟周期范围或单值
    dict_report["latency"] = _parse_latency(text)  # HLS 延迟周期指标

    # 解析启动间隔范围或单值
    dict_report["interval"] = _parse_interval(text)  # HLS 启动间隔指标

    # 解析 BRAM/DSP/FF/LUT/URAM 资源计数
    dict_report["resources"] = _parse_resources(text)  # 资源计数指标

    # 解析 WNS/TNS/估计时钟周期
    dict_report["timing"] = _parse_timing(text)  # 时序指标

    # 解析协同仿真文本中的通过、失败或未知状态
    dict_report["cosim"] = _parse_cosim(text)  # 协同仿真状态指标

    # 返回去除空指标分组后的报告
    return _drop_empty(dict_report)

# latency 指标解析
def _parse_latency(text: str) -> dict[str, int]:
    """
    解析 HLS 报告中的 latency 周期指标。

    参数:
        text: HLS 报告文本。
    返回:
        latency 指标字典，可能包含 min/max 或 value。
    异常:
        本函数不主动抛出业务异常；无法匹配时返回空字典。
    """

    # 按 Vitis HLS 常见格式准备 latency 正则候选
    list_latency_patterns: list[tuple[str, tuple[str, ...]]] = [
        (r"Latency\s*\(cycles\)\s*[:=]\s*min\s*=?\s*(\d+)\s*max\s*=?\s*(\d+)", ("min", "max")),  # 兼容键值形式里分别写出最小与最大周期
        (r"\bLatency\b[^\n|]*\|\s*(\d+)\s*\|\s*(\d+)", ("min", "max")),  # 兼容表格行里成对给出的两个 latency 数值
        (r"\bLatency\s*[:=]\s*(\d+)", ("value",)),  # 兼容日志里只记录单个 latency 周期
    ]  # latency 正则候选

    # 返回首个命中的 latency 指标
    return _parse_integer_patterns(text, list_latency_patterns)

# 启动间隔与 II 指标解析
def _parse_interval(text: str) -> dict[str, int]:
    """
    解析 HLS 报告中的启动间隔或 II 指标。

    参数:
        text: HLS 报告文本。
    返回:
        interval 指标字典，可能包含 min/max 或 value。
    异常:
        本函数不主动抛出业务异常；无法匹配时返回空字典。
    """

    # 汇总常见的 Interval 与 II 写法，按优先级准备匹配候选
    list_interval_patterns: list[tuple[str, tuple[str, ...]]] = [
        (r"(?:Interval|II)\s*[:=]\s*min\s*=?\s*(\d+)\s*max\s*=?\s*(\d+)", ("min", "max")),  # 匹配键值形式中的最小与最大启动间隔
        (r"\b(?:Interval|II)\b[^\n|]*\|\s*(\d+)\s*\|\s*(\d+)", ("min", "max")),  # 匹配表格列里并排给出的两个 II 数值
        (r"\bII\s*=?\s*(\d+)", ("value",)),  # 匹配日志只出现单个 II 数字的简化写法
    ]  # 启动间隔候选正则列表

    # 找到首个可解释的 II 表达式后立即结束匹配
    return _parse_integer_patterns(text, list_interval_patterns)

# 资源指标解析
def _parse_resources(text: str) -> dict[str, int]:
    """
    解析 HLS 报告中的板级资源使用量。

    参数:
        text: HLS 报告文本。
    返回:
        资源计数字典，字段可能包含 bram、dsp、ff、lut 和 uram。
    异常:
        本函数不主动抛出业务异常；无法匹配时返回空字典。
    """

    # 保存报告中识别出的资源计数
    dict_resources: dict[str, int] = {}  # 资源计数字典

    # 建立资源字段与 Vitis 报告别名的映射
    dict_resource_aliases = {  # 统一资源字段与报告列名的对应关系
        "bram": r"BRAM(?:_18K)?",  # BRAM 列在不同报告里可能写成 BRAM 或 BRAM_18K
        "dsp": r"DSP(?:48E)?",  # DSP 列偶尔会带 DSP48E 的器件后缀
        "ff": r"FF",  # 触发器资源直接使用 FF 缩写
        "lut": r"LUT",  # 查找表资源沿用 LUT 列名
        "uram": r"URAM",  # 片上超 RAM 资源使用 URAM 标识
    }

    # 先解析散落在文本中的资源单项
    for str_resource_key, str_resource_pattern in dict_resource_aliases.items():

        # 查找当前资源字段对应的数值
        match_resource_report: re.Match[str] | None = re.search(  # 当前资源字段在散列表达式中的匹配结果
            rf"\b{str_resource_pattern}\b\s*[:=|]\s*(\d+)",  # 匹配资源列名后紧随其后的整数值
            text,  # 待扫描的整份 HLS 报告文本
            flags=re.IGNORECASE,  # 忽略列名大小写差异以兼容不同版本输出
        )  # 单项资源匹配结果

        # 命中资源数值时写入统一字段
        if match_resource_report:

            # 保存当前资源字段的整数计数
            dict_resources[str_resource_key] = int(match_resource_report.group(1))  # 当前资源计数

    # 解析 Vitis HLS 表格形式的 BRAM/DSP/FF/LUT 汇总行
    match_resource_table_report: re.Match[str] | None = _match_resource_table(text)  # 表格资源匹配结果

    # 表格命中时覆盖同名资源字段，保持表格汇总优先
    if match_resource_table_report:

        # 将表格四列资源写入统一资源字典
        _merge_resource_table(dict_resources, match_resource_table_report)

    # 返回已识别资源计数
    return dict_resources

# 时序指标解析
def _parse_timing(text: str) -> dict[str, float]:
    """
    解析 HLS 报告中的 WNS、TNS 和估计时钟周期。

    参数:
        text: HLS 报告文本。
    返回:
        时序指标字典，时钟周期单位为 ns。
    异常:
        本函数不主动抛出业务异常；无法匹配时返回空字典。
    """

    # 保存时序指标浮点值
    dict_timing: dict[str, float] = {}  # 时序指标字典

    # 建立时序字段与报告正则的映射
    dict_timing_patterns = {
        "wns": r"\bWNS\b\s*[:=|]\s*(-?\d+(?:\.\d+)?)",  # 最差负裕量
        "tns": r"\bTNS\b\s*[:=|]\s*(-?\d+(?:\.\d+)?)",  # 总负裕量
        "estimated_clock_period_ns": r"(?:Estimated\s+)?Clock\s+Period(?:\s*\(ns\))?\s*[:=|]\s*(\d+(?:\.\d+)?)",  # 估计时钟周期
    }  # 时序字段正则映射

    # 逐个时序字段尝试匹配报告文本
    for str_timing_key, str_timing_pattern in dict_timing_patterns.items():

        # 查找当前时序字段的浮点数值
        match_timing_report: re.Match[str] | None = re.search(str_timing_pattern, text, flags=re.IGNORECASE)  # 时序字段匹配结果

        # 命中后写入统一时序字段
        if match_timing_report:

            # 保存当前时序字段的浮点值
            dict_timing[str_timing_key] = float(match_timing_report.group(1))  # 当前时序指标

    # 返回已识别时序指标
    return dict_timing

# cosim 状态解析
def _parse_cosim(text: str) -> dict[str, Any]:
    """
    解析 cosim 报告文本中的通过状态。

    参数:
        text: HLS 报告文本。
    返回:
        cosim 状态字典；非 cosim 文本返回空字典。
    异常:
        本函数不主动抛出业务异常。
    """

    # 转成小写便于同时识别 cosim 与 co-sim 拼写
    str_lower_text = text.lower()  # 小写报告文本

    # 非 cosim 报告不产生状态字段
    if "cosim" not in str_lower_text and "co-sim" not in str_lower_text:

        # 返回空字典表示当前报告没有 cosim 语义
        return {}

    # pass 文本出现时优先判定为通过
    if "pass" in str_lower_text:

        # 返回 cosim 通过状态
        return {"status": "pass"}

    # fail 文本出现时判定为失败
    if "fail" in str_lower_text:

        # 返回 cosim 失败状态
        return {"status": "fail"}

    # 未出现明确 pass/fail 时保留未知状态
    return {"status": "unknown"}

# 嵌套指标合并辅助函数
def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    """
    将一个报告分组合并进目标指标字典。

    参数:
        target: 被更新的指标字典。
        source: 新解析出的指标分组。
    返回:
        无业务返回值；target 会被原地更新。
    异常:
        本函数不主动抛出业务异常。
    """

    # 遍历新解析出的指标分组
    for str_metric_key in source:

        # 取出当前报告分组值，保留 Any 类型以兼容嵌套指标
        obj_report_metric_value: Any = source[str_metric_key]  # 当前报告分组值

        # 空指标不写入目标报告
        if not obj_report_metric_value:

            # 跳过空报告分组
            continue

        # 两边都是字典时做浅合并，避免覆盖已有同类指标
        if isinstance(obj_report_metric_value, dict) and isinstance(target.get(str_metric_key), dict):

            # 合并同一报告分组下的指标字段
            target[str_metric_key].update(obj_report_metric_value)

        # 其它类型直接覆盖同名字段
        else:

            # 保存当前报告分组值
            target[str_metric_key] = obj_report_metric_value  # 合并后的指标分组

# 空指标清理辅助函数
def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    """
    删除报告字典中的空分组。

    参数:
        value: 待清理的报告字典。
    返回:
        不包含空字典、空列表和 None 的新报告字典。
    异常:
        本函数不主动抛出业务异常。
    """

    # 保存非空指标字段
    dict_non_empty: dict[str, Any] = {}  # 去空后的报告字典

    # 遍历原始报告字段
    for str_metric_key in value:

        # 取出当前字段值，保留原类型给空值判断和结果透传
        obj_report_metric_value: Any = value[str_metric_key]  # 当前报告字段值

        # 空字典、空列表和 None 不进入最终报告
        if obj_report_metric_value in ({}, [], None):

            # 跳过没有有效内容的指标字段
            continue

        # 保留当前非空指标字段
        dict_non_empty[str_metric_key] = obj_report_metric_value  # 非空指标字段

    # 返回清理后的报告
    return dict_non_empty

# 整数型正则候选解析辅助函数
def _parse_integer_patterns(text: str, list_patterns: list[tuple[str, tuple[str, ...]]]) -> dict[str, int]:
    """
    按候选正则列表解析整数指标。

    参数:
        text: HLS 报告文本。
        list_patterns: 正则表达式和对应字段名组成的候选列表。
    返回:
        首个命中正则产生的整数字段字典；没有命中时返回空字典。
    异常:
        本函数不主动抛出业务异常。
    """

    # 保存当前正则候选命中的整数字段
    dict_integer_metrics: dict[str, int] = {}  # 整数指标字典

    # 按优先级尝试每个正则候选
    for str_pattern, tuple_metric_keys in list_patterns:

        # 在报告文本中查找当前正则
        match_metric_report: re.Match[str] | None = re.search(str_pattern, text, flags=re.IGNORECASE)  # 整数指标匹配结果

        # 命中当前候选后提取对应分组
        if match_metric_report:

            # 按字段名和捕获组配对写入指标
            for str_metric_key, str_metric_value in zip(tuple_metric_keys, match_metric_report.groups()):

                # 转成整数并保存到对应字段
                dict_integer_metrics[str_metric_key] = int(str_metric_value)  # 捕获组整数指标

            # 返回首个命中的候选，避免低优先级格式覆盖
            return dict_integer_metrics

    # 没有命中任何候选时返回空指标
    return dict_integer_metrics

# HLS 资源表格匹配辅助函数
def _match_resource_table(text: str) -> re.Match[str] | None:
    """
    匹配 Vitis HLS 报告中的资源表格汇总行。

    参数:
        text: HLS 报告文本。
    返回:
        表格资源匹配对象；没有命中时返回 None。
    异常:
        本函数不主动抛出业务异常。
    """

    # 资源表头和下一行数值需要跨行匹配
    str_table_pattern = (
        r"\|\s*BRAM_?18K\s*\|\s*DSP(?:48E)?\s*\|\s*FF\s*\|\s*LUT\s*\|.*?\n"
        r"\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
    )  # Vitis HLS 资源表格正则

    # 返回表格匹配结果供调用方提取四个资源字段
    return re.search(str_table_pattern, text, flags=re.IGNORECASE | re.DOTALL)

# 资源表格字段合并辅助函数
def _merge_resource_table(dict_resources: dict[str, int], match_resource_table: re.Match[str]) -> None:
    """
    将资源表格匹配结果写入资源字典。

    参数:
        dict_resources: 待更新的资源计数字典。
        match_resource_table: _match_resource_table 返回的匹配对象。
    返回:
        无业务返回值；dict_resources 会被原地更新。
    异常:
        本函数不主动抛出业务异常。
    """

    # 把表格首列的 BRAM 数值映射到统一资源字段
    dict_resources["bram"] = int(match_resource_table.group(1))  # 表格汇总得到的 BRAM 使用量

    # 把表格第二列的 DSP 结果落到乘法器资源字段
    dict_resources["dsp"] = int(match_resource_table.group(2))  # 表格汇总得到的 DSP 乘加单元数量

    # 把表格第三列的 FF 结果落到触发器资源字段
    dict_resources["ff"] = int(match_resource_table.group(3))  # 表格汇总得到的触发器数量

    # 把表格第四列的 LUT 结果落到查找表资源字段
    dict_resources["lut"] = int(match_resource_table.group(4))  # 表格汇总得到的查找表数量
