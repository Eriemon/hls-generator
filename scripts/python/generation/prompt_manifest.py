"""prompt staged manifest 组装 helper。"""

# 使用未来注解避免前向类型在运行时过早求值。
from __future__ import annotations

# PurePosixPath 用于稳定构造 manifest 相对路径。
from pathlib import PurePosixPath

# Any 覆盖 manifest JSON-like 载荷的混合值类型。
from typing import Any

# 根 prompt 模块提供共享常量与稳定外观合同。
from scripts.python.generation.prompt import (
    PATH_LANGUAGE_BY_SUFFIX,
    STAGED_FILE_TEMPLATES,
)

# 根据 stage 生成 staged workflow 期望的 manifest。
def _stage_manifest_for(spec: dict[str, Any], stage: str) -> dict[str, Any]:
    """
    根据 stage 生成 staged workflow 期望的 manifest。

    :param spec: 规范化后的 HLS spec。
    :param stage: 已校验通过的 stage 名称。
    :return: 当前 stage 应返回的 manifest 字典。
    异常:
        ValueError: 当模板映射中找不到 stage 对应的文件合同条目时抛出。
    """

    # hls 阶段直接沿用 spec.outputs，保持和最终生成合同完全一致。
    if stage == "hls":

        # 从 spec.outputs 派生最终 HLS 输出文件条目。
        list_files = _output_file_entries(spec["outputs"])  # HLS 阶段输出文件条目列表

    # 其它 stage 使用固定模板，只替换 spec.name 形成稳定输出路径。
    else:

        # 读取当前 stage 对应的文件模板元组。
        tuple_stage_templates = STAGED_FILE_TEMPLATES.get(stage)  # 当前 stage 的文件模板集合

        # 找不到模板时直接阻断非法 stage。
        if tuple_stage_templates is None:

            # 报告未知 stage，保持 HLS-only 边界清晰。
            raise ValueError(
                "> ERR: [Python] This skill is HLS-only; unknown stage "
                + repr(stage)
                + "."
            )

        # 模板阶段把输出路径、kind 与 language 固定展开为 manifest 条目。
        list_files = [  # requirements/codegen_plan/tests 阶段展开后的 manifest 文件清单
            {
                "path": PurePosixPath(*tuple_path_parts).as_posix().format(  # 按模板展开当前 stage 的相对输出路径
                    name=spec["name"]  # 用 spec.name 替换模板中的 {name}
                ),
                "kind": str_kind,  # 当前模板条目的文件角色
                "language": str_language,  # 当前模板条目的语言标签
            }
            for tuple_path_parts, str_kind, str_language in tuple_stage_templates  # 逐个展开 stage 模板条目
        ]

    # 返回带 stage 字段的 manifest 载荷。
    return _manifest_payload(spec, stage=stage, files=list_files)

# 根据输出列表生成最终 HLS prompt 使用的 manifest。
def _manifest_for(spec: dict[str, Any]) -> dict[str, Any]:
    """
    根据输出列表生成最终 HLS prompt 使用的 manifest。

    :param spec: 规范化后的 HLS spec。
    :return: 最终 HLS prompt 应使用的 manifest 字典。
    """

    # 最终 prompt 直接复用 spec.outputs，避免 staged/hls 合同分叉。
    list_files = _output_file_entries(spec["outputs"])  # 最终 HLS 输出文件条目列表

    # 返回不带 stage 字段的最终 manifest。
    return _manifest_payload(spec, stage=None, files=list_files)

# 拼装 manifest 的公共字段。
def _manifest_payload(
    spec: dict[str, Any],
    *,
    stage: str | None,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    拼装 manifest 的公共字段。

    :param spec: 规范化后的 HLS spec。
    :param stage: 当前 stage 名称；为空表示最终 HLS prompt。
    :param files: manifest 中的文件条目列表。
    :return: 带公共字段的 manifest 字典。
    """

    # top 函数名遵守 interfaces.top_function 优先、spec.name 回退的既有合同。
    str_top_function = spec["interfaces"].get("top_function", spec["name"])  # 当前 manifest 应声明的 top 函数名

    # 先构造所有 stage 共用的 manifest 公共主体。
    dict_manifest = {  # 把模型后续必须返回的目标域、设计名、顶层函数、文件清单和检查槽位一次性打包成统一输出合同
        "target": "hls",  # 目标生成域恒定为 HLS
        "name": spec["name"],  # 当前设计名称
        "top": str_top_function,  # 约束 manifest 指向当前设计的顶层入口函数
        "files": files,  # 输出文件条目列表
        "checks": _checks_template(),  # 预留检查结果槽位
    }

    # staged manifest 需要显式标记 stage，最终 HLS prompt 则省略该字段。
    if stage is not None:

        # 写入当前 stage 名称，供 staged workflow 检查。
        dict_manifest["stage"] = stage  # staged workflow 需要的当前阶段标记

    # 返回完整 manifest 载荷。
    return dict_manifest

# 把 spec.outputs 规范化为 manifest 文件条目。
def _output_file_entries(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    把 spec.outputs 规范化为 manifest 文件条目。

    :param outputs: spec 中声明的输出文件条目列表。
    :return: manifest 使用的规范化文件条目列表。
    """

    # 每个输出条目都会被补齐 kind 与 language。
    list_file_entries = [  # manifest 使用的文件条目列表
        {
            "path": dict_output["path"],  # 直接复用 spec 声明的相对路径
            "kind": dict_output.get("kind", "source"),  # 未显式声明时回退 source 类别
            "language": dict_output.get(  # 语言字段优先复用显式声明
                "language",  # 优先复用 spec 显式给出的语言标签
                _language_from_path(dict_output["path"]),  # 无显式 language 时从路径推断
            ),
        }
        for dict_output in outputs  # 逐个遍历 spec.outputs 条目
    ]

    # 返回规范化后的 manifest 文件条目。
    return list_file_entries

# 返回 manifest checks 字段的稳定模板。
def _checks_template() -> dict[str, list[str]]:
    """
    返回 manifest checks 字段的稳定模板。

    参数:
        无额外业务参数；当前函数只返回固定模板。
    返回:
        带固定 checks 键集合的 manifest 模板字典。
    """

    # checks 字段保持固定键顺序，便于 workflow 和模型按稳定槽位回填。
    return {
        "spec_coverage": [],
        "verification_plan": [],
        "execution_plan": [],
        "implementation_assessment": [],
        "reviewability_assessment": [],
        "assumptions": [],
        "known_limitations": [],
    }

# 根据文件后缀推断 manifest language 字段。
def _language_from_path(path: str) -> str:
    """
    根据文件后缀推断 manifest language 字段。

    :param path: manifest 文件相对路径。
    :return: 对应的 language 字段值；未识别时回退 `text`。
    """

    # 不带点号的路径视为未知文本类型，保持旧行为回退 text。
    str_suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""  # 从路径推断出的文件后缀

    # 返回稳定的后缀到语言映射结果。
    return PATH_LANGUAGE_BY_SUFFIX.get(str_suffix, "text")
