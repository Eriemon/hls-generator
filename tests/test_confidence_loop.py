from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "confidence_loop.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("confidence_loop_test_module", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _joined(*parts: str) -> str:
    return "".join(parts)


class ConfidenceLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()

    def test_local_confidence_stays_local_without_remote_gate(self) -> None:
        status, scope, risks, returncode = self.module._confidence_outcome(
            {
                "smoke": {"status": "passed"},
                "compileall": {"status": "passed"},
                "skill_dependencies": {"status": "passed"},
                "copyright_term_scan": {"status": "passed"},
                "example_mock_validation": {"status": "passed"},
            },
            remote_requested=False,
            remote_skipped=True,
        )

        self.assertEqual(status, "local_high_confidence")
        self.assertEqual(scope, "local")
        self.assertEqual(returncode, 0)
        self.assertIn("Final confidence requires remote Vitis acceptance.", risks)

    def test_final_confidence_requires_remote_gate(self) -> None:
        status, scope, risks, returncode = self.module._confidence_outcome(
            {
                "smoke": {"status": "passed"},
                "compileall": {"status": "passed"},
                "skill_dependencies": {"status": "passed"},
                "copyright_term_scan": {"status": "passed"},
                "example_mock_validation": {"status": "passed"},
                "remote_vitis_acceptance": {"status": "passed"},
            },
            remote_requested=True,
            remote_skipped=False,
        )

        self.assertEqual(status, "factual_high_confidence")
        self.assertEqual(scope, "final")
        self.assertEqual(returncode, 0)
        self.assertEqual(risks, [])

    def test_missing_remote_gate_blocks_final_confidence(self) -> None:
        status, scope, risks, returncode = self.module._confidence_outcome(
            {
                "smoke": {"status": "passed"},
                "compileall": {"status": "passed"},
                "skill_dependencies": {"status": "passed"},
                "copyright_term_scan": {"status": "passed"},
                "example_mock_validation": {"status": "passed"},
            },
            remote_requested=False,
            remote_skipped=False,
        )

        self.assertEqual(status, "blocked_remote_validation")
        self.assertEqual(scope, "final")
        self.assertEqual(returncode, 1)
        self.assertIn("Remote Vitis acceptance was not executed.", risks)

    def test_copyright_scan_catches_sensitive_content_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "references").mkdir()
            bad_file = root / "references" / "scan_notes.md"
            bad_file.write_text(
                "source " + _joined("off", "icial") + " note\n",
                encoding="utf-8",
            )
            bad_dir = root / _joined("tuto", "rials")
            bad_dir.mkdir()
            result = self.module._copyright_term_scan(root=root)

        self.assertEqual(result["status"], "failed")
        self.assertGreaterEqual(len(result["matches"]), 2)

    def test_release_sensitivity_scan_catches_fixed_remote_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime" / "hls_generator").mkdir(parents=True)
            (root / "runtime" / "hls_generator" / "runtime_config.json").write_text(
                json_text := (
                    "{\n"
                    '  "remote_validation": {\n'
                    '    "erie_settings_path": "${erie_skill_dir}/config/defaults.json",\n'
                    '    "vitis_profiles": {\n'
                    '      "vitis_2022": {\n'
                    '        "settings_script": "/' + "tools" + '/Xilinx/Vitis/2022.2/settings64.sh",\n'
                    '        "expected_tool": "vitis_hls",\n'
                    '        "target_part": "' + "xcu50" + '-fsvh2104-2-e"\n'
                    "      }\n"
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
                encoding="utf-8",
            )
            result = self.module._release_sensitivity_scan(root=root)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(any(("/" + "tools" + "/Xilinx/") in item for item in result["matches"]))
        self.assertTrue(any("xcu50" in item for item in result["matches"]))

    def test_release_sensitivity_scan_catches_sensitive_zip_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "erie-hls-generator-v0.1.8.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "erie-hls-generator-v0.1.8/skills/erie-hls-generator/runtime/hls_generator/runtime_config.json",
                    (
                        "{\n"
                        '  "remote_validation": {\n'
                        '    "settings_script": "/' + "tools" + '/Xilinx/Vitis/2022.2/settings64.sh"\n'
                        "  }\n"
                        "}\n"
                    ),
                )

            result = self.module._release_sensitivity_scan(root=archive_path)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(any(archive_path.name in item for item in result["matches"]))
        self.assertTrue(any(("/" + "tools" + "/Xilinx/") in item for item in result["matches"]))

    def test_example_spec_names_include_new_shipped_patterns(self) -> None:
        spec_names = self.module._example_spec_names()

        self.assertIn("hls_axi4_burst_vector_scale_spec.json", spec_names)
        self.assertIn("hls_task_graph_axis_spec.json", spec_names)
        self.assertIn("hls_directio_freerun_axis_spec.json", spec_names)

    def test_run_remote_passes_vitis_version_when_requested(self) -> None:
        seen: dict[str, object] = {}

        def fake_run_remote_command(command):
            seen["command"] = command
            return {"status": "passed"}

        with patch.object(self.module, "_run_remote_command", side_effect=fake_run_remote_command):
            result = self.module._run_remote("server-a", "cosim", "hls_vector_scale_spec.json", vitis_version="2024.2")

        self.assertEqual(result["status"], "passed")
        self.assertIn("--vitis-version", seen["command"])
        self.assertIn("2024.2", seen["command"])

    def test_run_remote_acceptance_stops_when_link_fails(self) -> None:
        commands: list[list[str]] = []

        def fake_run_remote_command(command):
            commands.append(command)
            if "--mode" in command and command[command.index("--mode") + 1] == "link":
                return {"status": "failed", "error": "link failed"}
            return {"status": "passed"}

        with patch.object(self.module, "_run_remote_command", side_effect=fake_run_remote_command):
            result = self.module._run_remote_acceptance("server-a", "cosim", ["hls_vector_scale_spec.json"], vitis_version="2024.2")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"], [])
        self.assertEqual(len(commands), 1)

    def test_run_split_remote_passes_dual_server_arguments(self) -> None:
        seen: dict[str, object] = {}

        def fake_run_remote_command(command):
            seen["command"] = command
            return {"status": "passed"}

        with patch.object(self.module, "_run_remote_command", side_effect=fake_run_remote_command):
            result = self.module._run_split_remote(
                "build-a",
                "validate-b",
                "cosim",
                "hls_vector_scale_spec.json",
                vitis_version="2022.2",
            )

        self.assertEqual(result["status"], "passed")
        self.assertIn("--build-server", seen["command"])
        self.assertIn("build-a", seen["command"])
        self.assertIn("--validate-server", seen["command"])
        self.assertIn("validate-b", seen["command"])

    def test_run_split_remote_acceptance_stops_when_preflight_fails(self) -> None:
        calls: list[list[str]] = []

        def fake_run_remote_command(command):
            calls.append(command)
            return {"status": "failed", "error": "preflight failed"}

        with patch.object(self.module, "_run_remote_command", side_effect=fake_run_remote_command):
            result = self.module._run_split_remote_acceptance(
                "build-a",
                "validate-b",
                "cosim",
                ["hls_vector_scale_spec.json"],
                vitis_version="2022.2",
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"], [])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
