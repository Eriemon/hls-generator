"""为 HLS workflow 提供手工、命令行和 mock 模型响应适配器。"""

# 启用延迟注解，避免 Protocol 与 dataclass 字段在导入期解析复杂类型。
from __future__ import annotations

# JSON 和环境模块负责响应载荷与子进程环境。
import json
import os

# 命令解析与执行模块负责 command provider 后端。
import shlex
import subprocess

# dataclass、路径和类型辅助用于 provider 上下文与协议定义。
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

# mock 渲染器保证 HLS C/C++ 片段满足当前注释语言覆盖要求。
from .mock_comment_rendering import _ensure_hls_line_comment_coverage

# mock artifact helper 继续由本模块兼容导出，供旧测试和调试脚本复用。
from .mock_hls_artifacts import (
    _hls_pragmas,
    _mock_hls_cfg_text,
    _mock_hls_header_text,
    _mock_hls_source_text,
    _mock_hls_testbench_text,
)

# mock_vectors 生成 workflow 测试使用的确定性参考向量。
from .mock_vectors import _mock_vectors

# 模型适配器错误基类，供 workflow 统一捕获并重试。
class ModelProviderError(ValueError):
    """表示模型适配器无法产出可用响应。

    参数:
        无外部业务参数；异常消息由抛出位置提供。
    返回:
        无业务返回值；该类作为 workflow 捕获的异常类型。
    """

# 手工模式缺少预置响应文件时抛出的专用错误。
class ManualResponseRequired(ModelProviderError):
    """表示手工模型提供器需要用户先写入响应文件。

    参数:
        无外部业务参数；异常消息由抛出位置提供。
    返回:
        无业务返回值；该类用于区分可由用户补文件恢复的流程。
    """

# 单次模型生成尝试的上下文载荷。
@dataclass(frozen=True)
class GenerationContext:
    """保存模型提供器生成响应时需要的 workflow 上下文。

    参数:
        attempt_id: 当前尝试编号，dtype=str，unit=dimensionless。
        stage: workflow 阶段名称，dtype=str，unit=dimensionless。
        prompt_path: prompt 文件路径，dtype=Path，unit=filesystem path。
        response_path: 响应文件路径，dtype=Path，unit=filesystem path。
        run_dir: 当前 run 根目录，dtype=Path，unit=filesystem path。
        attempt_dir: 当前尝试目录，dtype=Path，unit=filesystem path。
        spec: HLS 规范字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        manifest: 当前阶段期望产物清单，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        workflow_config: workflow 配置字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        vector_contract: 可选参考向量契约，shape=(n fields) or None，dtype=dict[str, Any] or None，unit=JSON object。
        comment_language: mock HLS 注释语言，dtype=str，unit=dimensionless。
    返回:
        dataclass 实例本身作为只读上下文，无额外业务返回值。
    """

    # 当前尝试编号会写入命令行环境和响应 metadata。
    attempt_id: str  # 当前 workflow 尝试编号

    # 阶段名用于 mock 分流和命令行 provider 环境变量。
    stage: str  # workflow 阶段名称

    # prompt 文件路径供外部命令读取完整提示词。
    prompt_path: Path  # prompt 文件路径

    # 响应文件路径供手工模式或命令行模式回写结果。
    response_path: Path  # 模型响应文件路径

    # run 根目录作为外部命令的工作目录。
    run_dir: Path  # 当前 run 根目录

    # attempt 目录用于定位本轮生成产物。
    attempt_dir: Path  # 当前尝试目录

    # HLS spec 提供 mock 文件名、顶层函数和测试向量生成依据。
    spec: dict[str, Any]  # HLS 规范字典

    # manifest 描述当前阶段期望模型返回的文件块。
    manifest: dict[str, Any]  # 阶段产物清单

    # workflow 配置携带 provider、预算和 mock 行为等运行参数。
    workflow_config: dict[str, Any]  # workflow 配置字段

    # 向量契约携带 sha256，用于 mock HLS testbench 标记参考数据。
    vector_contract: dict[str, Any] | None = None  # 参考向量契约

    # mock 产物默认使用中文注释，保持当前项目 HLS 代码输出风格。
    comment_language: str = "zh"  # mock HLS 注释语言

# 模型提供器协议，屏蔽手工、命令行和 mock 后端差异。
class ModelProvider(Protocol):
    """定义 workflow 调用模型适配器时依赖的最小协议。

    参数:
        无构造参数约束；具体 provider 自行定义初始化参数。
    返回:
        Protocol 类型本身不产生业务返回值。
    """

    # provider 名称写入 workflow trace 和配置摘要。
    name: str  # 模型提供器名称

    # provider 统一生成入口。
    def generate(self, prompt: str, context: GenerationContext) -> str:
        """
        根据 prompt 和上下文返回原始 fenced-block 响应。

        参数:
            prompt: 已渲染模型提示词，dtype=str，unit=text。
            context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        返回:
            原始模型响应文本，dtype=str，unit=text。
        """

# 根据配置名称创建具体模型提供器。
def build_model_provider(
    provider_name: str,
    *,
    command: str | Sequence[str] | None = None,
    timeout_s: int = 120,
    config: dict[str, Any] | None = None,
) -> ModelProvider:
    """
    构造 workflow 使用的模型提供器实例。

    参数:
        provider_name: provider 名称，dtype=str，unit=dimensionless。
        command: command provider 的命令模板，dtype=str or Sequence[str] or None，unit=shell command。
        timeout_s: command provider 超时时间，dtype=int，unit=s。
        config: provider 配置字典，shape=(n fields) or None，dtype=dict[str, Any] or None，unit=JSON object。
    返回:
        符合 ModelProvider 协议的实例，dtype=ModelProvider，unit=object。
    异常:
        ModelProviderError: provider 名称未知或 command provider 缺少命令时抛出。
    """

    # 统一 provider 名称大小写，兼容 CLI 与配置文件输入。
    str_provider_name: str = provider_name.lower()  # 规范化 provider 名称

    # mock provider 用于本地 smoke 和确定性测试。
    if str_provider_name == "mock":

        # 返回可生成确定性 HLS/Python/test 产物的 mock provider。
        return MockModelProvider(config=config)

    # manual provider 读取用户预先写好的响应文件。
    if str_provider_name == "manual":

        # 返回手工响应 provider。
        return ManualModelProvider()

    # command provider 通过外部命令连接真实模型或包装脚本。
    if str_provider_name == "command":

        # command 模式没有命令时无法启动外部模型。
        if not command:

            # 报告缺失命令，提示调用方补齐 provider_command。
            raise ModelProviderError("> ERR: [Python] Command provider requires a model command.")

        # 返回带超时控制的命令行 provider。
        return CommandModelProvider(command, timeout_s=timeout_s)

    # 未知 provider 必须显式阻断，避免 workflow 静默切换后端。
    raise ModelProviderError(f"> ERR: [Python] Unknown model provider {provider_name!r}.")

# 手工 provider 只读取 response_path 中的预置响应。
class ManualModelProvider:
    """从用户准备好的响应文件中读取模型输出。

    参数:
        无构造参数；响应路径来自 GenerationContext。
    返回:
        provider 实例本身，无额外业务返回值。
    """

    # workflow trace 使用该名称区分等待人工补文件的 provider。
    name = "manual"  # trace 中的手工响应 provider 标识

    # 手工模式生成入口。
    def generate(self, prompt: str, context: GenerationContext) -> str:
        """
        读取 context.response_path 中的原始响应文本。

        参数:
            prompt: 已渲染 prompt；手工模式不消费该文本，dtype=str，unit=text。
            context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        返回:
            响应文件文本，dtype=str，unit=text。
        异常:
            ManualResponseRequired: 响应文件不存在时抛出。
        """

        # 手工模式的 prompt 已经落盘，当前函数只关心响应文件。
        del prompt

        # 没有响应文件时让 workflow 进入可恢复的人工补文件状态。
        if not context.response_path.exists():

            # 报告需要准备的响应文件路径。
            raise ManualResponseRequired(
                f"> ERR: [Python] Manual provider expects a prepared response file at {context.response_path}."
            )

        # 读取用户准备的 fenced-block 响应文本。
        return context.response_path.read_text(encoding="utf-8")

# 命令行 provider 通过外部程序生成模型响应。
class CommandModelProvider:
    """执行外部命令并收集模型响应。

    参数:
        command: 外部命令或命令参数序列，dtype=str or Sequence[str]，unit=shell command。
        timeout_s: 命令超时时间，dtype=int，unit=s。
    返回:
        provider 实例本身，无额外业务返回值。
    """

    # workflow trace 使用该名称区分外部命令生成后端。
    name = "command"  # trace 中的命令行 provider 标识

    # 命令行 provider 初始化入口。
    def __init__(self, command: str | Sequence[str], *, timeout_s: int = 120) -> None:
        """
        规范化外部命令并保存超时配置。

        参数:
            command: 外部命令模板，dtype=str or Sequence[str]，unit=shell command。
            timeout_s: 命令超时时间，dtype=int，unit=s。
        返回:
            无业务返回值；初始化后实例可供 workflow 调用。
        异常:
            ModelProviderError: 命令为空时由 _normalize_command 抛出。
        """

        # 命令模板在 generate 中结合 GenerationContext 展开。
        self._command = _normalize_command(command)  # 外部命令参数模板

        # 超时时间限制外部模型或包装脚本的最长执行时间。
        self._timeout_s = timeout_s  # 命令超时时间，单位 s

    # 命令行 provider 生成入口。
    def generate(self, prompt: str, context: GenerationContext) -> str:
        """
        执行外部命令并返回 stdout 或响应文件文本。

        参数:
            prompt: 写入命令 stdin 的模型提示词，dtype=str，unit=text。
            context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        返回:
            原始模型响应文本，dtype=str，unit=text。
        异常:
            ModelProviderError: 命令超时、启动失败、返回非零或未产出响应时抛出。
        """

        # 外部命令通过环境变量读取 prompt、response 和 workflow 上下文路径。
        dict_env: dict[str, str] = os.environ.copy()  # 子进程环境变量副本

        # HLS_GEN_CONTEXT_JSON 给包装脚本提供轻量结构化上下文。
        str_context_json: str = _command_context_json(context)  # 命令行 provider 上下文 JSON

        # 将 workflow 关键路径和阶段信息注入子进程环境。
        dict_env.update(
            {
                "HLS_GEN_PROMPT_PATH": str(context.prompt_path),  # 给外部命令定位 prompt 输入文件
                "HLS_GEN_RESPONSE_PATH": str(context.response_path),  # 给外部命令定位响应输出文件
                "HLS_GEN_STAGE": context.stage,  # 告知子进程当前所处阶段
                "HLS_GEN_ATTEMPT_ID": context.attempt_id,  # 传递当前尝试的唯一编号
                "HLS_GEN_CONTEXT_JSON": str_context_json,  # 传递完整的上下文快照
            }
        )

        # 展开命令模板中的 {prompt_path}、{stage} 等占位符。
        list_command: list[str] = [_expand_part(str_part, context) for str_part in self._command]  # 已展开命令参数

        # 执行外部模型命令，并把 prompt 传入 stdin。
        completed_process_run_result = _run_provider_command(  # 外部命令执行结果
            list_command,  # 展开后的命令参数序列
            prompt,  # 传给外部命令的 prompt 文本
            context,  # 当前生成上下文
            dict_env,  # 子进程环境变量映射
            self._timeout_s,  # 外部命令超时秒数
        )  # 供后续检查返回码与 stdout/stderr

        # 命令失败时优先用 stderr 首行作为错误摘要。
        if completed_process_run_result.returncode != 0:

            # 提取外部命令失败原因的首行摘要。
            str_failure_detail: str = _command_failure_detail(completed_process_run_result)  # 命令失败摘要

            # 抛出 workflow 可捕获的 provider 错误。
            raise ModelProviderError(f"> ERR: [Python] Command provider failed: {str_failure_detail}")

        # stdout 非空时按模型响应直接返回。
        if completed_process_run_result.stdout.strip():

            # 返回外部命令直接写到 stdout 的 fenced-block 响应。
            return completed_process_run_result.stdout

        # 外部命令也可以把响应写入约定的 response_path。
        if context.response_path.exists():

            # 返回命令写入的响应文件文本。
            return context.response_path.read_text(encoding="utf-8")

        # 没有 stdout 且没有响应文件，说明外部命令未满足 provider 协议。
        raise ModelProviderError(
            "> ERR: [Python] Command provider produced no stdout and did not write the expected response file."
        )

# mock provider 生成确定性 fenced-block 响应。
class MockModelProvider:
    """为本地 workflow 测试生成确定性模型响应。

    参数:
        config: mock 行为配置，shape=(n fields) or None，dtype=dict[str, Any] or None，unit=JSON object。
    返回:
        provider 实例本身，无额外业务返回值。
    """

    # workflow trace 使用该名称区分本地确定性 mock 后端。
    name = "mock"  # trace 中的本地 mock provider 标识

    # mock provider 初始化入口。
    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        """
        保存 mock 行为配置。

        参数:
            config: 可选 mock 配置，shape=(n fields) or None，dtype=dict[str, Any] or None，unit=JSON object。
        返回:
            无业务返回值；初始化后实例可生成确定性响应。
        """

        # 空配置按默认 success 行为处理。
        self._config = config or {}  # mock 行为配置

    # mock provider 生成入口。
    def generate(self, prompt: str, context: GenerationContext) -> str:
        """
        根据 manifest 和当前阶段生成 mock fenced-block 响应。

        参数:
            prompt: 已渲染 prompt；mock 模式不消费该文本，dtype=str，unit=text。
            context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        返回:
            mock 模型响应文本，dtype=str，unit=text。
        """

        # mock 响应只依赖结构化上下文，不读取自然语言 prompt。
        del prompt

        # mock_behavior 可控制成功、无效响应或故意丢失 testbench 等场景。
        str_mode: str = _mock_mode(context, self._config)  # 当前 mock 行为模式

        # invalid_response 用于测试提取器对非 fenced 响应的错误处理。
        if str_mode == "invalid_response":

            # 返回无法被 extractor 解析的普通文本。
            return "This is not a fenced response.\n"

        # manifest 是 workflow 对当前阶段文件块的期望。
        dict_manifest: dict[str, Any] = context.manifest  # 当前阶段产物清单

        # 只保留带 path 的文件项，防止脏 manifest 进入 fenced path。
        list_files: list[dict[str, Any]] = _manifest_files(dict_manifest)  # 可生成的 manifest 文件项

        # spec_issue 模式故意丢弃 testbench 文件，用于验证合同门禁。
        list_response_files: list[dict[str, Any]] = _mode_adjusted_files(str_mode, list_files)  # mock 响应文件项

        # response manifest 保留原 manifest 的其余字段，并补齐 mock 检查说明。
        dict_response_manifest: dict[str, Any] = _mock_response_manifest(  # mock 响应使用的 manifest 载荷
            dict_manifest,  # 原始阶段 manifest
            list_response_files,  # 已按模式调整后的输出文件
            context.stage,  # 当前 workflow 阶段名
        )  # 组装成供 extractor 读取的 manifest 主体

        # fenced 响应第一块固定为 JSON manifest。
        list_blocks: list[str] = _manifest_block(dict_response_manifest)  # fenced 响应块列表

        # 为每个 manifest 文件生成对应文本。
        dict_file_map: dict[str, str] = _mock_file_contents(context, list_response_files)  # path 到 mock 文件文本的映射

        # 逐个追加 path fenced block。
        for dict_file_entry in list_response_files:

            # manifest path 作为 fenced block 的 path 属性。
            str_rel_path: str = str(dict_file_entry["path"])  # manifest 文件相对路径

            # language 字段只影响 fenced block 标记，不参与文件内容生成。
            str_language: str = str(dict_file_entry.get("language") or "text")  # fenced block 语言标签

            # 追加单个文件的 fenced block。
            list_blocks.extend(
                [
                    f"```{str_language} path={str_rel_path}",
                    dict_file_map[str_rel_path].rstrip(),
                    "```",
                ]
            )

        # 拼接完整 mock 响应并以换行结尾，匹配真实模型常见输出。
        return "\n".join(list_blocks) + "\n"

# 构造 command provider 传给外部命令的 JSON 上下文。
def _command_context_json(context: GenerationContext) -> str:
    """
    将 GenerationContext 的轻量字段序列化为 JSON。

    参数:
        context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
    返回:
        子进程环境变量使用的 JSON 文本，dtype=str，unit=JSON text。
    """

    # 只暴露外部命令需要的路径、阶段和 manifest 摘要。
    dict_context_payload: dict[str, Any] = {
        "attempt_id": context.attempt_id,  # 让外部命令识别当前尝试批次
        "stage": context.stage,  # 明确告诉外部命令所处流程阶段
        "prompt_path": str(context.prompt_path),  # 交给外部命令读取 prompt 的绝对路径
        "response_path": str(context.response_path),  # 交给外部命令写响应的目标路径
        "run_dir": str(context.run_dir),  # 暴露本次 run 的根目录
        "attempt_dir": str(context.attempt_dir),  # 暴露当前尝试的专属产物目录
        "target": "hls",  # 固定声明目标类型为 HLS
        "name": context.spec.get("name"),  # 透传规范里的设计名称
        "manifest": context.manifest,  # 直接携带当前阶段的完整 manifest 结构
    }  # 命令行 provider 上下文载荷

    # 返回 UTF-8 友好的 JSON 文本，便于中文字段透传。
    return json.dumps(dict_context_payload, ensure_ascii=False)

# 执行 command provider 的外部命令。
def _run_provider_command(
    command: list[str],
    prompt: str,
    context: GenerationContext,
    env: dict[str, str],
    timeout_s: int,
) -> subprocess.CompletedProcess[str]:
    """
    调用外部命令并转换启动类异常。

    参数:
        command: 已展开命令参数，shape=(n args)，dtype=list[str]，unit=shell command。
        prompt: 写入 stdin 的提示词，dtype=str，unit=text。
        context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        env: 子进程环境变量，shape=(n vars)，dtype=dict[str, str]，unit=environment。
        timeout_s: 命令超时时间，dtype=int，unit=s。
    返回:
        subprocess 执行结果，dtype=CompletedProcess[str]，unit=process result。
    异常:
        ModelProviderError: 超时或启动失败时抛出。
    """

    # subprocess.run 是 command provider 唯一的外部副作用。
    try:

        # 使用 run_dir 作为工作目录，保持命令与 workflow 产物相对路径一致。
        path_work_dir: Path = context.run_dir  # 子进程工作目录

        # 捕获 stdout/stderr 后由 provider 统一解释响应或错误。
        bool_capture_output: bool = True  # 是否捕获 stdout 和 stderr

        # 文本模式避免调用方手动处理 bytes 编码。
        bool_text_mode: bool = True  # 是否使用文本模式通信

        # 非零退出码需要带 stderr 摘要转换为 ModelProviderError。
        bool_check_exit_code: bool = False  # 是否由 subprocess 自动抛出非零退出码

        # 调用外部命令并返回原始 CompletedProcess。
        return subprocess.run(
            command,
            cwd=path_work_dir,
            input=prompt,

            # 输出捕获策略由 provider 统一处理 stdout/stderr。
            capture_output=bool_capture_output,
            text=bool_text_mode,

            # 超时和退出码策略保持 command provider 可控恢复。
            timeout=timeout_s,
            check=bool_check_exit_code,
            env=env,
        )

    # 超时通常表示外部模型或包装脚本没有按协议返回。
    except subprocess.TimeoutExpired as exc:

        # 把超时统一转换为 provider 错误，便于 workflow 上层重试或失败归档。
        raise ModelProviderError(f"> ERR: [Python] Command provider timed out after {timeout_s}s.") from exc

    # 启动失败说明命令路径、权限或环境配置不可用。
    except OSError as exc:

        # 把启动类系统错误统一转换为 provider 错误，避免上层直接暴露底层异常类型。
        raise ModelProviderError(f"> ERR: [Python] Command provider failed to start: {exc}") from exc

# 提取 command provider 失败摘要。
def _command_failure_detail(completed_process: subprocess.CompletedProcess[str]) -> str:
    """
    从外部命令 stderr/stdout 中提取一行错误摘要。

    参数:
        completed_process: 外部命令执行结果，dtype=CompletedProcess[str]，unit=process result。
    返回:
        一行错误摘要，dtype=str，unit=text。
    """

    # stderr 优先于 stdout，避免把普通输出误当作错误详情。
    str_output: str = (completed_process.stderr or completed_process.stdout).strip()  # 命令失败输出

    # 有输出时取第一行，避免异常文本过长污染 workflow 报告。
    if str_output:

        # 返回第一行错误摘要。
        return str_output.splitlines()[0]

    # 没有 stderr/stdout 时至少保留退出码。
    return f"exit code {completed_process.returncode}"

# 规范化 command provider 的命令模板。
def _normalize_command(command: str | Sequence[str]) -> list[str]:
    """
    将命令字符串或参数序列转换为参数列表。

    参数:
        command: 外部命令模板，dtype=str or Sequence[str]，unit=shell command。
    返回:
        命令参数列表，shape=(n args)，dtype=list[str]，unit=shell command。
    异常:
        ModelProviderError: 命令为空时抛出。
    """

    # 字符串命令按 Windows 兼容模式切分，序列命令逐项转为字符串。
    list_parts: list[str] = (
        shlex.split(command, posix=False)  # 字符串命令按 Windows 规则拆分
        if isinstance(command, str)  # 输入本身是单条命令字符串
        else [str(obj_item) for obj_item in command]  # 序列命令逐项转成字符串
    )  # 规范化命令参数

    # 空命令无法启动外部 provider。
    if not list_parts:

        # 报告空命令错误。
        raise ModelProviderError("> ERR: [Python] Model command must not be empty.")

    # 返回可传给 subprocess.run 的参数列表。
    return list_parts

# 展开命令参数中的 GenerationContext 占位符。
def _expand_part(part: str, context: GenerationContext) -> str:
    """
    用当前生成上下文替换命令参数模板中的字段。

    参数:
        part: 单个命令参数模板，dtype=str，unit=text。
        context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
    返回:
        展开后的命令参数；格式化失败时返回原文本，dtype=str，unit=text。
    """

    # 命令模板只提供稳定的 workflow 上下文字段。
    dict_values: dict[str, str] = {
        "attempt_id": context.attempt_id,  # 供 format_map 注入尝试编号
        "stage": context.stage,  # 供 format_map 注入阶段名称
        "prompt_path": str(context.prompt_path),  # 供 format_map 展开 prompt 路径
        "response_path": str(context.response_path),  # 供 format_map 展开响应路径
        "run_dir": str(context.run_dir),  # 供 format_map 展开 run 根目录
        "attempt_dir": str(context.attempt_dir),  # 供 format_map 展开尝试目录
        "target": "hls",  # 展开 {target} 占位符
        "name": str(context.spec.get("name") or ""),  # 供 format_map 展开设计名称
    }  # 命令参数模板字段

    # format_map 允许命令显式引用上下文字段。
    try:

        # 返回展开后的命令参数。
        return part.format_map(dict_values)

    # 模板异常时保留原参数，兼容包含普通花括号的命令。
    except Exception:

        # 格式化失败时回退原始参数文本，保持命令模板中的普通花括号可透传。
        return part

# 解析 mock provider 的行为模式。
def _mock_mode(context: GenerationContext, config: dict[str, Any]) -> str:
    """
    从 provider 配置或 workflow spec 中读取 mock 行为模式。

    参数:
        context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        config: provider 配置字典，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    返回:
        mock 行为模式名称，dtype=str，unit=dimensionless。
    """

    # provider 配置优先级高于 spec.workflow。
    mock_behavior_config: Any = config.get("mock_behavior")  # provider 级 mock 行为配置

    # 没有 provider 配置时读取 spec.workflow.mock_behavior。
    if mock_behavior_config is None:

        # workflow 内的 mock_behavior 支持按 stage 配置。
        mock_behavior_config = (context.spec.get("workflow") or {}).get("mock_behavior")  # 从规范侧回退读取 mock 行为配置

    # 字符串配置直接作为所有阶段模式。
    if isinstance(mock_behavior_config, str):

        # 返回显式 mock 模式。
        return mock_behavior_config

    # 字典配置可按 stage、* 或 default 分流。
    if isinstance(mock_behavior_config, dict):

        # 当前阶段优先，其次通配符，最后 default。
        stage_mock_behavior_config: Any = mock_behavior_config.get(  # 当前阶段最终采用的 mock 行为
            context.stage,  # 当前阶段专属 mock 配置
            mock_behavior_config.get("*", mock_behavior_config.get("default", "success")),  # 阶段未命中时使用通配或默认配置
        )  # 当前阶段 mock 行为

        # 字典型阶段配置允许把 mode 与其他扩展字段放在一起。
        if isinstance(stage_mock_behavior_config, dict):

            # 返回当前阶段的 mode 字段。
            return str(stage_mock_behavior_config.get("mode", "success"))

        # 非空标量配置转为字符串模式。
        if stage_mock_behavior_config:

            # 返回当前阶段标量模式。
            return str(stage_mock_behavior_config)

    # 缺省模式生成完整有效响应。
    return "success"

# 从 manifest 中提取可生成的文件项。
def _manifest_files(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """
    过滤 manifest.files 中缺少 path 的条目。

    参数:
        manifest: 阶段产物清单，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    返回:
        可生成文件项列表，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
    """

    # manifest.files 可能包含脏数据，mock 只处理字典且带 path 的项。
    list_files: list[dict[str, Any]] = [
        dict_entry  # 保留原始 manifest 文件条目
        for dict_entry in manifest.get("files", [])  # 遍历 manifest.files 候选
        if isinstance(dict_entry, dict) and dict_entry.get("path")  # 仅接受带 path 的字典条目
    ]  # manifest 有效文件项

    # 返回过滤后的文件项。
    return list_files

# 根据 mock 模式调整响应文件列表。
def _mode_adjusted_files(mode: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    为 spec_issue 等测试模式调整 mock 文件清单。

    参数:
        mode: mock 行为模式，dtype=str，unit=dimensionless。
        files: 原始 manifest 文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        调整后的文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
    """

    # spec_issue 模式需要至少两个文件，才能安全丢弃一个 testbench。
    if mode == "spec_issue" and len(files) > 1:

        # 选择 testbench 或最后一个文件作为故意遗漏目标。
        str_dropped_path: str = _dropped_spec_issue_path(files)  # spec_issue 模式丢弃路径

        # 返回去掉目标文件后的列表，触发后续契约校验。
        return [dict_entry for dict_entry in files if str(dict_entry["path"]) != str_dropped_path]

    # 其他模式保持 manifest 文件列表不变。
    return files

# 选择 spec_issue 模式要丢弃的文件路径。
def _dropped_spec_issue_path(files: list[dict[str, Any]]) -> str:
    """
    选择最适合制造 spec/testbench 缺失问题的文件路径。

    参数:
        files: manifest 文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        要从响应中移除的相对路径，dtype=str，unit=filesystem path。
    """

    # 优先丢弃 testbench，让契约验证更容易定位测试侧缺失。
    for dict_entry in files:

        # kind 或文件名包含 _tb. 都视为 testbench 候选。
        bool_testbench_entry: bool = (
            dict_entry.get("kind") == "testbench"  # kind 明确声明为 testbench
            or "_tb." in str(dict_entry["path"]).lower()  # 文件名后缀命中 testbench 约定
        )  # testbench 文件候选标志

        # 找到 testbench 后立即返回。
        if bool_testbench_entry:

            # 返回 testbench 路径。
            return str(dict_entry["path"])

    # 没有 testbench 时退回最后一个文件，保持旧版 next(..., last) 行为。
    return str(files[-1]["path"])

# 构造 mock 响应中的 manifest 块载荷。
def _mock_response_manifest(
    manifest: dict[str, Any],
    files: list[dict[str, Any]],
    stage: str,
) -> dict[str, Any]:
    """
    合成 mock 响应第一块 JSON manifest。

    参数:
        manifest: 原始阶段产物清单，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
        files: 响应实际携带的文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
        stage: workflow 阶段名称，dtype=str，unit=dimensionless。
    返回:
        mock 响应 manifest，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    """

    # checks 字段模仿真实模型的审查摘要，供 extractor/workflow 测试消费。
    dict_checks: dict[str, Any] = {
        "spec_coverage": [f"Mock provider generated HLS stage {stage} artifacts."],  # 阶段产物覆盖说明
        "verification_plan": ["Mock response includes deterministic vectors and PASS/FAIL hooks."],  # 验证路径说明
        "execution_plan": ["Mock response is intended for local workflow tests."],  # 执行用途说明
        "implementation_assessment": ["Mock HLS artifacts satisfy structural workflow contracts."],  # 结构契约评估
        "reviewability_assessment": ["Mock artifacts include minimal comments and result markers."],  # 可审阅性评估
        "assumptions": [],  # 当前阶段的默认假设
        "known_limitations": ["Mock provider prioritizes workflow determinism over hardware fidelity."],  # mock 方案已知限制
    }  # mock 响应检查摘要

    # 返回与真实响应 manifest 兼容的结构。
    return {
        **manifest,
        "files": files,
        "checks": dict_checks,
    }

# 生成 manifest 的 fenced JSON 块。
def _manifest_block(manifest: dict[str, Any]) -> list[str]:
    """
    将响应 manifest 包装为 fenced JSON 块。

    参数:
        manifest: mock 响应 manifest，shape=(n fields)，dtype=dict[str, Any]，unit=JSON object。
    返回:
        fenced block 行列表，shape=(3 lines)，dtype=list[str]，unit=text lines。
    """

    # JSON 块保持缩进，便于 extractor 和人工调试读取。
    str_manifest_json: str = json.dumps(manifest, indent=2, ensure_ascii=False)  # fenced JSON 块中的 manifest 文本

    # 返回 fenced block 三行结构。
    return ["```json", str_manifest_json, "```"]

# 为当前阶段生成 mock 文件内容映射。
def _mock_file_contents(context: GenerationContext, files: list[dict[str, Any]]) -> dict[str, str]:
    """
    按 workflow 阶段生成 path 到 mock 文件内容的映射。

    参数:
        context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        files: manifest 文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        文件内容映射，shape=(n files)，dtype=dict[str, str]，unit=text by path。
    """

    # 当前阶段决定文件内容生成策略。
    str_stage: str = context.stage  # 提取阶段名，后面据此选择不同的 mock 产物生成路径

    # HLS spec 用于生成参考向量和 HLS mock 代码。
    dict_spec: dict[str, Any] = context.spec  # 提取规范正文，供各阶段文本生成逻辑共享

    # 各阶段共享同一组确定性 mock 向量。
    list_vectors: list[dict[str, Any]] = _mock_vectors(dict_spec)  # mock 参考向量

    # tests 阶段只需要输出结构化向量 JSON。
    if str_stage == "tests":

        # 返回测试向量文件内容。
        return _mock_tests_file_contents(files, list_vectors)

    # hls 阶段输出头文件、源码、testbench 和 cfg。
    if str_stage == "hls":

        # hls 阶段要同时补齐头文件、源码、testbench 与 cfg 文本。
        return _mock_hls_file_contents(context, files, list_vectors)

    # 未知阶段返回空 JSON 占位文件，保持旧版宽松行为。
    return _default_file_contents(files)

# 生成 tests 阶段的 mock 文件内容。
def _mock_tests_file_contents(
    files: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
) -> dict[str, str]:
    """
    为 tests 阶段生成向量 JSON 文件内容。

    参数:
        files: manifest 文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
        vectors: mock 参考向量，shape=(n cases)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        文件内容映射，shape=(n files)，dtype=dict[str, str]，unit=text by path。
    """

    # tests 阶段载荷保留 case_ids 和完整 cases。
    dict_payload: dict[str, Any] = {
        "version": 1,  # payload 协议版本
        "case_ids": [str(dict_item["id"]) for dict_item in vectors],  # 向量用例编号列表
        "cases": vectors,  # 完整向量内容
    }  # tests 阶段向量载荷

    # 预先序列化一次，所有 tests 文件共享相同向量内容。
    str_payload_text: str = json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n"  # tests 阶段 JSON 文本

    # 将相同 JSON 写入 manifest 声明的每个文件。
    dict_contents: dict[str, str] = {
        str(dict_file["path"]): str_payload_text  # 每个目标路径复用同一份 tests JSON
        for dict_file in files  # 遍历 tests 阶段输出文件
    }  # tests 阶段 path 到内容的映射

    # 返回 tests 阶段每个目标文件对应的统一 JSON 文本映射。
    return dict_contents

# 依据目标后缀组装 hls 阶段的头文件、源码、testbench 与 cfg 文本。
def _mock_hls_file_contents(
    context: GenerationContext,
    files: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
) -> dict[str, str]:
    """
    为 hls 阶段生成头文件、源码、testbench 和 cfg 内容。

    参数:
        context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        files: manifest 文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
        vectors: mock 参考向量，shape=(n cases)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        文件内容映射，shape=(n files)，dtype=dict[str, str]，unit=text by path。
    """

    # 头文件名用于 HLS 源文件 include。
    str_header_name: str = _hls_header_name(files)  # HLS mock 头文件名

    # vector hash 写入 testbench，用于跨阶段契约验证。
    str_vector_hash: str = str((context.vector_contract or {}).get("sha256") or "")  # 读取向量契约里的 sha256 摘要

    # HLS 阶段逐文件生成对应文本。
    dict_contents: dict[str, str] = {}  # 为 hls 产物累积 path 到文本的结果字典

    # 遍历 manifest 条目，为每个 hls 目标文件生成具体文本。
    for dict_file in files:

        # 先取出相对路径，再交给后缀判断逻辑决定该生成哪类 hls 文本。
        str_rel_path: str = str(dict_file["path"])  # 当前 manifest 条目的相对路径

        # 根据文件后缀和 stem 选择 HLS mock 文本生成器。
        str_file_text: str = _mock_hls_file_text(  # 当前 HLS 目标文件的 mock 文本
            context,  # 提供当前规范、语言与目录等生成背景
            files,  # manifest 中声明的全部 hls 文件
            vectors,  # 当前阶段共用的向量数据
            str_rel_path,  # 当前正在生成的相对路径
            str_header_name,  # 头文件 include 名称
            str_vector_hash,  # testbench 使用的向量摘要
        )  # 单个 HLS mock 文件文本

        # 保存当前文件内容。
        dict_contents[str_rel_path] = str_file_text  # HLS 阶段单文件 mock 文本

    # 返回 hls 阶段完整的 path 到文本映射。
    return dict_contents

# 选择 HLS mock 源文件 include 使用的头文件名。
def _hls_header_name(files: list[dict[str, Any]]) -> str:
    """
    从 manifest 文件中选择 HLS 头文件名。

    参数:
        files: manifest 文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        头文件名，dtype=str，unit=filesystem filename。
    """

    # 优先使用 manifest 中第一个 .h/.hpp 文件。
    for dict_file in files:

        # 当前文件路径用于判断头文件后缀。
        str_rel_path: str = str(dict_file["path"])  # 取出路径后专门检查是否属于头文件后缀

        # HLS 头文件后缀包括 .h 和 .hpp。
        if str_rel_path.endswith((".h", ".hpp")):

            # 返回文件名部分供源码 include。
            return Path(str_rel_path).name

    # 缺少头文件时沿用旧版默认名。
    return "kernel.h"

# 生成单个 HLS mock 文件文本。
def _mock_hls_file_text(
    context: GenerationContext,
    files: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    rel_path: str,
    header_name: str,
    vector_hash: str,
) -> str:
    """
    根据 HLS 文件后缀和 stem 选择 mock 文本。

    参数:
        context: 当前生成尝试上下文，dtype=GenerationContext，unit=workflow state。
        files: manifest 文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
        vectors: mock 参考向量，shape=(n cases)，dtype=list[dict[str, Any]]，unit=JSON array。
        rel_path: manifest 文件相对路径，dtype=str，unit=filesystem path。
        header_name: HLS 头文件名，dtype=str，unit=filesystem filename。
        vector_hash: 参考向量 sha256，dtype=str，unit=hash text。
    返回:
        单个文件的 mock 文本，dtype=str，unit=text。
    """

    # 文件后缀决定 HLS 产物类型。
    str_suffix: str = Path(rel_path).suffix.lower()  # HLS 文件后缀

    # 文件 stem 用于区分 C++ kernel 源码和 testbench。
    str_stem: str = Path(rel_path).stem  # 用于识别 testbench 后缀的文件 stem

    # 头文件走 header mock 模板。
    if str_suffix in {".h", ".hpp"}:

        # 生成并补齐 HLS 行注释覆盖。
        return _covered_hls_text(
            _mock_hls_header_text(context.spec, context.comment_language),
            context.comment_language,
        )

    # 非 testbench C/C++ 源码走 kernel source 模板。
    if str_suffix in {".cpp", ".cc", ".cxx"} and "_tb" not in str_stem:

        # 生成并补齐 kernel 源码行注释覆盖。
        return _covered_hls_text(
            _mock_hls_source_text(context.spec, header_name, context.comment_language),
            context.comment_language,
        )

    # testbench C/C++ 源码写入向量和 hash 标记。
    if str_suffix in {".cpp", ".cc", ".cxx"}:

        # testbench 文本要额外写入向量摘要，方便后续做结果对账。
        return _covered_hls_text(
            _mock_hls_testbench_text(context.spec, vectors, vector_hash, context.comment_language),
            context.comment_language,
        )

    # cfg 文件使用 HLS cfg 模板。
    if str_suffix == ".cfg":

        # 返回 hls_config.cfg mock 文本。
        return _mock_hls_cfg_text(context.spec, files)

    # 未识别文件保持旧版空文本占位。
    return "\n"

# 补齐 HLS mock 文本的行注释覆盖。
def _covered_hls_text(text: str, comment_language: str) -> str:
    """
    对 HLS mock C/C++ 文本应用行注释覆盖规则。

    参数:
        text: 原始 HLS mock 文本，dtype=str，unit=text。
        comment_language: 注释语言，dtype=str，unit=dimensionless。
    返回:
        已补齐注释覆盖的 HLS 文本，dtype=str，unit=text。
    """

    # 委托 mock_comment_rendering 保持 C/C++ 注释策略一致。
    return _ensure_hls_line_comment_coverage(text, comment_language)

# 为未知阶段生成默认文件内容。
def _default_file_contents(files: list[dict[str, Any]]) -> dict[str, str]:
    """
    为非 tests/python/hls 阶段生成默认空 JSON 内容。

    参数:
        files: manifest 文件项，shape=(n files)，dtype=list[dict[str, Any]]，unit=JSON array。
    返回:
        文件内容映射，shape=(n files)，dtype=dict[str, str]，unit=text by path。
    """

    # 未知阶段保留旧行为，每个文件写入空 JSON。
    dict_contents: dict[str, str] = {
        str(dict_file["path"]): "{}\n"  # 每个未知阶段文件写入空 JSON 占位
        for dict_file in files  # 遍历未知阶段的全部 manifest 条目
    }  # 默认 path 到内容的映射

    # 返回默认文件内容。
    return dict_contents
