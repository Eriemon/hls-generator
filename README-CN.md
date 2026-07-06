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

## 从 v0.2.3 到 v0.2.6 更新了什么

- 公开入口从旧的 `runtime.*` 层迁移到 `python -m scripts.python.cli.hls_generator ...`。
- 仓库结构改为严格跟随新版载荷：Python 实现按功能拆分到 `scripts/python/*`，分别承载 `cli`、`config`、`generation`、`hls_quality_gate`、`integration`、`remote`、`task_dispatcher`、`validation`、`workflow` 等域。
- 旧公开兼容层已经从仓库和 release 包中下线。`runtime/`、`integration/`、`pyproject.toml`、`VERSION` 不再作为对外公开接口发布。
- 版本真值统一收口到 `scripts/python/config/version.py`；README、重建后的 release zip、git tag 和 GitHub release 全部对齐到 `0.2.6` / `v0.2.6`。
- 发布资产改为由更新后的仓库通过 `scripts/python/release/prepare_release.py` 重新构建，而不是直接信任上游现成压缩包。
- 公开边界继续严格执行：`<REDACTED_LOCAL_PATH>` 保持脱敏占位；`.settings/`、`*.local.json`、`*.remote.json`、`reports/`、`tests/`、`smoke*`、缓存以及私有 server 指纹不会进入公开仓库和 release zip。

## Skill 架构

<p align="center">
  <img src="docs/assets/architecture-cn.svg" alt="HLS Generator Skill 架构" width="100%">
</p>

## 工作流

<p align="center">
  <img src="docs/assets/workflow-cn.svg" alt="HLS Generator 工作流" width="100%">
</p>

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

## 安装

直接告诉你的 AI 助手安装 [https://github.com/Eriemon/hls-generator](https://github.com/Eriemon/hls-generator)

手动准备方式：

```powershell
git clone https://github.com/Eriemon/hls-generator.git
cd .\hls-generator
```

如果要作为 Codex skill 使用，把仓库放入宿主 skill 搜索路径后重启宿主。

## 快速开始

从仓库根目录使用新的公开 CLI 入口：

```powershell
python -m scripts.python.cli.hls_generator --version
python -m scripts.python.cli.hls_generator config --path
python -m scripts.python.cli.hls_generator deps check --json
python -m scripts.python.cli.hls_generator scaffold --target hls --name vector_scale --out .\out\hls\spec.json
python -m scripts.python.cli.hls_generator prompt --target hls --spec .\out\hls\spec.json --out .\out\hls\prompt.md --confirm-requirements --confirmation-notes "user-confirmed HLS contract"
python -m scripts.python.cli.hls_generator validate --target hls --spec .\out\hls\spec.json --path .\out\hls\generated --readiness static --no-external
python -m scripts.python.cli.hls_generator readability-gate --target hls --path .\out\hls\generated --profile kernel --style current-project --json
```

旧公开入口已经下线，不要再把 `python -m runtime.hls_generator`、`hls-gen` 或旧版 `pip install` 元数据当作 `v0.2.6` 的公开集成契约。

## Release 重建

从更新后的仓库重建公开发布资产：

```powershell
python .\scripts\python\release\prepare_release.py --version 0.2.6
```

这条命令会重建 `dist/erie-hls-generator-v0.2.6/` 与 `dist/erie-hls-generator-v0.2.6.zip`。这份重建产物才是 `v0.2.6` 的发布真值。

## 公开仓库边界

- 公开跟踪内容只保留 skill 载荷、release 必需元数据和面向用户的文档。
- `references/remote-board-platform-upload.md` 中继续保留 `<REDACTED_LOCAL_PATH>`；真实本地路径不得进入公开仓库或 release 包。
- `.settings/`、`*.local.json`、`*.remote.json`、`reports/`、`tests/`、`smoke*`、缓存及类似本地专用内容必须排除在公开仓库和重建 release zip 之外。
- 只有实际运行 `vitis-run` 或 `vitis_hls` 后，才可以声称完成外部 Vitis 执行验证。

## 范围

- 负责生成和验证面向 Vitis HLS 的产物，不负责手写 RTL。
- HLS 生成 RTL 的调试仅在问题能回溯到 HLS 代码、pragma、配置或报告时属于本 Skill 范围。
- 当本地缺少 Vitis 工具时，优先走远程验收辅助，而不是降低验证口径。

## 联系方式

问题、合作或学术使用，请联系：[erie@seu.edu.cn](mailto:erie@seu.edu.cn)。

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
