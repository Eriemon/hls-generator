"""封装基础质量门中的 docstring 规则。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import ast
from pathlib import Path

# 导入当前模块运行所需的依赖
from .ast_helpers import add_issue, is_public_name
from .profiles import ProfileConfig
from .report import Issue

# 类型标注需要转换成文本后才能判断是否为 None 返回。
def annotation_to_text(annotation: ast.AST | None) -> str:
    """将类型标注节点转换为可比较文本。

    参数:
        annotation: AST 中解析到的类型标注节点。

    返回:
        可比较的类型标注源码文本；无法转换时返回空字符串。
    """

    # 没有类型标注时没有可比较的返回契约文本。
    if annotation is None:

        # 空字符串让调用方按“未声明可分析类型”处理。
        return ""

    # 兼容包内导入和脚本直接运行两种入口
    try:

        # ast.unparse 保留联合类型、泛型等源码级结构。
        return ast.unparse(annotation)

    # ast.unparse 失败时退回保守空字符串，避免误判返回契约文本。
    except Exception:

        # 极少数解释器差异下，无法还原的标注不参与文本判断。
        return ""

# 缺失型文档项进入治理 backlog，避免用模板 docstring 填满旧代码。
def style_issue_level(config: ProfileConfig, code: str) -> str:
    """根据规则类型返回 docstring 类问题级别。

    参数:
        config: 控制 profile、风格开关和阈值的质量门配置。
        code: 当前发现项的 PG 规则编号。

    返回:
        根据 strict project style 选择问题严重级别产出的结果。
    """

    # 非 strict current-project 风格下，这类问题只保留为提示信息。
    if not config.strict_project_style:

        # 非严格项目风格下，文档字符串问题只作为可治理提示。
        return "WARNING"

    # 这几类遗留文档项优先作为 backlog，避免一次性阻断全目录治理。
    if code in {"PG025", "PG043", "PG044"}:

        # 缺少 docstring 的遗留治理项保持 warning，避免一次性阻断目录扫描。
        return "WARNING"

    # 其他 docstring 合同问题在严格模式下继续保持阻断。
    return "BLOCKER"

# docstring 段落检测同时兼容中文标题和常见英文风格。
def docstring_has_marker(docstring: str, markers: tuple[str, ...]) -> bool:
    """判断 docstring 是否包含指定语义段落标记。

    参数:
        docstring: 正在检查的函数或模块文档字符串。
        markers: 可接受的段落标题或标记词集合。

    返回:
        True 表示 docstring 文本包含任一标记词。
    """

    # 生成lowereddocstring的小写副本，确保关键词匹配不受大小写影响。
    str_lowered_docstring = docstring.lower()  # 小写化后的文档字符串

    # 只要任一可接受标题命中，就认为相关说明段已经存在。
    return any(marker.lower() in str_lowered_docstring for marker in markers)

# 公开函数参数名用于检查 docstring 是否逐项说明。
def function_argument_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """收集需要在 docstring 中说明的公开参数名称。

    参数:
        node: 需要检查 docstring 的函数定义节点。

    返回:
        排除 self/cls 后仍需在 docstring 中说明的参数名列表。
    """

    # 普通参数、仅位置参数和仅关键字参数都属于公开签名。
    list_arguments = [
        *node.args.posonlyargs,  # 仅位置参数列表
        *node.args.args,  # 常规位置参数列表
        *node.args.kwonlyargs,  # 仅关键字参数列表
    ]  # 函数定义中的位置参数节点

    # self/cls 由方法约定解释，不要求在 docstring 中逐项说明。
    list_argument_names = [
        argument.arg  # 公开参数名称
        for argument in list_arguments  # 逐个遍历候选形参节点
        if argument.arg not in {"self", "cls"}  # 排除方法约定保留参数
    ]  # 函数签名中的形参名称

    # *args 也属于公开调用入口，需要在参数说明中出现。
    if node.args.vararg is not None:

        # 追加可变位置参数名，后续统一检查 docstring 覆盖。
        list_argument_names.append(node.args.vararg.arg)

    # **kwargs 代表开放关键字扩展入口，也必须在 docstring 中体现。
    if node.args.kwarg is not None:

        # 追加可变关键字参数名，后续统一检查 docstring 覆盖。
        list_argument_names.append(node.args.kwarg.arg)

    # 返回公开参数名列表，供参数段落完整性检查使用。
    return list_argument_names

# 返回值检查需要同时看显式标注和函数体 return 语句。
def function_has_return_value(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断函数是否声明或实际返回业务值。

    参数:
        node: 需要检查 docstring 的函数定义节点。

    返回:
        True 表示函数声明或实际返回非 None 业务值。
    """

    # 显式非 None 返回标注意味着 docstring 必须描述返回契约。
    if node.returns is not None:

        # None 返回不需要额外的返回值段落。
        return annotation_to_text(node.returns) not in {"None", "NoneType"}

    # 扫描 AST 节点，寻找本规则关注的语法结构。
    for child in ast.walk(node):

        # 函数体中实际返回表达式时，也要要求返回说明。
        if isinstance(child, ast.Return) and child.value is not None:

            # 命中非空 return 表达式后，可以确认函数存在业务返回值。
            return True

    # 没有显式非空返回时，按“无业务返回值”处理。
    return False

# 异常说明只在函数体存在显式 raise 时强制要求。
def function_raises_exception(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断函数体内是否显式抛出异常。

    参数:
        node: 需要检查 docstring 的函数定义节点。

    返回:
        True 表示函数体内存在显式 raise 语句。
    """

    # 只要函数体内存在 raise，就要求 docstring 补充异常路径说明。
    return any(isinstance(child, ast.Raise) for child in ast.walk(node))

# `check_module_docstring` 检查并登记模块文档字符串。
def check_module_docstring(tree: ast.Module, filepath: Path, issues: list[Issue], config: ProfileConfig) -> None:
    """检查模块级 docstring 是否存在。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        None。该函数只在模块 docstring 缺失时向 ``issues`` 追加发现项。
    """

    # profile 未要求公开 docstring 时跳过 PG043。
    if not config.require_public_docstrings:

        # 当前 profile 未要求公开文档契约，因此本轮不检查模块 docstring。
        return

    # 空的 __init__.py 常用于包标记，不强制模块 docstring。
    if filepath.name == "__init__.py" and not tree.body:

        # 空包标记文件无需额外补写模块说明。
        return

    # 模块缺少 docstring 时无法说明文件级用途和边界。
    if ast.get_docstring(tree) is None:

        # PG043 用于提示模块级文档缺失。
        add_issue(
            issues,
            "PG043",
            style_issue_level(config, "PG043"),
            filepath,
            1,
            "Module lacks a top-level docstring describing its purpose.",
        )

# 参数说明既要有参数段落，也要包含每个公开参数名。
def missing_parameter_docstring_detail(node: ast.FunctionDef | ast.AsyncFunctionDef, docstring: str) -> bool:
    """判断函数参数是否缺少 docstring 说明。

    参数:
        node: 需要检查 docstring 的函数定义节点。
        docstring: 正在检查的函数或模块文档字符串。

    返回:
        True 表示缺少参数说明段落或遗漏公开参数名。
    """

    # 只检查公开签名中需要向调用方解释的参数。
    list_argument_names = function_argument_names(node)  # 需要说明的公开参数名

    # 无公开参数时不需要参数说明段。
    if not list_argument_names:

        # 没有公开业务参数时，本规则不强制要求参数段落。
        return False

    # 兼容中文、Sphinx 和 Google/Numpy 风格的参数段落标题。
    tuple_parameter_markers = ("参数", ":param", "Args", "Arguments", "Parameters")  # 参数说明段落标记词

    # 缺少参数段会让参数名即使命中也不够清晰。
    bool_has_parameter_section = docstring_has_marker(docstring, tuple_parameter_markers)  # 是否存在参数说明段落

    # 每个公开参数名都应当在 docstring 中出现。
    bool_has_argument_names = all(argument_name in docstring for argument_name in list_argument_names)  # 是否覆盖全部公开参数名

    # 任一条件缺失都表示参数说明不完整。
    # 参数段落缺失或参数名覆盖不全，都视为说明不完整。
    return not bool_has_parameter_section or not bool_has_argument_names

# 有业务返回值的函数必须在 docstring 中说明返回契约。
def missing_return_docstring_detail(node: ast.FunctionDef | ast.AsyncFunctionDef, docstring: str) -> bool:
    """判断函数返回值是否缺少 docstring 说明。

    参数:
        node: 需要检查 docstring 的函数定义节点。
        docstring: 正在检查的函数或模块文档字符串。

    返回:
        True 表示函数有业务返回值但 docstring 缺少返回说明。
    """

    # 兼容中文、Sphinx 和英文风格的返回段落标题。
    tuple_return_markers = ("返回", ":return", "Returns", "Return")  # 返回说明段落标记词

    # 仅在函数确实返回业务值且缺少返回段落时报告缺失。
    return function_has_return_value(node) and not docstring_has_marker(docstring, tuple_return_markers)

# 显式 raise 的函数必须在 docstring 中说明异常路径。
def missing_exception_docstring_detail(node: ast.FunctionDef | ast.AsyncFunctionDef, docstring: str) -> bool:
    """判断函数异常路径是否缺少 docstring 说明。

    参数:
        node: 需要检查 docstring 的函数定义节点。
        docstring: 正在检查的函数或模块文档字符串。

    返回:
        True 表示函数会显式抛出异常但 docstring 缺少异常说明。
    """

    # 兼容中文、Sphinx 和英文风格的异常段落标题。
    tuple_exception_markers = ("异常", "抛出", ":raises", "Raises", "Raise")  # 异常说明段落标记词

    # 仅在函数会显式抛异常且缺少异常段落时报告缺失。
    return function_raises_exception(node) and not docstring_has_marker(docstring, tuple_exception_markers)

# `collect_missing_docstring_parts` 收集缺失项文档字符串parts。
def collect_missing_docstring_parts(node: ast.FunctionDef | ast.AsyncFunctionDef, docstring: str) -> list[str]:
    """收集函数 docstring 缺少的语义部分。

    参数:
        node: 当前遍历到的 AST 节点。
        docstring: 正在检查的函数或模块文档字符串。

    返回:
        按参数、返回值、异常顺序收集到的缺失说明类别列表。
    """

    # 缺失项按参数、返回值和异常三类汇总到一条诊断中。
    list_missing_parts: list[str] = []  # 列表缺失项parts

    # 公开参数没有完整说明会影响调用方正确传参。
    if missing_parameter_docstring_detail(node, docstring):

        # 记录参数说明缺失，供 PG044 统一汇总展示。
        list_missing_parts.append("parameters")

    # 有业务返回值却缺少说明会让调用方无法理解返回契约。
    if missing_return_docstring_detail(node, docstring):

        # 把返回段落缺口加入缺失类别列表，供 PG044 一次性汇总。
        list_missing_parts.append("return")

    # 函数会抛出异常时，docstring 需要说明失败条件。
    if missing_exception_docstring_detail(node, docstring):

        # 把异常说明缺口登记到汇总列表，提示调用方补齐失败路径文档。
        list_missing_parts.append("exceptions")

    # 把汇总后的缺失类别列表返回给调用方，用于拼装 PG044 消息。
    return list_missing_parts

# `check_function_docstring_detail` 检查并登记函数文档字符串detail。
def check_function_docstring_detail(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """检查函数 docstring 是否说明参数、返回值和异常。

    参数:
        node: 当前遍历到的 AST 节点。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        None。该函数只在公开函数 docstring 内容不完整时登记 PG044。
    """

    # profile 未要求公开 docstring 时跳过函数细节检查。
    if not config.require_public_docstrings:

        # 当前 profile 未启用公开 docstring 细节约束，直接结束本函数。
        return

    # 私有函数不作为公开 API 文档契约检查对象。
    if not is_public_name(node.name):

        # 私有 helper 不纳入公开文档契约检查范围。
        return

    # 读取文档字符串内容，检查公开 API 说明是否完整。
    str_docstring = ast.get_docstring(node) or ""  # 当前函数文档字符串

    # 缺少 docstring 本身由 PG025 处理，这里只检查已有文档的内容。
    if not str_docstring:

        # 缺少 docstring 本身会由 PG025 处理，这里不重复登记。
        return

    # 收集参数、返回值和异常说明的缺失类别。
    list_missing_parts = collect_missing_docstring_parts(node, str_docstring)  # 文档字符串缺失段落

    # 缺失类别非空时登记一条 PG044 细节问题。
    if list_missing_parts:

        # 诊断消息列出缺失类别，便于一次性补全文档。
        add_issue(
            issues,
            "PG044",
            style_issue_level(config, "PG044"),
            filepath,
            node.lineno,
            f"Function `{node.name}` docstring does not describe: {', '.join(list_missing_parts)}.",
        )

# `check_docstring_contracts` 检查并登记文档字符串contracts。
def check_docstring_contracts(tree: ast.Module, filepath: Path, issues: list[Issue], config: ProfileConfig) -> None:
    """检查模块和函数 docstring 的信息完整性。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        None。该函数只负责串联模块级与函数级 docstring 合同检查。
    """

    # 执行子规则检查，保持主流程只负责编排顺序。
    check_module_docstring(tree, filepath, issues, config)

    # 遍历整棵语法树，把函数节点逐个送入细节合同检查。
    for node in ast.walk(tree):

        # 只有函数节点需要补做参数、返回值和异常说明完整性检查。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 把单个函数的文档合同细节检查委派给专门子规则。
            check_function_docstring_detail(node, filepath, issues, config)

# `check_public_docstrings` 检查并登记公开符号docstrings。
def check_public_docstrings(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """检查 public_docstrings 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        None。该函数只在公开定义缺失 docstring 时登记 PG025。
    """

    # 公开 docstring 规则未启用时，整段公开定义扫描可以直接省略。
    if not config.require_public_docstrings:

        # 当前 profile 不要求公开 docstring，因此整段扫描直接跳过。
        return

    # 逐个检查公开定义节点，确认哪些对象连最基本的 docstring 都还缺失。
    for node in ast.walk(tree):

        # 只有函数、异步函数和类才存在公开对象 docstring。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):

            # 非定义类节点没有独立 docstring 合同，不参与当前检查。
            # 非公开定义节点无需参加公开文档完整性检查。
            continue

        # 私有对象不作为公开文档质量检查对象。
        if not is_public_name(node.name):

            # 私有 helper 的文档粒度由模块内部自行约定，这里跳过。
            # 私有名称由内部实现自行约定，不强制公开文档说明。
            continue

        # 公开对象缺少 docstring 时，调用者无法快速理解用途。
        if ast.get_docstring(node) is None:

            # PG025 覆盖公开函数、异步函数和类的文档缺失。
            add_issue(
                issues,
                "PG025",
                style_issue_level(config, "PG025"),
                filepath,
                node.lineno,
                f"Public definition `{node.name}` lacks a docstring.",
            )
