"""提取 HLS 产物的静态接口契约。"""

# 延迟注解避免导入期求值复杂容器类型。
from __future__ import annotations

# 标准库负责哈希、JSON 规范化、正则和路径扫描。
import hashlib
import json
import re
from pathlib import Path
from typing import Any

# 向量工具提供跨阶段 case id 与 sha256 契约提取。
from scripts.python.generation.vectors import extract_vector_hashes, find_vector_contracts

# 接口审计只允许 HLS 交付物。
INTERFACE_TARGETS = ("hls",)  # 可审计接口目标集合

# C/C++ 源文件后缀用于 HLS 产物扫描。
HLS_SOURCE_SUFFIXES = (".cpp", ".cc", ".cxx", ".h", ".hpp")  # HLS 源码后缀集合

# HLS 配置文件后缀用于查找 hls_config.cfg 等工程配置。
HLS_CONFIG_SUFFIX = ".cfg"  # HLS 配置文件后缀

# 顶层函数提取跳过 C/C++ 关键字伪匹配。
CPP_CONTROL_KEYWORDS = {"if", "for", "while", "switch", "return"}  # 非函数关键字集合

# C/C++ 参数切分需要识别会进入嵌套上下文的括号。
CPP_ARG_OPEN_DEPTH_KEYS = {"<": "angle", "(": "paren", "[": "bracket"}  # 开括号到深度类别的映射

# C/C++ 参数切分需要识别会退出嵌套上下文的括号。
CPP_ARG_CLOSE_DEPTH_KEYS = {">": "angle", ")": "paren", "]": "bracket"}  # 闭括号到深度类别的映射

# 函数签名正则覆盖 extern "C"、模板类型和声明/定义两类形式。
CPP_FUNCTION_PATTERN = re.compile(  # C/C++ 函数签名主匹配器
    r"(?:^|\n)\s*"
    r"(?:extern\s+\"C\"\s+)?"
    r"(?:[\w:<>*&\s]+?)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\(([^;{}]*)\)\s*(?:\{|;)",
    re.MULTILINE,  # 支持整段源码扫描
)  # C/C++ 函数签名匹配器

# cfg 解析只接收 workflow 关心的 Vitis HLS 配置键。
HLS_CFG_LINE_PATTERN = re.compile(  # HLS cfg 行解析器
    r"\s*((?:syn|tb)\.file|syn\.top|part|clock)\s*=\s*(\S+)\s*$"  # 只接受 workflow 关心的 cfg 键
)  # HLS cfg 单行键值匹配器

# 公开入口返回 Python 或 HLS 产物的接口契约。
def audit_interface(target: str, root: Path) -> dict[str, Any]:
    """
    审计指定产物目录并返回稳定 JSON 契约。

    参数:
        target: 审计目标，dtype=str，unit=dimensionless；合法值仅为 hls。
        root: 产物根目录，dtype=Path，unit=filesystem path。
    返回:
        接口契约字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    异常:
        ValueError: target 不是 hls 时抛出。
    """

    # 目标名先归一化，避免 CLI 或 workflow 传入大小写变体。
    str_target: str = _require_target(target)  # 规范化后的接口目标

    # HLS-only 技能始终只审计 HLS 交付物接口。
    dict_contract: dict[str, Any] = _hls_contract(root)  # 未加 hash 的接口契约

    # interface_sha256 排除自身字段后计算，保持跨运行稳定。
    dict_contract["interface_sha256"] = _stable_hash(dict_contract)  # 契约内容稳定 hash

    # 调用方会把该契约写入 stage 输出和 verifier。
    return dict_contract

# 校验接口审计目标是否属于 HLS-only workflow。
def _require_target(target: str) -> str:
    """
    规范化并校验接口审计目标。

    参数:
        target: 原始目标名，dtype=str，unit=dimensionless。
    返回:
        小写目标名，dtype=str，unit=dimensionless。
    异常:
        ValueError: 目标不在 INTERFACE_TARGETS 中时抛出。
    """

    # HLS workflow 配置有时会传入大小写混合的目标名。
    str_normalized_target: str = target.lower()  # 小写接口目标名

    # 只允许 HLS 交付物接口契约。
    if str_normalized_target not in INTERFACE_TARGETS:

        # 错误文本面向用户和 workflow trace，按 current-project 前缀输出。
        raise ValueError(
            "> ERR: [Python] This skill is HLS-only; interface target must be `hls`."
        )

    # 规范目标名参与后续分支选择。
    return str_normalized_target

# 构造 HLS 阶段的接口契约。
def _hls_contract(root: Path) -> dict[str, Any]:
    """
    扫描 HLS C/C++ 和 cfg 产物并提取接口契约。

    参数:
        root: HLS 产物根目录，dtype=Path，unit=filesystem path。
    返回:
        HLS 阶段接口契约，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    """

    # HLS 源码文本按相对路径排序，确保函数提取顺序稳定。
    dict_text_by_path: dict[str, str] = _read_hls_sources(root)  # HLS 源码文本映射

    # 函数和 pragma 扫描需要合并头文件与源文件文本。
    str_combined_source: str = "\n".join(dict_text_by_path.values())  # 合并后的 HLS 源码文本

    # 函数列表用于确认 top 和参数契约。
    list_functions: list[dict[str, Any]] = _extract_cpp_functions(str_combined_source)  # HLS 函数契约列表

    # hls_config.cfg 提供 syn.top、syn.file、tb.file 等工具契约。
    dict_cfg: dict[str, Any] = _extract_hls_cfg(root)  # HLS cfg 契约字段

    # top 优先来自 cfg，缺失时用第一个非 testbench 函数兜底。
    str_top: str | None = _hls_top_function(dict_cfg, list_functions)  # HLS 顶层函数名

    # pragma 列表用于补齐 argument interface/bundle 信息。
    list_pragmas: list[dict[str, str]] = _extract_hls_pragmas(str_combined_source)  # HLS pragma 契约列表

    # 顶层函数参数结合 pragma 后形成 verifier 使用的接口数组。
    list_arguments: list[dict[str, str]] = _hls_argument_contract(  # 顶层参数契约
        list_functions,  # 已解析的 HLS 函数契约
        str_top,  # cfg 或推断得到的顶层函数名
        list_pragmas,  # 源码里提取出的接口 pragma
    )  # HLS 顶层参数契约列表

    # vectors.json 中登记的是样例级契约，用它把接口摘要和现有向量样本关联起来。
    list_vector_contracts: list[dict[str, Any]] = find_vector_contracts(root)  # vectors.json 抽取出的样例契约

    # 这里只检查 cfg、函数和 pragma 之间的结构漂移，具体文件定位交给上游字段。
    list_issues: list[dict[str, str]] = _hls_contract_issues(  # HLS 结构问题
        dict_cfg,  # 已解析的 cfg 契约
        list_functions,  # 已抽取的源码函数契约
    )  # cfg 与源码一致性审计结果

    # 返回字段名是下游 validation/verifier 的稳定 JSON 协议。
    return {
        "version": 1,
        "target": "hls",
        "source_root": root.name,
        "top": str_top,
        "functions": list_functions,
        "arguments": list_arguments,
        "control_mode": _hls_control_mode(list_pragmas),
        "pragmas": list_pragmas,
        "cfg": dict_cfg,
        "case_ids": _case_ids(list_vector_contracts),
        "vector_hashes": _vector_hashes(list_vector_contracts) or _scan_vector_hashes(dict_text_by_path),
        "issues": list_issues,
    }

# 读取 HLS 相关源码文本。
def _read_hls_sources(root: Path) -> dict[str, str]:
    """
    读取 HLS C/C++ 源文件和头文件。

    参数:
        root: HLS 产物根目录，dtype=Path，unit=filesystem path。
    返回:
        相对路径到源码文本的映射，shape=(n files)，dtype=dict[str, str]，unit=text by path。
    """

    # 源码文本映射按相对路径插入，保持 JSON hash 稳定。
    dict_texts: dict[str, str] = {}  # HLS 文件文本映射

    # 递归扫描全部 HLS 源文件，后缀组顺序保持旧版读取契约。
    for path_file in _iter_hls_source_paths(root):

        # 统一改成相对路径后，JSON 契约和 issue 都能稳定回指仓库内文件。
        str_rel_path: str = path_file.relative_to(root).as_posix()  # 用于契约键和 issue 定位的相对路径

        # 文本读取容忍编码坏字节，匹配旧版宽松行为。
        dict_texts[str_rel_path] = path_file.read_text(  # 源码全文
            encoding="utf-8",  # 按仓库默认编码读取
            errors="ignore",  # 坏字节直接跳过
        )  # HLS 文件源码文本

    # 返回全部可审计 HLS 源码。
    return dict_texts

# 遍历 HLS 源文件，保留旧版按后缀分组的读取顺序。
def _iter_hls_source_paths(root: Path) -> list[Path]:
    """
    返回 HLS 源文件路径列表。

    参数:
        root: HLS 产物根目录，dtype=Path，unit=filesystem path。
    返回:
        按后缀组和路径排序的源码文件列表，shape=(n files)，dtype=list[Path]，unit=filesystem path。
    """

    # 读取顺序影响默认 top function，必须与旧版 suffix_globs 顺序一致。
    list_source_paths: list[Path] = []  # 按旧版后缀组顺序排列的 HLS 源文件路径

    # 每个后缀组内部仍按路径排序，保持契约 hash 稳定。
    for str_suffix in HLS_SOURCE_SUFFIXES:

        # 当前后缀组只收集真实文件。
        list_source_paths.extend(
            sorted(
                path_file
                for path_file in root.rglob(f"*{str_suffix}")
                if path_file.is_file()
            )
        )

    # 返回与旧版 glob 分组一致的源码路径。
    return list_source_paths

# 提取 C/C++ 函数声明或定义。
def _extract_cpp_functions(text: str) -> list[dict[str, Any]]:
    """
    从合并后的 C/C++ 文本中提取函数名和参数。

    参数:
        text: C/C++ 源码文本，dtype=str，unit=text。
    返回:
        去重后的函数契约列表，shape=(n functions)，dtype=list[dict[str, Any]]，unit=JSON array。
    """

    # 临时列表保留正则发现顺序，稍后按函数名去重。
    list_functions: list[dict[str, Any]] = []  # C/C++ 函数契约候选列表

    # 正则只做轻量静态审计，不替代完整 C++ parser。
    for re_match in CPP_FUNCTION_PATTERN.finditer(text):

        # 第一捕获组是函数名。
        str_name: str = re_match.group(1)  # 正则命中的 C/C++ 函数名

        # 过滤控制流关键字被误识别成函数的情况。
        if str_name in CPP_CONTROL_KEYWORDS:

            # 当前命中不是函数声明或定义。
            continue

        # 第二捕获组是括号内原始参数文本。
        str_args_text: str = re_match.group(2)  # C/C++ 参数列表文本

        # 追加函数契约候选。
        list_functions.append(
            {
                "name": str_name,
                "args": _parse_cpp_args(str_args_text),
            }
        )

    # 同名声明和定义只保留第一次出现的契约。
    return _dedupe_by_name(list_functions)

# 解析 C/C++ 参数列表。
def _parse_cpp_args(args_text: str) -> list[dict[str, str]]:
    """
    将 C/C++ 参数文本解析为 name/type 字典列表。

    参数:
        args_text: 函数括号内参数文本，dtype=str，unit=text。
    返回:
        参数契约列表，shape=(n args)，dtype=list[dict[str, str]]，unit=JSON array。
    """

    # 参数契约按函数签名中的原始顺序保留。
    list_args: list[dict[str, str]] = []  # C/C++ 参数契约列表

    # 先按顶层逗号切分，避免模板、数组或函数指针中的逗号被误切。
    for str_raw_arg in _normalized_cpp_arg_parts(args_text):

        # C/C++ 的 void 参数表示无参数。
        if str_raw_arg == "void":

            # void 不进入参数契约。
            continue

        # 正则从尾部提取参数名，支持数组后缀。
        list_name_matches: list[str] = re.findall(  # 参数名候选
            r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?\s*$",  # 捕获参数尾部名称片段
            str_raw_arg,  # 当前原始参数文本
        )  # 参数尾部可作为契约名的候选列表

        # 参数名缺失时保留原文本，匹配旧版宽松解析。
        str_name: str = list_name_matches[-1] if list_name_matches else str_raw_arg  # 参数契约使用的 C/C++ 参数名

        # 参数类型是参数名前的文本。
        int_name_start: int = str_raw_arg.rfind(str_name) if list_name_matches else 0  # 参数名在原始片段中的起始下标

        # 参数类型保留参数名前的 C/C++ 声明文本。
        str_arg_type: str = (
            str_raw_arg[:int_name_start].strip()  # 参数类型片段
            if list_name_matches  # 命中名称时保留前缀类型文本
            else ""  # 未命中名称时退回空类型
        )  # C/C++ 参数类型文本

        # 追加单个参数契约。
        list_args.append({"name": str_name, "type": str_arg_type})

    # 返回当前函数的参数契约。
    return list_args

# 生成去空白后的 C/C++ 参数片段。
def _normalized_cpp_arg_parts(args_text: str) -> list[str]:
    """
    切分 C/C++ 参数文本并丢弃空片段。

    参数:
        args_text: 原始参数列表文本，dtype=str，unit=text。
    返回:
        参数片段列表，shape=(n args)，dtype=list[str]，unit=text items。
    """

    # 顶层切分后的片段仍可能包含首尾空白。
    list_parts: list[str] = [
        str_item.strip()  # 去掉片段首尾空白
        for str_item in _split_cpp_args(args_text)  # 遍历顶层逗号切分结果
        if str_item.strip()  # 丢弃纯空白片段
    ]  # 去空白后的参数片段

    # 返回可解析参数片段。
    return list_parts

# 按顶层逗号切分 C/C++ 参数列表。
def _split_cpp_args(args_text: str) -> list[str]:
    """
    在忽略模板、括号和数组层级的前提下切分 C/C++ 参数。

    参数:
        args_text: 原始参数列表文本，dtype=str，unit=text。
    返回:
        参数片段列表，shape=(n parts)，dtype=list[str]，unit=text items。
    """

    # 切分结果保留原始空白，调用方再决定是否清理。
    list_parts: list[str] = []  # 顶层参数片段列表

    # 当前片段逐字符累积。
    list_current_chars: list[str] = []  # 当前参数片段字符列表

    # 嵌套深度记录模板、括号和数组三类上下文。
    dict_depths: dict[str, int] = {
        "angle": 0,  # 模板尖括号深度
        "paren": 0,  # 圆括号深度
        "bracket": 0,  # 数组下标深度
    }  # C/C++ 参数切分深度状态

    # 逐字符更新嵌套深度并识别顶层逗号。
    for str_char in args_text:

        # 当前字符可能改变模板、括号或数组深度。
        _update_cpp_arg_depths(dict_depths, str_char)

        # 顶层逗号表示一个参数片段结束。
        if _is_top_level_comma(str_char, dict_depths):

            # 保存当前参数片段文本。
            list_parts.append("".join(list_current_chars))

            # 新片段从逗号之后重新累积。
            list_current_chars = []  # 逗号后的参数片段字符列表

            # 当前逗号不属于任何参数内容。
            continue

        # 非顶层分隔符字符保留到当前片段。
        list_current_chars.append(str_char)

    # 最后一个参数片段没有尾随逗号，需要显式收尾。
    list_parts.append("".join(list_current_chars))

    # 返回原始参数片段列表。
    return list_parts

# 更新 C/C++ 参数切分深度。
def _update_cpp_arg_depths(dict_depths: dict[str, int], char: str) -> None:
    """
    根据单个字符更新模板、括号和数组嵌套深度。

    参数:
        dict_depths: 深度状态字典，shape=(3 keys)，dtype=dict[str, int]，unit=nesting depth。
        char: 当前字符，dtype=str，unit=character。
    返回:
        无业务返回值；函数会原地更新 dict_depths。
    """

    # 开括号字符直接进入对应的嵌套上下文。
    str_open_depth_key: str = CPP_ARG_OPEN_DEPTH_KEYS.get(char, "")  # 当前开括号对应的深度类别

    # 识别到开括号时立即推进深度，后续逗号判断才不会误切模板或数组参数。
    if str_open_depth_key:

        # 已识别开括号时增加对应深度。
        _bump_cpp_arg_depth(dict_depths, str_open_depth_key, 1)

        # 开括号处理完成后不再检查闭括号映射。
        return

    # 闭括号字符只有在已有深度时才退出对应上下文。
    str_close_depth_key: str = CPP_ARG_CLOSE_DEPTH_KEYS.get(char, "")  # 当前闭括号对应的深度类别

    # 只有真正可闭合的右括号才回退深度，避免负计数污染后续分隔状态。
    if not str_close_depth_key or dict_depths[str_close_depth_key] <= 0:

        # 非括号字符或不平衡闭括号不改变参数切分状态。
        return

    # 已识别有效闭括号时减少对应深度。
    _bump_cpp_arg_depth(dict_depths, str_close_depth_key, -1)

# 统一执行 C/C++ 参数切分深度的原地增减。
def _bump_cpp_arg_depth(dict_depths: dict[str, int], key: str, delta: int) -> None:
    """
    调整 C/C++ 参数切分的某类括号嵌套深度。

    参数:
        dict_depths: 深度状态字典，shape=(3 keys)，dtype=dict[str, int]，unit=nesting depth。
        key: 深度类别键，dtype=str，unit=dimensionless。
        delta: 深度增量，dtype=int，unit=nesting depth。
    返回:
        无业务返回值；函数会原地更新 dict_depths。
    """

    # 单一赋值点让主扫描流程只描述字符分派，不散落深度计算。
    dict_depths[key] += delta  # 指定括号类别调整后的嵌套深度

# 判断当前字符是否为参数顶层分隔逗号。
def _is_top_level_comma(char: str, dict_depths: dict[str, int]) -> bool:
    """
    判断逗号是否处在模板、括号和数组之外。

    参数:
        char: 当前字符，dtype=str，unit=character。
        dict_depths: 深度状态字典，shape=(3 keys)，dtype=dict[str, int]，unit=nesting depth。
    返回:
        是否为顶层逗号，dtype=bool，unit=dimensionless。
    """

    # 顶层逗号要求所有嵌套深度均为零。
    bool_is_separator: bool = (
        char == ","  # 只有逗号才可能切分参数
        and dict_depths["angle"] == 0  # 不在模板尖括号内
        and dict_depths["paren"] == 0  # 不在函数调用括号内
        and dict_depths["bracket"] == 0  # 不在数组下标内
    )  # 参数顶层逗号标志

    # 返回切分判断。
    return bool_is_separator

# 提取 HLS cfg 键值契约。
def _extract_hls_cfg(root: Path) -> dict[str, Any]:
    """
    读取首个 HLS cfg 文件并提取 workflow 关心的键值。

    参数:
        root: HLS 产物根目录，dtype=Path，unit=filesystem path。
    返回:
        HLS cfg 契约字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    """

    # syn.files 和 tb.files 保留多文件列表，兼容 Vitis 配置。
    dict_cfg: dict[str, Any] = {
        "syn.files": [],  # 合成源文件列表
        "tb.files": [],  # testbench 源文件列表
    }  # 缺失 cfg 时也返回固定字段骨架

    # 旧行为只读取排序后的第一个 cfg 文件。
    path_cfg: Path | None = _first_hls_cfg_path(root)  # 首个 HLS cfg 文件路径

    # 缺少 cfg 时返回只有默认列表字段的契约。
    if path_cfg is None:

        # 没有 cfg 不阻断审计，validation 会报告 syn.file 缺失。
        return dict_cfg

    # 把 cfg 的相对路径写入契约后，问题报告就能直接回链到这份配置文件。
    dict_cfg["path"] = path_cfg.relative_to(root).as_posix()  # 供 issue 反向定位配置文件

    # 逐行解析 cfg 键值。
    for str_line in path_cfg.read_text(encoding="utf-8", errors="ignore").splitlines():

        # 空行或非目标键会被解析 helper 跳过。
        tuple_entry: tuple[str, str] | None = _parse_hls_cfg_line(str_line)  # 当前 cfg 行解析出的目标键值

        # 非目标 cfg 行不影响接口契约。
        if tuple_entry is None:

            # 当前 cfg 行不是 workflow 需要的键。
            continue

        # 将 syn.file/tb.file 聚合为列表并保留首个快捷字段。
        _merge_hls_cfg_entry(dict_cfg, tuple_entry)

    # 返回解析后的 cfg 契约。
    return dict_cfg

# 查找首个 HLS cfg 文件。
def _first_hls_cfg_path(root: Path) -> Path | None:
    """
    返回排序后第一个 cfg 文件路径。

    参数:
        root: HLS 产物根目录，dtype=Path，unit=filesystem path。
    返回:
        cfg 文件路径或 None，dtype=Path or None，unit=filesystem path。
    """

    # cfg 文件按路径排序，保持旧版 break-after-first 行为。
    list_cfg_paths: list[Path] = sorted(  # 所有 cfg 候选
        path_file  # 遍历到的 cfg 路径
        for path_file in root.rglob("*")  # 遍历仓库内所有路径
        if path_file.is_file() and path_file.suffix.lower() == HLS_CONFIG_SUFFIX  # 仅保留 cfg 文件
    )  # HLS cfg 候选路径列表

    # 取第一个 cfg 文件；不存在时返回 None。
    return list_cfg_paths[0] if list_cfg_paths else None

# 解析单行 HLS cfg。
def _parse_hls_cfg_line(line: str) -> tuple[str, str] | None:
    """
    从 cfg 行中提取目标键和值。

    参数:
        line: cfg 原始行文本，dtype=str，unit=text。
    返回:
        键值元组或 None，dtype=tuple[str, str] or None，unit=text pair。
    """

    # 正则只接受 syn.file、tb.file、syn.top、part 和 clock。
    list_cfg_matches: list[tuple[str, str]] = HLS_CFG_LINE_PATTERN.findall(line)  # 目标 cfg 行键值候选

    # 非目标键值行直接忽略。
    if not list_cfg_matches:

        # None 表示当前行不属于接口契约输入。
        return None

    # cfg 行正则是整行匹配，列表至多有一个键值元组。
    tuple_cfg_fields: tuple[str, str] = list_cfg_matches[0]  # 当前 cfg 行的字段名和值

    # 第一项标识 workflow 支持的 cfg 字段。
    str_key: str = tuple_cfg_fields[0]  # workflow 支持的 cfg 字段名

    # 第二项保存该 cfg 字段的未加引号文本值。
    str_cfg_value: str = tuple_cfg_fields[1]  # cfg 字段对应的原始文本值

    # 返回解析出的键值对。
    return str_key, str_cfg_value

# 合并单个 HLS cfg 键值。
def _merge_hls_cfg_entry(dict_cfg: dict[str, Any], tuple_entry: tuple[str, str]) -> None:
    """
    将 cfg 键值写入契约字典。

    参数:
        dict_cfg: cfg 契约字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        tuple_entry: cfg 键值元组，dtype=tuple[str, str]，unit=text pair。
    返回:
        无业务返回值；函数会原地更新 dict_cfg。
    """

    # 拆出 cfg 键和值，便于分支处理。
    str_key, str_cfg_value = tuple_entry  # 待写入 cfg 契约的字段和值

    # syn.file 既进入多文件列表，也保留首个快捷字段。
    if str_key == "syn.file":

        # 收集综合源文件列表。
        dict_cfg.setdefault("syn.files", []).append(str_cfg_value)

        # 首个 syn.file 兼容旧版字段。
        dict_cfg.setdefault("syn.file", str_cfg_value)

    # tb.file 与 syn.file 使用同样的双字段契约。
    elif str_key == "tb.file":

        # 收集 testbench 源文件列表。
        dict_cfg.setdefault("tb.files", []).append(str_cfg_value)

        # 旧消费者仍可能读取单值 tb.file，这里保留兼容字段但以 tb.files 为主。
        dict_cfg.setdefault("tb.file", str_cfg_value)

    # 其他目标键按标量写入。
    else:

        # 标量 cfg 字段直接覆盖同名键，反映最新解析值。
        dict_cfg[str_key] = str_cfg_value  # syn.top/part/clock 标量配置值

# 从源码行中筛选并保存 verifier 使用的 HLS INTERFACE pragma 契约。
def _extract_hls_pragmas(text: str) -> list[dict[str, str]]:
    """
    从 HLS 源码中提取 INTERFACE pragma 字段。

    参数:
        text: 合并后的 HLS 源码文本，dtype=str，unit=text。
    返回:
        pragma 契约列表，shape=(n pragmas)，dtype=list[dict[str, str]]，unit=JSON array。
    """

    # pragma 列表保留源码行出现顺序。
    list_pragmas: list[dict[str, str]] = []  # 按源码顺序保存的 INTERFACE pragma 契约

    # 逐行扫描，避免普通注释或其他 pragma 进入接口契约。
    for str_line in text.splitlines():

        # 只处理 HLS INTERFACE pragma。
        if "#pragma HLS INTERFACE" not in str_line:

            # 当前行不是接口 pragma。
            continue

        # 追加 port/mode/bundle 三个 verifier 关心字段。
        list_pragmas.append(_pragma_contract(str_line))

    # 返回所有接口 pragma。
    return list_pragmas

# 构造单条 pragma 契约。
def _pragma_contract(line: str) -> dict[str, str]:
    """
    从单条 pragma 行中提取接口字段。

    参数:
        line: pragma 原始行文本，dtype=str，unit=text。
    返回:
        pragma 契约字典，shape=(4 fields)，dtype=dict[str, str]，unit=JSON object。
    """

    # mode 可以是 mode=xxx，也可以是 INTERFACE 后的首个 token。
    str_mode: str = _pragma_value(line, "mode") or _pragma_mode(line)  # pragma 接口模式

    # 返回字段名与旧版契约保持一致。
    return {
        "line": line.strip(),
        "port": _pragma_value(line, "port"),
        "mode": str_mode,
        "bundle": _pragma_value(line, "bundle"),
    }

# 富化 HLS 顶层函数参数接口。
def _hls_argument_contract(
    functions: list[dict[str, Any]],
    top: str | None,
    pragmas: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    结合函数签名和 pragma 生成顶层参数契约。

    参数:
        functions: HLS 函数契约列表，shape=(n functions)，dtype=list[dict[str, Any]]，unit=JSON array。
        top: HLS 顶层函数名，dtype=str or None，unit=dimensionless。
        pragmas: HLS INTERFACE pragma 列表，shape=(n pragmas)，dtype=list[dict[str, str]]，unit=JSON array。
    返回:
        参数接口契约列表，shape=(n args)，dtype=list[dict[str, str]]，unit=JSON array。
    """

    # 只富化顶层函数参数；top 缺失时保持空列表。
    list_arguments: list[dict[str, str]] = _top_function_args(functions, top)  # 顶层函数参数列表

    # pragma 按 port 聚合，方便参数名查找接口模式。
    dict_pragmas_by_port: dict[str, list[dict[str, str]]] = _pragmas_by_port(pragmas)  # 参数名到对应 INTERFACE pragma 的索引

    # 富化后的参数列表保留函数签名顺序。
    list_enriched: list[dict[str, str]] = []  # HLS 参数接口契约列表

    # 逐个参数补充 interface 和 bundle。
    for dict_argument in list_arguments:

        # 非字典参数来自脏输入时跳过，匹配旧版宽松行为。
        if not isinstance(dict_argument, dict):

            # 跳过无法读取 name/type 的参数条目。
            continue

        # 参数名用于匹配 pragma port。
        str_name: str = str(dict_argument.get("name") or "")  # 当前顶层参数用于匹配 pragma port 的名称

        # 参数类型来自 C/C++ 函数签名。
        str_type: str = str(dict_argument.get("type") or "")  # HLS 参数类型

        # 同一 port 可能有 mode 和 bundle 两类 pragma。
        list_matches: list[dict[str, str]] = dict_pragmas_by_port.get(str_name, [])  # 当前参数 pragma 列表

        # mode 列表按源码出现顺序保留。
        list_modes: list[str] = _pragma_values(list_matches, "mode")  # 当前参数接口模式列表

        # bundle 名称按 pragma 出现顺序保留，便于比较端口绑定是否发生漂移。
        list_bundles: list[str] = _pragma_values(list_matches, "bundle")  # 当前参数 bundle 列表

        # 追加当前参数的富化契约。
        list_enriched.append(
            {
                "name": str_name,
                "type": str_type,
                "interface": list_modes[0] if list_modes else "",
                "bundle": list_bundles[0] if list_bundles else "",
            }
        )

    # 返回顶层参数接口契约。
    return list_enriched

# 查找顶层函数参数数组。
def _top_function_args(functions: list[dict[str, Any]], top: str | None) -> list[dict[str, str]]:
    """
    从函数契约列表中取得顶层函数参数。

    参数:
        functions: HLS 函数契约列表，shape=(n functions)，dtype=list[dict[str, Any]]，unit=JSON array。
        top: HLS 顶层函数名，dtype=str or None，unit=dimensionless。
    返回:
        顶层参数列表，shape=(n args)，dtype=list[dict[str, str]]，unit=JSON array。
    """

    # 顶层函数名缺失时无法选择参数契约。
    if not top:

        # 空列表表示没有可审计顶层参数。
        return []

    # 遍历函数契约寻找 top 名称。
    for dict_function in functions:

        # 函数名匹配时返回其 args 字段。
        if dict_function.get("name") == top:

            # args 字段由 _parse_cpp_args 生成。
            return list(dict_function.get("args") or [])

    # 未找到 top 时保留旧版空参数行为。
    return []

# 将 pragma 按 port 字段分组。
def _pragmas_by_port(pragmas: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """
    按 port 字段聚合 pragma。

    参数:
        pragmas: HLS pragma 列表，shape=(n pragmas)，dtype=list[dict[str, str]]，unit=JSON array。
    返回:
        port 到 pragma 列表的映射，shape=(n ports)，dtype=dict[str, list[dict[str, str]]]，unit=JSON object。
    """

    # port 分组用于顶层参数接口富化。
    dict_by_port: dict[str, list[dict[str, str]]] = {}  # INTERFACE pragma 按 port 分组后的索引

    # 空 port 的 pragma 不参与参数富化。
    for dict_pragma in pragmas:

        # port 字段可能缺失或为空。
        str_port: str = str(dict_pragma.get("port") or "")  # 当前 pragma 绑定的 HLS 端口名

        # 只聚合具名端口。
        if str_port:

            # 同一端口允许多条 pragma。
            dict_by_port.setdefault(str_port, []).append(dict_pragma)

    # 返回按端口聚合的 pragma。
    return dict_by_port

# 提取 pragma 字段的非空值列表。
def _pragma_values(pragmas: list[dict[str, str]], key: str) -> list[str]:
    """
    从 pragma 列表提取指定字段的非空字符串。

    参数:
        pragmas: 同一 port 的 pragma 列表，shape=(n pragmas)，dtype=list[dict[str, str]]，unit=JSON array。
        key: pragma 字段名，dtype=str，unit=dimensionless。
    返回:
        字段值列表，shape=(n values)，dtype=list[str]，unit=text items。
    """

    # 保留源码顺序，只丢弃空字段。
    list_values: list[str] = [
        str(dict_item.get(key) or "")  # 字段值统一转成字符串
        for dict_item in pragmas  # 按提取顺序遍历 pragma 记录
        if dict_item.get(key)  # 过滤缺失字段的 pragma
    ]  # pragma 字段值列表

    # 返回非空字段值。
    return list_values

# 提取 HLS control 协议。
def _hls_control_mode(pragmas: list[dict[str, str]]) -> str | None:
    """
    从 return port pragma 中读取控制协议。

    参数:
        pragmas: HLS INTERFACE pragma 列表，shape=(n pragmas)，dtype=list[dict[str, str]]，unit=JSON array。
    返回:
        control mode 或 None，dtype=str or None，unit=dimensionless。
    """

    # 控制协议来自 port=return 的 INTERFACE pragma。
    for dict_pragma in pragmas:

        # 端口名为 return 且 mode 非空时采用该控制模式。
        if str(dict_pragma.get("port") or "") == "return" and dict_pragma.get("mode"):

            # mode 字段保留源码中的拼写。
            return str(dict_pragma["mode"])

    # 缺少 return port pragma 时 control_mode 留空。
    return None

# 读取 pragma 中的 key=value 字段。
def _pragma_value(line: str, key: str) -> str:
    """
    从 pragma 行中读取指定 key 的值。

    参数:
        line: pragma 原始行文本，dtype=str，unit=text。
        key: 目标字段名，dtype=str，unit=dimensionless。
    返回:
        字段值或空字符串，dtype=str，unit=text。
    """

    # key 会经过 re.escape，避免字段名影响正则含义。
    list_pragma_values: list[str] = re.findall(  # 提取当前 pragma 行里该 key 的赋值 token
        rf"\b{re.escape(key)}\s*=\s*([A-Za-z0-9_]+)",  # 目标字段对应的 value 捕获组
        line,  # 用来抽取 key=value 形式字段的 pragma 行
    )  # pragma key=value 字段候选列表

    # 匹配成功时返回第一捕获组。
    return list_pragma_values[0] if list_pragma_values else ""

# 读取 INTERFACE 后的简写模式。
def _pragma_mode(line: str) -> str:
    """
    从 pragma 行中读取 INTERFACE 后的首个模式 token。

    参数:
        line: pragma 原始行文本，dtype=str，unit=text。
    返回:
        pragma 模式或空字符串，dtype=str，unit=text。
    """

    # 兼容 "#pragma HLS INTERFACE s_axilite ..." 这种无 mode= 的形式。
    list_mode_values: list[str] = re.findall(  # 接口模式候选值
        r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z0-9_]+)",  # 捕获 INTERFACE 关键字后的模式 token
        line,  # 用来兼容简写 INTERFACE 形式的 pragma 行
    )  # pragma 简写模式候选列表

    # 匹配成功时返回模式 token。
    return list_mode_values[0] if list_mode_values else ""

# 选择 HLS 顶层函数名。
def _hls_top_function(dict_cfg: dict[str, Any], functions: list[dict[str, Any]]) -> str | None:
    """
    根据 cfg 与函数列表选择 HLS top function。

    参数:
        dict_cfg: HLS cfg 契约，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        functions: HLS 函数契约列表，shape=(n functions)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        顶层函数名或 None，dtype=str or None，unit=dimensionless。
    """

    # cfg 的 syn.top 优先级最高。
    str_cfg_top: str = str(dict_cfg.get("syn.top") or "")  # cfg 声明的顶层函数名

    # syn.top 存在时直接使用。
    if str_cfg_top:

        # 返回 cfg 中的顶层函数名。
        return str_cfg_top

    # 缺少 syn.top 时退回源码中的非 testbench 函数。
    return _first_non_test_function(functions)

# 检查 HLS cfg 与源码函数的一致性。
def _hls_contract_issues(
    dict_cfg: dict[str, Any],
    functions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """
    生成 HLS 接口契约中的结构化问题。

    参数:
        dict_cfg: HLS cfg 契约，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        functions: HLS 函数契约列表，shape=(n functions)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        issue 列表，shape=(n issues)，dtype=list[dict[str, str]]，unit=JSON array。
    """

    # issue 顺序保持旧版：先 top 漂移，再 syn.file 缺失。
    list_issues: list[dict[str, str]] = []  # HLS 接口 issue 列表

    # cfg top 与源码函数列表不一致时报告错误。
    list_issues.extend(_hls_top_issues(dict_cfg, functions))

    # cfg 缺少 syn.file 时报告工具链配置警告。
    list_issues.extend(_hls_cfg_file_issues(dict_cfg))

    # 返回 HLS 接口审计问题。
    return list_issues

# 检查 cfg syn.top 是否能在源码中找到。
def _hls_top_issues(
    dict_cfg: dict[str, Any],
    functions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """
    生成 HLS top function 漂移问题。

    参数:
        dict_cfg: HLS cfg 契约，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        functions: HLS 函数契约列表，shape=(n functions)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        top 漂移 issue 列表，shape=(0 or 1)，dtype=list[dict[str, str]]，unit=JSON array。
    """

    # syn.top 是 cfg 唯一显式声明的顶层入口，没有它就无法和源码函数名做稳定比对。
    str_cfg_top: str = str(dict_cfg.get("syn.top") or "")  # cfg 显式声明的顶层入口

    # 没有 cfg top 或没有函数列表时保持旧版宽松行为。
    if not str_cfg_top or not functions:

        # 无法比较时不产生 top issue。
        return []

    # 源码函数名集合用于快速判断 cfg top 是否存在。
    set_function_names: set[str] = {
        str(dict_item["name"])  # 源码函数名文本
        for dict_item in functions  # 遍历已抽取的函数契约
    }  # HLS 源码函数名集合

    # cfg top 存在于源码函数列表时通过。
    if str_cfg_top in set_function_names:

        # top 一致时不产生 issue。
        return []

    # 保留历史英文 message 文本，便于旧 trace 检索。
    return [
        {
            "severity": "error",
            "source": "current_module_issue",
            "message": "cfg syn.top does not match any HLS source function.",
            "path": str(dict_cfg.get("path", "")),
        }
    ]

# 检查 cfg 是否声明 syn.file。
def _hls_cfg_file_issues(dict_cfg: dict[str, Any]) -> list[dict[str, str]]:
    """
    生成 cfg syn.file 缺失问题。

    参数:
        dict_cfg: HLS cfg 契约，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    返回:
        syn.file 缺失 issue 列表，shape=(0 or 1)，dtype=list[dict[str, str]]，unit=JSON array。
    """

    # syn.file 存在时工具链有明确综合输入。
    if dict_cfg.get("syn.file"):

        # cfg 已声明 syn.file。
        return []

    # 保留历史英文 warning 文本。
    return [
        {
            "severity": "warning",
            "source": "toolchain_issue",
            "message": "cfg syn.file is missing.",
            "path": str(dict_cfg.get("path", "")),
        }
    ]

# 选择第一个非 testbench 函数。
def _first_non_test_function(functions: list[dict[str, Any]]) -> str | None:
    """
    从函数契约中选择默认 HLS top function。

    参数:
        functions: HLS 函数契约列表，shape=(n functions)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        函数名或 None，dtype=str or None，unit=dimensionless。
    """

    # 优先选择不是 main 且不以 _tb 结尾的函数。
    for dict_item in functions:

        # 读取函数名用于过滤 testbench。
        str_name: str = str(dict_item["name"])  # 用于排除 main/_tb 的 HLS 函数名

        # 跳过 main 和 testbench 入口。
        if str_name != "main" and not str_name.endswith("_tb"):

            # 返回首个看起来像 kernel 的函数名。
            return str_name

    # 只有 main/testbench 时保留旧版第一个函数兜底。
    return str(functions[0]["name"]) if functions else None

# 按 name 字段去重字典列表。
def _dedupe_by_name(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    保留每个 name 第一次出现的契约条目。

    参数:
        items: 带 name 字段的字典列表，shape=(n items)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        去重后的条目列表，shape=(n unique)，dtype=list[dict[str, Any]]，unit=JSON array。
    """

    # 已见名称集合用于维持第一次出现优先。
    set_seen: set[str] = set()  # 已保留名称集合

    # 去重结果保持原始顺序。
    list_unique_items: list[dict[str, Any]] = []  # 去重后的契约条目

    # 逐项检查 name 是否已经出现。
    for dict_item in items:

        # name 字段作为去重 key。
        str_name: str = str(dict_item.get("name"))  # 契约条目名称

        # 已见名称不再重复写入契约。
        if str_name in set_seen:

            # 保留第一次出现的函数契约。
            continue

        # 记录新名称。
        set_seen.add(str_name)

        # 当前条目是该名称的第一个契约。
        list_unique_items.append(dict_item)

    # 返回去重后的契约列表。
    return list_unique_items

# 汇总向量契约中的 case id。
def _case_ids(contracts: list[dict[str, Any]]) -> list[str]:
    """
    从向量契约列表中按首次出现顺序提取 case id。

    参数:
        contracts: 向量契约列表，shape=(n contracts)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        case id 列表，shape=(n ids)，dtype=list[str]，unit=dimensionless items。
    """

    # case id 去重但保持首次出现顺序。
    list_ids: list[str] = []  # 向量 case id 列表

    # 每个 vectors.json 契约都可能携带 case_ids。
    for dict_contract in contracts:

        # 缺失 case_ids 时按空列表处理。
        list_contract_ids: list[Any] = list(dict_contract.get("case_ids", []) or [])  # 单个契约 case id 候选

        # 逐个合并 case id。
        for obj_case_id in list_contract_ids:

            # case id 写入 JSON 前统一转成字符串。
            str_case_id: str = str(obj_case_id)  # 单个 case id 文本

            # 首次出现的 case id 才写入契约。
            if str_case_id not in list_ids:

                # 追加新的 case id。
                list_ids.append(str_case_id)

    # 返回去重后的 case id 列表。
    return list_ids

# 向量样本可能重复引用同一份摘要，这里抽出稳定去重后的 hash 列表供主契约写入。
def _vector_hashes(contracts: list[dict[str, Any]]) -> list[str]:
    """
    从向量契约列表中按首次出现顺序提取 sha256。

    参数:
        contracts: 向量契约列表，shape=(n contracts)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        sha256 列表，shape=(n hashes)，dtype=list[str]，unit=hash text items。
    """

    # 结果列表同时承担去重和保序职责，后续输出要和样本首次出现顺序一致。
    list_hashes: list[str] = []  # 去重且保序的摘要结果

    # 每个向量契约最多贡献一个 sha256。
    for dict_contract in contracts:

        # 缺失 sha256 的条目直接跳过，避免把空字符串混入摘要集合。
        str_sha256: str = str(dict_contract.get("sha256") or "")  # 向量契约携带的 sha256 文本

        # 非空且未出现过的 hash 才写入列表。
        if str_sha256 and str_sha256 not in list_hashes:

            # 统一转成字符串，保持 JSON 类型稳定。
            list_hashes.append(str_sha256)

    # 返回值保留首次出现顺序，供上层写入 interface contract。
    return list_hashes

# 从 HLS 源码注释中扫描向量 hash 标记。
def _scan_vector_hashes(text_by_path: dict[str, str]) -> list[str]:
    """
    从 HLS 文件文本中扫描参考向量 hash。

    参数:
        text_by_path: HLS 源码文本映射，shape=(n files)，dtype=dict[str, str]，unit=text by path。
    返回:
        sha256 列表，shape=(n hashes)，dtype=list[str]，unit=hash text items。
    """

    # 源码 hash 作为缺少 vectors.json 时的兜底证据。
    list_hashes: list[str] = []  # HLS 源码中的向量 hash 列表

    # 逐文件扫描 VECTOR_HASH_TAG。
    for str_text in text_by_path.values():

        # extract_vector_hashes 负责具体标记格式识别。
        for str_hash in extract_vector_hashes(str_text):

            # 去重但保留首次出现顺序。
            if str_hash not in list_hashes:

                # 追加新的源码 hash。
                list_hashes.append(str_hash)

    # 返回源码中的向量 hash。
    return list_hashes

# 计算接口契约稳定 hash。
def _stable_hash(contract: dict[str, Any]) -> str:
    """
    对接口契约计算稳定 sha256。

    参数:
        contract: 接口契约字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    返回:
        sha256 十六进制文本，dtype=str，unit=hash text。
    """

    # hash 输入排除运行派生字段和本地根目录名。
    dict_payload: dict[str, Any] = {
        str_key: obj_contract_value  # 保留的契约字段
        for str_key, obj_contract_value in contract.items()  # 遍历原契约字段
        if str_key not in {"interface_sha256", "root", "source_root"}  # 排除运行期派生字段
    }  # hash 输入契约载荷

    # JSON canonical form 保证字段顺序和空白稳定。
    str_canonical: str = json.dumps(  # 稳定 JSON 文本
        dict_payload,  # 去掉运行派生字段后的载荷
        ensure_ascii=False,  # 保留中文字段原样编码
        sort_keys=True,  # 固定字段顺序
        separators=(",", ":"),  # 去掉多余空白
    )  # 交给 sha256 的 canonical JSON

    # 返回 UTF-8 字节的 sha256。
    return hashlib.sha256(str_canonical.encode("utf-8")).hexdigest()
