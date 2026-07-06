"""运行 HLS 任务分类器的命令行包装器。

本模块为上层治理脚本提供一个轻量 CLI：默认输出带项目统一前缀的人类可读摘要；
当调用方显式传入 ``--json`` 时，机器可读 stdout 协议: json。
"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 标准库依赖用于参数解析、JSON 输出和导入路径兼容。
import argparse
import json
import sys
from pathlib import Path

# 修正脚本直跑时的模块搜索路径
def _configure_import_path() -> None:
    """把技能根目录加入模块搜索路径。

    参数:
        无外部业务参数。

    返回:
        无业务返回值；函数只在必要时更新 ``sys.path``。
    """

    # 根据当前脚本位置定位 erie-hls-generator 技能根目录
    path_skill_root = Path(__file__).resolve().parents[3]  # 技能包根目录路径

    # sys.path 中保存字符串路径，先规整成可比较文本
    str_root = str(path_skill_root)  # 技能包根目录文本

    # 缺少技能根目录时才插入，避免重复污染导入搜索顺序
    if str_root not in sys.path:

        # 把技能根目录放到最前面，保证脚本直跑时优先导入当前仓库里的 runtime 实现。
        sys.path.insert(0, str_root)

# 构建 HLS 调度器 CLI 参数
def build_parser() -> argparse.ArgumentParser:
    """构造 HLS 工作流调度器的命令行参数。

    参数:
        无外部业务参数。

    返回:
        已声明请求文本、目标路径、基线路径和 JSON 输出开关的解析器。
    """

    # argparse 解析器集中保存 CLI 协议和帮助文本
    parser = argparse.ArgumentParser(description="Classify an HLS task and show required readability gates.")  # CLI 参数解析器

    # 请求文本是 HLS 调度器判断 generate/modify/explain 的主输入
    parser.add_argument("request", help="Natural-language HLS request text.")

    # 目标路径用于已有 HLS 代码的修改或注释治理流程
    parser.add_argument("--target-path")

    # 基线路径用于 comment-only 场景的前后等价检查
    parser.add_argument("--baseline-path")

    # JSON 开关声明 stdout 将输出完整机器可读载荷
    parser.add_argument("--json", action="store_true")

    # 返回给 main 复用的参数解析器
    return parser

# 命令行入口执行分类并输出摘要
def main(argv: list[str] | None = None) -> int:
    """运行 HLS 任务分类 CLI。

    参数:
        argv: 可选命令行参数序列；为 None 时使用当前进程参数。

    返回:
        进程退出码；成功完成分类时返回 0。
    """

    # 先修正导入路径，保证直接执行脚本时可加载 runtime 分类器
    _configure_import_path()

    # 运行期导入避免路径修正之前解析项目包
    from scripts.python.task_dispatcher.hls_task_classifier import HlsDispatchDecision, classify_hls_task

    # 解析 CLI 参数，得到请求文本和可选路径边界
    args = build_parser().parse_args(argv)  # 命令行参数对象

    # 调度器根据请求文本和路径边界生成 HLS 执行模式与门禁建议。
    hls_dispatch_decision_result: HlsDispatchDecision = classify_hls_task(  # HLS 调度决策结果
        args.request,  # 用户提交的原始任务文本
        target_path=args.target_path,  # 待分流任务的目标路径
        baseline_path=args.baseline_path,  # 仅注释流程的基线路径
    )

    # JSON 模式是显式机器可读 stdout 协议
    if args.json:

        # 向自动化调用方写出单个 JSON 结果，保持显式协议 stdout 的稳定性。
        sys.stdout.write(json.dumps(hls_dispatch_decision_result.to_dict(), indent=2, ensure_ascii=False) + "\n")

    # 默认模式只保留面向人的短摘要，不再把结构化矩阵直接打印到终端。
    else:

        # 把模式提取成标量字符串，便于终端摘要直接复用。
        str_mode = hls_dispatch_decision_result.mode  # 当前请求匹配到的 HLS 工作流模式

        # 仅注释标记决定后续是否允许进入带语义改写风险的流程。
        bool_comment_only = hls_dispatch_decision_result.comment_only  # 当前请求是否限制为仅注释改写

        # 缺少目标时需要继续向上层流程索取路径或代码片段。
        bool_needs_target_or_code = hls_dispatch_decision_result.needs_target_or_code  # 当前请求是否缺目标上下文

        # 基线需求决定仅注释流程能否安全做语义对照。
        bool_baseline_required = hls_dispatch_decision_result.baseline_required  # 当前请求是否还缺 baseline

        # 输出分流模式，便于人工先确认当前请求会进入哪条工作流。
        print(f"> INFO: [Python] mode: {str_mode}")

        # 输出仅注释标记，提醒调用方是否需要附带基线保护。
        print(f"> INFO: [Python] comment_only: {bool_comment_only}")

        # 输出目标补齐状态，便于上层流程决定是否继续追问上下文。
        print(f"> INFO: [Python] needs_target_or_code: {bool_needs_target_or_code}")

        # 输出 baseline 需求，便于人工快速判断是否缺少安全闭环条件。
        print(f"> INFO: [Python] baseline_required: {bool_baseline_required}")

    # 分类流程成功完成时返回 0
    return 0

# 脚本直接执行时启动 CLI 入口
if __name__ == "__main__":

    # SystemExit 负责把 main 的返回码传给调用进程
    raise SystemExit(main())
