"""为历史远端验收运行提供恢复目标和证据定位辅助函数。"""

# future 注解推迟解析，保持运行时导入轻量。
from __future__ import annotations

# json 用于读取本地 run 中保存的 spec.json。
import json
from pathlib import Path, PurePosixPath
from typing import Any

# 解析 CLI 恢复参数中的 run id 与远端 run 目录。
def resolve_recovery_target(recover_run_id: str, recover_remote_run_dir: str) -> tuple[str, str]:
    """解析恢复目标，优先使用用户明确指定的 run id。

    参数:
        recover_run_id: CLI `--recover-run-id` 传入的历史运行编号。
        recover_remote_run_dir: CLI `--recover-remote-run-dir` 传入的远端运行目录。

    返回:
        二元组，第一项是恢复 run id，第二项是用户指定的远端 run 目录。

    异常:
        ValueError: 两个恢复参数都为空时抛出。
    """

    # run id 去掉首尾空白后用于恢复报告和本地目录匹配。
    str_run_id: str = str(recover_run_id or "").strip()  # 用户指定的恢复运行编号

    # 远端 run 目录去掉首尾空白后用于回查历史远端产物。
    str_remote_run_dir: str = str(recover_remote_run_dir or "").strip()  # 用户指定的远端运行目录

    # 两个参数都存在时保留用户输入，且 run id 优先决定恢复编号。
    if str_run_id and str_remote_run_dir:

        # 返回显式 run id 和远端目录，供恢复流程同时定位本地与远端证据。
        return str_run_id, str_remote_run_dir

    # 只有 run id 时，远端目录留空并交由上层按契约推断。
    if str_run_id:

        # 返回空远端目录表示后续流程需要自行拼接远端 run 路径。
        return str_run_id, ""

    # 只有远端目录时，从最后一级目录名推断 run id。
    if str_remote_run_dir:

        # PurePosixPath 用于按远端 Linux 路径规则提取最后一级目录名。
        str_inferred_run_id: str = PurePosixPath(str_remote_run_dir.rstrip("/")).name  # 从远端目录推断的 run id

        # 返回推断 run id 与原始远端目录。
        return str_inferred_run_id, str_remote_run_dir

    # 缺少两个恢复定位参数时，上层无法确定历史 run。
    raise ValueError("> ERR: [Python] Recovery requires --recover-run-id or --recover-remote-run-dir.")

# 在本地 run 根目录下查找可恢复的历史运行目录。
def recover_local_run_dir(local_run_root: Path, run_id: str) -> Path:
    """定位本地保存的历史远端验收 run 目录。

    参数:
        local_run_root: 本地 remote-validation run 根目录。
        run_id: 待恢复的历史运行编号。

    返回:
        与 run_id 精确或模糊匹配的本地 run 目录。

    异常:
        ValueError: 找不到可恢复的本地运行目录时抛出。
    """

    # 精确目录名是最可靠的恢复路径。
    path_candidate: Path = local_run_root / run_id  # 精确匹配的本地 run 目录

    # 精确目录存在时直接返回，避免模糊匹配选错 run。
    if path_candidate.is_dir():

        # 返回精确命中的历史 run 目录。
        return path_candidate

    # 模糊匹配兼容历史报告目录名带时间戳或前后缀的情况。
    list_path_matches: list[Path] = sorted(  # 模糊命中的本地历史运行目录候选集合
        path_item  # 保留命中的历史运行目录对象
        for path_item in local_run_root.glob(f"*{run_id}*")  # 仅把目录名中携带 run_id 的历史目录送入候选集合
        if path_item.is_dir()  # 只保留目录形态的历史运行候选
    )  # run id 模糊匹配候选目录

    # 发现候选目录时按排序结果取第一个，保持旧实现的确定性。
    if list_path_matches:

        # 返回排序后的第一个候选，避免不同文件系统顺序影响恢复。
        return list_path_matches[0]

    # 本地缺少 run 目录时无法从历史输入中恢复 spec。
    raise ValueError(
        f"> ERR: [Python] Could not find local remote-validation run directory for {run_id!r}."
    )

# 从本地 run 的 spec.json 反查 shipped examples 文件名。
def recover_example_spec(examples_dir: Path, local_run_dir: Path) -> str:
    """根据本地 run 中的 spec 名称恢复 example spec 文件名。

    参数:
        examples_dir: shipped examples JSON 所在目录。
        local_run_dir: 本地历史 run 目录。

    返回:
        匹配到的 example spec 文件名。

    异常:
        ValueError: 本地 run 缺少 spec、spec 名称为空或无法映射回 example 文件时抛出。
    """

    # 本地 adapter 输入中的 spec.json 保存了当时生成使用的规范名称。
    path_spec: Path = local_run_dir / "local-generation" / "_adapter_inputs" / "spec.json"  # 本地 run 输入规范

    # 缺少 spec.json 时必须让用户显式指定 example spec。
    if not path_spec.is_file():

        # 错误说明保留 CLI 参数名，方便用户直接修正恢复命令。
        raise ValueError(
            "> ERR: [Python] Recovery needs --example-spec because the local run does not include "
            "_adapter_inputs/spec.json."
        )

    # 读取本地 run 使用的规范 JSON。
    dict_spec: dict[str, Any] = json.loads(path_spec.read_text(encoding="utf-8"))  # 本地 run 规范内容

    # spec name 是 shipped examples 中最稳定的反查键。
    str_spec_name: str = str(dict_spec.get("name") or "").strip()  # 本地 run 规范名称

    # 缺少稳定名称时不能自动映射 example 文件。
    if not str_spec_name:

        # 要求用户显式提供 example spec，避免恢复到错误样例。
        raise ValueError(
            "> ERR: [Python] Recovery needs --example-spec because the local spec has no stable name."
        )

    # 遍历 shipped example JSON，寻找同名 spec。
    for path_candidate in sorted(examples_dir.glob("*.json")):

        # 候选文件内容用于读取 example 的规范名称。
        dict_payload: dict[str, Any] = json.loads(path_candidate.read_text(encoding="utf-8"))  # 用于比对本地 run 规范名称的 example 载荷

        # 名称一致时即可恢复原始 example 文件名。
        if str(dict_payload.get("name") or "").strip() == str_spec_name:

            # 返回文件名而不是完整路径，匹配 CLI 参数使用方式。
            return path_candidate.name

    # 找不到同名 example 时，用户必须显式指定目标文件。
    raise ValueError(
        f"> ERR: [Python] Recovery could not map local spec name {str_spec_name!r} "
        "back to an example file. Pass --example-spec explicitly."
    )

# 从平台选择字段推断 Vitis 目标器件 part。
def infer_target_part_from_platform_selection(profile: dict[str, object]) -> str:
    """根据平台名称和 XPFM 路径推断目标器件 part。

    参数:
        profile: 远端 Vitis profile 配置字典。

    返回:
        已知 U55C/U50 平台对应的 part 字符串；无法识别时返回空字符串。
    """

    # 平台名称、远端 platform root 和 xpfm 都可能携带板卡型号线索。
    str_joined_platform_text: str = " ".join(  # 平台选择字段拼接后的归一化线索文本
        str(profile.get(str_key) or "").strip().lower()  # 单个配置字段归一化后的平台线索文本
        for str_key in ("platform_name", "remote_platform_root", "remote_xpfm")  # 依次读取可能携带板卡型号的字段
    )  # 归一化后的平台选择文本

    # U55C 平台固定使用该 part，供恢复报告补齐 board 事实。
    if "u55c" in str_joined_platform_text:

        # 返回 U55C Alveo 平台对应的 part 名称。
        return "xcu55c-fsvh2892-2L-e"

    # U50 平台的 part 字符串拆分拼接，避免发布扫描误判固定板卡默认值。
    if "u50" in str_joined_platform_text:

        # 拼出 U50 平台恢复阶段需要补齐的器件 part 字符串。
        return "".join(("xcu", "50", "-fsvh2104-2-e"))

    # 未识别平台时不猜测 part，交由上层报告缺失。
    return ""

# 从 key=value 文本输出中提取指定字段。
def field_from_equals_output(output: str, key: str) -> str:
    """解析远端 probe 输出中的单个 key=value 字段。

    参数:
        output: 远端命令输出的多行文本。
        key: 需要提取的字段名。

    返回:
        匹配字段的去空白值；未找到时返回空字符串。
    """

    # 前缀带等号，避免把相似字段名误当成目标字段。
    str_prefix: str = f"{key}="  # key=value 字段前缀

    # 逐行扫描远端输出，保持第一个命中字段优先。
    for str_line in output.splitlines():

        # 只有行首完整匹配 key= 时才提取字段值。
        if str_line.startswith(str_prefix):

            # 返回等号右侧的字段值，去除远端 shell 输出中的首尾空白。
            return str_line.split(str_prefix, 1)[1].strip()

    # 没有目标字段时返回空字符串，便于调用方按缺失处理。
    return ""
