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
  <a href="pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2f81f7"></a>
  <img alt="Version" src="https://img.shields.io/badge/version-v0.2.3-7c3aed">
  <a href="SKILL.md"><img alt="Agent Skill" src="https://img.shields.io/badge/agent-skill-16a34a"></a>
  <a href="references/vitis-hls-2024-2-script-guide.md"><img alt="Target" src="https://img.shields.io/badge/target-Vitis%20HLS-f59e0b"></a>
</p>

<h1 align="center">HLS Generator</h1>

<p align="center">
  面向 Codex/Agent 的 AMD/Xilinx Vitis HLS 专业工作流 Skill。
</p>

HLS Generator 用来把 AI 编程代理变成更可靠的 HLS 工程助手。它提供触发元数据、工作流指令、参考文档、确定性 runtime、示例规格和验证门禁，帮助 Agent 从确认后的硬件意图稳定推进到可审查的 Vitis HLS 产物。

这个仓库首先是一个 **Agent Skill Package**。Python CLI 是确定性执行层，但主要入口是 Agent 可加载、可遵循的 skill 结构。

## 为什么需要它

硬件生成最容易出错的地方，是 Agent 从模糊需求直接跳到代码。HLS Generator 在中间补上工程化步骤：需求确认、接口契约、分阶段规划、测试向量、Python reference 检查、HLS 产物抽取和验证证据。

适用场景包括：

- Vitis HLS C/C++ kernel、头文件和 testbench。
- AXI memory、AXI4-Stream、native scalar 和自定义接口契约。
- `PIPELINE`、`DATAFLOW`、`ARRAY_PARTITION`、`STREAM` 等 pragma 决策。
- HLS 配置、Tcl 渲染、报告收集和工具链就绪检查。
- 调试 HLS 生成 RTL 的接口问题，并回溯到 HLS 源码、pragma、配置或报告。

## Skill 架构

<p align="center">
  <img src="docs/assets/architecture-cn.svg" alt="HLS Generator Skill 架构" width="100%">
</p>

## 工作流

<p align="center">
  <img src="docs/assets/workflow-cn.svg" alt="HLS Generator 工作流" width="100%">
</p>

## v0.2.3 重点更新

- 新增 HLS 请求分流能力，可区分 `generate`、`modify`、`explain` 三类路径；其中 comment-only 修改现在必须提供 baseline 目录，先做对照再允许通过。
- 新增可读性与注释安全门禁栈，包括 `readability-gate`、comment-only 的 token/AST 保护、命名/pragma/结构检查，以及 `runtime/hls_generator/readability_gate/` 与 `scripts/python/quality_gate/` 下的公开包装入口。
- 扩展确定性 mock 生成能力，补入更完整的 mock HLS 产物、mock vectors 和 comment rendering，提升本地验证、审查和工作流 dry-run 的覆盖面。
- 新增 `scripts/python/task_dispatcher/` 下的任务调度与工作流包装脚本，并补入 `references/hls_dispatcher.md`、`references/hls_readability_gate.md`、`references/style/` 等说明文档。
- 继续保持公开仓库边界：远程板卡平台上传说明保留 `<REDACTED_LOCAL_PATH>` 脱敏占位符，本地验证资产、设置文件、缓存和 smoke 产物不进入公开仓库和重建 release。

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `SKILL.md` | 面向 Agent 的触发、流程、约束和工具使用规则。 |
| `agents/openai.yaml` | Skill 列表和调用入口的 UI 元数据。 |
| `runtime/hls_generator/` | scaffold、prompt 渲染、抽取、验证、报告和 workflow 状态。 |
| `runtime/hls_generator/readability_gate/` | HLS 可读性、命名、pragma、结构和 AST 支撑检查模块。 |
| `integration/hls_adapter.py` | 面向宿主应用的稳定接口。 |
| `assets/examples/` | stream、memory、dataflow、partition、reshape、fixed-point、multi-`m_axi` 等 HLS spec 示例。 |
| `assets/templates/` | 常见 kernel 家族与面向板卡场景的可复用结构化 HLS JSON 模板。 |
| `assets/validation-board/` | 远程验证所需的板端 host 模板与辅助载荷。 |
| `references/` | Vitis HLS 策略、分流规则、可读性指南、工作流契约、集成说明、风格覆盖和模板家族目录。 |
| `scripts/python/quality_gate/` | 公开可用的 HLS/Python 质量门包装器与规则执行入口。 |
| `scripts/python/task_dispatcher/` | HLS 与相关 Python 审查流程的请求分类与工作流包装入口。 |
| `scripts/python/` | 公开 skill 仓库提供的 curation、release、governance、validation、inspection、任务分流与 remote acceptance 辅助脚本。 |

## 安装

直接告诉你的AI让他安装 https://github.com/Eriemon/hls-generator

手动安装方式：

```powershell
git clone https://github.com/Eriemon/hls-generator.git
cd .\hls-generator
python -m pip install -e .
```

如果要作为 Codex skill 使用，把仓库放进宿主的 skill 搜索路径后重启宿主。

## 快速开始

把本仓库放入 Codex skill 搜索路径即可作为 Agent Skill 使用。开发 runtime 或做本地检查时：

```powershell
python -m runtime.hls_generator --version
python -m runtime.hls_generator config --path
python -m runtime.hls_generator deps check --json
python -m runtime.hls_generator scaffold --target hls --name vector_scale --out .\reports\hls\spec.json
python -m runtime.hls_generator prompt --target hls --spec .\reports\hls\spec.json --out .\reports\hls\prompt.md --confirm-requirements --confirmation-notes "user-confirmed HLS contract"
python -m runtime.hls_generator readability-gate --target hls --path .\reports\hls\generated --profile kernel --style current-project --json
```

首次使用时，依赖检查会阻塞缺失的 required 或 recommended Codex skills。运行 `python -m runtime.hls_generator deps install --all` 前需要先获得用户确认，安装后重启 Codex 以加载新的 skill metadata。

不依赖 AMD/Xilinx 外部工具的静态验证：

```powershell
python -m runtime.hls_generator validate --target hls --spec .\reports\hls\spec.json --path .\reports\hls\generated --readiness static --no-external
```

如果是只改注释的 HLS 文件，必须同时保留 baseline 目录，并在 `readability-gate` 与 `validate` 中传入 `--baseline-path`，让 token/AST 等价保护先完成再接受改写。

工作区私有的 smoke、单元和 confidence 验证资产放在公开仓库之外的 `tmp/validation/hls-generator/`。

## 公开仓库边界

- 公开跟踪内容只保留 skill payload、公开元数据，以及安装和使用所需的用户文档。
- 本地 smoke 资产、私有验证载荷、`.settings/`、`*.local.json`、`*.remote.json`、缓存、`reports/`、`tests/`、`smoke*` 等本地专用内容不会进入公开仓库和重建 release zip。
- 涉及远程路径的说明继续做脱敏处理，例如 `references/remote-board-platform-upload.md` 保留 `<REDACTED_LOCAL_PATH>`，不会写入真实本地文件系统路径。

外部验证需要真实 Vitis HLS 环境。只有实际运行 `vitis-run` 或 `vitis_hls` 后，才可以声称 Vitis 验证通过。

## 集成接口

```python
from integration.hls_adapter import (
    render_hls_prompt,
    run_hls_workflow,
    validate_hls_artifacts,
)
```

- `run_hls_workflow(...)`：运行或恢复分阶段 HLS 工作流。
- `render_hls_prompt(...)`：宿主系统自行调用模型时渲染 prompt。
- `validate_hls_artifacts(...)`：下游使用前验证生成产物。

## 边界

- 生成 Vitis HLS C/C++ 产物，不生成手写 RTL。
- Python model 和 vectors 是验证中间层，不是硬件交付物。
- HLS 生成 RTL 的问题只有在能回溯到 HLS 代码、pragma、配置或报告时才属于本 skill 范围。
- 本地密钥、私有硬件设计、生成缓存和私有远程服务器细节不应进入仓库。

## 机构说明

Jiyuan Liu 和 He Li 隶属于东南大学电子科学与工程学院。
两位作者所在团队为东南大学电子科学与工程学院异构智能与量子计算实验室（HIQC课题组），相关工作面向异构智能、量子计算及相关计算系统研究。

## 联系方式

问题、合作或学术使用，请联系：[erie@seu.edu.cn](mailto:erie@seu.edu.cn)。

## 引用

本 skill 由东南大学电子科学与工程学院异构智能与量子计算实验室（HIQC课题组）相关作者维护。

如果本 skill 对你的研究、教学或工程流程有帮助，请引用。规范引用元数据以 [CITATION.cff](CITATION.cff) 为准。

```bibtex
@software{liu_2026_hls_generator,
  author       = {Jiyuan Liu and He Li},
  title        = {{HLS Generator}: An Agent Skill for Vitis HLS Workflows},
  year         = {2026},
  version      = {0.2.3},
  date         = {2026-07-05},
  url          = {https://github.com/Eriemon/hls-generator},
  license      = {Apache-2.0},
  note         = {Agent skill package for structured AMD/Xilinx Vitis HLS workflows}
}
```

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
