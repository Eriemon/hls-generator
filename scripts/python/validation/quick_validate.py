#!/usr/bin/env python3
"""委托 skill-creator 执行快速验证脚本。"""

# 启用后续类型标注所需的解释器特性。
from __future__ import annotations

# 导入 CLI 委托运行所需的标准库能力。
import sys
from pathlib import Path

# 委托 wrapper 通过相邻治理目录定位共享委托模块。
GOVERNANCE_DIR = Path(__file__).resolve().parents[1] / "governance"  # 委托模块所在目录

# 脚本入口集中处理路径扩展和委托调用。
def main() -> int:
    """运行快速验证委托脚本并返回进程退出码。

    参数:
        无显式业务参数；CLI 参数由被委托脚本自行处理。

    返回:
        返回被委托验证脚本的进程退出码。
    """

    # 仅在 CLI 执行期扩展搜索路径，避免模块导入时修改全局 sys.path。
    str_governance_dir = str(GOVERNANCE_DIR)  # 委托模块搜索路径

    # 委托模块位于相邻 governance 目录，需要运行期加入搜索路径。
    if str_governance_dir not in sys.path:

        # 入口函数内的路径变更不会影响导入该模块的调用方。
        sys.path.insert(0, str_governance_dir)

    # 路径准备完成后再导入委托模块。
    from _skill_tool_delegate import run_delegate, skill_creator_script

    # 转交给 skill-creator 的快速验证脚本。
    return run_delegate(skill_creator_script("quick_validate.py"))

# 仅在脚本直接运行时转交给实际验证脚本。
if __name__ == "__main__":

    # 把退出码原样透传给 shell，保持委托脚本的结果语义。
    raise SystemExit(main())
