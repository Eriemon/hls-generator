#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


GOVERNANCE_DIR = Path(__file__).resolve().parents[1] / "governance"
if str(GOVERNANCE_DIR) not in sys.path:
    sys.path.insert(0, str(GOVERNANCE_DIR))

from _skill_tool_delegate import agents_md_generator_script, run_delegate


if __name__ == "__main__":
    raise SystemExit(run_delegate(agents_md_generator_script("inspect_project.py")))
