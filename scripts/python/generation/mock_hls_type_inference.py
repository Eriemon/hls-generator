"""收拢 mock HLS typed-prefix 所需的声明解析与 family 推断逻辑。"""

# 启用延迟注解，避免类型提示在导入阶段过早求值。
from __future__ import annotations

# 正则负责识别参数尾部标识符、局部声明变量名和整数关键字。
import re

# 保存允许保留原名的短循环变量，避免把经典短索引机械拉长。
EXEMPT_NAMES = {"i", "j", "k", "m", "n", "r", "c", "ii", "idx", "len"}  # HLS 生成器允许保留的短索引名集合

# 保存 typed-prefix 规则允许识别的主前缀，供旧前缀剥离与一致性判断复用。
KNOWN_PREFIXES = tuple(  # 当前 workflow 允许的 HLS typed-prefix 集合
    "bool_ int_ uint_ float_ double_ fixed_ ufixed_ ptr_ arr_ stream_ axis_".split()  # 需要展开成 tuple 的前缀文本
)

# AXIS 协议字段必须保留 data/keep/strb/last 原名，不能被 typed-prefix 重写。
AXIS_PROTOCOL_FIELDS = ("data", "keep", "strb", "last")  # AXIS 协议字段原名集合

# 参数尾部变量名提取统一使用的正则模式。
OBJ_PARAMETER_IDENTIFIER_PATTERN = re.compile(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?$")  # 参数尾部标识符的匹配模式

# 局部声明变量名提取统一使用的正则模式。
OBJ_DECLARATION_IDENTIFIER_PATTERN = re.compile(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:=[^;]+)?;\s*$")  # 局部声明变量名的匹配模式

# AXIS typedef 名称识别统一使用的正则模式。
OBJ_AXIS_TYPE_PATTERN = re.compile(r"\baxis_[a-z0-9_]*_t\b")  # AXIS 载荷 typedef 名称的匹配模式

# 无符号关键字识别统一使用的正则模式。
OBJ_UNSIGNED_WORD_PATTERN = re.compile(  # 无符号关键字的匹配模式
    r"(^|[^0-9a-z_])unsigned([^0-9a-z_]|$)"  # 兼容单词边界的 unsigned 检测文本
)

# 常见整数关键字识别统一使用的正则模式。
OBJ_INTEGRAL_WORD_PATTERN = re.compile(r"\b(?:char|short|int|long)\b")  # 常见整数关键字的匹配模式

# 轻量声明类型词识别统一使用的正则模式。
OBJ_TYPED_DECLARATION_PATTERN = re.compile(  # 轻量声明类型词的匹配模式
    r"\b(hls::stream|ap_uint|ap_int|ap_fixed|ap_ufixed|bool|double|float|"
    r"size_t|char|short|int|long)\b"
)

# 在不打断模板参数深度的前提下拆分函数签名参数文本。
def split_parameter_texts(signature: str) -> list[str]:
    """拆分函数签名里的参数文本，并兼容模板参数中的逗号。

    参数:
        signature: 当前函数的完整签名文本，shape=scalar，dtype=str，unit=signature text。

    返回:
        逐个参数的原始文本列表，shape=(n items)，dtype=list[str]，unit=parameter text list。
    """

    # 先读取参数列表的左右括号边界，没有边界时直接返回空列表。
    tuple_parameter_bounds = parameter_bounds(signature)  # 当前签名里参数列表左右括号的位置二元组

    # 缺少完整括号边界时，表示当前签名没有可拆分的参数段。
    if not tuple_parameter_bounds:

        # 直接返回空列表，避免后续扫描把非参数文本误当成参数区。
        return []

    # 再取出括号内部的参数段文本。
    str_parameters_text = signature[tuple_parameter_bounds[0] + 1 : tuple_parameter_bounds[1]].strip()  # 当前函数签名的参数段文本

    # 空参数或显式 `void` 参数都视为没有业务参数。
    if not str_parameters_text or str_parameters_text == "void":

        # 直接返回空列表，表示当前函数没有业务参数。
        return []

    # 把参数段按模板深度安全拆成单个参数文本。
    return collected_parameter_texts(str_parameters_text)

# 读取函数签名中参数列表左右括号的安全边界。
def parameter_bounds(signature: str) -> tuple[int, int] | None:
    """读取函数签名中参数列表左右括号的安全边界。

    参数:
        signature: 当前函数的完整签名文本，shape=scalar，dtype=str，unit=signature text。

    返回:
        命中时返回左右括号位置二元组；缺失时返回空值，shape=(2 items) 或 无，dtype=tuple[int, int] | None，unit=parenthesis bounds。
    """

    # 先定位参数列表左括号。
    int_left_parenthesis = signature.find("(")  # 参数列表左括号的位置

    # 再定位参数列表右括号。
    int_right_parenthesis = signature.rfind(")")  # 参数列表右括号的位置

    # 左右括号不完整时，不再继续构造参数边界。
    if int_left_parenthesis < 0 or int_right_parenthesis <= int_left_parenthesis:

        # 返回空值，表示当前签名不存在可安全拆分的参数段。
        return None

    # 返回已经验证过的左右括号边界二元组。
    return (int_left_parenthesis, int_right_parenthesis)

# 把参数段按模板深度与函数指针深度安全拆成单个参数文本。
def collected_parameter_texts(parameters_text: str) -> list[str]:
    """把参数段安全拆成单个参数文本。

    参数:
        parameters_text: 括号内部的参数段文本，shape=scalar，dtype=str，unit=parameter segment text。

    返回:
        按源码顺序拆分完成的参数文本列表，shape=(n items)，dtype=list[str]，unit=parameter text list。
    """

    # 初始化参数列表和当前缓冲，后续边扫描边收拢。
    list_parameters: list[str] = []  # 当前参数段拆出的参数文本列表

    # 当前尚未落盘的参数字符缓冲。
    list_buffer: list[str] = []  # 当前参数字符缓冲列表

    # 模板参数深度从零开始累计。
    int_angle_depth = 0  # 当前扫描位置的模板参数深度

    # 函数指针一类参数的小括号深度同样从零开始累计。
    int_parenthesis_depth = 0  # 当前扫描位置的小括号深度

    # 逐个字符扫描参数段，避免模板参数内部逗号被误切开。
    for str_character in parameters_text:

        # 先收拢当前字符更新后的两类深度，避免后续索引读取到旧值。
        tuple_updated_depths = updated_parameter_depths(  # 当前字符更新后的深度二元组
            str_character,  # 当前参与深度更新的字符
            int_angle_depth,  # 更新前的模板参数深度
            int_parenthesis_depth,  # 更新前的小括号深度
        )

        # 模板深度决定当前逗号是否仍处于模板参数内部。
        int_angle_depth = tuple_updated_depths[0]  # 当前字符更新后的模板参数深度

        # 小括号深度负责区分函数指针参数层。
        int_parenthesis_depth = tuple_updated_depths[1]  # 当前字符更新后的小括号深度

        # 只有所有深度都归零时，当前逗号才真正代表参数边界。
        if is_parameter_separator(str_character, int_angle_depth, int_parenthesis_depth):

            # 把当前缓冲收拢成一个完整参数文本并写入结果列表。
            append_buffered_parameter(list_parameters, list_buffer)

            # 当前参数已经结束，本轮直接进入下一个字符。
            continue

        # 普通字符继续写入当前缓冲。
        list_buffer.append(str_character)

    # 扫描结束后把最后一个参数缓冲收拢成尾项。
    append_buffered_parameter(list_parameters, list_buffer)

    # 返回按源码顺序拆好的参数文本列表。
    return list_parameters

# 根据当前字符更新模板深度和小括号深度。
def updated_parameter_depths(str_character: str, int_angle_depth: int, int_parenthesis_depth: int) -> tuple[int, int]:
    """根据当前字符更新参数扫描深度。

    参数:
        str_character: 当前扫描到的字符，shape=scalar，dtype=str，unit=character。
        int_angle_depth: 当前字符处理前的模板参数深度，shape=scalar，dtype=int，unit=depth。
        int_parenthesis_depth: 当前字符处理前的小括号深度，shape=scalar，dtype=int，unit=depth。

    返回:
        更新后的模板深度和小括号深度二元组，shape=(2 items)，dtype=tuple[int, int]，unit=depths。
    """

    # 小于号进入模板深度，后续逗号不能再作为参数分隔符。
    if str_character == "<":

        # 返回模板深度加一后的状态，同时保留当前小括号深度。
        return (int_angle_depth + 1, int_parenthesis_depth)

    # 大于号离开模板深度，但只在深度大于零时回退。
    if str_character == ">" and int_angle_depth > 0:

        # 返回模板深度减一后的状态，避免模板逗号继续屏蔽参数分隔。
        return (int_angle_depth - 1, int_parenthesis_depth)

    # 函数指针一类参数需要单独跟踪小括号深度。
    if str_character == "(":

        # 返回小括号深度加一后的状态，表示进入函数指针参数层。
        return (int_angle_depth, int_parenthesis_depth + 1)

    # 右括号只在深度大于零时才真正回退一层。
    if str_character == ")" and int_parenthesis_depth > 0:

        # 返回小括号深度减一后的状态，表示退出函数指针参数层。
        return (int_angle_depth, int_parenthesis_depth - 1)

    # 其余字符不会改变任何嵌套深度。
    return (int_angle_depth, int_parenthesis_depth)

# 只在所有嵌套深度归零时把逗号识别成参数分隔符。
def is_parameter_separator(str_character: str, int_angle_depth: int, int_parenthesis_depth: int) -> bool:
    """判断当前字符是否构成参数分隔符。

    参数:
        str_character: 当前扫描到的字符，shape=scalar，dtype=str，unit=character。
        int_angle_depth: 当前模板参数深度，shape=scalar，dtype=int，unit=depth。
        int_parenthesis_depth: 当前小括号深度，shape=scalar，dtype=int，unit=depth。

    返回:
        命中真正的参数边界时返回 True，否则返回 False，shape=scalar，dtype=bool，unit=flag。
    """

    # 所有深度都归零时的逗号才表示一个真实参数边界。
    return str_character == "," and int_angle_depth == 0 and int_parenthesis_depth == 0

# 把当前字符缓冲收拢成参数文本并在非空时写入结果列表。
def append_buffered_parameter(list_parameters: list[str], list_buffer: list[str]) -> None:
    """把当前字符缓冲收拢成参数文本并写入结果列表。

    参数:
        list_parameters: 已收集的参数文本列表，shape=(n items)，dtype=list[str]，unit=parameter text list。
        list_buffer: 当前尚未落盘的参数字符缓冲，shape=(n chars)，dtype=list[str]，unit=character buffer。

    返回:
        无返回；直接原地更新 `list_parameters` 与 `list_buffer`，shape=scalar，dtype=None，unit=not applicable。
    """

    # 先把当前字符缓冲拼成完整参数文本。
    str_parameter_text = "".join(list_buffer).strip()  # 当前缓冲收拢后的参数文本

    # 非空参数片段才写入结果列表。
    if str_parameter_text:

        # 只登记真正存在的参数文本，避免空逗号生成伪参数。
        list_parameters.append(str_parameter_text)

    # 当前参数已经写出后，清空缓冲等待下一个参数。
    list_buffer.clear()

# 从单个参数文本中提取参数名，供 helper 参数改名与 contract 生成共享。
def identifier_from_parameter_text(parameter_text: str) -> str:
    """从参数声明文本中提取参数名。

    参数:
        parameter_text: 单个参数的声明文本，shape=scalar，dtype=str，unit=parameter text。

    返回:
        参数名文本；无法识别时返回空字符串，shape=scalar，dtype=str，unit=identifier text。
    """

    # 先把指针和引用标记替换为空格，便于统一复用尾部标识符正则。
    str_normalized_text = parameter_text.replace("*", " ").replace("&", " ")  # 当前参数文本去掉指针和引用标记后的形式

    # 再从参数尾部抽取可识别的标识符候选列表。
    list_matches = OBJ_PARAMETER_IDENTIFIER_PATTERN.findall(str_normalized_text)  # 当前参数文本末尾可识别的标识符候选列表

    # 命中候选时返回最后一个识别结果，否则返回空字符串。
    return list_matches[-1] if list_matches else ""

# 从局部声明文本中提取变量名，供 typed-prefix 局部变量改名复用。
def identifier_from_declaration_text(declaration_text: str) -> str:
    """从局部声明文本中提取变量名。

    参数:
        declaration_text: 单条局部声明文本，shape=scalar，dtype=str，unit=declaration text。

    返回:
        识别出的变量名；无法识别时返回空字符串，shape=scalar，dtype=str，unit=identifier text。
    """

    # 先尝试从标准局部声明格式中抓取变量名。
    obj_match = OBJ_DECLARATION_IDENTIFIER_PATTERN.search(declaration_text)  # 局部声明文本中的变量名匹配结果

    # 命中时返回变量名，否则返回空字符串。
    return obj_match.group(1) if obj_match else ""

# 为 `.read()` 初始化等轻量声明形态补一层守旧识别。
def looks_like_typed_declaration(declaration_text: str) -> bool:
    """识别当前代码行是否像一条带类型信息的局部声明。

    参数:
        declaration_text: 待判断的单行代码文本，shape=scalar，dtype=str，unit=source line。

    返回:
        能证明包含常见 HLS/C++ 类型声明时返回 True，否则返回 False，shape=scalar，dtype=bool，unit=flag。
    """

    # 不是分号结尾的语句不视为局部声明候选。
    if not declaration_text.endswith(";"):

        # 直接返回 False，避免把普通表达式误认成声明。
        return False

    # 控制流和 pragma 行不参与轻量声明识别。
    if startswith_excluded_statement(declaration_text):

        # 直接返回 False，避免把控制流或 pragma 误认成声明。
        return False

    # 只有能抽取出变量名时，后续的类型词判断才有意义。
    if not identifier_from_declaration_text(declaration_text):

        # 直接返回 False，表示当前文本不具备声明结构。
        return False

    # 再读取等号左侧的类型与变量名组合文本。
    str_left_side = declaration_text.split("=", 1)[0].strip()  # 当前候选声明的左值文本

    # 命中常见 HLS/C++ 类型词时视为轻量声明。
    return contains_typed_declaration_keyword(str_left_side)

# 判断当前代码行是否以控制流或 pragma 关键字起始。
def startswith_excluded_statement(declaration_text: str) -> bool:
    """判断当前代码行是否以排除语句起始。

    参数:
        declaration_text: 待判断的单行代码文本，shape=scalar，dtype=str，unit=source line。

    返回:
        命中控制流或 pragma 起始词时返回 True，否则返回 False，shape=scalar，dtype=bool，unit=flag。
    """

    # 统一列出不应落入轻量声明识别的语句起始词。
    tuple_excluded_prefixes = ("return ", "if ", "if(", "for ", "for(", "while ", "while(", "#pragma")  # 轻量声明识别需要排除的语句起始词

    # 命中排除语句起始词时返回 True。
    return declaration_text.startswith(tuple_excluded_prefixes)

# 判断当前文本是否包含足以证明类型声明存在的关键词。
def contains_typed_declaration_keyword(left_side_text: str) -> bool:
    """判断当前文本是否包含类型声明关键词。

    参数:
        left_side_text: 候选声明的左值文本，shape=scalar，dtype=str，unit=declaration left side。

    返回:
        命中支持的类型关键词时返回 True，否则返回 False，shape=scalar，dtype=bool，unit=flag。
    """

    # 命中常见 HLS/C++ 类型词时，说明当前文本足以视为轻量声明。
    return bool(OBJ_TYPED_DECLARATION_PATTERN.search(left_side_text))

# 从参数或局部声明文本中保守推断 typed-prefix 家族。
def family_from_declaration_text(declaration_text: str) -> str:
    """从声明文本中推断 typed-prefix family。

    参数:
        declaration_text: 参数声明或局部声明文本，shape=scalar，dtype=str，unit=declaration text。

    返回:
        推断出的 family 名称；无法证明时返回空字符串，shape=scalar，dtype=str，unit=family name。
    """

    # 先提取真正承载类型与左值形态的声明头，避免初始化表达式污染 family 推断。
    str_declaration_head = declaration_head_text(declaration_text)  # 当前声明文本用于 family 推断的声明头

    # 再对声明头做小写归一化，便于按关键字识别 family。
    str_lower_text = str_declaration_head.casefold()  # 当前声明头的小写归一化结果

    # 先尝试匹配 stream 或 AXIS 一类通道家族。
    str_channel_family = stream_or_axis_family(str_lower_text)  # 当前声明文本命中的通道类 family

    # 命中通道家族时直接返回，避免被后续形态规则覆盖。
    if str_channel_family:

        # 返回优先级最高的通道家族推断结果。
        return str_channel_family

    # 再尝试匹配数组或指针这类存储形态家族。
    str_storage_family = storage_shape_family(str_declaration_head)  # 当前声明头命中的存储形态 family

    # 命中存储形态家族时直接返回。
    if str_storage_family:

        # 返回存储形态对应的 family 推断结果。
        return str_storage_family

    # 最后再按数值类型关键字判断标量 family。
    str_scalar_family = numeric_scalar_family(str_lower_text)  # 当前声明文本命中的标量数值 family

    # 命中数值 family 时直接返回。
    if str_scalar_family:

        # 返回标量数值类型的 family 推断结果。
        return str_scalar_family

    # 无法可靠证明类型时返回空字符串，让上游保留原名。
    return ""

# 提取声明中真正承载类型与左值形态的头部文本，避免初始化表达式误导 family 推断。
def declaration_head_text(declaration_text: str) -> str:
    """提取声明头文本。

    参数:
        declaration_text: 参数声明或局部声明文本，shape=scalar，dtype=str，unit=declaration text。

    返回:
        只包含类型与左值形态的声明头文本，shape=scalar，dtype=str，unit=declaration head text。
    """

    # 初始化表达式一律从首个等号处分离，保证 RHS 的索引与乘法不会污染左值形态判断。
    return declaration_text.split("=", 1)[0].strip()

# 优先识别 stream 与 AXIS 一类通道家族。
def stream_or_axis_family(lower_text: str) -> str:
    """优先识别 stream 与 AXIS 一类通道家族。

    参数:
        lower_text: 已做小写归一化的声明文本，shape=scalar，dtype=str，unit=normalized declaration text。

    返回:
        命中时返回 `stream` 或 `axis`，否则返回空字符串，shape=scalar，dtype=str，unit=family name。
    """

    # stream 类型优先级最高，避免被后续形态规则误判成其他 family。
    if "hls::stream<" in lower_text:

        # 返回 stream family，表示当前声明承载 HLS 流通道。
        return "stream"

    # AXIS packet 类型必须保留独立 family。
    if "ap_axiu<" in lower_text or OBJ_AXIS_TYPE_PATTERN.search(lower_text):

        # 返回 axis family，表示当前声明承载 AXI-Stream 载荷。
        return "axis"

    # 没有命中通道类家族时返回空字符串。
    return ""

# 识别数组和指针这类以存储形态为主的 family。
def storage_shape_family(declaration_text: str) -> str:
    """识别数组和指针这类存储形态 family。

    参数:
        declaration_text: 原始声明文本，shape=scalar，dtype=str，unit=declaration text。

    返回:
        命中时返回 `arr` 或 `ptr`，否则返回空字符串，shape=scalar，dtype=str，unit=family name。
    """

    # 数组声明显式带方括号时归入 arr family。
    if "[" in declaration_text and "]" in declaration_text:

        # 返回 arr family，表示当前声明承载数组形态。
        return "arr"

    # 指针声明显式带星号时归入 ptr family。
    if "*" in declaration_text:

        # 返回 ptr family，表示当前声明承载指针形态。
        return "ptr"

    # 没有命中存储形态 family 时返回空字符串。
    return ""

# 识别定点、浮点、布尔和整数一类标量数值 family。
def numeric_scalar_family(lower_text: str) -> str:
    """识别定点、浮点、布尔和整数一类标量数值 family。

    参数:
        lower_text: 已做小写归一化的声明文本，shape=scalar，dtype=str，unit=normalized declaration text。

    返回:
        命中时返回标量数值 family，否则返回空字符串，shape=scalar，dtype=str，unit=family name。
    """

    # HLS 无符号定点类型先判 ufixed，避免被 fixed 关键字吞掉。
    if "ap_ufixed<" in lower_text:

        # 返回 ufixed family，表示当前声明使用无符号定点类型。
        return "ufixed"

    # 有符号定点类型归入 fixed family。
    if "ap_fixed<" in lower_text:

        # `ap_fixed` 说明这是有符号定点值，命名需要保留 fixed 语义。
        return "fixed"

    # 双精度和单精度浮点按原生 family 区分。
    if "double" in lower_text:

        # 返回 double family，表示当前声明使用双精度浮点。
        return "double"

    # 单精度浮点归入 float family。
    if "float" in lower_text:

        # 命中 `float` 后保持单精度语义，避免和 double 家族混淆。
        return "float"

    # 布尔类型统一归入 bool family。
    if "bool" in lower_text:

        # 返回 bool family，表示当前声明使用布尔类型。
        return "bool"

    # 无符号整型和 ap_uint 统一归入 uint family。
    if "ap_uint<" in lower_text or OBJ_UNSIGNED_WORD_PATTERN.search(lower_text):

        # 返回 uint family，表示当前声明使用无符号整数。
        return "uint"

    # 其余整数类型和 size_t 统一归入 int family。
    if "ap_int<" in lower_text or "size_t" in lower_text or OBJ_INTEGRAL_WORD_PATTERN.search(lower_text):

        # 走到这里代表声明仍属于整数系，只是没有无符号语义证据。
        return "int"

    # 没有命中任何标量数值 family 时返回空字符串。
    return ""

# 为单个标识符生成符合 HG025/HG026 语义的 typed-prefix 名称。
def typed_name_for_identifier(name: str, family: str) -> str:
    """为单个标识符生成 typed-prefix 治理名。

    参数:
        name: 原始标识符名称，shape=scalar，dtype=str，unit=identifier name。
        family: 已推断的 typed-prefix family，shape=scalar，dtype=str，unit=family name。

    返回:
        治理后的 typed-prefix 名称；无法安全改写时保留原名，shape=scalar，dtype=str，unit=identifier name。
    """

    # 没有有效名字、缺少 family 或命中短索引豁免时，统一保留原名。
    if not name or name in EXEMPT_NAMES or not family:

        # 直接返回原名，避免对缺乏证据或经典短索引做机械改写。
        return name

    # 先准备当前 family 对应的期望主前缀。
    str_expected_prefix = f"{family}_"  # 当前 family 对应的 typed-prefix 前缀文本

    # 已经满足当前 family 前缀的名字不再重复改写。
    if name.startswith(str_expected_prefix):

        # 直接返回原名，避免生成重复前缀。
        return name

    # 再把旧的 ref_ 或旧 typed-prefix 规整成可安全拼接的新语义基名。
    str_base_name = normalized_base_identifier(name)  # 当前标识符去掉旧前缀后的语义基名

    # 指针和数组的通用 input/output/expected 名称需要补齐存储语义词。
    if family in {"ptr", "arr"} and str_base_name in {"input", "output", "expected"}:

        # 返回带 `_values` 的存储形态名称，避免再次命中空泛命名规则。
        return f"{family}_{str_base_name}_values"

    # scale 需要补齐 factor 语义，避免过短命名再次命中空泛命名规则。
    if str_base_name == "scale":

        # 返回补齐 `factor` 语义后的 typed-prefix 名称。
        return f"{family}_scale_factor"

    # alias_ 只能作为次级语义词，必须挂在类型主前缀之后。
    if str_base_name.startswith("alias_"):

        # 返回带主前缀的 alias_ 名称，显式保留别名语义。
        return f"{str_expected_prefix}{str_base_name}"

    # 默认把 family 主前缀接到当前语义名之前。
    return f"{str_expected_prefix}{str_base_name}"

# 把旧的 ref_ 或旧 typed-prefix 规整成新的语义基名。
def normalized_base_identifier(name: str) -> str:
    """把旧的 `ref_` 或旧 typed-prefix 规整成新的语义基名。

    参数:
        name: 原始标识符名称，shape=scalar，dtype=str，unit=identifier name。

    返回:
        去掉旧前缀后的语义基名，shape=scalar，dtype=str，unit=base identifier name。
    """

    # 先把原始标识符当作待规整的语义基名起点。
    str_base_name = name  # 当前标识符去主前缀前的语义基名

    # 旧的 ref_ 前缀统一改写成 alias_ 次级语义。
    if str_base_name.startswith("ref_"):

        # 先把旧的 ref_ 语义迁移成 alias_ 次级语义，满足新版命名约束。
        str_base_name = f"alias_{str_base_name.removeprefix('ref_')}"  # 已迁移为 alias_ 语义的基名

    # 已有其他 typed-prefix 时先剥离旧前缀，再接上当前可证明的 family。
    for str_known_prefix in KNOWN_PREFIXES:

        # 命中其他 typed-prefix 时，只保留前缀后的真实语义段。
        if str_base_name.startswith(str_known_prefix):

            # 先剥离旧 typed-prefix，再让调用方挂上当前可证明的主前缀。
            str_base_name = str_base_name.removeprefix(str_known_prefix)  # 去掉旧 typed-prefix 后的语义基名

            # 已经完成旧前缀剥离后立即结束循环。
            break

    # 返回规整完成的语义基名。
    return str_base_name
