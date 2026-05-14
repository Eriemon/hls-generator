#!/usr/bin/env python3
"""Prepare a versioned erie-hls-generator release artifact."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
PACKAGE_NAME = "erie-hls-generator"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.hls_generator.config import skill_dependencies_config  # noqa: E402

EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".Xil",
    "__pycache__",
    "_smoke_runs",
    "dist",
    "ref",
    "reports",
    "temp",
    "tmp",
    "xsim.dir",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_FILE_NAMES = {"checksums.sha256"}
EXCLUDED_GLOBS = (
    "*.jou",
    "*.log",
    "*.str",
    ".hls_generator_*.tcl",
    ".hls_generator_vitis_*",
    "solution*",
)
VALIDATION_COMMANDS = [
    r"python .\skills\erie-hls-generator\smoke\run_smoke.py",
    r"python -m compileall .\skills\erie-hls-generator\runtime\hls_generator",
    r"python C:\Users\17677\.codex\skills\.system\skill-creator\scripts\quick_validate.py .\skills\erie-hls-generator",
    r"python .\skills\erie-hls-generator\scripts\confidence_loop.py --skip-remote --json-out reports\confidence-loop\latest-local.json",
]


class ReleaseError(RuntimeError):
    """Raised for release preparation failures."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a versioned erie-hls-generator release directory and zip.")
    parser.add_argument("--version", required=True, help="Explicit SemVer release version, for example 0.1.4.")
    parser.add_argument("--dist-root", type=Path, default=REPO_ROOT / "dist", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        payload = prepare_release(args.version, args.dist_root)
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def prepare_release(version: str, dist_root: Path) -> dict[str, object]:
    version = version.strip()
    if not SEMVER_RE.fullmatch(version):
        raise ReleaseError(f"Release version must be explicit SemVer X.Y.Z, got {version!r}.")
    source_version = _read_runtime_version()
    cli_version = _read_cli_version()
    if source_version != version:
        raise ReleaseError(f"runtime/hls_generator/__init__.py version {source_version!r} does not match release version {version!r}.")
    if cli_version != version:
        raise ReleaseError(f"hls-gen --version reported {cli_version!r}, expected {version!r}.")
    dependency_summary = _validate_skill_dependency_manifest()

    dist_root = _resolve_dist_root(dist_root)
    release_dir = dist_root / f"{PACKAGE_NAME}-v{version}"
    zip_path = dist_root / f"{PACKAGE_NAME}-v{version}.zip"
    _replace_release_outputs(release_dir, zip_path)

    included_files = _copy_skill_tree(release_dir)
    _validate_release_markdown(release_dir)
    manifest = {
        "version": version,
        "tag": f"v{version}",
        "package": PACKAGE_NAME,
        "source_commit": _optional_git_output(["rev-parse", "HEAD"]),
        "source_branch": _optional_git_output(["branch", "--show-current"]),
        "built_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "included_files": included_files,
        "excluded_paths": sorted(EXCLUDED_DIR_NAMES | set(EXCLUDED_GLOBS) | EXCLUDED_FILE_SUFFIXES),
        "validation_commands": VALIDATION_COMMANDS,
        "skill_dependencies": dependency_summary,
    }
    manifest_path = release_dir / "RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    checksum_entries = _write_checksums(release_dir)
    _write_zip(release_dir, zip_path)
    return {
        "version": version,
        "release_dir": str(release_dir),
        "zip_path": str(zip_path),
        "file_count": len(included_files) + 1,
        "checksum_count": len(checksum_entries),
        "source_commit": manifest["source_commit"],
        "skill_dependency_count": dependency_summary["count"],
    }


def _validate_skill_dependency_manifest() -> dict[str, object]:
    try:
        dependencies = skill_dependencies_config()
    except ValueError as exc:
        raise ReleaseError(f"Invalid skill dependency configuration: {exc}") from exc
    if not dependencies:
        raise ReleaseError("Release requires at least one configured skill dependency.")
    non_blocking = [item["id"] for item in dependencies if not item["blocking"]]
    if non_blocking:
        raise ReleaseError(f"All configured skill dependencies must be blocking: {', '.join(non_blocking)}")
    return {
        "count": len(dependencies),
        "ids": [item["id"] for item in dependencies],
        "blocking_policy": "all",
    }


def _read_runtime_version() -> str:
    init_path = SKILL_ROOT / "runtime" / "hls_generator" / "__init__.py"
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise ReleaseError(f"Could not find __version__ in {init_path}.")
    return match.group(1)


def _read_cli_version() -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SKILL_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "runtime.hls_generator", "--version"],
        cwd=SKILL_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(f"Could not read CLI version: {result.stderr.strip() or result.stdout.strip()}")
    match = re.search(r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)", result.stdout.strip())
    if not match:
        raise ReleaseError(f"Could not parse CLI version from {result.stdout.strip()!r}.")
    return match.group(1)


def _resolve_dist_root(path: Path) -> Path:
    candidate = path if path.is_absolute() else REPO_ROOT / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ReleaseError(f"dist root must stay inside the repository: {path}") from exc
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _replace_release_outputs(release_dir: Path, zip_path: Path) -> None:
    for path in (release_dir, zip_path):
        resolved = path.resolve()
        try:
            resolved.relative_to(REPO_ROOT.resolve())
        except ValueError as exc:
            raise ReleaseError(f"Refusing to replace release output outside repository: {path}") from exc
    if release_dir.exists():
        shutil.rmtree(release_dir)
    if zip_path.exists():
        zip_path.unlink()
    release_dir.mkdir(parents=True)


def _copy_skill_tree(release_dir: Path) -> list[str]:
    included: list[str] = []
    for src in sorted(_iter_release_files(SKILL_ROOT), key=lambda item: item.as_posix().lower()):
        rel_repo = src.relative_to(REPO_ROOT)
        dst = release_dir / rel_repo
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        included.append(rel_repo.as_posix())
    return included


def _validate_release_markdown(release_dir: Path) -> None:
    for path in sorted(release_dir.rglob("*.md"), key=lambda item: item.as_posix().lower()):
        data = path.read_bytes()
        rel = path.relative_to(release_dir).as_posix()
        if data.startswith(b"\xef\xbb\xbf"):
            raise ReleaseError(f"Release Markdown must not use a UTF-8 BOM: {rel}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseError(f"Release Markdown must be UTF-8: {rel}: {exc}") from exc
        if not text.isascii():
            raise ReleaseError(f"Release Markdown must be ASCII-only to avoid install metadata mojibake: {rel}")


def _iter_release_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if _is_excluded(rel, path):
            if path.is_dir():
                continue
            continue
        if path.is_file():
            yield path


def _is_excluded(rel: Path, path: Path) -> bool:
    if any(part in EXCLUDED_DIR_NAMES for part in rel.parts):
        return True
    if path.is_file() and path.suffix in EXCLUDED_FILE_SUFFIXES:
        return True
    if path.name in EXCLUDED_FILE_NAMES:
        return True
    return any(path.match(pattern) or rel.match(pattern) for pattern in EXCLUDED_GLOBS)


def _write_checksums(release_dir: Path) -> list[str]:
    entries: list[str] = []
    for path in sorted((item for item in release_dir.rglob("*") if item.is_file()), key=lambda item: item.as_posix().lower()):
        rel = path.relative_to(release_dir).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{digest}  {rel}")
    (release_dir / "checksums.sha256").write_text("\n".join(entries) + "\n", encoding="utf-8")
    return entries


def _write_zip(release_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((item for item in release_dir.rglob("*") if item.is_file()), key=lambda item: item.as_posix().lower()):
            archive.write(path, Path(release_dir.name) / path.relative_to(release_dir))


def _git_output(args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _optional_git_output(args: list[str]) -> str:
    try:
        value = _git_output(args)
    except ReleaseError:
        return "unavailable"
    return value or "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())
