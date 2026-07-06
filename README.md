<p align="center">
  <a href="README.md"><strong>English</strong></a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md">中文</a>
</p>

<p align="center">
  <img src="docs/assets/hero.svg" alt="HLS Generator" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-1f6feb"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2f81f7">
  <img alt="Version" src="https://img.shields.io/badge/version-v0.2.6-7c3aed">
  <a href="SKILL.md"><img alt="Agent Skill" src="https://img.shields.io/badge/agent-skill-16a34a"></a>
  <a href="references/vitis-hls-2024-2-script-guide.md"><img alt="Target" src="https://img.shields.io/badge/target-Vitis%20HLS-f59e0b"></a>
</p>

<h1 align="center">HLS Generator</h1>

<p align="center">
  A Codex-ready skill for structured AMD/Xilinx Vitis HLS workflows.
</p>

HLS Generator is a public skill repository for HLS task routing, prompt scaffolding, artifact validation, readability governance, and remote acceptance support around AMD/Xilinx Vitis HLS work.

## What It Is For

Use this repository when an agent needs help with:

- Vitis HLS C/C++ kernels, headers, and testbenches.
- AXI memory, AXI4-Stream, native scalar, and custom interface contracts.
- `PIPELINE`, `DATAFLOW`, `ARRAY_PARTITION`, `STREAM`, and related pragma decisions.
- HLS configuration, Tcl rendering, report collection, and toolchain readiness checks.
- Debugging HLS-generated RTL issues that trace back to HLS code, pragmas, configuration, or reports.

## Install

Tell your AI assistant: install [https://github.com/Eriemon/hls-generator](https://github.com/Eriemon/hls-generator)

Manual setup:

```powershell
git clone https://github.com/Eriemon/hls-generator.git
cd .\hls-generator
```

For Codex usage, place the repository in the host skill search path and restart the host after installation.

## Quick Start

Use the public CLI entry from the repository root:

```powershell
python -m scripts.python.cli.hls_generator --version
python -m scripts.python.cli.hls_generator config --path
python -m scripts.python.cli.hls_generator deps check --json
python -m scripts.python.cli.hls_generator scaffold --target hls --name vector_scale --out .\out\hls\spec.json
python -m scripts.python.cli.hls_generator prompt --target hls --spec .\out\hls\spec.json --out .\out\hls\prompt.md --confirm-requirements --confirmation-notes "user-confirmed HLS contract"
python -m scripts.python.cli.hls_generator validate --target hls --spec .\out\hls\spec.json --path .\out\hls\generated --readiness static --no-external
python -m scripts.python.cli.hls_generator readability-gate --target hls --path .\out\hls\generated --profile kernel --style current-project --json
```

If you are doing comment-only HLS rewrites, keep a baseline tree and pass `--baseline-path` to validation and readability checks so token and AST equivalence are verified before accepting the rewrite.

## Repository Map

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Agent-facing routing, workflow, constraints, and reference-loading rules. |
| `agents/openai.yaml` | Skill metadata for host UIs. |
| `assets/examples/` | Reusable structured HLS specs and minimal examples. |
| `assets/templates/` | Reusable HLS JSON template families. |
| `assets/validation-board/` | Board-side payload helpers used by remote validation flows. |
| `references/` | On-demand policy, workflow, optimization, configuration, and remote-validation guidance. |
| `scripts/python/cli/` | Public CLI entry implementation. |
| `scripts/python/config/` | Runtime configuration, dependency manifests, and version truth. |
| `scripts/python/generation/` | Prompt, scaffold, and artifact generation helpers. |
| `scripts/python/hls_quality_gate/` | HLS readability and semantic gate logic. |
| `scripts/python/integration/` | Stable local facade used by other tools and scripts. |
| `scripts/python/quality_gate/` | Public Python-quality gate wrappers. |
| `scripts/python/release/` | Release rebuild and packaging helpers. |
| `scripts/python/remote/` | Remote Vitis and board-acceptance helpers. |
| `scripts/python/task_dispatcher/` | Request classification and workflow entry wrappers. |
| `scripts/python/validation/` | Local confidence, artifact, and readiness validation helpers. |
| `scripts/python/workflow/` | Staged HLS workflow orchestration. |

## v0.2.6 Notes

- The public entry is now `python -m scripts.python.cli.hls_generator ...`.
- The old public compatibility layer is no longer published: `runtime/`, the old top-level `integration/`, `pyproject.toml`, and `VERSION` were removed from the public repository interface.
- Version truth is unified in `scripts/python/config/version.py`.

## Skill Architecture

<p align="center">
  <img src="docs/assets/architecture.svg" alt="HLS Generator skill architecture" width="100%">
</p>

## Workflow

<p align="center">
  <img src="docs/assets/workflow.svg" alt="HLS Generator workflow" width="100%">
</p>

## Notes

- `references/remote-board-platform-upload.md` uses `<REDACTED_LOCAL_PATH>` in examples instead of real local filesystem paths.
- Local-only files such as `.settings/`, `*.local.json`, `*.remote.json`, reports, tests, smoke outputs, and caches are not part of the published repository payload or release zip.
- Vitis execution should only be claimed when `vitis-run` or `vitis_hls` actually ran.

## Scope

- Generates and validates Vitis HLS-oriented artifacts, not handwritten RTL.
- Keeps HLS-generated RTL debug in scope only when issues trace back to HLS code, pragmas, configuration, or reports.
- Uses remote acceptance helpers when local Vitis tooling is unavailable instead of weakening validation claims.

## Authors And Affiliation

HLS Generator is maintained by Jiyuan Liu and He Li.

The authors are with the School of Electronic Science and Engineering, Southeast University, and are affiliated with the Heterogeneous Intelligence and Quantum Computing Laboratory (HIQC).

## Contact

For questions, collaboration, or academic use, contact: [erie@seu.edu.cn](mailto:erie@seu.edu.cn).

## Citation

If this skill helps your research, teaching, or engineering workflow, please cite it:

```bibtex
@software{liu_2026_hls_generator,
  author       = {Jiyuan Liu and He Li},
  title        = {{HLS Generator}: An Agent Skill for Vitis HLS Workflows},
  year         = {2026},
  version      = {0.2.6},
  date         = {2026-07-06},
  url          = {https://github.com/Eriemon/hls-generator},
  license      = {Apache-2.0},
  note         = {Agent skill package for structured AMD/Xilinx Vitis HLS workflows}
}
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
