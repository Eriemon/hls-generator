"""为 mock HLS 打印语句补齐固定的 `[HLS]` 可读前缀。"""

# 启用延迟注解，避免类型提示在导入阶段过早求值。
from __future__ import annotations

# 正则负责识别 FAIL/error 与 warning 词面，供打印级别推断复用。
import re

# 错误级打印需要命中的词面模式集合。
OBJ_FAIL_OR_ERROR_PATTERN = re.compile(r"\bFAIL\b|\berr(or)?\b", flags=re.IGNORECASE)  # 错误级 transcript 的词面匹配模式

# 警告级打印需要命中的词面模式集合。
OBJ_WARNING_PATTERN = re.compile(r"\bwarn(ing)?\b", flags=re.IGNORECASE)  # 警告级 transcript 的词面匹配模式

# 对整段 mock HLS 文本逐行补齐固定的 `[HLS]` 可读前缀。
def normalize_hls_print_prefixes(text: str) -> str:
    """对 mock HLS 文本中的打印语句补齐固定前缀。

    参数:
        text: 原始 mock HLS 文本，shape=scalar，dtype=str，unit=text。

    返回:
        已补齐 `[HLS]` 输出前缀的文本，shape=scalar，dtype=str，unit=text。
    """

    # 逐行重写打印语句，避免跨字符串误改普通 HLS 代码。
    list_lines = [rewrite_hls_print_line(str_line) for str_line in text.splitlines()]  # 逐行补齐打印前缀后的源码行列表

    # 把逐行结果重新拼回完整文本，并保留既有的末尾换行习惯。
    return "\n".join(list_lines) + "\n"

# 对单行 HLS 打印语句补齐 `> LEVEL: [HLS]` 固定前缀。
def rewrite_hls_print_line(line: str) -> str:
    """对单行 HLS 打印语句补齐 `[HLS]` 固定前缀。

    参数:
        line: 当前待检查的单行 HLS 文本，shape=scalar，dtype=str，unit=source line。

    返回:
        当前单行补齐打印前缀后的文本，shape=scalar，dtype=str，unit=source line。
    """

    # 已经带 `[HLS]` 前缀的打印行保持原样。
    if "[HLS]" in line:

        # 发现现成的 `[HLS]` 前缀后直接返回，避免重复注入级别标识。
        return line

    # printf/fprintf 族统一走字符串字面量起始位置补前缀的路径。
    if "printf(" in line or "fprintf(" in line:

        # 先推断当前 C 风格打印应使用的信息级别。
        str_prefix = print_level_prefix(line)  # 当前 C 风格打印行应补齐的级别前缀

        # 再只改写首个字符串字面量开头，保留后续格式化占位符和参数列表。
        return re.sub(r'(")(?!> (?:INFO|WARNING|ERR): \[HLS\])', rf"\1{str_prefix}", line, count=1)

    # C++ iostream 打印同样只在首个字符串字面量前补齐固定前缀。
    if "std::cout" in line or "std::cerr" in line or "std::clog" in line:

        # 先根据 `cout/cerr/clog` 的职责和词面内容推断当前流式输出级别。
        str_prefix = print_level_prefix(line)  # 当前流式输出语句应补齐的级别前缀

        # 再只改写首个字符串字面量开头，避免误伤后续插入流表达式。
        return re.sub(r'(")(?!> (?:INFO|WARNING|ERR): \[HLS\])', rf"\1{str_prefix}", line, count=1)

    # 非打印语句保持原样。
    return line

# 根据输出流和词面语义推断当前打印应使用的固定级别前缀。
def print_level_prefix(line: str) -> str:
    """根据当前打印行推断固定级别前缀。

    参数:
        line: 当前待推断级别的单行 HLS 文本，shape=scalar，dtype=str，unit=source line。

    返回:
        应写入字符串字面量开头的固定前缀，shape=scalar，dtype=str，unit=print prefix。
    """

    # stderr、std::cerr 或显式 FAIL/error 词面统一推断成错误级输出。
    if "stderr" in line or "std::cerr" in line or OBJ_FAIL_OR_ERROR_PATTERN.search(line):

        # 错误级打印必须使用 `> ERR: [HLS]` 前缀。
        return "> ERR: [HLS] "

    # std::clog 或 warning 词面统一推断成警告级输出。
    if "std::clog" in line or OBJ_WARNING_PATTERN.search(line):

        # 警告级打印统一使用 `> WARNING: [HLS]` 前缀。
        return "> WARNING: [HLS] "

    # 其余打印默认回落到信息级输出。
    return "> INFO: [HLS] "
