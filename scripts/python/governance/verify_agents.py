#!/usr/bin/env python3
from __future__ import annotations

from _skill_tool_delegate import agents_md_generator_script, run_delegate_retrying_transient_fs


if __name__ == "__main__":
    raise SystemExit(run_delegate_retrying_transient_fs(agents_md_generator_script("verify_agents.py")))
