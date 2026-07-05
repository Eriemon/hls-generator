"""提供可直接运行的质量门 CLI 包装入口。"""

# 启用后续类型标注所需的解释器特性
from __future__ import annotations

# 导入当前模块运行所需的依赖
import sys
from pathlib import Path

# 直接运行包装脚本时，需要先把 skill 根目录放进 import 搜索路径。
def configure_import_path() -> None:
    """配置直接运行质量门包装脚本所需的导入路径。

    参数:
        无外部业务参数。

    返回:
        无业务返回值；函数只在脚本直跑时修正导入搜索路径。
    """

    # 当前文件位于 scripts/python/quality_gate，向上三层就是 skill 根。
    path_skill_root = Path(__file__).resolve().parents[3]  # skill 根目录路径

    # sys.path 使用字符串路径，避免 Path 对象和已有项比较失效。
    str_skill_root_text = str(path_skill_root)  # skill 根目录字符串路径

    # 只有缺失时才插入，避免重复改变模块解析优先级。
    if str_skill_root_text not in sys.path:

        # 放到最前面，确保导入的是当前工作区里的 skill 包。
        sys.path.insert(0, str_skill_root_text)

# 包装入口复用包内 CLI 主函数，只负责导入路径兼容。
def main(argv: list[str] | None = None) -> int:
    """组织命令行入口的参数解析、执行和退出码。

    参数:
        argv: 命令行入口接收的参数序列；为 None 时使用进程参数。

    返回:
        返回命令行入口使用的进程退出码。
    """

    # 先配置路径，再用绝对包名导入真正的 CLI 主函数。
    configure_import_path()

    # 导入当前模块运行所需的依赖
    from scripts.python.quality_gate.run import main as run_main

    # 退出码由真正的质量门 CLI 主函数计算。
    return run_main(argv)

# 脚本直接执行时启动 CLI 入口。
if __name__ == "__main__":

    # 直接透传 main 的退出码，便于上层脚本判断质量门执行结果。
    raise SystemExit(main())
