#!/usr/bin/env python3
from __future__ import annotations

from _skill_tool_delegate import agents_md_generator_script, run_delegate


if __name__ == "__main__":
    raise SystemExit(run_delegate(agents_md_generator_script("audit_skill.py")))
