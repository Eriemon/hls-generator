#!/usr/bin/env python3
"""验证当前工作文件夹 AGENTS 规则并兼容本仓库的 Python 语义尾注释策略。"""

# 延迟注解求值，避免运行时解析泛型类型造成旧 Python 兼容负担。
from __future__ import annotations

# 标准库用于解析治理配置、运行委托验证器并处理短暂文件系统错误。
import json
import os
import re
import shutil

# 子进程执行与临时目录缩面需要复用同一组标准库工具。
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, TextIO, cast
from urllib.parse import urlsplit

# 本地委托解析器集中维护已安装 agents-md-generator 的脚本路径。
from _skill_tool_delegate import agents_md_generator_script

# 根 AGENTS 中的相对路径引用会驱动官方 verifier 的路径存在性检查。
PATH_REFERENCE_PATTERN = re.compile(r"(?P<path>\.?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)")  # AGENTS Markdown 中常见的相对路径片段

# 这些文本只对应旧 agents-md-generator 尚未理解本仓库新注释策略时的误报。
SEMANTIC_TRAILING_ERROR_MARKERS = (  # 可被本地兼容层过滤的旧规则错误片段。
    "Code Comment Policy missing required rule `禁止右侧尾注释`",  # 旧根规则仍要求禁用 Python 尾注释的错误文本。
    "code_comment_policy.python missing required rule `禁止右侧尾注释`",  # 本地覆盖缺少旧规则时的错误文本。
    "code_comment_policy.positions.python.trailing must be forbidden",  # 旧位置策略要求 forbidden 的错误文本。
    "code_comment_policy.positions.python.trailing has invalid value semantic_assignment_allowed",  # 旧验证器拒绝本仓库新枚举值的错误文本。
    "run `python <codex-home>/skills/agents-md-generator/scripts/manage_docs.py sync-root-agents . --write`",  # 旧验证器给出的同步命令提示。
    (
        "local governance config `.agents/global-rule-overrides.json`: "
        "code_comment_policy.python missing required rule `禁止右侧尾注释`"
    ),  # 本地治理配置缺旧规则时的完整错误文本。
    (
        "local governance config `.agents/global-rule-overrides.json`: "
        "code_comment_policy.positions.python.trailing must be forbidden"
    ),  # 本地治理配置仍被旧枚举约束时的完整错误文本。
    (
        "local governance config `.agents/global-rule-overrides.json`: "
        "code_comment_policy.positions.python.trailing has invalid value semantic_assignment_allowed"
    ),  # 本地治理配置使用新枚举值时的完整错误文本。
)

# 检查本地治理覆盖是否授权兼容语义赋值尾注释。
def local_config_allows_semantic_trailing(project: Path) -> bool:
    """
    判断当前仓库是否显式启用受限的 Python 语义右侧注释。

    参数:
        project: 当前治理命令检查的项目根目录。

    返回:
        返回本地配置是否启用了 semantic_assignment_allowed。
    """

    # 本地覆盖文件是 AGENTS 代码注释策略的事实来源。
    path_config: Path = project / ".agents" / "global-rule-overrides.json"  # 本仓库治理覆盖 JSON 路径。

    # 缺失配置时不能放宽已安装验证器的默认规则。
    if not path_config.exists():

        # 没有本地授权时保持旧验证器的严格行为。
        return False

    # 进入 JSON 解析分支，读取本地治理覆盖中的注释位置策略。
    try:

        # 读取 JSON 配置，避免用字符串匹配误判策略。
        dict_governance_config: dict[str, Any] = json.loads(path_config.read_text(encoding="utf-8"))  # 已解析且待提取 source_governance 字段的治理覆盖字典

    # JSON 结构损坏时不能推断用户已显式授权兼容策略。
    except json.JSONDecodeError:

        # 配置损坏时不能按新策略过滤错误。
        return False

    # positions.python.trailing 是本仓库为 PG033 治理新增的受限策略开关。
    dict_comment_positions: dict[str, Any] = cast(  # 注释位置策略映射容器。
        dict[str, Any],  # 限定外层 positions 映射的预期类型。
        cast(dict[str, Any], dict_governance_config.get("code_comment_policy", {})).get("positions", {}),  # 读取注释位置策略子映射。
    )  # 注释位置策略映射。

    # 只有明确声明 semantic_assignment_allowed 才允许过滤旧验证器误报。
    return dict_comment_positions.get("python.trailing") == "semantic_assignment_allowed"

# 从 wrapper 参数中还原 AGENTS 验证应作用的项目根。
def project_from_args(args: list[str]) -> Path:
    """
    从 verify_agents 参数中解析项目根目录。

    参数:
        args: 传给委托脚本的命令行参数。

    返回:
        返回解析后的项目根目录。
    """

    # verify_agents 的第一个位置参数是项目路径；缺省时使用当前目录。
    for arg in args:

        # 跳过选项参数，保留位置参数作为项目根目录。
        if not arg.startswith("-"):

            # 返回绝对路径，避免委托脚本和 wrapper 对相对路径理解不一致。
            return Path(arg).resolve()

    # 未给出位置参数时按原 verify_agents 约定检查当前工作目录。
    return Path.cwd().resolve()

# 解析调用方是否已显式提供 installed skill 目录覆盖。
def installed_skill_dir_from_args(args: list[str]) -> Path | None:
    """
    从 wrapper 参数中解析已显式声明的 installed skill 目录。

    参数:
        args: 传给委托脚本的命令行参数。

    返回:
        返回调用方已声明的 installed skill 目录；缺失时返回 None。
    """

    # 使用下标遍历，兼容 `--installed-skill-dir value` 这种双 token 形式。
    for index, arg in enumerate(args):

        # 兼容 `--installed-skill-dir=value` 的单 token 形式。
        if arg.startswith("--installed-skill-dir="):

            # 直接解析等号后的显式目录文本。
            return Path(arg.split("=", 1)[1]).expanduser().resolve()

        # 兼容 `--installed-skill-dir value` 的双 token 形式。
        if arg == "--installed-skill-dir" and index + 1 < len(args):

            # 解析紧随其后的目录参数，保持 wrapper CLI 兼容。
            return Path(args[index + 1]).expanduser().resolve()

    # 调用方未显式声明 installed skill 目录时交给本地推导逻辑处理。
    return None

# 读取 source_governance 配置里的顶层排除目录集合。
def source_governance_excluded_roots(project: Path) -> tuple[str, ...]:
    """
    读取当前仓库 source_governance 的 excluded_roots 配置。

    参数:
        project: 当前 wrapper 需要验证的项目根目录。

    返回:
        返回去重后的顶层排除目录名元组。
    """

    # 本地治理覆盖文件承载 source_governance 的顶层排除目录配置。
    path_config = project / ".agents" / "global-rule-overrides.json"  # 本地治理覆盖 JSON 路径。

    # 缺少本地治理覆盖时，说明没有可复用的 source_governance 排除根。
    if not path_config.exists():

        # 没有配置就返回空集合，后续保持原项目路径验证。
        return ()

    # 只要覆盖文件存在，就先进入解析分支；坏文件会在 except 中触发保守回退。
    try:

        # 先拿到结构化字典，后续 excluded_roots 判断才不需要对原始 JSON 做字符串猜测。
        dict_governance_config: dict[str, Any] = json.loads(path_config.read_text(encoding="utf-8"))  # 本地治理覆盖内容。

    # 配置损坏时不能可信地构造轻量镜像，后续保守回退原项目路径。
    except json.JSONDecodeError:

        # JSON 损坏时不伪造排除目录集合。
        return ()

    # 读取 source_governance 子配置，只有字典结构才可能包含 excluded_roots。
    dict_source_governance = dict_governance_config.get("source_governance", {})  # source_governance 子配置对象。

    # source_governance 缺失或类型错误时不构造任何顶层排除集合。
    if not isinstance(dict_source_governance, dict):

        # 子配置类型不可信时回退空集合。
        return ()

    # 读取 excluded_roots 原始列表，后续统一清理斜杠与重复值。
    config_excluded_roots = dict_source_governance.get("excluded_roots", [])  # excluded_roots 原始配置值。

    # excluded_roots 不是列表时不能继续构造目录名集合。
    if not isinstance(config_excluded_roots, list):

        # 配置类型异常时保守返回空集合。
        return ()

    # 逐项去重，保持顶层目录排除集合顺序稳定。
    list_roots: list[str] = []  # 去重后的顶层排除目录名列表。

    # 统一清理路径分隔符，保证顶层目录名比较口径一致。
    for obj_item in config_excluded_roots:

        # 把任意配置项都转成目录名文本，兼容 JSON 字符串以外的历史写法。
        str_root_name = str(obj_item).strip().strip("/\\")  # 清理后的顶层排除目录名。

        # 空字符串或重复目录名都不需要进入最终排除集合。
        if str_root_name and str_root_name not in list_roots:

            # 记录当前 source_governance 声明的顶层排除目录名。
            list_roots.append(str_root_name)

    # 返回去重后的顶层排除目录集合，供轻量镜像构造流程复用。
    return tuple(list_roots)

# 提取根 AGENTS 文件里显式引用且真实存在的项目内相对路径。
def root_agents_referenced_paths(project: Path) -> tuple[Path, ...]:
    """
    收集根 AGENTS/override 中显式写出的项目内路径。

    参数:
        project: 当前 verify_agents 检查的项目根目录。

    返回:
        tuple[Path, ...]: 根 AGENTS 显式引用且真实存在的项目内绝对路径集合。
    """

    # 统一使用解析后的项目根目录做 relative_to 校验，避免大小写或相对路径差异干扰去重。
    path_project_root = project.resolve()  # 当前项目根目录的规范绝对路径。

    # 用字典保持插入顺序，便于镜像时生成稳定的复制顺序。
    dict_referenced_paths: dict[Path, None] = {}  # 根 AGENTS 中显式引用的项目内路径集合。

    # 根级 override 与根 AGENTS 都可能携带官方 verifier 会检查的路径引用。
    for str_agents_filename in ("AGENTS.override.md", "AGENTS.md"):

        # 构造当前要扫描的根级 AGENTS 文件路径。
        path_agents = path_project_root / str_agents_filename  # 当前扫描的根级 AGENTS/override 文件。

        # 缺失文件时直接跳过，避免为不存在的根 AGENTS 引入额外异常分支。
        if not path_agents.is_file():

            # 当前命名形态不存在时无需继续解析其内容。
            continue

        # 尝试读取根 AGENTS 内容，后续用相对路径模式提取显式引用。
        try:

            # 统一按 UTF-8 读取根 AGENTS，保持与项目内治理文档编码一致。
            str_agents_text = path_agents.read_text(encoding="utf-8")  # 当前根 AGENTS 文件的完整文本内容。

        # 根 AGENTS 无法读取时保持降级行为，后续镜像仍可回退到普通 excluded_roots 处理。
        except OSError:

            # 读失败时不再为当前文件提取引用，继续处理其他根级 AGENTS 形态。
            continue

        # 逐个抽取 Markdown 行中出现的相对路径片段。
        for obj_match in PATH_REFERENCE_PATTERN.finditer(str_agents_text):

            # 取出命中的相对路径文本，并去掉可选的 `./` 前缀。
            str_relative_path = obj_match.group("path").removeprefix("./")  # AGENTS 文本中命中的相对路径片段。

            # 空路径或看起来像 URL 片段的文本都不应当参与项目路径解析。
            if (not str_relative_path) or looks_like_url_reference(str_relative_path):

                # 当前命中不是项目内相对路径时直接跳过。
                continue

            # 把命中的相对路径解析到项目根目录下，供 exists 和 relative_to 双重校验。
            path_candidate = (path_project_root / Path(str_relative_path)).resolve()  # 当前 AGENTS 引用对应的项目内候选绝对路径。

            # 只保留项目根目录内部的真实路径，避免把奇怪片段当成本地文件复制。
            try:

                # relative_to 成功才说明当前候选路径仍位于项目根目录之内。
                path_candidate.relative_to(path_project_root)

            # 解析后越过项目根目录的候选路径不应进入轻量镜像复制集合。
            except ValueError:

                # 项目外路径不参与复制，继续处理下一个命中。
                continue

            # 只有真实存在的显式引用才值得为轻量镜像补拷贝。
            if path_candidate.exists():

                # 用字典键自然去重，保持根 AGENTS 中首次出现顺序。
                dict_referenced_paths[path_candidate] = None  # 根 AGENTS 显式引用且真实存在的项目内路径去重集合

    # 返回稳定顺序的显式引用路径集合，供轻量镜像阶段按需补拷贝。
    return tuple(dict_referenced_paths.keys())

# 为委托验证器补齐真实 installed skill 目录，避免 temp CODEX_HOME 误导版本探测。
def delegate_args_with_installed_skill_dir(script_path: Path, args: list[str]) -> tuple[list[str], Path]:
    """
    保证委托验证器总能拿到真实 installed skill 目录参数。

    参数:
        script_path: 已安装 agents-md-generator 的 verify_agents.py 路径。
        args: 原始 wrapper 参数序列。

    返回:
        返回补齐后的参数序列和应使用的 installed skill 根目录。
    """

    # 优先尊重调用方已显式声明的 installed skill 覆盖目录。
    path_explicit_installed_skill_dir = installed_skill_dir_from_args(args)  # 调用方显式给出的 installed skill 根目录。

    # 显式覆盖存在时不再改写参数顺序，只回传解析后的目录路径。
    if path_explicit_installed_skill_dir is not None:

        # 直接保留原始参数，避免 wrapper 对调用方 CLI 做额外改写。
        return list(args), path_explicit_installed_skill_dir

    # 委托脚本位于 `<skill-root>/scripts/python/verify/verify_agents.py`。
    path_installed_skill_dir = script_path.expanduser().parents[3]  # 已安装 agents-md-generator 技能根目录。

    # 缺省情况下补齐真实 installed skill 目录，避免 temp CODEX_HOME 指向空技能树。
    return [*args, "--installed-skill-dir", str(path_installed_skill_dir)], path_installed_skill_dir

# 用轻量镜像项目路径覆盖委托参数里的项目根。
def delegate_args_with_project_override(args: list[str], project: Path) -> list[str]:
    """
    把委托参数里的项目根改写成轻量镜像项目路径。

    参数:
        args: 原始委托参数序列。
        project: 委托验证器应当检查的轻量镜像项目路径。

    返回:
        返回改写后的委托参数序列。
    """

    # 复制参数列表，避免原地修改调用方仍要复用的实参。
    list_args = list(args)  # 准备写回轻量镜像项目路径的参数列表。

    # 第一个位置参数始终代表 verify_agents 要检查的项目根目录。
    for index, arg in enumerate(list_args):

        # 只替换首个非选项参数，保持其余 CLI 选项顺序不变。
        if not arg.startswith("-"):

            # 用轻量镜像项目路径覆盖原项目根目录参数。
            list_args[index] = str(project)  # 覆盖后的轻量镜像项目路径

            # 完成首个位置参数替换后即可返回。
            return list_args

    # 原参数没有显式项目根时，补一个轻量镜像路径作为默认位置参数。
    return [str(project), *list_args]

# 构造排除重目录内容的轻量项目镜像，避免官方 verifier 在 reports 等根目录上长时间遍历。
def build_delegate_project_mirror(project: Path) -> tuple[Path, Path | None]:
    """
    为委托验证器构造一个保留治理事实但排空 excluded_roots 内容的轻量项目镜像。

    参数:
        project: 当前 wrapper 需要验证的真实项目根目录。

    返回:
        返回委托验证应使用的项目路径，以及可选的轻量镜像根目录。
    """

    # 读取 source_governance 声明的顶层排除目录集合。
    tuple_excluded_roots = source_governance_excluded_roots(project)  # source_governance 顶层排除目录集合。

    # 没有排除目录配置时无需构造轻量镜像，直接沿用真实项目路径。
    if not tuple_excluded_roots:

        # 保持真实项目路径参与委托验证，不创建额外镜像目录。
        return project, None

    # 根 AGENTS 里显式引用的排除目录路径需要在轻量镜像中保真，否则官方 verifier 会误报缺文件。
    tuple_referenced_paths = root_agents_referenced_paths(project)  # 根 AGENTS 中显式引用且真实存在的项目内路径集合。

    # 只要有任一排除目录在真实项目根存在，就值得构造轻量镜像。
    bool_has_excluded_root = any((project / str_root_name).exists() for str_root_name in tuple_excluded_roots)  # 是否存在已声明的顶层排除目录。

    # 所有排除目录都不存在时无需再付出镜像成本。
    if not bool_has_excluded_root:

        # 顶层排除目录不存在时直接复用真实项目路径。
        return project, None

    # 开始构造轻量镜像，把 excluded_roots 保留为空目录而不复制其海量内容。
    try:

        # 用独立临时目录承载本轮 verify_agents 需要的轻量项目镜像。
        path_temp_project_root = Path(tempfile.mkdtemp(prefix="verify-agents-project-")).resolve()  # verify_agents 轻量项目镜像根目录。

        # `.agents/` 既是 verifier 的治理输入根，又常被 source_governance 排除，镜像时必须保留真实内容。
        set_required_roots = {".agents"}  # 轻量镜像阶段仍需完整复制的治理根目录集合。

        # 遍历真实项目根的顶层条目，按 excluded_roots 决定复制还是占位。
        for path_source_entry in project.iterdir():

            # 记录当前顶层条目的名称，供 excluded_roots 匹配复用。
            str_entry_name = path_source_entry.name  # 当前顶层条目名称。

            # 计算当前顶层条目在轻量镜像中的目标路径。
            path_target_entry = path_temp_project_root / str_entry_name  # 当前顶层条目的镜像目标路径。

            # excluded_roots 的顶层目录只保留空目录占位，避免官方 verifier 深入海量内容。
            if (
                str_entry_name in tuple_excluded_roots
                and path_source_entry.is_dir()
                and str_entry_name not in set_required_roots
            ):

                # 创建同名空目录，既保留路径存在事实，又跳过重目录内容复制。
                path_target_entry.mkdir(parents=True, exist_ok=True)

                # 当前排除根目录已经完成占位，不再复制内部内容。
                continue

            # 顶层目录需要完整复制时，保持原有相对路径结构。
            if path_source_entry.is_dir():

                # 复制当前非排除目录及其文件内容，供官方 verifier 读取真实治理事实。
                shutil.copytree(path_source_entry, path_target_entry)

                # 当前目录已复制完成，继续处理下一个顶层条目。
                continue

            # 普通文件直接复制到轻量镜像根，保持 AGENTS 和根级治理文件可见。
            shutil.copy2(path_source_entry, path_target_entry)

        # 再把根 AGENTS 明确引用且落在 excluded_roots 里的具体路径补进轻量镜像。
        for path_referenced_entry in tuple_referenced_paths:

            # 当前显式引用路径相对项目根目录的层级信息，供 top-level excluded root 过滤复用。
            path_relative_reference = path_referenced_entry.relative_to(project)  # 当前显式引用路径相对项目根目录的路径。

            # 只为 excluded_roots 内部的显式引用补拷贝，非排除根目录内容此前已完整复制。
            if path_relative_reference.parts[0] not in tuple_excluded_roots:

                # 非排除根目录内的显式引用无需额外补拷贝。
                continue

            # 计算显式引用路径在轻量镜像中的目标位置。
            path_target_reference = path_temp_project_root / path_relative_reference  # 当前显式引用路径在轻量镜像中的目标路径。

            # 目录引用只需保留目录存在事实，供官方 verifier 通过路径存在性检查。
            if path_referenced_entry.is_dir():

                # 创建完整父目录链，保证显式引用的目录在轻量镜像中可见。
                path_target_reference.mkdir(parents=True, exist_ok=True)

                # 目录占位已完成，继续处理下一条显式引用。
                continue

            # 文件引用需要连同父目录链一起补拷贝到轻量镜像中。
            path_target_reference.parent.mkdir(parents=True, exist_ok=True)

            # 用原始文件内容补齐显式引用的真实路径，避免 verifier 因 excluded_roots 误报缺失。
            shutil.copy2(path_referenced_entry, path_target_reference)

    # 轻量镜像构造异常时必须回退到真实项目路径，避免伪造验证成功。
    except OSError:

        # 镜像构造失败时不再使用残缺临时目录，后续回退到真实项目路径。
        return project, None

    # 返回轻量镜像项目路径，并保留临时目录供 finally 阶段清理。
    return path_temp_project_root, path_temp_project_root

# 生成可供 ripgrep 快速筛选 exact-cwd sessions 的候选字面量。
def session_search_terms(project: Path) -> tuple[str, ...]:
    """
    生成当前项目路径在 sessions JSONL 中常见的字面量形式。

    参数:
        project: 当前 wrapper 需要验证的项目根目录。

    返回:
        返回去重后的 exact-cwd 搜索字面量元组。
    """

    # 先解析项目绝对路径，确保搜索字面量和委托验证器的真实 cwd 对齐。
    path_project = project.resolve()  # 当前 verify_agents 项目的绝对路径。

    # Windows 原生路径最接近 `Path.resolve()` 产出的 cwd 文本。
    str_native_path = str(path_project)  # 当前项目的原生路径字符串。

    # JSONL 内的反斜杠会被转义，需要保留一份 JSON 字面量形式给 rg。
    str_json_literal_path = json.dumps(str_native_path)[1:-1]  # JSONL 中 cwd 常见的转义路径文本。

    # 某些会话或诊断记录可能保存 POSIX 斜杠风格路径。
    str_posix_path = path_project.as_posix()  # 当前项目的 POSIX 风格路径字符串。

    # 逐项去重，避免相同字面量触发重复 rg 扫描。
    list_terms: list[str] = []  # exact-cwd 会话筛选候选字面量列表。

    # 同时覆盖 JSON 转义、原生路径和 POSIX 路径三种常见编码形态。
    for str_term in (str_json_literal_path, str_native_path, str_posix_path):

        # 空字符串或重复字面量都没有必要继续追加。
        if str_term and str_term not in list_terms:

            # 记录当前仍需交给 rg 扫描的候选字面量。
            list_terms.append(str_term)

    # 返回去重后的 exact-cwd 会话搜索字面量集合。
    return tuple(list_terms)

# URL 片段不应被误判成项目内相对路径引用。
def looks_like_url_reference(str_reference: str) -> bool:
    """
    判断给定文本是否更像 URL，而不是项目内相对路径。

    参数:
        str_reference: 当前 AGENTS 中命中的路径文本。

    返回:
        返回该文本是否携带 URL scheme 与远端位置。
    """

    # 同时具备 scheme 与 netloc 时，说明命中内容更像远端 URL。
    return bool(urlsplit(str_reference).scheme and urlsplit(str_reference).netloc)

# 运行单个 exact-cwd 搜索字面量对应的 rg 路径筛选。
def run_session_rg_search(str_term: str, sessions_root: Path) -> subprocess.CompletedProcess[str]:
    """
    执行一次固定字符串 rg 搜索，返回命中的 session 文件路径输出。

    参数:
        str_term: 当前要匹配的 exact-cwd 路径字面量。
        sessions_root: 真实 CODEX_HOME 下的 sessions 根目录。

    返回:
        返回 rg 子进程结果对象。
    """

    # 用 ripgrep 只列出命中的 JSONL 文件路径，不读取完整内容到 Python。
    return subprocess.run(
        ["rg", "-l", "-i", "-F", "-g", "*.jsonl", str_term, str(sessions_root)],  # 使用固定字符串搜索当前项目路径字面量。
        check=False,  # rg 退出码 1 只表示无匹配，不属于异常。
        capture_output=True,  # 捕获 stdout 便于收集候选文件路径。
        text=True,  # 以文本模式读取 rg 的路径输出。
    )

# 把 rg 输出中的现存 JSONL 路径提取为去重集合。
def collect_existing_session_paths(str_rg_stdout: str) -> set[Path]:
    """
    解析 rg 输出文本，收集真实存在的 session JSONL 路径。

    参数:
        str_rg_stdout: rg 输出的候选路径文本。

    返回:
        返回当前 rg 结果中真实存在的 session 文件集合。
    """

    # 用集合自然去重，避免同一轮 rg 输出重复路径。
    set_session_paths: set[Path] = set()  # 单轮 rg 输出解析出的 session 文件集合。

    # 逐项吸收 rg 命中的 JSONL 路径。
    for str_raw_path in str_rg_stdout.splitlines():

        # 去掉换行和首尾空白，得到候选 session 文件文本路径。
        str_candidate_path = str_raw_path.strip()  # rg 返回的候选 session 路径文本。

        # 空白行没有任何路径意义，直接跳过。
        if not str_candidate_path:

            # 当前空白行不参与候选文件集合构建。
            continue

        # 解析成绝对路径，便于后续复制到 temp CODEX_HOME 时保持稳定。
        path_candidate_session = Path(str_candidate_path).expanduser().resolve()  # 命中的 session JSONL 绝对路径。

        # 只保留真实存在的 JSONL 文件，避免脏输出污染候选集合。
        if path_candidate_session.is_file():

            # 记录当前 exact-cwd 可能相关的 session 文件。
            set_session_paths.add(path_candidate_session)

    # 返回单轮 rg 输出中过滤后的真实路径集合。
    return set_session_paths

# 从单条 JSONL 原始文本中识别 session_meta 记录。
def parse_session_meta_line(str_raw_line: str) -> str | None:
    """
    解析单条 JSONL 文本，提取可直接复用的 session_meta 原始行。

    参数:
        str_raw_line: 当前扫描到的 JSONL 原始文本行。

    返回:
        返回标准化换行后的 session_meta 原始行；当前行不是目标记录时返回 None。
    """

    # 先按官方 verifier 的 JSONL 记录格式解析当前行。
    dict_data = json.loads(str_raw_line)  # 当前 session JSONL 记录对象。

    # 非 session_meta 记录对上游匹配无意义，继续读取下一行。
    if dict_data.get("type") != "session_meta":

        # 当前 JSONL 记录不是会话元数据，继续顺序扫描。
        return None

    # 上游 verifier 只消费 payload 为 dict 的 session_meta 记录。
    if not isinstance(dict_data.get("payload"), dict):

        # payload 不是对象时不满足上游 verifier 的读取前提。
        return None

    # 保留原始 session_meta 行文本，避免 wrapper 重写 JSON 字段顺序。
    return str_raw_line if str_raw_line.endswith("\n") else f"{str_raw_line}\n"

# 从已打开的 session JSONL 句柄中顺序抽取首条 session_meta 原始行。
def find_session_meta_line(handle: TextIO) -> str | None:
    """
    顺序扫描 session JSONL 文本流，返回首条可复用的 session_meta 原始行。

    参数:
        handle: 已打开的 session JSONL 文本句柄。

    返回:
        返回首条 session_meta 原始行；没有命中时返回 None。
    """

    # 顺序扫描 JSONL，命中首条 session_meta 后立即返回原始文本行。
    for str_raw_line in handle:

        # 当前行若已满足上游 verifier 的 session_meta 条件，则立即返回。
        str_session_meta_line = parse_session_meta_line(str_raw_line)  # 当前 JSONL 行对应的 session_meta 提取结果。

        # 当前行一旦满足 session_meta 条件，就立即停止继续扫描后续长日志。
        if str_session_meta_line is not None:

            # 命中 session_meta 后立即短路返回，避免继续扫描长日志。
            return str_session_meta_line

    # 扫描结束仍无命中时返回 None，让上层回退到整文件复制。
    return None

# 用 ripgrep 先把 sessions 缩到当前 exact-cwd 候选文件集合，避免全量 JSONL 扫描。
def find_matching_session_files(project: Path, sessions_root: Path) -> list[Path] | None:
    """
    用 ripgrep 预筛当前项目相关的 session JSONL 文件。

    参数:
        project: 当前 wrapper 需要验证的项目根目录。
        sessions_root: 真实 CODEX_HOME 下的 sessions 根目录。

    返回:
        返回候选 session 文件绝对路径列表；若环境不支持快速筛选则返回 None。
    """

    # sessions 根目录不存在时说明当前环境没有可供缩小的历史会话集合。
    if not sessions_root.is_dir():

        # 没有 sessions 目录时返回空列表，交由上层按无历史会话处理。
        return []

    # 收集去重后的候选文件路径，避免多个搜索字面量命中同一 JSONL。
    set_session_paths: set[Path] = set()  # 当前项目相关的 session 文件集合。

    # 逐个字面量执行固定字符串搜索，避免落回全量 Python 解析扫描。
    try:

        # 逐项推进 exact-cwd 搜索字面量，缩小 sessions 输入面。
        for str_term in session_search_terms(project):

            # 先执行当前字面量对应的 rg 搜索，再决定是否值得吸收候选 session 路径。
            completed_process_search = run_session_rg_search(str_term, sessions_root)  # 当前字面量的 rg 搜索结果。

            # rg 只有 0 和 1 是可接受结果；其他退出码通常表示环境或参数异常。
            if completed_process_search.returncode not in {0, 1}:

                # 快速筛选异常时回退到原始环境，避免 wrapper 假装拿到了完整证据。
                return None

            # 只吸收真实存在的 JSONL 文件，避免脏输出污染候选集合。
            set_session_paths.update(collect_existing_session_paths(completed_process_search.stdout))

    # rg 不存在时只能回退到原始环境，不能伪造缩面成功。
    except FileNotFoundError:

        # rg 缺失时保守回退到原始环境，不在 wrapper 内伪造完整会话视野。
        return None

    # 返回去重并排序后的候选 session 文件列表。
    return sorted(set_session_paths)

# 从单个 session JSONL 中抽出 session_meta 原始行，避免 temp HOME 复制整份长日志。
def extract_session_meta_line(path_session: Path) -> str | None:
    """
    从 session JSONL 中提取首条 session_meta 原始行。

    参数:
        path_session: 当前候选 session JSONL 文件路径。

    返回:
        返回首条 session_meta 原始行；无法安全提取时返回 None。
    """

    # 仅按官方 verifier 的读取方式顺序扫描文本行，避免自定义解析改变容错边界。
    try:

        # 以忽略坏字节的方式读取 JSONL，保持和上游 parse_session_meta 一致的编码容错行为。
        with path_session.open("r", encoding="utf-8", errors="ignore") as handle:

            # 把顺序扫描逻辑委托给 helper，保持当前函数只负责文件打开与异常边界。
            return find_session_meta_line(handle)

    # 任何读文件或 JSON 解析异常都回退到整文件复制，避免 wrapper 自己制造误判。
    except (OSError, json.JSONDecodeError):

        # 返回 None 让上层按保守路径复制原始 JSONL。
        return None

    # 没有 session_meta 记录时让上层回退到整文件复制，保持行为保守。
    return None

# 构造只含 exact-cwd 会话子集的临时 CODEX_HOME，缩小官方 verifier 的 sessions 扫描范围。
def build_delegate_environment(project: Path, installed_skill_dir: Path) -> tuple[dict[str, str], Path | None]:
    """
    为委托验证器构造必要的环境变量和可选的最小 CODEX_HOME 镜像。

    参数:
        project: 当前 wrapper 需要验证的项目根目录。
        installed_skill_dir: 应当暴露给委托验证器的真实 installed skill 根目录。

    返回:
        返回委托环境变量映射，以及可选的临时 CODEX_HOME 路径。
    """

    # 默认继承当前环境，避免 wrapper 意外丢失上游调用方的必要变量。
    dict_delegate_env = dict(os.environ)  # 透传给委托验证器的环境变量映射。

    # 无论是否切到 temp CODEX_HOME，都显式暴露真实 installed skill 目录。
    dict_delegate_env["AGENTS_MD_INSTALLED_SKILL_DIR"] = str(installed_skill_dir)  # 委托验证器应使用的真实 installed skill 根目录。

    # 先解析真实 CODEX_HOME 根目录，避免 temp 镜像再反向引用自身。
    # 先读取调用方显式传入的 CODEX_HOME 文本，避免同一表达式里重复访问环境变量。
    str_codex_home_override = dict_delegate_env.get("CODEX_HOME", "").strip()  # 当前进程显式传入的 CODEX_HOME 文本。

    # 调用方提供 CODEX_HOME 覆盖时优先沿用该根目录。
    if str_codex_home_override:

        # 解析显式覆盖的真实 CODEX_HOME 根目录。
        path_real_codex_home = Path(str_codex_home_override).expanduser().resolve()  # 当前环境显式指定的 CODEX_HOME 根目录。

    # 调用方没有显式覆盖时，需要回退到用户主目录下的默认 .codex 根目录。
    else:

        # 未显式覆盖时回退到用户主目录下的默认 .codex 根目录。
        path_real_codex_home = (Path.home() / ".codex").resolve()  # 当前用户默认的 CODEX_HOME 根目录。

    # 真实 sessions 根目录是上游 verifier 原本会全量扫描的输入面。
    path_real_sessions_root = path_real_codex_home / "sessions"  # 真实 CODEX_HOME 下的 sessions 根目录。

    # 先用 rg 缩出 exact-cwd 候选文件，再决定是否值得构造 temp CODEX_HOME。
    list_session_paths = find_matching_session_files(project, path_real_sessions_root)  # 当前项目相关的 session JSONL 候选集合。

    # 缺少可靠候选时保持原始环境，避免 wrapper 伪造完整会话证据。
    if not list_session_paths:

        # 没有可靠候选集合时继续使用真实 CODEX_HOME，让官方 verifier 自己决定。
        return dict_delegate_env, None

    # 进入 temp CODEX_HOME 构造分支，把扫描面缩到 exact-cwd 会话子集。
    try:

        # temp 目录只服务一次委托验证，任务结束后会被 wrapper 主动清理。
        path_temp_codex_home = Path(tempfile.mkdtemp(prefix="verify-agents-codex-home-")).resolve()  # 当前委托验证专用的临时 CODEX_HOME 根目录。

        # 临时 sessions 根目录保留和真实 CODEX_HOME 一致的相对布局。
        path_temp_sessions_root = path_temp_codex_home / "sessions"  # 临时 CODEX_HOME 下的 sessions 根目录。

        # 先创建 sessions 根目录，便于后续按相对路径回放命中文件。
        path_temp_sessions_root.mkdir(parents=True, exist_ok=True)

        # 全局 AGENTS baseline 仍需保留，否则官方 verifier 会把 temp HOME 误判成缺 baseline。
        path_global_agents = path_real_codex_home / "AGENTS.md"  # 真实 CODEX_HOME 下的全局 AGENTS 文件。

        # 全局 baseline 存在时才复制到 temp HOME，避免人为制造空文件。
        if path_global_agents.is_file():

            # 保持全局 AGENTS 文本完全一致，避免 temp HOME 引入额外 baseline 漂移。
            shutil.copy2(path_global_agents, path_temp_codex_home / "AGENTS.md")

        # 预先解析真实 sessions 根目录绝对路径，便于计算稳定的相对路径。
        path_real_sessions_root_resolved = path_real_sessions_root.resolve()  # 真实 sessions 根目录的绝对路径。

        # 逐个复制命中的 exact-cwd session 文件，优先只保留上游真正会读取的 session_meta 行。
        for path_session_source in list_session_paths:

            # 保留真实 sessions 相对路径，避免打乱官方 verifier 对日期层级的遍历习惯。
            path_relative_session = path_session_source.relative_to(path_real_sessions_root_resolved)  # 当前命中 session 相对真实 sessions 根目录的路径。

            # 目标路径沿用真实 sessions 的层级结构，便于官方 verifier 原样读取。
            path_session_target = path_temp_sessions_root / path_relative_session  # 当前命中 session 在 temp HOME 中的目标路径。

            # 先补齐目标父目录，再复制单个 JSONL 文件。
            path_session_target.parent.mkdir(parents=True, exist_ok=True)

            # 先尝试只抽出 session_meta 原始行，缩小上游 verifier 需要重新扫描的输入体积。
            str_session_meta_line = extract_session_meta_line(path_session_source)  # 当前命中 session 的首条 session_meta 原始行。

            # 能安全提取 session_meta 行时，只镜像上游真正读取的最小 JSONL 内容。
            if str_session_meta_line is not None:

                # 用 UTF-8 文本写入最小 session_meta JSONL，避免 temp HOME 复制整份长日志。
                path_session_target.write_text(str_session_meta_line, encoding="utf-8")

            # 无法安全提取时保守回退到整文件复制，避免 wrapper 自己引入漏判。
            else:

                # 精确保留当前命中的原始 session 文件内容与时间戳。
                shutil.copy2(path_session_source, path_session_target)

    # temp HOME 缩面构造异常时必须回退到原始环境，避免伪造受限证据输入面。
    except (OSError, ValueError):

        # temp 镜像构造失败时保守回退到原始环境，不伪造受限 sessions 视野。
        return dict_delegate_env, None

    # 只在 temp 镜像成功构造后切换 CODEX_HOME，减少官方 verifier 的扫描范围。
    dict_delegate_env["CODEX_HOME"] = str(path_temp_codex_home)  # 委托验证器读取的最小 CODEX_HOME 根目录。

    # 返回带 temp CODEX_HOME 的委托环境和后续清理所需的临时目录路径。
    return dict_delegate_env, path_temp_codex_home

# 委托验证结束后清理临时 CODEX_HOME，避免工作区外残留辅助镜像目录。
def cleanup_delegate_environment(path_temp_codex_home: Path | None) -> None:
    """
    清理 wrapper 为委托验证临时创建的 CODEX_HOME 目录。

    参数:
        path_temp_codex_home: wrapper 创建的临时 CODEX_HOME 根目录。

    返回:
        无返回值；清理失败时保持静默回收策略。
    """

    # 没有创建 temp CODEX_HOME 时无需执行任何清理动作。
    if path_temp_codex_home is None:

        # 当前 wrapper 没有临时目录需要回收。
        return

    # temp HOME 只用于一次性验证，验证结束后直接递归删除即可。
    shutil.rmtree(path_temp_codex_home, ignore_errors=True)

# 清理 wrapper 为委托验证创建的轻量项目镜像目录。
def cleanup_delegate_project_mirror(path_temp_project_root: Path | None) -> None:
    """
    清理 wrapper 为委托验证构造的轻量项目镜像目录。

    参数:
        path_temp_project_root: wrapper 创建的轻量项目镜像根目录。

    返回:
        无返回值；清理失败时保持静默回收策略。
    """

    # 没有创建轻量项目镜像时无需执行任何清理动作。
    if path_temp_project_root is None:

        # 当前 wrapper 没有轻量镜像目录需要回收。
        return

    # 轻量镜像只服务一次委托验证，结束后直接递归删除即可。
    shutil.rmtree(path_temp_project_root, ignore_errors=True)

# 调用已安装验证器并隔离短暂文件系统异常。
def run_delegate_capture(
    script_path: Path,
    args: list[str],
    delegate_env: dict[str, str] | None = None,
    retries: int = 3,
) -> subprocess.CompletedProcess[str]:
    """
    运行已安装验证器并捕获 JSON 输出。

    参数:
        script_path: 已安装 agents-md-generator 的 verify_agents.py 路径。
        args: 透传给验证器的命令行参数。
        delegate_env: 透传给委托验证器的环境变量映射。
        retries: 文件系统短暂缺失时的重试次数。

    返回:
        返回 subprocess.CompletedProcess，供调用方过滤兼容性错误。
    """

    # 重试只处理短暂文件系统读取问题，不吞掉真实治理错误。
    for attempt in range(1, retries + 1):

        # 使用当前 Python 解释器运行委托验证器，保持虚拟环境一致。
        completed_process_result: subprocess.CompletedProcess[str] = subprocess.run(  # 委托验证器本轮执行结果。
            [sys.executable, str(script_path), *args],  # 委托验证器进程命令行。
            check=False,  # 委托退出码交由 wrapper 自行解释。
            capture_output=True,  # 同时捕获 stdout 与 stderr 供 wrapper 过滤。
            env=delegate_env,  # 透传经过缩面的 CODEX_HOME 和真实 installed skill 目录。
            text=True,  # 使用文本模式读取委托器输出，便于直接做 JSON 解析。
        )  # 委托验证器的退出码、stdout 和 stderr。

        # stderr 用于识别 Windows 或 POSIX 风格的短暂文件缺失提示。
        str_stderr: str = completed_process_result.stderr or ""  # 委托验证器错误输出文本。

        # 仅当错误明确指向临时 FileNotFoundError 时才允许重试。
        bool_transient_missing_path: bool = "FileNotFoundError" in str_stderr and (  # 是否属于可重试的短暂路径缺失。
            "No such file or directory" in str_stderr or "系统找不到指定的路径" in str_stderr  # 英文与中文平台的路径缺失提示。
        )  # 可重试的短暂路径缺失标记。

        # 非临时错误或最后一次尝试都必须立即交还给调用方。
        if not bool_transient_missing_path or attempt == retries:

            # 返回委托验证器的完整结果，供上层决定是否过滤兼容性误报。
            return completed_process_result

        # 文件系统同步偶发失败时短暂停顿后重试。
        time.sleep(0.5)

    # 理论上循环总会提前返回；该返回满足静态类型检查和极端边界。
    return completed_process_result

# 判断错误文本是否属于本仓库已知的旧验证器兼容差异。
def error_is_semantic_trailing_compat(error: str) -> bool:
    """
    识别旧验证器对 semantic_assignment_allowed 的兼容性误报。

    参数:
        error: 已安装验证器返回的一条错误文本。

    返回:
        返回该错误是否可由本仓库策略兼容层接收。
    """

    # 遍历已知旧规则错误片段，避免误过滤其他治理失败。
    return any(
        str_marker in error
        for str_marker in SEMANTIC_TRAILING_ERROR_MARKERS
    )

# CLI 入口负责维持原 verify_agents 协议并套用本地兼容层。
def main(argv: list[str] | None = None) -> int:
    """
    运行 AGENTS 验证并应用本仓库的语义尾注释兼容策略。

    参数:
        argv: 命令行参数；为 None 时使用进程参数。

    返回:
        返回进程退出码。
    """

    # 透传调用方参数，保持 wrapper 原有 CLI 兼容性。
    list_args: list[str] = argv if argv is not None else sys.argv[1:]  # 委托验证器收到的参数序列。

    # 已安装 agents-md-generator 仍是主验证器。
    path_script: Path = agents_md_generator_script("verify_agents.py")  # verify_agents.py 委托脚本路径。

    # 缺少委托脚本时给出符合本仓库前缀规范的错误。
    if not path_script.exists():

        # stderr 保持错误流语义，便于治理命令识别失败原因。
        print(f"> ERR: [Python] Delegated script not found: {path_script}", file=sys.stderr)

        # 退出码 2 表示本地工具链缺失，而不是 AGENTS 内容验证失败。
        return 2

    # 还原当前 verify_agents 对应的项目根目录，供 temp CODEX_HOME 缩面使用。
    path_project: Path = project_from_args(list_args)  # 当前 wrapper 需要验证的项目根目录。

    # 先接收委托参数补齐结果，保持 installed skill 目录推导只执行一次。
    tuple_delegate_args_and_skill_dir = delegate_args_with_installed_skill_dir(  # 委托参数补齐结果与真实 installed skill 根目录。
        path_script,  # 已安装 verify_agents.py 的绝对路径。
        list_args,  # 调用方原始传入的 wrapper 参数。
    )

    # 拆出最终委托参数序列，后续直接透传给官方 verifier。
    list_delegate_args = tuple_delegate_args_and_skill_dir[0]  # 官方 verifier 需要接收的最终参数序列。

    # 拆出真实 installed skill 根目录，避免 temp HOME 指向空技能树。
    path_installed_skill_dir = tuple_delegate_args_and_skill_dir[1]  # 官方 verifier 应使用的真实 installed skill 根目录。

    # 先构造轻量项目镜像，避免 reports 等排除根目录拖慢官方 verifier 的 source_governance 遍历。
    tuple_delegate_project = build_delegate_project_mirror(path_project)  # 委托验证项目路径与可选轻量镜像根目录。

    # 取出官方 verifier 实际应检查的项目路径。
    path_delegate_project = tuple_delegate_project[0]  # 官方 verifier 应检查的真实或轻量镜像项目路径。

    # 再取出轻量镜像根目录，便于 finally 阶段统一回收。
    path_temp_project_root = tuple_delegate_project[1]  # 当前 wrapper 构造的轻量项目镜像根目录。

    # 如果启用了轻量镜像，就把项目根参数改写成镜像路径。
    list_delegate_args = delegate_args_with_project_override(  # 已改写为轻量镜像项目路径的委托参数序列。
        list_delegate_args,  # 当前 wrapper 已拼好的委托参数序列
        path_delegate_project,  # 官方 verifier 真正应检查的真实或镜像项目路径
    )

    # 先生成委托环境准备结果，再分别取出环境映射与可选 temp HOME。
    tuple_delegate_environment = build_delegate_environment(path_project, path_installed_skill_dir)  # 委托环境准备结果。

    # 取出真正要传给官方 verifier 的环境变量映射。
    dict_delegate_env = tuple_delegate_environment[0]  # 委托验证器运行时读取的环境变量映射。

    # 再取出可选 temp HOME，便于 finally 阶段统一回收。
    path_temp_codex_home = tuple_delegate_environment[1]  # 当前 wrapper 构造的一次性 temp CODEX_HOME 根目录。

    # 捕获主验证器 JSON，便于只过滤本仓库授权的兼容性差异。
    try:

        # 在 temp CODEX_HOME 生命周期内执行官方 verifier，避免 sessions 子集被提前回收。
        completed_process_result = run_delegate_capture(path_script, list_delegate_args, delegate_env=dict_delegate_env)  # 委托验证结果。

    # 官方 verifier 结束后需要统一回收一次性 temp HOME。
    finally:

        # 无论官方 verifier 成功或失败，都要回收一次性 temp CODEX_HOME。
        cleanup_delegate_environment(path_temp_codex_home)

        # 无论官方 verifier 成功或失败，都要回收轻量项目镜像目录。
        cleanup_delegate_project_mirror(path_temp_project_root)

    # stderr 原样透传，让调用方仍能看到委托验证器诊断。
    if completed_process_result.stderr:

        # 先补包装层错误摘要，再把委托验证器 stderr 原文附在后面。
        print(
            f"> ERR: [Python] Delegated verifier stderr follows.\n"
            f"{completed_process_result.stderr.rstrip()}",
            file=sys.stderr,
        )

    # 优先按 JSON 协议解析委托器结果，便于后续执行兼容层过滤。
    try:

        # 主验证器输出为 JSON；解析失败时直接透传原始结果。
        dict_payload: dict[str, Any] = json.loads(completed_process_result.stdout or "{}")  # 委托验证器 JSON 载荷。

    # 非 JSON 输出说明委托器协议异常，此时只能原样透传可见文本。
    except json.JSONDecodeError:

        # 非 JSON stdout 表示委托器协议异常，此处只负责保持原始可见输出。
        if completed_process_result.stdout:

            # 透传委托器 stdout，保留调用方原先的调试入口。
            sys.stdout.write(completed_process_result.stdout)

        # 使用委托器原始退出码，避免 wrapper 将协议错误改写成成功。
        return int(completed_process_result.returncode)

    # 解析项目根目录，用于读取本地语义尾注释策略。
    # 只有本仓库配置显式开启 semantic_assignment_allowed 时才过滤旧规则错误。
    bool_allow_semantic_trailing: bool = local_config_allows_semantic_trailing(  # 本地兼容策略授权状态。
        path_project  # 当前 wrapper 需要读取本地治理配置的项目根。
    )  # 供旧规则兼容过滤使用的授权布尔值。

    # 开启本地兼容策略后，仅移除已知旧验证器误报。
    if bool_allow_semantic_trailing:

        # 保留非兼容性错误，避免 wrapper 掩盖其他治理问题。
        list_errors: list[Any] = list(dict_payload.get("errors", []))  # 委托验证器报告的错误条目。

        # 过滤后的错误列表仍写回同一 JSON 字段，保持调用方协议稳定。
        dict_payload["errors"] = [  # 已剔除旧验证器语义尾注释误报的错误列表。
            obj_error  # 保留仍需参与兼容判定的原始错误条目。
            for obj_error in list_errors  # 遍历原始错误列表，逐项执行兼容性过滤。
            if not error_is_semantic_trailing_compat(str(obj_error))  # 仅保留非兼容性误报的真实错误。
        ]

        # 标记本地 wrapper 已接收语义尾注释策略，便于报告追踪。
        dict_payload["semantic_assignment_trailing_policy"] = "accepted_by_local_verify_wrapper"  # 兼容策略状态标记。

    # 输出过滤后的 JSON，保持调用方读取方式不变。
    sys.stdout.write(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n")

    # 任何剩余 errors 都代表治理验证失败。
    if dict_payload.get("errors"):

        # 返回 1 表示 AGENTS 内容未通过验证。
        return 1

    # 无剩余错误时 wrapper 验证通过。
    return 0

# 作为脚本运行时把 main 的返回码交给解释器。
if __name__ == "__main__":

    # raise SystemExit 是 CLI wrapper 的标准退出方式。
    raise SystemExit(main())
