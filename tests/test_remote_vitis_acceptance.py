from __future__ import annotations

import argparse
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "remote_vitis_acceptance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("remote_vitis_acceptance_test_module", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RemoteVitisAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()

    def test_planned_steps_keep_remote_artifacts_by_default(self) -> None:
        steps = self.module._planned_steps(
            "vitis",
            "server-a",
            "profile-a",
            "cosim",
            cleanup_remote=False,
            example_spec="hls_vector_scale_mock_spec.json",
        )

        self.assertIn("retain remote validation directory", steps)
        self.assertNotIn("erie request delete cleanup", steps)

    def test_planned_steps_include_split_validation_phases(self) -> None:
        steps = self.module._planned_steps(
            "vitis",
            "build-server",
            "profile-a",
            "cosim",
            cleanup_remote=False,
            example_spec="hls_vector_scale_mock_spec.json",
            validate_server="validate-server",
            topology="split_build_validate",
        )

        self.assertIn("erie check build-server", steps)
        self.assertIn("erie workspace-check validate-server", steps)
        self.assertIn("erie request command validation Vitis cosim", steps)

    def test_resolve_topology_accepts_split_server_inputs(self) -> None:
        args = argparse.Namespace(server=None, build_server="build-a", validate_server="validate-b")

        topology = self.module._resolve_topology(args)

        self.assertEqual(topology["topology"], "split_build_validate")
        self.assertEqual(topology["build_server"], "build-a")
        self.assertEqual(topology["validate_server"], "validate-b")

    def test_select_split_version_prefers_lowest_shared_version(self) -> None:
        build_candidates = [
            {"version": "2022.2", "settings_script": "/build/2022.2/settings64.sh", "expected_tool": "vitis_hls"},
            {"version": "2023.2", "settings_script": "/build/2023.2/settings64.sh", "expected_tool": "vitis_hls"},
        ]
        validate_candidates = [
            {"version": "2022.2", "settings_script": "/validate/2022.2/settings64.sh", "expected_tool": "vitis_hls"},
            {"version": "2023.2", "settings_script": "/validate/2023.2/settings64.sh", "expected_tool": "vitis_hls"},
        ]
        args = argparse.Namespace(vitis_version=None)

        version = self.module._select_shared_vitis_version(args, build_candidates, validate_candidates)

        self.assertEqual(version, "2022.2")

    def test_select_vitis_profile_blocks_when_multiple_versions_need_choice(self) -> None:
        args = argparse.Namespace(
            server="server-a",
            profile="configured_profile",
            readiness="cosim",
            example_spec="hls_vector_scale_mock_spec.json",
            vitis_version=None,
        )
        candidates = [
            {"version": "2022.1", "settings_script": "/user/configured/vitis-2022.1/settings64.sh", "expected_tool": "vitis_hls"},
            {"version": "2022.2", "settings_script": "/user/configured/vitis-2022.2/settings64.sh", "expected_tool": "vitis_hls"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with patch.object(self.module, "get_vitis_selection", return_value=None):
                result = self.module._select_vitis_profile(args, run_dir, candidates, {"settings_script": "/user/configured/fallback/settings64.sh", "expected_tool": "vitis_hls"})
            self.assertTrue(Path(result["remote_vitis_version_request"]).exists())

        self.assertEqual(result["status"], self.module.BLOCKED_VERSION_STATUS)
        self.assertEqual(len(result["candidate_versions"]), 2)

    def test_resolve_profile_config_blocks_when_user_must_provide_settings(self) -> None:
        args = argparse.Namespace(
            server="server-a",
            profile=None,
            readiness="cosim",
            example_spec="hls_vector_scale_mock_spec.json",
            vitis_version=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with patch.object(self.module, "get_vitis_selection", return_value=None):
                result = self.module._resolve_profile_config(
                    args,
                    run_dir,
                    candidates=[],
                    configured_profiles={},
                    required_fields=("settings_script", "expected_tool", "target_part"),
                )

        self.assertEqual(result["status"], self.module.BLOCKED_PROFILE_STATUS)
        self.assertEqual(result["missing_fields"], ["settings_script", "expected_tool", "target_part"])

    def test_select_vitis_profile_persists_explicit_version(self) -> None:
        args = argparse.Namespace(
            server="server-a",
            profile="configured_profile",
            readiness="cosim",
            example_spec="hls_vector_scale_mock_spec.json",
            vitis_version="2022.2",
        )
        candidate = {
            "version": "2022.2",
            "settings_script": "/user/configured/settings64.sh",
            "expected_tool": "vitis_hls",
            "target_part": "user-configured-part",
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with patch.object(self.module, "set_vitis_selection") as set_selection:
                result = self.module._select_vitis_profile(args, run_dir, [candidate], {"settings_script": "/user/configured/fallback/settings64.sh", "expected_tool": "vitis_hls"})

        self.assertEqual(result["version"], "2022.2")
        set_selection.assert_called_once()

    def test_wait_for_job_accepts_failed_status_with_nonzero_returncode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp)
            script = skill_dir / "scripts" / "remote_ssh.py"
            settings = skill_dir / "config" / "defaults.json"
            script.parent.mkdir(parents=True)
            settings.parent.mkdir(parents=True)
            script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            settings.write_text("{}\n", encoding="utf-8")
            helper = self.module.ErieHelper(
                {
                    "erie_skill_dir": str(skill_dir),
                    "erie_settings_path": str(settings),
                    "python_env": {},
                },
                timeout=20,
            )
            status_result = subprocess.CompletedProcess(
                ["python", "remote_ssh.py", "status"],
                7,
                "status: failed\nexit_code: 7\n",
                "",
            )

            with patch.object(self.module.subprocess, "run", return_value=status_result):
                result = helper.wait_for_job("server-a", "job-1", poll_s=0, max_wait_s=1)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["returncode"], 7)
        self.assertIn("exit_code: 7", result["output"])

    def test_run_server_vitis_phase_reports_status_output_and_tail_log(self) -> None:
        helper = Mock()
        helper.timeout = 120
        helper.request_and_run.return_value = "mkdir-request"
        helper.exec_detached.return_value = {"job_id": "job-1", "manifest": "manifest-1"}
        helper.wait_for_job.return_value = {
            "status": "failed",
            "output": "status: failed\nexit_code: 7\n",
            "returncode": 7,
        }
        helper.tail_log.return_value = "tail line 1\ntail line 2"

        with tempfile.TemporaryDirectory() as tmp:
            package_path = Path(tmp) / "artifacts.tar.gz"
            package_path.write_bytes(b"demo")
            with patch.object(self.module, "_transfer_package_by_request_commands", return_value=["upload-request"]):
                with patch.object(self.module, "_remote_vitis_command", return_value="run-command"):
                    with self.assertRaises(self.module.RemoteAcceptanceError) as exc:
                        self.module._run_server_vitis_phase(
                            helper,
                            Path(tmp),
                            "server-a",
                            {"version": "2022.2", "target_part": "part-a", "settings_script": "/opt/vitis/settings64.sh", "expected_tool": "vitis_hls"},
                            "cosim",
                            package_path,
                            {"remote_tmp_dir": ".remote"},
                            Path(tmp) / "run-dir",
                            phase_label="build",
                            cleanup_remote=False,
                            remote_workdir="/home/test/workspace",
                        )

        message = str(exc.exception)
        self.assertIn("status: failed", message)
        self.assertIn("exit_code: 7", message)
        self.assertIn("tail line 1", message)

    def test_probe_remote_workdir_uses_last_nonempty_line(self) -> None:
        helper = Mock()
        helper.exec.return_value = "\n/home/test/workspace\n"

        workdir = self.module._probe_remote_workdir("server-a", Path("settings.json"), helper)

        self.assertEqual(workdir, "/home/test/workspace")


if __name__ == "__main__":
    unittest.main()
