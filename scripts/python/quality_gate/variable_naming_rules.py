"""检查可推断类型前缀和变量命名的安全重命名建议。"""

# 兼容包内导入和脚本直接运行两种入口
from __future__ import annotations

# 兼容包内导入和脚本直接运行两种入口
import ast
import io
import keyword
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

# 兼容包内导入和脚本直接运行两种入口
from .ast_helpers import add_issue, get_call_name
from .report import Issue

# snake_case 变量名必须以小写字母或下划线开头。
SNAKE_CASE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")  # 变量名 snake_case 格式

# 模块常量允许全大写 snake_case，避免被普通变量规则误报。
CONSTANT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")  # 模块常量命名格式

# 这些类型族决定变量名前缀，也限定自动重命名的安全边界。
TYPE_PREFIXES = {  # Python 类型族到变量名前缀的映射
    "array": "array_",  # array 类型使用的标准前缀
    "bool": "bool_",  # bool 字面量和值对象统一使用的前缀
    "bytes": "bytes_",  # 二进制字节串使用的前缀
    "bytearray": "bytearray_",  # 可变字节缓冲区使用的前缀
    "callable": "func_",  # 可调用对象统一折叠到 func_ 前缀
    "class": "class_",  # 类型对象或类引用使用的前缀
    "dataframe": "df_",  # pandas DataFrame 约定使用的短前缀
    "dict": "dict_",  # 键值映射容器使用的前缀
    "file": "file_",  # 文件句柄或文件对象使用的前缀
    "float": "float_",  # 浮点数值使用的前缀
    "int": "int_",  # 整数值使用的前缀
    "list": "list_",  # 顺序列表容器使用的前缀
    "object": "obj_",  # 无法再细分的对象回退前缀
    "path": "path_",  # 路径对象统一使用的前缀
    "series": "series_",  # pandas Series 使用的前缀
    "set": "set_",  # 去重集合容器使用的前缀
    "str": "str_",  # 文本字符串使用的前缀
    "tuple": "tuple_",  # 固定位置元组容器使用的前缀
}

# 将注解名称和构造调用名称统一到内部类型族。
TYPE_NAME_FAMILY_MAP = {  # 注解名称到统一类型族的映射
    "dict": "dict",  # 原生 dict 注解映射到 dict 类型族
    "typing.dict": "dict",  # typing.dict 归并到 dict 类型族
    "list": "list",  # 直接写 list 时按可变序列容器处理
    "typing.list": "list",  # 老式 typing.List 写法也落到列表规则
    "tuple": "tuple",  # 原生 tuple 注解代表定长位置序列
    "typing.tuple": "tuple",  # typing.Tuple 兼容写法沿用元组规则
    "set": "set",  # set 注解表示去重集合容器
    "typing.set": "set",  # typing.Set 旧写法同样走集合规则
    "str": "str",  # 文本类型名称映射到 str 类型族
    "builtins.str": "str",  # 显式 builtins 限定名仍表示普通文本
    "int": "int",  # 整数类型名称映射到 int 类型族
    "builtins.int": "int",  # 带 builtins 前缀的整数类型仍视为数值
    "float": "float",  # 浮点类型名称映射到 float 类型族
    "builtins.float": "float",  # 模块限定浮点类型保持浮点语义
    "bool": "bool",  # 布尔类型名称映射到 bool 类型族
    "builtins.bool": "bool",  # builtins.bool 仍表示真假值类型
    "bytes": "bytes",  # 字节串类型名称映射到 bytes 类型族
    "builtins.bytes": "bytes",  # 模块限定字节串同样视为原始二进制文本
    "bytearray": "bytearray",  # 可变字节数组映射到 bytearray 类型族
    "builtins.bytearray": "bytearray",  # 带限定名的 bytearray 仍保留缓冲区语义
    "open": "file",  # open 构造返回文件对象类型族
    "io.open": "file",  # io.open 与 open 统一视为文件对象
    "pd.dataframe": "dataframe",  # pandas 别名 DataFrame 映射到 dataframe 类型族
    "pandas.dataframe": "dataframe",  # pandas 全名 DataFrame 映射到 dataframe 类型族
    "dataframe": "dataframe",  # 直接 DataFrame 名称映射到 dataframe 类型族
    "pd.series": "series",  # pandas 别名 Series 指向一维标记序列
    "pandas.series": "series",  # pandas 全限定 Series 同样按一维序列处理
    "series": "series",  # 裸 Series 名称也落到 series 类型族
    "np.array": "array",  # numpy 别名 array 构造映射到 array 类型族
    "numpy.array": "array",  # numpy 全名 array 构造映射到 array 类型族
    "np.asarray": "array",  # numpy asarray 结果按 array 类型族处理
    "numpy.asarray": "array",  # numpy 全名 asarray 结果按 array 类型族处理
    "array": "array",  # 裸 array 名称统一视为数组结果
    "callable": "callable",  # callable 注解映射到可调用类型族
    "typing.callable": "callable",  # typing.Callable 写法仍表示可调用对象
    "type": "class",  # type 构造器结果按 class 类型族处理
    "object": "object",  # object 注解保留为最宽泛对象类型族
}

# 这些短名缺少业务语义，PG040 会要求人工改成领域名称。
VAGUE_NAMES = {"data", "info", "obj", "result", "temp", "value"}  # 禁用的空泛变量名

# 框架约定、魔法名和常用接收器不参与类型前缀检查。
EXEMPT_NAMES = {"_", "__all__", "__file__", "__name__", "__version__", "args", "cls", "kwargs", "parser", "self"}  # 命名规则豁免标识符

# i/j/k 保留给短循环索引，避免强制扩写简单下标。
SHORT_LOOP_NAMES = {"i", "j", "k"}  # 允许的短循环索引

# 只有这些可静态确认的常见类型允许自动落盘重命名。
SAFE_RENAME_PREFIX_TYPES = {  # 允许自动补前缀的标量类型族
    "array",  # numpy/pandas 风格数组前缀可以安全补齐
    "bool",  # 布尔值前缀可以安全补齐
    "bytearray",  # 可变字节缓冲区前缀可以安全补齐
    "bytes",  # 字节串前缀可以安全补齐
    "dataframe",  # DataFrame 前缀不会破坏局部变量契约
    "dict",  # 字典容器前缀可以安全补齐
    "file",  # 文件对象前缀可以安全补齐
    "float",  # 浮点数前缀可以安全补齐
    "int",  # 整数前缀可以安全补齐
    "list",  # 列表容器前缀可以安全补齐
    "path",  # 路径对象前缀可以安全补齐
    "series",  # Series 前缀不会引入额外语义歧义
    "set",  # 集合容器前缀可以安全补齐
    "str",  # 文本前缀可以安全补齐
    "tuple",  # 元组容器前缀可以安全补齐
}

# 排除 object_，避免把无法精确推断的名称误认为已经合规。
PRECISE_TYPE_PREFIXES = tuple(prefix for type_name, prefix in TYPE_PREFIXES.items() if type_name != "object")  # 可证明类型前缀集合

# 类型未知但名称已经带有这些领域线索时，不再把 PG041 作为噪声追加。
SEMANTIC_TYPE_HINT_TERMS = (  # 允许消除 PG041 噪声的受控语义词根
    "argument",  # 变量名中可识别的领域词根示例
    "artifact",  # 变量名中可识别的工件词根
    "board",  # 板卡运行或平台选择相关的语义词根
    "candidate",  # 候选结果或待选项常见的语义词根
    "codegen",  # 代码生成流程中常见的语义词根
    "command",  # 命令行参数或命令对象常见的语义词根
    "comment",  # 注释文本或注释规则常见的语义词根
    "config",  # 配置对象常见的语义词根
    "confirmation",  # 确认状态或确认记录常见的语义词根
    "decision",  # 决策结果常见的语义词根
    "dependency",  # 依赖项常见的语义词根
    "dir",  # 目录路径常见的语义词根
    "docstring",  # 文档字符串内容常见的语义词根
    "evidence",  # 证据记录常见的语义词根
    "file",  # 文件对象或文件路径常见的语义词根
    "input",  # 输入数据常见的语义词根
    "json",  # JSON 载荷常见的语义词根
    "language",  # 语言配置常见的语义词根
    "line",  # 行文本或行号常见的语义词根
    "manifest",  # 清单对象常见的语义词根
    "node",  # AST 节点常见的语义词根
    "note",  # 备注文本常见的语义词根
    "output",  # 输出结果常见的语义词根
    "path",  # 路径对象常见的语义词根
    "payload",  # 载荷内容常见的语义词根
    "plan",  # 计划对象常见的语义词根
    "profile",  # 配置档或质量档常见的语义词根
    "prompt",  # 提示词文本常见的语义词根
    "provider",  # 提供方对象常见的语义词根
    "readiness",  # 就绪状态常见的语义词根
    "report",  # 报告对象常见的语义词根
    "root",  # 根路径或根节点常见的语义词根
    "source",  # 源文本或源对象常见的语义词根
    "status",  # 状态结果常见的语义词根
    "target",  # 目标对象常见的语义词根
    "text",  # 文本内容常见的语义词根
    "tree",  # 语法树对象常见的语义词根
    "workflow",  # 工作流状态常见的语义词根
)

# 遍历作用域时遇到这些节点要停止向内递归。
NESTED_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)  # 词法作用域边界节点

# token 重命名只应用到 AST 已确认的局部名称位置。
@dataclass(frozen=True)
class RenameAction:
    """记录一个经过 AST 确认的 NAME token 重命名位置。"""

    # 重命名前的局部变量名必须与 token 文本一致。
    old_name: str  # 原变量名

    # 添加类型前缀后的名称用于替换同一位置的 NAME token。
    new_name: str  # 替换后的变量名

    # token 起始行号用于和 tokenize 结果精确对齐。
    lineno: int  # 变量名 token 的源码行号

    # token 起始列号用于避免改写同名字符串或属性。
    col_offset: int  # 变量名 token 的源码列偏移

# `AssignmentRecord` 封装同一类规则检查需要共享的状态。
class AssignmentRecord:
    """保存一次可检查赋值的最小上下文。"""

    # 一条记录绑定变量名、赋值表达式和可选类型标注。
    def __init__(
        self,
        name: str,
        node: ast.AST,
        value: ast.AST | None,
        annotation: ast.AST | None = None,
    ) -> None:

        """初始化一条变量命名检查记录。

        参数:
            name: 赋值目标的变量名。
            node: 变量名所在 AST 节点，用于行号和父链判断。
            value: 赋值右侧表达式，用于静态类型推断；循环或增强赋值可为空。
            annotation: 显式类型标注节点，用于优先推断类型族。
        返回:
            无业务返回值；实例化后会保存一条赋值记录的检查上下文。
        """

        # 保存被检查的变量名，用于命名形态和前缀规则判断。
        self.name = name  # 被检查的变量名

        # 保存变量名节点，报告时需要读取原始源码行号。
        self.node = node  # 变量名所在 AST 节点

        # 保存赋值表达式，后续按字面量、容器和调用推断类型族。
        self.value = value  # 赋值右侧表达式

        # 保存显式标注，静态推断时优先于右侧表达式。
        self.annotation = annotation  # 显式类型标注节点

# 类型标注可能带泛型参数，前缀规则只需要主类型名称。
def annotation_name(annotation: ast.AST | None) -> str:
    """提取类型标注的主类型名称。

    参数:
        annotation: AST 中解析到的类型标注节点。

    返回:
        去掉下标参数后的类型名称；无法解析时返回空字符串。
    """

    # 没有标注时无法从 annotation 推断类型前缀。
    if annotation is None:

        # 缺少标注时无法推断类型族。
        return ""

    # 确认候选节点具备目标 AST 结构后读取专属字段。
    if isinstance(annotation, ast.Name):

        # Name 标注直接使用标识符文本。
        return annotation.id

    # Attribute 标注通常带模块限定名，需要保留完整路径参与类型族映射。
    if isinstance(annotation, ast.Attribute):

        # Attribute 标注保留模块路径，区分 pandas/numpy 类型。
        return get_call_name(annotation)

    # Subscript 标注只依赖最外层容器类型决定前缀规则。
    if isinstance(annotation, ast.Subscript):

        # 泛型标注只取外层容器类型。
        return annotation_name(annotation.value)

    # 字符串前向引用也允许参与静态类型族映射。
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):

        # 字符串标注先去掉泛型参数再交给类型族映射。
        return annotation.value.split("[", 1)[0].strip()

    # 其他复杂标注不参与自动类型前缀推断。
    return ""

# 内置和常见第三方类型先归一到受控类型族。
def normalized_type_name(type_name: str) -> str:
    """把标注或调用名规整到内部类型族。

    参数:
        type_name: 待转换为变量前缀的类型名称。

    返回:
        内部类型族名称；未命中内置映射时返回空字符串。
    """

    # 生成loweredtypename的小写副本，确保关键词匹配不受大小写影响。
    str_lowered_type_name = type_name.lower()  # 生成大小写无关文本，保证关键词匹配稳定

    # Path 及其子类统一映射到路径前缀规则。
    if str_lowered_type_name in {"path", "pathlib.path"} or type_name.endswith("Path"):

        # Path 及其子类按路径前缀规则处理。
        return "path"

    # 未登记类型交给自定义类型前缀逻辑兜底。
    return TYPE_NAME_FAMILY_MAP.get(str_lowered_type_name, "")

# 未登记类型可降级为自定义类名前缀，但 Any/object 不自动要求。
def custom_type_prefix(type_name: str) -> str:
    """把未知标注类型转换为保守的小写前缀。

    参数:
        type_name: 待转换为变量前缀的类型名称。

    返回:
        未知类型对应的小写 snake_case 前缀；Any/object 或空输入返回空字符串。
    """

    # 空标注没有可用的类型语义。
    if not type_name:

        # 保持空字符串，让调用方继续尝试其他推断来源。
        return ""

    # 只用最后一段类名生成变量前缀，避免模块路径污染变量名。
    str_raw_name = type_name.rsplit(".", 1)[-1]  # 去掉模块路径后的类型名

    # 将驼峰类名拆成 snake_case 候选前缀。
    str_snake_name = re.sub(r"(?<!^)(?=[A-Z])", "_", str_raw_name).lower()  # 驼峰拆分后的类型名前缀

    # 去除非标识符字符，确保建议前缀可以进入 Python 变量名。
    str_snake_name = re.sub(r"[^a-z0-9_]+", "_", str_snake_name).strip("_")  # 可作为变量名前缀的安全文本

    # Any/object 不能证明具体语义，强制前缀会制造误导。
    if not str_snake_name or str_snake_name in {"any", "object"}:

        # 保持空字符串，让 PG041 提醒人工确认类型。
        return ""

    # 调用方会追加下划线并与当前变量名前缀比较。
    return str_snake_name

# `collect_function_return_types` 收集函数return类型集合。
def collect_function_return_types(tree: ast.Module) -> dict[str, str]:
    """收集当前文件内函数返回标注，供调用赋值前缀判断使用。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。

    返回:
        函数名到返回类型族的映射；无法识别的函数不会写入。
    """

    # 记录本文件函数返回标注，供后续调用赋值复用。
    dict_return_types: dict[str, str] = {}  # 函数名到返回类型族的映射

    # 遍历整棵语法树，逐个提取可转成赋值记录的节点。
    for node in ast.walk(tree):

        # 只有函数和异步函数节点才携带可复用的返回标注信息。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 函数返回标注是调用赋值类型推断的唯一静态来源。
            str_return_annotation = annotation_name(node.returns)  # 函数返回标注名称

            # 返回标注先匹配内置类型族，再降级为自定义类名前缀。
            str_inferred_type = normalized_type_name(str_return_annotation) or custom_type_prefix(str_return_annotation)  # 返回标注映射出的类型族

            # 非空返回类型族才写入函数返回索引。
            if str_inferred_type:

                # 函数名是后续 Call 节点回查返回类型的键。
                dict_return_types[node.name] = str_inferred_type  # 函数返回类型族

    # 返回当前文件内可供调用赋值复用的返回类型索引。
    return dict_return_types

# 显式变量标注比右侧表达式更可信。
def type_from_annotation(annotation: ast.AST | None) -> str:
    """从显式类型标注中读取变量类型族。

    参数:
        annotation: AST 中解析到的类型标注节点。

    返回:
        由标注推断出的类型族；无法推断时返回空字符串。
    """

    # 先把 AST 标注规整成文本，再映射到规则内部类型族。
    str_annotation_text = annotation_name(annotation)  # 类型标注名称

    # 未登记的自定义类也可以产生保守前缀。
    return normalized_type_name(str_annotation_text) or custom_type_prefix(str_annotation_text)

# 字面量表达式可以直接给出基础类型族。
def type_from_constant(value: ast.AST | None) -> str:
    """根据字面常量推断变量类型族。

    参数:
        value: 当前规则正在分析的表达式或配置值。

    返回:
        字面量对应的基础类型族；非支持常量返回空字符串。
    """

    # 只有字面常量能直接映射到基础类型族。
    if not isinstance(value, ast.Constant):

        # 非常量表达式留给后续容器或调用推断处理。
        return ""

    # bool 必须排在 int 前面，因为 bool 是 int 的子类。
    tuple_constant_types = (
        (bool, "bool"),  # 布尔字面量必须最先匹配，避免被 int 抢先命中
        (int, "int"),  # 普通整数值映射到 int 类型族
        (float, "float"),  # 浮点字面量映射到 float 类型族
        (str, "str"),  # 文本字面量映射到 str 类型族
        (bytes, "bytes"),  # 字节串字面量映射到 bytes 类型族
    )  # 常量类型判断使用的候选顺序

    # 按 bool、int、float、str、bytes 的顺序匹配字面量类型。
    for class_type_class, str_type_name in tuple_constant_types:

        # 当前候选 Python 运行时类型命中后即可确认常量类型族。
        if isinstance(value.value, class_type_class):

            # 首个命中的 Python 字面量类型就是变量前缀类型族。
            return str_type_name

    # None 和其他常量没有强制类型前缀。
    return ""

# 容器字面量节点类型可直接映射到变量名前缀。
def type_from_container_literal(value: ast.AST | None) -> str:
    """根据容器字面量推断变量类型族。

    参数:
        value: 当前规则正在分析的表达式或配置值。

    返回:
        dict/list/tuple/set 字面量对应的类型族；不匹配时返回空字符串。
    """

    # 只处理语法上可证明的内置容器字面量。
    dict_container_types = {
        ast.Dict: "dict",  # 字典字面量映射到 dict 类型族
        ast.List: "list",  # 列表字面量映射到 list 类型族
        ast.Tuple: "tuple",  # 元组字面量映射到 tuple 类型族
        ast.Set: "set",  # 集合字面量映射到 set 类型族
    }  # 容器字面量判断使用的类型映射

    # 容器字面量节点类型直接决定变量名前缀。
    for class_node_type, str_type_name in dict_container_types.items():

        # 当前 AST 节点类型命中后即可确认容器类型族。
        if isinstance(value, class_node_type):

            # AST 节点类型命中后即可确定容器类型族。
            return str_type_name

    # 其他表达式交给调用推断或 PG041 处理。
    return ""

# 调用表达式从构造器名称或本文件函数返回标注推断类型。
def type_from_call(value: ast.AST | None, return_types: dict[str, str]) -> str:
    """根据调用表达式和本文件返回标注推断变量类型族。

    参数:
        value: 当前规则正在分析的表达式或配置值。
        return_types: 按函数名记录的静态返回类型推断结果。

    返回:
        调用表达式对应的类型族；无可用推断时返回空字符串。
    """

    # 非调用表达式不能通过函数返回标注推断。
    if not isinstance(value, ast.Call):

        # 保持空字符串，让容器或类型标注逻辑继续兜底。
        return ""

    # 保存调用表达式名称，识别导入期副作用和输出副作用。
    str_call_name = get_call_name(value.func)  # 调用名称

    # 构造器或已知工厂函数名称可直接映射到类型族。
    str_inferred_type = normalized_type_name(str_call_name)  # 调用名映射出的类型族

    # 构造器名称能推断类型时直接返回。
    if str_inferred_type:

        # 已知构造器优先于本文件函数返回标注。
        return str_inferred_type

    # 普通函数调用只能复用同文件函数的返回标注。
    return return_types.get(str_call_name, "")

# 类型族推断按可信来源排序，避免低可信调用覆盖显式标注。
def infer_type_family(
    value: ast.AST | None,
    annotation: ast.AST | None,
    return_types: dict[str, str],
) -> str:
    """用静态语法启发式推断变量类型族。

    参数:
        value: 当前规则正在分析的表达式或配置值。
        annotation: AST 中解析到的类型标注节点。
        return_types: 按函数名记录的静态返回类型推断结果。

    返回:
        按标注、常量、容器字面量、调用顺序得到的类型族；无法推断时返回空字符串。
    """

    # 候选顺序体现静态证据强度：标注最强，调用返回标注最弱。
    tuple_type_candidates = (
        type_from_annotation(annotation),  # 显式类型标注拥有最高可信度
        type_from_constant(value),  # 字面量常量提供次一级可信度
        type_from_container_literal(value),  # 容器字面量补充常见结构推断
        type_from_call(value, return_types),  # 调用返回类型作为最后兜底来源
    )  # 推断流程按可信度依次选择的候选类型

    # 按可信度顺序选择第一个非空类型族。
    for str_type_family in tuple_type_candidates:

        # 找到可用类型族后停止降级推断。
        if str_type_family:

            # 使用第一个非空类型族作为命名前缀依据。
            return str_type_family

    # 所有静态来源都失败时由 PG041 提示人工确认。
    return ""

# 赋值目标可能是解包结构，需要展开到每一个 Name 节点。
def iter_target_names(target: ast.AST) -> list[ast.Name]:
    """展开赋值目标中的 Name 节点。

    参数:
        target: 赋值语句左侧的目标 AST 节点。

    返回:
        赋值目标中所有可检查的 Name 节点；属性和下标目标会被忽略。
    """

    # 普通名称目标可以直接进入命名检查与重命名流程。
    if isinstance(target, ast.Name):

        # 普通变量赋值可以直接进入命名规则检查。
        return [target]

    # 元组和列表解包需要递归展开其中的名称目标。
    if isinstance(target, (ast.Tuple, ast.List)):

        # 解包赋值需要汇总每个元素内的变量目标。
        list_names: list[ast.Name] = []  # 解包目标中的变量节点

        # 解包赋值的每个元素都可能包含变量名。
        for child in target.elts:

            # 递归展开嵌套解包结构中的名称节点。
            list_names.extend(iter_target_names(child))

        # 解包结构中的属性或下标目标已经在递归中被过滤。
        return list_names

    # 属性、下标和其他复杂目标不适合自动重命名。
    return []

# 不同赋值语法会生成同一种命名检查记录。
def records_for_assignment_node(node: ast.AST) -> list[AssignmentRecord]:
    """把单个赋值或循环节点转换为命名检查记录。

    参数:
        node: 当前遍历到的 AST 节点。

    返回:
        该语句中每个赋值目标对应的命名检查记录。
    """

    # 每个左侧变量都要保留自己的 AST 位置，方便精确报告。
    list_records: list[AssignmentRecord] = []  # 当前语句的赋值检查记录

    # 普通 Assign 可能有一个或多个左侧目标。
    if isinstance(node, ast.Assign):

        # 多目标赋值的每个左侧目标都要展开。
        for target in node.targets:

            # 每个名称节点生成独立检查记录。
            for name_node in iter_target_names(target):

                # Assign 记录使用右侧表达式做类型族推断。
                list_records.append(AssignmentRecord(name_node.id, name_node, node.value))

        # 普通赋值可能有多个左侧目标，全部已展开。
        return list_records

    # AnnAssign 只含单个左侧目标，但会额外携带标注。
    if isinstance(node, ast.AnnAssign):

        # 标注赋值的目标同样可能是解包结构。
        for name_node in iter_target_names(node.target):

            # 标注记录优先使用 annotation 推断前缀。
            list_records.append(AssignmentRecord(name_node.id, name_node, node.value, node.annotation))

        # 标注赋值记录会携带 annotation，优先用于类型前缀判断。
        return list_records

    # For/AugAssign 共用 target 字段，但右侧表达式不适合前缀推断。
    # AugAssign 和 For 共享 target 字段，统一展开名称节点。
    if isinstance(node, (ast.AugAssign, ast.For)) and getattr(node, "target", None) is not None:

        # 循环变量和增强赋值目标只做基础命名检查。
        for name_node in iter_target_names(getattr(node, "target")):

            # 这类记录没有可依赖的右侧类型表达式。
            list_records.append(AssignmentRecord(name_node.id, name_node, None))

    # 非赋值语句不会产生命名检查记录。
    return list_records

# 全文件扫描用于报告 PG038-PG041 的变量命名问题。
def assignment_records(tree: ast.Module) -> list[AssignmentRecord]:
    """收集变量命名规则需要检查的赋值记录。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。

    返回:
        当前文件所有赋值、增强赋值和 for 目标的命名检查记录。
    """

    # 全文件记录包含嵌套作用域，报告阶段需要覆盖所有变量。
    list_records: list[AssignmentRecord] = []  # 当前文件的赋值检查记录

    # 扫描 AST 节点，寻找本规则关注的语法结构。
    for node in ast.walk(tree):

        # 合并子扫描产物，保持最终报告覆盖所有候选。
        list_records.extend(records_for_assignment_node(node))

    # 调用方按收集顺序逐条登记质量门发现项。
    return list_records

# 自动重命名只在模块或函数的直接语句体内建立作用域。
def scope_body(scope: ast.AST) -> list[ast.stmt]:
    """返回模块或函数作用域的直接语句体。

    参数:
        scope: 当前变量记录所属的词法作用域。

    返回:
        模块或函数直接拥有的语句列表；没有 body 时返回空列表。
    """

    # 只有拥有 body 字段的模块、类和函数节点可继续遍历。
    list_body = getattr(scope, "body", [])  # 作用域直接语句列表

    # body 字段只有在语句列表形态下才可作为作用域正文返回。
    if isinstance(list_body, list):

        # 返回直接语句体，嵌套作用域过滤由 iter_scope_nodes 负责。
        return list_body

    # 没有语句体的节点不参与作用域级自动重命名。
    return []

# 遍历一个重命名作用域时必须避开内部函数和类。
def iter_scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """遍历单个作用域内节点，跳过嵌套函数、类和 lambda。

    参数:
        scope: 当前变量记录所属的词法作用域。

    返回:
        该作用域内可检查的 AST 节点列表，不包含嵌套作用域内部节点。
    """

    # 自动重命名只依赖当前作用域自己的节点。
    list_nodes: list[ast.AST] = []  # 当前作用域内可检查节点

    # 显式栈避免递归进入已经排除的嵌套作用域。
    list_stack: list[ast.AST] = list(scope_body(scope))  # 待遍历的直接子节点栈

    # 持续扫描直到 `list_stack` 不再满足
    while list_stack:

        # 弹出的节点若是作用域边界，其子树不能参与当前作用域改名。
        a_s_t_node: ast.AST = list_stack.pop()  # 当前待分析的 AST 节点

        # 遇到内层函数、类或 lambda 时停止向该子树继续下钻。
        if isinstance(a_s_t_node, NESTED_SCOPE_NODES):

            # 该边界会在自己的作用域扫描中单独处理。
            continue

        # 把当前节点收进当前作用域的可检查节点集合。
        list_nodes.append(a_s_t_node)

        # 继续压入子节点，保持当前作用域的深度优先扫描。
        list_stack.extend(ast.iter_child_nodes(a_s_t_node))

    # 返回值只覆盖当前词法作用域的可检查节点。
    return list_nodes

# 安全重命名按词法作用域生成候选，避免跨函数误改。
def assignment_records_for_scope(scope: ast.AST) -> list[AssignmentRecord]:
    """收集当前作用域内的赋值记录，不跨入嵌套作用域。

    参数:
        scope: 当前变量记录所属的词法作用域。

    返回:
        该作用域内可检查的赋值记录，不包含嵌套作用域内部记录。
    """

    # 作用域内记录用于判断同名变量是否能一致重命名。
    list_records: list[AssignmentRecord] = []  # 当前作用域赋值检查记录

    # 作用域内逐条语句展开赋值记录。
    for node in iter_scope_nodes(scope):

        # 嵌套函数体已由 iter_scope_nodes 排除在当前作用域之外。
        list_records.extend(records_for_assignment_node(node))

    # 这些记录会在同一作用域内做冲突检查。
    return list_records

# 模块和函数是当前实现允许自动改名的作用域边界。
def iter_rename_scopes(tree: ast.Module) -> list[ast.AST]:
    """返回可进行局部变量重命名判断的作用域。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。

    返回:
        模块作用域和每个函数作用域的 AST 节点列表。
    """

    # 模块级变量也允许生成安全重命名建议。
    list_scopes: list[ast.AST] = [tree]  # 模块和函数级重命名作用域

    # 遍历全树，找出允许单独生成改名建议的函数节点。
    for node in ast.walk(tree):

        # 只有函数节点才构成额外的可改名词法作用域。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            # 函数作用域可独立生成安全重命名建议。
            list_scopes.append(node)

    # 类作用域暂不自动改名，避免影响属性或反射约定。
    return list_scopes

# 函数参数属于公开调用契约，自动重命名必须避开。
def public_parameter_names(scope: ast.AST) -> set[str]:
    """收集函数公开签名参数，自动重命名时必须避开。

    参数:
        scope: 当前变量记录所属的词法作用域。

    返回:
        公开函数签名中不能自动改名的参数名集合。
    """

    # 非函数作用域没有公开签名参数。
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):

        # 返回去重后的名称集合。
        return set()

    # 参数名集合用于过滤自动重命名候选。
    set_parameters: set[str] = set()  # 公开签名参数名集合

    # ast.arguments 集中保存普通参数、仅关键字参数和可变参数。
    arguments_args: ast.arguments = scope.args  # 函数参数 AST 节点集合

    # 普通参数、位置专用参数和仅关键字参数都属于公开签名。
    for arg in [*arguments_args.posonlyargs, *arguments_args.args, *arguments_args.kwonlyargs]:

        # 参数集合用于禁止自动改动调用契约。
        set_parameters.add(arg.arg)

    # 可变位置参数同样属于公开签名。
    if arguments_args.vararg is not None:

        # 自动重命名不能改变 *args 名称。
        set_parameters.add(arguments_args.vararg.arg)

    # 可变关键字参数同样属于公开签名。
    if arguments_args.kwarg is not None:

        # **kwargs 名称同样属于公开调用契约的一部分。
        set_parameters.add(arguments_args.kwarg.arg)

    # 函数签名收集结束后返回去重参数名。
    return set_parameters

# token 替换需要回到同一作用域内所有同名 Name 节点。
def name_nodes_for_scope(scope: ast.AST, name: str) -> list[ast.Name]:
    """收集当前作用域内与名称匹配的所有 Name 节点。

    参数:
        scope: 当前变量记录所属的词法作用域。
        name: 需要替换的原变量名。

    返回:
        同一作用域内与目标名称一致的 Name 节点列表。
    """

    # 保存精确节点位置，后续会转换为 tokenize 替换动作。
    list_name_nodes: list[ast.Name] = []  # 同名 NAME token 节点

    # 只扫描当前词法作用域内的 Name 节点。
    for node in iter_scope_nodes(scope):

        # 只保留与目标名称完全匹配的 Name 节点。
        if isinstance(node, ast.Name) and node.id == name:

            # 行列号后续用于定位真实 token。
            list_name_nodes.append(node)

    # 调用方会用这些节点的行列号定位真实 token。
    return list_name_nodes

# f-string 表达式里的名称不会被 tokenize 以普通 NAME 位置稳定替换。
def f_string_reference_names_for_scope(scope: ast.AST) -> set[str]:
    """收集当前作用域内 f-string 表达式引用过的名称。

    参数:
        scope: 当前变量记录所属的词法作用域。

    返回:
        f-string 表达式内部引用到的变量名集合。
    """

    # 这些名称必须退出自动重命名，避免只改定义不改 f-string 引用。
    set_names: set[str] = set()  # f-string 表达式引用名集合

    # 只扫描当前词法作用域，避免外层变量被内层 f-string 误阻断。
    for node in iter_scope_nodes(scope):

        # JoinedStr 是 Python AST 对 f-string 的顶层表示。
        if not isinstance(node, ast.JoinedStr):

            # 非 JoinedStr 节点不可能携带需要保护的 f-string 引用。
            continue

        # f-string 的 FormattedValue 子树可能包含普通 Name 引用。
        for child in ast.walk(node):

            # 只记录表达式内直接出现的变量名，属性名不在这里改写。
            if isinstance(child, ast.Name):

                # 记录后续自动改名必须跳过的 f-string 引用名。
                set_names.add(child.id)

    # 这一轮收集的 f-string 引用名将用于阻止自动改名。
    return set_names

# 嵌套作用域中的名称引用不能由外层 token 动作完整覆盖。
def _name_ids_in_subtree(node: ast.AST) -> set[str]:
    """收集任意子树中出现过的 Name 标识符。

    参数:
        node: 需要扫描的 AST 子树根节点。

    返回:
        该子树内所有 Name 标识符的去重集合。
    """

    # 记录子树中所有名称，供闭包和嵌套作用域规则复用。
    set_names: set[str] = set()  # 子树中的 Name 标识符集合

    # 遍历整个子树，提取所有普通名称引用。
    for child in ast.walk(node):

        # 任意 Name 都可能参与闭包读取、nonlocal 写回或局部遮蔽边界。
        if isinstance(child, ast.Name):

            # 记录该子树中出现过的变量标识符。
            set_names.add(child.id)

    # 返回给嵌套作用域分析逻辑复用。
    return set_names

# 内层函数或 lambda 中的引用会阻断外层自动改名。
def nested_scope_reference_names_for_scope(scope: ast.AST) -> set[str]:
    """收集当前作用域内部嵌套作用域引用过的名称。

    参数:
        scope: 当前变量记录所属的词法作用域。

    返回:
        内层函数、类或 lambda 子树中出现过的名称集合。
    """

    # 内层引用名用于让外层变量退出自动重命名。
    set_names: set[str] = set()  # 嵌套作用域中出现过的变量名集合

    # 只检查当前作用域直接包含的语句，避免把更外层节点混入判断。
    list_stack: list[ast.AST] = list(scope_body(scope))  # 待查找的当前作用域直接节点

    # 遍历当前作用域结构并定位真正的嵌套作用域边界。
    while list_stack:

        # 当前节点决定是否进入闭包风险扫描。
        a_s_t_node: ast.AST = list_stack.pop()  # 当前待分析节点

        # 嵌套作用域整体扫描，避免外层变量被改名后内层引用仍保留旧名。
        if isinstance(a_s_t_node, NESTED_SCOPE_NODES):

            # 记录嵌套子树中所有名称，保守退出外层自动改名。
            set_names.update(_name_ids_in_subtree(a_s_t_node))

            # 记录完嵌套子树名称后立即跳过其内部继续展开。
            continue

        # 非嵌套边界继续向下查找可能的内层函数或 lambda。
        list_stack.extend(ast.iter_child_nodes(a_s_t_node))

    # 嵌套作用域引用名会被上层安全重命名阶段整体过滤。
    return set_names

# `is_exempt_name` 判断exempt名称。
def is_exempt_name(name: str) -> bool:
    """判断名称是否属于规则豁免。

    参数:
        name: 当前规则正在检查的标识符名称。

    返回:
        布尔值，表示判断exempt名称是否成立。
    """

    # 约定豁免名和短循环名不要求类型前缀。
    if name in EXEMPT_NAMES or name in SHORT_LOOP_NAMES:

        # 这些名称不进入后续命名检查。
        return True

    # 双下划线魔术名由 Python 协议定义，不能改写。
    if name.startswith("__") and name.endswith("__"):

        # 魔术名直接视为豁免。
        return True

    # 返回该规则是否命中目标条件。
    return False

# 类型族到变量名前缀的映射是 PG039 的核心契约。
def type_prefix(inferred_type: str) -> str:
    """返回变量名必须使用的类型前缀。

    参数:
        inferred_type: 从赋值右侧表达式推断出的变量类型名称。

    返回:
        类型族对应的变量名前缀；未知类型族使用 `<type>_`。
    """

    # 自定义类型族使用同名 snake_case 前缀兜底。
    return TYPE_PREFIXES.get(inferred_type, f"{inferred_type}_")

# 建议名只在当前名称缺少类型前缀时添加前缀。
def prefixed_name(name: str, inferred_type: str) -> str:
    """根据类型族生成建议名称。

    参数:
        name: 当前规则正在检查的标识符名称。
        inferred_type: 从赋值右侧表达式推断出的变量类型名称。

    返回:
        带类型前缀的建议名称；已有前缀时返回原名。
    """

    # 类型族决定变量名应当具备的前缀。
    str_prefix = type_prefix(inferred_type)  # 类型族对应的命名前缀

    # 已经合规的名称不再重复添加前缀。
    if not str_prefix or name.startswith(str_prefix):

        # 保持原名，避免生成 `str_str_name` 这类重复建议。
        return name

    # 缺失前缀时才拼接推荐前缀生成新名称。
    return f"{str_prefix}{name}"

# `is_module_level_constant_record` 判断模块levelconstantrecord。
def is_module_level_constant_record(record: AssignmentRecord) -> bool:
    """判断赋值记录是否代表模块级全大写常量。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。

    返回:
        布尔值，表示判断modulelevelconstantrecord是否成立。
    """

    # 非全大写名称不是模块常量候选。
    if not CONSTANT_NAME_PATTERN.fullmatch(record.name):

        # 命名形态不满足常量约束时立即退出模块常量豁免。
        return False

    # 父节点应当是模块体中的赋值语句。
    # 只有模块顶层全大写赋值能豁免普通变量前缀检查。
    return (
        isinstance(getattr(record.node, "parent", None), (ast.Assign, ast.AnnAssign))
        and isinstance(getattr(getattr(record.node, "parent", None), "parent", None), ast.Module)
    )

# `is_class_body_field_record` 判断类函数体fieldrecord。
def is_class_body_field_record(record: AssignmentRecord) -> bool:
    """判断赋值记录是否来自类体字段声明。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。

    返回:
        布尔值，表示判断class正文fieldrecord是否成立。
    """

    # 类体字段的变量名父节点必须是类体直接赋值语句。
    # 没有赋值声明节点时不按类字段豁免处理。
    if not isinstance(getattr(record.node, "parent", None), (ast.Assign, ast.AnnAssign)):

        # 记录继续按普通局部变量检查。
        return False

    # 类字段可能被外部按属性名访问，自动命名规则不强制修改。
    return isinstance(getattr(getattr(record.node, "parent", None), "parent", None), ast.ClassDef)

# `is_module_level_type_alias_record` 判断模块level类型aliasrecord。
def is_module_level_type_alias_record(record: AssignmentRecord) -> bool:
    """判断赋值记录是否代表模块级 PascalCase 类型别名。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。

    返回:
        布尔值，表示判断modulelevel类型aliasrecord是否成立。
    """

    # 类型别名通常以 PascalCase 命名，小写变量不进入该豁免。
    if not record.name[:1].isupper():

        # 首字母不是大写时不可能是模块级类型别名。
        return False

    # 父节点应当是模块顶层的赋值或标注赋值。
    # 模块顶层 PascalCase 赋值通常是类型别名或类兼容名称。
    return (
        isinstance(getattr(record.node, "parent", None), (ast.Assign, ast.AnnAssign))
        and isinstance(getattr(getattr(record.node, "parent", None), "parent", None), ast.Module)
    )

# 保守豁免避免破坏公共 API、常量、类字段和类型别名。
def should_skip_assignment_record(record: AssignmentRecord) -> bool:
    """判断赋值记录是否位于命名规则的保守豁免范围。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。

    返回:
        True 表示该记录不应进入变量命名和前缀检查。
    """

    # 变量名会先经过语言关键字和约定名称豁免。
    str_name = record.name  # 生成 PG039/PG042 消息时引用的原变量名

    # 豁免名或关键字不能进入变量命名诊断。
    if is_exempt_name(str_name) or keyword.iskeyword(str_name):

        # 这类名称无需继续检查。
        return True

    # 特殊声明形式由各自的结构判断函数处理。
    return (
        is_module_level_constant_record(record)
        or is_class_body_field_record(record)
        or is_module_level_type_alias_record(record)
    )

# `check_record_shape_name` 检查并登记recordshape名称。
def check_record_shape_name(record: AssignmentRecord, filepath: Path, issues: list[Issue]) -> None:
    """检查变量基础形态和空泛命名。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无返回值；命中问题时直接向 issues 追加 PG038 或 PG040。
    """

    # 同一个变量名要同时检查形态和语义空泛问题。
    str_name = record.name  # 当前记录接受形态与语义双重检查的名称

    # 非 snake_case 名称会阻碍自动前缀建议的可靠生成。
    if not SNAKE_CASE_PATTERN.fullmatch(str_name):

        # 登记当前规则识别出的质量门问题。
        add_issue(
            issues,
            "PG038",
            "BLOCKER",
            filepath,
            getattr(record.node, "lineno", 1),
            f"变量 `{str_name}` 不符合 snake_case。",
        )

    # 空泛名称即便满足 snake_case，也仍需追加语义层面的阻断提示。
    if str_name in VAGUE_NAMES:

        # 追加 PG040，提示名称需要换成更具体的业务词。
        add_issue(
            issues,
            "PG040",
            "BLOCKER",
            filepath,
            getattr(record.node, "lineno", 1),
            f"变量 `{str_name}` 语义过空泛。",
        )

# 无法静态推断类型时只给 NOTE，除非变量名已经带精确前缀。
def has_semantic_type_hint(str_name: str) -> bool:
    """判断变量名是否已经包含受控语义类型线索。

    参数:
        str_name: 当前规则正在检查的变量名。

    返回:
        True 表示变量名已经携带路径、文本、报告等明确领域线索。
    """

    # 下划线拆分可以避免 `profiled` 之类偶然包含词根的误判。
    set_name_parts = {part for part in str_name.split("_") if part}  # 变量名中的语义片段集合

    # 与受控词根相交时，类型未知 NOTE 不再提供额外价值。
    return bool(set_name_parts.intersection(SEMANTIC_TYPE_HINT_TERMS))

# 无法静态推断类型时只给 NOTE，除非变量名已经带精确前缀或语义线索。
def report_missing_type_if_needed(record: AssignmentRecord, filepath: Path, issues: list[Issue]) -> bool:
    """在类型无法静态确定时报告 PG041 并返回是否已处理。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        True 表示该记录已经通过精确前缀或 PG041 处理完毕。
    """

    # 已有精确类型前缀或受控语义线索时不再提示类型未知，减少噪声。
    if record.name.startswith(PRECISE_TYPE_PREFIXES) or has_semantic_type_hint(record.name):

        # 这类记录已经完成 PG041 的噪声抑制判断。
        return True

    # 缺少可证明类型线索时补记 PG041，提醒人工确认类型。
    add_issue(
        issues,
        "PG041",
        "NOTE",
        filepath,
        getattr(record.node, "lineno", 1),
        f"变量 `{record.name}` 的类型无法静态确定。",
    )

    # 无论是否补记 NOTE，调用方都不需要继续追加 PG041。
    return True

# 前缀不匹配会同时报告建议名和自动改名安全边界。
def report_prefix_mismatch(
    record: AssignmentRecord,
    filepath: Path,
    issues: list[Issue],
    str_inferred_type: str,
) -> None:
    """在类型前缀不匹配时报告 PG039 和安全边界提示。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        str_inferred_type: 推断出的类型名称。

    返回:
        无返回值；该函数只向 issues 追加 PG039 或 PG042。
    """

    # 原变量名用于生成面向用户的质量门消息。
    str_name = record.name  # 生成 PG039/PG042 消息时使用的原变量名

    # 建议名只添加当前类型族要求的前缀。
    str_suggested_name = prefixed_name(str_name, str_inferred_type)  # 带类型前缀的建议变量名

    # 类型前缀建议保留为 backlog，避免自动重命名公开契约或 JSON 字段。
    add_issue(
        issues,
        "PG039",
        "WARNING",
        filepath,
        getattr(record.node, "lineno", 1),
        f"变量 `{str_name}` 可推断为 {str_inferred_type}，建议使用 `{str_suggested_name}`。",
    )

    # 非白名单类型族的重命名建议只能提示人工确认，不能自动落盘。
    if str_inferred_type not in SAFE_RENAME_PREFIX_TYPES:

        # 这类建议只补充安全边界提醒，避免误导为可自动改名。
        add_issue(
            issues,
            "PG042",
            "WARNING",
            filepath,
            getattr(record.node, "lineno", 1),
            f"变量 `{str_name}` 有重命名建议，但需要人工确认安全边界。",
        )

# 单条赋值记录先检查基础命名，再检查类型前缀。
def check_single_record(
    record: AssignmentRecord,
    filepath: Path,
    issues: list[Issue],
    return_types: dict[str, str],
) -> None:
    """检查单个赋值记录并追加对应问题。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。
        return_types: 按函数名记录的静态返回类型推断结果。
    """

    # 常量、类字段、类型别名和约定名称由豁免规则保护。
    if should_skip_assignment_record(record):

        # 交回调用方
        return

    # 检查变量的 snake_case 和空泛命名问题
    check_record_shape_name(record, filepath, issues)

    # 静态推断结果决定是否需要报告类型前缀不匹配。
    str_inferred_type = infer_type_family(record.value, record.annotation, return_types)  # 赋值记录推断出的类型族

    # 无法推断时只登记 PG041，不强行生成可能错误的前缀。
    if not str_inferred_type:

        # 报告类型未知或确认已有精确前缀
        report_missing_type_if_needed(record, filepath, issues)

        # 当前 profile 未启用此规则时直接结束检查。
        return

    # 期望前缀来自类型族映射或自定义类型名称。
    str_expected_prefix = type_prefix(str_inferred_type)  # 当前类型族要求的变量名前缀

    # 原名要和期望前缀逐一比对，才能判断是否真的缺少类型前缀。
    str_name = record.name  # 与期望前缀逐一比对的原始变量名

    # 推断出类型族且前缀不匹配时报告 PG039。
    if str_expected_prefix and not str_name.startswith(str_expected_prefix):

        # 这里登记的是前缀不匹配问题，同时保留人工确认边界。
        report_prefix_mismatch(record, filepath, issues, str_inferred_type)

# 循环变量和增强赋值目标缺少可靠右侧类型，只检查基础形态。
def check_loop_or_augassign_target(record: AssignmentRecord, filepath: Path, issues: list[Issue]) -> bool:
    """检查循环和增强赋值目标的基础 snake_case 形态。

    参数:
        record: 变量命名检查过程中收集到的单个变量记录。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        True 表示记录属于循环或增强赋值目标且已完成基础命名检查。
    """

    # 只有 Name 节点才可能是循环变量或增强赋值目标。
    if not isinstance(record.node, ast.Name):

        # 其他节点交给普通记录检查。
        return False

    # Store 上下文表示这个名称正在被赋值。
    if not isinstance(getattr(record.node, "ctx", None), ast.Store):

        # Load 名称不是赋值目标。
        return False

    # 父节点决定该 Store 名称是否来自 For 或 AugAssign。
    # 父节点必须是 For 或 AugAssign 才走基础命名分支。
    if not isinstance(getattr(record.node, "parent", None), (ast.AugAssign, ast.For)):

        # 普通赋值记录继续做类型前缀推断。
        return False

    # 循环变量仍要满足 snake_case，除非是 i/j/k 等短索引。
    str_name = record.name  # 循环或增强赋值变量名

    # 这里不做类型前缀判断，只报告基础命名形态问题。
    if not is_exempt_name(str_name) and not SNAKE_CASE_PATTERN.fullmatch(str_name):

        # 这里仅登记基础命名问题，不把循环变量误带入类型前缀规则。
        add_issue(
            issues,
            "PG038",
            "BLOCKER",
            filepath,
            getattr(record.node, "lineno", 1),
            f"变量 `{str_name}` 不符合 snake_case。",
        )

    # 走到这里说明记录已经按循环或增强赋值分支处理完毕。
    return True

# `check_variable_naming` 检查并登记variablenaming。
def check_variable_naming(tree: ast.Module, filepath: Path, issues: list[Issue]) -> None:
    """执行 PG038-PG041 变量命名检查。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。
        filepath: 需要读取、解析或检查的 Python 文件路径。
        issues: 用于累计当前质量门发现项的列表。

    返回:
        无业务返回值；发现项直接追加到 `issues`。
    """

    # 函数返回标注索引用于后续调用赋值的类型推断。
    dict_return_types = collect_function_return_types(tree)  # 函数返回类型族索引

    # 所有赋值记录按源码顺序执行命名检查。
    for record in assignment_records(tree):

        # 循环变量和增强赋值目标已在专门分支处理。
        if check_loop_or_augassign_target(record, filepath, issues):

            # 已处理的记录不再进入类型前缀推断。
            continue

        # 执行子规则检查，保持主流程只负责编排顺序。
        check_single_record(record, filepath, issues, dict_return_types)

# 单作用域内只有可证明无冲突的变量才进入自动重命名建议。
def scope_safe_rename_suggestions(scope: ast.AST, dict_return_types: dict[str, str]) -> dict[str, str]:
    """生成单个作用域内可证明安全的重命名建议。

    参数:
        scope: 当前变量记录所属的词法作用域。
        dict_return_types: 函数名到返回类型族的映射。

    返回:
        当前作用域内原变量名到安全建议名的映射。
    """

    # 公开参数不自动重命名，避免改变调用方契约。
    set_parameter_names = public_parameter_names(scope)  # 自动重命名必须避开的公开参数名

    # f-string 表达式引用当前无法由 token 写回稳定覆盖。
    set_f_string_names = f_string_reference_names_for_scope(scope)  # f-string 引用名集合

    # 内层函数引用外层变量时，外层 token 动作无法覆盖闭包引用。
    set_nested_reference_names = nested_scope_reference_names_for_scope(scope)  # 内层作用域引用名集合

    # 同一作用域内的同名变量必须得到同一个建议名才安全。
    dict_scope_suggestions: dict[str, str] = {}  # 当前作用域安全重命名建议

    # 单作用域内逐条赋值记录尝试生成建议名。
    for record in assignment_records_for_scope(scope):

        # 公开参数、豁免名和非 snake_case 名称不自动改写。
        if (
            record.name in set_parameter_names
            or record.name in set_f_string_names
            or record.name in set_nested_reference_names
            or is_exempt_name(record.name)
            or not SNAKE_CASE_PATTERN.fullmatch(record.name)
        ):

            # 当前记录不满足安全自动改名边界，直接跳过。
            continue

        # 这里要先拿到精确类型族，后面才能决定能否进入自动改名白名单。
        str_inferred_type = infer_type_family(record.value, record.annotation, dict_return_types)  # 当前记录静态推断出的候选类型族

        # 只有安全白名单里的类型族，才允许继续生成自动改名建议。
        if str_inferred_type not in SAFE_RENAME_PREFIX_TYPES:

            # 推断类型不在安全白名单时，不生成自动改名建议。
            continue

        # 这里生成的是同一作用域内后续冲突检查要复用的候选名。
        str_suggested_name = prefixed_name(record.name, str_inferred_type)  # 当前作用域准备加入冲突检查的候选新名

        # 比较结果决定当前规则是否需要登记问题。
        if str_suggested_name == record.name:

            # 原名已经满足当前类型族前缀要求，无需继续登记建议。
            continue

        # 同名变量必须收敛到同一个建议名，才能证明 token 级改写安全。
        str_existing_suggestion = dict_scope_suggestions.get(record.name)  # 同名变量已有建议

        # 发生冲突时放弃自动改名，避免同名变量映射到多个目标名。
        if str_existing_suggestion and str_existing_suggestion != str_suggested_name:

            # 同名变量建议冲突时直接跳过当前记录。
            continue

        # 记录该作用域内可统一替换的变量名。
        dict_scope_suggestions[record.name] = str_suggested_name  # 安全建议变量名

    # 作用域扫描结束后返回这一层可安全应用的建议映射。
    return dict_scope_suggestions

# 将作用域级建议转成 token 级动作，并记录跨作用域冲突。
def merge_scope_rename_suggestions(
    scope: ast.AST,
    dict_scope_suggestions: dict[str, str],
    list_actions: list[RenameAction],
    dict_suggestions: dict[str, str],
    set_conflicts: set[str],
) -> None:
    """把单作用域建议合并为全文件 token 级重命名动作。

    参数:
        scope: 当前变量记录所属的词法作用域。
        dict_scope_suggestions: 当前作用域内可安全重命名的变量建议映射。
        list_actions: 安全重命名流程准备应用的 token 替换动作列表。
        dict_suggestions: 按原变量名汇总的重命名建议映射。
        set_conflicts: 因命名冲突而不能自动应用的变量集合。

    返回:
        无返回值；命中建议时会原地更新动作、建议和冲突集合。
    """

    # 逐个登记作用域内可自动应用的建议。
    for str_old_name, str_new_name in dict_scope_suggestions.items():

        # 全文件同名变量若出现不同建议，必须整体放弃自动替换。
        str_previous_suggestion = dict_suggestions.get(str_old_name)  # 全文件已记录的建议名

        # 同一旧名称出现不同建议时标记为跨作用域冲突。
        if str_previous_suggestion and str_previous_suggestion != str_new_name:

            # 记录跨作用域冲突变量名
            set_conflicts.add(str_old_name)

            # 发生冲突后跳过当前建议，等待统一冲突清理阶段处理。
            continue

        # 报告映射只保留没有跨作用域歧义的建议。
        dict_suggestions[str_old_name] = str_new_name  # 全文件安全建议名

        # 同名节点全部生成 token 替换动作。
        for name_node in name_nodes_for_scope(scope, str_old_name):

            # 登记当前 token 位置的重命名动作
            list_actions.append(RenameAction(str_old_name, str_new_name, name_node.lineno, name_node.col_offset))

# 出现跨作用域歧义时同时剔除 token 动作和报告建议。
def remove_conflicted_rename_suggestions(
    list_actions: list[RenameAction],
    dict_suggestions: dict[str, str],
    set_conflicts: set[str],
) -> list[RenameAction]:
    """移除跨作用域出现歧义的重命名动作。

    参数:
        list_actions: 安全重命名流程准备应用的 token 替换动作列表。
        dict_suggestions: 按原变量名汇总的重命名建议映射。
        set_conflicts: 因命名冲突而不能自动应用的变量集合。

    返回:
        删除冲突变量后的 token 重命名动作列表。
    """

    # 没有冲突时可以直接使用原始动作列表。
    if not set_conflicts:

        # 返回的动作仍然只覆盖 AST 确认过的 NAME token。
        return list_actions

    # 冲突变量的所有 token 替换动作都要移除。
    list_filtered_actions = [
        action  # 仅保留未命中冲突名的安全替换动作
        for action in list_actions  # 逐个检查已收集的 token 级改名动作
        if action.old_name not in set_conflicts  # 过滤掉命中冲突变量名的动作
    ]  # 剔除冲突后的安全动作

    # 冲突变量需要同时从动作和报告建议中剔除。
    for old_name in set_conflicts:

        # 移除报告中的歧义建议
        dict_suggestions.pop(old_name, None)

    # 剩余动作可以安全交给 tokenize 写回。
    return list_filtered_actions

# 全文件安全重命名先按作用域计算，再统一消除冲突。
def scoped_rename_suggestions(tree: ast.Module) -> tuple[list[RenameAction], dict[str, str]]:
    """生成作用域内可证明安全的变量重命名动作。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。

    返回:
        可直接用于 token 替换的动作列表，以及对外报告的建议映射。
    """

    # 函数返回类型索引在每个作用域中复用。
    dict_return_types = collect_function_return_types(tree)  # 供各作用域回查调用结果类型的索引

    # token 动作保留精确行列号，避免替换注释或字符串。
    list_actions: list[RenameAction] = []  # token 级重命名动作

    # 建议映射用于 CLI 或调用方展示可自动应用的改名。
    dict_suggestions: dict[str, str] = {}  # 对外报告的重命名建议

    # 跨作用域同名但建议不同的变量不能自动替换。
    set_conflicts: set[str] = set()  # 跨作用域建议冲突变量

    # 每个可重命名作用域先独立计算建议，再全文件合并。
    for scope in iter_rename_scopes(tree):

        # 单作用域建议先保证不会改动公开参数或含糊类型。
        dict_scope_suggestions = scope_safe_rename_suggestions(scope, dict_return_types)  # 已经过豁免过滤的当前作用域建议

        # 合并当前作用域的 token 级安全重命名动作
        merge_scope_rename_suggestions(
            scope,
            dict_scope_suggestions,
            list_actions,
            dict_suggestions,
            set_conflicts,
        )

    # 冲突变量必须从写回动作和报告映射中同时删除。
    list_actions = remove_conflicted_rename_suggestions(  # 剔除冲突后的 token 动作
        list_actions,  # 待写回源码的 token 级动作列表
        dict_suggestions,  # 对外展示的建议映射
        set_conflicts,  # 需要整体放弃的冲突变量集合
    )

    # 调用方分别使用动作写回源码、使用映射展示建议。
    return list_actions, dict_suggestions

# 公开接口只暴露原名到建议名的映射，不暴露 token 细节。
def rename_suggestions(tree: ast.Module) -> dict[str, str]:
    """生成满足保守条件的变量安全重命名建议。

    参数:
        tree: 由 Python 源码解析得到的 AST 语法树。

    返回:
        原变量名到建议变量名的安全重命名映射。
    """

    # token 动作只供 apply_safe_renames 写回源码使用。
    return scoped_rename_suggestions(tree)[1]

# Python AST 列号使用 UTF-8 字节偏移，tokenize 使用源码字符偏移。
def ast_byte_col_to_text_col(line: str, byte_col: int) -> int:
    """把 AST 字节列号转换为 tokenize 使用的字符列号。

    参数:
        line: AST 节点所在的单行源码文本。
        byte_col: AST 节点记录的 UTF-8 字节列号。

    返回:
        与 tokenize token.start 兼容的字符列号。
    """

    # 纯 ASCII 行的字节列和字符列一致，可直接返回。
    if len(line) == len(line.encode("utf-8")):

        # ASCII 快路径避免对常规源码做额外扫描。
        return byte_col

    # 当前累计字节数用于和 AST 偏移对齐。
    int_seen_bytes = 0  # 已经遍历过的 UTF-8 字节数

    # 逐字符推进，遇到目标字节偏移时返回字符列。
    for int_col, str_char in enumerate(line):

        # 当前字符起点已经到达 AST 字节偏移。
        if int_seen_bytes >= byte_col:

            # 返回 tokenize 能匹配到的字符列号。
            return int_col

        # 追加当前字符在 UTF-8 中占用的字节数。
        int_seen_bytes += len(str_char.encode("utf-8"))  # 已累计的 UTF-8 字节数

    # 字节偏移落在行尾时，字符列就是当前行长度。
    return len(line)

# 将 AST 动作索引转换成 tokenize 可匹配的位置索引。
def action_index_for_source(source: str, actions: list[RenameAction]) -> dict[tuple[int, int], RenameAction]:
    """根据源码文本生成 token 替换动作索引。

    参数:
        source: 待分析或改写的 Python 源码文本。
        actions: 安全重命名流程生成的 AST 字节列动作。

    返回:
        tokenize 行列位置到重命名动作的映射。
    """

    # splitlines 保留源码行内容，行号仍按 AST 的一基序号索引。
    list_lines = source.splitlines()  # 源码物理行列表

    # token 位置索引用于快速匹配 generate_tokens 结果。
    dict_actions: dict[tuple[int, int], RenameAction] = {}  # token 位置到重命名动作的索引

    # 每个 AST 动作都要转换成 tokenize 的字符列号。
    for action in actions:

        # 防御异常行号，避免自动改名因损坏动作触发越界。
        if action.lineno < 1 or action.lineno > len(list_lines):

            # 非法动作直接丢弃，避免访问不存在的源码行。
            continue

        # 取出动作所在源码行，用于字节列转字符列。
        str_line = list_lines[action.lineno - 1]  # 当前动作所在源码行

        # 非 ASCII 行需要把 AST 字节列转换为 tokenize 字符列。
        int_text_col = ast_byte_col_to_text_col(str_line, action.col_offset)  # tokenize 字符列号

        # 建立替换阶段直接使用的位置索引。
        dict_actions[(action.lineno, int_text_col)] = action  # 当前 token 位置的替换动作

    # 返回转换后的 token 位置索引。
    return dict_actions

# 替换阶段只相信 AST 行列号和 tokenize 的 NAME token。
def replace_name_tokens(source: str, actions: list[RenameAction]) -> str:
    """仅替换 AST 确认位置的 NAME token，避开字符串、注释和字段文本。

    参数:
        source: 待分析或改写的 Python 源码文本。
        actions: 安全重命名流程生成的 token 替换动作。

    返回:
        完成安全变量名替换后的源码文本。
    """

    # 没有安全动作时保持源码字节级内容不变。
    if not actions:

        # 调用方不需要区分无动作和替换后文本。
        return source

    # 逐个 token 重建源码，避免正则替换误伤字符串和注释。
    list_tokens: list[tokenize.TokenInfo] = []  # 重建源码的 token 序列

    # 行列号索引需要先统一 AST 字节列和 tokenize 字符列。
    dict_action_by_position = action_index_for_source(source, actions)  # 按源码位置回查每个可替换 token 的动作

    # 扫描源码 token，定位注释和可替换名称。
    for token_info_token in tokenize.generate_tokens(io.StringIO(source).readline):

        # 显式保留 token 类型，便于命名门禁识别词法对象变量。
        token_info_current_token: tokenize.TokenInfo = token_info_token  # 当前源码词法 token

        # 只有精确位置命中的 NAME token 才允许被替换。
        rename_action_current_action: RenameAction = dict_action_by_position.get(token_info_current_token.start)  # 当前 token 对应的重命名动作

        # 只有类型、位置和旧名称三者同时匹配时才执行替换。
        if (
            token_info_current_token.type == tokenize.NAME
            and rename_action_current_action is not None
            and token_info_current_token.string == rename_action_current_action.old_name
        ):

            # 保存源码 token，定位注释或可替换标识符。
            token_info_current_token: tokenize.TokenInfo = tokenize.TokenInfo(  # 替换后的词法 token
                token_info_current_token.type,  # 保留原 token 类型
                rename_action_current_action.new_name,  # 仅替换 token 文本为新变量名
                token_info_current_token.start,  # 起始位置沿用原 token
                token_info_current_token.end,  # 结束位置仍保持原范围
                token_info_current_token.line,  # 原始源码行供 untokenize 复用
            )

        # 把原 token 或替换后 token 按顺序写回重建列表。
        list_tokens.append(token_info_current_token)

    # untokenize 保留原有注释和字符串内容。
    return tokenize.untokenize(list_tokens)

# 文件写回只在存在安全建议时发生。
def apply_safe_renames(filepath: str | Path) -> dict[str, str]:
    """对单个 Python 文件执行 token 级安全重命名。

    参数:
        filepath: 需要读取、解析或检查的 Python 文件路径。

    返回:
        实际写入文件的安全重命名映射；没有可写建议时返回空字典。
    """

    # 统一成 Path，后续读取和写回使用同一个文件对象。
    path_path = Path(filepath)  # 待改写的 Python 文件路径

    # 保存原始文本，token 替换需要在这份源码上重建。
    str_source = path_path.read_text(encoding="utf-8")  # 文件原始源码文本

    # AST 提供变量节点行列号，tokenize 负责安全写回。
    module_tree: ast.Module = ast.parse(str_source, filename=str(path_path))  # 文件源码 AST

    # 同时取得写回动作和给调用方展示的建议映射。
    tuple_actions, tuple_suggestions = scoped_rename_suggestions(module_tree)  # token 替换动作和报告建议

    # 没有可写建议时不能修改目标文件。
    if not tuple_suggestions:

        # 空映射让调用方知道本次没有落盘修改。
        return {}

    # 把生成的报告写入调用方指定文件。
    path_path.write_text(replace_name_tokens(str_source, tuple_actions), encoding="utf-8")

    # 返回值只包含本次实际允许应用的建议。
    return tuple_suggestions
