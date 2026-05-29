#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from _skill_tool_delegate import agents_md_generator_script, run_delegate, run_delegate_retrying_transient_fs


TOP_LEVEL_EXCLUDES = {
    "AGENTS.md",
    "_smoke_runs",
    "reports",
    "workflow-state.json",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

_SANITIZE_RELEASE_TEXT = None


def _run(script: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _git(project: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )


def _print_completed(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)


def _load_json(text: str) -> dict | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_package_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("project", nargs="?", default=".")
    parser.add_argument("--version", required=True)
    parser.add_argument("--skill-dir", required=True)
    return parser.parse_args(argv[1:])


def _is_release_member(path: Path, prefix: Path, *, receipt_name: str | None = None) -> bool:
    if not path.is_file():
        return False
    relative = path.relative_to(prefix).as_posix()
    parts = relative.split("/")
    if relative == "AGENTS.md" or ".git" in parts or "__pycache__" in parts or relative.endswith(".pyc"):
        return False
    if receipt_name and relative == receipt_name:
        return False
    if parts and parts[0] in TOP_LEVEL_EXCLUDES:
        return False
    return True


def _source_release_map(skill_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for top in sorted(skill_dir.iterdir(), key=lambda item: item.name.lower()):
        if top.name in TOP_LEVEL_EXCLUDES:
            continue
        walk = [top] if top.is_file() else top.rglob("*")
        for path in walk:
            if _is_release_member(path, skill_dir):
                files[path.relative_to(skill_dir).as_posix()] = path
    return files


def _release_file_list(root: Path, *, receipt_name: str | None = None) -> list[str]:
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if _is_release_member(path, root, receipt_name=receipt_name):
            files.append(path.relative_to(root).as_posix())
    return files


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_release_manifest(release_dir: Path, *, receipt_name: str) -> list[dict[str, str]]:
    return [
        {"path": relative, "sha256": _sha256_file(release_dir / relative)}
        for relative in _release_file_list(release_dir, receipt_name=receipt_name)
    ]


def _sanitize_release_text(text: str) -> tuple[str, list[dict[str, str]]]:
    global _SANITIZE_RELEASE_TEXT
    if _SANITIZE_RELEASE_TEXT is None:
        scripts_dir = agents_md_generator_script("manage_docs.py").parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from manage_docs_release import sanitize_release_text as imported  # type: ignore

        _SANITIZE_RELEASE_TEXT = imported
    return _SANITIZE_RELEASE_TEXT(text)


def _prune_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue


def _repair_release_tree(skill_dir: Path, release_dir: Path, *, receipt_name: str) -> bool:
    expected = _source_release_map(skill_dir)
    actual = set(_release_file_list(release_dir, receipt_name=receipt_name))
    changed = False

    for relative in sorted(set(expected) - actual):
        source = expected[relative]
        target = release_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        changed = True

    for relative in sorted(actual - set(expected), reverse=True):
        target = release_dir / relative
        if target.exists():
            target.unlink()
            changed = True

    if changed:
        _prune_empty_dirs(release_dir)
    return changed


def _rewrite_release_zip(release_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(release_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=path.relative_to(release_dir).as_posix())


def _refresh_receipt(receipt_path: Path, skill_dir: Path, release_dir: Path) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError(f"Invalid release receipt JSON: {receipt_path}")
    receipt_name = receipt_path.name
    receipt["generated_at"] = datetime.now().isoformat(timespec="seconds")
    receipt["files"] = _build_release_manifest(release_dir, receipt_name=receipt_name)
    sanitization = receipt.get("sanitization")
    if isinstance(sanitization, dict):
        rebuilt: list[dict[str, object]] = []
        for relative, source_path in _source_release_map(skill_dir).items():
            release_path = release_dir / relative
            if not release_path.is_file():
                continue
            source_bytes = source_path.read_bytes()
            if b"\x00" in source_bytes:
                continue
            source_text = source_bytes.decode("utf-8")
            _, matches = _sanitize_release_text(source_text)
            if not matches:
                continue
            rebuilt.append(
                {
                    "path": relative,
                    "rules": sorted({item["rule"] for item in matches}),
                    "placeholders": sorted({item["placeholder"] for item in matches}),
                    "sha256": _sha256_file(release_path),
                }
            )
        sanitization["files"] = rebuilt
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")


def _stage_and_commit_dist(project: Path, skill_name: str, version: str, *, repair_only: bool) -> None:
    add_result = _git(project, ["add", "-f", "--all", "--", "dist"])
    if add_result.returncode != 0:
        raise RuntimeError(
            "package release failed to stage dist artifacts even after forced git add -f: "
            + (add_result.stderr or add_result.stdout).strip()
        )
    diff_cached = _git(project, ["diff", "--cached", "--quiet"])
    if diff_cached.returncode == 1:
        message = (
            f"package-release: repair parity for {skill_name} {version}"
            if repair_only
            else f"package-release: {skill_name} {version}"
        )
        commit_result = _git(project, ["commit", "-m", message])
        if commit_result.returncode != 0:
            raise RuntimeError(
                "package release failed to commit dist artifacts: "
                + (commit_result.stderr or commit_result.stdout).strip()
            )
    elif diff_cached.returncode not in {0, 1}:
        raise RuntimeError("package release could not inspect staged release artifacts")


def _run_post_gate(script: Path, project: Path, version: str, skill_dir: str) -> tuple[int, dict]:
    post = _run(
        script,
        [
            "release-gate",
            str(project),
            "--version",
            version,
            "--skill-dir",
            skill_dir,
            "--phase",
            "post",
        ],
    )
    return post.returncode, (_load_json(post.stdout or "") or {})


def _package_release_with_repo_fixes(script: Path, argv: list[str]) -> int:
    parsed = _parse_package_args(argv)
    project = Path(parsed.project).resolve()
    skill_dir = (project / parsed.skill_dir).resolve() if not Path(parsed.skill_dir).is_absolute() else Path(parsed.skill_dir).resolve()
    skill_name = skill_dir.name
    release_dir = project / "dist" / f"{skill_name}-{parsed.version}"
    zip_path = project / "dist" / f"{skill_name}-{parsed.version}.zip"
    receipt_path = release_dir / "RELEASE_RECEIPT.json"

    first = _run(script, argv)
    payload = _load_json(first.stdout or "")
    errors = payload.get("errors", []) if isinstance(payload, dict) else []
    stage_only_failure = first.returncode != 0 and "package release failed to stage dist artifacts" in errors
    if first.returncode != 0 and not stage_only_failure:
        _print_completed(first)
        return int(first.returncode)

    if not receipt_path.is_file():
        _print_completed(first)
        return int(first.returncode)

    parity_repaired = _repair_release_tree(skill_dir, release_dir, receipt_name=receipt_path.name)
    _refresh_receipt(receipt_path, skill_dir, release_dir)
    _rewrite_release_zip(release_dir, zip_path)

    try:
        if stage_only_failure:
            _stage_and_commit_dist(project, skill_name, parsed.version, repair_only=parity_repaired)
        else:
            _stage_and_commit_dist(project, skill_name, parsed.version, repair_only=True)
    except RuntimeError as exc:
        failure = {
            "ok": False,
            "errors": [str(exc)],
            "pre_gate": payload.get("pre_gate") if isinstance(payload, dict) else None,
        }
        print(json.dumps(failure, indent=2, ensure_ascii=False))
        return 1

    post_rc, post_payload = _run_post_gate(script, project, parsed.version, parsed.skill_dir)
    result = {
        "ok": post_rc == 0 and not post_payload.get("errors"),
        "errors": post_payload.get("errors", []),
        "release_dir": f"dist/{skill_name}-{parsed.version}",
        "release_zip": f"dist/{skill_name}-{parsed.version}.zip",
        "receipt_path": f"dist/{skill_name}-{parsed.version}/RELEASE_RECEIPT.json",
        "pre_gate": payload.get("pre_gate") if isinstance(payload, dict) else None,
        "post_gate": post_payload,
        "forced_stage": stage_only_failure,
        "parity_repaired": parity_repaired,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    script = agents_md_generator_script("manage_docs.py")
    if args[:1] == ["package-release"]:
        raise SystemExit(_package_release_with_repo_fixes(script, args))
    raise SystemExit(run_delegate_retrying_transient_fs(script))
