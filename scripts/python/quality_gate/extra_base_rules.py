"""承载基础规则中的脚本入口、断言和重复结构检查。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import ast
import re
from pathlib import Path

# 导入规则常量，供脚本侧效应和随机种子判定复用
from .ast_helpers import RANDOM_SEED_CALLS, SCRIPT_SIDE_EFFECT_CALLS

# 导入 AST 辅助函数，供拆分后的基础规则复用
from .ast_helpers import (
    add_issue,
    ancestor_is_main_guard,
    get_call_name,
    is_project_script_file,
    is_main_guard,
    is_public_name,
)

# 导入基础标注文本转换逻辑
from .base_rules import annotation_to_text

# 导入 profile 和报告结构
from .profiles import ProfileConfig
from .report import Issue

# 在非 notebook profile 下拦截裸 assert 运行期校验。
def check_assert_usage(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """检查 assert_usage 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无；命中规则时直接向 issues 列表追加发现项。
    """

    # 比较结果决定当前规则是否需要登记问题。
    if config.name == "notebook":

        # notebook 探索代码允许直接使用 assert。
        return

    # 扫描 AST 节点，寻找本规则关注的语法结构。
    for node in ast.walk(tree):

        # 确认候选节点具备目标 AST 结构后读取专属字段。
        if isinstance(node, ast.Assert):

            # 把 notebook 之外的 assert 校验登记为可读性问题。
            add_issue(
                issues,
                "PG023",
                "BLOCKER" if config.name in {"library", "scientific", "test"} else "WARNING",
                filepath,
                node.lineno,
                "Use framework assertions outside notebooks.",
            )

# `report_wildcard_import` 处理wildcard导入。
def report_wildcard_import(node: ast.ImportFrom, filepath: Path, issues: list[Issue]) -> None:
    """报告wildcard导入。

    参数:
        node: 当前遍历到的 AST 节点。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；直接向 issues 列表追加通配符导入警告。
    """

    # 把通配符导入记录为依赖边界不清晰的可读性问题。
    add_issue(
        issues,
        "PG016",
        "WARNING",
        filepath,
        node.lineno,
        "Wildcard import hides dependencies and hurts readability.",
    )

# `check_wildcard_import` 检查并登记wildcard导入。
def check_wildcard_import(tree: ast.AST, filepath: Path, issues: list[Issue]) -> None:
    """检查 wildcard_import 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；命中规则时直接向 issues 列表追加发现项。
    """

    # 遍历整棵 AST，定位所有 from-import 语句。
    for node in ast.walk(tree):

        # 只有 from-import 语句可能包含通配符导入。
        if not isinstance(node, ast.ImportFrom):

            # 不是 from-import 的节点不可能触发该规则。
            continue

        # alias.name 为星号时表示 `from x import *`。
        if any(alias.name == "*" for alias in node.names):

            # 通配符导入会污染命名空间，需要单独报告。
            report_wildcard_import(node, filepath, issues)

# `check_duplicate_public_definitions` 检查并登记duplicate公开符号definitions。
def check_duplicate_public_definitions(
    tree: ast.Module,
    filepath: Path,
    issues: list[Issue],
) -> None:
    """检查 duplicate_public_definitions 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；发现重复公开定义时直接向 issues 列表追加警告。
    """

    # 记录模块顶层公开定义首次出现的行号。
    dict_names: dict[str, int] = {}  # 映射名称集合

    # 只检查模块直接成员，嵌套定义不属于公开模块 API。
    for node in tree.body:

        # 只有函数、异步函数和类会定义公开符号名称。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):

            # 非定义节点不会引入新的模块公开符号。
            continue

        # 私有名称不会出现在模块公开 API 重复检查中。
        if not is_public_name(node.name):

            # 私有名称重复不会污染模块对外公开接口。
            continue

        # 同名公开定义再次出现时会覆盖先前模块符号。
        if node.name in dict_names:

            # 记录同名公开定义覆盖前值的风险。
            add_issue(
                issues,
                "PG017",
                "WARNING",
                filepath,
                node.lineno,
                f"Public definition `{node.name}` is repeated and overwritten.",
            )

        # 保存首次出现行号，后续同名定义会覆盖该公开符号。
        dict_names[node.name] = node.lineno  # 公开定义首次行号

# 嵌套循环变量复用规则只关注简单名称目标。
def get_for_target_name(target: ast.AST) -> str:
    """取得 for 循环目标的简单变量名。

    参数:
        target: for 语句左侧的目标节点。

    返回:
        简单名称目标返回变量名；解包或属性目标返回空字符串。
    """

    # 只有简单名称目标才有可复用的循环变量名。
    if isinstance(target, ast.Name):

        # 直接返回 for 目标使用的变量名文本。
        return target.id

    # 解包、属性等复杂目标统一视为无简单变量名。
    return ""

# 访问器用栈记录外层循环变量，发现内层是否复用同名变量。
class LoopVariableVisitor(ast.NodeVisitor):
    """在遍历过程中检查嵌套 for 循环变量复用。"""

    # 每个文件使用独立访问器，避免不同文件的循环栈串扰。
    def __init__(self, filepath: Path, issues: list[Issue]) -> None:
        """保存当前文件的问题登记位置和循环变量栈。

        参数:
            filepath: 需要读取、解析或检查的 Python 文件路径。
            issues: 用于累计当前质量门发现项的列表。

        返回:
            无；构造阶段只初始化访问器状态。
        """

        # 解析文件系统位置，保证后续读写使用一致路径。
        self.filepath = filepath  # 当前规则判定所需状态

        # visitor 在遍历过程中直接把循环变量复用问题写入该列表。
        self.issues = issues  # 质量门发现项集合

        # 栈顶表示当前遍历路径最内层的 for 目标名称。
        self.loop_stack: list[str] = []  # 嵌套 for 变量名栈

    # 进入 for 节点时比对外层变量，离开节点时恢复栈。
    def visit_For(self, node: ast.For) -> None:
        """检查当前 for 节点是否复用外层循环变量。

        参数:
            node: 当前遍历到的 for 语句节点。

        返回:
            无；命中规则时直接向 issues 列表追加警告。
        """

        # 循环目标名用于检测内层循环是否覆盖外层变量。
        str_target_name = get_for_target_name(node.target)  # 文本目标名称

        # 内层 for 复用外层变量时会隐藏外层循环状态。
        if str_target_name and str_target_name in self.loop_stack:

            # 记录内层循环复用了外层变量名的可读性风险。
            add_issue(
                self.issues,
                "PG018",
                "WARNING",
                self.filepath,
                node.lineno,
                f"Nested loop reuses variable `{str_target_name}`; use a distinct name.",
            )

        # 有可识别目标名时入栈，供子循环比较。
        if str_target_name:

            # 栈顺序表示从外到内的循环变量路径。
            self.loop_stack.append(str_target_name)

        # 深入当前循环体，继续检查更内层的嵌套循环。
        self.generic_visit(node)

        # 离开 for 节点后恢复到外层循环变量路径。
        if str_target_name:

            # 退出当前嵌套层级，恢复外层遍历状态。
            self.loop_stack.pop()

# `check_nested_loop_variable_reuse` 检查并登记嵌套loopvariablereuse。
def check_nested_loop_variable_reuse(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
) -> None:
    """检查 nested_loop_variable_reuse 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；访问器命中规则时直接向 issues 列表追加警告。
    """

    # 访问器维护循环变量栈，用来发现内层复用外层名称。
    loop_variable_visitor_visitor: LoopVariableVisitor = LoopVariableVisitor(filepath, issues)  # 循环变量复用检查器

    # 从模块根节点启动访问器，扫描全部嵌套 for 语句。
    loop_variable_visitor_visitor.visit(tree)

# `check_magic_number_cluster` 检查并登记魔法数编号cluster。
def check_magic_number_cluster(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """检查 magic_number_cluster 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无；超过阈值时直接向 issues 列表追加发现项。
    """

    # 常见哨兵数值和布尔等价数值不会降低函数可读性。
    set_ignored_numbers = {0, 1, -1, 0.0, 1.0, -1.0}  # 魔法数聚类检查忽略值

    # 逐个检查函数节点，统计其中未命名数值常量的密度。
    for node in ast.walk(tree):

        # 只有调用表达式可能设置随机种子。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 只对函数和异步函数统计魔法数聚集情况。
            continue

        # 收集函数体内非哨兵数值字面量，用于判断参数表混入代码。
        list_numbers = []  # 非哨兵数值字面量集合

        # 深入当前函数节点，统计函数体里出现的数值字面量。
        for child in ast.walk(node):

            # 只有常量节点才可能携带数值字面量。
            if not isinstance(child, ast.Constant):

                # 非常量节点不会直接携带需要计数的字面量。
                continue

            # 布尔值和非数值常量不应计入魔法数统计。
            if isinstance(child.value, bool) or not isinstance(child.value, (int, float)):

                # 排除布尔和非数值常量，避免误报普通语义节点。
                continue

            # 0、1、-1 等哨兵值不按魔法数处理。
            if child.value in set_ignored_numbers:

                # 常见哨兵值不会单独降低函数可读性。
                continue

            # 非哨兵数值用于衡量函数内参数表密度。
            list_numbers.append(child.value)

        # 数值字面量过多通常意味着实验参数表混入函数体。
        if len(list_numbers) > config.block_magic_number_count:

            # 对超出阻断阈值的函数登记魔法数聚集问题。
            add_issue(
                issues,
                "PG012",
                "BLOCKER",
                filepath,
                node.lineno,
                f"Function `{node.name}` has {len(list_numbers)} unnamed numeric constants.",
            )

        # 数量超过提醒阈值时先给出可读性预警。
        elif len(list_numbers) > config.warn_magic_number_count:

            # 对超过提醒阈值的函数登记魔法数预警。
            add_issue(
                issues,
                "PG012",
                "WARNING",
                filepath,
                node.lineno,
                f"Function `{node.name}` has {len(list_numbers)} unnamed numeric constants.",
            )

# 脚本逻辑泄漏规则只适用于核心库或科学计算模块。
def should_skip_script_logic_check(filepath: Path, config: ProfileConfig) -> bool:
    """判断当前文件是否不适用 PG013 核心库脚本逻辑规则。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        True 表示当前文件不应报告核心库脚本逻辑泄漏。
    """

    # notebook/script profile 本身允许入口层逻辑。
    if config.name not in {"library", "scientific"}:

        # 这些 profile 不执行 PG013 核心库约束。
        return True

    # scripts/python 是工具脚本边界，main guard 和输出副作用属于入口职责。
    if is_project_script_file(filepath):

        # 脚本目录下的 CLI 文件不属于核心库模块。
        return True

    # Python 约定的包级执行入口允许承载极薄 main guard。
    if filepath.name == "__main__.py":

        # `python -m package` 入口不是普通库模块的脚本逻辑泄漏。
        return True

    # 当前文件处于核心库边界，需要继续检查脚本逻辑泄漏。
    return False

# main guard 出现在核心库模块时报告 PG013。
def report_main_guard_in_core_module(tree: ast.Module, filepath: Path, issues: list[Issue]) -> None:
    """报告核心库模块中的 main guard 运行逻辑。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；命中规则时直接向 issues 列表追加 PG013 发现项。
    """

    # main guard 只可能出现在模块顶层语句中。
    for node in tree.body:

        # 找到入口保护后检查其中是否承载过多脚本职责。
        if is_main_guard(node):

            # main guard 内容越多，越说明模块混入了脚本层职责。
            int_body_length = len(getattr(node, "body", []))  # main guard 语句数量

            # 简短入口提示为 warning，较长运行逻辑直接阻断。
            str_level = "WARNING" if int_body_length <= 3 else "BLOCKER"  # PG013 严重级别

            # 记录核心模块混入 main guard 运行逻辑的边界泄漏。
            add_issue(
                issues,
                "PG013",
                str_level,
                filepath,
                getattr(node, "lineno", 1),
                "Core module contains main-guard runtime logic; move it to scripts/.",
            )

# import 期或函数体内的脚本层调用不应留在核心库模块。
def report_script_side_effect_calls(
    tree: ast.Module,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """报告核心库模块中的终端输出和展示副作用调用。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无；命中规则时直接向 issues 列表追加 PG013 警告。
    """

    # 遍历整棵 AST，定位脚本层终端输出与展示调用。
    for node in ast.walk(tree):

        # 只有调用表达式可能触发脚本层输出副作用。
        if not isinstance(node, ast.Call):

            # 非调用节点不会触发终端输出或展示副作用。
            continue

        # 保存调用表达式名称，识别导入期副作用和输出副作用。
        str_call_name = get_call_name(node.func)  # 当前调用的规范化名称

        # 非脚本输出调用不属于 PG024 检查范围。
        if str_call_name not in SCRIPT_SIDE_EFFECT_CALLS:

            # 与脚本层副作用无关的调用不在本规则处理范围。
            continue

        # profile 禁止 print 时，print 由更专门的规则处理。
        if str_call_name == "print" and not config.allow_print:

            # 当前 profile 已有更专门的 print 规则负责处理。
            continue

        # profile 禁止 plt.show 时，绘图展示由更专门的规则处理。
        if str_call_name == "plt.show" and not config.allow_plot_show:

            # 当前 profile 已有更专门的绘图展示规则负责处理。
            continue

        # 记录核心模块仍然保留脚本层副作用调用的位置。
        add_issue(
            issues,
            "PG013",
            "WARNING",
            filepath,
            getattr(node, "lineno", 1),
            f"Core module contains script-layer call `{str_call_name}`; split responsibilities.",
        )

# `check_script_logic_in_core_module` 检查并登记scriptlogicincore模块。
def check_script_logic_in_core_module(
    tree: ast.Module,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """检查 script_logic_in_core_module 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无；命中规则时直接向 issues 列表追加 PG013 相关发现项。
    """

    # 非核心库边界不报告脚本入口和终端输出职责。
    if should_skip_script_logic_check(filepath, config):

        # 当前文件位于脚本边界或 profile 已允许入口逻辑。
        return

    # main guard 和脚本层调用分开检查，避免主函数承担过多分支。
    report_main_guard_in_core_module(tree, filepath, issues)

    # 终端输出、绘图展示等副作用也按核心库边界单独登记。
    report_script_side_effect_calls(tree, filepath, issues, config)

# `has_nearby_reproducibility_comment` 判断nearbyreproducibility注释。
def has_nearby_reproducibility_comment(line: int, comments: dict[int, str]) -> bool:
    """判断当前对象是否具备 nearby_reproducibility_comment。

    参数:
        line: 当前规则正在解析的单行文本。
        comments: 源码行号到普通注释文本的映射。

    返回:
        返回当前对象是否具备目标特征。
    """

    # 随机种子附近需要出现这些词，说明为什么固定随机性。
    tuple_keywords = ("复现", "可复现", "reproduc", "seed", "随机")  # 可复现性说明关键词

    # 只检查随机种子调用附近几行，避免远处注释误覆盖。
    for offset in range(-3, 3):

        # 只检查调用点附近注释，避免远处说明误覆盖当前随机种子。
        str_comment = comments.get(line + offset, "")  # 相邻注释文本

        # 非随机种子调用不需要复现性说明。
        if any(keyword in str_comment.lower() for keyword in tuple_keywords):

            # 一旦发现复现性关键词，就可以确认邻近说明已经存在。
            return True

    # 周围几行都没有出现复现性说明时，返回未命中状态。
    return False

# `check_random_seed_policy` 检查并登记randomseedpolicy。
def check_random_seed_policy(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    comments: dict[int, str],
) -> None:
    """检查 random_seed_policy 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        comments: 源码行号到普通注释文本的映射。

    返回:
        无；命中规则时直接向 issues 列表追加随机种子说明缺失告警。
    """

    # 遍历所有调用节点，检查随机种子设置是否配有复现说明。
    for node in ast.walk(tree):

        # 只有函数调用节点才可能真正设置随机种子。
        if not isinstance(node, ast.Call):

            # 非调用节点不会触发随机种子规则。
            continue

        # 先把调用对象标准化成名称文本，便于识别 random.seed 一类入口。
        str_call_name = get_call_name(node.func)  # 调用名称

        # 只有随机种子 API 才需要检查邻近复现性说明。
        if str_call_name not in RANDOM_SEED_CALLS:

            # 非随机种子调用不属于本规则的检查对象。
            continue

        # 计算源码行位置，定位相邻语句和注释。
        int_line = getattr(node, "lineno", 1)  # 问题定位行号

        # 缺少邻近复现性说明时登记随机种子风险。
        if not has_nearby_reproducibility_comment(int_line, comments):

            # 记录缺少复现性说明的随机种子调用。
            add_issue(
                issues,
                "PG015",
                "WARNING",
                filepath,
                int_line,
                f"Random seed `{str_call_name}` lacks a reproducibility note.",
            )

# `check_type_ignore_reason` 检查并登记类型ignorereason。
def check_type_ignore_reason(filepath: Path, issues: list[Issue], lines: list[str]) -> None:
    """检查 type_ignore_reason 对应的质量门约束。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        lines: 按行读取的源码文本。

    返回:
        无；命中规则时直接向 issues 列表追加 PG019 警告。
    """

    # 按行扫描源码，定位空行、注释和语句边界。
    for int_index, str_line in enumerate(lines, start=1):

        # 用拆分字面量定位 type ignore 标记，避免本规则误判当前实现源码。
        int_ignore_index = str_line.find("type:" + " ignore")  # type ignore 起始位置

        # 没有类型忽略标记的行不需要 PG016 检查。
        if int_ignore_index < 0:

            # 不含类型忽略标记的源码行无需继续检查。
            continue

        # 记录当前行第一个井号的位置，判断它是否真是注释。
        int_comment_index = str_line.find("#")  # 注释位置

        # 注释符号必须出现在 ignore 之前才是真正的类型忽略注释。
        if int_comment_index < 0 or int_comment_index > int_ignore_index:

            # 井号落在 ignore 之后时，这一行并没有真正附带类型忽略注释。
            continue

        # 带错误码的 ignore 才能避免压掉无关类型问题。
        bool_has_error_code = re.search(r"type:\s*ignore\[[^\]]+\]", str_line) is not None  # type ignore 错误码存在性

        # ignore 必须解释保留原因，便于后续删除或收窄。
        bool_has_reason = "reason:" in str_line.lower() or "原因" in str_line or "because" in str_line.lower()  # type ignore 原因存在性

        # 缺少错误码或原因时无法判断忽略是否仍然必要。
        if not bool_has_error_code or not bool_has_reason:

            # 记录缺少错误码或原因说明的 type ignore。
            add_issue(
                issues,
                "PG019",
                "WARNING",
                filepath,
                int_index,
                "`type: ignore` needs an error code and reason.",
            )

# 检查 except 子句是否过宽，或被 pass 静默吞掉。
def check_bare_and_swallowed_exceptions(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
) -> None:
    """检查 bare_and_swallowed_exceptions 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无；命中规则时直接向 issues 列表追加异常处理相关发现项。
    """

    # 遍历整棵 AST，检查 except 子句是否过宽或被静默吞掉。
    for node in ast.walk(tree):

        # 只有 except 子句可能捕获异常。
        if not isinstance(node, ast.ExceptHandler):

            # 非 except 节点不参与异常处理策略检查。
            continue

        # 裸 except 会吞掉 KeyboardInterrupt/SystemExit 等非业务异常。
        if node.type is None:

            # 记录会吞掉所有异常的裸 except 用法。
            add_issue(
                issues,
                "PG021",
                "BLOCKER",
                filepath,
                node.lineno,
                "Bare `except:` is forbidden; catch a specific exception.",
            )

            # 裸 except 已经确定违规，不再继续分析其异常类型文本。
            continue

        # 保存标识符名称，检查命名规则和重命名建议。
        str_type_name = annotation_to_text(node.type)  # 文本类型名称

        # 宽泛异常只包含 pass 时会静默吞掉真实故障。
        bool_body_is_only_pass = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)  # 异常处理体是否仅 pass

        # 相同公开名称再次出现会覆盖前一个定义。
        if str_type_name in {"Exception", "BaseException"} and bool_body_is_only_pass:

            # 记录宽泛异常被 pass 静默吞掉的维护风险。
            add_issue(
                issues,
                "PG022",
                "WARNING",
                filepath,
                node.lineno,
                "Broad exception is swallowed with pass; handle or re-raise explicitly.",
            )

