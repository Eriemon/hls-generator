"""Helpers for recovering prior remote validation runs."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath


def resolve_recovery_target(recover_run_id: str, recover_remote_run_dir: str) -> tuple[str, str]:
    run_id = str(recover_run_id or "").strip()
    remote_run_dir = str(recover_remote_run_dir or "").strip()
    if run_id and remote_run_dir:
        return run_id, remote_run_dir
    if run_id:
        return run_id, ""
    if remote_run_dir:
        return PurePosixPath(remote_run_dir.rstrip("/")).name, remote_run_dir
    raise ValueError("Recovery requires --recover-run-id or --recover-remote-run-dir.")


def recover_local_run_dir(local_run_root: Path, run_id: str) -> Path:
    candidate = local_run_root / run_id
    if candidate.is_dir():
        return candidate
    matches = sorted(path for path in local_run_root.glob(f"*{run_id}*") if path.is_dir())
    if matches:
        return matches[0]
    raise ValueError(f"Could not find local remote-validation run directory for {run_id!r}.")


def recover_example_spec(examples_dir: Path, local_run_dir: Path) -> str:
    spec_path = local_run_dir / "local-generation" / "_adapter_inputs" / "spec.json"
    if not spec_path.is_file():
        raise ValueError("Recovery needs --example-spec because the local run does not include _adapter_inputs/spec.json.")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec_name = str(spec.get("name") or "").strip()
    if not spec_name:
        raise ValueError("Recovery needs --example-spec because the local spec has no stable name.")
    for candidate in sorted(examples_dir.glob("*.json")):
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if str(payload.get("name") or "").strip() == spec_name:
            return candidate.name
    raise ValueError(f"Recovery could not map local spec name {spec_name!r} back to an example file. Pass --example-spec explicitly.")


def infer_target_part_from_platform_selection(profile: dict[str, object]) -> str:
    joined = " ".join(str(profile.get(key) or "").strip().lower() for key in ("platform_name", "remote_platform_root", "remote_xpfm"))
    if "u55c" in joined:
        return "xcu55c-fsvh2892-2L-e"
    if "u50" in joined:
        return "".join(("xcu", "50", "-fsvh2104-2-e"))
    return ""


def field_from_equals_output(output: str, key: str) -> str:
    prefix = f"{key}="
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.split(prefix, 1)[1].strip()
    return ""
