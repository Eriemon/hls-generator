"""实现科学计算代码的数组契约检查。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import ast
import re
from pathlib import Path

# 导入当前模块运行所需的依赖
from .ast_helpers import ARRAY_ANNOTATION_HINTS, ARRAY_NAME_HINTS, add_issue, is_public_name
from .profiles import ProfileConfig
from .report import Issue

# 数组契约规则需要把 AST 类型标注还原成可匹配的文本。
def annotation_to_text(annotation: ast.AST | None) -> str:
    """把类型标注节点转换为源码文本。

    参数:
        annotation: AST 中解析到的类型标注节点。

    返回:
        可匹配的类型标注文本；没有标注或还原失败时返回空字符串。
    """

    # 没有类型标注时无法从签名判断数组契约。
    if annotation is None:

        # 空字符串让调用方继续尝试名称启发式。
        return ""

    # 捕获当前操作中的可预期失败并转换为规则结果
    try:

        # ast.unparse 保留 ndarray、Tensor 等标注文本。
        return ast.unparse(annotation)

    # 标注还原失败时回落为空字符串，让后续规则继续尝试名称启发式。
    except Exception:

        # 无法还原的标注不参与数组关键词匹配。
        return ""

# 标识符提示词比较需要兼容 snake_case、camelCase 和符号分隔。
def split_identifier_tokens(name: str) -> set[str]:
    """把标识符拆成小写 token 集合。

    参数:
        name: 当前规则正在检查的标识符名称。

    返回:
        去重后的标识符语义 token。
    """

    # 先拆 camelCase，再用统一分隔逻辑处理 snake_case 等名称。
    str_spaced_name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)  # camelCase 拆分后的名称

    # 空 token 会干扰 issubset 判断，因此在集合推导里剔除。
    return {token for token in re.split(r"[^A-Za-z0-9]+", str_spaced_name.lower()) if token}

# 参数名包含数组领域提示词时，函数需要数组契约文档。
def name_has_domain_hint(name: str, hints: tuple[str, ...]) -> bool:
    """判断标识符是否包含领域提示词。

    参数:
        name: 当前规则正在检查的标识符名称。
        hints: 表示数组、张量或矩阵语义的提示词集合。

    返回:
        True 表示某个提示词的 token 全部出现在标识符中。
    """

    # 保存源码 token，定位注释或可替换标识符。
    set_tokens = split_identifier_tokens(name)  # 集合词法 token 序列

    # 任一领域提示词完整出现在标识符 token 中即可视为命中。
    for hint in hints:

        # 当前提示词也要拆成统一 token 集，才能与名称 token 做子集比较。
        set_hint_tokens = split_identifier_tokens(hint)  # 当前提示词拆分后的 token 集合

        # 提示词 token 必须全部出现，避免 `matrix` 与无关单词误匹配。
        if set_hint_tokens and set_hint_tokens.issubset(set_tokens):

            # 名称已经显示数组语义。
            return True

    # 返回该规则是否命中目标条件。
    return False

# `is_array_related_function` 判断数组related函数。
def is_array_related_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断 array_related_function 是否满足规则要求。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        布尔值，表示判断arrayrelatedfunction是否成立。
    """

    # 位置参数和关键字参数共同构成函数的数组输入契约。
    args = []  # 命令行参数

    # 先检查普通位置参数。
    args.extend(node.args.args)

    # 再检查仅关键字参数。
    args.extend(node.args.kwonlyargs)

    # 逐个查看参数名和参数标注是否暴露数组语义。
    for arg in args:

        # 参数名出现矩阵、向量或张量词时要求数组文档契约。
        if name_has_domain_hint(arg.arg, ARRAY_NAME_HINTS):

            # 参数名已经足够说明这是数组相关函数。
            return True

        # 参数标注可能直接写出 ndarray、Tensor 或 Sequence 数组类型。
        str_annotation_text = annotation_to_text(arg.annotation)  # 参数类型标注文本

        # 类型标注命中数组关键词时同样要求 shape/dtype/unit 说明。
        if any(hint in str_annotation_text for hint in ARRAY_ANNOTATION_HINTS):

            # 类型标注已经足够说明这是数组相关函数。
            return True

    # 返回值标注若暴露数组类型，也需要检查返回数组契约。
    str_return_annotation = annotation_to_text(node.returns)  # 返回值类型标注文本

    # 返回任一关键词命中后的布尔判断结果
    return any(hint in str_return_annotation for hint in ARRAY_ANNOTATION_HINTS)

# 科学计算函数的文档必须同时说明数组形状、类型和单位。
def docstring_has_array_details(docstring: str | None) -> bool:
    """判断 docstring 是否包含数组契约三要素。

    参数:
        docstring: 正在检查的函数或模块文档字符串。

    返回:
        True 表示文档同时提到 shape、dtype 和 unit/单位。
    """

    # 没有 docstring 时无法证明 shape、dtype 和单位契约存在。
    if not docstring:

        # 缺失文档由调用方登记 PG008。
        return False

    # 生成lowerdoc的小写副本，确保关键词匹配不受大小写影响。
    str_lower_doc = docstring.lower()  # 生成大小写无关文本，保证关键词匹配稳定

    # 形状说明用于明确数组维度和广播约束。
    bool_has_shape = "shape" in str_lower_doc or "维度" in docstring  # shape 或维度说明是否存在

    # dtype 说明用于避免数值精度和类型转换歧义。
    bool_has_dtype = "dtype" in str_lower_doc or "类型" in docstring  # dtype 或类型说明是否存在

    # 单位说明用于防止科学计算中量纲混用。
    bool_has_unit = "unit" in str_lower_doc or "单位" in docstring  # unit 或单位说明是否存在

    # 三类科学计算契约都出现时才算数组文档完整。
    return bool_has_shape and bool_has_dtype and bool_has_unit

# `check_array_docstring` 检查并登记数组文档字符串。
def check_array_docstring(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """检查 array_docstring 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无业务返回值；函数只负责按规则把发现项追加到 issues 列表。
    """

    # 非科学 profile 可关闭数组契约规则。
    if not config.require_array_docstring:

        # 当前 profile 未启用此规则时直接结束检查。
        return

    # 扫描 AST 节点，寻找本规则关注的语法结构。
    for node in ast.walk(tree):

        # 数组契约只检查函数和异步函数的公开调用接口。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 非函数节点不属于数组文档契约检查范围。
            continue

        # 私有函数不作为公开科学计算 API 检查对象。
        if not is_public_name(node.name):

            # 私有实现细节不进入公开科学接口文档约束。
            continue

        # 名称和标注都没有数组语义时不触发 PG008。
        if not is_array_related_function(node):

            # 没有数组语义的函数不需要登记科学数组契约问题。
            continue

        # 数组相关函数缺少 shape、dtype 或单位说明时登记问题。
        if not docstring_has_array_details(ast.get_docstring(node)):

            # PG008 阻断不完整的科学数组契约。
            add_issue(
                issues,
                "PG008",
                "BLOCKER",
                filepath,
                node.lineno,
                f"Scientific function `{node.name}` needs shape, dtype, and unit docs.",
            )
