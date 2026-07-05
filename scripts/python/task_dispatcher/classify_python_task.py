"""把用户请求分类为 readable-python-generator 的 Python 任务类型。

机器可读 stdout 协议:
    stdout_protocol: json
"""

# 命令行分类器只依赖标准库里的参数解析、JSON 协议输出和路径匹配能力。
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# 这些词说明用户已经明确提到 Python 源码或 `.py` 文件。
TRIGGER_TERMS = (
    "python", ".py", "py文件",  # 直接点名 Python 或文件扩展名
    "py file", "python代码设计", "python代码优化",  # 中英文治理语境的常见说法
)

# 这些词表示用户更像是在请求“新写一段 Python 代码”。
GENERATE_TERMS = (
    "生成", "创建", "新建", "写一个",  # 中文生成动作
    "write", "create", "generate",  # 英文生成动作
)

# 这些词表示用户想整改、审查或重命名既有 Python 代码。
MODIFY_TERMS = (
    "修改", "修复", "重构", "优化",  # 直接改现有实现的动作词
    "规范化", "注释", "添加注释",  # 治理格式或补充说明的动作词
    "检查", "规范检查", "变量命名",  # 审查质量或命名边界的动作词
    "结构规范", "代码结构", "重命名",  # 调整代码组织方式的动作词
    "review", "refactor", "fix", "rename",  # 英文整改语境里的常见动词
)

# 这些词表示请求更偏向解释、阅读或摘要，而不是直接改代码。
EXPLAIN_TERMS = (
    "解释", "说明", "分析", "阅读",  # 中文只读动作
    "explain", "describe", "summarize",  # 英文只读动作
)

# 这些组按优先级把请求领域映射到更贴近场景的 quality profile。
PROFILE_KEYWORD_GROUPS = (
    ("cli", ("cli", "命令行")),  # 明确提到终端入口时优先走 CLI 档案
    ("test", ("test", "测试")),  # 提到测试时切到测试档案
    ("scientific", ("numpy", "scipy", "pandas", "np.", "pd.", "科学", "矩阵", "array")),  # 科学计算词汇
    ("notebook", ("notebook", "ipynb")),  # Notebook 或单元格执行语境
    ("script", ("script", "脚本")),  # 普通脚本语境
)

# 这里只接受常见 ASCII 路径片段，避免把整段中文自然语言误吞成 `.py` 目标。
PYTHON_PATH_PATTERN = re.compile(  # 请求文本里的 Python 路径识别正则
    r"(?P<path>(?:[A-Za-z]:[\\/]|\.{1,2}[\\/])?(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+\.py)\b",  # 兼容盘符、相对路径和裸文件名
    re.IGNORECASE,  # Windows 风格路径保持大小写不敏感
)

# 把请求文本压成大小写无关的统一表示，方便后续关键词匹配。
def normalize_prompt(prompt: str) -> str:
    """规整用于关键词匹配的请求文本。

    参数:
        prompt: 用户请求文本。

    返回:
        适合做大小写无关匹配的规范化文本。
    """

    # `casefold` 比 `lower` 更适合处理中英文混杂的统一比较。
    return prompt.casefold()

# 收集请求文本命中的 Python skill 触发词。
def matched_trigger_terms(prompt: str) -> list[str]:
    """收集命中的 Python 触发词。

    参数:
        prompt: 用户请求文本。

    返回:
        按配置顺序返回的触发词列表；显式 `.py` 目标会补入 `.py` 触发词。
    """

    # 先按固定词表收集直接命中的 Python 触发证据。
    list_matches = [  # 与请求语义直接对齐的触发词列表
        term for term in TRIGGER_TERMS if term.casefold() in normalize_prompt(prompt)  # 只保留当前请求实际命中的固定触发词
    ]

    # 显式点到 `.py` 文件时，也把扩展名算作额外触发证据。
    for target_path in extract_target_paths(prompt):

        # 只有当前还没登记过 `.py` 时，才补入由路径导出的扩展名证据。
        if target_path.casefold().endswith(".py") and ".py" not in list_matches:

            # 补入由目标文件推导出的 `.py` 触发词，保持分类置信度稳定。
            list_matches.append(".py")

    # 按发现顺序返回触发词，方便上游工作流断言分类结果。
    return list_matches

# 从自由文本中提取显式 `.py` 文件路径。
def extract_target_paths(prompt: str) -> list[str]:
    """从请求中提取显式 `.py` 目标路径。

    参数:
        prompt: 用户请求文本。

    返回:
        去重后的 Python 文件路径文本列表。
    """

    # 这里按出现顺序保留唯一路径，避免后续重复审查同一目标。
    list_paths: list[str] = []  # 去重后仍保留原始顺序的目标路径列表

    # 顺序扫描正则命中的 `.py` 片段，尽量保留用户原始书写顺序。
    for match in PYTHON_PATH_PATTERN.finditer(prompt):

        # 只在当前片段尚未出现过时，才把它纳入目标路径列表。
        if match.group("path").strip() not in list_paths:

            # 记录新的 Python 文件片段，供后续质量门与任务分类复用。
            list_paths.append(match.group("path").strip())

    # 返回可直接交给审查流程的目标路径文本列表。
    return list_paths

# 判断请求文本是否包含任一意图关键词。
def contains_any(prompt: str, terms: tuple[str, ...]) -> bool:
    """判断请求是否包含任一分类关键词。

    参数:
        prompt: 用户请求文本。
        terms: 待匹配的触发词集合。

    返回:
        只要命中任一关键词就返回 True。
    """

    # 任一关键词命中即可确认对应意图存在。
    return any(term.casefold() in normalize_prompt(prompt) for term in terms)

# 将请求意图归入生成、修改或只读解释流程。
def classify_task_type(prompt: str, target_paths: list[str]) -> str:
    """按任务意图选择生成、修改或解释流程。

    参数:
        prompt: 用户请求文本。
        target_paths: 目标路径列表。

    返回:
        `generate`、`modify` 或 `explain` 任务类型标识。
    """

    # 解释类请求保持只读，不进入生成或修改路径。
    if contains_any(prompt, EXPLAIN_TERMS):

        # 只读解释场景固定返回 explain。
        return "explain"

    # 已给出目标文件或整改动作词时，优先按既有代码治理处理。
    if target_paths or contains_any(prompt, MODIFY_TERMS):

        # 既有代码整改场景固定返回 modify。
        return "modify"

    # 其余场景默认按新建代码请求处理。
    return "generate"

# 根据请求内容推荐质量门 profile。
def recommend_profile(prompt: str, task_type: str) -> str:
    """根据请求领域推荐质量门 profile。

    参数:
        prompt: 用户请求文本。
        task_type: 任务类型。

    返回:
        最适合该请求的质量门 profile 名称。
    """

    # 修改任务默认使用 refactor，更强调行为保持和存量代码治理。
    if task_type == "modify":

        # 存量代码整改优先返回 refactor 档案。
        return "refactor"

    # 非修改任务再按优先级扫描领域词，尽量给出贴近上下文的 profile。
    for candidate_profile_name, tuple_profile_terms in PROFILE_KEYWORD_GROUPS:

        # 命中领域词后立即锁定对应 profile，保持优先级语义稳定。
        if contains_any(prompt, tuple_profile_terms):

            # 当前命中的领域档案已经足够具体，可以直接返回。
            return candidate_profile_name

    # 未命中特殊领域时回退到通用 library 档案。
    return "library"

# 为每类任务声明必须执行的检查项。
def required_checks(task_type: str, has_target: bool) -> list[str]:
    """给每类任务返回稳定的检查矩阵。

    参数:
        task_type: 任务类型。
        has_target: 是否包含目标路径。

    返回:
        触发后需要执行或等待执行的检查项列表。
    """

    # 生成任务需要先确认意图，再在代码出现后补跑质量门。
    if task_type == "generate":

        # 新建代码请求的检查矩阵比其他类型多一层产后质量门。
        return [
            "task_classification",
            "comment_quality_gate",
            "typed_variable_naming",
            "intent_contract",
            "post_generation_quality_gate",
        ]

    # 修改任务先做既有代码基线审查，再进入整改流程。
    if task_type == "modify":

        # 存量代码整改固定追加 existing_code_quality_gate。
        return [
            "task_classification",
            "comment_quality_gate",
            "typed_variable_naming",
            "existing_code_quality_gate",
        ]

    # 点名目标文件的只读请求，还需要额外给出质量摘要。
    if has_target:

        # 只读且有目标路径时补充 read_only_quality_summary。
        return [
            "task_classification",
            "comment_quality_gate",
            "typed_variable_naming",
            "read_only_classification",
            "read_only_quality_summary",
        ]

    # 其余只读请求只保留分类层面的摘要检查。
    return [
        "task_classification",
        "comment_quality_gate",
        "typed_variable_naming",
        "read_only_classification",
    ]

# 标记每个检查项在当前请求中是否可立即执行。
def check_applicability(task_type: str, has_target: bool) -> dict[str, str]:
    """说明固定检查在当前请求里是否可执行。

    参数:
        task_type: 任务类型。
        has_target: 是否包含目标路径。

    返回:
        检查项到可执行状态的稳定映射。
    """

    # 生成任务在产物出现前，只能先完成分类和意图合同检查。
    if task_type == "generate":

        # 代码尚未落地时，质量门与命名检查只能标记为 after_code_exists。
        return {
            "task_classification": "available",
            "comment_quality_gate": "after_code_exists",
            "typed_variable_naming": "after_code_exists",
        }

    # 只要已经有目标文件，质量门和命名检查就都可以立即执行。
    if has_target:

        # 已有审查目标时，三个基础检查都可直接运行。
        return {
            "task_classification": "available",
            "comment_quality_gate": "available",
            "typed_variable_naming": "available",
        }

    # 没有目标文件的非生成请求，只能先完成分类检查。
    return {
        "task_classification": "available",
        "comment_quality_gate": "not_applicable_until_code_exists",
        "typed_variable_naming": "not_applicable_until_code_exists",
    }

# 生成 readable-python-generator 触发决策的完整 JSON 结果。
def classify_request(prompt: str) -> dict[str, Any]:
    """分类用户请求并返回稳定 JSON 字段。

    参数:
        prompt: 用户请求文本。

    返回:
        包含触发状态、任务类型、推荐 profile 和检查矩阵的字典。
    """

    # 先提取显式目标路径，后续任务分类和检查矩阵都会复用它。
    list_target_paths = extract_target_paths(prompt)  # 请求里显式写出的 `.py` 目标列表

    # 再收集触发词，判断是否真正进入 readable-python-generator 流程。
    list_triggers = matched_trigger_terms(prompt)  # 触发 skill 的关键词证据列表

    # 这里单独缓存触发布尔值，避免后续多处重复做布尔转换。
    bool_triggered = bool(list_triggers)  # 当前请求是否真正触发 Python skill

    # 已触发请求才细分任务类型；未触发时统一保持 explain。
    str_task_type = classify_task_type(prompt, list_target_paths) if bool_triggered else "explain"  # 本轮请求的任务类型

    # 返回协议稳定的分类结果，供上游工作流直接消费。
    return {
        "triggered": bool_triggered,
        "task_type": str_task_type,
        "confidence": (
            "high"
            if bool_triggered and (list_target_paths or len(list_triggers) > 1)
            else "medium"
            if bool_triggered
            else "low"
        ),
        "matched_triggers": list_triggers,
        "target_paths": list_target_paths,
        "recommended_profile": recommend_profile(prompt, str_task_type),
        "required_checks": required_checks(str_task_type, bool(list_target_paths)),
        "check_applicability": check_applicability(str_task_type, bool(list_target_paths)),
        "needs_target_or_code": bool_triggered and str_task_type == "modify" and not list_target_paths,
    }

# 从 CLI 参数、文件或 stdin 读取待分类请求。
def read_prompt(args: argparse.Namespace) -> str:
    """从 CLI 参数或 stdin 读取请求文本。

    参数:
        args: argparse 解析后的命令行参数对象。

    返回:
        按优先级选出的请求文本。
    """

    # 显式 `--prompt` 优先级最高，适合脚本直接传入整段请求。
    if args.prompt:

        # 直接返回命令行里显式给出的请求文本。
        return args.prompt

    # `--prompt-file` 允许调用方复用 UTF-8 文本文件里的长请求。
    if args.prompt_file:

        # 从文本文件读取完整请求，避免命令行转义噪声。
        return Path(args.prompt_file).read_text(encoding="utf-8")

    # 位置参数兼容不带选项的短请求场景。
    if args.text:

        # 把位置参数重新拼成一段完整请求文本。
        return " ".join(args.text)

    # 没有显式参数时，再从标准输入读取整段请求。
    return sys.stdin.read()

# 构建分类器命令行参数解析器。
def build_parser() -> argparse.ArgumentParser:
    """构造分类器命令行参数。

    参数:
        无。

    返回:
        构造完成的 argparse 解析器对象。
    """

    # 先创建解析器对象，后续统一往里面登记支持的 CLI 参数。
    parser = argparse.ArgumentParser(  # 分类器命令行入口的参数解析器
        description="Classify Python task requests for readable-python-generator."  # 终端帮助文本里展示的分类器用途说明
    )

    # 逐项登记位置参数和可选参数，保持声明顺序与帮助文本稳定。
    for tuple_args, dict_kwargs in (
        (("text",), {"nargs": "*", "help": "Request text to classify"}),
        (("--prompt",), {"default": "", "help": "Request text to classify"}),
        (("--prompt-file",), {"default": "", "help": "UTF-8 file containing request text"}),
    ):

        # 把当前参数规格写入解析器，供 main 统一解析 CLI 输入。
        parser.add_argument(*tuple_args, **dict_kwargs)

    # 返回解析器对象，供命令行入口复用。
    return parser

# 分类器命令行入口。
def main(argv: list[str] | None = None) -> int:
    """执行分类器 CLI 并输出 JSON。

    参数:
        argv: 命令行入口接收的参数序列；为 None 时使用进程参数。

    返回:
        命令行入口使用的进程退出码。
    """

    # 一次性完成参数解析、请求读取和任务分类，避免入口状态分散。
    dict_result = classify_request(read_prompt(build_parser().parse_args(argv)))  # CLI 将要输出的机器可读分类结果

    # 该 CLI 已在模块 docstring 声明 JSON stdout 协议，因此直接写出单个 JSON 结果。
    sys.stdout.write(json.dumps(dict_result, ensure_ascii=False, indent=2) + "\n")

    # 成功输出协议结果后返回零退出码。
    return 0

# 脚本直接执行时，把 CLI 退出码交给解释器进程。
if __name__ == "__main__":

    # 使用 SystemExit 透传 main 计算出的最终退出码。
    raise SystemExit(main())
