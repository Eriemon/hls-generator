"""prompt Markdown 章节与 JSON 代码块 helper。"""

# 使用未来注解避免前向类型在运行时过早求值。
from __future__ import annotations

# json 负责把 spec、manifest 与上下文字段稳定序列化为 JSON 代码块。
import json
from typing import Any

# 渲染 staged prompt 的输出合同说明。
def _stage_output_contract_text(manifest: dict[str, Any]) -> str:
    """
    渲染 staged prompt 的输出合同说明。

    :param manifest: 当前 stage 对应的 manifest。
    :return: staged prompt 输出合同说明文本。
    """

    # staged 输出合同先说明 fence 规则，再嵌入 manifest JSON 正文。
    list_contract_lines = [  # staged 输出合同正文行
        "Return only fenced code blocks: first the manifest JSON, "
        "then one file block per manifest file.",
        "Every file block must use `path=<relative/path>`, and every path "
        "must match the manifest exactly.",
        "",  # 规则说明与 manifest 正文之间的空行
        _json_code_block(manifest),  # 输出合同的 manifest JSON 正文
    ]

    # 返回单段 markdown 文本。
    return "\n".join(list_contract_lines)

# 渲染最终 HLS prompt 的基础合同。
def _base_prompt(
    *,
    spec: dict[str, Any],
    title: str,
    target_line: str,
    rules: list[str],
    manifest: dict[str, Any],
) -> str:
    """
    渲染最终 HLS prompt 的基础合同。

    :param spec: 规范化后的 HLS spec。
    :param title: prompt 标题行文本。
    :param target_line: 目标生成边界说明文本。
    :param rules: 设计规则列表。
    :param manifest: 最终输出合同 manifest。
    :return: 基础 prompt 合同文本。
    """

    # 最终 HLS prompt 保持固定 markdown 结构，便于测试断言与人工审阅。
    list_prompt_lines = [
        f"# {title}",  # prompt 顶部标题行
        "",  # 标题与身份说明之间的空行
        "You are an expert AMD-Xilinx HLS design generator. "
        f"{target_line}",
        "Do not generate Verilog or SystemVerilog. Do not output analysis.",  # HLS-only 输出边界
        "",  # 身份说明与规格章节之间的空行
        "## Generation spec",  # 规格章节标题
        "",  # 规格标题与 JSON 正文之间的空行
        _json_code_block(spec),  # 规格 JSON 正文
        "",  # 规格与规则章节之间的空行
        "## Design rules",  # 规则章节标题
        "",  # 规则标题与列表正文之间的空行
        _bullet_list(rules),  # 设计规则列表正文
        "",  # 规则与输出合同之间的空行
        "## Output contract",  # 输出合同章节标题
        "",  # 合同标题与正文之间的空行
        "Return only fenced code blocks: first the manifest JSON, "
        "then one file block per manifest file.",
        "The manifest must preserve the `files` array exactly and may fill "
        "the `checks` arrays with concise strings.",
        "",  # 合同规则与 manifest 正文之间的空行
        _json_code_block(manifest),  # 输出合同里要求模型原样回填的 manifest 树
        "",  # manifest 与路径规则之间的空行
        "Then return one fenced code block for every manifest file, and no "
        "extra file blocks. Put the exact relative file path in the fence "
        "info as `path=<relative/path>`.",
        "",  # 文件 fence 说明与路径规则之间的空行
        "Path rules:",  # 路径规则小节标题
        "",  # 路径规则标题与列表之间的空行
        "- Every manifest path must have exactly one matching code fence.",  # manifest 路径与 code fence 一一对应
        "- Every code fence path must appear in the manifest.",  # code fence 路径必须先出现在 manifest
        "- Paths must be relative, unique, case-exact, slash-exact, and must not contain `..`.",  # 路径格式与安全边界
        "",  # 路径规则与示例之间的空行
        "Example fence header:",  # fence 头示例标题
        "",  # 示例标题与示例正文之间的空行
        "```cpp path=src/example_kernel.cpp",  # fence 头示例正文
        "```",  # 示例 fence 结束标记
    ]  # 最终 HLS prompt 的完整正文行序列

    # 返回基础 HLS prompt 文本。
    return "\n".join(list_prompt_lines)

# 在最终 prompt 末尾追加可选 JSON 章节。
def _append_optional_sections(
    prompt: str,
    *,
    hls_profile: dict[str, Any] | None,
    decision: dict[str, Any] | None,
) -> str:
    """
    在最终 prompt 末尾追加可选 JSON 章节。

    :param prompt: 已渲染好的基础 prompt 文本。
    :param hls_profile: 当前生效的 HLS profile。
    :param decision: 人工决策约束对象。
    :return: 追加可选 JSON 章节后的最终 prompt 文本。
    """

    # 可选章节通过列表增量构建，保持没有 profile/decision 时的旧输出结构。
    list_optional_sections: list[str] = []  # prompt 末尾的可选章节集合

    # HLS profile 存在时追加 profile 章节。
    if hls_profile:

        # 追加 HLS profile JSON 章节。
        list_optional_sections.append(
            _optional_json_section("HLS profile constraints", hls_profile)
        )

    # decision 存在时追加人工决策章节。
    if decision:

        # 追加人工决策 JSON 章节。
        list_optional_sections.append(
            _optional_json_section("Human decision constraints", decision)
        )

    # 没有任何可选章节时直接返回基础 prompt。
    if not list_optional_sections:

        # 保持没有附加章节时的旧输出格式。
        return prompt

    # 返回拼接好可选章节的最终 prompt。
    return prompt.rstrip() + "\n\n" + "\n\n".join(list_optional_sections) + "\n"

# 渲染末尾追加用的 JSON 章节。
def _optional_json_section(title: str, payload: dict[str, Any]) -> str:
    """
    渲染末尾追加用的 JSON 章节。

    :param title: JSON 章节标题。
    :param payload: 章节对应的 JSON 对象。
    :return: 单个 markdown JSON 章节文本。
    """

    # 返回 markdown 标题加 JSON fenced block 组合文本。
    return f"## {title}\n\n{_json_code_block(payload)}"

# 把对象格式化为 JSON fenced block。
def _json_code_block(payload: Any) -> str:
    """
    把对象格式化为 JSON fenced block。

    :param payload: 需要序列化为 JSON 的对象。
    :return: JSON fenced block 文本。
    """

    # 返回带固定缩进与 UTF-8 中文直出的 JSON 代码块文本。
    return "```json\n" + json.dumps(
        payload,
        indent=2,
        ensure_ascii=False,
    ) + "\n```"

# 把字符串列表渲染为 Markdown 项目符号列表。
def _bullet_list(items: list[str]) -> str:
    """
    把字符串列表渲染为 Markdown 项目符号列表。

    :param items: 需要渲染的字符串列表。
    :return: Markdown 项目符号列表文本。
    """

    # 返回按顺序拼接的 markdown bullet 文本。
    return "\n".join(f"- {str_item}" for str_item in items)
