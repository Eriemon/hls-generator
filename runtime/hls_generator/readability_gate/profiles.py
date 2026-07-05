"""定义 HLS 可读性规则的 profile 默认值与 current-project 覆盖入口。"""

# 延迟解析类型注解，避免运行时加载配置对象时产生前向引用负担。
from __future__ import annotations

# 标准库依赖集中在文件顶部，便于确认该配置模块没有导入期外部副作用。
import json
from dataclasses import dataclass, replace
from json import JSONDecodeError
from pathlib import Path
from typing import Any

# 把多行词表文本规整成不可变元组，避免长黑名单逐元素触发行内注释规则。
def _nonempty_lines_tuple(text: str) -> tuple[str, ...]:
    """把多行文本拆成去空白且去掉空行的字符串元组。

    参数:
        text: 以多行文本书写的默认词表内容。
    返回:
        tuple[str, ...]，保留原顺序且每项都已去掉首尾空白。
    """

    # 过滤空行并逐项去空白，保证词表在 JSON 导出和比较时都保持稳定。
    return tuple(str_line.strip() for str_line in text.splitlines() if str_line.strip())

# 空泛注释黑名单用于识别生成器常见的占位说明，阻止无语义注释通过门禁。
DEFAULT_GENERIC_COMMENT_PHRASES = _nonempty_lines_tuple(  # 低语义注释短语黑名单
    """
    generic generated line
    not hardware intent
    keep the generated hls artifact line reviewable
    preserve the generated data movement or computation step
    open or close the generated hardware scope
    do the operation
    perform operation
    process data
    handle data
    main logic
    important code
    generated code
    this line
    定义xxx
    定义变量
    保存变量
    保存结果
    初始化变量
    初始化结果
    计算结果
    处理数据
    主要逻辑
    重要代码
    下方代码
    下方逻辑
    代码块功能
    说明代码
    说明下方代码
    执行函数
    调用函数
    返回结果
    赋值变量
    判断当前分支
    供后续逻辑使用
    当前步骤
    模板
    占位
    """,
)  # 默认拦截的空泛或模板化注释短语

# 过短语义片段用于捕获看似中文、实际无法解释代码目的的注释。
DEFAULT_VAGUE_COMMENT_PHRASES = _nonempty_lines_tuple(  # 过短中文注释片段黑名单
    """
    值
    代码
    结果
    数据
    内容
    逻辑
    步骤
    变量
    函数
    参数
    对象
    """,
)  # 默认拦截的过短中文注释片段

# 特殊语句类型定义了 HLS 文本扫描器需要强制检查上下文注释的代码形态。
DEFAULT_SPECIAL_STATEMENT_KINDS = _nonempty_lines_tuple(  # HLS 结构化语句类型清单
    """
    include
    macro
    typedef
    function_signature
    pragma
    function_call
    assignment_with_function_call
    for
    if
    while
    switch
    return
    try
    catch
    assert
    """,
)  # 需要检查前置说明的特殊语句类型

# HLS profile 配置对象承载检查开关、阈值和注释质量词表。
@dataclass(frozen=True)
class HlsProfileConfig:
    """
    保存一个 HLS 可读性 profile 的阈值、开关和注释质量词表。

    参数:
        name: profile 名称，作为 PROFILE_DEFAULTS 的稳定索引。
    返回:
        dataclass 实例由调用方读取，类定义本身不执行额外运行时动作。
    """

    # profile 名称需要进入报告与导出字典，保持与用户选择一致。
    name: str  # HLS profile 稳定名称

    # 中文注释开关决定 current-project 是否强制检查注释语言。
    require_chinese_comments: bool = True  # 是否要求中文语义注释

    # 文件头开关控制生成 HLS 源文件时是否必须描述模块用途。
    require_file_header: bool = True  # 是否要求文件级中文说明

    # 空行块注释开关对应 current-project 的 PG031 结构解释要求。
    require_blank_line_comments: bool = True  # 是否要求空行后代码块说明

    # 特殊语句间距开关对应分支、循环和返回等语句的语义护栏。
    require_special_statement_spacing: bool = True  # 是否要求特殊语句前置说明

    # 声明上方注释开关要求变量或端口声明具备可读目的说明。
    require_declaration_above_comment: bool = True  # 是否要求声明上方目的注释

    # 声明右侧注释开关要求关键声明行直接暴露硬件语义。
    require_declaration_inline_comment: bool = True  # 是否要求声明右侧中文注释

    # 右侧注释最大代码宽度用于限制过长声明行继续追加注释。
    inline_comment_max_code_chars: int = 130  # 允许右侧注释的代码长度上限

    # 多行声明豁免允许复杂签名将说明放到更适合的上方注释中。
    allow_multiline_inline_comment_exemption: bool = True  # 是否允许多行声明跳过右侧注释

    # 函数契约开关要求 HLS 函数说明参数、端口和返回语义。
    require_function_contract: bool = True  # 是否要求函数级契约说明

    # 顶层端口契约开关强调 kernel 对外接口必须可审查。
    require_top_port_contract: bool = True  # 是否要求顶层端口契约

    # pragma 意图开关避免流水线、展开和接口指令缺少硬件原因。
    require_pragma_hardware_intent: bool = True  # 是否要求 pragma 硬件意图说明

    # 循环意图开关要求循环边界与并行策略说明清楚。
    require_loop_intent: bool = True  # 是否要求循环用途说明

    # 函数长度警告阈值用于提示 profile 文件过度聚合的维护风险。
    warn_function_lines: int = 80  # 函数行数警告阈值

    # 函数长度阻断阈值用于防止生成过长且难审查的 HLS 函数。
    block_function_lines: int = 140  # 函数行数阻断阈值

    # 嵌套深度警告阈值用于提示分支层级已经影响阅读。
    warn_nested_depth: int = 4  # 嵌套深度警告阈值

    # 嵌套深度阻断阈值用于拒绝过深控制流继续交付。
    block_nested_depth: int = 6  # 嵌套深度阻断阈值

    # 分支循环警告阈值用于提示函数控制流开始变复杂。
    warn_branch_loop_count: int = 12  # 分支和循环数量警告阈值

    # 分支循环阻断阈值用于拒绝过多控制流混在一个函数内。
    block_branch_loop_count: int = 22  # 分支和循环数量阻断阈值

    # 魔法数字警告阈值用于提示数值常量应提升为语义参数。
    warn_magic_number_count: int = 6  # 魔法数字数量警告阈值

    # 魔法数字阻断阈值用于防止硬件参数散落在实现细节中。
    block_magic_number_count: int = 12  # 魔法数字数量阻断阈值

    # 参数数量警告阈值用于提示函数接口需要重新组织。
    warn_parameter_count: int = 10  # 函数参数数量警告阈值

    # 参数数量阻断阈值用于拒绝难以调用和验证的超宽接口。
    block_parameter_count: int = 18  # 函数参数数量阻断阈值

    # 注释掉代码的警告阈值用于提示文件里可能残留调试片段。
    warn_commented_code_lines: int = 4  # 注释代码行数警告阈值

    # 注释掉代码的阻断阈值用于拒绝把旧实现留在交付源码中。
    block_commented_code_lines: int = 10  # 注释代码行数阻断阈值

    # 最大行宽限制 current-project 报告中难以审查的横向代码。
    max_line_length: int = 160  # 单行字符数上限

    # 特殊语句清单决定 HLS 源码扫描时要检查哪些结构。
    special_statement_kinds: tuple[str, ...] = DEFAULT_SPECIAL_STATEMENT_KINDS  # 特殊语句类型

    # 空泛注释词表由 profile 或 current-project 配置覆盖。
    generic_comment_phrases: tuple[str, ...] = DEFAULT_GENERIC_COMMENT_PHRASES  # 空泛注释词表

    # 过短注释词表用于识别无法解释意图的中文片段。
    vague_comment_phrases: tuple[str, ...] = DEFAULT_VAGUE_COMMENT_PHRASES  # 过短注释词表

    # 导出配置时将不可变 tuple 转换为 JSON 友好的 list。
    def to_dict(self) -> dict[str, Any]:
        """
        将 profile 配置导出为可序列化的字典。

        参数:
            无；方法只读取当前 dataclass 实例的字段。
        返回:
            dict[str, Any]，包含 profile 名称、规则开关、阈值和注释词表。
        """

        # 字典字段保持与 dataclass 字段同名，便于 CLI 报告和测试断言复用。
        return {  # JSON 友好的 profile 配置快照
            "name": self.name,
            "require_chinese_comments": self.require_chinese_comments,
            "require_file_header": self.require_file_header,
            "require_blank_line_comments": self.require_blank_line_comments,
            "require_special_statement_spacing": self.require_special_statement_spacing,
            "require_declaration_above_comment": self.require_declaration_above_comment,
            "require_declaration_inline_comment": self.require_declaration_inline_comment,
            "inline_comment_max_code_chars": self.inline_comment_max_code_chars,
            "allow_multiline_inline_comment_exemption": self.allow_multiline_inline_comment_exemption,
            "require_function_contract": self.require_function_contract,
            "require_top_port_contract": self.require_top_port_contract,
            "require_pragma_hardware_intent": self.require_pragma_hardware_intent,
            "require_loop_intent": self.require_loop_intent,
            "warn_function_lines": self.warn_function_lines,
            "block_function_lines": self.block_function_lines,
            "warn_nested_depth": self.warn_nested_depth,
            "block_nested_depth": self.block_nested_depth,
            "warn_branch_loop_count": self.warn_branch_loop_count,
            "block_branch_loop_count": self.block_branch_loop_count,
            "warn_magic_number_count": self.warn_magic_number_count,
            "block_magic_number_count": self.block_magic_number_count,
            "warn_parameter_count": self.warn_parameter_count,
            "block_parameter_count": self.block_parameter_count,
            "warn_commented_code_lines": self.warn_commented_code_lines,
            "block_commented_code_lines": self.block_commented_code_lines,
            "max_line_length": self.max_line_length,
            "special_statement_kinds": list(self.special_statement_kinds),
            "generic_comment_phrases": list(self.generic_comment_phrases),
            "vague_comment_phrases": list(self.vague_comment_phrases),
        }  # 当前 profile 配置的可序列化字典快照

# 用独立工厂函数集中构造默认 profile 映射，避免模块级大字典逐元素触发行内注释规则。
def _build_profile_defaults() -> dict[str, HlsProfileConfig]:
    """构造内置 HLS profile 默认配置映射。

    参数:
        无；配置内容完全来自模块内置默认值。
    返回:
        dict[str, HlsProfileConfig]，覆盖 kernel、header、testbench 等常用 HLS 场景。
    """

    # 不同 HLS 场景的差异化阈值集中放在同一个返回字典里，便于统一审查。
    return {
        "kernel": HlsProfileConfig("kernel"),
        "header": HlsProfileConfig(
            "header",
            warn_function_lines=120,
            block_function_lines=220,
            require_declaration_inline_comment=False,
            require_top_port_contract=False,
        ),
        "testbench": HlsProfileConfig(
            "testbench",
            warn_function_lines=120,
            block_function_lines=220,
            require_top_port_contract=False,
            warn_branch_loop_count=18,
            block_branch_loop_count=32,
        ),
        "streaming": HlsProfileConfig(
            "streaming",
            warn_function_lines=90,
            block_function_lines=160,
        ),
        "dataflow": HlsProfileConfig(
            "dataflow",
            warn_function_lines=100,
            block_function_lines=180,
        ),
        "host_stub": HlsProfileConfig(
            "host_stub",
            require_top_port_contract=False,
            warn_function_lines=120,
            block_function_lines=220,
            require_declaration_inline_comment=False,
        ),
    }  # 内置 profile 默认配置映射

# 默认 profile 集合覆盖 kernel、header、testbench 和不同 HLS 结构重点场景。
PROFILE_DEFAULTS: dict[str, HlsProfileConfig] = _build_profile_defaults()  # profile 名称到默认配置的映射表

# 对外提供稳定排序的 profile 名称，供 CLI 参数提示和测试枚举使用。
def list_profiles() -> tuple[str, ...]:
    """
    返回当前内置 HLS profile 名称。

    参数:
        无；profile 名称直接来自模块级默认映射。
    返回:
        tuple[str, ...]，按字母序排列的 profile 名称。
    """

    # 排序后返回 tuple，避免调用方依赖字典插入顺序或修改结果。
    return tuple(sorted(PROFILE_DEFAULTS))

# 根据用户选择和 current-project 覆盖文件生成实际检查配置。
def get_hls_profile_config(
    profile: str = "kernel",
    *,
    style: str = "current-project",
    style_config_path: str | Path | None = None,
) -> HlsProfileConfig:
    """
    读取指定 HLS profile，并在 current-project 模式下叠加本项目配置。

    参数:
        profile: 用户请求的 profile 名称；空值回退到 kernel。
        style: 质量门风格名称；只有 current-project 会加载项目覆盖配置。
        style_config_path: 可选的 current-project JSON 配置路径。
    返回:
        HlsProfileConfig，表示最终用于 HLS 可读性检查的配置。
    """

    # profile 输入统一小写，保证 CLI 大小写差异不会改变选择结果。
    str_normalized_profile: str = str(profile or "kernel").strip().lower()  # 归一化 profile 名称

    # 未注册 profile 明确回退到 kernel，保持旧版调用方的宽容行为。
    hls_profile_config_selected: HlsProfileConfig = PROFILE_DEFAULTS.get(  # 当前请求匹配到的基础配置
        str_normalized_profile,  # 归一化后的 profile 名称
        PROFILE_DEFAULTS["kernel"],  # 未命中时回退的 kernel 默认配置
    )

    # 只有 current-project 需要读取仓库级 JSON 覆盖，其他风格使用内置默认值。
    if style == "current-project":

        # 覆盖后的配置仍返回新的冻结 dataclass，避免修改全局默认值。
        hls_profile_config_selected = _apply_current_project_style(  # 叠加项目规则后的配置
            hls_profile_config_selected,  # 当前选中的基础 profile 配置
            style_config_path,  # 可选的 current-project 配置文件路径
        )

    # 返回最终配置对象，调用方负责把它传给具体规则检查器。
    return hls_profile_config_selected

# 将 current-project JSON 中的检查开关和阈值合并到基础 profile。
def _apply_current_project_style(
    config: HlsProfileConfig,
    style_config_path: str | Path | None,
) -> HlsProfileConfig:
    """
    读取 current-project 配置并覆盖基础 HLS profile。

    参数:
        config: 已选中的基础 HLS profile 配置。
        style_config_path: 可选的 JSON 配置路径；为空时使用技能内置位置。
    返回:
        HlsProfileConfig，包含 JSON 覆盖后的规则开关、阈值和注释词表。
    """

    # JSON 载荷为空时保持基础 profile，兼容缺少配置文件的安装包。
    dict_payload: dict[str, Any] = _load_style_config(style_config_path)  # checks、thresholds 与注释词表覆盖载荷

    # 缺省配置不应中断质量门，直接返回调用方传入的冻结配置。
    if not dict_payload:

        # 返回原配置对象，不制造额外副本，便于测试通过对象相等性判断。
        return config

    # 将 JSON 顶层分区规整为字典，避免错误类型污染后续转换。
    tuple_style_sections = _style_config_sections(dict_payload)  # 已按区块顺序整理好的配置覆盖三元组

    # 第一段区块只保存布尔检查开关覆盖。
    dict_checks: dict[str, Any] = tuple_style_sections[0]  # 布尔规则开关覆盖区块

    # 第二段区块只保存数值阈值覆盖。
    dict_thresholds: dict[str, Any] = tuple_style_sections[1]  # 数值阈值覆盖区块

    # 第三段区块只保存注释词表覆盖。
    dict_comment_quality: dict[str, Any] = tuple_style_sections[2]  # 注释质量覆盖区块

    # 空泛短语配置为空时沿用基础 profile，确保黑名单不会被意外清空。
    tuple_generic_phrases = _comment_phrase_override(  # 合并后的空泛注释词表
        dict_comment_quality,  # 当前项目 JSON 中的注释质量配置分区
        "banned_generic_comment_phrases",  # 空泛注释短语的配置字段名
        config.generic_comment_phrases,  # 基础 profile 自带的空泛短语词表
    )

    # 过短短语配置为空时沿用基础 profile，保持 PG037 的默认语义密度。
    tuple_vague_phrases = _comment_phrase_override(  # 合并后的过短注释词表
        dict_comment_quality,  # 注释黑名单覆盖来源的 comment_quality 子字典
        "banned_vague_comment_phrases",  # 过短注释短语的配置字段名
        config.vague_comment_phrases,  # 基础 profile 自带的过短短语词表
    )

    # replace 保留 dataclass 冻结语义，同时只替换 JSON 明确声明的字段。
    return _replace_current_project_config(
        config,
        dict_checks,
        dict_thresholds,
        tuple_generic_phrases,
        tuple_vague_phrases,
    )

# 提取 current-project JSON 的顶层配置区块。
def _style_config_sections(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """返回 current-project 覆盖文件中的三类有效区块。

    参数:
        payload: 已解析的 current-project JSON 顶层载荷。

    返回:
        checks、thresholds 和 comment_quality 三个字典；类型不匹配时对应为空字典。
    """

    # 返回三类覆盖区块，调用方再按检查开关、阈值和注释质量分别消费。
    return (
        _dict_section(payload, "checks"),  # 供规则开关合并阶段读取的 checks 子字典
        _dict_section(payload, "thresholds"),  # 供结构阈值合并阶段读取的 thresholds 子字典
        _dict_section(payload, "comment_quality"),  # 供禁用短语合并阶段读取的 comment_quality 子字典
    )

# 从 JSON 顶层载荷中读取一个字典区块。
def _dict_section(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """安全读取 current-project JSON 中的字典区块。

    参数:
        payload: 已解析的 current-project JSON 顶层载荷。
        key: 需要读取的顶层字段名。

    返回:
        字段值是字典时返回该字典，否则返回空字典。
    """

    # 原始字段值可能来自用户编辑的 JSON，需要先保守验证类型。
    dict_section_candidate = payload.get(key, {})  # 等待类型确认的顶层配置区块

    # 只有真实字典才能参与后续 bool/int 转换。
    if isinstance(dict_section_candidate, dict):

        # 返回原字典，保持 JSON 字段和值不做额外复制或改名。
        return dict_section_candidate

    # 类型不匹配时使用空覆盖区块，沿用基础 profile 默认值。
    return {}

# 合并 current-project 注释短语覆盖。
def _comment_phrase_override(
    dict_comment_quality: dict[str, Any],
    key: str,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    """读取注释短语覆盖，缺省时保留基础 profile 词表。

    参数:
        dict_comment_quality: comment_quality 配置区块。
        key: 需要读取的短语字段名。
        fallback: 字段缺失或为空时沿用的基础词表。

    返回:
        tuple[str, ...]，用于冻结到 HlsProfileConfig。
    """

    # 空列表和缺失字段都表示不覆盖，避免误清空质量门黑名单。
    tuple_phrases = tuple(dict_comment_quality.get(key) or fallback)  # 冻结后的注释短语词表

    # tuple 保持 dataclass 字段不可变，调用方不能原地改写 profile。
    return tuple_phrases

# 用 current-project 覆盖值创建新的 HLS profile 配置。
def _replace_current_project_config(
    config: HlsProfileConfig,
    dict_checks: dict[str, Any],
    dict_thresholds: dict[str, Any],
    tuple_generic_phrases: tuple[str, ...],
    tuple_vague_phrases: tuple[str, ...],
) -> HlsProfileConfig:
    """把 current-project 覆盖值写入新的冻结配置对象。

    参数:
        config: 已选中的基础 HLS profile 配置。
        dict_checks: 布尔检查开关覆盖区块。
        dict_thresholds: 数值阈值覆盖区块。
        tuple_generic_phrases: 合并后的空泛注释词表。
        tuple_vague_phrases: 合并后的过短注释词表。

    返回:
        HlsProfileConfig，包含 current-project 覆盖后的配置。
    """

    # 在一个 replace 调用里同时写回布尔开关、阈值和短语词表，避免遗漏任何覆盖项。
    return replace(
        config,
        require_chinese_comments=bool(
            dict_checks.get("require_chinese_comments", config.require_chinese_comments),
        ),
        require_declaration_inline_comment=bool(
            dict_checks.get(
                "require_declaration_inline_comment",
                config.require_declaration_inline_comment,
            ),
        ),
        require_declaration_above_comment=bool(
            dict_checks.get(
                "require_declaration_above_comment",
                config.require_declaration_above_comment,
            ),
        ),
        require_top_port_contract=bool(
            dict_checks.get("require_top_port_contract", config.require_top_port_contract),
        ),
        inline_comment_max_code_chars=int(
            dict_thresholds.get("inline_comment_max_code_chars", config.inline_comment_max_code_chars),
        ),
        warn_function_lines=int(
            dict_thresholds.get("warn_function_lines", config.warn_function_lines),
        ),
        block_function_lines=int(
            dict_thresholds.get("block_function_lines", config.block_function_lines),
        ),
        warn_nested_depth=int(
            dict_thresholds.get("warn_nested_depth", config.warn_nested_depth),
        ),
        block_nested_depth=int(
            dict_thresholds.get("block_nested_depth", config.block_nested_depth),
        ),
        warn_branch_loop_count=int(
            dict_thresholds.get("warn_branch_loop_count", config.warn_branch_loop_count),
        ),
        block_branch_loop_count=int(
            dict_thresholds.get("block_branch_loop_count", config.block_branch_loop_count),
        ),
        warn_magic_number_count=int(
            dict_thresholds.get("warn_magic_number_count", config.warn_magic_number_count),
        ),
        block_magic_number_count=int(
            dict_thresholds.get("block_magic_number_count", config.block_magic_number_count),
        ),
        generic_comment_phrases=tuple_generic_phrases,
        vague_comment_phrases=tuple_vague_phrases,
    )

# 默认配置路径固定指向技能内的 current-project HLS 风格 JSON。
def default_style_config_path() -> Path:
    """
    返回内置 current-project HLS 风格配置文件路径。

    参数:
        无；路径通过当前模块位置推导。
    返回:
        Path，指向 references/style/current_project_hls_style_config.json。
    """

    # parents[3] 对应 skills/erie-hls-generator 技能根目录。
    path_skill_root: Path = Path(__file__).resolve().parents[3]  # 技能根目录路径

    # 配置文件跟随技能一起发布，避免依赖用户工作目录。
    return path_skill_root / "references" / "style" / "current_project_hls_style_config.json"

# 从显式路径或默认路径读取 current-project 风格覆盖 JSON。
def _load_style_config(style_config_path: str | Path | None) -> dict[str, Any]:
    """
    加载 current-project 风格配置 JSON。

    参数:
        style_config_path: 可选配置路径；为空时使用默认技能内置路径。
    返回:
        dict[str, Any]，解析成功时包含配置载荷；文件缺失或解析失败时为空字典。
    """

    # 先解析最终路径，保证后续存在性检查和读取使用同一个目标。
    path_style_config: Path = Path(style_config_path) if style_config_path else default_style_config_path()  # 实际读取的风格配置路径

    # 安装包缺少覆盖配置时保持空载荷，调用方会沿用默认 profile。
    if not path_style_config.exists():

        # 文件不存在不是运行错误，返回空字典维持向后兼容。
        return {}

    # 只捕获文件读取和 JSON 解析错误，避免吞掉其他编程错误。
    try:

        # 读取为字符串后再解析，便于将来在同一位置补充诊断。
        str_config_text: str = path_style_config.read_text(encoding="utf-8")  # JSON 配置原文

        # 返回解析后的映射载荷，类型细分由上层合并逻辑判断。
        return json.loads(str_config_text)

    # 配置文件损坏时回退为空载荷，质量门继续使用内置默认值。
    except (OSError, JSONDecodeError):

        # 返回空字典而不是抛错，保持旧版本缺省配置的容错行为。
        return {}
