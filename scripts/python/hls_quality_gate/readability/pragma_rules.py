"""检查 HLS pragma 与函数端口类型之间的接口一致性。"""

# 启用延迟注解，避免运行期解析类型标注。
from __future__ import annotations

# 正则库用于解析 pragma 参数和 C/C++ 函数参数名。
import re
from pathlib import Path

# C/C++ 轻量词法工具负责剥离注释、识别 pragma 和解析函数签名。
from .cpp_lexer import code_part, is_hls_pragma, parse_functions

# profile 和报告对象保持 HLS 可读性门禁的规则接口稳定。
from .profiles import HlsProfileConfig
from .report import HlsGateIssue, make_issue

# check_pragma_rules 是 pragma 规则的文件级入口。
def check_pragma_rules(root: Path, path: Path, config: HlsProfileConfig) -> list[HlsGateIssue]:
    """检查单个 HLS 源文件中的 INTERFACE pragma 绑定是否匹配端口类型。

    参数:
        root: HLS 可读性门禁扫描根目录，用于生成相对报告路径。
        path: 当前正在检查的 HLS 源文件路径。
        config: HLS profile 配置；当前 pragma 规则保留该参数以兼容规则接口。

    返回:
        当前文件 pragma 规则发现的问题列表。
    """

    # 当前规则暂不依赖 profile 细项，但保留参数避免破坏规则调用接口。
    del config

    # 报告路径统一使用 POSIX 分隔符，便于跨平台比较。
    str_rel_path = path.relative_to(root).as_posix()  # 当前文件相对扫描根目录的报告路径

    # 源码文本按行处理，保留 pragma 原始行号。
    str_text = path.read_text(encoding="utf-8", errors="ignore")  # HLS 源文件文本

    # 行列表同时供函数参数索引和 pragma 扫描使用。
    list_lines = str_text.splitlines()  # HLS 源码物理行

    # 函数参数类型索引用于判断 pragma port 绑定对象。
    dict_function_params = _parameter_type_index(list_lines)  # 端口名到参数声明文本的映射

    # pragma 诊断按源码顺序累计。
    list_issues: list[HlsGateIssue] = []  # 当前文件 pragma 诊断列表

    # 逐行扫描 pragma，避免普通注释或字符串触发规则。
    for int_line_number, str_raw_line in enumerate(list_lines, start=1):

        # 去掉注释后的代码片段用于 pragma 判断。
        str_code = code_part(str_raw_line).strip()  # 当前源码行的可执行 C/C++ 片段

        # 非 HLS pragma 行不参与接口一致性检查。
        if not is_hls_pragma(str_code):

            # 当前源码行不是 pragma，接口一致性规则在这里直接跳过。
            continue

        # INTERFACE pragma 才需要和函数端口类型对齐。
        if "interface" not in str_code.casefold():

            # PIPELINE、UNROLL 等其它 pragma 不绑定函数端口，因此这里不做接口类型核对。
            continue

        # 检查当前 pragma 与参数类型之间的冲突。
        list_issues.extend(
            _interface_conflict_issues(
                str_rel_path,
                int_line_number,
                str_code,
                dict_function_params,
            ),
        )

    # 返回当前文件累计的 pragma 诊断。
    return list_issues

# _parameter_type_index 建立函数端口名到声明文本的索引。
def _parameter_type_index(list_lines: list[str]) -> dict[str, str]:
    """从 HLS 函数签名中提取参数名和原始声明文本。

    参数:
        list_lines: HLS 源码物理行列表。

    返回:
        参数名到参数声明文本的映射。
    """

    # 参数索引用于后续 pragma port 反查类型。
    dict_index: dict[str, str] = {}  # 参数名到声明文本的映射

    # 逐个函数签名解析参数列表。
    for function in parse_functions(list_lines):

        # 函数签名中括号内容才是参数列表文本。
        str_params_text = _signature_params_text(function.signature)  # 函数参数列表文本

        # 按顶层逗号拆分参数，避免模板参数中的逗号误分裂。
        for str_raw_param in _split_params(str_params_text):

            # 提取参数声明中的最终参数名。
            str_param_name = _param_name(str_raw_param)  # 函数参数名称

            # 空参数名说明该声明形态超出轻量解析器能力。
            if not str_param_name:

                # 无法提取稳定参数名时，当前参数片段不能进入端口类型索引。
                continue

            # 保存原始参数声明文本，供接口类型规则判断。
            dict_index[str_param_name] = str_raw_param.strip()  # 参数原始声明文本

    # 返回完整参数索引。
    return dict_index

# _signature_params_text 提取函数签名中的参数列表文本。
def _signature_params_text(str_signature: str) -> str:
    """从函数签名中提取括号内参数文本。

    参数:
        str_signature: cpp_lexer 解析出的函数签名文本。

    返回:
        括号内参数文本；签名异常时返回空字符串。
    """

    # 缺少括号时说明签名不完整，无法可靠解析参数。
    if "(" not in str_signature or ")" not in str_signature:

        # 返回空文本，让调用方得到空参数列表。
        return ""

    # 只取最外层首个左括号到最后一个右括号之间的文本。
    return str_signature.split("(", 1)[1].rsplit(")", 1)[0]

# _interface_conflict_issues 检查单条 INTERFACE pragma。
def _interface_conflict_issues(
    str_rel_path: str,
    int_line: int,
    str_code: str,
    dict_params: dict[str, str],
) -> list[HlsGateIssue]:
    """检查单条 INTERFACE pragma 是否和绑定端口类型冲突。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        int_line: pragma 所在的一基源码行号。
        str_code: 去掉注释后的 pragma 源码片段。
        dict_params: 参数名到参数声明文本的映射。

    返回:
        当前 pragma 触发的接口一致性诊断列表。
    """

    # 解析 pragma 模式，例如 m_axi、axis、s_axilite。
    str_mode = _pragma_interface_mode(str_code)  # 绑定端口采用的 HLS 接口协议模式

    # 从 pragma 参数表里取出绑定对象，后续据此反查函数签名里的端口类型。
    str_port = _pragma_value(str_code, "port")  # INTERFACE pragma 绑定端口

    # 函数返回端口或未知端口无法和函数参数类型对齐。
    if not str_port or str_port == "return" or str_port not in dict_params:

        # 没有可验证端口时不产生命名冲突诊断。
        return []

    # 参数声明文本保留指针、数组和 stream 类型线索。
    str_param_type = dict_params.get(str_port, "")  # 绑定端口的参数声明文本

    # 小写化后检查 stream 和 AXIS 类型片段。
    str_lowered_type = str_param_type.casefold()  # 小写化后的参数声明文本

    # 一条 pragma 可能触发多种接口冲突，因此先准备顺序稳定的问题列表。
    list_issues: list[HlsGateIssue] = []  # 当前 pragma 诊断列表

    # m_axi 应绑定指针或数组端口。
    if str_mode == "m_axi" and "*" not in str_param_type and "[" not in str_param_type:

        # m_axi 绑定标量会导致接口协议和端口角色冲突。
        list_issues.append(
            _interface_issue(
                str_rel_path,
                int_line,
                str_code,
                str_port,
                str_param_type,
                "m_axi_scalar",
            ),
        )

    # axis 应绑定 hls::stream 或 ap_axiu token 类型。
    if str_mode == "axis" and "hls::stream" not in str_lowered_type and "ap_axiu" not in str_lowered_type:

        # axis 绑定非 stream/token 类型时提示人工确认。
        list_issues.append(
            _interface_issue(
                str_rel_path,
                int_line,
                str_code,
                str_port,
                str_param_type,
                "axis_non_stream",
            ),
        )

    # 标量控制接口不应绑定指针或 stream 端口。
    if str_mode in {"s_axilite", "ap_none", "ap_vld"} and _looks_like_pointer_or_stream(
        str_param_type,
        str_lowered_type,
    ):

        # 标量接口绑定复杂端口时提示人工确认。
        list_issues.append(
            _interface_issue(
                str_rel_path,
                int_line,
                str_code,
                str_port,
                str_param_type,
                "scalar_complex",
            ),
        )

    # 返回当前 pragma 的全部接口冲突诊断。
    return list_issues

# _interface_issue 构造接口冲突诊断对象。
def _interface_issue(
    str_rel_path: str,
    int_line: int,
    str_code: str,
    str_port: str,
    str_param_type: str,
    str_kind: str,
) -> HlsGateIssue:
    """根据冲突类别构造 HLS 接口诊断。

    参数:
        str_rel_path: 当前源文件相对扫描根目录的报告路径。
        int_line: pragma 所在的一基源码行号。
        str_code: 去掉注释后的 pragma 源码片段。
        str_port: pragma 绑定端口名。
        str_param_type: 端口对应的参数声明文本。
        str_kind: 冲突类别标识。

    返回:
        HLS 可读性诊断对象。
    """

    # 诊断 detail 统一记录端口名和参数类型，便于报告定位。
    str_detail = f"port={str_port}; type={str_param_type}"  # 接口冲突上下文字段

    # m_axi 绑定标量端口属于明确错误。
    if str_kind == "m_axi_scalar":

        # 构造 m_axi 端口角色冲突诊断。
        return make_issue(
            "HG023",
            "error",
            str_rel_path,
            int_line,
            "m_axi pragma 绑定到非指针/数组端口，端口角色和接口协议可能冲突。",
            detail=str_detail,
            node_kind="interface_pragma",
            code_excerpt=str_code,
        )

    # axis 绑定非 stream/token 类型属于可疑协议配置。
    if str_kind == "axis_non_stream":

        # 构造 axis 端口类型提示诊断。
        return make_issue(
            "HG023",
            "warning",
            str_rel_path,
            int_line,
            "axis pragma 绑定端口未体现 stream/AXIS token 类型，请确认协议角色。",
            detail=str_detail,
            node_kind="interface_pragma",
            code_excerpt=str_code,
        )

    # 其它类别统一表示标量控制接口绑定了复杂端口。
    return make_issue(
        "HG023",
        "warning",
        str_rel_path,
        int_line,
        "标量控制接口 pragma 绑定到指针或 stream 端口，请确认端口角色。",
        detail=str_detail,
        node_kind="interface_pragma",
        code_excerpt=str_code,
    )

# _looks_like_pointer_or_stream 判断参数声明是否为复杂数据端口。
def _looks_like_pointer_or_stream(str_param_type: str, str_lowered_type: str) -> bool:
    """判断参数声明是否像指针、数组或 stream 端口。

    参数:
        str_param_type: 原始参数声明文本。
        str_lowered_type: 小写化后的参数声明文本。

    返回:
        声明包含指针符号或 hls::stream 片段时返回 True。
    """

    # 指针和 stream 都不应被普通标量控制接口绑定。
    return "*" in str_param_type or "hls::stream" in str_lowered_type

# _pragma_value 提取 pragma 中 key=value 形式的字段。
def _pragma_value(str_line: str, str_key: str) -> str:
    """读取 pragma 中的指定键值。

    参数:
        str_line: pragma 源码片段。
        str_key: 需要提取的 pragma 键名。

    返回:
        键对应的标识符值；未命中时返回空字符串。
    """

    # 使用单词边界避免匹配到其它参数名后缀。
    list_pragma_values = re.findall(rf"\b{re.escape(str_key)}\s*=\s*([A-Za-z0-9_]+)", str_line)  # 指定 pragma 键名对应的参数取值列表

    # 命中时返回第一个捕获组文本。
    return list_pragma_values[0] if list_pragma_values else ""

# 从 `#pragma HLS INTERFACE` 语句头部抽出 m_axi、axis、s_axilite 这类模式名。
def _pragma_interface_mode(str_line: str) -> str:
    """读取 INTERFACE pragma 的接口模式。

    参数:
        str_line: pragma 源码片段。

    返回:
        小写化后的接口模式；未命中时返回空字符串。
    """

    # 这个正则只关心 `INTERFACE` 后紧跟的协议模式 token，不解析其余键值参数。
    list_mode_tokens = re.findall(  # INTERFACE 接口协议模式 token 列表
        r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z0-9_]+)",  # INTERFACE pragma 协议模式的提取正则
        str_line,  # 当前待解析的 pragma 源码片段
        flags=re.IGNORECASE,  # 忽略 HLS 关键字的大小写差异
    )

    # 命中后去掉边界空白并统一小写。
    return list_mode_tokens[0].strip().casefold() if list_mode_tokens else ""

# _split_params 按顶层逗号拆分函数参数。
def _split_params(str_params_text: str) -> list[str]:
    """把函数参数文本拆分成单个参数声明。

    参数:
        str_params_text: 函数签名括号内的参数文本。

    返回:
        单个参数声明文本列表。
    """

    # 参数片段列表按出现顺序保存。
    list_parts: list[str] = []  # 顶层参数片段列表

    # 模板、括号和数组维度嵌套深度用于保护内部逗号。
    int_depth = 0  # 当前括号或模板嵌套深度

    # 当前参数片段的起始字符位置。
    int_start = 0  # 当前参数片段起始索引

    # 逐字符扫描参数文本，只有顶层逗号才切分。
    for int_index, str_char in enumerate(str_params_text):

        # 打开模板或数组层级时先增加嵌套深度，保护内部逗号不参与参数切分。
        if str_char in "<([":

            # 当前字符打开模板或括号层级，后续逗号应视为参数内部内容。
            int_depth = int_depth + 1  # 参数拆分嵌套深度

            # 当前字符只用于更新嵌套层级，不应继续参与顶层分隔判断。
            continue

        # 关闭模板或数组层级时，只在已有嵌套深度的前提下回退一级。
        if str_char in ">)]" and int_depth:

            # 当前字符关闭一层模板或括号嵌套，逗号要等回到深度零才恢复分隔作用。
            int_depth = int_depth - 1  # 关闭一层模板或括号后的当前嵌套深度

            # 当前字符已经用于更新层级，不再参与顶层逗号判断。
            continue

        # 只有深度为零的逗号才表示一个顶层参数结束。
        if str_char != "," or int_depth != 0:

            # 非顶层逗号或普通字符都继续留在当前参数片段中。
            continue

        # 顶层逗号表示一个参数声明结束。
        list_parts.append(str_params_text[int_start:int_index])

        # 下一段参数从逗号后一位开始。
        int_start = int_index + 1  # 下一参数片段起始索引

    # 非空参数列表需要追加最后一个片段。
    if str_params_text.strip():

        # 追加最后一个参数片段。
        list_parts.append(str_params_text[int_start:])

    # 返回全部参数片段。
    return list_parts

# _param_name 从单个参数声明中提取最终参数名。
def _param_name(str_raw_param: str) -> str | None:
    """提取 C/C++ 参数声明中的参数名。

    参数:
        str_raw_param: 单个参数声明文本。

    返回:
        参数名；无法识别合法标识符时返回 None。
    """

    # 默认值不属于参数名解析范围。
    str_token = str_raw_param.strip().split("=")[0].strip()  # 去掉默认值后的参数声明

    # 指针和引用符号独立成 token，便于后续过滤。
    str_token = str_token.replace("&", " & ").replace("*", " * ")  # 标准化后的声明 token 文本

    # const、volatile 和指针引用符号不参与参数名候选。
    list_words = [  # 参数声明中的有效词序列
        str_part  # 过滤掉修饰符和指针引用符号后保留的候选 token
        for str_part in re.split(r"\s+", str_token)  # 按空白拆开的声明 token 序列
        if str_part and str_part not in {"const", "volatile", "&", "*"}  # 只保留可能构成参数名的 token
    ]

    # 没有有效词时无法判断参数名。
    if not list_words:

        # 返回 None 表示当前参数声明不可解析。
        return None

    # 参数名通常是过滤后的最后一个 token。
    str_candidate = list_words[-1]  # 候选参数名

    # 数组参数名可能带维度，需要去掉方括号部分。
    str_candidate = str_candidate.split("[")[0]  # 去掉数组维度后的候选参数名

    # 合法 C/C++ 标识符才作为参数名返回。
    return str_candidate if re.match(r"^[A-Za-z_]\w*$", str_candidate) else None
