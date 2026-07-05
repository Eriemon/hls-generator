#!/usr/bin/env python3
"""委托 agents-md-generator 执行技能审计脚本。"""

# 启用后续类型标注所需的解释器特性。
from __future__ import annotations

# 复用统一委托入口定位外部治理脚本。
from _skill_tool_delegate import agents_md_generator_script, run_delegate

# 仅在脚本直接运行时转交给实际治理脚本。
if __name__ == "__main__":

    # 把退出码原样透传给 shell，保持外部治理脚本的结果语义。
    raise SystemExit(run_delegate(agents_md_generator_script("audit_skill.py")))
