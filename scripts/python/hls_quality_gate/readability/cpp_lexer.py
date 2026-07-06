"""提供 HLS 可读性规则共用的轻量 C/C++ 词法辅助能力。"""

# 未来注解语义避免 dataclass 字段前向引用带来运行期耦合。
from __future__ import annotations

# 标准库用于构造注释剥离后的 token 指纹。
import hashlib

# 标准库正则负责轻量识别 C/C++ 语句形态。
import re

# dataclass 保存跨规则复用的源码定位结果。
from dataclasses import dataclass

# Path 用于收集工程中的 HLS 源文件。
from pathlib import Path

# Iterable 标注参数拆分 helper 的只读返回契约。
from typing import Iterable

# collect_hls_files 只把这些后缀当作 HLS C/C++ 输入，避免把脚本、报告和配置文件误扫进词法规则。
HLS_SOURCE_SUFFIXES = {
    ".c",  # 覆盖纯 C kernel 或辅助函数实现文件。
    ".cc",  # 兼容部分 GNU 风格 HLS 工程沿用的 C++ 源文件后缀。
    ".cpp",  # 覆盖最常见的标准 C++ HLS 源文件后缀。
    ".cxx",  # 兼容少量项目沿用的另一类 C++ 源码后缀。
    ".h",  # 覆盖内联 helper、模板与接口声明头文件。
    ".hpp",  # 覆盖常见 C++ 模板与声明头文件后缀。
    ".hh",  # 兼容少量工程使用的双 h 头文件后缀。
}

# AST 回退路径沿用同一组源码后缀。
HLS_AST_SUFFIXES = HLS_SOURCE_SUFFIXES  # AST fallback 可接受的 HLS 文件后缀

# is_assignment 用这条模式识别真正的赋值运算符，并避开比较表达式里的等号。
ASSIGNMENT_OPERATOR_PATTERN = (
    r"(?<![=!<>])=(?!=)|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>="  # 兼容普通赋值、复合赋值和位移赋值写法
)

# is_local_declaration 先用这份前缀白名单兜住 HLS 局部变量里最常见的标量、定宽类型和 stream 声明。
LOCAL_DECLARATION_PREFIXES = (
    "const ",  # 拦住只读局部变量一类最常见的声明起点
    "static ",  # 覆盖带静态存储期的局部工作缓冲或查表变量声明。
    "volatile ",  # 覆盖与寄存器或访存副作用绑定的局部声明。
    "ap_uint<",  # 覆盖常见无符号定宽整数声明。
    "ap_int<",  # 覆盖常见有符号定宽整数声明。
    "ap_fixed<",  # 覆盖定点数局部变量声明。
    "ap_ufixed<",  # 覆盖无符号定点数局部变量声明。
    "hls::stream<",  # 覆盖 HLS stream 局部对象声明。
    "hls::task",  # 覆盖 task 对象或 task 返回类型声明。
    "bool ",  # 覆盖布尔局部变量声明。
    "char ",  # 覆盖字符类型局部变量声明。
    "short ",  # 覆盖 short 系列局部变量声明。
    "int ",  # 覆盖最常见的整型局部变量声明。
    "long ",  # 覆盖 long、long long 一类长整型局部变量声明。
    "unsigned ",  # 覆盖 unsigned 开头的无符号整型局部变量声明。
    "float ",  # 覆盖单精度浮点局部变量声明。
    "double ",  # 覆盖双精度浮点局部变量声明。
    "size_t ",  # 覆盖长度和索引类局部变量声明。
    "auto ",  # 覆盖依赖类型推断的局部变量声明。
)

# is_function_signature 遇到这些控制流起始词时会直接退出，避免把语句误认成函数签名。
CONTROL_STATEMENT_PREFIXES = (
    "if",  # 先挡住条件分支，避免签名规则把它误判成函数头
    "for",  # 提前拦住循环头，避免把 `for (...)` 当成候选函数签名。
    "while",  # 挡住 while 循环，避免被轻量签名规则误收。
    "switch",  # 挡住 switch 分支，避免误判为函数签名。
    "return",  # 挡住返回语句，避免误判为签名文本。
    "catch",  # 挡住异常处理分支头。
    "try",  # 挡住 try 块起始，避免落入签名规则。
    "assert",  # 挡住断言调用式语句，避免误判。
)

# is_local_declaration 命中这些前缀时会优先按控制流或跳转语句处理，而不是局部声明。
LOCAL_DECLARATION_EXCLUDED_PREFIXES = (
    "return",  # 先排除返回语句，避免声明规则把它当成类型名起始
    "if",  # 先排除条件分支，避免声明识别把分支头误当成类型开头。
    "for",  # 排除循环头，避免把初始化段误看成局部声明。
    "while",  # 排除 while 循环头，避免误判成声明。
    "switch",  # 排除 switch 控制流头。
    "case",  # 排除 case 标签。
    "break",  # 排除 break 跳转语句。
    "continue",  # 排除 continue 语句，避免被声明规则误收为类型前缀。
)
# CommentSpan 记录注释原文、位置和是否为行尾注释。
@dataclass(frozen=True)
class CommentSpan:
    """描述 C/C++ 注释在源码中的位置和归一化文本。

    :param line: 注释起始行号，按一基计数。
    :param end_line: 注释结束行号，单行注释时等于起始行。
    :param text: 去掉注释标记后的归一化注释文本。
    :param raw: 源码中的注释原文片段。
    :param kind: 注释类型，取值为 line 或 block。
    :param inline: 注释前是否存在同一行代码。
    :param code_before: 行尾注释前面的源码片段。
    :return: dataclass 实例仅承载注释扫描结果。
    """

    # 注释起始行用于报告源码位置。
    line: int  # 一基注释起始行号

    # 注释结束行保留块注释跨度。
    end_line: int  # 一基注释结束行号

    # 归一化文本供注释质量规则复用。
    text: str  # 去除注释标记后的文本

    # 原始注释片段供报告和 token 守卫复核。
    raw: str  # 源码原始注释文本

    # 注释类型区分行注释与块注释。
    kind: str  # 注释类别字符串，区分 line/block

    # inline 标记帮助判断注释是否位于代码右侧。
    inline: bool  # 注释前是否已有有效代码

    # code_before 供行尾注释规则检查同一行代码语义。
    code_before: str  # 注释前的源码片段
# FunctionInfo 记录轻量函数边界，供 AST fallback 和规则检查复用。
@dataclass(frozen=True)
class FunctionInfo:
    """描述轻量解析得到的 C/C++ 函数或声明。

    :param name: 函数名。
    :param start_line: 函数体起始行；声明时为声明结束行。
    :param end_line: 函数体结束行；声明时为声明结束行。
    :param signature_start_line: 函数签名起始行。
    :param signature: 合并后的函数签名文本。
    :param params: 解析得到的参数名元组。
    :param return_type: 签名前缀中的返回类型文本。
    :param is_declaration: 是否为无函数体的声明。
    :return: dataclass 实例仅承载函数边界信息。
    """

    # name 保留调用方现有字段访问契约。
    name: str  # C/C++ 函数标识符

    # start_line 用于规则定位函数体开头。
    start_line: int  # 函数体起始行或声明行

    # end_line 用于规则定位函数体结尾。
    end_line: int  # 函数体结束行或声明行

    # signature_start_line 保留多行签名的真实起点。
    signature_start_line: int  # 函数签名起始行

    # signature 保留合并后的签名上下文。
    signature: str  # 合并后的函数签名

    # params 只保存参数名，便于 naming 规则检查。
    params: tuple[str, ...]  # 参数名元组

    # return_type 保存签名前缀，不做完整 C++ 类型解析。
    return_type: str  # 返回类型文本

    # is_declaration 区分函数声明和函数定义。
    is_declaration: bool = False  # 是否为函数声明
# StatementInfo 记录语句类型和括号深度。
@dataclass(frozen=True)
class StatementInfo:
    """描述一条可读性规则需要检查的 C/C++ 语句。

    :param line: 语句所在行号，按一基计数。
    :param code: 去掉注释后的语句文本。
    :param kind: 语句分类，例如 pragma、assignment 或 function_call。
    :param depth: 语句进入该行之前的花括号深度。
    :return: dataclass 实例仅承载语句扫描结果。
    """

    # line 用于报告具体源码位置。
    line: int  # 一基语句行号

    # code 保存去注释后的语句文本。
    code: str  # 语句代码文本

    # kind 供上层规则选择对应检查策略。
    kind: str  # 语句分类

    # depth 反映语句所在 C/C++ 作用域深度。
    depth: int  # 花括号嵌套深度

# collect_hls_files 为目录级质量门收集所有 HLS 源文件。
def collect_hls_files(root: Path) -> list[Path]:
    """按固定后缀收集目录下的 HLS C/C++ 源文件。

    :param root: 需要递归扫描的目录路径。
    :return: 去重并排序后的 HLS 源文件路径列表。
    """

    # list_files 汇总各后缀 glob 得到的候选文件。
    list_files: list[Path] = []  # HLS 源文件候选集合

    # 按后缀排序保证跨平台报告顺序稳定。
    for str_suffix in sorted(HLS_SOURCE_SUFFIXES):

        # 追加当前后缀匹配到的文件路径。
        list_files.extend(sorted(root.glob(f"**/*{str_suffix}")))

    # 返回去重后的稳定文件列表。
    return sorted(set(list_files))

# strip_comments_preserving_strings 为 token 指纹剥离注释但保留字符串。
def strip_comments_preserving_strings(text: str) -> str:
    """移除 C/C++ 注释，同时保留字符串字面量和换行结构。

    :param text: 待处理的 C/C++ 源码文本。
    :return: 注释替换为空白后的源码文本，字符串内容保持不变。
    """

    # str_clean_code 保存共享扫描器生成的去注释文本。
    str_clean_code = _strip_comment_text(text)  # 注释剥离后的源码文本

    # 返回供 token 指纹和 AST fallback 复用的源码。
    return str_clean_code

# code_token_fingerprint 生成忽略注释和空白差异的源码指纹。
def code_token_fingerprint(text: str) -> str:
    """生成用于检测非注释 token 是否变化的稳定指纹。

    :param text: 待计算指纹的 C/C++ 源码文本。
    :return: 去注释并压缩空白后的 SHA256 十六进制摘要。
    """

    # str_normalized_source 压缩空白以避免格式化差异影响 token 守卫。
    str_normalized_source = re.sub(  # 把多种空白归一成单空格后的源码文本
        r"\s+",  # 需要压缩的连续空白模式
        " ",  # 统一替换成单个空格
        strip_comments_preserving_strings(text),  # 已去掉注释但保留字符串的源码文本
    ).strip()  # 注释剥离并压缩空白后的源码

    # 返回摘要供 HLS AST guard 比较治理前后代码 token。
    return hashlib.sha256(str_normalized_source.encode("utf-8")).hexdigest()

# code_part 截取单行中注释开始前的代码部分。
def code_part(line: str) -> str:
    """返回单行源码中注释之前的代码片段。

    :param line: 待截取的 C/C++ 单行源码。
    :return: 保留字符串字面量后的注释前代码片段。
    """

    # str_code_prefix 只扫描到首个真正注释起点。
    str_code_prefix = _code_before_comment(line)  # 注释前源码片段

    # 返回行级规则需要检查的代码部分。
    return str_code_prefix

# has_inline_comment 判断一行是否包含有效代码后的注释。
def has_inline_comment(line: str) -> bool:
    """判断源码行是否带有行尾注释。

    :param line: 待检查的 C/C++ 单行源码。
    :return: 若注释前存在非空代码则返回 True。
    """

    # str_code_prefix 保存注释开始前的源码部分。
    str_code_prefix = code_part(line)  # 当前行注释起点之前的源码

    # 行尾注释必须同时存在代码和被截断掉的注释文本。
    return len(str_code_prefix) < len(line) and bool(str_code_prefix.strip())

# inline_comment_text 提取行尾注释的归一化文本。
def inline_comment_text(line: str) -> str:
    """提取一行源码中的行尾注释文本。

    :param line: 待提取行尾注释的 C/C++ 单行源码。
    :return: 去掉注释标记后的行尾注释文本；无行尾注释时返回空字符串。
    """

    # str_code_prefix 用于定位注释起始列。
    str_code_prefix = code_part(line)  # 注释前代码片段

    # 没有截断说明当前行没有可提取的注释。
    if len(str_code_prefix) >= len(line):

        # 无注释时保持旧接口返回空字符串。
        return ""

    # 返回去除注释标记后的行尾说明。
    return normalize_comment_text(line[len(str_code_prefix):])

# normalize_comment_text 去除常见 C/C++ 注释边界。
def normalize_comment_text(comment: str) -> str:
    """去掉 C/C++ 注释标记并清理边缘空白。

    :param comment: 原始注释片段，允许包含 //、/* */ 或块注释续行星号。
    :return: 归一化后的注释正文。
    """

    # str_stripped_comment 统一清理首尾空白后再判断注释类型。
    str_stripped_comment = comment.strip()  # 去除边缘空白的注释文本

    # 处理 C++ 单行注释前缀。
    if str_stripped_comment.startswith("//"):

        # 返回双斜杠之后的说明正文。
        return str_stripped_comment[2:].strip()

    # 处理 C 风格块注释边界。
    if str_stripped_comment.startswith("/*"):

        # 去掉块注释边界和常见星号填充。
        return str_stripped_comment.removeprefix("/*").removesuffix("*/").strip(" *")

    # 处理块注释内部的星号续行。
    if str_stripped_comment.startswith("*"):

        # 去掉续行星号后返回正文。
        return str_stripped_comment.lstrip("*").strip()

    # 返回已经没有显式注释边界的文本。
    return str_stripped_comment.strip()

# contains_cjk 判断注释是否包含中文语义文本。
def contains_cjk(text: str) -> bool:
    """检查文本中是否包含中文字符。

    :param text: 待检查的任意文本。
    :return: 存在 CJK 统一表意文字时返回 True。
    """

    # bool_has_chinese 只用于 current-project 注释门禁。
    bool_has_chinese = bool(re.search(r"[\u4e00-\u9fff]", text or ""))  # 是否包含中文字符

    # 返回供注释规则判断的布尔结果。
    return bool_has_chinese

# extract_comments 扫描源码中的行注释和块注释。
def extract_comments(text: str) -> list[CommentSpan]:
    """扫描 C/C++ 源码中的注释片段。

    :param text: 待扫描的 C/C++ 源码文本。
    :return: 按源码顺序排列的注释跨度列表。
    """

    # list_comments 保存跨行扫描过程中收集到的注释跨度。
    list_comments: list[CommentSpan] = []  # 注释跨度列表

    # obj_block_state 保存尚未闭合的块注释状态。
    block_comment_state: BlockCommentState | None = None  # 当前跨行块注释状态

    # list_lines 保留 splitlines 的行序，便于一基行号报告。
    list_lines = text.splitlines()  # 源码行列表

    # 逐行扫描，字符串中的注释标记不会被当作注释。
    for int_line_number, str_line in enumerate(list_lines, start=1):

        # 未闭合块注释优先消费当前行。
        if block_comment_state is not None:

            # block_comment_state 在块注释闭合时会被重置。
            tuple_block_line_inputs = (list_comments, block_comment_state, int_line_number, str_line)  # 跨行块注释续扫输入

            # 用上一轮状态和当前行信息继续推进跨行块注释扫描。
            block_comment_state = _consume_block_comment_line(*tuple_block_line_inputs)  # 块注释续扫状态

            # 块注释行不再进入普通代码扫描。
            continue

        # comment_line_scan_comment_line_scan 保存当前行发现的注释或新的块注释状态。
        comment_line_scan_comment_line_scan = _scan_comment_start_in_line(int_line_number, str_line)  # 单行注释扫描结果

        # 已闭合或单行注释直接进入输出列表。
        if comment_line_scan_comment_line_scan.comment_span is not None:

            # 记录当前行发现的注释。
            list_comments.append(comment_line_scan_comment_line_scan.comment_span)

        # 未闭合块注释需要带入下一行继续扫描。
        if comment_line_scan_comment_line_scan.block_state is not None:

            # 保存跨行块注释的起点、原文和代码前缀。
            block_comment_state = comment_line_scan_comment_line_scan.block_state  # 未闭合块注释状态

    # 返回按源码顺序扫描得到的注释列表。
    return list_comments

# is_comment_only 判断源码行是否只承载注释。
def is_comment_only(line: str) -> bool:
    """判断一行 C/C++ 源码是否以注释标记开头。

    :param line: 待检查的单行源码。
    :return: 行首非空字符为 //、/* 或 * 时返回 True。
    """

    # str_stripped_line 去掉缩进后检查注释前缀。
    str_stripped_line = line.strip()  # 去缩进后的源码行

    # 返回是否为注释行。
    return str_stripped_line.startswith(("//", "/*", "*"))

# is_trivial_code 过滤只有括号或分号的语法结构行。
def is_trivial_code(code: str) -> bool:
    """判断代码片段是否只是结构性括号或空语句。

    :param code: 去掉注释后的单行代码片段。
    :return: 空片段、独立括号或简单 else 结构行返回 True。
    """

    # str_stripped_code 去掉空白后用于语法壳判断。
    str_stripped_code = code.strip()  # 去空白后的代码片段

    # 空代码对语义规则没有检查价值。
    if not str_stripped_code:

        # 空字符串视为平凡代码。
        return True

    # 独立括号和分号只表达 C/C++ 结构边界。
    if str_stripped_code in {"{", "}", "};", ");", ";"}:

        # 结构边界行不作为语义语句检查。
        return True

    # 右括号后接 else 的行同样只表示结构衔接。
    if re.fullmatch(r"}+\s*(?:else\s*\{)?", str_stripped_code):

        # 结构衔接行不作为语义语句检查。
        return True

    # 其他代码片段交给上层规则继续分类。
    return False

# previous_meaningful_code_index 查找当前行前最近的语义代码行。
def previous_meaningful_code_index(lines: list[str], start: int) -> int | None:
    """向上查找最近一行非注释、非结构壳的代码。

    :param lines: 源码行列表。
    :param start: 当前行的零基索引。
    :return: 最近语义代码行的零基索引；找不到时返回 None。
    """

    # int_index 从当前行上一行开始反向扫描。
    int_index = start - 1  # 反向扫描索引

    # 向上跳过空行、注释行和结构壳行。
    while int_index >= 0:

        # 提取候选行的有效代码，供反向搜索判断是否为最近语义语句。
        str_code_part = code_part(lines[int_index]).strip()  # 向上搜索时当前候选行的代码

        # 找到靠近目标行的上游代码后立即停止反向搜索。
        if _is_meaningful_code_line(lines[int_index], str_code_part):

            # 上游代码位置用于判断注释与被保护语句的距离。
            return int_index

        # 移动到更早的源码行。
        int_index -= 1  # 反向扫描移动到更早源码行

    # 反向搜索失败时明确返回没有找到上游语义代码。
    return None

# next_meaningful_code_index 查找当前行后最近的语义代码行。
def next_meaningful_code_index(lines: list[str], start: int) -> int | None:
    """向下查找最近一行非注释、非结构壳的代码。

    :param lines: 源码行列表。
    :param start: 起始零基索引。
    :return: 最近语义代码行的零基索引；找不到时返回 None。
    """

    # int_index 从调用方指定的起点向下扫描。
    int_index = start  # 正向扫描索引

    # 向下跳过空行、注释行和结构壳行。
    while int_index < len(lines):

        # str_code_part 保存当前候选行的注释前代码。
        str_code_part = code_part(lines[int_index]).strip()  # 当前候选行代码

        # 找到靠近目标行的下游代码后立即停止正向搜索。
        if _is_meaningful_code_line(lines[int_index], str_code_part):

            # 下游代码位置用于判断注释与被保护语句的距离。
            return int_index

        # 移动到下一行源码。
        int_index += 1  # 正向扫描移动到下一源码行

    # 没有找到可用语义行。
    return None

# immediate_preceding_comment 返回紧邻当前行的上一行注释。
def immediate_preceding_comment(lines: list[str], index: int) -> str | None:
    """读取目标行上一行的注释文本。

    :param lines: 源码行列表。
    :param index: 目标行的零基索引。
    :return: 上一行是注释时返回归一化文本，否则返回 None。
    """

    # int_previous_index 指向目标行的上一行。
    int_previous_index = index - 1  # 上一行索引

    # 越界或上一行不是注释时没有可用注释。
    if int_previous_index < 0 or not is_comment_only(lines[int_previous_index]):

        # 调用方用 None 区分缺少紧邻注释。
        return None

    # 返回归一化后的上一行注释文本。
    return normalize_comment_text(lines[int_previous_index])

# has_blank_plus_chinese_comment_above 检查空行加中文注释契约。
def has_blank_plus_chinese_comment_above(lines: list[str], index: int) -> bool:
    """判断目标行上方是否满足空行加中文注释的结构。

    :param lines: 源码行列表。
    :param index: 目标行的零基索引。
    :return: 上一行是中文注释且再上一行为边界空行时返回 True。
    """

    # int_previous_index 指向目标行上方的候选注释行。
    int_previous_index = index - 1  # 候选注释行索引

    # 缺少上一行或上一行不是注释则不满足契约。
    if int_previous_index < 0 or not is_comment_only(lines[int_previous_index]):

        # 未命中 current-project 空行加中文注释结构。
        return False

    # str_comment_text 归一化后用于中文字符判断。
    str_comment_text = normalize_comment_text(lines[int_previous_index])  # 候选注释正文

    # 注释必须包含中文语义说明。
    if not contains_cjk(str_comment_text):

        # 非中文注释不能满足 current-project 语义要求。
        return False

    # int_blank_index 指向中文注释上方的空行。
    int_blank_index = int_previous_index - 1  # 注释上方空行索引

    # 文件开头的注释或空行隔离注释都视为满足结构。
    return int_blank_index < 0 or not lines[int_blank_index].strip()

# brace_depths 计算每行进入前的花括号深度。
def brace_depths(lines: list[str]) -> list[int]:
    """计算每一行进入前的 C/C++ 花括号嵌套深度。

    :param lines: 源码行列表。
    :return: 与输入行数等长的深度列表。
    """

    # list_depths 保存每行开始前的作用域深度。
    list_depths: list[int] = []  # 每行进入前的花括号深度

    # int_depth 记录扫描到当前行之前的深度。
    int_depth = 0  # 当前花括号深度

    # 逐行更新深度，注释里的括号不参与统计。
    for str_line in lines:

        # 当前行先记录进入前的深度。
        list_depths.append(int_depth)

        # str_code_part 仅保留注释前代码以避免注释误计数。
        str_code_part = code_part(str_line)  # 当前行注释前代码

        # 根据左右花括号差值更新后续行深度。
        int_depth += str_code_part.count("{") - str_code_part.count("}")  # 更新后续行作用域深度

        # 深度不允许跌破零，避免不完整片段污染后续行。
        int_depth = max(int_depth, 0)  # 修正不完整片段造成的负深度

    # 返回每行进入前的深度序列。
    return list_depths

# is_include 识别 C/C++ include 预处理语句。
def is_include(code: str) -> bool:
    """判断代码片段是否为 include 语句。

    :param code: 去注释后的单行代码片段。
    :return: 以 #include 开头时返回 True。
    """

    # str_stripped_code 去掉缩进后检查预处理前缀。
    str_stripped_code = code.strip()  # 去缩进代码片段

    # 返回 include 语句识别结果。
    return str_stripped_code.startswith("#include")

# is_pragma 识别任意 pragma 预处理语句。
def is_pragma(code: str) -> bool:
    """判断代码片段是否为 pragma 语句。

    :param code: 去注释后的单行代码片段。
    :return: 以 #pragma 开头时返回 True。
    """

    # 统一裁剪空白，供 pragma 前缀判断复用。
    str_stripped_code = code.strip()  # pragma 判定使用的单行代码

    # 命中 #pragma 前缀即可认定为 pragma 语句。
    return str_stripped_code.startswith("#pragma")

# is_hls_pragma 识别 HLS 专用 pragma。
def is_hls_pragma(code: str) -> bool:
    """判断代码片段是否为 #pragma HLS 语句。

    :param code: 去注释后的单行代码片段。
    :return: 命中 #pragma HLS 前缀时返回 True。
    """

    # 只在 #pragma 之后继续限定 HLS 关键字。
    bool_is_hls_pragma = bool(re.match(r"^\s*#pragma\s+HLS\b", code))  # HLS pragma 前缀匹配结果

    # 返回 HLS 专用 pragma 的识别结果。
    return bool_is_hls_pragma

# is_macro 识别 C/C++ 宏定义。
def is_macro(code: str) -> bool:
    """判断代码片段是否为宏定义语句。

    :param code: 去注释后的单行代码片段。
    :return: 以 #define 开头时返回 True。
    """

    # 统一裁剪空白，供宏定义前缀判断复用。
    str_stripped_code = code.strip()  # 宏定义判定使用的单行代码

    # 命中 #define 前缀即可认定为宏定义语句。
    return str_stripped_code.startswith("#define")

# is_type_definition 识别 typedef、using 和类型声明起始行。
def is_type_definition(code: str) -> bool:
    """判断代码片段是否为 C/C++ 类型定义起始语句。

    :param code: 去注释后的单行代码片段。
    :return: typedef、using、struct、class 或 enum 起始时返回 True。
    """

    # 统一裁剪空白，供类型定义起始正则复用。
    str_stripped_code = code.strip()  # 类型定义判定使用的单行代码

    # 命中 typedef、using、struct、class 或 enum 起始即视为类型定义。
    return bool(re.match(r"^\s*(?:typedef\b|using\b|struct\b|class\b|enum\b)", str_stripped_code))

# is_control_statement 识别需要语义注释保护的控制语句。
def is_control_statement(code: str) -> bool:
    """判断代码片段是否为主要控制语句。

    :param code: 去注释后的单行代码片段。
    :return: for、if、while、switch、return、try、catch 或 assert 时返回 True。
    """

    # 统一裁剪空白，供控制流起始词匹配复用。
    str_stripped_code = code.strip()  # 控制流判定使用的单行代码

    # bool_has_control_prefix 覆盖带括号的控制流起始形式。
    # 先识别带条件括号的主控制流语句。
    bool_has_control_prefix = bool(re.match(r"^(?:for|if|while|switch)\s*\(", str_stripped_code))  # 带括号控制流前缀命中结果

    # 其余 return/try/catch/assert 通过字符串前缀补齐判断。
    return bool_has_control_prefix or str_stripped_code.startswith(("return", "try", "catch", "assert"))

# is_loop 识别 for 和 while 循环。
def is_loop(code: str) -> bool:
    """判断代码片段是否为循环语句。

    :param code: 去注释后的单行代码片段。
    :return: for 或 while 起始时返回 True。
    """

    # 先裁掉两端空白，再判断当前语句是否是 for/while 循环头。
    str_stripped_code = code.strip()  # 循环判定使用的单行代码

    # 命中 for 或 while 起始即可认定为循环语句。
    return bool(re.match(r"^\s*(?:for|while)\s*\(", str_stripped_code))

# is_assignment 识别独立赋值语句。
def is_assignment(code: str) -> bool:
    """判断代码片段是否为 C/C++ 赋值语句。

    :param code: 去注释后的单行代码片段。
    :return: 以分号结束且包含赋值运算符时返回 True。
    """

    # 统一裁剪空白，供赋值语句外形判断复用。
    str_stripped_code = code.strip()  # 赋值判定使用的单行代码

    # 非分号语句、预处理语句和函数签名不按赋值处理。
    if (
        not str_stripped_code.endswith(";")
        or str_stripped_code.startswith("#")
        or is_function_signature(str_stripped_code)
    ):

        # 当前片段不满足赋值语句外形。
        return False

    # 循环头部的等号由控制语句规则处理。
    if str_stripped_code.startswith("for"):

        # 循环头部不是普通赋值语句。
        return False

    # 返回赋值运算符识别结果。
    return bool(re.search(ASSIGNMENT_OPERATOR_PATTERN, str_stripped_code))

# is_local_declaration 识别 HLS 函数体内局部声明。
def is_local_declaration(code: str) -> bool:
    """判断代码片段是否为局部变量声明。

    :param code: 去注释后的单行代码片段。
    :return: 命中常见类型前缀或类型名加变量名结构时返回 True。
    """

    # 统一裁剪空白，供局部声明外形判断复用。
    str_stripped_code = code.strip()  # 局部声明判定使用的单行代码

    # 非分号语句、预处理语句、函数签名和类型定义不按局部声明处理。
    if _is_not_local_declaration_shape(str_stripped_code):

        # 当前片段不具备局部声明外形。
        return False

    # 控制流语句不能被误判为声明。
    if str_stripped_code.startswith(LOCAL_DECLARATION_EXCLUDED_PREFIXES):

        # 控制流和跳转语句排除在声明之外。
        return False

    # str_compact_code 统一空白后匹配常见 HLS 类型前缀。
    str_compact_code = re.sub(r"\s+", " ", str_stripped_code)  # 压缩空白后的代码片段

    # 常见类型前缀直接视为局部声明。
    if str_compact_code.startswith(LOCAL_DECLARATION_PREFIXES):

        # HLS 常见类型声明已命中。
        return True

    # 返回类型名加变量名的通用声明形态识别结果。
    return bool(
        re.match(
            r"^(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?(?:\s*<[^;{}]+>)?)"
            r"\s+[A-Za-z_]\w*(?:\[[^\]]*\])?\s*(?:=|\{|;)",
            str_compact_code,
        )
    )

# is_function_call_statement 识别单行函数调用或调用式赋值。
def is_function_call_statement(code: str) -> bool:
    """判断代码片段是否像函数调用语句。

    :param code: 去注释后的单行代码片段。
    :return: 普通函数调用、成员函数调用或调用式赋值时返回 True。
    """

    # 先裁掉两端空白，再判断当前语句是否符合调用式外形。
    str_stripped_code = code.strip()  # 调用语句判定使用的单行代码

    # 没有括号或不是分号语句时不可能是调用语句。
    if not ("(" in str_stripped_code and str_stripped_code.endswith(";")):

        # 当前片段不满足调用语句外形。
        return False

    # 函数签名、声明和控制语句不按调用语句处理。
    if (
        is_function_signature(str_stripped_code)
        or is_local_declaration(str_stripped_code)
        or str_stripped_code.startswith(("for", "if", "while", "switch", "return"))
    ):

        # 当前片段应由其他分类处理。
        return False

    # 先用轻量正则识别普通函数或简单成员函数调用。
    str_direct_call_pattern = r"^(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?|[A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\("  # 普通函数或简单成员函数调用模式

    # 用轻量正则匹配普通函数或简单成员函数调用。
    bool_is_direct_call = bool(re.match(str_direct_call_pattern, str_stripped_code))  # 是否为直接函数调用

    # 返回调用识别结果；赋值中含调用时沿用旧规则视为调用相关语句。
    return bool_is_direct_call or is_assignment(str_stripped_code)

# is_function_signature 识别函数声明或定义签名。
def is_function_signature(code: str) -> bool:
    """判断代码片段是否符合轻量函数签名形态。

    :param code: 去注释后的单行或合并签名文本。
    :return: 命中函数签名正则时返回 True。
    """

    # 先裁掉两端空白，再判断当前文本是否满足函数签名外形。
    str_stripped_code = code.strip()  # 函数签名判定使用的单行代码

    # 没有完整括号时不能构成函数签名。
    if "(" not in str_stripped_code or ")" not in str_stripped_code:

        # 当前片段不具备签名外形。
        return False

    # 控制语句不能被误判为函数签名。
    if str_stripped_code.startswith(CONTROL_STATEMENT_PREFIXES):

        # 当前片段由控制语句规则处理。
        return False

    # 预处理语句和 hls::stream 声明不按函数签名处理。
    if str_stripped_code.startswith(("#", "hls::stream")):

        # 当前片段不是函数签名。
        return False

    # 返回轻量签名正则匹配结果。
    return bool(
        re.match(
            r"^(?:template\s*<[^>]+>\s*)?(?:[\w:<>~,&\*\[\]\s]+)"
            r"\s+[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?\s*\([^;{}]*\)"
            r"\s*(?:const\s*)?(?:;|\{)?$",
            str_stripped_code,
        )
    )

# special_statement_kind 给单行语句分配规则类别。
def special_statement_kind(code: str) -> str | None:
    """返回代码片段对应的特殊语句类别。

    :param code: 去注释后的单行代码片段。
    :return: 识别到的语句类别；无法识别时返回 None。
    """

    # 统一裁剪空白，供单行语句分类器按优先级分派。
    str_stripped_code = code.strip()  # 语句分类器使用的单行代码

    # include 需要优先于宏和普通代码分类。
    if is_include(str_stripped_code):

        # include 命中后直接返回对应类别。
        return "include"

    # HLS pragma 走独立类别，便于上层套用 pragma 专用规则。
    if is_hls_pragma(str_stripped_code):

        # HLS pragma 命中后直接返回对应类别。
        return "pragma"

    # 宏定义沿用单独的注释和可读性策略。
    if is_macro(str_stripped_code):

        # 宏定义命中后直接返回对应类别。
        return "macro"

    # 类型定义不应继续落入普通声明分支。
    if is_type_definition(str_stripped_code):

        # 类型定义命中后直接返回对应类别。
        return "typedef"

    # 函数签名用于函数边界和注释规则定位。
    if is_function_signature(str_stripped_code):

        # 返回函数签名分类。
        return "function_signature"

    # 控制语句按固定前缀分类。
    str_control_kind = _control_statement_kind(str_stripped_code)  # 控制语句分类

    # 命中控制语句时直接返回分类。
    if str_control_kind is not None:

        # 返回控制语句分类。
        return str_control_kind

    # 函数调用和调用式赋值保留为 function_call。
    if is_function_call_statement(str_stripped_code):

        # 返回函数调用分类。
        return "function_call"

    # 无特殊类别时交给调用方继续判断声明或赋值。
    return None

# statement_infos 抽取源码中的语句及其深度。
def statement_infos(lines: list[str]) -> list[StatementInfo]:
    """抽取可读性规则需要检查的单行语句信息。

    :param lines: 源码行列表。
    :return: 按源码顺序排列的语句信息列表。
    """

    # list_depths 保存每一行进入前的花括号深度。
    list_depths = brace_depths(lines)  # 每行花括号深度

    # list_infos 汇总所有非注释、非结构壳语句。
    list_infos: list[StatementInfo] = []  # 语句信息列表

    # 逐行抽取语句分类。
    for int_index, str_line in enumerate(lines):

        # 提取当前行有效代码，供语句分类器和深度记录共用。
        str_code_part = code_part(str_line).strip()  # 语句扫描阶段当前行的有效代码

        # 跳过没有语义检查价值的行。
        if not _is_meaningful_code_line(str_line, str_code_part):

            # 当前行没有业务语义，直接进入下一轮扫描。
            continue

        # str_kind 保存当前语句的最终分类。
        str_kind = _statement_kind_for_code(str_code_part)  # 当前语句分类

        # 记录当前语句的行号、文本、分类和深度。
        list_infos.append(StatementInfo(int_index + 1, str_code_part, str_kind, list_depths[int_index]))

    # 返回语句扫描结果。
    return list_infos

# parse_functions 轻量提取 C/C++ 函数声明和定义边界。
def parse_functions(lines: list[str]) -> list[FunctionInfo]:
    """从源码行中轻量解析函数声明和函数定义边界。

    :param lines: C/C++ 源码行列表。
    :return: 按源码顺序排列的函数信息列表。
    """

    # obj_state 保存跨行函数签名和函数体扫描状态。
    function_parse_state_function_parse_state: FunctionParseState = FunctionParseState()  # 函数解析状态

    # 逐行推进轻量函数解析器。
    for int_line_number, str_raw_line in enumerate(lines, start=1):

        # 提取当前行有效代码，供函数签名或函数体推进逻辑复用。
        str_code_part = code_part(str_raw_line).strip()  # 函数解析阶段当前行的有效代码

        # 空行和注释行不参与函数签名识别。
        if not str_code_part or is_comment_only(str_raw_line):

            # 当前行不能形成签名片段，直接跳过。
            continue

        # 根据当前是否已进入函数体选择推进逻辑。
        if function_parse_state_function_parse_state.bool_in_function:

            # 在函数体内只需要更新花括号深度和结束位置。
            _advance_function_body(function_parse_state_function_parse_state, int_line_number, str_code_part)

        # 尚未进入函数体时，当前行只能继续补全声明或定义签名缓存。
        else:

            # 在函数体外尝试收集声明或定义签名。
            _advance_function_signature(function_parse_state_function_parse_state, int_line_number, str_code_part)

    # 返回扫描过程中收集到的函数信息。
    return function_parse_state_function_parse_state.list_functions

# _parse_function_signature 解析合并后的函数签名文本。
def _parse_function_signature(signature: str) -> tuple[str, tuple[str, ...], str] | None:
    """从轻量函数签名中解析函数名、参数名和返回类型。

    :param signature: 合并后的函数签名文本，可带结尾 { 或 ;。
    :return: 成功时返回函数名、参数名元组和返回类型；失败时返回 None。
    """

    # str_signature_text 清理签名结尾符号和多余空白。
    str_signature_text = re.sub(r"\s+", " ", signature.strip()).rstrip("{").rstrip(";").strip()  # 归一化签名文本

    # 先定义轻量签名拆分模式，便于复用和单元定位。
    str_signature_pattern = r"(.+?)\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:const)?$"  # 返回类型、函数名与参数的轻量签名模式

    # 用统一模式拆分返回类型、函数名和参数文本。
    list_signature_groups = re.findall(str_signature_pattern, str_signature_text)  # 函数签名正则匹配结果

    # 正则未命中说明该片段不是当前轻量解析器支持的签名。
    if not list_signature_groups:

        # 当前候选无法拆成返回类型、函数名和参数三段。
        return None

    # str_return_type_text、str_function_name 和 str_params_text 对应签名三段。
    str_return_type_text, str_function_name, str_params_text = list_signature_groups[0]  # 函数签名拆分结果

    # 控制流关键字不能作为函数名。
    if str_function_name in {"if", "for", "while", "switch", "return"}:

        # 避免将控制语句误解析为函数。
        return None

    # tuple_params 保存从参数列表提取出的形参名。
    tuple_params = tuple(_parameter_names(str_params_text))  # 函数参数名元组

    # 返回函数名、参数名和返回类型文本。
    return str_function_name, tuple_params, str_return_type_text.strip()

# _parameter_names 提取函数参数声明中的参数名。
def _parameter_names(params_text: str) -> Iterable[str]:
    """提取 C/C++ 参数列表中的参数名。

    :param params_text: 函数签名括号内的参数文本。
    :return: 参数名的可迭代集合；void 或空参数返回空列表。
    """

    # str_params_text 保存清理后的参数整体文本。
    str_params_text = params_text.strip()  # 去空白后的参数文本

    # 空参数和 void 参数都没有业务参数名。
    if not str_params_text or str_params_text == "void":

        # 保持旧接口返回空列表。
        return []

    # list_names 收集每个参数声明中识别到的名称。
    list_names: list[str] = []  # 参数名列表

    # 逗号拆分需要避开模板和括号嵌套。
    for str_raw_param in _split_params(params_text):

        # str_parameter_token 去掉默认值并拆开引用/指针符号。
        str_parameter_token = _normalize_parameter_token(str_raw_param)  # 归一化参数声明

        # list_words 保存可能包含类型和参数名的 token。
        list_words = _parameter_token_words(str_parameter_token)  # 参数声明 token 列表

        # 没有 token 时跳过该参数片段。
        if not list_words:

            # 继续处理下一段参数。
            continue

        # str_candidate_name 默认取参数声明最后一个 token。
        str_candidate_name = list_words[-1]  # 参数名候选

        # str_candidate_name 去掉数组声明中的括号部分。
        str_candidate_name = _array_name_from_candidate(str_candidate_name)  # 去数组后缀的参数名候选

        # 合法标识符才进入参数名列表。
        if re.match(r"^[A-Za-z_]\w*$", str_candidate_name):

            # 记录当前参数名。
            list_names.append(str_candidate_name)

    # 返回提取到的参数名列表。
    return list_names

# _split_params 按顶层逗号拆分参数列表。
def _split_params(params_text: str) -> list[str]:
    """在模板和括号嵌套之外按逗号拆分参数文本。

    :param params_text: 函数签名括号内的参数文本。
    :return: 拆分后的参数声明片段列表。
    """

    # list_parts 保存顶层逗号切出的参数片段。
    list_parts: list[str] = []  # 参数片段列表

    # int_depth 记录模板尖括号和括号嵌套深度。
    int_depth = 0  # 参数片段嵌套深度

    # int_start_index 记录当前参数片段起始位置。
    int_start_index = 0  # 当前参数片段起始索引

    # 逐字符寻找顶层逗号。
    for int_index, str_char in enumerate(params_text):

        # 左括号或模板起点增加嵌套深度。
        # int_depth 根据当前括号或模板边界更新嵌套深度。
        int_depth = _updated_parameter_depth(str_char, int_depth)  # 更新后的参数嵌套深度

        # 顶层逗号表示一个参数声明结束。
        if str_char == "," and int_depth == 0:

            # 保存当前参数片段。
            list_parts.append(params_text[int_start_index:int_index])

            # 下一段参数从逗号后开始。
            int_start_index = int_index + 1  # 下一参数片段起始索引

    # 保存最后一个参数片段。
    list_parts.append(params_text[int_start_index:])

    # 返回顶层拆分后的参数片段。
    return list_parts

# _updated_parameter_depth 维护参数拆分时的嵌套深度。
def _updated_parameter_depth(str_char: str, int_depth: int) -> int:
    """根据当前字符更新参数列表拆分的嵌套深度。

    :param str_char: 参数文本中的当前字符。
    :param int_depth: 进入当前字符前的嵌套深度。
    :return: 处理当前字符后的嵌套深度。
    """

    # 左括号和模板起点会增加嵌套层级。
    if str_char in "<([":

        # 返回进入嵌套结构后的深度。
        return int_depth + 1

    # 右括号和模板终点会降低已有嵌套层级。
    if str_char in ">)]" and int_depth:

        # 返回离开一层嵌套结构后的深度。
        return int_depth - 1

    # 其他字符不改变参数拆分层级。
    return int_depth

# extract_identifier_from_declaration 提取局部声明中的变量名。
def extract_identifier_from_declaration(code: str) -> str | None:
    """从局部声明代码中提取声明目标标识符。

    :param code: 去注释后的局部声明语句。
    :return: 成功时返回变量名；无法识别时返回 None。
    """

    # str_stripped_code 去掉结尾分号后用于拆分初始化表达式。
    str_stripped_code = code.strip().rstrip(";")  # 去分号后的声明代码

    # 空声明没有可提取标识符。
    if not str_stripped_code:

        # 空声明没有变量名可供返回。
        return None

    # str_before_initializer 只保留变量初始化或聚合初始化之前的声明部分。
    # 先按初始化边界切开声明，再只保留左侧的变量声明部分。
    str_initializer_split_pattern = ASSIGNMENT_OPERATOR_PATTERN + r"|\{|;"  # 初始化边界拆分模式

    # 按初始化边界切开声明，得到左侧声明片段和右侧初始化片段。
    list_initializer_parts = re.split(str_initializer_split_pattern, str_stripped_code, maxsplit=1)  # 初始化符号切开的声明片段列表

    # 取初始化边界左侧的声明片段供变量名提取使用。
    str_before_initializer = list_initializer_parts[0].strip()  # 初始化前的声明片段

    # str_before_initializer 拆开引用和指针符号，便于按空白取最后一项。
    str_before_initializer = str_before_initializer.replace("&", " & ").replace("*", " * ")  # 拆分指针引用后的声明片段

    # list_words 排除修饰符后保留类型和变量名 token。
    # 先按空白拆出声明里的原始 token 序列。
    list_raw_words = re.split(r"\s+", str_before_initializer)  # 声明原始 token 列表

    # 过滤修饰符和指针符号后，保留变量名提取需要的 token。
    set_ignored_tokens = {"const", "static", "volatile", "&", "*"}  # 声明 token 过滤白名单

    # 从原始 token 列表里剔除修饰符，只保留候选类型名和变量名。
    list_words = [str_part for str_part in list_raw_words if str_part and str_part not in set_ignored_tokens]  # 过滤修饰符后的声明 token 列表

    # 没有 token 时无法提取变量名。
    if not list_words:

        # 调用方用 None 表示未识别到声明目标。
        return None

    # str_candidate_name 默认取声明最后一个 token。
    str_candidate_name = list_words[-1]  # 声明标识符候选

    # str_candidate_name 去掉数组声明后缀。
    str_candidate_name = str_candidate_name.split("[")[0]  # 去数组后缀的标识符候选

    # 合法标识符才返回给调用方。
    return str_candidate_name if re.match(r"^[A-Za-z_]\w*$", str_candidate_name) else None

# extract_assignment_target 提取赋值语句左侧的基础目标名。
def extract_assignment_target(code: str) -> str | None:
    """从赋值语句中提取左侧基础标识符。

    :param code: 去注释后的赋值语句。
    :return: 成功时返回基础目标名；无法识别时返回 None。
    """

    # str_stripped_code 去掉缩进后用于赋值左侧匹配。
    str_stripped_code = code.strip()  # 去缩进赋值语句

    # list_assignment_targets 捕获左侧标识符、数组元素或成员访问。
    # 先定义赋值左侧可接受的目标模式，便于后续复用和阅读。
    str_assignment_target_pattern = (
        r"([A-Za-z_]\w*(?:\[[^\]]+\])?(?:\.[A-Za-z_]\w*)?)\s*"
        r"(?:=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>=)"
    )  # 赋值左侧目标匹配模式

    # 用统一模式提取赋值左侧的标识符、数组元素或成员访问。
    list_assignment_targets = re.findall(str_assignment_target_pattern, str_stripped_code)  # 赋值左侧匹配结果

    # 未命中赋值左侧时无法提取目标。
    if not list_assignment_targets:

        # 调用方用 None 表示非支持的赋值形态。
        return None

    # str_assignment_target 保存匹配到的完整左侧目标。
    str_assignment_target = list_assignment_targets[0]  # 赋值左侧完整目标

    # 返回数组下标或成员访问前的基础标识符。
    return re.split(r"\[|\.", str_assignment_target, maxsplit=1)[0]

# find_multiline_statement_starts 找出多行语句起始行。
def find_multiline_statement_starts(lines: list[str]) -> list[tuple[int, str]]:
    """查找可能需要跨行注释保护的多行语句起点。

    :param lines: 源码行列表。
    :return: 元组列表，包含起始行号和合并后的语句文本。
    """

    # list_findings 保存多行语句起点及合并文本。
    list_findings: list[tuple[int, str]] = []  # 多行语句发现结果

    # obj_accumulator 保存当前正在积累的多行语句。
    multiline_statement_state_multiline_statement_state: MultilineStatementState = MultilineStatementState()  # 多行语句积累状态

    # 逐行检查声明或调用是否跨行。
    for int_line_number, str_raw_line in enumerate(lines, start=1):

        # 提取当前行有效代码，供多行语句检测器判断是否继续积累。
        str_code_part = code_part(str_raw_line).strip()  # 多行语句扫描阶段当前行的有效代码

        # 空行和注释行不参与多行语句积累。
        if not str_code_part or is_comment_only(str_raw_line):

            # 继续扫描下一行源码。
            continue

        # 根据当前状态推进多行语句扫描。
        _advance_multiline_statement(
            multiline_statement_state_multiline_statement_state,
            list_findings,
            int_line_number,
            str_code_part,
        )

    # 返回多行语句起始行列表。
    return list_findings

# BlockCommentState 保存未闭合块注释的跨行状态。
@dataclass
class BlockCommentState:
    """保存跨行块注释扫描过程中的中间状态。

    :param int_start_line: 块注释起始行号。
    :param list_parts: 去掉起始标记后的正文片段。
    :param list_raw_parts: 源码原始注释片段。
    :param str_code_before: 块注释起始行注释前的代码。
    :return: dataclass 实例仅承载块注释扫描状态。
    """

    # int_start_line 保留块注释起点。
    int_start_line: int  # 块注释起始行

    # list_parts 保存归一化前的正文行片段。
    list_parts: list[str]  # 块注释正文片段

    # list_raw_parts 保存完整块注释原始行片段。
    list_raw_parts: list[str]  # 块注释原始片段

    # str_code_before 保存块注释起始列前的代码。
    str_code_before: str  # 块注释前代码片段

# CommentLineScan 保存单行注释扫描结果。
@dataclass(frozen=True)
class CommentLineScan:
    """封装单行注释扫描得到的注释跨度或块注释状态。

    :param comment_span: 当前行已闭合的注释跨度。
    :param block_state: 当前行开启但尚未闭合的块注释状态。
    :return: dataclass 实例仅承载单行扫描结果。
    """

    # comment_span 表示当前行已经完整识别出的注释。
    comment_span: CommentSpan | None  # 已闭合注释跨度

    # block_state 表示需要下一行继续消费的块注释。
    block_state: BlockCommentState | None  # 需要带到下一行继续消费的块注释状态

# FunctionParseState 保存函数边界扫描状态。
@dataclass
class FunctionParseState:
    """保存轻量函数解析过程中的可变状态。

    :param list_functions: 已解析完成的函数信息。
    :param list_pending_signature_lines: 函数体外正在积累的签名行。
    :param int_pending_start: 待解析签名的起始行。
    :param bool_in_function: 当前是否处于函数体内。
    :param int_function_start: 当前函数体起始行。
    :param int_signature_start: 当前函数签名起始行。
    :param str_signature_text: 当前函数签名合并文本。
    :param int_brace_depth: 当前函数体花括号深度。
    :param str_name: 当前函数名。
    :param tuple_params: 当前函数参数名元组。
    :param str_return_type: 当前函数返回类型文本。
    :return: dataclass 实例仅承载函数解析状态。
    """

    # list_functions 保存已完成解析的函数条目。
    list_functions: list[FunctionInfo] = None  # 已解析函数列表

    # list_pending_signature_lines 保存函数体外积累的签名行。
    list_pending_signature_lines: list[str] = None  # 待解析签名行列表

    # int_pending_start 记录签名候选起始行。
    int_pending_start: int = 0  # 待解析签名起始行

    # bool_in_function 标识当前是否在函数体内部。
    bool_in_function: bool = False  # 是否正在扫描函数体

    # int_function_start 记录当前函数体起始行。
    int_function_start: int = 0  # 当前函数体起始行

    # int_signature_start 记录当前函数签名起始行。
    int_signature_start: int = 0  # 当前函数签名起始行

    # str_signature_text 保存当前函数完整签名文本。
    str_signature_text: str = ""  # 当前函数签名文本

    # int_brace_depth 保存当前函数体括号深度。
    int_brace_depth: int = 0  # 当前函数体花括号深度

    # str_name 保存当前函数名。
    str_name: str = ""  # 当前函数名

    # tuple_params 保存当前函数参数名元组。
    tuple_params: tuple[str, ...] = ()  # 当前函数参数名元组

    # str_return_type 保存当前函数返回类型。
    str_return_type: str = ""  # 当前函数返回类型文本

    # __post_init__ 避免 dataclass 使用可变默认值。
    def __post_init__(self) -> None:
        """初始化可变列表字段。

        :param: 无。
        :return: 无返回值；该方法只更新实例内部列表字段。
        """

        # list_functions 缺省时创建当前实例独有的列表。
        if self.list_functions is None:

            # 保存当前文件解析出的函数信息。
            self.list_functions = []  # 当前实例独有的函数信息列表

        # 缺省时创建当前实例独有的签名行缓冲。
        if self.list_pending_signature_lines is None:

            # 保存当前正在积累的函数签名行。
            self.list_pending_signature_lines = []  # 当前实例独有的签名行缓冲

# MultilineStatementState 保存多行语句积累状态。
@dataclass
class MultilineStatementState:
    """保存多行语句扫描过程中的积累状态。

    :param bool_accumulating: 当前是否正在积累多行语句。
    :param int_start_line: 多行语句起始行号。
    :param list_buffer: 已积累的代码片段。
    :return: dataclass 实例仅承载多行语句扫描状态。
    """

    # bool_accumulating 标识是否已经进入多行语句。
    bool_accumulating: bool = False  # 是否正在积累多行语句

    # int_start_line 保存多行语句起点。
    int_start_line: int = 0  # 多行语句起始行

    # list_buffer 保存多行语句片段。
    list_buffer: list[str] = None  # 多行语句代码片段

    # __post_init__ 避免可变默认值共享。
    def __post_init__(self) -> None:
        """初始化多行语句缓冲区。

        :param: 无。
        :return: 无返回值；该方法只更新实例内部缓冲区。
        """

        # 缺省时创建当前实例独有的多行语句缓冲。
        if self.list_buffer is None:

            # 保存当前多行语句的代码片段。
            self.list_buffer = []  # 当前实例独有的多行语句缓冲

# _strip_comment_text 扫描整段源码并剥离注释。
def _strip_comment_text(text: str) -> str:
    """剥离源码注释，同时保留字符串字面量和块注释换行。

    :param text: 待处理的 C/C++ 源码文本。
    :return: 去掉注释后的源码文本。
    """

    # list_output 保存扫描过程中输出的字符。
    list_output: list[str] = []  # 去注释后的字符列表

    # int_index 指向当前扫描字符。
    int_index = 0  # 源码扫描索引

    # str_state 区分普通代码和字符串字面量。
    str_state = "code"  # 当前扫描状态

    # str_quote 保存当前字符串使用的引号。
    str_quote = ""  # 当前字符串引号

    # 主循环逐字符扫描源码。
    while int_index < len(text):

        # str_char 保存当前字符。
        str_char = text[int_index]  # 当前扫描字符

        # str_next_char 保存下一个字符，越界时为空。
        str_next_char = text[int_index + 1] if int_index + 1 < len(text) else ""  # 下一字符

        # 代码状态负责识别注释起点和字符串起点。
        if str_state == "code":

            # obj_step 保存代码状态下一步扫描结果。
            strip_step_strip_step = _strip_comment_code_step(list_output, text, int_index, str_char, str_next_char)  # 代码状态扫描结果

            # 字符串起点需要切换状态。
            if strip_step_strip_step.str_state is not None:

                # 保存进入字符串后的扫描状态。
                str_state = strip_step_strip_step.str_state  # 注释扫描器进入字符串状态

                # 保存字符串使用的引号。
                str_quote = strip_step_strip_step.str_quote  # 当前字符串字面量引号

            # 更新扫描索引。
            int_index = strip_step_strip_step.int_next_index  # 注释扫描器下一字符索引

            # 当前字符处理结束。
            continue

        # 字符串状态保留所有字符。
        list_output.append(str_char)

        # 字符串中的转义字符需要额外保留下一个字符。
        if str_char == "\\" and int_index + 1 < len(text):

            # 保存被转义字符。
            list_output.append(text[int_index + 1])

            # 跳过被转义字符。
            int_index += 2  # 跳过字符串转义字符

            # 当前转义序列处理结束。
            continue

        # 当前引号闭合字符串状态。
        if str_char == str_quote:

            # 回到普通代码扫描状态。
            str_state = "code"  # 字符串闭合后回到代码状态

            # 清空当前字符串引号记录。
            str_quote = ""  # 当前没有未闭合字符串引号

        # 移动到下一个字符。
        int_index += 1  # 继续扫描下一个源码字符

    # 返回拼接后的去注释源码。
    return "".join(list_output)

# StripStep 保存注释剥离单步扫描结果。
@dataclass(frozen=True)
class StripStep:
    """描述注释剥离扫描器处理一个代码字符后的状态。

    :param int_next_index: 下一次扫描的字符索引。
    :param str_state: 需要切换到的状态；不切换时为 None。
    :param str_quote: 进入字符串状态时使用的引号。
    :return: dataclass 实例仅承载扫描步进结果。
    """

    # int_next_index 指向下一次扫描位置。
    int_next_index: int  # 下一扫描索引

    # str_state 在进入字符串时携带目标状态。
    str_state: str | None  # 状态切换目标

    # str_quote 保存字符串引号。
    str_quote: str  # 字符串引号

# _strip_comment_code_step 处理普通代码状态下的一个字符。
def _strip_comment_code_step(
    list_output: list[str],
    text: str,
    int_index: int,
    str_char: str,
    str_next_char: str,
) -> StripStep:
    """处理去注释扫描器在普通代码状态下的单步逻辑。

    :param list_output: 去注释输出字符列表，会在函数内追加字符。
    :param text: 完整源码文本。
    :param int_index: 当前字符索引。
    :param str_char: 当前字符。
    :param str_next_char: 下一个字符；越界时为空。
    :return: 下一索引以及必要的状态切换信息。
    """

    # 字符串起点需要保留引号并切换状态。
    if str_char in {'"', "'"}:

        # 原样保留字符串起始引号。
        list_output.append(str_char)

        # 返回进入字符串状态后的下一索引。
        return StripStep(int_index + 1, "string", str_char)

    # 双斜杠注释替换为空格并跳到行尾。
    if str_char == "/" and str_next_char == "/":

        # 行注释跳读逻辑交给专用 helper，避免主分支过深。
        return _strip_line_comment_step(list_output, text, int_index)

    # 块注释需要保留内部换行以维持行号稳定。
    if str_char == "/" and str_next_char == "*":

        # 块注释跳读逻辑交给专用 helper，保留换行但隐藏细节。
        return _strip_block_comment_step(list_output, text, int_index)

    # 普通代码字符原样保留。
    list_output.append(str_char)

    # 返回下一个字符索引。
    return StripStep(int_index + 1, None, "")

# _strip_line_comment_step 跳过双斜杠注释正文。
def _strip_line_comment_step(list_output: list[str], text: str, int_index: int) -> StripStep:
    """处理去注释扫描器遇到 // 行注释的跳读逻辑。

    :param list_output: 去注释输出字符列表，会在函数内追加空白占位。
    :param text: 完整源码文本。
    :param int_index: // 起始字符索引。
    :return: 行注释结束位置对应的扫描步进结果。
    """

    # 在注释位置补空格，避免前后 token 被误拼成一个标识符。
    list_output.append(" ")

    # int_next_index 跳过注释起始标记。
    int_next_index = int_index + 2  # 行注释正文扫描索引

    # 跳到换行符或文本末尾。
    while int_next_index < len(text) and text[int_next_index] not in "\r\n":

        # 行注释正文不进入输出。
        int_next_index += 1  # 行注释跳读索引

    # 返回行尾位置继续主循环处理换行。
    return StripStep(int_next_index, None, "")

# _strip_block_comment_step 跳过块注释并保留其中换行。
def _strip_block_comment_step(list_output: list[str], text: str, int_index: int) -> StripStep:
    """处理去注释扫描器遇到 /* 块注释的跳读逻辑。

    :param list_output: 去注释输出字符列表，会在函数内追加空白和换行。
    :param text: 完整源码文本。
    :param int_index: /* 起始字符索引。
    :return: 块注释结束位置对应的扫描步进结果。
    """

    # 保留一个空格避免注释两侧 token 粘连。
    list_output.append(" ")

    # int_next_index 跳过块注释起始标记。
    int_next_index = int_index + 2  # 块注释正文扫描索引

    # 扫描到块注释结束或源码末尾。
    while int_next_index + 1 < len(text) and not _is_block_comment_close(text, int_next_index):

        # 块注释内换行需要保留。
        if text[int_next_index] in "\r\n":

            # 保留换行符以保持行号对齐。
            list_output.append(text[int_next_index])

        # 块注释正文不进入输出。
        int_next_index += 1  # 块注释跳读索引

    # 若存在闭合标记，则跳过 */。
    int_next_index += 2 if int_next_index + 1 < len(text) else 0  # 跳过块注释闭合标记

    # 返回块注释结束后的扫描位置。
    return StripStep(int_next_index, None, "")

# _is_block_comment_close 判断当前位置是否为块注释闭合标记。
def _is_block_comment_close(text: str, int_index: int) -> bool:
    """判断源码指定索引是否落在 */ 块注释闭合标记上。

    :param text: 完整源码文本。
    :param int_index: 当前字符索引。
    :return: 当前字符和下一字符组成 */ 时返回 True。
    """

    # bool_is_close 保存当前位置的块注释闭合判断。
    bool_is_close = text[int_index] == "*" and text[int_index + 1] == "/"  # 是否为块注释闭合标记

    # 返回闭合标记判断结果。
    return bool_is_close

# _code_before_comment 扫描单行源码直到注释起点。
def _code_before_comment(line: str) -> str:
    """返回单行源码中第一个真实注释标记之前的内容。

    :param line: 待扫描的单行源码。
    :return: 注释前代码片段。
    """

    # list_output 保存注释前的字符。
    list_output: list[str] = []  # 注释前字符列表

    # int_index 始终落在尚未判定是否属于注释的当前字符上。
    int_index = 0  # 从行首开始逐字符寻找第一个真实注释起点

    # str_state 只区分普通代码段和字符串字面量，避免把字符串里的注释符号误判成真注释。
    str_state = "code"  # 初始阶段按普通代码解析整行

    # str_quote 记住打开字符串时使用的引号类型，便于在同类引号处安全退出。
    str_quote = ""  # 尚未进入字符串时保持空引号状态

    # 逐字符扫描单行源码。
    while int_index < len(line):

        # str_char 保存当前待判定字符，供字符串和注释分支共享判断结果。
        str_char = line[int_index]  # 当前需要判断语义的源码字符

        # str_next_char 只在识别 //、/* 和转义结构时读取一个字符的前看窗口。
        str_next_char = line[int_index + 1] if int_index + 1 < len(line) else ""  # 用于识别双字符边界的后看字符

        # 普通代码状态检测字符串和注释起点。
        if str_state == "code":

            # 字符串起点需要保留并切换状态。
            if str_char in {'"', "'"}:

                # 保存字符串起始引号。
                list_output.append(str_char)

                # 切换到字符串扫描状态。
                str_state = "string"  # 单行扫描器进入字符串状态

                # 记录触发字符串态的引号类型，避免单引号和双引号交叉提前闭合。
                str_quote = str_char  # 当前字符串字面量使用的引号

                # 字符串起始引号已经消费，下一轮从字符串正文首字符继续。
                int_index += 1  # 跳到字符串正文的首个字符位置

                # 进入字符串态后，本轮字符已经消费完毕。
                continue

            # 真正注释起点处停止扫描。
            if str_char == "/" and str_next_char in {"/", "*"}:

                # 结束并返回注释前的代码片段。
                break

            # 普通代码字符进入输出。
            list_output.append(str_char)

            # 普通代码字符写入输出后，继续检查后续字符是否仍属于代码区。
            int_index += 1  # 普通源码段按单字符步进继续扫描

            # 当前普通代码字符已经处理完成。
            continue

        # 字符串态下的字符需要原样写回输出缓冲。
        list_output.append(str_char)

        # 转义字符同时保留下一个字符。
        if str_char == "\\" and int_index + 1 < len(line):

            # 被反斜杠保护的后继字符也必须原样保留。
            list_output.append(line[int_index + 1])

            # 转义序列两字符必须一起保留，否则后续引号闭合判断会失真。
            int_index += 2  # 直接跳过反斜杠和其后的被转义字符

            # 转义序列已经整体消费，本轮不再做闭合判断。
            continue

        # 当前引号结束字符串状态。
        if str_char == str_quote:

            # 只有遇到成对闭合引号时才能返回代码态，继续寻找真正注释起点。
            str_state = "code"  # 当前字符串结束后恢复普通代码扫描

            # 退出字符串后清空引号记忆，避免影响后续字符判断。
            str_quote = ""  # 退出字符串后不再保留历史引号类型

        # 字符串中的普通字符逐个推进，直到遇到转义或闭合引号。
        int_index += 1  # 字符串态按单字符继续前进

    # 返回注释前代码字符串。
    return "".join(list_output)

# _scan_comment_start_in_line 在非块注释状态下扫描一行。
def _scan_comment_start_in_line(int_line_number: int, str_line: str) -> CommentLineScan:
    """扫描单行中的第一个真实注释起点。

    :param int_line_number: 当前行号，按一基计数。
    :param str_line: 当前源码行。
    :return: 当前行注释跨度或未闭合块注释状态。
    """

    # int_index 在这一行里追踪尚未判断是否为注释起点的当前位置。
    int_index = 0  # 从当前行开头向后寻找第一个真实注释标记

    # str_state 把字符串字面量与普通代码分开，避免把字符串里的 // 和 /* 当成注释。
    str_state = "code"  # 注释查找初始时先按普通代码段处理

    # str_quote 记录进入字符串时的引号类型，保证只在匹配的闭合引号处退出。
    str_quote = ""  # 尚未进入字符串时没有活动引号

    # 逐字符扫描当前行。
    while int_index < len(str_line):

        # str_char 保存当前候选字符，供注释起点和字符串状态共用判断。
        str_char = str_line[int_index]  # 当前待识别角色的源码字符

        # str_next_char 提供一个字符的前看窗口，用于识别 //、/* 和转义组合。
        str_next_char = str_line[int_index + 1] if int_index + 1 < len(str_line) else ""  # 识别双字符语法所需的后看字符

        # 普通代码状态负责发现注释起点。
        if str_state == "code":

            # 字符串起点进入字符串状态。
            if str_char in {'"', "'"}:

                # 进入字符串态后，这一段里的注释符号都必须按普通字符忽略。
                str_state = "string"  # 注释查找器切到字符串保护状态

                # 记住当前使用的引号类型，后续只在同类引号上闭合字符串态。
                str_quote = str_char  # 当前字符串字面量采用的引号类型

                # 起始引号已经消费，下一轮从字符串正文继续扫描。
                int_index += 1  # 进入字符串后的首个正文字符索引

                # 进入字符串保护态后，本轮字符不再参与注释判断。
                continue

            # 行注释一旦出现即结束当前行扫描。
            if str_char == "/" and str_next_char == "/":

                # 返回当前行注释跨度。
                return CommentLineScan(
                    _line_comment_span(int_line_number, str_line, int_index),
                    None,
                )

            # 块注释可能在当前行闭合，也可能跨行。
            if str_char == "/" and str_next_char == "*":

                # 把块注释起点交给专门 helper 继续处理。
                return _block_comment_start(int_line_number, str_line, int_index)

            # 既不是注释也不是字符串起点时，继续向后寻找真正的注释边界。
            int_index += 1  # 普通代码段按单字符继续向后扫描

            # 当前字符不是注释边界，本轮可以直接结束。
            continue

        # 字符串转义需要跳过下一个字符。
        if str_char == "\\" and int_index + 1 < len(str_line):

            # 转义序列要整体跳过，避免把被转义引号误当成字符串结束。
            int_index += 2  # 直接越过反斜杠和它保护的下一个字符

            # 被转义引号不能结束字符串，本轮直接跳到下一个候选。
            continue

        # 字符串结束后回到普通代码状态。
        if str_char == str_quote:

            # 当前字符串闭合后，后续字符重新允许触发真实注释检测。
            str_state = "code"  # 字符串闭合后恢复代码态注释扫描

            # 闭合字符串后清空引号记忆，避免污染下一段字符串判断。
            str_quote = ""  # 当前行后续字符不再处于该引号保护下

        # 未遇到转义或闭合引号时，字符串态按单字符向后推进。
        int_index += 1  # 字符串态继续向后扫描

    # 当前行没有发现注释。
    return CommentLineScan(None, None)

# _line_comment_span 构造单行注释跨度。
def _line_comment_span(int_line_number: int, str_line: str, int_comment_index: int) -> CommentSpan:
    """构造 // 行注释的 CommentSpan。

    :param int_line_number: 当前行号，按一基计数。
    :param str_line: 当前源码行。
    :param int_comment_index: // 注释起始列。
    :return: 当前行注释跨度。
    """

    # str_code_before 保存注释前的源码片段。
    str_code_before = str_line[:int_comment_index]  # 行注释前代码

    # str_raw_comment 保存注释原文。
    str_raw_comment = str_line[int_comment_index:]  # 行注释原文

    # 返回单行注释跨度。
    return CommentSpan(
        int_line_number,
        int_line_number,
        normalize_comment_text(str_raw_comment),
        str_raw_comment,
        "line",
        bool(str_code_before.strip()),
        str_code_before,
    )

# _block_comment_start 处理块注释起始行。
def _block_comment_start(int_line_number: int, str_line: str, int_comment_index: int) -> CommentLineScan:
    """处理一行中的 /* 块注释起点。

    :param int_line_number: 当前行号，按一基计数。
    :param str_line: 当前源码行。
    :param int_comment_index: /* 注释起始列。
    :return: 已闭合注释跨度或未闭合块注释状态。
    """

    # str_code_before 保存块注释前的源码片段。
    str_code_before = str_line[:int_comment_index]  # 块注释前代码

    # int_end_index 查找当前行内的块注释结束标记。
    int_end_index = str_line.find("*/", int_comment_index + 2)  # 块注释结束列

    # 当前行内闭合时直接构造 CommentSpan。
    if int_end_index >= 0:

        # str_raw_comment 保存当前行完整块注释原文。
        str_raw_comment = str_line[int_comment_index:int_end_index + 2]  # 单行块注释原文

        # 返回已闭合块注释跨度。
        return CommentLineScan(
            CommentSpan(
                int_line_number,
                int_line_number,
                normalize_comment_text(str_raw_comment),
                str_raw_comment,
                "block",
                bool(str_code_before.strip()),
                str_code_before,
            ),
            None,
        )

    # 把未闭合块注释的起点和正文缓存到状态对象里。
    list_block_parts = [str_line[int_comment_index + 2:]]  # 起始行去掉 /* 后的正文片段

    # 保留带注释标记的原始文本，供后续拼接完整块注释原文。
    list_block_raw_parts = [str_line[int_comment_index:]]  # 起始行保留注释标记的原始片段

    # 组合当前块注释的四个状态字段，供下一行继续沿用。
    block_comment_state = BlockCommentState(int_line_number, list_block_parts, list_block_raw_parts, str_code_before)  # 块注释状态

    # 返回未闭合块注释状态。
    return CommentLineScan(None, block_comment_state)

# _consume_block_comment_line 继续消费跨行块注释。
def _consume_block_comment_line(
    list_comments: list[CommentSpan],
    obj_state: BlockCommentState,
    int_line_number: int,
    str_line: str,
) -> BlockCommentState | None:
    """消费跨行块注释中的一行。

    :param list_comments: 已收集注释列表，块注释闭合时会追加新条目。
    :param obj_state: 当前未闭合块注释状态。
    :param int_line_number: 当前行号，按一基计数。
    :param str_line: 当前源码行。
    :return: 块注释仍未闭合时返回状态；闭合后返回 None。
    """

    # 当前行原文始终属于块注释原文。
    obj_state.list_raw_parts.append(str_line)

    # 查找当前块注释是否在本行闭合。
    int_end_index = str_line.find("*/")  # 当前块注释在本行的闭合位置

    # 当前行闭合块注释时构造完整跨度。
    if int_end_index >= 0:

        # 仅结束标记之前属于块注释正文。
        obj_state.list_parts.append(str_line[:int_end_index])

        # str_raw_comment 拼接完整块注释原文。
        str_raw_comment = "\n".join(obj_state.list_raw_parts)  # 跨行块注释原文

        # str_comment_text 拼接块注释正文并归一化。
        str_comment_text = normalize_comment_text("\n".join(obj_state.list_parts))  # 跨行块注释正文

        # 记录完整块注释跨度。
        list_comments.append(
            CommentSpan(
                obj_state.int_start_line,
                int_line_number,
                str_comment_text,
                str_raw_comment,
                "block",
                bool(obj_state.str_code_before.strip()),
                obj_state.str_code_before,
            )
        )

        # 返回 None 表示块注释已经闭合。
        return None

    # 未闭合时整行都属于块注释正文。
    obj_state.list_parts.append(str_line)

    # 返回原状态供下一行继续消费。
    return obj_state

# _is_meaningful_code_line 判断行级代码是否值得规则检查。
def _is_meaningful_code_line(str_line: str, str_code_part: str) -> bool:
    """判断源码行是否包含需要检查的语义代码。

    :param str_line: 原始源码行。
    :param str_code_part: 去注释并清理后的代码片段。
    :return: 非空、非注释、非结构壳代码返回 True。
    """

    # 合并三类过滤条件，判断当前行是否值得进入语义规则。
    bool_has_code = bool(str_code_part)  # 当前行是否存在非空代码文本

    # 只有非空、非注释、非结构壳的代码行才值得进入语义规则。
    bool_has_meaningful_code = bool_has_code and not is_comment_only(str_line) and not is_trivial_code(str_code_part)  # 是否为有语义的代码行

    # 返回当前行是否应进入语句规则。
    return bool_has_meaningful_code

# _is_not_local_declaration_shape 集中处理局部声明排除条件。
def _is_not_local_declaration_shape(str_stripped_code: str) -> bool:
    """判断代码片段是否不具备局部声明外形。

    :param str_stripped_code: 已去除首尾空白的代码片段。
    :return: 不应按局部声明处理时返回 True。
    """

    # 合并预处理、函数签名和类型定义等排除条件。
    bool_lacks_semicolon = not str_stripped_code.endswith(";")  # 当前片段是否缺少声明结束分号

    # 先分别判断预处理、函数签名和类型定义三类非声明形态。
    bool_is_preprocessor = str_stripped_code.startswith("#")  # 当前片段是否以预处理前缀开头

    # 单独判断当前片段是否会被函数签名规则命中。
    bool_matches_signature = is_function_signature(str_stripped_code)  # 当前片段是否会被函数签名规则命中

    # 单独判断当前片段是否会被类型定义规则命中。
    bool_matches_type_definition = is_type_definition(str_stripped_code)  # 当前片段是否会被类型定义规则命中

    # 汇总预处理、函数签名与类型定义等非声明形态。
    bool_hits_non_declaration_case = bool_is_preprocessor or bool_matches_signature or bool_matches_type_definition  # 其余不应按局部声明处理的形态

    # 汇总缺分号与特殊形态命中结果，判断当前片段是否应排除。
    bool_is_invalid_shape = bool_lacks_semicolon or bool_hits_non_declaration_case  # 是否不具备局部声明外形

    # 返回局部声明外形排除结果。
    return bool_is_invalid_shape

# _control_statement_kind 返回控制语句类别。
def _control_statement_kind(str_stripped_code: str) -> str | None:
    """按固定前缀识别控制语句类别。

    :param str_stripped_code: 已去除首尾空白的代码片段。
    :return: 控制语句类别；未命中时返回 None。
    """

    # tuple_ordered_kinds 保持旧 special_statement_kind 的分类优先级。
    tuple_ordered_kinds = (  # 先检查更常见的循环与分支前缀，再落到 return/try/catch/assert 这些单词型语句
        "for",  # 首先匹配循环入口，避免把 `for (...)` 继续当成普通关键字串
        "if",  # 条件分支排在前面，确保 `if (...)` 直接归入分支类别
        "while",  # 单独保留循环前缀，覆盖 `while (...)` 这一类条件循环
        "switch",  # 多路分支入口需要和普通标识符前缀区分开来
        "return",  # 返回语句属于无括号也可能成立的控制类关键字
        "try",  # 异常入口块需要在字符串前缀命中时优先返回 try
        "catch",  # 异常捕获分支依靠这个关键字进入专门分类
        "assert",  # 断言语句单独分类，便于规则识别保护性检查
    )

    # 依次检查控制语句前缀。
    for str_kind in tuple_ordered_kinds:

        # 命中前缀时返回该类别。
        if str_stripped_code.startswith(str_kind):

            # 返回匹配到的控制语句类型。
            return str_kind

    # 未命中任何控制语句。
    return None

# _statement_kind_for_code 组合特殊语句、声明和赋值分类。
def _statement_kind_for_code(str_code_part: str) -> str:
    """为有效代码行选择最终语句分类。

    :param str_code_part: 去注释后的有效代码片段。
    :return: statement_infos 使用的语句分类。
    """

    # str_special_kind 优先保留 pragma、控制语句和函数调用等分类。
    str_special_kind = special_statement_kind(str_code_part)  # 特殊语句分类

    # 特殊语句命中时直接返回。
    if str_special_kind is not None:

        # 返回特殊语句分类。
        return str_special_kind

    # 局部声明优先于普通赋值。
    if is_local_declaration(str_code_part):

        # 返回局部声明分类。
        return "declaration"

    # 普通赋值单独分类。
    if is_assignment(str_code_part):

        # 返回赋值语句分类。
        return "assignment"

    # 其他有效代码按普通 code 分类。
    return "code"

# _advance_function_signature 推进函数体外签名识别。
def _advance_function_signature(obj_state: FunctionParseState, int_line_number: int, str_code_part: str) -> None:
    """在函数体外积累并解析函数签名候选。

    :param obj_state: 函数解析状态，会在函数内更新。
    :param int_line_number: 当前行号，按一基计数。
    :param str_code_part: 当前行去注释后的代码片段。
    :return: 该函数只更新解析状态，不返回业务值。
    """

    # 已有候选签名时继续追加当前行。
    if obj_state.list_pending_signature_lines:

        # 追加当前签名片段。
        obj_state.list_pending_signature_lines.append(str_code_part)

    # 函数候选签名必须含左括号且不是控制流或预处理语句。
    elif _is_function_signature_start_candidate(str_code_part):

        # 保存新的签名候选起始行。
        obj_state.list_pending_signature_lines = [str_code_part]  # 新的函数签名候选行列表

        # 记录签名候选起始行号。
        obj_state.int_pending_start = int_line_number  # 函数签名候选起始行号

    # 没有候选签名时当前行处理结束。
    if not obj_state.list_pending_signature_lines:

        # 当前行未开启函数签名解析。
        return

    # str_joined_signature 合并当前积累的签名片段。
    str_joined_signature = " ".join(obj_state.list_pending_signature_lines)  # 合并后的签名候选

    # 声明候选以分号结束且没有函数体。
    if ";" in str_joined_signature and "{" not in str_joined_signature:

        # 尝试把候选作为函数声明记录。
        _finish_function_declaration(obj_state, int_line_number, str_joined_signature)

        # 声明候选处理完毕。
        return

    # 函数定义候选包含左花括号。
    if "{" in str_joined_signature:

        # 尝试进入函数体扫描。
        _start_function_body(obj_state, int_line_number, str_joined_signature)

        # 函数定义候选处理完毕。
        return

    # 长时间未闭合的候选签名视为普通代码，避免误积累。
    if int_line_number - obj_state.int_pending_start > 8:

        # 清空过长签名候选。
        obj_state.list_pending_signature_lines = []  # 清空过长函数签名候选

# _advance_function_body 推进函数体内花括号深度。
def _advance_function_body(obj_state: FunctionParseState, int_line_number: int, str_code_part: str) -> None:
    """更新当前函数体花括号深度并在闭合时记录函数信息。

    :param obj_state: 函数解析状态，会在函数内更新。
    :param int_line_number: 当前行号，按一基计数。
    :param str_code_part: 当前行去注释后的代码片段。
    :return: 该函数只更新解析状态，不返回业务值。
    """

    # 根据当前行花括号更新函数体深度。
    obj_state.int_brace_depth += str_code_part.count("{") - str_code_part.count("}")  # 合并当前行后的活动函数体深度

    # 函数体尚未闭合时继续等待后续行。
    if obj_state.int_brace_depth > 0:

        # 当前函数仍在扫描中。
        return

    # function_info_function_info 保存闭合函数的边界信息。
    function_info_function_info: FunctionInfo = FunctionInfo(  # 函数体闭合后立刻固化边界，供后续命名和注释规则复用
        name=obj_state.str_name,  # 闭合函数沿用签名解析出的函数名
        start_line=obj_state.int_function_start,  # 闭合函数体的起始行
        end_line=int_line_number,  # 当前行就是闭合函数体的结束行

        # 签名边界与签名文本字段。
        signature_start_line=obj_state.int_signature_start,  # 多行签名在源码中的真实起点
        signature=obj_state.str_signature_text,  # 保留闭合函数的完整签名文本

        # 参数与返回类型字段。
        params=obj_state.tuple_params,  # 命名规则复用的参数名元组
        return_type=obj_state.str_return_type,  # 报告展示使用的返回类型文本
        is_declaration=False,  # 当前记录来自函数定义而不是函数声明
    )

    # 记录完整函数边界。
    obj_state.list_functions.append(function_info_function_info)

    # 重置函数体内状态。
    _reset_active_function(obj_state)

# _finish_function_declaration 记录无函数体声明。
def _finish_function_declaration(
    obj_state: FunctionParseState,
    int_line_number: int,
    str_joined_signature: str,
) -> None:
    """尝试将当前候选签名记录为函数声明。

    :param obj_state: 函数解析状态，会在函数内更新。
    :param int_line_number: 当前行号，按一基计数。
    :param str_joined_signature: 合并后的签名候选文本。
    :return: 该函数只更新解析状态，不返回业务值。
    """

    # 函数声明需要先通过轻量签名识别。
    if is_function_signature(str_joined_signature):

        # tuple_parsed_signature 保存函数名、参数和返回类型。
        tuple_parsed_signature = _parse_function_signature(str_joined_signature)  # 解析后的函数签名

        # 成功解析时记录声明。
        if tuple_parsed_signature is not None:

            # 拆包函数声明字段以保留 FunctionInfo 的既有字段顺序。
            str_name, tuple_params, str_return_type = tuple_parsed_signature  # 函数声明名称、参数和返回类型

            # function_info_function_info 保存无函数体声明的边界信息。
            function_info_function_info: FunctionInfo = FunctionInfo(  # 无函数体声明也要记录边界，供签名相关规则复用
                name=str_name,  # 声明记录沿用签名解析出的函数名
                start_line=int_line_number,  # 声明记录的起始行等于当前声明结束行
                end_line=int_line_number,  # 声明记录的结束行同样是当前行

                # 声明签名边界与签名文本字段。
                signature_start_line=obj_state.int_pending_start,  # 多行声明的真实起始行
                signature=str_joined_signature,  # 保留声明原文合并后的签名文本

                # 声明记录复用的接口字段。
                params=tuple_params,  # 声明记录复用的参数名元组
                return_type=str_return_type,  # 声明记录复用的返回类型文本
                is_declaration=True,  # 当前记录来自函数声明
            )

            # 记录函数声明边界。
            obj_state.list_functions.append(function_info_function_info)

    # 声明候选处理后清空待解析签名。
    obj_state.list_pending_signature_lines = []  # 声明候选消费后的空签名缓冲

# _start_function_body 识别函数定义并初始化函数体状态。
def _start_function_body(
    obj_state: FunctionParseState,
    int_line_number: int,
    str_joined_signature: str,
) -> None:
    """尝试从签名候选进入函数体扫描状态。

    :param obj_state: 函数解析状态，会在函数内更新。
    :param int_line_number: 当前行号，按一基计数。
    :param str_joined_signature: 合并后的签名候选文本。
    :return: 该函数只更新解析状态，不返回业务值。
    """

    # str_signature_prefix 只保留函数体左花括号之前的签名。
    str_signature_prefix = str_joined_signature.split("{", 1)[0] + "{"  # 带左花括号的签名前缀

    # 非函数签名候选超过短阈值后清理。
    if not is_function_signature(str_signature_prefix):

        # 候选积累超过 6 行后避免误判拖延。
        if int_line_number - obj_state.int_pending_start > 6:

            # 清空无效签名候选。
            obj_state.list_pending_signature_lines = []  # 清空无效函数签名候选

        # 当前候选无法进入函数体。
        return

    # 尝试把签名前缀解析成函数名、参数与返回类型三元组。
    tuple_parsed_signature = _parse_function_signature(str_signature_prefix)  # 解析后的函数定义签名

    # 签名解析失败时清空候选并返回。
    if tuple_parsed_signature is None:

        # 清空无法解析的签名候选。
        obj_state.list_pending_signature_lines = []  # 清空解析失败的函数签名候选

        # 当前候选处理结束。
        return

    # str_name、tuple_params、str_return_type 对应当前函数字段。
    str_name, tuple_params, str_return_type = tuple_parsed_signature  # 当前函数组成字段

    # 初始化函数体扫描状态。
    _set_active_function(
        obj_state,
        int_line_number,
        str_joined_signature,
        str_name,
        tuple_params,
        str_return_type,
    )

    # 单行函数体可能已经闭合。
    if obj_state.int_brace_depth <= 0:

        # function_info_function_info 保存单行函数的完整边界信息。
        function_info_function_info: FunctionInfo = FunctionInfo(  # 单行函数在同一行闭合时同样要即时落盘边界信息
            name=obj_state.str_name,  # 单行函数沿用当前活动函数名
            start_line=int_line_number,  # 单行函数定义的起点就是当前行
            end_line=int_line_number,  # 单行函数定义的终点同样是当前行

            # 单行函数的签名边界与签名文本字段。
            signature_start_line=obj_state.int_signature_start,  # 单行函数候选在源码中的签名起点
            signature=obj_state.str_signature_text,  # 单行函数沿用当前活动签名文本

            # 单行函数的接口字段。
            params=obj_state.tuple_params,  # 单行函数沿用当前活动参数名元组
            return_type=obj_state.str_return_type,  # 单行函数沿用当前活动返回类型文本
            is_declaration=False,  # 当前记录来自单行函数定义
        )

        # 直接记录当前单行函数。
        obj_state.list_functions.append(function_info_function_info)

        # 单行函数已经落盘，立即清空活动函数状态。
        _reset_active_function(obj_state)

    # 当前签名候选已被消费。
    obj_state.list_pending_signature_lines = []  # 函数定义候选消费后的空签名缓冲

# _is_function_signature_start_candidate 判断行是否可能开启函数签名。
def _is_function_signature_start_candidate(str_code_part: str) -> bool:
    """判断当前代码行是否可能是函数签名起点。

    :param str_code_part: 去注释后的代码片段。
    :return: 含左括号且不是控制流或预处理语句时返回 True。
    """

    # bool_is_candidate 汇总函数签名起始候选条件。
    tuple_excluded_prefixes = ("if", "for", "while", "switch", "return", "#")  # 不应当作函数签名起点的前缀集合

    # 含左括号且不以控制流或预处理前缀开头时，才值得当作签名候选。
    bool_is_candidate = "(" in str_code_part and not str_code_part.startswith(tuple_excluded_prefixes)  # 是否可能开启函数签名

    # 返回候选判断结果。
    return bool_is_candidate

# _set_active_function 初始化当前函数体状态。
def _set_active_function(
    obj_state: FunctionParseState,
    int_line_number: int,
    str_signature_text: str,
    str_name: str,
    tuple_params: tuple[str, ...],
    str_return_type: str,
) -> None:
    """把解析状态切换到函数体内部。

    :param obj_state: 函数解析状态，会在函数内更新。
    :param int_line_number: 当前行号，按一基计数。
    :param str_signature_text: 合并后的函数签名文本。
    :param str_name: 函数名。
    :param tuple_params: 参数名元组。
    :param str_return_type: 返回类型文本。
    :return: 该函数只更新解析状态，不返回业务值。
    """

    # 标记解析器已经进入函数体。
    obj_state.bool_in_function = True  # 函数解析器进入函数体

    # 记录活动函数定义体的起始行。
    obj_state.int_function_start = int_line_number  # 活动函数定义体的起始行

    # 记录候选签名真正开始的源码行号。
    obj_state.int_signature_start = obj_state.int_pending_start  # 多行签名真正开始的源码行

    # 保存进入函数体时对应的合并签名文本。
    obj_state.str_signature_text = str_signature_text  # 当前活动函数的合并签名文本

    # 根据签名行中的花括号初始化函数体深度。
    obj_state.int_brace_depth = str_signature_text.count("{") - str_signature_text.count("}")  # 由签名行初始化的函数体深度

    # 保存当前活动函数的名称。
    obj_state.str_name = str_name  # 当前活动函数的名称

    # 保存命名规则后续会复用的参数名元组。
    obj_state.tuple_params = tuple_params  # 当前活动函数的参数名元组

    # 保存签名与报告展示会复用的返回类型文本。
    obj_state.str_return_type = str_return_type  # 当前活动函数的返回类型文本

# _reset_active_function 清空当前函数体状态。
def _reset_active_function(obj_state: FunctionParseState) -> None:
    """清空已经闭合的当前函数体状态。

    :param obj_state: 函数解析状态，会在函数内更新。
    :return: 该函数只更新解析状态，不返回业务值。
    """

    # 标记解析器回到函数体外。
    obj_state.bool_in_function = False  # 函数解析器回到函数体外

    # 清空刚刚闭合函数遗留的名称字段。
    obj_state.str_name = ""  # 清空活动函数名称

    # 清空刚刚闭合函数遗留的参数字段。
    obj_state.tuple_params = ()  # 清空活动函数参数名元组

    # 清空刚刚闭合函数遗留的返回类型字段。
    obj_state.str_return_type = ""  # 清空活动函数返回类型文本

# _normalize_parameter_token 清理单个参数声明。
def _normalize_parameter_token(str_raw_param: str) -> str:
    """去掉默认值并拆开指针、引用符号。

    :param str_raw_param: 单个参数声明片段。
    :return: 便于按空白拆分的参数声明文本。
    """

    # str_token 去掉默认值右侧内容。
    str_token = str_raw_param.strip().split("=")[0].strip()  # 去默认值后的参数声明

    # str_token 拆开引用符号。
    str_token = str_token.replace("&", " & ")  # 拆开引用符号的参数声明

    # str_token 拆开指针符号。
    str_token = str_token.replace("*", " * ")  # 拆开指针符号的参数声明

    # 返回便于后续按空白拆分的参数声明。
    return str_token

# _parameter_token_words 去掉修饰符后返回参数 token。
def _parameter_token_words(str_parameter_token: str) -> list[str]:
    """返回参数声明中排除修饰符后的 token 列表。

    :param str_parameter_token: 已拆开指针和引用符号的参数声明。
    :return: 排除 const、volatile、& 和 * 后的 token 列表。
    """

    # 先按空白拆出参数声明中的原始 token。
    list_parameter_words = re.split(r"\s+", str_parameter_token)  # 参数声明原始 token 列表

    # 过滤修饰符后保留真正参与参数名提取的 token。
    set_ignored_parameter_tokens = {"const", "volatile", "&", "*"}  # 参数 token 过滤白名单

    # 先去掉空字符串，避免后续白名单过滤处理无效 token。
    list_nonempty_words = [str_part for str_part in list_parameter_words if str_part]  # 去掉空字符串后的参数 token 列表

    # 过滤修饰符和指针符号后，保留参数名提取真正需要的 token。
    list_words = [str_part for str_part in list_nonempty_words if str_part not in set_ignored_parameter_tokens]  # 参数声明有效 token 列表

    # 返回有效 token 列表。
    return list_words

# _array_name_from_candidate 去掉参数名候选的数组后缀。
def _array_name_from_candidate(str_candidate_name: str) -> str:
    """从参数名候选中剥离数组声明后缀。

    :param str_candidate_name: 参数名候选 token。
    :return: 去掉数组括号后的参数名候选。
    """

    # list_array_names 捕获数组声明中的基础名称。
    list_array_names = re.findall(r"([A-Za-z_]\w*)\s*\[", str_candidate_name)  # 数组参数名匹配结果

    # 数组候选命中时只返回基础名称。
    if list_array_names:

        # 返回数组参数的基础名称。
        return list_array_names[0]

    # 非数组参数保持原候选名称。
    return str_candidate_name

# _advance_multiline_statement 推进多行语句检测状态。
def _advance_multiline_statement(
    obj_state: MultilineStatementState,
    list_findings: list[tuple[int, str]],
    int_line_number: int,
    str_code_part: str,
) -> None:
    """推进一行代码在多行语句检测器中的状态。

    :param obj_state: 多行语句扫描状态，会在函数内更新。
    :param list_findings: 多行语句发现结果列表，会在函数内追加。
    :param int_line_number: 当前行号，按一基计数。
    :param str_code_part: 当前行去注释后的代码片段。
    :return: 该函数只更新扫描状态，不返回业务值。
    """

    # 未积累状态下尝试开启多行语句。
    if not obj_state.bool_accumulating:

        # bool_started 表示当前行是否开启了新的多行语句。
        bool_started = _try_start_multiline_statement(obj_state, int_line_number, str_code_part)  # 是否开启多行语句

        # 开启后当前行处理结束。
        if bool_started:

            # 等待后续行继续积累。
            return

    # 已在积累状态时追加当前行。
    if obj_state.bool_accumulating:

        # 追加当前代码片段。
        obj_state.list_buffer.append(str_code_part)

        # str_joined_statement 保存合并后的多行语句文本。
        str_joined_statement = " ".join(obj_state.list_buffer)  # 合并后的多行语句

        # 语句闭合时记录多行发现。
        if ";" in str_joined_statement or "{" in str_joined_statement or ")" in str_code_part:

            # 起始行和结束行不同才记录为多行语句。
            if int_line_number > obj_state.int_start_line:

                # 保存多行语句起点和合并文本。
                list_findings.append((obj_state.int_start_line, str_joined_statement))

            # 重置积累状态。
            _reset_multiline_statement(obj_state)

        # 过长未闭合语句同样记录并重置，避免漏报。
        elif int_line_number - obj_state.int_start_line > 8:

            # 记录超过阈值仍未闭合的多行语句，避免后续规则漏报。
            list_findings.append((obj_state.int_start_line, str_joined_statement))

            # 记录完成后立即清空当前多行语句积累状态。
            _reset_multiline_statement(obj_state)

# _try_start_multiline_statement 判断当前行是否开启多行语句。
def _try_start_multiline_statement(
    obj_state: MultilineStatementState,
    int_line_number: int,
    str_code_part: str,
) -> bool:
    """尝试用当前行开启一个多行语句候选。

    :param obj_state: 多行语句扫描状态，会在函数内更新。
    :param int_line_number: 当前行号，按一基计数。
    :param str_code_part: 当前行去注释后的代码片段。
    :return: 成功开启多行语句时返回 True。
    """

    # bool_call_like_start 判断函数调用或签名是否跨行。
    tuple_control_prefixes = ("if", "for", "while", "switch")  # 不应被当作跨行调用候选的控制流前缀

    # 先判断当前行是否存在尚未闭合的左括号。
    bool_has_unclosed_parenthesis = "(" in str_code_part and ")" not in str_code_part  # 当前行是否带有未闭合左括号

    # 当前行出现未闭合左括号且不是控制流头时，可能正在开启跨行调用或跨行签名。
    bool_call_like_start = bool_has_unclosed_parenthesis and not str_code_part.startswith(tuple_control_prefixes)  # 是否为调用或签名式多行起点

    # bool_declaration_start 判断局部声明是否跨行。
    bool_declaration_start = is_local_declaration(str_code_part) and not str_code_part.endswith(";")  # 是否为多行声明起点

    # 当前行未开启多行语句。
    if not bool_call_like_start and not bool_declaration_start:

        # 保持未积累状态。
        return False

    # 初始化多行语句积累状态。
    obj_state.bool_accumulating = True  # 多行语句扫描器进入积累状态

    # 记录多行语句起始行。
    obj_state.int_start_line = int_line_number  # 多行语句起始行号

    # 当前行作为首个代码片段。
    obj_state.list_buffer = [str_code_part]  # 多行语句首行代码片段

    # 返回已开启状态。
    return True

# _reset_multiline_statement 清空多行语句积累状态。
def _reset_multiline_statement(obj_state: MultilineStatementState) -> None:
    """清空已经记录或放弃的多行语句状态。

    :param obj_state: 多行语句扫描状态，会在函数内更新。
    :return: 该函数只更新扫描状态，不返回业务值。
    """

    # 标记当前不再积累多行语句。
    obj_state.bool_accumulating = False  # 多行语句扫描器离开积累状态

    # 清空起始行号。
    obj_state.int_start_line = 0  # 清空多行语句起始行号

    # 清空当前多行语句缓冲。
    obj_state.list_buffer = []  # 清空多行语句代码片段
