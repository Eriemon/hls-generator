"""使用临时 pycache 前缀执行 compileall，避免把缓存写回目标目录。"""

# 启用后续类型标注所需的解释器特性。
from __future__ import annotations

# 导入命令行、语法编译和临时目录能力。
import argparse
import compileall
import sys
import tempfile
from pathlib import Path

# 构造无缓存 compileall 包装器的参数解析器。
def build_parser() -> argparse.ArgumentParser:
    """构造无缓存 compileall 包装器的命令行参数。

    参数:
        本函数不接收外部业务参数。

    返回:
        已声明一个或多个 compileall 目标参数的解析器。
    """

    # 先定义参数解析器描述文本，便于复用和追加中文右侧用途注释。
    str_description = "Run compileall with an external pycache prefix."  # 无缓存 compileall CLI 描述文本

    # 解析一个或多个待做语法编译检查的 Python 目标。
    parser = argparse.ArgumentParser(description=str_description)  # 无缓存 compileall 参数解析器

    # 位置参数允许传入文件或目录，兼容 skill 的推荐命令和治理门禁。
    parser.add_argument("targets", nargs="+", help="Python files or directories to compile")

    # 返回已经声明完位置参数的解析器。
    return parser

# 校验命令行传入的目标路径都真实存在。
def resolve_targets(raw_targets: list[str]) -> list[Path]:
    """把命令行目标解析成存在的路径列表。

    参数:
        raw_targets: 命令行传入的文件或目录目标字符串列表。

    返回:
        已通过存在性校验的路径列表。

    异常:
        FileNotFoundError: 任一目标不存在时抛出。
    """

    # 保存已经过存在性校验的目标路径。
    list_targets: list[Path] = []  # compileall 目标路径列表

    # 逐个校验目标路径，避免把缺失路径交给 compileall 后才产生含糊报错。
    for str_raw_target in raw_targets:

        # 把字符串参数转成 pathlib 路径，统一后续文件和目录分支。
        path_target = Path(str_raw_target)  # 当前 compileall 目标路径

        # 缺失路径直接作为命令失败处理，和 compileall 失败语义保持一致。
        if not path_target.exists():

            # 暴露缺失目标，避免后续 compileall 报出更含糊的失败原因。
            raise FileNotFoundError(f"> ERR: [Python] No such file or directory: {path_target}")

        # 记录已经通过校验的目标路径。
        list_targets.append(path_target)

    # 返回稳定顺序的目标路径列表。
    return list_targets

# 对单个文件或目录执行 compileall 语法编译检查。
def compile_target(path_target: Path) -> bool:
    """对单个目标执行 compileall，并返回是否通过。

    参数:
        path_target: 当前要做语法编译检查的文件或目录目标。

    返回:
        当前目标语法编译成功时返回 True，否则返回 False。
    """

    # 目录目标沿用 compileall 的递归目录检查能力。
    if path_target.is_dir():

        # 返回目录目标的递归语法编译结果。
        return compileall.compile_dir(str(path_target), quiet=1)

    # 返回单文件目标的语法编译结果，避免误扫兄弟目录。
    return compileall.compile_file(str(path_target), quiet=1)

# 在仓库外临时 pycache 前缀下运行 compileall。
def main(argv: list[str] | None = None) -> int:
    """执行无缓存 compileall 包装流程，并返回标准进程退出码。

    参数:
        argv: 可选命令行参数列表；为 None 时改用解释器传入参数。

    返回:
        全部目标语法编译成功时返回 0，否则返回 1。
    """

    # 先构造解析器，再统一进入目标校验和 compileall 流程。
    parser = build_parser()  # 当前 CLI 参数解析器

    # 解析调用方传入的命令行参数。
    args = parser.parse_args(argv)  # 解析后的命令行参数

    # 先解析并校验所有待编译目标路径，避免部分成功后才发现参数错误。
    try:

        # 保存已经通过存在性校验的 compileall 目标列表。
        list_targets = resolve_targets(args.targets)  # 已解析的 compileall 目标列表

    # 缺失路径直接返回非零退出码，并输出稳定错误文本。
    except FileNotFoundError as obj_error:

        # 按标准 argparse 失败出口返回错误消息，便于调用方捕获 stderr。
        parser.exit(status=1, message=f"{obj_error}\n")

    # 记录调用方原有 pycache 前缀，供 finally 恢复。
    str_original_pycache_prefix_or_none = sys.pycache_prefix  # 原始 pycache 前缀

    # 用仓库外临时目录承接 compileall 产物，避免污染 skill 和测试夹具目录。
    with tempfile.TemporaryDirectory(prefix="compileall-pycache-") as str_temp_dir:

        # 在执行 compileall 前切换到仓库外临时 pycache 前缀。
        try:

            # 当前进程只在临时目录下写 pyc，命令退出后由临时目录自动清理。
            sys.pycache_prefix = str(Path(str_temp_dir) / "pycache")  # compileall 临时 pycache 前缀

            # 初始化 compileall 总体通过状态，任一目标失败都要让最终退出码失败。
            bool_all_targets_passed = True  # compileall 总体是否通过

            # 逐个编译目标，保证目录和单文件都能得到稳定退出码。
            for path_target in list_targets:

                # 合并当前目标的 compileall 结果，任一失败都保留失败状态。
                bool_all_targets_passed = compile_target(path_target) and bool_all_targets_passed  # 叠加当前目标的 compileall 结果

        # 无论编译是否成功，都要恢复调用方原本的 pycache 前缀。
        finally:

            # 恢复调用方原本的 pycache 前缀，避免影响后续脚本流程。
            sys.pycache_prefix = str_original_pycache_prefix_or_none  # 恢复调用方原本的 pycache 前缀

    # 返回 compileall 的最终退出码。
    return 0 if bool_all_targets_passed else 1

# 允许以脚本方式直接执行当前无缓存 compileall 包装器。
if __name__ == "__main__":

    # 以标准 CLI 入口返回 compileall 包装器退出码。
    raise SystemExit(main())
