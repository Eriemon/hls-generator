"""定义质量门 profile 阈值并加载 current-project 风格配置。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# 列出 current-project 下需要 PG032 前置说明的语句类型。
DEFAULT_SPECIAL_STATEMENT_KINDS = (  # current-project 风格要求补充空行与说明注释的语句类型清单
    "function_call",  # 裸函数调用前需要说明副作用或流程目的
    "assignment_with_function_call",  # 函数返回值赋值前同样要解释调用语义
    "with",  # with 语句前需要说明资源上下文
    "try",  # try 语句前需要说明异常边界
    "assert",  # assert 语句前需要说明不变量校验目的
    "for",  # for 循环前需要说明迭代语义
    "if",  # if 分支前需要说明条件判断依据
    "while",  # while 循环前需要说明持续条件
    "return",  # return 语句前需要说明返回结果用途
)

# 列出会被 PG036 判为模板化占位注释的固定短语。
DEFAULT_GENERIC_COMMENT_PHRASES = (  # PG036 禁用的泛化模板注释短语
    "定义变量",  # 只复述定义动作的占位短语
    "定义xxx",  # 含未替换占位符的模板短语
    "保存变量",  # 未说明变量用途的泛化短语
    "保存结果",  # 未说明结果去向的泛化短语
    "初始化变量",  # 只描述初始化动作的占位短语
    "初始化结果",  # 未说明初始值语义的泛化短语
    "计算结果",  # 未解释计算含义的泛化短语
    "处理数据",  # 未说明数据角色的泛化短语
    "下方代码块",  # 只指代下方代码块本身的短语
    "下方代码",  # 只指代下方代码而未说明职责的短语
    "下方逻辑",  # 只指代下方逻辑而未说明意图的短语
    "代码块功能",  # 未说明具体功能的占位短语
    "执行函数",  # 只描述调用动作的短语
    "调用函数",  # 未说明副作用或结果的短语
    "返回结果",  # 未说明返回值含义的短语
    "赋值变量",  # 只复述赋值动作的短语
    "说明代码",  # 不包含具体语义的说明类短语
    "说明下方代码",  # 未解释下方职责的空泛说明短语
)

# 列出会被 PG037 视为名词式空泛说明的短词。
DEFAULT_VAGUE_COMMENT_PHRASES = (  # PG037 禁用的含糊名词注释短语
    "变量",  # 单独写变量无法说明用途
    "结果",  # 单独写结果无法说明去向
    "数据",  # 单独写数据无法说明角色
    "内容",  # 单独写内容无法说明语义
    "对象",  # 单独写对象无法说明职责
    "参数",  # 单独写参数无法说明含义
    "函数",  # 单独写函数无法说明调用目的
    "代码",  # 单独写代码无法说明逻辑职责
    "列表",  # 单独写列表无法说明元素语义
    "字典",  # 单独写字典无法说明键值角色
    "矩阵",  # 单独写矩阵无法说明数学含义
    "向量",  # 单独写向量无法说明元素意义
    "路径",  # 单独写路径无法说明文件角色
    "配置",  # 单独写配置无法说明控制作用
    "值",  # 单独写值无法说明上下文用途
    "下方代码",  # 只指代下方代码的空泛短语
    "下方逻辑",  # 只指代下方逻辑的空泛短语
    "代码逻辑",  # 只泛指逻辑而没有具体职责
    "业务逻辑",  # 只泛指业务而没有领域对象
    "块功能",  # 只说功能块而没有实际用途
)

# current-project 配置缺失或损坏时使用这份内置兜底。
DEFAULT_CURRENT_PROJECT_STYLE_CONFIG: dict[str, Any] = {  # 配置文件不可用时回退的 current-project 默认规则映射
    "spacing": {  # 空行分隔与特殊语句间距配置
        "require_blank_line_comments": True,  # 普通代码块之间要求空行与中文说明
        "require_control_spacing": True,  # 控制语句之间要求空行分隔
        "require_special_statement_spacing": True,  # 特殊语句之前要求独立说明块
        "special_statement_kinds": list(DEFAULT_SPECIAL_STATEMENT_KINDS),  # 需要 PG032 处理的语句类型清单
        "max_dense_code_lines": 8,  # 连续无说明密集代码的默认阈值
    },
    "assignment_comments": {  # 赋值上下方中文注释要求
        "require_inline_chinese_comment": True,  # 赋值右侧尾注默认开启
        "require_above_chinese_comment": True,  # 赋值上方说明默认开启
        "banned_generic_comment_phrases": list(DEFAULT_GENERIC_COMMENT_PHRASES),  # PG036 使用的模板短语黑名单
    },
    "comment_quality": {  # 注释具体性、长度和禁用短语配置
        "require_specific_purpose_comments": True,  # 关键注释默认要求具体用途
        "min_inline_assignment_chinese_chars": 4,  # 赋值尾注默认最少中文字符数
        "min_above_assignment_chinese_chars": 5,  # 赋值上方说明默认最少中文字符数
        "min_block_comment_chinese_chars": 4,  # 空行说明块默认最少中文字符数
        "allow_short_import_group_comments": True,  # 导入分组说明允许较短中文注释
        "banned_vague_comment_phrases": list(DEFAULT_VAGUE_COMMENT_PHRASES),  # PG037 使用的含糊短语黑名单
    },
    "language": {  # 注释语言约束配置
        "require_chinese_comments": True,  # 默认要求普通注释使用中文
    },
}

# 声明下方对象需要的装饰器约束
@dataclass(frozen=True)
class ProfileConfig:
    """保存profile配置配置字段。"""

    # profile 名称会写入报告，帮助用户确认启用的规则组合。
    name: str  # 报告中展示的规则组合名称

    # 函数源码行数超过该值时提示拆分。
    warn_function_lines: int  # 函数行数 warning 阈值

    # 函数源码行数超过该值时阻断质量门。
    block_function_lines: int  # 函数长度达到 blocker 时必须拆分的源码行数上限

    # 控制流嵌套超过该深度时提示降低复杂度。
    warn_nested_depth: int  # 嵌套深度 warning 阈值

    # 控制流嵌套超过该深度时阻断质量门。
    block_nested_depth: int  # 控制流嵌套达到 blocker 时必须削平的层数上限

    # 分支数量超过该值时提示拆分判断逻辑。
    warn_branch_count: int  # 分支数量 warning 阈值

    # 分支数量超过该值时阻断质量门。
    block_branch_count: int  # 条件分支达到 blocker 时必须收敛判断路径的数量上限

    # 函数内未命名数值超过该值时提示提取配置。
    warn_magic_number_count: int  # 魔法数 warning 阈值

    # 函数内未命名数值超过该值时阻断质量门。
    block_magic_number_count: int  # 未命名数字达到 blocker 时必须提取常量的数量上限

    # 参数数量超过该值时先给 warning，提醒收敛接口职责。
    warn_parameter_count: int = 7  # warning 级参数数量阈值

    # 参数数量继续增加时升级为 blocker，要求拆分接口面。
    block_parameter_count: int = 12  # 参数过多到阻断时允许的最大接口参数数

    # 超过该行宽时报告 warning，提醒拆分过长表达式。
    warn_line_length: int = 120  # warning 级源码行宽阈值

    # 超过该行宽时报告 blocker，要求拆分不可读长行。
    block_line_length: int = 160  # 单行源码超过此长度后直接按 blocker 处理

    # 连续注释块过长时先提示拆分，避免说明和代码脱节。
    warn_comment_block: int = 10  # warning 级连续注释块行数阈值

    # 连续注释块过长到影响可读性时直接阻断。
    block_comment_block: int = 25  # 连续说明块超过此长度后按 blocker 处理

    # profile 可以关闭该项，为脚本类代码保留更轻量的类型约束。
    require_type_hints: bool = True  # 是否强制变量、参数和返回值提供类型提示

    # 科学计算类函数可借此要求数组参数补齐 shape/dtype 说明。
    require_array_docstring: bool = True  # 是否强制数组相关 docstring 说明

    # 库式接口通常要求对外公开函数补齐完整 docstring 契约。
    require_public_docstrings: bool = False  # 是否强制公开 API 提供 docstring

    # profile 允许 print 时，脚本入口可保留终端输出。
    allow_print: bool = False  # print 调用允许开关

    # profile 允许绘图展示时，脚本或 notebook 可调用 plt.show。
    allow_plot_show: bool = False  # plt.show 允许开关

    # profile 允许导入期副作用时，脚本类文件可做入口初始化。
    allow_import_side_effects: bool = False  # 导入期副作用豁免开关

    # 脚本与 notebook profile 可按需放宽源码中的硬编码路径限制。
    allow_hardcoded_path: bool = False  # 硬编码路径豁免开关

    # current-project 覆盖层开启后要求普通注释使用中文。
    require_chinese_comments: bool = False  # 是否要求中文注释

    # 普通代码块之间是否必须用空行和中文说明拉开语义边界。
    require_blank_line_comments: bool = False  # 普通代码块空行说明要求开关

    # current-project 覆盖层要求控制语句前有空行分隔。
    require_control_spacing: bool = False  # 是否要求控制语句间隔

    # 赋值语句是否必须携带右侧中文用途注释。
    require_assignment_comments: bool = False  # 赋值尾注要求开关

    # 赋值语句是否必须携带上方中文目的注释。
    require_assignment_block_comments: bool = False  # 赋值上方说明要求开关

    # current-project 覆盖层要求调用、with、try、return 等特殊语句前空行。
    require_special_statement_spacing: bool = False  # 是否要求特殊语句间隔

    # 特殊语句类型列表来自风格配置，决定 PG032 检查范围。
    special_statement_kinds: tuple[str, ...] = ()  # 需要空行分隔的语句类型

    # 这些短语一旦出现在注释里，会被 PG036 视为模板化占位说明。
    banned_generic_comment_phrases: tuple[str, ...] = ()  # PG036 使用的泛化短语黑名单

    # 关键注释是否必须说明具体用途，而不是只写名词标签。
    require_specific_purpose_comments: bool = False  # PG037 具体性检查开关

    # 赋值尾注至少要达到这段中文信息量才算解释到位。
    min_inline_assignment_comment_chinese_chars: int = 0  # 赋值尾注最低中文字符数

    # 赋值上方说明至少要达到这段中文信息量才算解释到位。
    min_above_assignment_comment_chinese_chars: int = 0  # 赋值上方说明最低中文字符数

    # 空行后说明块至少要达到这段中文信息量才算具备语义价值。
    min_block_comment_chinese_chars: int = 0  # 代码块说明最低中文字符数

    # 导入分组说明可按需使用较短中文注释，而不强求普通说明长度。
    allow_short_import_group_comments: bool = True  # 导入分组短注释豁免开关

    # 这些短语会被 PG037 逐字比对为名词式空泛说明。
    banned_vague_comment_phrases: tuple[str, ...] = ()  # PG037 直接匹配的空泛名词短语集合

    # 连续无说明的密集代码超过该长度时提示拆分或补充语义边界。
    max_dense_code_lines: int = 0  # 密集代码连续行数阈值

    # current-project 风格启用后，风格问题按 blocker 处理。
    strict_project_style: bool = False  # 是否启用严格项目风格

# 内置 profile 定义不同使用场景下的复杂度阈值和豁免策略。
PROFILE_CONFIGS: dict[str, ProfileConfig] = {  # 按 profile 名称查找复杂度阈值与风格豁免的注册表
    "library": ProfileConfig(  # 通用库代码的默认复杂度与文档阈值
        name="library",  # 面向通用库代码的 profile 注册名
        warn_function_lines=55,  # 库函数超过 55 行时先提示拆分
        block_function_lines=95,  # 库函数超过 95 行时直接阻断
        warn_nested_depth=3,  # 库代码嵌套超过 3 层时给出 warning
        block_nested_depth=5,  # 库代码嵌套超过 5 层时升级为 blocker

        # 分支与魔法数阈值延续库代码的保守治理策略。
        warn_branch_count=10,  # 库代码分支超过 10 个时提示收敛判断
        block_branch_count=18,  # 库代码分支超过 18 个时直接阻断
        warn_magic_number_count=8,  # 库代码未命名数字超过 8 个时提示提取常量
        block_magic_number_count=16,  # 库代码未命名数字超过 16 个时直接阻断

        # 库式 profile 要求公开 API 默认补齐 docstring。
        require_public_docstrings=True,  # 库代码公开 API 默认要求完整 docstring
    ),
    "scientific": ProfileConfig(  # 数值与科研代码的默认复杂度阈值
        name="scientific",  # 面向科研与数值代码的 profile 注册名
        warn_function_lines=85,  # 科研函数超过 85 行时先提示拆分
        block_function_lines=150,  # 科研函数超过 150 行时直接阻断
        warn_nested_depth=4,  # 科研代码嵌套超过 4 层时给出 warning
        block_nested_depth=6,  # 科研代码嵌套超过 6 层时升级为 blocker

        # 科研代码允许略高复杂度，但仍限制分支和魔法数膨胀。
        warn_branch_count=14,  # 科研代码分支超过 14 个时提示收敛判断
        block_branch_count=24,  # 科研代码分支超过 24 个时直接阻断
        warn_magic_number_count=12,  # 科研代码未命名数字超过 12 个时提示抽常量
        block_magic_number_count=24,  # 科研代码未命名数字超过 24 个时直接阻断

        # 科研 profile 仍要求公开 API 提供 docstring 契约。
        require_public_docstrings=True,  # 科研代码对外接口默认要求 docstring 契约
    ),
    "script": ProfileConfig(  # 一次性脚本与工具脚本的默认豁免组合
        name="script",  # 面向一次性脚本的 profile 注册名
        warn_function_lines=110,  # 脚本函数超过 110 行时先提示拆分
        block_function_lines=220,  # 脚本函数超过 220 行时直接阻断
        warn_nested_depth=4,  # 脚本控制流嵌套超过 4 层时给出 warning
        block_nested_depth=7,  # 脚本控制流嵌套超过 7 层时升级为 blocker

        # 脚本型代码允许更高复杂度和更多未命名数字。
        warn_branch_count=18,  # 脚本分支超过 18 个时提示收敛判断
        block_branch_count=36,  # 脚本分支超过 36 个时直接阻断
        warn_magic_number_count=20,  # 脚本未命名数字超过 20 个时提示提取常量
        block_magic_number_count=48,  # 脚本未命名数字超过 48 个时直接阻断

        # 脚本 profile 放宽类型提示与数组 docstring 要求。
        require_type_hints=False,  # 脚本默认不强制完整类型提示
        require_array_docstring=False,  # 脚本默认放宽数组 docstring 契约

        # 工具脚本允许终端输出、绘图展示和硬编码路径。
        allow_print=True,  # 脚本可直接向终端打印过程信息
        allow_plot_show=True,  # 脚本可直接弹出绘图窗口
        allow_hardcoded_path=True,  # 脚本允许保留本地路径常量
    ),
    "cli": ProfileConfig(  # 终端 CLI 模块的默认复杂度与输出豁免
        name="cli",  # 面向命令行工具的 profile 注册名
        warn_function_lines=80,  # CLI 函数超过 80 行时先提示拆分
        block_function_lines=160,  # CLI 函数超过 160 行时直接阻断
        warn_nested_depth=4,  # CLI 控制流嵌套超过 4 层时给出 warning
        block_nested_depth=6,  # CLI 控制流嵌套超过 6 层时升级为 blocker

        # CLI 分支与魔法数阈值介于库代码和普通脚本之间。
        warn_branch_count=14,  # CLI 分支超过 14 个时提示收敛判断
        block_branch_count=24,  # CLI 分支超过 24 个时直接阻断
        warn_magic_number_count=14,  # CLI 未命名数字超过 14 个时提示抽常量
        block_magic_number_count=28,  # CLI 未命名数字超过 28 个时直接阻断

        # CLI 对数组 docstring 放宽，但继续要求公开 API 契约。
        require_array_docstring=False,  # CLI 默认放宽数组 docstring 契约
        require_public_docstrings=True,  # CLI 对外命令接口仍要求 docstring 契约

        # CLI 允许终端输出和路径常量，服务命令行工作流。
        allow_print=True,  # CLI 可直接向终端打印过程信息
        allow_hardcoded_path=True,  # CLI 可保留命令行运行所需的路径常量
    ),
    "notebook": ProfileConfig(  # 交互式 notebook 的默认复杂度与展示豁免
        name="notebook",  # 面向交互式笔记本的 profile 注册名
        warn_function_lines=130,  # 单个 notebook 辅助函数写到 130 行后提醒拆开
        block_function_lines=260,  # 交互式笔记本函数膨胀到 260 行后不再允许继续堆叠
        warn_nested_depth=5,  # 探索型单元出现 5 层嵌套时提醒把推导步骤拆平
        block_nested_depth=8,  # 交互式分析分支压到 8 层仍未展开时直接阻断

        # notebook 容忍更长的交互式探索代码块和条件分支。
        warn_branch_count=20,  # 一个 notebook 辅助函数出现 20 个条件分叉时提示拆步骤
        block_branch_count=40,  # 条件分叉堆到 40 条时判定为不可维护
        warn_magic_number_count=24,  # 试验数字散落到 24 处时提醒提炼实验参数
        block_magic_number_count=60,  # 试验数字扩散到 60 处时拒绝继续放行

        # notebook 默认放宽类型提示和数组 docstring 要求。
        require_type_hints=False,  # notebook 默认不强制完整类型提示
        require_array_docstring=False,  # 交互式探索阶段允许数组说明先不写满

        # notebook 允许输出、绘图、导入期副作用和路径常量。
        allow_print=True,  # notebook 可直接打印交互过程信息
        allow_plot_show=True,  # notebook 可直接弹出绘图窗口
        allow_import_side_effects=True,  # notebook 允许导入时执行初始化副作用
        allow_hardcoded_path=True,  # notebook 可保留实验路径常量
    ),
    "test": ProfileConfig(  # 测试代码的默认复杂度与断言场景阈值
        name="test",  # 面向测试代码的 profile 注册名
        warn_function_lines=55,  # 测试函数超过 55 行时先提示拆分
        block_function_lines=95,  # 测试函数超过 95 行时直接阻断
        warn_nested_depth=3,  # 测试控制流嵌套超过 3 层时给出 warning
        block_nested_depth=5,  # 测试控制流嵌套超过 5 层时升级为 blocker

        # 测试逻辑保持偏保守的分支和魔法数阈值。
        warn_branch_count=10,  # 测试分支超过 10 个时提示收敛判断
        block_branch_count=18,  # 测试分支超过 18 个时直接阻断
        warn_magic_number_count=8,  # 测试未命名数字超过 8 个时提示抽常量
        block_magic_number_count=16,  # 测试未命名数字超过 16 个时直接阻断

        # 测试中的公开帮助函数默认也要求 docstring。
        require_public_docstrings=True,  # 测试辅助 API 默认也要求完整 docstring
    ),
    "refactor": ProfileConfig(  # 重构场景使用的宽松复杂度与兼容阈值
        name="refactor",  # 面向重构过渡代码的 profile 注册名
        warn_function_lines=120,  # 重构函数超过 120 行时先提示拆分
        block_function_lines=240,  # 重构函数超过 240 行时直接阻断
        warn_nested_depth=5,  # 重构控制流嵌套超过 5 层时给出 warning
        block_nested_depth=8,  # 重构控制流嵌套超过 8 层时升级为 blocker

        # 重构中的过渡代码允许较宽松的分支和魔法数阈值。
        warn_branch_count=20,  # 重构分支超过 20 个时提示收敛判断
        block_branch_count=44,  # 重构分支超过 44 个时直接阻断
        warn_magic_number_count=24,  # 重构未命名数字超过 24 个时提示抽常量
        block_magic_number_count=60,  # 重构未命名数字超过 60 个时直接阻断

        # 重构 profile 放宽类型提示，但继续保留数组 docstring 约束。
        require_type_hints=False,  # 重构阶段默认放宽完整类型提示要求
        require_array_docstring=True,  # 重构阶段仍要求数组 docstring 契约

        # 重构脚本允许终端输出与硬编码路径辅助迁移工作。
        allow_print=True,  # 重构辅助脚本可直接输出迁移过程信息
        allow_hardcoded_path=True,  # 重构辅助脚本可保留迁移路径常量
    ),
}

# current-project 风格配置固定放在 skill references/style 下。
def current_project_style_config_path() -> Path:
    """返回 current-project 风格配置文件路径。

    参数:
        无。

    返回:
        `references/style/current_project_style_config.json` 的绝对路径。
    """

    # 固定按当前文件位置回溯到 references/style 配置文件。
    return Path(__file__).resolve().parents[3] / "references" / "style" / "current_project_style_config.json"

# `load_current_project_style_config` 加载currentproject风格规则配置。
def load_current_project_style_config() -> dict[str, Any]:
    """加载当前project风格配置。

    参数:
        无。

    返回:
        解析成功时返回 current-project 风格配置映射；失败时返回内置默认配置。
    """

    # 先定位当前 skill 内的 current-project 风格配置文件。
    path_config_path = current_project_style_config_path()  # `current_project_style_config.json` 的绝对路径

    # 读取配置文件时允许文件缺失或 JSON 损坏回退到内置默认值。
    try:

        # 读取待检查文本，后续规则直接分析这份源码。
        dict_loaded_config = json.loads(path_config_path.read_text(encoding="utf-8"))  # 读取规则配置，决定本次检查启用哪些约束

    # 配置文件丢失或 JSON 语法损坏时退回内置默认配置。
    except (OSError, json.JSONDecodeError):

        # 配置文件缺失或损坏时回退到内置 current-project 默认值。
        return DEFAULT_CURRENT_PROJECT_STYLE_CONFIG

    # 顶层配置必须是映射，否则无法读取分区开关。
    if not isinstance(dict_loaded_config, dict):

        # 类型不匹配时回退到内置默认值。
        return DEFAULT_CURRENT_PROJECT_STYLE_CONFIG

    # 成功解析为映射后，把它交给后续 helper 逐项读取分区字段。
    # 返回已解析配置，后续 helper 会逐项读取分区字段。
    return dict_loaded_config

# `get_nested_bool` 处理嵌套布尔值。
def get_nested_bool(settings: dict[str, Any], section: str, key: str, default: bool) -> bool:
    """取得nested布尔。

    参数:
        settings: 当前验证流程读取到的规则配置映射。
        section: 正在读取的配置分区名称。
        key: 正在校验的配置字段名称。
        default: 缺省配置值

    返回:
        指定分区字段的布尔值；字段缺失或分区无效时返回 default。
    """

    # 配置分区缺失时使用调用方提供的布尔默认值。
    dict_section_value = settings.get(section)  # 配置段字段值

    # 分区不是映射时无法读取具体字段。
    if not isinstance(dict_section_value, dict):

        # 使用默认值保持 profile 构建稳定。
        return default

    # YAML/JSON 中的布尔值按 Python truthiness 归一化。
    return bool(dict_section_value.get(key, default))

# `get_nested_int` 处理嵌套整数。
def get_nested_int(settings: dict[str, Any], section: str, key: str, default: int) -> int:
    """取得nested整数。

    参数:
        settings: 当前验证流程读取到的规则配置映射。
        section: 正在读取的配置分区名称。
        key: 正在校验的配置字段名称。
        default: 缺省配置值

    返回:
        指定分区字段的整数值；缺失或无法转换时返回 default。
    """

    # 先取出候选分区，后续才能判断是否能安全读取整数字段。
    dict_section_value = settings.get(section)  # section 名对应的原始配置分区对象

    # 分区结构不是映射时，整数字段读取没有可靠来源。
    if not isinstance(dict_section_value, dict):

        # 配置结构损坏时继续沿用调用方提供的默认阈值。
        return default

    # 原始字段可能来自 JSON 数字或字符串，需要统一转成 int。
    int_raw_value = dict_section_value.get(key, default)  # 配置字段原始取值

    # 字段值既可能是数字，也可能是字符串形式的数字阈值。
    try:

        # 转换成功后返回规范化整数阈值。
        return int(int_raw_value)

    # 非法数字文本或不支持的对象值都回退到默认阈值。
    except (TypeError, ValueError):

        # 非法值回退到默认阈值，避免配置损坏中断导入。
        return default

# `get_nested_string_tuple` 处理嵌套string元组。
def get_nested_string_tuple(
    settings: dict[str, Any],
    section: str,
    key: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    """取得nestedstring元组。

    参数:
        settings: 当前验证流程读取到的规则配置映射。
        section: 正在读取的配置分区名称。
        key: 正在校验的配置字段名称。
        default: 缺省配置值

    返回:
        指定分区字段中的字符串列表；配置无效时返回 default。
    """

    # 先取出候选分区，后续才能判断字符串列表字段能否安全读取。
    dict_section_value = settings.get(section)  # 当前 section 对应的原始字符串列表配置分区

    # 分区结构不是映射时，字符串列表字段没有可靠来源。
    if not isinstance(dict_section_value, dict):

        # 配置结构损坏时继续沿用调用方提供的默认字符串元组。
        return default

    # JSON 中的字符串列表需要先取出，再过滤并转成不可变元组。
    list_raw_value = dict_section_value.get(key, list(default))  # 用于构造字符串元组的原始配置列表候选

    # 字段不是列表时不能安全转换为语句类型集合。
    if not isinstance(list_raw_value, list):

        # 使用默认值避免错误配置扩大检查范围。
        return default

    # 过滤空白和非字符串项，保证规则只接收有效语句类型。
    return tuple(item for item in list_raw_value if isinstance(item, str) and item.strip())

# language 分区只控制注释语言类规则。
def language_style_options(settings: dict[str, Any]) -> dict[str, bool]:
    """读取 current-project 的语言风格开关。

    参数:
        settings: 当前验证流程读取到的规则配置映射。

    返回:
        包含 `require_chinese_comments` 的配置映射。
    """

    # 返回语言分区映射，供 current-project 覆盖层合并中文注释开关。
    return {
        "require_chinese_comments": get_nested_bool(
            settings,
            "language",
            "require_chinese_comments",
            True,
        ),
    }

# spacing 分区控制空行、特殊语句和密集代码阈值。
def spacing_style_options(settings: dict[str, Any]) -> dict[str, Any]:
    """读取 current-project 的空行和语句间距配置。

    参数:
        settings: 当前验证流程读取到的规则配置映射。

    返回:
        可直接传入 `ProfileConfig` 的间距类配置映射。
    """

    # 返回 spacing 分区映射，供 current-project 覆盖层合并空行和间距规则。
    return {
        "require_blank_line_comments": get_nested_bool(
            settings,
            "spacing",
            "require_blank_line_comments",
            True,
        ),
        "require_control_spacing": get_nested_bool(
            settings,
            "spacing",
            "require_control_spacing",
            True,
        ),
        "require_special_statement_spacing": get_nested_bool(
            settings,
            "spacing",
            "require_special_statement_spacing",
            True,
        ),
        "special_statement_kinds": get_nested_string_tuple(
            settings,
            "spacing",
            "special_statement_kinds",
            DEFAULT_SPECIAL_STATEMENT_KINDS,
        ),
        "max_dense_code_lines": get_nested_int(settings, "spacing", "max_dense_code_lines", 8),
    }

# assignment_comments 分区控制赋值上下方注释要求。
def assignment_style_options(settings: dict[str, Any]) -> dict[str, Any]:
    """读取 current-project 的赋值注释配置。

    参数:
        settings: 当前验证流程读取到的规则配置映射。

    返回:
        可直接传入 `ProfileConfig` 的赋值注释配置映射。
    """

    # 返回 assignment_comments 分区映射，供覆盖层合并赋值注释要求。
    return {
        "require_assignment_comments": get_nested_bool(
            settings,
            "assignment_comments",
            "require_inline_chinese_comment",
            True,
        ),
        "require_assignment_block_comments": get_nested_bool(
            settings,
            "assignment_comments",
            "require_above_chinese_comment",
            True,
        ),
        "banned_generic_comment_phrases": get_nested_string_tuple(
            settings,
            "assignment_comments",
            "banned_generic_comment_phrases",
            DEFAULT_GENERIC_COMMENT_PHRASES,
        ),
    }

# comment_quality 分区控制注释具体性、长度和禁用短语。
def comment_quality_style_options(settings: dict[str, Any]) -> dict[str, Any]:
    """读取 current-project 的注释质量配置。

    参数:
        settings: 当前验证流程读取到的规则配置映射。

    返回:
        可直接传入 `ProfileConfig` 的注释质量配置映射。
    """

    # 返回 comment_quality 分区映射，供覆盖层合并注释质量阈值。
    return {
        "require_specific_purpose_comments": get_nested_bool(
            settings,
            "comment_quality",
            "require_specific_purpose_comments",
            True,
        ),
        "min_inline_assignment_comment_chinese_chars": get_nested_int(
            settings,
            "comment_quality",
            "min_inline_assignment_chinese_chars",
            4,
        ),
        "min_above_assignment_comment_chinese_chars": get_nested_int(
            settings,
            "comment_quality",
            "min_above_assignment_chinese_chars",
            5,
        ),
        "min_block_comment_chinese_chars": get_nested_int(
            settings,
            "comment_quality",
            "min_block_comment_chinese_chars",
            4,
        ),
        "allow_short_import_group_comments": get_nested_bool(
            settings,
            "comment_quality",
            "allow_short_import_group_comments",
            True,
        ),
        "banned_vague_comment_phrases": get_nested_string_tuple(
            settings,
            "comment_quality",
            "banned_vague_comment_phrases",
            DEFAULT_VAGUE_COMMENT_PHRASES,
        ),
    }

# current-project 样式会把外部 JSON 配置覆盖到基础 profile 上。
def with_current_project_style(config: ProfileConfig) -> ProfileConfig:
    """把 current-project 风格配置合并到基础 profile。

    参数:
        config: 控制 profile、风格开关和阈值的质量门配置。

    返回:
        启用严格项目风格后的新 `ProfileConfig`。
    """

    # 读取风格配置，后续校验按配置字段判断规则完整性。
    dict_settings = load_current_project_style_config()  # 映射配置字段

    # 汇总各分区 helper 产出的覆盖项，最后一次性叠加到基础 profile。
    dict_style_options: dict[str, Any] = {}  # 待覆盖到基础 profile 的 current-project 风格选项

    # 先合并语言分区，让中文注释约束进入覆盖层。
    dict_style_options.update(language_style_options(dict_settings))

    # 再合并空行与间距分区，补齐 PG031-PG032 相关阈值。
    dict_style_options.update(spacing_style_options(dict_settings))

    # 继续合并赋值注释分区，补齐 PG033-PG035 相关开关。
    dict_style_options.update(assignment_style_options(dict_settings))

    # 最后合并注释质量分区，补齐 PG036-PG037 阈值与黑名单。
    dict_style_options.update(comment_quality_style_options(dict_settings))

    # 把 current-project 覆盖项写回基础 profile，并强制开启严格项目风格。
    return replace(
        config,
        **dict_style_options,
        strict_project_style=True,
    )

# `get_profile_config` 处理profile规则配置。
def get_profile_config(profile: str) -> ProfileConfig:
    """取得profile配置。

    参数:
        profile: 用户选择或调度器推荐的语义 profile 名称。

    返回:
        已注册 profile 对应的不可变质量门配置。

    异常:
        抛出配置缺失、字段类型错误或资源读取失败时对应的验证问题。
    """

    # 未注册的 profile 名称应给出完整可选列表。
    if profile not in PROFILE_CONFIGS:

        # 错误消息列出支持的 profile，便于 CLI 用户修正参数。
        str_supported_profiles = ", ".join(sorted(PROFILE_CONFIGS))  # 支持的 profile 名称列表

        # 对外抛出固定前缀错误，便于 CLI 和测试统一识别配置问题。
        raise ValueError(f"> ERR: [Python] unknown profile: {profile}. Supported profiles: {str_supported_profiles}")

    # 返回注册表中的不可变配置对象，供规则运行器直接读取阈值和开关。
    return PROFILE_CONFIGS[profile]

# profile 列表用于 CLI 展示和错误消息。
def list_profiles() -> list[str]:
    """列出当前质量门支持的 profile 名称。

    参数:
        无。

    返回:
        按字母序排列的 profile 名称列表。
    """

    # 统一按字母序输出 profile 列表，便于 CLI 展示和错误消息复用。
    return sorted(PROFILE_CONFIGS)
