#!/usr/bin/env python3
"""整理 confidence-loop 的最终信心状态和残余风险。"""

# 启用延迟标注，避免运行期导入额外类型依赖
from __future__ import annotations

# 结构化门禁结果使用标准库类型表达
from typing import Any

# 远端验收通过状态由 confidence_loop 在运行期同步
PASS_STATUS = "passed"  # 门禁成功状态文本

# 汇总信心时不计入本地通过性的远端门禁名称
REMOTE_GATE_NAMES = {
    "remote_pytest",  # 远端 pytest 回归门禁
    "remote_vitis_acceptance",  # 远端 Vitis 验收门禁
    "remote_board_acceptance",  # 远端板卡验收门禁
    "remote_family_coverage",  # Tier 1 族覆盖门禁
}  # 远端门禁名称集合

# REQUIRED_LOCAL_GATE_NAMES 定义 local/factual 请求在本地阶段至少要看到哪些 gate。
# `_missing_gate_names(REQUIRED_LOCAL_GATE_NAMES, gates)` 会据此生成 `missing_local`，用于指出当前结果还缺哪些本地门禁。
# 只要这里列出的 smoke、编译、自检与治理门禁有缺项，最终报告就不能保留高信心结论。
REQUIRED_LOCAL_GATE_NAMES = {
    "smoke",  # 技能级最小链路 smoke
    "compileall",  # runtime 导入编译检查
    "quick_validate",  # 技能结构快速自检
    "verify_agents",  # AGENTS 治理验证
    "manage_docs_verify",  # 会话、handoff 与 memory 文档治理验证
    "manage_dirs_verify",  # 目录治理验证
    "skill_dependencies",  # 技能依赖一致性检查
    "copyright_term_scan",  # 版权敏感词扫描
    "release_sensitivity_scan",  # 发布敏感内容扫描
    "forbidden_reference_names",  # 禁止引用名称扫描
    "example_mock_validation",  # 随包示例 mock 产物一致性验证
    "comment_policy",  # 注释策略门禁
    "forward_test",  # 前向测试门禁
    "route_contract",  # 远端路由契约
    "board_acceptance_declarations",  # board 声明契约
    "remote_directory_contract",  # 远端目录契约
}  # 必需本地门禁名称集合

# 提取门禁状态文本
def _gate_status(dict_gate: dict[str, Any] | None) -> str:
    """
    返回单个门禁的状态字段。

    :param dict_gate: confidence-loop 中某个门禁的结构化结果。
    :return: 规范化后的状态字符串；缺少门禁时返回空字符串。
    """

    # 缺失门禁表示该阶段没有产出可判断状态
    if dict_gate is None:

        # 返回空状态，方便调用方继续使用字符串比较
        return ""

    # 将状态字段归一化为字符串，避免 None 进入比较集合
    str_status = str(dict_gate.get("status") or "")  # 门禁状态文本

    # 返回供上层信心分支判断的状态
    return str_status

# 判断门禁是否处于通过状态
def _gate_passed(dict_gate: dict[str, Any] | None) -> bool:
    """
    判断单个门禁是否通过。

    :param dict_gate: confidence-loop 中某个门禁的结构化结果。
    :return: 门禁状态是否等于当前成功状态常量。
    """

    # 比较状态文本，保留 PASS_STATUS 可被外层同步的行为
    bool_passed = _gate_status(dict_gate) == PASS_STATUS  # 门禁通过判定

    # 返回布尔结果供组合条件复用
    return bool_passed

# 收集本地门禁名称
def _local_gate_names(dict_gates: dict[str, dict[str, Any]]) -> list[str]:
    """
    提取不属于远端验收阶段的门禁名称。

    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 参与本地信心判断的门禁名称列表。
    """

    # 先收集当前快照里已经出现的非远端门禁名称。
    set_gate_names: set[str] = set()  # 当前快照里的本地门禁名称集合

    # 逐项过滤远端门禁，保留本地信心判定真正需要的键名。
    for str_gate_name in dict_gates:

        # 远端门禁不参与本地门禁通过性判断。
        if str_gate_name in REMOTE_GATE_NAMES:

            # 跳过远端验收门禁，继续扫描下一个键名。
            continue

        # 当前键名属于本地门禁集合，加入后续排序范围。
        set_gate_names.add(str_gate_name)  # 收集当前快照里实际出现的本地门禁键名

    # 把 high confidence 必需的本地门禁并入集合，避免缺 gate 被误判为通过。
    set_gate_names.update(REQUIRED_LOCAL_GATE_NAMES)

    # 返回稳定排序后的名称列表，便于测试和报告保持确定性。
    list_gate_names = sorted(set_gate_names)  # 仅包含本地信心判断需要遍历的门禁键名

    # 返回给本地通过性计算复用
    return list_gate_names

# 汇总缺失的必需本地门禁名称
def _missing_required_local_gates(dict_gates: dict[str, dict[str, Any]]) -> list[str]:
    """
    返回当前门禁快照里缺失的必需本地门禁名称。

    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 缺失的必需本地门禁名称列表。
    """

    # 只保留当前快照里完全缺失的必需本地门禁名称。
    return sorted(str_gate_name for str_gate_name in REQUIRED_LOCAL_GATE_NAMES if str_gate_name not in dict_gates)

# 判断本地门禁是否全部通过
def _local_gates_passed(dict_gates: dict[str, dict[str, Any]]) -> bool:
    """
    判断所有本地门禁是否通过。

    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 非远端验收门禁是否全部为通过状态。
    """

    # 先收缩出真正参与本地判断的门禁名字，避免远端门禁混入本地结论
    list_gate_names = _local_gate_names(dict_gates)  # 本地门禁名称

    # 必需本地门禁缺失时，不能把“没跑”误判成“通过”。
    if _missing_required_local_gates(dict_gates):

        # 缺 gate 直接阻断本地高信心与最终高信心。
        return False

    # 先假设全部通过，再逐项检查具体状态，便于在失败时保留直白控制流。
    bool_all_passed = True  # 本地门禁整体状态默认先记为通过

    # 每个本地门禁都必须通过，才允许进入本地高信心或最终高信心。
    for str_gate_name in list_gate_names:

        # 只要任一门禁不是 passed，就必须把整体结论降为未通过。
        if dict_gates.get(str_gate_name, {}).get("status") != PASS_STATUS:

            # 记录当前快照存在未通过的本地门禁。
            bool_all_passed = False  # 本地门禁整体状态被未通过项拉低

            # 发现失败后即可提前结束循环，避免继续做无意义检查。
            break

    # 返回组合后的本地门禁结论
    return bool_all_passed

# 判断族覆盖门禁是否允许最终高信心
def _family_gate_allows_final(dict_family_gate: dict[str, Any] | None) -> bool:
    """
    判断远端族覆盖门禁是否满足最终高信心要求。

    :param dict_family_gate: 远端族覆盖门禁结果；缺失时视为不阻塞原有流程。
    :return: 族覆盖门禁是否通过或被显式跳过。
    """

    # 缺失族覆盖门禁沿用原逻辑，不单独阻断最终高信心
    if dict_family_gate is None:

        # 没有族覆盖门禁时允许后续条件继续判断
        return True

    # 族覆盖门禁可通过，也可在显式规格模式下被跳过
    bool_gate_allowed = _gate_status(dict_family_gate) in {PASS_STATUS, "skipped"}  # 远端族覆盖在最终高信心里是否可接受

    # 返回最终高信心条件需要的布尔值
    return bool_gate_allowed

# 构造远端阻塞结果
def _blocked_remote_outcome(dict_gates: dict[str, dict[str, Any]]) -> tuple[str, str, list[str], int]:
    """
    构造远端验收阻塞时的统一返回值。

    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 状态、范围、残余风险和进程返回码。
    """

    # 收集阻塞远端验收对应的可读风险说明
    list_risks = _residual_risks(  # 远端验收被阻塞时需要回传的风险列表
        "blocked_remote_validation",  # 这次风险展开对应的远端阻塞状态标签
        remote_requested=True,  # 这里明确按“请求过远端验收”的路径组织风险
        remote_skipped=False,  # 当前分支不是主动跳过远端，而是执行后受阻
        gates=dict_gates,  # 把完整门禁快照交给风险函数拼装具体阻塞说明
    )  # 远端阻塞风险列表

    # 返回原有调用方依赖的四元组协议
    return "blocked_remote_validation", "final", list_risks, 1

# 构造需要关注的最终状态
def _attention_outcome(dict_gates: dict[str, dict[str, Any]]) -> tuple[str, str, list[str], int]:
    """
    构造远端已请求但门禁未全部通过时的结果。

    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 状态、范围、残余风险和进程返回码。
    """

    # 收集未达到高信心时的风险说明
    list_risks = _residual_risks(  # 远端已请求但证据不足时要展开的风险列表
        "needs_attention",  # 这次风险展开对应的需关注状态标签
        remote_requested=True,  # 需关注场景同样属于“远端已经被请求”的路径
        remote_skipped=False,  # 这里的证据缺口不是由显式跳过远端造成
        gates=dict_gates,  # 用完整门禁快照拼出需要关注的细节说明
    )  # 需关注风险列表

    # 返回非零码，要求调用者检查门禁细节
    return "needs_attention", "final", list_risks, 1

# 处理已请求远端验收的信心状态
def _remote_requested_outcome(
    dict_gates: dict[str, dict[str, Any]],
    *,
    bool_local_passed: bool,
) -> tuple[str, str, list[str], int]:
    """
    汇总远端验收已请求场景的最终信心。

    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :param bool_local_passed: 本地门禁是否全部通过。
    :return: 状态、范围、残余风险和进程返回码。
    """

    # 远端路由门禁保证验收服务器符合 AGENTS 契约
    dict_route_gate = dict_gates.get("route_contract")  # 路由契约门禁

    # 路由失败时必须直接阻塞，不能继续声称远端高信心
    if dict_route_gate and _gate_status(dict_route_gate) != PASS_STATUS:

        # 返回远端阻塞状态并附带路由风险
        return _blocked_remote_outcome(dict_gates)

    # 远端 pytest 门禁代表目标服务器上的仓库级回归证据
    dict_remote_pytest_gate = dict_gates.get("remote_pytest")  # 远端 pytest 门禁

    # remote_vitis_acceptance 负责证明目标服务器上的 HLS 链路已通过接受性验证
    dict_remote_gate = dict_gates.get("remote_vitis_acceptance")  # 证明目标服务器 HLS 接受性链路已通过的 gate

    # 远端板卡门禁代表同机硬件闭环证据
    dict_board_gate = dict_gates.get("remote_board_acceptance")  # 远端板卡门禁

    # 远端族覆盖门禁代表 Tier 1 目标覆盖完整性
    dict_family_gate = dict_gates.get("remote_family_coverage")  # 远端族覆盖门禁

    # 把 remote_pytest 单独转成布尔量，避免最终判定阶段继续追字典字段。
    bool_remote_pytest_passed = _gate_passed(dict_remote_pytest_gate)  # 远端 pytest 是否通过

    # 把 remote_vitis_acceptance 独立转成布尔量，便于和 pytest 缺失分开降级。
    bool_remote_passed = _gate_passed(dict_remote_gate)  # 仅表示 Vitis 接受性 gate 是否为 passed

    # 板卡门禁必须通过才允许最终 factual high confidence
    bool_board_passed = _gate_passed(dict_board_gate)  # 远端板卡是否通过

    # 族覆盖门禁允许通过或显式跳过
    bool_family_ok = _family_gate_allows_final(dict_family_gate)  # 族覆盖是否满足

    # 本地、远端 pytest、远端、板卡和族覆盖全部满足时才给最终事实高信心
    # 先把所有远端事实 gate 压成一个布尔量，避免最终结论阶段同时阅读五个条件。
    bool_remote_facts_ready = bool_remote_pytest_passed and bool_remote_passed and bool_board_passed and bool_family_ok  # 远端 pytest、Vitis、板卡与族覆盖是否全部满足

    # 最终 factual high confidence 还要求本地 gate 与远端事实 gate 同时齐备。
    bool_final_ready = bool_local_passed and bool_remote_facts_ready  # 最终 factual high confidence 是否成立

    # 所有强证据齐备时返回最终高信心
    if bool_final_ready:

        # 返回零风险、零退出码的最终状态
        return "factual_high_confidence", "final", [], 0

    # 板卡门禁明确阻塞时保持远端阻塞状态
    if dict_board_gate and _gate_status(dict_board_gate) == "blocked":

        # 返回板卡阻塞风险
        return _blocked_remote_outcome(dict_gates)

    # 族覆盖失败表示 Tier 1 远端事实覆盖不完整
    if dict_family_gate and _gate_status(dict_family_gate) == "failed":

        # 返回覆盖缺口导致的远端阻塞状态
        return _blocked_remote_outcome(dict_gates)

    # 其他失败组合需要调用方查看门禁细节
    return _attention_outcome(dict_gates)

# 处理显式跳过远端验收的信心状态
def _remote_skipped_outcome(
    dict_gates: dict[str, dict[str, Any]],
    *,
    bool_local_passed: bool,
) -> tuple[str, str, list[str], int]:
    """
    汇总调用者显式跳过远端验收时的信心状态。

    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :param bool_local_passed: 本地门禁是否全部通过。
    :return: 状态、范围、残余风险和进程返回码。
    """

    # 本地通过时只能给 local 高信心，不能升级到 final
    str_confidence_status = "local_high_confidence" if bool_local_passed else "needs_attention"  # 跳过远端后的状态

    # 跳过远端时需要显式保留最终信心缺口
    list_risks = _residual_risks(  # 显式跳过远端后的残余风险列表
        str_confidence_status,  # 跳过远端后沿用的本地置信度状态标签
        remote_requested=False,  # 显式跳过远端时不能把它记成已请求
        remote_skipped=True,  # 这个分支专门标记用户主动跳过远端
        gates=dict_gates,  # 保留原始门禁快照用于补全残余风险文本
    )  # 跳过远端后的风险列表

    # 本地门禁通过时返回零退出码，否则保留失败退出码
    if bool_local_passed:

        # 返回本地高信心状态和远端缺口提示
        return "local_high_confidence", "local", list_risks, 0

    # 返回需关注状态，提示本地或远端证据仍不足
    return "needs_attention", "local", list_risks, 1

# 处理未执行远端验收的信心状态
def _remote_missing_outcome(
    dict_gates: dict[str, dict[str, Any]],
    *,
    bool_local_passed: bool,
) -> tuple[str, str, list[str], int]:
    """
    汇总没有远端验收请求时的最终状态。

    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :param bool_local_passed: 本地门禁是否全部通过。
    :return: 状态、范围、残余风险和进程返回码。
    """

    # 未执行远端验收时仍要记录最终信心阻塞原因
    list_risks = _residual_risks(  # 根本没跑远端验收时仍需显式暴露的风险列表
        "blocked_remote_validation",  # 未跑远端时沿用的阻塞状态标签
        remote_requested=False,  # 这里明确说明本轮根本没有请求远端验收
        remote_skipped=False,  # 这不是主动跳过，而是根本未执行远端
        gates=dict_gates,  # 仍把完整门禁快照交给风险函数输出阻塞原因
    )  # 未执行远端验收风险列表

    # 本地通过但缺远端事实证据时保持 final 阻塞
    if bool_local_passed:

        # 返回远端验收缺失导致的最终阻塞状态
        return "blocked_remote_validation", "final", list_risks, 1

    # 本地也未通过时给需关注状态，但仍保留远端缺口说明
    return "needs_attention", "final", list_risks, 1

# 汇总最终信心状态
def _confidence_outcome(
    gates: dict[str, dict[str, Any]],
    *,
    remote_requested: bool,
    remote_skipped: bool,
) -> tuple[str, str, list[str], int]:
    """
    根据本地和远端门禁汇总 confidence-loop 最终结论。

    :param gates: confidence-loop 收集的全部门禁结果。
    :param remote_requested: 调用方是否请求真实远端验收。
    :param remote_skipped: 调用方是否显式跳过远端验收。
    :return: 状态、范围、残余风险和进程返回码。
    """

    # 先折叠出本地门禁总结果，再把它交给不同远端分支复用
    bool_local_passed = _local_gates_passed(gates)  # 汇总后供远端分支复用的本地门禁结论

    # 已请求远端验收时优先检查远端事实证据
    if remote_requested:

        # 返回远端验收路径下的最终结论
        return _remote_requested_outcome(gates, bool_local_passed=bool_local_passed)

    # 显式跳过远端时只能返回 local 范围的信心
    if remote_skipped:

        # 返回跳过远端路径下的结论
        return _remote_skipped_outcome(gates, bool_local_passed=bool_local_passed)

    # 没有远端验收请求时最终信心必须保持阻塞或需关注
    return _remote_missing_outcome(gates, bool_local_passed=bool_local_passed)

# 追加远端路由失败风险
def _append_route_risk(list_risks: list[str], dict_gates: dict[str, dict[str, Any]]) -> None:
    """
    在路由契约失败时追加残余风险。

    :param list_risks: 正在汇总的残余风险列表。
    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 无返回值，直接更新风险列表。
    """

    # 先单独拿出 route_contract，便于精确报告服务器映射是否偏离 AGENTS 契约
    dict_route_gate = dict_gates.get("route_contract")  # 用来核对当前远端服务器映射是否命中 AGENTS 主路由

    # 路由失败时提示 AGENTS 契约目标不匹配
    if dict_route_gate and _gate_status(dict_route_gate) == "failed":

        # 保留原有风险文本中的 AGENTS contract 关键字
        list_risks.append("Remote route target does not match the AGENTS contract primary server.")

# 追加缺失本地必需门禁风险
def _append_missing_local_gate_risk(list_risks: list[str], dict_gates: dict[str, dict[str, Any]]) -> None:
    """
    在缺失必需本地门禁时追加明确风险。

    :param list_risks: 正在汇总的残余风险列表。
    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 无返回值，直接更新风险列表。
    """

    # 先读取当前门禁快照里缺失的必需本地 gate。
    list_missing_gates = _missing_required_local_gates(dict_gates)  # 缺失的必需本地门禁列表

    # 没有缺 gate 时不追加风险。
    if not list_missing_gates:

        # 当前快照已经覆盖全部必需本地门禁。
        return

    # 把缺失门禁拼成一行，保持报告可读且确定。
    str_missing_gates = ", ".join(list_missing_gates)  # 缺失门禁名称文本

    # 明确说明哪些必需 gate 根本没有执行。
    list_risks.append(f"Required local confidence gates were not executed: {str_missing_gates}.")

# 追加远端 Vitis 验收失败风险
def _append_remote_vitis_risk(list_risks: list[str], dict_gates: dict[str, dict[str, Any]]) -> None:
    """
    在远端 Vitis 验收未通过时追加残余风险。

    :param list_risks: 正在汇总的残余风险列表。
    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 无返回值，直接更新风险列表。
    """

    # 这里读取 remote_vitis_acceptance，是为了判断服务器侧 HLS 验收是否真正落地
    dict_remote_gate = dict_gates.get("remote_vitis_acceptance")  # 表示服务器侧 Vitis 验收是否已经给出通过证据

    # 非通过状态表示远端 Vitis 证据不足
    if dict_remote_gate and _gate_status(dict_remote_gate) not in {"", PASS_STATUS}:

        # 追加远端 Vitis 未通过风险
        list_risks.append("Remote Vitis acceptance did not pass on the routed server.")

# 从板卡结果中提取平台探测阻塞信息
def _blocked_board_probe(dict_board_gate: dict[str, Any]) -> tuple[bool, str]:
    """
    提取板卡验收中的平台探测阻塞信息。

    :param dict_board_gate: 远端板卡验收门禁结果。
    :return: 是否因平台探测阻塞，以及建议的平台包名称。
    """

    # 板卡结果可能缺失或被外部工具写成非列表
    obj_board_results: object = dict_board_gate.get("results", [])  # 原始板卡结果集合

    # 非列表结果无法安全提取平台探测证据
    if not isinstance(obj_board_results, list):

        # 返回无平台探测阻塞信息
        return False, ""

    # 逐个检查板卡子结果，定位平台探测阻塞原因
    for dict_board_result in obj_board_results:

        # 非字典条目没有稳定字段可读
        if not isinstance(dict_board_result, dict):

            # 跳过无法解析的子结果
            continue

        # 只处理板卡验证阻塞的子结果
        if str(dict_board_result.get("status")) != "blocked_board_validation":

            # 其他状态不提供平台缺失证据
            continue

        # 阻塞原因列表说明是否缺少已安装平台
        obj_blocking_reasons: object = dict_board_result.get("blocking_reasons", [])  # 原始阻塞原因集合

        # 非列表原因集合不能证明平台探测阻塞
        if not isinstance(obj_blocking_reasons, list):

            # 继续检查后续子结果
            continue

        # 字符串化原因集合，兼容远端脚本返回的非字符串条目
        set_blocking_reasons = {str(obj_reason) for obj_reason in obj_blocking_reasons}  # 阻塞原因文本集合

        # 平台探测阻塞表示硬件可见但平台包缺失
        if "platform_probe" not in set_blocking_reasons:

            # 当前子结果不是平台包缺失问题
            continue

        # 平台探测载荷可能包含建议安装的平台名称
        obj_platform_probe: object = dict_board_result.get("platform_probe", {})  # 原始平台探测载荷

        # 非字典载荷不能提取建议平台名
        if not isinstance(obj_platform_probe, dict):

            # 返回平台阻塞，但没有建议名称
            return True, ""

        # 读取远端探测脚本给出的建议平台包名
        str_suggested_platform = str(obj_platform_probe.get("suggested_platform_name") or "")  # 建议平台包名

        # 返回平台探测阻塞以及可选建议名称
        return True, str_suggested_platform

    # 没有找到平台探测阻塞证据
    return False, ""

# 追加平台包缺失导致的板卡风险
def _append_platform_blocked_risk(list_risks: list[str], str_suggested_platform: str) -> None:
    """
    根据平台探测结果追加板卡阻塞风险。

    :param list_risks: 正在汇总的残余风险列表。
    :param str_suggested_platform: 远端探测给出的建议平台包名称。
    :return: 无返回值，直接更新风险列表。
    """

    # 有建议平台名时给出更具体的安装提示
    if str_suggested_platform:

        # 保留原有风险语义，并补充建议平台包
        list_risks.append(
            "Board acceptance is blocked; the routed host shows an active U55C shell but no matching "
            f"installed platform/xpfm was found. Suggested platform package: {str_suggested_platform}."
        )

        # 已记录具体平台包风险，无需再追加泛化信息
        return

    # 没有建议平台名时保留泛化的平台包缺失说明
    list_risks.append(
        "Board acceptance is blocked; the routed host shows board-level evidence but no matching "
        "installed platform/xpfm was found."
    )

# 追加板卡验收阻塞风险
def _append_board_risk(list_risks: list[str], dict_gates: dict[str, dict[str, Any]]) -> None:
    """
    在远端板卡验收阻塞时追加残余风险。

    :param list_risks: 正在汇总的残余风险列表。
    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 无返回值，直接更新风险列表。
    """

    # 板卡风险只能从 remote_board_acceptance 推断，所以先把这个门禁单独抽出来
    dict_board_gate = dict_gates.get("remote_board_acceptance")  # 表示同机板卡闭环是否已经拿到硬件层证据

    # 只有 blocked 状态需要追加板卡阻塞风险
    if not dict_board_gate or _gate_status(dict_board_gate) != "blocked":

        # 非阻塞板卡状态不追加风险
        return

    # 提取平台探测细节，区分平台包缺失和硬件证据不完整
    tuple_probe_result = _blocked_board_probe(dict_board_gate)  # 平台探测阻塞结果

    # 拆出平台阻塞判定和建议平台包名
    bool_platform_blocked, str_suggested_platform = tuple_probe_result  # 平台探测阻塞信息

    # 平台包缺失时输出更具体的风险说明
    if bool_platform_blocked:

        # 追加平台包缺失风险
        _append_platform_blocked_risk(list_risks, str_suggested_platform)

        # 平台包缺失已经覆盖当前板卡阻塞原因
        return

    # 其他板卡阻塞多半是硬件指纹或板卡 profile 证据不足
    list_risks.append("Board acceptance is blocked; hardware fingerprint or board profile evidence is incomplete.")

# 追加远端族覆盖缺口风险
def _append_family_coverage_risk(list_risks: list[str], dict_gates: dict[str, dict[str, Any]]) -> None:
    """
    在 Tier 1 远端族覆盖失败时追加残余风险。

    :param list_risks: 正在汇总的残余风险列表。
    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 无返回值，直接更新风险列表。
    """

    # 族覆盖缺口来自 remote_family_coverage，因此先读取这个门禁节点
    dict_family_gate = dict_gates.get("remote_family_coverage")  # 记录 Tier 1 规格覆盖是否仍然存在缺口

    # 只有 failed 状态表示需要列出缺口
    if not dict_family_gate or _gate_status(dict_family_gate) != "failed":

        # 非失败状态不追加族覆盖风险
        return

    # 缺失规格集合来自远端族覆盖门禁
    obj_missing_specs: object = dict_family_gate.get("missing_specs", [])  # 原始缺失规格集合

    # 仅当缺失规格是列表时保留逐项名称
    if isinstance(obj_missing_specs, list):

        # 将缺失规格拼接为一行，保持原有风险文本协议
        str_missing_specs = ", ".join(str(obj_item) for obj_item in obj_missing_specs)  # 缺失规格文本

    # 如果门禁没有给出列表，就回退为空字符串而不是伪造规格名
    else:

        # 非列表载荷不适合逐项展开，空字符串能保持下游报告协议稳定
        str_missing_specs = ""  # 列表缺失时回退为空字符串以保持风险消息格式稳定

    # 追加 Tier 1 覆盖缺口说明
    list_risks.append(f"Remote factual coverage is incomplete for Tier 1 board targets: {str_missing_specs}.")

# 汇总远端阻塞场景下的具体风险
def _append_blocked_remote_risks(list_risks: list[str], dict_gates: dict[str, dict[str, Any]]) -> None:
    """
    汇总远端验收阻塞时的具体残余风险。

    :param list_risks: 正在汇总的残余风险列表。
    :param dict_gates: confidence-loop 收集的全部门禁结果。
    :return: 无返回值，直接更新风险列表。
    """

    # 路由契约风险说明服务器选择是否符合项目治理
    _append_route_risk(list_risks, dict_gates)

    # Vitis 风险说明远端 HLS 验收是否通过
    _append_remote_vitis_risk(list_risks, dict_gates)

    # 板卡风险说明同机 U55C 验收是否具备事实证据
    _append_board_risk(list_risks, dict_gates)

    # 族覆盖风险说明 Tier 1 远端规格是否完整
    _append_family_coverage_risk(list_risks, dict_gates)

# 汇总残余风险说明
def _residual_risks(
    confidence_status: str,
    *,
    remote_requested: bool,
    remote_skipped: bool,
    gates: dict[str, dict[str, Any]],
) -> list[str]:
    """
    生成当前 confidence-loop 状态对应的残余风险说明。

    :param confidence_status: `_confidence_outcome` 计算出的信心状态。
    :param remote_requested: 调用方是否请求真实远端验收。
    :param remote_skipped: 调用方是否显式跳过远端验收。
    :param gates: confidence-loop 收集的全部门禁结果。
    :return: 面向报告输出的残余风险列表。
    """

    # 残余风险按发生顺序累积，便于报告阅读
    list_risks: list[str] = []  # 残余风险文本列表

    # 需关注状态表示至少一个门禁失败或缺少证据
    if confidence_status == "needs_attention":

        # 追加通用门禁失败提示
        list_risks.append("At least one confidence gate failed; inspect gates for details.")

    # 远端阻塞状态需要展开具体阻塞来源
    if confidence_status == "blocked_remote_validation":

        # 追加路由、Vitis、板卡和族覆盖风险
        _append_blocked_remote_risks(list_risks, gates)

    # 缺失本地必需 gate 时，不管远端路径如何都要显式暴露。
    _append_missing_local_gate_risk(list_risks, gates)

    # 已请求远端验收时不再追加“未执行远端”的缺口说明
    if remote_requested:

        # 返回已收集的风险列表
        return list_risks

    # 显式跳过远端验收时保留最终信心缺口
    if remote_skipped:

        # 追加远端 pytest 与远端验收仍是最终信心前提的说明
        list_risks.append("Final confidence requires remote pytest and remote Vitis acceptance.")

    # 没有显式跳过时，要把“远端根本没执行”这件事写进风险列表
    else:

        # 追加远端 pytest 没有执行的事实说明
        list_risks.append("Remote pytest was not executed.")

        # 单独记录远端 Vitis 验收未运行，便于把 pytest 缺失和接受性缺失拆开呈现
        list_risks.append("Remote Vitis acceptance was not executed.")

    # 返回完整残余风险列表
    return list_risks
