"""承载基础规则中的原始文本、比较和配置类检查。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import ast
import re
from pathlib import Path

# 导入当前模块运行所需的依赖
from .ast_helpers import COMMENTED_CODE_PATTERNS, add_issue, get_call_name
from .profiles import ProfileConfig
from .report import Issue

# 行长度规则只度量可执行源码，不把右侧说明注释计入主体长度。
def effective_line_length(line: str) -> int:
    """计算去除右侧说明注释后的代码主体长度。

    参数:
        line: 当前规则正在解析的单行文本。

    返回:
        有源码主体时返回主体长度；整行是注释或空白时返回原始行长度。
    """

    # 去掉注释尾巴，保留换行前真正参与长度检查的源码片段。
    str_code_part = line.split("#", 1)[0].rstrip()  # 注释前源码主体

    # 存在源码主体时按主体长度判断，避免中文尾注误报 PG020。
    if str_code_part:

        # 返回行长度规则实际使用的字符数。
        return len(str_code_part)

    # 纯注释行仍保留原始长度，让超长说明块可以被发现。
    return len(line)

# `check_raw_text` 检查并登记原始值文本。
def check_raw_text(
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],
) -> None:
    """检查 raw_text 对应的质量门约束。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。

    返回:
        无；发现的问题会直接追加到 issues 列表。
    """

    # 记录当前连续注释块的起始行，供超长注释块告警定位。
    int_comment_block_start = 0  # 连续注释块起始行号

    # 累计当前连续注释块长度，遇到代码行时再统一结算。
    int_comment_block_len = 0  # 连续注释块累计行数

    # 按行扫描源码，定位空行、注释和语句边界。
    for int_index, str_line in enumerate(lines, start=1):

        # 去除stripped两端空白，避免格式差异影响规则判断。
        str_stripped = str_line.strip()  # 去掉边界空白，避免格式差异影响判断

        # 计算源码行位置，定位相邻语句和注释。
        int_line_length = effective_line_length(str_line)  # 整数源码行length

        # 长行保留为治理 backlog，避免在本轮注释治理中引入大规模表达式重排。
        if int_line_length > config.block_line_length and not str_stripped.startswith("#"):

            # 记录超过阻断阈值的长行问题。
            add_issue(
                issues,
                "PG020",
                "WARNING",
                filepath,
                int_index,
                f"Line has {int_line_length} characters; split into named intermediate values.",
            )

        # 超过提醒阈值但未到阻断阈值时给出 WARNING。
        elif int_line_length > config.warn_line_length and not str_stripped.startswith("#"):

            # 记录超过提醒阈值但仍可继续执行的长行问题。
            add_issue(
                issues,
                "PG020",
                "WARNING",
                filepath,
                int_index,
                f"Line has {int_line_length} characters; consider splitting it.",
            )

        # 注释掉的代码容易变成失效分支，提示删除或移入文档。
        if any(re.search(pattern, str_stripped) for pattern in COMMENTED_CODE_PATTERNS):

            # 记录疑似把代码留在注释中的可维护性问题。
            add_issue(
                issues,
                "PG007",
                "WARNING",
                filepath,
                int_index,
                "Comment appears to contain disabled code; delete it or move it to docs.",
            )

        # 连续注释块需要累计长度，防止源码里塞入长篇说明。
        if str_stripped.startswith("#"):

            # 新注释块从第一行注释开始定位。
            if int_comment_block_len == 0:

                # 起始行用于 PG021 报告定位。
                int_comment_block_start = int_index  # 首行注释对应的源码行号

            # 注释块长度逐行累计，遇到代码行再结算。
            int_comment_block_len += 1  # 注释文本、代码块

        # 遇到代码行时先结算上一段连续注释块。
        else:

            # 对刚刚结束的连续注释块执行长度检查。
            flush_comment_block(issues, filepath, int_comment_block_start, int_comment_block_len, config)

            # 重置长度，等待下一段注释块开始。
            int_comment_block_len = 0  # 准备统计下一段连续注释块

    # 文件结尾没有代码行收尾时，也要补做最后一次注释块检查。
    flush_comment_block(issues, filepath, int_comment_block_start, int_comment_block_len, config)

# `flush_comment_block` 处理注释代码块。
def flush_comment_block(
    issues: list[Issue],
    filepath: Path,
    start_line: int,
    length: int,
    config: ProfileConfig,
) -> None:
    """flushcommentblock：执行质量门所需的具体检查。

    参数:
        issues: 用于累计当前质量门发现项的列表。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        start_line: 源码中用于定位检查问题的行号或行文本。
        length: 长度阈值
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无；需要报告时直接把问题写入 issues。
    """

    # 超过注释块阈值时报告 PG021。
    if length > config.block_comment_block:

        # 记录达到阻断级别的超长注释块。
        add_issue(
            issues,
            "PG007",
            "BLOCKER",
            filepath,
            start_line,
            f"Comment block exceeds {config.block_comment_block} lines.",
        )

    # 判断当前规则条件是否需要进入该分支
    elif length > config.warn_comment_block:

        # 记录达到提醒级别但未阻断的注释块问题。
        add_issue(
            issues,
            "PG007",
            "WARNING",
            filepath,
            start_line,
            f"Comment block exceeds {config.warn_comment_block} lines.",
        )

# bool 是 int 的子类，比较规则需要单独识别 True/False。
def is_boolean_constant(node: ast.AST) -> bool:
    """判断 boolean_constant 是否满足规则要求。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        True 表示节点是布尔字面量。
    """

    # ast.Constant 中的布尔值是 PG027 关注的唯一字面量。
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)

# `check_boolean_literal_comparison` 检查并登记booleanliteralcomparison。
def check_boolean_literal_comparison(tree: ast.AST, filepath: Path, issues: list[Issue]) -> None:
    """检查 boolean_literal_comparison 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；命中显式布尔字面量比较时直接登记问题。
    """

    # 扫描 AST 节点，寻找本规则关注的语法结构。
    for node in ast.walk(tree):

        # 只有比较表达式可能出现 `x == True` 这类写法。
        if not isinstance(node, ast.Compare):

            # 非比较节点不可能出现显式布尔字面量比较。
            continue

        # 保存 AST 节点引用，读取语法结构专属字段。
        list_compared_nodes = [node.left, *node.comparators]  # 当前比较表达式涉及的左右两侧节点

        # 没有布尔常量时不属于显式布尔比较。
        if not any(is_boolean_constant(item) for item in list_compared_nodes):

            # 没有 True/False 字面量时无需触发该条可读性建议。
            continue

        # 把规则允许关注的比较运算符收拢成元组，便于统一复用。
        tuple_boolean_operator_types = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)  # 显式布尔比较关注的运算符类型

        # 默认当前比较尚未命中显式布尔比较反模式运算符。
        bool_uses_boolean_operator = False  # 当前比较是否使用显式布尔比较运算符

        # 顺序检查每个比较运算符，命中后即可停止扫描。
        for operator in node.ops:

            # 只要出现目标运算符，就说明当前比较属于反模式候选。
            if isinstance(operator, tuple_boolean_operator_types):

                # 发现目标运算符后，把当前比较标记为显式布尔比较反模式。
                bool_uses_boolean_operator = True  # 显式布尔比较反模式已命中

                # 命中后无需继续检查剩余运算符。
                break

        # 其他比较运算符不属于布尔常量反模式。
        if not bool_uses_boolean_operator:

            # 排除大小比较等非布尔可读性规则关心的写法。
            continue

        # 记录显式把表达式与 True/False 比较的可读性问题。
        add_issue(
            issues,
            "PG027",
            "WARNING",
            filepath,
            getattr(node, "lineno", 1),
            "Avoid comparing with True/False; use `if flag:` or `if not flag:` for readability.",
        )

# `is_type_call` 判断类型调用。
def is_type_call(node: ast.AST) -> bool:
    """判断 type_call 是否满足规则要求。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        布尔值，表示判断类型call是否成立。
    """

    # 只把内建 type(...) 调用视为直接类型比较模式的一部分。
    return isinstance(node, ast.Call) and get_call_name(node.func) == "type"

# `check_direct_type_comparison` 检查并登记direct类型comparison。
def check_direct_type_comparison(tree: ast.AST, filepath: Path, issues: list[Issue]) -> None:
    """检查 direct_type_comparison 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；命中 type(...) 直接比较时直接登记问题。
    """

    # 遍历所有比较表达式，识别 `type(x) == T` 一类反模式。
    for node in ast.walk(tree):

        # 先过滤出真正的比较表达式，避免无关节点进入后续规则判断。
        if not isinstance(node, ast.Compare):

            # 非比较节点不可能形成直接类型比较。
            continue

        # 先收集当前比较的所有参与节点，后面再统一检查其中是否出现 type(...)。
        list_compared_nodes = [node.left, *node.comparators]  # 本次比较涉及的全部左右两侧节点

        # 缺少 type(...) 调用时不属于当前规则关注的反模式。
        if not any(is_type_call(item) for item in list_compared_nodes):

            # 没有 type(...) 时，这条可读性规则不适用。
            continue

        # 直接类型比较只关心相等与身份判断这几类运算符。
        tuple_type_operator_types = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)  # 直接类型比较关注的运算符类型

        # 默认当前比较尚未命中直接类型比较反模式运算符。
        bool_uses_boolean_operator = False  # 当前比较是否使用直接类型比较运算符

        # 逐个查看 type(...) 比较里的运算符，直到找到目标集合。
        for operator in node.ops:

            # 只要出现目标运算符，就说明当前比较属于直接类型比较反模式。
            if isinstance(operator, tuple_type_operator_types):

                # 发现目标运算符后，把当前比较标记为直接类型比较反模式。
                bool_uses_boolean_operator = True  # 直接类型比较反模式已命中

                # 已经确认命中后，立刻结束当前运算符扫描。
                break

        # 其他比较运算符不属于 type(...) 直接比较。
        if not bool_uses_boolean_operator:

            # 跳过不属于该规则覆盖面的比较操作符。
            continue

        # 记录直接拿 type(...) 与类型对象比较的可读性问题。
        add_issue(
            issues,
            "PG028",
            "WARNING",
            filepath,
            getattr(node, "lineno", 1),
            "Use isinstance(...) instead of comparing type(...) directly.",
        )

# 已使用 dataclass 的类不应再按手写配置容器提示重构。
def class_has_dataclass_decorator(node: ast.ClassDef) -> bool:
    """判断类定义是否已经使用 dataclass 装饰器。

    参数:
        node: 需要检查装饰器列表的类定义节点。

    返回:
        True 表示装饰器链中存在 dataclass 或模块限定 dataclass。
    """

    # 类装饰器逐项解析，兼容 dataclass 与 dataclasses.dataclass。
    for decorator in node.decorator_list:

        # 保留模块限定前缀，兼容 dataclasses.dataclass 这类写法。
        str_decorator_name = get_call_name(decorator)  # 装饰器点分名称

        # 任意 dataclass 装饰器命中后即可跳过 PG029 建议。
        if str_decorator_name.endswith("dataclass"):

            # 调用方据此跳过配置容器重构提示。
            return True

    # 没有 dataclass 装饰器时，调用方继续检查手写字段数量。
    return False

# self 属性赋值数量用于识别手写配置容器。
def count_self_assignments(init_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """统计 __init__ 中写入 self 属性的次数。

    参数:
        init_node: 需要分析的 __init__ 函数节点。

    返回:
        __init__ 内直接 self 属性赋值的数量。
    """

    # PG029 使用该计数判断类是否像手写配置容器。
    int_count = 0  # self 属性赋值数量

    # 遍历 __init__ 的所有子节点，统计真正写入实例字段的赋值。
    for child in ast.walk(init_node):

        # 只有赋值语句可能写入 self 配置字段。
        if not isinstance(child, (ast.Assign, ast.AnnAssign)):

            # 仅赋值节点才可能新增实例字段。
            continue

        # Assign 可能有多个左侧目标，AnnAssign 只有单个 target。
        list_targets = getattr(child, "targets", None) or [getattr(child, "target", None)]  # 赋值左侧目标节点集合

        # Assign 的多个目标和 AnnAssign 的单目标统一处理。
        for target in list_targets:

            # 只有 self.xxx 才表示构造函数写入实例配置字段。
            if is_self_attribute_assignment(target):

                # 命中一次 self.<field> 赋值就累加一次字段计数。
                int_count += 1  # 新增一个已确认的实例字段赋值

    # 调用方用该数量和类名后缀共同决定是否报告 PG029。
    return int_count

# 只有 self.xxx 左侧目标才计入配置容器字段数量。
def is_self_attribute_assignment(target: ast.AST | None) -> bool:
    """判断赋值目标是否为 self 属性。

    参数:
        target: 赋值语句左侧的候选目标节点。

    返回:
        True 表示目标形如 self.<field>。
    """

    # 赋值左侧必须是属性访问才可能形如 self.xxx。
    if not isinstance(target, ast.Attribute):

        # 普通名称、下标或解包目标都不是实例字段写入。
        return False

    # 属性宿主必须是名称节点，才能判断是否为 self。
    if not isinstance(target.value, ast.Name):

        # 复杂表达式宿主不计入配置字段。
        return False

    # 只有 self.<field> 形式才计入配置容器字段数量。
    return target.value.id == "self"

# 配置容器识别只分析类体内直接定义的 __init__ 方法。
def find_init_method(node: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """查找类体内直接定义的 __init__ 方法。

    参数:
        node: 需要扫描类体成员的类定义节点。

    返回:
        找到的同步或异步 __init__ 函数节点；没有时返回 None。
    """

    # 只查找类体直接成员，避免把嵌套函数误当构造函数。
    for child in node.body:

        # 先判断当前类体成员是否就是构造函数。
        bool_is_init_function = (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))  # 当前成员是否是函数定义
            and child.name == "__init__"  # 当前函数名是否为构造函数
        )

        # 找到构造函数后即可结束类体扫描。
        if bool_is_init_function:

            # 返回类体中直接定义的构造函数节点。
            return child

    # 结束当前不可继续验证的错误路径
    return None

# `check_config_class_candidate` 检查并登记规则配置类candidate。
def check_config_class_candidate(tree: ast.AST, filepath: Path, issues: list[Issue]) -> None:
    """检查 config_class_candidate 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；命中配置容器特征时直接登记问题。
    """

    # 这些类名后缀即使字段较少，也通常表达配置容器语义。
    tuple_config_suffixes = ("Params", "Config", "Settings", "Options")  # 配置容器类名后缀

    # 遍历类定义，识别适合迁移为 dataclass 的手写配置容器。
    for node in ast.walk(tree):

        # PG029 只检查类定义是否应改为 dataclass。
        if not isinstance(node, ast.ClassDef):

            # 该规则仅对类定义节点生效。
            continue

        # 已经使用 dataclass 的类不需要重复提示。
        if class_has_dataclass_decorator(node):

            # 已显式声明 dataclass 的类不再重复建议。
            continue

        # 没有构造函数时无法根据实例字段数量判断配置容器。
        func_init_method = find_init_method(node)  # 类体内 __init__ 方法节点

        # 没有构造函数时无法统计实例字段写入。
        if func_init_method is None:

            # 缺少 __init__ 时无法可靠判断其是否是配置容器。
            continue

        # 构造函数写入多个实例字段时，类更可能适合 dataclass。
        int_self_assignment_count = count_self_assignments(func_init_method)  # __init__ 写入 self 属性数量

        # 类名已经表明配置语义时，字段数量阈值可以更宽松。
        bool_name_suggests_config = node.name.endswith(tuple_config_suffixes)  # 类名是否显式表示配置容器

        # 字段较少且类名不像配置容器时不提示 dataclass。
        if int_self_assignment_count < 5 and not bool_name_suggests_config:

            # 字段数量和命名信号都不足时，避免误报普通业务类。
            continue

        # 记录更适合迁移为 dataclass 的配置容器候选类。
        add_issue(
            issues,
            "PG029",
            "WARNING",
            filepath,
            node.lineno,
            f"Class `{node.name}` looks like a configuration container; prefer @dataclass.",
        )
