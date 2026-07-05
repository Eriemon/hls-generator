"""实现 current-project 中文注释风格的专项规则。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import ast
import re
from pathlib import Path

# 导入当前模块运行所需的依赖
from .ast_helpers import (
    CJK_TEXT_PATTERN,
    COMMENT_PRAGMA_HINTS,
    GENERIC_COMMENT_PATTERN,
    add_issue,
    get_call_name,
    # 公开名称判断用于跳过私有 API 的 docstring 要求。
    is_public_name,
)
from .profiles import ProfileConfig
from .report import Issue

# `has_chinese_text` 判断chinese文本。
def has_chinese_text(text: str) -> bool:
    """判断当前对象是否具备 chinese_text。

    参数:
        text: 当前规则正在分析的文本内容。

    返回:
        返回是否包含中文。
    """

    # 返回当前文本是否命中中文字符模式。
    return CJK_TEXT_PATTERN.search(text) is not None

# 中文字符数量用于判断注释是否达到最小信息量。
def count_chinese_characters(text: str) -> int:
    """统计文本中的中文字符数量。

    参数:
        text: 当前规则正在分析的文本内容。

    返回:
        返回中文字符数量。
    """

    # 返回当前文本包含的中文字符个数。
    return len(CJK_TEXT_PATTERN.findall(text))

# `is_allowed_non_chinese_comment` 判断allowednonchinese注释。
def is_allowed_non_chinese_comment(comment_text: str, line_number: int) -> bool:
    """判断 allowed_non_chinese_comment 是否满足规则要求。

    参数:
        comment_text: 从源码 token 中提取的注释文本
        line_number: 当前源码位置对应的一基行号。

    返回:
        返回是否允许英文注释。
    """

    # 生成loweredtext的小写副本，确保关键词匹配不受大小写影响。
    str_lowered_text = comment_text.lower()  # 生成大小写无关文本，保证关键词匹配稳定

    # 去除strippedtext两端空白，避免格式差异影响规则判断。
    str_stripped_text = comment_text.strip()  # 去掉边界空白，避免格式差异影响判断

    # 注释只包含符号或空白时不具备语义内容。
    if line_number <= 2 and str_stripped_text.startswith(("#!", "# -*-", "# coding", "# encoding")):

        # 返回该规则是否命中目标条件。
        return True

    # 返回当前注释是否属于允许保留的英文指令或 pragma 提示。
    return any(hint in str_lowered_text for hint in COMMENT_PRAGMA_HINTS)

# 缺失型铺排注释只进入 backlog，低质量既有注释仍然阻断。
def style_issue_level(config: ProfileConfig, code: str) -> str:
    """根据规则类型选择 current-project 风格问题级别。

    参数:
        config: 控制 profile、风格开关和阈值的质量门配置。
        code: 当前发现项的 PG 规则编号。

    返回:
        返回问题级别。
    """

    # 当前 profile 未启用严格 current-project 风格时降级为警告。
    if not config.strict_project_style:

        # 非严格项目风格下，当前规则只作为可治理提示。
        return "WARNING"

    # 中文注释质量和模板化注释问题在严格风格下直接阻断。
    if code in {"PG030", "PG036", "PG037"}:

        # 严格风格下中文说明缺失类问题直接阻断交付。
        return "BLOCKER"

    # 其他 current-project 风格问题在这里保持警告级别。
    return "WARNING"

# `check_chinese_comments_and_docstrings` 检查并登记chinese注释集合anddocstrings。
def check_chinese_comments_and_docstrings(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    comments: dict[int, str],
) -> None:
    """检查 chinese_comments_and_docstrings 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        comments: 源码行号到普通注释文本的映射。
    返回:
        当前函数只向 `issues` 追加发现项，不返回额外业务结果。
    """

    # 未启用中文注释检查时直接结束当前规则。
    # 没有可检查的源码行时跳过中文注释检查。
    if not config.require_chinese_comments:

        # 当前 profile 未启用此规则时直接结束检查。
        return

    # 按行扫描普通注释，跳过工具指令和允许的英文短注释。
    for int_line_number, str_comment_text in comments.items():

        # 已经包含中文的普通注释不需要再登记语言问题。
        if has_chinese_text(str_comment_text):

            # 当前注释已经满足中文语言要求。
            continue

        # 允许的 pragma 或编码指令保持英文时跳过报告。
        if is_allowed_non_chinese_comment(str_comment_text, int_line_number):

            # 当前英文注释属于允许豁免的控制指令。
            continue

        # 登记当前规则发现的质量门问题
        add_issue(
            issues,
            "PG030",
            style_issue_level(config, "PG030"),
            filepath,
            int_line_number,
            "Comment should use Chinese text under current-project style.",
        )

    # 扫描 AST 节点，寻找本规则关注的语法结构。
    for node in ast.walk(tree):

        # 只有字符串常量节点才可能来自 docstring。
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):

            # 其他 AST 节点不承载 docstring 检查目标。
            continue

        # 确认候选节点具备目标 AST 结构后读取专属字段。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):

            # 没有行号的字符串常量无法定位报告位置。
            if not is_public_name(node.name):

                # 私有函数和类不受公开 API 的 docstring 语言约束。
                continue

            # 计算源码行位置，定位相邻语句和注释。
            int_line_number = getattr(node, "lineno", 1)  # 整数源码行编号

        # 模块级 docstring 没有公开名称可见性约束，单独走默认定位分支。
        else:

            # 模块级 docstring 缺省定位到文件首行。
            int_line_number = 1  # 模块级 docstring 的默认报告行号

        # 读取文档字符串内容，检查公开 API 说明是否完整。
        str_docstring = ast.get_docstring(node)  # 文档字符串

        # docstring 缺少中文时报告当前项目语言要求。
        if str_docstring and not has_chinese_text(str_docstring):

            # 为当前公开节点登记 docstring 语言问题。
            add_issue(
                issues,
                "PG030",
                style_issue_level(config, "PG030"),
                filepath,
                int_line_number,
                "Docstring should use Chinese text under current-project style.",
            )

# `is_blank_line` 判断空行源码行。
def is_blank_line(line: str) -> bool:
    """判断 blank_line 是否满足规则要求。

    参数:
        line: 当前规则正在解析的单行文本。

    返回:
        返回是否为空行。
    """

    # 返回当前源码行是否只包含空白字符。
    return line.strip() == ""

# `is_comment_line` 判断注释源码行。
def is_comment_line(line: str) -> bool:
    """判断 comment_line 是否满足规则要求。

    参数:
        line: 当前规则正在解析的单行文本。

    返回:
        返回是否为注释行。
    """

    # 返回当前源码行去掉左侧缩进后是否以注释符号开头。
    return line.lstrip().startswith("#")

# `is_code_line` 判断code源码行。
def is_code_line(line: str) -> bool:
    """判断 code_line 是否满足规则要求。

    参数:
        line: 当前规则正在解析的单行文本。

    返回:
        返回是否为代码行。
    """

    # 空白行和纯注释行不计入代码块密度。
    str_stripped_line = line.strip()  # 去除缩进后的源码行文本

    # 返回当前行是否属于需要参与密度判断的真实代码行。
    return bool(str_stripped_line) and not str_stripped_line.startswith("#")

# `previous_significant_line_index` 处理significant源码行位置。
def previous_significant_line_index(lines: list[str], start_index: int) -> int | None:
    """查找指定行之前最近的有效源码行。

    参数:
        lines: 按行读取的源码文本。
        start_index: 起始源码行索引

    返回:
        查找指定行之前最近的有效源码行产出的结果。
    """

    # 从语句前一行向上查找最近的非空注释。
    for index in range(start_index, -1, -1):

        # 空行之前没有注释时结束向上搜索。
        if not is_blank_line(lines[index]):

            # 返回向上扫描时命中的最近有效源码行索引。
            return index

    # 文件开头之前未命中有效源码行时返回空结果。
    return None

# `collect_comment_group_after_blank` 收集注释groupafter空行。
def collect_comment_group_after_blank(lines: list[str], start_index: int) -> tuple[list[int], int]:
    """收集空行之后连续的独立注释。

    参数:
        lines: 按行读取的源码文本。
        start_index: 起始源码行索引

    返回:
        独立注释行号列表，以及注释块之后的第一行索引。
    """

    # 注释行集合用于判断空行分隔后的代码块是否已有说明。
    list_comment_line_numbers: list[int] = []  # 列表注释源码行numbers

    # 从空行后的第一行开始向下收集连续注释。
    int_current_index = start_index  # 连续注释块的当前扫描位置

    # 持续推进直到满足停止条件
    while int_current_index < len(lines) and is_comment_line(lines[int_current_index]):

        # 记录 standalone 注释所在行，供后续块检查快速查询。
        list_comment_line_numbers.append(int_current_index + 1)

        # 普通代码行会中断连续注释块搜索。
        int_current_index += 1  # 扫描位置

    # 返回所有可作为块说明的独立注释行号。
    return list_comment_line_numbers, int_current_index

# 特殊语句的空行由 PG032 检查，PG031 不再要求额外块说明。
def starts_special_spacing_statement(line_text: str) -> bool:
    """判断空行后的语句是否只需要 PG032 间距检查。

    参数:
        line_text: 空行和可选注释之后的第一行源码文本。

    返回:
        下方语句属于控制流、返回、调用或调用赋值等特殊语句时返回 True。
    """

    # 去掉缩进后识别语句开头，避免嵌套层级影响分类。
    str_stripped_line = line_text.lstrip()  # 空行后的实际语句文本

    # 控制流和终止语句由 PG032 负责检查空行，不再要求额外块说明。
    if str_stripped_line.startswith((
        "return",
        "if ",
        "for ",
        "while ",
        "with ",
        "try:",
        "assert ",
        "raise ",
        "continue",
        "break",
        "elif ",
        "else:",
        "except ",
        "finally:",
    )):

        # 这些特殊语句由 PG032 单独负责前置空行和语义注释检查。
        return True

    # 返回当前代码行是否呈现函数调用或调用赋值外形。
    return "(" in str_stripped_line and not str_stripped_line.startswith(("def ", "class "))

# `blank_line_block_needs_comment` 处理源码行代码块needs注释。
def blank_line_block_needs_comment(
    lines: list[str],
    previous_index: int | None,
    next_index: int,
    string_literal_lines: set[int],
) -> bool:
    """判断空行后的代码块是否需要说明注释。

    参数:
        lines: 按行读取的源码文本。
        previous_index: 前一源码行索引
        next_index: 下一源码行索引
        string_literal_lines: 由字符串字面量占用的源码行集合。

    返回:
        判断空行后的代码块是否需要说明注释产出的结果。
    """

    # 空白行和注释行不按可执行代码行处理。
    if previous_index is None or next_index >= len(lines):

        # 上下文不完整时当前空行不可能形成需要说明的代码块边界。
        return False

    # 记录空行上方是否存在真实代码块，供后续边界判断复用。
    bool_previous_is_block_boundary = bool(lines[previous_index].strip()) and not is_comment_line(lines[previous_index])  # 空行前方存在独立代码块

    # 下一行是否为代码决定该空行是否分隔出新代码块。
    bool_next_is_code = is_code_line(lines[next_index]) and next_index + 1 not in string_literal_lines  # 空行后方存在可检查代码

    # 特殊语句只需要保留前置空行，避免为了满足 PG031 填入模板注释。
    if bool_next_is_code and starts_special_spacing_statement(lines[next_index]):

        # 下方如果是特殊语句，则由 PG032 负责间距和语义注释检查。
        return False

    # 返回当前空行是否真正切分出了一个需要块说明的普通代码块。
    return bool_previous_is_block_boundary and bool_next_is_code

# 判断候选注释行集合中是否已经存在中文块说明。
def has_chinese_comment_line(comment_line_numbers: list[int], comments: dict[int, str]) -> bool:
    """判断当前对象是否具备 chinese_comment_line。

    参数:
        comment_line_numbers: 源码中用于定位检查问题的行号或行文本。
        comments: 源码行号到普通注释文本的映射。

    返回:
        返回当前对象是否具备目标特征。
    """

    # 返回当前候选注释组里是否至少有一条中文注释。
    return any(
        has_chinese_text(comments.get(line_number, ""))
        for line_number in comment_line_numbers
    )

# `check_blank_line_block_comments` 检查并登记空行源码行代码块注释集合。
def check_blank_line_block_comments(
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],
    comments: dict[int, str],
    string_literal_lines: set[int],
) -> None:
    """检查 blank_line_block_comments 对应的质量门约束。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comments: 源码行号到普通注释文本的映射。
        string_literal_lines: 由字符串字面量占用的源码行集合。
    返回:
        当前函数只向 `issues` 追加空行块相关发现项，不返回额外业务结果。
    """

    # 未启用空行块注释要求时跳过 PG031。
    if not config.require_blank_line_comments:

        # 当前 profile 未启用空行块注释规则时直接结束。
        return

    # 从文件首行开始顺序扫描空行分隔代码块。
    int_index = 0  # 空行块扫描游标

    # 逐段扫描空行分隔位置，判断下方代码块是否缺少中文说明。
    while int_index < len(lines):

        # 文件开头或结尾空行不形成两个代码块之间的边界。
        if not is_blank_line(lines[int_index]):

            # 没有分隔出下方代码块时，游标只需顺移一行。
            int_index += 1  # 跳到下一行继续扫描

            # 非空白行不形成块边界，直接进入下一轮扫描。
            continue

        # 先定位空行上方最近的有效代码行，判断是否形成块边界。
        int_previous_index = previous_significant_line_index(lines, int_index - 1)  # 空行上方最近的有效代码行

        # 连续空行只保留最后一个候选边界位置。
        while int_index < len(lines) and is_blank_line(lines[int_index]):

            # 代码块前已有独立说明注释时无需报告。
            int_index += 1  # 吞掉当前连续空行

        # 注释行缓存用于检测空行附近是否已有块说明。
        tuple_comment_line_numbers, tuple_next_index = collect_comment_group_after_blank(lines, int_index)  # 空行后连续注释及其后继位置

        # 上方相邻注释已经说明下方代码块时跳过。
        if not blank_line_block_needs_comment(lines, int_previous_index, tuple_next_index, string_literal_lines):

            # 当前空行块不需要补说明时，直接跳到下一段继续。
            int_index = max(tuple_next_index, int_index + 1)  # 跳到注释块之后或当前位置之后

            # 当前空行块已经处理完成。
            continue

        # 下方第一行本身是注释时，该块已带说明。
        if not has_chinese_comment_line(tuple_comment_line_numbers, comments):

            # 空行切分出的下方代码块缺少中文说明时登记 PG031。
            add_issue(
                issues,
                "PG031",
                style_issue_level(config, "PG031"),
                filepath,
                tuple_next_index + 1,
                "Blank-line-separated code block needs a Chinese comment before the lower block.",
            )

        # 处理完本段空行块后，把游标推进到下一段候选区域。
        int_index = max(tuple_next_index, int_index + 1)  # 跳到本段空行块之后

# 续接分支依附前一个控制块，不要求独立前置空行。
def control_statement_needs_spacing(line_text: str) -> bool:
    """判断控制语句前是否必须保留空行。

    参数:
        line_text: 当前规则正在解析的单行源码文本。

    返回:
        布尔值，表示判断控制语句前是否必须保留空行。
    """

    # 只移除左侧缩进，保留语句末尾内容用于前缀判断。
    str_stripped_line = line_text.lstrip()  # 去除缩进后的控制语句文本

    # `elif`、`else`、`except` 和 `finally` 依附前一个控制块，不强制额外空行。
    if str_stripped_line.startswith(("elif ", "else:", "except ", "finally:")):

        # 续接控制分支直接依附上一个控制块，不再要求单独空行。
        return False

    # 其他独立控制语句仍然需要保留前置空行。
    return True

# `has_blank_before_statement` 判断空行beforestatement。
def has_blank_before_statement(lines: list[str], line_number: int) -> bool:
    """判断当前对象是否具备 blank_before_statement。

    参数:
        lines: 按行读取的源码文本。
        line_number: 当前源码位置对应的一基行号。

    返回:
        返回当前对象是否具备目标特征。
    """

    # 先定位目标语句上一行，再判断是否存在前置空行。
    int_previous_index = line_number - 2  # 目标语句上一行的零基索引

    # 文件首行前没有可检查内容，视为已经满足空行要求。
    if int_previous_index < 0:

        # 文件开头之前没有内容时天然满足前置空行要求。
        return True

    # 目标语句上一行本身为空行时，分隔要求已经满足。
    if is_blank_line(lines[int_previous_index]):

        # 目标语句前已经存在物理空行。
        return True

    # 上一行不是注释也不是空行时，目标语句缺少 required spacing。
    if not is_comment_line(lines[int_previous_index]):

        # 普通代码直接贴在目标语句前方时判定为空行缺失。
        return False

    # 跳过紧邻的连续注释块，回看真正的物理分隔行。
    while int_previous_index >= 0 and is_comment_line(lines[int_previous_index]):

        # 连续注释块整体视为语句前说明，继续向上寻找真正的分隔行。
        int_previous_index -= 1  # 跳过当前注释行，继续回看更早的物理分隔

    # 返回注释块上方是否仍然保留了物理空行。
    return int_previous_index < 0 or is_blank_line(lines[int_previous_index])

# `assignment_value_contains_call` 处理字段值contains调用。
def assignment_value_contains_call(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> bool:
    """判断赋值右侧是否包含函数调用。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        判断赋值右侧是否包含函数调用产出的结果。
    """

    # 保存 AST 节点引用，读取语法结构专属字段。
    # 增强赋值等节点没有 `value` 字段时，不能通过右侧表达式发现调用。
    if getattr(node, "value", None) is None:

        # 缺少右侧表达式时当前赋值节点不可能包含函数调用。
        return False

    # 返回赋值右侧表达式树里是否出现过函数调用节点。
    return any(isinstance(child, ast.Call) for child in ast.walk(getattr(node, "value")))

# `control_special_statement_kind` 处理specialstatement类型。
def control_special_statement_kind(
    node: ast.AST,
    lines: list[str],
    allowed_kinds: set[str],
) -> str:
    """识别控制流节点对应的特殊语句类型。

    参数:
        node: 当前遍历到的 AST 节点。
        lines: 按行读取的源码文本。
        allowed_kinds: 允许的语句类型集合

    返回:
        识别控制流节点对应的特殊语句类型产出的结果。
    """

    # 记录当前控制流节点在源码中的一基行号。
    int_line_number = getattr(node, "lineno", 1)  # 当前控制流节点的一基行号

    # `for` 与 `async for` 在 spacing 规则里共用同一类标识。
    if isinstance(node, (ast.For, ast.AsyncFor)) and "for" in allowed_kinds:

        # 命中循环控制流后返回 `for` 特殊语句类型。
        return "for"

    # `while` 控制流直接映射到 `while` 特殊语句类型。
    if isinstance(node, ast.While) and "while" in allowed_kinds:

        # 命中 `while` 控制流后返回对应类型。
        return "while"

    # 只有允许检查 `if` 时，才继续判断独立 `if` 的 spacing 语义。
    if isinstance(node, ast.If) and "if" in allowed_kinds:

        # `if` 只有作为独立控制语句时才需要前置空行。
        if control_statement_needs_spacing(lines[int_line_number - 1]):

            # 确认该 `if` 需要单独分隔后返回 `if` 类型。
            return "if"

    # 当前控制流节点未命中任何受管特殊语句类型时返回空结果。
    return ""

# 识别块级特殊语句的帮助函数。
def block_special_statement_kind(node: ast.AST, allowed_kinds: set[str]) -> str:
    """识别块级语句对应的特殊语句类型。

    参数:
        node: 当前遍历到的 AST 节点。
        allowed_kinds: 允许的语句类型集合

    返回:
        识别块级语句对应的特殊语句类型产出的结果。
    """

    # 列出块级语句与 current-project 特殊语句名称之间的映射。
    tuple_node_type_to_kind: tuple[tuple[type[ast.AST], str], ...] = (  # 块级语句类型到规则名称的映射
        (ast.With, "with"),  # 同步上下文管理语句
        (ast.AsyncWith, "with"),  # 异步上下文管理语句
        (ast.Try, "try"),  # 异常保护语句
        (ast.Assert, "assert"),  # 断言语句
        (ast.Return, "return"),  # 返回语句
    )

    # 按 AST 类型把块级语句映射为 current-project 特殊语句名称。
    for class_node_type, str_statement_kind in tuple_node_type_to_kind:

        # 命中映射表中的块级语句后返回对应规则名称。
        if isinstance(node, class_node_type) and str_statement_kind in allowed_kinds:

            # 当前块级语句命中后直接返回对应类型。
            return str_statement_kind

    # 未命中块级语句映射时返回空字符串。
    return ""

# 识别调用相关特殊语句的帮助函数。
def call_special_statement_kind(node: ast.AST, allowed_kinds: set[str]) -> str:
    """识别调用语句对应的特殊语句类型。

    参数:
        node: 当前遍历到的 AST 节点。
        allowed_kinds: 允许的语句类型集合

    返回:
        识别调用语句对应的特殊语句类型产出的结果。
    """

    # 裸调用表达式对应 current-project 的 function_call 特殊语句。
    bool_is_call_expression = isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)  # 是否为裸函数调用语句

    # 裸调用语句命中时直接归类为 `function_call`。
    if bool_is_call_expression and "function_call" in allowed_kinds:

        # 命中裸调用表达式后返回 `function_call` 类型。
        return "function_call"

    # 赋值语句需要进一步检查右侧或增强赋值部分是否含调用。
    bool_is_assignment = isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))  # 是否为赋值类语句

    # 非赋值语句或未启用调用赋值规则时，不参与调用赋值分隔检查。
    if not bool_is_assignment or "assignment_with_function_call" not in allowed_kinds:

        # 当前节点不属于调用赋值检查范围时返回空字符串。
        return ""

    # 赋值右侧包含调用表达式时，需要按调用赋值语句检查空行。
    if assignment_value_contains_call(node):

        # 命中调用赋值后返回对应的特殊语句类型。
        return "assignment_with_function_call"

    # 未命中调用相关规则时返回空字符串。
    return ""

# 汇总三类特殊语句识别器的统一入口。
def special_statement_kind(
    node: ast.AST,
    lines: list[str],
    allowed_kinds: set[str],
) -> str:
    """识别语句节点在 current-project 中的分隔类型。

    参数:
        node: 当前遍历到的 AST 节点。
        lines: 按行读取的源码文本。
        allowed_kinds: 允许的语句类型集合

    返回:
        识别语句节点在 current-project 中的分隔类型产出的结果。
    """

    # 记录当前 AST 节点的一基源码行号。
    int_line_number = getattr(node, "lineno", 1)  # 当前 AST 节点的一基源码行号

    # AST 行号越界时无法读取源码上下文，直接忽略该节点。
    if int_line_number < 1 or int_line_number > len(lines):

        # 行号超出源码范围时返回空字符串。
        return ""

    # 依次尝试控制流、块级语句和调用语句三类特殊语句识别器。
    for kind_getter in (
        control_special_statement_kind,
        lambda item, _lines, kinds: block_special_statement_kind(item, kinds),
        lambda item, _lines, kinds: call_special_statement_kind(item, kinds),
    ):

        # 当前识别器返回的语句类型决定后续 PG032 报告中的分类名称。
        str_statement_kind = kind_getter(node, lines, allowed_kinds)  # 特殊语句类型名称

        # 第一个命中的语句类型即可代表该 AST 节点的分隔要求。
        if str_statement_kind:

            # 返回首个命中的特殊语句类型供 PG032 使用。
            return str_statement_kind

    # 所有识别器都未命中时返回空字符串。
    return ""

# `check_control_statement_spacing` 检查并登记controlstatementspacing。
def check_control_statement_spacing(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],
) -> None:
    """检查 control_statement_spacing 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。

    返回:
        None：当前函数只向 `issues` 追加发现项，不直接返回结果。
    """

    # 未启用控制流或特殊语句分隔时，不扫描 AST 节点。
    if not config.require_control_spacing and not config.require_special_statement_spacing:

        # 当前 profile 未打开相关规则时直接结束本轮检查。
        return

    # 初始化allowedkinds收集容器，汇总本轮扫描发现。
    set_allowed_kinds = set(config.special_statement_kinds)  # 初始化集合容器，去重保存本轮扫描命中项

    # control spacing 开关会把 for/if/while 纳入特殊语句集合。
    if config.require_control_spacing:

        # 合并当前检查得到的映射
        set_allowed_kinds.update({"for", "if", "while"})

    # 逐个遍历 AST 节点并定位需要检查空行分隔的语句。
    for node in ast.walk(tree):

        # 将 AST 节点转换为报告里可读的特殊语句类别。
        str_statement_kind = special_statement_kind(node, lines, set_allowed_kinds)  # 特殊语句类别

        # 非目标语句不需要 PG032 空行分隔检查。
        if not str_statement_kind:

            # 未命中目标类别的节点直接进入下一个节点。
            continue

        # 记录当前语句所在源码行，供后续定位与报错使用。
        int_line_number = getattr(node, "lineno", 1)  # 当前特殊语句所在的一基源码行号

        # 已有空行或注释块分隔的特殊语句不报告 PG032。
        if has_blank_before_statement(lines, int_line_number):

            # 具备合规分隔的语句无需重复登记问题。
            continue

        # 对缺少空行与用途注释的目标语句登记 PG032。
        add_issue(
            issues,
            "PG032",
            style_issue_level(config, "PG032"),
            filepath,
            int_line_number,
            (
                f"Special statement `{str_statement_kind}` should have a blank line, "
                "optionally followed by a Chinese comment, above it."
            ),
        )

# `line_has_inline_chinese_comment` 处理has行内chinese注释。
def line_has_inline_chinese_comment(
    line_number: int,
    lines: list[str],
    comment_positions: dict[int, tuple[int, str]],
) -> bool:
    """判断源码行是否带有中文右侧注释。

    参数:
        line_number: 当前源码位置对应的一基行号。
        lines: 按行读取的源码文本。
        comment_positions: 源码行号到注释列位置和注释文本的映射。

    返回:
        判断源码行是否带有中文右侧注释产出的结果。
    """

    # token 索引提供该行尾注释的列位置和原文。
    tuple_comment_info = comment_positions.get(line_number)  # 当前行尾注释信息

    # token 索引没有记录该行注释时，说明赋值行没有中文尾注释候选。
    if tuple_comment_info is None:

        # 缺少尾注释记录时当前赋值行直接视为不合规。
        return False

    # 注释列用于区分独占行注释和真正跟在代码后的尾注释。
    int_comment_column, str_comment_text = tuple_comment_info  # 注释列位置

    # 行首独占注释不能充当赋值语句右侧用途说明。
    if not lines[line_number - 1][:int_comment_column].strip():

        # 没有代码出现在注释左侧时，这一行只是独立注释而非尾注释。
        return False

    # 只有真正跟在代码后的中文注释才算作赋值尾注释。
    return has_chinese_text(str_comment_text)

# 赋值尾注释检查会同时查看赋值首行和末行。
def assignment_has_inline_chinese_comment(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
    lines: list[str],
    comment_positions: dict[int, tuple[int, str]],
) -> bool:
    """判断赋值语句是否已经带有中文用途尾注释。

    参数:
        node: 当前遍历到的 AST 节点。
        lines: 按行读取的源码文本。
        comment_positions: 源码行号到注释列位置和注释文本的映射。

    返回:
        判断赋值语句是否已经带有中文用途尾注释产出的结果。
    """

    # 多行赋值的首行和末行都可能承载尾注释。
    int_start_line = getattr(node, "lineno", 1)  # 赋值语句起始行号

    # end_lineno 缺失时退回首行，兼容旧 Python AST。
    int_end_line = getattr(node, "end_lineno", int_start_line)  # 赋值语句结束行号

    # 汇总首尾两行，后续统一检查哪一行已经带有尾注释。
    set_candidate_lines = {int_start_line, int_end_line}  # 集合candidate源码行序列

    # 只要候选行里任意一行具备中文尾注释，就视为当前赋值已合规。
    return any(
        1 <= line_number <= len(lines)
        and line_has_inline_chinese_comment(line_number, lines, comment_positions)
        for line_number in set_candidate_lines
    )

# `check_assignment_inline_comments` 检查并登记assignment行内注释集合。
def check_assignment_inline_comments(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],
    comment_positions: dict[int, tuple[int, str]],
) -> None:
    """检查 assignment_inline_comments 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comment_positions: 源码行号到注释列位置和注释文本的映射。

    返回:
        None：当前函数只向 `issues` 追加赋值注释缺失问题。
    """

    # profile 未要求赋值注释时，跳过 PG033 检查。
    if not config.require_assignment_comments:

        # 当前配置关闭赋值尾注释检查时直接结束。
        return

    # 遍历所有 AST 节点并筛出赋值类语句。
    for node in ast.walk(tree):

        # 只有赋值类节点需要右侧中文用途注释。
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):

            # 非赋值节点不参与当前规则检查。
            continue

        # 已经带有中文尾注释的赋值语句不报告 PG033。
        if assignment_has_inline_chinese_comment(node, lines, comment_positions):

            # 已满足用途注释要求的赋值节点直接跳过。
            continue

        # 对缺少右侧中文用途注释的赋值节点登记 PG033。
        add_issue(
            issues,
            "PG033",
            style_issue_level(config, "PG033"),
            filepath,
            getattr(node, "lineno", 1),
            "Variable assignment should include a right-side Chinese comment.",
        )

# 赋值目的注释检查需要读取目标语句前连续的独立说明注释。
def collect_comment_group_before_line(lines: list[str], line_number: int) -> tuple[list[int], int | None]:
    """收集目标语句上方连续的独立注释行。

    参数:
        lines: 按行读取的源码文本。
        line_number: 当前源码位置对应的一基行号。

    返回:
        注释行号列表，以及注释块上方最近的非注释行索引。
    """

    # 从目标语句上一行开始向上扫描连续的独立注释块。
    int_current_index = line_number - 2  # 当前向上扫描的零基行索引

    # 记录当前注释块涉及的一基源码行号。
    list_comment_line_numbers: list[int] = []  # 连续注释块对应的一基行号列表

    # 持续向上收集连续的独立注释行。
    while int_current_index >= 0 and is_comment_line(lines[int_current_index]):

        # 记录当前命中的注释行，稍后会统一恢复成源码顺序。
        list_comment_line_numbers.append(int_current_index + 1)

        # 连续注释块还未结束，继续向上收集同一组前置说明。
        int_current_index -= 1  # 继续查看上一行是否仍属于同一注释块

    # 反转候选顺序，优先检查距离目标语句最近的注释。
    list_comment_line_numbers.reverse()

    # 保存注释块上方最近的非注释行位置，供空行规则复核使用。
    int_previous_index = int_current_index if int_current_index >= 0 else None  # 注释块上方最近的非注释行索引

    # 返回本轮扫描收集到的候选列表。
    return list_comment_line_numbers, int_previous_index

# `assignment_has_above_chinese_comment` 处理has上方chinese注释。
def assignment_has_above_chinese_comment(
    line_number: int,
    lines: list[str],
    comments: dict[int, str],
) -> bool:
    """判断赋值语句上方是否已有中文目的注释。

    参数:
        line_number: 当前源码位置对应的一基行号。
        lines: 按行读取的源码文本。
        comments: 源码行号到普通注释文本的映射。

    返回:
        判断赋值语句上方是否已有中文目的注释产出的结果。
    """

    # 行号越界时无法验证赋值语句上方注释。
    if line_number < 1 or line_number > len(lines):

        # 越界位置无法关联任何有效前置注释。
        return False

    # 赋值语句前的连续注释行号和其上方分隔行共同决定注释是否贴近赋值。
    tuple_comment_line_numbers, tuple_previous_index = collect_comment_group_before_line(lines, line_number)  # 前置注释行号及其上方分隔位置

    # 没有前置注释时，调用方会用 PG035 报告缺失说明。
    if not tuple_comment_line_numbers:

        # 未收集到前置注释时直接判定为不合规。
        return False

    # 前置注释块上方不是空行时，不符合“一空行加目的注释”的结构。
    if tuple_previous_index is not None and not is_blank_line(lines[tuple_previous_index]):

        # 注释块和上方代码粘连时不满足独立目的说明结构。
        return False

    # 只要前置注释里存在中文语义说明，就视为满足上方注释要求。
    return any(has_chinese_text(comments.get(item, "")) for item in tuple_comment_line_numbers)

# `check_assignment_above_comments` 检查并登记assignment上方注释集合。
def check_assignment_above_comments(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],
    comments: dict[int, str],
) -> None:
    """检查 assignment_above_comments 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comments: 源码行号到普通注释文本的映射。

    返回:
        None：当前函数只向 `issues` 追加赋值上方注释缺失问题。
    """

    # profile 未启用赋值上方注释时，不报告 PG035。
    if not config.require_assignment_block_comments:

        # 当前配置关闭前置注释检查时直接结束。
        return

    # 遍历 AST 节点并筛出需要检查的赋值类语句。
    for node in ast.walk(tree):

        # 只有赋值类节点需要检查上方目的注释。
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):

            # 非赋值节点不参与上方注释规则检查。
            continue

        # 记录赋值语句所在行，供前置注释检查和报错定位使用。
        int_line_number = getattr(node, "lineno", 1)  # 赋值语句所在的一基行号

        # 已有合格中文目的注释的赋值语句不报告 PG035。
        if assignment_has_above_chinese_comment(int_line_number, lines, comments):

            # 已满足前置注释要求的赋值节点无需继续登记问题。
            continue

        # 对缺少上方中文目的注释的赋值节点登记 PG035。
        add_issue(
            issues,
            "PG035",
            style_issue_level(config, "PG035"),
            filepath,
            int_line_number,
            "Variable assignment should have one blank line and a Chinese purpose comment above it.",
        )

# `normalized_comment_content` 处理注释content。
def normalized_comment_content(comment_text: str) -> str:
    """去掉注释符号、标点和空白后生成匹配用文本。

    参数:
        comment_text: 从源码 token 中提取的注释文本

    返回:
        去掉注释符号、标点和空白后生成匹配用文本产出的结果。
    """

    # 先统一去掉注释前缀和大小写差异，方便后续模板匹配。
    str_content = comment_text.lstrip("#").strip().lower()  # 注释文本的归一化前置结果

    # 进一步清掉标点和空白，得到可稳定比较的核心文本。
    return re.sub(r"[\s`'\"：:，,。；;、（）()\[\]【】]+", "", str_content)

# `is_generic_comment_text` 判断泛化项注释文本。
def is_generic_comment_text(comment_text: str, banned_phrases: tuple[str, ...]) -> bool:
    """判断 generic_comment_text 是否满足规则要求。

    参数:
        comment_text: 从源码 token 中提取的注释文本
        banned_phrases: 禁用注释短语集合

    返回:
        布尔值，表示判断注释是否只包含空泛名词或动作词。
    """

    # 去掉符号后的注释文本用于和禁用短语做稳定比较。
    str_normalized_text = normalized_comment_content(comment_text)  # 归一化注释文本

    # 空注释不属于可判定的空泛语义模板。
    if not str_normalized_text:

        # 归一化后为空时，说明这条注释没有可分析语义。
        return False

    # 禁用短语先归一化，避免空格和符号差异绕过检测。
    set_banned_texts = {normalized_comment_content(item) for item in banned_phrases}  # 归一化后的禁用注释文本

    # 命中显式禁用短语时直接认定为模板化注释。
    if str_normalized_text in set_banned_texts:

        # 已经精确命中禁用短语，无需继续做正则匹配。
        return True

    # 回退到通用模板正则，捕获未在清单中逐条列出的空泛短语。
    return GENERIC_COMMENT_PATTERN.fullmatch(str_normalized_text) is not None

# `check_generic_comment_text` 检查并登记泛化项注释文本。
def check_generic_comment_text(
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    comments: dict[int, str],
) -> None:
    """检查 generic_comment_text 对应的质量门约束。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        comments: 源码行号到普通注释文本的映射。

    返回:
        无返回值；命中模板化注释时仅向 issues 追加 PG036。
    """

    # 没有配置禁用短语时，普通泛化注释检查不产生结果。
    if not config.banned_generic_comment_phrases:

        # 配置为空时无法定义模板化短语边界，直接跳过本规则。
        return

    # 对每条源码注释检查是否落入空泛名词或固定动作短语。
    for int_line_number, str_comment_text in comments.items():

        # 工具指令类英文注释允许保留，不参与中文语义质量检查。
        if is_allowed_non_chinese_comment(str_comment_text, int_line_number):

            # 允许的工具指令不应被 PG036 当成模板注释误报。
            continue

        # 只有命中空泛短语的注释才登记 PG036。
        if not is_generic_comment_text(str_comment_text, config.banned_generic_comment_phrases):

            # 未命中模板短语时继续检查下一条注释。
            continue

        # 对模板化注释登记 PG036，提示改写成具体语义说明。
        add_issue(
            issues,
            "PG036",
            style_issue_level(config, "PG036"),
            filepath,
            int_line_number,
            "Comment is too generic; explain the variable or code block purpose instead of using a fixed template.",
        )

# `is_vague_comment_text` 判断含糊项注释文本。
def is_vague_comment_text(comment_text: str, vague_phrases: tuple[str, ...]) -> bool:
    """判断 vague_comment_text 是否满足规则要求。

    参数:
        comment_text: 从源码 token 中提取的注释文本
        vague_phrases: 含糊注释短语集合

    返回:
        布尔值，表示判断注释是否命中含糊说明短语。
    """

    # 注释归一化后再和含糊短语表比较。
    str_normalized_text = normalized_comment_content(comment_text)  # 当前注释的归一化文本

    # 空注释不能命中含糊短语表。
    if not str_normalized_text:

        # 没有实质文本时不视为命中含糊短语清单。
        return False

    # 含糊短语同样按归一化文本比较。
    set_vague_texts = {normalized_comment_content(item) for item in vague_phrases}  # 归一化后的含糊注释文本

    # 只有精确落入含糊短语清单的注释才判定为语义模糊。
    return str_normalized_text in set_vague_texts

# 具体性检查会排除模板、含糊词和只有极少中文的注释。
def comment_has_specific_purpose(
    comment_text: str,
    minimum_chinese_chars: int,
    config: ProfileConfig,
) -> bool:
    """判断注释是否达到当前语句需要的具体程度。

    参数:
        comment_text: 从源码 token 中提取的注释文本
        minimum_chinese_chars: 中文字符数量下限
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        布尔值，表示判断注释是否达到当前语句需要的具体程度。
    """

    # 已经命中空泛模板的注释不再通过具体性检查。
    if is_generic_comment_text(comment_text, config.banned_generic_comment_phrases):

        # 模板化注释缺少当前语境信息，直接判定为不具体。
        return False

    # 含糊短语即使含中文，也不能算作真实语义说明。
    if is_vague_comment_text(comment_text, config.banned_vague_comment_phrases):

        # 含糊词没有解释变量或代码块目的，也应视为不具体。
        return False

    # 最后用最少中文字符数约束兜底，过滤过短说明。
    return count_chinese_characters(comment_text) >= minimum_chinese_chars

# `is_import_statement_line` 判断导入statement源码行。
def is_import_statement_line(line_text: str) -> bool:
    """判断 import_statement_line 是否满足规则要求。

    参数:
        line_text: 当前规则正在解析的单行源码文本。

    返回:
        布尔值，表示判断importstatement源码行是否成立。
    """

    # 导入语句判断只需要去除缩进后检查行首关键词。
    str_stripped_line = line_text.lstrip()  # 去除缩进后的导入语句文本

    # import/from 前缀已经足以判定当前行是否属于导入语句。
    return str_stripped_line.startswith(("import ", "from "))

# `is_import_group_comment` 判断导入group注释。
def is_import_group_comment(comment_text: str, next_line_text: str) -> bool:
    """判断 import_group_comment 是否满足规则要求。

    参数:
        comment_text: 从源码 token 中提取的注释文本
        next_line_text: 注释下方相邻源码行文本

    返回:
        布尔值，表示判断importgroup注释是否成立。
    """

    # 导入组注释只适用于紧邻 import/from 的说明行。
    if not is_import_statement_line(next_line_text):

        # 下方不是导入语句时，这条短注释不能按导入组豁免处理。
        return False

    # 归一化后的导入说明用于识别“系统库/项目模块”这类短注释。
    str_normalized_text = normalized_comment_content(comment_text)  # 归一化导入组注释

    # 导入组短注释允许使用这些领域词，而不要求满足普通块注释长度。
    tuple_import_group_hints = ("库", "导入", "基础", "项目", "算术", "绘图", "量子", "系统", "方法")  # 元组导入grouphints

    # 命中导入组关键词时允许使用更短的组级说明注释。
    return any(hint in str_normalized_text for hint in tuple_import_group_hints)

# 赋值尾注释必须说明该值在当前语境中的用途。
def assignment_inline_comment_is_specific(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
    config: ProfileConfig,
    lines: list[str],
    comment_positions: dict[int, tuple[int, str]],
) -> bool:
    """判断赋值语句的右侧尾注释是否足够具体。

    参数:
        node: 当前遍历到的 AST 节点。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comment_positions: 源码行号到注释列位置和注释文本的映射。

    返回:
        True 表示没有尾注释可检查，或已有尾注释通过具体性检查。
    """

    # 赋值语句首行可能携带右侧用途注释。
    int_start_line = getattr(node, "lineno", 1)  # 当前赋值语句起始行，优先承载尾注释

    # 多行赋值通常在末行追加尾注释。
    int_end_line = getattr(node, "end_lineno", int_start_line)  # 当前赋值语句结束行，也可能承载尾注释

    # 统一收集首尾候选行，后续逐行检查是否存在合格尾注释。
    tuple_candidate_lines = (int_start_line, int_end_line)  # 首尾两端的尾注释候选行号

    # 按行扫描源码，定位空行、注释和语句边界。
    for int_line_number in tuple_candidate_lines:

        # 多行赋值的候选行可能越界，越界行没有尾注释可读。
        if not (1 <= int_line_number <= len(lines)):

            # 越界候选行没有可读源码，直接跳到下一候选。
            continue

        # 没有中文尾注释的候选行交给 PG033 缺失检查处理。
        if not line_has_inline_chinese_comment(int_line_number, lines, comment_positions):

            # 这一候选行没有中文尾注释，继续尝试另一端行号。
            continue

        # 提取当前候选行的尾注释文本，复用具体性规则做语义判定。
        str_inline_comment = comment_positions[int_line_number][1]  # 当前候选行的行内注释文本

        # 一旦命中中文尾注释，就立即复用具体性规则做最终判定。
        return comment_has_specific_purpose(
            str_inline_comment,
            config.min_inline_assignment_comment_chinese_chars,
            config,
        )

    # 所有候选行都没有可判定的中文尾注释时，具体性检查默认通过。
    return True

# 赋值上方注释必须说明变量的领域目的，而不是重复赋值动作。
def assignment_above_comment_is_specific(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
    config: ProfileConfig,
    lines: list[str],
    comments: dict[int, str],
) -> bool:
    """判断赋值语句上方的中文注释是否足够具体。

    参数:
        node: 当前遍历到的 AST 节点。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comments: 源码行号到普通注释文本的映射。

    返回:
        True 表示没有上方注释可检查，或最近中文注释通过具体性检查。
    """

    # 上方注释组必须紧贴赋值语句起始行之前。
    int_start_line = getattr(node, "lineno", 1)  # 当前赋值语句起始行，用来回看前置说明

    # 返回的行号组描述赋值语句前连续注释块。
    tuple_comment_line_numbers, tuple__previous_index = collect_comment_group_before_line(lines, int_start_line)  # 上方连续注释行号组

    # 只选择中文注释参与具体性判断，英文工具注释不算说明。
    list_chinese_comment_numbers = [  # 上方中文注释行号
        line_number  # 仅保留带中文语义的前置注释行号
        for line_number in tuple_comment_line_numbers  # 逐行检查紧邻赋值的注释块
        if has_chinese_text(comments.get(line_number, ""))  # 过滤掉非中文或空注释
    ]

    # 没有中文前置注释时，本函数不额外报告具体性问题。
    if not list_chinese_comment_numbers:

        # 缺少中文前置注释的场景交给 PG035 缺失检查处理。
        return True

    # 只要有一条中文前置注释达到最小具体性要求，就视为当前赋值合规。
    return any(
        comment_has_specific_purpose(
            comments.get(line_number, ""),
            config.min_above_assignment_comment_chinese_chars,
            config,
        )
        for line_number in list_chinese_comment_numbers
    )

# `check_single_assignment_comment_quality` 检查并登记singleassignment注释质量门。
def check_single_assignment_comment_quality(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,

    # 源码行和注释索引用于同时检查赋值上方注释与右侧尾注释。
    lines: list[str],
    comments: dict[int, str],
    comment_positions: dict[int, tuple[int, str]],
) -> None:
    """检查 single_assignment_comment_quality 对应的质量门约束。

    参数:
        node: 当前遍历到的 AST 节点。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comments: 源码行号到普通注释文本的映射。
        comment_positions: 源码行号到注释列位置和注释文本的映射。

    返回:
        None：当前函数只在注释语义过于空泛时向 `issues` 追加 PG037。
    """

    # 报告 PG037 时定位到赋值语句起始行。
    int_start_line = getattr(node, "lineno", 1)  # 当前赋值语句起始行，也是 PG037 的定位行

    # 右侧尾注释存在但语义太弱时，登记 PG037。
    if not assignment_inline_comment_is_specific(node, config, lines, comment_positions):

        # 对语义空泛的尾注释登记 PG037。
        add_issue(
            issues,
            "PG037",
            style_issue_level(config, "PG037"),
            filepath,
            int_start_line,
            "Assignment inline comment is too vague; explain this value's purpose in the current context.",
        )

    # 前置目的注释存在但过于空泛时，登记 PG037。
    if not assignment_above_comment_is_specific(node, config, lines, comments):

        # 对语义空泛的上方目的注释登记 PG037。
        add_issue(
            issues,
            "PG037",
            style_issue_level(config, "PG037"),
            filepath,
            int_start_line,
            "Assignment block comment is too vague; explain the variable's function or mathematical/business meaning.",
        )

# 赋值注释质量主流程负责遍历赋值节点并编排具体性子检查。
def check_assignment_comment_quality(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,

    # 当前文件的源码文本和注释索引支撑所有赋值节点的具体性检查。
    lines: list[str],
    comments: dict[int, str],
    comment_positions: dict[int, tuple[int, str]],
) -> None:
    """检查 assignment_comment_quality 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comments: 源码行号到普通注释文本的映射。
        comment_positions: 源码行号到注释列位置和注释文本的映射。

    返回:
        None：当前函数只调度子检查并把发现项写入 `issues`。
    """

    # 遍历所有 AST 节点并筛出赋值语句。
    for node in ast.walk(tree):

        # 非赋值节点不参与赋值注释具体性规则。
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):

            # 只有赋值类节点才需要继续进入注释质量子检查。
            continue

        # 执行子规则检查，保持主流程只负责编排顺序。
        check_single_assignment_comment_quality(
            node, filepath, issues, config, lines, comments, comment_positions
        )

# PG037 需要知道空行后说明注释的位置以及真正的下方代码行。
def collect_blank_line_comment_context(
    lines: list[str],
    index: int,
    string_literal_lines: set[int],
    assignment_start_lines: set[int],
) -> tuple[int, list[int], int] | None:
    """整理空行块说明注释的上下文。

    参数:
        lines: 按行读取的源码文本。
        index: 源码行索引
        string_literal_lines: 由字符串字面量占用的源码行集合。
        assignment_start_lines: 赋值语句起始行号集合。

    返回:
        上方代码行索引、说明注释行号列表和下方代码行索引；不构成代码块时返回 None。
    """

    # 记录空行块上方最近的有效代码行，供块注释规则判断是否存在代码边界。
    int_previous_index = previous_significant_line_index(lines, index - 1)  # 空行块上方最近的有效代码行索引

    # 跳过连续空行，定位真正的下方候选内容起点。
    while index < len(lines) and is_blank_line(lines[index]):

        # 空行组可能连续出现，跳到下一个非空行继续判断。
        index += 1  # 推进到下一个待判定的源码位置

    # 空行后紧邻的连续注释行就是候选块说明。
    tuple_comment_line_numbers, tuple_next_index = collect_comment_group_after_blank(lines, index)  # 候选注释行号及其后首个有效代码索引

    # 不构成“空行分隔代码块”的位置不需要块注释。
    if not blank_line_block_needs_comment(lines, int_previous_index, tuple_next_index, string_literal_lines):

        # 不是独立代码块边界时不生成块注释上下文。
        return None

    # 还原下一段代码的一基行号，供赋值边界和报错定位复用。
    int_next_line_number = tuple_next_index + 1  # 空行块下方第一条有效代码的一基行号

    # 没有候选块注释或下方是赋值语句时，交给专门规则处理。
    if not tuple_comment_line_numbers or int_next_line_number in assignment_start_lines:

        # 赋值节点或无注释候选的场景由其他规则单独处理。
        return None

    # 返回块注释检查所需的完整上下文。
    return tuple_next_index, tuple_comment_line_numbers, int_next_line_number

# 空行分隔后的块注释必须解释下方代码块的真实职责。
def blank_line_comment_is_specific(
    config: ProfileConfig,
    comments: dict[int, str],
    lines: list[str],
    comment_line_numbers: list[int],
    next_index: int,
) -> bool:
    """判断空行分隔代码块前的中文注释是否足够具体。

    参数:
        config: 控制 profile、风格开关和阈值的质量门配置。
        comments: 源码行号到普通注释文本的映射。
        lines: 按行读取的源码文本。
        comment_line_numbers: 空行后、代码块前的连续注释行号。
        next_index: 下方代码块第一行的零基索引。

    返回:
        True 表示最近中文块注释通过具体性检查。
    """

    # 只有中文注释能满足 current-project 的块说明要求。
    list_chinese_comment_numbers = [  # 块说明中文注释行号
        line_number  # 记录真正具备中文语义的块说明注释行号
        for line_number in comment_line_numbers  # 逐条过滤空行后的连续注释
        if has_chinese_text(comments.get(line_number, ""))  # 仅保留中文说明注释
    ]

    # 没有中文块注释时，本函数不额外报告具体性问题。
    if not list_chinese_comment_numbers:

        # 缺少中文块注释的场景交给 PG031 缺失检查处理。
        return True

    # 短导入组注释允许低于普通块说明长度。
    if config.allow_short_import_group_comments and any(
        is_import_group_comment(comments.get(line_number, ""), lines[next_index])
        for line_number in list_chinese_comment_numbers
    ):

        # 导入组短注释命中白名单时不再强求普通块注释长度。
        return True

    # 其余块注释至少要有一条达到最小具体性要求。
    return any(
        comment_has_specific_purpose(
            comments.get(line_number, ""),
            config.min_block_comment_chinese_chars,
            config,
        )
        for line_number in list_chinese_comment_numbers
    )

# `check_blank_line_comment_quality` 检查并登记空行源码行注释质量门。
def check_blank_line_comment_quality(
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],

    # 注释和豁免行集合用于判断空行后代码块是否已有有效块说明。
    comments: dict[int, str],
    string_literal_lines: set[int],
    assignment_start_lines: set[int],
) -> None:
    """检查 blank_line_comment_quality 对应的质量门约束。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comments: 源码行号到普通注释文本的映射。
        string_literal_lines: 由字符串字面量占用的源码行集合。
        assignment_start_lines: 赋值语句起始行号集合。

    返回:
        无返回值；本函数只在块注释语义过泛时向 issues 追加 PG037。
    """

    # 从文件首行开始顺序扫描每一个可能形成块分隔的空行。
    int_index = 0  # 当前空行扫描游标

    # 主循环逐行寻找“空行 + 注释块 + 下方代码块”的结构。
    while int_index < len(lines):

        # 非空白行不是块分隔点，移动到下一行。
        if not is_blank_line(lines[int_index]):

            # 普通源码行只推进扫描游标。
            int_index += 1  # 向后移动到下一行继续寻找空行边界

            # 当前行不是空白分隔点，直接继续主循环。
            continue

        # 空行上下文描述候选块注释和下方代码块起点。
        tuple_context = collect_blank_line_comment_context(  # 空行块注释上下文
            lines,  # 整个文件的源码行文本
            int_index,  # 当前空行对应的零基行索引
            string_literal_lines,  # 需要跳过的字符串字面量占用行
            assignment_start_lines,  # 赋值起始行用于豁免赋值相关块注释
        )

        # 当前空行不形成需要检查的代码块说明时，继续扫描。
        if tuple_context is None:

            # 未形成块上下文时仅向后推进一行。
            int_index += 1  # 跳过当前空行，继续查找后续块边界

            # 不构成块注释上下文时无需继续处理这一轮。
            continue

        # context 指向空行后的注释组和下方代码块首行。
        int_next_index, list_comment_line_numbers, int_next_line_number = tuple_context  # 下方代码块定位上下文

        # 候选块注释过短或过泛时登记 PG037。
        if not blank_line_comment_is_specific(config, comments, lines, list_comment_line_numbers, int_next_index):

            # 对语义过泛的块说明注释登记 PG037。
            add_issue(
                issues,
                "PG037",
                style_issue_level(config, "PG037"),
                filepath,
                list_comment_line_numbers[-1],
                "Block comment after a blank line is too vague; explain the lower code block's concrete purpose.",
            )

        # 跳到下方代码块附近继续扫描，避免在同一空行组上重复报错。
        int_index = max(int_next_index, int_index + 1)  # 下一轮扫描起点

# PG037 汇总入口会先准备共享上下文，再调度两类具体性子检查。
def check_specific_purpose_comments(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,

    # 源码、注释 token 与字符串字面量行号共同决定具体性检查范围。
    lines: list[str],
    comment_positions: dict[int, tuple[int, str]],
    string_literal_lines: set[int],
) -> None:
    """检查 specific_purpose_comments 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comment_positions: 源码行号到注释列位置和注释文本的映射。
        string_literal_lines: 由字符串字面量占用的源码行集合。

    返回:
        无返回值；本函数只负责编排 PG037 相关的各类具体性子检查。
    """

    # 未启用具体性规则时，不检查 PG037。
    if not config.require_specific_purpose_comments:

        # 关闭具体性规则时无需继续准备任何附加上下文。
        return

    # 整理注释位置和文本，供当前规则检查注释质量。
    dict_comments = {line_number: text for line_number, (_, text) in comment_positions.items()}  # 注释集合

    # 先检查赋值语句的上方说明和尾注释是否足够具体。
    check_assignment_comment_quality(
        tree, filepath, issues, config, lines, dict_comments, comment_positions
    )

    # 汇总所有赋值起始行，供块注释检查豁免赋值专属说明场景。
    set_assignment_start_lines = {  # 赋值语句起始行号集合
        getattr(node, "lineno", 0)  # 读取每个赋值节点的一基起始行号
        for node in ast.walk(tree)  # 遍历整棵语法树收集赋值节点
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))  # 仅保留赋值类节点
    }

    # 再检查空行块注释是否具体说明了下方普通代码块的职责。
    check_blank_line_comment_quality(
        filepath, issues, config, lines, dict_comments, string_literal_lines, set_assignment_start_lines
    )

# 收集docstring源码行
