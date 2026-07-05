"""为 HLS readability gate 提供可复用的 C/C++ AST provider 抽象。"""

# 延迟注解解析避免可选 provider 类型在导入期求值。
from __future__ import annotations

# 标准库导入支撑 provider 探测、临时 include、进程调用和稳定指纹。
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess

# 临时目录只在 provider 单次解析或批量指标收集期间存在。
import tempfile
from dataclasses import dataclass

# 路径和可调用类型用于统一 provider 的公共签名。
from pathlib import Path
from typing import Any, Callable

# cpp_lexer 作为所有 provider 不可用或解析结果缺函数时的轻量回退。
from .cpp_lexer import HLS_AST_SUFFIXES, FunctionInfo, parse_functions

# clang 候选命令按常见 C++ 前端名称排序，保持原有 provider 优先级。
CLANG_CANDIDATES: tuple[str, ...] = ("clang++", "clang")  # clang 命令查找顺序

# AST provider 优先级同时暴露给诊断 JSON，调用方依赖该字段说明解析来源。
PROVIDER_PRIORITY: list[str] = ["clang", "tree-sitter-cpp", "pycparser"]  # 报告展示的 provider 优先级
# AstParseResult 是各 provider 统一返回给 readability gate 的解析结果。
@dataclass(frozen=True)
class AstParseResult:
    """保存单个 HLS 源文件的归一化解析结果。

    Args:
        ok: 当前 provider 是否成功生成可用 AST 指纹。
        provider: 产生结果或失败诊断的 provider 名称。
        fingerprint: 成功解析时的稳定 AST 指纹，失败时可为空。
        detail: 失败或降级时给调用方展示的诊断摘要。
        functions: cpp_lexer 提取到的函数边界信息。

    Returns:
        数据类实例本身不返回业务值。
    """

    # ok 标记 AST provider 是否成功覆盖当前文件。
    ok: bool  # AST 解析是否成功

    # provider 保留解析来源，供报告解释 clang/tree-sitter/pycparser 路径。
    provider: str  # 生成诊断时展示的解析后端标识

    # fingerprint 用于比较 AST 结构是否稳定，不包含源码位置噪声。
    fingerprint: str | None = None  # 稳定 AST 指纹

    # detail 保存失败原因或降级说明，避免调用方重新拼诊断。
    detail: str | None = None  # 解析诊断摘要

    # functions 保留轻量 lexer 函数信息，供 comment-only gate 继续工作。
    functions: tuple[FunctionInfo, ...] = ()  # 函数边界信息
# CppAstProvider 封装 provider 名称和统一解析入口。
@dataclass(frozen=True)
class CppAstProvider:
    """描述一个可调用的 C/C++ AST 解析 provider。

    Args:
        name: 报告中展示的 provider 名称。
        parse: 接收 root、源文件路径和 fake include 目录的解析函数。

    Returns:
        数据类实例本身不返回业务值。
    """

    # name 会进入报告，不能随意改成不兼容的 provider 标识。
    name: str  # provider 报告名称

    # parse 统一不同 backend 的调用签名，便于 runner 复用。
    parse: Callable[[Path, Path, Path], AstParseResult]  # provider 解析函数

# select_cpp_ast_provider 按 clang、tree-sitter、pycparser 顺序选择 provider。
def select_cpp_ast_provider() -> CppAstProvider | None:
    """选择当前环境中可用的最高优先级 C/C++ AST provider。

    Args:
        无外部业务参数。

    Returns:
        可用 provider；如果当前环境缺少 clang、tree-sitter 和 pycparser，则返回 None。
    """

    # clang 能处理 Vitis HLS C++ 特性，优先作为结构证明 provider。
    str_clang = _find_clang()  # clang 可执行文件路径

    # 命中 clang 时保留可执行文件名，方便报告说明实际前端。
    if str_clang:

        # lambda 捕获当前 clang 路径，保持 CppAstProvider.parse 的统一签名。
        return CppAstProvider(
            f"clang_ast_dump:{Path(str_clang).name}",
            lambda root, path, fake_include: _parse_with_clang(
                str_clang,
                root,
                path,
                fake_include,
            ),
        )

    # tree-sitter 作为无需编译通过的 C++ 语法树备选 provider。
    if _has_tree_sitter_cpp():

        # 返回 tree-sitter provider，调用时再导入具体语言绑定。
        return CppAstProvider("tree_sitter_cpp", _parse_with_tree_sitter)

    # pycparser 只覆盖 C99 风格文件，但仍能给纯 C HLS kernel 提供结构证明。
    if importlib.util.find_spec("pycparser") is not None:

        # 返回 pycparser provider，解析函数内部会拒绝 C++ 后缀。
        return CppAstProvider("pycparser_c99", _parse_with_pycparser)

    # 没有 AST provider 时由 cpp_lexer 负责函数边界回退。
    return None

# provider_status 暴露当前 provider 可用性给 CLI 和报告层。
def provider_status() -> dict[str, Any]:
    """返回 AST provider 探测状态。

    Args:
        无外部业务参数。

    Returns:
        包含 available、provider 和 priority 字段的状态字典。
    """

    # provider 可能为空，调用方通过 available 判断是否需要降级说明。
    ast_provider_state = select_cpp_ast_provider()  # 当前选中的 AST provider

    # 返回字段名保持原有 JSON 契约。
    return {
        "available": ast_provider_state is not None,
        "provider": ast_provider_state.name if ast_provider_state else None,
        "priority": PROVIDER_PRIORITY,
    }

# parse_hls_file 对单个 HLS 文件执行 AST 解析并保留 lexer 函数回退。
def parse_hls_file(
    root: Path,
    path: Path,
    *,
    provider: CppAstProvider | None = None,
) -> AstParseResult:
    """解析单个 HLS C/C++ 文件并返回统一结果。

    Args:
        root: 项目根目录，用于 include 搜索和相对路径诊断。
        path: 需要解析的 HLS C/C++ 源文件路径。
        provider: 调用方显式传入的 provider；为空时自动探测。

    Returns:
        AST 解析结果；provider 不可用或未提取到函数时会携带 cpp_lexer 函数回退信息。
    """

    # selected_provider_state 固定本次解析使用的 provider，避免重复探测导致报告不一致。
    selected_provider_state = provider or select_cpp_ast_provider()  # 本次解析 provider

    # 没有 provider 时仍返回 lexer 函数信息，保证 comment-only gate 可继续工作。
    if selected_provider_state is None:

        # 读取源码行供轻量函数解析器识别函数边界。
        tuple_functions = _parse_functions_from_file(path)  # lexer 函数回退结果

        # 返回不可用诊断，同时保留函数边界信息。
        return AstParseResult(
            False,
            "unavailable",
            detail="No clang/tree-sitter-cpp/pycparser provider available.",
            functions=tuple_functions,
        )

    # fake include 目录只在当前解析生命周期内存在，避免污染仓库。
    with tempfile.TemporaryDirectory(prefix="hls_cpp_ast_provider_") as str_temp_dir:

        # 写入 Vitis HLS 常见头文件的最小桩，帮助 clang/pycparser 解析 kernel。
        path_fake_include = write_fake_hls_headers(  # 当前文件使用的临时 HLS include 根目录
            Path(str_temp_dir) / "fake_hls_include"  # 当前文件的 fake include 目录
        )

        # 调用选定 provider 解析当前文件。
        ast_parse_result_parsed_result: AstParseResult = selected_provider_state.parse(  # 选定 provider 的原始解析结果
            root,  # 项目根目录
            path,  # 待解析的 HLS 源文件
            path_fake_include,  # 当前文件专用的 fake include 目录
        )

        # provider 未携带函数信息时使用 lexer 结果补齐。
        if not ast_parse_result_parsed_result.functions:

            # lexer 回退不改变 provider 成败，只补充函数列表。
            tuple_functions = _parse_functions_from_file(path)  # 补齐后的函数信息

            # 重新封装结果，保持原 provider 的 ok、fingerprint 和 detail。
            return AstParseResult(
                ast_parse_result_parsed_result.ok,
                ast_parse_result_parsed_result.provider,
                ast_parse_result_parsed_result.fingerprint,
                ast_parse_result_parsed_result.detail,
                tuple_functions,
            )  # 补齐函数边界后的解析结果

        # 返回携带 AST 指纹和函数边界的统一结果。
        return ast_parse_result_parsed_result

# ast_metrics_for_files 汇总多个 HLS 文件的 provider 可用性和失败明细。
def ast_metrics_for_files(root: Path, files: list[Path]) -> dict[str, Any]:
    """汇总一组 HLS 文件的 AST provider 解析指标。

    Args:
        root: 项目根目录，用于生成相对路径和 provider 工作目录。
        files: 候选 HLS C/C++ 文件列表。

    Returns:
        包含 provider、checked_files 和 parse_failures 的指标字典。
    """

    # provider 只探测一次，确保本轮指标使用同一解析策略。
    ast_provider_state = select_cpp_ast_provider()  # 本批指标复用的 provider 决策

    # dict_metrics 保持 runner 已消费的 JSON 字段契约。
    dict_metrics: dict[str, Any] = {
        "ast_provider": ast_provider_state.name if ast_provider_state else None,  # 本批指标锁定的 provider 名称
        "ast_provider_unavailable": ast_provider_state is None,  # 本批是否存在 provider 不可用场景
        "provider_priority": PROVIDER_PRIORITY,  # 报告中固定展示的 provider 优先级顺序
        "checked_files": [],  # 本批实际尝试解析的目标列表
        "parse_failures": [],  # provider 失败时登记的诊断条目
    }  # AST provider 指标载荷

    # provider 不可用时不尝试解析文件，只返回可诊断的空指标。
    if ast_provider_state is None:

        # 调用方据此将结构证明标记为不可用。
        return dict_metrics

    # fake include 在整批文件中复用，减少重复写临时头文件。
    with tempfile.TemporaryDirectory(prefix="hls_cpp_ast_provider_") as str_temp_dir:

        # 构造本批 provider 共享的 fake HLS include 目录。
        path_fake_include = write_fake_hls_headers(  # 批量解析共享的临时 HLS include 根目录
            Path(str_temp_dir) / "fake_hls_include"  # 整批文件共用的 fake include 目录
        )

        # 逐个文件收集可解析性指标。
        for path_source in files:

            # 只检查 HLS C/C++ 后缀，忽略其它候选文件。
            if path_source.suffix.lower() not in HLS_AST_SUFFIXES:

                # 非 AST 目标不进入 checked_files。
                continue

            # rel_path 使用 POSIX 形式，保持跨平台报告稳定。
            str_rel_path = path_source.relative_to(root).as_posix()  # 报告相对路径

            # checked_files 记录 provider 实际尝试解析的目标。
            dict_metrics["checked_files"].append(str_rel_path)

            # 执行 provider 解析，失败时只登记诊断而不中断整批检查。
            ast_parse_result_parsed_result: AstParseResult = ast_provider_state.parse(  # 单文件 provider 解析结果
                root,  # 批量解析阶段使用的项目根目录
                path_source,  # 当前批次中的候选源文件
                path_fake_include,  # 批量解析共用的 fake include 目录
            )

            # 解析失败说明本批 AST provider 不能完整覆盖所有 HLS 文件。
            if not ast_parse_result_parsed_result.ok:

                # 标记 provider 覆盖不完整，供上层降级提示。
                dict_metrics["ast_provider_unavailable"] = True  # 本批存在 provider 解析失败

                # 失败明细保留 path、provider 和 detail 三个稳定字段。
                dict_metrics["parse_failures"].append(
                    {
                        "path": str_rel_path,
                        "provider": ast_parse_result_parsed_result.provider,
                        "detail": ast_parse_result_parsed_result.detail,
                    }
                )

    # 返回整批文件的 AST provider 诊断指标。
    return dict_metrics

# _find_clang 在 PATH 中查找 clang++ 或 clang。
def _find_clang() -> str | None:
    """查找可用于 AST dump 的 clang 可执行文件。

    Args:
        无外部业务参数。

    Returns:
        首个可用 clang 命令的完整路径；未找到时返回 None。
    """

    # 候选顺序优先 clang++，再回退 clang。
    for str_candidate in CLANG_CANDIDATES:

        # shutil.which 兼容 Windows 和 POSIX PATH 查找。
        str_executable = shutil.which(str_candidate)  # 候选可执行文件路径

        # 找到可执行文件后立即返回，保持原有优先级。
        if str_executable:

            # 返回 PATH 解析后的实际命令路径。
            return str_executable

    # PATH 中没有可用 clang。
    return None

# _parse_with_clang 使用 clang AST JSON 生成稳定结构指纹。
def _parse_with_clang(clang: str, root: Path, path: Path, fake_include: Path) -> AstParseResult:
    """使用 clang AST dump 解析 HLS C/C++ 文件。

    Args:
        clang: clang 或 clang++ 可执行文件路径。
        root: 项目根目录，作为 clang 工作目录和 include 根。
        path: 待解析的 HLS 源文件。
        fake_include: fake HLS 头文件目录。

    Returns:
        成功时返回 clang AST 稳定指纹；失败时返回诊断摘要。
    """

    # 头文件按 C++ header 模式解析，源文件按 C++ 模式解析。
    str_language = (  # clang 的输入语言模式
        "c++-header"  # 头文件使用 header 解析模式
        if path.suffix.lower() in {".h", ".hpp", ".hh"}  # 头文件后缀命中时切换为 header 模式
        else "c++"  # 非头文件按普通 C++ 源文件解析
    )

    # clang 命令保留原有宏、include 和 warning 抑制设置。
    list_command = [  # clang AST dump 命令参数
        clang,  # 命令首位固定为本轮探测到的 clang
        "-x",  # 显式声明下一项给出的输入语言
        str_language,  # 与 -x 配对的 clang 输入语言值
        "-std=c++17",  # 统一按仓库当前 C++ 方言解析
        "-fsyntax-only",  # 只做语法分析而不生成目标文件
        "-ferror-limit=0",  # 不截断错误数量，保留完整诊断
        "-Wno-unknown-pragmas",  # 忽略未知 pragma 造成的额外噪声

        # 关闭非结构性告警，避免注释规则被 HLS pragma 噪声干扰。
        "-Wno-ignored-attributes",  # 放过 HLS 属性扩展的兼容性告警
        "-Wno-unused-value",  # 放过 pragma 展开带来的无用值告警
        "-D__SYNTHESIS__=1",  # 打开综合态条件编译宏
        "-D__VITIS_HLS__=1",  # 打开 Vitis HLS 条件编译宏
        "-I",  # 后续目录作为第一层 fake include 搜索路径
        str(fake_include),  # fake HLS 头文件根目录
        "-I",  # 后续目录作为项目根 include 搜索路径
        str(root),  # 让仓库级 include 能被 clang 正常解析
        "-I",  # 后续目录作为源文件局部 include 搜索路径
        str(path.parent),  # 当前源文件所在目录
        "-Xclang",  # 直传底层 clang 专有参数
        "-ast-dump=json",  # 让 clang 输出 JSON 形式的 AST
        str(path),  # 需要解析的目标文件路径
    ]

    # clang 调用可能超时或因环境缺失失败，失败只降级为诊断。
    try:

        # capture_output 保留 stdout JSON 和 stderr 诊断，check=False 由后续逻辑处理。
        completed_process_completed_process: subprocess.CompletedProcess[str] = subprocess.run(  # clang 子进程执行结果
            list_command,  # 完整 clang 命令参数
            cwd=root,  # 让相对 include 路径以项目根为基准
            capture_output=True,  # 同时捕获 stdout 和 stderr
            text=True,  # 以文本模式返回子进程输出

            # 限制外部 clang 调用时长，防止质量门被异常源码拖住。
            timeout=30,  # 最长等待 30 秒
            check=False,  # 非零返回码交由当前函数自己处理
        )

    # 超时表示 clang 在治理时限内无法稳定产出 AST。
    except subprocess.TimeoutExpired:

        # 超时说明 provider 当前不可用，但不阻塞 lexer 回退。
        return AstParseResult(False, "clang_ast_dump", detail="clang AST dump timed out")

    # OSError 说明 clang 进程甚至没有成功启动。
    except OSError as exc:

        # OSError 记录底层进程启动失败原因。
        return AstParseResult(False, "clang_ast_dump", detail=str(exc))

    # 非零返回码通常来自语法或 include 失败，保留短诊断。
    if completed_process_completed_process.returncode != 0:

        # stderr 优先，stdout 作为某些 clang 版本的兜底诊断。
        str_diagnostic = _short_diagnostic(  # 截断后的 clang 失败摘要
            completed_process_completed_process.stderr  # 优先采用 stderr 诊断
            or completed_process_completed_process.stdout  # stderr 为空时退回 stdout 诊断
        )

        # 返回失败结果，runner 会登记 parse_failures。
        return AstParseResult(False, "clang_ast_dump", detail=str_diagnostic)

    # clang stdout 应为 AST JSON，解析失败时登记 provider 失败。
    try:

        # JSON 载荷会在下一步去除位置和指针噪声。
        ast_payload: Any = json.loads(completed_process_completed_process.stdout)  # clang AST 结构载荷

    # JSON 解码失败说明 clang 输出不能作为稳定 AST 载荷。
    except json.JSONDecodeError as exc:

        # JSON 解码失败说明 clang 输出不可用于稳定指纹。
        return AstParseResult(False, "clang_ast_dump", detail=f"clang AST JSON decode failed: {exc}")

    # 去除位置、id 和内存地址后再生成稳定 hash。
    normalized_payload = _normalize_clang_ast(ast_payload)  # 去噪后的 clang AST 结构

    # 即使 clang 成功，函数边界仍复用 cpp_lexer 的轻量结果。
    tuple_functions = _parse_functions_from_file(path)  # clang 成功后的函数边界信息

    # 返回成功解析的稳定指纹和函数信息。
    return AstParseResult(
        True,
        "clang_ast_dump",
        fingerprint=_stable_fingerprint(normalized_payload),
        functions=tuple_functions,
    )

# _normalize_clang_ast 移除 clang AST 中随路径或编译轮次变化的字段。
def _normalize_clang_ast(value: Any) -> Any:
    """递归归一化 clang AST 载荷。

    Args:
        value: clang AST JSON 解析后的任意节点值。

    Returns:
        去除位置、id 和地址噪声后的可哈希结构。
    """

    # 字典节点需要删除位置、range 和引用 id 等不稳定字段。
    if isinstance(value, dict):

        # cleaned_items 保存当前 AST 字典节点的稳定字段。
        dict_cleaned_items: dict[str, Any] = {}  # 归一化后的字典字段

        # 逐项复制稳定字段，递归处理嵌套节点。
        for str_key, tree_ast_node_payload in value.items():

            # clang 位置和引用 id 会随环境变化，不能进入稳定指纹。
            if str_key in {
                "id",
                "loc",
                "range",
                "tokLen",
                "offset",
                "line",
                "col",
                "includedFrom",
                "spellingLoc",
                "expansionLoc",
                "previousDecl",
                "parentDeclContextId",
                "referencedMemberDecl",
                "referencedDecl",
                "foundReferencedDecl",
            }:

                # 跳过不稳定字段。
                continue

            # 以 Loc/Range 结尾的字段同样属于源码位置噪声。
            if str_key.endswith("Loc") or str_key.endswith("Range"):

                # 跳过位置型字段。
                continue

            # 保留字段值也要递归归一化。
            dict_cleaned_items[str_key] = _normalize_clang_ast(tree_ast_node_payload)  # 归一化后的稳定字段值

        # 返回当前字典节点的稳定结构。
        return dict_cleaned_items

    # 列表节点逐项归一化，保持 AST 子节点顺序。
    if isinstance(value, list):

        # 返回同顺序的归一化子节点列表。
        return [_normalize_clang_ast(tree_ast_node_payload) for tree_ast_node_payload in value]

    # 字符串中的十六进制地址会随 clang 运行变化。
    if isinstance(value, str):

        # 替换内存地址样式文本，避免指纹因运行而变化。
        return re.sub(r"0x[0-9a-fA-F]+", "<clang-id>", value)

    # 其它标量值可直接进入稳定指纹。
    return value

# _has_tree_sitter_cpp 判断 tree-sitter C++ provider 是否可导入。
def _has_tree_sitter_cpp() -> bool:
    """判断当前环境是否具备 tree-sitter C++ 解析能力。

    Args:
        无外部业务参数。

    Returns:
        tree_sitter 与任一 C++ 语言绑定可用时返回 True。
    """

    # tree_sitter 是基础解析器包，缺失时无需继续检查语言绑定。
    bool_has_parser = importlib.util.find_spec("tree_sitter") is not None  # tree_sitter 包是否存在

    # 任一 C++ 语言绑定可用即可构造 tree-sitter provider。
    bool_has_cpp_language = (  # 可选 C++ grammar 绑定是否存在
        importlib.util.find_spec("tree_sitter_cpp") is not None  # 独立 grammar 包是否存在
        or importlib.util.find_spec("tree_sitter_languages") is not None  # 兼容多语言打包分发的 grammar 集合
    )

    # 同时具备解析器和 C++ grammar 才能使用 tree-sitter。
    return bool_has_parser and bool_has_cpp_language

# _parse_with_tree_sitter 使用 tree-sitter 解析 C++ 语法树。
def _parse_with_tree_sitter(root: Path, path: Path, fake_include: Path) -> AstParseResult:
    """使用 tree-sitter C++ provider 解析 HLS 源文件。

    Args:
        root: 项目根目录；tree-sitter 路径不需要该值。
        path: 待解析的 HLS 源文件。
        fake_include: fake HLS include 目录；tree-sitter 路径不需要该值。

    Returns:
        成功时返回 tree-sitter 语法树指纹；失败时返回诊断摘要。
    """

    # root 和 fake_include 由统一 provider 签名传入，tree-sitter 不需要使用。
    del root, fake_include

    # tree-sitter 依赖可能不存在或 API 版本不同，失败时降级为诊断。
    try:

        # Parser 只在 provider 被选中后导入，避免导入期依赖要求。
        from tree_sitter import Parser

        # 获取当前环境可用的 C++ language 对象。
        tree_sitter_language: Any = _tree_sitter_cpp_language()  # C++ grammar 语言对象

        # Parser 实例承载本次文件解析状态。
        tree_sitter_parser = Parser()  # tree-sitter 解析器实例

        # 新旧 tree-sitter API 在设置 language 时不完全一致。
        try:

            # 旧版 API 使用 set_language。
            tree_sitter_parser.set_language(tree_sitter_language)

        # AttributeError 说明当前版本改用 language 属性注入 grammar。
        except AttributeError:

            # 新版 API 直接赋值 language 属性。
            tree_sitter_parser.language = tree_sitter_language  # 新版 parser 使用的 C++ grammar 对象

        # 读取文件字节并交给 tree-sitter 构建语法树。
        parsed_tree: Any = tree_sitter_parser.parse(path.read_bytes())  # C++ 源码语法树

        # 根节点用于归一化 AST 和检测 ERROR 节点。
        root_node: Any = parsed_tree.root_node  # C++ 语法树根节点

        # ERROR 节点说明 tree-sitter 未能可靠理解当前源码。
        if root_node.has_error:

            # 解析错误以 provider 失败返回，供 runner 记录。
            return AstParseResult(False, "tree_sitter_cpp", detail="tree-sitter reported ERROR nodes")

        # 注释节点和位置噪声不进入稳定指纹。
        normalized_tree = _normalize_tree_sitter_node(root_node)  # 归一化 tree-sitter 语法树

        # 函数边界复用 cpp_lexer，保持 provider 间一致。
        tuple_functions = _parse_functions_from_file(path)  # tree-sitter 路径补采的函数签名边界

        # 返回成功解析结果。
        return AstParseResult(
            True,
            "tree_sitter_cpp",
            fingerprint=_stable_fingerprint(normalized_tree),
            functions=tuple_functions,
        )

    # 任意可选依赖或 ABI 失败都需要降级为诊断，而不是中断治理流程。
    except Exception as exc:

        # 记录可选 provider 失败原因，不阻止其它规则继续运行。
        return AstParseResult(False, "tree_sitter_cpp", detail=str(exc))

# _tree_sitter_cpp_language 兼容两种常见 C++ grammar 包。
def _tree_sitter_cpp_language() -> Any:
    """获取 tree-sitter C++ language 对象。

    Args:
        无外部业务参数。

    Returns:
        可传给 tree-sitter Parser 的 C++ language 对象。
    """

    # 优先使用专门的 tree_sitter_cpp 包。
    try:

        # 独立 C++ grammar 包在新环境中最常见。
        import tree_sitter_cpp

        # 返回该包暴露的 language 对象。
        return tree_sitter_cpp.language()

    # grammar 包 API 差异时回退到 tree_sitter_languages 的统一入口。
    except Exception:

        # tree_sitter_languages 提供统一 get_language 回退。
        from tree_sitter_languages import get_language

        # 返回 cpp grammar 对象。
        return get_language("cpp")

# _normalize_tree_sitter_node 移除注释节点并保留语法形状。
def _normalize_tree_sitter_node(node: Any) -> Any:
    """递归归一化 tree-sitter 节点。

    Args:
        node: tree-sitter 语法树节点。

    Returns:
        不含注释节点的类型、命名状态和子节点结构；注释节点返回 None。
    """

    # 注释内容不参与 AST provider 结构证明。
    if node.type == "comment":

        # None 表示父节点应过滤该子节点。
        return None

    # 递归归一化所有子节点。
    list_children = [_normalize_tree_sitter_node(child_node) for child_node in node.children]  # 子节点归一化结果

    # 返回当前节点的稳定语法形状。
    return {
        "type": node.type,
        "named": bool(node.is_named),
        "children": [child_node for child_node in list_children if child_node is not None],
    }

# _parse_with_pycparser 处理 C99 风格 HLS 文件的 AST 指纹。
def _parse_with_pycparser(root: Path, path: Path, fake_include: Path) -> AstParseResult:
    """使用 pycparser 解析 C99 风格的 HLS 源文件。

    Args:
        root: 项目根目录；pycparser 路径不需要该值。
        path: 待解析的 C/H 目标文件。
        fake_include: fake HLS include 目录；pycparser 路径不需要该值。

    Returns:
        成功时返回 pycparser AST 指纹；不支持 C++ 或解析失败时返回诊断。
    """

    # root 和 fake_include 由统一 provider 签名传入，pycparser 不直接使用。
    del root, fake_include

    # pycparser 不能可靠解析 C++ HLS 源文件。
    if path.suffix.lower() not in {".c", ".h"}:

        # 明确说明 pycparser 的后缀边界。
        return AstParseResult(
            False,
            "pycparser_c99",
            detail="pycparser only supports C99-style .c/.h files, not C++ HLS sources",
        )

    # pycparser 依赖可能不存在或源码不兼容，失败以诊断返回。
    try:

        # c_parser 只在 provider 被选中后导入，避免强制依赖。
        from pycparser import c_parser

        # 注释剥离复用 cpp_lexer，保留字符串内容。
        from .cpp_lexer import strip_comments_preserving_strings

        # 原始源码先去掉注释，降低 pycparser 的噪声。
        str_stripped_source = strip_comments_preserving_strings(  # 去注释后的原始源码文本
            path.read_text(encoding="utf-8", errors="ignore")  # 原始文件文本
        )

        # 预处理指令不交给 pycparser，避免缺少真实预处理器。
        str_preprocessed_source = "\n".join(  # 过滤预处理行后的 C 源码
            str_line  # 保留的普通源码行
            for str_line in str_stripped_source.splitlines()  # 遍历去注释后的源码物理行
            if not str_line.lstrip().startswith("#")  # 丢弃预处理指令行
        )

        # pycparser 生成 C AST，filename 仅用于诊断。
        tree_pycparser_ast: Any = c_parser.CParser().parse(  # pycparser 生成的 C99 语法树
            str_preprocessed_source,  # 预处理行已剥离的源码文本
            filename=str(path),  # 诊断中保留原文件路径
        )

        # StringIO 接收 pycparser show 输出，便于生成稳定指纹。
        string_i_o_string_buffer: io.StringIO = io.StringIO()  # pycparser 展开文本缓冲

        # showcoord=False 避免源码位置进入指纹。
        tree_pycparser_ast.show(attrnames=True, showcoord=False, buf=string_i_o_string_buffer)

        # 函数边界仍复用 cpp_lexer 的结果。
        tuple_functions = _parse_functions_from_file(path)  # pycparser 分支补采的函数签名边界元组

        # 返回 pycparser AST 文本的稳定指纹。
        return AstParseResult(
            True,
            "pycparser_c99",
            fingerprint=_stable_fingerprint(string_i_o_string_buffer.getvalue()),
            functions=tuple_functions,
        )

    # 可选 provider 的解析失败必须转成诊断，保证其它规则还能继续执行。
    except Exception as exc:

        # 记录 pycparser 失败原因。
        return AstParseResult(False, "pycparser_c99", detail=str(exc))

# write_fake_hls_headers 写入 clang/pycparser 解析 HLS kernel 所需的最小头文件。
def write_fake_hls_headers(include_dir: Path) -> Path:
    """写入 Vitis HLS 常见头文件的最小解析桩。

    Args:
        include_dir: 临时 fake include 目录。

    Returns:
        已写入 fake HLS 头文件的 include 目录路径。
    """

    # fake include 目录由调用方传入临时目录，必要时递归创建。
    include_dir.mkdir(parents=True, exist_ok=True)

    # ap_int.h 桩覆盖 HLS 中常见的 ap_uint/ap_int 算术与转换接口。
    str_ap_int_header = r'''
#pragma once
#ifndef HLS_AST_PROVIDER_AP_INT_H
#define HLS_AST_PROVIDER_AP_INT_H
#include <cstddef>
template<int W>
class ap_uint {
public:
  unsigned long long value;
  ap_uint() : value(0) {}
  template<typename T> ap_uint(T input) : value((unsigned long long)input) {}
  template<typename T> ap_uint& operator=(T input) { value = (unsigned long long)input; return *this; }
  template<typename T> ap_uint operator+(T) const { return ap_uint(); }
  template<typename T> ap_uint operator-(T) const { return ap_uint(); }
  template<typename T> ap_uint operator*(T) const { return ap_uint(); }
  template<typename T> ap_uint operator/(T) const { return ap_uint(); }
  template<typename T> ap_uint operator<<(T) const { return ap_uint(); }
  template<typename T> ap_uint operator>>(T) const { return ap_uint(); }
  ap_uint& operator+=(ap_uint) { return *this; }
  ap_uint& operator-=(ap_uint) { return *this; }
  bool operator==(ap_uint) const { return true; }
  bool operator!=(ap_uint) const { return false; }
  bool operator<(ap_uint) const { return false; }
  bool operator>(ap_uint) const { return false; }
  template<typename T> bool operator==(T) const { return true; }
  template<typename T> bool operator!=(T) const { return false; }
  operator unsigned() const { return (unsigned)value; }
  operator unsigned long long() const { return value; }
  operator int() const { return (int)value; }
  operator double() const { return (double)value; }
};
template<int W>
class ap_int : public ap_uint<W> {
public:
  ap_int() : ap_uint<W>() {}
  template<typename T> ap_int(T input) : ap_uint<W>(input) {}
};
#endif
'''  # ap_int 与 ap_uint 解析桩源码

    # ap_fixed.h 桩覆盖定点类型模板和量化/溢出枚举。
    str_ap_fixed_header = r'''
#pragma once
#include "ap_int.h"
enum ap_q_mode { AP_TRN, AP_TRN_ZERO, AP_RND, AP_RND_ZERO, AP_RND_MIN_INF, AP_RND_INF, AP_RND_CONV };
enum ap_o_mode { AP_WRAP, AP_SAT, AP_SAT_ZERO, AP_SAT_SYM };
template<int W, int I, ap_q_mode Q = AP_TRN, ap_o_mode O = AP_WRAP, int N = 0>
class ap_fixed : public ap_int<W> {
public:
  ap_fixed() : ap_int<W>() {}
  template<typename T> ap_fixed(T input) : ap_int<W>(input) {}
};
template<int W, int I, ap_q_mode Q = AP_TRN, ap_o_mode O = AP_WRAP, int N = 0>
class ap_ufixed : public ap_uint<W> {
public:
  ap_ufixed() : ap_uint<W>() {}
  template<typename T> ap_ufixed(T input) : ap_uint<W>(input) {}
};
'''  # ap_fixed/ap_ufixed 的最小模板桩

    # hls_stream.h 桩覆盖 read/write/empty 这类常见 stream 操作。
    str_hls_stream_header = r'''
#pragma once
#include "ap_int.h"
namespace hls {
template<typename T>
class stream {
public:
  stream() {}
  bool empty() const { return false; }
  T read() { return T(); }
  void write(const T&) {}
};
}
'''  # hls stream 解析桩源码

    # ap_axi_sdata.h 桩覆盖 AXI stream 结构字段。
    str_ap_axi_header = r'''
#pragma once
#include "ap_int.h"
template<int D, int U, int TI, int TD>
struct ap_axiu {
  ap_uint<D> data;
  ap_uint<(D + 7) / 8> keep;
  ap_uint<(D + 7) / 8> strb;
  ap_uint<U> user;
  ap_uint<1> last;
  ap_uint<TI> id;
  ap_uint<TD> dest;
};
'''  # ap_axiu 结构体解析桩源码

    # hls_task.h 桩覆盖 DATAFLOW/task 示例中可能出现的 hls::task。
    str_hls_task_header = r'''
#pragma once
namespace hls {
class task {
public:
  task() {}
  template<typename... Args> task(Args&&...) {}
};
}
'''  # hls::task 的最小类定义桩

    # hls_vector.h 桩覆盖固定长度向量及下标访问。
    str_hls_vector_header = r'''
#pragma once
namespace hls {
template<typename T, int N>
class vector {
public:
  T data[N];
  T& operator[](int index) { return data[index]; }
  const T& operator[](int index) const { return data[index]; }
};
}
'''  # hls::vector 的最小容器桩

    # 标准头桩提供 clang 解析 fake HLS 类型所需的基础 typedef。
    str_fake_cstddef_header = "#pragma once\ntypedef __SIZE_TYPE__ size_t;\n"  # size_t 类型解析桩源码

    # cstdint 桩覆盖常见定宽整数类型。
    str_fake_cstdint_header = r'''
#pragma once
typedef signed char int8_t;
typedef short int16_t;
typedef int int32_t;
typedef long long int64_t;
typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
'''  # 定宽整数类型解析桩源码

    # cmath 桩提供常见数学函数，避免示例 kernel 因标准库缺失失败。
    str_fake_cmath_header = r'''
#pragma once
namespace std {
inline double sin(double value) { return value; }
inline double cos(double value) { return value; }
inline double sqrt(double value) { return value; }
inline double fabs(double value) { return value < 0 ? -value : value; }
inline float fabs(float value) { return value < 0 ? -value : value; }
inline double abs(double value) { return value < 0 ? -value : value; }
}
using std::sin;
using std::cos;
using std::sqrt;
using std::fabs;
using std::abs;
'''  # 常见数学函数解析桩源码

    # iostream 桩提供 cout/cerr 和 operator<<，覆盖 testbench 中的简单输出。
    str_fake_iostream_header = r'''
#pragma once
namespace std {
class ostream {
public:
  template<typename T> ostream& operator<<(const T&) { return *this; }
};
static ostream cout;
static ostream cerr;
}
'''  # 简易输出流解析桩源码

    # 空 HLS 头桩用于只需通过 include 的辅助 HLS 头文件。
    str_empty_hls_header = "#pragma once\n"  # include 存在性检查用的最小 HLS 头文件源码

    # dict_files 记录 fake include 中每个文件名对应的源码内容。
    dict_files: dict[str, str] = {  # 让 clang/pycparser 在缺少真实 Vitis HLS 头文件时仍能补齐常见 include
        "cstddef": str_fake_cstddef_header,  # C++ size_t 头文件桩
        "stddef.h": str_fake_cstddef_header,  # C 兼容 size_t 头文件桩
        "cstdint": str_fake_cstdint_header,  # C++ 定宽整数头文件桩
        "stdint.h": str_fake_cstdint_header,  # C 兼容定宽整数头文件桩
        "cmath": str_fake_cmath_header,  # C++ 数学函数头文件桩
        "math.h": str_fake_cmath_header,  # C 兼容数学函数头文件桩
        "iostream": str_fake_iostream_header,  # 简易输出流头文件桩
        "cstdio": "#pragma once\nextern \"C\" int printf(const char*, ...);\n",  # printf 声明头文件桩
        "stdio.h": "#pragma once\nextern \"C\" int printf(const char*, ...);\n",  # C 兼容 printf 声明桩
        "ap_int.h": str_ap_int_header,  # ap_int 与 ap_uint 类型头文件桩
        "ap_fixed.h": str_ap_fixed_header,  # 定点类型头文件桩
        "ap_axi_sdata.h": str_ap_axi_header,  # AXI stream 结构头文件桩
        "hls_stream.h": str_hls_stream_header,  # 提供流读写与空判断成员的最小头文件桩
        "hls_task.h": str_hls_task_header,  # DATAFLOW task 类定义桩
        "hls_vector.h": str_hls_vector_header,  # 固定长度向量容器桩
        "hls_streamofblocks.h": str_empty_hls_header,  # 空 stream blocks 头文件桩
        "hls_directio.h": str_empty_hls_header,  # 仅满足 include 的 directio 占位桩
        "hls_fence.h": str_empty_hls_header,  # fence 依赖的空占位头
        "hls_math.h": "#pragma once\n#include <cmath>\n",  # 转接 cmath 的 HLS 数学头文件桩
    }

    # 写出所有 fake 头文件，clang 通过 -I 指向该目录。
    for str_name, str_text in dict_files.items():

        # lstrip 保持原行为：去掉三引号开头空行。
        (include_dir / str_name).write_text(str_text.lstrip(), encoding="utf-8")

    # 返回 include 目录，调用方可直接传给 provider。
    return include_dir

# _parse_functions_from_file 封装重复的源码读取和 cpp_lexer 函数解析。
def _parse_functions_from_file(path: Path) -> tuple[FunctionInfo, ...]:
    """从源码文件中提取函数边界信息。

    Args:
        path: 需要读取的 HLS C/C++ 源文件路径。

    Returns:
        cpp_lexer 识别到的函数信息元组。
    """

    # 读取源码时忽略不可解码字节，保持 gate 对混杂编码样例的容忍。
    list_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()  # 源码物理行

    # parse_functions 返回 iterable，统一收敛为不可变 tuple。
    return tuple(parse_functions(list_lines))

# _stable_fingerprint 为归一化 AST 生成确定性 SHA-256 指纹。
def _stable_fingerprint(value: Any) -> str:
    """生成归一化 AST 载荷的稳定 SHA-256 指纹。

    Args:
        value: 已归一化的 AST 结构或文本。

    Returns:
        UTF-8 JSON 规范化后的 SHA-256 十六进制摘要。
    """

    # JSON 序列化使用固定排序和紧凑分隔符，保证同结构同指纹。
    str_payload = json.dumps(  # 指纹输入使用的规范化 JSON 文本
        value,  # 已归一化的 AST 载荷
        ensure_ascii=False,  # 保留中文等非 ASCII 内容的原始字符
        sort_keys=True,  # 固定字典键顺序
        separators=(",", ":"),  # 使用紧凑分隔符去除格式噪声
    )

    # 返回 SHA-256 摘要供报告和测试稳定比较。
    return hashlib.sha256(str_payload.encode("utf-8")).hexdigest()

# _short_diagnostic 将外部工具诊断压缩为报告可承载的摘要。
def _short_diagnostic(text: str, *, limit: int = 4000) -> str:
    """截断外部工具诊断文本。

    Args:
        text: clang 或其它外部工具输出的原始诊断文本。
        limit: 保留的最大字符数。

    Returns:
        去除回车并截断后的诊断摘要。
    """

    # normalized_text 去除 Windows 回车，避免报告中出现平台差异。
    str_normalized_text = (text or "").strip().replace("\r", "")  # 规范化诊断文本

    # 返回固定长度以内的诊断摘要。
    return str_normalized_text[:limit]
