"""读取和校验 HLS 示例的板卡验收元数据。"""

# 启用延迟注解，避免运行期解析泛型类型。
from __future__ import annotations

# 导入 JSON、路径和通用载荷类型。
import json
from pathlib import Path
from typing import Any

# U55C m_axi host profile 表示示例必须进入真实板卡验收。
BOARD_RUNNABLE_PROFILE = "u55c_m_axi_host"  # 可板卡运行 profile

# not_board_runnable profile 表示示例明确豁免板卡验收。
NON_BOARD_RUNNABLE_PROFILE = "not_board_runnable"  # 非板卡运行 profile

# Host 模板名称映射到 assets/validation-board/hosts 下的模板文件。
HOST_TEMPLATE_FILENAMES = {  # host 模板文件映射
    "vector_scale_host": "vector_scale_host.cpp.tpl",  # 向量缩放示例对应的 host 模板文件
    "vector_increment_host": "vector_increment_host.cpp.tpl",  # 向量自增示例对应的 host 模板文件
    "binary_add_host": "binary_add_host.cpp.tpl",  # 二元加法示例对应的 host 模板文件
    "matrix_unary_host": "matrix_unary_host.cpp.tpl",  # 单输入矩阵示例对应的 host 模板文件
    "unary_memory_host": "unary_memory_host.cpp.tpl",  # 单输入 memory 示例对应的 host 模板文件
    "binary_memory_host": "binary_memory_host.cpp.tpl",  # 双输入 memory 示例对应的 host 模板文件
    "matrix_memory_host": "matrix_memory_host.cpp.tpl",  # 矩阵 memory 示例对应的 host 模板文件
    "wrapper_unary_memory_host": "wrapper_unary_memory_host.cpp.tpl",  # wrapper unary memory 示例对应的 host 模板文件
}

# _dict_child 统一处理可选嵌套字典读取。
def _dict_child(dict_parent: dict[str, Any], str_key: str) -> dict[str, Any]:
    """从字典中读取子字典字段。

    Args:
        dict_parent: 需要读取的父级配置字典。
        str_key: 目标子字段名称。

    Returns:
        字段值是字典时返回该字典，否则返回空字典。
    """

    # 原始字段可能缺失或不是字典，不能直接传给后续读取逻辑。
    dict_child_candidate = dict_parent.get(str_key)  # 待确认的子配置字段

    # 只有真实字典才作为配置段继续处理。
    if isinstance(dict_child_candidate, dict):

        # 返回原始子字典，让调用方决定是否复制。
        return dict_child_candidate

    # 非字典字段视为缺失配置。
    return {}

# board_acceptance_config 是读取 workflow.board_acceptance 的公共入口。
def board_acceptance_config(dict_spec: dict[str, Any]) -> dict[str, Any]:
    """提取示例规格中的 board_acceptance 配置。

    Args:
        dict_spec: 单个 HLS example spec 的 JSON 字典。

    Returns:
        workflow.board_acceptance 的浅拷贝；缺失或类型错误时返回空字典。
    """

    # workflow 只有在字典形态下才可能包含 board_acceptance。
    dict_workflow = _dict_child(dict_spec, "workflow")  # 承载 board_acceptance 的 workflow 配置段

    # board_acceptance 记录板卡验收 profile、host 模板和豁免原因。
    dict_config = _dict_child(dict_workflow, "board_acceptance")  # 板卡验收配置段

    # 返回浅拷贝，避免调用方修改原始 spec。
    return dict(dict_config)

# board_acceptance_profile 返回标准化后的 profile 字符串。
def board_acceptance_profile(dict_spec: dict[str, Any]) -> str:
    """读取板卡验收 profile。

    Args:
        dict_spec: 单个 HLS example spec 的 JSON 字典。

    Returns:
        去除首尾空白后的 profile 字符串；缺失时返回空字符串。
    """

    # profile 是后续分区和校验的主判定字段。
    return str(board_acceptance_config(dict_spec).get("profile") or "").strip()

# board_acceptance_target_family 返回板卡目标族。
def board_acceptance_target_family(dict_spec: dict[str, Any]) -> str:
    """读取板卡验收目标族字段。

    Args:
        dict_spec: 单个 HLS example spec 的 JSON 字典。

    Returns:
        target_family 字符串；缺失时返回空字符串。
    """

    # 这里只读取 target_family 一项，因此单独抽出配置并沿用空字符串缺省语义。
    dict_config = board_acceptance_config(dict_spec)  # 专门为了读取 target_family 抽出的板卡验收配置段

    # target_family 用于远端验收选择平台族。
    return str(dict_config.get("target_family") or "").strip()

# is_board_runnable 判断示例是否必须进入 U55C host 验收。
def is_board_runnable(dict_spec: dict[str, Any]) -> bool:
    """判断示例是否声明为可板卡运行。

    Args:
        dict_spec: 单个 HLS example spec 的 JSON 字典。

    Returns:
        profile 等于 u55c_m_axi_host 时返回 True。
    """

    # 可板卡运行 profile 需要后续触发真实 board acceptance。
    return board_acceptance_profile(dict_spec) == BOARD_RUNNABLE_PROFILE

# validate_board_acceptance_config 检查单个 spec 的声明完整性。
def validate_board_acceptance_config(dict_spec: dict[str, Any]) -> list[str]:
    """校验板卡验收配置是否声明完整。

    Args:
        dict_spec: 单个 HLS example spec 的 JSON 字典。

    Returns:
        配置问题消息列表；列表为空表示声明有效。
    """

    # 这里先提取 board_acceptance 段，后续才能分别校验 profile、豁免原因和 host 模板。
    dict_config = board_acceptance_config(dict_spec)  # 当前 spec 进入完整校验前提取出的板卡验收配置段

    # profile 决定示例进入板卡运行还是显式豁免。
    str_profile = str(dict_config.get("profile") or "").strip()  # 板卡验收 profile

    # reason 只在豁免板卡运行时必填。
    str_reason = str(dict_config.get("reason") or "").strip()  # 板卡豁免原因

    # 错误列表按发现顺序返回，便于报告稳定。
    list_errors: list[str] = []  # 配置错误列表

    # 缺少 profile 时无法判断示例验收路径。
    if not str_profile:

        # profile 是 board_acceptance 的最小必填字段。
        list_errors.append("workflow.board_acceptance.profile is required")

        # profile 缺失后其余 profile 依赖检查没有意义。
        return list_errors

    # profile 只能是可运行或显式豁免两种状态。
    if str_profile not in {BOARD_RUNNABLE_PROFILE, NON_BOARD_RUNNABLE_PROFILE}:

        # 拼接合法 profile，保持原有英文诊断对测试和报告友好。
        str_allowed_profiles = (
            f"{BOARD_RUNNABLE_PROFILE!r} or {NON_BOARD_RUNNABLE_PROFILE!r}"  # 当前仓库允许声明的合法 profile 文本
        )  # 合法 profile 文本

        # 记录非法 profile 的配置错误。
        list_errors.append(
            f"workflow.board_acceptance.profile must be {str_allowed_profiles}",
        )

    # 豁免板卡运行必须说明原因，避免静默跳过真实验收。
    if str_profile == NON_BOARD_RUNNABLE_PROFILE and not str_reason:

        # reason 让后续审查知道为何无法运行 board acceptance。
        list_errors.append(
            "workflow.board_acceptance.reason is required when profile is not_board_runnable",
        )

    # 可板卡运行示例必须声明 host 模板。
    if str_profile == BOARD_RUNNABLE_PROFILE and not _host_template_name(dict_config):

        # host_template 缺失时无法生成 board host 程序。
        list_errors.append(
            "workflow.board_acceptance.host_template is required for board-runnable examples",
        )

    # 返回累积的配置错误。
    return list_errors

# partition_example_specs_by_board_acceptance 按验收路径归类 examples。
def partition_example_specs_by_board_acceptance(path_examples_dir: Path) -> dict[str, Any]:
    """按 board acceptance 声明对 example spec 分区。

    Args:
        path_examples_dir: 存放 HLS example JSON spec 的目录。

    Returns:
        包含 board_specs、exempt_specs 和 invalid_specs 三个列表的字典。
    """

    # board_specs 保存必须远端板卡验收的示例。
    list_board_specs: list[dict[str, Any]] = []  # 可板卡运行示例

    # exempt_specs 保存声明了合理豁免原因的示例。
    list_exempt_specs: list[dict[str, Any]] = []  # 板卡豁免示例

    # invalid_specs 保存 board_acceptance 配置不完整的示例。
    list_invalid_specs: list[dict[str, Any]] = []  # 无效配置示例

    # 逐个读取 example JSON，排序保证报告稳定。
    for path_spec in sorted(path_examples_dir.glob("*.json")):

        # 每个 JSON 文件代表一个 example spec。
        dict_spec: dict[str, Any] = json.loads(  # 当前 example spec 解析后的结构化字典
            path_spec.read_text(encoding="utf-8"),  # 当前 example spec 的原始 JSON 文本
        )  # 示例规格字典

        # 非 HLS 示例不参与 HLS board acceptance 分区。
        if str(dict_spec.get("target") or "").strip().lower() != "hls":

            # 跳过非 HLS 目标，避免把其他技能示例混入板卡验收分区。
            continue

        # 先取出板卡验收段，后面要用它填充分区摘要里的 reason 和 host_template。
        dict_config = board_acceptance_config(dict_spec)  # 当前 example 提取出的板卡验收配置段

        # 再读取归一化后的 profile，把当前 spec 路由到 board、exempt 或 invalid 分区。
        str_profile = board_acceptance_profile(dict_spec)  # 当前 example 归一化后的板卡验收 profile

        # 同时收集配置错误，决定当前 spec 是否必须进入 invalid_specs。
        list_errors = validate_board_acceptance_config(dict_spec)  # 当前 example 的板卡验收配置错误列表

        # entry 保持原有字段，供 confidence loop 和报告复用。
        dict_entry = {
            "spec": path_spec.name,  # 当前 example spec 的文件名
            "profile": str_profile,  # 当前 example 的板卡验收 profile
            "reason": str(dict_config.get("reason") or "").strip(),  # 当前 example 的板卡豁免原因
            "host_template": _host_template_name(dict_config),  # 当前 example 选择的 host 模板名
        }  # 分区条目

        # 无效 spec 需要保留具体 issues 供用户修复。
        if list_errors:

            # issues 字段只在 invalid_specs 条目中出现。
            list_invalid_specs.append({**dict_entry, "issues": list_errors})

            # 当前 spec 配置不完整时不再参与 board 或 exempt 分区。
            continue

        # 可板卡运行示例进入远端 board acceptance 候选。
        if str_profile == BOARD_RUNNABLE_PROFILE:

            # board_specs 后续会被远端验收流程逐个派发。
            list_board_specs.append(dict_entry)

        # 其余合法 profile 是显式豁免示例。
        else:

            # exempt_specs 仍保留 reason，便于最终信心报告解释。
            list_exempt_specs.append(dict_entry)

    # 返回三类分区列表，保持历史字段名不变。
    return {
        "board_specs": list_board_specs,  # 需要真实板卡验收的示例分区
        "exempt_specs": list_exempt_specs,  # 明确声明豁免原因的示例分区
        "invalid_specs": list_invalid_specs,  # 板卡验收配置不完整的示例分区
    }

# resolve_host_template_path 解析 host 模板名称到真实文件。
def resolve_host_template_path(path_skill_root: Path, str_template_name: str) -> Path:
    """解析 board host 模板文件路径。

    Args:
        path_skill_root: erie-hls-generator 技能根目录。
        str_template_name: workflow.board_acceptance.host_template 中的模板名称。

    Returns:
        已存在的 host 模板文件路径。

    Raises:
        ValueError: 模板名称未知或模板文件缺失时抛出。
    """

    # 映射表只允许项目内声明过的 host 模板。
    str_template_filename = HOST_TEMPLATE_FILENAMES.get(str_template_name)  # host 模板文件名

    # 未知模板名称无法安全渲染 host 程序。
    if not str_template_filename:

        # 使用 current-project 错误前缀暴露配置问题。
        raise ValueError(f"> ERR: [Python] Unsupported board host template {str_template_name!r}.")

    # host 模板统一放在 validation-board/hosts 下。
    path_template = (
        path_skill_root  # 技能根目录，作为 host 模板相对路径解析起点
        / "assets"  # 板卡验收静态资源目录
        / "validation-board"  # 板卡验收资源子目录
        / "hosts"  # host 模板集中存放目录
        / str_template_filename  # 当前 host 模板对应的文件名
    )  # host 模板路径

    # 模板映射存在但文件缺失时必须阻断。
    if not path_template.exists():

        # 缺失模板会导致远端 board acceptance 无法生成 host。
        raise ValueError(f"> ERR: [Python] Board host template does not exist: {path_template}")

    # 返回已验证存在的模板路径。
    return path_template

# _host_template_name 统一读取 host_template 字段。
def _host_template_name(dict_config: dict[str, Any]) -> str:
    """读取并规范化 host_template 名称。

    Args:
        dict_config: workflow.board_acceptance 配置段。

    Returns:
        去除首尾空白后的 host_template 名称；缺失时返回空字符串。
    """

    # host_template 是 board-runnable 示例生成 host 的模板键。
    return str(dict_config.get("host_template") or "").strip()
