#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from remote_acceptance_common import PASS_STATUS, _new_run_dir, _reject_decode_noise, _write_report

def _run_link_mode(args: argparse.Namespace, config: dict[str, Any], helper: "ErieHelper", plan: list[str], topology: dict[str, Any]) -> dict[str, Any]:
    run_dir = _new_run_dir(config, "link")
    helper.preflight(topology["server"])
    output = helper.exec(topology["server"], list(config["link_probe_command"]))
    _reject_decode_noise(output)
    required = ("HLS_REMOTE_LINK_OK", "host=", "pwd=", "python=")
    missing = [item for item in required if item not in output]
    status = PASS_STATUS if not missing else FAILED_STATUS
    result = {
        "status": status,
        "mode": "link",
        "server": topology["server"],
        "topology": topology["topology"],
        "run_dir": str(run_dir),
        "steps": plan,
        "output": output,
        "missing_markers": missing,
        "uses_erie_remote_ssh": True,
    }
    _write_report(run_dir, result)
    return result
