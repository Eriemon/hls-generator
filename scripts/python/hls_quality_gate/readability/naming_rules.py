"""检查 HLS C/C++ 标识符的基础可读性与 typed-prefix 契约。"""

# 启用延迟注解，避免运行期提前解析前向引用。
from __future__ import annotations

# 正则用于识别 snake_case、常量形态、类型 token 和数组/指针声明。
import re
from pathlib import Path

# 轻量 C/C++ 词法工具负责抽取函数、声明和赋值目标。
from .cpp_lexer import (
    code_part,
    extract_assignment_target,
    extract_identifier_from_declaration,
    is_assignment,
    is_local_declaration,
    parse_functions,
)

# profile 配置承载 typed-prefix 与 custom prefix 词表。
from .profiles import HlsProfileConfig

# 报告对象维持 HLS readability gate 的稳定 JSON 契约。
from .report import HlsGateIssue, make_issue

# 把空格分隔的词表文本规整成集合，避免多行元素字面量反复触发 current-project 赋值门禁。
def _word_set(str_words: str) -> set[str]:
    """把空格分隔的词表文本转换成去空白集合。

    参数:
        str_words: 使用空格分隔的词表文本，dtype=str，unit=word list text。

    返回:
        去掉空白后的词项集合，dtype=set[str]，unit=word set。
    """

    # 逐词拆分后直接转成集合，便于后续做高频成员判断。
    set_words = {str_word for str_word in str_words.split() if str_word}  # 归一化后的词项集合

    # 返回稳定可复用的词项集合。
    return set_words

# 这些短名无法说明 HLS 数据路径责任，除非命中协议字段或索引豁免。
VAGUE_NAMES = _word_set("data info temp tmp result value obj buf buffer val x y")  # 会遮蔽端口、缓存和事务角色的空泛名称

# 这些短名来自协议字段、紧凑索引或测试入口约定，不进入 typed-prefix 强制面。
EXEMPT_NAMES = _word_set("i j k n m r c ii tb ap axis last idx len")  # 紧凑索引和协议字段豁免

# HG027 说明性提示只在这些场景出现，提示调用方不要做不安全自动改名。
MANUAL_RENAME_BOUNDARY_KINDS = {"parameter", "assignment_target"}  # 需要手工处理的命名边界类别

# 自定义类型前缀推断时要忽略这些内置或修饰性 token。
IGNORED_TYPE_TOKENS = _word_set(  # 不适合作为 custom prefix 的 C/C++ 基础 token
    "const volatile static register constexpr "
    "signed unsigned short long int char bool float double void "
    "struct class enum typename"
)

# typed-prefix 强制词表统一从这里复用，避免后续规则和 rewrite plan 各自漂移。
STANDARD_TYPED_FAMILIES = _word_set("bool int uint float double fixed ufixed ptr arr stream axis")  # HLS 内建 typed-prefix family 集合

# 目录级入口负责把签名、局部声明与赋值目标三类命名检查串成一次扫描。
def check_naming_rules(root: Path, path: Path, config: HlsProfileConfig) -> list[HlsGateIssue]:
    """检查单个 HLS 源文件中的函数、参数、局部声明和赋值目标命名。

    参数:
        root: HLS 可读性门禁扫描根目录，用于生成相对报告路径。
        path: 当前正在检查的 HLS 源文件路径。
        config: HLS profile 配置，承载 typed-prefix 与 comment-quality 相关词表。

    返回:
        当前文件命名规则发现的问题列表。
    """

    # 报告路径统一使用 POSIX 分隔符，便于跨平台比较。
    str_rel_path = path.relative_to(root).as_posix()  # 当前文件相对扫描根目录的报告路径

    # 源码按行读取，后续参数、声明与赋值都按一基行号定位。
    list_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()  # HLS 源码物理行

    # 命名诊断列表按源码出现顺序追加，保持报告稳定。
    list_issues: list[HlsGateIssue] = []  # 当前文件命名问题列表

    # 轻量函数解析结果同时提供参数名、返回类型与签名文本。
    list_functions = parse_functions(list_lines)  # HLS 函数签名解析结果

    # 先检查所有函数签名，避免公共接口问题被局部变量噪声淹没。
    list_issues.extend(_function_signature_issues(str_rel_path, list_functions, config))

    # 再逐个函数构造局部符号表，检查声明与赋值目标的 typed-prefix。
    for function_info in list_functions:

        # 纯声明没有函数体，不参与局部赋值目标扫描。
        if function_info.is_declaration:

            # 纯声明没有函数体可扫，继续处理下一个函数对象。
            continue

        # 先准备当前函数的局部符号表，后续 assignment_target 会复用这里的 family 结果。
        dict_families: dict[str, str] = {}  # 当前函数可复用的名称到类型族映射

        # 逐个参数规格回填 family 信息，兼容 typed-prefix 规则对赋值目标的后续复用。
        for dict_parameter_spec in _parameter_specs_from_signature(function_info.signature, config):

            # 缺少参数名或 family 时，不把当前条目写入局部符号表。
            if not dict_parameter_spec["name"] or not dict_parameter_spec["family"]:

                # 当前参数规格无法提供稳定 family 证据，继续处理下一项。
                continue

            # 把当前参数的 family 写入局部符号表，供后续赋值目标推断复用。
            dict_families[str(dict_parameter_spec["name"])] = str(dict_parameter_spec["family"])  # 当前参数名到 family 的映射

        # 扫描当前函数体内部的声明和赋值目标。
        for int_line_number in range(function_info.start_line, function_info.end_line + 1):

            # 当前源码行先去掉注释，再决定是否是声明或赋值。
            str_code = code_part(list_lines[int_line_number - 1]).strip()  # 当前源码行的有效 C/C++ 片段

            # 这一行的通用检查上下文先打包好，避免声明/赋值两条分支各自重复堆长参数列表。
            tuple_line_context = (str_rel_path, int_line_number, str_code, config, dict_families)  # 当前行命名检查上下文

            # 空行和纯花括号不参与命名规则。
            if not str_code or str_code in {"{", "}"}:

                # 当前行不含可检查语义，继续扫描下一行。
                continue

            # 局部声明能提供最强类型证据，因此优先更新符号表。
            if is_local_declaration(str_code):

                # 当前局部声明要先独立收集 issue，再并回文件级结果。
                list_declaration_issues = _declaration_name_issues(*tuple_line_context)  # 声明路径问题列表

                # 把当前局部声明的问题并入文件级问题列表。
                list_issues.extend(list_declaration_issues)

                # 当前行已经按声明路径处理完成，不再进入赋值目标分支。
                continue

            # 赋值目标依赖现有符号表或右侧弱推断，覆盖循环内二次赋值场景。
            if is_assignment(str_code):

                # 当前赋值目标要先独立收集 issue，再并回文件级结果。
                list_assignment_issues = _assignment_name_issues(*tuple_line_context)  # 赋值路径问题列表

                # 把当前赋值目标的问题并入文件级问题列表。
                list_issues.extend(list_assignment_issues)

    # 返回当前文件累计的命名诊断。
    return list_issues

# _function_signature_issues 汇总函数名、参数名与 typed-prefix 诊断。
def _function_signature_issues(
    str_rel_path: str,
    list_functions: list[object],
    config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """检查全部函数签名中的函数名与参数 typed-prefix。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        list_functions: cpp_lexer.parse_functions 返回的函数描述对象列表。
        config: 当前 profile 的 typed-prefix 配置。

    返回:
        函数签名相关诊断列表。
    """

    # 函数签名诊断先按源码顺序累计。
    list_issues: list[HlsGateIssue] = []  # 函数签名诊断列表

    # 逐个函数检查名称和参数。
    for function_info in list_functions:

        # testbench main 保持 C/C++ 入口约定，不强制改名。
        if function_info.name == "main":

            # main 入口沿用 C/C++ 约定命名，继续检查下一个函数。
            continue

        # 函数名的 snake_case 与空泛命名仍沿用 HG014。
        list_issues.extend(_function_name_issues(str_rel_path, function_info))

        # 参数除了 HG014，还要进入 typed-prefix 三联门禁。
        for dict_parameter_spec in _parameter_specs_from_signature(function_info.signature, config):

            # 无法提取参数名时跳过当前参数片段，保持旧版轻量解析的容错边界。
            if not dict_parameter_spec["name"]:

                # 当前参数片段无法稳定定位名称，继续处理下一段签名文本。
                continue

            # 参数既要保留 HG014 基础命名检查，也要执行 typed-prefix 规则。
            list_issues.extend(
                _name_issues(
                    str_rel_path,
                    function_info.signature_start_line,
                    str(dict_parameter_spec["name"]),
                    "parameter",
                    function_info.signature,
                )
            )

            # 再对同一参数补 typed-prefix 三联门禁，避免公共接口绕过 HG025/HG026/HG027。
            list_issues.extend(
                _typed_prefix_issues(
                    str_rel_path,
                    function_info.signature_start_line,
                    str(dict_parameter_spec["name"]),
                    str(dict_parameter_spec["family"]),
                    "parameter",
                    function_info.signature,
                    config,
                )
            )

    # 返回函数签名相关诊断。
    return list_issues

# _declaration_name_issues 检查单个局部声明的名称与 typed-prefix。
def _declaration_name_issues(
    str_rel_path: str,
    int_line_number: int,
    str_code: str,
    config: HlsProfileConfig,
    dict_families: dict[str, str],
) -> list[HlsGateIssue]:
    """检查单条局部声明中的标识符命名。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的相对路径。
        int_line_number: 当前声明所在的第一行源代码行号。
        str_code: 去掉注释后的局部声明源码片段。
        config: 当前 profile 的 typed-prefix 配置。
        dict_families: 当前函数内可复用的名称到类型族映射。

    返回:
        当前声明触发的命名问题列表。
    """

    # 从声明文本中提取标识符；失败时直接按数据边界退出。
    str_name = extract_identifier_from_declaration(str_code)  # 局部声明里的标识符名称

    # 无法取到名称时说明当前行不适合继续做 typed-prefix 判断。
    if not str_name:

        # 无法提取名称时直接返回空问题列表。
        return []

    # 类型上下文只保留声明左侧，避免初始化表达式污染 family 推断。
    str_type_context = _declaration_type_context(str_code)  # 当前声明的类型上下文文本

    # 结合类型上下文和标识符名称推断 typed-prefix family。
    str_family = _inferred_family_from_text(str_type_context, str_name, config)  # 当前声明推断出的类型族

    # 当前声明要先判断常量还是普通局部变量，再统一复用 HG014 逻辑。
    str_kind = _declaration_kind(str_code)  # 当前声明对应的节点种类

    # 先执行通用命名可读性检查。
    list_issues = _name_issues(str_rel_path, int_line_number, str_name, str_kind, str_code)  # 当前声明触发的 HG014 问题列表

    # 先把 typed-prefix 请求压成短元组，避免当前行因上下文参数过长而损失可读性。
    tuple_prefix_request = (str_rel_path, int_line_number, str_name, str_family, str_kind, str_code)  # 声明 typed-prefix 请求

    # 先单独收集 typed-prefix 问题，再并入当前声明的问题列表。
    list_typed_prefix_issues = _typed_prefix_issues(*tuple_prefix_request, config)  # 声明 typed-prefix 问题

    # 把当前声明的 typed-prefix 问题合并回 HG014 主列表。
    list_issues.extend(list_typed_prefix_issues)

    # HLS 特定类型还要补充语义后缀检查。
    list_suffix_issues = _semantic_suffix_issues(str_rel_path, int_line_number, str_name, str_code)  # 当前声明触发的语义后缀问题列表

    # 把语义后缀问题并入当前声明的问题列表。
    list_issues.extend(list_suffix_issues)

    # 已成功推断 family 的局部变量需要写回符号表，供后续 assignment_target 复用。
    if str_family:

        # 记录局部变量对应的类型族映射。
        dict_families[str_name] = str_family  # 局部符号表中的类型族记录

    # 返回当前局部声明累计的问题列表。
    return list_issues

# _declaration_type_context 去掉局部声明右侧初始化表达式。
def _declaration_type_context(str_code: str) -> str:
    """返回局部声明可用于类型推断的上下文。

    参数:
        str_code: 去掉注释后的局部声明源码片段，dtype=str，unit=declaration text。

    返回:
        只保留声明左侧类型与名称部分的文本，dtype=str，unit=type context text。
    """

    # 先去掉尾部分号，统一后续分支处理的文本形态。
    str_context = str_code.rstrip(";").strip()  # 去掉尾部分号后的声明文本

    # 初始化表达式右侧不参与 family 推断，只保留左侧声明部分。
    if "=" in str_context:

        # 当前声明包含初始化表达式时，只保留等号左侧文本。
        str_context = str_context.split("=", 1)[0].rstrip()  # 仅保留声明左侧的类型上下文

    # 返回可供 family 推断复用的声明文本。
    return str_context

# _assignment_name_issues 检查单个赋值目标名称与 typed-prefix。
def _assignment_name_issues(
    str_rel_path: str,
    int_line_number: int,
    str_code: str,
    config: HlsProfileConfig,
    dict_families: dict[str, str],
) -> list[HlsGateIssue]:
    """检查单条赋值目标的命名。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的相对路径。
        int_line_number: 赋值语句所在的第一行源代码行号。
        str_code: 去掉注释后的赋值源码片段。
        config: 当前 profile 的 typed-prefix 配置。
        dict_families: 当前函数内可复用的名称到类型族映射。

    返回:
        当前赋值目标触发的命名问题列表。
    """

    # 从赋值语句中提取目标标识符；数组下标与成员访问会被词法器规整。
    str_name = extract_assignment_target(str_code)  # 赋值目标的标识符名称

    # 没有可见赋值目标时无需继续追踪 typed-prefix。
    if not str_name:

        # 当前赋值目标无法抽取时直接返回空问题列表。
        return []

    # 先尝试复用已知 family；缺失时再从右值表达式保守推断。
    str_family = dict_families.get(str_name) or _assignment_rhs_family(str_code, config)  # 赋值目标对应的类型族

    # 赋值目标这一路复用 HG014，但语义上是在检查“赋值后的名称仍然是否自解释”。
    list_issues = _name_issues(str_rel_path, int_line_number, str_name, "assignment_target", str_code)  # 赋值目标触发的 HG014 问题列表

    # 先把赋值路径的 typed-prefix 请求压成短元组，避免右侧实参列表把当前行拉得过长。
    tuple_prefix_request = (str_rel_path, int_line_number, str_name, str_family, "assignment_target", str_code)  # 赋值 typed-prefix 请求

    # 先单独收集 typed-prefix 问题，再并入赋值目标的问题列表。
    list_typed_prefix_issues = _typed_prefix_issues(*tuple_prefix_request, config)  # 赋值 typed-prefix 问题

    # 把赋值目标的 typed-prefix 问题合并回 HG014 主列表。
    list_issues.extend(list_typed_prefix_issues)

    # 若这是首次通过右值推断出 family，则写回局部符号表供后续复用。
    if str_family and str_name not in dict_families:

        # 把当前赋值目标的 family 写回局部符号表。
        dict_families[str_name] = str_family  # 赋值目标的类型族记录

    # 返回当前赋值目标累计的命名问题列表。
    return list_issues

# _typed_prefix_issues 负责 HG025/HG026/HG027 三联门禁。
def _typed_prefix_issues(
    str_rel_path: str,
    int_line: int,
    str_name: str,
    str_family: str,
    str_kind: str,
    str_code: str, config: HlsProfileConfig,
) -> list[HlsGateIssue]:
    """按 typed-prefix 约束检查单个标识符。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的相对路径。
        int_line: 标识符所在的第一行源代码行号。
        str_name: 当前 HLS 标识符名称。
        str_family: 当前名称推断出的类型族；空字符串表示无法可靠推断。
        str_kind: 标识符在源码里的节点种类。
        str_code: 用于问题报告的源码片段。
        config: 当前 profile 的 typed-prefix 配置。

    返回:
        当前标识符触发的 typed-prefix 问题列表。
    """

    # typed-prefix 关闭时不再生成 HG025/HG026/HG027 问题。
    if not config.require_typed_prefix:

        # 关闭 typed-prefix 规则时直接返回空问题列表。
        return []

    # 豁免名称、私有临时名和常量不参与 typed-prefix 强制校验。
    if str_name in EXEMPT_NAMES or str_name.startswith("_") or _is_upper_constant(str_name):

        # 豁免命名形态不再继续做 typed-prefix 判断。
        return []

    # AXIS 协议字段属于固定 payload 结构，不参与普通标识符前缀检查。
    if _is_axis_packet_field(str_code, str_name):

        # AXIS 协议字段直接跳过 typed-prefix 规则。
        return []

    # ref_ 作为主前缀已经被禁用，必须改成真正的类型前缀。
    if config.forbid_ref_primary_prefix and str_name.startswith("ref_"):

        # ref_ 作为主前缀时直接生成 HG025 与可选 HG027 问题。
        return _prefixed_boundary_issues(
            (
                "HG025",
                str_rel_path,
                int_line,
                str_name,
                str_kind,
                str_code,
                "明确禁止把 ref_ 作为主前缀；请改成真实 typed-prefix，必要时仅把 alias_ 作为次级语义词。",
                True,
            )
        )

    # alias_ 只能作为次级语义词，不能单独占据变量名前端。
    if config.allow_alias_secondary_token and str_name.startswith("alias_"):

        # alias_ 作为主前缀时同样视作 HG025 与可选 HG027 问题。
        return _prefixed_boundary_issues(
            (
                "HG025",
                str_rel_path,
                int_line,
                str_name,
                str_kind,
                str_code,
                "alias_ 只能作为次级语义词，变量名前端仍必须先出现真实 typed-prefix。",
                True,
            )
        )

    # 能可靠推断 family 时必须检查是否带了正确的 typed-prefix。
    if str_family:

        # 当前 family 对应的目标 typed-prefix 始终使用 `<family>_` 形态。
        str_expected_prefix = _typed_prefix_for_family(str_family)  # 当前名称应使用的 typed-prefix

        # 名称已经满足目标 typed-prefix 时，不再追加问题。
        if _name_matches_expected_prefix(str_name, str_expected_prefix):

            # typed-prefix 已经正确命中时直接返回空问题列表。
            return []

        # family 可证但前缀缺失时，直接报告 HG025，并按边界决定是否补 HG027。
        return _prefixed_boundary_issues(
            (
                "HG025",
                str_rel_path,
                int_line,
                str_name,
                str_kind,
                str_code,
                f"标识符 `{str_name}` 可推断为 `{str_family}`，必须改成以 `{str_expected_prefix}` 开头的 typed-prefix 命名。",
                _needs_manual_rename_boundary(str_kind, str_family),
            )
        )

    # family 无法可靠推断但名称已显式带有某个已知 typed-prefix 时，只给通过。
    if _name_uses_known_prefix(str_name, config):

        # 已显式暴露某个已知 typed-prefix 时不再追加 HG026。
        return []

    # 只能给出说明性提示时，统一落为 HG026 warning。
    return [
        make_issue(
            "HG026",
            "warning",
            str_rel_path,
            int_line,
            "当前标识符缺少可证明的 typed-prefix，且规则无法可靠推断类型；请通过显式类型、可读初始化或人工确认来补齐前缀语义。",
            detail=str_name,
            node_kind=str_kind,
            code_excerpt=str_code,
        )
    ]

# _prefixed_boundary_issues 统一生成 HG025 与可选 HG027 问题。
def _prefixed_boundary_issues(
    tuple_request: tuple[str, str, int, str, str, str, str, bool],
) -> list[HlsGateIssue]:
    """统一生成 HG025，并按需要补充 HG027。

    参数:
        tuple_request: 依次包含主规则、相对路径、行号、名称、节点种类、源码片段、提示消息和 manual-boundary 标志。

    返回:
        由 HG025 及可选 HG027 组成的问题列表。
    """

    # HG025 主问题先固定成单元素列表，后面再按需补 HG027。
    list_issues = [_primary_prefixed_boundary_issue(tuple_request)]  # HG025 主问题列表

    # public interface 或赋值边界需要额外提醒不能盲目自动改名。
    if tuple_request[7]:

        # 对外边界需要追加 HG027，提醒人工确认 alias 或 public interface 语义。
        list_issues.append(
            make_issue(
                "HG027",
                "warning",
                tuple_request[1],
                tuple_request[2],
                "当前名称位于不安全自动改名边界；请人工确认 public interface、赋值语义与 alias_ 暴露是否正确。",
                detail=tuple_request[3],
                node_kind=tuple_request[4],
                code_excerpt=tuple_request[5],
            )
        )

    # 返回组合后的 typed-prefix 问题列表。
    return list_issues

# _primary_prefixed_boundary_issue 抽出 HG025 主问题对象，避免调用点堆成超长表达式。
def _primary_prefixed_boundary_issue(
    tuple_request: tuple[str, str, int, str, str, str, str, bool],
) -> HlsGateIssue:
    """把 HG025 主问题构造成独立对象。

    参数:
        tuple_request: 依次包含主规则、相对路径、行号、名称、节点种类、源码片段、提示消息和 manual-boundary 标志。

    返回:
        HG025 主问题对象。
    """

    # 这里只消费 HG025 所需字段；manual-boundary 标志保留给调用方决定是否追加 HG027。
    return make_issue(
        tuple_request[0],
        "error",
        tuple_request[1],
        tuple_request[2],
        tuple_request[6],
        detail=tuple_request[3],
        node_kind=tuple_request[4],
        code_excerpt=tuple_request[5],
    )

# _parameter_specs_from_signature 把函数签名参数转成名称与类型族。
def _parameter_specs_from_signature(str_signature: str, config: HlsProfileConfig) -> list[dict[str, str]]:
    """从函数签名中提取参数名与 typed-prefix 类型族。

    参数:
        str_signature: 合并后的函数签名文本。
        config: 当前 profile 的 typed-prefix 配置。

    返回:
        每个参数对应一个 `{"name": ..., "family": ...}` 字典。
    """

    # 缺少参数边界时无法安全解析签名，直接返回空列表。
    if "(" not in str_signature or ")" not in str_signature:

        # 非法签名文本直接返回空参数列表。
        return []

    # 只保留函数签名圆括号内的参数片段。
    str_params_text = str_signature.split("(", 1)[1].rsplit(")", 1)[0]  # 参数列表文本

    # 空参数或显式 void 参数说明当前签名没有业务参数。
    if not str_params_text.strip() or str_params_text.strip() == "void":

        # 无参数签名直接返回空参数列表。
        return []

    # 参数结果按源码顺序累计，供后续签名检查与局部符号表复用。
    list_specs: list[dict[str, str]] = []  # 参数 name/family 列表

    # 逐段拆分参数文本，并分别抽取 name 与 family。
    for str_raw_param in _split_params(str_params_text):

        # 单个参数片段需要先稳定提取出参数名称。
        str_name = _name_from_parameter_text(str_raw_param)  # 当前参数片段解析出的名称

        # 提取失败时跳过当前参数片段，避免误报数据边界。
        if not str_name:

            # 当前参数片段无法可靠抽取名称，直接跳过。
            continue

        # 当前参数名称确定后，再从参数文本推断 typed-prefix family。
        list_specs.append(
            {
                "name": str_name,
                "family": _inferred_family_from_text(str_raw_param, str_name, config),
            }
        )

    # 返回当前函数签名解析出的全部参数规格。
    return list_specs

# _split_params 按顶层逗号拆分参数列表，避开模板和括号嵌套。
def _split_params(str_params_text: str) -> list[str]:
    """在忽略模板与括号嵌套的前提下按顶层逗号拆分参数。

    参数:
        str_params_text: 函数签名圆括号内部的参数文本。

    返回:
        按顶层逗号拆分后的参数片段列表。
    """

    # 参数片段需要按原始顺序累计，保证后续 issue 顺序稳定。
    list_parts: list[str] = []  # 参数片段列表

    # int_depth 记录模板和括号嵌套深度，避免误拆 `ap_axiu<...>` 之类文本。
    int_depth = 0  # 模板与括号的嵌套深度

    # int_start_index 表示当前参数片段的起始位置。
    int_start_index = 0  # 当前参数片段的起始下标

    # 逐字符扫描参数文本，识别顶层逗号边界。
    for int_index, str_char in enumerate(str_params_text):

        # 遇到左侧括号或模板界定符时增加嵌套深度。
        if str_char in "<([":

            # 当前字符打开了一个新的嵌套层级。
            int_depth += 1  # 模板与括号嵌套深度加一

        # 遇到右侧界定符时，退出一层嵌套。
        elif str_char in ">)]" and int_depth:

            # 当前字符关闭了一层模板或括号嵌套。
            int_depth -= 1  # 模板与括号嵌套深度减一

        # 只有顶层逗号才能切分出一个新的参数片段。
        elif str_char == "," and int_depth == 0:

            # 先收集当前顶层逗号左侧的参数片段。
            list_parts.append(str_params_text[int_start_index:int_index])

            # 下一个参数片段从逗号后一位重新开始。
            int_start_index = int_index + 1  # 下一段参数文本的起始下标

    # 最后一个参数片段同样需要收集到列表中。
    list_parts.append(str_params_text[int_start_index:])

    # 返回顶层逗号拆分后的全部参数片段。
    return list_parts

# _name_from_parameter_text 从单个参数片段中提取参数名。
def _name_from_parameter_text(str_raw_param: str) -> str:
    """从单个参数片段中提取参数名称。

    参数:
        str_raw_param: 单个参数声明的文本。

    返回:
        成功提取出的参数名；失败时返回空字符串。
    """

    # 默认值右侧不参与名称抽取，先保留左侧声明部分。
    str_candidate_text = str_raw_param.strip().split("=", 1)[0].strip()  # 去掉默认值后的参数文本

    # 把引用和指针符号转成空格，避免最后一个 token 被 `*` 或 `&` 污染。
    str_candidate_text = str_candidate_text.replace("&", " ").replace("*", " ")  # 去掉引用与指针符号

    # 这里缓存的是“末尾 token 抽名”专用的分词序列，不打算承载完整类型解析。
    list_words = [str_part for str_part in re.split(r"\s+", str_candidate_text) if str_part]  # 参数抽名用的 token 序列

    # 没有任何 token 时无法继续提取参数名。
    if not list_words:

        # 缺失 token 的参数片段直接返回空字符串。
        return ""

    # 最后一个 token 默认是参数名候选。
    str_candidate_name = list_words[-1]  # 参数名候选 token

    # 数组形态通常把 `name[...]` 粘在最后一个 token 上，需要先解出真正名称。
    list_array_names = re.findall(r"([A-Za-z_]\w*)\s*\[", str_candidate_name)  # 数组参数名匹配结果

    # 命中数组形态时直接返回数组名。
    if list_array_names:

        # 数组参数优先返回第一个合法匹配名称。
        return list_array_names[0]

    # 只有合法 C/C++ 标识符才能作为参数名返回。
    return str_candidate_name if re.fullmatch(r"[A-Za-z_]\w*", str_candidate_name) else ""

# _assignment_rhs_family 从赋值右侧做保守类型推断。
def _assignment_rhs_family(str_code: str, config: HlsProfileConfig) -> str:
    """从赋值右侧表达式中保守推断 typed-prefix family。

    参数:
        str_code: 去掉注释后的赋值语句文本。
        config: 当前 profile 的 typed-prefix 配置。

    返回:
        推断出的类型族；无法可靠推断时返回空字符串。
    """

    # 没有等号时说明当前语句不是标准赋值表达式。
    if "=" not in str_code:

        # 非赋值表达式不参与右值 family 推断。
        return ""

    # 先抽出右值文本，供后续正则判断复用。
    str_rhs = str_code.split("=", 1)[1].strip().rstrip(";")  # 赋值右侧表达式

    # 小写化后的右值文本用于大小写无关匹配。
    str_rhs_lowered = str_rhs.casefold()  # 小写化后的右值表达式

    # 布尔字面量、比较表达式与逻辑表达式优先映射到 bool family。
    if re.fullmatch(r"(?:true|false|\(.+\s*[=!]=\s*.+\)|.+&&.+|.+\|\|.+)", str_rhs_lowered):

        # 命中逻辑表达式后，直接返回 bool family。
        return "bool"

    # 浮点字面量需要根据 `f` 后缀区分 float 与 double。
    if re.fullmatch(r"-?\d+\.\d+(?:f)?", str_rhs_lowered):

        # 命中浮点字面量后，按后缀返回 float 或 double family。
        return "float" if str_rhs_lowered.endswith("f") else "double"

    # 纯整数与十六进制字面量统一归到 int family。
    if re.fullmatch(r"-?(?:0x[0-9a-f]+|\d+)", str_rhs_lowered):

        # 命中整数字面量后，直接返回 int family。
        return "int"

    # 其余右值表达式无法可靠推断 family，保留空字符串边界。
    return ""

# _inferred_family_from_text 按声明或参数文本推断 typed-prefix 类型族。
def _inferred_family_from_text(str_text: str, str_name: str, config: HlsProfileConfig) -> str:
    """从声明或参数文本中推断 typed-prefix family。

    参数:
        str_text: 与当前标识符相关的源码片段。
        str_name: 当前源码片段中的标识符名称。
        config: 当前 profile 的 typed-prefix 配置。

    返回:
        推断出的类型族；未知时返回空字符串。
    """

    # 所有不区分大小写的判断统一使用 casefold 文本。
    str_lowered_text = str_text.casefold()  # 小写化后的源码片段

    # hls::stream 先暴露存储或接口形态，再考虑 payload 类型。
    if "hls::stream<" in str_lowered_text:

        # hls::stream 明确映射到 stream family。
        return "stream"

    # AXIS packet 类型应统一映射到 axis_ typed-prefix。
    if _looks_like_axis_type(str_lowered_text):

        # AXIS packet 类型直接返回 axis family。
        return "axis"

    # 指针声明优先暴露 ptr_ 存储形态。
    if _declares_pointer(str_text, str_name):

        # 指针形态优先返回 ptr family。
        return "ptr"

    # 数组声明优先暴露 arr_ 存储形态。
    if _declares_array(str_text, str_name):

        # 数组形态优先返回 arr family。
        return "arr"

    # fixed 系列要先于普通整数/浮点分支命中，避免定点类型被稀释成更泛的 family。
    if "ap_ufixed<" in str_lowered_text or _contains_token(str_lowered_text, "ufixed"):

        # 这里保留的是无符号定点证据，避免它被普通 unsigned 分支吞掉。
        return "ufixed"

    # ap_fixed 也要在普通 signed/unsigned 推断前优先命中。
    if "ap_fixed<" in str_lowered_text or _contains_token(str_lowered_text, "fixed"):

        # 这里锁定的是有符号定点 family，不让后续整数规则覆盖掉它。
        return "fixed"

    # 布尔类型命中后不再继续尝试浮点或整数 family。
    if _contains_token(str_lowered_text, "bool"):

        # 看到 bool 关键字后立即结束，避免再被整数 family 误判。
        return "bool"

    # double 应比 float 更先判断，避免较宽松的浮点短语误吞宽精度类型。
    if _contains_token(str_lowered_text, "double"):

        # 宽精度浮点要优先落到 double，不能被更泛的 float 规则提前吸收。
        return "double"

    # 普通浮点最后落到 float family。
    if _contains_token(str_lowered_text, "float"):

        # 这里只剩常规浮点线索，因此统一收束到 float family。
        return "float"

    # unsigned-like 文本优先映射到 uint family。
    if _looks_like_unsigned_integer(str_lowered_text):

        # unsigned 类词形已经足够明确，后面不需要再试 signed 整数分支。
        return "uint"

    # signed integer 作为标准内建 family 的最后一层兜底。
    if _looks_like_signed_integer(str_lowered_text):

        # 走到这里说明只剩 signed 整数证据，最终落回 int family。
        return "int"

    # 标准 family 都无法命中时，再尝试 custom typedef 或 struct family。
    return _custom_type_family(str_text, str_name, config)

# _custom_type_family 从类型文本派生 custom prefix。
def _custom_type_family(str_text: str, str_name: str, config: HlsProfileConfig) -> str:
    """从 typedef 或 struct 文本中提取 custom typed-prefix family。

    参数:
        str_text: 与当前标识符相关的源码片段。
        str_name: 当前源码片段中的标识符名称。
        config: 当前 profile 的 custom prefix 配置。

    返回:
        custom typed-prefix 对应的类型族；失败时返回空字符串。
    """

    # 显式配置列出的 custom prefix 应优先命中，避免误判。
    for str_prefix in config.custom_type_prefixes:

        # 当前源码片段已显式写出 custom prefix 时直接复用。
        if re.search(rf"\b{re.escape(str_prefix)}(?:_t)?\b", str_text):

            # 命中显式 custom prefix 时直接返回该前缀。
            return str_prefix

    # 先去掉名称本身与数组维度，留下更接近类型定义的文本。
    str_type_text = re.sub(rf"\b{re.escape(str_name)}\b(?:\s*\[[^\]]*\])?", "", str_text, count=1).strip()  # 去掉名称后的类型文本

    # 这里移除的是声明层的 `&` / `*` 形态，避免 custom family 候选词把存储形态误当成类型名。
    str_type_text = str_type_text.replace("&", " ").replace("*", " ")  # 清掉引用与指针形态

    # custom family 只看模板外层的类型名，模板实参里的细节不应污染前缀推断。
    str_type_text = re.sub(r"<[^>]*>", "", str_type_text)  # 去掉模板实参文本

    # 再按空白拆成 token，准备从后向前寻找最接近变量名的类型词。
    list_tokens = [str_token for str_token in re.split(r"\s+", str_type_text) if str_token]  # 类型 token 列表

    # 从最靠近变量名的一侧回溯，优先选择最具体的 custom type token。
    for str_token in reversed(list_tokens):

        # 去掉命名空间前缀，只保留最终类型名词干。
        str_candidate = str_token.rsplit("::", 1)[-1]  # 去掉命名空间后的候选 token

        # `_t` 常见于 typedef 后缀，不应保留在 family 名称里。
        str_candidate = re.sub(r"_t$", "", str_candidate)  # 去掉 typedef 风格后缀

        # CamelCase 类型名先折成 snake_case，便于后续 family 比较。
        str_candidate = re.sub(r"(?<!^)(?=[A-Z])", "_", str_candidate).casefold()  # 先把 CamelCase 类型名折成可比较的 snake_case 词干

        # 过滤掉 family 中不允许出现的符号，只保留合法字符。
        str_candidate = re.sub(r"[^a-z0-9_]+", "_", str_candidate).strip("_")  # 归一化后的 family 候选词

        # 空 token 或内建类型关键字都不能作为 custom family。
        if not str_candidate or str_candidate in IGNORED_TYPE_TOKENS:

            # 无效候选词直接跳过，继续寻找下一项。
            continue

        # `ap_*` 内建 HLS 类型已由标准 family 规则覆盖，这里不再接管。
        if str_candidate.startswith(("ap_uint", "ap_int", "ap_fixed", "ap_ufixed")):

            # 内建 ap_* 类型不应降级成 custom family。
            continue

        # 第一个合法候选词即可作为 custom typed-prefix family。
        return str_candidate

    # 未找到任何可靠的 custom type 词干时返回空字符串。
    return ""

# _typed_prefix_for_family 把类型族转换成变量名前缀文本。
def _typed_prefix_for_family(str_family: str) -> str:
    """把类型族转换成变量名前缀。

    参数:
        str_family: 已推断出的类型族。

    返回:
        类型族对应的变量名前缀，始终带结尾下划线。
    """

    # 所有 typed-prefix 统一使用 `<family>_` 形式，custom family 也沿用同一规则。
    return f"{str_family.rstrip('_')}_"

# _name_matches_expected_prefix 判断名称是否满足期望 typed-prefix。
def _name_matches_expected_prefix(str_name: str, str_expected_prefix: str) -> bool:
    """判断名称是否已满足期望的 typed-prefix。

    参数:
        str_name: 待检查的 HLS 标识符名称。
        str_expected_prefix: 当前类型族要求的 typed-prefix。

    返回:
        名称已经使用期望前缀时返回 True。
    """

    # typed-prefix 需要出现在变量名前端；alias_ 可以继续跟在主前缀后面。
    return str_name.startswith(str_expected_prefix)

# _name_uses_known_prefix 判断名称是否已经带任一已知 typed-prefix。
def _name_uses_known_prefix(str_name: str, config: HlsProfileConfig) -> bool:
    """判断名称是否已经显式带有某个已知 typed-prefix。

    参数:
        str_name: 当前 HLS 标识符名称。
        config: 当前 profile 的 typed-prefix 配置。

    返回:
        当名称以前缀族或 custom prefix 开头时返回 True。
    """

    # 所有 built-in prefix 与 custom prefix 都要纳入显式前缀集合判断。
    tuple_prefixes = tuple(config.typed_prefix_families) + tuple(config.custom_type_prefixes)  # 已知 typed-prefix 与 custom prefix 集合

    # 只要名称带有 `<prefix>_` 形态，就认为已经显式暴露 typed-prefix。
    return any(str_name.startswith(f"{str_prefix.rstrip('_')}_") for str_prefix in tuple_prefixes)

# _needs_manual_rename_boundary 判断是否需要追加 HG027。
def _needs_manual_rename_boundary(str_kind: str, str_family: str) -> bool:
    """判断当前命名问题是否需要补充 HG027 的人工边界提示。

    参数:
        str_kind: 标识符在源码里的节点种类，例如 parameter 或 assignment_target。
        str_family: 当前名称推断出的类型族。

    返回:
        需要提示 public interface 或不安全自动改名边界时返回 True。
    """

    # public parameter 与 assignment target 直接暴露给外部语义，必须提醒人工确认。
    if str_kind in MANUAL_RENAME_BOUNDARY_KINDS:

        # 对外语义边界默认追加 HG027 警告。
        return True

    # custom family 可能绑定用户 typedef 语义，因此同样需要人工确认边界。
    return str_family not in STANDARD_TYPED_FAMILIES

# _declares_pointer 判断声明文本是否把名称声明为指针。
def _declares_pointer(str_text: str, str_name: str) -> bool:
    """判断类型文本是否把标识符声明为指针。

    参数:
        str_text: 声明、参数或其他带类型上下文的源码片段。
        str_name: 当前源码片段里的标识符名称。

    返回:
        命中 `*name` 或 `* name` 形态时返回 True。
    """

    # 指针形态必须出现在当前名称左侧，避免把乘法表达式误判成指针。
    return re.search(rf"\*\s*{re.escape(str_name)}\b", str_text) is not None

# _declares_array 判断声明文本是否把名称声明为数组。
def _declares_array(str_text: str, str_name: str) -> bool:
    """判断类型文本是否把标识符声明为数组。

    参数:
        str_text: 声明、参数或其他带类型上下文的源码片段。
        str_name: 当前源码片段里的标识符名称。

    返回:
        命中 `name[...]` 形态时返回 True。
    """

    # 数组维度必须紧跟名称出现，避免把下标读写表达式误判成声明。
    return re.search(rf"\b{re.escape(str_name)}\s*\[[^\]]*\]", str_text) is not None

# _looks_like_axis_type 识别 AXIS packet 类型。
def _looks_like_axis_type(str_lowered_text: str) -> bool:
    """判断类型文本是否像 AXIS token 或 packet。

    参数:
        str_lowered_text: 已统一成小写的源码片段。

    返回:
        命中 ap_axiu 或 axis 类型片段时返回 True。
    """

    # ap_axiu 和 axis 类型都表示需要 axis_ 前缀的流式 payload。
    return "ap_axiu" in str_lowered_text or "axis" in str_lowered_text

# _contains_token 统一执行基于单词边界的 token 判断。
def _contains_token(str_lowered_text: str, str_token: str) -> bool:
    """判断文本是否包含带单词边界的目标 token。

    参数:
        str_lowered_text: 已统一成小写的源码片段。
        str_token: 需要按完整 token 匹配的关键字。

    返回:
        当文本包含目标 token 时返回 True。
    """

    # 单词边界匹配可避免把关键字误命中到更长标识符中。
    return re.search(rf"\b{re.escape(str_token)}\b", str_lowered_text) is not None

# _looks_like_unsigned_integer 识别无符号整数类型。
def _looks_like_unsigned_integer(str_lowered_text: str) -> bool:
    """判断类型文本是否表达无符号整数家族。

    参数:
        str_lowered_text: 已统一成小写的源码片段。

    返回:
        命中 ap_uint、unsigned 或 uint*_t 等模式时返回 True。
    """

    # HLS 常见定宽无符号类型都应映射到 uint_。
    return (
        "ap_uint<" in str_lowered_text
        or "size_t" in str_lowered_text
        or _contains_token(str_lowered_text, "unsigned")
        or re.search(r"\buint(?:8|16|32|64)?_t\b", str_lowered_text) is not None
    )

# _looks_like_signed_integer 识别有符号整数类型。
def _looks_like_signed_integer(str_lowered_text: str) -> bool:
    """判断类型文本是否表达有符号整数家族。

    参数:
        str_lowered_text: 已统一成小写的源码片段。

    返回:
        命中 ap_int、int、short、long、char 等模式时返回 True。
    """

    # 无符号场景必须先于本函数判断，这里只处理其余整型家族。
    return (
        "ap_int<" in str_lowered_text
        or any(_contains_token(str_lowered_text, str_token) for str_token in ("int", "short", "long", "char"))
    )

# _function_name_issues 只处理函数名本身的结构与空泛语义。
def _function_name_issues(str_rel_path: str, function: object) -> list[HlsGateIssue]:
    """检查单个 HLS 函数名称问题。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的相对路径。
        function: `cpp_lexer.parse_functions` 返回的函数描述对象。

    返回:
        当前函数名称触发的问题列表。
    """

    # 问题列表按源码顺序累计，保证报告稳定。
    list_issues: list[HlsGateIssue] = []  # 函数名称问题列表

    # 非 snake_case 会降低 HLS pipeline/dataflow 阶段命名的一致性。
    if not _is_snake_case(function.name):

        # 记录函数名称的 snake_case 结构问题。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                function.signature_start_line,
                "HLS 函数名称应使用 snake_case，避免混用大小写导致可读性下降。",
                detail=function.name,
                node_kind="function",
                code_excerpt=function.signature,
            )
        )

    # 空泛函数名会掩盖 kernel、stage 或数据通路职责。
    if _is_vague_name(function.name):

        # 记录函数名称的业务语义过空问题。
        list_issues.append(
            make_issue(
                "HG014",
                "error",
                str_rel_path,
                function.signature_start_line,
                "HLS 函数名称过于空泛，必须显式暴露 kernel、stage 或数据通路职责。",
                detail=function.name,
                node_kind="function",
                code_excerpt=function.signature,
            )
        )

    # 返回当前函数名称累计的问题列表。
    return list_issues

# _name_issues 复用到参数、局部变量和赋值目标。
def _name_issues(str_rel_path: str, int_line: int, str_name: str, str_kind: str, str_code: str) -> list[HlsGateIssue]:
    """检查单个标识符的结构命名与业务语义。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的相对路径。
        int_line: 标识符所在的第一行源代码行号。
        str_name: 当前 HLS 标识符名称。
        str_kind: 标识符在源码里的节点种类，例如 parameter 或 local_identifier。
        str_code: 用于问题报告的源码片段。

    返回:
        当前标识符触发的 HG014 问题列表。
    """

    # 问题列表保持按源码顺序累计，便于报告稳定排序。
    list_issues: list[HlsGateIssue] = []  # 当前标识符的问题列表

    # 明确豁免名和私有临时名不参与 HG014 结构语义检查。
    if str_name in EXEMPT_NAMES or str_name.startswith("_"):

        # 豁免名称直接返回空问题列表。
        return list_issues

    # AXIS payload 固定字段属于协议定义，不走普通业务命名判断。
    if _is_axis_packet_field(str_code, str_name):

        # AXIS 协议字段直接跳过 HG014 普通命名规则。
        return list_issues

    # 普通标识符必须使用 snake_case；常量可以使用 UPPER_CASE。
    if not _is_snake_case(str_name) and not _is_upper_constant(str_name):

        # 记录标识符的命名结构问题。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                int_line,
                "HLS 标识符应使用 snake_case，常量允许使用 UPPER_CASE。",
                detail=str_name,
                node_kind=str_kind,
                code_excerpt=str_code,
            )
        )

    # 空泛名称会遮蔽端口、缓存、流或累加语义。
    if _is_vague_name(str_name):

        # 记录标识符业务语义过空的问题。
        list_issues.append(
            make_issue(
                "HG014",
                "error",
                str_rel_path,
                int_line,
                "HLS 标识符过于空泛，必须暴露端口、缓存、流、累加或业务语义。",
                detail=str_name,
                node_kind=str_kind,
                code_excerpt=str_code,
            )
        )

    # 返回当前标识符累计的 HG014 问题列表。
    return list_issues

# _semantic_suffix_issues 检查 HLS 类型和变量名是否互相印证。
def _semantic_suffix_issues(str_rel_path: str, int_line: int, str_name: str, str_code: str) -> list[HlsGateIssue]:
    """根据 HLS 类型片段检查名称是否携带协议或累加语义。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的相对路径。
        int_line: 当前声明所在的第一行源代码行号。
        str_name: 当前源码片段提取出的标识符名称。
        str_code: 去掉注释后的声明或参数源码片段。

    返回:
        当前名称触发的协议或累加语义问题列表。
    """

    # 这里缓存的是后缀规则复用的源码视图，后面会拿它分别匹配 stream、axis 和 accumulator 语义。
    str_lowered_code = str_code.casefold()  # 后缀规则复用的小写源码片段

    # 名称也要统一小写，避免 token 检查被大小写差异干扰。
    str_lowered_name = str_name.casefold()  # 小写化后的标识符名称

    # 问题列表按 stream / axis / accumulator 顺序累计。
    list_issues: list[HlsGateIssue] = []  # HLS 语义后缀问题列表

    # hls::stream 类型应显式暴露 FIFO、channel 或 stream 语义。
    if "hls::stream" in str_lowered_code and not _contains_any(str_lowered_name, ("stream", "channel", "fifo")):

        # 记录 stream 类型名称缺失通道语义的问题。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                int_line,
                "hls::stream 类型名称应显式包含 stream、channel 或 FIFO 语义。",
                detail=str_name,
                node_kind="stream_identifier",
                code_excerpt=str_code,
            )
        )

    # AXIS packet 名称应显式暴露 axis、word、packet 或 token 语义。
    if (
        "hls::stream" not in str_lowered_code
        and _looks_like_axis_type(str_lowered_code)
        and not _contains_any(
            str_lowered_name,
            ("axis", "word", "packet", "token", "pkt"),
        )
    ):

        # 记录 AXIS 类型名称缺失协议语义的问题。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                int_line,
                "AXIS token 名称应显式包含 axis、word、packet 或 token 协议语义。",
                detail=str_name,
                node_kind="axis_identifier",
                code_excerpt=str_code,
            )
        )

    # 累加类临时值应显式暴露 acc 或 sum 语义。
    if _looks_like_accumulator(str_lowered_code) and not _contains_any(str_lowered_name, ("acc", "sum")):

        # 记录累加变量名称缺失累加语义的问题。
        list_issues.append(
            make_issue(
                "HG014",
                "warning",
                str_rel_path,
                int_line,
                "累加变量名称应显式包含 acc 或 sum 语义。",
                detail=str_name,
                node_kind="accumulator_identifier",
                code_excerpt=str_code,
            )
        )

    # 返回当前名称累计的语义后缀问题列表。
    return list_issues

# _contains_any 封装名称 token 命中判断。
def _contains_any(str_text: str, tuple_tokens: tuple[str, ...]) -> bool:
    """判断文本中是否包含任一候选语义 token。

    参数:
        str_text: 已归一化大小写的待检查文本。
        tuple_tokens: 候选语义 token 集合。

    返回:
        命中任一 token 时返回 True。
    """

    # 逐项匹配候选 token，保持调用处条件表达式简洁。
    return any(str_token in str_text for str_token in tuple_tokens)

# _looks_like_accumulator 识别累加器相关声明。
def _looks_like_accumulator(str_code: str) -> bool:
    """判断声明源码是否包含累加器语义。

    参数:
        str_code: 已归一化大小写的声明源码片段。

    返回:
        出现 sum、acc、accum 或 accumulator 词根时返回 True。
    """

    # 使用词边界避免把普通长词中的 acc 误判为累加器。
    return re.search(r"\b(?:sum|acc|accum|accumulator)\b", str_code) is not None

# _declaration_kind 区分常量声明和普通局部标识符。
def _declaration_kind(str_code: str) -> str:
    """根据声明文本判断节点种类。

    参数:
        str_code: 去掉注释后的声明源码片段。

    返回:
        `constant` 或 `local_identifier`。
    """

    # const 前缀或全大写赋值形态都更接近常量语义。
    if str_code.strip().startswith("const ") or re.match(r"^\s*[A-Z0-9_]+\s*=", str_code):

        # 当前声明应按 constant 节点种类处理。
        return "constant"

    # 其余声明统一按普通局部标识符处理。
    return "local_identifier"

# _is_snake_case 检查普通 HLS 标识符结构。
def _is_snake_case(str_name: str) -> bool:
    """判断名称是否符合 snake_case。

    参数:
        str_name: 待检查的标识符名称。

    返回:
        小写字母开头并只包含小写字母、数字和下划线时返回 True。
    """

    # re.fullmatch 保证整个名称都满足 snake_case 结构。
    return re.fullmatch(r"^[a-z][a-z0-9_]*$", str_name) is not None

# _is_upper_constant 检查常量式标识符结构。
def _is_upper_constant(str_name: str) -> bool:
    """判断名称是否符合 UPPER_CASE 常量约定。

    参数:
        str_name: 待检查的标识符名称。

    返回:
        全大写字母、数字和下划线组成时返回 True。
    """

    # re.fullmatch 保证整个名称都是常量式结构。
    return re.fullmatch(r"^[A-Z][A-Z0-9_]*$", str_name) is not None

# _is_vague_name 检查名称是否只有空泛占位含义。
def _is_vague_name(str_name: str) -> bool:
    """判断标识符名称是否属于空泛占位词。

    参数:
        str_name: 当前标识符名称。

    返回:
        当名称只表达模板化占位语义时返回 True。
    """

    # 先统一成小写并去掉边界下划线，便于比较。
    str_lowered_name = str_name.casefold().strip("_")  # 归一化后的标识符名称

    # 明确列出的空泛词应直接视作 vague name。
    if str_lowered_name in VAGUE_NAMES:

        # 命中空泛词表时直接返回 True。
        return True

    # tmp1、value2 这类数字后缀形式仍然属于空泛占位命名。
    return re.fullmatch(r"(?:tmp|temp|data|result|value|buf)\d*", str_lowered_name) is not None

# _is_axis_packet_field 保留 AXIS data 字段的协议豁免。
def _is_axis_packet_field(str_code: str, str_name: str) -> bool:
    """判断当前声明是否属于 AXIS packet 的协议字段。

    参数:
        str_code: 去掉注释后的源码片段。
        str_name: 当前正在判断的字段名称。

    返回:
        当声明形态匹配 AXIS payload 或控制字段时返回 True。
    """

    # 只允许 AXIS 常见协议字段走豁免路径，其他名称仍按普通业务变量处理。
    if str_name not in {"data", "keep", "strb", "last"}:

        # 非 AXIS 固定字段时直接返回 False。
        return False

    # `ap_int` / `ap_uint` 字段命中时视作 AXIS 协议字段。
    return re.match(rf"^\s*ap_u?int<\d+>\s+{re.escape(str_name)}\s*;", str_code) is not None
