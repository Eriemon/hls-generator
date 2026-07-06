"""
HLS generator 命令行入口，负责解析子命令并路由到对应运行时能力。

stdout 协议:
- stdout_protocol: json
- ``readability-gate --json`` 与 ``selfcheck --json`` 输出单个 JSON 文本，供上游脚本直接解析。
- ``config``、``user-config``、``deps check --json``、``deps install`` 与
  ``run-workflow`` 在机器读取场景下也会输出单个 JSON 文本；未请求协议输出时
  只打印带 ``> INFO: [Python]`` 前缀的简短摘要。
"""

# 延迟注解避免在导入期解析 argparse 的复杂容器类型。
from __future__ import annotations

# argparse 负责 CLI 参数解析；json、sys 和 Path 负责协议输出与文本产物落盘。
import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 版本号用于 --version 与 selfcheck 输出。
from scripts.python.config.version import __version__

# 运行时配置与依赖配置入口负责 config/deps/selfcheck 命令。
from scripts.python.config.hls_config import (
    config_path,
    runtime_config,
    skill_dependencies_config,
    validate_runtime_config,
)

# HLS profile 优化提示词由专用生成器负责。
from scripts.python.validation.hls_profile import build_hls_optimizer_prompt

# Prompt 渲染依赖注释语言、预算和阶段约束。
from scripts.python.generation.prompt import (
    COMMENT_LANGUAGE_CHOICES,
    PROMPT_BUDGETS,
    PROMPT_STAGES,
    render_prompt,
)

# 注释重写计划支持只生成计划、不直接改写源码。
from scripts.python.hls_quality_gate.readability.rewrite_plan import (
    build_hls_comment_rewrite_plan,
    write_hls_comment_rewrite_plan,
)

# HLS readability gate 负责 HGxxx 规则执行。
from scripts.python.hls_quality_gate.readability.runner import run_hls_readability_gate

# requirement 工具统一确认、补默认值和 codegen plan 构造。
from scripts.python.generation.requirements import (
    apply_requirement_defaults,
    build_codegen_plan,
    validate_requirement_confirmation,
)

# 依赖治理支持检查、请求与安装流程。
from scripts.python.config.skill_dependencies import (
    BLOCKED_DEPENDENCY,
    SkillDependencyError,
    build_dependency_request,
    check_skill_dependencies,
    format_dependency_report,
    install_skill_dependencies,
)

# Spec 读写与脚手架入口负责最小 HLS 合同载荷。
from scripts.python.generation.spec import SpecError, read_spec, scaffold_spec, write_spec

# 用户配置提供注释语言的用户级默认值。
from scripts.python.config.user_config import (
    load_user_config,
    resolve_comment_language,
    set_comment_language,
    user_config_path,
)

# 验证器与 workflow 负责主流程执行和交付检查。
from scripts.python.validation.hls_artifacts import READINESS_LEVELS, ValidationRunOptions, validate_generated
from scripts.python.workflow.hls_workflow import WorkflowRunRequest, run_workflow

# 工作区工具负责路径边界与输出目录治理。
from scripts.python.config.workspace import require_configured_output_path, require_workspace_path

# 全部子命令都受 HLS-only 目标约束。
ONLY_HLS_TARGET = ("hls",)  # 唯一允许的 target 取值

# argparse 的私有子命令动作在当前解释器上不是可下标泛型，这里保留原始动作类型别名。
SubparserAction = argparse._SubParsersAction  # 子命令注册动作类型

# 单个 CLI 参数规格由 flags 与 kwargs 两部分构成，便于批量注册。
ArgumentSpec = tuple[tuple[str, ...], dict[str, Any]]  # add_argument 的参数描述

# 旧 Python reference 公开命令已经移除，命中时必须显式要求调用方重跑 HLS-only 流程。
LEGACY_REFERENCE_COMMAND_ERROR = (
    "> ERR: [Python] validate-python-reference was removed when this skill became HLS-only; "
    "rerun from a fresh HLS-only workflow run."
)

# 旧 reference_contract CLI 参数已经移除，命中时必须显式要求调用方重跑 HLS-only 流程。
LEGACY_REFERENCE_FLAG_ERROR = (
    "> ERR: [Python] legacy reference_contract (--reference-contract) is no longer supported; "
    "rerun from a fresh HLS-only workflow run."
)

# 解析 CLI 参数并把执行权路由给对应子命令处理器。
def main(argv: list[str] | None = None) -> int:
    """
    运行 HLS generator 的命令行入口。

    参数:
        argv: 可选参数列表；为 ``None`` 时改用进程命令行参数。
    返回:
        子命令退出码；成功为 ``0``，失败为非零值。
    """

    # 先把实参数列显式收敛成列表，便于在 argparse 之前拦截旧命令与旧参数。
    list_argv = list(sys.argv[1:] if argv is None else argv)  # 当前 CLI 实际要解析的参数序列

    # legacy Python reference 入口必须在 argparse 之前显式阻断，避免回退到模糊的 invalid choice 报错。
    str_legacy_error = _legacy_cli_error(list_argv)  # 旧 CLI 入口命中时返回的显式重跑错误

    # 命中旧入口时直接按 argparse 风格退出，确保 stderr 能稳定给出 rerun 指引。
    if str_legacy_error is not None:

        # 用临时解析器复用 argparse 标准错误输出样式。
        argparse.ArgumentParser(prog="hls-gen").exit(2, f"error: {str_legacy_error}\n")

    # 先构造完整命令树，确保所有子命令共享同一份 HLS-only 边界。
    argument_parser_root = _build_parser()  # 承载所有 HLS-only 子命令的根命令解析器

    # 解析用户输入参数并得到最终命名空间。
    namespace_args: argparse.Namespace = argument_parser_root.parse_args(  # 当前调用的参数命名空间
        list_argv,  # 调用方传入的原始命令行参数序列
    )

    # 统一进入命令执行与异常映射路径。
    try:

        # 所有子命令都通过 set_defaults(func=...) 绑定到具体处理器。
        return int(namespace_args.func(namespace_args) or 0)

    # 这些异常属于用户输入或运行前置条件问题，统一映射为 exit code 2。
    except (SpecError, ValueError, SkillDependencyError) as exc:

        # 保持 argparse 标准错误输出样式，方便脚本调用方识别。
        argument_parser_root.exit(2, f"error: {exc}\n")

# 构建根命令解析器，并注册所有支持的 HLS-only 子命令。
def _build_parser() -> argparse.ArgumentParser:
    """
    构造完整的 HLS generator CLI 解析器。

    参数:
        无外部业务参数；命令树由本模块静态定义。
    返回:
        已注册全部子命令的根级 ``ArgumentParser``。
    """

    # 创建根级 argparse 解析器，后续会在其上挂载全局 --version 与所有一级子命令。
    argument_parser_root = argparse.ArgumentParser(  # 先承接根命令帮助和版本参数，再生成一级子命令注册树
        prog="hls-gen",  # argparse 在帮助与报错里展示的 CLI 程序名
        description="AMD-Xilinx/Vitis HLS-only generator CLI.",  # 根命令 --help 顶部展示的 HLS-only 工具说明
    )

    # 版本输出直接复用包内版本号，避免双处维护。
    argument_parser_root.add_argument(
        "--version",
        action="version",
        version=f"hls-gen {__version__}",
    )

    # 所有子命令都挂在统一 subparsers 下，保证入口一致。
    subparser_action_root_commands: SubparserAction = (
        argument_parser_root.add_subparsers(  # 承载一级子命令的统一注册动作
            dest="command",  # 一级子命令字段名
            required=True,  # 强制要求显式选择一级子命令
        )
    )

    # 先注册面向核心生成流程的主命令。
    for func_register_command in (
        _register_scaffold_parser,
        _register_prompt_parser,
        _register_validate_parser,
        _register_readability_parser,
    ):

        # 依次挂入生成主链所需的核心命令。
        func_register_command(subparser_action_root_commands)

    # 再注册围绕注释治理与 workflow 的扩展命令。
    for func_register_command in (
        _register_comment_plan_parser,
        _register_run_workflow_parser,
        _register_optimize_hls_prompt_parser,
    ):

        # 依次挂入注释治理与 staged workflow 扩展命令。
        func_register_command(subparser_action_root_commands)

    # 最后注册面向配置、自检和依赖治理的管理命令。
    for func_register_command in (
        _register_config_parser,
        _register_selfcheck_parser,
        _register_user_config_parser,
        _register_deps_parser,
    ):

        # 依次挂入配置、自检与依赖治理命令。
        func_register_command(subparser_action_root_commands)

    # 返回完整命令树，供 main() 统一解析用户输入。
    return argument_parser_root

# scaffold 子命令只负责生成最小 HLS spec 骨架。
def _register_scaffold_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``scaffold`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 scaffold 命令挂到根命令树。
    """

    # 先准备承载 spec 骨架参数的 scaffold 解析器对象。
    argument_parser_scaffold: argparse.ArgumentParser = (  # 承载 spec 骨架参数的解析器
        subparser_action_root_commands.add_parser(  # 注册最小 spec 脚手架入口
            "scaffold",  # 用户请求生成 starter spec 时触发
            help="Create a starter HLS spec.",  # 帮助列表中说明它生成脚手架 spec
        )
    )

    # 批量注册 scaffold 所需参数。
    _add_argument_specs(
        argument_parser_scaffold,
        [
            _argument_spec("--target", default="hls", choices=ONLY_HLS_TARGET),
            _argument_spec("--name", default="hls_kernel"),
            _argument_spec("--out", required=True, type=Path),
        ],
    )

    # 把 scaffold 命令路由到最小 spec 骨架生成处理器。
    argument_parser_scaffold.set_defaults(func=_cmd_scaffold)

# prompt 子命令从 spec 渲染阶段化提示词。
def _register_prompt_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``prompt`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 prompt 命令挂到根命令树。
    """

    # 先准备承载提示词渲染参数的 prompt 解析器对象。
    argument_parser_prompt: argparse.ArgumentParser = (  # 提示词渲染命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册从 spec 渲染 prompt 的入口
            "prompt",  # 用户显式请求渲染提示词时触发
            help="Render an HLS prompt from a spec.",  # 帮助列表中说明它从 spec 渲染 prompt
        )
    )

    # 让 prompt 命令沿用统一的 HLS-only target 输入合同。
    _add_only_hls_target_argument(argument_parser_prompt)

    # 批量注册 prompt 渲染所需参数。
    _add_argument_specs(
        argument_parser_prompt,
        [
            _argument_spec("--spec", required=True, type=Path),
            _argument_spec("--out", required=True, type=Path),
            _argument_spec("--stage", choices=PROMPT_STAGES),
            _argument_spec(
                "--comment-language",
                default="auto",
                choices=COMMENT_LANGUAGE_CHOICES,
            ),
            _argument_spec("--budget", default="normal", choices=PROMPT_BUDGETS),
            _argument_spec("--hls-profile", type=Path),
        ],
    )

    # prompt 支持显式记录 requirements 已确认的上下文。
    _add_confirmation_args(argument_parser_prompt)

    # 把 prompt 命令绑定到提示词渲染处理器。
    argument_parser_prompt.set_defaults(func=_cmd_prompt)

# validate 子命令执行产物验证和可选报告落盘。
def _register_validate_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``validate`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 validate 命令挂到根命令树。
    """

    # 先准备承载产物验收参数的 validate 解析器对象。
    argument_parser_validate: argparse.ArgumentParser = (  # 产物验证命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册已生成产物验收入口
            "validate",  # 用户显式请求验证 HLS 产物时触发
            help="Validate generated HLS artifacts.",  # 帮助列表中说明它校验生成产物
        )
    )

    # 补入 validate 命令固定的 HLS-only target 约束。
    _add_only_hls_target_argument(argument_parser_validate)

    # 声明 validate 命令需要的 spec、产物路径与验证层级参数。
    _add_argument_specs(
        argument_parser_validate,
        [
            _argument_spec("--spec", required=True, type=Path),
            _argument_spec("--path", required=True, type=Path),
            _argument_spec(
                "--readiness",
                default="static",
                choices=READINESS_LEVELS,
            ),
            _argument_spec(
                "--comment-language",
                default="auto",
                choices=COMMENT_LANGUAGE_CHOICES,
            ),
            _argument_spec("--hls-profile", type=Path),
            _argument_spec(
                "--baseline-path",
                type=Path,
                help="Baseline HLS artifact tree for comment-only comparison.",
            ),
            _argument_spec("--report-json", type=Path),
            _argument_spec("--no-external", action="store_true"),
        ],
    )

    # validate 也允许把 requirements 确认信息带入当前运行。
    _add_confirmation_args(argument_parser_validate)

    # 把 validate 命令绑定到交付验证处理器。
    argument_parser_validate.set_defaults(func=_cmd_validate)

# readability-gate 子命令运行仓库内 HLS 可读性质量门禁。
def _register_readability_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``readability-gate`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 readability-gate 命令挂到根命令树。
    """

    # 先准备承载 HGxxx 门禁参数的 readability-gate 解析器对象。
    argument_parser_readability: argparse.ArgumentParser = (  # HLS 可读性门禁命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册 HGxxx HLS 门禁执行入口
            "readability-gate",  # 用户显式触发 HLS 可读性门禁时使用
            help="Run the HGxxx HLS readability quality gate.",  # 帮助列表中说明它执行 HGxxx 门禁
        )
    )

    # 让 HGxxx 门禁命令复用同一份 HLS-only target 限定。
    _add_only_hls_target_argument(argument_parser_readability)

    # 声明 readability-gate 需要的门禁路径、风格和报告参数。
    _add_argument_specs(
        argument_parser_readability,
        [
            _argument_spec("--path", required=True, type=Path),
            _argument_spec("--profile", default="kernel"),
            _argument_spec(
                "--style",
                default="current-project",
                choices=("default", "current-project"),
            ),
            _argument_spec("--baseline-path", type=Path),
            _argument_spec("--top-function"),
            _argument_spec("--fail-on-warning", action="store_true"),
            _argument_spec(
                "--json",
                action="store_true",
                help="Print machine-readable JSON.",
            ),
            _argument_spec("--out", type=Path, help="Optional JSON output path."),
        ],
    )

    # 把 readability-gate 命令绑定到 HLS 门禁处理器。
    argument_parser_readability.set_defaults(func=_cmd_readability_gate)

# comment-plan 子命令只生成 HLS 注释重写计划。
def _register_comment_plan_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``comment-plan`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 comment-plan 命令挂到根命令树。
    """

    # 先准备承载注释重写计划参数的 comment-plan 解析器对象。
    argument_parser_comment_plan: argparse.ArgumentParser = (  # 注释计划命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册注释改写计划生成入口
            "comment-plan",  # 用户只想生成改写计划而不改源码时触发
            help="Build an HLS comment rewrite plan without replacement text.",  # 帮助列表中说明它只产出注释计划
        )
    )

    # 让注释计划命令沿用统一的 HLS-only target 入口。
    _add_only_hls_target_argument(argument_parser_comment_plan)

    # 声明 comment-plan 需要的目标路径、baseline 和输出位置。
    _add_argument_specs(
        argument_parser_comment_plan,
        [
            _argument_spec("--path", required=True, type=Path),
            _argument_spec("--baseline-path", type=Path),
            _argument_spec("--profile", default="kernel"),
            _argument_spec("--out", required=True, type=Path),
        ],
    )

    # 把 comment-plan 命令绑定到注释计划生成处理器。
    argument_parser_comment_plan.set_defaults(func=_cmd_comment_plan)

# run-workflow 子命令驱动完整 staged HLS 生成流程。
def _register_run_workflow_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``run-workflow`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 run-workflow 命令挂到根命令树。
    """

    # 先准备承载 staged workflow 参数的解析器对象。
    argument_parser_run_workflow: argparse.ArgumentParser = (  # staged workflow 命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册 staged workflow 串行执行入口
            "run-workflow",  # 用户想运行完整多阶段流程时触发
            help="Run a staged HLS generation workflow.",  # 帮助列表中说明它串起 staged workflow
        )
    )

    # 让 staged workflow 命令复用统一的 HLS-only target 合同。
    _add_only_hls_target_argument(argument_parser_run_workflow)

    # 批量注册 workflow 执行所需参数。
    _add_argument_specs(
        argument_parser_run_workflow,
        [
            _argument_spec("--spec", type=Path),
            _argument_spec("--out-dir", type=Path),
            _argument_spec("--resume-dir", type=Path),
            _argument_spec("--decision", type=Path),
            _argument_spec(
                "--provider",
                default="manual",
                choices=("manual", "mock", "command"),
            ),
            _argument_spec("--provider-command"),
            _argument_spec(
                "--readiness",
                default="execute",
                choices=READINESS_LEVELS,
            ),
            _argument_spec("--max-attempts", default=3, type=int),
            _argument_spec(
                "--comment-language",
                default="auto",
                choices=COMMENT_LANGUAGE_CHOICES,
            ),
            _argument_spec("--hls-profile", type=Path),
            _argument_spec("--no-external", action="store_true"),
        ],
    )

    # workflow 同样要求在需要时记录 requirements 确认信息。
    _add_confirmation_args(argument_parser_run_workflow)

    # 把 run-workflow 命令绑定到 staged workflow 处理器。
    argument_parser_run_workflow.set_defaults(func=_cmd_run_workflow)

# optimize-hls-prompt 子命令根据报告生成聚焦修复提示词。
def _register_optimize_hls_prompt_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``optimize-hls-prompt`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 optimize-hls-prompt 命令挂到根命令树。
    """

    # 先准备承载 profile 修复提示词参数的解析器对象。
    argument_parser_optimize_prompt: argparse.ArgumentParser = (  # profile 修复提示词命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册基于报告生成修复 prompt 的入口
            "optimize-hls-prompt",  # 用户想围绕报告补优化 prompt 时触发
            help="Generate a focused HLS profile repair prompt.",  # 帮助列表中说明它生成修复型 prompt
        )
    )

    # 批量注册优化提示词所需参数。
    _add_argument_specs(
        argument_parser_optimize_prompt,
        [
            _argument_spec("--report-json", required=True, type=Path),
            _argument_spec("--profile", required=True, type=Path),
            _argument_spec("--out", required=True, type=Path),
        ],
    )

    # 把 optimize-hls-prompt 命令绑定到 profile 提示词修复处理器。
    argument_parser_optimize_prompt.set_defaults(func=_cmd_optimize_hls_prompt)

# config 子命令读取并输出当前激活的 runtime 配置。
def _register_config_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``config`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 config 命令挂到根命令树。
    """

    # 先准备承载 runtime 配置查询参数的解析器对象。
    argument_parser_config: argparse.ArgumentParser = (  # runtime 配置查询命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册 runtime 配置查询入口
            "config",  # 用户查看当前生效配置时触发
            help="Print the active runtime configuration.",  # 帮助列表中说明它输出当前 runtime 配置
        )
    )

    # 注册 config 所需的最小参数集合。
    _add_argument_specs(
        argument_parser_config,
        [
            _argument_spec(
                "--path",
                action="store_true",
                help="Print only the active config file path.",
            ),
        ],
    )

    # 把 config 命令绑定到运行时配置查询处理器。
    argument_parser_config.set_defaults(func=_cmd_config)

# selfcheck 子命令运行不依赖外部技能和 Vitis 的本地自检。
def _register_selfcheck_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``selfcheck`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 selfcheck 命令挂到根命令树。
    """

    # 先准备承载本地自检参数的解析器对象。
    argument_parser_selfcheck: argparse.ArgumentParser = (  # 本地自检命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册包内最小自检入口
            "selfcheck",  # 用户想确认本地最小健康状态时触发
            help="Run package-local checks without external skills or Vitis.",  # 帮助列表中说明它执行包内自检
        )
    )

    # 注册 selfcheck 唯一的输出格式参数。
    _add_argument_specs(
        argument_parser_selfcheck,
        [
            _argument_spec(
                "--json",
                action="store_true",
                help="Print machine-readable JSON.",
            ),
        ],
    )

    # 把 selfcheck 命令绑定到最小健康检查处理器。
    argument_parser_selfcheck.set_defaults(func=_cmd_selfcheck)

# user-config 子命令读取或更新用户级注释语言配置。
def _register_user_config_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``user-config`` 子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 user-config 命令挂到根命令树。
    """

    # 先准备承载用户偏好管理参数的解析器对象。
    argument_parser_user_config: argparse.ArgumentParser = (  # 用户偏好管理命令的参数解析器
        subparser_action_root_commands.add_parser(  # 注册用户偏好读取与更新入口
            "user-config",  # 用户查看或更新注释语言偏好时触发
            help="Print or update the user-level HLS generator config.",  # 帮助列表中说明它处理用户级配置
        )
    )

    # 批量注册用户配置查看与更新参数。
    _add_argument_specs(
        argument_parser_user_config,
        [
            _argument_spec(
                "--path",
                action="store_true",
                help="Print only the user config path.",
            ),
            _argument_spec(
                "--set-comment-language",
                choices=("en", "zh"),
                help="Persist the generated C/HLS comment language preference.",
            ),
        ],
    )

    # 把 user-config 命令绑定到用户配置读写处理器。
    argument_parser_user_config.set_defaults(func=_cmd_user_config)

# deps 子命令下再细分 check/request/install 三个依赖治理流程。
def _register_deps_parser(
    subparser_action_root_commands: SubparserAction,
) -> None:
    """
    注册 ``deps`` 子命令及其二级子命令。

    :param subparser_action_root_commands: 根命令树的一级子命令注册动作。
    :return: 无业务返回值；函数会把 deps 命令树挂到根命令树。
    """

    # 先准备承载依赖治理入口参数的一级解析器对象。
    argument_parser_deps: argparse.ArgumentParser = (  # 依赖治理入口命令的一级解析器
        subparser_action_root_commands.add_parser(  # 注册依赖治理总入口
            "deps",  # 用户进入依赖检查与安装流程时触发
            help="Check or install HLSGenerator skill dependencies.",  # 帮助列表中说明它管理技能依赖
        )
    )

    # deps 下还需要二级子命令区分检查、请求与安装。
    subparser_action_dependency_commands: SubparserAction = (
        argument_parser_deps.add_subparsers(  # 承载 deps 二级命令的统一注册动作
            dest="deps_command",  # deps 二级子命令字段名
            required=True,  # 强制要求显式选择 deps 二级子命令
        )
    )

    # 先准备承载依赖检查参数的解析器对象。
    argument_parser_deps_check: argparse.ArgumentParser = (  # 依赖检查命令的参数解析器
        subparser_action_dependency_commands.add_parser(  # 注册依赖状态检查入口
            "check",  # 用户只查看依赖健康状态时触发
            help="Check configured skill dependencies.",  # 帮助列表中说明它只做依赖检查
        )
    )

    # check 支持人类可读和机器可读两种输出格式。
    _add_argument_specs(
        argument_parser_deps_check,
        [
            _argument_spec(
                "--json",
                action="store_true",
                help="Print machine-readable JSON.",
            ),
            _argument_spec(
                "--human",
                action="store_true",
                help="Print human-readable status.",
            ),
        ],
    )

    # 把 deps check 命令绑定到依赖检查处理器。
    argument_parser_deps_check.set_defaults(func=_cmd_deps_check)

    # 先准备承载安装请求参数的解析器对象。
    argument_parser_deps_request: argparse.ArgumentParser = (  # 依赖请求命令的参数解析器
        subparser_action_dependency_commands.add_parser(  # 注册依赖申请文件生成入口
            "request",  # 用户需要导出缺失依赖申请单时触发
            help="Write an install request for missing dependencies.",  # 帮助列表中说明它写出安装请求
        )
    )

    # request 只要求输出目标路径。
    _add_argument_specs(
        argument_parser_deps_request,
        [
            _argument_spec("--out", required=True, type=Path),
        ],
    )

    # 把 deps request 命令绑定到安装请求写入处理器。
    argument_parser_deps_request.set_defaults(func=_cmd_deps_request)

    # 先准备承载依赖安装参数的解析器对象。
    argument_parser_deps_install: argparse.ArgumentParser = (  # 依赖安装命令的参数解析器
        subparser_action_dependency_commands.add_parser(  # 注册依赖安装执行入口
            "install",  # 用户确认后实际执行依赖安装时触发
            help="Install selected skill dependencies after user confirmation.",  # 帮助列表中说明它执行依赖安装
        )
    )

    # install 支持按 id、全量和目标技能根三种输入。
    _add_argument_specs(
        argument_parser_deps_install,
        [
            _argument_spec("--ids", nargs="+", help="Dependency ids to install."),
            _argument_spec(
                "--all",
                action="store_true",
                help="Install all configured dependencies.",
            ),
            _argument_spec(
                "--dest",
                type=Path,
                help="Override destination skills root.",
            ),
        ],
    )

    # 把 deps install 命令绑定到依赖安装处理器。
    argument_parser_deps_install.set_defaults(func=_cmd_deps_install)

# scaffold 只生成最小 spec 并输出生成位置。
def _cmd_scaffold(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``scaffold`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含 spec 名称和输出路径。
    :return: 成功返回 ``0``。
    """

    # 生成最小 HLS spec 骨架，供 prompt 或 workflow 继续消费。
    dict_spec_payload = scaffold_spec("hls", name=namespace_args.name)  # 初始 HLS spec 载荷

    # 输出路径仍需经过工作区边界校验，避免写出允许目录之外。
    path_output = require_configured_output_path(  # spec 输出路径
        namespace_args.out,  # CLI 提供的 spec 输出目标文件
        purpose="spec output path",  # 让路径校验器按 spec 输出语义报错
    )

    # 把生成的 HLS spec 落盘到目标位置。
    write_spec(path_output, dict_spec_payload)

    # 向调用方输出最终产物位置。
    print(f"> INFO: [Python] scaffold spec written: {path_output}")

    # 把脚手架 spec 成功写入目标位置后返回成功状态。
    return 0

# prompt 从 spec 渲染提示词，并支持记录 requirements 确认上下文。
def _cmd_prompt(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``prompt`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含 spec、输出路径和渲染选项。
    :return: 成功返回 ``0``。
    """

    # prompt 渲染依赖 core 技能可用，并把依赖请求文件放到输出目录旁。
    _require_dependencies_for_use(namespace_args.out.parent, scopes={"core"})

    # 读取 spec 并显式补入当前调用提供的确认信息。
    dict_spec_payload = _spec_with_explicit_confirmation(  # 已补确认信息的 HLS spec
        read_spec(  # 先读取原始 spec，再补显式确认信息
            require_workspace_path(  # 把 prompt 输入 spec 约束在允许的工作区路径内
                namespace_args.spec,  # prompt 命令输入的 spec 文件
                purpose="spec path",  # 供 prompt spec 路径校验使用的用途标签
                must_exist=True,  # prompt 渲染依赖现有 spec 文件
            ),
            target="hls",  # 固定按 HLS spec 合同解析
        ),
        namespace_args,  # prompt 命令解析得到的参数命名空间
    )

    # 读取可选 HLS profile JSON，供 prompt 渲染阶段使用。
    dict_hls_profile = _read_optional_json(namespace_args.hls_profile)  # 可选 HLS profile 配置

    # 把注释语言、预算和 stage 约束折叠进最终提示词文本。
    str_prompt_text = render_prompt(  # 渲染后的提示词文本
        dict_spec_payload,  # 已补 requirement 默认值与确认上下文的 spec
        target="hls",  # 固定走 HLS prompt 渲染分支
        stage=namespace_args.stage,  # 可选 prompt 渲染阶段
        comment_language=_require_resolved_comment_language(  # 解析本次 prompt 要使用的注释语言
            namespace_args.comment_language,  # CLI 请求值或 auto
        ),
        budget=namespace_args.budget,  # prompt 预算档位
        hls_profile=dict_hls_profile,  # 可选 HLS profile 约束载荷
        codegen_plan=build_codegen_plan(dict_spec_payload),  # 基于 spec 推导出的 codegen 计划
    )

    # prompt 文本输出也要落在受控工作区路径中。
    path_output = require_configured_output_path(  # 渲染后 prompt 文本的目标文件
        namespace_args.out,  # CLI 提供的 prompt 目标文件
        purpose="prompt output path",  # 供 prompt 输出边界校验使用的用途标签
    )

    # 先确保父目录存在，再写入 UTF-8 提示词文本。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 把渲染好的提示词文本写入目标文件。
    path_output.write_text(str_prompt_text, encoding="utf-8")

    # 向调用方返回最终提示词文件路径。
    print(f"> INFO: [Python] prompt written: {path_output}")

    # 把提示词写入目标文件后返回成功状态。
    return 0

# validate 负责静态或外部工具参与的 HLS 产物验证。
def _cmd_validate(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``validate`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含 spec、产物路径、readiness 和报告位置。
    :return: 验证通过返回 ``0``，否则返回 ``1``。
    """

    # static 验证不应依赖 remote/Vitis；更高 readiness 才要求外部依赖。
    if (not namespace_args.no_external) and namespace_args.readiness != "static":

        # 外部依赖请求文件优先跟随报告目录，便于用户就近处理。
        path_request_dir: Path | None = None  # 外部依赖请求文件的候选输出目录

        # 只有显式指定报告路径时，才把其父目录作为请求目录。
        if namespace_args.report_json is not None:

            # 把报告目录复用为依赖请求文件的就近落点。
            path_request_dir = namespace_args.report_json.parent  # 让依赖请求文件贴着验证报告输出

        # 外部验证前必须确认核心依赖可用。
        _require_dependencies_for_use(path_request_dir, scopes={"core"})

    # 先把 validate 输入 spec 校验到允许的工作区路径内。
    path_validate_spec = require_workspace_path(  # validate 输入 spec 的已校验路径
        namespace_args.spec,  # validate 从这里读取待校验的 spec 文件
        purpose="spec path",  # 让 spec 缺失或越界报错落在 validate 语境
        must_exist=True,  # validate 依赖现有 spec 文件
    )

    # 再读取 validate 使用的原始 HLS spec 载荷。
    dict_validate_spec_source = read_spec(  # validate 阶段读取到的原始 spec 载荷
        path_validate_spec,  # 已校验且确认存在的 spec 文件路径
        target="hls",  # validate 读取时使用 HLS spec 解析合同
    )

    # 最后把显式确认信息补到 validate 使用的 spec 副本上。
    dict_spec_payload = _spec_with_explicit_confirmation(  # 已确认的 HLS spec
        dict_validate_spec_source,  # 尚未补 requirement 默认值与确认信息的原始 spec
        namespace_args,  # 本次 validate 调用解析后的参数上下文
    )

    # 读取待校验的产物目录。
    path_artifacts = require_workspace_path(  # 待验证的 HLS 产物路径
        namespace_args.path,  # CLI 提供的 HLS 产物根路径
        purpose="artifacts path",  # 标记这是待验证的产物目录
        must_exist=True,  # validate 目标目录必须已经存在
    )

    # 读取可选 HLS profile，供验证阶段补充 profile 语义。
    dict_hls_profile = _read_optional_json(namespace_args.hls_profile)  # 供验证阶段附加 profile 约束的可选 JSON 配置

    # 读取可选 baseline 路径，用于 comment-only 差异比较。
    path_baseline = _read_optional_workspace_path(  # 可选 baseline 路径
        namespace_args.baseline_path,  # CLI 提供的 baseline 根路径
        purpose="baseline path",  # 标记这是 validate 使用的 baseline 目录
    )

    # 执行完整验证并收集结构化报告对象。
    report_validation = validate_generated(  # HLS 产物验证报告对象
        dict_spec_payload,  # 已通过 requirement 校验的 spec 载荷
        path_artifacts,  # 待验证的 HLS 产物目录
        target="hls",  # 固定走 HLS 产物验证分支
        options=ValidationRunOptions(  # validate_generated 要消费的验证运行选项对象
            # 这一组参数决定是否调用外部工具，以及验证推进到哪个 readiness 层级。
            run_external=not namespace_args.no_external,  # 是否允许调用外部工具
            readiness=namespace_args.readiness,  # 当前验证推进到的 readiness 层级

            # 这一组参数决定注释语言与可选参考输入的参与方式。
            comment_language=_require_resolved_comment_language(  # 折叠 validate CLI 请求值与用户默认语言
                namespace_args.comment_language,  # validate 传入的语言请求值
            ),
            hls_profile=dict_hls_profile,  # validate 阶段附加的 HLS profile 约束载荷
            baseline_path=path_baseline,  # comment-only 对照时使用的 baseline 根路径
        ),
    )

    # 需要 JSON 报告时，把结构化结果写入显式目标路径。
    path_validation_json: Path | None = None  # 可选验证报告输出路径

    # 仅在用户请求 JSON 报告时写出结构化验证结果。
    if namespace_args.report_json:

        # 报告文件路径仍要通过工作区写入边界检查。
        path_validation_json = require_configured_output_path(  # 验证报告路径
            namespace_args.report_json,  # CLI 提供的验证报告输出文件
            purpose="validation report path",  # 标记这是 validate 的报告输出路径
        )

        # 确保报告目录存在后再写入 JSON。
        path_validation_json.parent.mkdir(parents=True, exist_ok=True)

        # 把验证结果以结构化 JSON 形式写入磁盘。
        path_validation_json.write_text(
            json.dumps(
                report_validation.to_dict(),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    # 生成人类可读的总体验证状态摘要。
    str_validation_status = "ok" if report_validation.ok() else "failed"  # 验证总体状态

    # 需要同时回显 JSON 文件位置时，先确认报告已经生成。
    if path_validation_json is not None:

        # 回显验证状态与结构化报告文件位置。
        print(
            f"> INFO: [Python] validation status: {str_validation_status}; "
            f"report_json={path_validation_json}"
        )

    # 未生成 JSON 报告时，只输出最小状态摘要。
    else:

        # 只回显验证状态，避免在终端打印完整报告正文。
        print(f"> INFO: [Python] validation status: {str_validation_status}")

    # 验证完全通过返回 0，否则返回 1。
    return 0 if report_validation.ok() else 1

# 在 argparse 之前识别已经移除的 Python reference 旧入口。
def _legacy_cli_error(list_argv: list[str]) -> str | None:
    """
    识别已经移除的 CLI 旧命令和旧参数。

    参数:
        list_argv: 当前 CLI 实际要解析的参数序列。

    返回:
        命中旧入口时返回显式报错文本；否则返回 None。
    """

    # 旧 validate-python-reference 子命令已经移除，命中时必须先显式报错。
    if list_argv and list_argv[0] == "validate-python-reference":

        # 返回统一的 HLS-only 重跑提示文本。
        return LEGACY_REFERENCE_COMMAND_ERROR

    # 旧 --reference-contract 参数无论用独立参数还是 `--flag=value` 都必须显式报错。
    if any(
        str_argument == "--reference-contract"
        or str_argument.startswith("--reference-contract=")
        for str_argument in list_argv
    ):

        # 返回 legacy 标志专用的 HLS-only 重跑提示文本。
        return LEGACY_REFERENCE_FLAG_ERROR

    # 未命中旧入口时继续正常走 argparse 解析。
    return None

# readability-gate 调用仓库内 HLS-only 可读性规则。
def _cmd_readability_gate(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``readability-gate`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含目标路径、style、baseline 和输出选项。
    :return: 门禁通过返回 ``0``，否则返回 ``1``。
    """

    # baseline 只用于对照同源 HLS 产物的注释与版式变化。
    path_baseline = _read_optional_workspace_path(  # HLS gate 对照树的可选 baseline 根路径
        namespace_args.baseline_path,  # 用于 HLS gate 对照比对的 baseline 根路径
        purpose="baseline path",  # 标记这是 HLS gate 的 baseline 目录
    )

    # 执行 HLS readability gate 并获取结构化报告对象。
    report_readability = run_hls_readability_gate(  # HLS gate 报告对象
        require_workspace_path(  # 先校验待执行门禁的目标路径
            namespace_args.path,  # CLI 提供的 HLS gate 目标路径
            purpose="HLS readability target path",  # 标记这是 HLS gate 的目标路径
            must_exist=True,  # HLS gate 目标路径必须已经存在
        ),
        profile=namespace_args.profile,  # HLS gate 使用的规则画像
        style=namespace_args.style,  # HLS gate 使用的风格档
        baseline_root=path_baseline,  # HLS gate 对照用的 baseline 根路径
        fail_on_warning=bool(namespace_args.fail_on_warning),  # 是否把 warning 也升级成失败
        top_function=namespace_args.top_function,  # 可选指定的顶层函数名
    )

    # 需要落盘时写入结构化 JSON 报告。
    path_readability_json: Path | None = None  # 可选 readability 报告路径

    # 仅在用户提供输出路径时落盘 HLS gate 的 JSON 报告。
    if namespace_args.out:

        # 输出路径仍需接受工作区写入边界治理。
        path_readability_json = require_configured_output_path(  # readability 报告路径
            namespace_args.out,  # CLI 提供的 HLS gate 报告文件
            purpose="HLS readability report path",  # 供 HLS gate 报告写入边界校验使用的用途标签
        )

        # 报告对象自带 JSON 写入逻辑。
        report_readability.write_json(path_readability_json)

    # --json 要求直接打印机器可读 JSON。
    if namespace_args.json:

        # 保持 stdout 纯 JSON，便于脚本解析。
        sys.stdout.write(
            json.dumps(
                report_readability.to_dict(),
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )

    # 人类可读分支只回显简短摘要。
    else:

        # 生成人类可读的 gate 总体状态。
        str_gate_status = "ok" if report_readability.ok() else "failed"  # HLS gate 总体状态

        # 只有真的生成了 JSON 文件时，摘要里才带上 report_json 字段。
        if path_readability_json is not None:

            # 回显门禁状态与 JSON 报告文件位置。
            print(
                f"> INFO: [Python] readability gate status: {str_gate_status}; "
                f"report_json={path_readability_json}"
            )

        # 未生成 JSON 报告时，只输出总体状态。
        else:

            # 没有额外报告文件时仅回显总体状态。
            print(f"> INFO: [Python] readability gate status: {str_gate_status}")

    # 所有检查通过返回 0，否则返回 1。
    return 0 if report_readability.ok() else 1

# comment-plan 只生成注释改写计划，不直接触碰目标文件。
def _cmd_comment_plan(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``comment-plan`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含目标路径、baseline 和计划输出位置。
    :return: 成功返回 ``0``。
    """

    # baseline 在这里用于估算注释改写计划的增量范围。
    path_baseline = _read_optional_workspace_path(  # comment-plan 增量估算使用的 baseline 根路径
        namespace_args.baseline_path,  # 用于估算注释改写增量的 baseline 根路径
        purpose="baseline path",  # 让注释计划在 baseline 缺失时报出增量估算语境
    )

    # 构造注释重写计划对象。
    obj_comment_plan = build_hls_comment_rewrite_plan(  # HLS 注释重写计划对象
        require_workspace_path(  # 先校验待生成计划的 HLS 源文件路径
            namespace_args.path,  # CLI 提供的注释计划目标路径
            purpose="HLS comment-plan target path",  # 标记这是注释计划的目标路径
            must_exist=True,  # 只有现有 HLS 源文件才能生成注释计划
        ),
        baseline_root=path_baseline,  # 传给计划生成器的 baseline 对照树
        profile=namespace_args.profile,  # comment-plan 选用的 HGxxx 规则画像
    )

    # 计划文件也必须落在允许写入的工作区路径内。
    path_output = require_configured_output_path(  # 存放 comment-plan JSON 的目标文件
        namespace_args.out,  # CLI 提供的计划输出文件
        purpose="HLS comment rewrite plan path",  # 标记这是注释计划文件输出路径
    )

    # 落盘计划并回显生成位置。
    write_hls_comment_rewrite_plan(obj_comment_plan, path_output)

    # 向调用方返回 comment-plan 输出位置。
    print(f"> INFO: [Python] comment plan written: {path_output}")

    # 把注释计划写入目标位置后返回成功状态。
    return 0

# run-workflow 驱动完整 staged HLS 生成、验证与恢复流程。
def _cmd_run_workflow(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``run-workflow`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含新建或恢复 workflow 所需的目录与 provider 选项。
    :return: workflow 通过返回 ``0``，否则返回 ``1``。
    :raises ValueError: 当新建 workflow 缺少 ``--spec`` 或 ``--out-dir`` 时抛出。
    """

    # 新建 workflow 至少需要 spec 与 out-dir；恢复模式则要求 resume-dir。
    if namespace_args.resume_dir is None and (
        namespace_args.spec is None or namespace_args.out_dir is None
    ):

        # 缺失关键参数时立即阻断，避免 workflow 状态目录不完整。
        raise ValueError(
            "> ERR: [Python] run-workflow requires --spec and --out-dir for new runs, or "
            "--resume-dir for resume.",
        )

    # workflow 会触发 core/remote/vitis 多类能力，因此提前做依赖检查。
    _require_dependencies_for_use(
        namespace_args.resume_dir or namespace_args.out_dir,
        scopes={"core", "remote", "vitis"},
    )

    # 读取可选 HLS profile，供 workflow 内部 prompt/render 阶段细化生成约束。
    dict_hls_profile = _read_optional_json(namespace_args.hls_profile)  # 供 workflow 阶段细化约束的可选 HLS profile JSON

    # 运行 staged workflow 并得到结构化执行结果。
    dict_workflow_result = run_workflow(  # workflow 结构化执行结果
        WorkflowRunRequest(  # run_workflow 要消费的 staged workflow 请求对象
            spec_path=namespace_args.spec,  # 新建 workflow 时使用的 spec 文件
            target="hls",  # 固定走 HLS staged workflow 分支

            # 这一组参数决定 workflow 的运行目录与恢复来源。
            out_dir=namespace_args.out_dir,  # 新建 workflow 时的 run 根目录
            resume_dir=namespace_args.resume_dir,  # 恢复 workflow 时的既有 run 目录
            decision_path=namespace_args.decision,  # staged workflow 的决策文件路径

            # 这一组参数决定模型提供方与执行深度。
            provider_name=namespace_args.provider,  # 选择 manual/mock/command 哪种提供方
            provider_command=namespace_args.provider_command,  # command 提供方对应的执行命令
            readiness=namespace_args.readiness,  # workflow 推进到的 readiness 层级
            max_attempts=namespace_args.max_attempts,  # staged workflow 的最大尝试次数
            run_external=not namespace_args.no_external,  # staged workflow 是否可以触发外部链路验证

            # 这一组参数决定注释语言、profile 与确认上下文。
            comment_language=namespace_args.comment_language,  # workflow 直接收到的语言请求值
            hls_profile=dict_hls_profile,  # staged workflow 共享的 HLS profile 约束
            confirmation=_confirmation_payload(namespace_args),  # 供 workflow 复用的 requirement 确认载荷
        ),
    )

    # 回显 workflow 总体状态和当前 run 目录。
    sys.stdout.write(
        json.dumps(
            {
                "status": dict_workflow_result["status"],
                "run_dir": str(namespace_args.resume_dir or namespace_args.out_dir),
            },
            indent=2,
        )
        + "\n"
    )

    # workflow 只有在 passed 时才返回 0。
    return 0 if dict_workflow_result["status"] == "passed" else 1

# optimize-hls-prompt 从已有报告与 profile 生成修复提示词。
def _cmd_optimize_hls_prompt(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``optimize-hls-prompt`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含输入报告、profile 和提示词输出位置。
    :return: 成功返回 ``0``。
    """

    # 读取质量或验证报告 JSON，供修复 prompt 提取失败上下文。
    dict_report_payload = _read_json(namespace_args.report_json)  # 提供失败上下文的报告 JSON 载荷

    # 读取 HLS profile JSON，供修复 prompt 恢复 profile 约束细节。
    dict_profile_payload = _read_json(namespace_args.profile)  # 提供目标约束的 HLS profile JSON 载荷

    # 读取报告与 profile 后生成聚焦修复提示词文本。
    str_prompt_text = build_hls_optimizer_prompt(  # HLS profile 修复提示词文本
        dict_report_payload,  # 描述当前失败上下文的报告载荷
        dict_profile_payload,  # 描述目标约束的 HLS profile 载荷
    )

    # 输出路径仍需通过工作区写入边界治理。
    path_output = require_configured_output_path(  # 优化提示词输出路径
        namespace_args.out,  # CLI 提供的优化提示词输出文件
        purpose="optimizer prompt output path",  # 标记这是优化 prompt 输出路径
    )

    # 先创建父目录，再写入 UTF-8 提示词文本。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 把修复提示词文本写入目标文件。
    path_output.write_text(str_prompt_text, encoding="utf-8")

    # 回显最终输出位置。
    print(f"> INFO: [Python] optimizer prompt written: {path_output}")

    # 把修复提示词写入目标位置后返回成功状态。
    return 0

# config 负责打印当前生效的 runtime 配置或其路径。
def _cmd_config(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``config`` 子命令。

    :param namespace_args: 解析后的命令行参数，决定输出配置路径还是完整配置。
    :return: 成功返回 ``0``。
    """

    # 先确保 runtime 配置本身可读、可解析且满足合同。
    validate_runtime_config()

    # 仅请求路径时，只输出配置文件路径。
    if namespace_args.path:

        # 先读取 runtime 配置文件路径，再回显给调用方。
        path_runtime_file = config_path()  # runtime 配置文件路径

        # 回显 runtime 配置文件所在位置。
        print(f"> INFO: [Python] runtime config path: {path_runtime_file}")

        # 仅返回 runtime 配置文件路径后结束当前命令。
        return 0

    # 默认输出完整 runtime 配置对象。
    sys.stdout.write(json.dumps(runtime_config(), indent=2, ensure_ascii=False) + "\n")

    # 输出运行时配置后返回成功状态。
    return 0

# selfcheck 运行最小本地自检，不依赖外部技能或 Vitis。
def _cmd_selfcheck(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``selfcheck`` 子命令。

    :param namespace_args: 解析后的命令行参数，决定是否输出机器可读 JSON。
    :return: 成功返回 ``0``。
    """

    # 先准备结构化自检结果，便于 JSON 与人类可读输出复用。
    dict_selfcheck_payload = {
        "status": "ok",  # 当前包内自检总体状态
        "version": __version__,  # 当前 CLI 暴露的包版本号
        "external_dependencies_checked": False,  # selfcheck 不触碰外部技能与 Vitis
        "checks": {  # 最小本地健康检查明细
            "runtime_config": "ok",  # runtime 配置可被读取并通过合同校验
            "scaffold_spec": "ok",  # 最小 HLS spec 脚手架可被成功构造
        },
    }  # selfcheck 结构化结果载荷

    # config 与 scaffold 是不依赖外部技能的最小本地健康检查。
    validate_runtime_config()

    # 再确认最小 HLS spec 脚手架可正常构造。
    scaffold_spec("hls", name="selfcheck_kernel")

    # --json 模式输出完整结构化结果。
    if namespace_args.json:

        # 保持 stdout 为机器可读 JSON。
        sys.stdout.write(json.dumps(dict_selfcheck_payload, indent=2, ensure_ascii=False) + "\n")

    # 未请求 JSON 协议时，仅回显总体状态。
    else:

        # 默认终端模式仅回显总体状态。
        print("> INFO: [Python] selfcheck status: ok")

    # 完成本地健康检查后返回成功状态。
    return 0

# user-config 读取或更新用户级注释语言偏好。
def _cmd_user_config(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``user-config`` 子命令。

    :param namespace_args: 解析后的命令行参数，决定读取、更新还是仅返回配置路径。
    :return: 成功返回 ``0``。
    """

    # 显式设置注释语言时，优先执行写入并返回配置文件路径。
    if namespace_args.set_comment_language:

        # set_comment_language 会持久化用户级语言偏好。
        path_user_preferences_file = set_comment_language(  # 更新后的用户配置文件路径
            namespace_args.set_comment_language,  # CLI 提供的新注释语言偏好
        )

        # 把更新后的用户配置文件位置返回给调用方。
        print(f"> INFO: [Python] user config updated: {path_user_preferences_file}")

        # 成功写入后即可结束当前命令。
        return 0

    # 仅请求路径时返回用户配置文件位置。
    if namespace_args.path:

        # 先读取用户配置路径，再回显给调用方。
        path_user_preferences_file = user_config_path()  # 用户配置文件路径

        # 回显用户配置文件所在位置。
        print(f"> INFO: [Python] user config path: {path_user_preferences_file}")

        # 仅返回用户配置路径后结束当前命令。
        return 0

    # 默认输出完整用户配置内容。
    sys.stdout.write(json.dumps(load_user_config(), indent=2, ensure_ascii=False) + "\n")

    # 输出或更新用户配置后返回成功状态。
    return 0

# deps check 读取依赖配置并输出当前状态。
def _cmd_deps_check(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``deps check`` 子命令。

    :param namespace_args: 解析后的命令行参数，决定输出 JSON 还是人类可读摘要。
    :return: 未阻断返回 ``0``，阻断依赖时返回 ``1``。
    """

    # 先生成 core 依赖的统一检查报告。
    dict_dependency_report = check_skill_dependencies(  # core 依赖检查报告
        skill_dependencies_config(),  # 当前仓库声明的技能依赖配置
        scopes={"core"},  # 当前命令只检查 core 依赖域
    )

    # 默认优先返回机器可读 JSON；--human 时转为人类可读文本。
    if namespace_args.json or not namespace_args.human:

        # JSON 输出更适合脚本和 CI 消费。
        sys.stdout.write(json.dumps(dict_dependency_report, indent=2, ensure_ascii=False) + "\n")

    # 仅在显式人类可读模式下回显状态摘要。
    else:

        # 终端交互场景下输出格式化文本。
        str_dependency_status = str(  # 规范化为终端摘要可打印的依赖状态文本
            dict_dependency_report.get("status", "unknown"),  # 报告中声明的总体依赖状态
        )

        # 人类可读模式下只回显总体状态，完整报告交给 JSON 协议输出。
        print(f"> INFO: [Python] dependency check status: {str_dependency_status}")

    # 被阻断的依赖状态返回 1，其余状态返回 0。
    return (
        0
        if dict_dependency_report["status"] != BLOCKED_DEPENDENCY
        else 1
    )

# deps request 把缺失依赖转换成可审查的安装请求文件。
def _cmd_deps_request(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``deps request`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含安装请求输出位置。
    :return: 成功返回 ``0``。
    """

    # 先复用统一依赖检查结果，为安装请求文件准备原始输入。
    dict_dependency_report = check_skill_dependencies(  # 生成安装请求前使用的 core 检查结果
        skill_dependencies_config(),  # 为安装请求读取的依赖配置源
        scopes={"core"},  # request 子命令只检查 core 依赖域
    )

    # 基于检查结果构造可审查的安装请求载荷。
    dict_dependency_request = build_dependency_request(  # 依赖请求 JSON 载荷
        dict_dependency_report,  # 上一步生成的结构化依赖检查结果
    )

    # 请求文件输出路径需通过工作区边界治理。
    path_output = require_configured_output_path(  # 依赖请求输出路径
        namespace_args.out,  # CLI 提供的依赖请求输出文件
        purpose="dependency request output path",  # 标记这是依赖请求文件输出路径
    )

    # 创建父目录并写入 JSON 请求。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 把依赖请求 JSON 写入目标文件。
    path_output.write_text(
        json.dumps(
            dict_dependency_request,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    # 回显请求文件位置。
    print(f"> INFO: [Python] dependency request written: {path_output}")

    # 把安装请求写入目标位置后返回成功状态。
    return 0

# deps install 在用户确认后执行依赖安装流程。
def _cmd_deps_install(namespace_args: argparse.Namespace) -> int:
    """
    执行 ``deps install`` 子命令。

    :param namespace_args: 解析后的命令行参数，包含依赖 id、全量标记和目标安装目录。
    :return: 安装成功返回 ``0``，否则返回 ``1``。
    """

    # 调用统一安装器执行所选依赖安装。
    dict_install_result = install_skill_dependencies(  # 依赖安装结构化结果
        skill_dependencies_config(),  # 执行安装前读取的依赖配置源
        ids=namespace_args.ids,  # 用户点名要安装的依赖 id 列表
        install_all=bool(namespace_args.all),  # 是否忽略 ids 并执行全量安装
        dest_root=namespace_args.dest,  # 可选覆盖后的技能安装目标根目录
    )

    # 保持 stdout 为结构化 JSON，便于脚本和用户复盘。
    sys.stdout.write(json.dumps(dict_install_result, indent=2, ensure_ascii=False) + "\n")

    # 只有 status 为 ok 才返回 0。
    return 0 if dict_install_result["status"] == "ok" else 1

# 为所有需要 requirements 显式确认的子命令挂载共用参数。
def _add_confirmation_args(parser_command: argparse.ArgumentParser) -> None:
    """
    注册 requirements 确认相关的共用参数。

    :param parser_command: 当前需要挂载确认参数的子命令解析器。
    :return: 无业务返回值；函数会直接修改传入的解析器对象。
    """

    # --confirm-requirements 标记当前调用已经得到用户明确确认。
    parser_command.add_argument(
        "--confirm-requirements",
        action="store_true",
        help="Explicitly mark requirements as user-confirmed for this call.",
    )

    # --confirmation-notes 用于记录确认来源与上下文说明。
    parser_command.add_argument(
        "--confirmation-notes",
        help="Required notes to record when --confirm-requirements is used.",
    )

# 从命令行确认参数生成 requirement 默认值补丁并执行校验。
def _spec_with_explicit_confirmation(
    dict_spec_payload: dict[str, Any],
    namespace_args: argparse.Namespace,
) -> dict[str, Any]:
    """
    把显式确认信息注入 spec 并完成 requirement 校验。

    :param dict_spec_payload: 原始 HLS spec 载荷。
    :param namespace_args: 解析后的命令行参数，可能携带 requirements 确认标记。
    :return: 已补默认值并通过 requirement 校验的 spec 载荷。
    """

    # 先把命令行确认状态规范化为统一 payload。
    dict_confirmation = _confirmation_payload(  # 当前调用的确认信息载荷
        namespace_args,  # 当前子命令解析得到的参数命名空间
    )

    # requirement 默认值与确认信息会一起写回 spec 副本。
    dict_spec_payload = apply_requirement_defaults(  # 已补 requirement 默认值的 spec
        dict_spec_payload,  # 当前待补默认值的 spec 载荷
        **(dict_confirmation or {}),  # 已规范化的显式确认补丁字段
    )

    # 统一 requirement 校验确保后续 prompt/workflow 不消费非法 spec。
    validate_requirement_confirmation(dict_spec_payload)

    # 返回已经通过 requirement 校验的 spec。
    return dict_spec_payload

# 把 CLI 级确认参数转换成运行时可复用的结构化 payload。
def _confirmation_payload(
    namespace_args: argparse.Namespace,
) -> dict[str, object] | None:
    """
    解析 requirements 显式确认参数。

    :param namespace_args: 解析后的命令行参数，包含确认标记与说明文本。
    :return: 返回标准化确认载荷；未显式确认时返回 ``None``。
    :raises ValueError: 当声明已确认但缺少确认说明时抛出。
    """

    # 未显式确认时返回 None，保持旧调用语义不变。
    if not getattr(namespace_args, "confirm_requirements", False):

        # 没有确认信息时直接返回空值。
        return None

    # 读取并清洗确认说明文本。
    str_notes = str(  # 用户确认备注文本
        getattr(namespace_args, "confirmation_notes", "") or "",  # CLI 传入的确认说明原始文本
    ).strip()

    # 已勾选确认但没给说明时，必须阻断当前调用。
    if not str_notes:

        # 明确要求补充 confirmation-notes，避免审计信息缺失。
        raise ValueError(
            "> ERR: [Python] --confirmation-notes is required when "
            "--confirm-requirements is used.",
        )

    # 返回统一结构化确认载荷。
    return {
        "confirmed_by_user": True,
        "confirmation_notes": str_notes,
    }

# 批量参数规格统一通过这个 helper 调用 add_argument。
def _add_argument_specs(
    parser_command: argparse.ArgumentParser,
    list_argument_specs: list[ArgumentSpec],
) -> None:
    """
    按声明式规格批量注册 CLI 参数。

    :param parser_command: 需要接收参数声明的命令解析器。
    :param list_argument_specs: 待展开的 ``add_argument`` 规格列表。
    :return: 无业务返回值；函数会直接修改传入的解析器对象。
    """

    # 逐项把 flags 和 kwargs 透传给 argparse。
    for tuple_flags, dict_kwargs in list_argument_specs:

        # 这里保持 add_argument 的原生语义，不额外重写 argparse 行为。
        parser_command.add_argument(*tuple_flags, **dict_kwargs)

# 为多个子命令复用统一的 HLS-only target 参数。
def _add_only_hls_target_argument(parser_command: argparse.ArgumentParser) -> None:
    """
    给子命令注册统一的 HLS-only target 参数。

    :param parser_command: 需要挂载 ``--target`` 参数的命令解析器。
    :return: 无业务返回值；函数会直接修改传入的解析器对象。
    """

    # target 当前只允许 hls，仍保留参数以兼容既有调用协议。
    parser_command.add_argument(
        "--target",
        default="hls",
        choices=ONLY_HLS_TARGET,
    )

# 把 flags 与 kwargs 组合成批量参数注册可复用的规格对象。
def _argument_spec(*tuple_flags: str, **dict_kwargs: Any) -> ArgumentSpec:
    """
    创建单个 ``add_argument`` 规格。

    :param tuple_flags: 传给 ``add_argument`` 的位置参数标记序列。
    :param dict_kwargs: 传给 ``add_argument`` 的关键字参数。
    :return: 可被 ``_add_argument_specs`` 展开的参数规格二元组。
    """

    # 返回最小二元组结构，供 _add_argument_specs 顺序展开。
    return tuple_flags, dict_kwargs

# 读取一个必须存在的 JSON 文件并返回解析后的对象。
def _read_json(path_json: Path) -> dict[str, Any]:
    """
    读取并解析 JSON 文件。

    :param path_json: 需要读取的 JSON 文件路径。
    :return: 解析后的 JSON 对象。
    """

    # 路径仍需通过工作区边界与存在性检查。
    path_resolved = require_workspace_path(  # 已校验的 JSON 文件路径
        path_json,  # CLI 或调用方传入的 JSON 文件路径
        purpose="JSON path",  # 标记这是必须存在的 JSON 输入文件
        must_exist=True,  # JSON 输入文件必须已经存在
    )

    # 读取 UTF-8 文本并解析成 JSON 对象。
    return json.loads(path_resolved.read_text(encoding="utf-8"))

# 读取可选 JSON 文件；未提供路径时返回 None。
def _read_optional_json(path_json: Path | None) -> dict[str, Any] | None:
    """
    读取可选 JSON 文件。

    :param path_json: 可选 JSON 文件路径。
    :return: 提供路径时返回解析后的 JSON 对象，否则返回 ``None``。
    """

    # 未提供路径时不做任何读取。
    if path_json is None:

        # 保持可选配置的空值语义。
        return None

    # 非空路径统一复用必填 JSON 读取逻辑。
    return _read_json(path_json)

# 读取可选工作区路径；未提供路径时返回 None。
def _read_optional_workspace_path(
    path_value: Path | None,
    *,
    purpose: str,
) -> Path | None:
    """
    读取可选工作区路径。

    :param path_value: 可选路径值。
    :param purpose: 传给工作区路径校验器的用途说明。
    :return: 已校验的路径对象；未提供时返回 ``None``。
    """

    # 未提供路径时保留空值，交由调用方决定是否启用相关能力。
    if path_value is None:

        # 空 baseline/reference 等场景无需再做路径校验。
        return None

    # 非空路径统一通过工作区边界与存在性检查。
    return require_workspace_path(
        path_value,
        purpose=purpose,
        must_exist=True,
    )

# 将 auto/en/zh 注释语言请求解析成最终显式值。
def _require_resolved_comment_language(comment_language: str) -> str:
    """
    解析并校验最终注释语言。

    :param comment_language: CLI 传入的注释语言请求值。
    :return: 最终解析得到的显式注释语言。
    :raises ValueError: 当 CLI 与用户配置都未给出有效语言时抛出。
    """

    # 用户级默认值与 CLI 显式值会在这里统一折叠。
    str_resolved_language = resolve_comment_language(  # 最终解析出的注释语言
        comment_language,  # CLI 或用户配置提供的语言请求值
    )

    # 解析失败说明既没有显式值，也没有用户级默认值。
    if str_resolved_language is None:

        # 错误消息需要明确告诉用户两种修复方式。
        raise ValueError(
            "> ERR: [Python] Comment language is not configured. Choose `en` or `zh` with "
            "`python -m scripts.python.cli.hls_generator user-config "
            "--set-comment-language <en|zh>`, or pass "
            "--comment-language en|zh.",
        )

    # 返回已经解析完成的显式语言值。
    return str_resolved_language

# 在真正执行需要外部技能的命令前，先统一检查依赖状态。
def _require_dependencies_for_use(
    request_dir: Path | None,
    *,
    scopes: set[str],
) -> None:
    """
    校验所需技能依赖；必要时生成依赖请求文件。

    :param request_dir: 可选请求目录；提供时会尝试写出依赖请求 JSON。
    :param scopes: 当前命令需要的依赖能力域集合。
    :return: 无业务返回值；依赖满足时静默返回。
    :raises ValueError: 当所需技能依赖缺失或被阻断时抛出。
    """

    # 先生成当前 scope 的依赖检查报告。
    dict_dependency_report = check_skill_dependencies(  # 当前 scope 的依赖检查报告
        skill_dependencies_config(),  # 运行时按当前 scope 读取的依赖配置源
        scopes=scopes,  # 当前命令请求的依赖能力域集合
    )

    # 未被阻断时无需生成请求文件，直接继续执行。
    if dict_dependency_report["status"] != BLOCKED_DEPENDENCY:

        # 当前依赖状态允许继续使用相关能力。
        return

    # 提供 request_dir 时，优先把依赖请求文件写到可见位置。
    if request_dir is not None:

        # 依赖请求文件写入属于可失败的辅助步骤，需要与主阻断结论分开处理。
        try:

            # 依赖请求路径仍需通过工作区输出边界检查。
            path_request = require_configured_output_path(  # 自动生成的依赖请求文件路径
                request_dir / "skill_dependency_request.json",  # 默认落盘的依赖请求文件名
                purpose="dependency request output path",  # 标记这是自动依赖请求文件输出路径
            )

            # 创建父目录并落盘依赖请求 JSON。
            path_request.parent.mkdir(parents=True, exist_ok=True)

            # 把自动生成的依赖请求 JSON 写到请求目录中。
            path_request.write_text(
                json.dumps(
                    build_dependency_request(dict_dependency_report),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

        # 依赖请求文件写入失败不应吞掉原始阻断结论。
        except (OSError, ValueError):

            # 无法写请求文件时保持沉默，后续仍通过异常把阻断状态暴露给调用方。
            pass

    # 依赖被阻断时，统一抛出可静态验证的错误文本给 CLI 主入口处理。
    raise ValueError(
        "> ERR: [Python] Required skill dependencies are blocked. "
        "Run `python -m scripts.python.cli.hls_generator deps request --out <path>` "
        "or inspect the generated dependency request file."
    )

# 模块以 `python -m scripts.python.cli.hls_generator.cli` 方式运行时走统一入口。
if __name__ == "__main__":

    # SystemExit 让 main() 的整数退出码正确传回外层 shell。
    raise SystemExit(main())
