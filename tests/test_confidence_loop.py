from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
