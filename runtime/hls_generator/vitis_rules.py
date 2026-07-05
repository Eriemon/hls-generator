"""提供 Vitis HLS 2022.2 及更新版本的静态兼容规则。"""

# 启用延迟注解，避免运行时解析复杂类型别名。
from __future__ import annotations

# 导入正则扫描与 JSON 友好诊断所需的标准库类型。
import re
from typing import Any

# 复用正则词边界片段，避免路径门禁把 \b... 规则误判为文件路径。
REGEX_WORD_BOUNDARY = chr(92) + "b"  # 正则单词边界转义片段

# 约束 pragma interface 允许使用的 HLS 接口模式。
ALLOWED_INTERFACE_MODES = frozenset(  # 支持的接口模式集合
    {
        "ap_ctrl_none",  # 顶层函数不使用启动停止握手
        "ap_ctrl_hs",  # 顶层函数采用标准握手控制
        "ap_fifo",  # 端口映射为 FIFO 接口
        "ap_memory",  # 端口映射为片上存储接口
        "ap_none",  # 标量端口不附加握手
        "axis",  # 端口映射为 AXI4-Stream
        "m_axi",  # 大容量数组通过 AXI4 master 访问外部存储
        "s_axilite",  # 控制寄存器映射为 AXI4-Lite
    }
)

# 约束 hls_config.cfg 中允许出现的 config_* 命令。
ALLOWED_CONFIG_COMMANDS = frozenset(  # 支持的配置命令集合
    {
        "config_compile",  # 编译阶段配置命令
        "config_interface",  # 接口生成阶段配置命令
        "config_rtl",  # RTL 输出阶段配置命令
        "config_dataflow",  # DATAFLOW 调度配置命令
        "config_csim",  # C 仿真配置命令
        "config_cosim",  # 协同仿真配置命令
        "config_schedule",  # 调度阶段配置命令
        "config_export",  # 导出 IP 阶段配置命令
    }
)

# 约束各配置段可接收的 option，防止静态生成过期或未知选项。
ALLOWED_CONFIG_OPTIONS = {
    "compile": frozenset({"pipeline_loops", "enable_auto_rewind", "pipeline_style", "unsafe_math_optimizations"}),  # config_compile 只接受这些编译期开关
    "interface": frozenset({"m_axi_addr64", "m_axi_max_read_burst_length", "default_slave_interface"}),  # config_interface 只接受这些接口生成选项
    "rtl": frozenset({"reset", "register_all_io", "module_prefix", "reset_level"}),  # config_rtl 只接受这些 RTL 输出选项
    "dataflow": frozenset({"fifo_depth", "strict_mode", "start_fifo_depth"}),  # DATAFLOW 段控制通道深度与调度约束
    "schedule": frozenset({"enable_dsp_full_reg"}),  # config_schedule 只接受这些调度期选项
    "csim": frozenset({"clean", "argv", "compile_only", "o", "ldflags"}),  # config_csim 只接受这些 C 仿真选项
    "cosim": frozenset({"rtl", "tool", "trace_level", "wave_debug", "random_stall", "enable_tasks_with_m_axi"}),  # config_cosim 只接受这些协同仿真选项
    "export": frozenset(  # config_export 只接受这些 IP 导出选项
        {
            "format",  # 约束导出结果采用的封装格式
            "rtl",  # 约束导出时选择的 RTL 目标类型
            "vendor",  # 指定生成 IP 时写入的供应商标识
            "library",  # 指定生成 IP 时归属的库名称
            "version",  # 指定导出 IP 对外暴露的版本号
            "display_name",  # 指定导出 IP 在工具里显示的名称
            "vivado_synth_strategy",  # 指定 Vivado 综合阶段采用的策略名
            "ip_xdc_file",  # 指定导出 IP 额外绑定的约束文件路径
        }
    ),
}

# 约束 Tcl directive 与 pragma 生成只能使用当前支持的 HLS 指令。
ALLOWED_DIRECTIVES = frozenset(  # 支持的 HLS directive 名称集合
    {
        "aggregate",  # 聚合结构体或数组成员
        "array_partition",  # 数组分块存储
        "array_reshape",  # 数组重塑存储布局
        "bind_op",  # 运算绑定到底层实现
        "bind_storage",  # 存储资源绑定到底层实现
        "dataflow",  # 开启任务级 DATAFLOW
        "dependence",  # 显式声明依赖关系
        "inline",  # 控制函数内联
        "interface",  # 指定端口接口协议
        "loop_flatten",  # 展平嵌套循环
        "loop_merge",  # 合并相邻循环
        "loop_tripcount",  # 提供循环迭代范围
        "pipeline",  # 开启流水化
        "stream",  # 指定 stream/FIFO 行为
        "unroll",  # 展开循环体
    }
)

# 约束报告脚本中可直接生成的 Vitis HLS report 命令。
ALLOWED_REPORT_COMMANDS = frozenset(  # 支持的报告命令集合
    {
        "report_utilization",  # 资源利用率报告
        "report_timing",  # 时序报告
        "report_directive",  # directive 使用报告
        "report_dataflow",  # DATAFLOW 拓扑报告
        "report_interface",  # 接口报告
        "report_top",  # 顶层函数概要报告
    }
)

# 记录新流程禁止继续生成或接受的旧命令、旧 pragma 与旧编译选项。
DEPRECATED_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        REGEX_WORD_BOUNDARY + "config_sdx" + REGEX_WORD_BOUNDARY,  # 命中旧版 SDx 配置命令时直接阻断
        "Deprecated Vitis HLS command `config_sdx` is not allowed in new scripts.",  # 提示迁移到当前 Vitis HLS 配置命令体系
    ),
    (
        REGEX_WORD_BOUNDARY + "set_directive_data_pack" + REGEX_WORD_BOUNDARY,  # 命中已停用的 data_pack Tcl 指令时直接阻断
        "Deprecated Vitis HLS command `set_directive_data_pack` is not allowed; use aggregate.",  # 提示把 data_pack 改写成 aggregate
    ),
    (
        REGEX_WORD_BOUNDARY + "set_directive_resource" + REGEX_WORD_BOUNDARY,  # 命中已停用的资源绑定 Tcl 指令时直接阻断
        "Deprecated Vitis HLS command `set_directive_resource` is not allowed; use bind_op or bind_storage.",  # 提示改写为 bind_op 或 bind_storage
    ),
    (
        r"#pragma\s+HLS\s+DATA_PACK\b",  # 命中旧版 DATA_PACK pragma 时直接阻断
        "Deprecated Vitis HLS pragma `DATA_PACK` is not allowed; use AGGREGATE.",  # 提示把 DATA_PACK pragma 改写成 AGGREGATE
    ),
    (
        r"[\"<]hls_linear_algebra\.h[\">]",  # 命中旧版线性代数头文件时直接阻断
        "Deprecated Vitis HLS header `hls_linear_algebra.h` is not allowed.",  # 提示替换掉过期的线性代数头文件
    ),
    (
        REGEX_WORD_BOUNDARY + "-std=c" + chr(92) + "+" + chr(92) + "+0x" + REGEX_WORD_BOUNDARY,  # 命中过时的 C++0x 编译选项时直接阻断
        "Obsolete C++ flag `-std=c++0x` is not suitable for modern Vitis HLS 2022.2+ Clang-based flows.",  # 提示切换到现代 Clang 流程兼容的 C++ 标准
    ),
)

# 检测 C/C++ 中不适合 HLS 静态综合的变长栈数组声明。
VARIABLE_LENGTH_ARRAY_PATTERN = (
    r"\b[A-Za-z_][A-Za-z0-9_:<>]*\s+"
    r"[A-Za-z_][A-Za-z0-9_]*\s*"
    r"\[[A-Za-z_][A-Za-z0-9_]*\]\s*;"
)  # 变长数组正则

# 标记会执行 C/C++ 源码规则的语言名称。
SOURCE_SCAN_LANGUAGES = frozenset(  # 源码扫描语言集合
    {"c", "cpp", "c++", "cc", "cxx", "h", "hpp", "text"}  # 允许触发源码规则的语言标签
)

# 标记不需要浮点 unsafe_math 提醒的测试平台语言名称。
TESTBENCH_LANGUAGES = frozenset({"testbench", "tb"})  # 测试平台语言集合

# 扫描源码或配置文本中的 Vitis HLS 兼容性问题。
def scan_vitis_rule_violations(text: str, *, path: str | None = None, language: str = "text") -> list[dict[str, Any]]:
    """返回源码或配置文本中的确定性 Vitis HLS 兼容诊断。

    参数:
        text: 需要扫描的源码、Tcl 或 hls_config 文本。
        path: 可选的文件路径，用于诊断定位。
        language: 文本所属语言或用途标签。

    返回:
        包含 severity、message、path、stage 与 source 的诊断字典列表。
    """

    # 统一语言标签，避免重复 lower 调用造成条件难读。
    str_language = language.lower()  # 规范化语言标签

    # 收集本轮静态扫描发现的兼容性诊断。
    list_issues: list[dict[str, Any]] = []  # Vitis 兼容性诊断列表

    # 逐条检查已知废弃语法，保留规则表中的原始诊断文本。
    for tuple_deprecated_pattern in DEPRECATED_PATTERNS:

        # 拆出正则表达式，便于下方正则调用表达真实意图。
        str_pattern = tuple_deprecated_pattern[0]  # 废弃语法正则

        # 拆出诊断文本，便于追加到统一 issue 结构。
        str_message = tuple_deprecated_pattern[1]  # 废弃语法诊断文本

        # 命中废弃语法时登记 error 级兼容性诊断。
        if re.search(str_pattern, text, flags=re.IGNORECASE):

            # 把当前废弃语法命中登记到统一诊断列表。
            list_issues.append(_issue("error", str_message, path))

    # C/C++ 源码中禁止使用综合边界不稳定的变长栈数组。
    if str_language in SOURCE_SCAN_LANGUAGES and re.search(VARIABLE_LENGTH_ARRAY_PATTERN, text):

        # 对变长栈数组追加单独错误，提示改为静态边界。
        list_issues.append(
            _issue(
                "error",
                "Variable-length stack arrays are not suitable for this Vitis HLS flow; use static bounds.",
                path,
            )
        )

    # 追加 pragma interface mode 的合法性诊断。
    list_issues.extend(_interface_mode_issues(text, path))

    # 追加 ARRAY_PARTITION 与 ARRAY_RESHAPE 同变量冲突诊断。
    list_issues.extend(_array_partition_reshape_issues(text, path))

    # 非 testbench 浮点代码需要显式决定 unsafe_math 策略。
    if (
        str_language not in TESTBENCH_LANGUAGES
        and re.search(r"\bfloat\b|\bdouble\b", text)
        and not re.search(REGEX_WORD_BOUNDARY + "unsafe_math_optimizations" + REGEX_WORD_BOUNDARY, text)
    ):

        # 对浮点实现补记 unsafe_math 策略提醒，避免默认策略不透明。
        list_issues.append(
            _issue(
                "warning",
                "Floating-point HLS code should explicitly decide whether "
                "`config_compile -unsafe_math_optimizations` is allowed.",
                path,
            )
        )

    # 返回稳定排序来源下的诊断列表，调用方负责展示或阻断。
    return list_issues

# 校验 directive 名称是否属于当前支持集合。
def require_allowed_directive(name: str) -> str:
    """返回规范化后的合法 Vitis HLS directive 名称。

    参数:
        name: 用户或生成器给出的 directive 名称。

    返回:
        小写后的 directive 名称。

    异常:
        ValueError: 当 directive 不在允许集合中时抛出。
    """

    # 清理输入空白并统一小写，匹配规则表键名。
    str_normalized_directive = name.strip().lower()  # 规范化 directive 名称

    # 未登记的 directive 不能进入生成流程。
    if str_normalized_directive not in ALLOWED_DIRECTIVES:

        # 直接阻止未知 directive 进入后续 Tcl 生成链路。
        raise ValueError(f"> ERR: [Python] Unsupported Vitis HLS directive {name!r}.")

    # 返回规范化名称，便于后续 Tcl 生成复用。
    return str_normalized_directive

# 校验 hls_config 段名是否能映射到受支持的 config_* 命令。
def require_allowed_config_section(section: str) -> str:
    """返回规范化后的合法 hls_config 段名。

    参数:
        section: hls_config 中的配置段名。

    返回:
        小写后的配置段名。

    异常:
        ValueError: 当段名无法映射到允许的 config_* 命令时抛出。
    """

    # 清理输入空白并统一小写，匹配配置规则表。
    str_normalized_section = section.strip().lower()  # 规范化配置段名

    # 拼出 Vitis HLS 实际命令名称，用于允许集合校验。
    str_config_command = f"config_{str_normalized_section}"  # 配置段对应的 Vitis HLS Tcl 命令名

    # 未登记的 config_* 命令不能进入生成流程。
    if str_config_command not in ALLOWED_CONFIG_COMMANDS:

        # 阻止未受支持的配置段写入 hls_config。
        raise ValueError(f"> ERR: [Python] Unsupported Vitis HLS config section {section!r}.")

    # 返回规范化段名，便于后续 option 校验复用。
    return str_normalized_section

# 校验 hls_config 段内 option 是否属于该段允许集合。
def require_allowed_config_option(section: str, key: str) -> str:
    """返回规范化后的合法 hls_config option 名称。

    参数:
        section: hls_config 中的配置段名。
        key: 配置段内的 option 名称。

    返回:
        小写后的 option 名称。

    异常:
        ValueError: 当 option 不属于指定配置段允许集合时抛出。
    """

    # 先规范化配置段标签，确保后续查表命中对应配置域。
    str_normalized_section = section.strip().lower()  # 当前 option 所属的规范化配置段名

    # 再规范化 option 名称，避免用户输入大小写干扰允许集合判断。
    str_normalized_key = key.strip().lower()  # 当前待校验的规范化 option 键名

    # 读取该配置段允许的 option 集合。
    set_allowed_options = ALLOWED_CONFIG_OPTIONS.get(str_normalized_section)  # 允许的 option 集合

    # 缺失配置段或 option 未登记时拒绝生成。
    if set_allowed_options is None or str_normalized_key not in set_allowed_options:

        # 对未知 option 立即报错，避免生成器拼出无效配置项。
        raise ValueError(f"> ERR: [Python] Unsupported Vitis HLS cfg option [{section}].{key}.")

    # 返回规范化 option 名称，便于调用方生成稳定配置。
    return str_normalized_key

# 扫描 INTERFACE pragma 中不受支持的 mode。
def _interface_mode_issues(text: str, path: str | None) -> list[dict[str, Any]]:
    """返回 INTERFACE pragma mode 的兼容性诊断。

    参数:
        text: 需要扫描的源码文本。
        path: 可选的文件路径，用于诊断定位。

    返回:
        不合法接口模式对应的诊断列表。
    """

    # 收集所有不受支持的 interface mode 诊断。
    list_issues: list[dict[str, Any]] = []  # 接口模式违规诊断列表

    # 按行扫描 pragma，减少正则在无关文本上的误匹配。
    for str_line in text.splitlines():

        # 非 INTERFACE pragma 行不需要继续解析。
        if "#pragma HLS INTERFACE" not in str_line:

            # 跳过非接口 pragma 行，只保留真正声明接口模式的语句。
            continue

        # 同时支持 mode=axis 与 positional mode 两种写法。
        str_interface_mode = _pragma_value(str_line, "mode") or _pragma_interface_mode(str_line)  # 接口模式

        # 只在显式解析出 mode 且不在允许集合时登记错误。
        if str_interface_mode and str_interface_mode not in ALLOWED_INTERFACE_MODES:

            # 对超出白名单的接口模式追加兼容性错误。
            list_issues.append(
                _issue("error", f"Unsupported Vitis HLS interface mode {str_interface_mode!r}.", path)
            )

    # 返回本规则扫描出的所有接口模式问题。
    return list_issues

# 扫描同一变量同时 ARRAY_PARTITION 与 ARRAY_RESHAPE 的冲突。
def _array_partition_reshape_issues(text: str, path: str | None) -> list[dict[str, Any]]:
    """返回数组 partition 与 reshape 指令冲突诊断。

    参数:
        text: 需要扫描的源码或配置文本。
        path: 可选的文件路径，用于诊断定位。

    返回:
        同变量重复应用 ARRAY_PARTITION 与 ARRAY_RESHAPE 的诊断列表。
    """

    # 汇总被 ARRAY_PARTITION 约束的变量，兼顾 pragma 与 directive 两种来源。
    set_partitioned_targets = (  # 后续要参与冲突求交的 partition 目标全集
        _pragma_array_targets(text, "ARRAY_PARTITION")  # 从 pragma 中提取 ARRAY_PARTITION 目标变量名
        | _directive_array_targets(text, "array_partition")  # 从 Tcl directive 中提取 array_partition 目标变量名
    )

    # 汇总被 ARRAY_RESHAPE 约束的变量，后续与 partition 集合做交集。
    set_reshaped_targets = (  # 与 partition 集合求交前使用的 reshape 目标全集
        _pragma_array_targets(text, "ARRAY_RESHAPE")  # 读取源码 pragma 中声明的 ARRAY_RESHAPE 变量名
        | _directive_array_targets(text, "array_reshape")  # 读取配置 directive 中声明的 array_reshape 变量名
    )

    # 计算同时命中 partition 与 reshape 的变量名。
    list_conflict_names = sorted(set_partitioned_targets & set_reshaped_targets)  # 冲突变量名列表

    # 构造每个冲突变量对应的 error 诊断。
    return _array_conflict_issues(list_conflict_names, path)

# 提取 pragma ARRAY_PARTITION/ARRAY_RESHAPE 中的 variable 目标。
def _pragma_array_targets(text: str, pragma: str) -> set[str]:
    """返回指定数组 pragma 中声明的目标变量集合。

    参数:
        text: 需要扫描的源码文本。
        pragma: ARRAY_PARTITION 或 ARRAY_RESHAPE。

    返回:
        从 pragma variable= 片段中提取出的变量名集合。
    """

    # 构造只捕获 variable= 后变量名的 pragma 正则。
    str_pragma_pattern = (
        rf"#pragma\s+HLS\s+{re.escape(pragma)}\b"
        r"[^\n]*\bvariable\s*=\s*([A-Za-z_][A-Za-z0-9_]*)"
    )  # pragma 目标提取正则

    # 返回所有命中的目标变量名。
    return {regex_match.group(1) for regex_match in re.finditer(str_pragma_pattern, text, flags=re.IGNORECASE)}

# 提取 Tcl directive 配置中的数组目标变量。
def _directive_array_targets(text: str, directive: str) -> set[str]:
    """返回指定 directive 配置中声明的目标变量集合。

    参数:
        text: 需要扫描的配置文本。
        directive: array_partition 或 array_reshape。

    返回:
        从 directive 配置中提取出的变量名集合。
    """

    # 收集 directive 配置命中的目标变量。
    set_targets: set[str] = set()  # directive 声明的数组目标变量集合

    # 构造同时兼容 syn.directive 前缀与普通 directive 的正则。
    str_directive_pattern = (
        rf"\b(?:syn\.directive\.)?{re.escape(directive)}\s*=\s*"
        r"([A-Za-z_][A-Za-z0-9_:]*)\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)"
    )  # 同时提取层级路径与数组名的 directive 正则

    # 按 directive 配置命中项提取数组变量名，目标位于第二个捕获组。
    for regex_match in re.finditer(str_directive_pattern, text, flags=re.IGNORECASE):

        # 记录当前 directive 命中的数组变量名，供冲突检测去重。
        set_targets.add(regex_match.group(2))

    # 返回去重后的 directive 目标集合。
    return set_targets

# 解析 pragma 行里的显式 key=value 参数。
def _pragma_value(line: str, key: str) -> str:
    """返回 pragma 行内指定 key 的取值。

    参数:
        line: 单行 pragma 文本。
        key: 需要提取的参数名。

    返回:
        找到时返回参数值，否则返回空字符串。
    """

    # 查找 key=value 片段并捕获右侧简单标识符。
    match_text_pragma_value: re.Match[str] | None = re.search(rf"\b{re.escape(key)}\s*=\s*([A-Za-z0-9_]+)", line)  # 当前 pragma 行里命中的 key=value 参数对象

    # 未命中时返回空字符串，便于调用方使用 or 链接后备解析。
    return match_text_pragma_value.group(1) if match_text_pragma_value else ""

# 兜底提取 INTERFACE pragma 的位置参数模式。
def _pragma_interface_mode(line: str) -> str:
    """返回 INTERFACE pragma 的位置参数模式。

    参数:
        line: 单行 pragma 文本。

    返回:
        找到位置参数模式时返回模式名，否则返回空字符串。
    """

    # 捕获 INTERFACE 后第一个简单标识符作为 mode。
    match_text_interface_mode: re.Match[str] | None = re.search(r"#pragma\s+HLS\s+INTERFACE\s+([A-Za-z0-9_]+)", line)  # INTERFACE 位置参数模式命中对象

    # 未命中时返回空字符串，保持与 key=value 解析函数一致。
    return match_text_interface_mode.group(1) if match_text_interface_mode else ""

# 构造静态扫描诊断的统一字典结构。
def _issue(severity: str, message: str, path: str | None) -> dict[str, Any]:
    """返回 Vitis 规则扫描使用的诊断字典。

    参数:
        severity: 诊断严重级别。
        message: 人类可读诊断文本。
        path: 可选的文件路径。

    返回:
        包含 severity、message、path、stage 与 source 的诊断字典。
    """

    # 统一所有静态规则的诊断字段，方便调用方直接合并。
    return {
        "severity": severity,  # 诊断严重级别
        "message": message,  # 人类可读诊断文本
        "path": path,  # 诊断对应的文件路径
        "stage": "static",  # 诊断所属的静态扫描阶段
        "source": "current_module_issue",  # 诊断来源标识
    }

# 构造数组 partition/reshape 冲突的诊断列表。
def _array_conflict_issues(list_conflict_names: list[str], path: str | None) -> list[dict[str, Any]]:
    """返回数组指令冲突变量对应的诊断列表。

    参数:
        list_conflict_names: 同时应用 partition 与 reshape 的变量名列表。
        path: 可选的文件路径，用于诊断定位。

    返回:
        每个冲突变量对应一个 error 诊断。
    """

    # 收集冲突变量对应的诊断，避免长列表推导降低可读性。
    list_issues: list[dict[str, Any]] = []  # 数组指令冲突诊断列表

    # 为每个冲突变量生成独立诊断，便于上层精确定位。
    for str_name in list_conflict_names:

        # 生成包含变量名的稳定诊断文本。
        str_message = (
            "Do not apply both ARRAY_PARTITION and ARRAY_RESHAPE "
            f"to variable {str_name!r} in the same solution."
        )  # 数组指令冲突诊断文本

        # 将冲突诊断追加到本规则结果中。
        list_issues.append(_issue("error", str_message, path))

    # 返回冲突诊断列表。
    return list_issues
