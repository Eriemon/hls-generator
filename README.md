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

## What Changed From v0.2.3 To v0.2.6

- The public entry moved from the old `runtime.*` layer to `python -m scripts.python.cli.hls_generator ...`.
- The repository layout now follows the zip payload structure: Python implementation is split by function under `scripts/python/*`, with dedicated `cli`, `config`, `generation`, `hls_quality_gate`, `integration`, `remote`, `task_dispatcher`, `validation`, and `workflow` domains.
- The old public compatibility layer is gone from the repository and release package. `runtime/`, `integration/`, `pyproject.toml`, and `VERSION` are no longer published as the public interface.
- Version truth is now unified in `scripts/python/config/version.py`; the README, rebuilt release zip, git tag, and GitHub release all track `0.2.6` / `v0.2.6`.
- Release assets are rebuilt from the updated repository by `scripts/python/release/prepare_release.py` instead of trusting a prepacked upstream archive.
- Public-boundary rules remain strict: `<REDACTED_LOCAL_PATH>` stays redacted, and local-only validation data such as `.settings/`, `*.local.json`, `*.remote.json`, `reports/`, `tests/`, `smoke*`, caches, and private server fingerprints stay out of both the repository and the release zip.

## Skill Architecture

<p align="center">
  <img src="docs/assets/architecture.svg" alt="HLS Generator skill architecture" width="100%">
</p>

## Workflow

<p align="center">
  <img src="docs/assets/workflow.svg" alt="HLS Generator workflow" width="100%">
</p>

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

The old public entrypoints are retired. Do not rely on `python -m runtime.hls_generator`, `hls-gen`, or `pip install` metadata from older releases as the public integration contract for `v0.2.6`.

## Release Rebuild

Rebuild the public release asset from the updated repository:

```powershell
python .\scripts\python\release\prepare_release.py --version 0.2.6
```

That command rebuilds `dist/erie-hls-generator-v0.2.6/` and `dist/erie-hls-generator-v0.2.6.zip`. The rebuilt asset is the release truth for `v0.2.6`.

## Public Repository Boundary

- Public tracked content is limited to the skill payload, release-required metadata, and user-facing documentation.
- The redacted placeholder `<REDACTED_LOCAL_PATH>` remains in `references/remote-board-platform-upload.md`; real local filesystem paths must not appear in the public repository or release package.
- `.settings/`, `*.local.json`, `*.remote.json`, `reports/`, `tests/`, `smoke*`, caches, and similar local-only artifacts must stay out of the public repository and rebuilt release zip.
- External Vitis execution is only claimed when `vitis-run` or `vitis_hls` actually runs.

## Scope

- Generates and validates Vitis HLS-oriented artifacts, not handwritten RTL.
- Keeps HLS-generated RTL debug in scope only when issues trace back to HLS code, pragmas, configuration, or reports.
- Uses remote acceptance helpers when local Vitis tooling is unavailable instead of weakening validation claims.

## Contact

For questions, collaboration, or academic use, contact: [erie@seu.edu.cn](mailto:erie@seu.edu.cn).

## License

Apache License 2.0. See [LICENSE](LICENSE).
