<p align="center">
  <a href="README.md">English</a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md"><strong>中文</strong></a>
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
  面向 Codex/Agent 的结构化 AMD/Xilinx Vitis HLS 工作流 Skill。
</p>

HLS Generator 是一个公开 Skill 仓库，面向 HLS 任务分流、prompt scaffold、产物校验、可读性治理，以及围绕 AMD/Xilinx Vitis HLS 的远程验收辅助。

## 适用场景

当 Agent 需要处理下面这些事情时，可以使用这个仓库：

- Vitis HLS C/C++ kernel、头文件和 testbench。
- AXI memory、AXI4-Stream、native scalar 和自定义接口契约。
- `PIPELINE`、`DATAFLOW`、`ARRAY_PARTITION`、`STREAM` 等 pragma 决策。
- HLS 配置、Tcl 渲染、报告收集和工具链就绪检查。
- 能回溯到 HLS 代码、pragma、配置或报告的 HLS 生成 RTL 问题。

## 安装

直接告诉你的 AI 助手安装 [https://github.com/Eriemon/hls-generator](https://github.com/Eriemon/hls-generator)

手动准备方式：

```powershell
git clone https://github.com/Eriemon/hls-generator.git
cd .\hls-generator
```

如果要作为 Codex skill 使用，把仓库放入宿主 skill 搜索路径后重启宿主。

## 快速开始

从仓库根目录使用公开 CLI 入口：

```powershell
python -m scripts.python.cli.hls_generator --version
python -m scripts.python.cli.hls_generator config --path
python -m scripts.python.cli.hls_generator deps check --json
python -m scripts.python.cli.hls_generator scaffold --target hls --name vector_scale --out .\out\hls\spec.json
python -m scripts.python.cli.hls_generator prompt --target hls --spec .\out\hls\spec.json --out .\out\hls\prompt.md --confirm-requirements --confirmation-notes "user-confirmed HLS contract"
python -m scripts.python.cli.hls_generator validate --target hls --spec .\out\hls\spec.json --path .\out\hls\generated --readiness static --no-external
python -m scripts.python.cli.hls_generator readability-gate --target hls --path .\out\hls\generated --profile kernel --style current-project --json
```

如果只是做注释改写，请保留 baseline 目录，并在校验和可读性检查中传入 `--baseline-path`，先完成 token 和 AST 等价验证，再接受改写结果。

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `SKILL.md` | 面向 Agent 的路由、流程、约束与参考文档加载规则。 |
| `agents/openai.yaml` | Skill 的宿主 UI 元数据。 |
| `assets/examples/` | 可复用的结构化 HLS 规格与最小示例。 |
| `assets/templates/` | 可复用的 HLS JSON 模板家族。 |
| `assets/validation-board/` | 远程板卡验证流程使用的板端载荷辅助文件。 |
| `references/` | 按需加载的策略、工作流、优化、配置和远程验证说明。 |
| `scripts/python/cli/` | 公开 CLI 入口实现。 |
| `scripts/python/config/` | runtime 配置、依赖清单与版本真值。 |
| `scripts/python/generation/` | prompt、scaffold 和产物生成辅助。 |
| `scripts/python/hls_quality_gate/` | HLS 可读性与语义门禁实现。 |
| `scripts/python/integration/` | 供其他工具和脚本复用的稳定本地 facade。 |
| `scripts/python/quality_gate/` | 公开 Python 质量门包装器。 |
| `scripts/python/release/` | release 重建与打包辅助。 |
| `scripts/python/remote/` | 远程 Vitis 与板卡验收辅助。 |
| `scripts/python/task_dispatcher/` | 请求分类与工作流入口包装器。 |
| `scripts/python/validation/` | 本地 confidence、产物与 readiness 校验辅助。 |
| `scripts/python/workflow/` | 分阶段 HLS 工作流编排。 |

## v0.2.6 说明

- 当前公开入口是 `python -m scripts.python.cli.hls_generator ...`。
- 旧公开兼容层已经下线：`runtime/`、旧顶层 `integration/`、`pyproject.toml` 和 `VERSION` 不再属于公开仓库接口。
- 版本真值统一收口到 `scripts/python/config/version.py`。

## Skill 架构

<p align="center">
  <img src="docs/assets/architecture-cn.svg" alt="HLS Generator Skill 架构" width="100%">
</p>

## 工作流

<p align="center">
  <img src="docs/assets/workflow-cn.svg" alt="HLS Generator 工作流" width="100%">
</p>

## 使用注意事项

- `references/remote-board-platform-upload.md` 里的示例使用 `<REDACTED_LOCAL_PATH>`，不会放入真实本地路径。
- `.settings/`、`*.local.json`、`*.remote.json`、reports、tests、smoke 输出和缓存这类本地专用文件不属于公开仓库载荷，也不会放进 release zip。
- 只有实际运行 `vitis-run` 或 `vitis_hls` 后，才应该声称完成了 Vitis 执行验证。

## 范围

- 负责生成和验证面向 Vitis HLS 的产物，不负责手写 RTL。
- HLS 生成 RTL 的调试仅在问题能回溯到 HLS 代码、pragma、配置或报告时属于本 Skill 范围。
- 当本地缺少 Vitis 工具时，优先走远程验收辅助，而不是降低验证口径。

## 作者与机构

HLS Generator 由 Jiyuan Liu 和 He Li 维护。

两位作者隶属于东南大学电子科学与工程学院，并与东南大学异构智能与量子计算实验室（HIQC）相关。

## 联系方式

问题、合作或学术使用，请联系：[erie@seu.edu.cn](mailto:erie@seu.edu.cn)。

## 引用

如果这个 skill 对你的研究、教学或工程流程有帮助，请引用：

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

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
