from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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

    def test_select_vitis_profile_blocks_when_multiple_versions_need_choice(self) -> None:
        args = argparse.Namespace(
            server="server-a",
            profile="vitis_2022",
            readiness="cosim",
            example_spec="hls_vector_scale_mock_spec.json",
            vitis_version=None,
        )
        candidates = [
            {"version": "2022.1", "settings_script": "/tools/Xilinx/Vitis/2022.1/settings64.sh", "expected_tool": "vitis_hls"},
            {"version": "2022.2", "settings_script": "/tools/Xilinx/Vitis/2022.2/settings64.sh", "expected_tool": "vitis_hls"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with patch.object(self.module, "get_vitis_selection", return_value=None):
                result = self.module._select_vitis_profile(args, run_dir, candidates, {"settings_script": "/fallback/settings64.sh", "expected_tool": "vitis_hls"})
            self.assertTrue(Path(result["remote_vitis_version_request"]).exists())

        self.assertEqual(result["status"], self.module.BLOCKED_VERSION_STATUS)
        self.assertEqual(len(result["candidate_versions"]), 2)

    def test_select_vitis_profile_persists_explicit_version(self) -> None:
        args = argparse.Namespace(
            server="server-a",
            profile="vitis_2022",
            readiness="cosim",
            example_spec="hls_vector_scale_mock_spec.json",
            vitis_version="2022.2",
        )
        candidate = {
            "version": "2022.2",
            "settings_script": "/tools/Xilinx/Vitis/2022.2/settings64.sh",
            "expected_tool": "vitis_hls",
            "target_part": "xcu50-fsvh2104-2-e",
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with patch.object(self.module, "set_vitis_selection") as set_selection:
                result = self.module._select_vitis_profile(args, run_dir, [candidate], {"settings_script": "/fallback/settings64.sh", "expected_tool": "vitis_hls"})

        self.assertEqual(result["version"], "2022.2")
        set_selection.assert_called_once()


if __name__ == "__main__":
    unittest.main()
