"""实现 readable-python-generator 的基础可读性规则。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# AST 规则主体依赖标准库语法分析与文本匹配能力。
import ast
import re
import tokenize
from pathlib import Path

# 本地辅助模块负责 AST 父链、入口判断和 issue 登记。
from .ast_helpers import (
    IMPORT_SIDE_EFFECT_CALLS,
    PATH_ASSIGNMENT_NAMES,
    RANDOM_SEED_CALLS,
    SCRIPT_SIDE_EFFECT_CALLS,
    # 父链辅助用于区分模块导入期和函数体内代码。
    add_issue,
    ancestor_function,
    ancestor_is_main_guard,
    get_call_name,
    is_project_script_file,
    is_main_guard,
    is_public_name,
    # 导入期判断用于副作用和入口保护规则。
    node_is_at_module_import_time,
)
from .docstring_rules import check_docstring_contracts, check_public_docstrings
from .profiles import ProfileConfig
from .report import Issue

# 规则会把类型标注还原成文本，再复用已有字符串判断逻辑。
def annotation_to_text(annotation: ast.AST | None) -> str:
    """将 AST 类型标注还原成源码文本。

    参数:
        annotation: AST 中解析到的类型标注节点。

    返回:
        可比较的类型标注文本；标注缺失或无法还原时返回空字符串。
    """

    # 没有类型标注时没有可比较的源码文本。
    if annotation is None:

        # 空字符串让调用方按“无可用标注”处理。
        return ""

    # ast.unparse 能保留联合类型、泛型等复杂标注的原始结构。
    try:

        # 还原成功后，返回可复用的标注源码文本。
        return ast.unparse(annotation)

    # 某些异常 AST 片段无法反解源码时，调用方按“无可用标注”继续。
    except (AttributeError, TypeError, ValueError):

        # 还原失败时退回空字符串，避免后续文本匹配误判。
        return ""

# 可变默认参数会在函数调用之间共享状态。
def is_mutable_default(node: ast.AST) -> bool:
    """判断默认值表达式是否会生成可变对象。

    参数:
        node: 函数参数默认值对应的 AST 节点。

    返回:
        True 表示默认值会在多次调用之间共享可变状态。
    """

    # 字面量列表、字典和集合本身就是共享可变对象。
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):

        # 原生可变字面量命中后立即判定为风险默认值。
        return True

    # 构造器调用需要进一步区分是否创建出新的可变容器。
    if isinstance(node, ast.Call):

        # 读取调用名后，才能判断默认值是否来自可变容器工厂。
        str_call_name = get_call_name(node.func)  # 默认值调用的函数名

        # 标准容器构造器命中后与字面量情况等价。
        if str_call_name in {"list", "dict", "set"}:

            # 容器工厂函数会在定义时创建共享的可变对象。
            return True

        # 常见数组工厂函数也会返回可变缓冲区。
        tuple_mutable_suffixes = (".array", ".zeros", ".ones", ".empty", ".full")  # 可变数组构造器后缀

        # NumPy 风格工厂函数一旦命中，就需要回退到 None 初始化写法。
        if str_call_name.endswith(tuple_mutable_suffixes):

            # 数组工厂函数返回的也是调用间共享的可变缓冲区。
            return True

    # 其余表达式暂按不可变默认值处理。
    return False

# `check_mutable_defaults` 检查并登记mutabledefaults。
def check_mutable_defaults(tree: ast.AST, filepath: Path, issues: list[Issue]) -> None:
    """登记共享可变默认值问题。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 只需要检查函数定义节点上的默认参数表达式。
    for node in ast.walk(tree):

        # 只有函数定义节点才携带默认参数列表。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 其他 AST 节点不携带默认参数列表。
            continue

        # 先收集普通位置参数上的默认值表达式。
        list_defaults = list(node.args.defaults)  # 函数默认参数表达式列表

        # kw-only 参数里只有非 None 槽位才是真正的默认表达式。
        list_defaults.extend(default for default in node.args.kw_defaults if default is not None)

        # 任意一个默认值可变都足以触发共享状态风险。
        if any(is_mutable_default(default) for default in list_defaults):

            # 命中后登记函数级问题，提示改成 None 后在函数体内初始化。
            add_issue(
                issues,
                "PG001",
                "BLOCKER",
                filepath,
                node.lineno,
                f"Function `{node.name}` uses a mutable default; use None and initialize inside.",
            )

# `is_environment_or_path_assignment` 判断environmentor路径assignment。
def is_environment_or_path_assignment(target: ast.AST) -> bool:
    """判断赋值目标是否会改写环境变量或导入路径。

    参数:
        target: 赋值语句左侧的 AST 目标节点。

    返回:
        True 表示该赋值会修改 `os.environ`、`sys.path` 等全局运行状态。
    """

    # 下标形式通常对应 `os.environ[...]` 这类环境映射写入。
    if isinstance(target, ast.Subscript):

        # 只有命中环境映射或路径容器名时才算修改全局运行状态。
        return get_call_name(target.value) in PATH_ASSIGNMENT_NAMES

    # 属性形式主要用于识别 `sys.path.append` 之前的路径对象写入。
    if isinstance(target, ast.Attribute):

        # `sys.path` 前缀命中后说明代码正在触碰解释器导入路径。
        return get_call_name(target).startswith("sys.path")

    # 其他左值形态不会改写导入路径或环境映射。
    return False

# profile 允许导入副作用时降级为 NOTE，否则按 blocker 报告。
def side_effect_level(config: ProfileConfig) -> str:
    """返回导入期副作用问题的严重级别。

    参数:
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        `allow_import_side_effects` 启用时返回 NOTE，否则返回 BLOCKER。
    """

    # 规则级别只受 profile 的导入副作用豁免开关控制。
    return "NOTE" if config.allow_import_side_effects else "BLOCKER"

# 常规可调用对象和局部变量统一使用 snake_case。
SNAKE_CASE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")  # snake_case 名称模式

# 类型名规则允许类定义以大写字母起始。
PASCAL_CASE_NAME_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*$")  # 类和类型别名统一复用的命名模式

# 模块常量允许全大写 snake_case。
CONSTANT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")  # 模块常量名称模式

# `is_module_level_constant` 判断模块levelconstant。
def is_module_level_constant(node: ast.Assign | ast.AnnAssign) -> bool:
    """判断赋值是否是模块级常量候选。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        True 表示该赋值位于模块顶层，且至少有一个全大写目标名。
    """

    # 只有模块体直属赋值才有资格参与常量命名规则。
    if not isinstance(getattr(node, "parent", None), ast.Module):

        # 非模块顶层赋值不参与常量命名约束。
        return False

    # `Assign` 和 `AnnAssign` 的左值字段不同，这里统一成一个列表。
    list_targets = getattr(node, "targets", None) or [getattr(node, "target", None)]  # 赋值左侧目标节点集合

    # 多目标赋值里只要存在全大写名称，就按常量声明处理。
    for target in list_targets:

        # 只有名称型目标才可能表达模块级常量命名。
        if isinstance(target, ast.Name) and CONSTANT_NAME_PATTERN.fullmatch(target.id):

            # 任一目标符合常量形态时，就把整条赋值视为常量声明。
            return True

    # 没有任何常量形态目标时，不触发常量命名检查。
    return False

# `is_ast_visitor_method_name` 判断astvisitormethod名称。
def is_ast_visitor_method_name(function_name: str) -> bool:
    """判断函数名是否为 ast.NodeVisitor 分发钩子。

    参数:
        function_name: 待检查的函数名。

    返回:
        True 表示函数名符合 `visit_<AstNode>` 的 NodeVisitor 约定。
    """

    # 只有 visit_ 前缀才可能被 NodeVisitor 自动分发。
    if not function_name.startswith("visit_"):

        # 非 visitor 命名直接排除，不再继续做 AST 类型判断。
        return False

    # 后缀首字母大写时，通常对应具体 AST 节点类型名。
    str_node_type_name = function_name.removeprefix("visit_")  # visitor 目标 AST 类型名

    # 后缀合法时，该函数名就符合 NodeVisitor 的分发约定。
    return bool(str_node_type_name) and str_node_type_name[:1].isupper()

# `check_function_definition_name` 检查并登记函数definition名称。
def check_function_definition_name(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
    issues: list[Issue],
) -> None:
    """登记不符合 snake_case 的函数名。

    参数:
        node: 当前待检查的函数定义节点。
        filepath: 需要登记问题的源码文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 魔术方法和 NodeVisitor 钩子遵循 Python/AST 既定命名约定。
    if (node.name.startswith("__") and node.name.endswith("__")) or is_ast_visitor_method_name(node.name):

        # 魔术方法和 visitor 入口遵循单独约定，这里直接放行。
        return

    # 已满足 snake_case 的函数不需要再登记问题。
    if SNAKE_CASE_NAME_PATTERN.fullmatch(node.name):

        # 已满足 snake_case 的函数名无需重复报错。
        return

    # 函数名不符合规则时，登记命名问题供上层汇总。
    add_issue(
        issues,
        "PG045",
        "BLOCKER",
        filepath,
        node.lineno,
        f"Function `{node.name}` should use snake_case.",
    )

# `check_class_definition_name` 承接这一段规则检查逻辑。
def check_class_definition_name(node: ast.ClassDef, filepath: Path, issues: list[Issue]) -> None:
    """登记不符合 PascalCase 的类名。

    参数:
        node: 当前待检查的类定义节点。
        filepath: 需要登记问题的源码文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 已满足 PascalCase 的类定义不需要额外报告。
    if PASCAL_CASE_NAME_PATTERN.fullmatch(node.name):

        # 已满足 PascalCase 的类名可以直接通过。
        return

    # 类名不符合规则时，登记对应的命名问题。
    add_issue(
        issues,
        "PG045",
        "BLOCKER",
        filepath,
        node.lineno,
        f"Class `{node.name}` should use PascalCase.",
    )

# 模块常量命名检查只处理顶层赋值左值。
def check_constant_definition_name(
    node: ast.Assign | ast.AnnAssign,
    filepath: Path,
    issues: list[Issue],
) -> None:
    """检查模块级常量名是否满足全大写 snake_case。

    参数:
        node: 当前遍历到的 AST 节点。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 常量命名检查只需要遍历赋值左侧的每个简单名称。
    list_targets = getattr(node, "targets", None) or [getattr(node, "target", None)]  # 目标集合

    # 链式赋值需要逐个目标判断命名是否仍符合常量规范。
    for target in list_targets:

        # 只有简单名称目标才参与模块常量命名检查。
        if not isinstance(target, ast.Name):

            # 属性写入和下标写入不属于模块常量命名场景。
            continue

        # 已满足全大写 snake_case 的目标直接跳过。
        if CONSTANT_NAME_PATTERN.fullmatch(target.id):

            # 已符合常量样式的名称无需再登记问题。
            continue

        # 模块常量名违规时，在当前目标行登记 issue。
        add_issue(
            issues,
            "PG045",
            "BLOCKER",
            filepath,
            target.lineno,
            f"Constant `{target.id}` should use uppercase snake_case.",
        )

# 该入口统一编排函数、类和常量三类命名检查。
def check_definition_naming(tree: ast.Module, filepath: Path, issues: list[Issue]) -> None:
    """检查函数、类和常量命名形态。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 命名规则需要统一扫描函数、类和模块级常量。
    for node in ast.walk(tree):

        # 函数和异步函数优先走函数命名检查分支。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 当前节点交给函数命名规则继续检查。
            check_function_definition_name(node, filepath, issues)

            # 函数分支已经消费完当前节点，直接进入下一轮遍历。
            continue

        # 类定义单独套用 PascalCase 规则。
        if isinstance(node, ast.ClassDef):

            # 类定义节点交给类名规则继续检查。
            check_class_definition_name(node, filepath, issues)

            # 类定义分支处理完成后直接进入下一轮遍历。
            continue

        # 模块顶层常量需要按全大写命名约定检查。
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and is_module_level_constant(node):

            # 模块常量赋值节点交给常量命名规则处理。
            check_constant_definition_name(node, filepath, issues)

# `check_import_time_assignment` 检查并登记导入timeassignment。
def check_import_time_assignment(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记导入阶段修改环境或路径的赋值。

    参数:
        node: 当前待检查的赋值语句节点。
        filepath: 需要登记问题的源码文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 把不同赋值节点的左值统一成一个可遍历列表，后续才能批量判断。
    list_targets = getattr(node, "targets", None) or [getattr(node, "target", None)]  # 当前赋值语句覆盖到的左值节点

    # 命中环境映射或路径写入时，说明赋值会改变模块导入上下文。
    bool_mutates_import_state = any(  # 是否修改导入相关状态
        target is not None and is_environment_or_path_assignment(target)  # 当前左值是否命中环境写入
        for target in list_targets  # 逐个检查赋值左值
    )

    # 只有导入阶段的全局状态写入才属于本规则关注的副作用。
    if not bool_mutates_import_state or not node_is_at_module_import_time(node):

        # 结束当前检查函数，把控制权还给调用方。
        return

    # 把当前违规情况登记到 issues，供最终汇总输出。
    add_issue(
        issues,
        "PG002",
        side_effect_level(config),
        filepath,
        node.lineno,
        "Module import mutates environment or path; move it to main/config.",
    )

# `check_import_time_call` 检查并登记导入time调用。
def check_import_time_call(
    node: ast.Call,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记导入阶段触发副作用的函数调用。

    参数:
        node: 当前待检查的调用表达式节点。
        filepath: 需要登记问题的源码文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 规则只根据解析后的调用名判断是否属于已知副作用入口。
    str_call_name = get_call_name(node.func)  # 调用名称

    # 非已知副作用调用，或不在导入阶段执行的调用，都不在这里报告。
    if str_call_name not in IMPORT_SIDE_EFFECT_CALLS or not node_is_at_module_import_time(node):

        # 非白名单或非导入期调用直接跳过。
        return

    # `matplotlib.use` 单独保留更具体的提示文本。
    if str_call_name == "matplotlib.use":

        # 把 `matplotlib.use` 的后端绑定按 PG026 单独登记。
        add_issue(
            issues,
            "PG026",
            side_effect_level(config),
            filepath,
            getattr(node, "lineno", 1),
            "Matplotlib backend selection at import time couples library code to a UI backend.",
        )

        # 专门问题登记完后不再落入通用副作用提示。
        return

    # 其余白名单副作用调用统一按 PG002 记录。
    add_issue(
        issues,
        "PG002",
        side_effect_level(config),
        filepath,
        getattr(node, "lineno", 1),
        f"Import-time call `{str_call_name}` has side effects; move it to main/config.",
    )

# `is_import_fallback_try` 判断导入fallbacktry。
def is_import_fallback_try(node: ast.stmt) -> bool:
    """判断顶层 `try` 是否只是脚本直跑兼容导入。

    参数:
        node: 模块顶层语句节点。

    返回:
        True 表示 `try/except ImportError` 仅用于 import fallback。
    """

    # 只有 `try` 语句才可能承载兼容导入分支。
    if not isinstance(node, ast.Try):

        # 返回 False，表示当前分支不满足这一判定。
        return False

    # 主体里只允许出现 import 语句，才能视为纯导入回退。
    bool_body_imports = all(isinstance(item, (ast.Import, ast.ImportFrom)) for item in node.body)  # try 主体是否全是导入

    # 一旦混入其他逻辑，顶层 try 就会成为真实导入期控制流。
    if not bool_body_imports:

        # try 主体混入非导入语句后直接判定失败。
        return False

    # 每个 except 分支也必须保持“只导入、不执行别的逻辑”。
    for handler in node.handlers:

        # 命中这一条件时进入对应的规则分支。
        if not isinstance(handler.type, ast.Name) or handler.type.id != "ImportError":

            # 捕获类型不是 `ImportError` 时不能视为导入回退。
            return False

        # 回退分支混入其他语句后，就不再是纯粹的导入兼容逻辑。
        if not all(isinstance(item, (ast.Import, ast.ImportFrom)) for item in handler.body):

            # 捕获分支混入非导入语句后就放弃回退判定。
            return False

    # 满足以上条件时，可安全视为脚本直跑兼容导入。
    return True

# 模块顶层控制流需要单独识别导入期执行风险。
def check_top_level_control_flow(node: ast.stmt, filepath: Path, issues: list[Issue]) -> None:
    """登记导入阶段会直接执行的顶层控制流。

    参数:
        node: 模块顶层语句节点。
        filepath: 需要登记问题的源码文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 这些控制流一旦出现在模块顶层，就会在 import 时立即执行。
    tuple_top_level_control_types = (ast.For, ast.While, ast.If, ast.With, ast.Try)  # 顶层可执行控制流类型

    # main guard 与纯导入回退各有合理性，不在这里重复提示。
    if not isinstance(node, tuple_top_level_control_types) or is_main_guard(node) or is_import_fallback_try(node):

        # 已豁免的控制流节点在这里直接放行。
        return

    # 未豁免的顶层控制流按 PG002 发出告警。
    add_issue(
        issues,
        "PG002",
        "WARNING",
        filepath,
        getattr(node, "lineno", 1),
        "Top-level executable control flow may be an import-time side effect.",
    )

# `check_import_time_side_effects` 检查并登记导入time副作用effects。
def check_import_time_side_effects(
    tree: ast.Module,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """统一扫描导入阶段副作用。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # AST 全量遍历负责发现赋值和调用形式的副作用入口。
    for node in ast.walk(tree):

        # 赋值类节点需要走导入期赋值副作用检查。
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):

            # 继续调用 `check_import_time_assignment` 完成这一段子检查或辅助动作。
            check_import_time_assignment(node, filepath, issues, config)

        # 剩余的副作用入口主要表现为调用表达式。
        elif isinstance(node, ast.Call):

            # 调用表达式交给导入期调用规则细查。
            check_import_time_call(node, filepath, issues, config)

    # 模块顶层语句还要补查显式控制流结构。
    for node in tree.body:

        # 顶层语句再补查显式控制流副作用。
        check_top_level_control_flow(node, filepath, issues)

# 字符串字面量扫描需要排除模块、类和函数 docstring。
def iter_string_constants_without_docstrings(tree: ast.AST) -> Iterable[ast.Constant]:
    """迭代不属于 docstring 的字符串常量节点。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。

    返回:
        可用于路径、硬件和魔法字符串检查的字符串常量节点。
    """

    # 先收集 docstring 对应常量节点，避免它们被路径规则误报。
    set_docstring_node_ids: set[int] = set()  # docstring 常量节点 id 集合

    # 第一次遍历负责定位模块、类和函数体首位的 docstring 常量。
    for node in ast.walk(tree):

        # 先读取节点的直接语句体，后续才能判断是否存在 docstring 槽位。
        list_body = getattr(node, "body", None)  # 节点直接语句体

        # 空 body 或非列表 body 不可能承载 docstring。
        if not isinstance(list_body, list) or not list_body:

            # 当前节点无需继续处理，直接进入下一轮遍历。
            continue

        # docstring 只能出现在语句体首位，这里缓存首个语法节点。
        a_s_t_first_body_node: ast.AST = list_body[0]  # 语句体首个 AST 节点

        # 命中 `Expr(Constant(str))` 时，首项字符串就是 docstring 候选。
        if isinstance(a_s_t_first_body_node, ast.Expr) and isinstance(a_s_t_first_body_node.value, ast.Constant):

            # 只收集真实字符串常量，避免把其它字面量误当 docstring。
            if isinstance(a_s_t_first_body_node.value.value, str):

                # 先登记 docstring 节点，避免二次遍历重复产出。
                set_docstring_node_ids.add(id(a_s_t_first_body_node.value))

    # 第二次遍历只输出不属于 docstring 的普通字符串常量。
    for node in ast.walk(tree):

        # 第二轮只保留普通字符串常量候选。
        if isinstance(node, ast.Constant) and isinstance(node.value, str):

            # 排除 docstring 节点后再向上游产出字符串。
            if id(node) not in set_docstring_node_ids:
                yield node

# 正则和替换模板里的斜杠不应误判为文件路径。
def is_regex_or_replacement_literal(text: str) -> bool:
    """判断字符串是否更像正则或替换模板。

    参数:
        text: 待分析的字符串字面量内容。

    返回:
        True 表示文本应该跳过路径字面量检查。
    """

    # 这些符号强烈指向正则表达式或替换模板。
    tuple_regex_markers = ("^", "$", "\\s", "\\d", "\\w", "[", "]", "(", ")", "|")  # 正则表达式特征符号

    # 命中正则标记后就不再把文本当路径候选。
    if any(marker in text for marker in tuple_regex_markers):

        # 返回 True，把当前判定结果交给上层规则使用。
        return True

    # 返回当前分支的计算结果，供上层流程继续消费。
    return re.search(r"\\[1-9]", text) is not None

# 路径检查先排除正则、空白包裹和过短文本。
def path_literal_can_be_checked(text: str) -> str:
    """归一化可继续检查的路径字面量。

    参数:
        text: 当前规则正在分析的文本内容。

    返回:
        可用于路径规则判断的标准化文本；不可检查时返回空字符串。
    """

    # 正则或替换模板字符串不应被路径规则误判。
    if is_regex_or_replacement_literal(text):

        # 空字符串表示该文本不参与路径检查。
        return ""

    # Windows 分隔符归一成斜杠，后续路径规则只处理一种格式。
    str_normalized = text.replace("\\", "/")  # 统一分隔符后的文本

    # 去掉边界空白后，路径判断才能稳定比较真实文本。
    str_stripped = str_normalized.strip()  # 通过预过滤的候选路径文本

    # 前后有空白的文本不像可直接使用的路径字面量。
    if not str_stripped or str_stripped != str_normalized:

        # 带首尾空白的文本通常是展示文案，不参与路径判定。
        return ""

    # 过短路径片段和目录快捷写法不作为硬编码路径。
    if len(str_stripped) < 3 or str_stripped in {"./", "../", "~/"}:

        # 过短片段和目录快捷写法不视为稳定路径字面量。
        return ""

    # 包含空白的普通文本更可能是消息而不是路径。
    if any(character.isspace() for character in str_stripped):

        # 含空白的自然语言文本更像消息而不是文件系统路径。
        return ""

    # URL 不属于本地硬编码路径。
    if str_stripped.startswith(("http://", "https://")):

        # URL 走网络协议，不属于本地路径硬编码。
        return ""

    # 返回归一化后的可疑路径文本。
    return str_stripped

# 绝对路径识别同时覆盖 Windows 盘符和 POSIX 根路径。
def is_absolute_path_literal(original_text: str, stripped_text: str) -> bool:
    """判断字符串是否像绝对路径字面量。

    参数:
        original_text: 原始字符串字面量文本。
        stripped_text: 去掉首尾空白后的字符串文本。

    返回:
        True 表示文本具备绝对路径形态。
    """

    # Windows 盘符路径属于绝对本地路径。
    if re.match(r"^[A-Za-z]:[/\\]", original_text):

        # 盘符前缀命中后可直接判定为绝对路径。
        return True

    # 其余情况退回 Unix 根路径形态判断。
    return stripped_text.startswith("/") and len(stripped_text) > 3

# 相对路径识别只关注显式的 `./`、`../` 和 `~/` 前缀。
def is_relative_path_literal(stripped_text: str) -> bool:
    """判断字符串是否像相对路径字面量。

    参数:
        stripped_text: 去掉首尾空白后的字符串文本。

    返回:
        True 表示文本具备相对路径形态。
    """

    # 显式相对路径前缀命中后即可返回真值。
    return stripped_text.startswith(("./", "../", "~/")) and len(stripped_text) > 3

# `is_file_like_path_literal` 判断文件like路径literal。
def is_file_like_path_literal(stripped_text: str) -> bool:
    """判断字符串是否像文件或多级目录路径。

    参数:
        stripped_text: 去掉首尾空白后的字符串文本。

    返回:
        True 表示文本具备文件系统路径特征。
    """

    # 缺少路径分隔符的文本通常不是文件路径。
    if "/" not in stripped_text:

        # 单段文本先排除为文件路径候选。
        return False

    # 文件后缀命中时，单段路径也可能是具体文件名。
    file_suffix = re.search(r"\.[A-Za-z0-9]{1,8}$", stripped_text) is not None  # 路径后缀匹配结果

    # 文件后缀或多级目录命中时保留路径判定。
    return file_suffix or stripped_text.count("/") >= 2

# 路径形态由绝对路径、相对路径和文件后缀三类特征组成。
def looks_like_path(text: str) -> bool:
    """判断字符串字面量是否像文件系统路径。

    参数:
        text: 当前规则正在分析的文本内容。

    返回:
        True 表示文本具备可疑路径字面量特征。
    """

    # 先过滤掉正则、URL 和空白污染等不该进入路径规则的文本。
    str_stripped = path_literal_can_be_checked(text)  # 去掉边界空白，避免格式差异影响判断

    # 预过滤失败的文本直接跳过路径判定。
    if not str_stripped:

        # 空白字符串没有继续做路径分析的价值。
        return False

    # 绝对路径一旦命中，通常说明源码绑定了本地目录。
    if is_absolute_path_literal(text, str_stripped):

        # 绝对路径命中时立即视为硬编码路径。
        return True

    # 相对路径也应该通过配置、参数或 Path 拼装注入。
    if is_relative_path_literal(str_stripped):

        # 相对路径命中后同样视为硬编码路径。
        return True

    # 其余情况退回文件名与层级结构判定。
    return is_file_like_path_literal(str_stripped)

# 硬件绑定文本会降低脚本在不同设备上的可移植性。
def is_hardcoded_hardware_text(text: str) -> bool:
    """判断字符串是否把硬件选择直接写死在源码里。

    参数:
        text: 待分析的字符串字面量内容。

    返回:
        True 表示文本显式绑定了 GPU、CUDA 或设备序号。
    """

    # 统一转成小写后，设备关键词匹配不会受大小写影响。
    str_lowered = text.lower()  # 生成大小写无关文本，保证关键词匹配稳定

    # 分段拼接避免规则源码自身因常量文本而触发同类告警。
    str_cuda_env_name = "".join(("CU", "DA", "_VISIBLE", "_DEVICES"))  # CUDA 可见设备环境变量名

    # CUDA 可见设备环境变量表示硬件选择被写死。
    if str_cuda_env_name in text:

        # 环境变量名命中时视为设备选择被写死。
        return True

    # cuda:N 形式表示代码固定到了某个 GPU 序号。
    if re.search(r"cuda:\d+", str_lowered):

        # 显式 `cuda:N` 序号命中时也算硬件绑定。
        return True

    # 单独写设备关键词通常表示代码硬绑定运行硬件。
    set_hardcoded_device_words = {"g" + "pu", "cu" + "da"}  # 硬件后端关键词集合

    # 只有精确设备关键词才继续落入这一分支。
    if str_lowered in set_hardcoded_device_words:

        # 裸设备关键词命中后立即判为硬件绑定。
        return True

    # 其余文本暂不视为明确的硬件绑定。
    return False

# 脚本和 notebook 允许设备绑定弱一些，库代码保持 blocker。
def hardware_issue_level(config: ProfileConfig) -> str:
    """返回硬编码硬件文本问题的严重级别。

    参数:
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        script/notebook profile 返回 WARNING，其余 profile 返回 BLOCKER。
    """

    # 脚本型 profile 放宽到 warning，库型 profile 保持 blocker。
    return "WARNING" if config.name in {"script", "notebook"} else "BLOCKER"

# `check_string_constant_path_and_hardware` 检查并登记stringconstant路径andhardware。
def check_string_constant_path_and_hardware(
    node: ast.Constant,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记硬编码路径和硬件绑定字符串。

    参数:
        node: 当前待检查的字符串常量节点。
        filepath: 需要登记问题的源码文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 字符串常量内容用于路径和硬件绑定规则判断。
    str_text = node.value  # 字符串常量文本

    # 使用字符串节点自身的行号作为告警定位点。
    int_line = getattr(node, "lineno", 1)  # 问题定位行号

    # 字符串内容固定硬件设备时报告 PG003。
    if is_hardcoded_hardware_text(str_text):

        # 把硬编码设备字符串按 PG003 登记出来。
        add_issue(
            issues,
            "PG003",
            hardware_issue_level(config),
            filepath,
            int_line,
            f"Hardcoded GPU/CUDA/device setting `{str_text}` should be a parameter.",
        )

    # Path("...") 已经把字符串提升为路径对象，不再按裸字符串路径报告。
    # 只对 Path 构造器的直接参数豁免，其他字符串路径仍按 PG006 报告。
    bool_is_path_constructor_arg = (
        isinstance(getattr(node, "parent", None), ast.Call)  # 父节点必须是调用表达式
        and node in getattr(node, "parent").args  # 当前字符串必须是位置参数
        and get_call_name(getattr(node, "parent").func) in {"Path", "pathlib.Path"}  # 调用目标必须是 Path 构造器
    )  # 字符串是否已经传入 Path 构造器

    # 裸字符串路径比 `Path("...")` 更难维护和组合。
    if not config.allow_hardcoded_path and looks_like_path(str_text) and not bool_is_path_constructor_arg:

        # 把裸字符串路径按 PG006 记为可维护性风险。
        add_issue(
            issues,
            "PG006",
            "WARNING",
            filepath,
            int_line,
            f"Hardcoded path `{str_text}` should be a parameter or Path object.",
        )

# `check_configured_device_call` 检查并登记configureddevice调用。
def check_configured_device_call(
    node: ast.Call,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记通过配置接口显式写死设备名的调用。

    参数:
        node: 当前待检查的调用表达式节点。
        filepath: 需要登记问题的源码文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 先抽取调用名，再判断是否属于设备配置入口。
    str_call_name = get_call_name(node.func)  # 设备配置调用名

    # 非设备配置调用无需继续检查位置参数。
    if str_call_name not in {"set_device", "torch.device"}:

        # 不是设备选择入口时直接结束本轮检查。
        return

    # 逐个检查设备配置调用的位置参数。
    for arg in node.args:

        # 这里只分析字面量形式的设备参数。
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):

            # 参数字符串固定硬件时报告 PG003。
            if is_hardcoded_hardware_text(arg.value):

                # 把写死设备的参数字符串按 PG003 记录。
                add_issue(
                    issues,
                    "PG003",
                    hardware_issue_level(config),
                    filepath,
                    node.lineno,
                    f"Hardcoded device `{arg.value}` should be passed as configuration.",
                )

# 直接 CUDA 调用与配置式设备选择需要分开提示。
def check_cuda_call(
    node: ast.Call,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记直接触碰 CUDA 运行时的调用。

    参数:
        node: 当前待检查的调用表达式节点。
        filepath: 需要登记问题的源码文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 规则根据完整调用名识别 `torch.cuda.*` 一类硬件触碰操作。
    str_call_name = get_call_name(node.func)  # CUDA 相关调用名

    # main guard 外的 CUDA 调用会把库代码绑定到特定运行环境。
    if str_call_name.startswith("torch.cuda.") and not ancestor_is_main_guard(node):

        # 把 main guard 外的 CUDA 调用按 PG003 登记。
        add_issue(
            issues,
            "PG003",
            hardware_issue_level(config),
            filepath,
            getattr(node, "lineno", 1),
            f"CUDA call `{str_call_name}` should be isolated in a script entry point.",
        )

# 该入口把字符串字面量扫描和设备调用扫描串成一轮检查。
def check_hardcoded_path_and_hardware(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """统一扫描路径和硬件绑定问题。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 第一轮只扫描字符串常量中的路径和设备字面量。
    for node in iter_string_constants_without_docstrings(tree):

        # 先扫描字符串字面量里的路径和设备硬编码。
        check_string_constant_path_and_hardware(node, filepath, issues, config)

    # 第二轮补查直接触碰设备运行时的调用表达式。
    for node in ast.walk(tree):

        # 只有调用表达式才可能直接触碰设备运行时。
        if isinstance(node, ast.Call):

            # 先检查显式设备配置调用是否写死目标。
            check_configured_device_call(node, filepath, issues, config)

            # 再检查 `torch.cuda.*` 是否越过脚本入口边界。
            check_cuda_call(node, filepath, issues, config)

# 核心库输出副作用与脚本层输出边界在这里统一裁决。
def check_print_and_plot(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记核心代码中的终端输出和直接绘图展示。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # scripts/python 下的 CLI 工具允许直接向终端输出结果。
    if is_project_script_file(filepath):

        # 脚本工具边界不适用核心库 print/plot 副作用约束。
        return

    # 核心库代码才需要禁止 print 和 `plt.show()` 副作用。
    for node in ast.walk(tree):

        # print/plot 规则只关注调用表达式。
        if not isinstance(node, ast.Call):

            # 非调用节点与输出副作用规则无关，直接略过。
            continue

        # 先抽取调用名，再匹配终端输出和图形展示入口。
        str_call_name = get_call_name(node.func)  # 输出相关调用名

        # `print` 会把核心逻辑耦合到终端输出协议。
        if str_call_name == "print" and not config.allow_print:

            # 禁用 `print` 时在这里登记输出耦合问题。
            add_issue(
                issues,
                "PG004",
                "WARNING",
                filepath,
                getattr(node, "lineno", 1),
                "Core code should return data or use logging instead of print.",
            )

        # `plt.show()` 会让库函数强行决定展示时机和图形后端。
        if str_call_name == "plt.show" and not config.allow_plot_show:

            # 禁用 `plt.show()` 时在这里登记展示时机耦合。
            add_issue(
                issues,
                "PG005",
                "BLOCKER",
                filepath,
                getattr(node, "lineno", 1),
                "Core code should not call plt.show(); return Figure/Axes or let scripts display.",
            )

# 访问控制流节点时统计函数体内最大嵌套深度。
class NestingDepthVisitor(ast.NodeVisitor):
    """统计 if/for/while/with/try/match 的最大嵌套深度。"""

    # 初始深度为 0，每进入一个控制流节点递增。
    def __init__(self) -> None:
        """初始化嵌套深度计数器。

        参数:
            无。

        返回:
            无。构造后可直接访问 `current_depth` 与 `max_depth`。
        """

        # 当前遍历路径上的控制流嵌套层数。
        self.current_depth = 0  # 当前控制流嵌套深度

        # 遍历完成后用于和 profile 阈值比较。
        self.max_depth = 0  # 已观察到的最大嵌套深度

    # 条件分支节点进入嵌套深度统计。
    def visit_If(self, node: ast.If) -> None:
        """把 `if` 分支计入嵌套深度。

        参数:
            node: 当前访问到的 `if` 节点。

        返回:
            无。深度统计结果保存在访问器实例上。
        """

        # `for` 循环进入后继续复用统一的深度统计逻辑。
        self.visit_nested_node(node)

    # 计数循环节点进入嵌套深度统计。
    def visit_For(self, node: ast.For) -> None:
        """把 `for` 循环计入嵌套深度。

        参数:
            node: 当前访问到的 `for` 节点。

        返回:
            无。深度统计结果保存在访问器实例上。
        """

        # `for` 节点命中后，递归逻辑继续统计其内部嵌套深度。
        self.visit_nested_node(node)

    # 条件循环节点进入嵌套深度统计。
    def visit_While(self, node: ast.While) -> None:
        """把 `while` 循环计入嵌套深度。

        参数:
            node: 当前访问到的 `while` 节点。

        返回:
            无。深度统计结果保存在访问器实例上。
        """

        # `while` 结构继续递归统计其内部嵌套。
        self.visit_nested_node(node)

    # 上下文管理节点进入嵌套深度统计。
    def visit_With(self, node: ast.With) -> None:
        """把 `with` 资源块计入嵌套深度。

        参数:
            node: 当前访问到的 `with` 节点。

        返回:
            无。深度统计结果保存在访问器实例上。
        """

        # `with` 资源块把资源获取与清理路径也算进控制流层级。
        self.visit_nested_node(node)

    # try 节点进入嵌套深度统计。
    def visit_Try(self, node: ast.Try) -> None:
        """把 `try` 结构计入嵌套深度。

        参数:
            node: 当前访问到的 `try` 节点。

        返回:
            无。深度统计结果保存在访问器实例上。
        """

        # `try` 结构需要把异常分支一并计入嵌套深度。
        self.visit_nested_node(node)

    # 模式匹配语句也会增加函数的控制流层级。
    def visit_Match(self, node: ast.Match) -> None:
        """把 `match` 分派结构计入嵌套深度。

        参数:
            node: 当前访问到的 `match` 节点。

        返回:
            无。深度统计结果保存在访问器实例上。
        """

        # 继续下钻嵌套节点，统计当前函数的控制流深度。
        self.visit_nested_node(node)

    # 所有受控节点共用同一套深度递增和回退逻辑。
    def visit_nested_node(self, node: ast.AST) -> None:
        """在进入一个控制流节点时更新深度统计。

        参数:
            node: 当前遍历到的 AST 节点。

        返回:
            无。深度统计结果保存在访问器实例上。
        """

        # 进入当前控制流节点后，子节点比父层更深一层。
        self.current_depth += 1  # 进入节点后的嵌套深度

        # 最大值记录函数体最深的控制流路径。
        self.max_depth = max(self.max_depth, self.current_depth)  # 当前函数最大嵌套深度

        # 子节点会在更深一层继续更新统计值。
        self.generic_visit(node)

        # 离开当前控制流节点时恢复父层深度。
        self.current_depth -= 1  # 回退后的嵌套深度

# 函数长度按 AST 覆盖到的真实代码行计数。
def count_function_code_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """统计函数中实际 AST 节点覆盖的源码行数。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        函数体内 AST 节点覆盖的去重源码行数。
    """

    # 去重行号避免多节点共享一行时重复计数。
    set_code_lines: set[int] = set()  # 函数 AST 覆盖的源码行号集合

    # 扫描 AST 节点，寻找本规则关注的语法结构。
    for child in ast.walk(node):

        # 只有带 lineno 的语法节点才代表真实源码行。
        int_start_line = getattr(child, "lineno", None)  # AST 节点起始源码行

        # 没有源码行号的 AST 子节点不计入函数长度。
        if int_start_line is None:

            # 没有源码行号的辅助节点不参与长度统计。
            continue

        # 登记语法节点自身所在的真实代码行
        set_code_lines.add(int_start_line)

    # 当前集合大小就是函数真实覆盖的代码行数。
    return len(set_code_lines)

# `check_function_line_count` 检查并登记函数源码行数量。
def check_function_line_count(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记超出长度阈值的函数。

    参数:
        node: 当前遍历到的 AST 节点。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 先按 AST 覆盖行数统计函数的真实代码规模。
    int_function_lines = count_function_code_lines(node)  # 整数函数源码行序列

    # 超过 blocker 阈值时，函数已经需要拆分职责。
    if int_function_lines > config.block_function_lines:

        # 函数行数超过 blocker 阈值时按 PG009 登记。
        add_issue(
            issues,
            "PG009",
            "BLOCKER",
            filepath,
            node.lineno,
            f"Function `{node.name}` has {int_function_lines} lines; split responsibilities.",
        )

    # 介于 warning 与 blocker 之间时，先提醒尽早拆分。
    elif int_function_lines > config.warn_function_lines:

        # 函数行数进入 warning 区间时先提示拆分。
        add_issue(
            issues,
            "PG009",
            "WARNING",
            filepath,
            node.lineno,
            f"Function `{node.name}` has {int_function_lines} lines; consider splitting it.",
        )

# `check_function_nesting_depth` 检查并登记函数nesting深度。
def check_function_nesting_depth(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记控制流嵌套过深的函数。

    参数:
        node: 当前遍历到的 AST 节点。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 访问器会累计当前函数里最深的控制流嵌套层级。
    nesting_depth_visitor_nesting_depth_visitor: NestingDepthVisitor = NestingDepthVisitor()  # 嵌套深度统计访问器

    # 遍历函数体后即可直接读取统计出的最大深度。
    nesting_depth_visitor_nesting_depth_visitor.visit(node)

    # 读取访问器记录的最大深度后，再和 profile 阈值比较。
    int_max_depth = nesting_depth_visitor_nesting_depth_visitor.max_depth  # 当前函数观察到的最深控制流层级

    # 超过 blocker 阈值说明流程需要被拆成更平坦的步骤。
    if int_max_depth > config.block_nested_depth:

        # 嵌套深度超过 blocker 阈值时按 PG010 登记。
        add_issue(
            issues,
            "PG010",
            "BLOCKER",
            filepath,
            node.lineno,
            f"Function `{node.name}` nesting depth is {int_max_depth}; simplify flow.",
        )

    # 先给出 warning，帮助在进一步膨胀前重构控制流。
    elif int_max_depth > config.warn_nested_depth:

        # 嵌套深度进入 warning 区间时先提示控制流展开。
        add_issue(
            issues,
            "PG010",
            "WARNING",
            filepath,
            node.lineno,
            f"Function `{node.name}` nesting depth is {int_max_depth}; readability is reduced.",
        )

# `check_function_branch_count` 检查并登记函数分支数量。
def check_function_branch_count(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记分支和循环数量过多的函数。

    参数:
        node: 当前遍历到的 AST 节点。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 分支统计覆盖条件、循环、异常分派和布尔短路节点。
    tuple_branch_nodes = (ast.If, ast.For, ast.While, ast.Try, ast.BoolOp, ast.Match)  # 参与复杂度统计的分支节点类型

    # AST 计数结果近似反映函数需要读者维护的路径数量。
    int_branch_count = sum(1 for child in ast.walk(node) if isinstance(child, tuple_branch_nodes))  # 分支数量

    # 超过 blocker 阈值时，函数应拆成更少路径的子步骤。
    if int_branch_count > config.block_branch_count:

        # 分支数量超过 blocker 阈值时按 PG011 登记。
        add_issue(
            issues,
            "PG011",
            "BLOCKER",
            filepath,
            node.lineno,
            f"Function `{node.name}` has {int_branch_count} branches/loops; split it.",
        )

    # warning 阈值用于尽早提示路径数量正在失控。
    elif int_branch_count > config.warn_branch_count:

        # 分支数量进入 warning 区间时先提示继续拆分。
        add_issue(
            issues,
            "PG011",
            "WARNING",
            filepath,
            node.lineno,
            f"Function `{node.name}` has {int_branch_count} branches/loops; consider splitting.",
        )

# `check_function_parameter_count` 检查并登记函数parameter数量。
def check_function_parameter_count(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """登记参数数量过多的函数。

    参数:
        node: 当前遍历到的 AST 节点。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 参数个数直接反映调用契约是否已经过于膨胀。
    int_parameter_count = count_parameters(node)  # 对外参数数量

    # 参数数量超过 blocker 阈值时要求配置对象。
    if int_parameter_count > config.block_parameter_count:

        # 参数规模超过 blocker 阈值时要求引入配置对象。
        add_issue(
            issues,
            "PG024",
            "BLOCKER",
            filepath,
            node.lineno,
            f"Function `{node.name}` has {int_parameter_count} parameters; use a config object.",
        )

    # 先在 warning 阶段提示调用协议已经开始变重。
    elif int_parameter_count > config.warn_parameter_count:

        # 参数规模进入 warning 区间时先提示调用协议变重。
        add_issue(
            issues,
            "PG024",
            "WARNING",
            filepath,
            node.lineno,
            f"Function `{node.name}` has {int_parameter_count} parameters; consider a config object.",
        )

# `check_function_size_and_complexity` 检查并登记函数sizeandcomplexity。
def check_function_size_and_complexity(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """统一扫描函数长度、嵌套、分支和参数复杂度。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 只需要遍历函数定义节点，再把复杂度子规则串起来执行。
    for node in ast.walk(tree):

        # 复杂度规则只对函数和异步函数定义生效。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 非函数定义节点不参与复杂度规则分发。
            continue

        # 先检查函数源码行数是否超出阈值。
        check_function_line_count(node, filepath, issues, config)

        # 再检查控制流嵌套是否已经过深。
        check_function_nesting_depth(node, filepath, issues, config)

        # 然后检查分支与循环数量是否过多。
        check_function_branch_count(node, filepath, issues, config)

        # 最后检查公开参数数量是否失控。
        check_function_parameter_count(node, filepath, issues, config)

# 参数规模统计排除 self/cls，但包含 *args 和 **kwargs。
def count_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """统计函数公开签名中的参数数量。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        排除 self/cls 后的普通参数、关键字参数和可变参数总数。
    """

    # 先拼出所有显式声明的参数节点，再统一扣掉接收器参数。
    list_args = []  # 函数签名参数节点

    # 位置专用参数会出现在签名前段，也要计入复杂度。
    list_args.extend(node.args.posonlyargs)

    # 普通位置参数是公开调用契约的主体。
    list_args.extend(node.args.args)

    # 关键字专用参数同样会增加调用协议复杂度。
    list_args.extend(node.args.kwonlyargs)

    # self/cls 不计入对外调用参数复杂度。
    int_count = sum(1 for arg in list_args if arg.arg not in {"self", "cls"})  # 公开参数数量

    # 存在 *args 时额外计入一个参数入口。
    if node.args.vararg is not None:

        # *args 属于调用方可传入的额外参数入口。
        int_count += 1  # 计入 varargs 参数

    # `**kwargs` 会进一步放宽调用面，同样应计入复杂度。
    if node.args.kwarg is not None:

        # 关键字可变参数会把额外输入责任暴露给调用方。
        int_count += 1  # 把 kwargs 通道计入公开参数规模

    # 返回排除接收器后的公开参数总数。
    return int_count

# `is_solver_like_function_name` 判断求解器like函数名称。
def is_solver_like_function_name(function_name: str) -> bool:
    """判断函数名是否像求解器或仿真入口。

    参数:
        function_name: 待检查的函数名。

    返回:
        True 表示名称暗示该函数承担求解、计算或运行入口职责。
    """

    # 求解器关键词匹配不区分函数名大小写。
    str_lower_name = function_name.lower()  # 小写函数名

    # 这些关键词通常表示科研求解或仿真入口。
    tuple_keywords = ("solver", "run", "schrodinger", "solve", "simulate", "cal", "compute")  # 求解器函数名关键词

    # 命中任一关键词后，就按 solver 风格函数继续做配置表检查。
    return any(keyword in str_lower_name for keyword in tuple_keywords)

# `is_large_experiment_dict` 判断largeexperiment映射。
def is_large_experiment_dict(node: ast.Dict) -> bool:
    """判断字典是否像内嵌的大型实验参数表。

    参数:
        node: 待检查的字典字面量节点。

    返回:
        True 表示该字典更适合作为独立配置而不是内嵌在函数里。
    """

    # 大型实验配置通常表现为大量字面量键。
    int_typed_key_count = sum(  # 字面量配置键数量
        1  # 单个字面量键贡献一个计数单位
        for key in node.keys  # 逐个统计字典键
        if isinstance(key, ast.Constant) and isinstance(key.value, (int, float, str))  # 只计算静态字面量键
    )

    # 同时满足规模和字面量键数量门槛时，才按大型配置表处理。
    return len(node.keys) >= 6 and int_typed_key_count >= 4

# `check_experiment_config_inside_solver` 检查并登记experiment规则配置inside求解器。
def check_experiment_config_inside_solver(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
) -> None:
    """登记求解器函数内部塞入大型实验配置表的情况。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无。问题会直接追加到 `issues`。
    """

    # 先定位名字看起来像求解器或仿真入口的函数。
    for node in ast.walk(tree):

        # solver 参数表规则只检查函数和异步函数。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 非函数定义节点不参与 solver 参数表检查。
            continue

        # 非 solver 命名的函数不检查参数表混入问题。
        if not is_solver_like_function_name(node.name):

            # 非 solver 命名函数直接跳过大参数表扫描。
            continue

        # 再在函数体内部查找是否混入大型字典参数表。
        for child in ast.walk(node):

            # 只对字典字面量继续检查实验参数表规模。
            if isinstance(child, ast.Dict) and is_large_experiment_dict(child):

                # 把 solver 内嵌的大参数表按 PG013 发出告警。
                add_issue(
                    issues,
                    "PG013",
                    "WARNING",
                    filepath,
                    getattr(child, "lineno", node.lineno),
                    "Solver function contains a large experiment parameter table; move it to config.",
                )
