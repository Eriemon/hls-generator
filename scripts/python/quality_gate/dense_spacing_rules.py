"""承载 current-project 风格下的 docstring 与密集代码间距检查。"""

# 启用后续类型标注所需的解释器特性。
from __future__ import annotations

# 导入 AST 解析与路径类型。
import ast
from pathlib import Path

# 导入质量门登记所需的数据结构与工具。
from .ast_helpers import add_issue
from .profiles import ProfileConfig
from .report import Issue

# 根据 strict_project_style 选择风格类问题的严重级别。
def style_issue_level(config: ProfileConfig) -> str:
    """返回当前风格问题应使用的严重级别。

    参数:
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        strict_project_style 开启时返回 ``BLOCKER``，否则返回 ``WARNING``。
    """

    # 严格项目风格会把可读性问题提升为阻断项。
    return "BLOCKER" if config.strict_project_style else "WARNING"

# 判断单行源码是否属于需要计入密集代码统计的语句行。
def is_code_line(line: str) -> bool:
    """判断单行源码是否应计入连续代码长度。

    参数:
        line: 当前规则正在解析的单行文本。

    返回:
        True 表示该行是非空且非注释的代码行。
    """

    # 先移除首尾空白，便于统一识别空行与注释行。
    str_stripped_line = line.strip()  # 去除首尾空白后的源码行

    # 只有真正的可执行语句行才会增加密集代码长度。
    return bool(str_stripped_line) and not str_stripped_line.startswith("#")

# docstring 覆盖行需要从密集代码统计中剔除。
def collect_docstring_lines(tree: ast.AST) -> set[int]:
    """收集 Python 认可的 docstring 覆盖行号。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。

    返回:
        模块、类和函数 docstring 占用的一基源码行号集合。
    """

    # 聚合所有需要从密集代码统计中排除的 docstring 行。
    set_docstring_lines: set[int] = set()  # docstring 覆盖的一基源码行号

    # 扫描 AST 节点，寻找可能承载 docstring 的语法作用域。
    for node in ast.walk(tree):

        # docstring 只可能出现在带有 body 的模块、类或函数节点中。
        list_body = getattr(node, "body", None)  # 当前节点的直接语句体

        # 没有语句体的节点不可能拥有 Python 认可的 docstring。
        if not isinstance(list_body, list) or not list_body:

            # 当前节点没有首语句可供判断，因此直接跳到下一个作用域。
            continue

        # 语句体的首个节点才有资格被 Python 视为 docstring。
        a_s_t_ast_first_statement_node: ast.AST = list_body[0]  # 语句体首个 AST 节点

        # 仅首个字符串表达式才构成真正的 docstring。
        bool_is_docstring = (  # 当前首语句是否为规范 docstring
            isinstance(a_s_t_ast_first_statement_node, ast.Expr)  # 首语句必须是表达式节点
            and isinstance(a_s_t_ast_first_statement_node.value, ast.Constant)  # 表达式值必须是常量
            and isinstance(a_s_t_ast_first_statement_node.value.value, str)  # 常量内容必须是字符串
        )

        # 首语句不是字符串时，这个作用域就没有规范 docstring。
        if not bool_is_docstring:

            # 非字符串首语句不会贡献任何 docstring 覆盖行号。
            continue

        # 记录 docstring 的起始行，用于回映源码区段。
        int_start_line = getattr(a_s_t_ast_first_statement_node, "lineno", 0)  # docstring 起始行号

        # 多行 docstring 的结束行也需要一起排除。
        int_end_line = getattr(a_s_t_ast_first_statement_node, "end_lineno", int_start_line)  # docstring 结束行号

        # 缺失有效行号时无法安全登记源码覆盖范围。
        if int_start_line <= 0:

            # 无法映射回源码位置的 docstring 不能安全加入排除集合。
            continue

        # 把当前 docstring 覆盖的整段源码行并入排除集合。
        set_docstring_lines.update(range(int_start_line, int_end_line + 1))

    # 返回聚合后的 docstring 行号集合，供后续密集代码判断复用。
    return set_docstring_lines

# 多行字符串字面量同样不应被算作连续代码。
def collect_string_literal_lines(tree: ast.AST) -> set[int]:
    """收集字符串字面量覆盖行号。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。

    返回:
        所有字符串常量占用的一基源码行号集合。
    """

    # 聚合所有字符串字面量覆盖的源码行号。
    set_string_lines: set[int] = set()  # 字符串字面量覆盖的一基源码行号

    # 扫描 AST 节点，定位字符串常量对应的源码区段。
    for node in ast.walk(tree):

        # 只有字符串常量节点才可能形成需要排除的文本块。
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):

            # 数字或其他常量不会制造跨行文本块，继续检查下一个节点。
            continue

        # 记录字符串字面量的起始行。
        int_start_line = getattr(node, "lineno", 0)  # 字符串字面量起始行号

        # 多行字符串的结束行也要从代码密度统计中排除。
        int_end_line = getattr(node, "end_lineno", int_start_line)  # 字符串字面量结束行号

        # 缺失有效行号时不登记该字符串区段，避免误标源码。
        if int_start_line <= 0:

            # 没有源码行号的字符串常量不能参与逐行排除。
            continue

        # 把当前字符串字面量覆盖的每一行并入排除集合。
        set_string_lines.update(range(int_start_line, int_end_line + 1))

    # 返回聚合后的字符串字面量行号集合，供密集代码扫描使用。
    return set_string_lines

# 连续代码行超过阈值时，在片段起点登记 PG034。
def flush_dense_code_run(
    issues: list[Issue],
    filepath: Path,
    config: ProfileConfig,
    run_start_line: int,
    run_length: int,
) -> None:
    """检查并报告一个连续代码行片段。

    参数:
        issues: 用于累计当前质量门发现项的列表。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        config: 控制 profile、风格开关和阈值的质量门配置。
        run_start_line: 连续代码片段的起始源码行号。
        run_length: 连续代码片段包含的代码行数量。

    返回:
        None。该函数只在需要时向 ``issues`` 追加发现项。
    """

    # 阈值未启用时，当前规则不需要报告任何密集代码问题。
    if config.max_dense_code_lines <= 0:

        # 关闭密度阈值后，当前片段不可能产生 PG034。
        return

    # 片段长度未超出限制时保持静默。
    if run_length <= config.max_dense_code_lines:

        # 片段长度仍在允许范围内时保持无告警返回。
        return

    # 连续代码过长时，在片段起点登记可读性问题。
    add_issue(
        issues,
        "PG034",
        style_issue_level(config),
        filepath,
        run_start_line,
        f"Dense code block has {run_length} consecutive code lines; insert blank lines and Chinese comments.",
    )

# 扫描整份源码，找出连续代码过密的片段。
def check_dense_code_spacing(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],
) -> None:
    """检查 dense_code_spacing 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。

    返回:
        None。该函数只在发现问题时向 ``issues`` 追加记录。
    """

    # 阈值未启用时，整份文件都无需执行密集代码扫描。
    if config.max_dense_code_lines <= 0:

        # 配置关闭后不再逐行统计整份文件的密度。
        return

    # 字符串字面量覆盖行不参与连续代码统计。
    set_string_literal_line_numbers = collect_string_literal_lines(tree)  # 字符串字面量源码行集合

    # 这个起始行号槽位只在打开片段时记录后续 PG034 的锚点。
    int_run_start_line = 0  # 当前连续代码片段的起始行号

    # 这个计数器持续累计开放片段里的有效代码行，用来和阈值比较。
    int_run_length = 0  # 当前连续代码片段的代码行数量

    # 按源码顺序逐行扫描，识别片段边界并统计连续长度。
    for int_index, str_line in enumerate(lines, start=1):

        # 只有非空、非注释且不属于字符串字面量的行才计入代码密度。
        bool_counts_as_code = (  # 当前行是否计入连续代码长度
            is_code_line(str_line)  # 当前行本身是否为代码行
            and int_index not in set_string_literal_line_numbers  # 当前行是否位于字符串字面量外
        )

        # 命中有效代码行时继续扩展当前片段。
        if bool_counts_as_code:

            # 只有开放片段还是空状态时，才需要写入本段的首个代码行号。
            if int_run_length == 0:

                # 这里保存的是超阈值后向用户报告的片段起算位置。
                int_run_start_line = int_index  # 超阈值报告使用的片段首行锚点

            # 当前行已经属于活跃片段，所以只把长度再推进一格。
            int_run_length += 1  # 活跃片段已经累计的有效代码行数

            # 有效代码行已经并入片段，本轮无需执行分段结算。
            continue

        # 遇到分隔行时，先结算前一个连续代码片段。
        flush_dense_code_run(issues, filepath, config, int_run_start_line, int_run_length)

        # 前一段已经结算完毕，这里清空旧起点避免泄漏到下一段统计。
        int_run_start_line = 0  # 上一段结算后的空起点标记

        # 长度计数也必须一起复位，否则下一段会继承旧片段的累计值。
        int_run_length = 0  # 下一段开始前的零长度状态

    # 文件尾部没有分隔行时，也要补做最后一次片段结算。
    flush_dense_code_run(issues, filepath, config, int_run_start_line, int_run_length)
