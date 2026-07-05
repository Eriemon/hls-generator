"""检查公共 API 类型标注的基础规则。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import ast
from pathlib import Path

# 导入当前模块运行所需的依赖
from .ast_helpers import add_issue, is_public_name
from .profiles import ProfileConfig
from .report import Issue

# PG014 需要同时确认公开函数的参数和返回值标注完整性。
def function_missing_type_hints(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断公开函数是否缺少类型标注。

    参数:
        node: 公开同步或异步函数定义节点。

    返回:
        True 表示至少一个公开参数或返回值缺少标注。
    """

    # self 和 cls 之外的形参都需要显式类型，三类参数先合并后检查。
    args = []  # 待检查的函数形参节点

    # 把普通位置参数加入统一扫描列表。
    args.extend(node.args.posonlyargs)

    # 追加常规参数，后续按同一套规则检查类型标注。
    args.extend(node.args.args)

    # 最后并入关键字专用参数，避免漏掉公开调用入口。
    args.extend(node.args.kwonlyargs)

    # 公开函数的所有显式形参都参与 API 类型契约检查。
    for arg in args:

        # 方法接收者由类定义隐式约束，不要求重复标注。
        if arg.arg in {"self", "cls"}:

            # 跳过隐式接收者参数，避免把实例绑定约束误判成 API 缺口。
            continue

        # 任一业务形参缺少标注都会让公开 API 契约不完整。
        if arg.annotation is None:

            # 调用方只需要知道是否存在缺口。
            return True

    # 可变位置参数也属于公开调用契约，必须声明元素入口类型。
    if node.args.vararg is not None and node.args.vararg.annotation is None:

        # 这里命中后已经足够判定当前函数的公开契约不完整。
        return True

    # 可变关键字参数没有标注时，调用者无法知道允许的值类型。
    if node.args.kwarg is not None and node.args.kwarg.annotation is None:

        # 一旦发现缺失即可结束扫描。
        return True

    # 返回值没有标注时同样视为公开 API 契约不完整。
    return node.returns is None

# `check_public_api_type_hints` 检查并登记公开符号api类型hints。
def check_public_api_type_hints(
    tree: ast.AST,
    filepath: Path,
    issues: list[Issue],
    config: ProfileConfig,
) -> None:
    """检查 public_api_type_hints 对应的质量门约束。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        无业务返回值；发现项会直接追加到 `issues` 列表。
    """

    # 当前 profile 不要求公开 API 类型时，PG014 不参与该次检查。
    if not config.require_type_hints:

        # 交回调用方
        return

    # 扫描 AST 节点，寻找本规则关注的语法结构。
    for node in ast.walk(tree):

        # 只有同步和异步函数定义携带公开 API 类型契约。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 非函数节点不具备公开调用签名，直接跳过后续规则判断。
            continue

        # 私有函数不作为对外 API，避免对内部辅助函数过度约束。
        if not is_public_name(node.name):

            # 跳过私有符号，避免把内部实现细节升级为公开契约要求。
            continue

        # 发现必需项缺口时登记配置完整性问题。
        if function_missing_type_hints(node):

            # 登记当前问题
            add_issue(
                issues,
                "PG014",
                "BLOCKER",
                filepath,
                node.lineno,
                # 延续当前结构
                f"Public function `{node.name}` lacks parameter or return type annotations.",
            )
