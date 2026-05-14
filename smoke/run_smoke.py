"""Standalone smoke validator for the HLS-only skill."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.hls_adapter import render_hls_prompt, run_hls_workflow, validate_hls_artifacts  # noqa: E402
from runtime.hls_generator import cli  # noqa: E402
from runtime.hls_generator.config import generated_roots, runtime_config, skill_config_path, smoke_root_name, vitis_tools  # noqa: E402
from runtime.hls_generator.extractor import ExtractionError, extract_response  # noqa: E402
from runtime.hls_generator.hls_cfg import parse_hls_cfg_entries  # noqa: E402
from runtime.hls_generator.hls_tcl import render_vitis_hls_tcl  # noqa: E402
from runtime.hls_generator.model_provider import _mock_vectors  # noqa: E402
from runtime.hls_generator.reference_contract import REFERENCE_RESULT_TAG  # noqa: E402
from runtime.hls_generator.spec import SpecError, normalize_spec, scaffold_spec  # noqa: E402
from runtime.hls_generator.user_config import set_comment_language, user_config_path  # noqa: E402
from runtime.hls_generator.vitis_rules import scan_vitis_rule_violations  # noqa: E402
from runtime.hls_generator.vectors import VECTOR_HASH_TAG  # noqa: E402
import runtime.hls_generator.validation as validation  # noqa: E402
from runtime.hls_generator.workspace import use_workspace_root  # noqa: E402

HLS_TOOL_COMMANDS: list[list[str]] = []
HLS_TCL_TEXTS: list[str] = []
VITIS_TOOL_CONFIGS = vitis_tools()
PRIMARY_VITIS_TOOL = VITIS_TOOL_CONFIGS[0]
FALLBACK_VITIS_TOOL = VITIS_TOOL_CONFIGS[1]
REAL_SUBPROCESS_RUN = subprocess.run
CURRENT_SMOKE_BASE: Path | None = None


def main() -> int:
    global CURRENT_SMOKE_BASE
    with use_workspace_root(ROOT):
        base_root = ROOT / smoke_root_name()
        base_root.mkdir(parents=True, exist_ok=True)
        base = base_root / f"run-{os.getpid()}-{time.time_ns()}"
        CURRENT_SMOKE_BASE = base
        base.mkdir(parents=True)
        os.environ["HLS_GENERATOR_USER_CONFIG"] = str(base / "user_config.json")
        try:
            _run_skill_metadata_checks()
            _run_comment_language_choice_checks(base)
            set_comment_language("zh")
            artifact_dir = _run_mock_workflow(base)
            _run_prompt_and_static_validation(base, artifact_dir)
            _run_invalid_response(base)
            _run_human_resume(base)
            _run_rejection_checks(base, artifact_dir)
            _run_path_boundary_checks(base, artifact_dir)
            _run_config_safety_checks(base)
            _run_remote_acceptance_checks(base)
            _run_extraction_safety_checks(base)
            _run_copyright_gate_checks(base)
            _run_example_coverage(base)
            _run_ug_reference_integration_checks(base)
            _run_confidence_loop_checks(base)
            _run_release_packaging_checks(base)
            _run_vitis_selection_checks(base, artifact_dir)
            _run_missing_toolchain_workflow(base)
        finally:
            _remove_tree(base, strict=False)
            CURRENT_SMOKE_BASE = None
    print("HLS generator smoke checks passed.")
    return 0


def _remove_tree(path: Path, *, strict: bool, attempts: int = 8, delay_s: float = 0.1) -> None:
    if not path.exists():
        return
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, onerror=_handle_rmtree_error)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_s * (attempt + 1))
    if strict and last_error is not None:
        raise last_error


def _handle_rmtree_error(func, target, exc_info) -> None:
    exc = exc_info[1]
    if isinstance(exc, PermissionError):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
            return
        except OSError:
            pass
    raise exc


def _run_skill_metadata_checks() -> None:
    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    agent_text = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    frontmatter = skill_text.split("---", 2)[1]
    assert "name: erie-hls-generator" in frontmatter, frontmatter
    description_line = next(line for line in frontmatter.splitlines() if line.startswith("description:"))
    description = description_line.removeprefix("description:").strip()
    assert description.startswith("Use when"), description
    required_terms = [
        "HLS development",
        "HLS design",
        "HLS modification",
        "HLS debug",
        "HLS debugging",
        "Chinese-language HLS requests",
        "high-level synthesis",
        "Vitis HLS",
        "cosim",
        "HLS-generated RTL/Verilog",
    ]
    for term in required_terms:
        assert term in description, (term, description)
    assert description.isascii(), description
    for body_term in [
        "HLS-generated RTL/Verilog interface, export, cosim, and debug issues are in scope",
        "Pure handwritten Verilog/SystemVerilog debug is not led by this skill",
        "vivado-debug",
        "vivado-sim",
        "vivado-analysis",
    ]:
        assert body_term in skill_text, body_term
    assert 'short_description: "Develop, debug, and validate Vitis HLS kernels and HLS-generated RTL."' in agent_text, agent_text
    assert "allow_implicit_invocation: true" in agent_text, agent_text


def _run_comment_language_choice_checks(base: Path) -> None:
    blocked = run_hls_workflow(
        _load_spec(),
        out_dir=base / "comment-language-block",
        provider_name="mock",
        readiness="static",
        run_external=False,
        comment_language="auto",
    )
    assert blocked["status"] == "blocked_human", blocked
    request_path = Path(blocked["run_dir"]) / "comment_language_request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["action"] == "ask_comment_language"
    assert [item["value"] for item in request["options"]] == ["en", "zh"], request
    assert request["user_config_path"] == str(user_config_path()), request

    set_comment_language("en")
    en_result = run_hls_workflow(
        _load_spec(),
        out_dir=base / "comment-language-en",
        provider_name="mock",
        readiness="static",
        run_external=False,
        comment_language="auto",
    )
    assert en_result["status"] == "passed", en_result
    en_artifact_dir = Path(en_result["run_dir"]) / "attempt-001" / "hls" / "artifacts"
    en_source = (en_artifact_dir / "src" / "vector_scale_kernel.cpp").read_text(encoding="utf-8")
    assert "Port protocols and pipeline constraints" in en_source
    assert "中文注释" not in en_source
    en_report = validate_hls_artifacts(_load_spec(), en_artifact_dir, run_external=False, readiness="static", comment_language="en")
    assert en_report["ok"] is True, en_report
    assert en_report["warnings"] == 0, en_report


def _load_spec(name: str = "hls_vector_scale_mock_spec.json") -> dict:
    return json.loads((skill_config_path("examples_dir") / name).read_text(encoding="utf-8"))


def _run_mock_workflow(base: Path) -> Path:
    _install_hls_mocks(str(PRIMARY_VITIS_TOOL["name"]))
    run_dir = base / "happy"
    result = run_hls_workflow(
        _load_spec(),
        out_dir=run_dir,
        provider_name="mock",
        readiness="cosim",
    )
    assert result["status"] == "passed", result
    payload = json.loads((run_dir / "workflow_result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert (run_dir / "_adapter_inputs" / "requirements.json").exists()
    assert (run_dir / "_adapter_inputs" / "codegen_plan.json").exists()
    artifact_dir = run_dir / "attempt-001" / "hls" / "artifacts"
    _assert_hls_artifacts(artifact_dir)
    assert HLS_TOOL_COMMANDS and HLS_TOOL_COMMANDS[-1][0] == PRIMARY_VITIS_TOOL["command"][0], HLS_TOOL_COMMANDS
    assert "--tcl" in HLS_TOOL_COMMANDS[-1], HLS_TOOL_COMMANDS
    assert HLS_TCL_TEXTS, "Expected primary Vitis Tcl flow to be generated."
    assert "csim_design" in HLS_TCL_TEXTS[-1]
    assert "csynth_design" in HLS_TCL_TEXTS[-1]
    assert "cosim_design" in HLS_TCL_TEXTS[-1]
    return artifact_dir


def _run_prompt_and_static_validation(base: Path, artifact_dir: Path) -> None:
    spec = _load_spec()
    prompt_path = base / "prompt" / "hls_prompt.md"
    prompt = render_hls_prompt(spec, prompt_path, stage="hls")
    text = prompt["prompt"]
    assert prompt_path.exists()
    assert "Vitis HLS implementation generation" in text
    assert "Return only fenced code blocks" in text
    assert "Create HLS C/C++ source, header, self-checking testbench, and cfg artifacts." in text
    assert "Vitis HLS 2024.2" in text
    assert "vitis-developer" in text
    assert "vitis-hls-synthesis" in text
    assert "DATA_PACK" in text and "set_directive_resource" in text
    assert "array_partition and array_reshape" in text
    assert "AXI4-Stream" in text
    assert "Identify the intended HLS pattern" in text
    assert "report-driven" in text
    assert "validated sequential baseline" in text
    assert "variable-bound loops" in text
    assert "pointer aliasing" in text
    assert "control-driven orchestration" in text
    assert "QoR portability review" in text
    assert "DSP-oriented transforms and filters" in text
    assert "stable Tcl/.cfg execution flow only" in text
    assert "vitis-hls-introductory-examples" not in text.lower()
    assert "open_component" not in text
    assert "direct v++" not in text

    report = validate_hls_artifacts(spec, artifact_dir, run_external=False, readiness="static")
    assert report["ok"] is True, report
    assert report["errors"] == 0, report
    assert report["warnings"] == 0, report


def _run_invalid_response(base: Path) -> None:
    spec = _load_spec()
    spec["workflow"] = {"mock_behavior": {"tests": "invalid_response"}}
    result = run_hls_workflow(spec, out_dir=base / "invalid", provider_name="mock", run_external=False, readiness="static")
    assert result["status"] == "invalid_response", result


def _run_human_resume(base: Path) -> None:
    _install_hls_mocks(str(PRIMARY_VITIS_TOOL["name"]))
    spec = _load_spec()
    spec["workflow"] = {
        "codegen_plan_override": {
            "ready_for_generation": False,
            "open_questions": ["Confirm the exact HLS memory burst policy."],
        }
    }
    run_dir = base / "human"
    blocked = run_hls_workflow(spec, out_dir=run_dir, provider_name="mock", readiness="execute")
    assert blocked["status"] == "blocked_human", blocked

    resumed = run_hls_workflow(
        resume_dir=run_dir,
        decision={
            "version": 1,
            "status": "resolved",
            "decision": "Use non-burst AXI memory accesses for this HLS kernel.",
            "evidence": ["Confirmed vector scale example"],
            "constraints": ["Preserve the requested HLS outputs."],
        },
        provider_name="mock",
        readiness="execute",
    )
    assert resumed["status"] == "passed", resumed


def _run_rejection_checks(base: Path, artifact_dir: Path) -> None:
    _expect_error(lambda: scaffold_spec("rtl"), SpecError, "HLS-only")
    _expect_error(lambda: normalize_spec({"name": "bad", "target": "rtl"}, target="rtl"), SpecError, "HLS-only")
    _expect_error(
        lambda: normalize_spec(
            {
                "name": "bad",
                "target": "hls",
                "description": "Bad output language.",
                "interfaces": {},
                "behavior": [],
                "constraints": [],
                "outputs": [{"path": "rtl/bad.v", "kind": "source", "language": "verilog"}],
            }
        ),
        SpecError,
        "not allowed",
    )
    _expect_error(lambda: run_hls_workflow(_load_spec(), out_dir=base / "rtl-target", target="rtl"), ValueError, "HLS-only")
    _expect_error(lambda: render_hls_prompt(_load_spec(), base / "bad.md", target="rtl"), ValueError, "HLS-only")
    _expect_error(lambda: validate_hls_artifacts(_load_spec(), artifact_dir, target="rtl"), ValueError, "HLS-only")
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        try:
            cli.main(["scaffold", "--target", "rtl", "--name", "bad", "--out", str(base / "bad.json")])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("Expected old CLI target to be rejected.")


def _run_path_boundary_checks(base: Path, artifact_dir: Path) -> None:
    relative = run_hls_workflow(_load_spec(), out_dir=_smoke_relative_path("relative-out"), provider_name="mock", readiness="static", run_external=False)
    assert relative["status"] == "passed", relative
    cli_spec = base / "cli" / "spec.json"
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli.main(["scaffold", "--target", "hls", "--name", "cli_path_check", "--out", str(cli_spec)]) == 0
    assert cli_spec.exists()
    _expect_error(lambda: run_hls_workflow(_load_spec(), out_dir=ROOT / "runtime" / "bad", provider_name="mock"), ValueError, "protected")
    _expect_error(lambda: run_hls_workflow(_load_spec(), out_dir=ROOT / "scripts" / "bad", provider_name="mock"), ValueError, "protected")
    _expect_error(lambda: run_hls_workflow(_load_spec(), out_dir=ROOT.parent / "outside", provider_name="mock"), ValueError, "must stay")
    _expect_error(lambda: render_hls_prompt(_load_spec(), ROOT / "SKILL.md"), ValueError, "protected")
    _expect_error(lambda: validate_hls_artifacts(_load_spec(), artifact_dir, report_json=ROOT / "runtime" / "bad.json"), ValueError, "protected")


def _run_config_safety_checks(base: Path) -> None:
    bad_config = runtime_config()
    bad_config["paths"]["examples_dir"] = "../outside"
    bad_path = base / "bad_runtime_config.json"
    bad_path.write_text(json.dumps(bad_config, indent=2), encoding="utf-8")
    env = os.environ.copy()
    env["HLS_GENERATOR_RUNTIME_CONFIG"] = str(bad_path.relative_to(ROOT))
    result = REAL_SUBPROCESS_RUN([sys.executable, "-m", "runtime.hls_generator", "config"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 2, result
    assert "must stay inside the skill root" in result.stderr, result.stderr

    bad_remote = runtime_config()
    bad_remote["remote_validation"]["local_run_root"] = "../outside"
    bad_remote_path = base / "bad_remote_config.json"
    bad_remote_path.write_text(json.dumps(bad_remote, indent=2), encoding="utf-8")
    env["HLS_GENERATOR_RUNTIME_CONFIG"] = str(bad_remote_path.relative_to(ROOT))
    result = REAL_SUBPROCESS_RUN([sys.executable, "-m", "runtime.hls_generator", "config"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 2, result
    assert "remote_validation.local_run_root" in result.stderr, result.stderr

    protected_remote = runtime_config()
    protected_remote["remote_validation"]["local_run_root"] = "runtime/remote-validation"
    protected_remote_path = base / "protected_remote_config.json"
    protected_remote_path.write_text(json.dumps(protected_remote, indent=2), encoding="utf-8")
    env["HLS_GENERATOR_RUNTIME_CONFIG"] = str(protected_remote_path.relative_to(ROOT))
    result = REAL_SUBPROCESS_RUN([sys.executable, "-m", "runtime.hls_generator", "config"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 2, result
    assert "remote_validation.local_run_root" in result.stderr, result.stderr


def _run_remote_acceptance_checks(base: Path) -> None:
    config_path = _write_fake_remote_config(base)
    _run_remote_package_newline_check(base)
    env = os.environ.copy()
    env["HLS_GENERATOR_RUNTIME_CONFIG"] = str(config_path.relative_to(ROOT))
    env["PYTHONUTF8"] = "1"
    dry = REAL_SUBPROCESS_RUN(
        [sys.executable, "scripts/remote_vitis_acceptance.py", "--mode", "link", "--server", "link-server", "--dry-run", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert dry.returncode == 0, dry
    dry_payload = json.loads(dry.stdout)
    assert dry_payload["status"] == "dry_run", dry_payload
    assert dry_payload["uses_erie_remote_ssh"] is True

    dry_vitis = REAL_SUBPROCESS_RUN(
        [sys.executable, "scripts/remote_vitis_acceptance.py", "--mode", "vitis", "--server", "vitis-server", "--profile", "vitis_2022", "--readiness", "cosim", "--dry-run", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert dry_vitis.returncode == 0, dry_vitis
    dry_vitis_payload = json.loads(dry_vitis.stdout)
    assert dry_vitis_payload["status"] == "dry_run", dry_vitis_payload
    assert dry_vitis_payload["remote_artifacts_retained"] is True, dry_vitis_payload
    assert dry_vitis_payload["cleanup_performed"] is False, dry_vitis_payload
    assert "retain remote validation directory" in dry_vitis_payload["steps"], dry_vitis_payload
    assert "erie request delete cleanup" not in dry_vitis_payload["steps"], dry_vitis_payload

    blocked = REAL_SUBPROCESS_RUN(
        [sys.executable, "scripts/remote_vitis_acceptance.py", "--mode", "vitis", "--server", "link-server", "--profile", "vitis_2022", "--readiness", "cosim", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert blocked.returncode == 3, blocked
    blocked_payload = json.loads(blocked.stdout)
    assert blocked_payload["status"] == "blocked_vitis_server", blocked_payload
    assert blocked_payload["uses_erie_remote_ssh"] is True

    version_blocked = REAL_SUBPROCESS_RUN(
        [sys.executable, "scripts/remote_vitis_acceptance.py", "--mode", "vitis", "--server", "vitis-server", "--profile", "vitis_2022", "--readiness", "cosim", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert version_blocked.returncode == 4, version_blocked
    version_blocked_payload = json.loads(version_blocked.stdout)
    assert version_blocked_payload["status"] == "blocked_remote_version_choice", version_blocked_payload
    assert len(version_blocked_payload["candidate_versions"]) == 2, version_blocked_payload
    version_request = Path(version_blocked_payload["remote_vitis_version_request"])
    assert version_request.exists(), version_blocked_payload
    assert "2022.2" in version_request.read_text(encoding="utf-8")

    retained = REAL_SUBPROCESS_RUN(
        [sys.executable, "scripts/remote_vitis_acceptance.py", "--mode", "vitis", "--server", "vitis-server", "--profile", "vitis_2022", "--vitis-version", "2022.2", "--readiness", "cosim", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert retained.returncode == 0, retained
    retained_payload = json.loads(retained.stdout)
    assert retained_payload["status"] == "passed", retained_payload
    assert retained_payload["vitis_version"] == "2022.2", retained_payload
    assert retained_payload["remote_dir"].startswith(".hls-generator-remote-validation/vitis-"), retained_payload
    assert retained_payload["remote_artifacts_retained"] is True, retained_payload
    assert retained_payload["cleanup_performed"] is False, retained_payload
    assert not any("delete" in request for request in retained_payload["requests"]), retained_payload
    saved_user_config = json.loads(user_config_path().read_text(encoding="utf-8"))
    assert saved_user_config["vitis_version_selection"]["vitis-server"]["version"] == "2022.2", saved_user_config

    retained_from_config = REAL_SUBPROCESS_RUN(
        [sys.executable, "scripts/remote_vitis_acceptance.py", "--mode", "vitis", "--server", "vitis-server", "--profile", "vitis_2022", "--readiness", "cosim", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert retained_from_config.returncode == 0, retained_from_config
    retained_from_config_payload = json.loads(retained_from_config.stdout)
    assert retained_from_config_payload["status"] == "passed", retained_from_config_payload
    assert retained_from_config_payload["vitis_version"] == "2022.2", retained_from_config_payload
    retained_report = json.loads((Path(retained_payload["run_dir"]) / "result.json").read_text(encoding="utf-8"))
    assert retained_report["remote_dir"] == retained_payload["remote_dir"], retained_report
    assert retained_report["remote_artifacts_retained"] is True, retained_report

    cleaned = REAL_SUBPROCESS_RUN(
        [sys.executable, "scripts/remote_vitis_acceptance.py", "--mode", "vitis", "--server", "vitis-server", "--profile", "vitis_2022", "--readiness", "cosim", "--cleanup-remote", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert cleaned.returncode == 0, cleaned
    cleaned_payload = json.loads(cleaned.stdout)
    assert cleaned_payload["status"] == "passed", cleaned_payload
    assert cleaned_payload["remote_artifacts_retained"] is False, cleaned_payload
    assert cleaned_payload["cleanup_performed"] is True, cleaned_payload
    assert any("delete" in request for request in cleaned_payload["requests"]), cleaned_payload


def _run_remote_package_newline_check(base: Path) -> None:
    spec = importlib.util.spec_from_file_location("remote_vitis_acceptance", ROOT / "scripts" / "remote_vitis_acceptance.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    artifact_dir = base / "remote-package-artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "hls_config.cfg").write_text("[HLS]\nsyn.top=dummy\nclock=10\n", encoding="utf-8")
    run_dir = base / "remote-package"
    run_dir.mkdir()
    package_path = module._create_vitis_package(run_dir, artifact_dir)
    with tarfile.open(package_path, "r:gz") as package:
        runner = package.extractfile("run_vitis.sh")
        assert runner is not None
        runner_bytes = runner.read()
    assert b"\r\n" not in runner_bytes, runner_bytes[:80]
    assert runner_bytes.startswith(b"#!/usr/bin/env bash\nset -euo pipefail\n")
    assert b'"$PWD/remote_vitis_project"' not in runner_bytes
    assert b'"remote_vitis_project"' in runner_bytes
    assert b"HLS_TARGET_PART" in runner_bytes


def _write_fake_remote_config(base: Path) -> Path:
    fake_erie = base / "fake_erie"
    (fake_erie / "scripts").mkdir(parents=True)
    (fake_erie / "config").mkdir(parents=True)
    (fake_erie / "config" / "defaults.json").write_text('{"version":1,"paths":{"default_server_list":"${settings_dir}/server_list.local.json"}}\n', encoding="utf-8")
    (fake_erie / "config" / "server_list.local.json").write_text(
        json.dumps(
            {
                "version": 1,
                "servers": [
                    {"id": "link-server", "name": "link-server", "enabled": True},
                    {"id": "vitis-server", "name": "vitis-server", "enabled": True},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (fake_erie / "scripts" / "remote_ssh.py").write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "from pathlib import Path",
                "args = sys.argv[1:]",
                "cmd = args[0] if args else ''",
                "def server_arg():",
                "    return args[args.index('--server') + 1] if '--server' in args else ''",
                "def settings_path():",
                "    return Path(args[args.index('--settings') + 1]) if '--settings' in args else Path('missing.json')",
                "def server_list_path():",
                "    settings = json.loads(settings_path().read_text(encoding='utf-8'))",
                "    raw = settings.get('paths', {}).get('default_server_list', '')",
                "    return Path(raw.replace('${settings_dir}', str(settings_path().parent))).resolve()",
                "if cmd == 'discover':",
                "    print('{\"status\":\"available\"}')",
                "elif cmd == 'list':",
                "    print('id\\tname\\tenabled\\tvalidation\\tworkspace\\ttarget\\tkey')",
                "elif cmd == 'check':",
                "    print('status: ok')",
                "elif cmd == 'workspace-check':",
                "    print('status: ok')",
                "elif cmd == 'exec':",
                "    joined = ' '.join(args)",
                "    if 'expected_tool=' in joined:",
                "        server = server_arg()",
                "        print('expected_tool=/tools/fake/vitis_hls' if server == 'vitis-server' else 'expected_tool=')",
                "    else:",
                "        print('HLS_REMOTE_LINK_OK')",
                "        print('host=fake')",
                "        print('pwd=/workspace')",
                "        print('python=Python 3.10.12')",
                "elif cmd == 'scan-software':",
                "    path = server_list_path()",
                "    data = json.loads(path.read_text(encoding='utf-8'))",
                "    for server in data.get('servers', []):",
                "        if server.get('id') == server_arg() and server_arg() == 'vitis-server':",
                "            server['software_scan'] = {'status':'ok','tools':{'vitis':{'status':'installed','path':'/tools/Xilinx/Vitis/2022.2/bin/vitis','version':'Vitis v2022.2','install_path':'/tools/Xilinx/Vitis/2022.2','versions':[{'status':'installed','path':'/tools/Xilinx/Vitis/2022.2/bin/vitis','version':'Vitis v2022.2','install_path':'/tools/Xilinx/Vitis/2022.2'},{'status':'installed','path':'/tools/Xilinx/Vitis/2023.2/bin/vitis','version':'Vitis v2023.2','install_path':'/tools/Xilinx/Vitis/2023.2'}]}}}",
                "    path.write_text(json.dumps(data, indent=2), encoding='utf-8')",
                "    print('software_scan_status: ok')",
                "elif cmd in {'request-mkdir', 'request-command', 'request-delete'}:",
                "    print(f'request: fake-{cmd}.json')",
                "elif cmd == 'run-request':",
                "    print('request executed')",
                "else:",
                "    print('unsupported fake command', cmd, file=sys.stderr)",
                "    raise SystemExit(2)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = runtime_config()
    config["remote_validation"]["erie_skill_dir"] = "${skill_root}/" + fake_erie.relative_to(ROOT).as_posix()
    config["remote_validation"]["erie_settings_path"] = "${erie_skill_dir}/config/defaults.json"
    config_path = base / "fake_remote_runtime_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path


def _run_extraction_safety_checks(base: Path) -> None:
    unsafe = """```json
{"target":"hls","name":"bad","files":[{"path":"../escape.cpp","kind":"source","language":"cpp"}]}
```
```cpp path=../escape.cpp
int bad() { return 0; }
```
"""
    _expect_error(lambda: extract_response(unsafe, base / "unsafe"), ExtractionError, "unsafe")

    missing = """```json
{"target":"hls","name":"bad","files":[{"path":"src/missing.cpp","kind":"source","language":"cpp"}]}
```
"""
    _expect_error(lambda: extract_response(missing, base / "missing"), ExtractionError, "Missing fenced code block")


def _run_vitis_selection_checks(base: Path, artifact_dir: Path) -> None:
    spec = _load_spec()

    _install_hls_mocks(str(FALLBACK_VITIS_TOOL["name"]))
    report = validate_hls_artifacts(spec, artifact_dir, readiness="cosim", run_external=True)
    assert report["ok"] is True, report
    assert HLS_TCL_TEXTS, "Expected fallback Vitis Tcl flow to be generated."
    assert "csim_design" in HLS_TCL_TEXTS[-1]
    assert "csynth_design" in HLS_TCL_TEXTS[-1]
    assert "cosim_design" in HLS_TCL_TEXTS[-1]

    _install_hls_mocks(None)
    missing = validate_hls_artifacts(spec, artifact_dir, readiness="execute", run_external=True)
    assert missing["ok"] is False, missing
    messages = "\n".join(issue["message"] for issue in missing["issues"])
    for tool in VITIS_TOOL_CONFIGS:
        assert str(tool["name"]) in messages


def _run_missing_toolchain_workflow(base: Path) -> None:
    _install_hls_mocks(None)
    result = run_hls_workflow(_load_spec(), out_dir=base / "missing-toolchain", provider_name="mock", readiness="execute", max_attempts=3)
    assert result["status"] == "blocked_toolchain", result
    attempts = result["workflow_result"].get("attempts", [])
    assert len(attempts) == 1, result
    assert attempts[0]["status"] == "blocked_toolchain", result
    assert attempts[0]["remote_toolchain_request"] == "attempt-001/remote_toolchain_request.json", result
    request_path = Path(result["run_dir"]) / "attempt-001" / "remote_toolchain_request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["action"] == "ask_remote_server"
    assert request["primary_source"] == "local_vitis_missing"
    assert request["preferred_skill"] == "erie-remote-ssh"
    assert request["vitis_skill_routing"]["preferred_skill"] == "vitis-developer"
    assert request["vitis_skill_routing"]["fallback_skills"] == ["vitis-hls-synthesis"]
    assert request["vitis_skill_routing"]["selected_skill"] in {"vitis-developer", "vitis-hls-synthesis"}
    assert "choices" in " ".join(request["erie_remote_ssh"]["selection_commands"])
    assert "remote_vitis_acceptance.py --mode vitis --server <erie-server>" in " ".join(request["hls_generator_remote_commands"])
    assert request["remote_artifact_policy"]["default"] == "retain"
    assert "--cleanup-remote" in request["remote_artifact_policy"]["cleanup_override"]
    assert "remote_dir" in request["expected_next_step"]
    assert result["workflow_result"]["remote_toolchain_request"] == "attempt-001/remote_toolchain_request.json", result
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        _expect_error(lambda: cli.main(["scaffold", "--target", "hls", "--name", "bad_path", "--out", "not-configured/spec.json"]), SystemExit, "2")
    assert set(generated_roots())


def _run_example_coverage(base: Path) -> None:
    pattern_expectations = {
        "hls_vector_scale_mock_spec.json": ["#pragma HLS PIPELINE", "bundle=gmem0"],
        "hls_axis_increment_spec.json": ["#pragma HLS INTERFACE axis", "hls::stream"],
        "hls_partition_vector_scale_spec.json": ["#pragma HLS ARRAY_PARTITION", "local_buf"],
        "hls_array_reshape_vector_scale_spec.json": ["#pragma HLS ARRAY_RESHAPE", "wide_buf"],
        "hls_dataflow_axis_spec.json": ["#pragma HLS DATAFLOW", "read_dataflow_axis_increment", "compute_dataflow_axis_increment", "write_dataflow_axis_increment", "#pragma HLS STREAM variable=mid_stream depth=16"],
        "hls_multi_m_axi_add_spec.json": ["bundle=gmem_a", "bundle=gmem_b", "bundle=gmem_out"],
        "hls_fixed_point_scale_spec.json": ["ap_fixed<16,8, AP_RND, AP_SAT>", "ap_fixed<16,4, AP_RND, AP_SAT>"],
    }
    for name, expected_markers in pattern_expectations.items():
        spec = _load_spec(name)
        run_dir = base / name.replace(".json", "")
        result = run_hls_workflow(spec, out_dir=run_dir, provider_name="mock", readiness="static", run_external=False)
        assert result["status"] == "passed", result
        artifact_dir = run_dir / "attempt-001" / "hls" / "artifacts"
        report = validate_hls_artifacts(spec, artifact_dir, readiness="static", run_external=False)
        assert report["ok"] is True, report
        assert report["errors"] == 0, report
        assert report["warnings"] == 0, report
        source_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted((artifact_dir / "src").glob("*.cpp")))
        for marker in expected_markers:
            assert marker in source_text, (name, marker, source_text)


def _run_copyright_gate_checks(base: Path) -> None:
    module = _load_script_module(ROOT / "scripts" / "confidence_loop.py", "confidence_loop_smoke")
    clean_scan = module._copyright_term_scan()
    assert clean_scan["status"] == "passed", clean_scan

    bad_root = base / "copyright-scan"
    (bad_root / "references").mkdir(parents=True)
    (bad_root / "references" / "scan_notes.md").write_text("source " + "off" + "icial" + " note\n", encoding="utf-8")
    (bad_root / ("tuto" + "rials")).mkdir()
    blocked_scan = module._copyright_term_scan(root=bad_root)
    assert blocked_scan["status"] == "failed", blocked_scan
    assert len(blocked_scan["matches"]) >= 2, blocked_scan

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "references/hls-optimization-patterns.md" in skill_text
    assert "references/hls-report-driven-optimization.md" in skill_text
    assert "references/hls-device-migration-strategy.md" in skill_text
    assert ("vitis-hls-" + "off" + "icial-patterns.md") not in skill_text


def _run_ug_reference_integration_checks(base: Path) -> None:
    optimization_text = (ROOT / "references" / "hls-optimization-patterns.md").read_text(encoding="utf-8")
    report_text = (ROOT / "references" / "hls-report-driven-optimization.md").read_text(encoding="utf-8")
    migration_text = (ROOT / "references" / "hls-device-migration-strategy.md").read_text(encoding="utf-8")
    modeling_text = (ROOT / "references" / "hls-modeling-strategy.md").read_text(encoding="utf-8")
    parallel_text = (ROOT / "references" / "hls-task-parallel-strategy.md").read_text(encoding="utf-8")
    assert "optimization class" in optimization_text.lower()
    assert "ii violation" in report_text.lower()
    assert "qor" in migration_text.lower()
    assert "variable-bound loops" in modeling_text.lower()
    assert "aliasing" in modeling_text.lower()
    assert "control-driven" in parallel_text.lower()
    assert "data-driven" in parallel_text.lower()
    assert "stable generated path" in parallel_text.lower()
    assert "vitis-hls-introductory-examples" not in modeling_text.lower()
    assert "vitis-hls-introductory-examples" not in parallel_text.lower()

    legacy = parse_hls_cfg_entries(
        """[HLS]
syn.top=legacy_kernel
syn.file=src/legacy.cpp
syn.file=src/legacy.h
tb.file=tb/legacy_tb.cpp
clock=10.0
part=xc7z020clg484-1
flow_target=vitis
"""
    )
    assert legacy["syn.top"] == "legacy_kernel"
    assert legacy["syn.files"] == ["src/legacy.cpp", "src/legacy.h"]
    assert legacy["tb.files"] == ["tb/legacy_tb.cpp"]
    assert legacy["clock"] == "10.0"

    ug_cfg = """[hls]
top=ug_kernel
part=xc7z020clg484-1
clock=8ns
flow_target=vitis

[files]
src=src/ug.cpp
src=src/ug.h
tb=tb/ug_tb.cpp
cflags=-O2 -std=c++17
csimflags=-lm

[compile]
pipeline_loops=64
enable_auto_rewind=true
pipeline_style=frp
unsafe_math_optimizations=false

[schedule]
enable_dsp_full_reg=true

[interface]
m_axi_addr64=true
m_axi_max_read_burst_length=32
default_slave_interface=s_axilite

[directive]
pipeline=ug_kernel/main_loop -II 1 -style frp -rewind
array_partition=ug_kernel/data_buf -type complete -dim 1
array_reshape=ug_kernel/wide_buf -type complete -dim 1
dataflow=ug_kernel -disable_start_propagation
interface=ug_kernel/in_stream -mode axis -bundle data

[csim]
clean=true
ldflags=-lm
argv=input.dat output.dat

[cosim]
rtl=verilog
tool=xsim
trace_level=all
wave_debug=true
random_stall=true
enable_tasks_with_m_axi=true

[export]
format=xo
rtl=verilog
vendor=xilinx.com
library=hls
version=1.0
display_name=UG Kernel
vivado_synth_strategy=Flow_Quick
ip_xdc_file=constraints/kernel.xdc
"""
    parsed = parse_hls_cfg_entries(ug_cfg)
    assert parsed["syn.top"] == "ug_kernel"
    assert parsed["syn.files"] == ["src/ug.cpp", "src/ug.h"]
    assert parsed["tb.files"] == ["tb/ug_tb.cpp"]
    assert parsed["files"]["cflags"] == "-O2 -std=c++17"
    assert parsed["files"]["csimflags"] == "-lm"
    assert parsed["clock"] == "8ns"
    assert parsed["flow_target"] == "vitis"
    assert parsed["compile"]["pipeline_loops"] == "64"
    assert parsed["schedule"]["enable_dsp_full_reg"] == "true"
    assert parsed["interface"]["m_axi_addr64"] == "true"
    assert parsed["directives"][0]["name"] == "pipeline"
    assert parsed["csim"]["ldflags"] == "-lm"
    assert parsed["csim"]["argv"] == "input.dat output.dat"
    assert parsed["cosim"]["random_stall"] == "true"
    assert parsed["cosim"]["enable_tasks_with_m_axi"] == "true"
    assert parsed["export"]["format"] == "xo"
    assert parsed["export"]["vivado_synth_strategy"] == "Flow_Quick"

    tcl, project_dir = render_vitis_hls_tcl(
        _load_spec(),
        base,
        parsed,
        "cosim",
        {"temp_tcl_prefix": ".test_", "project_dir_prefix": ".test_prj_", "solution_name": "sol_custom"},
    )
    assert project_dir.name.startswith(".test_prj_")
    assert "open_project -reset -flow_target vitis" in tcl
    assert "set_top {ug_kernel}" in tcl
    assert "add_files -cflags {-O2 -std=c++17}" in tcl and "src/ug.cpp" in tcl
    assert "add_files -tb -cflags {-O2 -std=c++17} -csimflags {-lm}" in tcl and "tb/ug_tb.cpp" in tcl
    assert "open_solution -reset -flow_target vitis {sol_custom}" in tcl
    assert "set_part {xc7z020clg484-1}" in tcl
    assert "create_clock -period 8ns" in tcl
    assert "config_compile -pipeline_loops 64 -enable_auto_rewind -pipeline_style frp" in tcl
    assert "config_schedule -enable_dsp_full_reg true" in tcl
    assert "config_interface -m_axi_addr64 true -m_axi_max_read_burst_length 32 -default_slave_interface s_axilite" in tcl
    assert "set_directive_pipeline -II 1 -style frp -rewind {ug_kernel/main_loop}" in tcl
    assert "set_directive_array_reshape -type complete -dim 1 {ug_kernel/wide_buf}" in tcl
    assert "config_csim -ldflags {-lm}" in tcl
    assert "config_cosim -enable_tasks_with_m_axi true" in tcl
    assert "csim_design -clean -argv {input.dat output.dat}" in tcl
    assert "csynth_design" in tcl
    assert "report_utilization -file ./report/sol_custom_utilization.rpt" in tcl
    assert "cosim_design -rtl verilog -tool xsim -trace_level all -wave_debug -random_stall" in tcl
    assert "config_export -vivado_synth_strategy {Flow_Quick} -ip_xdc_file {constraints/kernel.xdc}" in tcl
    assert "export_design -format xo -rtl verilog -vendor {xilinx.com} -library {hls} -version {1.0}" in tcl

    violations = scan_vitis_rule_violations(
        "config_sdx\nset_directive_data_pack top/a\n#pragma HLS DATA_PACK variable=x\n#include \"hls_linear_algebra.h\"\nint bad[limit];\n",
        path="bad.cpp",
        language="cpp",
    )
    messages = "\n".join(item["message"] for item in violations)
    assert "config_sdx" in messages
    assert "set_directive_data_pack" in messages
    assert "DATA_PACK" in messages
    assert "hls_linear_algebra.h" in messages
    assert "Variable-length" in messages

    conflict_violations = scan_vitis_rule_violations(
        "#pragma HLS ARRAY_PARTITION variable=buf complete dim=1\n#pragma HLS ARRAY_RESHAPE variable=buf complete dim=1\n",
        path="conflict.cpp",
        language="cpp",
    )
    conflict_messages = "\n".join(item["message"] for item in conflict_violations)
    assert "ARRAY_PARTITION" in conflict_messages and "ARRAY_RESHAPE" in conflict_messages, conflict_messages

    glob_cfg = parse_hls_cfg_entries(
        """[hls]
top=glob_kernel
clock=10ns
[files]
src=src/*.cpp
tb=tb/*_tb.cpp
"""
    )
    glob_tcl, _ = render_vitis_hls_tcl(
        {"name": "glob", "interfaces": {"top_function": "glob_kernel"}},
        base,
        glob_cfg,
        "compile",
        {"temp_tcl_prefix": ".test_", "project_dir_prefix": ".test_prj_", "solution_name": "solution1"},
    )
    assert "[glob -nocomplain" in glob_tcl
    assert "src/*.cpp" in glob_tcl

    bad_cfg = base / "bad_directive.cfg"
    bad_cfg.write_text("[hls]\ntop=bad\nclock=10ns\n[unknown]\nfoo=bar\n[compile]\nunsupported_option=true\n[directive]\nunsupported=bad/loop -x\n", encoding="utf-8")
    bad_cfg_entries = parse_hls_cfg_entries(bad_cfg.read_text(encoding="utf-8"))
    assert len(bad_cfg_entries["parse_errors"]) == 3, bad_cfg_entries
    assert any("unsupported_option" in item for item in bad_cfg_entries["parse_errors"])

    ug_artifacts = base / "ug-artifacts"
    (ug_artifacts / "src").mkdir(parents=True)
    (ug_artifacts / "tb").mkdir()
    (ug_artifacts / "src" / "vector_scale_kernel.h").write_text(
        "#pragma once\n#include <ap_int.h>\nvoid vector_scale_kernel(const ap_uint<32> *input, ap_uint<32> *output, ap_uint<16> scale, int length);\n",
        encoding="utf-8",
    )
    (ug_artifacts / "src" / "vector_scale_kernel.cpp").write_text(
        """#include "vector_scale_kernel.h"
void vector_scale_kernel(const ap_uint<32> *input, ap_uint<32> *output, ap_uint<16> scale, int length) {
  // 中文注释: 接口协议和流水线约束。
  #pragma HLS INTERFACE mode=m_axi port=input bundle=gmem0
  #pragma HLS INTERFACE mode=m_axi port=output bundle=gmem1
  #pragma HLS INTERFACE mode=s_axilite port=scale
  #pragma HLS INTERFACE mode=s_axilite port=length
  #pragma HLS INTERFACE mode=s_axilite port=return
  #pragma HLS PIPELINE II=1
  for (int i = 0; i < length; ++i) {
    output[i] = input[i] * scale;
  }
}
""",
        encoding="utf-8",
    )
    (ug_artifacts / "tb" / "vector_scale_kernel_tb.cpp").write_text(
        """#include "../src/vector_scale_kernel.h"
#include <iostream>
int main() {
  // case_nominal PASS FAIL
  // case_boundary PASS FAIL
  ap_uint<32> input[1] = {1};
  ap_uint<32> output[1] = {0};
  vector_scale_kernel(input, output, ap_uint<16>(2), 1);
  std::cout << "PASS\\n";
  return output[0] == 2 ? 0 : 1;
}
""",
        encoding="utf-8",
    )
    (ug_artifacts / "hls_config.cfg").write_text(ug_cfg.replace("ug_kernel", "vector_scale_kernel").replace("src/ug.cpp", "src/vector_scale_kernel.cpp").replace("src/ug.h", "src/vector_scale_kernel.h").replace("tb/ug_tb.cpp", "tb/vector_scale_kernel_tb.cpp").replace("8ns", "10.0"), encoding="utf-8")
    ug_report = validate_hls_artifacts(_load_spec(), ug_artifacts, run_external=False, readiness="static")
    assert ug_report["ok"] is True, ug_report

    missing_tb_cfg_artifacts = base / "missing-tb-cfg"
    shutil.copytree(ug_artifacts, missing_tb_cfg_artifacts)
    missing_tb_cfg = missing_tb_cfg_artifacts / "hls_config.cfg"
    missing_tb_cfg.write_text(
        "\n".join(line for line in missing_tb_cfg.read_text(encoding="utf-8").splitlines() if "tb=tb/vector_scale_kernel_tb.cpp" not in line) + "\n",
        encoding="utf-8",
    )
    missing_tb_report = validate_hls_artifacts(_load_spec(), missing_tb_cfg_artifacts, run_external=False, readiness="static")
    assert missing_tb_report["ok"] is False, missing_tb_report
    missing_tb_messages = "\n".join(issue["message"] for issue in missing_tb_report["issues"])
    assert "tb.file" in missing_tb_messages

    mismatch_artifacts = base / "cfg-mismatch"
    shutil.copytree(ug_artifacts, mismatch_artifacts)
    (mismatch_artifacts / "hls_config.cfg").write_text(
        (mismatch_artifacts / "hls_config.cfg").read_text(encoding="utf-8").replace("clock=10.0", "clock=8ns").replace("flow_target=vitis", "flow_target=vivado"),
        encoding="utf-8",
    )
    mismatch_spec = _load_spec()
    mismatch_spec["workflow"] = {"part": "xcvu9p-flga2104-2-i", "flow_target": "vitis"}
    mismatch_report = validate_hls_artifacts(mismatch_spec, mismatch_artifacts, run_external=False, readiness="static")
    assert mismatch_report["ok"] is False, mismatch_report
    mismatch_messages = "\n".join(issue["message"] for issue in mismatch_report["issues"])
    assert "clock=8.0" in mismatch_messages
    assert "flow_target='vivado'" in mismatch_messages
    assert "part='xc7z020clg484-1'" in mismatch_messages

    bad_artifacts = base / "bad-vitis-rules"
    shutil.copytree(ug_artifacts, bad_artifacts)
    (bad_artifacts / "src" / "vector_scale_kernel.cpp").write_text(
        (bad_artifacts / "src" / "vector_scale_kernel.cpp").read_text(encoding="utf-8")
        + "\n#pragma HLS DATA_PACK variable=input\nconfig_sdx\n#include \"hls_linear_algebra.h\"\n",
        encoding="utf-8",
    )
    bad_report = validate_hls_artifacts(_load_spec(), bad_artifacts, run_external=False, readiness="static")
    assert bad_report["ok"] is False, bad_report
    bad_messages = "\n".join(issue["message"] for issue in bad_report["issues"])
    assert "DATA_PACK" in bad_messages
    assert "config_sdx" in bad_messages
    assert "hls_linear_algebra.h" in bad_messages

    invalid_mode_artifacts = base / "invalid-interface-mode"
    shutil.copytree(ug_artifacts, invalid_mode_artifacts)
    source_path = invalid_mode_artifacts / "src" / "vector_scale_kernel.cpp"
    source_path.write_text(source_path.read_text(encoding="utf-8").replace("mode=m_axi port=input", "mode=bad_mode port=input"), encoding="utf-8")
    invalid_mode_report = validate_hls_artifacts(_load_spec(), invalid_mode_artifacts, run_external=False, readiness="static")
    assert invalid_mode_report["ok"] is False, invalid_mode_report
    invalid_mode_messages = "\n".join(issue["message"] for issue in invalid_mode_report["issues"])
    assert "bad_mode" in invalid_mode_messages

    tb_vla_artifacts = base / "tb-vla-ok"
    shutil.copytree(ug_artifacts, tb_vla_artifacts)
    tb_path = tb_vla_artifacts / "tb" / "vector_scale_kernel_tb.cpp"
    tb_path.write_text(tb_path.read_text(encoding="utf-8").replace("int main() {", "int main() {\n  int runtime_len = 1;\n  int tb_only[runtime_len];\n  tb_only[0] = 0;"), encoding="utf-8")
    tb_vla_report = validate_hls_artifacts(_load_spec(), tb_vla_artifacts, run_external=False, readiness="static")
    assert tb_vla_report["ok"] is True, tb_vla_report

    unsafe_cfg_artifacts = base / "unsafe-cfg-path"
    shutil.copytree(ug_artifacts, unsafe_cfg_artifacts)
    unsafe_cfg = unsafe_cfg_artifacts / "hls_config.cfg"
    unsafe_cfg.write_text(unsafe_cfg.read_text(encoding="utf-8") + "\nsyn.file=../escape.cpp\ntb.file=C:/escape_tb.cpp\n", encoding="utf-8")
    unsafe_report = validate_hls_artifacts(_load_spec(), unsafe_cfg_artifacts, run_external=False, readiness="static")
    assert unsafe_report["ok"] is False, unsafe_report
    unsafe_messages = "\n".join(issue["message"] for issue in unsafe_report["issues"])
    assert "../escape.cpp" in unsafe_messages
    assert "C:/escape_tb.cpp" in unsafe_messages


def _run_confidence_loop_checks(base: Path) -> None:
    output = base / "confidence" / "result.json"
    result = REAL_SUBPROCESS_RUN(
        [
            sys.executable,
            "scripts/confidence_loop.py",
            "--skip-smoke",
            "--skip-compileall",
            "--skip-quick-validate",
            "--skip-remote",
            "--json-out",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["confidence_status"] == "local_high_confidence", payload
    assert payload["confidence_scope"] == "local", payload
    assert payload["gates"]["copyright_term_scan"]["status"] == "passed", payload
    assert "remote_vitis_acceptance" not in payload["gates"], payload
    assert "Final confidence requires remote Vitis acceptance." in payload["residual_risks"], payload
    assert "hls_array_reshape_vector_scale_spec.json" in payload["example_specs"], payload


def _run_release_packaging_checks(base: Path) -> None:
    script = ROOT / "scripts" / "prepare_release.py"
    no_version = REAL_SUBPROCESS_RUN(
        [sys.executable, str(script)],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert no_version.returncode != 0 and "--version" in no_version.stderr, no_version

    invalid_version = REAL_SUBPROCESS_RUN(
        [sys.executable, str(script), "--version", "not-a-version", "--dist-root", str(base / "release-dist-invalid")],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert invalid_version.returncode != 0 and "SemVer" in invalid_version.stderr, invalid_version

    mismatch = REAL_SUBPROCESS_RUN(
        [sys.executable, str(script), "--version", "9.9.9", "--dist-root", str(base / "release-dist-mismatch")],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert mismatch.returncode != 0 and "does not match release version" in mismatch.stderr, mismatch

    dist_root = base / "release-dist"
    valid = REAL_SUBPROCESS_RUN(
        [sys.executable, str(script), "--version", "0.1.7", "--dist-root", str(dist_root)],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert valid.returncode == 0, valid
    payload = json.loads(valid.stdout)
    release_dir = Path(payload["release_dir"])
    zip_path = Path(payload["zip_path"])
    assert release_dir.exists() and zip_path.exists(), payload
    assert (release_dir / "skills" / "erie-hls-generator" / "SKILL.md").exists()
    assert not (release_dir / "ref").exists()
    assert not (release_dir / "skills" / "erie-hls-generator" / "reports").exists()
    assert not list(release_dir.rglob("__pycache__"))
    assert (release_dir / "RELEASE_MANIFEST.json").exists()
    assert (release_dir / "checksums.sha256").exists()
    _assert_release_markdown_ascii(release_dir, zip_path)

    directory_files = {path.relative_to(release_dir).as_posix() for path in release_dir.rglob("*") if path.is_file()}
    with zipfile.ZipFile(zip_path) as archive:
        zipped_files = {
            Path(name).relative_to(release_dir.name).as_posix()
            for name in archive.namelist()
            if not name.endswith("/")
        }
    assert zipped_files == directory_files, (zipped_files ^ directory_files)


def _assert_release_markdown_ascii(release_dir: Path, zip_path: Path) -> None:
    for path in release_dir.rglob("*.md"):
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), path
        text = data.decode("utf-8")
        assert text.isascii(), path
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if name.endswith(".md"):
                data = archive.read(name)
                assert not data.startswith(b"\xef\xbb\xbf"), name
                text = data.decode("utf-8")
                assert text.isascii(), name


def _assert_hls_artifacts(artifact_dir: Path) -> None:
    spec = _load_spec()
    source = (artifact_dir / "src" / "vector_scale_kernel.cpp").read_text(encoding="utf-8")
    header = (artifact_dir / "src" / "vector_scale_kernel.h").read_text(encoding="utf-8")
    testbench = (artifact_dir / "tb" / "vector_scale_kernel_tb.cpp").read_text(encoding="utf-8")
    cfg = (artifact_dir / "hls_config.cfg").read_text(encoding="utf-8")
    vectors = _mock_vectors(spec)
    for path in [
        artifact_dir / "src" / "vector_scale_kernel.h",
        artifact_dir / "src" / "vector_scale_kernel.cpp",
        artifact_dir / "tb" / "vector_scale_kernel_tb.cpp",
        artifact_dir / "hls_config.cfg",
    ]:
        assert path.exists(), path
    assert "void vector_scale_kernel(" in header
    assert "#pragma HLS INTERFACE m_axi port=input bundle=gmem0" in source
    assert "#pragma HLS INTERFACE m_axi port=output bundle=gmem1" in source
    assert "depth=" in source
    assert "ap_uint<32> input[1024]" in testbench
    assert "ap_uint<32> output[1024]" in testbench
    assert "#pragma HLS INTERFACE s_axilite port=return" in source
    assert "#pragma HLS PIPELINE" in source
    assert cfg.startswith("[HLS]\n")
    assert "syn.top=vector_scale_kernel" in cfg
    assert "syn.file=src/vector_scale_kernel.cpp" in cfg
    assert "syn.file=src/vector_scale_kernel.h" in cfg
    assert "int main(" in testbench
    assert "vector_scale_kernel(" in testbench
    assert REFERENCE_RESULT_TAG in testbench
    assert "PASS" in testbench and "FAIL" in testbench
    assert VECTOR_HASH_TAG in testbench
    for case in vectors:
        assert case["id"] in testbench


def _install_hls_mocks(tool: str | None) -> None:
    HLS_TOOL_COMMANDS.clear()
    HLS_TCL_TEXTS.clear()

    def which(name: str) -> str | None:
        for item in VITIS_TOOL_CONFIGS:
            if tool == item["name"] and name == item.get("which", item["name"]):
                return str(_current_smoke_base() / "_mock_tools" / name)
        return None

    validation.shutil.which = which
    validation.subprocess.run = _happy_hls_run_tool


def _happy_hls_run_tool(*args, **kwargs):
    command = [str(item) for item in args[0]]
    if command[0] == sys.executable:
        return SimpleNamespace(returncode=0, stdout="PASS\n", stderr="")
    HLS_TOOL_COMMANDS.append(command)
    if "--tcl" in command:
        tcl_path = Path(command[command.index("--tcl") + 1])
        HLS_TCL_TEXTS.append(tcl_path.read_text(encoding="utf-8"))
    if "-f" in command:
        tcl_path = Path(command[command.index("-f") + 1])
        HLS_TCL_TEXTS.append(tcl_path.read_text(encoding="utf-8"))
    return SimpleNamespace(returncode=0, stdout=_semantic_transcript(), stderr="")


def _semantic_transcript() -> str:
    lines: list[str] = []
    for item in _mock_vectors(_load_spec()):
        payload = {
            "case_id": item["id"],
            "status": "PASS",
            "outputs": item.get("expected_outputs", {}),
            "checkpoints": item.get("checkpoints", {}),
        }
        lines.append(f"{REFERENCE_RESULT_TAG} {json.dumps(payload, separators=(',', ':'))}")
    return "\n".join(lines) + "\n"


def _joined_commands() -> list[str]:
    return [" ".join(command) for command in HLS_TOOL_COMMANDS]


def _current_smoke_base() -> Path:
    if CURRENT_SMOKE_BASE is None:
        raise AssertionError("Smoke base directory was not initialized.")
    return CURRENT_SMOKE_BASE


def _smoke_relative_path(*parts: str) -> Path:
    return Path(smoke_root_name()) / _current_smoke_base().name / Path(*parts)


def _load_script_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expect_error(func, exc_type: type[BaseException], text: str) -> None:
    try:
        func()
    except exc_type as exc:
        assert text in str(exc), exc
    else:
        raise AssertionError(f"Expected {exc_type.__name__} containing {text!r}.")


if __name__ == "__main__":
    raise SystemExit(main())
