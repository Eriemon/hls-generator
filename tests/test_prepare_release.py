from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "prepare_release.py"
EXPECTED_VERSION = "0.1.9"


def _load_module():
    spec = importlib.util.spec_from_file_location("prepare_release_test_module", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrepareReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()

    def test_prepare_release_allows_non_git_install_context(self) -> None:
        with tempfile.TemporaryDirectory(dir=SKILL_ROOT.parents[1]) as tmp:
            dist_root = Path(tmp) / "dist"
            with patch.object(self.module, "_git_output", side_effect=self.module.ReleaseError("git unavailable")):
                payload = self.module.prepare_release(EXPECTED_VERSION, dist_root)
            self.assertTrue(str(payload["release_dir"]).endswith(f"erie-hls-generator-v{EXPECTED_VERSION}"))
            release_manifest = json.loads((Path(payload["release_dir"]) / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(release_manifest["source_commit"], "unavailable")
            self.assertEqual(release_manifest["source_branch"], "unavailable")
            self.assertIn("skills/erie-hls-generator/SKILL.md", release_manifest["included_files"])
            self.assertNotIn("README-CN.md", "\n".join(release_manifest["included_files"]))


if __name__ == "__main__":
    unittest.main()
