"""提供可选外部工具探测和运行结果汇总。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import shutil
import subprocess
from pathlib import Path

# 导入当前模块运行所需的依赖
from .report import ToolResult

# 外部工具超时后转成 ToolResult，避免质量门进程被子工具卡住。
DEFAULT_TOOL_TIMEOUT_SECONDS = 120  # 外部检查命令超时秒数

# 报告只保留前段输出，防止失败工具刷屏淹没质量门结论。
MAX_CAPTURED_OUTPUT_CHARS = 12000  # 单个工具输出保留上限

# 每个工具集合按顺序执行，命令里的 {target} 在运行前替换为目标路径。
EXTERNAL_TOOL_COMMANDS: dict[str, list[list[str]]] = {  # 外部工具集合到命令模板
    "none": [],  # 不执行任何外部工具
    "minimal": [  # 轻量外部检查集合
        ["ruff", "format", "--check", "{target}"],  # 格式检查命令
        ["ruff", "check", "{target}"],  # 静态规则检查命令
    ],
    "full": [  # 全量外部检查集合
        ["ruff", "format", "--check", "{target}"],  # 全量流程的格式检查命令
        ["ruff", "check", "{target}"],  # 全量流程的静态规则检查命令
        ["mypy", "{target}"],  # 全量流程的类型检查命令
        ["pytest", "-q"],  # 全量流程的测试命令
        ["coverage", "run", "-m", "pytest", "-q"],  # 覆盖率采集测试命令
        ["coverage", "report"],  # 覆盖率汇总报告命令
        ["radon", "cc", "{target}", "-s"],  # 圈复杂度检查命令
        ["radon", "mi", "{target}", "-s"],  # 可维护性指数检查命令
        [
            "xenon",  # Xenon 可执行名
            "--max-absolute",  # 绝对复杂度阈值参数
            "B",  # 绝对复杂度阈值
            "--max-modules",  # 模块复杂度阈值参数
            "B",  # 模块复杂度阈值
            "--max-average",  # 平均复杂度阈值参数
            "A",  # 平均复杂度阈值
            "{target}",  # 当前目标路径占位符
        ],  # Xenon 复杂度阈值检查命令
        ["bandit", "-r", "{target}"],  # 安全静态扫描命令
        ["interrogate", "{target}"],  # docstring 覆盖率检查命令
    ],
}

# 将外部工具命令模板渲染成 subprocess 可直接执行的参数列表。
def build_command(command_template: list[str], target: str) -> list[str]:
    """构建命令。

    参数:
        command_template: 外部检查工具的命令模板片段列表。
        target: 需要运行质量门或外部工具的目标路径。

    返回:
        返回构造完成的对象。
    """

    # 每个参数独立渲染，避免拼接 shell 字符串带来的转义问题。
    return [part.format(target=target) for part in command_template]

# 报告展示需要把参数列表转成人类可读命令。
def command_to_text(command: list[str]) -> str:
    """把命令参数列表拼成报告文本。

    参数:
        command: 准备交给子进程执行的命令参数列表。

    返回:
        用空格连接的命令展示文本。
    """

    # 该文本仅用于报告，不再交回 shell 执行。
    return " ".join(command)

# 工具输出可能很长，报告只保留足够定位问题的前缀。
def truncate_output(output: str) -> str:
    """裁剪外部工具输出。

    参数:
        output: 外部工具写入标准输出和标准错误的文本。

    返回:
        未超限时返回原文；超限时返回前缀和省略字符数。
    """

    # 未超过报告长度上限时保留完整工具输出，方便复现失败。
    if len(output) <= MAX_CAPTURED_OUTPUT_CHARS:

        # 原始输出已经可读，不额外添加截断说明。
        return output

    # 记录被省略字符数，让报告说明输出并非完整日志。
    int_omitted_count = len(output) - MAX_CAPTURED_OUTPUT_CHARS  # 被省略的输出字符数

    # 只保留开头，通常包含命令错误摘要和首批失败位置。
    str_visible_output = output[:MAX_CAPTURED_OUTPUT_CHARS]  # 报告保留的输出前缀

    # 在裁剪边界附加说明，避免调用方误认为日志完整。
    return f"{str_visible_output}\n... [truncated {int_omitted_count} characters]"

# 执行单个外部工具，并把缺失、超时和退出码统一成 ToolResult。
def run_external_command(command: list[str], cwd: str | None = None) -> ToolResult:
    """运行external命令。

    参数:
        command: 准备交给子进程执行的命令参数列表。
        cwd: 外部工具运行时使用的工作目录。

    返回:
        返回当前执行流程产生的退出状态或报告结果。
    """

    # 第一个参数用于探测工具是否安装，也作为报告项名称。
    str_tool_name = command[0]  # 外部工具可执行名

    # 报告中保留完整命令，方便用户复现外部工具失败。
    str_command_text = command_to_text(command)  # 外部工具命令展示文本

    # 工具未安装时生成缺失记录，避免 subprocess 抛出文件不存在异常。
    if shutil.which(str_tool_name) is None:

        # 127 沿用常见命令缺失退出码，便于 CI 识别环境问题。
        return ToolResult(
            name=str_tool_name,
            command=str_command_text,
            exit_code=127,
            output=f"Missing command: {str_tool_name}",
            missing=True,
        )

    # 捕获当前操作中的可预期失败并转换为规则结果
    try:

        # 捕获合并后的 stdout/stderr，统一纳入质量门报告。
        completed_process_completed_process: subprocess.CompletedProcess[str] = subprocess.run(  # 外部工具进程结果
            command,  # 当前要执行的外部工具命令
            cwd=cwd,  # 外部工具运行目录
            stdout=subprocess.PIPE,  # 捕获标准输出
            stderr=subprocess.STDOUT,  # 把标准错误并入标准输出
            text=True,  # 以文本模式读取输出
            # 外部工具失败要进入报告，不在 subprocess 层抛异常。
            check=False,  # 非零退出码留给报告层处理
            timeout=DEFAULT_TOOL_TIMEOUT_SECONDS,  # 单个工具的最长等待时间
        )

    # 超时异常需要转换成稳定的 ToolResult，避免直接中断质量门流程。
    except subprocess.TimeoutExpired as error:

        # 超时时 subprocess 可能只返回部分输出，仍保留用于定位。
        str_partial_output = error.output if isinstance(error.output, str) else ""  # 超时前捕获的输出

        # 报告中显式写出超时时间，区分工具失败和质量门失败。
        str_timeout_message = f"Command timed out after {DEFAULT_TOOL_TIMEOUT_SECONDS} seconds."  # 超时说明文本

        # 超时视为工具执行失败，同时保留已捕获的诊断文本。
        return ToolResult(
            name=str_tool_name,
            command=str_command_text,
            exit_code=124,
            output=truncate_output(f"{str_timeout_message}\n{str_partial_output}"),
            missing=False,
        )

    # 正常完成的工具结果保留退出码，由报告层决定是否阻断。
    return ToolResult(
        name=str_tool_name,
        command=str_command_text,
        exit_code=completed_process_completed_process.returncode,
        output=truncate_output(completed_process_completed_process.stdout),
        missing=False,
    )

# 按配置的工具集合顺序运行外部检查命令。
def run_external_tools(tool_set: str, target: str, cwd: str | None = None) -> list[ToolResult]:
    """运行externaltools。

    参数:
        tool_set: 需要启用的外部检查工具集合名称。
        target: 需要运行质量门或外部工具的目标路径。
        cwd: 外部工具运行时使用的工作目录。

    返回:
        返回当前执行流程产生的退出状态或报告结果。
    """

    # 解析targettext所在位置，后续文件检查使用同一绝对路径。
    str_target_text = str(Path(target))  # 文本目标文本

    # 初始化results收集容器，汇总本轮扫描发现。
    list_results: list[ToolResult] = []  # 外部工具执行结果列表

    # 按工具集合声明顺序运行，报告顺序与配置顺序保持一致。
    for command_template in EXTERNAL_TOOL_COMMANDS[tool_set]:

        # 当前模板渲染后的参数列表直接交给 subprocess。
        list_command = build_command(command_template, str_target_text)  # 本次外部工具命令参数

        # 每条外部命令独立生成 ToolResult，缺失或超时不会中断后续工具。
        list_results.append(run_external_command(list_command, cwd=cwd))

    # 调用方统一合并这些结果到质量门报告。
    return list_results
