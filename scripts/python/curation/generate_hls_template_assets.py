#!/usr/bin/env python3
"""兼容旧命令名称并转交给参考优化资产生成脚本。"""

# 启用后续类型标注所需的解释器特性。
from __future__ import annotations

# 导入运行旧脚本所需的标准库能力。
import runpy
from pathlib import Path

# 兼容入口只负责定位并运行旧资产生成实现。
def main() -> None:
    """执行旧入口对应的资产生成脚本。

    参数:
        无外部业务参数。

    返回:
        无业务返回值；函数只负责按脚本方式转交执行旧入口。
    """

    # 保留旧命令入口，实际实现集中在 generate_ref_opt_assets.py。
    path_legacy_script = Path(__file__).with_name("generate_ref_opt_assets.py")  # 实际资产生成脚本路径

    # 按脚本方式执行目标文件，保持原有 `__main__` 语义。
    runpy.run_path(str(path_legacy_script), run_name="__main__")

# 仅在脚本直接运行时触发兼容入口。
if __name__ == "__main__":

    # main 内部会按旧脚本语义转交执行。
    main()
