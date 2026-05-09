from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.hls_generator.skill_dependencies import _default_install_root, build_dependency_request, check_skill_dependencies, install_skill_dependencies


def _write_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test skill\n---\n\n# {name}\n", encoding="utf-8")
    return skill_dir


class SkillDependencyTests(unittest.TestCase):
    def test_missing_dependency_blocks_and_request_includes_install_command(self) -> None:
        config = [
            {
                "id": "remote-ssh",
                "level": "required",
                "purpose": "remote validation",
                "repo_url": "https://github.com/Eriemon/remote-ssh.git",
                "ref": "main",
                "paths": ["."],
                "expected_skill_names": ["erie-remote-ssh"],
                "destination_names": ["erie-remote-ssh"],
                "aliases": [],
                "adapter": "erie-remote-ssh",
                "blocking": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = check_skill_dependencies(config, skill_dirs=[Path(tmp)], plugin_cache_dirs=[])

        self.assertEqual(report["status"], "blocked_dependency")
        self.assertEqual(report["dependencies"][0]["status"], "missing")
        request = build_dependency_request(report)
        self.assertEqual(request["action"], "ask_install_skill_dependencies")
        self.assertIn("python -m runtime.hls_generator deps install --all", request["recommended_commands"])

    def test_dependency_config_requires_one_to_one_install_mapping(self) -> None:
        config = [
            {
                "id": "bad-map",
                "level": "required",
                "purpose": "bad",
                "repo_url": "https://github.com/example/bad.git",
                "ref": "main",
                "paths": ["one"],
                "expected_skill_names": ["one", "two"],
                "destination_names": ["one"],
                "aliases": [],
                "adapter": "test",
                "blocking": True,
            }
        ]
        with self.assertRaisesRegex(ValueError, "expected_skill_names and paths"):
            check_skill_dependencies(config, skill_dirs=[], plugin_cache_dirs=[])

    def test_context_engineering_accepts_collection_frontmatter_alias(self) -> None:
        config = [
            {
                "id": "context-engineering",
                "level": "recommended",
                "purpose": "agent debug",
                "repo_url": "https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering.git",
                "ref": "main",
                "paths": ["."],
                "expected_skill_names": ["context-engineering"],
                "destination_names": ["context-engineering"],
                "aliases": ["context-engineering-collection"],
                "adapter": "context-engineering",
                "blocking": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "context-engineering"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: context-engineering-collection\ndescription: collection\n---\n\n# Context\n",
                encoding="utf-8",
            )
            report = check_skill_dependencies(config, skill_dirs=[Path(tmp)], plugin_cache_dirs=[])

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["dependencies"][0]["installed"][0]["frontmatter_name"], "context-engineering-collection")

    def test_superpowers_accepts_codex_plugin_cache_shape(self) -> None:
        config = [
            {
                "id": "superpowers",
                "level": "recommended",
                "purpose": "planning",
                "repo_url": "https://github.com/obra/superpowers.git",
                "ref": "main",
                "paths": ["skills/brainstorming", "skills/systematic-debugging", "skills/verification-before-completion"],
                "expected_skill_names": ["brainstorming", "systematic-debugging", "verification-before-completion"],
                "destination_names": ["brainstorming", "systematic-debugging", "verification-before-completion"],
                "aliases": [],
                "adapter": "superpowers",
                "blocking": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            plugin_skills = Path(tmp) / "superpowers-dev" / "superpowers" / "5.1.0" / "skills"
            _write_skill(plugin_skills, "brainstorming")
            _write_skill(plugin_skills, "systematic-debugging")
            _write_skill(plugin_skills, "verification-before-completion")
            report = check_skill_dependencies(config, skill_dirs=[], plugin_cache_dirs=[Path(tmp)])

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["dependencies"][0]["status"], "ok")

    def test_remote_ssh_adapter_marks_missing_helper_files_invalid(self) -> None:
        config = [
            {
                "id": "remote-ssh",
                "level": "required",
                "purpose": "remote validation",
                "repo_url": "https://github.com/Eriemon/remote-ssh.git",
                "ref": "main",
                "paths": ["."],
                "expected_skill_names": ["erie-remote-ssh"],
                "destination_names": ["erie-remote-ssh"],
                "aliases": [],
                "adapter": "erie-remote-ssh",
                "blocking": True,
                "required_files": ["scripts/remote_ssh.py", "config/defaults.json"],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            _write_skill(Path(tmp), "erie-remote-ssh")
            report = check_skill_dependencies(config, skill_dirs=[Path(tmp)], plugin_cache_dirs=[])

        self.assertEqual(report["status"], "blocked_dependency")
        self.assertEqual(report["dependencies"][0]["status"], "invalid")
        self.assertIn("scripts/remote_ssh.py", json.dumps(report["dependencies"][0], ensure_ascii=False))

    def test_frontmatter_name_mismatch_is_invalid_even_when_directory_matches(self) -> None:
        config = [
            {
                "id": "fpga",
                "level": "required",
                "purpose": "fpga",
                "repo_url": "https://github.com/example/fpga.git",
                "ref": "main",
                "paths": ["vitis-hls-synthesis"],
                "expected_skill_names": ["vitis-hls-synthesis"],
                "destination_names": ["vitis-hls-synthesis"],
                "aliases": [],
                "adapter": "fpga-agent-skills",
                "blocking": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "vitis-hls-synthesis"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: wrong-name\ndescription: bad\n---\n", encoding="utf-8")
            report = check_skill_dependencies(config, skill_dirs=[Path(tmp)], plugin_cache_dirs=[])

        self.assertEqual(report["status"], "blocked_dependency")
        self.assertEqual(report["dependencies"][0]["status"], "invalid")
        self.assertIn("frontmatter_name", json.dumps(report["dependencies"][0], ensure_ascii=False))

    def test_install_all_skips_valid_installed_dependencies_and_reports_invalid_existing_dirs(self) -> None:
        config = [
            {
                "id": "valid-installed",
                "level": "required",
                "purpose": "already there",
                "repo_url": "https://github.com/example/valid.git",
                "ref": "main",
                "paths": ["."],
                "expected_skill_names": ["valid-skill"],
                "destination_names": ["valid-skill"],
                "aliases": [],
                "adapter": "test",
                "blocking": True,
            },
            {
                "id": "invalid-installed",
                "level": "required",
                "purpose": "needs repair",
                "repo_url": "https://github.com/example/invalid.git",
                "ref": "main",
                "paths": ["."],
                "expected_skill_names": ["invalid-skill"],
                "destination_names": ["invalid-skill"],
                "aliases": [],
                "adapter": "test",
                "blocking": True,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "valid-skill")
            invalid = _write_skill(root, "invalid-skill")
            (invalid / "SKILL.md").write_text("---\nname: wrong-name\ndescription: bad\n---\n", encoding="utf-8")
            result = install_skill_dependencies(config, install_all=True, dest_root=root, skill_dirs=[root], plugin_cache_dirs=[])

        self.assertEqual(result["status"], "repair_required")
        self.assertEqual(result["skipped"][0]["id"], "valid-installed")
        self.assertEqual(result["repair_required"][0]["id"], "invalid-installed")
        self.assertEqual(result["installed"], [])

    def test_default_install_root_follows_skills_dir_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "skills-a"
            second = Path(tmp) / "skills-b"
            with patch.dict(os.environ, {"HLS_GENERATOR_SKILLS_DIRS": os.pathsep.join([str(first), str(second)])}, clear=False):
                self.assertEqual(_default_install_root(), first.resolve())

    def test_remote_validation_config_uses_dependency_discovery_for_custom_skill_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            remote = _write_skill(root, "erie-remote-ssh")
            (remote / "scripts").mkdir()
            (remote / "scripts" / "remote_ssh.py").write_text("# helper\n", encoding="utf-8")
            (remote / "config").mkdir()
            (remote / "config" / "defaults.json").write_text("{}\n", encoding="utf-8")
            env = os.environ.copy()
            env["HLS_GENERATOR_SKILLS_DIRS"] = str(root)
            env["HLS_GENERATOR_PLUGIN_CACHE_DIRS"] = ""
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from runtime.hls_generator.config import remote_validation_config; print(remote_validation_config()['erie_skill_dir'])",
                ],
                cwd=SKILL_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), remote.resolve())

    def test_cli_deps_check_blocks_with_empty_skill_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / "skills"
            plugins = Path(tmp) / "plugins"
            skills.mkdir()
            plugins.mkdir()
            env = os.environ.copy()
            env["HLS_GENERATOR_SKILLS_DIRS"] = str(skills)
            env["HLS_GENERATOR_PLUGIN_CACHE_DIRS"] = str(plugins)
            result = subprocess.run(
                [sys.executable, "-m", "runtime.hls_generator", "deps", "check", "--json"],
                cwd=SKILL_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "blocked_dependency")
        self.assertGreaterEqual(len(payload["dependencies"]), 4)

    def test_confidence_loop_reports_missing_dependencies_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / "skills"
            plugins = Path(tmp) / "plugins"
            skills.mkdir()
            plugins.mkdir()
            env = os.environ.copy()
            env["HLS_GENERATOR_SKILLS_DIRS"] = str(skills)
            env["HLS_GENERATOR_PLUGIN_CACHE_DIRS"] = str(plugins)
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/confidence_loop.py",
                    "--skip-smoke",
                    "--skip-compileall",
                    "--skip-quick-validate",
                    "--skip-remote",
                    "--json-out",
                    "reports/confidence-loop/missing-deps-test.json",
                ],
                cwd=SKILL_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["confidence_status"], "needs_attention")
        self.assertEqual(payload["gates"]["skill_dependencies"]["status"], "failed")
        self.assertEqual(payload["gates"]["example_mock_validation"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
