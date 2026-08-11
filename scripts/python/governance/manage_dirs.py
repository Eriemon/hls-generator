#!/usr/bin/env python3
"""委托 agents-md-generator 执行目录治理脚本。"""

# 启用后续类型标注所需的解释器特性。
from __future__ import annotations

# 阻止当前包装层进程在源码树里回写 __pycache__。
import sys

# 当前治理包装层不应制造新的 Python 字节码缓存。
sys.dont_write_bytecode = True  # 当前治理包装层禁写 Python 字节码缓存

# 复用统一委托入口定位外部治理脚本。
from skill_tool_delegate import agents_md_generator_script, run_delegate

# 仅在脚本直接运行时转交给实际治理脚本。
if __name__ == "__main__":

    # 把退出码原样透传给 shell，保持外部治理脚本的结果语义。
    raise SystemExit(run_delegate(agents_md_generator_script("manage_dirs.py")))
