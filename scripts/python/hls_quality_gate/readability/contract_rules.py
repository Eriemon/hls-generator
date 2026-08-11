"""检查 HLS 函数契约与 top 端口契约。"""

# 启用延迟注解，避免类型提示影响运行期导入。
from __future__ import annotations

# 正则和路径工具用于契约文本、函数签名与报告路径处理。
import re
from pathlib import Path

# 轻量 C/C++ 解析器提供函数签名和注释归一化能力。
from .cpp_lexer import normalize_comment_text, parse_functions

# HLS 可读性 profile 配置提供契约开关。
from .profiles import HlsProfileConfig

# 统一 issue 结构保持报告契约稳定。
from .report import HlsGateIssue, make_issue

# 用空格拆词的 helper 让常量词表保持单行赋值，避免多行元素触发 current-project 赋值门禁。
def _tuple_terms(str_terms: str) -> tuple[str, ...]:
    """把空格分隔的词表文本转换成稳定元组。

    参数:
        str_terms: 用单个空格分隔的词表文本，dtype=str，unit=term list text。

    返回:
        按空格拆分后的不可变词表元组，dtype=tuple[str, ...]，unit=term tuple。
    """

    # 去掉首尾空白后按单词边界拆开，避免空字符串进入词表。
    list_terms = str_terms.strip().split()  # 当前词表文本拆出的词项列表

    # 返回不可变元组，供规则常量稳定复用。
    return tuple(list_terms)

# 函数 contract 必须显式写出这四个固定字段。
REQUIRED_FUNCTION_CONTRACT_FIELDS = _tuple_terms("职责： 参数： 返回： 副作用：")  # 函数 contract 的固定字段集合

# 这些短语说明 contract 仍停留在占位层。
PLACEHOLDER_CONTRACT_TERMS = _tuple_terms("函数说明 参数说明 返回说明 副作用说明 todo 待补充 placeholder 描述函数")  # contract 占位短语

# 每个 top 端口都必须覆盖方向、协议和 depth/shape/unit 三类事实。
PORT_DIRECTION_TERMS = _tuple_terms("方向 input output inout")  # 方向事实关键词

# PORT_PROTOCOL_TERMS 收拢 top 端口 contract 允许出现的协议关键词。
PORT_PROTOCOL_TERMS = _tuple_terms("协议 axis m_axi s_axilite stream bundle control")  # 协议事实关键词

# PORT_SHAPE_TERMS 收拢 top 端口 contract 允许出现的 depth/shape/unit 事实词。
PORT_SHAPE_TERMS = _tuple_terms("depth shape unit 长度 维度 事务 标量 一维 二维")  # depth/shape/unit 事实关键词

# check_contract_rules 是 runner 汇总 HG008/HG015 的入口。
def check_contract_rules(
    root: Path,
    path: Path,
    config: HlsProfileConfig,
    *,
    top_function: str | None = None,
) -> list[HlsGateIssue]:
    """检查单个 HLS 文件中的函数 contract 与 top 端口 contract。

    参数:
        root: readability gate 扫描根目录，dtype=Path，unit=path。
        path: 当前被检查的 HLS 文件路径，dtype=Path，unit=path。
        config: 当前 profile 的 contract 开关集合，dtype=HlsProfileConfig，unit=profile config。
        top_function: 可选的 top function 名称；命中时追加 HG015 检查，dtype=str | None，unit=function name。

    返回:
        当前文件触发的 HG008/HG015 问题列表，dtype=list[HlsGateIssue]，unit=issue list。
    """

    # 报告路径统一转成 POSIX 形式，保证跨平台输出稳定。
    str_rel_path = path.relative_to(root).as_posix()  # 当前文件在报告中的相对路径

    # 按物理行读取源码，供轻量函数解析器和注释块扫描复用。
    list_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()  # 当前文件源码行

    # 函数列表由轻量解析器负责抽取，兼容多行签名。
    list_functions = parse_functions(list_lines)  # 当前文件函数信息列表

    # 问题列表按源码顺序追加，方便后续稳定排序。
    list_issues: list[HlsGateIssue] = []  # 契约规则问题集合

    # 逐个函数检查 HG008，并在 top function 上追加 HG015。
    for function_info in list_functions:

        # 纯声明不要求函数 contract；它们的可读性由签名和文件头规则承担。
        if function_info.is_declaration:

            # 跳过纯声明，继续扫描下一个拥有函数体的对象。
            continue

        # 只接受紧邻签名前方的连续 // contract 块。
        str_contract_block = _line_comment_block_above(list_lines, function_info.signature_start_line)  # 当前函数上方的连续 // contract 块

        # HG008 要求所有被检查函数都显式给出四段 contract。
        if config.require_function_contract:

            # 先把当前函数的四段 contract 合同检查登记到问题列表。
            _append_function_contract_issue(
                list_issues,
                str_rel_path,

                # 把签名行号和签名文本一起交给 HG008 生成稳定定位信息。
                function_info.signature_start_line,
                function_info.signature,
                function_info.params,
                function_info.return_type,
                str_contract_block,
            )

        # 只有 top function 额外承担逐项端口 contract。
        if (
            config.require_top_port_contract
            and top_function
            and function_info.name == top_function
        ):

            # 对命中的 top function 再追加逐端口 contract 完整性检查。
            list_issues.extend(_top_port_contract_issues(
                str_rel_path,
                function_info.signature_start_line,
                function_info.signature,
                function_info.params,
                str_contract_block,
            ))

    # 返回当前文件累计的 contract 问题。
    return list_issues

# _append_function_contract_issue 负责 HG008 的主判定。
def _append_function_contract_issue(
    issues: list[HlsGateIssue],
    rel_path: str,
    line_number: int,
    signature: str,
    params: tuple[str, ...], return_type: str, contract_block: str | None,
) -> None:
    """根据函数上方 contract 块追加 HG008。

    参数:
        issues: 当前文件累计的问题列表，dtype=list[HlsGateIssue]，unit=issue list。
        rel_path: 当前报告使用的相对路径，dtype=str，unit=relative path。
        line_number: 函数签名起始行号，dtype=int，unit=line number。
        signature: 当前函数签名文本，dtype=str，unit=signature text。
        params: 当前函数参数名元组，dtype=tuple[str, ...]，unit=parameter names。
        return_type: 当前函数返回类型文本，dtype=str，unit=return type text。
        contract_block: 紧邻签名前方的连续 `//` contract 文本，dtype=str | None，unit=contract text。

    返回:
        无业务返回值；本函数只向 `issues` 追加 HG008 诊断，dtype=None，unit=not applicable。
    """

    # 没有连续 // contract 块时直接阻断。
    if not contract_block:

        # 把缺失 contract 的阻断诊断登记到问题列表。
        issues.append(
            make_issue(
                "HG008",
                "error",
                rel_path,
                line_number,
                "函数签名前必须紧邻连续 `//` contract，并显式包含职责、参数、返回和副作用四段。",
                detail=signature,
                node_kind="function_contract",
                code_excerpt=signature,
            )
        )

        # 缺少 contract 时不再继续检查下方字段。
        return

    # 占位式 contract 不能满足函数契约要求。
    if _contract_uses_placeholder(contract_block):

        # 把占位 contract 的阻断诊断登记到问题列表。
        issues.append(
            make_issue(
                "HG008",
                "error",
                rel_path,
                line_number,
                "函数 contract 不能使用“函数说明”“待补充”等占位短语，必须写出真实职责与接口语义。",
                detail=contract_block,
                node_kind="function_contract",
                code_excerpt=signature,
            )
        )

        # contract 仍是模板文本时无需继续做字段细检。
        return

    # 固定字段按 label 提取，便于逐项核对缺失与空内容。
    dict_fields = _contract_fields(contract_block)  # 当前 contract 的字段映射

    # 先准备缺失字段名容器，后续按固定字段顺序填充。
    list_missing_fields: list[str] = []  # 当前 contract 缺失的固定字段

    # 逐个固定字段确认 contract 是否存在缺口。
    for str_field in REQUIRED_FUNCTION_CONTRACT_FIELDS:

        # 当前 contract 缺少该字段时，把去掉冒号的字段名加入诊断列表。
        if str_field not in dict_fields:

            # 保存当前缺失字段名，供错误消息直接展开。
            list_missing_fields.append(str_field.rstrip("："))

    # 缺失字段时直接指出具体缺口。
    if list_missing_fields:

        # 记录缺失固定字段的具体阻断原因。
        issues.append(
            make_issue(
                "HG008",
                "error",
                rel_path,
                line_number,
                f"函数 contract 缺少固定字段：{', '.join(list_missing_fields)}。",
                detail=contract_block,
                node_kind="function_contract",
                code_excerpt=signature,
            )
        )

        # 字段缺口已经足以判定 HG008 失败。
        return

    # 先准备空字段名容器，后续逐项登记只有标签没有正文的字段。
    list_empty_fields: list[str] = []  # 当前 contract 中正文为空的字段

    # 逐项确认已经出现的字段正文是否真正承载语义。
    for str_field, str_content in dict_fields.items():

        # 字段正文为空壳时，把字段名加入空字段列表。
        if not _field_content_is_meaningful(str_content):

            # 保存当前空字段名，供错误消息聚合展示。
            list_empty_fields.append(str_field.rstrip("："))

    # 空字段说明 agent 只补了标签，没有补真实语义。
    if list_empty_fields:

        # 记录标签存在但正文缺失的 contract 诊断。
        issues.append(
            make_issue(
                "HG008",
                "error",
                rel_path,
                line_number,
                f"函数 contract 字段正文不能为空：{', '.join(list_empty_fields)}。",
                detail=contract_block,
                node_kind="function_contract",
                code_excerpt=signature,
            )
        )

        # 字段正文为空时不再继续检查参数或返回细节。
        return

    # 无参数函数必须明确写出“无参数”，有参数函数不能把参数段写成无参数。
    str_parameter_contract = dict_fields["参数："]  # 参数字段正文

    # 先处理“函数确实有参数”的收紧分支。
    if params:

        # 有参数函数必须避免把参数段写成“无参数”。
        if "无参数" in str_parameter_contract:

            # 记录参数段伪装成无参数的阻断诊断。
            issues.append(
                make_issue(
                    "HG008",
                    "error",
                    rel_path,
                    line_number,
                    "有业务参数的函数不能把 `参数：` 段写成“无参数”。",
                    detail=str_parameter_contract,
                    node_kind="function_contract",
                    code_excerpt=signature,
                )
            )

            # 参数段语义已经自相矛盾，直接结束当前函数检查。
            return

    # 再处理“函数没有参数”但 contract 没写无参数的缺口。
    if not params and "无参数" not in str_parameter_contract:

        # 无参数函数必须显式暴露“无参数”占位语义。
        issues.append(
            make_issue(
                "HG008",
                "error",
                rel_path,
                line_number,
                "无参数函数的 `参数：` 段必须显式写出“无参数”。",
                detail=str_parameter_contract,
                node_kind="function_contract",
                code_excerpt=signature,
            )
        )

        # 无参数字段缺失时无需继续检查返回段。
        return

    # void 返回必须显式写出无返回；非 void 返回不能伪装成无返回。
    str_return_contract = dict_fields["返回："]  # 返回字段正文

    # 单独缓存返回类型是否为 void，后续分支复用同一判断结果。
    bool_void_return = _return_type_is_void(return_type)  # 当前函数是否 void 返回

    # 先检查 void 函数是否显式声明无返回。
    if bool_void_return and "无返回" not in str_return_contract:

        # 记录 void 函数缺少显式无返回声明的阻断诊断。
        issues.append(
            make_issue(
                "HG008",
                "error",
                rel_path,
                line_number,
                "void 函数的 `返回：` 段必须显式写出“无返回”或“无返回值”。",
                detail=str_return_contract,
                node_kind="function_contract",
                code_excerpt=signature,
            )
        )

        # void 返回字段缺失时不再继续检查非 void 分支。
        return

    # 再检查非 void 函数是否错误写成无返回。
    if not bool_void_return and "无返回" in str_return_contract:

        # 记录非 void 函数错误标记为无返回的阻断诊断。
        issues.append(
            make_issue(
                "HG008",
                "error",
                rel_path,
                line_number,
                "非 void 函数的 `返回：` 段不能写成“无返回”。",
                detail=str_return_contract,
                node_kind="function_contract",
                code_excerpt=signature,
            )
        )

# _top_port_contract_issues 负责 HG015 的逐端口检查。
def _top_port_contract_issues(
    rel_path: str,
    line_number: int,
    signature: str,
    params: tuple[str, ...],
    contract_block: str | None,
) -> list[HlsGateIssue]:
    """检查 top function 的逐项端口 contract。

    参数:
        rel_path: 当前报告使用的相对路径，dtype=str，unit=relative path。
        line_number: top function 签名起始行号，dtype=int，unit=line number。
        signature: top function 的签名文本，dtype=str，unit=signature text。
        params: top function 端口名元组，dtype=tuple[str, ...]，unit=port names。
        contract_block: top function 上方的连续 `//` contract 文本，dtype=str | None，unit=contract text。

    返回:
        当前 top function 触发的 HG015 问题列表，dtype=list[HlsGateIssue]，unit=issue list。
    """

    # 无参数 top function 没有端口 contract 需要检查。
    if not params:

        # 没有端口时无需继续执行逐端口合同检查。
        return []

    # 顶层端口 contract 必须复用同一个连续 // block。
    if not contract_block:

        # 直接返回“整体缺少 top 端口 contract”这一条阻断诊断。
        return [
            make_issue(
                "HG015",
                "error",
                rel_path,
                line_number,
                "top function contract 必须逐项说明每个端口的名称、方向、协议、depth/shape/unit。",
                detail=signature,
                node_kind="top_port_contract",
                code_excerpt=signature,
            )
        ]

    # 逐端口问题按参数顺序累计。
    list_issues: list[HlsGateIssue] = []  # top 端口 contract 问题集合

    # 每个参数在 contract 中都需要拥有自己的事实片段。
    for int_index, str_param_name in enumerate(params):

        # 当前端口对应的 contract 片段按参数名边界切分。
        str_port_segment = _parameter_contract_segment(contract_block, params, int_index)  # 当前端口的 contract 片段

        # 先确认 contract 至少显式提到了当前端口名称。
        if not str_port_segment:

            # 把缺少端口名称片段的 HG015 问题登记下来。
            list_issues.append(
                make_issue(
                    "HG015",
                    "error",
                    rel_path,
                    line_number,
                    "top function contract 必须逐项写出每个端口名称。",
                    detail=f"missing port={str_param_name}",
                    node_kind="top_port_contract",
                    code_excerpt=signature,
                )
            )

            # 当前端口缺少片段时，继续检查下一个端口名称。
            continue

        # 每个端口片段都必须覆盖方向、协议和 shape/depth/unit 三类事实。
        if not _segment_contains_any(str_port_segment, PORT_DIRECTION_TERMS):

            # 记录当前端口缺少方向事实的诊断。
            list_issues.append(
                make_issue(
                    "HG015",
                    "error",
                    rel_path,
                    line_number,
                    "top function contract 的每个端口都必须写出方向。",
                    detail=f"port={str_param_name}",
                    node_kind="top_port_contract",
                    code_excerpt=signature,
                )
            )

        # 继续检查当前端口的协议事实是否写全。
        if not _segment_contains_any(str_port_segment, PORT_PROTOCOL_TERMS):

            # 记录当前端口缺少协议事实的诊断。
            list_issues.append(
                make_issue(
                    "HG015",
                    "error",
                    rel_path,
                    line_number,
                    "top function contract 的每个端口都必须写出协议。",
                    detail=f"port={str_param_name}",
                    node_kind="top_port_contract",
                    code_excerpt=signature,
                )
            )

        # 最后检查当前端口是否暴露 depth/shape/unit 事实。
        if not _segment_contains_any(str_port_segment, PORT_SHAPE_TERMS):

            # 把当前端口缺少空间形态或单位信息的诊断登记下来。
            list_issues.append(
                make_issue(
                    "HG015",
                    "error",
                    rel_path,
                    line_number,
                    "top function contract 的每个端口都必须写出 depth、shape 或 unit 事实。",
                    detail=f"port={str_param_name}",
                    node_kind="top_port_contract",
                    code_excerpt=signature,
                )
            )

    # 返回逐端口 contract 问题列表。
    return list_issues

# _line_comment_block_above 只接受紧邻函数签名前的连续 // 注释块。
def _line_comment_block_above(lines: list[str], signature_start_line: int) -> str | None:
    """提取函数签名前紧邻的连续 `//` 注释块。

    参数:
        lines: 当前文件的源码物理行列表，dtype=list[str]，unit=source lines。
        signature_start_line: 函数签名起始的一基行号，dtype=int，unit=line number。

    返回:
        找到时返回按源码顺序拼接后的 contract 文本；缺失时返回 None，dtype=str | None，unit=contract text。
    """

    # 先定位到函数签名前一行的零基索引。
    int_index = signature_start_line - 2  # 函数签名前一行的零基索引

    # 文件开头之前没有可用 contract。
    if int_index < 0:

        # 签名位于文件起始处时，不可能存在上方连续注释块。
        return None

    # 注释块需要逆序收集，最后再恢复源码顺序。
    list_contract_lines: list[str] = []  # 连续 // contract 行集合

    # 只接受紧邻签名的连续注释，不跨空行也不接受块注释残片。
    while int_index >= 0:

        # 先读取当前回溯行的去噪文本，再判断它是否仍属于 contract 块。
        str_line = lines[int_index].strip()  # 当前待检查源码行

        # 空行会切断 contract 块。
        if not str_line:

            # 命中空行后停止向上扩展当前 contract 块。
            break

        # 只有 comment-only 的 // 行才算合法 contract。
        if not str_line.startswith("//"):

            # 命中非 `//` 代码或块注释残片后结束扫描。
            break

        # 收集归一化后的 contract 正文。
        list_contract_lines.append(normalize_comment_text(lines[int_index]))

        # 当前行已收集完毕，继续向更上方回溯。
        int_index -= 1  # 继续回溯上一行源码

    # 没有收集到任何 // 行时说明缺少合法 contract。
    if not list_contract_lines:

        # 没有合法 `//` 注释行时返回空结果。
        return None

    # 源码顺序恢复后拼成多行 contract 文本。
    return "\n".join(reversed(list_contract_lines)).strip()

# _contract_uses_placeholder 判断 contract 是否仍停留在模板层。
def _contract_uses_placeholder(contract_block: str) -> bool:
    """判断 contract 是否包含占位短语。

    参数:
        contract_block: 待判断的 contract 文本，dtype=str，unit=contract text。

    返回:
        命中任一占位短语时返回 True，否则返回 False，dtype=bool，unit=placeholder flag。
    """

    # 统一大小写后匹配英文和中文占位词。
    str_lowered_contract = contract_block.casefold()  # 小写化后的 contract 文本

    # 任一占位短语命中都说明 contract 仍未写实。
    return any(str_term in str_lowered_contract for str_term in PLACEHOLDER_CONTRACT_TERMS)

# _contract_fields 提取 contract 中的固定字段正文。
def _contract_fields(contract_block: str) -> dict[str, str]:
    """提取 contract 的固定字段正文。

    参数:
        contract_block: 待切分字段的 contract 文本，dtype=str，unit=contract text。

    返回:
        固定字段到字段正文的映射字典，dtype=dict[str, str]，unit=field mapping。
    """

    # 固定字段按源码顺序登记，便于后续逐项核对。
    dict_fields: dict[str, str] = {}  # contract 固定字段到正文的映射

    # 逐行扫描 `职责：`、`参数：` 等固定标签。
    for str_line in contract_block.splitlines():

        # 每行只会归属一个固定字段。
        for str_field in REQUIRED_FUNCTION_CONTRACT_FIELDS:

            # 命中固定字段前缀后记录正文。
            if str_line.startswith(str_field):

                # 保存当前字段的正文内容，供后续完整性判断复用。
                dict_fields[str_field] = str_line[len(str_field):].strip()  # 当前字段对应的正文文本

                # 当前行已经归属某个字段，不再重复匹配剩余标签。
                break

    # 返回已提取到的字段映射。
    return dict_fields

# _field_content_is_meaningful 判断字段正文是否为空壳。
def _field_content_is_meaningful(content: str) -> bool:
    """判断 contract 字段正文是否有实际内容。

    参数:
        content: 待判断的字段正文，dtype=str，unit=field text。

    返回:
        去噪后仍包含有效语义字符时返回 True，否则返回 False，dtype=bool，unit=meaningful flag。
    """

    # 去掉常见中文标点后仍然要保留至少一个非标点字符。
    str_compact_content = re.sub(r"[\s：:；;，,。.!！?？、]+", "", content or "")  # 去噪后的字段正文

    # 非空说明该字段确实承载了某种语义。
    return bool(str_compact_content)

# _parameter_contract_segment 取出当前参数在 contract 中的局部片段。
def _parameter_contract_segment(contract_block: str, params: tuple[str, ...], index: int) -> str:
    """按参数名边界截取当前端口的 contract 片段。

    参数:
        contract_block: top function 的完整 contract 文本，dtype=str，unit=contract text。
        params: top function 端口名元组，dtype=tuple[str, ...]，unit=port names。
        index: 当前需要切分的端口下标，dtype=int，unit=index。

    返回:
        当前端口在 contract 中对应的局部片段；找不到时返回空字符串，dtype=str，unit=port contract segment。
    """

    # 当前参数名用于在 contract 中定位片段起点。
    str_param_name = params[index]  # 当前参数名

    # 小写文本用于大小写无关查找。
    str_lowered_contract = contract_block.casefold()  # 小写化 contract 文本

    # 同步缓存当前端口名的小写文本，供回退匹配路径复用。
    str_lowered_param = str_param_name.casefold()  # 小写化参数名

    # 预先构造显式端口锚点，优先定位 `端口：<name>` 这一类合同文本。
    str_port_anchor = f"端口：{str_param_name}".casefold()  # 优先使用端口锚点定位当前参数片段

    # 当前参数在 contract 中的首次出现位置。
    int_start_index = str_lowered_contract.find(str_port_anchor)  # 当前端口 contract 起点

    # 锚点未命中时，退化到纯参数名查找。
    if int_start_index < 0:

        # 缺少显式端口锚点时退化为参数名级别匹配。
        int_start_index = str_lowered_contract.find(str_lowered_param)  # 退化后的参数名起点

    # 找不到参数名时返回空字符串，由调用方报告缺失端口项。
    if int_start_index < 0:

        # 缺少任何可用锚点时返回空片段。
        return ""

    # 默认片段一直延伸到 contract 结尾。
    int_end_index = len(contract_block)  # 当前端口 contract 片段终点

    # 向后寻找下一个参数名，作为当前片段的结束边界。
    for str_next_param_name in params[index + 1:]:

        # 优先用下一个端口的显式锚点切分当前片段。
        str_next_anchor = f"端口：{str_next_param_name}".casefold()  # 下一个端口 contract 锚点

        # 先尝试命中下一个端口锚点，确定当前片段的结束位置。
        int_candidate_end = str_lowered_contract.find(str_next_anchor, int_start_index + 1)  # 优先采用显式端口锚点定位片段终点

        # 下一个端口锚点缺失时，退化到参数名级别匹配。
        if int_candidate_end < 0:

            # 缺少端口锚点时退化为参数名匹配，兼容旧 fixture 文本。
            int_candidate_end = str_lowered_contract.find(str_next_param_name.casefold(), int_start_index + 1)  # 退化后的后继参数边界

        # 一旦找到有效后继边界，就用它截断当前端口片段。
        if int_candidate_end >= 0:

            # 用后继端口命中的起点裁掉当前端口片段尾部，避免吞入下一个端口说明。
            int_end_index = int_candidate_end  # 当前端口 contract 切片终止下标

            # 找到首个后继端口边界后即可结束向后扫描。
            break

    # 返回当前参数对应的 contract 片段。
    return contract_block[int_start_index:int_end_index]

# _segment_contains_any 判断局部 contract 片段是否覆盖某类事实。
def _segment_contains_any(segment: str, terms: tuple[str, ...]) -> bool:
    """判断端口 contract 片段是否命中任一事实关键词。

    参数:
        segment: 当前端口的 contract 局部片段，dtype=str，unit=contract segment。
        terms: 允许命中的事实关键词元组，dtype=tuple[str, ...]，unit=term tuple。

    返回:
        片段命中任一关键词时返回 True，否则返回 False，dtype=bool，unit=contains flag。
    """

    # 小写化后统一做包含判断。
    str_lowered_segment = segment.casefold()  # 小写 contract 片段

    # 命中任一关键词即可通过该类事实检查。
    return any(str_term.casefold() in str_lowered_segment for str_term in terms)

# _return_type_is_void 判断函数返回类型是否为 void。
def _return_type_is_void(return_type: str) -> bool:
    """判断函数返回类型是否为 void。

    参数:
        return_type: 待判断的函数返回类型文本，dtype=str，unit=return type text。

    返回:
        返回类型命中 `void` 单词边界时返回 True，否则返回 False，dtype=bool，unit=void flag。
    """

    # 去噪后统一做单词边界匹配。
    str_compact_return_type = " ".join((return_type or "").split())  # 压缩空白后的返回类型文本

    # 命中 void 单词边界时认定为无返回值函数。
    return bool(re.search(r"\bvoid\b", str_compact_return_type))
