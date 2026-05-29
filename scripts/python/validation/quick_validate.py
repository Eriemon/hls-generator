#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


GOVERNANCE_DIR = Path(__file__).resolve().parents[1] / "governance"
if str(GOVERNANCE_DIR) not in sys.path:
    sys.path.insert(0, str(GOVERNANCE_DIR))

from _skill_tool_delegate import run_delegate, skill_creator_script


if __name__ == "__main__":
    raise SystemExit(run_delegate(skill_creator_script("quick_validate.py")))
