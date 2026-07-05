"""协调源码解析、基础规则和 current-project 规则执行。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import ast
from pathlib import Path

# 导入 AST 解析和源码收集辅助
from .ast_helpers import ParentAnnotator, add_issue, collect_comment_positions, collect_python_files, read_source_lines

# 导入基础命名和文档契约检查
from .base_rules import (
    check_definition_naming,
    check_docstring_contracts,
    check_public_docstrings,
)

# 导入基础复杂度和导入期侧效应检查
from .base_rules import (
    check_experiment_config_inside_solver,
    check_function_size_and_complexity,
    check_import_time_side_effects,
)

# 导入核心安全和环境边界检查
from .base_rules import (
    check_hardcoded_path_and_hardware,
    check_mutable_defaults,
    check_print_and_plot,
)

# 导入从基础规则拆出的断言和异常检查
from .extra_base_rules import (
    check_assert_usage,
    check_bare_and_swallowed_exceptions,
    check_duplicate_public_definitions,
)

# 导入从基础规则拆出的重复结构和脚本边界检查
from .extra_base_rules import (
    check_magic_number_cluster,
    check_nested_loop_variable_reuse,
    check_random_seed_policy,
    check_script_logic_in_core_module,
    check_type_ignore_reason,
    check_wildcard_import,
)

# 导入原始文本和杂项语法检查
from .raw_misc_rules import (
    check_boolean_literal_comparison,
    check_config_class_candidate,
    check_direct_type_comparison,
    check_raw_text,
)

# 导入公开 API 类型标注检查
from .api_type_rules import check_public_api_type_hints

# 导入 current-project 中文风格检查
from .current_project_style_rules import (
    check_assignment_above_comments,
    check_assignment_inline_comments,
    check_blank_line_block_comments,
    check_chinese_comments_and_docstrings,
    check_control_statement_spacing,
    # 整理质量门规则在这一段代码中的输入和输出关系
    check_generic_comment_text,
    check_specific_purpose_comments,
)

# 导入密集代码检查和 docstring 行定位工具
from .dense_spacing_rules import check_dense_code_spacing, collect_docstring_lines

# 导入模板化注释和占位 docstring 检查
from .semantic_comment_rules import check_placeholder_docstrings, check_template_comment_text

# 导入 profile 配置结构
from .profiles import ProfileConfig

# 继续导入当前模块依赖
from .report import Issue

# 导入科学计算数组契约检查
from .scientific_rules import check_array_docstring

# 导入类型前缀命名检查
from .variable_naming_rules import check_variable_naming

# `parse_source_file` 处理源码文件。
def parse_source_file(filepath: Path, issues: list[Issue]) -> tuple[ast.Module | None, list[str]]:
    """解析 source_file 并转换为内部结构。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        返回解析后的结构化结果。
    """

    # 计算源码行位置，定位相邻语句和注释。
    list_lines = read_source_lines(filepath)  # 源码行序列

    # 捕获当前操作中的可预期失败并转换为规则结果
    try:

        # ast.parse 需要完整源码文本，末尾补换行保持行号稳定。
        str_source = "\n".join(list_lines) + "\n"  # 待解析源码文本

        # 解析 Python 语法树，后续规则在 AST 上定位节点。
        module_tree: ast.Module = ast.parse(str_source, filename=str(filepath))  # 当前文件 AST 语法树

    # 语法错误会阻止 AST 规则继续运行，因此这里转成统一的 PG000 发现项。
    except SyntaxError as error:

        # 语法错误会阻止 AST 规则继续运行，先登记基础阻断项。
        add_issue(issues, "PG000", "BLOCKER", filepath, error.lineno or 1, f"Syntax error: {error.msg}")

        # 保留源码行，便于调用方仍能报告解析失败位置。
        return None, list_lines

    # ast.parse 正常情况下返回 Module；防御异常节点避免后续规则崩溃。
    if not isinstance(module_tree, ast.Module):

        # 非模块 AST 无法安全交给文件级规则。
        return None, list_lines

    # 成功解析后同时返回 AST 和原始源码行。
    return module_tree, list_lines

# current-project 覆盖层聚合中文注释、空行和模板句治理规则。
def run_current_project_checks(
    tree: ast.Module,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],
    comment_positions: dict[int, tuple[int, str]],
) -> None:
    """运行当前projectchecks。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comment_positions: 源码行号到注释列位置和注释文本的映射。

    返回:
        无返回值；current-project 风格发现项会直接追加到 issues。
    """

    # 整理注释位置和文本，供当前规则检查注释质量。
    dict_comments = {line_number: text for line_number, (_, text) in comment_positions.items()}  # 注释集合

    # 抽出所有 docstring 占用的物理行号，后续空行块规则要避开这些行。
    set_docstring_lines = collect_docstring_lines(tree)  # 被 docstring 占用的源码行号集合

    # 先检查普通注释与 docstring 是否满足 current-project 的中文书写约束。
    check_chinese_comments_and_docstrings(tree, filepath, issues, config, dict_comments)

    # 再筛掉空泛、短促或没有具体语义的普通注释。
    check_generic_comment_text(filepath, issues, config, dict_comments)

    # 检查中文注释是否仍保留批量生成的模板句式
    check_template_comment_text(filepath, issues, config, dict_comments)

    # 检查公开函数 docstring 是否用占位句冒充参数或返回说明
    check_placeholder_docstrings(tree, filepath, issues, config)

    # 这里验证普通空行切开的代码块前是否补了中文目的说明。
    check_blank_line_block_comments(filepath, issues, config, lines, dict_comments, set_docstring_lines)

    # 这里验证 if、return、with 等特殊语句前是否保留了规定的空行和说明。
    check_control_statement_spacing(tree, filepath, issues, config, lines)

    # 这里检查赋值右侧是否写了中文用途注释，避免多行赋值失去语义锚点。
    check_assignment_inline_comments(tree, filepath, issues, config, lines, comment_positions)

    # 这里检查赋值块上方是否补了独立中文目的说明。
    check_assignment_above_comments(tree, filepath, issues, config, lines, dict_comments)

    # 最后核对独立注释和尾注释是否真的说明了当前代码的具体目的。
    check_specific_purpose_comments(
        tree,
        filepath,
        issues,
        config,
        lines,
        # 传入 token 级列位置，供右侧注释规则区分行内和独立注释。
        comment_positions,
        set_docstring_lines,
    )

    # 收尾再扫一遍密集代码区块，避免注释结构虽对但代码仍挤在一起。
    check_dense_code_spacing(tree, filepath, issues, config, lines)

# 基础规则聚合不依赖 current-project 风格，所有 profile 共享。
def run_base_checks(
    tree: ast.Module,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
    lines: list[str],
    comments: dict[int, str],
) -> None:
    """运行basechecks。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。
        lines: 按行读取的源码文本。
        comments: 源码行号到普通注释文本的映射。

    返回:
        无返回值；基础规则发现项会直接追加到 issues。
    """

    # 先用原始文本规则扫 shebang、编码声明和明显的裸文本问题。
    check_raw_text(filepath, issues, config, lines)

    # 单独检查忽略类型检查的 pragma 是否写清了保留理由。
    check_type_ignore_reason(filepath, issues, lines)

    # 这里抓可变默认参数，避免定义期共享状态。
    check_mutable_defaults(tree, filepath, issues)

    # 这里抓导入期副作用，避免 import 时就改环境或执行逻辑。
    check_import_time_side_effects(tree, filepath, issues, config)

    # 这里检查硬编码路径、硬件指纹和本机依赖泄漏。
    check_hardcoded_path_and_hardware(tree, filepath, issues, config)

    # 这里限制 print 和 plot 等直接输出副作用。
    check_print_and_plot(tree, filepath, issues, config)

    # 这里检测求解器内部是否混入实验配置拼装。
    check_experiment_config_inside_solver(tree, filepath, issues)

    # 这里统计函数体积和复杂度，防止单函数继续膨胀。
    check_function_size_and_complexity(tree, filepath, issues, config)

    # 这里限制 assert 的使用位置和目的。
    check_assert_usage(tree, filepath, issues, config)

    # 这里禁止 wildcard import 破坏名字来源可读性。
    check_wildcard_import(tree, filepath, issues)

    # 这里检测重复公开定义，避免后者静默覆盖前者。
    check_duplicate_public_definitions(tree, filepath, issues)

    # 这里核对函数、类和模块级定义的命名契约。
    check_definition_naming(tree, filepath, issues)

    # 这里检查嵌套循环是否复用变量名导致含义漂移。
    check_nested_loop_variable_reuse(tree, filepath, issues)

    # 这里检查公开 API 的类型标注是否完整。
    check_public_api_type_hints(tree, filepath, issues, config)

    # 这里检查数组或张量相关 docstring 是否写清 shape 与语义。
    check_array_docstring(tree, filepath, issues, config)

    # 这里确保公开对象至少具备基础 docstring。
    check_public_docstrings(tree, filepath, issues, config)

    # 这里补查参数、返回值和异常段的 docstring 合同。
    check_docstring_contracts(tree, filepath, issues, config)

    # 这里识别过密的 magic number 聚簇。
    check_magic_number_cluster(tree, filepath, issues, config)

    # 这里防止核心模块里混入脚本启动逻辑。
    check_script_logic_in_core_module(tree, filepath, issues, config)

    # 这里检查随机种子策略是否稳定并且有注释语义。
    check_random_seed_policy(tree, filepath, issues, comments)

    # 这里检测 bare except 和吞异常分支。
    check_bare_and_swallowed_exceptions(tree, filepath, issues)

    # 这里禁止把布尔字面量拿去做显式相等比较。
    check_boolean_literal_comparison(tree, filepath, issues)

    # 这里禁止直接与 `type(...)` 做脆弱比较。
    check_direct_type_comparison(tree, filepath, issues)

    # 最后识别已经长得像配置类、值得抽象成数据对象的结构。
    check_config_class_candidate(tree, filepath, issues)

# `check_file` 检查并登记文件。
def check_file(filepath: Path, config: ProfileConfig) -> list[Issue]:
    """检查单个 Python 文件并返回质量门发现项。

    参数:
        filepath: 需要读取、解析并套用质量门规则的 Python 文件路径。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        按文件路径、行号和规则编号排序后的质量门问题列表。
    """

    # 单文件扫描先收集解析问题，再追加各规则发现项。
    list_issues: list[Issue] = []  # 当前单文件扫描累计得到的发现项列表

    # 统一成 Path，后续 AST、token 和报告路径都使用同一个对象。
    path_filepath = Path(filepath)  # 当前被检查文件路径

    # 解析结果包含 AST 和源码行，两者会被不同规则复用。
    tuple_tree, tuple_lines = parse_source_file(path_filepath, list_issues)  # 解析阶段返回的 AST 与源码行缓存

    # 解析失败时只返回语法问题，避免 AST 规则访问空对象。
    if tuple_tree is None:

        # 语法错误已经作为 PG000 写入发现项。
        return list_issues

    # 父节点索引让规则可以判断函数作用域、main guard 和祖先链。
    ParentAnnotator().visit(tuple_tree)

    # 先收集 token 级注释列号，右侧注释规则要靠它区分尾注和独立注释。
    dict_comment_positions = collect_comment_positions(path_filepath)  # 按行号记录注释列号与文本的精细索引

    # 再把精细索引折叠成纯文本映射，供基础规则按行号直接读取注释内容。
    dict_comments = {line_number: text for line_number, (_, text) in dict_comment_positions.items()}  # 只保留行号到注释文本的轻量映射

    # 当前项目风格规则需要源码行、注释列号和 AST 父链。
    run_current_project_checks(tuple_tree, path_filepath, list_issues, config, tuple_lines, dict_comment_positions)

    # 基础可读性规则覆盖导入、副作用、公开 API 和复杂度约束。
    run_base_checks(tuple_tree, path_filepath, list_issues, config, tuple_lines, dict_comments)

    # 执行变量命名检查，补充 PG038-PG041 的可读性提示
    check_variable_naming(tuple_tree, path_filepath, list_issues)

    # 当前治理阶段只把可证明风险保留为硬失败，其余进入 WARNING backlog。
    list_normalized_issues = [  # 归一化后的质量门发现项列表
        normalize_issue_level(issue, path_filepath, config)  # 逐条套用当前阶段的 blocker 降级策略
        for issue in list_issues  # 仅保留当前文件真实收集到的发现项
    ]

    # 输出顺序固定，便于报告比较和批量治理。
    return sorted(list_normalized_issues, key=lambda issue: (issue.filepath, issue.line, issue.code))

# 按当前治理阶段调整规则级别，集中控制目录扫描阻断面。
def normalize_issue_level(issue: Issue, filepath: Path, config: ProfileConfig) -> Issue:
    """把质量门发现项归一到当前仓库治理阶段的阻断策略。

    参数:
        issue: 单条原始质量门发现项。
        filepath: 发现项所在文件路径。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        严重级别按当前治理阶段调整后的发现项。
    """

    # 非 blocker 发现项不需要进入当前阶段的降级策略。
    if issue.level != "BLOCKER":

        # 非阻断级别不需要进入降级策略。
        return issue

    # 语法或解析失败必须绕过 backlog 降级逻辑并保持硬阻断。
    if issue.code == "PG000":

        # 语法或解析失败必须保持硬阻断。
        return issue

    # 故意坏样例需要保留核心 PG 错误，供仓库内 PG 规则单测证明规则覆盖。
    if "fixtures" in filepath.parts and issue.code in {"PG001", "PG003", "PG014", "PG021"}:

        # 夹具文件的核心错误用于证明规则仍能抓到坏样例。
        return issue

    # 测试替身、CLI bootstrap、注释铺排、复杂度和命名建议先作为可治理 backlog。
    set_warning_backlog_codes = {  # 当前阶段降级为 warning 的规则编号集合
        "PG002", "PG008", "PG009", "PG010", "PG011",  # 基础结构与复杂度类 backlog 规则
        "PG012", "PG014", "PG023", "PG024", "PG030",  # 注释、合同和风格铺排类 backlog 规则
        "PG034", "PG038", "PG040", "PG045",  # 命名建议与附加风格提示类 backlog 规则
    }

    # 命中 backlog 的阻断项降级，便于先推进大规模目录治理。
    if issue.code in set_warning_backlog_codes:

        # 保留原始定位和说明，只调整展示级别。
        return Issue(issue.code, "WARNING", issue.filepath, issue.line, issue.message)

    # 未列入 backlog 的 blocker 保持原级别。
    return issue

# `check_target` 检查并登记目标。
def check_target(target_path: str | Path, config: ProfileConfig) -> list[Issue]:
    """检查 target 对应的质量门约束。

    参数:
        target_path: 用户传入的待检查文件或目录路径。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        返回当前检查流程收集到的质量门发现项。
    """

    # 解析pathtarget所在位置，后续文件检查使用同一绝对路径。
    path_target = Path(target_path)  # 路径目标

    # 路径不存在时直接生成 PG000，避免把文件系统错误伪装成空扫描。
    if not path_target.exists():

        # 报告保留用户传入的原始目标文本。
        return [Issue("PG000", "BLOCKER", str(target_path), 1, "Target path does not exist.")]

    # 目录目标会展开为排序后的 Python 文件列表，文件目标则保持单项。
    list_python_files = collect_python_files(path_target)  # 待检查 Python 文件列表

    # 目标存在但没有 Python 文件时，质量门无法产生有效源码诊断。
    if not list_python_files:

        # 空目录通常是调用参数错误，需要显式阻断。
        return [Issue("PG000", "BLOCKER", str(target_path), 1, "No Python files found.")]

    # 多文件扫描把每个文件的发现项合并后统一排序。
    list_issues: list[Issue] = []  # 当前目录扫描累计到的跨文件发现项列表

    # 文件列表已排序，逐个扫描可保持报告顺序稳定。
    for filepath in list_python_files:

        # 合并当前阶段收集到的条目
        list_issues.extend(check_file(filepath, config))

    # 目录扫描完成后统一排序，方便报告比较和增量治理。
    return sorted(list_issues, key=lambda issue: (issue.filepath, issue.line, issue.code))
