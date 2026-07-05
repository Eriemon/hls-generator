"""为生成的 HLS C/C++ 文件提供 AST 与注释-only 保护。"""

# 启用延迟注解，避免运行期解析类型标注。
from __future__ import annotations

# 临时目录用于放置 fake HLS headers，辅助 C/C++ parser 解析源码。
import tempfile

# dataclass 用于表达可转换为 validation issue 的诊断对象。
from dataclasses import dataclass

# Path 用于定位当前产物和 baseline 产物。
from pathlib import Path

# Any 描述指标字典中的异构值。
from typing import Any

# AST provider 提供 clang、tree-sitter-cpp、pycparser 的统一解析接口。
from .readability_gate.cpp_ast_provider import (
    AstParseResult,
    CppAstProvider,
    select_cpp_ast_provider,
    write_fake_hls_headers,
)

# C/C++ 词法工具负责筛选 HLS 后缀和生成去注释 token 指纹。
from .readability_gate.cpp_lexer import (
    HLS_AST_SUFFIXES,
    code_token_fingerprint,
    strip_comments_preserving_strings,
)

# baseline 缺失时无法证明 comment-only 改写。
BASELINE_MISSING_MESSAGE = "HG013: Baseline file for comment-only AST comparison is missing."  # baseline 缺失诊断正文

# token 变化说明改动已经超出注释-only 边界。
TOKEN_CHANGED_MESSAGE = "HG012: Non-comment C/C++ tokens changed; this edit is not a comment-only rewrite."  # 非注释 token 越界诊断正文

# 没有 AST provider 时不能完成结构等价证明。
PROVIDER_MISSING_MESSAGE = (  # AST provider 缺失诊断文本
    "HG013: Comment-only tokens are unchanged, but no AST provider is available "  # 说明 token 未变但缺少 AST provider
    "to prove structural equivalence."  # 说明因此无法确认结构等价
)

# 注释后文件无法解析时需要阻断 comment-only 结论。
AFTER_PARSE_FAILED_MESSAGE = (  # 注释后文件解析失败诊断文本
    "HG013: Comment-only tokens are unchanged, but the available AST provider "  # 说明 token 未变但改写后文件仍解析失败
    "could not parse the commented HLS file."  # 指向当前产物本身的 AST 解析失败
)

# baseline 无法解析时同样不能完成等价证明。
BEFORE_PARSE_FAILED_MESSAGE = (  # baseline 文件解析失败诊断文本
    "HG013: Comment-only tokens are unchanged, but the available AST provider "  # 说明 token 未变但基线文件仍解析失败
    "could not parse the baseline HLS file."  # 指向对照基线文件的 AST 解析失败
)

# AST 指纹不同说明结构发生变化。
AST_CHANGED_MESSAGE = (  # AST 指纹变化诊断文本
    "HG012: AST changed after a comment-only rewrite; do not accept this edit as "  # 说明 AST 指纹比较已经发现结构漂移
    "comment-only."  # 阻止把本次改写误判为纯注释修改
)

@dataclass(frozen=True)
class HlsAstIssue:
    """记录一条可转换为 validation issue 的 AST guard 诊断。"""

    # severity 对应 validation issue 的错误、警告或提示等级。
    severity: str  # validation issue 严重程度等级

    # message 保存面向用户的诊断文本。
    message: str  # AST guard 诊断文本

    # path 指向触发诊断的相对文件路径。
    path: str | None = None  # 触发诊断的相对路径

    # tool 记录触发诊断或执行解析的工具名称。
    tool: str | None = None  # AST 或 token guard 工具名称

    # detail 保存 parser 返回的补充细节。
    detail: str | None = None  # AST 解析补充细节

    # to_dict 保持与 validation 层 issue 字典字段一致。
    def to_dict(self) -> dict[str, Any]:
        """把诊断转换为 validation 层使用的字典。

        参数:
            无。

        返回:
            包含 severity、message、path、tool 和 detail 的诊断字典。
        """

        # 按 validation 层约定的字段顺序导出当前诊断。
        return {
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "tool": self.tool,
            "detail": self.detail,
        }

@dataclass(frozen=True)
class AstGuardContext:
    """保存单轮 AST guard 对比所需的共享上下文。"""

    # root 是当前 HLS 产物根目录。
    root: Path  # 当前 HLS 产物根目录

    # baseline_root 是 comment-only 对比的基线根目录。
    baseline_root: Path  # baseline HLS 产物根目录

    # ast_provider 是执行 comment-only AST 等价证明的 C/C++ 解析器。
    ast_provider: CppAstProvider | None  # comment-only AST 等价证明使用的 C/C++ 解析器

    # path_fake_include 指向 parser 解析 HLS 专用头文件时使用的 fake header 目录。
    path_fake_include: Path  # parser 使用的 fake HLS include 目录

    # dict_metrics 记录当前 AST guard 的指标输出。
    dict_metrics: dict[str, Any]  # AST guard 指标字典

    # list_issues 累计当前 AST guard 的诊断输出。
    list_issues: list[HlsAstIssue]  # AST guard 诊断列表

# validate_hls_ast_guard 是 AST guard 的公开入口。
def validate_hls_ast_guard(
    root: Path,
    hls_files: list[Path],
    *,
    baseline_root: Path | None = None,
) -> tuple[list[HlsAstIssue], dict[str, Any]]:
    """解析 HLS 文件，并可选证明注释-only 改写不改变 token 与 AST。

    参数:
        root: 当前 HLS 产物根目录。
        hls_files: 需要检查的候选文件列表。
        baseline_root: 可选 baseline 根目录；存在时启用 comment-only 对比。

    返回:
        AST guard 诊断列表和指标字典。
    """

    # 只检查 C/C++ HLS 源文件，其它产物不需要 AST guard。
    list_checked_files = _hls_ast_files(hls_files)  # 需要执行 AST guard 的 HLS 文件

    # 这个容器后面会逐步填入 provider 名称、baseline 模式和最终 issue JSON。
    dict_metrics = _initial_metrics(baseline_root)  # validate_hls_ast_guard 返回给上层的指标载荷

    # 这里累计的是最终要交给 validation 层的 HlsAstIssue 结果对象。
    list_issues: list[HlsAstIssue] = []  # validate_hls_ast_guard 最终要返回的 AST 诊断集合

    # 没有可检查文件时直接返回空诊断和默认指标。
    if not list_checked_files:

        # 记录当前分支为什么完全跳过 provider 解析。
        dict_metrics["provider"] = "not_required_no_hls_source"  # AST provider 跳过原因

        # 把无需 provider 的空检查结果返回给调用方。
        return list_issues, dict_metrics

    # provider 按 clang、tree-sitter-cpp、pycparser 的优先级自动选择。
    ast_provider_reference = select_cpp_ast_provider()  # 当前可用的 C/C++ AST provider 引用

    # 指标记录实际选中的 provider 名称；缺失时保留 None。
    dict_metrics["provider"] = ast_provider_reference.name if ast_provider_reference else None  # 当前 AST provider 名称

    # parse-after-only 模式缺少 provider 时只记录不可用状态。
    if ast_provider_reference is None and baseline_root is None:

        # 标记 parse-after-only 模式在无 provider 时无法做结构校验。
        dict_metrics["ast_provider_unavailable"] = True  # AST provider 不可用标志

        # 把 provider 缺失但未进入 baseline 比较的结果返回给调用方。
        return list_issues, dict_metrics

    # 在临时目录中准备 fake include，供 parser 解析 HLS 头依赖。
    with tempfile.TemporaryDirectory(prefix="hls_ast_guard_") as str_temp_dir:

        # 在临时目录内生成 parser 所需的 fake include 头目录。
        path_fake_include = write_fake_hls_headers(Path(str_temp_dir) / "fake_hls_include")  # parser 解析 HLS 文件时使用的 fake include 目录

        # 无 baseline 时只验证当前产物能否被 provider 解析。
        if baseline_root is None:

            # 进入 parse-after-only 流程，逐个验证当前文件的可解析性。
            _parse_after_files(
                root,
                list_checked_files,
                ast_provider_reference,
                path_fake_include,
                dict_metrics,
            )
        
        # 有 baseline 时转入 comment-only 对比模式。
        else:

            # 把 baseline 模式公用的路径、provider 和输出容器封装成上下文对象。
            ast_guard_context_ast_guard_context: AstGuardContext = AstGuardContext(  # baseline 对比共享上下文
                root, baseline_root,  # 锁定当前产物树与它对应的 baseline 根目录
                ast_provider_reference, path_fake_include,  # 共享已选 parser 以及它依赖的 fake include 目录
                dict_metrics, list_issues,  # 共享指标写入口和最终问题累计列表
            )

            # baseline 模式把共享上下文和文件清单交给逐文件比较流程。
            _compare_with_baseline(ast_guard_context_ast_guard_context, list_checked_files)

    # 指标中的 issues 字段保持和 validation 报告结构一致。
    dict_metrics["issues"] = [issue.to_dict() for issue in list_issues]  # validation 可消费的 AST 诊断字典列表

    # 返回 AST guard 汇总出的诊断和指标。
    return list_issues, dict_metrics

# _hls_ast_files 筛选需要 AST 检查的 HLS 源文件。
def _hls_ast_files(hls_files: list[Path]) -> list[Path]:
    """筛选 AST provider 支持的 HLS 源文件。

    参数:
        hls_files: 候选文件列表。

    返回:
        后缀属于 HLS_AST_SUFFIXES 的文件列表。
    """

    # 只返回 AST provider 支持后缀的 HLS 源文件清单。
    return [path_file for path_file in hls_files if path_file.suffix.lower() in HLS_AST_SUFFIXES]

# _initial_metrics 创建 AST guard 指标字典。
def _initial_metrics(baseline_root: Path | None) -> dict[str, Any]:
    """创建 AST guard 指标容器。

    参数:
        baseline_root: 可选 baseline 根目录。

    返回:
        包含默认字段的指标字典。
    """

    # 交付带默认字段的 AST guard 指标容器。
    return {
        "policy": "hls_ast_comment_guard",
        "provider": None,
        "baseline_root": str(baseline_root) if baseline_root else None,
        "checked_files": [],
        "mode": "comment_only_compare" if baseline_root else "parse_after_only",
        "issues": [],
    }

# _parse_after_files 在无 baseline 模式下只验证当前文件可解析。
def _parse_after_files(
    root: Path,
    list_checked_files: list[Path],
    ast_provider: CppAstProvider | None,
    path_fake_include: Path,
    dict_metrics: dict[str, Any],
) -> None:
    """解析当前 HLS 文件并记录 parse failure。

    参数:
        root: 当前 HLS 产物根目录。
        list_checked_files: 需要解析的 HLS 文件列表。
        ast_provider: 当前可用的 AST provider。
        path_fake_include: fake HLS include 目录。
        dict_metrics: AST guard 指标字典。

    返回:
        无显式返回值；解析结果直接写入传入的指标字典。
    """

    # 逐个验证 parse-after-only 模式下的 HLS 文件是否能被 provider 解析。
    for path_file in list_checked_files:

        # parse-after-only 阶段把当前文件折叠成产物根目录下的 POSIX 相对路径。
        str_rel_path = path_file.relative_to(root).as_posix()  # 当前文件相对产物根目录路径

        # 先把当前文件登记进 checked_files 指标。
        dict_metrics["checked_files"].append(str_rel_path)

        # 缺少 provider 时只记录不可用状态并跳过当前文件。
        if ast_provider is None:

            # 当前宿主机连一个可落地的 C/C++ parser 都没选出来，只能把 parse-after-only 状态标成 unavailable。
            dict_metrics["ast_provider_unavailable"] = True  # parse-after-only 分支检测到当前机器没有可用 parser

            # 当前文件没有可用 provider 时结束本轮循环。
            continue

        # 对当前产物文件发起 AST 解析，确认 parse-after-only 模式下仍可被 provider 接受。
        ast_parse_result_after: AstParseResult = ast_provider.parse(  # 对当前产物文件执行 AST 解析
            root,  # 以当前产物根目录解析 include 相对关系
            path_file,  # 当前需要验证可解析性的 HLS 文件
            path_fake_include,  # parser 所需的 fake include 目录
        )

        # 当前文件解析失败时要记入 parse_failures，并标记 provider 对该输入不可用。
        if not ast_parse_result_after.ok:

            # provider 虽然存在，但这份 HLS 文件把它解析打崩了，所以要把本轮样本记成不可用。
            dict_metrics["ast_provider_unavailable"] = True  # parse-after-only 分支检测到所选 parser 无法解析当前文件

            # 记录当前失败样本，供报告定位 provider 或源码问题。
            _append_parse_failure(dict_metrics, str_rel_path, ast_parse_result_after)

# _compare_with_baseline 执行 comment-only token 与 AST 对比。
def _compare_with_baseline(ast_guard_context: AstGuardContext, list_checked_files: list[Path]) -> None:
    """对比当前 HLS 文件和 baseline 文件。

    参数:
        ast_guard_context: 当前 AST guard 对比上下文。
        list_checked_files: 需要对比的 HLS 文件列表。

    返回:
        无显式返回值；比较结果直接追加到上下文中的指标和问题列表。
    """

    # 逐个进入 baseline 对比流程，确保每个目标文件都登记到 metrics。
    for path_file in list_checked_files:

        # baseline 分支继续用产物树里的相对路径做 metrics key，方便和改写后文件一一对齐。
        str_rel_path = path_file.relative_to(ast_guard_context.root).as_posix()  # baseline 对比阶段使用的当前文件相对路径键

        # 把当前文件登记到 comment-only 对比阶段的 checked_files。
        ast_guard_context.dict_metrics["checked_files"].append(str_rel_path)

        # 把当前文件交给单文件 token/AST 等价性证明流程。
        _compare_one_file(
            ast_guard_context,
            path_file,
            str_rel_path,
        )

# _compare_one_file 对单个 HLS 文件执行 comment-only 证明。
def _compare_one_file(
    ast_guard_context: AstGuardContext,
    path_file: Path,
    str_rel_path: str,
) -> None:
    """对单个文件执行 token 和 AST 等价证明。

    参数:
        ast_guard_context: 当前 AST guard 对比上下文。
        path_file: 当前 HLS 文件路径。
        str_rel_path: 当前文件相对产物根目录路径。

    返回:
        无显式返回值；单文件比较结果直接写入上下文中的指标和问题列表。
    """

    # baseline 路径和当前文件相对路径保持一致。
    path_before_file = ast_guard_context.baseline_root / str_rel_path  # baseline 中对应的 HLS 文件路径

    # baseline 文件缺失时先登记 HG013，再结束当前文件比较。
    if not path_before_file.exists():

        # 把 baseline 缺失诊断追加到总问题列表。
        ast_guard_context.list_issues.append(
            _baseline_missing_issue(str_rel_path, ast_guard_context.ast_provider),
        )

        # 缺少 baseline 时无法继续 token/AST 对比，直接结束当前文件。
        return

    # token guard 一旦失败，就不再继续 AST 指纹比较。
    if _append_token_issue_if_needed(ast_guard_context, path_before_file, path_file, str_rel_path):

        # token 已经改变时无需继续做 AST 等价证明。
        return

    # token 未变但 provider 缺失时仍要登记结构证明失败。
    if ast_guard_context.ast_provider is None:

        # 追加 provider 缺失诊断，明确无法完成 AST 等价证明。
        ast_guard_context.list_issues.append(
            HlsAstIssue(
                "error",
                PROVIDER_MISSING_MESSAGE,
                str_rel_path,
                "clang++|tree-sitter-cpp|pycparser",
            ),
        )

        # 这个分支不是代码越界，而是缺少可执行的 AST 证明工具，所以要把结果记成 unavailable。
        ast_guard_context.dict_metrics["ast_provider_unavailable"] = True  # comment-only 分支缺少 AST 证明工具

        # 缺少 provider 时结束当前文件比较流程。
        return

    # 进入 AST 指纹比较阶段，确认结构是否保持不变。
    _compare_ast_fingerprints(
        ast_guard_context,
        path_file,
        path_before_file,
        str_rel_path,
    )

# _compare_ast_fingerprints 比较 baseline 与当前文件的 AST 指纹。
def _compare_ast_fingerprints(
    ast_guard_context: AstGuardContext,
    path_after_file: Path,
    path_before_file: Path,
    str_rel_path: str,
) -> None:
    """解析并比较单个文件的 AST 指纹。

    参数:
        ast_guard_context: 当前 AST guard 对比上下文。
        path_after_file: 当前 HLS 文件路径。
        path_before_file: baseline HLS 文件路径。
        str_rel_path: 当前文件相对产物根目录路径。

    返回:
        无显式返回值；AST 指纹比较结果直接追加到上下文中的问题列表。
    """

    # 此函数只在 provider 存在后调用，这里保留防御式检查。
    ast_provider_reference = ast_guard_context.ast_provider  # AST 指纹比较使用的 C/C++ 解析器引用

    # 防御式分支兜住异常调用路径，避免空 provider 继续进入解析流程。
    if ast_provider_reference is None:

        # 没有 provider 时当前 helper 不产出任何附加诊断。
        return

    # 先为改写后文件提取 AST 指纹，确认当前产物具备可比较的解析结果。
    ast_parse_result_after: AstParseResult = ast_provider_reference.parse(  # 先为改写后文件提取 AST 指纹
        ast_guard_context.root,  # 以当前产物根目录恢复 include 相对关系
        path_after_file,  # 当前需要比较的改写后 HLS 文件
        ast_guard_context.path_fake_include,  # 解析 HLS 头依赖所需的 fake include 目录
    )

    # 当前文件无法解析时要记录失败诊断，并标记 provider 对该输入不可用。
    if not ast_parse_result_after.ok:

        # 把改写后文件解析失败诊断追加到总问题列表。
        ast_guard_context.list_issues.append(
            HlsAstIssue(
                "error",
                AFTER_PARSE_FAILED_MESSAGE,
                str_rel_path,
                ast_parse_result_after.provider,
                ast_parse_result_after.detail,
            ),
        )

        # 这里记录的是“provider 存在但改写后样本解析崩掉”的状态，方便区分环境缺失与源码失败。
        ast_guard_context.dict_metrics["ast_provider_unavailable"] = True  # comment-only 分支检测到改写后样本解析失败

        # 当前文件无法解析时无需继续对 baseline 做指纹比较。
        return

    # 再为 baseline 文件提取对照 AST 指纹，保证比较双方使用同一解析环境。
    ast_parse_result_before: AstParseResult = ast_provider_reference.parse(  # 再为 baseline 文件提取对照 AST 指纹
        ast_guard_context.baseline_root,  # 以 baseline 根目录恢复原始 include 相对关系
        path_before_file,  # 对照用的 baseline HLS 文件
        ast_guard_context.path_fake_include,  # 复用相同 fake include 目录保持比较口径一致
    )

    # baseline 文件无法解析时也必须中止结构等价结论。
    if not ast_parse_result_before.ok:

        # 把 baseline 文件解析失败诊断追加到总问题列表。
        ast_guard_context.list_issues.append(
            HlsAstIssue(
                "error",
                BEFORE_PARSE_FAILED_MESSAGE,
                str_rel_path,
                ast_parse_result_before.provider,
                ast_parse_result_before.detail,
            ),
        )

        # baseline 无法建立对照 AST 时结束当前文件比较。
        return

    # AST 指纹不同意味着这次改写已经越过 comment-only 边界。
    if ast_parse_result_before.fingerprint != ast_parse_result_after.fingerprint:

        # 把 AST 指纹漂移诊断追加到总问题列表。
        ast_guard_context.list_issues.append(
            HlsAstIssue(
                "error",
                AST_CHANGED_MESSAGE,
                str_rel_path,
                ast_provider_reference.name,
                "normalized AST fingerprints differ",
            ),
        )

# _append_parse_failure 把解析失败记录进指标字典。
def _append_parse_failure(dict_metrics: dict[str, Any], str_rel_path: str, parse_report: AstParseResult) -> None:
    """记录单个 AST parse failure。

    参数:
        dict_metrics: AST guard 指标字典。
        str_rel_path: 当前文件相对产物根目录路径。
        parse_report: AST provider 返回的解析结果。

    返回:
        无显式返回值；失败样本直接登记到 parse_failures 指标。
    """

    # 取出 metrics 中承载解析失败样本的 parse_failures 列表。
    list_parse_failures = dict_metrics.setdefault("parse_failures", [])  # parse_failures 指标列表

    # 把当前 AST 解析失败样本登记到 metrics.parse_failures。
    list_parse_failures.append(
        {
            "path": str_rel_path,
            "provider": parse_report.provider,
            "detail": parse_report.detail,
        },
    )

# _baseline_missing_issue 构造 baseline 缺失诊断。
def _baseline_missing_issue(str_rel_path: str, ast_provider: CppAstProvider | None) -> HlsAstIssue:
    """构造 baseline 文件缺失诊断。

    参数:
        str_rel_path: 当前文件相对产物根目录路径。
        ast_provider: 当前可用的 AST provider。

    返回:
        baseline 缺失诊断对象。
    """

    # 返回可直接进入总问题列表的 baseline 缺失诊断。
    return HlsAstIssue(
        "error",
        BASELINE_MISSING_MESSAGE,
        str_rel_path,
        ast_provider.name if ast_provider else None,
    )

# _append_token_issue_if_needed 执行 token guard 并追加诊断。
def _append_token_issue_if_needed(
    ast_guard_context: AstGuardContext,
    path_before_file: Path,
    path_after_file: Path,
    str_rel_path: str,
) -> bool:
    """检查 token 指纹并在失败时追加诊断。

    参数:
        ast_guard_context: 当前 AST guard 对比上下文。
        path_before_file: baseline HLS 文件路径。
        path_after_file: 当前 HLS 文件路径。
        str_rel_path: 当前文件相对产物根目录路径。

    返回:
        已追加 token 变化诊断时返回 True。
    """

    # 把 token guard 结果交给统一的可选诊断追加入口。
    return _append_optional_issue(
        ast_guard_context,
        _comment_only_token_issue(
            path_before_file,
            path_after_file,
            str_rel_path,
        ),
    )

# _append_optional_issue 追加可选诊断并返回是否追加。
def _append_optional_issue(ast_guard_context: AstGuardContext, hls_ast_issue: HlsAstIssue | None) -> bool:
    """追加非空 AST guard 诊断。

    参数:
        ast_guard_context: 当前 AST guard 对比上下文。
        hls_ast_issue: 可选 AST guard 诊断。

    返回:
        诊断非空且已经追加时返回 True。
    """

    # 缺少可追加诊断时直接告诉调用方当前没有命中问题。
    if hls_ast_issue is None:

        # 空诊断输入不会改变上下文状态。
        return False

    # 非空诊断需要立刻并入当前 AST guard 的问题列表。
    ast_guard_context.list_issues.append(hls_ast_issue)

    # 返回 True，通知调用方已经成功追加诊断。
    return True

# _comment_only_token_issue 判断非注释 token 是否发生变化。
def _comment_only_token_issue(path_before_file: Path, path_after_file: Path, str_rel_path: str) -> HlsAstIssue | None:
    """检查 comment-only 改写是否保持非注释 token 不变。

    参数:
        path_before_file: baseline HLS 文件路径。
        path_after_file: 当前 HLS 文件路径。
        str_rel_path: 当前文件相对产物根目录路径。

    返回:
        token 变化诊断；token 未变时返回 None。
    """

    # 先计算 baseline 文件的非注释 token 指纹。
    str_before_fingerprint = code_token_fingerprint(path_before_file.read_text(encoding="utf-8", errors="ignore"))  # baseline 非注释 token 指纹

    # 再计算当前文件的非注释 token 指纹，供 comment-only 边界比较。
    str_after_fingerprint = code_token_fingerprint(path_after_file.read_text(encoding="utf-8", errors="ignore"))  # 当前文件非注释 token 指纹

    # token 指纹完全一致时说明本轮改写没有触碰非注释代码。
    if str_before_fingerprint == str_after_fingerprint:

        # 保持 None，表示无需为当前文件追加 token guard 问题。
        return None

    # 构造 token 越界诊断，阻止把当前改写认定为 comment-only。
    return HlsAstIssue("error", TOKEN_CHANGED_MESSAGE, str_rel_path, "comment_stripped_token_guard")

# _strip_comments_preserving_strings 保留旧调用方使用的兼容入口。
def _strip_comments_preserving_strings(text: str) -> str:
    """剥离 C/C++ 注释，同时保留字符串字面量。

    参数:
        text: C/C++ 源码文本。

    返回:
        去掉注释后的源码文本。
    """

    # 继续复用底层词法 helper 的去注释实现。
    return strip_comments_preserving_strings(text)

# _code_token_fingerprint 继续兼容旧调用方的 token 指纹入口。
def _code_token_fingerprint(text: str) -> str:
    """计算去注释后的 C/C++ token 指纹。

    参数:
        text: C/C++ 源码文本。

    返回:
        非注释 token 指纹。
    """

    # 兼容旧接口时直接复用词法层的 token 指纹实现。
    return code_token_fingerprint(text)
