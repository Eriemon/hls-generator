"""检查 HLS 函数契约、top 端口契约和占位式说明。"""

# 启用延迟注解，避免类型提示影响运行期导入。
from __future__ import annotations

# 导入正则和路径类型，用于注释文本与源码路径处理。
import re
from pathlib import Path

# 导入 C/C++ 轻量解析器提供的注释和函数解析工具。
from .cpp_lexer import (
    contains_cjk,
    immediate_preceding_comment,
    normalize_comment_text,
    parse_functions,
)

# 导入 HLS 可读性门禁配置。
from .profiles import HlsProfileConfig

# 导入门禁问题结构和构造函数。
from .report import HlsGateIssue, make_issue

# 把多行关键词文本规整成不可变元组，避免长词表逐元素触发行内注释规则。
def _nonempty_lines_tuple(text: str) -> tuple[str, ...]:
    """把多行文本拆成去空白且去掉空行的字符串元组。

    参数:
        text: 以多行文本写入的关键词或占位短语集合。
    返回:
        tuple[str, ...]，保留原顺序且每项都已去掉首尾空白。
    """

    # 过滤空行并逐项去空白，保证关键词集合在匹配和比较时稳定一致。
    return tuple(str_line.strip() for str_line in text.splitlines() if str_line.strip())

# 函数契约注释需要体现职责、输入输出或数据形态等关键词。
CONTRACT_KEYWORDS = _nonempty_lines_tuple(  # 函数契约关键词
    """
    功能
    契约
    输入
    输出
    端口
    协议
    长度
    shape
    depth
    unit
    单位
    方向
    返回
    副作用
    事务
    数组
    stream
    bundle
    """,
)

# top 端口契约需要覆盖方向、协议、长度或 shape 等端口事实。
PORT_CONTRACT_KEYWORDS = _nonempty_lines_tuple(  # top 端口契约关键词
    """
    direction
    方向
    input
    output
    输入
    输出
    protocol
    协议
    depth
    长度
    shape
    维度
    unit
    单位
    bundle
    端口
    m_axi
    s_axilite
    axis
    """,
)

# 占位式契约说明会被判定为空泛注释。
PLACEHOLDER_CONTRACTS = _nonempty_lines_tuple(  # 占位契约短语
    """
    函数说明
    参数说明
    返回说明
    todo
    待补充
    placeholder
    描述函数
    """,
)

# 指针、数组和 stream 端口必须出现范围或事务规模描述。
PORT_RANGE_KEYWORDS = _nonempty_lines_tuple(  # 端口范围关键词
    """
    depth
    长度
    shape
    维度
    事务
    transaction
    """,
)

# check_contract_rules 是 runner 调用的函数契约检查入口。
def check_contract_rules(
    root: Path,
    path: Path,
    config: HlsProfileConfig,
    *,
    top_function: str | None = None,
) -> list[HlsGateIssue]:
    """检查单个 HLS 源文件中的函数契约与 top 端口契约。

    Args:
        root: 可读性门禁扫描根目录。
        path: 当前正在检查的 HLS 源文件。
        config: 当前 profile 展开的 HLS 检查配置。
        top_function: 可选的 top 函数名，用于启用端口契约检查。

    Returns:
        当前源文件中发现的契约类门禁问题列表。
    """

    # 相对路径用于报告输出，保持跨平台显示稳定。
    str_rel_path = path.relative_to(root).as_posix()  # 报告相对路径

    # splitlines 保留逐行语义，便于用解析器定位函数签名。
    list_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()  # 源码行列表

    # 轻量解析器返回函数定义和声明的基础结构。
    list_functions = parse_functions(list_lines)  # 函数信息列表

    # 问题列表按源码遍历顺序追加，方便报告稳定排序。
    list_issues: list[HlsGateIssue] = []  # 契约问题列表

    # 逐个函数检查 contract 注释和 top 端口说明。
    for function_info in list_functions:

        # 函数声明不要求完整契约说明。
        if function_info.is_declaration:

            # 只有完整定义需要检查契约注释，声明语句在这里直接跳过。
            continue

        # 测试平台中的 main 入口不是 HLS kernel/helper 函数。
        if function_info.name == "main" and "_tb" in str_rel_path.lower():

            # testbench main 只是宿主入口，不要求按 HLS 函数契约继续检查。
            continue

        # 函数签名前最近的连续注释块作为契约候选。
        str_comment = _contract_comment_above(list_lines, function_info.signature_start_line)  # 函数契约注释

        # 按 profile 要求检查普通函数契约。
        if config.require_function_contract:

            # 普通函数契约问题直接追加到当前文件的问题列表。
            _append_function_contract_issue(
                list_issues,
                str_rel_path,
                function_info.signature_start_line,
                function_info.signature,
                str_comment,
            )

        # top 函数额外检查端口名称和端口契约关键词。
        if (
            top_function
            and function_info.name == top_function
            and config.require_top_port_contract
        ):

            # top 端口契约问题按参数顺序批量合并。
            list_issues.extend(
                _top_port_contract_issues(
                    str_rel_path,
                    function_info.signature_start_line,
                    function_info.signature,
                    function_info.params,
                    str_comment or "",
                ),
            )

    # 返回当前文件内发现的全部契约问题。
    return list_issues

# 函数契约问题追加逻辑集中处理，避免主循环混入长 make_issue 调用。
def _append_function_contract_issue(
    list_issues: list[HlsGateIssue],
    str_rel_path: str,
    int_line: int,
    str_signature: str,
    str_comment: str | None,
) -> None:
    """根据函数契约注释内容追加 HG008 问题。

    参数:
        list_issues: 当前文件累计的契约问题列表。
        str_rel_path: 报告中使用的相对文件路径。
        int_line: 函数签名所在的一基源码行号。
        str_signature: 函数签名原文，用于问题回显。
        str_comment: 函数签名前提取到的契约注释文本。
    返回:
        None；发现的问题会原地追加到 `list_issues`。
    """

    # 缺少中文契约说明时直接报告必填问题。
    if not str_comment or not contains_cjk(str_comment):

        # HG008 要求函数说明覆盖职责、参数和返回或副作用。
        list_issues.append(
            make_issue(
                "HG008",
                "error",
                str_rel_path,
                int_line,
                "函数/top/helper 必须有中文 contract 注释说明职责、参数和返回/副作用。",
                detail=str_signature,
                node_kind="function_contract",
                code_excerpt=str_signature,
            ),
        )

        # 缺少契约注释时不继续做空泛性判断。
        return

    # 已有中文契约仍需要排除模板式或过短说明。
    if _contract_is_vague(str_comment):

        # 空泛契约必须指出数据路径职责、参数角色和输出含义。
        list_issues.append(
            make_issue(
                "HG008",
                "error",
                str_rel_path,
                int_line,
                "函数 contract 注释过于空泛，必须说明数据路径职责、参数角色和输出含义。",
                detail=str_comment,
                node_kind="function_contract",
                code_excerpt=str_signature,
            ),
        )

# 连续前置注释优先作为契约块，单行前置注释作为兜底。
def _contract_comment_above(
    list_lines: list[str],
    signature_start_line: int,
) -> str | None:
    """提取函数签名前方的连续注释文本。

    参数:
        list_lines: 当前 HLS 源码的逐行文本列表。
        signature_start_line: 函数签名所在的一基源码行号。
    返回:
        找到契约注释时返回合并后的文本，否则返回 `None`。
    """

    # signature_start_line 是一基行号，转为上一行的零基索引。
    int_index = signature_start_line - 2  # 注释扫描索引

    # 文件开头之前没有可用注释。
    if int_index < 0:

        # 返回 None 表示缺少契约注释。
        return None

    # 连续注释行会逆序收集，最后再恢复源码顺序。
    list_comments: list[str] = []  # 连续注释文本

    # 向上扫描直到遇到空行或非注释行。
    while int_index >= 0:

        # strip 后判断注释形态，保留原行用于 normalize。
        str_stripped = list_lines[int_index].strip()  # 去空白源码行

        # 空行切断函数契约注释块。
        if not str_stripped:

            # 注释块一旦被空行打断，就不再继续向上合并。
            break

        # C/C++ 注释行纳入契约候选块。
        if str_stripped.startswith(("//", "/*", "*")):

            # 标准化注释符号，避免检查规则依赖具体写法。
            list_comments.append(
                normalize_comment_text(list_lines[int_index]),
            )

            # 继续向上寻找同一注释块的上一行。
            int_index -= 1  # 上移到前一行继续扫描

            # 当前行已经并入契约注释块，继续处理更早的一行。
            continue

        # 非注释行说明已离开契约注释块。
        break

    # 多行契约注释按源码顺序合并后返回。
    if list_comments:

        # reversed 恢复注释块在源文件中的书写顺序。
        return "\n".join(reversed(list_comments)).strip()

    # 没有连续注释块时回退到解析器的一行前置注释逻辑。
    return immediate_preceding_comment(list_lines, signature_start_line - 1)

# 空泛契约判断只关注占位短语、关键词覆盖和中文信息量。
def _contract_is_vague(str_comment: str) -> bool:
    """判断函数契约注释是否过于模板化或信息不足。

    参数:
        str_comment: 函数签名前提取到的契约注释文本。
    返回:
        注释模板化、缺关键词或中文信息量不足时返回 `True`。
    """

    # 统一大小写，兼容英文关键词和占位短语。
    str_lowered = str_comment.casefold()  # 小写契约注释

    # 占位短语出现时直接判定为空泛。
    if any(str_phrase in str_lowered for str_phrase in PLACEHOLDER_CONTRACTS):

        # 占位文本不能作为有效契约。
        return True

    # 缺少契约关键词时，说明未描述数据路径或接口事实。
    if not any(str_keyword.casefold() in str_lowered for str_keyword in CONTRACT_KEYWORDS):

        # 缺关键词的中文说明通常只是在泛泛描述函数。
        return True

    # 中文字符数量过少时仍无法说明职责、参数和输出。
    if len(re.findall(r"[\u4e00-\u9fff]", str_comment)) < 12:

        # 信息量不足的短注释视为空泛契约。
        return True

    # 通过占位、关键词和信息量三重检查。
    return False

# top 端口契约检查必须逐个参数确认名称和端口事实。
def _top_port_contract_issues(
    str_rel_path: str,
    int_line: int,
    str_signature: str,
    tuple_params: tuple[str, ...],
    str_comment: str,
) -> list[HlsGateIssue]:
    """检查 top 函数注释是否逐个覆盖端口契约。

    参数:
        str_rel_path: 报告中使用的相对文件路径。
        int_line: top 函数签名所在的一基源码行号。
        str_signature: top 函数签名原文。
        tuple_params: top 函数参数名元组。
        str_comment: top 函数签名前方提取到的契约注释文本。
    返回:
        list[HlsGateIssue]，按参数顺序收集的端口契约问题列表。
    """

    # top 端口问题按参数顺序追加，方便用户定位缺失说明。
    list_issues: list[HlsGateIssue] = []  # top 端口问题列表

    # top 契约要同时匹配端口名与协议词，因此先把注释统一成小写。
    str_lowered_comment = str_comment.casefold()  # 归一化后的 top 契约注释文本，供端口名与协议词匹配复用

    # 无参数 top 函数没有端口契约可检查。
    if not tuple_params:

        # 返回空列表保持调用方 extend 语义稳定。
        return []

    # 逐个端口名检查注释是否明确提及。
    for str_param in tuple_params:

        # 端口名缺失会让契约无法映射到具体接口。
        if str_param not in str_lowered_comment and str_param.casefold() not in str_lowered_comment:

            # HG015 要求端口名、方向、协议、深度或 shape 逐个说明。
            list_issues.append(
                make_issue(
                    "HG015",
                    "error",
                    str_rel_path,
                    int_line,
                    "top function port contract 必须逐个说明端口名称、方向、协议、深度/shape/unit。",
                    detail=f"missing port={str_param}",
                    node_kind="top_port_contract",
                    code_excerpt=str_signature,
                ),
            )

    # 缺少端口契约关键词时，说明没有覆盖接口事实。
    if not any(str_keyword.casefold() in str_lowered_comment for str_keyword in PORT_CONTRACT_KEYWORDS):

        # HG015 要求 top contract 覆盖 direction/protocol/depth/shape/unit。
        list_issues.append(
            make_issue(
                "HG015",
                "error",
                str_rel_path,
                int_line,
                "top function contract 缺少 direction/protocol/depth/shape/unit 等端口契约关键词。",
                detail=str_comment,
                node_kind="top_port_contract",
                code_excerpt=str_signature,
            ),
        )

    # 指针、数组和 stream 端口必须说明范围或事务规模。
    if _signature_has_range_sensitive_port(str_signature) and not _comment_has_range_contract(
        str_lowered_comment,
    ):

        # 缺少范围说明会让 HLS 接口深度和事务边界不可审查。
        list_issues.append(
            make_issue(
                "HG015",
                "error",
                str_rel_path,
                int_line,
                "指针/数组/stream 端口契约必须说明 depth、shape、长度或事务范围。",
                detail=str_comment,
                node_kind="top_port_contract",
                code_excerpt=str_signature,
            ),
        )

    # 返回 top 函数端口契约问题列表。
    return list_issues

# 签名范围敏感性用于识别需要 depth 或 shape 的端口形态。
def _signature_has_range_sensitive_port(str_signature: str) -> bool:
    """判断函数签名是否含指针、数组或 hls::stream 端口。

    参数:
        str_signature: 待分析的函数签名原文。
    返回:
        发现指针、数组或 hls::stream 端口时返回 `True`。
    """

    # 提取括号内参数文本，避免返回类型或函数名干扰判断。
    str_params_text = (  # 参数签名文本
        str_signature.split("(", 1)[1].rsplit(")", 1)[0]  # 已定位到的签名括号内部文本
        if "(" in str_signature and ")" in str_signature  # 仅在同时存在左右括号时截取参数段
        else ""  # 非常规签名形态回退为空文本
    )

    # 指针、数组和 stream 都需要明确长度或事务范围。
    return (
        "*" in str_params_text
        or "[" in str_params_text
        or "hls::stream" in str_params_text.casefold()
    )

# 范围契约检查集中处理，便于后续扩展关键词集合。
def _comment_has_range_contract(str_lowered_comment: str) -> bool:
    """判断 top 注释是否包含范围、shape 或事务规模说明。

    参数:
        str_lowered_comment: 已统一成小写的 top 契约注释文本。
    返回:
        命中任一范围关键词时返回 `True`。
    """

    # 命中任一范围关键词即可视为覆盖端口规模。
    return any(str_keyword in str_lowered_comment for str_keyword in PORT_RANGE_KEYWORDS)
