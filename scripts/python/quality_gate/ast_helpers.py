"""提供质量门复用的 AST、token 和源码定位辅助能力。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import ast
import re
import tokenize
from pathlib import Path
from typing import Iterable

# 导入当前模块运行所需的依赖
from .report import Issue

# 递归扫描时排除缓存、虚拟环境和发布产物目录。
EXCLUDED_DIR_NAMES = {  # 文件收集阶段跳过的目录名
    ".git",  # Git 元数据目录
    ".hg",  # Mercurial 仓库控制目录
    ".mypy_cache",  # 静态类型分析缓存
    ".pytest_cache",  # 测试运行状态缓存
    ".ruff_cache",  # Lint 结果缓存
    ".tox",  # tox 隔离环境目录
    ".venv",  # 项目本地虚拟环境目录
    "__pycache__",  # Python 字节码缓存目录
    "build",  # 构建中间产物目录
    "dist",  # 发布产物目录
    "site-packages",  # 安装后的第三方包目录
}

# 识别数组、矩阵和张量变量名的领域关键词。
ARRAY_NAME_HINTS = (  # 科学计算数组变量名关键词
    "array",  # 通用数组命名
    "matrix",  # 矩阵语义命名
    "vector",  # 向量语义命名
    "tensor",  # 张量语义命名
    "ndarray",  # NumPy 数组类型名
    "state",  # 状态向量或状态矩阵
    "rho",  # 量子密度矩阵常见符号
    "theta",  # 参数角度或相位变量
    "signal",  # 信号样本容器
    "image",  # 图像数组容器
    "hamiltonian",  # 哈密顿量矩阵
)

# 识别求解器和仿真函数名称的领域关键词。
SOLVER_NAME_HINTS = (  # 数值求解函数名关键词
    "solver",  # 求解器语义前缀
    "solve",  # 求解动作语义
    "simulate",  # 仿真动作语义
    "simulation",  # 仿真流程语义
    "schrodinger",  # 薛定谔方程领域语义
    "ode",  # 常微分方程缩写
    "step",  # 单步推进语义
)

# 识别常见数组和张量类型标注的文本片段。
ARRAY_ANNOTATION_HINTS = (  # 数组或张量类型标注关键词
    "ndarray",  # NumPy 数组标注
    "np.ndarray",  # NumPy 简写类型标注
    "numpy.ndarray",  # NumPy 全名类型标注
    "Tensor",  # 通用张量类型名
    "torch.Tensor",  # PyTorch 张量类型标注
    "jax.Array",  # JAX 数组类型标注
    "jnp.ndarray",  # JAX NumPy 风格类型标注
)

# 导入阶段禁止执行的环境、绘图和设备副作用调用。
IMPORT_SIDE_EFFECT_CALLS = {  # 模块导入期禁止调用的副作用函数
    "matplotlib.use",  # 绘图库后端切换
    "plt.show",  # 导入期弹出绘图窗口
    "sys.path.append",  # 动态追加模块搜索路径
    "sys.path.insert",  # 动态插入模块搜索路径
    "sys.path.extend",  # 批量扩展模块搜索路径
    "os.chdir",  # 修改当前工作目录
    "os.environ.setdefault",  # 写入环境变量默认值
    "set_device",  # 自定义设备选择入口
    "torch.cuda.set_device",  # 显式切换 CUDA 设备
    "torch.cuda.set_per_process_memory_fraction",  # 配置显存占用上限
    "torch.cuda.empty_cache",  # 主动清空 CUDA 缓存
}

# 脚本 profile 允许在入口函数内出现的输出副作用调用。
SCRIPT_SIDE_EFFECT_CALLS = {  # 脚本入口允许调用的输出函数
    "open",  # 文件写入入口
    "Path.mkdir",  # 创建输出目录
    "os.makedirs",  # 递归创建目录
    "np.savetxt",  # 保存数值文本文件
    "numpy.savetxt",  # 保存数值文本文件全名入口
    "plt.savefig",  # 保存图像文件
    "plt.show",  # 显示图像窗口
    "print",  # 输出终端进度信息
    "time.perf_counter",  # 高精度计时入口
    "time.time",  # 墙钟时间采样入口
}

# 需要配套可复现实验说明的随机种子调用。
RANDOM_SEED_CALLS = {  # 随机性初始化函数名
    "random.seed",  # 标准库随机种子
    "np.random.seed",  # NumPy 随机种子
    "numpy.random.seed",  # NumPy 全名随机种子
    "torch.manual_seed",  # PyTorch 全局随机种子
    "torch.cuda.manual_seed",  # 当前 CUDA 设备随机种子
    "torch.cuda.manual_seed_all",  # 全部 CUDA 设备随机种子
}

# 识别被整行注释掉的 Python 代码片段。
COMMENTED_CODE_PATTERNS = (  # 注释掉代码的行首匹配模式
    r"^#\s*(def|class|if|elif|else:|for|while|try:|except|with|return)\b",  # 结构语句注释残留
    r"^#\s*(plt\.|print\(|np\.|torch\.|jax\.|qml\.)",  # 常见调用语句注释残留
)

# 识别路径和环境变量写入类赋值目标。
PATH_ASSIGNMENT_NAMES = {"os.environ", "sys.path"}  # 可改变运行环境的赋值目标

# 检测注释和文档字符串是否包含中文字符。
CJK_TEXT_PATTERN = re.compile(r"[\u3400-\u9fff]")  # 中文字符检测模式

# 豁免编码声明、noqa 和类型忽略等工具注释。
COMMENT_PRAGMA_HINTS = (  # 工具控制类注释关键词
    "coding",  # 编码声明
    "encoding",  # 编码声明别名
    "noqa",  # Lint 忽略指令
    "type: ignore",  # 类型检查忽略指令
    "pragma:",  # 覆盖率等工具 pragma
    "pylint:",  # pylint 局部规则开关
    "mypy:",  # mypy 局部类型开关
    "fmt:",  # 格式化工具指令
    "isort:",  # import 排序工具指令
)

# 识别只写动作和泛化名词的低信息注释。
GENERIC_COMMENT_PATTERN = re.compile(  # 低信息量注释文本模式
    r"^(定义|保存|初始化|计算|处理|生成|获取|返回|执行|调用)"
    r"(变量|结果|数据|内容|对象|参数|函数|代码|列表|字典|矩阵|向量|路径|配置|值)$"
)

# 父链标注让后续规则可以从任意节点回溯作用域。
class ParentAnnotator(ast.NodeVisitor):
    """为 AST 子节点写入 `parent` 引用。"""

    # 访问每个节点时先给直接子节点挂上父节点引用。
    def generic_visit(self, node: ast.AST) -> None:
        """给当前节点的直接子节点补充父链引用。

        参数:
            node: 当前遍历到的 AST 节点。

        返回:
            None。该方法只为子节点补充 ``parent`` 引用后继续递归遍历。
        """

        # 访问子节点并写入父节点引用，便于后续向上查找作用域。
        for child in ast.iter_child_nodes(node):

            # 把父节点引用写入子节点，便于后续向上查找 AST 上下文
            setattr(child, "parent", node)

        # 继续让 NodeVisitor 遍历当前节点的子树
        super().generic_visit(node)

# 质量门规则统一通过该函数追加发现项。
def add_issue(
    issues: list[Issue],
    code: str,
    level: str,
    filepath: Path | str,
    line: int | None,
    message: str,
) -> None:
    """追加一条带安全行号的质量门发现项。

    参数:
        issues: 用于累计当前质量门发现项的列表。
        code: 验证发现项使用的规则编号。
        level: 验证发现项的严重级别。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        line: 当前规则正在解析的单行文本。
        message: 展示给调用方的验证失败说明。

    返回:
        None。该函数只负责向 ``issues`` 列表追加一条发现项。
    """

    # 报告行号至少为 1，避免 AST 缺失行号时生成无效定位。
    int_safe_line = line if line and line > 0 else 1  # 归一化后的报告行号

    # 把标准化后的发现项对象压入当前问题列表。
    issues.append(Issue(code, level, str(filepath), int_safe_line, message))

# scripts/python 下的工具文件允许 CLI 输出和 main guard。
def is_project_script_file(filepath: Path | str) -> bool:
    """判断文件是否位于项目约定的 Python 脚本目录。

    参数:
        filepath: 正在被质量门检查的文件路径。

    返回:
        True 表示路径包含 scripts/python 目录段。
    """

    # 只比较路径段名称，兼容相对路径、绝对路径和 Windows 分隔符。
    tuple_path_parts = tuple(part.lower() for part in Path(filepath).parts)  # 归一化路径段

    # 连续 scripts/python 段表示该文件属于脚本工具边界。
    for index in range(len(tuple_path_parts) - 1):

        # 命中脚本边界后，调用方可跳过核心库副作用规则。
        if tuple_path_parts[index] == "scripts" and tuple_path_parts[index + 1] == "python":

            # 该文件位于项目脚本目录。
            return True

    # 未出现脚本目录段时，按普通库代码检查。
    return False

# 文件或目录输入会被统一展开为待检查的 Python 文件列表。
def collect_python_files(target_path: str | Path) -> list[Path]:
    """收集质量门需要检查的 Python 文件。

    参数:
        target_path: 用户传入的待检查文件或目录路径。

    返回:
        目标文件本身或目录下未被排除的 `.py` 文件列表。
    """

    # 统一路径对象，后续同时支持文件和目录输入。
    path_target = Path(target_path)  # 待展开的检查目标路径

    # 单个 Python 文件可以直接作为质量门输入。
    if path_target.is_file() and path_target.suffix == ".py":

        # 文件目标不需要目录展开。
        return [path_target]

    # 不存在或不是目录的目标没有可检查的 Python 文件。
    if not path_target.is_dir():

        # 调用方会把空列表转成目标无效的诊断。
        return []

    # 直接扫描 fixture 目录时保留坏样例，扫描 tests 根时跳过资源样例。
    bool_target_is_fixture = "fixtures" in path_target.parts  # 是否直接检查测试夹具目录

    # 目录输入会递归收集候选文件，同时跳过缓存、发布目录和测试资源。
    list_python_files: list[Path] = []  # 待检查 Python 文件列表

    # 扫描文件系统候选项，过滤缓存和发布产物。
    for path in sorted(path_target.rglob("*.py")):

        # 缓存、构建产物和发布包不参与源码质量扫描。
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):

            # 这些目录属于缓存或构建副产物，当前扫描应直接跳过。
            continue

        # tests 根目录扫描不把故意坏 fixture 当作待治理源码。
        if not bool_target_is_fixture and "fixtures" in path.parts:

            # fixture 内部故意保留坏样例，不能混入正式治理清单。
            continue

        # 排序后的 rglob 保证不同平台报告顺序稳定。
        list_python_files.append(path)

    # 返回值只包含实际参与规则扫描的 Python 源文件。
    return list_python_files

# 调用名需要保留属性链，供副作用和类型规则匹配。
def get_call_name(node: ast.AST | None) -> str:
    """提取调用表达式或属性链的点分名称。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        点分调用名称；无法解析时返回空字符串。
    """

    # None 表示调用点没有可解析的表达式名称。
    if node is None:

        # 空节点通常来自语法不匹配分支。
        return ""

    # 命名节点直接携带裸标识符名称。
    if isinstance(node, ast.Name):

        # 简单函数调用直接使用标识符。
        return node.id

    # 属性节点需要把左侧对象名和属性名重新拼回点分路径。
    if isinstance(node, ast.Attribute):

        # 递归读取属性链左侧，例如 `torch.cuda`。
        str_parent_name = get_call_name(node.value)  # 文本父节点名称

        # 保留完整点分名称，便于匹配 `os.environ.setdefault`。
        return f"{str_parent_name}.{node.attr}" if str_parent_name else node.attr

    # 下标表达式只保留外层容器名，不把索引片段并入调用名。
    if isinstance(node, ast.Subscript):

        # 下标调用只关心外层对象名称。
        return get_call_name(node.value)

    # 调用表达式继续下钻到 func 字段，提取被调用对象名称。
    if isinstance(node, ast.Call):

        # 调用节点继续解析其 func 字段。
        return get_call_name(node.func)

    # 复杂表达式没有稳定名称，不参与名称匹配规则。
    return ""

# `is_public_name` 判断公开符号名称。
def is_public_name(name: str) -> bool:
    """判断 public_name 是否满足规则要求。

    参数:
        name: 当前规则正在检查的标识符名称。

    返回:
        布尔值，表示判断public名称是否成立。
    """

    # 先排除普通私有命名，仅保留约定允许的魔术方法例外。
    if name.startswith("_") and name not in {"__init__", "__call__"}:

        # 这种命名对外不可见，因此不视为公开符号。
        return False

    # 其余名称都按公开符号处理，供上层规则继续检查。
    return True

# `if __name__ == "__main__"` 是导入期副作用规则的显式边界。
def is_main_guard(node: ast.AST) -> bool:
    """判断 main_guard 是否满足规则要求。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        布尔值，表示判断mainguard是否成立。
    """

    # 只有 if 语句可能承载 `if __name__ == "__main__"` 入口保护。
    if not isinstance(node, ast.If):

        # 非 if 节点不可能是 main guard。
        return False

    # main guard 的判断表达式必须是单个比较。
    # main guard 必须使用比较表达式，其他条件形式不算标准入口保护。
    if not isinstance(node.test, ast.Compare):

        # 调用方需要的是布尔判断，不需要错误明细。
        return False

    # 经过类型守卫后，比较表达式可安全读取 left、ops 和 comparators。
    compare_test: ast.Compare = node.test  # main guard 比较表达式

    # 比较左侧必须直接引用 Python 的 `__name__` 模块变量。
    if not isinstance(compare_test.left, ast.Name) or compare_test.left.id != "__name__":

        # 其他变量名不代表模块执行入口。
        return False

    # 运算符不是单个等号比较时，不能视为标准 main guard。
    if len(compare_test.ops) != 1 or not isinstance(compare_test.ops[0], ast.Eq):

        # 其他比较形式不满足脚本入口保护约束。
        return False

    # 比较右侧必须只有一个候选值，避免混入链式比较。
    if len(compare_test.comparators) != 1:

        # 多个比较对象不属于标准的 __main__ 判断形式。
        return False

    # 右侧常量必须精确等于 "__main__"。
    expr_comparator: ast.expr = compare_test.comparators[0]  # main guard 右侧比较值

    # 只有标准 main guard 形态被视为脚本入口边界。
    return isinstance(expr_comparator, ast.Constant) and expr_comparator.value == "__main__"

# 向上查找 main guard，用于豁免入口内的副作用。
def ancestor_is_main_guard(node: ast.AST) -> bool:
    """判断节点是否位于 main guard 代码块内。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        True 表示该节点拥有标准 main guard 祖先节点。
    """

    # 从当前节点的父节点开始向模块根部回溯。
    a_s_t_parent_node: ast.AST = getattr(node, "parent", None)  # 父链回溯起点节点

    # 沿父链持续向外层语句推进，直到找到函数边界或到达模块顶层。
    while a_s_t_parent_node is not None:

        # 找到标准入口保护后即可停止向上回溯。
        if is_main_guard(a_s_t_parent_node):

            # 祖先链中存在 main guard，说明该节点受入口保护。
            return True

        # 沿父链继续向外层语句查找。
        a_s_t_parent_node: ast.AST = getattr(a_s_t_parent_node, "parent", None)  # 继续外扩后的下一层父节点

    # 返回该规则是否命中目标条件。
    return False

# 向上查找函数祖先，区分导入期代码和函数体代码。
def ancestor_function(node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """返回包住当前节点的最近函数定义。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        最近的同步或异步函数节点；模块顶层节点返回 None。
    """

    # 从当前节点父级开始回溯，目标是找到最近的函数作用域边界。
    a_s_t_parent_node: ast.AST = getattr(node, "parent", None)  # 当前回溯的父节点

    # 这一段持续沿父链外扩，直到命中函数定义或走到模块顶层。
    while a_s_t_parent_node is not None:

        # 一旦命中函数定义，就说明当前节点属于该函数体内部。
        if isinstance(a_s_t_parent_node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 最近函数祖先就是当前节点所属的运行期函数体。
            return a_s_t_parent_node

        # 当前层不是函数边界时，继续回退到更外层父节点。
        a_s_t_parent_node: ast.AST = getattr(a_s_t_parent_node, "parent", None)  # 上一级 AST 父节点

    # 没有函数祖先时返回 None，表示该节点位于模块顶层。
    return None

# 导入期代码排除函数体和 main guard 内部代码。
def node_is_at_module_import_time(node: ast.AST) -> bool:
    """判断节点是否会在模块导入时执行。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        True 表示节点位于模块顶层且不在 main guard 内。
    """

    # 函数内部语句不是导入期顶层副作用。
    if ancestor_function(node) is not None:

        # 进入函数后只有调用时才会执行。
        return False

    # main guard 内部语句只在脚本直接运行时执行。
    if ancestor_is_main_guard(node):

        # 受入口保护的代码不按导入期副作用处理。
        return False

    # 经过函数体和入口保护筛除后，剩余节点都属于导入期代码。
    return True

# 源码行读取允许用忽略错误模式兜底，避免编码问题中断质量门。
def read_source_lines(filepath: Path) -> list[str]:
    """读取 Python 文件的源码行。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。

    返回:
        不含换行符的源码行列表；遇到编码错误时忽略不可解码字符。
    """

    # 先尝试按正常 UTF-8 路径读取源码文本。
    try:

        # 常规 UTF-8 路径保留完整文本内容。
        return filepath.read_text(encoding="utf-8").splitlines()

    # UTF-8 解码失败时切换到忽略坏字节的保底读取路径。
    except UnicodeDecodeError:

        # 编码异常文件仍要尽量参与规则扫描。
        return filepath.read_text(encoding="utf-8", errors="ignore").splitlines()

# `iter_comment_tokens` 处理注释词法 token 序列。
def iter_comment_tokens(filepath: Path) -> Iterable[tokenize.TokenInfo]:
    """迭代注释token。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。

    返回:
        迭代注释tokens产出的结果。
    """

    # 先按 tokenize 要求的二进制模式打开文件并枚举词法 token。
    try:

        # 在受控上下文中处理资源生命周期
        with filepath.open("rb") as file_obj:
            yield from (
                token
                for token in tokenize.tokenize(file_obj.readline)
                if token.type == tokenize.COMMENT
            )

    # 注释词法流读取失败时，只跳过当前文件的注释扫描阶段。
    except (OSError, tokenize.TokenError, UnicodeDecodeError):

        # 当前 profile 未启用此规则时直接结束检查。
        return

# 注释位置索引保留列号和原始文本，供行级规则复用。
def collect_comment_positions(filepath: Path) -> dict[int, tuple[int, str]]:
    """收集文件内注释 token 的行号、列号和文本。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。

    返回:
        以源码行号为键，值为注释列号和注释文本。
    """

    # token.start 同时给出行列号，行号用于快速查找邻近代码。
    dict_comments: dict[int, tuple[int, str]] = {}  # 行号到注释位置和文本的映射

    # tokenize 已经识别真实注释，避免把字符串里的 # 当成注释。
    for token in iter_comment_tokens(filepath):

        # 同一行只有一个 COMMENT token，保留其起始列和原始文本。
        dict_comments[token.start[0]] = (token.start[1], token.string)  # 注释列号和原始文本

    # 返回按源码行号索引的注释位置表，供行级规则复用。
    return dict_comments

# 只关心注释文本的规则可使用轻量映射。
def collect_comment_lines(filepath: Path) -> dict[int, str]:
    """收集文件内每行注释的原始文本。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。

    返回:
        以源码行号为键、注释文本为值的映射。
    """

    # 只保留注释文本映射，便于轻量规则直接按行号查询。
    return {line_number: text for line_number, (_, text) in collect_comment_positions(filepath).items()}
