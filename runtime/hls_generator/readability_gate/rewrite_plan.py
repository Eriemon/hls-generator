"""生成 HLS 注释治理计划，但不生成替换注释文本。"""

# 启用延迟注解，避免运行时解析复杂容器类型。
from __future__ import annotations

# 导入 JSON、哈希、路径和通用载荷类型。
import hashlib
import json
from pathlib import Path
from typing import Any

# 导入 HLS 注释目标收集器，用于发现可删除或需人工改写的注释。
from .comment_rules import collect_comment_quality_targets

# 导入 HLS 文件枚举与注释解析 helper。
from .cpp_lexer import (
    collect_hls_files,
    has_inline_comment,
    inline_comment_text,
    normalize_comment_text,
)

# 导入 profile 配置和门禁运行入口。
from .profiles import get_hls_profile_config
from .runner import run_hls_readability_gate

# 禁止出现在计划中的字段，避免工具直接生成可复制的替换注释。
FORBIDDEN_FIELDS = {"suggested_comment", "template_comment", "replacement_text"}  # 禁止输出字段集合

# 这些注释通常承载许可证、工具指令或向量指纹，不能被普通治理计划删除。
PRESERVE_MARKERS = (  # 需要保留的注释语义标记
    *"license copyright nolint clang-format vitis vector_hash type: pragma:".split(),  # 单词型保留标记
    "iwyu pragma",  # 双词工具指令标记
)

# 这些 HLS readability 规则需要人工语义改写，而不是脚本生成替换文本。
COMMENT_REWRITE_RULES = frozenset(  # 需要进入改写计划的规则集合
    "HG001 HG002 HG003 HG004 HG005 HG006 HG007 HG008 HG009 HG010 HG011 HG015 HG022 HG024".split()  # 人工改写相关的 HG 规则编号
)

# 生成 HLS 注释治理计划，供人工逐项处理。
def build_hls_comment_rewrite_plan(
    root: Path,
    baseline_root: Path | None = None,
    profile: str = "kernel",
) -> dict[str, Any]:
    """构建不含替换文本的 HLS 注释治理计划。

    参数:
        root: 需要审查的 HLS 工程或源码目录。
        baseline_root: 可选的基线目录，用于要求 AST/token guard。
        profile: HLS readability gate 使用的 profile 名称。

    返回:
        包含保留注释、删除范围、人工改写目标和复查命令的计划字典。
    """

    # 规范化审查根目录，确保报告路径计算稳定。
    path_resolved_root = root.resolve()  # HLS 注释治理目标根目录

    # 读取 current-project 风格下的 HLS readability 配置。
    profile_config = get_hls_profile_config(profile, style="current-project")  # 注释规则收集所用的 HLS profile 配置

    # 运行 HLS readability gate，后续从报告中提取人工改写目标。
    gate_report = run_hls_readability_gate(  # 提取人工改写目标所需的 HLS readability 报告
        path_resolved_root,  # 当前技能根目录
        profile=profile,  # HLS 注释治理 profile
        style="current-project",  # 固定使用 current-project 风格
        baseline_root=baseline_root,  # 可选 baseline 对照根目录
    )  # HLS readability gate 报告

    # 收集许可证、工具指令和向量 hash 等不可自动删除的注释。
    list_preserve_comments = _collect_preserve_comments(path_resolved_root)  # 需要保留的注释列表

    # 收集可删除的注释范围，但排除需要保留的许可证或工具指令。
    list_remove_ranges = _collect_remove_ranges(path_resolved_root, profile_config)  # 可删除注释范围列表

    # 从门禁诊断中抽取需要人工语义改写的目标。
    list_rewrite_targets = _rewrite_targets_from_report(  # 人工改写目标列表
        gate_report.to_dict().get("issues", []),  # 门禁报告里的 issue 列表载荷
        list_preserve_comments,  # 已登记保留的注释记录
    )  # 供治理计划写入的改写目标结果

    # 组装治理计划，刻意只提供上下文，不提供 replacement 文本。
    dict_plan: dict[str, Any] = {
        "version": 1,  # 计划载荷版本
        "target": "hls",  # 计划服务的语言目标
        "profile": profile,  # 当前治理 profile
        "path_policy": "relative_paths_only_absolute_roots_omitted",  # 路径只保留相对定位
        "baseline_provided": baseline_root is not None,  # 是否提供 baseline
        "comment_rewrite_required": bool(list_rewrite_targets or list_remove_ranges),  # 是否存在注释治理动作
        "ast_guard_required": baseline_root is not None,  # 是否需要 AST/token 对照回归
        "preserve_comments": list_preserve_comments,  # 不能移除的注释记录
        "remove_comment_ranges": list_remove_ranges,  # 可删除的注释区间
        "rewrite_targets": list_rewrite_targets,  # 需要人工改写的诊断目标
        "recheck_commands": _recheck_commands(path_resolved_root, baseline_root, profile),  # 完成后的复核命令
        "readability_gate_summary": gate_report.to_dict()["summary"],  # 原始门禁摘要
    }  # HLS 注释治理计划载荷

    # 防止计划字典意外包含脚本可复制的替换字段。
    _assert_forbidden_fields_absent(dict_plan)

    # 防止计划文本中出现被禁止的替换字段名。
    _assert_forbidden_strings_absent(dict_plan)

    # 返回经过安全边界检查的治理计划。
    return dict_plan

# 收集应保留的 HLS 注释。
def _collect_preserve_comments(root: Path) -> list[dict[str, Any]]:
    """返回应从删除计划中排除的 HLS 注释。

    参数:
        root: HLS 文件扫描根目录。

    返回:
        包含 path、line、reason 和 text_hash 的保留注释列表。
    """

    # 保存命中保留标记的注释记录。
    list_preserved: list[dict[str, Any]] = []  # 保留注释记录列表

    # 遍历 HLS 源码文件，逐行判断注释用途。
    for path_source in collect_hls_files(root):

        # 报告中只记录相对路径，避免泄漏本地绝对路径。
        str_rel_path = path_source.relative_to(root).as_posix()  # HLS 文件相对路径

        # 读取源码文本，忽略损坏字符以保证计划生成不中断。
        list_lines = path_source.read_text(encoding="utf-8", errors="ignore").splitlines()  # 源码行列表

        # 枚举每一行，保留一基准行号。
        for int_line_number, str_line in enumerate(list_lines, start=1):

            # 去除行首尾空白后判断整行注释形式。
            str_stripped_line = str_line.strip()  # 去空白后的源码行

            # 非整行注释且不含行内注释时跳过。
            if not str_stripped_line.startswith(("//", "/*", "*")) and not has_inline_comment(str_line):

                # 既不是注释行也没有尾注时，当前源码行不参与保留标记扫描。
                continue

            # 统一抽取注释文本，行内注释优先使用行内片段。
            str_comment_text = (
                inline_comment_text(str_line) if has_inline_comment(str_line) else normalize_comment_text(str_line)  # 优先保留尾注正文
            )  # 规范化后的注释文本

            # 使用 casefold 匹配工具标记，兼容大小写差异。
            str_lower_comment_text = str_comment_text.casefold()  # 小写化注释文本

            # 命中保留标记时把该注释加入 preserve 列表。
            if any(str_marker in str_lower_comment_text for str_marker in PRESERVE_MARKERS):

                # 命中保留标记后立即登记路径、行号和文本指纹，供后续排除人工改写目标。
                list_preserved.append(
                    {
                        "path": str_rel_path,
                        "line": int_line_number,
                        "reason": "license_tool_directive_or_vector_hash",
                        "text_hash": _small_hash(str_comment_text),
                    }
                )

    # 返回所有需要保留的注释记录。
    return list_preserved

# 收集可删除的注释范围。
def _collect_remove_ranges(root: Path, config: Any) -> list[dict[str, Any]]:
    """返回无需保留的注释删除范围。

    参数:
        root: HLS 文件扫描根目录。
        config: HLS readability profile 配置对象。

    返回:
        包含 path、start_line、end_line 和 reason 的范围列表。
    """

    # 汇总 comment_rules 判定可删的区间，后续只保留 JSON 计划真正需要的边界字段。
    list_ranges: list[dict[str, Any]] = []  # comment_rules 允许删除的注释区间

    # 对每个 HLS 文件收集注释质量目标。
    for path_source in collect_hls_files(root):

        # 当前文件的注释质量目标由 comment_rules 按 profile 计算。
        list_targets = collect_comment_quality_targets(root, path_source, config)  # 注释质量目标列表

        # 转换目标字段，同时过滤需要保留的许可证或工具指令文本。
        for dict_target in list_targets:

            # 保留类注释不进入删除范围。
            if _is_preserve_reason_text(str(dict_target.get("detail", ""))):

                # 已被保留原因覆盖的目标不进入删除范围，避免误删许可证或工具指令。
                continue

            # 追加可删除范围，字段保持 JSON 友好。
            list_ranges.append(
                {
                    "path": dict_target["path"],
                    "start_line": dict_target["start_line"],
                    "end_line": dict_target["end_line"],
                    "reason": dict_target["reason"],
                }
            )

    # 返回过滤后的删除范围列表。
    return list_ranges

# 从 readability gate 报告提取人工改写目标。
def _rewrite_targets_from_report(
    list_issues: list[dict[str, Any]],
    list_preserve_comments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """返回需要人工重写注释的 HLS 诊断目标。

    参数:
        list_issues: HLS readability gate 输出的 issue 列表。
        list_preserve_comments: 已确认需要保留的注释列表。

    返回:
        去重后的人工注释改写目标列表。
    """

    # 建立保留注释的位置集合，避免把许可证或工具指令加入改写列表。
    set_preserved_comment_lines = {
        (str(dict_item.get("path")), int(dict_item.get("line", 0)))  # 由 path 与 line 组成定位键
        for dict_item in list_preserve_comments  # 遍历保留注释记录
    }  # 保留注释位置集合

    # 保存最终要交付给人工审阅的改写目标，只输出定位和上下文，不写替换文本。
    list_targets: list[dict[str, Any]] = []  # 最终交付给人工审阅的改写目标

    # 保存已经登记过的 rule/path/line，避免重复提示。
    set_seen_targets: set[tuple[str, int, str]] = set()  # 已登记目标去重集合

    # 遍历门禁 issue，筛选需要人工改写的注释规则。
    for dict_issue in list_issues:

        # 兼容 report 中 rule 与 code 两种字段名。
        str_rule = str(dict_issue.get("rule") or dict_issue.get("code") or "")  # HLS readability 规则编号

        # 读取 issue 关联的相对路径。
        str_path = str(dict_issue.get("path") or "")  # issue 相对路径

        # 读取 issue 行号，缺省时回退到第一行。
        int_line = int(dict_issue.get("line") or 1)  # issue 一基准行号

        # 只处理注释改写相关规则。
        if str_rule not in COMMENT_REWRITE_RULES:

            # 非 comment rewrite 规则不参与人工改写计划。
            continue

        # 已确认保留的注释不进入人工改写目标。
        if (str_path, int_line) in set_preserved_comment_lines:

            # 已标记为保留的注释不再进入人工改写列表。
            continue

        # rule/path/line 组合用于去重。
        tuple_target_key = (str_path, int_line, str_rule)  # 人工改写目标去重键

        # 重复目标无需再次输出。
        if tuple_target_key in set_seen_targets:

            # 同一 rule/path/line 组合只保留一次，避免重复提示。
            continue

        # 记录已输出目标，保证计划稳定。
        set_seen_targets.add(tuple_target_key)

        # 追加人工改写目标，只给上下文，不给替换注释文本。
        list_targets.append(_rewrite_target_from_issue(dict_issue, str_rule, str_path, int_line))

    # 返回去重后的人工改写目标列表。
    return list_targets

# 构造单个人工改写目标。
def _rewrite_target_from_issue(
    dict_issue: dict[str, Any],
    str_rule: str,
    str_path: str,
    int_line: int,
) -> dict[str, Any]:
    """把单条 issue 转换为注释改写计划目标。

    参数:
        dict_issue: HLS readability gate 的单条诊断。
        str_rule: 诊断规则编号。
        str_path: 诊断所在相对路径。
        int_line: 诊断所在一基准行号。

    返回:
        只包含定位、代码摘录和语义上下文的改写目标字典。
    """

    # 优先使用门禁提供的代码摘录，缺失时回退到 detail。
    obj_payload_code_excerpt = dict_issue.get("code_excerpt") or dict_issue.get("detail")  # 人工审查代码上下文载荷

    # 返回不包含替换注释字段的改写目标。
    return {
        "rule": str_rule,
        "path": str_path,
        "line": int_line,
        "node_kind": dict_issue.get("node_kind") or "hls_comment_target",
        "code_excerpt": obj_payload_code_excerpt,
        "semantic_context": _semantic_context_for_rule(str_rule),
    }

# 返回不同 HLS 注释规则的人工审查语义提示。
def _semantic_context_for_rule(rule: str) -> str:
    """返回规则对应的人工改写语义提示。

    参数:
        rule: HLS readability 规则编号。

    返回:
        面向人工审查的英文语义提示，不包含可直接复制的注释文本。
    """

    # 规则到语义提示的映射只描述职责，不给出具体替换文本。
    dict_contexts = {
        "HG001": "rewrite existing comment in Chinese while preserving the code responsibility",  # 旧注释改写
        "HG002": "add a Chinese purpose comment for the lower block separated by a blank line",  # 下方代码块补用途注释
        "HG003": "add a blank line and adjacent Chinese purpose comment before the special HLS statement",  # 特殊语句前补说明
        "HG004": "describe the local declaration or assignment role in the datapath before the line",  # 解释局部数据通路角色
        "HG005": "add a right-side Chinese purpose note for the local declaration or assignment",  # 补右侧用途尾注
        "HG006": "replace stale or generic wording with concrete hardware/dataflow meaning",  # 替换空泛旧措辞
        "HG007": "write a file-role header describing kernel/header/testbench responsibility",  # 文件角色头注释
        "HG008": "write a function contract with parameters, output, side effects and hardware role",  # 函数契约说明
        "HG009": "explain the pragma hardware/interface/throughput intent",  # pragma 硬件意图说明
        "HG010": "explain loop bounds, transaction range, read/write object and reduction/comparison purpose",  # 循环与事务语义
        "HG011": "explain testbench vector, PASS/FAIL and vector hash semantics",  # testbench 语义说明
        "HG015": "document top port direction, protocol, depth, shape and unit",  # 顶层端口契约
        "HG022": "document DATAFLOW/STREAM channel depth and producer-consumer relationship",  # DATAFLOW/STREAM 关系说明
        "HG024": "review multi-line statement and place semantic comments at the owning construct",  # 多行语句归属注释
    }  # HLS 规则到人工审查语义提示的映射

    # 未知规则使用保守提示，仍强调不得改动 code token。
    return dict_contexts.get(rule, "rewrite HLS comment semantically without changing code tokens")

# 构造计划内的复查命令。
def _recheck_commands(root: Path, baseline_root: Path | None, profile: str) -> list[str]:
    """返回治理计划完成后的复查命令列表。

    参数:
        root: HLS 注释治理目标根目录。
        baseline_root: 可选的基线目录。
        profile: HLS readability gate 使用的 profile 名称。

    返回:
        用于人工执行复查的命令字符串列表。
    """

    # root 当前只用于保持函数签名语义，命令使用占位路径避免泄漏本地目录。
    _ = root  # 保留根目录参数以表达复查命令属于当前治理目标

    # readability gate 命令用于复查注释治理后的 HLS 规则状态。
    list_commands = [
        (
            "python -m runtime.hls_generator readability-gate "
            f"--target hls --path <target-path> --profile {profile} "
            "--style current-project --json"
        ),
    ]  # 治理后复查命令列表

    # 有 baseline 时还需要 validate 命令确认 token/AST 守卫。
    if baseline_root:

        # 只要提供 baseline，就补一条带 AST/token 对照的 validate 命令供人工回归。
        list_commands.append(
            "python -m runtime.hls_generator validate --target hls --spec <spec.json> "
            "--path <target-path> --baseline-path <baseline-path> "
            "--readiness static --no-external"
        )

    # 返回完整复查命令列表。
    return list_commands

# 生成短 hash，用于识别被保留注释而不泄漏全文。
def _small_hash(text: str) -> str:
    """返回注释文本的短 SHA256 摘要。

    参数:
        text: 需要摘要的注释文本。

    返回:
        前 16 位十六进制 SHA256 摘要。
    """

    # 对注释文本编码后计算稳定摘要。
    bytes_comment_text = text.encode("utf-8")  # 注释文本 UTF-8 字节

    # 截断摘要只用于识别同一注释，不承担安全用途。
    return hashlib.sha256(bytes_comment_text).hexdigest()[:16]

# 判断某段诊断文本是否指向需要保留的注释。
def _is_preserve_reason_text(text: str) -> bool:
    """判断诊断文本是否包含保留注释标记。

    参数:
        text: comment quality 目标中的 detail 文本。

    返回:
        命中许可证、工具指令或向量 hash 标记时返回 True。
    """

    # 使用 casefold 匹配，避免大小写差异漏掉工具标记。
    str_lower_text = text.casefold()  # 小写化诊断文本

    # 命中任意保留标记即不应进入删除范围。
    return any(str_marker in str_lower_text for str_marker in PRESERVE_MARKERS)

# 递归检查计划字典是否含有禁止字段。
def _assert_forbidden_fields_absent(value: Any) -> None:
    """确认计划载荷没有脚本可复制的替换字段。

    参数:
        value: 需要递归检查的计划片段。

    返回:
        无业务返回值；发现禁止字段时通过异常中断流程。

    异常:
        ValueError: 当任意 dict key 命中禁止字段时抛出。
    """

    # 字典需要同时检查自身 key 与嵌套值。
    if isinstance(value, dict):

        # 计算当前字典 key 中的禁止字段。
        set_forbidden_keys = FORBIDDEN_FIELDS.intersection(value)  # 当前字典命中的禁止字段集合

        # 命中禁止字段时立即阻断计划输出。
        if set_forbidden_keys:

            # 一旦发现禁止字段，立即阻断计划写出，防止脚本生成可复制替换文本。
            raise ValueError(f"> ERR: [Python] comment plan contains forbidden fields: {sorted(set_forbidden_keys)}")

        # 递归检查所有字典值。
        for obj_payload_value in value.values():

            # 每个字典 value 都要继续递归检查，避免深层嵌套漏检。
            _assert_forbidden_fields_absent(obj_payload_value)

    # 列表需要递归检查每个元素。
    elif isinstance(value, list):

        # 逐项检查列表元素，覆盖嵌套计划结构。
        for obj_payload_value in value:

            # 列表元素统一回到总入口递归，复用同一套禁止字段检查规则。
            _assert_forbidden_fields_absent(obj_payload_value)

# 递归检查计划文本是否包含禁止字段名。
def _assert_forbidden_strings_absent(value: Any) -> None:
    """确认计划载荷文本没有禁止字段名。

    参数:
        value: 需要递归检查的计划片段。

    返回:
        无业务返回值；发现禁止字段名时通过异常中断流程。

    异常:
        ValueError: 当任意字符串包含禁止字段名时抛出。
    """

    # 字典需要检查 key 和 value，防止禁止词藏在键名中。
    if isinstance(value, dict):

        # 字典路径交给专门 helper，避免递归分派函数嵌套过深。
        _assert_forbidden_strings_absent_in_dict(value)

        # 当前分支已经完成所有字典条目检查。
        return

    # 列表分支把每个元素重新送回总入口，保持 dict/list/str 三种路径共用一套递归规则。
    if isinstance(value, list):

        # 列表路径交给专门 helper，保持主分派函数只表达类型路由。
        _assert_forbidden_strings_absent_in_list(value)

        # 当前分支已经完成所有列表元素检查。
        return

    # 字符串需要按小写形式检测禁止词。
    if isinstance(value, str):

        # 字符串路径负责真正的禁止词命中判断。
        _assert_forbidden_strings_absent_in_text(value)

# 检查字典载荷中的禁止字段名文本。
def _assert_forbidden_strings_absent_in_dict(dict_value: dict[Any, Any]) -> None:
    """递归检查字典键和值中的禁止字段名文本。

    参数:
        dict_value: 需要检查的计划字典片段。

    返回:
        本函数无业务返回值；发现禁止文本时直接抛出异常。

    异常:
        ValueError: 当任意嵌套文本包含禁止字段名时抛出。
    """

    # 遍历字典条目，同时检查键和值。
    for obj_payload_key, obj_payload_value in dict_value.items():

        # 字典 key 也可能携带被禁止的替换字段名。
        _assert_forbidden_strings_absent(obj_payload_key)

        # 字典 value 覆盖计划主体中的嵌套文本。
        _assert_forbidden_strings_absent(obj_payload_value)

# 检查列表载荷中的禁止字段名文本。
def _assert_forbidden_strings_absent_in_list(list_value: list[Any]) -> None:
    """递归检查列表元素中的禁止字段名文本。

    参数:
        list_value: 需要检查的计划列表片段。

    返回:
        本函数无业务返回值；发现禁止文本时直接抛出异常。

    异常:
        ValueError: 当任意嵌套文本包含禁止字段名时抛出。
    """

    # 遍历列表元素，覆盖嵌套计划结构。
    for obj_payload_value in list_value:

        # 每个元素继续进入统一递归分派。
        _assert_forbidden_strings_absent(obj_payload_value)

# 检查单段文本中的禁止字段名。
def _assert_forbidden_strings_absent_in_text(str_value: str) -> None:
    """检查字符串是否包含脚本可复制的替换字段名。

    参数:
        str_value: 需要检查的计划文本。

    返回:
        本函数无业务返回值；发现禁止文本时直接抛出异常。

    异常:
        ValueError: 当字符串包含禁止字段名时抛出。
    """

    # casefold 后再匹配，避免大小写变化绕过安全边界。
    str_lower_value = str_value.casefold()  # 小写化计划文本

    # 收集当前字符串中出现的禁止字段名。
    list_forbidden_field_names = [
        str_field  # 命中的禁止字段名
        for str_field in FORBIDDEN_FIELDS  # 遍历所有禁止字段
        if str_field in str_lower_value  # 仅保留当前文本实际出现的字段
    ]  # 计划文本中命中的禁止字段名

    # 任何命中都说明计划可能包含可复制替换文本。
    if list_forbidden_field_names:

        # 检测到禁止字段名时立刻阻断，避免计划文本携带可复制替换字段。
        raise ValueError(
            f"> ERR: [Python] comment plan contains forbidden text: {sorted(list_forbidden_field_names)}"
        )

# 将 HLS 注释治理计划写入 JSON 文件。
def write_hls_comment_rewrite_plan(plan: dict[str, Any], out: str | Path) -> None:
    """写入通过安全检查的 HLS 注释治理计划。

    参数:
        plan: build_hls_comment_rewrite_plan 生成的治理计划。
        out: JSON 输出路径。

    返回:
        无业务返回值；函数把治理计划写入目标 JSON 文件。

    异常:
        ValueError: 当计划包含禁止字段或禁止文本时抛出。
    """

    # 写文件前再次检查禁止字段，防止调用方传入外部修改后的计划。
    _assert_forbidden_fields_absent(plan)

    # 写文件前再次检查禁止文本，保证安全边界贴近 I/O。
    _assert_forbidden_strings_absent(plan)

    # 规范化输出路径，支持字符串和 Path 两种输入。
    path_output = Path(out)  # 治理计划输出路径

    # 创建父目录，保证嵌套 reports 路径可以写入。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 写入 UTF-8 JSON，保留中文可读性并以换行结尾。
    path_output.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
