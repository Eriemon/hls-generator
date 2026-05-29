#!/usr/bin/env python3
"""Delegate local wrapper commands to installed Codex skill scripts."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def _codex_home() -> Path:
    return Path.home() / ".codex"


def agents_md_generator_script(name: str) -> Path:
    return _codex_home() / "skills" / "agents-md-generator" / "scripts" / name


def skill_creator_script(name: str) -> Path:
    return _codex_home() / "skills" / ".system" / "skill-creator" / "scripts" / name


def run_delegate(script_path: Path, argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not script_path.exists():
        print(f"Delegated script not found: {script_path}", file=sys.stderr)
        return 2
    result = subprocess.run([sys.executable, str(script_path), *args], check=False)
    return int(result.returncode)


def run_delegate_retrying_transient_fs(
    script_path: Path,
    argv: list[str] | None = None,
    *,
    retries: int = 3,
    delay_s: float = 0.5,
) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not script_path.exists():
        print(f"Delegated script not found: {script_path}", file=sys.stderr)
        return 2
    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            [sys.executable, str(script_path), *args],
            check=False,
            capture_output=True,
            text=True,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        transient_missing_path = "FileNotFoundError" in stderr and (
            "No such file or directory" in stderr or "系统找不到指定的路径" in stderr
        )
        if result.returncode == 0 or not transient_missing_path or attempt == attempts:
            if stdout:
                print(stdout, end="")
            if stderr:
                print(stderr, end="", file=sys.stderr)
            return int(result.returncode)
        time.sleep(delay_s)
    return 1
