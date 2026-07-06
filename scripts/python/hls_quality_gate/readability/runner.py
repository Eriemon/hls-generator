"""运行 HLS 可读性质量门并汇总规则诊断。"""

# 延迟注解解析，避免运行期导入顺序影响类型表达式
from __future__ import annotations

# 标准库依赖
import tempfile
from pathlib import Path
from typing import Any

# HLS 注释与契约规则
from .comment_rules import check_comment_rules
from .contract_rules import check_contract_rules

# HLS AST 与词法辅助
from .cpp_ast_provider import (
    AstParseResult,
    CppAstProvider,
    ast_metrics_for_files,
    select_cpp_ast_provider,
    write_fake_hls_headers,
)
from .cpp_lexer import HLS_AST_SUFFIXES, code_token_fingerprint, collect_hls_files, parse_functions

# HLS 命名、pragma 和结构规则
from .naming_rules import check_naming_rules
from .pragma_rules import check_pragma_rules
from .profiles import get_hls_profile_config
from .report import HlsGateIssue, HlsGateReport, make_issue
from .structure_rules import check_structure_rules

# HLS 可读性质量门入口
def run_hls_readability_gate(
    root: Path,
    *,
    profile: str = "kernel",
    style: str = "current-project",
    baseline_root: Path | None = None,
    fail_on_warning: bool = False,
    top_function: str | None = None,
) -> HlsGateReport:
    """
    对目标目录运行 HLS 可读性规则并返回结构化报告。

    :param root: 待检查的 HLS 文件或目录路径。
    :param profile: HLS 可读性规则使用的语义配置名称。
    :param style: 规则配置采用的项目风格名称。
    :param baseline_root: 注释-only 对比使用的基线目录，缺省时只做改写后解析。
    :param fail_on_warning: 是否把 warning 视作报告失败。
    :param top_function: 调用方显式指定的 HLS 顶层函数名。
    :return: 包含问题、指标和失败策略的 HLS 可读性报告。
    """

    # 解析目标路径，统一后续报告中的根目录语义
    path_resolved_root = Path(root).resolve()  # 质量门实际扫描根路径

    # 读取 profile/style 组合对应的 HLS 规则配置
    obj_config = get_hls_profile_config(profile, style=style)  # HLS 可读性规则配置

    # 保存所有规则返回的问题对象
    list_issues: list[HlsGateIssue] = []  # HLS 规则诊断集合

    # 组织报告需要的初始指标容器
    dict_metrics = _initial_metrics(obj_config, baseline_root)  # HLS 门禁指标载荷

    # 缺失目标路径直接生成 HG000 报告
    if not path_resolved_root.exists():

        # 返回缺失目标对应的质量门报告
        return _target_error_report(
            path_resolved_root,
            profile,
            style,
            dict_metrics,
            fail_on_warning,
            "target missing: HLS readability gate path does not exist.",
        )

    # 收集目标范围内所有 HLS 源文件
    list_files = collect_hls_files(path_resolved_root)  # 待执行 HLS 门禁的文件列表

    # 无 HLS 文件时直接生成 HG000 报告
    if not list_files:

        # 返回空目标对应的质量门报告
        return _target_error_report(
            path_resolved_root,
            profile,
            style,
            dict_metrics,
            fail_on_warning,
            "no HLS files found under target path.",
        )

    # 将文件清单写入指标载荷
    dict_metrics["checked_files"] = _checked_file_names(path_resolved_root, list_files)  # 报告中的 HLS 文件清单

    # 读取 AST provider 可用性和解析统计
    dict_ast_metrics = ast_metrics_for_files(path_resolved_root, _ast_source_files(list_files))  # 当前 HLS 文件集合的 AST 能力统计

    # 合并 AST provider 统计到总指标
    dict_metrics.update(dict_ast_metrics)

    # 推断或复用调用方指定的顶层函数名
    str_top_function = top_function or _infer_top_function(list_files, path_resolved_root)  # HLS 顶层函数名

    # 将顶层函数名写入报告指标
    dict_metrics["top_function"] = str_top_function  # 规则共享的顶层函数名

    # 逐文件运行 HLS 规则集合
    for path_file in list_files:

        # 调用规则调度器检查单个 HLS 文件
        _extend_file_issues(
            path_resolved_root,
            path_file,
            obj_config,
            str_top_function,
            list_issues,
            dict_metrics,
        )

    # 对注释-only 改写做 token 与 AST 防护，并把结果合并到报告载荷。
    _extend_comment_guard_report(path_resolved_root, list_files, baseline_root, list_issues, dict_metrics)

    # 统计注释质量相关规则数量
    dict_metrics["comment_quality_gate"] = _comment_quality_summary(list_issues)  # 注释质量摘要

    # 返回聚合后的 HLS 可读性质量门报告
    return _report(path_resolved_root, profile, style, list_issues, dict_metrics, fail_on_warning)

# 生成目标缺失或空目标的统一报告。
def _target_error_report(
    path_root: Path,
    profile: str,
    style: str,
    dict_metrics: dict[str, Any],
    fail_on_warning: bool,
    message: str,
) -> HlsGateReport:
    """
    构造无法扫描目标路径时的 HG000 报告。

    :param path_root: HLS 门禁扫描根目录。
    :param profile: 本轮报告使用的 profile 名称。
    :param style: 本轮报告使用的 style 名称。
    :param dict_metrics: 已初始化的报告指标字典。
    :param fail_on_warning: 是否把 warning 视作失败。
    :param message: HG000 诊断正文。
    :return: 只包含目标级错误的 HLS 可读性报告。
    """

    # 构造目标级错误问题，保持 HG000 历史契约。
    hls_gate_issue_item: HlsGateIssue = make_issue(  # 复用统一 issue 工厂构造目标级失败对象
        "HG000",  # 目标级失败沿用固定 HG000 规则号
        "error",  # 目标不可扫描属于错误级别
        str(path_root),  # 问题路径固定指向当前扫描根
        1,  # 目标级失败没有具体源码位置时锚定首行
        message,  # 调用方整理好的目标级失败正文
        node_kind="target",  # 让报告明确这是目标级而非文件级问题
    )  # 目标不可扫描诊断

    # 使用统一报告构造器完成排序和摘要统计。
    return _report(path_root, profile, style, [hls_gate_issue_item], dict_metrics, fail_on_warning)

# 生成报告中展示的 HLS 文件相对路径。
def _checked_file_names(path_root: Path, list_files: list[Path]) -> list[str]:
    """
    返回本轮实际参与检查的 HLS 相对路径清单。

    :param path_root: HLS 门禁扫描根目录。
    :param list_files: collect_hls_files 返回的文件路径列表。
    :return: 供报告 metrics.checked_files 使用的相对路径列表。
    """

    # 逐项生成报告中展示的 HLS 相对路径。
    list_checked_files = [path_file.relative_to(path_root).as_posix() for path_file in list_files]  # 把磁盘路径折叠成报告使用的相对 POSIX 路径

    # 返回稳定顺序的文件清单。
    return list_checked_files

# 筛选可由 AST provider 解析的 HLS 源文件。
def _ast_source_files(list_files: list[Path]) -> list[Path]:
    """
    返回需要送入 C/C++ AST provider 的文件列表。

    :param list_files: collect_hls_files 返回的文件路径列表。
    :return: 后缀属于 HLS_AST_SUFFIXES 的 C/C++ 源文件。
    """

    # 只把 C/C++ 源文件交给 AST provider 分析。
    list_ast_files = [
        path_file  # 当前后缀已命中 HLS_AST_SUFFIXES，可送入 AST provider
        for path_file in list_files  # 在全部 HLS 文件里保留可解析源文件
        if path_file.suffix.lower() in HLS_AST_SUFFIXES  # 只接受 AST provider 支持的源文件后缀
    ]  # AST 指标输入文件列表

    # 返回 AST provider 的输入文件列表。
    return list_ast_files

# 合并注释-only 防护的诊断和指标。
def _extend_comment_guard_report(
    path_root: Path,
    list_files: list[Path],
    baseline_root: Path | None,
    list_issues: list[HlsGateIssue],
    dict_metrics: dict[str, Any],
) -> None:
    """
    运行注释-only 对比并写回主报告问题与指标。

    :param path_root: HLS 门禁扫描根目录。
    :param list_files: 本轮扫描到的 HLS 文件列表。
    :param baseline_root: 注释-only 对比使用的基线目录。
    :param list_issues: 主报告累计问题列表。
    :param dict_metrics: 主报告指标字典。
    :return: 本函数只更新传入列表和字典，无业务返回值。
    """

    # 对注释-only 改写做 token 与 AST 防护。
    tuple_comment_guard_result = _comment_only_compare_issues(path_root, list_files, baseline_root)  # 收集注释-only 防护产出的诊断与指标

    # 合并注释-only 对比问题。
    list_issues.extend(tuple_comment_guard_result[0])

    # 保存注释-only 对比指标。
    dict_metrics["hls_ast_comment_guard"] = tuple_comment_guard_result[1]  # AST 注释防护指标

    # 注释-only 防护结果已并入主报告。
    return None

# 构造质量门初始指标载荷
def _initial_metrics(obj_config: Any, baseline_root: Path | None) -> dict[str, Any]:
    """
    生成质量门报告使用的基础指标字段。

    :param obj_config: HLS profile/style 已解析后的规则配置对象。
    :param baseline_root: 注释-only 对比使用的基线目录。
    :return: 带默认字段的指标字典。
    """

    # 记录注释-only 对比使用的基线目录文本
    str_baseline_root = str(baseline_root.resolve()) if baseline_root else None  # 基线目录展示文本

    # 建立后续规则共享的指标容器
    dict_metrics: dict[str, Any] = {}  # 报告指标根字典

    # 保存 profile 配置快照
    dict_metrics["profile_config"] = obj_config.to_dict()  # HLS profile 配置快照

    # 初始化已扫描 HLS 文件清单
    dict_metrics["checked_files"] = []  # 扫描范围内的 HLS 相对路径列表

    # 初始化 AST provider 名称字段
    dict_metrics["ast_provider"] = None  # AST 解析工具名称占位

    # 初始化 AST provider 可用性状态
    dict_metrics["ast_provider_unavailable"] = False  # AST 解析工具不可用标志

    # 保存注释-only 对比基线目录
    dict_metrics["comment_only_baseline_root"] = str_baseline_root  # 注释对比基线目录文本

    # 初始化注释质量子门禁指标
    dict_metrics["comment_quality_gate"] = {}  # 注释规则统计容器

    # 初始化 HLS 命名子门禁指标
    dict_metrics["hls_naming_gate"] = {}  # 命名规则统计容器

    # 返回供主入口继续填充的指标容器
    return dict_metrics

# 运行单个 HLS 文件上的规则集合
def _extend_file_issues(
    path_root: Path,
    path_file: Path,
    obj_config: Any,
    str_top_function: str | None,
    list_issues: list[HlsGateIssue],
    dict_metrics: dict[str, Any],
) -> None:
    """
    对单个 HLS 文件执行注释、契约、命名、结构和 pragma 检查。

    :param path_root: HLS 可读性门禁的扫描根目录。
    :param path_file: 当前正在检查的 HLS 文件路径。
    :param obj_config: HLS profile/style 规则配置。
    :param str_top_function: 当前报告认定的 HLS 顶层函数名。
    :param list_issues: 聚合所有 HLS 诊断的问题列表。
    :param dict_metrics: 质量门报告的指标字典。
    :return: 本函数只追加问题和指标，无业务返回值。
    """

    # 追加当前文件的注释规则诊断
    list_issues.extend(
        check_comment_rules(path_root, path_file, obj_config, top_function=str_top_function)
    )

    # 追加当前文件的接口契约诊断
    list_issues.extend(
        check_contract_rules(path_root, path_file, obj_config, top_function=str_top_function)
    )

    # 记录命名规则执行前的问题数量
    int_issue_count_before_naming = len(list_issues)  # 命名规则之前的累计问题数

    # 追加当前文件的 HLS 命名诊断
    list_issues.extend(check_naming_rules(path_root, path_file, obj_config))

    # 更新 HLS 命名子门禁问题计数
    _add_naming_issue_count(dict_metrics, len(list_issues) - int_issue_count_before_naming)

    # 追加当前文件的结构复杂度诊断
    list_issues.extend(check_structure_rules(path_root, path_file, obj_config))

    # 追加当前文件的 pragma 语义诊断
    list_issues.extend(check_pragma_rules(path_root, path_file, obj_config))

    # 当前文件所有规则已把问题追加到共享列表
    return None

# 累加命名子门禁的问题数量
def _add_naming_issue_count(dict_metrics: dict[str, Any], int_new_issues: int) -> None:
    """
    更新报告指标中的 HLS 命名问题计数。

    :param dict_metrics: 质量门报告的指标字典。
    :param int_new_issues: 当前文件命名规则新增的问题数量。
    :return: 本函数只更新指标，无业务返回值。
    """

    # 确保命名子门禁有固定计数字段
    dict_naming_metrics = dict_metrics.setdefault("hls_naming_gate", {})  # 命名规则指标容器

    # 初始化缺省问题数量
    dict_naming_metrics.setdefault("issues", 0)

    # 累加当前文件产生的命名问题
    dict_naming_metrics["issues"] += int_new_issues  # 命名规则累计问题数量

    # 命名问题数量已写回共享指标
    return None

# 汇总注释质量门禁规则
def _comment_quality_summary(list_issues: list[HlsGateIssue]) -> dict[str, Any]:
    """
    统计 HLS 注释质量相关规则的问题数量。

    :param list_issues: 已聚合的全部 HLS 诊断问题。
    :return: 注释质量子门禁的规则清单和问题数量。
    """

    # 注释质量相关的 HLS 规则编号
    set_comment_rules = {f"HG{int_index:03d}" for int_index in range(1, 12)}  # HG001-HG011 全部计入注释质量摘要

    # 统计已聚合问题中属于注释质量规则的数量
    int_comment_issue_count = sum(1 for obj_issue in list_issues if obj_issue.rule in set_comment_rules)  # 从总问题集中筛出注释质量规则命中的条数

    # 构造报告需要的注释质量摘要
    dict_summary = {
        "issues": int_comment_issue_count,  # 注释质量问题数量
        "rules": sorted(set_comment_rules),  # 注释质量规则清单
    }  # 注释质量门禁摘要

    # 返回注释质量子门禁指标
    return dict_summary

# 构造统一 HLS 质量门报告对象
def _report(
    path_root: Path,
    profile: str,
    style: str,
    list_issues: list[HlsGateIssue],
    dict_metrics: dict[str, Any],
    fail_on_warning: bool,
) -> HlsGateReport:
    """
    对问题列表排序并封装为 HLS 可读性报告。

    :param path_root: HLS 门禁扫描根目录。
    :param profile: 本轮报告使用的 profile 名称。
    :param style: 本轮报告使用的 style 名称。
    :param list_issues: 规则检查得到的问题列表。
    :param dict_metrics: 报告携带的指标字典。
    :param fail_on_warning: 是否把 warning 视作失败。
    :return: 可序列化的 HLS 可读性报告对象。
    """

    # 按路径、行号、规则和消息稳定排序
    tuple_sorted_issues = tuple(  # 冻结排序结果，保证报告与 JSON 输出稳定
        sorted(  # 先生成稳定顺序列表，再冻结成不可变元组
            list_issues,  # 当前待稳定排序的问题列表
            key=lambda obj_issue: (obj_issue.path, obj_issue.line, obj_issue.rule, obj_issue.message),  # 按路径、行号、规则号和正文生成稳定排序键
        )
    )  # 稳定排序后的问题元组

    # 返回统一报告对象
    return HlsGateReport(
        target="hls",
        root=str(path_root),
        profile=profile,
        style=style,
        issues=tuple_sorted_issues,
        metrics=dict_metrics,
        fail_on_warning=fail_on_warning,
    )

# 从 HLS 函数列表中推断顶层函数
def _infer_top_function(list_files: list[Path], path_root: Path) -> str | None:
    """
    根据 HLS 源文件中的函数定义推断最可能的顶层 kernel 名称。

    :param list_files: 本轮扫描到的 HLS 文件列表。
    :param path_root: HLS 门禁扫描根目录。
    :return: 推断出的顶层函数名；没有候选函数时返回 None。
    """

    # 收集非测试平台、非 main 的函数名候选
    list_candidates: list[str] = []  # 可能作为 HLS 顶层的函数名

    # 遍历所有 HLS 文件并解析函数签名
    for path_file in list_files:

        # 跳过文件名中显式标记为 testbench 的输入
        if "_tb" in path_file.stem.lower():

            # testbench 文件不参与顶层函数推断
            continue

        # 读取当前文件文本并按行交给轻量解析器
        list_source_lines = path_file.read_text(encoding="utf-8", errors="ignore").splitlines()  # 供轻量函数签名扫描器逐行消费的 HLS 文本

        # 逐个解析源码中可能作为顶层候选的函数签名
        for function_info in parse_functions(list_source_lines):

            # 声明和 main 不作为 kernel 顶层候选
            if function_info.is_declaration or function_info.name in {"main"}:

                # 当前函数不是可用的顶层候选
                continue

            # 记录可作为顶层的函数名
            list_candidates.append(function_info.name)

    # 没有候选时不强行推断顶层函数
    if not list_candidates:

        # 返回空值表示后续规则不使用顶层函数过滤
        return None

    # 优先选择名称明显包含 kernel 或项目目录名的候选
    for str_name in list_candidates:

        # 根据常见 kernel 命名和目录名判断优先候选
        if "kernel" in str_name.casefold() or path_root.name.casefold() in str_name.casefold():

            # 返回最像顶层 kernel 的函数名
            return str_name

    # 没有强特征时使用第一个函数定义作为默认候选
    return list_candidates[0]

# 比较注释-only 改写前后的 token 与 AST
def _comment_only_compare_issues(
    path_root: Path,
    list_files: list[Path],
    baseline_root: Path | None,
) -> tuple[list[HlsGateIssue], dict[str, Any]]:
    """
    检查注释-only 改写是否保持 HLS token 和 AST 结构不变。

    :param path_root: HLS 门禁扫描根目录。
    :param list_files: 本轮扫描到的 HLS 文件列表。
    :param baseline_root: 注释-only 对比使用的基线目录。
    :return: 注释-only 对比产生的问题列表和指标字典。
    """

    # 初始化注释-only 对比指标
    dict_metrics = _initial_comment_guard_metrics(baseline_root)  # 注释-only 对比指标

    # 保存注释-only 对比发现的问题
    list_issues: list[HlsGateIssue] = []  # 注释-only 对比问题列表

    # 只检查可被 C/C++ AST provider 解析的源文件
    list_ast_files: list[Path] = []  # AST 对比文件列表

    # 筛选需要执行 AST 注释防护的 HLS 源文件
    for path_file in list_files:

        # 只保留 C/C++ 源文件给 AST provider
        if path_file.suffix.lower() not in HLS_AST_SUFFIXES:

            # 当前文件不是 AST 防护目标
            continue

        # 保存需要解析的 HLS 源文件
        list_ast_files.append(path_file)

    # 没有 C/C++ 源文件时无需执行 AST provider
    if not list_ast_files:

        # 记录无需解析的 provider 状态
        dict_metrics["provider"] = "not_required_no_hls_source"  # 无 HLS 源文件时的 provider 状态

        # 返回空问题和无需解析的指标
        return list_issues, dict_metrics

    # 选择当前机器可用的 AST provider
    ast_provider_reference: CppAstProvider | None = select_cpp_ast_provider()  # 当前机器可用的 C/C++ AST 解析工具引用

    # 记录 provider 名称，缺失时使用 None
    dict_metrics["provider"] = ast_provider_reference.name if ast_provider_reference else None  # AST 解析工具名称

    # 没有基线且 provider 不可用时只能登记不可用状态
    if ast_provider_reference is None and baseline_root is None:

        # 标记 AST provider 不可用但不生成 comment-only 问题
        dict_metrics["ast_provider_unavailable"] = True  # AST 解析工具缺失状态

        # 返回 provider 不可用时的指标
        return list_issues, dict_metrics

    # 使用临时目录承载假的 HLS include 头文件
    with tempfile.TemporaryDirectory(prefix="hls_readability_ast_") as str_temp_dir:

        # 写入 AST provider 解析需要的 fake HLS 头目录
        path_fake_include = write_fake_hls_headers(Path(str_temp_dir) / "fake_hls_include")  # 临时生成 provider 解析 HLS 头依赖所需的 include 目录

        # 逐个执行改写后解析或基线对比
        for path_file in list_ast_files:

            # 调用单文件注释防护流程
            _extend_comment_guard_file(
                path_root, path_file, baseline_root, ast_provider_reference,
                path_fake_include, list_issues, dict_metrics,
            )

    # 将问题对象转换成指标中的可序列化字典
    dict_metrics["issues"] = [obj_issue.to_dict() for obj_issue in list_issues]  # 把 guard 问题对象展开成 JSON 兼容明细

    # 返回注释-only 对比结果
    return list_issues, dict_metrics

# 构造注释-only 防护指标容器
def _initial_comment_guard_metrics(baseline_root: Path | None) -> dict[str, Any]:
    """
    生成注释-only AST 防护的默认指标字段。

    :param baseline_root: 注释-only 对比使用的基线目录。
    :return: 可继续填充的指标字典。
    """

    # 根据是否存在基线选择对比模式
    str_mode = "comment_only_compare" if baseline_root else "parse_after_only"  # 注释-only 防护模式

    # 构造注释-only 防护指标
    dict_metrics: dict[str, Any] = {}  # 逐步填充 AST 注释防护的报告载荷

    # 保存 AST 注释防护策略名
    dict_metrics["policy"] = "hls_ast_comment_guard"  # 注释-only AST 防护策略标识

    # 保存当前防护运行模式
    dict_metrics["mode"] = str_mode  # 注释-only 防护运行模式

    # 初始化 AST provider 名称
    dict_metrics["provider"] = None  # 先占位，后续再写入实际 provider 名称

    # 初始化已参与 AST 防护的相对路径
    dict_metrics["checked_files"] = []  # 已参与 AST 防护的 HLS 相对路径

    # 初始化 AST 防护问题明细
    dict_metrics["issues"] = []  # AST 注释防护诊断明细

    # 返回注释-only 防护指标
    return dict_metrics

# 处理单个文件的注释-only 防护
def _extend_comment_guard_file(
    path_root: Path, path_file: Path, baseline_root: Path | None,
    obj_provider: Any, path_fake_include: Path,
    list_issues: list[HlsGateIssue], dict_metrics: dict[str, Any],
) -> None:
    """
    对单个 HLS 文件执行注释-only parse 或基线对比。

    :param path_root: HLS 门禁扫描根目录。
    :param path_file: 当前正在检查的 HLS 源文件。
    :param baseline_root: 注释-only 对比使用的基线目录。
    :param obj_provider: 当前机器可用的 AST provider，缺失时为 None。
    :param path_fake_include: AST provider 解析所需的 fake HLS include 目录。
    :param list_issues: 注释-only 对比问题聚合列表。
    :param dict_metrics: 注释-only 防护指标字典。
    :return: 本函数只追加问题和指标，无业务返回值。
    """

    # 计算报告中使用的相对路径
    str_relative_path = path_file.relative_to(path_root).as_posix()  # 当前 HLS 文件相对路径

    # 记录当前文件已参与注释-only 防护
    dict_metrics["checked_files"].append(str_relative_path)

    # 无基线时只检查改写后的文件是否可被 AST provider 解析
    if baseline_root is None:

        # 无基线模式只登记改写后文件是否仍能被 provider 接受
        _record_after_parse_status(
            path_root,
            path_file,
            str_relative_path,
            obj_provider,
            path_fake_include,
            dict_metrics,
        )

        # 无基线模式不产生 token 对比问题
        return None

    # 计算当前文件在基线目录下的对应路径
    path_before = baseline_root / str_relative_path  # 基线目录中的同名 HLS 文件

    # 执行 token 与 AST 等价性检查
    list_file_issues = _comment_only_file_issues(  # 提取当前文件相对基线的所有 guard 问题
        path_root, baseline_root, path_before, path_file,  # 当前根目录、基线根目录与前后文件位置
        str_relative_path, obj_provider, path_fake_include,  # 报告相对路径、AST provider 与伪头目录
    )

    # 合并当前文件发现的问题
    list_issues.extend(list_file_issues)

    # 当前文件防护结果已合并
    return None

# 在无基线模式下登记改写后文件的 AST 可解析性细节
def _record_after_parse_status(
    path_root: Path,
    path_file: Path,
    str_relative_path: str,
    obj_provider: Any,
    path_fake_include: Path,
    dict_metrics: dict[str, Any],
) -> None:
    """
    在没有基线目录时记录改写后 HLS 文件的解析可用性。

    :param path_root: HLS 门禁扫描根目录。
    :param path_file: 当前需要解析的 HLS 源文件。
    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :param obj_provider: 当前机器可用的 AST provider，缺失时为 None。
    :param path_fake_include: AST provider 解析所需的 fake HLS include 目录。
    :param dict_metrics: 注释-only 防护指标字典。
    :return: 本函数只更新指标，无业务返回值。
    """

    # provider 不可用时记录不可用状态并跳过解析
    if obj_provider is None:

        # 标记 AST provider 不可用
        dict_metrics["ast_provider_unavailable"] = True  # 改写后解析缺少 AST provider

        # 无 provider 时无法继续做改写后解析
        return None

    # 解析改写后的当前文件
    ast_parse_result_after: AstParseResult = obj_provider.parse(path_root, path_file, path_fake_include)  # 改写后 AST 解析结果

    # 解析失败时记录 provider 不可用与失败明细
    if not ast_parse_result_after.ok:

        # 标记 AST provider 未能完成本轮解析
        dict_metrics["ast_provider_unavailable"] = True  # 改写后文件解析失败状态

        # 记录解析失败明细
        list_parse_failures = dict_metrics.setdefault("parse_failures", [])  # AST 解析失败列表

        # 保存当前文件解析失败信息
        dict_parse_failure: dict[str, str] = {}  # 当前 HLS 文件的解析失败明细

        # 记录解析失败的 HLS 相对路径
        dict_parse_failure["path"] = str_relative_path  # 解析失败的 HLS 相对路径

        # 记录报告失败的 provider 名称
        dict_parse_failure["provider"] = ast_parse_result_after.provider  # 报告失败的 provider 名称

        # 记录 provider 返回的失败明细
        dict_parse_failure["detail"] = str(ast_parse_result_after.detail or "")  # provider 返回的失败明细

        # 把失败样本写入 metrics.parse_failures，便于定位 provider 故障
        list_parse_failures.append(dict_parse_failure)

    # 改写后解析状态已写入指标
    return None

# 检查单个文件注释-only 改写的等价性
def _comment_only_file_issues(
    path_root: Path, baseline_root: Path,
    path_before: Path, path_after: Path,
    str_relative_path: str, obj_provider: Any, path_fake_include: Path,
) -> list[HlsGateIssue]:
    """
    比较单个 HLS 文件改写前后的 token 指纹和 AST 指纹。

    :param path_root: HLS 门禁扫描根目录。
    :param baseline_root: 注释-only 对比使用的基线目录。
    :param path_before: 基线目录中的 HLS 文件路径。
    :param path_after: 改写后的 HLS 文件路径。
    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :param obj_provider: 当前机器可用的 AST provider，缺失时为 None。
    :param path_fake_include: AST provider 解析所需的 fake HLS include 目录。
    :return: 当前文件的注释-only 等价性问题列表。
    """

    # 缺失基线文件时无法比较 token 指纹
    if not path_before.exists():

        # 返回基线文件缺失问题
        return _missing_baseline_issues(str_relative_path)

    # 读取基线文件文本
    str_before_text = path_before.read_text(encoding="utf-8", errors="ignore")  # 基线 HLS 文本

    # 读取改写后文件文本
    str_after_text = path_after.read_text(encoding="utf-8", errors="ignore")  # 改写后 HLS 文本

    # 非注释 token 指纹变化说明改写越界
    if code_token_fingerprint(str_before_text) != code_token_fingerprint(str_after_text):

        # 返回 token 指纹变化问题
        return _token_changed_issues(str_relative_path)

    # 缺少 AST provider 时无法证明结构等价
    if obj_provider is None:

        # 返回 provider 缺失问题
        return _missing_provider_issues(str_relative_path)

    # 解析改写后的 HLS 文件
    ast_parse_result_after: AstParseResult = obj_provider.parse(  # 针对改写后文件提取 AST 指纹
        path_root,  # 让 provider 以当前扫描根解析 include 相对关系
        path_after,  # 对改写后的 HLS 文件建立 AST 指纹
        path_fake_include,  # 提供 HLS 相关伪头文件以降低解析噪声
    )

    # 改写后文件解析失败时阻止声明结构等价
    if not ast_parse_result_after.ok:

        # 返回改写后解析失败问题
        return _after_parse_failed_issues(str_relative_path, ast_parse_result_after)

    # 解析基线目录中的 HLS 文件
    ast_parse_result_before: AstParseResult = obj_provider.parse(  # 针对基线文件提取对照 AST 指纹
        baseline_root,  # 让 provider 以基线根目录复原原始 include 语义
        path_before,  # 对基线 HLS 文件建立对照 AST 指纹
        path_fake_include,  # 复用同一套伪头文件保证比较口径一致
    )

    # 基线文件解析失败时无法比较结构等价性
    if not ast_parse_result_before.ok:

        # 返回基线解析失败问题
        return _before_parse_failed_issues(str_relative_path, ast_parse_result_before)

    # 返回 AST 指纹对比结果，空列表表示注释-only 改写保持结构等价。
    return _ast_fingerprint_issues(str_relative_path, ast_parse_result_before, ast_parse_result_after)

# 构造基线文件缺失问题列表。
def _missing_baseline_issues(str_relative_path: str) -> list[HlsGateIssue]:
    """
    返回基线文件缺失时的注释-only 防护问题。

    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :return: 只包含 HG013 基线缺失问题的列表。
    """

    # 基线缺失时无法证明改写前后 token 等价。
    hls_gate_issue_item = _comment_guard_issue(  # 构造基线缺失时的 comment-only guard 问题
        str_relative_path,  # 标记当前无法完成 comment-only 对比的文件
        "Baseline file for comment-only comparison is missing.",  # 对外保持稳定的缺失基线正文
        "comment_only_baseline",  # 将失败原因归类为缺基线路径问题
    )  # 基线文件缺失诊断

    # 以单元素列表返回基线缺失问题，供 guard 聚合。
    return [hls_gate_issue_item]

# 构造 token 指纹变化问题列表。
def _token_changed_issues(str_relative_path: str) -> list[HlsGateIssue]:
    """
    返回非注释 token 发生变化时的防护问题。

    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :return: 只包含 HG012 token guard 问题的列表。
    """

    # 非注释 token 变化说明注释-only 改写越界。
    hls_gate_issue_item = _comment_guard_issue(  # 构造 token 越界时的 comment-only guard 问题
        str_relative_path,  # 标记 token 指纹发生漂移的文件
        "Non-comment HLS tokens changed during a comment-only rewrite.",  # 对外保持稳定的 token 越界正文
        "comment_only_token_guard",  # 将失败原因归类为 token 防护失效
        rule="HG012",  # token 越界属于 HG012 结构漂移规则
    )  # 非注释 token 指纹变化诊断

    # 把 token 越界问题封装成单元素列表，交回 guard 汇总流程。
    return [hls_gate_issue_item]

# 构造 AST provider 缺失问题列表。
def _missing_provider_issues(str_relative_path: str) -> list[HlsGateIssue]:
    """
    返回缺少 AST provider 时的结构证明问题。

    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :return: 只包含 HG013 provider 缺失问题的列表。
    """

    # token 未变但 provider 缺失时，仍无法声明 AST 结构等价。
    str_message = (
        "Comment-only tokens are unchanged, but no AST provider is available "
        "to prove structural equivalence."
    )  # 缺少 AST provider 的结构证明诊断正文

    # 构造 provider 缺失问题。
    hls_gate_issue_item = _comment_guard_issue(str_relative_path, str_message, "ast_provider")  # AST provider 缺失诊断

    # 用单元素列表回传 provider 缺失结果，阻止误判结构等价。
    return [hls_gate_issue_item]

# 构造改写后文件解析失败问题列表。
def _after_parse_failed_issues(
    str_relative_path: str,
    ast_parse_result: AstParseResult,
) -> list[HlsGateIssue]:
    """
    返回改写后 HLS 文件解析失败时的防护问题。

    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :param ast_parse_result: provider 返回的改写后解析结果。
    :return: 只包含 HG013 parse 失败问题的列表。
    """

    # 改写后文件无法解析时，不能声明注释-only 改写结构等价。
    str_message = (
        "Comment-only tokens are unchanged, but the available AST provider "
        "could not parse the commented HLS file."
    )  # 改写后 HLS 解析失败诊断正文

    # 把改写后文件解析失败封装成可序列化 guard issue。
    hls_gate_issue_item = _comment_guard_parse_issue(str_relative_path, str_message, ast_parse_result)  # 改写后解析失败诊断

    # 将改写后解析失败作为单元素列表回传给 guard 汇总。
    return [hls_gate_issue_item]

# 构造基线文件解析失败问题列表。
def _before_parse_failed_issues(
    str_relative_path: str,
    ast_parse_result: AstParseResult,
) -> list[HlsGateIssue]:
    """
    返回基线 HLS 文件解析失败时的防护问题。

    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :param ast_parse_result: provider 返回的基线解析结果。
    :return: 只包含 HG013 parse 失败问题的列表。
    """

    # 基线文件无法解析时，AST 指纹没有可信比较对象。
    str_message = (
        "Comment-only tokens are unchanged, but the available AST provider "
        "could not parse the baseline HLS file."
    )  # 基线 HLS 解析失败诊断正文

    # 记录基线文件自身无法建立 AST 对照的失败原因。
    hls_gate_issue_item = _comment_guard_parse_issue(str_relative_path, str_message, ast_parse_result)  # 基线解析失败诊断

    # 将基线解析失败作为单元素列表回传给等价性检查流程。
    return [hls_gate_issue_item]

# 对比注释-only 改写前后的 AST 指纹。
def _ast_fingerprint_issues(
    str_relative_path: str,
    ast_parse_result_before: AstParseResult,
    ast_parse_result_after: AstParseResult,
) -> list[HlsGateIssue]:
    """
    返回 AST 指纹差异导致的注释-only 防护问题。

    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :param ast_parse_result_before: 基线文件 AST 解析结果。
    :param ast_parse_result_after: 改写后文件 AST 解析结果。
    :return: 指纹一致时为空列表，不一致时包含 HG012 问题。
    """

    # 指纹一致说明注释-only 改写没有改变 AST 结构。
    if ast_parse_result_before.fingerprint == ast_parse_result_after.fingerprint:

        # 空列表表示当前文件通过结构等价检查。
        return []

    # AST 指纹不同说明注释-only 改写改变了解析结构。
    hls_gate_issue_item = _comment_guard_issue(  # 构造 AST 指纹漂移时的 comment-only guard 问题
        str_relative_path,  # 标记 AST 指纹与基线不一致的文件
        "AST changed after comment-only rewrite; fingerprints differ.",  # 对外保持稳定的 AST 漂移正文
        "comment_only_ast_guard",  # 在报告里标记为 AST guard 破坏，便于区别 token 越界
        rule="HG012",  # AST 漂移同样归入 HG012 结构变化规则
        detail="normalized AST fingerprints differ",  # 把差异原因写入 detail 便于报告追踪
    )  # AST 指纹差异诊断

    # 把 AST 指纹漂移问题作为单元素列表返回给调用方。
    return [hls_gate_issue_item]

# 构造注释-only 防护中的通用问题。
def _comment_guard_issue(
    str_relative_path: str,
    message: str,
    node_kind: str,
    *,
    rule: str = "HG013",
    detail: str | None = None,
) -> HlsGateIssue:
    """
    创建注释-only token 或 AST 防护问题。

    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :param message: 需要保持稳定的诊断正文。
    :param node_kind: 报告中标识防护失败类型的节点类别。
    :param rule: HLS gate 规则编号，缺省为 HG013。
    :param detail: 可选的 provider 或指纹差异明细。
    :return: 可直接加入报告的问题对象。
    """

    # make_issue 统一维护报告 dataclass 字段和 JSON 契约。
    hls_gate_issue_item: HlsGateIssue = make_issue(  # 统一构造注释-only guard 失败对象
        rule,  # 允许调用方复用 HG012 或 HG013 等具体规则号
        "error",  # comment-only 防护失败统一按 error 记账
        str_relative_path,  # 报告路径固定指向当前 HLS 相对路径
        1,  # 防护问题没有精确源码点时锚定首行
        message,  # 对外暴露的稳定诊断正文
        detail=detail,  # 可选 detail 用来携带 provider 或指纹差异补充信息
        node_kind=node_kind,  # 标记当前 guard 失败的具体类别
    )  # 注释-only 防护问题

    # 返回构造好的防护问题。
    return hls_gate_issue_item

# 构造 AST provider 解析失败问题。
def _comment_guard_parse_issue(
    str_relative_path: str,
    message: str,
    ast_parse_result: AstParseResult,
) -> HlsGateIssue:
    """
    创建带 provider 失败明细的注释-only AST 防护问题。

    :param str_relative_path: 当前 HLS 文件的报告相对路径。
    :param message: 需要保持稳定的诊断正文。
    :param ast_parse_result: provider 返回的解析结果。
    :return: 可直接加入报告的问题对象。
    """

    # provider detail 保留原始失败说明，方便用户定位解析环境或源码问题。
    str_detail = ast_parse_result.detail  # AST provider 解析失败明细

    # 解析失败都属于 AST 防护无法证明结构等价。
    return _comment_guard_issue(
        str_relative_path,
        message,
        "comment_only_ast_guard",
        detail=str_detail,
    )
