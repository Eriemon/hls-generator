#!/usr/bin/env python3
"""Synchronize the current skill source tree into the local Codex installed copy."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

EXCLUDED_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "_smoke_runs",
    "reports",
    "workflow-state.json",
}


def skill_source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_destination() -> Path:
    return (Path.home() / ".codex" / "skills" / skill_source_root().name).resolve()


def default_backup_root(dest: Path) -> Path:
    return dest.parent.resolve()


def _ignore(_root: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_NAMES}


def sync_installed_skill_copy(source: Path, dest: Path, backup_root: Path) -> dict[str, object]:
    source = source.resolve()
    dest = dest.resolve()
    backup_root = backup_root.resolve()
    if not source.is_dir():
        raise ValueError(f"Source skill directory does not exist: {source}")
    if source == dest:
        raise ValueError("Source and destination skill directories must be different.")
    if dest.exists() and not dest.is_dir():
        raise ValueError(f"Destination exists but is not a directory: {dest}")

    backup_path = backup_root / f"{dest.name}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    copied = False
    if dest.exists():
        if backup_path.exists():
            raise ValueError(f"Backup path already exists: {backup_path}")
        shutil.move(str(dest), str(backup_path))
    shutil.copytree(source, dest, ignore=_ignore)
    copied = True
    return {
        "source": str(source),
        "destination": str(dest),
        "backup": str(backup_path) if backup_path.exists() else "",
        "copied": copied,
        "excluded_names": sorted(EXCLUDED_NAMES),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync the current erie-hls-generator source tree into the local Codex installed copy.")
    parser.add_argument("--source", default=str(skill_source_root()))
    parser.add_argument("--dest", default=str(default_destination()))
    parser.add_argument("--backup-root", default="")
    args = parser.parse_args(argv)

    source = Path(args.source)
    dest = Path(args.dest)
    backup_root = Path(args.backup_root) if str(args.backup_root).strip() else default_backup_root(dest)
    result = sync_installed_skill_copy(source, dest, backup_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
